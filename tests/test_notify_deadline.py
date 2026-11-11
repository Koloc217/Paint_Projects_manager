"""notify.tg_post: a request that hangs (a proxy that never answers the SOCKS handshake) is cut off, not waited forever."""
import threading
import time

import httpx
import pytest

from hub.jobs import notify


def test_hung_request_is_abandoned_and_retried():
    calls = []
    stop = threading.Event()

    def hang(url, payload):
        calls.append(url)
        stop.wait(5)                      # "never" answers within the test

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        notify.tg_post("u", {}, deadline=0.2, attempts=2, post=hang)
    stop.set()
    assert len(calls) == 2
    assert time.monotonic() - started < 2


def test_second_attempt_can_succeed():
    calls = []
    ok = httpx.Response(200, json={"ok": True})

    def flaky(url, payload):
        calls.append(url)
        if len(calls) == 1:
            time.sleep(1)
        return ok

    assert notify.tg_post("u", {}, deadline=0.2, attempts=2, post=flaky) is ok


def test_network_error_is_raised_at_once():
    def broken(url, payload):
        raise httpx.ConnectError("refused")

    with pytest.raises(httpx.ConnectError):
        notify.tg_post("u", {}, deadline=1, attempts=2, post=broken)


def test_send_tg_message_returns_false_when_telegram_hangs(monkeypatch):
    monkeypatch.setattr(notify, "REQUEST_DEADLINE_SEC", 0.2)
    monkeypatch.setattr(notify, "REQUEST_ATTEMPTS", 1)
    stop = threading.Event()
    monkeypatch.setattr(notify, "_post_once", lambda url, payload: stop.wait(5))
    assert notify.send_tg_message(42, "hi") is False
    stop.set()
