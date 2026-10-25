import os
import re
import math
import time
import uuid
import base64
import contextlib
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, FileResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session
from fastapi.staticfiles import StaticFiles
import uvicorn

from database import SessionLocal, Project, Task, Adjustment, Reference, ProgressPhoto, ensure_schema
import progress
import logs
from i18n import tr, tv, get_lang, table_for, available_languages, normalize_lang
from common import (
    BASE_DIR, DEFAULT_NOTIFY_SETTINGS, DEFAULT_SCHEDULE, DAY_KEYS, DAY_LABELS,
    PROJECT_STATUSES, TASK_STATUSES, STAGES, PRIORITIES, STATUS_PLANNED, TASK_NOT_STARTED, STATUS_FINISHED,
    is_done, load_config, edit_config, compute_balance, DATE_FORMATS, detect_image_ext, live_projects, live_tasks,
    is_chat_bound, ensure_pairing_code, new_pairing_code, unlink_chat, format_pairing_code, parse_iso_date, iso_date,
    MAX_TOKENS, to_tokens, MAX_PROJECT_NAME_LEN, MAX_TASK_NAME_LEN,
)
from bot import start_bot_polling, active_commands as bot_active_commands, FEATURES as BOT_FEATURES, PHOTO_HELP as BOT_PHOTO_HELP
from scheduler import scheduler_loop, spawn_script
import auth
import pipeline
import vacation
import tgproxy
import storage
import ics
import activity

ensure_schema()

MAX_NAME_LEN = MAX_PROJECT_NAME_LEN
MAX_NOTES_LEN = 20000
MAX_PHOTO_BYTES = 6 * 1024 * 1024
MAX_REFS = 20
MAX_REF_BYTES = 25 * 1024 * 1024
MAX_THUMB_BYTES = 1536 * 1024
PHOTOS_DIR = os.path.join(BASE_DIR, "photos")
REFS_DIR = os.path.join(PHOTOS_DIR, "refs")
os.makedirs(REFS_DIR, exist_ok=True)

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# Route handlers that touch the database or files are plain `def`: FastAPI runs them in its thread pool,
# so a slow SD card or a long query never blocks the event loop shared with the Telegram bot and the scheduler.
# Handlers stay `async def` only when they await something or start background tasks (spawn_script).


def validate_name(raw, max_len=MAX_NAME_LEN, empty_key="err.project_name_empty"):
    """Returns (clean_name, None) or (None, error_text)."""
    name = (raw or "").strip()
    if not name:
        return None, tr(empty_key)
    if len(name) > max_len:
        return None, tr("err.name_too_long", n=max_len)
    return name, None


