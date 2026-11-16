"""The first start of a new installation: a starting token balance or DEV_MODE (hub/core/setup.py)."""
import os

from helpers import data, make_project

from hub.core import appconfig


def fresh_install():
    """No config.json at all: the app writes a new one, as on the very first start."""
    os.remove(appconfig.CONFIG_FILE)
    appconfig._config_cache["stamp"] = None


def test_existing_installation_is_not_asked(client):
    assert data(client)["first_run"] is False
    assert client.post("/api/setup", json={"mode": "dev"}).json()["status"] == "error"
    assert appconfig.load_config()["DEV_MODE"] is False


def test_new_installation_starts_with_tokens(client):
    fresh_install()
    assert data(client)["first_run"] is True
    assert client.post("/api/projects", json={"name": "First"}).json()["status"] == "error"     # 0 tokens
    for bad in (2.5, -1, 10 ** 12):
        assert client.post("/api/setup", json={"mode": "tokens", "tokens": bad}).json()["status"] == "error"
    assert client.post("/api/setup", json={"mode": "other"}).json()["status"] == "error"
    assert client.post("/api/setup", json={"mode": "tokens", "tokens": 30}).json()["status"] == "ok"
    d = data(client)
    assert d["first_run"] is False and d["total_tokens"] == 30 and d["dev_mode"] is False
    make_project(client, "First")
    assert data(client)["total_tokens"] == 20
    # works once: it must not become a way to top up the balance later
    assert client.post("/api/setup", json={"mode": "tokens", "tokens": 500}).json()["status"] == "error"
    assert "FIRST_RUN" not in appconfig.load_config()


def test_new_installation_starts_in_dev_mode(client):
    fresh_install()
    assert client.post("/api/setup", json={"mode": "dev"}).json()["status"] == "ok"
    d = data(client)
    assert d["first_run"] is False and d["dev_mode"] is True
    make_project(client, "Free in DEV MODE")


def test_no_question_once_projects_exist(client, config):
    config(FIRST_RUN=True)
    assert data(client)["first_run"] is True
    make_project(client)
    assert data(client)["first_run"] is False
    assert client.post("/api/setup", json={"mode": "dev"}).json()["status"] == "error"
