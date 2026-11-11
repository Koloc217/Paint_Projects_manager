"""Projects: statuses, creating, editing, notes and time outside tasks."""
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from hub.core import spent
from hub.core.common import (PRIORITIES, PROJECT_STATUSES, STAGES, TASK_STATUSES, compute_balance, is_done, live_project,
                             load_config, normalize_value, to_tokens)
from hub.core.database import Project, Task
from hub.core.i18n import tr
from hub.core.lifecycle import set_project_status, set_stage, set_task_status, touch_project
from hub.web.deps import get_db
from hub.web.responses import fail
from hub.web.schemas import (ActionPayload, PaintNotesUpdate, ProjectCreate, ProjectEdit, ProjectRename, ProjectSpent)
from hub.web.validation import parse_date_field, parse_token_field, validate_name

MAX_NOTES_LEN = 20000

router = APIRouter()


@router.post("/api/action")
def do_action(payload: ActionPayload, db: Session = Depends(get_db)):
    now = datetime.now()
    payload.status = normalize_value(payload.status)     # an old client may still send Russian values
    payload.stage = normalize_value(payload.stage)
    if payload.action == "update_project_status":
        if payload.status not in PROJECT_STATUSES:
            return fail(tr("err.bad_value"))
        proj = live_project(db, payload.id)
        if not proj:
            return fail(tr("err.project_not_found"))
        set_project_status(proj, payload.status, db, now)
        db.commit()
        return {"status": "ok"}

    if payload.action == "update_project_stage":
        if payload.stage not in STAGES:
            return fail(tr("err.bad_value"))
        proj = live_project(db, payload.id)
        if not proj:
            return fail(tr("err.project_not_found"))
        set_stage(proj, payload.stage, now)
        db.commit()
        return {"status": "ok"}

    if payload.action == "update_task_status":
        if payload.status not in TASK_STATUSES:
            return fail(tr("err.bad_value"))
        task = db.query(Task).filter(Task.id == payload.id).first()
        if not task:
            return fail(tr("bot.task_not_found"))
        set_task_status(task, payload.status, now)
        touch_project(db, task.project_id, now)
        db.commit()
        return {"status": "ok"}
    return {"status": "unknown_action"}

@router.post("/api/projects")
def create_project(data: ProjectCreate, db: Session = Depends(get_db)):
    name, err = validate_name(data.name)
    if err:
        return fail(err)
    data.status = normalize_value(data.status)
    if data.priority not in PRIORITIES or data.status not in PROJECT_STATUSES:
        return fail(tr("err.bad_value"))
    reward, err = parse_token_field(data.token_reward)
    if err:
        return fail(err)
    est_date, err = parse_date_field(data.estimate)
    if err:
        return fail(err)

    config = load_config()
    current_tokens, _ = compute_balance(db, config)
    project_cost = to_tokens(config.get("PROJECT_COST", 10), 10)
    dev_mode = config.get("DEV_MODE", False)

    if not dev_mode and current_tokens < project_cost:
        return fail(tr("err.not_enough_tokens", have=current_tokens, cost=project_cost))

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

@router.post("/api/projects/{project_id}/edit")
def edit_project(project_id: str, data: ProjectEdit, db: Session = Depends(get_db)):
    if not load_config().get("DEV_MODE", False):
        return fail(tr("err.editing_disabled"))
    name, err = validate_name(data.name)
    if err:
        return fail(err)
    if data.priority not in PRIORITIES:
        return fail(tr("err.bad_value"))
    estimate, err = parse_date_field(data.estimate)
    if err:
        return fail(err)
    proj = live_project(db, project_id)
    if not proj:
        return fail(tr("err.project_not_found"))
    proj.name = name
    proj.priority = data.priority
    proj.estimate = estimate
    proj.updated_at = datetime.now()
    db.commit()
    return {"status": "ok"}

@router.post("/api/projects/{project_id}/rename")
def rename_project(project_id: str, data: ProjectRename, db: Session = Depends(get_db)):
    """Name and priority: always allowed, regardless of DEV_MODE (the deadline needs DEV_MODE, see /edit)."""
    name, err = validate_name(data.name)
    if err:
        return fail(err)
    if data.priority is not None and data.priority not in PRIORITIES:
        return fail(tr("err.bad_value"))
    proj = live_project(db, project_id)
    if not proj:
        return fail(tr("err.project_not_found"))
    # updated_at is left alone: renaming or reprioritizing is not progress on the project
    # (otherwise it would reset the stagnation detector).
    proj.name = name
    if data.priority is not None:
        proj.priority = data.priority
    db.commit()
    return {"status": "ok", "name": name}

@router.post("/api/projects/{project_id}/notes")
def update_paint_notes(project_id: str, data: PaintNotesUpdate, db: Session = Depends(get_db)):
    proj = live_project(db, project_id)
    if not proj:
        return fail(tr("err.project_not_found"))
    notes = (data.paint_notes or "")[:MAX_NOTES_LEN]
    if notes != (proj.paint_notes or ""):          # saving unchanged notes (on blur) is not progress
        proj.paint_notes = notes
        proj.updated_at = datetime.now()
        db.commit()
    return {"status": "ok"}

@router.post("/api/projects/{project_id}/spent")
def set_project_spent(project_id: str, data: ProjectSpent, db: Session = Depends(get_db)):
    """Time spent on the project outside its tasks (choosing paints, research...)."""
    proj = live_project(db, project_id)
    if not proj:
        return fail(tr("err.project_not_found"))
    try:
        minutes = spent.parse_spent(data.extra)
    except spent.SpentError:
        return fail(tr("time.err_parse"))
    proj.extra_spent_min = minutes or 0
    db.commit()
    return {"status": "ok", "extra": proj.extra_spent_min}
