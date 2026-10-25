import os
import uuid
from datetime import datetime
from sqlalchemy import create_engine, event, Column, String, Float, DateTime, Integer, Text, text
from sqlalchemy.orm import declarative_base, sessionmaker

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "tracker.db")
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
    status = Column(String, default="В планах")
    stage = Column(String, default="—")
    priority = Column(String, default="Medium")
    estimate = Column(DateTime, nullable=True)
    token = Column(Float, default=0.0)
    token_reward = Column(Float, default=0.0)
    paint_notes = Column(Text, default="")  # paint mixing notes and recipes
    photo = Column(String, default="")      # file name of the project cover photo in photos/
    updated_at = Column(DateTime, default=datetime.now)
    completed_at = Column(DateTime, nullable=True)
    deleted_at = Column(DateTime, nullable=True)   # set = the project is in the trash


class Task(Base):
    __tablename__ = "tasks"
    id = Column(String, primary_key=True, default=gen_task_id)
    project_id = Column(String, nullable=False, index=True)
    task_name = Column(String, nullable=False)
    deadline = Column(DateTime, nullable=True)
    status = Column(String, default="Не начато")
    postponed = Column(Integer, default=0)
    completed_at = Column(DateTime, nullable=True)
    # set when the task was closed together with its project: the status it had before, so reopening the project
    # can bring it back; empty for tasks closed by hand
    closed_from = Column(String, default="")


class Reference(Base):
    """Paint references: original images (not recompressed) plus small previews."""
    __tablename__ = "project_refs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String, nullable=False, index=True)
    filename = Column(String, nullable=False)   # original in photos/refs/
    thumb = Column(String, default="")          # preview (JPEG) in photos/refs/
    position = Column(Integer, default=0)       # order in the card (drag to reorder); ties fall back to id
    created_at = Column(DateTime, default=datetime.now)


class ProgressPhoto(Base):
    """Progress (WIP) photo with the date and the project stage at upload time. Files live in photos/progress/."""
    __tablename__ = "progress_photos"
    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String, nullable=False, index=True)
    filename = Column(String, nullable=False)
    thumb = Column(String, default="")          # preview (JPEG); empty = the file itself is used
    stage = Column(String, default="—")         # project stage at upload time (canonical Russian value)
    note = Column(Text, default="")             # caption; from Telegram, the photo caption
    source = Column(String, default="web")      # web | telegram
    created_at = Column(DateTime, default=datetime.now)


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
    """Safe automatic migration of an existing SQLite database, without data loss."""
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        proj_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(projects)")).fetchall()]
        if "stage" not in proj_cols:
            conn.execute(text("ALTER TABLE projects ADD COLUMN stage VARCHAR DEFAULT '—'"))
        if "paint_notes" not in proj_cols:
            conn.execute(text("ALTER TABLE projects ADD COLUMN paint_notes TEXT DEFAULT ''"))
        if "photo" not in proj_cols:
            conn.execute(text("ALTER TABLE projects ADD COLUMN photo VARCHAR DEFAULT ''"))
        if "updated_at" not in proj_cols:
            conn.execute(text("ALTER TABLE projects ADD COLUMN updated_at DATETIME"))
            conn.execute(text("UPDATE projects SET updated_at = :now WHERE updated_at IS NULL"),
                         {"now": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        if "completed_at" not in proj_cols:
            conn.execute(text("ALTER TABLE projects ADD COLUMN completed_at DATETIME"))
        if "deleted_at" not in proj_cols:
            conn.execute(text("ALTER TABLE projects ADD COLUMN deleted_at DATETIME"))

        task_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(tasks)")).fetchall()]
        if "postponed" not in task_cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN postponed INTEGER DEFAULT 0"))
        if "completed_at" not in task_cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN completed_at DATETIME"))
        if "closed_from" not in task_cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN closed_from VARCHAR DEFAULT ''"))

        ref_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(project_refs)")).fetchall()]
        if "position" not in ref_cols:
            conn.execute(text("ALTER TABLE project_refs ADD COLUMN position INTEGER DEFAULT 0"))
            conn.execute(text("UPDATE project_refs SET position = id"))     # keep the old upload order

        # Tokens are whole numbers: older versions allowed fractions (step 0.1 in the form), round them once
        conn.execute(text("UPDATE projects SET token = ROUND(token) WHERE token IS NOT NULL AND token != ROUND(token)"))
        conn.execute(text("UPDATE projects SET token_reward = ROUND(token_reward) "
                          "WHERE token_reward IS NOT NULL AND token_reward != ROUND(token_reward)"))
        conn.execute(text("UPDATE adjustments SET amount = ROUND(amount) WHERE amount IS NOT NULL AND amount != ROUND(amount)"))

        # Indexes for the lookups done on every page load and bot update. create_all() does not add
        # indexes to tables that already exist, so older databases get them here.
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tasks_project_id ON tasks (project_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_projects_deleted_at ON projects (deleted_at)"))
        conn.commit()
