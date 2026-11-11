import asyncio

from hub.web import auth


def test_lockout_after_max_fails_and_forgive():
    ip = "203.0.113.9"
    auth._forgive(ip)
    assert auth._locked_response(ip) is None
    for _ in range(auth.MAX_FAILS):
        auth._register_fail(ip)
    resp = auth._locked_response(ip)
    assert resp is not None and resp.status_code == 429
    auth._forgive(ip)
    assert auth._locked_response(ip) is None


def test_penalize_counts_a_fail(monkeypatch):
    async def no_sleep(_):
        pass
    monkeypatch.setattr(auth.asyncio, "sleep", no_sleep)
    ip = "203.0.113.10"
    auth._forgive(ip)
    asyncio.run(auth._penalize(ip))
    assert len(auth._fails[ip]) == 1
    auth._forgive(ip)
