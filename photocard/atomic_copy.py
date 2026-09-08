from __future__ import annotations

import hashlib
import os
import platform
import sys
import tempfile
import uuid
import threading
from pathlib import Path


def _owner_material() -> str:
    user = next(
        (
            value
            for key in ("USERNAME", "USER", "LOGNAME")
            if (value := os.environ.get(key, "").strip())
        ),
        "",
    )
    if hasattr(os, "getuid"):
        user = f"{user}|{os.getuid()}"
    elif not user:
        user = "unknown-user"
    return f"{platform.node()}|{user}"


_OWNER_ID = hashlib.sha256(_owner_material().encode("utf-8")).hexdigest()[:16]
_PROCESS_RUN_ID = uuid.uuid4().hex[:16]
_PARTIAL_PREFIX = f".pco-{_OWNER_ID}-"
_CURRENT_PREFIX = f"{_PARTIAL_PREFIX}{_PROCESS_RUN_ID}-"
_CLEANED_DIRECTORIES: set[Path] = set()
_CLEANUP_LOCK = threading.Lock()


def cleanup_abandoned_partials(directory: Path) -> None:
    for candidate in directory.glob(f"{_PARTIAL_PREFIX}*.partial"):
        if candidate.name.startswith(_CURRENT_PREFIX):
            continue
        try:
            candidate.unlink()
        except OSError:
            pass


def create_partial_file(directory: Path) -> Path:
    key = directory.resolve()
    with _CLEANUP_LOCK:
        if key not in _CLEANED_DIRECTORIES:
            cleanup_abandoned_partials(directory)
            _CLEANED_DIRECTORIES.add(key)
    descriptor, name = tempfile.mkstemp(
        prefix=_CURRENT_PREFIX,
        suffix=".partial",
        dir=directory,
    )
    os.close(descriptor)
    return Path(name)


def commit_without_overwrite(temporary: Path, destination: Path) -> None:
    if os.name == "nt":
        os.rename(temporary, destination)
        return

    if sys.platform.startswith("linux"):
        try:
            import ctypes
            import errno

            libc = ctypes.CDLL(None, use_errno=True)
            renameat2 = libc.renameat2
            renameat2.argtypes = (
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            )
            renameat2.restype = ctypes.c_int
            result = renameat2(
                -100,
                os.fsencode(temporary),
                -100,
                os.fsencode(destination),
                1,
            )
            if result == 0:
                return
            error_number = ctypes.get_errno()
            if error_number == errno.EEXIST:
                raise FileExistsError(
                    error_number, os.strerror(error_number), destination
                )
            if error_number not in {
                errno.ENOSYS,
                errno.EINVAL,
                errno.EOPNOTSUPP,
            }:
                raise OSError(
                    error_number, os.strerror(error_number), destination
                )
        except AttributeError:
            pass

    # Both names are in one directory, so this is an atomic no-replace commit.
    os.link(temporary, destination, follow_symlinks=False)


def partial_owner_id() -> str:
    return _OWNER_ID


def same_filesystem(source: Path, destination: Path) -> bool:
    ancestor = destination.parent
    while not ancestor.exists():
        if ancestor.parent == ancestor:
            raise FileNotFoundError(f"Destination volume is unavailable: {destination}")
        ancestor = ancestor.parent
    device = source.stat().st_dev
    return bool(device) and device == ancestor.stat().st_dev


def relocate_without_overwrite(source: Path, destination: Path) -> None:
    """Reuse the no-replace primitive, including its hard-link fallback."""
    if not same_filesystem(source, destination):
        import errno
        raise OSError(errno.EXDEV, "A cross-filesystem relocation requires copying")
    commit_without_overwrite(source, destination)
    if source.exists():
        if not os.path.samefile(source, destination):
            raise OSError("Source identity changed during relocation")
        source.unlink()
    if os.name != "nt":
        for directory in {source.parent, destination.parent}:
            descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)


def current_partial_prefix() -> str:
    return _CURRENT_PREFIX
