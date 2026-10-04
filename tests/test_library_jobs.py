import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from photocard.config import normalize_config
from photocard.integrity import IntegrityCatalog, checksum
from photocard.library_jobs import build_library_job, execute_library_job
from photocard.manifest import ImportManifest
from photocard.organizer import Organizer


class LibraryJobsTests(unittest.TestCase):
    def test_folder_import_reuses_legacy_merge_receipt_and_completes_backup(self):
        from photocard.discovery import folder_import_source
        source = self.source_a / "clip.mp4"
        source.write_bytes(b"synthetic video")
        self.config["monitor"]["settle_seconds"] = 0
        self.config["local_history"]["directory"] = str(self.root / "local")
        plan = build_library_job(self.config, [self.source_a])
        execute_library_job(plan, local_root=self.root / "local")
        destination = plan.entries[0].destination
        self.config["replica_destinations"] = [{"id": "clone", "name": "Clone", "root": str(self.backup),
            "required": True, "enabled": True, "include_history": True, "conflict_policy": "block"}]
        config = normalize_config(self.config)
        with patch.object(Organizer, "_hash_file", side_effect=AssertionError("No content comparison needed")):
            organizer = Organizer(config)
            result = organizer.scan_card(folder_import_source(self.source_a))
        self.assertEqual(1, result.imported)
        self.assertEqual(0, result.failed)
        self.assertFalse((self.library / "Conflicts").exists())
        self.assertEqual(source.read_bytes(), (self.backup / destination.relative_to(self.library)).read_bytes())
        second = Organizer(config).scan_card(folder_import_source(self.source_a))
        self.assertEqual(0, second.imported)

    def test_size_only_migration_preserves_source_without_hashing(self):
        source = self.source_a / "photo.jpg"
        source.write_bytes(b"synthetic media")
        target = self.root / "size-only"
        plan = build_library_job(self.config, [self.source_a], mode="migrate",
                                 migration_target=target, migration_checksum=False, keep_originals=True)
        with patch("photocard.library_jobs.checksum", side_effect=AssertionError("Unexpected hash")), patch("photocard.organizer.Organizer._hash_file", side_effect=AssertionError("Unexpected hash")):
            records = execute_library_job(plan, local_root=self.root / "local")
        self.assertEqual(source.read_bytes(), (target / source.name).read_bytes())
        self.assertEqual(records[0]["sha256"], "")
        self.assertEqual(records[0]["verification"], "size")

    def test_changed_legacy_merge_destination_is_not_reused(self):
        from photocard.discovery import folder_import_source
        source = self.source_a / "clip.mp4"
        source.write_bytes(b"original video")
        self.config["monitor"]["settle_seconds"] = 0
        self.config["local_history"]["directory"] = str(self.root / "local")
        plan = build_library_job(self.config, [self.source_a])
        execute_library_job(plan, local_root=self.root / "local")
        destination = plan.entries[0].destination
        destination.write_bytes(b"changed destination content")
        result = Organizer(self.config).scan_card(folder_import_source(self.source_a))
        self.assertEqual(1, result.imported)
        self.assertEqual(b"changed destination content", destination.read_bytes())
        conflicts = list((self.library / "Conflicts").rglob("*.mp4"))
        self.assertEqual(1, len(conflicts))
        self.assertEqual(source.read_bytes(), conflicts[0].read_bytes())

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

    def test_merge_does_not_read_or_deduplicate_nonconflicting_names(self):
        (self.source_a / "IMG_0001.JPG").write_bytes(b"same")
        (self.source_b / "IMG_9999.JPG").write_bytes(b"same")
        with patch("photocard.library_jobs.checksum", side_effect=AssertionError("No full-library hashing")), patch.object(
            Organizer,
            "_hash_file", side_effect=AssertionError("No nonconflict hashing")
        ):
            plan = build_library_job(self.config, [self.source_a, self.source_b], backup_root=self.backup)
            self.assertEqual([entry.action for entry in plan.entries], ["Copy", "Copy"])
            execute_library_job(plan, local_root=self.root / "local")
        files = [path for path in self.library.rglob("*") if path.is_file() and path.suffix.lower() == ".jpg"]
        self.assertEqual(len(files), 2)
        backups = [path for path in self.backup.rglob("*") if path.is_file() and path.suffix.lower() == ".jpg"]
        self.assertEqual(len(backups), 2)
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
        self.assertTrue(all(entry.action == "Keep" for entry in repeat.entries))

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
        plan = build_library_job(self.config, [self.source_a], mode="migrate", migration_target=target, keep_originals=True)
        execute_library_job(plan, local_root=self.root / "local")
        self.assertTrue((self.source_a / "old.jpg").exists())
        self.assertTrue((target / "old.jpg").exists())

    def test_migration_copies_existing_index_and_relocates_live_paths(self):
        media = self.source_a / "old.jpg"
        media.write_bytes(b"data")
        manifest = ImportManifest(self.source_a)
        manifest.record("key", "card", "old.jpg", 4, media.stat().st_mtime_ns, media, "copy", "sha256")
        target = self.root / "migrated"
        plan = build_library_job(self.config, [self.source_a], mode="migrate", migration_target=target, keep_originals=True)
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
        execute_library_job(plan, local_root=self.root / "local")
        self.assertEqual(source.read_bytes(), b"original")
        self.assertEqual(destination.read_bytes(), b"external")
        self.assertIn("Conflicts", plan.entries[0].destination.parts)
        self.assertEqual(ImportManifest(self.library).conflict_count(), 1)

    def test_failed_clone_keeps_source_and_retry_reuses_primary(self):
        source = self.source_a / "image.jpg"
        source.write_bytes(b"original")
        plan = build_library_job(self.config, [self.source_a], backup_root=self.backup)
        replica = plan.entries[0].backup
        replica.parent.mkdir(parents=True)
        replica.write_bytes(b"external")
        with self.assertRaisesRegex(OSError, "backup conflicts await review"):
            execute_library_job(plan, local_root=self.root / "local")
        self.assertTrue(source.exists())
        primary_identity = plan.entries[0].destination.stat().st_ino
        replica.unlink()
        execute_library_job(plan, local_root=self.root / "local")
        self.assertEqual(replica.read_bytes(), b"original")
        self.assertEqual(plan.entries[0].destination.stat().st_ino, primary_identity)

    def test_unique_sizes_need_no_checksum_during_preview(self):
        (self.source_a / "first.jpg").write_bytes(b"small")
        (self.source_a / "second.jpg").write_bytes(b"a larger fixture")
        with patch("photocard.library_jobs.checksum", side_effect=AssertionError("No duplicate candidate")):
            plan = build_library_job(self.config, [self.source_a])
        self.assertTrue(all(not entry.digest for entry in plan.entries))
        execute_library_job(plan, local_root=self.root / "local")
        self.assertTrue(all(entry.destination.read_bytes() == entry.source.read_bytes() for entry in plan.entries))

    def test_unresolved_clone_conflict_retries_without_hashing_or_copying(self):
        source = self.source_a / "image.jpg"
        source.write_bytes(b"original")
        plan = build_library_job(self.config, [self.source_a], mode="reorganize", backup_root=self.backup)
        replica = plan.entries[0].backup
        replica.parent.mkdir(parents=True)
        replica.write_bytes(b"modified")
        with self.assertRaisesRegex(OSError, "backup conflicts await review"):
            execute_library_job(plan, local_root=self.root / "local")
        manifest = ImportManifest(self.library)
        manifest.mark_conflict_reviewed(manifest.conflicts()[0]["id"])
        with patch.object(Organizer, "_hash_file", side_effect=AssertionError("No repeated checksum reads")), \
                patch.object(Organizer, "_copy_and_verify", side_effect=AssertionError("No repeated copies")):
            with self.assertRaisesRegex(OSError, "backup conflicts await review"):
                execute_library_job(plan, local_root=self.root / "local")
        self.assertTrue(source.exists())
        self.assertEqual(manifest.conflict_count(status="all"), 1)
        replica.rename(replica.with_name("saved-older-version.jpg"))
        with patch.object(Organizer, "_hash_file", side_effect=AssertionError("No nonconflict checksum reads")):
            execute_library_job(plan, local_root=self.root / "local")
        self.assertFalse(source.exists())
        self.assertEqual(replica.read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()
