from syke import health


def test_hours_ago_handles_naive_iso():
    hours = health._hours_ago("2026-03-27 10:00:00")
    assert isinstance(hours, float)
    assert hours > 0


def test_hours_ago_positive():
    hours = health._hours_ago("2026-03-27 10:00:00")
    assert hours > 0
