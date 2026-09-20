"""路径工具：资源定位（兼容 PyInstaller）与可写数据目录。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "QuickTranslate"
ORG_NAME = "QuickTranslate"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包后的环境。"""
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    """项目根目录（开发环境）。"""
    return Path(__file__).resolve().parents[2]


def resource_path(*parts: str) -> str:
    """定位只读资源（模型、图标）。

    打包后资源被解压到 ``sys._MEIPASS``，开发时位于项目根目录下。
    """
    if is_frozen():
        base = Path(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    else:
        base = project_root()
    return str(base.joinpath("resources", *parts))


def data_dir() -> Path:
    """可写数据目录（配置、数据库、日志、模型缓存）。"""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    target = base / APP_NAME
    target.mkdir(parents=True, exist_ok=True)
    return target


def cache_dir() -> Path:
    """缓存目录（OCR 模型下载缓存等）。"""
    target = data_dir() / "cache"
    target.mkdir(parents=True, exist_ok=True)
    return target


def profiles_path() -> str:
    return str(data_dir() / "profiles.json")


def fallback_keys_path() -> str:
    """keyring 不可用时，混淆存储 API Key 的兜底文件。"""
    return str(data_dir() / "keys.json")


def history_db_path() -> str:
    return str(data_dir() / "history.db")


def log_path() -> str:
    return str(data_dir() / "quicktranslate.log")


def ocr_models_dir() -> str:
    """OCR 模型目录。

    依次尝试（命中即返回）：
    1. ``<MEIPASS>/resources/ocr_models``（打包布局，与开发布局一致）
    2. ``<MEIPASS>/ocr_models``（旧 spec 的 dest，兼容历史构建）
    3. ``<项目根>/resources/ocr_models``（开发环境）
    4. 可写数据目录下的 ``ocr_models``（兜底，供用户自行放模型）
    """
    candidates: list[str] = []
    if is_frozen():
        meipass = Path(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
        candidates += [
            str(meipass / "resources" / "ocr_models"),
            str(meipass / "ocr_models"),
        ]
    candidates.append(str(project_root() / "resources" / "ocr_models"))

    for candidate in candidates:
        if _has_onnx(candidate):
            return candidate

    target = data_dir() / "ocr_models"
    target.mkdir(parents=True, exist_ok=True)
    return str(target)


def _has_onnx(folder: str) -> bool:
    """目录存在且含有 .onnx 模型文件。"""
    try:
        return any(name.endswith(".onnx") for name in os.listdir(folder))
    except OSError:
        return False


def icon_path(name: str = "app.ico") -> str:
    p = resource_path("icons", name)
    return p if os.path.exists(p) else ""
