"""Project journal (the "Journal" view of the Feed tab): status and stage changes, tasks, deadlines.

Events are written in one place, right before SQLAlchemy flushes changes to the database (before_flush), so every
path is covered the same way: the web UI, the bot, pipeline templates, the trash. Nothing has to remember to log.

What is recorded
  project_created, project_status, project_stage, project_deadline, project_deleted, project_restored,
  task_created, task_status, task_deadline, task_deleted.
What is not
  • tasks closed together with their project (the project_status event already says it);
  • deadlines moved by a vacation (one vacation event instead, see quiet());
  • renames, notes, photos, priority (not progress on the project).

The source of an event is "web" by default; the bot marks its own with source("bot"). Existing data gets a one-time
history from completion dates (backfill), marked "history".
"""
import contextlib
import contextvars
from datetime import datetime

from sqlalchemy import event, inspect

from hub.core.database import SessionLocal, Activity, Project, Task, gen_proj_id, gen_task_id
from hub.core.common import is_done, iso_date

KINDS = ("project_created", "project_status", "project_stage", "project_deadline", "project_deleted",
         "project_restored", "task_created", "task_status", "task_deadline", "task_deleted", "vacation")

_source = contextvars.ContextVar("activity_source", default="web")
_quiet_deadlines = contextvars.ContextVar("activity_quiet_deadlines", default=False)


@contextlib.contextmanager
def source(name: str):
    """with activity.source("bot"): ... — events inside are marked with this source."""
    token = _source.set(name)
    try:
        yield
    finally:
        _source.reset(token)


@contextlib.contextmanager
def quiet_deadlines():
    """Deadline changes inside are not logged one by one (vacation moves them all at once)."""
    token = _quiet_deadlines.set(True)
    try:
        yield
    finally:
        _quiet_deadlines.reset(token)


def _changed(obj, attr):
    """(old, new) if the attribute changed in this flush, else None."""
    hist = inspect(obj).attrs[attr].history
    if not hist.has_changes():
        return None
    old = hist.deleted[0] if hist.deleted else None
    new = hist.added[0] if hist.added else None
    return None if old == new else (old, new)


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return iso_date(value)
    return str(value)


def _event(session, kind, project, task=None, old=None, new=None, project_id=None):
    session.add(Activity(
        created_at=datetime.now(), kind=kind, source=_source.get(),
        project_id=project.id if project is not None else (task.project_id if task is not None else project_id),
        project_name=(project.name if project is not None else "") or "",
        task_id=task.id if task is not None else None, task_name=(task.task_name if task is not None else "") or "",
        old=_text(old), new=_text(new),
    ))


def _project_of(session, task, cache):
    pid = task.project_id
    if pid is None:                        # a general task: no project
        return None
    if pid not in cache:
        cache[pid] = session.get(Project, pid)
    return cache[pid]


def _before_flush(session, flush_context, instances):
    if session.info.get("activity_off"):
        return
    projects = {}
    status_changed = set()                 # projects whose status changed in this flush
    quiet = _quiet_deadlines.get()

    for obj in list(session.new):            # ids come from column defaults at flush time; the journal needs them now
        if isinstance(obj, Project) and not obj.id:
            obj.id = gen_proj_id()
        elif isinstance(obj, Task) and not obj.id:
            obj.id = gen_task_id()
    for obj in list(session.new):
        if isinstance(obj, Project):
            projects[obj.id] = obj
            _event(session, "project_created", obj, new=obj.status)
    for obj in list(session.new):
        if isinstance(obj, Task):
            _event(session, "task_created", _project_of(session, obj, projects), obj, new=obj.deadline)

    for obj in list(session.dirty):
        if isinstance(obj, Project) and obj not in session.new:
            projects[obj.id] = obj
            ch = _changed(obj, "status")
            if ch:
                status_changed.add(obj.id)
                _event(session, "project_status", obj, old=ch[0], new=ch[1])
            ch = _changed(obj, "stage")
            if ch:
                _event(session, "project_stage", obj, old=ch[0], new=ch[1])
            ch = _changed(obj, "estimate")
            if ch and not quiet:
                _event(session, "project_deadline", obj, old=ch[0], new=ch[1])
            ch = _changed(obj, "deleted_at")
            if ch:
                _event(session, "project_deleted" if ch[1] else "project_restored", obj)

    for obj in list(session.dirty):
        if isinstance(obj, Task) and obj not in session.new:
            proj = _project_of(session, obj, projects)
            ch = _changed(obj, "status")
            if ch and obj.project_id not in status_changed:
                _event(session, "task_status", proj, obj, old=ch[0], new=ch[1])
            ch = _changed(obj, "deadline")
            if ch and not quiet:
                _event(session, "task_deadline", proj, obj, old=ch[0], new=ch[1])

    deleted_projects = {o.id for o in session.deleted if isinstance(o, Project)}
    for obj in list(session.deleted):
        if isinstance(obj, Task) and obj.project_id not in deleted_projects:
            _event(session, "task_deleted", _project_of(session, obj, projects), obj)


