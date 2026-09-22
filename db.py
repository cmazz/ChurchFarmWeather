"""SQLite schema and helpers for the weather database."""
from __future__ import annotations

import math
import sqlite3

from config import DB_PATH

__all__ = [
    "DB_PATH",
    "COLUMNS",
    "METRIC_COLUMNS",
    "connect",
    "connect_readonly",
    "upsert",
    "latest_ts",
    "clean_records",
]

METRIC_COLUMNS = ["temp_f", "humidity_pct", "wind_mph", "pressure_inhg", "rain_in"]
COLUMNS = ["ts", *METRIC_COLUMNS, "source"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    ts            TEXT PRIMARY KEY,   -- local time, 'YYYY-MM-DD HH:MM:SS'
    temp_f        REAL,               -- outdoor temperature, degrees Fahrenheit
    humidity_pct  REAL,               -- outdoor relative humidity, percent
    wind_mph      REAL,               -- wind speed, mph
    pressure_inhg REAL,               -- barometric pressure, inches of mercury
    rain_in       REAL,               -- rain during the interval ending at ts, inches
    source        TEXT                -- 'csv' (bulk history) or 'api' (recent)
);
CREATE INDEX IF NOT EXISTS idx_observations_ts ON observations(ts);
"""


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    """Open a read/write connection, creating the schema if needed."""
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def connect_readonly(path: str = DB_PATH) -> sqlite3.Connection:
    """Open a strictly read-only connection (used for user-driven queries)."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn


def clean_records(rows):
    """Replace NaN / missing values with None so SQLite stores real NULLs."""
    cleaned = []
    for row in rows:
        cleaned.append(
            {
                key: (
                    None
                    if value is None or (isinstance(value, float) and math.isnan(value))
                    else value
                )
                for key, value in row.items()
            }
        )
    return cleaned


def upsert(conn: sqlite3.Connection, rows) -> int:
    """Insert or update observation rows keyed by ``ts``. Returns rows written."""
    rows = list(rows)
    if not rows:
        return 0
    placeholders = ",".join("?" for _ in COLUMNS)
    updates = ",".join(f"{c}=excluded.{c}" for c in COLUMNS if c != "ts")
    sql = (
        f"INSERT INTO observations ({','.join(COLUMNS)}) VALUES ({placeholders}) "
        f"ON CONFLICT(ts) DO UPDATE SET {updates}"
    )
    conn.executemany(sql, [[r.get(c) for c in COLUMNS] for r in rows])
    conn.commit()
    return len(rows)


def latest_ts(conn: sqlite3.Connection) -> str | None:
    """Return the newest timestamp in the table, or None if empty."""
    row = conn.execute("SELECT MAX(ts) FROM observations").fetchone()
    return row[0] if row and row[0] else None
