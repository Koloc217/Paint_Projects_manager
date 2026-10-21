"""Общие утилиты: конфиг, экранирование, подсчёт баланса токенов."""
import os
import json
import html
import hmac
import secrets
import tempfile

from database import Project, Adjustment

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

DONE_STATUSES = ("закончен", "завершено", "завершен", "completed")

DEFAULT_NOTIFY_SETTINGS = {
    "allow_postpone": True,
    "stagnation_alert": True,
    "stagnation_days": 10,
    "friday_digest": True,
    "monthly_report": True,
}

# Расписание встроенного планировщика (заменяет cron).
# Время — локальное, формат ЧЧ:ММ. Пуши настраиваются отдельно для каждого дня недели.
DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")   # индекс = datetime.weekday()
DAY_LABELS = {"mon": "Пн", "tue": "Вт", "wed": "Ср", "thu": "Чт", "fri": "Пт", "sat": "Сб", "sun": "Вс"}


def normalize_schedule(raw) -> dict:
    """Приводит SCHEDULE к актуальному виду и мигрирует старый формат
    (notify_weekday_time / notify_weekend_time -> notify_days по дням недели)."""
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
    s.setdefault("backup_time", "03:00")      # каждый день
    return s


DEFAULT_SCHEDULE = normalize_schedule({})

# прежние короткие коды языка (до перехода на один файл locales.json) -> текущие
LEGACY_LANGUAGES = {"ru": "ru-RU", "en": "en-US"}

DEFAULT_CONFIG = {
    "DEV_MODE": False,
    "INITIAL_TOKENS": 0,
    "PROJECT_COST": 10,
    # True: удаление проекта не меняет баланс токенов
    # (потраченное не возвращается, заработанное не пропадает).
    "DELETE_KEEPS_BALANCE": True,
    # язык интерфейса, бота и уведомлений: код блока из locales.json (ru-RU, en-US, ...)
    "LANGUAGE": "ru-RU",
    # формат дат в интерфейсе, боте и уведомлениях: dmy = 21.03.2027, mdy = 03/21/2027
    "DATE_FORMAT": "dmy",
}

DATE_FORMATS = {"dmy": "%d.%m.%Y", "mdy": "%m/%d/%Y"}


def is_done(status) -> bool:
    return str(status).lower().strip() in DONE_STATUSES


def esc(value) -> str:
    """Экранирование текста для Telegram parse_mode=HTML (&, <, >)."""
    return html.escape("" if value is None else str(value), quote=False)


def load_config() -> dict:
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        save_config({**DEFAULT_CONFIG, "NOTIFY": dict(DEFAULT_NOTIFY_SETTINGS), "SCHEDULE": normalize_schedule({})})
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)

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
    return cfg


def fmt_date(d) -> str:
    """Дата (date/datetime) строкой в формате из настроек; для бота и уведомлений."""
    return d.strftime(DATE_FORMATS[load_config()["DATE_FORMAT"]])


def save_config(cfg: dict) -> None:
    """Атомарная запись: сначала во временный файл, затем os.replace."""
    fd, tmp_path = tempfile.mkstemp(dir=BASE_DIR, prefix=".config.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4, ensure_ascii=False)
        os.replace(tmp_path, CONFIG_FILE)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def compute_balance(db, config=None):
    """Возвращает (баланс, заработано).

    баланс = стартовые токены + награды завершённых проектов
             − стоимость всех проектов + корректировки (удалённые проекты).
    """
    if config is None:
        config = load_config()
    projects = db.query(Project).all()
    earned = sum((p.token_reward or 0) for p in projects if is_done(p.status))
    spent = sum((p.token or 0) for p in projects)
    adjustments = sum((a.amount or 0) for a in db.query(Adjustment).all())
    return config.get("INITIAL_TOKENS", 0) + earned - spent + adjustments, earned


# ---------------------------------------------------------------------------
# Привязка Telegram-бота к одному чату
#   • TG_CHAT_ID — единственный чат, которому бот отвечает (чужие сообщения игнорируются);
#   • пока чат не привязан, бот принимает только «/start КОД» из личного чата, а код
#     (TG_PAIRING_CODE) показывается в приложении: Настройки → Telegram.
# ---------------------------------------------------------------------------
PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"    # без похожих символов (0/O, 1/I)


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
    """Код привязки для непривязанного бота (создаётся при первом обращении). Если чат привязан — None."""
    cfg = load_config()
    if is_chat_bound(cfg):
        return None
    code = cfg.get("TG_PAIRING_CODE")
    if not code:
        code = _new_code()
        cfg["TG_PAIRING_CODE"] = code
        save_config(cfg)
    return code


def new_pairing_code():
    cfg = load_config()
    if is_chat_bound(cfg):
        return None
    cfg["TG_PAIRING_CODE"] = _new_code()
    save_config(cfg)
    return cfg["TG_PAIRING_CODE"]


def check_pairing_code(text) -> bool:
    cfg = load_config()
    code = cfg.get("TG_PAIRING_CODE")
    if is_chat_bound(cfg) or not code:
        return False
    return hmac.compare_digest(normalize_pairing_code(code), normalize_pairing_code(text))


def bind_chat(chat_id) -> None:
    cfg = load_config()
    cfg["TG_CHAT_ID"] = chat_id
    cfg.pop("TG_PAIRING_CODE", None)           # код одноразовый
    save_config(cfg)


def unlink_chat() -> str:
    cfg = load_config()
    cfg.pop("TG_CHAT_ID", None)
    cfg["TG_PAIRING_CODE"] = _new_code()
    save_config(cfg)
    return cfg["TG_PAIRING_CODE"]
