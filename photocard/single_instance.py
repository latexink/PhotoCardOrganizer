from __future__ import annotations

import hmac
import json
import os
import secrets
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path


INSTALLER_MUTEX_NAME = "PhotoCardOrganizer.App.1"


class SingleInstance:
    """Own a per-user process lock and redirect later launches to the owner."""

    def __init__(self, lock_path: Path | str):
        self.lock_path = Path(lock_path).expanduser()
        self.metadata_path = self.lock_path.with_name(f"{self.lock_path.name}.json")
        self._handle = None
        self._server: socket.socket | None = None
        self._server_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._token = ""
        self._windows_mutex_handle = None
        self.last_error: OSError | None = None

    @property
    def is_primary(self) -> bool:
        return self._handle is not None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        self.last_error = None
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.lock_path.open("a+b")
        except OSError as exc:
            self.last_error = exc
            return False
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
        except OSError as exc:
            self.last_error = exc
            handle.close()
            return False
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (ImportError, OSError):
            handle.close()
            return False

        self._handle = handle
        self._create_installer_mutex()
        if self._handle is not None:
            try:
                self.metadata_path.unlink(missing_ok=True)
            except OSError:
                pass
        return True

    def _create_installer_mutex(self) -> None:
        if os.name != "nt" or self._windows_mutex_handle is not None:
            return
        try:
            import ctypes

            create_mutex = ctypes.windll.kernel32.CreateMutexW
            create_mutex.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
            create_mutex.restype = ctypes.c_void_p
            self._windows_mutex_handle = create_mutex(None, False, INSTALLER_MUTEX_NAME)
        except (AttributeError, OSError):
            self._windows_mutex_handle = None

    def _close_installer_mutex(self) -> None:
        if self._windows_mutex_handle is None:
            return
        try:
            import ctypes

            close_handle = ctypes.windll.kernel32.CloseHandle
            close_handle.argtypes = [ctypes.c_void_p]
            close_handle.restype = ctypes.c_int
            close_handle(self._windows_mutex_handle)
        except (AttributeError, OSError):
            pass
        self._windows_mutex_handle = None

    def start_activation_server(self, on_activate: Callable[[], None]) -> None:
        if self._handle is None:
            raise RuntimeError("The instance lock must be acquired before activation starts.")
        if self._server is not None:
            return

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(4)
        server.settimeout(0.25)
        self._token = secrets.token_urlsafe(32)
        self._server = server
        self._stop_event.clear()

        metadata = {
            "pid": os.getpid(),
            "port": server.getsockname()[1],
            "token": self._token,
        }
        try:
            self._write_metadata(metadata)
        except Exception:
            server.close()
            self._server = None
            self._token = ""
            raise

        self._server_thread = threading.Thread(
            target=self._serve,
            args=(on_activate,),
            name="photo-card-activation",
            daemon=True,
        )
        self._server_thread.start()

    def _serve(self, on_activate: Callable[[], None]) -> None:
        while not self._stop_event.is_set():
            try:
                connection, _address = self._server.accept() if self._server else (None, None)
            except socket.timeout:
                continue
            except OSError:
                break
            if connection is None:
                break
            with connection:
                connection.settimeout(0.75)
                try:
                    payload = connection.recv(4096)
                    request = json.loads(payload.decode("utf-8"))
                    authorized = (
                        request.get("action") == "activate"
                        and isinstance(request.get("token"), str)
                        and hmac.compare_digest(request["token"], self._token)
                    )
                    if authorized:
                        try:
                            on_activate()
                        except Exception:
                            authorized = False
                    response = json.dumps({"ok": authorized}).encode("utf-8")
                    connection.sendall(response)
                except (OSError, UnicodeError, ValueError, TypeError):
                    continue

    def activate_existing(self, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + max(0.1, timeout)
        while time.monotonic() < deadline:
            try:
                metadata = json.loads(self.metadata_path.read_text(encoding="utf-8"))
                pid = int(metadata["pid"])
                port = int(metadata["port"])
                token = str(metadata["token"])
                if not 1 <= port <= 65535 or not token:
                    raise ValueError("Invalid activation metadata")
                self._allow_foreground(pid)
                with socket.create_connection(("127.0.0.1", port), timeout=0.5) as connection:
                    connection.sendall(
                        json.dumps({"action": "activate", "token": token}).encode("utf-8")
                    )
                    connection.settimeout(0.75)
                    response = json.loads(connection.recv(1024).decode("utf-8"))
                    if response.get("ok") is True:
                        return True
            except (KeyError, OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
                pass
            time.sleep(0.1)
        return False

    @staticmethod
    def _allow_foreground(pid: int) -> None:
        if os.name != "nt" or pid <= 0:
            return
        try:
            import ctypes

            ctypes.windll.user32.AllowSetForegroundWindow(pid)
        except (AttributeError, OSError):
            pass

    def _write_metadata(self, metadata: dict[str, object]) -> None:
        temporary = self.metadata_path.with_name(
            f".{self.metadata_path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        )
        try:
            temporary.write_text(json.dumps(metadata), encoding="utf-8")
            os.replace(temporary, self.metadata_path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def stop_activation_server(self) -> None:
        self._stop_event.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=1.0)
        self._server_thread = None
        self._token = ""
        if self._handle is not None:
            try:
                self.metadata_path.unlink(missing_ok=True)
            except OSError:
                pass

    def release(self) -> None:
        self.stop_activation_server()

        if self._handle is None:
            self._close_installer_mutex()
            return
        try:
            self._handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        finally:
            self._handle.close()
            self._handle = None
            self._close_installer_mutex()

    def __enter__(self) -> SingleInstance:
        if not self.acquire():
            raise RuntimeError("Another Photo Card Organizer instance is already running.")
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.release()
