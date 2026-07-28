from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request

from .config import DEFAULT_USER_AGENT
from .manifest import ImportManifest


class ReverseGeocoder:
    def __init__(self, config: dict, manifest: ImportManifest):
        self.config = config
        self.manifest = manifest
        self._lock = threading.Lock()
        self._last_request = 0.0

    def place_name(self, latitude: float | None, longitude: float | None) -> str:
        if latitude is None or longitude is None:
            return ""
        location = self.config["location"]
        if not location.get("online_place_names", False):
            return ""
        precision = int(location.get("coordinate_precision", 4))
        coordinate_key = f"{latitude:.{precision}f},{longitude:.{precision}f}"
        cached = self.manifest.get_location(coordinate_key)
        if cached:
            return cached
        if location.get("provider") != "nominatim":
            return ""
        with self._lock:
            elapsed = time.monotonic() - self._last_request
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)
            place = self._query_nominatim(latitude, longitude)
            self._last_request = time.monotonic()
        if place:
            self.manifest.put_location(coordinate_key, place)
        return place

    def _query_nominatim(self, latitude: float, longitude: float) -> str:
        location = self.config["location"]
        query = urllib.parse.urlencode(
            {
                "format": "jsonv2",
                "lat": latitude,
                "lon": longitude,
                "zoom": 10,
                "addressdetails": 1,
            }
        )
        request = urllib.request.Request(
            f"https://nominatim.openstreetmap.org/reverse?{query}",
            headers={
                "User-Agent": str(
                    location.get("user_agent", DEFAULT_USER_AGENT)
                )
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=float(location.get("request_timeout_seconds", 8))
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return ""
        address = payload.get("address", {})
        locality = (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or address.get("municipality")
            or address.get("county")
        )
        region = address.get("state") or address.get("country")
        parts = [str(part).strip() for part in (locality, region) if part]
        return ", ".join(dict.fromkeys(parts))
