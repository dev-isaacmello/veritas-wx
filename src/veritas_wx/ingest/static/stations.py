"""Station curation v0: INMET automatic + ISD Brazil candidates (contract STATIONS_V1).

Responsibilities (T3):
- fetch INMET automatic station metadata (apitempo, slow/unstable: retries + backoff);
- fetch/filter the NCEI isd-history inventory for active Brazilian stations;
- map both networks onto the canonical STATIONS_V1 schema;
- curation rules v0 (pure, testable): Brazil bounding box, INMET inactive flag,
  cross-network dedupe by distance (INMET wins: hourly rain gauge is the primary
  precipitation source, PLAN §2.3), |elev_station - elev_dem| review queue;
- 0.25 degree grid-cell accounting for the repr_floor coverage risk (R7).

Rows are NEVER silently dropped here: curation flips ``status`` and always fills
``exclusion_reason``. The only row filters happen in ``parse_isd_history`` (world
inventory -> Brazilian candidates) and are returned as itemized drop counts for
the caller's runlog reconciliation.
"""

import time
from collections.abc import Sequence

import httpx
import numpy as np
import polars as pl

from veritas_wx.contracts.schemas import STATIONS_V1

INMET_STATIONS_URL = "https://apitempo.inmet.gov.br/estacoes/T"
ISD_HISTORY_URL = "https://www.ncei.noaa.gov/pub/data/noaa/isd-history.csv"

# apitempo occasionally rejects generic clients; present a plain browser UA.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
    ),
    "Accept": "application/json, text/plain, */*",
}

# Curation v0 bounding box (PLAN R6): coordinates outside => excluded.
# Note: deliberately excludes far-offshore territories (e.g. São Pedro e São
# Paulo at lon ~-29.3); those exclusions surface in the curation report.
BRAZIL_BBOX = {"lat_min": -34.0, "lat_max": 6.0, "lon_min": -74.0, "lon_max": -32.0}

# CD_SITUACAO substrings that mean the station is decommissioned. "Pane"
# (malfunction) is NOT inactive: it may hold data inside the window; the v1
# completeness cut (M4) is the right filter for it.
INMET_INACTIVE_MARKERS = ("desativ", "encerr", "extint", "fechad")

EARTH_RADIUS_KM = 6371.0088  # IUGG mean Earth radius

_ELEV_SENTINEL_LOW = -900.0  # isd-history uses -999.0 for missing elevation
_ELEV_SENTINEL_HIGH = 9000.0  # and +9999.9; Earth's surface never reaches these


def _get_with_retries(
    url: str,
    *,
    timeout: float,
    retries: int,
    backoff_s: float,
) -> httpx.Response:
    """GET with browser headers, retries and exponential backoff. Raises after exhaustion."""
    errors: list[str] = []
    for attempt in range(retries):
        try:
            resp = httpx.get(
                url, headers=BROWSER_HEADERS, timeout=timeout, follow_redirects=True
            )
            resp.raise_for_status()
            return resp
        except httpx.HTTPError as exc:  # transport errors and 4xx/5xx alike
            errors.append(f"attempt {attempt + 1}: {type(exc).__name__}: {exc}")
            if attempt < retries - 1:
                time.sleep(backoff_s * (2**attempt))
    raise RuntimeError(
        f"GET {url} failed after {retries} attempts; NOT inventing data. " + " | ".join(errors)
    )


def fetch_inmet_stations(
    url: str = INMET_STATIONS_URL,
    *,
    timeout: float = 60.0,
    retries: int = 3,
    backoff_s: float = 5.0,
) -> list[dict]:
    """Fetch INMET automatic station metadata (apitempo /estacoes/T).

    Returns the raw list of station records. Raises RuntimeError when the API
    stays down after all attempts or answers something that is not a station
    list — the build must stop rather than fabricate metadata.
    """
    resp = _get_with_retries(url, timeout=timeout, retries=retries, backoff_s=backoff_s)
    data = resp.json()
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"GET {url}: expected non-empty JSON list, got {type(data).__name__}")
    missing = sum(1 for rec in data if not (isinstance(rec, dict) and rec.get("CD_ESTACAO")))
    if missing:
        raise RuntimeError(f"GET {url}: {missing} records without CD_ESTACAO — format changed?")
    return data


