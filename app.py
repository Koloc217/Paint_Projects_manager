import os
import re
import time
import uuid
import base64
import contextlib
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session
from fastapi.staticfiles import StaticFiles
import uvicorn

from database import SessionLocal, Project, Task, Adjustment, Reference, ProgressPhoto, ensure_schema
import progress
import logs
from i18n import tr, tv, get_lang, table_for, available_languages, normalize_lang, translate
from common import (
    BASE_DIR, DEFAULT_NOTIFY_SETTINGS, DEFAULT_SCHEDULE, DAY_KEYS, DAY_LABELS,
    is_done, load_config, save_config, compute_balance, DATE_FORMATS, detect_image_ext, live_projects, live_tasks,
    is_chat_bound, ensure_pairing_code, new_pairing_code, unlink_chat, format_pairing_code,
)
from bot import start_bot_polling
from scheduler import scheduler_loop, spawn_script
import auth
import pipeline
import vacation
import tgproxy
import storage

ensure_schema()

MAX_NAME_LEN = 120
MAX_PHOTO_BYTES = 6 * 1024 * 1024
MAX_REFS = 10
MAX_REF_BYTES = 25 * 1024 * 1024
MAX_THUMB_BYTES = 1536 * 1024
PHOTOS_DIR = os.path.join(BASE_DIR, "photos")
REFS_DIR = os.path.join(PHOTOS_DIR, "refs")
os.makedirs(REFS_DIR, exist_ok=True)
class TemplateStep(BaseModel):
    index: int                         # номер этапа в шаблоне (с 0)
    deadline: Optional[str] = None     # 'YYYY-MM-DD' или пусто


class TemplateRequest(BaseModel):
    kind: str = "print"                # id шаблона из config.json (PIPELINE_TEMPLATES)
    # None — все этапы со сроком проекта; список — только перечисленные, каждый со своим сроком
    steps: Optional[List[TemplateStep]] = None


TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def validate_name(raw):
    """Возвращает (очищенное_имя, None) или (None, текст_ошибки)."""
    name = (raw or "").strip()
    if not name:
        return None, tr("err.project_name_empty")
    if len(name) > MAX_NAME_LEN:
        return None, tr("err.name_too_long", n=MAX_NAME_LEN)
    return name, None


async def trash_loop():
    """Раз в несколько часов стирает проекты, у которых вышел срок в корзине (и сразу при запуске)."""
    while True:
        try:
            n = await asyncio.to_thread(purge_expired_trash)
            if n:
                print(f"🗑 Из корзины стёрто проектов: {n}")
        except Exception as e:
            print(f"[TRASH ERROR] {e!r}")
        await asyncio.sleep(6 * 3600)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    logs.setup_app_log()
    print("🚀 Инициализация бота и планировщика...")
    bot_task = asyncio.create_task(start_bot_polling())
    scheduler_task = asyncio.create_task(scheduler_loop())
    trash_task = asyncio.create_task(trash_loop())
    yield
    bot_task.cancel()
    scheduler_task.cancel()
    trash_task.cancel()

app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
# словарь перевода кладётся в страницу как есть (UTF-8), а не как \uXXXX: страница меньше; <, > и & по-прежнему экранируются
templates.env.globals["tr"] = tr          # в шаблонах: {{ tr('common.save') }}
templates.env.policies["json.dumps_kwargs"] = {"ensure_ascii": False, "sort_keys": True}
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
app.mount("/photos", StaticFiles(directory=PHOTOS_DIR), name="photos")
auth.setup(app, templates)               # вход по паролю: по умолчанию выключен (Настройки → Доступ)


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


def photo_url(filename):
    """URL фото или None, если файла нет на диске."""
    if filename and os.path.exists(os.path.join(PHOTOS_DIR, os.path.basename(filename))):
        return f"/photos/{os.path.basename(filename)}"
    return None

