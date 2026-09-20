"""翻译核心测试（不联网）：python tests/test_translator.py"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quicktranslate.core.translator import (  # noqa: E402
    KIND_CONTENT,
    KIND_REASONING,
    LANGUAGES,
    TARGET_LANGUAGES,
    Translator,
    build_user_message,
    create_translator,
    looks_like_thinking_model,
    parse_extra_body,
    parse_headers,
)


def main() -> int:
    # 请求头解析：JSON 与逐行两种格式
    assert parse_headers('{"X-A": "1", "X-B": "2"}') == {"X-A": "1", "X-B": "2"}
    assert parse_headers("X-A: 1\nX-B: 2") == {"X-A": "1", "X-B": "2"}
    assert parse_headers("") == {}

    # 额外请求参数（关闭思考链）
    assert parse_extra_body("") == {}
    assert parse_extra_body('{"enable_thinking": false}') == {"enable_thinking": False}
    for bad in ("not-json", "[1,2]", "123"):
        try:
            parse_extra_body(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"应当拒绝非法额外参数：{bad!r}")

    # 思考型模型识别
    assert looks_like_thinking_model("Qwen/Qwen3.5-4B")
    assert looks_like_thinking_model("deepseek-reasoner")
    assert not looks_like_thinking_model("gpt-4o-mini")

    # 提示词构建
    msg = build_user_message("hello", "英语", "中文")
    assert "英语" in msg and "中文" in msg and "hello" in msg
    auto = build_user_message("hello", "自动", "中文")
    assert "自动识别" in auto

    # 语言清单
    assert "自动" in LANGUAGES and "自动" not in TARGET_LANGUAGES

    # 客户端构造（占位 Key，不发请求）
    t = Translator(
        base_url="https://example.com/v1",
        api_key="sk-test",
        model="gpt-4o-mini",
        temperature=0.2,
        system_prompt="你是翻译引擎",
        custom_headers="X-Test: 1",
        extra_body='{"enable_thinking": false}',
        timeout=123,
    )
    assert t.model == "gpt-4o-mini"
    assert t.temperature == 0.2
    assert t.extra_body == {"enable_thinking": False}
    assert t.timeout == 123
    assert t._request_kwargs() == {"extra_body": {"enable_thinking": False}}  # noqa: SLF001
    msgs = t._messages("hi", "自动", "日语")  # noqa: SLF001
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["role"] == "user"

    # 无额外参数时不发送 extra_body，避免非思考模型被拒
    t3 = Translator(base_url="https://example.com/v1", api_key="k", model="m")
    assert t3._request_kwargs() == {}  # noqa: SLF001
    assert t3.timeout > 60  # 思考型模型需要更宽松的超时

    # 思考链事件类型常量（worker 依赖）
    assert KIND_REASONING != KIND_CONTENT

    # 由档案字典构造
    t2 = create_translator(
        {"base_url": "https://example.com/v1", "model": "m", "temperature": 0.5},
        "sk-abc",
    )
    assert t2.model == "m"
    t4 = create_translator(
        {
            "base_url": "https://example.com/v1",
            "model": "m",
            "extra_body": '{"enable_thinking": false}',
            "timeout": 0,
        },
        "sk-abc",
    )
    assert t4.extra_body == {"enable_thinking": False}
    assert t4.timeout > 0  # 非法超时回落到默认值

    print("test_translator: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
