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
from database import SessionLocal, Project, Task, ensure_schema
import progress
from common import (
    BASE_DIR, esc, load_config, config_value, compute_balance, fmt_date, live_projects, live_tasks,
    is_chat_bound, ensure_pairing_code, check_pairing_code, bind_chat, format_pairing_code,
    is_done as status_done, STAGES, STATUS_IN_WORK, STATUS_ON_HOLD, STATUS_PLANNED, STATUS_FINISHED,
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
    ("stats", "bot.cmd_stats_desc"),
    ("help", "bot.cmd_help_desc"),
    ("test_notify", "bot.cmd_test_desc"),
]
FEATURES = [                         # features without a command: text keys
    "bot.feat_photo",
    "bot.feat_buttons",
    "bot.feat_push",
    "bot.feat_postpone",
]


def commands_text() -> str:
    return "\n".join(f"• /{name} — {tr(key)}" for name, key in COMMANDS)


def welcome_text() -> str:
    return (
        f"🤖 <b>{tr('bot.connected')}</b>\n\n"
        f"{tr('bot.commands')}:\n{commands_text()}"
    )


def help_text() -> str:
    features = "\n".join(f"• {tr(key)}" for key in FEATURES)
    return (
        f"📖 <b>{tr('bot.help_title')}</b>\n\n"
        f"<b>{tr('bot.commands')}:</b>\n{commands_text()}\n\n"
        f"<b>{tr('bot.help_features_title')}:</b>\n{features}"
    )


def menu_commands() -> list:
    """List for setMyCommands (the "/" menu in Telegram): descriptions without extra notes, up to 256 characters."""
    return [{"command": name, "description": tr(key)[:256]} for name, key in COMMANDS]

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


