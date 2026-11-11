"""The task builder (/task) and the time-spent question after a task is closed."""
import time
import asyncio
import re
from datetime import datetime, date, timedelta
from hub.core.database import SessionLocal, Project, Task
from hub.core import spent
from hub.core.common import (
    esc, short_text, MAX_TASK_NAME_LEN, config_value, fmt_date, live_task, to_date, STATUS_PLANNED, TASK_NOT_STARTED,
)
from hub.core.i18n import tr
from hub.bot import bot_api
from hub.bot import bot_photos
from hub.bot import bot_views

# ==================== TASK BUILDER ====================
# /task or the "Task" button: project (or "no project": a general task) -> name -> deadline -> created.
# The draft lives in memory for one hour. A general task is kept in the draft as bot_views.GENERAL.

MAX_TASK_NAME = MAX_TASK_NAME_LEN
_task_drafts = {}          # chat_id -> {"step": ..., "project": id|None, "name": str|None, "ts": float}


def _draft(chat_id):
    d = _task_drafts.get(chat_id)
    if d and time.time() - d["ts"] > bot_views.PENDING_TTL_SEC:
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
        active = [(p.id, p.name) for p in bot_photos._active_projects(db)]
    finally:
        db.close()
    keyboard = [[{"text": f"📁 {short_text(name, 60)}", "callback_data": f"tk_p:{pid}"}] for pid, name in active[:bot_views.PROJECT_BUTTONS_MAX]]
    keyboard.append([{"text": f"🧰 {tr('bot.btn_no_project')}", "callback_data": f"tk_p:{bot_views.GENERAL}"}])
    keyboard.append(_cancel_row())
    text = tr("bot.task_pick_project") if active else tr("bot.task_no_projects")
    return f"➕ {text}", {"inline_keyboard": keyboard}


def _date_choice_view(project_id: str, name: str):
    db = SessionLocal()
    try:
        proj = db.query(Project).filter(Project.id == project_id, Project.deleted_at.is_(None)).first()
        # a general task may have no deadline (like a task of a "planned" project)
        planned = project_id == bot_views.GENERAL or (bool(proj) and str(proj.status).strip() == STATUS_PLANNED)
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
        active = bot_photos._active_projects(db)
        if project and project != bot_views.GENERAL and not any(p.id == project for p in active):
            project = None
        # with one active project there is still a choice: that project or a general task
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
        d["step"] = "project"
    elif not d["name"]:
        d["step"] = "name"
        ask = (tr("bot.task_ask_name_general") if d["project"] == bot_views.GENERAL
               else tr("bot.task_ask_name", name=esc(_project_name(d["project"]))))
        text, markup = f"➕ {ask}", {"inline_keyboard": [_cancel_row()]}
    else:
        d["step"] = "date"
        text, markup = _date_choice_view(d["project"], d["name"])
    d["ts"] = time.time()
    if message_id:
        await bot_api.edit_message_text(chat_id, message_id, text, markup)
    else:
        await bot_api.send_message(chat_id, text, markup)


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
        await bot_api.edit_message_text(chat_id, message_id, text, markup)
    else:
        await bot_api.send_message(chat_id, text, markup)


