import time
import asyncio
import traceback
import httpx
from datetime import datetime
from hub.core.database import SessionLocal, ensure_schema
from hub.bot import tgclean
from hub.core import activity
from hub.web import ics
from hub.core.appconfig import load_config, config_value
from hub.core.pairing import is_chat_bound, ensure_pairing_code, check_pairing_code, bind_chat, format_pairing_code
from hub.core.i18n import tr
from hub.bot import tgproxy
from hub.jobs.scheduler import spawn_script, seconds_to_next_run

from hub.bot import bot_api
from hub.bot import bot_views
from hub.bot import bot_photos
from hub.bot import bot_tasks
from hub.bot.callbacks import handle_callback

# ==================== ACCESS: the bot answers only the linked chat ====================

PAIRING_MAX_FAILS = 5          # wrong pairing codes
PAIRING_WINDOW_SEC = 600       # within this window; after that, attempts are ignored
_failed_pairing = []           # times of failed attempts (in memory)
_ignored_logged = {}           # chat -> when it was last logged, so the log is not flooded


def is_owner(chat_id) -> bool:
    owner = config_value("TG_CHAT_ID")
    return owner not in (None, "", 0) and chat_id is not None and str(owner) == str(chat_id)


def _log_ignored(chat_id, what: str):
    now = time.time()
    if now - _ignored_logged.get(chat_id, 0) > 3600:
        _ignored_logged[chat_id] = now
        print(f"[BOT] Ignoring {what} from a foreign chat {chat_id} (the bot is linked to another chat)")


def pairing_locked() -> bool:
    cutoff = time.time() - PAIRING_WINDOW_SEC
    _failed_pairing[:] = [t for t in _failed_pairing if t > cutoff]
    return len(_failed_pairing) >= PAIRING_MAX_FAILS


# Registry of the bot features: /help, the welcome text and the Telegram command menu are built from it.
# When you add a command or a feature, list it here (and its texts in locales.json); the help updates itself.
COMMANDS = [                         # (command, description key)
    ("current_task", "bot.cmd_start_desc"),
    ("projects", "bot.cmd_all_desc"),
    ("task", "bot.cmd_task_desc"),
    ("general", "bot.cmd_general_desc"),
    ("photo", "bot.cmd_photo_desc"),
    ("myphotos", "bot.cmd_myphotos_desc"),
    ("calendar", "bot.cmd_calendar_desc"),
    ("summary", "bot.cmd_summary_desc"),
    ("weekly", "bot.cmd_weekly_desc"),
    ("monthly", "bot.cmd_monthly_desc"),
    ("stats", "bot.cmd_stats_desc"),
    ("clear", "bot.cmd_clear_desc"),
    ("help", "bot.cmd_help_desc"),
    ("test_notify", "bot.cmd_test_desc"),
]
FEATURES = [                         # features without a command: text keys
    "bot.feat_buttons",
    "bot.feat_share",
    "bot.feat_push",
    "bot.feat_postpone",
    "bot.feat_cleanup",
    "bot.feat_time",
]


PHOTO_HELP = [                       # the "Photos" section of /help: how to add and how to view them
    "bot.help_photo_send",
    "bot.help_photo_project",
    "bot.help_photo_note",
    "bot.help_photo_quality",
    "bot.help_photo_view_bot",
    "bot.help_photo_view_app",
    "bot.help_photo_refs",
]


OPTIONAL_COMMANDS = {"calendar": ics.enabled}     # shown only while their option is on


def active_commands() -> list:
    """COMMANDS without those whose option is off: for /help, the "/" menu and the help in Settings."""
    return [(name, key) for name, key in COMMANDS if OPTIONAL_COMMANDS.get(name, lambda: True)()]


def commands_text() -> str:
    return "\n".join(f"• /{name} — {tr(key)}" for name, key in active_commands())


def welcome_text() -> str:
    return (
        f"🤖 <b>{tr('bot.connected')}</b>\n\n"
        f"{tr('bot.commands')}:\n{commands_text()}"
    )


def help_text() -> str:
    features = "\n".join(f"• {tr(key)}" for key in FEATURES)
    photos = "\n".join(f"• {tr(key)}" for key in PHOTO_HELP)
    return (
        f"📖 <b>{tr('bot.help_title')}</b>\n\n"
        f"<b>{tr('bot.commands')}:</b>\n{commands_text()}\n\n"
        f"📷 <b>{tr('bot.help_photo_title')}:</b>\n{photos}\n\n"
        f"<b>{tr('bot.help_features_title')}:</b>\n{features}"
    )


def menu_commands() -> list:
    """List for setMyCommands (the "/" menu in Telegram): descriptions without extra notes, up to 256 characters."""
    return [{"command": name, "description": tr(key)[:256]} for name, key in active_commands()]


