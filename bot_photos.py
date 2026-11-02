"""Photos in the bot: progress photos sent to the chat, the photo viewer, /myphotos, paint references and the
"Share" album.
"""
import os
import time
import asyncio
import json
import traceback
import uuid
from database import SessionLocal, Project, Reference
import progress
from common import (
    esc, short_text, fmt_date, live_projects, is_done as status_done, NO_STAGE,
)
from i18n import tr, tv
import bot_api
import bot_views

# ==================== PROGRESS PHOTOS ====================
# A photo sent to the bot goes to the project timeline (the photo caption becomes the note).
# With several active projects, the bot asks which one the photo belongs to.

MAX_TG_DOWNLOAD = 20 * 1024 * 1024        # Bot API limit for file downloads
_pending_photos = {}                       # token -> {"files": [file_id], "caption", "ts"}
_photo_groups = {}                         # media_group_id -> {"token": ..., "project": ..., "ts": ...}


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
    cutoff = time.time() - bot_views.PENDING_TTL_SEC
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
                raw = await bot_api.download_file(fid)
                row = progress.add_photo(db, project_id, raw, None, note, "telegram")
                saved.append(row.id)
            except progress.ProgressError as e:
                error = tr(e.key, **e.params)
            except Exception as e:
                print(f"[BOT ERROR] Could not fetch the photo: {bot_api.safe_error(e)}")
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
    row = [{"text": f"📷 {tr('bot.btn_open_photos')}", "callback_data": f"ph_v:{project_id}:-1"}]   # opens on the photo just added
    undo = "ph_undo:" + ",".join(str(i) for i in ids[:6])
    if ids:
        row.append({"text": f"↩ {tr('bot.btn_undo')}", "callback_data": undo})
    return text, {"inline_keyboard": [row]}


async def handle_photo_message(chat_id: int, msg: dict):
    """A photo from the chat: any unexpected failure is reported, so a photo never disappears silently."""
    try:
        await _handle_photo_message(chat_id, msg)
    except Exception as e:
        print(f"[BOT ERROR] photo message: {bot_api.safe_error(e)}")
        traceback.print_exc()
        await bot_api.send_message(chat_id, not_added_text(1, 1))


async def _handle_photo_message(chat_id: int, msg: dict):
    found = _extract_image(msg)
    if not found or not found[0]:
        return
    file_id, size = found
    if size and size > MAX_TG_DOWNLOAD:
        await bot_api.send_message(chat_id, tr("bot.photo_too_big"))
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
                await bot_api.send_message(chat_id, not_added_text(1, 1, error))
        elif entry["token"] in _pending_photos:
            _pending_photos[entry["token"]]["files"].append(file_id)
        else:                                           # the project question was cancelled or has expired
            await bot_api.send_message(chat_id, not_added_text(1, 1, tr("bot.photo_expired")))
        return

    db = SessionLocal()
    try:
        active = [(p.id, p.name) for p in _active_projects(db)]
    finally:
        db.close()
    if not active:
        await bot_api.send_message(chat_id, tr("bot.photo_no_projects"))
        return

    if len(active) == 1:
        pid = active[0][0]
        if group:
            _photo_groups[group] = {"token": None, "project": pid, "ts": time.time()}
        ids, name, stage, error, failed = await save_telegram_photos(pid, [file_id], note)
        if ids:
            text, markup = _saved_view(name, stage, ids, pid)
            await bot_api.send_message(chat_id, text, markup)
        if failed:
            await bot_api.send_message(chat_id, not_added_text(failed, 1, error))
        return

    token = uuid.uuid4().hex[:8]
    _pending_photos[token] = {"files": [file_id], "caption": note, "ts": time.time()}
    if group:
        _photo_groups[group] = {"token": token, "project": None, "ts": time.time()}
    keyboard = [[{"text": f"🎨 {short_text(name, 60)}", "callback_data": f"ph_to:{token}:{pid}"}]
                for pid, name in active[:bot_views.PROJECT_BUTTONS_MAX]]
    keyboard.append([{"text": f"✖ {tr('common.cancel')}", "callback_data": f"ph_cancel:{token}"}])
    await bot_api.send_message(chat_id, f"📷 {tr('bot.photo_which')}", {"inline_keyboard": keyboard})


