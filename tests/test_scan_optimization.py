from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock, patch

from photocard.atomic_copy import create_partial_file
from photocard.config import normalize_config
from photocard.discovery import folder_import_source
from photocard.library_tools import LibraryItem, build_capture_sets, filter_captures, scan_library
from photocard.manifest import ImportManifest
from photocard.metadata import extract_metadata
from photocard.models import ImportStats
from photocard.monitor import MonitorService
from photocard.organizer import Organizer
from photocard.source_monitor import SourceDiscovery


class ScanOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = normalize_config({"destination_root": str(self.root / "library"),
                                        "monitor": {"settle_seconds": 0},
                                        "identification": {"auto_detect": False}})

    def test_defaults_and_saved_poll_interval(self):
        self.assertEqual(self.config["monitor"]["poll_seconds"], 30)
        saved = normalize_config({"monitor": {"poll_seconds": 17}})
        self.assertEqual(saved["monitor"]["poll_seconds"], 17)
        for invalid in (0, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                normalize_config({"monitor": {"poll_seconds": invalid}})

    def test_completed_thousand_file_scan_uses_one_connection_and_no_metadata(self):
        source = self.root / "source"
        source.mkdir()
        card = folder_import_source(source)
        organizer = Organizer(self.config)
        self.config["organization"]["long_exposure_brackets"]["enabled"] = True
        self.config["media_rules"]["photo"]["folder_segments"] = ["{capture_group}"]
        with organizer.manifest.processing_session():
            for number in range(1000):
                path = source / f"IMG_{number:04}.jpg"
                path.write_bytes(b"fixture")
                stat = path.stat()
                key = organizer.source_key(card, path.relative_to(source), stat.st_size, stat.st_mtime_ns)
                organizer.manifest.record(key, card.card_id, str(path.relative_to(source)), stat.st_size,
                                          stat.st_mtime_ns, str(self.root / "library" / path.name), "copy", "sha256")
        with patch.object(organizer.manifest, "_connect", wraps=organizer.manifest._connect) as connect, \
                patch("photocard.organizer.extract_metadata") as metadata:
            result = organizer.scan_card(card)
        self.assertEqual((1000, 1000, 0), (result.discovered, result.skipped, result.failed))
        self.assertEqual(connect.call_count, 1)
        metadata.assert_not_called()

    def test_session_commits_each_write_and_closes_on_error(self):
        manifest = ImportManifest(self.root)
        with self.assertRaises(RuntimeError):
            with manifest.processing_session():
                manifest.record("key", "card", "a.jpg", 1, 1, str(self.root / "a.jpg"), "copy", "sha256")
                self.assertTrue(ImportManifest(self.root, create=False).contains("key"))
                raise RuntimeError("synthetic interruption")
        self.assertIsNone(manifest._session.connection)
        manifest.path.rename(manifest.path.with_suffix(".saved"))

    def test_metadata_cache_is_isolated_and_invalidated_by_sidecar_changes(self):
        source = self.root / "clip.mp4"
        source.write_bytes(b"video fixture")
        with patch("photocard.metadata._read_xmp", return_value={"rating": 2}) as read:
            first = extract_metadata(source, "video", use_exiftool=False)
            first.rating = 5
            second = extract_metadata(source, "video", use_exiftool=False)
            self.assertEqual(second.rating, 2)
            self.assertEqual(read.call_count, 1)
            source.with_suffix(".xmp").write_text("changed", encoding="utf-8")
            extract_metadata(source, "video", use_exiftool=False)
            self.assertEqual(read.call_count, 2)
            source.write_bytes(b"different video fixture")
            extract_metadata(source, "video", use_exiftool=False)
            self.assertEqual(read.call_count, 3)

    def test_catalog_uses_same_video_capture_date_as_import(self):
        source = self.root / "clip.mp4"
        source.write_bytes(b"fixture")
        with patch("photocard.metadata.shutil.which", return_value="exiftool"), \
                patch("photocard.metadata._read_exiftool", return_value={"date": "2024-03-04T12:00:00Z"}):
            metadata = extract_metadata(source, "video")
            catalog = scan_library(self.root, self.config["media_rules"])
        self.assertEqual(catalog[0].captured_at, metadata.captured_at)
        self.assertIsNone(metadata.captured_at.tzinfo)
        self.assertEqual(metadata.captured_at.year, 2024)

    def test_discovery_avoids_drive_probes_until_mount_change_or_expiry(self):
        discovery = SourceDiscovery()
        with patch.object(discovery, "mount_token", return_value="initial") as token, \
                patch("photocard.source_monitor.time.monotonic", return_value=100) as now, \
                patch("photocard.source_monitor.discover_cards", return_value=([], [])) as discover:
            discovery.poll(self.config)
            discovery.poll(self.config)
            self.assertEqual(discover.call_count, 1)
            token.return_value = "new drive"
            discovery.poll(self.config)
            self.assertEqual(discover.call_count, 2)
            discovery.poll(self.config, force=True)
            self.assertEqual(discover.call_count, 3)
            now.return_value = 500
            discovery.poll(self.config)
            self.assertEqual(discover.call_count, 4)

    def test_idle_scans_back_off_and_new_card_does_not_rescan_unchanged_card(self):
        card = folder_import_source(self.root)
        service = MonitorService(self.config, lambda event: None)
        with patch.object(service._discovery, "mount_token", return_value="drive") as token, \
                patch("photocard.source_monitor.discover_cards", return_value=([card], [])) as discover, \
                patch("photocard.monitor.time.monotonic", return_value=100) as now, \
                patch("photocard.monitor.Organizer") as factory:
            factory.return_value.scan_cards.side_effect = lambda cards, **kwargs: [ImportStats(card_id=cards[0].card_id)]
            service._scan_connected_cards(self.config)
            service._scan_connected_cards(self.config)
            self.assertEqual(factory.return_value.scan_cards.call_count, 1)
            (self.root / "new-card").mkdir()
            other = folder_import_source(self.root / "new-card")
            token.return_value = "new drive"
            discover.return_value = ([card, other], [])
            service._scan_connected_cards(self.config)
            self.assertEqual(factory.return_value.scan_cards.call_count, 2)
            self.assertEqual(factory.return_value.scan_cards.call_args.args[0], [other])
            service.scan_now()
            service._scan_connected_cards(self.config)
            self.assertEqual(factory.return_value.scan_cards.call_count, 4)

    def test_partial_cleanup_scans_each_destination_only_once(self):
        with patch("photocard.atomic_copy.cleanup_abandoned_partials") as cleanup:
            for _ in range(10):
                create_partial_file(self.root)
        self.assertEqual(cleanup.call_count, 1)

    def test_discovery_error_details_reach_activity_without_initializing_destination(self):
        events = []
        service = MonitorService(self.config, events.append)
        with patch.object(service._discovery, "poll", return_value=([], ["Card identity cannot be read"], True)), \
                patch("photocard.monitor.Organizer") as factory:
            service._scan_connected_cards(self.config)
        factory.assert_not_called()
        self.assertEqual(events[0].level, "error")
        self.assertEqual(events[0].message, "Card identity cannot be read")

    def test_type_and_date_filters_keep_only_requested_content(self):
        items = [LibraryItem(self.root / f"a.{ext}", Path(f"a.{ext}"), kind, datetime(2024, 3, 4))
                 for ext, kind in (("jpg", "photo"), ("mp4", "video"), ("xmp", "sidecar"))]
        captures = build_capture_sets(items)
        filtered = filter_captures(captures, media_kind="video", start=date(2024, 3, 4), end=date(2024, 3, 4))
        self.assertEqual({item.media_kind for item in filtered[0].items}, {"video", "sidecar"})
        filtered = filter_captures(captures, media_kind="video", include_sidecars=False)
        self.assertEqual({item.media_kind for item in filtered[0].items}, {"video"})
        self.assertEqual(filter_captures(captures, start=date(2024, 3, 5)), [])
        with self.assertRaises(ValueError):
            filter_captures(captures, start=date(2024, 3, 5), end=date(2024, 3, 4))
