"""统一日志：打包为 --windowed 后没有 stdout，日志必须落盘。"""

from __future__ import annotations

import logging
import sys

from ..config import paths

_LOGGER_NAME = "quicktranslate"
_configured = False


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    if _configured:
        return logger

    logger.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    try:
        fh = logging.FileHandler(paths.log_path(), encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass

    # 开发环境（有控制台）额外输出到 stderr
    if sys.stderr is not None and not paths.is_frozen():
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    logger.propagate = False
    _configured = True
    return logger


def get_logger(name: str = "") -> logging.Logger:
    return logging.getLogger(f"{_LOGGER_NAME}.{name}" if name else _LOGGER_NAME)
