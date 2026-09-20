"""命令行翻译 / OCR：验证 API 链路与耗时（含思考型模型的静默时长）。

用法：
    QuickTranslate.exe --translate "Hello, world!"
    QuickTranslate.exe --translate "Hello" --from 英语 --to 中文
    QuickTranslate.exe --translate "Hello" --profile siliconflow.cn
    QuickTranslate.exe --translate "Hello" --model Qwen/Qwen3-8B   # 临时换模型试
    QuickTranslate.exe --translate "Hello" --no-think             # 临时关闭思考链
    QuickTranslate.exe --translate "Hello" --no-fallback          # 临时禁用备用模型
    QuickTranslate.exe --ocr 截图.png                              # 截图 → OCR → 翻译
    QuickTranslate.exe --ocr 截图.png --lang japan --ocr-only      # 只做 OCR

`--ocr` 用来在不打开界面的情况下验证"截图取词"这条链路：
它会把识别出的原文打出来，再照常走翻译。排查"翻译质量差"时，
先看识别出的原文对不对 —— 空格丢了、断行没接，问题都在 OCR 而不是模型。

打包后的 `--windowed` 程序没有控制台，stdout 可能为空，
因此结果**同时**写入数据目录的 `last_translate.txt`，并打印该路径。
输出里会明确给出「思考阶段耗时 / 首译文延迟 / 总耗时」，
用来判断"卡在翻译中"到底是在思考还是真的挂了。
"""

from __future__ import annotations

import sys
import time

from .config import paths
from .config.profiles import DEFAULT_TIMEOUT, NO_THINKING_BODY, ProfileStore
from .core.errors import describe_failure, should_fallback
from .core.translator import (
    KIND_CONTENT,
    KIND_REASONING,
    create_fallback_translator,
    create_translator,
)

_LINES: list[str] = []


def _out(line: str = "") -> None:
    _LINES.append(line)
    if sys.stdout is not None:
        try:
            print(line)
        except Exception:  # noqa: BLE001  打包环境 stdout 可能是关闭的管道
            pass


def _flush() -> str:
    """把输出落盘，返回报告路径（打包后捞结果用）。"""
    target = paths.data_dir() / "last_translate.txt"
    try:
        target.write_text("\n".join(_LINES) + "\n", encoding="utf-8")
    except OSError:
        return ""
    return str(target)


def _arg_value(argv: list[str], flag: str, default: str = "") -> str:
    if flag in argv:
        idx = argv.index(flag)
        if idx + 1 < len(argv):
            return argv[idx + 1]
    return default


def _ocr_image_file(path: str, argv: list[str]) -> str:
    """对图片做 OCR，打印识别结果并返回文本（识别失败返回空串）。"""
    lang = _arg_value(argv, "--lang", "ch")
    _out(f"图片     : {path}")
    _out(f"识别语言 : {lang}")

    try:
        import numpy as np
        from PIL import Image

        from .core.ocr import is_available, ocr_image
    except Exception as exc:  # noqa: BLE001
        _out(f"加载 OCR 依赖失败：{type(exc).__name__}: {exc}")
        return ""

    if not is_available():
        _out("OCR 组件不可用（缺 rapidocr 或引擎起不来，先跑 --selftest）")
        return ""

    try:
        image = np.asarray(Image.open(path).convert("RGB"))
    except Exception as exc:  # noqa: BLE001
        _out(f"读取图片失败：{type(exc).__name__}: {exc}")
        return ""

    started = time.monotonic()
    try:
        text = ocr_image(image, lang)
    except Exception as exc:  # noqa: BLE001
        _out(f"识别失败：{type(exc).__name__}: {exc}")
        return ""
    elapsed = time.monotonic() - started

    _out(f"图片尺寸 : {image.shape[1]}x{image.shape[0]}")
    _out(f"识别用时 : {elapsed:.2f}s")
    if not text.strip():
        _out("识别结果 : （空 —— 图片里没有可识别的文字？）")
        return ""
    preview = text if len(text) <= 300 else text[:300] + "…"
    _out("识别结果 :")
    for line in preview.splitlines():
        _out(f"  {line}")
    return text


