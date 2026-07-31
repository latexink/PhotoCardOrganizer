from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .metadata import extract_metadata
from .models import MediaMetadata


YEAR = re.compile(r"^(?:19|20)\d{2}$")
YEAR_MONTH = re.compile(r"^(?:19|20)\d{2}[-_.](?:0?[1-9]|1[0-2])$")
DATE = re.compile(
    r"^(?:19|20)\d{2}[-_.](?:0?[1-9]|1[0-2])[-_.](?:0?[1-9]|[12]\d|3[01])$"
)
COMPACT_DATE = re.compile(r"^(?:19|20)\d{6}$")
RATING = re.compile(r"^(?:[0-5]\s*(?:stars?)?|unrated|rating[-_ ]?[0-5])$", re.IGNORECASE)
GPS_FOLDER = re.compile(r"^gps\s*-?\d+(?:\.\d+)?[, _]+-?\d+(?:\.\d+)?$", re.IGNORECASE)
CAMERA_WORDS = {
    "canon",
    "nikon",
    "sony",
    "fujifilm",
    "fuji",
    "lumix",
    "panasonic",
    "olympus",
    "leica",
    "pentax",
    "hasselblad",
    "phaseone",
    "phase one",
    "gopro",
    "dji",
    "iphone",
    "pixel",
    "eos",
}
DEFAULT_ANALYSIS_FILE_LIMIT = 10_000
MAX_EDITABLE_SOURCE_LEVELS = 12


@dataclass(frozen=True)
class DetectedLevel:
    index: int
    token: str
    label: str
    confidence: float
    coverage: float
    examples: tuple[str, ...]


@dataclass(frozen=True)
class DetectedMediaRule:
    media_kind: str
    file_count: int
    levels: tuple[DetectedLevel, ...]
    sample_paths: tuple[str, ...]

    @property
    def segments(self) -> list[str]:
        return [level.token for level in self.levels if level.token]


@dataclass(frozen=True)
class StructureAnalysis:
    source_root: Path
    scanned_files: int
    matched_files: int
    scanned_folders: int
    truncated: bool
    deepest_level: int
    preview_file_limit: int
    editable_levels: int
    rules: dict[str, DetectedMediaRule]


