"""翻译工作线程：流式产出译文，避免阻塞 UI。

支持「备用模型」：主模型超时 / 连不上 / 模型下架这类"跟着模型走"的错误，
会自动换备用模型再试一次，而不是直接失败——这是"一直翻译中"最实际的兜底。
"""

from __future__ import annotations

import time

from PySide6.QtCore import QThread, Signal

from ..core.errors import EmptyTranslationError, describe_failure, should_fallback
from ..core.translator import KIND_CONTENT, KIND_REASONING, Translator
from ..utils.logging import get_logger

log = get_logger("worker.translate")


class TranslateWorker(QThread):
    reasoning = Signal(str)  # 模型思考链片段（非译文）
    chunk = Signal(str)      # 译文片段
    switched = Signal(str)   # 已切换到备用模型（携带新模型名）
    succeeded = Signal(str)  # 完整译文
    failed = Signal(str)     # 错误信息

    def __init__(
        self,
        translator: Translator,
        text: str,
        source: str,
        target: str,
        fallback: Translator | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator
        self._fallback = fallback
        self._text = text
        self._source = source
        self._target = target
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    # ------------------------------------------------------------------ #
    def _attempt(self, translator: Translator) -> tuple[str, Exception | None, float]:
        """跑一次流式翻译，返回 (译文, 错误, 用时)。中途被取消时错误为 None。"""
        buffer: list[str] = []
        reasoning_len = 0
        started = time.monotonic()
        first_content_at: float | None = None
        try:
            for kind, piece in translator.translate_stream(
                self._text, self._source, self._target
            ):
                if self._cancelled:
                    log.info("翻译已取消（已用 %.1fs）", time.monotonic() - started)
                    return "", None, time.monotonic() - started
                if kind == KIND_REASONING:
                    reasoning_len += len(piece)
                    self.reasoning.emit(piece)
                    continue
                if kind != KIND_CONTENT:
                    continue
                if first_content_at is None:
                    first_content_at = time.monotonic() - started
                    log.info(
                        "模型思考结束：思考 %d 字，用时 %.1fs，开始输出译文",
                        reasoning_len, first_content_at,
                    )
                buffer.append(piece)
                self.chunk.emit(piece)
        except Exception as exc:  # noqa: BLE001
            elapsed = time.monotonic() - started
            log.exception("翻译失败（已用 %.1fs）", elapsed)
            return "", exc, elapsed

        elapsed = time.monotonic() - started
        result = "".join(buffer).strip()
        log.info(
            "翻译完成：用时 %.1fs（思考 %d 字 / 首译文 %.1fs）",
            elapsed, reasoning_len,
            first_content_at if first_content_at is not None else -1.0,
        )
        if not result:
            return "", EmptyTranslationError(reasoning_len), elapsed
        return result, None, elapsed

    def run(self) -> None:  # noqa: D102
        candidates = [(self._translator, False)]
        if self._fallback is not None:
            candidates.append((self._fallback, True))

        log.info(
            "开始翻译：model=%s 源=%s 目标=%s 长度=%d 额外参数=%s 备用=%s",
            self._translator.model, self._source, self._target, len(self._text),
            self._translator.extra_body or "无",
            self._fallback.model if self._fallback else "无",
        )

        last: tuple[Translator, Exception, float] | None = None
        for translator, is_fallback in candidates:
            if self._cancelled:
                return
            if is_fallback:
                log.warning(
                    "主模型 %s 不可用（%s），改用备用模型 %s 重试",
                    self._translator.model, type(last[1]).__name__ if last else "?",
                    translator.model,
                )
                self.switched.emit(translator.model)

            result, error, elapsed = self._attempt(translator)
            if error is None:
                if result:
                    self.succeeded.emit(result)
                return
            last = (translator, error, elapsed)
            if not should_fallback(error):
                self.failed.emit(
                    describe_failure(error, translator.model, elapsed, translator.timeout)
                )
                return

        # 所有候选（主 + 备用）都没成
        translator, error, elapsed = last  # type: ignore[misc]
        message = describe_failure(error, translator.model, elapsed, translator.timeout)
        if self._fallback is not None:
            message += (
                f"\n\n备用模型「{self._fallback.model}」也试过了，同样没成功，"
                "可能是这个服务商整体不可用或网络有问题。"
            )
        else:
            message += "\n\n提示：在「设置 → 模型」里填一个「备用模型」，下次遇到这种情况会自动换模型重试。"
        self.failed.emit(message)


class ConnectionTestWorker(QThread):
    """设置对话框的"测试连接"。"""

    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, translator: Translator, parent=None) -> None:
        super().__init__(parent)
        self._translator = translator

    def run(self) -> None:  # noqa: D102
        started = time.monotonic()
        try:
            reply = self._translator.test_connection()
            log.info("连接测试成功：用时 %.1fs", time.monotonic() - started)
            self.succeeded.emit(reply or "(空响应)")
        except Exception as exc:  # noqa: BLE001
            elapsed = time.monotonic() - started
            log.exception("连接测试失败")
            self.failed.emit(
                describe_failure(
                    exc,
                    self._translator.model,
                    elapsed,
                    float(getattr(self._translator, "timeout", 0.0) or 0.0),
                )
            )
