"""Tasks: which project they land in, statuses and completion time, editing, pipeline templates."""
from helpers import action, data, make_project, make_task, task


def test_task_goes_to_the_requested_project(client):
    """Regression: a task created from the second project's card must not land in the first project."""
    first = make_project(client, name="First")
    second = make_project(client, name="Second")
    tid = make_task(client, second)
    assert task(client, tid)["Project ID"] == second
    assert all(t["Project ID"] != first for t in data(client)["tasks"])


def test_task_for_unknown_or_trashed_project(client):
    assert client.post("/api/tasks", json={"project_id": "PRJ-NOPE", "task_name": "x"}).json()["status"] == "error"
    pid = make_project(client)
    client.delete(f"/api/projects/{pid}")
    assert client.post("/api/tasks", json={"project_id": pid, "task_name": "x"}).json()["status"] == "error"


def test_empty_task_name_rejected(client):
    pid = make_project(client)
    assert client.post("/api/tasks", json={"project_id": pid, "task_name": "  "}).json()["status"] == "error"


def test_bad_deadline_rejected(client):
    pid = make_project(client)
    res = client.post("/api/tasks", json={"project_id": pid, "task_name": "x", "deadline": "31.12.2026"}).json()
    assert res["status"] == "error"


def test_completion_time_set_and_cleared(client):
    pid = make_project(client)
    tid = make_task(client, pid)
    t = task(client, tid)
    assert t["Status"] == "not_started" and t["CompletedAt"] is None and t["CompletedTs"] is None

    action(client, action="update_task_status", id=tid, status="finished")
    t = task(client, tid)
    assert t["CompletedAt"] and t["CompletedTs"].startswith(t["CompletedAt"])

    action(client, action="update_task_status", id=tid, status="in_progress")
    t = task(client, tid)
    assert t["Status"] == "in_progress" and t["CompletedTs"] is None


def test_project_status_is_not_a_task_status(client):
    pid = make_project(client)
    tid = make_task(client, pid)
    assert action(client, action="update_task_status", id=tid, status="on_hold")["status"] == "error"


def test_edit_and_delete_task(client):
    pid = make_project(client)
    tid = make_task(client, pid)
    res = client.post(f"/api/tasks/{tid}/edit", json={"task_name": "Base coat", "deadline": "2027-02-01"}).json()
    assert res["status"] == "ok"
    t = task(client, tid)
    assert t["Task"] == "Base coat" and t["Deadline"] == "2027-02-01"
    client.delete(f"/api/tasks/{tid}")
    assert data(client)["tasks"] == []


def test_template_reapply_skips_existing_steps(client):
    pid = make_project(client)
    first = client.post(f"/api/projects/{pid}/template", json={"kind": "print"}).json()
    assert first["status"] == "ok" and first["added"] > 0
    count = len(data(client)["tasks"])
    assert count == first["added"]

    again = client.post(f"/api/projects/{pid}/template", json={"kind": "print"}).json()
    assert again["status"] == "error"                      # every step already exists
    assert len(data(client)["tasks"]) == count


def test_unknown_template(client):
    pid = make_project(client)
    assert client.post(f"/api/projects/{pid}/template", json={"kind": "nope"}).json()["status"] == "error"
