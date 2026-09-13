from __future__ import annotations

import sqlite3
import json
import threading
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .library_state import state_directory


class ImportManifest:
    def __init__(self, destination_root: Path | str, create: bool = True):
        self.state_dir = state_directory(destination_root)
        self.path = self.state_dir / "manifest.sqlite3"
        self._session = threading.local()
        if create:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self):
        shared = getattr(self._session, "connection", None)
        connection = shared or self._connect()
        try:
            with connection:
                yield connection
        finally:
            if shared is None:
                connection.close()

    @contextmanager
    def processing_session(self):
        """Reuse one thread-local connection, committing each write independently."""
        if getattr(self._session, "connection", None) is not None or not self.path.exists():
            yield
            return
        connection = self._connect()
        self._session.connection = connection
        try:
            yield
        finally:
            self._session.connection = None
            connection.close()

    def completed_keys(self, card_id: str) -> set[str]:
        if not self.path.exists():
            return set()
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT source_key FROM imports WHERE card_id = ? AND source_key NOT IN (SELECT source_key FROM pending_imports)", (card_id,)
            ).fetchall()
        return {str(row[0]) for row in rows}

    def relocate_destinations(self, source: Path, destination: Path) -> None:
        with self._connection() as connection:
            for table, column in (
                ("imports", "destination_path"),
                ("digest_items", "destination_path"),
                ("conflicts", "existing_path"),
                ("conflicts", "incoming_path"),
            ):
                connection.execute(
                    f"UPDATE {table} SET {column} = ? WHERE {column} = ?",
                    (str(destination), str(source)),
                )

    def rename_intents(self) -> list[dict]:
        with self._connection() as connection:
            return [json.loads(row[0]) for row in connection.execute("SELECT payload FROM rename_intents")]

    @staticmethod
    def metadata_signature(source):
        signature = []
        for path in dict.fromkeys((source, source.with_suffix(".xmp"), source.with_suffix(".XMP"))):
            try:
                value = path.stat()
                signature.append([value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_ino, value.st_dev])
            except FileNotFoundError:
                signature.append(None)
        return signature

    def cached_metadata(self, source, media_kind):
        if not self.path.exists():
            return None
        try:
            with self._connection() as connection:
                row = connection.execute("SELECT signature,payload FROM metadata_cache WHERE path=?", (str(source),)).fetchone()
            if row is None or json.loads(row[0]) != self.metadata_signature(source):
                return None
            from .models import MediaMetadata
            payload = json.loads(row[1])
            if payload["media_kind"] != media_kind:
                return None
            payload["captured_at"] = datetime.fromisoformat(payload["captured_at"])
            payload["capture_group"] = ""
            payload["location_name"] = ""
            return MediaMetadata(**payload)
        except (sqlite3.Error, ValueError, TypeError, KeyError):
            return None

    def cache_metadata(self, source, metadata):
        payload = asdict(metadata)
        payload["captured_at"] = metadata.captured_at.isoformat()
        # These values depend on current grouping/geocoding settings, not the file alone.
        payload["capture_group"] = ""
        payload["location_name"] = ""
        with self._connection() as connection:
            connection.execute("INSERT OR REPLACE INTO metadata_cache VALUES (?, ?, ?)",
                (str(source), json.dumps(self.metadata_signature(source)), json.dumps(payload)))

    def record_rename_intent(self, payload: dict) -> None:
        with self._connection() as connection:
            connection.execute("INSERT OR REPLACE INTO rename_intents VALUES (?, ?)", (payload["source"], json.dumps(payload)))

    def clear_rename_intent(self, source: Path) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM rename_intents WHERE source_path=?", (str(source),))

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS metadata_cache (
                    path TEXT PRIMARY KEY,
                    signature TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rename_intents (
                    source_path TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS imports (
                    source_key TEXT PRIMARY KEY,
                    card_id TEXT NOT NULL,
                    source_relative_path TEXT NOT NULL,
                    source_size INTEGER NOT NULL,
                    source_mtime_ns INTEGER NOT NULL,
                    destination_path TEXT NOT NULL,
                    action TEXT NOT NULL,
                    verification TEXT NOT NULL,
                    imported_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS imports_card_id_idx ON imports(card_id);
                CREATE INDEX IF NOT EXISTS imports_destination_idx ON imports(destination_path);
                CREATE TABLE IF NOT EXISTS pending_imports (
                    source_key TEXT PRIMARY KEY,
                    card_id TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    destination_path TEXT NOT NULL,
                    source_size INTEGER NOT NULL,
                    source_mtime_ns INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS location_cache (
                    coordinate_key TEXT PRIMARY KEY,
                    place_name TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conflicts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_key TEXT NOT NULL,
                    card_id TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    existing_path TEXT NOT NULL,
                    incoming_path TEXT NOT NULL,
                    conflict_type TEXT NOT NULL,
                    resolution TEXT NOT NULL,
                    content_checksum TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS conflicts_status_idx ON conflicts(status, created_at);
                CREATE INDEX IF NOT EXISTS conflicts_existing_idx ON conflicts(existing_path);
                CREATE INDEX IF NOT EXISTS conflicts_incoming_idx ON conflicts(incoming_path);
                CREATE INDEX IF NOT EXISTS conflicts_source_idx ON conflicts(source_key, incoming_path);
                CREATE TABLE IF NOT EXISTS hub_receipts (
                    hub_id TEXT NOT NULL,
                    producer_channel TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    receipt_path TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY (hub_id, producer_channel, source_key)
                );
                CREATE TABLE IF NOT EXISTS digest_items (
                    profile_id TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    media_kind TEXT NOT NULL,
                    source_size INTEGER NOT NULL,
                    source_mtime_ns INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    destination_path TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    first_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (profile_id, relative_path)
                );
                CREATE INDEX IF NOT EXISTS digest_items_status_idx
                    ON digest_items(profile_id, status, updated_at);
                CREATE INDEX IF NOT EXISTS digest_items_destination_idx ON digest_items(destination_path);
                CREATE TABLE IF NOT EXISTS digest_runs (
                    run_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    profile_name TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    discovered INTEGER NOT NULL DEFAULT 0,
                    imported INTEGER NOT NULL DEFAULT 0,
                    skipped INTEGER NOT NULL DEFAULT 0,
                    blocked INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0,
                    conflicts INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS digest_runs_profile_idx
                    ON digest_runs(profile_id, started_at);
                COMMIT;
                """
            )

    def contains(self, source_key: str) -> bool:
        if not self.path.exists():
            return False
        with self._connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM imports WHERE source_key = ? LIMIT 1", (source_key,)
            ).fetchone()
        return row is not None

    def record(
        self,
        source_key: str,
        card_id: str,
        source_relative_path: str,
        source_size: int,
        source_mtime_ns: int,
        destination_path: Path,
        action: str,
        verification: str,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO imports (
                    source_key, card_id, source_relative_path, source_size,
                    source_mtime_ns, destination_path, action, verification, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_key,
                    card_id,
                    source_relative_path,
                    source_size,
                    source_mtime_ns,
                    str(destination_path),
                    action,
                    verification,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def record_pending(
        self,
        *,
        source_key: str,
        card_id: str,
        source_path: Path,
        destination_path: Path,
        source_size: int,
        source_mtime_ns: int,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO pending_imports (
                    source_key, card_id, source_path, destination_path,
                    source_size, source_mtime_ns, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_key) DO UPDATE SET
                    card_id = excluded.card_id,
                    source_path = excluded.source_path,
                    destination_path = excluded.destination_path,
                    source_size = excluded.source_size,
                    source_mtime_ns = excluded.source_mtime_ns,
                    updated_at = excluded.updated_at
                """,
                (
                    source_key,
                    card_id,
                    str(source_path),
                    str(destination_path),
                    source_size,
                    source_mtime_ns,
                    now,
                    now,
                ),
            )

    def pending(self, source_key: str) -> dict[str, object] | None:
        if not self.path.exists():
            return None
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM pending_imports WHERE source_key = ? LIMIT 1",
                (source_key,),
            ).fetchone()
        return dict(row) if row else None

    def clear_pending(self, source_key: str) -> None:
        if not self.path.exists():
            return
        with self._connection() as connection:
            connection.execute("DELETE FROM pending_imports WHERE source_key = ?", (source_key,))

    def get_location(self, coordinate_key: str) -> str | None:
        if not self.path.exists():
            return None
        with self._connection() as connection:
            row = connection.execute(
                "SELECT place_name FROM location_cache WHERE coordinate_key = ?", (coordinate_key,)
            ).fetchone()
        return str(row["place_name"]) if row else None

    def put_location(self, coordinate_key: str, place_name: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO location_cache (coordinate_key, place_name, updated_at)
                VALUES (?, ?, ?)
                """,
                (coordinate_key, place_name, datetime.now(timezone.utc).isoformat()),
            )

    def recent(self, limit: int = 100) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT card_id, source_relative_path, destination_path, action, imported_at
                FROM imports ORDER BY imported_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def source_status(self, card_id: str) -> dict[str, object]:
        if not self.path.exists():
            return {"imported_files": 0, "last_imported_at": ""}
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS imported_files, MAX(imported_at) AS last_imported_at
                FROM imports WHERE card_id = ?
                """,
                (card_id,),
            ).fetchone()
        return {
            "imported_files": int(row["imported_files"] or 0),
            "last_imported_at": str(row["last_imported_at"] or ""),
        }

    def import_record(self, source_key: str) -> dict[str, object] | None:
        if not self.path.exists():
            return None
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM imports WHERE source_key = ? LIMIT 1",
                (source_key,),
            ).fetchone()
        return dict(row) if row else None

    def has_hub_receipt(
        self, hub_id: str, producer_channel: str, source_key: str
    ) -> bool:
        if not self.path.exists():
            return False
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM hub_receipts
                WHERE hub_id = ? AND producer_channel = ? AND source_key = ?
                LIMIT 1
                """,
                (hub_id, producer_channel, source_key),
            ).fetchone()
        return row is not None

    def record_hub_receipt(
        self,
        hub_id: str,
        producer_channel: str,
        source_key: str,
        receipt_path: Path,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO hub_receipts (
                    hub_id, producer_channel, source_key, receipt_path, recorded_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    hub_id,
                    producer_channel,
                    source_key,
                    str(receipt_path),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def record_conflict(
        self,
        *,
        source_key: str,
        card_id: str,
        source_path: Path,
        existing_path: Path,
        incoming_path: Path,
        conflict_type: str,
        resolution: str,
        content_checksum: str = "",
    ) -> None:
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT id FROM conflicts
                WHERE source_key = ? AND incoming_path = ? LIMIT 1
                """,
                (source_key, str(incoming_path)),
            ).fetchone()
            if existing:
                return
            connection.execute(
                """
                INSERT INTO conflicts (
                    source_key, card_id, source_path, existing_path, incoming_path,
                    conflict_type, resolution, content_checksum, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
                """,
                (
                    source_key,
                    card_id,
                    str(source_path),
                    str(existing_path),
                    str(incoming_path),
                    conflict_type,
                    resolution,
                    content_checksum,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    @staticmethod
    def _conflict_filters(
        status: str,
        search: str,
    ) -> tuple[list[str], list[object]]:
        clauses = []
        parameters: list[object] = []
        if status != "all":
            clauses.append("status = ?")
            parameters.append(status)
        search = search.strip()
        if search:
            clauses.append(
                """
                (
                    source_path LIKE ? COLLATE NOCASE
                    OR existing_path LIKE ? COLLATE NOCASE
                    OR incoming_path LIKE ? COLLATE NOCASE
                    OR conflict_type LIKE ? COLLATE NOCASE
                    OR resolution LIKE ? COLLATE NOCASE
                )
                """
            )
            pattern = f"%{search}%"
            parameters.extend([pattern] * 5)
        return clauses, parameters

    def conflicts(
        self,
        status: str = "open",
        limit: int = 500,
        *,
        offset: int = 0,
        search: str = "",
    ) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        clauses, parameters = self._conflict_filters(status, search)
        query = "SELECT * FROM conflicts"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        parameters.extend([max(1, int(limit)), max(0, int(offset))])
        query += " OFFSET ?"
        with self._connection() as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()
        return [dict(row) for row in rows]

    def conflict_count(
        self,
        status: str = "open",
        *,
        search: str = "",
    ) -> int:
        if not self.path.exists():
            return 0
        clauses, parameters = self._conflict_filters(status, search)
        query = "SELECT COUNT(*) AS count FROM conflicts"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        with self._connection() as connection:
            row = connection.execute(query, tuple(parameters)).fetchone()
        return int(row["count"] or 0)

    def mark_conflict_reviewed(self, conflict_id: int) -> None:
        self.mark_conflicts_reviewed([conflict_id])

    def mark_conflicts_reviewed(self, conflict_ids: list[int]) -> None:
        if not self.path.exists():
            return
        identifiers = sorted({int(conflict_id) for conflict_id in conflict_ids})
        if not identifiers:
            return
        placeholders = ", ".join("?" for _identifier in identifiers)
        with self._connection() as connection:
            connection.execute(
                f"""
                UPDATE conflicts
                SET status = 'reviewed', reviewed_at = ?
                WHERE id IN ({placeholders})
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    *identifiers,
                ),
            )

    def conflict_for_source(
        self,
        source_key: str,
        *,
        status: str = "all",
    ) -> dict[str, object] | None:
        if not self.path.exists():
            return None
        query = "SELECT * FROM conflicts WHERE source_key = ?"
        parameters: list[object] = [source_key]
        if status != "all":
            query += " AND status = ?"
            parameters.append(status)
        query += " ORDER BY created_at DESC LIMIT 1"
        with self._connection() as connection:
            row = connection.execute(query, tuple(parameters)).fetchone()
        return dict(row) if row else None

    def record_digest_item(
        self,
        *,
        profile_id: str,
        source_key: str,
        source_path: Path,
        relative_path: Path,
        media_kind: str,
        source_size: int,
        source_mtime_ns: int,
        status: str,
        destination_path: Path | str = "",
        error: str = "",
    ) -> None:
        if status not in {"pending", "processed", "failed", "conflict"}:
            raise ValueError(f"Unsupported digest item status: {status}")
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO digest_items (
                    profile_id, source_key, source_path, relative_path,
                    media_kind, source_size, source_mtime_ns, status,
                    destination_path, error, first_seen_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id, relative_path) DO UPDATE SET
                    source_key = excluded.source_key,
                    source_path = excluded.source_path,
                    media_kind = excluded.media_kind,
                    source_size = excluded.source_size,
                    source_mtime_ns = excluded.source_mtime_ns,
                    status = excluded.status,
                    destination_path = excluded.destination_path,
                    error = excluded.error,
                    updated_at = excluded.updated_at
                """,
                (
                    profile_id,
                    source_key,
                    str(source_path),
                    relative_path.as_posix(),
                    media_kind,
                    source_size,
                    source_mtime_ns,
                    status,
                    str(destination_path),
                    error,
                    now,
                    now,
                ),
            )

    def digest_items(
        self,
        profile_id: str = "",
        *,
        status: str = "all",
        limit: int = 1000,
    ) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        clauses = []
        parameters: list[object] = []
        if profile_id:
            clauses.append("profile_id = ?")
            parameters.append(profile_id)
        if status != "all":
            clauses.append("status = ?")
            parameters.append(status)
        query = "SELECT * FROM digest_items"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC LIMIT ?"
        parameters.append(max(1, int(limit)))
        with self._connection() as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()
        return [dict(row) for row in rows]

    def digest_summary(self, profile_id: str) -> dict[str, object]:
        empty = {
            "pending": 0,
            "processed": 0,
            "failed": 0,
            "conflict": 0,
            "total": 0,
            "last_run_at": "",
            "last_run_status": "",
        }
        if not self.path.exists():
            return empty
        with self._connection() as connection:
            # A new database file is visible before its schema transaction commits.
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('digest_items', 'digest_runs')"
            )}
            if tables != {"digest_items", "digest_runs"}:
                return empty
            counts = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending,
                    SUM(CASE WHEN status = 'processed' THEN 1 ELSE 0 END) AS processed,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN status = 'conflict' THEN 1 ELSE 0 END) AS conflict
                FROM digest_items WHERE profile_id = ?
                """,
                (profile_id,),
            ).fetchone()
            latest = connection.execute(
                """
                SELECT completed_at, status FROM digest_runs
                WHERE profile_id = ? AND completed_at <> ''
                ORDER BY completed_at DESC LIMIT 1
                """,
                (profile_id,),
            ).fetchone()
        result = dict(empty)
        for key in ("pending", "processed", "failed", "conflict", "total"):
            result[key] = int(counts[key] or 0)
        if latest:
            result["last_run_at"] = str(latest["completed_at"] or "")
            result["last_run_status"] = str(latest["status"] or "")
        return result

    def start_digest_run(
        self,
        run_id: str,
        profile_id: str,
        profile_name: str,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO digest_runs (
                    run_id, profile_id, profile_name, started_at, status
                ) VALUES (?, ?, ?, ?, 'running')
                """,
                (
                    run_id,
                    profile_id,
                    profile_name,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def finish_digest_run(
        self,
        run_id: str,
        *,
        status: str,
        discovered: int,
        imported: int,
        skipped: int,
        blocked: int,
        failed: int,
        conflicts: int,
        error: str = "",
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE digest_runs SET
                    completed_at = ?,
                    status = ?,
                    discovered = ?,
                    imported = ?,
                    skipped = ?,
                    blocked = ?,
                    failed = ?,
                    conflicts = ?,
                    error = ?
                WHERE run_id = ?
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    status,
                    discovered,
                    imported,
                    skipped,
                    blocked,
                    failed,
                    conflicts,
                    error,
                    run_id,
                ),
            )
