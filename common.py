"""Shared helpers: config file, canonical values, escaping, token balance, Telegram chat pairing."""
import contextlib
import copy
import html
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
from datetime import datetime

try:
    import fcntl          # Linux / Raspberry Pi
except ImportError:
    fcntl = None
try:
    import msvcrt         # Windows
except ImportError:
    msvcrt = None

from database import Project, Task, Adjustment, DATA_DIR

BASE_DIR = os.path.dirname(os.path.abspath(__file__))     # the code: templates, static, locales.json, scripts
# DATA_DIR (from database.py): tracker.db, config.json, .env, auth.json, photos/, logs/ and service files.
# Next to the code unless PROJECT_HUB_DATA_DIR says otherwise.
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
CONFIG_LOCK_FILE = os.path.join(DATA_DIR, ".config.lock")

# ---------------------------------------------------------------------------
# Canonical values: technical codes stored in the database and used in the logic and the bot's callback_data.
# Labels come from locales.json ("status.<code>", "stage.<code>"), see i18n.tv().
# ---------------------------------------------------------------------------
STATUS_PLANNED = "planned"
STATUS_IN_WORK = "in_work"
STATUS_ON_HOLD = "on_hold"
STATUS_FINISHED = "finished"
PROJECT_STATUSES = (STATUS_PLANNED, STATUS_IN_WORK, STATUS_ON_HOLD, STATUS_FINISHED)

TASK_NOT_STARTED = "not_started"
TASK_IN_PROGRESS = "in_progress"
TASK_STATUSES = (TASK_NOT_STARTED, TASK_IN_PROGRESS, STATUS_FINISHED)

NO_STAGE = "none"
STAGES = (NO_STAGE, "printing", "sanding", "assembly", "priming", "painting", "finishing")   # finishing: touches before completion
PRIORITIES = ("Low", "Medium", "High", "Critical")

# Values written by older versions (Russian strings) -> current codes.
# Used by the one-time database migration and to accept old input (bot buttons still in the chat, old clients).
LEGACY_VALUES = {
    "В планах": STATUS_PLANNED, "В работе": STATUS_IN_WORK, "На паузе": STATUS_ON_HOLD,
    "Закончен": STATUS_FINISHED, "Завершено": STATUS_FINISHED, "Завершен": STATUS_FINISHED,
    "Выполнено": STATUS_FINISHED, "Completed": STATUS_FINISHED, "completed": STATUS_FINISHED,
    "Не начато": TASK_NOT_STARTED, "В процессе": TASK_IN_PROGRESS,
    "—": NO_STAGE, "Без стадии": NO_STAGE,
    "Печать": "printing", "Шлифовка": "sanding", "Сборка": "assembly",
    "Грунт": "priming", "Покрас": "painting", "Доводка": "finishing",
}

# Every spelling of "finished": the current code plus what older versions could have written
DONE_STATUSES = (STATUS_FINISHED, "закончен", "завершено", "завершен", "выполнено", "completed")


def normalize_value(value):
    """A status or stage as a current code: old Russian values are converted, anything else is returned as is."""
    if value is None:
        return None
    s = str(value).strip()
    return LEGACY_VALUES.get(s, s)


def legacy_value(code):
    """Current code -> the Russian value older versions used (for the Google Sheets backup, whose format stays)."""
    for old, new in LEGACY_VALUES.items():
        if new == code and old not in ("Завершено", "Завершен", "Выполнено", "Completed", "completed", "Без стадии"):
            return old
    return code

DEFAULT_NOTIFY_SETTINGS = {
    "allow_postpone": True,
    "stagnation_alert": True,
    "stagnation_days": 10,
    "friday_digest": True,
    "monthly_report": True,
}

# Schedule of the built-in scheduler (replaces cron).
# Local time, HH:MM. Pushes are configured separately for every day of the week.
DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")   # index = datetime.weekday()
DAY_LABELS = {"mon": "Пн", "tue": "Вт", "wed": "Ср", "thu": "Чт", "fri": "Пт", "sat": "Сб", "sun": "Вс"}


