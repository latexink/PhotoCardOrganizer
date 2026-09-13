from __future__ import annotations

import copy
import os
import threading
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from .discovery import folder_import_source
from .brackets import assign_long_exposure_groups
from .library_state import read_library_metadata
from .metadata import extract_metadata
from .models import CardMarker, MediaMetadata
from .organizer import Organizer, SourceChangedError
from .qt_common import preset_folder_segments
from .atomic_copy import same_filesystem, relocate_without_overwrite
from .integrity import IntegrityCatalog, checksum, checked_path
from .config import library_media_rule


@dataclass(frozen=True)
class ReorganizationEntry:
    source: Path
    destination: Path
    media_kind: str
    snapshot: os.stat_result
    metadata: MediaMetadata
    status: str
    metadata_complete: bool = True


@dataclass
class ReorganizationPlan:
    config: dict
    card: CardMarker
    entries: list[ReorganizationEntry]
    cleanup_directories: list[Path]

    @property
    def changes(self) -> int:
        return sum(entry.status != "Already organized" for entry in self.entries)


def build_reorganization_plan(config: dict, preset: str, media: set[str], *,
                              cancel_event: threading.Event | None = None,
                              progress_callback=None) -> ReorganizationPlan:
    candidate = copy.deepcopy(config)
    root = Path(candidate["destination_root"]).expanduser().resolve()
    if not root.is_dir() or Organizer._is_link_like(Path(candidate["destination_root"])):
        raise ValueError("Choose an available library folder, not a linked folder.")
    read_library_metadata(root)
    if not media:
        raise ValueError("Select at least one media type.")
    effective_rules = {kind: library_media_rule(candidate, root, kind) for kind in candidate["media_rules"]}
    # The plan freezes the effective layout; do not let stored overrides mask
    # a new preset selected explicitly for this operation.
    for library in candidate.get("library_destinations", []):
        if Path(library["root"]).expanduser().resolve() == root:
            library["organization_overrides"] = {}
    for kind, rule in candidate["media_rules"].items():
        rule.update(effective_rules[kind])
        rule["enabled"] = kind in media
        rule["folder_segments"] = preset_folder_segments(kind, preset, rule["folder_segments"])
        if preset != "Use current detailed rules":
            rule["filename_template"] = "{original}"
    candidate["monitor"]["settle_seconds"] = 0
    candidate["local_history"]["enabled"] = True
    candidate["local_history"]["require_before_source_delete"] = True
    candidate["safety"]["exact_duplicate_policy"] = "conflict_folder"
    candidate["safety"]["manual_duplicate_prompt"] = False
    candidate["safety"]["fallback_destination_roots"] = []
    candidate["safety"]["manual_error_prompt"] = False
    candidate["safety"]["manual_space_prompt"] = False
    card = replace(folder_import_source(root, name=f"Reorganize {root.name}", action="move"),
                   source_type="reorganization", card_id=f"reorganize-{candidate['instance']['library_id']}")
    planner = Organizer(candidate, dry_run=True)
    protected = {
        root / ".photocard-organizer", root / candidate["identification"]["folder_name"],
        root / planner._safe_prefix(candidate["safety"]["conflict_folder"]),
    }
    if root in protected:
        raise ValueError("The conflict folder must be a subfolder of the library.")
    entries = []
    directories = []
    for current_text, names, filenames in os.walk(root):
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Preview cancelled.")
        current = Path(current_text)
        names[:] = sorted(name for name in names
                          if current / name not in protected and not Organizer._is_link_like(current / name))
        directories.extend(current / name for name in names)
        for filename in sorted(filenames):
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Preview cancelled.")
            source = current / filename
            kind = planner.classify(source)
            if not kind or Organizer._is_link_like(source):
                continue
            snapshot = source.stat()
            segments = candidate["media_rules"][kind]["folder_segments"]
            needs_metadata = any("{" in value and value not in {"{media}", "{ext}", "{card}", "{card_id}"} for value in segments)
            needs_metadata = needs_metadata or candidate["media_rules"][kind]["filename_template"] != "{original}"
            metadata = (planner.manifest.cached_metadata(source, kind) or extract_metadata(source, kind)) if needs_metadata else MediaMetadata(
                captured_at=datetime.fromtimestamp(snapshot.st_mtime), media_kind=kind,
            )
            if candidate["location"].get("online_place_names") and metadata.latitude is not None and metadata.longitude is not None:
                precision = int(candidate["location"].get("coordinate_precision", 4))
                key = f"{metadata.latitude:.{precision}f},{metadata.longitude:.{precision}f}"
                metadata.location_name = planner.manifest.get_location(key) or ""
            entries.append(ReorganizationEntry(source, source, kind, snapshot, metadata, "Pending", needs_metadata))
            if progress_callback and len(entries) % 25 == 0:
                progress_callback(len(entries), 0, source.name)
    assign_long_exposure_groups(
        [(entry.source, entry.media_kind) for entry in entries],
        {entry.source: entry.metadata for entry in entries},
        candidate.get("organization", {}).get("long_exposure_brackets", {}),
    )
    reserved = set()
    for index, entry in enumerate(entries):
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Preview cancelled.")
        target = planner._destination_for(card, entry.source, entry.media_kind, entry.metadata)
        resolved = target.resolve()
        if root not in resolved.parents or any(resolved == p or p in resolved.parents for p in protected):
            raise ValueError(f"The proposed destination is outside the media area: {target}")
        if resolved == entry.source.resolve():
            status = "Already organized"
        else:
            status = "Conflict review" if target.exists() or resolved in reserved else "Move"
        reserved.add(resolved)
        entries[index] = replace(entry, destination=target, status=status)
    # Destination ordering reduces directory switching without changing move verification.
    entries.sort(key=lambda entry: str(entry.destination).casefold())
    return ReorganizationPlan(candidate, card, entries, directories)


