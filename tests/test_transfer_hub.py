from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from photocard.config import normalize_config
from photocard.discovery import folder_import_source, write_card_identity
from photocard.organizer import Organizer
from photocard.transfer_hub import (
    catch_sources,
    producer_channel,
    publish_library,
    receipt_status,
    source_entries,
    write_digestion_receipts,
)


class TransferHubWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def jpeg(path: Path, captured: str = "2026:07:12 10:11:12") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (18, 12), "#596b82")
        exif = Image.Exif()
        exif[36867] = captured
        exif[271] = "Canon"
        exif[272] = "Canon EOS R5"
        image.save(path, exif=exif)
        timestamp = datetime(2026, 7, 12, 10, 11, 12).timestamp()
        os.utime(path, (timestamp, timestamp))

    def config(
        self,
        destination: Path,
        *,
        instance_id: str,
        instance_name: str,
        hubs: list[dict] | None = None,
        card_root: Path | None = None,
    ) -> dict:
        return normalize_config(
            {
                "destination_root": str(destination),
                "instance": {
                    "id": instance_id,
                    "name": instance_name,
                    "library_id": f"{instance_id}-library",
                },
                "identification": {
                    "auto_detect": False,
                    "configured_roots": [str(card_root)] if card_root else [],
                },
                "transfer_hubs": hubs or [],
                "monitor": {"settle_seconds": 0},
                "safety": {
                    "minimum_destination_free_percent": 0,
                    "minimum_destination_free_gb": 0,
                    "warn_source_free_percent": 0,
                },
            }
        )

    def test_fresh_laptop_session_is_caught_once_by_existing_desktop_library(self) -> None:
        hub_root = self.base / "Shared Transfer Hub"
        hub_root.mkdir()
        laptop_library = self.base / "Laptop Library"
        desktop_library = self.base / "Existing Desktop Master"
        desktop_library.mkdir()
        (desktop_library / "existing-note.txt").write_text(
            "pre-existing library", encoding="utf-8"
        )
        card = self.base / "CARD"
        card.mkdir()
        (card / "DCIM").mkdir()
        laptop_hub = {
            "id": "family-hub",
            "name": "Family Transfer Hub",
            "root": str(hub_root),
            "role": "publish",
            "producer_channel": "Travel Laptop",
            "required": True,
        }
        laptop = self.config(
            laptop_library,
            instance_id="new-laptop",
            instance_name="New Laptop",
            hubs=[laptop_hub],
            card_root=card,
        )
        identity = laptop["identification"]
        write_card_identity(
            card,
            identity["folder_name"],
            identity["identity_filename"],
            identity["history_folder_name"],
            "Travel Camera",
            "travel-camera",
            "copy",
            ["DCIM"],
        )
        self.jpeg(card / "DCIM" / "IMG_1001.JPG")

        laptop_results, laptop_errors = Organizer(laptop).scan_all()

        self.assertEqual([], laptop_errors)
        self.assertEqual(1, laptop_results[0].imported)
        channel_root = (
            hub_root
            / "Producers"
            / producer_channel(laptop, laptop["transfer_hubs"][0])
        )
        hub_media = list(channel_root.rglob("IMG_1001.JPG"))
        self.assertEqual(1, len(hub_media))
        self.assertTrue(
            list(
                (
                    channel_root
                    / ".photocard-organizer"
                    / "transfer-records"
                ).rglob("*.jsonl")
            )
        )

        desktop_hub = {
            "id": "family-hub",
            "name": "Family Transfer Hub",
            "root": str(hub_root),
            "role": "catch",
            "auto_catch": True,
            "write_receipts": True,
        }
        desktop = self.config(
            desktop_library,
            instance_id="desktop-main",
            instance_name="Desktop Main",
            hubs=[desktop_hub],
        )
        hub = desktop["transfer_hubs"][0]
        sources, source_errors = catch_sources(desktop, hub)
        self.assertEqual([], source_errors)
        self.assertEqual(1, len(sources))
        desktop_organizer = Organizer(desktop)
        entries = source_entries(desktop_organizer, sources[0])

        first = desktop_organizer.scan_card(sources[0])
        receipt_count, receipt_errors = write_digestion_receipts(
            desktop,
            hub,
            sources[0],
            desktop_organizer.manifest,
            entries,
        )
        second = Organizer(desktop).scan_card(sources[0])
        repeated_receipts, repeated_errors = write_digestion_receipts(
            desktop,
            hub,
            sources[0],
            desktop_organizer.manifest,
            entries,
        )

        self.assertEqual(1, first.imported)
        self.assertEqual(0, second.imported)
        self.assertEqual(1, second.skipped)
        self.assertEqual(1, receipt_count)
        self.assertEqual([], receipt_errors)
        self.assertEqual(0, repeated_receipts)
        self.assertEqual([], repeated_errors)
        self.assertTrue((desktop_library / "existing-note.txt").is_file())
        self.assertEqual(1, len(list(desktop_library.rglob("IMG_1001.JPG"))))
        self.assertEqual(1, len(list((hub_root / "Receipts").rglob("*.jsonl"))))
        self.assertEqual(
            1,
            len(
                list(
                    (
                        desktop_library
                        / ".photocard-organizer"
                        / "hub-receipts"
                    ).rglob("*.jsonl")
                )
            ),
        )

    def test_publish_now_backfills_library_imported_while_hub_was_offline(self) -> None:
        laptop_library = self.base / "Offline Laptop Library"
        source = self.base / "folder-import"
        self.jpeg(source / "IMG_2001.JPG")
        laptop = self.config(
            laptop_library,
            instance_id="field-laptop",
            instance_name="Field Laptop",
        )
        folder = folder_import_source(source, stable_id="field-import")
        imported = Organizer(laptop).scan_card(folder)
        self.assertEqual(1, imported.imported)

        hub_root = self.base / "Synced Google Drive Hub"
        hub_root.mkdir()
        laptop["transfer_hubs"] = [
            {
                "id": "cloud-hub",
                "name": "Cloud Transfer Hub",
                "root": str(hub_root),
                "role": "publish",
                "producer_channel": "Field Laptop",
                "required": False,
                "enabled": True,
            }
        ]
        laptop = normalize_config(laptop)

        published = publish_library(laptop, laptop["transfer_hubs"][0])
        repeated = publish_library(laptop, laptop["transfer_hubs"][0])

        self.assertEqual(1, published.published)
        self.assertEqual(0, published.failed)
        self.assertEqual(0, repeated.published)
        self.assertEqual(1, repeated.reused)
        self.assertEqual(
            1,
            len(
                list(
                    (
                        hub_root / "Producers" / "Field Laptop"
                    ).rglob("IMG_2001.JPG")
                )
            ),
        )
        self.assertTrue(published.session_path and published.session_path.is_file())
        self.assertTrue(published.checksum_path and published.checksum_path.is_file())

    def test_publish_conflict_never_overwrites_without_archiving(self) -> None:
        library = self.base / "publisher-library"
        hub_root = self.base / "hub"
        hub_root.mkdir()
        source = library / "Photos" / "IMG_4001.JPG"
        self.jpeg(source)
        hub_definition = {
            "id": "conflict-hub",
            "name": "Conflict Hub",
            "root": str(hub_root),
            "role": "publish",
            "producer_channel": "Laptop",
            "conflict_policy": "block",
        }
        config = self.config(
            library,
            instance_id="laptop",
            instance_name="Laptop",
            hubs=[hub_definition],
        )
        destination = (
            hub_root
            / "Producers"
            / "Laptop"
            / "Photos"
            / "IMG_4001.JPG"
        )
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"older-different-content")

        blocked = publish_library(config, config["transfer_hubs"][0])

        self.assertEqual(1, blocked.failed)
        self.assertEqual(b"older-different-content", destination.read_bytes())
        self.assertTrue(source.is_file())

        config["transfer_hubs"][0]["conflict_policy"] = "archive_and_replace"
        replaced = publish_library(config, config["transfer_hubs"][0])

        self.assertEqual(1, replaced.published)
        self.assertEqual(source.read_bytes(), destination.read_bytes())
        archived = list(
            (
                hub_root
                / "Producers"
                / "Laptop"
                / ".photocard-organizer"
                / "conflicts"
            ).rglob("IMG_4001.JPG")
        )
        self.assertEqual(1, len(archived))
        self.assertEqual(b"older-different-content", archived[0].read_bytes())

    def test_publish_source_change_does_not_commit_partial_content(self) -> None:
        library = self.base / "publisher-library"
        hub_root = self.base / "usb-hub"
        hub_root.mkdir()
        source = library / "Photos" / "IMG_5001.JPG"
        self.jpeg(source)
        hub_definition = {
            "id": "usb-hub",
            "name": "USB Transfer Hub",
            "root": str(hub_root),
            "role": "publish",
            "producer_channel": "Laptop",
        }
        config = self.config(
            library,
            instance_id="laptop",
            instance_name="Laptop",
            hubs=[hub_definition],
        )
        real_copy = shutil.copy2

        def copy_then_change(source_path, temporary_path):
            result = real_copy(source_path, temporary_path)
            Path(source_path).write_bytes(b"changed-during-publication")
            return result

        with patch(
            "photocard.transfer_hub.shutil.copy2",
            side_effect=copy_then_change,
        ):
            result = publish_library(config, config["transfer_hubs"][0])

        producer = hub_root / "Producers" / "Laptop"
        self.assertEqual(0, result.published)
        self.assertEqual(1, result.failed)
        self.assertFalse((producer / "Photos" / source.name).exists())
        self.assertEqual([], list(producer.rglob(".pco-*.partial")))

    def test_receipt_status_ignores_an_unavailable_receipt_file(self) -> None:
        library = self.base / "publisher-library"
        library.mkdir()
        hub_root = self.base / "usb-hub"
        receipt_root = hub_root / "Receipts" / "Desktop" / "Laptop"
        receipt_root.mkdir(parents=True)
        available = receipt_root / "available.jsonl"
        unavailable = receipt_root / "unavailable.jsonl"
        available.write_text("{}\n", encoding="utf-8")
        unavailable.write_text("{}\n", encoding="utf-8")
        hub_definition = {
            "id": "usb-hub",
            "name": "USB Transfer Hub",
            "root": str(hub_root),
            "role": "publish",
            "producer_channel": "Laptop",
        }
        config = self.config(
            library,
            instance_id="laptop",
            instance_name="Laptop",
            hubs=[hub_definition],
        )
        real_stat = Path.stat

        def intermittent_stat(path, *args, **kwargs):
            if Path(path) == unavailable:
                raise OSError("USB drive entry became unavailable")
            return real_stat(path, *args, **kwargs)

        with patch.object(Path, "stat", intermittent_stat):
            count, latest = receipt_status(
                config, config["transfer_hubs"][0]
            )

        self.assertEqual(1, count)
        self.assertTrue(latest)


if __name__ == "__main__":
    unittest.main()
