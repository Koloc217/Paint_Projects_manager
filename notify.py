import os
import sys
import json
import httpx
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
from database import SessionLocal, Project, Task, ensure_schema
from common import (
    BASE_DIR, esc, to_tokens, load_config, compute_balance, live_projects, live_tasks,
    is_done, STATUS_IN_WORK, STATUS_ON_HOLD, NO_STAGE, FileLock, clip_lines,
)
from i18n import tr, tv
import tgproxy
import vacation

# Duplicate protection: the automatic digest goes out at most once per MIN_GAP_SEC, even if two sources start it
# at the same time (an old cron entry + the built-in scheduler, two copies of the app).
STATE_FILE = os.path.join(BASE_DIR, ".notify_state.json")
LOCK_FILE = os.path.join(BASE_DIR, ".notify.lock")
MIN_GAP_SEC = 600
MAX_BUTTONS = 4           # task buttons under the message, so the push stays readable
MAX_SECTION_ITEMS = 12    # lines per section (overdue, burning, ...); the rest becomes "…and N more"

load_dotenv(os.path.join(BASE_DIR, ".env"))
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


def send_tg_message(chat_id, text, reply_markup=None) -> bool:
    """Sends one message to the linked chat. Returns True if Telegram accepted it."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    # The proxy is optional: Settings → Telegram or PROXY_URL in .env (see tgproxy.py)
    try:
        with httpx.Client(transport=tgproxy.transport(), timeout=20.0) as client:
            resp = client.post(url, json=payload)
    except httpx.HTTPError as e:
        print(f"[NOTIFY ERROR] no connection to Telegram ({tgproxy.describe()}): {e.__class__.__name__}")
        return False
    if resp.status_code != 200:
        print(f"[NOTIFY ERROR] Telegram returned {resp.status_code}: {resp.text[:300]}")
        return False
    return True


def claim_run_slot(source, now=None, state_file=STATE_FILE, lock_file=LOCK_FILE, min_gap=MIN_GAP_SEC):
    """Atomically "takes" the digest slot. Returns (may_send, previous_run)."""
    now = now or datetime.now()
    with FileLock(lock_file):
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


def more_line(n: int) -> str:
    return f"<i>… {tr('common.and_more', n=n)}</i>"


def section(title: str, items: list) -> list:
    """A digest section: the title, at most MAX_SECTION_ITEMS lines and an empty line after it."""
    if not items:
        return []
    shown = items[:MAX_SECTION_ITEMS]
    if len(items) > len(shown):
        shown.append(more_line(len(items) - len(shown)))
    return [title, *shown, ""]


def to_date(val):
    if not val:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _deadline_key(t):
    d = to_date(t.deadline)
    return (d is None, d or date.max)


def build_monthly_report(db, now, config):
    """Statistics for the previous month (sent on the 1st or with the --monthly flag)."""
    first_day_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month_end = first_day_this_month - timedelta(seconds=1)
    last_month_start = last_month_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    tasks = live_tasks(db).filter(Task.completed_at >= last_month_start, Task.completed_at <= last_month_end).all()
    projects = live_projects(db).filter(Project.completed_at >= last_month_start,
                                        Project.completed_at <= last_month_end).all()
    closed_projs = [p for p in projects if is_done(p.status)]
    earned_month = sum(to_tokens(p.token_reward) for p in closed_projs)

    balance, _ = compute_balance(db, config)

    month_name = last_month_start.strftime("%m.%Y")
    lines = [
        f"📊 <b>{tr('notify.monthly_summary')} ({month_name}):</b>\n",
        f"✅ {tr('stats.tasks_closed')}: <b>{len(tasks)}</b>",
        f"🏆 {tr('stats.projects_completed')}: <b>{len(closed_projs)}</b>",
        f"💰 {tr('stats.earned_month')}: <b>+{earned_month} {tr('common.tokens')}</b>",
        f"🏦 {tr('notify.current_balance')}: <b>{balance} {tr('common.tokens')}</b>"
    ]
    if closed_projs:
        lines.append("")
        lines += section(f"<b>{tr('notify.completed_projects')}:</b>", [f"• {esc(p.name)}" for p in closed_projs])
    return "\n".join(clip_lines(lines, more=more_line)).rstrip()


def main() -> int:
    ensure_schema()
    config = load_config()
    chat_id = config.get("TG_CHAT_ID")
    is_test = "--test" in sys.argv
    is_monthly_flag = "--monthly" in sys.argv
    is_force = "--force" in sys.argv
    source = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--source=")), "cron/manual")

    if not BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN is not set in .env; nothing to send.")
        return 1
    if not chat_id:
        print("The bot is not linked to a chat. Send the bot /start CODE (the code is in the app: Settings → Telegram).")
        return 0

    # DEV_MODE works as a silent mode for automatic pushes
    if not is_test and not is_monthly_flag and config.get("DEV_MODE", False):
        print("DEV_MODE (silent mode) is on: the automatic notification is skipped.")
        return 0

    # Vacation: automatic pushes (including the monthly report) are off for its duration
    if not is_test and not is_monthly_flag and vacation.is_active(config):
        print(f"On vacation until {vacation.state(config)['until']}: the automatic notification is skipped.")
        return 0

    # An automatic run takes the slot only once (test, --monthly and --force skip the check)
    if not is_test and not is_monthly_flag and not is_force:
        ok, prev = claim_run_slot(source)
        if not ok:
            print(f"[NOTIFY] Duplicate skipped: already sent at {prev.get('last_run')} "
                  f"(source: {prev.get('source')}, pid {prev.get('pid')}); "
                  f"this run: {source}, pid {os.getpid()}")
            return 0

    # The run time is decided by the built-in scheduler (SCHEDULE in config.json).
    now = datetime.now()
    is_friday = now.weekday() == 4
    notify_cfg = config.get("NOTIFY", {})
    sent_ok = True

    db = SessionLocal()
    try:
        # 1. Monthly report (on the 1st of the month, or by button / command)
        if is_monthly_flag or (now.day == 1 and notify_cfg.get("monthly_report", True) and not is_test):
            sent_ok &= send_tg_message(chat_id, build_monthly_report(db, now, config))
            if is_monthly_flag:
                return 0 if sent_ok else 1

        projects = {p.id: p for p in live_projects(db).all()}
        tasks = sorted(live_tasks(db).all(), key=_deadline_key)    # most overdue first
        today = now.date()

        overdue = []
        burning = []
        action_tasks = []  # tasks that get buttons under the message

        for t in tasks:
            if is_done(t.status):
                continue
            proj = projects.get(t.project_id)
            if not proj or is_done(proj.status) or str(proj.status).strip() == STATUS_ON_HOLD:
                continue
            d = to_date(t.deadline)
            if not d:
                continue

            diff = (d - today).days
            stage_info = f" [{esc(tv(proj.stage))}]" if (proj.stage or NO_STAGE) != NO_STAGE else ""
            n_post = int(t.postponed or 0)
            post_info = f" ⏳×{n_post}" if n_post >= 2 else ""

            if diff < 0:
                overdue.append(f"💀 <b>{esc(proj.name)}</b>{stage_info}: {esc(t.task_name)}{post_info} <i>({tr('notify.overdue_by')} {abs(diff)} {tr('common.days_short')})</i>")
                action_tasks.append(t)
            elif diff <= 3:
                burning.append(f"🔥 <b>{esc(proj.name)}</b>{stage_info}: {esc(t.task_name)}{post_info} <i>({tr('notify.left')} {diff} {tr('common.days_short')})</i>")
                action_tasks.append(t)

        # 2. Stagnation detector: projects "in work" without progress for too long
        stagnant = []
        if notify_cfg.get("stagnation_alert", True):
            stag_days = int(notify_cfg.get("stagnation_days", 10))
            for p in projects.values():
                if p.status == STATUS_IN_WORK and p.updated_at:
                    idle_days = (now - p.updated_at).days
                    if idle_days >= stag_days:
                        stagnant.append(f"🕸 <b>{esc(p.name)}</b> [{esc(tv(p.stage))}] — {tr('notify.idle_for')} <b>{idle_days} {tr('common.days_short')}</b>")

        # 3. Friday plan for the weekend: up to three open tasks of every project "in work", nearest deadline first
        # ... and projects "in work" without a single open task: there is nothing to plan for them, worth a look
        friday_plan = []
        no_tasks = []
        weekly = (is_friday or is_test) and notify_cfg.get("friday_digest", True)
        if weekly:
            for p in projects.values():
                if p.status == STATUS_IN_WORK:
                    p_tasks = [t for t in tasks if t.project_id == p.id and not is_done(t.status)]
                    for t in p_tasks[:3]:
                        friday_plan.append(f"🛠 <b>{esc(p.name)}</b>: {esc(t.task_name)} ({esc(tv(t.status))})")
                    if not p_tasks:
                        no_tasks.append(f"📭 <b>{esc(p.name)}</b>")

        if not overdue and not burning and not stagnant and not (weekly and (friday_plan or no_tasks)):
            if is_test:
                sent_ok &= send_tg_message(chat_id, f"✅ <b>[{tr('notify.test_title')}]</b>\n{tr('notify.test_ok')}")
            else:
                active_projs = [p for p in projects.values() if p.status == STATUS_IN_WORK]
                if active_projs:
                    p = active_projs[0]
                    st = p.stage or NO_STAGE
                    sent_ok &= send_tg_message(chat_id, f"✅ <b>{tr('notify.all_on_track')}</b>\n{tr('notify.no_urgent')}\n🎯 {tr('notify.current_focus')}: <b>{esc(p.name)}</b> [{esc(tv(st))}]")
                else:
                    sent_ok &= send_tg_message(chat_id, f"✅ <b>{tr('notify.all_on_track')}</b>\n{tr('notify.no_urgent_no_projects')}")
            return 0 if sent_ok else 1

        msg_lines = [f"🚨 <b>{tr('notify.evening_summary')}:</b>", ""]
        msg_lines += section(f"<b>{tr('deadline.overdue')}:</b>", overdue)
        msg_lines += section(f"<b>{tr('notify.burning')}:</b>", burning)
        msg_lines += section(f"<b>{tr('notify.stagnation')}:</b>", stagnant)
        if weekly:
            msg_lines += section(f"🍻 <b>{tr('notify.weekend_plan')}:</b>", friday_plan)
            msg_lines += section(f"<b>{tr('notify.no_open_tasks')}:</b>", no_tasks)
        # Each section is capped, but many projects together could still pass Telegram's limit: then the message
        # would not arrive at all, so the tail is cut by whole lines
        msg_lines = clip_lines(msg_lines, more=more_line)

        # Inline buttons: close the task + move it by 2 days (the bot comments on repeated postponing)
        keyboard = []
        allow_postpone = notify_cfg.get("allow_postpone", True)
        for t in action_tasks[:MAX_BUTTONS]:
            short = t.task_name[:16] + ".." if len(t.task_name) > 16 else t.task_name
            row = [{"text": f"✅ {short}", "callback_data": f"n_done:{t.id}"}]
            if allow_postpone:
                row.append({"text": f"⏳ {tr('notify.postpone_short')}: {short}", "callback_data": f"t_postpone:{t.id}"})
            keyboard.append(row)

        markup = {"inline_keyboard": keyboard} if keyboard else None
        sent_ok &= send_tg_message(chat_id, "\n".join(msg_lines), markup)
        return 0 if sent_ok else 1

    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
