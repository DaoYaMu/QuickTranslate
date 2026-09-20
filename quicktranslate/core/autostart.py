"""跨平台开机自启。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from ..config import paths
from ..utils.logging import get_logger

log = get_logger("autostart")

APP_ID = "QuickTranslate"
_WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_MAC_PLIST_NAME = "com.quicktranslate.plist"
_LINUX_DESKTOP_NAME = "quicktranslate.desktop"


def _launch_command() -> str:
    """构造自启命令行（打包后指向 exe，开发环境指向 pythonw + main.py）。"""
    if paths.is_frozen():
        return f'"{sys.executable}"'

    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else python
    main_py = paths.project_root() / "main.py"
    return f'"{exe}" "{main_py}"'


# --------------------------------------------------------------------------- #
# Windows
# --------------------------------------------------------------------------- #
def _win_is_enabled() -> bool:
    import winreg  # noqa: PLC0415

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_ID)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.warning("读取自启注册表失败：%s", exc)
        return False


def _win_set(enabled: bool) -> bool:
    import winreg  # noqa: PLC0415

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(key, APP_ID, 0, winreg.REG_SZ, _launch_command())
            else:
                try:
                    winreg.DeleteValue(key, APP_ID)
                except FileNotFoundError:
                    pass
        return True
    except OSError as exc:
        log.error("写入自启注册表失败：%s", exc)
        return False


# --------------------------------------------------------------------------- #
# macOS
# --------------------------------------------------------------------------- #
def _mac_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / _MAC_PLIST_NAME


def _mac_is_enabled() -> bool:
    return _mac_plist_path().exists()


def _mac_set(enabled: bool) -> bool:
    target = _mac_plist_path()
    try:
        if not enabled:
            target.unlink(missing_ok=True)
            return True
        target.parent.mkdir(parents=True, exist_ok=True)
        if paths.is_frozen():
            prog_args = f"<string>{sys.executable}</string>"
        else:
            prog_args = (
                f"<string>{sys.executable}</string>"
                f"<string>{paths.project_root() / 'main.py'}</string>"
            )
        target.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><dict>'
            "<key>Label</key><string>com.quicktranslate</string>"
            "<key>ProgramArguments</key><array>"
            f"{prog_args}"
            "</array>"
            "<key>RunAtLoad</key><true/>"
            "</dict></plist>\n",
            encoding="utf-8",
        )
        return True
    except OSError as exc:
        log.error("写入 LaunchAgents 失败：%s", exc)
        return False


# --------------------------------------------------------------------------- #
# Linux
# --------------------------------------------------------------------------- #
def _linux_desktop_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "autostart" / _LINUX_DESKTOP_NAME


def _linux_is_enabled() -> bool:
    return _linux_desktop_path().exists()


def _linux_set(enabled: bool) -> bool:
    target = _linux_desktop_path()
    try:
        if not enabled:
            target.unlink(missing_ok=True)
            return True
        target.parent.mkdir(parents=True, exist_ok=True)
        if paths.is_frozen():
            command = sys.executable
        else:
            command = f"{sys.executable} {paths.project_root() / 'main.py'}"
        target.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=QuickTranslate\n"
            f"Exec={command}\n"
            "X-GNOME-Autostart-enabled=true\n",
            encoding="utf-8",
        )
        return True
    except OSError as exc:
        log.error("写入 autostart 失败：%s", exc)
        return False


# --------------------------------------------------------------------------- #
# 统一入口
# --------------------------------------------------------------------------- #
def is_enabled() -> bool:
    if sys.platform == "win32":
        return _win_is_enabled()
    if sys.platform == "darwin":
        return _mac_is_enabled()
    return _linux_is_enabled()


def set_enabled(enabled: bool) -> bool:
    if sys.platform == "win32":
        return _win_set(enabled)
    if sys.platform == "darwin":
        return _mac_set(enabled)
    return _linux_set(enabled)


def is_supported() -> bool:
    return sys.platform in ("win32", "darwin", "linux")
