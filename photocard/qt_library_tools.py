from __future__ import annotations

import threading
import logging
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QProgressBar, QPushButton, QRadioButton, QTableView, QVBoxLayout, QWidget,
)

from .qt_common import MEDIA_LABELS, ORGANIZATION_PRESETS
from .reorganization import build_reorganization_plan
from .library_jobs import build_library_job, execute_library_job


class LazyTableModel(QAbstractTableModel):
    """Format only requested cells, not a widget item for every catalog entry."""

    def __init__(self, headers, rows=(), values=None, parent=None):
        super().__init__(parent)
        self.headers = headers
        self.rows = list(rows)
        self.values = values or (lambda row: row)
        self.tooltips = None

    def replace_rows(self, rows):
        logging.getLogger(__name__).debug("Model %x reset started; previous_rows=%d", id(self), len(self.rows))
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()
        logging.getLogger(__name__).debug("Model %x reset completed; rows=%d", id(self), len(self.rows))

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        if role == Qt.ItemDataRole.ToolTipRole and self.tooltips:
            return self.tooltips(row)[index.column()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return self.values(row)[index.column()]
        if role == Qt.ItemDataRole.UserRole:
            return getattr(row, "capture_id", None)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headers[section]
        return None


class DiagnosticDialog(QDialog):
    """Log modal boundaries without inspecting media or observing every UI event."""

    def exec(self):
        logger = logging.getLogger(__name__)
        logger.debug("Dialog %s %x opened", type(self).__name__, id(self))
        try:
            return super().exec()
        finally:
            logger.debug("Dialog %s %x closed", type(self).__name__, id(self))


class ReorganizationDialog(DiagnosticDialog):
    def __init__(self, parent, config):
        super().__init__(parent)
        self.config = config
        self.plan = None
        self._running = False
        self._cancel = threading.Event()
        self._done = threading.Event()
        self._result = {}
        self._processing = False
        self._process_after_preview = False
        self._progress = (0, "")
        self.setWindowTitle("Reorganize library")
        self.resize(900, 330)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)
        root = Path(config["destination_root"])
        heading = QLabel("Reorganize library")
        heading.setObjectName("dialogTitle")
        layout.addWidget(heading)
        title = QLabel(str(root))
        title.setWordWrap(True)
        title.setProperty("class", "muted")
        layout.addWidget(title)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setVerticalSpacing(14)
        self.preset = QComboBox()
        for name in ORGANIZATION_PRESETS:
            label = {"Media folder only": "Separate by media type", "Use current detailed rules": "Saved rules for this library"}.get(name, name)
            self.preset.addItem(label, name)
        self.preset.insertItem(1, "Default folder rules from Settings", "Use global organization rules")
        self.preset.setCurrentIndex(self.preset.findData("Use current detailed rules"))
        self.preset.setToolTip("Saved library rules include its custom overrides. Global rules use the saved Organization settings. Other layouts preserve original filenames.")
        form.addRow("Folder layout", self.preset)
        media_row = QHBoxLayout()
        self.media = {}
        for kind, name in MEDIA_LABELS.items():
            check = QCheckBox(name)
            check.setChecked(True)
            check.setToolTip(f"Include {name.lower()} using your configured file extensions.")
            self.media[kind] = check
            media_row.addWidget(check)
            check.toggled.connect(self.invalidate)
        form.addRow("Media to reorganize", media_row)
        layout.addLayout(form)
        self.preset.currentIndexChanged.connect(self.invalidate)
        self.save_layout = QCheckBox("Save this layout for this library's future imports")
        self.save_layout.setChecked(False)
        self.save_layout.setEnabled(False)
        self.save_layout.hide()
        self.save_layout.setToolTip("Save library-specific naming rules when processing starts. Other libraries and global organization stay unchanged.")
        self.preset.currentIndexChanged.connect(self._layout_changed)
        self.cleanup = QCheckBox("Remove folders left empty")
        self.cleanup.setToolTip("Remove only empty media folders found during preview. Library metadata and conflict folders are protected.")
        layout.addWidget(self.save_layout)
        layout.addWidget(self.cleanup)
        self.baselines = QCheckBox("Create missing checksums for future integrity checks")
        self.baselines.setChecked(bool(config.get("organization", {}).get("checksum_new_baselines", False)))
        self.baselines.setToolTip("Read files without baselines once. Existing checksums are retained without rechecking. Same-filesystem moves otherwise rename files without reading their contents.")
        self.baselines.toggled.connect(self.invalidate)
        self.baselines.setChecked(False)
        self.baselines.hide()
        self.table = QTableView()
        self.model = LazyTableModel(("CURRENT FILE", "PROPOSED FILE", "ACTION"), values=lambda entry: (
            str(entry.source.relative_to(root)), str(entry.destination.relative_to(root)), entry.status,
        ), parent=self)
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        for column in (0, 1):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.hide()
        layout.addWidget(self.table, 1)
        self.status = QLabel("Ready")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)
        actions = QHBoxLayout()
        self.preview = QPushButton("Preview changes")
        self.preview.setToolTip("Read metadata and list proposed paths without changing files or settings.")
        self.preview.clicked.connect(self.start_preview)
        self.process = QPushButton("Reorganize")
        self.process.setProperty("accent", True)
        self.process.setToolTip("Calculate the plan and confirm the summary. The detailed preview is optional.")
        self.process.clicked.connect(self.request_process)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        actions.addWidget(self.preview)
        actions.addStretch(1)
        actions.addWidget(cancel)
        actions.addWidget(self.process)
        layout.addLayout(actions)
        self.timer = QTimer(self)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.poll)

    def _layout_changed(self):
        changed = self.preset.currentData() != "Use current detailed rules"
        self.save_layout.setEnabled(changed)
        self.save_layout.setVisible(changed)
        self.save_layout.setChecked(changed)
        self.save_layout.setText(
            "Use global rules for this library's future imports"
            if self.preset.currentData() == "Use global organization rules"
            else "Save this layout for this library's future imports"
        )

    def invalidate(self):
        self.plan = None
        self.model.replace_rows([])
        self.table.hide()
        self.resize(self.width(), 330)
        self.process.setEnabled(True)
        self.status.setText("Ready")

    def request_process(self):
        if self._running:
            return
        if self.plan is not None:
            self.accept()
            return
        self._process_after_preview = True
        self.start_preview()

    def start_preview(self):
        if self._running:
            return
        self.invalidate()
        self._running = True
        self._cancel.clear()
        self._done.clear()
        self._result = {}
        self.preview.setEnabled(False)
        self.process.setEnabled(False)
        self.baselines.setEnabled(False)
        self.preset.setEnabled(False)
        for check in self.media.values():
            check.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        preset = str(self.preset.currentData())
        media = {kind for kind, check in self.media.items() if check.isChecked()}
        import copy
        candidate = copy.deepcopy(self.config)
        candidate["organization"]["checksum_new_baselines"] = self.baselines.isChecked()

        def progress(current, total, name):
            self._progress = (current, name)

        def work():
            try:
                self._result["plan"] = build_reorganization_plan(
                    candidate, preset, media, cancel_event=self._cancel, progress_callback=progress,
                )
            except Exception as exc:
                logging.getLogger(__name__).exception("Reorganization preview failed")
                self._result["error"] = exc
            finally:
                self._done.set()

        threading.Thread(target=work, name="reorganization-preview", daemon=True).start()
        self.timer.start()

    def poll(self):
        if not self._done.is_set():
            count, name = self._progress
            self.status.setText(f"Reading metadata: {count} files | {name}")
            return
        self.timer.stop()
        self._running = False
        self.progress.hide()
        self.preview.setEnabled(True)
        self.process.setEnabled(True)
        self.baselines.setEnabled(True)
        self.preset.setEnabled(True)
        for check in self.media.values():
            check.setEnabled(True)
        if self._cancel.is_set():
            super().reject()
            return
        error = self._result.get("error")
        if error:
            self._process_after_preview = False
            self.status.setText(str(error))
            return
        self.plan = self._result["plan"]
        self.model.replace_rows(self.plan.entries)
        conflicts = sum(entry.status == "Conflict review" for entry in self.plan.entries)
        self.status.setText(f"{len(self.plan.entries)} files | {self.plan.changes} proposed moves | {conflicts} potential conflicts")
        self.process.setEnabled(bool(self.plan.changes))
        process_requested = self._process_after_preview
        self._process_after_preview = False
        if process_requested and self.plan.changes:
            self.accept()
        else:
            self.table.show()
            self.resize(self.width(), 620)

    def reject(self):
        if self._running:
            self._cancel.set()
            self.status.setText("Cancelling preview...")
            return
        super().reject()

    def closeEvent(self, event):
        if self._running:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)


