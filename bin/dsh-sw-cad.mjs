#!/usr/bin/env node
// dsh-sw-cad -- one entry point for the two MCP servers this bundle installs.
//
//   dsh-sw-cad serve autocad       stdio MCP server for AutoCAD (used by dsh)
//   dsh-sw-cad serve solidworks    stdio MCP server for SolidWorks, behind the
//                                  discovery shim
//   dsh-sw-cad install             install the two upstream servers (idempotent)
//   dsh-sw-cad doctor              report what is present or missing, change nothing
//   dsh-sw-cad where               print the resolved paths
//
// `serve` is what the cordis patch calls. It resolves paths from this package's
// own location plus ~/.dsh/sw-cad.json, so the patch itself carries no absolute
// path and the same bundle works on any machine.
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import {
  CONFIG_FILE,
  PKG,
  PKG_ROOT,
  exists,
  missingPieces,
  resolveLayout,
  writeConfig,
} from "../lib/paths.mjs";

const PINS = JSON.parse(fs.readFileSync(path.join(PKG_ROOT, "lib", "upstream.json"), "utf8"));

const HELP = `dsh-sw-cad ${PKG.version}

  serve <autocad|solidworks>   run one MCP server over stdio (called by dsh)
  install [--base <dir>] [--work <dir>] [--python <exe>] [--force] [--with-dsh-patch]
                               install the upstream AutoCAD MCP and SolidWorks
                               skill; only the missing parts are touched
  doctor                       report present/missing pieces, change nothing
  where                        print the resolved paths
  version                      print the version

Environment:
  SW_CAD_BASE              base directory (default: first of D:/E:/F: then ~)
  SW_CAD_NO_AUTO_INSTALL   set to 1 to stop "serve" from installing on demand
`;

function log(msg) {
  process.stderr.write(`[dsh-sw-cad] ${msg}\n`);
}

function die(msg, code = 1) {
  log(msg);
  process.exit(code);
}

