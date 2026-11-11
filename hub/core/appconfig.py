"""config.json: defaults, validation, a cached reader and a locked read-modify-write.

The file is read on almost every request and by every bot update, so the parsed content is cached and re-read only
when the file changes on disk (mtime/size). Other processes (the notify job) write it with edit_config() as well, which
replaces the file atomically, so the cache notices their changes too. Callers get their own deep copy.
Changes go through edit_config() (a lock against lost concurrent updates).
"""
import contextlib
import copy
import json
import os
import re
import tempfile
import threading
import time

from hub.core.constants import DATE_FORMATS, DAY_KEYS, MAX_TOKENS, TG_CLEANUP_MODES
from hub.core.convert import to_tokens
from hub.core.filelock import FileLock
from hub.paths import DATA_DIR

CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
CONFIG_LOCK_FILE = os.path.join(DATA_DIR, ".config.lock")

DEFAULT_NOTIFY_SETTINGS = {
    "allow_postpone": True,
    "stagnation_alert": True,
    "stagnation_days": 10,
    "friday_digest": True,
    "monthly_report": True,
}

# Schedule of the built-in scheduler (replaces cron).
# Local time, HH:MM. Pushes are configured separately for every day of the week.

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


# ---------------------------------------------------------------------------
# Validation. Every rule is "this key must satisfy this check, else it is replaced by the default and reported".
# `problems` collects (key, bad value, value used) so the log can say what was wrong in the file.
# ---------------------------------------------------------------------------
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_BOOL_KEYS = ("DEV_MODE", "DELETE_KEEPS_BALANCE", "CALENDAR_EXPORT", "ASK_SPENT_TIME")


def _is_bool(value) -> bool:
    return isinstance(value, bool)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _int_between(low: int, high: int):
    return lambda value: _is_int(value) and low <= value <= high


def _one_of(options):
    return lambda value: isinstance(value, str) and value in options


def _fix(box: dict, key: str, ok, default, problems: list, label: str = None) -> None:
    """Replaces box[key] with default (and reports it) unless ok(value)."""
    value = box.get(key)
    if not ok(value):
        problems.append((label or key, value, default))
        box[key] = default


# (key, check) for the plain top-level settings; the default comes from DEFAULT_CONFIG
_SCALAR_RULES = (
    *((key, _is_bool) for key in _BOOL_KEYS),
    ("DATE_FORMAT", _one_of(DATE_FORMATS)),
    ("TG_CLEANUP", _one_of(TG_CLEANUP_MODES)),
    ("TRASH_DAYS", _int_between(1, 3650)),
)


def _fix_notify(cfg: dict, problems: list) -> None:
    notify = cfg.get("NOTIFY")
    if not isinstance(notify, dict):
        if "NOTIFY" in cfg:
            problems.append(("NOTIFY", notify, "defaults"))
        cfg["NOTIFY"] = dict(DEFAULT_NOTIFY_SETTINGS)
        return
    for key, default in DEFAULT_NOTIFY_SETTINGS.items():
        notify.setdefault(key, default)
        if isinstance(default, bool):
            _fix(notify, key, _is_bool, default, problems, f"NOTIFY.{key}")
    _fix(notify, "stagnation_days", _int_between(1, 90), DEFAULT_NOTIFY_SETTINGS["stagnation_days"], problems,
         "NOTIFY.stagnation_days")


def _fix_schedule(cfg: dict, problems: list) -> None:
    if "SCHEDULE" in cfg and not isinstance(cfg["SCHEDULE"], dict):
        problems.append(("SCHEDULE", cfg["SCHEDULE"], "defaults"))
    cfg["SCHEDULE"] = sched = normalize_schedule(cfg.get("SCHEDULE"))
    for index, key in enumerate(DAY_KEYS):
        default = "18:05" if index < 5 else "12:05"          # weekdays in the evening, the weekend at noon
        _fix(sched["notify_days"][key], "time", lambda v: isinstance(v, str) and bool(_HHMM.match(v)), default,
             problems, f"SCHEDULE.notify_days.{key}.time")
    _fix(sched, "notify_enabled", _is_bool, True, problems, "SCHEDULE.notify_enabled")


def _fix_language(cfg: dict, problems: list) -> None:
    value = cfg.get("LANGUAGE")
    if isinstance(value, str):
        cfg["LANGUAGE"] = LEGACY_LANGUAGES.get(value, value)
    _fix(cfg, "LANGUAGE", lambda v: isinstance(v, str) and bool(v), DEFAULT_CONFIG["LANGUAGE"], problems)


def _fix_token_amounts(cfg: dict, problems: list) -> None:
    """Tokens are whole numbers; 15.0 -> 15 is a silent clean-up, not a problem."""
    for key in ("PROJECT_COST", "INITIAL_TOKENS"):
        value = cfg.get(key)
        fixed = to_tokens(value, DEFAULT_CONFIG[key])
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= fixed <= MAX_TOKENS:
            fixed = DEFAULT_CONFIG[key]
            problems.append((key, value, fixed))
        cfg[key] = fixed


def _fix_ics_feed(cfg: dict, problems: list) -> None:
    feed = cfg.get("ICS_FEED")
    if not isinstance(feed, dict) or not isinstance(feed.get("token", ""), str):
        problems.append(("ICS_FEED", feed, "off"))
        cfg["ICS_FEED"] = dict(DEFAULT_CONFIG["ICS_FEED"])
    else:
        cfg["ICS_FEED"] = {"enabled": bool(feed.get("enabled")), "token": feed.get("token", "")}


def _fix_device_api(cfg: dict, problems: list) -> None:
    defaults = DEFAULT_CONFIG["DEVICE_API"]
    dev = cfg.get("DEVICE_API")
    if not isinstance(dev, dict):
        problems.append(("DEVICE_API", dev, "off"))
        cfg["DEVICE_API"] = dict(defaults)
        return
    fixed = {**defaults, **dev}
    _fix(fixed, "enabled", _is_bool, False, problems, "DEVICE_API.enabled")
    _fix(fixed, "key", lambda v: isinstance(v, str), "", problems, "DEVICE_API.key")
    _fix(fixed, "hot_days", _int_between(0, 365), defaults["hot_days"], problems, "DEVICE_API.hot_days")
    cfg["DEVICE_API"] = {k: fixed[k] for k in defaults}


def _normalize_config(cfg: dict, problems: list = None) -> dict:
    """Fills in defaults and replaces invalid values with the default. Every replaced value that was present in
    the file is added to `problems` as (key, bad value, value used), so the log can say what was wrong."""
    problems = problems if problems is not None else []
    for key, default in DEFAULT_CONFIG.items():
        cfg.setdefault(key, copy.deepcopy(default))
    for key, ok in _SCALAR_RULES:
        _fix(cfg, key, ok, DEFAULT_CONFIG[key], problems)
    _fix_token_amounts(cfg, problems)
    _fix_language(cfg, problems)
    _fix_notify(cfg, problems)
    _fix_schedule(cfg, problems)
    _fix_ics_feed(cfg, problems)
    _fix_device_api(cfg, problems)
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