def _is_link_like(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return True


def _normalized(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _ratio(values: list[str], predicate: Callable[[str], bool]) -> float:
    return sum(1 for value in values if predicate(value)) / max(1, len(values))


def _camera_matches(value: str, metadata: MediaMetadata) -> bool:
    folder = _normalized(value)
    if not folder:
        return False
    candidates = {
        _normalized(metadata.camera),
        _normalized(metadata.make),
        _normalized(f"{metadata.make} {metadata.model}"),
    }
    candidates.discard("")
    return any(folder == candidate or folder in candidate or candidate in folder for candidate in candidates)


def _classify_level(
    index: int,
    values: list[str],
    media_kind: str,
    metadata_pairs: list[tuple[tuple[str, ...], MediaMetadata]],
    file_count: int,
) -> DetectedLevel:
    coverage = len(values) / max(1, file_count)
    examples = tuple(dict.fromkeys(values))[:5]
    media_words = {
        "photo": {"photo", "photos", "image", "images", "jpeg", "jpg"},
        "raw": {"raw", "raws", "raw photo", "raw photos"},
        "video": {"video", "videos", "movie", "movies", "clips"},
        "sidecar": {"sidecar", "sidecars", "metadata", "xmp"},
    }[media_kind]
    classifiers: list[tuple[str, str, float]] = [
        ("{media}", "Media type", _ratio(values, lambda value: _normalized(value) in media_words)),
        ("{date:%Y}", "Year", _ratio(values, lambda value: bool(YEAR.fullmatch(value.strip())))),
        (
            "{date:%Y-%m}",
            "Year and month",
            _ratio(values, lambda value: bool(YEAR_MONTH.fullmatch(value.strip()))),
        ),
        (
            "{date:%Y-%m-%d}",
            "Shoot date",
            _ratio(values, lambda value: bool(DATE.fullmatch(value.strip()) or COMPACT_DATE.fullmatch(value.strip()))),
        ),
        ("{rating}", "Rating", _ratio(values, lambda value: bool(RATING.fullmatch(value.strip())))),
        ("{location}", "Location", _ratio(values, lambda value: bool(GPS_FOLDER.fullmatch(value.strip())))),
    ]

    camera_observations = 0
    camera_matches = 0
    for parts, metadata in metadata_pairs:
        if index >= len(parts):
            continue
        camera_observations += 1
        if _camera_matches(parts[index], metadata):
            camera_matches += 1
    metadata_camera_ratio = camera_matches / max(1, camera_observations)
    heuristic_camera_ratio = _ratio(
        values,
        lambda value: any(word in _normalized(value) for word in CAMERA_WORDS),
    )
    classifiers.append(("{camera}", "Camera", max(metadata_camera_ratio, heuristic_camera_ratio)))

    token, label, confidence = max(classifiers, key=lambda item: item[2])
    if confidence < 0.7:
        token = f"{{source_dir:{index + 1}}}"
        label = f"Preserve source folder {index + 1}"
        confidence = max(0.55, coverage)
    if coverage < 0.5:
        token = ""
        label = "None"
        confidence = coverage
    return DetectedLevel(
        index=index,
        token=token,
        label=label,
        confidence=min(1.0, confidence),
        coverage=min(1.0, coverage),
        examples=examples,
    )


def detect_existing_structure(
    source_root: Path | str,
    media_rules: dict,
    *,
    include_subfolders: bool = True,
    max_files: int = DEFAULT_ANALYSIS_FILE_LIMIT,
    max_levels: int = MAX_EDITABLE_SOURCE_LEVELS,
    max_metadata_samples: int = 20,
    cancel_event: threading.Event | None = None,
) -> StructureAnalysis:
    if max_files < 1:
        raise ValueError("Structure analysis requires a file limit of at least one.")
    if max_levels < 1:
        raise ValueError("Structure analysis requires at least one editable folder level.")
    root = Path(source_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Source folder does not exist: {root}")
    extension_kind: dict[str, str] = {}
    for kind, rule in media_rules.items():
        if not rule.get("enabled", True):
            continue
        for extension in rule.get("extensions", []):
            normalized = str(extension).casefold()
            if normalized and not normalized.startswith("."):
                normalized = f".{normalized}"
            if normalized:
                extension_kind.setdefault(normalized, kind)

    records: dict[str, list[tuple[Path, tuple[str, ...]]]] = {
        kind: [] for kind in media_rules
    }
    scanned_files = 0
    scanned_folders = 0
    deepest_level = 0
    truncated = False
    skip_names = {".photocard", ".photocard-organizer", ".git", "__pycache__"}
    for current, directory_names, filenames in os.walk(root, followlinks=False):
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Structure detection was cancelled.")
        current_path = Path(current)
        scanned_folders += 1
        directory_names[:] = [
            name
            for name in directory_names
            if name not in skip_names and not _is_link_like(current_path / name)
        ]
        relative_parent = current_path.relative_to(root)
        parts = () if relative_parent == Path(".") else relative_parent.parts
        for filename in filenames:
            if scanned_files >= max_files:
                truncated = True
                break
            scanned_files += 1
            kind = extension_kind.get(Path(filename).suffix.casefold())
            if kind:
                records[kind].append((current_path / filename, tuple(parts)))
                deepest_level = max(deepest_level, len(parts))
        if truncated or not include_subfolders:
            directory_names[:] = []
        if truncated:
            break

    rules: dict[str, DetectedMediaRule] = {}
    samples_remaining = max_metadata_samples
    for kind, kind_records in records.items():
        if not kind_records:
            rules[kind] = DetectedMediaRule(kind, 0, (), ())
            continue
        metadata_pairs: list[tuple[tuple[str, ...], MediaMetadata]] = []
        if kind in {"photo", "raw"} and samples_remaining:
            sample_count = min(samples_remaining, max(3, min(8, len(kind_records))))
            stride = max(1, len(kind_records) // sample_count)
            for path, parts in kind_records[::stride][:sample_count]:
                if cancel_event and cancel_event.is_set():
                    raise InterruptedError("Structure detection was cancelled.")
                try:
                    metadata_pairs.append(
                        (parts, extract_metadata(path, kind, use_exiftool=False))
                    )
                except (OSError, ValueError):
                    continue
            samples_remaining -= len(metadata_pairs)
        level_count = min(
            max_levels,
            max((len(parts) for _path, parts in kind_records), default=0),
        )
        levels = []
        for index in range(level_count):
            values = [parts[index] for _path, parts in kind_records if index < len(parts)]
            levels.append(
                _classify_level(
                    index,
                    values,
                    kind,
                    metadata_pairs,
                    len(kind_records),
                )
            )
        rules[kind] = DetectedMediaRule(
            media_kind=kind,
            file_count=len(kind_records),
            levels=tuple(levels),
            sample_paths=tuple(str(path.relative_to(root)) for path, _parts in kind_records[:5]),
        )
    return StructureAnalysis(
        source_root=root,
        scanned_files=scanned_files,
        matched_files=sum(len(items) for items in records.values()),
        scanned_folders=scanned_folders,
        truncated=truncated,
        deepest_level=deepest_level,
        preview_file_limit=max_files,
        editable_levels=max_levels,
        rules=rules,
    )
