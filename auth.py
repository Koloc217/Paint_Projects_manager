"""Необязательная защита входом (по умолчанию ВЫКЛЮЧЕНА).

Личный вариант: один пользователь. Состояние лежит в auth.json (рядом с config.json, в /api/config не попадает):

    {"enabled": false, "username": "admin", "salt": "...", "hash": "...", "must_change": true, "secret": "..."}

Как это работает
  • Пароль по сети уходит не открытым текстом: браузер отправляет digest = SHA-256("project-hub:<логин>:<пароль>"),
    сервер хранит scrypt(digest, salt). Это закрывает случайный подсмотр и повторное использование пароля на
    других сайтах. Но без HTTPS digest при перехвате можно переслать повторно — полноценную защиту даст HTTPS.
  • Вход выдаёт подписанную cookie (HttpOnly, SameSite=Lax; Secure — если запрос по https). Подпись привязана
    к хэшу пароля: смена пароля выбрасывает все прежние сессии.
  • Пароль по умолчанию admin / 123456 помечен must_change: пока его не сменят, всё, кроме смены пароля и
    выхода, закрыто.
  • Перебор: 5 неудачных попыток с одного адреса за 5 минут — блокировка на остаток окна, плюс пауза на каждой ошибке.
  • Бот Telegram, уведомления и планировщик работают мимо веба и вход не затрагивает.

Забыли пароль:  python auth.py reset   (сбросит на admin / 123456 и выключит вход)
"""
import os
import sys
import json
import hmac
import time
import hashlib
import secrets
import tempfile
import asyncio

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
from pydantic import BaseModel

from common import BASE_DIR
from i18n import tr, get_lang, table_for

AUTH_FILE = os.path.join(BASE_DIR, "auth.json")
COOKIE_NAME = "ph_session"
SESSION_TTL_SEC = 14 * 24 * 3600
DEFAULT_USER = "admin"
DEFAULT_PASSWORD = "123456"          # только для первого входа; потом обязательно меняется
MIN_PASSWORD_LEN = 8                 # проверяется в браузере (на сервер приходит только digest)

MAX_FAILS = 5
FAIL_WINDOW_SEC = 300
PUBLIC_PREFIXES = ("/static/",)
PUBLIC_PATHS = ("/login", "/api/auth/login", "/sw.js", "/manifest.webmanifest")   # PWA: без входа, иначе установка не работает
# пока пароль по умолчанию не сменён, доступно только это
MUST_CHANGE_PATHS = ("/login", "/api/auth/login", "/api/auth/change", "/api/auth/logout", "/api/auth/status")

_fails = {}            # ip -> [время неудачных попыток]
_state_cache = {"mtime": None, "data": None}


def make_digest(username: str, password: str) -> str:
    """То же самое считает браузер (static/auth.js): SHA-256 от 'project-hub:<логин>:<пароль>'."""
    return hashlib.sha256(f"project-hub:{str(username).lower()}:{password}".encode("utf-8")).hexdigest()


def _scrypt(digest: str, salt_hex: str) -> str:
    return hashlib.scrypt(digest.encode("ascii"), salt=bytes.fromhex(salt_hex), n=2 ** 14, r=8, p=1, dklen=32).hex()


def _default_state() -> dict:
    salt = secrets.token_hex(16)
    return {
        "enabled": False,
        "username": DEFAULT_USER,
        "salt": salt,
        "hash": _scrypt(make_digest(DEFAULT_USER, DEFAULT_PASSWORD), salt),
        "must_change": True,
        "secret": secrets.token_hex(32),
    }


