"""Settings: the config form, vacation and the notification buttons."""
import re

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from hub.core import activity, vacation
from hub.core.common import DATE_FORMATS, DAY_KEYS, DAY_LABELS, edit_config
from hub.core.i18n import normalize_lang, tr, tv
from hub.jobs.scheduler import spawn_script
from hub.web.deps import get_db
from hub.web.responses import fail
from hub.web.schemas import ConfigUpdate, VacationRequest
from hub.web.validation import parse_token_field

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

router = APIRouter()


@router.post("/api/config")
def update_config_route(data: ConfigUpdate):
    for key, day in (data.notify_days or {}).items():
        if key not in DAY_KEYS:
            return fail(tr("err.unknown_day", key=key))
        if not TIME_RE.match(day.time or ""):
            return fail(tr("err.bad_time_day", day=tv(DAY_LABELS[key])))
    if data.language is not None and normalize_lang(data.language) is None:
        return fail(tr("err.unknown_language"))
    if data.tg_cleanup is not None and data.tg_cleanup not in ("keep_46h", "latest_only", "off"):
        return fail(tr("err.bad_value"))
    if data.date_format is not None and data.date_format not in DATE_FORMATS:
        return fail(tr("err.bad_date_format"))
    project_cost, err = parse_token_field(data.project_cost)
    if err:
        return fail(err)

    with edit_config() as cfg:
        days = dict(cfg["SCHEDULE"]["notify_days"])
        for key, day in (data.notify_days or {}).items():
            days[key] = {"enabled": day.enabled, "time": day.time}
        cfg["DEV_MODE"] = data.dev_mode
        cfg["PROJECT_COST"] = project_cost
        cfg["DELETE_KEEPS_BALANCE"] = data.delete_keeps_balance
        if data.tg_cleanup is not None:
            cfg["TG_CLEANUP"] = data.tg_cleanup
        if data.calendar_export is not None:
            cfg["CALENDAR_EXPORT"] = data.calendar_export
        if data.language is not None:
            cfg["LANGUAGE"] = normalize_lang(data.language)
        if data.date_format is not None:
            cfg["DATE_FORMAT"] = data.date_format
        if data.ask_spent_time is not None:
            cfg["ASK_SPENT_TIME"] = data.ask_spent_time
        cfg["NOTIFY"] = {
            "allow_postpone": data.allow_postpone,
            "stagnation_alert": data.stagnation_alert,
            "stagnation_days": min(90, max(1, data.stagnation_days)),
            "friday_digest": data.friday_digest,
            "monthly_report": data.monthly_report
        }
        cfg["SCHEDULE"] = {
            "notify_enabled": data.notify_enabled,
            "notify_days": days,
        }
    return {"status": "ok"}


@router.post("/api/vacation")
def start_vacation(data: VacationRequest, db: Session = Depends(get_db)):
    with activity.quiet_deadlines():
        err, moved = vacation.start(db, data.days)
    if not err:
        activity.log_vacation(data.days, moved)
    if err == "bad_days":
        return fail(tr("err.vacation_days", n=vacation.MAX_DAYS))
    if err == "already":
        return fail(tr("err.vacation_active"))
    return {"status": "ok", "vacation": vacation.state(), **moved}


@router.post("/api/vacation/cancel")
def cancel_vacation():
    vacation.cancel()
    return {"status": "ok", "vacation": vacation.state()}

@router.post("/api/notify/test")
async def trigger_test_notify():
    spawn_script("hub.jobs.notify", ["--test"])
    return {"status": "ok"}

@router.post("/api/notify/now")
async def trigger_notify_now():
    """The regular digest right now, as the timer would send it (ignores DEV_MODE, vacation and the slot check)."""
    spawn_script("hub.jobs.notify", ["--now", "--source=manual"])
    return {"status": "ok"}
