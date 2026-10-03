"""Exercise native failure reporting and Qt lifetimes outside the test runner."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class ProcessSafetyTests(unittest.TestCase):
    def run_child(self, arguments, root, timeout=60):
        environment = dict(os.environ, QT_QPA_PLATFORM="offscreen",
                           APPDATA=str(root), LOCALAPPDATA=str(root),
                           XDG_CONFIG_HOME=str(root), XDG_STATE_HOME=str(root), XDG_DATA_HOME=str(root))
        environment.pop("PYTHONFAULTHANDLER", None)
        return subprocess.run(
            [sys.executable, *arguments], cwd=Path(__file__).resolve().parents[1],
            env=environment, capture_output=True, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    def test_native_fault_is_recorded_in_disposable_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = (
                "import ctypes, faulthandler, os, sys; from pathlib import Path; "
                "from photocard.diagnostics import Diagnostics; "
                "ctypes.windll.kernel32.SetErrorMode(3) if os.name == 'nt' else None; "
                "exec('import resource; resource.setrlimit(resource.RLIMIT_CORE, (0, 0))') if os.name != 'nt' else None; "
                "d = Diagnostics(Path(sys.argv[1])); faulthandler._sigsegv()"
            )
            result = self.run_child(["-c", script, str(root)], root, timeout=20)
            self.assertNotEqual(result.returncode, 0)
            evidence = (root / "native-crash.log").read_text(encoding="utf-8")
            self.assertIn("<module>", evidence)
            self.assertTrue("access violation" in evidence.lower() or "segmentation fault" in evidence.lower(), evidence)
            self.assertTrue((root / "session-active").exists())

    def test_custom_config_keeps_diagnostics_with_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "custom-client" / "config.json"
            result = self.run_child(["app.py", "--config", str(config), "--init-config"], root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((config.parent / "diagnostics" / "application.log").exists())
            self.assertFalse((config.parent / "diagnostics" / "session-active").exists())

    def test_repeated_dialog_preview_cancel_and_invalidation_in_child(self):
        cases = [
            "test_library_job_cancel_waits_for_worker",
            "test_migration_verification_change_invalidates_preview",
            "test_reorganization_dialog_preview_invalidates_on_layout_change",
        ]
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_child(
                ["-X", "faulthandler", "-m", "unittest",
                 *[f"tests.test_qt_ui.QtWorkflowTests.{case}" for _ in range(6) for case in cases]],
                Path(temporary),
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Ran 18 tests", result.stderr)


if __name__ == "__main__":
    unittest.main()
