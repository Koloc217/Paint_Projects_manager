"""Browser caching of static files and photos, and versioned /static addresses."""
import os

from fastapi.staticfiles import StaticFiles

from hub.paths import BASE_DIR

# ---------- Browser caching of static files and photos ----------
# /static/...?v=<stamp>  (pages link to them through static_url()): a new address after every change of the file,
#                         so the browser keeps the file for a year and never asks again;
# /static/... without v  (fonts and images referenced from CSS, the service worker list): kept for a day for
#                         fonts and icons, otherwise checked with the server every time (a cheap 304 when unchanged);
# /photos/...            every upload gets a new random file name and files are never rewritten: kept for a year.
YEAR = "public, max-age=31536000, immutable"
_LONG_UNVERSIONED = (".woff2", ".woff", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico")


class CachedStatic(StaticFiles):
    def __init__(self, *args, policy="static", **kwargs):
        super().__init__(*args, **kwargs)
        self.policy = policy

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code in (200, 304):
            if self.policy == "photos" or b"v=" in (scope.get("query_string") or b""):
                response.headers["Cache-Control"] = YEAR
            elif path.lower().endswith(_LONG_UNVERSIONED):
                response.headers["Cache-Control"] = "public, max-age=86400"
            else:
                response.headers["Cache-Control"] = "no-cache"
        return response


_static_stamps = {}


def static_url(path: str) -> str:
    """/static/<path>?v=<stamp>: the stamp changes when the file changes (modification time and size)."""
    full = os.path.join(BASE_DIR, "static", path)
    try:
        st = os.stat(full)
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return f"/static/{path}"
    cached = _static_stamps.get(path)
    if not cached or cached[0] != key:
        cached = (key, format(hash(key) & 0xFFFFFFFF, "08x"))
        _static_stamps[path] = cached
    return f"/static/{path}?v={cached[1]}"
