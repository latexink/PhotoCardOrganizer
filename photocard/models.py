from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CardMarker:
    root: Path
    identity_dir: Path
    identity_path: Path
    history_dir: Path
    card_id: str
    name: str
    action: str = "copy"
    source_folders: tuple[str, ...] = ("DCIM",)
    destination_prefix: str = ""
    camera_name: str = ""
    enabled: bool = True
    delete_empty_folders_after_move: bool = False
    recursive: bool = True
    portable_history: bool = True
    source_type: str = "card"
    ignored_source_folders: tuple[str, ...] = ()


@dataclass
class MediaMetadata:
    captured_at: datetime
    media_kind: str
    make: str = ""
    model: str = ""
    exposure_time_seconds: float | None = None
    exposure_bias: float | None = None
    capture_group: str = ""
    rating: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    location_name: str = ""

    @property
    def camera(self) -> str:
        return self.model or self.make


@dataclass(frozen=True)
class Capacity:
    path: Path
    total_bytes: int
    used_bytes: int
    free_bytes: int

    @property
    def free_percent(self) -> float:
        if not self.total_bytes:
            return 0.0
        return (self.free_bytes / self.total_bytes) * 100.0


@dataclass
class ImportStats:
    card_id: str = ""
    card_name: str = ""
    action: str = "copy"
    discovered: int = 0
    imported: int = 0
    skipped: int = 0
    blocked: int = 0
    failed: int = 0
    bytes_imported: int = 0
    pending_destructive_confirmation: bool = False
    stopped_early: bool = False
    requested_destination_root: str = ""
    persist_destination_change: bool = False
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def merge(self, other: "ImportStats") -> None:
        self.discovered += other.discovered
        self.imported += other.imported
        self.skipped += other.skipped
        self.blocked += other.blocked
        self.failed += other.failed
        self.bytes_imported += other.bytes_imported
        self.pending_destructive_confirmation |= other.pending_destructive_confirmation
        self.stopped_early |= other.stopped_early
        if other.requested_destination_root:
            self.requested_destination_root = other.requested_destination_root
            self.persist_destination_change = other.persist_destination_change
        self.warnings.extend(other.warnings)
        self.errors.extend(other.errors)


@dataclass(frozen=True)
class ActivityEvent:
    level: str
    message: str
    timestamp: datetime = field(default_factory=datetime.now)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionRequest:
    kind: str
    title: str
    message: str
    options: tuple[tuple[str, str], ...]
    default_action: str
    source: Path | None = None
    destination: Path | None = None
    allow_apply_to_session: bool = True
    allow_choose_destination: bool = False


@dataclass(frozen=True)
class DecisionResult:
    action: str
    value: str = ""
    apply_to_session: bool = False
    persist: bool = False
