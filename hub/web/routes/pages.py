"""The page itself and the PWA files."""
import os

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse

from hub.core.common import load_config
from hub.core.i18n import get_lang, table_for
from hub.paths import BASE_DIR
from hub.web import ics
from hub.web.templating import templates

router = APIRouter()


# PWA (install on a phone home screen). The service worker and the manifest are served from the site root:
# a service worker only controls URLs below its own, so from /static/ it could not serve the main page.
@router.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(os.path.join(BASE_DIR, "static", "sw.js"), media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


@router.get("/manifest.webmanifest", include_in_schema=False)
def web_manifest():
    return FileResponse(os.path.join(BASE_DIR, "static", "manifest.webmanifest"),
                        media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    lang = get_lang()
    return templates.TemplateResponse(
        request=request, name="index.html",
        context={"lang": lang, "i18n": table_for(lang), "date_format": load_config()["DATE_FORMAT"],
                 "calendar_export": ics.enabled()},
    )
