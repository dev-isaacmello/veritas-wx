"""Station-density weights, Rodwell et al. (2010) eq. 22-23 (ADR-0004 item 3).

Brazil's network is densest in the Southeast; an unweighted national mean
over-represents it. The remedy is inverse-density weighting with a Gaussian
kernel on great-circle angles: the density seen by station k is

    rho_k = sum_l exp(-(alpha_kl / alpha_0)^2)

where alpha_kl is the great-circle angle between stations k and l (a station
always contributes exp(0) = 1 to its own density), and the weight is
w_k = 1 / rho_k, normalized to mean 1 so that weighted and unweighted means
share the same scale. alpha_0 defaults to 0.75 degrees (~83 km); stations
further than ~4 * alpha_0 apart contribute negligibly to each other's
density. Pairwise angles make this O(N^2) — fine for our few hundred
stations. Pure function: stations in, (station_id, weight) out.

Consumers: the ``*_stat`` building blocks in ``analyze.metrics.core`` switch
to the weighted mean when an optional ``weight`` column is present on the
matched-pairs frame. Exploratory diagnostic only — metrics_registry.yaml is
untouched.

Reference: Rodwell, M.J., Richardson, D.S., Hewson, T.D., Haiden, T. (2010),
"A new equitable score suitable for verifying precipitation in numerical
weather prediction", QJRMS 136:1344-1363, doi:10.1002/qj.656, eq. 22-23.

Portions derived from WeatherBench-X (Copyright 2023 Google LLC, Apache
License 2.0), adapted from weatherbenchX/weighting.py
(StationDensityWeighting): the Gaussian-kernel density on pairwise
great-circle angles with alpha_0 = 0.75 degrees and mean-1 normalization.
"""

import numpy as np
import polars as pl

from veritas_wx.ingest.static.stations import EARTH_RADIUS_KM, haversine_km

DEFAULT_ALPHA_0_DEGREES = 0.75


def station_density_weights(
    stations: pl.DataFrame,
    alpha_0_degrees: float = DEFAULT_ALPHA_0_DEGREES,
) -> pl.DataFrame:
    """Inverse-density weight per station, normalized to mean 1.0.

    Expects one row per physical station with ``station_id``, ``lat`` and
    ``lon`` columns (extra columns ignored). Duplicated station_ids or null
    coordinates raise: duplicates would inflate their own local density and
    silently shrink their weight, so the caller must pass the curated,
    deduplicated station list. Distances reuse
    :func:`veritas_wx.ingest.static.stations.haversine_km`, converted to
    great-circle angle via the same mean Earth radius.

    Returns a DataFrame (station_id, weight) in the input row order, with
    ``mean(weight) == 1.0``. A single station gets weight 1.0; an isolated
    station gets a larger weight than members of a dense cluster.
    """
    if alpha_0_degrees <= 0.0:
        raise ValueError(f"alpha_0_degrees must be > 0, got {alpha_0_degrees}")
    if stations.height == 0:
        return pl.DataFrame(schema={"station_id": pl.Utf8, "weight": pl.Float64})
    if stations["station_id"].n_unique() != stations.height:
        raise ValueError(
            "station_density_weights: duplicated station_id — pass the curated, "
            "deduplicated station list (duplicates would skew the density)"
        )
    if stations["lat"].null_count() or stations["lon"].null_count():
        raise ValueError("station_density_weights: null lat/lon — curate coordinates first")

    lat = stations["lat"].to_numpy()
    lon = stations["lon"].to_numpy()
    distances_km = haversine_km(lat[:, None], lon[:, None], lat[None, :], lon[None, :])
    angles_rad = np.atleast_2d(distances_km) / EARTH_RADIUS_KM
    alpha_0_rad = np.deg2rad(alpha_0_degrees)
    density = np.exp(-((angles_rad / alpha_0_rad) ** 2)).sum(axis=1)
    weights = 1.0 / density
    weights /= weights.mean()
    return pl.DataFrame({"station_id": stations["station_id"], "weight": weights})
