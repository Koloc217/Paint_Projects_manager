import os
import time
import asyncio
import traceback
import httpx
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
from database import SessionLocal, Project, Task, ensure_schema
from common import (
    BASE_DIR, esc, load_config, compute_balance, fmt_date,
    is_chat_bound, ensure_pairing_code, check_pairing_code, bind_chat, format_pairing_code,
)
from i18n import tr, tv
from scheduler import spawn_script, seconds_to_next_run

load_dotenv(os.path.join(BASE_DIR, ".env"))

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PROXY_URL = os.getenv("PROXY_URL")

# ==================== ДОСТУП: бот отвечает только привязанному чату ====================

PAIRING_MAX_FAILS = 5          # неверных кодов привязки
PAIRING_WINDOW_SEC = 600       # за это окно, затем попытки игнорируются
_failed_pairing = []           # метки времени неудачных попыток (в памяти)
_ignored_logged = {}           # чат -> когда последний раз писали в лог, чтобы не засорять его


def is_owner(chat_id) -> bool:
    owner = load_config().get("TG_CHAT_ID")
    return owner not in (None, "", 0) and chat_id is not None and str(owner) == str(chat_id)


def _log_ignored(chat_id, what: str):
    now = time.time()
    if now - _ignored_logged.get(chat_id, 0) > 3600:
        _ignored_logged[chat_id] = now
        print(f"[BOT] Игнорирую {what} от стороннего чата {chat_id} (бот привязан к другому чату)")


def pairing_locked() -> bool:
    cutoff = time.time() - PAIRING_WINDOW_SEC
    _failed_pairing[:] = [t for t in _failed_pairing if t > cutoff]
    return len(_failed_pairing) >= PAIRING_MAX_FAILS


def welcome_text() -> str:
    return (
        f"🤖 <b>{tr('bot.connected')}</b>\n\n"
        f"{tr('bot.commands')}:\n"
        f"• /current_task — {tr('bot.cmd_start_desc')}\n"
        f"• /projects — {tr('bot.cmd_all_desc')}\n"
        f"• /stats — {tr('bot.cmd_stats_desc')}\n"
        f"• /test_notify — {tr('bot.cmd_test_desc')}"
    )

# ==================== СЕТЕВОЙ СЛОЙ (ЧЕРЕЗ SOCKS5) ====================

def _sync_tg_request(method: str, endpoint: str, payload: dict = None, params: dict = None, timeout_sec: float = 20.0):
    """Синхронный запрос через HTTPTransport (как в notify.py), стабильно работающий с SOCKS5"""
    transport = httpx.HTTPTransport(proxy=PROXY_URL) if PROXY_URL else None
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{endpoint}"
    timeout = httpx.Timeout(timeout_sec, connect=10.0)
    with httpx.Client(transport=transport, timeout=timeout) as client:
        if method == "GET":
            resp = client.get(url, params=params)
        else:
            resp = client.post(url, json=payload)
        return resp.json()

async def tg_request(method: str, endpoint: str, payload: dict = None, params: dict = None, timeout_sec: float = 20.0):
    """Неблокирующая обертка с ЖЕСТКИМ контролем зависаний"""
    try:
        # Даем потоку на 5 секунд больше, чем таймаут сокета. 
        # Если прокси зависнет намертво, asyncio принудительно убьет ожидание.
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
            print(f"[BOT ERROR] Telegram отклонил sendMessage: {res.get('description')}")
    except Exception as e:
        print(f"[BOT ERROR] Ошибка send_message: {repr(e)}")

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
            print(f"[BOT ERROR] Telegram отклонил editMessageText: {res.get('description')}")
    except Exception as e:
        print(f"[BOT ERROR] Ошибка edit_message_text: {repr(e)}")

async def answer_callback(callback_id: str, text: str = "", show_alert: bool = False):
    payload = {"callback_query_id": callback_id, "text": text, "show_alert": show_alert}
    try:
        await tg_request("POST", "answerCallbackQuery", payload=payload, timeout_sec=10.0)
    except Exception:
        pass

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

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
    
    # Если задача или проект уже закрыты — не пугаем дедлайнами
    if is_done:
        return f"{tr('bot.closed_lc')} ({date_str})"
        
    today = datetime.now().date()
    diff = (d - today).days
    if diff < 0: return f"⚠️ {tr('bot.overdue_lc')} ({date_str})"
    elif diff <= 3: return f"🔥 {tr('bot.burning_lc')} ({date_str})"
    return f"{tr('bot.until')} {date_str}"

