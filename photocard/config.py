from __future__ import annotations

import copy
import json
import os
import platform
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__

CURRENT_CONFIG_SCHEMA = 5
DEFAULT_USER_AGENT = (
    f"PhotoCardOrganizer/{'.'.join(__version__.split('.')[:2])}"
)


DEFAULT_CONFIG: dict[str, Any] = {
    "schema": CURRENT_CONFIG_SCHEMA,
    "destination_root": "~/Pictures/Photo Card Library",
    "default_library_id": "",
    "library_destinations": [],
    "identification": {
        "folder_name": ".photocard",
        "identity_filename": "identity.json",
        "history_folder_name": "transfers",
        "history_folder_segments": ["{date:%Y}", "{date:%Y-%m}"],
        "session_filename_template": "{date:%Y-%m-%d_%H-%M-%S}_{card}_{computer}_{session}.jsonl",
        "checksum_filename_template": "{date:%Y-%m-%d_%H-%M-%S}_{card}_{computer}_{session}.{algorithm}",
        "shared_history": True,
        "require_log_before_source_delete": True,
        "configured_roots": [],
        "auto_detect": True,
        "linux_mount_bases": ["/media", "/run/media", "/mnt"],
    },
    "instance": {
        "id": "",
        "name": "",
        "library_id": "",
    },
    "local_history": {
        "enabled": True,
        "directory": "",
        "require_before_source_delete": True,
    },
    "card_profiles": [],
    "travel_libraries": [],
    "transfer_hubs": [],
    "digest_inboxes": [],
    "replica_destinations": [],
    "monitor": {
        "poll_seconds": 5,
        "settle_seconds": 3,
        "start_minimized": False,
    },
    "maintenance": {
        "backup_before_migration": True,
    },
    "organization": {
        "long_exposure_brackets": {
            "enabled": False,
            "folder_name": "Long Exposure Brackets",
            "minimum_group_size": 3,
            "maximum_gap_seconds": 30.0,
            "minimum_long_exposure_seconds": 1.0,
        },
    },
    "safety": {
        "default_action": "copy",
        "confirm_destructive_actions": True,
        "copy_verification": "size",
        "move_checksum_algorithm": "sha256",
        "replica_verification": "sha256",
        "minimum_destination_free_percent": 10.0,
        "minimum_destination_free_gb": 5.0,
        "warn_source_free_percent": 10.0,
        "conflict_policy": "rename",
        "exact_duplicate_policy": "rename",
        "conflict_filename_appendage": "_{number}",
        "conflict_folder": "Conflicts",
        "manual_conflict_prompt": True,
        "manual_duplicate_prompt": False,
        "space_policy": "fallback_then_block",
        "manual_space_prompt": True,
        "fallback_destination_roots": [],
        "file_error_policy": "retry_then_continue",
        "manual_error_prompt": True,
        "io_retry_count": 2,
        "io_retry_delay_seconds": 1.0,
    },
    "location": {
        "online_place_names": False,
        "provider": "nominatim",
        "user_agent": DEFAULT_USER_AGENT,
        "request_timeout_seconds": 8,
        "coordinate_precision": 4,
    },
    "media_rules": {
        "photo": {
            "enabled": True,
            "extensions": [".jpg", ".jpeg", ".jpe", ".tif", ".tiff", ".heic"],
            "folder_segments": ["Photos", "{date:%Y}", "{date:%Y-%m-%d}", "{camera}"],
            "filename_template": "{original}",
        },
        "raw": {
            "enabled": True,
            "extensions": [
                ".3fr", ".arw", ".cr2", ".cr3", ".dng", ".erf", ".fff",
                ".iiq", ".kdc", ".mef", ".mos", ".mrw", ".nef", ".nrw",
                ".orf", ".pef", ".raf", ".raw", ".rw2", ".rwl", ".sr2",
                ".srf", ".x3f",
            ],
            "folder_segments": ["RAW", "{date:%Y}", "{date:%Y-%m-%d}", "{camera}"],
            "filename_template": "{original}",
        },
        "video": {
            "enabled": True,
            "extensions": [".3gp", ".avi", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mts"],
            "folder_segments": ["Videos", "{date:%Y}", "{date:%Y-%m-%d}", "{camera}"],
            "filename_template": "{original}",
        },
        "sidecar": {
            "enabled": True,
            "extensions": [".aae", ".dop", ".pp3", ".thm", ".xmp"],
            "folder_segments": ["Sidecars", "{date:%Y}", "{date:%Y-%m-%d}", "{camera}"],
            "filename_template": "{original}",
        },
    },
}


def config_schema(config: dict[str, Any]) -> int:
    value = config.get("schema", 1)
    try:
        schema = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("The configuration schema must be an integer.") from exc
    if schema < 1:
        raise ValueError("The configuration schema must be at least 1.")
    return schema


def migrate_config_data(config: dict[str, Any]) -> tuple[dict[str, Any], int]:
    migrated = copy.deepcopy(config)
    original_schema = config_schema(migrated)
    if original_schema > CURRENT_CONFIG_SCHEMA:
        raise ValueError(
            f"This configuration uses schema {original_schema}, but this version supports "
            f"up to schema {CURRENT_CONFIG_SCHEMA}. Install a newer Photo Card Organizer release."
        )

    schema = original_schema
    while schema < CURRENT_CONFIG_SCHEMA:
        if schema == 1:
            maintenance = migrated.get("maintenance")
            if not isinstance(maintenance, dict):
                maintenance = {}
                migrated["maintenance"] = maintenance
            maintenance.setdefault("backup_before_migration", True)
            schema = 2
        elif schema == 2:
            migrated.setdefault("travel_libraries", [])
            schema = 3
        elif schema == 3:
            migrated.setdefault("digest_inboxes", [])
            schema = 4
        elif schema == 4:
            destination_root = str(
                migrated.get(
                    "destination_root",
                    DEFAULT_CONFIG["destination_root"],
                )
            )
            library_id = str(
                migrated.get("instance", {}).get("library_id", "")
                or "main-library"
            )
            migrated.setdefault(
                "library_destinations",
                [
                    {
                        "id": library_id,
                        "name": Path(destination_root).expanduser().name
                        or "Main Library",
                        "root": destination_root,
                        "kind": "local",
                        "storage_profile": "auto",
                        "enabled": True,
                    }
                ],
            )
            migrated.setdefault("default_library_id", library_id)
            migrated.setdefault(
                "organization",
                copy.deepcopy(DEFAULT_CONFIG["organization"]),
            )
            schema = 5
        else:
            raise ValueError(f"No configuration migration is available from schema {schema}.")
        migrated["schema"] = schema
    return migrated, original_schema


def backup_config(config_path: Path, source_schema: int) -> Path:
    backup_directory = config_path.parent / "backups"
    backup_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_directory / f"config.schema-{source_schema}.{timestamp}.json"
    shutil.copy2(config_path, backup_path)
    return backup_path


def default_config_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "PhotoCardOrganizer" / "config.json"


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def normalize_card_profile(profile: dict[str, Any]) -> dict[str, Any] | None:
    card_id = str(profile.get("id", "")).strip()
    if not card_id:
        return None
    action = str(profile.get("action", "copy")).lower()
    if action not in {"copy", "move"}:
        action = "copy"
    sources = profile.get("source_folders", ["DCIM"])
    if not isinstance(sources, list):
        sources = ["DCIM"]
    source_folders = [
        str(folder).strip("/\\")
        for folder in sources
        if str(folder).strip("/\\")
    ]
    return {
        "id": card_id,
        "name": str(profile.get("name") or card_id).strip(),
        "last_root": str(Path(str(profile.get("last_root", ""))).expanduser()) if profile.get("last_root") else "",
        "action": action,
        "source_folders": source_folders or ["DCIM"],
        "destination_prefix": str(profile.get("destination_prefix", "")).strip("/\\"),
        "camera_name": str(profile.get("camera_name", "")).strip(),
        "enabled": bool(profile.get("enabled", True)),
        "delete_empty_folders_after_move": bool(profile.get("delete_empty_folders_after_move", False)),
        "updated_at": str(profile.get("updated_at", "")),
    }


def normalize_replica_destination(replica: dict[str, Any]) -> dict[str, Any] | None:
    name = str(replica.get("name", "")).strip()
    replica_id = str(replica.get("id", "")).strip() or re_slug(name)
    if not name or not replica_id:
        return None
    conflict_policy = str(replica.get("conflict_policy", "block")).lower()
    if conflict_policy not in {"block", "archive_and_replace"}:
        conflict_policy = "block"
    root = str(replica.get("root", "")).strip()
    return {
        "id": replica_id,
        "name": name,
        "root": str(Path(root).expanduser()) if root else "",
        "enabled": bool(replica.get("enabled", True)),
        "required": bool(replica.get("required", True)),
        "include_history": bool(replica.get("include_history", True)),
        "conflict_policy": conflict_policy,
    }


def normalize_library_destination(
    library: dict[str, Any],
) -> dict[str, Any] | None:
    name = str(library.get("name", "")).strip()
    library_id = str(library.get("id", "")).strip() or re_slug(name)
    root = str(library.get("root", "")).strip()
    if not name or not library_id:
        return None
    kind = str(library.get("kind", "local")).strip().lower()
    if kind not in {"local", "network", "removable"}:
        kind = "local"
    storage_profile = str(
        library.get("storage_profile", "auto")
    ).strip().lower()
    if storage_profile not in {"auto", "ssd", "hdd", "network"}:
        storage_profile = "auto"
    return {
        "id": library_id,
        "name": name,
        "root": str(Path(root).expanduser()) if root else "",
        "kind": kind,
        "storage_profile": storage_profile,
        "enabled": bool(library.get("enabled", True)),
    }


def library_destination(
    config: dict[str, Any],
    library_id: str,
) -> dict[str, Any] | None:
    return next(
        (
            library
            for library in config.get("library_destinations", [])
            if str(library.get("id", "")) == str(library_id)
        ),
        None,
    )


def select_library_destination(
    config: dict[str, Any],
    library_id: str,
) -> dict[str, Any]:
    selected = library_destination(config, library_id)
    if selected is None:
        raise ValueError("The selected library destination is no longer configured.")
    if not selected.get("enabled", True):
        raise ValueError("Enable the selected library destination before using it.")
    root = str(selected.get("root", "")).strip()
    if not root:
        raise ValueError("The selected library destination has no folder.")
    config["default_library_id"] = str(selected["id"])
    config["destination_root"] = root
    instance = config.setdefault("instance", {})
    instance["library_id"] = str(selected["id"])
    return config


def normalize_travel_library(library: dict[str, Any]) -> dict[str, Any] | None:
    name = str(library.get("name", "")).strip()
    library_id = str(library.get("id", "")).strip() or re_slug(name)
    if not name or not library_id:
        return None
    root = str(library.get("root", "")).strip()
    return {
        "id": library_id,
        "name": name,
        "root": str(Path(root).expanduser()) if root else "",
        "enabled": bool(library.get("enabled", True)),
        "include_subfolders": bool(library.get("include_subfolders", True)),
        "destination_prefix": str(library.get("destination_prefix", "")).strip("/\\"),
        "camera_name": str(library.get("camera_name", "")).strip(),
    }


def normalize_transfer_hub(hub: dict[str, Any]) -> dict[str, Any] | None:
    name = str(hub.get("name", "")).strip()
    hub_id = str(hub.get("id", "")).strip() or re_slug(name)
    if not name or not hub_id:
        return None
    role = str(hub.get("role", "catch")).lower()
    if role not in {"publish", "catch"}:
        role = "catch"
    root = str(hub.get("root", "")).strip()
    conflict_policy = str(hub.get("conflict_policy", "block")).lower()
    if conflict_policy not in {"block", "archive_and_replace"}:
        conflict_policy = "block"
    return {
        "id": hub_id,
        "name": name,
        "root": str(Path(root).expanduser()) if root else "",
        "enabled": bool(hub.get("enabled", True)),
        "role": role,
        "producer_channel": str(hub.get("producer_channel", "")).strip(),
        "required": bool(hub.get("required", False)),
        "auto_catch": bool(hub.get("auto_catch", False)),
        "poll_seconds": max(10.0, min(86400.0, float(hub.get("poll_seconds", 60.0)))),
        "write_receipts": bool(hub.get("write_receipts", True)),
        "conflict_policy": conflict_policy,
    }


def normalize_digest_inbox(inbox: dict[str, Any]) -> dict[str, Any] | None:
    name = str(inbox.get("name", "")).strip()
    inbox_id = str(inbox.get("id", "")).strip() or re_slug(name)
    if not name or not inbox_id:
        return None
    root = str(inbox.get("root", "")).strip()
    action = str(inbox.get("action", "copy")).lower()
    if action not in {"copy", "move"}:
        action = "copy"
    return {
        "id": inbox_id,
        "name": name,
        "root": str(Path(root).expanduser()) if root else "",
        "enabled": bool(inbox.get("enabled", True)),
        "include_subfolders": bool(inbox.get("include_subfolders", True)),
        "destination_prefix": str(
            inbox.get("destination_prefix", "")
        ).strip("/\\"),
        "camera_name": str(inbox.get("camera_name", "")).strip(),
        "action": action,
        "auto_digest": bool(
            inbox.get("auto_digest", False) and action == "copy"
        ),
        "poll_seconds": max(
            10.0,
            min(86400.0, float(inbox.get("poll_seconds", 60.0))),
        ),
    }


def re_slug(value: str) -> str:
    cleaned = "-".join(part for part in "".join(char.lower() if char.isalnum() else " " for char in value).split())
    return cleaned or uuid.uuid4().hex[:12]


def get_card_profile(config: dict[str, Any], card_id: str) -> dict[str, Any] | None:
    return next(
        (profile for profile in config.get("card_profiles", []) if profile.get("id") == card_id),
        None,
    )


def upsert_card_profile(config: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    candidate = dict(profile)
    candidate["updated_at"] = datetime.now(timezone.utc).isoformat()
    normalized = normalize_card_profile(candidate)
    if normalized is None:
        raise ValueError("A retained card profile requires a stable card ID.")
    profiles = [item for item in config.get("card_profiles", []) if item.get("id") != normalized["id"]]
    profiles.append(normalized)
    config["card_profiles"] = profiles
    return normalized


def remove_card_profile(config: dict[str, Any], card_id: str) -> None:
    config["card_profiles"] = [
        profile for profile in config.get("card_profiles", []) if profile.get("id") != card_id
    ]


def normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    migrated, _original_schema = migrate_config_data(config)
    normalized = deep_merge(DEFAULT_CONFIG, migrated)
    normalized["schema"] = CURRENT_CONFIG_SCHEMA
    normalized["destination_root"] = str(
        Path(normalized["destination_root"]).expanduser()
    )

    identification = normalized["identification"]
    for key, default in (
        ("folder_name", ".photocard"),
        ("identity_filename", "identity.json"),
        ("history_folder_name", "transfers"),
    ):
        name = str(identification.get(key, default)).strip()
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError(f"identification.{key} must be a single name, not a path.")
        identification[key] = name
    identification["history_folder_segments"] = [
        str(segment) for segment in identification.get("history_folder_segments", []) if str(segment)
    ]
    session_template = str(
        identification.get(
            "session_filename_template",
            "{date:%Y-%m-%d_%H-%M-%S}_{card}_{computer}_{session}.jsonl",
        )
    ).strip()
    if not session_template or "/" in session_template or "\\" in session_template:
        raise ValueError("identification.session_filename_template must be a filename template.")
    identification["session_filename_template"] = session_template
    checksum_template = str(
        identification.get(
            "checksum_filename_template",
            "{date:%Y-%m-%d_%H-%M-%S}_{card}_{computer}_{session}.{algorithm}",
        )
    ).strip()
    if not checksum_template or "/" in checksum_template or "\\" in checksum_template:
        raise ValueError("identification.checksum_filename_template must be a filename template.")
    identification["checksum_filename_template"] = checksum_template
    identification["configured_roots"] = [
        str(Path(root).expanduser()) for root in identification.get("configured_roots", []) if str(root).strip()
    ]

    instance = normalized["instance"]
    instance["id"] = str(instance.get("id") or uuid.uuid4())
    instance["library_id"] = str(instance.get("library_id") or uuid.uuid4())
    instance["name"] = str(instance.get("name") or platform.node() or "Photo organizer")

    local_history = normalized["local_history"]
    if str(local_history.get("directory", "")).strip():
        local_history["directory"] = str(Path(local_history["directory"]).expanduser())
    else:
        local_history["directory"] = ""

    libraries: list[dict[str, Any]] = []
    library_ids: set[str] = set()
    library_roots: set[str] = set()
    for library in normalized.get("library_destinations", []):
        if not isinstance(library, dict):
            continue
        clean_library = normalize_library_destination(library)
        if clean_library is None or clean_library["id"] in library_ids:
            continue
        if clean_library["root"]:
            root_key = os.path.normcase(
                os.path.abspath(clean_library["root"])
            )
            if root_key in library_roots:
                continue
            library_roots.add(root_key)
        library_ids.add(clean_library["id"])
        libraries.append(clean_library)
    if not libraries:
        library_id = str(
            normalized.get("default_library_id", "")
            or instance.get("library_id", "")
            or "main-library"
        )
        libraries.append(
            {
                "id": library_id,
                "name": Path(normalized["destination_root"]).name
                or "Main Library",
                "root": normalized["destination_root"],
                "kind": "local",
                "storage_profile": "auto",
                "enabled": True,
            }
        )
    default_library_id = str(
        normalized.get("default_library_id", "")
    )
    selected_library = next(
        (
            library
            for library in libraries
            if library["id"] == default_library_id
            and library.get("enabled", True)
            and library.get("root")
        ),
        None,
    )
    if selected_library is None:
        selected_library = next(
            (
                library
                for library in libraries
                if library.get("enabled", True) and library.get("root")
            ),
            libraries[0],
        )
    normalized["library_destinations"] = libraries
    normalized["default_library_id"] = str(selected_library["id"])
    if selected_library.get("root"):
        normalized["destination_root"] = str(selected_library["root"])
    instance["library_id"] = str(selected_library["id"])

    profiles: list[dict[str, Any]] = []
    profile_ids: set[str] = set()
    for profile in normalized.get("card_profiles", []):
        if not isinstance(profile, dict):
            continue
        clean_profile = normalize_card_profile(profile)
        if clean_profile is None or clean_profile["id"] in profile_ids:
            continue
        profile_ids.add(clean_profile["id"])
        profiles.append(clean_profile)
    normalized["card_profiles"] = profiles

    travel_libraries: list[dict[str, Any]] = []
    travel_ids: set[str] = set()
    for library in normalized.get("travel_libraries", []):
        if not isinstance(library, dict):
            continue
        clean_library = normalize_travel_library(library)
        if clean_library is None or clean_library["id"] in travel_ids:
            continue
        travel_ids.add(clean_library["id"])
        travel_libraries.append(clean_library)
    normalized["travel_libraries"] = travel_libraries

    transfer_hubs: list[dict[str, Any]] = []
    hub_ids: set[str] = set()
    for hub in normalized.get("transfer_hubs", []):
        if not isinstance(hub, dict):
            continue
        clean_hub = normalize_transfer_hub(hub)
        if clean_hub is None or clean_hub["id"] in hub_ids:
            continue
        hub_ids.add(clean_hub["id"])
        transfer_hubs.append(clean_hub)
    normalized["transfer_hubs"] = transfer_hubs

    digest_inboxes: list[dict[str, Any]] = []
    digest_ids: set[str] = set()
    for inbox in normalized.get("digest_inboxes", []):
        if not isinstance(inbox, dict):
            continue
        clean_inbox = normalize_digest_inbox(inbox)
        if clean_inbox is None or clean_inbox["id"] in digest_ids:
            continue
        digest_ids.add(clean_inbox["id"])
        digest_inboxes.append(clean_inbox)
    normalized["digest_inboxes"] = digest_inboxes

    replicas: list[dict[str, Any]] = []
    replica_ids: set[str] = set()
    replica_roots: set[str] = set()
    primary_root = os.path.normcase(os.path.abspath(normalized["destination_root"]))
    for replica in normalized.get("replica_destinations", []):
        if not isinstance(replica, dict):
            continue
        clean_replica = normalize_replica_destination(replica)
        if clean_replica is None or clean_replica["id"] in replica_ids:
            continue
        if clean_replica["root"]:
            replica_root = os.path.normcase(os.path.abspath(clean_replica["root"]))
            if replica_root == primary_root or replica_root in replica_roots:
                continue
            replica_roots.add(replica_root)
        replica_ids.add(clean_replica["id"])
        replicas.append(clean_replica)
    normalized["replica_destinations"] = replicas

    safety = normalized["safety"]
    if safety["default_action"] not in {"copy", "move"}:
        safety["default_action"] = "copy"
    if safety["copy_verification"] not in {"size", "sha256", "sha512", "blake2b"}:
        safety["copy_verification"] = "size"
    if safety["move_checksum_algorithm"] not in {"sha256", "sha512", "blake2b"}:
        safety["move_checksum_algorithm"] = "sha256"
    if safety["replica_verification"] not in {"sha256", "sha512", "blake2b"}:
        safety["replica_verification"] = "sha256"
    if safety["conflict_policy"] not in {"ask", "rename", "conflict_folder", "skip"}:
        safety["conflict_policy"] = "rename"
    if safety["exact_duplicate_policy"] not in {"rename", "conflict_folder", "skip"}:
        safety["exact_duplicate_policy"] = "rename"
    appendage = str(safety.get("conflict_filename_appendage", "_{number}"))
    safety["conflict_filename_appendage"] = appendage or "_{number}"
    conflict_folder = str(safety.get("conflict_folder", "Conflicts")).strip("/\\")
    if not conflict_folder or ".." in Path(conflict_folder).parts or Path(conflict_folder).is_absolute():
        conflict_folder = "Conflicts"
    safety["conflict_folder"] = conflict_folder
    if safety["space_policy"] not in {"block", "fallback_then_block", "continue_below_reserve"}:
        safety["space_policy"] = "fallback_then_block"
    if safety["file_error_policy"] not in {"continue", "stop_card", "retry_then_continue"}:
        safety["file_error_policy"] = "retry_then_continue"
    safety["fallback_destination_roots"] = [
        str(Path(root).expanduser())
        for root in safety.get("fallback_destination_roots", [])
        if str(root).strip()
    ]
    safety["io_retry_count"] = max(0, min(10, int(safety.get("io_retry_count", 2))))
    safety["io_retry_delay_seconds"] = max(0.0, min(30.0, float(safety.get("io_retry_delay_seconds", 1.0))))

    maintenance = normalized["maintenance"]
    maintenance["backup_before_migration"] = bool(
        maintenance.get("backup_before_migration", True)
    )

    organization = normalized["organization"]
    brackets = organization["long_exposure_brackets"]
    brackets["enabled"] = bool(brackets.get("enabled", False))
    folder_name = str(
        brackets.get("folder_name", "Long Exposure Brackets")
    ).strip("/\\")
    if (
        not folder_name
        or Path(folder_name).is_absolute()
        or ".." in Path(folder_name).parts
    ):
        folder_name = "Long Exposure Brackets"
    brackets["folder_name"] = folder_name
    brackets["minimum_group_size"] = max(
        2,
        min(12, int(brackets.get("minimum_group_size", 3))),
    )
    brackets["maximum_gap_seconds"] = max(
        0.5,
        min(
            600.0,
            float(brackets.get("maximum_gap_seconds", 30.0)),
        ),
    )
    brackets["minimum_long_exposure_seconds"] = max(
        0.0,
        min(
            3600.0,
            float(
                brackets.get("minimum_long_exposure_seconds", 1.0)
            ),
        ),
    )

    for rule in normalized["media_rules"].values():
        extensions = []
        for extension in rule.get("extensions", []):
            extension = str(extension).strip().lower()
            if extension and not extension.startswith("."):
                extension = f".{extension}"
            if extension and extension not in extensions:
                extensions.append(extension)
        rule["extensions"] = extensions
        rule["folder_segments"] = [str(segment) for segment in rule.get("folder_segments", []) if str(segment)]
        rule["filename_template"] = str(rule.get("filename_template", "{original}")) or "{original}"
    return normalized


def load_config(path: Path | str | None = None, create: bool = True) -> tuple[dict[str, Any], Path]:
    config_path = Path(path).expanduser() if path else default_config_path()
    if not config_path.exists():
        config = normalize_config({})
        if create:
            save_config(config, config_path)
        return config, config_path
    with config_path.open("r", encoding="utf-8-sig") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        raise ValueError("The configuration root must be a JSON object.")
    migrated, source_schema = migrate_config_data(loaded)
    normalized = normalize_config(migrated)
    if create and source_schema < CURRENT_CONFIG_SCHEMA:
        if normalized["maintenance"].get("backup_before_migration", True):
            backup_config(config_path, source_schema)
        save_config(normalized, config_path)
    return normalized, config_path


def save_config(config: dict[str, Any], path: Path | str) -> Path:
    config_path = Path(path).expanduser()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_config(config)
    temporary = config_path.with_suffix(config_path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(normalized, handle, indent=2, sort_keys=False)
        handle.write("\n")
    os.replace(temporary, config_path)
    return config_path


CLIENT_SETTINGS_FORMAT = "photo-card-organizer-client-settings"


def build_client_settings(config: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_config(config)
    identification = copy.deepcopy(normalized["identification"])
    identification.pop("configured_roots", None)
    identification.pop("linux_mount_bases", None)
    safety = copy.deepcopy(normalized["safety"])
    safety["fallback_destination_roots"] = []
    local_history = copy.deepcopy(normalized["local_history"])
    local_history["directory"] = ""
    profiles = []
    for profile in normalized["card_profiles"]:
        portable_profile = copy.deepcopy(profile)
        portable_profile["last_root"] = ""
        profiles.append(portable_profile)
    replicas = []
    for replica in normalized["replica_destinations"]:
        portable_replica = copy.deepcopy(replica)
        portable_replica["root"] = ""
        portable_replica["enabled"] = False
        replicas.append(portable_replica)
    travel_libraries = []
    for library in normalized["travel_libraries"]:
        portable_library = copy.deepcopy(library)
        portable_library["root"] = ""
        portable_library["enabled"] = False
        travel_libraries.append(portable_library)
    transfer_hubs = []
    for hub in normalized["transfer_hubs"]:
        portable_hub = copy.deepcopy(hub)
        portable_hub["root"] = ""
        portable_hub["enabled"] = False
        transfer_hubs.append(portable_hub)
    digest_inboxes = []
    for inbox in normalized["digest_inboxes"]:
        portable_inbox = copy.deepcopy(inbox)
        portable_inbox["root"] = ""
        portable_inbox["enabled"] = False
        portable_inbox["auto_digest"] = False
        digest_inboxes.append(portable_inbox)
    return {
        "format": CLIENT_SETTINGS_FORMAT,
        "schema": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "settings": {
            "identification": identification,
            "monitor": copy.deepcopy(normalized["monitor"]),
            "safety": safety,
            "location": copy.deepcopy(normalized["location"]),
            "media_rules": copy.deepcopy(normalized["media_rules"]),
            "local_history": local_history,
            "card_profiles": profiles,
            "travel_libraries": travel_libraries,
            "transfer_hubs": transfer_hubs,
            "digest_inboxes": digest_inboxes,
            "replica_destinations": replicas,
            "library_destinations": [
                {
                    **copy.deepcopy(library),
                    "root": "",
                    "enabled": False,
                }
                for library in normalized["library_destinations"]
            ],
            "default_library_id": normalized["default_library_id"],
            "organization": copy.deepcopy(normalized["organization"]),
        },
    }


def export_client_settings(config: dict[str, Any], path: Path | str) -> Path:
    export_path = Path(path).expanduser()
    export_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = export_path.with_suffix(export_path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(build_client_settings(config), handle, indent=2)
        handle.write("\n")
    os.replace(temporary, export_path)
    return export_path


def import_client_settings(config: dict[str, Any], path: Path | str) -> dict[str, Any]:
    import_path = Path(path).expanduser()
    with import_path.open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or payload.get("format") != CLIENT_SETTINGS_FORMAT:
        raise ValueError("This is not a Photo Card Organizer client-settings file.")
    settings = payload.get("settings")
    if not isinstance(settings, dict):
        raise ValueError("The client-settings file has no settings object.")

    merged = copy.deepcopy(config)
    local_history_directory = str(merged.get("local_history", {}).get("directory", ""))
    fallback_roots = copy.deepcopy(
        merged.get("safety", {}).get("fallback_destination_roots", [])
    )
    for key in (
        "identification",
        "monitor",
        "safety",
        "location",
        "media_rules",
        "local_history",
        "organization",
    ):
        incoming = settings.get(key)
        if isinstance(incoming, dict):
            merged[key] = deep_merge(merged.get(key, {}), incoming)
    merged["local_history"]["directory"] = local_history_directory
    merged["safety"]["fallback_destination_roots"] = fallback_roots

    existing_profiles = {profile["id"]: profile for profile in merged.get("card_profiles", [])}
    for profile in settings.get("card_profiles", []):
        if not isinstance(profile, dict) or not profile.get("id"):
            continue
        existing = existing_profiles.get(str(profile["id"]), {})
        imported = dict(profile)
        imported["last_root"] = existing.get("last_root", "")
        existing_profiles[str(profile["id"])] = imported
    merged["card_profiles"] = list(existing_profiles.values())

    existing_travel_libraries = {
        library["id"]: library for library in merged.get("travel_libraries", [])
    }
    for library in settings.get("travel_libraries", []):
        if not isinstance(library, dict) or not library.get("id"):
            continue
        existing = existing_travel_libraries.get(str(library["id"]), {})
        imported = dict(library)
        imported["root"] = existing.get("root", "")
        imported["enabled"] = bool(existing.get("enabled", False) and imported["root"])
        existing_travel_libraries[str(library["id"])] = imported
    merged["travel_libraries"] = list(existing_travel_libraries.values())

    existing_transfer_hubs = {
        hub["id"]: hub for hub in merged.get("transfer_hubs", [])
    }
    for hub in settings.get("transfer_hubs", []):
        if not isinstance(hub, dict) or not hub.get("id"):
            continue
        existing = existing_transfer_hubs.get(str(hub["id"]), {})
        imported = dict(hub)
        imported["root"] = existing.get("root", "")
        imported["enabled"] = bool(existing.get("enabled", False) and imported["root"])
        existing_transfer_hubs[str(hub["id"])] = imported
    merged["transfer_hubs"] = list(existing_transfer_hubs.values())

    existing_digest_inboxes = {
        inbox["id"]: inbox for inbox in merged.get("digest_inboxes", [])
    }
    for inbox in settings.get("digest_inboxes", []):
        if not isinstance(inbox, dict) or not inbox.get("id"):
            continue
        existing = existing_digest_inboxes.get(str(inbox["id"]), {})
        imported = dict(inbox)
        imported["root"] = existing.get("root", "")
        imported["enabled"] = bool(
            existing.get("enabled", False) and imported["root"]
        )
        imported["auto_digest"] = bool(
            imported["enabled"] and existing.get("auto_digest", False)
        )
        existing_digest_inboxes[str(inbox["id"])] = imported
    merged["digest_inboxes"] = list(existing_digest_inboxes.values())

    existing_libraries = {
        library["id"]: library
        for library in merged.get("library_destinations", [])
    }
    for library in settings.get("library_destinations", []):
        if not isinstance(library, dict) or not library.get("id"):
            continue
        existing = existing_libraries.get(str(library["id"]), {})
        imported = dict(library)
        imported["root"] = existing.get("root", "")
        imported["enabled"] = bool(
            existing.get("enabled", False) and imported["root"]
        )
        existing_libraries[str(library["id"])] = imported
    merged["library_destinations"] = list(existing_libraries.values())

    existing_replicas = {replica["id"]: replica for replica in merged.get("replica_destinations", [])}
    for replica in settings.get("replica_destinations", []):
        if not isinstance(replica, dict) or not replica.get("id"):
            continue
        existing = existing_replicas.get(str(replica["id"]), {})
        imported = dict(replica)
        imported["root"] = existing.get("root", "")
        imported["enabled"] = bool(existing.get("enabled", False) and imported["root"])
        existing_replicas[str(replica["id"])] = imported
    merged["replica_destinations"] = list(existing_replicas.values())
    return normalize_config(merged)
