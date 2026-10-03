import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from photocard.config import load_config, normalize_config
from photocard.discovery import folder_import_source
from photocard.integrity import IntegrityCatalog, checksum
from photocard.job_journal import load_job
from photocard.library_jobs import build_library_job, execute_library_job
from photocard.manifest import ImportManifest
from photocard.organizer import Organizer, SourceChangedError


class ManualTransferPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.library = self.root / "library"
        self.backup = self.root / "backup"
        self.local = self.root / "local"
        self.source.mkdir()
        self.library.mkdir()
        self.config = normalize_config({"destination_root": str(self.library),
            "monitor": {"settle_seconds": 0}, "local_history": {"directory": str(self.local)},
            "safety": {"minimum_destination_free_gb": 0, "minimum_destination_free_percent": 0,
                       "io_retry_count": 0, "io_retry_delay_seconds": 0, "manual_error_prompt": False}})
        self.config["media_rules"]["video"]["folder_segments"] = ["Videos"]
        self.file = self.source / "clip.mp4"
        self.file.write_bytes(b"synthetic video")
        self.card = folder_import_source(self.source, action="move")

    def no_hashing(self):
        return patch.object(Organizer, "_hash_file", side_effect=AssertionError("Unexpected full-file read"))

    def test_card_move_and_new_backup_need_no_hashes(self):
        self.config["replica_destinations"] = [{"id": "backup", "name": "Backup", "root": str(self.backup),
            "enabled": True, "required": True, "include_history": True, "kind": "local"}]
        with self.no_hashing():
            result = Organizer(self.config).scan_card(self.card, allow_destructive=True)
        self.assertEqual((result.imported, result.failed, result.blocked), (1, 0, 0), result.errors)
        self.assertFalse(self.file.exists())
        self.assertEqual((self.backup / "Videos/clip.mp4").read_bytes(), b"synthetic video")
        self.assertEqual(IntegrityCatalog(self.library, self.local).records(), {})

    def test_pending_primary_retry_uses_file_identity_not_hashes(self):
        with patch.object(Organizer, "_replicate_file", return_value=([], False)), self.no_hashing():
            result = Organizer(self.config).scan_card(self.card, allow_destructive=True)
        self.assertEqual(result.blocked, 1)
        identity = (self.library / "Videos/clip.mp4").stat().st_ino
        with self.no_hashing(), patch.object(Organizer, "_copy_and_verify", side_effect=AssertionError("Unexpected recopy")):
            result = Organizer(self.config).scan_card(self.card, allow_destructive=True)
        self.assertEqual((result.imported, result.failed), (1, 0), result.errors)
        self.assertEqual((self.library / "Videos/clip.mp4").stat().st_ino, identity)
        self.assertFalse(self.file.exists())

    def test_backup_conflict_queues_locally_without_prompt_or_backup_overwrite(self):
        self.config["replica_destinations"] = [{"id": "backup", "name": "Backup", "root": str(self.backup),
            "enabled": True, "required": True, "include_history": True, "kind": "local"}]
        existing = self.backup / "Videos/clip.mp4"
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"other backup version")
        def no_pause(request):
            self.fail(f"Unexpected conflict prompt: {request.kind}")
        result = Organizer(self.config, decision_callback=no_pause).scan_card(self.card, allow_destructive=True)
        self.assertEqual(result.blocked, 1)
        self.assertTrue(self.file.exists())
        self.assertEqual(existing.read_bytes(), b"other backup version")
        conflict = ImportManifest(self.library).conflicts()[0]
        self.assertEqual(Path(conflict["existing_path"]), existing)
        self.assertTrue(Path(conflict["incoming_path"]).is_relative_to(self.library / "Conflicts"))
        manifest = ImportManifest(self.library)
        manifest.mark_conflict_reviewed(conflict["id"])
        with self.no_hashing(), patch.object(Organizer, "_copy_and_verify", side_effect=AssertionError("No extra review copies")):
            retry = Organizer(self.config, decision_callback=no_pause).scan_card(self.card, allow_destructive=True)
        self.assertEqual(retry.blocked, 1)
        self.assertTrue(self.file.exists())
        self.assertEqual(ImportManifest(self.library).conflict_count(status="all"), 1)
        self.assertEqual(len(list((self.library / "Conflicts").rglob("*.mp4"))), 1)
        existing.rename(existing.with_name("saved-older-version.mp4"))
        with self.no_hashing():
            finished = Organizer(self.config).scan_card(self.card, allow_destructive=True)
        self.assertEqual((finished.imported, finished.blocked, finished.failed), (1, 0, 0), finished.errors)
        self.assertFalse(self.file.exists())
        self.assertEqual(existing.read_bytes(), b"synthetic video")

    def test_pending_backup_conflict_follows_copied_library_migration(self):
        self.config["replica_destinations"] = [{"id": "backup", "name": "Backup", "root": str(self.backup),
            "enabled": True, "required": True, "include_history": True, "kind": "local"}]
        existing = self.backup / "Videos/clip.mp4"
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"older version")
        first = Organizer(self.config).scan_card(self.card, allow_destructive=True)
        self.assertEqual(first.blocked, 1)
        target = self.root / "migrated"
        plan = build_library_job(self.config, [self.library], mode="migrate", migration_target=target, keep_originals=True)
        execute_library_job(plan, local_root=self.local)
        moved = dict(self.config)
        moved["destination_root"] = str(target)
        moved["library_destinations"] = [dict(item, root=str(target)) for item in moved["library_destinations"]]
        moved = normalize_config(moved)
        with self.no_hashing(), patch.object(Organizer, "_copy_and_verify", side_effect=AssertionError("No repeated copies")):
            repeated = Organizer(moved).scan_card(self.card, allow_destructive=True)
        self.assertEqual((repeated.blocked, repeated.failed), (1, 0), repeated.errors)
        self.assertTrue(self.file.exists())
        self.assertEqual(ImportManifest(target).conflict_count(status="all"), 1)

    def test_source_cleanup_failure_can_retry_despite_recorded_history(self):
        original_unlink = Path.unlink
        def fail_source(path, *args, **kwargs):
            if path == self.file:
                raise PermissionError("Source is busy")
            return original_unlink(path, *args, **kwargs)
        with patch.object(Path, "unlink", fail_source), self.no_hashing():
            Organizer(self.config).scan_card(self.card, allow_destructive=True)
        self.assertTrue(self.file.exists())
        with self.no_hashing(), patch.object(Organizer, "_copy_and_verify", side_effect=AssertionError("Unexpected recopy")):
            result = Organizer(self.config).scan_card(self.card, allow_destructive=True)
        self.assertEqual(result.failed, 0, result.errors)
        self.assertFalse(self.file.exists())

    def test_missing_manifest_record_keeps_source_and_is_resumable(self):
        with patch.object(ImportManifest, "record", side_effect=OSError("Index unavailable")):
            result = Organizer(self.config).scan_card(self.card, allow_destructive=True)
        self.assertEqual(result.blocked, 1)
        self.assertTrue(self.file.exists())
        with self.no_hashing(), patch.object(Organizer, "_copy_and_verify", side_effect=AssertionError("Unexpected recopy")):
            result = Organizer(self.config).scan_card(self.card, allow_destructive=True)
        self.assertEqual(result.failed, 0, result.errors)
        self.assertFalse(self.file.exists())

    def test_legacy_pending_copy_without_receipt_is_preserved_as_conflict(self):
        target = self.library / "Videos/clip.mp4"
        target.parent.mkdir()
        target.write_bytes(self.file.read_bytes())
        value = self.file.stat()
        key = Organizer.source_key(self.card, Path(self.file.name), value.st_size, value.st_mtime_ns)
        manifest = ImportManifest(self.library)
        manifest.record_pending(source_key=key, card_id=self.card.card_id, source_path=self.file,
            destination_path=target, source_size=value.st_size, source_mtime_ns=value.st_mtime_ns)
        result = Organizer(self.config).scan_card(self.card, allow_destructive=True)
        self.assertEqual((result.imported, result.failed), (1, 0), result.errors)
        conflicts = manifest.conflicts()
        self.assertEqual(conflicts[0]["conflict_type"], "exact_duplicate")
        self.assertTrue(Path(conflicts[0]["incoming_path"]).is_relative_to(self.library / "Conflicts"))
        self.assertTrue(target.exists())

    def test_identical_name_conflicts_are_preserved_not_eliminated(self):
        second = self.root / "second"
        second.mkdir()
        (second / self.file.name).write_bytes(self.file.read_bytes())
        with self.no_hashing():
            plan = build_library_job(self.config, [self.source, second])
        self.assertEqual([e.action for e in plan.entries], ["Copy", "Conflict review"])
        execute_library_job(plan, local_root=self.local)
        records = ImportManifest(self.library).conflicts()
        self.assertEqual(records[0]["conflict_type"], "exact_duplicate")
        self.assertEqual(len(list(self.library.rglob("*.mp4"))), 2)
        with self.no_hashing():
            repeat = build_library_job(self.config, [self.source, second])
        self.assertTrue(all(e.action == "Keep" for e in repeat.entries))

    def test_copy_before_log_interruption_resumes_without_content_reads(self):
        plan = build_library_job(self.config, [self.source])
        with patch("photocard.library_jobs.append_record", side_effect=OSError("History unavailable")):
            with self.assertRaises(OSError):
                execute_library_job(plan, local_root=self.local)
        with self.no_hashing(), patch.object(Organizer, "_copy_and_verify", side_effect=AssertionError("Unexpected recopy")):
            execute_library_job(load_job(plan.id, self.local), local_root=self.local)
        self.assertTrue(self.file.exists())

    def test_same_disk_reorganization_uses_rename_and_resumes_after_log_failure(self):
        original = self.library / "old.mp4"
        original.write_bytes(b"keep this data")
        identity = original.stat().st_ino
        plan = build_library_job(self.config, [self.library], mode="reorganize")
        with patch.object(Organizer, "_copy_and_verify", side_effect=AssertionError("Rename should not copy")), self.no_hashing():
            with patch("photocard.library_jobs.append_record", side_effect=OSError("History unavailable")):
                with self.assertRaises(OSError):
                    execute_library_job(plan, local_root=self.local)
            execute_library_job(load_job(plan.id, self.local), local_root=self.local)
        self.assertFalse(original.exists())
        self.assertEqual((self.library / "Videos/old.mp4").stat().st_ino, identity)

    def test_changed_completed_copy_is_not_trusted_on_retry(self):
        plan = build_library_job(self.config, [self.source])
        with patch("photocard.library_jobs.append_record", side_effect=OSError("History unavailable")):
            with self.assertRaises(OSError):
                execute_library_job(plan, local_root=self.local)
        plan.entries[0].destination.write_bytes(b"external replacement")
        with self.no_hashing(), self.assertRaises(SourceChangedError):
            execute_library_job(load_job(plan.id, self.local), local_root=self.local)
        self.assertTrue(self.file.exists())

    def test_schema_seven_checksum_preferences_are_backed_up_and_disabled(self):
        path = self.root / "config.json"
        original = {"schema": 7, "destination_root": str(self.library),
            "safety": {"copy_verification": "sha512", "move_checksum_algorithm": "sha256", "replica_verification": "blake2b"},
            "organization": {"checksum_new_baselines": True}}
        path.write_text(json.dumps(original), encoding="utf-8")
        config, _ = load_config(path)
        for key in ("copy_verification", "move_checksum_algorithm", "replica_verification"):
            self.assertEqual(config["safety"][key], "size")
        self.assertFalse(config["organization"]["checksum_new_baselines"])
        backups = list((self.root / "backups").glob("config.schema-7.*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8")), original)

    def test_new_migration_ignores_legacy_transfer_checksum_option(self):
        plan = build_library_job(self.config, [self.source], mode="migrate", migration_target=self.root / "moved",
                                 migration_checksum=True)
        self.assertFalse(plan.migration_checksum)
        with self.no_hashing(), patch("photocard.library_jobs.checksum", side_effect=AssertionError("Unexpected checksum")):
            execute_library_job(plan, local_root=self.local)

    def test_saved_baseline_follows_media_between_libraries_without_hashing(self):
        catalog = IntegrityCatalog(self.source, self.local)
        digest = checksum(self.file)
        catalog.record(self.file, digest, "sha256", verified=True)
        plan = build_library_job(self.config, [self.source], mode="reorganize")
        with self.no_hashing(), patch("photocard.integrity.checksum", side_effect=AssertionError("Unexpected checksum")):
            execute_library_job(plan, local_root=self.local)
        self.assertEqual(catalog.records(), {})
        destination = IntegrityCatalog(self.library, self.local).records()["Videos/clip.mp4"]
        self.assertEqual(destination["digest"], digest)
        self.assertEqual(destination["status"], "verification pending")

    def test_repeated_clone_job_uses_receipts_without_hashing(self):
        plan = build_library_job(self.config, [self.source], backup_root=self.backup)
        with self.no_hashing():
            execute_library_job(plan, local_root=self.local)
            repeat = build_library_job(self.config, [self.source], backup_root=self.backup)
            execute_library_job(repeat, local_root=self.local)
        self.assertTrue(self.file.exists())

    def test_reorganization_moves_name_conflicts_instead_of_copying_them(self):
        existing = self.library / "Videos/clip.mp4"
        existing.parent.mkdir()
        existing.write_bytes(b"different capture")
        incoming = self.library / "old/clip.mp4"
        incoming.parent.mkdir()
        incoming.write_bytes(b"incoming capture")
        identity = incoming.stat().st_ino
        plan = build_library_job(self.config, [self.library], mode="reorganize")
        with patch.object(Organizer, "_copy_and_verify", side_effect=AssertionError("Same-disk conflict should rename")):
            execute_library_job(plan, local_root=self.local)
        self.assertFalse(incoming.exists())
        conflicts = ImportManifest(self.library).conflicts()
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(Path(conflicts[0]["incoming_path"]).stat().st_ino, identity)
        self.assertEqual(existing.read_bytes(), b"different capture")

    def test_destination_created_during_copy_is_redirected_to_review(self):
        plan = build_library_job(self.config, [self.source])
        original_target = plan.entries[0].destination
        original_copy = Organizer._copy_and_verify
        def concurrent_copy(engine, source, destination, *args, **kwargs):
            if destination == original_target:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"concurrent capture")
            return original_copy(engine, source, destination, *args, **kwargs)
        with patch.object(Organizer, "_copy_and_verify", concurrent_copy):
            execute_library_job(plan, local_root=self.local)
        self.assertEqual(original_target.read_bytes(), b"concurrent capture")
        conflict = ImportManifest(self.library).conflicts()[0]
        self.assertEqual(Path(conflict["incoming_path"]).read_bytes(), self.file.read_bytes())
        execute_library_job(load_job(plan.id, self.local), local_root=self.local)
        self.assertEqual(ImportManifest(self.library).conflict_count(), 1)

    def test_manual_backup_compare_reports_differences_and_missing_files(self):
        (self.library / "same.mp4").write_bytes(b"same")
        (self.library / "different.mp4").write_bytes(b"good")
        (self.library / "only-local.mp4").write_bytes(b"local")
        self.backup.mkdir()
        (self.backup / "same.mp4").write_bytes(b"same")
        (self.backup / "different.mp4").write_bytes(b"evil")
        (self.backup / "only-backup.mp4").write_bytes(b"backup")
        catalog = IntegrityCatalog(self.library, self.local)
        statuses = {Path(path).name: status for path, status in catalog.compare_backup(self.config, self.backup)}
        self.assertEqual(statuses, {"same.mp4": "Matches backup", "different.mp4": "Different from backup",
                                   "only-local.mp4": "Missing from backup", "only-backup.mp4": "Missing from library"})
        self.assertEqual(catalog.records(), {})
        self.assertTrue(catalog.last_report.exists())
        self.assertEqual((self.library / "different.mp4").read_bytes(), b"good")

    def test_unavailable_backup_is_not_created_by_manual_comparison(self):
        with self.assertRaisesRegex(ValueError, "unavailable"):
            IntegrityCatalog(self.library, self.local).compare_backup(self.config, self.backup)
        self.assertFalse(self.backup.exists())

    def test_manual_comparison_cancellation_preserves_media_and_baselines(self):
        import threading
        source = self.library / "clip.mp4"
        source.write_bytes(b"original")
        self.backup.mkdir()
        (self.backup / "clip.mp4").write_bytes(b"original")
        cancel = threading.Event()
        catalog = IntegrityCatalog(self.library, self.local)
        def interrupted(*args, **kwargs):
            cancel.set()
            raise InterruptedError("cancelled")
        with patch("photocard.integrity.checksum", side_effect=interrupted):
            rows = catalog.compare_backup(self.config, self.backup, cancel_event=cancel)
        self.assertEqual(rows, [])
        self.assertTrue(catalog.cancelled)
        self.assertTrue(catalog.last_report.exists())
        self.assertEqual(catalog.records(), {})
        self.assertEqual(source.read_bytes(), b"original")


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main()
