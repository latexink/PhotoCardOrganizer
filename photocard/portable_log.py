from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .templates import safe_segment


SESSION_TOKEN = re.compile(r"\{([a-z_]+)(?::([^}]+))?\}")


class PortableTransferHistory:
    """Shared, append-only transfer sessions stored on the source volume."""

    def __init__(self, card, config: dict, history_dir: Path | None = None):
        identification = config["identification"]
        instance = config["instance"]
        self.history_dir = history_dir or card.history_dir
        self.card_id = card.card_id
        self.card_name = card.name
        self.shared_history = bool(identification.get("shared_history", True))
        self.folder_segments = list(identification.get("history_folder_segments", []))
        self.filename_template = identification["session_filename_template"]
        self.checksum_filename_template = identification["checksum_filename_template"]
        self.instance_id = instance["id"]
        self.instance_name = instance["name"]
        self.library_id = instance["library_id"]
        self._keys: set[str] | None = None
        self.read_warnings: list[str] = []

    def _load(self) -> None:
        keys: set[str] = set()
        self.read_warnings.clear()
        if self.history_dir.is_dir():
            try:
                files = sorted(path for path in self.history_dir.rglob("*") if path.is_file())
            except OSError as exc:
                self.read_warnings.append(f"Could not list portable transfer history: {exc}")
                files = []
            for path in files:
                try:
                    with path.open("r", encoding="utf-8", errors="replace") as handle:
                        numbered_lines = iter(enumerate(handle, start=1))
                        header = None
                        for _line_number, line in numbered_lines:
                            if line.strip():
                                try:
                                    header = json.loads(line)
                                except json.JSONDecodeError:
                                    header = None
                                break
                        if not isinstance(header, dict) or header.get("record_type") != "session":
                            continue
                        for line_number, line in numbered_lines:
                            if not line.strip():
                                continue
                            try:
                                record = json.loads(line)
                            except json.JSONDecodeError:
                                self.read_warnings.append(
                                    f"Ignored malformed history line {line_number} in {path.name}"
                                )
                                continue
                            if not isinstance(record, dict) or record.get("status") != "verified":
                                continue
                            if not self.shared_history and record.get("library_id") != self.library_id:
                                continue
                            source_key = str(record.get("source_key", ""))
                            if source_key:
                                keys.add(source_key)
                except OSError as exc:
                    self.read_warnings.append(f"Could not read {path}: {exc}")
        self._keys = keys

    def contains(self, source_key: str) -> bool:
        if self._keys is None:
            self._load()
        return source_key in (self._keys or set())

    def start_session(
        self,
        started_at: datetime | None = None,
        session_id: str = "",
    ) -> "PortableTransferSession":
        return PortableTransferSession(
            self,
            started_at or datetime.now().astimezone(),
            session_id=session_id,
        )

    def _remember(self, source_key: str) -> None:
        if self._keys is None:
            self._keys = set()
        self._keys.add(source_key)


