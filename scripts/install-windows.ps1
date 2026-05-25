# Install ClaudeBlinker for Windows (native — Claude Code running on Windows, not WSL).
# Run from PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1

$ErrorActionPreference = "Stop"

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoDir    = Split-Path -Parent $ScriptDir
$InstallDir = "$env:USERPROFILE\.local\claude-blinker"
$BinDir     = "$env:USERPROFILE\.local\bin"
$HooksDir   = "$InstallDir\hooks"

Write-Host "Installing ClaudeBlinker for Windows..."

# Python packages -------------------------------------------------------
Write-Host "Installing Python packages..."
pip install pystray pillow
if ($LASTEXITCODE -ne 0) {
    Write-Warning "pip install failed. Ensure Python 3 is installed and 'pip' is on PATH."
    Write-Warning "tkinter ships with standard Python — no extra install needed."
}

# Install files ---------------------------------------------------------
New-Item -ItemType Directory -Force -Path $HooksDir | Out-Null
New-Item -ItemType Directory -Force -Path $BinDir   | Out-Null

Copy-Item "$RepoDir\app_windows.py" "$InstallDir\app.py"  -Force
Copy-Item "$RepoDir\setstate.py"    "$HooksDir\setstate.py" -Force

# Launcher batch file ---------------------------------------------------
$LauncherPath = "$BinDir\claude-blinker.bat"

@"
@echo off
setlocal
set APP=%USERPROFILE%\.local\claude-blinker\app.py
set TRIGGER=%USERPROFILE%\.claude-blinker\open-prefs
if not exist "%USERPROFILE%\.claude-blinker" mkdir "%USERPROFILE%\.claude-blinker"

REM Signal running instance to open Preferences, or start fresh.
tasklist /FI "WINDOWTITLE eq ClaudeBlinker" /FO CSV 2>NUL | find /I "ClaudeBlinker" >NUL 2>&1
if not errorlevel 1 (
    echo. > "%TRIGGER%"
    exit /b 0
)
start "" /b pythonw "%APP%"
"@ | Set-Content $LauncherPath -Encoding ASCII

# Add ~/.local/bin to PATH for this session and advise on permanent setup ----
$UserPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")
if ($UserPath -notlike "*$BinDir*") {
    [System.Environment]::SetEnvironmentVariable(
        "PATH", "$BinDir;$UserPath", "User"
    )
    Write-Host "Added $BinDir to user PATH (takes effect in new shells)."
}

Write-Host ""
Write-Host "Done."
Write-Host "  Start:       $LauncherPath"
Write-Host "  Or:          pythonw $InstallDir\app.py"
Write-Host "  setstate.py: $HooksDir\setstate.py"
Write-Host "  Hooks file:  $RepoDir\hooks-example-windows.json"
Write-Host ""
Write-Host "Merge hooks-example-windows.json into %USERPROFILE%\.claude\settings.json"
Write-Host "to wire up Claude Code."
