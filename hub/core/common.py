"""Shared helpers: config file, canonical values, escaping, token balance, Telegram chat pairing."""
import contextlib
import copy
import html
import hmac
import json
import os
import re
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

from hub.core.database import Project, Task, Adjustment
from hub.paths import BASE_DIR, DATA_DIR      # noqa: F401  (re-exported: other modules import them from here)

# BASE_DIR: the code and its assets (templates, static, locales.json). DATA_DIR: tracker.db, config.json, .env,
# auth.json, photos/, logs/ and service files (see hub/paths.py).
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
    # the Google Sheets backup was removed: its settings drop out of config.json on the next save
    s.pop("backup_enabled", None)
    s.pop("backup_time", None)
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
    # the bot asks how long a task took right after it is closed there (quick buttons, can be skipped)
    "ASK_SPENT_TIME": True,
    # calendar subscription (/cal/<token>.ics): off by default; the token is generated when it is turned on
    "ICS_FEED": {"enabled": False, "token": ""},
    # API for external devices (e-ink frame, ESP32 screens), /api/v1/...: off by default, see devices.py.
    # key: optional secret for devices (required when app login is on); hot_days: how close a deadline is "hot"
    "DEVICE_API": {"enabled": False, "key": "", "hot_days": 3},
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
    if _is_avif(raw):
        return "avif"
    return None


AVIF_BRANDS = (b"avif", b"avis")     # still image / image sequence


def _is_avif(raw: bytes) -> bool:
    """AVIF is an ISO BMFF file that starts with an 'ftyp' box naming avif/avis as the major or a compatible brand.
    HEIC uses the same container with other brands (heic, mif1 without avif) and is not accepted: browsers can't show it."""
    if len(raw) < 16 or raw[4:8] != b"ftyp":
        return False
    size = int.from_bytes(raw[:4], "big")
    if size < 16 or size > min(len(raw), 4096):
        return False
    if raw[8:12] in AVIF_BRANDS:
        return True
    brands = raw[16:size]                 # after major brand (4) and minor version (4)
    return any(brands[i:i + 4] in AVIF_BRANDS for i in range(0, len(brands) - 3, 4))


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
#   re-read only when the file changes on disk (mtime/size). Other processes (notify.py) write it
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


