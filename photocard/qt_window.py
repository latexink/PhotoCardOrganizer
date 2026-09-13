from __future__ import annotations

import copy
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QDate, QEvent, QItemSelectionModel, QObject, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QColor, QDesktopServices, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDateEdit,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QSystemTrayIcon,
    QTabWidget,
    QTableWidget,
    QTableView,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .config import (
    DEFAULT_USER_AGENT,
    export_client_settings,
    get_card_profile,
    import_client_settings,
    library_destination,
    normalize_config,
    normalize_digest_inbox,
    remove_card_profile,
    save_config,
    select_library_destination,
    upsert_card_profile,
)
from .digest import DigestRunResult, run_digest_profile
from .discovery import capacity_for, discover_cards, folder_import_source, write_card_identity
from .manifest import ImportManifest
from .library_tools import (
    CaptureGroup,
    CaptureSet,
    build_capture_sets,
    detect_capture_groups,
    export_captures,
    filter_captures,
    scan_library,
)
from .library_state import (
    library_metadata_status,
    metadata_path as library_metadata_path,
    upgrade_library_metadata,
)
from .models import ActivityEvent, CardMarker, DecisionRequest, DecisionResult, ImportStats
from .monitor import MonitorService
from .organizer import MAX_DESTINATION_REDIRECTS, Organizer
from .progress import timing_text
from .reorganization import ReorganizationOrganizer
from .qt_library_tools import LazyTableModel, LibraryJobDialog, ReorganizationDialog
from .qt_integrity import IntegrityPanel
from .qt_common import (
    CAMERA_NAME_HELP,
    IMPORT_FOLDER_MODES,
    LIBRARY_SUBFOLDER_HELP,
    MEDIA_LABELS,
    ORGANIZATION_PRESETS,
    format_bytes,
    initial_import_summary,
    import_destination_prefix,
    path_key,
    paths_overlap,
    preset_folder_segments,
    profile_for_card,
)
from .qt_dialogs import (
    CardOnboardingWizard,
    CardProfileDialog,
    DecisionDialog,
    DigestInboxDialog,
    InstallationDialog,
    LibraryDestinationDialog,
    ReplicaDialog,
    StructureMappingDialog,
    TransferHubDialog,
    TravelLibraryDialog,
    choice_combo,
    combo_value,
    directory_editor,
    is_yes,
    set_combo_data,
)
from .qt_theme import (
    COLORS,
    application_icon,
    configure_application,
    configure_windows_taskbar_identity,
    standard_icon,
)
from .single_instance import SingleInstance
from .structure_detection import (
    MAX_EDITABLE_SOURCE_LEVELS,
    StructureAnalysis,
    detect_existing_structure,
)
from .templates import SEGMENT_LABELS, TEMPLATE_LABELS, safe_segment
from .transfer_hub import (
    catch_sources,
    effective_replica_destinations,
    producer_channel,
    publish_library,
    receipt_status,
    source_entries,
    write_digestion_receipts,
)


PAGE_NAMES = (
    "Dashboard",
    "Libraries",
    "Cards and drives",
    "Import or merge",
    "Digest inboxes",
    "Travel sync",
    "Library export",
    "Organization",
    "Integrity",
    "Safety and location",
    "Conflict review",
    "Activity",
    "General options",
    "Help & about",
)

NAVIGATION_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "WORKSPACE",
        (
            "Dashboard",
            "Libraries",
            "Cards and drives",
            "Import or merge",
            "Digest inboxes",
        ),
    ),
    (
        "LIBRARY TOOLS",
        (
            "Organization",
            "Travel sync",
            "Library export",
            "Integrity",
            "Conflict review",
            "Activity",
        ),
    ),
    ("SETTINGS", ("Safety and location", "General options", "Help & about")),
)

NAVIGATION_TOOLTIPS = {
    "Integrity": "Verify saved checksums, create missing baselines, and review integrity reports.",
    "General options": "Monitoring interval, portable settings, and application maintenance.",
    "Dashboard": "Connected sources, capacity, and reviewed import actions.",
    "Libraries": "Set up destinations, choose a default, import or merge files, and maintain library metadata.",
    "Cards and drives": "Onboard cards and retain their identity and source settings.",
    "Import or merge": "Add files from another folder to a managed library after reviewing the destination and source-file handling.",
    "Digest inboxes": "Retain mixed incoming folders, digest new files incrementally, and review per-file state.",
    "Travel sync": "Bring new laptop or travel-drive media into the desktop master with copy-only reconciliation.",
    "Library export": "Select captures and detected bracket, burst, or interval groups for verified editing exports.",
    "Organization": "Configure per-media destination folders, filenames, and session-record names.",
    "Safety and location": "Configure verification, duplicates, low space, backups, logs, and place names.",
    "Conflict review": "Compare preserved filename conflicts side by side and mark them reviewed.",
    "Activity": "Review transfer, warning, and error events from this application session.",
    "Help & about": "Open the included manual and view release, project, and credit information.",
}

NAVIGATION_ICONS = {
    "Integrity": "info",
    "General options": "settings",
    "Dashboard": "info",
    "Libraries": "settings",
    "Cards and drives": "open",
    "Import or merge": "add",
    "Digest inboxes": "play",
    "Travel sync": "refresh",
    "Library export": "save",
    "Organization": "settings",
    "Safety and location": "warning",
    "Conflict review": "warning",
    "Activity": "info",
    "Help & about": "info",
}


def set_dynamic_class(widget: QWidget, name: str) -> None:
    widget.setProperty("class", name)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def accent(button: QPushButton) -> QPushButton:
    button.setProperty("accent", True)
    return button


class ScrollWheelRedirector(QObject):
    def __init__(self, area: QScrollArea):
        super().__init__(area)
        self.area = area

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if event.type() != QEvent.Type.Wheel:
            return False
        pixel_delta = event.pixelDelta().y()
        angle_delta = event.angleDelta().y()
        if not pixel_delta and not angle_delta:
            return False
        scrollbar = self.area.verticalScrollBar()
        if pixel_delta:
            distance = pixel_delta
        else:
            distance = round(
                angle_delta
                / 120
                * max(1, QApplication.wheelScrollLines())
                * max(1, scrollbar.singleStep())
            )
        scrollbar.setValue(scrollbar.value() - distance)
        event.accept()
        return True


def scrollable(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    area.setWidget(widget)
    redirector = ScrollWheelRedirector(area)
    for control_type in (QComboBox, QDoubleSpinBox, QSpinBox):
        for control in widget.findChildren(control_type):
            control.installEventFilter(redirector)
    area._wheel_redirector = redirector
    return area


class AspectPreviewLabel(QLabel):
    def __init__(self, text: str = ""):
        super().__init__(text)
        self._source_pixmap = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)

    def set_preview_pixmap(self, pixmap: QPixmap) -> None:
        self._source_pixmap = pixmap
        self.setText("")
        self._fit_pixmap()

    def clear_preview(self, text: str) -> None:
        self._source_pixmap = QPixmap()
        super().setPixmap(QPixmap())
        self.setText(text)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._fit_pixmap()

    def _fit_pixmap(self) -> None:
        if self._source_pixmap.isNull():
            return
        target = self.contentsRect().size() - QSize(12, 12)
        if target.width() <= 0 or target.height() <= 0:
            return
        super().setPixmap(
            self._source_pixmap.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


def open_local_path(path: Path, *, create: bool = False) -> None:
    if create:
        path.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        raise OSError(f"The path does not exist: {path}")
    if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve()))):
        raise OSError(f"The operating system could not open: {path}")


def directory_available(path: Path | str) -> bool:
    try:
        return bool(str(path).strip()) and Path(path).expanduser().is_dir()
    except OSError:
        return False