def parse_isd_history(
    csv_text: str,
    *,
    country: str = "BR",
    min_end: str = "20250701",
) -> tuple[pl.DataFrame, dict[str, int]]:
    """Filter the world isd-history inventory down to active candidates.

    Keeps rows with CTRY == country, END >= min_end and parseable, non-(0,0)
    coordinates. Returns (filtered_df, dropped) where dropped itemizes every
    removed row for runlog reconciliation. Pure: testable without network.
    """
    raw = pl.read_csv(csv_text.encode("utf-8"), infer_schema_length=0)
    dropped: dict[str, int] = {}

    stage = raw.filter(pl.col("CTRY") == country)
    dropped["not_country"] = raw.height - stage.height

    with_end = stage.filter(pl.col("END").is_not_null() & (pl.col("END") >= min_end))
    dropped["end_before_min"] = stage.height - with_end.height

    parsed = with_end.with_columns(
        lat=pl.col("LAT").str.strip_chars().cast(pl.Float64, strict=False),
        lon=pl.col("LON").str.strip_chars().cast(pl.Float64, strict=False),
    )
    good = parsed.filter(
        pl.col("lat").is_not_null()
        & pl.col("lon").is_not_null()
        & ~((pl.col("lat") == 0.0) & (pl.col("lon") == 0.0))
    )
    dropped["invalid_coords"] = parsed.height - good.height

    return good, {k: v for k, v in dropped.items() if v > 0}


def fetch_isd_history(
    url: str = ISD_HISTORY_URL,
    *,
    country: str = "BR",
    min_end: str = "20250701",
    timeout: float = 120.0,
    retries: int = 3,
    backoff_s: float = 5.0,
) -> tuple[pl.DataFrame, dict[str, int]]:
    """Fetch isd-history.csv and filter to active Brazilian candidates.

    Returns (filtered_df, dropped) — see ``parse_isd_history``.
    """
    resp = _get_with_retries(url, timeout=timeout, retries=retries, backoff_s=backoff_s)
    return parse_isd_history(resp.text, country=country, min_end=min_end)


def _to_float(value) -> float | None:
    """Parse a source-metadata number; absence stays absent (None), NEVER 0."""
    if value is None:
        return None
    try:
        return float(str(value).strip().replace(",", "."))
    except ValueError:
        return None


def inmet_to_canonical(
    records: Sequence[dict],
    *,
    ingest_version: str,
    source_meta: str = "inmet_apitempo:/estacoes/T",
    inactive_end_cutoff: str | None = None,
) -> pl.DataFrame:
    """Map raw INMET records onto STATIONS_V1.

    Inactive rule (curation v0): CD_SITUACAO matching a decommissioned marker
    (``INMET_INACTIVE_MARKERS``), or DT_FIM_OPERACAO strictly before
    ``inactive_end_cutoff`` (ISO date, typically the ingest window start),
    => status="excluded", exclusion_reason="inactive". No row is dropped.
    """
    rows = []
    for rec in records:
        native_id = str(rec["CD_ESTACAO"]).strip()
        situacao = str(rec.get("CD_SITUACAO") or "").strip()
        fim = str(rec.get("DT_FIM_OPERACAO") or "").strip()[:10]  # ISO date prefix

        inactive = any(m in situacao.lower() for m in INMET_INACTIVE_MARKERS)
        if inactive_end_cutoff and fim and fim < inactive_end_cutoff:
            inactive = True

        rows.append(
            {
                "station_id": f"inmet:{native_id}",
                "network": "inmet",
                "native_id": native_id,
                "name": (str(rec.get("DC_NOME") or "").strip() or None),
                "uf": (str(rec.get("SG_ESTADO") or "").strip() or None),
                "lat": _to_float(rec.get("VL_LATITUDE")),
                "lon": _to_float(rec.get("VL_LONGITUDE")),
                "elev_station": _to_float(rec.get("VL_ALTITUDE")),
                "elev_dem": None,
                "koppen": None,
                "cross_ref": None,
                "status": "excluded" if inactive else "included",
                "exclusion_reason": "inactive" if inactive else None,
                "source_meta": f"{source_meta} situacao={situacao or 'NA'}",
                "ingest_version": ingest_version,
            }
        )
    return pl.DataFrame(rows, schema=STATIONS_V1)


