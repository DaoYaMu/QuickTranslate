"""API / 模型健康检查：模型"一直翻译中"时先跑这个。

用法：
    .venv/Scripts/python.exe tools/check_api.py
    .venv/Scripts/python.exe tools/check_api.py --models Qwen/Qwen3-8B deepseek-ai/DeepSeek-V3

做三件事：
1. 列一下服务商给了哪些模型（顺带验证 Key 是通的）；
2. 把当前档案的主模型 / 备用模型和一批常见模型逐个发一次真实翻译请求；
3. 给出「谁活着、谁不响应」的结论。

典型结论：Base URL / Key 都正常，但某个模型 90 秒零字节 —— 那是服务商那边该模型
的推理排队或故障，换模型即可，不用折腾本机网络。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx2 as httpx  # noqa: E402

from quicktranslate.config.profiles import ProfileStore  # noqa: E402

TIMEOUT = 30.0

# 常见的备用候选（同系列挂了就别选同系列，跨一家更稳）
DEFAULT_CANDIDATES = [
    "Qwen/Qwen3-8B",
    "deepseek-ai/DeepSeek-V3",
    "zai-org/GLM-5.3",
]

PROBE_TEXT = "Good morning! The meeting has been moved to 3 PM."


def _arg_models(argv: list[str]) -> list[str]:
    if "--models" in argv:
        return [a for a in argv[argv.index("--models") + 1:] if not a.startswith("--")]
    return []


def main() -> int:
    store = ProfileStore()
    profile = store.get(store.first_name())
    if profile is None:
        print("没有可用的档案")
        return 1
    key = store.api_key(str(profile["name"]))
    base = str(profile["base_url"]).rstrip("/")

    print(f"档案     : {profile['name']}")
    print(f"Base URL : {base}")
    print(f"API Key  : {'已配置' if key.strip() else '!! 未配置'}")
    print("-" * 72)

    # --- 1. 连通性 + 模型列表 ---
    try:
        with httpx.Client(timeout=15, trust_env=False) as c:
            r = c.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
        if r.status_code != 200:
            print(f"!! GET /models 返回 HTTP {r.status_code}：{r.text[:200]}")
            return 1
        ids = [m["id"] for m in r.json().get("data", [])]
        print(f"连通性   : OK，服务商提供 {len(ids)} 个模型")
    except Exception as exc:  # noqa: BLE001
        print(f"!! 连不上 {base}：{type(exc).__name__}: {exc}")
        print("   检查网络 / 代理软件是否把该域名挡掉了。")
        return 1

    # --- 2. 逐个探测 ---
    targets: list[str] = []
    for m in (profile.get("model"), profile.get("fallback_model")):
        if m and m not in targets:
            targets.append(str(m))
    for m in _arg_models(sys.argv) or DEFAULT_CANDIDATES:
        if m not in targets:
            targets.append(m)

    print("-" * 72)
    print(f"{'模型':<34} {'结果':<22} 说明")
    print("-" * 72)
    alive: list[str] = []
    dead: list[str] = []
    for model in targets:
        tag = ""
        if model == profile.get("model"):
            tag = "（主模型）"
        elif model == profile.get("fallback_model"):
            tag = "（备用模型）"

        body = {
            "model": model,
            "temperature": 0.3,
            "stream": False,
            "max_tokens": 120,
            "messages": [
                {"role": "user", "content": f"翻译成中文，只输出译文：\n\n{PROBE_TEXT}"}
            ],
            "enable_thinking": False,
        }
        started = time.monotonic()
        try:
            with httpx.Client(
                timeout=httpx.Timeout(TIMEOUT, connect=8), trust_env=False
            ) as c:
                r = c.post(
                    f"{base}/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json=body,
                )
            dt = time.monotonic() - started
            if r.status_code == 200:
                out = (r.json()["choices"][0]["message"].get("content") or "").strip()
                print(f"{model:<34} {'OK':<22} {dt:.1f}s  {out[:34]}")
                alive.append(model)
            else:
                print(f"{model:<34} {'HTTP ' + str(r.status_code):<22} {dt:.1f}s  {r.text[:60]}")
                dead.append(model)
        except Exception as exc:  # noqa: BLE001
            dt = time.monotonic() - started
            print(f"{model:<34} {'无响应':<20} {dt:.1f}s  {type(exc).__name__}{tag}")
            dead.append(model)

    # --- 3. 结论 ---
    print("-" * 72)
    if dead and alive:
        print(f"结论：{', '.join(dead)} 没响应（服务商侧问题），"
              f"可用的是 {', '.join(alive[:3])}。")
        print("      建议把主模型/备用模型都设成上面的可用项。")
    elif dead:
        print(f"结论：所有模型都不响应。可能是 Key 失效、余额耗尽，或该服务商整体故障。")
    else:
        print("结论：全部正常。若界面仍卡住，请看日志 quicktranslate.log。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
