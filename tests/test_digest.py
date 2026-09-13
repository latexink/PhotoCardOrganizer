from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from photocard.config import normalize_config
from photocard.digest import run_digest_profile
from photocard.manifest import ImportManifest
from photocard.monitor import MonitorService


class DigestWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def jpeg(
        path: Path,
        *,
        captured: str = "2025:03:04 05:06:07",
        color: str = "#4f725d",
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (20, 14), color)
        exif = Image.Exif()
        exif[36867] = captured
        exif[271] = "Nikon"
        exif[272] = "Nikon Z 8"
        image.save(path, exif=exif)
        timestamp = datetime(2025, 3, 4, 5, 6, 7).timestamp()
        os.utime(path, (timestamp, timestamp))

    def config(
        self,
        source: Path,
        destination: Path,
        *,
        action: str = "copy",
        auto_digest: bool = False,
    ) -> dict:
        return normalize_config(
            {
                "destination_root": str(destination),
                "identification": {
                    "auto_detect": False,
                    "configured_roots": [],
                },
                "digest_inboxes": [
                    {
                        "id": "legacy-drop",
                        "name": "Legacy Drop",
                        "root": str(source),
                        "enabled": True,
                        "include_subfolders": True,
                        "action": action,
                        "auto_digest": auto_digest,
                        "poll_seconds": 10,
                    }
                ],
                "monitor": {
                    "poll_seconds": 3600,
                    "settle_seconds": 0,
                },
                "safety": {
                    "copy_verification": "sha256",
                    "minimum_destination_free_percent": 0,
                    "minimum_destination_free_gb": 0,
                    "warn_source_free_percent": 0,
                    "io_retry_count": 0,
                },
            }
        )

    def test_varied_hierarchy_is_digested_once_and_state_folder_is_ignored(self) -> None:
        source = self.base / "Incoming"
        destination = self.base / "Master"
        self.jpeg(source / "Old Program" / "2025" / "IMG_1001.JPG")
        raw = source / "Camera Dump" / "Session A" / "IMG_1001.CR3"
        raw.parent.mkdir(parents=True)
        raw.write_bytes(b"raw-data")
        video = source / "Exports" / "Unsorted" / "CLIP_2001.MP4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"video-data")
        sidecar = source / "Ratings" / "IMG_1001.XMP"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text(
            '<x:xmpmeta xmlns:x="adobe:ns:meta/"><Rating>4</Rating></x:xmpmeta>',
            encoding="utf-8",
        )
        self.jpeg(source / ".photocard-digest" / "ignored.JPG")
        config = self.config(source, destination)
        profile = config["digest_inboxes"][0]

        first = run_digest_profile(config, profile)
        second = run_digest_profile(config, profile)

        self.assertEqual(4, first.stats.imported)
        self.assertEqual(0, first.failed)
        self.assertEqual(0, second.stats.imported)
        self.assertEqual(4, second.stats.skipped)
        self.assertEqual(
            5,
            len([path for path in source.rglob("*") if path.is_file()]),
        )
        self.assertEqual(4, first.processed)
        self.assertEqual(4, len(ImportManifest(destination).digest_items("legacy-drop")))
        self.assertEqual([], list(destination.rglob("ignored.JPG")))
        self.assertEqual(1, len(list(destination.rglob("IMG_1001.JPG"))))
        self.assertEqual(1, len(list(destination.rglob("IMG_1001.CR3"))))
        self.assertEqual(1, len(list(destination.rglob("CLIP_2001.MP4"))))
        self.assertEqual(1, len(list(destination.rglob("IMG_1001.XMP"))))
        self.assertEqual(
            1,
            len(
                list(
                    (
                        destination
                        / ".photocard-organizer"
                        / "transfer-records"
                    ).rglob("*.jsonl")
                )
            ),
        )

    def test_move_digest_requires_confirmation_and_removes_verified_source(self) -> None:
        source = self.base / "Move Inbox"
        destination = self.base / "Master"
        self.jpeg(source / "IMG_3001.JPG")
        config = self.config(source, destination, action="move", auto_digest=True)
        profile = config["digest_inboxes"][0]
        self.assertFalse(profile["auto_digest"])

        with self.assertRaisesRegex(ValueError, "explicit manual confirmation"):
            run_digest_profile(config, profile)

        self.assertTrue((source / "IMG_3001.JPG").is_file())
        result = run_digest_profile(config, profile, allow_destructive=True)

        self.assertEqual(1, result.stats.imported)
        self.assertFalse((source / "IMG_3001.JPG").exists())
        self.assertEqual(1, len(list(destination.rglob("IMG_3001.JPG"))))

    def test_different_content_name_collision_enters_conflict_queue(self) -> None:
        source = self.base / "Conflict Inbox"
        destination = self.base / "Master"
        self.jpeg(source / "Program A" / "IMG_4001.JPG", color="#355c7d")
        self.jpeg(source / "Program B" / "IMG_4001.JPG", color="#c06c84")
        config = self.config(source, destination)

        result = run_digest_profile(config, config["digest_inboxes"][0])
        items = ImportManifest(destination).digest_items("legacy-drop")

        self.assertEqual(2, result.stats.imported)
        self.assertEqual(1, result.conflicts)
        self.assertEqual(
            ["conflict", "processed"],
            sorted(str(item["status"]) for item in items),
        )
        self.assertEqual(2, len(list(destination.rglob("IMG_4001*.JPG"))))
        self.assertEqual(1, len(ImportManifest(destination).conflicts()))

    def test_copy_failure_is_retained_in_failed_queue_for_retry(self) -> None:
        source = self.base / "Failed Inbox"
        destination = self.base / "Master"
        self.jpeg(source / "IMG_5001.JPG")
        config = self.config(source, destination)

        with patch(
            "photocard.organizer.Organizer._copy_and_verify",
            side_effect=OSError("simulated destination failure"),
        ):
            result = run_digest_profile(config, config["digest_inboxes"][0])

        items = ImportManifest(destination).digest_items(
            "legacy-drop",
            status="failed",
        )
        self.assertEqual(1, result.stats.failed)
        self.assertEqual(1, len(items))
        self.assertIn("simulated destination failure", str(items[0]["error"]))
        self.assertTrue((source / "IMG_5001.JPG").is_file())
        self.assertEqual([], list(destination.rglob("IMG_5001.JPG")))

    def test_background_monitor_runs_enabled_copy_digest(self) -> None:
        source = self.base / "Automatic Inbox"
        destination = self.base / "Master"
        self.jpeg(source / "Other App" / "IMG_6001.JPG")
        config = self.config(
            source,
            destination,
            action="copy",
            auto_digest=True,
        )
        events = []
        monitor = MonitorService(config, events.append)

        with patch("photocard.monitor.time.monotonic", return_value=1.0):
            imported, errors = monitor._digest_inboxes(config)

        self.assertEqual(1, imported)
        self.assertEqual([], errors)
        self.assertEqual(1, len(list(destination.rglob("IMG_6001.JPG"))))
        self.assertTrue(
            any("digest complete" in event.message for event in events)
        )


if __name__ == "__main__":
    unittest.main()