def _sync_tg_request(method: str, endpoint: str, payload: dict = None, params: dict = None, timeout_sec: float = 20.0):
    """Blocking request to the Bot API (run in a worker thread by tg_request)."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{endpoint}"
    timeout = httpx.Timeout(timeout_sec, connect=10.0)
    client = _http_client()
    if method == "GET":
        resp = client.get(url, params=params, timeout=timeout)
    else:
        resp = client.post(url, json=payload, timeout=timeout)
    return resp.json()

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
    except Exception as e:
        print(f"[BOT ERROR] send_message failed: {safe_error(e)}")

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
    payload = {"callback_query_id": callback_id, "text": text, "show_alert": show_alert}
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

def build_project_card(proj, tasks, tokens, total_active_count=1, is_focus=True):
    def task_sort_key(t):
        d = to_date(t.deadline)
        return (1 if status_done(t.status) else 0, 1 if d is None else 0, d or date(2099, 1, 1))

    tasks.sort(key=task_sort_key)

    stage_str = getattr(proj, 'stage', '—') or '—'
    proj_is_done = status_done(proj.status)
    est_str = format_deadline(proj.estimate, proj_is_done) if proj.estimate else tr("bot.not_set")
    header_icon = f"🔥 <b>{tr('bot.in_focus')}:" if is_focus else f"🎯 <b>{tr('common.project')}:"

    lines = [
        f"{header_icon} {esc(proj.name)}</b>",
        f"📌 {tr('bot.status')}: <b>{esc(tv(proj.status))}</b> | 🎨 {tr('project.stage')}: <b>{esc(tv(stage_str))}</b>",
        f"⚡ {tr('bot.priority')}: {priority_badge(proj.priority)} | 💰 {tr('bot.reward')}: <b>+{proj.token_reward}</b> ({tr('common.balance')}: {tokens})",
        f"📅 {tr('bot.project_deadline')}: {est_str}\n",
        f"<b>{tr('bot.task_list')}:</b>"
    ]

    keyboard = []
    if not tasks:
        lines.append(f"<i>{tr('bot.no_tasks')}</i>")
    else:
        for t in tasks:
            is_done = status_done(t.status)
            in_progress = str(t.status).strip() == TASK_IN_PROGRESS
            icon = "✅" if is_done else ("🔄" if in_progress else "⏸️")
            post_mark = postpone_mark(getattr(t, 'postponed', 0))
            lines.append(f"{icon} {esc(t.task_name)}{post_mark} <i>({format_deadline(t.deadline, is_done)})</i>")

            if not is_done:
                short_name = t.task_name[:18] + ".." if len(t.task_name) > 18 else t.task_name
                row = []
                if not in_progress:
                    row.append({"text": f"🔄 {tr('bot.btn_start_work')}: {short_name}", "callback_data": f"t_prog:{t.id}:{proj.id}"})
                row.append({"text": f"✅ {tr('bot.btn_done')}: {short_name}", "callback_data": f"t_done:{t.id}:{proj.id}"})
                keyboard.append(row)

    keyboard.append([{"text": f"🎨 {tr('bot.change_stage')} ({tv(stage_str)})", "callback_data": f"stage_menu:{proj.id}"},
                     {"text": f"➕ {tr('bot.btn_add_task')}", "callback_data": f"tk_new:{proj.id}"}])
    photos_count = _count_photos(proj.id)
    if photos_count:
        keyboard.append([{"text": f"📷 {tr('bot.btn_photos', n=photos_count)}", "callback_data": f"ph_v:{proj.id}:0"}])
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
            
            extra_lines = [f"\n🚨 <b>{tr('bot.urgent_other')}:</b>"]
            for t, p, diff in burning_other:
                date_str = fmt_date(to_date(t.deadline))
                diff_text = f"{tr('bot.overdue_lc')} ({date_str})" if diff < 0 else f"{tr('bot.burning_lc')} ({date_str})"
                extra_lines.append(f"• <b>{esc(p.name)}</b>: {esc(t.task_name)} <i>({diff_text})</i>")
            
            text += "\n" + "\n".join(extra_lines)

        return text, markup
    finally:
        db.close()

def build_stage_menu_view(proj_id: str):
    db = SessionLocal()
    try:
        proj = db.query(Project).filter(Project.id == proj_id).first()
        if not proj:
            return tr("err.project_not_found"), None
        stage_str = getattr(proj, 'stage', '—') or '—'
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
        lines = [f"📋 <b>{tr('project.active_list')}:</b>\n"]
        keyboard = []
        for p in active:
            st = getattr(p, 'stage', '—')
            lines.append(f"• <b>{esc(p.name)}</b> — {esc(tv(p.status))} [{esc(tv(st))}] ({priority_badge(p.priority)})")
            keyboard.append([{"text": f"🔍 {p.name}", "callback_data": f"open_p:{p.id}"}])
        keyboard.append([{"text": f"➕ {tr('bot.btn_add_task')}", "callback_data": "tk_new:*"}])
        return "\n".join(lines), {"inline_keyboard": keyboard}
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
        earned_month = sum((p.token_reward or 0) for p in closed_projs)

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


def _count_photos(project_id: str) -> int:
    db = SessionLocal()
    try:
        return progress.count_photos(db, project_id)
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


def _sync_tg_upload(endpoint: str, data: dict, files: dict):
    """A request with a file (multipart): sendPhoto and editMessageMedia."""
    return _http_client().post(f"https://api.telegram.org/bot{BOT_TOKEN}/{endpoint}", data=data, files=files,
                               timeout=httpx.Timeout(60.0, connect=10.0)).json()


async def tg_upload(endpoint: str, data: dict, files: dict):
    return await asyncio.wait_for(asyncio.to_thread(_sync_tg_upload, endpoint, data, files), timeout=70.0)


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
    """Downloads the files and saves them as progress photos. Returns (saved ids, project name, stage, error)."""
    db = SessionLocal()
    try:
        proj = db.query(Project).filter(Project.id == project_id).first()
        if not proj:
            return [], "", "", tr("err.project_not_found")
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
        return saved, proj.name, getattr(proj, "stage", "—") or "—", error
    finally:
        db.close()


def _saved_view(name: str, stage: str, ids: list, project_id: str):
    suffix = f" · {esc(tv(stage))}" if stage and stage != "—" else ""
    text = f"📷 {tr('bot.photo_saved', name=esc(name), stage=suffix)}"
    row = [{"text": f"📷 {tr('bot.btn_open_photos')}", "callback_data": f"ph_v:{project_id}:0"}]
    undo = "ph_undo:" + ",".join(str(i) for i in ids[:6])
    if ids:
        row.append({"text": f"↩ {tr('bot.btn_undo')}", "callback_data": undo})
    return text, {"inline_keyboard": [row]}


async def handle_photo_message(chat_id: int, msg: dict):
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
            await save_telegram_photos(entry["project"], [file_id], note)   # silently: this album was already answered
        elif entry["token"] in _pending_photos:
            _pending_photos[entry["token"]]["files"].append(file_id)
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
        ids, name, stage, error = await save_telegram_photos(pid, [file_id], note)
        if ids:
            text, markup = _saved_view(name, stage, ids, pid)
            await send_message(chat_id, text, markup)
        if error:
            await send_message(chat_id, f"⚠️ {error}")
        return

    token = uuid.uuid4().hex[:8]
    _pending_photos[token] = {"files": [file_id], "caption": note, "ts": time.time()}
    if group:
        _photo_groups[group] = {"token": token, "project": None, "ts": time.time()}
    keyboard = [[{"text": f"🎨 {name}", "callback_data": f"ph_to:{token}:{pid}"}] for pid, name in active]
    keyboard.append([{"text": f"✖ {tr('common.cancel')}", "callback_data": f"ph_cancel:{token}"}])
    await send_message(chat_id, f"📷 {tr('bot.photo_which')}", {"inline_keyboard": keyboard})


async def handle_photo_choice(cb_id: str, chat_id: int, message_id: int, token: str, project_id: str):
    pending = _pending_photos.pop(token, None)
    if not pending:
        await answer_callback(cb_id, tr("bot.photo_expired"), show_alert=True)
        return
    for g, v in list(_photo_groups.items()):
        if v.get("token") == token:
            v["project"], v["token"] = project_id, None
    await answer_callback(cb_id)
    ids, name, stage, error = await save_telegram_photos(project_id, pending["files"], pending["caption"])
    if ids:
        text, markup = _saved_view(name, stage, ids, project_id)
        if error:
            text += f"\n⚠️ {error}"
        await edit_message_text(chat_id, message_id, text, markup)
    else:
        await edit_message_text(chat_id, message_id, f"⚠️ {error or tr('bot.photo_download_failed')}")


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
        if row.stage and row.stage != "—":
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
        keyboard.append([{"text": f"🗑 {tr('bot.btn_delete')}", "callback_data": f"ph_d:{scope}:{index}"}])
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


# ==================== TASK BUILDER ====================
# /task or the "Task" button: project -> name -> deadline -> created. The draft lives in memory for one hour.

MAX_TASK_NAME = 120
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
    keyboard = [[{"text": f"📁 {name}", "callback_data": f"tk_p:{pid}"}] for pid, name in active]
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
    data = cb.get("data", "")
    msg = cb.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    message_id = msg.get("message_id")
    now = datetime.now()

    if data.split(":")[0] in ("tk_new", "tk_p", "tk_d", "tk_x"):
        try:
            if await handle_task_callback(cb, data):
                return
        except Exception as e:
            print(f"[CALLBACK ERROR]: {safe_error(e)}")
            traceback.print_exc()
            return

    if data == "noop" or data.split(":")[0] in ("ph_to", "ph_cancel", "ph_undo", "ph_v", "ph_d", "ph_dy"):
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
            task = db.query(Task).filter(Task.id == parts[1]).first()
            if not task:
                await answer_callback(cb_id, tr("bot.task_not_found"))
                return
            if not status_done(task.status):           # a second press must not move the completion date
                task.status = STATUS_FINISHED
                task.completed_at = now
                _touch_project(db, task.project_id, now)
                db.commit()
            if from_push:
                await answer_callback(cb_id, f"✅ {tr('bot.task_closed_named', name=task.task_name)}", show_alert=True)
            else:
                await answer_callback(cb_id, f"✅ {tr('bot.task_closed')}")
            card_project = task.project_id if from_push else (parts[2] if len(parts) > 2 else None)
            text, markup = await asyncio.to_thread(build_current_focus_view, card_project)
            await edit_message_text(chat_id, message_id, text, markup)

        elif data.startswith("t_postpone:"):
            # Moves the deadline by +2 days from a push. The first time silently, repeated ones with a snarky
            # comment (text only, no token penalty).
            task_id = data.split(":", 1)[1]
            task = db.query(Task).filter(Task.id == task_id).first()
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
            task = db.query(Task).filter(Task.id == task_id).first()
            if task:
                task.status = TASK_IN_PROGRESS
                task.completed_at = None
                _touch_project(db, task.project_id, now)
                db.commit()
                await answer_callback(cb_id, f"🔄 {tr('bot.task_in_progress')}")
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
            proj = db.query(Project).filter(Project.id == proj_id).first()
            if proj and new_stage in STAGES:
                proj.stage = new_stage
                proj.updated_at = now
                db.commit()
                await answer_callback(cb_id, f"🎨 {tr('project.stage')}: {tv(new_stage)}")
            else:
                await answer_callback(cb_id)
            text, markup = await asyncio.to_thread(build_current_focus_view, proj_id)
            await edit_message_text(chat_id, message_id, text, markup)

        elif data == "show_all_proj":
            await answer_callback(cb_id)
            text, markup = await asyncio.to_thread(build_all_projects_view)
            await edit_message_text(chat_id, message_id, text, markup)

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
    """One Telegram update: a message or a button press."""
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
    elif text.startswith("/photo"):
        if not await send_photo_view(chat_id, "*", 0):
            await send_message(chat_id, tr("bot.photo_none"))
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
            try:
                await tg_request("POST", "setMyCommands", payload={"commands": menu_commands()}, timeout_sec=15.0)
            except Exception as e:
                print(f"⚠️ Could not update the command menu: {safe_error(e)}")
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
