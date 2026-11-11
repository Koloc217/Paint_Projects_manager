"""/api/data: everything the page shows."""
import hashlib
import json
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from hub.core import activity, progress, vacation
from hub.core.appconfig import DEFAULT_NOTIFY_SETTINGS, DEFAULT_SCHEDULE, load_config
from hub.core.constants import NO_STAGE
from hub.core.convert import iso_date, to_tokens
from hub.core.queries import compute_balance, live_projects, live_tasks
from hub.core.database import Reference
from hub.core.i18n import available_languages, get_lang
from hub.core.photofiles import PHOTOS_DIR, REFS_DIR, list_dir_names, photo_thumb_url, photo_url
from hub.core.trash import serialize_trash
from hub.web.deps import get_db

router = APIRouter()


@router.get("/api/data")
def get_data(request: Request, db: Session = Depends(get_db)):
    """Everything the page shows. The answer carries an ETag (a hash of its content): the page sends it back
    in If-None-Match and gets an empty 304 when nothing changed, so it neither downloads nor redraws anything."""
    body = json.dumps(collect_data(db), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      default=str).encode("utf-8")
    etag = '"' + hashlib.sha1(body).hexdigest()[:20] + '"'
    headers = {"ETag": etag, "Cache-Control": "no-store"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


def collect_data(db: Session) -> dict:
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
        "Stage": p.stage or NO_STAGE,
        "Priority": p.priority,
        "Estimate": iso_date(p.estimate),
        "Token Reward": to_tokens(p.token_reward),
        "PaintNotes": p.paint_notes or "",
        "Photo": photo_url(p.photo, covers),
        "PhotoThumb": photo_thumb_url(p.photo, covers),
        "ExtraSpent": int(p.extra_spent_min or 0),
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
        "CompletedTs": t.completed_at.isoformat(timespec="seconds") if t.completed_at else None,  # sort key: newest closed first
        "Spent": t.spent_min or None,
        "SpentStage": t.spent_stage or "",
    } for t in tasks]

    return {
        "projects": proj_list,
        "tasks": task_list,
        "total_tokens": current_tokens,
        "earned_tokens": earned,
        # shame metric: postponements over the last 30 days (from the journal, deleted tasks included)
        "postponed_30d": activity.count_postponements(db, datetime.now() - timedelta(days=30)),
        "dev_mode": config.get("DEV_MODE", False),
        "project_cost": to_tokens(config.get("PROJECT_COST", 10), 10),
        "delete_keeps_balance": config.get("DELETE_KEEPS_BALANCE", True),
        "calendar_export": bool(config.get("CALENDAR_EXPORT")),
        "tg_cleanup": config.get("TG_CLEANUP", "keep_46h"),
        "ask_spent_time": bool(config.get("ASK_SPENT_TIME", True)),
        "notify_settings": config.get("NOTIFY", DEFAULT_NOTIFY_SETTINGS),
        "schedule_settings": config.get("SCHEDULE", DEFAULT_SCHEDULE),
        "language": get_lang(),
        "languages": available_languages(),
        "date_format": config.get("DATE_FORMAT", "dmy"),
        "trash": serialize_trash(db, config),
        "trash_days": config.get("TRASH_DAYS", 30),
        "vacation": vacation.state(config),
    }