async def handle_photo_choice(cb_id: str, chat_id: int, message_id: int, token: str, project_id: str):
    pending = _pending_photos.pop(token, None)
    if not pending:
        await bot_api.answer_callback(cb_id, tr("bot.photo_expired"), show_alert=True)
        await bot_api.edit_message_text(chat_id, message_id, not_added_text(1, 1, tr("bot.photo_expired")))
        return
    for g, v in list(_photo_groups.items()):
        if v.get("token") == token:
            v["project"], v["token"] = project_id, None
    await bot_api.answer_callback(cb_id)
    total = len(pending["files"])
    ids, name, stage, error, failed = await save_telegram_photos(project_id, pending["files"], pending["caption"])
    if ids:
        text, markup = _saved_view(name, stage, ids, project_id)
        if failed:
            text += "\n" + not_added_text(failed, total, error)
        await bot_api.edit_message_text(chat_id, message_id, text, markup)
    else:
        await bot_api.edit_message_text(chat_id, message_id, not_added_text(total, total, error or tr("bot.photo_download_failed")))


# ----- sharing a project: an album to forward -----
# Cover, the latest progress photos and the references (up to Telegram's 10 per album), with the name, the stage
# and the paint recipe in the caption. Tasks, tokens and deadlines stay private. The album can be forwarded to
# anyone; the bot answers only the bound chat, so a forwarded album gives nobody access to the data.
SHARE_MAX_ITEMS = 10                    # Telegram limit for an album
SHARE_PROGRESS = 5                      # the latest progress photos
SHARE_FILE_MAX = 5 * 1024 * 1024        # bigger originals (and GIF/WebP/AVIF) go as their JPEG preview
SHARE_PHOTO_EXT = (".jpg", ".jpeg", ".png")
CAPTION_MAX = 1024                      # Telegram limit for a caption (characters after markup)


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
        proj = bot_views._live_project(db, project_id)
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
            path = os.path.join(bot_views.SHARE_REFS_DIR, os.path.basename(r.filename))
            thumb = os.path.join(bot_views.SHARE_REFS_DIR, os.path.basename(r.thumb)) if r.thumb else ""
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
        await bot_api.send_message(chat_id, tr("err.project_not_found"))
        return
    files, caption, extra = built
    try:
        if not files:
            res = await bot_api.tg_request("POST", "sendMessage", payload={"chat_id": chat_id, "text": caption, "parse_mode": "HTML"})
        elif len(files) == 1:
            res = await bot_api.tg_upload("sendPhoto", {"chat_id": str(chat_id), "caption": caption, "parse_mode": "HTML"},
                                  {"photo": (os.path.basename(files[0]), await asyncio.to_thread(_read_bytes, files[0]))})
        else:
            media, upload = [], {}
            for i, path in enumerate(files):
                item = {"type": "photo", "media": f"attach://f{i}"}
                if i == 0:
                    item.update({"caption": caption, "parse_mode": "HTML"})
                media.append(item)
                upload[f"f{i}"] = (os.path.basename(path), await asyncio.to_thread(_read_bytes, path))
            res = await bot_api.tg_upload("sendMediaGroup", {"chat_id": str(chat_id), "media": json.dumps(media)}, upload,
                                  timeout_sec=180.0)
        if not res.get("ok"):
            print(f"[BOT ERROR] Telegram rejected the share: {res.get('description')}")
            await bot_api.send_message(chat_id, f"⚠️ {tr('bot.share_failed')}")
            return
    except Exception as e:
        print(f"[BOT ERROR] share failed: {bot_api.safe_error(e)}")
        await bot_api.send_message(chat_id, f"⚠️ {tr('bot.share_failed')}")
        return
    if extra:
        await bot_api.send_message(chat_id, extra)
    await bot_api.send_message(chat_id, f"📤 {tr('bot.share_hint')}")


