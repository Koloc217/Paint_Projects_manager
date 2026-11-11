"""What runs next to the web app: the bot, the scheduler and the housekeeping loop."""
import asyncio
import contextlib
from datetime import datetime

from fastapi import FastAPI

from hub.bot import tgclean
from hub.bot.bot import start_bot_polling
from hub.core import activity, archive, integrity, logs
from hub.core.common import load_config
from hub.core.database import SessionLocal, engine
from hub.core.i18n import get_lang, normalize_lang
from hub.core.trash import purge_expired_trash
from hub.jobs.scheduler import scheduler_loop

_last_optimize = {"day": None}


def optimize_db() -> None:
    """Once a day: PRAGMA optimize refreshes SQLite's query statistics where they went stale, so lookups keep using
    the right indexes as the tables grow (cheap: SQLite only re-analyzes what needs it)."""
    today = datetime.now().date()
    if _last_optimize["day"] == today:
        return
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA optimize")
    _last_optimize["day"] = today


def cleanup_orphans() -> dict:
    db = SessionLocal()
    try:
        return integrity.cleanup_orphan_files(db)
    finally:
        db.close()


async def trash_loop():
    """Every few hours (and right at start-up) erases projects whose time in the trash is over and moves photo
    files no row points to into the quarantine folder (see integrity.py)."""
    while True:
        try:
            n = await asyncio.to_thread(purge_expired_trash)
            if n:
                print(f"🗑 Erased from the trash: {n} project(s)")
        except Exception as e:
            print(f"[TRASH ERROR] {e!r}")
        try:
            # rows of bot messages older than 48 h: Telegram no longer lets the bot delete them, so they are
            # useless. The bot prunes too, but only while it is connected; this keeps the table small either way.
            await asyncio.to_thread(tgclean.prune)
        except Exception as e:
            print(f"[TGCLEAN ERROR] {e!r}")
        try:
            res = await asyncio.to_thread(cleanup_orphans)
            if res["moved"] or res["erased_days"]:
                print(f"🧹 Photos without a project: {res['moved']} moved to orphans/, "
                      f"{res['erased_days']} old quarantine day(s) erased")
        except Exception as e:
            print(f"[ORPHANS ERROR] {e!r}")
        try:
            await asyncio.to_thread(optimize_db)
        except Exception as e:
            print(f"[DB] optimize failed: {e!r}")
        await asyncio.sleep(6 * 3600)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    logs.setup_app_log()
    lang = load_config().get("LANGUAGE")           # also reads config.json: invalid values are reported in the log
    if normalize_lang(lang) is None:
        print(f'[CONFIG] config.json: LANGUAGE = "{lang}" is not in locales.json, using {get_lang()}')
    print("🚀 Starting the bot and the scheduler...")
    bot_task = asyncio.create_task(start_bot_polling())
    scheduler_task = asyncio.create_task(scheduler_loop())
    trash_task = asyncio.create_task(trash_loop())
    with contextlib.suppress(Exception):
        archive.cleanup_temp()          # leftovers of an archive download or restore cut off by a restart
    try:
        n = await asyncio.to_thread(activity.backfill_once)
        if n:
            print(f"📜 Journal: {n} past event(s) restored from completion dates")
    except Exception as e:
        print(f"[JOURNAL] backfill failed: {e!r}")
    yield
    bot_task.cancel()
    scheduler_task.cancel()
    trash_task.cancel()
