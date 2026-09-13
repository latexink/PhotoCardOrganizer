import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from photocard.integrity import IntegrityCatalog, checksum
from photocard.integrity_restore import restore_file
from photocard.atomic_copy import relocate_without_overwrite


class RestoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "library"
        self.backup = self.base / "backup"
        self.path = self.root / "Photos" / "image.jpg"
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"original")
        (self.backup / "Photos").mkdir(parents=True)
        (self.backup / "Photos/image.jpg").write_bytes(b"original")
        self.catalog = IntegrityCatalog(self.root, self.base / "local")
        self.catalog.record(self.path, checksum(self.path), "sha256", verified=True)
        self.baselines = self.catalog.records()
        self.path.write_bytes(b"damaged!")

    def test_restores_verified_bytes_preserves_damage_and_baseline(self):
        self.assertEqual(restore_file(self.catalog, self.path, [self.backup]), "Restored from verified backup")
        self.assertEqual(self.path.read_bytes(), b"original")
        originals = list((self.catalog.directory / "recovery").glob("*/original/Photos/image.jpg"))
        self.assertEqual(len(originals), 1)
        self.assertEqual(originals[0].read_bytes(), b"damaged!")
        self.assertEqual(self.catalog.records(), self.baselines)
        self.assertIn('"state": "complete"', self.catalog.local_path.read_text())

    def test_skips_offline_and_bad_backups(self):
        bad = self.base / "bad"
        (bad / "Photos").mkdir(parents=True)
        (bad / "Photos/image.jpg").write_bytes(b"badbytes")
        restore_file(self.catalog, self.path, [self.base / "offline", bad, self.backup])
        self.assertEqual(self.path.read_bytes(), b"original")
        self.assertFalse((self.base / "offline").exists())

    def test_no_matching_backup_never_changes_original(self):
        (self.backup / "Photos/image.jpg").write_bytes(b"badbytes")
        with self.assertRaisesRegex(ValueError, "No verified backup"):
            restore_file(self.catalog, self.path, [self.backup])
        self.assertEqual(self.path.read_bytes(), b"damaged!")

    def test_missing_file_is_restored(self):
        self.path.unlink()
        restore_file(self.catalog, self.path, [self.backup])
        self.assertEqual(self.path.read_bytes(), b"original")

    def test_cancellation_and_no_baseline(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(InterruptedError):
            restore_file(self.catalog, self.path, [self.backup], cancel_event=cancel)
        with self.assertRaisesRegex(ValueError, "saved checksum"):
            restore_file(self.catalog, self.root / "unknown.jpg", [self.backup])
        self.assertEqual(self.path.read_bytes(), b"damaged!")

    def test_interruption_preserves_original_staged_copy_and_journal(self):
        def fail_commit(source, destination):
            if source.name == "verified-copy.partial":
                raise OSError("simulated interruption")
            relocate_without_overwrite(source, destination)
        with patch("photocard.integrity_restore.relocate_without_overwrite", side_effect=fail_commit):
            with self.assertRaisesRegex(OSError, "simulated interruption"):
                restore_file(self.catalog, self.path, [self.backup])
        recovery = next((self.catalog.directory / "recovery").iterdir())
        self.assertEqual((recovery / "original/Photos/image.jpg").read_bytes(), b"damaged!")
        self.assertEqual((recovery / "verified-copy.partial").read_bytes(), b"original")
        self.assertTrue((recovery / "journal.jsonl").exists())
        restore_file(self.catalog, self.path, [self.backup])
        self.assertEqual(self.path.read_bytes(), b"original")

    def test_outside_library_is_rejected(self):
        with self.assertRaises(ValueError):
            restore_file(self.catalog, self.base / "outside.jpg", [self.backup])