def parse_token_field(value):
    """(whole_number, None) or (None, error_text) for a token amount from the client (a reward or a cost).
    Rejects fractions, negatives, NaN/Infinity and absurdly large values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None, tr("err.tokens_whole", n=MAX_TOKENS)
    if value != int(value) or not 0 <= value <= MAX_TOKENS:
        return None, tr("err.tokens_whole", n=MAX_TOKENS)
    return int(value), None


def parse_date_field(value):
    """(datetime_or_None, None) or (None, error_text) for a 'YYYY-MM-DD' field from the client."""
    try:
        return parse_iso_date(value), None
    except (TypeError, ValueError):
        return None, tr("err.bad_date")


async def trash_loop():
    """Every few hours (and right at start-up) erases projects whose time in the trash is over."""
    while True:
        try:
            n = await asyncio.to_thread(purge_expired_trash)
            if n:
                print(f"🗑 Erased from the trash: {n} project(s)")
        except Exception as e:
            print(f"[TRASH ERROR] {e!r}")
        await asyncio.sleep(6 * 3600)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    logs.setup_app_log()
    print("🚀 Starting the bot and the scheduler...")
    bot_task = asyncio.create_task(start_bot_polling())
    scheduler_task = asyncio.create_task(scheduler_loop())
    trash_task = asyncio.create_task(trash_loop())
    try:
        n = await asyncio.to_thread(activity.backfill_once)
        if n:
            print(f"📜 Journal: {n} past event(s) restored from completion dates")
    except Exception as e:
        print(f"[JOURNAL] backfill failed: {e!r}")
    yield
    bot_task.cancel()
    scheduler_task.cancel()
    trash_task.cancel()

app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
# The translation table goes into the page as is (UTF-8) rather than as \uXXXX: the page is smaller;
# <, > and & are still escaped, so the data cannot close the <script> tag.
templates.env.globals["tr"] = tr          # in templates: {{ tr('common.save') }}
# Bot help in Settings -> Telegram is built from the same registry as /help in bot.py, so the texts never diverge
templates.env.globals["bot_help"] = lambda: {"commands": bot_active_commands(), "photos": BOT_PHOTO_HELP, "features": BOT_FEATURES}
templates.env.policies["json.dumps_kwargs"] = {"ensure_ascii": False, "sort_keys": True}
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
app.mount("/photos", StaticFiles(directory=PHOTOS_DIR), name="photos")
auth.setup(app, templates)               # password login: off by default (Settings → Access)


def remove_photo_file(filename):
    if not filename:
        return
    try:
        os.remove(os.path.join(PHOTOS_DIR, os.path.basename(filename)))
    except OSError:
        pass


def decode_data_url(data_url: str):
    m = re.match(r"^data:[^;,]*;base64,(.+)$", data_url or "", re.S)
    if not m:
        raise ValueError(tr("err.bad_image_data"))
    try:
        return base64.b64decode(m.group(1), validate=True)
    except Exception:
        raise ValueError(tr("err.image_unreadable"))


def remove_ref_files(*names):
    for name in names:
        if name:
            try:
                os.remove(os.path.join(REFS_DIR, os.path.basename(name)))
            except OSError:
                pass


def list_dir_names(path) -> set:
    """File names in a folder (one directory read instead of a stat call per file)."""
    try:
        return set(os.listdir(path))
    except OSError:
        return set()


def photo_url(filename, existing: set = None):
    """URL of a cover photo, or None if the file is missing on disk."""
    if not filename:
        return None
    name = os.path.basename(filename)
    present = (name in existing) if existing is not None else os.path.exists(os.path.join(PHOTOS_DIR, name))
    return f"/photos/{name}" if present else None


def get_project(db: Session, project_id: str, include_trash=False):
    q = db.query(Project).filter(Project.id == project_id)
    if not include_trash:
        q = q.filter(Project.deleted_at.is_(None))
    return q.first()


# ==================== MODELS ====================
class ActionPayload(BaseModel):
    action: str
    id: Optional[str] = None
    status: Optional[str] = None
    stage: Optional[str] = None

class ProjectCreate(BaseModel):
    name: str
    priority: str = "Medium"
    estimate: Optional[str] = None
    token_reward: float = 0         # a whole number; float here only so that 2.5 gets a readable error, not a 422
    status: str = STATUS_PLANNED

class ProjectEdit(BaseModel):
    name: str
    priority: str
    estimate: Optional[str] = None

class ProjectRename(BaseModel):
    name: str
    priority: Optional[str] = None      # None: leave the priority as is

class PhotoUpload(BaseModel):
    image: str  # data:image/jpeg;base64,... (the browser resizes and converts to JPEG)

class RefUpload(BaseModel):
    image: str                     # the original file as is (data URL, base64)
    thumb: Optional[str] = None    # small JPEG preview made by the browser

class ProgressUpload(BaseModel):
    image: str                     # JPEG resized by the browser (data URL)
    thumb: Optional[str] = None    # small preview
    note: str = ""

class RefOrder(BaseModel):
    ids: List[int]                 # reference ids in the new order

class ProgressNote(BaseModel):
    note: str = ""

class PaintNotesUpdate(BaseModel):
    paint_notes: str = ""

class TaskCreate(BaseModel):
    project_id: str
    task_name: str
    deadline: Optional[str] = None

class TaskEdit(BaseModel):
    task_name: str
    deadline: Optional[str] = None

class DaySchedule(BaseModel):
    enabled: bool = True
    time: str = "18:05"

class ConfigUpdate(BaseModel):
    dev_mode: bool
    project_cost: float = 10        # a whole number, checked by parse_token_field
    delete_keeps_balance: bool = True
    calendar_export: Optional[bool] = None      # None: an older page without the field, keep the setting
    tg_cleanup: Optional[str] = None            # keep_46h | latest_only | off; None keeps the setting
    allow_postpone: bool = True
    stagnation_alert: bool = True
    stagnation_days: int = 10
    friday_digest: bool = True
    monthly_report: bool = True
    notify_enabled: bool = True
    notify_days: Optional[Dict[str, DaySchedule]] = None
    backup_enabled: bool = False
    backup_time: str = DEFAULT_SCHEDULE["backup_time"]
    language: Optional[str] = None
    date_format: Optional[str] = None

class TemplateStep(BaseModel):
    index: int                         # step number in the template (from 0)
    deadline: Optional[str] = None     # 'YYYY-MM-DD' or empty

class TemplateRequest(BaseModel):
    kind: str = "print"                # template id from config.json (PIPELINE_TEMPLATES)
    # None: all steps with the project deadline; a list: only these steps, each with its own deadline
    steps: Optional[List[TemplateStep]] = None

class TemplatesUpdate(BaseModel):
    templates: Optional[List[dict]] = None
    reset: bool = False

class VacationRequest(BaseModel):
    days: int

class ProxyForm(BaseModel):
    enabled: bool = False
    url: Optional[str] = None          # empty: keep the current address


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ==================== ROUTES ====================
# PWA (install on a phone home screen). The service worker and the manifest are served from the site root:
# a service worker only controls URLs below its own, so from /static/ it could not serve the main page.
@app.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(os.path.join(BASE_DIR, "static", "sw.js"), media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest", include_in_schema=False)
def web_manifest():
    return FileResponse(os.path.join(BASE_DIR, "static", "manifest.webmanifest"),
                        media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    lang = get_lang()
    return templates.TemplateResponse(
        request=request, name="index.html",
        context={"lang": lang, "i18n": table_for(lang), "date_format": load_config()["DATE_FORMAT"],
                 "calendar_export": ics.enabled()},
    )

@app.get("/api/data")
def get_data(db: Session = Depends(get_db)):
    config = load_config()
    projects = live_projects(db).all()
    tasks = live_tasks(db).all()
    live_ids = {p.id for p in projects}

    current_tokens, earned = compute_balance(db, config)

    covers = list_dir_names(PHOTOS_DIR)
    proj_list = [{
        "ID": p.id,
        "Name": p.name,
        "Status": p.status,
        "Stage": p.stage or "—",
        "Priority": p.priority,
        "Estimate": iso_date(p.estimate),
        "Token Reward": to_tokens(p.token_reward),
        "PaintNotes": p.paint_notes or "",
        "Photo": photo_url(p.photo, covers),
        "CompletedAt": iso_date(p.completed_at),
    } for p in projects]

    ref_files = list_dir_names(REFS_DIR)
    refs_by_project = {}
    for r in db.query(Reference).filter(Reference.project_id.in_(live_ids)).order_by(Reference.position, Reference.id):
        name = os.path.basename(r.filename)
        if name not in ref_files:
            continue
        thumb = os.path.basename(r.thumb) if r.thumb else ""
        refs_by_project.setdefault(r.project_id, []).append({
            "ID": r.id,
            "Url": f"/photos/refs/{name}",
            "Thumb": f"/photos/refs/{thumb if thumb in ref_files else name}",
        })
    progress_by_project = progress.serialize_all(db)
    for pr in proj_list:
        pr["Refs"] = refs_by_project.get(pr["ID"], [])
        pr["Progress"] = progress_by_project.get(pr["ID"], [])

    task_list = [{
        "ID": t.id,
        "Project ID": t.project_id,
        "Task": t.task_name,
        "Deadline": iso_date(t.deadline),
        "Status": t.status,
        "Postponed": t.postponed or 0,
        "CompletedAt": iso_date(t.completed_at),
    } for t in tasks]

    return {
        "projects": proj_list,
        "tasks": task_list,
        "total_tokens": current_tokens,
        "earned_tokens": earned,
        "dev_mode": config.get("DEV_MODE", False),
        "project_cost": to_tokens(config.get("PROJECT_COST", 10), 10),
        "delete_keeps_balance": config.get("DELETE_KEEPS_BALANCE", True),
        "calendar_export": bool(config.get("CALENDAR_EXPORT")),
        "tg_cleanup": config.get("TG_CLEANUP", "keep_46h"),
        "notify_settings": config.get("NOTIFY", DEFAULT_NOTIFY_SETTINGS),
        "schedule_settings": config.get("SCHEDULE", DEFAULT_SCHEDULE),
        "language": get_lang(),
        "languages": available_languages(),
        "date_format": config.get("DATE_FORMAT", "dmy"),
        "trash": serialize_trash(db, config),
        "trash_days": config.get("TRASH_DAYS", 30),
        "vacation": vacation.state(config),
    }

@app.post("/api/config")
def update_config_route(data: ConfigUpdate):
    for key, day in (data.notify_days or {}).items():
        if key not in DAY_KEYS:
            return {"status": "error", "message": tr("err.unknown_day", key=key)}
        if not TIME_RE.match(day.time or ""):
            return {"status": "error", "message": tr("err.bad_time_day", day=tv(DAY_LABELS[key]))}
    if not TIME_RE.match(data.backup_time or ""):
        return {"status": "error", "message": tr("err.bad_time_backup")}
    if data.language is not None and normalize_lang(data.language) is None:
        return {"status": "error", "message": tr("err.unknown_language")}
    if data.tg_cleanup is not None and data.tg_cleanup not in ("keep_46h", "latest_only", "off"):
        return {"status": "error", "message": tr("err.bad_value")}
    if data.date_format is not None and data.date_format not in DATE_FORMATS:
        return {"status": "error", "message": tr("err.bad_date_format")}
    project_cost, err = parse_token_field(data.project_cost)
    if err:
        return {"status": "error", "message": err}

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
            "backup_enabled": data.backup_enabled,
            "backup_time": data.backup_time
        }
    return {"status": "ok"}


@app.post("/api/vacation")
def start_vacation(data: VacationRequest, db: Session = Depends(get_db)):
    with activity.quiet_deadlines():
        err, moved = vacation.start(db, data.days)
    if not err:
        activity.log_vacation(data.days, moved)
    if err == "bad_days":
        return {"status": "error", "message": tr("err.vacation_days", n=vacation.MAX_DAYS)}
    if err == "already":
        return {"status": "error", "message": tr("err.vacation_active")}
    return {"status": "ok", "vacation": vacation.state(), **moved}


@app.post("/api/vacation/cancel")
def cancel_vacation():
    vacation.cancel()
    return {"status": "ok", "vacation": vacation.state()}

@app.post("/api/notify/test")
async def trigger_test_notify():
    spawn_script("notify.py", ["--test"])
    return {"status": "ok"}

def telegram_state() -> dict:
    cfg = load_config()
    bound = is_chat_bound(cfg)
    code = None if bound else ensure_pairing_code()
    return {
        "bound": bound,
        "chat_id": cfg.get("TG_CHAT_ID") if bound else None,
        "pairing_code": format_pairing_code(code) if code else None,
    }

@app.get("/api/telegram")
def telegram_status():
    return telegram_state()

@app.post("/api/telegram/unlink")
def telegram_unlink():
    unlink_chat()
    return {"status": "ok", **telegram_state()}

@app.post("/api/telegram/code")
def telegram_new_code():
    if is_chat_bound():
        return {"status": "error", "message": tr("err.bot_already_linked")}
    new_pairing_code()
    return {"status": "ok", **telegram_state()}


PROXY_ERRORS = {"no_url": "tg.proxy_err_no_url", "bad_scheme": "tg.proxy_err_scheme",
                "bad_host": "tg.proxy_err_host", "too_long": "tg.proxy_err_long"}


@app.get("/api/telegram/proxy")
def telegram_proxy_state():
    return tgproxy.state()


@app.post("/api/telegram/proxy")
def telegram_proxy_save(data: ProxyForm):
    err = tgproxy.save(data.enabled, data.url)
    if err:
        return {"status": "error", "message": tr(PROXY_ERRORS.get(err, "settings.err_save"))}
    return {"status": "ok", **tgproxy.state()}


@app.post("/api/telegram/proxy/check")
async def telegram_proxy_check(data: ProxyForm):
    """Checks the connection to Telegram with what is in the form right now (before saving)."""
    url = tgproxy.resolve_new_url(data.url, load_config()) if data.enabled else None
    if data.enabled:
        if not url:
            return {"ok": False, "message": tr("tg.proxy_err_no_url")}
        err = tgproxy.validate(url)
        if err:
            return {"ok": False, "message": tr(PROXY_ERRORS[err])}
    res = await asyncio.to_thread(tgproxy.check, os.getenv("TELEGRAM_BOT_TOKEN"), url)
    via = tr("tg.proxy_via", url=tgproxy.mask(url)) if url else tr("tg.proxy_direct")
    if res["ok"]:
        return {"ok": True, "message": tr("tg.proxy_check_ok", bot="@" + str(res.get("username")), via=via)}
    if res["error"] == "no_token":
        return {"ok": False, "message": tr("tg.proxy_err_token")}
    return {"ok": False, "message": tr("tg.proxy_check_fail", via=via, detail=res.get("detail") or "")}

# ---------- Calendar for a phone: a .ics file and an optional subscription address ----------
class CalendarFeedBody(BaseModel):
    enabled: bool = False
    new_token: bool = False


def ics_response(data: bytes, download: bool) -> Response:
    headers = {"Cache-Control": "no-store"}
    if download:
        headers["Content-Disposition"] = 'attachment; filename="deadlines.ics"'
    return Response(content=data, media_type="text/calendar; charset=utf-8", headers=headers)


@app.get("/api/calendar.ics")
def calendar_file(request: Request, db: Session = Depends(get_db)):
    if not ics.enabled():
        return Response(status_code=404)
    return ics_response(ics.build(db, str(request.base_url)), download=True)


@app.get("/api/calendar/feed")
def calendar_feed_state():
    return ics.feed_state()


@app.post("/api/calendar/feed")
def calendar_feed_set(body: CalendarFeedBody):
    if not ics.enabled():
        return {"status": "error", "message": tr("calexp.err_off")}
    return {"status": "ok", **ics.set_feed(body.enabled, body.new_token)}


@app.get("/cal/{name}", include_in_schema=False)
def calendar_feed(name: str, request: Request, db: Session = Depends(get_db)):
    """Public (no login, see auth.py): the token in the address is the password. Off or wrong token: 404."""
    if not name.endswith(".ics") or not ics.token_ok(name[:-4]):
        return Response(status_code=404)
    return ics_response(ics.build(db, str(request.base_url)), download=False)


@app.get("/api/activity")
def get_activity(project: str = "", offset: int = 0, db: Session = Depends(get_db)):
    """Project journal for the Feed tab, newest first, 200 per page."""
    limit = 200
    rows = activity.list_events(db, project or None, offset, limit + 1)
    names = {p.id: p.name for p in db.query(Project.id, Project.name).all()}
    live = {pid for (pid,) in live_projects(db).with_entities(Project.id).all()}
    items = [{
        "id": ev.id,
        "date": iso_date(ev.created_at),
        "time": ev.created_at.strftime("%H:%M") if ev.created_at and ev.source != "history" else "",
        "project_id": ev.project_id,
        "project": names.get(ev.project_id) or ev.project_name or "",
        "open": ev.project_id in live,
        "kind": ev.kind,
        "text": activity.describe(ev),
        "source": ev.source,
    } for ev in rows[:limit]]
    return {"items": items, "more": len(rows) > limit}


@app.get("/api/storage")
def storage_usage():
    """Space used by the app, the database, photos and logs (folders are walked on every request)."""
    return {"status": "ok", **storage.usage()}


def set_project_status(proj: Project, status: str, db: Session, now: datetime) -> None:
    """Changes the status. Finishing a project closes its open tasks and remembers their previous status;
    reopening it brings exactly those tasks back (tasks closed by hand stay closed) and clears the finish date.
    Dates that are already set are kept, so the analytics charts do not move when nothing really changed."""
    was_done = is_done(proj.status)
    proj.status = status
    proj.updated_at = now
    tasks = db.query(Task).filter(Task.project_id == proj.id)
    if is_done(status):
        if not was_done or proj.completed_at is None:
            proj.completed_at = now
        for task in tasks:
            if not is_done(task.status):
                task.closed_from = task.status if task.status in TASK_STATUSES else TASK_NOT_STARTED
                task.status = STATUS_FINISHED
                task.completed_at = now
    else:
        proj.completed_at = None
        if was_done:
            for task in tasks:
                if task.closed_from and is_done(task.status):
                    task.status = task.closed_from
                    task.completed_at = None
                task.closed_from = ""


def set_task_status(task: Task, status: str, now: datetime) -> None:
    was_done = is_done(task.status)
    task.closed_from = ""                  # changed by hand: no longer "closed together with the project"
    task.status = status
    if not is_done(status):
        task.completed_at = None
    elif not was_done or task.completed_at is None:
        task.completed_at = now


@app.post("/api/action")
def do_action(payload: ActionPayload, db: Session = Depends(get_db)):
    now = datetime.now()
    if payload.action == "update_project_status":
        if payload.status not in PROJECT_STATUSES:
            return {"status": "error", "message": tr("err.bad_value")}
        proj = get_project(db, payload.id)
        if proj:
            set_project_status(proj, payload.status, db, now)
            db.commit()
        return {"status": "ok"}

    if payload.action == "update_project_stage":
        if payload.stage not in STAGES:
            return {"status": "error", "message": tr("err.bad_value")}
        proj = get_project(db, payload.id)
        if proj:
            proj.stage = payload.stage
            proj.updated_at = now
            db.commit()
        return {"status": "ok"}

    if payload.action == "update_task_status":
        if payload.status not in TASK_STATUSES:
            return {"status": "error", "message": tr("err.bad_value")}
        task = db.query(Task).filter(Task.id == payload.id).first()
        if task:
            set_task_status(task, payload.status, now)
            proj = get_project(db, task.project_id)
            if proj:
                proj.updated_at = now
            db.commit()
        return {"status": "ok"}
    return {"status": "unknown_action"}

@app.post("/api/projects")
def create_project(data: ProjectCreate, db: Session = Depends(get_db)):
    name, err = validate_name(data.name)
    if err:
        return {"status": "error", "message": err}
    if data.priority not in PRIORITIES or data.status not in PROJECT_STATUSES:
        return {"status": "error", "message": tr("err.bad_value")}
    reward, err = parse_token_field(data.token_reward)
    if err:
        return {"status": "error", "message": err}
    est_date, err = parse_date_field(data.estimate)
    if err:
        return {"status": "error", "message": err}

    config = load_config()
    current_tokens, _ = compute_balance(db, config)
    project_cost = to_tokens(config.get("PROJECT_COST", 10), 10)
    dev_mode = config.get("DEV_MODE", False)

    if not dev_mode and current_tokens < project_cost:
        return {"status": "error", "message": tr("err.not_enough_tokens", have=current_tokens, cost=project_cost)}

    now = datetime.now()
    new_proj = Project(
        name=name, status=data.status, priority=data.priority,
        estimate=est_date, token_reward=reward,
        token=project_cost if not dev_mode else 0,
        paint_notes="",
        updated_at=now,
        completed_at=now if is_done(data.status) else None,
    )
    db.add(new_proj)
    db.commit()
    return {"status": "ok", "id": new_proj.id}

@app.post("/api/projects/{project_id}/edit")
def edit_project(project_id: str, data: ProjectEdit, db: Session = Depends(get_db)):
    if not load_config().get("DEV_MODE", False):
        return {"status": "error", "message": tr("err.editing_disabled")}
    name, err = validate_name(data.name)
    if err:
        return {"status": "error", "message": err}
    if data.priority not in PRIORITIES:
        return {"status": "error", "message": tr("err.bad_value")}
    estimate, err = parse_date_field(data.estimate)
    if err:
        return {"status": "error", "message": err}
    proj = get_project(db, project_id)
    if not proj:
        return {"status": "error", "message": tr("err.project_not_found")}
    proj.name = name
    proj.priority = data.priority
    proj.estimate = estimate
    proj.updated_at = datetime.now()
    db.commit()
    return {"status": "ok"}

@app.post("/api/projects/{project_id}/rename")
def rename_project(project_id: str, data: ProjectRename, db: Session = Depends(get_db)):
    """Name and priority: always allowed, regardless of DEV_MODE (the deadline needs DEV_MODE, see /edit)."""
    name, err = validate_name(data.name)
    if err:
        return {"status": "error", "message": err}
    if data.priority is not None and data.priority not in PRIORITIES:
        return {"status": "error", "message": tr("err.bad_value")}
    proj = get_project(db, project_id)
    if not proj:
        return {"status": "error", "message": tr("err.project_not_found")}
    # updated_at is left alone: renaming or reprioritizing is not progress on the project
    # (otherwise it would reset the stagnation detector).
    proj.name = name
    if data.priority is not None:
        proj.priority = data.priority
    db.commit()
    return {"status": "ok", "name": name}

@app.post("/api/projects/{project_id}/photo")
def set_project_photo(project_id: str, data: PhotoUpload, db: Session = Depends(get_db)):
    proj = get_project(db, project_id)
    if not proj:
        return {"status": "error", "message": tr("err.project_not_found")}
    m = re.match(r"^data:image/jpeg;base64,(.+)$", data.image or "", re.S)
    if not m:
        return {"status": "error", "message": tr("err.expect_jpeg")}
    try:
        raw = base64.b64decode(m.group(1), validate=True)
    except Exception:
        return {"status": "error", "message": tr("err.image_unreadable")}
    if len(raw) > MAX_PHOTO_BYTES:
        return {"status": "error", "message": tr("err.photo_too_big")}
    if not raw.startswith(b"\xff\xd8\xff"):
        return {"status": "error", "message": tr("err.not_jpeg")}

    # a random suffix: two uploads within one second must not overwrite each other's file
    filename = f"{proj.id}_{int(time.time())}_{uuid.uuid4().hex[:4]}.jpg"
    with open(os.path.join(PHOTOS_DIR, filename), "wb") as f:
        f.write(raw)
    old = proj.photo
    proj.photo = filename
    db.commit()
    remove_photo_file(old)
    return {"status": "ok", "photo": f"/photos/{filename}"}

@app.delete("/api/projects/{project_id}/photo")
def delete_project_photo(project_id: str, db: Session = Depends(get_db)):
    proj = get_project(db, project_id)
    if not proj:
        return {"status": "error", "message": tr("err.project_not_found")}
    old = proj.photo
    proj.photo = ""
    db.commit()
    remove_photo_file(old)
    return {"status": "ok"}

@app.post("/api/projects/{project_id}/refs")
def add_project_ref(project_id: str, data: RefUpload, db: Session = Depends(get_db)):
    proj = get_project(db, project_id)
    if not proj:
        return {"status": "error", "message": tr("err.project_not_found")}
    if db.query(Reference).filter(Reference.project_id == project_id).count() >= MAX_REFS:
        return {"status": "error", "message": tr("ref.limit", n=MAX_REFS)}
    try:
        raw = decode_data_url(data.image)
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    if len(raw) > MAX_REF_BYTES:
        return {"status": "error", "message": tr("err.file_too_big", n=MAX_REF_BYTES // 1024 // 1024)}
    ext = detect_image_ext(raw)
    if not ext:
        return {"status": "error", "message": tr("err.supported_formats")}

    base = f"{proj.id}_{uuid.uuid4().hex[:8]}"
    filename = f"{base}.{ext}"
    with open(os.path.join(REFS_DIR, filename), "wb") as f:
        f.write(raw)                       # the original is stored byte for byte

    thumb_name = ""
    if data.thumb:
        try:
            thumb_raw = decode_data_url(data.thumb)
            if thumb_raw.startswith(b"\xff\xd8\xff") and len(thumb_raw) <= MAX_THUMB_BYTES:
                thumb_name = f"{base}_t.jpg"
                with open(os.path.join(REFS_DIR, thumb_name), "wb") as f:
                    f.write(thumb_raw)
        except ValueError:
            thumb_name = ""

    last = db.query(func.max(Reference.position)).filter(Reference.project_id == project_id).scalar()
    ref = Reference(project_id=project_id, filename=filename, thumb=thumb_name, position=(last or 0) + 1)
    db.add(ref)
    db.commit()
    return {"status": "ok", "id": ref.id}

@app.post("/api/projects/{project_id}/refs/order")
def reorder_project_refs(project_id: str, data: RefOrder, db: Session = Depends(get_db)):
    """New order of the references: the full list of their ids, first to last."""
    refs = {r.id: r for r in db.query(Reference).filter(Reference.project_id == project_id).all()}
    if sorted(data.ids) != sorted(refs):
        return {"status": "error", "message": tr("ref.err_order")}
    for pos, ref_id in enumerate(data.ids, start=1):
        refs[ref_id].position = pos
    db.commit()
    return {"status": "ok"}

@app.delete("/api/projects/{project_id}/refs/{ref_id}")
def delete_project_ref(project_id: str, ref_id: int, db: Session = Depends(get_db)):
    ref = db.query(Reference).filter(Reference.id == ref_id, Reference.project_id == project_id).first()
    if not ref:
        return {"status": "error", "message": tr("err.ref_not_found")}
    names = (ref.filename, ref.thumb)
    db.delete(ref)
    db.commit()
    remove_ref_files(*names)
    return {"status": "ok"}

@app.post("/api/projects/{project_id}/progress")
def add_progress_photo(project_id: str, data: ProgressUpload, db: Session = Depends(get_db)):
    try:
        raw = decode_data_url(data.image)
        thumb = decode_data_url(data.thumb) if data.thumb else None
        row = progress.add_photo(db, project_id, raw, thumb, data.note, "web")
    except progress.ProgressError as e:
        return {"status": "error", "message": tr(e.key, **e.params)}
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    return {"status": "ok", "photo": progress.serialize(row)}

@app.patch("/api/projects/{project_id}/progress/{photo_id}")
def edit_progress_note(project_id: str, photo_id: int, data: ProgressNote, db: Session = Depends(get_db)):
    row = db.query(ProgressPhoto).filter(ProgressPhoto.id == photo_id, ProgressPhoto.project_id == project_id).first()
    if not row:
        return {"status": "error", "message": tr("progress.not_found")}
    row.note = (data.note or "").strip()[:progress.MAX_NOTE_LEN]
    db.commit()
    return {"status": "ok"}

@app.delete("/api/projects/{project_id}/progress/{photo_id}")
def delete_progress_photo(project_id: str, photo_id: int, db: Session = Depends(get_db)):
    if not progress.delete_photo(db, photo_id, project_id):
        return {"status": "error", "message": tr("progress.not_found")}
    return {"status": "ok"}

@app.post("/api/projects/{project_id}/notes")
def update_paint_notes(project_id: str, data: PaintNotesUpdate, db: Session = Depends(get_db)):
    proj = get_project(db, project_id)
    if not proj:
        return {"status": "error", "message": tr("err.project_not_found")}
    notes = (data.paint_notes or "")[:MAX_NOTES_LEN]
    if notes != (proj.paint_notes or ""):          # saving unchanged notes (on blur) is not progress
        proj.paint_notes = notes
        proj.updated_at = datetime.now()
        db.commit()
    return {"status": "ok"}

def _step_match_key(name: str) -> str:
    """A step name for comparison: without a leading number ("2. ", "3) "), outer spaces and case.
    This way tasks created by older numbered templates are recognized too."""
    return re.sub(r"^\s*\d+\s*[.)]\s*", "", name or "").strip().casefold()


def existing_template_steps(db: Session, project_id: str, templates: list) -> Dict[str, List[int]]:
    """Step numbers of every template that already exist among the project's tasks (in any UI language)."""
    have = {_step_match_key(name) for (name,) in db.query(Task.task_name).filter(Task.project_id == project_id)}
    return {t["id"]: [i for i, raw in enumerate(t["steps"])
                      if any(_step_match_key(v) in have for v in pipeline.step_variants(raw))]
            for t in templates}


