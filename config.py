"""Central configuration and secret loading.

Secrets are read from environment variables first (used by the GitHub Action and
the command-line scripts), then from Streamlit secrets (``.streamlit/secrets.toml``,
used when the dashboard runs). Never hard-code keys in source files.
"""
from __future__ import annotations

import os
import pathlib

# Local time zone of the weather station. Historic API timestamps arrive as UTC
# epoch seconds and are converted to this zone so they line up with the CSV
# history (which is already in local time).
STATION_TZ = "America/New_York"

# SQLite database file. It is a build artifact (rebuilt from the CSVs in data/)
# and is intentionally git-ignored.
DB_PATH = str(pathlib.Path(__file__).with_name("weather.db"))

# Station coordinates (Church Farm School, Exton PA), from the WeatherLink
# station record. Used to look up the National Weather Service forecast grid.
STATION_LAT = 40.03228
STATION_LON = -75.59433

# Gemini model(s) for the "Ask" tab. The first is used; the rest are fallbacks
# tried only if it fails or is overloaded. Both are pinned, non-"latest" models -
# "gemini-flash-latest" tracks whatever is newest, which in practice has been
# a slow "thinking" model prone to 503s under load; pinned models are faster
# and more predictable. Revisit this list occasionally as models are retired.
# Model list: https://ai.google.dev/gemini-api/docs/models
GEMINI_MODELS = ["gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.1-flash-lite"]


def get_secret(name: str, default: str | None = None) -> str | None:
    """Return a secret from the environment, then Streamlit secrets, then default."""
    value = os.environ.get(name)
    if value:
        return value
    try:
        import streamlit as st  # noqa: PLC0415 - optional at runtime

        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return default


def require(name: str) -> str:
    """Return a required secret or raise a clear error explaining how to set it."""
    value = get_secret(name)
    if not value:
        raise RuntimeError(
            f"Missing secret {name!r}. Set it as an environment variable, or add it "
            f"to .streamlit/secrets.toml (see .streamlit/secrets.toml.example)."
        )
    return value
