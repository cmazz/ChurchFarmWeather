"""Answer plain-English questions about the weather history.

Gemini turns the question into one read-only SQL query, which is validated and
run against a read-only connection, then Gemini explains the result.

Uses the Gemini REST API directly (via ``requests``) - fewer moving parts than
the SDK and identical behaviour on Streamlit Cloud.
"""
from __future__ import annotations

import json
import re
import time

import pandas as pd
import requests

from config import GEMINI_MODELS, require
from db import connect_readonly

_API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"

SCHEMA_DOC = """
Table: observations   (one row per weather reading)
  ts             TEXT   local time 'YYYY-MM-DD HH:MM:SS' (America/New_York).
                        Filter dates as strings, e.g.
                        ts >= '2024-01-01' AND ts < '2024-02-01'.
  temp_f         REAL   outdoor temperature, degrees Fahrenheit
  humidity_pct   REAL   outdoor relative humidity, percent
  wind_mph       REAL   wind speed, mph
  pressure_inhg  REAL   barometric pressure, inches of mercury
  rain_in        REAL   rain that fell during the interval ending at ts, inches.
                        Use SUM(rain_in) for rainfall totals, never AVG(rain_in).
  source         TEXT   'csv' (history, ~30-minute readings) or 'api' (recent)

Notes:
- Data starts 2019-09-01. Some early rows have NULL temp/humidity/wind.
- Use strftime('%Y', ts) / strftime('%Y-%m', ts) for year / month grouping.
- "this year" means strftime('%Y', ts) = strftime('%Y', 'now').
- The station recorded nothing from 2026-07-27 to 2026-08-26.
"""

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum|reindex)\b",
    re.IGNORECASE,
)
_AGGREGATE = re.compile(r"\b(count|sum|avg|min|max|group\s+by)\b", re.IGNORECASE)


