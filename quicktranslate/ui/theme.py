"""浅色 / 深色主题（QSS）与配色常量。"""

from __future__ import annotations

import os

from PySide6.QtGui import QFont

LIGHT: dict[str, str] = {
    "bg": "#F4F6FA",
    "card": "#FFFFFF",
    "card_alt": "#FBFCFE",
    "border": "#E2E8F0",
    "border_strong": "#CBD5E1",
    "text": "#1F2937",
    "text_muted": "#6B7280",
    "text_faint": "#9CA3AF",
    "accent": "#2F6FED",
    "accent_hover": "#255FD8",
    "accent_pressed": "#1E4FBA",
    "accent_soft": "#E8F0FE",
    "accent_text": "#FFFFFF",
    "hover": "#EEF2F8",
    "pressed": "#E2E8F0",
    "input_bg": "#FFFFFF",
    "selection": "#C9DBFF",
    "danger": "#DC2626",
    "warn": "#B45309",
    "ok": "#15803D",
    "shadow": "rgba(15, 23, 42, 0.08)",
    "scrollbar": "#D6DEE9",
    "scrollbar_hover": "#BAC6D6",
}

DARK: dict[str, str] = {
    "bg": "#17181B",
    "card": "#212327",
    "card_alt": "#26282D",
    "border": "#33363C",
    "border_strong": "#43474E",
    "text": "#E6E8EC",
    "text_muted": "#A2A9B4",
    "text_faint": "#7A828E",
    "accent": "#4C8DFF",
    "accent_hover": "#5E9AFF",
    "accent_pressed": "#3F7BE6",
    "accent_soft": "#22304A",
    "accent_text": "#FFFFFF",
    "hover": "#2A2D33",
    "pressed": "#31353C",
    "input_bg": "#1C1E22",
    "selection": "#2E4A7D",
    "danger": "#F87171",
    "warn": "#FBBF24",
    "ok": "#4ADE80",
    "shadow": "rgba(0, 0, 0, 0.45)",
    "scrollbar": "#3A3E45",
    "scrollbar_hover": "#4C515A",
}

THEMES = {"light": LIGHT, "dark": DARK}


def palette(name: str) -> dict[str, str]:
    return THEMES.get(name, LIGHT)


def font_family() -> str:
    # Qt 会按顺序回退，第一个可用即生效
    return '"Microsoft YaHei UI", "PingFang SC", "Segoe UI", "Noto Sans SC", sans-serif'


def app_font(size: int = 10) -> QFont:
    f = QFont()
    f.setFamilies(
        ["Microsoft YaHei UI", "PingFang SC", "Segoe UI", "Noto Sans SC", "Sans Serif"]
    )
    f.setPointSize(size)
    return f


# --------------------------------------------------------------------------- #
# 矢量图标：Qt 的 QSS 不支持用 CSS 边框画三角形，必须用图片
# --------------------------------------------------------------------------- #
_GLYPH_CACHE: dict[str, str] = {}


def _glyph_dir() -> str:
    from ..config import paths as paths_mod  # 局部导入，避免循环依赖

    target = paths_mod.cache_dir() / "ui"
    target.mkdir(parents=True, exist_ok=True)
    return str(target)