def normalize_schedule(raw) -> dict:
    """Brings SCHEDULE to the current shape and migrates the old format
    (notify_weekday_time / notify_weekend_time -> notify_days per weekday)."""
    s = dict(raw) if isinstance(raw, dict) else {}
    legacy_weekday = s.pop("notify_weekday_time", None)
    legacy_weekend = s.pop("notify_weekend_time", None)
    days_in = s.get("notify_days") if isinstance(s.get("notify_days"), dict) else {}
    days = {}
    for i, key in enumerate(DAY_KEYS):
        fallback = (legacy_weekday or "18:05") if i < 5 else (legacy_weekend or "12:05")
        d = days_in.get(key) if isinstance(days_in.get(key), dict) else {}
        days[key] = {"enabled": bool(d.get("enabled", True)), "time": str(d.get("time") or fallback)}
    s["notify_days"] = days
    s.setdefault("notify_enabled", True)
    s.setdefault("backup_enabled", False)
    s.setdefault("backup_time", "03:00")      # every day
    return s


DEFAULT_SCHEDULE = normalize_schedule({})

# Old short language codes (before the single locales.json file) -> current codes
LEGACY_LANGUAGES = {"ru": "ru-RU", "en": "en-US"}

DEFAULT_CONFIG = {
    "DEV_MODE": False,
    "INITIAL_TOKENS": 0,
    "PROJECT_COST": 10,
    # True: deleting a project does not change the token balance
    # (spent tokens are not refunded, earned ones are not lost).
    "DELETE_KEEPS_BALANCE": True,
    # language of the UI, the bot and notifications: a block code from locales.json (ru-RU, en-US, ...)
    "LANGUAGE": "ru-RU",
    # date format in the UI, the bot and notifications: dmy = 21.03.2027, mdy = 03/21/2027
    "DATE_FORMAT": "dmy",
    # how many days a deleted project stays in the trash before it is erased for good
    "TRASH_DAYS": 30,
    # deadlines to phones and other devices (the "To phone" button, the .ics file, /calendar in the bot, the subscription):
    # an option, off by default. The Calendar tab in the app is always on.
    "CALENDAR_EXPORT": False,
    # cleanup of notification digests in the bot chat (tgclean.py): keep_46h | latest_only | off
    "TG_CLEANUP": "keep_46h",
    # calendar subscription (/cal/<token>.ics): off by default; the token is generated when it is turned on
    "ICS_FEED": {"enabled": False, "token": ""},
}

DATE_FORMATS = {"dmy": "%d.%m.%Y", "mdy": "%m/%d/%Y"}


MAX_TOKENS = 1_000_000          # upper bound for a reward or a start cost


def to_tokens(value, default=0) -> int:
    """A token amount as a whole number. Old data and configs may hold floats (15.0, 2.5): they are rounded;
    anything that is not a finite number (None, NaN, Infinity, text) becomes default."""
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return int(round(number))


def is_done(status) -> bool:
    return str(status).lower().strip() in DONE_STATUSES


def parse_iso_date(value):
    """'YYYY-MM-DD' -> datetime at midnight; empty -> None. Raises ValueError for anything else."""
    if value in (None, ""):
        return None
    return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d")


def iso_date(value):
    """date/datetime -> 'YYYY-MM-DD' (None stays None)."""
    return value.isoformat()[:10] if value else None       # isoformat is several times faster than strftime


def detect_image_ext(raw: bytes):
    """Image format by the file content, not by what the client claims."""
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    return None


# Name limits shared by the web app, the bot and the templates. A task name has to fit, with a short phrase around it,
# into a pop-up answer to a button (200 characters), see TG_ALERT_LIMIT.
MAX_PROJECT_NAME_LEN = 120
MAX_TASK_NAME_LEN = 120

TG_TEXT_LIMIT = 4096        # Telegram rejects longer messages entirely (characters after the HTML markup is parsed)
TG_ALERT_LIMIT = 200        # text of a pop-up answer to a button (answerCallbackQuery)
TG_SAFE_LIMIT = 3900        # what we aim for: the limit minus room for the "…and N more" line and counting slack


def clip_lines(lines, limit: int = TG_SAFE_LIMIT, more=None) -> list:
    """Keeps whole lines while the text stays within limit. Each line must be complete HTML on its own (tags opened
    and closed in the same line), then cutting between lines never breaks the markup. The cut part is replaced by one
    line from more(n) (n = number of dropped non-empty lines). Length is counted with the tags, so it is an upper bound."""
    out, used = [], 0
    for i, line in enumerate(lines):
        cost = len(line) + 1
        if used + cost > limit:
            rest = sum(1 for x in lines[i:] if x.strip())
            if rest and more:
                out.append(more(rest))
            return out
        out.append(line)
        used += cost
    return out


