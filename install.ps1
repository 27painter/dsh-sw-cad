#requires -Version 5.1
<#
.SYNOPSIS
  Install CAD agent MCP servers (AutoCAD + SolidWorks) for dsh / Codex.
  ALL runtime artifacts go to -BaseDir (default D:\cad-agents, keeps C: free).
.DESCRIPTION
  - Creates two venvs under -BaseDir (base interpreter may stay on C:)
  - Installs autocad-mcp-pro[full] and the SolidWorks MCP dependencies
  - Clones the SolidWorks skill into <BaseDir>\dsh-skills
  - Creates a directory junction  ~/.dsh/skills -> <BaseDir>\dsh-skills
    so dsh auto-discovers the skill from its default scan path while the
    files physically live on the data drive
    (2 meta-tools instead of 66: ~256 tokens idle instead of ~23.4k)
  - Copies the two dsh patch files into <BaseDir>
  - Backs up and rewrites ~/.dsh/cordis.patch.yml (adds two insert entries)
  Does NOT touch CAD software, the registry, or ~/.codex/config.toml.
  NOTE: This script is intentionally ASCII-only (PowerShell 5.1 + no-BOM UTF8).
.PARAMETER Python
  Python interpreter used as the venv base. Must be >= 3.11.
  Default: "python" resolved from PATH.
.PARAMETER BaseDir
  Root directory for all runtime artifacts. Default: D:\cad-agents.
.PARAMETER WorkDir
  AutoCAD working directory, also used as the MCP ALLOWED_PATHS whitelist.
  Default: D:\AutoCad.
.PARAMETER Force
  Recreate venvs / re-clone the skill if they already exist.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install.ps1
.EXAMPLE
  .\install.ps1 -Python "D:\somewhere\python.exe" -BaseDir "E:\cad" -Force
#>
param(
    [string]$Python = "python",
    [string]$BaseDir = "D:\cad-agents",
    [string]$WorkDir = "D:\AutoCad",
    [switch]$Force,
    # Skip rewriting ~/.dsh/cordis.patch.yml. Used when the servers are
    # registered by the npm bundle patch (dsh plugin add) instead.
    [switch]$SkipDshPatch,
    # Pinned upstream versions. Defaults match lib/upstream.json, which is the
    # pair this bundle was validated against; changing them is deliberate.
    [string]$AutocadVersion = "1.6.0",
    [string]$SkillRef = "2750ac0892fa2854a4495a296ba238a2f2baa83d"
)

$ErrorActionPreference = "Stop"

# ---- everything heavy lives on the data drive ----
$VenvAcad  = Join-Path $BaseDir "venv-autocad"
$VenvSw    = Join-Path $BaseDir "venv-solidworks"
$SkillsDir = Join-Path $BaseDir "dsh-skills"                    # physical location
$SkillDest = Join-Path $SkillsDir "solidworks-automation"       # dir name == frontmatter name
$SkillRepo = "https://github.com/wzyn20051216/solidworks-automation-skill.git"
$SkillZip  = "https://codeload.github.com/wzyn20051216/solidworks-automation-skill/zip/$SkillRef"
$Junction  = Join-Path $env:USERPROFILE ".dsh\skills"          # logical path dsh scans
$DshHome   = Join-Path $env:USERPROFILE ".dsh"
$PatchFile = Join-Path $DshHome "cordis.patch.yml"
$SwServer  = Join-Path $SkillDest "mcp-server\server.py"

function Say($msg) { Write-Host "==> $msg" }

# ---------- 1. Python check ----------
Say "[1/7] Checking Python (need >= 3.11 for autocad-mcp-pro)..."
if (-not (Test-Path -LiteralPath $Python)) {
    $cmd = Get-Command $Python -ErrorAction SilentlyContinue
    if ($cmd) { $Python = $cmd.Source } else { throw "Python not found: $Python" }
}
$ver = & $Python -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
if ($LASTEXITCODE -ne 0) { throw "Cannot run Python via '$Python'." }
$p = $ver.Split(".")
if ([int]$p[0] -lt 3 -or ([int]$p[0] -eq 3 -and [int]$p[1] -lt 11)) {
    throw "Python $ver too old; autocad-mcp-pro requires >= 3.11."
}
Write-Host "    Python $ver OK  (base: $Python)"

# ---------- 2. data dirs ----------
Say "[2/7] Creating $BaseDir and $WorkDir ..."
New-Item -ItemType Directory -Force -Path $BaseDir | Out-Null
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null   # MCP ALLOWED_PATHS target
$env:PIP_CACHE_DIR = Join-Path $BaseDir "pip_cache"   # keep pip cache off C: too

