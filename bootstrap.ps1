#requires -Version 5.1
<#
.SYNOPSIS
  Check this machine for everything dsh-sw-cad needs, and install whatever is missing.

.DESCRIPTION
  Idempotent: it inspects first and only touches the pieces that are absent or stale,
  so running it twice is a no-op. Safe to hand to an agent.

  Detection covers:
    - Python >= 3.11
    - venv-autocad + the autocad-mcp-pro package (AutoCAD upstream)
    - venv-solidworks + the SolidWorks upstream's dependencies
    - the SolidWorks skill clone
    - the discovery shim and its catalogue (rebuilt when older than server.py)
    - the ~/.dsh/skills junction and the two ~/.codex/skills junctions
    - the mcp entries in ~/.dsh/cordis.patch.yml and ~/.codex/config.toml

  Missing core pieces are handed to install.ps1, the full installer. Small ones
  (catalogue, junctions, config entries) are fixed in place.

  Writes nothing on C: except ~/.dsh and ~/.codex. Never touches AutoCAD,
  SolidWorks or the registry.

.PARAMETER Python
  Base interpreter used to create the venvs. Default: "python" from PATH.
.PARAMETER BaseDir
  Root for all runtime artifacts. Default: D:\cad-agents.
.PARAMETER WorkDir
  AutoCAD working directory, also the MCP ALLOWED_PATHS whitelist. Default: D:\AutoCad.
.PARAMETER Check
  Report only, change nothing. Exit code 0 when everything is in place, 1 otherwise.
.PARAMETER NoDshPatch
  Do not read or rewrite ~/.dsh/cordis.patch.yml. Set this when the two MCP
  servers are registered by the npm bundle patch (dsh plugin add) instead.
.PARAMETER SkipCodex
  Ignore the Codex junctions and config.
.PARAMETER Force
  Passed to install.ps1: rebuild the venvs and re-clone the skill.
.EXAMPLE
  .\bootstrap.ps1 -Check
.EXAMPLE
  .\bootstrap.ps1
.EXAMPLE
  .\bootstrap.ps1 -BaseDir "E:\cad" -WorkDir "E:\AutoCad"
