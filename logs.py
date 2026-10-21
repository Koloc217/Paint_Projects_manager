"""Журналы приложения с ротацией, без внешних средств (logrotate, systemd): работает одинаково в Windows и Linux.

  logs/app.log       всё, что приложение печатает в консоль (бот, планировщик, ошибки); копия вывода пишется и в консоль
  logs/notify.log    вывод скрипта уведомлений, logs/backup.log, вывод скрипта бэкапа

Файл больше MAX_BYTES сдвигается: app.log -> app.log.1 -> app.log.2 ... Старше KEEP копий удаляется.
"""
import logging
import os
import sys
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
    """Сдвигает файл в path.1, path.1 в path.2 и так далее, если он больше max_bytes. Ошибки файловой системы
    (например, файл занят в Windows) не считаются сбоем: ротация просто повторится при следующем запуске."""
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
    """Поток, который пишет и в исходную консоль, и в журнал (построчно, с отметкой времени)."""

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
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line.strip():
                self.handler.emit(logging.LogRecord("app", logging.INFO, "", 0, line, None, None))
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
    """Включает запись вывода приложения в logs/app.log с ротацией. Повторный вызов ничего не делает."""
    global _installed
    if _installed:
        return
    try:
        handler = RotatingFileHandler(log_path("app.log"), maxBytes=MAX_BYTES, backupCount=KEEP, encoding="utf-8", delay=True)
    except OSError as e:
        print(f"[LOG] журнал в файл недоступен: {e!r}")
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    sys.stdout = _Tee(sys.stdout, handler)
    sys.stderr = _Tee(sys.stderr, handler)
    _installed = True
