"""Отпуск: пауза уведомлений на N дней (не больше 30) со сдвигом всех сроков на те же N дней.

В config.json: "VACATION": {"start": "YYYY-MM-DD", "until": "YYYY-MM-DD", "days": N}.
Отпуск действует, пока сегодня < until. Отмена возвращает уведомления, но сроки остаются сдвинутыми.
"""
from datetime import date, datetime, timedelta

from common import load_config, save_config, is_done, live_projects, live_tasks
from database import Project, Task

MAX_DAYS = 30


def _parse(value):
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def state(cfg: dict = None, today: date = None) -> dict:
    """{"active": bool, "start", "until", "days"}; until — первый день после отпуска."""
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
    """Начинает отпуск: сдвигает сроки и «последнее движение» по незавершённым проектам на days дней.
    Возвращает (None, {"projects": n, "tasks": m}) или (код_ошибки, None)."""
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= MAX_DAYS:
        return "bad_days", None
    cfg = load_config()
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
        # Детектор застоя не должен сработать сразу после возвращения: отпуск не считается простоем
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
    save_config(cfg)
    return None, {"projects": moved_projects, "tasks": moved_tasks}


def cancel(now: datetime = None) -> bool:
    """Досрочно заканчивает отпуск: уведомления снова идут, сроки не возвращаются."""
    cfg = load_config()
    if not state(cfg)["active"]:
        return False
    v = dict(cfg["VACATION"])
    v["until"] = (now or datetime.now()).date().isoformat()
    v["cancelled"] = True
    cfg["VACATION"] = v
    save_config(cfg)
    return True
