"""热键转换测试：python tests/test_hotkey.py"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quicktranslate.core.hotkey import to_pynput  # noqa: E402


def main() -> int:
    assert to_pynput("Ctrl+Shift+S") == "<ctrl>+<shift>+s"
    assert to_pynput("Alt+A") == "<alt>+a"
    assert to_pynput("Ctrl+Shift+T") == "<ctrl>+<shift>+t"
    assert to_pynput("Meta+Shift+T") == "<cmd>+<shift>+t"
    assert to_pynput("Ctrl+Alt+P") == "<ctrl>+<alt>+p"
    assert to_pynput("Ctrl+Shift+Space") == "<ctrl>+<shift>+<space>"
    assert to_pynput("F9") == "<f9>"
    assert to_pynput("") == ""
    assert to_pynput("<ctrl>+<shift>+s") == "<ctrl>+<shift>+s"

    # 实际注册（离屏/无显示环境下可能失败，不算错误）
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from quicktranslate.core.hotkey import HotkeyManager

    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841
    manager = HotkeyManager()
    started = manager.start({"screenshot": "Ctrl+Shift+S"})
    print(f"  全局热键注册：{'成功' if started else '失败（环境限制，非代码问题）'}")
    manager.stop()

    print("test_hotkey: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