def build_project_card(proj, tasks, tokens, total_active_count=1, is_focus=True):
    def task_sort_key(t):
        is_done = str(t.status).lower().strip() in ['закончен', 'завершено', 'completed']
        d = to_date(t.deadline)
        return (1 if is_done else 0, 1 if d is None else 0, d or date(2099, 1, 1))

    tasks.sort(key=task_sort_key)

    stage_str = getattr(proj, 'stage', '—') or '—'
    proj_is_done = str(proj.status).lower().strip() in ['закончен', 'завершено', 'completed']
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
            st_lower = str(t.status).lower().strip()
            is_done = st_lower in ['закончен', 'завершено', 'completed']
            icon = "✅" if is_done else ("🔄" if st_lower == "в процессе" else "⏸️")
            post_mark = " ⏳" if getattr(t, 'postponed', 0) else ""
            lines.append(f"{icon} {esc(t.task_name)}{post_mark} <i>({format_deadline(t.deadline, is_done)})</i>")

            if not is_done:
                short_name = t.task_name[:18] + ".." if len(t.task_name) > 18 else t.task_name
                row = []
                if st_lower != "в процессе":
                    row.append({"text": f"🔄 {tr('bot.btn_start_work')}: {short_name}", "callback_data": f"t_prog:{t.id}:{proj.id}"})
                row.append({"text": f"✅ {tr('bot.btn_done')}: {short_name}", "callback_data": f"t_done:{t.id}:{proj.id}"})
                keyboard.append(row)

    keyboard.append([{"text": f"🎨 {tr('bot.change_stage')} ({tv(stage_str)})", "callback_data": f"stage_menu:{proj.id}"}])
    if total_active_count > 1:
        keyboard.append([{"text": f"📋 {tr('bot.btn_all_active')}", "callback_data": "show_all_proj"}])

    return "\n".join(lines), {"inline_keyboard": keyboard}

def build_current_focus_view(specific_proj_id: str = None):
    db = SessionLocal()
    try:
        config = load_config()
        all_projects = db.query(Project).all()
        all_tasks = db.query(Task).all()
        
        tokens, _ = compute_balance(db, config)

        active_projects = [p for p in all_projects if str(p.status).lower().strip() not in ['закончен', 'завершено', 'completed']]
        if not active_projects:
            return f"🎉 {tr('bot.all_closed')}", None

        # 1. Определяем проект в фокусе
        if specific_proj_id:
            proj = next((p for p in active_projects if p.id == specific_proj_id), active_projects[0])
            is_focus = False
        else:
            prio_rank = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
            active_projects.sort(key=lambda p: (
                0 if p.status == "В работе" else 1,
                prio_rank.get(p.priority, 2),
                to_date(p.estimate) or date(2099, 1, 1)
            ))
            proj = active_projects[0]
            is_focus = True

        tasks = [t for t in all_tasks if t.project_id == proj.id]
        
        # Строим основную карточку фокуса
        text, markup = build_project_card(proj, tasks, tokens, len(active_projects), is_focus)

        # 2. Ищем горящие задачи в ДРУГИХ проектах
        burning_other = []
        today = datetime.now().date()
        
        # Словарь активных проектов, исключая текущий фокус и те, что "На паузе"
        other_active_projs = {
            p.id: p for p in active_projects 
            if p.id != proj.id and str(p.status).lower().strip() != 'на паузе'
        }
        
        for t in all_tasks:
            if t.project_id not in other_active_projs:
                continue
                
            # Пропускаем уже закрытые задачи
            st_lower = str(t.status).lower().strip()
            if st_lower in ['закончен', 'завершено', 'completed']:
                continue
                
            d = to_date(t.deadline)
            if not d:
                continue
                
            diff = (d - today).days
            if diff <= 3:  # Если осталось 3 дня или задача уже просрочена
                burning_other.append((t, other_active_projs[t.project_id], diff))

        # 3. Если есть горящие в других местах — дописываем их списком вниз
        if burning_other:
            burning_other.sort(key=lambda x: x[2]) # Сортируем: сначала самые просроченные
            
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
        stages = ["—", "Печать", "Шлифовка", "Сборка", "Грунт", "Покрас"]
        btns = [{"text": (f"• {tv(st)} •" if st == stage_str else tv(st)), "callback_data": f"p_stage:{proj.id}:{st}"} for st in stages]
        keyboard = [btns[:3], btns[3:], [{"text": f"⬅️ {tr('bot.back_to_project')}", "callback_data": f"open_p:{proj.id}"}]]
        return text, {"inline_keyboard": keyboard}
    finally:
        db.close()

def build_all_projects_view():
    db = SessionLocal()
    try:
        projects = db.query(Project).all()
        active = [p for p in projects if str(p.status).lower().strip() not in ['закончен', 'завершено', 'completed']]
        if not active:
            return tr("project.no_active"), None
        lines = [f"📋 <b>{tr('project.active_list')}:</b>\n"]
        keyboard = []
        for p in active:
            st = getattr(p, 'stage', '—')
            lines.append(f"• <b>{esc(p.name)}</b> — {esc(tv(p.status))} [{esc(tv(st))}] ({priority_badge(p.priority)})")
            keyboard.append([{"text": f"🔍 {p.name}", "callback_data": f"open_p:{p.id}"}])
        return "\n".join(lines), {"inline_keyboard": keyboard}
    finally:
        db.close()

