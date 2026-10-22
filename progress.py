"""Progress (WIP) photos: storing, listing and removing. Shared by the web API and the Telegram bot.

Files live in photos/progress/. The database keeps the project, the stage at upload time, a note and the source.
"""
import os
import uuid

from sqlalchemy.orm import Session

from common import BASE_DIR, detect_image_ext
from database import Project, ProgressPhoto

PHOTOS_DIR = os.path.join(BASE_DIR, "photos")
PROGRESS_DIR = os.path.join(PHOTOS_DIR, "progress")
os.makedirs(PROGRESS_DIR, exist_ok=True)

MAX_PROGRESS_BYTES = 12 * 1024 * 1024   # the browser resizes to ~1600 px, Telegram already compresses
MAX_THUMB_BYTES = 512 * 1024
MAX_NOTE_LEN = 300


class ProgressError(ValueError):
    """Message key (for tr) and its parameters."""
    def __init__(self, key: str, **params):
        super().__init__(key)
        self.key = key
        self.params = params


def _write(name: str, raw: bytes) -> None:
    with open(os.path.join(PROGRESS_DIR, name), "wb") as f:
        f.write(raw)


def remove_files(*names) -> None:
    for name in names:
        if name:
            try:
                os.remove(os.path.join(PROGRESS_DIR, os.path.basename(name)))
            except OSError:
                pass


def add_photo(db: Session, project_id: str, raw: bytes, thumb_raw: bytes = None,
              note: str = "", source: str = "web") -> ProgressPhoto:
    """Saves one progress photo. The stage is taken from the project at this moment."""
    proj = db.query(Project).filter(Project.id == project_id, Project.deleted_at.is_(None)).first()
    if not proj:
        raise ProgressError("err.project_not_found")
    if len(raw) > MAX_PROGRESS_BYTES:
        raise ProgressError("err.file_too_big", n=MAX_PROGRESS_BYTES // 1024 // 1024)
    ext = detect_image_ext(raw)
    if not ext:
        raise ProgressError("err.supported_formats")
    base = f"{proj.id}_{uuid.uuid4().hex[:8]}"
    filename = f"{base}.{ext}"
    _write(filename, raw)
    thumb_name = ""
    if thumb_raw and thumb_raw.startswith(b"\xff\xd8\xff") and len(thumb_raw) <= MAX_THUMB_BYTES:
        thumb_name = f"{base}_t.jpg"
        _write(thumb_name, thumb_raw)
    row = ProgressPhoto(
        project_id=project_id, filename=filename, thumb=thumb_name,
        stage=getattr(proj, "stage", None) or "—",
        note=(note or "").strip()[:MAX_NOTE_LEN], source=source if source in ("web", "telegram") else "web",
    )
    db.add(row)
    db.commit()
    return row


def _present_files() -> set:
    """Names of the files in photos/progress/: one directory read instead of a stat call per photo."""
    try:
        return set(os.listdir(PROGRESS_DIR))
    except OSError:
        return set()


def exists(row: ProgressPhoto, present: set = None) -> bool:
    name = os.path.basename(row.filename)
    return name in present if present is not None else os.path.exists(os.path.join(PROGRESS_DIR, name))


def url(name: str) -> str:
    return f"/photos/progress/{os.path.basename(name)}"


def thumb_url(row: ProgressPhoto, present: set = None) -> str:
    if row.thumb:
        name = os.path.basename(row.thumb)
        if (name in present) if present is not None else os.path.exists(os.path.join(PROGRESS_DIR, name)):
            return url(row.thumb)
    return url(row.filename)


def serialize(row: ProgressPhoto, present: set = None) -> dict:
    return {
        "ID": row.id, "ProjectID": row.project_id, "Url": url(row.filename), "Thumb": thumb_url(row, present),
        "Stage": row.stage or "—", "Note": row.note or "", "Source": row.source or "web",
        "At": row.created_at.strftime("%Y-%m-%d %H:%M") if row.created_at else "",
    }


def _query(db: Session, project_id: str = None):
    q = db.query(ProgressPhoto).filter(
        ProgressPhoto.project_id.in_(db.query(Project.id).filter(Project.deleted_at.is_(None))))
    if project_id:
        q = q.filter(ProgressPhoto.project_id == project_id)
    return q.order_by(ProgressPhoto.created_at.desc(), ProgressPhoto.id.desc())


def list_photos(db: Session, project_id: str = None, limit: int = None, present: set = None) -> list:
    """Photos whose files still exist, newest first."""
    present = present if present is not None else _present_files()
    out = []
    for row in _query(db, project_id):
        if exists(row, present):
            out.append(row)
            if limit and len(out) >= limit:
                break
    return out


def count_photos(db: Session, project_id: str) -> int:
    return len(list_photos(db, project_id))


def serialize_all(db: Session) -> dict:
    """{project_id: [photo, ...]} from oldest to newest (the order of the timeline in the project card)."""
    present = _present_files()
    out = {}
    for row in reversed(list_photos(db, present=present)):
        out.setdefault(row.project_id, []).append(serialize(row, present))
    return out


def delete_photo(db: Session, photo_id: int, project_id: str = None) -> bool:
    q = db.query(ProgressPhoto).filter(ProgressPhoto.id == photo_id)
    if project_id:
        q = q.filter(ProgressPhoto.project_id == project_id)
    row = q.first()
    if not row:
        return False
    names = (row.filename, row.thumb)
    db.delete(row)
    db.commit()
    remove_files(*names)
    return True


def delete_project_photos(db: Session, project_id: str) -> list:
    """Removes the rows (the caller commits) and returns file names to delete after the commit."""
    names = []
    for row in db.query(ProgressPhoto).filter(ProgressPhoto.project_id == project_id).all():
        names += [row.filename, row.thumb]
        db.delete(row)
    return names