class PhotoCardApp(QMainWindow):
    def __init__(
        self,
        config: dict,
        config_path: Path,
        *,
        start_minimized: bool = False,
        instance_guard: SingleInstance | None = None,
    ):
        super().__init__()
        self.config = config
        self.config_path = config_path
        self.start_minimized = start_minimized
        self.instance_guard = instance_guard
        self.events: queue.Queue[ActivityEvent] = queue.Queue()
        self.decisions: queue.Queue[tuple[DecisionRequest, dict, threading.Event]] = queue.Queue()
        self.ui_actions: queue.Queue[Callable[[], None]] = queue.Queue()
        self.card_by_id: dict[str, CardMarker] = {}
        self.profile_by_id: dict[str, dict] = {}
        self.replica_destinations = copy.deepcopy(config.get("replica_destinations", []))
        self.library_destinations = copy.deepcopy(
            config.get("library_destinations", [])
        )
        self.default_library_id = str(
            config.get("default_library_id", "")
        )
        self.travel_libraries = copy.deepcopy(config.get("travel_libraries", []))
        self.transfer_hubs = copy.deepcopy(config.get("transfer_hubs", []))
        self.digest_inboxes = copy.deepcopy(config.get("digest_inboxes", []))
        self.conflict_records: dict[str, dict] = {}
        self.library_captures: list[CaptureSet] = []
        self.library_groups: list[CaptureGroup] = []
        self.media_controls: dict[str, dict] = {}
        self.existing_media_checks: dict[str, QCheckBox] = {}
        self.existing_structure_rules: dict[str, list[str]] | None = None
        self.existing_structure_analysis: StructureAnalysis | None = None
        self._manual_import_running = False
        self._quitting = False
        self._shutdown_complete = False
        self._tray_available = False
        self._settings_dirty = False
        self._dirty_tracking_ready = False
        self._loading_controls = False
        self.tray: QSystemTrayIcon | None = None

        self.setWindowTitle("Photo Card Organizer")
        self.setWindowIcon(application_icon())
        self.resize(1220, 800)
        self.setMinimumSize(980, 660)
        self._build_ui()

        if self.instance_guard is not None:
            self.instance_guard.start_activation_server(
                lambda: self.ui_actions.put(self.show_window)
            )

        self.monitor = MonitorService(self.config, self.events.put)
        self._setup_tray()
        self.monitor.start()
        self.event_timer = QTimer(self)
        self.event_timer.setInterval(150)
        self.event_timer.timeout.connect(self._drain_queues)
        self.event_timer.start()
        self.refresh_cards()
        self.show_page("Dashboard")
        if self.start_minimized and self._tray_available:
            self.hide()
        else:
            self.show()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 14, 24, 14)
        title = QLabel("Photo Card Organizer")
        title.setObjectName("appTitle")
        self.status_label = QLabel("Starting monitor")
        set_dynamic_class(self.status_label, "muted")
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        header_layout.addWidget(self.status_label)
        root_layout.addWidget(header)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        sidebar = QFrame()
        sidebar.setFixedWidth(215)
        sidebar.setStyleSheet(f"background: {COLORS['sidebar']};")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 4, 0, 4)
        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.navigation_items: dict[str, QListWidgetItem] = {}
        app = QApplication.instance()
        for section_name, page_names in NAVIGATION_SECTIONS:
            section = QListWidgetItem(section_name)
            section.setData(Qt.ItemDataRole.UserRole, None)
            section.setFlags(Qt.ItemFlag.NoItemFlags)
            section.setSizeHint(QSize(0, 22))
            section.setToolTip(f"{section_name.title()} navigation")
            self.navigation.addItem(section)
            for name in page_names:
                item = QListWidgetItem(name)
                if app is not None:
                    item.setIcon(standard_icon(app, NAVIGATION_ICONS[name]))
                item.setData(Qt.ItemDataRole.UserRole, name)
                item.setSizeHint(QSize(0, 30))
                item.setToolTip(NAVIGATION_TOOLTIPS[name])
                self.navigation.addItem(item)
                self.navigation_items[name] = item
        self.navigation.currentRowChanged.connect(self._navigation_changed)
        sidebar_layout.addWidget(self.navigation, 1)
        body_layout.addWidget(sidebar)

        self.stack = QStackedWidget()
        body_layout.addWidget(self.stack, 1)
        self.page_indexes: dict[str, int] = {}
        self._build_dashboard_page()
        self._build_library_management_page()
        self._build_cards_page()
        self._build_existing_library_page()
        self._build_digest_inboxes_page()
        self._build_travel_sync_page()
        self._build_library_export_page()
        self._build_organization_page()
        self._build_integrity_page()
        self._build_safety_page()
        self._build_conflict_page()
        self._build_activity_page()
        self._build_general_page()
        self._build_help_page()
        self._apply_tooltips()
        root_layout.addWidget(body, 1)
        root_layout.addWidget(self._build_progress_center())
        root_layout.addWidget(self._build_footer())
        self._connect_settings_dirty_tracking()
        self._set_settings_dirty(False)

    def _build_progress_center(self) -> QFrame:
        progress_center = QFrame()
        progress_center.setObjectName("progressCenter")
        layout = QHBoxLayout(progress_center)
        layout.setContentsMargins(24, 5, 24, 5)
        layout.setSpacing(14)
        caption = QLabel("PROGRESS")
        set_dynamic_class(caption, "muted")
        caption.setMinimumWidth(72)
        layout.addWidget(caption)
        self.progress_label = QLabel("Ready")
        self.progress_label.setMinimumWidth(120)
        self.progress_label.setMaximumWidth(420)
        self.progress_label.setWordWrap(True)
        self.progress_label.setToolTip(
            "ETA estimates the current source from completed files. Rates are "
            "average application payload I/O, including copy verification reads, "
            "not physical disk speed. Large files and filesystem caching affect estimates."
        )
        layout.addWidget(self.progress_label)
        self.transfer_progress = QProgressBar()
        self.transfer_progress.setObjectName("persistentProgress")
        self.transfer_progress.setRange(0, 1)
        self.transfer_progress.setValue(0)
        self.transfer_progress.setTextVisible(True)
        self.transfer_progress.setMinimumWidth(160)
        self.transfer_progress.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        layout.addWidget(self.transfer_progress, 1)
        return progress_center

    def _build_integrity_page(self) -> None:
        _page, layout = self._new_page("Integrity", "Integrity")
        self.integrity_panel = IntegrityPanel(self, lambda: self.config, self._begin_integrity,
                                              lambda job: self.monitor.run_exclusive(job), self._finish_integrity)
        for button, icon in ((self.integrity_panel.verify, "refresh"), (self.integrity_panel.create, "add"),
                             (self.integrity_panel.choose, "open"), (self.integrity_panel.stop, "stop"),
                             (self.integrity_panel.open_report, "open")):
            self._add_icon(button, icon)
        layout.addWidget(self.integrity_panel, 1)

    def _begin_integrity(self):
        if self._manual_import_running:
            QMessageBox.information(self, "Operation in progress", "Finish the current operation first.")
            return None
        saved = self._saved_processing_config("checking library integrity")
        if saved is None:
            return None
        self._integrity_was_paused = self.monitor.is_paused
        self.monitor.set_paused(True)
        self._manual_import_running = True
        return saved

    def _finish_integrity(self):
        self._manual_import_running = False
        if not self._integrity_was_paused:
            self.monitor.set_paused(False)

    def _open_integrity(self):
        selected = self._selected_library_destination()
        self.show_page("Integrity")
        self.integrity_panel.refresh(selected["id"] if selected else None)

    def _build_general_page(self) -> None:
        _page, layout = self._new_page("General options", "General options")
        content = QWidget()
        form = QFormLayout(content)
        form.setContentsMargins(0, 0, 12, 16)
        form.setVerticalSpacing(18)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.poll_spin = self._number_spin(
            self.config["monitor"]["poll_seconds"], 1, 3600, decimals=1, suffix=" sec"
        )
        form.addRow("Connected-card check interval", self.poll_spin)
        self.idle_scan_spin = self._number_spin(
            self.config["monitor"].get("idle_scan_max_seconds", 300), 30, 86400, decimals=0, suffix=" sec"
        )
        self.idle_scan_spin.setToolTip(
            "Unchanged cards are scanned less often, up to this interval. Scan now checks immediately. "
            "Higher values reduce disk activity but delay detecting new files on a still-connected source."
        )
        form.addRow("Maximum idle-card scan interval", self.idle_scan_spin)
        self.import_settings_button = QPushButton("Import settings")
        self._add_icon(self.import_settings_button, "open")
        self.import_settings_button.setToolTip("Merge portable settings from another computer after confirmation, retaining this computer's local paths.")
        self.import_settings_button.clicked.connect(self._import_client_settings)
        self.export_settings_button = QPushButton("Export settings")
        self._add_icon(self.export_settings_button, "save")
        self.export_settings_button.setToolTip("Create a portable settings file with machine-specific paths removed.")
        self.export_settings_button.clicked.connect(self._export_client_settings)
        settings_row = QWidget()
        settings_layout = QHBoxLayout(settings_row)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.addWidget(self.import_settings_button)
        settings_layout.addWidget(self.export_settings_button)
        settings_layout.addStretch(1)
        form.addRow("Settings transfer", settings_row)
        self.maintenance_button = QPushButton("Installation and maintenance")
        self._add_icon(self.maintenance_button, "settings")
        self.maintenance_button.setToolTip("View the detected installation and open install, repair, update, or uninstall options.")
        self.maintenance_button.clicked.connect(self._installation_maintenance)
        form.addRow("Application", self.maintenance_button)
        layout.addWidget(scrollable(content), 1)

    def _build_footer(self) -> QFrame:
        footer = QFrame()
        footer.setObjectName("footer")
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(24, 9, 24, 9)
        layout.setSpacing(8)
        app = QApplication.instance()
        self.open_library_button = QPushButton("Open library")
        self.open_library_button.setIcon(standard_icon(app, "open"))
        self.open_library_button.setToolTip("Open the primary destination library.")
        self.open_library_button.clicked.connect(self._open_library)
        layout.addWidget(self.open_library_button)
        layout.addStretch(1)
        self.settings_state_label = QLabel("Settings saved")
        set_dynamic_class(self.settings_state_label, "muted")
        layout.addWidget(self.settings_state_label)
        self.save_button = QPushButton("Save settings")
        self.save_button.setIcon(standard_icon(app, "save"))
        self.save_button.clicked.connect(self.save_settings)
        layout.addWidget(self.save_button)
        return footer

    def _connect_settings_dirty_tracking(self) -> None:
        if self._dirty_tracking_ready:
            return
        for control in self.centralWidget().findChildren(QLineEdit):
            control.textChanged.connect(self._update_settings_dirty_state)
        for control in self.centralWidget().findChildren(QComboBox):
            control.currentIndexChanged.connect(
                self._update_settings_dirty_state
            )
            control.currentTextChanged.connect(
                self._update_settings_dirty_state
            )
        for control in self.centralWidget().findChildren(QCheckBox):
            control.toggled.connect(self._update_settings_dirty_state)
        for control in self.centralWidget().findChildren(QSpinBox):
            control.valueChanged.connect(self._update_settings_dirty_state)
        for control in self.centralWidget().findChildren(QDoubleSpinBox):
            control.valueChanged.connect(self._update_settings_dirty_state)
        self._dirty_tracking_ready = True

    def _set_settings_dirty(self, dirty: bool) -> None:
        self._settings_dirty = bool(dirty)
        if not hasattr(self, "save_button"):
            return
        self.save_button.setProperty("accent", self._settings_dirty)
        self.settings_state_label.setText(
            "Unsaved changes" if self._settings_dirty else "Settings saved"
        )
        self.save_button.setToolTip(
            "Validate and save the changed settings."
            if self._settings_dirty
            else "All configurable settings are saved."
        )
        self.save_button.style().unpolish(self.save_button)
        self.save_button.style().polish(self.save_button)

    def _update_settings_dirty_state(self, *_args) -> None:
        if not self._dirty_tracking_ready or self._loading_controls:
            return
        try:
            dirty = self._collect_config() != self.config
        except (OSError, ValueError):
            dirty = True
        self._set_settings_dirty(dirty)

    def _saved_processing_config(self, action: str) -> dict | None:
        if self._settings_dirty:
            QMessageBox.information(
                self,
                "Save settings first",
                f"Save Settings is highlighted because configuration changes "
                f"have not been saved.\n\nSave them before {action}.",
            )
            return None
        return normalize_config(copy.deepcopy(self.config))

    def _new_page(self, name: str, title: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(12)
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet(f"color: {COLORS['border']};")
        layout.addWidget(separator)
        self.page_indexes[name] = self.stack.addWidget(page)
        return page, layout

    @staticmethod
    def _add_icon(button: QPushButton, name: str) -> None:
        app = QApplication.instance()
        if app is not None:
            button.setIcon(standard_icon(app, name))

    def _metric(self, label: str) -> tuple[QFrame, QLabel]:
        frame = QFrame()
        frame.setProperty("class", "metric")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(18, 14, 18, 14)
        caption = QLabel(label.upper())
        set_dynamic_class(caption, "muted")
        value = QLabel("Checking")
        value.setObjectName("metricValue")
        layout.addWidget(caption)
        layout.addWidget(value)
        return frame, value

    def _card_table(self) -> QTableWidget:
        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(("CARD OR DRIVE", "ACTION", "FREE", "ROOT", "STATE"))
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(30)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        return table

    def _build_dashboard_page(self) -> None:
        _page, layout = self._new_page("Dashboard", "Dashboard")
        metrics = QHBoxLayout()
        destination, self.destination_capacity_label = self._metric("Destination")
        sources, self.card_summary_label = self._metric("Sources")
        metrics.addWidget(destination, 1)
        metrics.addWidget(sources, 1)
        layout.addLayout(metrics)
        actions = QGridLayout()
        self.import_button = accent(QPushButton("Import selected cards"))
        self.import_button.setEnabled(False)
        self.import_button.setToolTip(
            "Review and import one or more selected connected cards. Multiple cards are queued safely."
        )
        self.import_button.clicked.connect(self._import_selected)
        self.organize_library_button = QPushButton("Import or merge folder")
        self._add_icon(self.organize_library_button, "settings")
        self.organize_library_button.setToolTip(
            "Copy or safely move files from another folder into a managed library."
        )
        self.organize_library_button.clicked.connect(
            self._open_import_merge
        )
        scan = QPushButton("Scan now")
        self._add_icon(scan, "refresh")
        scan.clicked.connect(self._scan_now)
        scan.setToolTip("Check all enabled connected cards now.")
        self.pause_button = QPushButton("Pause monitoring")
        self._add_icon(self.pause_button, "pause")
        self.pause_button.clicked.connect(self._toggle_pause)
        self.pause_button.setToolTip("Pause or resume automatic background scans.")
        refresh = QPushButton("Refresh cards")
        self._add_icon(refresh, "refresh")
        refresh.clicked.connect(self.refresh_cards)
        refresh.setToolTip("Refresh connected-card and retained-profile status.")
        actions.addWidget(refresh, 0, 0)
        actions.addWidget(scan, 0, 1)
        actions.addWidget(self.pause_button, 0, 2)
        actions.setColumnStretch(3, 1)
        self.dashboard_actions_layout = actions
        layout.addLayout(actions)

        import_options = QGroupBox("Destination for this import")
        import_options_layout = QGridLayout(import_options)
        self.import_library_combo = QComboBox()
        self.import_library_combo.setToolTip(
            "Choose any enabled local, remote-mounted, or removable library "
            "for this import without changing the saved default."
        )
        self.import_folder_mode_combo = choice_combo(
            list(IMPORT_FOLDER_MODES),
            "standard",
        )
        self.import_folder_mode_combo.setToolTip(
            "Use normal organization or route this import into a named "
            "Wedding, Client Shoot, Trip, or custom folder."
        )
        self.import_folder_name_edit = QLineEdit()
        self.import_folder_name_edit.setPlaceholderText(
            "Example: Smith–Jones Wedding"
        )
        self.import_folder_name_edit.setToolTip(
            "A case-by-case event or project name. It does not modify retained "
            "card settings."
        )
        self.import_destination_preview = QLabel()
        self.import_destination_preview.setWordWrap(True)
        set_dynamic_class(self.import_destination_preview, "muted")
        import_options_layout.addWidget(
            QLabel("Library"),
            0,
            0,
        )
        import_options_layout.addWidget(
            self.import_library_combo,
            0,
            1,
        )
        import_options_layout.addWidget(
            QLabel("Folder"),
            0,
            2,
        )
        import_options_layout.addWidget(
            self.import_folder_mode_combo,
            0,
            3,
        )
        self.import_folder_name_label = QLabel("Event or project")
        import_options_layout.addWidget(self.import_folder_name_label, 1, 0)
        import_options_layout.addWidget(
            self.import_folder_name_edit,
            1,
            1,
            1,
            3,
        )
        import_options_layout.addWidget(
            self.import_destination_preview,
            2,
            0,
            1,
            4,
        )
        import_options_layout.setColumnStretch(1, 2)
        import_options_layout.setColumnStretch(3, 1)
        layout.addWidget(import_options)
        self.import_library_combo.currentIndexChanged.connect(
            self._update_import_destination_preview
        )
        self.import_folder_mode_combo.currentIndexChanged.connect(
            self._update_import_destination_preview
        )
        self.import_folder_name_edit.textChanged.connect(
            self._update_import_destination_preview
        )
        self._refresh_import_library_choices()
        self._update_import_destination_preview()

        self.dashboard_table = self._card_table()
        self.dashboard_table.itemSelectionChanged.connect(self._update_card_action_state)
        self.dashboard_table.cellDoubleClicked.connect(lambda _row, _column: self._import_selected())
        layout.addWidget(self.dashboard_table, 1)
        import_actions = QHBoxLayout()
        import_actions.addWidget(self.organize_library_button)
        import_actions.addStretch(1)
        import_actions.addWidget(self.import_button)
        layout.addLayout(import_actions)

    def _build_library_management_page(self) -> None:
        _page, layout = self._new_page(
            "Libraries",
            "Libraries",
        )
        summary = QFrame()
        summary.setProperty("class", "preview")
        summary_layout = QVBoxLayout(summary)
        summary_title = QLabel("MANAGED LIBRARIES")
        set_dynamic_class(summary_title, "muted")
        self.library_summary_label = QLabel()
        self.library_summary_label.setWordWrap(True)
        summary_layout.addWidget(summary_title)
        summary_layout.addWidget(self.library_summary_label)
        layout.addWidget(summary)

        primary_actions = QHBoxLayout()
        self.add_library_button = accent(
            QPushButton("Set up library")
        )
        self._add_icon(self.add_library_button, "add")
        self.add_library_button.setToolTip(
            "Create a destination or connect an existing Photo Card Organizer "
            "library. This does not import or move media."
        )
        self.add_library_button.clicked.connect(
            self._add_library_destination
        )
        self.import_or_merge_button = QPushButton("Import or merge")
        self._add_icon(self.import_or_merge_button, "next")
        self.import_or_merge_button.setToolTip(
            "Add files from another folder or library to the selected managed "
            "library through a reviewed copy or verified-move workflow."
        )
        self.import_or_merge_button.clicked.connect(
            self._open_import_merge
        )
        self.merge_library_button = QPushButton("Merge library")
        self._add_icon(self.merge_library_button, "next")
        self.merge_library_button.setToolTip("Merge another managed library or folder into the selected library after a content-based preview.")
        self.merge_library_button.clicked.connect(self._merge_selected_library)
        primary_actions.addWidget(self.add_library_button)
        primary_actions.addWidget(self.import_or_merge_button)
        primary_actions.addWidget(self.merge_library_button)
        self.library_export_button = QPushButton("Export media")
        self._add_icon(self.library_export_button, "save")
        self.library_export_button.setToolTip("Select media and capture dates from this library for a verified copy export.")
        self.library_export_button.clicked.connect(self._open_library_export)
        primary_actions.addWidget(self.library_export_button)
        self.reorganize_selected_library_button = QPushButton("Reorganize library")
        self._add_icon(self.reorganize_selected_library_button, "refresh")
        self.reorganize_selected_library_button.setToolTip("Preview and change folders inside this library, keeping filenames and verifying moves.")
        self.reorganize_selected_library_button.clicked.connect(self._reorganize_selected_library)
        self.migrate_selected_library_button = QPushButton("Migrate library")
        self._add_icon(self.migrate_selected_library_button, "refresh")
        self.migrate_selected_library_button.setToolTip("Copy a library to a new location, verify it, and leave the original available for explicit cleanup.")
        self.migrate_selected_library_button.clicked.connect(self._migrate_selected_library)
        primary_actions.addStretch(1)
        layout.addLayout(primary_actions)
        library_tools = QHBoxLayout()
        library_tools.addWidget(self.reorganize_selected_library_button)
        library_tools.addWidget(self.migrate_selected_library_button)
        self.integrity_library_button = QPushButton("Verify integrity")
        self._add_icon(self.integrity_library_button, "info")
        self.integrity_library_button.setToolTip("Check files against saved checksums in the Integrity section.")
        self.integrity_library_button.clicked.connect(self._open_integrity)
        library_tools.addWidget(self.integrity_library_button)
        library_tools.addStretch(1)
        layout.addLayout(library_tools)

        self.library_table = QTableWidget(0, 6)
        self.library_table.setHorizontalHeaderLabels(
            (
                "LIBRARY",
                "TYPE",
                "STATE",
                "FREE",
                "FOLDER",
                "DEFAULT",
            )
        )
        self.library_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.library_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self.library_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.library_table.setAlternatingRowColors(True)
        self.library_table.verticalHeader().setVisible(False)
        self.library_table.verticalHeader().setDefaultSectionSize(32)
        header = self.library_table.horizontalHeader()
        for column in (0, 1, 2, 3, 5):
            header.setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.ResizeToContents,
            )
        header.setSectionResizeMode(
            4,
            QHeaderView.ResizeMode.Stretch,
        )
        self.library_table.itemSelectionChanged.connect(
            self._update_library_action_state
        )
        self.library_table.cellDoubleClicked.connect(
            lambda _row, _column: self._edit_library_destination()
        )
        layout.addWidget(self.library_table, 1)

        actions = QHBoxLayout()
        self.edit_library_button = QPushButton("Edit details")
        self._add_icon(self.edit_library_button, "edit")
        self.edit_library_button.setToolTip(
            "Change the selected library's name, connected folder, type, or "
            "storage profile. Changing the folder does not move media."
        )
        self.edit_library_button.clicked.connect(
            self._edit_library_destination
        )
        self.default_library_button = QPushButton("Use by default")
        self._add_icon(self.default_library_button, "save")
        self.default_library_button.setToolTip(
            "Use the selected library for monitoring and new imports by default."
        )
        self.default_library_button.clicked.connect(
            self._set_default_library
        )
        self.open_selected_library_button = QPushButton("Open folder")
        self._add_icon(self.open_selected_library_button, "open")
        self.open_selected_library_button.setToolTip(
            "Open the selected library folder."
        )
        self.open_selected_library_button.clicked.connect(
            self._open_selected_library
        )
        self.upgrade_library_button = QPushButton("Prepare metadata")
        self._add_icon(self.upgrade_library_button, "refresh")
        self.upgrade_library_button.setToolTip(
            "Initialize, check, or upgrade the selected library's internal "
            "metadata. Media files are not changed."
        )
        self.upgrade_library_button.clicked.connect(
            self._upgrade_selected_library
        )
        self.remove_library_button = QPushButton("Forget")
        self._add_icon(self.remove_library_button, "remove")
        self.remove_library_button.setProperty("danger", True)
        self.remove_library_button.setToolTip(
            "Forget the selected destination without deleting any library files."
        )
        self.remove_library_button.clicked.connect(
            self._remove_library_destination
        )
        for button in (
            self.edit_library_button,
            self.default_library_button,
            self.open_selected_library_button,
            self.upgrade_library_button,
        ):
            actions.addWidget(button)
        actions.addStretch(1)
        actions.addWidget(self.remove_library_button)
        layout.addLayout(actions)

        self._refresh_library_destinations()

    def _build_cards_page(self) -> None:
        _page, layout = self._new_page("Cards and drives", "Cards and drives")
        actions = QHBoxLayout()
        onboard = accent(QPushButton("Onboard card"))
        self._add_icon(onboard, "add")
        onboard.setToolTip("Open the guided setup for a connected card or removable drive.")
        onboard.clicked.connect(self._add_card)
        offline = QPushButton("Add offline card")
        self._add_icon(offline, "add")
        offline.setToolTip("Retain settings for a card that is not connected.")
        offline.clicked.connect(self._add_profile)
        self.edit_card_button = QPushButton("Edit")
        self._add_icon(self.edit_card_button, "edit")
        self.edit_card_button.setToolTip("Edit the selected card's retained name, source folders, transfer method, and camera override.")
        self.edit_card_button.clicked.connect(self._edit_card)
        self.open_identity_button = QPushButton("Open identity folder")
        self._add_icon(self.open_identity_button, "open")
        self.open_identity_button.setToolTip("Open the card metadata folder that contains its identity and transfer-session records.")
        self.open_identity_button.clicked.connect(self._open_identity)
        self.forget_profile_button = QPushButton("Forget profile")
        self._add_icon(self.forget_profile_button, "remove")
        self.forget_profile_button.setToolTip("Remove an offline retained profile from this computer. Files on the card are unchanged.")
        self.forget_profile_button.setProperty("danger", True)
        self.forget_profile_button.clicked.connect(self._forget_profile)
        actions.addWidget(onboard)
        actions.addWidget(offline)
        actions.addWidget(self.edit_card_button)
        actions.addWidget(self.open_identity_button)
        actions.addStretch(1)
        actions.addWidget(self.forget_profile_button)
        layout.addLayout(actions)
        self.cards_table = self._card_table()
        self.cards_table.itemSelectionChanged.connect(self._update_card_action_state)
        layout.addWidget(self.cards_table, 1)
        identity_group = QGroupBox("Card metadata stored at the card root")
        identity_layout = QGridLayout(identity_group)
        identification = self.config["identification"]
        self.folder_name_edit = QLineEdit(identification["folder_name"])
        self.identity_filename_edit = QLineEdit(identification["identity_filename"])
        self.history_folder_edit = QLineEdit(identification["history_folder_name"])
        self.history_folder_edit.textChanged.connect(self._update_history_preview)
        identity_layout.addWidget(QLabel("Metadata folder"), 0, 0)
        identity_layout.addWidget(self.folder_name_edit, 0, 1)
        identity_layout.addWidget(QLabel("Identity filename"), 0, 2)
        identity_layout.addWidget(self.identity_filename_edit, 0, 3)
        identity_layout.addWidget(QLabel("Session-record folder"), 0, 4)
        identity_layout.addWidget(self.history_folder_edit, 0, 5)
        identity_layout.setColumnStretch(1, 1)
        identity_layout.setColumnStretch(3, 1)
        identity_layout.setColumnStretch(5, 1)
        layout.addWidget(identity_group)

    def _build_existing_library_page(self) -> None:
        _page, page_layout = self._new_page(
            "Import or merge",
            "Import or merge",
        )
        workflow_header = QWidget()
        workflow_layout = QVBoxLayout(workflow_header)
        workflow_layout.setContentsMargins(0, 0, 0, 0)
        workflow_layout.setSpacing(7)
        step_row = QVBoxLayout()
        self.existing_step_label = QLabel("Step 1 of 3: Source")
        self.existing_step_label.setObjectName("dialogTitle")
        self.existing_step_detail = QLabel(
            "Choose the folder whose files you want to add"
        )
        set_dynamic_class(self.existing_step_detail, "muted")
        self.existing_step_detail.setWordWrap(True)
        step_row.addWidget(self.existing_step_label)
        step_row.addWidget(self.existing_step_detail)
        workflow_layout.addLayout(step_row)
        self.existing_step_progress = QProgressBar()
        self.existing_step_progress.setRange(1, 3)
        self.existing_step_progress.setValue(1)
        self.existing_step_progress.setTextVisible(False)
        self.existing_step_progress.setFixedHeight(5)
        workflow_layout.addWidget(self.existing_step_progress)
        page_layout.addWidget(workflow_header)

        self.existing_step_stack = QStackedWidget()
        page_layout.addWidget(self.existing_step_stack, 1)

        source_content = QWidget()
        source_layout = QVBoxLayout(source_content)
        source_layout.setContentsMargins(2, 2, 12, 16)
        source_layout.setSpacing(14)
        source_group = QGroupBox("Files to add")
        source_form = QFormLayout(source_group)
        source_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        source_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        source_row = QWidget()
        source_row_layout = QHBoxLayout(source_row)
        source_row_layout.setContentsMargins(0, 0, 0, 0)
        self.existing_source_edit = QLineEdit()
        source_browse = QPushButton("Browse")
        self._add_icon(source_browse, "open")
        source_browse.setToolTip("Choose the existing photo or video library to analyze and import.")
        source_browse.clicked.connect(self._browse_existing_source)
        source_row_layout.addWidget(self.existing_source_edit, 1)
        source_row_layout.addWidget(source_browse)
        self.existing_name_edit = QLineEdit()
        self.existing_camera_edit = QLineEdit()
        self.existing_camera_edit.setToolTip(CAMERA_NAME_HELP)
        self.existing_recursive_check = QCheckBox("Include files in subfolders")
        self.existing_recursive_check.setChecked(True)
        source_form.addRow("Source folder", source_row)
        source_form.addRow("Source name", self.existing_name_edit)
        source_form.addRow("", self.existing_recursive_check)
        source_layout.addWidget(source_group)
        self.existing_source_state_label = QLabel("Choose a folder to continue.")
        self.existing_source_state_label.setWordWrap(True)
        set_dynamic_class(self.existing_source_state_label, "muted")
        source_layout.addWidget(self.existing_source_state_label)
        source_layout.addStretch(1)
        self.existing_step_stack.addWidget(scrollable(source_content))

        plan_content = QWidget()
        plan_layout = QVBoxLayout(plan_content)
        plan_layout.setContentsMargins(2, 2, 12, 16)
        plan_layout.setSpacing(14)
        destination_group = QGroupBox("Destination")
        destination_form = QFormLayout(destination_group)
        self.existing_destination_form = destination_form
        destination_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        destination_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.existing_library_combo = QComboBox()
        self.existing_library_combo.setToolTip(
            "Choose one of the enabled destinations from Libraries."
        )
        destination_row = QWidget()
        destination_row_layout = QHBoxLayout(destination_row)
        destination_row_layout.setContentsMargins(0, 0, 0, 0)
        self.destination_edit = QLineEdit(self.config["destination_root"])
        self.destination_edit.setReadOnly(True)
        destination_browse = QPushButton("Manage")
        self._add_icon(destination_browse, "settings")
        destination_browse.setToolTip(
            "Add or edit selectable destinations in Libraries."
        )
        destination_browse.clicked.connect(
            lambda: self.show_page("Libraries")
        )
        destination_row_layout.addWidget(self.destination_edit, 1)
        destination_row_layout.addWidget(destination_browse)
        self.existing_prefix_edit = QLineEdit()
        self.existing_prefix_edit.setToolTip(LIBRARY_SUBFOLDER_HELP)
        self.existing_prefix_edit.setReadOnly(True)
        self.existing_folder_mode_combo = choice_combo(
            list(IMPORT_FOLDER_MODES),
            "standard",
        )
        self.existing_folder_mode_combo.setToolTip(
            "Route this one import through standard organization or a named "
            "Wedding, Client Shoot, Trip, or custom folder."
        )
        self.existing_event_name_edit = QLineEdit()
        self.existing_event_name_edit.setPlaceholderText(
            "Example: Smith–Jones Wedding"
        )
        self.existing_event_name_edit.setToolTip(
            "The case-by-case event or project name used to build the "
            "destination subfolder."
        )
        self.existing_action_combo = choice_combo(
            [
                ("Copy and keep source files", "copy"),
                (
                    "Verified move; remove source after checks",
                    "move",
                ),
            ],
            "copy",
        )
        destination_form.addRow(
            "Receiving library",
            self.existing_library_combo,
        )
        destination_form.addRow("Library folder", destination_row)
        destination_form.addRow(
            "Import grouping",
            self.existing_folder_mode_combo,
        )
        destination_form.addRow(
            "Event or project name",
            self.existing_event_name_edit,
        )
        destination_form.addRow(
            "Resolved library subfolder",
            self.existing_prefix_edit,
        )
        destination_form.addRow(
            "Source-file handling",
            self.existing_action_combo,
        )
        plan_layout.addWidget(destination_group)

        organization_group = QGroupBox("Organization")
        organization_layout = QVBoxLayout(organization_group)
        preset_form = QFormLayout()
        preset_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        preset_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.existing_preset_combo = QComboBox()
        self.existing_preset_combo.addItems(list(ORGANIZATION_PRESETS))
        preset_form.addRow("Folder layout", self.existing_preset_combo)
        preset_form.addRow("Camera override (optional)", self.existing_camera_edit)
        organization_layout.addLayout(preset_form)
        self.existing_structure_status_label = QLabel(
            "Source structure has not been analyzed. The selected folder layout will be used."
        )
        self.existing_structure_status_label.setWordWrap(True)
        set_dynamic_class(self.existing_structure_status_label, "muted")
        organization_layout.addWidget(self.existing_structure_status_label)
        preset_actions = QHBoxLayout()
        self.existing_analyze_button = QPushButton("Analyze source folders")
        self._add_icon(self.existing_analyze_button, "settings")
        self.existing_analyze_button.setToolTip(
            "Read supported filenames and folders, then propose an editable destination layout. No files are changed."
        )
        self.existing_analyze_button.clicked.connect(self._detect_existing_structure)
        preset_actions.addWidget(self.existing_analyze_button)
        detailed = QPushButton("Edit detailed rules")
        self._add_icon(detailed, "edit")
        detailed.setToolTip("Open the complete per-media folder and filename settings.")
        detailed.clicked.connect(lambda: self.show_page("Organization"))
        preset_actions.addWidget(detailed)
        preset_actions.addStretch(1)
        organization_layout.addLayout(preset_actions)
        media_row = QHBoxLayout()
        media_row.addWidget(QLabel("Media types"))
        for kind, label in MEDIA_LABELS.items():
            check = QCheckBox(label)
            check.setChecked(bool(self.config["media_rules"][kind].get("enabled", True)))
            check.setToolTip(f"Include supported {label.lower()} files in this existing-library import.")
            self.existing_media_checks[kind] = check
            media_row.addWidget(check)
        media_row.addStretch(1)
        organization_layout.addLayout(media_row)
        preview = QFrame()
        preview.setProperty("class", "preview")
        preview_layout = QVBoxLayout(preview)
        caption = QLabel("PLANNED IMPORT")
        set_dynamic_class(caption, "muted")
        self.existing_preview_label = QLabel("Choose a source folder")
        self.existing_preview_label.setWordWrap(True)
        preview_layout.addWidget(caption)
        preview_layout.addWidget(self.existing_preview_label)
        organization_layout.addWidget(preview)
        plan_layout.addWidget(organization_group)
        plan_layout.addStretch(1)
        self.existing_step_stack.addWidget(scrollable(plan_content))

        review_content = QWidget()
        review_layout = QVBoxLayout(review_content)
        review_layout.setContentsMargins(2, 2, 2, 2)
        review_layout.setSpacing(12)
        review_group = QGroupBox("Import plan")
        review_group_layout = QVBoxLayout(review_group)
        self.existing_review_summary = QPlainTextEdit()
        self.existing_review_summary.setReadOnly(True)
        self.existing_review_summary.setMinimumHeight(235)
        self.existing_review_summary.setToolTip(
            "The source, destination, operation, organization, verification, and backup plan that will be confirmed before scanning."
        )
        review_group_layout.addWidget(self.existing_review_summary)
        review_layout.addWidget(review_group, 1)
        self.existing_move_warning = QLabel(
            "Verified move removes each source file only after required copies, checksums, backups, and transfer records succeed."
        )
        self.existing_move_warning.setWordWrap(True)
        set_dynamic_class(self.existing_move_warning, "warning")
        review_layout.addWidget(self.existing_move_warning)
        self.existing_step_stack.addWidget(review_content)

        navigation_row = QHBoxLayout()
        self.existing_back_button = QPushButton("Back")
        self._add_icon(self.existing_back_button, "back")
        self.existing_back_button.setToolTip("Return to the previous existing-library setup step.")
        self.existing_back_button.clicked.connect(self._existing_step_back)
        navigation_row.addWidget(self.existing_back_button)
        navigation_row.addStretch(1)
        self.existing_next_button = accent(
            QPushButton("Continue to destination")
        )
        self._add_icon(self.existing_next_button, "next")
        self.existing_next_button.setToolTip(
            "Validate this step and continue without scanning or changing media."
        )
        self.existing_next_button.clicked.connect(self._existing_step_next)
        navigation_row.addWidget(self.existing_next_button)
        self.existing_review_button = accent(
            QPushButton("Save settings + Import")
        )
        self._add_icon(self.existing_review_button, "play")
        self.existing_review_button.setToolTip(
            "Show the final confirmation, save the reviewed settings, then "
            "begin the import or merge."
        )
        self.existing_review_button.clicked.connect(self._review_existing_import)
        navigation_row.addWidget(self.existing_review_button)
        page_layout.addLayout(navigation_row)

        self.existing_source_edit.textChanged.connect(
            self._existing_source_scope_changed
        )
        self.existing_name_edit.textChanged.connect(self._update_existing_preview)
        self.existing_recursive_check.toggled.connect(
            self._existing_source_scope_changed
        )
        self.destination_edit.textChanged.connect(self._update_existing_preview)
        self.existing_library_combo.currentIndexChanged.connect(
            self._existing_library_changed
        )
        self.existing_folder_mode_combo.currentIndexChanged.connect(
            self._update_existing_folder_route
        )
        self.existing_event_name_edit.textChanged.connect(
            self._update_existing_folder_route
        )
        self.existing_prefix_edit.textChanged.connect(
            self._update_existing_preview
        )
        self.existing_action_combo.currentIndexChanged.connect(self._update_existing_preview)
        self.existing_preset_combo.currentIndexChanged.connect(self._update_existing_preview)
        for check in self.existing_media_checks.values():
            check.toggled.connect(self._existing_source_scope_changed)
        self._refresh_import_library_choices()
        self._update_existing_folder_route()
        self._set_existing_step(0)
        self._update_existing_preview()

    def _build_digest_inboxes_page(self) -> None:
        _page, layout = self._new_page("Digest inboxes", "Digest inboxes")
        summary = QFrame()
        summary.setProperty("class", "preview")
        summary_layout = QVBoxLayout(summary)
        summary_title = QLabel("RETAINED INCOMING FOLDERS")
        set_dynamic_class(summary_title, "muted")
        summary_text = QLabel(
            "Digest mixed folder structures from local, removable, shared, or synchronized folders. "
            "Unchanged files are skipped from retained history; automatic digestion is copy-only."
        )
        summary_text.setWordWrap(True)
        summary_layout.addWidget(summary_title)
        summary_layout.addWidget(summary_text)
        layout.addWidget(summary)

        self.digest_table = QTableWidget(0, 8)
        self.digest_table.setHorizontalHeaderLabels(
            (
                "DIGEST INBOX",
                "FOLDER",
                "ACTION",
                "STATE",
                "PENDING",
                "PROCESSED",
                "ISSUES",
                "LAST RUN",
            )
        )
        self.digest_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.digest_table.setSelectionMode(
            QTableWidget.SelectionMode.ExtendedSelection
        )
        self.digest_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.digest_table.setAlternatingRowColors(True)
        self.digest_table.verticalHeader().setVisible(False)
        self.digest_table.setMinimumHeight(145)
        digest_header = self.digest_table.horizontalHeader()
        digest_header.setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        digest_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, 8):
            digest_header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.digest_table.itemSelectionChanged.connect(
            self._digest_selection_changed
        )
        self.digest_table.cellDoubleClicked.connect(
            lambda _row, _column: self._edit_digest_inbox()
        )
        layout.addWidget(self.digest_table)

        actions = QGridLayout()
        self.add_digest_button = accent(QPushButton("Add Digest Inbox"))
        self._add_icon(self.add_digest_button, "add")
        self.add_digest_button.setToolTip(
            "Retain an incoming folder and configure how its media enters the master library."
        )
        self.add_digest_button.clicked.connect(self._add_digest_inbox)
        self.edit_digest_button = QPushButton("Edit selected")
        self._add_icon(self.edit_digest_button, "edit")
        self.edit_digest_button.setToolTip(
            "Edit the selected inbox without changing its retained digest identity or history."
        )
        self.edit_digest_button.clicked.connect(self._edit_digest_inbox)
        self.open_digest_button = QPushButton("Open inbox")
        self._add_icon(self.open_digest_button, "open")
        self.open_digest_button.setToolTip(
            "Open the selected incoming folder when it is currently available."
        )
        self.open_digest_button.clicked.connect(self._open_digest_inbox)
        self.remove_digest_button = QPushButton("Forget selected")
        self._add_icon(self.remove_digest_button, "remove")
        self.remove_digest_button.setProperty("danger", True)
        self.remove_digest_button.setToolTip(
            "Remove only the retained inbox profile. Source files, master files, and digest history remain unchanged."
        )
        self.remove_digest_button.clicked.connect(self._remove_digest_inboxes)
        self.run_digest_button = accent(QPushButton("Review and digest selected"))
        self._add_icon(self.run_digest_button, "refresh")
        self.run_digest_button.setToolTip(
            "Review source handling, verification, backups, and destination before scanning selected inboxes."
        )
        self.run_digest_button.clicked.connect(self._digest_selected_inboxes)
        actions.addWidget(self.add_digest_button, 0, 0)
        actions.addWidget(self.edit_digest_button, 0, 1)
        actions.addWidget(self.open_digest_button, 0, 2)
        actions.addWidget(self.remove_digest_button, 0, 3)
        actions.addWidget(self.run_digest_button, 1, 0, 1, 2)
        actions.setColumnStretch(4, 1)
        self.digest_actions_layout = actions
        layout.addLayout(actions)

        queue_header = QHBoxLayout()
        queue_title = QLabel("DIGEST QUEUE")
        set_dynamic_class(queue_title, "muted")
        queue_header.addWidget(queue_title)
        self.digest_queue_count_label = QLabel("0 files")
        set_dynamic_class(self.digest_queue_count_label, "muted")
        queue_header.addWidget(self.digest_queue_count_label)
        queue_header.addStretch(1)
        queue_header.addWidget(QLabel("Show"))
        self.digest_status_filter = choice_combo(
            [
                ("All files", "all"),
                ("Pending", "pending"),
                ("Processed", "processed"),
                ("Failed", "failed"),
                ("Conflicts", "conflict"),
            ],
            "all",
        )
        self.digest_status_filter.setToolTip(
            "Filter retained per-file digest state for the selected inboxes."
        )
        self.digest_status_filter.currentIndexChanged.connect(
            self._refresh_digest_queue
        )
        queue_header.addWidget(self.digest_status_filter)
        refresh_queue = QPushButton("Refresh")
        self._add_icon(refresh_queue, "refresh")
        refresh_queue.setToolTip(
            "Reload Digest Inbox totals and per-file state from the local manifest."
        )
        refresh_queue.clicked.connect(self._refresh_digest_inboxes)
        queue_header.addWidget(refresh_queue)
        layout.addLayout(queue_header)

        self.digest_queue_table = QTableWidget(0, 6)
        self.digest_queue_table.setHorizontalHeaderLabels(
            (
                "STATUS",
                "INBOX",
                "TYPE",
                "SOURCE FILE",
                "DESTINATION OR ERROR",
                "UPDATED",
            )
        )
        self.digest_queue_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.digest_queue_table.setSelectionMode(
            QTableWidget.SelectionMode.ExtendedSelection
        )
        self.digest_queue_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.digest_queue_table.setAlternatingRowColors(True)
        self.digest_queue_table.verticalHeader().setVisible(False)
        queue_table_header = self.digest_queue_table.horizontalHeader()
        for column in (0, 1, 2, 5):
            queue_table_header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        queue_table_header.setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        queue_table_header.setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.digest_queue_table, 1)
        self._refresh_digest_inboxes()

    def _build_travel_sync_page(self) -> None:
        _page, page_layout = self._new_page("Travel sync", "Travel sync")
        self.travel_tabs = QTabWidget()
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(2, 2, 12, 16)
        layout.setSpacing(12)
        summary = QFrame()
        summary.setProperty("class", "preview")
        summary_layout = QVBoxLayout(summary)
        title = QLabel("DESKTOP MASTER WORKFLOW")
        set_dynamic_class(title, "muted")
        description = QLabel(
            "Import cards into the laptop while away. When the laptop library is reachable "
            "again, select it here and copy only new media into this desktop master. "
            "Travel sync never removes source files or propagates deletions."
        )
        description.setWordWrap(True)
        summary_layout.addWidget(title)
        summary_layout.addWidget(description)
        layout.addWidget(summary)

        direct_label = QLabel("DIRECT LAPTOP OR MOUNTED-DRIVE SOURCES")
        set_dynamic_class(direct_label, "muted")
        layout.addWidget(direct_label)
        self.travel_table = QTableWidget(0, 5)
        self.travel_table.setHorizontalHeaderLabels(
            ("TRAVEL LIBRARY", "PATH", "CONNECTION", "MASTER FILES", "LAST SYNC")
        )
        self.travel_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.travel_table.setSelectionMode(
            QTableWidget.SelectionMode.ExtendedSelection
        )
        self.travel_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.travel_table.setAlternatingRowColors(True)
        self.travel_table.verticalHeader().setVisible(False)
        self.travel_table.setFixedHeight(110)
        travel_header = self.travel_table.horizontalHeader()
        travel_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        travel_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, 5):
            travel_header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.travel_table.itemSelectionChanged.connect(
            self._update_travel_action_state
        )
        self.travel_table.cellDoubleClicked.connect(
            lambda _row, _column: self._sync_selected_travel_libraries()
        )
        layout.addWidget(self.travel_table)

        actions = QGridLayout()
        self.add_travel_button = accent(QPushButton("Add laptop library"))
        self._add_icon(self.add_travel_button, "add")
        self.add_travel_button.setToolTip(
            "Retain a laptop library, network share, or mounted travel drive for future return-home syncs."
        )
        self.add_travel_button.clicked.connect(self._add_travel_library)
        self.edit_travel_button = QPushButton("Edit selected")
        self._add_icon(self.edit_travel_button, "edit")
        self.edit_travel_button.setToolTip(
            "Edit the selected travel source without changing its stable sync identity."
        )
        self.edit_travel_button.clicked.connect(self._edit_travel_library)
        self.open_travel_button = QPushButton("Open source")
        self._add_icon(self.open_travel_button, "open")
        self.open_travel_button.setToolTip(
            "Open the selected laptop library when its path is available."
        )
        self.open_travel_button.clicked.connect(self._open_travel_library)
        self.remove_travel_button = QPushButton("Forget selected")
        self._add_icon(self.remove_travel_button, "remove")
        self.remove_travel_button.setProperty("danger", True)
        self.remove_travel_button.setToolTip(
            "Remove only the retained source profile. Laptop and desktop files remain unchanged."
        )
        self.remove_travel_button.clicked.connect(self._remove_travel_library)
        self.sync_travel_button = accent(QPushButton("Review and sync selected"))
        self._add_icon(self.sync_travel_button, "refresh")
        self.sync_travel_button.setToolTip(
            "Review a copy-only summary, then reconcile every selected available travel library into the desktop master."
        )
        self.sync_travel_button.clicked.connect(
            self._sync_selected_travel_libraries
        )
        actions.addWidget(self.add_travel_button, 0, 0)
        actions.addWidget(self.edit_travel_button, 0, 1)
        actions.addWidget(self.open_travel_button, 0, 2)
        actions.addWidget(self.remove_travel_button, 0, 3)
        actions.addWidget(self.sync_travel_button, 1, 0, 1, 2)
        actions.setColumnStretch(4, 1)
        self.travel_actions_layout = actions
        layout.addLayout(actions)
        layout.addStretch(1)
        self.travel_tabs.addTab(
            scrollable(content), "Laptop libraries"
        )

        hub_content = QWidget()
        hub_page_layout = QVBoxLayout(hub_content)
        hub_page_layout.setContentsMargins(2, 2, 12, 16)
        hub_page_layout.setSpacing(12)
        hub_summary = QFrame()
        hub_summary.setProperty("class", "preview")
        hub_summary_layout = QVBoxLayout(hub_summary)
        hub_summary_title = QLabel("SHARED TRANSFER WORKFLOW")
        set_dynamic_class(hub_summary_title, "muted")
        hub_summary_text = QLabel(
            "Use a removable drive, SMB/NAS share, or locally synchronized cloud "
            "folder to publish travel sessions or catch new sessions in the master library."
        )
        hub_summary_text.setWordWrap(True)
        hub_summary_layout.addWidget(hub_summary_title)
        hub_summary_layout.addWidget(hub_summary_text)
        hub_page_layout.addWidget(hub_summary)
        hub_group = QGroupBox("Shared transfer hubs")
        hub_layout = QVBoxLayout(hub_group)
        self.hub_table = QTableWidget(0, 6)
        self.hub_table.setHorizontalHeaderLabels(
            ("HUB", "ROLE", "FOLDER", "CHANNEL", "STATE", "CONFIRMATIONS")
        )
        self.hub_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.hub_table.setSelectionMode(
            QTableWidget.SelectionMode.ExtendedSelection
        )
        self.hub_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.hub_table.setAlternatingRowColors(True)
        self.hub_table.verticalHeader().setVisible(False)
        self.hub_table.setFixedHeight(140)
        hub_header = self.hub_table.horizontalHeader()
        hub_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hub_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hub_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for column in range(3, 6):
            hub_header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.hub_table.itemSelectionChanged.connect(
            self._update_hub_action_state
        )
        hub_layout.addWidget(self.hub_table)
        hub_actions = QHBoxLayout()
        add_hub = QPushButton("Add hub")
        self._add_icon(add_hub, "add")
        add_hub.setToolTip(
            "Configure a USB drive, SMB/NAS path, mounted drive, or synchronized "
            "cloud folder as a publish or catch hub."
        )
        add_hub.clicked.connect(self._add_transfer_hub)
        self.edit_hub_button = QPushButton("Edit selected")
        self._add_icon(self.edit_hub_button, "edit")
        self.edit_hub_button.setToolTip(
            "Edit the selected hub role, folder, producer channel, monitoring, or receipt settings."
        )
        self.edit_hub_button.clicked.connect(self._edit_transfer_hub)
        self.remove_hub_button = QPushButton("Forget selected")
        self._add_icon(self.remove_hub_button, "remove")
        self.remove_hub_button.setProperty("danger", True)
        self.remove_hub_button.setToolTip(
            "Forget only these hub profiles. Shared media, sessions, receipts, and local files remain unchanged."
        )
        self.remove_hub_button.clicked.connect(self._remove_transfer_hubs)
        self.run_hub_button = accent(QPushButton("Run selected hub"))
        self._add_icon(self.run_hub_button, "refresh")
        self.run_hub_button.setToolTip(
            "Publish this library or catch producer channels according to each selected hub role."
        )
        self.run_hub_button.clicked.connect(self._run_selected_hubs)
        hub_actions.addWidget(add_hub)
        hub_actions.addWidget(self.edit_hub_button)
        hub_actions.addWidget(self.remove_hub_button)
        hub_actions.addStretch(1)
        hub_actions.addWidget(self.run_hub_button)
        hub_layout.addLayout(hub_actions)
        hub_page_layout.addWidget(hub_group)
        hub_page_layout.addStretch(1)
        self.travel_tabs.addTab(scrollable(hub_content), "Shared hubs")
        page_layout.addWidget(self.travel_tabs, 1)
        self._refresh_travel_libraries()
        self._refresh_transfer_hubs()

    def _build_library_export_page(self) -> None:
        _page, layout = self._new_page("Library export", "Library export")
        controls = QFrame()
        controls.setProperty("class", "preview")
        controls_layout = QGridLayout(controls)
        self.export_library_label = QLabel(self.config["destination_root"])
        self.export_library_label.setWordWrap(True)
        self.export_library_label.setToolTip(
            "The selected library folder. Changing libraries clears the scanned selection."
        )
        self.export_library_combo = QComboBox()
        self.export_library_combo.setToolTip("Choose a saved library without changing the default import destination.")
        self._export_scanned_root = None
        self.filtered_export_captures = []
        self.export_library_combo.currentIndexChanged.connect(self._export_library_changed)
        self.bracket_seconds_spin = self._number_spin(
            3.0, 0.1, 60.0, decimals=1, suffix=" sec"
        )
        self.bracket_seconds_spin.setToolTip(
            "Adjacent captures at or below this gap are treated as one bracket or burst."
        )
        self.interval_seconds_spin = self._number_spin(
            300.0, 5.0, 3600.0, decimals=0, suffix=" sec"
        )
        self.interval_seconds_spin.setToolTip(
            "Regular sequences with at least three captures and gaps below this limit are grouped as interval photography."
        )
        self.bracket_seconds_spin.valueChanged.connect(
            self._rebuild_export_groups
        )
        self.interval_seconds_spin.valueChanged.connect(
            self._rebuild_export_groups
        )
        controls_layout.addWidget(QLabel("Library"), 0, 0)
        controls_layout.addWidget(self.export_library_combo, 0, 1, 1, 3)
        controls_layout.addWidget(QLabel("Bracket / burst gap"), 1, 0)
        controls_layout.addWidget(self.bracket_seconds_spin, 1, 1)
        controls_layout.addWidget(QLabel("Interval maximum gap"), 1, 2)
        controls_layout.addWidget(self.interval_seconds_spin, 1, 3)
        self.export_media_combo = choice_combo((("All media", "all"), *[(label, kind) for kind, label in MEDIA_LABELS.items()]), "all")
        self.export_media_combo.setToolTip("Limit exports to this media type. Group selection cannot add other types.")
        self.export_sidecars_check = QCheckBox("Include matching sidecars")
        self.export_sidecars_check.setChecked(True)
        self.export_sidecars_check.setToolTip("Include sidecars paired with matching photos, RAW files or videos.")
        controls_layout.addWidget(QLabel("Media"), 2, 0)
        controls_layout.addWidget(self.export_media_combo, 2, 1)
        controls_layout.addWidget(self.export_sidecars_check, 2, 2, 1, 2)
        self.export_date_check = QCheckBox("Capture date range")
        self.export_date_check.setToolTip("Include both boundary dates. Files without capture metadata use their modification date.")
        self.export_start_date = QDateEdit(QDate.currentDate().addYears(-1))
        self.export_end_date = QDateEdit(QDate.currentDate())
        for field, tip in ((self.export_start_date, "First capture date, inclusive."), (self.export_end_date, "Last capture date, inclusive.")):
            field.setCalendarPopup(True)
            field.setDisplayFormat("yyyy-MM-dd")
            field.setToolTip(tip)
            field.setEnabled(False)
            field.dateChanged.connect(self._refresh_export_table)
        controls_layout.addWidget(self.export_date_check, 3, 0)
        controls_layout.addWidget(self.export_start_date, 3, 1)
        controls_layout.addWidget(QLabel("Through"), 3, 2)
        controls_layout.addWidget(self.export_end_date, 3, 3)
        controls_layout.addWidget(self.export_library_label, 4, 0, 1, 4)
        self.export_date_check.toggled.connect(self.export_start_date.setEnabled)
        self.export_date_check.toggled.connect(self.export_end_date.setEnabled)
        self.export_date_check.toggled.connect(self._refresh_export_table)
        self.export_media_combo.currentIndexChanged.connect(self._refresh_export_table)
        self.export_sidecars_check.toggled.connect(self._refresh_export_table)
        controls_layout.setColumnStretch(1, 1)
        controls_layout.setColumnStretch(3, 1)
        layout.addWidget(controls)

        status_row = QHBoxLayout()
        self.export_status_label = QLabel(
            "Scan the master library to select captures for editing."
        )
        set_dynamic_class(self.export_status_label, "muted")
        self.scan_export_library_button = QPushButton("Scan library")
        self._add_icon(self.scan_export_library_button, "refresh")
        self.scan_export_library_button.setToolTip(
            "Read supported media metadata in the master library. No files are modified."
        )
        self.scan_export_library_button.clicked.connect(self._scan_export_library)
        status_row.addWidget(self.export_status_label, 1)
        status_row.addWidget(self.scan_export_library_button)
        layout.addLayout(status_row)

        self.export_table = QTableView()
        self.export_model = LazyTableModel(
            ("CAPTURED", "GROUP", "MEDIA", "RATING", "CAMERA", "FILE"),
            values=self._export_row_values, parent=self,
        )
        self.export_table.setModel(self.export_model)
        self.export_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.export_table.setSelectionMode(
            QTableWidget.SelectionMode.ExtendedSelection
        )
        self.export_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.export_table.setAlternatingRowColors(True)
        self.export_table.verticalHeader().setVisible(False)
        export_header = self.export_table.horizontalHeader()
        for column, width in enumerate((142, 124, 105, 58, 115)):
            export_header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.Fixed
            )
            self.export_table.setColumnWidth(column, width)
        export_header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.export_table.selectionModel().selectionChanged.connect(
            self._update_export_action_state
        )
        layout.addWidget(self.export_table, 1)

        export_options = QGridLayout()
        self.select_export_group_button = QPushButton(
            "Select detected group"
        )
        self._add_icon(self.select_export_group_button, "add")
        self.select_export_group_button.setToolTip(
            "Expand the current selection to every capture in its bracket, burst, or interval group."
        )
        self.select_export_group_button.clicked.connect(
            self._select_export_groups
        )
        self.export_full_groups_check = QCheckBox(
            "Include complete detected groups"
        )
        self.export_full_groups_check.setChecked(True)
        self.export_full_groups_check.setToolTip(
            "When any group member is selected, include every capture in that detected group."
        )
        self.export_group_folders_check = QCheckBox(
            "Create one folder per detected group"
        )
        self.export_group_folders_check.setChecked(True)
        self.export_group_folders_check.setToolTip(
            "Keep bracket/burst and interval files together in clearly named export folders."
        )
        self.export_selected_button = accent(QPushButton("Export selected"))
        self._add_icon(self.export_selected_button, "save")
        self.export_selected_button.setEnabled(False)
        self.export_selected_button.setToolTip(
            "Copy and SHA-256 verify all selected JPEG, RAW, video, and matching sidecar files into an editing folder."
        )
        self.export_selected_button.clicked.connect(self._export_selected_captures)
        export_options.addWidget(self.export_full_groups_check, 0, 0)
        export_options.addWidget(self.export_group_folders_check, 0, 1)
        export_options.addWidget(self.select_export_group_button, 1, 0)
        self.export_select_all_button = QPushButton("Select all matching")
        self._add_icon(self.export_select_all_button, "add")
        self.export_select_all_button.setToolTip("Select every capture matching the current media and date filters.")
        self.export_select_all_button.clicked.connect(self.export_table.selectAll)
        export_options.addWidget(self.export_select_all_button, 1, 1)
        export_options.addWidget(self.export_selected_button, 1, 2)
        export_options.setColumnStretch(2, 1)
        self.export_options_layout = export_options
        layout.addLayout(export_options)
        self._refresh_import_library_choices()
        self._export_library_changed()

    def _segment_combo(self, current: str = "") -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        for label, value in SEGMENT_LABELS.items():
            combo.addItem(label, value)
            if label == "Year and week (ISO)":
                combo.setItemData(combo.count() - 1,
                    "Monday-start weeks. Week 1 contains January 4; dates near New Year can belong to the previous or next week-year.",
                    Qt.ItemDataRole.ToolTipRole)
        combo.setToolTip(
            "Choose metadata, a retained source-folder level, or enter a fixed folder "
            "name. None omits only this destination level."
        )
        set_combo_data(combo, current)
        if combo.currentIndex() < 0 and current:
            combo.setEditText(current)
        return combo

    def _append_media_segment(self, kind: str, value: str = "") -> None:
        controls = self.media_controls[kind]
        segments: list[QComboBox] = controls["segments"]
        if len(segments) >= MAX_EDITABLE_SOURCE_LEVELS:
            return
        index = len(segments)
        combo = self._segment_combo(value)
        combo.setToolTip(
            f"Destination folder level {index + 1}, read from left to right. "
            "Choose None to omit only this level."
        )
        combo.currentIndexChanged.connect(
            lambda _value, media=kind: self._update_media_preview(media)
        )
        combo.editTextChanged.connect(
            lambda _value, media=kind: self._update_media_preview(media)
        )
        if self._dirty_tracking_ready:
            combo.currentIndexChanged.connect(
                self._update_settings_dirty_state
            )
            combo.currentTextChanged.connect(
                self._update_settings_dirty_state
            )
        level_box = QWidget()
        level_layout = QVBoxLayout(level_box)
        level_layout.setContentsMargins(0, 0, 0, 0)
        level_layout.setSpacing(4)
        level_label = QLabel(f"LEVEL {index + 1}")
        set_dynamic_class(level_label, "muted")
        level_layout.addWidget(level_label)
        level_layout.addWidget(combo)
        controls["segment_grid"].addWidget(level_box, index // 3, index % 3)
        segments.append(combo)
        controls["segment_boxes"].append(level_box)
        self._refresh_media_segment_actions(kind)
        self._update_settings_dirty_state()

    def _remove_media_segment(self, kind: str) -> None:
        controls = self.media_controls[kind]
        segments: list[QComboBox] = controls["segments"]
        if len(segments) <= 1:
            return
        segments.pop()
        level_box = controls["segment_boxes"].pop()
        controls["segment_grid"].removeWidget(level_box)
        level_box.deleteLater()
        self._refresh_media_segment_actions(kind)
        self._update_media_preview(kind)
        self._update_settings_dirty_state()

    def _refresh_media_segment_actions(self, kind: str) -> None:
        controls = self.media_controls[kind]
        count = len(controls["segments"])
        controls["add_segment"].setEnabled(count < MAX_EDITABLE_SOURCE_LEVELS)
        controls["remove_segment"].setEnabled(count > 1)

    def _build_organization_page(self) -> None:
        _page, layout = self._new_page("Organization", "Organization")
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        for kind, media_label in MEDIA_LABELS.items():
            rule = self.config["media_rules"][kind]
            content = QWidget()
            form = QFormLayout(content)
            form.setContentsMargins(18, 18, 18, 18)
            form.setHorizontalSpacing(18)
            form.setVerticalSpacing(12)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
            enabled = QCheckBox(f"Process {media_label.lower()}")
            enabled.setChecked(bool(rule.get("enabled", True)))
            extensions = QLineEdit(", ".join(rule["extensions"]))
            segment_row = QWidget()
            segment_layout = QVBoxLayout(segment_row)
            segment_layout.setContentsMargins(0, 0, 0, 0)
            segment_layout.setSpacing(8)
            segment_grid = QGridLayout()
            segment_grid.setContentsMargins(0, 0, 0, 0)
            segment_grid.setHorizontalSpacing(8)
            segment_grid.setVerticalSpacing(8)
            for column in range(3):
                segment_grid.setColumnStretch(column, 1)
            segment_layout.addLayout(segment_grid)
            segment_actions = QHBoxLayout()
            segment_actions.setContentsMargins(0, 0, 0, 0)
            add_segment = QPushButton("Add level")
            self._add_icon(add_segment, "add")
            add_segment.setToolTip(
                f"Add another destination folder level, up to {MAX_EDITABLE_SOURCE_LEVELS}."
            )
            add_segment.clicked.connect(
                lambda _checked=False, media=kind: self._append_media_segment(media)
            )
            remove_segment = QPushButton("Remove last")
            self._add_icon(remove_segment, "remove")
            remove_segment.setToolTip(
                "Remove the final destination folder level from this media rule."
            )
            remove_segment.clicked.connect(
                lambda _checked=False, media=kind: self._remove_media_segment(media)
            )
            segment_actions.addWidget(add_segment)
            segment_actions.addWidget(remove_segment)
            segment_actions.addStretch(1)
            segment_layout.addLayout(segment_actions)
            filename = QLineEdit(str(rule.get("filename_template", "{original}")))
            filename.textChanged.connect(
                lambda _value, media=kind: self._update_media_preview(media)
            )
            preview_frame = QFrame()
            preview_frame.setProperty("class", "preview")
            preview_layout = QVBoxLayout(preview_frame)
            caption = QLabel("PREVIEW")
            set_dynamic_class(caption, "muted")
            preview = QLabel()
            preview.setWordWrap(True)
            preview_layout.addWidget(caption)
            preview_layout.addWidget(preview)
            self.media_controls[kind] = {
                "enabled": enabled,
                "extensions": extensions,
                "segments": [],
                "segment_boxes": [],
                "segment_grid": segment_grid,
                "add_segment": add_segment,
                "remove_segment": remove_segment,
                "filename": filename,
                "preview": preview,
            }
            configured_segments = list(rule.get("folder_segments", []))
            initial_count = min(
                MAX_EDITABLE_SOURCE_LEVELS,
                max(1, len(configured_segments)),
            )
            for index in range(initial_count):
                value = (
                    configured_segments[index]
                    if index < len(configured_segments)
                    else ""
                )
                self._append_media_segment(kind, value)
            form.addRow("", enabled)
            form.addRow("Extensions", extensions)
            form.addRow("Destination folder levels", segment_row)
            form.addRow("Destination filename template", filename)
            form.addRow(preview_frame)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            tabs.addTab(scrollable(content), media_label)

        brackets = self.config["organization"][
            "long_exposure_brackets"
        ]
        grouping = QWidget()
        grouping_form = QFormLayout(grouping)
        grouping_form.setContentsMargins(18, 18, 18, 18)
        grouping_form.setHorizontalSpacing(18)
        grouping_form.setVerticalSpacing(12)
        grouping_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        grouping_form.setRowWrapPolicy(
            QFormLayout.RowWrapPolicy.WrapLongRows
        )
        self.bracket_enabled_check = QCheckBox(
            "Detect high-confidence long-exposure groups"
        )
        self.bracket_enabled_check.setChecked(
            bool(brackets.get("enabled", False))
        )
        self.bracket_enabled_check.setToolTip(
            "Match nearby JPEG and RAW captures by folder, camera, capture "
            "time, shutter speed, and exposure bias. Detection runs only when "
            "a media rule includes the Long-exposure brackets folder level."
        )
        self.bracket_folder_name_edit = QLineEdit(
            str(
                brackets.get(
                    "folder_name",
                    "Long Exposure Brackets",
                )
            )
        )
        self.bracket_folder_name_edit.setToolTip(
            "Folder name used when {capture_group} is populated. Captures "
            "that do not meet the confidence rules omit this conditional level."
        )
        self.bracket_minimum_group_spin = QSpinBox()
        self.bracket_minimum_group_spin.setRange(2, 12)
        self.bracket_minimum_group_spin.setValue(
            int(brackets.get("minimum_group_size", 3))
        )
        self.bracket_minimum_group_spin.setToolTip(
            "Minimum distinct captures required before a sequence can be "
            "treated as a long-exposure group."
        )
        self.bracket_maximum_gap_spin = self._number_spin(
            float(brackets.get("maximum_gap_seconds", 30.0)),
            0.5,
            600.0,
            decimals=1,
            suffix=" sec",
        )
        self.bracket_maximum_gap_spin.setToolTip(
            "Largest allowed capture-time gap between adjacent candidates."
        )
        self.bracket_minimum_exposure_spin = self._number_spin(
            float(
                brackets.get(
                    "minimum_long_exposure_seconds",
                    1.0,
                )
            ),
            0.0,
            3600.0,
            decimals=2,
            suffix=" sec",
        )
        self.bracket_minimum_exposure_spin.setToolTip(
            "At least one capture must meet this shutter duration, and the "
            "sequence must also vary shutter speed or exposure bias."
        )
        grouping_form.addRow("", self.bracket_enabled_check)
        grouping_form.addRow(
            "Conditional folder name",
            self.bracket_folder_name_edit,
        )
        grouping_form.addRow(
            "Minimum captures",
            self.bracket_minimum_group_spin,
        )
        grouping_form.addRow(
            "Maximum gap between captures",
            self.bracket_maximum_gap_spin,
        )
        grouping_form.addRow(
            "Minimum long exposure",
            self.bracket_minimum_exposure_spin,
        )
        tabs.addTab(scrollable(grouping), "Capture grouping")

        history = QWidget()
        history_form = QFormLayout(history)
        history_form.setContentsMargins(18, 18, 18, 18)
        history_form.setHorizontalSpacing(18)
        history_form.setVerticalSpacing(12)
        history_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        history_segments = list(self.config["identification"].get("history_folder_segments", []))
        history_row = QWidget()
        history_row_layout = QHBoxLayout(history_row)
        history_row_layout.setContentsMargins(0, 0, 0, 0)
        history_row_layout.setSpacing(6)
        self.history_segment_combos: list[QComboBox] = []
        for index in range(3):
            value = history_segments[index] if index < len(history_segments) else ""
            combo = self._segment_combo(value)
            combo.setToolTip(
                f"Session-record folder level {index + 1}. Levels are created inside the card's transfer-history folder."
            )
            combo.currentIndexChanged.connect(self._update_history_preview)
            self.history_segment_combos.append(combo)
            history_row_layout.addWidget(combo, 1)
        self.session_filename_edit = QLineEdit(
            self.config["identification"]["session_filename_template"]
        )
        self.checksum_filename_edit = QLineEdit(
            self.config["identification"]["checksum_filename_template"]
        )
        self.session_filename_edit.textChanged.connect(self._update_history_preview)
        self.checksum_filename_edit.textChanged.connect(self._update_history_preview)
        history_preview_frame = QFrame()
        history_preview_frame.setProperty("class", "preview")
        history_preview_layout = QVBoxLayout(history_preview_frame)
        history_caption = QLabel("PREVIEW")
        set_dynamic_class(history_caption, "muted")
        self.history_preview_label = QLabel()
        self.history_preview_label.setWordWrap(True)
        history_preview_layout.addWidget(history_caption)
        history_preview_layout.addWidget(self.history_preview_label)
        history_form.addRow("Session-record folder levels", history_row)
        history_form.addRow("Transfer-session filename template", self.session_filename_edit)
        history_form.addRow("Checksum-log filename template", self.checksum_filename_edit)
        history_form.addRow(history_preview_frame)
        tabs.addTab(scrollable(history), "Transfer records")
        for kind in MEDIA_LABELS:
            self._update_media_preview(kind)
        self._update_history_preview()

    @staticmethod
    def _number_spin(
        value: float,
        minimum: float,
        maximum: float,
        *,
        decimals: int = 1,
        suffix: str = "",
    ) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setValue(float(value))
        spin.setSuffix(suffix)
        return spin

    def _build_safety_page(self) -> None:
        _page, layout = self._new_page("Safety and location", "Safety and location")
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        safety = self.config["safety"]
        identification = self.config["identification"]
        local_history = self.config["local_history"]

        transfer = QWidget()
        transfer_form = QFormLayout(transfer)
        transfer_form.setContentsMargins(18, 18, 18, 18)
        transfer_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        destination_row = QWidget()
        destination_layout = QHBoxLayout(destination_row)
        destination_layout.setContentsMargins(0, 0, 0, 0)
        self.safety_destination_edit = QLineEdit(self.config["destination_root"])
        self.safety_destination_edit.setReadOnly(True)
        browse_destination = QPushButton("Manage libraries")
        self._add_icon(browse_destination, "settings")
        browse_destination.clicked.connect(
            lambda: self.show_page("Libraries")
        )
        destination_layout.addWidget(self.safety_destination_edit, 1)
        destination_layout.addWidget(browse_destination)
        self.default_action_combo = choice_combo(
            [("Copy", "copy"), ("Move", "move")], safety["default_action"]
        )
        self.copy_verification_combo = choice_combo(
            [("File size", "size"), ("SHA-256", "sha256"), ("SHA-512", "sha512"), ("BLAKE2b", "blake2b")],
            safety["copy_verification"],
        )
        self.move_checksum_combo = choice_combo(
            [("SHA-256", "sha256"), ("SHA-512", "sha512"), ("BLAKE2b", "blake2b")],
            safety["move_checksum_algorithm"],
        )
        self.move_checksum_combo.currentIndexChanged.connect(self._update_history_preview)
        self.shared_history_check = QCheckBox("Share transfer history across computers")
        self.shared_history_check.setChecked(bool(identification.get("shared_history", True)))
        self.require_portable_log_check = QCheckBox(
            "Require portable history before deleting a moved source"
        )
        self.require_portable_log_check.setChecked(
            bool(identification.get("require_log_before_source_delete", True))
        )
        self.local_history_enabled_check = QCheckBox(
            "Save matching session and checksum logs locally"
        )
        self.local_history_enabled_check.setChecked(bool(local_history.get("enabled", True)))
        local_row, self.local_history_edit = directory_editor(
            transfer,
            str(local_history.get("directory", "")),
            title="Choose the local transfer log directory",
        )
        self.require_local_log_check = QCheckBox(
            "Require matching local history before deleting a moved source"
        )
        self.require_local_log_check.setChecked(
            bool(local_history.get("require_before_source_delete", True))
        )
        transfer_form.addRow("Default library", destination_row)
        transfer_form.addRow("Default source-file handling", self.default_action_combo)
        transfer_form.addRow("Copy verification method", self.copy_verification_combo)
        transfer_form.addRow("Move verification algorithm", self.move_checksum_combo)
        transfer_form.addRow("", self.shared_history_check)
        transfer_form.addRow("", self.require_portable_log_check)
        transfer_form.addRow("", self.local_history_enabled_check)
        transfer_form.addRow("Local session-record folder", local_row)
        transfer_form.addRow("", self.require_local_log_check)
        tabs.addTab(scrollable(transfer), "Transfer safety")

        policies = QWidget()
        policies_form = QFormLayout(policies)
        policies_form.setContentsMargins(18, 18, 18, 18)
        policies_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.conflict_policy_label = QLabel("Keep both files in Conflict review")
        self.conflict_policy_label.setWordWrap(True)
        self.exact_duplicate_combo = choice_combo(
            [
                ("Append filename", "rename"),
                ("Conflict folder", "conflict_folder"),
                ("Skip", "skip"),
            ],
            safety.get("exact_duplicate_policy", "rename"),
        )
        self.manual_duplicate_check = QCheckBox("Ask about exact duplicates during manual imports")
        self.manual_duplicate_check.setChecked(bool(safety.get("manual_duplicate_prompt", False)))
        self.conflict_appendage_edit = QLineEdit(
            str(safety.get("conflict_filename_appendage", "_{number}"))
        )
        self.conflict_folder_edit = QLineEdit(str(safety.get("conflict_folder", "Conflicts")))
        self.space_policy_combo = choice_combo(
            [
                ("Try fallback destinations, then block", "fallback_then_block"),
                ("Block below reserve", "block"),
                ("Continue below reserve when physically possible", "continue_below_reserve"),
            ],
            safety.get("space_policy", "fallback_then_block"),
        )
        self.manual_space_check = QCheckBox("Ask during manual low-space conditions")
        self.manual_space_check.setChecked(bool(safety.get("manual_space_prompt", True)))
        fallback_row = QWidget()
        fallback_layout = QHBoxLayout(fallback_row)
        fallback_layout.setContentsMargins(0, 0, 0, 0)
        self.fallback_destinations_edit = QLineEdit(
            "; ".join(safety.get("fallback_destination_roots", []))
        )
        add_fallback = QPushButton("Add")
        self._add_icon(add_fallback, "add")
        add_fallback.setToolTip("Choose another library to try when earlier destinations are below their free-space reserve.")
        add_fallback.clicked.connect(self._add_fallback_destination)
        fallback_layout.addWidget(self.fallback_destinations_edit, 1)
        fallback_layout.addWidget(add_fallback)
        self.minimum_percent_spin = self._number_spin(
            safety["minimum_destination_free_percent"], 0, 99, suffix=" %"
        )
        self.minimum_gb_spin = self._number_spin(
            safety["minimum_destination_free_gb"], 0, 1_000_000, suffix=" GB"
        )
        self.source_warning_spin = self._number_spin(
            safety["warn_source_free_percent"], 0, 99, suffix=" %"
        )
        self.file_error_policy_combo = choice_combo(
            [
                ("Retry, then continue", "retry_then_continue"),
                ("Continue with next file", "continue"),
                ("Stop this card", "stop_card"),
            ],
            safety.get("file_error_policy", "retry_then_continue"),
        )
        self.manual_error_check = QCheckBox("Ask after retries during manual imports")
        self.manual_error_check.setChecked(bool(safety.get("manual_error_prompt", True)))
        self.retry_count_spin = QSpinBox()
        self.retry_count_spin.setRange(0, 10)
        self.retry_count_spin.setValue(int(safety.get("io_retry_count", 2)))
        self.retry_delay_spin = self._number_spin(
            safety.get("io_retry_delay_seconds", 1), 0, 30, suffix=" sec"
        )
        policies_form.addRow("Same name, different content", self.conflict_policy_label)
        policies_form.addRow("Exact-content duplicate", self.exact_duplicate_combo)
        policies_form.addRow("", self.manual_duplicate_check)
        policies_form.addRow("Conflict filename suffix", self.conflict_appendage_edit)
        policies_form.addRow("Conflict review folder", self.conflict_folder_edit)
        policies_form.addRow("Low-space response", self.space_policy_combo)
        policies_form.addRow("", self.manual_space_check)
        policies_form.addRow("Fallback libraries, in order", fallback_row)
        policies_form.addRow("Free-space reserve (percent)", self.minimum_percent_spin)
        policies_form.addRow("Free-space reserve (gigabytes)", self.minimum_gb_spin)
        policies_form.addRow("Source low-space warning", self.source_warning_spin)
        policies_form.addRow("Unreadable-file response", self.file_error_policy_combo)
        policies_form.addRow("", self.manual_error_check)
        policies_form.addRow("Automatic retries per file", self.retry_count_spin)
        policies_form.addRow("Delay between retries", self.retry_delay_spin)
        tabs.addTab(scrollable(policies), "Conflicts and space")

        backups = QWidget()
        backups_layout = QVBoxLayout(backups)
        backups_layout.setContentsMargins(18, 18, 18, 18)
        verification_row = QHBoxLayout()
        verification_row.addWidget(QLabel("Backup verification algorithm"))
        self.replica_verification_combo = choice_combo(
            [("SHA-256", "sha256"), ("SHA-512", "sha512"), ("BLAKE2b", "blake2b")],
            safety.get("replica_verification", "sha256"),
        )
        verification_row.addWidget(self.replica_verification_combo, 1)
        backups_layout.addLayout(verification_row)
        self.replica_table = QTableWidget(0, 6)
        self.replica_table.setHorizontalHeaderLabels(
            ("NAME", "ROOT", "REQUIRED", "HISTORY", "CONFLICT POLICY", "STATE")
        )
        self.replica_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.replica_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.replica_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.replica_table.verticalHeader().setVisible(False)
        replica_header = self.replica_table.horizontalHeader()
        replica_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        replica_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, 6):
            replica_header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        backups_layout.addWidget(self.replica_table, 1)
        replica_actions = QHBoxLayout()
        add_replica = accent(QPushButton("Add destination"))
        self._add_icon(add_replica, "add")
        add_replica.setToolTip("Add a verified backup or clone destination.")
        add_replica.clicked.connect(self._add_replica)
        edit_replica = QPushButton("Edit selected")
        self._add_icon(edit_replica, "edit")
        edit_replica.setToolTip("Edit the selected backup destination and whether it is required.")
        edit_replica.clicked.connect(self._edit_replica)
        remove_replica = QPushButton("Remove selected")
        self._add_icon(remove_replica, "remove")
        remove_replica.setToolTip("Remove this destination from the configuration. Existing backup files are unchanged.")
        remove_replica.setProperty("danger", True)
        remove_replica.clicked.connect(self._remove_replica)
        replica_actions.addWidget(add_replica)
        replica_actions.addWidget(edit_replica)
        replica_actions.addWidget(remove_replica)
        replica_actions.addStretch(1)
        backups_layout.addLayout(replica_actions)
        tabs.addTab(backups, "Backups and clones")

        location = QWidget()
        location_form = QFormLayout(location)
        location_form.setContentsMargins(18, 18, 18, 18)
        location_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.location_enabled_check = QCheckBox("Resolve GPS coordinates to online place names")
        self.location_enabled_check.setChecked(
            bool(self.config["location"].get("online_place_names", False))
        )
        self.location_user_agent_edit = QLineEdit(
            str(
                self.config["location"].get(
                    "user_agent", DEFAULT_USER_AGENT
                )
            )
        )
        location_form.addRow("", self.location_enabled_check)
        location_form.addRow("Provider", QLabel("Nominatim"))
        location_form.addRow("Online lookup identifier", self.location_user_agent_edit)
        tabs.addTab(scrollable(location), "Location")
        self._refresh_replicas()

    def _build_conflict_page(self) -> None:
        _page, layout = self._new_page("Conflict review", "Conflict review")
        self.conflict_page_size = 200
        self.conflict_page_index = 0
        top = QGridLayout()
        self.conflict_summary_label = QLabel("No conflicts to review")
        set_dynamic_class(self.conflict_summary_label, "muted")
        self.conflict_filter_combo = choice_combo(
            [("Open", "open"), ("Reviewed", "reviewed"), ("All", "all")], "open"
        )
        self.conflict_filter_combo.setToolTip("Show open conflicts, reviewed conflicts, or the complete review history.")
        self.conflict_filter_combo.currentIndexChanged.connect(
            self._reset_conflict_page
        )
        self.conflict_search_edit = QLineEdit()
        self.conflict_search_edit.setPlaceholderText(
            "Filter by filename, path, type, or resolution"
        )
        self.conflict_search_edit.setClearButtonEnabled(True)
        self.conflict_search_edit.setToolTip(
            "Search all retained conflict records without loading the complete history into the table."
        )
        self.conflict_search_edit.textChanged.connect(
            self._reset_conflict_page
        )
        refresh = QPushButton("Refresh")
        self._add_icon(refresh, "refresh")
        refresh.setToolTip("Reload conflict records from the primary destination library.")
        refresh.clicked.connect(self._refresh_conflicts)
        app = QApplication.instance()
        self.conflict_previous_button = QToolButton()
        self.conflict_previous_button.setIcon(standard_icon(app, "back"))
        self.conflict_previous_button.setToolTip("Show the previous conflict page.")
        self.conflict_previous_button.clicked.connect(
            lambda: self._change_conflict_page(-1)
        )
        self.conflict_page_label = QLabel("Page 1 of 1")
        self.conflict_page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.conflict_page_label.setMinimumWidth(92)
        self.conflict_next_button = QToolButton()
        self.conflict_next_button.setIcon(standard_icon(app, "next"))
        self.conflict_next_button.setToolTip("Show the next conflict page.")
        self.conflict_next_button.clicked.connect(
            lambda: self._change_conflict_page(1)
        )
        top.addWidget(self.conflict_summary_label, 0, 0)
        top.addWidget(QLabel("Show"), 0, 2)
        top.addWidget(self.conflict_filter_combo, 0, 3)
        top.addWidget(refresh, 0, 4)
        top.addWidget(self.conflict_search_edit, 1, 0, 1, 2)
        top.addWidget(self.conflict_previous_button, 1, 2)
        top.addWidget(self.conflict_page_label, 1, 3)
        top.addWidget(self.conflict_next_button, 1, 4)
        top.setColumnStretch(0, 1)
        layout.addLayout(top)
        self.conflict_table = QTableWidget(0, 4)
        self.conflict_table.setHorizontalHeaderLabels(("CREATED", "TYPE", "FILE", "RESOLUTION"))
        self.conflict_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.conflict_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.conflict_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.conflict_table.verticalHeader().setVisible(False)
        conflict_header = self.conflict_table.horizontalHeader()
        conflict_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        conflict_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        conflict_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        conflict_header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.conflict_table.itemSelectionChanged.connect(self._show_conflict)
        layout.addWidget(self.conflict_table, 1)

        comparison = QSplitter(Qt.Orientation.Horizontal)
        self.conflict_preview_labels: dict[str, QLabel] = {}
        self.conflict_detail_labels: dict[str, QLabel] = {}
        self.conflict_path_labels: dict[str, QLabel] = {}
        for side, title in (("existing", "Existing file"), ("incoming", "Incoming file")):
            panel = QGroupBox(title)
            panel_layout = QVBoxLayout(panel)
            panel_layout.setContentsMargins(10, 6, 10, 8)
            panel_layout.setSpacing(6)
            preview = AspectPreviewLabel("None selected")
            preview.setMinimumSize(180, 48)
            preview.setMaximumHeight(180)
            preview.setStyleSheet(
                f"background: {COLORS['surface_alt']}; border: 1px solid {COLORS['border']}; border-radius: 4px;"
            )
            details = QLabel()
            details.setWordWrap(True)
            details.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
            path = QLabel("None selected")
            path.setWordWrap(True)
            set_dynamic_class(path, "muted")
            open_button = QPushButton("Open")
            self._add_icon(open_button, "open")
            open_button.setToolTip(f"Open the {title.lower()} with its operating-system application.")
            open_button.clicked.connect(
                lambda _checked=False, selected_side=side: self._open_conflict_file(selected_side)
            )
            panel_layout.addWidget(preview, 1)
            panel_layout.addWidget(details)
            panel_layout.addWidget(path)
            panel_layout.addWidget(open_button, 0, Qt.AlignmentFlag.AlignLeft)
            self.conflict_preview_labels[side] = preview
            self.conflict_detail_labels[side] = details
            self.conflict_path_labels[side] = path
            comparison.addWidget(panel)
        comparison.setSizes([500, 500])
        layout.addWidget(comparison, 1)
        review_row = QHBoxLayout()
        self.conflict_resolution_label = QLabel()
        set_dynamic_class(self.conflict_resolution_label, "muted")
        self.mark_conflicts_reviewed_button = accent(
            QPushButton("Mark selected reviewed")
        )
        self._add_icon(self.mark_conflicts_reviewed_button, "save")
        self.mark_conflicts_reviewed_button.setToolTip(
            "Mark all selected preserved conflicts as reviewed without changing either file."
        )
        self.mark_conflicts_reviewed_button.clicked.connect(
            self._mark_conflict_reviewed
        )
        review_row.addWidget(self.conflict_resolution_label)
        review_row.addStretch(1)
        review_row.addWidget(self.mark_conflicts_reviewed_button)
        layout.addLayout(review_row)
        self._clear_conflict_preview()
        self._refresh_conflicts()

    def _build_activity_page(self) -> None:
        _page, layout = self._new_page("Activity", "Activity")
        self.activity_table = QTableWidget(0, 3)
        self.activity_table.setHorizontalHeaderLabels(("TIME", "LEVEL", "MESSAGE"))
        self.activity_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.activity_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.activity_table.verticalHeader().setVisible(False)
        header = self.activity_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.activity_table, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        clear = QPushButton("Clear activity")
        self._add_icon(clear, "remove")
        clear.setToolTip("Clear only the visible in-memory activity list. Transfer records are unchanged.")
        clear.clicked.connect(lambda: self.activity_table.setRowCount(0))
        row.addWidget(clear)
        layout.addLayout(row)

    def _build_help_page(self) -> None:
        _page, layout = self._new_page("Help & about", "Help & about")
        guide_group = QGroupBox("Documentation")
        guide_layout = QVBoxLayout(guide_group)
        guide_status = QLabel(
            f"Photo Card Organizer {__version__} includes a version-matched PDF manual."
        )
        guide_status.setWordWrap(True)
        self.user_guide_path_label = QLabel()
        self.user_guide_path_label.setWordWrap(True)
        set_dynamic_class(self.user_guide_path_label, "muted")
        guide_actions = QHBoxLayout()
        self.open_user_guide_button = accent(
            QPushButton("Open user guide")
        )
        self._add_icon(self.open_user_guide_button, "open")
        self.open_user_guide_button.setToolTip(
            "Open the version-matched PDF manual included with this release."
        )
        self.open_user_guide_button.clicked.connect(self._open_user_guide)
        self.open_changelog_button = QPushButton("Open changelog")
        self._add_icon(self.open_changelog_button, "open")
        self.open_changelog_button.setToolTip(
            "Open the cumulative release history, including previous versions."
        )
        self.open_changelog_button.clicked.connect(self._open_changelog)
        guide_actions.addWidget(self.open_user_guide_button)
        guide_actions.addWidget(self.open_changelog_button)
        guide_actions.addStretch(1)
        guide_layout.addWidget(guide_status)
        guide_layout.addWidget(self.user_guide_path_label)
        guide_layout.addLayout(guide_actions)
        layout.addWidget(guide_group)

        about_group = QGroupBox("About")
        about_layout = QVBoxLayout(about_group)
        name = QLabel("Photo Card Organizer")
        name.setObjectName("dialogTitle")
        version = QLabel(f"Version {__version__}")
        set_dynamic_class(version, "muted")
        description = QLabel(
            "A cross-platform media ingestion, verification, organization, "
            "backup, reconciliation, and conflict-review utility."
        )
        description.setWordWrap(True)
        credits_title = QLabel("PROGRAMMERS / DESIGNERS")
        set_dynamic_class(credits_title, "muted")
        human_credit = QLabel(
            "<b>The Meat Popsicle</b><br>"
            "Product direction, design judgment, field testing, and possession "
            "of the cameras."
        )
        robot_credit = QLabel(
            "<b>Codex, Latest Robot Overlord</b><br>"
            "Programming, systems design, documentation, and strictly limited "
            "authority over folder names."
        )
        human_credit.setWordWrap(True)
        robot_credit.setWordWrap(True)
        about_layout.addWidget(name)
        about_layout.addWidget(version)
        about_layout.addSpacing(8)
        about_layout.addWidget(description)
        about_layout.addSpacing(12)
        about_layout.addWidget(credits_title)
        about_layout.addWidget(human_credit)
        about_layout.addSpacing(6)
        about_layout.addWidget(robot_credit)
        layout.addWidget(about_group)
        layout.addStretch(1)
        self._refresh_user_guide_status()

    def _apply_tooltips(self) -> None:
        tooltips: list[tuple[QWidget, str]] = [
            (
                self.folder_name_edit,
                "A single folder created at the root of each newly onboarded card. Changing it does not rename metadata already stored on cards.",
            ),
            (
                self.identity_filename_edit,
                "The JSON filename that gives a newly onboarded card a stable identity across computers. Existing card files are not renamed.",
            ),
            (
                self.history_folder_edit,
                "The folder inside new card metadata where one transfer record and checksum log are created per session. Existing history is not moved.",
            ),
            (
                self.session_filename_edit,
                "Filename template for one JSON Lines transfer record per session. Date, card, computer, and session tokens are supported.",
            ),
            (
                self.checksum_filename_edit,
                "Filename template for the separate checksum log created for verified move sessions.",
            ),
            (
                self.existing_source_edit,
                "The folder to scan and add. It must be separate from the "
                "destination library.",
            ),
            (
                self.existing_name_edit,
                "A readable name used in activity and transfer records for this one-time folder source.",
            ),
            (self.existing_camera_edit, CAMERA_NAME_HELP),
            (
                self.existing_recursive_check,
                "Include supported files from every subfolder. Turn this off to scan only the selected folder itself.",
            ),
            (
                self.destination_edit,
                "The primary library that receives organized media. Source and destination cannot overlap.",
            ),
            (self.existing_prefix_edit, LIBRARY_SUBFOLDER_HELP),
            (
                self.existing_action_combo,
                "Copy keeps the source files. Move deletes each source only after configured verification, required backups, and logs succeed.",
            ),
            (
                self.existing_preset_combo,
                "Choose a simple destination layout, retain detailed per-media rules, or apply a detected source structure.",
            ),
            (
                self.safety_destination_edit,
                "The primary library used by card monitoring and manual imports.",
            ),
            (
                self.default_action_combo,
                "The starting choice for newly onboarded cards. Copy is the safer default and leaves source files untouched.",
            ),
            (
                self.copy_verification_combo,
                "File size is fastest. A cryptographic hash reads both files and provides stronger verification.",
            ),
            (
                self.move_checksum_combo,
                "Cryptographic algorithm used to verify a moved file before its source is eligible for deletion.",
            ),
            (
                self.poll_spin,
                "How often the background service checks for connected identified cards.",
            ),
            (
                self.shared_history_check,
                "Read transfer records stored on cards so another computer can identify files already handled.",
            ),
            (
                self.require_portable_log_check,
                "For card moves, do not delete a source unless its card-side session record was written successfully.",
            ),
            (
                self.local_history_enabled_check,
                "Keep a matching local copy of each session record and checksum log for audit and recovery.",
            ),
            (
                self.local_history_edit,
                "Optional local log folder. Leave blank to use the application's default per-user location.",
            ),
            (
                self.require_local_log_check,
                "For moves, require the local session record before deleting the source file.",
            ),
            (
                self.conflict_policy_label,
                "Preserves the existing file and routes the incoming file into the local conflict-review folder without pausing the import.",
            ),
            (
                self.exact_duplicate_combo,
                "Applied when incoming and existing files have exactly matching content.",
            ),
            (
                self.manual_duplicate_check,
                "Pause manual imports for exact-content duplicates instead of applying the default automatically.",
            ),
            (
                self.conflict_appendage_edit,
                "Suffix used before the extension when preserving both filenames. Include {number}, for example _{number} or ({number}).",
            ),
            (
                self.conflict_folder_edit,
                "Relative folder inside the destination library where preserved conflicts are organized for review.",
            ),
            (
                self.space_policy_combo,
                "Controls whether low space blocks, tries configured fallbacks, or permits use below the reserve when bytes still fit.",
            ),
            (
                self.manual_space_check,
                "Pause manual imports at low-space decisions so you can select a destination or response immediately.",
            ),
            (
                self.fallback_destinations_edit,
                "Semicolon-separated destination libraries tried from left to right when the primary library is below reserve.",
            ),
            (
                self.minimum_percent_spin,
                "Keep at least this percentage free after each transfer.",
            ),
            (
                self.minimum_gb_spin,
                "Keep at least this many gigabytes free after each transfer. Both free-space reserves are enforced.",
            ),
            (
                self.source_warning_spin,
                "Show a warning when a card has less than this percentage free. This does not delete files automatically.",
            ),
            (
                self.file_error_policy_combo,
                "Response after a source file cannot be read or a destination write fails.",
            ),
            (
                self.manual_error_check,
                "Pause manual imports after automatic retries so you can retry, continue, or stop that card.",
            ),
            (self.retry_count_spin, "Number of automatic retries for a failed file operation."),
            (self.retry_delay_spin, "Delay between automatic retries for the same file."),
            (
                self.replica_verification_combo,
                "Cryptographic algorithm used to verify every enabled backup or clone destination.",
            ),
            (
                self.location_enabled_check,
                "When EXIF contains GPS coordinates, query the configured online provider for a readable place name.",
            ),
            (
                self.location_user_agent_edit,
                "Identifier sent to the place-name provider. Use a stable application or contact identifier accepted by that service.",
            ),
        ]
        for widget, tooltip in tooltips:
            widget.setToolTip(tooltip)
        self.conflict_appendage_edit.setPlaceholderText("_{number}")
        self.existing_name_edit.setPlaceholderText("Example: Family archive")
        self.existing_camera_edit.setPlaceholderText("Use EXIF automatically")
        self.existing_prefix_edit.setPlaceholderText("Optional path inside the library")
        self.fallback_destinations_edit.setPlaceholderText("D:\\Photo Backup; E:\\Overflow")
        for kind, controls in self.media_controls.items():
            media = MEDIA_LABELS[kind]
            controls["enabled"].setToolTip(f"Include supported {media.lower()} files in scans and imports.")
            controls["extensions"].setToolTip(
                f"Comma-, space-, or semicolon-separated {media.lower()} extensions. A leading period is optional."
            )
            controls["filename"].setToolTip(
                "Template for the organized filename. {original} preserves the source name; {stem} and {ext} can be combined with metadata tokens."
            )
        for item_name, item in self.navigation_items.items():
            item.setToolTip(NAVIGATION_TOOLTIPS[item_name])

    def _navigation_changed(self, row: int) -> None:
        item = self.navigation.item(row)
        if item is None:
            return
        page_name = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(page_name, str):
            return
        page_index = self.page_indexes.get(page_name)
        if page_index is None:
            return
        self.stack.setCurrentIndex(page_index)
        if page_name == "Digest inboxes":
            self._refresh_digest_inboxes()

    def show_page(self, name: str) -> None:
        index = self.page_indexes.get(name)
        if index is None:
            return
        self.stack.setCurrentIndex(index)
        item = self.navigation_items.get(name)
        if item is not None:
            self.navigation.setCurrentItem(item)
        if hasattr(self, "edit_card_button"):
            self._update_card_action_state()
        if name == "Integrity" and hasattr(self, "integrity_panel"):
            self.integrity_panel.refresh()

    def _open_import_merge(self) -> None:
        selected = self._import_merge_target()
        self.show_page("Import or merge")
        if hasattr(self, "existing_step_stack"):
            self._set_existing_step(0)
        if selected is None or not hasattr(
            self,
            "existing_library_combo",
        ):
            return
        index = self.existing_library_combo.findData(
            str(selected.get("id", ""))
        )
        if index >= 0:
            self.existing_library_combo.setCurrentIndex(index)
            self._existing_library_changed()

    def _import_merge_target(self) -> dict | None:
        preferred = (
            self._selected_library_destination(),
            library_destination(
                {
                    "library_destinations": self.library_destinations,
                },
                self.default_library_id,
            ),
            *self.library_destinations,
        )
        seen: set[str] = set()
        for library in preferred:
            if library is None:
                continue
            library_id = str(library.get("id", ""))
            if library_id in seen:
                continue
            seen.add(library_id)
            if (
                library.get("enabled", True)
                and str(library.get("root", "")).strip()
            ):
                return library
        return None

    def _open_library_export(self) -> None:
        selected = self._selected_library_destination()
        self.show_page("Library export")
        if selected:
            set_combo_data(self.export_library_combo, str(selected["id"]))

    def _reorganize_selected_library(self) -> None:
        selected = self._selected_library_destination()
        if self._manual_import_running or not selected:
            return
        saved = self._saved_processing_config("reorganizing a library")
        if saved is None:
            return
        candidate = copy.deepcopy(saved)
        select_library_destination(candidate, str(selected["id"]))
        was_paused = self.monitor.is_paused
        self.monitor.set_paused(True)
        try:
            # Finish any current scan before taking the preview snapshot.
            ready = threading.Event()
            threading.Thread(target=lambda: self.monitor.run_exclusive(ready.set), daemon=True).start()
            while not ready.wait(0.04):
                QApplication.processEvents()
            dialog = ReorganizationDialog(self, candidate)
            if dialog.exec() != QDialog.DialogCode.Accepted or dialog.plan is None:
                return
            plan = dialog.plan
            algorithm = str(plan.config["safety"]["move_checksum_algorithm"]).upper()
            if not is_yes(QMessageBox.warning(
                self, "Confirm library reorganization",
                f"Library: {selected['name']}\nFolder: {candidate['destination_root']}\n"
                f"Proposed moves: {plan.changes}\nLayout: {dialog.preset.currentText()}\n"
                f"Save layout for future imports: {'Yes' if dialog.save_layout.isChecked() else 'No'}\n"
                f"Remove empty folders: {'Yes' if dialog.cleanup.isChecked() else 'No'}\n\n"
                "Same-filesystem moves rename files without rereading their contents. "
                f"Cross-filesystem copies receive one {algorithm} comparison before source removal. "
                f"Create missing checksum baselines: {'Yes' if dialog.baselines.isChecked() else 'No'}. "
                "Required backups and records remain enabled; conflicts are preserved for review.\n\nStart?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )):
                return
            if dialog.save_layout.isChecked():
                saved["organization"]["checksum_new_baselines"] = dialog.baselines.isChecked()
                saved_library = next(item for item in saved["library_destinations"] if item["id"] == selected["id"])
                overrides = saved_library.setdefault("organization_overrides", {})
                for kind, check in dialog.media.items():
                    if check.isChecked():
                        overrides[kind] = {key: copy.deepcopy(plan.config["media_rules"][kind][key])
                                           for key in ("folder_segments", "filename_template")}
                save_config(saved, self.config_path)
                self.config = saved
                self._load_config_into_controls()
                self.monitor.update_config(saved)
                self._set_settings_dirty(False)
            self._start_card_batch(
                [plan.card], config_override=plan.config, confirmation_complete=True,
                move_confirmation_complete=True, reorganization_plan=plan,
                remove_empty_folders=dialog.cleanup.isChecked(), resume_monitor_after=not was_paused,
            )
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not reorganize library", str(exc))
        finally:
            if not self._manual_import_running and not was_paused:
                self.monitor.set_paused(False)

    def _open_library_job(self, mode: str, source: Path, *, target: Path | None = None) -> None:
        selected = self._selected_library_destination()
        if self._manual_import_running or not selected:
            return
        saved = self._saved_processing_config("starting a library operation")
        if saved is None:
            return
        candidate = copy.deepcopy(saved)
        select_library_destination(candidate, str(selected["id"]))
        dialog = LibraryJobDialog(self, candidate, [source], mode=mode, migration_target=target)
        was_paused = self.monitor.is_paused
        self.monitor.set_paused(True)
        try:
            ready = threading.Event()
            threading.Thread(target=lambda: self.monitor.run_exclusive(ready.set), daemon=True).start()
            while not ready.wait(0.04):
                QApplication.processEvents()
            self._manual_import_running = True
            if dialog.exec() == QDialog.DialogCode.Accepted:
                if mode == "migrate" and target is not None:
                    updated = copy.deepcopy(saved)
                    for library in updated["library_destinations"]:
                        if str(library.get("id")) == str(selected["id"]):
                            library["root"] = str(target.expanduser())
                    save_config(updated, self.config_path)
                    self.config = normalize_config(updated)
                    self._load_config_into_controls()
                    self.monitor.update_config(self.config)
                    self._set_settings_dirty(False)
                self.events.put(ActivityEvent("success", f"{mode.title()} operation completed"))
                self._refresh_library_destinations()
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Library operation needs attention", str(exc))
        finally:
            self._manual_import_running = False
            if not was_paused:
                self.monitor.set_paused(False)

    def _merge_selected_library(self) -> None:
        selected = self._selected_library_destination()
        if not selected:
            return
        source_text = QFileDialog.getExistingDirectory(self, "Choose library or folder to merge")
        if source_text:
            self._open_library_job("merge", Path(source_text))

    def _migrate_selected_library(self) -> None:
        selected = self._selected_library_destination()
        if not selected or not selected.get("root"):
            return
        target_text = QFileDialog.getExistingDirectory(self, "Choose empty migration destination")
        if target_text and Path(target_text).resolve() != Path(selected["root"]).expanduser().resolve():
            self._open_library_job("migrate", Path(selected["root"]), target=Path(target_text))

    def _refresh_import_library_choices(self) -> None:
        for attribute in (
            "import_library_combo",
            "existing_library_combo",
            "export_library_combo",
        ):
            combo = getattr(self, attribute, None)
            if combo is None:
                continue
            previous = str(
                combo.currentData()
                or self.default_library_id
            )
            combo.blockSignals(True)
            combo.clear()
            for library in self.library_destinations:
                if (
                    not library.get("enabled", True)
                    or not str(library.get("root", "")).strip()
                ):
                    continue
                kind = {
                    "local": "Local",
                    "network": "Network",
                    "removable": "Removable",
                }.get(
                    str(library.get("kind", "local")),
                    "Local",
                )
                combo.addItem(
                    f"{library['name']} — {kind}",
                    str(library["id"]),
                )
            selected_index = combo.findData(previous)
            if selected_index < 0:
                selected_index = combo.findData(
                    self.default_library_id
                )
            if selected_index >= 0:
                combo.setCurrentIndex(selected_index)
            combo.blockSignals(False)
        if hasattr(self, "import_destination_preview"):
            self._update_import_destination_preview()
        if hasattr(self, "existing_library_combo"):
            self._existing_library_changed()

    def _selected_import_library(self) -> dict | None:
        if not hasattr(self, "import_library_combo"):
            return None
        return library_destination(
            {
                "library_destinations": self.library_destinations,
            },
            str(self.import_library_combo.currentData() or ""),
        )

    def _existing_library_changed(self, *_args) -> None:
        if not hasattr(self, "existing_library_combo"):
            return
        selected = library_destination(
            {
                "library_destinations": self.library_destinations,
            },
            str(self.existing_library_combo.currentData() or ""),
        )
        if selected is not None:
            root = str(selected.get("root", ""))
            self.destination_edit.setText(root)
            self.destination_edit.setCursorPosition(0)
            self.destination_edit.setToolTip(
                f"Library folder: {root}\n"
                "This receiving folder cannot overlap the source."
            )
        self._update_existing_preview()

    def _update_existing_folder_route(self, *_args) -> None:
        if not hasattr(self, "existing_folder_mode_combo"):
            return
        mode = combo_value(self.existing_folder_mode_combo)
        named = mode != "standard"
        self.existing_event_name_edit.setEnabled(named)
        self.existing_destination_form.setRowVisible(
            self.existing_event_name_edit,
            named,
        )
        self.existing_destination_form.setRowVisible(
            self.existing_prefix_edit,
            named,
        )
        prefix = ""
        if named:
            try:
                prefix = import_destination_prefix(
                    mode,
                    self.existing_event_name_edit.text(),
                )
            except ValueError:
                prefix = ""
        self.existing_prefix_edit.setText(prefix)
        self._update_existing_preview()

    def _update_import_destination_preview(self, *_args) -> None:
        if not hasattr(self, "import_destination_preview"):
            return
        mode = combo_value(self.import_folder_mode_combo)
        named = mode != "standard"
        self.import_folder_name_edit.setEnabled(named)
        self.import_folder_name_edit.setVisible(named)
        self.import_folder_name_label.setVisible(named)
        selected = self._selected_import_library()
        if selected is None:
            self.import_destination_preview.setText(
                "Add or enable a library destination before importing."
            )
            return
        root = Path(str(selected["root"]))
        if not named:
            self.import_destination_preview.setText(
                f"{root} / [saved card folder] / [organization rules]"
            )
            return
        try:
            prefix = import_destination_prefix(
                mode,
                self.import_folder_name_edit.text(),
            )
        except ValueError:
            prefix = "[enter a name]"
        self.import_destination_preview.setText(
            f"{root / prefix} / [organization rules]"
        )

    @staticmethod
    def _sync_destination(source: QLineEdit, target: QLineEdit, value: str) -> None:
        if target.text() == value:
            return
        target.blockSignals(True)
        target.setText(value)
        target.blockSignals(False)

    @staticmethod
    def _example_template(value: str, kind: str, *, algorithm: str = "sha256") -> str:
        now = datetime(2026, 7, 12, 14, 30, 5)
        source_name = {
            "photo": "IMG_0421.JPG",
            "raw": "IMG_0421.CR3",
            "video": "MVI_0421.MP4",
            "sidecar": "IMG_0421.XMP",
        }.get(kind, "IMG_0421.JPG")
        rendered = re.sub(
            r"\{date:([^}]+)\}",
            lambda match: now.strftime(match.group(1)),
            value,
        )
        replacements = {
            "{camera}": "Canon EOS R5",
            "{location}": "Asheville, North Carolina",
            "{rating}": "4 stars",
            "{card}": "R5 Card A",
            "{card_id}": "r5-card-a",
            "{media}": MEDIA_LABELS.get(kind, "Photos"),
            "{original}": source_name,
            "{stem}": Path(source_name).stem,
            "{ext}": Path(source_name).suffix.lower().lstrip("."),
            "{computer}": "Studio-PC",
            "{session}": "a1b2c3d4",
            "{instance}": "8f90e1a2",
            "{library}": "7d12c4b9",
            "{algorithm}": algorithm,
        }
        source_examples = (
            "Photos",
            "Archive",
            "2026",
            "2026-07-12",
            "Canon EOS R5",
            "Final selects",
        )
        for index in range(1, MAX_EDITABLE_SOURCE_LEVELS + 1):
            replacements[f"{{source_dir:{index}}}"] = (
                source_examples[index - 1]
                if index <= len(source_examples)
                else f"Source folder {index}"
            )
        for token, replacement in replacements.items():
            rendered = rendered.replace(token, replacement)
        return safe_segment(rendered)

    def _update_media_preview(self, kind: str) -> None:
        controls = self.media_controls.get(kind)
        if not controls:
            return
        segments = [
            self._example_template(combo_value(combo), kind)
            for combo in controls["segments"]
            if combo_value(combo)
        ]
        filename = self._example_template(
            controls["filename"].text().strip() or "{original}", kind
        )
        controls["preview"].setText(str(Path(*segments, filename)) if segments else filename)

    def _update_history_preview(self) -> None:
        if not hasattr(self, "history_preview_label"):
            return
        algorithm = combo_value(self.move_checksum_combo) if hasattr(self, "move_checksum_combo") else "sha256"
        segments = [
            self._example_template(combo_value(combo), "photo", algorithm=algorithm)
            for combo in self.history_segment_combos
            if combo_value(combo)
        ]
        session_name = self._example_template(
            self.session_filename_edit.text().strip() or "transfer-session.jsonl",
            "photo",
            algorithm=algorithm,
        )
        checksum_name = self._example_template(
            self.checksum_filename_edit.text().strip() or "transfer-session.sha256",
            "photo",
            algorithm=algorithm,
        )
        folder = Path(self.history_folder_edit.text().strip() or ".photocard", *segments)
        self.history_preview_label.setText(f"{folder / session_name}   |   {folder / checksum_name}")

    def _set_existing_step(self, index: int) -> None:
        if not hasattr(self, "existing_step_stack"):
            return
        index = max(0, min(2, int(index)))
        titles = ("Source", "Destination", "Review")
        details = (
            "Choose the folder whose files you want to add",
            "Choose the receiving library and folder layout",
            "Confirm what will be copied, verified, and recorded",
        )
        self.existing_step_stack.setCurrentIndex(index)
        self.existing_step_progress.setValue(index + 1)
        self.existing_step_label.setText(f"Step {index + 1} of 3: {titles[index]}")
        self.existing_step_detail.setText(details[index])
        self.existing_back_button.setVisible(index > 0)
        self.existing_next_button.setVisible(index < 2)
        self.existing_review_button.setVisible(index == 2)
        self.existing_next_button.setText(
            "Continue to destination"
            if index == 0
            else "Continue to review"
        )
        if index == 2:
            self._update_existing_review_summary()
        self._update_existing_review_state()

    def _existing_step_back(self) -> None:
        self._set_existing_step(self.existing_step_stack.currentIndex() - 1)

    def _existing_step_next(self) -> None:
        current = self.existing_step_stack.currentIndex()
        try:
            if current == 0:
                self._validate_existing_source()
            elif current == 1:
                self._build_existing_import_plan()
            else:
                return
        except (OSError, ValueError) as exc:
            QMessageBox.critical(
                self,
                "Check import or merge setup",
                str(exc),
            )
            return
        self._set_existing_step(current + 1)

    def _validate_existing_source(self) -> Path:
        source = Path(self.existing_source_edit.text()).expanduser().resolve()
        if not source.is_dir():
            raise ValueError("Choose a source folder.")
        if not self.existing_name_edit.text().strip():
            raise ValueError("Enter a short source name.")
        return source

    def _reset_existing_structure(self) -> None:
        self.existing_structure_rules = None
        self.existing_structure_analysis = None
        if not hasattr(self, "existing_preset_combo"):
            return
        detected_index = self.existing_preset_combo.findText(
            "Detected existing structure"
        )
        if detected_index >= 0:
            self.existing_preset_combo.removeItem(detected_index)
        if hasattr(self, "existing_structure_status_label"):
            self.existing_structure_status_label.setText(
                "Source structure has not been analyzed. The selected folder layout will be used."
            )
            set_dynamic_class(self.existing_structure_status_label, "muted")

    def _existing_source_scope_changed(self, *_args) -> None:
        self._reset_existing_structure()
        self._update_existing_preview()

    def _update_existing_preview(self) -> None:
        if not hasattr(self, "existing_preview_label"):
            return
        source = self.existing_source_edit.text().strip() or "No source selected"
        destination = self.destination_edit.text().strip() or "No destination selected"
        prefix = self.existing_prefix_edit.text().strip("/\\")
        if prefix and destination != "No destination selected":
            destination = str(Path(destination) / prefix)
        media = ", ".join(
            MEDIA_LABELS[kind]
            for kind, check in self.existing_media_checks.items()
            if check.isChecked()
        ) or "None"
        preset = self.existing_preset_combo.currentText()
        if preset == "Detected existing structure" and self.existing_structure_rules:
            mapped = []
            for kind, segments in self.existing_structure_rules.items():
                if self.existing_media_checks[kind].isChecked():
                    labels = [TEMPLATE_LABELS.get(segment, segment) for segment in segments]
                    mapped.append(f"{MEDIA_LABELS[kind]}: {' / '.join(labels) if labels else 'no folders'}")
            organization = "; ".join(mapped) or "Detected structure"
        else:
            organization = preset
        self.existing_preview_label.setText(
            f"{source}  ->  {destination}\n"
            f"{self.existing_action_combo.currentText()} | {media} | {organization}"
        )
        if not self.existing_source_edit.text().strip():
            source_state = "Choose a folder to continue."
        elif not self.existing_name_edit.text().strip():
            source_state = "Enter a source name to continue."
        else:
            scope = (
                "folder and subfolders"
                if self.existing_recursive_check.isChecked()
                else "top-level files only"
            )
            source_state = (
                f"Ready: {self.existing_name_edit.text().strip()} | {scope}"
            )
        self.existing_source_state_label.setText(source_state)
        self.existing_move_warning.setVisible(
            combo_value(self.existing_action_combo) == "move"
        )
        if self.existing_step_stack.currentIndex() == 2:
            self._update_existing_review_summary()
        self._update_existing_review_state()

    def _update_existing_review_state(self) -> None:
        if not hasattr(self, "existing_review_button"):
            return
        source_ready = bool(
            self.existing_source_edit.text().strip()
            and self.existing_name_edit.text().strip()
        )
        plan_ready = bool(
            source_ready
            and self.destination_edit.text().strip()
            and self.existing_library_combo.currentData()
            and (
                combo_value(self.existing_folder_mode_combo)
                == "standard"
                or self.existing_event_name_edit.text().strip()
            )
            and any(check.isChecked() for check in self.existing_media_checks.values())
        )
        current = self.existing_step_stack.currentIndex()
        self.existing_next_button.setEnabled(
            not self._manual_import_running
            and ((current == 0 and source_ready) or (current == 1 and plan_ready))
        )
        self.existing_review_button.setEnabled(
            current == 2 and plan_ready and not self._manual_import_running
        )
        self.existing_analyze_button.setEnabled(
            source_ready
            and any(check.isChecked() for check in self.existing_media_checks.values())
            and not self._manual_import_running
        )

    def _build_existing_import_plan(
        self,
    ) -> tuple[dict, CardMarker, str]:
        source_root = self._validate_existing_source()
        if not any(
            check.isChecked() for check in self.existing_media_checks.values()
        ):
            raise ValueError("Enable at least one media type.")
        candidate = self._collect_config()
        selected_library_id = str(
            self.existing_library_combo.currentData() or ""
        )
        if not selected_library_id:
            raise ValueError("Choose a managed destination library.")
        select_library_destination(
            candidate,
            selected_library_id,
        )
        destination_root = Path(candidate["destination_root"]).expanduser()
        if paths_overlap(source_root, destination_root):
            raise ValueError(
                "The existing source library and destination library must be separate locations."
            )
        preset = self.existing_preset_combo.currentText()
        for kind, rule in candidate["media_rules"].items():
            rule["enabled"] = self.existing_media_checks[kind].isChecked()
            if preset == "Detected existing structure":
                if not self.existing_structure_rules:
                    raise ValueError(
                        "Analyze the source folders or choose another folder layout."
                    )
                rule["folder_segments"] = list(
                    self.existing_structure_rules.get(kind, [])
                )
            else:
                rule["folder_segments"] = preset_folder_segments(
                    kind,
                    preset,
                    list(rule.get("folder_segments", [])),
                )
        candidate = normalize_config(candidate)
        folder_mode = combo_value(
            self.existing_folder_mode_combo
        )
        destination_prefix = import_destination_prefix(
            folder_mode,
            self.existing_event_name_edit.text(),
        )
        card = folder_import_source(
            source_root,
            name=self.existing_name_edit.text(),
            action=combo_value(self.existing_action_combo),
            include_subfolders=self.existing_recursive_check.isChecked(),
            destination_prefix=destination_prefix,
            camera_name=self.existing_camera_edit.text(),
        )
        return candidate, card, preset

    def _update_existing_review_summary(self) -> None:
        if not hasattr(self, "existing_review_summary"):
            return
        try:
            candidate, card, preset = self._build_existing_import_plan()
            summary = initial_import_summary(card, candidate, preset)
        except (OSError, ValueError) as exc:
            summary = f"Return to the previous step and correct this item:\n\n{exc}"
        self.existing_review_summary.setPlainText(summary)

    def _browse_destination(self) -> None:
        current = self.safety_destination_edit.text() if hasattr(self, "safety_destination_edit") else self.destination_edit.text()
        selected = QFileDialog.getExistingDirectory(self, "Choose the destination library", current)
        if selected:
            self.destination_edit.setText(selected)
            self.safety_destination_edit.setText(selected)

    def _browse_existing_source(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose a folder to import or merge",
            self.existing_source_edit.text(),
        )
        if not selected:
            return
        self.existing_source_edit.setText(selected)
        if not self.existing_name_edit.text().strip():
            self.existing_name_edit.setText(
                Path(selected).name or "Imported folder"
            )
        self._update_existing_preview()

    def _detect_existing_structure(self) -> None:
        try:
            source = self._validate_existing_source()
            if not any(
                check.isChecked()
                for check in self.existing_media_checks.values()
            ):
                raise ValueError("Enable at least one media type.")
            candidate = self._collect_config()
            for kind, rule in candidate["media_rules"].items():
                rule["enabled"] = self.existing_media_checks[kind].isChecked()
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Cannot analyze library", str(exc))
            return

        result: dict[str, object] = {}
        completed = threading.Event()
        cancelled = threading.Event()

        def work() -> None:
            try:
                result["analysis"] = detect_existing_structure(
                    source,
                    candidate["media_rules"],
                    include_subfolders=self.existing_recursive_check.isChecked(),
                    cancel_event=cancelled,
                )
            except Exception as exc:  # surfaced on the UI thread below
                result["error"] = exc
            finally:
                completed.set()

        threading.Thread(target=work, name="library-structure-analysis", daemon=True).start()
        progress = QProgressDialog(
            "Reading supported files and folder names...",
            "Cancel",
            0,
            0,
            self,
        )
        progress.setWindowTitle("Detect existing organization")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()
        while not completed.wait(0.04):
            QApplication.processEvents()
            if progress.wasCanceled():
                cancelled.set()
                progress.setLabelText("Cancelling analysis...")
        progress.close()
        QApplication.processEvents()

        error = result.get("error")
        if isinstance(error, InterruptedError):
            self.status_label.setText("Structure analysis cancelled")
            return
        if error is not None:
            QMessageBox.critical(self, "Could not analyze library", str(error))
            return
        analysis = result.get("analysis")
        if not isinstance(analysis, StructureAnalysis):
            QMessageBox.critical(self, "Could not analyze library", "No analysis result was returned.")
            return
        if not analysis.matched_files:
            QMessageBox.information(
                self,
                "No supported media found",
                "No files matched the currently configured photo, RAW, video, or sidecar extensions.",
            )
            return
        current_rules = {
            kind: [combo_value(combo) for combo in controls["segments"] if combo_value(combo)]
            for kind, controls in self.media_controls.items()
        }
        dialog = StructureMappingDialog(self, analysis, current_rules)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_rules:
            return
        self.existing_structure_rules = dialog.result_rules
        self.existing_structure_analysis = analysis
        label = "Detected existing structure"
        if self.existing_preset_combo.findText(label) < 0:
            self.existing_preset_combo.insertItem(0, label)
        self.existing_preset_combo.setCurrentText(label)
        self.existing_structure_status_label.setText(
            f"Applied an editable mapping from {analysis.matched_files} supported files "
            f"in {analysis.scanned_folders} folders."
        )
        set_dynamic_class(self.existing_structure_status_label, "success")
        self.status_label.setText(
            f"Detected organization from {analysis.matched_files} supported files"
        )
        self._update_existing_preview()

    def _review_existing_import(self) -> None:
        if self._manual_import_running:
            return
        try:
            candidate, card, preset = self._build_existing_import_plan()
        except (OSError, ValueError) as exc:
            QMessageBox.critical(
                self,
                "Cannot import or merge",
                str(exc),
            )
            return
        summary = initial_import_summary(card, candidate, preset)
        if not is_yes(
            QMessageBox.question(
                self,
                "Confirm import or merge",
                f"{summary}\n\nStart this import?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        ):
            return
        try:
            save_config(candidate, self.config_path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not save import settings", str(exc))
            return
        self.config = candidate
        self._load_config_into_controls()
        self.monitor.update_config(candidate)
        if self._start_card_batch(
            [card],
            confirmation_complete=True,
            config_override=candidate,
        ):
            self._set_existing_step(0)

    def _selected_digest_inboxes(self) -> list[dict]:
        selection = self.digest_table.selectionModel()
        if selection is None:
            return []
        selected_ids = []
        for index in selection.selectedRows(0):
            item = self.digest_table.item(index.row(), 0)
            if item is not None:
                selected_ids.append(
                    str(item.data(Qt.ItemDataRole.UserRole) or "")
                )
        return [
            inbox
            for inbox in self.digest_inboxes
            if str(inbox.get("id", "")) in selected_ids
        ]

    @staticmethod
    def _local_timestamp(value: object, *, fallback: str = "Never") -> str:
        text = str(value or "")
        if not text:
            return fallback
        try:
            return datetime.fromisoformat(text).astimezone().strftime(
                "%Y-%m-%d %H:%M"
            )
        except ValueError:
            return text

    def _digest_selection_changed(self) -> None:
        self._update_digest_action_state()
        self._refresh_digest_queue()

    def _refresh_digest_inboxes(self) -> None:
        if not hasattr(self, "digest_table"):
            return
        selected_ids = {
            str(inbox["id"]) for inbox in self._selected_digest_inboxes()
        }
        try:
            manifest = ImportManifest(
                Path(self.config["destination_root"]), create=False
            )
        except (OSError, ValueError):
            manifest = None
        self.digest_table.blockSignals(True)
        self.digest_table.setRowCount(len(self.digest_inboxes))
        selected_rows = []
        for row, inbox in enumerate(self.digest_inboxes):
            root_text = str(inbox.get("root", ""))
            enabled = bool(inbox.get("enabled", True))
            available = directory_available(root_text)
            auto = bool(inbox.get("auto_digest", False))
            if not enabled:
                state = "Disabled"
            elif not available:
                state = "Offline"
            elif auto:
                state = "Monitoring"
            else:
                state = "Ready"
            summary = (
                manifest.digest_summary(str(inbox["id"]))
                if manifest is not None
                else {
                    "pending": 0,
                    "processed": 0,
                    "failed": 0,
                    "conflict": 0,
                    "last_run_at": "",
                }
            )
            issues = int(summary.get("failed", 0)) + int(
                summary.get("conflict", 0)
            )
            values = (
                str(inbox["name"]),
                (
                    Path(root_text).name
                    if root_text and Path(root_text).name
                    else root_text or "Path not set"
                ),
                (
                    "Verified move"
                    if inbox.get("action") == "move"
                    else "Copy"
                ),
                state,
                str(summary.get("pending", 0)),
                str(summary.get("processed", 0)),
                str(issues),
                self._local_timestamp(summary.get("last_run_at")),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(
                    root_text if column == 1 and root_text else value
                )
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, inbox["id"])
                if column == 3:
                    color_name = {
                        "Ready": "success",
                        "Monitoring": "success",
                        "Offline": "warning",
                        "Disabled": "muted",
                    }.get(value, "text")
                    item.setForeground(QColor(COLORS[color_name]))
                if column == 6 and issues:
                    item.setForeground(QColor(COLORS["warning"]))
                self.digest_table.setItem(row, column, item)
            if str(inbox["id"]) in selected_ids:
                selected_rows.append(row)
        self.digest_table.clearSelection()
        for row in selected_rows:
            self.digest_table.selectRow(row)
        self.digest_table.blockSignals(False)
        self._update_digest_action_state()
        self._refresh_digest_queue()

    def _refresh_digest_queue(self) -> None:
        if not hasattr(self, "digest_queue_table"):
            return
        selected = self._selected_digest_inboxes()
        profiles = selected or self.digest_inboxes
        status = combo_value(self.digest_status_filter)
        total_records = 0
        try:
            manifest = ImportManifest(
                Path(self.config["destination_root"]), create=False
            )
            records = []
            for inbox in profiles:
                digest_summary = manifest.digest_summary(str(inbox["id"]))
                total_records += int(
                    digest_summary[
                        "total" if status == "all" else status
                    ]
                    or 0
                )
                for record in manifest.digest_items(
                    str(inbox["id"]),
                    status=status,
                    limit=1000,
                ):
                    record["_profile_name"] = str(inbox["name"])
                    records.append(record)
            records.sort(
                key=lambda record: str(record.get("updated_at", "")),
                reverse=True,
            )
            records = records[:1000]
        except (OSError, ValueError):
            records = []
            total_records = 0
        if total_records > len(records):
            self.digest_queue_count_label.setText(
                f"Showing latest {len(records):,} of {total_records:,}"
            )
        else:
            self.digest_queue_count_label.setText(
                f"{total_records:,} {'file' if total_records == 1 else 'files'}"
            )
        self.digest_queue_table.setRowCount(len(records))
        for row, record in enumerate(records):
            record_status = str(record.get("status", "")).upper()
            destination_or_error = str(
                record.get("error")
                or record.get("destination_path")
                or "Awaiting next digest"
            )
            values = (
                record_status,
                str(record.get("_profile_name", "")),
                str(record.get("media_kind", "")).upper(),
                str(record.get("relative_path", "")),
                destination_or_error,
                self._local_timestamp(
                    record.get("updated_at"), fallback=""
                ),
            )
            details = (
                f"Source: {record.get('source_path', '')}\n"
                f"Destination: {record.get('destination_path', '')}\n"
                f"Error: {record.get('error', '')}"
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(details if column in {3, 4} else value)
                if column == 0:
                    color_name = {
                        "PROCESSED": "success",
                        "PENDING": "muted",
                        "FAILED": "error",
                        "CONFLICT": "warning",
                    }.get(record_status, "text")
                    item.setForeground(QColor(COLORS[color_name]))
                self.digest_queue_table.setItem(row, column, item)

    def _update_digest_action_state(self) -> None:
        if not hasattr(self, "digest_table"):
            return
        selected = self._selected_digest_inboxes()
        one = len(selected) == 1
        available = any(
            inbox.get("enabled", True)
            and inbox.get("root")
            and directory_available(str(inbox["root"]))
            for inbox in selected
        )
        self.add_digest_button.setEnabled(not self._manual_import_running)
        self.edit_digest_button.setEnabled(
            one and not self._manual_import_running
        )
        self.open_digest_button.setEnabled(
            one
            and bool(selected[0].get("root"))
            and directory_available(str(selected[0]["root"]))
        )
        self.remove_digest_button.setEnabled(
            bool(selected) and not self._manual_import_running
        )
        self.run_digest_button.setEnabled(
            available and not self._manual_import_running
        )

    def _persist_digest_inboxes(self) -> bool:
        candidate = copy.deepcopy(self.config)
        candidate["digest_inboxes"] = copy.deepcopy(self.digest_inboxes)
        try:
            candidate = normalize_config(candidate)
            save_config(candidate, self.config_path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(
                self, "Could not save Digest Inboxes", str(exc)
            )
            return False
        self.config = candidate
        self.digest_inboxes = copy.deepcopy(candidate["digest_inboxes"])
        self.monitor.update_config(candidate)
        self._refresh_digest_inboxes()
        self._update_settings_dirty_state()
        return True

    def _add_digest_inbox(self) -> None:
        primary = Path(
            self.safety_destination_edit.text().strip()
            or self.config["destination_root"]
        )
        dialog = DigestInboxDialog(self, primary)
        if (
            dialog.exec() != QDialog.DialogCode.Accepted
            or not dialog.result_inbox
        ):
            return
        candidate = dict(dialog.result_inbox)
        existing_ids = {str(inbox["id"]) for inbox in self.digest_inboxes}
        base_id = str(candidate["id"])
        suffix = 2
        while candidate["id"] in existing_ids:
            candidate["id"] = f"{base_id}-{suffix}"
            suffix += 1
        candidate_root = path_key(candidate["root"]) if candidate["root"] else ""
        if candidate_root and any(
            inbox.get("root")
            and path_key(inbox["root"]) == candidate_root
            for inbox in self.digest_inboxes
        ):
            QMessageBox.critical(
                self,
                "Duplicate Digest Inbox",
                "That incoming folder is already retained.",
            )
            return
        self.digest_inboxes.append(candidate)
        if self._persist_digest_inboxes():
            for row in range(self.digest_table.rowCount()):
                item = self.digest_table.item(row, 0)
                if (
                    item is not None
                    and item.data(Qt.ItemDataRole.UserRole) == candidate["id"]
                ):
                    self.digest_table.selectRow(row)
                    break

    def _edit_digest_inbox(self) -> None:
        selected = self._selected_digest_inboxes()
        if len(selected) != 1:
            return
        original = selected[0]
        primary = Path(
            self.safety_destination_edit.text().strip()
            or self.config["destination_root"]
        )
        dialog = DigestInboxDialog(self, primary, original)
        if (
            dialog.exec() != QDialog.DialogCode.Accepted
            or not dialog.result_inbox
        ):
            return
        candidate = dialog.result_inbox
        candidate_root = path_key(candidate["root"]) if candidate["root"] else ""
        if candidate_root and any(
            inbox["id"] != original["id"]
            and inbox.get("root")
            and path_key(inbox["root"]) == candidate_root
            for inbox in self.digest_inboxes
        ):
            QMessageBox.critical(
                self,
                "Duplicate Digest Inbox",
                "That incoming folder is already retained.",
            )
            return
        self.digest_inboxes = [
            candidate if inbox["id"] == original["id"] else inbox
            for inbox in self.digest_inboxes
        ]
        self._persist_digest_inboxes()

    def _remove_digest_inboxes(self) -> None:
        selected = self._selected_digest_inboxes()
        if not selected:
            return
        names = ", ".join(str(inbox["name"]) for inbox in selected)
        if not is_yes(
            QMessageBox.question(
                self,
                "Forget Digest Inbox",
                f"Forget {names}?\n\n"
                "This removes only the retained inbox profile. Incoming files, "
                "master-library files, and local digest history remain unchanged.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        ):
            return
        selected_ids = {str(inbox["id"]) for inbox in selected}
        self.digest_inboxes = [
            inbox
            for inbox in self.digest_inboxes
            if str(inbox["id"]) not in selected_ids
        ]
        self._persist_digest_inboxes()

    def _open_digest_inbox(self) -> None:
        selected = self._selected_digest_inboxes()
        if len(selected) != 1:
            return
        try:
            open_local_path(Path(str(selected[0]["root"])).expanduser())
        except OSError as exc:
            QMessageBox.critical(
                self, "Could not open Digest Inbox", str(exc)
            )

    def _digest_selected_inboxes(self) -> None:
        if self._manual_import_running:
            return
        selected = self._selected_digest_inboxes()
        if not selected:
            QMessageBox.information(
                self,
                "Select a Digest Inbox",
                "Select one or more available Digest Inboxes first.",
            )
            return
        candidate = self._saved_processing_config(
            "digesting incoming folders"
        )
        if candidate is None:
            return
        try:
            master_root = Path(candidate["destination_root"]).expanduser().resolve()
            output_roots = [("master library", master_root)]
            output_roots.extend(
                (str(replica["name"]), Path(replica["root"]).expanduser())
                for replica in effective_replica_destinations(candidate)
                if replica.get("enabled", True) and replica.get("root")
            )
            available = []
            unavailable = []
            for inbox in selected:
                root = Path(str(inbox.get("root", ""))).expanduser()
                if not inbox.get("enabled", True) or not root.is_dir():
                    unavailable.append(str(inbox["name"]))
                    continue
                source_root = root.resolve()
                for label, output_root in output_roots:
                    if paths_overlap(source_root, output_root):
                        raise ValueError(
                            f"{inbox['name']} overlaps the {label}: {output_root}"
                        )
                available.append(inbox)
            if not available:
                raise ValueError(
                    "None of the selected Digest Inboxes is currently available."
                )
        except (OSError, ValueError) as exc:
            QMessageBox.critical(
                self, "Cannot digest incoming folders", str(exc)
            )
            return

        move_inboxes = [
            inbox for inbox in available if inbox.get("action") == "move"
        ]
        names = "\n".join(
            f"  {index}. {inbox['name']} ({'verified move' if inbox.get('action') == 'move' else 'copy'})"
            for index, inbox in enumerate(available, 1)
        )
        required_backups = self._required_replica_names(candidate)
        skipped_text = (
            f"\nUnavailable and skipped: {', '.join(unavailable)}"
            if unavailable
            else ""
        )
        summary = (
            f"Incoming folders ({len(available)}):\n{names}\n\n"
            f"Master library: {master_root}\n"
            f"Copy verification: {candidate['safety']['copy_verification'].upper()}\n"
            f"Move verification: {candidate['safety']['move_checksum_algorithm'].upper()}\n"
            f"Required backups: {', '.join(required_backups) if required_backups else 'None'}\n"
            "Organization: current JPEG, RAW, video, and sidecar rules\n"
            "Retained history: unchanged source files are skipped"
            f"{skipped_text}\n\n"
            "Scan and digest these incoming folders?"
        )
        if not is_yes(
            QMessageBox.question(
                self,
                "Review Digest Inbox queue",
                summary,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        ):
            return
        if move_inboxes and not is_yes(
            QMessageBox.warning(
                self,
                "Additional confirmation for verified move",
                "Move source files from: "
                + ", ".join(str(inbox["name"]) for inbox in move_inboxes)
                + "?\n\n"
                "Each file is removed only after its primary copy, every required "
                "backup, checksum verification, and required transfer records succeed. "
                "Any failure leaves that source file in place.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        ):
            return

        candidate["digest_inboxes"] = copy.deepcopy(self.digest_inboxes)
        try:
            save_config(candidate, self.config_path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(
                self, "Could not save Digest Inbox settings", str(exc)
            )
            return
        self.config = candidate
        self.monitor.update_config(candidate)
        self._manual_import_running = True
        self._update_card_action_state()
        self._update_digest_action_state()
        self.organize_library_button.setEnabled(False)
        self.existing_review_button.setEnabled(False)
        self.sync_travel_button.setEnabled(False)
        self.run_hub_button.setEnabled(False)
        self.scan_export_library_button.setEnabled(False)
        self.export_selected_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.status_label.setText(
            f"Preparing {len(available)} Digest Inbox"
            + ("" if len(available) == 1 else "es")
        )
        self.progress_label.setText("Preparing digest queue")
        self.transfer_progress.setRange(0, 0)
        was_paused = self.monitor.is_paused

        def work() -> None:
            if not was_paused:
                self.monitor.set_paused(True)
            results: list[DigestRunResult] = []
            failures: list[str] = []
            try:
                def digest_batch() -> None:
                    for index, inbox in enumerate(available, 1):
                        if self._quitting:
                            break

                        def send(
                            event: ActivityEvent,
                            current_index: int = index,
                            current_inbox: dict = inbox,
                        ) -> None:
                            if event.level == "progress":
                                details = dict(event.details)
                                details.update(
                                    {
                                        "card_index": current_index,
                                        "card_total": len(available),
                                        "card_name": str(current_inbox["name"]),
                                    }
                                )
                                event = ActivityEvent(
                                    event.level,
                                    f"Digest {current_index} of {len(available)} - {event.message}",
                                    event.timestamp,
                                    details,
                                )
                            self.events.put(event)

                        try:
                            results.append(
                                run_digest_profile(
                                    candidate,
                                    inbox,
                                    allow_destructive=(
                                        inbox.get("action") == "move"
                                    ),
                                    event_callback=send,
                                    decision_callback=self._request_decision,
                                )
                            )
                        except Exception as exc:
                            message = f"{inbox['name']}: {exc}"
                            failures.append(message)
                            self.events.put(ActivityEvent("error", message))

                self.monitor.run_exclusive(digest_batch)
            finally:
                if not was_paused:
                    self.monitor.set_paused(False)
                self.ui_actions.put(
                    lambda: self._digest_finished(
                        results, failures, unavailable
                    )
                )

        threading.Thread(
            target=work,
            name="manual-digest-inboxes",
            daemon=True,
        ).start()

    def _digest_finished(
        self,
        results: list[DigestRunResult],
        failures: list[str],
        unavailable: list[str],
    ) -> None:
        self._import_finished()
        imported = sum(result.stats.imported for result in results)
        skipped = sum(result.stats.skipped for result in results)
        issues = sum(
            result.stats.failed + result.stats.blocked for result in results
        ) + len(failures)
        self.status_label.setText("Digest queue complete")
        self.progress_label.setText("Digest complete")
        summary = (
            f"Imported: {imported}\n"
            f"Already handled: {skipped}\n"
            f"Issues: {issues}\n"
            f"Inboxes completed: {len(results)}"
        )
        if unavailable:
            summary += f"\nUnavailable and skipped: {', '.join(unavailable)}"
        if failures:
            shown_failures = failures[:5]
            summary += "\n\n" + "\n".join(shown_failures)
            if len(failures) > len(shown_failures):
                summary += (
                    f"\n...and {len(failures) - len(shown_failures)} more. "
                    "All failures are retained in Activity."
                )
        if issues:
            QMessageBox.warning(
                self,
                "Digest completed with issues",
                summary,
            )
        else:
            QMessageBox.information(
                self,
                "Digest complete",
                summary,
            )

    def _selected_travel_libraries(self) -> list[dict]:
        selection = self.travel_table.selectionModel()
        if selection is None:
            return []
        selected_ids = []
        for index in selection.selectedRows(0):
            item = self.travel_table.item(index.row(), 0)
            if item is not None:
                selected_ids.append(
                    str(item.data(Qt.ItemDataRole.UserRole) or "")
                )
        return [
            library
            for library in self.travel_libraries
            if library.get("id") in selected_ids
        ]

    def _refresh_travel_libraries(self) -> None:
        if not hasattr(self, "travel_table"):
            return
        selected_ids = {
            library["id"] for library in self._selected_travel_libraries()
        }
        self.travel_table.setRowCount(len(self.travel_libraries))
        try:
            manifest = ImportManifest(
                Path(self.config["destination_root"]), create=False
            )
        except (OSError, ValueError):
            manifest = None
        selected_rows = []
        for row, library in enumerate(self.travel_libraries):
            root_text = str(library.get("root", ""))
            available = directory_available(root_text)
            enabled = bool(library.get("enabled", True))
            if not enabled:
                connection = "Disabled"
            elif available:
                connection = "Available"
            else:
                connection = "Offline"
            status = (
                manifest.source_status(f"folder-travel-{library['id']}")
                if manifest is not None
                else {"imported_files": 0, "last_imported_at": ""}
            )
            last_sync = str(status.get("last_imported_at", ""))
            if last_sync:
                try:
                    last_sync = datetime.fromisoformat(last_sync).astimezone().strftime(
                        "%Y-%m-%d %H:%M"
                    )
                except ValueError:
                    pass
            values = (
                str(library["name"]),
                root_text or "Path not set",
                connection,
                str(status.get("imported_files", 0)),
                last_sync or "Never",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, library["id"])
                self.travel_table.setItem(row, column, item)
            if library["id"] in selected_ids:
                selected_rows.append(row)
        for row in selected_rows:
            self.travel_table.selectRow(row)
        self._update_travel_action_state()

    def _update_travel_action_state(self) -> None:
        if not hasattr(self, "travel_table"):
            return
        selected = self._selected_travel_libraries()
        one = len(selected) == 1
        available = any(
            library.get("enabled", True)
            and library.get("root")
            and directory_available(str(library["root"]))
            for library in selected
        )
        self.edit_travel_button.setEnabled(one)
        self.open_travel_button.setEnabled(
            one
            and bool(selected[0].get("root"))
            and directory_available(str(selected[0]["root"]))
        )
        self.remove_travel_button.setEnabled(bool(selected))
        self.sync_travel_button.setEnabled(
            available and not self._manual_import_running
        )

    def _persist_travel_libraries(self) -> bool:
        candidate = copy.deepcopy(self.config)
        candidate["travel_libraries"] = copy.deepcopy(self.travel_libraries)
        try:
            candidate = normalize_config(candidate)
            save_config(candidate, self.config_path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(
                self, "Could not save travel libraries", str(exc)
            )
            return False
        self.config = candidate
        self.travel_libraries = copy.deepcopy(candidate["travel_libraries"])
        self.monitor.update_config(candidate)
        self._refresh_travel_libraries()
        self._update_settings_dirty_state()
        return True

    def _add_travel_library(self) -> None:
        primary = Path(
            self.safety_destination_edit.text().strip()
            or self.config["destination_root"]
        )
        dialog = TravelLibraryDialog(self, primary)
        if (
            dialog.exec() != QDialog.DialogCode.Accepted
            or not dialog.result_library
        ):
            return
        candidate = dict(dialog.result_library)
        existing_ids = {
            str(library["id"]) for library in self.travel_libraries
        }
        base_id = str(candidate["id"])
        suffix = 2
        while candidate["id"] in existing_ids:
            candidate["id"] = f"{base_id}-{suffix}"
            suffix += 1
        candidate_root = path_key(candidate["root"]) if candidate["root"] else ""
        if candidate_root and any(
            library.get("root")
            and path_key(library["root"]) == candidate_root
            for library in self.travel_libraries
        ):
            QMessageBox.critical(
                self,
                "Duplicate travel source",
                "That laptop library path is already retained.",
            )
            return
        self.travel_libraries.append(candidate)
        if self._persist_travel_libraries():
            for row in range(self.travel_table.rowCount()):
                item = self.travel_table.item(row, 0)
                if (
                    item is not None
                    and item.data(Qt.ItemDataRole.UserRole) == candidate["id"]
                ):
                    self.travel_table.selectRow(row)
                    break

    def _edit_travel_library(self) -> None:
        selected = self._selected_travel_libraries()
        if len(selected) != 1:
            return
        original = selected[0]
        primary = Path(
            self.safety_destination_edit.text().strip()
            or self.config["destination_root"]
        )
        dialog = TravelLibraryDialog(self, primary, original)
        if (
            dialog.exec() != QDialog.DialogCode.Accepted
            or not dialog.result_library
        ):
            return
        candidate = dialog.result_library
        candidate_root = path_key(candidate["root"]) if candidate["root"] else ""
        if candidate_root and any(
            library["id"] != original["id"]
            and library.get("root")
            and path_key(library["root"]) == candidate_root
            for library in self.travel_libraries
        ):
            QMessageBox.critical(
                self,
                "Duplicate travel source",
                "That laptop library path is already retained.",
            )
            return
        self.travel_libraries = [
            candidate if library["id"] == original["id"] else library
            for library in self.travel_libraries
        ]
        self._persist_travel_libraries()

    def _remove_travel_library(self) -> None:
        selected = self._selected_travel_libraries()
        if not selected:
            return
        names = ", ".join(str(library["name"]) for library in selected)
        if not is_yes(
            QMessageBox.question(
                self,
                "Forget travel source",
                f"Forget {names}?\n\n"
                "This removes only the retained connection profile. Laptop media, "
                "desktop media, and sync history remain unchanged.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        ):
            return
        selected_ids = {str(library["id"]) for library in selected}
        self.travel_libraries = [
            library
            for library in self.travel_libraries
            if library["id"] not in selected_ids
        ]
        self._persist_travel_libraries()

    def _open_travel_library(self) -> None:
        selected = self._selected_travel_libraries()
        if len(selected) != 1:
            return
        try:
            open_local_path(Path(str(selected[0]["root"])).expanduser())
        except OSError as exc:
            QMessageBox.critical(
                self, "Could not open travel library", str(exc)
            )

    def _sync_selected_travel_libraries(self) -> None:
        if self._manual_import_running:
            return
        selected = self._selected_travel_libraries()
        if not selected:
            QMessageBox.information(
                self,
                "Select a travel library",
                "Select one or more available laptop libraries first.",
            )
            return
        candidate = self._saved_processing_config(
            "synchronizing travel libraries"
        )
        if candidate is None:
            return
        try:
            master_root = Path(candidate["destination_root"]).expanduser().resolve()
            cards = []
            unavailable = []
            for library in selected:
                root = Path(str(library.get("root", ""))).expanduser()
                if not library.get("enabled", True) or not root.is_dir():
                    unavailable.append(str(library["name"]))
                    continue
                source_root = root.resolve()
                if paths_overlap(source_root, master_root):
                    raise ValueError(
                        f"{library['name']} overlaps the desktop master library."
                    )
                cards.append(
                    folder_import_source(
                        source_root,
                        name=f"Travel: {library['name']}",
                        stable_id=f"travel-{library['id']}",
                        action="copy",
                        include_subfolders=bool(
                            library.get("include_subfolders", True)
                        ),
                        destination_prefix=str(
                            library.get("destination_prefix", "")
                        ),
                        camera_name=str(library.get("camera_name", "")),
                    )
                )
            if not cards:
                raise ValueError(
                    "None of the selected travel libraries is currently available."
                )
        except (OSError, ValueError) as exc:
            QMessageBox.critical(
                self, "Cannot sync travel libraries", str(exc)
            )
            return

        required_backups = self._required_replica_names(candidate)
        skipped_text = (
            f"\nUnavailable and skipped: {', '.join(unavailable)}"
            if unavailable
            else ""
        )
        summary = (
            f"Travel sources: {', '.join(card.name.removeprefix('Travel: ') for card in cards)}\n"
            f"Desktop master: {master_root}\n"
            "Source handling: Copy only; laptop files and deletions are untouched\n"
            f"Verification: {candidate['safety']['copy_verification'].upper()}\n"
            f"Required backups: {', '.join(required_backups) if required_backups else 'None'}\n"
            "Previously completed source files: skipped from retained sync history"
            f"{skipped_text}\n\n"
            "Scan these sources and bring new media home?"
        )
        if not is_yes(
            QMessageBox.question(
                self,
                "Confirm travel sync",
                summary,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        ):
            return
        candidate["travel_libraries"] = copy.deepcopy(self.travel_libraries)
        try:
            save_config(candidate, self.config_path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(
                self, "Could not save travel sync settings", str(exc)
            )
            return
        self.config = candidate
        self.monitor.update_config(candidate)
        self._start_card_batch(cards, confirmation_complete=True)

    def _selected_transfer_hubs(self) -> list[dict]:
        selection = self.hub_table.selectionModel()
        if selection is None:
            return []
        selected_ids = []
        for index in selection.selectedRows(0):
            item = self.hub_table.item(index.row(), 0)
            if item is not None:
                selected_ids.append(
                    str(item.data(Qt.ItemDataRole.UserRole) or "")
                )
        return [
            hub for hub in self.transfer_hubs if hub.get("id") in selected_ids
        ]

    def _refresh_transfer_hubs(self) -> None:
        if not hasattr(self, "hub_table"):
            return
        selected_ids = {
            str(hub["id"]) for hub in self._selected_transfer_hubs()
        }
        self.hub_table.setRowCount(len(self.transfer_hubs))
        selected_rows = []
        for row, hub in enumerate(self.transfer_hubs):
            root_text = str(hub.get("root", ""))
            available = directory_available(root_text)
            enabled = bool(hub.get("enabled", True))
            role = str(hub.get("role", "catch"))
            if not enabled:
                state = "Disabled"
            elif not available:
                state = "Offline"
            elif role == "catch" and hub.get("auto_catch", False):
                state = "Monitoring"
            else:
                state = "Ready"
            if role == "publish":
                channel = producer_channel(self.config, hub)
                receipt_count, last_receipt = receipt_status(self.config, hub)
                confirmations = (
                    f"{receipt_count} / {last_receipt}"
                    if receipt_count
                    else "None"
                )
                role_label = "Publish"
            else:
                channel = "All producers"
                confirmations = (
                    "Writes receipts"
                    if hub.get("write_receipts", True)
                    else "Receipts off"
                )
                role_label = "Catch"
            values = (
                str(hub["name"]),
                role_label,
                root_text or "Path not set",
                channel,
                state,
                confirmations,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, hub["id"])
                self.hub_table.setItem(row, column, item)
            if hub["id"] in selected_ids:
                selected_rows.append(row)
        for row in selected_rows:
            self.hub_table.selectRow(row)
        self._update_hub_action_state()

    def _update_hub_action_state(self) -> None:
        if not hasattr(self, "hub_table"):
            return
        selected = self._selected_transfer_hubs()
        one = len(selected) == 1
        runnable = any(
            hub.get("enabled", True)
            and hub.get("root")
            and directory_available(str(hub["root"]))
            for hub in selected
        )
        self.edit_hub_button.setEnabled(one)
        self.remove_hub_button.setEnabled(bool(selected))
        self.run_hub_button.setEnabled(
            runnable and not self._manual_import_running
        )
        roles = {str(hub.get("role", "catch")) for hub in selected}
        if roles == {"publish"}:
            self.run_hub_button.setText("Publish now")
        elif roles == {"catch"}:
            self.run_hub_button.setText("Catch new sessions")
        else:
            self.run_hub_button.setText("Run selected hubs")

    def _persist_transfer_hubs(self) -> bool:
        candidate = copy.deepcopy(self.config)
        candidate["transfer_hubs"] = copy.deepcopy(self.transfer_hubs)
        try:
            candidate = normalize_config(candidate)
            save_config(candidate, self.config_path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not save transfer hubs", str(exc))
            return False
        self.config = candidate
        self.transfer_hubs = copy.deepcopy(candidate["transfer_hubs"])
        self.monitor.update_config(candidate)
        self._refresh_transfer_hubs()
        self._update_settings_dirty_state()
        return True

    def _add_transfer_hub(self) -> None:
        primary = Path(
            self.safety_destination_edit.text().strip()
            or self.config["destination_root"]
        )
        dialog = TransferHubDialog(self, primary)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_hub:
            return
        candidate = dict(dialog.result_hub)
        existing_ids = {str(hub["id"]) for hub in self.transfer_hubs}
        base_id = str(candidate["id"])
        suffix = 2
        while candidate["id"] in existing_ids:
            candidate["id"] = f"{base_id}-{suffix}"
            suffix += 1
        candidate_root = path_key(candidate["root"]) if candidate["root"] else ""
        if candidate_root and any(
            hub.get("root") and path_key(hub["root"]) == candidate_root
            for hub in self.transfer_hubs
        ):
            QMessageBox.critical(
                self,
                "Hub already configured",
                "That shared hub folder already has a role on this client. Edit the existing profile instead.",
            )
            return
        self.transfer_hubs.append(candidate)
        if self._persist_transfer_hubs():
            for row in range(self.hub_table.rowCount()):
                item = self.hub_table.item(row, 0)
                if (
                    item is not None
                    and item.data(Qt.ItemDataRole.UserRole) == candidate["id"]
                ):
                    self.hub_table.selectRow(row)
                    break

    def _edit_transfer_hub(self) -> None:
        selected = self._selected_transfer_hubs()
        if len(selected) != 1:
            return
        original = selected[0]
        primary = Path(
            self.safety_destination_edit.text().strip()
            or self.config["destination_root"]
        )
        dialog = TransferHubDialog(self, primary, original)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_hub:
            return
        candidate = dialog.result_hub
        candidate_root = path_key(candidate["root"]) if candidate["root"] else ""
        if candidate_root and any(
            hub["id"] != original["id"]
            and hub.get("root")
            and path_key(hub["root"]) == candidate_root
            for hub in self.transfer_hubs
        ):
            QMessageBox.critical(
                self,
                "Hub already configured",
                "That shared hub folder already has another role on this client.",
            )
            return
        self.transfer_hubs = [
            candidate if hub["id"] == original["id"] else hub
            for hub in self.transfer_hubs
        ]
        self._persist_transfer_hubs()

    def _remove_transfer_hubs(self) -> None:
        selected = self._selected_transfer_hubs()
        if not selected:
            return
        names = ", ".join(str(hub["name"]) for hub in selected)
        if not is_yes(
            QMessageBox.question(
                self,
                "Forget shared transfer hub",
                f"Forget {names} on this client?\n\n"
                "Shared producer files, receipts, local media, and local import history are unchanged.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        ):
            return
        selected_ids = {str(hub["id"]) for hub in selected}
        self.transfer_hubs = [
            hub for hub in self.transfer_hubs if hub["id"] not in selected_ids
        ]
        self._persist_transfer_hubs()

    def _run_selected_hubs(self) -> None:
        if self._manual_import_running:
            return
        selected = [
            hub
            for hub in self._selected_transfer_hubs()
            if hub.get("enabled", True)
            and hub.get("root")
            and directory_available(str(hub["root"]))
        ]
        if not selected:
            QMessageBox.information(
                self,
                "No available hubs",
                "Select at least one enabled hub whose shared folder is available.",
            )
            return
        publishing = [hub for hub in selected if hub.get("role") == "publish"]
        catching = [hub for hub in selected if hub.get("role") == "catch"]
        if publishing and not self._publish_to_hubs(publishing):
            return
        if catching:
            self._catch_from_hubs(catching)

    def _publish_to_hubs(self, hubs: list[dict]) -> bool:
        candidate = self._saved_processing_config(
            "publishing to transfer hubs"
        )
        if candidate is None:
            return False
        destinations = "\n".join(
            f"- {hub['name']}: {Path(hub['root']) / 'Producers' / producer_channel(candidate, hub)}"
            for hub in hubs
        )
        if not is_yes(
            QMessageBox.question(
                self,
                "Confirm hub publication",
                f"Local library: {candidate['destination_root']}\n"
                f"Producer destinations:\n{destinations}\n\n"
                "Operation: Copy only with SHA-256 verification\n"
                "Matching hub files: reused\n"
                "Different-content hub paths: blocked or archived according to each hub\n"
                "Source files and remote receipts: never deleted\n\n"
                "Publish this library now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        ):
            return False

        completed = threading.Event()
        cancelled = threading.Event()
        result: dict[str, object] = {"stats": []}
        progress_state = {
            "hub_index": 0,
            "hub_total": len(hubs),
            "hub_name": "",
            "current": 0,
            "total": 0,
            "name": "",
        }

        def work() -> None:
            try:
                for hub_index, hub in enumerate(hubs, start=1):
                    if cancelled.is_set():
                        break
                    progress_state.update(
                        hub_index=hub_index,
                        hub_name=str(hub["name"]),
                        current=0,
                        total=0,
                        name="",
                    )

                    def update(current: int, total: int, name: str) -> None:
                        progress_state.update(
                            current=current, total=total, name=name
                        )

                    stats = publish_library(
                        candidate,
                        hub,
                        progress_callback=update,
                        event_callback=self.events.put,
                        cancel_event=cancelled,
                    )
                    result["stats"].append((hub, stats))
            except Exception as exc:
                result["error"] = exc
            finally:
                completed.set()

        threading.Thread(
            target=work, name="hub-library-publication", daemon=True
        ).start()
        progress = QProgressDialog(
            "Preparing hub publication...",
            "Cancel",
            0,
            0,
            self,
        )
        progress.setWindowTitle("Publish to transfer hubs")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()
        while not completed.wait(0.04):
            total = int(progress_state["total"])
            current = int(progress_state["current"])
            if total:
                progress.setRange(0, total)
                progress.setValue(current)
            else:
                progress.setRange(0, 0)
            progress.setLabelText(
                f"Hub {progress_state['hub_index']} of {progress_state['hub_total']} - "
                f"{progress_state['hub_name']}\n"
                f"{current} of {total}: {progress_state['name']}"
            )
            QApplication.processEvents()
            if progress.wasCanceled():
                cancelled.set()
                progress.setLabelText(
                    "Stopping after the current verified file..."
                )
        progress.close()
        QApplication.processEvents()
        if result.get("error") is not None:
            QMessageBox.critical(
                self, "Hub publication failed", str(result["error"])
            )
            return False
        stats_items = list(result.get("stats", []))
        published = sum(stats.published for _hub, stats in stats_items)
        reused = sum(stats.reused for _hub, stats in stats_items)
        failed = sum(stats.failed for _hub, stats in stats_items)
        message = (
            f"Published and verified: {published}\n"
            f"Matching hub files reused: {reused}\n"
            f"Failed: {failed}"
        )
        if failed or cancelled.is_set():
            QMessageBox.warning(
                self, "Hub publication completed with issues", message
            )
        else:
            QMessageBox.information(self, "Hub publication complete", message)
        self.events.put(
            ActivityEvent(
                "warning" if failed else "success",
                f"Hub publication complete: {published} published, {reused} reused, {failed} failed.",
            )
        )
        self._refresh_transfer_hubs()
        return not cancelled.is_set()

    def _catch_from_hubs(self, hubs: list[dict]) -> bool:
        candidate = self._saved_processing_config(
            "catching transfer-hub sessions"
        )
        if candidate is None:
            return False
        try:
            cards = []
            receipt_hubs: dict[str, dict] = {}
            errors = []
            for hub in hubs:
                hub_cards, hub_errors = catch_sources(candidate, hub)
                errors.extend(hub_errors)
                for card in hub_cards:
                    cards.append(card)
                    receipt_hubs[card.card_id] = hub
            if not cards:
                raise ValueError(
                    "No producer channels with supported media are currently available."
                )
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Cannot catch hub sessions", str(exc))
            return False
        warning_text = (
            "\n\nUnavailable channels:\n" + "\n".join(errors)
            if errors
            else ""
        )
        if not is_yes(
            QMessageBox.question(
                self,
                "Confirm hub catch",
                f"Producer channels: {len(cards)}\n"
                f"Destination library: {candidate['destination_root']}\n"
                "Operation: Copy only; hub files and remote deletions are ignored\n"
                "Previously digested files: skipped by this client's local manifest\n"
                "Receipts: written only after local ingestion is confirmed"
                f"{warning_text}\n\n"
                "Catch new committed media now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        ):
            return False
        return self._start_card_batch(
            cards,
            confirmation_complete=True,
            hub_receipts=receipt_hubs,
        )

    def _scan_export_library(self) -> None:
        if self._manual_import_running:
            return
        candidate = self._saved_processing_config(
            "scanning the master library"
        )
        if candidate is None:
            return
        try:
            select_library_destination(candidate, str(self.export_library_combo.currentData() or ""))
            library_root = Path(candidate["destination_root"]).expanduser().resolve()
            if not library_root.is_dir():
                raise ValueError("The desktop master library does not exist.")
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Cannot scan master library", str(exc))
            return

        result: dict[str, object] = {}
        completed = threading.Event()
        cancelled = threading.Event()
        progress_state = {"current": 0, "total": 0, "name": ""}

        def update_progress(current: int, total: int, name: str) -> None:
            progress_state.update(current=current, total=total, name=name)

        def work() -> None:
            try:
                result["items"] = scan_library(
                    library_root,
                    candidate["media_rules"],
                    progress_callback=update_progress,
                    cancel_event=cancelled,
                )
            except Exception as exc:
                result["error"] = exc
            finally:
                completed.set()

        threading.Thread(
            target=work, name="master-library-catalog", daemon=True
        ).start()
        progress = QProgressDialog(
            "Finding supported media...",
            "Cancel",
            0,
            0,
            self,
        )
        progress.setWindowTitle("Scan master library")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()
        while not completed.wait(0.04):
            current = int(progress_state["current"])
            total = int(progress_state["total"])
            if total:
                progress.setRange(0, total)
                progress.setValue(current)
                progress.setLabelText(
                    f"Reading metadata {current} of {total}: "
                    f"{progress_state['name']}"
                )
            QApplication.processEvents()
            if progress.wasCanceled():
                cancelled.set()
                progress.setLabelText("Cancelling library scan...")
        progress.close()
        QApplication.processEvents()

        error = result.get("error")
        if isinstance(error, InterruptedError):
            self.export_status_label.setText("Master-library scan cancelled")
            return
        if error is not None:
            QMessageBox.critical(
                self, "Could not scan master library", str(error)
            )
            return
        items = result.get("items")
        if not isinstance(items, list):
            QMessageBox.critical(
                self, "Could not scan master library", "No catalog was returned."
            )
            return
        self.library_captures = build_capture_sets(items)
        self._export_scanned_root = library_root
        self._rebuild_export_groups()
        self.export_library_label.setText(str(library_root))
        self._refresh_export_table()

    def _rebuild_export_groups(self) -> None:
        if not hasattr(self, "export_table"):
            return
        self.library_groups = detect_capture_groups(
            self.library_captures,
            bracket_seconds=self.bracket_seconds_spin.value(),
            interval_max_seconds=self.interval_seconds_spin.value(),
        )
        if self.library_captures:
            self._refresh_export_table()

    def _export_library_changed(self) -> None:
        if not hasattr(self, "export_model"):
            return
        selected = library_destination(self.config, str(self.export_library_combo.currentData() or ""))
        root = Path(selected["root"]).expanduser().resolve() if selected else None
        self.export_library_label.setText(str(root) if root else "No library selected")
        if self._export_scanned_root != root:
            self.library_captures = []
            self.library_groups = []
            self._export_scanned_root = None
            self._refresh_export_table()

    def _export_row_values(self, capture):
        group = self._export_group_by_capture.get(capture.capture_id)
        group_text = "Single"
        if group is not None:
            label = "Bracket / burst" if group.kind == "bracket" else "Interval"
            group_text = f"{label} ({len(group.capture_ids)})"
        return (
            capture.captured_at.strftime("%Y-%m-%d %H:%M:%S"), group_text,
            capture.media_label, str(capture.rating) if capture.rating is not None else "-",
            capture.camera or "Unknown", capture.primary.relative_path.as_posix(),
        )

    def _refresh_export_table(self, *_args) -> None:
        if not hasattr(self, "export_table"):
            return
        selected_ids = set(self._selected_export_capture_ids())
        self._export_group_by_capture = {
            capture_id: group
            for group in self.library_groups
            for capture_id in group.capture_ids
        }
        error = ""
        try:
            self.filtered_export_captures = filter_captures(
                self.library_captures, media_kind=str(self.export_media_combo.currentData()),
                start=self.export_start_date.date().toPython() if self.export_date_check.isChecked() else None,
                end=self.export_end_date.date().toPython() if self.export_date_check.isChecked() else None,
                include_sidecars=self.export_sidecars_check.isChecked(),
            )
        except ValueError as exc:
            self.filtered_export_captures = []
            error = str(exc)
        self.export_model.replace_rows(self.filtered_export_captures)
        for row, capture in enumerate(self.filtered_export_captures):
            if capture.capture_id in selected_ids:
                self.export_table.selectionModel().select(
                    self.export_model.index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                )
        bracket_count = sum(
            1 for group in self.library_groups if group.kind == "bracket"
        )
        interval_count = sum(
            1 for group in self.library_groups if group.kind == "interval"
        )
        file_count = sum(
            len(capture.items) for capture in self.filtered_export_captures
        )
        self.export_status_label.setText(
            error or f"{len(self.filtered_export_captures)} matching captures / {file_count} files | "
            f"{bracket_count} bracket or burst groups | "
            f"{interval_count} interval groups"
        )
        self._update_export_action_state()

    def _selected_export_capture_ids(self) -> list[str]:
        if not hasattr(self, "export_table"):
            return []
        selection = self.export_table.selectionModel()
        if selection is None:
            return []
        result = []
        for index in selection.selectedRows(0):
            result.append(str(index.data(Qt.ItemDataRole.UserRole) or ""))
        return result

    def _update_export_action_state(self, *_args) -> None:
        if hasattr(self, "export_selected_button"):
            self.export_selected_button.setEnabled(
                bool(self._selected_export_capture_ids())
                and not self._manual_import_running
            )

    def _select_export_groups(self) -> None:
        selected_ids = set(self._selected_export_capture_ids())
        if not selected_ids:
            return
        for group in self.library_groups:
            if selected_ids.intersection(group.capture_ids):
                selected_ids.update(group.capture_ids)
        selection = self.export_table.selectionModel()
        if selection is None:
            return
        selection.clearSelection()
        for row in range(self.export_model.rowCount()):
            item = self.export_model.index(row, 0)
            if (
                item is not None
                and item.data(Qt.ItemDataRole.UserRole) in selected_ids
            ):
                selection.select(
                    self.export_table.model().index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )

    def _export_selected_captures(self) -> None:
        if self._manual_import_running:
            return
        selected_ids = set(self._selected_export_capture_ids())
        if not selected_ids:
            return
        if self._saved_processing_config(
            "exporting captures for editing"
        ) is None:
            return
        if self.export_full_groups_check.isChecked():
            for group in self.library_groups:
                if selected_ids.intersection(group.capture_ids):
                    selected_ids.update(group.capture_ids)
        captures = [
            capture
            for capture in self.filtered_export_captures
            if capture.capture_id in selected_ids
        ]
        selected_groups = [
            group
            for group in self.library_groups
            if selected_ids.intersection(group.capture_ids)
        ]
        destination_text = QFileDialog.getExistingDirectory(
            self, "Choose an editing export folder"
        )
        if not destination_text:
            return
        destination = Path(destination_text).expanduser().resolve()
        library_root = self._export_scanned_root
        if library_root is None:
            return
        if paths_overlap(destination, library_root):
            QMessageBox.critical(
                self,
                "Choose a separate export folder",
                "The editing export folder cannot contain or be inside the managed master library.",
            )
            return
        group_subfolders = self.export_group_folders_check.isChecked()
        conflict_appendage = str(
            self.config["safety"].get(
                "conflict_filename_appendage", "_{number}"
            )
        )
        minimum_free_percent = float(
            self.config["safety"].get(
                "minimum_destination_free_percent", 0
            )
        )
        minimum_free_gb = float(
            self.config["safety"].get(
                "minimum_destination_free_gb", 0
            )
        )
        reserve_parts = []
        if minimum_free_percent > 0:
            reserve_parts.append(f"{minimum_free_percent:g}%")
        if minimum_free_gb > 0:
            reserve_parts.append(f"{minimum_free_gb:g} GB")
        reserve_summary = (
            " and ".join(reserve_parts)
            if reserve_parts
            else "No additional reserve"
        )
        file_count = sum(len(capture.items) for capture in captures)
        group_count = len(
            {
                group.group_id
                for group in selected_groups
                if selected_ids.intersection(group.capture_ids)
            }
        )
        if not is_yes(
            QMessageBox.question(
                self,
                "Confirm editing export",
                f"Captures: {len(captures)}\n"
                f"JPEG, RAW, video, and sidecar files: {file_count}\n"
                f"Detected groups represented: {group_count}\n"
                f"Destination: {destination}\n"
                "Operation: Copy only with SHA-256 verification\n"
                f"Group subfolders: {'On' if group_subfolders else 'Off'}\n"
                f"Filename conflict suffix: {conflict_appendage}\n"
                f"Destination free-space reserve: {reserve_summary}\n"
                "Existing different-content filenames: both files are preserved\n\n"
                "Start this editing export?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        ):
            return

        result: dict[str, object] = {}
        completed = threading.Event()
        cancelled = threading.Event()
        progress_state = {"current": 0, "total": file_count, "name": ""}

        def update_progress(current: int, total: int, name: str) -> None:
            progress_state.update(current=current, total=total, name=name)

        def work() -> None:
            try:
                result["stats"] = export_captures(
                    captures,
                    destination,
                    groups=selected_groups,
                    group_subfolders=group_subfolders,
                    verification="sha256",
                    conflict_appendage=conflict_appendage,
                    minimum_free_percent=minimum_free_percent,
                    minimum_free_gb=minimum_free_gb,
                    progress_callback=update_progress,
                    cancel_event=cancelled,
                )
            except Exception as exc:
                result["error"] = exc
            finally:
                completed.set()

        threading.Thread(
            target=work, name="editing-export", daemon=True
        ).start()
        progress = QProgressDialog(
            "Preparing editing export...",
            "Cancel",
            0,
            max(1, file_count),
            self,
        )
        progress.setWindowTitle("Export for editing")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()
        while not completed.wait(0.04):
            progress.setMaximum(max(1, int(progress_state["total"])))
            progress.setValue(int(progress_state["current"]))
            progress.setLabelText(
                f"Copying {progress_state['current']} of "
                f"{progress_state['total']}: {progress_state['name']}"
            )
            QApplication.processEvents()
            if progress.wasCanceled():
                cancelled.set()
                progress.setLabelText(
                    "Stopping after the current verified file..."
                )
        progress.close()
        QApplication.processEvents()

        error = result.get("error")
        if error is not None:
            QMessageBox.critical(self, "Editing export failed", str(error))
            return
        stats = result.get("stats")
        if stats is None:
            QMessageBox.critical(
                self, "Editing export failed", "No export result was returned."
            )
            return
        message = (
            f"Copied and verified: {stats.files_copied}\n"
            f"Matching files already present: {stats.files_reused}\n"
            f"Export record: {stats.log_path}"
        )
        if stats.errors:
            shown_errors = stats.errors[:8]
            error_text = "\n".join(shown_errors)
            if len(stats.errors) > len(shown_errors):
                error_text += (
                    f"\n...and {len(stats.errors) - len(shown_errors)} more. "
                    "The export record retains the complete result."
                )
            QMessageBox.warning(
                self,
                "Editing export completed with issues",
                f"{message}\n\n{error_text}",
            )
        else:
            QMessageBox.information(
                self, "Editing export complete", message
            )
        self.events.put(
            ActivityEvent(
                "warning" if stats.errors else "success",
                f"Editing export completed: {stats.files_copied} copied, "
                f"{stats.files_reused} reused, {len(stats.errors)} issues",
            )
        )

    @staticmethod
    def _selected_ids_from(table: QTableWidget) -> list[str]:
        selection = table.selectionModel()
        if selection is None:
            return []
        values = []
        for index in selection.selectedRows(0):
            item = table.item(index.row(), 0)
            source_id = str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""
            if source_id and source_id not in values:
                values.append(source_id)
        return values

    def _selected_source_ids(self) -> list[str]:
        preferred = self.cards_table if self.stack.currentIndex() == self.page_indexes["Cards and drives"] else self.dashboard_table
        alternate = self.dashboard_table if preferred is self.cards_table else self.cards_table
        selected = self._selected_ids_from(preferred)
        return selected or self._selected_ids_from(alternate)

    def _selected_source_id(self) -> str:
        selected = self._selected_source_ids()
        if len(selected) > 1:
            QMessageBox.information(
                self,
                "Select one card",
                "This action changes one card profile at a time. Select a single card or drive.",
            )
            return ""
        return selected[0] if selected else ""

    def _selected_cards(self) -> list[CardMarker]:
        return [self.card_by_id[source_id] for source_id in self._selected_source_ids() if source_id in self.card_by_id]

    def _update_card_action_state(self) -> None:
        selected = self._selected_source_ids()
        connected = [source_id for source_id in selected if source_id in self.card_by_id]
        single = len(selected) == 1
        running = self._manual_import_running
        if connected and len(connected) != len(selected):
            noun = "card" if len(connected) == 1 else "cards"
            self.import_button.setText(f"Import {len(connected)} connected {noun}")
        elif len(connected) == 1:
            self.import_button.setText("Import selected card")
        elif len(connected) > 1:
            self.import_button.setText(f"Import {len(connected)} selected cards")
        else:
            self.import_button.setText("Import selected cards")
        self.import_button.setEnabled(bool(connected) and not running)
        self.edit_card_button.setEnabled(single and not running)
        self.open_identity_button.setEnabled(single and bool(connected) and not running)
        self.forget_profile_button.setEnabled(
            single and selected[0] not in self.card_by_id and not running
            if selected
            else False
        )

    @staticmethod
    def _select_table_ids(table: QTableWidget, source_ids: set[str]) -> None:
        table.clearSelection()
        selection = table.selectionModel()
        if selection is None:
            return
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item and str(item.data(Qt.ItemDataRole.UserRole) or "") in source_ids:
                selection.select(
                    table.model().index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )

    def refresh_cards(self) -> None:
        previous = set(self._selected_source_ids()) if hasattr(self, "dashboard_table") else set()
        cards, errors = discover_cards(self.config)
        self.card_by_id = {card.card_id: card for card in cards}
        profiles_changed = False
        for card in cards:
            existing = get_card_profile(self.config, card.card_id)
            candidate = profile_for_card(card)
            if existing is None:
                upsert_card_profile(self.config, candidate)
                profiles_changed = True
            elif existing.get("last_root") != str(card.root):
                updated = dict(existing)
                updated["last_root"] = str(card.root)
                upsert_card_profile(self.config, updated)
                profiles_changed = True
        if profiles_changed:
            try:
                save_config(self.config, self.config_path)
                if hasattr(self, "monitor"):
                    self.monitor.update_config(self.config)
            except (OSError, ValueError) as exc:
                self.events.put(ActivityEvent("error", f"Could not retain card profiles: {exc}"))
        self.profile_by_id = {
            profile["id"]: profile for profile in self.config.get("card_profiles", [])
        }

        display_rows: list[tuple[str, str, str, str, str, str]] = []
        for card in cards:
            try:
                capacity = capacity_for(card.root)
                free = f"{capacity.free_percent:.1f}%"
            except OSError:
                free = "Unknown"
            state = "Ready" if card.action == "copy" else "Move requires confirmation"
            display_rows.append(
                (card.card_id, card.name, card.action.capitalize(), free, str(card.root), state)
            )
        for profile in self.config.get("card_profiles", []):
            if profile["id"] in self.card_by_id:
                continue
            state = "Profile disabled" if not profile.get("enabled", True) else "Not connected"
            display_rows.append(
                (
                    profile["id"],
                    profile["name"],
                    str(profile["action"]).capitalize(),
                    "Offline",
                    str(profile.get("last_root", "")),
                    state,
                )
            )
        for table in (self.dashboard_table, self.cards_table):
            table.setSortingEnabled(False)
            table.setRowCount(len(display_rows))
            for row, values in enumerate(display_rows):
                source_id, *columns = values
                for column, value in enumerate(columns):
                    item = QTableWidgetItem(value)
                    if column == 0:
                        item.setData(Qt.ItemDataRole.UserRole, source_id)
                    if column == 4:
                        if value == "Ready":
                            item.setForeground(QColor(COLORS["success"]))
                        elif value == "Move requires confirmation":
                            item.setForeground(QColor(COLORS["warning"]))
                        else:
                            item.setForeground(QColor(COLORS["muted"]))
                    item.setToolTip(value)
                    table.setItem(row, column, item)
            table.setSortingEnabled(True)
            self._select_table_ids(table, previous)
        self.card_summary_label.setText(
            f"{len(cards)} connected  |  {len(self.profile_by_id)} retained"
        )
        try:
            destination_capacity = capacity_for(Path(self.config["destination_root"]))
            self.destination_capacity_label.setText(
                f"{destination_capacity.free_percent:.1f}% free  |  {format_bytes(destination_capacity.free_bytes)}"
            )
        except OSError:
            self.destination_capacity_label.setText("Unavailable")
        for error in errors:
            self.events.put(ActivityEvent("error", error))
        self._update_card_action_state()

    def _add_card(self) -> None:
        if self._manual_import_running:
            return
        try:
            candidate = self._collect_config()
        except ValueError as exc:
            QMessageBox.critical(self, "Cannot onboard card", str(exc))
            return
        was_paused = self.monitor.is_paused
        if not was_paused:
            self.monitor.set_paused(True)
        dialog = CardOnboardingWizard(self, candidate)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            if not was_paused:
                self.monitor.set_paused(False)
            return
        if not dialog.result_root or not dialog.result_card or not dialog.result_config:
            if not was_paused:
                self.monitor.set_paused(False)
            return
        self.config = dialog.result_config
        if dialog.result_profile:
            upsert_card_profile(self.config, dialog.result_profile)
        configured = self.config["identification"]["configured_roots"]
        if str(dialog.result_root) not in configured:
            configured.append(str(dialog.result_root))
        try:
            save_config(self.config, self.config_path)
        except (OSError, ValueError) as exc:
            if not was_paused:
                self.monitor.set_paused(False)
            QMessageBox.critical(self, "Could not save onboarding", str(exc))
            return
        self._load_config_into_controls()
        self.monitor.update_config(self.config)
        self.refresh_cards()
        source_ids = {dialog.result_card.card_id}
        for table in (self.cards_table, self.dashboard_table):
            self._select_table_ids(table, source_ids)
        started = self._start_card_batch(
            [dialog.result_card],
            resume_monitor_after=not was_paused,
            confirmation_complete=True,
        )
        if not started and not was_paused:
            self.monitor.set_paused(False)

    def _edit_card(self) -> None:
        source_id = self._selected_source_id()
        if not source_id:
            if not self._selected_source_ids():
                QMessageBox.information(self, "Select a card", "Select one card or retained profile first.")
            return
        card = self.card_by_id.get(source_id)
        profile = profile_for_card(card) if card else self.profile_by_id.get(source_id)
        if not profile:
            return
        dialog = CardProfileDialog(self, profile, connected=card is not None)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_profile:
            return
        candidate_profile = dialog.result_profile
        try:
            if card is not None:
                identification = self.config["identification"]
                write_card_identity(
                    card.root,
                    identification["folder_name"],
                    identification["identity_filename"],
                    identification["history_folder_name"],
                    candidate_profile["name"],
                    candidate_profile["id"],
                    candidate_profile["action"],
                    candidate_profile["source_folders"],
                    candidate_profile["destination_prefix"],
                    candidate_profile["enabled"],
                    candidate_profile["delete_empty_folders_after_move"],
                    candidate_profile.get("camera_name", ""),
                )
            upsert_card_profile(self.config, candidate_profile)
            save_config(self.config, self.config_path)
            self.monitor.update_config(self.config)
            self.refresh_cards()
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not save card profile", str(exc))

    def _add_profile(self) -> None:
        dialog = CardProfileDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_profile:
            return
        if dialog.result_profile["id"] in self.profile_by_id:
            QMessageBox.critical(
                self,
                "Card ID already retained",
                "Choose a unique stable card ID, or select the existing profile and use Edit.",
            )
            return
        try:
            profile = upsert_card_profile(self.config, dialog.result_profile)
            last_root = profile.get("last_root", "")
            if last_root and last_root not in self.config["identification"]["configured_roots"]:
                self.config["identification"]["configured_roots"].append(last_root)
            save_config(self.config, self.config_path)
            self.monitor.update_config(self.config)
            self.refresh_cards()
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not save offline profile", str(exc))

    def _forget_profile(self) -> None:
        source_id = self._selected_source_id()
        if not source_id:
            if not self._selected_source_ids():
                QMessageBox.information(self, "Select a profile", "Select one offline retained profile first.")
            return
        if source_id in self.card_by_id:
            QMessageBox.information(
                self,
                "Card is connected",
                "Disconnect this card before forgetting its retained profile.",
            )
            return
        profile = self.profile_by_id.get(source_id)
        if not profile or not is_yes(
            QMessageBox.question(
                self,
                "Forget retained profile",
                f"Forget the retained configuration for {profile['name']}?\n\n"
                "The card identity and all transfer records remain unchanged.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        ):
            return
        try:
            remove_card_profile(self.config, source_id)
            last_root = profile.get("last_root", "")
            self.config["identification"]["configured_roots"] = [
                root for root in self.config["identification"]["configured_roots"] if root != last_root
            ]
            save_config(self.config, self.config_path)
            self.monitor.update_config(self.config)
            self.refresh_cards()
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not forget profile", str(exc))

    def _open_identity(self) -> None:
        source_id = self._selected_source_id()
        if not source_id:
            if not self._selected_source_ids():
                QMessageBox.information(self, "Select a card", "Select one connected card first.")
            return
        card = self.card_by_id.get(source_id)
        if card is None:
            QMessageBox.information(self, "Card not connected", "Connect the card before opening its identity folder.")
            return
        try:
            open_local_path(card.identity_dir)
        except OSError as exc:
            QMessageBox.critical(self, "Could not open identity folder", str(exc))

    def _collect_config(self) -> dict:
        candidate = copy.deepcopy(self.config)
        candidate["library_destinations"] = copy.deepcopy(
            self.library_destinations
        )
        candidate["default_library_id"] = self.default_library_id
        select_library_destination(
            candidate,
            self.default_library_id,
        )
        identification = candidate["identification"]
        identification["folder_name"] = self.folder_name_edit.text().strip()
        identification["identity_filename"] = self.identity_filename_edit.text().strip()
        identification["history_folder_name"] = self.history_folder_edit.text().strip()
        identification["history_folder_segments"] = [
            combo_value(combo) for combo in self.history_segment_combos if combo_value(combo)
        ]
        identification["session_filename_template"] = self.session_filename_edit.text().strip()
        identification["checksum_filename_template"] = self.checksum_filename_edit.text().strip()
        identification["shared_history"] = self.shared_history_check.isChecked()
        identification["require_log_before_source_delete"] = self.require_portable_log_check.isChecked()
        local_history = candidate["local_history"]
        local_history["enabled"] = self.local_history_enabled_check.isChecked()
        local_history["directory"] = self.local_history_edit.text().strip()
        local_history["require_before_source_delete"] = self.require_local_log_check.isChecked()
        safety = candidate["safety"]
        safety["default_action"] = combo_value(self.default_action_combo)
        safety["copy_verification"] = combo_value(self.copy_verification_combo)
        safety["move_checksum_algorithm"] = combo_value(self.move_checksum_combo)
        safety["replica_verification"] = combo_value(self.replica_verification_combo)
        safety["conflict_policy"] = "conflict_folder"
        safety["exact_duplicate_policy"] = combo_value(self.exact_duplicate_combo)
        safety["conflict_filename_appendage"] = self.conflict_appendage_edit.text()
        safety["conflict_folder"] = self.conflict_folder_edit.text().strip()
        safety["manual_conflict_prompt"] = False
        safety["manual_duplicate_prompt"] = self.manual_duplicate_check.isChecked()
        safety["space_policy"] = combo_value(self.space_policy_combo)
        safety["manual_space_prompt"] = self.manual_space_check.isChecked()
        safety["fallback_destination_roots"] = [
            item.strip() for item in self.fallback_destinations_edit.text().split(";") if item.strip()
        ]
        safety["minimum_destination_free_percent"] = self.minimum_percent_spin.value()
        safety["minimum_destination_free_gb"] = self.minimum_gb_spin.value()
        safety["warn_source_free_percent"] = self.source_warning_spin.value()
        safety["file_error_policy"] = combo_value(self.file_error_policy_combo)
        safety["manual_error_prompt"] = self.manual_error_check.isChecked()
        safety["io_retry_count"] = self.retry_count_spin.value()
        safety["io_retry_delay_seconds"] = self.retry_delay_spin.value()
        candidate["monitor"]["poll_seconds"] = self.poll_spin.value()
        candidate["monitor"]["idle_scan_max_seconds"] = self.idle_scan_spin.value()
        candidate["location"]["online_place_names"] = self.location_enabled_check.isChecked()
        candidate["location"]["user_agent"] = self.location_user_agent_edit.text().strip()
        candidate["travel_libraries"] = copy.deepcopy(self.travel_libraries)
        candidate["transfer_hubs"] = copy.deepcopy(self.transfer_hubs)
        candidate["digest_inboxes"] = copy.deepcopy(self.digest_inboxes)
        candidate["replica_destinations"] = copy.deepcopy(self.replica_destinations)
        brackets = candidate["organization"][
            "long_exposure_brackets"
        ]
        brackets["enabled"] = self.bracket_enabled_check.isChecked()
        brackets["folder_name"] = (
            self.bracket_folder_name_edit.text().strip()
        )
        brackets["minimum_group_size"] = (
            self.bracket_minimum_group_spin.value()
        )
        brackets["maximum_gap_seconds"] = (
            self.bracket_maximum_gap_spin.value()
        )
        brackets["minimum_long_exposure_seconds"] = (
            self.bracket_minimum_exposure_spin.value()
        )
        for kind, controls in self.media_controls.items():
            rule = candidate["media_rules"][kind]
            rule["enabled"] = controls["enabled"].isChecked()
            rule["extensions"] = [
                item for item in re.split(r"[,;\s]+", controls["extensions"].text()) if item
            ]
            rule["folder_segments"] = [
                combo_value(combo) for combo in controls["segments"] if combo_value(combo)
            ]
            rule["filename_template"] = controls["filename"].text().strip() or "{original}"
        return normalize_config(candidate)

    def save_settings(self) -> None:
        try:
            candidate = self._collect_config()
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not save settings", str(exc))
            return
        current_identity = self.config["identification"]
        candidate_identity = candidate["identification"]
        identity_names_changed = any(
            current_identity.get(key) != candidate_identity.get(key)
            for key in ("folder_name", "identity_filename", "history_folder_name")
        )
        if identity_names_changed and self.config.get("card_profiles") and not is_yes(
            QMessageBox.warning(
                self,
                "Card metadata names changed",
                "These names are used to discover and onboard cards, but existing metadata folders and files are not renamed automatically.\n\n"
                "Previously onboarded cards may need to be onboarded again using the new names. Save these changes anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        ):
            return
        try:
            save_config(candidate, self.config_path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not save settings", str(exc))
            return
        self.config = candidate
        self.library_destinations = copy.deepcopy(
            candidate.get("library_destinations", [])
        )
        self.default_library_id = str(
            candidate.get("default_library_id", "")
        )
        self.travel_libraries = copy.deepcopy(candidate.get("travel_libraries", []))
        self.transfer_hubs = copy.deepcopy(candidate.get("transfer_hubs", []))
        self.digest_inboxes = copy.deepcopy(candidate.get("digest_inboxes", []))
        self.replica_destinations = copy.deepcopy(candidate.get("replica_destinations", []))
        self._refresh_travel_libraries()
        self._refresh_transfer_hubs()
        self._refresh_digest_inboxes()
        self._refresh_replicas()
        self._refresh_library_destinations()
        self.monitor.update_config(candidate)
        self.status_label.setText("Settings saved")
        self.events.put(ActivityEvent("success", f"Settings saved to {self.config_path}"))
        self.refresh_cards()
        self._refresh_conflicts()
        self._set_settings_dirty(False)

    def _load_config_into_controls(self) -> None:
        self._loading_controls = True
        identification = self.config["identification"]
        safety = self.config["safety"]
        local_history = self.config["local_history"]
        self.library_destinations = copy.deepcopy(
            self.config.get("library_destinations", [])
        )
        self.default_library_id = str(
            self.config.get("default_library_id", "")
        )
        self._refresh_library_destinations()
        self.destination_edit.setText(self.config["destination_root"])
        self.safety_destination_edit.setText(self.config["destination_root"])
        self.folder_name_edit.setText(identification["folder_name"])
        self.identity_filename_edit.setText(identification["identity_filename"])
        self.history_folder_edit.setText(identification["history_folder_name"])
        self.session_filename_edit.setText(identification["session_filename_template"])
        self.checksum_filename_edit.setText(identification["checksum_filename_template"])
        history_segments = list(identification.get("history_folder_segments", []))
        for index, combo in enumerate(self.history_segment_combos):
            set_combo_data(combo, history_segments[index] if index < len(history_segments) else "")
        self.shared_history_check.setChecked(bool(identification.get("shared_history", True)))
        self.require_portable_log_check.setChecked(bool(identification.get("require_log_before_source_delete", True)))
        self.local_history_enabled_check.setChecked(bool(local_history.get("enabled", True)))
        self.local_history_edit.setText(str(local_history.get("directory", "")))
        self.require_local_log_check.setChecked(bool(local_history.get("require_before_source_delete", True)))
        set_combo_data(self.default_action_combo, safety["default_action"])
        set_combo_data(self.copy_verification_combo, safety["copy_verification"])
        set_combo_data(self.move_checksum_combo, safety["move_checksum_algorithm"])
        set_combo_data(self.replica_verification_combo, safety.get("replica_verification", "sha256"))
        set_combo_data(self.exact_duplicate_combo, safety.get("exact_duplicate_policy", "rename"))
        self.conflict_appendage_edit.setText(str(safety.get("conflict_filename_appendage", "_{number}")))
        self.conflict_folder_edit.setText(str(safety.get("conflict_folder", "Conflicts")))
        self.manual_duplicate_check.setChecked(bool(safety.get("manual_duplicate_prompt", False)))
        set_combo_data(self.space_policy_combo, safety.get("space_policy", "fallback_then_block"))
        self.manual_space_check.setChecked(bool(safety.get("manual_space_prompt", True)))
        self.fallback_destinations_edit.setText("; ".join(safety.get("fallback_destination_roots", [])))
        self.minimum_percent_spin.setValue(float(safety["minimum_destination_free_percent"]))
        self.minimum_gb_spin.setValue(float(safety["minimum_destination_free_gb"]))
        self.source_warning_spin.setValue(float(safety["warn_source_free_percent"]))
        set_combo_data(self.file_error_policy_combo, safety.get("file_error_policy", "retry_then_continue"))
        self.manual_error_check.setChecked(bool(safety.get("manual_error_prompt", True)))
        self.retry_count_spin.setValue(int(safety.get("io_retry_count", 2)))
        self.retry_delay_spin.setValue(float(safety.get("io_retry_delay_seconds", 1.0)))
        self.poll_spin.setValue(float(self.config["monitor"]["poll_seconds"]))
        self.idle_scan_spin.setValue(float(self.config["monitor"].get("idle_scan_max_seconds", 300)))
        self.location_enabled_check.setChecked(bool(self.config["location"]["online_place_names"]))
        self.location_user_agent_edit.setText(str(self.config["location"]["user_agent"]))
        brackets = self.config["organization"][
            "long_exposure_brackets"
        ]
        self.bracket_enabled_check.setChecked(
            bool(brackets.get("enabled", False))
        )
        self.bracket_folder_name_edit.setText(
            str(
                brackets.get(
                    "folder_name",
                    "Long Exposure Brackets",
                )
            )
        )
        self.bracket_minimum_group_spin.setValue(
            int(brackets.get("minimum_group_size", 3))
        )
        self.bracket_maximum_gap_spin.setValue(
            float(brackets.get("maximum_gap_seconds", 30.0))
        )
        self.bracket_minimum_exposure_spin.setValue(
            float(
                brackets.get(
                    "minimum_long_exposure_seconds",
                    1.0,
                )
            )
        )
        self.travel_libraries = copy.deepcopy(self.config.get("travel_libraries", []))
        self._refresh_travel_libraries()
        self.transfer_hubs = copy.deepcopy(self.config.get("transfer_hubs", []))
        self._refresh_transfer_hubs()
        self.digest_inboxes = copy.deepcopy(self.config.get("digest_inboxes", []))
        self._refresh_digest_inboxes()
        self.replica_destinations = copy.deepcopy(self.config.get("replica_destinations", []))
        self._refresh_replicas()
        for kind, controls in self.media_controls.items():
            rule = self.config["media_rules"][kind]
            controls["enabled"].setChecked(bool(rule.get("enabled", True)))
            controls["extensions"].setText(", ".join(rule["extensions"]))
            segments = list(rule.get("folder_segments", []))[
                :MAX_EDITABLE_SOURCE_LEVELS
            ]
            target_count = max(1, len(segments))
            while len(controls["segments"]) < target_count:
                self._append_media_segment(kind)
            while len(controls["segments"]) > target_count:
                self._remove_media_segment(kind)
            for index, combo in enumerate(controls["segments"]):
                set_combo_data(combo, segments[index] if index < len(segments) else "")
                if combo.currentIndex() < 0 and index < len(segments):
                    combo.setEditText(segments[index])
            controls["filename"].setText(str(rule.get("filename_template", "{original}")))
            self.existing_media_checks[kind].setChecked(bool(rule.get("enabled", True)))
            self._update_media_preview(kind)
        self._reset_existing_structure()
        self._update_history_preview()
        self._update_existing_preview()
        self._export_library_changed()
        self._loading_controls = False
        self._set_settings_dirty(False)

    def _export_client_settings(self) -> None:
        try:
            candidate = self._collect_config()
        except ValueError as exc:
            QMessageBox.critical(self, "Cannot export settings", str(exc))
            return
        selected, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export client settings",
            "PhotoCardOrganizer-client-settings.json",
            "JSON settings (*.json);;All files (*)",
        )
        if not selected:
            return
        if not Path(selected).suffix:
            selected += ".json"
        try:
            export_client_settings(candidate, selected)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not export settings", str(exc))
            return
        self.events.put(ActivityEvent("success", f"Client settings exported to {selected}"))
        QMessageBox.information(self, "Settings exported", "The portable client-settings file was created.")

    def _import_client_settings(self) -> None:
        selected, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Import client settings",
            "",
            "JSON settings (*.json);;All files (*)",
        )
        if not selected:
            return
        try:
            current = self._collect_config()
            candidate = import_client_settings(current, selected)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not import settings", str(exc))
            return
        if not is_yes(
            QMessageBox.question(
                self,
                "Import client settings",
                "Apply the portable organization, safety, source-profile, Digest Inbox, and backup definitions to this computer?\n\n"
                "Local library and drive paths will be retained.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        ):
            return
        try:
            save_config(candidate, self.config_path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not save imported settings", str(exc))
            return
        self.config = candidate
        self._load_config_into_controls()
        self.monitor.update_config(candidate)
        self.refresh_cards()
        self._refresh_conflicts()
        self.events.put(ActivityEvent("success", f"Client settings imported from {selected}"))
        QMessageBox.information(self, "Settings imported", "The client settings were imported successfully.")

    def _add_fallback_destination(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Add a fallback library")
        if not selected:
            return
        current = [
            item.strip() for item in self.fallback_destinations_edit.text().split(";") if item.strip()
        ]
        if path_key(selected) not in {path_key(item) for item in current}:
            current.append(selected)
        self.fallback_destinations_edit.setText("; ".join(current))

    def _selected_library_destination(self) -> dict | None:
        if not hasattr(self, "library_table"):
            return None
        row = self.library_table.currentRow()
        item = self.library_table.item(row, 0) if row >= 0 else None
        library_id = (
            str(item.data(Qt.ItemDataRole.UserRole) or "")
            if item
            else ""
        )
        return next(
            (
                library
                for library in self.library_destinations
                if str(library.get("id", "")) == library_id
            ),
            None,
        )

    def _refresh_library_destinations(self) -> None:
        if not hasattr(self, "library_table"):
            return
        selected = self._selected_library_destination()
        selected_id = str(selected.get("id", "")) if selected else ""
        self.library_table.setRowCount(0)
        kind_labels = {
            "local": "Local",
            "network": "Network",
            "removable": "Removable",
        }
        enabled_count = 0
        online_count = 0
        selected_row = -1
        for library in self.library_destinations:
            row = self.library_table.rowCount()
            self.library_table.insertRow(row)
            library_id = str(library.get("id", ""))
            root = str(library.get("root", ""))
            enabled = bool(library.get("enabled", True))
            if enabled:
                enabled_count += 1
            online = enabled and directory_available(root)
            if online:
                online_count += 1
            if not enabled:
                state = "Disabled"
            elif not online:
                state = "Offline"
            else:
                state = library_metadata_status(root)
            try:
                free = (
                    format_bytes(capacity_for(Path(root)).free_bytes)
                    if online
                    else "—"
                )
            except OSError:
                free = "Unavailable"
            values = (
                str(library.get("name", "Library")),
                kind_labels.get(
                    str(library.get("kind", "local")),
                    "Local",
                ),
                state,
                free,
                root or "Not configured",
                (
                    "Default"
                    if library_id == self.default_library_id
                    else ""
                ),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(
                        Qt.ItemDataRole.UserRole,
                        library_id,
                    )
                item.setToolTip(value)
                self.library_table.setItem(row, column, item)
            if (
                library_id == selected_id
                or (
                    not selected_id
                    and library_id == self.default_library_id
                )
            ):
                selected_row = row
        if selected_row >= 0:
            self.library_table.selectRow(selected_row)
        default = next(
            (
                library
                for library in self.library_destinations
                if str(library.get("id", ""))
                == self.default_library_id
            ),
            None,
        )
        default_name = (
            str(default.get("name", "No default"))
            if default
            else "No default"
        )
        self.library_summary_label.setText(
            f"{len(self.library_destinations)} configured • "
            f"{enabled_count} enabled • {online_count} online\n"
            f"Default for new imports: {default_name}"
        )
        self._refresh_import_library_choices()
        self._update_library_action_state()

    def _update_library_action_state(self) -> None:
        if not hasattr(self, "library_table"):
            return
        selected = self._selected_library_destination()
        available = selected is not None
        is_default = bool(
            selected
            and str(selected.get("id", ""))
            == self.default_library_id
        )
        online = bool(
            selected
            and directory_available(selected.get("root", ""))
        )
        import_target = self._import_merge_target()
        self.import_or_merge_button.setEnabled(
            import_target is not None
        )
        self.library_export_button.setEnabled(online and not self._manual_import_running)
        self.reorganize_selected_library_button.setEnabled(online and not self._manual_import_running)
        if import_target is not None:
            self.import_or_merge_button.setToolTip(
                "Add files from another folder or library to "
                f"{import_target.get('name', 'the selected library')} through "
                "a reviewed copy or verified-move workflow."
            )
        else:
            self.import_or_merge_button.setToolTip(
                "Set up an enabled library before importing or merging files."
            )
        self.edit_library_button.setEnabled(available)
        self.default_library_button.setEnabled(
            available
            and not is_default
            and bool(selected.get("enabled", True))
            and bool(str(selected.get("root", "")).strip())
        )
        self.open_selected_library_button.setEnabled(online)
        self.upgrade_library_button.setEnabled(online)
        if online:
            metadata_state = library_metadata_status(
                selected.get("root", "")
            )
            if metadata_state == "Not initialized":
                metadata_action = "Initialize metadata"
            elif metadata_state.startswith("Upgrade available"):
                metadata_action = "Upgrade metadata"
            elif metadata_state == "Needs attention":
                metadata_action = "Repair metadata"
            else:
                metadata_action = "Check metadata"
        else:
            metadata_action = "Prepare metadata"
        self.upgrade_library_button.setText(metadata_action)
        self.remove_library_button.setEnabled(
            available and len(self.library_destinations) > 1
        )

    def _library_roots_except(
        self,
        library_id: str = "",
    ) -> tuple[Path, ...]:
        return tuple(
            Path(str(library["root"])).expanduser()
            for library in self.library_destinations
            if str(library.get("id", "")) != library_id
            and str(library.get("root", "")).strip()
        )

    def _add_library_destination(self) -> None:
        dialog = LibraryDestinationDialog(
            self,
            existing_roots=self._library_roots_except(),
        )
        if (
            dialog.exec() != QDialog.DialogCode.Accepted
            or dialog.result_library is None
        ):
            return
        candidate = dict(dialog.result_library)
        existing_ids = {
            str(library.get("id", ""))
            for library in self.library_destinations
        }
        base_id = str(candidate["id"])
        suffix = 2
        while str(candidate["id"]) in existing_ids:
            candidate["id"] = f"{base_id}-{suffix}"
            suffix += 1
        self.library_destinations.append(candidate)
        self._refresh_library_destinations()
        self._update_settings_dirty_state()

    def _edit_library_destination(self) -> None:
        selected = self._selected_library_destination()
        if selected is None:
            return
        library_id = str(selected.get("id", ""))
        dialog = LibraryDestinationDialog(
            self,
            selected,
            existing_roots=self._library_roots_except(library_id),
        )
        if (
            dialog.exec() != QDialog.DialogCode.Accepted
            or dialog.result_library is None
        ):
            return
        self.library_destinations = [
            dialog.result_library
            if str(library.get("id", "")) == library_id
            else library
            for library in self.library_destinations
        ]
        if library_id == self.default_library_id:
            self._apply_default_library_to_controls()
        self._refresh_library_destinations()
        self._update_settings_dirty_state()

    def _set_default_library(self) -> None:
        selected = self._selected_library_destination()
        if selected is None:
            return
        self.default_library_id = str(selected["id"])
        self._apply_default_library_to_controls()
        self._refresh_library_destinations()
        self._update_settings_dirty_state()

    def _apply_default_library_to_controls(self) -> None:
        selected = next(
            (
                library
                for library in self.library_destinations
                if str(library.get("id", ""))
                == self.default_library_id
            ),
            None,
        )
        if selected is None:
            return
        root = str(selected.get("root", ""))
        if hasattr(self, "destination_edit"):
            self.destination_edit.setText(root)
        if hasattr(self, "safety_destination_edit"):
            self.safety_destination_edit.setText(root)
        if hasattr(self, "export_library_label"):
            self._export_library_changed()

    def _open_selected_library(self) -> None:
        selected = self._selected_library_destination()
        if selected is None:
            return
        try:
            open_local_path(Path(str(selected["root"])))
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Could not open library",
                str(exc),
            )

    def _upgrade_selected_library(self) -> None:
        selected = self._selected_library_destination()
        if selected is None:
            return
        if self._saved_processing_config(
            "preparing library metadata"
        ) is None:
            return
        root = Path(str(selected.get("root", ""))).expanduser()
        current_status = library_metadata_status(root)
        legacy_logs = len(
            list(root.glob("PhotoCardOrganizer-export-*.jsonl"))
        )
        summary = (
            f"Library: {selected['name']}\n"
            f"Folder: {root}\n"
            f"Current state: {current_status}\n"
            f"Legacy export-session records to migrate: {legacy_logs}\n\n"
            "The app will initialize, check, or upgrade library.json inside "
            ".photocard-organizer. Existing metadata is backed up before any "
            "schema migration. Media files are not changed."
        )
        if not is_yes(
            QMessageBox.question(
                self,
                "Confirm metadata preparation",
                summary,
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        ):
            return
        try:
            result = upgrade_library_metadata(
                root,
                library_id=str(selected["id"]),
                name=str(selected["name"]),
            )
        except (OSError, ValueError) as exc:
            QMessageBox.critical(
                self,
                "Could not prepare library metadata",
                str(exc),
            )
            return
        details = [
            f"Metadata: {result.metadata_path}",
            (
                "Migrated export-session records: "
                f"{result.migrated_export_sessions}"
            ),
        ]
        if result.backup_path is not None:
            details.append(f"Backup: {result.backup_path}")
        QMessageBox.information(
            self,
            "Library metadata ready",
            "\n".join(details),
        )
        self._refresh_library_destinations()

    def _remove_library_destination(self) -> None:
        selected = self._selected_library_destination()
        if selected is None or len(self.library_destinations) <= 1:
            return
        if not is_yes(
            QMessageBox.warning(
                self,
                "Forget library destination",
                f"Forget {selected['name']} on this computer?\n\n"
                "No media, manifests, or session records will be deleted.",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        ):
            return
        removed_id = str(selected["id"])
        self.library_destinations = [
            library
            for library in self.library_destinations
            if str(library.get("id", "")) != removed_id
        ]
        if self.default_library_id == removed_id:
            replacement = next(
                (
                    library
                    for library in self.library_destinations
                    if library.get("enabled", True)
                    and library.get("root")
                ),
                self.library_destinations[0],
            )
            self.default_library_id = str(replacement["id"])
            self._apply_default_library_to_controls()
        self._refresh_library_destinations()
        self._update_settings_dirty_state()

    def _refresh_replicas(self) -> None:
        if not hasattr(self, "replica_table"):
            return
        selected = self._selected_replica()
        selected_id = str(selected.get("id", "")) if selected else ""
        self.replica_table.setRowCount(len(self.replica_destinations))
        selected_row = -1
        for row, replica in enumerate(self.replica_destinations):
            values = (
                str(replica["name"]),
                str(replica.get("root", "")),
                "Yes" if replica.get("required", True) else "No",
                "Yes" if replica.get("include_history", True) else "No",
                str(replica.get("conflict_policy", "block")).replace("_", " ").title(),
                "Enabled" if replica.get("enabled", True) else "Disabled",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, replica["id"])
                item.setToolTip(value)
                self.replica_table.setItem(row, column, item)
            if replica["id"] == selected_id:
                selected_row = row
        if selected_row >= 0:
            self.replica_table.selectRow(selected_row)

    def _selected_replica(self) -> dict | None:
        if not hasattr(self, "replica_table"):
            return None
        row = self.replica_table.currentRow()
        item = self.replica_table.item(row, 0) if row >= 0 else None
        replica_id = str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""
        return next(
            (replica for replica in self.replica_destinations if replica.get("id") == replica_id),
            None,
        )

    def _add_replica(self) -> None:
        dialog = ReplicaDialog(self, Path(self.safety_destination_edit.text()).expanduser())
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_replica:
            return
        candidate = dict(dialog.result_replica)
        if candidate.get("root") and any(
            replica.get("root") and path_key(replica["root"]) == path_key(candidate["root"])
            for replica in self.replica_destinations
        ):
            QMessageBox.critical(self, "Duplicate destination", "That backup root is already configured.")
            return
        existing_ids = {str(replica["id"]) for replica in self.replica_destinations}
        if candidate["id"] in existing_ids:
            base_id = str(candidate["id"])
            suffix = 2
            while f"{base_id}-{suffix}" in existing_ids:
                suffix += 1
            candidate["id"] = f"{base_id}-{suffix}"
        self.replica_destinations.append(candidate)
        self._refresh_replicas()
        self._update_settings_dirty_state()
        for row in range(self.replica_table.rowCount()):
            item = self.replica_table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == candidate["id"]:
                self.replica_table.selectRow(row)
                break

    def _edit_replica(self) -> None:
        replica = self._selected_replica()
        if not replica:
            QMessageBox.information(self, "Select a destination", "Select one backup destination first.")
            return
        dialog = ReplicaDialog(
            self,
            Path(self.safety_destination_edit.text()).expanduser(),
            replica,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_replica:
            return
        candidate = dialog.result_replica
        if candidate.get("root") and any(
            item["id"] != replica["id"]
            and item.get("root")
            and path_key(item["root"]) == path_key(candidate["root"])
            for item in self.replica_destinations
        ):
            QMessageBox.critical(self, "Duplicate destination", "That backup root is already configured.")
            return
        self.replica_destinations = [
            candidate if item["id"] == replica["id"] else item
            for item in self.replica_destinations
        ]
        self._refresh_replicas()
        self._update_settings_dirty_state()

    def _remove_replica(self) -> None:
        replica = self._selected_replica()
        if not replica:
            QMessageBox.information(self, "Select a destination", "Select one backup destination first.")
            return
        if not is_yes(
            QMessageBox.question(
                self,
                "Remove backup destination",
                f"Remove {replica['name']} from this computer?\n\nNo files will be deleted.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        ):
            return
        self.replica_destinations = [
            item for item in self.replica_destinations if item["id"] != replica["id"]
        ]
        self._refresh_replicas()
        self._update_settings_dirty_state()

    def _conflict_manifest(self) -> ImportManifest:
        root = self.safety_destination_edit.text().strip() or self.config["destination_root"]
        return ImportManifest(Path(root).expanduser(), create=False)

    def _reset_conflict_page(self, *_args) -> None:
        self.conflict_page_index = 0
        self._refresh_conflicts()

    def _change_conflict_page(self, delta: int) -> None:
        self.conflict_page_index = max(
            0, self.conflict_page_index + int(delta)
        )
        self._refresh_conflicts()

    def _refresh_conflicts(self, *_args) -> None:
        if not hasattr(self, "conflict_table"):
            return
        selected_records = self._selected_conflict_records()
        selected_ids = {
            str(record.get("id", "")) for record in selected_records
        }
        current = self._selected_conflict_record()
        current_id = str(current.get("id", "")) if current else ""
        self.conflict_records = {}
        status = combo_value(self.conflict_filter_combo)
        search = self.conflict_search_edit.text().strip()
        try:
            manifest = self._conflict_manifest()
            total = manifest.conflict_count(status=status, search=search)
            page_count = max(
                1,
                (total + self.conflict_page_size - 1)
                // self.conflict_page_size,
            )
            self.conflict_page_index = min(
                self.conflict_page_index, page_count - 1
            )
            offset = self.conflict_page_index * self.conflict_page_size
            records = manifest.conflicts(
                status=status,
                limit=self.conflict_page_size,
                offset=offset,
                search=search,
            )
        except Exception as exc:
            records = []
            total = 0
            page_count = 1
            offset = 0
            self.events.put(ActivityEvent("error", f"Could not read conflict review: {exc}"))
        self.conflict_table.blockSignals(True)
        self.conflict_table.setRowCount(len(records))
        selected_rows = []
        current_row = -1
        for row, record in enumerate(records):
            record_id = str(record["id"])
            self.conflict_records[record_id] = record
            created = str(record.get("created_at", ""))[:16].replace("T", " ")
            incoming = Path(str(record.get("incoming_path", "")))
            values = (
                created,
                str(record.get("conflict_type", "")).replace("_", " ").title(),
                incoming.name,
                str(record.get("resolution", "")).replace("_", " ").title(),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, record_id)
                item.setToolTip(value)
                self.conflict_table.setItem(row, column, item)
            if record_id in selected_ids:
                selected_rows.append(row)
            if record_id == current_id:
                current_row = row
        page_start = offset + 1 if total else 0
        page_end = min(offset + len(records), total)
        self.conflict_summary_label.setText(
            f"Showing {page_start}-{page_end} of {total}"
        )
        self.conflict_page_label.setText(
            f"Page {self.conflict_page_index + 1} of {page_count}"
        )
        self.conflict_previous_button.setEnabled(
            self.conflict_page_index > 0
        )
        self.conflict_next_button.setEnabled(
            self.conflict_page_index + 1 < page_count
        )
        if current_row < 0 and selected_rows:
            current_row = selected_rows[0]
        if current_row < 0 and records:
            current_row = 0
            selected_rows = [0]
        if current_row >= 0:
            self.conflict_table.setCurrentCell(current_row, 0)
            selection = self.conflict_table.selectionModel()
            if selection is not None:
                for row in selected_rows:
                    selection.select(
                        self.conflict_table.model().index(row, 0),
                        QItemSelectionModel.SelectionFlag.Select
                        | QItemSelectionModel.SelectionFlag.Rows,
                    )
        else:
            self._clear_conflict_preview()
        self.conflict_table.blockSignals(False)
        self._show_conflict()

    def _clear_conflict_preview(self) -> None:
        if not hasattr(self, "conflict_preview_labels"):
            return
        for side in ("existing", "incoming"):
            self.conflict_preview_labels[side].clear_preview("None selected")
            self.conflict_detail_labels[side].clear()
            self.conflict_path_labels[side].setText("None selected")
        self.conflict_resolution_label.clear()

    def _selected_conflict_record(self) -> dict | None:
        if not hasattr(self, "conflict_table"):
            return None
        row = self.conflict_table.currentRow()
        item = self.conflict_table.item(row, 0) if row >= 0 else None
        record_id = str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""
        return self.conflict_records.get(record_id)

    def _selected_conflict_records(self) -> list[dict]:
        if not hasattr(self, "conflict_table"):
            return []
        selection = self.conflict_table.selectionModel()
        if selection is None:
            return []
        records = []
        for index in selection.selectedRows(0):
            item = self.conflict_table.item(index.row(), 0)
            record_id = (
                str(item.data(Qt.ItemDataRole.UserRole) or "")
                if item is not None
                else ""
            )
            record = self.conflict_records.get(record_id)
            if record is not None:
                records.append(record)
        return records

    def _file_preview(self, path: Path, side: str) -> str:
        label = self.conflict_preview_labels[side]
        label.clear_preview("Preview unavailable")
        dimensions = ""
        if path.is_file():
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            image = reader.read()
            if not image.isNull():
                dimensions = f"{image.width()} x {image.height()}"
                label.set_preview_pixmap(QPixmap.fromImage(image))
        if not path.is_file():
            return "File is missing"
        try:
            stat = path.stat()
            modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            size = format_bytes(stat.st_size)
            return f"{dimensions + ' | ' if dimensions else ''}{size}\nModified {modified}"
        except OSError as exc:
            return str(exc)

    def _show_conflict(self) -> None:
        record = self._selected_conflict_record()
        selected_count = len(self._selected_conflict_records())
        self.mark_conflicts_reviewed_button.setEnabled(
            selected_count > 0
        )
        self.mark_conflicts_reviewed_button.setText(
            (
                f"Mark {selected_count} selected reviewed"
                if selected_count > 1
                else "Mark selected reviewed"
            )
        )
        if not record:
            self._clear_conflict_preview()
            return
        for side, key in (("existing", "existing_path"), ("incoming", "incoming_path")):
            path = Path(str(record.get(key, "")))
            root = Path(self.safety_destination_edit.text().strip() or self.config["destination_root"])
            try:
                display_path = str(path.relative_to(root))
            except ValueError:
                display_path = str(path)
            self.conflict_path_labels[side].setText(display_path)
            self.conflict_path_labels[side].setToolTip(str(path))
            self.conflict_detail_labels[side].setText(self._file_preview(path, side))
        self.conflict_resolution_label.setText(
            f"{str(record.get('conflict_type', '')).replace('_', ' ').title()}  |  "
            f"{str(record.get('resolution', '')).replace('_', ' ').title()}"
        )

    def _open_conflict_file(self, side: str) -> None:
        record = self._selected_conflict_record()
        if not record:
            QMessageBox.information(self, "Select a conflict", "Select a conflict first.")
            return
        key = "existing_path" if side == "existing" else "incoming_path"
        try:
            open_local_path(Path(str(record[key])))
        except OSError as exc:
            QMessageBox.critical(self, "Could not open file", str(exc))

    def _mark_conflict_reviewed(self) -> None:
        records = self._selected_conflict_records()
        if not records:
            QMessageBox.information(self, "Select a conflict", "Select a conflict first.")
            return
        if len(records) > 1 and not is_yes(
            QMessageBox.question(
                self,
                "Mark conflicts reviewed",
                f"Mark {len(records)} selected conflicts as reviewed?\n\n"
                "This updates review status only. No media files are changed or removed.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        ):
            return
        try:
            self._conflict_manifest().mark_conflicts_reviewed(
                [int(record["id"]) for record in records]
            )
        except Exception as exc:
            QMessageBox.critical(self, "Could not update conflict review", str(exc))
            return
        self._refresh_conflicts()

    @staticmethod
    def _user_guide_path() -> Path | None:
        filename = f"PhotoCardOrganizer-{__version__}-User-Guide.pdf"
        candidates = [
            Path(sys.executable).resolve().parent / filename,
            Path(__file__).resolve().parent.parent / "output" / "pdf" / filename,
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _changelog_path() -> Path | None:
        candidates = [
            Path(sys.executable).resolve().parent / "CHANGELOG.md",
            Path(__file__).resolve().parent.parent / "CHANGELOG.md",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    def _refresh_user_guide_status(self) -> None:
        if not hasattr(self, "user_guide_path_label"):
            return
        guide = self._user_guide_path()
        self.open_changelog_button.setEnabled(
            self._changelog_path() is not None
        )
        if guide is None:
            self.user_guide_path_label.setText(
                "The version-matched manual is not present in this build."
            )
            self.open_user_guide_button.setEnabled(False)
            return
        self.user_guide_path_label.setText(
            f"Available: {guide.name}"
        )
        self.user_guide_path_label.setToolTip(str(guide))
        self.open_user_guide_button.setEnabled(True)

    def _open_user_guide(self) -> None:
        guide = self._user_guide_path()
        if guide is None:
            self._refresh_user_guide_status()
            QMessageBox.warning(
                self,
                "User guide unavailable",
                "The version-matched PDF manual is missing from this build.",
            )
            return
        try:
            open_local_path(guide)
        except OSError as exc:
            QMessageBox.critical(
                self, "Could not open user guide", str(exc)
            )

    def _open_changelog(self) -> None:
        changelog = self._changelog_path()
        if changelog is None:
            QMessageBox.warning(
                self,
                "Changelog unavailable",
                "The cumulative CHANGELOG.md file is missing from this build.",
            )
            return
        try:
            text = changelog.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            QMessageBox.critical(
                self, "Could not read changelog", str(exc)
            )
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Photo Card Organizer changelog")
        dialog.setMinimumSize(760, 560)
        layout = QVBoxLayout(dialog)
        title = QLabel("Release history")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        history = QPlainTextEdit()
        history.setReadOnly(True)
        history.setPlainText(text)
        history.setToolTip(
            "Cumulative Photo Card Organizer release history."
        )
        layout.addWidget(history, 1)
        actions = QHBoxLayout()
        actions.addStretch(1)
        close = QPushButton("Close")
        self._add_icon(close, "close")
        close.clicked.connect(dialog.accept)
        actions.addWidget(close)
        layout.addLayout(actions)
        dialog.exec()

    def _open_library(self) -> None:
        try:
            open_local_path(Path(self.safety_destination_edit.text()).expanduser(), create=True)
        except OSError as exc:
            QMessageBox.critical(self, "Could not open library", str(exc))

    def _installation_maintenance(self) -> None:
        project_root = Path(__file__).resolve().parent.parent
        dialog = InstallationDialog(self, project_root)
        if (
            dialog.exec() == QDialog.DialogCode.Accepted
            and dialog.selected_action
            and dialog.selected_target is not None
        ):
            self._launch_installation_action(dialog.selected_action, dialog.selected_target)

    def _launch_installation_action(self, action: str, target: Path) -> None:
        if not target.is_file():
            QMessageBox.critical(self, "Maintenance unavailable", f"The maintenance program was not found:\n{target}")
            return
        verb = "start the installer or updater" if action == "install" else "start the uninstaller"
        if not is_yes(
            QMessageBox.question(
                self,
                "Confirm installation maintenance",
                f"Close Photo Card Organizer and {verb}?\n\n"
                "The installer or uninstaller asks again before it changes this computer.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        ):
            return
        try:
            if os.name == "nt":
                os.startfile(target)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["sh", str(target)], cwd=target.parent)
        except OSError as exc:
            QMessageBox.critical(self, "Could not start maintenance", str(exc))
            return
        QTimer.singleShot(250, self.quit_application)

    def _scan_now(self) -> None:
        self.refresh_cards()
        self.monitor.scan_now()
        self.status_label.setText("Scanning connected cards")

    def _toggle_pause(self) -> None:
        paused = self.monitor.toggle_paused()
        self.pause_button.setText("Resume monitoring" if paused else "Pause monitoring")
        self.status_label.setText("Background monitoring paused" if paused else "Background monitoring active")
        if hasattr(self, "tray_pause_action"):
            self.tray_pause_action.setText("Resume monitoring" if paused else "Pause monitoring")

    def _request_decision(self, request: DecisionRequest) -> DecisionResult:
        completed = threading.Event()
        holder: dict[str, DecisionResult] = {"result": DecisionResult(request.default_action)}
        self.decisions.put((request, holder, completed))
        while not completed.wait(0.1):
            if self._quitting:
                return DecisionResult(request.default_action)
        return holder["result"]

    def _persist_destination(self, path: str) -> None:
        self.destination_edit.setText(path)
        self.safety_destination_edit.setText(path)
        self.config["destination_root"] = path
        try:
            save_config(self.config, self.config_path)
            self.monitor.update_config(self.config)
            self.events.put(ActivityEvent("success", f"Default destination changed to {path}"))
            self._refresh_conflicts()
        except (OSError, ValueError) as exc:
            self.events.put(ActivityEvent("error", f"Could not save the alternate destination: {exc}"))

    def _import_selected(self) -> None:
        selected_ids = self._selected_source_ids()
        if not selected_ids:
            QMessageBox.information(
                self,
                "Select cards",
                "Select one or more connected cards or drives. Use Ctrl or Shift to select several rows.",
            )
            return
        cards = [self.card_by_id[source_id] for source_id in selected_ids if source_id in self.card_by_id]
        offline = len(selected_ids) - len(cards)
        if not cards:
            QMessageBox.information(
                self,
                "Cards not connected",
                "The selected retained profiles are offline. Connect at least one card before importing.",
            )
            return
        if offline:
            QMessageBox.information(
                self,
                "Offline profiles skipped",
                f"{offline} selected offline {'profile was' if offline == 1 else 'profiles were'} excluded from this batch.",
            )
        if self._saved_processing_config(
            "starting the selected import"
        ) is None:
            return
        selected_library = self._selected_import_library()
        if selected_library is None:
            QMessageBox.critical(
                self,
                "Choose a library",
                "Add or enable a library destination before importing.",
            )
            return
        try:
            mode = combo_value(self.import_folder_mode_combo)
            prefix = import_destination_prefix(
                mode,
                self.import_folder_name_edit.text(),
            )
        except ValueError as exc:
            QMessageBox.critical(
                self,
                "Check import folder",
                str(exc),
            )
            return
        if mode != "standard":
            cards = [
                replace(card, destination_prefix=prefix)
                for card in cards
            ]
        config_override = copy.deepcopy(self.config)
        try:
            select_library_destination(
                config_override,
                str(selected_library["id"]),
            )
            config_override = normalize_config(config_override)
        except ValueError as exc:
            QMessageBox.critical(
                self,
                "Cannot use library",
                str(exc),
            )
            return
        self._start_card_batch(
            cards,
            config_override=config_override,
        )

    @staticmethod
    def _required_replica_names(config: dict) -> list[str]:
        return [
            str(replica["name"])
            for replica in effective_replica_destinations(config)
            if replica.get("enabled", True)
            and replica.get("required", True)
            and replica.get("root")
        ]

    def _batch_review_text(self, cards: list[CardMarker], config: dict) -> str:
        copy_count = sum(card.action == "copy" for card in cards)
        move_count = len(cards) - copy_count
        visible_cards = cards[:12]
        source_lines = "\n".join(
            f"  {index}. {card.name} ({card.action})"
            for index, card in enumerate(visible_cards, 1)
        )
        if len(cards) > len(visible_cards):
            source_lines += f"\n  ...and {len(cards) - len(visible_cards)} more"
        replicas = self._required_replica_names(config)
        selected_library = library_destination(
            config,
            str(config.get("default_library_id", "")),
        )
        library_name = (
            str(selected_library.get("name", "Library"))
            if selected_library
            else "Library"
        )
        prefixes = sorted(
            {
                card.destination_prefix
                for card in cards
                if card.destination_prefix
            }
        )
        try:
            capacity = capacity_for(Path(config["destination_root"]))
            free = f"{capacity.free_percent:.1f}% / {format_bytes(capacity.free_bytes)} free"
        except OSError:
            free = "capacity unavailable"
        return (
            f"Sources ({len(cards)}):\n{source_lines}\n\n"
            f"Library: {library_name}\n"
            f"Destination: {config['destination_root']} ({free})\n"
            f"Named folder: {', '.join(prefixes) if prefixes else 'Standard organization'}\n"
            f"Operations: {copy_count} copy, {move_count} move\n"
            f"Required backups: {', '.join(replicas) if replicas else 'None'}\n"
            "Processing: one card at a time; one separate transfer session per card"
        )

    def _start_card_batch(
        self,
        cards: list[CardMarker],
        *,
        resume_monitor_after: bool = False,
        confirmation_complete: bool = False,
        hub_receipts: dict[str, dict] | None = None,
        config_override: dict | None = None,
        reorganization_plan=None,
        remove_empty_folders: bool = False,
        move_confirmation_complete: bool = False,
    ) -> bool:
        if self._manual_import_running or not cards:
            return False
        saved_config = None
        if config_override is None:
            saved_config = self._saved_processing_config(
                "starting file processing"
            )
            if saved_config is None:
                return False
        try:
            base_config = (
                normalize_config(copy.deepcopy(config_override))
                if config_override is not None
                else saved_config
            )
        except ValueError as exc:
            QMessageBox.critical(self, "Cannot start import", str(exc))
            return False
        output_roots = [("primary destination", Path(base_config["destination_root"]))]
        output_roots.extend(
            (f"backup destination {replica['name']}", Path(replica["root"]))
            for replica in effective_replica_destinations(base_config)
            if replica.get("enabled", True) and replica.get("root")
        )
        for card in cards:
            for label, output_root in output_roots:
                if (reorganization_plan is not None and card == reorganization_plan.card
                        and label == "primary destination" and card.root.resolve() == output_root.resolve()):
                    continue
                if paths_overlap(card.root, output_root):
                    QMessageBox.critical(
                        self,
                        "Source and destination overlap",
                        f"{card.name}'s source root overlaps the {label}:\n{output_root}\n\n"
                        "Choose a separate destination before importing.",
                    )
                    return False
        if not confirmation_complete and not is_yes(
            QMessageBox.question(
                self,
                "Review import queue",
                f"{self._batch_review_text(cards, base_config)}\n\nStart this import queue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        ):
            return False

        move_cards = [card for card in cards if card.action == "move"]
        if move_cards and not move_confirmation_complete:
            algorithm = str(base_config["safety"].get("move_checksum_algorithm", "sha256")).upper()
            replicas = self._required_replica_names(base_config)
            names = ", ".join(card.name for card in move_cards)
            backup_text = f" Required backups: {', '.join(replicas)}." if replicas else ""
            if not is_yes(
                QMessageBox.warning(
                    self,
                    "Additional confirmation for verified move",
                    f"Move source files from: {names}?\n\n"
                    f"Each source is deleted only after its destination copy matches with {algorithm}, "
                    f"required transfer records are written, and every required backup succeeds.{backup_text}\n\n"
                    "A failed verification, log write, backup, space check, or source change leaves the source file in place.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
            ):
                return False

        self._manual_import_running = True
        self._update_library_action_state()
        self._update_card_action_state()
        self._update_digest_action_state()
        self.organize_library_button.setEnabled(False)
        self.existing_review_button.setEnabled(False)
        self.sync_travel_button.setEnabled(False)
        self.run_hub_button.setEnabled(False)
        self.scan_export_library_button.setEnabled(False)
        self.export_selected_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.status_label.setText(f"Preparing {len(cards)} {'card' if len(cards) == 1 else 'cards'}")
        self.progress_label.setText("Preparing import queue")
        self.transfer_progress.setRange(0, 0)
        was_paused = self.monitor.is_paused

        def work() -> None:
            if not was_paused:
                self.monitor.set_paused(True)
            totals = ImportStats(card_name="Import queue")
            try:
                def scan_batch() -> None:
                    nonlocal base_config
                    for card_index, card in enumerate(cards, 1):
                        if self._quitting:
                            break
                        current_config = copy.deepcopy(base_config)
                        aggregate = ImportStats(
                            card_id=card.card_id,
                            card_name=card.name,
                            action=card.action,
                        )
                        persistent_destination = ""
                        visited = {path_key(current_config["destination_root"])}

                        def send(event: ActivityEvent) -> None:
                            if event.level == "progress":
                                details = dict(event.details)
                                details.update(
                                    {
                                        "card_index": card_index,
                                        "card_total": len(cards),
                                        "card_name": card.name,
                                    }
                                )
                                event = ActivityEvent(
                                    event.level,
                                    f"Card {card_index} of {len(cards)} - {event.message}",
                                    event.timestamp,
                                    details,
                                )
                            self.events.put(event)

                        try:
                            for _redirect in range(MAX_DESTINATION_REDIRECTS):
                                organizer = (
                                    ReorganizationOrganizer(reorganization_plan, event_callback=send)
                                    if reorganization_plan is not None else
                                    Organizer(current_config, event_callback=send, decision_callback=self._request_decision)
                                )
                                receipt_entries = (
                                    source_entries(organizer, card)
                                    if hub_receipts
                                    and card.card_id in hub_receipts
                                    else []
                                )
                                result = organizer.scan_card(
                                    card,
                                    allow_destructive=card.action == "move",
                                )
                                aggregate.merge(result)
                                if reorganization_plan is not None:
                                    if remove_empty_folders and not (result.failed or result.blocked or result.stopped_early):
                                        organizer.remove_empty_folders()
                                    break
                                if not result.requested_destination_root:
                                    receipt_hub = (
                                        hub_receipts.get(card.card_id)
                                        if hub_receipts
                                        else None
                                    )
                                    if (
                                        receipt_hub
                                        and receipt_hub.get(
                                            "write_receipts", True
                                        )
                                    ):
                                        recorded, receipt_errors = (
                                            write_digestion_receipts(
                                                current_config,
                                                receipt_hub,
                                                card,
                                                organizer.manifest,
                                                receipt_entries,
                                            )
                                        )
                                        if recorded:
                                            self.events.put(
                                                ActivityEvent(
                                                    "success",
                                                    f"{card.name}: wrote {recorded} digestion receipt(s).",
                                                )
                                            )
                                        for receipt_error in receipt_errors:
                                            aggregate.warnings.append(
                                                receipt_error
                                            )
                                            self.events.put(
                                                ActivityEvent(
                                                    "warning",
                                                    f"{card.name}: digestion receipt failed: {receipt_error}",
                                                )
                                            )
                                    break
                                requested = str(Path(result.requested_destination_root).expanduser())
                                key = path_key(requested)
                                if key in visited:
                                    aggregate.failed += 1
                                    aggregate.errors.append(
                                        f"Destination fallback loop detected at {requested}"
                                    )
                                    break
                                visited.add(key)
                                current_config = copy.deepcopy(current_config)
                                current_config["destination_root"] = requested
                                if result.persist_destination_change:
                                    persistent_destination = requested
                            else:
                                message = (
                                    "Stopped after "
                                    f"{MAX_DESTINATION_REDIRECTS} destination changes "
                                    "without reaching an available destination."
                                )
                                aggregate.failed += 1
                                aggregate.errors.append(message)
                                self.events.put(
                                    ActivityEvent(
                                        "error",
                                        f"{card.name}: {message}",
                                    )
                                )
                        except Exception as exc:
                            aggregate.failed += 1
                            aggregate.errors.append(str(exc))
                            self.events.put(
                                ActivityEvent("error", f"{card.name}: import could not continue: {exc}")
                            )
                        if persistent_destination:
                            base_config = copy.deepcopy(base_config)
                            base_config["destination_root"] = persistent_destination
                            self.ui_actions.put(
                                lambda path=persistent_destination: self._persist_destination(path)
                            )
                        totals.merge(aggregate)
                        self.events.put(
                            ActivityEvent(
                                "success" if not aggregate.failed and not aggregate.blocked else "warning",
                                f"{card.name}: {aggregate.imported} imported, "
                                f"{aggregate.skipped} already handled, {aggregate.blocked} blocked, "
                                f"{aggregate.failed} failed.",
                            )
                        )

                self.monitor.run_exclusive(scan_batch)
                self.events.put(
                    ActivityEvent(
                        "success" if not totals.failed and not totals.blocked else "warning",
                        f"Queue complete: {totals.imported} imported, {totals.skipped} already handled, "
                        f"{totals.blocked} blocked, {totals.failed} failed across {len(cards)} "
                        f"{'card' if len(cards) == 1 else 'cards'}.",
                    )
                )
            finally:
                if not was_paused or resume_monitor_after:
                    self.monitor.set_paused(False)
                self.ui_actions.put(self._import_finished)

        threading.Thread(target=work, name="manual-media-import", daemon=True).start()
        return True

    def _start_manual_import(
        self,
        card: CardMarker,
        *,
        resume_monitor_after: bool = False,
    ) -> bool:
        return self._start_card_batch([card], resume_monitor_after=resume_monitor_after)

    def _import_finished(self) -> None:
        self._manual_import_running = False
        self._update_library_action_state()
        self.organize_library_button.setEnabled(True)
        self._update_existing_review_state()
        self._update_digest_action_state()
        self._update_travel_action_state()
        self._update_hub_action_state()
        self.scan_export_library_button.setEnabled(True)
        self._update_export_action_state()
        self.save_button.setEnabled(True)
        self.status_label.setText("Import queue complete")
        self.progress_label.setText("Import complete")
        self.transfer_progress.setRange(0, 1)
        self.transfer_progress.setValue(1)
        self.refresh_cards()
        self._update_card_action_state()
        self._refresh_conflicts()
        self._refresh_digest_inboxes()
        self._refresh_travel_libraries()
        self._refresh_transfer_hubs()

    def _drain_queues(self) -> None:
        try:
            while True:
                self.ui_actions.get_nowait()()
        except queue.Empty:
            pass
        try:
            while True:
                request, holder, completed = self.decisions.get_nowait()
                if self._quitting:
                    completed.set()
                    continue
                dialog = DecisionDialog(self, request)
                dialog.exec()
                holder["result"] = dialog.result_decision
                self.events.put(
                    ActivityEvent(
                        "info",
                        f"{request.kind.replace('_', ' ').capitalize()}: "
                        f"{dialog.result_decision.action.replace('_', ' ')}",
                    )
                )
                completed.set()
        except queue.Empty:
            pass
        try:
            while True:
                event = self.events.get_nowait()
                if event.level == "progress":
                    raw_total = int(event.details.get("total", 1))
                    raw_current = int(event.details.get("current", 0))
                    if "card_index" not in event.details and raw_current >= raw_total:
                        self.transfer_progress.setRange(0, 1)
                        self.transfer_progress.setValue(0)
                        self.progress_label.setText("Monitoring active")
                        self.status_label.setText("Monitoring active")
                        continue
                    total = max(1, raw_total)
                    current = max(0, min(total, raw_current))
                    self.transfer_progress.setRange(0, total)
                    self.transfer_progress.setValue(current)
                    timing = timing_text(event.details)
                    self.progress_label.setText(
                        f"{event.message}\n{timing}" if timing else event.message
                    )
                    self.status_label.setText(event.message)
                    continue
                self.activity_table.insertRow(0)
                for column, value in enumerate(
                    (event.timestamp.strftime("%H:%M:%S"), event.level.upper(), event.message)
                ):
                    item = QTableWidgetItem(value)
                    if column == 1:
                        color_name = {
                            "success": "success",
                            "warning": "warning",
                            "error": "error",
                            "info": "muted",
                        }.get(event.level, "text")
                        item.setForeground(QColor(COLORS[color_name]))
                    item.setToolTip(value)
                    self.activity_table.setItem(0, column, item)
                while self.activity_table.rowCount() > 1000:
                    self.activity_table.removeRow(self.activity_table.rowCount() - 1)
                self.status_label.setText(event.message)
                if self.tray is not None and event.level in {"warning", "error"}:
                    icon = (
                        QSystemTrayIcon.MessageIcon.Critical
                        if event.level == "error"
                        else QSystemTrayIcon.MessageIcon.Warning
                    )
                    self.tray.showMessage("Photo Card Organizer", event.message, icon, 6000)
        except queue.Empty:
            pass

    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.events.put(ActivityEvent("warning", "System tray is unavailable; closing the window will quit the application."))
            return
        tray = QSystemTrayIcon(application_icon(), self)
        tray.setToolTip("Photo Card Organizer")
        menu = QMenu(self)
        show_action = QAction("Show Photo Card Organizer", self)
        show_action.triggered.connect(self.show_window)
        scan_action = QAction("Scan connected cards", self)
        scan_action.triggered.connect(self._tray_scan)
        self.tray_pause_action = QAction("Pause monitoring", self)
        self.tray_pause_action.triggered.connect(self._toggle_pause)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.quit_application)
        menu.addAction(show_action)
        menu.addAction(scan_action)
        menu.addAction(self.tray_pause_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        tray.activated.connect(self._tray_activated)
        tray.show()
        self.tray = tray
        self._tray_available = True

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self.show_window()

    def _tray_scan(self) -> None:
        self.show_window()
        self._scan_now()

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.refresh_cards()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        if self._quitting:
            event.accept()
            return
        if self._tray_available:
            self.hide()
            event.ignore()
            if self.tray is not None:
                self.tray.showMessage(
                    "Photo Card Organizer",
                    "Monitoring continues in the notification area.",
                    QSystemTrayIcon.MessageIcon.Information,
                    3500,
                )
            return
        self.quit_application()
        event.accept() if self._quitting else event.ignore()

    def quit_application(self) -> None:
        if self._manual_import_running:
            QMessageBox.warning(
                self,
                "Import in progress",
                "Wait for the current import queue to finish before quitting. This protects verification and transfer records.",
            )
            self.show_window()
            return
        if self._quitting:
            return
        self._quitting = True
        QApplication.quit()

    def shutdown(self) -> None:
        if self._shutdown_complete:
            return
        self._shutdown_complete = True
        if hasattr(self, "integrity_panel"):
            self.integrity_panel.shutdown()
        if hasattr(self, "event_timer"):
            self.event_timer.stop()
        if self.instance_guard is not None:
            self.instance_guard.stop_activation_server()
        if hasattr(self, "monitor"):
            self.monitor.stop()
        if self.tray is not None:
            self.tray.hide()


def run_gui(
    config: dict,
    config_path: Path,
    start_minimized: bool = False,
    instance_guard: SingleInstance | None = None,
) -> None:
    configure_windows_taskbar_identity()
    app = QApplication.instance()
    owns_application = app is None
    if app is None:
        app = QApplication(sys.argv)
    app.setApplicationName("Photo Card Organizer")
    app.setApplicationDisplayName("Photo Card Organizer")
    app.setOrganizationName("Photo Card Organizer")
    app.setDesktopFileName("photo-card-organizer")
    app.setWindowIcon(application_icon())
    app.setQuitOnLastWindowClosed(False)
    configure_application(app)
    window = PhotoCardApp(
        config,
        config_path,
        start_minimized=start_minimized,
        instance_guard=instance_guard,
    )
    app.aboutToQuit.connect(window.shutdown)
    if owns_application:
        app.exec()
