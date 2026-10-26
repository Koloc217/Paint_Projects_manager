import os
import time
import asyncio
import json
import re
import threading
import traceback
import uuid
import httpx
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
from database import SessionLocal, Project, Task, Reference, ensure_schema
import progress
import tgclean
import activity
import ics
from common import (
    BASE_DIR, esc, to_tokens, clip_lines, short_text, TG_ALERT_LIMIT, MAX_TASK_NAME_LEN, load_config, config_value, compute_balance, fmt_date, live_projects, live_tasks,
    is_chat_bound, ensure_pairing_code, check_pairing_code, bind_chat, format_pairing_code,
    is_done as status_done, STAGES, NO_STAGE, normalize_value, STATUS_IN_WORK, STATUS_ON_HOLD, STATUS_PLANNED, STATUS_FINISHED,
    TASK_NOT_STARTED, TASK_IN_PROGRESS,
)
from i18n import tr, tv
import tgproxy
from scheduler import spawn_script, seconds_to_next_run

load_dotenv(os.path.join(BASE_DIR, ".env"))

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

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
    ("photo", "bot.cmd_photo_desc"),
    ("myphotos", "bot.cmd_myphotos_desc"),
    ("calendar", "bot.cmd_calendar_desc"),
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
        res = await tg_request("POST", "setMyCommands", payload={"commands": menu}, timeout_sec=15.0)
        if res.get("ok"):
            _menu_sent["value"] = menu
    except Exception as e:
        print(f"⚠️ Could not update the command menu: {safe_error(e)}")

# ==================== NETWORK LAYER (direct or through a proxy, see tgproxy.py) ====================
# One HTTP client is kept and reused, so requests share open connections: without it every call (including the
# long poll every few seconds) paid a new TCP + TLS (+ SOCKS) handshake. The client is rebuilt when the proxy
# setting changes and at every planned session restart (start_bot_polling), so a stuck connection cannot live long.

_client_lock = threading.Lock()
_client = None
_client_proxy = None


def _http_client() -> httpx.Client:
    global _client, _client_proxy
    url = tgproxy.get_url()              # the proxy is optional: Settings → Telegram or PROXY_URL in .env
    with _client_lock:
        if _client is None or url != _client_proxy:
            old = _client
            transport = httpx.HTTPTransport(proxy=url) if url else None
            _client = httpx.Client(transport=transport, timeout=httpx.Timeout(20.0, connect=10.0))
            _client_proxy = url
            if old is not None:
                _close_quietly(old)
        return _client


def _close_quietly(client) -> None:
    try:
        client.close()
    except Exception:
        pass


def reset_http_client() -> None:
    """Drops the shared client: the next request opens fresh connections."""
    global _client
    with _client_lock:
        old, _client = _client, None
    if old is not None:
        _close_quietly(old)


def safe_error(e) -> str:
    """repr() of an exception without the bot token (httpx puts the request URL into some messages)."""
    text = repr(e)
    return text.replace(BOT_TOKEN, "***") if BOT_TOKEN else text


# Every message the bot sends is remembered so /clear and the digest cleanup can delete it later (tgclean.py)
TRACKED_ENDPOINTS = ("sendMessage", "sendPhoto", "sendDocument", "sendMediaGroup")


def _sync_tg_request(method: str, endpoint: str, payload: dict = None, params: dict = None, timeout_sec: float = 20.0):
    """Blocking request to the Bot API (run in a worker thread by tg_request)."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{endpoint}"
    timeout = httpx.Timeout(timeout_sec, connect=10.0)
    client = _http_client()
    if method == "GET":
        resp = client.get(url, params=params, timeout=timeout)
    else:
        resp = client.post(url, json=payload, timeout=timeout)
    result = resp.json()
    if endpoint in TRACKED_ENDPOINTS:
        tgclean.record((payload or {}).get("chat_id"), tgclean.ids_from_result(result), "bot")
    return result

async def tg_request(method: str, endpoint: str, payload: dict = None, params: dict = None, timeout_sec: float = 20.0):
    """Non-blocking wrapper with a HARD limit against hangs."""
    try:
        # The thread gets 5 seconds more than the socket timeout.
        # If the proxy hangs for good, asyncio stops waiting anyway.
        return await asyncio.wait_for(
            asyncio.to_thread(_sync_tg_request, method, endpoint, payload, params, timeout_sec),
            timeout=timeout_sec + 5.0
        )
    except asyncio.TimeoutError:
        raise httpx.ReadTimeout("Hard async timeout")

async def send_message(chat_id: int, text: str, reply_markup: dict = None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        res = await tg_request("POST", "sendMessage", payload=payload)
        if not res.get("ok"):
            print(f"[BOT ERROR] Telegram rejected sendMessage: {res.get('description')}")
        return res
    except Exception as e:
        print(f"[BOT ERROR] send_message failed: {safe_error(e)}")
        return {}

async def edit_message_text(chat_id: int, message_id: int, text: str, reply_markup: dict = None):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        res = await tg_request("POST", "editMessageText", payload=payload)
        if not res.get("ok") and "not modified" not in str(res.get("description", "")):
            print(f"[BOT ERROR] Telegram rejected editMessageText: {res.get('description')}")
    except Exception as e:
        print(f"[BOT ERROR] edit_message_text failed: {safe_error(e)}")

async def answer_callback(callback_id: str, text: str = "", show_alert: bool = False):
    # Telegram silently drops an answer longer than 200 characters: the user would see no confirmation at all
    payload = {"callback_query_id": callback_id, "text": short_text(text, TG_ALERT_LIMIT), "show_alert": show_alert}
    try:
        await tg_request("POST", "answerCallbackQuery", payload=payload, timeout_sec=10.0)
    except Exception:
        pass

# ==================== HELPERS ====================

def priority_badge(priority: str) -> str:
    p = str(priority).lower()
    if "critical" in p: return "🔴 Critical"
    if "high" in p: return "🟠 High"
    if "low" in p: return "🟢 Low"
    return "🟡 Medium"

def to_date(val):
    if not val: return None
    if isinstance(val, datetime): return val.date()
    if isinstance(val, date): return val
    try: return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
    except Exception: return None

def format_deadline(dt, is_done: bool = False) -> str:
    d = to_date(dt)
    if not d: return tr("bot.no_date")
    date_str = fmt_date(d)
    
    # A closed task or project does not need a scary deadline
    if is_done:
        return f"{tr('bot.closed_lc')} ({date_str})"
        
    today = datetime.now().date()
    diff = (d - today).days
    if diff < 0: return f"⚠️ {tr('bot.overdue_lc')} ({date_str})"
    elif diff <= 3: return f"🔥 {tr('bot.burning_lc')} ({date_str})"
    return f"{tr('bot.until')} {date_str}"

CARD_MAX_OPEN = 15          # open tasks listed in a card; the rest becomes "…and N more"
CARD_MAX_BUTTONS = 8        # task button rows under a card (the nearest deadlines first)
URGENT_MAX = 8              # urgent tasks of other projects under the focus card
PROJECT_BUTTONS_MAX = 40    # project buttons in lists (Telegram allows about 100 buttons per message)
BTN_NAME_LEN = 18           # task name on a button


def more_line(n: int) -> str:
    return f"<i>… {tr('common.and_more', n=n)}</i>"


def build_project_card(proj, tasks, tokens, total_active_count=1, is_focus=True):
    """Text and buttons of a project card. Closed tasks are folded into one line and the open ones are capped,
    so the card stays under Telegram's message limit however many tasks the project collects."""
    def task_sort_key(t):
        d = to_date(t.deadline)
        return (1 if status_done(t.status) else 0, 1 if d is None else 0, d or date(2099, 1, 1))

    tasks.sort(key=task_sort_key)
    open_tasks = [t for t in tasks if not status_done(t.status)]
    closed_count = len(tasks) - len(open_tasks)

    stage_str = getattr(proj, 'stage', NO_STAGE) or NO_STAGE
    proj_is_done = status_done(proj.status)
    est_str = format_deadline(proj.estimate, proj_is_done) if proj.estimate else tr("bot.not_set")
    header_icon = f"🔥 <b>{tr('bot.in_focus')}:" if is_focus else f"🎯 <b>{tr('common.project')}:"

    lines = [
        f"{header_icon} {esc(proj.name)}</b>",
        f"📌 {tr('bot.status')}: <b>{esc(tv(proj.status))}</b> | 🎨 {tr('project.stage')}: <b>{esc(tv(stage_str))}</b>",
        f"⚡ {tr('bot.priority')}: {priority_badge(proj.priority)} | 💰 {tr('common.balance')}: {tokens}",
        f"📅 {tr('bot.project_deadline')}: {est_str}",
        "",
        f"<b>{tr('bot.task_list')}:</b>",
    ]

    keyboard = []
    if not tasks:
        lines.append(f"<i>{tr('bot.no_tasks')}</i>")
    for t in open_tasks[:CARD_MAX_OPEN]:
        in_progress = str(t.status).strip() == TASK_IN_PROGRESS
        icon = "🔄" if in_progress else "⏸️"
        post_mark = postpone_mark(getattr(t, 'postponed', 0))
        lines.append(f"{icon} {esc(t.task_name)}{post_mark} <i>({format_deadline(t.deadline, False)})</i>")
        if len(keyboard) < CARD_MAX_BUTTONS:
            short_name = short_text(t.task_name, BTN_NAME_LEN)
            row = []
            if not in_progress:
                row.append({"text": f"🔄 {tr('bot.btn_start_work')}: {short_name}", "callback_data": f"t_prog:{t.id}:{proj.id}"})
            row.append({"text": f"✅ {tr('bot.btn_done')}: {short_name}", "callback_data": f"t_done:{t.id}:{proj.id}"})
            keyboard.append(row)
    if len(open_tasks) > CARD_MAX_OPEN:
        lines.append(more_line(len(open_tasks) - CARD_MAX_OPEN))
    if closed_count:
        lines.append(f"✅ <i>{tr('bot.closed_count', n=closed_count)}</i>")

    keyboard.append([{"text": f"🎨 {tr('bot.change_stage')} ({tv(stage_str)})", "callback_data": f"stage_menu:{proj.id}"},
                     {"text": f"➕ {tr('bot.btn_add_task')}", "callback_data": f"tk_new:{proj.id}"}])
    photos_count = _count_photos(proj.id)
    refs_count = _count_refs(proj.id)
    media_row = []
    if photos_count:
        media_row.append({"text": f"📷 {tr('bot.btn_photos', n=photos_count)}", "callback_data": f"ph_v:{proj.id}:0"})
    if refs_count:
        media_row.append({"text": f"🖼 {tr('bot.btn_refs', n=refs_count)}", "callback_data": f"rf_v:{proj.id}:0"})
    media_row.append({"text": f"📤 {tr('bot.btn_share')}", "callback_data": f"share:{proj.id}"})
    keyboard.append(media_row)
    if total_active_count > 1:
        keyboard.append([{"text": f"📋 {tr('bot.btn_all_active')}", "callback_data": "show_all_proj"}])

    return "\n".join(lines), {"inline_keyboard": keyboard}

