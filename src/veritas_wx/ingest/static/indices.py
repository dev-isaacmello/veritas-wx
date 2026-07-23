"""Climate regime indices: ONI (ENSO, CPC) and MJO RMM (BoM) parsers.

Pure text -> tidy polars frames consumed by analyze.strata joins. Missing
values are dropped AND counted — never silently interpolated.

ONI (https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt)::

     SEAS    YR    TOTAL   ANOM
     DJF  1950   24.72  -1.53

Each SEAS is a 3-month running mean; we stamp it on its CENTER month
(DJF 1950 = Dec49-Feb50, centered Jan 1950).

MJO RMM (http://www.bom.gov.au/climate/mjo/graphics/rmm.74toRealtime.txt)::

    year month day RMM1 RMM2 phase amplitude <origin note>

Missing sentinel: 1.E36 (any |value| > 1e35 treated as missing).
"""

import datetime as dt

import polars as pl

_ONI_CENTER_MONTH: dict[str, int] = {
    "DJF": 1, "JFM": 2, "FMA": 3, "MAM": 4, "AMJ": 5, "MJJ": 6,
    "JJA": 7, "JAS": 8, "ASO": 9, "SON": 10, "OND": 11, "NDJ": 12,
}
_MISSING_THRESHOLD = 1e35

_ONI_SCHEMA = {"year": pl.Int32, "month": pl.Int8, "oni": pl.Float64}
_RMM_SCHEMA = {
    "date": pl.Date, "rmm1": pl.Float64, "rmm2": pl.Float64,
    "phase": pl.Int8, "amplitude": pl.Float64,
}


def parse_oni(text: str) -> tuple[pl.DataFrame, dict[str, int]]:
    """CPC ONI table -> (year, month, oni). Returns (frame, dropped counts)."""
    rows: list[dict] = []
    dropped = {"malformed_line": 0}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 4 or parts[0] not in _ONI_CENTER_MONTH:
            continue
        try:
            rows.append(
                {
                    "year": int(parts[1]),
                    "month": _ONI_CENTER_MONTH[parts[0]],
                    "oni": float(parts[3]),
                }
            )
        except ValueError:
            dropped["malformed_line"] += 1
    df = pl.DataFrame(rows, schema=_ONI_SCHEMA) if rows else pl.DataFrame(schema=_ONI_SCHEMA)
    return df, dropped


def parse_rmm(text: str) -> tuple[pl.DataFrame, dict[str, int]]:
    """BoM RMM file -> daily (date, rmm1, rmm2, phase, amplitude)."""
    rows: list[dict] = []
    dropped = {"missing_value": 0, "malformed_line": 0, "bad_date": 0}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 7:
            continue
        try:
            year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        try:
            rmm1, rmm2 = float(parts[3]), float(parts[4])
            phase, amplitude = int(float(parts[5])), float(parts[6])
        except ValueError:
            dropped["malformed_line"] += 1
            continue
        if abs(rmm1) > _MISSING_THRESHOLD or abs(rmm2) > _MISSING_THRESHOLD:
            dropped["missing_value"] += 1
            continue
        try:
            date = dt.date(year, month, day)
        except ValueError:
            dropped["bad_date"] += 1
            continue
        rows.append(
            {"date": date, "rmm1": rmm1, "rmm2": rmm2, "phase": phase, "amplitude": amplitude}
        )
    df = pl.DataFrame(rows, schema=_RMM_SCHEMA) if rows else pl.DataFrame(schema=_RMM_SCHEMA)
    return df, dropped