event.listen(SessionLocal, "before_flush", _before_flush)


def log_vacation(days: int, moved: dict) -> None:
    db = SessionLocal()
    try:
        db.add(Activity(created_at=datetime.now(), kind="vacation", source=_source.get(), project_id=None,
                        old=str(days), new=f"{moved.get('projects', 0)}/{moved.get('tasks', 0)}"))
        db.commit()
    finally:
        db.close()


def backfill_once() -> int:
    """First start with the journal: restores what can be restored from dates already in the database
    (finished projects, closed tasks). Runs only while the journal is empty. Returns the number of events."""
    db = SessionLocal()
    db.info["activity_off"] = True
    try:
        if db.query(Activity.id).first() is not None:
            return 0
        rows = []
        projects = {p.id: p for p in db.query(Project).all()}
        for p in projects.values():
            if p.completed_at and is_done(p.status):
                rows.append(Activity(created_at=p.completed_at, kind="project_status", source="history",
                                     project_id=p.id, project_name=p.name, new=p.status))
        for t in db.query(Task).filter(Task.completed_at.isnot(None)).all():
            if not is_done(t.status):
                continue
            p = projects.get(t.project_id)
            if p is None and t.project_id is not None:
                continue                       # the project is gone
            if p is not None and p.completed_at and t.completed_at == p.completed_at:
                continue                       # closed together with the project
            rows.append(Activity(created_at=t.completed_at, kind="task_status", source="history",
                                 project_id=p.id if p else None, project_name=p.name if p else "",
                                 task_id=t.id, task_name=t.task_name, new=t.status))
        db.add_all(rows)
        db.commit()
        return len(rows)
    finally:
        db.close()


def _label(kind: str, value: str) -> str:
    from hub.core.i18n import tr, tv
    if not value:
        return tr("journal.none")
    if kind.endswith("_deadline") or kind == "task_created":
        try:
            from hub.core.common import fmt_date, parse_iso_date
            return fmt_date(parse_iso_date(value))
        except (TypeError, ValueError):
            return value
    return tv(value)


def describe(ev: Activity) -> str:
    """One journal line in the interface language (the project name is shown separately)."""
    from hub.core.i18n import tr
    old, new = _label(ev.kind, ev.old), _label(ev.kind, ev.new)
    task = ev.task_name or ""
    if ev.kind == "project_created":
        return tr("journal.project_created", status=new)
    if ev.kind in ("project_status", "project_stage", "project_deadline"):
        what = tr({"project_status": "journal.status", "project_stage": "journal.stage",
                   "project_deadline": "journal.project_deadline"}[ev.kind])
        return f"{what}: {new}" if not ev.old else f"{what}: {old} → {new}"
    if ev.kind == "project_deleted":
        return tr("journal.project_deleted")
    if ev.kind == "project_restored":
        return tr("journal.project_restored")
    if ev.kind == "task_created":
        return tr("journal.task_created", task=task) + (f" · {tr('journal.deadline')} {new}" if ev.new else "")
    if ev.kind == "task_status":
        return tr("journal.task_status", task=task, change=new if not ev.old else f"{old} → {new}")
    if ev.kind == "task_deadline":
        return tr("journal.task_deadline", task=task, change=f"{old} → {new}")
    if ev.kind == "task_deleted":
        return tr("journal.task_deleted", task=task)
    if ev.kind == "vacation":
        p, _, t = (ev.new or "0/0").partition("/")
        return tr("journal.vacation", days=ev.old or "?", projects=p, tasks=t or "0")
    return ev.kind


def count_postponements(db, since: datetime, until: datetime = None) -> int:
    """Shame metric: how many times task deadlines were moved later in [since, until).

    Counted from the journal, so deleted tasks still count: the bot's "+2 days" and a later date set in the web
    editor. Setting a first deadline, moving it earlier and a vacation (not logged one by one) do not count."""
    q = db.query(Activity.old, Activity.new).filter(Activity.kind == "task_deadline", Activity.created_at >= since)
    if until is not None:
        q = q.filter(Activity.created_at < until)
    return sum(1 for old, new in q.all() if old and new and new > old)       # ISO dates compare as strings


def list_events(db, project_id: str = None, offset: int = 0, limit: int = 200) -> list:
    """Newest first. History events carry their original dates, so the order is by time, not by id."""
    q = db.query(Activity)
    if project_id:
        q = q.filter(Activity.project_id == project_id)
    return q.order_by(Activity.created_at.desc(), Activity.id.desc()).offset(max(0, offset)).limit(limit).all()
