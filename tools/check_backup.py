#!/usr/bin/env python3
"""Checks that a database backup can really be restored. Read-only: the backup and the live data are not changed.

    python tools/check_backup.py                       # the newest copy in backups/
    python tools/check_backup.py path/to/tracker.db    # a specific file

What happens:
  1. the backup is copied into a temp folder (sqlite3 backup API, the original is opened read-only);
  2. SQLite checks the file (PRAGMA integrity_check);
  3. the copy is migrated to the current schema exactly as the app does on start (migrations.py);
  4. the copy is read the way the app reads it: projects, tasks, references, progress photos, the token balance;
  5. the numbers are compared with the live database, and the photo files the backup refers to are looked up
     in photos/ (a database backup does not contain photos: they must still be on disk or copied separately).

Exit code 0: the backup restores. 1: it does not (the reason is printed).

Copies in backups/ are made by tools/deploy.sh before every update and by the app before a schema migration.
The full archive (database, settings, photos) is checked with `python tools/archive_tool.py check <zip>`.

How to restore a copy that passed the check: stop the app, put the file in place of tracker.db (and delete
tracker.db-wal and tracker.db-shm next to it if they exist), start the app.
"""
import argparse
import glob
import os
import shutil
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def newest_backup(data_dir: str):
    files = glob.glob(os.path.join(data_dir, "backups", "tracker-*.db"))
    return max(files, key=os.path.getmtime) if files else None


def copy_db(src_path: str, dst_path: str) -> None:
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    dst = sqlite3.connect(dst_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def read_numbers(db_path: str, photos_dir: str) -> dict:
    """Opens the database with the app's models and counts what the app would show."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from hub.core import database
    from hub.core.appconfig import load_config
    from hub.core.queries import compute_balance

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        db = sessionmaker(bind=engine)()
        try:
            live = db.query(database.Project).filter(database.Project.deleted_at.is_(None))
            numbers = {
                "projects": live.count(),
                "in_trash": db.query(database.Project).filter(database.Project.deleted_at.isnot(None)).count(),
                "tasks": db.query(database.Task).count(),
                "references": db.query(database.Reference).count(),
                "progress_photos": db.query(database.ProgressPhoto).count(),
                "journal": db.query(database.Activity).count(),
            }
            numbers["balance"] = compute_balance(db, load_config())[0]
            missing = 0
            names = [(p or "") for (p,) in db.query(database.Project.photo)]
            missing += sum(1 for n in names if n and not os.path.exists(os.path.join(photos_dir, os.path.basename(n))))
            for model, sub in ((database.Reference, "refs"), (database.ProgressPhoto, "progress")):
                for (n,) in db.query(model.filename):
                    if not os.path.exists(os.path.join(photos_dir, sub, os.path.basename(n))):
                        missing += 1
            numbers["photo_files_missing"] = missing
            return numbers
        finally:
            db.close()
    finally:
        engine.dispose()


def check(backup_path: str, data_dir: str, out=print) -> bool:
    from hub.core import migrations
    from hub.core import database
    from sqlalchemy import create_engine

    out(f"Backup: {backup_path} ({os.path.getsize(backup_path) / 1024:.0f} KB)")
    tmp = tempfile.mkdtemp(prefix="project-hub-restore-check-")
    try:
        copy_path = os.path.join(tmp, "tracker.db")
        try:
            copy_db(backup_path, copy_path)
        except sqlite3.Error as e:
            out(f"❌ Not a readable SQLite database: {e}")
            return False

        con = sqlite3.connect(copy_path)
        try:
            result = con.execute("PRAGMA integrity_check").fetchone()[0]
            version = con.execute("PRAGMA user_version").fetchone()[0]
        finally:
            con.close()
        if result != "ok":
            out(f"❌ SQLite integrity check failed: {result}")
            return False
        out(f"✅ File is intact (schema version {version}, current {migrations.LATEST})")

        try:
            engine = create_engine(f"sqlite:///{copy_path}")
            database.Base.metadata.create_all(bind=engine)
            engine.dispose()
            migrations.migrate(copy_path, had_data=False, log=lambda m: out("   " + m))
        except Exception as e:
            out(f"❌ Migration of the copy failed: {e!r}")
            return False
        out("✅ Migrates to the current schema")

        photos_dir = os.path.join(data_dir, "photos")
        try:
            restored = read_numbers(copy_path, photos_dir)
        except Exception as e:
            out(f"❌ The app could not read the copy: {e!r}")
            return False
        out("✅ Readable by the app")

        live_path = os.path.join(data_dir, "tracker.db")
        current = None
        if os.path.exists(live_path) and os.path.abspath(live_path) != os.path.abspath(backup_path):
            live_copy = os.path.join(tmp, "live.db")
            try:
                copy_db(live_path, live_copy)
                migrations.migrate(live_copy, had_data=False, log=lambda m: None)
                current = read_numbers(live_copy, photos_dir)
            except Exception as e:
                out(f"   (could not read the live database for comparison: {e!r})")

        out("")
        out(f"{'':<22}{'backup':>10}{'now':>10}")
        for key, value in restored.items():
            now = "" if current is None else current.get(key, "")
            out(f"{key:<22}{value:>10}{now:>10}")
        if restored["photo_files_missing"]:
            out(f"\n⚠️ {restored['photo_files_missing']} photo file(s) the backup refers to are not in photos/: "
                f"after a restore those photos would be missing (photos are not part of a database backup).")
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    from hub.paths import DATA_DIR
    ap = argparse.ArgumentParser(description="Checks that a database backup restores (read-only).")
    ap.add_argument("path", nargs="?", help="backup file (default: the newest in backups/)")
    args = ap.parse_args()
    path = args.path or newest_backup(DATA_DIR)
    if not path:
        print(f"No backups in {os.path.join(DATA_DIR, 'backups')}. Pass a file: python tools/check_backup.py FILE")
        return 1
    if not os.path.isfile(path):
        print(f"No such file: {path}")
        return 1
    ok = check(path, DATA_DIR)
    print("\n✅ The backup can be restored." if ok else "\n❌ This backup would NOT restore.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
