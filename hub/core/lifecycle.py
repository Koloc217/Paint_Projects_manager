"""Status changes of projects and tasks: the rules in one place.

The web UI and the Telegram bot both go through these functions, so closing a task, reopening a project or
postponing a deadline cannot behave differently depending on where the button was pressed.
None of them commits: the caller owns the transaction.
"""
from datetime import datetime, timedelta

from hub.core.constants import STATUS_FINISHED, TASK_IN_PROGRESS, TASK_NOT_STARTED, TASK_STATUSES, is_done
from hub.core.convert import to_date
from hub.core.database import Project, Task

POSTPONE_DAYS = 2        # how far the "postpone" button moves a deadline


def touch_project(db, project_id, now: datetime) -> None:
    """Marks progress on a project (resets the stagnation detector). A general task has no project: nothing to do."""
    if not project_id:
        return
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj:
        proj.updated_at = now


def set_project_status(proj: Project, status: str, db, now: datetime) -> None:
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


def set_stage(proj: Project, stage: str, now: datetime) -> None:
    """Moves a project to another stage (the caller has checked the stage is one of STAGES)."""
    proj.stage = stage
    proj.updated_at = now


def set_task_status(task: Task, status: str, now: datetime) -> None:
    was_done = is_done(task.status)
    task.closed_from = ""                  # changed by hand: no longer "closed together with the project"
    task.status = status
    if not is_done(status):
        task.completed_at = None
    elif not was_done or task.completed_at is None:
        task.completed_at = now


def complete_task(task: Task, now: datetime) -> bool:
    """Closes a task. True if it was open; a second press changes nothing (the completion date must not move)."""
    if is_done(task.status):
        return False
    task.status = STATUS_FINISHED
    task.completed_at = now
    task.closed_from = ""
    return True


def start_task(task: Task) -> bool:
    """Puts an open task "in progress". A closed one stays closed (an old button must not reopen it): False."""
    if is_done(task.status):
        return False
    task.status = TASK_IN_PROGRESS
    task.completed_at = None
    return True


def postpone_task(task: Task, now: datetime, days: int = POSTPONE_DAYS):
    """Moves the deadline `days` days on, counting from today if it is already overdue.
    Returns (new deadline as a date, how many times the task has been postponed)."""
    base = to_date(task.deadline) or now.date()
    if base < now.date():
        base = now.date()
    new_date = base + timedelta(days=days)
    task.deadline = datetime.combine(new_date, datetime.min.time())
    task.postponed = int(task.postponed or 0) + 1
    return new_date, task.postponed
