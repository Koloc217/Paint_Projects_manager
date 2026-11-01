"""Texts and keyboards of the bot: project cards, the focus view, lists, monthly stats; lookups shared by the
bot modules (live project/task, "touch" a project).
"""
import os
from datetime import datetime, date
from database import SessionLocal, Project, Task, Reference
import progress
from common import (
    esc, to_tokens, clip_lines, short_text, load_config, compute_balance, fmt_date, live_projects, live_tasks, is_done as status_done, STAGES, NO_STAGE, STATUS_IN_WORK,
    STATUS_ON_HOLD, TASK_IN_PROGRESS,
)
from i18n import tr, tv

PENDING_TTL_SEC = 3600                 # drafts and pending photos live this long


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


# ---------- photo counts for the cards ----------

SHARE_REFS_DIR = os.path.join(progress.PHOTOS_DIR, "refs")


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
