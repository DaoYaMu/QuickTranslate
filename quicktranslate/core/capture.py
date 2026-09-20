"""截图与图像转换工具。"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QPixmap

from ..utils.logging import get_logger

log = get_logger("capture")


def qimage_to_ndarray(image: QImage) -> np.ndarray:
    """QImage -> numpy RGB (H, W, 3)。"""
    img = image.convertToFormat(QImage.Format.Format_RGB888)
    width, height = img.width(), img.height()
    if width <= 0 or height <= 0:
        return np.zeros((0, 0, 3), dtype=np.uint8)
    buffer = np.frombuffer(img.constBits(), dtype=np.uint8)
    expected = img.sizeInBytes()
    if buffer.size < expected:
        log.warning("QImage 缓冲区比预期小：%s < %s", buffer.size, expected)
        expected = buffer.size
    row_bytes = img.bytesPerLine()
    usable = buffer[: height * row_bytes]
    arr = usable.reshape(height, row_bytes)[:, : width * 3].reshape(height, width, 3)
    return np.ascontiguousarray(arr)


def qpixmap_to_ndarray(pixmap: QPixmap) -> np.ndarray:
    return qimage_to_ndarray(pixmap.toImage())


def load_image_file(path: str) -> np.ndarray:
    """从磁盘读取图片为 numpy RGB。"""
    image = QImage(path)
    if image.isNull():
        raise ValueError(f"无法读取图片：{path}")
    return qimage_to_ndarray(image)


def virtual_desktop_rect() -> QRect:
    """所有显示器几何的并集。"""
    rect = QRect()
    for screen in QGuiApplication.screens():
        rect = rect.united(screen.geometry())
    return rect


def grab_virtual_desktop() -> tuple[QPixmap, QRect, float]:
    """抓取整个虚拟桌面，返回 (pixmap, 桌面逻辑矩形, devicePixelRatio)。

    通过把每块屏幕的抓图拼到一张画布上，兼容多显示器与负坐标。
    """
    screens = QGuiApplication.screens()
    rect = virtual_desktop_rect()
    dpr = max((s.devicePixelRatio() for s in screens), default=1.0)

    canvas = QPixmap(int(rect.width() * dpr), int(rect.height() * dpr))
    canvas.setDevicePixelRatio(dpr)
    canvas.fill(Qt.GlobalColor.black)

    painter = QPainter(canvas)
    try:
        for screen in screens:
            shot = screen.grabWindow(0)
            geo = screen.geometry()
            painter.drawPixmap(geo.topLeft() - rect.topLeft(), shot)
    finally:
        painter.end()
    return canvas, rect, dpr


def crop_pixmap(pixmap: QPixmap, logical_rect: QRect, origin: QRect) -> QPixmap:
    """从虚拟桌面截图中裁出逻辑矩形区域（按 devicePixelRatio 换算到设备像素）。"""
    dpr = pixmap.devicePixelRatio() or 1.0
    rel = logical_rect.translated(-origin.topLeft())
    device_rect = QRect(
        round(rel.x() * dpr),
        round(rel.y() * dpr),
        max(1, round(rel.width() * dpr)),
        max(1, round(rel.height() * dpr)),
    )
    cropped = pixmap.copy(device_rect)
    cropped.setDevicePixelRatio(dpr)
    return cropped
