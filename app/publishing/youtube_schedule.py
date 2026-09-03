"""Cálculo do calendário de publicações diárias do YouTube."""

from __future__ import annotations

import re
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

DEFAULT_SCHEDULE_TIMEZONE = "America/Campo_Grande"

_TIME_RE = re.compile(r"^\s*(\d{1,2})(?:\s*[:h]\s*(\d{1,2}))?\s*$", re.IGNORECASE)


def parse_schedule_time(value: str) -> time:
    """Aceita horários como ``7``, ``7:00``, ``07:30`` ou ``7h30``."""
    match = _TIME_RE.fullmatch(value or "")
    if not match:
        raise ValueError("Horário inválido. Use HH:MM, por exemplo 07:00.")
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Horário inválido. Use uma hora entre 00:00 e 23:59.")
    return time(hour=hour, minute=minute)


def build_daily_publish_times(
    time_text: str,
    *,
    count: int = 5,
    timezone_name: str = DEFAULT_SCHEDULE_TIMEZONE,
    now: datetime | None = None,
) -> tuple[datetime, ...]:
    """Gera ``count`` datas, uma por dia, começando amanhã no fuso escolhido."""
    if count <= 0:
        raise ValueError("A quantidade de publicações deve ser maior que zero.")
    zone = ZoneInfo(timezone_name)
    current = now.astimezone(zone) if now is not None else datetime.now(zone)
    publish_time = parse_schedule_time(time_text)
    first_day = current.date() + timedelta(days=1)
    return tuple(
        datetime.combine(first_day + timedelta(days=index), publish_time, tzinfo=zone)
        for index in range(count)
    )


def youtube_publish_at(value: datetime) -> str:
    """Converte uma data consciente de fuso para RFC 3339 UTC aceito pelo YouTube."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A data de publicação precisa ter fuso horário.")
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
