"""Deadlines for a phone: a .ics file and an optional subscription address (see ics.py)."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from hub.core.i18n import tr
from hub.web import ics
from hub.web.deps import get_db
from hub.web.responses import fail
from hub.web.schemas import CalendarFeedBody

router = APIRouter()


def ics_response(data: bytes, download: bool) -> Response:
    headers = {"Cache-Control": "no-store"}
    if download:
        headers["Content-Disposition"] = 'attachment; filename="deadlines.ics"'
    return Response(content=data, media_type="text/calendar; charset=utf-8", headers=headers)


@router.get("/api/calendar.ics")
def calendar_file(request: Request, db: Session = Depends(get_db)):
    if not ics.enabled():
        return Response(status_code=404)
    return ics_response(ics.build(db, str(request.base_url)), download=True)


@router.get("/api/calendar/feed")
def calendar_feed_state():
    return ics.feed_state()


@router.post("/api/calendar/feed")
def calendar_feed_set(body: CalendarFeedBody):
    if not ics.enabled():
        return fail(tr("calexp.err_off"))
    return {"status": "ok", **ics.set_feed(body.enabled, body.new_token)}


@router.get("/cal/{name}", include_in_schema=False)
def calendar_feed(name: str, request: Request, db: Session = Depends(get_db)):
    """Public (no login, see auth.py): the token in the address is the password. Off or wrong token: 404."""
    if not name.endswith(".ics") or not ics.token_ok(name[:-4]):
        return Response(status_code=404)
    return ics_response(ics.build(db, str(request.base_url)), download=False)