# ---------- 3. venvs on D: ----------
Say "[3/7] Creating venvs on D: ..."
foreach ($vp in @($VenvAcad, $VenvSw)) {
    if ((Test-Path -LiteralPath $vp) -and $Force) { Remove-Item -Recurse -Force -LiteralPath $vp }
    if (-not (Test-Path -LiteralPath $vp)) {
        & $Python -m venv $vp
        if ($LASTEXITCODE -ne 0) { throw "venv creation failed: $vp" }
    }
    & (Join-Path $vp "Scripts\python.exe") -m pip install --upgrade pip --quiet
    if ($LASTEXITCODE -ne 0) { throw "pip self-upgrade failed in $vp" }
}

# ---------- 4. junction ~/.dsh/skills -> <BaseDir>\dsh-skills ----------
Say "[4/7] Linking ~/.dsh/skills -> $SkillsDir (junction)..."
New-Item -ItemType Directory -Force -Path $SkillsDir | Out-Null
if (Test-Path -LiteralPath $Junction) {
    Write-Host "    ~/.dsh/skills already exists, skipping (fine if it is already a junction)"
}
else {
    New-Item -ItemType Junction -Path $Junction -Target $SkillsDir | Out-Null
    if (-not (Test-Path -LiteralPath $Junction)) { throw "junction creation failed (run as your normal user; no admin needed)" }
    Write-Host "    junction created"
}

# ---------- 5. SolidWorks skill (pinned) ----------
Say "[5/7] Fetching SolidWorks skill at $SkillRef ..."
if ((Test-Path -LiteralPath $SkillDest) -and $Force) { Remove-Item -Recurse -Force -LiteralPath $SkillDest }
if (-not (Test-Path -LiteralPath $SkillDest)) {
    # Always the archive of the pinned ref: no git needed, and the result is the
    # same commit on every machine. A branch name works too (GitHub redirects).
    $tmp = Join-Path $env:TEMP "sw-skill.zip"
    $expand = Join-Path $env:TEMP "sw-skill-extract"
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        & curl.exe -sL -o $tmp $SkillZip
        if ($LASTEXITCODE -ne 0) { throw "skill archive download failed: $SkillZip" }
    }
    else {
        Invoke-WebRequest -Uri $SkillZip -OutFile $tmp -UseBasicParsing
    }
    if (Test-Path $expand) { Remove-Item -Recurse -Force $expand }
    Expand-Archive -Path $tmp -Destination $expand -Force
    $inner = Get-ChildItem -LiteralPath $expand -Directory | Select-Object -First 1
    if (-not $inner) { throw "unexpected zip layout in $expand" }
    Move-Item -LiteralPath $inner.FullName -Destination $SkillDest
    Remove-Item -Recurse -Force $expand
    Remove-Item -Force $tmp
    Write-Host "    extracted to $SkillDest"
}
else {
    Write-Host "    skill already present, skipping (use -Force to re-fetch)"
}

# ---------- 6. Python packages ----------
Say "[6/7] Installing Python packages (pip cache -> D:\pip_cache)..."
$pyAcad = Join-Path $VenvAcad "Scripts\python.exe"
$pySw   = Join-Path $VenvSw   "Scripts\python.exe"

& $pyAcad -m pip install "autocad-mcp-pro[full]==$AutocadVersion" --quiet
if ($LASTEXITCODE -ne 0) { throw "pip install autocad-mcp-pro[full]==$AutocadVersion failed" }

$swReq = Join-Path $SkillDest "mcp-server\requirements.txt"
if (-not (Test-Path -LiteralPath $swReq)) { throw "requirements not found: $swReq" }
& $pySw -m pip install -r $swReq --quiet
if ($LASTEXITCODE -ne 0) { throw "pip install -r mcp-server/requirements.txt failed" }

# root requirements.txt carries the skill's own deps (PyMuPDF/ezdxf/matplotlib)
$swRootReq = Join-Path $SkillDest "requirements.txt"
if (Test-Path -LiteralPath $swRootReq) {
    & $pySw -m pip install -r $swRootReq --quiet
    if ($LASTEXITCODE -ne 0) { throw "pip install -r root requirements.txt failed" }
}

# ---------- 7. dsh patch config + AutoCAD skill ----------
Say "[7/7] Writing dsh MCP config (cordis.patch.yml) and skills..."
$kitDir = $PSScriptRoot
$patchTemplate = Join-Path $kitDir "cordis.patch.yml"
if (Test-Path -LiteralPath $patchTemplate) {
    Copy-Item -LiteralPath $patchTemplate -Destination (Join-Path $BaseDir "cordis.patch.yml") -Force
}

