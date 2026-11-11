"""Telegram Bot API calls: one reused HTTP client (direct or through the proxy), sending, editing, deleting,
uploading and downloading. Every other bot module calls Telegram through here (tests replace these functions).
"""
import os
import asyncio
import threading
import httpx
from dotenv import load_dotenv
from hub.bot import tgclean
from hub.core.constants import TG_ALERT_LIMIT
from hub.core.formatting import short_text
from hub.paths import DATA_DIR
from hub.bot import tgproxy

load_dotenv(os.path.join(DATA_DIR, ".env"))

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# ==================== NETWORK LAYER (direct or through a proxy, see tgproxy.py) ====================
# One HTTP client is kept and reused, so requests share open connections: without it every call (including the
# long poll every few seconds) paid a new TCP + TLS (+ SOCKS) handshake. The client is rebuilt when the proxy
# setting changes and at every planned session restart (start_bot_polling), so a stuck connection cannot live long.

_client_lock = threading.Lock()
_client = None
_client_proxy = None


def _http_client() -> httpx.Client:
    global _client, _client_proxy
    url = tgproxy.get_url()              # the proxy is optional: Settings → Telegram or PROXY_URL in .env
    with _client_lock:
        if _client is None or url != _client_proxy:
            old = _client
            transport = tgproxy.make_transport(url)      # SOCKS handshake limited, see tgproxy.STALL_TIMEOUT_SEC
            _client = httpx.Client(transport=transport, timeout=httpx.Timeout(20.0, connect=10.0))
            _client_proxy = url
            if old is not None:
                _close_quietly(old)
        return _client


def _close_quietly(client) -> None:
    try:
        client.close()
    except Exception:
        pass


def reset_http_client() -> None:
    """Drops the shared client: the next request opens fresh connections."""
    global _client
    with _client_lock:
        old, _client = _client, None
    if old is not None:
        _close_quietly(old)


def safe_error(e) -> str:
    """repr() of an exception without the bot token (httpx puts the request URL into some messages)."""
    text = repr(e)
    return text.replace(BOT_TOKEN, "***") if BOT_TOKEN else text


# Every message the bot sends is remembered so /clear and the digest cleanup can delete it later (tgclean.py)
TRACKED_ENDPOINTS = ("sendMessage", "sendPhoto", "sendDocument", "sendMediaGroup")


def _sync_tg_request(method: str, endpoint: str, payload: dict = None, params: dict = None, timeout_sec: float = 20.0):
    """Blocking request to the Bot API (run in a worker thread by tg_request)."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{endpoint}"
    timeout = httpx.Timeout(timeout_sec, connect=10.0)
    client = _http_client()
    if method == "GET":
        resp = client.get(url, params=params, timeout=timeout)
    else:
        resp = client.post(url, json=payload, timeout=timeout)
    result = resp.json()
    if endpoint in TRACKED_ENDPOINTS:
        tgclean.record((payload or {}).get("chat_id"), tgclean.ids_from_result(result), "bot")
    return result

async def tg_request(method: str, endpoint: str, payload: dict = None, params: dict = None, timeout_sec: float = 20.0):
    """Non-blocking wrapper with a HARD limit against hangs."""
    try:
        # The thread gets 5 seconds more than the socket timeout.
        # If the proxy hangs for good, asyncio stops waiting anyway.
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
            print(f"[BOT ERROR] Telegram rejected sendMessage: {res.get('description')}")
        return res
    except Exception as e:
        print(f"[BOT ERROR] send_message failed: {safe_error(e)}")
        return {}

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
            print(f"[BOT ERROR] Telegram rejected editMessageText: {res.get('description')}")
    except Exception as e:
        print(f"[BOT ERROR] edit_message_text failed: {safe_error(e)}")

async def answer_callback(callback_id: str, text: str = "", show_alert: bool = False):
    # Telegram silently drops an answer longer than 200 characters: the user would see no confirmation at all
    payload = {"callback_query_id": callback_id, "text": short_text(text, TG_ALERT_LIMIT), "show_alert": show_alert}
    try:
        await tg_request("POST", "answerCallbackQuery", payload=payload, timeout_sec=10.0)
    except Exception:
        pass


def _sync_tg_download(file_id: str) -> bytes:
    """Downloads a file by file_id (getFile + download) through the same connection as the rest of the bot."""
    client = _http_client()
    timeout = httpx.Timeout(40.0, connect=10.0)
    info = client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile", json={"file_id": file_id}, timeout=timeout).json()
    path = (info.get("result") or {}).get("file_path")
    if not info.get("ok") or not path:
        raise RuntimeError(f"getFile: {info.get('description')}")
    resp = client.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}", timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"file download: HTTP {resp.status_code}")     # no URL in the text: it holds the token
    return resp.content


async def download_file(file_id: str) -> bytes:
    return await asyncio.wait_for(asyncio.to_thread(_sync_tg_download, file_id), timeout=50.0)


def _sync_tg_upload(endpoint: str, data: dict, files: dict, timeout_sec: float = 60.0):
    """A request with files (multipart): sendPhoto, sendMediaGroup and editMessageMedia."""
    result = _http_client().post(f"https://api.telegram.org/bot{BOT_TOKEN}/{endpoint}", data=data, files=files,
                                 timeout=httpx.Timeout(timeout_sec, connect=10.0)).json()
    if endpoint in TRACKED_ENDPOINTS:
        tgclean.record(data.get("chat_id"), tgclean.ids_from_result(result), "bot")
    return result


async def tg_upload(endpoint: str, data: dict, files: dict, timeout_sec: float = 60.0):
    return await asyncio.wait_for(asyncio.to_thread(_sync_tg_upload, endpoint, data, files, timeout_sec),
                                  timeout=timeout_sec + 10.0)


# ---------- deleting messages (/clear, digests, self-destructing notes) ----------

CLEAR_NOTE_SEC = 5                     # how long "Chat cleared" stays before it removes itself


async def delete_messages(chat_id, ids: list) -> int:
    """Deletes messages in batches of 100. Returns how many were sent for deletion without an error;
    Telegram skips messages that are already gone or too old."""
    done = 0
    for batch in tgclean.batches(list(ids)):
        try:
            res = await tg_request("POST", "deleteMessages", payload={"chat_id": chat_id, "message_ids": batch})
            if res.get("ok"):
                done += len(batch)
            else:
                print(f"[BOT] deleteMessages: {res.get('description')}")
        except Exception as e:
            print(f"[BOT] deleteMessages failed: {safe_error(e)}")
    await asyncio.to_thread(tgclean.forget, chat_id, ids)       # either deleted or not deletable any more
    return done


async def _delete_later(chat_id, message_id, delay: float):
    await asyncio.sleep(delay)
    await delete_messages(chat_id, [message_id])


async def send_self_destructing(chat_id, text: str, delay: float = CLEAR_NOTE_SEC):
    res = await send_message(chat_id, text)
    ids = tgclean.ids_from_result(res)
    if ids:
        asyncio.create_task(_delete_later(chat_id, ids[0], delay))