# Snarky comments on repeated postponing: the 2nd, 3rd, 4th, and the 5th and later. There is no token penalty.
POSTPONE_SNARK_KEYS = (
    "bot.postpone_snark_2",
    "bot.postpone_snark_3",
    "bot.postpone_snark_4",
    "bot.postpone_snark_5",
)


def postpone_snark(count: int) -> str:
    """Reaction text for postponement number count (1 = the first one, no comment)."""
    if count < 2:
        return ""
    key = POSTPONE_SNARK_KEYS[min(count, 5) - 2]
    return f"😏 {tr(key, n=count)}"


def postpone_mark(count) -> str:
    """Postponement mark of a task in the card: ⏳ for one postponement, ⏳×N for several."""
    count = int(count or 0)
    if count <= 0:
        return ""
    return " ⏳" if count == 1 else f" ⏳×{count}"


def build_current_focus_view(specific_proj_id: str = None):
    db = SessionLocal()
    try:
        config = load_config()
        all_projects = live_projects(db).all()
        all_tasks = live_tasks(db).all()
        
        tokens, _ = compute_balance(db, config)

        active_projects = [p for p in all_projects if not status_done(p.status)]
        if not active_projects:
            return f"🎉 {tr('bot.all_closed')}", None

        # 1. Pick the project in focus
        if specific_proj_id:
            proj = next((p for p in active_projects if p.id == specific_proj_id), active_projects[0])
            is_focus = False
        else:
            prio_rank = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
            active_projects.sort(key=lambda p: (
                0 if p.status == STATUS_IN_WORK else 1,
                prio_rank.get(p.priority, 2),
                to_date(p.estimate) or date(2099, 1, 1)
            ))
            proj = active_projects[0]
            is_focus = True

        tasks = [t for t in all_tasks if t.project_id == proj.id]
        
        # Build the main focus card
        text, markup = build_project_card(proj, tasks, tokens, len(active_projects), is_focus)

        # 2. Look for urgent tasks in OTHER projects
        burning_other = []
        today = datetime.now().date()
        
        # Active projects except the one in focus and those "on hold"
        other_active_projs = {
            p.id: p for p in active_projects 
            if p.id != proj.id and str(p.status).strip() != STATUS_ON_HOLD
        }
        
        for t in all_tasks:
            if t.project_id not in other_active_projs:
                continue
                
            # Skip tasks that are already closed
            if status_done(t.status):
                continue
                
            d = to_date(t.deadline)
            if not d:
                continue
                
            diff = (d - today).days
            if diff <= 3:  # 3 days or less left, or already overdue
                burning_other.append((t, other_active_projs[t.project_id], diff))

        # 3. Urgent tasks elsewhere are listed below the card
        if burning_other:
            burning_other.sort(key=lambda x: x[2])  # most overdue first
            
            extra_lines = ["", f"🚨 <b>{tr('bot.urgent_other')}:</b>"]
            for t, p, diff in burning_other[:URGENT_MAX]:
                date_str = fmt_date(to_date(t.deadline))
                diff_text = f"{tr('bot.overdue_lc')} ({date_str})" if diff < 0 else f"{tr('bot.burning_lc')} ({date_str})"
                extra_lines.append(f"• <b>{esc(p.name)}</b>: {esc(t.task_name)} <i>({diff_text})</i>")
            if len(burning_other) > URGENT_MAX:
                extra_lines.append(more_line(len(burning_other) - URGENT_MAX))
            text += "\n" + "\n".join(extra_lines)

        # Long project and task names can still add up: a message over the limit would not arrive at all
        return "\n".join(clip_lines(text.split("\n"), more=more_line)), markup
    finally:
        db.close()

def build_stage_menu_view(proj_id: str):
    db = SessionLocal()
    try:
        proj = _live_project(db, proj_id)
        if not proj:
            return tr("err.project_not_found"), None
        stage_str = getattr(proj, 'stage', NO_STAGE) or NO_STAGE
        text = f"🎨 <b>{tr('bot.choose_stage')} «{esc(proj.name)}»</b>\n{tr('bot.current_stage')}: <b>{esc(tv(stage_str))}</b>"
        stages = STAGES
        btns = [{"text": (f"• {tv(st)} •" if st == stage_str else tv(st)), "callback_data": f"p_stage:{proj.id}:{st}"} for st in stages]
        keyboard = [btns[:3], btns[3:], [{"text": f"⬅️ {tr('bot.back_to_project')}", "callback_data": f"open_p:{proj.id}"}]]
        return text, {"inline_keyboard": keyboard}
    finally:
        db.close()

def build_all_projects_view():
    db = SessionLocal()
    try:
        projects = live_projects(db).all()
        active = [p for p in projects if not status_done(p.status)]
        if not active:
            return tr("project.no_active"), None
        lines = [f"📋 <b>{tr('project.active_list')}:</b>", ""]
        keyboard = []
        for p in active:
            st = getattr(p, 'stage', NO_STAGE)
            lines.append(f"• <b>{esc(p.name)}</b> — {esc(tv(p.status))} [{esc(tv(st))}] ({priority_badge(p.priority)})")
            if len(keyboard) < PROJECT_BUTTONS_MAX:
                keyboard.append([{"text": f"🔍 {short_text(p.name, 60)}", "callback_data": f"open_p:{p.id}"}])
        keyboard.append([{"text": f"➕ {tr('bot.btn_add_task')}", "callback_data": "tk_new:*"}])
        return "\n".join(clip_lines(lines, more=more_line)), {"inline_keyboard": keyboard}
    finally:
        db.close()

def build_current_month_stats():
    """Statistics for the CURRENT month, for the /stats command."""
    db = SessionLocal()
    try:
        now = datetime.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        tasks = live_tasks(db).all()
        projects = live_projects(db).all()
        config = load_config()

        closed_tasks = [t for t in tasks if t.completed_at and t.completed_at >= month_start]
        closed_projs = [p for p in projects if p.completed_at and p.completed_at >= month_start]
        earned_month = sum(to_tokens(p.token_reward) for p in closed_projs)

        balance, _ = compute_balance(db, config)

        return (
            f"📈 <b>{tr('bot.month_progress')} ({now.strftime('%m.%Y')}):</b>\n\n"
            f"✅ {tr('stats.tasks_closed')}: <b>{len(closed_tasks)}</b>\n"
            f"🏆 {tr('stats.projects_completed')}: <b>{len(closed_projs)}</b>\n"
            f"💰 {tr('stats.earned_month')}: <b>+{earned_month} {tr('common.tokens')}</b>\n"
            f"🏦 {tr('bot.available_balance')}: <b>{balance} {tr('common.tokens')}</b>"
        )
    finally:
        db.close()

# ==================== COMMANDS AND CALLBACKS ====================

async def cmd_test_notify(chat_id: int):
    spawn_script("notify.py", ["--test"])
    await send_message(chat_id, f"🧪 {tr('bot.test_started')}...")

# ==================== PROGRESS PHOTOS ====================
# A photo sent to the bot goes to the project timeline (the photo caption becomes the note).
# With several active projects, the bot asks which one the photo belongs to.

