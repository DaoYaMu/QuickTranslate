"""翻译界面链路测试（用假流，不联网）：python tests/test_translate_flow.py

验证思考型模型场景下界面的状态流转：
    等待 <模型> 响应 → 模型思考中（有字数与计时） → 翻译中 → 翻译完成
以及"停止"按钮能中断翻译。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import quicktranslate.app as app_mod  # noqa: E402
from quicktranslate.core.translator import KIND_CONTENT, KIND_REASONING  # noqa: E402


class FakeTranslator:
    """模拟思考型模型：先吐一大段思考链，再吐译文。"""

    model = "fake-thinking-model"
    extra_body: dict = {}

    def __init__(self, reasoning_chunks: int = 40, content: str = "你好，世界！") -> None:
        self._reasoning_chunks = reasoning_chunks
        self._content = content
        self.cancelled = False

    def translate_stream(self, text: str, source: str, target: str):
        for _ in range(self._reasoning_chunks):
            yield KIND_REASONING, "思考内容"  # 每个 4 字
            time.sleep(0.01)
        for ch in self._content:
            yield KIND_CONTENT, ch
            time.sleep(0.01)


class SlowTranslator(FakeTranslator):
    """永远只思考、不出译文，用来测试中断。"""

    def translate_stream(self, text: str, source: str, target: str):
        while True:
            yield KIND_REASONING, "仍在思考"
            time.sleep(0.02)


def _pump(app: QApplication, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    from quicktranslate.ui import theme as theme_mod

    app.setFont(theme_mod.app_font())

    seen: list[str] = []

    # --- 场景 1：思考型模型，界面必须有"思考中"反馈 ---
    application = app_mod.Application(app)
    application.window.translate_btn.click()  # 空输入：应提示而非翻译
    assert "请先输入" in application.window.status_label.fullText()

    application.window.set_input_text("Hello, world!")
    fake = FakeTranslator()
    app_mod.create_translator = lambda profile, key: fake  # type: ignore[assignment]

    application._start_translate("Hello, world!", "英语", "中文", "window")  # noqa: SLF001
    assert application.window.translate_btn.text() == "停止", "翻译中按钮应变为停止"
    first_status = application.window.status_label.fullText()
    # 文案里带模型名，用来一眼看出是哪个模型不响应；这里只校验"有等待提示"
    assert "等待" in first_status and "响应" in first_status, (
        f"首屏应有等待提示，实际：{first_status!r}"
    )

    for _ in range(60):
        _pump(app, 0.02)
        text = application.window.status_label.fullText()
        if text and text not in seen:
            seen.append(text)
        if "翻译完成" in text:
            break

    status_all = " | ".join(seen)
    assert any("思考中" in s for s in seen), f"缺少思考中提示：{status_all}"
    assert any("翻译中… 已输出" in s for s in seen), f"缺少输出进度：{status_all}"
    assert application.window.output_text().strip() == "你好，世界！"
    assert application.window.translate_btn.text() == "翻译", "结束后按钮应还原"

    # --- 场景 2：点"停止"应能中断 ---
    application.window.set_input_text("Hello again")
    application._start_translate("Hello again", "英语", "中文", "window")  # noqa: SLF001
    _pump(app, 0.15)
    application.window.translate_btn.click()  # 停止
    _pump(app, 0.4)
    assert "已中断" in application.window.status_label.fullText(), (
        f"中断后状态不对：{application.window.status_label.fullText()!r}"
    )
    assert application.window.translate_btn.text() == "翻译"

    # --- 场景 3：浮层窗也要有进度反馈 ---
    slow = SlowTranslator()
    app_mod.create_translator = lambda profile, key: slow  # type: ignore[assignment]
    application._start_translate("Pop up", "英语", "中文", "popup")  # noqa: SLF001
    _pump(app, 0.3)
    popup_status = application.popup.status_label.text()
    assert "思考中" in popup_status, f"浮层缺少思考提示：{popup_status!r}"
    application.cancel_translate()
    _pump(app, 0.2)
    assert "已中断" in application.popup.status_label.text()

    print(f"  状态流转：{status_all}")

    # --- 场景 4：划词取不到文字时必须有可见提示（不能只发托盘气泡） ---
    # 以前只发托盘通知，系统折叠通知时用户就看到"按了没反应"。
    app_mod.grab_selection = lambda timeout=0.5: (  # type: ignore[assignment]
        "",
        "复制时仍按着 左 Alt，请把热键的修饰键完全松开后再试",
    )
    application.popup.finish(True)
    application.translate_selection()
    _pump(app, 0.1)
    assert application.popup.isVisible(), "划词失败时应弹出提示浮层"
    hint = application.popup.output_edit.output_text()
    assert "松开" in hint and "截图翻译" in hint, f"提示应给出可操作建议：{hint!r}"
    assert application.popup.status_label.text() == "划词未取到文字", (
        f"状态栏应说明失败原因：{application.popup.status_label.text()!r}"
    )

    # --- 场景 5：主窗口不在前台时，必须取系统选区 -------------------- #
    # 否则会拿着输入框里的旧内容去翻译，看起来就像"划词没反应"。
    recorded: list[str] = []
    application._start_translate = (  # type: ignore[assignment]
        lambda text, src, tgt, view="window", mode="": recorded.append(text)
    )
    application.window.set_input_text("输入框里的旧内容")
    application.window.hide()
    app_mod.grab_selection = lambda timeout=0.5: ("系统选区里的文字", "")  # type: ignore[assignment]
    application.translate_selection()
    assert recorded == ["系统选区里的文字"], f"应取系统选区，实际：{recorded}"

    application.window.show()
    print("  划词失败提示 + 系统选区优先级：OK")
    print("test_translate_flow: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
