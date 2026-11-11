import os
import uuid
from datetime import datetime
from sqlalchemy import create_engine, event, Column, String, Float, DateTime, Integer, Text, ForeignKey, text
from sqlalchemy.orm import declarative_base, sessionmaker

from hub.paths import BASE_DIR, DATA_DIR      # noqa: F401  (re-exported: other modules import them from here)

os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "tracker.db")
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):
    """WAL mode: readers do not block the writer and vice versa (the web app, the bot and the notify/backup
    scripts share one database). busy_timeout waits up to 5 s for a busy database instead of failing with
    "database is locked". WAL is stored in the database file itself, so an old database switches on first connect."""
    cur = dbapi_connection.cursor()
    try:
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        # Foreign keys are off by default in SQLite, per connection. On: a task, reference or progress photo
        # cannot point to a project that does not exist, and erasing a project removes their rows too.
        cur.execute("PRAGMA foreign_keys=ON")
    except Exception as e:      # e.g. a file system without WAL support: keep the default journal mode
        print(f"[DB] could not enable WAL: {e!r}")
    finally:
        cur.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def gen_proj_id():
    return f"PRJ-{uuid.uuid4().hex[:6].upper()}"


def gen_task_id():
    return f"TSK-{uuid.uuid4().hex[:6].upper()}"


class Project(Base):
    __tablename__ = "projects"
    id = Column(String, primary_key=True, default=gen_proj_id)
    name = Column(String, nullable=False)
    status = Column(String, default="planned")    # code, see common.PROJECT_STATUSES
    stage = Column(String, default="none")       # code, see common.STAGES
    priority = Column(String, default="Medium")
    estimate = Column(DateTime, nullable=True)
    token = Column(Float, default=0.0)
    token_reward = Column(Float, default=0.0)
    paint_notes = Column(Text, default="")  # paint mixing notes and recipes
    photo = Column(String, default="")      # file name of the project cover photo in photos/
    updated_at = Column(DateTime, default=datetime.now)
    completed_at = Column(DateTime, nullable=True)
    deleted_at = Column(DateTime, nullable=True)   # set = the project is in the trash
    extra_spent_min = Column(Integer, default=0)   # time spent on the project outside its tasks, minutes


# Rows that belong to a project. ON DELETE CASCADE needs PRAGMA foreign_keys=ON (see _sqlite_pragmas);
# the app still deletes them explicitly (purge_project), the constraint is the safety net.
def _project_fk():
    return ForeignKey("projects.id", ondelete="CASCADE")


class Task(Base):
    __tablename__ = "tasks"
    id = Column(String, primary_key=True, default=gen_task_id)
    # empty (NULL) = a general task outside any project (shelf tidying, airbrush maintenance...): no stage,
    # no tokens, it never goes to the trash with a project
    project_id = Column(String, _project_fk(), nullable=True, index=True)
    task_name = Column(String, nullable=False)
    deadline = Column(DateTime, nullable=True)
    status = Column(String, default="not_started")   # code, see common.TASK_STATUSES
    postponed = Column(Integer, default=0)
    completed_at = Column(DateTime, nullable=True)
    # set when the task was closed together with its project: the status it had before, so reopening the project
    # can bring it back; empty for tasks closed by hand
    closed_from = Column(String, default="")
    spent_min = Column(Integer, nullable=True)       # time spent, minutes; empty = not given (it is optional)
    spent_stage = Column(String, default="")         # project stage when the time was given (code), for the breakdown


class Reference(Base):
    """Paint references: original images (not recompressed) plus small previews."""
    __tablename__ = "project_refs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String, _project_fk(), nullable=False, index=True)
    filename = Column(String, nullable=False)   # original in photos/refs/
    thumb = Column(String, default="")          # preview (JPEG) in photos/refs/
    position = Column(Integer, default=0)       # order in the card (drag to reorder); ties fall back to id
    created_at = Column(DateTime, default=datetime.now)


