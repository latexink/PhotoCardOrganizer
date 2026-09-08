from __future__ import annotations

import hashlib
import json
import os
import shutil
import statistics
import threading
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path

from .atomic_copy import commit_without_overwrite, create_partial_file
from .discovery import capacity_for
from .library_state import migrate_export_sessions, state_directory
from .metadata import extract_metadata
from .templates import safe_segment


ProgressCallback = Callable[[int, int, str], None]
MEDIA_PRIORITY = {"photo": 0, "raw": 1, "video": 2, "sidecar": 3}


def _item_priority(item: "LibraryItem") -> tuple[int, str]:
    return (
        MEDIA_PRIORITY.get(item.media_kind, 9),
        item.path.name.lower(),
    )


@dataclass(frozen=True)
class LibraryItem:
    path: Path
    relative_path: Path
    media_kind: str
    captured_at: datetime
    camera: str = ""
    rating: int | None = None
    capture_scope: str = ""


@dataclass(frozen=True)
class CaptureSet:
    capture_id: str
    items: tuple[LibraryItem, ...]
    captured_at: datetime
    camera: str
    rating: int | None

    @property
    def primary(self) -> LibraryItem:
        return min(self.items, key=_item_priority)

    @property
    def media_label(self) -> str:
        labels = {"photo": "JPEG/photo", "raw": "RAW", "video": "Video", "sidecar": "Sidecar"}
        present = {item.media_kind for item in self.items}
        ordered = [
            labels.get(kind, kind.title())
            for kind in ("photo", "raw", "video", "sidecar")
            if kind in present
        ]
        ordered.extend(
            sorted(
                labels.get(kind, kind.title())
                for kind in present
                if kind not in {"photo", "raw", "video", "sidecar"}
            )
        )
        return " + ".join(ordered)


@dataclass(frozen=True)
class CaptureGroup:
    group_id: str
    kind: str
    capture_ids: tuple[str, ...]
    started_at: datetime

    @property
    def label(self) -> str:
        prefix = "Bracket / burst" if self.kind == "bracket" else "Interval sequence"
        return f"{prefix} - {self.started_at:%Y-%m-%d %H-%M-%S}"


@dataclass
class ExportStats:
    captures: int = 0
    files_copied: int = 0
    files_reused: int = 0
    bytes_copied: int = 0
    log_path: Path | None = None
    errors: list[str] = field(default_factory=list)


def extension_map(media_rules: dict) -> dict[str, str]:
    result: dict[str, str] = {}
    for kind, rule in media_rules.items():
        if not rule.get("enabled", True):
            continue
        for extension in rule.get("extensions", []):
            result.setdefault(str(extension).lower(), str(kind))
    return result