def short_text(value, n: int) -> str:
    """At most n characters, with "…" when cut."""
    value = "" if value is None else str(value)
    return value if len(value) <= n else value[:max(n - 1, 0)].rstrip() + "…"


def esc(value) -> str:
    """Escapes text for Telegram parse_mode=HTML (&, <, >)."""
    return html.escape("" if value is None else str(value), quote=False)


# ---------------------------------------------------------------------------
# config.json
#   The file is read on almost every request and by every bot update, so the parsed content is cached and
#   re-read only when the file changes on disk (mtime/size). Other processes (notify.py, backup.py) write it
#   with edit_config as well, which replaces the file atomically, so the cache notices their changes too.
#   Callers get their own deep copy. Changes go through edit_config() (a lock against lost concurrent updates).
# ---------------------------------------------------------------------------
_config_lock = threading.Lock()
_config_cache = {"stamp": None, "data": None}


def _file_stamp(path):
    try:
        st = os.stat(path)
        return st.st_mtime_ns, st.st_size, st.st_ino
    except OSError:
        return None


def _read_config_file() -> dict:
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("config.json must contain a JSON object")
        return data
    except ValueError as e:
        # A damaged file must not take the whole app down: keep a copy for manual recovery and start
        # from defaults (the Telegram binding and settings will have to be restored from the copy).
        broken = f"{CONFIG_FILE}.broken-{int(time.time())}"
        try:
            os.replace(CONFIG_FILE, broken)
        except OSError:
            pass
        print(f"[CONFIG] config.json is damaged ({e}); saved as {os.path.basename(broken)}, using defaults")
        return {}


def _normalize_config(cfg: dict) -> dict:
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    if not isinstance(cfg.get("NOTIFY"), dict):
        cfg["NOTIFY"] = dict(DEFAULT_NOTIFY_SETTINGS)
    else:
        for k, v in DEFAULT_NOTIFY_SETTINGS.items():
            cfg["NOTIFY"].setdefault(k, v)
    cfg["SCHEDULE"] = normalize_schedule(cfg.get("SCHEDULE"))
    cfg["LANGUAGE"] = LEGACY_LANGUAGES.get(cfg.get("LANGUAGE"), cfg.get("LANGUAGE"))
    if cfg.get("DATE_FORMAT") not in DATE_FORMATS:
        cfg["DATE_FORMAT"] = DEFAULT_CONFIG["DATE_FORMAT"]
    for key in ("PROJECT_COST", "INITIAL_TOKENS"):        # tokens are whole numbers
        cfg[key] = to_tokens(cfg.get(key), DEFAULT_CONFIG[key])
    if cfg.get("TG_CLEANUP") not in ("keep_46h", "latest_only", "off"):
        cfg["TG_CLEANUP"] = DEFAULT_CONFIG["TG_CLEANUP"]
    days = cfg.get("TRASH_DAYS")
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 3650:
        cfg["TRASH_DAYS"] = DEFAULT_CONFIG["TRASH_DAYS"]
    cfg["CALENDAR_EXPORT"] = cfg.get("CALENDAR_EXPORT") is True
    feed = cfg.get("ICS_FEED")
    if not isinstance(feed, dict) or not isinstance(feed.get("token", ""), str):
        cfg["ICS_FEED"] = dict(DEFAULT_CONFIG["ICS_FEED"])
    else:
        cfg["ICS_FEED"] = {"enabled": bool(feed.get("enabled")), "token": feed.get("token", "")}
    return cfg


def _cached_config() -> dict:
    with _config_lock:
        stamp = _file_stamp(CONFIG_FILE)
        if stamp is None:
            save_config({**DEFAULT_CONFIG, "NOTIFY": dict(DEFAULT_NOTIFY_SETTINGS), "SCHEDULE": normalize_schedule({})})
            stamp = _file_stamp(CONFIG_FILE)
        if stamp != _config_cache["stamp"] or _config_cache["data"] is None:
            # the stamp taken BEFORE reading is stored: if the file changes meanwhile, the next call re-reads it
            _config_cache["data"] = _normalize_config(_read_config_file())
            _config_cache["stamp"] = stamp
        return _config_cache["data"]


def load_config() -> dict:
    """Current settings with defaults filled in. Returns a private copy (safe to modify and save)."""
    return copy.deepcopy(_cached_config())


def config_value(key, default=None):
    """One setting, read without copying the whole config (for hot paths: translations, date formatting).
    Do not modify a returned dict or list."""
    return _cached_config().get(key, default)


