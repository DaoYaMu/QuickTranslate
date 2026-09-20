# -*- mode: python ; coding: utf-8 -*-
"""QuickTranslate PyInstaller 打包配置。

用法（在项目根目录）：
    python -m PyInstaller --noconfirm --clean build/quicktranslate.spec
单文件模式：
    设置环境变量 QT_ONEFILE=1 后再执行上面的命令

说明：
- 默认产出 onedir（目录版，启动快、易排错），通过 QT_ONEFILE=1 切换为单文件。
- 模型文件放在 resources/ocr_models，需先执行 tools/prepare_ocr_models.py。
"""

import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

PROJECT_ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))  # noqa: F821
ONEFILE = os.environ.get("QT_ONEFILE", "0") == "1"
APP_NAME = "QuickTranslate"

# --------------------------------------------------------------------------- #
# 资源文件
# --------------------------------------------------------------------------- #
# 注意：dest 必须带 "resources/" 前缀，才能与 paths.resource_path() 的
# 运行时查找路径（<MEIPASS>/resources/ocr_models）一致。
datas = []
for folder in ("ocr_models", "icons"):
    path = os.path.join(PROJECT_ROOT, "resources", folder)
    if os.path.isdir(path) and os.listdir(path):
        datas.append((path, os.path.join("resources", folder)))

# rapidocr 自带的配置/字典等数据文件
try:
    datas += collect_data_files("rapidocr")
except Exception:
    pass

# --------------------------------------------------------------------------- #
# 隐藏导入
# --------------------------------------------------------------------------- #
hiddenimports = [
    "openai",
    "httpx",
    "httpx2",
    "httpcore",
    "httpcore2",
    "certifi",
    "pyperclip",
    "PIL",
    "PIL.Image",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "keyring.backends",
    "keyring.backends.Windows",
    "keyring.backends.macOS",
    "keyring.backends.SecretService",
    "onnxruntime",
    "numpy",
    "cv2",
    # rapidocr 走 __getattr__ 懒加载，显式列出入口与其顶层依赖，避免被打包器漏掉
    "rapidocr.main",
    "rapidocr.utils",
    "rapidocr.utils.typings",
    "rapidocr.utils.model_resolver",
    "rapidocr.ch_ppocr_det",
    "rapidocr.ch_ppocr_rec",
    "rapidocr.ch_ppocr_cls",
    "rapidocr.cal_rec_boxes",
    "rapidocr.inference_engine.onnxruntime",
    "yaml",
    # rapidocr 的检测后处理在顶层 import shapely.geometry，缺了整个 rapidocr 都起不来
    "shapely",
    "shapely.geometry",
]

if sys.platform == "win32":
    hiddenimports += ["pynput.keyboard._win32", "pynput.mouse._win32"]
elif sys.platform == "darwin":
    hiddenimports += ["pynput.keyboard._darwin", "pynput.mouse._darwin"]
else:
    hiddenimports += ["pynput.keyboard._xorg", "pynput.mouse._xorg"]

for pkg in ("onnxruntime", "rapidocr", "pynput", "keyring.backends"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass

# --------------------------------------------------------------------------- #
# 排除项（控制体积）
# --------------------------------------------------------------------------- #
excludes = [
    "torch",
    "torchvision",
    "tensorflow",
    "scipy",
    "matplotlib",
    "pandas",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "tkinter",
    "IPython",
    "notebook",
    "pytest",
    "setuptools._distutils",
    # 注意：不要排除 shapely —— rapidocr 的检测后处理依赖它
]

# --------------------------------------------------------------------------- #
icon_file = os.path.join(PROJECT_ROOT, "resources", "icons", "app.ico")
icon_arg = icon_file if os.path.exists(icon_file) else None

a = Analysis(  # noqa: F821
    [os.path.join(PROJECT_ROOT, "main.py")],
    pathex=[PROJECT_ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

# --------------------------------------------------------------------------- #
# 体积裁剪：只删确定的二进制/翻译资源，不动任何 Python 模块
# --------------------------------------------------------------------------- #
# 精确文件名（小写比较）
_DROP_EXACT = {
    # 只用 QtWidgets，不需要 QML / Quick / PDF / 软件 OpenGL
    "opengl32sw.dll",
    "qt6quick.dll",
    "qt6qml.dll",
    "qt6qmlmodels.dll",
    "qt6qmlworkerscript.dll",
    "qt6quickwidgets.dll",
    "qt6quicktemplates2.dll",
    "qt6quickshapes.dll",
    "qt6quicklayouts.dll",
    "qt6quickdialogs2.dll",
    "qt6quickdialogs2quickimpl.dll",
    "qt6quickdialogs2utils.dll",
    "qt6quickcontrols2.dll",
    "qt6quickcontrols2impl.dll",
    "qt6pdf.dll",
    "qt6pdfwidgets.dll",
    "qt6opengl.dll",
    "qt6openglwidgets.dll",
}
# 前缀匹配
_DROP_PREFIX = (
    "opencv_videoio_ffmpeg",  # OpenCV 视频编解码，OCR 用不到（约 30MB）
)


def _keep(dest: str, src: str = "") -> bool:
    """判断一个资源是否保留。

    dest 形如 'PySide6/translations/qt_ar.qm' 或 'models/PP-OCRv6_det_small.onnx'
    （collect_data_files 产生的 dest 是相对包目录的路径，不一定带包名前缀），
    所以这里同时看源路径，避免漏判。
    """
    norm = dest.replace("\\", "/").lower().lstrip("/")
    srcnorm = src.replace("\\", "/").lower()
    name = norm.rsplit("/", 1)[-1]

    if name in _DROP_EXACT:
        return False
    if name.startswith(_DROP_PREFIX):
        return False
    # Qt 自带 96 种语言翻译，只保留简体中文（qt_zh_CN / qtbase_zh_CN）
    if "pyside6/translations/" in norm and "zh_cn" not in name:
        return False
    # rapidocr/models 是它的“联网下载缓存”目录，不是随包资源；
    # 本程序自带 resources/ocr_models 离线模型，无需重复打包这些缓存
    if name.endswith(".onnx") and "rapidocr/models/" in srcnorm:
        return False
    return True


def _keep_item(item) -> bool:
    # TOC 条目形如 (dest, src, typecode)；datas 里也可能出现二元组
    dest = item[0]
    src = item[1] if len(item) > 1 else ""
    return _keep(str(dest), str(src))


a.binaries = [item for item in a.binaries if _keep_item(item)]
a.datas = [item for item in a.datas if _keep_item(item)]

pyz = PYZ(a.pure)  # noqa: F821

if ONEFILE:
    exe = EXE(  # noqa: F821
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        icon=icon_arg,
    )
    if sys.platform == "darwin":
        app = BUNDLE(  # noqa: F821
            exe,
            name=f"{APP_NAME}.app",
            icon=icon_arg,
            bundle_identifier="com.quicktranslate",
        )
else:
    exe = EXE(  # noqa: F821
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        icon=icon_arg,
    )
    coll = COLLECT(  # noqa: F821
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name=APP_NAME,
    )
    if sys.platform == "darwin":
        app = BUNDLE(  # noqa: F821
            coll,
            name=f"{APP_NAME}.app",
            icon=icon_arg,
            bundle_identifier="com.quicktranslate",
        )