#>
param(
    [string]$Python = "python",
    [string]$BaseDir = "D:\cad-agents",
    [string]$WorkDir = "D:\AutoCad",
    [switch]$Check,
    [switch]$SkipCodex,
    [switch]$NoDshPatch,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$VenvAcad    = Join-Path $BaseDir "venv-autocad"
$VenvSw      = Join-Path $BaseDir "venv-solidworks"
$SkillsDir   = Join-Path $BaseDir "dsh-skills"
$SkillDest   = Join-Path $SkillsDir "solidworks-automation"
$ShimDir     = Join-Path $BaseDir "mcp-shim"
$ShimScript  = Join-Path $ShimDir "sw_discovery_shim.py"
$ShimCatalog = Join-Path $ShimDir "sw_catalog.json"
$SwServer    = Join-Path $SkillDest "mcp-server\server.py"
$DshHome     = Join-Path $env:USERPROFILE ".dsh"
$PatchFile   = Join-Path $DshHome "cordis.patch.yml"
$Junction    = Join-Path $DshHome "skills"
$CodexHome   = Join-Path $env:USERPROFILE ".codex"
$CodexSkills = Join-Path $CodexHome "skills"
$CodexConfig = Join-Path $CodexHome "config.toml"
$Installer   = Join-Path $PSScriptRoot "install.ps1"
$Snippet     = Join-Path $PSScriptRoot "codex-config.snippet.toml"

$PyAcad = Join-Path $VenvAcad "Scripts\python.exe"
$PySw   = Join-Path $VenvSw   "Scripts\python.exe"
$AcadExe = Join-Path $VenvAcad "Scripts\autocad-mcp.exe"

function Add-Item {
    param($Group, $Name, $State, $Detail, $Repair)
    $script:items += [pscustomobject]@{
        Group = $Group; Name = $Name; State = $State; Detail = $Detail; Repair = $Repair
    }
}

function Test-FileContains {
    param([string]$Path, [string[]]$Needles)
    $missing = @()
    if (-not (Test-Path -LiteralPath $Path)) {
        return $Needles
    }
    $text = Get-Content -LiteralPath $Path -Raw -ErrorAction SilentlyContinue
    if ($null -eq $text) { return $Needles }
    foreach ($n in $Needles) {
        if ($text.IndexOf($n) -lt 0) { $missing += $n }
    }
    return $missing
}

function Get-MissingModules {
    param([string]$Py, [string[]]$Modules)
    $list = ($Modules | ForEach-Object { "'" + $_ + "'" }) -join ","
    $code = "import importlib.util as u; print('|'.join([m for m in [$list] if u.find_spec(m) is None]))"
    $out = & $Py -c $code 2>$null
    if ($LASTEXITCODE -ne 0 -or $null -eq $out) { return $Modules }
    $text = ($out -join "").Trim()
    if ($text -eq "") { return @() }
    return $text.Split("|")
}

function Invoke-Detect {
    $script:items = @()

    # 1. Python
    $pyResolved = $null
    if (Test-Path -LiteralPath $Python) {
        $pyResolved = $Python
    }
    else {
        $cmd = Get-Command $Python -ErrorAction SilentlyContinue
        if ($cmd) { $pyResolved = $cmd.Source }
    }
    if (-not $pyResolved) {
        Add-Item "core" "python" "MISSING" "no interpreter found for '$Python'" $null
    }
    else {
        $ver = & $pyResolved -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $ver) {
            Add-Item "core" "python" "MISSING" "'$pyResolved' will not run" $null
        }
        else {
            $parts = $ver.Split(".")
            $okVer = ([int]$parts[0] -gt 3) -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 11)
            if ($okVer) {
                Add-Item "core" "python" "OK" "$ver at $pyResolved" $null
            }
            else {
                Add-Item "core" "python" "MISSING" "$ver is too old, need >= 3.11" $null
            }
        }
    }

    # 2. AutoCAD upstream
    if (-not (Test-Path -LiteralPath $PyAcad)) {
        Add-Item "core" "venv-autocad" "MISSING" $VenvAcad $null
    }
    elseif (-not (Test-Path -LiteralPath $AcadExe)) {
        Add-Item "core" "autocad-mcp-pro" "MISSING" "package not installed in $VenvAcad" $null
    }
    else {
        $shown = & $PyAcad -m pip show autocad-mcp-pro 2>$null
        $verLine = ($shown | Select-String -Pattern "^Version:" | Select-Object -First 1)
        if ($verLine) {
            $verText = $verLine.Line.Replace("Version:", "").Trim()
            Add-Item "core" "autocad-mcp-pro" "OK" "v$verText" $null
        }
        else {
            Add-Item "core" "autocad-mcp-pro" "MISSING" "pip show autocad-mcp-pro failed" $null
        }
    }

    # 3. SolidWorks upstream deps
    if (-not (Test-Path -LiteralPath $PySw)) {
        Add-Item "core" "venv-solidworks" "MISSING" $VenvSw $null
    }
    else {
        $missing = @(Get-MissingModules $PySw @("mcp", "fitz", "ezdxf", "win32com"))
        if ($missing.Count -eq 0) {
            Add-Item "core" "sw-dependencies" "OK" "mcp, pymupdf, ezdxf, pywin32 present" $null
        }
        else {
            Add-Item "core" "sw-dependencies" "MISSING" ("missing: " + ($missing -join ", ")) $null
        }
    }

    # 4. SolidWorks skill clone
    if ((Test-Path -LiteralPath $SwServer) -and (Test-Path -LiteralPath (Join-Path $SkillDest "SKILL.md"))) {
        Add-Item "core" "sw-skill" "OK" $SkillDest $null
    }
    else {
        Add-Item "core" "sw-skill" "MISSING" $SkillDest $null
    }

    # 5. discovery shim + catalogue
    if (-not (Test-Path -LiteralPath $ShimScript)) {
        Add-Item "light" "sw-shim" "MISSING" $ShimScript $null
    }
    else {
        Add-Item "light" "sw-shim" "OK" $ShimDir $null
        if (-not (Test-Path -LiteralPath $ShimCatalog)) {
            Add-Item "light" "shim-catalogue" "MISSING" "run --build-catalog" $null
        }
        elseif (Test-Path -LiteralPath $SwServer) {
            $catTime = (Get-Item -LiteralPath $ShimCatalog).LastWriteTime
            $srvTime = (Get-Item -LiteralPath $SwServer).LastWriteTime
            if ($catTime -lt $srvTime) {
                Add-Item "light" "shim-catalogue" "STALE" "older than server.py, rebuild" $null
            }
            else {
                Add-Item "light" "shim-catalogue" "OK" $catTime.ToString("yyyy-MM-dd HH:mm") $null
            }
        }
        else {
            Add-Item "light" "shim-catalogue" "OK" "present" $null
        }
    }

    # 6. dsh skills junction
    $swVisible = Join-Path $Junction "solidworks-automation\SKILL.md"
    $acadVisible = Join-Path $Junction "autocad-mcp-pro\SKILL.md"
    if ((Test-Path -LiteralPath $swVisible) -and (Test-Path -LiteralPath $acadVisible)) {
        Add-Item "light" "dsh-skills-junction" "OK" $Junction $null
    }
    else {
        Add-Item "light" "dsh-skills-junction" "MISSING" $Junction $null
    }

    # 7. dsh config entries -- not checked when the npm bundle patch registers them
    if (-not $NoDshPatch) {
        $needDsh = @("mcp-autocad", "mcp-solidworks", "sw_discovery_shim.py")
        $missDsh = @(Test-FileContains $PatchFile $needDsh)
        if ($missDsh.Count -eq 0) {
            Add-Item "light" "dsh-config" "OK" $PatchFile $null
        }
        else {
            Add-Item "light" "dsh-config" "MISSING" ("absent: " + ($missDsh -join ", ")) $null
        }
    }

    if ($SkipCodex) {
        return
    }

    # 8. Codex junctions
    $cSw = Join-Path $CodexSkills "solidworks-automation\SKILL.md"
    $cAcad = Join-Path $CodexSkills "autocad-mcp-pro\SKILL.md"
    if ((Test-Path -LiteralPath $cSw) -and (Test-Path -LiteralPath $cAcad)) {
        Add-Item "light" "codex-skills-junction" "OK" $CodexSkills $null
    }
    else {
        Add-Item "light" "codex-skills-junction" "MISSING" $CodexSkills $null
    }

    # 9. Codex config entries
    $needCodex = @("[mcp_servers.autocad]", "[mcp_servers.solidworks]", "sw_discovery_shim.py")
    $missCodex = @(Test-FileContains $CodexConfig $needCodex)
    if ($missCodex.Count -eq 0) {
        Add-Item "light" "codex-config" "OK" $CodexConfig $null
    }
    else {
        Add-Item "light" "codex-config" "MISSING" ("absent: " + ($missCodex -join ", ")) $null
    }
}

