import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from photocard.config import normalize_config
from photocard.job_journal import JobJournal, load_job, pending_jobs, mark_activated
from photocard.library_jobs import build_library_job, execute_library_job
from photocard.manifest import ImportManifest
from photocard.organizer import Organizer, SourceChangedError


class JobRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.target = self.root / "target"
        self.local = self.root / "local"
        self.source.mkdir()
        self.config = normalize_config({"destination_root": str(self.source),
            "safety": {"minimum_destination_free_gb": 0, "minimum_destination_free_percent": 0}})
        (self.source / "a.mp4").write_bytes(b"first")
        (self.source / "b.mp4").write_bytes(b"second file")

    def migration(self):
        return build_library_job(self.config, [self.source], mode="migrate",
                                 migration_target=self.target, migration_checksum=False, keep_originals=True)

    def move_plan(self):
        return build_library_job(self.config, [self.source], mode="migrate", migration_target=self.target)

    def test_merge_restart_accepts_existing_destination_media(self):
        self.target.mkdir()
        (self.target / "existing.mp4").write_bytes(b"already in library")
        config = normalize_config({"destination_root": str(self.target),
            "safety": {"minimum_destination_free_gb": 0, "minimum_destination_free_percent": 0}})
        plan = build_library_job(config, [self.source], mode="merge")
        self.interrupt(plan)
        execute_library_job(load_job(plan.id, self.local), local_root=self.local)
        self.assertEqual((self.target / "existing.mp4").read_bytes(), b"already in library")
        self.assertTrue((self.source / "a.mp4").exists())

    def test_same_filesystem_migration_renames_without_content_reads(self):
        plan = self.move_plan()
        self.assertFalse(plan.keep_originals)
        with patch.object(Organizer, "_copy_and_verify", side_effect=AssertionError("No copying")), \
                patch("photocard.library_jobs.checksum", side_effect=AssertionError("No hashing")):
            execute_library_job(plan, local_root=self.local)
            execute_library_job(load_job(plan.id, self.local), local_root=self.local)
        self.assertFalse(self.source.exists())
        self.assertEqual((self.target / "a.mp4").read_bytes(), b"first")

    def test_rename_interruption_resumes_without_recreating_source(self):
        plan = self.move_plan()
        with patch("photocard.library_jobs.relocate_migrated_index", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                execute_library_job(plan, local_root=self.local)
        with patch("photocard.job_journal.local_state_directory", return_value=self.local):
            with self.assertRaisesRegex(ValueError, "unfinished migration"):
                Organizer(self.config)
        self.assertFalse(self.source.exists())
        execute_library_job(load_job(plan.id, self.local), local_root=self.local)
        self.assertFalse(self.source.exists())

    def test_cross_filesystem_move_restarts_after_cleanup_interruption(self):
        plan = self.move_plan()
        cancel = threading.Event()
        def progress(*args):
            if not (self.source / "a.mp4").exists():
                cancel.set()
        with patch("photocard.library_jobs.same_filesystem", return_value=False):
            with self.assertRaises(InterruptedError):
                execute_library_job(plan, local_root=self.local, cancel_event=cancel, progress=progress)
        self.assertTrue((self.source / "b.mp4").exists())
        with patch.object(Organizer, "_copy_and_verify", side_effect=AssertionError("No recopy")):
            execute_library_job(load_job(plan.id, self.local), local_root=self.local)
            execute_library_job(load_job(plan.id, self.local), local_root=self.local)
        self.assertFalse(self.source.exists())
        self.assertEqual((self.target / "b.mp4").read_bytes(), b"second file")

    def test_cleanup_never_deletes_a_recreated_source_file(self):
        plan = self.move_plan()
        cancel = threading.Event()
        def progress(*args):
            if not (self.source / "a.mp4").exists():
                cancel.set()
        with patch("photocard.library_jobs.same_filesystem", return_value=False):
            with self.assertRaises(InterruptedError):
                execute_library_job(plan, local_root=self.local, cancel_event=cancel, progress=progress)
        (self.source / "a.mp4").write_bytes(b"new arrival")
        with self.assertRaisesRegex(SourceChangedError, "Source changed"):
            execute_library_job(load_job(plan.id, self.local), local_root=self.local)
        self.assertEqual((self.source / "a.mp4").read_bytes(), b"new arrival")

    def interrupt(self, plan):
        cancel = threading.Event()
        with self.assertRaises(InterruptedError):
            execute_library_job(plan, local_root=self.local, cancel_event=cancel,
                                progress=lambda *args: cancel.set())

    def test_restart_resumes_without_copying_or_hashing_completed_media(self):
        plan = self.migration()
        self.interrupt(plan)
        self.assertEqual(len(list(self.target.glob("*.mp4"))), 1)
        self.assertEqual(pending_jobs(self.source, self.local)[0]["id"], plan.id)
        resumed = load_job(plan.id, self.local)
        original_copy = Organizer._copy_and_verify
        copies = []
        def copy(engine, source, *args, **kwargs):
            copies.append(source.name)
            return original_copy(engine, source, *args, **kwargs)
        with patch.object(Organizer, "_copy_and_verify", copy), \
                patch("photocard.library_jobs.checksum", side_effect=AssertionError("Unexpected reread")):
            execute_library_job(resumed, local_root=self.local)
        self.assertEqual(copies, ["b.mp4"])
        self.assertEqual((self.target / "a.mp4").read_bytes(), b"first")
        self.assertTrue((self.source / "a.mp4").exists())
        mark_activated(plan.id, self.local)
        self.assertEqual(pending_jobs(self.source, self.local), [])

    def test_modified_completed_destination_requires_review(self):
        plan = self.migration()
        self.interrupt(plan)
        (self.target / "a.mp4").write_bytes(b"external change")
        with self.assertRaisesRegex(SourceChangedError, "destination changed"):
            execute_library_job(load_job(plan.id, self.local), local_root=self.local)
        self.assertFalse((self.target / "b.mp4").exists())
        self.assertEqual((self.target / "a.mp4").read_bytes(), b"external change")

    def test_replaced_source_folder_is_rejected(self):
        plan = self.migration()
        self.interrupt(plan)
        self.source.rename(self.root / "original")
        self.source.mkdir()
        with self.assertRaisesRegex(ValueError, "replaced"):
            execute_library_job(load_job(plan.id, self.local), local_root=self.local)

    def test_finalization_can_resume_after_index_paths_were_updated(self):
        media = self.source / "a.mp4"
        manifest = ImportManifest(self.source)
        manifest.record("key", "card", "a.mp4", 5, media.stat().st_mtime_ns, media, "copy", "size")
        plan = self.migration()
        from photocard.library_jobs import relocate_migrated_index
        def crash_after_index(source, destination, **kwargs):
            relocate_migrated_index(source, destination, **kwargs)
            raise RuntimeError("simulated interruption after index update")
        with patch("photocard.library_jobs.relocate_migrated_index", side_effect=crash_after_index):
            with self.assertRaises(RuntimeError):
                execute_library_job(plan, local_root=self.local)
        with patch.object(Organizer, "_copy_and_verify", side_effect=AssertionError("No recopy")):
            execute_library_job(load_job(plan.id, self.local), local_root=self.local)
        journal = JobJournal(plan, self.local)
        try:
            self.assertEqual(journal.phase, "complete")
        finally:
            journal.close()

    def test_move_cleanup_interruption_reconciles_missing_source(self):
        plan = build_library_job(self.config, [self.source], mode="reorganize")
        with patch.object(JobJournal, "mark_done", side_effect=RuntimeError("after cleanup")):
            with self.assertRaises(RuntimeError):
                execute_library_job(plan, local_root=self.local)
        self.assertFalse(plan.entries[0].source.exists())
        records = execute_library_job(load_job(plan.id, self.local), local_root=self.local)
        self.assertEqual(len(records), 2)
        self.assertTrue(all(entry.destination.exists() for entry in plan.entries))


if __name__ == "__main__":
    unittest.main()
