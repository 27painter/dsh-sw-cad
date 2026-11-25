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
//    that carries the self-review evidence behind its verdict — not just the
//    verdict.
const good = path.join(root, "good");
fs.mkdirSync(good);
stepFile(path.join(good, "part.step"), "AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }", 300);
fs.writeFileSync(
  path.join(good, "part_review_report.json"),
  JSON.stringify({
    evaluation: { status: "pass", issues: [], score: 92 },
    checks: {
      previews_created: true,
      previews_not_blank: true,
      expected_outputs_exist: true,
      feature_summary_available: true,
    },
  }),
  "utf8",
);
fs.writeFileSync(path.join(good, "part_isometric.bmp"), Buffer.alloc(2048, 7));
const goodRun = deliver(good, ["--require", "step"]);
assert.equal(goodRun.code, 0, `a complete delivery must pass:\n${goodRun.stdout}`);
assert.equal(goodRun.report.verdict, "pass");
assert.equal(goodRun.report.counts.failures, 0);
assert.equal(goodRun.report.counts.warnings, 0, "a complete delivery must not warn either");
assert.ok(
  goodRun.report.files.every((file) => file.sha256 && file.sha256.length === 64),
  "every checked file carries a sha256",
);
const goodIds = goodRun.report.files.flatMap((file) => file.checks.map((c) => c.id));
assert.ok(goodIds.includes("step.bbox"), `expected step.bbox in ${goodIds}`);
assert.ok(goodIds.includes("step.schema"), `expected step.schema in ${goodIds}`);

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

// 5. A STEP whose points all sit at the origin has a header and a schema but
//    no geometry. Counting entities alone would call that a pass.
const flat = path.join(root, "flat");
fs.mkdirSync(flat);
const flatLines = [
  "ISO-10303-21;", "HEADER;", "FILE_DESCRIPTION((''),'2;1');",
  "FILE_NAME('flat.step','2026-11-17T00:00:00',(''),(''),'','','');",
  "FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));", "ENDSEC;", "DATA;",
];
for (let i = 1; i <= 200; i++) flatLines.push(`#${i}=CARTESIAN_POINT('',(0.0,0.0,0.0));`);
flatLines.push("ENDSEC;", "END-ISO-10303-21;", "");
fs.writeFileSync(path.join(flat, "flat.step"), flatLines.join("\n"), "utf8");
const flatRun = deliver(flat);
assert.equal(flatRun.code, 1, "a STEP with no extent must fail");
const flatIds = flatRun.report.files.flatMap((file) => file.checks.filter((c) => !c.ok).map((c) => c.id));
assert.ok(flatIds.includes("step.bbox"), `expected step.bbox in ${flatIds}`);

// 6. Half-delivery: the values on the drawing must come from lib/standards.json.
//    Needs ezdxf to build the DXF; skipped when the interpreter does not have it.
const BUILD = String.raw`
import sys, os, ezdxf
base, which = sys.argv[1], sys.argv[2]
notes = (["比例 1:2", "材料 45", "φ40H7/g6", "Ra 1.6"] if which == "ok"
         else ["比例 1:2", "材料 40CrMnMoX", "φ40H7/z9", "Ra 2.0"])
doc = ezdxf.new("R2018", setup=True)
doc.header["$INSUNITS"] = 4
doc.layers.add("粗实线", lineweight=50)
doc.layers.add("细实线", lineweight=13)
doc.styles.add("GB", font="fangsong.ttf")
msp = doc.modelspace()
msp.add_lwpolyline([(0,0),(100,0),(100,60),(0,60)], close=True, dxfattribs={"layer":"粗实线"})
msp.add_linear_dim(base=(0,-12), p1=(0,0), p2=(100,0)).render()
blk = doc.blocks.new("TITLEBLOCK")
blk.add_lwpolyline([(0,0),(180,0),(180,40),(0,40)], close=True)
sheet = doc.layouts.get("Layout1")
sheet.dxf.paper_width, sheet.dxf.paper_height = 420, 297
sheet.add_blockref("TITLEBLOCK", (10,10))
for i, text in enumerate(notes):
    sheet.add_text(text, dxfattribs={"height":4,"style":"GB"}).set_placement((20, 34 - i*8))
doc.saveas(os.path.join(base, which + ".dxf"))
`;
const halfRoot = path.join(root, "half");
const halfOkDir = path.join(halfRoot, "ok");
const halfBadDir = path.join(halfRoot, "bad");
fs.mkdirSync(halfOkDir, { recursive: true });
fs.mkdirSync(halfBadDir, { recursive: true });
const builtOk = spawnSync(PY, ["-c", BUILD, halfOkDir, "ok"], { encoding: "utf8" });
const builtBad = spawnSync(PY, ["-c", BUILD, halfBadDir, "bad"], { encoding: "utf8" });
if (builtOk.status !== 0 || builtBad.status !== 0) {
  console.log("deliver: ezdxf not available here, half-delivery cases skipped");
} else {
  const halfOk = deliver(halfOkDir, ["--profile", "half"]);
  assert.equal(halfOk.code, 0, `values from the table must pass:\n${halfOk.stdout}`);
  assert.equal(halfOk.report.profile, "half");
  assert.ok(halfOk.report.not_checked.length > 0, "half profile must say what it did not check");
  const okIds = halfOk.report.files.flatMap((f) => f.checks.map((c) => c.id));
  assert.ok(okIds.includes("standards.material"), `expected standards.material in ${okIds}`);
  assert.ok(okIds.includes("standards.fit"), `expected standards.fit in ${okIds}`);

  const halfBad = deliver(halfBadDir, ["--profile", "half"]);
  assert.equal(halfBad.code, 1, "a material and a fit outside the table must fail");
  const badIds = halfBad.report.files.flatMap((f) => f.checks.filter((c) => !c.ok).map((c) => c.id));
  assert.ok(badIds.includes("standards.material_table"), `expected standards.material_table in ${badIds}`);
  assert.ok(badIds.includes("standards.fit"), `expected standards.fit in ${badIds}`);
  assert.ok(badIds.includes("standards.roughness"), `expected standards.roughness in ${badIds}`);

  // The same drawing under the default profile must pass: the values are only
  // judged when the job asked to be judged that way.
  const normalRun = deliver(halfBadDir, ["--profile", "normal"]);
  assert.equal(normalRun.code, 0, "normal profile must not judge standard values");
}