function Show-Items {
    foreach ($i in $script:items) {
        Write-Host ("  [{0,-7}] {1,-22} {2}" -f $i.State, $i.Name, $i.Detail)
    }
}

function Invoke-Installer {
    if (-not (Test-Path -LiteralPath $Installer)) { throw "install.ps1 not found next to bootstrap.ps1" }
    & $Installer -BaseDir $BaseDir -WorkDir $WorkDir -Python $Python -Force:$Force -SkipDshPatch:$NoDshPatch
}

function Repair-Shim {
    $src = Join-Path $PSScriptRoot "mcp-shim"
    if (-not (Test-Path -LiteralPath $src)) { throw "mcp-shim folder not found next to bootstrap.ps1" }
    New-Item -ItemType Directory -Force -Path $ShimDir | Out-Null
    foreach ($f in @("sw_discovery_shim.py", "sw_lexicon.json", "README.md")) {
        $from = Join-Path $src $f
        if (Test-Path -LiteralPath $from) {
            Copy-Item -LiteralPath $from -Destination (Join-Path $ShimDir $f) -Force
        }
    }
    Write-Host "  installed the discovery shim to $ShimDir"
}

function Repair-Catalogue {
    & $PySw $ShimScript --python $PySw --server $SwServer --build-catalog
    if ($LASTEXITCODE -ne 0) { throw "catalogue build failed" }
}

function Repair-DshJunction {
    New-Item -ItemType Directory -Force -Path $DshHome | Out-Null
    New-Item -ItemType Directory -Force -Path $SkillsDir | Out-Null
    if (Test-Path -LiteralPath $Junction) {
        Write-Host "  $Junction exists but does not resolve; remove it and re-run with -Force"
        return
    }
    New-Item -ItemType Junction -Path $Junction -Target $SkillsDir | Out-Null
    Write-Host "  junctioned $Junction -> $SkillsDir"
}

