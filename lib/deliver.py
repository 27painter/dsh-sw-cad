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
import atexit
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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


# ------------------------------------------------------------------ DWG input
#
# A DWG cannot be parsed here: the format needs a converter we will not bundle.
# What we can do is drive one that is already on the machine, so a DWG gets the
# same treatment as a DXF instead of being waved through on its header.
#
# Order: an explicit override first (so a site can point at their own tool),
# then LibreDWG's dwg2dxf, then an ODA File Converter installation.

_CONVERT_TEMP = []


def _cleanup_temp():
    for folder in _CONVERT_TEMP:
        shutil.rmtree(folder, ignore_errors=True)


atexit.register(_cleanup_temp)


def find_dwg_converter():
    """Return (kind, command) for something that can turn a DWG into a DXF."""
    override = os.environ.get("DSH_SW_CAD_DWG_CONVERTER", "").strip()
    if override:
        return ("configured", override)

    dwg2dxf = shutil.which("dwg2dxf")
    if dwg2dxf:
        return ("libredwg", dwg2dxf)

    for pattern in (
        r"C:\Program Files\ODA\*\ODAFileConverter.exe",
        r"C:\Program Files (x86)\ODA\*\ODAFileConverter.exe",
        "/opt/ODA/ODAFileConverter/ODAFileConverter",
        "/usr/bin/ODAFileConverter",
    ):
        hits = sorted(glob.glob(pattern))
        if hits:
            return ("oda", hits[-1])
    return None


