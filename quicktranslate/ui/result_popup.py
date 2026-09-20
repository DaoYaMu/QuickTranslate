"""浮层结果窗：用于截图 / 划词 / 剪贴板翻译的结果展示。"""

from __future__ import annotations

import time

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .widgets import StreamTextEdit


class _DragBar(QFrame):
    """可拖拽移动整窗的标题栏。"""

    def __init__(self, window: QWidget, parent=None) -> None:
        super().__init__(parent)
        self._window = window
        self._offset: QPoint | None = None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._offset = event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._window.move(event.globalPosition().toPoint() - self._offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._offset = None
        event.accept()


class ResultPopup(QWidget):
    """置顶浮层结果窗。"""

    closed = Signal()
    retry_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setObjectName("Root")
        self.resize(480, 340)
        self._source_visible = True
        self._t0 = 0.0
        self._content_chars = 0
        self._reasoning_chars = 0
        self._streaming = False
        self._model = ""
        self._timeout = 0.0
        self._using_fallback = False
        self._tick: QTimer | None = None
        self._build_ui()

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setObjectName("Card")
        outer.addWidget(card)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(8)

        bar = _DragBar(self)
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(0, 0, 0, 0)
        bar_lay.setSpacing(6)

        title = QLabel("翻译结果")
        title.setObjectName("Title")

        self.mode_label = QLabel("")
        self.mode_label.setObjectName("Faint")

        self.source_toggle = QToolButton()
        self.source_toggle.setText("原文 ▾")
        self.source_toggle.setToolTip("显示 / 隐藏识别出的原文")
        self.source_toggle.clicked.connect(self._toggle_source)

        close_btn = QToolButton()
        close_btn.setText("✕")
        close_btn.setToolTip("关闭")
        close_btn.clicked.connect(self.close)

        bar_lay.addWidget(title)
        bar_lay.addWidget(self.mode_label)
        bar_lay.addStretch(1)
        bar_lay.addWidget(self.source_toggle)
        bar_lay.addWidget(close_btn)
        lay.addWidget(bar)

        self.source_edit = StreamTextEdit(read_only=True, placeholder="未识别到文字")
        self.source_edit.setMaximumHeight(110)
        lay.addWidget(self.source_edit)

        self.output_edit = StreamTextEdit(read_only=True, placeholder="翻译中…")
        lay.addWidget(self.output_edit, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.status_label = QLabel("")
        self.status_label.setObjectName("Faint")
        bottom.addWidget(self.status_label, 1)

        copy_btn = QPushButton("复制译文")
        copy_btn.clicked.connect(self._copy)
        bottom.addWidget(copy_btn)

        retry_btn = QPushButton("重新翻译")
        retry_btn.clicked.connect(self.retry_requested.emit)
        bottom.addWidget(retry_btn)
        lay.addLayout(bottom)

    # ------------------------------------------------------------------ #
    def present(
        self,
        source_text: str,
        mode: str,
        source_lang: str = "",
        target_lang: str = "",
    ) -> None:
        self.mode_label.setText(mode)
        self.source_edit.setPlainText(source_text)
        self.output_edit.clear()
        self.status_label.setText("")
        self._set_source_visible(self._source_visible)
        self._place_near_top()
        self.show()
        self.raise_()
        self.activateWindow()

    def _place_near_top(self) -> None:
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        x = geo.left() + (geo.width() - self.width()) // 2
        y = geo.top() + max(60, int(geo.height() * 0.12))
        self.move(x, y)

    # ------------------------------------------------------------------ #
    def begin_stream(self, model: str = "", timeout: float = 0.0) -> None:
        self.output_edit.clear()
        self._model = model or ""
        self._timeout = timeout or 0.0
        self._using_fallback = False
        self.status_label.setText("已发送请求，等待模型响应…")
        self._t0 = time.monotonic()
        self._content_chars = 0
        self._reasoning_chars = 0
        self._streaming = True
        if self._tick is None:
            self._tick = QTimer(self)
            self._tick.setInterval(200)
            self._tick.timeout.connect(self._tick_progress)
        self._tick.start()
        self._tick_progress()

    def note_switch(self, model: str) -> None:
        """主模型没响应，改用备用模型：丢掉半截输出，重新开始计时。"""
        if not self._streaming:
            return
        self.output_edit.clear()
        self._model = model or ""
        self._using_fallback = True
        self._content_chars = 0
        self._reasoning_chars = 0
        self._t0 = time.monotonic()
        self._tick_progress()

    def note_reasoning(self, piece: str) -> None:
        if not self._streaming:
            return  # 已结束/已中断，忽略排队迟到的信号
        self._reasoning_chars += len(piece)
        self._tick_progress()

    def _tick_progress(self) -> None:
        if not self._streaming:
            return
        elapsed = time.monotonic() - self._t0 if self._t0 else 0.0
        if self._content_chars == 0 and self._reasoning_chars:
            text = f"模型思考中… 已思考 {self._reasoning_chars} 字 · {elapsed:.1f}s"
        elif self._content_chars:
            text = f"翻译中… 已输出 {self._content_chars} 字 · {elapsed:.1f}s"
        else:
            # 把模型名和超时上限摆出来：既能一眼看出是哪个模型不响应，
            # 也知道最多再等多久就会报错，不用干等。
            who = self._model.split("/")[-1] if self._model else "模型"
            if self._using_fallback:
                who = f"备用模型 {who}"
            text = f"等待 {who} 响应… {elapsed:.1f}s"
            if self._timeout and not self._using_fallback:
                text += f"（{self._timeout:.0f}s 超时）"
        self.status_label.setText(text)

    def append_chunk(self, chunk: str) -> None:
        self.output_edit.append_chunk(chunk)
        self._content_chars += len(chunk)
        self._tick_progress()

    def show_hint(self, mode: str, message: str, title: str = "") -> None:
        """不翻译，只把一段说明摆到浮层上（例如划词取不到文字时怎么办）。

        以前这类提示只走托盘气泡，系统一旦折叠通知用户就以为"按了没反应"。
        """
        keep_source = self._source_visible
        self.present("", mode=mode)
        self._set_source_visible(False)
        self.finish(False, message, title=title or "提示")
        self._source_visible = keep_source  # 不改动用户对「原文」栏的偏好

    def finish(self, ok: bool, message: str = "", title: str = "") -> None:
        self._streaming = False
        if self._tick is not None:
            self._tick.stop()
        if ok:
            self.status_label.setText(
                "完成（已使用备用模型）" if self._using_fallback else "完成"
            )
            return
        if not message:
            self.status_label.setText("已中断")
            return
        # 一句话的短提示（"已中断"、"未识别到文字…"）放状态栏就够；
        # 而超时之类的错误说明是多行的、带处理建议，挤进状态栏没法看，
        # 所以铺到结果区，状态栏只留一句。
        if "\n" not in message and len(message) <= 30:
            self.status_label.setText(title or message)
            return
        self.status_label.setText(title or "失败")
        self.output_edit.setPlainText(message)
        self.output_edit.setToolTip(message)

    def set_source_text(self, text: str) -> None:
        self.source_edit.setPlainText(text)

    def output_text(self) -> str:
        return self.output_edit.output_text()

    # ------------------------------------------------------------------ #
    def _toggle_source(self) -> None:
        self._set_source_visible(not self._source_visible)

    def _set_source_visible(self, visible: bool) -> None:
        self._source_visible = visible
        self.source_edit.setVisible(visible)
        self.source_toggle.setText("原文 ▾" if visible else "原文 ▸")
        self.adjustSize()

    def _copy(self) -> None:
        from PySide6.QtWidgets import QApplication

        text = self.output_text().strip()
        if text:
            QApplication.clipboard().setText(text)
            self.status_label.setText("已复制到剪贴板")

    def closeEvent(self, event) -> None:  # noqa: N802
        self.closed.emit()
        super().closeEvent(event)