class LibraryJobDialog(DiagnosticDialog):
    """Shared preview and confirmation surface for merge and migration jobs."""

    def __init__(self, parent, config, sources, *, mode="merge", backup_root=None, migration_target=None, resume_plan=None):
        super().__init__(parent)
        self.config = config
        self.sources = sources
        self.mode = mode
        self.backup_root = backup_root
        self.migration_target = migration_target
        self.resume_plan = resume_plan
        self.plan = None
        self._processing = False
        self._running = False
        self._closing = False
        self._cancel = threading.Event()
        self._done = threading.Event()
        self._result = {}
        self.setWindowTitle({"merge": "Merge library", "migrate": "Move library", "reorganize": "Consolidate library"}.get(mode, "Library operation"))
        self.resize(960, 650)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)
        heading = QLabel(self.windowTitle())
        heading.setObjectName("dialogTitle")
        layout.addWidget(heading)
        details = QFormLayout()
        details.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        for caption, value in (("From", ', '.join(str(p) for p in sources)),
                               ("To", str(migration_target or config['destination_root']))):
            label = QLabel(value)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            details.addRow(caption, label)
        layout.addLayout(details)
        self.keep_originals = QCheckBox("Keep originals (copy instead of move)", self)
        self.keep_originals.hide()
        self.keep_originals.setToolTip("Migration moves the library by default. Enable this to leave a separate copy at its original location.")
        self.keep_originals.toggled.connect(self._invalidate_preview)
        self.transfer_method_row = QWidget()
        transfer_layout = QHBoxLayout(self.transfer_method_row)
        transfer_layout.setContentsMargins(0, 0, 0, 0)
        transfer_layout.addWidget(QLabel("Transfer"))
        self.transfer_methods = QButtonGroup(self)
        for index, text in enumerate(("Move library", "Copy and keep originals")):
            button = QRadioButton(text)
            button.setToolTip("Remove the old library after a completed move." if index == 0
                              else "Keep a separate library at the original location.")
            self.transfer_methods.addButton(button, index)
            transfer_layout.addWidget(button)
        transfer_layout.addStretch(1)
        self.transfer_methods.button(0).setChecked(True)
        self.transfer_methods.idToggled.connect(
            lambda index, checked: self.keep_originals.setChecked(index == 1) if checked else None
        )
        self.keep_originals.toggled.connect(lambda checked: self.transfer_methods.button(int(checked)).setChecked(True))
        self.transfer_method_row.setVisible(mode == "migrate")
        layout.addWidget(self.transfer_method_row)
        self.migration_checksum = QCheckBox("Verify migrated files with SHA-256")
        self.migration_checksum.setChecked(False)
        self.migration_checksum.hide()
        self.migration_checksum.setToolTip("Optional content checks for removable drives and network transfers. Otherwise same-filesystem moves rename the folder; copies check file size. Existing checksum baselines are preserved.")
        self.migration_checksum.toggled.connect(self._invalidate_preview)
        layout.addWidget(self.migration_checksum)
        self.table = QTableView()
        self.model = LazyTableModel(("SOURCE", "DESTINATION", "ACTION", "CHECKSUM"), parent=self)
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.verticalHeader().hide()
        for column in (0, 1):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnHidden(3, not (resume_plan and resume_plan.migration_checksum))
        layout.addWidget(self.table, 1)
        self.status = QLabel("Review the planned paths before starting. No files have been changed.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        actions = QHBoxLayout()
        self.preview = QPushButton("Preview changes")
        self.preview.setToolTip("Plan destination paths without changing files. Content is compared only for filename conflicts.")
        self.preview.clicked.connect(self.start_preview)
        self.process = QPushButton({"migrate": "Move library", "merge": "Combine libraries"}.get(mode, "Reorganize"))
        self.process.setProperty("accent", True)
        self.process.setEnabled(False)
        self.process.clicked.connect(self.confirm_process)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        actions.addWidget(self.preview)
        actions.addStretch(1)
        actions.addWidget(cancel)
        actions.addWidget(self.process)
        layout.addLayout(actions)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        if resume_plan is not None:
            self.keep_originals.setChecked(resume_plan.keep_originals)
            self.migration_checksum.setChecked(resume_plan.migration_checksum)
            self._result = {"plan": resume_plan}
            self._done.set()
            self.poll()
            self.process.setText("Resume operation")
            self.preview.setEnabled(False)
            self.migration_checksum.setEnabled(False)
            self.keep_originals.setEnabled(False)
            self.transfer_method_row.setEnabled(False)
            self.status.setText("Saved plan loaded. Completed files will be checked before remaining work continues.")

    def _invalidate_preview(self):
        self.model.replace_rows([])
        self.plan = None
        self.process.setEnabled(False)
        if self.mode == "migrate":
            self.process.setText("Copy library" if self.keep_originals.isChecked() else "Move library")
        self.status.setText("Options changed; generate a new preview.")

    def start_preview(self):
        if self._running:
            return
        self._running = True
        self.migration_checksum.setEnabled(False)
        self.keep_originals.setEnabled(False)
        self.transfer_method_row.setEnabled(False)
        keep_originals = self.keep_originals.isChecked()
        migration_checksum = self.migration_checksum.isChecked()
        self.plan = None
        self.model.replace_rows([])
        self.preview.setEnabled(False)
        self.process.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        self._cancel.clear()
        self._done.clear()
        self._result = {}

        def work():
            try:
                self._result["plan"] = build_library_job(
                    self.config, self.sources, mode=self.mode,
                    backup_root=self.backup_root, migration_target=self.migration_target,
                    cancel_event=self._cancel,
                    migration_checksum=migration_checksum,
                    keep_originals=keep_originals,
                    progress=lambda current, total, name: setattr(self, "_progress", (current, total, name)),
                )
            except Exception as exc:
                logging.getLogger(__name__).exception("Library %s preview failed", self.mode)
                self._result["error"] = exc
            finally:
                self._done.set()
        self._progress = (0, 0, "")
        threading.Thread(target=work, name="library-job-preview", daemon=True).start()
        self.timer.start(80)

    def poll(self):
        if not self._done.is_set():
            current, total, name = getattr(self, "_progress", (0, 0, ""))
            self.progress.setRange(0, total)
            self.progress.setValue(current)
            self.status.setText(f"{'Processing' if self._processing else 'Planning'}: {current} / {total} | {name}")
            return
        self.timer.stop()
        self._running = False
        self.migration_checksum.setEnabled(self.resume_plan is None)
        self.keep_originals.setEnabled(self.resume_plan is None)
        self.transfer_method_row.setEnabled(self.resume_plan is None)
        self.progress.hide()
        self.preview.setEnabled(self.resume_plan is None)
        if self._closing:
            super().reject()
            return
        if self._processing:
            self._processing = False
            if self._result.get("error"):
                self.status.setText(f"Completed with an error: {self._result['error']}")
                self.process.setText("Retry remaining")
                self.process.setEnabled(True)
                return
            self.status.setText("Operation completed and transfer records were saved.")
            super().accept()
            return
        if self._result.get("error"):
            self.status.setText(str(self._result["error"]))
            return
        self.plan = self._result["plan"]
        roots = tuple(self.plan.source_roots)
        migration_checksum = self.plan.migration_checksum
        def source_label(entry):
            # Rendering cells must not probe disks or depend on mutable dialog state.
            candidates = (*roots, entry.root)
            root = next((p for p in candidates if p in entry.source.parents), entry.source.parent)
            return str(entry.source.relative_to(root))
        self.model.values = lambda entry: (source_label(entry), str(entry.destination.relative_to(entry.root)), entry.action, entry.digest[:16] + "..." if entry.digest else "Size / source state")
        self.model.tooltips = lambda entry: (str(entry.source), str(entry.destination), entry.action, entry.digest)
        self.model.replace_rows(self.plan.entries)
        self.status.setText(f"{len(self.plan.entries)} files | {self.plan.changes} changes | preview complete")
        self.process.setEnabled(bool(self.plan.entries))

    def confirm_process(self):
        if self.plan is None or self._running:
            return
        verification = "SHA-256 (resumed legacy plan)" if self.plan.migration_checksum else "completion, size and source state"
        if self.mode == "migrate" and not self.plan.keep_originals:
            effect = "MOVE the library to its new location. The old location will be removed. On the same filesystem, the folder is renamed without copying its contents."
        elif self.mode == "reorganize":
            effect = "Move files into the selected organization."
        else:
            effect = "Copy files; originals will be retained."
        answer = QMessageBox.warning(self, "Confirm library operation", f"{self.plan.changes} planned changes.\n{effect}\nContent verification when copying: {verification}.\nContinue?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.resume_plan = self.plan
        self._done.clear()
        self._processing = True
        self.migration_checksum.setEnabled(False)
        self.keep_originals.setEnabled(False)
        self.transfer_method_row.setEnabled(False)
        self._running = True
        self._cancel.clear()
        self._result = {}
        self.process.setEnabled(False)
        self.preview.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        def work():
            try:
                self._result["records"] = execute_library_job(
                    self.plan, cancel_event=self._cancel,
                    progress=lambda current, total, name: setattr(self, "_progress", (current, total, name)),
                )
            except Exception as exc:
                logging.getLogger(__name__).exception("Library %s execution failed", self.mode)
                self._result["error"] = exc
            finally:
                self._done.set()
        threading.Thread(target=work, name="library-job-process", daemon=True).start()
        self.timer.start(80)

    def reject(self):
        if self._running:
            self._closing = True
            self._cancel.set()
            self.status.setText("Finishing the current file before closing...")
            return
        super().reject()

    def closeEvent(self, event):
        if self._running:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
