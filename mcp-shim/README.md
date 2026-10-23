# SolidWorks discovery shim

Upstream `wzyn20051216/solidworks-automation-skill` registers all 66 of its MCP
tools at startup. Every client therefore carries the whole catalogue in its
context window before the user has typed anything.

Measured on the wire (`tools/list` response, `tiktoken` `cl100k_base`):

| Surface | Tools seen | Idle cost |
|---|---:|---:|
| upstream `server.py` directly | 66 | **23,404 tokens** |
| this shim | 2 | **256 tokens** |

98.9% of that cost removed, with no change to the upstream repository.

## How it works

```
MCP client ──stdio──▶ sw_discovery_shim.py ──stdio──▶ upstream server.py
                       │                              (spawned lazily)
                       └── sw_catalog.json (search index, built at install)
```

The shim advertises two tools:

| Tool | Purpose |
|---|---|
| `search_tools(query, top_k)` | Ranked matches by intent, Chinese or English. Returns exact names, one-line purpose, required arguments. |
| `call_tool(name, arguments)` | Forwards to the upstream server verbatim. |

`resources/list`, `resources/read` and `prompts/list` are proxied through, so
artifact delivery keeps working.

The upstream process is only spawned on the first `call_tool`; searching is
served from a catalogue cache, so a session that never draws anything never pays
for the child process at all.

## Why search instead of a static filter

The tool names are `solidworks_sketch_and_extrude`, but a drafter says
"拉伸凸台". `sw_lexicon.json` holds 57 bilingual alias groups (Chinese command
names, English API words, format names) that expand a query before scoring.
`干涉检查` and `检查干涉` both land on `solidworks_interference_check`; `工程图`
lands on the three drawing tools, not on every tool that mentions "view".

Scoring is summed **per alias group**, not per alias, so a tool whose
description happens to list many formats (`step, stl, iges, parasolid, pdf`) does
not accumulate points from synonyms of a single concept. Latin terms are matched
on word boundaries, so `view` does not match inside `preview`.

## Usage

Normally installed and wired up by `install.ps1`; run it by hand like this:

```powershell
$py = "D:\cad-agents\venv-solidworks\Scripts\python.exe"
$srv = "D:\cad-agents\dsh-skills\solidworks-automation\mcp-server\server.py"

# 1. build the catalogue cache (once, and after every upstream update)
& $py sw_discovery_shim.py --python $py --server $srv --build-catalog

# 2. serve over stdio
& $py sw_discovery_shim.py --python $py --server $srv
```

### Reproduce the numbers

`verify_shim.py` spawns the shim, measures the real `tools/list` and `initialize`
frames it puts on the wire, compares them against the upstream catalogue, and
prints the top hit for 36 Chinese and English queries:

```powershell
python verify_shim.py --python $py --server $srv
```

It uses `tiktoken` (`cl100k_base`) when available and falls back to character
counts otherwise.

### Options

| Flag | Env | Default |
|---|---|---|
| `--python` | `SW_UPSTREAM_PYTHON` | required |
| `--server` | `SW_UPSTREAM_SERVER` | required |
| `--catalog` | `SW_SHIM_CATALOG` | `sw_catalog.json` next to the script |
| `--lexicon` | `SW_SHIM_LEXICON` | `sw_lexicon.json` next to the script |
| `--top-k` | `SW_SHIM_TOP_K` | `8` |
| `--timeout` | `SW_SHIM_TIMEOUT` | `900` seconds per upstream call |
| `--build-catalog` | — | generate the cache and exit |

`SW_SHIM_DEBUG=1` mirrors upstream stderr to the shim's stderr.

## Caveats

- The catalogue is a cache. After updating the upstream skill, rebuild it
  (`--build-catalog`), otherwise `search_tools` may offer a tool that no longer
  exists. `call_tool` always goes to the live server, so a stale entry fails
  loudly instead of silently.
- Discovery trades one extra round trip for the context saving: `search_tools`
  then `call_tool`. For a one-shot "draw a box" that is slower than calling the
  upstream tool directly. It pays off when a session makes several calls, and it
  is what keeps the idle cost at 256 tokens.
- `call_tool` does not validate arguments; the upstream server does. Invalid
  arguments come back as the upstream error, unchanged.
