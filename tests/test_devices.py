"""Device API (/api/v1/hot): which tasks a small screen gets, the "nearest" and "empty" fallbacks, access rules."""
from datetime import date, timedelta

from hub.web import auth
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


# ---------- /api/v1/project ----------

def project_view(client, **params):
    res = client.get("/api/v1/project", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def test_project_needs_the_same_access_as_hot(client, config):
    assert client.get("/api/v1/project").status_code == 404
    config(DEVICE_API={"enabled": True, "key": "s3cret", "hot_days": 3})
    assert client.get("/api/v1/project").status_code == 401
    assert client.get("/api/v1/project", params={"key": "s3cret"}).status_code == 200


def test_project_empty_without_projects(client, config):
    config(DEVICE_API=ON)
    res = project_view(client)
    assert res["mode"] == "empty" and res["project"] is None and res["message"]


def test_project_focus_and_by_id(client, config):
    config(DEVICE_API=ON)
    low = make_project(client, name="Low one", priority="Low")
    crit = make_project(client, name="Critical one", priority="Critical", estimate=day(5))
    make_task(client, crit, "later", deadline=day(9))
    make_task(client, crit, "sooner", deadline=day(1))
    done = make_task(client, crit, "done already", deadline=day(0))
    action(client, action="update_task_status", id=done, status="finished")

    res = project_view(client)
    assert res["mode"] == "focus" and res["project"]["id"] == crit
    p = res["project"]
    assert p["name"] == "Critical one" and p["deadline"] == day(5) and p["days_left"] == 5 and p["overdue"] is False
    assert p["tasks_open"] == 2 and p["tasks_total"] == 3
    assert p["next_task"]["name"] == "sooner" and p["next_task"]["days_left"] == 1

    asked = project_view(client, id=low)
    assert asked["mode"] == "project" and asked["project"]["id"] == low
    assert asked["project"]["next_task"] is None and asked["project"]["deadline"] is None


def test_project_unknown_or_finished_id_falls_back_to_focus(client, config):
    config(DEVICE_API=ON)
    keep = make_project(client, name="Keep")
    gone = make_project(client, name="Gone")
    action(client, action="update_project_status", id=gone, status="finished")
    assert project_view(client, id=gone)["project"]["id"] == keep
    assert project_view(client, id="PRJ-nope")["mode"] == "focus"