def _write(state: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=BASE_DIR, prefix=".auth.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, AUTH_FILE)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    _state_cache["mtime"] = None


def load_state() -> dict:
    try:
        mtime = os.stat(AUTH_FILE).st_mtime_ns
    except OSError:
        state = _default_state()
        _write(state)
        return state
    if mtime == _state_cache["mtime"]:
        return _state_cache["data"]
    try:
        with open(AUTH_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        if not all(k in state for k in ("salt", "hash", "secret", "username")):
            raise ValueError("incomplete")
    except (OSError, ValueError):
        state = _default_state()
        _write(state)
        return state
    state.setdefault("enabled", False)
    state.setdefault("must_change", False)
    _state_cache.update(mtime=mtime, data=state)
    return state


def is_enabled() -> bool:
    return bool(load_state().get("enabled"))


def verify_password(digest: str, state=None) -> bool:
    state = state or load_state()
    if not isinstance(digest, str) or not _is_hex64(digest):
        return False
    return hmac.compare_digest(_scrypt(digest, state["salt"]), state["hash"])


def _is_hex64(value: str) -> bool:
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


# ---------------------------------------------------------------- сессии
def _sign(state: dict, expires: int) -> str:
    msg = f"{state['username']}|{expires}|{state['hash'][:16]}".encode("utf-8")
    return hmac.new(bytes.fromhex(state["secret"]), msg, hashlib.sha256).hexdigest()


def make_session(state=None) -> str:
    state = state or load_state()
    expires = int(time.time()) + SESSION_TTL_SEC
    return f"{expires}.{_sign(state, expires)}"


def session_valid(token) -> bool:
    if not token or "." not in token:
        return False
    exp_s, _, sig = token.partition(".")
    try:
        expires = int(exp_s)
    except ValueError:
        return False
    if expires < time.time():
        return False
    return hmac.compare_digest(_sign(load_state(), expires), sig)


def _is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"


def set_session_cookie(response, request: Request, state=None) -> None:
    response.set_cookie(COOKIE_NAME, make_session(state), max_age=SESSION_TTL_SEC, httponly=True,
                        samesite="lax", secure=_is_https(request), path="/")


# ---------------------------------------------------------------- защита от перебора
def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _recent_fails(ip: str) -> list:
    now = time.time()
    fails = [t for t in _fails.get(ip, []) if now - t < FAIL_WINDOW_SEC]
    if fails:
        _fails[ip] = fails
    else:
        _fails.pop(ip, None)
    return fails


def locked_minutes(ip: str) -> int:
    """0, если можно пробовать; иначе сколько минут ждать."""
    fails = _recent_fails(ip)
    if len(fails) >= MAX_FAILS:
        return max(1, int((FAIL_WINDOW_SEC - (time.time() - fails[0])) // 60) + 1)
    return 0


def _register_fail(ip: str) -> None:
    _fails.setdefault(ip, []).append(time.time())


# ---------------------------------------------------------------- подключение к приложению
class LoginBody(BaseModel):
    username: str = ""
    digest: str = ""


class ChangeBody(BaseModel):
    old_digest: str = ""
    new_digest: str = ""


class EnableBody(BaseModel):
    enabled: bool
    digest: str = ""


def _err(message, status=200, **extra):
    return JSONResponse({"status": "error", "message": message, **extra}, status_code=status)


def setup(app, templates) -> None:
    """Регистрирует проверку входа и маршруты /login, /api/auth/*."""

    @app.middleware("http")
    async def auth_guard(request: Request, call_next):
        state = load_state()
        if not state.get("enabled"):
            return await call_next(request)
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)
        if session_valid(request.cookies.get(COOKIE_NAME)):
            if state.get("must_change") and path not in MUST_CHANGE_PATHS:
                if path.startswith("/api/"):
                    return JSONResponse({"status": "error", "code": "must_change", "message": tr("auth.change_required")}, status_code=403)
                return RedirectResponse("/login", status_code=303)
            return await call_next(request)
        if path.startswith("/api/"):
            return JSONResponse({"status": "error", "code": "auth_required", "message": tr("auth.err_required_login")}, status_code=401)
        if path.startswith("/photos/"):
            return JSONResponse({"status": "error", "code": "auth_required"}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        state = load_state()
        logged = session_valid(request.cookies.get(COOKIE_NAME))
        if not state.get("enabled") or (logged and not state.get("must_change")):
            return RedirectResponse("/", status_code=303)
        lang = get_lang()
        return templates.TemplateResponse(
            request=request, name="login.html",
            context={"lang": lang, "i18n": table_for(lang), "username": state["username"],
                     "change_mode": bool(logged and state.get("must_change")), "min_len": MIN_PASSWORD_LEN},
        )

    @app.post("/api/auth/login")
    async def api_login(body: LoginBody, request: Request):
        state = load_state()
        if not state.get("enabled"):
            return _err(tr("auth.err_invalid"))
        ip = _client_ip(request)
        wait = locked_minutes(ip)
        if wait:
            return _err(tr("auth.err_locked", minutes=wait), status=429)
        ok_user = hmac.compare_digest(body.username.strip().lower().encode(), state["username"].lower().encode())
        ok_pass = await asyncio.to_thread(verify_password, body.digest, state)   # always computed, so timing does not reveal the user name
        if not (ok_user and ok_pass):
            _register_fail(ip)
            await asyncio.sleep(0.6)
            return _err(tr("auth.err_invalid"), status=401)
        _fails.pop(ip, None)
        resp = JSONResponse({"status": "ok", "must_change": bool(state.get("must_change"))})
        set_session_cookie(resp, request, state)
        return resp

    @app.post("/api/auth/logout")
    async def api_logout():
        resp = JSONResponse({"status": "ok"})
        resp.delete_cookie(COOKIE_NAME, path="/")
        return resp

    @app.get("/api/auth/status")
    async def api_status():
        state = load_state()
        return {"enabled": bool(state.get("enabled")), "username": state["username"],
                "must_change": bool(state.get("must_change")), "min_len": MIN_PASSWORD_LEN}

    @app.post("/api/auth/change")
    async def api_change(body: ChangeBody, request: Request):
        state = load_state()
        ip = _client_ip(request)
        wait = locked_minutes(ip)
        if wait:
            return _err(tr("auth.err_locked", minutes=wait), status=429)
        if not _is_hex64(body.new_digest):
            return _err(tr("auth.err_bad_request"))
        if not await asyncio.to_thread(verify_password, body.old_digest, state):
            _register_fail(ip)
            await asyncio.sleep(0.6)
            return _err(tr("auth.err_old"))
        if body.new_digest == body.old_digest:
            return _err(tr("auth.err_same"))
        if body.new_digest == make_digest(state["username"], DEFAULT_PASSWORD):
            return _err(tr("auth.err_default"))
        _fails.pop(ip, None)
        salt = secrets.token_hex(16)
        state = {**state, "salt": salt, "hash": await asyncio.to_thread(_scrypt, body.new_digest, salt),
                 "must_change": False, "secret": secrets.token_hex(32)}     # новый secret: старые сессии недействительны
        _write(state)
        resp = JSONResponse({"status": "ok"})
        if state.get("enabled"):
            set_session_cookie(resp, request, state)
        return resp

    @app.post("/api/auth/enable")
    async def api_enable(body: EnableBody, request: Request):
        state = load_state()
        if body.enabled == bool(state.get("enabled")):
            return {"status": "ok", "enabled": body.enabled, "must_change": bool(state.get("must_change"))}
        if not body.enabled:
            # выключить можно только вошедшему (middleware) и с текущим паролем
            ip = _client_ip(request)
            wait = locked_minutes(ip)
            if wait:
                return _err(tr("auth.err_locked", minutes=wait), status=429)
            if not await asyncio.to_thread(verify_password, body.digest, state):
                _register_fail(ip)
                await asyncio.sleep(0.6)
                return _err(tr("auth.err_old"))
            _fails.pop(ip, None)
        state = {**state, "enabled": body.enabled}
        _write(state)
        resp = JSONResponse({"status": "ok", "enabled": body.enabled, "must_change": bool(state.get("must_change"))})
        if body.enabled:
            set_session_cookie(resp, request, state)      # включивший остаётся внутри, пока не сменит пароль
        else:
            resp.delete_cookie(COOKIE_NAME, path="/")
        return resp


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "reset":
        _write(_default_state())
        print(f"Сброшено: вход выключен, пользователь {DEFAULT_USER}, пароль {DEFAULT_PASSWORD} (при включении потребуют сменить).")
    elif cmd == "status":
        st = load_state()
        print(f"Вход: {'включён' if st['enabled'] else 'выключен'}; пользователь: {st['username']}; "
              f"{'пароль по умолчанию, нужна смена' if st['must_change'] else 'пароль изменён'}")
    else:
        print("Использование: python auth.py [status|reset]")
