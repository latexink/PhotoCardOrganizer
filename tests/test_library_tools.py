from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from photocard.library_tools import (
    LibraryItem,
    build_capture_sets,
    detect_capture_groups,
    export_captures,
    scan_library,
)
from photocard.models import Capacity


class LibraryToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def item(
        path: Path,
        relative: str,
        kind: str,
        captured_at: datetime,
        capture_scope: str = "",
    ) -> LibraryItem:
        return LibraryItem(
            path=path,
            relative_path=Path(relative),
            media_kind=kind,
            captured_at=captured_at,
            camera="Canon EOS R5",
            capture_scope=capture_scope,
        )

    def test_capture_sets_keep_jpeg_raw_and_sidecar_together(self) -> None:
        captured = datetime(2026, 7, 12, 10, 30)
        items = [
            self.item(self.base / "IMG_0001.JPG", "shoot/IMG_0001.JPG", "photo", captured),
            self.item(self.base / "IMG_0001.CR3", "shoot/IMG_0001.CR3", "raw", captured),
            self.item(self.base / "IMG_0001.XMP", "shoot/IMG_0001.XMP", "sidecar", captured),
        ]

        captures = build_capture_sets(items)

        self.assertEqual(1, len(captures))
        self.assertEqual(3, len(captures[0].items))
        self.assertEqual("JPEG/photo + RAW + Sidecar", captures[0].media_label)

    def test_capture_sets_cross_configured_media_partition_folders(self) -> None:
        captured = datetime(2026, 7, 12, 10, 35)
        scope = "2026/2026-07-12/Canon EOS R5"
        items = [
            self.item(
                self.base / "Photos" / "IMG_0002.JPG",
                f"Photos/{scope}/IMG_0002.JPG",
                "photo",
                captured,
                scope,
            ),
            self.item(
                self.base / "RAW" / "IMG_0002.CR3",
                f"RAW/{scope}/IMG_0002.CR3",
                "raw",
                captured,
                scope,
            ),
            self.item(
                self.base / "Sidecars" / "IMG_0002.XMP",
                f"Sidecars/{scope}/IMG_0002.XMP",
                "sidecar",
                captured,
                scope,
            ),
        ]

        captures = build_capture_sets(items)

        self.assertEqual(1, len(captures))
        self.assertEqual("JPEG/photo + RAW + Sidecar", captures[0].media_label)

    def test_sidecar_timestamp_does_not_replace_visual_capture_time(self) -> None:
        visual_time = datetime(2026, 7, 12, 10, 40)
        old_sidecar_time = datetime(2001, 1, 1, 0, 0)
        captures = build_capture_sets(
            [
                self.item(
                    self.base / "IMG_0003.JPG",
                    "shoot/IMG_0003.JPG",
                    "photo",
                    visual_time,
                ),
                self.item(
                    self.base / "IMG_0003.XMP",
                    "shoot/IMG_0003.XMP",
                    "sidecar",
                    old_sidecar_time,
                ),
            ]
        )

        self.assertEqual(visual_time, captures[0].captured_at)

    def test_detects_short_brackets_and_regular_interval_sequences(self) -> None:
        start = datetime(2026, 7, 12, 10, 0)
        items = []
        for number, offset in enumerate((0, 1, 2, 100, 130, 160), start=1):
            items.append(
                self.item(
                    self.base / f"IMG_{number:04d}.JPG",
                    f"shoot/IMG_{number:04d}.JPG",
                    "photo",
                    start + timedelta(seconds=offset),
                )
            )

        groups = detect_capture_groups(
            build_capture_sets(items),
            bracket_seconds=3,
            interval_max_seconds=60,
        )

        self.assertEqual(["bracket", "interval"], [group.kind for group in groups])
        self.assertEqual([3, 3], [len(group.capture_ids) for group in groups])

    def test_verified_export_reuses_exact_files_and_preserves_conflicts(self) -> None:
        source = self.base / "source"
        destination = self.base / "editing"
        source.mkdir()
        jpg = source / "IMG_0020.JPG"
        raw = source / "IMG_0020.CR3"
        jpg.write_bytes(b"jpeg-content")
        raw.write_bytes(b"raw-content")
        captured = datetime(2026, 7, 12, 11, 0)
        capture = build_capture_sets(
            [
                self.item(jpg, "IMG_0020.JPG", "photo", captured),
                self.item(raw, "IMG_0020.CR3", "raw", captured),
            ]
        )[0]

        first = export_captures([capture], destination)
        second = export_captures([capture], destination)
        jpg.write_bytes(b"changed-jpeg-content")
        third = export_captures([capture], destination)

        self.assertEqual(2, first.files_copied)
        self.assertEqual(2, second.files_reused)
        self.assertEqual(1, third.files_copied)
        self.assertEqual(1, third.files_reused)
        self.assertEqual(b"changed-jpeg-content", (destination / "IMG_0020_2.JPG").read_bytes())
        self.assertTrue(first.log_path and first.log_path.is_file())
        records = [
            json.loads(line)
            for line in first.log_path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(2, len(records))
        self.assertTrue(all(record["verification"] == "sha256" for record in records))

    def test_catalog_skips_internal_manifest_folder(self) -> None:
        library = self.base / "library"
        internal = library / ".photocard-organizer"
        library.mkdir()
        internal.mkdir()
        (library / "IMG_0030.JPG").write_bytes(b"not-a-real-jpeg")
        (internal / "HIDDEN.JPG").write_bytes(b"internal")
        rules = {
            "photo": {"enabled": True, "extensions": [".jpg"]},
            "raw": {"enabled": False, "extensions": []},
        }

        items = scan_library(library, rules)

        self.assertEqual(["IMG_0030.JPG"], [item.path.name for item in items])

    def test_catalog_normalizes_default_media_partition_folders(self) -> None:
        library = self.base / "library"
        photo = (
            library
            / "Photos"
            / "2026"
            / "2026-07-12"
            / "Canon EOS R5"
            / "IMG_0035.JPG"
        )
        raw = (
            library
            / "RAW"
            / "2026"
            / "2026-07-12"
            / "Canon EOS R5"
            / "IMG_0035.CR3"
        )
        photo.parent.mkdir(parents=True)
        raw.parent.mkdir(parents=True)
        photo.write_bytes(b"jpeg")
        raw.write_bytes(b"raw")
        rules = {
            "photo": {
                "enabled": True,
                "extensions": [".jpg"],
                "folder_segments": [
                    "Photos",
                    "{date:%Y}",
                    "{date:%Y-%m-%d}",
                    "{camera}",
                ],
            },
            "raw": {
                "enabled": True,
                "extensions": [".cr3"],
                "folder_segments": [
                    "RAW",
                    "{date:%Y}",
                    "{date:%Y-%m-%d}",
                    "{camera}",
                ],
            },
        }

        captures = build_capture_sets(scan_library(library, rules))

        self.assertEqual(1, len(captures))
        self.assertEqual("JPEG/photo + RAW", captures[0].media_label)

    def test_export_source_change_is_contained_to_that_file(self) -> None:
        source = self.base / "source"
        destination = self.base / "editing"
        source.mkdir()
        photo = source / "IMG_0040.JPG"
        photo.write_bytes(b"original-content")
        capture = build_capture_sets(
            [
                self.item(
                    photo,
                    photo.name,
                    "photo",
                    datetime(2026, 7, 12, 12, 0),
                )
            ]
        )[0]
        real_copy = shutil.copy2

        def copy_then_change(source_path, temporary_path):
            result = real_copy(source_path, temporary_path)
            Path(source_path).write_bytes(b"changed-during-copy")
            return result

        with patch(
            "photocard.library_tools.shutil.copy2",
            side_effect=copy_then_change,
        ):
            result = export_captures([capture], destination)

        self.assertEqual(0, result.files_copied)
        self.assertEqual(1, len(result.errors))
        self.assertFalse((destination / photo.name).exists())
        self.assertEqual([], list(destination.glob(".pco-*.partial")))

    def test_export_honors_free_space_reserve(self) -> None:
        source = self.base / "source"
        destination = self.base / "editing"
        source.mkdir()
        photo = source / "IMG_0050.JPG"
        photo.write_bytes(b"content")
        capture = build_capture_sets(
            [
                self.item(
                    photo,
                    photo.name,
                    "photo",
                    datetime(2026, 7, 12, 12, 30),
                )
            ]
        )[0]
        capacity = Capacity(
            path=destination,
            total_bytes=100,
            used_bytes=50,
            free_bytes=50,
        )

        with patch("photocard.library_tools.capacity_for", return_value=capacity):
            result = export_captures(
                [capture],
                destination,
                minimum_free_percent=50,
            )

        self.assertEqual(0, result.files_copied)
        self.assertTrue(
            any("below 50.0% free space" in error for error in result.errors)
        )
        self.assertFalse((destination / photo.name).exists())


if __name__ == "__main__":
    unittest.main()
