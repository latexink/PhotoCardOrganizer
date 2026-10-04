"""Render the real Qt interface with disposable, disconnected library state."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase
from photocard.config import normalize_config
from photocard.qt_theme import configure_application, application_icon
from photocard.qt_window import PhotoCardApp, PAGE_NAMES
from photocard.qt_dialogs import LibraryOrganizationDialog
from photocard.qt_library_tools import LibraryJobDialog, ReorganizationDialog


def main():
    app = QApplication([])
    if os.name == "nt":
        for name in ("segoeui.ttf", "segoeuib.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name))
    configure_application(app)
    app.setWindowIcon(application_icon())
    output = ROOT / "assets" / "screenshots"
    output.mkdir(parents=True, exist_ok=True)
    audit = ROOT / "build" / "ui-review"
    audit.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        (base / "Photo Library").mkdir()
        (base / "Incoming").mkdir()
        config = normalize_config({"destination_root": str(base / "Photo Library"),
            "identification": {"auto_detect": False, "configured_roots": []},
            "local_history": {"directory": str(base / "history")},
            "replica_destinations": [{"id": "archive", "name": "Archive drive",
                "root": str(base / "Backup"), "required": True}],
            "travel_libraries": [{"id": "field", "name": "Field laptop", "root": ""}],
            "transfer_hubs": [{"id": "shared", "name": "Return-home USB", "root": "",
                "role": "catch", "producer_channel": "Field-Laptop"}],
            "monitor": {"poll_seconds": 3600}})
        window = PhotoCardApp(config, base / "config.json")
        try:
            window.show()
            for width, height in ((980, 660), (1440, 900)):
                window.resize(width, height)
                for page in PAGE_NAMES:
                    window.show_page(page)
                    app.processEvents()
                    slug = page.lower().replace(" ", "-")
                    window.grab().save(str(audit / f"{slug}-{width}.png"))
                    asset_names = {"Libraries": "libraries", "Integrity": "integrity",
                        "Organization": "organization", "Safety and location": "backups",
                        "General options": "general", "Travel sync": "travel"}
                    if width == 1440 and page in asset_names:
                        window.grab().save(str(output / f"{asset_names[page]}.png"))
                window.show_page("Safety and location")
                window.record_options_button.setChecked(True)
                window.error_options_button.setChecked(True)
                window.conflict_options_button.setChecked(True)
                window.location_options_button.setChecked(True)
                app.processEvents()
                area = window.safety_scroll_area
                area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
                app.processEvents()
                window.grab().save(str(audit / f"backup-advanced-{width}.png"))
                for button in (window.record_options_button, window.error_options_button,
                               window.conflict_options_button, window.location_options_button):
                    button.setChecked(False)
                area.verticalScrollBar().setValue(0)
                window.show_page("Import or merge")
                window.existing_source_edit.setText(str(base / "Incoming"))
                window._set_existing_step(1)
                app.processEvents()
                window.grab().save(str(audit / f"import-options-{width}.png"))
            dialog = LibraryOrganizationDialog(window, {}, config["media_rules"])
            dialog.show()
            app.processEvents()
            dialog.grab().save(str(audit / "library-organization.png"))
            dialog.close()
            for name, dialog in (
                ("move-library", LibraryJobDialog(window, config, [base / "Photo Library"],
                    mode="migrate", migration_target=base / "New Library")),
                ("reorganize-library", ReorganizationDialog(window, config)),
            ):
                dialog.show()
                app.processEvents()
                dialog.grab().save(str(audit / f"{name}.png"))
                dialog.close()
        finally:
            window.shutdown()
            window.hide()
    print(output)


if __name__ == "__main__":
    main()
