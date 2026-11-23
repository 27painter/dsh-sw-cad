// Static checks for the Codex plugin shell. The MCP runtime itself is covered
// by the existing plugin and delivery tests.
import assert from "node:assert";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, "..");

function readJson(relativePath) {
  return JSON.parse(fs.readFileSync(path.join(root, relativePath), "utf8"));
}

const pkg = readJson("package.json");
const manifest = readJson(".codex-plugin/plugin.json");
const mcp = readJson(".mcp.json");
const hooks = readJson("hooks/hooks.json");
const marketplace = readJson(".agents/plugins/marketplace.json");

assert.equal(manifest.name, pkg.name, "manifest and npm package names must match");
assert.equal(manifest.version, pkg.version, "manifest and npm package versions must match");
assert.equal(manifest.skills, "./skills/", "skills must be exposed lazily from the plugin");
assert.equal(manifest.mcpServers, "./.mcp.json", "MCP servers must come from .mcp.json");
assert.equal(manifest.hooks, "./hooks/hooks.json", "the SessionStart hook must be declared");

for (const [name, expectedArgs, expectedToolTimeout] of [
  ["autocad", ["./bin/dsh-sw-cad.mjs", "serve", "autocad"], 180],
  ["solidworks", ["./bin/dsh-sw-cad.mjs", "serve", "solidworks"], 300],
]) {
  const server = mcp.mcpServers[name];
  assert.ok(server, `${name} MCP server is missing`);
  assert.equal(server.type, "stdio");
  assert.equal(server.command, "node");
  assert.deepEqual(server.args, expectedArgs);
  assert.equal(server.cwd, "./", "relative cwd must resolve to the installed plugin root");
  assert.equal(server.env.DSH_SW_CAD_CODEX_PLUGIN, "1", "serve must know it runs inside Codex");
  assert.equal(server.startup_timeout_sec, 300, "first use may install the upstream");
  assert.equal(server.tool_timeout_sec, expectedToolTimeout);
}

const sessionStart = hooks.hooks.SessionStart?.[0]?.hooks?.[0];
assert.ok(sessionStart, "SessionStart hook is missing");
assert.equal(sessionStart.type, "command");
assert.match(sessionStart.command, /dsh-sw-cad\.mjs" doctor --plugin --soft$/, "hook must run the non-blocking plugin doctor");
assert.equal(sessionStart.timeoutSec, 60);

assert.equal(marketplace.plugins.length, 1);
assert.equal(marketplace.plugins[0].name, pkg.name);
assert.equal(marketplace.plugins[0].source.source, "local");
assert.equal(marketplace.plugins[0].source.path, ".");

for (const relativePath of [
  "skills/autocad-mcp-pro/SKILL.md",
  "skills/solidworks-automation/SKILL.md",
  "assets/icon-180.png",
  ".mcp.json",
  "hooks/hooks.json",
]) {
  assert.ok(fs.existsSync(path.join(root, relativePath)), `missing plugin file: ${relativePath}`);
}

console.log("Codex plugin manifest, MCP entries, hook, skills, and marketplace look consistent");
