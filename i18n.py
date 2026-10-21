"""Локализация сервера, бота и уведомлений.

Все тексты лежат в ОДНОМ файле locales.json, сгруппированные по языкам (коды BCP-47):

    {
      "ru-RU": {"meta.name": "Русский", "common.save": "Сохранить", "err.name_too_long": "...{n}..."},
      "en-US": {"meta.name": "English", "common.save": "Save",      "err.name_too_long": "...{n}..."}
    }

В коде и шаблонах используются только технические имена ключей — tr("common.save").
Русская фраза в коде больше не нужна: правится только locales.json.

  • tr("key", n=3)  — текст на языке интерфейса; {n} подставляется через str.format.
  • tv(value)       — перевод значения из базы (статус, стадия, день недели). В базе остаются
                      канонические русские значения («В работе», «Покрас»...), чтобы не ломать
                      данные и callback_data бота; здесь они переводятся только для показа.
  • нет ключа в выбранном языке — берётся ru-RU (язык по умолчанию), нет и там — показывается сам ключ.

Новый язык: добавьте в locales.json блок с кодом (например "de-DE") и ключом "meta.name"
(или `python tools/check_locales.py --create de-DE --name Deutsch`) — он сразу появится в
настройках (Основные → Язык интерфейса). Проверка полноты: python tools/check_locales.py
"""
import os
import json

from common import BASE_DIR, CONFIG_FILE, load_config

LOCALES_FILE = os.path.join(BASE_DIR, "locales.json")
DEFAULT_LANG = "ru-RU"
NAME_KEY = "meta.name"

# Значения, которые хранятся в базе по-русски (канонические) -> ключ перевода.
# «—» (стадия не выбрана) намеренно отсутствует: показывается как есть.
VALUE_KEYS = {
    "В планах": "status.planned", "В работе": "status.in_work", "На паузе": "status.on_hold",
    "Закончен": "status.finished", "Не начато": "status.not_started", "В процессе": "status.in_progress",
    "Выполнено": "status.done",
    "Печать": "stage.printing", "Шлифовка": "stage.sanding", "Сборка": "stage.assembly",
    "Грунт": "stage.priming", "Покрас": "stage.painting", "Без стадии": "stage.none",
    "Пн": "day.mon", "Вт": "day.tue", "Ср": "day.wed", "Чт": "day.thu",
    "Пт": "day.fri", "Сб": "day.sat", "Вс": "day.sun",
}

_file_cache = {"mtime": None, "data": {}}
_lang_cache = {"mtime": None, "lang": DEFAULT_LANG}


def _all_locales() -> dict:
    """Весь locales.json: {код: {ключ: текст}}; перечитывается только при изменении файла."""
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
        _file_cache.update(mtime=mtime, data=data)
    return _file_cache["data"]


def load_locale(code: str) -> dict:
    """Таблица одного языка (пустая, если такого блока нет)."""
    return _all_locales().get(code) or {}


def available_languages() -> list:
    """[{'code': 'ru-RU', 'name': 'Русский'}, {'code': 'en-US', 'name': 'English'}, ...]"""
    data = _all_locales()
    codes = sorted(data, key=lambda c: (c != DEFAULT_LANG, c))
    return [{"code": c, "name": str(data[c].get(NAME_KEY, c))} for c in codes if data[c]]


def normalize_lang(code):
    """'en', 'en_us', 'EN-us' -> 'en-US' (если такой язык есть в locales.json); иначе None."""
    if not code:
        return None
    codes = [l["code"] for l in available_languages()]
    if code in codes:
        return code
    flat = str(code).replace("_", "-").lower()
    for c in codes:
        if c.lower() == flat:
            return c
    for c in codes:                      # короткий код: 'ru' -> первый 'ru-*'
        if c.lower().split("-")[0] == flat.split("-")[0]:
            return c
    return None


def is_valid_language(code) -> bool:
    return normalize_lang(code) is not None


def get_lang() -> str:
    """Язык из config.json (LANGUAGE), приведённый к коду из locales.json; иначе ru-RU."""
    try:
        mtime = os.stat(CONFIG_FILE).st_mtime_ns
    except OSError:
        mtime = None
    if mtime != _lang_cache["mtime"]:
        try:
            lang = str(load_config().get("LANGUAGE", DEFAULT_LANG))
        except Exception:
            lang = DEFAULT_LANG
        _lang_cache.update(mtime=mtime, lang=lang)
    return normalize_lang(_lang_cache["lang"]) or DEFAULT_LANG


def table_for(lang: str) -> dict:
    """Словарь для страницы: тексты языка поверх языка по умолчанию (пропуски закрываются ru-RU)."""
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
    """Текст по техническому ключу на языке интерфейса; params подставляются через str.format."""
    text = translate(key, get_lang())
    if not params:
        return text
    try:
        return text.format(**params)
    except (KeyError, IndexError, ValueError):
        return text                      # опечатка в переводе не должна ронять бота/приложение


def tv(value) -> str:
    """Перевод значения из базы (статус, стадия, день недели); незнакомое значение возвращается как есть."""
    key = VALUE_KEYS.get(value)
    return tr(key) if key else ("" if value is None else str(value))
