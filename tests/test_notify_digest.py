"""The pieces of the digest that notify.main() puts together."""
from datetime import datetime, timedelta
from types import SimpleNamespace

from hub.core.database import SessionLocal
from hub.jobs import notify

from helpers import action, make_project, make_task

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


def _digest(db, now, cfg=None):
    return notify.collect_digest(db, now, notify.parse_options(["n"]), {"friday_digest": True, **(cfg or {})})


def test_week_done_counts_closed_tasks_and_finished_projects(client):
    pid = make_project(client, "Finished one", status="in_work", token_reward=7)
    other = make_project(client, "Still going", status="in_work")
    t1, t2 = make_task(client, other, "Closed a"), make_task(client, other, "Closed b")
    make_task(client, other, "Open one")
    action(client, action="update_task_status", id=t1, status="finished")
    action(client, action="update_task_status", id=t2, status="finished")
    action(client, action="update_project_status", id=pid, status="finished")
    db = SessionLocal()
    try:
        weekly = notify.collect_digest(db, datetime.now(), notify.parse_options(["n", "--test"]), {})
        ordinary = _digest(db, MONDAY)
    finally:
        db.close()
    assert weekly.weekly
    text = "\n".join(weekly.week_done)
    assert "2" in text and "Finished one" in text and "+7" in text
    db = SessionLocal()
    try:
        message = notify.render_digest(db, weekly, datetime.now())
    finally:
        db.close()
    assert "Finished one" in message and "+7" in message          # the block reaches the message
    assert not ordinary.weekly and not ordinary.week_done      # only the weekly digest has the block


def test_week_done_ignores_tasks_closed_together_with_their_project(client):
    pid = make_project(client, "Closed whole", status="in_work")
    make_task(client, pid, "Dragged along")
    action(client, action="update_project_status", id=pid, status="finished")
    db = SessionLocal()
    try:
        d = notify.collect_digest(db, datetime.now(), notify.parse_options(["n", "--test"]), {})
    finally:
        db.close()
    lines = "\n".join(d.week_done)
    assert "Closed whole" in lines                                # the project counts
    assert not any(line.startswith("✅") for line in d.week_done)   # its task does not


def test_week_ahead_window_and_project_deadlines(client):
    pid = make_project(client, "Ahead", status="in_work", estimate=day(5, FRIDAY))
    make_task(client, pid, "Burning", day(2, FRIDAY))          # already in "burning"
    make_task(client, pid, "Next week", day(5, FRIDAY))
    make_task(client, pid, "Far", day(20, FRIDAY))
    db = SessionLocal()
    try:
        d = _digest(db, FRIDAY)
    finally:
        db.close()
    text = "\n".join(d.week_ahead)
    assert "Next week" in text and "Burning" not in text and "Far" not in text
    assert any(line.startswith("🎯") and "Ahead" in line for line in d.week_ahead)   # the project deadline


def test_week_blocks_can_be_switched_off(client):
    pid = make_project(client, "Quiet", status="in_work", estimate=day(5, FRIDAY))
    make_task(client, pid, "Soon", day(5, FRIDAY))
    db = SessionLocal()
    try:
        d = _digest(db, FRIDAY, {"weekly_done": False, "weekly_ahead": False})
    finally:
        db.close()
    assert d.weekly and not d.week_done and not d.week_ahead


def test_week_ahead_skips_projects_on_hold_and_not_weekly_days(client):
    pid = make_project(client, "Paused", status="on_hold", estimate=day(4, FRIDAY))
    make_task(client, pid, "Paused task", day(5, FRIDAY))
    db = SessionLocal()
    try:
        assert not _digest(db, FRIDAY).week_ahead
        assert not _digest(db, MONDAY).week_ahead              # only the weekly digest has the block
    finally:
        db.close()


def test_settings_save_weekly_flags_and_keep_them_when_the_page_does_not_send_them(client):
    from hub.core.appconfig import load_config
    body = {"dev_mode": False, "project_cost": 10}
    assert client.post("/api/config", json={**body, "weekly_done": False}).json()["status"] == "ok"
    notify_cfg = load_config()["NOTIFY"]
    assert notify_cfg["weekly_done"] is False and notify_cfg["weekly_ahead"] is True
    assert client.post("/api/config", json=body).json()["status"] == "ok"          # an older page: no such fields
    assert load_config()["NOTIFY"]["weekly_done"] is False


def test_data_has_last_activity_for_sorting(client):
    from helpers import data
    make_project(client, "Sortable")
    assert data(client)["projects"][0]["UpdatedAt"]


def test_weekly_flag_forces_the_friday_digest_on_any_day_even_when_switched_off(client):
    pid = make_project(client, "Any day", status="in_work")
    make_task(client, pid, "Plan me", day(20))
    opts = notify.parse_options(["n", "--weekly"])
    assert opts.weekly and opts.force and not opts.automatic            # skips silent mode, vacation, the slot check
    assert notify.skip_reason(opts, {"DEV_MODE": True}) is None
    db = SessionLocal()
    try:
        d = notify.collect_digest(db, MONDAY, opts, {"friday_digest": False})
    finally:
        db.close()
    assert d.weekly and any("Plan me" in line for line in d.friday_plan)


def test_bot_has_weekly_and_monthly_commands():
    from hub.bot import bot
    commands = [c for c, _key in bot.COMMANDS]
    assert "weekly" in commands and "monthly" in commands
