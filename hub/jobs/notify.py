import os
import sys
import json
import threading
import httpx
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from dotenv import load_dotenv
from hub.core.database import SessionLocal, Project, Task, ensure_schema
from hub.core.common import (
    DATA_DIR, esc, to_date, to_tokens, load_config, compute_balance, live_projects, live_tasks,
    is_done, STATUS_IN_WORK, STATUS_ON_HOLD, NO_STAGE, FileLock, clip_lines,
)
from hub.core.i18n import tr, tv
from hub.bot import tgproxy
from hub.core import vacation
from hub.core import activity
from hub.bot import tgclean

# Duplicate protection: the automatic digest goes out at most once per MIN_GAP_SEC, even if two sources start it
# at the same time (an old cron entry + the built-in scheduler, two copies of the app).
STATE_FILE = os.path.join(DATA_DIR, ".notify_state.json")
LOCK_FILE = os.path.join(DATA_DIR, ".notify.lock")
MIN_GAP_SEC = 600
MAX_BUTTONS = 4           # task buttons under the message, so the push stays readable
MAX_SECTION_ITEMS = 12    # lines per section (overdue, burning, ...); the rest becomes "…and N more"
# httpx applies no timeout to the SOCKS5 handshake: a proxy that accepts the connection but never answers it hangs
# the request for good (the scheduler then kills the script after 3 minutes and nothing reaches the chat).
# So every request also gets a hard limit of its own, and a hung one is tried once more on a fresh connection.
REQUEST_DEADLINE_SEC = 30
REQUEST_ATTEMPTS = 2

load_dotenv(os.path.join(DATA_DIR, ".env"))
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


def _post_once(url, payload):
    with httpx.Client(transport=tgproxy.transport(), timeout=20.0) as client:
        return client.post(url, json=payload)


def tg_post(url, payload, deadline=None, attempts=None, post=None):
    """POST to Telegram with a hard time limit per attempt (see REQUEST_DEADLINE_SEC).
    The request runs in a daemon thread: a hung one is abandoned, and the process can still exit.
    Returns the response; raises httpx.HTTPError on a network error, TimeoutError if every attempt hung."""
    deadline = REQUEST_DEADLINE_SEC if deadline is None else deadline
    attempts = REQUEST_ATTEMPTS if attempts is None else attempts
    post = post or _post_once
    for attempt in range(1, attempts + 1):
        box = {}

        def run():
            try:
                box["resp"] = post(url, payload)
            except BaseException as e:          # handed over to the caller's thread
                box["err"] = e

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(deadline)
        if "resp" in box:
            return box["resp"]
        if "err" in box:
            raise box["err"]
        print(f"[NOTIFY] Telegram did not answer in {deadline} s ({tgproxy.describe()}), attempt {attempt} of {attempts}")
    raise TimeoutError(f"no answer from Telegram in {deadline} s")


