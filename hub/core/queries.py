"""Queries every part of the app shares: what is not in the trash, and the token balance."""
from sqlalchemy import or_

from hub.core.appconfig import load_config
from hub.core.constants import is_done
from hub.core.convert import to_tokens
from hub.core.database import Adjustment, Project, Task


def live_projects(db):
    """Query for projects outside the trash: used everywhere projects are shown or counted."""
    return db.query(Project).filter(Project.deleted_at.is_(None))


def live_project(db, project_id: str):
    """One project outside the trash, or None. Buttons of old bot messages stay clickable after a project is deleted:
    they must not change a project in the trash."""
    return live_projects(db).filter(Project.id == project_id).first()


def live_task(db, task_id: str):
    """One task (a general task or a task whose project is outside the trash), or None."""
    return live_tasks(db).filter(Task.id == task_id).first()


def live_tasks(db):
    """Query for tasks of projects outside the trash, plus general tasks (no project)."""
    return db.query(Task).filter(or_(Task.project_id.is_(None),
                                     Task.project_id.in_(db.query(Project.id).filter(Project.deleted_at.is_(None)))))


def compute_balance(db, config=None):
    """Returns (balance, earned).

    balance = initial tokens + rewards of finished projects
              - start cost of all projects + adjustments (deleted projects).
    Only the three needed columns are read, without building ORM objects. Tokens are whole numbers.
    """
    if config is None:
        config = load_config()
    q = db.query(Project.status, Project.token, Project.token_reward)
    if not config.get("DELETE_KEEPS_BALANCE", True):
        q = q.filter(Project.deleted_at.is_(None))
    # with DELETE_KEEPS_BALANCE a project in the trash may still come back, so its contribution is kept for now
    earned = spent = 0
    for status, token, reward in q:
        spent += to_tokens(token)
        if is_done(status):
            earned += to_tokens(reward)
    adjustments = sum(to_tokens(amount) for (amount,) in db.query(Adjustment.amount))
    return to_tokens(config.get("INITIAL_TOKENS", 0)) + earned - spent + adjustments, earned
