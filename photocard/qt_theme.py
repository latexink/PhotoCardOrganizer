from __future__ import annotations

import ctypes
import os

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QStyle


COLORS = {
    "background": "#202224",
    "surface": "#292c2f",
    "surface_alt": "#34383b",
    "sidebar": "#191c1d",
    "border": "#465055",
    "text": "#f2f4f5",
    "muted": "#aab4b6",
    "accent": "#43b99c",
    "accent_hover": "#59c9ac",
    "accent_pressed": "#2f9e83",
    "focus": "#8fe0cc",
    "selection": "#294941",
    "success": "#43b99c",
    "warning": "#e6b86b",
    "error": "#e78677",
}

WINDOWS_APP_USER_MODEL_ID = "PhotoCardOrganizer.Desktop"


APP_STYLESHEET = f"""
QWidget {{
    background: {COLORS['background']};
    color: {COLORS['text']};
    font-family: "Segoe UI", "Noto Sans", sans-serif;
    font-size: 10pt;
}}
QMainWindow, QDialog, QWizard {{
    background: {COLORS['background']};
}}
QFrame#header, QFrame#footer, QFrame#progressCenter, QFrame.surface {{
    background: {COLORS['surface']};
}}
QFrame#progressCenter {{
    border-top: 1px solid {COLORS['border']};
    border-bottom: 1px solid {COLORS['border']};
}}
QFrame#progressCenter QLabel {{
    background: transparent;
}}
QFrame.metric {{
    background: {COLORS['surface']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
}}
QFrame.preview {{
    background: {COLORS['surface_alt']};
    border: 1px solid {COLORS['border']};
    border-radius: 4px;
}}
QLabel {{
    background: transparent;
}}
QLabel#appTitle {{
    font-size: 16pt;
    font-weight: 600;
}}
QLabel#pageTitle {{
    font-size: 20pt;
    font-weight: 650;
}}
QLabel#dialogTitle {{
    font-size: 15pt;
    font-weight: 650;
}}
QLabel#metricValue {{
    font-size: 17pt;
    font-weight: 650;
}}
QLabel[class="muted"] {{
    color: {COLORS['muted']};
}}
QLabel[class="success"] {{ color: {COLORS['success']}; }}
QLabel[class="warning"] {{ color: {COLORS['warning']}; }}
QLabel[class="error"] {{ color: {COLORS['error']}; }}
QPushButton, QToolButton {{
    min-height: 32px;
    padding: 0 12px;
    background: {COLORS['surface_alt']};
    border: 1px solid {COLORS['border']};
    border-radius: 4px;
}}
QPushButton:hover, QToolButton:hover {{
    background: #3e4548;
}}
QPushButton:pressed, QToolButton:pressed {{
    background: #25292b;
}}
QPushButton:focus, QToolButton:focus {{
    border: 1px solid {COLORS['focus']};
}}
QPushButton:disabled, QToolButton:disabled {{
    color: #777777;
    background: #2a2e30;
}}
QPushButton[accent="true"] {{
    color: white;
    background: {COLORS['accent']};
    border: 1px solid {COLORS['accent']};
    font-weight: 600;
}}
QPushButton[accent="true"]:hover {{
    background: {COLORS['accent_hover']};
}}
QPushButton[accent="true"]:pressed {{
    background: {COLORS['accent_pressed']};
}}
QPushButton[accent="true"]:disabled {{
    color: #777777;
    background: #292929;
    border-color: {COLORS['border']};
}}
QPushButton[danger="true"] {{
    color: {COLORS['error']};
}}
QLineEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QComboBox, QTextEdit, QPlainTextEdit {{
    min-height: 30px;
    padding: 0 8px;
    background: {COLORS['surface_alt']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    border-radius: 4px;
    selection-background-color: {COLORS['selection']};
}}
QComboBox {{
    padding-right: 30px;
}}
QTextEdit, QPlainTextEdit {{
    padding: 7px;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid {COLORS['focus']};
}}
QComboBox::drop-down {{
    width: 28px;
    border: 0;
    border-left: 1px solid {COLORS['border']};
    background: transparent;
}}
QComboBox::drop-down:hover {{
    background: #3b4245;
}}
QComboBox::down-arrow {{
    image: url(:/qt-project.org/styles/commonstyle/images/arrow-down-16.png);
    width: 12px;
    height: 12px;
}}
QComboBox::down-arrow:on {{
    top: 1px;
}}
QComboBox QAbstractItemView {{
    background: {COLORS['surface_alt']};
    border: 1px solid {COLORS['border']};
    selection-background-color: {COLORS['selection']};
    outline: 0;
}}
QCheckBox {{
    spacing: 8px;
    min-height: 24px;
}}
QCheckBox::indicator {{
    width: 17px;
    height: 17px;
    border: 1px solid {COLORS['muted']};
    border-radius: 4px;
    background: {COLORS['surface_alt']};
}}
QCheckBox::indicator:hover {{
    border-color: {COLORS['text']};
}}
QCheckBox::indicator:checked {{
    background: {COLORS['accent']};
    border-color: {COLORS['accent']};
    image: url(:/qt-project.org/styles/commonstyle/images/standardbutton-apply-16.png);
}}
QGroupBox {{
    margin-top: 13px;
    padding: 16px 12px 12px 12px;
    border: 1px solid {COLORS['border']};
    border-radius: 5px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
    color: {COLORS['text']};
}}
QTabWidget::pane {{
    border: 0;
    background: {COLORS['background']};
}}
QTabBar::tab {{
    min-height: 34px;
    padding: 0 14px;
    color: {COLORS['muted']};
    background: {COLORS['surface']};
    border: 0;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{
    color: {COLORS['text']};
    background: {COLORS['surface_alt']};
    border-bottom: 2px solid {COLORS['accent']};
}}
QTabBar::tab:hover:!selected {{
    color: {COLORS['text']};
    background: #3b4245;
}}
QListWidget#navigation {{
    background: {COLORS['sidebar']};
    border: 0;
    outline: 0;
    padding: 8px;
}}
QListWidget#navigation::item {{
    min-height: 32px;
    padding: 0 10px;
    color: {COLORS['muted']};
    border-left: 3px solid transparent;
    border-radius: 4px;
}}
QListWidget#navigation::item:hover {{
    color: {COLORS['text']};
    background: {COLORS['surface']};
}}
QListWidget#navigation::item:selected {{
    color: {COLORS['text']};
    background: {COLORS['selection']};
    border-left: 3px solid {COLORS['focus']};
}}
QListWidget#navigation::item:disabled {{
    min-height: 22px;
    padding: 0 10px;
    color: #9f9f9f;
    background: transparent;
    border: 0;
    font-size: 8pt;
    font-weight: 650;
}}
QTableView, QTableWidget, QTreeWidget, QListView {{
    background: {COLORS['surface']};
    alternate-background-color: #2f3436;
    border: 1px solid {COLORS['border']};
    border-radius: 4px;
    gridline-color: {COLORS['border']};
    selection-background-color: {COLORS['selection']};
    selection-color: {COLORS['text']};
    outline: 0;
}}
QHeaderView::section {{
    min-height: 32px;
    padding: 0 8px;
    color: {COLORS['muted']};
    background: {COLORS['surface_alt']};
    border: 0;
    border-right: 1px solid {COLORS['border']};
    border-bottom: 1px solid {COLORS['border']};
    font-size: 9pt;
    font-weight: 600;
}}
QScrollArea {{
    border: 0;
    background: {COLORS['background']};
}}
QScrollArea > QWidget > QWidget {{
    background: {COLORS['background']};
}}
QScrollBar:vertical {{
    width: 10px;
    margin: 0;
    background: {COLORS['background']};
}}
QScrollBar::handle:vertical {{
    min-height: 28px;
    background: {COLORS['border']};
    border-radius: 5px;
}}
QScrollBar::handle:vertical:hover {{ background: #697779; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    height: 10px;
    margin: 0;
    background: {COLORS['background']};
}}
QScrollBar::handle:horizontal {{
    min-width: 28px;
    background: {COLORS['border']};
    border-radius: 5px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QProgressBar {{
    min-height: 6px;
    max-height: 6px;
    background: {COLORS['surface_alt']};
    border: 0;
    border-radius: 3px;
    text-align: center;
}}
QProgressBar::chunk {{
    background: {COLORS['accent']};
    border-radius: 3px;
}}
QProgressBar#persistentProgress {{
    min-height: 14px;
    max-height: 14px;
    border: 1px solid {COLORS['border']};
    border-radius: 4px;
}}
QSplitter::handle {{
    background: {COLORS['border']};
}}
QToolTip {{
    padding: 7px;
    color: {COLORS['text']};
    background: {COLORS['surface_alt']};
    border: 1px solid {COLORS['border']};
}}
QMenu {{
    padding: 5px;
    background: {COLORS['surface_alt']};
    border: 1px solid {COLORS['border']};
}}
QMenu::item {{
    min-height: 28px;
    padding: 0 24px 0 10px;
    border-radius: 3px;
}}
QMenu::item:selected {{ background: {COLORS['selection']}; }}
QWizard QFrame {{ background: transparent; }}
QMessageBox {{ background: {COLORS['background']}; }}
"""


