"""Proxy for Telegram: an optional setting.

Where the address comes from:
1. Settings → Telegram (config.json, key TG_PROXY = {"enabled": bool, "url": "..."}). Once this block has been saved
   in the UI, only it counts: off means the bot connects directly, on means through the given address.
2. Until anything is saved in the UI: PROXY_URL from .env, as before (empty = direct).

The address is looked up for every request (from the cached config), so changes apply without a restart.
Supported: http://, https://, socks5://, socks5h:// (a Telegram MTProto proxy does not work for bots).
"""
import os
from typing import Optional
from urllib.parse import urlsplit

import httpcore
import httpx
from dotenv import load_dotenv

from common import DATA_DIR, config_value, load_config, edit_config

load_dotenv(os.path.join(DATA_DIR, ".env"))

SCHEMES = ("http", "https", "socks5", "socks5h")
MAX_URL_LEN = 500


def _env_url() -> Optional[str]:
    return (os.getenv("PROXY_URL") or "").strip() or None


def _cfg_block(cfg: dict):
    block = cfg.get("TG_PROXY")
    return block if isinstance(block, dict) else None


def get_url(cfg: dict = None) -> Optional[str]:
    """The proxy address to reach Telegram through right now, or None for a direct connection."""
    try:
        block = _cfg_block(cfg) if cfg is not None else _cfg_block({"TG_PROXY": config_value("TG_PROXY")})
    except Exception:
        block = None
    if block is None:
        return _env_url()
    if not block.get("enabled"):
        return None
    return (str(block.get("url") or "")).strip() or None


# httpx/httpcore read the SOCKS5 handshake replies with no timeout at all: a proxy that accepts the connection and
# then stays silent blocks the request (and its thread) forever, whatever timeout the client has. In the bot those
# threads piled up until the worker pool was full, and from then on every request failed even with the proxy fine.
# So any socket read or write that comes without a timeout gets this one.
STALL_TIMEOUT_SEC = 15.0


class _DeadlineStream:
    """A network stream whose reads and writes never wait without a limit."""

    def __init__(self, inner, limit: float):
        self._inner = inner
        self._limit = limit

    def _t(self, timeout):
        return self._limit if timeout is None else timeout

    def read(self, max_bytes, timeout=None):
        return self._inner.read(max_bytes, self._t(timeout))

    def write(self, buffer, timeout=None):
        return self._inner.write(buffer, self._t(timeout))

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        return _DeadlineStream(self._inner.start_tls(ssl_context, server_hostname, self._t(timeout)), self._limit)

    def close(self):
        return self._inner.close()

    def get_extra_info(self, info):
        return self._inner.get_extra_info(info)


class _DeadlineBackend(httpcore.SyncBackend):
    def __init__(self, limit: float):
        self._limit = limit

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        stream = super().connect_tcp(host, port, timeout=self._limit if timeout is None else timeout,
                                     local_address=local_address, socket_options=socket_options)
        return _DeadlineStream(stream, self._limit)


def make_transport(url: Optional[str], stall_timeout: float = None):
    """httpx transport through the given proxy (None for a direct connection), with the SOCKS handshake limited."""
    if not url:
        return None
    tr_ = httpx.HTTPTransport(proxy=url)
    pool = getattr(tr_, "_pool", None)
    # the backend is a private attribute of httpcore 1.x; if a future version renames it, the transport still works,
    # only without the handshake limit
    if isinstance(pool, httpcore.SOCKSProxy) and hasattr(pool, "_network_backend"):
        pool._network_backend = _DeadlineBackend(STALL_TIMEOUT_SEC if stall_timeout is None else stall_timeout)
    return tr_


def transport(cfg: dict = None):
    """httpx transport for requests to Telegram: through the proxy, or direct (None)."""
    return make_transport(get_url(cfg))


def validate(url: str) -> Optional[str]:
    """None if the address is fine, otherwise an error code: bad_scheme, bad_host, too_long."""
    url = (url or "").strip()
    if len(url) > MAX_URL_LEN:
        return "too_long"
    try:
        parts = urlsplit(url)
        _ = parts.port               # ValueError if the port is not a number
    except ValueError:
        return "bad_host"
    if parts.scheme.lower() not in SCHEMES:
        return "bad_scheme"
    if not parts.hostname:
        return "bad_host"
    return None


def mask(url: Optional[str]) -> str:
    """The address without login and password, for the UI and the log: socks5://***@host:1080."""
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        auth = "***@" if (parts.username or parts.password) else ""
        return f"{parts.scheme}://{auth}{host}"
    except ValueError:
        return "***"


def describe(cfg: dict = None) -> str:
    """A short string for the log: "direct" or "via proxy socks5://***@host:port"."""
    url = get_url(cfg)
    return f"via proxy {mask(url)}" if url else "direct"


def state(cfg: dict = None) -> dict:
    """State for the UI. The address itself (with the password) never goes to the browser, only the mask."""
    cfg = cfg if cfg is not None else load_config()
    block = _cfg_block(cfg)
    if block is None:
        url = _env_url()
        return {"source": "env", "enabled": bool(url), "has_url": bool(url), "display": mask(url)}
    url = (str(block.get("url") or "")).strip()
    return {"source": "settings", "enabled": bool(block.get("enabled")) and bool(url),
            "has_url": bool(url), "display": mask(url)}


def resolve_new_url(new_url: Optional[str], cfg: dict) -> Optional[str]:
    """The address from the form: an empty field means "keep the current one" (from the settings or .env)."""
    new_url = (new_url or "").strip()
    if new_url:
        return new_url
    block = _cfg_block(cfg)
    if block is not None:
        return (str(block.get("url") or "")).strip() or None
    return _env_url()


def save(enabled: bool, new_url: Optional[str]) -> Optional[str]:
    """Saves the setting to config.json. Returns an error code or None."""
    with edit_config() as cfg:
        url = resolve_new_url(new_url, cfg)
        if enabled and not url:
            return "no_url"
        if url:
            err = validate(url)
            if err:
                return err
        cfg["TG_PROXY"] = {"enabled": bool(enabled), "url": url or ""}
    return None


def check(bot_token: Optional[str], url: Optional[str], timeout: float = 12.0) -> dict:
    """A test getMe request through the given proxy (or directly if url is empty)."""
    if not bot_token:
        return {"ok": False, "error": "no_token"}
    tr_ = make_transport(url)
    try:
        with httpx.Client(transport=tr_, timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0))) as client:
            data = client.get(f"https://api.telegram.org/bot{bot_token}/getMe").json()
    except Exception as e:                         # no connection, the proxy does not answer, no socksio package, etc.
        text = str(e) or e.__class__.__name__
        return {"ok": False, "error": "network", "detail": text.replace(bot_token, "***")[:200]}
    if not data.get("ok"):
        return {"ok": False, "error": "telegram", "detail": str(data.get("description") or "")[:200]}
    return {"ok": True, "username": (data.get("result") or {}).get("username")}
