"""生成打包用的 resources/icons/app.ico（无需可见桌面环境）。

用法：python tools/make_icon.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from PySide6.QtGui import QGuiApplication  # noqa: E402

from quicktranslate.ui.appicon import save_ico  # noqa: E402


def main() -> int:
    QGuiApplication(sys.argv)  # QPixmap 需要一个 GUI 应用实例
    target = _ROOT / "resources" / "icons" / "app.ico"
    if save_ico(str(target)):
        print(f"已生成图标：{target}")
        return 0
    print("图标生成失败")
    return 1


if __name__ == "__main__":
    sys.exit(main())
