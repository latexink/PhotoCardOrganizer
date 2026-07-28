from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from photocard.autostart import install_autostart, runtime_command


class AutostartCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.config_path = self.base / "config.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_developer_command_uses_python_module(self) -> None:
        with patch("photocard.autostart.sys.frozen", False, create=True):
            command = runtime_command(self.config_path)
        self.assertIn("-m", command)
        self.assertIn("photocard", command)

    def test_frozen_command_invokes_executable_directly(self) -> None:
        executable = self.base / "PhotoCardOrganizer.exe"
        with (
            patch("photocard.autostart.sys.frozen", True, create=True),
            patch("photocard.autostart.sys.executable", str(executable)),
        ):
            command = runtime_command(self.config_path)
        self.assertEqual(str(executable), command[0])
        self.assertNotIn("-m", command)
        self.assertEqual("--service", command[1])

    @unittest.skipUnless(os.name == "nt", "Windows startup command")
    def test_windows_startup_file_uses_packaged_argument_shape(self) -> None:
        executable = self.base / "PhotoCardOrganizer.exe"
        with (
            patch.dict(os.environ, {"APPDATA": str(self.base)}, clear=False),
            patch("photocard.autostart.sys.frozen", True, create=True),
            patch("photocard.autostart.sys.executable", str(executable)),
        ):
            target = install_autostart(self.config_path)
        content = target.read_text(encoding="utf-8")
        self.assertIn(str(executable), content)
        self.assertIn("--service", content)
        self.assertNotIn(" -m ", content)


if __name__ == "__main__":
    unittest.main()
