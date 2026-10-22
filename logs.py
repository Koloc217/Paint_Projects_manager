"""Application logs with rotation and no external tools (logrotate, systemd): works the same on Windows and Linux.

  logs/app.log       everything the app prints to the console (bot, scheduler, errors); the console still gets a copy
  logs/notify.log    output of the notification script; logs/backup.log, output of the backup script

A file larger than MAX_BYTES is shifted: app.log -> app.log.1 -> app.log.2 ... Copies older than KEEP are removed.
"""
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler

from common import BASE_DIR

LOG_DIR = os.path.join(BASE_DIR, "logs")
MAX_BYTES = 1_000_000
KEEP = 3

_installed = False


def log_path(name: str) -> str:
    os.makedirs(LOG_DIR, exist_ok=True)
    return os.path.join(LOG_DIR, name)


def rotate_file(path: str, max_bytes: int = MAX_BYTES, keep: int = KEEP) -> bool:
    """Shifts path to path.1, path.1 to path.2 and so on if the file is larger than max_bytes. File system errors
    (for example, a file held open on Windows) are not failures: rotation is simply retried on the next run."""
    try:
        if os.path.getsize(path) <= max_bytes:
            return False
        oldest = f"{path}.{keep}"
        if os.path.exists(oldest):
            os.remove(oldest)
        for i in range(keep - 1, 0, -1):
            src = f"{path}.{i}"
            if os.path.exists(src):
                os.replace(src, f"{path}.{i + 1}")
        os.replace(path, f"{path}.1")
        return True
    except OSError:
        return False


class _Tee:
    """A stream that writes both to the original console and to the log (line by line, with a timestamp).
    print() is called from several threads (web handlers, bot workers), so the line buffer is guarded by a lock
    shared by stdout and stderr, and records go through handler.handle(), which takes the handler's own lock."""

    _lock = threading.Lock()

    def __init__(self, console, handler):
        self.console = console
        self.handler = handler
        self.buffer = ""

    def write(self, text):
        try:
            if self.console:
                self.console.write(text)
        except Exception:
            pass
        with self._lock:
            self.buffer += text
            lines = []
            while "\n" in self.buffer:
                line, self.buffer = self.buffer.split("\n", 1)
                if line.strip():
                    lines.append(line)
        for line in lines:
            try:
                self.handler.handle(logging.LogRecord("app", logging.INFO, "", 0, line, None, None))
            except Exception:
                pass
        return len(text)

    def flush(self):
        try:
            if self.console:
                self.console.flush()
        except Exception:
            pass

    def isatty(self):
        return bool(self.console and getattr(self.console, "isatty", lambda: False)())

    def __getattr__(self, name):
        return getattr(self.console, name)


def setup_app_log() -> None:
    """Starts copying the app output to logs/app.log with rotation. Repeated calls do nothing."""
    global _installed
    if _installed:
        return
    try:
        handler = RotatingFileHandler(log_path("app.log"), maxBytes=MAX_BYTES, backupCount=KEEP, encoding="utf-8", delay=True)
    except OSError as e:
        print(f"[LOG] cannot write the log file: {e!r}")
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    sys.stdout = _Tee(sys.stdout, handler)
    sys.stderr = _Tee(sys.stderr, handler)
    _installed = True
