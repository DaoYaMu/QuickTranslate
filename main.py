"""QuickTranslate 程序入口。"""

from __future__ import annotations

import sys
import traceback

from PySide6.QtWidgets import QApplication, QMessageBox

from quicktranslate import __app_name__, __version__
from quicktranslate.app import Application
from quicktranslate.ui import theme as theme_mod
from quicktranslate.ui.appicon import build_icon
from quicktranslate.utils.logging import get_logger, setup_logging


def main() -> int:
    # 打包后排查问题的第一入口：不依赖桌面交互，自检依赖 / 路径 / OCR
    if "--selftest" in sys.argv:
        from quicktranslate.selftest import run as run_selftest

        return run_selftest()

    # 命令行翻译 / OCR：无需 GUI，用来验证 API 链路与"截图取词"链路
    if "--translate" in sys.argv or "--ocr" in sys.argv:
        from quicktranslate.cli import run_translate

        return run_translate(sys.argv)

    logger = setup_logging()
    logger.info("%s %s 启动", __app_name__, __version__)

    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    app.setApplicationDisplayName("QuickTranslate")
    app.setOrganizationName(__app_name__)
    app.setStyle("Fusion")  # 统一各平台控件外观，保证 QSS 一致
    app.setWindowIcon(build_icon())
    app.setFont(theme_mod.app_font())

    try:
        controller = Application(app)
    except Exception:  # noqa: BLE001
        logger.critical("初始化失败：\n%s", traceback.format_exc())
        QMessageBox.critical(None, "QuickTranslate", f"启动失败：\n{traceback.format_exc()}")
        return 1

    controller.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
