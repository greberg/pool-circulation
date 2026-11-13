"""Price list extraction for Pool Circulation — pure Python, no HA imports.

Turns a price sensor's attributes (Nordpool, Tibber, Trulsibrunn, …) into a
list of 24 hourly prices for today. Quarter-hour data (96 entries, Nordpool
since Oct 2025) is averaged per hour. Missing hours are padded with infinity
so they are never picked as cheap hours.
"""
from __future__ import annotations

import logging
from datetime import date

_LOGGER = logging.getLogger(__name__)

INF = float("inf")


def _ts_to_hour(ts: str) -> int | None:
    """Extract the local hour (0-23) from an ISO-8601 timestamp string."""
    # "2026-05-11T14:00:00+02:00"  or  "2026-05-11 14:00:00"
    try:
        for sep in ("T", " "):
            if sep in ts:
                return int(ts.split(sep)[1][:2])
    except (ValueError, IndexError):
        pass
    return None


def _hourly_from_flat(values: list[float]) -> list[float]:
    """Collapse a flat list to 24 hourly values.

    24 entries → as-is. 92–100 entries (15-min resolution, DST days included)
    → mean of each group of 4. Anything shorter than 24 is padded with ∞.
    """
    if len(values) >= 92:
        hourly = []
        for h in range(24):
            chunk = [v for v in values[h * 4:(h + 1) * 4] if v < INF]
            hourly.append(sum(chunk) / len(chunk) if chunk else INF)
        return hourly
    return (values + [INF] * 24)[:24]


def parse_price_list(raw: list, source_attr: str, today_str: str | None = None) -> list[float] | None:
    """Try to extract 24 hourly floats from a raw attribute list.

    When *today_str* is supplied (ISO date) dict lists are filtered to entries
    whose timestamp starts with that date — handles multi-day attributes like
    ``prices`` (today + tomorrow). Dict entries with timestamps are bucketed by
    hour and averaged, so both hourly and 15-minute data work.
    """
    if not raw:
        return None

    first = raw[0]

    # ── plain floats / ints ──────────────────────────────────────────────────
    if isinstance(first, (int, float)):
        try:
            return _hourly_from_flat([float(v) for v in raw])
        except (TypeError, ValueError):
            return None

    # ── list of dicts ────────────────────────────────────────────────────────
    if isinstance(first, dict):
        ts_key = next(
            (k for k in ("start_time", "startsAt", "start", "starts_at", "time", "hour_start")
             if k in first),
            None,
        )
        for price_key in ("price", "total", "price_per_kwh", "energy", "value", "cost", "amount"):
            if price_key not in first:
                continue
            try:
                entries: list = list(raw)
                if today_str and ts_key:
                    entries = [e for e in entries if str(e.get(ts_key, "")).startswith(today_str)]
                if not entries:
                    continue
                if not ts_key:
                    return _hourly_from_flat([float(e[price_key]) for e in entries])
                buckets: dict[int, list[float]] = {}
                for e in entries:
                    hour = _ts_to_hour(str(e.get(ts_key, "")))
                    if hour is None:
                        continue
                    buckets.setdefault(hour, []).append(float(e[price_key]))
                if not buckets:
                    continue
                return [
                    sum(buckets[h]) / len(buckets[h]) if h in buckets else INF
                    for h in range(24)
                ]
            except (KeyError, TypeError, ValueError):
                continue

    return None


def extract_today_prices(attrs: dict, today: date) -> list[float] | None:
    """Extract a 24-float hourly price list for *today* from sensor attributes.

    Supports ``today`` (plain floats), ``raw_today``, ``prices`` / ``data``
    (multi-day dict lists, date-filtered) and several other naming variants.
    Returns None when nothing usable is found.
    """
    today_str = today.isoformat()

    # Ordered by reliability: plain list first, then dict variants
    candidates = [
        ("today",         False),
        ("raw_today",     False),
        ("prices",        True),
        ("data",          True),
        ("today_prices",  False),
        ("prices_today",  False),
        ("hourly_prices", False),
        ("current_day",   False),
        ("today_data",    False),
    ]

    for attr, needs_date_filter in candidates:
        raw = attrs.get(attr)
        if not raw or not isinstance(raw, (list, tuple)):
            continue
        result = parse_price_list(list(raw), attr, today_str if needs_date_filter else None)
        if result is not None and any(p < INF for p in result):
            return result
    return None


def describe_attrs(attrs: dict) -> str:
    """Short description of a sensor's list attributes — for a one-off warning."""
    lists = {
        k: f"len={len(v)}, sample={v[0]!r}"
        for k, v in attrs.items()
        if isinstance(v, (list, tuple)) and v
    }
    if lists:
        return f"list attributes: {lists}"
    return f"no list attributes (keys: {sorted(attrs)})"
