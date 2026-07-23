"""Lapse-rate elevation correction for temperature (non-negotiable #2).

Both values are persisted in the fact table: fcst_raw and fcst_elev_adj,
together with delta_z. Pairs with |delta_z| > max_delta_z (default 500 m,
configs/ingest.yaml) are dropped by the fact builder — counted, never silent.
"""

LAPSE_RATE_K_PER_M = 0.0065  # standard atmosphere, 6.5 K/km (Phase 1: fixed)


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
