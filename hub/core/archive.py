"""Full backup as one zip archive: the database, config.json and every photo (covers, references, progress).

    make_archive(path)          writes the archive (works while the app is running)
    restore_archive(path)       puts an archive in place of the current data

Used by the web app (Settings -> Backup: "Download archive" / "Restore from archive") and by tools/archive_tool.py.

Inside the archive:
    manifest.json      format, creation time, schema version of the database, counts
    tracker.db         a consistent snapshot (sqlite3 backup API: no need to stop the app)
    config.json        settings
    photos/...         the photos folder as is

Not included: .env and auth.json (secrets, the login password), tls/ certificates, logs, orphans/ and backups/.
After a restore on another machine these stay as they are there.

Restore:
- the archive is checked first (manifest, only known paths, SQLite integrity_check of the database) — nothing
  is changed if anything is wrong;
- the current database is saved to backups/tracker-before-restore-<date>.db;
- the database is written into the live file with the sqlite3 backup API, so it works while the app runs and on
  Windows (the file is never replaced, open connections simply see the new content), then brought to the current
  schema by the regular migrations (an archive from an older version is fine; from a newer one it is refused);
- photos from the archive are added to photos/ (same names are overwritten). Photos that exist now but are not in
  the archive are left alone: no row points to them, so the regular orphan check moves them to orphans/ later.
"""
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime

from hub.core.database import DATA_DIR, DB_PATH, engine
from hub.core import migrations

FORMAT = 1
PHOTOS_DIR = os.path.join(DATA_DIR, "photos")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
BACKUPS_DIR = os.path.join(DATA_DIR, "backups")
KEEP_RESTORE_COPIES = 3

# Photos are JPEG/PNG/WebP already: compressing them again only costs time on a Raspberry Pi
_STORED_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".avif")


class ArchiveError(Exception):
    """The archive cannot be restored. `key` is a locales.json key, `params` its placeholders."""

    def __init__(self, key: str, **params):
        super().__init__(key)
        self.key = key
        self.params = params


def cleanup_temp(max_age_sec: int = 3600) -> int:
    """Removes temp files and folders left by an archive job that was cut off (the app stopped in the middle)."""
    import time
    removed = 0
    try:
        entries = list(os.scandir(DATA_DIR))
    except OSError:
        return 0
    for entry in entries:
        if not entry.name.startswith((".archive-", ".restore-", ".download-", ".upload-")):
            continue
        try:
            if time.time() - entry.stat(follow_symlinks=False).st_mtime < max_age_sec:
                continue
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path, ignore_errors=True)
            else:
                os.remove(entry.path)
            removed += 1
        except OSError:
            continue
    return removed


def archive_name(now: datetime = None) -> str:
    return f"project-hub-{(now or datetime.now()):%Y-%m-%d-%H%M}.zip"