async def create_task_from_draft(chat_id: int, message_id, deadline):
    d = _task_drafts.pop(chat_id, None)
    if not d:
        return
    general = d["project"] == bot_views.GENERAL
    db = SessionLocal()
    try:
        proj = None
        if not general:
            proj = db.query(Project).filter(Project.id == d["project"], Project.deleted_at.is_(None)).first()
            if not proj:
                await _reply(chat_id, message_id, tr("err.project_not_found"))
                return
        task = Task(project_id=proj.id if proj else None, task_name=d["name"],
                    deadline=datetime.combine(deadline, datetime.min.time()) if deadline else None,
                    status=TASK_NOT_STARTED, postponed=0)
        db.add(task)
        if proj:
            proj.updated_at = datetime.now()
        db.commit()
        when = fmt_date(deadline) if deadline else tr("bot.not_set")
        where = f"🧰 {esc(tr('general.title'))}" if general else f"📁 {esc(proj.name)}"
        text = (f"✅ <b>{tr('bot.task_created')}</b>\n{esc(d['name'])}\n"
                f"{where} · 📅 {when}")
        target = bot_views.GENERAL if general else proj.id
        markup = {"inline_keyboard": [[
            {"text": f"➕ {tr('bot.btn_more_task')}", "callback_data": f"tk_new:{target}"},
            {"text": f"🧰 {tr('general.title')}", "callback_data": "gen_v"} if general
            else {"text": f"🔍 {tr('bot.btn_open_project')}", "callback_data": f"open_p:{proj.id}"},
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
            await bot_api.send_message(chat_id, tr("bot.task_bad_name", n=MAX_TASK_NAME))
            return True
        d["name"] = name
        await _task_step(chat_id)
        return True
    parsed = parse_user_date(text)
    if not parsed:
        await bot_api.send_message(chat_id, tr("bot.task_bad_date", example=fmt_date(date.today())))
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
        await bot_api.answer_callback(cb_id)
        await start_task_constructor(chat_id, None if parts[1] == "*" else parts[1])
    elif action == "tk_x":
        _task_drafts.pop(chat_id, None)
        await bot_api.answer_callback(cb_id)
        await bot_api.edit_message_text(chat_id, message_id, f"✖ {tr('bot.task_cancelled')}")
    elif action == "tk_p" and len(parts) == 2:
        d = _draft(chat_id)
        if not d:
            await bot_api.answer_callback(cb_id, tr("bot.task_expired"), show_alert=True)
            return True
        db = SessionLocal()
        try:
            exists = parts[1] == bot_views.GENERAL or any(p.id == parts[1] for p in bot_photos._active_projects(db))
        finally:
            db.close()
        if not exists:
            await bot_api.answer_callback(cb_id, tr("err.project_not_found"), show_alert=True)
            return True
        d["project"] = parts[1]
        await bot_api.answer_callback(cb_id)
        await _task_step(chat_id, message_id)
    elif action == "tk_d" and len(parts) == 2:
        d = _draft(chat_id)
        if not d or not d.get("name") or not d.get("project"):
            await bot_api.answer_callback(cb_id, tr("bot.task_expired"), show_alert=True)
            return True
        today = date.today()
        code = parts[1]
        if code == "own":
            d["step"] = "own_date"
            await bot_api.answer_callback(cb_id)
            await bot_api.edit_message_text(chat_id, message_id, f"✏️ {tr('bot.task_ask_own_date', example=fmt_date(today))}",
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
            await bot_api.answer_callback(cb_id)
            return True
        if deadline is None and code == "none" and d["project"] != bot_views.GENERAL:
            db = SessionLocal()
            try:
                proj = db.query(Project).filter(Project.id == d["project"]).first()
                planned = bool(proj) and str(proj.status).strip() == STATUS_PLANNED
            finally:
                db.close()
            if not planned:                      # a deadline is required: the same rule as in the web UI
                await bot_api.answer_callback(cb_id, tr("bot.task_deadline_required"), show_alert=True)
                return True
        await bot_api.answer_callback(cb_id)
        await create_task_from_draft(chat_id, message_id, deadline)
    else:
        return False
    return True


# ==================== TIME SPENT: asked right after a task is closed in the bot ====================
# Optional (Settings: ASK_SPENT_TIME, on by default) and skippable. Quick buttons or your own value;
# the time never affects tokens. The same value can be changed later in the web app (task editing).

TIME_QUICK = (15, 30, 60, 120)
_time_drafts = {}                 # chat_id -> {"task": task id, "message_id": the question, "ts": time}


def _time_markup(task_id: str) -> dict:
    quick = [{"text": spent.format_minutes(m), "callback_data": f"tm:{task_id}:{m}"} for m in TIME_QUICK]
    return {"inline_keyboard": [quick, [
        {"text": f"✏️ {tr('bot.time_own')}", "callback_data": f"tm_own:{task_id}"},
        {"text": tr("bot.time_skip"), "callback_data": f"tm_x:{task_id}"},
    ]]}


async def ask_spent_time(chat_id: int, task_id: str, task_name: str):
    if not config_value("ASK_SPENT_TIME", True):
        return
    await bot_api.send_message(chat_id, f"⏱ {tr('bot.time_ask', name=esc(short_text(task_name, 80)))}", _time_markup(task_id))


def _save_task_time(task_id: str, minutes: int):
    """Saves the time. Returns the task name, or None if the task is gone."""
    db = SessionLocal()
    try:
        task = live_task(db, task_id)
        if not task:
            return None
        spent.set_task_spent(db, task, minutes)
        db.commit()
        return task.task_name
    finally:
        db.close()


def _time_saved_text(name: str, minutes: int) -> str:
    return f"⏱ {tr('bot.time_saved', name=esc(short_text(name, 80)), time=spent.format_minutes(minutes))}"


async def handle_time_callback(cb: dict, data: str):
    cb_id = cb["id"]
    msg = cb.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    message_id = msg.get("message_id")
    parts = data.split(":")
    kind, task_id = parts[0], (parts[1] if len(parts) > 1 else "")
    if kind == "tm_x":
        _time_drafts.pop(chat_id, None)
        await bot_api.answer_callback(cb_id)
        await bot_api.delete_messages(chat_id, [message_id])
        return
    if kind == "tm_own":
        _time_drafts[chat_id] = {"task": task_id, "message_id": message_id, "ts": time.time()}
        await bot_api.answer_callback(cb_id)
        await bot_api.edit_message_text(chat_id, message_id, f"⏱ {tr('bot.time_type')}",
                                {"inline_keyboard": [[{"text": tr("bot.time_skip"), "callback_data": f"tm_x:{task_id}"}]]})
        return
    try:
        minutes = spent.parse_spent(parts[2] if len(parts) > 2 else "")
    except spent.SpentError:
        minutes = None
    if not minutes:
        await bot_api.answer_callback(cb_id)
        return
    name = await asyncio.to_thread(_save_task_time, task_id, minutes)
    if name is None:
        await bot_api.answer_callback(cb_id, tr("bot.task_not_found"))
        await bot_api.delete_messages(chat_id, [message_id])
        return
    _time_drafts.pop(chat_id, None)
    await bot_api.answer_callback(cb_id)
    await bot_api.edit_message_text(chat_id, message_id, _time_saved_text(name, minutes))


async def handle_time_text(chat_id: int, text: str) -> bool:
    """Plain text after "Own value": the time. True if the message was handled."""
    d = _time_drafts.get(chat_id)
    if not d:
        return False
    if time.time() - d["ts"] > bot_views.PENDING_TTL_SEC:
        _time_drafts.pop(chat_id, None)
        return False
    try:
        minutes = spent.parse_spent(text)
    except spent.SpentError:
        minutes = None
    if not minutes:
        await bot_api.send_message(chat_id, tr("time.err_parse"))
        return True
    _time_drafts.pop(chat_id, None)
    name = await asyncio.to_thread(_save_task_time, d["task"], minutes)
    if name is None:
        await bot_api.send_message(chat_id, tr("bot.task_not_found"))
        return True
    await bot_api.edit_message_text(chat_id, d["message_id"], _time_saved_text(name, minutes))
    return True


