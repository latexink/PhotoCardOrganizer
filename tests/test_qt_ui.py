from __future__ import annotations

import copy
import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QItemSelectionModel, Qt
from PySide6.QtGui import QFontDatabase, QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QMessageBox,
    QScrollArea,
    QTabWidget,
)

from photocard.config import normalize_config, normalize_digest_inbox
from photocard.discovery import folder_import_source, write_card_identity
from photocard.models import ActivityEvent
from photocard.library_tools import CaptureGroup, LibraryItem, build_capture_sets
from photocard.qt_library_tools import LibraryJobDialog, ReorganizationDialog
from photocard.reorganization import build_reorganization_plan
from photocard.qt_dialogs import (
    CardOnboardingWizard,
    DigestInboxDialog,
    LibraryDestinationDialog,
    StructureMappingDialog,
)
from photocard.qt_common import initial_import_summary
from photocard.qt_theme import application_icon, configure_application
from photocard.qt_window import (
    NAVIGATION_SECTIONS,
    PAGE_NAMES,
    PhotoCardApp,
    directory_available,
)
from photocard.structure_detection import detect_existing_structure


class QtWorkflowTests(unittest.TestCase):
    def test_library_organization_dialog_retains_custom_fields(self):
        from photocard.qt_dialogs import LibraryOrganizationDialog
        overrides = {"video": {"folder_segments": ["Client clips", "{date:%Y}"], "filename_template": "clip_{original}"}}
        dialog = LibraryOrganizationDialog(self.window, overrides, self.window.config["media_rules"])
        self.assertEqual(dialog.values(), overrides)
        dialog.controls["video"][0].setChecked(False)
        self.assertEqual(dialog.values(), {})
        dialog.deleteLater()

    def test_integrity_library_change_clears_stale_recovery_selection(self):
        panel = self.window.integrity_panel
        panel.model.replace_rows([(str(self.base / "image.jpg"), "Missing")])
        panel.table.selectRow(0)
        self.assertTrue(panel.restore.isEnabled())
        panel.clear_selection()
        self.assertFalse(panel.restore.isEnabled())
        self.assertEqual(panel.model.rows, [])

    def test_whole_library_button_starts_verification_and_clears_filter(self):
        panel = self.window.integrity_panel
        panel.selected_paths = [self.base / "selected.jpg"]
        with patch.object(panel, "start") as start:
            panel.all_files.click()
        self.assertIsNone(panel.selected_paths)
        self.assertEqual(panel.scope.text(), "Whole library")
        start.assert_called_once_with(False)

    def test_reorganization_can_proceed_without_detailed_preview(self):
        root = Path(self.window.config["destination_root"])
        (root / "old").mkdir(parents=True, exist_ok=True)
        source = root / "old/video.mp4"
        source.write_bytes(b"fixture")
        dialog = ReorganizationDialog(self.window, self.window.config)
        self.assertTrue(dialog.process.isEnabled())
        dialog.request_process()
        deadline = time.monotonic() + 10
        while dialog._running and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertFalse(dialog._running)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted, dialog.status.text())
        self.assertIsNotNone(dialog.plan)
        self.assertTrue(source.exists(), "The final parent confirmation must precede file changes")
        self.assertFalse((root / "Videos/video.mp4").exists())
        dialog.deleteLater()

    def test_integrity_workflow_pauses_monitor_and_produces_results(self):
        from photocard.integrity import IntegrityCatalog, checksum
        root = Path(self.window.config["destination_root"])
        root.mkdir(parents=True, exist_ok=True)
        source = root / "image.jpg"
        source.write_bytes(b"fixture")
        catalog = IntegrityCatalog(root, self.base / "local")
        catalog.record(source, checksum(source), "sha256", verified=True)
        self.window.show_page("Integrity")
        panel = self.window.integrity_panel
        was_paused = self.window.monitor.is_paused
        with patch("photocard.integrity.local_state_directory", return_value=self.base / "local"), patch.object(
            self.window, "_saved_processing_config", return_value=self.window.config
        ):
            panel.start(False)
            self.assertTrue(self.window.monitor.is_paused)
            self.assertTrue(self.window._manual_import_running)
            deadline = time.monotonic() + 10
            while panel.running and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
            self.assertFalse(panel.running)
        self.assertFalse(self.window._manual_import_running)
        self.assertEqual(self.window.monitor.is_paused, was_paused)
        self.assertEqual(panel.model.rows, [(str(source), "Verified")])
        self.assertTrue(panel.report.is_file())
        self.assertTrue(panel.open_report.isEnabled())

    def test_library_job_preview_and_processing_complete(self):
        from photocard.library_jobs import execute_library_job
        source = self.base / "incoming"
        source.mkdir()
        (source / "photo.jpg").write_bytes(b"media")
        dialog = LibraryJobDialog(self.window, self.window.config, [source])
        dialog.start_preview()
        deadline = time.monotonic() + 10
        while dialog._running and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertFalse(dialog._running)
        self.assertIsNotNone(dialog.plan, dialog.status.text())
        self.assertTrue(dialog.process.isEnabled())
        with patch("photocard.qt_library_tools.QMessageBox.warning", return_value=QMessageBox.StandardButton.Yes), patch(
            "photocard.qt_library_tools.execute_library_job",
            side_effect=lambda plan, **kwargs: execute_library_job(plan, local_root=self.base / "local", **kwargs),
        ):
            dialog.confirm_process()
            deadline = time.monotonic() + 10
            while dialog._running and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
            self.assertFalse(dialog._running)
            self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted, dialog.status.text())
        dialog.deleteLater()

    def test_library_job_cancel_waits_for_worker(self):
        import threading
        from photocard.library_jobs import build_library_job
        gate = threading.Event()
        source = self.base / "incoming"
        source.mkdir()
        dialog = LibraryJobDialog(self.window, self.window.config, [source])
        def delayed(*args, **kwargs):
            gate.wait(5)
            return build_library_job(*args, **kwargs)
        with patch("photocard.qt_library_tools.build_library_job", side_effect=delayed):
            dialog.start_preview()
            dialog.reject()
            self.assertTrue(dialog._running)
            self.assertTrue(dialog._cancel.is_set())
            gate.set()
            deadline = time.monotonic() + 10
            while dialog._running and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
            self.assertFalse(dialog._running)
        dialog.deleteLater()

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        if os.name == "nt":
            QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
        configure_application(cls.app)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.cards = [self.base / "CARD_A", self.base / "CARD_B"]
        config = normalize_config(
            {
                "destination_root": str(self.base / "library"),
                "identification": {
                    "auto_detect": False,
                    "configured_roots": [str(card) for card in self.cards],
                },
                "monitor": {"poll_seconds": 3600},
                "safety": {
                    "minimum_destination_free_percent": 0,
                    "minimum_destination_free_gb": 0,
                },
            }
        )
        identification = config["identification"]
        for index, card in enumerate(self.cards, 1):
            card.mkdir()
            (card / "DCIM").mkdir()
            write_card_identity(
                card,
                identification["folder_name"],
                identification["identity_filename"],
                identification["history_folder_name"],
                f"Camera Card {index}",
                f"camera-card-{index}",
            )
        self.window = PhotoCardApp(config, self.base / "config.json")
        self.app.processEvents()

    def tearDown(self) -> None:
        self.window.shutdown()
        self.window.hide()
        self.window.deleteLater()
        self.app.processEvents()
        self.temporary.cleanup()

    def test_progressive_navigation_and_tooltips_are_present(self) -> None:
        self.assertEqual(len(PAGE_NAMES), self.window.stack.count())
        self.assertEqual(
            set(PAGE_NAMES),
            {
                page_name
                for _section_name, pages in NAVIGATION_SECTIONS
                for page_name in pages
            },
        )
        self.assertEqual(
            len(PAGE_NAMES) + len(NAVIGATION_SECTIONS),
            self.window.navigation.count(),
        )
        self.assertEqual("WORKSPACE", self.window.navigation.item(0).text())
        self.assertFalse(self.window.navigation.item(0).flags())
        self.window.show_page("Library export")
        self.assertEqual(
            "Library export",
            self.window.navigation.currentItem().data(Qt.ItemDataRole.UserRole),
        )
        self.assertIn(
            'QPushButton[accent="true"]:disabled',
            self.app.styleSheet(),
        )
        self.assertEqual(
            QAbstractItemView.SelectionMode.ExtendedSelection,
            self.window.dashboard_table.selectionMode(),
        )
        self.assertEqual(
            QAbstractItemView.SelectionMode.ExtendedSelection,
            self.window.travel_table.selectionMode(),
        )
        self.assertEqual(
            QAbstractItemView.SelectionMode.ExtendedSelection,
            self.window.hub_table.selectionMode(),
        )
        self.assertEqual(
            QAbstractItemView.SelectionMode.ExtendedSelection,
            self.window.digest_table.selectionMode(),
        )
        self.assertEqual(
            QAbstractItemView.SelectionMode.ExtendedSelection,
            self.window.conflict_table.selectionMode(),
        )
        self.assertIn("Digest inboxes", PAGE_NAMES)
        self.assertIn("Travel sync", PAGE_NAMES)
        self.assertIn("Library export", PAGE_NAMES)
        self.assertIn("Libraries", PAGE_NAMES)
        self.assertIn("Import or merge", PAGE_NAMES)
        self.assertEqual("Help & about", PAGE_NAMES[-1])
        for widget in (
            self.window.folder_name_edit,
            self.window.identity_filename_edit,
            self.window.existing_source_edit,
            self.window.existing_action_combo,
            self.window.existing_analyze_button,
            self.window.existing_next_button,
            self.window.copy_verification_combo,
            self.window.move_checksum_combo,
            self.window.conflict_policy_label,
            self.window.minimum_percent_spin,
            self.window.replica_verification_combo,
            self.window.bracket_seconds_spin,
            self.window.interval_seconds_spin,
            self.window.scan_export_library_button,
            self.window.run_digest_button,
            self.window.digest_status_filter,
            self.window.conflict_search_edit,
            self.window.open_user_guide_button,
            self.window.open_changelog_button,
        ):
            self.assertTrue(widget.toolTip().strip(), widget.objectName() or type(widget).__name__)

    def test_save_button_highlights_only_for_unsaved_settings(self) -> None:
        self.assertFalse(bool(self.window.save_button.property("accent")))
        self.assertFalse(self.window._settings_dirty)

        self.window.poll_spin.setValue(
            self.window.poll_spin.value() - 1
        )
        self.app.processEvents()

        self.assertTrue(bool(self.window.save_button.property("accent")))
        self.assertTrue(self.window._settings_dirty)
        self.window.save_settings()
        self.app.processEvents()

        self.assertFalse(bool(self.window.save_button.property("accent")))
        self.assertFalse(self.window._settings_dirty)
        self.assertTrue((self.base / "config.json").is_file())

    def test_processing_requires_saved_settings(self) -> None:
        self.window.poll_spin.setValue(
            self.window.poll_spin.value() - 1
        )
        self.app.processEvents()

        with patch.object(
            QMessageBox,
            "information",
            return_value=QMessageBox.StandardButton.Ok,
        ) as information:
            candidate = self.window._saved_processing_config(
                "testing imports"
            )

        self.assertIsNone(candidate)
        self.assertIn(
            "Save Settings is highlighted",
            information.call_args.args[2],
        )

    def test_workflow_controls_are_explicit(self) -> None:
        self.assertEqual(
            "Save settings + Import",
            self.window.existing_review_button.text(),
        )
        self.assertTrue(self.window.destination_edit.isReadOnly())
        self.assertGreaterEqual(
            self.window.transfer_progress.width(),
            160,
        )
        self.assertTrue(self.window.transfer_progress.isTextVisible())
        self.assertTrue(self.window.bracket_enabled_check.toolTip())
        self.assertTrue(self.window.bracket_folder_name_edit.toolTip())
        organization_page = self.window.stack.widget(
            self.window.page_indexes["Organization"]
        )
        organization_tabs = organization_page.findChild(QTabWidget)
        self.assertIsNotNone(organization_tabs)
        self.assertIn(
            "Capture grouping",
            [
                organization_tabs.tabText(index)
                for index in range(organization_tabs.count())
            ],
        )
        self.assertIn(
            "QComboBox::down-arrow",
            self.app.styleSheet(),
        )

    def test_inaccessible_network_directory_is_reported_offline(self) -> None:
        with patch(
            "photocard.qt_window.Path.is_dir",
            side_effect=PermissionError("network access denied"),
        ):
            self.assertFalse(directory_available(r"\\OFFLINE\Photo Hub"))

    def test_initial_import_summary_lists_publish_hubs(self) -> None:
        candidate = copy.deepcopy(self.window.config)
        candidate["transfer_hubs"] = [
            {
                "id": "travel-hub",
                "name": "Travel Hub",
                "root": str(self.base / "hub"),
                "role": "publish",
                "producer_channel": "laptop",
                "enabled": True,
            }
        ]
        candidate = normalize_config(candidate)
        card = next(iter(self.window.card_by_id.values()))

        summary = initial_import_summary(card, candidate, "Date then camera")

        self.assertIn("Backups and clones: Hub: Travel Hub", summary)
        self.assertIn("Verification: File-size check; keep source files", summary)

    def test_folder_import_summary_uses_readable_scan_scope(self) -> None:
        source = self.base / "legacy-library"
        source.mkdir()
        card = folder_import_source(
            source,
            name="Legacy library",
            include_subfolders=False,
        )

        summary = initial_import_summary(
            card, self.window.config, "Use current detailed rules"
        )

        self.assertIn("Scan scope: Selected folder only", summary)
        self.assertNotIn("Source folders: .", summary)

    def test_multiple_connected_cards_can_form_one_queue_selection(self) -> None:
        self.assertFalse(self.window.import_button.isEnabled())
        selection = self.window.dashboard_table.selectionModel()
        self.assertIsNotNone(selection)
        for row in range(self.window.dashboard_table.rowCount()):
            index = self.window.dashboard_table.model().index(row, 0)
            selection.select(
                index,
                QItemSelectionModel.SelectionFlag.Select
                | QItemSelectionModel.SelectionFlag.Rows,
            )

        selected = self.window._selected_cards()

        self.assertEqual(2, len(selected))
        self.assertTrue(self.window.import_button.isEnabled())
        self.assertEqual("Import 2 selected cards", self.window.import_button.text())
        self.assertFalse(self.window.edit_card_button.isEnabled())
        review = self.window._batch_review_text(selected, self.window.config)
        self.assertIn("Sources (2)", review)
        self.assertIn("one separate transfer session per card", review)

    def test_multi_card_selection_survives_refresh(self) -> None:
        selection = self.window.dashboard_table.selectionModel()
        self.assertIsNotNone(selection)
        for row in range(self.window.dashboard_table.rowCount()):
            selection.select(
                self.window.dashboard_table.model().index(row, 0),
                QItemSelectionModel.SelectionFlag.Select
                | QItemSelectionModel.SelectionFlag.Rows,
            )

        self.window.refresh_cards()

        self.assertEqual(2, len(self.window._selected_cards()))
        self.assertEqual("Import 2 selected cards", self.window.import_button.text())

    def test_completed_background_progress_returns_to_idle_status(self) -> None:
        self.window.events.put(
            ActivityEvent(
                "progress",
                "Camera Card 1: 0 of 0 files checked",
                details={"current": 0, "total": 0},
            )
        )

        self.window._drain_queues()

        self.assertEqual("Monitoring active", self.window.status_label.text())
        self.assertEqual("Monitoring active", self.window.progress_label.text())
        self.assertEqual(0, self.window.transfer_progress.value())

    def test_existing_library_uses_guided_source_plan_review_steps(self) -> None:
        self.window.resize(980, 660)
        self.window.show_page("Import or merge")
        self.app.processEvents()
        page = self.window.stack.widget(
            self.window.page_indexes["Import or merge"]
        )
        areas = page.findChildren(QScrollArea)

        self.assertEqual(2, len(areas))
        self.assertEqual(0, self.window.existing_step_stack.currentIndex())
        self.assertFalse(self.window.existing_back_button.isVisibleTo(page))
        self.assertTrue(self.window.existing_next_button.isVisibleTo(page))
        self.assertFalse(self.window.existing_review_button.isVisibleTo(page))
        self.assertFalse(self.window.existing_review_button.isEnabled())
        source = self.base / "existing-library"
        source.mkdir()
        self.window.existing_source_edit.setText(str(source))
        self.window.existing_name_edit.setText("Archive source")
        self.assertTrue(self.window.existing_next_button.isEnabled())

        self.window.existing_next_button.click()
        self.app.processEvents()
        self.assertEqual(1, self.window.existing_step_stack.currentIndex())
        self.assertTrue(self.window.existing_back_button.isVisibleTo(page))
        self.assertTrue(self.window.existing_next_button.isVisibleTo(page))
        plan_area = self.window.existing_step_stack.currentWidget()
        self.assertIsInstance(plan_area, QScrollArea)
        self.assertEqual(0, plan_area.horizontalScrollBar().maximum())

        self.window.existing_next_button.click()
        self.app.processEvents()
        self.assertEqual(2, self.window.existing_step_stack.currentIndex())
        self.assertTrue(self.window.existing_review_button.isVisibleTo(page))
        self.assertTrue(self.window.existing_review_button.isEnabled())
        summary = self.window.existing_review_summary.toPlainText()
        self.assertIn(f"Source: {source}", summary)
        self.assertIn("Operation: Copy", summary)
        self.assertIn("Scan scope: Selected folder and subfolders", summary)

    def test_library_tasks_use_plain_intents_and_carry_destination(self) -> None:
        self.window.show_page("Libraries")
        self.app.processEvents()

        self.assertEqual("Set up library", self.window.add_library_button.text())
        self.assertEqual("Export media", self.window.library_export_button.text())
        self.assertEqual("Reorganize library", self.window.reorganize_selected_library_button.text())
        self.assertEqual(
            "Import or merge",
            self.window.import_or_merge_button.text(),
        )
        selected = self.window._selected_library_destination()
        self.assertIsNotNone(selected)
        self.assertEqual(self.window.default_library_id, selected["id"])

        self.window._open_import_merge()
        self.app.processEvents()

        self.assertEqual(
            self.window.page_indexes["Import or merge"],
            self.window.stack.currentIndex(),
        )
        self.assertEqual(
            self.window.default_library_id,
            self.window.existing_library_combo.currentData(),
        )
        self.assertEqual(
            "Receiving library",
            self.window.existing_destination_form.labelForField(
                self.window.existing_library_combo
            ).text(),
        )
        self.assertEqual(
            "Library folder",
            self.window.existing_destination_form.labelForField(
                self.window.destination_edit.parentWidget()
            ).text(),
        )
        self.assertEqual(
            "Import grouping",
            self.window.existing_destination_form.labelForField(
                self.window.existing_folder_mode_combo
            ).text(),
        )
        self.assertIn(
            "keep source files",
            self.window.existing_action_combo.currentText(),
        )
        self.assertIn(
            self.window.destination_edit.text(),
            self.window.destination_edit.toolTip(),
        )
        self.assertEqual(0, self.window.destination_edit.cursorPosition())
        self.assertTrue(self.window.existing_event_name_edit.isHidden())
        self.assertTrue(self.window.existing_prefix_edit.isHidden())

        route_index = self.window.existing_folder_mode_combo.findData(
            "wedding"
        )
        self.window.existing_folder_mode_combo.setCurrentIndex(route_index)
        self.app.processEvents()
        self.assertFalse(self.window.existing_event_name_edit.isHidden())
        self.assertFalse(self.window.existing_prefix_edit.isHidden())
        self.assertEqual(
            "Save settings + Import",
            self.window.existing_review_button.text(),
        )

    def test_library_metadata_action_names_the_available_operation(self) -> None:
        Path(self.window.config["destination_root"]).mkdir(
            parents=True,
            exist_ok=True,
        )
        self.window._refresh_library_destinations()
        self.app.processEvents()

        self.assertEqual(
            "Initialize metadata",
            self.window.upgrade_library_button.text(),
        )
        self.assertIn(
            "Media files are not changed",
            self.window.upgrade_library_button.toolTip(),
        )

    def test_library_setup_hides_optional_storage_details(self) -> None:
        dialog = LibraryDestinationDialog(self.window)
        dialog.show()
        self.app.processEvents()

        self.assertEqual("Set up library", dialog.windowTitle())
        self.assertTrue(dialog.storage_label.isHidden())
        self.assertTrue(dialog.storage_combo.isHidden())
        self.assertIn("does not move files", dialog.root_edit.toolTip())
        buttons = dialog.findChild(QDialogButtonBox)
        self.assertIsNotNone(buttons)
        save_button = buttons.button(
            QDialogButtonBox.StandardButton.Save
        )
        self.assertIsNotNone(save_button)
        self.assertEqual("Add library", save_button.text())

        dialog.advanced_storage_check.setChecked(True)
        self.app.processEvents()
        self.assertFalse(dialog.storage_label.isHidden())
        self.assertFalse(dialog.storage_combo.isHidden())
        dialog.close()
        dialog.deleteLater()

    def test_export_filters_and_group_expansion_never_add_hidden_photos(self):
        root = self.base / "library"
        items = []
        for stem, kind, ext in (("a", "photo", "jpg"), ("a", "video", "mp4"), ("b", "photo", "jpg")):
            path = root / f"{stem}.{ext}"
            path.write_bytes(b"fixture")
            items.append(LibraryItem(path, Path(path.name), kind, datetime(2024, 3, 4)))
        self.window.library_captures = build_capture_sets(items)
        self.window.library_groups = [CaptureGroup("synthetic", "bracket", tuple(c.capture_id for c in self.window.library_captures), datetime(2024, 3, 4))]
        self.window._export_scanned_root = root
        combo = self.window.export_media_combo
        combo.setCurrentIndex(combo.findData("video"))
        self.window._refresh_export_table()
        self.assertEqual(self.window.export_model.rowCount(), 1)
        self.window.export_table.selectAll()
        self.window._select_export_groups()
        output = self.base / "export"
        output.mkdir()
        with patch("photocard.qt_window.QFileDialog.getExistingDirectory", return_value=str(output)), \
                patch("photocard.qt_window.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
                patch("photocard.qt_window.QMessageBox.information"):
            self.window._export_selected_captures()
        self.assertEqual(len(list(output.rglob("*.mp4"))), 1)
        self.assertEqual(len(list(output.rglob("*.jpg"))), 0)

    def test_export_button_carries_named_library_without_changing_default(self):
        default_id = self.window.default_library_id
        other_root = self.base / "other-library"
        other_root.mkdir()
        other = {"id": "other", "name": "Other library", "root": str(other_root), "enabled": True, "kind": "local"}
        self.window.library_destinations.append(other)
        self.window.config["library_destinations"] = copy.deepcopy(self.window.library_destinations)
        self.window._refresh_library_destinations()
        self.window.library_table.selectRow(1)
        self.window._open_library_export()
        self.assertEqual(self.window.export_library_combo.currentData(), "other")
        self.assertEqual(self.window.config["default_library_id"], default_id)
        self.assertEqual(self.window.export_library_label.text(), str(other_root))
        (other_root / "only.mp4").write_bytes(b"fixture")
        self.window._set_settings_dirty(False)
        self.window._scan_export_library()
        self.assertEqual(self.window._export_scanned_root, other_root)
        self.assertEqual(len(self.window.library_captures), 1)
        self.window.export_library_combo.setCurrentIndex(0)
        self.assertEqual(self.window.library_captures, [])
        self.assertFalse(self.window.export_selected_button.isEnabled())

    def test_reorganization_dialog_preview_invalidates_on_layout_change(self):
        source = self.base / "library" / "clip.mp4"
        source.write_bytes(b"fixture")
        dialog = ReorganizationDialog(self.window, self.window.config)
        dialog.start_preview()
        deadline = time.monotonic() + 10
        while dialog._running and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.02)
        self.assertIsNotNone(dialog.plan)
        self.assertTrue(dialog.process.isEnabled())
        self.assertTrue(source.exists())
        self.assertFalse((source.parent / "Videos").exists())
        dialog.preset.setCurrentIndex(1)
        self.assertIsNone(dialog.plan)
        self.assertTrue(dialog.process.isEnabled())
        dialog.reject()

    def test_reorganization_saves_layout_only_after_confirmation(self):
        from unittest.mock import Mock
        source = self.base / "library" / "old" / "clip.mp4"
        source.parent.mkdir()
        source.write_bytes(b"fixture")
        plan = build_reorganization_plan(self.window.config, "Media folder only", {"video"})
        dialog = Mock()
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        dialog.plan = plan
        dialog.preset.currentText.return_value = "Separate by media type"
        dialog.save_layout.isChecked.return_value = True
        dialog.cleanup.isChecked.return_value = True
        dialog.baselines.isChecked.return_value = False
        check = Mock()
        check.isChecked.return_value = True
        dialog.media = {"video": check}
        previous = copy.deepcopy(self.window.config)
        with patch("photocard.qt_window.ReorganizationDialog", return_value=dialog), \
                patch("photocard.qt_window.QMessageBox.warning", return_value=QMessageBox.StandardButton.No):
            self.window._reorganize_selected_library()
        self.assertEqual(self.window.config, previous)
        self.assertTrue(source.exists())
        with patch("photocard.qt_window.ReorganizationDialog", return_value=dialog), \
                patch("photocard.qt_window.QMessageBox.warning", return_value=QMessageBox.StandardButton.Yes):
            self.window._reorganize_selected_library()
            deadline = time.monotonic() + 10
            while self.window._manual_import_running and time.monotonic() < deadline:
                self.window._drain_queues()
                self.app.processEvents()
                time.sleep(0.02)
        self.assertFalse(self.window._manual_import_running)
        self.assertFalse(source.exists())
        self.assertTrue((self.base / "library/Videos/clip.mp4").exists())
        self.assertEqual(self.window.config["media_rules"], previous["media_rules"])
        library = next(item for item in self.window.config["library_destinations"] if item["id"] == previous["default_library_id"])
        self.assertEqual(library["organization_overrides"]["video"]["folder_segments"], ["Videos"])
        self.assertEqual(self.window.config["default_library_id"], previous["default_library_id"])
        self.assertFalse(self.window._settings_dirty)

    def test_guided_existing_library_copy_runs_after_final_confirmation(self) -> None:
        self.window.config["monitor"]["settle_seconds"] = 0
        source = self.base / "legacy-source" / "old-layout"
        source.mkdir(parents=True)
        media = source / "MVI_0042.MP4"
        media.write_bytes(b"existing-library-video")
        self.window.existing_source_edit.setText(str(source.parent))
        self.window.existing_name_edit.setText("Legacy source")
        self.window.existing_next_button.click()
        self.window.existing_next_button.click()
        self.app.processEvents()

        with patch(
            "photocard.qt_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.window.existing_review_button.click()
            deadline = time.monotonic() + 10
            while (
                self.window._manual_import_running
                and time.monotonic() < deadline
            ):
                self.window._drain_queues()
                self.app.processEvents()
                time.sleep(0.02)

        self.assertFalse(self.window._manual_import_running)
        self.assertTrue(media.is_file())
        self.assertEqual(
            1,
            len(list((self.base / "library").rglob("MVI_0042.MP4"))),
        )
        self.assertFalse(
            (source.parent / ".photocard-folder-import-disabled").exists()
        )
        self.assertEqual(0, self.window.existing_step_stack.currentIndex())

    def test_guided_existing_library_preserves_six_detected_levels(self) -> None:
        self.window.config["monitor"]["settle_seconds"] = 0
        source_root = self.base / "deep-source"
        relative_parent = Path("one", "two", "three", "four", "five", "six")
        media = source_root / relative_parent / "DEEP_0001.MP4"
        media.parent.mkdir(parents=True)
        media.write_bytes(b"deep-existing-library-video")
        self.window.existing_source_edit.setText(str(source_root))
        self.window.existing_name_edit.setText("Deep source")
        self.window.existing_structure_rules = {
            kind: list(rule["folder_segments"])
            for kind, rule in self.window.config["media_rules"].items()
        }
        self.window.existing_structure_rules["video"] = [
            f"{{source_dir:{index}}}" for index in range(1, 7)
        ]
        self.window.existing_preset_combo.insertItem(
            0, "Detected existing structure"
        )
        self.window.existing_preset_combo.setCurrentIndex(0)
        self.window.existing_next_button.click()
        self.window.existing_next_button.click()
        self.app.processEvents()

        with patch(
            "photocard.qt_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.window.existing_review_button.click()
            deadline = time.monotonic() + 10
            while (
                self.window._manual_import_running
                and time.monotonic() < deadline
            ):
                self.window._drain_queues()
                self.app.processEvents()
                time.sleep(0.02)

        self.assertFalse(self.window._manual_import_running)
        self.assertTrue(media.is_file())
        self.assertTrue(
            (
                self.base
                / "library"
                / relative_parent
                / "DEEP_0001.MP4"
            ).is_file()
        )
        self.assertEqual(
            6,
            len(self.window.config["media_rules"]["video"]["folder_segments"]),
        )
        self.assertEqual(
            6,
            len(self.window.media_controls["video"]["segments"]),
        )

    def test_choosing_existing_library_does_not_force_structure_analysis(self) -> None:
        source = self.base / "uninterrupted-source"
        source.mkdir()
        with (
            patch(
                "photocard.qt_window.QFileDialog.getExistingDirectory",
                return_value=str(source),
            ),
            patch("photocard.qt_window.QMessageBox.question") as question,
        ):
            self.window._browse_existing_source()

        question.assert_not_called()
        self.assertEqual(str(source), self.window.existing_source_edit.text())
        self.assertEqual("uninterrupted-source", self.window.existing_name_edit.text())
        self.assertEqual(0, self.window.existing_step_stack.currentIndex())

    def test_existing_structure_mapping_is_invalidated_when_scope_changes(self) -> None:
        self.window.existing_structure_rules = {
            kind: ["{source_dir:1}"] for kind in self.window.existing_media_checks
        }
        self.window.existing_preset_combo.insertItem(
            0, "Detected existing structure"
        )
        self.window.existing_preset_combo.setCurrentIndex(0)

        self.window.existing_recursive_check.setChecked(False)

        self.assertIsNone(self.window.existing_structure_rules)
        self.assertEqual(
            -1,
            self.window.existing_preset_combo.findText(
                "Detected existing structure"
            ),
        )
        self.assertIn(
            "has not been analyzed",
            self.window.existing_structure_status_label.text(),
        )

    def test_travel_sources_and_shared_hubs_have_separate_tabs(self) -> None:
        self.assertEqual(2, self.window.travel_tabs.count())
        self.assertEqual("Laptop libraries", self.window.travel_tabs.tabText(0))
        self.assertEqual("Shared hubs", self.window.travel_tabs.tabText(1))
        self.assertTrue(
            self.window.travel_tabs.widget(0).isAncestorOf(
                self.window.travel_table
            )
        )
        self.assertTrue(
            self.window.travel_tabs.widget(1).isAncestorOf(
                self.window.hub_table
            )
        )

    def test_changelog_opens_inside_application(self) -> None:
        with (
            patch(
                "photocard.qt_window.QDialog.exec",
                return_value=QDialog.DialogCode.Rejected,
            ) as execute,
            patch("photocard.qt_window.open_local_path") as external_open,
        ):
            self.window._open_changelog()

        execute.assert_called_once()
        external_open.assert_not_called()

    def test_primary_workflows_use_responsive_control_rows(self) -> None:
        self.window.resize(980, 660)
        page_layouts = {
            "Dashboard": self.window.dashboard_actions_layout,
            "Digest inboxes": self.window.digest_actions_layout,
            "Travel sync": self.window.travel_actions_layout,
            "Library export": self.window.export_options_layout,
        }

        for page_name, controls in page_layouts.items():
            self.window.show_page(page_name)
            self.app.processEvents()
            page = self.window.stack.currentWidget()
            for index in range(controls.count()):
                widget = controls.itemAt(index).widget()
                if widget is not None and widget.isVisible():
                    origin = widget.mapTo(page, QPoint(0, 0))
                    self.assertTrue(page.rect().contains(origin), page_name)
                    self.assertLessEqual(origin.x() + widget.width(), page.width(), page_name)
                    self.assertLessEqual(origin.y() + widget.height(), page.height(), page_name)
        self.assertEqual(0, self.window.navigation.verticalScrollBar().maximum())

    def test_general_settings_remain_saved_and_event_fields_are_conditional(self) -> None:
        self.window.show_page("General options")
        self.app.processEvents()
        self.assertTrue(self.window.poll_spin.isVisible())
        self.assertTrue(self.window.maintenance_button.isVisible())
        self.window.poll_spin.setValue(30)
        self.assertTrue(self.window._settings_dirty)
        self.assertEqual(30, self.window._collect_config()["monitor"]["poll_seconds"])
        self.assertEqual("Unsaved changes", self.window.settings_state_label.text())
        self.window.show_page("Dashboard")
        self.assertFalse(self.window.import_folder_name_edit.isVisible())
        self.window.import_folder_mode_combo.setCurrentIndex(1)
        self.assertTrue(self.window.import_folder_name_edit.isVisible())

    def test_conflict_review_fits_inside_minimum_window_body(self) -> None:
        self.window.resize(980, 660)
        self.window.show_page("Conflict review")
        self.app.processEvents()
        page = self.window.stack.currentWidget()

        self.assertLessEqual(page.minimumSizeHint().width(), self.window.stack.width())
        self.assertLessEqual(page.minimumSizeHint().height(), self.window.stack.height())

    def test_application_icon_contains_taskbar_and_high_resolution_sizes(self) -> None:
        sizes = {(size.width(), size.height()) for size in application_icon().availableSizes()}

        self.assertIn((16, 16), sizes)
        self.assertIn((64, 64), sizes)
        self.assertIn((256, 256), sizes)
        self.assertIn((512, 512), sizes)

    def test_visual_identity_uses_graphite_jade_coral_palette(self) -> None:
        from photocard.qt_theme import COLORS

        self.assertEqual("#202224", COLORS["background"])
        self.assertEqual("#43b99c", COLORS["accent"])
        self.assertEqual("#e78677", COLORS["error"])
        self.assertIn("#43b99c", self.app.styleSheet())

    def test_digest_dialog_keeps_move_manual_and_explains_controls(self) -> None:
        dialog = DigestInboxDialog(
            self.window,
            Path(self.window.config["destination_root"]),
            {
                "id": "drop",
                "name": "Drop",
                "root": str(self.base / "drop"),
                "action": "move",
                "auto_digest": True,
            },
        )
        self.app.processEvents()

        self.assertEqual("move", dialog.action_combo.currentData())
        self.assertFalse(dialog.auto_digest_check.isChecked())
        self.assertFalse(dialog.auto_digest_check.isEnabled())
        self.assertFalse(dialog.poll_spin.isEnabled())
        for field in (
            dialog.name_edit,
            dialog.root_edit,
            dialog.prefix_edit,
            dialog.camera_edit,
            dialog.action_combo,
            dialog.auto_digest_check,
            dialog.poll_spin,
        ):
            self.assertTrue(field.toolTip().strip())
        dialog.close()
        dialog.deleteLater()

    def test_digest_page_runs_reviewed_copy_and_updates_queue(self) -> None:
        source = self.base / "drop"
        source.mkdir()
        (source / "legacy" / "session").mkdir(parents=True)
        (source / "legacy" / "session" / "CLIP_0001.MP4").write_bytes(
            b"video"
        )
        inbox = normalize_digest_inbox(
            {
                "id": "drop",
                "name": "Legacy drop",
                "root": str(source),
                "action": "copy",
                "enabled": True,
            }
        )
        self.assertIsNotNone(inbox)
        self.window.config["monitor"]["settle_seconds"] = 0
        self.window.digest_inboxes = [inbox]
        self.assertTrue(self.window._persist_digest_inboxes())
        self.window.digest_table.selectRow(0)

        with (
            patch(
                "photocard.qt_window.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ),
            patch("photocard.qt_window.QMessageBox.information"),
            patch("photocard.qt_window.QMessageBox.warning"),
        ):
            self.window._digest_selected_inboxes()
            deadline = time.monotonic() + 10
            while (
                self.window._manual_import_running
                and time.monotonic() < deadline
            ):
                self.window._drain_queues()
                self.app.processEvents()
                time.sleep(0.02)

        self.assertFalse(self.window._manual_import_running)
        self.assertEqual(
            1,
            len(
                list(
                    (self.base / "library").rglob("CLIP_0001.MP4")
                )
            ),
        )
        self.window._refresh_digest_inboxes()
        self.assertEqual("1", self.window.digest_table.item(0, 5).text())
        self.assertEqual(1, self.window.digest_queue_table.rowCount())
        self.assertEqual("1 file", self.window.digest_queue_count_label.text())
        self.assertEqual(
            "PROCESSED", self.window.digest_queue_table.item(0, 0).text()
        )

    def test_mouse_wheel_over_number_field_scrolls_form(self) -> None:
        self.window.resize(980, 660)
        self.window.show_page("Safety and location")
        self.app.processEvents()
        page = self.window.stack.widget(
            self.window.page_indexes["Safety and location"]
        )
        tabs = page.findChild(QTabWidget)
        self.assertIsNotNone(tabs)
        tabs.setCurrentIndex(1)
        self.app.processEvents()
        area = tabs.currentWidget()
        scrollbar = area.verticalScrollBar()
        self.assertGreater(scrollbar.maximum(), 0)
        original_value = self.window.minimum_percent_spin.value()
        event = QWheelEvent(
            QPointF(5, 5),
            QPointF(5, 5),
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate,
            False,
        )

        QApplication.sendEvent(self.window.minimum_percent_spin, event)

        self.assertGreater(scrollbar.value(), 0)
        self.assertEqual(
            original_value, self.window.minimum_percent_spin.value()
        )

    def test_onboarding_wizard_keeps_progress_and_field_help_alive(self) -> None:
        wizard = CardOnboardingWizard(self.window, self.window.config)
        self.app.processEvents()

        self.assertEqual(4, len(wizard.pageIds()))
        self.assertEqual(1, wizard.progress.value())
        self.assertFalse(wizard.move_checksum_combo.isEnabled())
        wizard.action_combo.setCurrentIndex(wizard.action_combo.findData("move"))
        self.assertTrue(wizard.move_checksum_combo.isEnabled())
        for field in (
            wizard.root_edit,
            wizard.id_edit,
            wizard.sources_edit,
            wizard.destination_edit,
            wizard.action_combo,
            wizard.minimum_percent_edit,
        ):
            self.assertTrue(field.toolTip().strip())
        wizard.root_edit.setText(str(self.cards[0]))
        wizard.name_edit.setText("Camera Card 1")
        wizard.id_edit.setText("camera-card-1")
        wizard.show()
        wizard.next()
        self.app.processEvents()
        self.assertEqual(1, wizard.currentId())
        self.assertTrue(wizard.destination_browse_button.isVisibleTo(wizard))
        self.assertEqual(82, wizard.destination_browse_button.width())
        wizard.hide()
        wizard.close()
        wizard.deleteLater()

    def test_detected_structure_opens_as_an_editable_mapping(self) -> None:
        source = self.base / "existing" / "Videos" / "2026" / "2026-07-12"
        source.mkdir(parents=True)
        (source / "clip.mp4").write_bytes(b"video")
        analysis = detect_existing_structure(
            self.base / "existing", self.window.config["media_rules"]
        )
        current = {
            kind: list(rule["folder_segments"])
            for kind, rule in self.window.config["media_rules"].items()
        }
        dialog = StructureMappingDialog(self.window, analysis, current)

        self.assertEqual(4, len(dialog.level_combos))
        self.assertTrue(dialog.level_combos["video"][0].isEditable())
        self.assertTrue(dialog.level_combos["video"][0].toolTip())
        dialog.accept()
        self.assertEqual(
            ["{media}", "{date:%Y}", "{date:%Y-%m-%d}"],
            dialog.result_rules["video"],
        )
        dialog.deleteLater()

    def test_large_deep_structure_preview_is_clear_and_scrollable(self) -> None:
        source = (
            self.base
            / "deep-existing"
            / "Videos"
            / "Archive"
            / "2026"
            / "2026-07-27"
            / "Project"
            / "Final"
        )
        source.mkdir(parents=True)
        (source / "clip-1.mp4").write_bytes(b"video-one")
        (source / "clip-2.mp4").write_bytes(b"video-two")
        analysis = detect_existing_structure(
            self.base / "deep-existing",
            self.window.config["media_rules"],
            max_files=1,
        )
        current = {
            kind: list(rule["folder_segments"])
            for kind, rule in self.window.config["media_rules"].items()
        }

        dialog = StructureMappingDialog(self.window, analysis, current)

        self.assertEqual(6, len(dialog.level_combos["video"]))
        self.assertIsNotNone(dialog.preview_limit_warning)
        self.assertIn(
            "import is not limited",
            dialog.preview_limit_warning.text(),
        )
        self.assertNotIn(
            "larger or deeper than the editable sample",
            dialog.preview_limit_warning.text(),
        )
        self.assertIsNone(dialog.depth_limit_warning)
        areas = dialog.findChildren(QScrollArea)
        self.assertEqual(4, len(areas))
        dialog.resize(820, 650)
        dialog.show()
        self.app.processEvents()
        tabs = dialog.findChild(QTabWidget)
        for index in range(tabs.count()):
            tabs.setCurrentIndex(index)
            self.app.processEvents()
            area = tabs.currentWidget()
            self.assertEqual(0, area.horizontalScrollBar().maximum())
        dialog.hide()
        dialog.deleteLater()

    def test_two_card_ui_queue_runs_sequential_import_sessions(self) -> None:
        self.window.config["monitor"]["settle_seconds"] = 0
        for index, card_root in enumerate(self.cards, 1):
            (card_root / "DCIM" / f"MVI_{index:04d}.MP4").write_bytes(
                f"video-{index}".encode("ascii")
            )
        cards = list(self.window.card_by_id.values())
        with patch(
            "photocard.qt_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.assertTrue(self.window._start_card_batch(cards))

        deadline = time.monotonic() + 10
        while self.window._manual_import_running and time.monotonic() < deadline:
            self.window._drain_queues()
            self.app.processEvents()
            time.sleep(0.02)

        self.assertFalse(self.window._manual_import_running)
        self.assertEqual(2, len(list((self.base / "library").rglob("MVI_*.MP4"))))
        for card_root in self.cards:
            self.assertEqual(
                1,
                len(list((card_root / ".photocard" / "transfers").rglob("*.jsonl"))),
            )


if __name__ == "__main__":
    unittest.main()
