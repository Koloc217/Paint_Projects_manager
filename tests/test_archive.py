"""Data archive (archive.py): download through the app, restore through the app and from the console script."""
import base64
import io
import json
import os
import sys
import zipfile

from PIL import Image

import archive
import common
import database
from helpers import data, make_project, make_task

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import archive_tool     # noqa: E402


def _jpeg_url() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, "JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _download(client) -> bytes:
    res = client.get("/api/archive")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/zip"
    assert "project-hub-" in res.headers["content-disposition"]
    return res.content


def _restore(client, raw: bytes) -> dict:
    return client.post("/api/archive/restore", content=raw, headers={"Content-Type": "application/zip"}).json()


def _no_temp_left():
    left = [n for n in os.listdir(database.DATA_DIR) if n.startswith((".archive-", ".restore-", ".download-", ".upload-"))]
    assert left == []


def test_archive_contents(client):
    pid = make_project(client, name="Dreadnought")
    make_task(client, pid)
    assert client.post(f"/api/projects/{pid}/photo", json={"image": _jpeg_url()}).json()["status"] == "ok"
    assert client.post(f"/api/projects/{pid}/refs", json={"image": _jpeg_url()}).json()["status"] == "ok"

    raw = _download(client)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
        manifest = json.loads(zf.read("manifest.json"))
    assert {"manifest.json", "tracker.db", "config.json"} <= set(names)
    assert any(n.startswith("photos/refs/") for n in names)
    assert any(n.startswith("photos/") and n.count("/") == 1 for n in names)       # the cover
    assert not any(n.endswith((".env", "auth.json")) for n in names)
    assert manifest["projects"] == 1 and manifest["tasks"] == 1 and manifest["photos"] >= 2
    _no_temp_left()


def test_restore_round_trip(client, config):
    pid = make_project(client, name="Before")
    make_task(client, pid, name="Kept task")
    res = client.post(f"/api/projects/{pid}/photo", json={"image": _jpeg_url()}).json()
    assert res["status"] == "ok", res
    cover = res["photo"].rsplit("/", 1)[1]
    config(INITIAL_TOKENS=100, DATE_FORMAT="%d.%m.%Y")
    raw = _download(client)

    # change everything after the archive was made
    client.post(f"/api/projects/{pid}/rename", json={"name": "After"})
    make_project(client, name="Extra")
    config(INITIAL_TOKENS=5)
    os.remove(os.path.join(database.DATA_DIR, "photos", cover))

    res = _restore(client, raw)
    assert res["status"] == "ok", res
    assert res["projects"] == 1 and res["tasks"] == 1 and res["photos"] >= 1 and res["config"] is True
    names = [p["Name"] for p in data(client)["projects"]]
    assert names == ["Before"]
    assert os.path.isfile(os.path.join(database.DATA_DIR, "photos", cover))
    assert common.load_config()["INITIAL_TOKENS"] == 100
    assert os.path.isfile(os.path.join(database.DATA_DIR, "backups", res["saved_copy"]))
    _no_temp_left()


def test_restore_older_schema_is_migrated(client, tmp_path):
    make_project(client)
    raw = _download(client)
    # pretend the archive comes from an older version: version 2 database without the foreign keys step
    src = tmp_path / "a.zip"
    src.write_bytes(raw)
    with zipfile.ZipFile(src) as zf:
        zf.extract("tracker.db", tmp_path)
    import sqlite3
    conn = sqlite3.connect(tmp_path / "tracker.db")
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()
    out = tmp_path / "old.zip"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(out, "w") as zout:
        for name in zin.namelist():
            if name == "tracker.db":
                zout.write(tmp_path / "tracker.db", "tracker.db")
            else:
                zout.writestr(name, zin.read(name))
    assert _restore(client, out.read_bytes())["status"] == "ok"
    conn = sqlite3.connect(database.DB_PATH)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == archive.migrations.LATEST
    finally:
        conn.close()


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_bad_archives_change_nothing(client):
    pid = make_project(client, name="Safe")
    manifest = json.dumps({"app": "project-hub", "format": 1})
    cases = {
        "not a zip": b"hello",
        "foreign zip": _zip({"readme.txt": "x"}),
        "path escape": _zip({"manifest.json": manifest, "tracker.db": b"x", "photos/../../evil.py": "x"}),
        "newer format": _zip({"manifest.json": json.dumps({"app": "project-hub", "format": 99}), "tracker.db": b"x"}),
        "broken db": _zip({"manifest.json": manifest, "tracker.db": b"not a database at all" * 100}),
    }
    for label, raw in cases.items():
        res = _restore(client, raw)
        assert res["status"] == "error", label
        assert res["message"] and not res["message"].startswith("archive."), label
    assert [p["ID"] for p in data(client)["projects"]] == [pid]
    assert not os.path.exists(os.path.join(database.DATA_DIR, "evil.py"))
    _no_temp_left()


def test_console_script(client, tmp_path, capsys, monkeypatch):
    make_project(client, name="Console")
    out = tmp_path / "hub.zip"
    monkeypatch.setattr(sys, "argv", ["archive_tool.py", "create", str(out)])
    assert archive_tool.main() == 0 and out.is_file()
    make_project(client, name="Later")

    monkeypatch.setattr(sys, "argv", ["archive_tool.py", "check", str(out)])
    assert archive_tool.main() == 0
    monkeypatch.setattr(sys, "argv", ["archive_tool.py", "restore", str(out), "--no-config"])
    assert archive_tool.main() == 0
    assert [p["Name"] for p in data(client)["projects"]] == ["Console"]

    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"nope")
    monkeypatch.setattr(sys, "argv", ["archive_tool.py", "restore", str(bad)])
    assert archive_tool.main() == 1
    assert "❌" in capsys.readouterr().out
