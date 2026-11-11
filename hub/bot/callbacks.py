"""Inline-button presses: routing and the simple handlers.

Every button carries "prefix:arg:arg" as its callback data. ROUTES maps a prefix to the async function that
handles it, so a new button means one handler and one entry in the table; handle_callback() does not change.
A handler may return False for "not mine after all"; the press is then answered with an empty reply so the
button stops spinning (the same happens to unknown and outdated buttons).
Handlers that grew big live next to what they do: tasks and time in bot_tasks, photos in bot_photos.
"""
import asyncio
import traceback
from dataclasses import dataclass
from datetime import datetime

from hub.bot import bot_api, bot_photos, bot_tasks, bot_views, tgclean
from hub.core.common import MAX_TASK_NAME_LEN, STAGES, fmt_date, live_project, live_task, load_config, normalize_value
from hub.core.common import is_done as status_done, short_text
from hub.core.database import SessionLocal
from hub.core.i18n import tr, tv
from hub.core.lifecycle import complete_task, postpone_task, set_stage, start_task, touch_project


@dataclass
class Press:
    """One button press: who pressed what, with the small helpers every handler needs."""
    cb: dict
    data: str
    now: datetime

    @property
    def cb_id(self) -> str:
        return self.cb["id"]

    @property
    def chat_id(self):
        return ((self.cb.get("message") or {}).get("chat") or {}).get("id")

    @property
    def message_id(self):
        return (self.cb.get("message") or {}).get("message_id")

    @property
    def prefix(self) -> str:
        return self.data.split(":")[0]

    @property
    def rest(self) -> str:
        """Everything after the first colon."""
        return self.data.split(":", 1)[1] if ":" in self.data else ""

    def arg(self, index: int):
        """The index-th colon-separated part (0 is the prefix), or None."""
        parts = self.data.split(":")
        return parts[index] if len(parts) > index else None

    async def answer(self, text: str = "", alert: bool = False) -> None:
        await bot_api.answer_callback(self.cb_id, text, alert)

    async def show(self, build, *args) -> None:
        """Redraws the message the button is under with a view built off the event loop."""
        text, markup = await asyncio.to_thread(build, *args)
        await bot_api.edit_message_text(self.chat_id, self.message_id, text, markup)


# ---------- handlers ----------

async def _clear(p: Press):
    await p.answer()
    if p.rest == "no":
        await bot_api.delete_messages(p.chat_id, [p.message_id])
        return
    plan = await asyncio.to_thread(tgclean.clear_plan, p.chat_id)
    ids = plan["keep_last"] if p.rest == "keep" else plan["all"]
    ids = sorted(set(ids) | {p.message_id})                 # the question itself goes too
    await bot_api.delete_messages(p.chat_id, ids)
    await bot_api.send_self_destructing(p.chat_id, f"🧹 {tr('bot.clear_done')}")


async def _task_builder(p: Press):
    return await bot_tasks.handle_task_callback(p.cb, p.data)


async def _time(p: Press):
    await bot_tasks.handle_time_callback(p.cb, p.data)


async def _photos(p: Press):
    return await bot_photos.handle_photo_callback(p.cb, p.data)


async def _task_done(p: Press):
    """t_done comes from a project card, n_done straight from a push notification."""
    from_push = p.prefix == "n_done"
    with SessionLocal() as db:
        task = live_task(db, p.arg(1))
        if not task:
            await p.answer(tr("bot.task_not_found"))
            return
        newly_closed = complete_task(task, p.now)             # a second press must not move the completion date
        if newly_closed:
            touch_project(db, task.project_id, p.now)
            db.commit()
        if from_push:
            await p.answer(f"✅ {tr('bot.task_closed_named', name=short_text(task.task_name, MAX_TASK_NAME_LEN))}", alert=True)
        else:
            await p.answer(f"✅ {tr('bot.task_closed')}")
        card_project = task.project_id if from_push else p.arg(2)
        ask_time = newly_closed and not task.spent_min
        task_id, task_name = task.id, task.task_name
    await p.show(bot_views.build_view_for, card_project)
    if ask_time:
        await bot_tasks.ask_spent_time(p.chat_id, task_id, task_name)


