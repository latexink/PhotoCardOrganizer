from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from photocard.brackets import assign_long_exposure_groups
from photocard.config import (
    CURRENT_CONFIG_SCHEMA,
    build_client_settings,
    import_client_settings,
    normalize_config,
)
from photocard.library_state import (
    CURRENT_LIBRARY_SCHEMA,
    LIBRARY_METADATA_FORMAT,
    metadata_path,
    read_library_metadata,
    upgrade_library_metadata,
)
from photocard.library_tools import export_captures
from photocard.discovery import folder_import_source
from photocard.models import CardMarker, MediaMetadata
from photocard.organizer import Organizer
from photocard.qt_common import import_destination_prefix
from photocard.templates import render_folder


class Release070Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_schema_four_becomes_one_named_default_library(self) -> None:
        library = self.base / "Desktop Library"

        migrated = normalize_config(
            {
                "schema": 4,
                "destination_root": str(library),
                "instance": {"library_id": "desktop-main"},
            }
        )

        self.assertEqual(CURRENT_CONFIG_SCHEMA, migrated["schema"])
        self.assertEqual("desktop-main", migrated["default_library_id"])
        self.assertEqual("desktop-main", migrated["instance"]["library_id"])
        self.assertEqual(
            [
                {
                    "id": "desktop-main",
                    "name": "Desktop Library",
                    "root": str(library),
                    "kind": "local",
                    "storage_profile": "auto",
                    "enabled": True,
                }
            ],
            migrated["library_destinations"],
        )

    def test_portable_named_libraries_do_not_replace_local_roots(self) -> None:
        desktop = self.base / "desktop"
        archive = self.base / "archive"
        local = normalize_config(
            {
                "library_destinations": [
                    {
                        "id": "main",
                        "name": "Desktop",
                        "root": str(desktop),
                        "enabled": True,
                    },
                    {
                        "id": "archive",
                        "name": "Archive",
                        "root": str(archive),
                        "kind": "removable",
                        "enabled": True,
                    },
                ],
                "default_library_id": "main",
            }
        )
        portable_source = normalize_config(
            {
                "library_destinations": [
                    {
                        "id": "main",
                        "name": "Renamed desktop",
                        "root": "Z:/other-computer",
                        "enabled": True,
                    }
                ],
                "default_library_id": "main",
            }
        )
        settings_path = self.base / "client-settings.json"
        settings_path.write_text(
            json.dumps(build_client_settings(portable_source)),
            encoding="utf-8",
        )

        imported = import_client_settings(local, settings_path)
        libraries = {
            library["id"]: library
            for library in imported["library_destinations"]
        }

        self.assertEqual(str(desktop), libraries["main"]["root"])
        self.assertTrue(libraries["main"]["enabled"])
        self.assertEqual("Renamed desktop", libraries["main"]["name"])
        self.assertEqual(str(archive), libraries["archive"]["root"])

    def test_library_metadata_upgrade_is_backed_up_and_idempotent(self) -> None:
        library = self.base / "library"
        library.mkdir()
        path = metadata_path(library)
        path.parent.mkdir()
        path.write_text(
            json.dumps(
                {
                    "format": LIBRARY_METADATA_FORMAT,
                    "schema": 0,
                    "library_id": "main",
                    "name": "Old name",
                    "created_at": "2025-01-01T00:00:00+00:00",
                    "migrations": [],
                }
            ),
            encoding="utf-8",
        )
        legacy = library / "PhotoCardOrganizer-export-old.jsonl"
        legacy.write_text('{"old": true}\n', encoding="utf-8")

        first = upgrade_library_metadata(
            library,
            library_id="main",
            name="Main library",
        )
        second = upgrade_library_metadata(
            library,
            library_id="main",
            name="Main library",
        )

        self.assertEqual(0, first.upgraded_from)
        self.assertIsNotNone(first.backup_path)
        self.assertTrue(first.backup_path and first.backup_path.is_file())
        self.assertEqual(1, first.migrated_export_sessions)
        self.assertIsNone(second.upgraded_from)
        self.assertIsNone(second.backup_path)
        self.assertEqual(0, second.migrated_export_sessions)
        self.assertFalse(legacy.exists())
        self.assertFalse(path.with_suffix(".json.tmp").exists())
        payload = read_library_metadata(library)
        self.assertIsNotNone(payload)
        self.assertEqual(CURRENT_LIBRARY_SCHEMA, payload["schema"])
        self.assertEqual("Main library", payload["name"])

    def test_future_library_metadata_is_left_unchanged(self) -> None:
        library = self.base / "future-library"
        library.mkdir()
        path = metadata_path(library)
        path.parent.mkdir()
        future = {
            "format": LIBRARY_METADATA_FORMAT,
            "schema": CURRENT_LIBRARY_SCHEMA + 1,
            "library_id": "future",
        }
        path.write_text(json.dumps(future), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "supports up to"):
            upgrade_library_metadata(
                library,
                library_id="future",
                name="Future",
            )

        self.assertEqual(
            future,
            json.loads(path.read_text(encoding="utf-8")),
        )
        self.assertFalse((path.parent / "backups").exists())

    def test_editing_export_migrates_legacy_session_records(self) -> None:
        destination = self.base / "editing"
        destination.mkdir()
        legacy = destination / "PhotoCardOrganizer-export-legacy.jsonl"
        legacy.write_text('{"legacy": true}\n', encoding="utf-8")

        stats = export_captures([], destination)

        sessions = (
            destination
            / ".photocard-organizer"
            / "export-sessions"
        )
        self.assertFalse(legacy.exists())
        self.assertTrue(stats.log_path and stats.log_path.parent == sessions)
        self.assertEqual(2, len(list(sessions.glob("*.jsonl"))))

    def test_month_and_conditional_group_levels_render_independently(self) -> None:
        card = CardMarker(
            root=self.base,
            identity_dir=self.base / ".photocard",
            identity_path=self.base / ".photocard" / "identity.json",
            history_dir=self.base / ".photocard" / "transfers",
            card_id="camera-card",
            name="Camera card",
        )
        source = self.base / "IMG_0001.JPG"
        metadata = MediaMetadata(
            captured_at=datetime(2026, 7, 28, 11, 30),
            media_kind="photo",
        )

        ordinary = render_folder(
            ["{date:%Y}", "{date:%m - %B}", "{capture_group}"],
            metadata,
            card,
            source,
        )
        metadata.capture_group = "Long Exposure Brackets"
        grouped = render_folder(
            ["{date:%Y}", "{date:%m - %B}", "{capture_group}"],
            metadata,
            card,
            source,
        )

        self.assertEqual(Path("2026", "07 - July"), ordinary)
        self.assertEqual(
            Path("2026", "07 - July", "Long Exposure Brackets"),
            grouped,
        )

    def test_named_import_routes_are_safe_and_predictable(self) -> None:
        self.assertEqual(
            str(Path("Events", "Weddings", "Smith-Jones")),
            import_destination_prefix("wedding", "Smith-Jones"),
        )
        self.assertEqual(
            str(Path("Clients", "Northwind")),
            import_destination_prefix("client", "Northwind"),
        )
        self.assertEqual(
            str(Path("Events", "Trips", "Iceland")),
            import_destination_prefix("trip", "Iceland"),
        )
        self.assertEqual(
            str(Path("Projects", "Catalog")),
            import_destination_prefix(
                "custom",
                str(Path("Projects", "Catalog")),
            ),
        )
        with self.assertRaisesRegex(ValueError, "inside"):
            import_destination_prefix(
                "custom",
                str(Path("..", "outside")),
            )

    def test_long_exposure_groups_are_partitioned_by_folder_and_camera(self) -> None:
        started = datetime(2026, 7, 28, 20, 0)
        source_files: list[tuple[Path, str]] = []
        metadata_by_path: dict[Path, MediaMetadata] = {}
        exposures = (0.5, 1.0, 2.0)
        for folder_name in ("A", "B"):
            for camera_name in ("Camera One", "Camera Two"):
                for index, exposure in enumerate(exposures):
                    path = (
                        self.base
                        / folder_name
                        / f"{camera_name[-3:].replace(' ', '')}_{index}.JPG"
                    )
                    source_files.append((path, "photo"))
                    metadata_by_path[path] = MediaMetadata(
                        captured_at=started
                        + timedelta(seconds=index * 2),
                        media_kind="photo",
                        model=camera_name,
                        exposure_time_seconds=exposure,
                        exposure_bias=float(index - 1),
                    )

        count = assign_long_exposure_groups(
            source_files,
            metadata_by_path,
            {
                "enabled": True,
                "folder_name": "Long Exposure Brackets",
                "minimum_group_size": 3,
                "maximum_gap_seconds": 5,
                "minimum_long_exposure_seconds": 1,
            },
        )

        self.assertEqual(4, count)
        self.assertTrue(
            all(
                metadata.capture_group
                == "Long Exposure Brackets"
                for metadata in metadata_by_path.values()
            )
        )

    def test_long_exposure_detection_rejects_unvaried_sequences(self) -> None:
        started = datetime(2026, 7, 28, 20, 0)
        source_files = []
        metadata_by_path = {}
        for index in range(4):
            path = self.base / "shoot" / f"IMG_{index:04d}.JPG"
            source_files.append((path, "photo"))
            metadata_by_path[path] = MediaMetadata(
                captured_at=started + timedelta(seconds=index),
                media_kind="photo",
                model="Camera",
                exposure_time_seconds=2.0,
                exposure_bias=0.0,
            )

        count = assign_long_exposure_groups(
            source_files,
            metadata_by_path,
            {
                "enabled": True,
                "folder_name": "Long Exposure Brackets",
                "minimum_group_size": 3,
                "maximum_gap_seconds": 5,
                "minimum_long_exposure_seconds": 1,
            },
        )

        self.assertEqual(0, count)
        self.assertTrue(
            all(
                not metadata.capture_group
                for metadata in metadata_by_path.values()
            )
        )

    def test_bracket_preflight_reads_only_visual_candidates_once(self) -> None:
        source = self.base / "source"
        destination = self.base / "library"
        source.mkdir()
        for name in (
            "IMG_0001.JPG",
            "IMG_0002.JPG",
            "IMG_0003.JPG",
            "IMG_0001.XMP",
            "CLIP_0001.MP4",
        ):
            (source / name).write_bytes(name.encode("ascii"))
        config = normalize_config(
            {
                "destination_root": str(destination),
                "monitor": {"settle_seconds": 0},
                "local_history": {"enabled": False},
                "safety": {
                    "minimum_destination_free_percent": 0,
                    "minimum_destination_free_gb": 0,
                    "warn_source_free_percent": 0,
                },
                "organization": {
                    "long_exposure_brackets": {
                        "enabled": True,
                        "folder_name": "Long Exposure Brackets",
                        "minimum_group_size": 3,
                        "maximum_gap_seconds": 5,
                        "minimum_long_exposure_seconds": 1,
                    }
                },
                "media_rules": {
                    "photo": {
                        "folder_segments": [
                            "Photos",
                            "{capture_group}",
                        ]
                    },
                    "raw": {
                        "folder_segments": [
                            "RAW",
                            "{capture_group}",
                        ]
                    },
                    "video": {
                        "folder_segments": [
                            "Videos",
                            "{capture_group}",
                        ]
                    },
                    "sidecar": {
                        "folder_segments": [
                            "Sidecars",
                            "{capture_group}",
                        ]
                    },
                },
            }
        )
        started = datetime(2026, 7, 28, 20, 0)
        calls: list[str] = []

        def metadata_for(path: Path, media_kind: str) -> MediaMetadata:
            calls.append(path.name)
            match = {
                "IMG_0001.JPG": (0, 0.5, -1.0),
                "IMG_0002.JPG": (1, 1.0, 0.0),
                "IMG_0003.JPG": (2, 2.0, 1.0),
            }.get(path.name)
            if match is None:
                return MediaMetadata(
                    captured_at=started,
                    media_kind=media_kind,
                    model="Camera",
                )
            offset, exposure, bias = match
            return MediaMetadata(
                captured_at=started + timedelta(seconds=offset),
                media_kind=media_kind,
                model="Camera",
                exposure_time_seconds=exposure,
                exposure_bias=bias,
            )

        with patch(
            "photocard.organizer.extract_metadata",
            side_effect=metadata_for,
        ):
            stats = Organizer(config).scan_card(
                folder_import_source(source)
            )

        self.assertEqual(5, stats.imported)
        self.assertEqual(
            {
                "IMG_0001.JPG": 1,
                "IMG_0002.JPG": 1,
                "IMG_0003.JPG": 1,
                "IMG_0001.XMP": 1,
                "CLIP_0001.MP4": 1,
            },
            {name: calls.count(name) for name in set(calls)},
        )
        self.assertTrue(
            (
                destination
                / "Sidecars"
                / "Long Exposure Brackets"
                / "IMG_0001.XMP"
            ).is_file()
        )
        self.assertTrue(
            (
                destination
                / "Videos"
                / "CLIP_0001.MP4"
            ).is_file()
        )


if __name__ == "__main__":
    unittest.main()
