// The delivery check is the part of this bundle that decides whether a job
// holds up, so it gets its own test: build a delivery that should pass, one
// that should fail, and an empty folder that must never pass.
//
// No CAD application and no ezdxf/PyMuPDF needed — STEP and the review report
// are text files, and those two checks carry the verdict on their own.
import assert from "node:assert";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SCRIPT = path.join(HERE, "..", "lib", "deliver.py");

function python() {
  const candidates = [
    process.env.DSH_SW_CAD_PYTHON,
    "D:/cad-agents/venv-solidworks/Scripts/python.exe",
    process.env.PYTHON,
    "python",
    "py",
  ].filter(Boolean);
  for (const candidate of candidates) {
    const probe = spawnSync(candidate, ["-c", "import sys; print(sys.version_info[0])"], { encoding: "utf8" });
    if (probe.status === 0 && probe.stdout.trim() === "3") return candidate;
  }
  return null;
}

const PY = python();
if (!PY) {
  console.log("deliver: no python3 on PATH, skipping");
  process.exit(0);
}

function stepFile(target, schema, count) {
  const lines = [
    "ISO-10303-21;",
    "HEADER;",
    "FILE_DESCRIPTION((''),'2;1');",
    "FILE_NAME('part.step','2026-11-17T00:00:00',(''),(''),'','','');",
    `FILE_SCHEMA(('${schema}'));`,
    "ENDSEC;",
    "DATA;",
  ];
  for (let i = 1; i <= count; i++) lines.push(`#${i}=CARTESIAN_POINT('',(${i}.0,0.0,0.0));`);
  lines.push("ENDSEC;", "END-ISO-10303-21;", "");
  fs.writeFileSync(target, lines.join("\n"), "utf8");
}

function deliver(dir, extra = []) {
  const out = path.join(dir, "delivery.json");
  const res = spawnSync(PY, [SCRIPT, dir, "--out", out, ...extra], { encoding: "utf8" });
  let report = null;
  if (fs.existsSync(out)) report = JSON.parse(fs.readFileSync(out, "utf8"));
  return { code: res.status, stdout: res.stdout, report };
}

const root = fs.mkdtempSync(path.join(os.tmpdir(), "dsh-sw-cad-deliver-"));

// 1. A delivery that holds up: AP214 with real geometry, and a review report
//    that says pass.
const good = path.join(root, "good");
fs.mkdirSync(good);
stepFile(path.join(good, "part.step"), "AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }", 300);
fs.writeFileSync(
  path.join(good, "part_review_report.json"),
  JSON.stringify({ evaluation: { status: "pass", issues: [] } }),
  "utf8",
);
const goodRun = deliver(good, ["--require", "step"]);
assert.equal(goodRun.code, 0, `a complete delivery must pass:\n${goodRun.stdout}`);
assert.equal(goodRun.report.verdict, "pass");
assert.equal(goodRun.report.counts.failures, 0);
assert.ok(
  goodRun.report.files.every((file) => file.sha256 && file.sha256.length === 64),
  "every checked file carries a sha256",
);

// 2. The same shape with two things wrong: AP203 (no colour, no assembly) and
//    a review report that failed. The verdict must be fail, and the reasons
//    must name both.
const bad = path.join(root, "bad");
fs.mkdirSync(bad);
stepFile(path.join(bad, "part.step"), "CONFIG_CONTROL_DESIGN", 40);
fs.writeFileSync(
  path.join(bad, "part_review_report.json"),
  JSON.stringify({ evaluation: { status: "fail", issues: ["front view empty"] } }),
  "utf8",
);
const badRun = deliver(bad);
assert.equal(badRun.code, 1, "a failed review report must fail the delivery");
assert.equal(badRun.report.verdict, "fail");
const ids = badRun.report.files.flatMap((file) => file.checks.filter((c) => !c.ok).map((c) => c.id));
assert.ok(ids.includes("review.status"), `expected review.status in ${ids}`);
assert.ok(ids.includes("step.entities"), `expected step.entities in ${ids}`);

// 3. An empty folder must not pass. Reporting success for a job that produced
//    nothing is the one answer this must never give.
const empty = path.join(root, "empty");
fs.mkdirSync(empty);
const emptyRun = deliver(empty);
assert.equal(emptyRun.code, 1, "an empty folder must fail");
assert.equal(emptyRun.report.verdict, "fail");

// 4. --require names a kind that is not there.
const partial = path.join(root, "partial");
fs.mkdirSync(partial);
stepFile(path.join(partial, "part.step"), "AUTOMOTIVE_DESIGN", 200);
const required = deliver(partial, ["--require", "drawing"]);
assert.equal(required.code, 1, "--require drawing must fail when there is no drawing");
assert.deepEqual(required.report.missing, ["drawing"]);

fs.rmSync(root, { recursive: true, force: true });
console.log("deliver: 4 cases behave as intended");
