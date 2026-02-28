from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

from syke.time import (
    day_part,
    format_for_human,
    format_for_llm,
    require_utc,
    resolve_user_tz,
    temporal_grounding_block,
    to_local,
)


def test_resolve_user_tz_returns_tzinfo(monkeypatch) -> None:
    monkeypatch.delenv("SYKE_TIMEZONE", raising=False)
    tz = resolve_user_tz()
    assert isinstance(tz, tzinfo)


def test_resolve_user_tz_with_env_var(monkeypatch) -> None:
    monkeypatch.setenv("SYKE_TIMEZONE", "America/New_York")
    tz = resolve_user_tz()
    assert getattr(tz, "key", None) == "America/New_York"


def test_resolve_user_tz_auto(monkeypatch) -> None:
    monkeypatch.setenv("SYKE_TIMEZONE", "auto")
    tz = resolve_user_tz()
    assert isinstance(tz, tzinfo)


def test_day_part_mapping() -> None:
    assert day_part(0) == "night"
    assert day_part(6) == "early morning"
    assert day_part(10) == "morning"
    assert day_part(13) == "afternoon"
    assert day_part(18) == "evening"
    assert day_part(23) == "night"


def test_to_local_from_iso_string() -> None:
    user_tz = ZoneInfo("America/New_York")
    local = to_local("2026-02-27T12:00:00+00:00", user_tz=user_tz)
    assert local.tzinfo == user_tz
    assert local.hour == 7


def test_to_local_from_datetime_object() -> None:
    user_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 2, 27, 12, 0, 0, tzinfo=timezone.utc)
    local = to_local(dt, user_tz=user_tz)
    assert local.tzinfo == user_tz
    assert local.hour == 7


def test_format_for_llm_has_local_utc_and_day_part() -> None:
    user_tz = ZoneInfo("America/Los_Angeles")
    out = format_for_llm("2026-02-27T06:15:00+00:00", user_tz=user_tz)
    assert "(" in out and "Z)" in out
    assert any(
        part in out
        for part in (
            "night",
            "early morning",
            "morning",
            "midday",
            "afternoon",
            "late afternoon",
            "evening",
        )
    )


def test_format_for_human_today() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    out = format_for_human(now, user_tz=timezone.utc)
    assert out == f"today {now.strftime('%H:%M:%S')}"


def test_format_for_human_yesterday() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    yesterday = now - timedelta(days=1)
    out = format_for_human(yesterday, user_tz=timezone.utc)
    assert out == f"yesterday {yesterday.strftime('%H:%M:%S')}"


def test_temporal_grounding_block_contains_expected_sections() -> None:
    block = temporal_grounding_block(user_tz=ZoneInfo("UTC"))
    assert "## Temporal Context" in block
    assert "Current time:" in block
    assert "Today is" in block and "Yesterday was" in block
    assert "All event timestamps below are in the user's local timezone." in block
    assert "UTC anchors are provided in parentheses for precision." in block


def test_require_utc_naive_datetime() -> None:
    dt = datetime(2026, 2, 27, 12, 0, 0)
    out = require_utc(dt)
    assert out.tzinfo == timezone.utc
    assert out.hour == 12


