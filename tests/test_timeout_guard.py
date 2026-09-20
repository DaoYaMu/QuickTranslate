"""超时/失败提示回归测试：python tests/test_timeout_guard.py

锁住这次踩过的坑：

1. 超时必须是"等首字节"的粒度，而且不能再被 SDK 的 2 次重试放大 3 倍
   —— 否则模型服务一挂就要静默等 15 分钟。
2. 旧的 300 秒默认值要一次性迁移到 90 秒（只动没被手工改过的档案）。
3. 超时/连不上/Key 错了，都要给出"照着能做什么"的提示，
   而不是甩一句 "Request timed out"。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quicktranslate.config.profiles import (  # noqa: E402
    DEFAULT_TIMEOUT,
    LEGACY_DEFAULT_TIMEOUT,
    ProfileStore,
)
from quicktranslate.core.errors import describe_failure  # noqa: E402
from quicktranslate.core.translator import (  # noqa: E402
    CONNECT_TIMEOUT,
    MAX_RETRIES,
    build_timeout,
)


def test_timeout_shape() -> None:
    t = build_timeout(90.0)
    assert t.read == 90.0, f"read 超时应该是用户设置的响应超时，实际 {t.read}"
    assert t.connect == CONNECT_TIMEOUT, f"connect 应固定为 {CONNECT_TIMEOUT}，实际 {t.connect}"
    # 用户把响应超时设得比 connect 还小时，connect 不能反超
    assert build_timeout(5.0).connect == 5.0, "connect 不应超过 read"
    assert MAX_RETRIES == 0, "不应自动重试：重试只会把等待时间成倍拉长"
    print(f"  [超时] read={t.read}s connect={t.connect}s retries={MAX_RETRIES}  OK")


def test_profile_timeout_migration() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "profiles.json"
        path.write_text(
            json.dumps([
                {"name": "旧的没改过", "timeout": LEGACY_DEFAULT_TIMEOUT},
                {"name": "手工调过", "timeout": 240.0},
            ], ensure_ascii=False),
            encoding="utf-8",
        )
        store = ProfileStore(str(path))
        by_name = {p["name"]: p for p in store.profiles}
        assert by_name["旧的没改过"]["timeout"] == DEFAULT_TIMEOUT, (
            f"旧默认值应迁移为 {DEFAULT_TIMEOUT}，实际 {by_name['旧的没改过']['timeout']}"
        )
        assert by_name["手工调过"]["timeout"] == 240.0, "手工调过的超时不能被覆盖"

        # 再加载一次：迁移过一次就不该反复改写
        saved = json.loads(path.read_text(encoding="utf-8"))
        again = ProfileStore(str(path))
        assert {p["name"]: p["timeout"] for p in again.profiles} == {
            p["name"]: p["timeout"] for p in saved
        }, "重复加载不应再次改写档案"
    print("  [迁移] 旧 300s -> 90s，手工值不动，且只迁移一次  OK")


def test_failure_messages() -> None:
    import httpx2 as httpx
    import openai

    req = httpx.Request("POST", "https://example.com/v1/chat/completions")

    timeout_msg = describe_failure(
        openai.APITimeoutError(request=req), "Qwen/Qwen3.5-4B", 90.0, 90.0
    )
    assert "换一个模型" in timeout_msg, f"超时提示必须先建议换模型：{timeout_msg!r}"
    assert "Qwen/Qwen3.5-4B" in timeout_msg, "超时提示应带上是哪个模型"
    assert "90" in timeout_msg, "超时提示应说明等了多久/上限多少"

    conn_msg = describe_failure(openai.APIConnectionError(request=req), "m", 15.0, 90.0)
    assert "代理" in conn_msg, f"连不上的提示应提到代理：{conn_msg!r}"

    auth_msg = describe_failure(
        openai.AuthenticationError(
            "bad key",
            response=httpx.Response(401, request=req),
            body=None,
        ),
        "m", 1.0, 90.0,
    )
    assert "API Key" in auth_msg, f"鉴权失败应直说 Key 有问题：{auth_msg!r}"

    # 兜底分支不能炸
    assert describe_failure(ValueError("boom"), "m", 1.0, 90.0)
    print("  [提示] 超时/连不上/鉴权 三类都有可操作建议  OK")


def main() -> int:
    test_timeout_shape()
    test_profile_timeout_migration()
    test_failure_messages()
    print("test_timeout_guard: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
