"""INMET annual ``dadoshistoricos`` zips -> canonical OBS_V1 rows (ADR-0002 §2).

The hourly apitempo API is degraded (2xx with empty body), so the annual bulk
zips are the PRIMARY observation source for Phase 1. Each zip holds one
latin-1 CSV per automatic station: 8 metadata lines (``KEY:;value``), then a
``;``-separated header row, then hourly rows.

Format facts verified against the real 2026.zip (2026-07-01 build):
- dates are ``YYYY/MM/DD``, hours ``HHMM UTC``;
- decimal comma, including the leading-digit-omitted form ``,8`` (= 0.8);
- missing values are EMPTY fields or the ``-9999`` sentinel;
- every data line carries a trailing ``;``;
- header wording varies in accents across vintages, so columns are matched
  after accent-stripping + uppercasing, never by position.

``parse_station_csv`` is pure (testable offline); ``rows_from_zip`` only adds
local zipfile I/O; ``fetch_year_zip`` is the single network function and goes
through the checksummed manifest for idempotency.
"""

import datetime as dt
import io
import unicodedata
import zipfile
from pathlib import Path

import httpx
import polars as pl

from veritas_wx.contracts import OBS_V1
from veritas_wx.contracts.units import c_to_k
from veritas_wx.ingest import manifest

BULK_URL = "https://portal.inmet.gov.br/uploads/dadoshistoricos/{year}.zip"
_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) veritas-wx research; +github"}
MISSING_SENTINEL = -9999.0
N_METADATA_LINES = 8

# normalized (accent-stripped, uppercased) header substring -> (variable, converter)
COLUMN_MAP: dict[str, tuple[str, callable]] = {
    "PRECIPITACAO TOTAL": ("precip_1h", float),  # mm accumulated in the hour
    "TEMPERATURA DO AR - BULBO SECO": ("t2m", c_to_k),  # degC -> K
    "VENTO, VELOCIDADE": ("wind10m", float),  # m/s
}


def _normalize(header: str) -> str:
    """Accent-strip + uppercase so header matching survives vintage wording."""
    decomposed = unicodedata.normalize("NFD", header)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).upper()


def _parse_value(field: str) -> float | None:
    """Decimal-comma field -> float; None for empty/-9999. Raises ValueError."""
    field = field.strip()
    if not field:
        return None
    value = float(field.replace(",", "."))
    if value == MISSING_SENTINEL:
        return None
    return value


def _column_indices(header_line: str) -> dict[int, tuple[str, callable]]:
    """Map CSV column index -> (canonical variable, converter) for Phase 1 vars.

    Raises if any of the three registered variables is absent — a silent
    partial parse would undercount an entire variable for a whole station.
    """
    columns = [_normalize(c) for c in header_line.split(";")]
    indices: dict[int, tuple[str, callable]] = {}
    for key, target in COLUMN_MAP.items():
        matches = [i for i, col in enumerate(columns) if key in col]
        if len(matches) != 1:
            raise ValueError(f"header match for '{key}' found {len(matches)} columns")
        indices[matches[0]] = target
    return indices