def build_current_month_stats():
    """Статистика выкладки за ТЕКУЩИЙ месяц по команде /stats"""
    db = SessionLocal()
    try:
        now = datetime.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        tasks = db.query(Task).all()
        projects = db.query(Project).all()
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

# ==================== КОМАНДЫ И КОЛЛБЭКИ ====================

async def cmd_test_notify(chat_id: int):
    spawn_script("notify.py", ["--test"])
    await send_message(chat_id, f"🧪 {tr('bot.test_started')}...")

async def handle_callback(cb: dict):
    cb_id = cb["id"]
    data = cb.get("data", "")
    msg = cb.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    message_id = msg.get("message_id")
    now = datetime.now()

    db = SessionLocal()
    try:
        if data.startswith("t_done:"):
            parts = data.split(":")
            task_id, proj_id = parts[1], (parts[2] if len(parts) > 2 else None)
            task = db.query(Task).filter(Task.id == task_id).first()
            if task:
                task.status = "Закончен"
                task.completed_at = now
                proj = db.query(Project).filter(Project.id == task.project_id).first()
                if proj: proj.updated_at = now
                db.commit()
                await answer_callback(cb_id, f"✅ {tr('bot.task_closed')}")
            text, markup = build_current_focus_view(proj_id)
            await edit_message_text(chat_id, message_id, text, markup)

        elif data.startswith("n_done:"):
            # Закрытие задачи прямо из пуш-уведомления
            task_id = data.split(":", 1)[1]
            task = db.query(Task).filter(Task.id == task_id).first()
            if task:
                task.status = "Закончен"
                task.completed_at = now
                proj = db.query(Project).filter(Project.id == task.project_id).first()
                if proj: proj.updated_at = now
                db.commit()
                await answer_callback(cb_id, f"✅ {tr('bot.task_closed_named', name=task.task_name)}", show_alert=True)
                text, markup = build_current_focus_view(task.project_id)
                await edit_message_text(chat_id, message_id, text, markup)

        elif data.startswith("t_postpone:"):
            # Одноразовый перенос дедлайна на +2 дня из пуша
            task_id = data.split(":", 1)[1]
            task = db.query(Task).filter(Task.id == task_id).first()
            if not task:
                await answer_callback(cb_id, tr("bot.task_not_found"))
                return
            if getattr(task, 'postponed', 0) >= 1:
                await answer_callback(cb_id, f"⚠️ {tr('bot.already_postponed')}", show_alert=True)
                return
            base_date = to_date(task.deadline) or now.date()
            if base_date < now.date():
                base_date = now.date()
            new_deadline = base_date + timedelta(days=2)
            task.deadline = datetime.combine(new_deadline, datetime.min.time())
            task.postponed = 1
            proj = db.query(Project).filter(Project.id == task.project_id).first()
            if proj: proj.updated_at = now
            db.commit()
            await answer_callback(cb_id, f"⏳ {tr('bot.postponed', date=fmt_date(new_deadline))}", show_alert=True)
            text, markup = build_current_focus_view(task.project_id)
            await edit_message_text(chat_id, message_id, text, markup)

        elif data.startswith("t_prog:"):
            parts = data.split(":")
            task_id, proj_id = parts[1], (parts[2] if len(parts) > 2 else None)
            task = db.query(Task).filter(Task.id == task_id).first()
            if task:
                task.status = "В процессе"
                proj = db.query(Project).filter(Project.id == task.project_id).first()
                if proj: proj.updated_at = now
                db.commit()
                await answer_callback(cb_id, f"🔄 {tr('bot.task_in_progress')}")
            text, markup = build_current_focus_view(proj_id)
            await edit_message_text(chat_id, message_id, text, markup)

        elif data.startswith("stage_menu:"):
            proj_id = data.split(":", 1)[1]
            await answer_callback(cb_id)
            text, markup = build_stage_menu_view(proj_id)
            await edit_message_text(chat_id, message_id, text, markup)

        elif data.startswith("p_stage:"):
            _, proj_id, new_stage = data.split(":", 2)
            proj = db.query(Project).filter(Project.id == proj_id).first()
            if proj:
                proj.stage = new_stage
                proj.updated_at = now
                db.commit()
                await answer_callback(cb_id, f"🎨 {tr('project.stage')}: {tv(new_stage)}")
            text, markup = build_current_focus_view(proj_id)
            await edit_message_text(chat_id, message_id, text, markup)

        elif data == "show_all_proj":
            await answer_callback(cb_id)
            text, markup = build_all_projects_view()
            await edit_message_text(chat_id, message_id, text, markup)

        elif data.startswith("open_p:"):
            proj_id = data.split(":", 1)[1]
            await answer_callback(cb_id)
            text, markup = build_current_focus_view(proj_id)
            await edit_message_text(chat_id, message_id, text, markup)
    except Exception as e:
        print(f"[CALLBACK ERROR]: {repr(e)}")
        traceback.print_exc()
    finally:
        db.close()

