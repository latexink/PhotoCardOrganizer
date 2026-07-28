from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import __version__


@dataclass(frozen=True)
class InstallationInfo:
    kind: str
    root: Path
    status: str
    detail: str
    verified: bool
    install_target: Path | None = None
    uninstall_target: Path | None = None


def _packaged_installation() -> InstallationInfo:
    executable = Path(sys.executable).resolve()
    if os.name == "nt":
        root = executable.parent
        uninstallers = sorted(root.glob("unins*.exe"))
        return InstallationInfo(
            kind="windows-package",
            root=root,
            status=f"Installed Windows package {__version__}",
            detail=(
                "PhotoCardOrganizer.exe launches the application. Run a versioned "
                "PhotoCardOrganizer-Installer file to upgrade or repair, use Uninstall "
                "below, or use Windows Installed apps. Settings, card profiles, and "
                "transfer records are preserved by default."
            ),
            verified=executable.is_file(),
            uninstall_target=uninstallers[0] if uninstallers else None,
        )

    appimage = os.environ.get("APPIMAGE", "").strip()
    if appimage:
        image_path = Path(appimage).expanduser()
        return InstallationInfo(
            kind="appimage",
            root=image_path.parent,
            status=f"AppImage release {__version__}",
            detail=(
                "Replace this AppImage with a newer release to update it. Your configuration and "
                "transfer records remain in their user data locations."
            ),
            verified=image_path.is_file(),
        )

    package_kind = os.environ.get("PHOTO_CARD_ORGANIZER_PACKAGE_KIND", "").strip().lower()
    if package_kind == "deb":
        return InstallationInfo(
            kind="deb",
            root=executable.parent,
            status=f"Debian package {__version__}",
            detail=(
                "Install a newer .deb with your package manager to upgrade. Removing the package "
                "does not remove per-user settings or transfer records."
            ),
            verified=executable.is_file(),
        )

    return InstallationInfo(
        kind="packaged",
        root=executable.parent,
        status=f"Packaged release {__version__}",
        detail="Use the package supplied for this operating system to upgrade or uninstall.",
        verified=executable.is_file(),
    )


def detect_installation(project_root: Path) -> InstallationInfo:
    if bool(getattr(sys, "frozen", False)):
        return _packaged_installation()

    project_root = project_root.resolve()
    if os.name == "nt":
        python_path = project_root / ".venv" / "Scripts" / "python.exe"
        install_target = project_root / "install.bat"
        uninstall_target = project_root / "uninstall.bat"
    else:
        python_path = project_root / ".venv" / "bin" / "python"
        install_target = project_root / "install.sh"
        uninstall_target = project_root / "uninstall.sh"
    managed_exists = (project_root / ".venv").exists()
    verified = False
    if python_path.is_file():
        try:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            check = subprocess.run(
                [str(python_path), "-c", "import photocard, PySide6"],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=flags,
                check=False,
            )
            verified = check.returncode == 0
        except (OSError, subprocess.SubprocessError):
            verified = False

    if verified:
        status = f"Verified developer installation {__version__}"
        detail = "The managed virtual environment can be updated, repaired, or removed."
    elif managed_exists:
        status = "Incomplete developer installation detected"
        detail = "Run repair to rebuild and verify the managed virtual environment."
    else:
        status = "No developer installation detected"
        detail = "Run install to create and verify the managed virtual environment."
    return InstallationInfo(
        kind="developer",
        root=project_root,
        status=status,
        detail=detail,
        verified=verified,
        install_target=install_target if install_target.is_file() else None,
        uninstall_target=(
            uninstall_target if managed_exists and uninstall_target.is_file() else None
        ),
    )