class PortableTransferSession:
    def __init__(
        self,
        history: PortableTransferHistory,
        started_at: datetime,
        session_id: str = "",
    ):
        self.history = history
        self.started_at = started_at
        self.session_id = session_id or uuid.uuid4().hex
        self.path = self._build_path()
        self._initialized = False
        self._checksum_paths: dict[str, Path] = {}

    def _value(self, token: str, format_spec: str | None, algorithm: str = "") -> str:
        if token == "date":
            return self.started_at.strftime(format_spec or "%Y-%m-%d")
        if token in {"card", "camera"}:
            return self.history.card_name
        if token == "card_id":
            return self.history.card_id
        if token == "computer":
            return self.history.instance_name
        if token == "instance":
            return self.history.instance_id[:8]
        if token == "library":
            return self.history.library_id[:8]
        if token == "session":
            return self.session_id[:8]
        if token == "algorithm":
            return algorithm or "checksum"
        return token

    def _render(self, template: str, algorithm: str = "") -> str:
        return SESSION_TOKEN.sub(
            lambda match: self._value(match.group(1), match.group(2), algorithm), template
        )

    def _build_path(self) -> Path:
        segments = [safe_segment(self._render(segment)) for segment in self.history.folder_segments if segment]
        filename = safe_segment(self._render(self.history.filename_template), "transfer-session.jsonl")
        if not Path(filename).suffix:
            filename += ".jsonl"
        path = self.history.history_dir.joinpath(*segments, filename)
        if path.exists():
            path = path.with_name(f"{path.stem}_{self.session_id[:8]}{path.suffix}")
        return path

    def _checksum_path(self, algorithm: str) -> Path:
        if algorithm in self._checksum_paths:
            return self._checksum_paths[algorithm]
        segments = [
            safe_segment(self._render(segment, algorithm))
            for segment in self.history.folder_segments
            if segment
        ]
        filename = safe_segment(
            self._render(self.history.checksum_filename_template, algorithm),
            f"{self.session_id[:8]}.{algorithm}",
        )
        path = self.history.history_dir.joinpath(*segments, filename)
        if path.exists():
            path = path.with_name(f"{path.stem}_{self.session_id[:8]}{path.suffix}")
        self._checksum_paths[algorithm] = path
        return path

    def _initialize(self) -> None:
        if self._initialized:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        header = {
            "schema": 1,
            "record_type": "session",
            "session_id": self.session_id,
            "card_id": self.history.card_id,
            "card_name": self.history.card_name,
            "instance_id": self.history.instance_id,
            "instance_name": self.history.instance_name,
            "library_id": self.history.library_id,
            "started_at": self.started_at.astimezone(timezone.utc).isoformat(),
        }
        with self.path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(header, separators=(",", ":"), sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._initialized = True

    def _append_checksum(self, algorithm: str, digest: str, destination_relative_path: str) -> Path:
        checksum_path = self._checksum_path(algorithm)
        checksum_path.parent.mkdir(parents=True, exist_ok=True)
        if not checksum_path.exists():
            with checksum_path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(f"# Photo Card Organizer {algorithm} checksums\n")
                handle.write(f"# Session {self.session_id}\n")
                handle.flush()
                os.fsync(handle.fileno())
        with checksum_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{digest}  {destination_relative_path}\n")
            handle.flush()
            os.fsync(handle.fileno())
        return checksum_path

    def append_verified(
        self,
        *,
        source_key: str,
        source_relative_path: str,
        source_size: int,
        source_mtime_ns: int,
        destination_relative_path: str,
        action: str,
        verification: str,
        content_checksum: str = "",
        checksum_algorithm: str = "",
        transferred_at: datetime | None = None,
        replicas: list[dict[str, Any]] | None = None,
    ) -> None:
        self._initialize()
        checksum_path = None
        if content_checksum:
            checksum_path = self._append_checksum(
                checksum_algorithm or verification,
                content_checksum,
                destination_relative_path,
            )
        record: dict[str, Any] = {
            "schema": 1,
            "record_type": "transfer",
            "status": "verified",
            "session_id": self.session_id,
            "source_key": source_key,
            "card_id": self.history.card_id,
            "source_relative_path": source_relative_path,
            "source_size": source_size,
            "source_mtime_ns": source_mtime_ns,
            "destination_relative_path": destination_relative_path,
            "action": action,
            "verification": verification,
            "checksum_algorithm": checksum_algorithm or (verification if content_checksum else ""),
            "checksum_log": (
                checksum_path.relative_to(self.history.history_dir).as_posix() if checksum_path else ""
            ),
            "instance_id": self.history.instance_id,
            "instance_name": self.history.instance_name,
            "library_id": self.history.library_id,
            "content_checksum": content_checksum,
            "replicas": replicas or [],
            "transferred_at": (transferred_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
        }
        encoded = json.dumps(record, separators=(",", ":"), sort_keys=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.history._remember(source_key)
