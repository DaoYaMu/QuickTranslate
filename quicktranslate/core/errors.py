"""把 SDK 原始异常翻译成"用户能照着做点什么"的提示。

放在 core 里是为了让界面（workers）和命令行（cli）共用同一套说法 ——
同一个错误从哪进去都应该给出同样的建议。
"""

from __future__ import annotations


class EmptyTranslationError(RuntimeError):
    """接口通了、模型也回话了，但没吐译文（例如思考链吃掉了全部输出）。"""

    def __init__(self, reasoning_chars: int = 0) -> None:
        super().__init__("模型没有返回译文内容")
        self.reasoning_chars = reasoning_chars


def should_fallback(exc: Exception) -> bool:
    """这个错误值不值得换个模型再试一次？

    关键判断：错误是否"跟着模型走"。
    - 超时 / 连不上 / 5xx / 404（模型下架）/ 429（该模型限流）→ 换模型有意义
    - Key 无效、无权限 → Key 是同一个，换模型照样失败，不如直接报错更快

    没有 openai 依赖时保守返回 False。
    """
    try:
        import openai  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        return False

    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return False
    if isinstance(exc, EmptyTranslationError):
        return True
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code >= 500 or exc.status_code in (404, 408, 409, 429)
    return isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError))


def describe_failure(
    exc: Exception, model: str = "", elapsed: float = 0.0, timeout: float = 0.0
) -> str:
    """生成面向用户的错误说明（多行，可直接铺到结果区）。"""
    try:
        import openai  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        return f"{type(exc).__name__}: {exc}"

    name = f"「{model}」" if model else "当前模型"

    if isinstance(exc, EmptyTranslationError):
        extra = (
            f"（模型思考了 {exc.reasoning_chars} 字，可能全部被思考过程占用）"
            if exc.reasoning_chars
            else ""
        )
        return (
            f"{name} 没有返回译文{extra}。\n\n"
            "可以先确认「关闭模型思考链」已勾选；若仍如此，换一个模型试试。"
        )
    if isinstance(exc, openai.APITimeoutError):
        return (
            f"{name} 等待 {timeout:.0f} 秒仍没有任何响应。\n\n"
            "这通常是该模型在服务端的推理排队或临时不可用，不是本机的问题。\n"
            "可以这样处理：\n"
            "1. 换一个模型再试 —— 同一家服务商换个模型往往立刻就好；\n"
            "2. 打开「测试连接」确认 API 本身是通的；\n"
            "3. 确实需要更长的等待，再把「响应超时」调大。"
        )
    if isinstance(exc, openai.APIConnectionError):
        return (
            f"连不上 API 服务（已用 {elapsed:.0f} 秒）。\n\n"
            "常见原因：本机网络断开、代理软件没开或规则把该域名挡掉了、"
            "Base URL 填错。可在「设置」里点「测试连接」定位。"
        )
    if isinstance(exc, openai.AuthenticationError):
        return "API Key 无效或已过期，请在「设置」里重新填写。"
    if isinstance(exc, openai.PermissionDeniedError):
        return f"当前 API Key 没有调用 {name} 的权限，换一个模型或检查账号权限。"
    if isinstance(exc, openai.NotFoundError):
        return f"{name} 不存在或已下架，请在「设置」里重新选择模型。"
    if isinstance(exc, openai.RateLimitError):
        return f"{name} 触发了限流或余额不足，稍后重试或检查账户余额。"
    if isinstance(exc, openai.APIStatusError):
        detail = str(getattr(exc, "message", "") or "").strip() or str(exc)
        return f"服务端返回 HTTP {exc.status_code}：{detail[:300]}"
    return f"{type(exc).__name__}: {exc}"
