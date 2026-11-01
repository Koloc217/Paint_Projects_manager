"""Versioned database migrations.

The version of a database is kept in SQLite itself (PRAGMA user_version). Each step below brings the schema from
version N-1 to N and runs once; the version is raised in the same transaction as the step, so a step that fails
leaves the database exactly as it was and is retried on the next start.

    0  a database from before versioned migrations (or a new, empty file)
    1  baseline: the column additions that ensure_schema() used to check on every start
    2  time spent: tasks.spent_min / tasks.spent_stage, projects.extra_spent_min
    3  foreign keys: tasks, references and progress photos belong to a project (ON DELETE CASCADE)

Rules for new steps:
- add a function and append it to STEPS; never change or reorder a released step;
- a step must be safe on a database where create_all() already made the newest tables (a new install): check
  before adding a column or rebuilding a table;
- no data loss: rename and copy, never drop data the app still shows.

Before the first pending step runs on a database that already has data, a copy is saved to
backups/tracker-before-v<N>-<date>.db (the sqlite3 backup API: consistent even while the app is running).
The newest KEEP_COPIES of these copies are kept.

Works with plain sqlite3 on any database file, so tools/check_backup.py migrates a copy of a backup the same way.
"""
import glob
import os
import sqlite3
from datetime import datetime

KEEP_COPIES = 5


