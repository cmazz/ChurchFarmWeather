"""National Weather Service forecast client.

Uses api.weather.gov - free, public, no API key required. Docs:
https://www.weather.gov/documentation/services-web-api
"""
from __future__ import annotations

import functools

import requests

from config import STATION_LAT, STATION_LON

_BASE = "https://api.weather.gov"
# NWS asks every client to identify itself; no registration needed.
_HEADERS = {"User-Agent": "ChurchFarmSchoolWeather/1.0 (github.com/cmazz/ChurchFarmWeather)"}


@functools.lru_cache(maxsize=1)
def _forecast_url() -> str:
    """Resolve the station's lat/lon to its NWS forecast-grid endpoint (cached)."""
    resp = requests.get(
        f"{_BASE}/points/{STATION_LAT},{STATION_LON}", headers=_HEADERS, timeout=20
    )
    resp.raise_for_status()
    return resp.json()["properties"]["forecast"]


def get_periods() -> list[dict]:
    """Return the raw NWS forecast periods (alternating day/night, ~7 days out).

    Each period has at least: name, startTime, isDaytime, temperature,
    temperatureUnit, shortForecast, windSpeed, windDirection, and
    probabilityOfPrecipitation ({"value": int | None}).
    """
    resp = requests.get(_forecast_url(), headers=_HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.json()["properties"]["periods"]
