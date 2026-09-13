from __future__ import annotations

import time


class TransferTiming:
    """Operation averages without extra filesystem reads or disk polling."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.read_bytes = 0
        self.written_bytes = 0

    def details(self, current: int, total: int) -> dict:
        elapsed = max(0.0, self.clock() - self.started)
        eta = None
        if current >= total and total > 0:
            eta = 0
        elif elapsed >= 2 and 0 < current < total:
            eta = elapsed * (total - current) / current
        return {
            "eta_seconds": eta,
            "read_bytes_per_second": self.read_bytes / elapsed if elapsed else 0,
            "write_bytes_per_second": self.written_bytes / elapsed if elapsed else 0,
        }


def timing_text(details: dict) -> str:
    if "eta_seconds" not in details:
        return ""
    eta = details["eta_seconds"]
    if eta is None:
        remaining = "Estimating..."
    elif eta < 60:
        remaining = f"~{max(0, int(eta))}s remaining"
    elif eta < 3600:
        remaining = f"~{int(eta // 60)}m {int(eta % 60)}s remaining"
    else:
        remaining = f"~{int(eta // 3600)}h {int(eta % 3600 // 60)}m remaining"
    read = details.get("read_bytes_per_second", 0) / (1024 * 1024)
    write = details.get("write_bytes_per_second", 0) / (1024 * 1024)
    return f"{remaining} (avg read {read:.1f} / write {write:.1f} MiB/s)"
