# Patches

Two upstream defects are fixed here until the pull requests land. Both are
**silent**: nothing raises, nothing appears in the return value, and the damage
only shows in the drawing or in a file read back later.

`apply_patches.py` runs after the upstream pieces are installed. It is safe to
run repeatedly: each patch carries a marker, and a patch whose anchor text has
moved is reported as **drift** rather than applied to the wrong code.

```powershell
# what is applied right now
python patches\apply_patches.py --check

# apply
python patches\apply_patches.py
```

`install.ps1` runs it as step 6b, and `bootstrap.ps1` reports it as
`upstream-patches`, repairing it like any other missing item.

## sw-edge-length-diameter

- **Target:** `dsh-skills/solidworks-automation/scripts/sw_edge_select.py`
- **Upstream:** <https://github.com/wzyn20051216/solidworks-automation-skill/pull/67>
- **Symptom:** `describe_edge` returned `length_mm: null` and `diameter_mm: null`
  for every edge, so `select_edges({"diameter_mm": 60})` rejected everything with
  "diameter unknown" and `min_length_mm` rejected everything.

Two API mistakes:

| field | was | is |
|---|---|---|
| `length_mm` | `edge.GetLength` — `IEdge` has no such member | `ICurve.GetLength3(startParam, endParam)` |
| `diameter_mm` | `curve_params[6]` treated as a radius | `ICurve.CircleParams[6] * 2` |

`GetCurveParams2` returns `[start.xyz, end.xyz, startParam, endParam, ...]` — 11
values on 2025 SP5 — so index 6 is a parameter, not a radius.

Getting there needed one workaround: `edge.GetCurve` raises
`DISP_E_MEMBERNOTFOUND` through pywin32's attribute path even though the member
exists (`dispid 1`), while `GetCurveParams2` and `GetTwoAdjacentFaces2` on the
same edge work. The patch invokes the dispid directly and wraps the result with
`dynamic.Dispatch`; the normal path is still tried first.

Verified on a 45-edge part: 45 errors → 0, 27 diameters, 45 lengths;
`diameter_mm=60` selects exactly 2 edges with length 188.495559 mm (π×60).

## acad-dimension-rotation

- **Target:** `venv-autocad/Lib/site-packages/backends/com_backend.py`
- **Upstream:** <https://github.com/U-C4N/Autocad-MCP/pull/16>
- **Symptom:** every vertical linear dimension measured **0.0**, with the entity
  created and no error reported.

`AddDimRotated` measures the projection of the two points onto the dimension
line. `rotation` defaulted to `0.0`, so a vertical pair (same X) projected to
nothing. Now `None` means "follow the points" — a mostly vertical pair gets 90°.
An explicit rotation is still honoured.

The patch also adds `minor_angle` to `dimension_angular`. `AddDimAngular` sweeps
counter-clockwise from ray 1 to ray 2 and that sweep is what lands in the DXF,
while the entity's COM `Measurement` always reports the minor angle — the same
geometry reads 30° on screen and 330° in the file. The rays are ordered before
the entity is created, so both views agree.

Verified on AutoCAD 2026: vertical 70 → 70.0000, horizontal 100 → 100.0000,
explicit `rotation=90` on horizontal points → 0.0000; a 200° sweep exports as
160° with `minor_angle=True` and 200° with `False`.

## acad-dimension-tool-defaults

- **Target:** `venv-autocad/Lib/site-packages/server.py`
- **Upstream:** same PR as above
- **Why separate:** the tool layer computes the nominal value used for fit
  tolerances from `rotation`. If only the backend learns about `None`, the
  tolerance text is computed at 0° while the dimension is drawn at 90°.

Changes the tool signature to `rotation: float | None = None`, resolves it the
same way the backend does before computing the nominal, and exposes
`minor_angle`.

## When upstream ships these

Delete the corresponding entry from `PATCHES` in `apply_patches.py` and drop the
section here. `--check` reports `drift` once the anchor text changes, which is the
signal to look — it does not silently stop applying.
