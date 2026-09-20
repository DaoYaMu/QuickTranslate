"""自定义控件。"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QFrame, QLabel, QPlainTextEdit, QSizePolicy


class StreamTextEdit(QPlainTextEdit):
    """支持流式追加的文本区。只读模式下仍可选中复制。"""

    def __init__(self, read_only: bool = False, placeholder: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(read_only)
        self.setPlaceholderText(placeholder)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setTabChangesFocus(True)
        font = self.font()
        font.setPointSizeF(max(10.0, font.pointSizeF()))
        self.setFont(font)

    # --- 流式写入 ---
    def begin_stream(self) -> None:
        self.clear()

    def append_chunk(self, text: str) -> None:
        if not text:
            return
        bar = self.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        cursor = self.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        self.setTextCursor(cursor)
        if at_bottom:
            bar.setValue(bar.maximum())

    def output_text(self) -> str:
        return self.toPlainText()


class ElidedLabel(QLabel):
    """过长时自动省略的标签（用于状态栏提示）。"""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def setFullText(self, text: str) -> None:  # noqa: N802
        self._full = text
        # 先原样显示，避免在布局完成前（宽度尚未确定）被裁成省略号；
        # 布局就绪后再按实际宽度做一次省略处理。
        super().setText(text)
        QTimer.singleShot(0, self._update_elide)

    def fullText(self) -> str:  # noqa: N802
        return self._full

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._update_elide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_elide()

    def _update_elide(self) -> None:
        width = self.width()
        if width <= 0:
            return
        metrics = QFontMetrics(self.font())
        elided = metrics.elidedText(self._full, Qt.TextElideMode.ElideRight, max(width - 4, 24))
        super().setText(elided)
        self.setToolTip(self._full)
