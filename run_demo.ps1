# One-click reproduction: tests -> paradigm comparison -> hot-plug -> workflow
# Usage: powershell -ExecutionPolicy Bypass -File run_demo.ps1
$ErrorActionPreference = "Stop"
$env:PYTHONPATH = Join-Path $PSScriptRoot "src"
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot "reports") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot "runs") | Out-Null

Write-Host "== 1/4 pytest ==" -ForegroundColor Cyan
python -m pytest 2>&1 | Tee-Object -FilePath (Join-Path $PSScriptRoot "reports\pytest.txt")

Write-Host "== 2/4 paradigm comparison ==" -ForegroundColor Cyan
python demo\compare_paradigms.py 2>&1 | Tee-Object -FilePath (Join-Path $PSScriptRoot "reports\demo_output.txt")

Write-Host "== 3/4 tool hot-plug ==" -ForegroundColor Cyan
python demo\hotplug_demo.py 2>&1 | Tee-Object -FilePath (Join-Path $PSScriptRoot "reports\hotplug_demo.txt")

Write-Host "== 4/4 low-code workflow ==" -ForegroundColor Cyan
python demo\workflow_demo.py | Tee-Object -FilePath (Join-Path $PSScriptRoot "reports\workflow_demo.txt")

Write-Host "Done: reports/ and runs/ updated" -ForegroundColor Green
