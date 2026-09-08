from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass, field, replace
from collections import Counter
from pathlib import Path

from .discovery import folder_import_source
from .integrity import IntegrityCatalog, checksum, local_state_directory
from .library_state import read_library_metadata
from .metadata import extract_metadata
from .organizer import Organizer, SourceChangedError


def checked_path(path: Path) -> Path:
    path = path.expanduser().absolute()
    if any(Organizer._is_link_like(p) for p in (path, *path.parents)):
        raise ValueError(f"Linked paths are not supported for library jobs: {path}")
    return path.resolve()


def media_root(config, kind, source=None):
    route_kind = kind
    if kind == "sidecar" and source is not None:
        classifier = Organizer(config, dry_run=True)
        companions = sorted(source.parent.glob(source.stem + ".*"))
        kinds = {classifier.classify(p) for p in companions if p != source}
        # RAW/photo sidecars stay with the photographic capture when video shares its stem.
        route_kind = next((k for k in ("raw", "photo", "video") if k in kinds), kind)
    identifier = config.get("media_library_routes", {}).get(route_kind, "")
    if not identifier:
        return checked_path(Path(config["destination_root"]))
    library = next((item for item in config["library_destinations"] if item["id"] == identifier), None)
    if not library or not library.get("enabled", True) or not library.get("root"):
        raise ValueError(f"The {route_kind} destination library is unavailable. Update saved routing settings.")
    return checked_path(Path(library["root"]))


def walk_files(root: Path, config, *, all_files=False, include_conflicts=False):
    checked_path(root)
    if not root.is_dir():
        raise ValueError(f"Source folder unavailable: {root}")
    excluded = {".photocard-organizer", config["identification"]["folder_name"]}
    conflict = Path(config["safety"]["conflict_folder"])
    classifier = Organizer(config, dry_run=True)
    for current, names, files in os.walk(root):
        directory = Path(current)
        if all_files and any(Organizer._is_link_like(directory / name) for name in names):
            raise ValueError(f"Linked folder requires manual review before migration: {directory}")
        names[:] = sorted(n for n in names if not Organizer._is_link_like(directory / n)
                          and (all_files or n not in excluded)
                          and (all_files or include_conflicts or directory / n != root / conflict))
        for name in sorted(files):
            path = directory / name
            if Organizer._is_link_like(path):
                raise ValueError(f"Linked file requires manual review: {path}")
            if all_files or classifier.classify(path):
                yield path


@dataclass(frozen=True)
class LibraryJobEntry:
    source: Path
    destination: Path
    root: Path
    digest: str
    snapshot: os.stat_result
    action: str
    backup: Path | None = None
    existing: Path | None = None


@dataclass
class LibraryJobPlan:
    config: dict
    entries: list[LibraryJobEntry]
    mode: str
    source_roots: list[Path]
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    migration_target: Path | None = None
    backup_root: Path | None = None

    @property
    def changes(self):
        return sum(e.action not in {"Keep", "Duplicate"} for e in self.entries)


def stable_digest(path):
    before = path.stat()
    digest = checksum(path)
    if not Organizer._same_snapshot(before, path.stat()):
        raise SourceChangedError(f"File changed while previewing: {path}")
    return before, digest


