from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .models import MediaMetadata


@dataclass
class _Capture:
    key: tuple[str, str]
    captured_at: datetime
    camera: str
    exposure_time_seconds: float | None
    exposure_bias: float | None
    paths: list[Path] = field(default_factory=list)


def capture_key(path: Path) -> tuple[str, str]:
    return (
        str(path.parent).casefold(),
        path.stem.casefold(),
    )


def _same_series(first: _Capture, second: _Capture) -> bool:
    return (
        first.key[0] == second.key[0]
        and first.camera.casefold() == second.camera.casefold()
    )


def _qualifies(
    captures: list[_Capture],
    *,
    minimum_group_size: int,
    minimum_long_exposure_seconds: float,
) -> bool:
    if len(captures) < minimum_group_size:
        return False
    exposure_times = sorted(
        {
            round(value, 8)
            for value in (
                capture.exposure_time_seconds
                for capture in captures
            )
            if value is not None and value > 0
        }
    )
    exposure_biases = sorted(
        {
            round(value, 4)
            for value in (
                capture.exposure_bias for capture in captures
            )
            if value is not None
        }
    )
    has_long_exposure = any(
        value >= minimum_long_exposure_seconds
        for value in exposure_times
    )
    varied_shutter = bool(
        len(exposure_times) >= 2
        and exposure_times[-1] / exposure_times[0] >= 1.5
    )
    varied_bias = bool(
        len(exposure_biases) >= 2
        and exposure_biases[-1] - exposure_biases[0] >= 0.5
    )
    return has_long_exposure and (varied_shutter or varied_bias)


def assign_long_exposure_groups(
    source_files: list[tuple[Path, str]],
    metadata_by_path: dict[Path, MediaMetadata],
    settings: dict,
) -> int:
    if not settings.get("enabled", False):
        return 0
    minimum_group_size = max(
        2,
        int(settings.get("minimum_group_size", 3)),
    )
    maximum_gap_seconds = max(
        0.5,
        float(settings.get("maximum_gap_seconds", 30.0)),
    )
    minimum_long_exposure_seconds = max(
        0.0,
        float(settings.get("minimum_long_exposure_seconds", 1.0)),
    )
    folder_name = str(
        settings.get("folder_name", "Long Exposure Brackets")
    ).strip()
    if not folder_name:
        return 0

    captures_by_key: dict[tuple[str, str], _Capture] = {}
    attached_paths: dict[tuple[str, str], list[Path]] = {}
    for path, media_kind in source_files:
        key = capture_key(path)
        attached_paths.setdefault(key, []).append(path)
        if media_kind not in {"photo", "raw"}:
            continue
        metadata = metadata_by_path.get(path)
        if metadata is None:
            continue
        capture = captures_by_key.get(key)
        if capture is None:
            captures_by_key[key] = _Capture(
                key=key,
                captured_at=metadata.captured_at,
                camera=metadata.camera,
                exposure_time_seconds=metadata.exposure_time_seconds,
                exposure_bias=metadata.exposure_bias,
                paths=[path],
            )
            continue
        capture.paths.append(path)
        if (
            capture.exposure_time_seconds is None
            and metadata.exposure_time_seconds is not None
        ):
            capture.exposure_time_seconds = (
                metadata.exposure_time_seconds
            )
        if (
            capture.exposure_bias is None
            and metadata.exposure_bias is not None
        ):
            capture.exposure_bias = metadata.exposure_bias
        if not capture.camera and metadata.camera:
            capture.camera = metadata.camera

    captures_by_series: dict[
        tuple[str, str],
        list[_Capture],
    ] = {}
    for capture in captures_by_key.values():
        series_key = (
            capture.key[0],
            capture.camera.casefold(),
        )
        captures_by_series.setdefault(series_key, []).append(capture)
    if not captures_by_series:
        return 0

    groups: list[list[_Capture]] = []
    for series_key in sorted(captures_by_series):
        captures = sorted(
            captures_by_series[series_key],
            key=lambda capture: (
                capture.captured_at,
                capture.key,
            ),
        )
        run: list[_Capture] = [captures[0]]
        for capture in captures[1:] + [None]:
            if capture is not None:
                gap = (
                    capture.captured_at
                    - run[-1].captured_at
                ).total_seconds()
                if (
                    0 <= gap <= maximum_gap_seconds
                    and _same_series(run[-1], capture)
                ):
                    run.append(capture)
                    continue
            if _qualifies(
                run,
                minimum_group_size=minimum_group_size,
                minimum_long_exposure_seconds=(
                    minimum_long_exposure_seconds
                ),
            ):
                groups.append(run)
            run = [capture] if capture is not None else []

    for group in groups:
        for capture in group:
            for path in attached_paths.get(capture.key, capture.paths):
                metadata = metadata_by_path.get(path)
                if metadata is not None:
                    metadata.capture_group = folder_name
    return len(groups)
