"""Status and stage codes: only the current codes are accepted, and they are translated for display."""
from hub.core import constants
from hub.core.i18n import tv
from helpers import action, make_project, project


def test_only_codes_are_accepted(client):
    pid = make_project(client)
    assert action(client, action="update_project_stage", id=pid, stage="sanding")["status"] == "ok"
    assert action(client, action="update_project_status", id=pid, status="in_work")["status"] == "ok"
    # labels are not values: what older versions stored is refused
    assert action(client, action="update_project_stage", id=pid, stage="Шлифовка")["status"] == "error"
    assert action(client, action="update_project_status", id=pid, status="В работе")["status"] == "error"
    p = project(client, pid)
    assert (p["Status"], p["Stage"]) == ("in_work", "sanding")


def test_translation_of_codes(client, config):
    assert tv("in_work") == "В работе"
    assert tv("painting") == "Покрас"
    assert tv("none") == "—"
    assert tv("something else") == "something else"       # an unknown value is shown as is
    config(LANGUAGE="en-US")
    assert tv("painting") == "Painting"
    assert tv("not_started") == "Not started"


def test_done_detection():
    assert constants.is_done("finished")
    for value in ("in_work", "not_started", "Закончен", "", None):
        assert not constants.is_done(value), value
