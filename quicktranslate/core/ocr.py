"""本地 OCR：基于 RapidOCR（onnxruntime，无需 torch）。

兼容 rapidocr 3.x（新 API）与 rapidocr-onnxruntime（旧 API）两种包与返回结构。
优先使用 resources/ocr_models 下的离线模型；缺失时回退到引擎自带的自动下载。

模型选择说明（实测结论，别随手改回去）：
    PP-OCRv4 管道有两个叠加的短板 ——
    ① 检测模型在小字号下会把**整行英文漏掉**（14~22px 时相似度只剩 43%）；
    ② 中文识别模型给英文补空格很不稳定，长句会粘成
       ``BritishlaunchedtheIndustrialRevolution?``。
    换成 **PP-OCRv5 检测 + PP-OCRv5 中文识别** 后，12/14/16/19/22/28px ×
    有无 JPEG 失真的 12 种组合全部 98.8%~100%（平均 99.3%，原来 67.0%）。
    日文官方没有 v5 识别模型，继续用 v4 日文模型（配 v5 检测模型无退化）。
"""

from __future__ import annotations

import os
import re
import threading
import traceback
from typing import Any

from ..config import paths
from ..utils.logging import get_logger

log = get_logger("ocr")

# 界面语言标识 -> 显示名
OCR_LANGUAGES: dict[str, str] = {
    "ch": "中英",
    "japan": "日文",
}

# 各语言需要的识别模型与字典（元组内按优先级排列，命中即用；
# 保留 v4 文件名作为兜底，便于老版本资源目录继续可用）
_COMMON_FILES: dict[str, tuple[str, ...]] = {
    "det": ("det_v5.onnx", "det.onnx"),
    "cls": ("cls.onnx",),
}

_LANG_FILES: dict[str, dict[str, tuple[str, ...]]] = {
    "ch": {
        "rec": ("rec_ch_v5.onnx", "rec_ch.onnx"),
        "dict": ("dict_ch_v5.txt", "dict_ch.txt", "ppocr_keys_v1.txt"),
    },
    "japan": {
        "rec": ("rec_japan.onnx",),
        "dict": ("dict_japan.txt",),
    },
}

_engine: Any = None
_engine_lang: str | None = None
_lock = threading.Lock()

# 检测分辨率上限。
#
# RapidOCR 默认（limit_side_len=736, limit_type=min）会把图像**按最短边放大到 736**
# 再送检测，而垂直填充只在 宽/高 > 8 时才触发 —— 于是"宽而矮"的裁切图
# （例如 1040x180 的段落选区）会被放大 4~7 倍，文字高度变成 100px 上下，
# 检测器直接崩掉（整行漏检或切出碎片）。
#
# 实测（5 种真实裁切尺寸 × 9/10/12/13px 小字号，共 20 组）：
#     736（默认）  平均 71.7%   11/20 崩坏   平均 3.50s
#     512          平均 86.9%    6/20 崩坏   平均 2.47s   ← 采用
#     384          平均 83.1%    8/20 崩坏   平均 1.99s
# 512 准确度最高，同时比默认快约 29%（放大倍数小了，检测输入也小）。
DET_LIMIT_SIDE_LEN = 512

# 垂直填充的触发阈值（宽/高 大于它就给上下补黑边）。
#
# 库默认 8 太宽松：宽高比 4~8 的图（用户框选一两行时的常见形状）不补边，
# 随即被上面那条规则放大到 4 倍以上，文字反而变得过大。
# 调成 4 之后，这类图会先补边、再以接近原始的字号送检测。
#
# 实测（31 组「裁切尺寸 × 12/13/16px × 浅色/深色」）：
#     8（默认）  平均 95.9%   3/31 崩坏   平均 2.71s
#     4          平均 97.0%   2/31 崩坏   平均 2.09s   ← 采用
# 深色截图（YouTube 深色模式）单独验证过，同样无退化（深色场景全部 100%）。
# 变快是因为补边后最短边已超过 DET_LIMIT_SIDE_LEN，省掉了那一次放大。
DET_WIDTH_HEIGHT_RATIO = 4.0


# --------------------------------------------------------------------------- #
# 结果归一化
# --------------------------------------------------------------------------- #
def _split_result(result: Any) -> tuple[Any, Any]:
    """把两种包版本的返回值统一成 (boxes, txts)。"""
    if result is None:
        return None, None

    # 新 API：RapidOCROutput 对象
    txts = getattr(result, "txts", None)
    if txts is not None:
        return getattr(result, "boxes", None), txts

    # 旧 API：( [boxes, txts, scores], elapsed )
    if isinstance(result, (tuple, list)) and result:
        inner = result[0]
        if inner is None:
            return None, None
        if isinstance(inner, (list, tuple)) and len(inner) >= 2:
            return inner[0], inner[1]
    return None, None


