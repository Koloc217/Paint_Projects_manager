#!/usr/bin/env python3
"""Makes the missing small previews once, for photos uploaded before previews existed (optional).

    pip install pillow            # only needed for this script, the app itself does not use Pillow
    python tools/make_thumbs.py

New uploads from the web app get their preview from the browser. Without a preview the app simply shows the photo
itself, so this script only saves traffic on a phone for older photos:
  • covers: photos/<cover>_t.jpg (used by the phone layout only);
  • progress photos sent from Telegram before the bot kept previews: photos/progress/<name>_t.jpg (the database
    row gets the name). Newer ones come with a preview: the bot saves a small size Telegram already made.

Files are written next to the originals; originals are never changed. Can be run again at any time: photos that
already have a preview are skipped. Pillow cannot read some formats (AVIF without a plugin): those are skipped.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

MAX_SIDE = 320


def make_thumb(src: str, dst: str) -> bool:
    from PIL import Image, ImageOps
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            if im.mode not in ("RGB", "L"):
                bg = Image.new("RGB", im.size, (15, 23, 42))         # the same dark fill as the browser uses
                bg.paste(im, mask=im.convert("RGBA").split()[-1])
                im = bg
            tmp = dst + ".part"
            im.save(tmp, "JPEG", quality=80)
            os.replace(tmp, dst)
        return True
    except Exception as e:
        print(f"   skipped {os.path.basename(src)}: {e}")
        return False


def main() -> int:
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("Pillow is not installed: pip install pillow")
        return 1
    from database import SessionLocal, Project, ProgressPhoto, ensure_schema
    import integrity
    import progress

    ensure_schema()
    db = SessionLocal()
    made = 0
    try:
        for (photo,) in db.query(Project.photo).filter(Project.photo.isnot(None), Project.photo != ""):
            src = os.path.join(integrity.PHOTOS_DIR, os.path.basename(photo))
            dst = os.path.join(integrity.PHOTOS_DIR, integrity.cover_thumb_name(photo))
            if os.path.exists(src) and not os.path.exists(dst) and make_thumb(src, dst):
                made += 1
        for row in db.query(ProgressPhoto).filter((ProgressPhoto.thumb.is_(None)) | (ProgressPhoto.thumb == "")):
            src = os.path.join(progress.PROGRESS_DIR, os.path.basename(row.filename))
            name = os.path.splitext(os.path.basename(row.filename))[0] + "_t.jpg"
            dst = os.path.join(progress.PROGRESS_DIR, name)
            if os.path.exists(src) and (os.path.exists(dst) or make_thumb(src, dst)):
                row.thumb = name
                db.commit()
                made += 1
    finally:
        db.close()
    print(f"Previews made: {made}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
