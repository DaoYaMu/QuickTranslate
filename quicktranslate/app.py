"""应用装配层：把 UI、核心逻辑、托盘、热键、存储连接起来。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from .config.profiles import ProfileStore
from .config.settings import Settings
from .core import ocr as ocr_core
from .core.capture import grab_virtual_desktop, load_image_file, qpixmap_to_ndarray
from .core.clipboard import get_clipboard_text, get_selected_text
from .core.hotkey import HotkeyManager
from .core.translator import create_fallback_translator, create_translator
from .storage.history import HistoryStore
from .ui.appicon import build_icon
from .ui.history_window import HistoryWindow
from .ui.main_window import MainWindow
from .ui.result_popup import ResultPopup
from .ui.screenshot_overlay import ScreenshotOverlay
from .ui.settings_dialog import SettingsDialog
from .ui.tray import TrayIcon
from .utils.logging import get_logger
from .workers.ocr_worker import OcrWorker, WarmupWorker
from .workers.translate_worker import TranslateWorker

log = get_logger("app")


class Application(QObject):
    def __init__(self, qt_app: QApplication) -> None:
        super().__init__()
        self._qt_app = qt_app

        self.settings = Settings()
        self.profiles = ProfileStore()
        self.history = HistoryStore()

        icon = build_icon()
        self.window = MainWindow(self.settings)
        self.window.setWindowIcon(icon)
        self.popup = ResultPopup()
        self.popup.setWindowIcon(icon)
        self.tray = TrayIcon(icon, self)
        self.hotkeys = HotkeyManager(self)

        self._translate_worker: TranslateWorker | None = None
        self._ocr_worker: OcrWorker | None = None
        self._warmup_worker: WarmupWorker | None = None
        self._overlay: ScreenshotOverlay | None = None
        self._history_window: HistoryWindow | None = None
        self._settings_dialog: SettingsDialog | None = None
        self._hotkeys_suspended = False

        self._pending: dict[str, Any] = {}
        self._restore_window_pending = False

        self._wire()

    # ------------------------------------------------------------------ #
    # 装配
    # ------------------------------------------------------------------ #
    def _wire(self) -> None:
        w = self.window
        w.translate_requested.connect(
            lambda text, src, dst: self._start_translate(text, src, dst, "window")
        )
        w.cancel_requested.connect(self.cancel_translate)
        w.screenshot_requested.connect(self.start_screenshot)
        w.selection_requested.connect(self.translate_selection)
        w.clipboard_requested.connect(self.translate_clipboard)
        w.ocr_file_requested.connect(self.translate_image_file)
        w.settings_requested.connect(self.open_settings)
        w.history_requested.connect(self.open_history)
        w.hidden_to_tray.connect(self._on_hidden_to_tray)

        self.popup.retry_requested.connect(self._retry)
        self.popup.closed.connect(self._on_popup_closed)

        self.tray.show_requested.connect(self.show_main)
        self.tray.screenshot_requested.connect(self.start_screenshot)
        self.tray.settings_requested.connect(self.open_settings)
        self.tray.quit_requested.connect(self.quit)

        self.hotkeys.screenshot_triggered.connect(self.start_screenshot)
        self.hotkeys.selection_triggered.connect(self.translate_selection)
        self.hotkeys.clipboard_triggered.connect(self.translate_clipboard)

    def start(self) -> None:
        self._qt_app.setQuitOnLastWindowClosed(False)
        self.window.set_profile_name(
            str(self.settings.get("profile/current", self.profiles.first_name()))
        )
        self._refresh_tray_flags()
        self._reload_hotkeys()
        self.tray.show()
        self.window.show()
        if self.settings.ocr_enabled:
            self._warmup_ocr()
        if not self._has_usable_profile():
            self.window.set_status("尚未配置 API，请点击右上角「设置」填写模型与密钥", "error")

    # ------------------------------------------------------------------ #
    # 档案
    # ------------------------------------------------------------------ #
    def _current_profile(self) -> dict[str, Any] | None:
        name = str(self.settings.get("profile/current", self.profiles.first_name()))
        return self.profiles.get(name)

    def _has_usable_profile(self) -> bool:
        profile = self._current_profile()
        if profile is None:
            return False
        return bool(self.profiles.api_key(str(profile.get("name", ""))).strip())

    # ------------------------------------------------------------------ #
    # 翻译
    # ------------------------------------------------------------------ #
    def _start_translate(
        self,
        text: str,
        source: str,
        target: str,
        view: str = "window",
        mode: str = "",
    ) -> None:
        text = (text or "").strip()
        if not text:
            self._notify("没有可翻译的文本")
            return

        if self._translate_worker is not None and self._translate_worker.isRunning():
            self._translate_worker.cancel()
            self._translate_worker.wait(2000)

        profile = self._current_profile()
        if profile is None:
            self._notify("没有可用的 API 档案，请先在设置中配置")
            return
        name = str(profile.get("name", ""))
        api_key = self.profiles.api_key(name)
        if not api_key.strip():
            self._notify("请先在「设置 → API 与模型」中填写 API Key")
            return

        try:
            translator = create_translator(profile, api_key)
            fallback = create_fallback_translator(profile, api_key)
        except Exception as exc:  # noqa: BLE001
            log.exception("初始化翻译客户端失败")
            self._notify(f"初始化翻译客户端失败：{exc}")
            return

        self._pending = {
            "text": text,
            "source": source,
            "target": target,
            "view": view,
            "engine": name,
        }

        if view == "popup":
            if mode:
                self.popup.present(text, mode=mode)
            self.popup.begin_stream(
                model=getattr(translator, "model", ""),
                timeout=getattr(translator, "timeout", 0.0),
            )
        else:
            self.window.begin_output()
            # set_busy 内部会立即渲染「等待 / 思考中 + 计时」的状态文案
            self.window.set_busy(
                True,
                model=getattr(translator, "model", ""),
                timeout=getattr(translator, "timeout", 0.0),
            )

        worker = TranslateWorker(translator, text, source, target, fallback, self)
        worker.reasoning.connect(self._on_reasoning)
        worker.chunk.connect(self._on_chunk)
        worker.switched.connect(self._on_switched)
        worker.succeeded.connect(self._on_translate_success)
        worker.failed.connect(self._on_translate_failed)
        self._translate_worker = worker
        worker.start()

    def _on_reasoning(self, piece: str) -> None:
        """模型思考链片段：只用来告诉用户"还在干活"（界面不显示思考内容）。"""
        if self._pending.get("view") == "popup":
            self.popup.note_reasoning(piece)
        else:
            self.window.note_reasoning(piece)

    def _on_switched(self, model: str) -> None:
        """主模型不可用，已自动改用备用模型。"""
        log.warning("已切换到备用模型：%s", model)
        if self._pending.get("view") == "popup":
            self.popup.note_switch(model)
        else:
            self.window.note_switch(model)

    def cancel_translate(self) -> None:
        """中断当前翻译。"""
        worker = self._translate_worker
        if worker is None or not worker.isRunning():
            return  # 已经结束，别覆盖"翻译完成"等终态
        worker.cancel()
        if self._pending.get("view") == "popup":
            self.popup.finish(False, "已中断")
        else:
            self.window.set_busy(False)
            self.window.set_status("已中断", "info")

    def _on_chunk(self, chunk: str) -> None:
        if self._pending.get("view") == "popup":
            self.popup.append_chunk(chunk)
        else:
            self.window.append_output(chunk)

    def _on_translate_success(self, full_text: str) -> None:
        view = self._pending.get("view", "window")
        if view == "popup":
            self.popup.finish(True)
        else:
            self.window.note_success()
        try:
            self.history.add(
                self._pending.get("text", ""),
                full_text,
                self._pending.get("source", ""),
                self._pending.get("target", ""),
                self._pending.get("engine", ""),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("写入历史失败：%s", exc)

    def _on_translate_failed(self, message: str) -> None:
        view = self._pending.get("view", "window")
        if view == "popup":
            self.popup.finish(False, message)
        else:
            # 错误提示是多行的（含处理建议），交给结果区显示
            self.window.show_error(message)

    def _retry(self) -> None:
        pending = self._pending
        if not pending.get("text"):
            return
        self._start_translate(
            pending["text"],
            pending.get("source", "自动"),
            pending.get("target", "中文"),
            "popup",
            mode="重新翻译",
        )

    # ------------------------------------------------------------------ #
    # 截图 / OCR
    # ------------------------------------------------------------------ #
    def start_screenshot(self) -> None:
        if not self.settings.ocr_enabled:
            self._notify("截图翻译已在设置中关闭")
            return
        if self._overlay is not None:
            return
        # 设置窗口开着的时候不要弹截图遮罩 —— 遮罩是全屏置顶窗口，
        # 会把设置对话框压住，用户会以为程序"卡死/退不出去"。
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            log.info("设置窗口已打开，忽略本次截图请求")
            return
        self._restore_window_pending = self.window.isVisible()
        if self._restore_window_pending:
            self.window.hide()
        QTimer.singleShot(220, self._grab_and_show_overlay)

    def _grab_and_show_overlay(self) -> None:
        try:
            frozen, rect, _dpr = grab_virtual_desktop()
        except Exception as exc:  # noqa: BLE001
            log.exception("抓取屏幕失败")
            self._restore_window()
            self._notify(f"抓取屏幕失败：{exc}")
            return

        overlay = ScreenshotOverlay(frozen, rect)
        overlay.captured.connect(self._on_region_captured)
        overlay.cancelled.connect(self._on_overlay_cancelled)
        overlay.destroyed.connect(lambda *_: setattr(self, "_overlay", None))
        self._overlay = overlay
        overlay.show()

    def _on_overlay_cancelled(self) -> None:
        self._restore_window()

    def _on_region_captured(self, pixmap: QPixmap) -> None:
        try:
            image = qpixmap_to_ndarray(pixmap)
        except Exception as exc:  # noqa: BLE001
            log.exception("截图转换失败")
            self._restore_window()
            self._notify(f"截图转换失败：{exc}")
            return
        self._run_ocr(image, "截图翻译")

    def translate_image_file(self, path: str) -> None:
        try:
            image = load_image_file(path)
        except Exception as exc:  # noqa: BLE001
            self._notify(f"读取图片失败：{exc}")
            return
        self._run_ocr(image, "图片翻译")

    def _run_ocr(self, image: Any, mode: str) -> None:
        if not self.settings.ocr_enabled:
            self._notify("OCR 已在设置中关闭")
            return
        if not ocr_core.is_available():
            self._notify("未安装 OCR 组件（rapidocr），请先安装依赖")
            return

        self.popup.present("", mode=f"{mode} · 识别中…")
        self.popup.set_source_text("正在识别文字…")

        worker = OcrWorker(image, self.settings.ocr_lang, self)
        worker.recognized.connect(lambda text: self._on_ocr_done(text, mode))
        worker.failed.connect(lambda msg: self.popup.finish(False, msg))
        self._ocr_worker = worker
        worker.start()

    def _on_ocr_done(self, text: str, mode: str) -> None:
        text = (text or "").strip()
        if not text:
            self.popup.set_source_text("")
            self.popup.finish(False, "未识别到文字，请重新框选")
            return
        self.popup.set_source_text(text)
        self.popup.mode_label.setText(mode)
        self._start_translate(
            text,
            self.window.current_source(),
            self.window.current_target(),
            "popup",
        )

    def _warmup_ocr(self) -> None:
        if not ocr_core.is_available():
            return
        if self._warmup_worker is not None and self._warmup_worker.isRunning():
            return
        worker = WarmupWorker(self.settings.ocr_lang, self)
        worker.done.connect(self._on_warmup_done)
        self._warmup_worker = worker
        worker.start()

    def _on_warmup_done(self, ok: bool) -> None:
        if ok:
            log.info("OCR 预热完成")
            return
        log.warning("OCR 预热失败，截图 / 图片识别将不可用")
        self.window.set_status("OCR 引擎不可用，请检查安装是否完整", "error")

    # ------------------------------------------------------------------ #
    # 划词 / 剪贴板
    # ------------------------------------------------------------------ #
    def translate_selection(self) -> None:
        # 主窗口在前台时，「划词」指输入框里选中的文字；
        # 否则一律取系统选区 —— 不然会拿着输入框里的旧内容去翻译，
        # 看起来就像"划词没反应"。
        text = ""
        if self.window.isActiveWindow():
            text = self.window.selected_input_text()
        if not text.strip():
            text, reason = grab_selection()
        if not text.strip():
            self._show_selection_hint(reason or "没有检测到选中的文本")
            return
        self._start_translate(
            text,
            self.window.current_source(),
            self.window.current_target(),
            "popup",
            mode="划词翻译",
        )

    def _show_selection_hint(self, reason: str) -> None:
        """划词失败要看得见。

        以前只发托盘气泡，系统一旦折叠通知用户就以为"按了没反应"，
        所以这里直接把原因和处理办法摆到结果浮层上。
        """
        log.warning("划词取词失败：%s", reason)
        if self.window.isVisible():
            self.window.set_status(f"划词翻译：{reason}", "error")
        self.popup.show_hint(
            "划词翻译",
            f"{reason}。\n\n"
            "可以这样处理：\n"
            "1. 先用鼠标选中要翻译的文字，再按热键；\n"
            "2. 热键的 Alt / Ctrl / Shift 要完全松开 —— 程序会自动等，"
            "但一直按住不放就会复制失败；\n"
            "3. 目标程序若以管理员身份运行，Windows 会拦截按键注入，"
            "这种情况请改用「截图翻译」；\n"
            "4. 也可以把文字粘贴到主窗口后点「翻译」。",
            title="划词未取到文字",
        )

    def translate_clipboard(self) -> None:
        text = get_clipboard_text()
        if not text.strip():
            self._notify("剪贴板为空")
            return
        self._start_translate(
            text,
            self.window.current_source(),
            self.window.current_target(),
            "popup",
            mode="剪贴板翻译",
        )

    # ------------------------------------------------------------------ #
    # 窗口与托盘
    # ------------------------------------------------------------------ #
    def _refresh_tray_flags(self) -> None:
        available = QSystemTrayIcon.isSystemTrayAvailable()
        self.window.hide_on_close = bool(self.settings.close_to_tray and available)
        self.window.hide_on_minimize = bool(self.settings.minimize_to_tray and available)

    def _restore_window(self) -> None:
        if self._restore_window_pending:
            self._restore_window_pending = False
            self.show_main()

    def _on_popup_closed(self) -> None:
        self._restore_window()

    def _on_hidden_to_tray(self) -> None:
        self.tray.notify("QuickTranslate", "已最小化到托盘，快捷键依然可用")

    def show_main(self) -> None:
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()
        self.window.focus_input()

    def _reload_hotkeys(self) -> None:
        if getattr(self, "_hotkeys_suspended", False):
            return  # 正在录入热键，等录入结束再注册
        if not self.settings.hotkeys_enabled:
            self.hotkeys.stop()
            self.window.set_status("全局快捷键已关闭")
            return
        mapping = {
            "screenshot": self.settings.hotkey("screenshot"),
            "selection": self.settings.hotkey("selection"),
            "clipboard": self.settings.hotkey("clipboard"),
        }
        if not self.hotkeys.start(mapping):
            self.window.set_status("全局快捷键注册失败（可能被占用）", "error")

    def _notify(self, message: str) -> None:
        log.warning("提示：%s", message)
        if self.window.isVisible():
            self.window.set_status(message, "error")
        else:
            self.tray.notify("QuickTranslate", message)

    # ------------------------------------------------------------------ #
    # 设置 / 历史
    # ------------------------------------------------------------------ #
    def open_settings(self) -> None:
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        dialog = SettingsDialog(self.settings, self.profiles, self.window)
        self._settings_dialog = dialog
        dialog.theme_changed.connect(self.window.apply_theme)
        dialog.hotkeys_changed.connect(self._reload_hotkeys)
        dialog.hotkey_recording.connect(self._on_hotkey_recording)
        try:
            dialog.exec()
        finally:
            # 对话框关闭后无论发生什么都要恢复全局热键
            self._settings_dialog = None
            self._hotkeys_suspended = False
            self._reload_hotkeys()

        self.window.apply_theme(self.settings.theme)
        self.window.reload_from_settings()
        self.window.set_profile_name(
            str(self.settings.get("profile/current", self.profiles.first_name()))
        )
        self._refresh_tray_flags()
        if self.settings.ocr_enabled:
            self._warmup_ocr()

    def _on_hotkey_recording(self, active: bool) -> None:
        """录入热键期间摘掉全局热键。

        否则用户在"截图翻译"框里按 Ctrl+Shift+S 验证能不能用时，
        会真的弹出截图遮罩把设置窗口盖住 —— 表现就是"进了截图模式退不出来"。
        """
        self._hotkeys_suspended = active
        if active:
            self.hotkeys.stop()
            log.info("热键录入中，已临时暂停全局热键")
        else:
            self._reload_hotkeys()

    def open_history(self) -> None:
        if self._history_window is None:
            self._history_window = HistoryWindow(self.history, self.window)
            self._history_window.reuse_requested.connect(self._on_history_reuse)
        self._history_window.reload()
        self._history_window.show()
        self._history_window.raise_()
        self._history_window.activateWindow()

    def _on_history_reuse(self, src_text: str, _dst_text: str) -> None:
        self.window.set_input_text(src_text)
        self.show_main()

    # ------------------------------------------------------------------ #
    def quit(self) -> None:
        log.info("退出应用")
        try:
            if self._translate_worker is not None and self._translate_worker.isRunning():
                self._translate_worker.cancel()
                self._translate_worker.wait(1500)
            if self._ocr_worker is not None and self._ocr_worker.isRunning():
                self._ocr_worker.wait(1500)
            if self._warmup_worker is not None and self._warmup_worker.isRunning():
                self._warmup_worker.wait(1500)
        except Exception:  # noqa: BLE001
            pass

        self.hotkeys.stop()
        self.tray.hide()
        self.window.save_state()
        self.settings.sync()
        try:
            self.history.close()
        except Exception:  # noqa: BLE001
            pass
        self._qt_app.quit()
