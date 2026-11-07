"""API for external devices (the e-ink photo frame, ESP32 screens): /api/v1/...

The app only hands out data; each device lays it out itself (fonts, colors, clipping to its own width).

Off by default. In config.json:
    "DEVICE_API": {"enabled": false, "key": "", "hot_days": 3}

  • enabled  — the /api/v1/ addresses answer only when this is true (otherwise 404);
  • key      — optional secret. When it is set, a device must send it (header X-Device-Key or ?key=...).
               When app login is on, the key is REQUIRED: without it /api/v1/ stays closed, so turning on the
               login never leaves task names open to the network by accident;
  • hot_days — a task with a deadline within this many days (or overdue) is "hot".

GET /api/v1/hot?limit=2 picks what a small screen should show:
  1. hot tasks: overdue first (oldest first), then deadlines within hot_days, nearest first — up to `limit`;
  2. no hot tasks: the single nearest task with a deadline, however far away;
  3. no tasks with a deadline at all: an empty list and a ready phrase ("no tasks, check the tasks").
Tasks without a deadline never count. Closed tasks, finished or paused projects and the trash are left out.
General tasks (no project) count too: their "project" is the "General" label and "project_id" is null.

Answer: {"mode": "hot" | "nearest" | "empty", "today": "YYYY-MM-DD", "tasks": [...], "message": "..."}
  each task: {"id", "name", "project", "project_id" (null for a general task), "deadline": "YYYY-MM-DD", "days_left": int, "overdue": bool}
  message is filled only for "empty" (in the app's language).
"""
import secrets
from datetime import date

from common import config_value, is_done, live_projects, live_tasks, STATUS_ON_HOLD
from database import Task
from i18n import tr

MODE_HOT = "hot"
MODE_NEAREST = "nearest"
MODE_EMPTY = "empty"

MAX_LIMIT = 5


def settings() -> dict:
    return config_value("DEVICE_API") or {}


def enabled() -> bool:
    return bool(settings().get("enabled"))


def key_ok(sent, login_on: bool) -> bool:
    """True if the request may read /api/v1/. sent = the key the device sent (or None)."""
    key = settings().get("key") or ""
    if not key:
        return not login_on
    if not sent:
        return False
    return secrets.compare_digest(str(sent).encode("utf-8"), key.encode("utf-8"))


def _row(t, project, today: date) -> dict:
    day = t.deadline.date()
    left = (day - today).days
    return {
        "id": t.id,
        "name": t.task_name,
        "project": project.name if project else tr("general.label"),
        "project_id": project.id if project else None,
        "deadline": day.isoformat(),
        "days_left": left,
        "overdue": left < 0,
    }


def hot(db, limit: int = 2, today: date = None) -> dict:
    today = today or date.today()
    limit = max(1, min(int(limit), MAX_LIMIT))
    hot_days = settings().get("hot_days", 3)

    projects = {p.id: p for p in live_projects(db).all()
                if not is_done(p.status) and p.status != STATUS_ON_HOLD}
    open_tasks = [t for t in live_tasks(db).filter(Task.deadline.isnot(None)).all()
                  if (t.project_id is None or t.project_id in projects) and not is_done(t.status)]
    # nearest (and most overdue) first; equal dates keep a stable order
    open_tasks.sort(key=lambda t: (t.deadline, t.id))

    hot_tasks = [t for t in open_tasks if (t.deadline.date() - today).days <= hot_days]
    if hot_tasks:
        mode, chosen = MODE_HOT, hot_tasks[:limit]
    elif open_tasks:
        mode, chosen = MODE_NEAREST, open_tasks[:1]
    else:
        mode, chosen = MODE_EMPTY, []

    return {
        "mode": mode,
        "today": today.isoformat(),
        "tasks": [_row(t, projects.get(t.project_id), today) for t in chosen],
        "message": tr("devices.no_tasks") if mode == MODE_EMPTY else "",
    }
