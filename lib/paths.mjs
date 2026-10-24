// Resolve every path this package needs at runtime, from the package's own
// location plus a small JSON file under ~/.dsh. Nothing here is hardcoded to a
// particular machine: the defaults only pick a drive that exists.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const PKG_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const PKG = JSON.parse(fs.readFileSync(path.join(PKG_ROOT, "package.json"), "utf8"));

export const DSH_HOME = process.env.DSH_HOME || path.join(os.homedir(), ".dsh");
export const CONFIG_FILE = path.join(DSH_HOME, "sw-cad.json");

export function defaultBaseDir() {
  // D: first because that is where the kit has always put its runtime, then any
  // non-system drive, then the home directory.
  if (process.env.SW_CAD_BASE) return process.env.SW_CAD_BASE;
  if (process.platform === "win32") {
    for (const drive of ["D:", "E:", "F:"]) {
      try {
        if (fs.existsSync(drive + "\\")) return path.join(drive + "\\", "cad-agents");
      } catch {}
    }
  }
  return path.join(os.homedir(), "cad-agents");
}

export function readConfig() {
  try {
    return JSON.parse(fs.readFileSync(CONFIG_FILE, "utf8"));
  } catch {
    return null;
  }
}

export function writeConfig(cfg) {
  fs.mkdirSync(path.dirname(CONFIG_FILE), { recursive: true });
  fs.writeFileSync(CONFIG_FILE, JSON.stringify(cfg, null, 2) + "\n", "utf8");
}

export function resolveLayout(overrides = {}) {
  const cfg = readConfig() || {};
  const baseDir = path.resolve(
    overrides.baseDir || process.env.SW_CAD_BASE || cfg.baseDir || defaultBaseDir(),
  );
  const workDir = path.resolve(overrides.workDir || cfg.workDir || "D:\\AutoCad");
  const exe = process.platform === "win32" ? ".exe" : "";
  const layout = {
    baseDir,
    workDir,
    venvAcad: path.join(baseDir, "venv-autocad"),
    venvSw: path.join(baseDir, "venv-solidworks"),
    skillsDir: path.join(baseDir, "dsh-skills"),
    skillDir: path.join(baseDir, "dsh-skills", "solidworks-automation"),
    shimDir: path.join(baseDir, "mcp-shim"),
    python: cfg.python || overrides.python || "python",
  };
  layout.pyAcad = path.join(layout.venvAcad, "Scripts", "python" + exe);
  layout.acadExe = path.join(layout.venvAcad, "Scripts", "autocad-mcp" + exe);
  layout.pySw = path.join(layout.venvSw, "Scripts", "python" + exe);
  layout.swServer = path.join(layout.skillDir, "mcp-server", "server.py");
  layout.shimScript = path.join(PKG_ROOT, "mcp-shim", "sw_discovery_shim.py");
  layout.shimLexicon = path.join(PKG_ROOT, "mcp-shim", "sw_lexicon.json");
  layout.shimCatalog = path.join(layout.shimDir, "sw_catalog.json");
  layout.acadSkill = path.join(PKG_ROOT, "skills", "autocad-mcp-pro", "SKILL.md");
  return layout;
}

export function exists(p) {
  try {
    return fs.existsSync(p);
  } catch {
    return false;
  }
}

// What is missing before either server can start.
export function missingPieces(layout, target) {
  const missing = [];
  if (target === "autocad" || target === "all") {
    if (!exists(layout.acadExe)) missing.push(layout.acadExe);
  }
  if (target === "solidworks" || target === "all") {
    if (!exists(layout.pySw)) missing.push(layout.pySw);
    if (!exists(layout.swServer)) missing.push(layout.swServer);
  }
  return missing;
}
