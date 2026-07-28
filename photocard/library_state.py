from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__


STATE_FOLDER_NAME = ".photocard-organizer"
LIBRARY_METADATA_FORMAT = "photo-card-organizer-library"
CURRENT_LIBRARY_SCHEMA = 1


@dataclass(frozen=True)
class LibraryUpgradeResult:
    metadata_path: Path
    created: bool = False
    upgraded_from: int | None = None
    backup_path: Path | None = None
    migrated_export_sessions: int = 0


def state_directory(root: Path | str) -> Path:
    return Path(root).expanduser() / STATE_FOLDER_NAME


def metadata_path(root: Path | str) -> Path:
    return state_directory(root) / "library.json"


def read_library_metadata(
    root: Path | str,
) -> dict[str, Any] | None:
    path = metadata_path(root)
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    if (
        not isinstance(payload, dict)
        or payload.get("format") != LIBRARY_METADATA_FORMAT
    ):
        raise ValueError(f"Unsupported library metadata file: {path}")
    try:
        schema = int(payload.get("schema", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Library metadata schema is invalid: {path}"
        ) from exc
    if schema > CURRENT_LIBRARY_SCHEMA:
        raise ValueError(
            f"This library uses metadata schema {schema}, but this release "
            f"supports up to {CURRENT_LIBRARY_SCHEMA}. Upgrade the application."
        )
    return payload


def library_metadata_status(root: Path | str) -> str:
    path = metadata_path(root)
    if not path.exists():
        return "Not initialized"
    try:
        payload = read_library_metadata(root)
    except (OSError, ValueError, json.JSONDecodeError):
        return "Needs attention"
    if payload is None:
        return "Not initialized"
    schema = int(payload.get("schema", 0))
    if schema < CURRENT_LIBRARY_SCHEMA:
        return f"Upgrade available (schema {schema})"
    return f"Ready (schema {schema})"


def _backup_metadata(path: Path, schema: int) -> Path:
    backup_directory = path.parent / "backups"
    backup_directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = (
        backup_directory
        / f"library.schema-{schema}.{stamp}.json"
    )
    shutil.copy2(path, backup)
    return backup


def _available_destination(path: Path) -> Path:
    if not path.exists():
        return path
    for number in range(2, 100_000):
        candidate = path.with_name(
            f"{path.stem}_{number}{path.suffix}"
        )
        if not candidate.exists():
            return candidate
    raise OSError(f"Could not allocate a migration filename for {path.name}")


def migrate_export_sessions(root: Path | str) -> int:
    library_root = Path(root).expanduser()
    destination_directory = (
        state_directory(library_root) / "export-sessions"
    )
    candidates = sorted(
        path
        for path in library_root.glob(
            "PhotoCardOrganizer-export-*.jsonl"
        )
        if path.is_file()
    )
    if not candidates:
        return 0
    destination_directory.mkdir(parents=True, exist_ok=True)
    moved = 0
    for source in candidates:
        destination = _available_destination(
            destination_directory / source.name
        )
        os.replace(source, destination)
        moved += 1
    return moved


def upgrade_library_metadata(
    root: Path | str,
    *,
    library_id: str,
    name: str,
    migrate_sessions: bool = True,
) -> LibraryUpgradeResult:
    library_root = Path(root).expanduser()
    if not library_root.is_dir():
        raise ValueError(
            f"Library folder does not exist: {library_root}"
        )
    path = metadata_path(library_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    created = not path.exists()
    upgraded_from: int | None = None
    backup: Path | None = None
    now = datetime.now(timezone.utc).isoformat()

    if created:
        payload: dict[str, Any] = {
            "format": LIBRARY_METADATA_FORMAT,
            "schema": CURRENT_LIBRARY_SCHEMA,
            "library_id": str(library_id),
            "name": str(name),
            "created_at": now,
            "updated_at": now,
            "app_version": __version__,
            "migrations": [],
        }
    else:
        with path.open("r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
        if (
            not isinstance(payload, dict)
            or payload.get("format") != LIBRARY_METADATA_FORMAT
        ):
            raise ValueError(
                "The existing library.json is not a Photo Card Organizer "
                "metadata file and was left unchanged."
            )
        try:
            schema = int(payload.get("schema", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "The existing library metadata schema is invalid."
            ) from exc
        if schema > CURRENT_LIBRARY_SCHEMA:
            raise ValueError(
                f"This library uses metadata schema {schema}, but this release "
                f"supports up to {CURRENT_LIBRARY_SCHEMA}."
            )
        existing_library_id = str(
            payload.get("library_id", "")
        ).strip()
        if (
            existing_library_id
            and existing_library_id != str(library_id)
        ):
            raise ValueError(
                "This folder is already initialized as a different library. "
                "Change the configured destination instead of replacing its "
                "identity."
            )
        if schema < CURRENT_LIBRARY_SCHEMA:
            upgraded_from = schema
            backup = _backup_metadata(path, schema)
            migrations = payload.get("migrations")
            if not isinstance(migrations, list):
                migrations = []
            migrations.append(
                {
                    "from_schema": schema,
                    "to_schema": CURRENT_LIBRARY_SCHEMA,
                    "completed_at": now,
                    "app_version": __version__,
                }
            )
            payload["migrations"] = migrations
            payload["schema"] = CURRENT_LIBRARY_SCHEMA
        payload["library_id"] = str(library_id)
        payload["name"] = str(name)
        payload["updated_at"] = now
        payload["app_version"] = __version__

    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    migrated = (
        migrate_export_sessions(library_root)
        if migrate_sessions
        else 0
    )
    return LibraryUpgradeResult(
        metadata_path=path,
        created=created,
        upgraded_from=upgraded_from,
        backup_path=backup,
        migrated_export_sessions=migrated,
    )
