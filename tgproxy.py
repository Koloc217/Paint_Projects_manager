"""Прокси для Telegram — необязательная настройка.

Откуда берётся адрес:
1. Настройки → Telegram (config.json, ключ TG_PROXY = {"enabled": bool, "url": "..."}). Если блок сохранён
   в интерфейсе, действует только он: выключен — бот ходит напрямую, включён — через указанный адрес.
2. Пока в интерфейсе ничего не сохраняли — как раньше: PROXY_URL из .env (пусто — напрямую).

Адрес читается при каждом запросе, поэтому изменения применяются без перезапуска.
Поддерживаются http://, https://, socks5://, socks5h:// (MTProto-прокси Telegram для ботов не подходит).
"""
import os
from typing import Optional
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv

from common import BASE_DIR, load_config, save_config

load_dotenv(os.path.join(BASE_DIR, ".env"))

SCHEMES = ("http", "https", "socks5", "socks5h")
MAX_URL_LEN = 500


def _env_url() -> Optional[str]:
    return (os.getenv("PROXY_URL") or "").strip() or None


def _cfg_block(cfg: dict):
    block = cfg.get("TG_PROXY")
    return block if isinstance(block, dict) else None


def get_url(cfg: dict = None) -> Optional[str]:
    """Адрес прокси, через который сейчас ходить в Telegram, или None — напрямую."""
    try:
        cfg = cfg if cfg is not None else load_config()
    except Exception:
        cfg = {}
    block = _cfg_block(cfg)
    if block is None:
        return _env_url()
    if not block.get("enabled"):
        return None
    return (str(block.get("url") or "")).strip() or None


def transport(cfg: dict = None):
    """httpx-транспорт для запросов к Telegram: через прокси или напрямую (None)."""
    url = get_url(cfg)
    return httpx.HTTPTransport(proxy=url) if url else None


def validate(url: str) -> Optional[str]:
    """None, если адрес подходит, иначе код ошибки: bad_scheme, bad_host, too_long."""
    url = (url or "").strip()
    if len(url) > MAX_URL_LEN:
        return "too_long"
    try:
        parts = urlsplit(url)
        _ = parts.port               # ValueError, если порт не число
    except ValueError:
        return "bad_host"
    if parts.scheme.lower() not in SCHEMES:
        return "bad_scheme"
    if not parts.hostname:
        return "bad_host"
    return None


def mask(url: Optional[str]) -> str:
    """Адрес без логина и пароля, для показа в интерфейсе и журнале: socks5://***@host:1080."""
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
    """Короткая строка для журнала: «напрямую» или «через прокси socks5://***@host:port»."""
    url = get_url(cfg)
    return f"через прокси {mask(url)}" if url else "напрямую"


def state(cfg: dict = None) -> dict:
    """Состояние для интерфейса. Сам адрес (с паролем) в браузер не отдаётся, только маска."""
    cfg = cfg if cfg is not None else load_config()
    block = _cfg_block(cfg)
    if block is None:
        url = _env_url()
        return {"source": "env", "enabled": bool(url), "has_url": bool(url), "display": mask(url)}
    url = (str(block.get("url") or "")).strip()
    return {"source": "settings", "enabled": bool(block.get("enabled")) and bool(url),
            "has_url": bool(url), "display": mask(url)}


def resolve_new_url(new_url: Optional[str], cfg: dict) -> Optional[str]:
    """Адрес из формы: пустое поле означает «оставить текущий» (из настроек или из .env)."""
    new_url = (new_url or "").strip()
    if new_url:
        return new_url
    block = _cfg_block(cfg)
    if block is not None:
        return (str(block.get("url") or "")).strip() or None
    return _env_url()


def save(enabled: bool, new_url: Optional[str]) -> Optional[str]:
    """Сохраняет настройку в config.json. Возвращает код ошибки или None."""
    cfg = load_config()
    url = resolve_new_url(new_url, cfg)
    if enabled and not url:
        return "no_url"
    if url:
        err = validate(url)
        if err:
            return err
    cfg["TG_PROXY"] = {"enabled": bool(enabled), "url": url or ""}
    save_config(cfg)
    return None


def check(bot_token: Optional[str], url: Optional[str], timeout: float = 12.0) -> dict:
    """Пробный запрос getMe через указанный прокси (или напрямую, если url пустой)."""
    if not bot_token:
        return {"ok": False, "error": "no_token"}
    tr_ = httpx.HTTPTransport(proxy=url) if url else None
    try:
        with httpx.Client(transport=tr_, timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0))) as client:
            data = client.get(f"https://api.telegram.org/bot{bot_token}/getMe").json()
    except Exception as e:                         # нет связи, прокси не отвечает, нет пакета socksio и т.п.
        text = str(e) or e.__class__.__name__
        return {"ok": False, "error": "network", "detail": text.replace(bot_token, "***")[:200]}
    if not data.get("ok"):
        return {"ok": False, "error": "telegram", "detail": str(data.get("description") or "")[:200]}
    return {"ok": True, "username": (data.get("result") or {}).get("username")}
