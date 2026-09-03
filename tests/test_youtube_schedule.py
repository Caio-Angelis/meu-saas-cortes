from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.publishing.youtube_schedule import (
    build_daily_publish_times,
    parse_schedule_time,
    youtube_publish_at,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("7", "07:00"), ("7:00", "07:00"), ("07:30", "07:30"), ("7h15", "07:15")],
)
def test_parse_schedule_time(raw: str, expected: str):
    assert parse_schedule_time(raw).strftime("%H:%M") == expected


@pytest.mark.parametrize("raw", ["", "24:00", "12:60", "amanhã"])
def test_parse_schedule_time_rejects_invalid_value(raw: str):
    with pytest.raises(ValueError, match="Horário inválido"):
        parse_schedule_time(raw)


def test_build_daily_publish_times_starts_tomorrow_for_five_days():
    zone = ZoneInfo("America/Campo_Grande")
    now = datetime(2026, 8, 8, 22, 30, tzinfo=zone)

    dates = build_daily_publish_times("07:00", now=now)

    assert [value.strftime("%Y-%m-%d %H:%M") for value in dates] == [
        "2026-08-09 07:00",
        "2026-08-10 07:00",
        "2026-08-11 07:00",
        "2026-08-12 07:00",
        "2026-08-13 07:00",
    ]
    assert youtube_publish_at(dates[0]) == "2026-08-09T11:00:00Z"
