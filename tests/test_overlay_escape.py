"""截图遮罩退出路径测试：python tests/test_overlay_escape.py

回归背景
--------
用户报告「在设置里改快捷键会进入截图模式，然后无法退出也无法截图」。
根因有两处，这个文件锁住第二处的修复：

1. 快捷键被存成裸字母（见 ``test_hotkey_edit.py``），导致按任何字母都触发截图；
2. 遮罩本身**只有 Esc 一条退路** —— 一旦置顶窗口抢不到键盘焦点，
   用户就真的退不出去了，只能杀进程。

所以遮罩必须有多条独立退路：Esc、回车、右键、以及无操作自动超时。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QMouseEvent, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from quicktranslate.ui.screenshot_overlay import ScreenshotOverlay  # noqa: E402


def _make(app):
    overlay = ScreenshotOverlay(QPixmap(400, 300), QRect(0, 0, 400, 300))
    state = {"cancelled": 0, "captured": 0}
    overlay.cancelled.connect(lambda: state.__setitem__("cancelled", state["cancelled"] + 1))
    overlay.captured.connect(lambda *_: state.__setitem__("captured", state["captured"] + 1))
    overlay.show()
    app.processEvents()
    return overlay, state


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841

    no_mod = Qt.KeyboardModifier.NoModifier

    # ---------- 1. Esc 退出（主路径） ----------
    overlay, state = _make(app)
    overlay.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, no_mod))
    app.processEvents()
    assert state["cancelled"] == 1, "Esc 应该取消截图"
    print("  Esc 退出：OK")

    # ---------- 2. 回车退出 ----------
    # Esc 可能被输入法或全屏程序吃掉，必须留一条备用退路。
    overlay, state = _make(app)
    overlay.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, no_mod))
    app.processEvents()
    assert state["cancelled"] == 1, "回车应该也能取消截图"
    print("  回车退出：OK")

    # ---------- 3. 右键退出 ----------
    overlay, state = _make(app)
    overlay.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(10, 10),
            QPointF(10, 10),
            Qt.MouseButton.RightButton,
            Qt.MouseButton.RightButton,
            no_mod,
        )
    )
    app.processEvents()
    assert state["cancelled"] == 1, "右键应该取消截图"
    print("  右键退出：OK")

    # ---------- 4. 无操作自动超时退出 ----------
    # 最后一道保险：即使用户离开电脑，遮罩也不会永久盖住桌面。
    overlay, state = _make(app)
    assert overlay._timeout.isActive(), "遮罩显示时必须启动超时定时器"  # noqa: SLF001
    assert overlay._timeout.interval() > 0  # noqa: SLF001
    overlay._timeout.setInterval(0)  # noqa: SLF001
    overlay._timeout.start()  # noqa: SLF001
    app.processEvents()
    assert state["cancelled"] == 1, "超时应该自动取消截图"
    print("  超时自动退出：OK")

    # ---------- 5. 框选成功时不该误报取消 ----------
    overlay, state = _make(app)
    overlay._origin = QPointF(10, 10).toPoint()  # noqa: SLF001
    overlay._current = QPointF(120, 90).toPoint()  # noqa: SLF001
    overlay._finish(QRect(10, 10, 110, 80))  # noqa: SLF001
    app.processEvents()
    assert state["captured"] == 1, "框选完成应该发出 captured"
    assert state["cancelled"] == 0, "框选完成不该同时报取消"
    print("  框选成功不误报取消：OK")

    # ---------- 6. Esc 之后定时器要停掉 ----------
    # 遮罩带 WA_DeleteOnClose，Esc 后 C++ 对象即被释放，
    # 此时访问 _timeout 会抛 RuntimeError —— 这本身就说明做过清理。
    overlay, state = _make(app)
    timer = overlay._timeout  # noqa: SLF001
    overlay.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, no_mod))
    app.processEvents()
    try:
        active = timer.isActive()
    except RuntimeError:
        active = False  # 定时器随遮罩一起销毁，等价于已停止
    assert not active, "退出后必须停掉超时定时器"
    print("  退出后定时器停止：OK")

    print("test_overlay_escape: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