// 7. A reader that is missing must not turn into a pass. `-S` disables
//    site-packages, so ezdxf is genuinely not importable while the script still
//    runs — the exact state of a machine that installed the bundle without the
//    Python dependencies. Before this case existed, every DXF passed there.
const nodepDir = path.join(root, "nodep");
fs.mkdirSync(nodepDir);
fs.writeFileSync(
  path.join(nodepDir, "blank.dxf"),
  "0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1032\n0\nENDSEC\n999\n" + "x".repeat(3000) + "\n0\nEOF\n",
  "utf8",
);
const nodepOut = path.join(nodepDir, "delivery.json");
const nodepRun = spawnSync(
  PY,
  ["-S", SCRIPT, nodepDir, "--out", nodepOut, "--require", "drawing"],
  { encoding: "utf8" },
);
assert.equal(nodepRun.status, 2, `a missing reader must exit 2: ${nodepRun.stdout}`);
const nodepReport = JSON.parse(fs.readFileSync(nodepOut, "utf8"));
assert.equal(nodepReport.verdict, "unknown", "no dependency must never be reported as pass");
assert.ok(nodepReport.counts.blocked > 0, "the unreadable file must be counted as not checked");
assert.ok(
  nodepReport.blocked.some((item) => item.id === "dxf.parsed"),
  "dxf.parsed is the check that could not run",
);
assert.match(nodepRun.stdout, /unknown is not a pass/, "the report must say why 2 is not 0");

// 8. A DWG needs a converter, so it cannot be judged either. The header is real
//    and the size passes; what is missing is the ability to read it.
const dwgDir = path.join(root, "dwg");
fs.mkdirSync(dwgDir);
fs.writeFileSync(
  path.join(dwgDir, "blank.dwg"),
  Buffer.concat([Buffer.from("AC1032", "ascii"), Buffer.alloc(4000, 0)]),
);
const dwgRun = deliver(dwgDir, ["--require", "drawing"]);
assert.equal(dwgRun.code, 2, `an unreadable DWG must exit 2, not pass:\n${dwgRun.stdout}`);
assert.equal(dwgRun.report.verdict, "unknown");
assert.ok(
  dwgRun.report.blocked.some((item) => item.id === "drawing.entities"),
  "drawing.entities is the check that could not run",
);

// 9. A report that states a verdict but carries no self-review evidence is a
//    claim, not a result. One line saying "pass" used to be enough.
const hollow = path.join(root, "hollow");
fs.mkdirSync(hollow);
stepFile(path.join(hollow, "part.step"), "AUTOMOTIVE_DESIGN", 300);
fs.writeFileSync(
  path.join(hollow, "part_review_report.json"),
  JSON.stringify({ evaluation: { status: "pass", issues: [] } }),
  "utf8",
);
const hollowRun = deliver(hollow);
assert.equal(hollowRun.code, 1, `a bare status must not pass:\n${hollowRun.stdout}`);
const hollowIds = hollowRun.report.files.flatMap((f) => f.checks.filter((c) => !c.ok).map((c) => c.id));
assert.ok(hollowIds.includes("review.checks"), `expected review.checks in ${hollowIds}`);

