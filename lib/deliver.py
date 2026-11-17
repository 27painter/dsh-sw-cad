#!/usr/bin/env python
"""Check a CAD delivery the way a shop would, and write the evidence down.

The two skills end in different places.  The AutoCAD side has
``drawing_finalize`` -- an eight-step validator that raises instead of
returning, so a model cannot claim success over a broken drawing.  The
SolidWorks side asks the model to look at four previews and says please.
This script is the part both sides can share: it reads whatever landed in the
output folder, checks it against what the file format promises, and exits
non-zero when the delivery does not hold up.

It never runs CAD.  Everything here is read from the files on disk, so it is
safe to run anywhere, and it produces the same verdict twice.

    python deliver.py <dir-or-file>... [--out delivery.json] [--require drawing,pdf]

Exit codes: 0 all checks passed, 1 at least one failed, 2 the arguments were
wrong (nothing to check).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import traceback
from datetime import datetime, timezone

# ------------------------------------------------------------------ kinds

KIND_BY_EXT = {
    ".dxf": "drawing",
    ".dwg": "drawing",
    ".pdf": "pdf",
    ".step": "step",
    ".stp": "step",
    ".sldprt": "model",
    ".sldasm": "model",
    ".prt": "model",
    ".ipt": "model",
    ".iam": "model",
}

# A drawing this small is a title block or a stray line, not a part drawing.
MIN_BYTES = {
    "drawing": 2 * 1024,
    "pdf": 1 * 1024,
    "step": 1 * 1024,
    "model": 10 * 1024,
}

# ISO 216 / GB/T 14689 sheet sizes, portrait w x h in mm.
SHEETS = {
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
    "A2": (420.0, 594.0),
    "A1": (594.0, 841.0),
    "A0": (841.0, 1189.0),
}
SHEET_TOLERANCE_MM = 2.0

# DWG carries its format version in the first six bytes: AC1015 is 2000,
# AC1032 is 2018, and so on.  A .dwg that does not start with AC is not a DWG
# whatever its name says.
DWG_VERSIONS = {
    "AC1015": "2000",
    "AC1018": "2004",
    "AC1021": "2007",
    "AC1024": "2010",
    "AC1027": "2013",
    "AC1032": "2018",
}

AP_SCHEMAS = {
    "AP203": "AP203 (geometry only, no assembly structure or colour)",
    "AP214": "AP214 (the usual mechanical exchange default)",
    "AP242": "AP242 (carries PMI and tolerances as semantics)",
}

# FILE_SCHEMA carries the schema's name, not its AP number, and exporters use
# several spellings of each.  Matching the name is the only reliable way in.
SCHEMA_NAMES = (
    ("AP242", ("ap242", "managed_model_based_3d_engineering")),
    ("AP214", ("automotive_design",)),
    ("AP203", ("config_control_design",)),
)


class Check:
    """One assertion about one file, with the reason written next to it."""

    def __init__(self, ident, ok, detail, severity="error"):
        self.ident = ident
        self.ok = bool(ok)
        self.detail = detail
        self.severity = severity

    def as_dict(self):
        return {"id": self.ident, "ok": self.ok, "detail": self.detail, "severity": self.severity}


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(block)
    return digest.hexdigest()


# ------------------------------------------------------------------ checks


def check_drawing(path, size):
    """DXF is parsed; DWG only gets its header read, because parsing DWG
    needs a licence we are not going to assume."""
    checks = []

    if path.lower().endswith(".dwg"):
        with open(path, "rb") as handle:
            header = handle.read(6)
        version = header.decode("ascii", "replace")
        known = DWG_VERSIONS.get(version)
        checks.append(
            Check(
                "dwg.header",
                known is not None,
                f"format {version} (AutoCAD {known})" if known else f"first bytes are {version!r}, not a DWG version tag",
            )
        )
        checks.append(
            Check(
                "drawing.entities",
                True,
                "not counted: reading DWG needs a converter, so entity and dimension counts are unchecked",
                severity="warning",
            )
        )
        return checks

    try:
        import ezdxf
    except Exception:
        checks.append(Check("dxf.parsed", False, "ezdxf is not importable, so the DXF was not read", severity="warning"))
        return checks

    try:
        doc = ezdxf.readfile(path)
    except Exception as exc:
        checks.append(Check("dxf.parsed", False, f"ezdxf could not read it: {exc}"))
        return checks
    checks.append(Check("dxf.parsed", True, f"DXF {doc.dxfversion}"))

    msp = doc.modelspace()
    entities = list(msp)
    dims = [e for e in entities if e.dxftype() == "DIMENSION"]
    # A dimension is the difference between a drawing and a picture.  Text
    # plus a leader is what the skill forbids, and this is where that shows up.
    checks.append(
        Check(
            "drawing.dimensions",
            len(dims) > 0,
            f"{len(dims)} DIMENSION entit{'y' if len(dims) == 1 else 'ies'} in modelspace",
        )
    )
    checks.append(
        Check("drawing.entities", len(entities) > 0, f"{len(entities)} entities in modelspace")
    )

    layers = [layer.dxf.name for layer in doc.layers]
    checks.append(Check("drawing.layers", len(layers) > 0, f"{len(layers)} layers: {', '.join(sorted(layers)[:8])}"))

    insunits = doc.header.get("$INSUNITS", 0)
    unit_names = {0: "unitless", 1: "inch", 4: "mm", 5: "cm", 6: "m"}
    checks.append(
        Check(
            "drawing.units",
            insunits in (1, 4, 5, 6),
            f"$INSUNITS={insunits} ({unit_names.get(insunits, 'unknown')})",
            severity="error" if insunits == 0 else "warning",
        )
    )

    # A layout that exists but is empty is the default of every new DXF, so
    # counting names proves nothing.  What matters is whether anything was
    # actually placed on the sheet.
    sheets = []
    for name in doc.layouts.names():
        if name.lower() == "model":
            continue
        layout = doc.layouts.get(name)
        placed = len(list(layout))
        if placed:
            sheets.append(f"{name} ({placed} entities)")
    checks.append(
        Check(
            "drawing.sheets",
            len(sheets) > 0,
            "paper space: " + ", ".join(sheets) if sheets else "no sheet has anything on it; nothing frames the drawing",
        )
    )

    # A title block is usually a block reference on the sheet, with the fields
    # filled as text.  This is a heuristic, so it warns rather than fails: the
    # block name is the shop's choice, and a DXF may legitimately carry none.
    title_hint = ("\u56fe\u540d", "\u6bd4\u4f8b", "\u6750\u6599", "\u5236\u56fe", "\u5ba1\u6838", "scale", "material", "drawn", "title")
    found = []
    for name in doc.layouts.names():
        if name.lower() == "model":
            continue
        layout = doc.layouts.get(name)
        for entity in layout:
            if entity.dxftype() == "INSERT":
                found.append(entity.dxf.name)
            elif entity.dxftype() in ("TEXT", "MTEXT"):
                content = (entity.dxf.text if entity.dxftype() == "TEXT" else entity.text).lower()
                if any(word in content for word in title_hint):
                    found.append(content[:24])
    checks.append(
        Check(
            "drawing.titleblock",
            len(found) > 0,
            f"found {', '.join(found[:4])}" if found else "no title block reference or field text on the sheet",
            severity="warning",
        )
    )
    return checks


def check_pdf(path, size):
    checks = []
    try:
        import pymupdf as fitz
    except Exception:
        try:
            import fitz
        except Exception:
            checks.append(Check("pdf.parsed", False, "PyMuPDF is not importable, so the PDF was not read", severity="warning"))
            return checks

    try:
        doc = fitz.open(path)
    except Exception as exc:
        checks.append(Check("pdf.parsed", False, f"PyMuPDF could not read it: {exc}"))
        return checks

    pages = doc.page_count
    checks.append(Check("pdf.parsed", True, f"{pages} page(s), producer {doc.metadata.get('producer') or 'unknown'}"))

    if pages == 0:
        checks.append(Check("pdf.pages", False, "the file has no pages"))
        return checks

    page = doc[0]
    width_mm = page.rect.width * 25.4 / 72.0
    height_mm = page.rect.height * 25.4 / 72.0
    short, long = sorted((width_mm, height_mm))
    sheet = None
    for name, (w, h) in SHEETS.items():
        if abs(short - w) <= SHEET_TOLERANCE_MM and abs(long - h) <= SHEET_TOLERANCE_MM:
            sheet = name
            break
    checks.append(
        Check(
            "pdf.sheet",
            sheet is not None,
            f"{short:.0f} x {long:.0f} mm ({sheet or 'not an ISO A-series sheet'})",
        )
    )

    # A vector PDF of a drawing has text; a picture of a drawing does not.
    text = page.get_text().strip()
    checks.append(
        Check(
            "pdf.text",
            len(text) > 0,
            f"{len(text)} characters of extractable text" if text else "no text layer: dimensions are pixels, not numbers",
        )
    )
    doc.close()
    return checks


def check_step(path, size):
    checks = []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        head = handle.read(16384)

    schema = None
    raw_schema = ""
    for line in head.splitlines():
        if "FILE_SCHEMA" in line.upper():
            raw_schema = line.strip()
            lowered = line.lower()
            for key, names in SCHEMA_NAMES:
                if any(name in lowered for name in names):
                    schema = (key, AP_SCHEMAS[key])
                    break
            break
    checks.append(
        Check(
            "step.schema",
            schema is not None,
            f"{schema[1]}  [{raw_schema[:60]}]" if schema else f"unrecognised FILE_SCHEMA: {raw_schema[:80] or 'no FILE_SCHEMA line'}",
        )
    )
    if schema and schema[0] == "AP203":
        checks.append(
            Check(
                "step.pmi",
                False,
                "AP203 drops colour and assembly structure; use AP214 or AP242 for a mechanical handoff",
                severity="warning",
            )
        )

    # Count the geometry rather than trusting the file size.
    points = 0
    entities = 0
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith("#"):
                entities += 1
                if "CARTESIAN_POINT" in line:
                    points += 1
    checks.append(Check("step.entities", entities > 100, f"{entities} entities, {points} CARTESIAN_POINTs"))
    return checks


def check_model(path, size):
    """Native parts cannot be read without the application, so this is a
    presence check with the format tag where one exists."""
    checks = []
    ext = os.path.splitext(path)[1].lower()
    if ext in (".sldprt", ".sldasm"):
        with open(path, "rb") as handle:
            head = handle.read(16)
        checks.append(
            Check(
                "model.header",
                b"SolidWorks" in head or head.startswith(b"\xd0\xcf\x11\xe0") or head.startswith(b"PK"),
                "SolidWorks file signature present",
            )
        )
    checks.append(
        Check(
            "model.unchecked",
            True,
            "geometry not verified: reading a native part needs the application, so only presence and size were checked",
            severity="warning",
        )
    )
    return checks


def check_review_report(path, size):
    checks = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            report = json.load(handle)
    except Exception as exc:
        checks.append(Check("review.parsed", False, f"not valid JSON: {exc}"))
        return checks

    evaluation = report.get("evaluation") or {}
    status = evaluation.get("status") or report.get("status")
    checks.append(
        Check(
            "review.status",
            str(status).lower() == "pass",
            f"evaluation.status={status!r}",
        )
    )
    issues = evaluation.get("issues") or []
    checks.append(
        Check(
            "review.issues",
            len(issues) == 0,
            f"{len(issues)} issue(s)" + (f": {issues[:3]}" if issues else ""),
        )
    )
    return checks


CHECKERS = {
    "drawing": check_drawing,
    "pdf": check_pdf,
    "step": check_step,
    "model": check_model,
    "report": check_review_report,
}


# ------------------------------------------------------------------ driver


def classify(path):
    name = os.path.basename(path).lower()
    if name.endswith("review_report.json") or name == "review.json":
        return "report"
    return KIND_BY_EXT.get(os.path.splitext(name)[1])


def collect(targets):
    files = []
    for target in targets:
        if os.path.isdir(target):
            for root, _dirs, names in os.walk(target):
                for name in names:
                    files.append(os.path.join(root, name))
        elif os.path.isfile(target):
            files.append(target)
        else:
            raise SystemExit(f"nothing at {target}")
    return sorted(set(files))


def inspect(path):
    kind = classify(path)
    entry = {
        "path": os.path.abspath(path),
        "kind": kind or "other",
        "bytes": os.path.getsize(path),
        "sha256": sha256(path),
        "checks": [],
    }
    if kind is None:
        return entry

    minimum = MIN_BYTES.get(kind, 1)
    entry["checks"].append(
        Check("file.size", entry["bytes"] >= minimum, f"{entry['bytes']} bytes (minimum {minimum})").as_dict()
    )
    try:
        for check in CHECKERS[kind](path, entry["bytes"]):
            entry["checks"].append(check.as_dict())
    except Exception:
        entry["checks"].append(
            Check("check.crashed", False, traceback.format_exc(limit=2).strip().splitlines()[-1]).as_dict()
        )
    return entry


def main(argv):
    parser = argparse.ArgumentParser(description="Check a CAD delivery and write the evidence.")
    parser.add_argument("targets", nargs="+", help="folder(s) or file(s) to check")
    parser.add_argument("--out", default="delivery.json", help="where to write the report (default: delivery.json)")
    parser.add_argument(
        "--require",
        default="",
        help="comma-separated kinds that must be present, e.g. drawing,pdf or step",
    )
    parser.add_argument("--json", action="store_true", help="print the report to stdout as well")
    parser.add_argument("--quiet", action="store_true", help="only the verdict line")
    args = parser.parse_args(argv)

    files = collect(args.targets)
    entries = [inspect(path) for path in files]

    present = {entry["kind"] for entry in entries if entry["kind"] != "other"}
    required = [item.strip() for item in args.require.split(",") if item.strip()]
    missing = [kind for kind in required if kind not in present]

    failures = []
    warnings = []
    for entry in entries:
        for check in entry["checks"]:
            if check["ok"]:
                continue
            if check["severity"] == "warning":
                warnings.append((entry["path"], check))
            else:
                failures.append((entry["path"], check))

    for kind in missing:
        failures.append(("(delivery)", {"id": f"required.{kind}", "detail": f"no {kind} in the delivery", "severity": "error"}))

    # An empty folder passing every check is the worst possible answer: it
    # reports success for a job that produced nothing.
    if not any(entry["kind"] != "other" for entry in entries):
        failures.append(
            ("(delivery)", {"id": "delivery.empty", "detail": "no drawing, PDF, STEP or model in the target", "severity": "error"})
        )

    verdict = "fail" if failures else "pass"
    report = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "targets": [os.path.abspath(t) for t in args.targets],
        "verdict": verdict,
        "required": required,
        "missing": missing,
        "counts": {
            "files": len(entries),
            "checked": sum(1 for e in entries if e["kind"] != "other"),
            "failures": len(failures),
            "warnings": len(warnings),
        },
        "files": entries,
    }

    out = args.out if os.path.isabs(args.out) else os.path.abspath(args.out)
    with open(out, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    if not args.quiet:
        for entry in entries:
            if entry["kind"] == "other":
                continue
            print(f"{entry['kind']:<8} {os.path.basename(entry['path'])}  {entry['bytes']} B")
            for check in entry["checks"]:
                mark = "ok  " if check["ok"] else ("warn" if check["severity"] == "warning" else "FAIL")
                print(f"  {mark} {check['id']:<22} {check['detail']}")
        if missing:
            print(f"\nmissing: {', '.join(missing)}")
    print(f"verdict: {verdict}  ({len(failures)} failure(s), {len(warnings)} warning(s))  -> {out}")
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if verdict == "pass" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
