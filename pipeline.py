"""Pipeline templates: stored in config.json (PIPELINE_TEMPLATES) and edited in the settings.

A template: {"id": "print", "name": "...", "steps": ["...", ...]}.
The name and the steps are either a translation key from locales.json (that is how the built-in templates are
written, so they follow a language change) or plain text (whatever the user typed).
If the config has no templates, the two built-in ones apply: "3D print" and "Kit model".
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
    """The string is a translation key (present in locales.json), not user text."""
    return isinstance(raw, str) and translate(raw, DEFAULT_LANG) != raw


def text(raw: str) -> str:
    """Text in the interface language: a key is translated, plain text is returned as is."""
    return tr(raw) if is_key(raw) else raw


def _copy(templates):
    return [{"id": t["id"], "name": t["name"], "steps": list(t["steps"])} for t in templates]


def _clean(raw_list):
    """Validates a list of templates. Returns (templates, None) or (None, error_text)."""
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
    """Templates from the config as stored (keys not translated); the built-in ones if the config is invalid."""
    raw = cfg.get("PIPELINE_TEMPLATES")
    if raw is None:
        return _copy(DEFAULT_TEMPLATES)
    clean, err = _clean(raw)
    return clean if clean else _copy(DEFAULT_TEMPLATES)


def find(cfg: dict, template_id: str):
    return next((t for t in get_templates(cfg) if t["id"] == template_id), None)


def for_client(cfg: dict) -> list:
    """Templates for the UI: texts in the current language plus the stored values (so keys are not lost
    when untouched rows are saved back)."""
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
    """Writes the templates into cfg (without saving the file). Returns an error text or None.
    If the list equals the built-in one, the key is removed from the config: the templates are "default" again."""
    clean, err = _clean(raw_list)
    if err:
        return err
    if clean == DEFAULT_TEMPLATES:
        cfg.pop("PIPELINE_TEMPLATES", None)
    else:
        cfg["PIPELINE_TEMPLATES"] = clean
    return None


def step_variants(raw: str) -> list:
    """Every spelling of a step: for a key, its translation in every language; for text, the text itself."""
    if is_key(raw):
        return [translate(raw, lang["code"]) for lang in available_languages()]
    return [raw]
