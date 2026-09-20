"""OCR 基准测试：换模型 / 调参数之后，用它确认没有变差。

用法：
    python tools/ocr_bench.py              # 跑默认矩阵
    python tools/ocr_bench.py --quick      # 少跑几组，快速冒烟

输出：每个「裁切尺寸 × 字号」条件下的英文识别相似度与耗时，
以及汇总的「平均相似度 / 崩坏样本数 / 平均耗时」。

为什么要专门测这些：
    RapidOCR 的检测器对**输入构图**很敏感 —— 同样的模型，
    1040x180 的段落裁切能拿到完美结果，而 620x120 的小字块可能整行崩掉。
    只测一张图会得出完全错误的结论，必须扫一个矩阵。
"""

from __future__ import annotations

import argparse
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

SENTENCE = (
    "Why do so many people say that Chinese history is more glorious than "
    "British history, even though the British launched the Industrial Revolution "
    "and dominated global trade for over a century?"
)

_EN_FONTS = (r"C:\Windows\Fonts\arial.ttf", "/System/Library/Fonts/Helvetica.ttc")
_CN_FONTS = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
)

# (宽, 高) —— 覆盖用户可能的选区习惯：整段、小字块、宽而矮的单行
GEOMETRIES = [
    (620, 120),
    (1040, 180),
    (1100, 90),
    (1600, 200),
    (1900, 60),
]
SIZES = (12, 13, 16)

_QUICK_GEOMETRIES = [(1040, 180), (620, 120)]
_QUICK_SIZES = (13, 16)


def _font(candidates: tuple[str, ...], size: int):
    from PIL import ImageFont

    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return None


def render(width: int, height: int, size: int):
    """在 width×height 的画布上铺满该字号的英文，返回 (图, 期望文本)。"""
    from PIL import Image, ImageDraw

    font = _font(_EN_FONTS, size)
    if font is None:
        return None, ""

    words, lines, cur = SENTENCE.split(" "), [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if font.getlength(trial) <= width - 24:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    y, drawn, line_height = 6, [], int(size * 1.45)
    while y + line_height <= height - 4:
        line = lines[len(drawn) % len(lines)]
        draw.text((12, y), line, font=font, fill=(28, 28, 32))
        drawn.append(line)
        y += line_height
    return np.asarray(image), " ".join(drawn)


def similarity(got: str, want: str) -> float:
    norm = lambda s: " ".join(s.lower().split())  # noqa: E731
    return SequenceMatcher(None, norm(got), norm(want)).ratio()


def run(quick: bool) -> int:
    from quicktranslate.core.ocr import is_available, ocr_image

    if not is_available():
        print("OCR 组件不可用，先执行：python tools/prepare_ocr_models.py")
        return 1

    geometries = _QUICK_GEOMETRIES if quick else GEOMETRIES
    sizes = _QUICK_SIZES if quick else SIZES

    results: list[tuple[str, float, float]] = []
    print(f"{'条件':<22}{'相似度':>8}{'耗时':>9}")
    print("-" * 42)
    for width, height in geometries:
        for size in sizes:
            image, want = render(width, height, size)
            if image is None:
                continue
            if not want:
                continue
            started = time.monotonic()
            try:
                got = ocr_image(image, "ch")
            except Exception as exc:  # noqa: BLE001
                got = f"<异常 {exc!r}>"
            elapsed = time.monotonic() - started
            score = similarity(got, want)
            results.append((f"{width}x{height} @{size}px", score, elapsed))
            print(f"{width}x{height} @{size}px{'':<6}{score * 100:>6.1f}%{elapsed:>8.2f}s")

    if not results:
        print("没有可用样本（缺字体？）")
        return 1

    total = len(results)
    average = sum(r[1] for r in results) / total
    broken = sum(1 for r in results if r[1] < 0.8)
    mean_time = sum(r[2] for r in results) / total
    print("-" * 42)
    print(f"平均相似度 {average * 100:.1f}%   崩坏样本 {broken}/{total}   平均耗时 {mean_time:.2f}s")
    if broken:
        print("\n崩坏样本：")
        for name, score, _ in results:
            if score < 0.8:
                print(f"  - {name}: {score * 100:.0f}%")
    return 0 if broken == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="OCR 基准测试")
    parser.add_argument("--quick", action="store_true", help="只跑少量组合")
    args = parser.parse_args()
    return run(args.quick)


if __name__ == "__main__":
    raise SystemExit(main())
