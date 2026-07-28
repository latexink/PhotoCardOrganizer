from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .atomic_copy import commit_without_overwrite, create_partial_file
from .discovery import capacity_for, folder_import_source
from .manifest import ImportManifest
from .models import ActivityEvent, CardMarker
from .templates import safe_segment


EventCallback = Callable[[ActivityEvent], None]
ProgressCallback = Callable[[int, int, str], None]


@dataclass
class HubPublishStats:
    discovered: int = 0
    published: int = 0
    reused: int = 0
    failed: int = 0
    bytes_published: int = 0
    session_path: Path | None = None
    checksum_path: Path | None = None
    errors: list[str] = field(default_factory=list)


def producer_channel(config: dict, hub: dict) -> str:
    configured = str(hub.get("producer_channel", "")).strip()
    if configured:
        return safe_segment(configured)
    instance = config["instance"]
    return safe_segment(
        f"{instance.get('name', 'Client')}_{str(instance.get('id', ''))[:8]}"
    )


def producer_root(config: dict, hub: dict) -> Path:
    return (
        Path(str(hub["root"])).expanduser()
        / "Producers"
        / producer_channel(config, hub)
    )


def effective_replica_destinations(config: dict) -> list[dict]:
    replicas = [dict(replica) for replica in config.get("replica_destinations", [])]
    existing_ids = {str(replica.get("id", "")) for replica in replicas}
    for hub in config.get("transfer_hubs", []):
        if (
            not hub.get("enabled", True)
            or hub.get("role") != "publish"
            or not hub.get("root")
        ):
            continue
        replica_id = f"transfer-hub-{hub['id']}"
        if replica_id in existing_ids:
            continue
        replicas.append(
            {
                "id": replica_id,
                "name": f"Hub: {hub['name']}",
                "root": str(producer_root(config, hub)),
                "enabled": True,
                "required": bool(hub.get("required", False)),
                "include_history": True,
                "conflict_policy": str(
                    hub.get("conflict_policy", "block")
                ),
            }
        )
        existing_ids.add(replica_id)
    return replicas


def catch_sources(config: dict, hub: dict) -> tuple[list[CardMarker], list[str]]:
    root = Path(str(hub.get("root", ""))).expanduser()
    producers = root / "Producers"
    if not producers.is_dir():
        return [], [f"{hub['name']}: Producers folder is unavailable at {producers}"]
    cards = []
    errors = []
    own_channel = (
        producer_channel(config, hub)
        if hub.get("role") == "publish"
        else ""
    )
    try:
        channels = sorted(
            path
            for path in producers.iterdir()
            if path.is_dir() and not path.is_symlink()
        )
    except OSError as exc:
        return [], [f"{hub['name']}: could not list producer channels: {exc}"]
    for channel_root in channels:
        if channel_root.name == own_channel:
            continue
        stable = hashlib.sha256(
            f"{hub['id']}\0{channel_root.name}".encode("utf-8")
        ).hexdigest()[:16]
        try:
            cards.append(
                folder_import_source(
                    channel_root,
                    name=f"Hub: {hub['name']} / {channel_root.name}",
                    stable_id=f"hub-{stable}",
                    action="copy",
                    include_subfolders=True,
                )
            )
        except ValueError as exc:
            errors.append(str(exc))
    return cards, errors


