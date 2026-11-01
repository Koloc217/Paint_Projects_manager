"""The bot asks for the time spent after a task is closed there: quick buttons, own value, skip, the setting."""
import asyncio

import pytest

import bot
import bot_api
import bot_tasks
from helpers import make_project, make_task, task


@pytest.fixture
def tg(monkeypatch):
    """Records what the bot would send to Telegram instead of sending it."""
    sent = {"messages": [], "edits": [], "deleted": [], "answers": []}

    async def send_message(chat_id, text, reply_markup=None):
        sent["messages"].append({"text": text, "markup": reply_markup})
        return {"ok": True}

    async def edit_message_text(chat_id, message_id, text, reply_markup=None):
        sent["edits"].append({"id": message_id, "text": text, "markup": reply_markup})

    async def answer_callback(cb_id, text="", show_alert=False):
        sent["answers"].append(text)

    async def delete_messages(chat_id, ids):
        sent["deleted"].extend(ids)
        return len(ids)

    for name, fn in [("send_message", send_message), ("edit_message_text", edit_message_text),
                     ("answer_callback", answer_callback), ("delete_messages", delete_messages)]:
        monkeypatch.setattr(bot_api, name, fn)
    bot_tasks._time_drafts.clear()
    return sent


def press(data, message_id=10):
    cb = {"id": "cb", "data": data, "message": {"chat": {"id": 1}, "message_id": message_id}}
    asyncio.run(bot.handle_callback(cb))


def test_close_then_quick_button(client, tg):
    pid = make_project(client)
    tid = make_task(client, pid)
    press(f"t_done:{tid}:{pid}")
    assert task(client, tid)["Status"] == "finished"
    question = tg["messages"][-1]
    assert "Prime the base" in question["text"]
    buttons = [b["callback_data"] for row in question["markup"]["inline_keyboard"] for b in row]
    assert f"tm:{tid}:60" in buttons and f"tm_x:{tid}" in buttons

    press(f"tm:{tid}:60", message_id=11)
    assert task(client, tid)["Spent"] == 60
    assert tg["edits"][-1]["id"] == 11 and "1 ч" in tg["edits"][-1]["text"]


def test_own_value_typed(client, tg):
    pid = make_project(client)
    tid = make_task(client, pid)
    press(f"t_done:{tid}:{pid}")
    press(f"tm_own:{tid}", message_id=12)
    assert asyncio.run(bot_tasks.handle_time_text(1, "что-то")) is True          # bad value: asked again
    assert task(client, tid)["Spent"] is None
    assert asyncio.run(bot_tasks.handle_time_text(1, "1ч 15м")) is True
    assert task(client, tid)["Spent"] == 75
    assert asyncio.run(bot_tasks.handle_time_text(1, "another message")) is False  # the draft is over


def test_skip_deletes_the_question(client, tg):
    pid = make_project(client)
    tid = make_task(client, pid)
    press(f"t_done:{tid}:{pid}")
    press(f"tm_x:{tid}", message_id=13)
    assert tg["deleted"] == [13]
    assert task(client, tid)["Spent"] is None


def test_no_question_when_off_or_already_closed(client, tg, config):
    pid = make_project(client)
    tid = make_task(client, pid)
    config(ASK_SPENT_TIME=False)
    press(f"t_done:{tid}:{pid}")
    assert tg["messages"] == []
    config(ASK_SPENT_TIME=True)
    press(f"t_done:{tid}:{pid}")                     # a second press on a closed task
    assert tg["messages"] == []
