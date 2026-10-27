"""Order of progress photos in the bot viewer: No. 1 is the oldest photo, like the timeline in the project card."""
import io
from datetime import datetime, timedelta

from PIL import Image

import bot
import progress
from database import SessionLocal
from helpers import make_project


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 50, 50)).save(buf, "PNG")
    return buf.getvalue()


def _add(project_id: str, note: str, created_at: datetime) -> int:
    db = SessionLocal()
    try:
        row = progress.add_photo(db, project_id, _png(), None, note, "telegram")
        row.created_at = created_at
        db.commit()
        return row.id
    finally:
        db.close()


def test_photo_view_counts_from_the_oldest(client):
    pid = make_project(client)
    base = datetime(2026, 10, 1, 12, 0)
    # added out of order on purpose: the order must follow the photo dates
    newest = _add(pid, "third", base + timedelta(days=2))
    oldest = _add(pid, "first", base)
    middle = _add(pid, "second", base + timedelta(days=1))

    first = bot._photo_view(pid, 0)
    assert first["id"] == oldest and first["index"] == 0 and first["total"] == 3
    nav = first["markup"]["inline_keyboard"][0]
    assert nav[1]["text"] == "1/3"
    assert nav[0]["callback_data"] == f"ph_v:{pid}:2"      # ◀ from No. 1 wraps around to the newest
    assert nav[2]["callback_data"] == f"ph_v:{pid}:1"      # ▶ goes to the next, newer photo

    assert bot._photo_view(pid, 1)["id"] == middle
    last = bot._photo_view(pid, -1)                         # "open photos" after an upload, /photo
    assert last["id"] == newest and last["index"] == 2
    assert last["markup"]["inline_keyboard"][0][1]["text"] == "3/3"