# ==================== МОДЕЛИ ====================
class ActionPayload(BaseModel):
    action: str
    id: str = None
    status: str = None
    stage: str = None

class ProjectCreate(BaseModel):
    name: str
    priority: str = "Medium"
    estimate: str = None
    token_reward: float = 0.0
    status: str = "В планах"

class ProjectEdit(BaseModel):
    name: str
    priority: str
    estimate: str = None

class ProjectRename(BaseModel):
    name: str

class PhotoUpload(BaseModel):
    image: str  # data:image/jpeg;base64,... (браузер сжимает и конвертирует в JPEG)

class RefUpload(BaseModel):
    image: str           # оригинал файла как есть (data URL, base64)
    thumb: str = None    # маленькое JPEG-превью, сделанное браузером

class ProgressUpload(BaseModel):
    image: str                 # JPEG, уменьшенный браузером (data URL)
    thumb: str = None          # маленькое превью
    note: str = ""

class ProgressNote(BaseModel):
    note: str = ""

class PaintNotesUpdate(BaseModel):
    paint_notes: str = ""

class TaskCreate(BaseModel):
    project_id: str
    task_name: str
    deadline: str = None

class TaskEdit(BaseModel):
    task_name: str
    deadline: str = None

class DaySchedule(BaseModel):
    enabled: bool = True
    time: str = "18:05"

