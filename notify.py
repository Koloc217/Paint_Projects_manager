import os
import sys
import json
import httpx
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
from database import SessionLocal, Project, Task, ensure_schema
from common import BASE_DIR, esc, load_config, compute_balance, live_projects, live_tasks
from i18n import tr, tv

try:
    import fcntl          # Linux / Raspberry Pi
except ImportError:       # Windows: защита от гонки недоступна, остаётся проверка по времени
    fcntl = None

# Защита от дублей: автоматическая рассылка не чаще одного раза за MIN_GAP_SEC, даже если её
# запустили сразу два источника (старый cron + встроенный планировщик, два экземпляра приложения).
STATE_FILE = os.path.join(BASE_DIR, ".notify_state.json")
LOCK_FILE = os.path.join(BASE_DIR, ".notify.lock")
MIN_GAP_SEC = 600

load_dotenv(os.path.join(BASE_DIR, ".env"))
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PROXY_URL = os.getenv("PROXY_URL")
transport = httpx.HTTPTransport(proxy=PROXY_URL) if PROXY_URL else None

def send_tg_message(chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    with httpx.Client(transport=transport, timeout=20.0) as client:
        resp = client.post(url, json=payload)
        if resp.status_code != 200:
            print(f"[NOTIFY ERROR] Telegram вернул {resp.status_code}: {resp.text[:300]}")

def claim_run_slot(source, now=None, state_file=STATE_FILE, lock_file=LOCK_FILE, min_gap=MIN_GAP_SEC):
    """Атомарно «занимает» слот рассылки. Возвращает (можно_отправлять, прошлый_запуск)."""
    now = now or datetime.now()
    with open(lock_file, "a+") as lock:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX)      # второй процесс подождёт здесь
        try:
            prev = None
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    prev = json.load(f)
            except (OSError, ValueError):
                prev = None
            if isinstance(prev, dict):
                try:
                    last = datetime.fromisoformat(prev["last_run"])
                except (KeyError, ValueError, TypeError):
                    last = None
                if last and 0 <= (now - last).total_seconds() < min_gap:
                    return False, prev
            tmp = state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"last_run": now.isoformat(timespec="seconds"), "source": source, "pid": os.getpid()}, f)
            os.replace(tmp, state_file)
            return True, prev if isinstance(prev, dict) else None
        finally:
            if fcntl:
                fcntl.flock(lock, fcntl.LOCK_UN)

def to_date(val):
    if not val: return None
    if isinstance(val, datetime): return val.date()
    if isinstance(val, date): return val
    try: return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
    except Exception: return None

def build_monthly_report(db, now, config):
    """Статистика за прошлый месяц (срабатывает 1-го числа или по флагу --monthly)"""
    first_day_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month_end = first_day_this_month - timedelta(seconds=1)
    last_month_start = last_month_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    tasks = live_tasks(db).all()
    projects = live_projects(db).all()

    closed_tasks = [t for t in tasks if t.completed_at and last_month_start <= t.completed_at <= last_month_end]
    closed_projs = [p for p in projects if p.completed_at and last_month_start <= p.completed_at <= last_month_end]
    earned_month = sum((p.token_reward or 0) for p in closed_projs)

    balance, _ = compute_balance(db, config)

    month_name = last_month_start.strftime("%m.%Y")
    lines = [
        f"📊 <b>{tr('notify.monthly_summary')} ({month_name}):</b>\n",
        f"✅ {tr('stats.tasks_closed')}: <b>{len(closed_tasks)}</b>",
        f"🏆 {tr('stats.projects_completed')}: <b>{len(closed_projs)}</b>",
        f"💰 {tr('stats.earned_month')}: <b>+{earned_month} {tr('common.tokens')}</b>",
        f"🏦 {tr('notify.current_balance')}: <b>{balance} {tr('common.tokens')}</b>"
    ]
    if closed_projs:
        lines.append(f"\n<b>{tr('notify.completed_projects')}:</b>")
        for p in closed_projs:
            lines.append(f"• {esc(p.name)} (+{p.token_reward})")
    return "\n".join(lines)