def configure_application(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLESHEET)
    font = QFont("Segoe UI", 10)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(font)


def standard_icon(app: QApplication, name: str) -> QIcon:
    mapping = {
        "open": QStyle.StandardPixmap.SP_DirOpenIcon,
        "save": QStyle.StandardPixmap.SP_DialogSaveButton,
        "add": QStyle.StandardPixmap.SP_FileDialogNewFolder,
        "edit": QStyle.StandardPixmap.SP_FileDialogDetailedView,
        "remove": QStyle.StandardPixmap.SP_TrashIcon,
        "refresh": QStyle.StandardPixmap.SP_BrowserReload,
        "play": QStyle.StandardPixmap.SP_MediaPlay,
        "pause": QStyle.StandardPixmap.SP_MediaPause,
        "back": QStyle.StandardPixmap.SP_ArrowBack,
        "next": QStyle.StandardPixmap.SP_ArrowForward,
        "info": QStyle.StandardPixmap.SP_MessageBoxInformation,
        "warning": QStyle.StandardPixmap.SP_MessageBoxWarning,
        "settings": QStyle.StandardPixmap.SP_FileDialogContentsView,
        "close": QStyle.StandardPixmap.SP_DialogCloseButton,
    }
    return app.style().standardIcon(mapping.get(name, QStyle.StandardPixmap.SP_FileIcon))


