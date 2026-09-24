"""Trading-session (time-of-day) window helpers.

China Standard Time is a fixed UTC+8 offset with no DST, so we use a plain
`timedelta` instead of the IANA tz database — no extra `tzdata` dependency.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def parse_hhmm(s: str) -> int:
    """'09:00' -> 540 (minutes since midnight)."""
    h, m = s.strip().split(":")
    return int(h) * 60 + int(m)


def is_in_session(
    now: datetime,
    start: str = "09:00",
    end: str = "01:00",
    utc_offset_hours: int = 8,
) -> bool:
    """True if `now` (an aware datetime) is inside [start, end) in local time.

    Supports windows that cross midnight (e.g. 09:00 -> next-day 01:00).
    """
    local = now.astimezone(timezone(timedelta(hours=utc_offset_hours)))
    now_min = local.hour * 60 + local.minute
    start_min = parse_hhmm(start)
    end_min = parse_hhmm(end)

    if start_min <= end_min:
        return start_min <= now_min < end_min
    # Crosses midnight: [start, 24:00) plus [00:00, end).
    return now_min >= start_min or now_min < end_min


def session_label(start: str, end: str, utc_offset_hours: int) -> str:
    return f"{start} ~ {end} (UTC+{utc_offset_hours})"


def trading_day_key(now: datetime | None = None, day_start: str = "09:00") -> str:
    """Return the 'trading day' key: the Beijing date, but a day starts at
    `day_start` (09:00) rather than midnight. Used for daily PnL boundaries."""
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(timezone(timedelta(hours=8)))
    if local.hour < parse_hhmm(day_start) // 60:
        local = local - timedelta(days=1)
    return local.date().isoformat()
