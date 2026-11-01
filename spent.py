"""Time spent: optional, per task, plus an extra amount per project for work outside its tasks.

Stored in whole minutes. Input is forgiving, because it is typed on a phone or in the bot:
    90 · 1:30 · 1.5 · 1,5ч · 1.5h · 40м · 40 мин · 1ч 30м · 1h30m · 2 часа
A bare whole number is minutes; a number with a fraction is hours.

The project total is shown as an estimate ("about ~14 h"): not every task has a time, so the text says how many do.
Time never affects tokens.
"""
import re

from i18n import tr

MAX_SPENT_MIN = 60_000            # 1000 hours: more is a typo

_HOURS = ("ч", "час", "часа", "часов", "h", "hr", "hrs", "hour", "hours")
_MINUTES = ("м", "мин", "минут", "минуты", "минута", "m", "min", "mins", "minute", "minutes")
_PART = re.compile(r"(\d+(?:\.\d+)?)\s*([a-zа-яё]+)")


class SpentError(ValueError):
    pass


def parse_spent(text):
    """Minutes (int) from what the user typed; None for an empty value. Raises SpentError for anything else."""
    if text is None:
        return None
    if isinstance(text, bool):
        raise SpentError(text)
    if isinstance(text, (int, float)):
        minutes = int(round(text))
    else:
        s = str(text).strip().lower().replace(",", ".")
        if not s:
            return None
        if re.fullmatch(r"\d+", s):
            minutes = int(s)
        elif re.fullmatch(r"\d+\.\d+", s):
            minutes = round(float(s) * 60)
        elif m := re.fullmatch(r"(\d+):([0-5]\d)", s):
            minutes = int(m.group(1)) * 60 + int(m.group(2))
        else:
            minutes, pos = 0, 0
            compact = re.sub(r"\s+", " ", s)
            for m in _PART.finditer(compact):
                if compact[pos:m.start()].strip():
                    raise SpentError(text)
                number, unit = float(m.group(1)), m.group(2).rstrip(".")
                if unit in _HOURS:
                    minutes += number * 60
                elif unit in _MINUTES:
                    minutes += number
                else:
                    raise SpentError(text)
                pos = m.end()
            if pos == 0 or compact[pos:].strip():
                raise SpentError(text)
            minutes = int(round(minutes))
    if not 0 < minutes <= MAX_SPENT_MIN:
        raise SpentError(text)
    return minutes


def format_minutes(minutes) -> str:
    """Exact: "40 мин", "2 ч", "1 ч 30 мин"."""
    minutes = int(minutes or 0)
    h, m = divmod(minutes, 60)
    if h and m:
        return tr("time.h_m", h=h, m=m)
    if h:
        return tr("time.h", h=h)
    return tr("time.m", m=m)


def set_task_spent(db, task, minutes) -> None:
    """Sets (or clears, for None) the time spent on a task; the caller commits. The project stage is remembered
    the first time, so changing the number later does not move the time to another stage of the breakdown."""
    from database import Project
    task.spent_min = minutes
    if minutes is None:
        task.spent_stage = ""
    elif not task.spent_stage:
        proj = db.query(Project).filter(Project.id == task.project_id).first()
        task.spent_stage = (proj.stage if proj else "") or "none"
