"""OCR 测试：先用 PIL 生成含文字的图片，再识别并比对。

覆盖三件事：
    1. 中文 / 日文识别覆盖率（原有能力没退化）；
    2. **英文词间空格还原** —— PP-OCRv4 会把长句粘成
       ``BritishlaunchedtheIndustrialRevolution?``，换 v5 后必须正常；
    3. **段落折行还原** —— 截图里被排版折断的英文行要接回成一句。

前置：已执行 python tools/prepare_ocr_models.py
用法：python tests/test_ocr.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quicktranslate.core.ocr import (  # noqa: E402
    OCR_LANGUAGES,
    _join_wrapped_lines,  # noqa: PLR2701  测试内部纯函数
    is_available,
    ocr_image,
)

_WIN_FONTS = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
]

_EN_FONT = r"C:\Windows\Fonts\arial.ttf"

_EN_SAMPLE = (
    "Why do so many people say that Chinese history is more glorious than "
    "British history, even though the British launched the Industrial Revolution?"
)


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in _WIN_FONTS:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _render(lines: list[str], path: str) -> None:
    font = _load_font(40)
    image = Image.new("RGB", (760, 90 * len(lines) + 60), "white")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        draw.text((30, 30 + index * 90), line, fill="black", font=font)
    image.save(path)


def _check_spacing(failures: list[str]) -> None:
    """英文词间空格：小字号也要把单词分对。"""
    if not Path(_EN_FONT).exists():
        print("  [英文空格] 跳过：未找到 arial.ttf")
        return

    font = ImageFont.truetype(_EN_FONT, 16)
    image = Image.new("RGB", (760, 200), "white")
    draw = ImageDraw.Draw(image)
    # 手工折行，模拟网页正文（同时覆盖"检测模型漏行"这个 v4 的老毛病）
    wrapped = [
        "Why do so many people say that Chinese history is more",
        "glorious than British history, even though the British",
        "launched the Industrial Revolution?",
    ]
    for index, line in enumerate(wrapped):
        draw.text((14, 14 + index * 26), line, fill=(28, 28, 32), font=font)

    text = ocr_image(np.asarray(image), "ch")
    flat = " ".join(text.lower().replace(",", " ").split())
    want = _EN_SAMPLE.lower().replace("?", "").replace(",", "").split()
    missing = [w for w in want if w not in flat.split()]
    keep = 1 - len(missing) / max(len(want), 1)
    print(f"  [英文空格] 单词完整率 {keep:.0%}；识别结果：{text!r}")
    if keep < 0.85:
        failures.append(f"英文空格还原不足 {keep:.0%}，缺失单词 {missing[:6]}")
    if "\n" in text:
        failures.append(f"折行的英文段落没有被接回一句：{text!r}")


def _check_wrap_join(failures: list[str]) -> None:
    """折行还原：命中信号才合并，列表不能被误合并。"""
    cases: list[tuple[list[str], list[str], str]] = [
        (
            ["Why do so many people say that Chinese history is more", "glorious than British history."],
            ["Why do so many people say that Chinese history is more glorious than British history."],
            "小写续行应合并",
        ),
        (
            ["even though the", "British launched it."],
            ["even though the British launched it."],
            "虚词结尾的续行应合并",
        ),
        (
            ["the Indus-", "trial Revolution began."],
            ["the Industrial Revolution began."],
            "连字符断词应拼接且去掉连字符",
        ),
        (
            ["Apple", "Banana"],
            ["Apple", "Banana"],
            "逐项成行的列表不能合并",
        ),
        (
            ["第一行中文。", "第二行中文。"],
            ["第一行中文。", "第二行中文。"],
            "中文硬折行保持原样",
        ),
    ]
    for raw, expected, label in cases:
        got = _join_wrapped_lines(list(raw))
        ok = got == expected
        print(f"  [{label}] {'OK' if ok else f'FAIL {got!r}'}")
        if not ok:
            failures.append(f"{label}：期望 {expected!r}，实际 {got!r}")


def main() -> int:
    if not is_available():
        print("test_ocr: SKIP（未安装 rapidocr）")
        return 0

    cases = {
        "ch": ["你好，世界", "今天天气不错"],
        "japan": ["こんにちは世界"],
    }

    failures: list[str] = []
    _check_wrap_join(failures)

    with tempfile.TemporaryDirectory() as tmp:
        for lang, lines in cases.items():
            label = OCR_LANGUAGES.get(lang, lang)
            path = str(Path(tmp) / f"{lang}.png")
            _render(lines, path)
            image = np.asarray(Image.open(path).convert("RGB"))

            try:
                text = ocr_image(image, lang)
            except FileNotFoundError as exc:
                print(f"  [{label}] 跳过：模型缺失（{exc}）")
                continue
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{label}: 识别异常 {exc!r}")
                continue

            flat = text.replace("\n", "").replace(" ", "")
            expected = "".join(lines).replace(" ", "")
            hit = sum(1 for ch in set(expected) if ch in flat)
            ratio = hit / max(len(set(expected)), 1)
            print(f"  [{label}] 识别：{text!r}  覆盖率 {ratio:.0%}")
            if ratio < 0.6:
                failures.append(f"{label}: 识别覆盖率过低 {ratio:.0%}")

    _check_spacing(failures)

    if failures:
        print("test_ocr: FAIL")
        for item in failures:
            print("   -", item)
        return 1

    print("test_ocr: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