def _reading_order(boxes: Any, txts: Any) -> list[str]:
    """按阅读顺序（上到下、左到右）重排文本。"""
    texts = [str(t) for t in txts]
    if boxes is None:
        return texts
    try:
        box_count = len(boxes)
    except TypeError:
        return texts
    if box_count != len(texts) or box_count == 0:
        return texts

    keyed: list[tuple[float, float, str]] = []
    heights: list[float] = []
    for box, text in zip(boxes, texts):
        try:
            points = list(box)
            ys = [float(p[1]) for p in points]
            xs = [float(p[0]) for p in points]
            keyed.append((min(ys), min(xs), text))
            heights.append(max(ys) - min(ys))
        except (TypeError, IndexError, ValueError):
            keyed.append((0.0, 0.0, text))

    if not keyed:
        return texts

    # 行高粗略估计：用平均框高作为行分组阈值
    line_tol = (sum(heights) / len(heights) * 0.6) if heights else 10.0
    keyed.sort(key=lambda it: (round(it[0] / max(line_tol, 1.0)), it[1]))
    return [it[2] for it in keyed]


# --------------------------------------------------------------------------- #
# 段落断行还原
# --------------------------------------------------------------------------- #
# 常见的"句子没说完"结尾词。命中它说明这一行是被排版折行的，不是段落结束。
_FUNCTION_WORDS = frozenset(
    """
    a an the this that these those
    of to in on at by for with from into over under about
    and or but nor so yet because though although while when where which who whom whose
    is are was were be been being am do does did have has had
    not no than as if then there here it its his her their our your my
    more most much many other another such same
    """.split()
)

_LATIN_LETTER = re.compile(r"[A-Za-z]")


def _is_latin(part: str) -> bool:
    """以拉丁字母开头／结尾（用于判断是不是英文句子被折断）。"""
    return bool(_LATIN_LETTER.match(part))


def _should_join(prev: str, current: str) -> bool:
    """判断 current 这一行是不是 prev 被排版折断后的延续。"""
    if not prev or not current:
        return False
    if not _is_latin(current[0]):
        return False  # 下一行不以拉丁字母开头，肯定不是英文断行

    # 1) 连字符断词：Indus- / trial → Industrial（要放在"以字母结尾"判断之前，
    #    因为这里 prev 的最后一个字符是连字符，不是字母）
    if prev.endswith("-") and not prev.endswith("--"):
        return current[0].islower()

    if not _is_latin(prev[-1]):
        return False  # 只处理英文（中文不用空格断词，硬折行也可能是有意的）

    # 2) 下一行以小写字母开头，且上一行已有一定长度 —— 典型的换行续写
    words = prev.split()
    if current[0].islower() and len(words) >= 3:
        return True

    # 3) 上一行以虚词结尾（"…even though the" / "…more glorious than"）：
    #    句子明显没说完，即使下一行是大写开头也要接上（常接专有名词）
    return bool(words) and words[-1].strip(",.;:!?").lower() in _FUNCTION_WORDS


def _join_wrapped_lines(lines: list[str]) -> list[str]:
    """把"被排版折行"的相邻行重新拼成一句，避免译文断成两截。

    只对英文行生效，且要求命中明确信号（见 ``_should_join``），
    因此像 ``Apple`` / ``Banana`` 这种逐项成行的列表不会被误合并。
    """
    merged: list[str] = []
    for line in lines:
        text = line.strip()
        if not text:
            continue
        if merged and _should_join(merged[-1], text):
            head = merged[-1]
            if head.endswith("-"):
                merged[-1] = head[:-1] + text  # 连字符断词：直接接上
            else:
                merged[-1] = f"{head} {text}"
        else:
            merged.append(text)
    return merged


# --------------------------------------------------------------------------- #
# 引擎构造
# --------------------------------------------------------------------------- #
def _first_existing(base: str, names: tuple[str, ...]) -> str:
    for name in names:
        path = os.path.join(base, name)
        if os.path.exists(path):
            return path
    return ""


def _local_models(lang: str) -> dict[str, str]:
    """返回离线模型路径（若齐备）。"""
    base = paths.ocr_models_dir()
    spec = _LANG_FILES.get(lang, _LANG_FILES["ch"])

    result: dict[str, str] = {}
    for kind, names in _COMMON_FILES.items():
        found = _first_existing(base, names)
        if found:
            result[kind] = found
    for kind, names in spec.items():
        found = _first_existing(base, names)
        if found:
            result[kind] = found

    if len(result) == 4:
        log.info(
            "使用离线 OCR 模型：%s（det=%s rec=%s）",
            base,
            os.path.basename(result["det"]),
            os.path.basename(result["rec"]),
        )
        if result["det"] == os.path.join(base, "det.onnx"):
            log.warning(
                "检测模型仍是 PP-OCRv4（det.onnx）："
                "小字号英文可能整行漏检，建议重跑 tools/prepare_ocr_models.py"
            )
        return result
    log.warning("离线 OCR 模型不完整（%s），将使用引擎自带模型（可能联网下载）", base)
    return {}