@app.get("/api/pipeline-templates")
def get_pipeline_templates(project_id: Optional[str] = None, db: Session = Depends(get_db)):
    """Templates in the UI language, the built-in ones (for "Reset") and, for project_id, the steps the project already has."""
    cfg = load_config()
    raw = pipeline.get_templates(cfg)
    existing = existing_template_steps(db, project_id, raw) if project_id else {t["id"]: [] for t in raw}
    return {"templates": pipeline.for_client(cfg), "defaults": pipeline.defaults_for_client(),
            "existing": existing, "custom": "PIPELINE_TEMPLATES" in cfg}


@app.post("/api/pipeline-templates")
def save_pipeline_templates(data: TemplatesUpdate):
    with edit_config() as cfg:
        if data.reset:
            cfg.pop("PIPELINE_TEMPLATES", None)
        else:
            err = pipeline.save(cfg, data.templates)
            if err:
                return {"status": "error", "message": err}
    return {"status": "ok", "templates": pipeline.for_client(cfg)}

@app.post("/api/projects/{project_id}/template")
def apply_project_template(project_id: str, data: Optional[TemplateRequest] = None, db: Session = Depends(get_db)):
    proj = get_project(db, project_id)
    if not proj:
        return {"status": "error", "message": tr("err.project_not_found")}

    cfg = load_config()
    kind = (data.kind if data else "print")
    template = pipeline.find(cfg, kind)
    if template is None:
        return {"status": "error", "message": tr("err.unknown_template")}
    keys = template["steps"]

    if data is None or data.steps is None:
        plan = [(i, proj.estimate) for i in range(len(keys))]
    else:
        plan, seen = [], set()
        for step in sorted(data.steps, key=lambda x: x.index):
            if not 0 <= step.index < len(keys) or step.index in seen:
                return {"status": "error", "message": tr("err.bad_template_step")}
            seen.add(step.index)
            dead, err = parse_date_field(step.deadline)
            if err:
                return {"status": "error", "message": tr("err.bad_template_date")}
            plan.append((step.index, dead))
        if not plan:
            return {"status": "error", "message": tr("template.none_selected")}

    # Re-applying a template does not create duplicates: steps the project already has are skipped
    already = set(existing_template_steps(db, project_id, [template])[kind])
    skipped = sum(1 for i, _ in plan if i in already)
    plan = [(i, dead) for i, dead in plan if i not in already]
    if not plan:
        return {"status": "error", "message": tr("template.all_exist")}

    for i, dead in plan:
        db.add(Task(
            project_id=project_id,
            task_name=pipeline.text(keys[i]),
            deadline=dead,
            status=TASK_NOT_STARTED,
            postponed=0
        ))
    proj.updated_at = datetime.now()
    db.commit()
    return {"status": "ok", "added": len(plan), "skipped": skipped}

