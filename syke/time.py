"""Temporal grounding — timezone-aware formatting for Syke.

Store UTC. Precompute local. The LLM is a narrator, not a clock.
"""

from __future__ import annotations

import os
from contextlib import suppress
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

SYKE_TIMEZONE_ENV = "SYKE_TIMEZONE"

# Day-part buckets for precomputed narration
_DAY_PARTS = [
    (5, "early morning"),
    (9, "morning"),
    (12, "midday"),
    (13, "afternoon"),
    (17, "late afternoon"),
    (18, "evening"),
    (23, "night"),
]


def _detect_system_tz() -> tzinfo:
    """Best-effort system timezone. Prefers IANA name over fixed offset."""
    with suppress(Exception):
        link = Path("/etc/localtime").resolve()
        parts = link.parts
        if "zoneinfo" in parts:
            idx = parts.index("zoneinfo")
            iana_key = "/".join(parts[idx + 1 :])
            return ZoneInfo(iana_key)
    return datetime.now().astimezone().tzinfo or timezone.utc


def resolve_user_tz() -> tzinfo:
    """Resolve user timezone. Precedence: SYKE_TIMEZONE env > auto-detect.

    Falls back to auto-detect if the env value is not a valid IANA timezone.
    """
    raw = os.getenv(SYKE_TIMEZONE_ENV, "auto").strip()
    if raw.lower() in ("", "auto", "local", "system"):
        return _detect_system_tz()
    try:
        return ZoneInfo(raw)
    except (KeyError, ValueError):
        import logging
        logging.getLogger(__name__).warning(
            "Invalid SYKE_TIMEZONE '%s', falling back to auto-detect", raw
        )
        return _detect_system_tz()


def require_utc(dt: datetime) -> datetime:
    """Normalize to aware UTC. Treats naive as UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def day_part(hour: int) -> str:
    """Map hour (0-23) to human day-part label."""
    for threshold, label in reversed(_DAY_PARTS):
        if hour >= threshold:
            return label
    return "night"


def to_local(dt_or_iso: datetime | str, user_tz: tzinfo | None = None) -> datetime:
    """Convert UTC datetime or ISO string to user's local timezone."""
    if isinstance(dt_or_iso, str):
        dt_or_iso = datetime.fromisoformat(dt_or_iso.replace("Z", "+00:00"))
    tz = user_tz or resolve_user_tz()
    return require_utc(dt_or_iso).astimezone(tz)


def format_for_llm(dt_or_iso: datetime | str, user_tz: tzinfo | None = None) -> str:
    """Format timestamp for LLM consumption: local primary + UTC anchor.

    Returns: 'Wed Feb 26, 10:15 PM PST (06:15Z) · evening'
    """
    local = to_local(dt_or_iso, user_tz)
    utc = require_utc(local)
    utc_short = utc.strftime("%H:%MZ")
    tz_abbrev = local.strftime("%Z") or "UTC"
    local_str = local.strftime("%a %b %d, %I:%M %p").replace(" 0", " ")
    part = day_part(local.hour)
    return f"{local_str} {tz_abbrev} ({utc_short}) · {part}"


def format_for_human(dt_or_iso: datetime | str, user_tz: tzinfo | None = None) -> str:
    """Format for CLI display: relative labels (today/yesterday) + time."""
    local = to_local(dt_or_iso, user_tz)
    now = datetime.now(timezone.utc).astimezone(local.tzinfo)
    time_str = local.strftime("%H:%M:%S")
    if local.date() == now.date():
        return f"today {time_str}"
    from datetime import timedelta

    if local.date() == (now - timedelta(days=1)).date():
        return f"yesterday {time_str}"
    return local.strftime("%b %d %H:%M:%S")


def temporal_grounding_block(user_tz: tzinfo | None = None) -> str:
    """Generate the temporal grounding block for injection into prompts."""
    tz = user_tz or resolve_user_tz()
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(tz)

    tz_name = getattr(tz, "key", None) or str(tz)
    time_str = now_local.strftime("%a %b %d, %Y %I:%M %p %Z").replace(" 0", " ")
    today = now_local.strftime("%A")
    from datetime import timedelta

    yesterday = (now_local - timedelta(days=1)).strftime("%A")

    return (
        "## Temporal Context\n"
        f"Current time: {time_str} ({tz_name})\n"
        f"Today is {today}. Yesterday was {yesterday}.\n"
        "All event timestamps below are in the user's local timezone.\n"
        "UTC anchors are provided in parentheses for precision."
    )
