"""备用模型自动切换测试：python tests/test_fallback.py

覆盖三条边界：
1. 主模型超时 → 自动切备用模型并成功，且译文是备用模型给的；
2. Key 无效 → 不切备用模型（换了也一样失败），直接报错；
3. 没配备用模型 → 正常失败，并提示去设置里配一个。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx2 as httpx  # noqa: E402
import openai  # noqa: E402

from quicktranslate.core.translator import KIND_CONTENT, KIND_REASONING  # noqa: E402
from quicktranslate.workers.translate_worker import TranslateWorker  # noqa: E402

_REQ = httpx.Request("POST", "https://example.com/v1/chat/completions")


class FakeTranslator:
    """按预设剧本走：要么抛错，要么先思考再吐译文。"""

    def __init__(self, model: str, error: Exception | None = None,
                 content: str = "译文", reasoning: int = 2) -> None:
        self.model = model
        self.timeout = 90.0
        self.extra_body: dict = {}
        self._error = error
        self._content = content
        self._reasoning = reasoning
        self.calls = 0

    def translate_stream(self, text: str, source: str, target: str):
        self.calls += 1
        if self._error is not None:
            raise self._error
        for _ in range(self._reasoning):
            yield KIND_REASONING, "思考"
        for ch in self._content:
            yield KIND_CONTENT, ch


def _run(primary: FakeTranslator, fallback: FakeTranslator | None):
    """直接同步跑 run()，不启线程，省掉事件循环的麻烦。"""
    worker = TranslateWorker(primary, "hello", "英语", "中文", fallback)
    got = {"switched": [], "ok": [], "fail": []}
    worker.switched.connect(lambda m: got["switched"].append(m))
    worker.succeeded.connect(lambda t: got["ok"].append(t))
    worker.failed.connect(lambda m: got["fail"].append(m))
    worker.run()
    return got


def test_timeout_falls_back() -> None:
    primary = FakeTranslator("bad-model", openai.APITimeoutError(request=_REQ))
    fallback = FakeTranslator("good-model", content="你好，世界！")
    got = _run(primary, fallback)

    assert primary.calls == 1, "主模型应该被尝试一次"
    assert fallback.calls == 1, "备用模型应该被自动启用"
    assert got["switched"] == ["good-model"], f"应发出切换通知：{got['switched']}"
    assert got["ok"] == ["你好，世界！"], f"应拿到备用模型的译文：{got['ok']}"
    assert not got["fail"], "不该报错"
    print("  [超时] 主模型超时 -> 切备用模型 -> 译文成功  OK")


def test_auth_error_does_not_fall_back() -> None:
    primary = FakeTranslator(
        "bad-model",
        openai.AuthenticationError("bad key", response=httpx.Response(401, request=_REQ), body=None),
    )
    fallback = FakeTranslator("good-model")
    got = _run(primary, fallback)

    assert fallback.calls == 0, "Key 无效时不该浪费一次备用模型调用"
    assert not got["switched"], "不该发出切换通知"
    assert got["fail"] and "API Key" in got["fail"][0], f"应直说 Key 有问题：{got['fail']}"
    print("  [鉴权] Key 无效 -> 不切备用模型，直接报错  OK")


def test_no_fallback_configured() -> None:
    primary = FakeTranslator("bad-model", openai.APITimeoutError(request=_REQ))
    got = _run(primary, None)

    assert not got["switched"], "没配备用模型就不该有切换通知"
    assert got["fail"], "应该失败"
    assert "备用模型" in got["fail"][0], (
        f"失败提示应引导用户去配备用模型：{got['fail'][0]!r}"
    )
    print("  [未配] 没设备用模型 -> 报错并提示去设置  OK")


def test_empty_output_falls_back() -> None:
    """模型只思考不吐译文，也该换备用模型试。"""
    primary = FakeTranslator("bad-model", content="")  # 只出思考链，没有正文
    fallback = FakeTranslator("good-model", content="备用译文")
    got = _run(primary, fallback)

    assert got["switched"] == ["good-model"], "空译文应触发切换"
    assert got["ok"] == ["备用译文"], f"应拿到备用译文：{got['ok']}"
    print("  [空译文] 只有思考链 -> 切备用模型 -> 成功  OK")


def main() -> int:
    test_timeout_falls_back()
    test_auth_error_does_not_fall_back()
    test_no_fallback_configured()
    test_empty_output_falls_back()
    print("test_fallback: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
