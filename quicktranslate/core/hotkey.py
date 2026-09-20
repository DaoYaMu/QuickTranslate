"""全局热键：pynput GlobalHotKeys 跑守护线程，回调经 Qt 信号切回主线程。"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ..utils.logging import get_logger

log = get_logger("hotkey")

_MODIFIERS = {
    "ctrl": "<ctrl>",
    "control": "<ctrl>",
    "alt": "<alt>",
    "option": "<alt>",
    "shift": "<shift>",
    "cmd": "<cmd>",
    "command": "<cmd>",
    "win": "<cmd>",
    "super": "<cmd>",
    "meta": "<cmd>",
}

_SPECIAL = {
    "space": "<space>",
    "tab": "<tab>",
    "esc": "<esc>",
    "escape": "<esc>",
    "enter": "<enter>",
    "return": "<enter>",
    "backspace": "<backspace>",
    "delete": "<delete>",
    "insert": "<insert>",
    "home": "<home>",
    "end": "<end>",
    "pageup": "<page_up>",
    "pagedown": "<page_down>",
    "up": "<up>",
    "down": "<down>",
    "left": "<left>",
    "right": "<right>",
}


def to_pynput(sequence: str) -> str:
    """把 ``Ctrl+Shift+S`` 转成 pynput 的 ``<ctrl>+<shift>+s``。"""
    sequence = (sequence or "").strip()
    if not sequence:
        return ""
    if sequence.startswith("<"):
        return sequence  # 已经是 pynput 格式
    parts = [p.strip() for p in sequence.split("+") if p.strip()]
    out: list[str] = []
    for part in parts:
        low = part.lower()
        if low in _MODIFIERS:
            out.append(_MODIFIERS[low])
        elif low in _SPECIAL:
            out.append(_SPECIAL[low])
        elif len(part) == 1:
            out.append(part.lower())
        elif low.startswith("f") and low[1:].isdigit():
            out.append(f"<{low}>")
        else:
            out.append(low)
    return "+".join(out)


class HotkeyManager(QObject):
    """注册 / 更新 / 注销全局热键。"""

    screenshot_triggered = Signal()
    selection_triggered = Signal()
    clipboard_triggered = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._listener = None

    @property
    def active(self) -> bool:
        return self._listener is not None

    def start(self, mapping: dict[str, str]) -> bool:
        """mapping 形如 ``{"screenshot": "Ctrl+Shift+S", ...}``。"""
        self.stop()
        try:
            from pynput import keyboard  # noqa: PLC0415
        except ImportError:
            log.error("未安装 pynput，全局热键不可用")
            return False

        actions = {
            "screenshot": self.screenshot_triggered,
            "selection": self.selection_triggered,
            "clipboard": self.clipboard_triggered,
        }
        bindings: dict[str, object] = {}
        for action, sequence in mapping.items():
            signal = actions.get(action)
            combo = to_pynput(sequence)
            if signal is None or not combo:
                continue
            bindings[combo] = (lambda s=signal: s.emit())

        if not bindings:
            log.warning("没有可注册的热键")
            return False

        try:
            listener = keyboard.GlobalHotKeys(bindings)  # type: ignore[arg-type]
            listener.daemon = True
            listener.start()
            self._listener = listener
            log.info("全局热键已注册：%s", ", ".join(bindings))
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("注册全局热键失败：%s", exc)
            self._listener = None
            return False

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception as exc:  # noqa: BLE001
                log.warning("停止热键监听失败：%s", exc)
            self._listener = None