def build_library_job(config, sources, *, mode="merge", backup_root=None,
                      reorganize_existing=False, migration_target=None,
                      cancel_event=None, progress=None):
    candidate = copy.deepcopy(config)
    primary = checked_path(Path(candidate["destination_root"]))
    roots = list(dict.fromkeys(checked_path(Path(p)) for p in sources))
    if mode not in {"merge", "reorganize", "migrate"}:
        raise ValueError("Unknown library operation")
    backup = checked_path(Path(backup_root)) if backup_root else None
    output_roots = {primary}
    if mode != "migrate":
        output_roots.update(media_root(candidate, k) for k in candidate["media_rules"])
    target = checked_path(Path(migration_target)) if migration_target else None
    if mode == "migrate":
        if len(roots) != 1 or target is None:
            raise ValueError("Migration requires one source and a new destination.")
        if Organizer._paths_overlap(roots[0], target):
            raise ValueError("Migration source and destination must be separate.")
        if target.exists() and any(target.iterdir()):
            raise ValueError("Choose an empty migration destination. Use Merge for a different existing library.")
        output_roots = {target}
    for a in output_roots:
        for b in output_roots:
            if a != b and Organizer._paths_overlap(a, b):
                raise ValueError("Media library destinations must not be nested.")
    if backup and any(Organizer._paths_overlap(backup, p) for p in [*roots, *output_roots]):
        raise ValueError("The backup must be separate from every source and destination.")
    if backup and len(output_roots) > 1:
        raise ValueError("A single clone backup requires one central library. Disable media-library routing for this job, or back up each library separately.")
    for root in roots:
        read_library_metadata(root)
        for output in output_roots:
            if root != output and Organizer._paths_overlap(root, output):
                raise ValueError("Source and destination folders must not be nested.")
    for root in output_roots:
        read_library_metadata(root)
    if mode == "migrate":
        files = [(p, roots[0]) for p in walk_files(roots[0], candidate, all_files=True)]
    else:
        files = []
        seen = set()
        for root in [*sorted(output_roots), *roots]:
            if root.exists():
                for path in walk_files(root, candidate, include_conflicts=root in output_roots):
                    if path not in seen:
                        seen.add(path)
                        files.append((path, root))
    entries = []
    content = {}
    reserved = set()
    planner = Organizer(candidate, dry_run=True)
    catalogs = {root: IntegrityCatalog(root).records() for root in set(roots) | output_roots}
    snapshots = {source: source.stat() for source, _ in files}
    sizes = Counter(snapshot.st_size for snapshot in snapshots.values())
    for index, (source, source_root) in enumerate(files):
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Preview cancelled")
        snapshot = snapshots[source]
        digest = ""
        baseline = catalogs.get(source_root, {}).get(source.relative_to(source_root).as_posix())
        if mode != "migrate" and sizes[snapshot.st_size] > 1:
            _, digest = stable_digest(source)
        if baseline:
            if digest and (digest if baseline["algorithm"] == "sha256" else checksum(source, baseline["algorithm"])) != baseline["digest"]:
                raise ValueError(f"Checksum mismatch in {source}. Review integrity before merging; baseline was not changed.")
            if not digest and baseline["algorithm"] == "sha256":
                digest = baseline["digest"]
        if mode == "migrate":
            root = target
            destination = root / source.relative_to(source_root)
            action = "Copy (retain original)"
            existing = None
        else:
            kind = planner.classify(source)
            root = media_root(candidate, kind, source)
            metadata = extract_metadata(source, kind)
            card = folder_import_source(source_root, action="copy")
            proposed = planner._destination_for(card, source, kind, metadata)
            destination = root / proposed.relative_to(planner.destination_root)
            destination = checked_path(destination)
            if root not in destination.parents or (root / ".photocard-organizer") in destination.parents:
                raise ValueError(f"Invalid media destination: {destination}")
            existing = None
            # Do not combine sidecars with another capture simply because their bytes match.
            key = (root, kind, digest or str(source), source.stem.casefold() if kind == "sidecar" else "")
            if key in content:
                destination = content[key]
                action = "Duplicate" if source != destination else "Keep"
            elif source_root in output_roots and (
                not (reorganize_existing or mode == "reorganize")
                or source_root / candidate["safety"]["conflict_folder"] in source.parents
            ):
                destination = source
                action = "Keep"
                content[key] = destination
            else:
                action = "Keep" if source == destination else "Copy"
                if action != "Keep" and (destination.exists() or destination in reserved):
                    existing = destination
                    conflict_root = checked_path(root / planner._safe_prefix(candidate["safety"]["conflict_folder"]))
                    if root not in conflict_root.parents:
                        raise ValueError("Conflict folder must be inside the destination library")
                    destination = conflict_root / "Library merge" / destination.relative_to(root)
                    number = 2
                    base = destination
                    template = candidate["safety"].get("conflict_filename_appendage", "_{number}")
                    while destination.exists() or destination in reserved:
                        suffix = template.replace("{number}", str(number))
                        if "{number}" not in template:
                            suffix += f"_{number}"
                        if any(c in suffix for c in "/\\"):
                            raise ValueError("Conflict appendage cannot contain a path separator")
                        destination = base.with_name(base.stem + suffix + base.suffix)
                        number += 1
                    action = "Conflict review"
                elif action == "Copy" and (mode == "reorganize" or (source_root in output_roots and reorganize_existing)):
                    action = "Move"
                content[key] = destination
        reserved.add(destination)
        replica = backup / destination.relative_to(root) if backup else None
        if replica and replica.exists() and digest and checksum(replica) != digest:
            raise ValueError(f"Backup conflict: {replica}. Choose an empty backup or resolve this difference first.")
        entries.append(LibraryJobEntry(source, destination, root, digest, snapshot, action, replica, existing))
        if progress:
            progress(index + 1, len(files), str(source))
    # Full plan includes transient copying space, not just the final unique size.
    required = {}
    for entry in entries:
        if entry.action not in {"Keep", "Duplicate"}:
            required[entry.root] = required.get(entry.root, 0) + entry.snapshot.st_size
        if entry.backup and not entry.backup.exists():
            required[backup] = required.get(backup, 0) + entry.snapshot.st_size
    by_device = {}
    for root, size in required.items():
        ancestor = root
        while not ancestor.exists():
            if ancestor.parent == ancestor:
                raise FileNotFoundError(f"Destination volume is unavailable: {root}")
            ancestor = ancestor.parent
        device = ancestor.stat().st_dev
        old_size, _ = by_device.get(device, (0, ancestor))
        by_device[device] = (old_size + size, ancestor)
    for size, ancestor in by_device.values():
        capacity = shutil.disk_usage(ancestor)
        reserve = max(candidate["safety"]["minimum_destination_free_gb"] * 1024**3,
                      capacity.total * candidate["safety"]["minimum_destination_free_percent"] / 100)
        if capacity.free - size < reserve:
            raise ValueError(f"Insufficient space including the configured reserve on {ancestor}")
    return LibraryJobPlan(candidate, entries, mode, roots, migration_target=target, backup_root=backup)


