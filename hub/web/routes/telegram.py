"""Settings -> Telegram: linking the chat and the proxy."""
import asyncio

from fastapi import APIRouter

from hub.bot import tgproxy
from hub.bot.telegram import BOT_TOKEN
from hub.core.appconfig import load_config
from hub.core.pairing import ensure_pairing_code, format_pairing_code, is_chat_bound, new_pairing_code, unlink_chat
from hub.core.i18n import tr
from hub.web.responses import fail
from hub.web.schemas import ProxyForm

router = APIRouter()


def telegram_state() -> dict:
    cfg = load_config()
    bound = is_chat_bound(cfg)
    code = None if bound else ensure_pairing_code()
    return {
        "bound": bound,
        "chat_id": cfg.get("TG_CHAT_ID") if bound else None,
        "pairing_code": format_pairing_code(code) if code else None,
    }

@router.get("/api/telegram")
def telegram_status():
    return telegram_state()

@router.post("/api/telegram/unlink")
def telegram_unlink():
    unlink_chat()
    return {"status": "ok", **telegram_state()}

@router.post("/api/telegram/code")
def telegram_new_code():
    if is_chat_bound():
        return fail(tr("err.bot_already_linked"))
    new_pairing_code()
    return {"status": "ok", **telegram_state()}


PROXY_ERRORS = {"no_url": "tg.proxy_err_no_url", "bad_scheme": "tg.proxy_err_scheme",
                "bad_host": "tg.proxy_err_host", "too_long": "tg.proxy_err_long"}


@router.get("/api/telegram/proxy")
def telegram_proxy_state():
    return tgproxy.state()


@router.post("/api/telegram/proxy")
def telegram_proxy_save(data: ProxyForm):
    err = tgproxy.save(data.enabled, data.url)
    if err:
        return fail(tr(PROXY_ERRORS.get(err, "settings.err_save")))
    return {"status": "ok", **tgproxy.state()}


@router.post("/api/telegram/proxy/check")
async def telegram_proxy_check(data: ProxyForm):
    """Checks the connection to Telegram with what is in the form right now (before saving)."""
    url = tgproxy.resolve_new_url(data.url, load_config()) if data.enabled else None
    if data.enabled:
        if not url:
            return {"ok": False, "message": tr("tg.proxy_err_no_url")}
        err = tgproxy.validate(url)
        if err:
            return {"ok": False, "message": tr(PROXY_ERRORS[err])}
    res = await asyncio.to_thread(tgproxy.check, BOT_TOKEN, url)
    via = tr("tg.proxy_via", url=tgproxy.mask(url)) if url else tr("tg.proxy_direct")
    if res["ok"]:
        return {"ok": True, "message": tr("tg.proxy_check_ok", bot="@" + str(res.get("username")), via=via)}
    if res["error"] == "no_token":
        return {"ok": False, "message": tr("tg.proxy_err_token")}
    return {"ok": False, "message": tr("tg.proxy_check_fail", via=via, detail=res.get("detail") or "")}
