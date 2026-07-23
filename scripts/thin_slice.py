"""M5 thin slice: one model x few days x few stations, end-to-end to Parquet.

    uv run python scripts/thin_slice.py --model gfs \
        --start 2025-08-01 --end 2025-08-02 --max-stations 20

Validates the whole forecast chain on minimum volume BEFORE any scale:
ranged fetch -> decode -> station extraction (bilinear + wind-at-nodes) ->
precip 24h convention -> FORECAST_POINTS_V1 parquet + streamed-artifact
manifest + reconciled runlog counts.

Orography: GFS reads HGT:surface at f000 (decodes as 'orog', meters —
field-verified). ECMWF models: pending audit of a surface-z product; run with
--no-orog (grid_elev NULL, elevation adjustment deferred).
"""

import argparse
import datetime as dt
import hashlib
import sys
import time
from pathlib import Path

import httpx
import polars as pl

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from veritas_wx.contracts import FORECAST_POINTS_V1, validate  # noqa: E402
from veritas_wx.ingest import manifest  # noqa: E402
from veritas_wx.ingest.forecasts import ecmwf_opendata, gfs, graphcast  # noqa: E402
from veritas_wx.ingest.forecasts.gribidx import coalesce, parse_gfs_idx, select_gfs  # noqa: E402
from veritas_wx.match import extract  # noqa: E402
from veritas_wx.match.precip import MODEL_CONVENTION, precip_24h  # noqa: E402
from veritas_wx.runlog import log_stage  # noqa: E402

LEADS = list(range(6, 241, 6))
PRECIP_LEADS = [x for x in LEADS if x >= 24]


def _retry(fn, attempts: int = 3, base_sleep: float = 5.0):
    for i in range(attempts):
        try:
            return fn()
        except (httpx.HTTPError, ValueError) as exc:  # noqa: PERF203
            if i == attempts - 1:
                raise
            print(f"[retry {i + 1}/{attempts}] {exc}", file=sys.stderr)
            time.sleep(base_sleep * (i + 1))


G0 = 9.80665  # WMO standard gravity — ECMWF 'z' (m^2/s^2) -> meters


def fetch_ecmwf_orography(
    client: httpx.Client, init: dt.datetime, model: str
) -> extract.DecodedField:
    """Surface geopotential 'z' at step 0 -> orography in meters (ADR-0002).

    Raises loudly when 'z' is absent from the 0h index — running HRES/AIFS
    without elevation correction is a --no-orog decision, never a fallback.
    """
    from veritas_wx.ingest.forecasts.gribidx import parse_ecmwf_index, select_ecmwf

    resp = client.get(ecmwf_opendata.index_url(init, 0, model), timeout=60.0)
    resp.raise_for_status()
    picked = select_ecmwf(parse_ecmwf_index(resp.text), step=0, wanted=frozenset({"z"}))
    if not picked:
        raise ValueError(f"no 'z' at step 0 in {model} index for {init:%Y-%m-%d %HZ}")
    url = ecmwf_opendata.grib_url(init, 0, model)
    start, stop = coalesce(picked)[0]
    header = f"bytes={start}-" if stop is None else f"bytes={start}-{stop - 1}"
    r = client.get(url, headers={"Range": header}, timeout=120.0)
    r.raise_for_status()
    z = extract.decode_messages(r.content)[0]
    return extract.DecodedField(
        short_name="orog",
        lats=z.lats,
        lons=z.lons,
        values=z.values / G0,
        units="m",
        step=z.step,
    )