PENDING_TTL_SEC = 3600
MAX_TG_DOWNLOAD = 20 * 1024 * 1024        # Bot API limit for file downloads
_pending_photos = {}                       # token -> {"files": [file_id], "caption", "ts"}
_photo_groups = {}                         # media_group_id -> {"token": ..., "project": ..., "ts": ...}


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
        await send_message(chat_id, tr("bot.calendar_empty"))
        return
    try:
        res = await tg_upload("sendDocument",
                              {"chat_id": str(chat_id), "caption": tr("bot.calendar_caption", n=count), "parse_mode": "HTML"},
                              {"document": ("deadlines.ics", data, "text/calendar")})
        if not res.get("ok"):
            print(f"[BOT] sendDocument (calendar): {res.get('description')}")
            await send_message(chat_id, tr("bot.calendar_failed"))
    except Exception as e:
        print(f"[BOT] sendDocument (calendar): {safe_error(e)}")
        await send_message(chat_id, tr("bot.calendar_failed"))


def _count_photos(project_id: str) -> int:
    db = SessionLocal()
    try:
        return progress.count_photos(db, project_id)
    except Exception:
        return 0
    finally:
        db.close()


def _ref_rows(db, project_id: str) -> list:
    """References of a project in the card's order, only those whose file is on disk."""
    rows = db.query(Reference).filter(Reference.project_id == project_id).order_by(Reference.position, Reference.id).all()
    return [r for r in rows if os.path.exists(os.path.join(SHARE_REFS_DIR, os.path.basename(r.filename)))]


def _count_refs(project_id: str) -> int:
    db = SessionLocal()
    try:
        return len(_ref_rows(db, project_id))
    except Exception:
        return 0
    finally:
        db.close()


def _sync_tg_download(file_id: str) -> bytes:
    """Downloads a file by file_id (getFile + download) through the same connection as the rest of the bot."""
    client = _http_client()
    timeout = httpx.Timeout(40.0, connect=10.0)
    info = client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile", json={"file_id": file_id}, timeout=timeout).json()
    path = (info.get("result") or {}).get("file_path")
    if not info.get("ok") or not path:
        raise RuntimeError(f"getFile: {info.get('description')}")
    resp = client.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}", timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"file download: HTTP {resp.status_code}")     # no URL in the text: it holds the token
    return resp.content


async def download_file(file_id: str) -> bytes:
    return await asyncio.wait_for(asyncio.to_thread(_sync_tg_download, file_id), timeout=50.0)


def _sync_tg_upload(endpoint: str, data: dict, files: dict, timeout_sec: float = 60.0):
    """A request with files (multipart): sendPhoto, sendMediaGroup and editMessageMedia."""
    result = _http_client().post(f"https://api.telegram.org/bot{BOT_TOKEN}/{endpoint}", data=data, files=files,
                                 timeout=httpx.Timeout(timeout_sec, connect=10.0)).json()
    if endpoint in TRACKED_ENDPOINTS:
        tgclean.record(data.get("chat_id"), tgclean.ids_from_result(result), "bot")
    return result


async def tg_upload(endpoint: str, data: dict, files: dict, timeout_sec: float = 60.0):
    return await asyncio.wait_for(asyncio.to_thread(_sync_tg_upload, endpoint, data, files, timeout_sec),
                                  timeout=timeout_sec + 10.0)


def _extract_image(msg: dict):
    """(file_id, size) of the image in a message: a regular photo (the largest size) or an image sent as a file."""
    sizes = msg.get("photo")
    if sizes:
        best = max(sizes, key=lambda s: (s.get("file_size") or 0, (s.get("width") or 0) * (s.get("height") or 0)))
        return best.get("file_id"), best.get("file_size")
    doc = msg.get("document")
    if doc and str(doc.get("mime_type", "")).startswith("image/"):
        return doc.get("file_id"), doc.get("file_size")
    return None


def _prune_pending():
    cutoff = time.time() - PENDING_TTL_SEC
    for token in [t for t, v in _pending_photos.items() if v["ts"] < cutoff]:
        _pending_photos.pop(token, None)
    for g in [g for g, v in _photo_groups.items() if v["ts"] < cutoff]:
        _photo_groups.pop(g, None)


def _active_projects(db):
    return [p for p in live_projects(db).all() if not status_done(p.status)]


async def save_telegram_photos(project_id: str, file_ids: list, note: str):
    """Downloads the files and saves them as progress photos.
    Returns (saved ids, project name, stage, error, number of photos NOT saved)."""
    db = SessionLocal()
    try:
        proj = db.query(Project).filter(Project.id == project_id).first()
        if not proj:
            return [], "", "", tr("err.project_not_found"), len(file_ids)
        saved, error = [], None
        for fid in file_ids:
            try:
                raw = await download_file(fid)
                row = progress.add_photo(db, project_id, raw, None, note, "telegram")
                saved.append(row.id)
            except progress.ProgressError as e:
                error = tr(e.key, **e.params)
            except Exception as e:
                print(f"[BOT ERROR] Could not fetch the photo: {safe_error(e)}")
                error = tr("bot.photo_download_failed")
        return saved, proj.name, getattr(proj, "stage", NO_STAGE) or NO_STAGE, error, len(file_ids) - len(saved)
    finally:
        db.close()


def not_added_text(failed: int, total: int, error: str = None) -> str:
    """Warning that some or all photos did not make it into the project, with the reason when known."""
    head = tr("bot.photo_not_added") if failed >= total else tr("bot.photo_not_added_some", n=failed, total=total)
    return f"⚠️ {head}" + (f": {error}" if error else "")


def _saved_view(name: str, stage: str, ids: list, project_id: str):
    suffix = f" · {esc(tv(stage))}" if stage and stage != NO_STAGE else ""
    text = f"📷 {tr('bot.photo_saved', name=esc(name), stage=suffix)}"
    row = [{"text": f"📷 {tr('bot.btn_open_photos')}", "callback_data": f"ph_v:{project_id}:0"}]
    undo = "ph_undo:" + ",".join(str(i) for i in ids[:6])
    if ids:
        row.append({"text": f"↩ {tr('bot.btn_undo')}", "callback_data": undo})
    return text, {"inline_keyboard": [row]}


async def handle_photo_message(chat_id: int, msg: dict):
    """A photo from the chat: any unexpected failure is reported, so a photo never disappears silently."""
    try:
        await _handle_photo_message(chat_id, msg)
    except Exception as e:
        print(f"[BOT ERROR] photo message: {safe_error(e)}")
        traceback.print_exc()
        await send_message(chat_id, not_added_text(1, 1))


async def _handle_photo_message(chat_id: int, msg: dict):
    found = _extract_image(msg)
    if not found or not found[0]:
        return
    file_id, size = found
    if size and size > MAX_TG_DOWNLOAD:
        await send_message(chat_id, tr("bot.photo_too_big"))
        return
    note = (msg.get("caption") or "").strip()
    group = msg.get("media_group_id")
    _prune_pending()

    entry = _photo_groups.get(group) if group else None
    if entry:                                           # the next photo of the same album
        if entry.get("project"):
            # this album was already answered: success stays quiet, a failure is reported
            ids, _, _, error, failed = await save_telegram_photos(entry["project"], [file_id], note)
            if failed:
                await send_message(chat_id, not_added_text(1, 1, error))
        elif entry["token"] in _pending_photos:
            _pending_photos[entry["token"]]["files"].append(file_id)
        else:                                           # the project question was cancelled or has expired
            await send_message(chat_id, not_added_text(1, 1, tr("bot.photo_expired")))
        return

    db = SessionLocal()
    try:
        active = [(p.id, p.name) for p in _active_projects(db)]
    finally:
        db.close()
    if not active:
        await send_message(chat_id, tr("bot.photo_no_projects"))
        return

    if len(active) == 1:
        pid = active[0][0]
        if group:
            _photo_groups[group] = {"token": None, "project": pid, "ts": time.time()}
        ids, name, stage, error, failed = await save_telegram_photos(pid, [file_id], note)
        if ids:
            text, markup = _saved_view(name, stage, ids, pid)
            await send_message(chat_id, text, markup)
        if failed:
            await send_message(chat_id, not_added_text(failed, 1, error))
        return

    token = uuid.uuid4().hex[:8]
    _pending_photos[token] = {"files": [file_id], "caption": note, "ts": time.time()}
    if group:
        _photo_groups[group] = {"token": token, "project": None, "ts": time.time()}
    keyboard = [[{"text": f"🎨 {short_text(name, 60)}", "callback_data": f"ph_to:{token}:{pid}"}]
                for pid, name in active[:PROJECT_BUTTONS_MAX]]
    keyboard.append([{"text": f"✖ {tr('common.cancel')}", "callback_data": f"ph_cancel:{token}"}])
    await send_message(chat_id, f"📷 {tr('bot.photo_which')}", {"inline_keyboard": keyboard})


async def handle_photo_choice(cb_id: str, chat_id: int, message_id: int, token: str, project_id: str):
    pending = _pending_photos.pop(token, None)
    if not pending:
        await answer_callback(cb_id, tr("bot.photo_expired"), show_alert=True)
        await edit_message_text(chat_id, message_id, not_added_text(1, 1, tr("bot.photo_expired")))
        return
    for g, v in list(_photo_groups.items()):
        if v.get("token") == token:
            v["project"], v["token"] = project_id, None
    await answer_callback(cb_id)
    total = len(pending["files"])
    ids, name, stage, error, failed = await save_telegram_photos(project_id, pending["files"], pending["caption"])
    if ids:
        text, markup = _saved_view(name, stage, ids, project_id)
        if failed:
            text += "\n" + not_added_text(failed, total, error)
        await edit_message_text(chat_id, message_id, text, markup)
    else:
        await edit_message_text(chat_id, message_id, not_added_text(total, total, error or tr("bot.photo_download_failed")))


