# Explicit local publication; no Wind, production updater or scheduler.
$ErrorActionPreference = 'Stop'
$terminalRoot = Split-Path -Parent $PSScriptRoot
$terminalPython = Join-Path $terminalRoot '.venv/Scripts/python.exe'
$occupied = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in 8790,8794,8795 })
if ($occupied.Count) { throw '请先正常停止统一终端，再发布期货版本；不会停止现有进程。' }
Push-Location (Join-Path $terminalRoot 'frontend')
try {
    if (-not (Test-Path -LiteralPath 'node_modules/vite/bin/vite.js')) { throw '前端依赖未安装，请先按README准备依赖。' }
    & $terminalPython -X utf8 tools/build_frontend.py --refresh-futures
    if ($LASTEXITCODE -ne 0) { throw '期货前端副本校验失败。' }
    & node node_modules/vite/bin/vite.js build
    if ($LASTEXITCODE -ne 0) { throw '期货前端发布失败；未继续更新后端和数据。' }
} finally { Pop-Location }
& $terminalPython -X utf8 (Join-Path $PSScriptRoot 'freeze_backend.py') --refresh-data
if ($LASTEXITCODE -ne 0) { throw '期货后端或数据发布失败；请查看输出后再启动。' }
Write-Host '期货前端、后端和已保存数据已发布。运行 start.ps1 打开统一终端。'