def _draw_glyph(key: str, color: str, points: list[tuple[float, float]], width: float) -> str:
    """把折线画成 14×14 的透明 PNG 并缓存，返回 QSS url() 可用的路径。"""
    if key in _GLYPH_CACHE:
        return _GLYPH_CACHE[key]

    try:
        from PySide6.QtCore import QPointF, Qt  # noqa: PLC0415
        from PySide6.QtGui import QColor, QPainter, QPen, QPixmap  # noqa: PLC0415

        pixmap = QPixmap(14, 14)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(color))
        pen.setWidthF(width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPolyline([QPointF(x, y) for x, y in points])
        painter.end()

        path = os.path.join(_glyph_dir(), f"{key}.png")
        pixmap.save(path)
        _GLYPH_CACHE[key] = path.replace("\\", "/")
    except Exception:  # noqa: BLE001  无 GUI 环境（如纯逻辑测试）时静默降级
        _GLYPH_CACHE[key] = ""

    return _GLYPH_CACHE[key]


def _arrow_icon(theme: str) -> str:
    """下拉框箭头（朝下的 V）。"""
    return _draw_glyph(
        f"chevron_{theme}",
        palette(theme)["text_muted"],
        [(3.6, 5.6), (7.0, 9.0), (10.4, 5.6)],
        1.7,
    )


def _check_icon(theme: str) -> str:
    """勾选框对勾。"""
    return _draw_glyph(
        f"check_{theme}",
        palette(theme)["accent_text"],
        [(3.4, 7.4), (5.9, 9.9), (10.6, 4.3)],
        2.0,
    )


def _image_rule(selector: str, path: str, extra: str = "") -> str:
    if not path:
        return ""
    return f"{selector} {{\n    image: url(\"{path}\");\n{extra}}}\n"


def build_qss(theme: str = "light") -> str:
    c = palette(theme)
    fam = font_family()

    arrow_qss = _image_rule(
        "QComboBox::down-arrow",
        _arrow_icon(theme),
        "    width: 14px;\n    height: 14px;\n    margin-right: 5px;\n",
    )
    check_qss = _image_rule("QCheckBox::indicator:checked", _check_icon(theme))

    return f"""
* {{
    font-family: {fam};
    outline: none;
}}

QWidget {{
    color: {c['text']};
    font-size: 13px;
}}

QMainWindow, QDialog {{
    background: {c['bg']};
}}

QWidget#Root {{
    background: {c['bg']};
}}

/* ---------- 卡片 ---------- */
QFrame#Card {{
    background: {c['card']};
    border: 1px solid {c['border']};
    border-radius: 12px;
}}
QFrame#Header {{
    background: {c['card']};
    border: 1px solid {c['border']};
    border-radius: 12px;
}}
QFrame#Divider {{
    background: {c['border']};
    border: none;
    max-height: 1px;
    min-height: 1px;
}}

/* ---------- 文本 ---------- */
QLabel#Title {{
    font-size: 17px;
    font-weight: 600;
    color: {c['text']};
}}
QLabel#PanelTitle {{
    font-size: 12px;
    font-weight: 600;
    color: {c['text_muted']};
    letter-spacing: 0.5px;
}}
QLabel#Muted {{
    color: {c['text_muted']};
}}
QLabel#Faint {{
    color: {c['text_faint']};
    font-size: 12px;
}}
QLabel#Error {{
    color: {c['danger']};
}}
QLabel#Ok {{
    color: {c['ok']};
}}

/* ---------- 输入控件 ---------- */
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit, QTextBrowser {{
    background: {c['input_bg']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    padding: 6px 8px;
    selection-background-color: {c['selection']};
    selection-color: {c['text']};
}}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
    border-color: {c['border_strong']};
}}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {c['accent']};
}}
QPlainTextEdit, QTextEdit, QTextBrowser {{
    padding: 10px 12px;
    line-height: 160%;
}}
QComboBox::drop-down {{
    border: none;
    width: 26px;
}}
{arrow_qss}QComboBox QAbstractItemView {{
    background: {c['card']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    padding: 4px;
    selection-background-color: {c['accent_soft']};
    selection-color: {c['text']};
    outline: none;
}}

QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 16px;
    border: none;
    background: transparent;
}}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: {c['card_alt']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    padding: 7px 14px;
    color: {c['text']};
}}
QPushButton:hover {{
    background: {c['hover']};
    border-color: {c['border_strong']};
}}
QPushButton:pressed {{
    background: {c['pressed']};
}}
QPushButton:disabled {{
    color: {c['text_faint']};
    background: {c['card_alt']};
    border-color: {c['border']};
}}

QPushButton#Primary {{
    background: {c['accent']};
    border: 1px solid {c['accent']};
    color: {c['accent_text']};
    font-weight: 600;
    padding: 8px 22px;
}}
QPushButton#Primary:hover {{
    background: {c['accent_hover']};
    border-color: {c['accent_hover']};
}}
QPushButton#Primary:pressed {{
    background: {c['accent_pressed']};
}}
QPushButton#Primary:disabled {{
    background: {c['border']};
    border-color: {c['border']};
    color: {c['text_faint']};
}}

QPushButton#Danger {{
    color: {c['danger']};
}}
QPushButton#Danger:hover {{
    background: {c['hover']};
    border-color: {c['danger']};
}}

/* ---------- 热键录入框 ---------- */
QPushButton#HotkeyField {{
    background: {c['card_alt']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    padding: 7px 12px;
    color: {c['text']};
    text-align: left;
    font-family: "Consolas", "Cascadia Mono", monospace;
}}
QPushButton#HotkeyField:hover {{
    background: {c['hover']};
    border-color: {c['border_strong']};
}}
QPushButton#HotkeyField[recording="true"] {{
    background: {c['accent_soft']};
    border: 1px solid {c['accent']};
    color: {c['accent']};
}}

QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 6px 10px;
    color: {c['text_muted']};
}}
QToolButton:hover {{
    background: {c['hover']};
    color: {c['text']};
}}
QToolButton:checked {{
    background: {c['accent_soft']};
    color: {c['accent']};
}}
QToolButton#SwapButton {{
    font-size: 15px;
    padding: 4px 10px;
    color: {c['text_muted']};
}}
QToolButton#SwapButton:hover {{
    background: {c['accent_soft']};
    color: {c['accent']};
}}

/* ---------- 勾选 / 标签页 ---------- */
QCheckBox, QRadioButton {{
    spacing: 8px;
    color: {c['text']};
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {c['border_strong']};
    border-radius: 4px;
    background: {c['input_bg']};
}}
QRadioButton::indicator {{
    border-radius: 8px;
}}
QCheckBox::indicator:checked {{
    background: {c['accent']};
    border-color: {c['accent']};
}}
{check_qss}QRadioButton::indicator:checked {{
    background: {c['accent']};
    border-color: {c['accent']};
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {c['accent']};
}}

QTabWidget::pane {{
    border: 1px solid {c['border']};
    border-radius: 10px;
    top: -1px;
    background: {c['card']};
}}
QTabBar::tab {{
    background: transparent;
    color: {c['text_muted']};
    padding: 8px 18px;
    margin-right: 4px;
    border: 1px solid transparent;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
}}
QTabBar::tab:hover {{
    color: {c['text']};
    background: {c['hover']};
}}
QTabBar::tab:selected {{
    color: {c['accent']};
    background: {c['card']};
    border-color: {c['border']};
    border-bottom-color: {c['card']};
    font-weight: 600;
}}

/* ---------- 列表 / 表格 ---------- */
QListWidget, QListView, QTreeWidget, QTableWidget {{
    background: {c['card']};
    border: 1px solid {c['border']};
    border-radius: 10px;
    padding: 4px;
    outline: none;
}}
QListWidget::item, QListView::item {{
    padding: 8px 10px;
    border-radius: 8px;
    color: {c['text']};
}}
QListWidget::item:hover, QListView::item:hover {{
    background: {c['hover']};
}}
QListWidget::item:selected, QListView::item:selected {{
    background: {c['accent_soft']};
    color: {c['text']};
}}
/* 历史列表：条目由自定义控件填充，内边距交给控件自己处理，避免两行文字被压重叠 */
QListWidget#HistoryList::item {{
    padding: 0px;
    margin: 3px 0px;
}}
QHeaderView::section {{
    background: {c['card_alt']};
    color: {c['text_muted']};
    padding: 6px 8px;
    border: none;
    border-bottom: 1px solid {c['border']};
}}

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 4px 2px 4px 2px;
}}
QScrollBar::handle:vertical {{
    background: {c['scrollbar']};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {c['scrollbar_hover']};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px 4px;
}}
QScrollBar::handle:horizontal {{
    background: {c['scrollbar']};
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {c['scrollbar_hover']};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0; width: 0; background: none; border: none;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
}}

/* ---------- 菜单 / 托盘 ---------- */
QMenu {{
    background: {c['card']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    padding: 6px;
}}
QMenu::item {{
    padding: 7px 22px 7px 14px;
    border-radius: 6px;
    color: {c['text']};
}}
QMenu::item:selected {{
    background: {c['accent_soft']};
    color: {c['text']};
}}
QMenu::separator {{
    height: 1px;
    background: {c['border']};
    margin: 5px 8px;
}}

/* ---------- 其它 ---------- */
QStatusBar {{
    background: transparent;
    color: {c['text_muted']};
}}
QToolTip {{
    background: {c['card']};
    color: {c['text']};
    border: 1px solid {c['border']};
    border-radius: 6px;
    padding: 5px 8px;
}}
QGroupBox {{
    border: 1px solid {c['border']};
    border-radius: 10px;
    margin-top: 12px;
    padding: 14px 12px 12px 12px;
    color: {c['text_muted']};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 5px;
}}
QSplitter::handle {{
    background: transparent;
    width: 10px;
    height: 10px;
}}
"""