# ----- viewing photos -----

def _photo_view(scope: str, index: int):
    """Photo number index (oldest first, like the card's timeline) in the selection, as a dict
    (path, caption, keyboard, index, total), or None. A negative index counts from the newest end (-1 = newest)."""
    db = SessionLocal()
    try:
        rows = list(reversed(progress.list_photos(db, None if scope == "*" else scope)))
        if not rows:
            return None
        if index < 0:
            index += len(rows)
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
                {"text": "◀", "callback_data": f"ph_v:{scope}:{(index - 1) % total}"},
                {"text": f"{index + 1}/{total}", "callback_data": "noop"},
                {"text": "▶", "callback_data": f"ph_v:{scope}:{(index + 1) % total}"},
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
        res = await bot_api.tg_upload("sendPhoto", {
            "chat_id": str(chat_id), "caption": view["caption"], "parse_mode": "HTML",
            "reply_markup": json.dumps(view["markup"]),
        }, {"photo": (os.path.basename(view["path"]), _read_bytes(view["path"]))})
        if not res.get("ok"):
            print(f"[BOT ERROR] Telegram rejected sendPhoto: {res.get('description')}")
            await bot_api.send_message(chat_id, f"⚠️ {tr('bot.photo_send_failed')}")
    except Exception as e:
        print(f"[BOT ERROR] sendPhoto failed: {bot_api.safe_error(e)}")
        await bot_api.send_message(chat_id, f"⚠️ {tr('bot.photo_send_failed')}")
    return True


async def edit_photo_view(chat_id: int, message_id: int, scope: str, index: int):
    view = _photo_view(scope, index)
    if not view:
        await bot_api.tg_request("POST", "editMessageCaption", payload={
            "chat_id": chat_id, "message_id": message_id, "caption": tr("bot.photo_none"),
            "reply_markup": {"inline_keyboard": []}})
        return
    media = {"type": "photo", "media": "attach://photo", "caption": view["caption"], "parse_mode": "HTML"}
    try:
        res = await bot_api.tg_upload("editMessageMedia", {
            "chat_id": str(chat_id), "message_id": str(message_id),
            "media": json.dumps(media), "reply_markup": json.dumps(view["markup"]),
        }, {"photo": (os.path.basename(view["path"]), _read_bytes(view["path"]))})
        if not res.get("ok") and "not modified" not in str(res.get("description", "")):
            print(f"[BOT ERROR] Telegram rejected editMessageMedia: {res.get('description')}")
    except Exception as e:
        print(f"[BOT ERROR] editMessageMedia failed: {bot_api.safe_error(e)}")


