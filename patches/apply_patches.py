#!/usr/bin/env python
"""Apply the upstream fixes this bundle depends on, idempotently.

Both fixes are offered upstream as pull requests. Until those land (or if a user
installs a release that predates them), the installed copies are missing them,
and the symptoms are silent: a vertical dimension measures 0.0, and an edge's
diameter and length come back empty.

Each patch is a list of (old, new) string replacements plus a marker. The marker
is checked first, so running this twice is safe, and a patch whose anchor has
moved is reported as "drift" instead of being applied blindly to the wrong code.

Exit code: 0 when every patch is applied or already present, 1 when any patch
could not be applied because the upstream text changed.
"""
import argparse
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# SolidWorks skill: edge length and circle diameter
# ---------------------------------------------------------------------------
SW_HELPERS = '''
def _edge_curve(edge):
    """返回 IEdge 的 ICurve。

    pywin32 的属性访问在部分 SolidWorks 版本上会对 GetCurve 抛
    DISP_E_MEMBERNOTFOUND，但该成员其实存在（dispid=1）——同一条边的
    GetCurveParams2 / GetTwoAdjacentFaces2 都正常。直接 Invoke 再包装可以绕开。
    """
    try:
        curve = get_com_member(edge, "GetCurve", default=None)
        if curve is not None:
            return curve
    except Exception:
        pass
    try:
        import pythoncom
        from win32com.client import dynamic
    except ImportError:
        return None
    try:
        raw = edge._oleobj_.Invoke(1, 0, pythoncom.DISPATCH_METHOD, True)
        return dynamic.Dispatch(raw)
    except Exception:
        return None


def _curve_span(curve_params):
    """从 GetCurveParams2 取 (起参, 止参)。

    实测布局是 [起点x, 起点y, 起点z, 终点x, 终点y, 终点z, 起参, 止参, ...]，
    共 11 个值，参数在索引 6、7。若某些版本只返回两个值，退回索引 0、1。
    """
    if not curve_params:
        return None
    try:
        if len(curve_params) >= 8:
            return float(curve_params[6]), float(curve_params[7])
        if len(curve_params) >= 2:
            return float(curve_params[0]), float(curve_params[1])
    except (TypeError, ValueError):
        return None
    return None


'''

SW_EDGE = {
    "id": "sw-edge-length-diameter",
    "title": "SolidWorks skill: read edge length and circle diameter through ICurve",
    "pr": "https://github.com/wzyn20051216/solidworks-automation-skill/pull/67",
    "target": ("sw-skill", "scripts/sw_edge_select.py"),
    "marker": "_edge_curve",
    "edits": [
        (
            '''def describe_edge(edge, index=0):''',
            SW_HELPERS.lstrip("\n") + '''def describe_edge(edge, index=0):''',
        ),
        (
            '''    curve_params = None
    try:
        curve = get_com_member(edge, "GetCurve")
        curve_params = list(get_com_member(edge, "GetCurveParams2") or [])
        descriptor["is_line"] = bool(_safe(curve, "IsLine"))
        descriptor["is_circle"] = bool(_safe(curve, "IsCircle"))
    except Exception as exc:
        descriptor["errors"].append(f"读取曲线类型失败: {exc}")

    try:
        descriptor["length_mm"] = round(float(_safe(edge, "GetLength")) * _MM_PER_M, 6)
    except Exception as exc:
        descriptor["errors"].append(f"读取边长失败: {exc}")''',
            '''    curve_params = None
    curve = _edge_curve(edge)
    try:
        curve_params = list(get_com_member(edge, "GetCurveParams2") or [])
    except Exception as exc:
        descriptor["errors"].append(f"读取曲线参数失败: {exc}")
    try:
        if curve is not None:
            descriptor["is_line"] = bool(get_com_member(curve, "IsLine"))
            descriptor["is_circle"] = bool(get_com_member(curve, "IsCircle"))
    except Exception as exc:
        descriptor["errors"].append(f"读取曲线类型失败: {exc}")

    # 边长：IEdge 没有 GetLength，长度在 ICurve::GetLength3(起参, 止参) 上。
    try:
        span = _curve_span(curve_params)
        if curve is not None and span is not None:
            length_m = float(get_com_member(curve, "GetLength3", span[0], span[1]))
            descriptor["length_mm"] = round(length_m * _MM_PER_M, 6)
    except Exception as exc:
        descriptor["errors"].append(f"读取边长失败: {exc}")''',
        ),
        (
            '''    if curve_params and len(curve_params) >= 7:
        if descriptor["is_circle"]:
            candidate = abs(float(curve_params[6])) * 2.0 * _MM_PER_M
            length = descriptor.get("length_mm") or 0.0
            # 整圆时周长 = π*d；圆弧时直径仍应不大于边长。
            if 0.0 < candidate <= max(length, 1e-6) * 4.0 + 1e-6:
                descriptor["diameter_mm"] = round(candidate, 6)
        elif descriptor["direction"] is None:
            descriptor["direction"] = _normalize(
                [float(curve_params[3 + axis]) for axis in range(3)]
            )''',
            '''    # 直径：ICurve::CircleParams 的第 7 个元素是半径。
    # GetCurveParams2 里根本没有半径——旧代码取 curve_params[6]，实际拿到的是
    # 起点坐标的分量或参数，任何版本上都算不出正确直径。
    if descriptor["is_circle"] and curve is not None:
        try:
            circle = list(get_com_member(curve, "CircleParams") or [])
            if len(circle) >= 7:
                candidate = abs(float(circle[6])) * 2.0 * _MM_PER_M
                if candidate > 0.0:
                    descriptor["diameter_mm"] = round(candidate, 6)
        except Exception as exc:
            descriptor["errors"].append(f"读取直径失败: {exc}")
    elif descriptor["direction"] is None and curve_params and len(curve_params) >= 6:
        try:
            descriptor["direction"] = _normalize(
                [float(curve_params[3 + axis]) for axis in range(3)]
            )
        except (TypeError, ValueError):
            pass''',
        ),
    ],
}

