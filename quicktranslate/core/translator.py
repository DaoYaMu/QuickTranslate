"""AI 翻译核心：封装 OpenAI 兼容 Chat Completions 接口（支持流式）。

要点：
1. 思考型模型（Qwen3 / DeepSeek-R1 / GLM-Z1 等）会先吐一大段
   ``reasoning_content``（思考链），几十秒后才开始输出正文。这里把两类内容
   都作为事件流抛给上层，否则界面在思考阶段完全无输出，看起来像卡死。
2. 超时必须是"等首字节 / 等下一个字"的粒度，而不是"整次请求"。否则模型
   服务端不响应时，界面会长时间停在"翻译中"。具体见 ``build_timeout``。
"""

from __future__ import annotations

import json
from typing import Iterator

from ..config.profiles import DEFAULT_TIMEOUT
from ..utils.logging import get_logger

log = get_logger("translator")

# 支持的语言（"自动" 表示由模型自行识别源语言）
LANGUAGES: list[str] = ["自动", "中文", "英语", "日语"]

# 目标语言不能是"自动"
TARGET_LANGUAGES: list[str] = [x for x in LANGUAGES if x != "自动"]

# 流式事件类型
KIND_REASONING = "reasoning"  # 模型的思考链（不是译文）
KIND_CONTENT = "content"      # 真正的译文片段

# --------------------------------------------------------------------------- #
# 超时策略
# --------------------------------------------------------------------------- #
# 建连超时：连不上就赶快失败（代理没开、域名解析不了都归这里），
# 不必跟着 read 超时一起等。
CONNECT_TIMEOUT = 15.0
# 写出请求体的超时
WRITE_TIMEOUT = 60.0
# 等待连接池空闲连接的超时
POOL_TIMEOUT = 15.0

# 重试次数：0 表示不自动重试。
# 超时重试毫无意义（模型服务挂了重试多少次都一样），只会把等待时间成倍拉长，
# 让"卡住"变得更难忍受；失败后由用户点「重新翻译」更可控。
MAX_RETRIES = 0

# 常见思考型模型的特征词（用于在界面上给出提示 / 自动关闭思考链）
THINKING_MODEL_HINTS = (
    "qwen3",
    "qwen-3",
    "deepseek-r1",
    "deepseek-reasoner",
    "-r1",
    "reasoner",
    "reasoning",
    "glm-z1",
    "magistral",
    "thinking",
)


def _httpx_module():
    """拿到 openai SDK 实际使用的 httpx 实现（本项目里被重命名为 httpx2）。"""
    try:
        import httpx2 as httpx  # noqa: PLC0415

        return httpx
    except ImportError:
        import httpx  # noqa: PLC0415

        return httpx


def build_timeout(read_timeout: float):
    """构造细粒度超时对象。

    关键点：``read`` 在流式响应下是"两个数据块之间的最长间隔"，
    于是 ``read=去程超时`` 同时充当了两个看门狗：
    - 等第一个字节（模型服务不响应 → 到点报错，不再无限等待）
    - 等下一个字（模型输出途中卡死 → 同样能及时止损）
    """
    httpx = _httpx_module()
    return httpx.Timeout(
        connect=min(CONNECT_TIMEOUT, read_timeout),
        read=read_timeout,
        write=WRITE_TIMEOUT,
        pool=POOL_TIMEOUT,
    )


def looks_like_thinking_model(model: str) -> bool:
    """根据模型名粗略判断是否为"思考型"模型。"""
    name = (model or "").lower()
    return any(hint in name for hint in THINKING_MODEL_HINTS)


def parse_extra_body(text: str) -> dict:
    """解析「额外请求参数」（JSON 对象）。非法时抛 ValueError。"""
    text = (text or "").strip()
    if not text:
        return {}
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"额外请求参数不是合法 JSON：{exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError("额外请求参数必须是 JSON 对象，例如 {\"enable_thinking\": false}")
    return obj


def parse_headers(text: str) -> dict[str, str]:
    """解析自定义请求头：支持 JSON 对象或 ``Key: Value`` 多行格式。"""
    text = (text or "").strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return {str(k): str(v) for k, v in obj.items()}
        except json.JSONDecodeError:
            log.warning("自定义请求头 JSON 解析失败，回退按行解析")
    headers: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip().rstrip(",")
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().strip('"')
        value = value.strip().strip('"')
        if key:
            headers[key] = value
    return headers


