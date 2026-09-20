"""应用图标：优先使用 resources/icons 下的文件，否则运行时绘制。"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

from ..config import paths

_ACCENT = "#2F6FED"
_cache: QIcon | None = None


def _draw(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(_ACCENT))
    radius = size * 0.24
    painter.drawRoundedRect(1, 1, size - 2, size - 2, radius, radius)

    painter.setPen(QColor("#FFFFFF"))
    font = QFont()
    font.setFamilies(["Microsoft YaHei UI", "PingFang SC", "Segoe UI", "sans-serif"])
    font.setPointSizeF(size * 0.46)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "译")
    painter.end()
    return pixmap


def build_icon() -> QIcon:
    global _cache
    if _cache is not None:
        return _cache

    for name in ("app.ico", "app.png"):
        path = paths.icon_path(name)
        if path and os.path.exists(path):
            icon = QIcon(path)
            if not icon.isNull():
                _cache = icon
                return icon

    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_draw(size))
    _cache = icon
    return icon


def save_ico(path: str) -> bool:
    """导出 .ico 供打包使用。"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return _draw(256).save(path, "ICO")
    except Exception:  # noqa: BLE001
        return False
