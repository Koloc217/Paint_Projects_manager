"""Status rules shared by the web UI and the bot (hub/core/lifecycle.py) and the button router."""
import asyncio
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from hub.bot import callbacks
from hub.core import lifecycle
from hub.core.constants import STATUS_FINISHED, TASK_IN_PROGRESS, TASK_NOT_STARTED

NOW = datetime(2026, 11, 11, 12, 0)


def make_task(**kw):
    base = dict(status=TASK_NOT_STARTED, completed_at=None, closed_from="", deadline=None, postponed=0)
    return SimpleNamespace(**{**base, **kw})


def test_complete_task_is_idempotent():
    task = make_task()
    assert lifecycle.complete_task(task, NOW) is True
    assert task.status == STATUS_FINISHED and task.completed_at == NOW
    assert lifecycle.complete_task(task, NOW + timedelta(days=1)) is False
    assert task.completed_at == NOW                      # a second press must not move the completion date


def test_start_task_does_not_reopen_a_closed_one():
    done = make_task(status=STATUS_FINISHED, completed_at=NOW)
    assert lifecycle.start_task(done) is False
    assert done.status == STATUS_FINISHED and done.completed_at == NOW
    open_task = make_task()
    assert lifecycle.start_task(open_task) is True
    assert open_task.status == TASK_IN_PROGRESS


def test_postpone_counts_from_today_when_overdue():
    task = make_task(deadline=datetime(2026, 11, 1))
    new_date, count = lifecycle.postpone_task(task, NOW)
    assert new_date == date(2026, 11, 13) and count == 1
    assert task.deadline == datetime(2026, 11, 13)


def test_postpone_counts_from_the_deadline_when_it_is_ahead():
    task = make_task(deadline=datetime(2026, 11, 20), postponed=2)
    new_date, count = lifecycle.postpone_task(task, NOW)
    assert new_date == date(2026, 11, 22) and count == 3


def test_postpone_without_a_deadline_starts_today():
    new_date, _ = lifecycle.postpone_task(make_task(), NOW)
    assert new_date == date(2026, 11, 13)


def test_set_task_status_clears_closed_from():
    task = make_task(status=STATUS_FINISHED, completed_at=NOW, closed_from=TASK_IN_PROGRESS)
    lifecycle.set_task_status(task, TASK_IN_PROGRESS, NOW)
    assert task.closed_from == "" and task.completed_at is None


def test_unknown_button_is_answered_not_raised(monkeypatch):
    answers = []

    async def answer_callback(cb_id, text="", show_alert=False):
        answers.append(cb_id)

    monkeypatch.setattr(callbacks.bot_api, "answer_callback", answer_callback)
    asyncio.run(callbacks.handle_callback({"id": "x1", "data": "old_button:42", "message": {"chat": {"id": 1}, "message_id": 2}}))
    asyncio.run(callbacks.handle_callback({"id": "x2", "message": {}}))      # no data at all
    assert answers == ["x1", "x2"]


def test_every_route_is_an_async_handler():
    assert all(asyncio.iscoroutinefunction(h) for h in callbacks.ROUTES.values())
    assert {"t_done", "n_done", "t_postpone", "clr", "noop"} <= set(callbacks.ROUTES)