async def handle_photo_callback(cb: dict, data: str) -> bool:
    """Photo buttons. True if the callback was one of them."""
    cb_id = cb["id"]
    msg = cb.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    message_id = msg.get("message_id")
    parts = data.split(":")
    action = parts[0]

    if action == "noop":
        await bot_api.answer_callback(cb_id)
    elif action == "mp_list":
        await bot_api.answer_callback(cb_id)
        view_text, markup = await asyncio.to_thread(build_myphotos_projects)
        await _show_text(chat_id, msg, view_text, markup)
    elif action == "mp_p" and len(parts) == 2:
        await bot_api.answer_callback(cb_id)
        view_text, markup = await asyncio.to_thread(build_myphotos_kinds, parts[1])
        await _show_text(chat_id, msg, view_text, markup)
    elif action == "rf_v" and len(parts) == 3 and parts[2].lstrip("-").isdigit():
        index = int(parts[2])
        if msg.get("photo"):                                   # flip through in the same message
            await bot_api.answer_callback(cb_id)
            await edit_ref_view(chat_id, message_id, parts[1], index)
        elif await send_ref_view(chat_id, parts[1], index):
            await bot_api.answer_callback(cb_id)
        else:
            await bot_api.answer_callback(cb_id, tr("bot.refs_none"), show_alert=True)
    elif action == "rf_o" and len(parts) == 3 and parts[2].isdigit():
        await bot_api.answer_callback(cb_id)
        await send_ref_original(chat_id, parts[1], int(parts[2]))
    elif action == "ph_to" and len(parts) == 3:
        await handle_photo_choice(cb_id, chat_id, message_id, parts[1], parts[2])
    elif action == "ph_cancel" and len(parts) == 2:
        _pending_photos.pop(parts[1], None)
        await bot_api.answer_callback(cb_id)
        await bot_api.edit_message_text(chat_id, message_id, f"✖ {tr('bot.photo_cancelled')}")
    elif action == "ph_undo" and len(parts) == 2:
        db = SessionLocal()
        try:
            for pid in parts[1].split(","):
                if pid.isdigit():
                    progress.delete_photo(db, int(pid))
        finally:
            db.close()
        await bot_api.answer_callback(cb_id)
        await bot_api.edit_message_text(chat_id, message_id, f"↩ {tr('bot.photo_undone')}")
    elif action == "ph_v" and len(parts) == 3 and parts[2].lstrip("-").isdigit():
        index = int(parts[2])
        if msg.get("photo"):                                   # flip through in the same message
            await bot_api.answer_callback(cb_id)
            await edit_photo_view(chat_id, message_id, parts[1], index)
        elif await send_photo_view(chat_id, parts[1], index):  # from a text card: a new message with the photo
            await bot_api.answer_callback(cb_id)
        else:
            await bot_api.answer_callback(cb_id, tr("bot.photo_none"), show_alert=True)
    elif action == "ph_d" and len(parts) == 3 and parts[2].isdigit():
        view = _photo_view(parts[1], int(parts[2]))
        if not view:
            await bot_api.answer_callback(cb_id, tr("bot.photo_none"), show_alert=True)
        else:
            await bot_api.answer_callback(cb_id)
            markup = {"inline_keyboard": [[
                {"text": f"🗑 {tr('bot.btn_delete_yes')}", "callback_data": f"ph_dy:{parts[1]}:{view['id']}:{view['index']}"},
                {"text": f"↩ {tr('common.cancel')}", "callback_data": f"ph_v:{parts[1]}:{view['index']}"},
            ]]}
            await bot_api.tg_request("POST", "editMessageReplyMarkup",
                             payload={"chat_id": chat_id, "message_id": message_id, "reply_markup": markup})
    elif action == "ph_dy" and len(parts) == 4 and parts[2].isdigit() and parts[3].isdigit():
        db = SessionLocal()
        try:
            progress.delete_photo(db, int(parts[2]))
        finally:
            db.close()
        await bot_api.answer_callback(cb_id, tr("bot.photo_deleted"))
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
        refs = len(bot_views._ref_rows(db, p.id))
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
    for p, photos, refs in items[:bot_views.PROJECT_BUTTONS_MAX]:
        mark = "🏆" if status_done(p.status) else "🎨"
        counts = " · ".join(x for x in (f"📷{photos}" if photos else "", f"🖼{refs}" if refs else "") if x)
        keyboard.append([{"text": f"{mark} {short_text(p.name, 48)} ({counts})", "callback_data": f"mp_p:{p.id}"}])
    text = f"🖼 <b>{tr('bot.myphotos_title')}</b>\n{tr('bot.myphotos_pick_project')}"
    if len(items) > bot_views.PROJECT_BUTTONS_MAX:
        text += "\n" + bot_views.more_line(len(items) - bot_views.PROJECT_BUTTONS_MAX)
    return text, {"inline_keyboard": keyboard}


def build_myphotos_kinds(project_id: str):
    db = SessionLocal()
    try:
        proj = bot_views._live_project(db, project_id)
        if not proj:
            return tr("err.project_not_found"), {"inline_keyboard": [[{"text": f"◀ {tr('bot.btn_back_projects')}", "callback_data": "mp_list"}]]}
        photos = progress.count_photos(db, proj.id)
        refs = len(bot_views._ref_rows(db, proj.id))
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
        await bot_api.send_message(chat_id, text, markup)
    else:
        await bot_api.edit_message_text(chat_id, msg.get("message_id"), text, markup)


