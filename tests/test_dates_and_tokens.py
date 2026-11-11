"""The fragile parts with dates and tokens: vacation, scheduler catch-up, the token balance."""
from datetime import date, datetime, timedelta

import pytest

from hub.jobs import scheduler
from hub.core import vacation
from hub.core.database import SessionLocal, Project
from helpers import action, data, make_project, make_task, project, task


def balance(client) -> int:
    return data(client)["total_tokens"]


# ---------- vacation ----------

def test_vacation_moves_only_open_deadlines(client):
    open_p = make_project(client, estimate="2026-11-20", status="in_work")
    t_open = make_task(client, open_p, deadline="2026-11-10")
    t_done = make_task(client, open_p, deadline="2026-11-05")
    action(client, action="update_task_status", id=t_done, status="finished")
    t_nodate = make_task(client, open_p)
    done_p = make_project(client, estimate="2026-11-15")
    t_of_done = make_task(client, done_p, deadline="2026-11-12")
    action(client, action="update_project_status", id=done_p, status="finished")
    trashed = make_project(client, estimate="2026-11-18")
    client.delete(f"/api/projects/{trashed}")

    res = client.post("/api/vacation", json={"days": 7}).json()
    assert res["status"] == "ok" and (res["projects"], res["tasks"]) == (1, 1)
    assert project(client, open_p)["Estimate"] == "2026-11-27"
    assert task(client, t_open)["Deadline"] == "2026-11-17"
    assert task(client, t_done)["Deadline"] == "2026-11-05"           # closed: untouched
    assert task(client, t_nodate)["Deadline"] is None
    assert project(client, done_p)["Estimate"] == "2026-11-15"        # finished project: untouched
    assert task(client, t_of_done)["Deadline"] == "2026-11-12"
    st = data(client)["vacation"]
    assert st["active"] and st["days"] == 7
    assert st["until"] == (date.today() + timedelta(days=7)).isoformat()


def test_vacation_resets_stagnation_clock(client):
    pid = make_project(client, status="in_work")
    db = SessionLocal()
    try:
        before = db.get(Project, pid).updated_at
    finally:
        db.close()
    client.post("/api/vacation", json={"days": 10})
    db = SessionLocal()
    try:
        after = db.get(Project, pid).updated_at
    finally:
        db.close()
    assert after - before == timedelta(days=10)


@pytest.mark.parametrize("days", [0, 31, -1])
def test_vacation_days_limits(client, days):
    assert client.post("/api/vacation", json={"days": days}).json()["status"] == "error"
    assert not data(client)["vacation"]["active"]


def test_second_vacation_refused_and_cancel(client):
    pid = make_project(client, estimate="2026-12-01", status="in_work")
    assert client.post("/api/vacation", json={"days": 5}).json()["status"] == "ok"
    assert client.post("/api/vacation", json={"days": 5}).json()["status"] == "error"
    assert project(client, pid)["Estimate"] == "2026-12-06"            # moved once, not twice

    res = client.post("/api/vacation/cancel").json()
    assert res["status"] == "ok" and not res["vacation"]["active"]
    assert project(client, pid)["Estimate"] == "2026-12-06"            # cancel keeps the moved deadlines
    assert client.post("/api/vacation", json={"days": 3}).json()["status"] == "ok"   # a new one is allowed


def test_vacation_state_ends_by_itself():
    cfg = {"VACATION": {"start": "2026-11-01", "until": "2026-11-08", "days": 7}}
    assert vacation.state(cfg, today=date(2026, 11, 7))["active"]
    assert not vacation.state(cfg, today=date(2026, 11, 8))["active"]       # "until" is the first day back
    assert not vacation.state({"VACATION": "broken"})["active"]


# ---------- scheduler catch-up ----------

def _cfg(**schedule):
    days = {k: {"enabled": True, "time": "18:05"} for k in ("mon", "tue", "wed", "thu", "fri")}
    days.update({k: {"enabled": True, "time": "12:05"} for k in ("sat", "sun")})
    return {"SCHEDULE": {"notify_enabled": True, "notify_days": days, **schedule}}


def _ids(result):
    return sorted((job.id, at.strftime("%Y-%m-%d %H:%M")) for job, at in result)


MON = datetime(2026, 11, 2)       # a Monday


