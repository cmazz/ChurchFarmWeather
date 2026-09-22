"""Minimal WeatherLink v2 API client.

Uses the current API-Secret header authentication (no request signing). Docs:
https://weatherlink.github.io/v2-api/
"""
from __future__ import annotations

import datetime as dt
import time
from zoneinfo import ZoneInfo

import requests

from config import STATION_TZ, get_secret, require

BASE_URL = "https://api.weatherlink.com/v2"
_TZ = ZoneInfo(STATION_TZ)
_station_id_cache: int | None = None

# The v2 API rate-limits bursts. Keep a minimum gap between calls and back off
# on 429/503 so a multi-day backfill doesn't get throttled.
_MIN_INTERVAL_S = 2.0
_MAX_RETRIES = 5
_last_call_at = 0.0

# Sensor/firmware combinations use different field names. For each metric we try
# these keys in order and take the first that is present and non-null.
_FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    "temp_f": ("temp_out", "temp_avg", "temp_last", "temp"),
    "humidity_pct": ("hum_out", "hum_last", "hum"),
    "wind_mph": (
        "wind_speed_avg",
        "wind_speed_last",
        "wind_speed_avg_last_10_min",
        "wind_speed",
    ),
    "pressure_inhg": ("bar_sea_level", "bar", "bar_absolute", "bar_last"),
}
# Rain is interval-based in archive records (sum these) but cumulative-for-today
# in the "current" record (display only).
_RAIN_INTERVAL_KEYS = ("rainfall_in", "rain_in")
_RAIN_TODAY_KEYS = ("rain_day_in", "rainfall_daily_in", "rainfall_day_in")


def _headers() -> dict[str, str]:
    return {"X-Api-Secret": require("WEATHERLINK_API_SECRET")}


def _params(extra: dict | None = None) -> dict:
    params = {"api-key": require("WEATHERLINK_API_KEY")}
    if extra:
        params.update(extra)
    return params


def _get(path: str, extra_params: dict | None = None, timeout: int = 60) -> dict:
    """GET {BASE_URL}{path} with pacing + retry/backoff. Returns parsed JSON."""
    global _last_call_at
    url = f"{BASE_URL}{path}"
    for attempt in range(1, _MAX_RETRIES + 1):
        wait = _MIN_INTERVAL_S - (time.monotonic() - _last_call_at)
        if wait > 0:
            time.sleep(wait)
        resp = requests.get(url, headers=_headers(), params=_params(extra_params), timeout=timeout)
        _last_call_at = time.monotonic()
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < _MAX_RETRIES:
            backoff = float(resp.headers.get("Retry-After", 2 ** attempt))
            time.sleep(min(backoff, 60))
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()  # exhausted retries
    return resp.json()


def get_station_id() -> int:
    """Return the station id (from config STATION_ID if set, else the first station)."""
    global _station_id_cache
    if _station_id_cache is not None:
        return _station_id_cache

    configured = get_secret("WEATHERLINK_STATION_ID")
    if configured:
        _station_id_cache = int(configured)
        return _station_id_cache

    stations = _get("/stations", timeout=30).get("stations", [])
    if not stations:
        raise RuntimeError("No stations found on this WeatherLink account.")
    _station_id_cache = int(stations[0]["station_id"])
    return _station_id_cache


def _first(record: dict, keys) -> float | None:
    return next((record[k] for k in keys if record.get(k) is not None), None)


def _pick_metrics(record: dict) -> dict:
    out: dict[str, float | None] = {
        metric: _first(record, keys) for metric, keys in _FIELD_CANDIDATES.items()
    }
    return out


def _iter_weather_records(payload: dict):
    """Yield sensor data records that contain at least one metric we care about."""
    wanted = [k for keys in _FIELD_CANDIDATES.values() for k in keys]
    wanted += _RAIN_INTERVAL_KEYS + _RAIN_TODAY_KEYS
    for sensor in payload.get("sensors", []):
        for record in sensor.get("data") or []:
            if any(record.get(k) is not None for k in wanted):
                yield record


def _to_local(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, tz=_TZ).strftime("%Y-%m-%d %H:%M:%S")


def get_current() -> dict:
    """Return the latest reading as an observation dict (keys match db.COLUMNS)."""
    payload = _get(f"/current/{get_station_id()}", timeout=30)

    record = None
    for record in _iter_weather_records(payload):  # noqa: B007 - last match wins
        pass
    if record is None:
        raise RuntimeError(f"No usable sensor data in /current response: {payload}")

    values = _pick_metrics(record)
    # "current" gives rain accumulated so far today, not an interval amount.
    values["rain_in"] = _first(record, _RAIN_TODAY_KEYS) or 0.0
    values["rain_is_daily_total"] = True
    values["ts"] = _to_local(record.get("ts", dt.datetime.now(tz=dt.timezone.utc).timestamp()))
    values["source"] = "api"
    return values


def get_historic(start: dt.datetime, end: dt.datetime) -> list[dict]:
    """Return archive observation dicts for the half-open interval ``(start, end]``.

    The span must be at most 24 hours (WeatherLink API limit).
    """
    payload = _get(
        f"/historic/{get_station_id()}",
        {
            "start-timestamp": int(start.timestamp()),
            "end-timestamp": int(end.timestamp()),
        },
    )

    rows: dict[str, dict] = {}
    for record in _iter_weather_records(payload):
        epoch = record.get("ts")
        if epoch is None:
            continue
        local_ts = _to_local(epoch)
        values = _pick_metrics(record)
        values["rain_in"] = _first(record, _RAIN_INTERVAL_KEYS)
        values["ts"] = local_ts
        values["source"] = "api"
        rows[local_ts] = values  # de-dupe within the response
    return list(rows.values())


if __name__ == "__main__":
    # Quick connectivity / field-name check:  python weatherlink.py
    import json

    sid = get_station_id()
    print(f"station_id = {sid}")
    print("current reading:")
    print(json.dumps(get_current(), indent=2, default=str))
