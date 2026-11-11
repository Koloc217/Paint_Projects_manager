"""An exclusive lock on a file, shared between processes."""
try:
    import fcntl          # Linux / Raspberry Pi
except ImportError:
    fcntl = None
try:
    import msvcrt         # Windows
except ImportError:
    msvcrt = None


class FileLock:
    """Exclusive lock on a file between processes: fcntl on Linux, msvcrt on Windows."""

    def __init__(self, path):
        self.path = path
        self.f = None

    def __enter__(self):
        self.f = open(self.path, "a+")
        if fcntl:
            fcntl.flock(self.f, fcntl.LOCK_EX)        # a second process waits here
        elif msvcrt:
            self.f.seek(0)
            while True:
                try:
                    msvcrt.locking(self.f.fileno(), msvcrt.LK_LOCK, 1)   # retries for ~10 s, then raises OSError
                    break
                except OSError:
                    continue
        return self

    def __exit__(self, *exc):
        try:
            if fcntl:
                fcntl.flock(self.f, fcntl.LOCK_UN)
            elif msvcrt:
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self.f.close()
