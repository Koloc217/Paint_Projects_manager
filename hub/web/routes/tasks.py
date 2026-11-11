"""Tasks: create, edit, delete."""
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from hub.core import spent
from hub.core.common import MAX_TASK_NAME_LEN, TASK_NOT_STARTED, is_done, live_project
from hub.core.database import Task
from hub.core.i18n import tr
from hub.web.deps import get_db
from hub.web.responses import fail
from hub.web.schemas import TaskCreate, TaskEdit
from hub.web.validation import parse_date_field, validate_name

router = APIRouter()


@router.post("/api/tasks")
def create_task(data: TaskCreate, db: Session = Depends(get_db)):
    name, err = validate_name(data.task_name, MAX_TASK_NAME_LEN, "task.err_empty")
    if err:
        return fail(err)
    proj = None
    if data.project_id:                    # no project: a general task (maintenance, tidying...)
        proj = live_project(db, data.project_id)
        if proj is None:
            return fail(tr("err.project_not_found"))
    dead_date, err = parse_date_field(data.deadline)
    if err:
        return fail(err)
    new_task = Task(project_id=proj.id if proj else None, task_name=name, deadline=dead_date,
                    status=TASK_NOT_STARTED, postponed=0)
    db.add(new_task)
    if proj:
        proj.updated_at = datetime.now()
    db.commit()
    return {"status": "ok", "id": new_task.id}

@router.post("/api/tasks/{task_id}/edit")
def edit_task(task_id: str, data: TaskEdit, db: Session = Depends(get_db)):
    name, err = validate_name(data.task_name, MAX_TASK_NAME_LEN, "task.err_empty")
    if err:
        return fail(err)
    deadline, err = parse_date_field(data.deadline)
    if err:
        return fail(err)
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        return fail(tr("bot.task_not_found"))
    if data.spent is not None:
        try:
            minutes = spent.parse_spent(data.spent)
        except spent.SpentError:
            return fail(tr("time.err_parse"))
        spent.set_task_spent(db, task, minutes)
    task.task_name = name
    # moving the deadline of an open task later counts as a postponement (shame metric), as the bot's "+2 days"
    if task.deadline and deadline and deadline.date() > task.deadline.date() and not is_done(task.status):
        task.postponed = int(task.postponed or 0) + 1
    task.deadline = deadline
    db.commit()
    return {"status": "ok"}

@router.delete("/api/tasks/{task_id}")
def delete_task(task_id: str, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        return fail(tr("bot.task_not_found"))
    db.delete(task)
    db.commit()
    return {"status": "ok"}
