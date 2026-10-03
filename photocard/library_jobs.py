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
from pathlib import Path
from types import SimpleNamespace

from .discovery import folder_import_source
from .diagnostics import traced_operation
from .integrity import IntegrityCatalog, checksum, local_state_directory
from .library_state import read_library_metadata
from .metadata import extract_metadata
from .manifest import ImportManifest
from .organizer import Organizer, SourceChangedError
from .job_journal import JobJournal
from .atomic_copy import same_filesystem, relocate_without_overwrite


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
    migration_checksum: bool = False
    keep_originals: bool = False

    @property
    def changes(self):
        return sum(e.action not in {"Keep", "Duplicate"} for e in self.entries)


@traced_operation
def build_library_job(config, sources, *, mode="merge", backup_root=None,
                      reorganize_existing=False, migration_target=None,
                      cancel_event=None, progress=None, migration_checksum=False, keep_originals=False):
    candidate = copy.deepcopy(config)
    # The field remains readable for old resumable plans; new jobs use manual
    # library integrity checks instead of transfer-time checksum options.
    migration_checksum = False
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
    reserved = set()
    planner = Organizer(candidate, dry_run=True)
    manifests = {root: ImportManifest(root, create=False) for root in output_roots}
    snapshots = {source: source.stat() for source, _ in files}
    for index, (source, source_root) in enumerate(files):
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Preview cancelled")
        snapshot = snapshots[source]
        digest = ""
        if mode == "migrate":
            root = target
            destination = root / source.relative_to(source_root)
            action = "Copy (retain original)" if keep_originals else "Move library"
            existing = None
        else:
            kind = planner.classify(source)
            root = media_root(candidate, kind, source)
            metadata = extract_metadata(source, kind)
            card = folder_import_source(source_root, action="copy")
            destination = planner._destination_for(card, source, kind, metadata, destination_root=root)
            destination = checked_path(destination)
            if root not in destination.parents or (root / ".photocard-organizer") in destination.parents:
                raise ValueError(f"Invalid media destination: {destination}")
            existing = None
            previous = manifests[root].library_copy_receipt(source)
            previous_destination = checked_path(Path(previous["destination"])) if previous else None
            previous_matches = bool(previous and Organizer._matches_receipt(snapshot, previous["source"])
                and previous_destination.is_relative_to(root) and not Organizer._is_link_like(previous_destination)
                and previous_destination.exists()
                and Organizer._matches_receipt(previous_destination.stat(), previous["copied"]))
            if previous_matches and not (reorganize_existing or mode == "reorganize"):
                destination = previous_destination
                action = "Keep"
            elif source_root in output_roots and (
                not (reorganize_existing or mode == "reorganize")
                or source_root / candidate["safety"]["conflict_folder"] in source.parents
            ):
                destination = source
                action = "Keep"
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
                    action = "Move" if mode == "reorganize" or (source_root in output_roots and reorganize_existing) else "Conflict review"
                elif action == "Copy" and (mode == "reorganize" or (source_root in output_roots and reorganize_existing)):
                    action = "Move"
        reserved.add(destination)
        replica = backup / destination.relative_to(root) if backup else None
        entries.append(LibraryJobEntry(source, destination, root, digest, snapshot, action, replica, existing))
        if progress:
            progress(index + 1, len(files), str(source))
    # Full plan includes transient copying space, not just the final unique size.
    required = {}
    rename_library = mode == "migrate" and not keep_originals and backup is None and same_filesystem(roots[0], target)
    for entry in entries:
        rename_file = entry.action == "Move" and entry.backup is None and same_filesystem(entry.source, entry.destination)
        if entry.action not in {"Keep", "Duplicate"} and not rename_library and not rename_file:
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
    return LibraryJobPlan(candidate, entries, mode, roots, migration_target=target, backup_root=backup,
                          migration_checksum=migration_checksum, keep_originals=keep_originals)