def publish_library(
    config: dict,
    hub: dict,
    *,
    progress_callback: ProgressCallback | None = None,
    event_callback: EventCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> HubPublishStats:
    source_root = Path(config["destination_root"]).expanduser().resolve()
    if not source_root.is_dir():
        raise ValueError(f"The local library does not exist: {source_root}")
    hub_root = Path(str(hub.get("root", ""))).expanduser()
    if not hub_root.is_dir():
        raise ValueError(f"The transfer hub is unavailable: {hub_root}")
    destination_root = producer_root(config, hub)
    try:
        resolved_hub = hub_root.resolve()
        if (
            resolved_hub == source_root
            or resolved_hub in source_root.parents
            or source_root in resolved_hub.parents
        ):
            raise ValueError(
                "The transfer hub and local library must be separate locations."
            )
    except OSError:
        pass
    destination_root.mkdir(parents=True, exist_ok=True)
    files = list(_library_files(source_root, config["media_rules"]))
    stats = HubPublishStats(discovered=len(files))
    now = datetime.now(timezone.utc)
    session_id = uuid.uuid4().hex
    records_root = (
        destination_root / ".photocard-organizer" / "hub-sessions"
    )
    records_root.mkdir(parents=True, exist_ok=True)
    stem = (
        f"{now.astimezone():%Y-%m-%d_%H-%M-%S}_"
        f"{safe_segment(config['instance']['name'])}_{session_id[:8]}"
    )
    stats.session_path = records_root / f"{stem}.jsonl"
    stats.checksum_path = records_root / f"{stem}.sha256"
    local_records_root = (
        source_root
        / ".photocard-organizer"
        / "hub-publications"
        / safe_segment(str(hub["id"]))
    )
    local_records_root.mkdir(parents=True, exist_ok=True)
    local_session_path = local_records_root / stats.session_path.name
    local_checksum_path = local_records_root / stats.checksum_path.name
    header = {
        "schema": 1,
        "record_type": "hub_publication_session",
        "session_id": session_id,
        "hub_id": hub["id"],
        "hub_name": hub["name"],
        "producer_channel": producer_channel(config, hub),
        "instance_id": config["instance"]["id"],
        "instance_name": config["instance"]["name"],
        "library_id": config["instance"]["library_id"],
        "started_at": now.isoformat(),
    }
    for path in (stats.session_path, local_session_path):
        _append_json(path, header, create=True)
    for path in (stats.checksum_path, local_checksum_path):
        _append_line(
            path,
            f"# Photo Card Organizer SHA-256 hub publication {session_id}",
            create=True,
        )

    for index, source in enumerate(files, start=1):
        if cancel_event is not None and cancel_event.is_set():
            stats.errors.append("Publication cancelled before all files completed.")
            break
        relative = source.relative_to(source_root)
        destination = destination_root / relative
        try:
            snapshot = source.stat()
            checksum = _hash_file(source)
            _verify_source_snapshot(source, snapshot)
            reused = False
            if destination.exists():
                if (
                    destination.stat().st_size == snapshot.st_size
                    and _hash_file(destination) == checksum
                ):
                    _verify_source_snapshot(source, snapshot)
                    reused = True
                    stats.reused += 1
                elif hub.get("conflict_policy", "block") == "archive_and_replace":
                    _archive_existing(
                        destination_root, destination, relative
                    )
                else:
                    raise OSError(
                        "The producer channel contains different content at this path."
                    )
            if not reused:
                _require_destination_space(
                    config, destination_root, snapshot.st_size
                )
                _copy_verified(source, destination, checksum, snapshot)
                stats.published += 1
                stats.bytes_published += snapshot.st_size
            record = {
                "schema": 1,
                "record_type": "hub_publication",
                "status": "verified_existing" if reused else "verified",
                "session_id": session_id,
                "relative_path": relative.as_posix(),
                "source_size": snapshot.st_size,
                "source_mtime_ns": snapshot.st_mtime_ns,
                "checksum_algorithm": "sha256",
                "content_checksum": checksum,
                "published_at": datetime.now(timezone.utc).isoformat(),
            }
            for path in (stats.session_path, local_session_path):
                _append_json(path, record)
            checksum_line = f"{checksum}  {relative.as_posix()}"
            for path in (stats.checksum_path, local_checksum_path):
                _append_line(path, checksum_line)
        except OSError as exc:
            stats.failed += 1
            message = f"{relative.as_posix()}: {exc}"
            stats.errors.append(message)
            if event_callback:
                event_callback(ActivityEvent("warning", f"Hub publish failed: {message}"))
        if progress_callback:
            progress_callback(index, len(files), relative.as_posix())
    return stats


def write_digestion_receipts(
    config: dict,
    hub: dict,
    card: CardMarker,
    manifest: ImportManifest,
    source_entries: list[tuple[Path, Path, int, int, str]],
) -> tuple[int, list[str]]:
    producer = card.root.name
    pending = []
    for _source, relative, _size, _mtime_ns, source_key in source_entries:
        record = manifest.import_record(source_key)
        if record is None or manifest.has_hub_receipt(
            str(hub["id"]), producer, source_key
        ):
            continue
        pending.append((relative, source_key, record))
    if not pending:
        return 0, []

    session_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    consumer = safe_segment(
        f"{config['instance']['name']}_{str(config['instance']['id'])[:8]}"
    )
    receipt_root = (
        Path(str(hub["root"])).expanduser()
        / "Receipts"
        / consumer
        / safe_segment(producer)
    )
    local_root = (
        Path(config["destination_root"]).expanduser()
        / ".photocard-organizer"
        / "hub-receipts"
        / safe_segment(str(hub["id"]))
        / safe_segment(producer)
    )
    filename = (
        f"{now.astimezone():%Y-%m-%d_%H-%M-%S}_"
        f"{safe_segment(producer)}_{session_id[:8]}.jsonl"
    )
    hub_path = receipt_root / filename
    local_path = local_root / filename
    header = {
        "schema": 1,
        "record_type": "hub_digestion_session",
        "session_id": session_id,
        "hub_id": hub["id"],
        "producer_channel": producer,
        "consumer_id": config["instance"]["id"],
        "consumer_name": config["instance"]["name"],
        "library_id": config["instance"]["library_id"],
        "digested_at": now.isoformat(),
    }
    errors = []
    try:
        for path in (hub_path, local_path):
            _append_json(path, header, create=True)
        for relative, source_key, record in pending:
            receipt = {
                "schema": 1,
                "record_type": "hub_digestion",
                "status": "verified",
                "session_id": session_id,
                "source_key": source_key,
                "producer_channel": producer,
                "source_relative_path": relative.as_posix(),
                "destination_path": record["destination_path"],
                "verification": record["verification"],
                "imported_at": record["imported_at"],
            }
            for path in (hub_path, local_path):
                _append_json(path, receipt)
            manifest.record_hub_receipt(
                str(hub["id"]), producer, source_key, hub_path
            )
    except (OSError, ValueError) as exc:
        errors.append(str(exc))
    return len(pending) if not errors else 0, errors


def receipt_status(config: dict, hub: dict) -> tuple[int, str]:
    if hub.get("role") != "publish" or not hub.get("root"):
        return 0, ""
    channel = producer_channel(config, hub)
    root = Path(str(hub["root"])).expanduser() / "Receipts"
    if not root.is_dir():
        return 0, ""
    files = []
    try:
        for consumer in root.iterdir():
            candidate = consumer / channel
            if candidate.is_dir():
                files.extend(candidate.glob("*.jsonl"))
    except OSError:
        return 0, ""
    if not files:
        return 0, ""
    available = []
    for path in files:
        try:
            available.append((path.stat().st_mtime, path))
        except OSError:
            continue
    if not available:
        return 0, ""
    latest_mtime, _latest = max(available, key=lambda item: item[0])
    try:
        latest_text = datetime.fromtimestamp(latest_mtime).strftime(
            "%Y-%m-%d %H:%M"
        )
    except (OSError, OverflowError, ValueError):
        latest_text = ""
    return len(available), latest_text


def source_entries(
    organizer,
    card: CardMarker,
) -> list[tuple[Path, Path, int, int, str]]:
    entries = []
    for source, _media_kind in organizer._source_files(card):
        try:
            stat = source.stat()
            relative = source.relative_to(card.root)
        except (OSError, ValueError):
            continue
        source_key = organizer.source_key(
            card, relative, stat.st_size, stat.st_mtime_ns
        )
        entries.append(
            (source, relative, stat.st_size, stat.st_mtime_ns, source_key)
        )
    return entries


def _library_files(root: Path, media_rules: dict) -> Iterator[Path]:
    extensions = {
        str(extension).lower()
        for rule in media_rules.values()
        if rule.get("enabled", True)
        for extension in rule.get("extensions", [])
    }
    for current_root, directory_names, file_names in os.walk(root):
        directory_names[:] = [
            name
            for name in directory_names
            if name != ".photocard-organizer"
            and not (Path(current_root) / name).is_symlink()
        ]
        for filename in sorted(file_names):
            path = Path(current_root) / filename
            if not path.is_symlink() and path.suffix.lower() in extensions:
                yield path


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_destination_space(
    config: dict, destination_root: Path, source_size: int
) -> None:
    capacity = capacity_for(destination_root)
    projected_free = capacity.free_bytes - source_size
    if projected_free < 0:
        raise OSError("The transfer hub does not have enough physical free space.")
    safety = config["safety"]
    minimum_percent = float(
        safety.get("minimum_destination_free_percent", 0)
    )
    projected_percent = (
        projected_free / capacity.total_bytes * 100
        if capacity.total_bytes
        else 0
    )
    if projected_percent < minimum_percent:
        raise OSError(
            f"The transfer hub would fall below {minimum_percent:.1f}% free space."
        )
    minimum_bytes = (
        float(safety.get("minimum_destination_free_gb", 0)) * 1024**3
    )
    if projected_free < minimum_bytes:
        minimum_gb = float(safety.get("minimum_destination_free_gb", 0))
        raise OSError(
            f"The transfer hub would fall below {minimum_gb:.1f} GB free space."
        )


def _verify_source_snapshot(source: Path, expected_stat) -> None:
    current = source.stat()
    identity_changed = (
        expected_stat.st_ino
        and current.st_ino
        and (
            expected_stat.st_ino != current.st_ino
            or expected_stat.st_dev != current.st_dev
        )
    )
    if (
        identity_changed
        or current.st_size != expected_stat.st_size
        or current.st_mtime_ns != expected_stat.st_mtime_ns
    ):
        raise OSError("Source changed while publishing; no partial copy was kept.")


def _copy_verified(
    source: Path,
    destination: Path,
    checksum: str,
    expected_stat,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = create_partial_file(destination.parent)
    try:
        _verify_source_snapshot(source, expected_stat)
        shutil.copy2(source, temporary)
        _verify_source_snapshot(source, expected_stat)
        if temporary.stat().st_size != expected_stat.st_size:
            raise OSError("Published size does not match the source.")
        if _hash_file(temporary) != checksum:
            raise OSError("Published file failed SHA-256 verification.")
        _verify_source_snapshot(source, expected_stat)
        commit_without_overwrite(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _archive_existing(
    producer_root_path: Path,
    existing: Path,
    relative: Path,
) -> None:
    archive = (
        producer_root_path
        / ".photocard-organizer"
        / "conflicts"
        / relative
    )
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        for number in range(2, 10000):
            candidate = archive.with_name(
                f"{archive.stem}_{number}{archive.suffix}"
            )
            if not candidate.exists():
                archive = candidate
                break
        else:
            raise OSError(
                f"Could not find an available hub conflict archive filename near {archive}"
            )
    shutil.move(str(existing), str(archive))


def _append_json(path: Path, payload: dict, *, create: bool = False) -> None:
    _append_line(
        path,
        json.dumps(payload, separators=(",", ":"), sort_keys=True),
        create=create,
    )


def _append_line(path: Path, value: str, *, create: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "x" if create else "a"
    with path.open(mode, encoding="utf-8", newline="\n") as handle:
        handle.write(value + "\n")
        handle.flush()
        os.fsync(handle.fileno())
