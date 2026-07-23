"""Elevation corrections between model grid cell and station.

Two pure corrections live here, one per variable family:

- :func:`lapse_adjust` — temperature lapse-rate correction (non-negotiable #2).
- :func:`wind_altitude_factor` / :func:`adjust_wind` — Ingleby (QJRMS 2014,
  doi:10.1002/qj.2372, section 3.3) 10 m wind speed-up factor for stations
  sitting above the model orography (ADR-0004 item 1).

Both raw and adjusted values are persisted in the fact table: fcst_raw and
fcst_elev_adj, together with delta_z. Pairs with |delta_z| > max_delta_z
(default 500 m, configs/ingest.yaml) are dropped by the fact builder —
counted, never silent.

Portions derived from WeatherBench-X (Copyright 2023 Google LLC, Apache
License 2.0), adapted from weatherbenchX/interpolations.py
(GridToSparseWithAltitudeAdjustment): the Ingleby wind adjustment factor,
including the 100 m onset offset inside the slope and the saturation at 3.0.
"""

LAPSE_RATE_K_PER_M = 0.0065

WIND_ONSET_DELTA_Z_M = 100.0
WIND_FACTOR_PER_M = 0.002
WIND_FACTOR_MAX = 3.0


def delta_z(elev_station: float, elev_cell: float) -> float:
    """Contract definition (FACT_V1): delta_z = elev_station - elev_cell, meters."""
    return elev_station - elev_cell


def lapse_adjust(
    fcst_raw: float,
    dz: float,
    lapse_rate: float = LAPSE_RATE_K_PER_M,
) -> float:
    """Adjust a temperature forecast from cell elevation to station elevation.

        fcst_elev_adj = fcst_raw - lapse_rate * delta_z

    Golden example (tests/unit/test_elevation.py): station at 800 m, cell at
    1200 m => delta_z = -400 m => adjustment = +2.6 K (station sits lower,
    therefore warmer). Applies to t2m (and dewpoint when added); NULL for
    other variables — the fact builder controls applicability.
    """
    return fcst_raw - lapse_rate * dz


def wind_altitude_factor(delta_z_m: float) -> float:
    """Ingleby (2014, section 3.3) speed-up factor for 10 m wind at elevated stations.

    Sign convention: same as FACT_V1 and the WeatherBench-X source
    (``sparse_higher_than_grid_m``): ``delta_z_m = elev_station - grid_elev``,
    POSITIVE when the station sits ABOVE the model orography. Only that
    regime is adjusted — model winds are too slow for stations on hills and
    ridges the grid smooths away. Stations at or below the model surface, or
    less than the 100 m onset above it, get factor 1.0 (no adjustment).

        factor = 1                              for delta_z <  100 m
        factor = 1 + 0.002 * (delta_z - 100)    for delta_z >= 100 m
        saturating at 3.0                       (reached at delta_z >= 1100 m)

    The 100 m subtraction inside the slope follows the WeatherBench-X port
    verbatim (their comment: not spelled out in the paper, but it makes the
    regimes overlap) and keeps the factor continuous at the onset:
    factor(100) == 1.0 exactly, factor(600) == 2.0, factor(>= 1100) == 3.0.
    The fact builder drops |delta_z| > 500 m pairs upstream
    (configs/ingest.yaml), so the saturation branch is a safety net here,
    not a working regime.
    """
    max_excess_m = (WIND_FACTOR_MAX - 1.0) / WIND_FACTOR_PER_M
    excess = min(max(delta_z_m - WIND_ONSET_DELTA_Z_M, 0.0), max_excess_m)
    return 1.0 + WIND_FACTOR_PER_M * excess


def adjust_wind(fcst_raw: float, dz: float) -> float:
    """Adjust a 10 m wind speed forecast from cell elevation to station elevation.

        fcst_elev_adj = fcst_raw * wind_altitude_factor(delta_z)

    Multiplicative, unlike the additive lapse correction: the Ingleby factor
    scales the model wind speed up for stations above the model orography and
    leaves everything else untouched (factor 1.0). Applies to wind10m only;
    the fact builder controls applicability.
    """
    return fcst_raw * wind_altitude_factor(dz)
