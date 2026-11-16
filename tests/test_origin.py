"""Data-changing requests from other sites are refused (hub/web/origin.py)."""
from helpers import data, make_project

from hub.web.origin import foreign_request, same_origin

FOREIGN = {"Origin": "http://evil.example"}


def test_same_origin_compares_host_and_port_only():
    assert same_origin("http://192.168.1.60:8000", "192.168.1.60:8000")
    assert same_origin("https://hub.local", "HUB.local")              # the scheme may differ behind a proxy
    assert not same_origin("http://192.168.1.60:9000", "192.168.1.60:8000")
    assert not same_origin("http://evil.example", "192.168.1.60:8000")
    assert not same_origin("null", "192.168.1.60:8000")               # sandboxed page, file://


def test_which_requests_are_foreign():
    host = {"host": "hub.local:8000"}
    assert not foreign_request("GET", {**host, "origin": "http://evil.example"})      # reads are not checked
    assert not foreign_request("POST", host)                                          # curl, scripts, devices
    assert not foreign_request("POST", {**host, "origin": "http://hub.local:8000"})
    assert foreign_request("POST", {**host, "origin": "http://evil.example"})
    assert foreign_request("DELETE", {**host, "origin": "null"})
    assert foreign_request("POST", {**host, "sec-fetch-site": "cross-site"})
    assert not foreign_request("POST", {**host, "sec-fetch-site": "same-origin"})


def test_foreign_post_is_refused_and_changes_nothing(client):
    pid = make_project(client)
    for path in ("/api/telegram/unlink", "/api/vacation/cancel", "/api/archive/restore", "/api/notify/now"):
        res = client.post(path, headers=FOREIGN)
        assert res.status_code == 403 and res.json()["code"] == "cross_origin", path
    assert client.delete(f"/api/projects/{pid}", headers=FOREIGN).status_code == 403
    assert [p["ID"] for p in data(client)["projects"]] == [pid]


def test_own_page_and_scripts_still_work(client):
    own = {"Origin": "http://testserver"}                # TestClient sends Host: testserver
    res = client.post("/api/projects", json={"name": "Dreadnought", "token_reward": 5}, headers=own).json()
    assert res["status"] == "ok"
    assert client.post("/api/vacation/cancel").json()["status"] == "ok"           # no Origin at all
    assert client.get("/api/data", headers=FOREIGN).status_code == 200