def isd_to_canonical(
    df: pl.DataFrame,
    *,
    ingest_version: str,
    source_meta: str = "ncei:isd-history.csv",
) -> pl.DataFrame:
    """Map the filtered isd-history frame onto STATIONS_V1.

    native_id = "USAF-WBAN". ``uf`` stays NULL (STATE in isd-history is
    US-only). Elevation sentinels (-999 / +9999.9) => NULL, never 0.
    """
    rows = []
    for rec in df.iter_rows(named=True):
        usaf = str(rec["USAF"]).strip()
        wban = str(rec["WBAN"]).strip()
        native_id = f"{usaf}-{wban}"
        elev = _to_float(rec.get("ELEV(M)"))
        if elev is not None and not (_ELEV_SENTINEL_LOW < elev < _ELEV_SENTINEL_HIGH):
            elev = None
        rows.append(
            {
                "station_id": f"isd:{native_id}",
                "network": "isd",
                "native_id": native_id,
                "name": (str(rec.get("STATION NAME") or "").strip() or None),
                "uf": None,
                "lat": rec["lat"],
                "lon": rec["lon"],
                "elev_station": elev,
                "elev_dem": None,
                "koppen": None,
                "cross_ref": None,
                "status": "included",
                "exclusion_reason": None,
                "source_meta": f"{source_meta} begin={rec.get('BEGIN')} end={rec.get('END')}",
                "ingest_version": ingest_version,
            }
        )
    return pl.DataFrame(rows, schema=STATIONS_V1)


