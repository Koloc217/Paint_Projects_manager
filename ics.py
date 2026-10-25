"""Deadlines as an iCalendar (.ics) file for a phone or desktop calendar.

Two ways to get it:
  • a file: /calendar in the bot, or the "Download .ics" button in the app (a snapshot, imported once);
  • a subscription: /cal/<token>.ics, which the calendar app re-reads by itself (off by default, Settings in the
    Calendar tab). The token is the only protection of that address, so it is long and can be replaced at any time;
    the address works only where the server is reachable (home network or VPN).

What goes in: the same as in the app's Calendar tab, minus closed tasks: deadlines of projects that are not
finished and deadlines of their open tasks. Projects in the trash are left out. Every event is an all-day event
with a stable UID, so a subscribed calendar updates moved dates instead of adding duplicates.
"""
import secrets
from datetime import datetime, timedelta, timezone

from common import live_projects, live_tasks, is_done, edit_config, config_value, STATUS_ON_HOLD
from i18n import tr, tv

PRODID = "-//Project Hub//Deadlines//EN"
UID_DOMAIN = "project-hub.local"
REFRESH = "PT1H"                 # how often a subscribed calendar is asked to re-read the feed (a hint; apps decide)


# ---------------------------------------------------------------- iCalendar text helpers (RFC 5545)
def _escape(value) -> str:
    text = "" if value is None else str(value)
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n"))


def _fold(line: str) -> str:
    """Lines longer than 75 bytes are split, continuation lines start with a space. UTF-8 characters are never cut."""
    out, chunk, size = [], "", 0
    limit = 75
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > limit:
            out.append(chunk)
            chunk, size, limit = " ", 1, 75
        chunk += ch
        size += n
    out.append(chunk)
    return "\r\n".join(out)


def _date(value) -> str:
    return value.strftime("%Y%m%d")


def _event(uid: str, day, summary: str, description: str, url: str, stamp: str) -> list:
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}@{UID_DOMAIN}",
        f"DTSTAMP:{stamp}",
        f"DTSTART;VALUE=DATE:{_date(day)}",
        f"DTEND;VALUE=DATE:{_date(day + timedelta(days=1))}",
        f"SUMMARY:{_escape(summary)}",
        "TRANSP:TRANSPARENT",                 # an all-day deadline does not make the day "busy"
    ]
    if description:
        lines.append(f"DESCRIPTION:{_escape(description)}")
    if url:
        lines.append(f"URL:{url}")
    lines.append("END:VEVENT")
    return lines


# ---------------------------------------------------------------- the calendar
def collect(db):
    """[(uid, date, summary, description, project_id)] for every deadline that belongs in the calendar."""
    projects = {p.id: p for p in live_projects(db).all() if not is_done(p.status)}
    events = []
    for p in projects.values():
        if not p.estimate:
            continue
        details = [f"{tr('ics.status')}: {tv(p.status)}"]
        if p.stage and p.stage != "—":
            details.append(f"{tr('ics.stage')}: {tv(p.stage)}")
        events.append((f"project-{p.id}", p.estimate.date(), tr("ics.project_summary", name=p.name),
                       "\n".join(details), p.id))
    for t in live_tasks(db).all():
        p = projects.get(t.project_id)
        if p is None or not t.deadline or is_done(t.status):
            continue
        details = [f"{tr('ics.project')}: {p.name}", f"{tr('ics.status')}: {tv(t.status)}"]
        if p.status == STATUS_ON_HOLD:
            details.append(tr("ics.project_on_hold"))
        events.append((f"task-{t.id}", t.deadline.date(), tr("ics.task_summary", task=t.task_name, project=p.name),
                       "\n".join(details), p.id))
    events.sort(key=lambda e: (e[1], e[0]))
    return events


def build(db, base_url: str = "") -> bytes:
    """The whole calendar as UTF-8 bytes. base_url (http://host:8000) adds a link to the project card to each event."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = base_url.rstrip("/")
    name = tr("ics.calendar_name")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(name)}",
        f"NAME:{_escape(name)}",
        f"REFRESH-INTERVAL;VALUE=DURATION:{REFRESH}",
        f"X-PUBLISHED-TTL:{REFRESH}",
    ]
    for uid, day, summary, description, project_id in collect(db):
        url = f"{base}/?card={project_id}" if base else ""
        lines += _event(uid, day, summary, description, url, stamp)
    lines.append("END:VCALENDAR")
    return ("\r\n".join(_fold(line) for line in lines) + "\r\n").encode("utf-8")


# ---------------------------------------------------------------- subscription address
def feed_settings() -> dict:
    feed = config_value("ICS_FEED") or {}
    return {"enabled": bool(feed.get("enabled")), "token": feed.get("token") or ""}


def feed_state() -> dict:
    """For the app: whether the subscription is on and its path (without the host, the browser adds its own)."""
    feed = feed_settings()
    on = feed["enabled"] and bool(feed["token"])
    return {"enabled": on, "path": f"/cal/{feed['token']}.ics" if on else None}


def set_feed(enabled: bool, new_token: bool = False) -> dict:
    with edit_config() as cfg:
        feed = dict(cfg.get("ICS_FEED") or {})
        feed["enabled"] = bool(enabled)
        if new_token or not feed.get("token"):
            feed["token"] = secrets.token_urlsafe(24)
        cfg["ICS_FEED"] = feed
    return feed_state()


def token_ok(token: str) -> bool:
    feed = feed_settings()
    if not (feed["enabled"] and feed["token"]):
        return False
    return secrets.compare_digest(str(token).encode("utf-8"), feed["token"].encode("utf-8"))
