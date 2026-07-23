"""Data contracts: schemas, QC bits, canonical units, validators.

This package is the single source of truth for every inter-stage schema.
Contracts are versioned and frozen: evolution is additive-only; breaking
changes require a new version constant plus an ADR in docs/decisions/.
"""

from veritas_wx.contracts import qc_bits, units
from veritas_wx.contracts.schemas import (
    FACT_V1,
    FORECAST_POINTS_V1,
    INTERP_METHODS,
    MODELS,
    OBS_QC_V1,
    OBS_V1,
    RESULTS_V1,
    STATIONS_V1,
    VARIABLES,
)
from veritas_wx.contracts.validate import ContractError, validate

__all__ = [
    "FACT_V1",
    "FORECAST_POINTS_V1",
    "INTERP_METHODS",
    "MODELS",
    "OBS_QC_V1",
    "OBS_V1",
    "RESULTS_V1",
    "STATIONS_V1",
    "VARIABLES",
    "ContractError",
    "qc_bits",
    "units",
    "validate",
]
