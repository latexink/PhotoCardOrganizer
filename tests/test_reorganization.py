from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from photocard.config import normalize_config
from photocard.discovery import folder_import_source
from photocard.manifest import ImportManifest
from photocard.integrity import IntegrityCatalog, checksum
from photocard.atomic_copy import relocate_without_overwrite
from photocard.organizer import Organizer
from photocard.reorganization import build_reorganization_plan, ReorganizationOrganizer


class ReorganizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "library"
        self.root.mkdir()
        self.config = normalize_config({
            "destination_root": str(self.root), "monitor": {"settle_seconds": 0},
            "identification": {"auto_detect": False},
            "safety": {"minimum_destination_free_percent": 0, "minimum_destination_free_gb": 0,
                       "io_retry_count": 0, "io_retry_delay_seconds": 0},
        })
        local_state = patch("photocard.integrity.local_state_directory", return_value=self.root.parent / "local")
        local_state.start()
        self.addCleanup(local_state.stop)

    def file(self, name, data=b"synthetic fixture"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def plan(self, media=None):
        return build_reorganization_plan(self.config, "Media folder only", media or {"photo", "raw", "video", "sidecar"})

    def run_plan(self, plan):
        worker = ReorganizationOrganizer(plan)
        result = worker.scan_card(plan.card, allow_destructive=True)
        return worker, result

    def test_preview_is_read_only_and_move_is_explicit(self):
        source = self.file("old/a.mp4")
        before = set(self.root.rglob("*"))
        plan = self.plan()
        self.assertEqual(before, set(self.root.rglob("*")))
        self.assertEqual(plan.entries[0].destination, self.root / "Videos/a.mp4")
        plan.config["safety"]["confirm_destructive_actions"] = False
        result = ReorganizationOrganizer(plan).scan_card(plan.card)
        self.assertEqual(result.blocked, 1)
        self.assertTrue(source.exists())

    def test_separates_media_ignores_internal_state_and_is_repeatable(self):
        for name in ("old/a.jpg", "old/a.cr2", "old/a.xmp", "old/a.mp4", "old/readme.txt",
                     ".photocard/ignore.jpg", ".photocard-organizer/ignore.jpg", "Conflicts/ignore.jpg"):
            self.file(name)
        plan = self.plan()
        self.assertEqual(len(plan.entries), 4)
        worker, result = self.run_plan(plan)
        self.assertEqual((4, 0, 0), (result.imported, result.blocked, result.failed), result.errors)
        for name in ("Photos/a.jpg", "RAW/a.cr2", "Sidecars/a.xmp", "Videos/a.mp4", "old/readme.txt"):
            self.assertTrue((self.root / name).is_file(), name)
        self.assertTrue(list((self.root / ".photocard-organizer").rglob("*.jsonl")))
        _, repeated = self.run_plan(self.plan())
        self.assertEqual((0, 4), (repeated.imported, repeated.skipped))
        worker.remove_empty_folders()
        self.assertTrue((self.root / "old").exists())

    def test_conflicts_preserve_both_and_review_records_follow_relocations(self):
        first = self.file("first/a.mp4", b"first")
        second = self.file("second/a.mp4", b"second")
        manifest = ImportManifest(self.root)
        manifest.record_conflict(source_key="previous", card_id="card", source_path=first,
                                 existing_path=first, incoming_path=second,
                                 conflict_type="filename_conflict", resolution="pending")
        worker, result = self.run_plan(self.plan())
        self.assertEqual((2, 0), (result.imported, result.failed), result.errors)
        self.assertEqual((self.root / "Videos/a.mp4").read_bytes(), b"first")
        self.assertEqual((self.root / "Conflicts/Videos/a.mp4").read_bytes(), b"second")
        rows = worker.manifest.conflicts()
        old = next(row for row in rows if row["source_key"] == "previous")
        self.assertEqual(Path(old["existing_path"]), self.root / "Videos/a.mp4")
        self.assertEqual(Path(old["incoming_path"]), self.root / "Conflicts/Videos/a.mp4")

    def test_changed_source_and_later_files_are_not_processed(self):
        original = self.file("old/a.mp4")
        plan = self.plan()
        original.write_bytes(b"changed after the preview")
        later = self.file("old/b.mp4")
        _, result = self.run_plan(plan)
        self.assertEqual(result.blocked, 1)
        self.assertTrue(original.exists())
        self.assertTrue(later.exists())
        self.assertFalse((self.root / "Videos").exists())

    def test_failed_required_log_retains_source_and_retry_reuses_copy(self):
        source = self.file("old/a.mp4")
        plan = self.plan()
        worker = ReorganizationOrganizer(plan)
        with patch.object(worker, "_record_transfer", return_value=(True, False)):
            result = worker.scan_card(plan.card, allow_destructive=True)
        self.assertEqual(result.blocked, 1)
        self.assertTrue(source.exists())
        result = worker.scan_card(plan.card, allow_destructive=True)
        self.assertEqual(result.failed, 0, result.errors)
        self.assertFalse(source.exists())
        self.assertEqual(list((self.root / "Videos").glob("*.mp4")), [self.root / "Videos/a.mp4"])
        self.assertFalse((self.root / "Conflicts").exists())

    def test_conflict_record_failure_retains_source_and_is_retried(self):
        source = self.file("old/a.mp4", b"new")
        self.file("Videos/a.mp4", b"existing")
        worker = ReorganizationOrganizer(self.plan())
        with patch.object(worker.manifest, "record_conflict", side_effect=OSError("log unavailable")):
            result = worker.scan_card(worker.plan.card, allow_destructive=True)
        self.assertEqual(result.failed, 1)
        self.assertTrue(source.exists())
        result = worker.scan_card(worker.plan.card, allow_destructive=True)
        self.assertEqual(result.failed, 0, result.errors)
        self.assertFalse(source.exists())
        self.assertEqual(len(worker.manifest.conflicts()), 1)

    def test_checksum_and_catalog_failures_do_not_delete_original(self):
        source = self.file("old/a.mp4")
        worker = ReorganizationOrganizer(self.plan())
        with patch("photocard.reorganization.same_filesystem", return_value=False), patch.object(worker, "_copy_and_verify", side_effect=OSError("verification failed")):
            result = worker.scan_card(worker.plan.card, allow_destructive=True)
        self.assertEqual(result.failed, 1)
        self.assertTrue(source.exists())
        with patch("photocard.reorganization.same_filesystem", return_value=False), patch.object(worker.manifest, "relocate_destinations", side_effect=OSError("database unavailable")):
            result = worker.scan_card(worker.plan.card, allow_destructive=True)
        self.assertTrue(source.exists())
        self.assertEqual(result.failed, 1)

    def test_ordinary_import_still_refuses_same_library(self):
        self.file("old/a.mp4")
        result = Organizer(self.config).scan_card(folder_import_source(self.root))
        self.assertEqual(result.failed, 1)

    def test_required_backup_failure_retains_original_and_resumes(self):
        source = self.file("old/a.mp4")
        backup = self.root.parent / "backup"
        self.config["replica_destinations"] = [{"name": "Backup", "root": str(backup), "required": True}]
        self.config = normalize_config(self.config)
        worker = ReorganizationOrganizer(self.plan())
        original = worker._copy_and_verify

        def fail_backup(src, dest, *args, **kwargs):
            if backup in dest.parents:
                raise OSError("Backup offline")
            return original(src, dest, *args, **kwargs)

        with patch.object(worker, "_copy_and_verify", side_effect=fail_backup):
            result = worker.scan_card(worker.plan.card, allow_destructive=True)
        self.assertEqual(result.blocked, 1)
        self.assertTrue(source.exists())
        result = worker.scan_card(worker.plan.card, allow_destructive=True)
        self.assertEqual((result.blocked, result.failed), (0, 0), result.errors)
        self.assertFalse(source.exists())
        self.assertEqual((backup / "Videos/a.mp4").read_bytes(), b"synthetic fixture")

    def test_empty_cleanup_never_removes_metadata_or_new_files(self):
        self.file("old/a.mp4")
        worker, result = self.run_plan(self.plan())
        self.assertEqual(result.failed, 0)
        worker.remove_empty_folders()
        self.assertFalse((self.root / "old").exists())
        self.assertTrue((self.root / ".photocard-organizer").is_dir())

    def test_same_filesystem_rename_does_not_copy_or_hash_media(self):
        source = self.file("old/a.mp4")
        worker = ReorganizationOrganizer(self.plan())
        with patch.object(worker, "_copy_and_verify", side_effect=AssertionError("Unexpected copy")), patch.object(
            worker, "_hash_file", side_effect=AssertionError("Unexpected hash")
        ), patch("photocard.reorganization.checksum", side_effect=AssertionError("Unexpected baseline")):
            result = worker.scan_card(worker.plan.card, allow_destructive=True)
        self.assertEqual((result.imported, result.failed, result.blocked), (1, 0, 0), result.errors)
        self.assertFalse(source.exists())
        self.assertEqual((self.root / "Videos/a.mp4").read_bytes(), b"synthetic fixture")

    def test_compatible_whole_folder_uses_one_rename(self):
        for name in ("old/a.mp4", "old/b.mp4"):
            self.file(name)
        with patch("photocard.reorganization.relocate_without_overwrite", wraps=relocate_without_overwrite) as relocate:
            _, result = self.run_plan(self.plan())
        self.assertEqual((result.imported, result.failed), (2, 0), result.errors)
        self.assertEqual(relocate.call_count, 1)
        self.assertEqual(relocate.call_args.args, (self.root / "old", self.root / "Videos"))

    def test_unknown_folder_member_forces_individual_moves(self):
        for name in ("old/a.mp4", "old/b.mp4", "old/notes.txt"):
            self.file(name)
        with patch("photocard.reorganization.relocate_without_overwrite", wraps=relocate_without_overwrite) as relocate:
            _, result = self.run_plan(self.plan())
        self.assertEqual((result.imported, result.failed), (2, 0), result.errors)
        self.assertEqual(relocate.call_count, 2)
        self.assertTrue((self.root / "old/notes.txt").exists())

    def test_deep_source_structure_can_be_flattened_without_folder_shortcut(self):
        for filename in ("a.mp4", "b.mp4"):
            self.file("/".join(["old", *(["nested"] * 12), filename]))
        _, result = self.run_plan(self.plan())
        self.assertEqual((result.imported, result.failed), (2, 0), result.errors)
        self.assertTrue((self.root / "Videos/a.mp4").exists())

    def test_recovery_refuses_a_changed_renamed_file(self):
        self.file("old/a.mp4")
        worker = ReorganizationOrganizer(self.plan())
        with patch.object(worker.manifest, "relocate_destinations", side_effect=OSError("index unavailable")):
            worker.scan_card(worker.plan.card, allow_destructive=True)
        destination = self.root / "Videos/a.mp4"
        destination.write_bytes(b"changed after relocation")
        with self.assertRaisesRegex(OSError, "changed before catalog update"):
            worker.scan_card(worker.plan.card, allow_destructive=True)
        self.assertEqual(len(worker.manifest.rename_intents()), 1)
        self.assertEqual(destination.read_bytes(), b"changed after relocation")

    def test_missing_baseline_is_optional_and_existing_checksum_follows_rename(self):
        source = self.file("old/a.mp4")
        self.config["organization"]["checksum_new_baselines"] = True
        with patch("photocard.reorganization.checksum", wraps=checksum) as hash_file:
            _, result = self.run_plan(self.plan())
        self.assertEqual(result.imported, 1)
        self.assertEqual(hash_file.call_count, 1)
        catalog = IntegrityCatalog(self.root, self.root.parent / "local")
        self.assertEqual(set(catalog.records()), {"Videos/a.mp4"})
        self.assertEqual(catalog.records()["Videos/a.mp4"]["digest"], checksum(self.root / "Videos/a.mp4"))
        self.assertFalse(source.exists())

    def test_existing_baseline_is_not_rehashed_during_move(self):
        source = self.file("old/a.mp4")
        catalog = IntegrityCatalog(self.root, self.root.parent / "local")
        digest = checksum(source)
        catalog.record(source, digest, "sha256", verified=True)
        self.config["organization"]["checksum_new_baselines"] = True
        with patch("photocard.reorganization.checksum", side_effect=AssertionError("Baseline reread")):
            _, result = self.run_plan(self.plan())
        self.assertEqual(result.imported, 1)
        self.assertEqual(catalog.records()["Videos/a.mp4"]["digest"], digest)

    def test_folder_rename_catalog_failure_is_resumable_without_recopies(self):
        for name in ("old/a.mp4", "old/b.mp4"):
            self.file(name)
        worker = ReorganizationOrganizer(self.plan())
        with patch.object(worker.manifest, "relocate_destinations", side_effect=OSError("index unavailable")):
            result = worker.scan_card(worker.plan.card, allow_destructive=True)
        self.assertEqual(result.blocked, 2)
        self.assertEqual(len(worker.manifest.rename_intents()), 2)
        self.assertTrue((self.root / "Videos/a.mp4").exists())
        with patch.object(worker, "_copy_and_verify", side_effect=AssertionError("Recovery recopied media")):
            resumed = worker.scan_card(worker.plan.card, allow_destructive=True)
        self.assertEqual((resumed.failed, resumed.blocked), (0, 0))
        self.assertEqual(worker.manifest.rename_intents(), [])

    def test_verified_copy_fallback_relocates_existing_baseline(self):
        source = self.file("old/a.mp4")
        catalog = IntegrityCatalog(self.root, self.root.parent / "local")
        digest = checksum(source)
        catalog.record(source, digest, "sha256", verified=True)
        with patch("photocard.reorganization.same_filesystem", return_value=False):
            _, result = self.run_plan(self.plan())
        self.assertEqual((result.imported, result.failed, result.blocked), (1, 0, 0), result.errors)
        self.assertEqual(set(catalog.records()), {"Videos/a.mp4"})
        self.assertEqual(catalog.records()["Videos/a.mp4"]["digest"], digest)

    def test_verified_copy_fallback_reuses_its_hash_for_missing_baseline(self):
        self.file("old/a.mp4")
        self.config["organization"]["checksum_new_baselines"] = True
        with patch("photocard.reorganization.same_filesystem", return_value=False), patch(
            "photocard.integrity.checksum", side_effect=AssertionError("Unnecessary checksum pass")
        ):
            _, result = self.run_plan(self.plan())
        self.assertEqual(result.failed, 0, result.errors)
        catalog = IntegrityCatalog(self.root, self.root.parent / "local")
        self.assertEqual(catalog.records()["Videos/a.mp4"]["digest"], checksum(self.root / "Videos/a.mp4"))
