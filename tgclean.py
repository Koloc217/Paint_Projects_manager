"""Keeping the bot chat tidy: /clear and automatic cleanup of notification digests.

Telegram limits (Bot API, deleteMessage):
  • a message can be deleted only while it is younger than 48 hours; after that it stays until the user clears the
    history in Telegram;
  • in a private chat the bot may delete both its own messages and the user's;
  • the bot cannot read the chat history, so it records every message it sends or receives (table tg_messages) and
    can delete only those. Messages from before this feature are unknown to it.

Digest cleanup modes (TG_CLEANUP in config.json, Settings → Telegram):
  keep_46h     older digests are deleted when they turn 46 hours old (the last moment it is still allowed);
               the latest digest always stays;
  latest_only  a new digest removes the previous ones, only the latest stays;
  off          digests are not deleted automatically.
Digests are the notify.py messages (evening summary, Friday plan, test push); the monthly report is not touched.
"""
from datetime import datetime, timedelta

from database import SessionLocal, TgMessage
from common import config_value

DELETE_LIMIT = timedelta(hours=48)       # Telegram: older messages cannot be deleted by a bot
KEEP_DIGEST = timedelta(hours=46)        # keep_46h: digests go just before the limit
SAFETY = timedelta(minutes=5)            # do not try messages that are about to cross the limit
BATCH = 100                              # deleteMessages takes up to 100 ids per call
MODES = ("keep_46h", "latest_only", "off")


def mode() -> str:
    value = config_value("TG_CLEANUP", "keep_46h")
    return value if value in MODES else "keep_46h"


def record(chat_id, message_ids, kind: str = "bot", when: datetime = None) -> None:
    """Remembers messages that may be deleted later. Never raises: tracking must not break sending."""
    ids = [int(i) for i in (message_ids or []) if isinstance(i, int) or str(i).isdigit()]
    if not ids or chat_id in (None, ""):
        return
    db = SessionLocal()
    try:
        when = when or datetime.now()
        for mid in ids:
            db.add(TgMessage(chat_id=str(chat_id), message_id=mid, kind=kind, created_at=when))
        db.commit()
    except Exception as e:
        print(f"[TGCLEAN] could not record messages: {e!r}")
    finally:
        db.close()


def ids_from_result(result) -> list:
    """message_id(s) from a Bot API reply: one message, or a list for sendMediaGroup."""
    if not isinstance(result, dict) or not result.get("ok"):
        return []
    res = result.get("result")
    items = res if isinstance(res, list) else [res]
    return [m["message_id"] for m in items if isinstance(m, dict) and "message_id" in m]


def _deletable(db, chat_id, now):
    return (db.query(TgMessage)
            .filter(TgMessage.chat_id == str(chat_id), TgMessage.created_at > now - DELETE_LIMIT + SAFETY)
            .order_by(TgMessage.created_at, TgMessage.id))


def _latest_digest(db, chat_id):
    return (db.query(TgMessage).filter(TgMessage.chat_id == str(chat_id), TgMessage.kind == "digest")
            .order_by(TgMessage.created_at.desc(), TgMessage.id.desc()).first())


def clear_plan(chat_id, now: datetime = None) -> dict:
    """What /clear can delete: {"all": [ids], "keep_last": [ids without the latest digest], "has_digest": bool}."""
    now = now or datetime.now()
    db = SessionLocal()
    try:
        rows = _deletable(db, chat_id, now).all()
        latest = _latest_digest(db, chat_id)
        keep_id = latest.message_id if latest else None
        all_ids = sorted({r.message_id for r in rows})
        return {"all": all_ids, "keep_last": [i for i in all_ids if i != keep_id],
                "has_digest": keep_id in all_ids}
    finally:
        db.close()


def digest_candidates(chat_id, now: datetime = None, cleanup_mode: str = None) -> list:
    """Digest messages the automatic cleanup should delete now (never the latest digest)."""
    now = now or datetime.now()
    cleanup_mode = cleanup_mode or mode()
    if cleanup_mode == "off":
        return []
    db = SessionLocal()
    try:
        latest = _latest_digest(db, chat_id)
        if not latest:
            return []
        q = (_deletable(db, chat_id, now).filter(TgMessage.kind == "digest", TgMessage.id != latest.id,
                                                 TgMessage.message_id != latest.message_id))
        if cleanup_mode == "keep_46h":
            q = q.filter(TgMessage.created_at <= now - KEEP_DIGEST)
        return sorted({r.message_id for r in q.all()})
    finally:
        db.close()


def forget(chat_id, message_ids) -> None:
    """Drops deleted messages from the list."""
    ids = list(message_ids or [])
    if not ids:
        return
    db = SessionLocal()
    try:
        for i in range(0, len(ids), 500):
            db.query(TgMessage).filter(TgMessage.chat_id == str(chat_id), TgMessage.message_id.in_(ids[i:i + 500])) \
                .delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def prune(now: datetime = None) -> int:
    """Forgets messages that can no longer be deleted (older than 48 hours); the latest digest row stays,
    so the "latest digest always stays" rule keeps working. Returns the number of rows removed."""
    now = now or datetime.now()
    db = SessionLocal()
    try:
        keep = set()
        for (chat,) in db.query(TgMessage.chat_id).distinct().all():
            latest = _latest_digest(db, chat)
            if latest:
                keep.add(latest.id)
        q = db.query(TgMessage).filter(TgMessage.created_at <= now - DELETE_LIMIT)
        if keep:
            q = q.filter(~TgMessage.id.in_(keep))
        n = q.delete(synchronize_session=False)
        db.commit()
        return n
    finally:
        db.close()


def batches(ids: list) -> list:
    return [ids[i:i + BATCH] for i in range(0, len(ids), BATCH)]