def main():
    ensure_schema()
    config = load_config()
    chat_id = config.get("TG_CHAT_ID")
    is_test = "--test" in sys.argv
    is_monthly_flag = "--monthly" in sys.argv
    is_force = "--force" in sys.argv
    source = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--source=")), "cron/вручную")

    if not chat_id:
        print("Бот не привязан к чату. Отправьте боту /start КОД (код — в приложении: Настройки → Telegram).")
        return

    # DEV_MODE работает как Тихий режим для автоматических пушей
    if not is_test and not is_monthly_flag and config.get("DEV_MODE", False):
        print("Включен DEV_MODE (Тихий режим) — автоматическое уведомление пропущено.")
        return

    # Автоматический запуск — только один раз за слот (тест, --monthly и --force проверку обходят)
    if not is_test and not is_monthly_flag and not is_force:
        ok, prev = claim_run_slot(source)
        if not ok:
            print(f"[NOTIFY] Пропуск дубля: уже отправлялось в {prev.get('last_run')} "
                  f"(источник: {prev.get('source')}, pid {prev.get('pid')}); "
                  f"этот запуск: {source}, pid {os.getpid()}")
            return

    # Время запуска определяет встроенный планировщик (SCHEDULE в config.json).
    now = datetime.now()
    is_friday = now.weekday() == 4
    notify_cfg = config.get("NOTIFY", {})

    db = SessionLocal()
    try:
        # 1. Ежемесячный отчет (1-го числа месяца или по кнопке/команде)
        if is_monthly_flag or (now.day == 1 and notify_cfg.get("monthly_report", True) and not is_test):
            report_text = build_monthly_report(db, now, config)
            send_tg_message(chat_id, report_text)
            if is_monthly_flag:
                return

        projects = {p.id: p for p in live_projects(db).all()}
        tasks = live_tasks(db).all()
        today = now.date()

        overdue = []
        burning = []
        action_tasks = [] # Задачи для кнопок под сообщением

        for t in tasks:
            if str(t.status).lower().strip() in ['закончен', 'завершено', 'completed']:
                continue
            proj = projects.get(t.project_id)
            if not proj or str(proj.status).lower().strip() in ['закончен', 'завершено', 'completed', 'на паузе']:
                continue
            d = to_date(t.deadline)
            if not d:
                continue

            diff = (d - today).days
            stage_info = f" [{esc(tv(proj.stage))}]" if getattr(proj, 'stage', '—') != '—' else ""
            n_post = int(getattr(t, 'postponed', 0) or 0)
            post_info = f" ⏳×{n_post}" if n_post >= 2 else ""

            if diff < 0:
                overdue.append(f"💀 <b>{esc(proj.name)}</b>{stage_info}: {esc(t.task_name)}{post_info} <i>({tr('notify.overdue_by')} {abs(diff)} {tr('common.days_short')})</i>")
                action_tasks.append(t)
            elif diff <= 3:
                burning.append(f"🔥 <b>{esc(proj.name)}</b>{stage_info}: {esc(t.task_name)}{post_info} <i>({tr('notify.left')} {diff} {tr('common.days_short')})</i>")
                action_tasks.append(t)

        # 2. Детектор «Застоя» (Stagnation Alert)
        stagnant = []
        if notify_cfg.get("stagnation_alert", True):
            stag_days = int(notify_cfg.get("stagnation_days", 10))
            for p in projects.values():
                if p.status == "В работе" and p.updated_at:
                    idle_days = (now - p.updated_at).days
                    if idle_days >= stag_days:
                        stagnant.append(f"🕸 <b>{esc(p.name)}</b> [{esc(tv(p.stage))}] — {tr('notify.idle_for')} <b>{idle_days} {tr('common.days_short')}</b>")

        # 3. Пятничный план на выходные
        friday_plan = []
        if (is_friday or is_test) and notify_cfg.get("friday_digest", True):
            for p in projects.values():
                if p.status == "В работе":
                    p_tasks = [t for t in tasks if t.project_id == p.id and str(t.status).lower().strip() not in ['закончен', 'завершено', 'completed']]
                    for t in p_tasks[:3]:
                        friday_plan.append(f"🛠 <b>{esc(p.name)}</b>: {esc(t.task_name)} ({esc(tv(t.status))})")

        if not overdue and not burning and not stagnant and not (is_friday and friday_plan):
            if is_test:
                send_tg_message(chat_id, f"✅ <b>[{tr('notify.test_title')}]</b>\n{tr('notify.test_ok')}")
            else:
                active_projs = [p for p in projects.values() if p.status == "В работе"]
                if active_projs:
                    p = active_projs[0]
                    st = getattr(p, 'stage', '—') or '—'
                    send_tg_message(chat_id, f"✅ <b>{tr('notify.all_on_track')}</b>\n{tr('notify.no_urgent')}\n🎯 {tr('notify.current_focus')}: <b>{esc(p.name)}</b> [{esc(tv(st))}]")
                else:
                    send_tg_message(chat_id, f"✅ <b>{tr('notify.all_on_track')}</b>\n{tr('notify.no_urgent_no_projects')}")
            return

        msg_lines = [f"🚨 <b>{tr('notify.evening_summary')}:</b>\n"]
        if overdue:
            msg_lines.append(f"<b>{tr('deadline.overdue')}:</b>")
            msg_lines.extend(overdue)
            msg_lines.append("")
        if burning:
            msg_lines.append(f"<b>{tr('notify.burning')}:</b>")
            msg_lines.extend(burning)
            msg_lines.append("")
        if stagnant:
            msg_lines.append(f"<b>{tr('notify.stagnation')}:</b>")
            msg_lines.extend(stagnant)
            msg_lines.append("")
        if (is_friday or is_test) and friday_plan:
            msg_lines.append(f"🍻 <b>{tr('notify.weekend_plan')}:</b>")
            msg_lines.extend(friday_plan)
            msg_lines.append("")

        # Формируем инлайн-кнопки (закрытие + перенос на 2 дня; повторный перенос бот прокомментирует)
        keyboard = []
        allow_postpone = notify_cfg.get("allow_postpone", True)
        for t in action_tasks[:4]: # Максимум 4 задачи, чтобы не перегружать пуш
            short = t.task_name[:16] + ".." if len(t.task_name) > 16 else t.task_name
            row = [{"text": f"✅ {short}", "callback_data": f"n_done:{t.id}"}]
            if allow_postpone:
                row.append({"text": f"⏳ {tr('notify.postpone_short')}: {short}", "callback_data": f"t_postpone:{t.id}"})
            keyboard.append(row)

        markup = {"inline_keyboard": keyboard} if keyboard else None
        send_tg_message(chat_id, "\n".join(msg_lines), markup)

    finally:
        db.close()

if __name__ == "__main__":
    main()