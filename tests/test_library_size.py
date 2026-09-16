import tempfile
import unittest
from pathlib import Path

from photocard.library_size import library_size


class LibrarySizeTests(unittest.TestCase):
    def test_counts_nested_files_and_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "photo.jpg").write_bytes(b"1234")
            metadata = root / ".photocard-organizer"
            metadata.mkdir()
            (metadata / "record").write_bytes(b"12")
            self.assertEqual(library_size(root), 6)

    def test_missing_root_is_not_reported_as_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(OSError):
                library_size(Path(folder) / "missing")
