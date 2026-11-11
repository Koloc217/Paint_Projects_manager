"""Binding the Telegram bot to one chat.

  * TG_CHAT_ID is the only chat the bot answers (messages from others are ignored);
  * while no chat is bound, the bot accepts only "/start CODE" from a private chat, and the code
    (TG_PAIRING_CODE) is shown in the app: Settings -> Telegram.
"""
import hmac
import secrets

from hub.core.appconfig import edit_config, load_config

PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"    # no look-alike characters (0/O, 1/I)


def is_chat_bound(cfg=None) -> bool:
    cfg = cfg if cfg is not None else load_config()
    return cfg.get("TG_CHAT_ID") not in (None, "", 0)


def _new_code() -> str:
    return "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))


def normalize_pairing_code(text) -> str:
    return "".join(ch for ch in str(text or "").upper() if ch.isalnum())


def format_pairing_code(code) -> str:
    code = normalize_pairing_code(code)
    return f"{code[:4]}-{code[4:]}" if code else ""


def ensure_pairing_code():
    """Pairing code for an unbound bot (created on first use). None if a chat is already bound."""
    if is_chat_bound():
        return None
    with edit_config() as cfg:
        if is_chat_bound(cfg):
            return None
        code = cfg.get("TG_PAIRING_CODE")
        if not code:
            code = _new_code()
            cfg["TG_PAIRING_CODE"] = code
        return code


def new_pairing_code():
    with edit_config() as cfg:
        if is_chat_bound(cfg):
            return None
        cfg["TG_PAIRING_CODE"] = _new_code()
        return cfg["TG_PAIRING_CODE"]


def check_pairing_code(text) -> bool:
    cfg = load_config()
    code = cfg.get("TG_PAIRING_CODE")
    if is_chat_bound(cfg) or not code:
        return False
    return hmac.compare_digest(normalize_pairing_code(code), normalize_pairing_code(text))


def bind_chat(chat_id) -> None:
    with edit_config() as cfg:
        cfg["TG_CHAT_ID"] = chat_id
        cfg.pop("TG_PAIRING_CODE", None)           # the code is single-use


def unlink_chat() -> str:
    with edit_config() as cfg:
        cfg.pop("TG_CHAT_ID", None)
        cfg["TG_PAIRING_CODE"] = _new_code()
        return cfg["TG_PAIRING_CODE"]
