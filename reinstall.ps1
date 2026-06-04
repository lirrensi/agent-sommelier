# reinstall.ps1 — Force reinstall agent-sommelier CLI tool
# Kills any processes locking the install dir, then reinstalls.
param([switch]$NoKill)

$ErrorActionPreference = "Stop"
$ToolDir = "$env:APPDATA\uv\tools\agent-sommelier-cli"

if (-not $NoKill) {
    Write-Host "Killing processes in $ToolDir ..." -ForegroundColor Yellow
    $procs = Get-Process | Where-Object {
        $_.Path -like "$ToolDir*"
    }
    if ($procs) {
        $procs | ForEach-Object {
            Write-Host "  Killing PID $($_.Id) ($($_.ProcessName))" -ForegroundColor Red
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        }
        Write-Host "Done." -ForegroundColor Green
    } else {
        Write-Host "  No locking processes found." -ForegroundColor DarkGray
    }
}

Write-Host "Installing..." -ForegroundColor Yellow
uv tool install . --force --reinstall
Write-Host "Done." -ForegroundColor Green
