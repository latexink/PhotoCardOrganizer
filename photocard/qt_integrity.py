from __future__ import annotations

import copy
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QProgressBar, QPushButton, QTableView, QVBoxLayout, QWidget)

from .integrity import IntegrityCatalog
from .qt_library_tools import LazyTableModel


class IntegrityPanel(QWidget):
    def __init__(self, parent, config_provider, before_start, exclusive, after_finish):
        super().__init__(parent)
        self.config_provider = config_provider
        self.before_start = before_start
        self.exclusive = exclusive
        self.after_finish = after_finish
        self.running = False
        self.cancel = threading.Event()
        self.done = threading.Event()
        self.worker = None
        self.report = None
        self.selected_paths = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.library = QComboBox()
        self.library.setToolTip("Choose the library whose saved checksums will be checked.")
        self.library.currentIndexChanged.connect(self.clear_selection)
        form.addRow("Library", self.library)
        layout.addLayout(form)
        actions = QHBoxLayout()
        self.verify = QPushButton("Verify library")
        self.verify.setProperty("accent", True)
        self.verify.setToolTip("Read each file once and compare it with its saved checksum. Missing checksums are reported, not silently created.")
        self.verify.clicked.connect(lambda: self.start(False))
        self.create = QPushButton("Create missing checksums")
        self.create.setToolTip("Record the current contents of files without a baseline. Existing baselines are never replaced.")
        self.create.clicked.connect(lambda: self.start(True))
        self.choose = QPushButton("Select files")
        self.choose.setToolTip("Limit the next check to selected media inside this library.")
        self.choose.clicked.connect(self.choose_files)
        for button in (self.verify, self.create, self.choose):
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.scope = QLabel("Whole library")
        layout.addWidget(self.scope)
        self.model = LazyTableModel(("FILE", "RESULT"), values=self.display_row, parent=self)
        self.model.tooltips = lambda row: row
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(1, 245)
        layout.addWidget(self.table, 1)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        self.status = QLabel("Ready")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        footer = QHBoxLayout()
        self.stop = QPushButton("Cancel check")
        self.stop.setEnabled(False)
        self.stop.setToolTip("Stop after the current read chunk; completed results are saved.")
        self.stop.clicked.connect(self.cancel.set)
        self.open_report = QPushButton("Open report")
        self.open_report.setEnabled(False)
        self.open_report.setToolTip("Open the saved verification report from the library integrity folder.")
        self.open_report.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.report))))
        self.all_files = QPushButton("Whole library")
        self.all_files.setToolTip("Clear the file selection and include every enabled media file plus missing baseline entries.")
        self.all_files.clicked.connect(self.clear_selection)
        footer.addWidget(self.all_files)
        footer.addStretch(1)
        footer.addWidget(self.open_report)
        footer.addWidget(self.stop)
        layout.addLayout(footer)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.refresh()

    def display_row(self, row):
        try:
            return str(Path(row[0]).relative_to(self._display_root)), row[1]
        except (AttributeError, TypeError, ValueError):
            return row

    def refresh(self, library_id=None):
        if self.running:
            return
        previous = library_id or self.library.currentData()
        self.library.blockSignals(True)
        self.library.clear()
        config = self.config_provider()
        for library in config["library_destinations"]:
            if library.get("enabled", True) and library.get("root"):
                self.library.addItem(library["name"], library["id"])
        index = self.library.findData(previous or config["default_library_id"])
        if index >= 0:
            self.library.setCurrentIndex(index)
        self.library.blockSignals(False)
        root = next((p["root"] for p in config["library_destinations"] if p["id"] == self.library.currentData()), None)
        if previous != self.library.currentData() or root != getattr(self, "_display_root", None):
            self.clear_selection()
        self._display_root = root

    def clear_selection(self):
        self.selected_paths = None
        self.scope.setText("Whole library")

    def choose_files(self):
        config = self.config_provider()
        library = next((p for p in config["library_destinations"] if p["id"] == self.library.currentData()), None)
        if library:
            files, _ = QFileDialog.getOpenFileNames(self, "Select media to check", library["root"])
            if files:
                self.selected_paths = [Path(p) for p in files]
                self.scope.setText(f"{len(files)} selected files")

    def set_running(self, value):
        self.running = value
        for control in (self.library, self.verify, self.create, self.choose, self.all_files):
            control.setEnabled(not value)
        self.stop.setEnabled(value)

    def start(self, establish):
        if self.running:
            return
        if establish and QMessageBox.question(self, "Create checksum baselines",
                "Record the current contents of files that have no checksum? Existing baselines will remain unchanged.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        config = self.before_start()
        if config is None:
            return
        config = copy.deepcopy(config)
        library = next((p for p in config["library_destinations"] if p["id"] == self.library.currentData()), None)
        if library is None:
            self.after_finish()
            return
        paths = list(self.selected_paths) if self.selected_paths is not None else None
        self._display_root = library["root"]
        self.result = {}
        self.cancel.clear()
        self.done.clear()
        self.report = None
        self.open_report.setEnabled(False)
        self.model.replace_rows([])
        self.progress.setRange(0, 0)
        self.status.setText("Waiting for other disk operations...")
        self._progress = (0, 0, "")
        self.set_running(True)
        def check():
            catalog = IntegrityCatalog(Path(library["root"]))
            self.result["rows"] = catalog.run_library_check(config, establish=establish, paths=paths,
                cancel_event=self.cancel, progress=lambda current, total, path: setattr(self, "_progress", (current, total, path)))
            self.result["report"] = catalog.last_report
            self.result["cancelled"] = catalog.cancelled
        def work():
            try:
                self.exclusive(check)
            except Exception as exc:
                self.result["error"] = str(exc)
            finally:
                self.done.set()
        self.worker = threading.Thread(target=work, name="library-integrity", daemon=True)
        self.worker.start()
        self.timer.start()

    def poll(self):
        current, total, path = self._progress
        self.progress.setRange(0, total)
        self.progress.setValue(current)
        self.status.setText(f"{current} / {total} | {Path(path).name}" if total else "Preparing file list...")
        self.status.setToolTip(path)
        if not self.done.is_set():
            return
        self.timer.stop()
        self.set_running(False)
        self.after_finish()
        rows = self.result.get("rows", [])
        self.model.replace_rows(rows)
        self.report = self.result.get("report")
        self.open_report.setEnabled(bool(self.report))
        counts = {}
        for _, status in rows:
            label = "Error" if status.startswith("Error:") else status
            counts[label] = counts.get(label, 0) + 1
        prefix = "Cancelled" if self.result.get("cancelled") else "Complete"
        self.status.setText(self.result.get("error") or prefix + " | " + " | ".join(f"{key}: {value}" for key, value in counts.items()))
        self.progress.setRange(0, max(total, 1))

    def shutdown(self):
        self.cancel.set()
        if self.worker and self.worker.is_alive():
            self.worker.join()
        self.timer.stop()