_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_BOOL_KEYS = ("DEV_MODE", "DELETE_KEEPS_BALANCE", "CALENDAR_EXPORT", "ASK_SPENT_TIME")


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _normalize_config(cfg: dict, problems: list = None) -> dict:
    """Fills in defaults and replaces invalid values with the default. Every replaced value that was present in
    the file is added to `problems` as (key, bad value, value used), so the log can say what was wrong."""
    problems = problems if problems is not None else []

    def bad(key, value, used):
        problems.append((key, value, used))

    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, copy.deepcopy(v))
    for key in _BOOL_KEYS:
        if not isinstance(cfg.get(key), bool):
            bad(key, cfg.get(key), DEFAULT_CONFIG[key])
            cfg[key] = DEFAULT_CONFIG[key]

    if not isinstance(cfg.get("NOTIFY"), dict):
        if "NOTIFY" in cfg:
            bad("NOTIFY", cfg["NOTIFY"], "defaults")
        cfg["NOTIFY"] = dict(DEFAULT_NOTIFY_SETTINGS)
    else:
        notify = cfg["NOTIFY"]
        for k, v in DEFAULT_NOTIFY_SETTINGS.items():
            notify.setdefault(k, v)
            if isinstance(v, bool) and not isinstance(notify[k], bool):
                bad(f"NOTIFY.{k}", notify[k], v)
                notify[k] = v
        days = notify.get("stagnation_days")
        if not _is_int(days) or not 1 <= days <= 90:
            bad("NOTIFY.stagnation_days", days, DEFAULT_NOTIFY_SETTINGS["stagnation_days"])
            notify["stagnation_days"] = DEFAULT_NOTIFY_SETTINGS["stagnation_days"]

    if "SCHEDULE" in cfg and not isinstance(cfg["SCHEDULE"], dict):
        bad("SCHEDULE", cfg["SCHEDULE"], "defaults")
    cfg["SCHEDULE"] = normalize_schedule(cfg.get("SCHEDULE"))
    sched = cfg["SCHEDULE"]
    for key, day in sched["notify_days"].items():
        if not _HHMM.match(day["time"]):
            used = "18:05" if DAY_KEYS.index(key) < 5 else "12:05"
            bad(f"SCHEDULE.notify_days.{key}.time", day["time"], used)
            day["time"] = used
    if not isinstance(sched.get("notify_enabled"), bool):
        bad("SCHEDULE.notify_enabled", sched.get("notify_enabled"), True)
        sched["notify_enabled"] = True

    cfg["LANGUAGE"] = LEGACY_LANGUAGES.get(cfg.get("LANGUAGE"), cfg.get("LANGUAGE"))
    if not isinstance(cfg.get("LANGUAGE"), str) or not cfg["LANGUAGE"]:
        bad("LANGUAGE", cfg.get("LANGUAGE"), DEFAULT_CONFIG["LANGUAGE"])
        cfg["LANGUAGE"] = DEFAULT_CONFIG["LANGUAGE"]
    if cfg.get("DATE_FORMAT") not in DATE_FORMATS:
        bad("DATE_FORMAT", cfg.get("DATE_FORMAT"), DEFAULT_CONFIG["DATE_FORMAT"])
        cfg["DATE_FORMAT"] = DEFAULT_CONFIG["DATE_FORMAT"]
    for key in ("PROJECT_COST", "INITIAL_TOKENS"):        # tokens are whole numbers
        value = cfg.get(key)
        fixed = to_tokens(value, DEFAULT_CONFIG[key])
        if isinstance(value, bool) or not isinstance(value, (int, float)) or fixed < 0 or fixed > MAX_TOKENS:
            fixed = DEFAULT_CONFIG[key]
            bad(key, value, fixed)
        cfg[key] = fixed                                  # 15.0 -> 15 is a silent clean-up, not a problem
    if cfg.get("TG_CLEANUP") not in ("keep_46h", "latest_only", "off"):
        bad("TG_CLEANUP", cfg.get("TG_CLEANUP"), DEFAULT_CONFIG["TG_CLEANUP"])
        cfg["TG_CLEANUP"] = DEFAULT_CONFIG["TG_CLEANUP"]
    days = cfg.get("TRASH_DAYS")
    if not _is_int(days) or not 1 <= days <= 3650:
        bad("TRASH_DAYS", days, DEFAULT_CONFIG["TRASH_DAYS"])
        cfg["TRASH_DAYS"] = DEFAULT_CONFIG["TRASH_DAYS"]
    feed = cfg.get("ICS_FEED")
    if not isinstance(feed, dict) or not isinstance(feed.get("token", ""), str):
        bad("ICS_FEED", feed, "off")
        cfg["ICS_FEED"] = dict(DEFAULT_CONFIG["ICS_FEED"])
    else:
        cfg["ICS_FEED"] = {"enabled": bool(feed.get("enabled")), "token": feed.get("token", "")}
    dev = cfg.get("DEVICE_API")
    if not isinstance(dev, dict):
        bad("DEVICE_API", dev, "off")
        cfg["DEVICE_API"] = dict(DEFAULT_CONFIG["DEVICE_API"])
    else:
        fixed = {**DEFAULT_CONFIG["DEVICE_API"], **dev}
        if not isinstance(fixed["enabled"], bool):
            bad("DEVICE_API.enabled", fixed["enabled"], False)
            fixed["enabled"] = False
        if not isinstance(fixed["key"], str):
            bad("DEVICE_API.key", fixed["key"], "")
            fixed["key"] = ""
        if not _is_int(fixed["hot_days"]) or not 0 <= fixed["hot_days"] <= 365:
            bad("DEVICE_API.hot_days", fixed["hot_days"], DEFAULT_CONFIG["DEVICE_API"]["hot_days"])
            fixed["hot_days"] = DEFAULT_CONFIG["DEVICE_API"]["hot_days"]
        cfg["DEVICE_API"] = {k: fixed[k] for k in DEFAULT_CONFIG["DEVICE_API"]}
    return cfg


def _report_config_problems(problems: list) -> None:
    """One line per invalid value: what the file has and what the app uses instead. The file itself is not
    changed (a typo can be fixed by hand; the next save from the settings writes the value in use)."""
    for key, value, used in problems:
        shown = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else f'"{value}"'
        print(f"[CONFIG] config.json: {key} = {shown[:80]} is invalid, using {json.dumps(used, ensure_ascii=False)}")


def _cached_config() -> dict:
    with _config_lock:
        stamp = _file_stamp(CONFIG_FILE)
        if stamp is None:
            save_config({**DEFAULT_CONFIG, "NOTIFY": dict(DEFAULT_NOTIFY_SETTINGS), "SCHEDULE": normalize_schedule({})})
            stamp = _file_stamp(CONFIG_FILE)
        if stamp != _config_cache["stamp"] or _config_cache["data"] is None:
            # the stamp taken BEFORE reading is stored: if the file changes meanwhile, the next call re-reads it
            problems = []
            _config_cache["data"] = _normalize_config(_read_config_file(), problems)
            _config_cache["stamp"] = stamp
            _report_config_problems(problems)
        return _config_cache["data"]


def load_config() -> dict:
    """Current settings with defaults filled in. Returns a private copy (safe to modify and save)."""
    return copy.deepcopy(_cached_config())


def config_value(key, default=None):
    """One setting, read without copying the whole config (for hot paths: translations, date formatting).
    Do not modify a returned dict or list."""
    return _cached_config().get(key, default)


def fmt_date(d) -> str:
    """A date (date/datetime) in the format from the settings; for the bot and notifications. None -> ""."""
    if d is None:
        return ""
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
    """Query for tasks of projects outside the trash, plus general tasks (no project)."""
    from sqlalchemy import or_
    return db.query(Task).filter(or_(Task.project_id.is_(None),
                                     Task.project_id.in_(db.query(Project.id).filter(Project.deleted_at.is_(None)))))


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
