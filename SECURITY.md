# Security Policy

## What this project touches

`dsh-sw-cad` is unusual for a plugin: it starts other processes and drives two
desktop applications over COM. The parts that matter, stated plainly:

- **It runs an installer.** On first use, `bootstrap.ps1` runs `pip install` for
  the pinned AutoCAD MCP server and clones the SolidWorks skill at a pinned
  commit. It writes to `D:\cad-agents` (or `SW_CAD_BASE`) and reads
  `~/.dsh/sw-cad.json`.
- **It launches two stdio MCP servers**, which in turn attach to a running
  AutoCAD or SolidWorks through COM. Anything those applications can do to a
  file on disk, a tool call can do.
- **`deliver` reads files** (DXF, DWG headers, PDF, STEP, SLDPRT) and writes a
  `delivery.json` next to them. It never launches a CAD application.
- **It does not open a network port, phone home, or send telemetry.** The only
  outbound traffic is what `pip`, `git`, and npm do during install.

## Reporting a vulnerability

Open a private security advisory on this repository
(<https://github.com/27painter/dsh-sw-cad/security/advisories/new>), or email the
maintainer listed in `package.json`.

Please include: what you ran, what happened, and the version
(`dsh-sw-cad version`). A proof of concept helps; a malicious CAD file that
makes `deliver` crash or hang is a valid report.

**Do not open a public issue for a vulnerability.**

Expect an initial response within seven days. This is a single-maintainer
project — there is no on-call rotation, and no bug bounty.

## Supported versions

The latest published minor line receives fixes. Anything older may be asked to
upgrade first, because the upstream versions it pins also move.

## Specific things worth reporting

- A crafted DXF/PDF/STEP file that makes `deliver` write outside the target
  directory, or executes something.
- A way to make `serve` spawn an unexpected executable, e.g. by influencing path
  resolution through `SW_CAD_BASE` or `~/.dsh/sw-cad.json`.
- Prompt-injection content inside a drawing (text entities, layer names, block
  names) that steers an agent into running a destructive tool call. This is a
  real class of bug here: `deliver` reads text out of files, and the model reads
  what `deliver` reports.
- Anything that causes a CAD tool call to delete or overwrite files without the
  user asking.

## What is out of scope

- **The upstream components.** Bugs in `autocad-mcp-pro` (U-C4N/Autocad-MCP) and
  the SolidWorks skill belong upstream. This repo ships three patches for known
  upstream defects; report those to their projects as well.
- **AutoCAD and SolidWorks themselves.** A crash, a licensing dialog, or data
  loss inside the application is not something this project can fix.
- **Anything requiring a COM server the user did not install.** If AutoCAD is
  not running, the tools cannot reach it — an error saying so is expected
  behaviour, not a vulnerability.
- **`deliver` passing a drawing that is wrong in engineering terms.** It checks
  that a file holds up to reading: entities, units, sheets, a title block, a
  real text layer. It does not check tolerances, fits, material choice, or
  whether a dimension chain closes. A `pass` verdict is not a manufacturable
  drawing, and it does not claim to be.