class ConfigUpdate(BaseModel):
    dev_mode: bool
    project_cost: float = 10.0
    delete_keeps_balance: bool = True
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

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ==================== РОУТЫ ====================
# PWA (установка на экран телефона). Service worker и манифест отдаются из корня сайта: service worker
# действует только на адреса ниже своего, а из /static/ он не смог бы обслуживать главную страницу.
@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    return FileResponse(os.path.join(BASE_DIR, "static", "sw.js"), media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest", include_in_schema=False)
async def web_manifest():
    return FileResponse(os.path.join(BASE_DIR, "static", "manifest.webmanifest"),
                        media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    lang = get_lang()
    return templates.TemplateResponse(
        request=request, name="index.html",
        context={"lang": lang, "i18n": table_for(lang), "date_format": load_config()["DATE_FORMAT"]},
    )

@app.get("/api/data")
async def get_data(db: Session = Depends(get_db)):
    config = load_config()
    projects = live_projects(db).all()
    tasks = live_tasks(db).all()

    current_tokens, earned = compute_balance(db, config)

    proj_list = [{
        "ID": p.id,
        "Name": p.name,
        "Status": p.status,
        "Stage": getattr(p, 'stage', '—'),
        "Priority": p.priority,
        "Estimate": p.estimate.strftime("%Y-%m-%d") if p.estimate else None,
        "Token Reward": p.token_reward,
        "PaintNotes": getattr(p, 'paint_notes', '') or '',
        "Photo": photo_url(getattr(p, 'photo', '')),
        "CompletedAt": p.completed_at.strftime("%Y-%m-%d") if getattr(p, 'completed_at', None) else None
    } for p in projects]

    refs_by_project = {}
    for r in db.query(Reference).order_by(Reference.id).all():
        if not os.path.exists(os.path.join(REFS_DIR, os.path.basename(r.filename))):
            continue
        refs_by_project.setdefault(r.project_id, []).append({
            "ID": r.id,
            "Url": f"/photos/refs/{os.path.basename(r.filename)}",
            "Thumb": f"/photos/refs/{os.path.basename(r.thumb)}" if r.thumb and os.path.exists(os.path.join(REFS_DIR, os.path.basename(r.thumb))) else f"/photos/refs/{os.path.basename(r.filename)}",
        })
    progress_by_project = {}
    for ph in reversed(progress.list_photos(db)):          # от старых к новым: так идёт лента времени
        progress_by_project.setdefault(ph.project_id, []).append(progress.serialize(ph))
    for pr in proj_list:
        pr["Refs"] = refs_by_project.get(pr["ID"], [])
        pr["Progress"] = progress_by_project.get(pr["ID"], [])

    trash_list = serialize_trash(db)
    task_list = [{
        "ID": t.id,
        "Project ID": t.project_id,
        "Task": t.task_name,
        "Deadline": t.deadline.strftime("%Y-%m-%d") if t.deadline else None,
        "Status": t.status,
        "Postponed": getattr(t, 'postponed', 0),
        "CompletedAt": t.completed_at.strftime("%Y-%m-%d") if getattr(t, 'completed_at', None) else None
    } for t in tasks]

    return {
        "projects": proj_list,
        "tasks": task_list,
        "total_tokens": current_tokens,
        "earned_tokens": earned,
        "dev_mode": config.get("DEV_MODE", False),
        "project_cost": config.get("PROJECT_COST", 10),
        "delete_keeps_balance": config.get("DELETE_KEEPS_BALANCE", True),
        "notify_settings": config.get("NOTIFY", DEFAULT_NOTIFY_SETTINGS),
        "schedule_settings": config.get("SCHEDULE", DEFAULT_SCHEDULE),
        "language": get_lang(),
        "languages": available_languages(),
        "date_format": config.get("DATE_FORMAT", "dmy"),
        "trash": trash_list,
        "trash_days": config.get("TRASH_DAYS", 30),
        "vacation": vacation.state(config),
    }

@app.post("/api/config")
async def update_config_route(data: ConfigUpdate):
    for key, day in (data.notify_days or {}).items():
        if key not in DAY_KEYS:
            return {"status": "error", "message": tr("err.unknown_day", key=key)}
        if not TIME_RE.match(day.time or ""):
            return {"status": "error", "message": tr("err.bad_time_day", day=tv(DAY_LABELS[key]))}
    if not TIME_RE.match(data.backup_time or ""):
        return {"status": "error", "message": tr("err.bad_time_backup")}
    if data.language is not None and normalize_lang(data.language) is None:
        return {"status": "error", "message": tr("err.unknown_language")}
    if data.date_format is not None and data.date_format not in DATE_FORMATS:
        return {"status": "error", "message": tr("err.bad_date_format")}

    cfg = load_config()
    days = dict(cfg["SCHEDULE"]["notify_days"])
    for key, day in (data.notify_days or {}).items():
        days[key] = {"enabled": day.enabled, "time": day.time}
    cfg["DEV_MODE"] = data.dev_mode
    cfg["PROJECT_COST"] = data.project_cost
    cfg["DELETE_KEEPS_BALANCE"] = data.delete_keeps_balance
    if data.language is not None:
        cfg["LANGUAGE"] = normalize_lang(data.language)
    if data.date_format is not None:
        cfg["DATE_FORMAT"] = data.date_format
    cfg["NOTIFY"] = {
        "allow_postpone": data.allow_postpone,
        "stagnation_alert": data.stagnation_alert,
        "stagnation_days": max(1, data.stagnation_days),
        "friday_digest": data.friday_digest,
        "monthly_report": data.monthly_report
    }
    cfg["SCHEDULE"] = {
        "notify_enabled": data.notify_enabled,
        "notify_days": days,
        "backup_enabled": data.backup_enabled,
        "backup_time": data.backup_time
    }
    save_config(cfg)
    return {"status": "ok"}

class VacationRequest(BaseModel):
    days: int


@app.post("/api/vacation")
async def start_vacation(data: VacationRequest, db: Session = Depends(get_db)):
    err, moved = vacation.start(db, data.days)
    if err == "bad_days":
        return {"status": "error", "message": tr("err.vacation_days", n=vacation.MAX_DAYS)}
    if err == "already":
        return {"status": "error", "message": tr("err.vacation_active")}
    return {"status": "ok", "vacation": vacation.state(), **moved}


@app.post("/api/vacation/cancel")
async def cancel_vacation():
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
async def telegram_status():
    return telegram_state()

@app.post("/api/telegram/unlink")
async def telegram_unlink():
    unlink_chat()
    return {"status": "ok", **telegram_state()}

@app.post("/api/telegram/code")
async def telegram_new_code():
    if is_chat_bound():
        return {"status": "error", "message": tr("err.bot_already_linked")}
    new_pairing_code()
    return {"status": "ok", **telegram_state()}

class ProxyForm(BaseModel):
    enabled: bool = False
    url: Optional[str] = None          # пусто — оставить текущий адрес


PROXY_ERRORS = {"no_url": "tg.proxy_err_no_url", "bad_scheme": "tg.proxy_err_scheme",
                "bad_host": "tg.proxy_err_host", "too_long": "tg.proxy_err_long"}


@app.get("/api/telegram/proxy")
async def telegram_proxy_state():
    return tgproxy.state()


@app.post("/api/telegram/proxy")
async def telegram_proxy_save(data: ProxyForm):
    err = tgproxy.save(data.enabled, data.url)
    if err:
        return {"status": "error", "message": tr(PROXY_ERRORS.get(err, "settings.err_save"))}
    return {"status": "ok", **tgproxy.state()}


@app.post("/api/telegram/proxy/check")
async def telegram_proxy_check(data: ProxyForm):
    """Проверка связи с Telegram с тем, что сейчас в форме (до сохранения)."""
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

@app.get("/api/storage")
async def storage_usage():
    """Сколько места занимают приложение, база, фото и журналы (обход папок, считается при каждом запросе)."""
    return {"status": "ok", **(await asyncio.to_thread(storage.usage))}

@app.post("/api/action")
async def do_action(payload: ActionPayload, db: Session = Depends(get_db)):
    now = datetime.now()
    if payload.action == "update_project_status":
        proj = db.query(Project).filter(Project.id == payload.id).first()
        if proj:
            proj.status = payload.status
            proj.updated_at = now
            if is_done(payload.status):
                proj.completed_at = now
                db.query(Task).filter(Task.project_id == payload.id).update({"status": "Закончен", "completed_at": now})
            else:
                proj.completed_at = None
            db.commit()
        return {"status": "ok"}

    if payload.action == "update_project_stage":
        proj = db.query(Project).filter(Project.id == payload.id).first()
        if proj:
            proj.stage = payload.stage
            proj.updated_at = now
            db.commit()
        return {"status": "ok"}

    if payload.action == "update_task_status":
        task = db.query(Task).filter(Task.id == payload.id).first()
        if task:
            task.status = payload.status
            if is_done(payload.status):
                task.completed_at = now
            else:
                task.completed_at = None
            proj = db.query(Project).filter(Project.id == task.project_id).first()
            if proj:
                proj.updated_at = now
            db.commit()
        return {"status": "ok"}
    return {"status": "unknown_action"}

@app.post("/api/projects")
async def create_project(data: ProjectCreate, db: Session = Depends(get_db)):
    name, err = validate_name(data.name)
    if err:
        return {"status": "error", "message": err}

    config = load_config()
    current_tokens, _ = compute_balance(db, config)

    project_cost = config.get("PROJECT_COST", 10)
    dev_mode = config.get("DEV_MODE", False)

    if not dev_mode and current_tokens < project_cost:
        return {"status": "error", "message": tr("err.not_enough_tokens", have=current_tokens, cost=project_cost)}

    est_date = datetime.strptime(data.estimate, "%Y-%m-%d") if data.estimate else None
    new_proj = Project(
        name=name, status=data.status, priority=data.priority,
        estimate=est_date, token_reward=data.token_reward,
        token=project_cost if not dev_mode else 0,
        paint_notes="",
        updated_at=datetime.now()
    )
    db.add(new_proj)
    db.commit()
    return {"status": "ok", "id": new_proj.id}

@app.post("/api/projects/{project_id}/edit")
async def edit_project(project_id: str, data: ProjectEdit, db: Session = Depends(get_db)):
    config = load_config()
    if not config.get("DEV_MODE", False):
        return {"status": "error", "message": tr("err.editing_disabled")}
    name, err = validate_name(data.name)
    if err:
        return {"status": "error", "message": err}
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj:
        proj.name = name
        proj.priority = data.priority
        proj.estimate = datetime.strptime(data.estimate, "%Y-%m-%d") if data.estimate else None
        proj.updated_at = datetime.now()
        db.commit()
        return {"status": "ok"}
    return {"status": "error", "message": tr("err.project_not_found")}

@app.post("/api/projects/{project_id}/rename")
async def rename_project(project_id: str, data: ProjectRename, db: Session = Depends(get_db)):
    """Переименование доступно всегда, независимо от DEV_MODE."""
    name, err = validate_name(data.name)
    if err:
        return {"status": "error", "message": err}
    proj = db.query(Project).filter(Project.id == project_id).first()
    if not proj:
        return {"status": "error", "message": tr("err.project_not_found")}
    # updated_at не трогаем: переименование не считается движением по проекту
    # (иначе оно сбрасывало бы детектор застоя).
    proj.name = name
    db.commit()
    return {"status": "ok", "name": name}

@app.post("/api/projects/{project_id}/photo")
async def set_project_photo(project_id: str, data: PhotoUpload, db: Session = Depends(get_db)):
    proj = db.query(Project).filter(Project.id == project_id).first()
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

    filename = f"{proj.id}_{int(time.time())}.jpg"
    with open(os.path.join(PHOTOS_DIR, filename), "wb") as f:
        f.write(raw)
    old = getattr(proj, "photo", "")
    proj.photo = filename
    db.commit()
    remove_photo_file(old)
    return {"status": "ok", "photo": f"/photos/{filename}"}

@app.delete("/api/projects/{project_id}/photo")
async def delete_project_photo(project_id: str, db: Session = Depends(get_db)):
    proj = db.query(Project).filter(Project.id == project_id).first()
    if not proj:
        return {"status": "error", "message": tr("err.project_not_found")}
    old = getattr(proj, "photo", "")
    proj.photo = ""
    db.commit()
    remove_photo_file(old)
    return {"status": "ok"}

@app.post("/api/projects/{project_id}/refs")
async def add_project_ref(project_id: str, data: RefUpload, db: Session = Depends(get_db)):
    proj = db.query(Project).filter(Project.id == project_id).first()
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
        f.write(raw)                       # оригинал сохраняется байт в байт

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

    ref = Reference(project_id=project_id, filename=filename, thumb=thumb_name)
    db.add(ref)
    db.commit()
    return {"status": "ok", "id": ref.id}

@app.delete("/api/projects/{project_id}/refs/{ref_id}")
async def delete_project_ref(project_id: str, ref_id: int, db: Session = Depends(get_db)):
    ref = db.query(Reference).filter(Reference.id == ref_id, Reference.project_id == project_id).first()
    if not ref:
        return {"status": "error", "message": tr("err.ref_not_found")}
    names = (ref.filename, ref.thumb)
    db.delete(ref)
    db.commit()
    remove_ref_files(*names)
    return {"status": "ok"}

@app.post("/api/projects/{project_id}/progress")
async def add_progress_photo(project_id: str, data: ProgressUpload, db: Session = Depends(get_db)):
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
async def edit_progress_note(project_id: str, photo_id: int, data: ProgressNote, db: Session = Depends(get_db)):
    row = db.query(ProgressPhoto).filter(ProgressPhoto.id == photo_id, ProgressPhoto.project_id == project_id).first()
    if not row:
        return {"status": "error", "message": tr("progress.not_found")}
    row.note = (data.note or "").strip()[:progress.MAX_NOTE_LEN]
    db.commit()
    return {"status": "ok"}

@app.delete("/api/projects/{project_id}/progress/{photo_id}")
async def delete_progress_photo(project_id: str, photo_id: int, db: Session = Depends(get_db)):
    if not progress.delete_photo(db, photo_id, project_id):
        return {"status": "error", "message": tr("progress.not_found")}
    return {"status": "ok"}

@app.post("/api/projects/{project_id}/notes")
async def update_paint_notes(project_id: str, data: PaintNotesUpdate, db: Session = Depends(get_db)):
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj:
        proj.paint_notes = data.paint_notes
        proj.updated_at = datetime.now()
        db.commit()
        return {"status": "ok"}
    return {"status": "error"}

def _step_match_key(name: str) -> str:
    """Название этапа для сравнения: без номера в начале («2. », «3) »), пробелов по краям и регистра.
    Так задачи, созданные старыми шаблонами с номерами, тоже узнаются."""
    return re.sub(r"^\s*\d+\s*[.)]\s*", "", name or "").strip().casefold()


def existing_template_steps(db: Session, project_id: str, templates: list) -> Dict[str, List[int]]:
    """Номера этапов каждого шаблона, которые уже есть среди задач проекта (на любом языке интерфейса)."""
    have = {_step_match_key(name) for (name,) in db.query(Task.task_name).filter(Task.project_id == project_id)}
    return {t["id"]: [i for i, raw in enumerate(t["steps"])
                      if any(_step_match_key(v) in have for v in pipeline.step_variants(raw))]
            for t in templates}


class TemplatesUpdate(BaseModel):
    templates: Optional[List[dict]] = None
    reset: bool = False


@app.get("/api/pipeline-templates")
async def get_pipeline_templates(project_id: Optional[str] = None, db: Session = Depends(get_db)):
    """Шаблоны на языке интерфейса, встроенные (для «Сбросить») и (для project_id) этапы, которые уже есть в проекте."""
    cfg = load_config()
    raw = pipeline.get_templates(cfg)
    existing = existing_template_steps(db, project_id, raw) if project_id else {t["id"]: [] for t in raw}
    return {"templates": pipeline.for_client(cfg), "defaults": pipeline.defaults_for_client(),
            "existing": existing, "custom": "PIPELINE_TEMPLATES" in cfg}


@app.post("/api/pipeline-templates")
async def save_pipeline_templates(data: TemplatesUpdate):
    cfg = load_config()
    if data.reset:
        cfg.pop("PIPELINE_TEMPLATES", None)
    else:
        err = pipeline.save(cfg, data.templates)
        if err:
            return {"status": "error", "message": err}
    save_config(cfg)
    return {"status": "ok", "templates": pipeline.for_client(cfg)}

@app.post("/api/projects/{project_id}/template")
async def apply_project_template(project_id: str, data: Optional[TemplateRequest] = None, db: Session = Depends(get_db)):
    proj = db.query(Project).filter(Project.id == project_id).first()
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
            try:
                dead = datetime.strptime(step.deadline, "%Y-%m-%d") if step.deadline else None
            except ValueError:
                return {"status": "error", "message": tr("err.bad_template_date")}
            plan.append((step.index, dead))
        if not plan:
            return {"status": "error", "message": tr("template.none_selected")}

    # Повторное применение шаблона не плодит дубли: этапы, которые уже есть в проекте, пропускаются
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
            status="Не начато",
            postponed=0
        ))
    proj.updated_at = datetime.now()
    db.commit()
    return {"status": "ok", "added": len(plan), "skipped": skipped}