def purge_project(db: Session, proj: Project, config: dict = None) -> None:
    """Erases a project for good: tasks, photos, references, progress photos. The balance is kept with an adjustment."""
    config = config if config is not None else load_config()
    if config.get("DELETE_KEEPS_BALANCE", True):
        # The project's contribution to the balance: the reward (if finished) minus the start cost.
        # It is recorded as an adjustment, so deleting does not change the balance.
        reward = to_tokens(proj.token_reward) if is_done(proj.status) else 0
        contribution = reward - to_tokens(proj.token)
        if contribution:
            db.add(Adjustment(amount=contribution, reason=f"Deleted project «{proj.name}»"))
    photo_name = proj.photo
    ref_files = []
    for r in db.query(Reference).filter(Reference.project_id == proj.id).all():
        ref_files += [r.filename, r.thumb]
        db.delete(r)
    progress_files = progress.delete_project_photos(db, proj.id)
    db.query(Task).filter(Task.project_id == proj.id).delete()
    db.delete(proj)
    db.commit()
    remove_photo_file(photo_name)
    remove_ref_files(*ref_files)
    progress.remove_files(*progress_files)


def purge_expired_trash() -> int:
    """Erases projects that stayed in the trash longer than TRASH_DAYS. Returns how many were erased."""
    config = load_config()
    cutoff = datetime.now() - timedelta(days=config["TRASH_DAYS"])
    db = SessionLocal()
    try:
        old = db.query(Project).filter(Project.deleted_at.isnot(None), Project.deleted_at < cutoff).all()
        for proj in old:
            purge_project(db, proj, config)
        return len(old)
    finally:
        db.close()


