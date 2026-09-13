from __future__ import annotations

import tempfile
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from photocard.installation import detect_installation


class InstallationDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_developer_install_exposes_available_maintenance_scripts(self) -> None:
        install = self.base / ("install.bat" if os.name == "nt" else "install.sh")
        install.write_text("@echo off\n", encoding="ascii")

        info = detect_installation(self.base)

        self.assertEqual("developer", info.kind)
        self.assertEqual(install, info.install_target)
        self.assertIsNone(info.uninstall_target)

    @unittest.skipUnless(os.name == "nt", "Inno Setup installation detection is Windows-specific")
    def test_frozen_windows_install_finds_inno_uninstaller(self) -> None:
        executable = self.base / "PhotoCardOrganizer.exe"
        uninstaller = self.base / "unins000.exe"
        executable.write_bytes(b"exe")
        uninstaller.write_bytes(b"uninstaller")

        with (
            patch("photocard.installation.sys.frozen", True, create=True),
            patch("photocard.installation.sys.executable", str(executable)),
        ):
            info = detect_installation(self.base / "unused")

        self.assertEqual("windows-package", info.kind)
        self.assertTrue(info.verified)
        self.assertEqual(uninstaller, info.uninstall_target)
        self.assertIsNone(info.install_target)
        self.assertIn(
            "PhotoCardOrganizer.exe launches the application",
            info.detail,
        )
        self.assertIn(
            "PhotoCardOrganizer-Installer",
            info.detail,
        )


if __name__ == "__main__":
    unittest.main()