def purge_project(db: Session, proj: Project) -> None:
    """Стирает проект насовсем: задачи, фото, референсы, фото прогресса. Баланс фиксируется корректировкой."""
    config = load_config()
    if config.get("DELETE_KEEPS_BALANCE", True):
        # Вклад проекта в баланс: награда (если завершён) минус стоимость старта.
        # Фиксируем его корректировкой, чтобы удаление не меняло баланс.
        reward = (proj.token_reward or 0) if is_done(proj.status) else 0
        contribution = reward - (proj.token or 0)
        if contribution:
            db.add(Adjustment(amount=contribution, reason=f"Удалён проект «{proj.name}»"))
    photo_name = getattr(proj, "photo", "")
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
    """Стирает проекты, пролежавшие в корзине дольше TRASH_DAYS. Возвращает, сколько стёрто."""
    cutoff = datetime.now() - timedelta(days=load_config()["TRASH_DAYS"])
    db = SessionLocal()
    try:
        old = db.query(Project).filter(Project.deleted_at.isnot(None), Project.deleted_at < cutoff).all()
        for proj in old:
            purge_project(db, proj)
        return len(old)
    finally:
        db.close()


def serialize_trash(db: Session) -> list:
    days = load_config()["TRASH_DAYS"]
    now = datetime.now()
    out = []
    for p in db.query(Project).filter(Project.deleted_at.isnot(None)).order_by(Project.deleted_at.desc()).all():
        left = days - (now - p.deleted_at).days
        out.append({
            "ID": p.id, "Name": p.name, "Status": p.status,
            "DeletedAt": p.deleted_at.strftime("%Y-%m-%d"), "DaysLeft": max(0, left),
            "Tasks": db.query(Task).filter(Task.project_id == p.id).count(),
        })
    return out


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str, db: Session = Depends(get_db)):
    """Удаление = перенос в корзину. Насовсем проект стирается через TRASH_DAYS дней или вручную."""
    proj = db.query(Project).filter(Project.id == project_id, Project.deleted_at.is_(None)).first()
    if proj:
        proj.deleted_at = datetime.now()
        db.commit()
    return {"status": "ok"}

