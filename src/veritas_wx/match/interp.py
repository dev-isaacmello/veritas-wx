"""Grid -> point interpolation: pure, explicit, hand-testable.

Conventions handled here so callers never think about them again:
  - latitudes may be ascending or descending (ECMWF grids run 90 -> -90)
  - longitudes may be 0..360 (both ECMWF and GFS) while stations use -180..180
  - contract PLAN.md §2.2: wind speed is computed at grid NODES before
    interpolation (callers pass a speed field, never components, to these
    functions for wind10m)

Phase 1 defaults (configs/ingest.yaml): bilinear for t2m/wind10m, nearest for
precip_24h (preserves extremes).
"""

import numpy as np


def normalize_lon(lon: float, grid_lons: np.ndarray) -> float:
    """Map a -180..180 longitude onto the grid's convention (0..360 if needed)."""
    if grid_lons.max() > 180.0 and lon < 0.0:
        return lon + 360.0
    return lon


def _ascending(lats: np.ndarray, field: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return views with latitudes ascending (flip rows when grid is 90->-90)."""
    if lats[0] > lats[-1]:
        return lats[::-1], field[::-1, :]
    return lats, field


def _bracket(x: float, coords: np.ndarray) -> tuple[int, int, float]:
    """Indices (i0, i1) surrounding x in ascending coords and weight toward i1."""
    if x < coords[0] or x > coords[-1]:
        raise ValueError(f"point {x} outside grid [{coords[0]}, {coords[-1]}]")
    i1 = int(np.searchsorted(coords, x))
    if i1 == 0:
        return 0, 0, 0.0
    if coords[i1 - 1] == x:
        return i1 - 1, i1 - 1, 0.0
    i0 = i1 - 1
    w = (x - coords[i0]) / (coords[i1] - coords[i0])
    return i0, i1, float(w)


def bilinear(lat: float, lon: float, lats: np.ndarray, lons: np.ndarray,
             field: np.ndarray) -> float:
    """Bilinear interpolation on a regular lat/lon grid. field[lat_idx, lon_idx]."""
    lats_a, field_a = _ascending(np.asarray(lats, dtype=float), np.asarray(field, dtype=float))
    lon = normalize_lon(lon, np.asarray(lons, dtype=float))
    j0, j1, wy = _bracket(lat, lats_a)
    i0, i1, wx = _bracket(lon, np.asarray(lons, dtype=float))

    bottom = field_a[j0, i0] + wx * (field_a[j0, i1] - field_a[j0, i0])
    top = field_a[j1, i0] + wx * (field_a[j1, i1] - field_a[j1, i0])
    return float(bottom + wy * (top - bottom))


def nearest(lat: float, lon: float, lats: np.ndarray, lons: np.ndarray,
            field: np.ndarray) -> float:
    """Nearest-node value (used for precip: no smoothing of extremes)."""
    j, i = nearest_index(lat, lon, lats, lons)
    return float(np.asarray(field, dtype=float)[j, i])


def nearest_index(lat: float, lon: float, lats: np.ndarray,
                  lons: np.ndarray) -> tuple[int, int]:
    """Index of the nearest grid node in the ORIGINAL grid orientation."""
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)
    lon = normalize_lon(lon, lons)
    if not (min(lats[0], lats[-1]) <= lat <= max(lats[0], lats[-1])):
        raise ValueError(f"lat {lat} outside grid")
    if not (lons[0] <= lon <= lons[-1]):
        raise ValueError(f"lon {lon} outside grid")
    return int(np.abs(lats - lat).argmin()), int(np.abs(lons - lon).argmin())