def _ref_view(project_id: str, index: int):
    """Reference number index (in the card's order) as a dict (path, caption, markup, ...), or None."""
    db = SessionLocal()
    try:
        proj = bot_views._live_project(db, project_id)
        rows = bot_views._ref_rows(db, project_id) if proj else []
        if not rows:
            return None
        total = len(rows)
        index = index % total
        row = rows[index]
        path = os.path.join(bot_views.SHARE_REFS_DIR, os.path.basename(row.filename))
        thumb = os.path.join(bot_views.SHARE_REFS_DIR, os.path.basename(row.thumb)) if row.thumb else ""
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
        res = await bot_api.tg_upload("sendPhoto", {
            "chat_id": str(chat_id), "caption": view["caption"], "parse_mode": "HTML",
            "reply_markup": json.dumps(view["markup"]),
        }, {"photo": (os.path.basename(view["path"]), await asyncio.to_thread(_read_bytes, view["path"]))})
        if not res.get("ok"):
            print(f"[BOT ERROR] Telegram rejected sendPhoto (reference): {res.get('description')}")
            await bot_api.send_message(chat_id, f"⚠️ {tr('bot.photo_send_failed')}")
    except Exception as e:
        print(f"[BOT ERROR] sendPhoto (reference) failed: {bot_api.safe_error(e)}")
        await bot_api.send_message(chat_id, f"⚠️ {tr('bot.photo_send_failed')}")
    return True


async def edit_ref_view(chat_id: int, message_id: int, project_id: str, index: int):
    view = await asyncio.to_thread(_ref_view, project_id, index)
    if not view:
        await bot_api.tg_request("POST", "editMessageCaption", payload={
            "chat_id": chat_id, "message_id": message_id, "caption": tr("bot.refs_none"),
            "reply_markup": {"inline_keyboard": []}})
        return
    media = {"type": "photo", "media": "attach://photo", "caption": view["caption"], "parse_mode": "HTML"}
    try:
        res = await bot_api.tg_upload("editMessageMedia", {
            "chat_id": str(chat_id), "message_id": str(message_id),
            "media": json.dumps(media), "reply_markup": json.dumps(view["markup"]),
        }, {"photo": (os.path.basename(view["path"]), await asyncio.to_thread(_read_bytes, view["path"]))})
        if not res.get("ok") and "not modified" not in str(res.get("description", "")):
            print(f"[BOT ERROR] Telegram rejected editMessageMedia (reference): {res.get('description')}")
    except Exception as e:
        print(f"[BOT ERROR] editMessageMedia (reference) failed: {bot_api.safe_error(e)}")


async def send_ref_original(chat_id: int, project_id: str, ref_id: int):
    """The reference file exactly as stored (any size up to the 25 MB upload limit), as a document."""
    db = SessionLocal()
    try:
        row = db.query(Reference).filter(Reference.id == ref_id, Reference.project_id == project_id).first()
        path = os.path.join(bot_views.SHARE_REFS_DIR, os.path.basename(row.filename)) if row else ""
    finally:
        db.close()
    if not path or not os.path.exists(path):
        await bot_api.send_message(chat_id, tr("bot.refs_none"))
        return
    try:
        res = await bot_api.tg_upload("sendDocument", {"chat_id": str(chat_id)},
                              {"document": (os.path.basename(path), await asyncio.to_thread(_read_bytes, path))},
                              timeout_sec=180.0)
        if not res.get("ok"):
            print(f"[BOT ERROR] Telegram rejected sendDocument (reference): {res.get('description')}")
            await bot_api.send_message(chat_id, f"⚠️ {tr('bot.photo_send_failed')}")
    except Exception as e:
        print(f"[BOT ERROR] sendDocument (reference) failed: {bot_api.safe_error(e)}")
        await bot_api.send_message(chat_id, f"⚠️ {tr('bot.photo_send_failed')}")