async def handle_foreign_message(chat: dict, text: str):
    """Сообщение не от владельца. Привязанный бот молчит; непривязанный принимает только «/start КОД»."""
    chat_id = chat.get("id")
    if is_chat_bound():
        _log_ignored(chat_id, "сообщение")
        return
    if not text.startswith("/start"):
        return
    parts = text.split(maxsplit=1)
    payload = parts[1] if len(parts) > 1 else ""
    if pairing_locked():
        _log_ignored(chat_id, "попытку привязки (слишком много неверных кодов)")
        return
    if chat.get("type") != "private":
        await send_message(chat_id, tr("bot.private_only"))
        return
    if payload and check_pairing_code(payload):
        bind_chat(chat_id)
        _failed_pairing.clear()
        print(f"[BOT] Бот привязан к чату {chat_id}")
        await send_message(chat_id, f"✅ {tr('bot.chat_linked')}")
        await send_message(chat_id, welcome_text())
        return
    if payload:
        _failed_pairing.append(time.time())
    await send_message(chat_id, tr("bot.not_linked"))


async def process_update(item: dict):
    """Один апдейт Telegram: сообщение или нажатие кнопки."""
    if "callback_query" in item:
        cb = item["callback_query"]
        cb_chat_id = ((cb.get("message") or {}).get("chat") or {}).get("id")
        if not is_owner(cb_chat_id):
            _log_ignored(cb_chat_id, "нажатие кнопки")
            return
        await handle_callback(cb)
        return

    msg = item.get("message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    if not chat_id or not text:
        return

    if not is_owner(chat_id):
        await handle_foreign_message(chat, text)
        return

    if text.startswith("/start"):
        await send_message(chat_id, welcome_text())
    elif text.startswith("/current_task"):
        view_text, markup = build_current_focus_view()
        await send_message(chat_id, view_text, markup)
    elif text.startswith("/projects"):
        view_text, markup = build_all_projects_view()
        await send_message(chat_id, view_text, markup)
    elif text.startswith("/stats"):
        await send_message(chat_id, build_current_month_stats())
    elif text.startswith("/test_notify"):
        await cmd_test_notify(chat_id)


async def start_bot_polling():
    ensure_schema()
    if not BOT_TOKEN:
        print("⚠️ TELEGRAM_BOT_TOKEN не задан в .env!")
        return
    code = ensure_pairing_code()
    if code:
        print(f"🔗 Бот не привязан к чату. Отправьте ему в личные сообщения: /start {format_pairing_code(code)} "
              f"(код также виден в приложении: Настройки → Telegram)")

    while True:  # ВНЕШНИЙ ЦИКЛ: Принудительное пересоздание сессии
        try:
            await tg_request("POST", "deleteWebhook", payload={"drop_pending_updates": False}, timeout_sec=15.0)
            print(f"✅ [{datetime.now().strftime('%H:%M:%S')}] Telegram-бот начал новую сессию!")
        except Exception as e:
            print(f"⚠️ Ошибка подключения к Telegram при старте: {repr(e)}")

        offset = 0
        session_start = datetime.now()

        while True:  # ВНУТРЕННИЙ ЦИКЛ: Обычный поллинг
            now = datetime.now()
            
            # 1. Принудительный рестарт каждые 3 часа (10800 секунд)
            if (now - session_start).total_seconds() > 10800:
                print(f"🔄 [{now.strftime('%H:%M:%S')}] Плановый реконнект (прошло 3 часа)...")
                break
            
            # 2. Свежая сессия за ~10 минут до запуска уведомлений по расписанию
            to_next = seconds_to_next_run()
            if to_next is not None and 0 < to_next <= 600 and (now - session_start).total_seconds() > 660:
                print(f"🔄 [{now.strftime('%H:%M:%S')}] Плановый реконнект перед пушами по расписанию...")
                break

            try:
                data = await tg_request("GET", "getUpdates", params={"offset": offset, "timeout": 10}, timeout_sec=20.0)

                if not data.get("ok"):
                    await asyncio.sleep(3)
                    continue

                for item in data.get("result", []):
                    offset = item["update_id"] + 1

                    await process_update(item)

            except asyncio.CancelledError:
                return  # Корректно завершаем при остановке системной службы
            except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError):
                continue
            except Exception as e:
                print(f"[BOT POLLING ERROR]: {repr(e)}")
                await asyncio.sleep(5)