def configure_windows_taskbar_identity(
    app_id: str = WINDOWS_APP_USER_MODEL_ID,
) -> bool:
    if os.name != "nt":
        return False
    try:
        setter = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        setter.argtypes = [ctypes.c_wchar_p]
        setter.restype = ctypes.c_long
        return setter(app_id) == 0
    except (AttributeError, OSError):
        return False


def _application_icon_pixmap(size: int) -> QPixmap:
    pixmap = QPixmap(QSize(size, size))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    scale = size / 256
    card = QPainterPath()
    card.moveTo(42 * scale, 30 * scale)
    card.lineTo(184 * scale, 30 * scale)
    card.lineTo(214 * scale, 60 * scale)
    card.lineTo(214 * scale, 214 * scale)
    card.quadTo(214 * scale, 226 * scale, 202 * scale, 226 * scale)
    card.lineTo(42 * scale, 226 * scale)
    card.quadTo(30 * scale, 226 * scale, 30 * scale, 214 * scale)
    card.lineTo(30 * scale, 42 * scale)
    card.quadTo(30 * scale, 30 * scale, 42 * scale, 30 * scale)
    card.closeSubpath()
    painter.fillPath(card, QColor(COLORS["accent"]))

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(COLORS["background"]))
    for x in (68, 94, 120, 146):
        painter.drawRoundedRect(int(x * scale), int(48 * scale), int(13 * scale), int(24 * scale), int(4 * scale), int(4 * scale))

    frame = QPainterPath()
    frame.addRoundedRect(62 * scale, 86 * scale, 140 * scale, 122 * scale, 6 * scale, 6 * scale)
    painter.fillPath(frame, QColor(COLORS["text"]))
    painter.setBrush(QColor(COLORS["error"]))
    painter.drawRect(int(76 * scale), int(99 * scale), int(112 * scale), int(20 * scale))
    painter.setBrush(QColor(COLORS["surface_alt"]))
    painter.drawRect(int(76 * scale), int(127 * scale), int(112 * scale), int(66 * scale))
    painter.setBrush(QColor(COLORS["accent"]))
    painter.drawPolygon(
        [
            QPointF(78 * scale, 184 * scale),
            QPointF(113 * scale, 145 * scale),
            QPointF(134 * scale, 166 * scale),
            QPointF(151 * scale, 151 * scale),
            QPointF(186 * scale, 184 * scale),
        ]
    )
    painter.setPen(QPen(QColor(COLORS["background"]), max(1, round(size * 0.018))))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(62 * scale, 86 * scale, 140 * scale, 122 * scale, 6 * scale, 6 * scale)
    painter.end()
    return pixmap


def application_icon(size: int | None = None) -> QIcon:
    icon = QIcon()
    sizes = (
        (size,)
        if size is not None
        else (16, 20, 24, 32, 40, 48, 64, 96, 128, 256, 512)
    )
    for icon_size in sizes:
        icon.addPixmap(_application_icon_pixmap(max(1, int(icon_size))))
    return icon
