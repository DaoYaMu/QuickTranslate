"""设置对话框：API 档案、热键、常规、OCR。"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..config.profiles import DEFAULT_PROFILE, NO_THINKING_BODY, ProfileStore
from ..config.settings import Settings
from ..core import autostart
from ..core.ocr import OCR_LANGUAGES
from ..core.translator import (
    DEFAULT_TIMEOUT,
    LANGUAGES,
    TARGET_LANGUAGES,
    looks_like_thinking_model,
    parse_extra_body,
)
from ..utils.logging import get_logger
from ..workers.translate_worker import ConnectionTestWorker
from .hotkey_edit import HotkeyEdit

log = get_logger("settings_dialog")

_THEME_LABELS = {"light": "浅色", "dark": "深色"}
_THEME_VALUES = {v: k for k, v in _THEME_LABELS.items()}


class SettingsDialog(QDialog):
    theme_changed = Signal(str)
    hotkeys_changed = Signal()
    #: 热键录入开始 / 结束。True = 请暂停全局热键，否则用户按下的
    #: 组合键会真的触发功能（截图遮罩会盖住设置窗口，看起来像"卡死"）。
    hotkey_recording = Signal(bool)

    def __init__(self, settings: Settings, store: ProfileStore, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._store = store
        self._editing_name: str | None = None
        self._test_worker: ConnectionTestWorker | None = None

        self.setWindowTitle("设置")
        self.resize(780, 680)
        self._build_ui()
        self._load_general()
        self._load_profiles()
        self._load_hotkeys()
        self._load_ocr()

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 12)
        lay.setSpacing(10)

        tabs = QTabWidget()
        tabs.addTab(self._tab_general(), "常规")
        tabs.addTab(self._tab_profiles(), "API 与模型")
        tabs.addTab(self._tab_hotkeys(), "快捷键")
        tabs.addTab(self._tab_ocr(), "截图与 OCR")
        lay.addWidget(tabs, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    # ------------------------------------------------------------------ #
    def _tab_general(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 16, 14, 14)
        lay.setSpacing(12)

        box = QGroupBox("外观")
        form = QFormLayout(box)
        form.setSpacing(10)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(list(_THEME_LABELS.values()))
        form.addRow("主题", self.theme_combo)
        lay.addWidget(box)

        box2 = QGroupBox("启动与托盘")
        form2 = QFormLayout(box2)
        form2.setSpacing(10)
        self.autostart_check = QCheckBox("开机时自动启动")
        if not autostart.is_supported():
            self.autostart_check.setEnabled(False)
            self.autostart_check.setToolTip("当前平台不支持自动配置")
        form2.addRow(self.autostart_check)

        self.close_to_tray_check = QCheckBox("关闭主窗口时缩到系统托盘（推荐）")
        form2.addRow(self.close_to_tray_check)

        self.minimize_to_tray_check = QCheckBox("最小化主窗口时缩到系统托盘")
        form2.addRow(self.minimize_to_tray_check)
        lay.addWidget(box2)

        lay.addStretch(1)
        return page

    # ------------------------------------------------------------------ #
    def _tab_profiles(self) -> QWidget:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(14, 16, 14, 14)
        lay.setSpacing(12)

        # 左：档案列表
        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(QLabel("配置档案"))
        self.profile_list = QListWidget()
        self.profile_list.setFixedWidth(170)
        self.profile_list.currentTextChanged.connect(self._on_profile_selected)
        left.addWidget(self.profile_list, 1)

        row = QHBoxLayout()
        add_btn = QPushButton("新增")
        add_btn.clicked.connect(self._add_profile)
        del_btn = QPushButton("删除")
        del_btn.setObjectName("Danger")
        del_btn.clicked.connect(self._delete_profile)
        row.addWidget(add_btn)
        row.addWidget(del_btn)
        left.addLayout(row)
        lay.addLayout(left)

        # 右：表单
        form_box = QGroupBox("档案详情")
        form = QFormLayout(form_box)
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.name_edit = QLineEdit()
        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("https://api.openai.com/v1")
        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("gpt-4o-mini")

        # 备用模型：主模型超时 / 连不上 / 被下架时自动改用它重试。
        # 这是"一直翻译中"最实际的兜底 —— 服务商某个模型挂了不至于让工具瘫痪。
        self.fallback_edit = QLineEdit()
        self.fallback_edit.setPlaceholderText("可选，例如 Qwen/Qwen3-8B")
        self.fallback_edit.setToolTip(
            "留空 = 不启用。\n"
            "主模型「超时 / 连不上 / 模型不存在 / 服务端 5xx」时，会自动改用这个模型再翻一次。\n"
            "API Key、Base URL、超时等其余设置沿用当前档案。\n"
            "注意：Key 无效、权限不足这类错误不会切备用模型（换了也一样会失败）。"
        )

        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 2.0)
        self.temp_spin.setSingleStep(0.1)
        self.temp_spin.setValue(0.3)

        key_row = QWidget()
        key_lay = QHBoxLayout(key_row)
        key_lay.setContentsMargins(0, 0, 0, 0)
        key_lay.setSpacing(6)
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("sk-…")
        self.reveal_btn = QToolButton()
        self.reveal_btn.setText("显示")
        self.reveal_btn.setCheckable(True)
        self.reveal_btn.toggled.connect(self._toggle_reveal)
        key_lay.addWidget(self.api_key_edit, 1)
        key_lay.addWidget(self.reveal_btn)

        self.headers_edit = QPlainTextEdit()
        self.headers_edit.setPlaceholderText('可选，JSON 或每行 "Key: Value"')
        self.headers_edit.setFixedHeight(64)

        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText("系统提示词：定义模型作为翻译引擎的行为")
        self.prompt_edit.setFixedHeight(96)

        # 思考型模型（Qwen3 / DeepSeek-R1 …）翻译前会先"想"几十秒，
        # 关掉思考链能直接从 60s+ 降到几秒，且译文质量基本无损。
        self.no_thinking_check = QCheckBox("关闭模型思考链（Qwen3 / DeepSeek-R1 等，显著提速）")
        self.no_thinking_check.setToolTip(
            "等价于发送 {\"enable_thinking\": false}。\n"
            "翻译任务不需要推理，关闭后首个译文通常在 1~3 秒内出现。"
        )
        self.no_thinking_check.toggled.connect(self._on_no_thinking_toggled)

        self.extra_body_edit = QPlainTextEdit()
        self.extra_body_edit.setPlaceholderText(
            '可选，JSON 对象，会合并进请求体。例如 {"enable_thinking": false}'
        )
        self.extra_body_edit.setFixedHeight(64)

        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(10.0, 1800.0)
        self.timeout_spin.setSingleStep(10.0)
        self.timeout_spin.setDecimals(0)
        self.timeout_spin.setSuffix(" 秒")
        self.timeout_spin.setValue(DEFAULT_TIMEOUT)
        self.timeout_spin.setToolTip(
            "等待模型开始回话的最长时间（不是整次请求的总时长）。\n"
            "关掉思考链后健康模型 1~3 秒就出字，90 秒足够宽松。\n"
            "填得过大只会有个副作用：当某个模型在服务端排队或故障时，\n"
            "界面会长时间停在「翻译中」才报错。"
        )

        form.addRow("名称", self.name_edit)
        form.addRow("Base URL", self.base_url_edit)
        form.addRow("模型", self.model_edit)
        form.addRow("备用模型", self.fallback_edit)
        form.addRow("温度", self.temp_spin)
        form.addRow("API Key", key_row)
        form.addRow("自定义请求头", self.headers_edit)
        form.addRow("系统提示词", self.prompt_edit)
        form.addRow("", self.no_thinking_check)
        form.addRow("额外请求参数", self.extra_body_edit)
        form.addRow("响应超时", self.timeout_spin)

        actions = QHBoxLayout()
        save_btn = QPushButton("保存当前档案")
        save_btn.setObjectName("Primary")
        save_btn.clicked.connect(self._save_current_profile)
        self.test_btn = QPushButton("测试连接")
        self.test_btn.clicked.connect(self._test_connection)
        actions.addWidget(save_btn)
        actions.addWidget(self.test_btn)
        actions.addStretch(1)
        form.addRow("", self._wrap(actions))

        self.test_status = QLabel("")
        self.test_status.setObjectName("Faint")
        self.test_status.setWordWrap(True)
        form.addRow("", self.test_status)

        if not self._store.secure_storage:
            warn = QLabel("提示：系统凭据库不可用，API Key 将以混淆方式保存在本地文件。")
            warn.setObjectName("Faint")
            warn.setWordWrap(True)
            form.addRow("", warn)

        lay.addWidget(form_box, 1)
        return page

    @staticmethod
    def _wrap(layout) -> QWidget:  # type: ignore[no-untyped-def]
        holder = QWidget()
        holder.setLayout(layout)
        return holder

    # ------------------------------------------------------------------ #
    def _tab_hotkeys(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 16, 14, 14)
        lay.setSpacing(12)

        self.hotkeys_check = QCheckBox("启用全局快捷键（在其他程序中也能触发）")
        lay.addWidget(self.hotkeys_check)

        box = QGroupBox("快捷键")
        form = QFormLayout(box)
        form.setSpacing(10)
        self.hk_screenshot = HotkeyEdit()
        self.hk_selection = HotkeyEdit()
        self.hk_clipboard = HotkeyEdit()
        for edit in (self.hk_screenshot, self.hk_selection, self.hk_clipboard):
            edit.recording_changed.connect(self.hotkey_recording)
        form.addRow("截图翻译", self.hk_screenshot)
        form.addRow("划词翻译", self.hk_selection)
        form.addRow("剪贴板翻译", self.hk_clipboard)
        lay.addWidget(box)

        tip = QLabel(
            "点击输入框后按下组合键即可录入。\n"
            "· 必须带修饰键（Ctrl / Alt / Shift），例如 Ctrl+Shift+S —— \n"
            "  单个字母不会被接受，否则会在任何程序里抢走这个按键。\n"
            "· 录入状态下按 Esc 取消、按 Delete 清空。\n"
            "· 录入时全局热键会临时暂停，不会中途弹出截图窗口。\n"
            "· macOS 请在「系统设置 → 隐私与安全性 → 辅助功能」中授权本应用。"
        )
        tip.setObjectName("Faint")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        lay.addStretch(1)
        return page

    # ------------------------------------------------------------------ #
    def _tab_ocr(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 16, 14, 14)
        lay.setSpacing(12)

        box = QGroupBox("截图识别")
        form = QFormLayout(box)
        form.setSpacing(10)
        self.ocr_check = QCheckBox("启用截图 / 图片 OCR 翻译")
        form.addRow(self.ocr_check)

        self.ocr_lang_combo = QComboBox()
        for code, label in OCR_LANGUAGES.items():
            self.ocr_lang_combo.addItem(label, code)
        self.ocr_lang_combo.setToolTip(
            "截图里主要是中文就选「中英」，纯日文选「日文」。\n"
            "「中英」模式也能正确识别英文的词间空格。"
        )
        form.addRow("识别语言", self.ocr_lang_combo)

        self.default_src_combo = QComboBox()
        self.default_src_combo.addItems(LANGUAGES)
        self.default_dst_combo = QComboBox()
        self.default_dst_combo.addItems(TARGET_LANGUAGES)
        form.addRow("默认源语言", self.default_src_combo)
        form.addRow("默认目标语言", self.default_dst_combo)
        lay.addWidget(box)

        tip = QLabel(
            "· OCR 首次使用需要加载模型，可能等待几秒；之后识别速度很快。\n"
            "· 中文模型同时支持英文；日文请切换到对应识别语言。"
        )
        tip.setObjectName("Faint")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        lay.addStretch(1)
        return page

    # ------------------------------------------------------------------ #
    # 载入
    # ------------------------------------------------------------------ #
    def _load_general(self) -> None:
        self.theme_combo.setCurrentText(_THEME_LABELS.get(self._settings.theme, "浅色"))
        self.autostart_check.setChecked(autostart.is_enabled())
        self.close_to_tray_check.setChecked(self._settings.close_to_tray)
        self.minimize_to_tray_check.setChecked(self._settings.minimize_to_tray)

    def _load_profiles(self) -> None:
        self.profile_list.blockSignals(True)
        self.profile_list.clear()
        self.profile_list.addItems(self._store.names())
        current = str(self._settings.get("profile/current", self._store.first_name()))
        names = self._store.names()
        index = names.index(current) if current in names else 0
        self.profile_list.setCurrentRow(index)
        self.profile_list.blockSignals(False)
        if names:
            self._load_profile_form(names[index])

    def _load_profile_form(self, name: str) -> None:
        profile = self._store.get(name)
        if profile is None:
            return
        self._editing_name = name
        model = str(profile.get("model", ""))
        self.name_edit.setText(str(profile.get("name", "")))
        self.base_url_edit.setText(str(profile.get("base_url", "")))
        self.model_edit.setText(model)
        self.fallback_edit.setText(str(profile.get("fallback_model", "") or ""))
        self.temp_spin.setValue(float(profile.get("temperature", 0.3)))
        self.headers_edit.setPlainText(str(profile.get("custom_headers", "")))
        self.prompt_edit.setPlainText(str(profile.get("system_prompt", DEFAULT_PROFILE["system_prompt"])))

        extra = str(profile.get("extra_body", "") or "")
        self.extra_body_edit.setPlainText(extra)
        try:
            timeout = float(profile.get("timeout", DEFAULT_TIMEOUT))
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT
        self.timeout_spin.setValue(timeout if timeout > 0 else DEFAULT_TIMEOUT)

        try:
            body = parse_extra_body(extra)
        except ValueError:
            body = {}
        self.no_thinking_check.blockSignals(True)
        self.no_thinking_check.setChecked(body.get("enable_thinking") is False)
        self.no_thinking_check.blockSignals(False)

        self.api_key_edit.setText(self._store.api_key(name))
        self.test_status.setText("")
        # 思考型模型且尚未配置过：直接替用户勾上并填好参数（保存后生效）
        if not extra and looks_like_thinking_model(model):
            self.no_thinking_check.setChecked(True)
            self.test_status.setText(
                "检测到思考型模型：已默认勾选「关闭模型思考链」。"
                "翻译无需推理，勾选后首个译文通常 1~3 秒出现 —— 点「保存当前档案」生效。"
            )

    def _load_hotkeys(self) -> None:
        self.hotkeys_check.setChecked(self._settings.hotkeys_enabled)
        for edit, action in (
            (self.hk_screenshot, "screenshot"),
            (self.hk_selection, "selection"),
            (self.hk_clipboard, "clipboard"),
        ):
            stored = self._settings.hotkey(action)
            edit.set_sequence(self._sanitize_hotkey(stored))

    @staticmethod
    def _sanitize_hotkey(sequence: str) -> str:
        """清掉历史版本写坏的热键。

        旧版用 ``QKeySequenceEdit``，会把 ``Ctrl+Shift+S`` 存成裸 ``S``；
        这种热键注册出去等于抢走全系统的这个按键，直接丢弃更安全。
        """
        text = (sequence or "").strip()
        if not text:
            return ""
        if "+" not in text and not text.startswith("<"):
            log.warning("丢弃无效的全局热键（缺少修饰键）：%s", text)
            return ""
        return text

    def _load_ocr(self) -> None:
        self.ocr_check.setChecked(self._settings.ocr_enabled)
        code = self._settings.ocr_lang
        index = self.ocr_lang_combo.findData(code)
        if index >= 0:
            self.ocr_lang_combo.setCurrentIndex(index)
        self.default_src_combo.setCurrentText(str(self._settings.get("translate/source", "自动")))
        self.default_dst_combo.setCurrentText(str(self._settings.get("translate/target", "中文")))

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _toggle_reveal(self, checked: bool) -> None:
        self.api_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
        self.reveal_btn.setText("隐藏" if checked else "显示")

    def _on_profile_selected(self, name: str) -> None:
        if name:
            self._load_profile_form(name)
            self._settings.set("profile/current", name)

    def _on_no_thinking_toggled(self, checked: bool) -> None:
        """勾选/取消「关闭思考链」时，同步维护额外请求参数里的 enable_thinking。"""
        text = self.extra_body_edit.toPlainText().strip()
        try:
            obj = parse_extra_body(text) if text else {}
        except ValueError:
            obj = {}  # 用户手写的内容非法时，以复选框为准整体重写
        if checked:
            obj["enable_thinking"] = False
        else:
            obj.pop("enable_thinking", None)
        self.extra_body_edit.setPlainText(
            json.dumps(obj, ensure_ascii=False) if obj else ""
        )

    def _collect_form(self) -> dict:
        return {
            "name": self.name_edit.text().strip() or "未命名",
            "base_url": self.base_url_edit.text().strip(),
            "model": self.model_edit.text().strip(),
            "fallback_model": self.fallback_edit.text().strip(),
            "temperature": self.temp_spin.value(),
            "custom_headers": self.headers_edit.toPlainText().strip(),
            "system_prompt": self.prompt_edit.toPlainText().strip(),
            "extra_body": self.extra_body_edit.toPlainText().strip(),
            "timeout": self.timeout_spin.value(),
        }

    def _save_current_profile(self) -> None:
        data = self._collect_form()
        try:
            parse_extra_body(data["extra_body"])
        except ValueError as exc:
            QMessageBox.warning(self, "额外请求参数", str(exc))
            return
        self._store.upsert(data, original_name=self._editing_name)
        self._store.set_api_key(data["name"], self.api_key_edit.text())
        self._editing_name = data["name"]
        self._settings.set("profile/current", data["name"])

        self.profile_list.blockSignals(True)
        self.profile_list.clear()
        self.profile_list.addItems(self._store.names())
        names = self._store.names()
        self.profile_list.setCurrentRow(names.index(data["name"]) if data["name"] in names else 0)
        self.profile_list.blockSignals(False)

        self.test_status.setObjectName("Ok")
        self.test_status.setStyleSheet("color: #15803D;")
        self.test_status.setText("档案已保存")

    def _add_profile(self) -> None:
        base = "新建档案"
        name = base
        idx = 2
        existing = set(self._store.names())
        while name in existing:
            name = f"{base} {idx}"
            idx += 1
        profile = dict(DEFAULT_PROFILE)
        profile["name"] = name
        self._store.upsert(profile)
        self._load_profiles_hard()
        self._load_profile_form(name)

    def _load_profiles_hard(self) -> None:
        names = self._store.names()
        self.profile_list.blockSignals(True)
        self.profile_list.clear()
        self.profile_list.addItems(names)
        self.profile_list.blockSignals(False)

    def _delete_profile(self) -> None:
        name = self.profile_list.currentItem().text() if self.profile_list.currentItem() else ""
        if not name:
            return
        if len(self._store.names()) <= 1:
            QMessageBox.information(self, "无法删除", "至少需要保留一个配置档案。")
            return
        confirm = QMessageBox.question(
            self,
            "删除档案",
            f"确定删除配置档案「{name}」吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._store.remove(name)
        self._load_profiles_hard()
        remaining = self._store.names()
        if remaining:
            self.profile_list.setCurrentRow(0)
            self._load_profile_form(remaining[0])
            self._settings.set("profile/current", remaining[0])

    def _test_connection(self) -> None:
        from ..core.translator import create_translator

        data = self._collect_form()
        if not data["model"]:
            self.test_status.setStyleSheet("color: #DC2626;")
            self.test_status.setText("请先填写模型名称")
            return
        try:
            translator = create_translator(data, self.api_key_edit.text())
        except Exception as exc:  # noqa: BLE001
            self.test_status.setStyleSheet("color: #DC2626;")
            self.test_status.setText(f"配置有误：{exc}")
            return

        self.test_btn.setEnabled(False)
        self.test_status.setStyleSheet("color: #2F6FED;")
        self.test_status.setText("正在测试连接…")

        worker = ConnectionTestWorker(translator, self)
        worker.succeeded.connect(self._on_test_ok)
        worker.failed.connect(self._on_test_fail)
        worker.finished.connect(lambda: self.test_btn.setEnabled(True))
        self._test_worker = worker
        worker.start()

    def _on_test_ok(self, reply: str) -> None:
        self.test_status.setStyleSheet("color: #15803D;")
        self.test_status.setText(f"连接成功，模型回复：{reply[:120]}")

    def _on_test_fail(self, message: str) -> None:
        self.test_status.setStyleSheet("color: #DC2626;")
        self.test_status.setText(f"连接失败：{message[:300]}")

    # ------------------------------------------------------------------ #
    def _on_accept(self) -> None:
        # 保存当前档案
        if self.name_edit.text().strip():
            self._save_current_profile()

        new_theme = _THEME_VALUES.get(self.theme_combo.currentText(), "light")
        theme_changed = new_theme != self._settings.theme
        self._settings.theme = new_theme

        if autostart.is_supported():
            want = self.autostart_check.isChecked()
            if want != autostart.is_enabled():
                if not autostart.set_enabled(want):
                    QMessageBox.warning(self, "开机自启", "设置开机自启失败，请检查系统权限。")
            self._settings.autostart = want

        self._settings.close_to_tray = self.close_to_tray_check.isChecked()
        self._settings.minimize_to_tray = self.minimize_to_tray_check.isChecked()

        self._settings.hotkeys_enabled = self.hotkeys_check.isChecked()
        for edit, action in (
            (self.hk_screenshot, "screenshot"),
            (self.hk_selection, "selection"),
            (self.hk_clipboard, "clipboard"),
        ):
            self._settings.set_hotkey(action, edit.sequence())

        self._settings.ocr_enabled = self.ocr_check.isChecked()
        self._settings.ocr_lang = self.ocr_lang_combo.currentData() or "ch"
        self._settings.set("translate/source", self.default_src_combo.currentText())
        self._settings.set("translate/target", self.default_dst_combo.currentText())
        self._settings.sync()

        if theme_changed:
            self.theme_changed.emit(new_theme)
        self.hotkeys_changed.emit()
        self.accept()
