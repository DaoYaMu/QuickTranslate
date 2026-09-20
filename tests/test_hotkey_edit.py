"""热键录入控件测试：python tests/test_hotkey_edit.py

回归重点
--------
Qt 自带的 ``QKeySequenceEdit`` 会丢掉修饰键（按 ``Ctrl+Shift+S`` 只记下 ``S``），
导致保存出裸字母热键 —— 那种热键注册到系统后会抢走全系统的该按键，
对截图功能而言就是「改快捷键时每按一下字母就弹一次截图」。
本测试锁死新控件的正确行为。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from quicktranslate.ui.hotkey_edit import HotkeyEdit, sequence_to_text  # noqa: E402


def _press(widget: HotkeyEdit, *keys) -> None:
    for key in keys:
        QTest.keyPress(widget._button, key)  # noqa: SLF001
    for key in reversed(keys):
        QTest.keyRelease(widget._button, key)  # noqa: SLF001


def _record(keys, release_order=None) -> str:
    """完整走一遍"按下 → 松开"，返回最终录入值。"""
    edit = HotkeyEdit()
    edit.show()
    edit._start_recording()  # noqa: SLF001
    for key in keys:
        QTest.keyPress(edit._button, key)  # noqa: SLF001
    for key in (release_order or list(reversed(keys))):
        QTest.keyRelease(edit._button, key)  # noqa: SLF001
    return edit.sequence()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841

    # ---------- 1. 修饰键必须被保留（核心回归） ----------
    for keys, expected in (
        ((Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_S), "Ctrl+Shift+S"),
        ((Qt.Key.Key_Alt, Qt.Key.Key_Shift, Qt.Key.Key_D), "Alt+Shift+D"),
        ((Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_P), "Ctrl+Alt+P"),
        ((Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Space), "Ctrl+Shift+Space"),
        ((Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_F9), "Ctrl+Shift+F9"),
    ):
        got = _record(list(keys))
        names = "+".join(
            {
                Qt.Key.Key_Control: "Ctrl",
                Qt.Key.Key_Alt: "Alt",
                Qt.Key.Key_Shift: "Shift",
                Qt.Key.Key_S: "S",
                Qt.Key.Key_D: "D",
                Qt.Key.Key_P: "P",
                Qt.Key.Key_Space: "Space",
                Qt.Key.Key_F9: "F9",
            }.get(k, str(k))
            for k in keys
        )
        assert got == expected, f"按下 {names} 应录入 {expected!r}，实际 {got!r}"
        print(f"  {names:>18} -> {got}")

    # ---------- 2. 松键顺序不能影响结果 ----------
    # 真实用户按 Ctrl+Shift+S 时常常先松 Ctrl，
    # 早期实现会把"松 Ctrl 时 Shift 还按着"误读成 Shift+D 之类的假组合键。
    combo = [Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_S]
    for order, label in (
        (list(reversed(combo)), "先松 S"),
        (combo, "先松 Ctrl 再松 Shift"),
        ([Qt.Key.Key_S, Qt.Key.Key_Shift, Qt.Key.Key_Control], "先松 Shift 再松 Ctrl"),
        ([Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_S], "先松 Shift 再松 Ctrl 再松 S"),
    ):
        got = _record(combo, order)
        assert got == "Ctrl+Shift+S", f"{label} 时录入成了 {got!r}，应为 'Ctrl+Shift+S'"
        print(f"  松键顺序「{label}」 -> {got}")

    # ---------- 3. 单个字母必须被拒绝 ----------
    # 就是这一条挡住了"每按一个字母弹一次截图"的 bug。
    for key in (Qt.Key.Key_S, Qt.Key.Key_D, Qt.Key.Key_T, Qt.Key.Key_A):
        assert _record([key]) == "", f"单个按键 {key} 不该被录入为热键"
    print("  单个字母被拒绝：OK")

    # ---------- 4. 只按修饰键不成键 ----------
    assert _record([Qt.Key.Key_Control, Qt.Key.Key_Shift]) == "", "只有修饰键不该成键"
    print("  纯修饰键被拒绝：OK")

    # ---------- 5. Esc 取消录入且不改动原值 ----------
    edit = HotkeyEdit()
    edit.set_sequence("Ctrl+Shift+S")
    edit._start_recording()  # noqa: SLF001
    QTest.keyPress(edit._button, Qt.Key.Key_Escape)  # noqa: SLF001
    QTest.keyRelease(edit._button, Qt.Key.Key_Escape)  # noqa: SLF001
    assert edit.sequence() == "Ctrl+Shift+S", "Esc 应该只取消录入、不修改已存的热键"
    print("  Esc 取消不改动原值：OK")

    # ---------- 6. Delete / Backspace 清空 ----------
    for key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
        edit = HotkeyEdit()
        edit.set_sequence("Ctrl+Shift+S")
        edit._start_recording()  # noqa: SLF001
        QTest.keyPress(edit._button, key)  # noqa: SLF001
        QTest.keyRelease(edit._button, key)  # noqa: SLF001
        assert edit.sequence() == "", f"{key} 应清空热键"
    print("  Delete / Backspace 清空：OK")

    # ---------- 7. 录入状态信号（用于暂停全局热键） ----------
    # 这一条是"改快捷键时弹截图"的直接防线：
    # 进入录入必须发 True 让上层摘掉全局热键，录完发 False 恢复。
    edit = HotkeyEdit()
    seen: list[bool] = []
    edit.recording_changed.connect(seen.append)
    edit._start_recording()  # noqa: SLF001
    _press(edit, Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_S)
    assert seen == [True, False], f"录完应发出 True -> False，实际 {seen}"
    print("  录入状态信号：OK")

    # ---------- 8. 不带修饰键的键位不被接受（防未来回归） ----------
    for key in (Qt.Key.Key_F9, Qt.Key.Key_Space):
        assert _record([key]) == "", f"无修饰键的 {key} 不该被接受"
    print("  无修饰键的功能键被拒绝：OK")

    # ---------- 9. 显示文本 ----------
    assert sequence_to_text("Ctrl+Shift+S") == "Ctrl+Shift+S"
    assert sequence_to_text("") == ""
    print("  显示文本：OK")

    # ---------- 10. 损坏的历史热键被丢弃 ----------
    from quicktranslate.ui.settings_dialog import SettingsDialog

    for bad in ("S", "D", "a", "F9"):
        assert SettingsDialog._sanitize_hotkey(bad) == "", f"{bad!r} 应被丢弃"  # noqa: SLF001
    for good in ("Ctrl+Shift+S", "Alt+Shift+D", "Ctrl+Alt+P"):
        assert SettingsDialog._sanitize_hotkey(good) == good  # noqa: SLF001
    print("  历史损坏热键被丢弃：OK")

    print("test_hotkey_edit: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
