"""Append new observations from the WeatherLink historic API.

Fetches every archive record between the newest reading already on disk and now,
in <=24h chunks, and appends the new ones to ``data/live/readings.csv``. That
file is small and diff-friendly, so the hourly GitHub Action can commit it
cheaply. Safe to run repeatedly. Run locally with::

    python update_data.py
"""
from __future__ import annotations

import csv
import datetime as dt
from zoneinfo import ZoneInfo

import weatherlink
from build import LIVE_CSV, read_live, read_yearly, yearly_files
from config import STATION_TZ
from db import COLUMNS

_TZ = ZoneInfo(STATION_TZ)
LOOKBACK_DAYS_IF_EMPTY = 2


def _latest_local_ts() -> dt.datetime | None:
    timestamps = [row["ts"] for row in read_live()]
    if not timestamps:
        # First run: fall back to the newest yearly export.
        newest = yearly_files()[-1:] or []
        for path in newest:
            timestamps.extend(row["ts"] for row in read_yearly(path))
    if not timestamps:
        return None
    return dt.datetime.strptime(max(timestamps), "%Y-%m-%d %H:%M:%S").replace(tzinfo=_TZ)


def main() -> None:
    now = dt.datetime.now(tz=_TZ)
    start = _latest_local_ts() or (now - dt.timedelta(days=LOOKBACK_DAYS_IF_EMPTY))
    print(f"Fetching {start:%Y-%m-%d %H:%M} -> {now:%Y-%m-%d %H:%M} ({STATION_TZ})")

    existing = {row["ts"] for row in read_live()}
    new_rows: list[dict] = []
    chunk_start = start
    while chunk_start < now:
        chunk_end = min(chunk_start + dt.timedelta(hours=24), now)
        for record in weatherlink.get_historic(chunk_start, chunk_end):
            if record["ts"] not in existing:
                existing.add(record["ts"])
                new_rows.append(record)
        chunk_start = chunk_end

    if not new_rows:
        print("No new records.")
        return

    LIVE_CSV.parent.mkdir(parents=True, exist_ok=True)
    write_header = not LIVE_CSV.exists()
    with open(LIVE_CSV, "a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        if write_header:
            writer.writeheader()
        for record in sorted(new_rows, key=lambda r: r["ts"]):
            writer.writerow({k: record.get(k) for k in COLUMNS})

    print(f"Appended {len(new_rows)} rows. Newest: {max(r['ts'] for r in new_rows)}")


if __name__ == "__main__":
    main()
