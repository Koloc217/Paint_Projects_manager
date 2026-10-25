#!/usr/bin/env python3
"""Checks the translation file locales.json (one file, blocks per language: ru-RU, en-US, ...).

  python tools/check_locales.py                  check the file and the code
  python tools/check_locales.py --fix            add missing keys (the ru-RU text becomes the value)
                                                 and sort the file
  python tools/check_locales.py --create de-DE --name Deutsch
                                                 add a language: a copy of all ru-RU keys to translate

What is checked:
  • every key the code uses through tr("...") / tv values exists in ALL languages;
  • all languages have the same set of keys and the same {placeholders};
  • the file has no keys the code does not use (extra keys are a warning);
  • tr() calls use technical keys (area.name), not text.
Exit code 1 if anything is missing, placeholders differ, or there is "text instead of a key".
"""
import argparse
import ast
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCALES_FILE = os.path.join(ROOT, "locales.json")
DEFAULT_LANG = "ru-RU"
NAME_KEY = "meta.name"

KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
PY_FILES = ["app.py", "bot.py", "notify.py", "common.py", "scheduler.py", "database.py", "i18n.py", "auth.py", "pipeline.py", "vacation.py", "tgproxy.py", "ics.py"]
JS_FILES = ["static/app.js", "static/progress.js", "static/dropzone.js", "static/trash.js", "static/calendar.js", "static/i18n.js", "static/auth.js", "static/pwa.js", "static/dateinput.js", "static/templates.js"]
HTML_FILES = ["templates/index.html", "templates/components/nav.html", "templates/components/tabs.html",
              "templates/components/modals.html", "templates/components/toasts.html", "templates/login.html"]

JS_TR = re.compile(r"""\btr\(\s*(['"`])((?:\\.|(?!\1).)*)\1""", re.S)
HTML_TR = re.compile(r"""\btr\(\s*(['"])((?:\\.|(?!\1).)*)\1""", re.S)
ANY_KEY_LITERAL = re.compile(r"""(['"`])([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)\1""")


def read(rel):
    path = os.path.join(ROOT, rel)
    return open(path, encoding="utf-8").read() if os.path.isfile(path) else ""


def placeholders(text):
    return set(re.findall(r"\{\w+\}", text))


def scan_code():
    """-> (used, literals): used are keys from tr() calls; literals are any area.name-like strings in the code."""
    used, literals = {}, set()

    def add(key, where):
        used.setdefault(key, set()).add(where)

    for rel in PY_FILES:
        src = read(rel)
        if not src:
            continue
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "tr" and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                add(node.args[0].value, rel)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and KEY_RE.match(node.value):
                literals.add(node.value)
    for rel in JS_FILES:
        src = read(rel)
        for m in JS_TR.finditer(src):
            add(m.group(2), rel)
        literals.update(m.group(2) for m in ANY_KEY_LITERAL.finditer(src))
    for rel in HTML_FILES:
        src = read(rel)
        for m in HTML_TR.finditer(src):
            add(m.group(2), rel)
    # database values that are shown through tv()
    sys.path.insert(0, ROOT)
    try:
        from i18n import VALUE_KEYS
        for key in VALUE_KEYS.values():
            add(key, "i18n.VALUE_KEYS")
    finally:
        sys.path.pop(0)
    return used, literals


def load():
    with open(LOCALES_FILE, encoding="utf-8") as f:
        return json.load(f)


def save(data):
    ordered = {}
    for code in sorted(data, key=lambda c: (c != DEFAULT_LANG, c)):
        ordered[code] = dict(sorted(data[code].items()))
    with open(LOCALES_FILE, "w", encoding="utf-8") as f:
        json.dump(ordered, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--create", metavar="CODE")
    ap.add_argument("--name")
    args = ap.parse_args()

    data = load()
    if DEFAULT_LANG not in data:
        print(f"В locales.json нет языка по умолчанию {DEFAULT_LANG}")
        return 1

    if args.create:
        if args.create in data:
            print(f"Язык {args.create} уже есть в locales.json")
            return 1
        data[args.create] = dict(data[DEFAULT_LANG])
        data[args.create][NAME_KEY] = args.name or args.create
        save(data)
        print(f"Добавлен {args.create}: {len(data[args.create])} ключей скопированы с {DEFAULT_LANG} — переведите значения.")
        return 0

    used, literals = scan_code()
    problems = 0

    for key, where in sorted(used.items()):
        if not KEY_RE.match(key):
            print(f"ТЕКСТ ВМЕСТО КЛЮЧА  {key!r}  ({', '.join(sorted(where))}) — используйте технический ключ area.name")
            problems += 1

    all_keys = set().union(*(set(t) for t in data.values()))
    for code, table in data.items():
        wanted = {k for k in set(used) if KEY_RE.match(k)} | all_keys
        missing = sorted(k for k in wanted if k not in table)
        for k in missing:
            src = ", ".join(sorted(used.get(k, {"другой язык"})))
            print(f"[{code}] нет ключа  {k}  ({src})")
        problems += len(missing)
        if args.fix and missing:
            for k in missing:
                table[k] = data[DEFAULT_LANG].get(k, k)

    base = data[DEFAULT_LANG]
    for code, table in data.items():
        if code == DEFAULT_LANG:
            continue
        for k, v in table.items():
            if k in base and placeholders(v) != placeholders(base[k]):
                print(f"[{code}] не совпали подстановки  {k}: {sorted(placeholders(v))} вместо {sorted(placeholders(base[k]))}")
                problems += 1

    unused = sorted(k for k in all_keys if k != NAME_KEY and k not in used and k not in literals)
    for k in unused:
        print(f"предупреждение: ключ не используется в коде  {k}")

    if args.fix:
        save(data)
        print("locales.json дополнен и отсортирован.")
    langs = ", ".join(f"{c} ({len(t)})" for c, t in data.items())
    print(f"языки: {langs}; используется в коде: {len(used)} ключей; проблем: {problems}")
    return 1 if problems and not args.fix else 0


if __name__ == "__main__":
    sys.exit(main())
