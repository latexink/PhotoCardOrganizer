from __future__ import annotations

import copy
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .digest import run_digest_profile
from .models import ActivityEvent
from .organizer import Organizer
from .source_monitor import SourceDiscovery
from .transfer_hub import (
    catch_sources,
    source_entries,
    write_digestion_receipts,
)


class MonitorService:
    def __init__(self, config: dict, event_callback: Callable[[ActivityEvent], None]):
        self._config = copy.deepcopy(config)
        self._event_callback = event_callback
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._paused = threading.Event()
        self._config_lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last_event: dict[str, float] = {}
        self._last_hub_scan: dict[str, float] = {}
        self._last_digest_scan: dict[str, float] = {}
        self._discovery = SourceDiscovery()
        self._card_schedule: dict[tuple[str, str], tuple[float, float]] = {}
        self._force_scan = threading.Event()

    @property
    def is_paused(self) -> bool:
        return self._paused.is_set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="photo-card-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._paused.set()
            self._send(ActivityEvent("info", "Background monitoring paused."), force=True)
        else:
            self._paused.clear()
            self._wake_event.set()
            self._send(ActivityEvent("info", "Background monitoring resumed."), force=True)

    def toggle_paused(self) -> bool:
        self.set_paused(not self.is_paused)
        return self.is_paused

    def scan_now(self) -> None:
        self._force_scan.set()
        self._wake_event.set()

    def update_config(self, config: dict) -> None:
        with self._config_lock:
            self._config = copy.deepcopy(config)
        self.scan_now()

    def run_exclusive(self, callback: Callable[[], None]) -> None:
        with self._scan_lock:
            callback()

    def _config_snapshot(self) -> dict:
        with self._config_lock:
            return copy.deepcopy(self._config)

    def _send(self, event: ActivityEvent, force: bool = False) -> None:
        now = time.monotonic()
        previous = self._last_event.get(event.message)
        if not force and previous is not None and now - previous < 60:
            return
        self._last_event[event.message] = now
        self._event_callback(event)

    def _run(self) -> None:
        self._send(ActivityEvent("info", "Background monitoring started."), force=True)
        while not self._stop_event.is_set():
            config = self._config_snapshot()
            poll_seconds = max(1.0, float(config["monitor"].get("poll_seconds", 30)))
            if not self._paused.is_set():
                try:
                    with self._scan_lock:
                        results, errors = self._scan_connected_cards(config)
                        hub_imported, hub_errors = self._catch_hubs(
                            config, None
                        )
                        digest_imported, digest_errors = self._digest_inboxes(
                            config
                        )
                    imported = sum(result.imported for result in results)
                    imported += hub_imported
                    imported += digest_imported
                    if imported:
                        self._send(
                            ActivityEvent("success", f"Background import completed: {imported} file(s)."),
                            force=True,
                        )
                    if errors:
                        self._send(
                            ActivityEvent("warning", f"Card discovery reported {len(errors)} issue(s)."),
                            force=True,
                        )
                    if hub_errors:
                        self._send(
                            ActivityEvent(
                                "warning",
                                f"Transfer hub monitoring reported {len(hub_errors)} issue(s).",
                            ),
                            force=True,
                        )
                    if digest_errors:
                        self._send(
                            ActivityEvent(
                                "warning",
                                f"Digest Inbox monitoring reported {len(digest_errors)} issue(s).",
                            ),
                            force=True,
                        )
                except Exception as exc:
                    self._send(ActivityEvent("error", f"Background scan failed: {exc}"))
            self._wake_event.wait(poll_seconds)
            self._wake_event.clear()
        self._send(ActivityEvent("info", "Background monitoring stopped."), force=True)

    def _scan_connected_cards(self, config: dict):
        force = self._force_scan.is_set()
        self._force_scan.clear()
        previous = {(card.card_id, str(card.root)): card for card in self._discovery.cards}
        cards, errors, changed = self._discovery.poll(config, force=force)
        for error in errors:
            self._send(ActivityEvent("error", error))
        if force:
            self._card_schedule.clear()
        elif changed:
            current = {(card.card_id, str(card.root)): card for card in cards}
            self._card_schedule = {
                key: schedule for key, schedule in self._card_schedule.items()
                if key in current and previous.get(key) == current[key]
            }
        now = time.monotonic()
        interval = max(1.0, float(config["monitor"].get("poll_seconds", 30)))
        maximum = max(interval, float(config["monitor"].get("idle_scan_max_seconds", 300)))
        results = []
        organizer = None
        for card in cards:
            key = (card.card_id, str(card.root))
            due, delay = self._card_schedule.get(key, (0.0, interval))
            if now < due:
                continue
            if organizer is None:
                organizer = Organizer(config, event_callback=self._send)
            result = organizer.scan_cards([card], allow_destructive=False)[0]
            results.append(result)
            idle = not (result.imported or result.blocked or result.failed)
            delay = min(maximum, delay * 2) if idle else interval
            self._card_schedule[key] = (time.monotonic() + delay, delay)
        return results, errors

    def _catch_hubs(
        self, config: dict, organizer: Organizer | None
    ) -> tuple[int, list[str]]:
        imported = 0
        errors: list[str] = []
        now = time.monotonic()
        for hub in config.get("transfer_hubs", []):
            if (
                not hub.get("enabled", True)
                or hub.get("role") != "catch"
                or not hub.get("auto_catch", False)
            ):
                continue
            hub_id = str(hub["id"])
            interval = max(10.0, float(hub.get("poll_seconds", 60)))
            previous = self._last_hub_scan.get(hub_id)
            if previous is not None and now - previous < interval:
                continue
            self._last_hub_scan[hub_id] = now
            cards, hub_errors = catch_sources(config, hub)
            errors.extend(hub_errors)
            for card in cards:
                if organizer is None:
                    organizer = Organizer(config, event_callback=self._send)
                entries = source_entries(organizer, card)
                result = organizer.scan_card(card, allow_destructive=False)
                imported += result.imported
                errors.extend(result.errors)
                if hub.get("write_receipts", True):
                    recorded, receipt_errors = write_digestion_receipts(
                        config,
                        hub,
                        card,
                        organizer.manifest,
                        entries,
                    )
                    errors.extend(receipt_errors)
                    if recorded:
                        self._send(
                            ActivityEvent(
                                "success",
                                f"{card.name}: confirmed digestion of {recorded} file(s).",
                            ),
                            force=True,
                        )
        return imported, errors

    def _digest_inboxes(self, config: dict) -> tuple[int, list[str]]:
        imported = 0
        errors: list[str] = []
        now = time.monotonic()
        for profile in config.get("digest_inboxes", []):
            if (
                not profile.get("enabled", True)
                or not profile.get("auto_digest", False)
                or profile.get("action", "copy") != "copy"
            ):
                continue
            profile_id = str(profile["id"])
            interval = max(10.0, float(profile.get("poll_seconds", 60)))
            previous = self._last_digest_scan.get(profile_id)
            if previous is not None and now - previous < interval:
                continue
            self._last_digest_scan[profile_id] = now
            root = Path(str(profile.get("root", ""))).expanduser()
            try:
                available = root.is_dir()
            except OSError:
                available = False
            if not available:
                errors.append(f"{profile['name']}: Digest Inbox is offline.")
                continue
            try:
                result = run_digest_profile(
                    config,
                    profile,
                    event_callback=self._send,
                )
            except Exception as exc:
                errors.append(f"{profile['name']}: {exc}")
                continue
            imported += result.stats.imported
            errors.extend(result.errors)
        return imported, errors
