"""Optional check on a COPY of a real database: migration runs and the main page data loads.

Skipped unless PROJECT_HUB_TEST_DB points to a database file, e.g.

    PROJECT_HUB_TEST_DB=/path/to/tracker.db python -m pytest tests/test_real_db.py

The file itself is never opened for writing: it is copied into the test folder first. The database holds no
secrets (the bot token and proxy live in .env, settings in config.json, and neither is copied).
"""
import os
import sqlite3

import pytest

import database
from conftest import clear_database, write_config

SOURCE = os.environ.get("PROJECT_HUB_TEST_DB")


@pytest.mark.skipif(not SOURCE, reason="PROJECT_HUB_TEST_DB is not set")
def test_real_database_copy(client):
    assert os.path.isfile(SOURCE), f"no such file: {SOURCE}"
    database.engine.dispose()                     # release the test database before replacing it
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(database.DB_PATH + suffix)
        except FileNotFoundError:
            pass
    # a consistent copy even if the app is running and writing to it (WAL)
    src, dst = sqlite3.connect(f"file:{SOURCE}?mode=ro", uri=True), sqlite3.connect(database.DB_PATH)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()

    try:
        database.ensure_schema()
        write_config()
        res = client.get("/api/data")
        assert res.status_code == 200
        d = res.json()
        from common import PROJECT_STATUSES, STAGES, TASK_STATUSES
        assert {p["Status"] for p in d["projects"]} <= set(PROJECT_STATUSES), "unknown project status after migration"
        assert {p["Stage"] for p in d["projects"]} <= set(STAGES), "unknown stage after migration"
        assert {t["Status"] for t in d["tasks"]} <= set(TASK_STATUSES), "unknown task status after migration"
        assert client.get("/api/activity").status_code == 200
    finally:
        database.engine.dispose()
        clear_database()                           # the next tests start empty again