# ----- sharing a project: an album to forward -----
# Cover, the latest progress photos and the references (up to Telegram's 10 per album), with the name, the stage
# and the paint recipe in the caption. Tasks, tokens and deadlines stay private. The album can be forwarded to
# anyone; the bot answers only the bound chat, so a forwarded album gives nobody access to the data.
SHARE_MAX_ITEMS = 10                    # Telegram limit for an album
SHARE_PROGRESS = 5                      # the latest progress photos
SHARE_FILE_MAX = 5 * 1024 * 1024        # bigger originals (and GIF/WebP) go as their JPEG preview
SHARE_PHOTO_EXT = (".jpg", ".jpeg", ".png")
CAPTION_MAX = 1024                      # Telegram limit for a caption (characters after markup)
SHARE_REFS_DIR = os.path.join(progress.PHOTOS_DIR, "refs")


def _share_file(path: str, thumb: str = ""):
    """The file to send for an image: the original if Telegram takes it as a photo, otherwise the preview."""
    try:
        if path and path.lower().endswith(SHARE_PHOTO_EXT) and os.path.getsize(path) <= SHARE_FILE_MAX:
            return path
    except OSError:
        pass
    return thumb if thumb and os.path.exists(thumb) else None


def build_share(project_id: str):
    """(files, caption, extra_text) for the project, or None if there is no such project."""
    db = SessionLocal()
    try:
        proj = _live_project(db, project_id)
        if not proj:
            return None
        files = []
        if proj.photo:
            cover = _share_file(os.path.join(progress.PHOTOS_DIR, os.path.basename(proj.photo)))
            if cover:
                files.append(cover)
        latest = progress.list_photos(db, proj.id, limit=SHARE_PROGRESS)
        for row in reversed(latest):                              # oldest of the latest first: a timeline
            path = os.path.join(progress.PROGRESS_DIR, os.path.basename(row.filename))
            thumb = os.path.join(progress.PROGRESS_DIR, os.path.basename(row.thumb)) if row.thumb else ""
            f = _share_file(path, thumb)
            if f:
                files.append(f)
        refs = db.query(Reference).filter(Reference.project_id == proj.id).order_by(Reference.position, Reference.id).all()
        for r in refs:
            if len(files) >= SHARE_MAX_ITEMS:
                break
            path = os.path.join(SHARE_REFS_DIR, os.path.basename(r.filename))
            thumb = os.path.join(SHARE_REFS_DIR, os.path.basename(r.thumb)) if r.thumb else ""
            f = _share_file(path, thumb)
            if f:
                files.append(f)

        stage = proj.stage or NO_STAGE
        head = f"🎨 <b>{esc(proj.name)}</b>"
        head_len = len(proj.name) + 3
        if status_done(proj.status):
            head += f"\n🏆 {tr('bot.share_finished')}"
            head_len += len(tr('bot.share_finished')) + 3
        elif stage != NO_STAGE:
            head += f"\n{tr('project.stage')}: {esc(tv(stage))}"
            head_len += len(tr('project.stage')) + len(tv(stage)) + 3
        recipe = (proj.paint_notes or "").strip()
        extra = None
        if recipe:
            title = f"📝 {tr('bot.share_recipe')}:"
            if head_len + len(title) + len(recipe) + 4 <= CAPTION_MAX:
                caption = f"{head}\n\n<b>{title}</b>\n{esc(recipe)}"
            else:                                                  # a long recipe goes as its own message
                caption = f"{head}\n\n<i>{tr('bot.share_recipe_below')}</i>"
                extra = f"<b>{title}</b> {esc(proj.name)}\n\n{esc(recipe[:3800])}"
        else:
            caption = head
        return files, caption, extra
    finally:
        db.close()


async def send_project_share(chat_id: int, project_id: str):
    built = await asyncio.to_thread(build_share, project_id)
    if not built:
        await send_message(chat_id, tr("err.project_not_found"))
        return
    files, caption, extra = built
    try:
        if not files:
            res = await tg_request("POST", "sendMessage", payload={"chat_id": chat_id, "text": caption, "parse_mode": "HTML"})
        elif len(files) == 1:
            res = await tg_upload("sendPhoto", {"chat_id": str(chat_id), "caption": caption, "parse_mode": "HTML"},
                                  {"photo": (os.path.basename(files[0]), await asyncio.to_thread(_read_bytes, files[0]))})
        else:
            media, upload = [], {}
            for i, path in enumerate(files):
                item = {"type": "photo", "media": f"attach://f{i}"}
                if i == 0:
                    item.update({"caption": caption, "parse_mode": "HTML"})
                media.append(item)
                upload[f"f{i}"] = (os.path.basename(path), await asyncio.to_thread(_read_bytes, path))
            res = await tg_upload("sendMediaGroup", {"chat_id": str(chat_id), "media": json.dumps(media)}, upload,
                                  timeout_sec=180.0)
        if not res.get("ok"):
            print(f"[BOT ERROR] Telegram rejected the share: {res.get('description')}")
            await send_message(chat_id, f"⚠️ {tr('bot.share_failed')}")
            return
    except Exception as e:
        print(f"[BOT ERROR] share failed: {safe_error(e)}")
        await send_message(chat_id, f"⚠️ {tr('bot.share_failed')}")
        return
    if extra:
        await send_message(chat_id, extra)
    await send_message(chat_id, f"📤 {tr('bot.share_hint')}")


# ----- viewing photos -----

def _photo_view(scope: str, index: int):
    """Photo number index (newest first) in the selection, as a dict (path, caption, keyboard, index, total), or None."""
    db = SessionLocal()
    try:
        rows = progress.list_photos(db, None if scope == "*" else scope)
        if not rows:
            return None
        index = max(0, min(index, len(rows) - 1))
        row = rows[index]
        proj = db.query(Project).filter(Project.id == row.project_id).first()
        lines = [f"<b>{esc(proj.name if proj else row.project_id)}</b>"]
        if row.stage and row.stage != NO_STAGE:
            lines[0] += f" · {esc(tv(row.stage))}"
        when = row.created_at.strftime("%H:%M") if row.created_at else ""
        lines.append(f"📅 {fmt_date(row.created_at)} {when}".strip())
        if row.note:
            lines.append(esc(row.note))
        total = len(rows)
        nav = []
        if total > 1:
            nav = [
                {"text": "◀", "callback_data": f"ph_v:{scope}:{(index + 1) % total}"},
                {"text": f"{index + 1}/{total}", "callback_data": "noop"},
                {"text": "▶", "callback_data": f"ph_v:{scope}:{(index - 1) % total}"},
            ]
        keyboard = [nav] if nav else []
        last_row = [{"text": f"🗑 {tr('bot.btn_delete')}", "callback_data": f"ph_d:{scope}:{index}"}]
        if scope != "*":                                       # one project's photos: a way back to /myphotos
            last_row.append({"text": f"◀ {tr('bot.btn_back')}", "callback_data": f"mp_p:{scope}"})
        keyboard.append(last_row)
        path = os.path.join(progress.PROGRESS_DIR, os.path.basename(row.filename))
        return {"path": path, "caption": "\n".join(lines), "markup": {"inline_keyboard": keyboard},
                "index": index, "total": total, "id": row.id}
    finally:
        db.close()


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


async def send_photo_view(chat_id: int, scope: str, index: int = 0) -> bool:
    view = _photo_view(scope, index)
    if not view:
        return False
    try:
        res = await tg_upload("sendPhoto", {
            "chat_id": str(chat_id), "caption": view["caption"], "parse_mode": "HTML",
            "reply_markup": json.dumps(view["markup"]),
        }, {"photo": (os.path.basename(view["path"]), _read_bytes(view["path"]))})
        if not res.get("ok"):
            print(f"[BOT ERROR] Telegram rejected sendPhoto: {res.get('description')}")
            await send_message(chat_id, f"⚠️ {tr('bot.photo_send_failed')}")
    except Exception as e:
        print(f"[BOT ERROR] sendPhoto failed: {safe_error(e)}")
        await send_message(chat_id, f"⚠️ {tr('bot.photo_send_failed')}")
    return True


async def edit_photo_view(chat_id: int, message_id: int, scope: str, index: int):
    view = _photo_view(scope, index)
    if not view:
        await tg_request("POST", "editMessageCaption", payload={
            "chat_id": chat_id, "message_id": message_id, "caption": tr("bot.photo_none"),
            "reply_markup": {"inline_keyboard": []}})
        return
    media = {"type": "photo", "media": "attach://photo", "caption": view["caption"], "parse_mode": "HTML"}
    try:
        res = await tg_upload("editMessageMedia", {
            "chat_id": str(chat_id), "message_id": str(message_id),
            "media": json.dumps(media), "reply_markup": json.dumps(view["markup"]),
        }, {"photo": (os.path.basename(view["path"]), _read_bytes(view["path"]))})
        if not res.get("ok") and "not modified" not in str(res.get("description", "")):
            print(f"[BOT ERROR] Telegram rejected editMessageMedia: {res.get('description')}")
    except Exception as e:
        print(f"[BOT ERROR] editMessageMedia failed: {safe_error(e)}")


