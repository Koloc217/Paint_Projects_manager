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

GET /api/v1/project[?id=PRJ-...] gives one project for a screen that shows a project instead of a task list:
the one with the given id, or (no id, or an unfinished project is not found) the project in focus, picked as in the bot.
Answer: {"mode": "project" | "focus" | "empty", "today": "YYYY-MM-DD", "message": "...", "project": {...} | null}
  project: {"id", "name", "status", "stage", "priority", "deadline": "YYYY-MM-DD" | null, "days_left": int | null,
            "overdue": bool, "tasks_open", "tasks_total", "next_task": {"id", "name", "deadline", "days_left"} | null}
  next_task: the open task with the nearest deadline (tasks without a date come last); message only for "empty".

Answer of /hot: {"mode": "hot" | "nearest" | "empty", "today": "YYYY-MM-DD", "tasks": [...], "message": "..."}
  each task: {"id", "name", "project", "project_id" (null for a general task), "deadline": "YYYY-MM-DD", "days_left": int, "overdue": bool}
  message is filled only for "empty" (in the app's language).
"""
import secrets
from datetime import date

from hub.core.appconfig import config_value
from hub.core.constants import NO_STAGE, is_done, STATUS_ON_HOLD
from hub.core.queries import focus_project, live_projects, live_tasks
from hub.core.database import Task
from hub.core.i18n import tr

MODE_HOT = "hot"
MODE_NEAREST = "nearest"
MODE_EMPTY = "empty"
MODE_PROJECT = "project"
MODE_FOCUS = "focus"

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


def _days_left(value, today: date):
    day = value.date() if hasattr(value, "date") else value
    return None if day is None else (day - today).days


def project_view(db, project_id: str = None, today: date = None) -> dict:
    today = today or date.today()
    active = [p for p in live_projects(db).all() if not is_done(p.status)]
    proj = next((p for p in active if p.id == project_id), None) if project_id else None
    mode = MODE_PROJECT
    if proj is None:
        proj, mode = focus_project(active), MODE_FOCUS
    if proj is None:
        return {"mode": MODE_EMPTY, "today": today.isoformat(), "project": None, "message": tr("devices.no_projects")}

    tasks = live_tasks(db).filter(Task.project_id == proj.id).all()
    open_tasks = [t for t in tasks if not is_done(t.status)]
    open_tasks.sort(key=lambda t: (t.deadline is None, t.deadline or 0, t.id))
    nxt = open_tasks[0] if open_tasks else None
    left = _days_left(proj.estimate, today)
    return {
        "mode": mode,
        "today": today.isoformat(),
        "message": "",
        "project": {
            "id": proj.id,
            "name": proj.name,
            "status": proj.status,
            "stage": proj.stage or NO_STAGE,
            "priority": proj.priority,
            "deadline": proj.estimate.date().isoformat() if proj.estimate else None,
            "days_left": left,
            "overdue": left is not None and left < 0,
            "tasks_open": len(open_tasks),
            "tasks_total": len(tasks),
            "next_task": None if nxt is None else {
                "id": nxt.id,
                "name": nxt.task_name,
                "deadline": nxt.deadline.date().isoformat() if nxt.deadline else None,
                "days_left": _days_left(nxt.deadline, today),
            },
        },
    }
