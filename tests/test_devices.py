"""Device API (/api/v1/hot): which tasks a small screen gets, the "nearest" and "empty" fallbacks, access rules."""
from datetime import date, timedelta

import auth
from helpers import action, make_project, make_task

ON = {"enabled": True, "key": "", "hot_days": 3}


def day(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def hot(client, **params):
    res = client.get("/api/v1/hot", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def test_off_by_default(client):
    assert client.get("/api/v1/hot").status_code == 404


def test_overdue_first_then_soon_limited_to_two(client, config):
    config(DEVICE_API=ON)
    pid = make_project(client, name="Knight")
    make_task(client, pid, "soon", deadline=day(2))
    make_task(client, pid, "overdue", deadline=day(-1))
    make_task(client, pid, "today", deadline=day(0))
    make_task(client, pid, "far", deadline=day(20))
    make_task(client, pid, "no deadline")

    res = hot(client)
    assert res["mode"] == "hot" and res["message"] == ""
    assert [t["name"] for t in res["tasks"]] == ["overdue", "today"]
    first = res["tasks"][0]
    assert first["overdue"] is True and first["days_left"] == -1
    assert first["project"] == "Knight" and first["project_id"] == pid and first["deadline"] == day(-1)
    assert [t["name"] for t in hot(client, limit=5)["tasks"]] == ["overdue", "today", "soon"]


def test_nearest_when_nothing_is_hot(client, config):
    config(DEVICE_API=ON)
    pid = make_project(client)
    make_task(client, pid, "later", deadline=day(40))
    make_task(client, pid, "next", deadline=day(10))
    make_task(client, pid, "no deadline")
    res = hot(client)
    assert res["mode"] == "nearest"
    assert [(t["name"], t["overdue"]) for t in res["tasks"]] == [("next", False)]


def test_empty_when_no_deadlines(client, config):
    config(DEVICE_API=ON)
    pid = make_project(client)
    make_task(client, pid, "no deadline")
    res = hot(client)
    assert res == {"mode": "empty", "today": date.today().isoformat(), "tasks": [],
                   "message": "Задач нет, проверь задачи"}


def test_closed_paused_and_trashed_left_out(client, config):
    config(DEVICE_API=ON)
    done = make_project(client, name="Done task")
    tid = make_task(client, done, "closed", deadline=day(0))
    action(client, action="update_task_status", id=tid, status="finished")

    paused = make_project(client, name="Paused")
    make_task(client, paused, "paused", deadline=day(0))
    action(client, action="update_project_status", id=paused, status="on_hold")

    trashed = make_project(client, name="Trashed")
    make_task(client, trashed, "trashed", deadline=day(0))
    client.delete(f"/api/projects/{trashed}")

    assert hot(client)["mode"] == "empty"


def test_hot_days_from_config(client, config):
    config(DEVICE_API={**ON, "hot_days": 0})
    pid = make_project(client)
    make_task(client, pid, "tomorrow", deadline=day(1))
    assert hot(client)["mode"] == "nearest"


def test_key_checked_when_set(client, config):
    config(DEVICE_API={**ON, "key": "s3cret"})
    assert client.get("/api/v1/hot").status_code == 401
    assert client.get("/api/v1/hot", params={"key": "wrong"}).status_code == 401
    assert client.get("/api/v1/hot", params={"key": "s3cret"}).status_code == 200
    assert client.get("/api/v1/hot", headers={"X-Device-Key": "s3cret"}).status_code == 200


def test_login_on_requires_a_key(client, config, monkeypatch):
    monkeypatch.setattr(auth, "load_state", lambda: {"enabled": True})
    monkeypatch.setattr(auth, "is_enabled", lambda: True)
    config(DEVICE_API=ON)
    assert client.get("/api/v1/hot").status_code == 401          # no key set: closed, never open by accident
    config(DEVICE_API={**ON, "key": "s3cret"})
    assert client.get("/api/v1/hot", headers={"X-Device-Key": "s3cret"}).status_code == 200


def test_bad_config_values_fall_back(client, config):
    config(DEVICE_API={"enabled": "yes", "hot_days": -2})
    assert client.get("/api/v1/hot").status_code == 404
