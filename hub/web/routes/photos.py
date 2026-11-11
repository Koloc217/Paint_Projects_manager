"""Pictures of a project: the cover, paint references and progress photos."""
import base64
import re
import time
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from hub.core import imagestore, progress
from hub.core.common import detect_image_ext, live_project
from hub.core.database import ProgressPhoto, Reference
from hub.core.i18n import tr
from hub.core.photofiles import PHOTOS_DIR, REFS_DIR, remove_photo_file, remove_ref_files
from hub.web.deps import get_db
from hub.web.responses import fail
from hub.web.schemas import PhotoUpload, ProgressNote, ProgressUpload, RefOrder, RefUpload

MAX_PHOTO_BYTES = 6 * 1024 * 1024
MAX_REFS = 20
MAX_REF_BYTES = 25 * 1024 * 1024
MAX_THUMB_BYTES = 1536 * 1024

router = APIRouter()


@router.post("/api/projects/{project_id}/photo")
def set_project_photo(project_id: str, data: PhotoUpload, db: Session = Depends(get_db)):
    proj = live_project(db, project_id)
    if not proj:
        return fail(tr("err.project_not_found"))
    m = re.match(r"^data:image/jpeg;base64,(.+)$", data.image or "", re.S)
    if not m:
        return fail(tr("err.expect_jpeg"))
    try:
        raw = base64.b64decode(m.group(1), validate=True)
    except Exception:
        return fail(tr("err.image_unreadable"))
    if len(raw) > MAX_PHOTO_BYTES:
        return fail(tr("err.photo_too_big"))
    if not raw.startswith(b"\xff\xd8\xff"):
        return fail(tr("err.not_jpeg"))

    # a random suffix: two uploads within one second must not overwrite each other's file
    filename = f"{proj.id}_{int(time.time())}_{uuid.uuid4().hex[:4]}.jpg"
    imagestore.save_with_thumb(PHOTOS_DIR, filename, raw, imagestore.thumb_from_data_url(data.thumb, MAX_THUMB_BYTES), MAX_THUMB_BYTES)
    old = proj.photo
    proj.photo = filename
    db.commit()
    remove_photo_file(old)
    return {"status": "ok", "photo": f"/photos/{filename}"}

@router.delete("/api/projects/{project_id}/photo")
def delete_project_photo(project_id: str, db: Session = Depends(get_db)):
    proj = live_project(db, project_id)
    if not proj:
        return fail(tr("err.project_not_found"))
    old = proj.photo
    proj.photo = ""
    db.commit()
    remove_photo_file(old)
    return {"status": "ok"}

@router.post("/api/projects/{project_id}/refs")
def add_project_ref(project_id: str, data: RefUpload, db: Session = Depends(get_db)):
    proj = live_project(db, project_id)
    if not proj:
        return fail(tr("err.project_not_found"))
    if db.query(Reference).filter(Reference.project_id == project_id).count() >= MAX_REFS:
        return fail(tr("ref.limit", n=MAX_REFS))
    try:
        raw = imagestore.decode_data_url(data.image)
    except ValueError as e:
        return fail(str(e))
    if len(raw) > MAX_REF_BYTES:
        return fail(tr("err.file_too_big", n=MAX_REF_BYTES // 1024 // 1024))
    ext = detect_image_ext(raw)
    if not ext:
        return fail(tr("err.supported_formats"))

    filename = f"{imagestore.random_base(proj.id)}.{ext}"
    thumb_name = imagestore.save_with_thumb(REFS_DIR, filename, raw,        # the original is stored byte for byte
                                            imagestore.thumb_from_data_url(data.thumb, MAX_THUMB_BYTES), MAX_THUMB_BYTES)

    last = db.query(func.max(Reference.position)).filter(Reference.project_id == project_id).scalar()
    ref = Reference(project_id=project_id, filename=filename, thumb=thumb_name, position=(last or 0) + 1)
    db.add(ref)
    db.commit()
    return {"status": "ok", "id": ref.id}

@router.post("/api/projects/{project_id}/refs/order")
def reorder_project_refs(project_id: str, data: RefOrder, db: Session = Depends(get_db)):
    """New order of the references: the full list of their ids, first to last."""
    refs = {r.id: r for r in db.query(Reference).filter(Reference.project_id == project_id).all()}
    if sorted(data.ids) != sorted(refs):
        return fail(tr("ref.err_order"))
    for pos, ref_id in enumerate(data.ids, start=1):
        refs[ref_id].position = pos
    db.commit()
    return {"status": "ok"}

@router.delete("/api/projects/{project_id}/refs/{ref_id}")
def delete_project_ref(project_id: str, ref_id: int, db: Session = Depends(get_db)):
    ref = db.query(Reference).filter(Reference.id == ref_id, Reference.project_id == project_id).first()
    if not ref:
        return fail(tr("err.ref_not_found"))
    names = (ref.filename, ref.thumb)
    db.delete(ref)
    db.commit()
    remove_ref_files(*names)
    return {"status": "ok"}

@router.post("/api/projects/{project_id}/progress")
def add_progress_photo(project_id: str, data: ProgressUpload, db: Session = Depends(get_db)):
    try:
        raw = imagestore.decode_data_url(data.image)
        thumb = imagestore.decode_data_url(data.thumb) if data.thumb else None
        row = progress.add_photo(db, project_id, raw, thumb, data.note, "web")
    except progress.ProgressError as e:
        return fail(tr(e.key, **e.params))
    except ValueError as e:
        return fail(str(e))
    return {"status": "ok", "photo": progress.serialize(row)}

@router.patch("/api/projects/{project_id}/progress/{photo_id}")
def edit_progress_note(project_id: str, photo_id: int, data: ProgressNote, db: Session = Depends(get_db)):
    row = db.query(ProgressPhoto).filter(ProgressPhoto.id == photo_id, ProgressPhoto.project_id == project_id).first()
    if not row:
        return fail(tr("progress.not_found"))
    row.note = (data.note or "").strip()[:progress.MAX_NOTE_LEN]
    db.commit()
    return {"status": "ok"}

@router.delete("/api/projects/{project_id}/progress/{photo_id}")
def delete_progress_photo(project_id: str, photo_id: int, db: Session = Depends(get_db)):
    if not progress.delete_photo(db, photo_id, project_id):
        return fail(tr("progress.not_found"))
    return {"status": "ok"}
