from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from .config import get_card_profile
from .models import Capacity, CardMarker


class MarkerError(ValueError):
    pass


def folder_import_source(
    root: Path | str,
    *,
    name: str = "",
    stable_id: str = "",
    action: str = "copy",
    include_subfolders: bool = True,
    destination_prefix: str = "",
    camera_name: str = "",
) -> CardMarker:
    source_root = Path(root).expanduser().resolve()
    if not source_root.is_dir():
        raise MarkerError(f"Import folder does not exist: {source_root}")
    if action not in {"copy", "move"}:
        raise MarkerError("Folder import action must be 'copy' or 'move'.")
    normalized_path = os.path.normcase(str(source_root))
    source_id = stable_id.strip() or hashlib.sha256(
        normalized_path.encode("utf-8")
    ).hexdigest()[:16]
    source_id = re.sub(r"[^A-Za-z0-9._-]+", "-", source_id).strip("-") or "folder"
    disabled_identity = source_root / ".photocard-folder-import-disabled"
    return CardMarker(
        root=source_root,
        identity_dir=disabled_identity,
        identity_path=disabled_identity / "identity.json",
        history_dir=disabled_identity / "transfers",
        card_id=f"folder-{source_id}",
        name=name.strip() or source_root.name or "Imported folder",
        action=action,
        source_folders=(".",),
        destination_prefix=destination_prefix.strip("/\\"),
        camera_name=camera_name.strip(),
        recursive=bool(include_subfolders),
        portable_history=False,
        source_type="folder",
    )


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "camera-card"


