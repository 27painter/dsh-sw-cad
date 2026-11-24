// What the plugin registers, and what it refuses to register.
//
// Runs anywhere with Node: it imports the real MCP client when it is installed
// (npm test installs it as a devDependency) and skips when it is not, so the
// check never blocks on an environment that cannot resolve the peer.
import assert from "node:assert";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const pkgRoot = path.join(here, "..");

let peer = null;
try {
  peer = await import("@deepseek-ai/dsh-mcp-client");
} catch {
  // A developer without devDependencies can skip; CI cannot — a skipped test
  // that exits 0 is a green run that verified nothing.
  if (process.env.CI) {
    console.error("FAIL: @deepseek-ai/dsh-mcp-client missing in CI; run npm install first");
    process.exit(1);
  }
  console.log("SKIP: @deepseek-ai/dsh-mcp-client is not installed (npm install)");
  process.exit(0);
}

const plugin = await import(pathToFileURL(path.join(pkgRoot, "lib", "index.js")).href);

assert.equal(plugin.name, "dsh-sw-cad", "the plugin must be loadable by package name");
assert.equal(typeof plugin.apply, "function", "apply must be a function");
assert.deepEqual(plugin.inject, peer.inject, "inject must match the MCP client's");

function attempt(env) {
  const saved = {};
  for (const k of Object.keys(env)) {
    saved[k] = process.env[k];
    if (env[k] === undefined) delete process.env[k];
    else process.env[k] = env[k];
  }
  const registered = [];
  const ctx = { plugin: (mod, cfg) => registered.push({ mod, cfg }) };
  let err = null;
  try {
    plugin.apply(ctx);
  } catch (e) {
    err = e;
  }
  Object.assign(process.env, saved);
  return { registered, err };
}

// --- both CAD applications present -----------------------------------------
// SW_CAD_ASSUME makes the expected result independent of what this machine
// has installed: CI runners have neither AutoCAD nor SolidWorks.
const both = attempt({ SW_CAD_DISABLE: undefined, SW_CAD_ASSUME: "autocad,solidworks" });
for (const r of both.registered) {
  assert.equal(r.mod, peer, "must register the MCP client itself");
  assert.ok(path.isAbsolute(r.cfg.command), "command must be absolute, not a PATH name");
  assert.equal(r.cfg.transport, "stdio");
  assert.equal(r.cfg.failOnStartupError, true, "a server that cannot start must say so");
}
assert.deepEqual(both.registered.map((r) => r.cfg.serverName).sort(), ["autocad", "solidworks"]);
// The SolidWorks row runs upstream's server directly and asks it for its own
// discovery surface; there is no shim in between any more.
const sw = both.registered.find((r) => r.cfg.serverName === "solidworks");
assert.ok(sw, "solidworks row missing");
assert.equal(sw.cfg.env.SW_MCP_DISCOVERY, "search", "must ask upstream for discovery mode");
assert.ok(sw.cfg.args.length === 1 && sw.cfg.args[0].endsWith("server.py"),
  `the solidworks row should run server.py alone, got: ${JSON.stringify(sw.cfg.args)}`);
console.log("both installed  ->", both.registered.map((r) => r.cfg.serverName).sort().join(", "));

// --- one side disabled ------------------------------------------------------
const noAcad = attempt({ SW_CAD_DISABLE: "autocad", SW_CAD_ASSUME: "autocad,solidworks" });
assert.deepEqual(noAcad.registered.map((r) => r.cfg.serverName), ["solidworks"]);
console.log("autocad off     -> solidworks only");

const noSw = attempt({ SW_CAD_DISABLE: "solidworks", SW_CAD_ASSUME: "autocad,solidworks" });
assert.deepEqual(noSw.registered.map((r) => r.cfg.serverName), ["autocad"]);
console.log("solidworks off  -> autocad only");

// --- nothing left to drive: register nothing, but do not fail the plugin -----
// `deliver` never starts a CAD application, so a machine with neither one still
// has a working half of this bundle. Failing the load would take it away.
const none = attempt({ SW_CAD_DISABLE: "autocad,solidworks", SW_CAD_ASSUME: "autocad,solidworks" });
assert.equal(none.registered.length, 0, "must not register rows that cannot start");
assert.equal(none.err, null, "having no CAD is not an error; deliver still works");
console.log("both off        -> registers nothing, no error");

// Detection path: with nothing assumed and nothing detected, register nothing
// rather than a row that cannot start. On a machine with CAD this registers
// both, which is the other half of the same guarantee.
const bare = attempt({ SW_CAD_DISABLE: undefined, SW_CAD_ASSUME: undefined });
if (bare.registered.length === 0) {
  assert.equal(bare.err, null, "must not throw when nothing is detected");
  console.log("nothing detected -> registers nothing, no error");
} else {
  console.log("nothing detected -> skipped (this machine has CAD installed)");
}

// --- a machine with no upstream installed is an error, not a silent skip ----
// SW_CAD_ASSUME stands in for a registered CAD: apply() probes before it
// installs, so without a target there would be nothing to fetch and no error to
// check. This is the case that must still fail loudly.
const empty = fs.mkdtempSync(path.join(os.tmpdir(), "swcad-test-"));
try {
  const saved = process.env.SW_CAD_BASE;
  const savedNoInstall = process.env.SW_CAD_NO_AUTO_INSTALL;
  const savedAssume = process.env.SW_CAD_ASSUME;
  process.env.SW_CAD_BASE = empty;
  process.env.SW_CAD_NO_AUTO_INSTALL = "1";
  process.env.SW_CAD_ASSUME = "autocad";
  const registered = [];
  const ctx = { plugin: (m, c) => registered.push(c) };
  let err = null;
  try {
    plugin.apply(ctx);
  } catch (e) {
    err = e;
  }
  assert.equal(registered.length, 0, "must not register anything when the upstream is missing");
  assert.ok(err, "must throw when the upstream is missing and auto-install is off");
  assert.match(err.message, /upstream missing/i);
  console.log("no upstream     -> throws, registers nothing");
  const restore = (k, v) => { if (v === undefined) delete process.env[k]; else process.env[k] = v; };
  restore("SW_CAD_BASE", saved);
  restore("SW_CAD_NO_AUTO_INSTALL", savedNoInstall);
  restore("SW_CAD_ASSUME", savedAssume);
} finally {
  fs.rmSync(empty, { recursive: true, force: true });
}

console.log("\nall cases behave as intended");
