"""基于 QSettings 的偏好持久化封装（跨平台：Windows 注册表 / macOS plist / Linux ini）。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSettings

from . import paths

# --- 键名常量 ---
K_THEME = "ui/theme"
K_GEOMETRY = "ui/geometry"
K_SPLITTER = "ui/splitter"

K_HOTKEY_ENABLED = "hotkey/enabled"
K_HOTKEY_SCREENSHOT = "hotkey/screenshot"
K_HOTKEY_SELECTION = "hotkey/selection"
K_HOTKEY_CLIPBOARD = "hotkey/clipboard"

K_AUTOSTART = "general/autostart"
K_CLOSE_TO_TRAY = "general/close_to_tray"
K_MINIMIZE_TO_TRAY = "general/minimize_to_tray"
K_SHOW_POPUP_ALWAYS = "general/show_popup_always"

K_SRC_LANG = "translate/source"
K_DST_LANG = "translate/target"

K_CURRENT_PROFILE = "profile/current"
K_OCR_LANG = "ocr/lang"
K_OCR_ENABLED = "ocr/enabled"


class Settings:
    """轻量包装 QSettings，提供带类型的方法与常用属性。"""

    def __init__(self, org: str = paths.ORG_NAME, app: str = paths.APP_NAME) -> None:
        self._q = QSettings(org, app)

    # --- 原始读写 ---
    def get(self, key: str, default: Any = None) -> Any:
        return self._q.value(key, default)

    def get_bool(self, key: str, default: bool = False) -> bool:
        return self._q.value(key, default, type=bool)  # type: ignore[return-value]

    def get_int(self, key: str, default: int = 0) -> int:
        return self._q.value(key, default, type=int)  # type: ignore[return-value]

    def get_float(self, key: str, default: float = 0.0) -> float:
        return self._q.value(key, default, type=float)  # type: ignore[return-value]

    def set(self, key: str, value: Any) -> None:
        self._q.setValue(key, value)

    def remove(self, key: str) -> None:
        self._q.remove(key)

    def sync(self) -> None:
        self._q.sync()

    # --- 常用属性 ---
    @property
    def theme(self) -> str:
        return str(self.get(K_THEME, "light"))

    @theme.setter
    def theme(self, value: str) -> None:
        self.set(K_THEME, value)

    @property
    def hotkeys_enabled(self) -> bool:
        return self.get_bool(K_HOTKEY_ENABLED, True)

    @hotkeys_enabled.setter
    def hotkeys_enabled(self, value: bool) -> None:
        self.set(K_HOTKEY_ENABLED, bool(value))

    @property
    def autostart(self) -> bool:
        return self.get_bool(K_AUTOSTART, False)

    @autostart.setter
    def autostart(self, value: bool) -> None:
        self.set(K_AUTOSTART, bool(value))

    @property
    def close_to_tray(self) -> bool:
        return self.get_bool(K_CLOSE_TO_TRAY, True)

    @close_to_tray.setter
    def close_to_tray(self, value: bool) -> None:
        self.set(K_CLOSE_TO_TRAY, bool(value))

    @property
    def minimize_to_tray(self) -> bool:
        return self.get_bool(K_MINIMIZE_TO_TRAY, False)

    @minimize_to_tray.setter
    def minimize_to_tray(self, value: bool) -> None:
        self.set(K_MINIMIZE_TO_TRAY, bool(value))

    @property
    def ocr_enabled(self) -> bool:
        return self.get_bool(K_OCR_ENABLED, True)

    @ocr_enabled.setter
    def ocr_enabled(self, value: bool) -> None:
        self.set(K_OCR_ENABLED, bool(value))

    @property
    def ocr_lang(self) -> str:
        return str(self.get(K_OCR_LANG, "ch"))

    @ocr_lang.setter
    def ocr_lang(self, value: str) -> None:
        self.set(K_OCR_LANG, value)

    def hotkey(self, action: str) -> str:
        """action ∈ {screenshot, selection, clipboard}。"""
        defaults = {
            "screenshot": "Ctrl+Shift+S",
            "selection": "Ctrl+Shift+T",
            "clipboard": "Ctrl+Shift+C",
        }
        key = {
            "screenshot": K_HOTKEY_SCREENSHOT,
            "selection": K_HOTKEY_SELECTION,
            "clipboard": K_HOTKEY_CLIPBOARD,
        }[action]
        return str(self.get(key, defaults[action]))

    def set_hotkey(self, action: str, value: str) -> None:
        key = {
            "screenshot": K_HOTKEY_SCREENSHOT,
            "selection": K_HOTKEY_SELECTION,
            "clipboard": K_HOTKEY_CLIPBOARD,
        }[action]
        self.set(key, value)