def test_require_utc_aware_datetime() -> None:
    dt = datetime(2026, 2, 27, 7, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    out = require_utc(dt)
    assert out.tzinfo == timezone.utc
    assert out.hour == 12


# --- Critical gap tests: DST, cross-timezone, validation, integration ---


def test_dst_spring_forward() -> None:
    """Spring forward: 2:30 AM doesn't exist in US/Eastern on Mar 8 2026.

    ZoneInfo should fold to 3:30 AM EDT. Verify the pipeline handles it.
    """
    tz = ZoneInfo("America/New_York")
    # Mar 8 2026 02:30 UTC — during spring-forward in Eastern
    utc_dt = datetime(2026, 3, 8, 7, 30, 0, tzinfo=timezone.utc)
    local = to_local(utc_dt, user_tz=tz)
    assert local.tzinfo == tz
    # Should be EDT (UTC-4) so 7:30 UTC = 3:30 AM EDT
    assert local.hour == 3
    assert local.minute == 30
    # format_for_llm should not crash
    out = format_for_llm(utc_dt, user_tz=tz)
    assert "3:30" in out or "03:30" in out


def test_dst_fall_back() -> None:
    """Fall back: 1:30 AM is ambiguous in US/Eastern on Nov 1 2026.

    We store UTC so there's no ambiguity — just verify conversion works.
    """
    tz = ZoneInfo("America/New_York")
    # Nov 1 2026 05:30 UTC — during fall-back in Eastern (still EDT)
    utc_before = datetime(2026, 11, 1, 5, 30, 0, tzinfo=timezone.utc)
    local_before = to_local(utc_before, user_tz=tz)
    assert local_before.hour == 1
    assert local_before.minute == 30

    # Nov 1 2026 06:30 UTC — after fall-back (now EST)
    utc_after = datetime(2026, 11, 1, 6, 30, 0, tzinfo=timezone.utc)
    local_after = to_local(utc_after, user_tz=tz)
    assert local_after.hour == 1
    assert local_after.minute == 30

    # Same local hour, different UTC — DST handled correctly
    assert utc_before != utc_after
    assert local_before.hour == local_after.hour
    # But the UTC anchors in format_for_llm should differ
    llm_before = format_for_llm(utc_before, user_tz=tz)
    llm_after = format_for_llm(utc_after, user_tz=tz)
    assert "05:30Z" in llm_before
    assert "06:30Z" in llm_after


def test_cross_timezone_events_all_normalize_to_utc() -> None:
    """Events from different timezones should all normalize to UTC correctly.

    Simulates: Tokyo event + London event + LA event, all representing the
    same instant. After require_utc(), they should all be identical.
    """
    instant_utc = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

    # Same instant in different timezones
    tokyo = instant_utc.astimezone(ZoneInfo("Asia/Tokyo"))       # 21:00 JST
    london = instant_utc.astimezone(ZoneInfo("Europe/London"))   # 13:00 BST
    la = instant_utc.astimezone(ZoneInfo("America/Los_Angeles")) # 05:00 PDT

    assert require_utc(tokyo) == instant_utc
    assert require_utc(london) == instant_utc
    assert require_utc(la) == instant_utc


def test_cross_timezone_format_for_llm_shows_correct_local() -> None:
    """format_for_llm should show the right local time for each timezone."""
    utc_dt = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

    tokyo_out = format_for_llm(utc_dt, user_tz=ZoneInfo("Asia/Tokyo"))
    la_out = format_for_llm(utc_dt, user_tz=ZoneInfo("America/Los_Angeles"))

    # Tokyo should show 9:00 PM, LA should show 5:00 AM
    assert "9:00 PM" in tokyo_out
    assert "5:00 AM" in la_out
    # Both should have the same UTC anchor
    assert "12:00Z" in tokyo_out
    assert "12:00Z" in la_out


def test_resolve_user_tz_invalid_falls_back(monkeypatch) -> None:
    """Invalid SYKE_TIMEZONE should fall back to auto-detect, not crash."""
    monkeypatch.setenv("SYKE_TIMEZONE", "Invalid/Timezone")
    tz = resolve_user_tz()
    assert isinstance(tz, tzinfo)
    # Should NOT be the invalid value — it fell back
    assert getattr(tz, "key", None) != "Invalid/Timezone"


def test_resolve_user_tz_empty_string(monkeypatch) -> None:
    """Empty SYKE_TIMEZONE should auto-detect."""
    monkeypatch.setenv("SYKE_TIMEZONE", "")
    tz = resolve_user_tz()
    assert isinstance(tz, tzinfo)


def test_to_local_from_iso_z_suffix() -> None:
    """ISO string with Z suffix (common from APIs) should parse correctly."""
    user_tz = ZoneInfo("America/New_York")
    local = to_local("2026-06-15T12:00:00Z", user_tz=user_tz)
    # June = EDT (UTC-4), so 12:00 UTC = 8:00 AM EDT
    assert local.hour == 8


def test_format_for_llm_day_part_accuracy() -> None:
    """Day-part labels should match the LOCAL hour, not UTC.

    6 AM UTC = 10 PM PST. Hour 22 maps to 'evening' (night starts at 23).
    The key assertion: the label should NOT be 'early morning' (what UTC hour 6 would give).
    """
    utc_dt = datetime(2026, 2, 27, 6, 0, 0, tzinfo=timezone.utc)
    out = format_for_llm(utc_dt, user_tz=ZoneInfo("America/Los_Angeles"))
    assert "evening" in out  # 10 PM local (hour 22) = evening
    assert "early morning" not in out  # NOT the UTC hour's label


def test_temporal_grounding_block_respects_user_timezone() -> None:
    """Temporal grounding block should show the user's local timezone, not UTC."""
    block_tokyo = temporal_grounding_block(user_tz=ZoneInfo("Asia/Tokyo"))
    block_la = temporal_grounding_block(user_tz=ZoneInfo("America/Los_Angeles"))

    assert "Asia/Tokyo" in block_tokyo
    assert "America/Los_Angeles" in block_la
    # They should show different current times
    assert block_tokyo != block_la
