from __future__ import annotations

import copy
import hashlib
import os
import re
import shutil
import sqlite3
import stat
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path

from .atomic_copy import commit_without_overwrite, create_partial_file
from .brackets import assign_long_exposure_groups, capture_key
from .discovery import capacity_for, discover_cards
from .geocode import ReverseGeocoder
from .io_schedule import serialized_io
from .manifest import ImportManifest
from .metadata import extract_metadata
from .models import (
    ActivityEvent,
    CardMarker,
    DecisionRequest,
    DecisionResult,
    ImportStats,
    MediaMetadata,
)
from .portable_log import PortableTransferHistory, PortableTransferSession
from .templates import render_filename, render_folder, safe_segment
from .transfer_hub import effective_replica_destinations


EventCallback = Callable[[ActivityEvent], None]
DecisionCallback = Callable[[DecisionRequest], DecisionResult]
MAX_DESTINATION_REDIRECTS = 32


class StopCardRequested(Exception):
    pass


class DestinationChangeRequested(Exception):
    def __init__(self, path: str, persist: bool = False):
        super().__init__(path)
        self.path = path
        self.persist = persist


class SourceChangedError(OSError):
    pass


class Organizer:
    def __init__(
        self,
        config: dict,
        *,
        dry_run: bool = False,
        event_callback: EventCallback | None = None,
        decision_callback: DecisionCallback | None = None,
    ):
        self.config = config
        self.dry_run = dry_run
        self.destination_root = Path(config["destination_root"]).expanduser()
        if not dry_run:
            self.destination_root.mkdir(parents=True, exist_ok=True)
        self.manifest = ImportManifest(self.destination_root, create=not dry_run)
        self.geocoder = ReverseGeocoder(config, self.manifest)
        self.event_callback = event_callback
        self.decision_callback = decision_callback
        self._decision_overrides: dict[str, DecisionResult] = {}
        self.replica_destinations = []
        primary_key = os.path.normcase(str(self.destination_root.resolve()))
        for replica in effective_replica_destinations(config):
            root = str(replica.get("root", "")).strip()
            if not replica.get("enabled", True) or not root:
                continue
            replica_root = Path(root).expanduser()
            try:
                replica_key = os.path.normcase(str(replica_root.resolve()))
            except OSError:
                replica_key = os.path.normcase(str(replica_root))
            if replica_key != primary_key:
                self.replica_destinations.append(dict(replica))
        self._extension_map = self._build_extension_map()

    def _emit(self, level: str, message: str, **details: object) -> None:
        if self.event_callback:
            self.event_callback(ActivityEvent(level, message, details=details))

    def _build_extension_map(self) -> dict[str, str]:
        extension_map: dict[str, str] = {}
        for kind, rule in self.config["media_rules"].items():
            if not rule.get("enabled", True):
                continue
            for extension in rule.get("extensions", []):
                extension_map.setdefault(str(extension).lower(), kind)
        return extension_map

    def _decide(self, request: DecisionRequest, fallback_action: str) -> DecisionResult:
        if request.kind in self._decision_overrides:
            return self._decision_overrides[request.kind]
        result = DecisionResult(fallback_action)
        if self.decision_callback:
            try:
                candidate = self.decision_callback(request)
                valid_actions = {action for action, _label in request.options}
                if candidate.action in valid_actions:
                    result = candidate
            except Exception as exc:
                self._emit("error", f"Could not obtain an interactive decision: {exc}")
        if result.apply_to_session and request.allow_apply_to_session:
            self._decision_overrides[request.kind] = result
        return result

    def classify(self, path: Path) -> str | None:
        return self._extension_map.get(path.suffix.lower())

    @staticmethod
    def source_key(card: CardMarker, relative_path: Path, size: int, mtime_ns: int) -> str:
        identity = f"{card.card_id}\0{relative_path.as_posix()}\0{size}\0{mtime_ns}"
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def _source_files(self, card: CardMarker) -> Iterator[tuple[Path, str]]:
        seen: set[Path] = set()
        destination_root = self.destination_root.resolve()
        identity_dir = card.identity_dir.resolve()
        card_root = card.root.resolve()
        ignored_roots: tuple[Path, ...] = tuple(
            (card.root / folder).resolve()
            for folder in card.ignored_source_folders
            if str(folder).strip()
        )

        def ignored(path: Path) -> bool:
            return any(path == root or root in path.parents for root in ignored_roots)

        for source_folder in card.source_folders:
            configured_source = card.root / source_folder
            if self._is_link_like(configured_source):
                continue
            source_root = configured_source.resolve()
            if ignored(source_root) or not source_root.is_dir():
                continue
            try:
                source_root.relative_to(card_root)
            except ValueError:
                continue
            if source_root == destination_root:
                raise ValueError("The import source and destination cannot be the same folder.")
            destination_inside_source = source_root in destination_root.parents
            if not card.recursive:
                try:
                    candidates = sorted(
                        path
                        for path in source_root.iterdir()
                        if path.is_file() and not self._is_link_like(path)
                    )
                except OSError:
                    candidates = []
                for path in candidates:
                    if path in seen:
                        continue
                    seen.add(path)
                    media_kind = self.classify(path)
                    if media_kind:
                        yield path, media_kind
                continue
            for current_root, directory_names, file_names in os.walk(source_root):
                current = Path(current_root)
                retained_directories = []
                for name in sorted(directory_names, key=str.casefold):
                    unresolved_candidate = current / name
                    if self._is_link_like(unresolved_candidate):
                        continue
                    candidate = unresolved_candidate.resolve()
                    if ignored(candidate):
                        continue
                    if card.portable_history and candidate == identity_dir:
                        continue
                    if destination_inside_source and (
                        candidate == destination_root or destination_root in candidate.parents
                    ):
                        continue
                    retained_directories.append(name)
                directory_names[:] = retained_directories
                for filename in sorted(file_names, key=str.casefold):
                    path = current / filename
                    if (
                        path in seen
                        or self._is_link_like(path)
                        or ignored(path)
                        or (card.portable_history and card.identity_dir in path.parents)
                    ):
                        continue
                    seen.add(path)
                    media_kind = self.classify(path)
                    if media_kind:
                        yield path, media_kind

    def source_candidates(self, card: CardMarker) -> list[tuple[Path, str]]:
        return list(self._source_files(card))

    @staticmethod
    def _is_link_like(path: Path) -> bool:
        try:
            if path.is_symlink():
                return True
            is_junction = getattr(path, "is_junction", None)
            return bool(is_junction and is_junction())
        except OSError:
            return True

    @staticmethod
    def _paths_overlap(first: Path, second: Path) -> bool:
        try:
            first = first.expanduser().resolve()
            second = second.expanduser().resolve()
        except OSError:
            first = Path(os.path.abspath(str(first.expanduser())))
            second = Path(os.path.abspath(str(second.expanduser())))
        return first == second or first in second.parents or second in first.parents

    @staticmethod
    @serialized_io
    def _hash_file(path: Path, algorithm: str = "sha256") -> str:
        digest = hashlib.new(algorithm)
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def _same_content(
        cls, source: Path, destination: Path, algorithm: str = "sha256"
    ) -> tuple[bool, str]:
        try:
            if source.stat().st_size != destination.stat().st_size:
                return False, ""
            source_hash = cls._hash_file(source, algorithm)
            return source_hash == cls._hash_file(destination, algorithm), source_hash
        except OSError:
            return False, ""

    @staticmethod
    def _safe_prefix(value: str) -> Path:
        parts = []
        for part in re.split(r"[/\\]+", value):
            part = part.strip()
            if not part or part == ".":
                continue
            if part == "..":
                raise ValueError("The library subfolder cannot contain '..'.")
            parts.append(safe_segment(part))
        return Path(*parts) if parts else Path()

    def _destination_for(self, card: CardMarker, source: Path, media_kind: str, metadata) -> Path:
        rule = self.config["media_rules"][media_kind]
        relative_folder = render_folder(rule["folder_segments"], metadata, card, source)
        filename = render_filename(rule["filename_template"], metadata, card, source)
        return self.destination_root / self._safe_prefix(card.destination_prefix) / relative_folder / filename

    def _resolve_conflict(
        self,
        card: CardMarker,
        source: Path,
        requested: Path,
        checksum_algorithm: str,
        *, compare_content: bool = True,
    ) -> tuple[Path | None, str, dict[str, object] | None]:
        if not requested.exists():
            return requested, "", None
        matches, content_hash = self._same_content(source, requested, checksum_algorithm) if compare_content else (False, "")
        safety = self.config["safety"]
        conflict_type = "exact_duplicate" if matches else "filename_conflict"
        if matches:
            policy = safety.get("exact_duplicate_policy", "rename")
            interactive = bool(
                self.decision_callback and safety.get("manual_duplicate_prompt", False)
            )
        else:
            # Preserve both versions and keep the batch moving. Filename conflicts
            # are reviewed after transfer instead of stopping an active import.
            policy = "conflict_folder"
            interactive = False
        if interactive or policy == "ask":
            result = self._decide(
                DecisionRequest(
                    kind=conflict_type,
                    title="Exact duplicate" if matches else "Destination filename conflict",
                    message=(
                        f"{requested.name} already exists with "
                        f"{'identical' if matches else 'different'} content. "
                        "Both files can be preserved with a new name or an organized conflict folder."
                    ),
                    options=(
                        ("rename", "Append filename"),
                        ("conflict_folder", "Use conflict folder"),
                        ("skip", "Skip file"),
                        ("stop", "Stop this card"),
                    ),
                    default_action="rename",
                    source=source,
                    destination=requested,
                ),
                "rename",
            )
            policy = result.action
        if policy == "stop":
            raise StopCardRequested()
        if policy == "skip":
            return None, content_hash, None

        base_candidate = requested
        if policy == "conflict_folder":
            relative = requested.relative_to(self.destination_root)
            base_candidate = self.destination_root / self._safe_prefix(safety["conflict_folder"]) / relative
            if not base_candidate.exists():
                return base_candidate, content_hash, {
                    "existing_path": requested,
                    "conflict_type": conflict_type,
                    "resolution": policy,
                    "content_checksum": content_hash,
                }

        appendage_template = str(safety.get("conflict_filename_appendage", "_{number}"))
        for number in range(2, 10000):
            appendage = (
                appendage_template.replace("{number}", str(number))
                .replace("{card}", safe_segment(card.name))
                .replace("{card_id}", safe_segment(card.card_id))
                .replace("{stem}", safe_segment(requested.stem))
            )
            if "{number}" not in appendage_template and number > 2:
                appendage = f"{appendage}_{number}"
            appendage = safe_segment(f"x{appendage}", fallback=f"x_{number}")[1:]
            candidate = base_candidate.with_name(
                f"{base_candidate.stem}{appendage}{base_candidate.suffix}"
            )
            if not candidate.exists():
                return candidate, content_hash, {
                    "existing_path": requested,
                    "conflict_type": conflict_type,
                    "resolution": policy,
                    "content_checksum": content_hash,
                }
        raise OSError(f"Could not find an available filename near {requested}")

    def _destination_space_status(self, root: Path, source_size: int) -> tuple[bool, str, bool]:
        capacity = capacity_for(root)
        projected_free = capacity.free_bytes - source_size
        projected_percent = (projected_free / capacity.total_bytes * 100.0) if capacity.total_bytes else 0.0
        minimum_percent = float(self.config["safety"].get("minimum_destination_free_percent", 0))
        minimum_bytes = float(self.config["safety"].get("minimum_destination_free_gb", 0)) * 1024**3
        if projected_free < 0:
            return False, "The destination does not have enough physical free space.", True
        if projected_percent < minimum_percent:
            return False, f"The destination would fall below {minimum_percent:.1f}% free space.", False
        if projected_free < minimum_bytes:
            minimum_gb = float(self.config["safety"].get("minimum_destination_free_gb", 0))
            return False, f"The destination would fall below {minimum_gb:.1f} GB free space.", False
        return True, "", False

    def _fallback_destination(self, source_size: int) -> str:
        for configured in self.config["safety"].get("fallback_destination_roots", []):
            candidate = Path(configured).expanduser()
            try:
                if candidate.resolve() == self.destination_root.resolve():
                    continue
                enough, _reason, _hard_limit = self._destination_space_status(candidate, source_size)
                if enough:
                    return str(candidate)
            except OSError:
                continue
        return ""

    def _space_decision(
        self,
        source: Path,
        destination: Path,
        source_size: int,
        reason: str,
        hard_limit: bool,
    ) -> DecisionResult:
        safety = self.config["safety"]
        if self.decision_callback and safety.get("manual_space_prompt", True):
            options: list[tuple[str, str]] = [
                ("choose_destination", "Choose another destination"),
                ("skip", "Skip file"),
                ("stop", "Stop this card"),
            ]
            if not hard_limit:
                options.insert(0, ("continue", "Continue below reserve"))
            return self._decide(
                DecisionRequest(
                    kind="space_hard" if hard_limit else "space_reserve",
                    title="Destination space issue",
                    message=f"{reason}\n\nFile: {source.name}\nSize: {source_size / 1024**3:.2f} GB",
                    options=tuple(options),
                    default_action="skip",
                    source=source,
                    destination=destination,
                    allow_choose_destination=True,
                ),
                "skip",
            )

        policy = safety.get("space_policy", "fallback_then_block")
        if policy == "fallback_then_block":
            fallback = self._fallback_destination(source_size)
            if fallback:
                return DecisionResult("choose_destination", fallback)
        if policy == "continue_below_reserve" and not hard_limit:
            return DecisionResult("continue")
        return DecisionResult("skip")

    @staticmethod
    def _same_snapshot(current, expected) -> bool:
        if current.st_size != expected.st_size or current.st_mtime_ns != expected.st_mtime_ns:
            return False
        expected_inode = getattr(expected, "st_ino", 0)
        current_inode = getattr(current, "st_ino", 0)
        expected_device = getattr(expected, "st_dev", 0)
        current_device = getattr(current, "st_dev", 0)
        return not (
            expected_inode
            and current_inode
            and (expected_inode != current_inode or expected_device != current_device)
        )

    @serialized_io
    def _copy_and_verify(
        self,
        source: Path,
        destination: Path,
        verification: str,
        expected_stat=None,
    ) -> str:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = create_partial_file(destination.parent)
        content_hash = ""
        snapshot = expected_stat or source.stat()

        def verify_snapshot() -> None:
            if not self._same_snapshot(source.stat(), snapshot):
                raise SourceChangedError(
                    "The source changed during transfer; it will be retried after it settles."
                )

        try:
            verify_snapshot()
            content_hash = self._copy_payload(source, temporary, verification)
            verify_snapshot()
            if snapshot.st_size != temporary.stat().st_size:
                raise OSError("Copied size does not match the source.")
            if verification != "size":
                if content_hash != self._hash_file(temporary, verification):
                    raise OSError(f"{verification.upper()} verification failed.")
            verify_snapshot()
            mode = temporary.stat().st_mode
            if not mode & stat.S_IWRITE:
                temporary.chmod(mode | stat.S_IWRITE)
            try:
                with temporary.open("rb+") as handle:
                    os.fsync(handle.fileno())
            finally:
                if not mode & stat.S_IWRITE:
                    temporary.chmod(mode)
            commit_without_overwrite(temporary, destination)
            if os.name != "nt":
                descriptor = os.open(destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            return content_hash
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _copy_payload(source: Path, destination: Path, verification: str) -> str:
        if verification == "size":
            shutil.copy2(source, destination)
            return ""
        digest = hashlib.new(verification)
        with source.open("rb") as reader, destination.open("wb") as writer:
            for chunk in iter(lambda: reader.read(4 * 1024 * 1024), b""):
                writer.write(chunk)
                digest.update(chunk)
        shutil.copystat(source, destination)
        return digest.hexdigest()

    def _archive_replica_existing(
        self,
        card: CardMarker,
        replica_root: Path,
        relative_path: Path,
        existing_path: Path,
    ) -> Path:
        safety = self.config["safety"]
        archive = (
            replica_root
            / self._safe_prefix(safety.get("conflict_folder", "Conflicts"))
            / "Replica existing"
            / relative_path
        )
        archive.parent.mkdir(parents=True, exist_ok=True)
        if archive.exists():
            template = str(safety.get("conflict_filename_appendage", "_{number}"))
            for number in range(2, 10000):
                appendage = (
                    template.replace("{number}", str(number))
                    .replace("{card}", safe_segment(card.name))
                    .replace("{card_id}", safe_segment(card.card_id))
                    .replace("{stem}", safe_segment(archive.stem))
                )
                if "{number}" not in template and number > 2:
                    appendage = f"{appendage}_{number}"
                appendage = safe_segment(f"x{appendage}", fallback=f"x_{number}")[1:]
                candidate = archive.with_name(f"{archive.stem}{appendage}{archive.suffix}")
                if not candidate.exists():
                    archive = candidate
                    break
            else:
                raise OSError(
                    f"Could not find an available replica archive filename near {archive}"
                )
        shutil.move(str(existing_path), str(archive))
        return archive

    def _replicate_file(
        self,
        card: CardMarker,
        primary_destination: Path,
        stats: ImportStats,
        *, source_override: Path | None = None,
    ) -> tuple[list[dict[str, object]], bool]:
        if not self.replica_destinations:
            return [], True
        relative_path = primary_destination.relative_to(self.destination_root)
        source = source_override or primary_destination
        verification = self.config["safety"].get("replica_verification", "sha256")
        records: list[dict[str, object]] = []
        required_ok = True

        for replica in self.replica_destinations:
            replica_root = Path(replica["root"]).expanduser()
            destination = replica_root / relative_path
            record: dict[str, object] = {
                "id": replica["id"],
                "name": replica["name"],
                "required": bool(replica.get("required", True)),
                "relative_path": relative_path.as_posix(),
                "verification": verification,
                "status": "failed",
            }
            try:
                enough, reason, hard_limit = self._destination_space_status(
                    replica_root, source.stat().st_size
                )
                if not enough:
                    continue_below_reserve = (
                        not hard_limit
                        and self.config["safety"].get("space_policy") == "continue_below_reserve"
                    )
                    if self.decision_callback:
                        options: list[tuple[str, str]] = [
                            ("skip", "Skip this replica"),
                            ("stop", "Stop this card"),
                        ]
                        if not hard_limit:
                            options.insert(0, ("continue", "Continue below reserve"))
                        decision = self._decide(
                            DecisionRequest(
                                kind=f"replica_space_{replica['id']}",
                                title=f"Replica space issue: {replica['name']}",
                                message=reason,
                                options=tuple(options),
                                default_action="skip",
                                source=primary_destination,
                                destination=destination,
                            ),
                            "skip",
                        )
                        if decision.action == "stop":
                            raise StopCardRequested()
                        continue_below_reserve = decision.action == "continue" and not hard_limit
                    if not continue_below_reserve:
                        raise OSError(reason)

                if destination.exists():
                    matches, _checksum = self._same_content(
                        source, destination, verification
                    )
                    if matches:
                        record["status"] = "verified_existing"
                        records.append(record)
                        continue
                    policy = replica.get("conflict_policy", "block")
                    if self.decision_callback:
                        decision = self._decide(
                            DecisionRequest(
                                kind=f"replica_conflict_{replica['id']}",
                                title=f"Replica conflict: {replica['name']}",
                                message=(
                                    "The replica contains different content at the resolved clone path. "
                                    "The existing replica file can be archived before replacement."
                                ),
                                options=(
                                    ("archive_and_replace", "Archive existing and replace"),
                                    ("skip", "Skip this replica"),
                                    ("stop", "Stop this card"),
                                ),
                                default_action=(
                                    "archive_and_replace"
                                    if policy == "archive_and_replace"
                                    else "skip"
                                ),
                                source=primary_destination,
                                destination=destination,
                            ),
                            "skip",
                        )
                        policy = decision.action
                    if policy == "stop":
                        raise StopCardRequested()
                    if policy != "archive_and_replace":
                        raise OSError("Replica path contains different content.")
                    archived = self._archive_replica_existing(
                        card, replica_root, relative_path, destination
                    )
                    record["archived_existing"] = archived.relative_to(replica_root).as_posix()

                self._copy_and_verify(source, destination, verification)
                record["status"] = "verified"
            except StopCardRequested:
                raise
            except Exception as exc:
                record["error"] = str(exc)
                warning = f"Replica {replica['name']} failed for {primary_destination.name}: {exc}"
                stats.warnings.append(warning)
                self._emit("warning", warning, destination=str(destination))
                if replica.get("required", True):
                    required_ok = False
            records.append(record)
        return records, required_ok

    def _record_transfer(
        self,
        session: PortableTransferSession | None,
        local_sessions: list[tuple[PortableTransferSession, bool, str]],
        card: CardMarker,
        source: Path,
        relative_path: Path,
        destination: Path,
        source_key: str,
        source_size: int,
        source_mtime_ns: int,
        action: str,
        verification: str,
        content_hash: str,
        replica_records: list[dict[str, object]],
        stats: ImportStats,
    ) -> tuple[bool, bool]:
        transferred_at = datetime.now(timezone.utc)
        append_arguments = {
            "source_key": source_key,
            "source_relative_path": relative_path.as_posix(),
            "source_size": source_size,
            "source_mtime_ns": source_mtime_ns,
            "destination_relative_path": destination.relative_to(self.destination_root).as_posix(),
            "action": action,
            "verification": verification,
            "content_checksum": content_hash,
            "checksum_algorithm": verification if content_hash else "",
            "transferred_at": transferred_at,
            "replicas": replica_records,
        }
        required_local_recorded = True
        for local_session, required, label in local_sessions:
            try:
                local_session.append_verified(**append_arguments)
            except OSError as exc:
                warning = (
                    f"Transferred {source.name}, but could not update "
                    f"{label} matching history: {exc}"
                )
                stats.warnings.append(warning)
                self._emit("warning", warning, source=str(source))
                if required:
                    required_local_recorded = False

        if not required_local_recorded:
            return False, False

        portable_recorded = session is None
        if session is not None:
            try:
                session.append_verified(**append_arguments)
                portable_recorded = True
            except OSError as exc:
                warning = (
                    f"Transferred {source.name}, but could not update portable history on the card: {exc}"
                )
                stats.warnings.append(warning)
                self._emit("warning", warning, source=str(source))

        if not portable_recorded:
            return False, required_local_recorded

        try:
            self.manifest.record(
                source_key,
                card.card_id,
                relative_path.as_posix(),
                source_size,
                source_mtime_ns,
                destination,
                action,
                verification,
            )
        except (OSError, sqlite3.Error) as exc:
            warning = f"Transferred {source.name}, but could not update the local index: {exc}"
            stats.warnings.append(warning)
            self._emit("warning", warning, source=str(source))
        return portable_recorded, required_local_recorded

    def _process_source(
        self,
        *,
        card: CardMarker,
        source: Path,
        media_kind: str,
        source_stat,
        relative_path: Path,
        source_key: str,
        session: PortableTransferSession | None,
        local_sessions: list[tuple[PortableTransferSession, bool, str]],
        local_history_config: dict,
        stats: ImportStats,
        allow_destructive: bool,
        metadata: MediaMetadata | None = None,
        capture_group: str = "",
    ) -> None:
        safety = self.config["safety"]
        metadata = metadata or self.manifest.cached_metadata(source, media_kind) or extract_metadata(source, media_kind)
        if capture_group and not metadata.capture_group:
            metadata.capture_group = capture_group
        if card.source_type != "reorganization":
            metadata.location_name = self.geocoder.place_name(metadata.latitude, metadata.longitude)
        requested_destination = self._destination_for(card, source, media_kind, metadata)
        if card.source_type == "reorganization" and requested_destination.resolve() == source.resolve():
            stats.skipped += 1
            return
        verification = (
            safety.get("move_checksum_algorithm", "sha256")
            if card.action == "move"
            else safety.get("copy_verification", "size")
        )
        comparison_algorithm = verification if verification != "size" else "sha256"
        destination = None
        content_hash = ""
        conflict_info = None
        resumed_primary = False
        pending = self.manifest.pending(source_key)
        if pending is not None:
            pending_destination = Path(str(pending["destination_path"]))
            try:
                pending_destination.relative_to(self.destination_root)
                matches, pending_hash = self._same_content(
                    source, pending_destination, comparison_algorithm
                )
            except (OSError, ValueError):
                matches, pending_hash = False, ""
            if matches:
                destination = pending_destination
                content_hash = pending_hash
                resumed_primary = True
                verification = comparison_algorithm
                self._emit(
                    "info",
                    f"Resuming pending replicas and records for {source.name}",
                    source=str(source),
                    destination=str(destination),
                )
            else:
                if not self.dry_run:
                    self.manifest.clear_pending(source_key)

        if destination is None:
            destination, content_hash, conflict_info = self._resolve_conflict(
                card, source, requested_destination, comparison_algorithm
            )
        if destination is None:
            stats.blocked += 1
            message = f"Skipped {source.name} because its destination already exists."
            stats.warnings.append(message)
            self._emit("warning", message, source=str(source))
            return

        enough_space, reason, hard_limit = self._destination_space_status(
            self.destination_root, source_stat.st_size
        )
        if not resumed_primary and not enough_space:
            decision = self._space_decision(
                source,
                destination,
                source_stat.st_size,
                reason,
                hard_limit,
            )
            if decision.action == "choose_destination" and decision.value:
                raise DestinationChangeRequested(decision.value, decision.persist)
            if decision.action == "stop":
                raise StopCardRequested()
            if decision.action != "continue" or hard_limit:
                stats.blocked += 1
                warning = f"Blocked {source.name}: {reason}"
                stats.warnings.append(warning)
                self._emit("warning", warning, source=str(source))
                return
            self._emit(
                "warning",
                f"Continuing below the configured destination reserve for {source.name}.",
                source=str(source),
            )

        if self.dry_run:
            stats.imported += 1
            stats.bytes_imported += source_stat.st_size
            self._emit(
                "info",
                f"Would {card.action} {source.name}",
                source=str(source),
                destination=str(destination),
            )
            return

        if not resumed_primary:
            self.manifest.record_pending(
                source_key=source_key,
                card_id=card.card_id,
                source_path=source,
                destination_path=destination,
                source_size=source_stat.st_size,
                source_mtime_ns=source_stat.st_mtime_ns,
            )
            copied_hash = self._copy_and_verify(
                source,
                destination,
                verification,
                expected_stat=source_stat,
            )
            if copied_hash:
                content_hash = copied_hash
            elif content_hash:
                if self._hash_file(destination, comparison_algorithm) != content_hash:
                    raise OSError(
                        f"{comparison_algorithm.upper()} conflict-copy verification failed."
                    )
                verification = comparison_algorithm

        if conflict_info is not None:
            try:
                self.manifest.record_conflict(
                    source_key=source_key,
                    card_id=card.card_id,
                    source_path=source,
                    existing_path=Path(conflict_info["existing_path"]),
                    incoming_path=destination,
                    conflict_type=str(conflict_info["conflict_type"]),
                    resolution=str(conflict_info["resolution"]),
                    content_checksum=str(conflict_info.get("content_checksum", content_hash)),
                )
            except (OSError, sqlite3.Error) as exc:
                warning = f"Could not add {source.name} to conflict review: {exc}"
                stats.warnings.append(warning)
                self._emit("warning", warning, source=str(source))
                if card.source_type == "reorganization":
                    raise

        if resumed_primary and card.source_type == "reorganization":
            conflict_root = self.destination_root / self._safe_prefix(safety["conflict_folder"])
            if conflict_root in destination.parents:
                matches, _ = self._same_content(source, requested_destination, comparison_algorithm)
                self.manifest.record_conflict(
                    source_key=source_key, card_id=card.card_id, source_path=source,
                    existing_path=requested_destination, incoming_path=destination,
                    conflict_type="exact_duplicate" if matches else "filename_conflict",
                    resolution="conflict_folder", content_checksum=content_hash,
                )

        verified_destination_stat = destination.stat()
        replica_records, required_replicas_ok = self._replicate_file(card, destination, stats)
        if not required_replicas_ok:
            stats.blocked += 1
            warning = f"Kept {source.name} pending because a required replica is incomplete."
            stats.warnings.append(warning)
            self._emit("warning", warning, source=str(source))
            return

        portable_recorded, local_recorded = self._record_transfer(
            session,
            local_sessions,
            card,
            source,
            relative_path,
            destination,
            source_key,
            source_stat.st_size,
            source_stat.st_mtime_ns,
            card.action,
            verification,
            content_hash,
            replica_records,
            stats,
        )
        if not portable_recorded or not local_recorded:
            stats.blocked += 1
            warning = f"Kept {source.name} pending because required transfer history is incomplete."
            stats.warnings.append(warning)
            self._emit("warning", warning, source=str(source))
            return
        try:
            if card.source_type != "reorganization":
                self.manifest.clear_pending(source_key)
        except (OSError, sqlite3.Error) as exc:
            warning = f"Completed {source.name}, but could not clear its pending marker: {exc}"
            stats.warnings.append(warning)
            self._emit("warning", warning, source=str(source))
        stats.imported += 1
        stats.bytes_imported += source_stat.st_size
        try:
            self.manifest.cache_metadata(destination, metadata)
        except (OSError, sqlite3.Error):
            pass
        self._emit(
            "success",
            f"{card.action.capitalize()} verified: {source.name}",
            source=str(source),
            destination=str(destination),
        )

        if card.action != "move":
            return
        require_log = self.config["identification"].get("require_log_before_source_delete", True)
        require_local_log = local_history_config.get("require_before_source_delete", True)
        missing_history = []
        if require_log and not portable_recorded:
            missing_history.append("portable")
        if local_sessions and require_local_log and not local_recorded:
            missing_history.append("local")
        if missing_history:
            stats.blocked += 1
            warning = (
                f"Kept {source.name} on the card because "
                f"{' and '.join(missing_history)} history was not recorded."
            )
            stats.warnings.append(warning)
            self._emit("warning", warning, source=str(source))
            return

        delete_attempts = max(1, int(safety.get("io_retry_count", 2)) + 1)
        delete_error = None
        while True:
            for attempt in range(delete_attempts):
                try:
                    if not self._same_snapshot(source.stat(), source_stat) or not self._same_snapshot(destination.stat(), verified_destination_stat):
                        raise SourceChangedError("File changed after verification; source retained")
                    if card.source_type == "reorganization":
                        if self.destination_root.resolve() not in destination.resolve().parents:
                            raise SourceChangedError("The destination no longer belongs to this library.")
                        if not self._same_snapshot(source.stat(), source_stat):
                            raise SourceChangedError("Source changed before cleanup; the original was retained.")
                        self.manifest.relocate_destinations(source, destination)
                        from .integrity import IntegrityCatalog
                        catalog = IntegrityCatalog(self.destination_root, self.config.get("local_history", {}).get("directory") or None)
                        catalog.relocate(source, destination)
                        if self.config.get("organization", {}).get("checksum_new_baselines", False):
                            relative = destination.relative_to(catalog.root).as_posix()
                            if relative not in catalog.records():
                                catalog.record(destination, content_hash, verification, verified=True)
                    source.unlink()
                    if card.source_type == "reorganization":
                        try:
                            self.manifest.clear_pending(source_key)
                        except (OSError, sqlite3.Error) as exc:
                            stats.warnings.append(f"Moved {source.name}, but could not clear its pending marker: {exc}")
                    return
                except OSError as exc:
                    delete_error = exc
                    if attempt + 1 < delete_attempts:
                        time.sleep(float(safety.get("io_retry_delay_seconds", 1.0)))
            decision = DecisionResult("keep")
            if self.decision_callback and safety.get("manual_error_prompt", True):
                decision = self._decide(
                    DecisionRequest(
                        kind="source_delete_error",
                        title="Source cleanup failed",
                        message=(
                            f"{source.name} was copied and verified, but could not be removed.\n\n"
                            f"{type(delete_error).__name__}: {delete_error}"
                        ),
                        options=(
                            ("retry", "Retry source deletion"),
                            ("keep", "Keep source"),
                            ("stop", "Keep source and stop card"),
                        ),
                        default_action="keep",
                        source=source,
                        destination=destination,
                        allow_apply_to_session=False,
                    ),
                    "keep",
                )
            if decision.action == "retry":
                continue
            stats.failed += 1
            warning = f"Verified {source.name}, but could not remove the source: {delete_error}"
            stats.errors.append(warning)
            self._emit("error", warning, source=str(source))
            if decision.action == "stop":
                raise StopCardRequested()
            return

    def _file_error_decision(self, source: Path, error: Exception) -> DecisionResult:
        safety = self.config["safety"]
        if self.decision_callback and safety.get("manual_error_prompt", True):
            return self._decide(
                DecisionRequest(
                    kind="file_error",
                    title="File processing error",
                    message=f"{source.name} could not be processed.\n\n{type(error).__name__}: {error}",
                    options=(
                        ("retry", "Retry file"),
                        ("choose_destination", "Choose another destination"),
                        ("skip", "Skip file"),
                        ("stop", "Stop this card"),
                    ),
                    default_action="skip",
                    source=source,
                    allow_choose_destination=True,
                    allow_apply_to_session=False,
                ),
                "skip",
            )
        if not source.exists():
            return DecisionResult("stop")
        policy = safety.get("file_error_policy", "retry_then_continue")
        return DecisionResult("stop" if policy == "stop_card" else "skip")

    def scan_card(self, card: CardMarker, *, allow_destructive: bool = False) -> ImportStats:
        with self.manifest.processing_session():
            return self._scan_card(card, allow_destructive=allow_destructive)

    def _scan_card(self, card: CardMarker, *, allow_destructive: bool = False) -> ImportStats:
        stats = ImportStats(card_id=card.card_id, card_name=card.name, action=card.action)
        output_roots = [("primary destination", self.destination_root)]
        output_roots.extend(
            (f"backup destination {replica['name']}", Path(replica["root"]).expanduser())
            for replica in self.replica_destinations
        )
        for label, output_root in output_roots:
            if (card.source_type == "reorganization" and label == "primary destination"
                    and card.root.resolve() == output_root.resolve()):
                continue
            if self._paths_overlap(card.root, output_root):
                message = (
                    f"{card.name}: the {label} overlaps the source root "
                    f"({output_root}). Choose a separate destination."
                )
                stats.failed = 1
                stats.errors.append(message)
                self._emit("error", message, card_id=card.card_id)
                return stats
        history = PortableTransferHistory(card, self.config) if card.portable_history else None
        session = history.start_session() if history is not None else None
        local_sessions: list[tuple[PortableTransferSession, bool, str]] = []
        primary_local_history: PortableTransferHistory | None = None
        local_history_config = self.config["local_history"]
        anchor_started = session.started_at if session is not None else datetime.now().astimezone()
        anchor_session_id = session.session_id if session is not None else uuid.uuid4().hex
        history_targets: dict[str, int] = {}

        def add_local_session(
            local_history: PortableTransferHistory,
            required: bool,
            label: str,
        ) -> None:
            key = os.path.normcase(os.path.abspath(str(local_history.history_dir)))
            existing_index = history_targets.get(key)
            if existing_index is not None:
                existing_session, existing_required, existing_label = local_sessions[existing_index]
                local_sessions[existing_index] = (
                    existing_session,
                    existing_required or required,
                    f"{existing_label} / {label}",
                )
                return
            history_targets[key] = len(local_sessions)
            local_sessions.append(
                (
                    local_history.start_session(anchor_started, anchor_session_id),
                    required,
                    label,
                )
            )

        if local_history_config.get("enabled", True):
            configured_local_root = str(local_history_config.get("directory", "")).strip()
            local_root = (
                Path(configured_local_root).expanduser()
                if configured_local_root
                else self.manifest.state_dir / "transfer-records"
            )
            local_history = PortableTransferHistory(
                card,
                self.config,
                history_dir=local_root / safe_segment(card.card_id),
            )
            primary_local_history = local_history
            add_local_session(
                local_history,
                bool(local_history_config.get("require_before_source_delete", True)),
                "primary local",
            )
        for replica in self.replica_destinations:
            if not replica.get("include_history", True):
                continue
            replica_history = PortableTransferHistory(
                card,
                self.config,
                history_dir=(
                    Path(replica["root"]).expanduser()
                    / ".photocard-organizer"
                    / "transfer-records"
                    / safe_segment(card.card_id)
                ),
            )
            add_local_session(
                replica_history,
                bool(replica.get("required", True)),
                f"replica {replica['name']}",
            )
        safety = self.config["safety"]
        confirmation_required = card.source_type == "reorganization" or bool(safety.get("confirm_destructive_actions", True))
        destructive_allowed = self.dry_run or allow_destructive or not confirmation_required

        try:
            source_capacity = capacity_for(card.root)
            warning_threshold = float(safety.get("warn_source_free_percent", 0))
            if source_capacity.free_percent < warning_threshold:
                stats.warnings.append(
                    f"{card.name} has only {source_capacity.free_percent:.1f}% free space remaining."
                )
        except OSError as exc:
            stats.warnings.append(f"Could not read source capacity for {card.name}: {exc}")

        source_files = list(self._source_files(card))
        if card.source_type != "reorganization":
            source_files.sort(
            key=lambda item: (
                item[0].relative_to(card.root).as_posix().casefold(),
                item[1],
            ),
        )
        bracket_context = source_files
        total_files = len(source_files)
        # Skip completed files before bracket analysis and per-file database lookups.
        completed_keys = self.manifest.completed_keys(card.card_id)
        if card.source_type != "reorganization" and completed_keys:
            remaining = []
            for source, kind in source_files:
                try:
                    stat = source.stat()
                    key = self.source_key(card, source.relative_to(card.root), stat.st_size, stat.st_mtime_ns)
                except OSError:
                    remaining.append((source, kind))
                    continue
                if key in completed_keys:
                    stats.discovered += 1
                    stats.skipped += 1
                else:
                    remaining.append((source, kind))
            source_files = remaining
            total_files = len(source_files)
        metadata_by_path: dict[Path, MediaMetadata] = {}
        capture_groups: dict[tuple[str, str], str] = {}
        bracket_settings = self.config.get("organization", {}).get(
            "long_exposure_brackets",
            {},
        )
        bracket_folder_used = any(
            rule.get("enabled", True)
            and any(
                "{capture_group}" in str(segment)
                for segment in rule.get("folder_segments", [])
            )
            for rule in self.config.get("media_rules", {}).values()
        )
        bracket_sources = [
            (source, media_kind)
            for source, media_kind in (bracket_context if source_files else [])
            if media_kind in {"photo", "raw"}
        ]
        if (
            bracket_settings.get("enabled", False)
            and card.source_type != "reorganization"
            and bracket_folder_used
            and bracket_sources
        ):
            analysis_total = len(bracket_sources)
            self._emit(
                "progress",
                f"Analyzing long-exposure brackets on {card.name}",
                card_id=card.card_id,
                current=0,
                total=analysis_total,
                stage="Analyzing",
            )
            for metadata_index, (source, media_kind) in enumerate(
                bracket_sources,
                start=1,
            ):
                try:
                    metadata_by_path[source] = extract_metadata(
                        source,
                        media_kind,
                    )
                except OSError:
                    continue
                if (
                    metadata_index == analysis_total
                    or metadata_index == 1
                    or metadata_index % 25 == 0
                ):
                    self._emit(
                        "progress",
                        f"Analyzing {card.name}: {metadata_index} of {analysis_total}",
                        card_id=card.card_id,
                        current=metadata_index,
                        total=analysis_total,
                        stage="Analyzing",
                    )
            group_count = assign_long_exposure_groups(
                bracket_context,
                metadata_by_path,
                bracket_settings,
            )
            capture_groups = {
                capture_key(path): metadata.capture_group
                for path, metadata in metadata_by_path.items()
                if metadata.capture_group
            }
            if group_count:
                self._emit(
                    "info",
                    f"{card.name}: detected {group_count} long-exposure "
                    f"{'bracket' if group_count == 1 else 'brackets'}.",
                    card_id=card.card_id,
                )
        progress_interval = max(1, total_files // 200)
        processed_files = 0
        self._emit(
            "progress",
            f"Scanning {card.name}: 0 of {total_files}",
            card_id=card.card_id,
            current=0,
            total=total_files,
        )
        for index, (source, media_kind) in enumerate(source_files, start=1):
            if index == 1 or (index - 1) % progress_interval == 0:
                self._emit(
                    "progress",
                    f"Scanning {card.name}: {index - 1} of {total_files}",
                    card_id=card.card_id,
                    current=index - 1,
                    total=total_files,
                )
            stats.discovered += 1
            try:
                source_stat = source.stat()
            except OSError as exc:
                stats.failed += 1
                message = f"Cannot inspect {source}: {exc}"
                stats.errors.append(message)
                self._emit("error", message, source=str(source))
                continue

            settle_seconds = float(self.config["monitor"].get("settle_seconds", 0))
            source_age = time.time() - source_stat.st_mtime
            if 0 <= source_age < settle_seconds:
                stats.skipped += 1
                continue
            relative_path = source.relative_to(card.root)
            source_key = self.source_key(card, relative_path, source_stat.st_size, source_stat.st_mtime_ns)
            pending = self.manifest.pending(source_key)
            completed_locally = bool(
                history is None
                and pending is None
                and primary_local_history is not None
                and primary_local_history.contains(source_key)
            )
            if card.source_type != "reorganization" and (
                (history is not None and history.contains(source_key))
                or self.manifest.contains(source_key)
                or completed_locally
            ):
                stats.skipped += 1
                continue

            if card.action == "move" and not destructive_allowed:
                stats.blocked += 1
                stats.pending_destructive_confirmation = True
                continue

            automatic_retries = int(safety.get("io_retry_count", 2))
            attempt = 0
            stop_card = False
            while True:
                try:
                    self._process_source(
                        card=card,
                        source=source,
                        media_kind=media_kind,
                        source_stat=source_stat,
                        relative_path=relative_path,
                        source_key=source_key,
                        session=session,
                        local_sessions=local_sessions,
                        local_history_config=local_history_config,
                        stats=stats,
                        allow_destructive=allow_destructive,
                        metadata=metadata_by_path.get(source),
                        capture_group=capture_groups.get(
                            capture_key(source),
                            "",
                        ),
                    )
                    break
                except DestinationChangeRequested as change:
                    stats.requested_destination_root = change.path
                    stats.persist_destination_change = change.persist
                    stats.stopped_early = True
                    stop_card = True
                    break
                except StopCardRequested:
                    stats.stopped_early = True
                    stop_card = True
                    break
                except SourceChangedError as exc:
                    try:
                        if not self.dry_run:
                            self.manifest.clear_pending(source_key)
                    except (OSError, sqlite3.Error) as pending_error:
                        stats.warnings.append(
                            f"Could not clear the deferred marker for {source.name}: {pending_error}"
                        )
                    stats.blocked += 1
                    warning = f"Deferred {source.name}: {exc}"
                    stats.warnings.append(warning)
                    self._emit("warning", warning, source=str(source))
                    break
                except Exception as exc:  # isolate each file from the rest of the card
                    if attempt < automatic_retries:
                        attempt += 1
                        self._emit(
                            "warning",
                            f"Retry {attempt}/{automatic_retries} for {source.name}: {exc}",
                            source=str(source),
                        )
                        time.sleep(float(safety.get("io_retry_delay_seconds", 1.0)))
                        continue
                    decision = self._file_error_decision(source, exc)
                    if decision.action == "retry":
                        attempt = 0
                        continue
                    if decision.action == "choose_destination" and decision.value:
                        stats.requested_destination_root = decision.value
                        stats.persist_destination_change = decision.persist
                        stats.stopped_early = True
                        stop_card = True
                        break
                    stats.failed += 1
                    message = f"Failed to process {source}: {exc}"
                    stats.errors.append(message)
                    self._emit("error", message, source=str(source))
                    if decision.action == "stop":
                        stats.stopped_early = True
                        stop_card = True
                    break
            if stop_card:
                processed_files = index
                break
        else:
            processed_files = total_files

        self._emit(
            "progress",
            f"{card.name}: {processed_files} of {total_files} files checked",
            card_id=card.card_id,
            current=processed_files,
            total=total_files,
        )

        if history is not None:
            stats.warnings.extend(history.read_warnings)
        if stats.pending_destructive_confirmation:
            self._emit(
                "warning",
                f"{card.name} is configured to move files and is waiting for confirmation.",
                card_id=card.card_id,
            )
        if card.action == "move" and destructive_allowed and card.delete_empty_folders_after_move:
            if not self.dry_run:
                self._remove_empty_source_folders(card)
        return stats

    @staticmethod
    def _remove_empty_source_folders(card: CardMarker) -> None:
        for source_folder in card.source_folders:
            source_root = card.root / source_folder
            if Organizer._is_link_like(source_root) or not source_root.is_dir():
                continue
            directories = sorted(
                (
                    path
                    for path in source_root.rglob("*")
                    if path.is_dir() and not Organizer._is_link_like(path)
                ),
                key=lambda path: len(path.parts),
                reverse=True,
            )
            for directory in directories:
                try:
                    directory.rmdir()
                except OSError:
                    pass

    def scan_all(self, *, allow_destructive: bool = False) -> tuple[list[ImportStats], list[str]]:
        cards, discovery_errors = discover_cards(self.config)
        for error in discovery_errors:
            self._emit("error", error)
        return self.scan_cards(cards, allow_destructive=allow_destructive), discovery_errors

    def scan_cards(self, cards: list[CardMarker], *, allow_destructive: bool = False) -> list[ImportStats]:
        results = []
        for card in cards:
            aggregate = ImportStats(card_id=card.card_id, card_name=card.name, action=card.action)
            current = self
            visited_destinations = {str(self.destination_root.resolve())}
            for _redirect in range(MAX_DESTINATION_REDIRECTS):
                result = current.scan_card(card, allow_destructive=allow_destructive)
                aggregate.merge(result)
                requested = result.requested_destination_root
                if not requested:
                    aggregate.requested_destination_root = ""
                    aggregate.persist_destination_change = False
                    break
                requested_path = Path(requested).expanduser()
                try:
                    key = str(requested_path.resolve())
                except OSError:
                    key = str(requested_path)
                if key in visited_destinations:
                    aggregate.errors.append(f"Destination fallback loop detected at {requested_path}")
                    aggregate.failed += 1
                    break
                visited_destinations.add(key)
                redirected_config = copy.deepcopy(current.config)
                redirected_config["destination_root"] = str(requested_path)
                current = Organizer(
                    redirected_config,
                    dry_run=self.dry_run,
                    event_callback=self.event_callback,
                    decision_callback=self.decision_callback,
                )
            else:
                aggregate.errors.append(
                    "Stopped after "
                    f"{MAX_DESTINATION_REDIRECTS} destination changes without reaching "
                    "an available destination."
                )
                aggregate.failed += 1
            results.append(aggregate)
        return results