_menu_sent = {"value": None}     # the last menu Telegram accepted: resent when an option or the language changes


async def sync_menu_commands():
    menu = menu_commands()
    if menu == _menu_sent["value"]:
        return
    try:
        res = await bot_api.tg_request("POST", "setMyCommands", payload={"commands": menu}, timeout_sec=15.0)
        if res.get("ok"):
            _menu_sent["value"] = menu
    except Exception as e:
        print(f"⚠️ Could not update the command menu: {bot_api.safe_error(e)}")


# ==================== COMMANDS AND CALLBACKS ====================

async def cmd_summary():
    """/summary: the regular digest right now, exactly as the scheduled one (like "Send now" in the app).
    No "started" reply: the digest itself is the answer, so the chat stays clean."""
    spawn_script("hub.jobs.notify", ["--now", "--source=bot"])


async def cmd_weekly():
    """/weekly: the Friday digest right now, on any day (for checking the weekly blocks)."""
    spawn_script("hub.jobs.notify", ["--weekly", "--source=bot"])


async def cmd_monthly():
    """/monthly: the report for the previous month right now."""
    spawn_script("hub.jobs.notify", ["--monthly", "--source=bot"])


async def cmd_test_notify(chat_id: int):
    spawn_script("hub.jobs.notify", ["--test"])
    await bot_api.send_message(chat_id, f"🧪 {tr('bot.test_started')}...")


def _build_calendar():
    """(file bytes, number of deadlines) for /calendar."""
    db = SessionLocal()
    try:
        return ics.build(db), len(ics.collect(db))
    finally:
        db.close()


async def send_calendar(chat_id: int):
    """/calendar: deadlines as a .ics file; opened on a phone, it adds them to the calendar."""
    data, count = await asyncio.to_thread(_build_calendar)
    if not count:
        await bot_api.send_message(chat_id, tr("bot.calendar_empty"))
        return
    try:
        res = await bot_api.tg_upload("sendDocument",
                              {"chat_id": str(chat_id), "caption": tr("bot.calendar_caption", n=count), "parse_mode": "HTML"},
                              {"document": ("deadlines.ics", data, "text/calendar")})
        if not res.get("ok"):
            print(f"[BOT] sendDocument (calendar): {res.get('description')}")
            await bot_api.send_message(chat_id, tr("bot.calendar_failed"))
    except Exception as e:
        print(f"[BOT] sendDocument (calendar): {bot_api.safe_error(e)}")
        await bot_api.send_message(chat_id, tr("bot.calendar_failed"))



# ==================== CHAT CLEANUP (/clear and digests, see tgclean.py) ====================

CLEANUP_EVERY_SEC = 600                # digest cleanup check; keep_46h needs it well inside the 2-hour window
_cleanup_state = {"last": 0.0}


async def start_clear(chat_id: int):
    """/clear: asks first. Only messages younger than 48 hours that the bot knows about can be removed."""
    plan = await asyncio.to_thread(tgclean.clear_plan, chat_id)
    n = len(plan["all"])
    if n <= 1:                                           # only the /clear message itself
        await bot_api.delete_messages(chat_id, plan["all"])
        await bot_api.send_self_destructing(chat_id, f"🧹 {tr('bot.clear_nothing')}")
        return
    rows = [[{"text": f"🗑 {tr('bot.clear_all', n=n)}", "callback_data": "clr:all"}]]
    if plan["has_digest"]:
        rows.append([{"text": f"📌 {tr('bot.clear_keep_last', n=len(plan['keep_last']))}", "callback_data": "clr:keep"}])
    rows.append([{"text": f"✖ {tr('common.cancel')}", "callback_data": "clr:no"}])
    await bot_api.send_message(chat_id, f"🧹 <b>{tr('bot.clear_title')}</b>\n{tr('bot.clear_question', n=n)}\n"
                                f"<i>{tr('bot.clear_limit_note')}</i>", {"inline_keyboard": rows})


async def cleanup_digests():
    """Digest cleanup by the TG_CLEANUP mode (runs from the polling loop every few minutes)."""
    cfg = load_config()
    chat_id = cfg.get("TG_CHAT_ID")
    try:
        if chat_id:
            ids = await asyncio.to_thread(tgclean.digest_candidates, chat_id)
            if ids:
                await bot_api.delete_messages(chat_id, ids)
        await asyncio.to_thread(tgclean.prune)
    except Exception as e:
        print(f"[BOT] digest cleanup failed: {bot_api.safe_error(e)}")



