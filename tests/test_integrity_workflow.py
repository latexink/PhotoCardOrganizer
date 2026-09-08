from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from photocard.config import normalize_config
from photocard.atomic_copy import same_filesystem
from photocard.discovery import folder_import_source
from photocard.integrity import IntegrityCatalog, checksum
from photocard.io_schedule import bulk_io
from photocard.manifest import ImportManifest
from photocard.models import MediaMetadata
from photocard.organizer import Organizer
from photocard.templates import SEGMENT_LABELS, render_folder


class IntegrityWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "library"
        self.root.mkdir()
        self.catalog = IntegrityCatalog(self.root, self.base / "local")

    def file(self, name, contents=b"fixture"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        return path

    def baseline(self, path):
        digest = checksum(path)
        self.catalog.record(path, digest, "sha256", verified=True)
        return digest

    def test_legacy_catalog_migrates_without_changing_backup(self):
        source = self.file("image.jpg")
        self.baseline(source)
        self.catalog.path.rename(self.catalog.legacy_path)
        before = self.catalog.legacy_path.read_bytes()
        self.assertIn("image.jpg", self.catalog.records())
        self.assertFalse(self.catalog.path.exists(), "A preview must not migrate the catalog")
        self.assertEqual(self.catalog.check([source])[0][1], "Verified")
        self.assertEqual(self.catalog.legacy_path.read_bytes(), before)
        self.assertTrue(self.catalog.path.exists())
        self.catalog._migrate()
        self.assertEqual(self.catalog.legacy_path.read_bytes(), before)
        self.assertFalse(list(self.catalog.directory.glob("*.partial")))

    def test_future_catalog_is_not_downgraded(self):
        source = self.file("image.jpg")
        self.baseline(source)
        self.catalog.path.rename(self.catalog.legacy_path)
        with closing(sqlite3.connect(self.catalog.legacy_path)) as connection:
            connection.execute("PRAGMA user_version=99")
        with self.assertRaisesRegex(ValueError, "newer application"):
            self.catalog._migrate()
        self.assertFalse(self.catalog.path.exists())

    def test_verify_reports_missing_unknown_and_changed_without_rewriting_baselines(self):
        good = self.file("good.jpg")
        changed = self.file("changed.jpg")
        missing = self.file("missing.jpg")
        unknown = self.file("unknown.jpg")
        for path in (good, changed, missing):
            self.baseline(path)
        previous = self.catalog.records()
        changed.write_bytes(b"altered contents")
        missing.unlink()
        with patch.object(self.catalog, "record", side_effect=AssertionError("Known checksums must not be rewritten")):
            rows = self.catalog.check([good, changed, missing, unknown])
        self.assertEqual([row[1] for row in rows], ["Verified", "Changed; baseline retained", "Missing", "No baseline"])
        self.assertEqual(self.catalog.records(), previous)
        local_report = self.catalog.local_path.parent / "reports" / self.catalog.last_report.name
        self.assertEqual(local_report.read_bytes(), self.catalog.last_report.read_bytes())

    def test_create_missing_does_not_hash_or_replace_existing_baselines(self):
        old = self.file("old.jpg")
        original = self.baseline(old)
        old.write_bytes(b"changed")
        new = self.file("new.jpg")
        with patch("photocard.integrity.checksum", wraps=checksum) as hash_file:
            rows = self.catalog.check([old, new], establish=True)
        self.assertEqual(hash_file.call_count, 1)
        self.assertEqual(hash_file.call_args.args[0], new)
        self.assertEqual(rows[0][1], "Baseline already exists")
        self.assertEqual(self.catalog.records()["old.jpg"]["digest"], original)
        self.assertIn("new.jpg", self.catalog.records())

    def test_cancellation_keeps_completed_report_and_baselines(self):
        paths = [self.file("first.jpg"), self.file("second.jpg")]
        cancel = threading.Event()
        rows = self.catalog.check(paths, establish=True, cancel_event=cancel,
                                  progress=lambda *_: cancel.set())
        self.assertEqual(len(rows), 1)
        self.assertTrue(self.catalog.cancelled)
        report = json.loads(self.catalog.last_report.read_text())
        self.assertTrue(report["cancelled"])
        self.assertEqual(report["files_planned"], 2)
        self.assertEqual(set(self.catalog.records()), {"first.jpg"})

    def test_read_error_is_reported_and_later_files_are_checked(self):
        bad, good = self.file("bad.jpg"), self.file("good.jpg")
        def read(path, *args, **kwargs):
            if path == bad:
                raise PermissionError("fixture access denied")
            return checksum(path, *args, **kwargs)
        with patch("photocard.integrity.checksum", side_effect=read):
            rows = self.catalog.check([bad, good], establish=True)
        self.assertTrue(rows[0][1].startswith("Error:"))
        self.assertIn("Baseline established", rows[1][1])

    def test_outside_library_path_is_not_read(self):
        outside = self.base / "outside.jpg"
        outside.write_bytes(b"outside")
        with patch("photocard.integrity.checksum", side_effect=AssertionError("Outside path was read")):
            rows = self.catalog.check([outside], establish=True)
        self.assertTrue(rows[0][1].startswith("Error:"))

    def test_relocation_keeps_checksum_and_local_audit(self):
        source = self.file("old/image.jpg")
        digest = self.baseline(source)
        destination = self.root / "new/image.jpg"
        destination.parent.mkdir()
        source.rename(destination)
        with patch("photocard.integrity.checksum", side_effect=AssertionError("Rename rehashed media")):
            self.catalog.relocate(source, destination)
            self.catalog.relocate(source, destination)
        self.assertEqual(set(self.catalog.records()), {"new/image.jpg"})
        self.assertEqual(self.catalog.records()["new/image.jpg"]["digest"], digest)
        events = [json.loads(line) for line in self.catalog.local_path.read_text().splitlines()]
        self.assertEqual(events[-1]["action"], "relocate")
        self.assertEqual(events[-1]["source"], "old/image.jpg")

    def test_metadata_cache_survives_restart_and_invalidates_on_sidecar_change(self):
        source = self.file("image.jpg")
        manifest = ImportManifest(self.root)
        metadata = MediaMetadata(datetime(2026, 9, 8), "photo", model="Fixture", rating=4)
        manifest.cache_metadata(source, metadata)
        reopened = ImportManifest(self.root)
        self.assertEqual(reopened.cached_metadata(source, "photo"), metadata)
        self.file("image.xmp", b"new rating")
        self.assertIsNone(reopened.cached_metadata(source, "photo"))
        self.assertIsNone(reopened.cached_metadata(source, "video"))

    def test_streaming_copy_hashes_source_inline_and_destination_once(self):
        source = self.file("source.jpg", b"fixture" * 1000)
        worker = Organizer(normalize_config({"destination_root": str(self.root)}))
        destination = self.root / "copied.jpg"
        with patch.object(worker, "_hash_file", wraps=worker._hash_file) as hash_file:
            digest = worker._copy_and_verify(source, destination, "sha256")
        self.assertEqual(digest, hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(source.read_bytes(), destination.read_bytes())
        self.assertEqual(hash_file.call_count, 1)
        self.assertNotEqual(hash_file.call_args.args[0], source)

    def test_metadata_cache_does_not_retain_option_dependent_group_or_place_names(self):
        source = self.file("image.jpg")
        manifest = ImportManifest(self.root)
        metadata = MediaMetadata(datetime(2026, 9, 8), "photo", model="Fixture",
                                 capture_group="Old grouping", location_name="Old geocoding")
        manifest.cache_metadata(source, metadata)
        cached = manifest.cached_metadata(source, "photo")
        self.assertEqual(cached.model, "Fixture")
        self.assertEqual(cached.capture_group, "")
        self.assertEqual(cached.location_name, "")
        self.assertEqual(metadata.capture_group, "Old grouping")

    def test_size_difference_skips_content_reads(self):
        small, large = self.file("small.jpg", b"s"), self.file("large.jpg", b"larger")
        with patch.object(Organizer, "_hash_file", side_effect=AssertionError("Unnecessary read")):
            self.assertEqual(Organizer._same_content(small, large), (False, ""))

    def test_unavailable_volume_does_not_loop_searching_for_a_parent(self):
        with patch.object(Path, "exists", return_value=False):
            with self.assertRaisesRegex(FileNotFoundError, "volume is unavailable"):
                same_filesystem(self.root / "source.jpg", self.base / "offline/destination.jpg")

    def test_queued_disk_operation_can_be_cancelled(self):
        entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
        def owner():
            with bulk_io():
                entered.set()
                release.wait(5)
        thread = threading.Thread(target=owner)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            cancelled.set()
            with self.assertRaises(InterruptedError):
                with bulk_io(cancelled):
                    self.fail("Entered another disk operation while its owner was active")
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())

    def test_day_and_iso_week_folder_names(self):
        source = self.file("image.jpg")
        metadata = MediaMetadata(datetime(2021, 1, 1, 14), "photo", make="Fixture")
        card = folder_import_source(self.root)
        self.assertEqual(render_folder([SEGMENT_LABELS[k] for k in ("Year", "Month number", "Day")], metadata, card, source), Path("2021/01/01"))
        self.assertEqual(render_folder([SEGMENT_LABELS["Year and week (ISO)"]], metadata, card, source), Path("2020-W53"))


if __name__ == "__main__":
    unittest.main()
