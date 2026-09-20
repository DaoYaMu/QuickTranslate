"""热键录入控件。

为什么不用 ``QKeySequenceEdit``
--------------------------------
Qt 自带的 ``QKeySequenceEdit`` 在按下 ``Ctrl+Shift+S`` 时**只会记录主键**，
修饰键被丢弃：实测无论按 ``Ctrl+Shift+S`` 还是 ``Alt+Shift+D``，
``keySequence()`` 都返回 ``"S"`` / ``"D"``。

对一个「注册系统级全局热键」的翻译工具来说这是致命的：

* 用户想录 ``Ctrl+Shift+S``，实际存下来的是裸 ``S``；
* 裸 ``S`` 被注册成全局热键后，**在任何程序里打字都会触发**；
* 对截图热键而言，后果就是"在设置里改快捷键 → 每按一个字母就弹一次截图遮罩"。

本控件改为自己处理 ``keyPressEvent``，用 ``QKeyEvent.modifiers()`` 取修饰键，
因此能正确得到 ``Ctrl+Shift+S``。录入期间还临时摘掉全局热键，
避免用户按下的测试按键触发真正功能。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

# 只有修饰键时不算一个合法的组合键
_MODIFIER_KEYS = {
    Qt.Key.Key_Control,
    Qt.Key.Key_Shift,
    Qt.Key.Key_Alt,
    Qt.Key.Key_Meta,
    Qt.Key.Key_AltGr,
    Qt.Key.Key_CapsLock,
    Qt.Key.Key_NumLock,
    Qt.Key.Key_ScrollLock,
}

# 这些键单独用不适合当热键（容易撞系统行为）
_REJECTED_KEYS = {
    Qt.Key.Key_Escape,
    Qt.Key.Key_Tab,
    Qt.Key.Key_Backtab,
    Qt.Key.Key_unknown,
}

# Qt 键位 → PortableText 里的名字
_SPECIAL_NAMES = {
    Qt.Key.Key_Space: "Space",
    Qt.Key.Key_Return: "Return",
    Qt.Key.Key_Enter: "Enter",
    Qt.Key.Key_Backspace: "Backspace",
    Qt.Key.Key_Delete: "Delete",
    Qt.Key.Key_Insert: "Insert",
    Qt.Key.Key_Home: "Home",
    Qt.Key.Key_End: "End",
    Qt.Key.Key_PageUp: "PgUp",
    Qt.Key.Key_PageDown: "PgDown",
    Qt.Key.Key_Up: "Up",
    Qt.Key.Key_Down: "Down",
    Qt.Key.Key_Left: "Left",
    Qt.Key.Key_Right: "Right",
    Qt.Key.Key_Print: "Print",
    Qt.Key.Key_Pause: "Pause",
}

_PLACEHOLDER = "点击这里，然后按下组合键"


def sequence_to_text(sequence: str) -> str:
    """``PortableText`` → 人类可读的显示文本（``Ctrl+Shift+S``）。"""
    text = (sequence or "").strip()
    if not text:
        return ""
    seq = QKeySequence(text)
    if seq.isEmpty():
        return text
    return seq.toString(QKeySequence.SequenceFormat.NativeText)


def _to_portable(
    event: QKeyEvent,
    held: set[int] | None = None,
    *,
    commit: bool = False,
) -> str:
    """把一次按键事件转成 ``PortableText``；无法成键时返回空串。

    ``held`` 是"当前按下的修饰键"集合。窗口管理器在按下主键时通常会把
    修饰键状态写进事件里，但**不保证**（远程桌面、输入法、自动化注入都可能
    给出 ``modifiers() == 0``）。所以两个来源取并集，谁有算谁。

    ``commit`` 表示"用户已经把组合键松开了吗"。

    这里有一条**必须按顺序**的处理：用户在按 ``Ctrl+Shift+S`` 时会先松
    ``Ctrl``。但如果按"当前持有哪些修饰键"去解读那次 ``Ctrl`` 的松开，
    就会把它误读成一次 ``Shift+D`` 之类的假组合键，把刚录好的值覆盖掉。
    所以 ``commit=False`` 时只记录、不产出结果，等修饰键全松开再定稿。
    """
    key = event.key()
    if key in _REJECTED_KEYS or key in _MODIFIER_KEYS:
        return ""

    held = held or set()
    mods = event.modifiers()
    ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier) or Qt.Key.Key_Control in held
    alt = bool(mods & Qt.KeyboardModifier.AltModifier) or Qt.Key.Key_Alt in held
    shift = bool(mods & Qt.KeyboardModifier.ShiftModifier) or Qt.Key.Key_Shift in held
    meta = bool(mods & Qt.KeyboardModifier.MetaModifier) or Qt.Key.Key_Meta in held

    parts: list[str] = []
    # 顺序固定为 Ctrl、Alt、Shift、Meta，与 Windows 习惯一致
    if ctrl:
        parts.append("Ctrl")
    if alt:
        parts.append("Alt")
    if shift:
        parts.append("Shift")
    if meta:
        parts.append("Meta")

    if key in _SPECIAL_NAMES:
        parts.append(_SPECIAL_NAMES[key])
    elif Qt.Key.Key_F1 <= key <= Qt.Key.Key_F35:
        parts.append(f"F{key - Qt.Key.Key_F1 + 1}")
    else:
        text = event.text()
        if not text or not text.isprintable():
            return ""
        parts.append(text.upper())

    if len(parts) < 2:
        if commit and not mods and not held:
            # 用户最终松手时手里没剩修饰键 —— 确实是单键，明确拒绝。
            return ""
        # 还没定稿：给个中间值，等修饰键全松开后再以最终状态确认。
        return "".join(parts) if commit else ""
    return "+".join(parts)


class HotkeyEdit(QWidget):
    """录入单个全局热键的输入框。"""

    key_sequence_changed = Signal(str)
    #: 进入 / 离开录入状态。录入期间应暂停全局热键，
    #: 否则用户按下的组合键会真的触发对应功能（尤其是截图）。
    recording_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sequence = ""
        self._recording = False
        #: 当前按下的修饰键。事件里的 modifiers() 在某些情况下
        #: （远程桌面 / 输入法 / 自动化注入）会是 0，靠这个集合兜底。
        self._held: set[int] = set()
        #: 已经按下主键、但修饰键还没全松开时的暂存值。
        #: 必须先暂存 —— 用户松 Ctrl 的那一刻会再来一次事件，
        #: 直接落盘的话会被误读成"Shift+D"这类假组合键。
        self._pending = ""

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self._button = QPushButton()
        self._button.setObjectName("HotkeyField")
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._button.setToolTip(
            "必须包含修饰键（Ctrl / Alt / Shift 之一），例如 Ctrl+Shift+S。\n"
            "只按单个字母不会被接受 —— 那会抢走全系统的按键。\n"
            "在录入状态下按 Esc 取消，按 Delete / Backspace 清空。"
        )
        self._button.installEventFilter(self)
        lay.addWidget(self._button, 1)

        self._clear_btn = QPushButton("清空")
        self._clear_btn.setToolTip("清除这个快捷键")
        self._clear_btn.clicked.connect(lambda: self.set_sequence(""))
        lay.addWidget(self._clear_btn)

        self._refresh()

    # ------------------------------------------------------------------ #
    def sequence(self) -> str:
        return self._sequence

    def set_sequence(self, sequence: str) -> None:
        text = (sequence or "").strip()
        if text == self._sequence:
            self._refresh()
            return
        self._sequence = text
        self._refresh()
        self.key_sequence_changed.emit(text)

    # ------------------------------------------------------------------ #
    def _refresh(self) -> None:
        if self._recording:
            self._button.setText("请按下组合键…（Esc 取消）")
            self._button.setProperty("recording", True)
        else:
            pretty = sequence_to_text(self._sequence)
            self._button.setText(pretty or _PLACEHOLDER)
            self._button.setProperty("recording", False)
        # 让 QSS 里 [recording="true"] 的样式生效
        style = self._button.style()
        style.unpolish(self._button)
        style.polish(self._button)

    def _start_recording(self) -> None:
        if self._recording:
            return
        self._recording = True
        self._held.clear()
        self._pending = ""
        self._refresh()
        self._button.setFocus(Qt.FocusReason.OtherFocusReason)
        self.recording_changed.emit(True)

    def _stop_recording(self, *, commit: bool = True) -> None:
        if not self._recording:
            return
        if commit:
            self._commit_pending()
        self._recording = False
        self._held.clear()
        self._pending = ""
        self._refresh()
        self.recording_changed.emit(False)

    def _commit_pending(self) -> None:
        """把暂存的组合键正式落盘，并结束本次录入。"""
        pending = self._pending
        self._pending = ""
        if not pending:
            return
        self._stop_recording(commit=False)
        self.set_sequence(pending)

    # ------------------------------------------------------------------ #
    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is not self._button:
            return super().eventFilter(obj, event)

        etype = event.type()
        if etype == event.Type.MouseButtonPress:
            self._start_recording()
            return True
        if etype == event.Type.FocusOut:
            self._stop_recording()
            return False
        if etype == event.Type.KeyPress:
            self._track_press(event)
            return self._handle_key(event)
        if etype == event.Type.KeyRelease:
            self._track_release(event)
            return True
        return super().eventFilter(obj, event)

    def _track_press(self, event: QKeyEvent) -> None:
        if event.key() in _MODIFIER_KEYS:
            self._held.add(event.key())

    def _track_release(self, event: QKeyEvent) -> None:
        """按键松开时定稿。

        组合键的**最终**状态要看"修饰键全部松开"那一刻，
        因为用户在按下主键后会先松掉一部分修饰键
        （``Ctrl+Shift+S`` 常见松法是先松 ``Ctrl`` 再松 ``Shift``）。
        在那一刻之前 ``_to_portable`` 都会拒绝成键，不会误存中间值。
        """
        self._held.discard(event.key())
        if not self._recording or self._held:
            return
        # 修饰键都松开了 —— 有暂存值就可以定稿了
        if self._pending:
            self._commit_pending()
        else:
            self._stop_recording()

    def _handle_key(self, event: QKeyEvent) -> bool:
        if not self._recording:
            # 未处于录入状态时，任何按键都只是"开始录入"。
            # 但只有在**确实拿到焦点**时才算 —— 否则焦点在别处时
            # 飘进来的按键（切页、Tab 导航）会被误当成录入开始，
            # 把残留状态带进下一次录入。
            if event.key() in _MODIFIER_KEYS:
                return True
            if not self._button.hasFocus():
                return False  # 放行给父级，别吞掉
            self._start_recording()
            return self._handle_key(event)

        key = event.key()
        if key == Qt.Key.Key_Escape:
            self._pending = ""
            self._stop_recording()
            return True
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and not event.modifiers():
            if not self._held:
                self._pending = ""
                self.set_sequence("")
                self._stop_recording()
                return True
        if key in _MODIFIER_KEYS:
            return True  # 等主键

        # 主键按下：先暂存，等修饰键松开后由 _track_release 定稿
        candidate = _to_portable(event, self._held, commit=False)
        if candidate:
            self._pending = candidate
        elif not self._held and not event.modifiers():
            # 确定是单键 —— 明确拒绝（不落盘），但结束录入
            self._pending = ""
            self._stop_recording()
        return True