async def _task_postpone(p: Press):
    """Moves the deadline by +2 days from a push. The first time silently, repeated ones with a snarky comment
    (text only, no token penalty)."""
    with SessionLocal() as db:
        task = live_task(db, p.rest)
        if not task:
            await p.answer(tr("bot.task_not_found"))
            return
        if status_done(task.status):
            await p.answer(f"✅ {tr('bot.task_closed')}")
            return
        if not load_config().get("NOTIFY", {}).get("allow_postpone", True):
            await p.answer(tr("bot.postpone_disabled"), alert=True)
            return
        new_deadline, count = postpone_task(task, p.now)
        touch_project(db, task.project_id, p.now)
        db.commit()
        reply = f"⏳ {tr('bot.postponed', date=fmt_date(new_deadline))}"
        snark = bot_views.postpone_snark(count)
        if snark:
            reply += f"\n\n{snark}"
        await p.answer(reply, alert=True)
        project_id = task.project_id
    await p.show(bot_views.build_view_for, project_id or bot_views.GENERAL)


async def _task_in_progress(p: Press):
    with SessionLocal() as db:
        task = live_task(db, p.arg(1))
        if task and start_task(task):                          # an old button must not reopen a closed task
            touch_project(db, task.project_id, p.now)
            db.commit()
            await p.answer(f"🔄 {tr('bot.task_in_progress')}")
        elif task:
            await p.answer(f"✅ {tr('bot.task_closed')}")
        else:
            await p.answer(tr("bot.task_not_found"))
    await p.show(bot_views.build_view_for, p.arg(2))


async def _stage_menu(p: Press):
    await p.answer()
    await p.show(bot_views.build_stage_menu_view, p.rest)


async def _set_stage(p: Press):
    _, proj_id, new_stage = p.data.split(":", 2)
    new_stage = normalize_value(new_stage)                     # buttons sent before the switch to codes carry Russian names
    with SessionLocal() as db:
        proj = live_project(db, proj_id)
        if proj and new_stage in STAGES:
            set_stage(proj, new_stage, p.now)
            db.commit()
            await p.answer(f"🎨 {tr('project.stage')}: {tv(new_stage)}")
        elif not proj:
            await p.answer(tr("err.project_not_found"))
        else:
            await p.answer()
    await p.show(bot_views.build_current_focus_view, proj_id)


async def _general(p: Press):
    await p.answer()
    await p.show(bot_views.build_general_view)


async def _all_projects(p: Press):
    await p.answer()
    await p.show(bot_views.build_all_projects_view)


async def _open_project(p: Press):
    await p.answer()
    await p.show(bot_views.build_current_focus_view, p.rest)


async def _share(p: Press):
    await p.answer(tr("bot.share_preparing"))
    await bot_photos.send_project_share(p.chat_id, p.rest)


ROUTES = {
    "clr": _clear,
    **dict.fromkeys(("tk_new", "tk_p", "tk_d", "tk_x"), _task_builder),
    **dict.fromkeys(("noop", "ph_to", "ph_cancel", "ph_undo", "ph_v", "ph_d", "ph_dy", "mp_list", "mp_p", "rf_v", "rf_o"), _photos),
    **dict.fromkeys(("tm", "tm_own", "tm_x"), _time),
    **dict.fromkeys(("t_done", "n_done"), _task_done),
    "t_postpone": _task_postpone,
    "t_prog": _task_in_progress,
    "stage_menu": _stage_menu,
    "p_stage": _set_stage,
    "gen_v": _general,
    "show_all_proj": _all_projects,
    "open_p": _open_project,
    "share": _share,
}


async def handle_callback(cb: dict) -> None:
    press = Press(cb=cb, data=cb.get("data") or "", now=datetime.now())
    handler = ROUTES.get(press.prefix)
    try:
        handled = await handler(press) if handler else False
        if handled is False:
            await press.answer()                               # unknown or outdated button: stop the spinner
    except Exception as e:
        print(f"[CALLBACK ERROR]: {bot_api.safe_error(e)}")
        traceback.print_exc()