def send_tg_message(chat_id, text, reply_markup=None, kind="digest") -> bool:
    """Sends one message to the linked chat. Returns True if Telegram accepted it.
    kind is how the chat cleanup treats it (tgclean.py): digests are cleaned up automatically, a report is not."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    # The proxy is optional: Settings → Telegram or PROXY_URL in .env (see tgproxy.py)
    try:
        resp = tg_post(url, payload)
    except (httpx.HTTPError, TimeoutError) as e:
        print(f"[NOTIFY ERROR] no connection to Telegram ({tgproxy.describe()}): {e.__class__.__name__}")
        return False
    if resp.status_code != 200:
        print(f"[NOTIFY ERROR] Telegram returned {resp.status_code}: {resp.text[:300]}")
        return False
    try:
        tgclean.record(chat_id, tgclean.ids_from_result(resp.json()), kind)
    except ValueError:
        pass
    if kind == "digest":
        cleanup_digests(chat_id)
    return True


def cleanup_digests(chat_id) -> None:
    """Removes older digests right after a new one (TG_CLEANUP mode). The bot also checks every few minutes,
    but hub.jobs.notify runs as its own process, so without this "latest only" left the old digest for up to 10 minutes."""
    try:
        ids = tgclean.digest_candidates(chat_id)
        if not ids:
            return
        for batch in tgclean.batches(ids):
            resp = tg_post(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessages",
                           {"chat_id": chat_id, "message_ids": batch}, attempts=1)
            if resp.status_code != 200:
                print(f"[NOTIFY] deleteMessages: Telegram returned {resp.status_code}: {resp.text[:200]}")
        tgclean.forget(chat_id, ids)          # deleted, or too old to delete anyway
    except Exception as e:                     # cleanup must never break sending
        print(f"[NOTIFY] digest cleanup failed: {e.__class__.__name__}")


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


def _deadline_key(t):
    d = to_date(t.deadline)
    return (d is None, d or date.max)


def shame_snark(n: int) -> str:
    """A short comment on the number of postponements (the more, the snarkier)."""
    for limit, key in ((1, "shame.snark_1"), (3, "shame.snark_3"), (6, "shame.snark_6")):
        if n <= limit:
            return tr(key)
    return tr("shame.snark_many")


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
    shame = activity.count_postponements(db, last_month_start, first_day_this_month)
    if shame:
        lines.append(f"🐌 {tr('shame.month_line', n=shame)}")
    if closed_projs:
        lines.append("")
        lines += section(f"<b>{tr('notify.completed_projects')}:</b>", [f"• {esc(p.name)}" for p in closed_projs])
    return "\n".join(clip_lines(lines, more=more_line)).rstrip()


@dataclass
class Options:
    """What this run was asked to do (command-line flags)."""
    test: bool = False        # --test: a test push; ignores the "all quiet" rule and the once-per-slot check
    monthly: bool = False     # --monthly: only the monthly report
    now: bool = False         # --now: the "Send now" button, a regular digest on request: ignores the silent mode,
                              # the vacation and the once-per-slot check
    force: bool = False       # --force (or --now): skip the once-per-slot check
    source: str = "cron/manual"

    @property
    def automatic(self) -> bool:
        """A run started by the timer rather than by a person: silent mode and vacation apply to it."""
        return not (self.test or self.monthly or self.now)


def parse_options(argv) -> Options:
    now = "--now" in argv
    return Options(
        test="--test" in argv, monthly="--monthly" in argv, now=now, force="--force" in argv or now,
        source=next((a.split("=", 1)[1] for a in argv if a.startswith("--source=")), "cron/manual"),
    )


def skip_reason(opts: Options, config: dict):
    """Why an automatic run must send nothing (silent mode, vacation), or None.
    A run asked for by a person (--test, --now, --monthly) always goes through."""
    if not opts.automatic:
        return None
    if config.get("DEV_MODE", False):
        return "DEV_MODE (silent mode) is on: the automatic notification is skipped."
    if vacation.is_active(config):
        return f"On vacation until {vacation.state(config)['until']}: the automatic notification is skipped."
    return None


@dataclass
class Digest:
    """What there is to say: lines per section, the tasks that get buttons, and the live projects."""
    projects: dict
    overdue: list = field(default_factory=list)
    burning: list = field(default_factory=list)
    action_tasks: list = field(default_factory=list)     # tasks that get buttons under the message
    stagnant: list = field(default_factory=list)
    friday_plan: list = field(default_factory=list)
    no_tasks: list = field(default_factory=list)
    weekly: bool = False

    @property
    def has_content(self) -> bool:
        return bool(self.overdue or self.burning or self.stagnant or (self.weekly and (self.friday_plan or self.no_tasks)))


def _stage_info(proj) -> str:
    return f" [{esc(tv(proj.stage))}]" if proj and (proj.stage or NO_STAGE) != NO_STAGE else ""


def _collect_deadlines(digest: Digest, tasks: list, today) -> None:
    """Overdue and burning (up to 3 days left) tasks of projects that are in play, plus general tasks."""
    for t in tasks:
        if is_done(t.status):
            continue
        proj = digest.projects.get(t.project_id)
        if t.project_id is None:
            proj = None                    # a general task (no project): shown under the "General" label
        elif not proj or is_done(proj.status) or str(proj.status).strip() == STATUS_ON_HOLD:
            continue
        d = to_date(t.deadline)
        if not d:
            continue

        diff = (d - today).days
        label = esc(proj.name) if proj else f"🧰 {esc(tr('general.label'))}"
        n_post = int(t.postponed or 0)
        post_info = f" ⏳×{n_post}" if n_post >= 2 else ""
        head = f"<b>{label}</b>{_stage_info(proj)}: {esc(t.task_name)}{post_info}"

        if diff < 0:
            digest.overdue.append(f"💀 {head} <i>({tr('notify.overdue_by')} {abs(diff)} {tr('common.days_short')})</i>")
            digest.action_tasks.append(t)
        elif diff <= 3:
            digest.burning.append(f"🔥 {head} <i>({tr('notify.left')} {diff} {tr('common.days_short')})</i>")
            digest.action_tasks.append(t)


def _collect_stagnant(digest: Digest, now: datetime, notify_cfg: dict) -> None:
    """Stagnation detector: projects "in work" without progress for too long."""
    if not notify_cfg.get("stagnation_alert", True):
        return
    stag_days = int(notify_cfg.get("stagnation_days", 10))
    for p in digest.projects.values():
        if p.status == STATUS_IN_WORK and p.updated_at:
            idle_days = (now - p.updated_at).days
            if idle_days >= stag_days:
                digest.stagnant.append(f"🕸 <b>{esc(p.name)}</b> [{esc(tv(p.stage))}] — {tr('notify.idle_for')} <b>{idle_days} {tr('common.days_short')}</b>")


def _collect_weekend_plan(digest: Digest, tasks: list) -> None:
    """Up to three open tasks of every project "in work", nearest deadline first, then the three nearest general
    tasks; and projects "in work" without a single open task: nothing to plan for them, worth a look."""
    for p in digest.projects.values():
        if p.status == STATUS_IN_WORK:
            p_tasks = [t for t in tasks if t.project_id == p.id and not is_done(t.status)]
            for t in p_tasks[:3]:
                digest.friday_plan.append(f"🛠 <b>{esc(p.name)}</b>: {esc(t.task_name)} ({esc(tv(t.status))})")
            if not p_tasks:
                digest.no_tasks.append(f"📭 <b>{esc(p.name)}</b>")
    general = [t for t in tasks if t.project_id is None and not is_done(t.status)]
    for t in general[:3]:
        digest.friday_plan.append(f"🧰 <b>{esc(tr('general.label'))}</b>: {esc(t.task_name)} ({esc(tv(t.status))})")


def collect_digest(db, now: datetime, opts: Options, notify_cfg: dict) -> Digest:
    digest = Digest(projects={p.id: p for p in live_projects(db).all()})
    tasks = sorted(live_tasks(db).all(), key=_deadline_key)    # most overdue first
    digest.weekly = bool((now.weekday() == 4 or opts.test) and notify_cfg.get("friday_digest", True))
    _collect_deadlines(digest, tasks, now.date())
    _collect_stagnant(digest, now, notify_cfg)
    if digest.weekly:
        _collect_weekend_plan(digest, tasks)
    return digest


def render_digest(db, digest: Digest, now: datetime) -> str:
    lines = [f"🚨 <b>{tr('notify.evening_summary')}:</b>", ""]
    lines += section(f"<b>{tr('deadline.overdue')}:</b>", digest.overdue)
    lines += section(f"<b>{tr('notify.burning')}:</b>", digest.burning)
    lines += section(f"<b>{tr('notify.stagnation')}:</b>", digest.stagnant)
    if digest.weekly:
        lines += section(f"🍻 <b>{tr('notify.weekend_plan')}:</b>", digest.friday_plan)
        lines += section(f"<b>{tr('notify.no_open_tasks')}:</b>", digest.no_tasks)
        # shame metric of the week: deadlines moved later (text only, no token penalty)
        shame = activity.count_postponements(db, now - timedelta(days=7))
        if shame:
            lines += [f"🐌 <b>{tr('shame.week_line', n=shame)}</b> {esc(shame_snark(shame))}", ""]
    # Each section is capped, but many projects together could still pass Telegram's limit: then the message
    # would not arrive at all, so the tail is cut by whole lines
    return "\n".join(clip_lines(lines, more=more_line))


def build_keyboard(action_tasks: list, allow_postpone: bool):
    """Inline buttons: close the task + move it by 2 days (the bot comments on repeated postponing)."""
    rows = []
    for t in action_tasks[:MAX_BUTTONS]:
        short = t.task_name[:16] + ".." if len(t.task_name) > 16 else t.task_name
        row = [{"text": f"✅ {short}", "callback_data": f"n_done:{t.id}"}]
        if allow_postpone:
            row.append({"text": f"⏳ {tr('notify.postpone_short')}: {short}", "callback_data": f"t_postpone:{t.id}"})
        rows.append(row)
    return {"inline_keyboard": rows} if rows else None


def send_all_clear(chat_id, digest: Digest, opts: Options) -> bool:
    """Nothing to report: a test push confirms the link works, a regular run says everything is on track."""
    if opts.test:
        return send_tg_message(chat_id, f"✅ <b>[{tr('notify.test_title')}]</b>\n{tr('notify.test_ok')}")
    active = [p for p in digest.projects.values() if p.status == STATUS_IN_WORK]
    if active:
        p = active[0]
        st = p.stage or NO_STAGE
        return send_tg_message(chat_id, f"✅ <b>{tr('notify.all_on_track')}</b>\n{tr('notify.no_urgent')}\n🎯 {tr('notify.current_focus')}: <b>{esc(p.name)}</b> [{esc(tv(st))}]")
    return send_tg_message(chat_id, f"✅ <b>{tr('notify.all_on_track')}</b>\n{tr('notify.no_urgent_no_projects')}")


def main() -> int:
    ensure_schema()
    config = load_config()
    chat_id = config.get("TG_CHAT_ID")
    opts = parse_options(sys.argv)

    if not BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN is not set in .env; nothing to send.")
        return 1
    if not chat_id:
        print("The bot is not linked to a chat. Send the bot /start CODE (the code is in the app: Settings → Telegram).")
        return 0

    reason = skip_reason(opts, config)
    if reason:
        print(reason)
        return 0

    # An automatic run takes the slot only once (test, --monthly and --force skip the check)
    # The run time is decided by the built-in scheduler (SCHEDULE in config.json).
    if not (opts.test or opts.monthly or opts.force):
        ok, prev = claim_run_slot(opts.source)
        if not ok:
            print(f"[NOTIFY] Duplicate skipped: already sent at {prev.get('last_run')} "
                  f"(source: {prev.get('source')}, pid {prev.get('pid')}); "
                  f"this run: {opts.source}, pid {os.getpid()}")
            return 0

    now = datetime.now()
    notify_cfg = config.get("NOTIFY", {})
    sent_ok = True

    db = SessionLocal()
    try:
        # 1. Monthly report (on the 1st of the month, or by button / command)
        if opts.monthly or (now.day == 1 and notify_cfg.get("monthly_report", True) and not opts.test):
            sent_ok &= send_tg_message(chat_id, build_monthly_report(db, now, config), kind="report")
            if opts.monthly:
                return 0 if sent_ok else 1

        # 2. The digest: deadlines, stagnation, the Friday plan
        digest = collect_digest(db, now, opts, notify_cfg)
        if not digest.has_content:
            sent_ok &= send_all_clear(chat_id, digest, opts)
        else:
            markup = build_keyboard(digest.action_tasks, notify_cfg.get("allow_postpone", True))
            sent_ok &= send_tg_message(chat_id, render_digest(db, digest, now), markup)
        return 0 if sent_ok else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
