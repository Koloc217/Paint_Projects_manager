"""Status/stage codes: migration of old Russian values, old input still accepted, translation."""
import sqlite3

import common
import database
from i18n import tv
from helpers import action, make_project, project


def _raw(sql, *args):
    con = sqlite3.connect(database.DB_PATH)
    try:
        rows = con.execute(sql, args).fetchall()
        con.commit()
        return rows
    finally:
        con.close()


def test_migration_converts_old_values(client):
    _raw("INSERT INTO projects (id,name,status,stage,priority,token,token_reward) "
         "VALUES ('PRJ-OLD','Old','В работе','Покрас','High',0,5)")
    _raw("INSERT INTO projects (id,name,status,stage,priority,token,token_reward) "
         "VALUES ('PRJ-EMP','Empty stage','В планах','','Low',0,5)")
    _raw("INSERT INTO tasks (id,project_id,task_name,status,postponed,closed_from) "
         "VALUES ('TSK-OLD','PRJ-OLD','t','Закончен',0,'В процессе')")
    _raw("INSERT INTO tasks (id,project_id,task_name,status,postponed,closed_from) "
         "VALUES ('TSK-OLD2','PRJ-OLD','t2','Выполнено',0,'')")
    _raw("INSERT INTO progress_photos (project_id,filename,stage,source) VALUES ('PRJ-OLD','a.jpg','Грунт','web')")
    _raw("INSERT INTO activity (kind,project_id,old,new,source) VALUES ('project_stage','PRJ-OLD','Грунт','Покрас','web')")
    _raw("INSERT INTO activity (kind,project_id,old,new,source) "
         "VALUES ('task_deadline','PRJ-OLD','2026-10-01','2026-10-05','web')")

    database.ensure_schema()
    database.ensure_schema()            # a second run changes nothing

    assert sorted(_raw("SELECT status, stage FROM projects")) == [("in_work", "painting"), ("planned", "none")]
    assert sorted(_raw("SELECT status, closed_from FROM tasks")) == [("finished", ""), ("finished", "in_progress")]
    assert _raw("SELECT stage FROM progress_photos") == [("priming",)]
    assert _raw("SELECT old, new FROM activity ORDER BY id") == [("priming", "painting"), ("2026-10-01", "2026-10-05")]


def test_old_russian_input_still_accepted(client):
    pid = make_project(client)
    assert action(client, action="update_project_stage", id=pid, stage="Шлифовка")["status"] == "ok"
    assert action(client, action="update_project_status", id=pid, status="В работе")["status"] == "ok"
    p = project(client, pid)
    assert (p["Status"], p["Stage"]) == ("in_work", "sanding")


def test_translation_of_codes(client, config):
    assert tv("in_work") == "В работе"
    assert tv("painting") == "Покрас"
    assert tv("none") == "—"
    assert tv("Покрас") == "Покрас"         # old values in the journal are still shown
    config(LANGUAGE="en-US")
    assert tv("painting") == "Painting"
    assert tv("not_started") == "Not started"


def test_done_detection():
    for value in ("finished", "Закончен", "завершено", "Выполнено", "Completed"):
        assert common.is_done(value), value
    for value in ("in_work", "not_started", "", None):
        assert not common.is_done(value), value


def test_backup_keeps_old_sheet_values():
    assert [common.legacy_value(c) for c in ("in_work", "finished", "not_started", "none", "painting")] == \
        ["В работе", "Закончен", "Не начато", "—", "Покрас"]