# ---------------------------------------------------------------------------
# autocad-mcp-pro: dimension rotation and angular minor angle
# ---------------------------------------------------------------------------
ACAD_HELPERS = '''
def _auto_linear_rotation(x1, y1, x2, y2) -> float:
    """Rotation for AddDimRotated when the caller did not pin one, in radians.

    AddDimRotated measures the projection of the two points onto its dimension
    line. A vertical pair (same X) with rotation 0 therefore measures 0.0, and
    AutoCAD still creates the entity without complaint - the failure is silent
    in the drawing, not in the API.
    """
    return math.pi / 2 if abs(y2 - y1) > abs(x2 - x1) else 0.0


def _ray_sweep(vx, vy, x1, y1, x2, y2) -> float:
    """Counter-clockwise sweep from ray 1 to ray 2, in radians, within [0, 2pi).

    AddDimAngular stores this sweep in the DXF: the entity's COM Measurement
    always reports the minor angle, but a consumer reading the DXF back gets the
    reflex angle when the rays are handed over the other way round.
    """
    a1 = math.atan2(y1 - vy, x1 - vx)
    a2 = math.atan2(y2 - vy, x2 - vx)
    return (a2 - a1) % (2.0 * math.pi)


'''

ACAD_COM = {
    "id": "acad-dimension-rotation",
    "title": "autocad-mcp-pro: linear dimension rotation follows the points",
    "pr": "https://github.com/U-C4N/Autocad-MCP/pull/16",
    "target": ("acad-site", "backends/com_backend.py"),
    "marker": "_auto_linear_rotation",
    "edits": [
        (
            '''class ComBackend(AutoCADBackend):''',
            ACAD_HELPERS.lstrip("\n") + '''class ComBackend(AutoCADBackend):''',
        ),
        (
            '''        dim_x,
        dim_y,
        rotation=0.0,
        layer=None,
        tol_upper=None,''',
            '''        dim_x,
        dim_y,
        rotation=None,
        layer=None,
        tol_upper=None,''',
        ),
        (
            '''            dim = mspace.AddDimRotated(
                _apoint(x1, y1),
                _apoint(x2, y2),
                _apoint(dim_x, dim_y),
                deg2rad(rotation),
            )''',
            '''            angle = (
                _auto_linear_rotation(x1, y1, x2, y2)
                if rotation is None
                else deg2rad(rotation)
            )
            dim = mspace.AddDimRotated(
                _apoint(x1, y1),
                _apoint(x2, y2),
                _apoint(dim_x, dim_y),
                angle,
            )''',
        ),
        (
            '''        tx,
        ty,
        layer=None,
    ) -> EntityInfo:
        def _sync():
            mspace = _msp()
            dim = mspace.AddDimAngular(
                _apoint(vx, vy),
                _apoint(x1, y1),
                _apoint(x2, y2),
                _apoint(tx, ty),
            )''',
            '''        tx,
        ty,
        layer=None,
        minor_angle=True,
    ) -> EntityInfo:
        def _sync():
            mspace = _msp()
            rx1, ry1, rx2, ry2 = x1, y1, x2, y2
            if minor_angle and _ray_sweep(vx, vy, rx1, ry1, rx2, ry2) > math.pi:
                # Swap before creating: the sweep is what lands in the DXF, and
                # COM Measurement always reports the minor angle either way.
                rx1, ry1, rx2, ry2 = rx2, ry2, rx1, ry1
            dim = mspace.AddDimAngular(
                _apoint(vx, vy),
                _apoint(rx1, ry1),
                _apoint(rx2, ry2),
                _apoint(tx, ty),
            )''',
        ),
    ],
}

