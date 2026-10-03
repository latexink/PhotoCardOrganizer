import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from photocard.config import normalize_config
from photocard.integrity import IntegrityCatalog
from photocard.job_journal import load_job
from photocard.library_jobs import build_library_job, execute_library_job, relocate_migrated_index
from photocard.manifest import ImportManifest
from photocard.organizer import Organizer, SourceChangedError


class ReceiptRelocationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.source = self.base / "library"
        self.target = self.base / "new-library"
        self.origin = self.base / "incoming"
        self.local = self.base / "local"
        self.source.mkdir()
        self.origin.mkdir()
        self.incoming = self.origin / "clip.mp4"
        self.incoming.write_bytes(b"synthetic media")
        self.media = self.source / "clip.mp4"
        shutil.copy2(self.incoming, self.media)
        self.manifest = ImportManifest(self.source)
        self.manifest.record_library_copy(self.incoming, self.incoming.stat(), self.media)
        self.manifest.record("origin-key", "card", "clip.mp4", self.incoming.stat().st_size,
            self.incoming.stat().st_mtime_ns, self.media, "copy", "size")
        self.config = normalize_config({"destination_root": str(self.source),
            "local_history": {"directory": str(self.local)},
            "safety": {"minimum_destination_free_gb": 0, "minimum_destination_free_percent": 0}})
        self.config["media_rules"]["video"]["folder_segments"] = ["Videos"]

    def migrate(self, *, keep_originals=False, force_copy=False, interrupt=False):
        plan = build_library_job(self.config, [self.source], mode="migrate",
            migration_target=self.target, keep_originals=keep_originals)
        real_relocate = relocate_migrated_index
        def fail_after_relocate(*args, **kwargs):
            real_relocate(*args, **kwargs)
            raise RuntimeError("after index commit")
        with patch.object(Organizer, "_hash_file", side_effect=AssertionError("No hash reads")), \
                patch("photocard.library_jobs.same_filesystem", return_value=not force_copy):
            if interrupt:
                with patch("photocard.library_jobs.relocate_migrated_index", side_effect=fail_after_relocate):
                    with self.assertRaisesRegex(RuntimeError, "after index"):
                        execute_library_job(plan, local_root=self.local)
                with patch.object(Organizer, "_copy_and_verify", side_effect=AssertionError("No repeated copies")):
                    execute_library_job(load_job(plan.id, self.local), local_root=self.local)
            else:
                execute_library_job(plan, local_root=self.local)
        return plan

    def assert_target_receipt(self):
        receipt = ImportManifest(self.target).library_copy_receipt(self.incoming)
        self.assertEqual(Path(receipt["destination"]), self.target / "clip.mp4")
        self.assertTrue(Organizer._matches_receipt(self.incoming.stat(), receipt["source"]))
        self.assertTrue(Organizer._matches_receipt((self.target / "clip.mp4").stat(), receipt["copied"]))
        config = normalize_config({"destination_root": str(self.target),
            "safety": {"minimum_destination_free_gb": 0, "minimum_destination_free_percent": 0}})
        with patch.object(Organizer, "_hash_file", side_effect=AssertionError("No duplicate scan")), \
                patch.object(Organizer, "_copy_and_verify", side_effect=AssertionError("No repeated copies")):
            repeat = build_library_job(config, [self.origin])
            self.assertTrue(all(entry.action == "Keep" for entry in repeat.entries))
            execute_library_job(repeat, local_root=self.local)

    def test_folder_rename_retains_receipts_and_repeat_import(self):
        identity = self.media.stat().st_ino
        self.migrate()
        self.assertFalse(self.source.exists())
        self.assertEqual((self.target / "clip.mp4").stat().st_ino, identity)
        self.assert_target_receipt()

    def test_copy_migration_refreshes_only_target_receipts(self):
        original = self.manifest.library_copy_receipt(self.incoming)
        self.migrate(keep_originals=True)
        self.assertEqual(self.manifest.library_copy_receipt(self.incoming), original)
        self.assertNotEqual(self.media.stat().st_ino, (self.target / "clip.mp4").stat().st_ino)
        self.assert_target_receipt()

    def test_cross_filesystem_move_refreshes_receipts_without_hashes(self):
        self.migrate(force_copy=True)
        self.assertFalse(self.source.exists())
        self.assert_target_receipt()

    def test_copy_finalization_restart_keeps_relocated_receipts(self):
        self.migrate(keep_originals=True, interrupt=True)
        self.assert_target_receipt()

    def test_rename_finalization_restart_keeps_relocated_receipts(self):
        self.migrate(interrupt=True)
        self.assert_target_receipt()

    def test_changed_destination_receipt_is_not_blessed_by_migration(self):
        old = self.manifest.library_copy_receipt(self.incoming)["copied"]
        self.media.write_bytes(b"a newer edited version")
        self.migrate(keep_originals=True)
        receipt = ImportManifest(self.target).library_copy_receipt(self.incoming)
        self.assertEqual(receipt["copied"], old)
        self.assertFalse(Organizer._matches_receipt((self.target / "clip.mp4").stat(), receipt["copied"]))

    def test_pending_copy_receipt_follows_migration(self):
        self.manifest.record_pending(source_key="pending", card_id="card", source_path=self.incoming,
            destination_path=self.media, source_size=self.incoming.stat().st_size,
            source_mtime_ns=self.incoming.stat().st_mtime_ns)
        self.manifest.record_pending_copy("pending", self.incoming.stat(), self.media.stat(),
            conflict={"existing_path": str(self.media), "incoming_path": str(self.media)})
        self.migrate(keep_originals=True)
        receipt = ImportManifest(self.target).pending_copy_receipt("pending")
        self.assertTrue(Organizer._matches_receipt((self.target / "clip.mp4").stat(), receipt["destination"]))
        self.assertEqual(receipt["destination"]["conflict"]["existing_path"], str(self.target / "clip.mp4"))

    def test_media_changed_during_copy_finalization_retains_the_source(self):
        plan = build_library_job(self.config, [self.source], mode="migrate", migration_target=self.target)
        def alter_after_index(*args, **kwargs):
            relocate_migrated_index(*args, **kwargs)
            (self.target / "clip.mp4").write_bytes(b"changed during finalization")
        with patch("photocard.library_jobs.same_filesystem", return_value=False), \
                patch("photocard.library_jobs.relocate_migrated_index", side_effect=alter_after_index):
            with self.assertRaisesRegex(SourceChangedError, "Destination changed"):
                execute_library_job(plan, local_root=self.local)
        self.assertEqual(self.media.read_bytes(), b"synthetic media")
        self.assertEqual((self.target / "clip.mp4").read_bytes(), b"changed during finalization")

    def test_media_changed_after_rename_validation_does_not_refresh_receipt(self):
        plan = build_library_job(self.config, [self.source], mode="migrate", migration_target=self.target)
        def alter_before_index(*args, **kwargs):
            (self.target / "clip.mp4").write_bytes(b"new edited contents")
            relocate_migrated_index(*args, **kwargs)
        with patch("photocard.library_jobs.relocate_migrated_index", side_effect=alter_before_index):
            execute_library_job(plan, local_root=self.local)
        receipt = ImportManifest(self.target).library_copy_receipt(self.incoming)
        self.assertFalse(Organizer._matches_receipt((self.target / "clip.mp4").stat(), receipt["copied"]))

    def test_backup_changed_during_finalization_retains_source(self):
        backup = self.base / "backup"
        plan = build_library_job(self.config, [self.source], mode="migrate",
            migration_target=self.target, backup_root=backup)
        def alter_after_index(*args, **kwargs):
            relocate_migrated_index(*args, **kwargs)
            (backup / "clip.mp4").write_bytes(b"modified backup")
        with patch("photocard.library_jobs.relocate_migrated_index", side_effect=alter_after_index):
            with self.assertRaisesRegex(SourceChangedError, "Destination changed"):
                execute_library_job(plan, local_root=self.local)
        self.assertEqual(self.media.read_bytes(), b"synthetic media")

    def cross_library_plan(self):
        self.target.mkdir()
        config = normalize_config({**self.config,
            "library_destinations": [
                {"id": "main", "name": "Main", "root": str(self.source)},
                {"id": "videos", "name": "Videos", "root": str(self.target)}],
            "default_library_id": "main", "media_library_routes": {"video": "videos"}})
        return build_library_job(config, [self.source], mode="reorganize")

    def test_cross_library_move_carries_origin_mapping_and_history(self):
        plan = self.cross_library_plan()
        with patch.object(Organizer, "_hash_file", side_effect=AssertionError("No hash reads")):
            execute_library_job(plan, local_root=self.local)
        new = self.target / "Videos/clip.mp4"
        self.assertFalse(self.media.exists())
        target = ImportManifest(self.target)
        receipt = target.library_copy_receipt(self.incoming)
        self.assertEqual(Path(receipt["destination"]), new)
        self.assertTrue(Organizer._matches_receipt(new.stat(), receipt["copied"]))
        self.assertEqual(self.manifest.library_copy_receipt(self.incoming)["destination"], str(new))
        self.assertIn("origin-key", target.completed_keys("card"))
        with patch.object(Organizer, "_hash_file", side_effect=AssertionError("No duplicate scan")):
            repeat = build_library_job(plan.config, [self.origin])
        self.assertTrue(all(entry.action == "Keep" for entry in repeat.entries))

    def test_cross_library_copy_move_restart_preserves_origin_mapping(self):
        plan = self.cross_library_plan()
        with patch("photocard.library_jobs.same_filesystem", return_value=False), \
                patch.object(IntegrityCatalog, "relocate_to", side_effect=RuntimeError("before cleanup")):
            with self.assertRaisesRegex(RuntimeError, "before cleanup"):
                execute_library_job(plan, local_root=self.local)
        self.assertTrue(self.media.exists())
        with patch.object(Organizer, "_copy_and_verify", side_effect=AssertionError("No repeated copies")):
            execute_library_job(load_job(plan.id, self.local), local_root=self.local)
        self.assertFalse(self.media.exists())
        receipt = ImportManifest(self.target).library_copy_receipt(self.incoming)
        self.assertTrue(Organizer._matches_receipt((self.target / "Videos/clip.mp4").stat(), receipt["copied"]))

    def test_changed_destination_receipt_is_not_blessed_by_reorganization(self):
        old = self.manifest.library_copy_receipt(self.incoming)["copied"]
        self.media.write_bytes(b"new edited contents")
        plan = build_library_job(self.config, [self.source], mode="reorganize")
        execute_library_job(plan, local_root=self.local)
        receipt = self.manifest.library_copy_receipt(self.incoming)
        self.assertEqual(receipt["copied"], old)
        self.assertFalse(Organizer._matches_receipt((self.source / "Videos/clip.mp4").stat(), receipt["copied"]))

    def test_destination_change_during_index_updates_keeps_source_and_old_evidence(self):
        plan = self.cross_library_plan()
        original = IntegrityCatalog.relocate_to
        def update_then_modify(catalog, source, destination, target):
            original(catalog, source, destination, target)
            destination.write_bytes(b"changed while updating indexes")
        with patch("photocard.library_jobs.same_filesystem", return_value=False), \
                patch.object(IntegrityCatalog, "relocate_to", update_then_modify):
            with self.assertRaisesRegex(SourceChangedError, "index updates"):
                execute_library_job(plan, local_root=self.local)
        self.assertTrue(self.media.exists())
        receipt = ImportManifest(self.target).library_copy_receipt(self.incoming)
        self.assertFalse(Organizer._matches_receipt((self.target / "Videos/clip.mp4").stat(), receipt["copied"]))
        with self.assertRaisesRegex(SourceChangedError, "destination changed"):
            execute_library_job(load_job(plan.id, self.local), local_root=self.local)

    def test_old_source_index_without_copy_receipt_table_still_reorganizes(self):
        from contextlib import closing
        with closing(sqlite3.connect(self.manifest.path)) as connection:
            with connection:
                connection.execute("DROP TABLE library_source_receipts")
        plan = self.cross_library_plan()
        execute_library_job(plan, local_root=self.local)
        self.assertFalse(self.media.exists())
        self.assertIn("origin-key", ImportManifest(self.target).completed_keys("card"))

    def test_target_mapping_commit_can_resume_before_old_index_commit(self):
        from contextlib import contextmanager
        plan = self.cross_library_plan()
        original = ImportManifest._connection
        stopped = False
        @contextmanager
        def fail_after_target_commit(manifest):
            nonlocal stopped
            with original(manifest) as connection:
                yield connection
            if manifest.path == ImportManifest(self.target, create=False).path and not stopped:
                with original(manifest) as connection:
                    found = connection.execute("SELECT 1 FROM library_source_receipts WHERE source_path=?", (str(self.incoming),)).fetchone()
                if found:
                    stopped = True
                    raise RuntimeError("after target mapping commit")
        with patch.object(ImportManifest, "_connection", fail_after_target_commit):
            with self.assertRaisesRegex(RuntimeError, "after target mapping"):
                execute_library_job(plan, local_root=self.local)
        self.assertEqual(self.manifest.library_copy_receipt(self.incoming)["destination"], str(self.media))
        with patch.object(Organizer, "_copy_and_verify", side_effect=AssertionError("No repeated copies")):
            execute_library_job(load_job(plan.id, self.local), local_root=self.local)
        new = self.target / "Videos/clip.mp4"
        self.assertEqual(self.manifest.library_copy_receipt(self.incoming)["destination"], str(new))
        self.assertEqual(ImportManifest(self.target).library_copy_receipt(self.incoming)["destination"], str(new))
