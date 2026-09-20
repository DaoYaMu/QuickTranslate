"""划词取词自检：验证「按热键取当前选中文字」这条链路真的能用。

原理：起一个独立进程的窗口当复制目标（本进程阻塞取词时，目标进程仍有自己的
消息循环，能真实收到注入的按键），然后模拟热键的三种时序。

  [1] 不按修饰键            —— 对照组，验证注入本身可用
  [2] 按住 Alt+Shift 后松开  —— 真实热键时序（修复前必然失败）
  [3] 全程按住 Alt+Shift     —— 预期失败，但必须给出可读原因

用法：.venv/Scripts/python.exe tools/check_selection.py
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quicktranslate.core.clipboard import (  # noqa: E402
    get_clipboard_text,
    grab_selection,
    set_clipboard_text,
)

SAMPLE = "Hello selection test 划词测试 alpha beta gamma"
TITLE = "QT_SEL_TARGET_WINDOW"

user32 = ctypes.windll.user32


def run_target() -> int:
    """独立进程：显示一个带选中文本的窗口，等待被外部注入按键。"""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMainWindow, QTextEdit

    app = QApplication([])
    win = QMainWindow()
    win.setWindowTitle(TITLE)
    edit = QTextEdit(SAMPLE)
    win.setCentralWidget(edit)
    win.resize(620, 220)
    win.show()
    win.raise_()
    win.activateWindow()
    edit.setFocus()
    edit.selectAll()
    QTimer.singleShot(120_000, app.quit)  # 兜底退出
    return app.exec()


def focus(hwnd: int) -> bool:
    """抢前台焦点。Windows 前台锁很烦，多试几种手段。

    全都失败通常意味着**桌面已锁定**（锁屏界面占着前台）或有全屏程序，
    这时划词无从验证 —— 调用方应打印"跳过"而不是报失败。
    """
    user32.AllowSetForegroundWindow(-1)
    for _ in range(3):
        # ① 按住 Alt 不放时 SetForegroundWindow（最可靠的一招）
        user32.keybd_event(0x12, 0, 0, 0)  # VK_MENU down
        time.sleep(0.05)
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.15)
        user32.keybd_event(0x12, 0, 2, 0)  # VK_MENU up
        time.sleep(0.2)
        if user32.GetForegroundWindow() == hwnd:
            return True
        # ② AttachThreadInput 到当前前台线程后再抢
        current = user32.GetForegroundWindow()
        tid = user32.GetWindowThreadProcessId(current, None)
        me = ctypes.windll.kernel32.GetCurrentThreadId()
        user32.AttachThreadInput(tid, me, True)
        user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(tid, me, False)
        time.sleep(0.3)
        if user32.GetForegroundWindow() == hwnd:
            return True
    return False


def foreground_title() -> str:
    buffer = ctypes.create_unicode_buffer(160)
    user32.GetWindowTextW(user32.GetForegroundWindow(), buffer, 160)
    return buffer.value


def main() -> int:
    backup = get_clipboard_text()
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__)), "--target"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    failures: list[str] = []
    try:
        hwnd = 0
        end = time.monotonic() + 20
        while time.monotonic() < end and not hwnd:
            hwnd = user32.FindWindowW(None, TITLE)
            time.sleep(0.2)
        if not hwnd:
            print("跳过：没找到目标窗口")
            return 0
        if not focus(hwnd):
            print(f"跳过：抢不到前台焦点（当前前台是「{foreground_title()}」）")
            print("      桌面已锁定或有全屏程序占着时无法验证划词 —— 请解锁后重跑")
            return 0
        time.sleep(0.7)
        print(f"目标窗口已就绪（hwnd={hwnd}），文本已全选\n")

        from pynput.keyboard import Controller, Key

        kb = Controller()

        def select_all() -> None:
            with kb.pressed(Key.ctrl):
                kb.press("a")
                kb.release("a")
            time.sleep(0.4)

        def probe(name: str, hold: list, release_after: float | None) -> str:
            if not focus(hwnd):  # 每轮都确认还在前台，否则结论不可信
                print(f"  --   {name:<34} 跳过：丢掉了前台焦点")
                return ""
            select_all()
            for key in hold:
                kb.press(key)
            timer = None
            if release_after is not None and hold:
                timer = threading.Timer(release_after, _release, args=(hold,))
                timer.daemon = True
                timer.start()
            time.sleep(0.15)
            set_clipboard_text("")
            started = time.monotonic()
            text, reason = grab_selection(timeout=0.6)
            elapsed = time.monotonic() - started
            if timer is not None:
                timer.join(timeout=2)
            else:
                _release(hold)
            time.sleep(0.25)
            ok = bool(text.strip())
            print(
                f"  {'OK  ' if ok else '失败'} {name:<34} {elapsed:5.2f}s  "
                + (repr(text[:48]) if ok else f"原因：{reason}")
            )
            return "" if ok else name

        def _release(keys: list) -> None:
            for key in reversed(keys):
                try:
                    kb.release(key)
                except Exception:  # noqa: BLE001
                    pass

        print("=== 划词取词（目标：独立进程窗口）===")
        failures.append(probe("不按修饰键（对照组）", [], None))
        failures.append(
            probe("按住 Alt+Shift，0.25s 后松开", [Key.alt, Key.shift], 0.25)
        )
        print("\n--- 以下为预期失败，用来确认失败时提示是否可读 ---")
        probe("全程按住 Alt+Shift", [Key.alt, Key.shift], None)

        failures = [f for f in failures if f]
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()
        set_clipboard_text(backup)
        print("\n目标进程已关闭，剪贴板已还原")

    print()
    if failures:
        print("check_selection: FAIL ->", "、".join(failures))
        return 1
    print("check_selection: OK")
    return 0


if __name__ == "__main__":
    if "--target" in sys.argv:
        raise SystemExit(run_target())
    raise SystemExit(main())