def _payload(prompt: str, thinking_off: bool) -> dict:
    config: dict = {"temperature": 0}
    if thinking_off:
        # Text-to-SQL doesn't need "thinking"; leaving it on can push a single
        # call past two minutes. Not every model accepts this (some 400).
        config["thinkingConfig"] = {"thinkingBudget": 0}
    return {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": config}


def _ask_gemini(prompt: str) -> str:
    """POST a prompt to Gemini, retrying 5xx and falling back to the next model."""
    api_key = require("GEMINI_API_KEY")
    last_error = "no models configured"

    for index, model in enumerate(GEMINI_MODELS):
        is_last_model = index == len(GEMINI_MODELS) - 1
        url = f"{_API_ROOT}/{model}:generateContent"
        thinking_off = True

        # 2 tries per model, then move on - with 3 models this still covers
        # transient blips without piling up a long wait on one overloaded model.
        for attempt in range(2):
            try:
                resp = requests.post(
                    url,
                    params={"key": api_key},
                    json=_payload(prompt, thinking_off),
                    timeout=(10, 90),
                )
            except requests.RequestException as exc:
                last_error = f"network error: {exc}"
                time.sleep(2**attempt)
                continue

            if resp.status_code == 200:
                try:
                    parts = resp.json()["candidates"][0]["content"]["parts"]
                    text = "".join(p.get("text", "") for p in parts).strip()
                except (KeyError, IndexError, ValueError) as exc:
                    raise RuntimeError(
                        f"Unexpected Gemini response: {resp.text[:300]}"
                    ) from exc
                if text:
                    return text
                last_error = "empty response"
                break  # next model

            if resp.status_code in (429, 500, 502, 503, 504):  # transient / rate limit
                last_error = f"{resp.status_code} from {model}"
                retry_after = resp.headers.get("Retry-After")
                time.sleep(float(retry_after) if retry_after else 2 ** (attempt + 1))
                continue

            if resp.status_code == 400 and thinking_off:
                # This model rejects the thinking-off config; retry without it.
                thinking_off = False
                continue

            if is_last_model:
                detail = resp.text[:300]
                if resp.status_code == 429:
                    detail = (
                        "the Gemini API quota for this key is used up. Free-tier keys "
                        "have low daily limits - enable billing on the Google Cloud "
                        "project, or try again later."
                    )
                raise RuntimeError(f"Gemini API error {resp.status_code}: {detail}")
            last_error = f"{resp.status_code} from {model}: {resp.text[:120]}"
            break  # next model

    if "503" in last_error or "429" in last_error:
        raise RuntimeError(
            "Gemini is experiencing high demand across every model this app tries. "
            "This is on Google's end, not this app - please try again in a minute."
        )
    raise RuntimeError(f"Gemini is unavailable right now ({last_error}).")


def _extract_json(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE | re.DOTALL)
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    try:
        return json.loads(match.group(0) if match else cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Gemini didn't return a query I could use for that question. "
            "Try rephrasing it as something about the recorded data."
        ) from exc


def _plan_query(question: str) -> dict:
    prompt = f"""You convert weather questions into ONE SQLite query over data this
station has ALREADY RECORDED. You can only answer questions a SQL query over
that historical data can answer - not future forecasts, not live conditions
elsewhere, not anything not derivable from the table below.

If the question fits, return STRICT JSON:
{{"sql": "<a single SELECT or WITH...SELECT statement, no semicolon>",
  "chart": "line" | "bar" | "none",
  "x": "<column for the x-axis or null>",
  "y": ["<numeric column>", "..."],
  "note": "<one sentence describing what the rows contain>"}}

If it does NOT fit (e.g. it asks for a weather forecast/prediction, or
anything not answerable from stored history), return STRICT JSON instead:
{{"error": "<one short, friendly sentence explaining this tool only answers
questions about recorded history and can't do that>"}}

Rules:
- SELECT only. No writes, no PRAGMA, no semicolons.
- Alias aggregates with clear names (e.g. AVG(temp_f) AS avg_temp_f).
- For a time series, ORDER BY the time/period column ascending.
- Keep results modest; add LIMIT 1000 when not aggregating.

{SCHEMA_DOC}

Question: {question}
"""
    plan = _extract_json(_ask_gemini(prompt))
    if plan.get("error"):
        raise ValueError(plan["error"])
    if not plan.get("sql"):
        raise ValueError(
            "I can only answer questions about recorded weather history - "
            "try asking about a specific date, month, or trend instead."
        )
    return plan


def _validate_sql(sql: str) -> str:
    statement = sql.strip().rstrip(";").strip()
    lowered = statement.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise ValueError("Only SELECT queries are allowed.")
    if ";" in statement:
        raise ValueError("Only a single statement is allowed.")
    if _FORBIDDEN.search(statement):
        raise ValueError("Query contains a keyword that is not allowed.")
    if " limit " not in f" {lowered} " and not _AGGREGATE.search(lowered):
        statement += " LIMIT 1000"
    return statement


def answer(question: str, db_path: str) -> dict:
    """Return dict with keys: sql, df, narrative, chart, x, y, note."""
    plan = _plan_query(question)
    sql = _validate_sql(str(plan.get("sql", "")))

    conn = connect_readonly(db_path)
    try:
        df = pd.read_sql_query(sql, conn)
    finally:
        conn.close()

    preview = df.head(50).to_string(index=False) if not df.empty else "(no rows)"
    summary_prompt = f"""Question: {question}

SQL that ran:
{sql}

Result ({len(df)} rows):
{preview}

Write a 2-4 sentence answer for a general audience. Include units
(degrees F, %, mph, inHg, inches). If there are no rows, say the data
doesn't cover that. Do not invent numbers that aren't in the result.
"""
    narrative = _ask_gemini(summary_prompt)

    return {
        "sql": sql,
        "df": df,
        "narrative": narrative,
        "chart": plan.get("chart", "none"),
        "x": plan.get("x"),
        "y": plan.get("y") or [],
        "note": plan.get("note", ""),
    }
