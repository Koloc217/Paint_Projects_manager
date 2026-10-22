"""Сколько места занимает приложение: код, база, фото, журналы.

Считается обходом папок (os.scandir), без du и других внешних команд, поэтому одинаково работает в Windows и Linux.
Размер — длина файлов в байтах (как в свойствах файла), а не место, выделенное на диске.
Симлинки не открываются: учитывается только то, что реально лежит в папке приложения.
"""
import os

from common import BASE_DIR

PHOTOS_DIR = os.path.join(BASE_DIR, "photos")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
BACKUPS_DIR = os.path.join(BASE_DIR, "backups")      # копии базы, которые делает tools/deploy.sh

# Верхний уровень папки приложения, который считается отдельно, а не как «файлы приложения»
DATA_DIRS = {"photos", "logs", "backups", ".git", ".venv", "venv"}


def _is_db_file(name: str) -> bool:
    # tracker.db и служебные файлы SQLite рядом с ней (-wal, -shm, -journal)
    return name == "tracker.db" or name.startswith("tracker.db-")


def _is_log_file(name: str) -> bool:
    # старые журналы в корне (notify.log, backup.log и их ротации) до переезда в logs/
    return name.endswith(".log") or ".log." in name


def _file_size(entry) -> int:
    try:
        return entry.stat(follow_symlinks=False).st_size
    except OSError:
        return 0


def dir_size(path: str):
    """(байты, число файлов) в папке со всеми подпапками. Нет папки — (0, 0); недоступное пропускается."""
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
    """Разбивка занятого места по категориям. Категории, которых нет (копий базы, .git), не выводятся."""
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

    # фото: обложки лежат прямо в photos/, референсы в photos/refs, фото прогресса в photos/progress
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
    # быстрая проверка из консоли: python storage.py
    data = usage()
    for it in data["items"]:
        print(f"{it['key']:<16} {it['bytes']:>14,} B  {it['files']:>6} files")
        for ch in it.get("children", []):
            print(f"  {ch['key']:<14} {ch['bytes']:>14,} B  {ch['files']:>6} files")
    print(f"{'total':<16} {data['total']:>14,} B")
