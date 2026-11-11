"""API for external devices: an e-ink frame, ESP32 screens (see devices.py)."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from hub.web import auth, devices
from hub.web.deps import get_db

router = APIRouter()


# ---------------------------------------------------------------- API for external devices (devices.py)
def _device_guard(request: Request, key: str):
    """None if the device may read; otherwise the response to return. Off: 404, as if the address did not exist."""
    if not devices.enabled():
        return Response(status_code=404)
    sent = request.headers.get("X-Device-Key") or key or None
    if not devices.key_ok(sent, auth.is_enabled()):
        return JSONResponse({"status": "error", "code": "device_key"}, status_code=401)
    return None


@router.get("/api/v1/hot")
def device_hot(request: Request, limit: int = 2, key: str = "", db: Session = Depends(get_db)):
    """1-2 hot tasks for a small screen (e-ink frame, ESP32); the device does the layout."""
    denied = _device_guard(request, key)
    if denied is not None:
        return denied
    return JSONResponse(devices.hot(db, limit), headers={"Cache-Control": "no-store"})
