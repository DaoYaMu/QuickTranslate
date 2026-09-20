"""自检：验证程序依赖、路径与 OCR 是否完好（打包后排查问题的第一入口）。

用法：
    python main.py --selftest
    # 打包后（窗口模式没有控制台）：
    QuickTranslate.exe --selftest
    # 报告会写到数据目录下的 selftest.txt，并弹窗显示结论。

返回码 0 = 全部通过，1 = 有失败项。
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

_LINES: list[str] = []
_FAILS: list[str] = []


def _emit(line: str = "") -> None:
    _LINES.append(line)
    try:
        print(line)
    except Exception:  # noqa: BLE001  窗口模式（sys.stdout 为 None）下忽略
        pass


def _check(label: str, cond: bool, extra: str = "") -> bool:
    mark = "OK  " if cond else "FAIL"
    _emit(f"[{mark}] {label}{(' — ' + extra) if extra else ''}")
    if not cond:
        _FAILS.append(label)
    return cond


def _section(title: str) -> None:
    _emit(f"\n=== {title} ===")


def _check_imports() -> None:
    _section("依赖导入")
    modules = [
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "numpy",
        "PIL.Image",
        "onnxruntime",
        "cv2",
        "shapely.geometry",
        "openai",
        "pyperclip",
        "pynput",
        "keyring",
    ]
    for name in modules:
        try:
            __import__(name)
            _check(f"import {name}", True)
        except Exception as exc:  # noqa: BLE001
            _check(f"import {name}", False, f"{type(exc).__name__}: {exc}")


def _check_paths() -> str:
    _section("路径")
    from .config import paths

    _emit(f"  frozen      : {paths.is_frozen()}")
    _emit(f"  executable  : {sys.executable}")
    _emit(f"  数据目录    : {paths.data_dir()}")
    models_dir = paths.ocr_models_dir()
    _emit(f"  OCR 模型目录: {models_dir}")

    for name in ("det_v5.onnx", "cls.onnx", "rec_ch_v5.onnx", "dict_ch_v5.txt"):
        _check(f"模型 {name}", os.path.exists(os.path.join(models_dir, name)))
    for name in ("rec_japan.onnx", "dict_japan.txt"):
        _check(f"模型 {name}（可选）", os.path.exists(os.path.join(models_dir, name)))
    return models_dir


def _check_ocr(models_dir: str) -> None:  # noqa: ARG001  仅用于日志定位
    _section("OCR 识别（合成图）")
    try:
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont
    except Exception as exc:  # noqa: BLE001
        _check("加载 numpy / Pillow", False, str(exc))
        return

    from .core import ocr as ocr_core

    _check("OCR 组件可用", ocr_core.is_available())
    if not ocr_core.is_available():
        return

    font_path = ""
    for candidate in (
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ):
        if Path(candidate).exists():
            font_path = candidate
            break
    if not font_path:
        _check("找到中日文测试字体", False, "未找到可用字体，跳过识别测试")
        return

    font = ImageFont.truetype(font_path, 26)

    def render(text: str):
        # 画布用 900x160、文字留出边距：这是实测最稳的构图。曾经的 760x100
        # 恰好让"宽/高"落在垂直填充阈值（8）下方，检测器会把这个小图放大
        # 6 倍多，文字高度变成 200px+ 而漏检 —— 那是测试图的问题，不是功能问题。
        image = Image.new("RGB", (900, 160), "white")
        ImageDraw.Draw(image).text((24, 24), text, fill="black", font=font)
        return np.asarray(image)

    for lang, text in (("ch", "你好，世界 Hello"), ("japan", "こんにちは世界")):
        try:
            got = ocr_core.ocr_image(render(text), lang)
            flat = got.replace("\n", "").replace(" ", "")
            expected = set(text.replace(" ", ""))
            hit = sum(1 for ch in expected if ch in flat)
            ratio = hit / max(len(expected), 1)
            _check(
                f"[{lang}] 识别 {text!r}", ratio >= 0.6, f"结果={got!r} 覆盖率={ratio:.0%}"
            )
        except Exception:  # noqa: BLE001
            _check(f"[{lang}] 识别", False, traceback.format_exc(limit=3))

    _check_english_spacing()


def _check_english_spacing() -> None:
    """英文词间空格 + 段落折行还原的回归检查。

    PP-OCRv4 的中文识别模型会把英文长句粘成
    ``BritishlaunchedtheIndustrialRevolution?``，v4 检测模型还会在小字号下
    整行漏掉英文。这里用一段真实字号的折行英文做硬断言，防止以后换模型又退回去。
    """
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    from .core import ocr as ocr_core

    sample = (
        "Why do so many people say that Chinese history is more glorious than "
        "British history, even though the British launched the Industrial Revolution? "
        "Some argue that cultural continuity matters more than industrial output."
    )
    font = None
    for candidate in (r"C:\Windows\Fonts\arial.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        if Path(candidate).exists():
            font = ImageFont.truetype(candidate, 16)  # 网页正文的典型字号
            break
    if font is None:
        _emit("  （跳过英文空格检查：未找到西文字体）")
        return

    # 按视觉折成多行，模拟网页正文：既要还原空格，也要把折行接回一句。
    # 画布 700x240 是实测最稳的构图（连续 3 次均 97% 且自动合并）；
    # 换成 900x240 会掉到 48%，说明检测器对"文字占比过低"的图很敏感。
    words, lines, cur = sample.split(" "), [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if font.getlength(trial) <= 700 - 36:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)

    image = Image.new("RGB", (700, 240), "white")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        draw.text((18, 18 + index * 24), line, fill=(30, 30, 34), font=font)
    try:
        got = ocr_core.ocr_image(np.asarray(image), "ch")
    except Exception:  # noqa: BLE001
        _check("英文空格还原", False, traceback.format_exc(limit=3))
        return

    want_words = set(sample.lower().replace("?", "").split())
    got_flat = " ".join(got.lower().split())
    missing = [w for w in want_words if w not in got_flat.replace(",", " ").split()]
    keep = 1 - len(missing) / max(len(want_words), 1)
    _check(
        "英文空格还原（PP-OCRv5 模型）",
        keep >= 0.85,
        f"单词完整率={keep:.0%}" + (f" 缺失={missing[:6]}" if missing else ""),
    )
    _check(
        "英文折行接回一句",
        "\n" not in got.strip(),
        f"结果={got!r}" if "\n" in got.strip() else "已合并为一段",
    )


def _check_translator() -> None:
    _section("翻译客户端")
    try:
        from .core.translator import Translator

        # 只验证构造与参数拼装，不发网络请求
        translator = Translator(
            base_url="https://example.com/v1", api_key="sk-test", model="demo"
        )
        _check("构造 OpenAI 兼容客户端", translator is not None)
    except Exception as exc:  # noqa: BLE001
        _check("构造 OpenAI 兼容客户端", False, f"{type(exc).__name__}: {exc}")


def _check_hotkey_capture() -> None:
    """热键录入控件的回归检查。

    抓的是一条真出过事的回归：旧版用 Qt 自带的 ``QKeySequenceEdit``，
    它会把 ``Ctrl+Shift+S`` 只记成裸 ``S``。裸字母被注册成全局热键后，
    在**任何程序里打字都会触发** —— 对截图热键来说就是
    「在设置里改快捷键 → 每按一个字母弹一次全屏截图遮罩，退不出去」。
    """
    _section("热键录入")
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtCore import QEvent

        from .ui.hotkey_edit import _to_portable
    except Exception as exc:  # noqa: BLE001
        _check("导入热键录入控件", False, f"{type(exc).__name__}: {exc}")
        return

    mods = (
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
    )

    def ev(key, modifiers, text=""):
        return QKeyEvent(QEvent.Type.KeyPress, key, modifiers, text)

    # 1. 修饰键必须保留 —— 核心回归
    got = _to_portable(ev(Qt.Key.Key_S, mods, "s"))
    _check("录入 Ctrl+Shift+S 保留修饰键", got == "Ctrl+Shift+S", f"实际={got!r}")

    got = _to_portable(
        ev(Qt.Key.Key_D, Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier, "d")
    )
    _check("录入 Alt+Shift+D 保留修饰键", got == "Alt+Shift+D", f"实际={got!r}")

    # 2. 单个字母必须被拒绝 —— 就是它挡住了"打字弹截图"
    got = _to_portable(ev(Qt.Key.Key_S, Qt.KeyboardModifier.NoModifier, "s"))
    _check("拒绝裸字母 S（否则会抢全系统按键）", got == "", f"实际={got!r}")

    got = _to_portable(ev(Qt.Key.Key_F9, Qt.KeyboardModifier.NoModifier))
    _check("拒绝无修饰键的 F9", got == "", f"实际={got!r}")

    # 3. 损坏的历史配置要被丢弃
    try:
        from .ui.settings_dialog import SettingsDialog

        bad = SettingsDialog._sanitize_hotkey("S")
        good = SettingsDialog._sanitize_hotkey("Ctrl+Shift+S")
        _check("丢弃历史遗留的裸字母热键", bad == "", f"实际={bad!r}")
        _check("正常热键不被误删", good == "Ctrl+Shift+S", f"实际={good!r}")
    except Exception as exc:  # noqa: BLE001
        _check("热键清洗", False, f"{type(exc).__name__}: {exc}")


def _check_selection() -> None:
    """实测划词取词的两层保证。

    A 层**不依赖前台焦点**，任何情况都能跑：验证「能查到按下修饰键」+
    「注入前会等它们松开」—— 这正是"划词没反应"的根因所在。

    B 层需要一个真实的前台窗口（会**短暂弹出约 1 秒的自检窗口**）：
    取词走的是"往当前前台程序发 Ctrl+C"，桌面锁定/全屏程序占着时无从验证，
    这种情况下**跳过而不是报失败**（否则就是假警报）。

    关键细节：取词必须跑在工作线程里，主线程要一直抽事件循环 ——
    否则 Qt 收不到注入进来的 Ctrl+C。真实运行时取词发生在热键回调里，
    主线程本来就在跑循环，所以这是等价复现。
    """
    _section("划词取词")
    if sys.platform != "win32":
        _emit("  （非 Windows 平台，跳过按键注入检查）")
        return

    try:
        import ctypes
        import threading
        import time

        from pynput.keyboard import Controller, Key

        from .core import clipboard as clipboard_mod
        from .core.clipboard import (
            get_clipboard_text,
            grab_selection,
            pressed_modifiers,
            set_clipboard_text,
            wait_modifier_release,
        )
    except Exception:  # noqa: BLE001
        _check("划词取词环境", False, traceback.format_exc(limit=3))
        return

    keyboard = Controller()

    # ------------------------------------------------------------------ #
    # A 层：修饰键查询与等待（不依赖前台焦点）
    # ------------------------------------------------------------------ #
    try:
        keyboard.press(Key.ctrl)
        try:
            held = pressed_modifiers()
        finally:
            keyboard.release(Key.ctrl)
        _check(
            "能查到正在按下的修饰键",
            any("Ctrl" in name for name in held),
            f"查到={held}",
        )
    except Exception:  # noqa: BLE001
        _check("能查到正在按下的修饰键", False, traceback.format_exc(limit=3))

    try:
        keyboard.press(Key.ctrl)
        keyboard.press(Key.shift)
        releaser = threading.Timer(
            0.25, lambda: [keyboard.release(k) for k in (Key.shift, Key.ctrl)]
        )
        releaser.daemon = True
        releaser.start()
        started = time.monotonic()
        released = wait_modifier_release(timeout=1.5)
        spent = time.monotonic() - started
        releaser.join(timeout=1.0)
        # 注入的修饰键在 0.25s 时松开：若查询有效，应当等到那时候才继续；
        # 若立刻返回（spent≈0），说明查询是空转的，"等松开"等于没做。
        _check(
            "注入 Ctrl+C 前会等修饰键松开",
            released and spent >= 0.15,
            f"用时 {spent:.2f}s（修饰键在 0.25s 时松开）",
        )
    except Exception:  # noqa: BLE001
        _check("注入 Ctrl+C 前会等修饰键松开", False, traceback.format_exc(limit=3))
    finally:
        for key in (Key.shift, Key.ctrl):
            try:
                keyboard.release(key)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ #
    # B 层：端到端取词（需要前台焦点，取不到就跳过）
    # ------------------------------------------------------------------ #
    if os.environ.get("QT_QPA_PLATFORM", "").lower().startswith("offscreen"):
        _emit("  （离屏模式，跳过端到端取词检查）")
        return

    try:
        from PySide6.QtWidgets import QApplication, QMainWindow, QTextEdit

        from .core import clipboard as clipboard_mod
    except Exception:  # noqa: BLE001
        _check("划词取词环境（Qt）", False, traceback.format_exc(limit=3))
        return

    sample = "QuickTranslate selection selftest 划词自检"
    app = QApplication.instance() or QApplication(sys.argv)

    def pump(seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    def grab_with_pump() -> tuple[str, str]:
        """在工作线程取词，主线程持续抽消息 —— 否则 Qt 收不到注入的 Ctrl+C。"""
        box: dict[str, str] = {}

        def task() -> None:
            text, reason = grab_selection(timeout=0.6)
            box["text"], box["reason"] = text, reason

        worker = threading.Thread(target=task, daemon=True)
        worker.start()
        deadline = time.monotonic() + 6.0
        while worker.is_alive() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        worker.join(timeout=1.0)
        return box.get("text", ""), box.get("reason", "")

    backup = get_clipboard_text()
    win = QMainWindow()
    win.setWindowTitle("QuickTranslate 划词自检")
    edit = QTextEdit(sample)
    win.setCentralWidget(edit)
    win.resize(560, 170)
    win.show()
    win.raise_()
    win.activateWindow()
    edit.setFocus()
    edit.selectAll()

    user32 = ctypes.windll.user32

    def force_foreground() -> bool:
        """把自检窗口抢到前台，成功返回 True。

        Windows 有前台锁，硬抢会被拒；下面几种手段轮着试。
        全都失败通常意味着**桌面已锁定**（锁屏界面占着前台）或有全屏程序 ——
        这时划词无从验证，调用方应该**跳过而不是报失败**，否则就成了假警报。
        """
        target = int(win.winId())
        user32.AllowSetForegroundWindow(-1)
        if user32.GetForegroundWindow() == target:
            return True
        for _ in range(3):
            # ① Alt 轻敲解除前台锁
            user32.keybd_event(0x12, 0, 0, 0)
            user32.keybd_event(0x12, 0, 2, 0)
            pump(0.08)
            user32.ShowWindow(target, 9)  # SW_RESTORE
            user32.SetForegroundWindow(target)
            pump(0.3)
            if user32.GetForegroundWindow() == target:
                return True
            # ② 按住 Alt 不放时 SetForegroundWindow（另一招常用技巧）
            user32.keybd_event(0x12, 0, 0, 0)
            pump(0.05)
            user32.ShowWindow(target, 9)
            user32.SetForegroundWindow(target)
            pump(0.15)
            user32.keybd_event(0x12, 0, 2, 0)
            pump(0.2)
            if user32.GetForegroundWindow() == target:
                return True
            # ③ AttachThreadInput 到当前前台线程后再抢
            current = user32.GetForegroundWindow()
            tid = user32.GetWindowThreadProcessId(current, None)
            me = ctypes.windll.kernel32.GetCurrentThreadId()
            user32.AttachThreadInput(tid, me, True)
            user32.ShowWindow(target, 9)
            user32.SetForegroundWindow(target)
            user32.AttachThreadInput(tid, me, False)
            pump(0.3)
            if user32.GetForegroundWindow() == target:
                return True
        return False

    try:
        if not force_foreground():
            _emit("  （抢不到前台焦点：桌面可能已锁定，或有全屏程序占着）")
            _emit("  跳过 —— 划词取词要往真实的前台窗口发 Ctrl+C，此时无法验证")
            return

        # 1) 基准：没有修饰键按着
        edit.selectAll()
        edit.setFocus()
        pump(0.2)
        text, reason = grab_with_pump()
        _check(
            "划词取词可拿到选中文字",
            text.strip() == sample,
            f"取到={text!r}" + (f" 原因={reason!r}" if not text else ""),
        )

        # 2) 真实热键时序：热键回调是在按下主键的**瞬间**触发的，那时用户
        #    通常还按着修饰键。若不等它们松开就注入 Ctrl+C，系统会把它合成
        #    成 Ctrl+Shift+C，目标程序不认 —— 这就是"划词没反应"的根因。
        #
        #    这里必须数"注入了几次"：只靠"最终取到文字"是抓不到回归的，
        #    因为退一步还有重试兜着（第一次白注入、等到修饰键松开后第二次才成）。
        if not force_foreground():
            _emit("  （注入修饰键后丢了前台焦点）跳过第二项检查")
            return

        held = (Key.ctrl, Key.shift)
        injections = {"n": 0}
        real_inject = clipboard_mod._inject_copy  # noqa: SLF001

        def counting_inject() -> bool:
            injections["n"] += 1
            return real_inject()

        result2 = ("", "")
        clipboard_mod._inject_copy = counting_inject  # type: ignore[assignment]
        try:
            edit.selectAll()
            edit.setFocus()
            pump(0.2)
            for key in held:
                keyboard.press(key)
            releaser = threading.Timer(
                0.2, lambda: [keyboard.release(k) for k in reversed(held)]
            )
            releaser.daemon = True
            releaser.start()
            result2 = grab_with_pump()
            releaser.join(timeout=1.0)
        finally:
            clipboard_mod._inject_copy = real_inject  # type: ignore[assignment]
            for key in reversed(held):
                try:
                    keyboard.release(key)
                except Exception:  # noqa: BLE001
                    pass

        text2, reason2 = result2
        _check(
            "按住热键修饰键时也能取到（注入前会等松开）",
            text2.strip() == sample,
            f"取到={text2!r}" + (f" 原因={reason2!r}" if not text2 else ""),
        )
        _check(
            "取词只注入一次 Ctrl+C（说明确实等到了松开）",
            injections["n"] == 1,
            f"实际注入 {injections['n']} 次"
            + ("；说明注入时修饰键还按着，靠重试才成" if injections["n"] > 1 else ""),
        )
    except Exception:  # noqa: BLE001
        _check("划词取词", False, traceback.format_exc(limit=3))
    finally:
        win.close()
        pump(0.2)
        set_clipboard_text(backup)  # 自检不改动用户的剪贴板


def _report_path() -> str:
    try:
        from .config import paths

        return str(Path(paths.data_dir()) / "selftest.txt")
    except Exception:  # noqa: BLE001
        return str(Path(os.path.expanduser("~")) / "quicktranslate_selftest.txt")


def _flush_report() -> str:
    target = _report_path()
    try:
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("\n".join(_LINES) + "\n")
    except Exception:  # noqa: BLE001
        pass
    return target


def _notify(summary: str, target: str) -> None:
    """窗口模式没有控制台，用弹窗把结论告诉用户（Qt 不可用时静默跳过）。"""
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication(sys.argv)
        box = QMessageBox()
        box.setWindowTitle("QuickTranslate 自检")
        box.setIcon(QMessageBox.Icon.Information if not _FAILS else QMessageBox.Icon.Warning)
        box.setText(summary)
        box.setInformativeText(f"完整报告：\n{target}")
        box.exec()
        del app
    except Exception:  # noqa: BLE001
        pass


def run(notify: bool = True) -> int:
    from . import __app_name__, __version__
    from .utils.logging import setup_logging

    setup_logging()
    _emit("=" * 66)
    _emit(f"{__app_name__} {__version__} 自检")
    _emit(f"Python {sys.version.split()[0]}  |  frozen={bool(getattr(sys, 'frozen', False))}")
    _emit("=" * 66)

    _check_imports()
    models_dir = ""
    try:
        models_dir = _check_paths()
    except Exception:  # noqa: BLE001
        _check("路径检查", False, traceback.format_exc(limit=3))
    if models_dir:
        _check_ocr(models_dir)
    _check_translator()
    _check_hotkey_capture()
    _check_selection()

    _emit("\n" + "=" * 66)
    if _FAILS:
        summary = f"自检失败：{len(_FAILS)} 项"
        _emit(summary)
        for item in _FAILS:
            _emit("  - " + item)
    else:
        summary = "自检通过：全部正常"
        _emit(summary)
    _emit("=" * 66)

    target = _flush_report()
    _emit(f"报告已保存：{target}")

    # 打包后的窗口模式看不到控制台输出，用弹窗把结论告诉用户；
    # 加 --no-gui 可跳过弹窗（自动化 / 脚本调用时用）
    if notify and getattr(sys, "frozen", False) and "--no-gui" not in sys.argv:
        _notify(summary, target)
    return 1 if _FAILS else 0