class ReorganizationOrganizer(Organizer):
    def __init__(self, plan: ReorganizationPlan, **kwargs):
        super().__init__(plan.config, **kwargs)
        self.plan = plan
        self.entries = {entry.source: entry for entry in plan.entries}
        self._recovered_sources = set()
        self._integrity_records = None
        self._folder_groups = self._plan_folder_moves()
        self._prepared = {}
        self._intents = {}
        self._catalog = IntegrityCatalog(self.destination_root, self.config.get("local_history", {}).get("directory") or None)

    def _plan_folder_moves(self):
        candidates = {}
        root = self.destination_root.resolve()
        for entry in self.plan.entries:
            relative = entry.source.relative_to(root)
            if len(relative.parts) < 2 or entry.status in {"Already organized", "Conflict review"}:
                continue
            source_folder = root / relative.parts[0]
            suffix = entry.source.relative_to(source_folder)
            if len(suffix.parts) > len(entry.destination.relative_to(root).parts):
                continue
            target_folder = entry.destination.parents[len(suffix.parts) - 1]
            if target_folder == root or target_folder.exists() or target_folder == source_folder:
                continue
            if source_folder in target_folder.parents or target_folder in source_folder.parents:
                continue
            candidates.setdefault((source_folder, target_folder), []).append(entry)
        result = {}
        for (source_folder, target_folder), entries in candidates.items():
            if len(entries) < 2 or not same_filesystem(source_folder, target_folder):
                continue
            if all(e.destination == target_folder / e.source.relative_to(source_folder) for e in entries):
                actual = {p for p in source_folder.rglob("*") if p.is_file() or self._is_link_like(p)}
                if actual == {e.source for e in entries}:
                    for entry in entries:
                        result[entry.source] = (source_folder, target_folder, entries)
        return result

    def _finish_rename(self, intent):
        source = checked_path(Path(intent["source"]))
        destination = checked_path(Path(intent["destination"]))
        if self.destination_root.resolve() not in source.parents or self.destination_root.resolve() not in destination.parents:
            raise ValueError("Rename recovery paths must belong to this library")
        current = destination.stat()
        if [current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns] != intent["snapshot"]:
            raise SourceChangedError("Relocated file changed before catalog update. Integrity review is required.")
        if source.exists():
            if not os.path.samefile(source, destination):
                raise SourceChangedError("Source path has been reused; recovery requires review")
            source.unlink()
        self.manifest.relocate_destinations(source, destination)
        self._catalog.relocate(source, destination)
        if self._integrity_records is not None:
            old = source.relative_to(self.destination_root).as_posix()
            if old in self._integrity_records:
                self._integrity_records[destination.relative_to(self.destination_root).as_posix()] = self._integrity_records.pop(old)
        self.manifest.clear_rename_intent(source)
        self._intents.pop(str(source), None)
        self._recovered_sources.add(source)
        entry = self.entries.get(source)
        if entry and entry.metadata_complete:
            try:
                self.manifest.cache_metadata(destination, entry.metadata)
            except (OSError, sqlite3.Error):
                pass

    def scan_card(self, card, *, allow_destructive=False):
        self._prepared.clear()
        if allow_destructive and not self.dry_run:
            self._intents = {intent["source"]: intent for intent in self.manifest.rename_intents()}
            for intent in list(self._intents.values()):
                source, destination = Path(intent["source"]), Path(intent["destination"])
                if destination.exists() and (not source.exists() or os.path.samefile(source, destination)):
                    self._finish_rename(intent)
        result = super().scan_card(card, allow_destructive=allow_destructive)
        retained = sum(source.exists() for source in self._prepared)
        if retained:
            result.warnings.append(f"{retained} prepared folder members remain at their source after an incomplete folder move. Retry to resume.")
        return result

    def _source_files(self, card):
        if card != self.plan.card or card.root.resolve() != self.destination_root.resolve():
            raise ValueError("This reorganization belongs to a different library.")
        return iter((entry.source, entry.media_kind) for entry in self.plan.entries if entry.source not in self._recovered_sources)

    def _destination_for(self, card, source, media_kind, metadata):
        destination = self.entries[source].destination
        root = self.destination_root.resolve()
        if root not in destination.resolve().parents:
            raise ValueError("The destination changed since preview and is outside the library.")
        return destination

    def _process_source(self, **kwargs):
        entry = self.entries[kwargs["source"]]
        if self._is_link_like(entry.source) or any(self._is_link_like(p) for p in entry.source.parents):
            raise SourceChangedError("The source became a linked path after preview.")
        if not self._same_snapshot(kwargs["source_stat"], entry.snapshot):
            raise SourceChangedError("File changed since preview. Generate a new preview.")
        kwargs["metadata"] = copy.deepcopy(entry.metadata)
        if (entry.source != entry.destination and same_filesystem(entry.source, entry.destination)
                and self.manifest.pending(kwargs["source_key"]) is None):
            return self._rename_source(entry, **kwargs)
        return super()._process_source(**kwargs)

    def _rename_source(self, entry, **kwargs):
        stats = kwargs["stats"]
        card = kwargs["card"]
        if not kwargs["allow_destructive"] and not self.dry_run:
            stats.blocked += 1
            return
        # Reuse a prepared path after a log/backup failure, before allocating another conflict name.
        intent = self._intents.get(str(entry.source)) if not self.dry_run else None
        conflict_info = None
        if intent:
            destination = Path(intent["destination"])
            conflict_info = intent.get("conflict")
            if destination.exists():
                raise FileExistsError(f"Prepared rename target is now occupied: {destination}")
        else:
            destination, _, conflict_info = self._resolve_conflict(card, entry.source, entry.destination, "sha256", compare_content=False)
        if destination is None:
            stats.blocked += 1
            return
        checked_path(destination)
        if not same_filesystem(entry.source, destination):
            return super()._process_source(**kwargs)
        if self.dry_run:
            stats.imported += 1
            return
        catalog = self._catalog
        if self._integrity_records is None:
            self._integrity_records = catalog.records()
        records = self._integrity_records
        source_relative = entry.source.relative_to(self.destination_root).as_posix()
        target_relative = destination.relative_to(self.destination_root).as_posix()
        if target_relative in records:
            raise ValueError("The rename destination already has an integrity baseline. Review it in Integrity first.")
        if self.config.get("organization", {}).get("checksum_new_baselines", False) and source_relative not in records:
            digest = checksum(entry.source)
            if not self._same_snapshot(entry.source.stat(), entry.snapshot):
                raise SourceChangedError("Source changed while creating its baseline")
            catalog.record(entry.source, digest, "sha256", verified=False)
            records[source_relative] = {"digest": digest, "algorithm": "sha256"}
        snapshot = entry.source.stat()
        if conflict_info:
            conflict_info = {key: str(value) if isinstance(value, Path) else value for key, value in conflict_info.items()}
        intent = {"source": str(entry.source), "destination": str(destination),
                  "snapshot": [snapshot.st_dev, snapshot.st_ino, snapshot.st_size, snapshot.st_mtime_ns],
                  "conflict": conflict_info}
        self.manifest.record_rename_intent(intent)
        self._intents[str(entry.source)] = intent
        replicas, ok = self._replicate_file(card, destination, stats, source_override=entry.source)
        if not ok:
            stats.blocked += 1
            return
        if conflict_info:
            self.manifest.record_conflict(source_key=kwargs["source_key"], card_id=card.card_id,
                source_path=entry.source, existing_path=Path(conflict_info["existing_path"]), incoming_path=destination,
                conflict_type="filename_conflict", resolution="conflict_folder", content_checksum="")
        portable_ok, local_ok = self._record_transfer(
            kwargs["session"], kwargs["local_sessions"], card, entry.source, kwargs["relative_path"], destination,
            kwargs["source_key"], snapshot.st_size, snapshot.st_mtime_ns, "move", "rename", "", replicas, stats,
        )
        if not portable_ok or not local_ok:
            stats.blocked += 1
            return
        if not self._same_snapshot(entry.source.stat(), snapshot):
            raise SourceChangedError("Source changed before relocation")
        group = self._folder_groups.get(entry.source)
        if group and destination == entry.destination:
            self._prepared[entry.source] = intent
            source_folder, target_folder, members = group
            if not all(member.source in self._prepared for member in members):
                return
            actual = {p for p in source_folder.rglob("*") if p.is_file() or self._is_link_like(p)}
            if actual != {member.source for member in members}:
                raise SourceChangedError("Folder contents changed since planning; files were retained")
            for member in members:
                checked_path(member.source)
                if not self._same_snapshot(member.source.stat(), member.snapshot):
                    raise SourceChangedError("Folder member changed; files were retained")
            target_folder.parent.mkdir(parents=True, exist_ok=True)
            checked_path(target_folder)
            try:
                relocate_without_overwrite(source_folder, target_folder)
            except OSError:
                if not source_folder.exists() or target_folder.exists():
                    raise
                # Some filesystems lack a no-replace directory primitive. Fall back to file renames.
                for member in members:
                    member.destination.parent.mkdir(parents=True, exist_ok=True)
                    relocate_without_overwrite(member.source, member.destination)
            for member in members:
                self._complete_rename(self._prepared[member.source], member.snapshot.st_size, stats)
            self._emit("success", f"Relocated folder: {source_folder.name}", source=str(source_folder), destination=str(target_folder))
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        checked_path(destination)
        relocate_without_overwrite(entry.source, destination)
        self._complete_rename(intent, snapshot.st_size, stats)

    def _complete_rename(self, intent, size, stats):
        try:
            self._finish_rename(intent)
        except (OSError, ValueError, sqlite3.Error) as exc:
            # The media is already at its new path. Retain the intent for idempotent index repair.
            stats.blocked += 1
            stats.warnings.append(f"Relocated {Path(intent['source']).name}; catalog update pending: {exc}")
            return
        stats.imported += 1
        stats.bytes_imported += size
        self._emit("success", f"Relocated: {Path(intent['source']).name}", source=intent["source"], destination=intent["destination"])

    def _resolve_conflict(self, *args, **kwargs):
        result = super()._resolve_conflict(*args, **kwargs)
        destination = result[0]
        if destination is not None and self.destination_root.resolve() not in destination.resolve().parents:
            raise ValueError("The conflict destination no longer belongs to this library.")
        return result

    def remove_empty_folders(self):
        for directory in sorted(self.plan.cleanup_directories, key=lambda path: len(path.parts), reverse=True):
            try:
                if not self._is_link_like(directory):
                    directory.rmdir()
            except OSError:
                pass