// 10. The report says previews were created, but the folder next to it holds no
//     image. That is the shape of a report written without looking.
const noPix = path.join(root, "nopix");
fs.mkdirSync(noPix);
stepFile(path.join(noPix, "part.step"), "AUTOMOTIVE_DESIGN", 300);
fs.writeFileSync(
  path.join(noPix, "part_review_report.json"),
  JSON.stringify({
    evaluation: { status: "pass", issues: [] },
    checks: { previews_created: true, previews_not_blank: true, expected_outputs_exist: true },
  }),
  "utf8",
);
const noPixRun = deliver(noPix);
assert.equal(noPixRun.code, 1, `previews claimed but absent must fail:\n${noPixRun.stdout}`);
const noPixIds = noPixRun.report.files.flatMap((f) => f.checks.filter((c) => !c.ok).map((c) => c.id));
assert.ok(
  noPixIds.includes("review.previews_on_disk"),
  `expected review.previews_on_disk in ${noPixIds}`,
);

// 11. Upstream marks every report as needing a human. That is a warning, not a
//     failure: it is a limit of the reviewer, not a defect in the delivery.
fs.writeFileSync(
  path.join(good, "part_review_report.json"),
  JSON.stringify({
    evaluation: { status: "pass", issues: [], score: 92 },
    checks: { previews_created: true, previews_not_blank: true, expected_outputs_exist: true },
    manual_review_required: true,
  }),
  "utf8",
);
const manualRun = deliver(good, ["--require", "step"]);
assert.equal(manualRun.code, 0, `a human-review flag must not fail the delivery:\n${manualRun.stdout}`);
assert.ok(
  manualRun.report.files
    .flatMap((f) => f.checks)
    .some((c) => c.id === "review.manual" && c.severity === "warning"),
  "manual_review_required must surface as a warning",
);

// 12. A DWG gets the same treatment as a DXF when a converter can be driven.
//     The converter here is a stub that writes a real DXF, so the path from
//     "unreadable binary" to "fully checked drawing" is exercised end to end.
const dwgReal = path.join(root, "dwgreal");
fs.mkdirSync(dwgReal);
fs.writeFileSync(
  path.join(dwgReal, "sheet.dwg"),
  Buffer.concat([Buffer.from("AC1032", "ascii"), Buffer.alloc(4000, 0)]),
);
const stubSource = path.join(root, "stub-source.dxf");
const stubScript = path.join(root, "stub-converter.py");
const stubBuilt = spawnSync(PY, ["-c", BUILD, root, "stub-source"], { encoding: "utf8" });
if (stubBuilt.status === 0) {
  fs.renameSync(path.join(root, "stub-source.dxf"), stubSource);
}
fs.writeFileSync(
  stubScript,
  [
    "import shutil, sys",
    `shutil.copy(r'${stubSource}', sys.argv[2])`,
    "",
  ].join("\n"),
  "utf8",
);
fs.writeFileSync(
  path.join(dwgReal, "sheet.dwg"),
  Buffer.concat([Buffer.from("AC1032", "ascii"), Buffer.alloc(4000, 0)]),
);
const savedConverter = process.env.DSH_SW_CAD_DWG_CONVERTER;
process.env.DSH_SW_CAD_DWG_CONVERTER = `"${PY}" "${stubScript}" {in} {out}`;
const dwgRealOut = path.join(dwgReal, "delivery.json");
const dwgRealRun = spawnSync(PY, [SCRIPT, dwgReal, "--out", dwgRealOut, "--require", "drawing"], {
  encoding: "utf8",
  env: process.env,
});
if (savedConverter === undefined) delete process.env.DSH_SW_CAD_DWG_CONVERTER;
else process.env.DSH_SW_CAD_DWG_CONVERTER = savedConverter;
if (fs.existsSync(stubSource)) {
  const dwgReport = JSON.parse(fs.readFileSync(dwgRealOut, "utf8"));
  const dwgIds = dwgReport.files.flatMap((f) => f.checks.map((c) => c.id));
  assert.ok(dwgIds.includes("dwg.converted"), `expected dwg.converted in ${dwgIds}`);
  assert.ok(
    dwgIds.includes("drawing.sheets") || dwgIds.includes("drawing.entities"),
    `the converted DWG must be checked like a DXF, got ${dwgIds}`,
  );
  assert.ok(
    !dwgReport.blocked.some((b) => b.id === "drawing.entities"),
    "a converted DWG must not be reported as unchecked",
  );
  console.log(`dwg converted   -> ${dwgReport.verdict}`);
} else {
  console.log("dwg converted   -> skipped (ezdxf not available to build the stub DXF)");
}

fs.rmSync(root, { recursive: true, force: true });
console.log("deliver: 12 cases behave as intended");

