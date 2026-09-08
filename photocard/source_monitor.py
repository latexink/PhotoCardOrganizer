from __future__ import annotations

import os
import time
from pathlib import Path

from .discovery import _windows_drive_roots, discover_cards


class SourceDiscovery:
    """Cache card identities between mount changes and bounded reconciliation."""

    def __init__(self):
        self.token = None
        self.expires = 0.0
        self.cards = []
        self.errors = []

    @staticmethod
    def mount_token():
        if os.name == "nt":
            return tuple(str(root) for root in _windows_drive_roots())
        try:
            return Path("/proc/self/mountinfo").read_text(encoding="utf-8")
        except OSError:
            return None

    def poll(self, config: dict, *, force: bool = False):
        now = time.monotonic()
        token = self.mount_token()
        changed = force or token != self.token or now >= self.expires
        if changed:
            self.cards, self.errors = discover_cards(config)
            self.token = token
            self.expires = now + max(30.0, float(config["monitor"].get("idle_scan_max_seconds", 300)))
        return self.cards, self.errors, changed