async def handle_photo_callback(cb: dict, data: str) -> bool:
    """Photo buttons. True if the callback was one of them."""
    cb_id = cb["id"]
    msg = cb.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    message_id = msg.get("message_id")
    parts = data.split(":")
    action = parts[0]

    if action == "noop":
        await answer_callback(cb_id)
    elif action == "mp_list":
        await answer_callback(cb_id)
        view_text, markup = await asyncio.to_thread(build_myphotos_projects)
        await _show_text(chat_id, msg, view_text, markup)
    elif action == "mp_p" and len(parts) == 2:
        await answer_callback(cb_id)
        view_text, markup = await asyncio.to_thread(build_myphotos_kinds, parts[1])
        await _show_text(chat_id, msg, view_text, markup)
    elif action == "rf_v" and len(parts) == 3 and parts[2].lstrip("-").isdigit():
        index = int(parts[2])
        if msg.get("photo"):                                   # flip through in the same message
            await answer_callback(cb_id)
            await edit_ref_view(chat_id, message_id, parts[1], index)
        elif await send_ref_view(chat_id, parts[1], index):
            await answer_callback(cb_id)
        else:
            await answer_callback(cb_id, tr("bot.refs_none"), show_alert=True)
    elif action == "rf_o" and len(parts) == 3 and parts[2].isdigit():
        await answer_callback(cb_id)
        await send_ref_original(chat_id, parts[1], int(parts[2]))
    elif action == "ph_to" and len(parts) == 3:
        await handle_photo_choice(cb_id, chat_id, message_id, parts[1], parts[2])
    elif action == "ph_cancel" and len(parts) == 2:
        _pending_photos.pop(parts[1], None)
        await answer_callback(cb_id)
        await edit_message_text(chat_id, message_id, f"✖ {tr('bot.photo_cancelled')}")
    elif action == "ph_undo" and len(parts) == 2:
        db = SessionLocal()
        try:
            for pid in parts[1].split(","):
                if pid.isdigit():
                    progress.delete_photo(db, int(pid))
        finally:
            db.close()
        await answer_callback(cb_id)
        await edit_message_text(chat_id, message_id, f"↩ {tr('bot.photo_undone')}")
    elif action == "ph_v" and len(parts) == 3 and parts[2].lstrip("-").isdigit():
        index = int(parts[2])
        if msg.get("photo"):                                   # flip through in the same message
            await answer_callback(cb_id)
            await edit_photo_view(chat_id, message_id, parts[1], index)
        elif await send_photo_view(chat_id, parts[1], index):  # from a text card: a new message with the photo
            await answer_callback(cb_id)
        else:
            await answer_callback(cb_id, tr("bot.photo_none"), show_alert=True)
    elif action == "ph_d" and len(parts) == 3 and parts[2].isdigit():
        view = _photo_view(parts[1], int(parts[2]))
        if not view:
            await answer_callback(cb_id, tr("bot.photo_none"), show_alert=True)
        else:
            await answer_callback(cb_id)
            markup = {"inline_keyboard": [[
                {"text": f"🗑 {tr('bot.btn_delete_yes')}", "callback_data": f"ph_dy:{parts[1]}:{view['id']}:{view['index']}"},
                {"text": f"↩ {tr('common.cancel')}", "callback_data": f"ph_v:{parts[1]}:{view['index']}"},
            ]]}
            await tg_request("POST", "editMessageReplyMarkup",
                             payload={"chat_id": chat_id, "message_id": message_id, "reply_markup": markup})
    elif action == "ph_dy" and len(parts) == 4 and parts[2].isdigit() and parts[3].isdigit():
        db = SessionLocal()
        try:
            progress.delete_photo(db, int(parts[2]))
        finally:
            db.close()
        await answer_callback(cb_id, tr("bot.photo_deleted"))
        await edit_photo_view(chat_id, message_id, parts[1], int(parts[3]))
    else:
        return False
    return True


# ----- /myphotos: pick a project, then progress photos or paint references -----

def _project_media_counts(db):
    """[(project, progress count, refs count)] for live projects that have anything to show; active ones first."""
    out = []
    for p in live_projects(db).all():
        photos = progress.count_photos(db, p.id)
        refs = len(_ref_rows(db, p.id))
        if photos or refs:
            out.append((p, photos, refs))
    out.sort(key=lambda x: (status_done(x[0].status), x[0].name.lower()))
    return out


def build_myphotos_projects():
    db = SessionLocal()
    try:
        items = _project_media_counts(db)
    finally:
        db.close()
    if not items:
        return tr("bot.myphotos_empty"), None
    keyboard = []
    for p, photos, refs in items[:PROJECT_BUTTONS_MAX]:
        mark = "🏆" if status_done(p.status) else "🎨"
        counts = " · ".join(x for x in (f"📷{photos}" if photos else "", f"🖼{refs}" if refs else "") if x)
        keyboard.append([{"text": f"{mark} {short_text(p.name, 48)} ({counts})", "callback_data": f"mp_p:{p.id}"}])
    text = f"🖼 <b>{tr('bot.myphotos_title')}</b>\n{tr('bot.myphotos_pick_project')}"
    if len(items) > PROJECT_BUTTONS_MAX:
        text += "\n" + more_line(len(items) - PROJECT_BUTTONS_MAX)
    return text, {"inline_keyboard": keyboard}


def build_myphotos_kinds(project_id: str):
    db = SessionLocal()
    try:
        proj = _live_project(db, project_id)
        if not proj:
            return tr("err.project_not_found"), {"inline_keyboard": [[{"text": f"◀ {tr('bot.btn_back_projects')}", "callback_data": "mp_list"}]]}
        photos = progress.count_photos(db, proj.id)
        refs = len(_ref_rows(db, proj.id))
        name = proj.name
    finally:
        db.close()
    row = []
    if photos:
        row.append({"text": f"📷 {tr('bot.btn_progress', n=photos)}", "callback_data": f"ph_v:{project_id}:0"})
    if refs:
        row.append({"text": f"🖼 {tr('bot.btn_refs', n=refs)}", "callback_data": f"rf_v:{project_id}:0"})
    keyboard = [row] if row else []
    keyboard.append([{"text": f"◀ {tr('bot.btn_back_projects')}", "callback_data": "mp_list"}])
    text = f"🎨 <b>{esc(name)}</b>\n" + (tr("bot.myphotos_pick_kind") if row else tr("bot.myphotos_project_empty"))
    return text, {"inline_keyboard": keyboard}


async def _show_text(chat_id: int, msg: dict, text: str, markup):
    """Text menus: edited in place, but a photo message cannot become text, so a new message is sent instead."""
    if msg.get("photo"):
        await send_message(chat_id, text, markup)
    else:
        await edit_message_text(chat_id, msg.get("message_id"), text, markup)


def _ref_view(project_id: str, index: int):
    """Reference number index (in the card's order) as a dict (path, caption, markup, ...), or None."""
    db = SessionLocal()
    try:
        proj = _live_project(db, project_id)
        rows = _ref_rows(db, project_id) if proj else []
        if not rows:
            return None
        total = len(rows)
        index = index % total
        row = rows[index]
        path = os.path.join(SHARE_REFS_DIR, os.path.basename(row.filename))
        thumb = os.path.join(SHARE_REFS_DIR, os.path.basename(row.thumb)) if row.thumb else ""
        send = _share_file(path, thumb)
        if not send:
            return None
        caption = f"<b>{esc(proj.name)}</b> · {tr('bot.ref_caption', n=index + 1, total=total)}"
        if send != path:
            caption += f"\n<i>{tr('bot.ref_preview_note')}</i>"
        keyboard = []
        if total > 1:
            keyboard.append([
                {"text": "◀", "callback_data": f"rf_v:{project_id}:{(index - 1) % total}"},
                {"text": f"{index + 1}/{total}", "callback_data": "noop"},
                {"text": "▶", "callback_data": f"rf_v:{project_id}:{(index + 1) % total}"},
            ])
        keyboard.append([{"text": f"📎 {tr('bot.btn_original')}", "callback_data": f"rf_o:{project_id}:{row.id}"},
                         {"text": f"◀ {tr('bot.btn_back')}", "callback_data": f"mp_p:{project_id}"}])
        return {"path": send, "caption": caption, "markup": {"inline_keyboard": keyboard}, "index": index, "total": total}
    finally:
        db.close()


async def send_ref_view(chat_id: int, project_id: str, index: int = 0) -> bool:
    view = await asyncio.to_thread(_ref_view, project_id, index)
    if not view:
        return False
    try:
        res = await tg_upload("sendPhoto", {
            "chat_id": str(chat_id), "caption": view["caption"], "parse_mode": "HTML",
            "reply_markup": json.dumps(view["markup"]),
        }, {"photo": (os.path.basename(view["path"]), await asyncio.to_thread(_read_bytes, view["path"]))})
        if not res.get("ok"):
            print(f"[BOT ERROR] Telegram rejected sendPhoto (reference): {res.get('description')}")
            await send_message(chat_id, f"⚠️ {tr('bot.photo_send_failed')}")
    except Exception as e:
        print(f"[BOT ERROR] sendPhoto (reference) failed: {safe_error(e)}")
        await send_message(chat_id, f"⚠️ {tr('bot.photo_send_failed')}")
    return True


