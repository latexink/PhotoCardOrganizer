from __future__ import annotations

import os
from pathlib import Path

from .models import CardMarker
from .templates import safe_segment
from .transfer_hub import effective_replica_destinations


MEDIA_LABELS = {
    "photo": "Photos",
    "raw": "RAW",
    "video": "Videos",
    "sidecar": "Sidecars",
}

LIBRARY_SUBFOLDER_HELP = (
    "Places this source inside an optional subfolder of the main library before "
    "media organization rules are applied. Example: Clients/Smith Wedding."
)

CAMERA_NAME_HELP = (
    "Camera make and model are read from EXIF automatically. Enter a value only to "
    "override missing or inconsistent EXIF camera names for the Camera folder rule."
)

ORGANIZATION_PRESETS: dict[str, list[str] | None] = {
    "Use current detailed rules": None,
    "Year then month": [
        "{date:%Y}",
        "{date:%m - %B}",
        "{date:%Y-%m-%d}",
    ],
    "Date then camera": ["{date:%Y}", "{date:%Y-%m-%d}", "{camera}"],
    "Camera then date": ["{camera}", "{date:%Y}", "{date:%Y-%m-%d}"],
    "Date only": ["{date:%Y}", "{date:%Y-%m-%d}"],
    "Media folder only": [],
    "None (no folders)": ["__none__"],
}

IMPORT_FOLDER_MODES: tuple[tuple[str, str], ...] = (
    ("Standard library organization", "standard"),
    ("Wedding", "wedding"),
    ("Client shoot", "client"),
    ("Trip", "trip"),
    ("Custom named folder", "custom"),
)


def import_destination_prefix(mode: str, name: str) -> str:
    mode = str(mode or "standard").strip().lower()
    if mode == "standard":
        return ""
    value = str(name or "").strip()
    if not value:
        raise ValueError("Enter an event or folder name.")
    if mode == "custom":
        candidate = Path(value)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(
                "A custom import folder must stay inside the selected library."
            )
        parts = [
            safe_segment(part)
            for part in candidate.parts
            if part not in {"", "."}
        ]
        if not parts:
            raise ValueError("Enter a custom import folder.")
        return str(Path(*parts))
    leaf = safe_segment(value)
    prefixes = {
        "wedding": Path("Events", "Weddings", leaf),
        "client": Path("Clients", leaf),
        "trip": Path("Events", "Trips", leaf),
    }
    if mode not in prefixes:
        raise ValueError("Choose a supported import folder type.")
    return str(prefixes[mode])


def format_bytes(value: int) -> str:
    number = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if number < 1024 or unit == "TB":
            return f"{number:.1f} {unit}"
        number /= 1024
    return f"{number:.1f} TB"


def path_key(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(str(Path(path).expanduser())))


def paths_overlap(first: Path, second: Path) -> bool:
    try:
        first = first.expanduser().resolve()
        second = second.expanduser().resolve()
    except OSError:
        return path_key(first) == path_key(second)
    return first == second or first in second.parents or second in first.parents


def preset_folder_segments(kind: str, preset: str, current: list[str]) -> list[str]:
    suffix = ORGANIZATION_PRESETS.get(preset)
    if suffix is None:
        return list(current)
    if suffix == ["__none__"]:
        return []
    return [MEDIA_LABELS[kind], *suffix]


def initial_import_summary(card: CardMarker, config: dict, organization: str) -> str:
    destination = Path(config["destination_root"]).expanduser()
    if card.destination_prefix:
        destination /= card.destination_prefix
    media = ", ".join(
        MEDIA_LABELS[kind]
        for kind, rule in config["media_rules"].items()
        if rule.get("enabled", True)
    ) or "None"
    replicas = [
        replica["name"]
        for replica in effective_replica_destinations(config)
        if replica.get("enabled", True) and replica.get("root")
    ]
    camera = card.camera_name or "EXIF make/model"
    if card.action == "move":
        algorithm = str(
            config["safety"].get(
                "move_checksum_algorithm", "sha256"
            )
        )
        checksum_label = {
            "sha256": "SHA-256",
            "sha512": "SHA-512",
            "blake2b": "BLAKE2b",
        }.get(algorithm, algorithm.upper())
        verification = (
            f"{checksum_label} checksum; "
            "delete source only after required copies and logs succeed"
        )
    else:
        algorithm = str(
            config["safety"].get("copy_verification", "size")
        )
        verification_label = {
            "size": "File-size check",
            "sha256": "SHA-256 checksum",
            "sha512": "SHA-512 checksum",
            "blake2b": "BLAKE2b checksum",
        }.get(algorithm, f"{algorithm.upper()} verification")
        verification = (
            f"{verification_label}; keep source files"
        )
    if card.source_type in {"folder", "digest", "travel", "hub"} and card.source_folders == (".",):
        source_scope = (
            "Selected folder and subfolders"
            if card.recursive
            else "Selected folder only"
        )
    else:
        source_scope = ", ".join(card.source_folders)
    return "\n".join(
        (
            f"Source: {card.root}",
            f"Scan scope: {source_scope}",
            f"Destination: {destination}",
            f"Operation: {card.action.capitalize()}",
            f"Media: {media}",
            f"Organization: {organization}",
            f"Camera label: {camera}",
            f"Verification: {verification}",
            f"Backups and clones: {', '.join(replicas) if replicas else 'None'}",
        )
    )


def profile_for_card(card: CardMarker) -> dict:
    return {
        "id": card.card_id,
        "name": card.name,
        "last_root": str(card.root),
        "action": card.action,
        "source_folders": list(card.source_folders),
        "destination_prefix": card.destination_prefix,
        "camera_name": card.camera_name,
        "enabled": card.enabled,
        "delete_empty_folders_after_move": card.delete_empty_folders_after_move,
    }
