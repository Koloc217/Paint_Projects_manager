"""The web app: assembles the FastAPI application. Routes live in hub/web/routes, one module per area.

Route handlers that touch the database or files are plain `def`: FastAPI runs them in its thread pool, so a slow
SD card or a long query never blocks the event loop shared with the Telegram bot and the scheduler. Handlers stay
`async def` only when they await something or start background tasks.
"""
import mimetypes
import os

import uvicorn
from fastapi import FastAPI

from hub.core import logs
from hub.core.database import ensure_schema
from hub.core.photofiles import PHOTOS_DIR
from hub.paths import BASE_DIR
from hub.web import auth
from hub.web.lifespan import lifespan
from hub.web.routes import ROUTERS
from hub.web.static import CachedStatic
from hub.web.templating import templates

ensure_schema()

app = FastAPI(lifespan=lifespan)
app.mount("/static", CachedStatic(directory=os.path.join(BASE_DIR, "static")), name="static")
# Python before 3.13 and Windows registries often don't know .avif: without this the photos would go out as
# text/plain or application/octet-stream and some browsers would refuse to show them
mimetypes.add_type("image/avif", ".avif")
app.mount("/photos", CachedStatic(directory=PHOTOS_DIR, policy="photos"), name="photos")
auth.setup(app, templates)               # password login: off by default (Settings -> Access)
for router in ROUTERS:
    app.include_router(router)


def _start_args():
    """python app.py [--port N] [--host ADDR]: override PORT / HOST from .env for this run."""
    import argparse
    ap = argparse.ArgumentParser(description="Project Hub: web app, Telegram bot and scheduler in one process")
    ap.add_argument("--port", help="port of the web app (default: PORT in .env, otherwise 8000)")
    ap.add_argument("--host", help="address to listen on (default: HOST in .env, otherwise 0.0.0.0 = all networks)")
    return ap.parse_args()


def main() -> None:
    from hub.web import tls
    args = _start_args()
    try:
        options = tls.uvicorn_options(port=args.port, host=args.host)   # PORT, HOST, HTTPS in .env (see tls.py)
    except tls.TlsError as e:
        raise SystemExit("Start-up settings error: " + str(e))
    logs.setup_app_log()
    scheme = "https" if "ssl_certfile" in options else "http"
    shown = "localhost" if options["host"] in ("0.0.0.0", "::") else options["host"]
    print(f"Project Hub: {scheme}://{shown}:{options['port']}  (listening on {options['host']}:{options['port']};"
          f" change with PORT / HOST in .env or --port / --host)")
    uvicorn.run("hub.web.server:app", **options)


if __name__ == "__main__":
    main()