def test_due_jobs_fire_once_per_minute():
    fired = {}
    now = MON.replace(hour=18, minute=5, second=10)
    assert [j.id for j in scheduler.due_jobs(_cfg(), now, fired)] == ["notify_mon"]
    assert scheduler.due_jobs(_cfg(), now.replace(second=50), fired) == []
    assert scheduler.due_jobs(_cfg(), MON.replace(hour=18, minute=6), fired) == []


def test_first_start_catches_up_nothing():
    assert scheduler.missed_jobs(_cfg(), MON.replace(hour=19), None, {}) == []


def test_missed_evening_digest_same_day():
    alive = MON.replace(hour=17)
    assert _ids(scheduler.missed_jobs(_cfg(), MON.replace(hour=19), alive, {})) == [("notify_mon", "2026-11-02 18:05")]
    # more than 3 hours late: too late for an evening digest
    assert scheduler.missed_jobs(_cfg(), MON.replace(hour=21, minute=30), alive, {}) == []


def test_digest_not_caught_up_next_day():
    alive = MON.replace(hour=17)
    now = (MON + timedelta(days=1)).replace(hour=1)        # Tuesday 01:00, the app was off since Monday 17:00
    assert _ids(scheduler.missed_jobs(_cfg(), now, alive, {})) == []
    now = (MON + timedelta(days=1)).replace(hour=9)
    assert _ids(scheduler.missed_jobs(_cfg(), now, alive, {})) == []


def test_only_latest_missed_run_and_not_twice():
    alive = MON - timedelta(days=10)                        # off for more than a week
    now = MON.replace(hour=19)
    result = scheduler.missed_jobs(_cfg(), now, alive, {})
    assert _ids(result) == [("notify_mon", "2026-11-02 18:05")]   # today's digest only, not the older days
    fired = {"notify_mon": "2026-11-02 18:05"}
    assert scheduler.missed_jobs(_cfg(), now, alive, fired) == []


def test_disabled_jobs_are_not_caught_up():
    alive = MON.replace(hour=17)
    cfg = _cfg(notify_enabled=False)
    assert scheduler.missed_jobs(cfg, MON.replace(hour=19), alive, {}) == []


# ---------- token balance ----------

def test_balance_through_trash_restore_and_reopen(client):
    assert balance(client) == 100
    pid = make_project(client, token_reward=15)               # -10
    assert balance(client) == 90
    action(client, action="update_project_status", id=pid, status="finished")    # +15
    assert balance(client) == 105
    client.delete(f"/api/projects/{pid}")                      # trash: may come back, nothing changes
    assert balance(client) == 105
    client.post(f"/api/projects/{pid}/restore")
    assert balance(client) == 105
    action(client, action="update_project_status", id=pid, status="in_work")     # reopened: reward taken back
    assert balance(client) == 90
    action(client, action="update_project_status", id=pid, status="finished")    # finished again: paid once
    assert balance(client) == 105


def test_purge_after_reopen_keeps_balance(client):
    pid = make_project(client, token_reward=15)
    action(client, action="update_project_status", id=pid, status="finished")
    action(client, action="update_project_status", id=pid, status="in_work")
    before = balance(client)                                   # 90: cost paid, reward not
    client.delete(f"/api/projects/{pid}")
    client.delete(f"/api/trash/{pid}")
    assert balance(client) == before


def test_trash_without_keep_balance_refunds_at_once(client, config):
    config(DELETE_KEEPS_BALANCE=False)
    pid = make_project(client, token_reward=15)
    action(client, action="update_project_status", id=pid, status="finished")
    assert balance(client) == 105
    client.delete(f"/api/projects/{pid}")                      # in the trash: not counted at all
    assert balance(client) == 100
    client.post(f"/api/projects/{pid}/restore")
    assert balance(client) == 105


def test_dev_mode_project_costs_nothing_and_cannot_overdraw(client, config):
    config(DEV_MODE=True)
    for i in range(12):
        make_project(client, name=f"P{i}")
    assert balance(client) == 100
    config(DEV_MODE=False)
    for _ in range(10):
        make_project(client)
    assert balance(client) == 0
    res = client.post("/api/projects", json={"name": "one more", "priority": "Medium", "token_reward": 0}).json()
    assert res["status"] == "error"
    assert balance(client) == 0                                # never below zero