def to_canonical(
    inmet_records: Sequence[dict],
    isd_df: pl.DataFrame,
    *,
    ingest_version: str,
    inactive_end_cutoff: str | None = None,
) -> pl.DataFrame:
    """Concatenate both networks in canonical STATIONS_V1 form (exact dtypes/columns)."""
    inmet = inmet_to_canonical(
        inmet_records,
        ingest_version=ingest_version,
        inactive_end_cutoff=inactive_end_cutoff,
    )
    isd = isd_to_canonical(isd_df, ingest_version=ingest_version)
    return pl.concat([inmet, isd], how="vertical")


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km (haversine, mean Earth radius 6371.0088 km).

    Accepts scalars or numpy arrays (broadcasting). Golden anchor: one degree
    of latitude ~= 111.19 km.
    """
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(x, dtype=np.float64)) for x in
                              (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2
    d = 2.0 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))
    return float(d) if np.ndim(d) == 0 else d


def dedupe_cross_network(df: pl.DataFrame, max_km: float = 2.0) -> pl.DataFrame:
    """Cross-network dedupe: INMET x ISD pairs closer than ``max_km`` are the
    same physical site.

    The INMET record stays ``included`` (its hourly rain gauge is the primary
    precipitation source, PLAN §2.3); the ISD twin becomes ``excluded`` with
    exclusion_reason="duplicate_of:<inmet station_id>". ``cross_ref`` is filled
    on BOTH records. Only pairs where both sides are currently ``included`` are
    considered. When several stations fall inside the radius, the nearest
    counterpart wins the cross_ref. Pure function: same number of rows out.
    """
    inc = df.filter(pl.col("status") == "included")
    inmet = inc.filter(pl.col("network") == "inmet").select(
        pl.col("station_id").alias("inmet_id"),
        pl.col("lat").alias("inmet_lat"),
        pl.col("lon").alias("inmet_lon"),
    )
    isd = inc.filter(pl.col("network") == "isd").select(
        pl.col("station_id").alias("isd_id"),
        pl.col("lat").alias("isd_lat"),
        pl.col("lon").alias("isd_lon"),
    )
    if inmet.is_empty() or isd.is_empty():
        return df

    pairs = isd.join(inmet, how="cross").drop_nulls(
        ["isd_lat", "isd_lon", "inmet_lat", "inmet_lon"]
    )
    dist = haversine_km(
        pairs["isd_lat"].to_numpy(),
        pairs["isd_lon"].to_numpy(),
        pairs["inmet_lat"].to_numpy(),
        pairs["inmet_lon"].to_numpy(),
    )
    close = pairs.with_columns(pl.Series("dist_km", np.atleast_1d(dist))).filter(
        pl.col("dist_km") <= max_km
    )
    if close.is_empty():
        return df

    ranked = close.sort("dist_km")
    isd_best = ranked.group_by("isd_id", maintain_order=True).first()
    inmet_best = ranked.group_by("inmet_id", maintain_order=True).first()

    isd_upd = isd_best.select(
        pl.col("isd_id").alias("station_id"), pl.col("inmet_id").alias("_dup_of")
    )
    inmet_upd = inmet_best.select(
        pl.col("inmet_id").alias("station_id"), pl.col("isd_id").alias("_xref")
    )

    out = df.join(isd_upd, on="station_id", how="left").join(
        inmet_upd, on="station_id", how="left"
    )
    is_dup = pl.col("_dup_of").is_not_null()
    return out.with_columns(
        status=pl.when(is_dup).then(pl.lit("excluded")).otherwise(pl.col("status")),
        exclusion_reason=pl.when(is_dup)
        .then(pl.lit("duplicate_of:") + pl.col("_dup_of"))
        .otherwise(pl.col("exclusion_reason")),
        cross_ref=pl.when(is_dup)
        .then(pl.col("_dup_of"))
        .when(pl.col("_xref").is_not_null())
        .then(pl.col("_xref"))
        .otherwise(pl.col("cross_ref")),
    ).drop("_dup_of", "_xref")


def flag_out_of_bbox(df: pl.DataFrame, bbox: dict[str, float] | None = None) -> pl.DataFrame:
    """Curation v0: coordinates outside the Brazil bounding box => excluded.

    Also excludes rows whose coordinates could not be parsed (NULL lat/lon):
    they cannot be matched to any grid cell. Never overwrites an existing
    exclusion. Pure: same rows out, only status/exclusion_reason change.
    """
    bbox = bbox or BRAZIL_BBOX
    included = pl.col("status") == "included"
    coords_null = pl.col("lat").is_null() | pl.col("lon").is_null()
    outside = (
        (pl.col("lat") < bbox["lat_min"])
        | (pl.col("lat") > bbox["lat_max"])
        | (pl.col("lon") < bbox["lon_min"])
        | (pl.col("lon") > bbox["lon_max"])
    )
    return df.with_columns(
        exclusion_reason=pl.when(included & coords_null)
        .then(pl.lit("invalid_coords"))
        .when(included & outside)
        .then(pl.lit("coords_out_of_brazil"))
        .otherwise(pl.col("exclusion_reason")),
        status=pl.when(included & (coords_null | outside))
        .then(pl.lit("excluded"))
        .otherwise(pl.col("status")),
    )


def flag_elev_review(df: pl.DataFrame, max_diff_m: float = 100.0) -> pl.DataFrame:
    """Curation v0 (risk R6): |elev_station - elev_dem| > max_diff_m => status="review".

    Review is a manual queue, NOT an exclusion — the station stays out of the
    included set until a human confirms its metadata. ``exclusion_reason``
    doubles as the review reason so no status change is ever unexplained.
    Rows lacking either elevation are left untouched (nothing to compare).
    """
    mismatch = (
        (pl.col("status") == "included")
        & pl.col("elev_station").is_not_null()
        & pl.col("elev_dem").is_not_null()
        & ((pl.col("elev_station") - pl.col("elev_dem")).abs() > max_diff_m)
    )
    reason = f"elev_diff_gt_{max_diff_m:g}m"
    return df.with_columns(
        status=pl.when(mismatch).then(pl.lit("review")).otherwise(pl.col("status")),
        exclusion_reason=pl.when(mismatch)
        .then(pl.lit(reason))
        .otherwise(pl.col("exclusion_reason")),
    )


def assign_grid_cells(df: pl.DataFrame, res: float = 0.25) -> pl.DataFrame:
    """Add INTERNAL helper columns cell_lat_idx/cell_lon_idx (floor(coord/res)).

    The 0.25 degree graticule matches the model grids used for repr_floor
    accounting (risk R7). These columns are NOT part of STATIONS_V1 and must be
    dropped before persisting.
    """
    return df.with_columns(
        cell_lat_idx=(pl.col("lat") / res).floor().cast(pl.Int32),
        cell_lon_idx=(pl.col("lon") / res).floor().cast(pl.Int32),
    )


def count_cells_with_min_stations(
    df: pl.DataFrame,
    res: float = 0.25,
    min_n: int = 2,
    status: str | None = "included",
) -> int:
    """Number of res-degree cells holding >= min_n stations (default: included only).

    Early check of risk R7: cells with >= 2 included stations are the only ones
    where repr_floor is estimable.
    """
    d = df if status is None else df.filter(pl.col("status") == status)
    d = d.filter(pl.col("lat").is_not_null() & pl.col("lon").is_not_null())
    if d.is_empty():
        return 0
    cells = assign_grid_cells(d, res=res).group_by(["cell_lat_idx", "cell_lon_idx"]).len()
    return cells.filter(pl.col("len") >= min_n).height
