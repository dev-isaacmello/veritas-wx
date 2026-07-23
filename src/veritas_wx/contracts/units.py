"""Canonical units and centralized conversions.

Canonical storage units (frozen, contract v1):
    t2m, dewpoint  -> K
    wind10m        -> m/s (scalar speed)
    precip_24h     -> mm accumulated over [valid_time - 24h, valid_time]

Every conversion used by ingestion lives here and has a hand-computed test.
Silent unit drift is one of the most expensive bugs in this domain (risk R5).
"""

import math

KELVIN_OFFSET = 273.15
M_TO_MM = 1000.0  # ECMWF `tp` comes in meters of water equivalent


def c_to_k(celsius: float) -> float:
    """Degrees Celsius -> Kelvin."""
    return celsius + KELVIN_OFFSET


def k_to_c(kelvin: float) -> float:
    """Kelvin -> degrees Celsius (display only; storage stays in K)."""
    return kelvin - KELVIN_OFFSET


def tp_m_to_mm(tp_meters: float) -> float:
    """ECMWF total precipitation (m of water) -> mm."""
    return tp_meters * M_TO_MM


def wind_speed(u: float, v: float) -> float:
    """Scalar wind speed from components.

    Contract note (frozen in PLAN.md §2.2): speed is computed at native grid
    nodes BEFORE interpolation. Interpolating components first and taking the
    magnitude afterwards biases speed low near directional shear.
    """
    return math.hypot(u, v)


def isd_lite_scaled(raw: int, missing: int = -9999) -> float | None:
    """ISD-Lite stores scaled integers (value x 10); -9999 means missing.

    Returns the unscaled float, or None for missing values. Missing NEVER
    becomes zero (anti-pattern: silent imputation).
    """
    if raw == missing:
        return None
    return raw / 10.0