def parse_station_csv(
    text: str,
    ingest_version: str,
) -> tuple[str, pl.DataFrame, dict[str, int], int]:
    """Pure parser: one station CSV (already latin-1 decoded) -> OBS_V1 frame.

    Returns (native_id, frame, dropped, n_data_lines) — native_id comes from
    the in-file ``CODIGO (WMO):`` metadata, NOT the filename (names embed free
    text). ``n_data_lines`` is counted independently of emission so the caller
    can enforce the non-circular runlog identity:
    n_data_lines * 3 == emitted + value_missing + value_unparseable + bad_timestamp.
    """
    lines = text.splitlines()
    meta: dict[str, str] = {}
    for line in lines[:N_METADATA_LINES]:
        key, _, value = line.partition(":;")
        meta[_normalize(key)] = value.strip()
    native_id = meta.get("CODIGO (WMO)", "")
    if not native_id:
        raise ValueError("station CSV missing 'CODIGO (WMO):;' metadata line")

    header_line = lines[N_METADATA_LINES]
    if not header_line.startswith("Data;"):
        raise ValueError(f"expected header at line {N_METADATA_LINES + 1}: {header_line[:60]!r}")
    indices = _column_indices(header_line)

    station_id = f"inmet:{native_id}"
    rows: list[dict] = []
    dropped = {"bad_timestamp": 0, "value_missing": 0, "value_unparseable": 0}
    n_data_lines = 0

    for line in lines[N_METADATA_LINES + 1 :]:
        if not line.strip():
            continue
        n_data_lines += 1
        fields = line.split(";")
        try:
            day = dt.date(*map(int, fields[0].split("/")))
            hhmm = int(fields[1].removesuffix(" UTC"))
            valid_time = dt.datetime(day.year, day.month, day.day, hhmm // 100, tzinfo=dt.UTC)
        except (ValueError, IndexError):
            dropped["bad_timestamp"] += len(COLUMN_MAP)
            continue
        for idx, (variable, convert) in indices.items():
            try:
                raw = _parse_value(fields[idx])
            except (ValueError, IndexError):
                dropped["value_unparseable"] += 1
                continue
            if raw is None:
                dropped["value_missing"] += 1
                continue
            rows.append(
                {
                    "station_id": station_id,
                    "valid_time": valid_time,
                    "variable": variable,
                    "value": convert(raw),
                    "source": "inmet",
                    "source_qc_raw": None,  # bulk CSVs expose no per-value flag
                    "ingest_version": ingest_version,
                }
            )

    df = pl.DataFrame(rows, schema=OBS_V1) if rows else pl.DataFrame(schema=OBS_V1)
    return native_id, df, dropped, n_data_lines


def rows_from_zip(
    zip_path: Path,
    ingest_version: str,
    station_filter: set[str] | None = None,
) -> tuple[pl.DataFrame, dict[str, int], dict[str, dict[str, int]], int]:
    """Parse every station CSV in an annual zip -> one OBS_V1 frame.

    ``station_filter`` holds native ids (e.g. {"A001"}); stations outside it
    are skipped WITH accounting (skipped_station counts whole files, not rows,
    because their line counts are irrelevant to the OBS reconciliation).
    Returns (frame, aggregate dropped, per-station dropped, n_data_lines) —
    the caller enforces n_data_lines * 3 == frame.height + sum(row drops).
    """
    frames: list[pl.DataFrame] = []
    total = {"bad_timestamp": 0, "value_missing": 0, "value_unparseable": 0}
    per_station: dict[str, dict[str, int]] = {}
    skipped_files = 0
    n_data_lines = 0

    with zipfile.ZipFile(zip_path) as zf:
        for name in sorted(zf.namelist()):
            if not name.upper().endswith(".CSV"):
                continue
            text = zf.read(name).decode("latin-1")
            native_id, df, dropped, n_lines = parse_station_csv(text, ingest_version)
            if station_filter is not None and native_id not in station_filter:
                skipped_files += 1
                continue
            if df.height:
                frames.append(df)
            per_station[native_id] = dropped
            n_data_lines += n_lines
            for reason, count in dropped.items():
                total[reason] += count

    total["skipped_station_files"] = skipped_files
    df = pl.concat(frames) if frames else pl.DataFrame(schema=OBS_V1)
    return df, total, per_station, n_data_lines


def fetch_year_zip(
    client: httpx.Client,
    year: int,
    raw_dir: Path,
    manifest_path: Path,
) -> Path:
    """Download {year}.zip to the raw lake, sha256 it into the manifest.

    Idempotent: if the manifest already has this URL and the local file's
    checksum matches, the download is skipped. A stale/corrupt local file is
    re-fetched (never trusted on size alone).
    """
    url = BULK_URL.format(year=year)
    dest = raw_dir / f"inmet_dadoshistoricos_{year}.zip"
    mf = manifest.load(manifest_path)

    if manifest.is_fetched(mf, url) and dest.exists():
        recorded = mf.filter(pl.col("url") == url).row(0, named=True)["sha256"]
        if manifest.sha256_of(dest) == recorded:
            return dest

    raw_dir.mkdir(parents=True, exist_ok=True)
    with client.stream("GET", url, headers=_HEADERS, timeout=300.0) as resp:
        resp.raise_for_status()
        buffer = io.BytesIO()
        for chunk in resp.iter_bytes():
            buffer.write(chunk)
    payload = buffer.getvalue()
    if not zipfile.is_zipfile(io.BytesIO(payload)):
        raise RuntimeError(f"INMET bulk response for {year} is not a zip ({len(payload)} bytes)")

    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(payload)
    tmp.replace(dest)
    manifest.append(
        manifest_path,
        [
            manifest.row(
                url=url,
                local_path=dest,
                sha256=manifest.sha256_of(dest),
                size_bytes=dest.stat().st_size,
                source="inmet_bulk",
                model=None,
                init_time=None,
                status="fetched",
            )
        ],
    )
    return dest
