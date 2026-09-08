from __future__ import annotations

import json
import copy
import logging
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from .models import MediaMetadata

try:
    from PIL import ExifTags, Image
except ImportError:  # pragma: no cover - exercised on minimal installations
    ExifTags = None
    Image = None

try:
    import exifread
except ImportError:  # pragma: no cover - optional RAW metadata dependency
    exifread = None
else:
    exifread_logger = logging.getLogger("exifread")
    if not exifread_logger.handlers:
        exifread_logger.addHandler(logging.NullHandler())
        exifread_logger.propagate = False


def parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    text = str(value).strip().strip("\x00")
    if not text or text.startswith("0000:00:00"):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    for pattern in (
        "%Y:%m:%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y:%m:%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def _number(value: Any) -> float:
    if hasattr(value, "num") and hasattr(value, "den"):
        return float(value.num) / float(value.den or 1)
    if isinstance(value, tuple) and len(value) == 2:
        return float(value[0]) / float(value[1] or 1)
    return float(value)


def _optional_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    values = getattr(value, "values", value)
    if isinstance(values, (list, tuple)) and len(values) == 1:
        values = values[0]
    try:
        return _number(values)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _dms_to_decimal(values: Any, reference: object) -> float | None:
    try:
        degrees, minutes, seconds = (_number(item) for item in values)
        decimal = degrees + minutes / 60.0 + seconds / 3600.0
        if str(reference).upper().strip() in {"S", "W"}:
            decimal *= -1
        return decimal
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _parse_coordinate(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    direction = text[-1:].upper()
    sign = -1 if direction in {"S", "W"} else 1
    if direction in {"N", "S", "E", "W"}:
        text = text[:-1].strip()
    try:
        return float(text) * sign
    except ValueError:
        pass
    match = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(\d+(?:\.\d+)?)", text)
    if match:
        return sign * (abs(float(match.group(1))) + float(match.group(2)) / 60.0)
    return None


def _rating(value: object, percent: bool = False) -> int | None:
    values = getattr(value, "values", value)
    if isinstance(values, (list, tuple)) and len(values) == 1:
        values = values[0]
    try:
        number = _number(values)
    except (TypeError, ValueError):
        return None
    if percent:
        number = round(number / 20)
    return max(0, min(5, int(round(number))))


def _read_pillow(path: Path) -> dict[str, Any]:
    if Image is None:
        return {}
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            if not exif:
                return {}
            data: dict[str, Any] = {
                "date": exif.get(36867) or exif.get(36868) or exif.get(306),
                "make": exif.get(271, ""),
                "model": exif.get(272, ""),
                "exposure_time": exif.get(33434),
                "exposure_bias": exif.get(37380),
                "rating": exif.get(18246),
                "rating_percent": exif.get(18249),
            }
            gps = None
            try:
                gps = exif.get_ifd(ExifTags.IFD.GPSInfo) if ExifTags else None
            except (AttributeError, KeyError, TypeError):
                gps = exif.get(34853)
            if isinstance(gps, dict):
                data["latitude"] = _dms_to_decimal(gps.get(2), gps.get(1))
                data["longitude"] = _dms_to_decimal(gps.get(4), gps.get(3))
            return data
    except (OSError, ValueError):
        return {}


def _read_exifread(path: Path) -> dict[str, Any]:
    if exifread is None:
        return {}
    try:
        with path.open("rb") as handle:
            tags = exifread.process_file(handle, details=False, strict=False)
    except (OSError, ValueError):
        return {}
    latitude = tags.get("GPS GPSLatitude")
    longitude = tags.get("GPS GPSLongitude")
    return {
        "date": tags.get("EXIF DateTimeOriginal") or tags.get("EXIF DateTimeDigitized") or tags.get("Image DateTime"),
        "make": tags.get("Image Make", ""),
        "model": tags.get("Image Model", ""),
        "exposure_time": tags.get("EXIF ExposureTime"),
        "exposure_bias": tags.get("EXIF ExposureBiasValue"),
        "rating": tags.get("Image Rating") or tags.get("EXIF Rating"),
        "latitude": _dms_to_decimal(latitude.values, tags.get("GPS GPSLatitudeRef")) if latitude else None,
        "longitude": _dms_to_decimal(longitude.values, tags.get("GPS GPSLongitudeRef")) if longitude else None,
    }


def _read_exiftool(path: Path) -> dict[str, Any]:
    executable = shutil.which("exiftool")
    if not executable:
        return {}
    command = [
        executable,
        "-j",
        "-n",
        "-DateTimeOriginal",
        "-CreateDate",
        "-MediaCreateDate",
        "-Make",
        "-Model",
        "-ExposureTime",
        "-ExposureCompensation",
        "-Rating",
        "-GPSLatitude",
        "-GPSLongitude",
        str(path),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
        if completed.returncode != 0:
            return {}
        records = json.loads(completed.stdout)
        record = records[0] if records else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {}
    return {
        "date": record.get("DateTimeOriginal") or record.get("CreateDate") or record.get("MediaCreateDate"),
        "make": record.get("Make", ""),
        "model": record.get("Model", ""),
        "exposure_time": record.get("ExposureTime"),
        "exposure_bias": record.get("ExposureCompensation"),
        "rating": record.get("Rating"),
        "latitude": record.get("GPSLatitude"),
        "longitude": record.get("GPSLongitude"),
    }


def _local_name(name: str) -> str:
    return name.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _read_xmp(path: Path) -> dict[str, Any]:
    candidates = [path] if path.suffix.lower() == ".xmp" else [path.with_suffix(".xmp"), path.with_suffix(".XMP")]
    sidecar = next((candidate for candidate in candidates if candidate.is_file()), None)
    if sidecar is None:
        return {}
    try:
        root = ET.parse(sidecar).getroot()
    except (OSError, ET.ParseError):
        return {}
    values: dict[str, str] = {}
    wanted = {
        "Rating", "DateTimeOriginal", "DateCreated", "CreateDate",
        "ExposureTime", "ExposureBiasValue", "ExposureCompensation",
        "GPSLatitude", "GPSLongitude", "Make", "Model",
    }
    for element in root.iter():
        local = _local_name(element.tag)
        if local in wanted and element.text:
            values[local] = element.text.strip()
        for key, value in element.attrib.items():
            local = _local_name(key)
            if local in wanted:
                values[local] = value.strip()
    return {
        "date": values.get("DateTimeOriginal") or values.get("DateCreated") or values.get("CreateDate"),
        "make": values.get("Make", ""),
        "model": values.get("Model", ""),
        "exposure_time": values.get("ExposureTime"),
        "exposure_bias": (
            values.get("ExposureBiasValue")
            or values.get("ExposureCompensation")
        ),
        "rating": values.get("Rating"),
        "latitude": _parse_coordinate(values.get("GPSLatitude")),
        "longitude": _parse_coordinate(values.get("GPSLongitude")),
    }


def extract_metadata(
    path: Path,
    media_kind: str,
    *,
    use_exiftool: bool = True,
) -> MediaMetadata:
    path = path.resolve()
    signature = []
    for candidate in (path, path.with_suffix(".xmp"), path.with_suffix(".XMP")):
        try:
            stat = candidate.stat()
            signature.append((stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino))
        except FileNotFoundError:
            signature.append(None)
    executable = shutil.which("exiftool") if use_exiftool else None
    return copy.deepcopy(_cached_metadata(path, media_kind, bool(executable), tuple(signature)))


@lru_cache(maxsize=8192)
def _cached_metadata(path: Path, media_kind: str, use_exiftool: bool, signature: tuple) -> MediaMetadata:
    fallback_date = datetime.fromtimestamp(path.stat().st_mtime)
    merged: dict[str, Any] = {}
    readers = [_read_exiftool] if use_exiftool else []
    if media_kind == "photo":
        # Pillow is the stronger JPEG/TIFF reader and should win when ExifRead
        # returns wrapper tag objects for fields such as Rating.
        readers.extend([_read_exifread, _read_pillow])
    elif media_kind == "raw":
        readers.append(_read_exifread)
    readers.append(_read_xmp)
    for reader in readers:
        data = reader(path)
        for key, value in data.items():
            if value not in {None, ""}:
                merged[key] = value

    rating = _rating(merged.get("rating"))
    if rating is None:
        rating = _rating(merged.get("rating_percent"), percent=True)
    captured_at = parse_datetime(merged.get("date")) or fallback_date
    if captured_at.tzinfo is not None:
        captured_at = captured_at.astimezone().replace(tzinfo=None)
    return MediaMetadata(
        captured_at=captured_at,
        media_kind=media_kind,
        make=str(merged.get("make", "")).strip(),
        model=str(merged.get("model", "")).strip(),
        exposure_time_seconds=_optional_number(
            merged.get("exposure_time")
        ),
        exposure_bias=_optional_number(merged.get("exposure_bias")),
        rating=rating,
        latitude=_parse_coordinate(merged.get("latitude")),
        longitude=_parse_coordinate(merged.get("longitude")),
    )
