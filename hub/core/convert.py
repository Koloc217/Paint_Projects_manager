"""Plain conversions of values that come from the database, the config and the client."""
from datetime import date, datetime


def to_tokens(value, default=0) -> int:
    """A token amount as a whole number. Old data and configs may hold floats (15.0, 2.5): they are rounded;
    anything that is not a finite number (None, NaN, Infinity, text) becomes default."""
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return int(round(number))

def parse_iso_date(value):
    """'YYYY-MM-DD' -> datetime at midnight; empty -> None. Raises ValueError for anything else."""
    if value in (None, ""):
        return None
    return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d")


def to_date(val):
    """date / datetime / 'YYYY-MM-DD...' -> date; empty or unparsable -> None."""
    if not val:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def iso_date(value):
    """date/datetime -> 'YYYY-MM-DD' (None stays None)."""
    return value.isoformat()[:10] if value else None       # isoformat is several times faster than strftime
