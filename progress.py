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
    proj = db.query(Project).filter(Project.id == project_id).first()
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


def exists(row: ProgressPhoto) -> bool:
    return os.path.exists(os.path.join(PROGRESS_DIR, os.path.basename(row.filename)))


def url(name: str) -> str:
    return f"/photos/progress/{os.path.basename(name)}"


def thumb_url(row: ProgressPhoto) -> str:
    if row.thumb and os.path.exists(os.path.join(PROGRESS_DIR, os.path.basename(row.thumb))):
        return url(row.thumb)
    return url(row.filename)


def serialize(row: ProgressPhoto) -> dict:
    return {
        "ID": row.id, "ProjectID": row.project_id, "Url": url(row.filename), "Thumb": thumb_url(row),
        "Stage": row.stage or "—", "Note": row.note or "", "Source": row.source or "web",
        "At": row.created_at.strftime("%Y-%m-%d %H:%M") if row.created_at else "",
    }


def list_photos(db: Session, project_id: str = None, limit: int = None) -> list:
    """Photos whose files still exist, newest first."""
    q = db.query(ProgressPhoto)
    if project_id:
        q = q.filter(ProgressPhoto.project_id == project_id)
    q = q.order_by(ProgressPhoto.created_at.desc(), ProgressPhoto.id.desc())
    out = []
    for row in q:
        if exists(row):
            out.append(row)
            if limit and len(out) >= limit:
                break
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
