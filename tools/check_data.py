#!/usr/bin/env python3
"""Data integrity report for the live app data (read-only unless --fix).

    python tools/check_data.py           # report
    python tools/check_data.py --fix     # also move unreferenced photos to orphans/ (what the app does every 6 h)

Reports:
  • rows that point to a project that does not exist (foreign keys; should be none);
  • rows whose photo file is missing on disk (the app hides such photos, nothing breaks);
  • app photo files no row points to, older than an hour (they go to orphans/<date>/ and are erased after
    30 days; move a file back into its folder if it was needed).
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main() -> int:
    ap = argparse.ArgumentParser(description="Data integrity report.")
    ap.add_argument("--fix", action="store_true", help="move unreferenced photos to orphans/")
    args = ap.parse_args()

    from hub.core.database import SessionLocal, ensure_schema
    from hub.core import integrity

    ensure_schema()
    db = SessionLocal()
    try:
        fk = integrity.foreign_key_problems(db)
        missing = integrity.find_missing_files(db)
        orphans = integrity.find_orphan_files(db)
        print(f"Rows without a project: {len(fk)}")
        for row in fk[:20]:
            print(f"   {row}")
        print(f"Rows with a missing photo file: {sum(len(v) for v in missing.values())}")
        for kind, rows in missing.items():
            for pid, name in rows[:20]:
                print(f"   {kind:<9} {pid}  {name}")
        print(f"Photo files without a row: {sum(len(v) for v in orphans.values())}")
        for kind, paths in orphans.items():
            for path in paths[:20]:
                print(f"   {kind:<9} {os.path.basename(path)}")
        if args.fix and orphans:
            moved = integrity.quarantine(orphans)
            print(f"Moved to {integrity.ORPHANS_DIR}: {moved}")
        return 1 if fk else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
