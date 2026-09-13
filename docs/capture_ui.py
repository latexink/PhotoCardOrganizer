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
        config = normalize_config({"destination_root": str(base / "Photo Library"),
            "identification": {"auto_detect": False, "configured_roots": []},
            "local_history": {"directory": str(base / "history")},
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
                    if width == 1440 and page in {"Libraries", "Integrity", "Organization"}:
                        window.grab().save(str(output / f"{slug}.png"))
            dialog = LibraryOrganizationDialog(window, {}, config["media_rules"])
            dialog.show()
            app.processEvents()
            dialog.grab().save(str(audit / "library-organization.png"))
            dialog.close()
        finally:
            window.shutdown()
            window.hide()
    print(output)


if __name__ == "__main__":
    main()
