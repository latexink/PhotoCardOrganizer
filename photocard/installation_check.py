from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path

from . import __version__
from .config import normalize_config


def check_gui(report_path: Path) -> int:
    """Exercise the installed GUI with disposable state and no connected sources."""
    report_path = report_path.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report: dict = {"status": "failed", "version": __version__, "pages": []}
    previous_hook = sys.excepthook
    errors: list[str] = []
    sys.excepthook = lambda *error: errors.append("".join(traceback.format_exception(*error)))
    try:
        from PySide6.QtCore import QTimer, qVersion
        from PySide6.QtWidgets import QApplication

        from .qt_theme import application_icon, configure_application, configure_windows_taskbar_identity
        from .qt_window import PAGE_NAMES, PhotoCardApp

        configure_windows_taskbar_identity()
        app = QApplication([])
        app.setApplicationName("Photo Card Organizer")
        app.setWindowIcon(application_icon())
        app.setQuitOnLastWindowClosed(False)
        configure_application(app)
        report["qt_version"] = qVersion()
        report["platform"] = app.platformName()
        with tempfile.TemporaryDirectory(prefix="gui-check-", dir=report_path.parent) as temporary:
            base = Path(temporary)
            config = normalize_config({
                "destination_root": str(base / "library"),
                "local_history": {"directory": str(base / "history")},
                "identification": {"auto_detect": False, "configured_roots": []},
                "monitor": {"poll_seconds": 3600},
            })
            window = PhotoCardApp(config, base / "config.json")
            try:
                for page in PAGE_NAMES:
                    window.show_page(page)
                    app.processEvents()
                    report["pages"].append(page)
                window.show_page("Dashboard")
                QTimer.singleShot(1000, app.quit)
                app.exec()
                snapshot = window.grab()
                if snapshot.isNull() or not snapshot.save(str(report_path.with_suffix(".png"))):
                    raise RuntimeError("The application window could not be rendered.")
                if window.windowIcon().isNull():
                    raise RuntimeError("The application icon is missing.")
            finally:
                window.shutdown()
                window.hide()
                window.deleteLater()
                app.processEvents()
        if errors:
            raise RuntimeError("\n".join(errors))
        report["status"] = "passed"
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        sys.excepthook = previous_hook
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report["status"] == "passed" else 1
