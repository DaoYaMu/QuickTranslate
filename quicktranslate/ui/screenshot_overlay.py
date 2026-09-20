"""全屏截图选区遮罩：覆盖所有显示器，拖拽框选，Esc / 右键取消。"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from ..core.capture import crop_pixmap
from ..utils.logging import get_logger

log = get_logger("overlay")

_MIN_SIZE = 4
_AUTO_CANCEL_MS = 5 * 60 * 1000  # 5 分钟无操作自动退出，防止遮罩卡死界面
_DIM = QColor(0, 0, 0, 120)
_ACCENT = QColor(47, 111, 237)
_HINT_BG = QColor(20, 22, 26, 210)
_HINT_FG = QColor(235, 238, 242)


class ScreenshotOverlay(QWidget):
    """冻结桌面 + 遮罩 + 框选。"""

    captured = Signal(QPixmap)
    cancelled = Signal()

    def __init__(self, frozen: QPixmap, desktop_rect: QRect, parent=None) -> None:
        super().__init__(None)
        self._frozen = frozen
        self._desktop = desktop_rect
        self._origin: QPoint | None = None
        self._current: QPoint | None = None
        self._finished = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setGeometry(desktop_rect)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # 兜底：遮罩永远不能变成"退不出去"的窗口。
        # 无边框置顶窗口一旦抢不到键盘焦点，Esc 就收不到；
        # 因此超时后强制退出，并且始终支持右键取消。
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(_AUTO_CANCEL_MS)
        self._timeout.timeout.connect(self._cancel)

    # ------------------------------------------------------------------ #
    def _selection(self) -> QRect:
        if self._origin is None or self._current is None:
            return QRect()
        return QRect(self._origin, self._current).normalized()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self._timeout.start()

    def _cancel(self) -> None:
        self._finished = True
        self._timeout.stop()
        self.hide()
        self.cancelled.emit()
        self.close()

    # ------------------------------------------------------------------ #
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # 冻结的桌面底图
        painter.drawPixmap(0, 0, self._frozen)
        # 整屏压暗
        painter.fillRect(self.rect(), _DIM)

        selection = self._selection()
        if not selection.isEmpty():
            # 选区还原为原始亮度
            painter.save()
            painter.setClipRect(selection)
            painter.drawPixmap(0, 0, self._frozen)
            painter.restore()

            pen = QPen(_ACCENT, 2)
            painter.setPen(pen)
            painter.drawRect(selection.adjusted(0, 0, -1, -1))

            self._draw_size_badge(painter, selection)

        self._draw_hint(painter)
        painter.end()

    def _draw_size_badge(self, painter: QPainter, selection: QRect) -> None:
        text = f"{selection.width()} × {selection.height()}"
        font = QFont()
        font.setPointSize(9)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        w = metrics.horizontalAdvance(text) + 16
        h = metrics.height() + 8

        x = min(selection.left(), self.width() - w - 4)
        y = selection.top() - h - 6
        if y < 4:
            y = min(selection.bottom() + 6, self.height() - h - 4)

        badge = QRect(x, y, w, h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_HINT_BG)
        painter.drawRoundedRect(badge, 4, 4)
        painter.setPen(_HINT_FG)
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_hint(self, painter: QPainter) -> None:
        text = "拖动鼠标框选要翻译的区域    ·    Esc / 右键 取消"
        font = QFont()
        font.setPointSize(10)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        w = metrics.horizontalAdvance(text) + 28
        h = metrics.height() + 14
        x = (self.width() - w) // 2
        badge = QRect(x, 36, w, h)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_HINT_BG)
        painter.drawRoundedRect(badge, 8, 8)
        painter.setPen(_HINT_FG)
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)

    # ------------------------------------------------------------------ #
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            self._cancel()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position().toPoint()
            self._current = self._origin
            self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._origin is not None:
            self._current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._origin is None:
            return
        self._current = event.position().toPoint()
        selection = self._selection()
        if selection.width() < _MIN_SIZE or selection.height() < _MIN_SIZE:
            # 视为误触，继续等待用户框选
            self._origin = None
            self._current = None
            self.update()
            return
        self._finish(selection)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # Esc 退出是主要方式；把回车也当成"退出"，
            # 万一 Esc 被输入法拦截也还有退路。
            self._cancel()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._timeout.stop()
        if not self._finished:
            self._finished = True
            self.cancelled.emit()
        super().closeEvent(event)

    # ------------------------------------------------------------------ #
    def _finish(self, selection: QRect) -> None:
        self._finished = True
        self._timeout.stop()
        try:
            cropped = crop_pixmap(self._frozen, selection, self._desktop)
        except Exception as exc:  # noqa: BLE001
            log.exception("裁剪选区失败")
            self.cancelled.emit()
            self.close()
            return
        self.hide()
        self.captured.emit(cropped)
        self.close()
