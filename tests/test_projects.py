"""Projects: creation and token cost, rename/edit rules, finishing and reopening, the trash."""
from helpers import action, data, make_project, make_task, project, task


def balance(client) -> int:
    return data(client)["total_tokens"]


# ---------- creation and tokens ----------

def test_new_project_costs_tokens(client):
    assert balance(client) == 100
    pid = make_project(client)
    assert balance(client) == 90
    p = project(client, pid)
    assert p["Status"] == "planned" and p["Stage"] == "none"


def test_not_enough_tokens(client, config):
    config(INITIAL_TOKENS=5)
    res = client.post("/api/projects", json={"name": "Too expensive", "priority": "Low", "token_reward": 1}).json()
    assert res["status"] == "error"
    assert data(client)["projects"] == []


def test_dev_mode_projects_are_free(client, config):
    config(INITIAL_TOKENS=0, DEV_MODE=True)
    make_project(client)
    assert balance(client) == 0


def test_empty_name_rejected(client):
    for name in ("", "   "):
        res = client.post("/api/projects", json={"name": name, "priority": "Low", "token_reward": 1}).json()
        assert res["status"] == "error"
    assert data(client)["projects"] == []


def test_reward_must_be_whole(client):
    res = client.post("/api/projects", json={"name": "X", "priority": "Low", "token_reward": 2.5}).json()
    assert res["status"] == "error"


# ---------- rename / edit ----------

def test_rename_allowed_without_dev_mode(client):
    pid = make_project(client)
    res = client.post(f"/api/projects/{pid}/rename", json={"name": "Dreadnought", "priority": "High"}).json()
    assert res["status"] == "ok"
    p = project(client, pid)
    assert p["Name"] == "Dreadnought" and p["Priority"] == "High"


def test_rename_to_blank_rejected(client):
    pid = make_project(client, name="Keep me")
    res = client.post(f"/api/projects/{pid}/rename", json={"name": "  "}).json()
    assert res["status"] == "error"
    assert project(client, pid)["Name"] == "Keep me"


def test_deadline_edit_needs_dev_mode(client, config):
    pid = make_project(client)
    body = {"name": "Space Marine", "priority": "Medium", "estimate": "2027-01-15"}
    assert client.post(f"/api/projects/{pid}/edit", json=body).json()["status"] == "error"
    config(DEV_MODE=True)
    assert client.post(f"/api/projects/{pid}/edit", json=body).json()["status"] == "ok"
    assert project(client, pid)["Estimate"] == "2027-01-15"


# ---------- finishing and reopening ----------

def test_finishing_pays_reward(client):
    pid = make_project(client, token_reward=15)
    assert action(client, action="update_project_status", id=pid, status="finished")["status"] == "ok"
    assert balance(client) == 100 - 10 + 15
    assert project(client, pid)["CompletedAt"]


def test_finishing_closes_tasks_and_reopening_restores_them(client):
    pid = make_project(client)
    t_open = make_task(client, pid, "Open task")
    t_busy = make_task(client, pid, "Busy task")
    t_hand = make_task(client, pid, "Closed by hand")
    action(client, action="update_task_status", id=t_busy, status="in_progress")
    action(client, action="update_task_status", id=t_hand, status="finished")

    action(client, action="update_project_status", id=pid, status="finished")
    assert all(task(client, t)["Status"] == "finished" for t in (t_open, t_busy, t_hand))

    action(client, action="update_project_status", id=pid, status="in_work")
    assert task(client, t_open)["Status"] == "not_started"
    assert task(client, t_busy)["Status"] == "in_progress"
    assert task(client, t_hand)["Status"] == "finished"      # closed by hand stays closed
    assert not project(client, pid)["CompletedAt"]


def test_unknown_status_rejected(client):
    pid = make_project(client)
    assert action(client, action="update_project_status", id=pid, status="whatever")["status"] == "error"
    assert action(client, action="update_project_stage", id=pid, stage="whatever")["status"] == "error"
    assert project(client, pid)["Status"] == "planned"


def test_stage_change(client):
    pid = make_project(client)
    assert action(client, action="update_project_stage", id=pid, stage="priming")["status"] == "ok"
    assert project(client, pid)["Stage"] == "priming"


# ---------- trash ----------

def test_delete_moves_to_trash_and_restore_brings_back(client):
    pid = make_project(client)
    make_task(client, pid)
    client.delete(f"/api/projects/{pid}")
    d = data(client)
    assert d["projects"] == [] and d["tasks"] == []
    assert [x["ID"] for x in d["trash"]] == [pid]

    assert client.post(f"/api/projects/{pid}/restore").json()["status"] == "ok"
    d = data(client)
    assert [p["ID"] for p in d["projects"]] == [pid] and len(d["tasks"]) == 1 and d["trash"] == []


def test_purge_keeps_balance_by_default(client):
    pid = make_project(client, token_reward=15)
    action(client, action="update_project_status", id=pid, status="finished")
    before = balance(client)
    client.delete(f"/api/projects/{pid}")
    client.delete(f"/api/trash/{pid}")
    assert data(client)["trash"] == []
    assert balance(client) == before


def test_purge_can_change_balance(client, config):
    config(DELETE_KEEPS_BALANCE=False)
    pid = make_project(client)
    assert balance(client) == 90
    client.delete(f"/api/projects/{pid}")
    client.delete("/api/trash")
    assert balance(client) == 100           # the start cost is returned with the project
