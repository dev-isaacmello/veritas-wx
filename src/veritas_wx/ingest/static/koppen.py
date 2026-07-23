"""Köppen-Geiger class per station coordinate — Beck et al. (2023), 1991-2020, 1 km.

Source raster: "High-resolution (1 km) Köppen-Geiger maps for 1901-2099 based
on constrained CMIP6 projections", Scientific Data 10, 724 (2023). Distributed
by GloH2O (https://www.gloh2o.org/koppen/) via figshare. The pipeline downloads
the official zip once into ``data/static/raw/`` and extracts only the
1991-2020 1 km GeoTIFF (~12 MB). If no source URL works, stations get
``koppen=None`` and the attempted URLs are surfaced for the curation report —
classes are NEVER invented.

Raster values 1..30 map to classes below (verbatim from the zip's legend.txt);
0 is ocean/no data. Coastal 1 km pixels can fall on ocean: a 3x3-neighborhood
majority vote (documented fallback) resolves those; otherwise None.
"""

import hashlib
import zipfile
from collections.abc import Sequence
from pathlib import Path

import httpx

KOPPEN_CLASS_BY_VALUE: dict[int, str] = {
    1: "Af", 2: "Am", 3: "Aw",
    4: "BWh", 5: "BWk", 6: "BSh", 7: "BSk",
    8: "Csa", 9: "Csb", 10: "Csc",
    11: "Cwa", 12: "Cwb", 13: "Cwc",
    14: "Cfa", 15: "Cfb", 16: "Cfc",
    17: "Dsa", 18: "Dsb", 19: "Dsc", 20: "Dsd",
    21: "Dwa", 22: "Dwb", 23: "Dwc", 24: "Dwd",
    25: "Dfa", 26: "Dfb", 27: "Dfc", 28: "Dfd",
    29: "ET", 30: "EF",
}

KOPPEN_SOURCES: tuple[dict[str, str], ...] = (
    {
        "url": "https://ndownloader.figshare.com/files/61012822",
        "member": "1991_2020/koppen_geiger_0p00833333.tif",
    },
)

RASTER_FILENAME = "koppen_geiger_1991_2020_0p00833333.tif"
ZIP_FILENAME = "koppen_geiger_tif.zip"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_koppen_raster(
    dest_dir: Path,
    sources: Sequence[dict[str, str]] = KOPPEN_SOURCES,
    *,
    timeout: float = 600.0,
) -> tuple[Path | None, dict]:
    """Fetch the 1991-2020 1 km Köppen GeoTIFF into ``dest_dir`` (idempotent).

    Tries each source zip in order, streaming to disk then extracting only the
    1 km member. Returns (raster_path | None, info). ``info`` records the URLs
    attempted, checksums and whether a cached file was reused. On total failure
    the raster path is None — the caller must record the pendency, not guess.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    raster_path = dest_dir / RASTER_FILENAME
    info: dict = {"attempted": [], "cached": False}

    if raster_path.exists() and raster_path.stat().st_size > 0:
        info.update(cached=True, raster_sha256=_sha256(raster_path))
        return raster_path, info

    for source in sources:
        url, member = source["url"], source["member"]
        info["attempted"].append(url)
        zip_path = dest_dir / ZIP_FILENAME
        try:
            if not zip_path.exists() or zip_path.stat().st_size == 0:
                tmp_path = zip_path.with_suffix(".part")
                with httpx.stream(
                    "GET", url, timeout=timeout, follow_redirects=True
                ) as resp:
                    resp.raise_for_status()
                    with tmp_path.open("wb") as fh:
                        for chunk in resp.iter_bytes(1 << 20):
                            fh.write(chunk)
                tmp_path.rename(zip_path)
            with zipfile.ZipFile(zip_path) as zf:
                with zf.open(member) as src, raster_path.open("wb") as dst:
                    dst.write(src.read())
            info.update(
                url=url,
                member=member,
                zip_sha256=_sha256(zip_path),
                raster_sha256=_sha256(raster_path),
            )
            return raster_path, info
        except (httpx.HTTPError, zipfile.BadZipFile, KeyError, OSError) as exc:
            info.setdefault("errors", []).append(f"{url}: {type(exc).__name__}: {exc}")

    return None, info


def lookup_koppen(
    stations: Sequence[tuple[str, float, float]],
    raster_path: Path,
) -> tuple[dict[str, str | None], dict]:
    """Köppen class for each (station_id, lat, lon) from the local 1 km raster.

    Samples one pixel per station. A 0 (ocean) pixel falls back to the majority
    non-zero class in the surrounding 3x3 window (coastal stations); when that
    is also empty the station gets None. Returns (classes, stats).
    """
    import numpy as np
    import rasterio
    from rasterio.windows import Window

    classes: dict[str, str | None] = {}
    n_window_fallback = 0
    with rasterio.open(raster_path) as ds:
        values = ds.sample([(lon, lat) for _, lat, lon in stations])
        for (sid, lat, lon), val in zip(stations, values, strict=True):
            code = int(val[0])
            if code == 0:
                row, col = ds.index(lon, lat)
                win = Window(col - 1, row - 1, 3, 3)
                block = ds.read(1, window=win, boundless=True, fill_value=0)
                nonzero = block[block > 0]
                if nonzero.size:
                    code = int(np.bincount(nonzero.ravel()).argmax())
                    n_window_fallback += 1
            classes[sid] = KOPPEN_CLASS_BY_VALUE.get(code)

    stats = {
        "n_stations": len(stations),
        "n_null": sum(1 for v in classes.values() if v is None),
        "n_window_fallback": n_window_fallback,
        "raster": str(raster_path),
    }
    return classes, stats