def _snapshot_db(dst_path: str) -> None:
    src = sqlite3.connect(DB_PATH, timeout=30)
    dst = sqlite3.connect(dst_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _db_info(db_path: str) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        info = {"db_version": conn.execute("PRAGMA user_version").fetchone()[0]}
        for table in ("projects", "tasks"):
            try:
                info[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                info[table] = 0
        return info
    finally:
        conn.close()


def _photo_files():
    """(full path, path inside the archive) for every file under photos/, symlinks skipped."""
    for root, dirs, files in os.walk(PHOTOS_DIR):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for name in sorted(files):
            full = os.path.join(root, name)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, DATA_DIR).replace(os.sep, "/")
            yield full, rel


def make_archive(out_path: str) -> dict:
    """Writes the archive to out_path. Returns the manifest."""
    tmp_dir = tempfile.mkdtemp(prefix=".archive-", dir=DATA_DIR)
    try:
        db_copy = os.path.join(tmp_dir, "tracker.db")
        _snapshot_db(db_copy)
        photos = list(_photo_files())
        manifest = {
            "format": FORMAT,
            "app": "project-hub",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            **_db_info(db_copy),
            "photos": len(photos),
            "config": os.path.isfile(CONFIG_FILE),
        }
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            zf.write(db_copy, "tracker.db")
            if manifest["config"]:
                zf.write(CONFIG_FILE, "config.json")
            for full, rel in photos:
                kind = zipfile.ZIP_STORED if rel.lower().endswith(_STORED_EXT) else zipfile.ZIP_DEFLATED
                try:
                    zf.write(full, rel, compress_type=kind)
                except OSError:
                    continue          # deleted between listing and reading: the database no longer needs it
        return manifest
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _safe_photo_path(name: str):
    """photos/<...> inside the archive -> path relative to photos/, or None for anything unexpected."""
    if not name.startswith("photos/") or name.endswith("/") or "\\" in name:
        return None
    parts = name.split("/")[1:]
    if not parts or any(p in ("", ".", "..") or ":" in p for p in parts):
        return None
    return os.path.join(*parts)


def check_archive(zip_path: str) -> dict:
    """Opens and checks the archive without changing anything. Returns the manifest."""
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError):
        raise ArchiveError("archive.err_not_zip")
    with zf:
        names = zf.namelist()
        if "manifest.json" not in names or "tracker.db" not in names:
            raise ArchiveError("archive.err_not_ours")
        try:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ArchiveError("archive.err_not_ours")
        if not isinstance(manifest, dict) or manifest.get("app") != "project-hub":
            raise ArchiveError("archive.err_not_ours")
        if int(manifest.get("format") or 0) > FORMAT:
            raise ArchiveError("archive.err_newer")
        for name in names:
            if name in ("manifest.json", "tracker.db", "config.json") or name.endswith("/"):
                continue
            if _safe_photo_path(name) is None:
                raise ArchiveError("archive.err_bad_path", name=name[:80])
        if "config.json" in names:
            try:
                cfg = json.loads(zf.read("config.json").decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise ArchiveError("archive.err_bad_config")
            if not isinstance(cfg, dict):
                raise ArchiveError("archive.err_bad_config")
        bad = zf.testzip()
        if bad:
            raise ArchiveError("archive.err_damaged", name=bad[:80])
    return manifest


def _check_db_file(db_path: str) -> int:
    """SQLite integrity check of the database taken from the archive. Returns its schema version."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if ok != "ok":
                raise ArchiveError("archive.err_bad_db")
            conn.execute("SELECT COUNT(*) FROM projects").fetchone()
            return conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        raise ArchiveError("archive.err_bad_db")


def _save_current_db() -> str:
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    path = os.path.join(BACKUPS_DIR, f"tracker-before-restore-{datetime.now():%Y%m%d-%H%M%S}.db")
    if os.path.isfile(DB_PATH):
        _snapshot_db(path)
    copies = sorted(f for f in os.listdir(BACKUPS_DIR) if f.startswith("tracker-before-restore-"))
    for old in copies[:-KEEP_RESTORE_COPIES]:
        try:
            os.remove(os.path.join(BACKUPS_DIR, old))
        except OSError:
            pass
    return path


def restore_archive(zip_path: str, with_config: bool = True, log=print) -> dict:
    """Replaces the database (and config.json) with the archive's and adds its photos. Returns a summary."""
    manifest = check_archive(zip_path)
    tmp_dir = tempfile.mkdtemp(prefix=".restore-", dir=DATA_DIR)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            db_tmp = os.path.join(tmp_dir, "tracker.db")
            with zf.open("tracker.db") as src, open(db_tmp, "wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            version = _check_db_file(db_tmp)
            if version > migrations.LATEST:
                raise ArchiveError("archive.err_newer")

            # --- from here on the live data changes ---
            saved = _save_current_db()
            log(f"[ARCHIVE] current database saved to {saved}")
            src = sqlite3.connect(db_tmp)
            dst = sqlite3.connect(DB_PATH, timeout=30)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            migrations.migrate(DB_PATH, had_data=False, log=log)
            engine.dispose()                  # pooled connections reopen on the restored file

            photos = 0
            for info in zf.infolist():
                rel = _safe_photo_path(info.filename)
                if rel is None:
                    continue
                target = os.path.join(PHOTOS_DIR, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                part = target + ".part"
                with zf.open(info) as src_f, open(part, "wb") as dst_f:
                    shutil.copyfileobj(src_f, dst_f, 1024 * 1024)
                os.replace(part, target)
                photos += 1

            config = False
            if with_config and "config.json" in zf.namelist():
                from hub.core.appconfig import save_config
                save_config(json.loads(zf.read("config.json").decode("utf-8")))
                config = True
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    summary = {
        "created_at": manifest.get("created_at", ""),
        "projects": manifest.get("projects", 0),
        "tasks": manifest.get("tasks", 0),
        "photos": photos,
        "config": config,
        "saved_copy": os.path.basename(saved),
    }
    log(f"[ARCHIVE] restored: {summary}")
    return summary
