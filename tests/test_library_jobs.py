import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from photocard.config import normalize_config
from photocard.integrity import IntegrityCatalog, checksum
from photocard.library_jobs import build_library_job, execute_library_job
from photocard.manifest import ImportManifest


class LibraryJobsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_a = self.root / "camera-a"
        self.source_b = self.root / "camera-b"
        self.library = self.root / "central"
        self.backup = self.root / "clone"
        for path in (self.source_a, self.source_b, self.library, self.backup):
            path.mkdir()
        self.config = normalize_config({"destination_root": str(self.library)})
        self.config["safety"]["minimum_destination_free_gb"] = 0
        self.config["safety"]["minimum_destination_free_percent"] = 0

    def tearDown(self):
        self.temp.cleanup()

    def test_merge_deduplicates_content_across_different_names(self):
        (self.source_a / "IMG_0001.JPG").write_bytes(b"same")
        (self.source_b / "IMG_9999.JPG").write_bytes(b"same")
        plan = build_library_job(self.config, [self.source_a, self.source_b], backup_root=self.backup)
        self.assertEqual([entry.action for entry in plan.entries], ["Copy", "Duplicate"])
        execute_library_job(plan, local_root=self.root / "local")
        files = [path for path in self.library.rglob("*") if path.is_file() and path.suffix.lower() == ".jpg"]
        self.assertEqual(len(files), 1)
        backups = [path for path in self.backup.rglob("*") if path.is_file() and path.suffix.lower() == ".jpg"]
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), files[0].read_bytes())

    def test_different_content_same_target_goes_to_conflict_review(self):
        self.config["media_rules"]["photo"]["folder_segments"] = ["Photos"]
        (self.source_a / "IMG_0001.JPG").write_bytes(b"one")
        (self.source_b / "IMG_0001.JPG").write_bytes(b"two")
        plan = build_library_job(self.config, [self.source_a, self.source_b])
        self.assertEqual(plan.entries[1].action, "Conflict review")
        execute_library_job(plan, local_root=self.root / "local")
        self.assertEqual({entry.destination.read_bytes() for entry in plan.entries}, {b"one", b"two"})
        repeat = build_library_job(self.config, [self.source_a, self.source_b])
        self.assertTrue(all(entry.action in {"Keep", "Duplicate"} for entry in repeat.entries))

    def test_integrity_baseline_detects_changed_content_without_replacing_it(self):
        path = self.library / "file.jpg"
        path.write_bytes(b"original")
        catalog = IntegrityCatalog(self.library, self.root / "local")
        original = checksum(path)
        catalog.record(path, original, "sha256", verified=True)
        path.write_bytes(b"changed")
        result = catalog.check([path])
        self.assertEqual(result[0][1], "Changed; baseline retained")
        self.assertEqual(catalog.records()["file.jpg"]["digest"], original)

    def test_migration_retains_source_until_explicit_cleanup(self):
        (self.source_a / "old.jpg").write_bytes(b"data")
        target = self.root / "migrated"
        plan = build_library_job(self.config, [self.source_a], mode="migrate", migration_target=target)
        execute_library_job(plan, local_root=self.root / "local")
        self.assertTrue((self.source_a / "old.jpg").exists())
        self.assertTrue((target / "old.jpg").exists())

    def test_migration_copies_existing_index_and_relocates_live_paths(self):
        media = self.source_a / "old.jpg"
        media.write_bytes(b"data")
        manifest = ImportManifest(self.source_a)
        manifest.record("key", "card", "old.jpg", 4, media.stat().st_mtime_ns, media, "copy", "sha256")
        target = self.root / "migrated"
        plan = build_library_job(self.config, [self.source_a], mode="migrate", migration_target=target)
        execute_library_job(plan, local_root=str(self.root / "local"))
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(target / ".photocard-organizer/manifest.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT destination_path FROM imports").fetchone()[0], str(target / "old.jpg"))
        with closing(sqlite3.connect(manifest.path)) as db:
            self.assertEqual(db.execute("SELECT destination_path FROM imports").fetchone()[0], str(media))

    def test_destination_race_preserves_both_files(self):
        source = self.source_a / "image.jpg"
        source.write_bytes(b"original")
        plan = build_library_job(self.config, [self.source_a])
        destination = plan.entries[0].destination
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"external")
        with self.assertRaises(ValueError):
            execute_library_job(plan, local_root=self.root / "local")
        self.assertEqual(source.read_bytes(), b"original")
        self.assertEqual(destination.read_bytes(), b"external")

    def test_failed_clone_keeps_source_and_retry_reuses_primary(self):
        source = self.source_a / "image.jpg"
        source.write_bytes(b"original")
        plan = build_library_job(self.config, [self.source_a], backup_root=self.backup)
        replica = plan.entries[0].backup
        replica.parent.mkdir(parents=True)
        replica.write_bytes(b"external")
        with self.assertRaises(ValueError):
            execute_library_job(plan, local_root=self.root / "local")
        self.assertTrue(source.exists())
        replica.unlink()
        execute_library_job(plan, local_root=self.root / "local")
        self.assertEqual(replica.read_bytes(), b"original")

    def test_unique_sizes_need_no_checksum_during_preview(self):
        (self.source_a / "first.jpg").write_bytes(b"small")
        (self.source_a / "second.jpg").write_bytes(b"a larger fixture")
        with patch("photocard.library_jobs.checksum", side_effect=AssertionError("No duplicate candidate")):
            plan = build_library_job(self.config, [self.source_a])
        self.assertTrue(all(not entry.digest for entry in plan.entries))
        execute_library_job(plan, local_root=self.root / "local")
        self.assertTrue(all(entry.destination.read_bytes() == entry.source.read_bytes() for entry in plan.entries))


if __name__ == "__main__":
    unittest.main()
