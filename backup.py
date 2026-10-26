"""Sends a copy of projects and tasks to Google Sheets through a Google Apps Script web app (APPS_SCRIPT_URL in .env).

Photos, references and balance adjustments are not included: copy tracker.db and photos/ separately.
The exit code is 0 on success and 1 on failure, so the scheduler log shows failed runs.
"""
import os
import sys
import httpx
from datetime import datetime
from dotenv import load_dotenv
from database import SessionLocal, ensure_schema
from common import DATA_DIR, NO_STAGE, legacy_value, live_projects, live_tasks

load_dotenv(os.path.join(DATA_DIR, ".env"))
APPS_SCRIPT_URL = os.getenv("APPS_SCRIPT_URL")


def _dt(value, fmt):
    return value.strftime(fmt) if value else ""


def main() -> int:
    ensure_schema()
    print(f"[{datetime.now()}] Starting the backup...")

    if not APPS_SCRIPT_URL:
        print("❌ Error: APPS_SCRIPT_URL is not set in .env!")
        return 1

    db = SessionLocal()
    try:
        proj_data = [{
            "id": p.id,
            "name": p.name,
            # the sheet keeps the old Russian values: the backup format does not change
            "status": legacy_value(p.status),
            "stage": legacy_value(p.stage or NO_STAGE),
            "priority": p.priority,
            "estimate": _dt(p.estimate, "%Y-%m-%d"),
            "token": p.token or 0,
            "token_reward": p.token_reward or 0,
            "paint_notes": p.paint_notes or "",
            "completed_at": _dt(p.completed_at, "%Y-%m-%d %H:%M:%S"),
        } for p in live_projects(db).all()]

        task_data = [{
            "id": t.id,
            "project_id": t.project_id,
            "task_name": t.task_name,
            "deadline": _dt(t.deadline, "%Y-%m-%d"),
            "status": legacy_value(t.status),
            "postponed": t.postponed or 0,
            "completed_at": _dt(t.completed_at, "%Y-%m-%d %H:%M:%S"),
        } for t in live_tasks(db).all()]
    finally:
        db.close()

    payload = {"projects": proj_data, "tasks": task_data, "timestamp": datetime.now().isoformat()}
    try:
        with httpx.Client(timeout=25.0, follow_redirects=True) as client:
            resp = client.post(APPS_SCRIPT_URL, json=payload)
    except httpx.HTTPError as e:
        print(f"[{datetime.now()}] ❌ Could not reach Google: {e.__class__.__name__}: {e}")
        return 1
    if resp.status_code in (200, 201):
        print(f"[{datetime.now()}] ✅ Backup sent to Google Sheets ({len(proj_data)} projects, {len(task_data)} tasks)")
        return 0
    print(f"[{datetime.now()}] ❌ Google server error (code {resp.status_code}): {resp.text[:300]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
