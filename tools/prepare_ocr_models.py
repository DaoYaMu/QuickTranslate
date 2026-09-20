"""下载离线 OCR 模型到 resources/ocr_models。

用法：
    python tools/prepare_ocr_models.py            # 下载全部（中英 / 日文）
    python tools/prepare_ocr_models.py --lang ch  # 只下载中英
    python tools/prepare_ocr_models.py --force    # 覆盖已存在的文件

模型来源：ModelScope RapidAI/RapidOCR（Apache-2.0，模型版权归百度 PaddleOCR）。
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.request
from pathlib import Path

_BASE = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve"

# 目标文件名 -> 下载地址（目标名与 quicktranslate/core/ocr.py 的约定一致）
#
# 为什么用 PP-OCRv5 而不是 v4（实测数据，合成图 + 序列相似度打分）：
#   · v4 的**检测**模型在 14~22px 的小字号下会把整行英文漏掉（只有 43% 相似度）；
#   · v4 的**中文识别**模型给英文加空格很不稳定
#     （"BritishlaunchedtheIndustrialRevolution?"）；
#   · v5 检测 + v5 中文识别在 12/14/16/19/22/28px × 有无 JPEG 失真共 12 种条件下
#     全部 98.8%~100%，平均 99.3%；v4 组合只有 67.0%。
# 日文 PP-OCRv5 官方未提供，仍用 v4 日文识别模型（配 v5 检测模型无退化）。
_CATALOG: dict[str, dict[str, str]] = {
    "common": {
        "det_v5.onnx": f"{_BASE}/v3.9.2/onnx/PP-OCRv5/det/ch_PP-OCRv5_det_mobile.onnx",
        "cls.onnx": f"{_BASE}/v3.4.0/onnx/PP-OCRv4/cls/ch_ppocr_mobile_v2.0_cls_infer.onnx",
    },
    "ch": {
        "rec_ch_v5.onnx": f"{_BASE}/v3.9.2/onnx/PP-OCRv5/rec/ch_PP-OCRv5_rec_mobile.onnx",
        "dict_ch_v5.txt": f"{_BASE}/v3.9.2/paddle/PP-OCRv5/rec/ch_PP-OCRv5_rec_mobile/ppocrv5_dict.txt",
    },
    "japan": {
        "rec_japan.onnx": f"{_BASE}/v3.4.0/onnx/PP-OCRv4/rec/japan_PP-OCRv4_rec_infer.onnx",
        "dict_japan.txt": f"{_BASE}/v2.0.7/paddle/PP-OCRv4/rec/japan_PP-OCRv4_rec_infer/japan_dict.txt",
    },
}

# 已被 v5 取代、需要清理的旧模型文件
_OBSOLETE = ("det.onnx", "rec_ch.onnx", "dict_ch.txt")

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_TARGET_DIR = _PROJECT_ROOT / "resources" / "ocr_models"


def _human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}"
        size /= 1024  # type: ignore[assignment]
    return f"{size}B"


def download(name: str, url: str, force: bool = False) -> bool:
    target = _TARGET_DIR / name
    if target.exists() and not force and target.stat().st_size > 0:
        print(f"  · 已存在，跳过：{name}")
        return True

    tmp = target.with_suffix(target.suffix + ".part")
    try:
        print(f"  ↓ 下载 {name} …", end="", flush=True)
        with urllib.request.urlopen(url, timeout=90) as response:  # noqa: S310
            total = int(response.headers.get("Content-Length") or 0)
            chunk = 1024 * 256
            done = 0
            with open(tmp, "wb") as fh:
                while True:
                    data = response.read(chunk)
                    if not data:
                        break
                    fh.write(data)
                    done += len(data)
        os.replace(tmp, target)
        print(f"\r  ✓ {name}  ({_human(target.stat().st_size)})")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"\r  ✗ {name} 失败：{exc}")
        tmp.unlink(missing_ok=True)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="下载离线 OCR 模型")
    parser.add_argument(
        "--lang",
        nargs="+",
        default=["ch", "japan"],
        choices=["ch", "japan"],
        help="要下载的识别语言",
    )
    parser.add_argument("--force", action="store_true", help="覆盖已存在的文件")
    args = parser.parse_args()

    _TARGET_DIR.mkdir(parents=True, exist_ok=True)
    print(f"目标目录：{_TARGET_DIR}")

    # 清掉被 v5 取代的旧模型，避免新旧混用（体积也更小）
    for name in _OBSOLETE:
        stale = _TARGET_DIR / name
        if stale.exists():
            try:
                stale.unlink()
                print(f"  ✗ 删除过时模型：{name}")
            except OSError as exc:
                print(f"  ! 删除 {name} 失败：{exc}")

    jobs: dict[str, str] = dict(_CATALOG["common"])
    for lang in args.lang:
        jobs.update(_CATALOG[lang])

    ok = fail = 0
    for name, url in jobs.items():
        if download(name, url, force=args.force):
            ok += 1
        else:
            fail += 1

    print(f"\n完成：成功 {ok} 个，失败 {fail} 个")
    if fail:
        print(
            "提示：失败通常是网络问题，可重试；"
            "中英识别只需 det_v5 / cls / rec_ch_v5 / dict_ch_v5 四个文件。"
        )
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