def _build_new_api(lang: str) -> Any:
    """rapidocr 3.x：RapidOCR(config_path=None, params={...})。

    已实测（rapidocr 3.9.2）：只传模型路径即可正常识别，无需设置 ocr_version
    （该键要求枚举类型，传字符串会报错）。

    这里直接导入 ``rapidocr.main`` 而不是 ``from rapidocr import RapidOCR``：
    后者依赖包内的 ``__getattr__`` 懒加载，在 PyInstaller 打包环境下更容易出问题。
    """
    from rapidocr.main import RapidOCR  # noqa: PLC0415

    models = _local_models(lang)
    if not models:
        return RapidOCR(params={"Global.log_level": "warning"})

    base_params = {
        "Global.log_level": "warning",
        "Global.width_height_ratio": DET_WIDTH_HEIGHT_RATIO,
        "Det.model_path": models["det"],
        "Det.limit_side_len": DET_LIMIT_SIDE_LEN,
        "Cls.model_path": models["cls"],
        "Rec.model_path": models["rec"],
    }
    # 字典键名在不同版本间有过调整，逐一尝试
    last_exc: Exception | None = None
    for dict_key in ("Rec.rec_keys_path", "Rec.dict_path", "Rec.keys_path"):
        params = {**base_params, dict_key: models["dict"]}
        try:
            return RapidOCR(params=params)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    if last_exc is not None:
        log.warning("离线模型参数不被接受（%s），回退默认模型", last_exc)
    return RapidOCR(params={"Global.log_level": "warning"})


def _build_old_api(lang: str) -> Any:
    from rapidocr_onnxruntime import RapidOCR  # noqa: PLC0415

    models = _local_models(lang)
    if not models:
        return RapidOCR()
    try:
        return RapidOCR(
            det_model_path=models["det"],
            cls_model_path=models["cls"],
            rec_model_path=models["rec"],
            rec_keys_path=models["dict"],
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("旧 API 离线模型参数失败（%s），回退默认模型", exc)
        return RapidOCR()


def _build_engine(lang: str) -> Any:
    try:
        return _build_new_api(lang)
    except ImportError:
        log.warning(
            "导入 rapidocr 失败，改用旧包 rapidocr_onnxruntime：\n%s", traceback.format_exc()
        )
    except Exception:
        log.exception("初始化 rapidocr 引擎失败")
        raise

    try:
        return _build_old_api(lang)
    except ImportError as exc:
        raise RuntimeError(
            "未找到可用的 OCR 引擎（需要 rapidocr；旧包 rapidocr_onnxruntime 也已停更）"
        ) from exc


def get_engine(lang: str = "ch") -> Any:
    """获取（懒加载）指定语言的 OCR 引擎单例。"""
    global _engine, _engine_lang
    with _lock:
        if _engine is None or _engine_lang != lang:
            log.info("初始化 OCR 引擎：lang=%s", lang)
            _engine = _build_engine(lang)
            _engine_lang = lang
        return _engine


# --------------------------------------------------------------------------- #
# 对外接口
# --------------------------------------------------------------------------- #
def ocr_image(image: Any, lang: str = "ch") -> str:
    """对 numpy RGB 图像做 OCR，返回按阅读顺序拼接的文本。

    会顺带把"被排版折断的英文行"重新接成一句：截图里的英文段落常常在
    ``…even though the`` / ``British launched…`` 处硬换行，直接丢给模型
    会译成两截。
    """
    engine = get_engine(lang)
    result = engine(image)
    boxes, txts = _split_result(result)
    if not txts:
        return ""
    return "\n".join(_join_wrapped_lines(_reading_order(boxes, txts)))


def warmup(lang: str = "ch") -> bool:
    """预热引擎（在后台线程调用，避免首次识别卡顿）。返回是否成功。"""
    try:
        get_engine(lang)
        return True
    except Exception:  # noqa: BLE001
        log.exception("OCR 预热失败")
        return False


def is_available() -> bool:
    """OCR 是否真的可用：直接试导入入口类，避免"包在但引擎起不来"的假阳性。"""
    try:
        from rapidocr.main import RapidOCR  # noqa: F401, PLC0415

        return True
    except Exception:  # noqa: BLE001
        log.debug("rapidocr 不可用：\n%s", traceback.format_exc())
    try:
        from rapidocr_onnxruntime import RapidOCR  # noqa: F401, PLC0415

        return True
    except Exception:  # noqa: BLE001
        return False