def append_record(paths, record):
    for path in paths:
        checked_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


@traced_operation
def execute_library_job(plan, *, cancel_event=None, progress=None, local_root=None):
    journal = JobJournal(plan, local_root)
    try:
        if plan.mode == "migrate" and not plan.keep_originals:
            if journal.method is None:
                method = "rename" if not journal.files() and plan.backup_root is None and same_filesystem(plan.source_roots[0], plan.migration_target) else "copy"
                journal.set_method(method)
            if journal.method == "copy" and journal.phase in {"cleaning", "complete", "activated"}:
                return _cleanup_migration(plan, journal, cancel_event=cancel_event, progress=progress)
            if journal.method == "rename":
                return _rename_library(plan, journal, cancel_event=cancel_event, progress=progress)
        return _execute_library_job(plan, journal, cancel_event=cancel_event,
                                    progress=progress, local_root=local_root)
    except Exception as exc:
        journal.fail(exc)
        raise
    finally:
        journal.close()


def _execute_library_job(plan, journal, *, cancel_event=None, progress=None, local_root=None):
    local_root = Path(local_root) if local_root else local_state_directory()
    records = []
    workers = {}
    verified_stats = {}
    baseline_roots = sorted({*plan.source_roots, *[e.root for e in plan.entries]}, key=lambda p: len(p.parts), reverse=True)
    def relocate_baseline(entry):
        source_root = next((root for root in baseline_roots if entry.source.is_relative_to(root)), None)
        if source_root:
            IntegrityCatalog(source_root, local_root).relocate_to(entry.source, entry.destination, IntegrityCatalog(entry.root, local_root))
    def relocate_indexes(entry):
        target_manifest = worker(entry.root).manifest
        source_root = next((root for root in baseline_roots if entry.source.is_relative_to(root)), None)
        source_manifest = ImportManifest(source_root, create=False) if source_root else None
        if source_manifest is not None and source_manifest.path.exists():
            source_manifest.relocate_destinations(entry.source, entry.destination,
                expected_source=entry.snapshot, target_manifest=target_manifest,
                destination_snapshot=verified_stats[entry.destination])
        if source_manifest is None or source_manifest.path != target_manifest.path:
            target_manifest.relocate_destinations(entry.source, entry.destination, expected_source=entry.snapshot,
                destination_snapshot=verified_stats[entry.destination])
    committed = journal.files()
    finalizing = plan.mode == "migrate" and journal.phase in {"finalizing", "complete", "activated"}
    migrated_database = plan.migration_target / ".photocard-organizer" / "manifest.sqlite3" if plan.migration_target else None
    def worker(root):
        if root not in workers:
            config = copy.deepcopy(plan.config)
            config["destination_root"] = str(root)
            workers[root] = Organizer(config, dry_run=plan.mode == "migrate")
        return workers[root]
    deferred = []
    # Existing library files precede incoming conflicts that reference them.
    for index, entry in enumerate(plan.entries):
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Cancelled between files. Completed copies and records are retained.")
        for path in (entry.source, entry.destination, *([entry.backup] if entry.backup else [])):
            checked_path(path)
        previous = committed.get(index)
        if previous and (previous[0] == "done" or (entry.action == "Move" and not entry.source.exists())):
            state, evidence = previous
            if entry.source.exists():
                if not Organizer._same_snapshot(entry.snapshot, entry.source.stat()):
                    raise SourceChangedError(f"Completed source changed; review required: {entry.source}")
            elif entry.action != "Move":
                raise SourceChangedError(f"Completed source is missing; review required: {entry.source}")
            for path, expected in ((entry.destination, evidence["destination"]), (entry.backup, evidence["backup"])):
                if path is None:
                    continue
                if finalizing and path == migrated_database:
                    continue
                if not expected or not Organizer._matches_receipt(path.stat(), expected):
                    raise SourceChangedError(f"Completed destination changed; review required: {path}")
            verified_stats[entry.destination] = SimpleNamespace(**evidence["destination"]) if not (finalizing and entry.destination == migrated_database) else entry.destination.stat()
            if state != "done" and entry.action == "Move":
                relocate_indexes(entry)
                relocate_baseline(entry)
            journal.mark_done(index) if state != "done" else None
            if evidence["event"]:
                records.append(evidence["event"])
            if progress:
                progress(index + 1, len(plan.entries), str(entry.destination))
            continue
        receipt = journal.copy_receipt(index, "destination")
        recovered_rename = bool(entry.action == "Move" and not entry.source.exists() and receipt
                               and entry.destination.exists()
                               and Organizer._matches_receipt(entry.destination.stat(), receipt)
                               and Organizer._same_snapshot(entry.destination.stat(), entry.snapshot))
        if not recovered_rename and not Organizer._same_snapshot(entry.snapshot, entry.source.stat()):
            raise SourceChangedError(f"Source changed since preview: {entry.source}")
        if entry.action == "Keep" and entry.backup is None:
            journal.record(index, "done", entry, None)
            if progress:
                progress(index + 1, len(plan.entries), str(entry.source))
            continue
        engine = worker(entry.root)
        verification = "sha256" if plan.mode == "migrate" and plan.migration_checksum else "size"
        digest = ""
        conflict_type = "filename_conflict"
        if entry.destination.exists():
            known_copy = bool(receipt and Organizer._matches_receipt(entry.destination.stat(), receipt))
            if receipt and not known_copy:
                raise SourceChangedError(f"Completed destination changed; source retained: {entry.destination}")
            if not known_copy and entry.action not in {"Keep", "Duplicate"}:
                if plan.mode == "migrate":
                    raise SourceChangedError(f"Unrecorded file at migration destination; source retained: {entry.destination}")
                destination, _, conflict = engine._resolve_conflict(
                    folder_import_source(entry.source.parent), entry.source, entry.destination, "sha256", compare_content=False)
                updated = replace(entry, destination=destination, existing=entry.destination,
                    backup=plan.backup_root / destination.relative_to(entry.root) if plan.backup_root else None,
                    action="Move" if entry.action == "Move" else "Conflict review")
                journal.retarget(plan, index, updated)
                entry, receipt = updated, None
            elif entry.action == "Duplicate" and not known_copy:
                matches, digest = engine._same_content(entry.source, entry.destination)
                if not matches:
                    raise SourceChangedError(f"Legacy duplicate destination changed: {entry.destination}")
        if entry.existing and entry.existing.exists():
            comparison_source = entry.source if entry.source.exists() else entry.destination
            matches, digest = engine._same_content(comparison_source, entry.existing)
            conflict_type = "exact_duplicate" if matches else "filename_conflict"
        if not entry.destination.exists():
            enough, reason, _ = engine._destination_space_status(entry.root, entry.snapshot.st_size)
            rename_file = entry.action == "Move" and entry.backup is None and same_filesystem(entry.source, entry.destination)
            if not enough and not rename_file:
                raise OSError(reason)
            for _attempt in range(32):
                try:
                    if rename_file:
                        entry.destination.parent.mkdir(parents=True, exist_ok=True)
                        journal.remember_copy(index, "destination", entry.snapshot)
                        relocate_without_overwrite(entry.source, entry.destination)
                    else:
                        actual = engine._copy_and_verify(entry.source, entry.destination, verification, expected_stat=entry.snapshot)
                        if actual and entry.digest and actual != entry.digest:
                            raise SourceChangedError(f"Source content differs from saved plan: {entry.source}")
                        if actual:
                            digest = actual
                        journal.remember_copy(index, "destination", entry.destination.stat())
                    break
                except FileExistsError:
                    checked_path(entry.destination)
                    if plan.mode == "migrate" or not entry.destination.exists():
                        raise
                    destination, digest, conflict = engine._resolve_conflict(
                        folder_import_source(entry.source.parent), entry.source, entry.destination, "sha256")
                    conflict_type = conflict["conflict_type"]
                    updated = replace(entry, destination=destination, existing=entry.destination,
                        backup=plan.backup_root / destination.relative_to(entry.root) if plan.backup_root else None,
                        action="Move" if entry.action == "Move" else "Conflict review")
                    journal.retarget(plan, index, updated)
                    entry = updated
            else:
                raise OSError("Too many simultaneous destination conflicts; completed files and source are retained")
        current_destination = entry.destination.stat()
        destination_proof = journal.copy_receipt(index, "destination")
        if destination_proof is None and entry.action == "Keep" and entry.destination != entry.source:
            stored = engine.manifest.library_copy_receipt(entry.source)
            if not stored or Path(stored["destination"]) != entry.destination or not Organizer._matches_receipt(entry.snapshot, stored["source"]):
                raise SourceChangedError(f"Saved import mapping changed; source retained: {entry.source}")
            destination_proof = stored["copied"]
        if destination_proof is not None and not Organizer._matches_receipt(current_destination, destination_proof):
            raise SourceChangedError(f"Destination changed before recording completion; source retained: {entry.destination}")
        verified_stats[entry.destination] = SimpleNamespace(**destination_proof) if destination_proof is not None else current_destination
        if entry.existing:
            engine.manifest.record_conflict(source_key=hashlib.sha256(str(entry.source).encode()).hexdigest(),
                card_id="library-merge", source_path=entry.source, existing_path=entry.existing,
                incoming_path=entry.destination, conflict_type=conflict_type, resolution="conflict_folder",
                content_checksum=digest)
        if entry.backup:
            backup_root = plan.backup_root
            if backup_root is None:
                raise ValueError("Backup destination was not retained in the job plan")
            if entry.backup.exists():
                existing_backup_stat = entry.backup.stat()
                review = entry.root / engine._safe_prefix(plan.config["safety"]["conflict_folder"]) / "Backup conflicts" / plan.id / entry.destination.relative_to(entry.root)
                review_receipt = journal.copy_receipt(index, "backup_conflict")
                existing_conflict = journal.copy_receipt(index, "backup_conflict_existing")
                unchanged_conflict = bool(existing_conflict and review_receipt and review.exists()
                    and Organizer._matches_receipt(existing_backup_stat, existing_conflict)
                    and Organizer._matches_receipt(review.stat(), review_receipt))
                backup_receipt = journal.copy_receipt(index, "backup")
                matches = bool(backup_receipt and Organizer._matches_receipt(entry.backup.stat(), backup_receipt))
                if backup_receipt and not matches:
                    raise SourceChangedError(f"Completed backup changed; source retained: {entry.backup}")
                if not matches:
                    backup_manifest = engine._replica_manifest(backup_root)
                    stored = backup_manifest.library_copy_receipt(entry.destination)
                    matches = bool(stored and Path(stored["destination"]) == entry.backup
                        and Organizer._matches_receipt(entry.destination.stat(), stored["source"])
                        and Organizer._matches_receipt(entry.backup.stat(), stored["copied"]))
                    if not matches and not unchanged_conflict:
                        matches, _ = engine._same_content(entry.destination, entry.backup)
                if not matches:
                    if plan.mode == "migrate":
                        raise SourceChangedError(f"Backup conflict; migration source retained: {entry.backup}")
                    if not review.exists():
                        engine._copy_and_verify(entry.destination, review, "size")
                        journal.remember_copy(index, "backup_conflict", review.stat())
                    elif not review_receipt or not Organizer._matches_receipt(review.stat(), review_receipt):
                        raise SourceChangedError(f"Backup conflict review file changed: {review}")
                    engine.manifest.record_conflict(source_key=f"backup:{plan.id}:{index}", card_id="library-backup",
                        source_path=entry.destination, existing_path=entry.backup, incoming_path=review,
                        conflict_type="filename_conflict", resolution="conflict_folder")
                    journal.remember_copy(index, "backup_conflict_existing", existing_backup_stat)
                    deferred.append(str(entry.backup))
                    if progress:
                        progress(index + 1, len(plan.entries), str(review))
                    continue
            else:
                ancestor = backup_root
                while not ancestor.exists():
                    if ancestor.parent == ancestor:
                        raise FileNotFoundError(f"Backup volume is unavailable: {backup_root}")
                    ancestor = ancestor.parent
                enough, reason, _ = engine._destination_space_status(ancestor, entry.snapshot.st_size)
                if not enough:
                    raise OSError(reason)
                engine._copy_and_verify(entry.destination, entry.backup, "size")
            journal.remember_copy(index, "backup", entry.backup.stat())
            if plan.mode != "migrate":
                engine._replica_manifest(backup_root).record_library_copy(entry.destination, entry.destination.stat(), entry.backup)
        event = dict(session=plan.id, source=str(entry.source), destination=str(entry.destination),
                     backup=str(entry.backup) if entry.backup else "", sha256=digest, action=entry.action,
                     verification=verification, conflict_type=conflict_type if entry.existing else "")
        logs = [local_root / "library-jobs" / f"{plan.id}.jsonl"]
        if plan.mode != "migrate":
            logs.append(entry.root / ".photocard-organizer" / "library-jobs" / f"{plan.id}.jsonl")
        if entry.backup:
            logs.append(plan.backup_root / ".photocard-organizer" / "library-jobs" / f"{plan.id}.jsonl")
        append_record(logs, event)
        if plan.mode != "migrate":
            engine.manifest.record_library_copy(entry.source, entry.snapshot, entry.destination,
                destination_snapshot=verified_stats[entry.destination])
        # A committed destination and audit trail must exist before source cleanup.
        journal.record(index, "ready", entry, event, destination_snapshot=verified_stats[entry.destination])
        if entry.action == "Move":
            if (entry.source.exists() and not Organizer._same_snapshot(entry.snapshot, entry.source.stat())) or not Organizer._same_snapshot(verified_stats[entry.destination], entry.destination.stat()):
                raise SourceChangedError("Content changed before source removal; source retained")
            relocate_indexes(entry)
            relocate_baseline(entry)
            if (entry.source.exists() and not Organizer._same_snapshot(entry.snapshot, entry.source.stat())) or not Organizer._same_snapshot(verified_stats[entry.destination], entry.destination.stat()):
                raise SourceChangedError("File changed during index updates; source retained")
            entry.source.unlink(missing_ok=True)
        journal.mark_done(index)
        records.append(event)
        if progress:
            progress(index + 1, len(plan.entries), str(entry.destination))
    if deferred:
        raise OSError(f"{len(deferred)} backup conflicts await review. Completed files are retained; retry this operation after resolving them.")
    if plan.mode == "migrate":
        # Do not activate a partial migration or one whose source changed during copying.
        original = set(walk_files(plan.source_roots[0], plan.config, all_files=True))
        if original != {e.source for e in plan.entries}:
            raise SourceChangedError("Migration source inventory changed; original location remains active")
        for entry in plan.entries:
            if not Organizer._same_snapshot(entry.snapshot, entry.source.stat()) or not Organizer._same_snapshot(verified_stats[entry.destination], entry.destination.stat()):
                raise SourceChangedError("Migration source or destination changed; original location remains active")
        journal.set_phase("finalizing")
        relocate_migrated_index(plan.source_roots[0], plan.migration_target,
            evidence={entry.source: (entry.snapshot, verified_stats[entry.destination]) for entry in plan.entries})
        append_record([plan.migration_target / ".photocard-organizer" / "library-jobs" / f"{plan.id}.jsonl"],
                      dict(session=plan.id, action="migration verified", source=str(plan.source_roots[0]), destination=str(plan.migration_target),
                           verification="sha256" if plan.migration_checksum else "size"))
        if not plan.keep_originals:
            # The index is intentionally changed by finalization. Capture its new
            # identity before any source cleanup can begin.
            for index, entry in enumerate(plan.entries):
                if entry.destination == migrated_database:
                    journal.refresh_destination(index, entry.destination.stat())
                else:
                    journal.mark_done(index)
            journal.anchor_target(plan.migration_target)
            journal.set_phase("cleaning")
            return _cleanup_migration(plan, journal, cancel_event=cancel_event, progress=progress)
    journal.set_phase("complete")
    return records


