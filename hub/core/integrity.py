"""Data integrity: photo files that no database row points to, rows whose files are missing, rows without a project.

Unreferenced photo files are not deleted right away: they are moved to DATA_DIR/orphans/<date>/<folder>/ and erased
from there after QUARANTINE_DAYS. If something was moved by mistake, it can be put back by hand. The quarantine is
outside photos/, so it is not served by the web app; storage.py shows its size.

Safety rules (so nothing that is still in use disappears):
- only files with the names the app itself gives (PRJ-XXXXXX_...) are considered; anything else in the folders is
  left alone;
- a file younger than MIN_AGE_SEC is never touched: an upload writes the file first and the database row a moment
  later, so a fresh file may simply not have its row yet;
- every row counts, including projects in the trash (they can still be restored);
- a cover's preview (<cover>_t.jpg) belongs to its cover.

A new kind of file stored in photos/ (a collage cache, say) must be added to referenced_files(), otherwise it would
be taken for an orphan once it is older than MIN_AGE_SEC.

Used by the app (every few hours, next to the trash clean-up) and by tools/check_data.py.
"""
import os
import re
import shutil
import time
from datetime import datetime, timedelta

from hub.paths import DATA_DIR
from hub.core.database import Project, Reference, ProgressPhoto

PHOTOS_DIR = os.path.join(DATA_DIR, "photos")
ORPHANS_DIR = os.path.join(DATA_DIR, "orphans")
QUARANTINE_DAYS = 30
MIN_AGE_SEC = 3600

# folder key -> path. Covers lie directly in photos/.
FOLDERS = {
    "covers": PHOTOS_DIR,
    "refs": os.path.join(PHOTOS_DIR, "refs"),
    "progress": os.path.join(PHOTOS_DIR, "progress"),
}
APP_NAME_RE = re.compile(r"^PRJ-[0-9A-Za-z]{4,12}_")


def cover_thumb_name(photo: str) -> str:
    """Preview of a cover: next to it, <name>_t.jpg (empty for no cover)."""
    if not photo:
        return ""
    base = os.path.splitext(os.path.basename(photo))[0]
    return f"{base}_t.jpg"


def referenced_files(db) -> dict:
    """{folder key: set of file names some row points to}. Projects in the trash count."""
    covers, refs, progress = set(), set(), set()
    for (photo,) in db.query(Project.photo):
        if photo:
            covers.add(os.path.basename(photo))
            covers.add(cover_thumb_name(photo))
    for filename, thumb in db.query(Reference.filename, Reference.thumb):
        refs.update(os.path.basename(n) for n in (filename, thumb) if n)
    for filename, thumb in db.query(ProgressPhoto.filename, ProgressPhoto.thumb):
        progress.update(os.path.basename(n) for n in (filename, thumb) if n)
    return {"covers": covers, "refs": refs, "progress": progress}


def _files(folder: str) -> list:
    try:
        with os.scandir(folder) as it:
            return [e for e in it if e.is_file(follow_symlinks=False)]
    except OSError:
        return []


def find_orphan_files(db, now: float = None) -> dict:
    """{folder key: [path, ...]} of app-named files no row points to and older than MIN_AGE_SEC."""
    now = now if now is not None else time.time()
    used = referenced_files(db)
    out = {}
    for key, folder in FOLDERS.items():
        found = []
        for entry in _files(folder):
            if entry.name in used[key] or not APP_NAME_RE.match(entry.name):
                continue
            try:
                if now - entry.stat(follow_symlinks=False).st_mtime < MIN_AGE_SEC:
                    continue
            except OSError:
                continue
            found.append(entry.path)
        if found:
            out[key] = sorted(found)
    return out


def find_missing_files(db) -> dict:
    """{kind: [(project_id, file name), ...]} for rows whose main file is not on disk (thumbs are optional)."""
    def present(folder):
        return {e.name for e in _files(folder)}
    covers, refs, progress = (present(FOLDERS[k]) for k in ("covers", "refs", "progress"))
    out = {"covers": [], "refs": [], "progress": []}
    for pid, photo in db.query(Project.id, Project.photo):
        if photo and os.path.basename(photo) not in covers:
            out["covers"].append((pid, photo))
    for pid, name in db.query(Reference.project_id, Reference.filename):
        if os.path.basename(name) not in refs:
            out["refs"].append((pid, name))
    for pid, name in db.query(ProgressPhoto.project_id, ProgressPhoto.filename):
        if os.path.basename(name) not in progress:
            out["progress"].append((pid, name))
    return {k: v for k, v in out.items() if v}


def foreign_key_problems(db) -> list:
    """Rows pointing to a project that does not exist (should be none once foreign keys are on)."""
    return [tuple(r) for r in db.connection().exec_driver_sql("PRAGMA foreign_key_check").fetchall()]


def quarantine(orphans: dict, when: datetime = None) -> int:
    """Moves the files into ORPHANS_DIR/<YYYYMMDD>/<folder key>/. Returns how many were moved."""
    stamp = (when or datetime.now()).strftime("%Y%m%d")
    moved = 0
    for key, paths in orphans.items():
        target = os.path.join(ORPHANS_DIR, stamp, key)
        for path in paths:
            try:
                os.makedirs(target, exist_ok=True)
                dst = os.path.join(target, os.path.basename(path))
                if os.path.exists(dst):
                    dst += f".{int(time.time())}"
                shutil.move(path, dst)
                moved += 1
            except OSError as e:
                print(f"[INTEGRITY] could not move {path}: {e!r}")
    return moved


def purge_quarantine(days: int = QUARANTINE_DAYS, now: datetime = None) -> int:
    """Erases quarantine day folders older than `days`. Returns how many folders were erased."""
    cutoff = ((now or datetime.now()) - timedelta(days=days)).strftime("%Y%m%d")
    erased = 0
    for entry in _dirs(ORPHANS_DIR):
        if re.fullmatch(r"\d{8}", entry.name) and entry.name < cutoff:
            shutil.rmtree(entry.path, ignore_errors=True)
            erased += 1
    return erased


def _dirs(folder: str) -> list:
    try:
        with os.scandir(folder) as it:
            return [e for e in it if e.is_dir(follow_symlinks=False)]
    except OSError:
        return []


def cleanup_orphan_files(db) -> dict:
    """The periodic job: unreferenced photos go to quarantine, old quarantine is erased."""
    orphans = find_orphan_files(db)
    moved = quarantine(orphans) if orphans else 0
    erased = purge_quarantine()
    return {"moved": moved, "erased_days": erased}
