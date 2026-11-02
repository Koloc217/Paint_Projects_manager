"""Order of progress photos in the bot viewer: No. 1 is the oldest photo, like the timeline in the project card."""
import io
from datetime import datetime, timedelta

from PIL import Image

import bot_photos
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

    first = bot_photos._photo_view(pid, 0)
    assert first["id"] == oldest and first["index"] == 0 and first["total"] == 3
    nav = first["markup"]["inline_keyboard"][0]
    assert nav[1]["text"] == "1/3"
    assert nav[0]["callback_data"] == f"ph_v:{pid}:2"      # ◀ from No. 1 wraps around to the newest
    assert nav[2]["callback_data"] == f"ph_v:{pid}:1"      # ▶ goes to the next, newer photo

    assert bot_photos._photo_view(pid, 1)["id"] == middle
    last = bot_photos._photo_view(pid, -1)                         # "open photos" after an upload, /photo
    assert last["id"] == newest and last["index"] == 2
    assert last["markup"]["inline_keyboard"][0][1]["text"] == "3/3"


def _jpeg(size) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (20, 90, 160)).save(buf, "JPEG")
    return buf.getvalue()


SIZES = [   # what Telegram sends for one photo
    {"file_id": "s90", "width": 90, "height": 68, "file_size": 1000},
    {"file_id": "s320", "width": 320, "height": 240, "file_size": 15000},
    {"file_id": "s800", "width": 800, "height": 600, "file_size": 60000},
    {"file_id": "s1280", "width": 1280, "height": 960, "file_size": 120000},
]


def test_preview_size_is_picked():
    assert bot_photos._extract_image({"photo": SIZES}) == ("s1280", 120000, "s320")
    assert bot_photos._extract_image({"photo": SIZES[:1]}) == ("s90", 1000, None)            # nothing smaller
    doc = {"document": {"file_id": "d", "mime_type": "image/png", "file_size": 5}}
    assert bot_photos._extract_image(doc) == ("d", 5, None)


def test_telegram_photo_saved_with_preview(client, monkeypatch):
    import asyncio
    import bot_api
    files = {"s1280": _jpeg((1280, 960)), "s320": _jpeg((320, 240))}

    async def fake_download(fid):
        if fid not in files:
            raise RuntimeError("gone")
        return files[fid]
    monkeypatch.setattr(bot_api, "download_file", fake_download)

    pid = make_project(client)
    ids, *_ = asyncio.run(bot_photos.save_telegram_photos(pid, [("s1280", "s320")], "wip"))
    ph = client.get("/api/data").json()["projects"][0]["Progress"][0]
    assert ph["ID"] == ids[0] and ph["Thumb"].endswith("_t.jpg") and ph["Thumb"] != ph["Url"]

    # the preview fails: the photo is still saved and shown by itself
    ids, *_ = asyncio.run(bot_photos.save_telegram_photos(pid, [("s1280", "missing")], ""))
    ph = next(p for p in client.get("/api/data").json()["projects"][0]["Progress"] if p["ID"] == ids[0])
    assert ph["Thumb"] == ph["Url"]
    # old format (a bare file_id) still works
    assert asyncio.run(bot_photos.save_telegram_photos(pid, ["s1280"], ""))[0]