def _cleanup_migration(plan, journal, *, cancel_event=None, progress=None):
    source = plan.source_roots[0]
    evidence = journal.files()
    if source.exists():
        current = set(walk_files(source, plan.config, all_files=True))
        if current - {entry.source for entry in plan.entries}:
            raise SourceChangedError("New files appeared in the old library. Source cleanup needs review.")
    records = []
    for index, entry in enumerate(plan.entries):
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Migration cleanup paused; use Resume interrupted operation.")
        checked_path(entry.source)
        state, saved = evidence[index]
        for path, expected in ((entry.destination, saved["destination"]), (entry.backup, saved["backup"])):
            if path is not None and not Organizer._same_snapshot(checked_path(path).stat(), SimpleNamespace(**expected)):
                raise SourceChangedError(f"Destination changed before cleanup; source retained: {path}")
        if entry.source.exists():
            if state == "cleaned" or not Organizer._same_snapshot(entry.source.stat(), entry.snapshot):
                raise SourceChangedError(f"Source changed before cleanup; retained: {entry.source}")
            entry.source.unlink()
        journal.record(index, "cleaned", entry, saved["event"])
        records.append(saved["event"])
        if progress:
            progress(index + 1, len(plan.entries), str(entry.source))
    if source.exists():
        for current, _names, _files in os.walk(source, topdown=False):
            checked_path(Path(current)).rmdir()
    journal.set_phase("complete")
    return records


