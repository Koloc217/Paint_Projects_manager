"""Every bot command and the main buttons run end to end against a fake Telegram (no network).

Catches wiring mistakes between the bot modules (bot, bot_api, bot_views, bot_photos, bot_tasks): a name that is
not reachable from where it is called fails here, not in the chat.
"""
import asyncio

import pytest

import bot
import bot_api
import bot_tasks
from helpers import make_project, make_task

CHAT = 777


@pytest.fixture
def tg(monkeypatch, config):
    config(TG_CHAT_ID=str(CHAT))
    calls = []

    async def fake_request(method, endpoint, payload=None, params=None, timeout_sec=20.0):
        calls.append((endpoint, payload))
        result = {"message_id": len(calls)} if endpoint.startswith("send") else True
        return {"ok": True, "result": result}

    async def fake_upload(endpoint, data, files, timeout_sec=60.0):
        calls.append((endpoint, data))
        return {"ok": True, "result": {"message_id": len(calls)}}

    monkeypatch.setattr(bot_api, "tg_request", fake_request)
    monkeypatch.setattr(bot_api, "tg_upload", fake_upload)
    bot_tasks._task_drafts.clear()
    bot_tasks._time_drafts.clear()
    return calls


def say(text):
    asyncio.run(bot.process_update({"message": {"message_id": 1, "chat": {"id": CHAT, "type": "private"},
                                                "text": text}}))


def press(data):
    asyncio.run(bot.process_update({"callback_query": {"id": "c", "data": data,
                                                       "message": {"message_id": 5, "chat": {"id": CHAT}}}}))


def texts(calls):
    return [(p or {}).get("text", "") for _, p in calls]


@pytest.mark.parametrize("command", ["/start", "/help", "/current_task", "/projects", "/stats", "/myphotos",
                                     "/photo", "/clear", "/calendar", "/task"])
def test_commands_answer(client, tg, command):
    pid = make_project(client)
    make_task(client, pid, deadline="2026-11-05")
    say(command)
    assert tg, f"{command} sent nothing"


def test_buttons(client, tg):
    pid = make_project(client, status="in_work")
    tid = make_task(client, pid, deadline="2026-11-05")
    for data in (f"open_p:{pid}", f"stage_menu:{pid}", f"p_stage:{pid}:painting", "show_all_proj",
                 f"t_prog:{tid}:{pid}", f"t_postpone:{tid}", f"mp_p:{pid}", "tk_new", "tk_x"):
        before = len(tg)
        press(data)
        assert len(tg) > before, data


def test_task_builder_flow(client, tg):
    pid = make_project(client)
    say("/task Prime the base")
    press(f"tk_p:{pid}")
    press("tk_d:tom")
    assert any("Prime the base" in t for t in texts(tg))
    assert client.get("/api/data").json()["tasks"][0]["Task"] == "Prime the base"


def test_strangers_are_ignored(client, tg):
    asyncio.run(bot.process_update({"message": {"message_id": 1, "chat": {"id": 1, "type": "private"},
                                                "text": "/projects"}}))
    assert tg == []


def test_backoff_grows_and_resets(capsys):
    t = [0.0]
    b = bot.Backoff(clock=lambda: t[0])
    delays = []
    for _ in range(6):
        delays.append(b.fail("ConnectError"))
        t[0] += delays[-1]
    assert delays == [3, 10, 30, 60, 60, 60]
    b.ok()
    assert b.fail("again") == 3                    # a success starts over
    out = capsys.readouterr().out
    assert out.count("No connection to Telegram") == 2    # one line per outage, not per attempt
    assert "is back after 223 s (6 failed attempt(s))" in out