def convert_dwg(path, converter):
    """Convert one DWG to a DXF in a temporary folder. None when it fails."""
    kind, command = converter
    workdir = tempfile.mkdtemp(prefix="dsh-sw-cad-dwg-")
    _CONVERT_TEMP.append(workdir)
    source = os.path.join(workdir, "in")
    outdir = os.path.join(workdir, "out")
    os.makedirs(source)
    os.makedirs(outdir)
    stem = os.path.splitext(os.path.basename(path))[0]
    copied = os.path.join(source, stem + ".dwg")
    try:
        shutil.copy2(path, copied)
    except OSError:
        return None

    try:
        if kind == "libredwg":
            target = os.path.join(outdir, stem + ".dxf")
            res = subprocess.run(
                [command, copied, "-o", target],
                capture_output=True, text=True, timeout=180,
            )
        elif kind == "oda":
            res = subprocess.run(
                [command, source, outdir, "ACAD2018", "DXF", "0", "1", "*.DWG"],
                capture_output=True, text=True, timeout=300,
            )
        else:
            # "configured": a command template, {in} and {out} substituted.
            target = os.path.join(outdir, stem + ".dxf")
            line = command.replace("{in}", copied).replace("{out}", target)
            res = subprocess.run(line, shell=True, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return None

    candidates = sorted(
        os.path.join(outdir, name)
        for name in os.listdir(outdir)
        if name.lower().endswith(".dxf")
    )
    if candidates and os.path.getsize(candidates[0]) > 0:
        return candidates[0]
    # A converter that exits 0 but writes nothing is still a failure.
    _ = res
    return None


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
        converter = find_dwg_converter()
        converted = convert_dwg(path, converter) if converter and known else None
        if converted:
            # From here the DWG is checked exactly like a DXF, because it now is
            # one. Nothing below knows or cares that the input was a DWG.
            checks.append(
                Check(
                    "dwg.converted",
                    True,
                    f"converted to DXF with {converter[0]} ({os.path.basename(converter[1])}); "
                    "the checks below read the converted file",
                    severity="warning",
                )
            )
            path = converted
        else:
            reason = (
                "no DWG-to-DXF converter was found (install LibreDWG's dwg2dxf, ODA File Converter, "
                "or set DSH_SW_CAD_DWG_CONVERTER to a command using {in} and {out})"
                if not converter
                else "the converter produced no DXF"
            )
            checks.append(
                Check(
                    "drawing.entities",
                    False,
                    f"not checked: {reason}, so entities, units, sheets and dimensions were not examined",
                    severity="blocked",
                )
            )
            return checks

    try:
        import ezdxf
    except Exception:
        # A reader that is missing is not a pass. Reporting one here would tell
        # the caller "this drawing holds up" while nothing was read at all — and
        # the caller stops looking. This used to be a warning, which meant any
        # DXF passed on a machine without ezdxf.
        checks.append(
            Check(
                "dxf.parsed",
                False,
                "ezdxf is not importable, so this DXF was NOT checked "
                "(install ezdxf, or run deliver with an interpreter that has it)",
                severity="blocked",
            )
        )
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

    # The sheet the drawing will be plotted on.  A DXF leaves this at the A3
    # default until someone sets it, so a mismatch is a warning: the drawing is
    # still readable, it just will not plot at the size the title block claims.
    sheets_seen = []
    for name in doc.layouts.names():
        if name.lower() == "model":
            continue
        layout = doc.layouts.get(name)
        if not len(list(layout)):
            continue
        width = layout.dxf.get("paper_width", 0)
        height = layout.dxf.get("paper_height", 0)
        short, long = sorted((float(width), float(height)))
        matched = None
        for sheet_name, (w, h) in SHEETS.items():
            if abs(short - w) <= SHEET_TOLERANCE_MM and abs(long - h) <= SHEET_TOLERANCE_MM:
                matched = sheet_name
                break
        sheets_seen.append((name, short, long, matched))
    if sheets_seen:
        detail = "; ".join(f"{n} {s:.0f}x{l:.0f}mm ({m or 'not ISO A'})" for n, s, l, m in sheets_seen)
        checks.append(Check("drawing.sheet", all(m for *_rest, m in sheets_seen), detail, severity="warning"))

    # 比例 / SCALE, stated on the sheet.  dimlfac is not a substitute: it is a
    # measurement factor on a dimension style, and a style left at 100 (as the
    # ezdxf templates are) says nothing about the plot scale.  It is reported
    # next to the verdict, not counted as one.
    scale_pattern = re.compile(r"(?:比例|SCALE)\s*[:：]?\s*([0-9]+\s*[:：]\s*[0-9]+)", re.I)
    scale_found = []
    for name in doc.layouts.names():
        if name.lower() == "model":
            continue
        for entity in doc.layouts.get(name):
            if entity.dxftype() == "TEXT":
                text = entity.dxf.text
            elif entity.dxftype() == "MTEXT":
                text = entity.text
            else:
                continue
            hit = scale_pattern.search(text)
            if hit:
                scale_found.append(hit.group(0))
    factors = []
    for entity in msp.query("DIMENSION"):
        style_name = entity.dxf.get("dimstyle", "Standard")
        try:
            factor = float(doc.dimstyles.get(style_name).dxf.get("dimlfac", 1.0))
        except Exception:
            continue
        if abs(factor - 1.0) > 1e-9:
            factors.append(f"{style_name} dimlfac={factor:g}")
    detail = ", ".join(sorted(set(scale_found))[:3]) if scale_found else "no 比例/SCALE stated on the sheet"
    if factors:
        detail += "  (dimension style factor: " + ", ".join(sorted(set(factors))[:2]) + ")"
    checks.append(Check("drawing.scale", len(scale_found) > 0, detail, severity="error" if sheets_seen else "warning"))

    # Line weights: a mechanical drawing separates thick outlines from thin
    # dimensions by weight, not only by colour.  One weight everywhere means
    # the plot comes out flat.
    weights = set()
    for layer in doc.layers:
        value = layer.dxf.get("lineweight", -3)
        weights.add(value)
    distinct = {w for w in weights if w >= 0}
    checks.append(
        Check(
            "drawing.lineweights",
            len(distinct) >= 2,
            f"{len(distinct)} explicit line weight(s) across {len(doc.layers)} layers"
            + ("" if len(distinct) >= 2 else "; thick and thin will plot the same"),
            severity="warning",
        )
    )

    # A dimension whose text disagrees with the geometry is worse than a missing
    # dimension: the drawing names a number the part does not have, and it looks
    # authoritative while doing it. Only an overridden text can disagree — "<>"
    # and an empty string both make the application print the measured value, so
    # there is nothing to compare there.
    number_re = re.compile(r"-?\d+(?:[.,]\d+)?")
    mismatched = []
    compared = 0
    skipped_factor = 0
    for entity in msp.query("DIMENSION"):
        raw_text = entity.dxf.get("text", "")
        if raw_text in ("", "<>"):
            continue
        dim_kind = entity.dimtype & 7  # the low 3 bits; the rest are flag bits
        if dim_kind in (2, 5, 6):  # angular and ordinate report other quantities
            continue
        try:
            measured = float(entity.get_measurement())
        except Exception:
            continue
        style_name = entity.dxf.get("dimstyle", "Standard")
        try:
            factor = float(doc.dimstyles.get(style_name).dxf.get("dimlfac", 1.0))
        except Exception:
            factor = 1.0
        # With a measurement factor in play the printed value is the geometry
        # times that factor, and there is no way to tell from the file alone
        # whether a disagreement is a wrong text or a wrong factor. Say so and
        # leave it to drawing.scale, which already reports the factor.
        if abs(factor - 1.0) > 1e-9:
            skipped_factor += 1
            continue
        expected = measured
        cleaned = raw_text.replace("%%c", "").replace("%%C", "")
        numbers = number_re.findall(cleaned)
        if not numbers:
            continue
        try:
            shown = float(numbers[0].replace(",", "."))
        except ValueError:
            continue
        compared += 1
        tolerance = max(0.01, abs(expected) * 0.002)
        if abs(shown - expected) > tolerance:
            mismatched.append((entity.dxf.get("handle", "?"), shown, measured))
    factor_note = (
        f"; {skipped_factor} more skipped because their style has a dimlfac other than 1" if skipped_factor else ""
    )
    if mismatched:
        preview = "; ".join(
            f"handle {handle}: text says {shown:g}, geometry measures {measured:g}"
            for handle, shown, measured in mismatched[:4]
        )
        more = f" (+{len(mismatched) - 4} more)" if len(mismatched) > 4 else ""
        checks.append(
            Check(
                "drawing.dim_values",
                False,
                f"{len(mismatched)} of {compared} overridden dimension text(s) disagree with the geometry: "
                f"{preview}{more}{factor_note}",
            )
        )
    else:
        checks.append(
            Check(
                "drawing.dim_values",
                True,
                (
                    f"{compared} overridden dimension text(s), all within 0.2% of the measured geometry"
                    if compared
                    else "no dimension text was overridden, so every value comes from the geometry"
                )
                + factor_note,
                severity="warning",
            )
        )

    # Chinese drawings are set in 仿宋 (or 宋体 for headings).  If the sheet
    # carries Chinese text and every style points at a Latin font, the plot
    # shows boxes on a machine without that font.
    cjk = re.compile(r"[\u4e00-\u9fff]")
    has_cjk = False
    used_styles = set()
    for layout_name in doc.layouts.names():
        layout = doc.layouts.get(layout_name)
        for entity in layout:
            kind = entity.dxftype()
            text = None
            if kind == "TEXT":
                text = entity.dxf.text
                used_styles.add(entity.dxf.get("style", "Standard"))
            elif kind == "MTEXT":
                text = entity.text
                used_styles.add(entity.dxf.get("style", "Standard"))
            if text and cjk.search(text):
                has_cjk = True
    if has_cjk:
        fonts = []
        for style_name in used_styles:
            try:
                font = doc.styles.get(style_name).dxf.get("font", "")
            except Exception:
                font = ""
            fonts.append(f"{style_name}:{font or 'none'}")
        gb_font = re.compile(r"(fangsong|simsun|simhei|song|kai|msyh|yahei)", re.I)
        ok_font = any(gb_font.search(item) for item in fonts)
        checks.append(
            Check(
                "drawing.fonts",
                ok_font,
                ", ".join(fonts[:4]) + ("" if ok_font else "  — Chinese text with no CJK font assigned"),
                severity="warning",
            )
        )

    # Dimension text that lands on top of other dimension text.  Measured from
    # text_midpoint, so it catches the crowded cases a reviewer would circle.
    mids = []
    for entity in msp.query("DIMENSION"):
        mid = entity.dxf.get("text_midpoint", None)
        if mid is not None:
            mids.append((float(mid[0]), float(mid[1])))
    close = 0
    for i in range(len(mids)):
        for j in range(i + 1, len(mids)):
            if abs(mids[i][0] - mids[j][0]) < 2.0 and abs(mids[i][1] - mids[j][1]) < 2.0:
                close += 1
    checks.append(
        Check(
            "drawing.dim_overlap",
            close == 0,
            f"{close} pair(s) of dimension texts within 2 mm of each other" if close else f"{len(mids)} dimension texts, none overlapping",
            severity="warning",
        )
    )

    if PROFILE == "half":
        checks.extend(check_standards(doc))
    return checks


def check_pdf(path, size):
    checks = []
    try:
        import pymupdf as fitz
    except Exception:
        try:
            import fitz
        except Exception:
            checks.append(
                Check(
                    "pdf.parsed",
                    False,
                    "PyMuPDF is not importable, so this PDF was NOT checked "
                    "(install pymupdf, or run deliver with an interpreter that has it)",
                    severity="blocked",
                )
            )
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

    # A bounding box from the raw coordinates.  This is not a geometry check —
    # it does not know faces from holes — but it does catch the two things that
    # silently ruin a handoff: the part was scaled, or the export carries no
    # geometry at all.  Reading B-Rep properly needs OCCT and 200 MB of wheels,
    # which is more than this bundle is willing to add for one check.
    pattern = re.compile(
        r"CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(\s*([-0-9.eE+]+)\s*,\s*([-0-9.eE+]+)\s*,\s*([-0-9.eE+]+)\s*\)"
    )
    xs = []
    ys = []
    zs = []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            hit = pattern.search(line)
            if hit:
                xs.append(float(hit.group(1)))
                ys.append(float(hit.group(2)))
                zs.append(float(hit.group(3)))
    if xs:
        size = (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
        degenerate = max(size) <= 1e-6
        checks.append(
            Check(
                "step.bbox",
                not degenerate,
                "bounding box %.3f x %.3f x %.3f (approximate, from raw points)" % size
                if not degenerate
                else "every point sits at the same place: the export carries no geometry",
            )
        )
    else:
        checks.append(Check("step.bbox", False, "no CARTESIAN_POINT coordinates could be read", severity="warning"))
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

    # `evaluation.status` is written by the model that did the work. On its own
    # it is a claim, not evidence: "status": "pass" costs one line and proves
    # nothing. What follows asks for the artefacts behind the claim.
    detail = report.get("checks")
    has_detail = isinstance(detail, dict) and len(detail) > 0
    checks.append(
        Check(
            "review.checks",
            has_detail,
            f"{len(detail)} self-review check(s): {', '.join(sorted(detail)[:4])}"
            if has_detail
            else "no 'checks' block: the report states a verdict but carries no self-review evidence",
        )
    )

    if has_detail:
        created = detail.get("previews_created")
        not_blank = detail.get("previews_not_blank")
        problems = []
        if created is not None and not created:
            problems.append("previews_created=false")
        if not_blank is not None and not not_blank:
            problems.append("previews_not_blank=false")
        if created is not None or not_blank is not None:
            checks.append(
                Check(
                    "review.previews",
                    not problems,
                    "previews_created={!r}, previews_not_blank={!r}".format(created, not_blank)
                    + ("" if not problems else ": " + ", ".join(problems)),
                )
            )

        outputs = detail.get("expected_outputs_exist")
        if outputs is not None:
            checks.append(
                Check(
                    "review.outputs",
                    bool(outputs),
                    f"expected_outputs_exist={outputs!r}",
                )
            )

    # A preview that the report says was created has to exist somewhere. The
    # reviewer writes them next to the report, so an empty folder next to a
    # "previews_created: true" claim is the signature of a report nobody filled
    # by looking at the model.
    if has_detail and detail.get("previews_created"):
        folder = os.path.dirname(os.path.abspath(path))
        try:
            images = [
                name
                for name in os.listdir(folder)
                if name.lower().endswith((".bmp", ".png", ".jpg", ".jpeg"))
            ]
        except OSError:
            images = []
        checks.append(
            Check(
                "review.previews_on_disk",
                len(images) > 0,
                f"{len(images)} preview image(s) beside the report"
                if images
                else "the report claims previews_created=true but there is no image next to it",
            )
        )

    # Upstream sets this on purpose: a structured report cannot replace a human
    # accepting the geometry. It stays a warning because it is a known limit of
    # the reviewer, not a defect in the delivery — but a pass must not be read as
    # "someone looked at this".
    manual = report.get("manual_review_required")
    if manual is None:
        manual = evaluation.get("manual_review_required")
    if manual:
        checks.append(
            Check(
                "review.manual",
                False,
                "the report sets manual_review_required=true: these files read back, but no human has accepted "
                "the geometry",
                severity="warning",
            )
        )

    score = evaluation.get("score")
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        checks.append(Check("review.score", True, f"evaluation.score={score:g}", severity="warning"))
    return checks


CHECKERS = {
    "drawing": check_drawing,
    "pdf": check_pdf,
    "step": check_step,
    "model": check_model,
    "report": check_review_report,
}

# Which checks run. "normal" answers "do the files hold up"; "half" additionally
# requires the values written on the drawing to come from lib/standards.json.
PROFILE = "normal"

STANDARDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "standards.json")


def load_standards():
    with open(STANDARDS_FILE, "r", encoding="utf-8") as handle:
        return json.load(handle)


def drawing_text(doc):
    """Every string the drawing carries, wherever it sits."""
    parts = []
    for name in doc.layouts.names():
        layout = doc.layouts.get(name)
        for entity in layout:
            kind = entity.dxftype()
            if kind == "TEXT":
                parts.append(entity.dxf.text)
            elif kind == "MTEXT":
                parts.append(entity.text)
            elif kind == "DIMENSION":
                override = entity.dxf.get("text", "")
                if override and override not in ("<>", ""):
                    parts.append(override)
            elif kind == "INSERT":
                for attrib in entity.attribs:
                    parts.append(attrib.dxf.text)
    return "\n".join(parts)


def check_standards(doc):
    """Half-delivery checks: the values must come from the table, not from the
    model's imagination. A value outside the table is a fail, because the point
    of the table is that a reviewer can look the value up."""
    table = load_standards()
    text = drawing_text(doc)
    checks = []

    material_names = [name for group, values in table["materials"].items() if not group.startswith("_") for name in values]
    # A material is stated either as 材料: 45 or by naming the grade outright.
    hit = next((name for name in material_names if re.search(r"(?<![0-9A-Za-z])" + re.escape(name) + r"(?![0-9A-Za-z])", text)), None)
    checks.append(
        Check(
            "standards.material",
            hit is not None,
            f"材料 {hit}" if hit else "no material from lib/standards.json appears on the drawing",
        )
    )

    stated = re.search(r"(?:材料|MATERIAL)\s*[:：]?\s*([^\s,，;；]{1,20})", text, re.I)
    if stated and hit is None:
        checks.append(
            Check(
                "standards.material_table",
                False,
                f"材料栏写的是 {stated.group(1)!r}, which is not in lib/standards.json — add it to the table or use a listed grade",
            )
        )

    # No leading \b: in "φ40H7/g6" the H follows a digit, so a word boundary
    # never appears there and the code would go unnoticed.
    fits = set(re.findall(r"([A-Z]{1,2}\d{1,2}/[a-z]{1,2}\d{1,2})(?![0-9A-Za-z])", text))
    known_fits = {item for group, values in table["fits"].items() if not group.startswith("_") for item in values}
    if fits:
        unknown = sorted(fits - known_fits)
        checks.append(
            Check(
                "standards.fit",
                not unknown,
                f"{', '.join(sorted(fits))}" if not unknown else f"not in the table: {', '.join(unknown)}",
            )
        )
    else:
        checks.append(Check("standards.fit", True, "no fit code on the drawing", severity="warning"))

    rough = set(re.findall(r"Ra\s*([0-9]+(?:\.[0-9]+)?)", text, re.I))
    if rough:
        allowed = {f"{value:g}" for value in table["roughness_ra"]["values"]}
        unknown = sorted(value for value in rough if value not in allowed)
        checks.append(
            Check(
                "standards.roughness",
                not unknown,
                "Ra " + ", ".join(sorted(rough)) if not unknown else f"Ra {', '.join(unknown)} not in the preferred series",
            )
        )
    else:
        checks.append(
            Check("standards.roughness", False, "no Ra value anywhere; a half-delivery drawing states surface finish")
        )

    return checks


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
    parser.add_argument(
        "--profile",
        default="normal",
        choices=("normal", "half"),
        help="normal checks the files; half also requires tolerance, material and finish to come from lib/standards.json",
    )
    args = parser.parse_args(argv)

    global PROFILE
    PROFILE = args.profile

    files = collect(args.targets)
    entries = [inspect(path) for path in files]

    present = {entry["kind"] for entry in entries if entry["kind"] != "other"}
    required = [item.strip() for item in args.require.split(",") if item.strip()]
    missing = [kind for kind in required if kind not in present]

    failures = []
    warnings = []
    blocked = []
    for entry in entries:
        for check in entry["checks"]:
            if check["ok"]:
                continue
            if check["severity"] == "blocked":
                blocked.append((entry["path"], check))
            elif check["severity"] == "warning":
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

    # Three answers, not two. "unknown" exists because a check that could not
    # run is not a pass: calling it one tells the caller to stop looking, which
    # is the worst failure this tool can have. Exit code 2 keeps it distinct
    # from a real 1, so a script can tell "this delivery is bad" apart from
    # "this machine cannot judge it".
    if failures:
        verdict = "fail"
    elif blocked:
        verdict = "unknown"
    else:
        verdict = "pass"
    not_checked = [
        "审签（设计/校对/审核/批准）",
        "尺寸链闭合",
        "零件可加工性",
    ]
    report = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "profile": args.profile,
        "targets": [os.path.abspath(t) for t in args.targets],
        "verdict": verdict,
        "required": required,
        "missing": missing,
        "not_checked": not_checked if args.profile == "half" else [],
        "counts": {
            "files": len(entries),
            "checked": sum(1 for e in entries if e["kind"] != "other"),
            "failures": len(failures),
            "warnings": len(warnings),
            "blocked": len(blocked),
        },
        "blocked": [
            {"path": path, "id": check["id"], "detail": check["detail"]} for path, check in blocked
        ],
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
                if check["ok"]:
                    mark = "ok  "
                elif check["severity"] == "blocked":
                    mark = "BLOCK"
                elif check["severity"] == "warning":
                    mark = "warn"
                else:
                    mark = "FAIL"
                print(f"  {mark} {check['id']:<22} {check['detail']}")
        if missing:
            print(f"\nmissing: {', '.join(missing)}")
    print(f"profile: {args.profile}" + ("  (values must come from lib/standards.json; 审签 and 尺寸链 still need a human)" if args.profile == "half" else ""))
    print(f"verdict: {verdict}  ({len(failures)} failure(s), {len(warnings)} warning(s), {len(blocked)} not checked)  -> {out}")
    if verdict == "unknown":
        print(
            "unknown is not a pass: a reader this check needs was missing, so part of the delivery was never examined. "
            "Install the dependency (or run deliver with the venv interpreter) and judge again."
        )
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    if verdict == "pass":
        return 0
    return 2 if verdict == "unknown" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