function Repair-CodexJunction {
    New-Item -ItemType Directory -Force -Path $CodexSkills | Out-Null
    foreach ($pair in @(
            @("solidworks-automation", (Join-Path $SkillsDir "solidworks-automation")),
            @("autocad-mcp-pro", (Join-Path $SkillsDir "autocad-mcp-pro")))) {
        $link = Join-Path $CodexSkills $pair[0]
        if (-not (Test-Path -LiteralPath $link)) {
            New-Item -ItemType Junction -Path $link -Target $pair[1] | Out-Null
            Write-Host "  junctioned $link -> $($pair[1])"
        }
    }
}

function Repair-CodexConfig {
    if (-not (Test-Path -LiteralPath $Snippet)) { throw "codex-config.snippet.toml not found" }
    New-Item -ItemType Directory -Force -Path $CodexHome | Out-Null
    if (Test-Path -LiteralPath $CodexConfig) {
        $bak = "$CodexConfig.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Copy-Item -LiteralPath $CodexConfig -Destination $bak -Force
        Write-Host "  backup: $bak"
        $existing = Get-Content -LiteralPath $CodexConfig -Raw
        if ($existing -and -not $existing.EndsWith("`n")) {
            Add-Content -LiteralPath $CodexConfig -Value ""
        }
    }
    Add-Content -LiteralPath $CodexConfig -Value ""
    Add-Content -LiteralPath $CodexConfig -Value "# --- appended by dsh-sw-cad/bootstrap.ps1 ---"
    Get-Content -LiteralPath $Snippet | Add-Content -LiteralPath $CodexConfig
    Write-Host "  appended $Snippet to $CodexConfig"
}

# ---------------------------------------------------------------- main

Write-Host ""
Write-Host "dsh-sw-cad bootstrap -- BaseDir $BaseDir"
Write-Host ""

Invoke-Detect
Write-Host "Before:"
Show-Items

$coreBad = @($script:items | Where-Object { $_.Group -eq "core" -and $_.State -ne "OK" })
$lightBad = @($script:items | Where-Object { $_.Group -eq "light" -and $_.State -ne "OK" })

if ($Check) {
    Write-Host ""
    if ($coreBad.Count -eq 0 -and $lightBad.Count -eq 0) {
        Write-Host "Everything is in place."
        exit 0
    }
    $wouldFix = (($coreBad + $lightBad) | ForEach-Object { $_.Name }) -join ", "
    Write-Host "Would fix: $wouldFix"
    exit 1
}

if ($coreBad.Count -gt 0) {
    Write-Host ""
    Write-Host "Installing the missing upstream pieces:"
    Invoke-Installer
    Invoke-Detect
}

# light repairs, in dependency order
$fixOrder = @("sw-shim", "shim-catalogue", "dsh-skills-junction", "dsh-config",
              "codex-skills-junction", "codex-config")
foreach ($name in $fixOrder) {
    $item = $script:items | Where-Object { $_.Name -eq $name -and $_.State -ne "OK" } | Select-Object -First 1
    if (-not $item) { continue }
    Write-Host ""
    Write-Host "Fixing $name ($($item.State)):"
    switch ($name) {
        "sw-shim" { Repair-Shim }
        "shim-catalogue" { Repair-Catalogue }
        "dsh-skills-junction" { Repair-DshJunction }
        "dsh-config" { Invoke-Installer }
        "codex-skills-junction" { Repair-CodexJunction }
        "codex-config" { Repair-CodexConfig }
    }
    Invoke-Detect
}

Invoke-Detect
Write-Host ""
Write-Host "After:"
Show-Items
Write-Host ""

$remaining = @($script:items | Where-Object { $_.State -ne "OK" })
if ($remaining.Count -gt 0) {
    Write-Host ("Still unresolved: " + (($remaining | ForEach-Object { $_.Name }) -join ", "))
    Write-Host "See the Troubleshooting section of README.md."
    exit 1
}

Write-Host "All set. Next:"
Write-Host "  pnpm dsh --profile headless --dump-config"
Write-Host "  codex mcp list"
Write-Host "  start AutoCAD / SolidWorks as the same Windows user as your agent"
Write-Host ""
exit 0
