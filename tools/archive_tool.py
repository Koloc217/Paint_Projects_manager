#!/usr/bin/env python3
"""Data archive from the console: the same zip as Settings -> Backup -> "Download archive" (see archive.py).

    python tools/archive_tool.py create                     # project-hub-<date>.zip in the current folder
    python tools/archive_tool.py create D:\\backups\\hub.zip  # to a chosen file
    python tools/archive_tool.py check  hub.zip             # check an archive, nothing is changed
    python tools/archive_tool.py restore hub.zip            # put the archive's data in place of the current data
    python tools/archive_tool.py restore hub.zip --no-config   # keep the current config.json

Restore works while the app is running, but stopping it first is the calm way: the bot and the scheduler then
cannot write anything in the middle. The current database is saved to backups/tracker-before-restore-<date>.db.
On a new machine: install the app, run this restore, start the app; .env (bot token) and the login password
are not in the archive and are set up there as usual.

Exit code 0 on success, 1 on an error (the reason is printed).
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import archive                      # noqa: E402
from database import ensure_schema  # noqa: E402
from i18n import tr                 # noqa: E402


def _mb(path: str) -> str:
    return f"{os.path.getsize(path) / 1024 / 1024:.1f} MB"


def main() -> int:
    parser = argparse.ArgumentParser(description="Project Hub data archive (database, config.json, photos)")
    sub = parser.add_subparsers(dest="command", required=True)
    p_create = sub.add_parser("create", help="make an archive")
    p_create.add_argument("path", nargs="?", help="archive file (default: project-hub-<date>.zip here)")
    p_check = sub.add_parser("check", help="check an archive without restoring it")
    p_check.add_argument("path")
    p_restore = sub.add_parser("restore", help="restore data from an archive")
    p_restore.add_argument("path")
    p_restore.add_argument("--no-config", action="store_true", help="keep the current config.json")
    args = parser.parse_args()

    try:
        if args.command == "create":
            ensure_schema()
            path = os.path.abspath(args.path or archive.archive_name())
            m = archive.make_archive(path)
            print(f"✅ {path} ({_mb(path)}): projects {m['projects']}, tasks {m['tasks']}, photos {m['photos']}")
            return 0
        if not os.path.isfile(args.path):
            print(f"❌ no such file: {args.path}")
            return 1
        if args.command == "check":
            m = archive.check_archive(args.path)
            print(f"✅ the archive is fine: made {m.get('created_at')}, projects {m.get('projects')}, "
                  f"tasks {m.get('tasks')}, photos {m.get('photos')}")
            return 0
        ensure_schema()                     # a fresh install: create the database file first
        s = archive.restore_archive(args.path, with_config=not args.no_config)
        print(f"✅ restored: projects {s['projects']}, tasks {s['tasks']}, photos {s['photos']}"
              f"{', config.json' if s['config'] else ''}. Previous database: backups/{s['saved_copy']}")
        return 0
    except archive.ArchiveError as e:
        print(f"❌ {tr(e.key, **e.params)}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