def append_record(paths, record):
    for path in paths:
        checked_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def execute_library_job(plan, *, cancel_event=None, progress=None, local_root=None):
    local_root = Path(local_root) if local_root else local_state_directory()
    records = []
    workers = {}
    verified_stats = {}
    def worker(root):
        if root not in workers:
            config = copy.deepcopy(plan.config)
            config["destination_root"] = str(root)
            workers[root] = Organizer(config, dry_run=plan.mode == "migrate")
        return workers[root]
    # Plan order keeps canonical copies before duplicates that refer to them.
    for index, entry in enumerate(plan.entries):
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Cancelled between files. Completed copies and records are retained.")
        for path in (entry.source, entry.destination, *([entry.backup] if entry.backup else [])):
            checked_path(path)
        if not entry.source.exists() and entry.action == "Move" and entry.destination.exists() and checksum(entry.destination) == entry.digest:
            continue
        if not Organizer._same_snapshot(entry.snapshot, entry.source.stat()):
            raise SourceChangedError(f"Source changed since preview: {entry.source}")
        if entry.action == "Keep" and entry.backup is None:
            if progress:
                progress(index + 1, len(plan.entries), str(entry.source))
            continue
        engine = worker(entry.root)
        if entry.destination.exists():
            if not entry.digest:
                entry = replace(entry, digest=checksum(entry.source))
            cached = verified_stats.get(entry.destination)
            if not (cached and Organizer._same_snapshot(cached, entry.destination.stat())) and checksum(entry.destination) != entry.digest:
                raise ValueError(f"Destination changed since preview; nothing overwritten: {entry.destination}")
        else:
            enough, reason, _ = engine._destination_space_status(entry.root, entry.snapshot.st_size)
            if not enough:
                raise OSError(reason)
            actual = engine._copy_and_verify(entry.source, entry.destination, "sha256", expected_stat=entry.snapshot)
            if entry.digest and actual != entry.digest:
                raise SourceChangedError(f"Source content differs from preview: {entry.source}")
            entry = replace(entry, digest=actual)
        verified_stats[entry.destination] = entry.destination.stat()
        if plan.mode != "migrate":
            IntegrityCatalog(entry.root, local_root).record(entry.destination, entry.digest, "sha256", verified=True)
        if entry.backup:
            backup_root = plan.backup_root
            if backup_root is None:
                raise ValueError("Backup destination was not retained in the job plan")
            if entry.backup.exists():
                if checksum(entry.backup) != entry.digest:
                    raise ValueError(f"Backup differs; nothing overwritten: {entry.backup}")
            else:
                ancestor = backup_root
                while not ancestor.exists():
                    if ancestor.parent == ancestor:
                        raise FileNotFoundError(f"Backup volume is unavailable: {backup_root}")
                    ancestor = ancestor.parent
                enough, reason, _ = engine._destination_space_status(ancestor, entry.snapshot.st_size)
                if not enough:
                    raise OSError(reason)
                engine._copy_and_verify(entry.destination, entry.backup, "sha256")
            IntegrityCatalog(backup_root, local_root).record(entry.backup, entry.digest, "sha256", verified=True)
        event = dict(session=plan.id, source=str(entry.source), destination=str(entry.destination),
                     backup=str(entry.backup) if entry.backup else "", sha256=entry.digest, action=entry.action)
        logs = [local_root / "library-jobs" / f"{plan.id}.jsonl"]
        if plan.mode != "migrate":
            logs.append(entry.root / ".photocard-organizer" / "library-jobs" / f"{plan.id}.jsonl")
        if entry.backup:
            logs.append(plan.backup_root / ".photocard-organizer" / "library-jobs" / f"{plan.id}.jsonl")
        append_record(logs, event)
        if entry.existing:
            engine.manifest.record_conflict(source_key=hashlib.sha256(str(entry.source).encode()).hexdigest(),
                card_id="library-merge", source_path=entry.source, existing_path=entry.existing,
                incoming_path=entry.destination, conflict_type="filename_conflict", resolution="conflict_folder",
                content_checksum=entry.digest)
        if entry.action == "Move":
            if not Organizer._same_snapshot(entry.snapshot, entry.source.stat()) or not Organizer._same_snapshot(verified_stats[entry.destination], entry.destination.stat()):
                raise SourceChangedError("Content changed before source removal; source retained")
            engine.manifest.relocate_destinations(entry.source, entry.destination)
            entry.source.unlink()
        records.append(event)
        if progress:
            progress(index + 1, len(plan.entries), str(entry.destination))
    if plan.mode == "migrate":
        # Do not activate a partial migration or one whose source changed during copying.
        original = set(walk_files(plan.source_roots[0], plan.config, all_files=True))
        if original != {e.source for e in plan.entries}:
            raise SourceChangedError("Migration source inventory changed; original location remains active")
        for entry in plan.entries:
            if not Organizer._same_snapshot(entry.snapshot, entry.source.stat()) or not Organizer._same_snapshot(verified_stats[entry.destination], entry.destination.stat()):
                raise SourceChangedError("Migration source or destination changed; original location remains active")
        relocate_migrated_index(plan.source_roots[0], plan.migration_target)
        append_record([plan.migration_target / ".photocard-organizer" / "library-jobs" / f"{plan.id}.jsonl"],
                      dict(session=plan.id, action="migration verified", source=str(plan.source_roots[0]), destination=str(plan.migration_target)))
    return records


def relocate_migrated_index(source, destination):
    """Update live index paths; historical transfer records remain historical."""
    database = destination / ".photocard-organizer" / "manifest.sqlite3"
    if not database.exists():
        return
    with closing(sqlite3.connect(database)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Migrated library index failed its integrity check")
        with connection:
            for table, columns in {
                "imports": ("destination_path",),
                "pending_imports": ("source_path", "destination_path"),
                "conflicts": ("source_path", "existing_path", "incoming_path"),
                "digest_items": ("source_path", "destination_path"),
                "hub_receipts": ("receipt_path",),
            }.items():
                available = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
                for column in columns:
                    if column not in available:
                        continue
                    for (value,) in connection.execute(f'SELECT DISTINCT "{column}" FROM "{table}"').fetchall():
                        try:
                            relative = Path(value).relative_to(source)
                        except (ValueError, TypeError):
                            continue
                        connection.execute(f'UPDATE "{table}" SET "{column}"=? WHERE "{column}"=?', (str(destination / relative), value))
