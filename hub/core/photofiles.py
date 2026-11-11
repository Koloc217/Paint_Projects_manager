"""Where cover photos and paint references live on disk, and how they are named, linked and removed."""
import os

from hub.core import integrity
from hub.paths import DATA_DIR

PHOTOS_DIR = os.path.join(DATA_DIR, "photos")
REFS_DIR = os.path.join(PHOTOS_DIR, "refs")
os.makedirs(REFS_DIR, exist_ok=True)


def remove_photo_file(filename):
    """Removes a cover and its preview."""
    if not filename:
        return
    for name in (os.path.basename(filename), integrity.cover_thumb_name(filename)):
        try:
            os.remove(os.path.join(PHOTOS_DIR, name))
        except OSError:
            pass


def remove_ref_files(*names):
    for name in names:
        if name:
            try:
                os.remove(os.path.join(REFS_DIR, os.path.basename(name)))
            except OSError:
                pass


def list_dir_names(path) -> set:
    """File names in a folder (one directory read instead of a stat call per file)."""
    try:
        return set(os.listdir(path))
    except OSError:
        return set()


def photo_url(filename, existing: set = None):
    """URL of a cover photo, or None if the file is missing on disk."""
    if not filename:
        return None
    name = os.path.basename(filename)
    present = (name in existing) if existing is not None else os.path.exists(os.path.join(PHOTOS_DIR, name))
    return f"/photos/{name}" if present else None


def photo_thumb_url(filename, existing: set = None):
    """URL of the cover preview (made by the browser on upload, or by tools/make_thumbs.py), else of the cover."""
    thumb = integrity.cover_thumb_name(filename)
    if thumb and ((thumb in existing) if existing is not None else os.path.exists(os.path.join(PHOTOS_DIR, thumb))):
        return f"/photos/{thumb}"
    return photo_url(filename, existing)
