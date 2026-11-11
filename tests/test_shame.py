"""Shame metric: deadline postponements (text only, no token penalty)."""
import sys
from datetime import date, datetime, timedelta

from hub.core import activity
from hub.jobs import notify
from hub.core.database import SessionLocal
from helpers import data, make_project, make_task, task


def day(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def edit(client, tid, deadline, name="Prime"):
    assert client.post(f"/api/tasks/{tid}/edit", json={"task_name": name, "deadline": deadline}).json()["status"] == "ok"


def test_web_edit_later_counts(client):
    pid = make_project(client, status="in_work")
    tid = make_task(client, pid, deadline=day(2))
    tokens = data(client)["total_tokens"]
    edit(client, tid, day(5))                 # later: +1
    edit(client, tid, day(4))                 # earlier: no
    edit(client, tid, day(4))                 # same: no
    edit(client, tid, day(9))                 # later: +1
    assert task(client, tid)["Postponed"] == 2
    assert data(client)["postponed_30d"] == 2
    assert data(client)["total_tokens"] == tokens          # no penalty


def test_first_deadline_and_closed_task_do_not_count(client):
    tid = client.post("/api/tasks", json={"task_name": "Shelves"}).json()["id"]
    edit(client, tid, day(3), "Shelves")       # first deadline
    assert task(client, tid)["Postponed"] == 0
    client.post("/api/action", json={"action": "update_task_status", "id": tid, "status": "finished"})
    edit(client, tid, day(10), "Shelves")
    assert task(client, tid)["Postponed"] == 0


def test_counted_from_journal_after_delete(client):
    tid = client.post("/api/tasks", json={"task_name": "Shelves", "deadline": day(1)}).json()["id"]
    edit(client, tid, day(6), "Shelves")
    client.delete(f"/api/tasks/{tid}")
    db = SessionLocal()
    try:
        assert activity.count_postponements(db, datetime.now() - timedelta(days=1)) == 1
        assert activity.count_postponements(db, datetime.now() + timedelta(days=1)) == 0
    finally:
        db.close()


def test_vacation_is_not_a_postponement(client):
    client.post("/api/tasks", json={"task_name": "Shelves", "deadline": day(1)})
    assert client.post("/api/vacation", json={"days": 5}).json()["status"] == "ok"
    assert data(client)["postponed_30d"] == 0


def test_friday_digest_shame_line(client, config, monkeypatch):
    config(TG_CHAT_ID="1")
    tid = client.post("/api/tasks", json={"task_name": "Shelves", "deadline": day(-1)}).json()["id"]
    edit(client, tid, day(0), "Shelves")
    sent = []
    monkeypatch.setattr(notify, "BOT_TOKEN", "x")
    monkeypatch.setattr(notify, "send_tg_message", lambda chat, text, markup=None, kind="bot": sent.append(text) or True)
    monkeypatch.setattr(sys, "argv", ["notify.py", "--test"])
    notify.main()
    assert any("🐌" in t for t in sent)


def test_snark_tiers():
    texts = {notify.shame_snark(n) for n in (1, 2, 5, 50)}
    assert len(texts) == 4
