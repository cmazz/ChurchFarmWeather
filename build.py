"""Build ``weather.db`` from the CSV files in ``data/``.

Sources:
  * ``data/*.csv``            - yearly WeatherLink exports (bulk history)
  * ``data/live/readings.csv`` - rows appended hourly by ``update_data.py``

The database is a disposable build artifact. The app imports :func:`build` and
rebuilds automatically whenever a source CSV is newer than the database. Run this
module directly to force a rebuild::

    python build.py --force
"""
from __future__ import annotations

import csv
import datetime as dt
import glob
import os
import pathlib
import sys

from db import DB_PATH, METRIC_COLUMNS, clean_records, connect, upsert

DATA_DIR = pathlib.Path(__file__).with_name("data")
LIVE_CSV = DATA_DIR / "live" / "readings.csv"
CSV_ENCODING = "cp1252"

_TS_FORMATS = (
    "%m/%d/%y %I:%M %p",   # WeatherLink export: "9/1/19 12:00 AM"
    "%m/%d/%Y %I:%M %p",
    "%m/%d/%y %H:%M",
    "%Y-%m-%d %H:%M:%S",   # already normalized (live CSV)
)


# --------------------------------------------------------------------------- #
# parsing helpers
# --------------------------------------------------------------------------- #
def yearly_files() -> list[str]:
    return sorted(glob.glob(str(DATA_DIR / "*.csv")))


def parse_ts(value: str | None) -> str | None:
    value = (value or "").strip().strip('"')
    for fmt in _TS_FORMATS:
        try:
            return dt.datetime.strptime(value, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


def _num(value: str | None) -> float | None:
    value = (value or "").strip().strip('"')
    if value in ("", "--", "---", "N/A"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _header_row(path: str) -> int:
    with open(path, encoding=CSV_ENCODING, errors="ignore") as fh:
        for i, line in enumerate(fh):
            if line.lower().lstrip().startswith('"date'):
                return i
    raise RuntimeError(f"{path}: could not find the data header row")


def _map_columns(header: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for col in header:
        name = col.strip().strip('"').lower()
        if name == "date & time":
            mapping[col] = "ts"
        elif name.startswith("barometer"):
            mapping[col] = "pressure_inhg"
        elif name.startswith("temp -"):          # excludes "Inside Temp", "High Temp", "Low Temp"
            mapping[col] = "temp_f"
        elif name == "hum - %":                   # excludes "Inside Hum"
            mapping[col] = "humidity_pct"
        elif name.startswith("wind speed"):      # excludes "High Wind Speed"
            mapping[col] = "wind_mph"
        elif name == "rain - in":                 # excludes "Rain Rate - in/h"
            mapping[col] = "rain_in"
    return mapping


def read_yearly(path: str):
    """Yield observation dicts from one yearly WeatherLink export."""
    skip = _header_row(path)
    with open(path, encoding=CSV_ENCODING, errors="ignore", newline="") as fh:
        for _ in range(skip):
            next(fh)
        reader = csv.reader(fh)
        header = next(reader)
        mapping = _map_columns(header)
        idx = {mapping[h]: i for i, h in enumerate(header) if h in mapping}
        missing = {"ts", *METRIC_COLUMNS} - set(idx)
        if missing:
            raise RuntimeError(f"{path}: could not map column(s) {sorted(missing)}")
        for row in reader:
            if not row or len(row) <= idx["ts"]:
                continue
            ts = parse_ts(row[idx["ts"]])
            if not ts:
                continue
            record = {"ts": ts, "source": "csv"}
            for metric in METRIC_COLUMNS:
                record[metric] = _num(row[idx[metric]])
            yield record


def read_live():
    """Yield observation dicts from data/live/readings.csv (may not exist yet)."""
    if not LIVE_CSV.exists():
        return
    with open(LIVE_CSV, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            ts = parse_ts(row.get("ts"))
            if not ts:
                continue
            record = {"ts": ts, "source": row.get("source") or "api"}
            for metric in METRIC_COLUMNS:
                record[metric] = _num(row.get(metric))
            yield record


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #
def _needs_rebuild() -> bool:
    if not os.path.exists(DB_PATH):
        return True
    db_mtime = os.path.getmtime(DB_PATH)
    sources = yearly_files() + ([str(LIVE_CSV)] if LIVE_CSV.exists() else [])
    return any(os.path.getmtime(src) > db_mtime for src in sources)


def build(force: bool = False) -> str:
    """Rebuild weather.db if stale (or ``force``) and return its path."""
    if not force and not _needs_rebuild():
        return DB_PATH

    files = yearly_files()
    if not files and not LIVE_CSV.exists():
        raise SystemExit("No data found in data/. Add the yearly CSV exports first.")

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = connect()
    try:
        for path in files:
            rows = list(read_yearly(path))
            upsert(conn, clean_records(rows))
            print(f"{os.path.basename(path)}: {len(rows)} rows")
        live_rows = list(read_live())
        if live_rows:
            upsert(conn, clean_records(live_rows))
            print(f"live/readings.csv: {len(live_rows)} rows")
        lo, hi, count = conn.execute(
            "SELECT MIN(ts), MAX(ts), COUNT(*) FROM observations"
        ).fetchone()
    finally:
        conn.close()
    print(f"weather.db: {count} rows, {lo} -> {hi}")
    return DB_PATH


if __name__ == "__main__":
    build(force="--force" in sys.argv or "-f" in sys.argv)
