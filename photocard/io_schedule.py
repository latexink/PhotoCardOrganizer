"""Serialize bulk file I/O so background work does not contend with transfers."""
from contextlib import contextmanager
from functools import wraps
import threading

_bulk_lock = threading.RLock()


@contextmanager
def bulk_io(cancel_event=None):
    while not _bulk_lock.acquire(timeout=0.1):
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Disk operation cancelled while waiting")
    try:
        yield
    finally:
        _bulk_lock.release()


def serialized_io(function):
    @wraps(function)
    def run(*args, **kwargs):
        with bulk_io(kwargs.get("cancel_event")):
            return function(*args, **kwargs)
    return run