@app.post("/api/projects/{project_id}/restore")
async def restore_project(project_id: str, db: Session = Depends(get_db)):
    proj = db.query(Project).filter(Project.id == project_id, Project.deleted_at.isnot(None)).first()
    if not proj:
        return {"status": "error", "message": tr("trash.not_found")}
    proj.deleted_at = None
    proj.updated_at = datetime.now()
    db.commit()
    return {"status": "ok"}

@app.delete("/api/trash/{project_id}")
async def purge_trashed_project(project_id: str, db: Session = Depends(get_db)):
    proj = db.query(Project).filter(Project.id == project_id, Project.deleted_at.isnot(None)).first()
    if not proj:
        return {"status": "error", "message": tr("trash.not_found")}
    purge_project(db, proj)
    return {"status": "ok"}

@app.delete("/api/trash")
async def empty_trash(db: Session = Depends(get_db)):
    for proj in db.query(Project).filter(Project.deleted_at.isnot(None)).all():
        purge_project(db, proj)
    return {"status": "ok"}

@app.post("/api/tasks")
async def create_task(data: TaskCreate, db: Session = Depends(get_db)):
    target = db.query(Project).filter(Project.id == data.project_id).first()
    if target is not None and target.deleted_at is not None:
        return {"status": "error", "message": tr("err.project_not_found")}
    dead_date = datetime.strptime(data.deadline, "%Y-%m-%d") if data.deadline else None
    new_task = Task(project_id=data.project_id, task_name=data.task_name, deadline=dead_date, status="Не начато", postponed=0)
    db.add(new_task)
    proj = db.query(Project).filter(Project.id == data.project_id).first()
    if proj:
        proj.updated_at = datetime.now()
    db.commit()
    return {"status": "ok", "id": new_task.id}

@app.post("/api/tasks/{task_id}/edit")
async def edit_task(task_id: str, data: TaskEdit, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if task:
        task.task_name = data.task_name
        task.deadline = datetime.strptime(data.deadline, "%Y-%m-%d") if data.deadline else None
        db.commit()
        return {"status": "ok"}
    return {"status": "error"}

@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str, db: Session = Depends(get_db)):
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
