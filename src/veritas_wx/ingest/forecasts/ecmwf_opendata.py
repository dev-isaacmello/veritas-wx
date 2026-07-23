"""ECMWF Open Data (IFS HRES + AIFS Single) on AWS: URLs + ranged fetches.

Layout (to be confirmed byte-for-byte by the T2 audit; base is config-driven):
    {yyyymmdd}/{HH}z/ifs/0p25/oper/{yyyymmddHH}0000-{L}h-oper-fc.grib2  (+ .index)
    {yyyymmdd}/{HH}z/aifs-single/0p25/oper/{...}-oper-fc.grib2         (+ .index)

Each step is its own small GRIB2 + JSON-lines .index with _offset/_length —
we transfer only 2t/10u/10v/tp ranges. License: CC-BY-4.0 (attribution in
published dataset metadata).
"""

import datetime as dt

import httpx

from veritas_wx.ingest.forecasts.gribidx import (
    ECMWF_WANTED,
    IdxEntry,
    coalesce,
    parse_ecmwf_index,
    select_ecmwf,
)

DEFAULT_BASE = "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com"

PRODUCTS = {
    "hres": "ifs/0p25/oper",
    "aifs": "aifs-single/0p25/oper",
}


def object_key(init: dt.datetime, lead_hours: int, model: str) -> str:
    product = PRODUCTS[model]
    d = init.strftime("%Y%m%d")
    hh = f"{init.hour:02d}"
    stamp = f"{d}{hh}0000"
    return f"{d}/{hh}z/{product}/{stamp}-{lead_hours}h-oper-fc.grib2"


def grib_url(init: dt.datetime, lead_hours: int, model: str, base: str = DEFAULT_BASE) -> str:
    return f"{base}/{object_key(init, lead_hours, model)}"


def index_url(init: dt.datetime, lead_hours: int, model: str, base: str = DEFAULT_BASE) -> str:
    return grib_url(init, lead_hours, model, base).removesuffix(".grib2") + ".index"


def fetch_fields(
    client: httpx.Client,
    init: dt.datetime,
    lead_hours: int,
    model: str,
    wanted: frozenset[str] = ECMWF_WANTED,
    base: str = DEFAULT_BASE,
) -> tuple[bytes, list[IdxEntry]]:
    """Fetch only wanted params of one step via ranged requests (see gfs.py)."""
    resp = client.get(index_url(init, lead_hours, model, base), timeout=60.0)
    resp.raise_for_status()
    selected = select_ecmwf(parse_ecmwf_index(resp.text), step=lead_hours, wanted=wanted)
    found = {e.var for e in selected}
    if found != set(wanted):
        raise ValueError(
            f"ECMWF index missing params for +{lead_hours}h {model} "
            f"{init:%Y-%m-%d %HZ}: {sorted(set(wanted) - found)}"
        )

    url = grib_url(init, lead_hours, model, base)
    chunks: list[bytes] = []
    for start, stop in coalesce(selected):
        header = f"bytes={start}-" if stop is None else f"bytes={start}-{stop - 1}"
        r = client.get(url, headers={"Range": header}, timeout=120.0)
        r.raise_for_status()
        chunks.append(r.content)
    return b"".join(chunks), selected
