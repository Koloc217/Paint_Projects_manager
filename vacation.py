"""Vacation: notifications paused for N days (30 at most) and all deadlines moved by the same N days.

In config.json: "VACATION": {"start": "YYYY-MM-DD", "until": "YYYY-MM-DD", "days": N}.
The vacation is active while today < until. Cancelling brings notifications back; deadlines stay moved.
"""
from datetime import date, datetime, timedelta

from common import load_config, edit_config, is_done, live_projects, live_tasks
from database import Project, Task

MAX_DAYS = 30


def _parse(value):
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def state(cfg: dict = None, today: date = None) -> dict:
    """{"active": bool, "start", "until", "days"}; until is the first day after the vacation."""
    cfg = cfg if cfg is not None else load_config()
    today = today or date.today()
    v = cfg.get("VACATION") if isinstance(cfg.get("VACATION"), dict) else {}
    until = _parse(v.get("until"))
    active = bool(until and today < until)
    return {
        "active": active,
        "start": v.get("start") if active else None,
        "until": until.isoformat() if active else None,
        "days": int(v.get("days") or 0) if active else 0,
    }


def is_active(cfg: dict = None) -> bool:
    return state(cfg)["active"]


def start(db, days: int, now: datetime = None):
    """Starts a vacation: moves deadlines and the "last progress" time of unfinished projects by days days.
    Returns (None, {"projects": n, "tasks": m}) or (error_code, None)."""
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= MAX_DAYS:
        return "bad_days", None
    with edit_config() as cfg:          # the lock also stops two vacations from starting at once
        if state(cfg)["active"]:
            return "already", None
        now = now or datetime.now()
        delta = timedelta(days=days)

        projects = [p for p in live_projects(db).all() if not is_done(p.status)]
        open_ids = {p.id for p in projects}
        moved_projects = 0
        for p in projects:
            if p.estimate:
                p.estimate = p.estimate + delta
                moved_projects += 1
            # The stagnation detector must not fire right after the return: a vacation is not idle time
            if p.updated_at:
                p.updated_at = p.updated_at + delta
        moved_tasks = 0
        for t in live_tasks(db).filter(Task.deadline.isnot(None)).all():
            if t.project_id in open_ids and not is_done(t.status):
                t.deadline = t.deadline + delta
                moved_tasks += 1
        db.commit()

        cfg["VACATION"] = {
            "start": now.date().isoformat(),
            "until": (now.date() + delta).isoformat(),
            "days": days,
        }
        return None, {"projects": moved_projects, "tasks": moved_tasks}


def cancel(now: datetime = None) -> bool:
    """Ends a vacation early: notifications resume, deadlines are not moved back."""
    with edit_config() as cfg:
        if not state(cfg)["active"]:
            return False
        v = dict(cfg["VACATION"])
        v["until"] = (now or datetime.now()).date().isoformat()
        v["cancelled"] = True
        cfg["VACATION"] = v
        return True
