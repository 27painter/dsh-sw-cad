# Upstream install steps, verbatim

These are the two upstream projects' own instructions, kept out of the README so it stays readable. This is
also what `install.ps1` automates — read it if you want to understand or debug an install by hand.

If you are not sure what is already on the machine, run `bootstrap.ps1 -Check` instead: it decides whether
`install.ps1` is needed at all.

**AutoCAD MCP server** — [upstream install](https://github.com/U-C4N/Autocad-MCP#install). Into a venv on
the data drive (recommended here):

```powershell
python -m venv D:\cad-agents\venv-autocad
D:\cad-agents\venv-autocad\Scripts\python.exe -m pip install "autocad-mcp-pro[full]"
D:\cad-agents\venv-autocad\Scripts\autocad-mcp.exe --help    # stdio server entry point
```

Upstream's own one-liners, if you do not want a venv:

```bash
pip install autocad-mcp-pro              # or: uvx autocad-mcp-pro
autocad-mcp                              # backend auto-selected
AUTOCAD_MCP_BACKEND=ezdxf autocad-mcp    # headless DXF, no AutoCAD needed
AUTOCAD_MCP_BACKEND=com   autocad-mcp    # live AutoCAD (needs the [com] extra)
```

Extras: `[com]` = live AutoCAD control (pywin32 + Pillow), `[pdf]` = matplotlib for PDF/PNG output,
`[office]` = openpyxl for XLSX takeoffs, `[full]` = all of them. Upstream also documents an HTTP transport
(`autocad-mcp --transport http --port 8000`) and a JSON config for Claude Desktop / Cursor.

**SolidWorks skill + MCP** — [upstream quick start](https://github.com/wzyn20051216/solidworks-automation-skill#quick-start).
Upstream offers three entry points; pick one:

```bash
# Option 1 — skill only (simplest)
npx github:wzyn20051216/solidworks-automation-skill

# Option 2 — MCP via Smithery
npm install -g @smithery/cli
smithery mcp add wzyn20051216/solidworks-automation-skill --client codex --config '{}'

# Option 3 — CAD Studio desktop app, from the upstream Releases page
```

Manual clone (what this kit does, and what you need if you want the skill on the data drive):

```powershell
git clone --depth 1 https://github.com/wzyn20051216/solidworks-automation-skill.git D:\cad-agents\dsh-skills\solidworks-automation
D:\cad-agents\venv-solidworks\Scripts\python.exe -m pip install -r D:\cad-agents\dsh-skills\solidworks-automation\requirements.txt
D:\cad-agents\venv-solidworks\Scripts\python.exe -m pip install -r D:\cad-agents\dsh-skills\solidworks-automation\mcp-server\requirements.txt
D:\cad-agents\venv-solidworks\Scripts\python.exe D:\cad-agents\dsh-skills\solidworks-automation\scripts\cad_doctor.py
```

**Set `SW_MCP_DISCOVERY=search` on the SolidWorks server** (every route above does this for you; this is
the manual step). Upstream defaults to it since 2026-10-29, so an unconfigured client already gets the
three entry tools; setting it explicitly keeps that true if upstream ever changes the default back.
Without it the client carries all 66 tool schemas — 23,411 tokens — in every request.

**Register both servers with your client.** dsh reads `~/.dsh/cordis.patch.yml` — copy this repository's
`cordis.patch.yml` there. It inserts one row — this package — and the plugin registers the servers itself,
so nothing has to be on PATH. Codex reads
`~/.codex/config.toml` — append `codex-config.snippet.toml`, or use upstream's own registrar
(`mcp-server\register_codex_mcp.ps1 -Python <venv python> -InstallDependencies`). The Codex snippet does
contain absolute paths, so adjust those to your `-BaseDir`.

**Make the skills visible.** Both clients only scan their own skills directory:

```powershell
# dsh: one junction for the whole skills folder (matches what install.ps1 does)
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.dsh" | Out-Null
New-Item -ItemType Junction -Path "$env:USERPROFILE\.dsh\skills" -Target "D:\cad-agents\dsh-skills"

# Codex: one junction per skill (its skills folder may hold other skills)
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.codex\skills" | Out-Null
New-Item -ItemType Junction -Path "$env:USERPROFILE\.codex\skills\solidworks-automation" -Target "D:\cad-agents\dsh-skills\solidworks-automation"
New-Item -ItemType Junction -Path "$env:USERPROFILE\.codex\skills\autocad-mcp-pro"       -Target "D:\cad-agents\dsh-skills\autocad-mcp-pro"
```

## What `install.ps1` does

`install.ps1` runs everything in the appendix above, plus the parts that are easy to get wrong: both venvs on the data
drive, the junctions, the discovery-mode parameters, and a timestamped backup of `~/.dsh/cordis.patch.yml`.

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1

# custom locations / force rebuild
.\install.ps1 -BaseDir "E:\cad" -WorkDir "E:\AutoCad" -Force
```

It checks Python, creates the directories, builds the venvs, installs `autocad-mcp-pro[full]` and the
SolidWorks requirements, fetches the skill at the pinned commit (zip, so git is not needed), applies
`patches/` to the installed copies, junctions the skills into `~/.dsh/skills`, writes the dsh config,
and drops the AutoCAD skill in place.

If you are not sure what is already on the machine, run `bootstrap.ps1` instead: it decides whether
`install.ps1` is needed at all.

**Codex users still need one step** — append `codex-config.snippet.toml` to `~/.codex/config.toml` and
junction the two skills into `~/.codex/skills/` (last four commands of the appendix above).

> [!IMPORTANT]
> **Start AutoCAD / SolidWorks first, as the same user.** COM automation crosses a process boundary that
> Windows guards by user and integrity level. An agent started elevated while the CAD app runs normally
> (or vice versa) will connect to nothing, and the failure looks like "the tool did nothing".
>
> This rule is specific to COM. [`dsh-nx`](https://github.com/27painter/dsh-nx) drives NX through
> NXOpen, which runs inside the NX process — an open NX window cannot be attached to there, so that
> plugin starts its own batch session and must **not** be pre-opened. Do not carry this habit across.

