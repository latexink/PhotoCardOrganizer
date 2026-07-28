from __future__ import annotations

import ctypes
import os

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QStyle


COLORS = {
    "background": "#202020",
    "surface": "#2b2b2b",
    "surface_alt": "#323232",
    "sidebar": "#181818",
    "border": "#484848",
    "text": "#f5f5f5",
    "muted": "#bdbdbd",
    "accent": "#0f6cbd",
    "accent_hover": "#1975c5",
    "accent_pressed": "#0b5a9d",
    "focus": "#60cdff",
    "selection": "#29465c",
    "success": "#36a269",
    "warning": "#f2c94c",
    "error": "#ff7b86",
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
    background: #3a3a3a;
}}
QPushButton:pressed, QToolButton:pressed {{
    background: #272727;
}}
QPushButton:focus, QToolButton:focus {{
    border: 1px solid {COLORS['focus']};
}}
QPushButton:disabled, QToolButton:disabled {{
    color: #777777;
    background: #292929;
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
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
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
    background: #353535;
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
    background: #353535;
}}
QListWidget#navigation {{
    background: {COLORS['sidebar']};
    border: 0;
    outline: 0;
    padding: 8px;
}}
QListWidget#navigation::item {{
    min-height: 42px;
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
QTableWidget, QTreeWidget, QListView {{
    background: {COLORS['surface']};
    alternate-background-color: #2e2e2e;
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
QScrollBar::handle:vertical:hover {{ background: #676767; }}
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
    path = QPainterPath()
    margin = size * 0.08
    path.addRoundedRect(margin, margin, size - 2 * margin, size - 2 * margin, size * 0.16, size * 0.16)
    painter.fillPath(path, QColor(COLORS["accent"]))
    painter.setPen(QPen(QColor("white"), max(1, round(size * 0.07)), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(int(size * 0.28), int(size * 0.25), int(size * 0.28), int(size * 0.75))
    painter.drawLine(int(size * 0.28), int(size * 0.25), int(size * 0.55), int(size * 0.25))
    painter.drawArc(int(size * 0.39), int(size * 0.38), int(size * 0.33), int(size * 0.33), 35 * 16, 290 * 16)
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