def write_card_identity(
    root: Path | str,
    folder_name: str,
    identity_filename: str,
    history_folder_name: str,
    name: str,
    card_id: str = "",
    action: str = "copy",
    source_folders: Iterable[str] = ("DCIM",),
    destination_prefix: str = "",
    enabled: bool = True,
    delete_empty_folders_after_move: bool = False,
    camera_name: str = "",
) -> Path:
    volume_root = Path(root).expanduser().resolve()
    if not volume_root.is_dir():
        raise MarkerError(f"Card or drive root does not exist: {volume_root}")
    for value in (folder_name, identity_filename, history_folder_name):
        if Path(value).name != value or value in {"", ".", ".."}:
            raise MarkerError("Identity folder and filenames must each be a single name.")
    if action not in {"copy", "move"}:
        raise MarkerError("Card action must be 'copy' or 'move'.")
    clean_sources = [str(folder).strip("/\\") for folder in source_folders if str(folder).strip("/\\")]
    payload = {
        "schema": 1,
        "id": card_id.strip() or _slugify(name),
        "name": name.strip() or volume_root.name or "Camera card",
        "enabled": bool(enabled),
        "action": action,
        "source_folders": clean_sources or ["DCIM"],
        "destination_prefix": destination_prefix.strip("/\\"),
        "camera_name": camera_name.strip(),
        "delete_empty_folders_after_move": bool(delete_empty_folders_after_move),
    }
    identity_dir = volume_root / folder_name
    identity_dir.mkdir(parents=False, exist_ok=True)
    marker_path = identity_dir / identity_filename
    temporary = marker_path.with_suffix(marker_path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    os.replace(temporary, marker_path)
    (identity_dir / history_folder_name).mkdir(parents=False, exist_ok=True)
    return marker_path


def load_card_identity(
    root: Path,
    folder_name: str,
    identity_filename: str,
    history_folder_name: str,
    default_action: str = "copy",
) -> CardMarker:
    identity_dir = root / folder_name
    marker_path = identity_dir / identity_filename
    try:
        with marker_path.open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise MarkerError(f"Cannot read {marker_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise MarkerError(f"Marker must contain a JSON object: {marker_path}")

    name = str(data.get("name") or root.name or "Camera card").strip()
    card_id = str(data.get("id") or _slugify(name)).strip()
    action = str(data.get("action") or default_action).lower()
    if action not in {"copy", "move"}:
        raise MarkerError(f"Unsupported action '{action}' in {marker_path}")
    sources = data.get("source_folders", ["DCIM"])
    if not isinstance(sources, list):
        raise MarkerError(f"source_folders must be a list in {marker_path}")
    source_folders = tuple(str(folder).strip("/\\") for folder in sources if str(folder).strip("/\\"))
    return CardMarker(
        root=root,
        identity_dir=identity_dir,
        identity_path=marker_path,
        history_dir=identity_dir / history_folder_name,
        card_id=card_id,
        name=name,
        action=action,
        source_folders=source_folders or ("DCIM",),
        destination_prefix=str(data.get("destination_prefix", "")).strip("/\\"),
        camera_name=str(data.get("camera_name", "")).strip(),
        enabled=bool(data.get("enabled", True)),
        delete_empty_folders_after_move=bool(data.get("delete_empty_folders_after_move", False)),
    )


def _windows_drive_roots() -> list[Path]:
    try:
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    except (AttributeError, OSError):
        return []
    roots = []
    for index in range(26):
        if bitmask & (1 << index):
            roots.append(Path(f"{chr(65 + index)}:\\"))
    return roots


def _linux_mount_roots(mount_bases: list[str]) -> list[Path]:
    allowed = [Path(base).expanduser() for base in mount_bases]
    roots: set[Path] = set()
    mounts = Path("/proc/mounts")
    if mounts.exists():
        try:
            for line in mounts.read_text(encoding="utf-8", errors="replace").splitlines():
                fields = line.split()
                if len(fields) < 2:
                    continue
                mount = Path(fields[1].replace("\\040", " ").replace("\\011", "\t"))
                if any(mount == base or base in mount.parents for base in allowed):
                    roots.add(mount)
        except OSError:
            pass
    for base in allowed:
        if not base.is_dir():
            continue
        roots.add(base)
        try:
            for child in base.iterdir():
                if child.is_dir():
                    roots.add(child)
                    try:
                        roots.update(grandchild for grandchild in child.iterdir() if grandchild.is_dir())
                    except OSError:
                        pass
        except OSError:
            pass
    return sorted(roots, key=str)


def _configured_candidates(configured_roots: list[str]) -> set[Path]:
    candidates: set[Path] = set()
    for configured in configured_roots:
        root = Path(configured).expanduser()
        candidates.add(root)
        if not root.is_dir():
            continue
        try:
            candidates.update(child for child in root.iterdir() if child.is_dir())
        except OSError:
            pass
    return candidates


def apply_retained_profile(card: CardMarker, config: dict) -> CardMarker:
    profile = get_card_profile(config, card.card_id)
    if profile is None:
        return card
    return replace(
        card,
        name=profile["name"],
        action=profile["action"],
        source_folders=tuple(profile["source_folders"]),
        destination_prefix=profile["destination_prefix"],
        camera_name=profile.get("camera_name", ""),
        enabled=profile["enabled"],
        delete_empty_folders_after_move=profile["delete_empty_folders_after_move"],
    )


def discover_cards(config: dict) -> tuple[list[CardMarker], list[str]]:
    identification = config["identification"]
    folder_name = identification["folder_name"]
    identity_filename = identification["identity_filename"]
    history_folder_name = identification["history_folder_name"]
    candidates = _configured_candidates(identification.get("configured_roots", []))
    if identification.get("auto_detect", True):
        if os.name == "nt":
            candidates.update(_windows_drive_roots())
        else:
            candidates.update(_linux_mount_roots(identification.get("linux_mount_bases", [])))

    cards: list[CardMarker] = []
    errors: list[str] = []
    seen_ids: set[str] = set()
    default_action = config["safety"].get("default_action", "copy")
    for root in sorted(candidates, key=lambda path: str(path).lower()):
        try:
            root = root.resolve()
        except OSError:
            continue
        try:
            has_identity = (root / folder_name / identity_filename).is_file()
        except OSError:
            # Empty card readers and protected volumes can be enumerated by the OS.
            # Treat them as unavailable instead of aborting discovery.
            continue
        if not has_identity:
            continue
        try:
            card = load_card_identity(
                root,
                folder_name,
                identity_filename,
                history_folder_name,
                default_action,
            )
        except MarkerError as exc:
            errors.append(str(exc))
            continue
        card = apply_retained_profile(card, config)
        if card.card_id in seen_ids:
            errors.append(f"Duplicate card id '{card.card_id}' at {root}")
            continue
        seen_ids.add(card.card_id)
        if card.enabled:
            cards.append(card)
    return cards, errors


def capacity_for(path: Path | str) -> Capacity:
    candidate = Path(path).expanduser()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    usage = shutil.disk_usage(candidate)
    return Capacity(candidate, usage.total, usage.used, usage.free)
