"""Requests that change data must come from the app's own pages.

A browser sends cookies and reaches home-network addresses for any site the user opens, so without this check a
foreign page could POST to the hub (restore an archive over the data, unlink the bot) just by knowing its address.
Every browser adds an Origin header to such requests; it must name the same host and port the request was sent to.

Requests without an Origin header are let through: scripts, curl and devices are not browsers and cannot be
tricked this way. GET requests never change data and are not checked.
"""
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

from hub.core.i18n import tr

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def same_origin(origin: str, host: str) -> bool:
    """True if the Origin header names the host (and port) the request was addressed to. The scheme is not
    compared: behind a reverse proxy the app sees http while the browser used https."""
    try:
        netloc = urlsplit(origin).netloc
    except ValueError:
        return False
    return bool(netloc) and netloc.lower() == (host or "").lower()


def foreign_request(method: str, headers) -> bool:
    """True for a data-changing request a browser sent from another site."""
    if method.upper() in SAFE_METHODS:
        return False
    origin = headers.get("origin")
    if origin is not None:
        return not same_origin(origin, headers.get("host", ""))
    # no Origin: an old browser may still say where the request came from
    return headers.get("sec-fetch-site", "").lower() == "cross-site"


def setup(app) -> None:
    @app.middleware("http")
    async def origin_guard(request: Request, call_next):
        if foreign_request(request.method, request.headers):
            return JSONResponse({"status": "error", "code": "cross_origin", "message": tr("err.cross_origin")},
                                status_code=403)
        return await call_next(request)