def build_user_message(text: str, source: str, target: str) -> str:
    if source and source != "自动":
        task = f"将下面的文本从{source}翻译成{target}"
    else:
        task = f"将下面的文本翻译成{target}（请自动识别源语言）"
    return f"{task}：\n\n{text}"


class Translator:
    """一次翻译会话（对应一个 API 档案 + 一次配置快照）。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.3,
        system_prompt: str = "",
        custom_headers: str = "",
        extra_body: str = "",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        try:
            from openai import OpenAI  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("未安装 openai 依赖，请执行 pip install openai") from exc

        self.model = (model or "").strip()
        self.temperature = float(temperature)
        self.system_prompt = system_prompt or ""
        self.extra_body = parse_extra_body(extra_body)
        self.timeout = float(timeout) if timeout else DEFAULT_TIMEOUT

        kwargs: dict[str, object] = {
            "api_key": (api_key or "").strip() or "sk-placeholder",
            "timeout": build_timeout(self.timeout),
            "max_retries": MAX_RETRIES,
        }
        if base_url.strip():
            kwargs["base_url"] = base_url.strip()
        headers = parse_headers(custom_headers)
        if headers:
            kwargs["default_headers"] = headers

        self._client = OpenAI(**kwargs)  # type: ignore[arg-type]

    # ------------------------------------------------------------------ #
    def _messages(self, text: str, source: str, target: str) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if self.system_prompt.strip():
            messages.append({"role": "system", "content": self.system_prompt.strip()})
        messages.append({"role": "user", "content": build_user_message(text, source, target)})
        return messages

    def _request_kwargs(self) -> dict:
        """把 extra_body 合并进请求参数（为空则不加）。"""
        return {"extra_body": self.extra_body} if self.extra_body else {}

    def translate_stream(
        self, text: str, source: str, target: str
    ) -> Iterator[tuple[str, str]]:
        """流式翻译，产出 ``(kind, 片段)``。

        kind 为 ``KIND_REASONING``（模型思考链）或 ``KIND_CONTENT``（译文）。
        """
        stream = self._client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=self._messages(text, source, target),  # type: ignore[arg-type]
            stream=True,
            **self._request_kwargs(),
        )
        for event in stream:  # type: ignore[union-attr]
            choices = getattr(event, "choices", None)
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            if delta is None:
                continue
            # 思考链：不同服务商字段名不一致，都兼容一下
            for attr in ("reasoning_content", "reasoning", "thinking"):
                piece = getattr(delta, attr, None)
                if piece:
                    yield KIND_REASONING, piece
                    break
            piece = getattr(delta, "content", None)
            if piece:
                yield KIND_CONTENT, piece

    def translate(self, text: str, source: str, target: str) -> str:
        """一次性（非流式）翻译。"""
        response = self._client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=self._messages(text, source, target),  # type: ignore[arg-type]
            **self._request_kwargs(),
        )
        return response.choices[0].message.content or ""

    def test_connection(self) -> str:
        """连通性自检，返回模型回显内容。"""
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": "ping"}],
            **self._request_kwargs(),
        )
        return response.choices[0].message.content or ""


def create_translator(profile: dict, api_key: str) -> Translator:
    """由档案字典构造 Translator。"""
    return Translator(
        base_url=str(profile.get("base_url", "")),
        api_key=api_key,
        model=str(profile.get("model", "")),
        temperature=float(profile.get("temperature", 0.3)),
        system_prompt=str(profile.get("system_prompt", "")),
        custom_headers=str(profile.get("custom_headers", "")),
        extra_body=str(profile.get("extra_body", "") or ""),
        timeout=float(profile.get("timeout", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT),
    )


def create_fallback_translator(profile: dict, api_key: str) -> Translator | None:
    """构造备用模型的 Translator。

    未配置、或备用模型与主模型同名（没意义）时返回 None。
    其余参数（Base URL / 超时 / 额外参数）全部沿用主档案。
    """
    fallback = str(profile.get("fallback_model", "") or "").strip()
    primary = str(profile.get("model", "") or "").strip()
    if not fallback or fallback == primary:
        return None
    data = dict(profile)
    data["model"] = fallback
    return create_translator(data, api_key)
