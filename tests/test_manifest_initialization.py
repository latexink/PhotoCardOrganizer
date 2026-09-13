import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from photocard.manifest import ImportManifest


class ManifestInitializationTests(unittest.TestCase):
    def test_reader_cannot_see_partial_schema(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reader = ImportManifest(root, create=False)
            observed = []
            connect = ImportManifest._connect

            def traced_connect(manifest):
                connection = connect(manifest)
                if manifest is not reader:
                    def trace(sql):
                        if "CREATE TABLE IF NOT EXISTS digest_runs" in sql:
                            with closing(sqlite3.connect(reader.path)) as other:
                                observed.append(other.execute(
                                    "SELECT name FROM sqlite_master WHERE type='table'"
                                ).fetchall())
                            observed.append(reader.digest_summary("test")["total"])
                    connection.set_trace_callback(trace)
                return connection

            with patch.object(ImportManifest, "_connect", traced_connect):
                ImportManifest(root)
            self.assertEqual(observed, [[], 0])
            self.assertEqual(reader.digest_summary("test")["total"], 0)