def serialize_trash(db: Session, config: dict = None) -> list:
    days = (config or load_config())["TRASH_DAYS"]
    now = datetime.now()
    trashed = db.query(Project).filter(Project.deleted_at.isnot(None)).order_by(Project.deleted_at.desc()).all()
    if not trashed:
        return []
    counts = dict(db.query(Task.project_id, func.count(Task.id))
                  .filter(Task.project_id.in_([p.id for p in trashed])).group_by(Task.project_id))
    return [{
        "ID": p.id, "Name": p.name, "Status": p.status,
        "DeletedAt": iso_date(p.deleted_at), "DaysLeft": max(0, days - (now - p.deleted_at).days),
        "Tasks": counts.get(p.id, 0),
    } for p in trashed]


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str, db: Session = Depends(get_db)):
    """Deleting = moving to the trash. The project is erased for good after TRASH_DAYS days or by hand."""
    proj = get_project(db, project_id)
    if proj:
        proj.deleted_at = datetime.now()
        db.commit()
    return {"status": "ok"}

@app.post("/api/projects/{project_id}/restore")
def restore_project(project_id: str, db: Session = Depends(get_db)):
    proj = db.query(Project).filter(Project.id == project_id, Project.deleted_at.isnot(None)).first()
    if not proj:
        return {"status": "error", "message": tr("trash.not_found")}
    proj.deleted_at = None
    proj.updated_at = datetime.now()
    db.commit()
    return {"status": "ok"}

