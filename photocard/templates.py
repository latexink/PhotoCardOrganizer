from __future__ import annotations

import re
from pathlib import Path

from .models import CardMarker, MediaMetadata


TOKEN_PATTERN = re.compile(r"\{([a-z_]+)(?::([^}]+))?\}")
INVALID_PATH_CHARS = re.compile(r"[<>:\"/\\|?*\x00-\x1f]")
WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


SEGMENT_LABELS: dict[str, str] = {
    "None": "",
    "Photos": "Photos",
    "RAW": "RAW",
    "Videos": "Videos",
    "Sidecars": "Sidecars",
    "Year": "{date:%Y}",
    "Month": "{date:%m - %B}",
    "Year and month": "{date:%Y-%m}",
    "Shoot date": "{date:%Y-%m-%d}",
    "Camera": "{camera}",
    "Location": "{location}",
    "Rating": "{rating}",
    "Card name": "{card}",
    "Media type": "{media}",
    "Long-exposure brackets": "{capture_group}",
    **{
        f"Source folder {index}": f"{{source_dir:{index}}}"
        for index in range(1, 13)
    },
}
TEMPLATE_LABELS = {value: key for key, value in SEGMENT_LABELS.items()}


def safe_segment(value: object, fallback: str = "Unknown") -> str:
    text = str(value or "").strip()
    text = INVALID_PATH_CHARS.sub("_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text:
        text = fallback
    if text.upper() in WINDOWS_RESERVED:
        text = f"_{text}"
    return text[:120].rstrip(" .") or fallback


def _token_value(
    token: str,
    format_spec: str | None,
    metadata: MediaMetadata,
    card: CardMarker,
    source: Path,
) -> str:
    if token == "date":
        return metadata.captured_at.strftime(format_spec or "%Y-%m-%d")
    if token == "camera":
        return card.camera_name or metadata.camera or card.name or "Unknown camera"
    if token == "make":
        return metadata.make or "Unknown make"
    if token == "model":
        return metadata.model or card.name or "Unknown model"
    if token == "location":
        if metadata.location_name:
            return metadata.location_name
        if metadata.latitude is not None and metadata.longitude is not None:
            return f"GPS {metadata.latitude:.4f}, {metadata.longitude:.4f}"
        return "Unknown location"
    if token == "rating":
        return f"{metadata.rating} stars" if metadata.rating is not None else "Unrated"
    if token == "card":
        return card.name
    if token == "card_id":
        return card.card_id
    if token == "media":
        return {
            "photo": "Photos",
            "raw": "RAW",
            "video": "Videos",
            "sidecar": "Sidecars",
        }.get(metadata.media_kind, metadata.media_kind.capitalize())
    if token == "capture_group":
        return metadata.capture_group
    if token == "source_dir":
        try:
            index = max(0, int(format_spec or "1") - 1)
            parts = source.relative_to(card.root).parent.parts
            return parts[index] if index < len(parts) else f"Unknown source folder {index + 1}"
        except (OSError, ValueError):
            return "Unknown source folder"
    if token == "original":
        return source.name
    if token == "stem":
        return source.stem
    if token == "ext":
        return source.suffix.lower().lstrip(".")
    return token


def render_text(template: str, metadata: MediaMetadata, card: CardMarker, source: Path) -> str:
    return TOKEN_PATTERN.sub(
        lambda match: _token_value(match.group(1), match.group(2), metadata, card, source),
        template,
    )


def render_folder(
    segments: list[str],
    metadata: MediaMetadata,
    card: CardMarker,
    source: Path,
) -> Path:
    rendered = []
    for segment in segments:
        if not segment:
            continue
        value = render_text(segment, metadata, card, source).strip()
        if not value:
            continue
        rendered.append(safe_segment(value))
    return Path(*rendered) if rendered else Path()


def render_filename(template: str, metadata: MediaMetadata, card: CardMarker, source: Path) -> str:
    rendered = safe_segment(render_text(template, metadata, card, source), fallback=source.name)
    if "{ext}" not in template and "{original}" not in template and not Path(rendered).suffix:
        rendered += source.suffix.lower()
    return rendered
