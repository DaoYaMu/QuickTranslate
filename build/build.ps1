# QuickTranslate Windows 打包脚本
# 用法：
#   powershell -ExecutionPolicy Bypass -File build\build.ps1
#   powershell -ExecutionPolicy Bypass -File build\build.ps1 -OneFile

param(
    [switch]$OneFile,
    [switch]$SkipModels
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    Write-Error "未找到虚拟环境：$Python`n请先执行：python -m venv .venv 并 pip install -r requirements.txt"
}

Push-Location $Root
try {
    if (-not $SkipModels) {
        Write-Host "==> 准备离线 OCR 模型" -ForegroundColor Cyan
        & $Python "tools\prepare_ocr_models.py"
    }

    Write-Host "==> 生成应用图标" -ForegroundColor Cyan
    & $Python "tools\make_icon.py"

    if ($OneFile) {
        $env:QT_ONEFILE = "1"
        Write-Host "==> 打包模式：单文件 (onefile)" -ForegroundColor Cyan
    } else {
        $env:QT_ONEFILE = "0"
        Write-Host "==> 打包模式：目录版 (onedir)" -ForegroundColor Cyan
    }

    & $Python -m PyInstaller --noconfirm --clean "build\quicktranslate.spec"

    Write-Host ""
    Write-Host "==> 打包完成，产物位置：" -ForegroundColor Green
    if ($OneFile) {
        Write-Host "    dist\QuickTranslate.exe"
    } else {
        Write-Host "    dist\QuickTranslate\QuickTranslate.exe"
    }
}
finally {
    Pop-Location
}
