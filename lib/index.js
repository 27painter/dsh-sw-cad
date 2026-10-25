// The dsh plugin entry point.
//
// This is a real plugin, not a patch that spawns a binary: dsh imports this
// module by package name, so nothing has to be on PATH. Every path is resolved
// here, at load time, from this file's own location plus ~/.dsh/sw-cad.json.
//
// Two things happen in apply():
//   1. make sure the pinned upstream servers are installed (installing them
//      here, once, if they are not), and throw with an actionable message when
//      that is impossible;
//   2. register the two MCP clients with absolute paths.
//
// Registering only after the check is what keeps a missing upstream from
// turning into a silent no-tool session.
import { spawnSync } from "node:child_process";
import path from "node:path";

import * as McpClient from "@deepseek-ai/dsh-mcp-client";

import { PKG, PKG_ROOT, exists, missingPieces, resolveLayout } from "./paths.mjs";

export const name = "dsh-sw-cad";
// Wait for the same services the MCP client needs.
export const inject = McpClient.inject;

const LOG_PREFIX = "[dsh-sw-cad]";

function say(msg) {
  process.stderr.write(`${LOG_PREFIX} ${msg}\n`);
}

function powershell() {
  const ps = path.join(
    process.env.SystemRoot || "C:\\Windows",
    "System32",
    "WindowsPowerShell",
    "v1.0",
    "powershell.exe",
  );
  return exists(ps) ? ps : "powershell";
}

function installerArgs(layout) {
  return [
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", path.join(PKG_ROOT, "bootstrap.ps1"),
    "-BaseDir", layout.baseDir,
    "-WorkDir", layout.workDir,
    "-Python", layout.python,
    "-NoDshPatch",
  ];
}

function installPinned(layout) {
  if (process.platform !== "win32") {
    throw new Error(
      `${LOG_PREFIX} this bundle only runs on Windows (AutoCAD and SolidWorks are COM servers).`,
    );
  }
  const script = path.join(PKG_ROOT, "bootstrap.ps1");
  if (!exists(script)) {
    throw new Error(`${LOG_PREFIX} bootstrap.ps1 is missing from the package at ${script}`);
  }
  say("upstream servers are not installed yet; installing the pinned versions now.");
  say("this runs once and takes a few minutes.");
  const res = spawnSync(powershell(), installerArgs(layout), { stdio: "inherit" });
  if (res.error) {
    throw new Error(`${LOG_PREFIX} could not run the installer: ${res.error.message}`);
  }
  if (res.status !== 0) {
    throw new Error(
      `${LOG_PREFIX} installing the upstream servers failed (exit ${res.status}).\n` +
        `Re-run it by hand to see the full output:\n  node "${path.join(PKG_ROOT, "bin", "dsh-sw-cad.mjs")}" install`,
    );
  }
}

function ensureUpstream(layout) {
  let missing = missingPieces(layout, "all");
  if (missing.length === 0) return;

  if (process.env.SW_CAD_NO_AUTO_INSTALL === "1") {
    throw new Error(
      `${LOG_PREFIX} upstream missing (${missing.join(", ")}) and SW_CAD_NO_AUTO_INSTALL=1 is set, ` +
        `so nothing was installed. Unset it, or run:\n` +
        `  node "${path.join(PKG_ROOT, "bin", "dsh-sw-cad.mjs")}" install`,
    );
  }

  installPinned(layout);

  missing = missingPieces(layout, "all");
  if (missing.length > 0) {
    throw new Error(
      `${LOG_PREFIX} still missing after install: ${missing.join(", ")}\n` +
        `Run "node "${path.join(PKG_ROOT, "bin", "dsh-sw-cad.mjs")}" doctor" for a per-item report.`,
    );
  }
}

function autocadConfig(layout) {
  return {
    serverName: "autocad",
    transport: "stdio",
    command: layout.acadExe,
    args: [],
    env: {
      AUTOCAD_MCP_BACKEND: "auto",
      TOOL_PROFILE: "full",
      TOOL_PACKS: "all",
      DISCOVERY_MODE: "search",
      ALLOWED_PATHS: layout.workDir,
    },
    toolCallTimeoutMs: 180000,
    // A server that cannot start should say so, not retry in silence.
    failOnStartupError: true,
    reconnect: { enabled: true, initialDelayMs: 500, maxDelayMs: 30000, maxAttempts: 10 },
  };
}

function solidworksConfig(layout) {
  return {
    serverName: "solidworks",
    transport: "stdio",
    command: layout.pySw,
    args: [
      layout.shimScript,
      "--python", layout.pySw,
      "--server", layout.swServer,
      "--catalog", layout.shimCatalog,
      "--lexicon", layout.shimLexicon,
    ],
    env: { SW_SHIM_TOP_K: "8" },
    toolCallTimeoutMs: 300000,
    failOnStartupError: true,
    reconnect: { enabled: true, initialDelayMs: 500, maxDelayMs: 30000, maxAttempts: 10 },
  };
}

export function apply(ctx) {
  const layout = resolveLayout();

  if (PKG.os && !PKG.os.includes(process.platform)) {
    throw new Error(`${LOG_PREFIX} this bundle only runs on ${PKG.os.join(", ")}.`);
  }

  ensureUpstream(layout);

  ctx.plugin(McpClient, autocadConfig(layout));
  ctx.plugin(McpClient, solidworksConfig(layout));

  say(
    `registered autocad + solidworks (base ${layout.baseDir}, upstream pinned in lib/upstream.json)`,
  );
}