# AutoCAD skill is authored in this kit (upstream has none); install if missing
$acadSkillDir = Join-Path $SkillsDir "autocad-mcp-pro"
$acadSkillSrc = Join-Path $kitDir "skills\autocad-mcp-pro\SKILL.md"
if (-not (Test-Path -LiteralPath (Join-Path $acadSkillDir "SKILL.md"))) {
    New-Item -ItemType Directory -Force -Path $acadSkillDir | Out-Null
    if (Test-Path -LiteralPath $acadSkillSrc) {
        Copy-Item -LiteralPath $acadSkillSrc -Destination (Join-Path $acadSkillDir "SKILL.md") -Force
        Write-Host "    autocad-mcp-pro skill installed"
    }
}

# backup existing patch (even if it is just [])
if ($SkipDshPatch) {
    Write-Host "    skipping ~/.dsh/cordis.patch.yml (the bundle patch registers the servers)"
}
elseif (Test-Path -LiteralPath $PatchFile) {
    $bak = "$PatchFile.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Copy-Item -LiteralPath $PatchFile -Destination $bak -Force
    Write-Host "    backup: $bak"
}

$patchContent = @"
# Managed by dsh-sw-cad/install.ps1 -- on-demand skill architecture
# Entry points : 2 skills, lazily loaded via SKILL.md
#   - solidworks-automation  ($SkillDest)
#   - autocad-mcp-pro         ($SkillsDir\autocad-mcp-pro)
# Muscles (kept): both MCP servers registered below.
#   - autocad   : DISCOVERY mode = 2 meta-tools only (~256 tokens idle),
#                 full 242-tool catalog searched on demand.
#   - solidworks: upstream SW_MCP_DISCOVERY=search = 3 entry tools (~474 tokens
#                 idle) instead of 66 named tools (~23.4k).
- insert:
    - id: mcp-autocad
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: autocad
        transport: stdio
        command: '$VenvAcad\Scripts\autocad-mcp.exe'
        args: []
        env:
          AUTOCAD_MCP_BACKEND: auto
          TOOL_PROFILE: full
          TOOL_PACKS: 'all'
          DISCOVERY_MODE: search
          ALLOWED_PATHS: '$WorkDir'
        toolCallTimeoutMs: 180000
        failOnStartupError: false
        reconnect:
          enabled: true
          initialDelayMs: 500
          maxDelayMs: 30000
          maxAttempts: 10
- insert:
    - id: mcp-solidworks
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: solidworks
        transport: stdio
        command: '$VenvSw\Scripts\python.exe'
        args:
          - '$SwServer'
        env:
          SW_MCP_DISCOVERY: search
        toolCallTimeoutMs: 300000
        failOnStartupError: false
        reconnect:
          enabled: true
          initialDelayMs: 500
          maxDelayMs: 30000
          maxAttempts: 10
"@
# write UTF-8 without BOM (YAML parsers are happiest)
if (-not $SkipDshPatch) {
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($PatchFile, $patchContent, $utf8NoBom)
}

# ---------- verify ----------
$acadExe = Join-Path $VenvAcad "Scripts\autocad-mcp.exe"
$swServer = $SwServer
$skillVisible = Join-Path $Junction "solidworks-automation\SKILL.md"
$acadSkillVisible = Join-Path $Junction "autocad-mcp-pro\SKILL.md"
$mustExist = @($acadExe, $swServer, $skillVisible, $acadSkillVisible)
if (-not $SkipDshPatch) { $mustExist += $PatchFile }
foreach ($f in $mustExist) {
    if (-not (Test-Path -LiteralPath $f)) { throw "verify failed, missing: $f" }
}

Write-Host ""
Write-Host "=================================================="
Write-Host " Install complete. Everything heavy is on $BaseDir."
Write-Host "   Skills (on-demand entry):"
Write-Host "     - solidworks-automation  (script-based, any COM-capable SW)"
Write-Host "     - autocad-mcp-pro        (MCP discovery mode, any COM-capable AutoCAD)"
Write-Host "   AutoCAD MCP : $acadExe   (2 meta-tools, ~256 tokens idle)"
Write-Host "   SW MCP      : $pySw $swServer   (3 discovery entries, ~474 tokens idle)"
Write-Host "                 SW_MCP_DISCOVERY=search; full mode is 66 tools, ~23.4k tokens"
Write-Host "   dsh config  : $PatchFile  (template copied to $BaseDir\cordis.patch.yml)"
Write-Host ""
Write-Host " Verify:"
Write-Host "   pnpm dsh --profile headless --dump-config"
Write-Host "   pnpm dsh --profile headless `"list available skills`""
Write-Host "   SW self-check: & '$pySw' '$SkillDest\scripts\sw_preflight.py'"
Write-Host ""
Write-Host " Discovery is upstream's now; nothing to rebuild after a skill update."
Write-Host ""
Write-Host " Reminder: start AutoCAD / SolidWorks first (detected by COM, not by version),"
Write-Host " and run dsh/Codex as the SAME Windows user and integrity level as the CAD apps."
Write-Host "=================================================="
