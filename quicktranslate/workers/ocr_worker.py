"""OCR 工作线程：在后台执行识别（首次会加载模型，耗时较长）。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThread, Signal

from ..core.ocr import ocr_image, warmup
from ..utils.logging import get_logger

log = get_logger("worker.ocr")


class OcrWorker(QThread):
    recognized = Signal(str)
    failed = Signal(str)

    def __init__(self, image: Any, lang: str = "ch", parent=None) -> None:
        super().__init__(parent)
        self._image = image
        self._lang = lang

    def run(self) -> None:  # noqa: D102
        try:
            text = ocr_image(self._image, self._lang)
            self.recognized.emit(text)
        except Exception as exc:  # noqa: BLE001
            log.exception("OCR 失败")
            self.failed.emit(str(exc))


class WarmupWorker(QThread):
    """后台预热 OCR 引擎，减少首次截图识别的等待。"""

    done = Signal(bool)

    def __init__(self, lang: str = "ch", parent=None) -> None:
        super().__init__(parent)
        self._lang = lang

    def run(self) -> None:  # noqa: D102
        self.done.emit(bool(warmup(self._lang)))
