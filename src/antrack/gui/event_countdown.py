"""Helpers to format the next AOS/LOS event countdown for GUI widgets."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Tuple


def _parse_utc_timestamp(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _select_next_event(payload: dict, now_utc: datetime) -> Tuple[Optional[str], Optional[datetime]]:
    visible_now = payload.get("visible_now")
    aos_dt = _parse_utc_timestamp(payload.get("aos_utc"))
    los_dt = _parse_utc_timestamp(payload.get("los_utc"))

    if visible_now is True and los_dt is not None and los_dt > now_utc:
        return "LOS", los_dt
    if visible_now is False and aos_dt is not None and aos_dt > now_utc:
        return "AOS", aos_dt

    future_events = []
    if aos_dt is not None and aos_dt > now_utc:
        future_events.append(("AOS", aos_dt))
    if los_dt is not None and los_dt > now_utc:
        future_events.append(("LOS", los_dt))
    if not future_events:
        return None, None
    return min(future_events, key=lambda item: item[1])


def format_next_event_countdown(payload: dict, now_utc: Optional[datetime] = None) -> str:
    """Return a compact label like 'AOS 00:12:34' for the next pass event."""
    if not isinstance(payload, dict):
        return "-"

    now_utc = now_utc or datetime.now(timezone.utc)
    event_name, event_time = _select_next_event(payload, now_utc)
    if event_name is None or event_time is None:
        return "-"

    total_seconds = max(0, int(round((event_time - now_utc).total_seconds())))
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)

    if days > 0:
        return f"{event_name} +{days}j {hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{event_name} {hours:02d}:{minutes:02d}:{seconds:02d}"


def next_event_tooltip(payload: dict) -> str:
    """Return the full UTC timestamp of the next event for tooltip display."""
    if not isinstance(payload, dict):
        return "-"
    event_name, event_time = _select_next_event(payload, datetime.now(timezone.utc))
    if event_name is None or event_time is None:
        return "-"
    return f"{event_name}: {event_time.strftime('%Y-%m-%d %H:%M:%S')} UTC"
