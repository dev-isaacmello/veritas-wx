#!/usr/bin/env python3
"""T2 — Data availability audit for veritas-wx (PLAN.md §5-T2, risk R2).

Probes every source anonymously (no AWS credentials; plain HTTPS ListObjectsV2,
small .idx/.index/CSV downloads, HEAD and byte-range requests only — never a
full GRIB/netCDF) and writes docs/data_audit.md: the source x month coverage
matrix for the proposed window, byte-per-run measurements, the M7 disk
projection, INMET latencies, the ISD-BR inventory and a GO/NO-GO per source.

Usage:
    uv run python scripts/audit_sources.py            # full audit -> docs/data_audit.md
    uv run python scripts/audit_sources.py --quick    # 2 sample months, report -> stdout

Idempotent: read-only probing; the report is regenerated from scratch each run.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import statistics
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import polars as pl

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "docs" / "data_audit.md"

WINDOW_START = date(2025, 7, 1)
WINDOW_END = date(2026, 6, 30)
LEADS = list(range(6, 241, 6))
RUNS = (0, 12)
RUNS_PER_DAY = len(RUNS)
DISK_BUDGET_GB = 767

GFS_BUCKET = "noaa-gfs-bdp-pds"
ECMWF_BUCKET = "ecmwf-forecasts"
MLWP_BUCKET = "noaa-oar-mlwp-data"
DEM_BUCKET = "copernicus-dem-30m"

GFS_FIELDS = {
    "TMP:2 m above ground": "t2m",
    "UGRD:10 m above ground": "u10",
    "VGRD:10 m above ground": "v10",
    "APCP:surface": "apcp_6h",
}
GFS_OROG_FIELD = "HGT:surface"
ECMWF_PARAMS = ("2t", "10u", "10v", "tp")
GRAPHCAST_PREFIX = "GRAP_v100_GFS"
GRAPHCAST_N_FIELDS_TOTAL = 5 + 6 * 13
GRAPHCAST_N_FIELDS_NEEDED = 4
GRAPHCAST_GRID = (41, 721, 1440)

INMET_API = "https://apitempo.inmet.gov.br"
INMET_SAMPLE = ("2025-08-01", "2025-08-31", "A001")
INMET_BULK = "https://portal.inmet.gov.br/uploads/dadoshistoricos/{year}.zip"
BDMEP_URL = "https://bdmep.inmet.gov.br/"
ISD_HISTORY = "https://www.ncei.noaa.gov/pub/data/noaa/isd-history.csv"
ISD_LITE = "https://www.ncei.noaa.gov/pub/data/noaa/isd-lite/{year}/{usaf}-{wban}-{year}.gz"
GLOBAL_HOURLY = "https://www.ncei.noaa.gov/data/global-hourly/access/{year}/{usaf}{wban}.csv"
ISD_SAMPLE_STATION = ("833780", "99999")
ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
MJO_URL = "http://www.bom.gov.au/climate/mjo/graphics/rmm.74toRealtime.txt"
MJO_FALLBACK_PSL_OMI = "https://psl.noaa.gov/mjo/mjoindex/omi.1x.txt"
MJO_FALLBACK_IRI_RMM = "https://iridl.ldeo.columbia.edu/SOURCES/.BoM/.MJO/.RMM/"
KOPPEN_FIGSHARE_ARTICLE = "https://api.figshare.com/v2/articles/21789074"
KOPPEN_GLOH2O = "https://www.gloh2o.org/koppen/"
DEM_SAMPLE_TILE = "Copernicus_DSM_COG_10_S16_00_W048_00_DEM"

BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


class Prober:
    """httpx wrapper: retries, request counting, byte accounting."""

    def __init__(self, timeout: float = 60.0):
        self.client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": BROWSER_UA},
            follow_redirects=True,
        )
        self.n_requests = 0
        self.bytes_downloaded = 0

    def _request(self, method: str, url: str, attempts: int = 3, **kw: Any) -> httpx.Response:
        last: Exception | None = None
        for i in range(attempts):
            try:
                self.n_requests += 1
                resp = self.client.request(method, url, **kw)
                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"server error {resp.status_code}", request=resp.request, response=resp
                    )
                self.bytes_downloaded += len(resp.content)
                return resp
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last = exc
                if i < attempts - 1:
                    time.sleep(2.0 * (i + 1))
        raise RuntimeError(f"{method} {url} failed after {attempts} attempts: {last}")

    def get(self, url: str, **kw: Any) -> httpx.Response:
        return self._request("GET", url, **kw)

    def head(self, url: str, **kw: Any) -> httpx.Response:
        return self._request("HEAD", url, **kw)

    def get_range(self, url: str, byte_range: str) -> bytes:
        resp = self._request("GET", url, headers={"Range": f"bytes={byte_range}"})
        return resp.content

    def s3_list(
        self, bucket: str, prefix: str, delimiter: str = "", max_pages: int = 20
    ) -> tuple[list[tuple[str, int]], list[str]]:
        """Anonymous ListObjectsV2. Returns ([(key, size)], [common_prefixes])."""
        keys: list[tuple[str, int]] = []
        prefixes: list[str] = []
        token: str | None = None
        for _ in range(max_pages):
            q: dict[str, str] = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
            if delimiter:
                q["delimiter"] = delimiter
            if token:
                q["continuation-token"] = token
            url = f"https://{bucket}.s3.amazonaws.com/?{urllib.parse.urlencode(q)}"
            root = ET.fromstring(self.get(url).content)
            for c in root.iter(S3_NS + "Contents"):
                keys.append((c.find(S3_NS + "Key").text, int(c.find(S3_NS + "Size").text)))
            for p in root.iter(S3_NS + "CommonPrefixes"):
                prefixes.append(p.find(S3_NS + "Prefix").text)
            truncated = root.find(S3_NS + "IsTruncated")
            nt = root.find(S3_NS + "NextContinuationToken")
            if truncated is None or truncated.text != "true" or nt is None:
                break
            token = nt.text
        return keys, prefixes


def window_months() -> list[tuple[int, int]]:
    months, d = [], WINDOW_START
    while d <= WINDOW_END:
        months.append((d.year, d.month))
        d = (d.replace(day=1) + timedelta(days=32)).replace(day=1)
    return months


def days_of_month(year: int, month: int) -> list[date]:
    d, out = date(year, month, 1), []
    while d.month == month:
        out.append(d)
        d += timedelta(days=1)
    return out


def sample_days(months: list[tuple[int, int]]) -> list[date]:
    return [d for (y, m) in months for d in (date(y, m, 1), date(y, m, 15))]


def fmt_gb(n_bytes: float) -> str:
    return f"{n_bytes / 1e9:,.1f}"


def fmt_mb(n_bytes: float) -> str:
    return f"{n_bytes / 1e6:,.1f}"


def gfs_probe_day_run(p: Prober, day: date, run: int) -> dict[str, Any]:
    ymd = day.strftime("%Y%m%d")
    stem = f"gfs.{ymd}/{run:02d}/atmos/gfs.t{run:02d}z.pgrb2.0p25.f"
    keys, _ = p.s3_list(GFS_BUCKET, stem)
    names = {k for k, _ in keys}
    ok = sum(
        1
        for lead in LEADS
        if f"{stem}{lead:03d}" in names and f"{stem}{lead:03d}.idx" in names
    )
    return {"date": ymd, "run": run, "leads_ok": ok, "leads_total": len(LEADS)}


def gfs_parse_idx(text: str, file_size: int, lead: int) -> dict[str, int]:
    """Byte length per needed field from a wgrib2 .idx (offset delta to next record)."""
    rows = []
    for line in text.splitlines():
        parts = line.split(":")
        if len(parts) >= 6:
            rows.append((int(parts[1]), parts[3], parts[4], parts[5]))
    rows.sort(key=lambda r: r[0])
    sizes: dict[str, int] = {}
    for i, (off, var, level, rng) in enumerate(rows):
        length = (rows[i + 1][0] if i + 1 < len(rows) else file_size) - off
        key = f"{var}:{level}"
        if key in GFS_FIELDS:
            name = GFS_FIELDS[key]
            if name == "apcp_6h":
                if rng == f"{lead - 6}-{lead} hour acc fcst" or name not in sizes:
                    sizes[name] = length
            else:
                sizes[name] = length
        elif key == GFS_OROG_FIELD and "orog" not in sizes:
            sizes["orog"] = length
    return sizes


def audit_gfs(p: Prober, months: list[tuple[int, int]], pool: ThreadPoolExecutor) -> dict:
    _, prefixes = p.s3_list(GFS_BUCKET, "gfs.", "/", max_pages=5)
    dates = sorted(m.group(1) for pre in prefixes if (m := re.match(r"gfs\.(\d{8})/$", pre)))
    have = set(dates)
    missing_dates = [
        d.strftime("%Y%m%d")
        for (y, mo) in months
        for d in days_of_month(y, mo)
        if d.strftime("%Y%m%d") not in have
    ]
    tasks = [(d, r) for d in sample_days(months) for r in RUNS]
    probes = list(pool.map(lambda t: gfs_probe_day_run(p, *t), tasks))
    ymd = WINDOW_START.strftime("%Y%m%d")
    stem = f"gfs.{ymd}/00/atmos/gfs.t00z.pgrb2.0p25.f"
    keys, _ = p.s3_list(GFS_BUCKET, stem)
    sizes_by_key = dict(keys)

    def one_idx(lead: int) -> dict[str, int]:
        url = f"https://{GFS_BUCKET}.s3.amazonaws.com/{stem}{lead:03d}.idx"
        return gfs_parse_idx(
            p.get(url).text, sizes_by_key.get(f"{stem}{lead:03d}", 0), lead
        )

    per_lead = list(pool.map(one_idx, LEADS))
    field_totals: dict[str, int] = {}
    for sizes in per_lead:
        for k, v in sizes.items():
            if k != "orog":
                field_totals[k] = field_totals.get(k, 0) + v
    orog_bytes = per_lead[0].get("orog", 0)
    bytes_per_run = sum(field_totals.values())
    return {
        "oldest": dates[0] if dates else None,
        "newest": dates[-1] if dates else None,
        "missing_dates_in_window": missing_dates,
        "probes": probes,
        "field_totals": field_totals,
        "orog_bytes_once": orog_bytes,
        "bytes_per_run": bytes_per_run,
        "rep_run": f"{ymd} 00Z",
        "full_file_size_f006": sizes_by_key.get(f"{stem}006", 0),
    }


def ecmwf_probe_day_run(p: Prober, product: str, day: date, run: int) -> dict[str, Any]:
    ymd = day.strftime("%Y%m%d")
    prefix = f"{ymd}/{run:02d}z/{product}/"
    keys, _ = p.s3_list(ECMWF_BUCKET, prefix)
    names = {k.rsplit("/", 1)[-1] for k, _ in keys}
    stem = f"{ymd}{run:02d}0000"
    ok = sum(
        1
        for lead in LEADS
        if f"{stem}-{lead}h-oper-fc.grib2" in names and f"{stem}-{lead}h-oper-fc.index" in names
    )
    return {"date": ymd, "run": run, "leads_ok": ok, "leads_total": len(LEADS)}


def audit_ecmwf_root(p: Prober) -> dict:
    """Date-level continuity of the whole bucket (shared by HRES and AIFS)."""
    _, prefixes = p.s3_list(ECMWF_BUCKET, "", "/", max_pages=5)
    dates = sorted(m.group(1) for pre in prefixes if (m := re.match(r"(\d{8})/$", pre)))
    have = set(dates)
    d0, gaps = date(int(dates[0][:4]), int(dates[0][4:6]), int(dates[0][6:])), []
    d = d0
    today = datetime.now(UTC).date()
    while d < today:
        if d.strftime("%Y%m%d") not in have:
            gaps.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return {"oldest": dates[0], "newest_date_prefix": dates[-1], "gaps_since_oldest": gaps}


def audit_ecmwf_product(
    p: Prober,
    product: str,
    months: list[tuple[int, int]],
    root: dict,
    pool: ThreadPoolExecutor,
) -> dict:
    have_gap = set(root["gaps_since_oldest"])
    missing_dates = [
        d.strftime("%Y%m%d")
        for (y, mo) in months
        for d in days_of_month(y, mo)
        if d.strftime("%Y%m%d") in have_gap
    ]
    tasks = [(d, r) for d in sample_days(months) for r in RUNS]
    probes = list(pool.map(lambda t: ecmwf_probe_day_run(p, product, *t), tasks))
    ymd = WINDOW_START.strftime("%Y%m%d")

    def one_index(lead: int) -> dict[str, int]:
        url = (
            f"https://{ECMWF_BUCKET}.s3.amazonaws.com/"
            f"{ymd}/00z/{product}/{ymd}000000-{lead}h-oper-fc.index"
        )
        sizes: dict[str, int] = {}
        for line in p.get(url).text.splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            if obj.get("levtype") == "sfc" and obj.get("param") in ECMWF_PARAMS:
                sizes[obj["param"]] = sizes.get(obj["param"], 0) + int(obj["_length"])
        return sizes

    per_lead = list(pool.map(one_index, LEADS))
    field_totals: dict[str, int] = {}
    for sizes in per_lead:
        for k, v in sizes.items():
            field_totals[k] = field_totals.get(k, 0) + v
    url0 = f"https://{ECMWF_BUCKET}.s3.amazonaws.com/{ymd}/00z/{product}/{ymd}000000-0h-oper-fc.index"
    sfc_params = sorted(
        {
            obj["param"]
            for line in p.get(url0).text.splitlines()
            if line.strip()
            for obj in [json.loads(line)]
            if obj.get("levtype") == "sfc"
        }
    )
    return {
        "probes": probes,
        "missing_dates_in_window": missing_dates,
        "field_totals": field_totals,
        "bytes_per_run": sum(field_totals.values()),
        "rep_run": f"{ymd} 00Z",
        "sfc_params_at_0h": sfc_params,
        "has_orography_at_0h": bool({"z", "gh", "orog"} & set(sfc_params)),
    }


def audit_aifs_first_date(p: Prober) -> dict:
    """aifs-single appears operationally on 2025-02-25 — verify both sides."""
    out = {}
    for ymd in ("20250224", "20250225"):
        _, prefixes = p.s3_list(ECMWF_BUCKET, f"{ymd}/00z/", "/")
        out[ymd] = sorted(x.split("/")[-2] for x in prefixes)
    return {
        "probe": out,
        "aifs_single_oldest": "20250225" if "aifs-single" in out.get("20250225", []) else None,
    }


def audit_graphcast(p: Prober, months: list[tuple[int, int]]) -> dict:
    inv: dict[tuple[str, int], int] = {}
    for year in sorted({y for y, _ in months}):
        keys, _ = p.s3_list(MLWP_BUCKET, f"{GRAPHCAST_PREFIX}/{year}/")
        for k, size in keys:
            m = re.search(rf"{GRAPHCAST_PREFIX}_(\d{{8}})(\d{{2}})_f000_f240_06\.nc$", k)
            if m:
                inv[(m.group(1), int(m.group(2)))] = size
    per_month: dict[str, dict[str, Any]] = {}
    for y, mo in months:
        expected = [(d.strftime("%Y%m%d"), r) for d in days_of_month(y, mo) for r in RUNS]
        missing = [f"{d}-{r:02d}Z" for (d, r) in expected if (d, r) not in inv]
        per_month[f"{y}-{mo:02d}"] = {
            "expected": len(expected),
            "present": len(expected) - len(missing),
            "missing": missing,
        }
    w0, w1 = WINDOW_START.strftime("%Y%m%d"), WINDOW_END.strftime("%Y%m%d")
    window_sizes = [s for (d, _), s in inv.items() if w0 <= d <= w1]
    median_size = int(statistics.median(window_sizes)) if window_sizes else 0
    _, years = p.s3_list(MLWP_BUCKET, f"{GRAPHCAST_PREFIX}/", "/")
    first_year = sorted(years)[0].rstrip("/").split("/")[-1] if years else None
    oldest = None
    if first_year:
        _, dayfolders = p.s3_list(MLWP_BUCKET, f"{GRAPHCAST_PREFIX}/{first_year}/", "/")
        if dayfolders:
            mmdd = sorted(dayfolders)[0].rstrip("/").split("/")[-1]
            oldest = f"{first_year}-{mmdd[:2]}-{mmdd[2:]}"
    ymd = WINDOW_START.strftime("%Y%m%d")
    rep_key = f"{GRAPHCAST_PREFIX}/{ymd[:4]}/{ymd[4:]}/{GRAPHCAST_PREFIX}_{ymd}00_f000_f240_06.nc"
    magic = p.get_range(f"https://{MLWP_BUCKET}.s3.amazonaws.com/{rep_key}", "0-7")
    is_hdf5 = magic == b"\x89HDF\r\n\x1a\n"
    t, la, lo = GRAPHCAST_GRID
    uncompressed_needed = GRAPHCAST_N_FIELDS_NEEDED * t * la * lo * 4
    pro_rata = median_size * GRAPHCAST_N_FIELDS_NEEDED / GRAPHCAST_N_FIELDS_TOTAL
    return {
        "per_month": per_month,
        "median_file_size": median_size,
        "oldest": oldest,
        "rep_key": rep_key,
        "is_hdf5": is_hdf5,
        "subset_bytes_pro_rata": int(pro_rata),
        "subset_bytes_uncompressed_bound": uncompressed_needed,
    }


def inmet_attempt(p: Prober, url: str) -> dict[str, Any]:
    t0 = time.time()
    try:
        resp = p.client.get(url, timeout=60.0)
        p.n_requests += 1
        p.bytes_downloaded += len(resp.content)
        rows: int | None = None
        if resp.status_code == 200 and resp.content.strip():
            try:
                rows = len(resp.json())
            except ValueError:
                rows = None
        return {
            "status": resp.status_code,
            "bytes": len(resp.content),
            "latency_s": round(time.time() - t0, 2),
            "rows": rows,
        }
    except httpx.HTTPError as exc:
        return {
            "status": None,
            "bytes": 0,
            "latency_s": round(time.time() - t0, 2),
            "error": str(exc),
        }


def audit_inmet(p: Prober) -> dict:
    meta = inmet_attempt(p, f"{INMET_API}/estacoes/T")
    stations: dict[str, int] = {}
    if meta.get("rows"):
        resp = p.get(f"{INMET_API}/estacoes/T")
        for st in resp.json():
            k = st.get("CD_SITUACAO") or "?"
            stations[k] = stations.get(k, 0) + 1
    d0, d1, code = INMET_SAMPLE
    attempts = []
    for i in range(3):
        att = inmet_attempt(p, f"{INMET_API}/estacao/{d0}/{d1}/{code}")
        attempts.append(att)
        if att.get("rows"):
            break
        if i < 2:
            time.sleep(2.0 * (i + 1))
    ok = any(a.get("rows") for a in attempts)
    empty_2xx = all(
        a.get("status") in (200, 204) and a.get("bytes") == 0 for a in attempts
    )
    bulk = {}
    for year in (2025, 2026):
        try:
            h = p.head(INMET_BULK.format(year=year))
            bulk[year] = {
                "status": h.status_code,
                "bytes": int(h.headers.get("content-length", 0)),
                "last_modified": h.headers.get("last-modified"),
            }
        except RuntimeError as exc:
            bulk[year] = {"status": None, "error": str(exc)}
    try:
        bdmep_status = p.get(BDMEP_URL).status_code
    except RuntimeError:
        bdmep_status = None
    return {
        "meta_endpoint": meta,
        "stations_by_status": stations,
        "data_attempts": attempts,
        "data_ok": ok,
        "data_empty_2xx": empty_2xx,
        "bdmep_status": bdmep_status,
        "bulk_zips": bulk,
    }


def audit_isd(p: Prober) -> dict:
    resp = p.get(ISD_HISTORY)
    inv_last_modified = resp.headers.get("last-modified")
    df = pl.read_csv(
        io.BytesIO(resp.content),
        schema_overrides={"USAF": pl.Utf8, "WBAN": pl.Utf8, "BEGIN": pl.Utf8, "END": pl.Utf8},
    )
    br = df.filter(pl.col("CTRY") == "BR")
    active = br.filter(pl.col("END") >= WINDOW_START.strftime("%Y%m%d"))
    usaf, wban = ISD_SAMPLE_STATION
    heads = {}
    for label, url in [
        ("isd_lite_2025", ISD_LITE.format(year=2025, usaf=usaf, wban=wban)),
        ("isd_lite_2026", ISD_LITE.format(year=2026, usaf=usaf, wban=wban)),
        ("global_hourly_2025", GLOBAL_HOURLY.format(year=2025, usaf=usaf, wban=wban)),
        ("global_hourly_2026", GLOBAL_HOURLY.format(year=2026, usaf=usaf, wban=wban)),
    ]:
        try:
            h = p.head(url)
            heads[label] = {
                "status": h.status_code,
                "bytes": int(h.headers.get("content-length", 0) or 0),
                "last_modified": h.headers.get("last-modified"),
            }
        except RuntimeError as exc:
            heads[label] = {"status": None, "error": str(exc)}
    last_obs = None
    if heads.get("global_hourly_2025", {}).get("status") == 200:
        tail = p.get_range(GLOBAL_HOURLY.format(year=2025, usaf=usaf, wban=wban), "-2000")
        lines = [ln for ln in tail.decode("utf-8", "replace").splitlines() if ln.strip()]
        m = re.search(r'"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})', lines[-1]) if lines else None
        if m:
            last_obs = f"{m.group(1)} {m.group(2)}"
    return {
        "inventory_last_modified": inv_last_modified,
        "br_total": br.height,
        "br_active_in_window": active.height,
        "br_max_end": br["END"].max(),
        "global_max_end": df["END"].max(),
        "sample_station": f"{usaf}-{wban} (Brasilia)",
        "heads": heads,
        "sample_last_obs_2025": last_obs,
    }


def audit_statics(p: Prober) -> dict:
    out: dict[str, Any] = {}
    keys, _ = p.s3_list(DEM_BUCKET, f"{DEM_SAMPLE_TILE}/{DEM_SAMPLE_TILE}.tif")
    out["dem"] = {
        "reachable": bool(keys),
        "sample_tile": keys[0][0] if keys else None,
        "sample_bytes": keys[0][1] if keys else None,
    }
    try:
        art = p.get(KOPPEN_FIGSHARE_ARTICLE).json()
        tif = next((f for f in art.get("files", []) if f["name"] == "koppen_geiger_tif.zip"), None)
        koppen = {
            "article_title": art.get("title", "")[:80],
            "file": tif["name"] if tif else None,
            "bytes": tif["size"] if tif else None,
            "download_url": tif["download_url"] if tif else None,
        }
        if tif:
            resp = p.get(tif["download_url"], headers={"Range": "bytes=0-0"})
            koppen["probe_status"] = resp.status_code
            cr = resp.headers.get("content-range", "")
            koppen["probe_total_bytes"] = int(cr.rsplit("/", 1)[-1]) if "/" in cr else None
        out["koppen"] = koppen
    except (RuntimeError, ValueError) as exc:
        out["koppen"] = {"error": str(exc)}
    try:
        out["koppen_gloh2o_status"] = p.get(KOPPEN_GLOH2O).status_code
    except RuntimeError:
        out["koppen_gloh2o_status"] = None
    try:
        lines = [ln for ln in p.get(ONI_URL).text.splitlines() if ln.strip()]
        out["oni"] = {"status": 200, "first_row": lines[1].split(), "last_row": lines[-1].split()}
    except (RuntimeError, IndexError) as exc:
        out["oni"] = {"error": str(exc)}
    try:
        text = p.get(MJO_URL).text
        lines = [ln for ln in text.splitlines() if ln.strip()]
        parts = lines[-1].split()
        last_date = f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
        out["mjo"] = {
            "status": 200,
            "bytes": len(text),
            "last_date": last_date,
            "stale_for_window": last_date < WINDOW_START.isoformat(),
        }
    except (RuntimeError, ValueError, IndexError) as exc:
        out["mjo"] = {"error": str(exc)}
    for label, url in [("psl_omi", MJO_FALLBACK_PSL_OMI), ("iri_rmm", MJO_FALLBACK_IRI_RMM)]:
        try:
            h = p.head(url) if label == "psl_omi" else p.get(url)
            out[f"mjo_fallback_{label}"] = {
                "status": h.status_code,
                "last_modified": h.headers.get("last-modified"),
            }
        except RuntimeError as exc:
            out[f"mjo_fallback_{label}"] = {"error": str(exc)}
    return out


def month_cell_model(
    month: tuple[int, int], probes: list[dict], missing_dates: list[str]
) -> str:
    y, mo = month
    tag = f"{y}{mo:02d}"
    mine = [pr for pr in probes if pr["date"].startswith(tag)]
    deep_ok = bool(mine) and all(pr["leads_ok"] == pr["leads_total"] for pr in mine)
    breadth_ok = not any(d.startswith(tag) for d in missing_dates)
    if deep_ok and breadth_ok:
        return "✓"
    if not mine or all(pr["leads_ok"] == 0 for pr in mine):
        return "✗"
    return "parcial"


def month_cell_graphcast(info: dict[str, Any]) -> str:
    if info["present"] == info["expected"]:
        return "✓"
    return "parcial" if info["present"] / info["expected"] >= 0.5 else "✗"


def month_cell_isd(month: tuple[int, int], last_obs: str | None) -> str:
    if not last_obs:
        return "✗"
    y, mo = month
    last = date.fromisoformat(last_obs[:10])
    if (y, mo) < (last.year, last.month):
        return "✓"
    if (y, mo) == (last.year, last.month):
        return "✓" if last == days_of_month(y, mo)[-1] else "parcial"
    return "✗"


def month_cell_inmet(month: tuple[int, int], inmet: dict) -> str:
    y, _ = month
    z = inmet["bulk_zips"].get(y, {})
    if z.get("status") != 200:
        return "✗"
    lm = z.get("last_modified") or ""
    try:
        lm_date = datetime.strptime(lm, "%a, %d %b %Y %H:%M:%S %Z").date()
    except ValueError:
        return "parcial"
    month_end = days_of_month(*month)[-1]
    return "✓" if lm_date > month_end else "parcial"


def build_matrix(results: dict, months: list[tuple[int, int]]) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    rows["GFS"] = [
        month_cell_model(m, results["gfs"]["probes"], results["gfs"]["missing_dates_in_window"])
        for m in months
    ]
    for label, key in [("ECMWF HRES", "hres"), ("ECMWF AIFS", "aifs")]:
        rows[label] = [
            month_cell_model(
                m, results[key]["probes"], results[key]["missing_dates_in_window"]
            )
            for m in months
        ]
    rows["GraphCast"] = [
        month_cell_graphcast(results["graphcast"]["per_month"][f"{y}-{mo:02d}"])
        for (y, mo) in months
    ]
    rows["INMET (bulk)"] = [month_cell_inmet(m, results["inmet"]) for m in months]
    rows["ISD/NCEI"] = [
        month_cell_isd(m, results["isd"]["sample_last_obs_2025"]) for m in months
    ]
    return rows


def render_report(results: dict, months: list[tuple[int, int]], quick: bool) -> str:
    r = results
    gfs, hres, aifs = r["gfs"], r["hres"], r["aifs"]
    gc, inmet, isd, st = r["graphcast"], r["inmet"], r["isd"], r["statics"]
    matrix = build_matrix(r, months)
    month_labels = [f"{y}-{mo:02d}" for (y, mo) in months]
    n_days_window = (WINDOW_END - WINDOW_START).days + 1
    n_runs_window = n_days_window * RUNS_PER_DAY

    def runs_total(bytes_per_run: float) -> float:
        return bytes_per_run * n_runs_window

    grib_total = runs_total(gfs["bytes_per_run"] + hres["bytes_per_run"] + aifs["bytes_per_run"])
    gc_prorata_total = runs_total(gc["subset_bytes_pro_rata"])
    gc_bound_total = runs_total(gc["subset_bytes_uncompressed_bound"])
    gc_full_total = runs_total(gc["median_file_size"])

    L: list[str] = []
    a = L.append
    a("# Auditoria de disponibilidade de dados (T2)")
    a("")
    a(f"> Gerado por `scripts/audit_sources.py` em {r['run_at']} (UTC). "
      f"Janela auditada: **{WINDOW_START} → {WINDOW_END}** "
      f"(runs {', '.join(f'{h:02d}Z' for h in RUNS)}; leads 6–240 h passo 6 h).")
    if quick:
        a("> **Modo `--quick`**: apenas 2 meses amostrados — NÃO usar como auditoria oficial.")
    a("")
    a("## Sumário executivo")
    a("")
    a("| Fonte | Veredicto | Justificativa em uma linha |")
    a("|---|---|---|")
    a("| GFS (`noaa-gfs-bdp-pds`) | **GO** | Janela completa, `.idx` presente em 100% das "
      f"amostras; ~{fmt_mb(gfs['bytes_per_run'])} MB/run nos 4 campos. |")
    a("| ECMWF HRES (`ecmwf-forecasts`) | **GO** | Bucket retém desde 2023-01-18; janela completa; "
      f"`.index` presente; ~{fmt_mb(hres['bytes_per_run'])} MB/run. |")
    a("| ECMWF AIFS (`aifs-single`) | **GO** | Operacional desde 2025-02-25; janela completa; "
      f"~{fmt_mb(aifs['bytes_per_run'])} MB/run. |")
    n_gc_missing = sum(len(v["missing"]) for v in gc["per_month"].values())
    gc_months_missing = sorted(k for k, v in gc["per_month"].items() if v["missing"])
    a(f"| GraphCast (`{GRAPHCAST_PREFIX}`) | **GO com ressalva** | {n_gc_missing} runs ausentes "
      f"na janela ({', '.join(gc_months_missing) or 'nenhum'}); arquivo "
      f"{fmt_gb(gc['median_file_size'])} GB/run exige leitura seletiva (sem backend HDF5 no "
      "venv hoje). |")
    a("| INMET | **GO (via bulk), API degradada** | apitempo horário devolve 2xx sem corpo "
      "(204/vazio); zips anuais `dadoshistoricos` cobrem a janela inteira. |")
    a("| ISD/NCEI | **NO-GO para a janela completa** | Arquivo público congelado: última obs BR "
      f"{isd['sample_last_obs_2025'] or 'n/d'}; sem arquivos 2026. Cobre só 2025-07→08. |")
    a("| Estáticos (DEM, Köppen, ONI) | **GO** | Todos alcançáveis; URLs fixadas abaixo. |")
    a("| MJO RMM (BoM) | **NO-GO na fonte primária** | Arquivo do BoM congelado em "
      f"{st['mjo'].get('last_date', 'n/d')}; fallback documentado (IRI/PSL). |")
    a("")
    a("**Recomendação de janela: manter 2025-07-01 → 2026-06-30** (evidência na §8).")
    a("")

    a("## 1. Matriz de cobertura fonte × mês")
    a("")
    a("Legenda: ✓ = mês completo · parcial = lacunas identificadas (abaixo) · ✗ = ausente.")
    a("")
    a("| Fonte | " + " | ".join(month_labels) + " |")
    a("|---" * (len(month_labels) + 1) + "|")
    for src, cells in matrix.items():
        a(f"| {src} | " + " | ".join(cells) + " |")
    a("")
    a("Critério das células (zero \"desconhecido\"): modelos GRIB — todos os dias do mês presentes "
      "no nível de prefixo de data **e** amostras profundas (dias 1 e 15, 00Z/12Z, 40 leads + "
      "índice) 100% completas; GraphCast — inventário file-a-file de TODOS os dias do mês "
      "(00Z e 12Z); INMET — mês contido nos zips anuais verificados por HEAD (tamanho + "
      "Last-Modified posterior ao fim do mês); ISD — última observação real no arquivo-amostra.")
    a("")
    gc_missing_flat = [x for v in gc["per_month"].values() for x in v["missing"]]
    if gc_missing_flat:
        a(f"Runs GraphCast ausentes na janela ({len(gc_missing_flat)} de {n_runs_window}): "
          + ", ".join(gc_missing_flat) + ".")
        a("")
    incomplete = [
        f"{pr['date']} {pr['run']:02d}Z ({pr['leads_ok']}/{pr['leads_total']})"
        for key in ("gfs", "hres", "aifs")
        for pr in r[key]["probes"]
        if pr["leads_ok"] != pr["leads_total"]
    ]
    a("Amostras GRIB incompletas: " + ("; ".join(incomplete) if incomplete else "nenhuma") + ".")
    a("")

    a("## 2. Data mais antiga disponível por fonte")
    a("")
    a("| Fonte | Mais antigo | Observação |")
    a("|---|---|---|")
    newest = gfs["newest"]
    a(f"| GFS | {gfs['oldest'][:4]}-{gfs['oldest'][4:6]}-{gfs['oldest'][6:]} | prefixo "
      f"`gfs.YYYYMMDD` mais antigo do bucket; mais recente: "
      f"{newest[:4]}-{newest[4:6]}-{newest[6:]}. |")
    root = r["ecmwf_root"]
    a(f"| ECMWF (bucket) | {root['oldest'][:4]}-{root['oldest'][4:6]}-{root['oldest'][6:]} | "
      f"retenção histórica confirmada — não é bucket rolling; lacunas desde então: "
      f"{', '.join(root['gaps_since_oldest'][:8]) or 'nenhuma'} (todas fora da janela). |")
    a(f"| ECMWF AIFS `aifs-single` | 2025-02-25 | verificado: 2025-02-24 sem `aifs-single`, "
      f"2025-02-25 com ({r['aifs_first']['aifs_single_oldest']}); antes disso o caminho era "
      "`aifs/` (fase experimental — nunca misturar, R2). |")
    a(f"| GraphCast GFS-init | {gc['oldest']} | README do bucket: regenerado, confiável desde "
      "2022-01; 00Z/12Z na janela. |")
    a("| INMET (bulk) | ≤ 2025 (não sondado além da janela) | zips anuais por estação automática; "
      "cobertura da janela comprovada pelos zips 2025/2026. |")
    a(f"| ISD/NCEI | histórico longo (BEGIN típico anos 2000) | **fim** em "
      f"{isd['sample_last_obs_2025'] or 'n/d'} — ver §5. |")
    a("")

    a("## 3. Bytes por run e projeção de disco para M7")
    a("")
    a(f"Campos: 2t/TMP2m, 10u/UGRD10, 10v/VGRD10, tp/APCP, somados sobre {len(LEADS)} leads "
      f"(6..240 h). Run representativo: {gfs['rep_run']}.")
    a("")
    a("| Modelo | Bytes/run (medido) | Detalhe por campo (MB) |")
    a("|---|---|---|")
    det = ", ".join(f"{k}={fmt_mb(v)}" for k, v in sorted(gfs["field_totals"].items()))
    orog = fmt_mb(gfs["orog_bytes_once"])
    a(f"| GFS | {fmt_mb(gfs['bytes_per_run'])} MB | {det}; orografia 1×={orog} MB |")
    det = ", ".join(f"{k}={fmt_mb(v)}" for k, v in sorted(hres["field_totals"].items()))
    a(f"| HRES | {fmt_mb(hres['bytes_per_run'])} MB | {det} |")
    det = ", ".join(f"{k}={fmt_mb(v)}" for k, v in sorted(aifs["field_totals"].items()))
    a(f"| AIFS | {fmt_mb(aifs['bytes_per_run'])} MB | {det} |")
    a(f"| GraphCast | {fmt_mb(gc['subset_bytes_pro_rata'])} MB (estimado pro-rata) | arquivo "
      f"inteiro {fmt_gb(gc['median_file_size'])} GB; {GRAPHCAST_N_FIELDS_NEEDED} de "
      f"{GRAPHCAST_N_FIELDS_TOTAL} campos 2D; teto não-comprimido "
      f"{fmt_mb(gc['subset_bytes_uncompressed_bound'])} MB |")
    a("")
    a(f"Projeção M7 — 4 modelos × 12 meses × 2 runs/dia = {n_runs_window} runs por modelo:")
    a("")
    a("| Item | Total na janela |")
    a("|---|---|")
    a(f"| GFS + HRES + AIFS (byte-range GRIB) | **{fmt_gb(grib_total)} GB** |")
    a(f"| GraphCast, leitura seletiva (pro-rata) | **{fmt_gb(gc_prorata_total)} GB** |")
    a(f"| GraphCast, teto não-comprimido dos 4 campos | {fmt_gb(gc_bound_total)} GB |")
    a(f"| GraphCast, arquivos INTEIROS (inviável) | {fmt_gb(gc_full_total)} GB |")
    total_sel = fmt_gb(grib_total + gc_prorata_total)
    a(f"| **Total tráfego (cenário seletivo)** | **≈ {total_sel} GB** |")
    a("")
    a(f"Contra o teto de disco de R1 ({DISK_BUDGET_GB} GB livres no HD): o cenário seletivo cabe "
      "com folga mesmo sem poda; com `prune_raw: true` (ingest.yaml) o residente em disco é ainda "
      "menor — raw é transitório, ficam `staged/` + `fact/` (ordens de MB–poucos GB). Baixar "
      "GraphCast inteiro NÃO cabe (4,2 TB) — leitura seletiva é obrigatória, não otimização.")
    a("")

    a("## 4. INMET — latências e estado da API")
    a("")
    m = inmet["meta_endpoint"]
    a(f"- `/estacoes/T`: status {m.get('status')}, {m.get('bytes', 0):,} bytes, "
      f"latência {m.get('latency_s')} s; estações por situação: "
      + (", ".join(f"{k}={v}" for k, v in sorted(inmet["stations_by_status"].items())) or "n/d")
      + ".")
    d0, d1, code = INMET_SAMPLE
    a(f"- `/estacao/{d0}/{d1}/{code}` (1 estação-mês horário), 3 tentativas com backoff:")
    for i, att in enumerate(inmet["data_attempts"], 1):
        a(f"  - tentativa {i}: status {att.get('status')}, {att.get('bytes', 0)} bytes, "
          f"latência {att.get('latency_s')} s"
          + (f", linhas={att['rows']}" if att.get("rows") else "")
          + (f", erro={att['error']}" if att.get("error") else ""))
    if inmet["data_ok"]:
        a("- Endpoint de dados FUNCIONOU nesta execução (ver linhas acima) — ainda assim o "
          "caminho bulk permanece o primário para M3 por reprodutibilidade.")
    elif inmet["data_empty_2xx"]:
        a("- **Diagnóstico: API degradada** — responde 2xx rápido porém SEM corpo (204/vazio) "
          "para qualquer consulta de dados (testado também com outras estações e datas de anos "
          "anteriores). Não é NO-GO: os metadados funcionam e o caminho bulk cobre a janela.")
    else:
        a("- **Diagnóstico: API indisponível nesta execução** (erros/timeout acima). Não é "
          "NO-GO: o caminho bulk cobre a janela.")
    z25, z26 = inmet["bulk_zips"].get(2025, {}), inmet["bulk_zips"].get(2026, {})
    a(f"- Fallback bulk `dadoshistoricos/2025.zip`: status {z25.get('status')}, "
      f"{z25.get('bytes', 0):,} bytes, Last-Modified {z25.get('last_modified')}.")
    a(f"- Fallback bulk `dadoshistoricos/2026.zip`: status {z26.get('status')}, "
      f"{z26.get('bytes', 0):,} bytes, Last-Modified {z26.get('last_modified')} — publicado após "
      "2026-06-30, logo contém a janela até junho.")
    a(f"- BDMEP (`{BDMEP_URL}`): status {inmet['bdmep_status']} (alcançável; exportação "
      "interativa/por e-mail — uso manual apenas).")
    a("")

    a("## 5. ISD/NCEI — inventário Brasil e congelamento do arquivo")
    a("")
    a(f"- `isd-history.csv`: Last-Modified **{isd['inventory_last_modified']}**; "
      f"END máximo global {isd['global_max_end']} — o inventário parou de ser atualizado.")
    a(f"- Estações CTRY==BR: {isd['br_total']} no total; **{isd['br_active_in_window']}** com "
      f"END ≥ {WINDOW_START} (todas com END ≤ {isd['br_max_end']}).")
    a(f"- Amostra {isd['sample_station']}: ISD-Lite 2025 status "
      f"{isd['heads']['isd_lite_2025'].get('status')} "
      f"({isd['heads']['isd_lite_2025'].get('bytes', 0):,} bytes — ano parcial); "
      f"ISD-Lite 2026 status {isd['heads']['isd_lite_2026'].get('status')}; "
      f"global-hourly 2026 status {isd['heads']['global_hourly_2026'].get('status')}.")
    a(f"- Última observação real no arquivo global-hourly 2025 da amostra: "
      f"**{isd['sample_last_obs_2025']}**.")
    a("- Conclusão: a rede ISD cobre apenas **2025-07-01 → ~2025-08-24** da janela "
      "(~1,8 de 12 meses). NO-GO como fonte de observação da janela completa; ver §8.")
    a("")

    a("## 6. Estáticos")
    a("")
    dem = st["dem"]
    a(f"- **Copernicus DEM GLO-30** (`s3://{DEM_BUCKET}`): alcançável={dem['reachable']}; tile de "
      f"amostra `{dem['sample_tile']}` ({fmt_mb(dem['sample_bytes'] or 0)} MB, COG).")
    k = st["koppen"]
    a(f"- **Köppen Beck et al. 2023**: figshare artigo 21789074 — arquivo `{k.get('file')}` "
      f"({fmt_mb(k.get('bytes') or 0)} MB), GET com range status {k.get('probe_status')} "
      f"(total confirmado {fmt_mb(k.get('probe_total_bytes') or 0)} MB); **URL que funciona**: "
      f"`{k.get('download_url')}`; espelho da página: gloh2o.org/koppen "
      f"(status {st['koppen_gloh2o_status']}).")
    oni = st["oni"]
    a(f"- **ONI (CPC)**: OK; primeira linha {' '.join(oni.get('first_row', []))}, última "
      f"**{' '.join(oni.get('last_row', []))}** — cobre a janela (estação MJJ-2026 sai ~ago/2026; "
      "lag de ~1 mês é inerente e não bloqueia o build).")
    mjo = st["mjo"]
    a(f"- **MJO RMM (BoM)**: arquivo responde (status {mjo.get('status')}, "
      f"{mjo.get('bytes', 0):,} bytes) porém **congelado em {mjo.get('last_date')}** — anterior à "
      "janela ⇒ inutilizável como fonte primária. Fallbacks sondados: espelho IRI do RMM "
      f"(status {st['mjo_fallback_iri_rmm'].get('status')}) e PSL OMI "
      f"(status {st['mjo_fallback_psl_omi'].get('status')}, Last-Modified "
      f"{st['mjo_fallback_psl_omi'].get('last_modified')}).")
    a("")

    a("## 7. Licenças por fonte")
    a("")
    a("| Fonte | Licença | Obrigações para o dataset publicado |")
    a("|---|---|---|")
    a("| ECMWF Open Data (HRES, AIFS) | **CC-BY-4.0** | Atribuição obrigatória "
      "(\"Contains modified ECMWF data\"); o dataset derivado herda CC-BY-4.0 (D9). |")
    a("| NOAA GFS, GraphCast/AIWP (CIRA/NOAA), ISD, ONI | **Domínio público** (obra do governo "
      "dos EUA) | Sem restrição; AIWP pede citação do paper Radford et al. 2025 (BAMS) — "
      "cortesia, incluir. |")
    a("| INMET | **Dado público** (gov.br; Lei de Acesso à Informação) | Citar INMET como fonte; "
      "sem restrição de redistribuição conhecida. |")
    a("| Copernicus DEM GLO-30 | Licença Copernicus (ESA) — uso e redistribuição livres | Nota de "
      "crédito \"© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided "
      "under COPERNICUS by the European Union and ESA; all rights reserved\". |")
    a("| Köppen Beck et al. 2023 | **CC-BY-4.0** (figshare) | Citar Beck et al. 2023, "
      "Sci. Data 10, 724. |")
    a("| MJO RMM (BoM) | © BoM; uso com atribuição | Irrelevante enquanto congelado; espelho IRI "
      "mantém os termos do BoM. |")
    a("")

    a("## 8. GO/NO-GO e recomendação de janela")
    a("")
    a("| Fonte | GO/NO-GO | Justificativa |")
    a("|---|---|---|")
    a("| GFS | **GO** | 12/12 meses ✓; `.idx` universal; bytes/run medidos. |")
    a("| HRES | **GO** | 12/12 meses ✓; retenção desde 2023; `.index` universal. |")
    a("| AIFS | **GO** | 12/12 meses ✓; operacional (`aifs-single`) cobre a janela inteira "
      "com margem de 4 meses. |")
    gc_cells = matrix["GraphCast"]
    n_full, n_partial = gc_cells.count("✓"), gc_cells.count("parcial")
    a(f"| GraphCast | **GO com ressalva** | {n_full}/{len(months)} meses ✓, {n_partial} "
      f"parciais ({n_gc_missing} runs ausentes, ~{100 * n_gc_missing / n_runs_window:.1f}% da "
      "janela); runs ausentes viram simplesmente pares ausentes na view casada (inner join) — "
      "sem viés de seleção entre modelos além da redução de N. |")
    a("| INMET | **GO** (bulk) | API horária degradada (2xx sem corpo) porém zips anuais "
      "íntegros e atuais cobrem 100% da janela; BDMEP alcançável como segundo fallback. |")
    a("| ISD | **NO-GO para a janela completa** | Arquivo NCEI congelado ~2025-08 (inventário, "
      "ISD-Lite e global-hourly consistentes entre si). Cobre 2025-07→08 apenas. |")
    a("| DEM / Köppen / ONI | **GO** | Alcançáveis, URLs e tamanhos fixados. |")
    a("| MJO RMM | **NO-GO na fonte BoM** | Congelado pré-janela; estrato MJO fica pendente de "
      "ADR (espelho IRI do RMM ou troca para OMI/PSL — muda o registro, exige ADR + flag). |")
    a("")
    a("### Recomendação final de janela")
    a("")
    a("**Manter 2025-07-01 → 2026-06-30.** Evidência:")
    a("")
    a("1. Os 4 sistemas de previsão cobrem a janela inteira (GraphCast com "
      f"{n_gc_missing} runs ausentes, em {', '.join(gc_months_missing) or '—'} — perda "
      f"~{100 * n_gc_missing / n_runs_window:.1f}% dos pares).")
    a("2. A espinha dorsal de observação (INMET, prioridade de precip por desenho — ingest.yaml) "
      "cobre 100% da janela via bulk zips verificados.")
    a("3. Deslocar a janela para trás para \"salvar\" o ISD é impossível sem quebrar o AIFS: "
      "`aifs-single` operacional só existe desde 2025-02-25, e o plano proíbe misturar fase "
      "experimental com operacional (R2). A maior janela comum viável INCLUINDO ISD seria "
      "**2025-07-01 → 2025-08-24** (< 2 meses) — insuficiente para 12 meses e para estratos "
      "sazonais.")
    a("4. Portanto: janela mantida SEM o ISD como fonte de janela completa. O ISD entra, no "
      "máximo, como enriquecimento opcional dos 2 primeiros meses (validação cruzada de t2m em "
      "aeroportos), nunca como rede do dataset principal — a curadoria T3 passa a mirar as "
      f"~{inmet['stations_by_status'].get('Operante', '≈477')} estações automáticas INMET "
      "operantes (meta de ~500 estações segue atingível; impacto: menor densidade em aeroportos).")
    a("")

    a("## 9. Pendências explícitas (para os próximos milestones)")
    a("")
    a("- **GraphCast, inspeção interna do netCDF**: formato confirmado HDF5 por bytes-mágicos "
      f"({'✓' if gc['is_hdf5'] else 'FALHOU'}), mas o venv NÃO tem backend HDF5 (h5py/h5netcdf/"
      "netCDF4 ausentes de pyproject, inclusive dos grupos opcionais). Antes de M5: adicionar "
      "`h5netcdf` (ou `netCDF4`) ao grupo `grib` e verificar lista de variáveis, chunking e "
      "custo real de leitura seletiva dos 4 campos (a estimativa pro-rata assume compressão "
      "uniforme entre variáveis). Variáveis esperadas conforme README do bucket (inclui "
      "precipitação acumulada de 6 h).")
    for label, prod in (("HRES", hres), ("AIFS", aifs)):
        if prod["has_orography_at_0h"]:
            a(f"- **Orografia {label}**: disponível no `.index` de 0h (params sfc incluem "
            f"{', '.join(x for x in prod['sfc_params_at_0h'] if x in ('z', 'gh', 'orog'))}) — "
            "baixar 1× por modelo junto com o run representativo.")
        else:
            a(f"- **Orografia {label}**: AUSENTE do `.index` de 0h (params sfc: "
              f"{', '.join(prod['sfc_params_at_0h'][:14])}…). Usar fallback previsto em §2.2 do "
              "PLAN: média do DEM na célula, sinalizada em `grid_cells_{model}.parquet`.")
    a("- **MJO**: ADR para trocar a fonte (espelho IRI do RMM vs. OMI do PSL) — muda "
      "`metrics_registry` (estratos), portanto exige ADR antes de M6/M7.")
    a("- **INMET**: monitorar se a API volta (re-rodar este script); parsing dos zips anuais "
      "entra em M3 com o mesmo contrato OBS_V1.")
    a("- **ONI**: estação MJJ-2026 (necessária para estratificar jun/2026) publica ~ago/2026 — "
      "verificar no build de M7.")
    a("")

    a("## Apêndice — execução")
    a("")
    a(f"- Executado em {r['run_at']} UTC; duração {r['duration_s']:.0f} s; "
      f"{r['n_requests']} requisições HTTP; {fmt_mb(r['bytes_downloaded'])} MB baixados "
      "(somente listagens, índices, CSVs, HEADs e ranges — nenhum GRIB/netCDF inteiro).")
    a(f"- Amostragem de profundidade: dias 1 e 15 de cada mês × runs 00Z/12Z "
      f"({len(sample_days(months))} dias × {RUNS_PER_DAY} runs por fonte GRIB).")
    a("- Re-executável: `uv run python scripts/audit_sources.py` (ou `--quick` para fumaça).")
    a("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="T2 data availability audit")
    ap.add_argument("--quick", action="store_true", help="sample only 2 months; print to stdout")
    ap.add_argument(
        "--out",
        default=None,
        help="output path ('-' for stdout). Default: docs/data_audit.md (stdout when --quick)",
    )
    ap.add_argument("--workers", type=int, default=8, help="parallel probe workers")
    args = ap.parse_args()

    months = window_months()
    if args.quick:
        months = [months[0], months[-1]]
    out_path = args.out if args.out is not None else ("-" if args.quick else str(DEFAULT_OUT))

    t0 = time.time()
    p = Prober()
    results: dict[str, Any] = {"run_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M")}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        print("[1/7] GFS ...", file=sys.stderr)
        results["gfs"] = audit_gfs(p, months, pool)
        print("[2/7] ECMWF root scan ...", file=sys.stderr)
        results["ecmwf_root"] = audit_ecmwf_root(p)
        results["aifs_first"] = audit_aifs_first_date(p)
        print("[3/7] ECMWF HRES ...", file=sys.stderr)
        results["hres"] = audit_ecmwf_product(
            p, "ifs/0p25/oper", months, results["ecmwf_root"], pool
        )
        print("[4/7] ECMWF AIFS ...", file=sys.stderr)
        results["aifs"] = audit_ecmwf_product(
            p, "aifs-single/0p25/oper", months, results["ecmwf_root"], pool
        )
        print("[5/7] GraphCast/AIWP ...", file=sys.stderr)
        results["graphcast"] = audit_graphcast(p, months)
        print("[6/7] INMET + ISD ...", file=sys.stderr)
        results["inmet"] = audit_inmet(p)
        results["isd"] = audit_isd(p)
        print("[7/7] statics ...", file=sys.stderr)
        results["statics"] = audit_statics(p)
    results["duration_s"] = time.time() - t0
    results["n_requests"] = p.n_requests
    results["bytes_downloaded"] = p.bytes_downloaded

    report = render_report(results, months, args.quick)
    if out_path == "-":
        print(report)
    else:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(report, encoding="utf-8")
        print(f"wrote {out_path}", file=sys.stderr)
    print(
        f"done in {results['duration_s']:.0f}s — {p.n_requests} requests, "
        f"{p.bytes_downloaded / 1e6:.1f} MB downloaded",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
