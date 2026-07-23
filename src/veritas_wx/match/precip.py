"""Per-model precipitation accumulation conventions -> canonical 24h totals.

Getting this wrong silently corrupts one third of the dataset (risk R4), so
each model's convention is explicit, documented, and golden-tested:

  from_init  (aifs, hres): field is TOTAL accumulation since init.
                           precip_24h(L) = value(L) - value(L-24)
  per_step_6h (gfs, graphcast): each 6h step carries the accumulation of ITS
                           OWN preceding 6h window (GFS "X-Y hour acc" buckets
                           at 6-hourly steps; GraphCast 6h totals).
                           precip_24h(L) = sum(value at L-18, L-12, L-6, L)

Any missing component => None (never partial sums, never zero-fill).
Negative accumulations (GRIB packing artifacts, AI-model quirks) are PRESERVED
— the caller counts them; clipping is an analysis-registry decision, not an
ingestion decision.
"""

from enum import Enum


class AccumConvention(Enum):
    FROM_INIT = "from_init"
    PER_STEP_6H = "per_step_6h"


MODEL_CONVENTION: dict[str, AccumConvention] = {
    "aifs": AccumConvention.FROM_INIT,
    "hres": AccumConvention.FROM_INIT,
    "gfs": AccumConvention.PER_STEP_6H,
    "graphcast": AccumConvention.PER_STEP_6H,
}


def precip_24h(
    lead_hours: int,
    series_mm: dict[int, float],
    convention: AccumConvention,
) -> float | None:
    """24h accumulation ending at ``lead_hours`` from a lead->value(mm) series.

    ``series_mm`` values must already be in mm (ECMWF tp converted upstream).
    Returns None when lead < 24 or any required component is missing.
    """
    if lead_hours < 24:
        return None

    if convention is AccumConvention.FROM_INIT:
        end = series_mm.get(lead_hours)
        start = 0.0 if lead_hours == 24 else series_mm.get(lead_hours - 24)
        if lead_hours == 24:
            return end
        if end is None or start is None:
            return None
        return end - start

    chunks = [series_mm.get(lead_hours - k) for k in (18, 12, 6, 0)]
    if any(c is None for c in chunks):
        return None
    return float(sum(chunks))