def _rename_library(plan, journal, *, cancel_event=None, progress=None):
    source, target = plan.source_roots[0], plan.migration_target
    checked_path(source)
    checked_path(target)
    if source.exists():
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Migration cancelled before renaming the library")
        if set(walk_files(source, plan.config, all_files=True)) != {e.source for e in plan.entries}:
            raise SourceChangedError("Library contents changed since preview; generate a new plan")
        for entry in plan.entries:
            if not Organizer._same_snapshot(entry.source.stat(), entry.snapshot):
                raise SourceChangedError(f"Source changed since preview: {entry.source}")
        journal.set_phase("renaming")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.rmdir()  # Only an empty chosen destination may be removed.
        relocate_without_overwrite(source, target)
        journal.anchor_target(target)
    elif journal.phase == "cleaning":
        return _cleanup_migration(plan, journal, cancel_event=cancel_event, progress=progress)
    hashes = []
    verified_stats = {}
    for index, entry in enumerate(plan.entries):
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Migration paused; use Resume interrupted operation.")
        # A folder rename preserves each file's identity and contents.
        is_index = entry.destination == target / ".photocard-organizer/manifest.sqlite3"
        current = checked_path(entry.destination).stat()
        if not (is_index and journal.phase in {"finalizing", "complete", "activated"}):
            if not Organizer._same_snapshot(current, entry.snapshot):
                raise SourceChangedError(f"Relocated file changed; review required: {entry.destination}")
        verified_stats[entry.source] = current
        if plan.migration_checksum and not is_index:
            digest = checksum(entry.destination)
            if entry.digest and digest != entry.digest:
                raise SourceChangedError(f"Saved checksum differs: {entry.destination}")
            hashes.append(dict(path=str(entry.destination.relative_to(target)), sha256=digest))
        if progress:
            progress(index + 1, len(plan.entries), str(entry.destination))
    journal.set_phase("finalizing")
    relocate_migrated_index(source, target,
        evidence={entry.source: (entry.snapshot, verified_stats[entry.source]) for entry in plan.entries})
    event = dict(session=plan.id, source=str(source), destination=str(target), action="library renamed",
                 verification="sha256" if plan.migration_checksum else "filesystem rename", checksums=hashes)
    append_record([target / ".photocard-organizer/library-jobs" / f"{plan.id}.jsonl"], event)
    journal.set_phase("complete")
    return [event]