async def edit_ref_view(chat_id: int, message_id: int, project_id: str, index: int):
    view = await asyncio.to_thread(_ref_view, project_id, index)
    if not view:
        await tg_request("POST", "editMessageCaption", payload={
            "chat_id": chat_id, "message_id": message_id, "caption": tr("bot.refs_none"),
            "reply_markup": {"inline_keyboard": []}})
        return
    media = {"type": "photo", "media": "attach://photo", "caption": view["caption"], "parse_mode": "HTML"}
    try:
        res = await tg_upload("editMessageMedia", {
            "chat_id": str(chat_id), "message_id": str(message_id),
            "media": json.dumps(media), "reply_markup": json.dumps(view["markup"]),
        }, {"photo": (os.path.basename(view["path"]), await asyncio.to_thread(_read_bytes, view["path"]))})
        if not res.get("ok") and "not modified" not in str(res.get("description", "")):
            print(f"[BOT ERROR] Telegram rejected editMessageMedia (reference): {res.get('description')}")
    except Exception as e:
        print(f"[BOT ERROR] editMessageMedia (reference) failed: {safe_error(e)}")


async def send_ref_original(chat_id: int, project_id: str, ref_id: int):
    """The reference file exactly as stored (any size up to the 25 MB upload limit), as a document."""
    db = SessionLocal()
    try:
        row = db.query(Reference).filter(Reference.id == ref_id, Reference.project_id == project_id).first()
        path = os.path.join(SHARE_REFS_DIR, os.path.basename(row.filename)) if row else ""
    finally:
        db.close()
    if not path or not os.path.exists(path):
        await send_message(chat_id, tr("bot.refs_none"))
        return
    try:
        res = await tg_upload("sendDocument", {"chat_id": str(chat_id)},
                              {"document": (os.path.basename(path), await asyncio.to_thread(_read_bytes, path))},
                              timeout_sec=180.0)
        if not res.get("ok"):
            print(f"[BOT ERROR] Telegram rejected sendDocument (reference): {res.get('description')}")
            await send_message(chat_id, f"⚠️ {tr('bot.photo_send_failed')}")
    except Exception as e:
        print(f"[BOT ERROR] sendDocument (reference) failed: {safe_error(e)}")
        await send_message(chat_id, f"⚠️ {tr('bot.photo_send_failed')}")


# ==================== CHAT CLEANUP (/clear and digests, see tgclean.py) ====================

CLEAR_NOTE_SEC = 5                     # how long "Chat cleared" stays before it removes itself
CLEANUP_EVERY_SEC = 600                # digest cleanup check; keep_46h needs it well inside the 2-hour window
_cleanup_state = {"last": 0.0}


async def delete_messages(chat_id, ids: list) -> int:
    """Deletes messages in batches of 100. Returns how many were sent for deletion without an error;
    Telegram skips messages that are already gone or too old."""
    done = 0
    for batch in tgclean.batches(list(ids)):
        try:
            res = await tg_request("POST", "deleteMessages", payload={"chat_id": chat_id, "message_ids": batch})
            if res.get("ok"):
                done += len(batch)
            else:
                print(f"[BOT] deleteMessages: {res.get('description')}")
        except Exception as e:
            print(f"[BOT] deleteMessages failed: {safe_error(e)}")
    await asyncio.to_thread(tgclean.forget, chat_id, ids)       # either deleted or not deletable any more
    return done


async def _delete_later(chat_id, message_id, delay: float):
    await asyncio.sleep(delay)
    await delete_messages(chat_id, [message_id])


async def send_self_destructing(chat_id, text: str, delay: float = CLEAR_NOTE_SEC):
    res = await send_message(chat_id, text)
    ids = tgclean.ids_from_result(res)
    if ids:
        asyncio.create_task(_delete_later(chat_id, ids[0], delay))


async def start_clear(chat_id: int):
    """/clear: asks first. Only messages younger than 48 hours that the bot knows about can be removed."""
    plan = await asyncio.to_thread(tgclean.clear_plan, chat_id)
    n = len(plan["all"])
    if n <= 1:                                           # only the /clear message itself
        await delete_messages(chat_id, plan["all"])
        await send_self_destructing(chat_id, f"🧹 {tr('bot.clear_nothing')}")
        return
    rows = [[{"text": f"🗑 {tr('bot.clear_all', n=n)}", "callback_data": "clr:all"}]]
    if plan["has_digest"]:
        rows.append([{"text": f"📌 {tr('bot.clear_keep_last', n=len(plan['keep_last']))}", "callback_data": "clr:keep"}])
    rows.append([{"text": f"✖ {tr('common.cancel')}", "callback_data": "clr:no"}])
    await send_message(chat_id, f"🧹 <b>{tr('bot.clear_title')}</b>\n{tr('bot.clear_question', n=n)}\n"
                                f"<i>{tr('bot.clear_limit_note')}</i>", {"inline_keyboard": rows})


async def handle_clear_callback(cb_id: str, chat_id: int, message_id: int, data: str):
    action = data.split(":", 1)[1] if ":" in data else ""
    await answer_callback(cb_id)
    if action == "no":
        await delete_messages(chat_id, [message_id])
        return
    plan = await asyncio.to_thread(tgclean.clear_plan, chat_id)
    ids = plan["keep_last"] if action == "keep" else plan["all"]
    ids = sorted(set(ids) | {message_id})                 # the question itself goes too
    await delete_messages(chat_id, ids)
    await send_self_destructing(chat_id, f"🧹 {tr('bot.clear_done')}")


async def cleanup_digests():
    """Digest cleanup by the TG_CLEANUP mode (runs from the polling loop every few minutes)."""
    cfg = load_config()
    chat_id = cfg.get("TG_CHAT_ID")
    try:
        if chat_id:
            ids = await asyncio.to_thread(tgclean.digest_candidates, chat_id)
            if ids:
                await delete_messages(chat_id, ids)
        await asyncio.to_thread(tgclean.prune)
    except Exception as e:
        print(f"[BOT] digest cleanup failed: {safe_error(e)}")


# ==================== TASK BUILDER ====================
# /task or the "Task" button: project -> name -> deadline -> created. The draft lives in memory for one hour.

MAX_TASK_NAME = MAX_TASK_NAME_LEN
_task_drafts = {}          # chat_id -> {"step": ..., "project": id|None, "name": str|None, "ts": float}


def _draft(chat_id):
    d = _task_drafts.get(chat_id)
    if d and time.time() - d["ts"] > PENDING_TTL_SEC:
        _task_drafts.pop(chat_id, None)
        return None
    return d


def _new_draft(chat_id, project=None, name=None):
    _task_drafts[chat_id] = {"step": "project", "project": project, "name": name, "ts": time.time()}
    return _task_drafts[chat_id]


def parse_user_date(text: str):
    """A date from text in the format from the settings (21.03.2027 or 03/21/2027; the year may be omitted)."""
    m = re.fullmatch(r"\s*(\d{1,2})[./\-](\d{1,2})(?:[./\-](\d{2}|\d{4}))?\s*", text or "")
    if not m:
        return None
    a, b, y = int(m.group(1)), int(m.group(2)), m.group(3)
    day, month = (b, a) if config_value("DATE_FORMAT") == "mdy" else (a, b)
    today = date.today()
    year = (int(y) + 2000 if y and len(y) == 2 else int(y)) if y else today.year
    try:
        result = date(year, month, day)
    except ValueError:
        return None
    if not y and result < today:
        try:
            result = date(year + 1, month, day)
        except ValueError:
            return None
    return result


def _cancel_row(extra=None):
    row = list(extra or [])
    row.append({"text": f"✖ {tr('common.cancel')}", "callback_data": "tk_x"})
    return row


def _project_choice_view():
    db = SessionLocal()
    try:
        active = [(p.id, p.name) for p in _active_projects(db)]
    finally:
        db.close()
    if not active:
        return tr("bot.task_no_projects"), None
    keyboard = [[{"text": f"📁 {short_text(name, 60)}", "callback_data": f"tk_p:{pid}"}] for pid, name in active[:PROJECT_BUTTONS_MAX]]
    keyboard.append(_cancel_row())
    return f"➕ {tr('bot.task_pick_project')}", {"inline_keyboard": keyboard}


def _date_choice_view(project_id: str, name: str):
    db = SessionLocal()
    try:
        proj = db.query(Project).filter(Project.id == project_id, Project.deleted_at.is_(None)).first()
        planned = bool(proj) and str(proj.status).strip() == STATUS_PLANNED
        estimate = to_date(proj.estimate) if proj and proj.estimate else None
    finally:
        db.close()
    rows = [
        [{"text": tr("bot.btn_today"), "callback_data": "tk_d:today"}, {"text": tr("bot.btn_tomorrow"), "callback_data": "tk_d:tom"}],
        [{"text": tr("bot.btn_in_3"), "callback_data": "tk_d:3d"}, {"text": tr("bot.btn_in_week"), "callback_data": "tk_d:7d"}],
    ]
    if estimate:
        rows.append([{"text": tr("bot.btn_project_deadline", date=fmt_date(estimate)), "callback_data": "tk_d:proj"}])
    rows.append([{"text": f"✏️ {tr('bot.btn_own_date')}", "callback_data": "tk_d:own"}])
    # for a project that is not "planned" a task deadline is required (as in the web UI)
    rows.append(_cancel_row([{"text": tr("bot.btn_no_deadline"), "callback_data": "tk_d:none"}] if planned else None))
    return f"📅 {tr('bot.task_ask_date', name=esc(name))}", {"inline_keyboard": rows}


