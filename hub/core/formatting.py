"""Text and date formatting for Telegram messages and the UI."""
import html

from hub.core.appconfig import config_value
from hub.core.constants import DATE_FORMATS, TG_SAFE_LIMIT


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


def fmt_date(d) -> str:
    """A date (date/datetime) in the format from the settings; for the bot and notifications. None -> ""."""
    if d is None:
        return ""
    return d.strftime(DATE_FORMATS.get(config_value("DATE_FORMAT"), DATE_FORMATS["dmy"]))
