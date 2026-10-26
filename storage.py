"""How much space the app takes: code, database, photos, logs.

Computed by walking folders (os.scandir), without du or other external commands, so it works the same on Windows
and Linux. The size is the file length in bytes (as in file properties), not the space allocated on disk.
Symlinks are not followed: only what really lies in the app folder is counted.
"""
import os

from common import BASE_DIR, DATA_DIR

PHOTOS_DIR = os.path.join(DATA_DIR, "photos")
LOGS_DIR = os.path.join(DATA_DIR, "logs")
BACKUPS_DIR = os.path.join(DATA_DIR, "backups")      # database copies made by tools/deploy.sh

# Top-level folders of the app that are counted separately, not as "app files"
DATA_DIRS = {"photos", "logs", "backups", ".git", ".venv", "venv"}


def _is_db_file(name: str) -> bool:
    # tracker.db and the SQLite side files next to it (-wal, -shm, -journal)
    return name == "tracker.db" or name.startswith("tracker.db-")


def _is_log_file(name: str) -> bool:
    # old logs in the root folder (notify.log, backup.log and their rotations) from before the move to logs/
    return name.endswith(".log") or ".log." in name


def _file_size(entry) -> int:
    try:
        return entry.stat(follow_symlinks=False).st_size
    except OSError:
        return 0


def dir_size(path: str):
    """(bytes, number of files) in a folder with all subfolders. No folder: (0, 0); unreadable parts are skipped."""
    total, count = 0, 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += _file_size(entry)
                            count += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return total, count


def _item(key: str, size: int, files: int, children=None) -> dict:
    item = {"key": key, "bytes": size, "files": files}
    if children:
        item["children"] = children
    return item


def usage() -> dict:
    """Used space by category. Categories that do not exist (database copies, .git) are left out."""
    app_bytes = app_files = 0
    db_bytes = db_files = 0
    loose_log_bytes = loose_log_files = 0
    optional = {}

    try:
        entries = list(os.scandir(BASE_DIR))
    except OSError:
        entries = []
    for entry in entries:
        name = entry.name
        try:
            if entry.is_dir(follow_symlinks=False):
                if name in DATA_DIRS:
                    if name in (".git", ".venv", "venv"):
                        optional[name] = dir_size(entry.path)
                    continue
                b, c = dir_size(entry.path)
                app_bytes += b
                app_files += c
            elif entry.is_file(follow_symlinks=False):
                size = _file_size(entry)
                if _is_db_file(name):
                    db_bytes += size
                    db_files += 1
                elif _is_log_file(name):
                    loose_log_bytes += size
                    loose_log_files += 1
                else:
                    app_bytes += size
                    app_files += 1
        except OSError:
            continue

    # Data kept outside the code folder (PROJECT_HUB_DATA_DIR): its database and loose log files
    if os.path.normcase(DATA_DIR) != os.path.normcase(BASE_DIR):
        try:
            data_entries = list(os.scandir(DATA_DIR))
        except OSError:
            data_entries = []
        for entry in data_entries:
            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
                if _is_db_file(entry.name):
                    db_bytes += _file_size(entry)
                    db_files += 1
                elif _is_log_file(entry.name):
                    loose_log_bytes += _file_size(entry)
                    loose_log_files += 1
            except OSError:
                continue

    # photos: covers lie directly in photos/, references in photos/refs, progress photos in photos/progress
    refs = dir_size(os.path.join(PHOTOS_DIR, "refs"))
    progress = dir_size(os.path.join(PHOTOS_DIR, "progress"))
    photos_all = dir_size(PHOTOS_DIR)
    other = (max(photos_all[0] - refs[0] - progress[0], 0), max(photos_all[1] - refs[1] - progress[1], 0))
    photos = _item("photos", photos_all[0], photos_all[1], [
        _item("photos_covers", *other),
        _item("photos_refs", *refs),
        _item("photos_progress", *progress),
    ])

    logs_dir = dir_size(LOGS_DIR)
    items = [
        _item("app", app_bytes, app_files),
        _item("db", db_bytes, db_files),
        photos,
        _item("logs", logs_dir[0] + loose_log_bytes, logs_dir[1] + loose_log_files),
    ]

    backups = dir_size(BACKUPS_DIR)
    if backups[1]:
        items.append(_item("backups", *backups))
    venv = tuple(map(sum, zip(optional.get(".venv", (0, 0)), optional.get("venv", (0, 0)))))
    if venv[1]:
        items.append(_item("venv", *venv))
    if optional.get(".git", (0, 0))[1]:
        items.append(_item("git", *optional[".git"]))

    return {
        "items": items,
        "total": sum(i["bytes"] for i in items),
        "path": BASE_DIR,
    }


if __name__ == "__main__":
    # quick check from the console: python storage.py
    data = usage()
    for it in data["items"]:
        print(f"{it['key']:<16} {it['bytes']:>14,} B  {it['files']:>6} files")
        for ch in it.get("children", []):
            print(f"  {ch['key']:<14} {ch['bytes']:>14,} B  {ch['files']:>6} files")
    print(f"{'total':<16} {data['total']:>14,} B")