async def start_task_constructor(chat_id: int, project: str = None, name: str = None):
    """Start: if the project is known (a card button, or the only active one), go straight to the name."""
    db = SessionLocal()
    try:
        active = _active_projects(db)
        if project and not any(p.id == project for p in active):
            project = None
        if not project and len(active) == 1:
            project = active[0].id
    finally:
        db.close()
    _new_draft(chat_id, project, name)
    await _task_step(chat_id)


async def _task_step(chat_id: int, message_id: int = None):
    """Shows the next step of the draft; message_id is the message with buttons that may be replaced."""
    d = _draft(chat_id)
    if not d:
        return
    if not d["project"]:
        text, markup = _project_choice_view()
        if markup is None:
            _task_drafts.pop(chat_id, None)
        d["step"] = "project"
    elif not d["name"]:
        d["step"] = "name"
        text, markup = f"➕ {tr('bot.task_ask_name', name=esc(_project_name(d['project'])))}", {"inline_keyboard": [_cancel_row()]}
    else:
        d["step"] = "date"
        text, markup = _date_choice_view(d["project"], d["name"])
    d["ts"] = time.time()
    if message_id:
        await edit_message_text(chat_id, message_id, text, markup)
    else:
        await send_message(chat_id, text, markup)


def _project_name(project_id: str) -> str:
    db = SessionLocal()
    try:
        p = db.query(Project).filter(Project.id == project_id).first()
        return p.name if p else project_id
    finally:
        db.close()


async def _reply(chat_id: int, message_id, text: str, markup=None):
    """Replaces the message with buttons, or sends a new one if there is none (a reply to user text)."""
    if message_id:
        await edit_message_text(chat_id, message_id, text, markup)
    else:
        await send_message(chat_id, text, markup)


async def create_task_from_draft(chat_id: int, message_id, deadline):
    d = _task_drafts.pop(chat_id, None)
    if not d:
        return
    db = SessionLocal()
    try:
        proj = db.query(Project).filter(Project.id == d["project"], Project.deleted_at.is_(None)).first()
        if not proj:
            await _reply(chat_id, message_id, tr("err.project_not_found"))
            return
        task = Task(project_id=proj.id, task_name=d["name"], deadline=datetime.combine(deadline, datetime.min.time()) if deadline else None,
                    status=TASK_NOT_STARTED, postponed=0)
        db.add(task)
        proj.updated_at = datetime.now()
        db.commit()
        when = fmt_date(deadline) if deadline else tr("bot.not_set")
        text = (f"✅ <b>{tr('bot.task_created')}</b>\n{esc(d['name'])}\n"
                f"📁 {esc(proj.name)} · 📅 {when}")
        markup = {"inline_keyboard": [[
            {"text": f"➕ {tr('bot.btn_more_task')}", "callback_data": f"tk_new:{proj.id}"},
            {"text": f"🔍 {tr('bot.btn_open_project')}", "callback_data": f"open_p:{proj.id}"},
        ]]}
    finally:
        db.close()
    await _reply(chat_id, message_id, text, markup)


async def handle_task_text(chat_id: int, text: str) -> bool:
    """Plain text while a draft is open: the task name or a custom date. True if the message was handled."""
    d = _draft(chat_id)
    if not d or d["step"] not in ("name", "own_date"):
        return False
    if d["step"] == "name":
        name = text.strip()
        if not name or len(name) > MAX_TASK_NAME:
            await send_message(chat_id, tr("bot.task_bad_name", n=MAX_TASK_NAME))
            return True
        d["name"] = name
        await _task_step(chat_id)
        return True
    parsed = parse_user_date(text)
    if not parsed:
        await send_message(chat_id, tr("bot.task_bad_date", example=fmt_date(date.today())))
        return True
    await create_task_from_draft(chat_id, None, parsed)
    return True


async def handle_task_callback(cb: dict, data: str) -> bool:
    cb_id = cb["id"]
    msg = cb.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    message_id = msg.get("message_id")
    parts = data.split(":")
    action = parts[0]

    if action == "tk_new" and len(parts) == 2:
        await answer_callback(cb_id)
        await start_task_constructor(chat_id, None if parts[1] == "*" else parts[1])
    elif action == "tk_x":
        _task_drafts.pop(chat_id, None)
        await answer_callback(cb_id)
        await edit_message_text(chat_id, message_id, f"✖ {tr('bot.task_cancelled')}")
    elif action == "tk_p" and len(parts) == 2:
        d = _draft(chat_id)
        if not d:
            await answer_callback(cb_id, tr("bot.task_expired"), show_alert=True)
            return True
        db = SessionLocal()
        try:
            exists = any(p.id == parts[1] for p in _active_projects(db))
        finally:
            db.close()
        if not exists:
            await answer_callback(cb_id, tr("err.project_not_found"), show_alert=True)
            return True
        d["project"] = parts[1]
        await answer_callback(cb_id)
        await _task_step(chat_id, message_id)
    elif action == "tk_d" and len(parts) == 2:
        d = _draft(chat_id)
        if not d or not d.get("name") or not d.get("project"):
            await answer_callback(cb_id, tr("bot.task_expired"), show_alert=True)
            return True
        today = date.today()
        code = parts[1]
        if code == "own":
            d["step"] = "own_date"
            await answer_callback(cb_id)
            await edit_message_text(chat_id, message_id, f"✏️ {tr('bot.task_ask_own_date', example=fmt_date(today))}",
                                    {"inline_keyboard": [_cancel_row()]})
            return True
        if code == "proj":
            db = SessionLocal()
            try:
                proj = db.query(Project).filter(Project.id == d["project"]).first()
                deadline = to_date(proj.estimate) if proj and proj.estimate else None
            finally:
                db.close()
        else:
            deadline = {"today": today, "tom": today + timedelta(days=1), "3d": today + timedelta(days=3),
                        "7d": today + timedelta(days=7), "none": None}.get(code, False)
        if deadline is False:
            await answer_callback(cb_id)
            return True
        if deadline is None and code == "none":
            db = SessionLocal()
            try:
                proj = db.query(Project).filter(Project.id == d["project"]).first()
                planned = bool(proj) and str(proj.status).strip() == STATUS_PLANNED
            finally:
                db.close()
            if not planned:                      # a deadline is required: the same rule as in the web UI
                await answer_callback(cb_id, tr("bot.task_deadline_required"), show_alert=True)
                return True
        await answer_callback(cb_id)
        await create_task_from_draft(chat_id, message_id, deadline)
    else:
        return False
    return True


