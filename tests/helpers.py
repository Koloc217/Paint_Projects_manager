"""Small helpers for the API tests (the `client` fixture comes from conftest.py)."""


def data(client) -> dict:
    return client.get("/api/data").json()


def make_project(client, name="Space Marine", **fields) -> str:
    body = {"name": name, "priority": "Medium", "token_reward": 15, **fields}
    res = client.post("/api/projects", json=body).json()
    assert res["status"] == "ok", res
    return res["id"]


def make_task(client, project_id, name="Prime the base", deadline=None) -> str:
    res = client.post("/api/tasks", json={"project_id": project_id, "task_name": name, "deadline": deadline}).json()
    assert res["status"] == "ok", res
    return res["id"]


def action(client, **body) -> dict:
    return client.post("/api/action", json=body).json()


def project(client, project_id) -> dict:
    return next(p for p in data(client)["projects"] if p["ID"] == project_id)


def task(client, task_id) -> dict:
    return next(t for t in data(client)["tasks"] if t["ID"] == task_id)
