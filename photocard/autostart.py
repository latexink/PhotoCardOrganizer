from __future__ import annotations

import os
import sys
from pathlib import Path


def _windows_path() -> Path:
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "PhotoCardOrganizer.cmd"


def _linux_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "autostart" / "photo-card-organizer.desktop"


def _desktop_quote(value: object) -> str:
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return f'"{escaped}"'


def runtime_command(config_path: Path) -> tuple[object, ...]:
    appimage = os.environ.get("APPIMAGE", "").strip() if os.name != "nt" else ""
    if appimage:
        return (appimage, "--service", "--config", config_path)
    if bool(getattr(sys, "frozen", False)):
        return (sys.executable, "--service", "--config", config_path)
    return (sys.executable, "-m", "photocard", "--service", "--config", config_path)


def _batch_quote(value: object) -> str:
    escaped = str(value).replace("%", "%%").replace('"', '""')
    return f'"{escaped}"'


def install_autostart(config_path: Path) -> Path:
    command = runtime_command(config_path)
    if os.name == "nt":
        target = _windows_path()
        content = f"@echo off\r\nstart \"\" /min {' '.join(_batch_quote(part) for part in command)}\r\n"
    else:
        target = _linux_path()
        desktop_command = " ".join(_desktop_quote(part) for part in command)
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Photo Card Organizer\n"
            f"Exec={desktop_command}\n"
            "Terminal=false\n"
            "X-GNOME-Autostart-enabled=true\n"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, target)
    return target


def remove_autostart() -> Path:
    target = _windows_path() if os.name == "nt" else _linux_path()
    target.unlink(missing_ok=True)
    return target
