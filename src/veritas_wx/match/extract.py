"""GRIB decode + station-point extraction -> FORECAST_POINTS_V1 rows.

eccodes normalizes vendor field names to shortNames (GFS TMP@2m and ECMWF 2t
both decode as '2t'), which keeps this module model-agnostic. Units are read
from the message (never assumed): ECMWF tp arrives in meters, GFS APCP in
kg m-2 (== mm); tp_to_mm converts explicitly.

Wind contract (PLAN.md §2.2): speed is computed at grid NODES (hypot of the
u/v fields) BEFORE interpolation.
"""

import datetime as dt
import tempfile
from dataclasses import dataclass

import eccodes
import numpy as np
import polars as pl

from veritas_wx.contracts import FORECAST_POINTS_V1, validate
from veritas_wx.match import interp


@dataclass(frozen=True)
class DecodedField:
    short_name: str
    lats: np.ndarray  # 1D, grid order (may be descending)
    lons: np.ndarray  # 1D
    values: np.ndarray  # 2D [lat_idx, lon_idx]
    units: str
    step: str  # eccodes stepRange ("6", "0-6", ...)


def decode_messages(grib_bytes: bytes) -> list[DecodedField]:
    """Decode concatenated GRIB messages (as produced by the ranged fetchers)."""
    fields: list[DecodedField] = []
    with tempfile.NamedTemporaryFile(suffix=".grib2") as tmp:
        tmp.write(grib_bytes)
        tmp.flush()
        with open(tmp.name, "rb") as fh:
            while True:
                handle = eccodes.codes_grib_new_from_file(fh)
                if handle is None:
                    break
                try:
                    ni = eccodes.codes_get(handle, "Ni")
                    nj = eccodes.codes_get(handle, "Nj")
                    fields.append(
                        DecodedField(
                            short_name=eccodes.codes_get(handle, "shortName"),
                            lats=np.asarray(
                                eccodes.codes_get_array(handle, "distinctLatitudes")
                            ),
                            lons=np.asarray(
                                eccodes.codes_get_array(handle, "distinctLongitudes")
                            ),
                            values=np.asarray(
                                eccodes.codes_get_values(handle)
                            ).reshape(nj, ni),
                            units=str(eccodes.codes_get(handle, "units")),
                            step=str(eccodes.codes_get(handle, "stepRange")),
                        )
                    )
                finally:
                    eccodes.codes_release(handle)
    return fields


def tp_to_mm(field: DecodedField) -> np.ndarray:
    """Precip accumulation to mm, driven by the message's own units."""
    if field.units == "m":
        return field.values * 1000.0
    if field.units in ("kg m**-2", "kg m-2", "mm"):
        return field.values
    raise ValueError(f"unrecognized precip units {field.units!r} — refusing to guess")


def by_short_name(fields: list[DecodedField]) -> dict[str, DecodedField]:
    out: dict[str, DecodedField] = {}
    for f in fields:
        if f.short_name in out:
            raise ValueError(f"duplicate field {f.short_name} in message set")
        out[f.short_name] = f
    return out


def instantaneous_points(
    fields: dict[str, DecodedField],
    stations: pl.DataFrame,
    model: str,
    init_time: dt.datetime,
    lead_hours: int,
    ingest_version: str,
    grid_elev_field: DecodedField | None = None,
) -> pl.DataFrame:
    """t2m + wind10m rows at every station (bilinear per configs/ingest.yaml).

    Precip is NOT handled here: it needs the cross-lead series (see
    match.precip); callers assemble it via ``tp_nearest``.
    """
    t2m = fields["2t"]
    speed_values = np.hypot(fields["10u"].values, fields["10v"].values)
    wind_grid = fields["10u"]

    rows: list[dict] = []
    valid_time = init_time + dt.timedelta(hours=lead_hours)
    for st in stations.select("station_id", "lat", "lon").to_dicts():
        j, i = interp.nearest_index(st["lat"], st["lon"], t2m.lats, t2m.lons)
        grid_elev = (
            float(grid_elev_field.values[j, i]) if grid_elev_field is not None else None
        )
        common = {
            "station_id": st["station_id"],
            "model": model,
            "init_time": init_time,
            "valid_time": valid_time,
            "lead_hours": lead_hours,
            "interp_method": "bilinear",
            "grid_lat": float(t2m.lats[j]),
            "grid_lon": float(t2m.lons[i]),
            "grid_elev": grid_elev,
            "ingest_version": ingest_version,
        }
        rows.append(
            {**common, "variable": "t2m",
             "value": interp.bilinear(st["lat"], st["lon"], t2m.lats, t2m.lons, t2m.values)}
        )
        rows.append(
            {**common, "variable": "wind10m",
             "value": interp.bilinear(
                 st["lat"], st["lon"], wind_grid.lats, wind_grid.lons, speed_values)}
        )
    df = pl.DataFrame(rows, schema=FORECAST_POINTS_V1)
    return validate(df, FORECAST_POINTS_V1, "forecast_points")


def tp_nearest(
    fields: dict[str, DecodedField],
    stations: pl.DataFrame,
) -> dict[str, float]:
    """Nearest-node precip accumulation (mm) per station for THIS lead.

    Callers accumulate {lead -> value} series per station and run
    match.precip.precip_24h with the model's convention.
    """
    tp = fields["tp"]
    values_mm = tp_to_mm(tp)
    out: dict[str, float] = {}
    for st in stations.select("station_id", "lat", "lon").to_dicts():
        j, i = interp.nearest_index(st["lat"], st["lon"], tp.lats, tp.lons)
        out[st["station_id"]] = float(values_mm[j, i])
    return out