function powershell() {
  // Prefer the built-in Windows PowerShell; fall back to pwsh on PATH.
  const ps = path.join(process.env.SystemRoot || "C:\\Windows", "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
  return exists(ps) ? ps : "powershell";
}

function runInstaller(layout, { check, force, dshPatch = false }) {
  const script = path.join(PKG_ROOT, "bootstrap.ps1");
  if (!exists(script)) die(`bootstrap.ps1 is missing from the package at ${script}`);
  const args = [
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", script,
    "-BaseDir", layout.baseDir,
    "-WorkDir", layout.workDir,
    "-Python", layout.python,
    "-AutocadVersion", PINS.autocadMcp.version,
    "-SkillRef", PINS.solidworksSkill.commit,
  ];
  if (check) args.push("-Check");
  if (force) args.push("-Force");
  // The bundle patch registers the two servers for dsh, so the installer only
  // writes ~/.dsh/cordis.patch.yml when the user explicitly asks for it.
  if (!dshPatch) args.push("-NoDshPatch");
  return spawnSync(powershell(), args, { stdio: "inherit" });
}

// ---------------------------------------------------------------- commands

function cmdServe(target) {
  if (target !== "autocad" && target !== "solidworks") {
    die(`serve needs a target: autocad or solidworks (got ${target || "nothing"})`);
  }
  const layout = resolveLayout();
  const missing = missingPieces(layout, target);

  if (missing.length && process.env.SW_CAD_NO_AUTO_INSTALL !== "1") {
    log(`${target} needs ${missing.length} missing piece(s); installing them once.`);
    log("set SW_CAD_NO_AUTO_INSTALL=1 to disable this.");
    const res = runInstaller(layout, { check: false, force: false, dshPatch: false });
    if (res.status !== 0) {
      log("install failed; the server will not start.");
      log(`run "dsh-sw-cad install" by hand to see the full output.`);
      process.exit(1);
    }
    const still = missingPieces(layout, target);
    if (still.length) {
      log(`still missing after install: ${still.join(", ")}`);
      process.exit(1);
    }
  } else if (missing.length) {
    log(`cannot start ${target}, missing: ${missing.join(", ")}`);
    log(`run "dsh-sw-cad install" first.`);
    process.exit(1);
  }

  if (target === "autocad") {
    serveAutocad(layout);
  } else {
    serveSolidworks(layout);
  }
}

function serveAutocad(layout) {
  const env = {
    ...process.env,
    AUTOCAD_MCP_BACKEND: "auto",
    TOOL_PROFILE: "full",
    TOOL_PACKS: "all",
    DISCOVERY_MODE: "search",
    ALLOWED_PATHS: layout.workDir,
  };
  relay(spawn(layout.acadExe, [], { env, stdio: "inherit" }), "autocad");
}

function serveSolidworks(layout) {
  if (!exists(layout.shimScript)) die(`discovery shim missing from the package: ${layout.shimScript}`);
  const args = [
    layout.shimScript,
    "--python", layout.pySw,
    "--server", layout.swServer,
    "--catalog", layout.shimCatalog,
    "--lexicon", layout.shimLexicon,
  ];
  const env = { ...process.env, SW_SHIM_TOP_K: "8" };
  relay(spawn(layout.pySw, args, { env, stdio: "inherit" }), "solidworks");
}

function relay(child, name) {
  child.on("error", (err) => die(`${name} failed to start: ${err.message}`));
  child.on("exit", (code, signal) => {
    if (signal) log(`${name} exited on ${signal}`);
    process.exit(code ?? 1);
  });
  for (const sig of ["SIGINT", "SIGTERM"]) {
    process.on(sig, () => {
      try {
        child.kill(sig);
      } catch {}
    });
  }
}

function cmdInstall(argv) {
  const layout = resolveLayout(parseFlags(argv));
  log(`base ${layout.baseDir}`);
  log(`work ${layout.workDir}`);
  const res = runInstaller(layout, {
    check: false,
    force: argv.includes("--force"),
    dshPatch: argv.includes("--with-dsh-patch"),
  });
  if (res.status !== 0) {
    die(`installer exited with ${res.status}; see the output above.`);
  }
  writeConfig({
    baseDir: layout.baseDir,
    workDir: layout.workDir,
    python: layout.python,
    installedAt: new Date().toISOString(),
    version: PKG.version,
  });
  log(`wrote ${CONFIG_FILE}`);
  log("done. dsh picks the servers up from the bundle patch (dsh plugin add).");
  log("installed without `dsh plugin add`? rerun with --with-dsh-patch.");
}

function cmdDoctor() {
  const layout = resolveLayout();
  log(`package  ${PKG_ROOT} (v${PKG.version})`);
  log(`pinned   autocad-mcp-pro ${PINS.autocadMcp.version}, solidworks skill ${PINS.solidworksSkill.commit.slice(0, 12)}`);
  log(`config   ${exists(CONFIG_FILE) ? CONFIG_FILE : "(none yet)"}`);
  const res = runInstaller(layout, { check: true, force: false });
  process.exit(res.status === 0 ? 0 : 1);
}

function cmdWhere() {
  const layout = resolveLayout();
  const rows = {
    baseDir: layout.baseDir,
    workDir: layout.workDir,
    "autocad exe": layout.acadExe,
    "solidworks python": layout.pySw,
    "solidworks server": layout.swServer,
    "shim (packaged)": layout.shimScript,
    "shim catalog": layout.shimCatalog,
  };
  for (const [k, v] of Object.entries(rows)) {
    console.log(`${exists(v) ? "ok  " : "--  "} ${k.padEnd(18)} ${v}`);
  }
}

function parseFlags(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--base") out.baseDir = argv[++i];
    else if (a === "--work") out.workDir = argv[++i];
    else if (a === "--python") out.python = argv[++i];
  }
  return out;
}

// ---------------------------------------------------------------- dispatch

const [cmd, ...rest] = process.argv.slice(2);
switch (cmd) {
  case "serve":
    cmdServe(rest[0]);
    break;
  case "install":
    cmdInstall(rest);
    break;
  case "doctor":
    cmdDoctor();
    break;
  case "where":
    cmdWhere();
    break;
  case "version":
  case "--version":
  case "-v":
    console.log(PKG.version);
    break;
  case undefined:
  case "help":
  case "--help":
  case "-h":
    process.stdout.write(HELP);
    break;
  default:
    die(`unknown command: ${cmd}\n\n${HELP}`);
}