def fmt_date(d) -> str:
    """A date (date/datetime) in the format from the settings; for the bot and notifications."""
    return d.strftime(DATE_FORMATS.get(config_value("DATE_FORMAT"), DATE_FORMATS["dmy"]))


class FileLock:
    """Exclusive lock on a file between processes: fcntl on Linux, msvcrt on Windows."""

    def __init__(self, path):
        self.path = path
        self.f = None

    def __enter__(self):
        self.f = open(self.path, "a+")
        if fcntl:
            fcntl.flock(self.f, fcntl.LOCK_EX)        # a second process waits here
        elif msvcrt:
            self.f.seek(0)
            while True:
                try:
                    msvcrt.locking(self.f.fileno(), msvcrt.LK_LOCK, 1)   # retries for ~10 s, then raises OSError
                    break
                except OSError:
                    continue
        return self

    def __exit__(self, *exc):
        try:
            if fcntl:
                fcntl.flock(self.f, fcntl.LOCK_UN)
            elif msvcrt:
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self.f.close()


# Writers of config.json: the web app (several worker threads), the bot and the notify/backup scripts. Every change is
# "read, modify, write"; without a lock two of them at the same moment (saving the settings while the bot binds a chat)
# would each write their own copy and one change would be lost. The thread lock covers one process, the file lock the
# others; nesting in the same thread is allowed (the file lock is taken only by the outermost level).
_edit_lock = threading.RLock()
_edit_depth = threading.local()


@contextlib.contextmanager
def edit_config():
    """Read-modify-write of config.json under a lock:

        with edit_config() as cfg:
            cfg["KEY"] = value

    The file is written on exit only if something changed (and not at all if the block raises)."""
    with _edit_lock:
        depth = getattr(_edit_depth, "n", 0)
        file_lock = FileLock(CONFIG_LOCK_FILE) if depth == 0 else contextlib.nullcontext()
        with file_lock:
            _edit_depth.n = depth + 1
            try:
                _config_cache["stamp"] = None      # another process may have written the file a moment ago
                cfg = load_config()
                before = copy.deepcopy(cfg)
                yield cfg
                if cfg != before:
                    save_config(cfg)
            finally:
                _edit_depth.n = depth


def save_config(cfg: dict) -> None:
    """Atomic write: a temporary file first, then os.replace. To change settings, use edit_config():
    it takes the lock, so concurrent changes are not lost."""
    fd, tmp_path = tempfile.mkstemp(dir=DATA_DIR, prefix=".config.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4, ensure_ascii=False)
        os.replace(tmp_path, CONFIG_FILE)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
    _config_cache["stamp"] = None          # re-read on the next load_config (also normalizes what was saved)


def live_projects(db):
    """Query for projects outside the trash: used everywhere projects are shown or counted."""
    return db.query(Project).filter(Project.deleted_at.is_(None))


def live_tasks(db):
    """Query for tasks of projects outside the trash."""
    return db.query(Task).filter(Task.project_id.in_(db.query(Project.id).filter(Project.deleted_at.is_(None))))


def compute_balance(db, config=None):
    """Returns (balance, earned).

    balance = initial tokens + rewards of finished projects
              - start cost of all projects + adjustments (deleted projects).
    Only the three needed columns are read, without building ORM objects. Tokens are whole numbers.
    """
    if config is None:
        config = load_config()
    q = db.query(Project.status, Project.token, Project.token_reward)
    if not config.get("DELETE_KEEPS_BALANCE", True):
        q = q.filter(Project.deleted_at.is_(None))
    # with DELETE_KEEPS_BALANCE a project in the trash may still come back, so its contribution is kept for now
    earned = spent = 0
    for status, token, reward in q:
        spent += to_tokens(token)
        if is_done(status):
            earned += to_tokens(reward)
    adjustments = sum(to_tokens(amount) for (amount,) in db.query(Adjustment.amount))
    return to_tokens(config.get("INITIAL_TOKENS", 0)) + earned - spent + adjustments, earned


# ---------------------------------------------------------------------------
# Binding the Telegram bot to one chat
#   • TG_CHAT_ID is the only chat the bot answers (messages from others are ignored);
#   • while no chat is bound, the bot accepts only "/start CODE" from a private chat, and the code
#     (TG_PAIRING_CODE) is shown in the app: Settings → Telegram.
# ---------------------------------------------------------------------------
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
