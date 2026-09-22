# Church Farm School Weather

A Streamlit web app for the school's Davis WeatherLink station. It shows current
conditions, lets you explore ~6+ years of history, and answers plain-English
questions about that history using Google Gemini.

Tracked metrics: **outdoor temperature, humidity, wind speed, barometric
pressure, rainfall.**

## How it fits together

```
data/*.csv                yearly WeatherLink exports (bulk history, committed)
data/live/readings.csv     new readings appended hourly by the GitHub Action
        │
        ▼
build.py  ──► weather.db   rebuilt automatically whenever a CSV is newer
        │                  (git-ignored - it is a disposable build artifact)
        ▼
app.py                     Streamlit dashboard: Now / Explore / Ask
```

* **Now** – live reading pulled straight from the WeatherLink API on each visit,
  plus the last 7 days of charts.
* **Explore** – pick a date range, metrics, and grouping (raw / hourly / daily /
  weekly / monthly); chart, summary stats, CSV download.
* **Ask** – Gemini writes a **read-only** SQL query, it is validated
  (SELECT-only, single statement, forced row limit) and run, then Gemini
  explains the result.

| File | Purpose |
|---|---|
| `config.py` | secret loading (env vars → Streamlit secrets), model + timezone |
| `db.py` | SQLite schema, connections, upsert helpers |
| `build.py` | build `weather.db` from the CSVs (`python build.py --force`) |
| `weatherlink.py` | WeatherLink v2 API client (current + historic), rate-limited |
| `update_data.py` | append new readings to `data/live/readings.csv` |
| `ai.py` | natural-language question → SQL → answer |
| `app.py` | the Streamlit app |

## Secrets

Three keys, never committed:

| Key | Where to get it |
|---|---|
| `WEATHERLINK_API_KEY` | weatherlink.com → Account → API v2 |
| `WEATHERLINK_API_SECRET` | same page (**regenerate it** – see below) |
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey |

Optional: `WEATHERLINK_STATION_ID` (skips the station lookup). This station's id
is **74095**.

### Rotate the WeatherLink secret

The secret was previously stored in plain text in a synced file, so treat it as
exposed:

1. weatherlink.com → Account → API v2 → regenerate the secret.
2. Update `.streamlit/secrets.toml` locally, the Streamlit Cloud secrets, and the
   GitHub repo secret.

## Run locally

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # then fill in keys
python build.py --force        # build weather.db from data/
streamlit run app.py
```

`python update_data.py` pulls anything new from the WeatherLink historic API and
appends it to `data/live/readings.csv`.

## Deploy (Streamlit Community Cloud + GitHub Actions)

1. Push this folder to a GitHub repo.
2. **Streamlit Community Cloud** → New app → point at `app.py`. In *Advanced
   settings → Secrets*, paste the three keys in TOML form:
   ```toml
   WEATHERLINK_API_KEY = "..."
   WEATHERLINK_API_SECRET = "..."
   GEMINI_API_KEY = "..."
   ```
3. **GitHub → repo Settings → Secrets and variables → Actions** → add
   `WEATHERLINK_API_KEY`, `WEATHERLINK_API_SECRET` (and optionally
   `WEATHERLINK_STATION_ID`).
4. The `Update weather data` workflow then runs hourly: it fetches new readings,
   commits `data/live/readings.csv`, and the push makes Streamlit redeploy with
   fresh data. Trigger it once by hand from the Actions tab to confirm it works.

The database is rebuilt from the CSVs on each app start (~1–2 s), so it is never
committed and the repo stays small.

## Notes

* Timestamps are stored as station local time (`America/New_York`),
  `YYYY-MM-DD HH:MM:SS`.
* `rain_in` is the rainfall **during the interval ending at that timestamp** –
  use `SUM(rain_in)` for totals, never `AVG`.
* The station recorded nothing from **2026-07-27 to 2026-08-26** (it was
  offline); charts will show a gap there.
* Historic data before the CSV exports is 30-minute; new API data follows the
  station's archive interval (currently 30 minutes).
* `_archive/` holds the original prototype scripts, kept for reference and
  git-ignored.
