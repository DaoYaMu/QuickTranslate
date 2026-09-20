"""系统托盘（Qt 原生 QSystemTrayIcon，跨平台且无需额外依赖）。"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..utils.logging import get_logger

log = get_logger("tray")


class TrayIcon(QObject):
    show_requested = Signal()
    screenshot_requested = Signal()
    settings_requested = Signal()
    quit_requested = Signal()

    def __init__(self, icon: QIcon, parent=None) -> None:
        super().__init__(parent)
        self._tray = QSystemTrayIcon(icon, parent)
        self._tray.setToolTip("QuickTranslate · AI 翻译")

        menu = QMenu()

        act_show = QAction("显示主窗口", menu)
        act_show.triggered.connect(self.show_requested.emit)
        menu.addAction(act_show)

        act_shot = QAction("截图翻译", menu)
        act_shot.triggered.connect(self.screenshot_requested.emit)
        menu.addAction(act_shot)

        menu.addSeparator()

        act_settings = QAction("设置…", menu)
        act_settings.triggered.connect(self.settings_requested.emit)
        menu.addAction(act_settings)

        menu.addSeparator()

        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self.quit_requested.emit)
        menu.addAction(act_quit)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_activated)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_requested.emit()

    def show(self) -> None:
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray.show()
        else:
            log.warning("系统托盘不可用")

    def hide(self) -> None:
        self._tray.hide()

    def notify(self, title: str, message: str) -> None:
        try:
            self._tray.showMessage(title, message, self._tray.icon(), 3000)
        except Exception as exc:  # noqa: BLE001
            log.warning("托盘通知失败：%s", exc)
