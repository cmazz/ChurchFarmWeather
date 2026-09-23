"""Church Farm School weather dashboard (Streamlit)."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

import normals
import nws
import weatherlink
from build import build
from db import METRIC_COLUMNS, connect_readonly

st.set_page_config(page_title="Church Farm School Weather", page_icon="⛅", layout="wide")

METRIC_LABELS = {
    "temp_f": "Temperature (°F)",
    "humidity_pct": "Humidity (%)",
    "wind_mph": "Wind speed (mph)",
    "pressure_inhg": "Pressure (inHg)",
    "rain_in": "Rain (in)",
}
RESAMPLE_RULES = {"hourly": "h", "daily": "D", "weekly": "W", "monthly": "MS"}


@st.cache_resource
def database_path() -> str:
    return build()


@st.cache_data(ttl=300)
def load_history() -> pd.DataFrame:
    conn = connect_readonly(database_path())
    try:
        df = pd.read_sql_query(
            "SELECT * FROM observations ORDER BY ts", conn, parse_dates=["ts"]
        )
    finally:
        conn.close()
    return df


@st.cache_data(ttl=600)
def live_reading() -> tuple[dict | None, str | None]:
    try:
        return weatherlink.get_current(), None
    except Exception as exc:  # network / auth / schema issues
        return None, str(exc)


@st.cache_data(ttl=1800)
def forecast_periods() -> list[dict]:
    return nws.get_periods()


@st.cache_data(ttl=3600)
def historical_normal(day: dt.date) -> dict:
    return normals.historical_normal(database_path(), day)


def _fmt(value, suffix: str) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    return f"{value:g}{suffix}"


st.title("⛅ Church Farm School Weather")

history = load_history()
tab_now, tab_forecast, tab_explore, tab_ask = st.tabs(["Now", "Forecast", "Explore", "Ask"])

# --------------------------------------------------------------------------- #
# Now
# --------------------------------------------------------------------------- #
with tab_now:
    current, error = live_reading()
    if current is None:
        if history.empty:
            st.error("No live reading and no stored history yet.")
            st.stop()
        st.warning(f"Live reading unavailable ({error}). Showing the latest stored reading.")
        current = history.iloc[-1].to_dict()

    cols = st.columns(5)
    cols[0].metric("Temperature", _fmt(current.get("temp_f"), " °F"))
    cols[1].metric("Humidity", _fmt(current.get("humidity_pct"), " %"))
    cols[2].metric("Wind", _fmt(current.get("wind_mph"), " mph"))
    cols[3].metric("Pressure", _fmt(current.get("pressure_inhg"), " inHg"))
    rain_label = "Rain today" if current.get("rain_is_daily_total") else "Rain (interval)"
    cols[4].metric(rain_label, _fmt(current.get("rain_in"), " in"))
    st.caption(f"As of {current.get('ts', 'unknown')} · station local time")

    if not history.empty:
        st.subheader("Last 7 days")
        recent = history[history["ts"] >= history["ts"].max() - pd.Timedelta(days=7)]
        recent = recent.set_index("ts")
        st.line_chart(recent[["temp_f", "humidity_pct"]])
        st.line_chart(recent[["wind_mph", "pressure_inhg"]])
        st.bar_chart(recent[["rain_in"]])

# --------------------------------------------------------------------------- #
# Forecast
# --------------------------------------------------------------------------- #
with tab_forecast:
    st.write(
        "The National Weather Service's actual forecast, next to what's "
        "*typically* been recorded on this station around each date."
    )
    st.caption(
        "The historical side isn't a prediction - it's the average of what this "
        "station has recorded within 5 days of that date, across every year on record."
    )

    try:
        periods = forecast_periods()
    except Exception as exc:
        periods = []
        st.error(f"Couldn't reach the National Weather Service forecast: {exc}")

    if periods:
        by_date: dict[dt.date, dict] = {}
        for p in periods:
            day = dt.datetime.fromisoformat(p["startTime"]).date()
            by_date.setdefault(day, {})["day" if p["isDaytime"] else "night"] = p

        for day, entry in list(by_date.items())[:4]:
            day_p, night_p = entry.get("day"), entry.get("night")
            normal = historical_normal(day)

            with st.container(border=True):
                st.markdown(f"**{day.strftime('%A, %B')} {day.day}**")
                col_nws, col_normal = st.columns(2)

                with col_nws:
                    st.caption("NWS forecast")
                    if day_p:
                        pop = (day_p.get("probabilityOfPrecipitation") or {}).get("value") or 0
                        st.write(f"High **{day_p['temperature']}°F** · {day_p['shortForecast']}")
                        st.write(f"{pop}% chance of rain · wind {day_p.get('windSpeed', '—')}")
                    if night_p:
                        st.write(f"Low **{night_p['temperature']}°F** overnight")

                with col_normal:
                    n = normal["window_days"]
                    st.caption(f"Historical normal (±{n} days, {normal['day_count']} past days)")
                    if normal["avg_temp_f"] is not None:
                        st.write(
                            f"Avg **{normal['avg_temp_f']:g}°F** "
                            f"(range {normal['min_temp_f']:g}–{normal['max_temp_f']:g}°F)"
                        )
                        st.write(f"Rain on {normal['pct_days_with_rain']:g}% of these days historically")
                    else:
                        st.write("Not enough historical data for this date yet.")

                if day_p and normal["avg_temp_f"] is not None:
                    delta = day_p["temperature"] - normal["avg_temp_f"]
                    if abs(delta) < 1:
                        st.caption("Right around the historical average for this date.")
                    else:
                        direction = "warmer" if delta > 0 else "cooler"
                        st.caption(f"Forecast high is {abs(delta):.0f}°F {direction} than the historical average.")

# --------------------------------------------------------------------------- #
# Explore
# --------------------------------------------------------------------------- #
with tab_explore:
    if history.empty:
        st.info("No data yet. Run `python build.py` after adding CSVs to `data/`.")
    else:
        min_date = history["ts"].min().date()
        max_date = history["ts"].max().date()
        default_start = max(min_date, (history["ts"].max() - pd.Timedelta(days=365)).date())

        c1, c2, c3 = st.columns(3)
        start = c1.date_input("From", default_start, min_value=min_date, max_value=max_date)
        end = c2.date_input("To", max_date, min_value=min_date, max_value=max_date)
        grouping = c3.selectbox("Group by", ["raw", *RESAMPLE_RULES], index=2)
        metrics = st.multiselect(
            "Metrics",
            METRIC_COLUMNS,
            default=["temp_f"],
            format_func=lambda m: METRIC_LABELS[m],
        )

        window = history[
            (history["ts"].dt.date >= start) & (history["ts"].dt.date <= end)
        ].copy()

        if grouping != "raw" and not window.empty:
            agg = {m: ("sum" if m == "rain_in" else "mean") for m in METRIC_COLUMNS}
            window = (
                window.set_index("ts").resample(RESAMPLE_RULES[grouping]).agg(agg).reset_index()
            )

        if not metrics:
            st.info("Pick at least one metric.")
        elif window.empty:
            st.warning("No readings in that date range.")
        else:
            st.line_chart(window.set_index("ts")[metrics])
            stats = window[metrics].describe().rename(index=str)
            st.dataframe(stats, width="stretch")
            st.download_button(
                "Download selection (CSV)",
                window[["ts", *metrics]].to_csv(index=False),
                file_name=f"weather_{start}_{end}_{grouping}.csv",
                mime="text/csv",
            )

# --------------------------------------------------------------------------- #
# Ask
# --------------------------------------------------------------------------- #
with tab_ask:
    st.write("Ask about the weather history in plain English.")
    st.caption(
        "Examples: *What was the average temperature in January 2024?* · "
        "*Compare monthly rainfall this year with average humidity.* · "
        "*Which month has been the windiest since 2019?*"
    )
    question = st.text_input("Your question", label_visibility="collapsed",
                             placeholder="What was the average temperature in January 2024?")

    if st.button("Ask", type="primary") and question:
        import ai  # imported lazily so the other tabs work without a Gemini key

        with st.spinner("Thinking…"):
            try:
                result = ai.answer(question, database_path())
            except Exception as exc:
                st.error(f"Couldn't answer that: {exc}")
            else:
                st.markdown(result["narrative"])
                frame = result["df"]
                x, y = result.get("x"), [c for c in result.get("y", []) if c in frame.columns]
                if result["chart"] in ("line", "bar") and x in frame.columns and y:
                    plotted = frame.set_index(x)[y]
                    (st.line_chart if result["chart"] == "line" else st.bar_chart)(plotted)
                if not frame.empty:
                    st.dataframe(frame, width="stretch")
                with st.expander("Show the SQL that ran"):
                    st.code(result["sql"], language="sql")