class ProgressPhoto(Base):
    """Progress (WIP) photo with the date and the project stage at upload time. Files live in photos/progress/."""
    __tablename__ = "progress_photos"
    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String, _project_fk(), nullable=False, index=True)
    filename = Column(String, nullable=False)
    thumb = Column(String, default="")          # preview (JPEG); empty = the file itself is used
    stage = Column(String, default="none")      # project stage at upload time (code)
    note = Column(Text, default="")             # caption; from Telegram, the photo caption
    source = Column(String, default="web")      # web | telegram
    created_at = Column(DateTime, default=datetime.now)


class Activity(Base):
    """Project journal: what happened and when (status, stage, tasks, deadlines). Written by activity.py.
    Names are copied at the time of the event, so the journal stays readable after renames or erasing."""
    __tablename__ = "activity"
    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.now, index=True)
    project_id = Column(String, index=True)
    project_name = Column(String, default="")
    task_id = Column(String, nullable=True)
    task_name = Column(String, default="")
    kind = Column(String, nullable=False)        # see activity.KINDS
    old = Column(String, default="")
    new = Column(String, default="")
    source = Column(String, default="web")       # web | bot | history (restored from dates)


class TgMessage(Base):
    """Messages in the bot chat that the bot may delete later (/clear, digest cleanup, see tgclean.py).
    The Bot API cannot list a chat's history, so the bot remembers what it sent and received."""
    __tablename__ = "tg_messages"
    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(String, nullable=False)
    message_id = Column(Integer, nullable=False)
    kind = Column(String, default="bot")         # bot | user | digest | report
    created_at = Column(DateTime, default=datetime.now, index=True)


class Adjustment(Base):
    """Token balance adjustments (for example, when a project is deleted)."""
    __tablename__ = "adjustments"
    id = Column(Integer, primary_key=True, autoincrement=True)
    amount = Column(Float, default=0.0)
    reason = Column(String, default="")
    created_at = Column(DateTime, default=datetime.now)


def ensure_schema():
    """Creates missing tables and runs the pending versioned migrations (migrations.py), then the cheap idempotent
    clean-up of old status/stage values. Safe to call from every entry point (app, bot, scripts) on every start."""
    from hub.core import migrations
    had_data = os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) > 0
    Base.metadata.create_all(bind=engine)
    migrations.migrate(DB_PATH, had_data=had_data)
    engine.dispose()                  # pooled connections must see the rebuilt tables
    with engine.connect() as conn:
        # Not a versioned step on purpose: an old client (a bot button sent before the switch to codes, an older
        # script) may still write a Russian value; this pass fixes such rows on the next start.
        migrate_value_codes(conn)
        conn.commit()


# Columns that hold a status or a stage. activity.old/new hold them only for these event kinds.
_CODE_COLUMNS = (("projects", "status"), ("projects", "stage"), ("tasks", "status"),
                 ("tasks", "closed_from"), ("progress_photos", "stage"))
_ACTIVITY_KINDS = ("project_created", "project_status", "project_stage", "task_status")


def migrate_value_codes(conn) -> None:
    """Statuses and stages: old Russian values -> technical codes (common.LEGACY_VALUES).
    Idempotent: only rows that still hold an old value are touched, so it is cheap on every start."""
    from hub.core.common import LEGACY_VALUES    # common imports this module, so the import stays local
    kinds = ", ".join(f"'{k}'" for k in _ACTIVITY_KINDS)
    for old, new in LEGACY_VALUES.items():
        params = {"old": old, "new": new}
        for table, column in _CODE_COLUMNS:
            conn.execute(text(f"UPDATE {table} SET {column} = :new WHERE {column} = :old"), params)
        for column in ("old", "new"):
            conn.execute(text(f"UPDATE activity SET {column} = :new WHERE {column} = :old AND kind IN ({kinds})"),
                         params)
    # an empty stage meant "no stage"
    conn.execute(text("UPDATE projects SET stage = 'none' WHERE stage IS NULL OR stage = ''"))
    conn.execute(text("UPDATE progress_photos SET stage = 'none' WHERE stage IS NULL OR stage = ''"))
