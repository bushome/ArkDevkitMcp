<#
.SYNOPSIS
  Registers ArkDevkitMcp with the Claude desktop app.

.DESCRIPTION
  Adds (or updates) an "ark-devkit" entry under mcpServers in claude_desktop_config.json. The existing
  file is backed up first and every other entry is kept. Restart the Claude desktop app afterwards.

  The server only needs Python's standard library. By default it runs on a standalone Python
  (-Python, else Python 3.11 found via the py launcher or the usual install folders), not the DevKit's
  bundled Python: the Epic launcher treats the DevKit as "running" while any process from the DevKit
  folder is alive, which would block launching it. The DevKit's Python is only used as a last resort.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install.ps1
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -DevkitRoot D:\ARKDevkit -Check
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -Python C:\Python311\python.exe
#>
param(
    [string]$DevkitRoot = "E:\ARKDevkit",
    [string]$ServerName = "ark-devkit",
    [string]$Python = "",  # python.exe to run the server with; empty = find a standalone install
    [switch]$Check,      # also run the server's --check against the open editor
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$repo   = Split-Path -Parent $MyInvocation.MyCommand.Path
$server = Join-Path $repo "src\arkmcp_server.py"
$devkitPython = Join-Path $DevkitRoot "Engine\Binaries\ThirdParty\Python3\Win64\python.exe"

function Test-InDevkit([string]$path) {
    $root = [IO.Path]::GetFullPath($DevkitRoot).TrimEnd('\') + '\'
    return [IO.Path]::GetFullPath($path).StartsWith($root, [StringComparison]::OrdinalIgnoreCase)
}

function Find-StandalonePython {
    $candidates = @()
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        try {
            $exe = & $py.Source -3.11 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) { $candidates += "$exe".Trim() }
        } catch { }  # no 3.11 registered with the py launcher
    }
    $candidates += @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
        (Join-Path $env:ProgramFiles "Python311\python.exe"),
        "C:\Python311\python.exe"
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c) -and -not (Test-InDevkit $c)) { return (Resolve-Path $c).Path }
    }
    return $null
}

if ($Python) {
    $python = $Python
} else {
    $python = Find-StandalonePython
}

if (-not $Uninstall) {
    if ($Python -and -not (Test-Path $python)) { throw "Python not found at $python" }
    if (-not $python) {
        if (-not (Test-Path $devkitPython)) { throw "No standalone Python 3.11 found, and no DevKit Python at $devkitPython (wrong -DevkitRoot?)" }
        $python = $devkitPython
        Write-Warning ("No standalone Python 3.11 found, using the DevKit's bundled Python. While the server runs, " +
            "the Epic launcher will think the DevKit is running. Install Python 3.11 (or pass -Python) and run this again.")
    } elseif (Test-InDevkit $python) {
        Write-Warning "$python is inside the DevKit folder; the Epic launcher will think the DevKit is running while the server runs."
    }
    if (-not (Test-Path $server)) { throw "Server script not found at $server" }
    Write-Host "Using Python: $python"
}

# Standard install, plus the Microsoft Store package location if that build is installed.
$targets = @(Join-Path $env:APPDATA "Claude\claude_desktop_config.json")
Get-ChildItem (Join-Path $env:LOCALAPPDATA "Packages") -Directory -Filter "Claude_*" -ErrorAction SilentlyContinue |
    ForEach-Object { $targets += (Join-Path $_.FullName "LocalCache\Roaming\Claude\claude_desktop_config.json") }

$utf8 = New-Object System.Text.UTF8Encoding $false
$wrote = 0
foreach ($cfg in $targets) {
    $dir = Split-Path -Parent $cfg
    if (-not (Test-Path $dir)) { continue }

    if (Test-Path $cfg) {
        $raw = [IO.File]::ReadAllText($cfg)
        Copy-Item $cfg "$cfg.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
        $json = if ($raw.Trim()) { $raw | ConvertFrom-Json } else { New-Object PSObject }
    } else {
        $json = New-Object PSObject
    }

    if (-not $json.PSObject.Properties["mcpServers"]) {
        $json | Add-Member -NotePropertyName mcpServers -NotePropertyValue (New-Object PSObject)
    }
    $servers = $json.mcpServers
    if ($servers.PSObject.Properties[$ServerName]) { $servers.PSObject.Properties.Remove($ServerName) }

    if (-not $Uninstall) {
        $entry = [ordered]@{ command = $python; args = @("-u", $server) }
        $servers | Add-Member -NotePropertyName $ServerName -NotePropertyValue ([PSCustomObject]$entry)
    }

    [IO.File]::WriteAllText($cfg, ($json | ConvertTo-Json -Depth 32), $utf8)
    Write-Host ("{0} '{1}' in {2}" -f $(if ($Uninstall) { "Removed" } else { "Registered" }), $ServerName, $cfg)
    $wrote++
}

if ($wrote -eq 0) {
    throw "No Claude desktop config folder found. Open the Claude desktop app once, then run this again."
}

if ($Check -and -not $Uninstall) {
    Write-Host "`nChecking the connection to the open DevKit editor..."
    & $python -u $server --check
}

Write-Host "`nRestart the Claude desktop app (quit it from the tray icon) to load the change."