def fetch_gfs_orography(client: httpx.Client, init: dt.datetime) -> extract.DecodedField:
    idx_text = client.get(gfs.idx_url(init, 0), timeout=60.0)
    idx_text.raise_for_status()
    picked = select_gfs(parse_gfs_idx(idx_text.text), frozenset({("HGT", "surface")}))
    if not picked:
        raise ValueError(f"no HGT:surface in GFS f000 idx for {init}")
    start, stop = coalesce(picked)[0]
    header = f"bytes={start}-" if stop is None else f"bytes={start}-{stop - 1}"
    r = client.get(gfs.grib_url(init, 0), headers={"Range": header}, timeout=120.0)
    r.raise_for_status()
    fields = extract.decode_messages(r.content)
    return fields[0]


def run(args: argparse.Namespace) -> None:
    stations = pl.read_parquet(args.stations).filter(pl.col("status") == "included")
    if args.max_stations is not None:
        stations = stations.head(args.max_stations)
    print(f"[thin-slice] {stations.height} stations, model={args.model}", file=sys.stderr)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.parquet"

    start_d = dt.date.fromisoformat(args.start)
    end_d = dt.date.fromisoformat(args.end)
    inits = [
        dt.datetime(d.year, d.month, d.day, hh, tzinfo=dt.UTC)
        for d in (start_d + dt.timedelta(days=i) for i in range((end_d - start_d).days + 1))
        for hh in args.runs
    ]

    ingest_version = args.ingest_version
    frames: list[pl.DataFrame] = []
    dropped = {"lead_fetch_failed": 0, "precip_missing_components": 0}
    manifest_rows: list[dict] = []
    orog = None

    with httpx.Client(follow_redirects=True) as client:
        if not args.no_orog:
            if args.model in ("gfs", "graphcast"):
                # GraphCast is GFS-initialized on the SAME 0.25 graticule:
                # GFS HGT:surface doubles as its grid elevation (ADR-0002).
                orog = _retry(lambda: fetch_gfs_orography(client, inits[0]))
            else:
                orog = _retry(lambda: fetch_ecmwf_orography(client, inits[0], args.model))
            print(f"[thin-slice] orography loaded: {orog.short_name}", file=sys.stderr)

        for init in inits:
            gc_run = None
            if args.model == "graphcast":
                try:
                    gc_run = _retry(lambda init=init: graphcast.GraphCastRun(init))
                except (OSError, ValueError, httpx.HTTPError) as exc:
                    # 50 known-missing runs in the window (ADR-0002): absence
                    # becomes absence of pairs, never imputation.
                    print(f"[skip] {init:%Y%m%d%HZ}: run unavailable: {exc}", file=sys.stderr)
                    dropped["lead_fetch_failed"] += len(LEADS)
                    continue

            tp_series: dict[str, dict[int, float]] = {}
            leads_ok: list[int] = []
            for lead in LEADS:
                def _fetch(init=init, lead=lead):
                    if args.model == "gfs":
                        return gfs.fetch_fields(client, init, lead)
                    return ecmwf_opendata.fetch_fields(client, init, lead, args.model)

                if gc_run is not None:
                    try:
                        gc_fields = _retry(
                            lambda lead=lead, run=gc_run: run.fields_at_lead(lead)
                        )
                    except (OSError, ValueError, httpx.HTTPError) as exc:
                        print(f"[skip] {init:%Y%m%d%HZ} +{lead}h: {exc}", file=sys.stderr)
                        dropped["lead_fetch_failed"] += 1
                        continue
                    blob = b"".join(f.values.tobytes() for f in gc_fields)
                else:
                    try:
                        blob, _sel = _retry(_fetch)
                    except (httpx.HTTPError, ValueError) as exc:
                        print(f"[skip] {init:%Y%m%d%HZ} +{lead}h: {exc}", file=sys.stderr)
                        dropped["lead_fetch_failed"] += 1
                        continue

                manifest_rows.append(
                    manifest.row(
                        url=f"{args.model}:{init:%Y%m%d%H}+{lead:03d}",
                        local_path=None,
                        sha256=hashlib.sha256(blob).hexdigest(),
                        size_bytes=len(blob),
                        source=args.model,
                        model=args.model,
                        init_time=init,
                        status="streamed",
                    )
                )
                if gc_run is not None:
                    fields = extract.by_short_name(gc_fields)
                else:
                    fields = extract.by_short_name(extract.decode_messages(blob))
                frames.append(
                    extract.instantaneous_points(
                        fields, stations, args.model, init, lead,
                        ingest_version, grid_elev_field=orog,
                    )
                )
                for st_id, mm in extract.tp_nearest(fields, stations).items():
                    tp_series.setdefault(st_id, {})[lead] = mm
                leads_ok.append(lead)

            # precip rows for this init from the accumulated series
            precip_rows: list[dict] = []
            convention = MODEL_CONVENTION[args.model]
            for st in stations.select("station_id", "lat", "lon").to_dicts():
                series = tp_series.get(st["station_id"], {})
                for lead in PRECIP_LEADS:
                    value = precip_24h(lead, series, convention)
                    if value is None:
                        dropped["precip_missing_components"] += 1
                        continue
                    grid_elev = grid_lat = grid_lon = None
                    if orog is not None:
                        from veritas_wx.match.interp import nearest_index

                        j, i = nearest_index(st["lat"], st["lon"], orog.lats, orog.lons)
                        grid_lat = float(orog.lats[j])
                        grid_lon = float(orog.lons[i])
                        grid_elev = float(orog.values[j, i])
                    precip_rows.append(
                        {
                            "station_id": st["station_id"], "model": args.model,
                            "variable": "precip_24h", "init_time": init,
                            "valid_time": init + dt.timedelta(hours=lead),
                            "lead_hours": lead, "value": value,
                            "interp_method": "nearest",
                            "grid_lat": grid_lat, "grid_lon": grid_lon,
                            "grid_elev": grid_elev, "ingest_version": ingest_version,
                        }
                    )
            if precip_rows:
                frames.append(pl.DataFrame(precip_rows, schema=FORECAST_POINTS_V1))
            if gc_run is not None:
                gc_run.close()
            print(f"[thin-slice] {init:%Y-%m-%d %HZ}: {len(leads_ok)}/{len(LEADS)} leads",
                  file=sys.stderr)

    points = pl.concat(frames) if frames else pl.DataFrame(schema=FORECAST_POINTS_V1)
    validate(points, FORECAST_POINTS_V1, "forecast_points")
    manifest.append(manifest_path, manifest_rows)

    expected = (
        len(inits) * len(LEADS) * stations.height * 2  # t2m + wind rows per fetched lead
        + len(inits) * len(PRECIP_LEADS) * stations.height  # potential precip rows
    )
    accounted = (
        points.height
        + dropped["lead_fetch_failed"] * stations.height * 2
        + dropped["precip_missing_components"]
    )
    log_stage(
        "thin_slice.extract",
        rows_in=expected,
        rows_out=points.height,
        dropped={
            "lead_fetch_failed_x_stations_x2": dropped["lead_fetch_failed"] * stations.height * 2,
            "precip_missing_components": dropped["precip_missing_components"],
            "_unaccounted": expected - accounted,
        },
        model=args.model,
        n_inits=len(inits),
    )

    out_file = out_dir / f"forecast_points_{args.model}.parquet"
    points.write_parquet(out_file)
    print(f"[thin-slice] wrote {points.height} rows -> {out_file}", file=sys.stderr)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", choices=["gfs", "hres", "aifs", "graphcast"], default="gfs")
    p.add_argument("--stations", default="data/static/stations_v0.parquet")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--runs", nargs="*", type=int, default=[0, 12])
    p.add_argument("--max-stations", type=int, default=None, help="default: all included")
    p.add_argument("--out", default="data/staged/thin_slice")
    p.add_argument("--no-orog", action="store_true")
    p.add_argument("--ingest-version", default="0.1.0+thinslice")
    args = p.parse_args()
    run(args)


if __name__ == "__main__":
    main()
