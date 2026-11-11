"""The trash: deleted projects wait TRASH_DAYS days, then are erased for good (tasks, photos, references)."""
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from hub.core import progress
from hub.core.common import is_done, iso_date, load_config, to_tokens
from hub.core.database import Adjustment, Project, Reference, SessionLocal, Task
from hub.core.photofiles import remove_photo_file, remove_ref_files


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
    db.flush()          # the rows that belong to the project go first, then the project (foreign keys)
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