@app.delete("/api/trash/{project_id}")
def purge_trashed_project(project_id: str, db: Session = Depends(get_db)):
    proj = db.query(Project).filter(Project.id == project_id, Project.deleted_at.isnot(None)).first()
    if not proj:
        return {"status": "error", "message": tr("trash.not_found")}
    purge_project(db, proj)
    return {"status": "ok"}

@app.delete("/api/trash")
def empty_trash(db: Session = Depends(get_db)):
    config = load_config()
    for proj in db.query(Project).filter(Project.deleted_at.isnot(None)).all():
        purge_project(db, proj, config)
    return {"status": "ok"}

@app.post("/api/tasks")
def create_task(data: TaskCreate, db: Session = Depends(get_db)):
    name, err = validate_name(data.task_name, MAX_TASK_NAME_LEN, "task.err_empty")
    if err:
        return {"status": "error", "message": err}
    proj = get_project(db, data.project_id)
    if proj is None:
        return {"status": "error", "message": tr("err.project_not_found")}
    dead_date, err = parse_date_field(data.deadline)
    if err:
        return {"status": "error", "message": err}
    new_task = Task(project_id=proj.id, task_name=name, deadline=dead_date, status=TASK_NOT_STARTED, postponed=0)
    db.add(new_task)
    proj.updated_at = datetime.now()
    db.commit()
    return {"status": "ok", "id": new_task.id}

@app.post("/api/tasks/{task_id}/edit")
def edit_task(task_id: str, data: TaskEdit, db: Session = Depends(get_db)):
    name, err = validate_name(data.task_name, MAX_TASK_NAME_LEN, "task.err_empty")
    if err:
        return {"status": "error", "message": err}
    deadline, err = parse_date_field(data.deadline)
    if err:
        return {"status": "error", "message": err}
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        return {"status": "error", "message": tr("bot.task_not_found")}
    task.task_name = name
    task.deadline = deadline
    db.commit()
    return {"status": "ok"}

@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if task:
        db.delete(task)
        db.commit()
    return {"status": "ok"}

if __name__ == "__main__":
    import tls
    try:
        options = tls.uvicorn_options()      # HTTPS=1 in .env turns on HTTPS (see tls.py)
    except tls.TlsError as e:
        raise SystemExit("HTTPS setup failed: " + str(e))
    logs.setup_app_log()
    print(f"Project Hub: {'https' if 'ssl_certfile' in options else 'http'}://0.0.0.0:{options['port']}")
    uvicorn.run("app:app", **options)
