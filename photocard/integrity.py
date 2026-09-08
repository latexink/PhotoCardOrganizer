from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from .io_schedule import serialized_io


@serialized_io
def checksum(path: Path, algorithm: str = "sha256", *, cancel_event=None) -> str:
    if algorithm not in {"sha256", "sha512", "blake2b"}:
        raise ValueError("Unsupported checksum algorithm")
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Integrity check cancelled")
            digest.update(chunk)
    return digest.hexdigest()


def local_state_directory() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home())) / "PhotoCardOrganizer"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "PhotoCardOrganizer"


def checked_path(path: Path) -> Path:
    path = Path(os.path.abspath(path.expanduser()))
    for parent in (path, *path.parents):
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise ValueError(f"Linked path is not supported: {path}")
    return path


class IntegrityCatalog:
    """Portable baselines, with a separately stored local audit trail."""

    def __init__(self, root: Path, local_root: Path | None = None):
        self.root = checked_path(Path(root))
        self.directory = self.root / ".photocard-organizer" / "integrity"
        self.path = self.directory / "catalog.sqlite3"
        self.legacy_path = self.root / ".photocard-organizer" / "integrity.sqlite3"
        key = hashlib.sha256(str(self.root).encode()).hexdigest()
        self.local_path = (Path(local_root) if local_root else local_state_directory()) / "integrity" / f"{key}.jsonl"
        self.last_report = None
        self.cancelled = False

    def _migrate(self):
        checked_path(self.path)
        if self.path.exists() or not self.legacy_path.exists():
            return
        checked_path(self.legacy_path)
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.directory / f"migration-{uuid.uuid4().hex}.partial"
        try:
            with closing(sqlite3.connect(f"{self.legacy_path.as_uri()}?mode=ro", uri=True)) as source:
                if source.execute("PRAGMA user_version").fetchone()[0] > 1:
                    raise ValueError("Integrity catalog requires a newer application")
                with closing(sqlite3.connect(temporary)) as target:
                    source.backup(target)
                    if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ValueError("Legacy integrity catalog failed validation")
                    target.execute("SELECT path, algorithm, digest, size, status, checked_at FROM baselines LIMIT 1")
                    target.execute("PRAGMA user_version=1")
                    target.commit()
            with temporary.open("rb+") as handle:
                os.fsync(handle.fileno())
            from .atomic_copy import commit_without_overwrite
            commit_without_overwrite(temporary, self.path)
            # The legacy file is retained as a rollback copy; future reads prefer the new catalog.
        finally:
            temporary.unlink(missing_ok=True)

    def _connect(self):
        self._migrate()
        checked_path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        if connection.execute("PRAGMA user_version").fetchone()[0] > 1:
            connection.close()
            raise ValueError("Integrity catalog requires a newer application")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("CREATE TABLE IF NOT EXISTS baselines (path TEXT PRIMARY KEY, algorithm TEXT NOT NULL, digest TEXT NOT NULL, size INTEGER NOT NULL, status TEXT NOT NULL, checked_at TEXT NOT NULL)")
        connection.execute("PRAGMA user_version=1")
        connection.commit()
        return connection

    def records(self) -> dict[str, dict]:
        path = self.path if self.path.exists() else self.legacy_path
        checked_path(path)
        if not path.exists():
            return {}
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
            if connection.execute("PRAGMA user_version").fetchone()[0] > 1:
                raise ValueError("Integrity catalog requires a newer application")
            connection.row_factory = sqlite3.Row
            return {row["path"]: dict(row) for row in connection.execute("SELECT * FROM baselines")}

    def record(self, path: Path, digest: str, algorithm: str, *, verified: bool) -> None:
        relative = checked_path(path).relative_to(self.root).as_posix()
        stamp = datetime.now(timezone.utc).isoformat()
        status = "verified" if verified else "verification pending"
        size = path.stat().st_size
        with closing(self._connect()) as connection, connection:
            existing = connection.execute("SELECT algorithm, digest FROM baselines WHERE path=?", (relative,)).fetchone()
            if existing and existing != (algorithm, digest):
                raise ValueError(f"Recorded checksum differs for {path}. Review the change; baseline retained.")
            event = dict(path=relative, algorithm=algorithm, digest=digest, size=size, status=status, checked_at=stamp)
            self._audit(event)
            connection.execute("INSERT INTO baselines VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(path) DO UPDATE SET status=excluded.status, checked_at=excluded.checked_at", (relative, algorithm, digest, size, status, stamp))

    def _audit(self, event):
        checked_path(self.local_path)
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        with self.local_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def relocate(self, source: Path, destination: Path):
        old = checked_path(source).relative_to(self.root).as_posix()
        new = checked_path(destination).relative_to(self.root).as_posix()
        if old == new or not (self.path.exists() or self.legacy_path.exists()):
            return
        with closing(self._connect()) as connection, connection:
            baseline = connection.execute("SELECT algorithm,digest,size,status,checked_at FROM baselines WHERE path=?", (old,)).fetchone()
            if baseline is None:
                return
            if connection.execute("SELECT 1 FROM baselines WHERE path=?", (new,)).fetchone():
                raise ValueError(f"Destination already has an integrity baseline: {destination}")
            self._audit(dict(action="relocate", source=old, path=new, algorithm=baseline[0], digest=baseline[1],
                             checked_at=datetime.now(timezone.utc).isoformat()))
            connection.execute("UPDATE baselines SET path=? WHERE path=?", (new, old))

    def run_library_check(self, config, *, establish=False, cancel_event=None, progress=None, paths=None):
        from .library_jobs import walk_files
        if not self.root.is_dir():
            raise ValueError("Library is unavailable")
        if paths is None:
            selected = set()
            for path in walk_files(self.root, config, include_conflicts=True):
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError("Integrity inventory cancelled")
                selected.add(path)
            # Include missing baseline entries, even if their extension was subsequently disabled.
            for name in self.records():
                selected.add(self.root / name)
            paths = sorted(selected)
        return self.check(paths, establish=establish, cancel_event=cancel_event, progress=progress)

    def check(self, paths=None, *, establish=False, cancel_event=None, progress=None):
        records = self.records()
        selected = list(paths) if paths is not None else [self.root / name for name in records]
        results = []
        self.cancelled = False
        self._migrate()
        for index, path in enumerate(selected):
            if cancel_event and cancel_event.is_set():
                self.cancelled = True
                break
            try:
                path = checked_path(Path(path))
                relative = path.relative_to(self.root).as_posix()
                baseline = records.get(relative)
                if not path.is_file():
                    status = "Missing"
                elif baseline is None and not establish:
                    status = "No baseline"
                elif baseline is not None and establish:
                    status = "Baseline already exists"
                else:
                    before = path.stat()
                    algorithm = baseline["algorithm"] if baseline else "sha256"
                    digest = checksum(path, algorithm, cancel_event=cancel_event)
                    after = path.stat()
                    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino):
                        status = "Changed during check"
                    elif baseline and digest != baseline["digest"]:
                        status = "Changed; baseline retained"
                    else:
                        # Existing baselines remain immutable; the session report records this check.
                        if baseline is None:
                            self.record(path, digest, algorithm, verified=True)
                        status = "Verified" if baseline else "Baseline established (current contents)"
            except InterruptedError:
                self.cancelled = True
                break
            except (OSError, ValueError, sqlite3.Error) as exc:
                status = f"Error: {exc}"
            results.append((str(path), status))
            if progress:
                progress(index + 1, len(selected), str(path))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        name = f"{stamp}-{uuid.uuid4().hex}.json"
        report = {"schema": 1, "library": str(self.root), "operation": "create missing baselines" if establish else "verify",
                  "cancelled": self.cancelled, "files_planned": len(selected), "results": results}
        for directory in (self.directory / "reports", self.local_path.parent / "reports"):
            path = checked_path(directory / name)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as handle:
                json.dump(report, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
        self.last_report = self.directory / "reports" / name
        return results
