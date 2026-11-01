"""Versioned migrations (migrations.py) on a database made by an older version of the app."""
import os
import sqlite3

import pytest

import database
import migrations

# Schema of a database from before versioned migrations: no foreign keys, no time columns, no refs position.
OLD_SCHEMA = """
CREATE TABLE projects (id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, status VARCHAR, stage VARCHAR, priority VARCHAR,
    estimate DATETIME, token FLOAT, token_reward FLOAT, paint_notes TEXT, photo VARCHAR, updated_at DATETIME,
    completed_at DATETIME, deleted_at DATETIME);
CREATE TABLE tasks (id VARCHAR PRIMARY KEY, project_id VARCHAR NOT NULL, task_name VARCHAR NOT NULL, deadline DATETIME,
    status VARCHAR, postponed INTEGER, completed_at DATETIME, closed_from VARCHAR);
CREATE INDEX ix_tasks_project_id ON tasks (project_id);
CREATE TABLE project_refs (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id VARCHAR NOT NULL, filename VARCHAR NOT NULL,
    thumb VARCHAR, created_at DATETIME);
CREATE INDEX ix_project_refs_project_id ON project_refs (project_id);
CREATE TABLE progress_photos (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id VARCHAR NOT NULL,
    filename VARCHAR NOT NULL, thumb VARCHAR, stage VARCHAR, note TEXT, source VARCHAR, created_at DATETIME);
CREATE TABLE adjustments (id INTEGER PRIMARY KEY AUTOINCREMENT, amount FLOAT, reason VARCHAR, created_at DATETIME);
INSERT INTO projects (id, name, status, stage, priority, token, token_reward) VALUES
    ('PRJ-A', 'Alive', 'in_work', 'painting', 'High', 10, 15.4);
INSERT INTO tasks (id, project_id, task_name, status, postponed) VALUES
    ('TSK-1', 'PRJ-A', 'Prime', 'finished', 0),
    ('TSK-2', 'PRJ-A', 'Paint', 'in_progress', 2),
    ('TSK-X', 'PRJ-GONE', 'Orphan', 'not_started', 0);
INSERT INTO project_refs (project_id, filename, thumb) VALUES ('PRJ-A', 'a.png', 'a_t.jpg'), ('PRJ-GONE', 'b.png', '');
INSERT INTO progress_photos (project_id, filename, stage, source) VALUES ('PRJ-A', 'p.jpg', 'priming', 'web');
"""


@pytest.fixture
def old_db(tmp_path):
    path = str(tmp_path / "tracker.db")
    con = sqlite3.connect(path)
    con.executescript(OLD_SCHEMA)
    con.close()
    return path


def _q(path, sql):
    con = sqlite3.connect(path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_old_database_is_migrated(old_db, tmp_path):
    logs = []
    assert migrations.migrate(old_db, log=logs.append) == migrations.LATEST
    assert _q(old_db, "PRAGMA user_version")[0][0] == migrations.LATEST

    # data kept, orphans of a missing project dropped
    assert sorted(r[0] for r in _q(old_db, "SELECT id FROM tasks")) == ["TSK-1", "TSK-2"]
    assert _q(old_db, "SELECT filename FROM project_refs") == [("a.png",)]
    assert _q(old_db, "SELECT postponed FROM tasks WHERE id='TSK-2'") == [(2,)]
    assert _q(old_db, "SELECT token_reward FROM projects") == [(15.0,)]        # tokens rounded (baseline step)
    # new columns
    assert "spent_min" in [r[1] for r in _q(old_db, "PRAGMA table_info(tasks)")]
    assert "extra_spent_min" in [r[1] for r in _q(old_db, "PRAGMA table_info(projects)")]
    assert "position" in [r[1] for r in _q(old_db, "PRAGMA table_info(project_refs)")]
    # foreign keys and the index are in place
    for table in ("tasks", "project_refs", "progress_photos"):
        fks = _q(old_db, f"PRAGMA foreign_key_list({table})")
        assert fks and fks[0][2] == "projects" and fks[0][6] == "CASCADE", table
    assert "ix_tasks_project_id" in [r[1] for r in _q(old_db, "PRAGMA index_list(tasks)")]
    # a copy was saved before migrating
    copies = os.listdir(tmp_path / "backups")
    assert len(copies) == 1 and copies[0].startswith("tracker-before-v1-")


def test_second_run_does_nothing(old_db, tmp_path):
    migrations.migrate(old_db, log=lambda *_: None)
    logs = []
    assert migrations.migrate(old_db, log=logs.append) == migrations.LATEST
    assert logs == []
    assert len(os.listdir(tmp_path / "backups")) == 1           # no new copy when nothing is pending


def test_cascade_delete(old_db):
    migrations.migrate(old_db, log=lambda *_: None)
    con = sqlite3.connect(old_db)
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("DELETE FROM projects WHERE id='PRJ-A'")
    con.commit()
    assert con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM progress_photos").fetchone()[0] == 0
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO tasks (id, project_id, task_name) VALUES ('T', 'NOPE', 'x')")
    con.close()


def test_failed_step_rolls_back(old_db, monkeypatch):
    def boom(conn):
        conn.execute("ALTER TABLE tasks ADD COLUMN half_done INTEGER")
        raise RuntimeError("boom")
    monkeypatch.setattr(migrations, "STEPS", [migrations.step_1_baseline, boom])
    monkeypatch.setattr(migrations, "LATEST", 2)
    with pytest.raises(RuntimeError):
        migrations.migrate(old_db, log=lambda *_: None)
    assert _q(old_db, "PRAGMA user_version")[0][0] == 1          # step 1 kept, step 2 not applied
    assert "half_done" not in [r[1] for r in _q(old_db, "PRAGMA table_info(tasks)")]


def test_newer_database_is_left_alone(old_db):
    con = sqlite3.connect(old_db)
    con.execute(f"PRAGMA user_version = {migrations.LATEST + 5}")
    con.close()
    logs = []
    assert migrations.migrate(old_db, log=logs.append) == migrations.LATEST + 5
    assert logs and "rolled back" in logs[0]


def test_app_database_is_current(client):
    assert _q(database.DB_PATH, "PRAGMA user_version")[0][0] == migrations.LATEST
    with database.engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
