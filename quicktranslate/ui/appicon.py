"""应用图标：优先使用 resources/icons 下的文件，否则运行时绘制。

图标为蓝底圆角方块 + 居中的白色大写 Q。Q 用**矢量路径**手绘而非系统字体，
原因：打包分发到不同机器时字体不一定存在（实测离屏渲染环境字体数为 0，
字形会退化成方框），路径绘制在任何环境都能得到一致结果，且小尺寸下更锐利。
"""

from __future__ import annotations

import os

from PySide6.QtCore import QBuffer, QIODevice, QPointF, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPixmap,
)

from ..config import paths

_ACCENT = "#2F6FED"
SIZES = (16, 24, 32, 48, 64, 128, 256)
_cache: QIcon | None = None


def _q_path(size: float) -> QPainterPath:
    """在 size×size 的画布上构造大写 Q 的矢量轮廓（以 100×100 为设计栅格）。"""
    s = size / 100.0

    cx = 50.0 * s          # 圆心
    cy = 47.0 * s          # 圆心略微上移，给 Q 的尾部留出空间
    r = 27.0 * s           # 圆环外半径
    lw = 14.0 * s          # 笔画粗细

    path = QPainterPath()
    # 圆环：外圈顺时针
    path.addEllipse(QPointF(cx, cy), r, r)
    # 内圈逆时针（挖空），用反向的椭圆实现实心环
    hole = QPainterPath()
    hole.addEllipse(QPointF(cx, cy), r - lw, r - lw)
    path = path.subtracted(hole)

    # 尾部：从圆环右下约 4 点钟方向甩出一条短斜线
    p0 = QPointF(cx + r * 0.42, cy + r * 0.42)     # 起点（贴近圆环内侧）
    p1 = QPointF(cx + r * 1.06, cy + r * 1.06)     # 终点（伸出圆环外）

    pen = QPen(Qt.GlobalColor.white, lw, Qt.PenStyle.SolidLine)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    stroker = QPainterPathStroker(pen)

    spine = QPainterPath()
    spine.moveTo(p0)
    spine.lineTo(p1)
    tail_path = stroker.createStroke(spine)

    return path.united(tail_path)


def _draw(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # 底：蓝色圆角方块
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(_ACCENT))
    radius = size * 0.24
    painter.drawRoundedRect(1, 1, size - 2, size - 2, radius, radius)

    # 白色大写 Q（矢量路径，居中）
    path = _q_path(float(size))
    bounds = path.boundingRect()
    # 依据实际包围盒做居中校正，避免视觉偏移
    dx = (size - bounds.width()) / 2.0 - bounds.left()
    dy = (size - bounds.height()) / 2.0 - bounds.top()
    offset = QPainterPath()
    offset.addPath(path.translated(dx, dy))

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#FFFFFF"))
    painter.drawPath(offset)
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
    for size in SIZES:
        icon.addPixmap(_draw(size))
    _cache = icon
    return icon


def save_ico(path: str) -> bool:
    """导出多尺寸 .ico 供打包使用。

    多尺寸是必要的：Windows 在标题栏（16px）、任务栏（32px）、
    资源管理器大图标（256px）等场景会挑选最接近的尺寸。
    只放一张 256 会让小尺寸场景缩放出毛边。
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return _write_ico(path)
    except Exception:  # noqa: BLE001
        return False


def _write_ico(path: str) -> bool:
    """构造多帧 ICO 容器，每个目录项指向一段 PNG 数据。

    Qt 自带的 ICO 写入器只保存单一帧，所以这里直接拼 ICO 文件格式
    （Windows Vista 起支持用 PNG 压缩帧）。
    """
    import struct

    frames: list[tuple[int, bytes]] = []
    for size in SIZES:
        pixmap = _draw(size)
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(buffer, "PNG")
        data = bytes(buffer.data())
        buffer.close()
        if data:
            frames.append((size, data))

    if not frames:
        return False

    header = struct.pack("<HHH", 0, 1, len(frames))  # reserved, type=icon, count
    offset = 6 + 16 * len(frames)
    entries = bytearray()
    payload = bytearray()
    for size, data in frames:
        dim = 0 if size >= 256 else size   # ICO 用 0 表示 256
        entries += struct.pack(
            "<BBBBHHII",
            dim,          # width
            dim,          # height
            0,            # palette colors
            0,            # reserved
            1,            # color planes
            32,           # bits per pixel
            len(data),
            offset,
        )
        payload += data
        offset += len(data)

    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(bytes(entries))
        fh.write(bytes(payload))
    return True