def run_translate(argv: list[str]) -> int:
    image_path = _arg_value(argv, "--ocr")
    text = _arg_value(argv, "--translate")
    ocr_only = "--ocr-only" in argv

    if image_path:
        text = _ocr_image_file(image_path, argv)
        if not text.strip():
            _flush()
            return 1
        if ocr_only:
            _out(f"报告     : {paths.data_dir() / 'last_translate.txt'}")
            _flush()
            return 0
        _out("-" * 60)
    elif not text:
        _out('用法：--translate "要翻译的文本" [--from 自动] [--to 中文] [--no-think]')
        _out("      --ocr 图片路径 [--lang ch|japan] [--ocr-only]")
        _flush()
        return 2

    source = _arg_value(argv, "--from", "自动")
    target = _arg_value(argv, "--to", "中文")
    wanted = _arg_value(argv, "--profile")
    model_override = _arg_value(argv, "--model")

    store = ProfileStore()
    name = wanted or store.first_name()
    profile = store.get(name)
    if profile is None:
        _out(f"找不到档案：{name}")
        _flush()
        return 1
    profile = dict(profile)
    if model_override:
        profile["model"] = model_override
    if "--no-think" in argv:
        profile["extra_body"] = NO_THINKING_BODY

    key = store.api_key(str(profile.get("name", "")))
    if not key.strip():
        _out(f"档案「{profile.get('name')}」没有配置 API Key")
        _flush()
        return 1

    _out(f"档案     : {profile.get('name')}")
    _out(f"模型     : {profile.get('model')}"
         f"{'（--model 覆盖）' if model_override else ''}")
    _out(f"方向     : {source} -> {target}")
    _out(f"额外参数 : {profile.get('extra_body') or '无'}")
    _out(f"响应超时 : {profile.get('timeout', 0):.0f}s")
    _out(f"原文     : {text[:80]}{'…' if len(text) > 80 else ''}")
    _out("-" * 60)

    try:
        translator = create_translator(profile, key)
        fallback = None if "--no-fallback" in argv else create_fallback_translator(profile, key)
    except Exception as exc:  # noqa: BLE001
        _out(f"初始化失败：{exc}")
        _flush()
        return 1

    def attempt(t) -> tuple[list[str], int, float | None, Exception | None, float]:
        """跑一次流式翻译，返回 (片段, 思考字数, 首译文耗时, 错误, 用时)。"""
        t0 = time.monotonic()
        parts: list[str] = []
        reasoning = 0
        first_at: float | None = None
        try:
            for kind, piece in t.translate_stream(text, source, target):
                if kind == KIND_REASONING:
                    reasoning += len(piece)
                    continue
                if kind != KIND_CONTENT:
                    continue
                if first_at is None:
                    first_at = time.monotonic() - t0
                parts.append(piece)
        except Exception as exc:  # noqa: BLE001
            return parts, reasoning, first_at, exc, time.monotonic() - t0
        return parts, reasoning, first_at, None, time.monotonic() - t0

    timeout_s = float(profile.get("timeout") or DEFAULT_TIMEOUT)
    primary = str(profile.get("model", ""))
    started = time.monotonic()
    parts, reasoning_chars, first_content_at, error, elapsed = attempt(translator)
    active_model = primary

    if error is not None and fallback is not None and should_fallback(error):
        _out(f"主模型 {primary} 不可用（{type(error).__name__}，已用 {elapsed:.1f}s），"
             f"改用备用模型 {fallback.model} 重试…")
        active_model = fallback.model
        parts, reasoning_chars, first_content_at, error, elapsed = attempt(fallback)

    if error is not None:
        _out("翻译失败：" + describe_failure(error, active_model, elapsed, timeout_s))
        if reasoning_chars:
            _out(f"（失败前模型已思考 {reasoning_chars} 字，用时 {elapsed:.1f}s）")
        _out(f"报告     : {paths.data_dir() / 'last_translate.txt'}")
        _flush()
        return 1

    result = "".join(parts).strip()
    _out(f"译文     : {result}")
    _out("-" * 60)
    if active_model != primary:
        _out(f"实际模型 : {active_model}（主模型不可用，已自动切换）")
    _out(f"思考     : {reasoning_chars} 字")
    _out(
        "首译文   : "
        + (f"{first_content_at:.2f}s" if first_content_at is not None else "未产出译文")
    )
    _out(f"总耗时   : {time.monotonic() - started:.2f}s")
    if not result:
        _out("提示：模型没有返回译文。若是思考型模型，试试加 --no-think 关闭思考链。")
    _out(f"报告     : {paths.data_dir() / 'last_translate.txt'}")
    _flush()
    return 0 if result else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run_translate(sys.argv))
