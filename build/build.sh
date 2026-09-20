#!/usr/bin/env bash
# QuickTranslate macOS / Linux 打包脚本
# 用法：bash build/build.sh [--onefile] [--skip-models]
# 注意：macOS 的 .app 必须在 macOS 本机构建，无法交叉编译。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"
ONEFILE=0
SKIP_MODELS=0

for arg in "$@"; do
  case "$arg" in
    --onefile) ONEFILE=1 ;;
    --skip-models) SKIP_MODELS=1 ;;
  esac
done

if [ ! -x "$PYTHON" ]; then
  echo "未找到虚拟环境：$PYTHON"
  echo "请先执行：python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

cd "$ROOT"

if [ "$SKIP_MODELS" -eq 0 ]; then
  echo "==> 准备离线 OCR 模型"
  "$PYTHON" tools/prepare_ocr_models.py
fi

echo "==> 生成应用图标"
"$PYTHON" tools/make_icon.py

if [ "$ONEFILE" -eq 1 ]; then
  export QT_ONEFILE=1
  echo "==> 打包模式：单文件"
else
  export QT_ONEFILE=0
  echo "==> 打包模式：目录版"
fi

"$PYTHON" -m PyInstaller --noconfirm --clean build/quicktranslate.spec

echo ""
echo "==> 打包完成，产物位于 dist/"
