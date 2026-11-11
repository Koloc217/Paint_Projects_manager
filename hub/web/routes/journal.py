"""The activity journal (Feed tab)."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from hub.core import activity
from hub.core.convert import iso_date
from hub.core.queries import live_projects
from hub.core.database import Project
from hub.core.i18n import tr
from hub.web.deps import get_db

router = APIRouter()


@router.get("/api/activity")
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
        # an event of a general task has no project: it is shown under the "General" label
        "project": names.get(ev.project_id) or ev.project_name
                   or (tr("general.label") if ev.task_id and not ev.project_id else ""),
        "open": ev.project_id in live,
        "kind": ev.kind,
        "text": activity.describe(ev),
        "source": ev.source,
    } for ev in rows[:limit]]
    return {"items": items, "more": len(rows) > limit}
