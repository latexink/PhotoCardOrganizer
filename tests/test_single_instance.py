from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from photocard.single_instance import SingleInstance


class SingleInstanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.lock_path = Path(self.temporary.name) / "instance.lock"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_competing_instance_is_rejected_and_can_activate_owner(self) -> None:
        activated = threading.Event()
        primary = SingleInstance(self.lock_path)
        secondary = SingleInstance(self.lock_path)
        try:
            self.assertTrue(primary.acquire())
            primary.start_activation_server(activated.set)
            self.assertFalse(secondary.acquire())
            self.assertTrue(secondary.activate_existing())
            self.assertTrue(activated.wait(1.0))
        finally:
            secondary.release()
            primary.release()

    def test_lock_can_be_acquired_after_owner_releases_it(self) -> None:
        primary = SingleInstance(self.lock_path)
        replacement = SingleInstance(self.lock_path)
        try:
            self.assertTrue(primary.acquire())
            self.assertFalse(replacement.acquire())
            primary.release()
            self.assertTrue(replacement.acquire())
        finally:
            replacement.release()
            primary.release()

    def test_second_cli_process_redirects_to_running_owner(self) -> None:
        environment = os.environ.copy()
        environment["APPDATA"] = self.temporary.name
        environment["XDG_CONFIG_HOME"] = self.temporary.name
        child_script = (
            "import threading\n"
            "from photocard.config import default_config_path\n"
            "from photocard.single_instance import SingleInstance\n"
            "event = threading.Event()\n"
            "guard = SingleInstance(default_config_path().parent / 'instance.lock')\n"
            "assert guard.acquire()\n"
            "guard.start_activation_server(event.set)\n"
            "print('READY', flush=True)\n"
            "activated = event.wait(8)\n"
            "guard.release()\n"
            "raise SystemExit(0 if activated else 4)\n"
        )
        owner = subprocess.Popen(
            [sys.executable, "-c", child_script],
            cwd=Path.cwd(),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual("READY", owner.stdout.readline().strip())
            redirected = subprocess.run(
                [sys.executable, "-m", "photocard"],
                cwd=Path.cwd(),
                env=environment,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(0, redirected.returncode, redirected.stderr)
            self.assertEqual(0, owner.wait(timeout=5), owner.stderr.read())
        finally:
            if owner.poll() is None:
                owner.terminate()
                owner.wait(timeout=5)
            if owner.stdout is not None:
                owner.stdout.close()
            if owner.stderr is not None:
                owner.stderr.close()
