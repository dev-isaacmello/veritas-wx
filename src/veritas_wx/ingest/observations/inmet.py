"""INMET automatic stations: apitempo hourly payloads -> canonical OBS_V1 rows.

Separation of concerns: ``rows_from_payload`` is pure (testable offline);
``fetch_station_range`` is the only function that touches the network.
Missing values NEVER become rows (absence == missing; zero is a measurement).
"""

import datetime as dt

import httpx
import polars as pl

from veritas_wx.contracts import OBS_V1
from veritas_wx.contracts.units import c_to_k

API_BASE = "https://apitempo.inmet.gov.br"
_HEADERS = {"User-Agent": "Mozilla/5.0 (veritas-wx research; +github)"}

# INMET field -> (canonical variable, converter to canonical units)
VAR_MAP: dict[str, tuple[str, callable]] = {
    "TEM_INS": ("t2m", c_to_k),  # instantaneous at top of hour, degC -> K
    "VEN_VEL": ("wind10m", float),  # m/s
    "CHUVA": ("precip_1h", float),  # mm accumulated in the hour
}


def _parse_valid_time(rec: dict) -> dt.datetime | None:
    """DT_MEDICAO 'YYYY-MM-DD' + HR_MEDICAO 'HHMM' (UTC) -> aware datetime."""
    try:
        day = dt.date.fromisoformat(str(rec["DT_MEDICAO"]))
        hhmm = int(str(rec["HR_MEDICAO"]))
        return dt.datetime(day.year, day.month, day.day, hhmm // 100, tzinfo=dt.UTC)
    except (KeyError, ValueError, TypeError):
        return None


def rows_from_payload(
    payload: list[dict],
    station_id: str,
    ingest_version: str,
) -> tuple[pl.DataFrame, dict[str, int]]:
    """Pure mapper: one API record (station-hour) -> up to len(VAR_MAP) OBS rows.

    Returns (OBS_V1 frame, dropped counts by reason) so the caller can feed the
    runlog reconciliation identity: potential = len(payload) * len(VAR_MAP).
    """
    rows: list[dict] = []
    dropped = {"bad_timestamp": 0, "value_missing": 0, "value_unparseable": 0}

    for rec in payload:
        valid_time = _parse_valid_time(rec)
        if valid_time is None:
            dropped["bad_timestamp"] += len(VAR_MAP)
            continue
        for field, (variable, convert) in VAR_MAP.items():
            raw = rec.get(field)
            if raw is None or raw == "":
                dropped["value_missing"] += 1
                continue
            try:
                value = convert(float(raw))
            except (ValueError, TypeError):
                dropped["value_unparseable"] += 1
                continue
            rows.append(
                {
                    "station_id": station_id,
                    "valid_time": valid_time,
                    "variable": variable,
                    "value": value,
                    "source": "inmet",
                    "source_qc_raw": None,  # apitempo exposes no per-value flag
                    "ingest_version": ingest_version,
                }
            )

    df = pl.DataFrame(rows, schema=OBS_V1) if rows else pl.DataFrame(schema=OBS_V1)
    return df, dropped


def fetch_station_range(
    client: httpx.Client,
    native_id: str,
    start: dt.date,
    end: dt.date,
    retries: int = 3,
) -> list[dict]:
    """GET /estacao/{start}/{end}/{id} with retry/backoff (the API is fragile)."""
    url = f"{API_BASE}/estacao/{start.isoformat()}/{end.isoformat()}/{native_id}"
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            resp = client.get(url, headers=_HEADERS, timeout=60.0)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                raise ValueError(f"unexpected INMET payload type: {type(data)}")
            return data
        except (httpx.HTTPError, ValueError) as exc:  # noqa: PERF203
            last_error = exc
            if attempt < retries - 1:
                import time

                time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"INMET fetch failed after {retries} attempts: {url}") from last_error
