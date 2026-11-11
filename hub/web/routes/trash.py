"""Deleting a project moves it to the trash; from there it is restored or erased (see core/trash.py)."""
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from hub.core.appconfig import load_config
from hub.core.queries import live_project
from hub.core.database import Project
from hub.core.i18n import tr
from hub.core.trash import purge_project
from hub.web.deps import get_db
from hub.web.responses import fail

router = APIRouter()


@router.delete("/api/projects/{project_id}")
def delete_project(project_id: str, db: Session = Depends(get_db)):
    """Deleting = moving to the trash. The project is erased for good after TRASH_DAYS days or by hand."""
    proj = live_project(db, project_id)
    if not proj:
        return fail(tr("err.project_not_found"))
    proj.deleted_at = datetime.now()
    db.commit()
    return {"status": "ok"}

@router.post("/api/projects/{project_id}/restore")
def restore_project(project_id: str, db: Session = Depends(get_db)):
    proj = db.query(Project).filter(Project.id == project_id, Project.deleted_at.isnot(None)).first()
    if not proj:
        return fail(tr("trash.not_found"))
    proj.deleted_at = None
    proj.updated_at = datetime.now()
    db.commit()
    return {"status": "ok"}

@router.delete("/api/trash/{project_id}")
def purge_trashed_project(project_id: str, db: Session = Depends(get_db)):
    proj = db.query(Project).filter(Project.id == project_id, Project.deleted_at.isnot(None)).first()
    if not proj:
        return fail(tr("trash.not_found"))
    purge_project(db, proj)
    return {"status": "ok"}

@router.delete("/api/trash")
def empty_trash(db: Session = Depends(get_db)):
    config = load_config()
    for proj in db.query(Project).filter(Project.deleted_at.isnot(None)).all():
        purge_project(db, proj, config)
    return {"status": "ok"}