def scan_library(
    root: Path | str,
    media_rules: dict,
    *,
    progress_callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> list[LibraryItem]:
    library_root = Path(root).expanduser().resolve()
    if not library_root.is_dir():
        raise ValueError(f"Library folder does not exist: {library_root}")
    classified = extension_map(media_rules)
    partition_markers = _media_partition_markers(media_rules)
    candidates: list[tuple[Path, str]] = []
    for current_root, directory_names, file_names in os.walk(library_root):
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Library scan cancelled.")
        directory_names[:] = [
            name
            for name in directory_names
            if name != ".photocard-organizer"
            and not (Path(current_root) / name).is_symlink()
        ]
        for filename in file_names:
            path = Path(current_root) / filename
            if path.is_symlink():
                continue
            media_kind = classified.get(path.suffix.lower())
            if media_kind:
                candidates.append((path, media_kind))

    items: list[LibraryItem] = []
    total = len(candidates)
    for index, (path, media_kind) in enumerate(candidates, start=1):
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Library scan cancelled.")
        try:
            metadata = extract_metadata(path, media_kind)
            relative_path = path.relative_to(library_root)
            items.append(
                LibraryItem(
                    path=path,
                    relative_path=relative_path,
                    media_kind=media_kind,
                    captured_at=metadata.captured_at,
                    camera=metadata.camera,
                    rating=metadata.rating,
                    capture_scope=_capture_scope(
                        relative_path.parent,
                        partition_markers.get(media_kind, ""),
                    ),
                )
            )
        except OSError:
            continue
        if progress_callback and (index == total or index == 1 or index % 25 == 0):
            progress_callback(index, total, path.name)
    return items


def build_capture_sets(items: Iterable[LibraryItem]) -> list[CaptureSet]:
    grouped: dict[tuple[str, str], list[LibraryItem]] = {}
    for item in items:
        key = (
            (
                item.capture_scope
                or item.relative_path.parent.as_posix()
            ).casefold(),
            item.relative_path.stem.casefold(),
        )
        grouped.setdefault(key, []).append(item)

    captures = []
    for key, members in grouped.items():
        ordered = tuple(sorted(members, key=lambda item: item.path.name.lower()))
        prioritized = sorted(ordered, key=_item_priority)
        captured_at = prioritized[0].captured_at
        camera = next(
            (item.camera for item in prioritized if item.camera), ""
        )
        ratings = [item.rating for item in ordered if item.rating is not None]
        capture_id = hashlib.sha256(
            f"{key[0]}\0{key[1]}".encode("utf-8")
        ).hexdigest()[:16]
        captures.append(
            CaptureSet(
                capture_id=capture_id,
                items=ordered,
                captured_at=captured_at,
                camera=camera,
                rating=max(ratings) if ratings else None,
            )
        )
    return sorted(
        captures,
        key=lambda capture: (
            capture.captured_at,
            capture.primary.relative_path.as_posix().casefold(),
        ),
    )


def filter_captures(
    captures: Iterable[CaptureSet],
    media_kind: str = "all",
    start: date | None = None,
    end: date | None = None,
    include_sidecars: bool = True,
) -> list[CaptureSet]:
    if start and end and start > end:
        raise ValueError("The start date must be on or before the end date.")
    result = []
    for capture in captures:
        primary_items = tuple(
            item for item in capture.items
            if (media_kind == "all" or item.media_kind == media_kind)
            and (include_sidecars or media_kind == "sidecar" or item.media_kind != "sidecar")
            and (start is None or item.captured_at.date() >= start)
            and (end is None or item.captured_at.date() <= end)
        )
        if not primary_items:
            continue
        items = primary_items
        if include_sidecars and any(item.media_kind != "sidecar" for item in primary_items):
            items += tuple(item for item in capture.items if item.media_kind == "sidecar" and item not in items)
        primary = min(primary_items, key=_item_priority)
        result.append(replace(capture, items=items, captured_at=primary.captured_at))
    return result


def _media_partition_markers(media_rules: dict) -> dict[str, str]:
    enabled = {
        str(kind): [
            str(segment)
            for segment in rule.get("folder_segments", [])
        ]
        for kind, rule in media_rules.items()
        if rule.get("enabled", True)
    }
    if len(enabled) < 2:
        return {}
    shared_depth = min((len(segments) for segments in enabled.values()), default=0)
    for index in range(shared_depth):
        values = {
            kind: safe_segment(segments[index]).casefold()
            for kind, segments in enabled.items()
            if "{" not in segments[index] and "}" not in segments[index]
        }
        if len(values) != len(enabled):
            continue
        if len(set(values.values())) > 1:
            return values
    return {}


def _capture_scope(parent: Path, partition_marker: str) -> str:
    parts = list(parent.parts)
    if partition_marker:
        for index, part in enumerate(parts):
            if part.casefold() == partition_marker:
                del parts[index]
                break
    return Path(*parts).as_posix() if parts else "."


def detect_capture_groups(
    captures: Iterable[CaptureSet],
    *,
    bracket_seconds: float = 3.0,
    interval_max_seconds: float = 300.0,
    interval_tolerance: float = 0.25,
) -> list[CaptureGroup]:
    visual = [
        capture
        for capture in captures
        if any(item.media_kind in {"photo", "raw"} for item in capture.items)
    ]
    visual.sort(key=lambda capture: capture.captured_at)
    if len(visual) < 2:
        return []

    groups: list[CaptureGroup] = []
    claimed: set[str] = set()

    run: list[CaptureSet] = [visual[0]]
    for capture in visual[1:] + [None]:
        if (
            capture is not None
            and _same_capture_series(run[-1], capture)
            and (capture.captured_at - run[-1].captured_at).total_seconds()
            <= bracket_seconds
        ):
            run.append(capture)
            continue
        if len(run) >= 2:
            groups.append(_capture_group("bracket", run))
            claimed.update(item.capture_id for item in run)
        run = [capture] if capture is not None else []

    remaining = [capture for capture in visual if capture.capture_id not in claimed]
    index = 0
    while index + 2 < len(remaining):
        run = [remaining[index]]
        gaps: list[float] = []
        cursor = index + 1
        while cursor < len(remaining):
            gap = (
                remaining[cursor].captured_at - run[-1].captured_at
            ).total_seconds()
            if (
                not _same_capture_series(run[-1], remaining[cursor])
                or gap <= bracket_seconds
                or gap > interval_max_seconds
            ):
                break
            if gaps:
                expected = statistics.median(gaps)
                tolerance = max(2.0, expected * interval_tolerance)
                if abs(gap - expected) > tolerance:
                    break
            gaps.append(gap)
            run.append(remaining[cursor])
            cursor += 1
        if len(run) >= 3:
            groups.append(_capture_group("interval", run))
            index = cursor
        else:
            index += 1
    return groups


def _same_capture_series(first: CaptureSet, second: CaptureSet) -> bool:
    if (
        first.primary.relative_path.parent.as_posix().casefold()
        != second.primary.relative_path.parent.as_posix().casefold()
    ):
        return False
    return not (
        first.camera
        and second.camera
        and first.camera.casefold() != second.camera.casefold()
    )


def _capture_group(kind: str, captures: list[CaptureSet]) -> CaptureGroup:
    identity = "\0".join(capture.capture_id for capture in captures)
    return CaptureGroup(
        group_id=hashlib.sha256(f"{kind}\0{identity}".encode("utf-8")).hexdigest()[:16],
        kind=kind,
        capture_ids=tuple(capture.capture_id for capture in captures),
        started_at=captures[0].captured_at,
    )


def export_captures(
    captures: Iterable[CaptureSet],
    destination_root: Path | str,
    *,
    groups: Iterable[CaptureGroup] = (),
    group_subfolders: bool = True,
    verification: str = "sha256",
    conflict_appendage: str = "_{number}",
    minimum_free_percent: float = 0.0,
    minimum_free_gb: float = 0.0,
    progress_callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> ExportStats:
    destination = Path(destination_root).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    migrate_export_sessions(destination)
    selected = list(captures)
    stats = ExportStats(captures=len(selected))
    group_by_capture = {
        capture_id: group
        for group in groups
        for capture_id in group.capture_ids
    }
    session_id = uuid.uuid4().hex
    started_at = datetime.now(timezone.utc)
    log_directory = state_directory(destination) / "export-sessions"
    log_directory.mkdir(parents=True, exist_ok=True)
    log_path = log_directory / (
        f"PhotoCardOrganizer-export-{started_at.astimezone():%Y-%m-%d_%H-%M-%S}-"
        f"{session_id[:8]}.jsonl"
    )
    stats.log_path = log_path
    all_files = [
        (capture, item)
        for capture in selected
        for item in capture.items
    ]

    with log_path.open("x", encoding="utf-8", newline="\n") as log:
        for index, (capture, item) in enumerate(all_files, start=1):
            if cancel_event is not None and cancel_event.is_set():
                stats.errors.append("Export cancelled before all selected files completed.")
                break
            group = group_by_capture.get(capture.capture_id)
            target_directory = destination
            if group_subfolders and group is not None:
                target_directory /= safe_segment(group.label)
            target_directory.mkdir(parents=True, exist_ok=True)
            try:
                snapshot = item.path.stat()
                checksum = _hash_file(item.path, verification)
                _verify_source_snapshot(item.path, snapshot)
                target, reused = _available_export_path(
                    target_directory / item.path.name,
                    snapshot.st_size,
                    checksum,
                    verification,
                    conflict_appendage,
                )
                _verify_source_snapshot(item.path, snapshot)
                if not reused:
                    _require_export_space(
                        target_directory,
                        snapshot.st_size,
                        minimum_free_percent,
                        minimum_free_gb,
                    )
                    _copy_verified(
                        item.path,
                        target,
                        verification,
                        checksum,
                        snapshot,
                    )
                    stats.files_copied += 1
                    stats.bytes_copied += snapshot.st_size
                else:
                    if not _matching_export(
                        target,
                        snapshot.st_size,
                        checksum,
                        verification,
                    ):
                        raise OSError(
                            "An existing export changed while it was being verified."
                        )
                    stats.files_reused += 1
                record = {
                    "session_id": session_id,
                    "source": str(item.path),
                    "destination": str(target),
                    "media_kind": item.media_kind,
                    "capture_id": capture.capture_id,
                    "capture_group": group.group_id if group else "",
                    "capture_group_kind": group.kind if group else "",
                    "verification": verification,
                    "checksum": checksum,
                    "source_size": snapshot.st_size,
                    "reused_existing": reused,
                    "exported_at": datetime.now(timezone.utc).isoformat(),
                }
                log.write(json.dumps(record, sort_keys=True) + "\n")
                log.flush()
                os.fsync(log.fileno())
            except OSError as exc:
                stats.errors.append(f"{item.path.name}: {exc}")
            if progress_callback:
                progress_callback(index, len(all_files), item.path.name)
    return stats


def _hash_file(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _available_export_path(
    requested: Path,
    source_size: int,
    source_checksum: str,
    verification: str,
    conflict_appendage: str,
) -> tuple[Path, bool]:
    for number in range(1, 10000):
        if number == 1:
            candidate = requested
        else:
            appendage = conflict_appendage.replace("{number}", str(number))
            if "{number}" not in conflict_appendage and number > 2:
                appendage = f"{appendage}_{number}"
            appendage = safe_segment(f"x{appendage}", fallback=f"x_{number}")[1:]
            candidate = requested.with_name(
                f"{requested.stem}{appendage}{requested.suffix}"
            )
        try:
            if _matching_export(
                candidate,
                source_size,
                source_checksum,
                verification,
            ):
                return candidate, True
        except FileNotFoundError:
            return candidate, False
    raise OSError(f"Could not find an available export filename near {requested}")


def _matching_export(
    destination: Path,
    source_size: int,
    source_checksum: str,
    verification: str,
) -> bool:
    stat = destination.stat()
    return (
        stat.st_size == source_size
        and _hash_file(destination, verification) == source_checksum
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
        raise OSError("The source changed during export; no partial copy was kept.")


def _require_export_space(
    destination: Path,
    source_size: int,
    minimum_free_percent: float,
    minimum_free_gb: float,
) -> None:
    capacity = capacity_for(destination)
    projected_free = capacity.free_bytes - source_size
    if projected_free < 0:
        raise OSError("The editing destination does not have enough physical space.")
    projected_percent = (
        projected_free / capacity.total_bytes * 100
        if capacity.total_bytes
        else 0.0
    )
    if projected_percent < minimum_free_percent:
        raise OSError(
            "The editing destination would fall below "
            f"{minimum_free_percent:.1f}% free space."
        )
    minimum_free_bytes = minimum_free_gb * 1024**3
    if projected_free < minimum_free_bytes:
        raise OSError(
            "The editing destination would fall below "
            f"{minimum_free_gb:.1f} GB free space."
        )


def _copy_verified(
    source: Path,
    destination: Path,
    verification: str,
    expected_checksum: str,
    expected_stat,
) -> None:
    temporary = create_partial_file(destination.parent)
    try:
        _verify_source_snapshot(source, expected_stat)
        shutil.copy2(source, temporary)
        _verify_source_snapshot(source, expected_stat)
        if expected_stat.st_size != temporary.stat().st_size:
            raise OSError("Exported size does not match the source.")
        if _hash_file(temporary, verification) != expected_checksum:
            raise OSError(f"{verification.upper()} export verification failed.")
        _verify_source_snapshot(source, expected_stat)
        commit_without_overwrite(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
