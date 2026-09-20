"""翻译历史窗口：搜索、收藏、复制、回填。"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..storage.history import HistoryItem, HistoryStore


class _HistoryRow(QWidget):
    """历史条目：星标 + 原文/译文摘要 + 时间。"""

    star_clicked = Signal(int)

    def __init__(self, item: HistoryItem, parent=None) -> None:
        super().__init__(parent)
        self._item = item
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(8)

        self.star = QToolButton()
        self.star.setText("★" if item.favorite else "☆")
        self.star.setToolTip("收藏 / 取消收藏")
        self.star.setStyleSheet(
            "color: #F0A020; font-size: 15px; border: none; background: transparent;"
        )
        self.star.clicked.connect(lambda: self.star_clicked.emit(item.id))
        lay.addWidget(self.star)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        src = QLabel(self._elide(item.src_text.replace("\n", " "), 70))
        src.setStyleSheet("font-weight: 600;")
        dst = QLabel(self._elide(item.dst_text.replace("\n", " "), 70))
        dst.setObjectName("Muted")
        texts.addWidget(src)
        texts.addWidget(dst)
        lay.addLayout(texts, 1)

        meta = QLabel(item.time_text)
        meta.setObjectName("Faint")
        meta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        lay.addWidget(meta)

    @staticmethod
    def _elide(text: str, limit: int) -> str:
        text = text.strip()
        return text if len(text) <= limit else text[:limit] + "…"


class HistoryWindow(QDialog):
    reuse_requested = Signal(str, str)  # 回填原文 / 译文

    def __init__(self, store: HistoryStore, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self.setWindowTitle("翻译历史")
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.resize(680, 520)
        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索原文或译文…")
        self.search_edit.textChanged.connect(lambda _: self.reload())
        bar.addWidget(self.search_edit, 1)

        self.fav_only = QCheckBox("只看收藏")
        self.fav_only.stateChanged.connect(lambda _: self.reload())
        bar.addWidget(self.fav_only)

        clear_btn = QPushButton("清空历史")
        clear_btn.setObjectName("Danger")
        clear_btn.clicked.connect(self._clear)
        bar.addWidget(clear_btn)
        lay.addLayout(bar)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("HistoryList")
        self.list_widget.setAlternatingRowColors(False)
        self.list_widget.setUniformItemSizes(False)
        self.list_widget.itemDoubleClicked.connect(self._on_activate)
        lay.addWidget(self.list_widget, 1)

        tip = QLabel("双击条目把原文回填到主窗口    ·    点击 ★ 收藏")
        tip.setObjectName("Faint")
        lay.addWidget(tip)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.reuse_btn = QPushButton("回填原文")
        self.reuse_btn.clicked.connect(lambda: self._emit_reuse(src_only=True))
        buttons.addWidget(self.reuse_btn)

        self.copy_btn = QPushButton("复制译文")
        self.copy_btn.setObjectName("Primary")
        self.copy_btn.clicked.connect(lambda: self._emit_reuse(src_only=False))
        buttons.addWidget(self.copy_btn)
        lay.addLayout(buttons)

    # ------------------------------------------------------------------ #
    def reload(self) -> None:
        self.list_widget.clear()
        items = self._store.query(
            keyword=self.search_edit.text(),
            only_favorite=self.fav_only.isChecked(),
        )
        if not items:
            placeholder = QListWidgetItem("暂无记录")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            placeholder.setSizeHint(QSize(0, 56))
            placeholder.setTextAlignment(
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
            )
            self.list_widget.addItem(placeholder)
            return
        for item in items:
            row = _HistoryRow(item)
            row.star_clicked.connect(self._toggle_star)
            entry = QListWidgetItem()
            # 必须让行控件先完成样式/字体测量，否则 sizeHint 偏小会把两行文字压重叠
            row.ensurePolished()
            row.adjustSize()
            entry.setSizeHint(row.sizeHint())
            entry.setData(Qt.ItemDataRole.UserRole, item)
            self.list_widget.addItem(entry)
            self.list_widget.setItemWidget(entry, row)

    def _current_item(self) -> HistoryItem | None:
        entry = self.list_widget.currentItem()
        if entry is None:
            return None
        return entry.data(Qt.ItemDataRole.UserRole)

    def _toggle_star(self, item_id: int) -> None:
        self._store.toggle_favorite(item_id)
        self.reload()

    def _on_activate(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if data is not None:
            self.reuse_requested.emit(data.src_text, data.dst_text)

    def _emit_reuse(self, src_only: bool) -> None:
        data = self._current_item()
        if data is None:
            return
        if src_only:
            self.reuse_requested.emit(data.src_text, data.dst_text)
        else:
            from PySide6.QtWidgets import QApplication

            QApplication.clipboard().setText(data.dst_text)

    def _clear(self) -> None:
        if self._store.count() == 0:
            return
        confirm = QMessageBox.question(
            self,
            "清空历史",
            "确定要删除全部翻译历史吗？此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self._store.clear()
            self.reload()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.reload()
