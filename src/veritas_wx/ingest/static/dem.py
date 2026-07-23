"""Copernicus GLO-30 DEM elevation lookup at station coordinates (risk R6 cross-check).

Reads ONE pixel per station from the public COG bucket ``copernicus-dem-30m``
via windowed range requests — never a whole tile. Stations are grouped per
1x1 degree tile so each tile handle is opened once and reused. Access order:
unsigned S3 (AWS_NO_SIGN_REQUEST) then plain HTTPS. When both fail for a tile,
its stations get elevation ``None`` and the failure is itemized in the returned
stats — values are NEVER invented.
"""

import math
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor

DEM_BUCKET = "copernicus-dem-30m"
DEM_HTTPS_BASE = f"https://{DEM_BUCKET}.s3.amazonaws.com"


def tile_id(lat: float, lon: float) -> str:
    """GLO-30 tile name for a coordinate, e.g. Copernicus_DSM_COG_10_S16_00_W048_00_DEM.

    Tiles are 1x1 degree, named by their south-west corner (floor of lat/lon).
    """
    lat0 = math.floor(lat)
    lon0 = math.floor(lon)
    ns = "N" if lat0 >= 0 else "S"
    ew = "E" if lon0 >= 0 else "W"
    return f"Copernicus_DSM_COG_10_{ns}{abs(lat0):02d}_00_{ew}{abs(lon0):03d}_00_DEM"


def _tile_urls(tile: str) -> list[str]:
    return [
        f"s3://{DEM_BUCKET}/{tile}/{tile}.tif",  # unsigned S3 (preferred)
        f"{DEM_HTTPS_BASE}/{tile}/{tile}.tif",  # plain HTTPS fallback
    ]


def _sample_tile(
    tile: str, points: list[tuple[str, float, float]]
) -> tuple[dict[str, float | None], str | None, str | None]:
    """Open one tile (S3 unsigned, then HTTPS) and sample one pixel per station.

    Returns (elevations, transport_used, error). On total failure every station
    of the tile maps to None and ``error`` carries the last message.

    Coordinates exactly on a tile's south/east edge index one pixel past the
    raster (e.g. lat -25.0 in tile S25 -> row 3600 of 0..3599); a naive
    ``ds.sample`` silently returns the fill value 0 there. The row/col are
    clamped into the valid range instead — an offset of at most one pixel
    (~30 m), never an invented elevation.
    """
    import rasterio  # local import: worker threads need their own GDAL env
    from rasterio.windows import Window

    errors: list[str] = []
    for url in _tile_urls(tile):
        try:
            with rasterio.Env(
                AWS_NO_SIGN_REQUEST="YES",
                GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
            ), rasterio.open(url) as ds:
                out: dict[str, float | None] = {}
                for sid, lat, lon in points:
                    row, col = ds.index(lon, lat)
                    row = min(max(row, 0), ds.height - 1)
                    col = min(max(col, 0), ds.width - 1)
                    block = ds.read(1, window=Window(col, row, 1, 1))
                    out[sid] = float(block[0, 0])
                transport = "s3" if url.startswith("s3://") else "https"
                return out, transport, None
        except Exception as exc:  # rasterio raises various IO error types
            errors.append(f"{url}: {type(exc).__name__}: {exc}")
    return {sid: None for sid, _, _ in points}, None, " | ".join(errors)


def lookup_dem_elevations(
    stations: Sequence[tuple[str, float, float]],
    *,
    max_workers: int = 8,
) -> tuple[dict[str, float | None], dict]:
    """Elevation (m) from GLO-30 for each (station_id, lat, lon).

    Returns (elevations, stats). ``elevations`` maps every input station_id to
    a float or None (unavailable). ``stats`` reports tiles touched, transport
    counts and per-tile failures for the curation report.
    """
    groups: dict[str, list[tuple[str, float, float]]] = {}
    for sid, lat, lon in stations:
        groups.setdefault(tile_id(lat, lon), []).append((sid, lat, lon))

    elevations: dict[str, float | None] = {}
    failures: dict[str, str] = {}
    transports: dict[str, int] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_sample_tile, tile, pts): tile for tile, pts in groups.items()
        }
        for fut, tile in futures.items():
            vals, transport, error = fut.result()
            elevations.update(vals)
            if error is not None:
                failures[tile] = error
            if transport is not None:
                transports[transport] = transports.get(transport, 0) + 1

    stats = {
        "n_stations": len(stations),
        "n_tiles": len(groups),
        "n_tiles_failed": len(failures),
        "n_null": sum(1 for v in elevations.values() if v is None),
        "transports": transports,
        "failures": failures,
    }
    return elevations, stats
