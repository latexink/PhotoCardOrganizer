from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from photocard.atomic_copy import (
    cleanup_abandoned_partials,
    commit_without_overwrite,
    create_partial_file,
    current_partial_prefix,
    partial_owner_id,
)


class AtomicCopyTests(unittest.TestCase):
    def test_commit_never_replaces_an_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            partial = root / "incoming.partial"
            destination = root / "IMG_0001.JPG"
            partial.write_bytes(b"incoming")
            destination.write_bytes(b"existing")

            with self.assertRaises(OSError):
                commit_without_overwrite(partial, destination)

            self.assertEqual(b"existing", destination.read_bytes())
            self.assertEqual(b"incoming", partial.read_bytes())

    def test_restart_cleanup_removes_only_this_clients_abandoned_partials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            current = create_partial_file(directory)
            abandoned = (
                directory
                / f".pco-{partial_owner_id()}-previous-run-abandoned.partial"
            )
            foreign = directory / ".pco-another-client-previous-run.partial"
            abandoned.write_bytes(b"incomplete")
            foreign.write_bytes(b"other client")

            cleanup_abandoned_partials(directory)

            self.assertTrue(current.exists())
            self.assertTrue(current.name.startswith(current_partial_prefix()))
            self.assertFalse(abandoned.exists())
            self.assertTrue(foreign.exists())


if __name__ == "__main__":
    unittest.main()
