import logging
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess
from unittest.mock import patch
from zipfile import ZipFile

from photocard.diagnostics import Diagnostics, export_report


class DiagnosticsTests(unittest.TestCase):
    def test_session_marker_cleared_only_after_clean_exit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = Diagnostics(root)
            self.assertFalse(first.previous_unclean_exit)
            first.failed = True
            first.close()
            second = Diagnostics(root)
            self.assertTrue(second.previous_unclean_exit)
            second.close()
            self.assertFalse((root / "session-active").exists())
            third = Diagnostics(root)
            self.assertFalse(third.previous_unclean_exit)
            third.close()

    def test_clean_launch_does_not_erase_preserved_crash(self):
        import faulthandler
        if faulthandler.is_enabled():
            self.skipTest("External fault handler already enabled")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "native-crash.log").write_text("synthetic native crash\nstack\n")
            for _ in range(3):
                diagnostics = Diagnostics(root)
                diagnostics.close()
            self.assertIn("synthetic native crash", (root / "native-crash.previous.log").read_text())

    def test_abrupt_child_exit_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run(
                [sys.executable, "-c", "from photocard.diagnostics import Diagnostics; "
                 "from pathlib import Path; import os, sys; "
                 "d=Diagnostics(Path(sys.argv[1])); os._exit(19)", folder],
                capture_output=True, timeout=20,
            )
            self.assertEqual(result.returncode, 19, result.stderr)
            diagnostics = Diagnostics(Path(folder))
            self.assertTrue(diagnostics.previous_unclean_exit)
            diagnostics.close()

    def test_unhandled_exception_leaves_recovery_marker(self):
        with tempfile.TemporaryDirectory() as folder, patch("sys.excepthook"):
            diagnostics = Diagnostics(Path(folder))
            diagnostics._exception(ValueError, ValueError("synthetic"), None)
            diagnostics.close()
            self.assertTrue((Path(folder) / "session-active").exists())

    def test_error_traceback_is_saved_and_hooks_restored(self):
        previous = sys.excepthook
        with tempfile.TemporaryDirectory() as folder:
            diagnostics = Diagnostics(Path(folder))
            try:
                try:
                    raise ValueError("synthetic failure")
                except ValueError:
                    logging.getLogger("photocard.test").exception("operation failed")
                text = (Path(folder) / "application.log").read_text(encoding="utf-8")
                self.assertIn("Traceback", text)
                self.assertIn("synthetic failure", text)
                self.assertEqual(diagnostics.logger.level, logging.INFO)
                diagnostics.set_detailed(True)
                self.assertEqual(diagnostics.logger.level, logging.DEBUG)
            finally:
                diagnostics.close()
            self.assertIs(sys.excepthook, previous)

    def test_existing_native_report_is_preserved(self):
        import faulthandler
        if faulthandler.is_enabled():
            self.skipTest("External fault handler already enabled")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "native-crash.log").write_text("previous crash")
            diagnostics = Diagnostics(root)
            diagnostics.close()
            self.assertEqual((root / "native-crash.previous.log").read_text(), "previous crash")

    def test_export_report_redacts_library_root_and_excludes_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            library = root / "private-library"
            library.mkdir()
            (root / "application.log").write_text(
                f"Could not read {library / 'private.jpg'}\n",
                encoding="utf-8",
            )
            destination = root / "report.zip"
            (root / "private.dmp").write_bytes(b"private memory")
            (root / "settings.json").write_text("private settings")
            export_report(
                root,
                destination,
                {"library_destinations": [{"root": str(library)}]},
            )
            with ZipFile(destination) as archive:
                names = archive.namelist()
                log = archive.read("application.log").decode("utf-8")
            self.assertEqual(["report.txt", "application.log"], names)
            self.assertNotIn(str(library), log)
            self.assertIn("<local-path-", log)

    def test_qt_warning_is_captured_and_handler_restored(self):
        from PySide6.QtCore import qInstallMessageHandler, qWarning
        received = []
        prior = qInstallMessageHandler(lambda *args: received.append(args[-1]))
        try:
            with tempfile.TemporaryDirectory() as folder:
                diagnostics = Diagnostics(Path(folder))
                try:
                    diagnostics.install_qt_handler()
                    diagnostics.install_qt_handler()
                    qWarning("synthetic Qt warning")
                finally:
                    diagnostics.close()
                text = (Path(folder) / "application.log").read_text(encoding="utf-8")
                self.assertEqual(text.count("synthetic Qt warning"), 1)
                qWarning("restored handler")
                self.assertEqual(received, ["restored handler"])
        finally:
            qInstallMessageHandler(prior)
