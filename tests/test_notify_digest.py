"""The pieces of the digest that notify.main() puts together."""
from datetime import datetime, timedelta
from types import SimpleNamespace

from hub.core.database import SessionLocal
from hub.jobs import notify

from helpers import make_project, make_task

FRIDAY = datetime(2026, 11, 13, 18, 5)      # a Friday
MONDAY = datetime(2026, 11, 9, 18, 5)


def day(offset, base=MONDAY):
    return (base + timedelta(days=offset)).strftime("%Y-%m-%d")


def test_parse_options():
    o = notify.parse_options(["notify", "--now", "--source=bot"])
    assert o.now and o.force and not o.test and o.source == "bot" and not o.automatic
    o = notify.parse_options(["notify"])
    assert o.automatic and not o.force and o.source == "cron/manual"
    assert notify.parse_options(["notify", "--monthly"]).automatic is False


def test_automatic_runs_are_skipped_in_silent_mode_but_requested_ones_are_not():
    silent = {"DEV_MODE": True}
    assert "silent mode" in notify.skip_reason(notify.parse_options(["n"]), silent)
    for flag in ("--test", "--now", "--monthly"):
        assert notify.skip_reason(notify.parse_options(["n", flag]), silent) is None
    assert notify.skip_reason(notify.parse_options(["n"]), {"DEV_MODE": False}) is None


def test_keyboard_has_close_and_postpone_and_is_capped():
    tasks = [SimpleNamespace(id=f"T{i}", task_name="x" * 30) for i in range(10)]
    kb = notify.build_keyboard(tasks, allow_postpone=True)["inline_keyboard"]
    assert len(kb) == notify.MAX_BUTTONS
    assert kb[0][0]["callback_data"] == "n_done:T0" and kb[0][1]["callback_data"] == "t_postpone:T0"
    assert kb[0][0]["text"].endswith("..")                                # long names are shortened
    assert all(len(row) == 1 for row in notify.build_keyboard(tasks, allow_postpone=False)["inline_keyboard"])
    assert notify.build_keyboard([], allow_postpone=True) is None


def test_digest_content_rules():
    d = notify.Digest(projects={})
    assert not d.has_content
    d.friday_plan.append("x")
    assert not d.has_content                    # the weekend plan counts only on a weekly digest
    d.weekly = True
    assert d.has_content


def test_collect_splits_overdue_and_burning(client):
    pid = make_project(client, status="in_work")
    make_task(client, pid, "Overdue one", day(-2))
    make_task(client, pid, "Burning one", day(2))
    make_task(client, pid, "Far away", day(30))
    db = SessionLocal()
    try:
        d = notify.collect_digest(db, MONDAY, notify.parse_options(["n"]), {})
    finally:
        db.close()
    assert len(d.overdue) == 1 and "Overdue one" in d.overdue[0]
    assert len(d.burning) == 1 and "Burning one" in d.burning[0]
    assert len(d.action_tasks) == 2 and not d.weekly and d.has_content


def test_weekly_plan_only_on_friday_or_test(client):
    pid = make_project(client, status="in_work")
    make_task(client, pid, "Plan me", day(20))
    db = SessionLocal()
    try:
        cfg = {"friday_digest": True}
        assert not notify.collect_digest(db, MONDAY, notify.parse_options(["n"]), cfg).weekly
        fri = notify.collect_digest(db, FRIDAY, notify.parse_options(["n"]), cfg)
        assert fri.weekly and any("Plan me" in line for line in fri.friday_plan)
        assert notify.collect_digest(db, MONDAY, notify.parse_options(["n", "--test"]), cfg).weekly
        assert not notify.collect_digest(db, FRIDAY, notify.parse_options(["n"]), {"friday_digest": False}).weekly
    finally:
        db.close()
