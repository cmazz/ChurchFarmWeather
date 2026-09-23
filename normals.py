"""Historical 'normal' conditions for a calendar date, from this station's own history.

Not a forecast - a climatological average: what conditions have typically been
like around this date across every recorded year. Useful as context next to an
actual forecast (see nws.py).
"""
from __future__ import annotations

import datetime as dt

from db import connect_readonly


def historical_normal(db_path: str, target_date: dt.date, window_days: int = 5) -> dict:
    """Average conditions recorded within `window_days` of `target_date`'s
    month/day, across every year in the database.
    """
    candidates = sorted(
        {
            (target_date + dt.timedelta(days=offset)).strftime("%m-%d")
            for offset in range(-window_days, window_days + 1)
        }
    )
    placeholders = ",".join("?" for _ in candidates)

    conn = connect_readonly(db_path)
    try:
        avg_temp, min_temp, max_temp, avg_hum, avg_wind, avg_pressure, reading_count = conn.execute(
            f"""
            SELECT ROUND(AVG(temp_f), 1), ROUND(MIN(temp_f), 1), ROUND(MAX(temp_f), 1),
                   ROUND(AVG(humidity_pct), 0), ROUND(AVG(wind_mph), 1),
                   ROUND(AVG(pressure_inhg), 2), COUNT(*)
            FROM observations
            WHERE strftime('%m-%d', ts) IN ({placeholders})
            """,
            candidates,
        ).fetchone()

        avg_daily_rain, pct_days_with_rain, day_count = conn.execute(
            f"""
            SELECT ROUND(AVG(daily_total), 2),
                   ROUND(AVG(CASE WHEN daily_total > 0 THEN 100.0 ELSE 0 END), 0),
                   COUNT(*)
            FROM (
                SELECT date(ts) AS d, SUM(rain_in) AS daily_total
                FROM observations
                WHERE strftime('%m-%d', ts) IN ({placeholders})
                GROUP BY d
            )
            """,
            candidates,
        ).fetchone()
    finally:
        conn.close()

    return {
        "avg_temp_f": avg_temp,
        "min_temp_f": min_temp,
        "max_temp_f": max_temp,
        "avg_humidity_pct": avg_hum,
        "avg_wind_mph": avg_wind,
        "avg_pressure_inhg": avg_pressure,
        "avg_daily_rain_in": avg_daily_rain,
        "pct_days_with_rain": pct_days_with_rain,
        "reading_count": reading_count,
        "day_count": day_count,
        "window_days": window_days,
    }
