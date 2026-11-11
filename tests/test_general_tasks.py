"""General tasks: tasks outside any project (tidy the paint shelves, service the airbrush...).

project_id is empty for them. They are created, listed, edited and closed like project tasks, and they show up
in every place that lists deadlines (journal, device API, calendar, vacation, the digest, the bot).
"""
import asyncio
import sqlite3
import sys
from datetime import date, timedelta

from hub.bot import bot
from hub.bot import bot_api
from hub.bot import bot_tasks
from hub.bot import bot_views
from hub.web import ics
from hub.core import migrations
from hub.jobs import notify
from hub.core.database import SessionLocal
from helpers import action, data, make_project, make_task, task

CHAT = 777


def day(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def make_general(client, name="Service the airbrush", deadline=None) -> str:
    res = client.post("/api/tasks", json={"project_id": None, "task_name": name, "deadline": deadline}).json()
    assert res["status"] == "ok", res
    return res["id"]


# ---------- web API ----------

def test_created_without_project(client):
    a = make_general(client)
    b = client.post("/api/tasks", json={"task_name": "Tidy the shelves"}).json()["id"]       # no field at all
    c = client.post("/api/tasks", json={"project_id": "", "task_name": "Buy primer"}).json()["id"]
    for tid in (a, b, c):
        assert task(client, tid)["Project ID"] is None


def test_unknown_project_is_still_an_error(client):
    assert client.post("/api/tasks", json={"project_id": "PRJ-NOPE", "task_name": "x"}).json()["status"] == "error"


def test_status_edit_delete(client):
    tid = make_general(client)
    assert action(client, action="update_task_status", id=tid, status="finished")["status"] == "ok"
    assert task(client, tid)["Status"] == "finished" and task(client, tid)["CompletedAt"]
    res = client.post(f"/api/tasks/{tid}/edit", json={"task_name": "Deep clean", "deadline": day(3), "spent": "30"})
    assert res.json()["status"] == "ok"
    t = task(client, tid)
    assert t["Task"] == "Deep clean" and t["Deadline"] == day(3) and t["Spent"] == 30
    client.delete(f"/api/tasks/{tid}")
    assert all(x["ID"] != tid for x in data(client)["tasks"])


def test_not_touched_by_project_trash_or_finish(client):
    pid = make_project(client)
    gid = make_general(client)
    action(client, action="update_project_status", id=pid, status="finished")
    assert task(client, gid)["Status"] == "not_started"
    client.delete(f"/api/projects/{pid}")
    client.delete(f"/api/trash/{pid}")
    assert task(client, gid)["Project ID"] is None


def test_journal_label(client):
    make_general(client, "Tidy the shelves")
    items = client.get("/api/activity").json()["items"]
    ev = next(i for i in items if "Tidy the shelves" in i["text"])
    assert ev["project_id"] is None and ev["project"] and ev["open"] is False


# ---------- deadlines elsewhere ----------

def test_device_api_includes_general(client, config):
    config(DEVICE_API={"enabled": True, "key": "", "hot_days": 3})
    make_general(client, "Shelves", deadline=day(-1))
    res = client.get("/api/v1/hot").json()
    assert res["mode"] == "hot"
    row = res["tasks"][0]
    assert row["name"] == "Shelves" and row["project_id"] is None and row["project"]


def test_calendar_feed_includes_general(client):
    make_general(client, "Shelves", deadline=day(5))
    db = SessionLocal()
    try:
        events = ics.collect(db)
        body = ics.build(db, "http://pi:8000").decode()
    finally:
        db.close()
    assert any(e[2] == "Shelves" and e[4] is None for e in events)
    assert "card=None" not in body


def test_vacation_moves_general_deadlines(client):
    tid = make_general(client, deadline=day(2))
    assert client.post("/api/vacation", json={"days": 7}).json()["status"] == "ok"
    assert task(client, tid)["Deadline"] == day(9)


def test_digest_lists_general(client, config, monkeypatch):
    config(TG_CHAT_ID="1")
    make_general(client, "Shelves", deadline=day(-2))
    sent = []
    monkeypatch.setattr(notify, "BOT_TOKEN", "x")
    monkeypatch.setattr(notify, "send_tg_message", lambda chat, text, markup=None, kind="bot": sent.append(text) or True)
    monkeypatch.setattr(sys, "argv", ["notify.py", "--now"])
    assert notify.main() == 0
    assert any("Shelves" in t for t in sent)


# ---------- migration ----------

V3_SCHEMA = """
CREATE TABLE projects (id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, status VARCHAR, stage VARCHAR, priority VARCHAR,
    estimate DATETIME, token FLOAT, token_reward FLOAT, paint_notes TEXT, photo VARCHAR, updated_at DATETIME,
    completed_at DATETIME, deleted_at DATETIME, extra_spent_min INTEGER DEFAULT 0);
CREATE TABLE tasks (id VARCHAR PRIMARY KEY, project_id VARCHAR NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    task_name VARCHAR NOT NULL, deadline DATETIME, status VARCHAR, postponed INTEGER, completed_at DATETIME,
    closed_from VARCHAR, spent_min INTEGER, spent_stage VARCHAR);
CREATE INDEX ix_tasks_project_id ON tasks (project_id);
INSERT INTO projects (id, name, status) VALUES ('PRJ-A', 'Alive', 'in_work');
INSERT INTO tasks (id, project_id, task_name, status, postponed, spent_min) VALUES
    ('TSK-1', 'PRJ-A', 'Prime', 'finished', 0, 45), ('TSK-2', 'PRJ-A', 'Paint', 'in_progress', 2, NULL);
PRAGMA user_version = 3;
"""


def test_v3_database_allows_empty_project(tmp_path):
    path = str(tmp_path / "tracker.db")
    con = sqlite3.connect(path)
    con.executescript(V3_SCHEMA)
    con.close()

    assert migrations.migrate(path, log=lambda *_: None) == migrations.LATEST
    con = sqlite3.connect(path)
    try:
        info = {r[1]: r for r in con.execute("PRAGMA table_info(tasks)")}
        assert info["project_id"][3] == 0                        # NOT NULL dropped
        assert con.execute("SELECT id, postponed, spent_min FROM tasks ORDER BY id").fetchall() == \
            [("TSK-1", 0, 45), ("TSK-2", 2, None)]
        assert any(r[2] == "projects" for r in con.execute("PRAGMA foreign_key_list(tasks)"))
        assert con.execute("SELECT 1 FROM sqlite_master WHERE name='ix_tasks_project_id'").fetchone()
        con.execute("INSERT INTO tasks (id, project_id, task_name) VALUES ('TSK-G', NULL, 'Shelves')")
    finally:
        con.close()


# ---------- bot ----------

def _tg(monkeypatch, config):
    config(TG_CHAT_ID=str(CHAT))
    calls = []

    async def fake_request(method, endpoint, payload=None, params=None, timeout_sec=20.0):
        calls.append((endpoint, payload))
        result = {"message_id": len(calls)} if endpoint.startswith("send") else True
        return {"ok": True, "result": result}

    monkeypatch.setattr(bot_api, "tg_request", fake_request)
    bot_tasks._task_drafts.clear()
    bot_tasks._time_drafts.clear()
    return calls


def say(text):
    asyncio.run(bot.process_update({"message": {"message_id": 1, "chat": {"id": CHAT, "type": "private"},
                                                "text": text}}))


def press(data_):
    asyncio.run(bot.process_update({"callback_query": {"id": "c", "data": data_,
                                                       "message": {"message_id": 5, "chat": {"id": CHAT}}}}))


def texts(calls):
    return [(p or {}).get("text", "") for _, p in calls]


def test_bot_builder_without_projects(client, monkeypatch, config):
    tg = _tg(monkeypatch, config)
    say("/task Tidy the shelves")
    press(f"tk_p:{bot_views.GENERAL}")
    press("tk_d:none")                                       # a general task may have no deadline
    tasks = data(client)["tasks"]
    assert [(t["Task"], t["Project ID"], t["Deadline"]) for t in tasks] == [("Tidy the shelves", None, None)]
    assert any("Tidy the shelves" in t for t in texts(tg))


def test_bot_general_view_and_buttons(client, monkeypatch, config):
    tg = _tg(monkeypatch, config)
    pid = make_project(client, status="in_work")
    make_task(client, pid, deadline=day(10))
    gid = make_general(client, "Shelves", deadline=day(1))

    say("/current_task")
    focus = tg[-1][1]
    assert "Shelves" in focus["text"]                         # urgent general task under the focus card
    assert any(b["callback_data"] == "gen_v" for row in focus["reply_markup"]["inline_keyboard"] for b in row)

    say("/general")
    assert "Shelves" in tg[-1][1]["text"]
    press(f"t_prog:{gid}:{bot_views.GENERAL}")
    assert task(client, gid)["Status"] == "in_progress"
    press(f"t_done:{gid}:{bot_views.GENERAL}")
    assert task(client, gid)["Status"] == "finished"
    press(f"t_postpone:{gid}")                                 # closed: no change, no error
    press("gen_v")
    assert tg