def relocate_migrated_index(source, destination, *, evidence=None):
    """Update live index paths; historical transfer records remain historical."""
    database = destination / ".photocard-organizer" / "manifest.sqlite3"
    if not database.exists():
        return
    with closing(sqlite3.connect(database)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Migrated library index failed its integrity check")
        with connection:
            _relocate_migrated_receipts(connection, source, destination, evidence or {})
            for table, columns in {
                "imports": ("destination_path",),
                "pending_imports": ("source_path", "destination_path"),
                "library_source_receipts": ("source_path", "destination_path"),
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


def _relocate_migrated_receipts(connection, source, destination, evidence):
    from .job_journal import snapshot
    def refreshed(path, stored):
        path = Path(path)
        pair = evidence.get(path)
        if pair is None and path.is_relative_to(destination):
            pair = evidence.get(source / path.relative_to(destination))
        if pair is not None and Organizer._matches_receipt(pair[0], json.loads(stored)):
            return json.dumps(snapshot(pair[1]))
        return stored
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "library_source_receipts" in tables:
        for key, target, original, copied in connection.execute(
                "SELECT source_path, destination_path, source_snapshot, copied_snapshot FROM library_source_receipts").fetchall():
            connection.execute("UPDATE library_source_receipts SET source_snapshot=?, copied_snapshot=? WHERE source_path=?",
                (refreshed(key, original), refreshed(target, copied), key))
    if "pending_copy_receipts" in tables and "pending_imports" in tables:
        rows = connection.execute("SELECT r.source_key, p.source_path, p.destination_path, r.source_snapshot, r.destination_snapshot "
            "FROM pending_copy_receipts r JOIN pending_imports p ON p.source_key=r.source_key").fetchall()
        for key, origin, target, original, copied in rows:
            payload = json.loads(refreshed(target, copied))
            # Preserve conflict review details while updating only proven identity.
            conflict = json.loads(copied).get("conflict")
            if conflict:
                payload["conflict"] = dict(conflict)
                for name in ("source_path", "existing_path", "incoming_path"):
                    value = payload["conflict"].get(name)
                    if value and Path(value).is_relative_to(source):
                        payload["conflict"][name] = str(destination / Path(value).relative_to(source))
            connection.execute("UPDATE pending_copy_receipts SET source_snapshot=?, destination_snapshot=? WHERE source_key=?",
                (refreshed(origin, original), json.dumps(payload), key))
    if "backup_conflict_receipts" in tables and "conflicts" in tables:
        rows = connection.execute("SELECT r.source_key, r.evidence, c.source_path FROM backup_conflict_receipts r "
            "JOIN conflicts c ON c.id=(SELECT MAX(id) FROM conflicts WHERE source_key=r.source_key)").fetchall()
        for key, stored, origin in rows:
            payload = json.loads(stored)
            incoming = Path(payload["incoming_path"])
            payload["source"] = json.loads(refreshed(origin, json.dumps(payload["source"])))
            payload["incoming"] = json.loads(refreshed(str(incoming), json.dumps(payload["incoming"])))
            if incoming.is_relative_to(source):
                payload["incoming_path"] = str(destination / incoming.relative_to(source))
            connection.execute("UPDATE backup_conflict_receipts SET evidence=? WHERE source_key=?", (json.dumps(payload), key))
