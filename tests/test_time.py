from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

import pytest

from syke.time import (
    day_part,
    format_for_human,
    format_for_llm,
    require_utc,
    resolve_user_tz,
    temporal_grounding_block,
    to_local,
)


@pytest.mark.parametrize(
    "env_value,expected_key",
    [
        ("America/New_York", "America/New_York"),
        ("", None),
    ],
)
def test_resolve_user_tz_honors_env_or_auto(
    monkeypatch, env_value: str, expected_key: str | None
) -> None:
    monkeypatch.setenv("SYKE_TIMEZONE", env_value)
    tz = resolve_user_tz()
    assert isinstance(tz, tzinfo)
    if expected_key is not None:
        assert getattr(tz, "key", None) == expected_key


def test_resolve_user_tz_invalid_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("SYKE_TIMEZONE", "Invalid/Timezone")
    tz = resolve_user_tz()
    assert isinstance(tz, tzinfo)
    assert getattr(tz, "key", None) != "Invalid/Timezone"


@pytest.mark.parametrize(
    "hour,expected",
    [
        (0, "night"),
        (6, "early morning"),
        (10, "morning"),
        (18, "evening"),
    ],
)
def test_day_part_mapping(hour: int, expected: str) -> None:
    assert day_part(hour) == expected


@pytest.mark.parametrize(
    "value,expected_hour",
    [
        ("2026-06-15T12:00:00Z", 8),
    ],
)
def test_to_local_converts_from_strings_and_datetimes(
    value: str | datetime, expected_hour: int
) -> None:
    local = to_local(value, user_tz=ZoneInfo("America/New_York"))
    assert local.tzinfo == ZoneInfo("America/New_York")
    assert local.hour == expected_hour


@pytest.mark.parametrize(
    "value,expected_hour",
    [
        (datetime(2026, 2, 27, 12, 0, 0), 12),
    ],
)
def test_require_utc_handles_naive_and_aware(
    value: datetime, expected_hour: int
) -> None:
    out = require_utc(value)
    assert out.tzinfo == timezone.utc
    assert out.hour == expected_hour


def test_format_for_llm_has_local_and_utc_anchor() -> None:
    out = format_for_llm(
        "2026-02-27T06:15:00+00:00", user_tz=ZoneInfo("America/Los_Angeles")
    )
    assert "(" in out and "Z)" in out
    assert any(
        part in out
        for part in ("night", "early morning", "morning", "afternoon", "evening")
    )


def test_format_for_llm_day_part_accuracy() -> None:
    utc_dt = datetime(2026, 2, 27, 6, 0, 0, tzinfo=timezone.utc)
    out = format_for_llm(utc_dt, user_tz=ZoneInfo("America/Los_Angeles"))
    assert "evening" in out
    assert "early morning" not in out


def test_format_for_human_today_and_yesterday() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    out_today = format_for_human(now, user_tz=timezone.utc)
    out_yesterday = format_for_human(now - timedelta(days=1), user_tz=timezone.utc)
    assert out_today == f"today {now.strftime('%H:%M:%S')}"
    assert (
        out_yesterday == f"yesterday {(now - timedelta(days=1)).strftime('%H:%M:%S')}"
    )


def test_dst_spring_forward() -> None:
    tz = ZoneInfo("America/New_York")
    utc_dt = datetime(2026, 3, 8, 7, 30, 0, tzinfo=timezone.utc)
    local = to_local(utc_dt, user_tz=tz)
    assert local.tzinfo == tz
    assert local.hour == 3
    assert local.minute == 30
    assert "3:30" in format_for_llm(utc_dt, user_tz=tz) or "03:30" in format_for_llm(
        utc_dt, user_tz=tz
    )


def test_dst_fall_back() -> None:
    tz = ZoneInfo("America/New_York")
    utc_before = datetime(2026, 11, 1, 5, 30, 0, tzinfo=timezone.utc)
    utc_after = datetime(2026, 11, 1, 6, 30, 0, tzinfo=timezone.utc)
    local_before = to_local(utc_before, user_tz=tz)
    local_after = to_local(utc_after, user_tz=tz)

    assert local_before.hour == 1 and local_before.minute == 30
    assert local_after.hour == 1 and local_after.minute == 30
    assert utc_before != utc_after
    assert "05:30Z" in format_for_llm(utc_before, user_tz=tz)
    assert "06:30Z" in format_for_llm(utc_after, user_tz=tz)


def test_cross_timezone_normalization_and_presentation() -> None:
    instant_utc = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    tokyo = instant_utc.astimezone(ZoneInfo("Asia/Tokyo"))
    london = instant_utc.astimezone(ZoneInfo("Europe/London"))
    la = instant_utc.astimezone(ZoneInfo("America/Los_Angeles"))

    assert require_utc(tokyo) == instant_utc
    assert require_utc(london) == instant_utc
    assert require_utc(la) == instant_utc

    tokyo_out = format_for_llm(instant_utc, user_tz=ZoneInfo("Asia/Tokyo"))
    la_out = format_for_llm(instant_utc, user_tz=ZoneInfo("America/Los_Angeles"))
    assert "9:00 PM" in tokyo_out
    assert "5:00 AM" in la_out
    assert "12:00Z" in tokyo_out and "12:00Z" in la_out


def test_temporal_grounding_block_uses_user_timezone() -> None:
    block_tokyo = temporal_grounding_block(user_tz=ZoneInfo("Asia/Tokyo"))
    block_la = temporal_grounding_block(user_tz=ZoneInfo("America/Los_Angeles"))
    assert "## Temporal Context" in block_tokyo
    assert "All event timestamps below are in the user's local timezone." in block_tokyo
    assert "Asia/Tokyo" in block_tokyo
    assert "America/Los_Angeles" in block_la
    assert block_tokyo != block_la
