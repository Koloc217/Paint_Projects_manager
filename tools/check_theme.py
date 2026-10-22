#!/usr/bin/env python3
"""Checks a UI theme (static/themes.css + the THEMES registry in static/app.js).

  python tools/check_theme.py                 check every theme in the THEMES registry
  python tools/check_theme.py theme-neon      check one theme

A theme in themes.css must sit between markers:

    /* === THEME: theme-neon === */
    ... rules, each one starting with body.theme-neon ...
    /* === END THEME: theme-neon === */

Errors (exit code 1): the theme is not registered or has no markers, a selector does not start with body.<id>,
one of the --chart-* tokens is missing, external links (http/https, @import, @font-face), unbalanced braces,
@keyframes without the <id>- prefix.
Warnings: low contrast of charts and text, properties without !important (the Tailwind CDN may override them),
layout properties (sizes, margins, positioning), which have to be checked by eye.
Details and requirements: THEMES_GUIDE.md
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS = os.path.join(ROOT, "static", "themes.css")
APP_JS = os.path.join(ROOT, "static", "app.js")
INDEX = os.path.join(ROOT, "templates", "index.html")

CHART_TOKENS = [
    "--chart-font", "--chart-text", "--chart-muted", "--chart-grid",
    "--chart-stage-1", "--chart-stage-2", "--chart-stage-3", "--chart-stage-4", "--chart-stage-5", "--chart-stage-6",
    "--chart-series-1", "--chart-series-2", "--chart-border", "--chart-border-width", "--chart-radius",
]
ID_RE = re.compile(r"^theme-[a-z0-9]+(-[a-z0-9]+)*$")
LAYOUT_PROPS = {"width", "min-width", "max-width", "height", "min-height", "max-height", "margin", "margin-top",
                "margin-bottom", "margin-left", "margin-right", "padding", "padding-top", "padding-bottom",
                "padding-left", "padding-right", "position", "top", "left", "right", "bottom", "display",
                "flex", "flex-direction", "grid-template-columns", "gap", "float", "z-index"}


def registered_themes():
    src = open(APP_JS, encoding="utf-8").read()
    m = re.search(r"const THEMES = \[(.*?)\];", src, re.S)
    if not m:
        return None
    return [i for i in re.findall(r"id:\s*'([^']+)'", m.group(1)) if i != "default"]


def theme_block(css, tid):
    m = re.search(r"/\*\s*===\s*THEME:\s*%s\s*===\s*\*/(.*?)/\*\s*===\s*END THEME:\s*%s\s*===\s*\*/" % (re.escape(tid), re.escape(tid)), css, re.S)
    return m.group(1) if m else None


def strip_comments(text):
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def split_selectors(sel):
    parts, depth, cur = [], 0, ""
    for ch in sel:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def parse_rules(text):
    """-> [(context, selector, body)], brace errors. Understands one nesting level (@media, @keyframes)."""
    rules, errors, stack, buf, i = [], [], [], "", 0
    ctx_stack = []
    while i < len(text):
        ch = text[i]
        if ch == "{":
            head = buf.strip()
            buf = ""
            stack.append(head)
            if head.startswith("@"):
                ctx_stack.append(head)
                body_start = None
            else:
                end = text.find("}", i)
                if end < 0:
                    errors.append(f"не закрыта скобка после «{head[:50]}»")
                    break
                rules.append((ctx_stack[-1] if ctx_stack else "", head, text[i + 1:end]))
                stack.pop()
                i = end
        elif ch == "}":
            if not stack:
                errors.append("лишняя закрывающая скобка")
            else:
                head = stack.pop()
                if head.startswith("@") and ctx_stack:
                    ctx_stack.pop()
            buf = ""
        else:
            buf += ch
        i += 1
    if stack:
        errors.append(f"не закрыта скобка: {stack[-1][:50]}")
    return rules, errors


def hex_rgb(value):
    m = re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", value.strip())
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def luminance(rgb):
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def decls(body):
    out = {}
    for part in re.findall(r"([-\w]+)\s*:\s*([^;]+);?", body):
        out[part[0].strip()] = part[1].strip()
    return out


def check(tid, css, fonts_html):
    errors, warnings = [], []
    if not ID_RE.match(tid):
        errors.append("имя темы должно быть вида theme-<имя> (латиница, цифры, дефисы)")
    block = theme_block(css, tid)
    if block is None:
        errors.append(f"в themes.css нет блока между маркерами /* === THEME: {tid} === */ и /* === END THEME: {tid} === */")
        return errors, warnings
    block = strip_comments(block)
    if re.search(r"@import|@font-face|url\(\s*['\"]?\s*(https?:)?//", block, re.I):
        errors.append("внешние подключения запрещены (@import, @font-face, url(http...)); шрифты — только ссылкой в index.html, картинки — файлами в static/backgrounds/")
    rules, brace_errors = parse_rules(block)
    errors.extend(brace_errors)
    prefix = f"body.{tid}"
    root_decls, all_decls = {}, []
    non_important, layout_hits = 0, {}
    for ctx, head, body in rules:
        if ctx.startswith("@keyframes"):
            if not re.match(r"@keyframes\s+%s-" % re.escape(tid), ctx):
                errors.append(f"имя анимации должно начинаться с «{tid}-»: {ctx}")
            continue
        for sel in split_selectors(head):
            if not (sel == prefix or sel.startswith(prefix + " ") or sel.startswith(prefix + ".") or sel.startswith(prefix + ":")
                    or sel.startswith(prefix + ">") or sel.startswith(prefix + "[") or sel.startswith(prefix + "#")):
                errors.append(f"селектор вне темы: «{sel[:70]}» (должен начинаться с {prefix})")
        d = decls(body)
        all_decls.append((head, body))
        if head.strip() == prefix:
            root_decls.update(d)
        for m in re.finditer(r"([-\w]+)\s*:\s*([^;]+?)\s*(!important)?\s*;", body + ";"):
            prop, _, imp = m.group(1), m.group(2), m.group(3)
            if prop.startswith("--"):
                continue
            if not imp and "::" not in head and prop not in ("transition", "transform", "animation", "content"):
                non_important += 1
            if prop in LAYOUT_PROPS and "::" not in head:
                layout_hits[prop] = layout_hits.get(prop, 0) + 1
    missing = [t for t in CHART_TOKENS if t not in root_decls]
    if missing:
        errors.append("в правиле «%s» не заданы токены графиков: %s" % (prefix, ", ".join(missing)))
    if not any(k.startswith("background") for k in root_decls) or "color" not in root_decls:
        warnings.append(f"в правиле «{prefix}» нет background и color: фон и цвет текста страницы остаются стандартными")
    # contrast
    bg = None
    for key in ("background-color", "background"):
        if key in root_decls:
            bg = hex_rgb(re.sub(r"\s*!important", "", root_decls[key]).split()[0]) if root_decls[key] else None
            if bg:
                break
    if bg:
        for tok in ("--chart-text", "--chart-muted", "color"):
            v = root_decls.get(tok)
            rgb = hex_rgb(re.sub(r"\s*!important", "", v)) if v else None
            if rgb and contrast(rgb, bg) < 4.5:
                warnings.append(f"низкий контраст {tok} на фоне страницы: {contrast(rgb, bg):.1f}:1 (нужно ≥ 4.5)")
    else:
        warnings.append("фон body задан не одним #hex-цветом: контраст текста автоматически не проверен")
    if non_important:
        warnings.append(f"{non_important} свойств без !important: Tailwind CDN подключается позже и может их перебить — проверьте на странице")
    if layout_hits:
        hits = ", ".join(f"{k}×{v}" for k, v in sorted(layout_hits.items()))
        warnings.append(f"свойства раскладки ({hits}): тема должна менять оформление, а не вёрстку; проверьте глазами на всех экранах")
    # fonts
    generic = {"serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui", "ui-sans-serif", "ui-monospace", "inherit",
               "georgia", "arial", "helvetica", "verdana", "tahoma", "times new roman", "courier new", "segoe ui", "roboto", "-apple-system"}
    wanted = set()
    for head, body in all_decls:
        for m in re.finditer(r"(?:font-family|--chart-font|--[\w-]+(?:title|body|font)[\w-]*)\s*:\s*([^;]+);", body):
            for fam in m.group(1).replace("!important", "").split(","):
                fam = fam.strip().strip("'\"")
                if fam and not fam.startswith("var(") and fam.lower() not in generic:
                    wanted.add(fam)
    for fam in sorted(wanted):
        if fam.replace(" ", "+") not in fonts_html and fam not in fonts_html:
            warnings.append(f"шрифт «{fam}» не найден в ссылке Google Fonts в templates/index.html (если он не системный, браузер подставит запасной)")
    return errors, warnings


def main():
    css = open(CSS, encoding="utf-8").read()
    fonts_html = open(INDEX, encoding="utf-8").read() if os.path.exists(INDEX) else ""
    registry = registered_themes()
    if registry is None:
        print("В static/app.js не найден реестр const THEMES = [...]")
        return 1
    targets = sys.argv[1:] or registry
    failed = 0
    for tid in targets:
        errors, warnings = check(tid, css, fonts_html)
        if tid not in registry:
            errors.insert(0, f"тема не зарегистрирована в THEMES (static/app.js): добавьте {{ id: '{tid}', label: () => '...' }}")
        status = "ОШИБКИ" if errors else "ок"
        print(f"== {tid}: {status}")
        for e in errors:
            print(f"   ✗ {e}")
        for w in warnings:
            print(f"   ! {w}")
        failed += bool(errors)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
