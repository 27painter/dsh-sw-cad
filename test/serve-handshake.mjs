// Manual integration check for the two packaged serve entries.
// Run after the upstream is installed:
//   node test/serve-handshake.mjs autocad
// Expected entry tools: autocad=2, solidworks=3.
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/client";
import { StdioClientTransport } from "@modelcontextprotocol/client/stdio";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, "..");
const target = process.argv[2] || "autocad";
const expected = target === "solidworks" ? 3 : 2;

const transport = new StdioClientTransport({
  command: process.execPath,
  args: [path.join(root, "bin", "dsh-sw-cad.mjs"), "serve", target],
  cwd: root,
  stderr: "pipe",
});

let stderr = "";
transport.stderr?.on("data", (chunk) => {
  stderr += chunk.toString();
});

const client = new Client({ name: "dsh-sw-cad-test", version: "1.0.0" });
try {
  await client.connect(transport, { timeout: 30_000 });
  const { tools } = await client.listTools();
  const names = tools.map((tool) => tool.name);
  console.log(`${target}: server ready, ${names.length} entry tool(s): ${names.join(", ")}`);
  if (names.length !== expected) {
    throw new Error(`${target}: expected ${expected} entry tools, got ${names.length}`);
  }
} catch (error) {
  console.error(`handshake failed for ${target}: ${error.message}`);
  if (stderr.trim()) console.error(stderr.trim());
  process.exitCode = 1;
} finally {
  await client.close().catch(() => {});
}