ACAD_SERVER = {
    "id": "acad-dimension-tool-defaults",
    "title": "autocad-mcp-pro: tool layer accepts an omitted rotation and minor_angle",
    "pr": "https://github.com/U-C4N/Autocad-MCP/pull/16",
    "target": ("acad-site", "server.py"),
    "marker": '"Angle of the measured dimension in degrees',
    "edits": [
        (
            '''    rotation: Annotated[float, "Angle of the measured dimension (0=horizontal, 90=vertical)"] = 0.0,''',
            '''    rotation: Annotated[
        float | None,
        "Angle of the measured dimension in degrees (0=horizontal, 90=vertical). "
        "Omit to let it follow the two points, which is what a vertical pair needs.",
    ] = None,''',
        ),
        (
            '''    angle = math.radians(rotation)''',
            '''    if rotation is None:
        rotation = 90.0 if abs(y2 - y1) > abs(x2 - x1) else 0.0
    angle = math.radians(rotation)''',
        ),
        (
            '''    text_x: Annotated[float, "Dimension text position X"],
    text_y: Annotated[float, "Dimension text position Y"],
    layer: Annotated[str | None, "Layer name"] = None,
    ctx: Context = None,
) -> dict:
    """Create an angular dimension measuring the angle between two lines from a vertex."""
    result = await _backend(ctx).dimension_angular(
        vertex_x, vertex_y, x1, y1, x2, y2, text_x, text_y, layer
    )''',
            '''    text_x: Annotated[float, "Dimension text position X"],
    text_y: Annotated[float, "Dimension text position Y"],
    layer: Annotated[str | None, "Layer name"] = None,
    minor_angle: Annotated[
        bool,
        "Measure the angle below 180 degrees. Set false to keep whichever angle "
        "the ray order produces (the reflex angle above 180).",
    ] = True,
    ctx: Context = None,
) -> dict:
    """Create an angular dimension measuring the angle between two lines from a vertex."""
    result = await _backend(ctx).dimension_angular(
        vertex_x, vertex_y, x1, y1, x2, y2, text_x, text_y, layer, minor_angle
    )''',
        ),
    ],
}

PATCHES = [SW_EDGE, ACAD_COM, ACAD_SERVER]


def resolve(base: Path, target):
    kind, rel = target
    if kind == "sw-skill":
        return base / "dsh-skills" / "solidworks-automation" / rel
    if kind == "acad-site":
        return base / "venv-autocad" / "Lib" / "site-packages" / rel
    raise ValueError("unknown target kind: %s" % kind)


def apply_one(base: Path, patch, dry_run=False):
    path = resolve(base, patch["target"])
    if not path.is_file():
        return "missing", "not installed: %s" % path

    text = path.read_text(encoding="utf-8")
    if patch["marker"] in text:
        return "present", "already applied"

    applied = 0
    skipped = []
    for old, new in patch["edits"]:
        if new in text:
            continue
        if old in text:
            text = text.replace(old, new, 1)
            applied += 1
        else:
            skipped.append(old.strip().splitlines()[0][:60])

    if skipped:
        return "drift", "anchor not found: " + " | ".join(skipped)

    if applied == 0:
        return "present", "already applied"

    if not dry_run:
        path.write_text(text, encoding="utf-8", newline="\n")
    return "applied", "%d edit(s) in %s" % (applied, path.name)


def main():
    ap = argparse.ArgumentParser(description="Apply the upstream fixes this bundle depends on.")
    ap.add_argument("--base", default=r"D:\cad-agents", help="install root (default: D:\\cad-agents)")
    ap.add_argument("--dry-run", action="store_true", help="report only, do not write")
    ap.add_argument("--check", action="store_true", help="exit 1 if any patch is not applied yet")
    args = ap.parse_args()

    base = Path(args.base)
    worst = 0
    pending = 0
    for patch in PATCHES:
        state, detail = apply_one(base, patch, dry_run=args.dry_run or args.check)
        mark = {"applied": "PATCHED", "present": "OK     ", "missing": "SKIP   ", "drift": "DRIFT  "}[state]
        print("[%s] %-32s %s" % (mark, patch["id"], detail))
        if state == "drift":
            worst = 1
            print("           upstream text moved; see %s" % patch["pr"])
        elif state == "applied":
            pending = 1

    if args.check:
        return worst or pending

    if worst:
        print()
        print("One or more patches no longer match. Check whether upstream shipped the fix:")
        for patch in PATCHES:
            print("  %s -> %s" % (patch["id"], patch["pr"]))
    return worst


if __name__ == "__main__":
    sys.exit(main())
