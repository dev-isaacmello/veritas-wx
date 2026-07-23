"""GFS on AWS Open Data: URL construction + byte-range field fetches.

Layout (audited against s3://noaa-gfs-bdp-pds):
    gfs.{yyyymmdd}/{HH}/atmos/gfs.t{HH}z.pgrb2.0p25.f{FFF}      (+ .idx)

Only the Phase 1 fields are transferred (GFS_WANTED via .idx byte ranges) —
never the whole ~500 MB global file.
"""

import datetime as dt

import httpx

from veritas_wx.ingest.forecasts.gribidx import (
    GFS_WANTED,
    IdxEntry,
    coalesce,
    parse_gfs_idx,
    select_gfs,
)

DEFAULT_BASE = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"


def object_key(init: dt.datetime, lead_hours: int) -> str:
    d = init.strftime("%Y%m%d")
    hh = f"{init.hour:02d}"
    return f"gfs.{d}/{hh}/atmos/gfs.t{hh}z.pgrb2.0p25.f{lead_hours:03d}"


def grib_url(init: dt.datetime, lead_hours: int, base: str = DEFAULT_BASE) -> str:
    return f"{base}/{object_key(init, lead_hours)}"


def idx_url(init: dt.datetime, lead_hours: int, base: str = DEFAULT_BASE) -> str:
    return grib_url(init, lead_hours, base) + ".idx"


def fetch_fields(
    client: httpx.Client,
    init: dt.datetime,
    lead_hours: int,
    wanted: frozenset[tuple[str, str]] = GFS_WANTED,
    base: str = DEFAULT_BASE,
) -> tuple[bytes, list[IdxEntry]]:
    """Fetch only the wanted GRIB messages via ranged requests.

    Returns (concatenated GRIB bytes, selected idx entries). Raises on any
    HTTP failure — the caller's manifest/retry layer decides what to do;
    silent partial data is never returned.
    """
    idx_text = client.get(idx_url(init, lead_hours, base), timeout=60.0)
    idx_text.raise_for_status()
    selected = select_gfs(parse_gfs_idx(idx_text.text), wanted)
    if len(selected) < len(wanted):
        found = {(e.var, e.level) for e in selected}
        raise ValueError(
            f"GFS idx missing fields for f{lead_hours:03d} {init:%Y-%m-%d %HZ}: "
            f"{sorted(wanted - found)}"
        )

    url = grib_url(init, lead_hours, base)
    chunks: list[bytes] = []
    for start, stop in coalesce(selected):
        header = f"bytes={start}-" if stop is None else f"bytes={start}-{stop - 1}"
        resp = client.get(url, headers={"Range": header}, timeout=120.0)
        resp.raise_for_status()
        chunks.append(resp.content)
    return b"".join(chunks), selected
