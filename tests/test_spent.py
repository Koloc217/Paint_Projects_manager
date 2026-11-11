"""Time spent: parsing what is typed, saving per task and per project, the stage of the breakdown."""
import pytest

from hub.core import spent
from helpers import action, make_project, make_task, project, task


@pytest.mark.parametrize("text, minutes", [
    ("90", 90), (" 45 ", 45), ("1:30", 90), ("0:05", 5), ("1.5", 90), ("1,5", 90),
    ("1.5ч", 90), ("1,5 ч", 90), ("2h", 120), ("40м", 40), ("40 мин", 40), ("40min", 40),
    ("1ч 30м", 90), ("1h30m", 90), ("2 часа", 120), ("1 час 15 минут", 75), ("1.5 hours", 90),
    (90, 90),
])
def test_parse(text, minutes):
    assert spent.parse_spent(text) == minutes


@pytest.mark.parametrize("text", ["", "  ", None])
def test_parse_empty(text):
    assert spent.parse_spent(text) is None


@pytest.mark.parametrize("text", ["abc", "1:75", "0", "-5", "1ч абв", "ч", "5 parsecs", "1:30:00", "999999", True])
def test_parse_rejects(text):
    with pytest.raises(spent.SpentError):
        spent.parse_spent(text)


def test_format(config):
    assert spent.format_minutes(40) == "40 мин"
    assert spent.format_minutes(120) == "2 ч"
    assert spent.format_minutes(95) == "1 ч 35 мин"
    config(LANGUAGE="en-US")
    assert spent.format_minutes(95) == "1 h 35 min"


def test_task_time_saved_with_stage(client):
    pid = make_project(client)
    action(client, action="update_project_stage", id=pid, stage="priming")
    tid = make_task(client, pid)
    res = client.post(f"/api/tasks/{tid}/edit", json={"task_name": "Prime the base", "spent": "1:30"}).json()
    assert res["status"] == "ok", res
    t = task(client, tid)
    assert (t["Spent"], t["SpentStage"]) == (90, "priming")

    # a new stage later does not move the time already given
    action(client, action="update_project_stage", id=pid, stage="painting")
    client.post(f"/api/tasks/{tid}/edit", json={"task_name": "Prime the base", "spent": "2ч"})
    t = task(client, tid)
    assert (t["Spent"], t["SpentStage"]) == (120, "priming")

    # omitted: kept; empty: cleared
    client.post(f"/api/tasks/{tid}/edit", json={"task_name": "Renamed"})
    assert task(client, tid)["Spent"] == 120
    client.post(f"/api/tasks/{tid}/edit", json={"task_name": "Renamed", "spent": ""})
    t = task(client, tid)
    assert (t["Spent"], t["SpentStage"]) == (None, "")


def test_task_time_bad_value(client):
    pid = make_project(client)
    tid = make_task(client, pid)
    res = client.post(f"/api/tasks/{tid}/edit", json={"task_name": "x", "spent": "много"}).json()
    assert res["status"] == "error" and "90" in res["message"]
    assert task(client, tid)["Task"] == "Prime the base"          # nothing else changed either


def test_project_extra_time(client):
    pid = make_project(client)
    assert client.post(f"/api/projects/{pid}/spent", json={"extra": "3ч"}).json()["extra"] == 180
    assert project(client, pid)["ExtraSpent"] == 180
    assert client.post(f"/api/projects/{pid}/spent", json={"extra": ""}).json()["extra"] == 0
    assert client.post(f"/api/projects/{pid}/spent", json={"extra": "nope"}).json()["status"] == "error"


def test_time_does_not_touch_tokens(client):
    pid = make_project(client)
    tid = make_task(client, pid)
    before = client.get("/api/data").json()["total_tokens"]
    client.post(f"/api/tasks/{tid}/edit", json={"task_name": "x", "spent": "10ч"})
    client.post(f"/api/projects/{pid}/spent", json={"extra": "5ч"})
    assert client.get("/api/data").json()["total_tokens"] == before


def test_ask_spent_time_setting(client):
    assert client.get("/api/data").json()["ask_spent_time"] is True
    body = {"dev_mode": False, "ask_spent_time": False}
    assert client.post("/api/config", json=body).json()["status"] == "ok"
    assert client.get("/api/data").json()["ask_spent_time"] is False
