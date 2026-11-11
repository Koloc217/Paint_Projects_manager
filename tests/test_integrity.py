"""Photo files without a row go to quarantine, files in use never do; cover previews; browser caching."""
import base64
import io
import os
import time
from datetime import datetime, timedelta

from PIL import Image

from hub.core import integrity
from hub.core.database import SessionLocal
from helpers import make_project, project


def _jpeg(size=(40, 30)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 120, 200)).save(buf, "JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _age(path, seconds):
    t = time.time() - seconds
    os.utime(path, (t, t))


def _touch(folder, name, age=7200):
    path = os.path.join(folder, name)
    with open(path, "wb") as f:
        f.write(b"x")
    _age(path, age)
    return path


def _orphans():
    db = SessionLocal()
    try:
        return integrity.find_orphan_files(db)
    finally:
        db.close()


def test_cover_with_preview_and_cleanup(client):
    pid = make_project(client)
    res = client.post(f"/api/projects/{pid}/photo", json={"image": _jpeg((800, 600)), "thumb": _jpeg()}).json()
    assert res["status"] == "ok", res
    p = project(client, pid)
    assert p["PhotoThumb"].endswith("_t.jpg") and p["PhotoThumb"] != p["Photo"]
    cover = os.path.join(integrity.PHOTOS_DIR, os.path.basename(p["Photo"]))
    thumb = os.path.join(integrity.PHOTOS_DIR, os.path.basename(p["PhotoThumb"]))
    _age(cover, 7200)
    _age(thumb, 7200)
    assert _orphans() == {}                                         # both belong to the project

    # replacing the cover removes the old one and its preview
    client.post(f"/api/projects/{pid}/photo", json={"image": _jpeg()})
    assert not os.path.exists(cover) and not os.path.exists(thumb)
    p = project(client, pid)
    assert p["PhotoThumb"] == p["Photo"]                            # no preview sent: the cover itself


def test_orphans_go_to_quarantine(client, tmp_path):
    pid = make_project(client)
    client.post(f"/api/projects/{pid}/refs", json={"image": _jpeg(), "thumb": _jpeg()})
    for name in os.listdir(integrity.FOLDERS["refs"]):
        _age(os.path.join(integrity.FOLDERS["refs"], name), 7200)
    lost = _touch(integrity.FOLDERS["refs"], "PRJ-DEAD00_aaaa.png")
    fresh = _touch(integrity.FOLDERS["progress"], "PRJ-DEAD00_bbbb.jpg", age=10)    # an upload in progress
    foreign = _touch(integrity.FOLDERS["covers"], "my-notes.txt")                    # not an app file name

    found = _orphans()
    assert lost in found.get("refs", []) and fresh not in found.get("progress", [])

    db = SessionLocal()
    try:
        res = integrity.cleanup_orphan_files(db)
    finally:
        db.close()
    assert res["moved"] >= 1
    assert not os.path.exists(lost) and os.path.exists(fresh) and os.path.exists(foreign)
    day = datetime.now().strftime("%Y%m%d")
    assert "PRJ-DEAD00_aaaa.png" in os.listdir(os.path.join(integrity.ORPHANS_DIR, day, "refs"))
    assert len(project(client, pid)["Refs"]) == 1                   # the project's reference is untouched

    # old quarantine days are erased, recent ones kept
    assert integrity.purge_quarantine(now=datetime.now() + timedelta(days=5)) == 0
    assert integrity.purge_quarantine(now=datetime.now() + timedelta(days=31)) == 1
    assert not os.path.exists(os.path.join(integrity.ORPHANS_DIR, day))
    os.remove(fresh)
    os.remove(foreign)


def test_trashed_project_files_are_kept(client):
    pid = make_project(client)
    client.post(f"/api/projects/{pid}/refs", json={"image": _jpeg()})
    for name in os.listdir(integrity.FOLDERS["refs"]):
        _age(os.path.join(integrity.FOLDERS["refs"], name), 7200)
    client.delete(f"/api/projects/{pid}")                           # to the trash, can still be restored
    mine = [path for paths in _orphans().values() for path in paths if os.path.basename(path).startswith(pid)]
    assert mine == []


def test_purge_removes_rows_and_files(client):
    pid = make_project(client)
    client.post(f"/api/projects/{pid}/refs", json={"image": _jpeg()})
    client.delete(f"/api/projects/{pid}")
    assert client.delete(f"/api/trash/{pid}").json()["status"] == "ok"
    db = SessionLocal()
    try:
        assert integrity.foreign_key_problems(db) == []
        assert integrity.find_missing_files(db) == {}
    finally:
        db.close()
    assert [n for n in os.listdir(integrity.FOLDERS["refs"]) if n.startswith(pid)] == []


def test_cache_headers(client):
    from hub.web.static import static_url
    url = static_url("app.js")
    assert "?v=" in url
    assert "immutable" in client.get(url).headers["cache-control"]
    assert client.get("/static/app.js").headers["cache-control"] == "no-cache"
    assert "max-age=86400" in client.get("/static/icons/favicon-32.png").headers["cache-control"]
    pid = make_project(client)
    client.post(f"/api/projects/{pid}/photo", json={"image": _jpeg()})
    assert "immutable" in client.get(project(client, pid)["Photo"]).headers["cache-control"]
    assert url in client.get("/").text                              # pages link to the versioned address


def test_data_etag(client):
    first = client.get("/api/data")
    etag = first.headers["etag"]
    assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
    same = client.get("/api/data", headers={"If-None-Match": etag})
    assert same.status_code == 304 and same.content == b""
    make_project(client)                                            # any change gives a new ETag
    changed = client.get("/api/data", headers={"If-None-Match": etag})
    assert changed.status_code == 200 and changed.headers["etag"] != etag