async def handle_foreign_message(chat: dict, text: str):
    """A message from someone else. A linked bot stays silent; an unlinked one accepts only "/start CODE"."""
    chat_id = chat.get("id")
    if is_chat_bound():
        _log_ignored(chat_id, "a message")
        return
    if not text.startswith("/start"):
        return
    parts = text.split(maxsplit=1)
    payload = parts[1] if len(parts) > 1 else ""
    if pairing_locked():
        _log_ignored(chat_id, "a pairing attempt (too many wrong codes)")
        return
    if chat.get("type") != "private":
        await bot_api.send_message(chat_id, tr("bot.private_only"))
        return
    if payload and check_pairing_code(payload):
        bind_chat(chat_id)
        _failed_pairing.clear()
        print(f"[BOT] The bot is now linked to chat {chat_id}")
        await bot_api.send_message(chat_id, f"✅ {tr('bot.chat_linked')}")
        await bot_api.send_message(chat_id, welcome_text())
        return
    if payload:
        _failed_pairing.append(time.time())
    await bot_api.send_message(chat_id, tr("bot.not_linked"))


async def process_update(item: dict):
    """One Telegram update: a message or a button press. Journal events from here are marked as made in the bot."""
    with activity.source("bot"):
        await _process_update(item)


async def _process_update(item: dict):
    if "callback_query" in item:
        cb = item["callback_query"]
        cb_chat_id = ((cb.get("message") or {}).get("chat") or {}).get("id")
        if not is_owner(cb_chat_id):
            _log_ignored(cb_chat_id, "a button press")
            return
        await handle_callback(cb)
        return

    msg = item.get("message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    has_image = bool(msg.get("photo") or (msg.get("document") or {}).get("mime_type", "").startswith("image/"))
    if not chat_id or not (text or has_image):
        return

    if not is_owner(chat_id):
        await handle_foreign_message(chat, text)
        return
    await asyncio.to_thread(tgclean.record, chat_id, [msg.get("message_id")], "user")

    if has_image:
        await bot_photos.handle_photo_message(chat_id, msg)
        return

    if text.startswith("/"):
        bot_tasks._task_drafts.pop(chat_id, None)           # any command cancels a task draft in progress
        bot_tasks._time_drafts.pop(chat_id, None)           # ... and a time being typed
    elif await bot_tasks.handle_time_text(chat_id, text):
        return
    elif await bot_tasks.handle_task_text(chat_id, text):
        return

    if text.startswith("/start"):
        await bot_api.send_message(chat_id, welcome_text())
    elif text.startswith("/current_task"):
        view_text, markup = await asyncio.to_thread(bot_views.build_current_focus_view)
        await bot_api.send_message(chat_id, view_text, markup)
    elif text.startswith("/help"):
        await bot_api.send_message(chat_id, help_text())
    elif text.startswith("/task"):
        rest = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ""
        await bot_tasks.start_task_constructor(chat_id, None, rest[:bot_tasks.MAX_TASK_NAME] if rest else None)
    elif text.startswith("/general"):
        view_text, markup = await asyncio.to_thread(bot_views.build_general_view)
        await bot_api.send_message(chat_id, view_text, markup)
    elif text.startswith("/projects"):
        view_text, markup = await asyncio.to_thread(bot_views.build_all_projects_view)
        await bot_api.send_message(chat_id, view_text, markup)
    elif text.startswith("/myphotos"):
        view_text, markup = await asyncio.to_thread(bot_photos.build_myphotos_projects)
        await bot_api.send_message(chat_id, view_text, markup)
    elif text.startswith("/photo"):
        if not await bot_photos.send_photo_view(chat_id, "*", -1):        # /photo: the latest photo first
            await bot_api.send_message(chat_id, tr("bot.photo_none"))
    elif text.startswith("/calendar"):
        if ics.enabled():
            await send_calendar(chat_id)
        else:
            await bot_api.send_message(chat_id, tr("bot.calendar_off"))
    elif text.startswith("/clear"):
        await start_clear(chat_id)
    elif text.startswith("/summary"):
        await cmd_summary()
    elif text.startswith("/weekly"):
        await cmd_weekly()
    elif text.startswith("/monthly"):
        await cmd_monthly()
    elif text.startswith("/stats"):
        await bot_api.send_message(chat_id, await asyncio.to_thread(bot_views.build_current_month_stats))
    elif text.startswith("/test_notify"):
        await cmd_test_notify(chat_id)


SESSION_MAX_SEC = 3 * 3600            # planned reconnect: a fresh connection every 3 hours
PRE_PUSH_RECONNECT_SEC = 600          # ... and about 10 minutes before scheduled notifications
RETRY_STEPS_SEC = (3, 10, 30, 60)     # pause after consecutive network errors: grows, then stays at the last step


class Backoff:
    """Pauses between failed polls: 3 → 10 → 30 → 60 s (then 60 s each time), back to 3 s after a success.
    The log gets one line when the connection is lost (with the reason) and one when it is back, with how long it
    took and how many attempts failed, instead of a line every few seconds while a proxy is down."""

    def __init__(self, steps=RETRY_STEPS_SEC, clock=time.monotonic):
        self.steps = steps
        self.clock = clock
        self.failures = 0
        self.since = None

    def fail(self, reason: str) -> float:
        if self.failures == 0:
            self.since = self.clock()
            print(f"⚠️ [{datetime.now().strftime('%H:%M:%S')}] No connection to Telegram ({tgproxy.describe()}): "
                  f"{reason}. Retrying with growing pauses ({', '.join(str(s) for s in self.steps)} s)...")
        delay = self.steps[min(self.failures, len(self.steps) - 1)]
        self.failures += 1
        return delay

    def ok(self) -> None:
        if self.failures:
            took = int(self.clock() - self.since) if self.since is not None else 0
            print(f"✅ [{datetime.now().strftime('%H:%M:%S')}] Connection to Telegram is back "
                  f"after {took} s ({self.failures} failed attempt(s))")
        self.failures = 0
        self.since = None


async def start_bot_polling():
    ensure_schema()
    if not bot_api.BOT_TOKEN:
        print("⚠️ TELEGRAM_BOT_TOKEN is not set in .env!")
        return
    code = ensure_pairing_code()
    if code:
        print(f"🔗 The bot is not linked to a chat. Send it in a private chat: /start {format_pairing_code(code)} "
              f"(the code is also shown in the app: Settings → Telegram)")

    # The offset survives session restarts: getUpdates confirms the previous batch only through the next offset,
    # so starting again from 0 would deliver the last processed updates twice (a "+2d" press applied twice).
    offset = 0
    backoff = Backoff()
    while True:  # OUTER LOOP: a new session (fresh connections) every few hours
        bot_api.reset_http_client()
        try:
            await bot_api.tg_request("POST", "deleteWebhook", payload={"drop_pending_updates": False}, timeout_sec=15.0)
            print(f"✅ [{datetime.now().strftime('%H:%M:%S')}] Telegram bot started a new session ({tgproxy.describe()})")
            _menu_sent["value"] = None
            await sync_menu_commands()
        except asyncio.CancelledError:
            return
        except Exception as e:
            print(f"⚠️ Could not connect to Telegram at session start ({tgproxy.describe()}): {bot_api.safe_error(e)}")

        session_start = datetime.now()

        while True:  # INNER LOOP: regular long polling
            now = datetime.now()
            age = (now - session_start).total_seconds()

            # 1. Planned reconnect every 3 hours
            if age > SESSION_MAX_SEC:
                print(f"🔄 [{now.strftime('%H:%M:%S')}] Planned reconnect (3 hours passed)...")
                break

            # 2. A fresh session about 10 minutes before scheduled notifications
            to_next = seconds_to_next_run()
            if to_next is not None and 0 < to_next <= PRE_PUSH_RECONNECT_SEC and age > PRE_PUSH_RECONNECT_SEC + 60:
                print(f"🔄 [{now.strftime('%H:%M:%S')}] Planned reconnect before scheduled notifications...")
                break

            await sync_menu_commands()          # cheap when nothing changed: compares with what was sent
            if time.time() - _cleanup_state["last"] >= CLEANUP_EVERY_SEC:
                _cleanup_state["last"] = time.time()
                await cleanup_digests()
            try:
                data = await bot_api.tg_request("GET", "getUpdates", params={"offset": offset, "timeout": 10}, timeout_sec=20.0)
            except asyncio.CancelledError:
                return  # clean stop when the service shuts down
            except httpx.ReadTimeout:
                continue
            except (httpx.TransportError, ValueError) as e:     # no connection, proxy refused, broken reply
                await asyncio.sleep(backoff.fail(f"{e.__class__.__name__}: {bot_api.safe_error(e)}"[:200]))
                continue
            except Exception as e:
                print(f"[BOT POLLING ERROR]: {bot_api.safe_error(e)}")
                await asyncio.sleep(backoff.fail(e.__class__.__name__))
                continue

            if not data.get("ok"):
                await asyncio.sleep(backoff.fail(f"Telegram: {str(data.get('description') or data)[:150]}"))
                continue
            backoff.ok()

            for item in data.get("result", []):
                offset = item["update_id"] + 1
                try:
                    await process_update(item)
                except asyncio.CancelledError:
                    return
                except Exception as e:                    # one bad update must not stop the rest of the batch
                    print(f"[BOT UPDATE ERROR]: {bot_api.safe_error(e)}")
                    traceback.print_exc()
