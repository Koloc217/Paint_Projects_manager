"""What every Telegram caller shares: the bot token (from .env), API addresses and the shape of a text message.

The callers differ in how they send: the bot keeps one connection open and works through worker threads
(bot_api.py), the notification job is a short process with a hard deadline and a retry (notify.py).
"""
import os

from dotenv import load_dotenv

from hub.paths import DATA_DIR

load_dotenv(os.path.join(DATA_DIR, ".env"))
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

API_ROOT = "https://api.telegram.org"


def api_url(token, endpoint: str) -> str:
    return f"{API_ROOT}/bot{token}/{endpoint}"


def file_url(token, file_path: str) -> str:
    return f"{API_ROOT}/file/bot{token}/{file_path}"


def text_payload(chat_id, text: str, reply_markup: dict = None, **extra) -> dict:
    """The body of sendMessage / editMessageText: HTML text, optional inline keyboard."""
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", **extra}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return payload
