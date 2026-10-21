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
    """Режим WAL: читатели не блокируют запись и наоборот (веб, бот и скрипты notify/backup работают с одной базой).
    busy_timeout — подождать до 5 с, если база занята, вместо ошибки "database is locked".
    Режим WAL хранится в самом файле базы: старая база переключается при первом подключении."""
    cur = dbapi_connection.cursor()
    try:
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
    except Exception as e:      # например, файловая система без поддержки WAL: работаем в обычном режиме
        print(f"[DB] не удалось включить WAL: {e!r}")
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
    paint_notes = Column(Text, default="")  # Блокнот для красок и рецептов
    photo = Column(String, default="")      # Имя файла фото проекта в папке photos/
    updated_at = Column(DateTime, default=datetime.now)
    completed_at = Column(DateTime, nullable=True)
    deleted_at = Column(DateTime, nullable=True)   # не пусто — проект в корзине

class Task(Base):
    __tablename__ = "tasks"
    id = Column(String, primary_key=True, default=gen_task_id)
    project_id = Column(String, nullable=False)
    task_name = Column(String, nullable=False)
    deadline = Column(DateTime, nullable=True)
    status = Column(String, default="Не начато")
    postponed = Column(Integer, default=0)
    completed_at = Column(DateTime, nullable=True)

class Reference(Base):
    """Референсы для покраса: оригиналы картинок (без сжатия) + мини-превью."""
    __tablename__ = "project_refs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String, nullable=False, index=True)
    filename = Column(String, nullable=False)   # оригинал в photos/refs/
    thumb = Column(String, default="")          # превью (JPEG) в photos/refs/
    created_at = Column(DateTime, default=datetime.now)

class ProgressPhoto(Base):
    """Фото прогресса (WIP): с датой и стадией проекта на момент загрузки. Файлы лежат в photos/progress/."""
    __tablename__ = "progress_photos"
    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String, nullable=False, index=True)
    filename = Column(String, nullable=False)
    thumb = Column(String, default="")          # превью (JPEG); пусто — используется сам файл
    stage = Column(String, default="—")         # стадия проекта в момент загрузки (хранится канонически, по-русски)
    note = Column(Text, default="")             # подпись; из Telegram — подпись к фото
    source = Column(String, default="web")      # web | telegram
    created_at = Column(DateTime, default=datetime.now)

class Adjustment(Base):
    """Корректировки баланса токенов (например, при удалении проекта)."""
    __tablename__ = "adjustments"
    id = Column(Integer, primary_key=True, autoincrement=True)
    amount = Column(Float, default=0.0)
    reason = Column(String, default="")
    created_at = Column(DateTime, default=datetime.now)

def ensure_schema():
    """Безопасная авто-миграция существующей БД SQLite без потери данных"""
    Base.metadata.create_all(bind=engine)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
            conn.execute(text(f"UPDATE projects SET updated_at = '{now_str}' WHERE updated_at IS NULL"))
        if "completed_at" not in proj_cols:
            conn.execute(text("ALTER TABLE projects ADD COLUMN completed_at DATETIME"))
        if "deleted_at" not in proj_cols:
            conn.execute(text("ALTER TABLE projects ADD COLUMN deleted_at DATETIME"))

        task_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(tasks)")).fetchall()]
        if "postponed" not in task_cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN postponed INTEGER DEFAULT 0"))
        if "completed_at" not in task_cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN completed_at DATETIME"))
        conn.commit()