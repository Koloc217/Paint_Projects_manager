"""tools/check_backup.py: a backup copy restores; a broken file is reported. tools/make_thumbs.py: previews."""
import base64
import io
import os
import sqlite3
import sys

from PIL import Image

from hub.core import database
from hub.core import migrations
from helpers import make_project, make_task, project

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import check_backup     # noqa: E402
import make_thumbs      # noqa: E402


def _backup_to(path):
    src, dst = sqlite3.connect(database.DB_PATH), sqlite3.connect(path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def test_good_backup_restores(client, tmp_path):
    pid = make_project(client)
    make_task(client, pid)
    copy = str(tmp_path / "tracker-20261101-120000.db")
    _backup_to(copy)
    lines = []
    assert check_backup.check(copy, database.DATA_DIR, out=lines.append) is True
    text = "\n".join(lines)
    assert "✅ File is intact" in text and "✅ Readable by the app" in text
    row = next(l for l in lines if l.startswith("projects"))
    assert row.split()[1] == "1"


def test_old_backup_is_migrated_on_the_copy_only(client, tmp_path):
    make_project(client)
    copy = str(tmp_path / "old.db")
    _backup_to(copy)
    con = sqlite3.connect(copy)
    con.execute("PRAGMA user_version = 0")             # pretend it was made before versioned migrations
    con.commit()
    con.close()
    assert check_backup.check(copy, database.DATA_DIR, out=lambda *_: None) is True
    con = sqlite3.connect(copy)
    assert con.execute("PRAGMA user_version").fetchone()[0] == 0      # the backup itself is untouched
    con.close()


def test_broken_backup_fails(client, tmp_path):
    bad = tmp_path / "broken.db"
    bad.write_bytes(b"this is not a database" * 100)
    lines = []
    assert check_backup.check(str(bad), database.DATA_DIR, out=lines.append) is False
    assert any("❌" in l for l in lines)


def test_make_thumbs_for_old_cover(client):
    pid = make_project(client)
    buf = io.BytesIO()
    Image.new("RGB", (1200, 900), (90, 10, 10)).save(buf, "JPEG")
    image = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    client.post(f"/api/projects/{pid}/photo", json={"image": image})      # an old upload: no preview
    p = project(client, pid)
    assert p["PhotoThumb"] == p["Photo"]
    assert make_thumbs.main() == 0
    p = project(client, pid)
    assert p["PhotoThumb"].endswith("_t.jpg")
    with Image.open(os.path.join(database.DATA_DIR, "photos", os.path.basename(p["PhotoThumb"]))) as im:
        assert max(im.size) == make_thumbs.MAX_SIDE


def test_latest_version_constant():
    assert migrations.LATEST == len(migrations.STEPS)
