from __future__ import annotations

import copy
import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
    QTabWidget,
)

from .config import (
    normalize_config,
    normalize_digest_inbox,
    normalize_library_destination,
    normalize_replica_destination,
    normalize_transfer_hub,
    normalize_travel_library,
)
from .discovery import load_card_identity, write_card_identity
from .installation import InstallationInfo, detect_installation
from .models import CardMarker, DecisionRequest, DecisionResult
from .qt_common import (
    CAMERA_NAME_HELP,
    LIBRARY_SUBFOLDER_HELP,
    MEDIA_LABELS,
    ORGANIZATION_PRESETS,
    initial_import_summary,
    path_key,
    paths_overlap,
    preset_folder_segments,
)
from .structure_detection import StructureAnalysis


def is_yes(result: QMessageBox.StandardButton) -> bool:
    return result == QMessageBox.StandardButton.Yes


def set_combo_data(combo: QComboBox, value: str) -> None:
    index = combo.findData(value)
    if index < 0:
        index = combo.findText(value, Qt.MatchFlag.MatchFixedString)
    if index >= 0:
        combo.setCurrentIndex(index)
    elif combo.isEditable():
        combo.setCurrentIndex(-1)
        combo.setEditText(value)


def combo_value(combo: QComboBox) -> str:
    data = combo.currentData()
    return str(data if data is not None else combo.currentText())


def choice_combo(choices: list[tuple[str, str]], current: str = "") -> QComboBox:
    combo = QComboBox()
    for label, value in choices:
        combo.addItem(label, value)
    set_combo_data(combo, current)
    return combo


def directory_editor(
    parent: QWidget,
    value: str = "",
    *,
    title: str,
) -> tuple[QWidget, QLineEdit]:
    container = QWidget(parent)
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    edit = QLineEdit(value)
    browse = QPushButton("Browse")
    browse.setToolTip(title)
    layout.addWidget(edit, 1)
    layout.addWidget(browse)

    def choose() -> None:
        selected = QFileDialog.getExistingDirectory(parent, title, edit.text())
        if selected:
            edit.setText(selected)

    browse.clicked.connect(choose)
    return container, edit


def safe_source_folders(value: str) -> list[str]:
    folders = [item.strip() for item in re.split(r"[,;]", value) if item.strip()]
    folders = folders or ["DCIM"]
    for folder in folders:
        candidate = Path(folder)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("Media source folders must stay within the card root.")
    return folders


