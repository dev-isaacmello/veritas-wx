"""ISD-Lite fixed-format hourly files -> canonical OBS_V1 rows.

Format (NOAA isd-lite-format.txt): whitespace-delimited integers
    year month day hour air_temp dew_point slp wind_dir wind_speed sky precip_1h precip_6h
Scaled x10 where applicable; -9999 = missing; precip trace = -1.

Phase 1 maps: air_temp -> t2m (K), wind_speed -> wind10m (m/s),
precip_1h -> precip_1h (mm). Dewpoint is parsed-ready but NOT emitted
(Phase 1 scope is fixed at three variables).
"""

import datetime as dt

import polars as pl

from veritas_wx.contracts import OBS_V1
from veritas_wx.contracts.units import c_to_k, isd_lite_scaled

MISSING = -9999
TRACE = -1


def rows_from_isd_lite(
    text: str,
    station_id: str,
    ingest_version: str,
) -> tuple[pl.DataFrame, dict[str, int]]:
    """Pure parser: ISD-Lite file content -> OBS_V1 frame + dropped counts.

    Reconciliation: potential rows = parsed_lines * 3 variables;
    emitted + value_missing + trace-as-zero adjustments must account for all.
    Trace precipitation (-1) becomes 0.0 mm with source_qc_raw='T' — the
    information "below measurable" is preserved, not silently zeroed.
    """
    rows: list[dict] = []
    dropped = {"malformed_line": 0, "bad_timestamp": 0, "value_missing": 0}

    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) < 12:
            dropped["malformed_line"] += 3
            continue
        try:
            valid_time = dt.datetime(
                int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]), tzinfo=dt.UTC
            )
        except ValueError:
            dropped["bad_timestamp"] += 3
            continue

        air_temp, wind_speed, precip_1h = int(parts[4]), int(parts[8]), int(parts[10])

        def emit(variable: str, value: float, qc_raw: str | None = None) -> None:
            rows.append(
                {
                    "station_id": station_id,
                    "valid_time": valid_time,  # noqa: B023
                    "variable": variable,
                    "value": value,
                    "source": "isd",
                    "source_qc_raw": qc_raw,
                    "ingest_version": ingest_version,
                }
            )

        t = isd_lite_scaled(air_temp)
        if t is None:
            dropped["value_missing"] += 1
        else:
            emit("t2m", c_to_k(t))

        w = isd_lite_scaled(wind_speed)
        if w is None:
            dropped["value_missing"] += 1
        else:
            emit("wind10m", w)

        if precip_1h == MISSING:
            dropped["value_missing"] += 1
        elif precip_1h == TRACE:
            emit("precip_1h", 0.0, qc_raw="T")
        else:
            emit("precip_1h", precip_1h / 10.0)

    df = pl.DataFrame(rows, schema=OBS_V1) if rows else pl.DataFrame(schema=OBS_V1)
    return df, dropped