async def handle_callback(cb: dict):
    cb_id = cb["id"]
    data = cb.get("data") or ""
    msg = cb.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    message_id = msg.get("message_id")
    now = datetime.now()

    if data.split(":")[0] == "clr":
        try:
            await handle_clear_callback(cb_id, chat_id, message_id, data)
        except Exception as e:
            print(f"[CALLBACK ERROR]: {safe_error(e)}")
            traceback.print_exc()
        return

    if data.split(":")[0] in ("tk_new", "tk_p", "tk_d", "tk_x"):
        try:
            if await handle_task_callback(cb, data):
                return
        except Exception as e:
            print(f"[CALLBACK ERROR]: {safe_error(e)}")
            traceback.print_exc()
            return

    if data == "noop" or data.split(":")[0] in ("ph_to", "ph_cancel", "ph_undo", "ph_v", "ph_d", "ph_dy",
                                                        "mp_list", "mp_p", "rf_v", "rf_o"):
        try:
            if await handle_photo_callback(cb, data):
                return
        except Exception as e:
            print(f"[CALLBACK ERROR]: {safe_error(e)}")
            traceback.print_exc()
            return

    db = SessionLocal()
    try:
        if data.startswith("t_done:") or data.startswith("n_done:"):
            # t_done comes from a project card, n_done straight from a push notification
            parts = data.split(":")
            from_push = parts[0] == "n_done"
            task = _live_task(db, parts[1])
            if not task:
                await answer_callback(cb_id, tr("bot.task_not_found"))
                return
            if not status_done(task.status):           # a second press must not move the completion date
                task.status = STATUS_FINISHED
                task.completed_at = now
                task.closed_from = ""
                _touch_project(db, task.project_id, now)
                db.commit()
            if from_push:
                await answer_callback(cb_id, f"✅ {tr('bot.task_closed_named', name=short_text(task.task_name, MAX_TASK_NAME_LEN))}", show_alert=True)
            else:
                await answer_callback(cb_id, f"✅ {tr('bot.task_closed')}")
            card_project = task.project_id if from_push else (parts[2] if len(parts) > 2 else None)
            text, markup = await asyncio.to_thread(build_current_focus_view, card_project)
            await edit_message_text(chat_id, message_id, text, markup)

        elif data.startswith("t_postpone:"):
            # Moves the deadline by +2 days from a push. The first time silently, repeated ones with a snarky
            # comment (text only, no token penalty).
            task_id = data.split(":", 1)[1]
            task = _live_task(db, task_id)
            if not task:
                await answer_callback(cb_id, tr("bot.task_not_found"))
                return
            if status_done(task.status):
                await answer_callback(cb_id, f"✅ {tr('bot.task_closed')}")
                return
            if not load_config().get("NOTIFY", {}).get("allow_postpone", True):
                await answer_callback(cb_id, tr("bot.postpone_disabled"), show_alert=True)
                return
            base_date = to_date(task.deadline) or now.date()
            if base_date < now.date():
                base_date = now.date()
            new_deadline = base_date + timedelta(days=2)
            task.deadline = datetime.combine(new_deadline, datetime.min.time())
            count = int(task.postponed or 0) + 1
            task.postponed = count
            _touch_project(db, task.project_id, now)
            db.commit()
            reply = f"⏳ {tr('bot.postponed', date=fmt_date(new_deadline))}"
            snark = postpone_snark(count)
            if snark:
                reply += f"\n\n{snark}"
            await answer_callback(cb_id, reply, show_alert=True)
            text, markup = await asyncio.to_thread(build_current_focus_view, task.project_id)
            await edit_message_text(chat_id, message_id, text, markup)

        elif data.startswith("t_prog:"):
            parts = data.split(":")
            task_id, proj_id = parts[1], (parts[2] if len(parts) > 2 else None)
            task = _live_task(db, task_id)
            if task and not status_done(task.status):   # an old button must not reopen a closed task
                task.status = TASK_IN_PROGRESS
                task.completed_at = None
                _touch_project(db, task.project_id, now)
                db.commit()
                await answer_callback(cb_id, f"🔄 {tr('bot.task_in_progress')}")
            elif task:
                await answer_callback(cb_id, f"✅ {tr('bot.task_closed')}")
            else:
                await answer_callback(cb_id, tr("bot.task_not_found"))
            text, markup = await asyncio.to_thread(build_current_focus_view, proj_id)
            await edit_message_text(chat_id, message_id, text, markup)

        elif data.startswith("stage_menu:"):
            proj_id = data.split(":", 1)[1]
            await answer_callback(cb_id)
            text, markup = await asyncio.to_thread(build_stage_menu_view, proj_id)
            await edit_message_text(chat_id, message_id, text, markup)

        elif data.startswith("p_stage:"):
            _, proj_id, new_stage = data.split(":", 2)
            new_stage = normalize_value(new_stage)     # buttons sent before the switch to codes carry Russian names
            proj = _live_project(db, proj_id)
            if proj and new_stage in STAGES:
                proj.stage = new_stage
                proj.updated_at = now
                db.commit()
                await answer_callback(cb_id, f"🎨 {tr('project.stage')}: {tv(new_stage)}")
            elif not proj:
                await answer_callback(cb_id, tr("err.project_not_found"))
            else:
                await answer_callback(cb_id)
            text, markup = await asyncio.to_thread(build_current_focus_view, proj_id)
            await edit_message_text(chat_id, message_id, text, markup)

        elif data == "show_all_proj":
            await answer_callback(cb_id)
            text, markup = await asyncio.to_thread(build_all_projects_view)
            await edit_message_text(chat_id, message_id, text, markup)

        elif data.startswith("share:"):
            await answer_callback(cb_id, tr("bot.share_preparing"))
            await send_project_share(chat_id, data.split(":", 1)[1])

        elif data.startswith("open_p:"):
            proj_id = data.split(":", 1)[1]
            await answer_callback(cb_id)
            text, markup = await asyncio.to_thread(build_current_focus_view, proj_id)
            await edit_message_text(chat_id, message_id, text, markup)

        else:
            await answer_callback(cb_id)                # unknown or outdated button: stop the spinner
    except Exception as e:
        print(f"[CALLBACK ERROR]: {safe_error(e)}")
        traceback.print_exc()
    finally:
        db.close()


def _live_project(db, project_id: str):
    """A project outside the trash, or None. Buttons of old messages stay clickable after a project is deleted:
    they must not change a project in the trash."""
    return db.query(Project).filter(Project.id == project_id, Project.deleted_at.is_(None)).first()


def _live_task(db, task_id: str):
    """A task whose project is outside the trash, or None."""
    return (db.query(Task).filter(Task.id == task_id)
            .filter(Task.project_id.in_(db.query(Project.id).filter(Project.deleted_at.is_(None)))).first())


def _touch_project(db, project_id: str, now: datetime) -> None:
    """Marks progress on a project (resets the stagnation detector)."""
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj:
        proj.updated_at = now


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
        await send_message(chat_id, tr("bot.private_only"))
        return
    if payload and check_pairing_code(payload):
        bind_chat(chat_id)
        _failed_pairing.clear()
        print(f"[BOT] The bot is now linked to chat {chat_id}")
        await send_message(chat_id, f"✅ {tr('bot.chat_linked')}")
        await send_message(chat_id, welcome_text())
        return
    if payload:
        _failed_pairing.append(time.time())
    await send_message(chat_id, tr("bot.not_linked"))


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
        await handle_photo_message(chat_id, msg)
        return

    if text.startswith("/"):
        _task_drafts.pop(chat_id, None)           # any command cancels a task draft in progress
    elif await handle_task_text(chat_id, text):
        return

    if text.startswith("/start"):
        await send_message(chat_id, welcome_text())
    elif text.startswith("/current_task"):
        view_text, markup = await asyncio.to_thread(build_current_focus_view)
        await send_message(chat_id, view_text, markup)
    elif text.startswith("/help"):
        await send_message(chat_id, help_text())
    elif text.startswith("/task"):
        rest = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ""
        await start_task_constructor(chat_id, None, rest[:MAX_TASK_NAME] if rest else None)
    elif text.startswith("/projects"):
        view_text, markup = await asyncio.to_thread(build_all_projects_view)
        await send_message(chat_id, view_text, markup)
    elif text.startswith("/myphotos"):
        view_text, markup = await asyncio.to_thread(build_myphotos_projects)
        await send_message(chat_id, view_text, markup)
    elif text.startswith("/photo"):
        if not await send_photo_view(chat_id, "*", 0):
            await send_message(chat_id, tr("bot.photo_none"))
    elif text.startswith("/calendar"):
        if ics.enabled():
            await send_calendar(chat_id)
        else:
            await send_message(chat_id, tr("bot.calendar_off"))
    elif text.startswith("/clear"):
        await start_clear(chat_id)
    elif text.startswith("/stats"):
        await send_message(chat_id, await asyncio.to_thread(build_current_month_stats))
    elif text.startswith("/test_notify"):
        await cmd_test_notify(chat_id)


SESSION_MAX_SEC = 3 * 3600            # planned reconnect: a fresh connection every 3 hours
PRE_PUSH_RECONNECT_SEC = 600          # ... and about 10 minutes before scheduled notifications
NETWORK_RETRY_SEC = 3                 # pause after a network error, so a dead proxy does not spin the CPU


async def start_bot_polling():
    ensure_schema()
    if not BOT_TOKEN:
        print("⚠️ TELEGRAM_BOT_TOKEN is not set in .env!")
        return
    code = ensure_pairing_code()
    if code:
        print(f"🔗 The bot is not linked to a chat. Send it in a private chat: /start {format_pairing_code(code)} "
              f"(the code is also shown in the app: Settings → Telegram)")

    # The offset survives session restarts: getUpdates confirms the previous batch only through the next offset,
    # so starting again from 0 would deliver the last processed updates twice (a "+2d" press applied twice).
    offset = 0
    while True:  # OUTER LOOP: a new session (fresh connections) every few hours
        reset_http_client()
        try:
            await tg_request("POST", "deleteWebhook", payload={"drop_pending_updates": False}, timeout_sec=15.0)
            print(f"✅ [{datetime.now().strftime('%H:%M:%S')}] Telegram bot started a new session ({tgproxy.describe()})")
            _menu_sent["value"] = None
            await sync_menu_commands()
        except asyncio.CancelledError:
            return
        except Exception as e:
            print(f"⚠️ Could not connect to Telegram at session start ({tgproxy.describe()}): {safe_error(e)}")

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
                data = await tg_request("GET", "getUpdates", params={"offset": offset, "timeout": 10}, timeout_sec=20.0)
            except asyncio.CancelledError:
                return  # clean stop when the service shuts down
            except httpx.ReadTimeout:
                continue
            except (httpx.TransportError, ValueError):     # no connection, proxy refused, broken reply
                await asyncio.sleep(NETWORK_RETRY_SEC)
                continue
            except Exception as e:
                print(f"[BOT POLLING ERROR]: {safe_error(e)}")
                await asyncio.sleep(5)
                continue

            if not data.get("ok"):
                await asyncio.sleep(NETWORK_RETRY_SEC)
                continue

            for item in data.get("result", []):
                offset = item["update_id"] + 1
                try:
                    await process_update(item)
                except asyncio.CancelledError:
                    return
                except Exception as e:                    # one bad update must not stop the rest of the batch
                    print(f"[BOT UPDATE ERROR]: {safe_error(e)}")
                    traceback.print_exc()
