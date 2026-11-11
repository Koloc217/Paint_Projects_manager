"""Checks of values that come from the client. Each returns (value, None) or (None, error text)."""
import math

from hub.core.constants import MAX_PROJECT_NAME_LEN, MAX_TOKENS
from hub.core.convert import parse_iso_date
from hub.core.i18n import tr

MAX_NAME_LEN = MAX_PROJECT_NAME_LEN


def validate_name(raw, max_len=MAX_NAME_LEN, empty_key="err.project_name_empty"):
    """Returns (clean_name, None) or (None, error_text)."""
    name = (raw or "").strip()
    if not name:
        return None, tr(empty_key)
    if len(name) > max_len:
        return None, tr("err.name_too_long", n=max_len)
    return name, None


def parse_token_field(value):
    """(whole_number, None) or (None, error_text) for a token amount from the client (a reward or a cost).
    Rejects fractions, negatives, NaN/Infinity and absurdly large values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None, tr("err.tokens_whole", n=MAX_TOKENS)
    if value != int(value) or not 0 <= value <= MAX_TOKENS:
        return None, tr("err.tokens_whole", n=MAX_TOKENS)
    return int(value), None


def parse_date_field(value):
    """(datetime_or_None, None) or (None, error_text) for a 'YYYY-MM-DD' field from the client."""
    try:
        return parse_iso_date(value), None
    except (TypeError, ValueError):
        return None, tr("err.bad_date")
