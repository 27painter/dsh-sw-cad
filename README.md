# dsh-sw-cad

[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**Two MCP servers and two skills that let a DeepSeek Harness or Codex agent drive real CAD software.**
They wire up two existing open-source projects — [U-C4N/Autocad-MCP](https://github.com/U-C4N/Autocad-MCP)
and [wzyn20051216/solidworks-automation-skill](https://github.com/wzyn20051216/solidworks-automation-skill) —
so an agent can draw in AutoCAD, model in SolidWorks, and export the results. This repository is the
configuration layer: isolated environments, launch parameters, on-demand loading and client registration.
It does not fork, patch or repackage either server.

English | [中文](README.zh.md)

## What it does, and when you want it

A CLI agent that can read a drawing is useful. One that can *produce* one is a different tool. With this
kit installed, dsh or Codex can take a sentence like "draw an M6 threaded-hole bracket on an ISO A3 sheet
and export PDF" and actually do it — create layers, draw geometry, dimension it, stamp a title block,
run a validator, and hand back a file.

The catch is the same as every COM automation: **the CAD application must be running, and the agent must
run as the same Windows user and integrity level as the CAD app.** This is not a cloud service and there
is no headless substitute for native `.dwg` / `.sldprt` output.

Two skills are the entry points. They load lazily — the SKILL.md is read only when a request matches its
description, so an idle session pays nothing for them:

| Skill | How it works |
|---|---|
| `solidworks-automation` | Script-driven: 66 Python modules (30 `sw_*.py` COM wrappers plus OCCT/FEA/DFM tooling), with preflight, capability gating and evidence chains |
| `autocad-mcp-pro` | Authored here (upstream ships no AutoCAD skill). Drives the AutoCAD MCP through its two meta-tools |

Two MCP servers are the muscle, registered in the client config:

| Server | Tools | Mode |
|---|---|---|
| `autocad` | 242 | `DISCOVERY_MODE=search`: only `search_tools` + `call_tool` advertised (~356 tokens idle); the full catalog is searched by keyword |
| `solidworks` | 66 | Full catalog advertised (50 `solidworks_*` + 14 `cadstudio_*` + 2 `design_*`) |

```
you speak natural language
   │
   ├─ dsh / Codex lazily loads a skill by its SKILL.md description
   │      ├─ solidworks-automation  → sw_*.py scripts → COM → SolidWorks
   │      └─ autocad-mcp-pro        → search_tools / call_tool → MCP → AutoCAD
   │
   └─ MCP servers (cordis.patch.yml / codex config.toml)
          ├─ autocad    discovery mode, 242 tools on demand
          └─ solidworks 66-tool stdio server
```

## What is upstream, what is ours

This matters more than usual here, because the two servers are other people's work. The split is:

| Part | Origin | License |
|---|---|---|
| AutoCAD MCP server (`autocad-mcp-pro`) | [U-C4N/Autocad-MCP](https://github.com/U-C4N/Autocad-MCP), installed from PyPI | MIT |
| SolidWorks skill + MCP server | [wzyn20051216/solidworks-automation-skill](https://github.com/wzyn20051216/solidworks-automation-skill), cloned at install time | MIT |
| `skills/autocad-mcp-pro/SKILL.md` | Authored here — upstream has no AutoCAD skill | MIT |
| `install.ps1`, `cordis.patch.yml`, `codex-config.snippet.toml`, READMEs | Authored here | MIT |

**Nothing upstream is vendored.** The installer fetches the PyPI package and clones (or zip-downloads) the
SolidWorks repository at install time, so you always get upstream's current code and upstream keeps
ownership of it. The value this repository adds is the part that was missing: verified launch parameters,
the discovery-mode configuration, the on-demand skill wiring, the junction trick that keeps gigabytes off
C:, and an installer that survives a blocked git protocol.

## Install

**Route 1 — one script.** Creates two venvs under `D:\cad-agents` (change with `-BaseDir`), installs both
servers, clones the SolidWorks skill, junctions it into `~/.dsh/skills`, and writes
`~/.dsh/cordis.patch.yml` with a timestamped backup of whatever was there before:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

**Route 2 — by hand.** Copy `cordis.patch.yml` into `~/.dsh/cordis.patch.yml` (adjust paths), then run the
installer once to create the environments, or set up the venvs yourself and point the patch at them.

**Codex users need one extra step** — append `codex-config.snippet.toml` to `~/.codex/config.toml`, then
junction the skills in:

```powershell
New-Item -ItemType Junction -Path "$env:USERPROFILE\.codex\skills\solidworks-automation" -Target "D:\cad-agents\dsh-skills\solidworks-automation"
New-Item -ItemType Junction -Path "$env:USERPROFILE\.codex\skills\autocad-mcp-pro"       -Target "D:\cad-agents\dsh-skills\autocad-mcp-pro"
```

> [!IMPORTANT]
> **Start AutoCAD / SolidWorks first, as the same user.** COM automation crosses a process boundary that
> Windows guards by user and integrity level. An agent started elevated while the CAD app runs normally
> (or vice versa) will connect to nothing, and the failure looks like "the tool did nothing".

## Usage

Just describe the drawing. The skill loads itself.

```text
Open AutoCAD, draw a bearing housing: base 120×80×10, upright 60 high × 12 thick,
φ40 through hole on top. ISO A3 sheet with title block and dimensions, save to
D:\AutoCad and export PDF.
```

```text
In SolidWorks, build a flange: OD 100, 20 thick, 30 bore, four M10 bolt holes on a
bolt circle. Save as SLDPRT and export STEP.
```

Practical notes, learned the hard way:

- **Say one CAD at a time.** Mentioning both loads both skills and mixes the task.
- **Ask for a plan first on anything complex.** Both skills have a planning step; approving a plan before
  geometry is drawn is cheaper than undoing a bad drawing.
- **Ask for the preview.** The AutoCAD path ends in an 8-step validator plus a screenshot; the SolidWorks
  path exports four orthographic previews. "Show me the preview" is a fair request and the skills honor it.

## Verification

```powershell
# dsh sees both servers and both skills
pnpm dsh --profile headless --dump-config
pnpm dsh --profile headless "list available skills"

# SolidWorks skill self-check (Python deps + COM)
& "D:\cad-agents\venv-solidworks\Scripts\python.exe" "D:\cad-agents\dsh-skills\solidworks-automation\scripts\sw_preflight.py"

# AutoCAD MCP starts and reports its version
& "D:\cad-agents\venv-autocad\Scripts\autocad-mcp.exe" --help

# Codex
codex mcp list
```

What was verified before publishing: both MCP servers answer a JSON-RPC handshake (`autocad-mcp-pro`
v3.4.11, `solidworks_mcp` v1.30.0); discovery mode advertises exactly two meta-tools and finds
`gear_draw_spur_front_view` / `keyway_draw_keyed_bore` by keyword; `dsh --dump-config` loads both entries;
the SolidWorks preflight passes.

## Design notes

**1. Discovery mode is the whole trick.** 242 tool schemas cost ~84k tokens if advertised. In discovery
mode the client sees two tools (~356 tokens) and searches a catalog that carries 206 AutoCAD command names
plus 1439 synonyms — say "chamfer" and it finds `FILLET`.

**2. Keep `TOOL_PROFILE=full` and `TOOL_PACKS=all`.** They only narrow the *searchable* catalog and do not
change idle cost. Measured: `lean + core,mech` drops the catalog to 58 tools and makes the parametric gear
and keyway tools unfindable. Narrowing packs loses capability without saving a token.

**3. Junctions, not copies.** dsh only scans `~/.dsh/skills`, Codex only scans `~/.codex/skills`. Directory
junctions point both at the data drive; neither tool notices. Deleting a junction does not delete data.

**4. Scope the file access.** The AutoCAD server's `ALLOWED_PATHS` is empty by default, and its own README
says to set it. This kit sets it to a working directory.

**5. Know what is verified.** AutoCAD COM was verified on 2026; 2027's ActiveX API is compatible but run
the upstream smoke scripts first. SolidWorks was verified upstream on 2026 SP4.1.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| dsh lists no skills | `~/.dsh/skills` must be a junction whose target exists; folder name must equal the frontmatter `name` |
| Codex lists no skills | both junctions under `~/.codex/skills\`; Codex must support skills |
| AutoCAD tools return nothing | CAD not running, or different user/integrity level; set `AUTOCAD_MCP_BACKEND=com` to rule out the ezdxf fallback |
| SolidWorks preflight fails | missing deps (`requirements.txt` of the skill) or SW not running |
| `codex mcp list` empty | the snippet must sit at the end of `config.toml`; paths need doubled backslashes |
| `git clone` fails during install | the installer falls back to a codeload zip automatically |

## License

MIT (see [LICENSE](LICENSE)). Upstream components remain under their own MIT licenses; this repository
redistributes neither.
