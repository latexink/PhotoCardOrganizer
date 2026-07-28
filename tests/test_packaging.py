from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from photocard import __version__
from photocard.config import CURRENT_CONFIG_SCHEMA, DEFAULT_USER_AGENT


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class PackagingContractTests(unittest.TestCase):
    def test_project_and_runtime_versions_match(self) -> None:
        import tomllib

        with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
            project_version = tomllib.load(handle)["project"]["version"]
        self.assertEqual(project_version, __version__)
        self.assertEqual(
            f"PhotoCardOrganizer/{'.'.join(__version__.split('.')[:2])}",
            DEFAULT_USER_AGENT,
        )

    def test_cli_version_does_not_create_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = {
                "APPDATA": temporary,
                "XDG_CONFIG_HOME": temporary,
                "PATH": str(Path(sys.executable).parent),
            }
            result = subprocess.run(
                [sys.executable, "app.py", "--version"],
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn(__version__, result.stdout)
            self.assertEqual([], list(Path(temporary).rglob("config.json")))

    def test_windows_installer_has_stable_upgrade_and_data_preservation_contract(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "windows" / "PhotoCardOrganizer.iss").read_text(
            encoding="utf-8"
        )
        build_script = (
            PROJECT_ROOT / "packaging" / "windows" / "build-installer.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("AppId={{88A215E6-7853-4B4B-BFD4-FCE119514E0D}", script)
        self.assertIn("PrivilegesRequired=lowest", script)
        self.assertIn("AppMutex=", script)
        self.assertIn("UsePreviousTasks=yes", script)
        self.assertIn("Start monitoring when I sign in", script)
        self.assertIn(
            "OutputBaseFilename=PhotoCardOrganizer-Installer-{#AppVersion}",
            script,
        )
        self.assertIn(
            'PhotoCardOrganizer-Installer-$Version.exe',
            build_script,
        )
        self.assertNotIn(
            'PhotoCardOrganizer-Setup-$Version.exe',
            build_script,
        )
        self.assertIn(
            "VersionInfoDescription=Photo Card Organizer Installer and Uninstaller",
            script,
        )
        self.assertIn('Name: "{app}\\PhotoCardOrganizer-*-User-Guide.pdf"', script)
        self.assertIn('PhotoCardOrganizer-{#AppVersion}-User-Guide.pdf', script)
        self.assertIn(
            'Name: "{userprograms}\\Photo Card Organizer\\Changelog"',
            script,
        )
        self.assertIn('Filename: "{sys}\\notepad.exe"', script)
        self.assertIn('Parameters: """{app}\\CHANGELOG.md"""', script)
        self.assertIn(
            'Name: "{userprograms}\\Photo Card Organizer\\Uninstall Photo Card Organizer"',
            script,
        )
        self.assertIn('Filename: "{uninstallexe}"', script)
        self.assertIn("function InitializeUninstall: Boolean", script)
        self.assertIn("CreateInputOptionPage", script)
        self.assertIn("Uninstall {#AppName}", script)
        self.assertIn('ValueName: "UninstallPath"', script)
        self.assertIn("function NextButtonClick", script)
        self.assertIn("Exec('>', CommandLine", script)
        self.assertIn("procedure CancelButtonClick", script)
        self.assertIn("RemoveUserData := False", script)
        self.assertIn("if UninstallSilent then", script)
        self.assertIn("MB_YESNOCANCEL", script)
        self.assertIn("CurUninstallStep = usDone", script)
        self.assertNotIn("[UninstallDelete]\nType: filesandordirs; Name: \"{userappdata}", script)

    def test_linux_packages_keep_cli_arguments_and_user_data_external(self) -> None:
        app_run = (PROJECT_ROOT / "packaging" / "linux" / "AppRun").read_text(
            encoding="utf-8"
        )
        cli = (PROJECT_ROOT / "packaging" / "linux" / "photo-card-organizer-cli").read_text(
            encoding="utf-8"
        )
        build = (PROJECT_ROOT / "packaging" / "linux" / "build-packages.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('"$@"', app_run)
        self.assertIn('"$@"', cli)
        self.assertIn("/usr/bin/photo-card-organizer", build)
        self.assertIn("PhotoCardOrganizer-256.png", build)
        self.assertIn("sha256sum", build)
        self.assertNotIn("PhotoCardOrganizer/config.json", build)
        self.assertNotIn("$HOME/.config/PhotoCardOrganizer", build)

    def test_release_metadata_schema_is_current(self) -> None:
        metadata = {
            "format": "photo-card-organizer-release",
            "version": __version__,
            "config_schema": CURRENT_CONFIG_SCHEMA,
        }
        encoded = json.dumps(metadata)
        self.assertEqual(CURRENT_CONFIG_SCHEMA, json.loads(encoded)["config_schema"])

    def test_user_guide_version_is_derived_and_packaged(self) -> None:
        guide_source = (PROJECT_ROOT / "docs" / "build_user_guide.py").read_text(
            encoding="utf-8"
        )
        bundle_source = (PROJECT_ROOT / "packaging" / "build_bundle.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('ROOT / "pyproject.toml"', guide_source)
        self.assertIn('f"PhotoCardOrganizer-{VERSION}-User-Guide.pdf"', guide_source)
        self.assertNotIn("VERSION 0.1", guide_source)
        self.assertIn("build_user_guide(version)", bundle_source)
        self.assertIn('"user_guide": user_guide.name', bundle_source)
        self.assertIn('bundle / "CHANGELOG.md"', bundle_source)

    def test_generated_release_icons_include_master_desktop_and_ico_sizes(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        module = runpy.run_path(
            str(PROJECT_ROOT / "packaging" / "build_bundle.py")
        )
        with tempfile.TemporaryDirectory() as temporary:
            png, desktop, ico, version_file = module["generate_assets"](
                Path(temporary), __version__
            )

            with (
                Image.open(png) as master_image,
                Image.open(desktop) as desktop_image,
                Image.open(ico) as ico_image,
            ):
                self.assertEqual((1024, 1024), master_image.size)
                self.assertEqual((256, 256), desktop_image.size)
                self.assertEqual(
                    {
                        (16, 16),
                        (24, 24),
                        (32, 32),
                        (48, 48),
                        (64, 64),
                        (128, 128),
                        (256, 256),
                    },
                    ico_image.info["sizes"],
                )
            self.assertTrue(version_file.is_file())

    def test_taskbar_identity_is_configured_before_qt_application(self) -> None:
        window_source = (
            PROJECT_ROOT / "photocard" / "qt_window.py"
        ).read_text(encoding="utf-8")
        run_gui_source = window_source[
            window_source.index("def run_gui(") :
        ]

        self.assertLess(
            run_gui_source.index("configure_windows_taskbar_identity()"),
            run_gui_source.index("QApplication.instance()"),
        )
        self.assertIn(
            'WINDOWS_APP_USER_MODEL_ID = "PhotoCardOrganizer.Desktop"',
            (PROJECT_ROOT / "photocard" / "qt_theme.py").read_text(
                encoding="utf-8"
            ),
        )


if __name__ == "__main__":
    unittest.main()
