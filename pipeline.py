"""Шаблоны пайплайна: хранятся в config.json (PIPELINE_TEMPLATES) и редактируются в настройках.

Шаблон: {"id": "print", "name": "...", "steps": ["...", ...]}.
Название и этапы — либо ключ перевода из locales.json (так записаны встроенные шаблоны, и они
переводятся при смене языка), либо обычный текст (то, что пользователь ввёл сам).
Если в конфиге шаблонов нет, действуют два встроенных: «3D-печать» и «Сборная модель».
"""
import re
import secrets

from i18n import translate, tr, DEFAULT_LANG, available_languages

MAX_TEMPLATES = 20
MAX_STEPS = 30
MAX_NAME_LEN = 60
MAX_STEP_LEN = 120
ID_RE = re.compile(r"^[a-z0-9_-]{1,32}$")

DEFAULT_TEMPLATES = [
    {
        "id": "print",
        "name": "template.print",
        "steps": [
            "pipeline.print.step1", "pipeline.print.step2", "pipeline.print.step3", "pipeline.print.step4",
            "pipeline.print.step5", "pipeline.print.step6", "pipeline.print.step7",
        ],
    },
    {
        "id": "kit",
        "name": "template.kit",
        "steps": [
            "pipeline.kit.step1", "pipeline.kit.step2", "pipeline.kit.step3",
            "pipeline.kit.step4", "pipeline.kit.step5",
        ],
    },
]


def is_key(raw: str) -> bool:
    """Строка — ключ перевода (есть в locales.json), а не текст пользователя."""
    return isinstance(raw, str) and translate(raw, DEFAULT_LANG) != raw


def text(raw: str) -> str:
    """Текст на языке интерфейса: ключ переводится, обычный текст возвращается как есть."""
    return tr(raw) if is_key(raw) else raw


def _copy(templates):
    return [{"id": t["id"], "name": t["name"], "steps": list(t["steps"])} for t in templates]


def _clean(raw_list):
    """Проверяет список шаблонов. Возвращает (шаблоны, None) или (None, текст_ошибки)."""
    if not isinstance(raw_list, list) or not raw_list:
        return None, tr("err.templates_empty")
    if len(raw_list) > MAX_TEMPLATES:
        return None, tr("err.templates_too_many", n=MAX_TEMPLATES)
    result, ids = [], set()
    for item in raw_list:
        if not isinstance(item, dict):
            return None, tr("err.templates_bad")
        name = str(item.get("name") or "").strip()
        if not name:
            return None, tr("err.template_name_empty")
        if len(text(name)) > MAX_NAME_LEN:
            return None, tr("err.name_too_long", n=MAX_NAME_LEN)
        steps = [str(s).strip() for s in (item.get("steps") or []) if str(s).strip()]
        if not steps:
            return None, tr("err.template_no_steps", name=text(name))
        if len(steps) > MAX_STEPS:
            return None, tr("err.template_too_many_steps", name=text(name), n=MAX_STEPS)
        if any(len(text(s)) > MAX_STEP_LEN for s in steps):
            return None, tr("err.name_too_long", n=MAX_STEP_LEN)
        tid = str(item.get("id") or "")
        if not ID_RE.match(tid) or tid in ids:
            tid = "t" + secrets.token_hex(4)
        ids.add(tid)
        result.append({"id": tid, "name": name, "steps": steps})
    return result, None


def get_templates(cfg: dict) -> list:
    """Шаблоны из конфига в исходном виде (ключи не переведены); при ошибке в конфиге — встроенные."""
    raw = cfg.get("PIPELINE_TEMPLATES")
    if raw is None:
        return _copy(DEFAULT_TEMPLATES)
    clean, err = _clean(raw)
    return clean if clean else _copy(DEFAULT_TEMPLATES)


def find(cfg: dict, template_id: str):
    return next((t for t in get_templates(cfg) if t["id"] == template_id), None)


def for_client(cfg: dict) -> list:
    """Шаблоны для интерфейса: тексты на текущем языке и исходные значения (чтобы ключи не терялись
    при сохранении нетронутых строк)."""
    return [{
        "id": t["id"],
        "name": text(t["name"]),
        "steps": [text(s) for s in t["steps"]],
        "raw": {"name": t["name"], "steps": list(t["steps"])},
    } for t in get_templates(cfg)]


def defaults_for_client() -> list:
    return [{
        "id": t["id"],
        "name": text(t["name"]),
        "steps": [text(s) for s in t["steps"]],
        "raw": {"name": t["name"], "steps": list(t["steps"])},
    } for t in DEFAULT_TEMPLATES]


def save(cfg: dict, raw_list) -> str:
    """Записывает шаблоны в cfg (без сохранения файла). Возвращает текст ошибки или None.
    Если список совпадает со встроенным, ключ из конфига убирается: шаблоны снова «по умолчанию»."""
    clean, err = _clean(raw_list)
    if err:
        return err
    if clean == DEFAULT_TEMPLATES:
        cfg.pop("PIPELINE_TEMPLATES", None)
    else:
        cfg["PIPELINE_TEMPLATES"] = clean
    return None


def step_variants(raw: str) -> list:
    """Все написания этапа: для ключа — перевод на каждом языке, для текста — он сам."""
    if is_key(raw):
        return [translate(raw, lang["code"]) for lang in available_languages()]
    return [raw]
