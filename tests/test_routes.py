"""The API is split into routers (hub/web/routes); together they must give the same address space as before."""
from collections import Counter

from hub.web.routes import ROUTERS
from hub.web.server import app


def _routes(routes):
    for r in routes:
        if hasattr(r, "path"):
            yield r
        else:
            yield from _routes(r.original_router.routes)


def test_no_address_is_served_twice():
    pairs = Counter((r.path, m) for r in _routes(app.routes) for m in (getattr(r, "methods", None) or ()))
    assert [k for k, n in pairs.items() if n > 1] == []


def test_every_router_has_routes_and_is_mounted():
    assert all(router.routes for router in ROUTERS)
    served = {r.path for r in _routes(app.routes)}
    for router in ROUTERS:
        assert {r.path for r in router.routes} <= served


def test_main_addresses_exist():
    served = {(r.path, m) for r in _routes(app.routes) for m in (getattr(r, "methods", None) or ())}
    for expected in [("/", "GET"), ("/api/data", "GET"), ("/api/projects", "POST"), ("/api/tasks", "POST"),
                     ("/api/projects/{project_id}", "DELETE"), ("/api/action", "POST"), ("/api/archive", "GET"),
                     ("/cal/{name}", "GET"), ("/api/v1/hot", "GET"), ("/sw.js", "GET")]:
        assert expected in served, expected


def test_errors_share_one_shape():
    from hub.web.responses import fail
    assert fail("x") == {"status": "error", "message": "x"}
