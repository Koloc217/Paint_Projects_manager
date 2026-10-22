"""Localization of the server, the bot and notifications.

All texts live in ONE file, locales.json, grouped by language (BCP-47 codes):

    {
      "ru-RU": {"meta.name": "Русский", "common.save": "Сохранить", "err.name_too_long": "...{n}..."},
      "en-US": {"meta.name": "English", "common.save": "Save",      "err.name_too_long": "...{n}..."}
    }

Code and templates use technical key names only: tr("common.save"). Wording is edited in locales.json only.

  • tr("key", n=3)  — text in the interface language; {n} is filled in with str.format.
  • tv(value)       — translation of a value stored in the database (status, stage, weekday). The database keeps
                      canonical Russian values ("В работе", "Покрас"...), so data and the bot's callback_data
                      stay stable; they are translated here for display only.
  • a key missing in the chosen language falls back to ru-RU (the default language), then to the key itself.

New language: add a block with its code (e.g. "de-DE") and a "meta.name" key to locales.json
(or run `python tools/check_locales.py --create de-DE --name Deutsch`); it shows up in
Settings → General → Interface language right away. Completeness check: python tools/check_locales.py
"""
import os
import json

from common import BASE_DIR, config_value

LOCALES_FILE = os.path.join(BASE_DIR, "locales.json")
DEFAULT_LANG = "ru-RU"
NAME_KEY = "meta.name"

# Values stored in the database in Russian (canonical) -> translation key.
# "—" (no stage selected) is deliberately absent: it is shown as is.
VALUE_KEYS = {
    "В планах": "status.planned", "В работе": "status.in_work", "На паузе": "status.on_hold",
    "Закончен": "status.finished", "Не начато": "status.not_started", "В процессе": "status.in_progress",
    "Выполнено": "status.done",
    "Печать": "stage.printing", "Шлифовка": "stage.sanding", "Сборка": "stage.assembly",
    "Грунт": "stage.priming", "Покрас": "stage.painting", "Без стадии": "stage.none",
    "Пн": "day.mon", "Вт": "day.tue", "Ср": "day.wed", "Чт": "day.thu",
    "Пт": "day.fri", "Сб": "day.sat", "Вс": "day.sun",
}

_file_cache = {"mtime": None, "data": {}, "languages": []}
_norm_cache = {}           # (requested code, locales mtime) -> normalized code or None


def _all_locales() -> dict:
    """The whole locales.json: {code: {key: text}}; re-read only when the file changes."""
    try:
        mtime = os.stat(LOCALES_FILE).st_mtime_ns
    except OSError:
        return {}
    if mtime != _file_cache["mtime"]:
        try:
            with open(LOCALES_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            data = {code: tbl for code, tbl in raw.items() if isinstance(tbl, dict)} if isinstance(raw, dict) else {}
        except (OSError, ValueError):
            data = {}
        codes = sorted(data, key=lambda c: (c != DEFAULT_LANG, c))
        languages = [{"code": c, "name": str(data[c].get(NAME_KEY, c))} for c in codes if data[c]]
        _file_cache.update(mtime=mtime, data=data, languages=languages)
        _norm_cache.clear()
    return _file_cache["data"]


def load_locale(code: str) -> dict:
    """The table of one language (empty if there is no such block)."""
    return _all_locales().get(code) or {}


def available_languages() -> list:
    """[{'code': 'ru-RU', 'name': 'Русский'}, {'code': 'en-US', 'name': 'English'}, ...]"""
    _all_locales()
    return [dict(lang) for lang in _file_cache["languages"]]


def _normalize(code):
    codes = [lang["code"] for lang in _file_cache["languages"]]
    if code in codes:
        return code
    flat = str(code).replace("_", "-").lower()
    for c in codes:
        if c.lower() == flat:
            return c
    for c in codes:                      # short code: 'ru' -> the first 'ru-*'
        if c.lower().split("-")[0] == flat.split("-")[0]:
            return c
    return None


def normalize_lang(code):
    """'en', 'en_us', 'EN-us' -> 'en-US' (if such a language exists in locales.json); otherwise None."""
    if not code:
        return None
    _all_locales()
    key = (str(code), _file_cache["mtime"])
    if key not in _norm_cache:
        _norm_cache[key] = _normalize(code)
    return _norm_cache[key]


def is_valid_language(code) -> bool:
    return normalize_lang(code) is not None


def get_lang() -> str:
    """Language from config.json (LANGUAGE) mapped to a code from locales.json; ru-RU otherwise.
    Cheap enough to call for every string: config and locales are both cached by file stamp."""
    try:
        lang = str(config_value("LANGUAGE", DEFAULT_LANG))
    except Exception:
        lang = DEFAULT_LANG
    return normalize_lang(lang) or DEFAULT_LANG


def table_for(lang: str) -> dict:
    """Dictionary for the page: the language's texts over the default language (gaps are filled from ru-RU)."""
    data = _all_locales()
    return {**data.get(DEFAULT_LANG, {}), **data.get(lang, {})}


def translate(key: str, lang: str) -> str:
    data = _all_locales()
    for code in (lang, DEFAULT_LANG):
        text = (data.get(code) or {}).get(key)
        if text is not None:
            return text
    return key


def tr(key: str, **params) -> str:
    """Text for a technical key in the interface language; params are filled in with str.format."""
    text = translate(key, get_lang())
    if not params:
        return text
    try:
        return text.format(**params)
    except (KeyError, IndexError, ValueError):
        return text                      # a typo in a translation must not crash the bot or the app


def tv(value) -> str:
    """Translation of a database value (status, stage, weekday); an unknown value is returned as is."""
    key = VALUE_KEYS.get(value)
    return tr(key) if key else ("" if value is None else str(value))