def _columns(conn, table: str) -> list:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _has_table(conn, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _add_column(conn, table: str, column: str, ddl: str) -> None:
    if _has_table(conn, table) and column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


# ---------------------------------------------------------------- steps

def step_1_baseline(conn) -> None:
    """Everything the old ensure_schema() added to databases made by earlier versions."""
    _add_column(conn, "projects", "stage", "VARCHAR DEFAULT 'none'")
    _add_column(conn, "projects", "paint_notes", "TEXT DEFAULT ''")
    _add_column(conn, "projects", "photo", "VARCHAR DEFAULT ''")
    if _has_table(conn, "projects") and "updated_at" not in _columns(conn, "projects"):
        conn.execute("ALTER TABLE projects ADD COLUMN updated_at DATETIME")
        conn.execute("UPDATE projects SET updated_at = ? WHERE updated_at IS NULL",
                     (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
    _add_column(conn, "projects", "completed_at", "DATETIME")
    _add_column(conn, "projects", "deleted_at", "DATETIME")

    _add_column(conn, "tasks", "postponed", "INTEGER DEFAULT 0")
    _add_column(conn, "tasks", "completed_at", "DATETIME")
    _add_column(conn, "tasks", "closed_from", "VARCHAR DEFAULT ''")

    if _has_table(conn, "project_refs") and "position" not in _columns(conn, "project_refs"):
        conn.execute("ALTER TABLE project_refs ADD COLUMN position INTEGER DEFAULT 0")
        conn.execute("UPDATE project_refs SET position = id")     # keep the old upload order

    # Tokens are whole numbers: older versions allowed fractions (step 0.1 in the form)
    if not (_has_table(conn, "projects") and _has_table(conn, "adjustments")):
        return                            # not a Project Hub database: create_all() must run first
    conn.execute("UPDATE projects SET token = ROUND(token) WHERE token IS NOT NULL AND token != ROUND(token)")
    conn.execute("UPDATE projects SET token_reward = ROUND(token_reward) "
                 "WHERE token_reward IS NOT NULL AND token_reward != ROUND(token_reward)")
    conn.execute("UPDATE adjustments SET amount = ROUND(amount) WHERE amount IS NOT NULL AND amount != ROUND(amount)")

    # create_all() does not add indexes to tables that already exist
    conn.execute("CREATE INDEX IF NOT EXISTS ix_tasks_project_id ON tasks (project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_projects_deleted_at ON projects (deleted_at)")


def step_2_time_spent(conn) -> None:
    """Optional time spent: per task (minutes and the project stage at that moment) and an extra amount per project."""
    _add_column(conn, "tasks", "spent_min", "INTEGER")
    _add_column(conn, "tasks", "spent_stage", "VARCHAR DEFAULT ''")
    _add_column(conn, "projects", "extra_spent_min", "INTEGER DEFAULT 0")


# Tables that get a foreign key to projects.id in step 3, with their model (for the new CREATE TABLE).
_FK_TABLES = ("tasks", "project_refs", "progress_photos")


def step_3_foreign_keys(conn) -> None:
    """tasks, project_refs and progress_photos get project_id REFERENCES projects(id) ON DELETE CASCADE.

    SQLite cannot add a constraint to an existing table, so each table is rebuilt: indexes dropped, the old table
    renamed, the new one created from the model (same DDL as a new install), the rows copied, the old table dropped.
    Rows of projects that no longer exist are not copied: nothing could show them, and the constraint would reject
    them. Their photo files become unreferenced and are moved aside by integrity.cleanup_orphan_files().
    Runs with foreign_keys=OFF (see migrate()), as the SQLite manual prescribes for table rebuilds."""
    from sqlalchemy.dialects import sqlite as sqlite_dialect
    from sqlalchemy.schema import CreateIndex, CreateTable
    import database

    dialect = sqlite_dialect.dialect()
    for name in _FK_TABLES:
        if not _has_table(conn, name):
            continue
        if any(r[2] == "projects" for r in conn.execute(f"PRAGMA foreign_key_list({name})").fetchall()):
            continue                                      # a new install: create_all() already made it right
        table = database.Base.metadata.tables[name]
        old_cols = set(_columns(conn, name))
        for idx in conn.execute(f"PRAGMA index_list({name})").fetchall():
            if idx[3] == "c":                             # made by CREATE INDEX (not a primary key / unique)
                conn.execute(f'DROP INDEX IF EXISTS "{idx[1]}"')
        conn.execute(f"ALTER TABLE {name} RENAME TO {name}__old")
        conn.execute(str(CreateTable(table).compile(dialect=dialect)))
        for index in table.indexes:
            conn.execute(str(CreateIndex(index).compile(dialect=dialect)))
        cols = ", ".join(c.name for c in table.columns if c.name in old_cols)
        conn.execute(f"INSERT INTO {name} ({cols}) SELECT {cols} FROM {name}__old "
                     f"WHERE project_id IN (SELECT id FROM projects)")
        conn.execute(f"DROP TABLE {name}__old")
    bad = conn.execute("PRAGMA foreign_key_check").fetchall()
    if bad:
        raise RuntimeError(f"foreign key check failed after the rebuild: {bad[:5]}")


STEPS = [step_1_baseline, step_2_time_spent, step_3_foreign_keys]
LATEST = len(STEPS)


# ---------------------------------------------------------------- runner

def version(conn) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _save_copy(db_path: str, from_version: int) -> str:
    """Consistent copy of the database before migrating it. Returns the path, or "" if it could not be made."""
    folder = os.path.join(os.path.dirname(db_path), "backups")
    try:
        os.makedirs(folder, exist_ok=True)
        dst_path = os.path.join(folder, f"tracker-before-v{from_version + 1}-{datetime.now():%Y%m%d-%H%M%S}.db")
        src, dst = sqlite3.connect(db_path), sqlite3.connect(dst_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        for old in sorted(glob.glob(os.path.join(folder, "tracker-before-v*.db")), reverse=True)[KEEP_COPIES:]:
            try:
                os.remove(old)
            except OSError:
                pass
        return dst_path
    except (OSError, sqlite3.Error) as e:
        print(f"[DB] could not save a copy before the migration: {e!r}")
        return ""


def migrate(db_path: str, had_data: bool = True, log=print) -> int:
    """Runs the pending steps on the database file. Returns the version it ends at.

    had_data: the database existed before this start (False for a brand-new file: no copy is saved)."""
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)   # explicit BEGIN / COMMIT below
    try:
        current = version(conn)
        if current > LATEST:
            log(f"[DB] the database is at version {current}, this code knows up to {LATEST} "
                f"(code rolled back?). Starting anyway; newer columns are ignored.")
            return current
        if current == LATEST:
            return current
        if had_data:
            copy = _save_copy(db_path, current)
            if copy:
                log(f"[DB] copy before the migration: {copy}")
        conn.execute("PRAGMA foreign_keys=OFF")            # cannot change inside a transaction
        for number in range(current + 1, LATEST + 1):
            conn.execute("BEGIN IMMEDIATE")                # one writer: the app and a script may start together
            try:
                if version(conn) >= number:                # done by another process while we waited
                    conn.execute("ROLLBACK")
                    continue
                STEPS[number - 1](conn)
                conn.execute(f"PRAGMA user_version = {number}")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            log(f"[DB] migration {number}: {STEPS[number - 1].__name__} done")
        return version(conn)
    finally:
        conn.close()
