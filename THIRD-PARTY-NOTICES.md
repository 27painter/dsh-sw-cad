# Third-party notices

`dsh-sw-cad` is an integration wrapper. It does not contain the AutoCAD MCP server
or the SolidWorks skill — those are installed from their own sources and remain
the property of their authors. This file records what is used, under what terms,
and what this package actually adds.

## Components

### autocad-mcp-pro

- Source: <https://github.com/U-C4N/Autocad-MCP>
- Version pinned: 1.6.0
- Licence: MIT
- Copyright (c) 2026 Umutcan Edizsalan

Installed as a Python package (`pip install autocad-mcp-pro==1.6.0`). Provides the
AutoCAD MCP server: 242 tools over the COM API, plus an ezdxf backend.

### solidworks-automation skill

- Source: <https://github.com/wzyn20051216/solidworks-automation-skill>
- Commit pinned: `2750ac0892fa`
- Licence: MIT
- Copyright (c) 2026 SolidWorks Automation Skill Contributors

Installed into the agent's skill directory. Provides the SolidWorks scripting
layer (`sw_session`, `sw_part`, `sw_assembly`, `sw_drawing`, …).

### nxopen-journal skill (dsh-nx only)

- Source: <https://github.com/EVOL233awa/nxopen-journal>
- Version pinned: v1.3.0 (`4b77d88`)
- Licence: MIT
- Copyright (c) 2026 nxopen-journal contributors

### NX_MCP (dsh-nx only, vendored under `nxsrc/`)

- Source: <https://github.com/DreamEnding/NX_MCP>
- Commit pinned: `4a7c8811`
- Licence: MIT
- Copyright (c) 2026 NX MCP contributors

Vendored and patched for CPython 3.6.1. `nxsrc/PORT-NOTES.md` lists every change.

## What this package adds

`dsh-sw-cad` itself is the wrapper: `install.ps1`, `bootstrap.ps1`, `lib/probe.py`,
the `dsh-sw-cad` CLI, the discovery-mode configuration, and the documentation.
That work is what this repository owns.

The upstream code is not modified except where `nxsrc/` says so, and no upstream
author endorses or supports this package.

## MIT obligations

MIT requires that the copyright notice and permission notice above accompany any
copy or substantial portion of the software. Keeping this file in the package
satisfies that for the components listed here. It does not grant any right to
the names or trademarks of the upstream projects, and it does not transfer their
copyright.

## If you redistribute this commercially

You may. MIT permits commercial use and resale. Two things are required:

1. Keep this file, and keep the upstream `LICENSE` files that the installers place
   in their own directories.
2. Do not describe the upstream components as your own work, and do not imply the
   upstream authors endorse your product.

What you can add on top — support, deployment, a validated configuration for a
specific CAD version — is yours to license however you like. What you cannot do is
grant a sublicence on the upstream code, because you do not hold its copyright.