class CardOnboardingWizard(QWizard):
    def __init__(self, parent: QWidget, config: dict):
        super().__init__(parent)
        self.config = copy.deepcopy(config)
        self.result_root: Path | None = None
        self.result_card: CardMarker | None = None
        self.result_profile: dict | None = None
        self.result_config: dict | None = None
        self.setWindowTitle("Onboard card or drive")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setMinimumSize(820, 650)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.setButtonText(QWizard.WizardButton.FinishButton, "Confirm and start")
        self.setButtonText(QWizard.WizardButton.NextButton, "Next")
        self.setButtonText(QWizard.WizardButton.BackButton, "Back")

        safety = self.config["safety"]
        self.root_edit = QLineEdit()
        self.name_edit = QLineEdit()
        self.id_edit = QLineEdit()
        self.camera_edit = QLineEdit()
        self.camera_edit.setToolTip(CAMERA_NAME_HELP)
        self.sources_edit = QLineEdit("DCIM")
        self.identity_state = QLabel("New card identity")
        self.identity_state.setProperty("class", "muted")
        self.destination_edit = QLineEdit(self.config["destination_root"])
        self.prefix_edit = QLineEdit()
        self.prefix_edit.setToolTip(LIBRARY_SUBFOLDER_HELP)
        self.action_combo = choice_combo(
            [("Copy", "copy"), ("Move", "move")],
            str(safety.get("default_action", "copy")),
        )
        self.cleanup_check = QCheckBox("Remove empty source folders after a verified move")
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(list(ORGANIZATION_PRESETS))
        self.media_checks: dict[str, QCheckBox] = {}
        self.copy_verification_combo = choice_combo(
            [("File size", "size"), ("SHA-256", "sha256"), ("SHA-512", "sha512"), ("BLAKE2b", "blake2b")],
            str(safety.get("copy_verification", "size")),
        )
        self.move_checksum_combo = choice_combo(
            [("SHA-256", "sha256"), ("SHA-512", "sha512"), ("BLAKE2b", "blake2b")],
            str(safety.get("move_checksum_algorithm", "sha256")),
        )
        self.minimum_percent_edit = QDoubleSpinBox()
        self.minimum_percent_edit.setRange(0, 99)
        self.minimum_percent_edit.setDecimals(1)
        self.minimum_percent_edit.setSuffix(" %")
        self.minimum_percent_edit.setValue(float(safety.get("minimum_destination_free_percent", 10)))
        self.minimum_gb_edit = QDoubleSpinBox()
        self.minimum_gb_edit.setRange(0, 1_000_000)
        self.minimum_gb_edit.setDecimals(1)
        self.minimum_gb_edit.setSuffix(" GB")
        self.minimum_gb_edit.setValue(float(safety.get("minimum_destination_free_gb", 5)))
        self.location_check = QCheckBox("Resolve GPS coordinates to online place names")
        self.location_check.setChecked(bool(self.config["location"].get("online_place_names", False)))
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self._configure_tooltips()
        self.action_combo.currentIndexChanged.connect(self._update_move_controls)
        self._update_move_controls()

        self.addPage(self._identity_page())
        self.addPage(self._destination_page())
        self.addPage(self._organization_page())
        self.addPage(self._review_page())
        self.side_progress_widget = self._side_progress()
        self.setSideWidget(self.side_progress_widget)
        self.currentIdChanged.connect(self._update_progress)
        self._update_progress(0)

    def _configure_tooltips(self) -> None:
        fields: list[tuple[QWidget, str]] = [
            (self.root_edit, "Choose the root of the removable card or drive, such as E:\\ or /media/user/CAMERA."),
            (self.name_edit, "A readable card name shown in the dashboard, folders, and transfer records."),
            (self.id_edit, "A stable unique ID shared across computers. Keep it unchanged after the card has transfer history."),
            (self.camera_edit, CAMERA_NAME_HELP),
            (self.sources_edit, "Comma-separated folders relative to the card root that contain media. DCIM is the usual value."),
            (self.destination_edit, "The primary library that receives organized media. It must not overlap the card root."),
            (self.prefix_edit, LIBRARY_SUBFOLDER_HELP),
            (self.action_combo, "Copy keeps source files. Move requires cryptographic verification, required backups, and logs before deletion."),
            (self.cleanup_check, "After a verified move, remove source directories only when they are empty."),
            (self.preset_combo, "Apply a simple folder layout to every enabled media type, or retain detailed rules already configured."),
            (self.copy_verification_combo, "File size is fastest; a cryptographic hash provides stronger copy verification."),
            (self.move_checksum_combo, "Cryptographic algorithm used before any moved source file can be deleted."),
            (self.minimum_percent_edit, "Keep at least this percentage free in the destination after every transfer."),
            (self.minimum_gb_edit, "Keep at least this many gigabytes free. Both destination reserves are enforced."),
            (self.location_check, "Resolve EXIF GPS coordinates to readable place names through the configured online provider."),
        ]
        for widget, text in fields:
            widget.setToolTip(text)
        self.name_edit.setPlaceholderText("Example: Canon R5 - Card A")
        self.id_edit.setPlaceholderText("Example: canon-r5-card-a")
        self.camera_edit.setPlaceholderText("Use EXIF automatically")

    def _update_move_controls(self) -> None:
        moving = combo_value(self.action_combo) == "move"
        self.cleanup_check.setEnabled(moving)
        self.move_checksum_combo.setEnabled(moving)

    def _side_progress(self) -> QWidget:
        side = QFrame()
        side.setFixedWidth(205)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(18, 24, 18, 24)
        title = QLabel("CARD SETUP")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        self.progress_label = QLabel("1 of 4")
        self.progress_label.setProperty("class", "muted")
        layout.addWidget(self.progress_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 4)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        layout.addSpacing(18)
        self.step_labels: list[QLabel] = []
        for text in ("Card identity", "Destination", "Organization and safety", "Review"):
            label = QLabel(text)
            label.setMinimumHeight(34)
            self.step_labels.append(label)
            layout.addWidget(label)
        layout.addStretch(1)
        return side

    def _update_progress(self, page_id: int) -> None:
        if page_id < 0:
            return
        step = min(4, page_id + 1)
        self.progress.setValue(step)
        self.progress_label.setText(f"{step} of 4")
        for index, label in enumerate(self.step_labels):
            label.setStyleSheet(
                "font-weight: 600; color: #f5f5f5;" if index == page_id else "color: #9f9f9f;"
            )

    def _page(self, title: str, subtitle: str) -> tuple[QWizardPage, QFormLayout]:
        page = QWizardPage()
        page.setTitle(title)
        page.setSubTitle(subtitle)
        layout = QFormLayout(page)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setHorizontalSpacing(18)
        layout.setVerticalSpacing(12)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        return page, layout

    def _identity_page(self) -> QWizardPage:
        page, form = self._page("Card identity", "Choose the source and the stable identity retained across computers.")
        root_row = QWidget(page)
        row_layout = QHBoxLayout(root_row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        self.root_browse_button = QPushButton("Browse")
        self.root_browse_button.setFixedWidth(82)
        self.root_browse_button.setToolTip(
            "Choose the root of the card or removable drive."
        )
        self.root_browse_button.clicked.connect(self._browse_root)
        row_layout.addWidget(self.root_edit, 1)
        row_layout.addWidget(self.root_browse_button)
        form.addRow("Card or drive root", root_row)
        form.addRow("Display name", self.name_edit)
        form.addRow("Stable card ID", self.id_edit)
        form.addRow("Camera name override (optional)", self.camera_edit)
        form.addRow("Media folders on card", self.sources_edit)
        form.addRow("", self.identity_state)
        return page

    def _destination_page(self) -> QWizardPage:
        page, form = self._page("Destination", "Set the primary library and choose how source files are handled.")
        destination_row = QWidget(page)
        row_layout = QHBoxLayout(destination_row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        self.destination_browse_button = QPushButton("Browse")
        self.destination_browse_button.setFixedWidth(82)
        self.destination_browse_button.setToolTip(
            "Choose the primary destination library."
        )
        self.destination_browse_button.clicked.connect(
            self._browse_destination
        )
        row_layout.addWidget(self.destination_edit, 1)
        row_layout.addWidget(self.destination_browse_button)
        form.addRow("Destination library", destination_row)
        form.addRow("Library subfolder (optional)", self.prefix_edit)
        form.addRow("Transfer method", self.action_combo)
        form.addRow("", self.cleanup_check)
        return page

    def _organization_page(self) -> QWizardPage:
        page, form = self._page("Organization and safety", "Choose a simple layout or retain the detailed rules already configured.")
        form.addRow("Folder organization", self.preset_combo)
        media_row = QWidget(page)
        media_layout = QHBoxLayout(media_row)
        media_layout.setContentsMargins(0, 0, 0, 0)
        for kind, label in MEDIA_LABELS.items():
            check = QCheckBox(label)
            check.setChecked(bool(self.config["media_rules"][kind].get("enabled", True)))
            self.media_checks[kind] = check
            media_layout.addWidget(check)
        media_layout.addStretch(1)
        form.addRow("Media types", media_row)
        form.addRow("Copy verification", self.copy_verification_combo)
        form.addRow("Move checksum", self.move_checksum_combo)
        form.addRow("Free-space reserve (percent)", self.minimum_percent_edit)
        form.addRow("Free-space reserve (gigabytes)", self.minimum_gb_edit)
        form.addRow("", self.location_check)
        return page

    def _review_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("Review")
        page.setSubTitle("Confirm the initial scan and import before the identity is written.")
        layout = QVBoxLayout(page)
        panel = QFrame()
        panel.setProperty("class", "preview")
        panel_layout = QVBoxLayout(panel)
        heading = QLabel("Initial scan and import")
        heading.setObjectName("dialogTitle")
        panel_layout.addWidget(heading)
        panel_layout.addWidget(self.summary, 1)
        layout.addWidget(panel, 1)
        return page

    def _browse_root(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose the card or drive root", self.root_edit.text())
        if not selected:
            return
        root = Path(selected).expanduser()
        self.root_edit.setText(str(root))
        identification = self.config["identification"]
        marker_path = root / identification["folder_name"] / identification["identity_filename"]
        if marker_path.is_file():
            try:
                card = load_card_identity(
                    root.resolve(),
                    identification["folder_name"],
                    identification["identity_filename"],
                    identification["history_folder_name"],
                    combo_value(self.action_combo),
                )
            except (OSError, ValueError):
                self.identity_state.setText("Existing identity could not be read")
                self.identity_state.setProperty("class", "warning")
                self.identity_state.style().unpolish(self.identity_state)
                self.identity_state.style().polish(self.identity_state)
            else:
                self.name_edit.setText(card.name)
                self.id_edit.setText(card.card_id)
                self.camera_edit.setText(card.camera_name)
                self.sources_edit.setText(", ".join(card.source_folders))
                set_combo_data(self.action_combo, card.action)
                self.prefix_edit.setText(card.destination_prefix)
                self.cleanup_check.setChecked(card.delete_empty_folders_after_move)
                self.identity_state.setText("Existing identity detected")
                self.identity_state.setProperty("class", "success")
                self.identity_state.style().unpolish(self.identity_state)
                self.identity_state.style().polish(self.identity_state)
                return
        if not self.name_edit.text().strip():
            self.name_edit.setText(root.name or "Camera card")
        if not self.id_edit.text().strip():
            self.id_edit.setText(self._slug(self.name_edit.text()))
        self.identity_state.setText("New card identity")
        self.identity_state.setProperty("class", "muted")

    def _browse_destination(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose the destination library", self.destination_edit.text())
        if selected:
            self.destination_edit.setText(selected)

    @staticmethod
    def _slug(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "camera-card"

    def _candidate_config(self) -> dict:
        candidate = copy.deepcopy(self.config)
        candidate["destination_root"] = self.destination_edit.text().strip()
        safety = candidate["safety"]
        safety["default_action"] = combo_value(self.action_combo)
        safety["copy_verification"] = combo_value(self.copy_verification_combo)
        safety["move_checksum_algorithm"] = combo_value(self.move_checksum_combo)
        safety["minimum_destination_free_percent"] = self.minimum_percent_edit.value()
        safety["minimum_destination_free_gb"] = self.minimum_gb_edit.value()
        candidate["location"]["online_place_names"] = self.location_check.isChecked()
        preset = self.preset_combo.currentText()
        for kind, rule in candidate["media_rules"].items():
            rule["enabled"] = self.media_checks[kind].isChecked()
            rule["folder_segments"] = preset_folder_segments(
                kind,
                preset,
                list(rule.get("folder_segments", [])),
            )
        return normalize_config(candidate)

    def _candidate_card(self, candidate: dict) -> CardMarker:
        root = Path(self.root_edit.text()).expanduser().resolve()
        identification = candidate["identification"]
        identity_dir = root / identification["folder_name"]
        return CardMarker(
            root=root,
            identity_dir=identity_dir,
            identity_path=identity_dir / identification["identity_filename"],
            history_dir=identity_dir / identification["history_folder_name"],
            card_id=self.id_edit.text().strip(),
            name=self.name_edit.text().strip(),
            action=combo_value(self.action_combo),
            source_folders=tuple(safe_source_folders(self.sources_edit.text())),
            destination_prefix=self.prefix_edit.text().strip("/\\"),
            camera_name=self.camera_edit.text().strip(),
            delete_empty_folders_after_move=self.cleanup_check.isChecked(),
        )

    def validateCurrentPage(self) -> bool:  # noqa: N802 - Qt API
        try:
            page_id = self.currentId()
            if page_id == 0:
                root = Path(self.root_edit.text()).expanduser()
                if not root.is_dir() or not self.name_edit.text().strip():
                    raise ValueError("Choose an existing card root and enter a display name.")
                if not self.id_edit.text().strip():
                    self.id_edit.setText(self._slug(self.name_edit.text()))
                safe_source_folders(self.sources_edit.text())
                identification = self.config["identification"]
                marker = root / identification["folder_name"] / identification["identity_filename"]
                retained_ids = {
                    str(profile.get("id", ""))
                    for profile in self.config.get("card_profiles", [])
                }
                if self.id_edit.text().strip() in retained_ids and not marker.is_file():
                    raise ValueError(
                        "That stable card ID is already retained for another card. Enter a unique ID."
                    )
            elif page_id == 1:
                if not self.destination_edit.text().strip():
                    raise ValueError("Choose a destination library.")
                if paths_overlap(Path(self.root_edit.text()), Path(self.destination_edit.text())):
                    raise ValueError("The card root and destination library must be separate locations.")
            elif page_id == 2:
                if not any(check.isChecked() for check in self.media_checks.values()):
                    raise ValueError("Enable at least one media type.")
                self._candidate_config()
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Check setup", str(exc))
            return False
        return True

    def initializePage(self, page_id: int) -> None:  # noqa: N802 - Qt API
        super().initializePage(page_id)
        if page_id != 3:
            return
        try:
            candidate = self._candidate_config()
            card = self._candidate_card(candidate)
            self.summary.setPlainText(
                initial_import_summary(card, candidate, self.preset_combo.currentText())
            )
        except (OSError, ValueError):
            self.summary.setPlainText("Return to the previous step and check the setup values.")

    def accept(self) -> None:
        try:
            candidate = self._candidate_config()
            card = self._candidate_card(candidate)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Cannot onboard card", str(exc))
            return
        if card.identity_path.exists() and not is_yes(
            QMessageBox.question(
                self,
                "Update existing card identity",
                "Replace the existing identity settings on this card?\n\nTransfer records will be preserved.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        ):
            return
        identification = candidate["identification"]
        try:
            write_card_identity(
                card.root,
                identification["folder_name"],
                identification["identity_filename"],
                identification["history_folder_name"],
                card.name,
                card.card_id,
                card.action,
                card.source_folders,
                card.destination_prefix,
                True,
                card.delete_empty_folders_after_move,
                card.camera_name,
            )
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not write identity", str(exc))
            return
        self.result_root = card.root
        self.result_card = card
        self.result_config = candidate
        self.result_profile = {
            "id": card.card_id,
            "name": card.name,
            "last_root": str(card.root),
            "action": card.action,
            "source_folders": list(card.source_folders),
            "destination_prefix": card.destination_prefix,
            "camera_name": card.camera_name,
            "enabled": True,
            "delete_empty_folders_after_move": card.delete_empty_folders_after_move,
        }
        super().accept()


class CardProfileDialog(QDialog):
    def __init__(self, parent: QWidget, profile: dict | None = None, *, connected: bool = False):
        super().__init__(parent)
        self.original = profile or {}
        self.connected = connected
        self.result_profile: dict | None = None
        self.setWindowTitle("Edit card" if profile else "Add offline card")
        self.setMinimumWidth(650)
        layout = QVBoxLayout(self)
        title = QLabel("Edit card profile" if profile else "Add offline card profile")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.name_edit = QLineEdit(str(self.original.get("name", "")))
        self.id_edit = QLineEdit(str(self.original.get("id", "")))
        self.id_edit.setReadOnly(bool(profile))
        root_widget, self.root_edit = directory_editor(
            self,
            str(self.original.get("last_root", "")),
            title="Choose the card or drive root",
        )
        self.camera_edit = QLineEdit(str(self.original.get("camera_name", "")))
        self.camera_edit.setToolTip(CAMERA_NAME_HELP)
        self.action_combo = choice_combo(
            [("Copy", "copy"), ("Move", "move")],
            str(self.original.get("action", "copy")),
        )
        self.sources_edit = QLineEdit(", ".join(self.original.get("source_folders", ["DCIM"])))
        self.prefix_edit = QLineEdit(str(self.original.get("destination_prefix", "")))
        self.prefix_edit.setToolTip(LIBRARY_SUBFOLDER_HELP)
        self.enabled_check = QCheckBox("Profile enabled")
        self.enabled_check.setChecked(bool(self.original.get("enabled", True)))
        self.cleanup_check = QCheckBox("Remove empty source folders after a verified move")
        self.cleanup_check.setChecked(bool(self.original.get("delete_empty_folders_after_move", False)))
        self.name_edit.setToolTip("A readable name used in the dashboard and transfer records.")
        self.id_edit.setToolTip("The stable unique ID used to match this card and its history across computers.")
        self.root_edit.setToolTip("The last known card root. Connected card roots cannot be changed here.")
        self.action_combo.setToolTip("Copy keeps source files. Move deletes only after verification, required backups, and logs succeed.")
        self.sources_edit.setToolTip("Comma-separated media folders relative to the card root. DCIM is the usual value.")
        self.enabled_check.setToolTip("Disabled profiles are retained but ignored by discovery and monitoring.")
        self.cleanup_check.setToolTip("After a verified move, remove source directories only when they are empty.")
        self.action_combo.currentIndexChanged.connect(self._update_move_controls)
        self._update_move_controls()
        if connected:
            root_widget.setEnabled(False)
        form.addRow("Display name", self.name_edit)
        form.addRow("Stable card ID", self.id_edit)
        form.addRow("Card or drive root", root_widget)
        form.addRow("Camera name override (optional)", self.camera_edit)
        form.addRow("Source-file handling", self.action_combo)
        form.addRow("Media folders on card", self.sources_edit)
        form.addRow("Library subfolder (optional)", self.prefix_edit)
        form.addRow("", self.enabled_check)
        form.addRow("", self.cleanup_check)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_move_controls(self) -> None:
        self.cleanup_check.setEnabled(combo_value(self.action_combo) == "move")

    def accept(self) -> None:
        name = self.name_edit.text().strip()
        card_id = self.id_edit.text().strip()
        try:
            sources = safe_source_folders(self.sources_edit.text())
            if not name or not card_id:
                raise ValueError("Enter a display name and stable card ID.")
        except ValueError as exc:
            QMessageBox.critical(self, "Missing information", str(exc))
            return
        self.result_profile = {
            "id": card_id,
            "name": name,
            "last_root": self.root_edit.text().strip(),
            "action": combo_value(self.action_combo),
            "source_folders": sources,
            "destination_prefix": self.prefix_edit.text().strip("/\\"),
            "camera_name": self.camera_edit.text().strip(),
            "enabled": self.enabled_check.isChecked(),
            "delete_empty_folders_after_move": self.cleanup_check.isChecked(),
        }
        super().accept()


class LibraryDestinationDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        library: dict | None = None,
        *,
        existing_roots: tuple[Path, ...] = (),
    ):
        super().__init__(parent)
        self.original = library or {}
        self.existing_roots = existing_roots
        self.result_library: dict | None = None
        self.setWindowTitle(
            "Edit library"
            if library
            else "Set up library"
        )
        self.setMinimumWidth(680)
        layout = QVBoxLayout(self)
        title = QLabel(self.windowTitle())
        title.setObjectName("dialogTitle")
        detail = QLabel(
            "Choose where organized media will be stored. Connecting an "
            "existing folder does not scan, import, or move its files."
        )
        detail.setWordWrap(True)
        detail.setProperty("class", "muted")
        detail.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(detail)
        form = QFormLayout()
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        self.name_edit = QLineEdit(
            str(self.original.get("name", ""))
        )
        root_widget, self.root_edit = directory_editor(
            self,
            str(self.original.get("root", "")),
            title="Choose the library folder",
        )
        self.kind_combo = choice_combo(
            [
                ("On this computer", "local"),
                ("Mounted network folder", "network"),
                ("Removable or archive drive", "removable"),
            ],
            str(self.original.get("kind", "local")),
        )
        self.storage_combo = choice_combo(
            [
                ("Detect automatically", "auto"),
                ("Solid-state drive (SSD)", "ssd"),
                ("Platter drive (HDD)", "hdd"),
                ("Network storage", "network"),
            ],
            str(self.original.get("storage_profile", "auto")),
        )
        self.enabled_check = QCheckBox("Library enabled")
        self.enabled_check.setChecked(
            bool(self.original.get("enabled", True))
        )
        self.advanced_storage_check = QCheckBox(
            "Show storage options"
        )
        show_storage = (
            str(self.original.get("storage_profile", "auto"))
            != "auto"
        )
        self.advanced_storage_check.setChecked(show_storage)
        self.storage_label = QLabel("Storage profile")
        self.storage_label.setVisible(show_storage)
        self.storage_combo.setVisible(show_storage)
        self.advanced_storage_check.toggled.connect(
            self.storage_label.setVisible
        )
        self.advanced_storage_check.toggled.connect(
            self.storage_combo.setVisible
        )
        self.name_edit.setPlaceholderText("Example: Main photo library")
        self.name_edit.setToolTip(
            "A short name shown in import destination selectors."
        )
        self.root_edit.setToolTip(
            "The folder that contains organized media and the "
            ".photocard-organizer state folder. Changing this path reconnects "
            "the library; it does not move files."
        )
        self.kind_combo.setToolTip(
            "Describes how this computer reaches the library. Network "
            "authentication remains managed by the operating system."
        )
        self.storage_combo.setToolTip(
            "HDD and network profiles let the app favor sequential, "
            "lower-churn transfer behavior as those optimizations are enabled."
        )
        self.advanced_storage_check.setToolTip(
            "Show an optional storage hint. Automatic detection is appropriate "
            "for most libraries."
        )
        self.enabled_check.setToolTip(
            "Disabled libraries stay configured but cannot be selected for "
            "an import."
        )
        form.addRow("Library name", self.name_edit)
        form.addRow("Library folder", root_widget)
        form.addRow("Location type", self.kind_combo)
        form.addRow("", self.advanced_storage_check)
        form.addRow(self.storage_label, self.storage_combo)
        form.addRow("", self.enabled_check)
        layout.addLayout(form)
        layout.addStretch(1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        save_button = buttons.button(
            QDialogButtonBox.StandardButton.Save
        )
        if save_button is not None:
            save_button.setText(
                "Save changes" if library else "Add library"
            )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:
        name = self.name_edit.text().strip()
        root = self.root_edit.text().strip()
        if not name:
            QMessageBox.critical(
                self,
                "Missing name",
                "Enter a library name.",
            )
            return
        if self.enabled_check.isChecked() and not root:
            QMessageBox.critical(
                self,
                "Missing library folder",
                "Choose a folder before enabling this library.",
            )
            return
        if root:
            candidate_root = Path(root).expanduser()
            for existing_root in self.existing_roots:
                if paths_overlap(candidate_root, existing_root):
                    QMessageBox.critical(
                        self,
                        "Overlapping library",
                        "Each managed library must use a separate folder.",
                    )
                    return
        candidate = {
            "id": self.original.get("id", ""),
            "name": name,
            "root": root,
            "kind": combo_value(self.kind_combo),
            "storage_profile": combo_value(self.storage_combo),
            "enabled": self.enabled_check.isChecked(),
        }
        self.result_library = normalize_library_destination(candidate)
        super().accept()


class ReplicaDialog(QDialog):
    def __init__(self, parent: QWidget, primary_root: Path, replica: dict | None = None):
        super().__init__(parent)
        self.primary_root = primary_root.expanduser()
        self.original = replica or {}
        self.result_replica: dict | None = None
        self.setWindowTitle("Edit backup destination" if replica else "Add backup destination")
        self.setMinimumWidth(650)
        layout = QVBoxLayout(self)
        title = QLabel(self.windowTitle())
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.name_edit = QLineEdit(str(self.original.get("name", "")))
        root_widget, self.root_edit = directory_editor(
            self,
            str(self.original.get("root", "")),
            title="Choose a backup destination",
        )
        self.conflict_combo = choice_combo(
            [("Block replica", "block"), ("Archive existing and replace", "archive_and_replace")],
            str(self.original.get("conflict_policy", "block")),
        )
        self.enabled_check = QCheckBox("Destination enabled")
        self.enabled_check.setChecked(bool(self.original.get("enabled", True)))
        self.required_check = QCheckBox("Required before an import is complete")
        self.required_check.setChecked(bool(self.original.get("required", True)))
        self.history_check = QCheckBox("Save matching transfer records here")
        self.history_check.setChecked(bool(self.original.get("include_history", True)))
        self.name_edit.setToolTip("A readable label for this backup or clone destination.")
        self.root_edit.setToolTip("A destination root different from the primary library and every other backup root.")
        self.conflict_combo.setToolTip("Block preserves both locations unchanged. Archive and replace preserves the old replica file in its conflict area.")
        self.enabled_check.setToolTip("Disabled destinations remain configured but receive no files or records.")
        self.required_check.setToolTip("A required destination must verify successfully before an import completes or a move source can be deleted.")
        self.history_check.setToolTip("Write matching session and checksum records alongside this backup.")
        form.addRow("Name", self.name_edit)
        form.addRow("Destination root", root_widget)
        form.addRow("Existing-file policy", self.conflict_combo)
        form.addRow("", self.enabled_check)
        form.addRow("", self.required_check)
        form.addRow("", self.history_check)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:
        name = self.name_edit.text().strip()
        root = self.root_edit.text().strip()
        if not name:
            QMessageBox.critical(self, "Missing name", "Enter a destination name.")
            return
        if self.enabled_check.isChecked() and not root:
            QMessageBox.critical(self, "Missing destination", "Choose a root before enabling this destination.")
            return
        if root and paths_overlap(Path(root), self.primary_root):
            QMessageBox.critical(
                self,
                "Overlapping destination",
                "A backup destination must be separate from the primary library, not inside or around it.",
            )
            return
        candidate = {
            "id": self.original.get("id", ""),
            "name": name,
            "root": root,
            "enabled": self.enabled_check.isChecked(),
            "required": self.required_check.isChecked(),
            "include_history": self.history_check.isChecked(),
            "conflict_policy": combo_value(self.conflict_combo),
        }
        self.result_replica = normalize_replica_destination(candidate)
        super().accept()


class DigestInboxDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        primary_root: Path,
        inbox: dict | None = None,
    ):
        super().__init__(parent)
        self.primary_root = primary_root.expanduser()
        self.original = inbox or {}
        self.result_inbox: dict | None = None
        self.setWindowTitle(
            "Edit Digest Inbox" if inbox else "Add Digest Inbox"
        )
        self.setMinimumWidth(710)
        layout = QVBoxLayout(self)
        title = QLabel(self.windowTitle())
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.name_edit = QLineEdit(str(self.original.get("name", "")))
        root_widget, self.root_edit = directory_editor(
            self,
            str(self.original.get("root", "")),
            title="Choose the folder to digest",
        )
        self.prefix_edit = QLineEdit(
            str(self.original.get("destination_prefix", ""))
        )
        self.camera_edit = QLineEdit(str(self.original.get("camera_name", "")))
        self.action_combo = choice_combo(
            [
                ("Copy and leave source files", "copy"),
                ("Move after verified digestion", "move"),
            ],
            str(self.original.get("action", "copy")),
        )
        self.recursive_check = QCheckBox("Include files in subfolders")
        self.recursive_check.setChecked(
            bool(self.original.get("include_subfolders", True))
        )
        self.auto_digest_check = QCheckBox(
            "Monitor and digest new files automatically"
        )
        self.auto_digest_check.setChecked(
            bool(self.original.get("auto_digest", False))
        )
        self.poll_spin = QDoubleSpinBox()
        self.poll_spin.setRange(10, 86400)
        self.poll_spin.setDecimals(0)
        self.poll_spin.setSuffix(" sec")
        self.poll_spin.setValue(float(self.original.get("poll_seconds", 60)))
        self.enabled_check = QCheckBox("Digest Inbox enabled")
        self.enabled_check.setChecked(bool(self.original.get("enabled", True)))

        self.name_edit.setToolTip(
            "A stable readable name for this watched or manually digested folder."
        )
        self.root_edit.setToolTip(
            "A local folder, removable drive folder, network share, or synchronized cloud folder containing incoming media."
        )
        self.prefix_edit.setToolTip(LIBRARY_SUBFOLDER_HELP)
        self.camera_edit.setToolTip(CAMERA_NAME_HELP)
        self.action_combo.setToolTip(
            "Copy preserves incoming files. Move removes each source only after all required copies, checksums, and transfer records succeed."
        )
        self.recursive_check.setToolTip(
            "Scan supported media in every subfolder while ignoring Photo Card Organizer state."
        )
        self.auto_digest_check.setToolTip(
            "Poll this folder in the background. Automatic digestion is copy-only so synchronized or shared sources are never deleted."
        )
        self.poll_spin.setToolTip(
            "How often the background monitor checks this inbox for new or changed files."
        )
        self.enabled_check.setToolTip(
            "Disabled inboxes remain configured but cannot be digested."
        )

        form.addRow("Digest Inbox name", self.name_edit)
        form.addRow("Incoming media folder", root_widget)
        form.addRow("Master-library subfolder (optional)", self.prefix_edit)
        form.addRow("Camera name override (optional)", self.camera_edit)
        form.addRow("Source-file handling", self.action_combo)
        form.addRow("", self.recursive_check)
        form.addRow("", self.auto_digest_check)
        form.addRow("Automatic check interval", self.poll_spin)
        form.addRow("", self.enabled_check)
        layout.addLayout(form)

        self.safety_label = QLabel()
        self.safety_label.setWordWrap(True)
        layout.addWidget(self.safety_label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.action_combo.currentIndexChanged.connect(self._update_action)
        self.auto_digest_check.toggled.connect(self._update_action)
        self._update_action()

    def _update_action(self) -> None:
        move = combo_value(self.action_combo) == "move"
        if move:
            self.auto_digest_check.setChecked(False)
        self.auto_digest_check.setEnabled(not move)
        self.poll_spin.setEnabled(
            not move and self.auto_digest_check.isChecked()
        )
        if move:
            self.safety_label.setText(
                "Verified move is manual only. Each source remains in place unless the primary destination, required backups, checksum verification, and transfer records all succeed."
            )
            self.safety_label.setProperty("class", "warning")
        else:
            self.safety_label.setText(
                "Copy leaves the inbox unchanged. Retained digest history skips unchanged files during later scans."
            )
            self.safety_label.setProperty("class", "success")
        self.safety_label.style().unpolish(self.safety_label)
        self.safety_label.style().polish(self.safety_label)

    def accept(self) -> None:
        name = self.name_edit.text().strip()
        root = self.root_edit.text().strip()
        if not name:
            QMessageBox.critical(
                self, "Missing name", "Enter a Digest Inbox name."
            )
            return
        if self.enabled_check.isChecked() and not root:
            QMessageBox.critical(
                self,
                "Missing incoming folder",
                "Choose an incoming media folder before enabling this Digest Inbox.",
            )
            return
        if root and paths_overlap(Path(root), self.primary_root):
            QMessageBox.critical(
                self,
                "Overlapping folders",
                "The Digest Inbox must be separate from the managed master library.",
            )
            return
        candidate = {
            "id": self.original.get("id", ""),
            "name": name,
            "root": root,
            "enabled": self.enabled_check.isChecked(),
            "include_subfolders": self.recursive_check.isChecked(),
            "destination_prefix": self.prefix_edit.text().strip("/\\"),
            "camera_name": self.camera_edit.text().strip(),
            "action": combo_value(self.action_combo),
            "auto_digest": self.auto_digest_check.isChecked(),
            "poll_seconds": self.poll_spin.value(),
        }
        self.result_inbox = normalize_digest_inbox(candidate)
        super().accept()


class TravelLibraryDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        primary_root: Path,
        library: dict | None = None,
    ):
        super().__init__(parent)
        self.primary_root = primary_root.expanduser()
        self.original = library or {}
        self.result_library: dict | None = None
        self.setWindowTitle(
            "Edit travel library" if library else "Add travel library"
        )
        self.setMinimumWidth(690)
        layout = QVBoxLayout(self)
        title = QLabel(self.windowTitle())
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        explanation = QLabel(
            "Choose the library created on the laptop while travelling. On return, "
            "Photo Card Organizer copies only new media into this computer's master library."
        )
        explanation.setWordWrap(True)
        explanation.setProperty("class", "muted")
        layout.addWidget(explanation)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.name_edit = QLineEdit(str(self.original.get("name", "")))
        root_widget, self.root_edit = directory_editor(
            self,
            str(self.original.get("root", "")),
            title="Choose the laptop library, network share, or mounted travel drive",
        )
        self.prefix_edit = QLineEdit(
            str(self.original.get("destination_prefix", ""))
        )
        self.camera_edit = QLineEdit(str(self.original.get("camera_name", "")))
        self.enabled_check = QCheckBox("Travel source enabled")
        self.enabled_check.setChecked(bool(self.original.get("enabled", True)))
        self.recursive_check = QCheckBox("Include media in subfolders")
        self.recursive_check.setChecked(
            bool(self.original.get("include_subfolders", True))
        )
        self.name_edit.setToolTip(
            "A stable readable name for the laptop or travel library."
        )
        self.root_edit.setToolTip(
            "A local folder, mapped drive, UNC share such as \\\\LAPTOP\\Photos, "
            "or Linux mount. The path may be saved while the laptop is offline."
        )
        self.prefix_edit.setToolTip(LIBRARY_SUBFOLDER_HELP)
        self.camera_edit.setToolTip(CAMERA_NAME_HELP)
        self.enabled_check.setToolTip(
            "Disabled travel sources remain configured but cannot be selected for sync."
        )
        self.recursive_check.setToolTip(
            "Scan every subfolder in the travel library while ignoring its internal manifest."
        )
        form.addRow("Travel library name", self.name_edit)
        form.addRow("Laptop library path", root_widget)
        form.addRow("Master-library subfolder (optional)", self.prefix_edit)
        form.addRow("Camera name override (optional)", self.camera_edit)
        form.addRow("", self.recursive_check)
        form.addRow("", self.enabled_check)
        layout.addLayout(form)
        safety = QLabel(
            "Travel sync is copy-only. It never propagates deletions and never removes "
            "files from the laptop."
        )
        safety.setWordWrap(True)
        safety.setProperty("class", "success")
        layout.addWidget(safety)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:
        name = self.name_edit.text().strip()
        root = self.root_edit.text().strip()
        if not name:
            QMessageBox.critical(
                self, "Missing name", "Enter a travel library name."
            )
            return
        if self.enabled_check.isChecked() and not root:
            QMessageBox.critical(
                self,
                "Missing source",
                "Enter a laptop library path before enabling this travel source.",
            )
            return
        if root and paths_overlap(Path(root), self.primary_root):
            QMessageBox.critical(
                self,
                "Overlapping libraries",
                "The travel library must be separate from the desktop master library.",
            )
            return
        candidate = {
            "id": self.original.get("id", ""),
            "name": name,
            "root": root,
            "enabled": self.enabled_check.isChecked(),
            "include_subfolders": self.recursive_check.isChecked(),
            "destination_prefix": self.prefix_edit.text().strip("/\\"),
            "camera_name": self.camera_edit.text().strip(),
        }
        self.result_library = normalize_travel_library(candidate)
        super().accept()


class TransferHubDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        primary_root: Path,
        hub: dict | None = None,
    ):
        super().__init__(parent)
        self.primary_root = primary_root.expanduser()
        self.original = hub or {}
        self.result_hub: dict | None = None
        self.setWindowTitle(
            "Edit shared transfer hub" if hub else "Add shared transfer hub"
        )
        self.setMinimumWidth(720)
        layout = QVBoxLayout(self)
        title = QLabel(self.windowTitle())
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        description = QLabel(
            "A hub may be a removable USB drive, SMB/NAS folder, Google Drive for "
            "desktop folder, or another mounted location. Choose one role on this "
            "client to avoid loops."
        )
        description.setWordWrap(True)
        description.setProperty("class", "muted")
        layout.addWidget(description)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.name_edit = QLineEdit(str(self.original.get("name", "")))
        root_widget, self.root_edit = directory_editor(
            self,
            str(self.original.get("root", "")),
            title="Choose the shared transfer hub folder",
        )
        self.role_combo = choice_combo(
            [
                ("Publish this client's library", "publish"),
                ("Catch producer channels into this library", "catch"),
            ],
            str(self.original.get("role", "catch")),
        )
        self.channel_edit = QLineEdit(
            str(self.original.get("producer_channel", ""))
        )
        self.required_check = QCheckBox(
            "Require this hub during new card imports"
        )
        self.required_check.setChecked(bool(self.original.get("required", False)))
        self.auto_catch_check = QCheckBox(
            "Monitor and catch new committed files automatically"
        )
        self.auto_catch_check.setChecked(
            bool(self.original.get("auto_catch", False))
        )
        self.poll_spin = QDoubleSpinBox()
        self.poll_spin.setRange(10, 86400)
        self.poll_spin.setDecimals(0)
        self.poll_spin.setSuffix(" sec")
        self.poll_spin.setValue(float(self.original.get("poll_seconds", 60)))
        self.receipts_check = QCheckBox(
            "Write digestion receipts back to the hub"
        )
        self.receipts_check.setChecked(
            bool(self.original.get("write_receipts", True))
        )
        self.conflict_combo = choice_combo(
            [
                ("Block changed hub path", "block"),
                ("Archive old hub file and replace", "archive_and_replace"),
            ],
            str(self.original.get("conflict_policy", "block")),
        )
        self.enabled_check = QCheckBox("Hub enabled on this client")
        self.enabled_check.setChecked(bool(self.original.get("enabled", True)))
        self.name_edit.setToolTip(
            "A readable name shared in this client's status and transfer records."
        )
        self.root_edit.setToolTip(
            "The locally visible hub folder, such as E:\\PhotoTransferHub, "
            "\\\\NAS\\PhotoTransferHub, or a Google Drive synchronized folder."
        )
        self.role_combo.setToolTip(
            "Publish is for travel producers. Catch is for a desktop or other consumer library. One role prevents circular copies."
        )
        self.channel_edit.setToolTip(
            "Optional producer folder name. Leave blank to derive a stable name from this client."
        )
        self.required_check.setToolTip(
            "When enabled for a publisher, card imports remain pending unless their hub copy verifies."
        )
        self.auto_catch_check.setToolTip(
            "Poll catch-role producer channels with the background monitor. Source files are never deleted."
        )
        self.poll_spin.setToolTip(
            "How often this catch client checks the synchronized hub folder. Cloud folders usually suit 60 seconds or longer."
        )
        self.receipts_check.setToolTip(
            "After the local manifest confirms ingestion, write a receipt that syncs back to producers."
        )
        self.conflict_combo.setToolTip(
            "Publishing never silently overwrites different content in a producer channel."
        )
        form.addRow("Hub name", self.name_edit)
        form.addRow("USB, shared, or synchronized folder", root_widget)
        form.addRow("This client's role", self.role_combo)
        form.addRow("Producer channel name (optional)", self.channel_edit)
        form.addRow("Changed publication path", self.conflict_combo)
        form.addRow("", self.required_check)
        form.addRow("", self.auto_catch_check)
        form.addRow("Automatic catch interval", self.poll_spin)
        form.addRow("", self.receipts_check)
        form.addRow("", self.enabled_check)
        layout.addLayout(form)
        safety = QLabel(
            "Catch is copy-only. Receipts confirm ingestion but never instruct another "
            "client to delete media."
        )
        safety.setWordWrap(True)
        safety.setProperty("class", "success")
        layout.addWidget(safety)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.role_combo.currentIndexChanged.connect(self._update_role)
        self._update_role()

    def _update_role(self) -> None:
        publishing = combo_value(self.role_combo) == "publish"
        self.channel_edit.setEnabled(publishing)
        self.required_check.setEnabled(publishing)
        self.conflict_combo.setEnabled(publishing)
        self.auto_catch_check.setEnabled(not publishing)
        self.poll_spin.setEnabled(not publishing)
        self.receipts_check.setEnabled(not publishing)

    def accept(self) -> None:
        name = self.name_edit.text().strip()
        root = self.root_edit.text().strip()
        if not name:
            QMessageBox.critical(self, "Missing name", "Enter a hub name.")
            return
        if self.enabled_check.isChecked() and not root:
            QMessageBox.critical(
                self,
                "Missing hub folder",
                "Choose or enter the shared hub folder before enabling it.",
            )
            return
        if root and paths_overlap(Path(root), self.primary_root):
            QMessageBox.critical(
                self,
                "Overlapping hub",
                "The transfer hub must be separate from the managed local library.",
            )
            return
        role = combo_value(self.role_combo)
        candidate = {
            "id": self.original.get("id", ""),
            "name": name,
            "root": root,
            "enabled": self.enabled_check.isChecked(),
            "role": role,
            "producer_channel": (
                self.channel_edit.text().strip() if role == "publish" else ""
            ),
            "required": (
                self.required_check.isChecked() if role == "publish" else False
            ),
            "auto_catch": (
                self.auto_catch_check.isChecked() if role == "catch" else False
            ),
            "poll_seconds": self.poll_spin.value(),
            "write_receipts": (
                self.receipts_check.isChecked() if role == "catch" else True
            ),
            "conflict_policy": combo_value(self.conflict_combo),
        }
        self.result_hub = normalize_transfer_hub(candidate)
        super().accept()


class StructureMappingDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        analysis: StructureAnalysis,
        current_rules: dict[str, list[str]],
    ):
        super().__init__(parent)
        self.analysis = analysis
        self.current_rules = copy.deepcopy(current_rules)
        self.result_rules: dict[str, list[str]] | None = None
        self.level_combos: dict[str, list[QComboBox]] = {}
        self.setWindowTitle("Detected library organization")
        self.setMinimumSize(780, 610)
        layout = QVBoxLayout(self)
        title = QLabel("Detected library organization")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        summary = QLabel(
            f"Analysis preview: {analysis.matched_files} supported media files among "
            f"{analysis.scanned_files} filesystem files in {analysis.scanned_folders} "
            "folders. Edit the proposed destination mapping before applying it. "
            "The eventual import scans the complete selected scope."
        )
        summary.setWordWrap(True)
        summary.setProperty("class", "muted")
        layout.addWidget(summary)
        self.preview_limit_warning: QLabel | None = None
        self.depth_limit_warning: QLabel | None = None
        if analysis.truncated:
            self.preview_limit_warning = QLabel(
                f"The analysis preview reached its {analysis.preview_file_limit:,}-file "
                "limit. The import is not limited: it will still scan every enabled media "
                "file in the selected scope. Because later folders may differ, review the "
                "proposed mapping before applying it."
            )
            self.preview_limit_warning.setWordWrap(True)
            self.preview_limit_warning.setProperty("class", "warning")
            layout.addWidget(self.preview_limit_warning)
        if analysis.deepest_level > analysis.editable_levels:
            self.depth_limit_warning = QLabel(
                f"Supported media reaches {analysis.deepest_level} source-folder levels. "
                f"The first {analysis.editable_levels} levels can be retained individually "
                "below; deeper source names are not retained unless represented by a "
                "metadata-based destination rule."
            )
            self.depth_limit_warning.setWordWrap(True)
            self.depth_limit_warning.setProperty("class", "warning")
            layout.addWidget(self.depth_limit_warning)

        tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        for kind, media_label in MEDIA_LABELS.items():
            rule = analysis.rules.get(kind)
            tab = QWidget()
            tab_layout = QVBoxLayout(tab)
            count = rule.file_count if rule else 0
            count_label = QLabel(f"{count} matching {media_label.lower()} files")
            count_label.setProperty("class", "muted")
            tab_layout.addWidget(count_label)
            levels_layout = QVBoxLayout()
            levels_layout.setContentsMargins(0, 0, 0, 0)
            levels_layout.setSpacing(10)
            combos: list[QComboBox] = []
            detected_by_index = {level.index: level for level in rule.levels} if rule else {}
            level_count = max(
                4,
                len(rule.levels) if rule else 0,
                len(self.current_rules.get(kind, [])),
            )
            for index in range(level_count):
                level = detected_by_index.get(index)
                combo = QComboBox()
                combo.setEditable(True)
                combo.addItem("None", "")
                combo.addItem(f"Preserve source folder {index + 1}", f"{{source_dir:{index + 1}}}")
                combo.addItem("Media type", "{media}")
                combo.addItem("Year", "{date:%Y}")
                combo.addItem("Year and month", "{date:%Y-%m}")
                combo.addItem("Shoot date", "{date:%Y-%m-%d}")
                combo.addItem("Camera", "{camera}")
                combo.addItem("Location", "{location}")
                combo.addItem("Rating", "{rating}")
                combo.addItem("Card name", "{card}")
                combo.setToolTip(
                    "Choose how this source-folder level contributes to the destination. "
                    "Preserve keeps that folder name, a metadata rule replaces it, and None "
                    "omits only this destination level without excluding any files."
                )
                if level:
                    default = level.token
                elif rule and rule.file_count:
                    default = ""
                else:
                    default = (
                        self.current_rules.get(kind, [])[index]
                        if index < len(self.current_rules.get(kind, []))
                        else ""
                    )
                set_combo_data(combo, default)
                if combo.currentIndex() < 0 and default:
                    combo.setEditText(default)
                combos.append(combo)
                if level and level.examples:
                    examples = ", ".join(level.examples[:3])
                    label_text = (
                        f"Level {index + 1}  |  {level.label}  |  "
                        f"{level.confidence * 100:.0f}% confidence\nExamples: {examples}"
                    )
                else:
                    label_text = f"Level {index + 1}"
                level_container = QWidget()
                level_layout = QVBoxLayout(level_container)
                level_layout.setContentsMargins(0, 0, 0, 0)
                level_layout.setSpacing(4)
                level_label = QLabel(label_text)
                level_label.setWordWrap(True)
                level_label.setProperty("class", "muted")
                level_layout.addWidget(level_label)
                level_layout.addWidget(combo)
                levels_layout.addWidget(level_container)
            self.level_combos[kind] = combos
            tab_layout.addLayout(levels_layout)
            if rule and rule.sample_paths:
                samples = QLabel("Example source paths:\n" + "\n".join(rule.sample_paths[:4]))
                samples.setWordWrap(True)
                samples.setProperty("class", "muted")
                tab_layout.addWidget(samples)
            tab_layout.addStretch(1)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            scroll.setWidget(tab)
            tabs.addTab(scroll, media_label)

        buttons = QDialogButtonBox()
        apply_button = buttons.addButton("Apply detected rules", QDialogButtonBox.ButtonRole.AcceptRole)
        apply_button.setProperty("accent", True)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:
        result: dict[str, list[str]] = {}
        for kind, combos in self.level_combos.items():
            segments = []
            for combo in combos:
                value = combo_value(combo).strip()
                if value:
                    segments.append(value)
            result[kind] = segments
        self.result_rules = result
        super().accept()


class DecisionDialog(QDialog):
    def __init__(self, parent: QWidget, request: DecisionRequest):
        super().__init__(parent)
        self.request = request
        self.result_decision = DecisionResult(request.default_action)
        self.setWindowTitle(request.title)
        self.setMinimumWidth(610)
        layout = QVBoxLayout(self)
        title = QLabel(request.title)
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        message = QLabel(request.message)
        message.setWordWrap(True)
        layout.addWidget(message)
        if request.source:
            source = QLabel(f"Source: {request.source}")
            source.setWordWrap(True)
            source.setProperty("class", "muted")
            layout.addWidget(source)
        if request.destination:
            destination = QLabel(f"Destination: {request.destination}")
            destination.setWordWrap(True)
            destination.setProperty("class", "muted")
            layout.addWidget(destination)
        layout.addSpacing(8)
        for action, label in request.options:
            button = QPushButton(label)
            if action == request.default_action:
                button.setProperty("accent", True)
            button.clicked.connect(lambda _checked=False, value=action: self._choose(value))
            layout.addWidget(button)
        self.apply_check = QCheckBox("Use this choice for the rest of this import")
        self.apply_check.setVisible(request.allow_apply_to_session)
        layout.addWidget(self.apply_check)
        self.persist_check = QCheckBox("Make an alternate destination the new default")
        self.persist_check.setVisible(request.allow_choose_destination)
        layout.addWidget(self.persist_check)

    def _choose(self, action: str) -> None:
        value = ""
        if action == "choose_destination":
            value = QFileDialog.getExistingDirectory(self, "Choose an alternate destination")
            if not value:
                return
        self.result_decision = DecisionResult(
            action,
            value=value,
            apply_to_session=self.apply_check.isChecked(),
            persist=self.persist_check.isChecked() if action == "choose_destination" else False,
        )
        super().accept()


class InstallationDialog(QDialog):
    def __init__(self, parent: QWidget, project_root: Path):
        super().__init__(parent)
        self.selected_action = ""
        self.selected_target: Path | None = None
        self.project_root = project_root
        self.installation_info: InstallationInfo = detect_installation(project_root)
        self.setWindowTitle("Installation and maintenance")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        title = QLabel("Installation and maintenance")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        state = QLabel(self.installation_info.status)
        state.setProperty("class", "success" if self.installation_info.verified else "warning")
        layout.addWidget(state)
        detail = QLabel(self.installation_info.detail)
        detail.setWordWrap(True)
        detail.setProperty("class", "muted")
        layout.addWidget(detail)
        layout.addSpacing(14)
        buttons = QHBoxLayout()
        if self.installation_info.install_target is not None:
            action_label = (
                "Update or repair" if self.installation_info.verified else
                "Repair installation" if (project_root / ".venv").exists() else
                "Install"
            )
            install = QPushButton(action_label)
            install.setProperty("accent", True)
            install.setToolTip("Create, update, or repair the managed environment, then verify it.")
            install.clicked.connect(
                lambda: self._select("install", self.installation_info.install_target)
            )
            buttons.addWidget(install)
        uninstall = QPushButton("Uninstall")
        uninstall.setToolTip(
            "Launch the uninstaller. Application files are removed; user data is preserved unless explicitly selected."
        )
        uninstall.setEnabled(self.installation_info.uninstall_target is not None)
        uninstall.clicked.connect(
            lambda: self._select("uninstall", self.installation_info.uninstall_target)
        )
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        buttons.addWidget(uninstall)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def _select(self, action: str, target: Path | None) -> None:
        if target is None:
            return
        self.selected_action = action
        self.selected_target = target
        self.accept()
