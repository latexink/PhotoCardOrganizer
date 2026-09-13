"""Conservative recovery using an immutable library checksum baseline."""
from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

from .atomic_copy import relocate_without_overwrite
from .integrity import IntegrityCatalog, checked_path, checksum
from .io_schedule import serialized_io


def _snapshot(path):
    if not path.exists():
        return None
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


@serialized_io
def restore_file(catalog: IntegrityCatalog, path: Path, backup_roots, *, cancel_event=None):
    path = checked_path(path)
    relative = path.relative_to(catalog.root)
    if relative.parts[0] == ".photocard-organizer":
        raise ValueError("Library metadata cannot be restored as media")
    baseline = catalog.records().get(relative.as_posix())
    if baseline is None:
        raise ValueError("A saved checksum is required before restoring a file")
    original = _snapshot(path)
    if original is not None and not path.is_file():
        raise ValueError("The destination is not a regular file")
    if path.is_file() and checksum(path, baseline["algorithm"], cancel_event=cancel_event) == baseline["digest"]:
        return "Already verified"
    operation = uuid.uuid4().hex
    directory = checked_path(catalog.directory / "recovery" / operation)
    directory.mkdir(parents=True)
    stage = directory / "verified-copy.partial"
    damaged = directory / "original" / relative
    journal = directory / "journal.jsonl"

    def record(state, **extra):
        event = dict(operation=operation, action="restore", state=state,
                     destination=str(path), preserved_original=str(damaged),
                     staged_copy=str(stage), algorithm=baseline["algorithm"],
                     digest=baseline["digest"], **extra)
        catalog._audit(event)
        with journal.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    errors = []
    try:
        for root in backup_roots:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Recovery cancelled")
            try:
                root = checked_path(Path(root))
                if root == catalog.root or not root.is_dir():
                    errors.append(f"{root}: unavailable or primary library")
                    continue
                source = checked_path(root / relative)
                if not source.is_file() or source.stat().st_size != baseline["size"]:
                    errors.append(f"{root}: matching file unavailable")
                    continue
                before = _snapshot(source)
                # Verify the staged bytes, not just the source: a failing read or write
                # must never replace the library's existing file.
                with source.open("rb") as reader, stage.open("wb") as writer:
                    while chunk := reader.read(4 * 1024 * 1024):
                        if cancel_event is not None and cancel_event.is_set():
                            raise InterruptedError("Recovery cancelled")
                        writer.write(chunk)
                    writer.flush()
                    os.fsync(writer.fileno())
                if before != _snapshot(source):
                    raise ValueError("Backup changed during recovery")
                if checksum(stage, baseline["algorithm"], cancel_event=cancel_event) != baseline["digest"]:
                    raise ValueError("Backup does not match the saved checksum")
                shutil.copystat(source, stage)
                break
            except (OSError, ValueError) as exc:
                if isinstance(exc, InterruptedError):
                    raise
                errors.append(f"{root}: {exc}")
                stage.unlink(missing_ok=True)
        else:
            raise ValueError("No verified backup available. " + "; ".join(errors))
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Recovery cancelled")
        checked_path(path)
        if _snapshot(path) != original:
            raise ValueError("Library file changed during recovery; nothing replaced")
        path.parent.mkdir(parents=True, exist_ok=True)
        damaged.parent.mkdir(parents=True, exist_ok=True)
        record("prepared", backup=str(source))
        if original is not None:
            relocate_without_overwrite(path, damaged)
        # Keep a verified staged copy and journal after interruption/failure.
        # Never overwrite a file another process created in the meantime.
        record("original preserved")
        relocate_without_overwrite(stage, path)
        record("complete")
        return "Restored from verified backup"
    except BaseException:
        if not journal.exists():
            stage.unlink(missing_ok=True)
        raise
