"""The monthly report and the weekly digest are not lost when their day is skipped (notify.py: monthly_due,
weekly_due, seed_reports and the record in .notify_reports.json)."""
import sys
from datetime import datetime

import pytest

from hub.jobs import notify

AUTO = notify.parse_options(["n"])
THURSDAY = datetime(2026, 11, 12, 18, 5)
FRIDAY = datetime(2026, 11, 13, 18, 5)
SATURDAY = datetime(2026, 11, 14, 12, 5)
SUNDAY = datetime(2026, 11, 15, 12, 5)
MONDAY = datetime(2026, 11, 16, 18, 5)
FIRST = datetime(2026, 12, 1, 18, 5)
THIRD = datetime(2026, 12, 3, 18, 5)


def test_monthly_goes_out_on_the_first_and_only_once():
    assert notify.monthly_due(FIRST, AUTO, {}, {"monthly": "2026-11"})
    assert not notify.monthly_due(FIRST, AUTO, {}, {"monthly": "2026-12"})          # a second run that day
    assert not notify.monthly_due(FIRST, AUTO, {"monthly_report": False}, {"monthly": "2026-11"})
    assert not notify.monthly_due(FIRST, notify.parse_options(["n", "--test"]), {}, {})


def test_monthly_is_caught_up_after_a_skipped_first():
    assert notify.monthly_due(THIRD, AUTO, {}, {"monthly": "2026-11"})
    # a digest asked for by hand does not bring an old report along; --monthly always sends it
    assert not notify.monthly_due(THIRD, notify.parse_options(["n", "--now"]), {}, {"monthly": "2026-11"})
    assert notify.monthly_due(THIRD, notify.parse_options(["n", "--monthly"]), {}, {"monthly": "2026-12"})


def test_weekly_is_caught_up_on_the_weekend_only():
    last_week, this_week = {"weekly": "2026-W45"}, {"weekly": "2026-W46"}
    assert notify.weekly_due(FRIDAY, AUTO, {}, last_week)
    assert notify.weekly_due(SATURDAY, AUTO, {}, last_week) and notify.weekly_due(SUNDAY, AUTO, {}, last_week)
    assert not notify.weekly_due(SATURDAY, AUTO, {}, this_week)                     # Friday's went out
    assert not notify.weekly_due(MONDAY, AUTO, {}, last_week)                       # the weekend plan is stale
    assert not notify.weekly_due(SATURDAY, AUTO, {"friday_digest": False}, last_week)
    assert not notify.weekly_due(SATURDAY, notify.parse_options(["n", "--now"]), {}, last_week)
    assert notify.weekly_due(MONDAY, notify.parse_options(["n", "--weekly"]), {"friday_digest": False}, this_week)


def test_first_run_with_the_record_repeats_nothing():
    """An installation updated in the middle of a month, on a weekend: its reports went out by the old rules."""
    sent = {}
    assert notify.seed_reports(SATURDAY, AUTO, sent)
    assert sent == {"monthly": "2026-11", "weekly": "2026-W46"}
    assert not notify.monthly_due(SATURDAY, AUTO, {}, sent) and not notify.weekly_due(SATURDAY, AUTO, {}, sent)
    assert not notify.seed_reports(SATURDAY, AUTO, sent)                             # nothing to add the second time
    sent = {}
    notify.seed_reports(THURSDAY, AUTO, sent)
    assert notify.weekly_due(SATURDAY, AUTO, {}, sent)          # ... and a skipped Friday is caught up from then on
    sent = {}
    assert not notify.seed_reports(SATURDAY, notify.parse_options(["n", "--now"]), sent) and sent == {}


@pytest.fixture
def run(client, config, monkeypatch, tmp_path):
    """Runs notify.main() at a given moment; returns the kinds of the messages it sent."""
    config(TG_CHAT_ID="1")
    monkeypatch.setattr(notify, "BOT_TOKEN", "x")
    monkeypatch.setattr(notify, "REPORTS_FILE", str(tmp_path / "reports.json"))
    monkeypatch.setattr(notify, "claim_run_slot", lambda source: (True, None))

    def go(now, *flags, ok=True):
        sent = []

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return now

        monkeypatch.setattr(notify, "datetime", Clock)
        monkeypatch.setattr(notify, "send_tg_message",
                            lambda chat, text, markup=None, kind="digest": sent.append(kind) or ok)
        monkeypatch.setattr(sys, "argv", ["notify.py", *flags])
        notify.main()
        return sent
    return go


def test_main_sends_the_missed_monthly_report_once(run):
    assert run(datetime(2026, 11, 20, 18, 5)) == ["digest"]            # the record starts here: nothing extra
    assert notify.load_reports()["monthly"] == "2026-11"
    assert run(THIRD) == ["report", "digest"]                            # the 1st and the 2nd had no digest
    assert run(datetime(2026, 12, 4, 18, 5)) == ["digest"]
    assert run(datetime(2027, 1, 1, 18, 5)) == ["report", "digest"]


def test_a_report_that_failed_to_send_is_tried_again(run):
    run(datetime(2026, 11, 20, 18, 5))
    assert run(FIRST, ok=False) == ["report", "digest"]
    assert notify.load_reports()["monthly"] == "2026-11"
    assert run(THIRD) == ["report", "digest"]


def test_main_marks_the_week(run):
    run(THURSDAY)
    assert notify.load_reports()["weekly"] == ""
    run(SATURDAY)                                                        # Friday was off in the schedule
    assert notify.load_reports()["weekly"] == "2026-W46"
    run(MONDAY, "--weekly")                                              # a manual check does not count
    assert notify.load_reports()["weekly"] == "2026-W46"
