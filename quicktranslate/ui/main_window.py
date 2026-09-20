"""主窗口：词典式双栏翻译界面。"""

from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QTimer, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..config.settings import Settings
from ..core.translator import LANGUAGES, TARGET_LANGUAGES
from .widgets import ElidedLabel, StreamTextEdit

_STATUS_KEYS = {"info": "text_muted", "ok": "ok", "error": "danger", "busy": "accent"}


def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    return line


class MainWindow(QWidget):
    # 出站请求
    translate_requested = Signal(str, str, str)   # text, source, target
    cancel_requested = Signal()
    screenshot_requested = Signal()
    selection_requested = Signal()
    clipboard_requested = Signal()
    ocr_file_requested = Signal(str)
    settings_requested = Signal()
    history_requested = Signal()
    theme_changed = Signal(str)
    hidden_to_tray = Signal()

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Root")
        self._settings = settings
        self._theme = "light"
        self._status: tuple[str, str] = ("就绪", "info")
        self.hide_on_close = False
        self.hide_on_minimize = False
        self.setWindowTitle("QuickTranslate · AI 翻译")
        self.setMinimumSize(760, 540)
        self.resize(960, 660)

        # 进行中状态（用于思考进度与计时）
        self._busy = False
        self._content_chars = 0
        self._reasoning_chars = 0
        self._started_at = 0.0
        self._model = ""
        self._timeout = 0.0
        self._using_fallback = False
        self._tick = QTimer(self)
        self._tick.setInterval(200)
        self._tick.timeout.connect(self._render_progress)

        self._build_ui()
        self._load_state()

    # ------------------------------------------------------------------ #
    def closeEvent(self, event) -> None:  # noqa: N802
        if self.hide_on_close:
            self.save_state()
            event.ignore()
            self.hide()
            self.hidden_to_tray.emit()
            return
        self.save_state()
        super().closeEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        if (
            event.type() == QEvent.Type.WindowStateChange
            and self.isMinimized()
            and self.hide_on_minimize
        ):
            QTimer.singleShot(0, self.hide)
        super().changeEvent(event)

    def save_state(self) -> None:
        self._settings.set("ui/geometry", self.saveGeometry())

    # ------------------------------------------------------------------ #
    # 构建界面
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(10)

        root.addWidget(self._build_header())
        root.addWidget(self._build_lang_bar())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_input_panel())
        splitter.addWidget(self._build_output_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([480, 480])
        root.addWidget(splitter, 1)

        root.addWidget(self._build_bottom_bar())

        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._on_translate)
        QShortcut(QKeySequence("Ctrl+Enter"), self, activated=self._on_translate)

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("Header")
        lay = QHBoxLayout(header)
        lay.setContentsMargins(16, 10, 12, 10)
        lay.setSpacing(10)

        titles = QVBoxLayout()
        titles.setSpacing(1)
        title = QLabel("QuickTranslate")
        title.setObjectName("Title")
        subtitle = QLabel("AI 大模型翻译 · 截图取词 · 全局热键")
        subtitle.setObjectName("Faint")
        titles.addWidget(title)
        titles.addWidget(subtitle)

        lay.addLayout(titles)
        lay.addStretch(1)

        self.profile_btn = QToolButton()
        self.profile_btn.setToolTip("当前 API 档案 —— 点击进入设置")
        self.profile_btn.setText("未配置")
        self.profile_btn.clicked.connect(self.settings_requested.emit)
        lay.addWidget(self.profile_btn)

        self.theme_btn = QToolButton()
        self.theme_btn.setToolTip("切换浅色 / 深色主题")
        self.theme_btn.clicked.connect(self._toggle_theme)
        lay.addWidget(self.theme_btn)

        self.history_btn = QToolButton()
        self.history_btn.setText("历史")
        self.history_btn.setToolTip("翻译历史记录")
        self.history_btn.clicked.connect(self.history_requested.emit)
        lay.addWidget(self.history_btn)

        self.settings_btn = QToolButton()
        self.settings_btn.setText("设置")
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        lay.addWidget(self.settings_btn)

        return header

    def _build_lang_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("Card")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(8)

        self.src_combo = QComboBox()
        self.src_combo.addItems(LANGUAGES)
        self.src_combo.setMinimumWidth(110)
        self.src_combo.currentTextChanged.connect(self._on_lang_changed)

        self.swap_btn = QToolButton()
        self.swap_btn.setObjectName("SwapButton")
        self.swap_btn.setText("⇄")
        self.swap_btn.setToolTip("互换源语言 / 目标语言")
        self.swap_btn.clicked.connect(self._swap_languages)

        self.dst_combo = QComboBox()
        self.dst_combo.addItems(TARGET_LANGUAGES)
        self.dst_combo.setMinimumWidth(110)
        self.dst_combo.currentTextChanged.connect(self._on_lang_changed)

        lay.addWidget(QLabel("翻译方向"))
        lay.addSpacing(4)
        lay.addWidget(self.src_combo)
        lay.addWidget(self.swap_btn)
        lay.addWidget(self.dst_combo)
        lay.addStretch(1)

        hint = QLabel("截图翻译快捷键可在「设置」中修改")
        hint.setObjectName("Faint")
        lay.addWidget(hint)
        return bar

    def _build_input_panel(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(6)
        head.addWidget(self._panel_title("原文"))
        head.addStretch(1)

        self.count_label = QLabel("0 字")
        self.count_label.setObjectName("Faint")
        head.addWidget(self.count_label)

        for text, tip, slot in (
            ("粘贴", "从剪贴板粘贴", self._paste),
            ("打开图片", "选择本地图片进行 OCR 翻译", self._pick_image),
            ("清空", "清空原文", self._clear_input),
        ):
            btn = QToolButton()
            btn.setText(text)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            head.addWidget(btn)
        lay.addLayout(head)

        self.input_edit = StreamTextEdit(
            read_only=False, placeholder="在此输入或粘贴文本…（Ctrl+Enter 翻译）"
        )
        self.input_edit.textChanged.connect(self._update_count)
        lay.addWidget(self.input_edit, 1)
        return card

    def _build_output_panel(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(6)
        head.addWidget(self._panel_title("译文"))
        head.addStretch(1)

        self.copy_btn = QToolButton()
        self.copy_btn.setText("复制")
        self.copy_btn.setToolTip("复制译文")
        self.copy_btn.clicked.connect(self._copy_output)
        head.addWidget(self.copy_btn)

        self.replace_btn = QToolButton()
        self.replace_btn.setText("替换原文")
        self.replace_btn.setToolTip("把译文放到原文框继续翻译")
        self.replace_btn.clicked.connect(self._move_output_to_input)
        head.addWidget(self.replace_btn)

        clear_btn = QToolButton()
        clear_btn.setText("清空")
        clear_btn.clicked.connect(lambda: self.output_edit.clear())
        head.addWidget(clear_btn)
        lay.addLayout(head)

        self.output_edit = StreamTextEdit(
            read_only=True, placeholder="译文将显示在这里…"
        )
        lay.addWidget(self.output_edit, 1)
        return card

    def _build_bottom_bar(self) -> QWidget:
        bar = QWidget()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(8)

        self.translate_btn = QPushButton("翻译")
        self.translate_btn.setObjectName("Primary")
        self.translate_btn.setToolTip("翻译原文（Ctrl+Enter）")
        self.translate_btn.clicked.connect(self._on_translate)
        lay.addWidget(self.translate_btn)

        self.screenshot_btn = QPushButton("截图翻译")
        self.screenshot_btn.setToolTip("框选屏幕区域并翻译")
        self.screenshot_btn.clicked.connect(self.screenshot_requested.emit)
        lay.addWidget(self.screenshot_btn)

        self.selection_btn = QPushButton("划词翻译")
        self.selection_btn.setToolTip("翻译当前选中的文本")
        self.selection_btn.clicked.connect(self.selection_requested.emit)
        lay.addWidget(self.selection_btn)

        self.clipboard_btn = QPushButton("剪贴板")
        self.clipboard_btn.setToolTip("翻译剪贴板内容")
        self.clipboard_btn.clicked.connect(self.clipboard_requested.emit)
        lay.addWidget(self.clipboard_btn)

        self.status_label = ElidedLabel("就绪")
        self.status_label.setObjectName("Muted")
        self.status_label.setMinimumWidth(160)
        self.status_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        lay.addWidget(self.status_label, 1)
        return bar

    @staticmethod
    def _panel_title(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("PanelTitle")
        return label

    # ------------------------------------------------------------------ #
    # 状态与数据
    # ------------------------------------------------------------------ #
    def _load_state(self) -> None:
        src = str(self._settings.get("translate/source", "自动"))
        dst = str(self._settings.get("translate/target", "中文"))
        if src in LANGUAGES:
            self.src_combo.setCurrentText(src)
        if dst in TARGET_LANGUAGES:
            self.dst_combo.setCurrentText(dst)
        self.apply_theme(self._settings.theme)
        geometry = self._settings.get("ui/geometry")
        if geometry:
            self.restoreGeometry(geometry)

    def apply_theme(self, theme: str) -> None:
        from PySide6.QtWidgets import QApplication

        from . import theme as theme_mod

        QApplication.instance().setStyleSheet(theme_mod.build_qss(theme))
        self._theme = theme if theme in theme_mod.THEMES else "light"
        self.theme_btn.setText("☾ 深色" if theme == "light" else "☀ 浅色")
        self.theme_btn.setToolTip("切换到深色主题" if theme == "light" else "切换到浅色主题")
        self._apply_status_color()

    def current_source(self) -> str:
        return self.src_combo.currentText()

    def current_target(self) -> str:
        return self.dst_combo.currentText()

    def input_text(self) -> str:
        return self.input_edit.toPlainText()

    def set_input_text(self, text: str) -> None:
        self.input_edit.setPlainText(text)

    def set_profile_name(self, name: str) -> None:
        self.profile_btn.setText(f"⚙ {name}" if name else "未配置")

    def reload_from_settings(self) -> None:
        src = str(self._settings.get("translate/source", "自动"))
        dst = str(self._settings.get("translate/target", "中文"))
        if src in LANGUAGES:
            self.src_combo.setCurrentText(src)
        if dst in TARGET_LANGUAGES:
            self.dst_combo.setCurrentText(dst)

    def selected_input_text(self) -> str:
        cursor = self.input_edit.textCursor()
        if cursor.hasSelection():
            return cursor.selectedText().replace("\u2029", "\n")
        return ""

    def focus_input(self) -> None:
        self.input_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    # --- 输出区流式控制 ---
    def begin_output(self) -> None:
        self.output_edit.clear()
        self.output_edit.begin_stream()

    def append_output(self, chunk: str) -> None:
        self.output_edit.append_chunk(chunk)
        self._content_chars += len(chunk)
        if self._busy:
            self._render_progress()

    def note_reasoning(self, piece: str) -> None:
        """记录模型思考链长度（不显示内容，只反映"还在干活"）。"""
        self._reasoning_chars += len(piece)
        if self._busy:
            self._render_progress()

    def output_text(self) -> str:
        return self.output_edit.output_text()

    def set_busy(self, busy: bool, model: str = "", timeout: float = 0.0) -> None:
        self._busy = busy
        if model:
            self._model = model
        if timeout:
            self._timeout = timeout
        self.translate_btn.setText("停止" if busy else "翻译")
        self.translate_btn.setToolTip(
            "停止当前翻译" if busy else "翻译原文（Ctrl+Enter）"
        )
        for btn in (self.screenshot_btn, self.selection_btn, self.clipboard_btn):
            btn.setEnabled(not busy)
        if busy:
            self._content_chars = 0
            self._reasoning_chars = 0
            self._using_fallback = False
            self._started_at = time.monotonic()
            self._render_progress()
            self._tick.start()
        else:
            self._tick.stop()

    def note_switch(self, model: str) -> None:
        """主模型没响应，改用备用模型：输出清空，重新计时。"""
        self._model = model or self._model
        self._using_fallback = True
        self._content_chars = 0
        self._reasoning_chars = 0
        self._started_at = time.monotonic()
        self.output_edit.clear()
        if self._busy:
            self._render_progress()

    def _render_progress(self) -> None:
        """把"思考中 / 翻译中 + 计时"写进状态栏，避免看起来像卡死。"""
        elapsed = time.monotonic() - self._started_at if self._started_at else 0.0
        if self._content_chars == 0 and self._reasoning_chars:
            # 思考型模型：正文还没出来，先告诉用户它在思考
            text = f"模型思考中… 已思考 {self._reasoning_chars} 字 · {elapsed:.1f}s"
        elif self._content_chars:
            text = f"翻译中… 已输出 {self._content_chars} 字 · {elapsed:.1f}s"
        else:
            # 带上模型名与超时上限：一眼看出是哪个模型不响应，也知道还要等多久
            who = self._model.split("/")[-1] if self._model else "模型"
            if self._using_fallback:
                who = f"备用模型 {who}"
            text = f"等待 {who} 响应… {elapsed:.1f}s"
            if self._timeout and not self._using_fallback:
                text += f"（{self._timeout:.0f}s 超时）"
        self.set_status(text, "busy")

    def show_error(self, message: str) -> None:
        """把详细错误铺在结果区（状态栏一行放不下），并给一句简短状态。"""
        self.set_busy(False)  # 复位按钮/计时器
        self.output_edit.setPlainText(message)
        self.set_status("翻译失败，详见上方说明", "error")

    def note_success(self) -> None:
        self.set_busy(False)
        self.set_status(
            "翻译完成（已使用备用模型）" if self._using_fallback else "翻译完成", "ok"
        )

    def set_status(self, text: str, kind: str = "info") -> None:
        self._status = (text, kind)
        self.status_label.setFullText(text)
        self._apply_status_color()

    def _apply_status_color(self) -> None:
        from . import theme as theme_mod  # 局部导入避免循环依赖

        key = _STATUS_KEYS.get(self._status[1], "text_muted")
        color = theme_mod.palette(self._theme)[key]
        self.status_label.setStyleSheet(f"color: {color};")

    # ------------------------------------------------------------------ #
    # 交互槽
    # ------------------------------------------------------------------ #
    def _update_count(self) -> None:
        self.count_label.setText(f"{len(self.input_edit.toPlainText())} 字")

    def _on_translate(self) -> None:
        # 翻译进行中时，这个按钮变成"停止"
        if self._busy:
            self.cancel_requested.emit()
            return
        text = self.input_text().strip()
        if not text:
            self.set_status("请先输入需要翻译的文本", "error")
            return
        self.translate_requested.emit(text, self.current_source(), self.current_target())

    def _on_lang_changed(self) -> None:
        self._settings.set("translate/source", self.current_source())
        self._settings.set("translate/target", self.current_target())

    def _swap_languages(self) -> None:
        src, dst = self.current_source(), self.current_target()
        if src == "自动":
            # 目标是"自动"不可用，改为互换为：把当前目标作为源
            self.src_combo.setCurrentText(dst)
            self.dst_combo.setCurrentText("中文" if dst != "中文" else "英语")
        else:
            self.src_combo.setCurrentText(dst)
            self.dst_combo.setCurrentText(src)
        self._on_lang_changed()

    def _toggle_theme(self) -> None:
        new_theme = "dark" if self._settings.theme == "light" else "light"
        self._settings.theme = new_theme
        self.apply_theme(new_theme)
        self.theme_changed.emit(new_theme)

    def _paste(self) -> None:
        from ..core.clipboard import get_clipboard_text

        text = get_clipboard_text()
        if text:
            self.input_edit.setPlainText(text)
            self.set_status("已从剪贴板粘贴", "ok")
        else:
            self.set_status("剪贴板为空", "error")

    def _clear_input(self) -> None:
        self.input_edit.clear()
        self.set_status("就绪")

    def _pick_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择图片",
            "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp);;所有文件 (*)",
        )
        if path:
            self.ocr_file_requested.emit(path)

    def _copy_output(self) -> None:
        from PySide6.QtWidgets import QApplication

        text = self.output_text()
        if not text.strip():
            self.set_status("没有可复制的译文", "error")
            return
        QApplication.clipboard().setText(text)
        self.set_status("译文已复制到剪贴板", "ok")

    def _move_output_to_input(self) -> None:
        text = self.output_text().strip()
        if not text:
            return
        self.set_input_text(text)
        self._swap_languages()
