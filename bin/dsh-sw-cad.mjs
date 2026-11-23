#!/usr/bin/env node
// dsh-sw-cad -- one entry point for the two MCP servers this bundle installs.
//
//   dsh-sw-cad serve autocad       stdio MCP server for AutoCAD (used by dsh)
//   dsh-sw-cad serve solidworks    stdio MCP server for SolidWorks, behind the
//                                  upstream discovery
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
  readConfig,
  resolveLayout,
  writeConfig,
} from "../lib/paths.mjs";

const PINS = JSON.parse(fs.readFileSync(path.join(PKG_ROOT, "lib", "upstream.json"), "utf8"));

const HELP = `dsh-sw-cad ${PKG.version}

  serve <autocad|solidworks>   run one MCP server over stdio (called by dsh)
  install [--base <dir>] [--work <dir>] [--python <exe>] [--force] [--with-dsh-patch]
                               install the upstream AutoCAD MCP and SolidWorks
                               skill; only the missing parts are touched
  doctor [--plugin] [--soft]   report present/missing pieces, change nothing
  deliver <dir|file>... [--require drawing,pdf] [--profile normal|half] [--out delivery.json] [--json] [--quiet]
                               check what a job produced and write delivery.json;
                               exit 1 when it does not hold up
  mode [normal|half]           show or set the delivery profile that deliver uses
                               by default (saved in ~/.dsh/sw-cad.json)
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

function runInstaller(layout, { check, force, dshPatch = false, skipCodex = false }) {
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
  if (skipCodex) args.push("-SkipCodex");
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
  const pluginMode = process.env.DSH_SW_CAD_CODEX_PLUGIN === "1";

  if (missing.length && process.env.SW_CAD_NO_AUTO_INSTALL !== "1") {
    log(`${target} needs ${missing.length} missing piece(s); installing them once.`);
    log("set SW_CAD_NO_AUTO_INSTALL=1 to disable this.");
    const res = runInstaller(layout, {
      check: false,
      force: false,
      dshPatch: false,
      skipCodex: pluginMode,
    });
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
    PYTHONUTF8: "1",
    PYTHONIOENCODING: "utf-8",
    NO_COLOR: "1",
  };
  relay(spawn(layout.acadExe, [], { env, stdio: "inherit" }), "autocad");
}

function serveSolidworks(layout) {
  if (!exists(layout.swServer)) die(`SolidWorks server missing: ${layout.swServer}`);
  // Upstream does its own discovery now; no shim in between.
  const env = {
    ...process.env,
    SW_MCP_DISCOVERY: "search",
    PYTHONUTF8: "1",
    PYTHONIOENCODING: "utf-8",
    NO_COLOR: "1",
  };
  relay(spawn(layout.pySw, [layout.swServer], { env, stdio: "inherit" }), "solidworks");
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

function cmdDoctor(argv) {
  const layout = resolveLayout();
  const pluginMode = argv.includes("--plugin") || process.env.DSH_SW_CAD_CODEX_PLUGIN === "1";
  const soft = argv.includes("--soft");
  log(`package  ${PKG_ROOT} (v${PKG.version})`);
  log(`install  ${pluginMode ? "Codex plugin" : "manual / dsh"}`);
  log(`pinned   autocad-mcp-pro ${PINS.autocadMcp.version}, solidworks skill ${PINS.solidworksSkill.commit.slice(0, 12)}`);

  // Which sides actually have a CAD application to drive. Only these get
  // registered; a side with no COM server registered is skipped on purpose.
  const probe = path.join(PKG_ROOT, "lib", "probe.py");
  if (exists(probe)) {
    const py = exists(layout.pySw) ? layout.pySw : layout.python;
    const r = spawnSync(py, [probe], { encoding: "utf8", timeout: 20000 });
    let cad = null;
    try {
      cad = JSON.parse(r.stdout.trim().split("\n").pop());
    } catch {}
    if (cad) {
      log(`cad apps  autocad: ${cad.autocad ? "registered" : "not found"}, solidworks: ${cad.solidworks ? "registered" : "not found"}`);
      if (!cad.autocad && !cad.solidworks) {
        log(`          neither is installed; the plugin will not register any MCP row`);
      }
    } else {
      log(`cad apps  probe failed`);
    }
  }
  log(`config   ${exists(CONFIG_FILE) ? CONFIG_FILE : "(none yet)"}`);
  const res = runInstaller(layout, { check: true, force: false, skipCodex: pluginMode });
  if (res.status !== 0 && soft) {
    log("preflight found missing pieces; the first CAD request will install them.");
  }
  process.exit(res.status === 0 || soft ? 0 : 1);
}

// The delivery check. Both CAD routes end here: whatever the skills produced,
// this reads it back and decides whether it holds up. It runs the interpreter
// the SolidWorks side already installed, because that venv is the one with
// ezdxf and PyMuPDF in it.
function cmdDeliver(argv) {
  const script = path.join(PKG_ROOT, "lib", "deliver.py");
  if (!exists(script)) die(`deliver.py is missing from the package at ${script}`);
  const layout = resolveLayout();
  const python = [layout.pySw, layout.python].find((candidate) => candidate && exists(candidate)) || "python";
  // A profile named on the command line wins; otherwise the one saved by
  // `dsh-sw-cad mode`. Batch runs rely on this: the choice is made once for the
  // job, not once per part.
  const args = [...argv];
  if (!argv.includes("--profile")) {
    const saved = (readConfig() || {}).profile;
    if (saved === "half" || saved === "normal") args.push("--profile", saved);
  }
  const res = spawnSync(python, [script, ...args], { stdio: "inherit" });
  if (res.error) die(`could not run ${python}: ${res.error.message}`);
  process.exit(res.status === null ? 1 : res.status);
}

function cmdMode(argv) {
  const wanted = argv[0];
  const cfg = readConfig() || {};
  if (!wanted) {
    console.log(cfg.profile || "normal");
    return;
  }
  if (wanted !== "normal" && wanted !== "half") {
    die(`mode takes "normal" or "half" (got ${wanted})`);
  }
  writeConfig({ ...cfg, profile: wanted });
  log(`delivery profile: ${wanted}`);
  if (wanted === "half") {
    log("values on the drawing must come from lib/standards.json; 审签 and 尺寸链闭合 are still not checked");
  }
}

function cmdWhere() {
  const layout = resolveLayout();
  const rows = {
    baseDir: layout.baseDir,
    workDir: layout.workDir,
    "autocad exe": layout.acadExe,
    "solidworks python": layout.pySw,
    "solidworks server": layout.swServer,
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
    cmdDoctor(rest);
    break;
  case "deliver":
    cmdDeliver(rest);
    break;
  case "mode":
    cmdMode(rest);
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
