"""Bounded local error evidence, including faults outside Python exceptions."""

import faulthandler
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import platform
import sys
import threading
import re
import uuid
import os
from functools import wraps
from zipfile import ZIP_DEFLATED, ZipFile

from . import __version__


class Diagnostics:
    def __init__(self, directory: Path, detailed: bool = False):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger("photocard")
        self.previous_level = self.logger.level
        self.handler = RotatingFileHandler(self.directory / "application.log", maxBytes=2 * 1024 * 1024,
                                          backupCount=3, encoding="utf-8")
        self.handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"))
        self.logger.addHandler(self.handler)
        self.set_detailed(detailed)
        self.previous_sys_hook = sys.excepthook
        self.previous_thread_hook = threading.excepthook
        self.sys_hook = self._exception
        self.thread_hook = self._thread_exception
        sys.excepthook = self.sys_hook
        threading.excepthook = self.thread_hook
        self.fault_file = None
        self.closed = False
        self.qt_handler = None
        self.session_marker = self.directory / "session-active"
        self.previous_unclean_exit = self.session_marker.exists()
        self.session_id = uuid.uuid4().hex
        self.failed = False
        self.fault_header = f"Photo Card Organizer {__version__}\n"
        try:
            self.session_marker.write_text(self.session_id, encoding="ascii")
        except OSError:
            self.close()
            raise
        self.logger.info("Session %s started; version=%s; python=%s; platform=%s; pid=%s; previous_unclean_exit=%s",
                         self.session_id, __version__, platform.python_version(), sys.platform,
                         os.getpid(), self.previous_unclean_exit)
        if not faulthandler.is_enabled():
            # Never rotate an open faulthandler descriptor; rotate only at startup.
            try:
                fault = self.directory / "native-crash.log"
                if fault.exists() and fault.stat().st_size:
                    with fault.open("r", encoding="utf-8", errors="replace") as handle:
                        header = handle.readline()
                        has_details = bool(handle.read(1))
                    if has_details or not header.startswith("Photo Card Organizer "):
                        fault.replace(self.directory / "native-crash.previous.log")
                self.fault_file = fault.open("w", encoding="utf-8")
                self.fault_file.write(self.fault_header)
                self.fault_file.flush()
                faulthandler.enable(file=self.fault_file, all_threads=True)
            except OSError:
                self.close()
                raise

    def set_detailed(self, enabled: bool):
        self.logger.setLevel(logging.DEBUG if enabled else logging.INFO)

    def install_qt_handler(self):
        if self.qt_handler is not None:
            return
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
        def capture(kind, context, message):
            levels = {QtMsgType.QtDebugMsg: logging.DEBUG, QtMsgType.QtInfoMsg: logging.DEBUG,
                      QtMsgType.QtWarningMsg: logging.WARNING, QtMsgType.QtCriticalMsg: logging.ERROR,
                      QtMsgType.QtFatalMsg: logging.CRITICAL}
            self.logger.log(levels.get(kind, logging.ERROR), "Qt: %s", message)
        self.qt_handler = capture
        self.previous_qt_handler = qInstallMessageHandler(capture)

    def _exception(self, kind, value, traceback):
        self.failed = True
        self.logger.critical("Unhandled exception; version=%s", __version__,
                             exc_info=(kind, value, traceback))
        self.previous_sys_hook(kind, value, traceback)

    def _thread_exception(self, args):
        self.failed = True
        self.logger.critical("Unhandled worker exception; version=%s", __version__,
                             exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        self.previous_thread_hook(args)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.qt_handler is not None:
            from PySide6.QtCore import qInstallMessageHandler
            qInstallMessageHandler(self.previous_qt_handler)
            self.qt_handler = None
        if sys.excepthook is self.sys_hook:
            sys.excepthook = self.previous_sys_hook
        if threading.excepthook is self.thread_hook:
            threading.excepthook = self.previous_thread_hook
        if self.fault_file is not None:
            faulthandler.disable()
            self.fault_file.close()
        self.logger.info("Session %s ended; unhandled_error=%s", self.session_id, self.failed)
        try:
            if not self.failed and self.session_marker.read_text(encoding="ascii") == self.session_id:
                self.session_marker.unlink()
        except (OSError, UnicodeError):
            self.logger.warning("Could not clear diagnostic session marker")
        self.logger.removeHandler(self.handler)
        self.handler.close()
        self.logger.setLevel(self.previous_level)


def traced_operation(function):
    """Record only operation boundaries, including an ID usable after a crash."""
    @wraps(function)
    def run(*args, **kwargs):
        logger = logging.getLogger("photocard.operations")
        identifier = uuid.uuid4().hex[:12]
        logger.info("Operation %s %s started", identifier, function.__name__)
        try:
            result = function(*args, **kwargs)
        except Exception:
            logger.exception("Operation %s %s failed", identifier, function.__name__)
            raise
        logger.info("Operation %s %s completed", identifier, function.__name__)
        return result
    return run


def _redact_text(text: str, paths: list[Path | str]) -> str:
    """Replace local paths before a support report leaves this computer."""
    values = sorted(
        {str(Path(path).expanduser()) for path in paths if str(path).strip()},
        key=len,
        reverse=True,
    )
    for index, value in enumerate(values, 1):
        variants = {value, value.replace("\\", "/"), value.replace("/", "\\")}
        for variant in variants:
            text = re.sub(re.escape(variant), lambda _match: f"<local-path-{index}>", text,
                          flags=re.IGNORECASE if sys.platform == "win32" else 0)
    return text


def export_report(directory: Path, destination: Path, config: dict) -> Path:
    """Create a small redacted archive containing only diagnostic evidence."""
    directory = Path(directory)
    destination = Path(destination)
    configured_paths: list[Path | str] = [
        Path.home(),
        *[
            library.get("root", "")
            for library in config.get("library_destinations", [])
            if isinstance(library, dict)
        ],
    ]
    report = "\n".join(
        (
            f"Photo Card Organizer {__version__}",
            f"Platform: {platform.platform()}",
            "This archive contains redacted diagnostic logs only; no media or settings are included.",
            "Known home and library roots are redacted; filenames, messages, and other paths may remain. Review before sharing.",
        )
    ) + "\n"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("report.txt", report)
        names = ["application.log", *[f"application.log.{n}" for n in range(1, 4)],
                 "native-crash.log", "native-crash.previous.log"]
        for name in names:
            path = directory / name
            if path.is_symlink():
                continue
            try:
                with path.open("rb") as handle:
                    text = handle.read(2 * 1024 * 1024).decode("utf-8", errors="replace")
            except OSError:
                continue
            archive.writestr(path.name, _redact_text(text, configured_paths))
    return destination
