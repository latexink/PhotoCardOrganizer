import unittest
import tempfile
from pathlib import Path

from photocard.progress import TransferTiming, timing_text
from photocard.organizer import Organizer


class TransferTimingTests(unittest.TestCase):
    def test_estimate_and_io_averages(self):
        now = [10.0]
        timing = TransferTiming(clock=lambda: now[0])
        self.assertIsNone(timing.details(0, 10)["eta_seconds"])
        timing.read_bytes = 40 * 1024 * 1024
        timing.written_bytes = 20 * 1024 * 1024
        now[0] = 20
        result = timing.details(2, 10)
        self.assertEqual(result["eta_seconds"], 40)
        self.assertEqual(timing_text(result),
                         "~40s remaining (avg read 4.0 / write 2.0 MiB/s)")

    def test_completion_reset_and_unknown_progress(self):
        timing = TransferTiming(clock=lambda: 0)
        self.assertEqual(timing.details(10, 10)["eta_seconds"], 0)
        self.assertIsNone(timing.details(0, 0)["eta_seconds"])
        self.assertEqual(timing_text({}), "")
        self.assertIn("Estimating", timing_text(timing.details(0, 10)))

    def test_duration_formats(self):
        self.assertTrue(timing_text({"eta_seconds": 125}).startswith("~2m 5s"))
        self.assertTrue(timing_text({"eta_seconds": 3660}).startswith("~1h 1m"))

    def test_copy_counts_payload_and_verification_reads(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.jpg"
            source.write_bytes(b"synthetic media" * 100)
            for algorithm, reads in (("size", 1), ("sha256", 2)):
                worker = Organizer.__new__(Organizer)
                worker._transfer_timing = TransferTiming()
                worker._copy_and_verify(source, root / algorithm, algorithm)
                self.assertEqual(worker._transfer_timing.read_bytes,
                                 source.stat().st_size * reads)
                self.assertEqual(worker._transfer_timing.written_bytes,
                                 source.stat().st_size)

    def test_progress_events_include_timing_but_analysis_does_not(self):
        worker = Organizer.__new__(Organizer)
        worker._transfer_timing = TransferTiming(clock=lambda: 0)
        events = []
        worker.event_callback = events.append
        worker._emit("progress", "Importing", current=0, total=10)
        worker._emit("progress", "Analyzing", current=0, total=10, stage="Analyzing")
        self.assertIn("eta_seconds", events[0].details)
        self.assertNotIn("eta_seconds", events[1].details)
