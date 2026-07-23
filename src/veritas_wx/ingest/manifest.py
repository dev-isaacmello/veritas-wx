"""Checksummed download manifests — the idempotency and audit backbone.

Every raw artifact gets a manifest row {url, local_path, sha256, bytes, ...}.
Re-running ingestion skips artifacts whose checksum verifies; raw files may be
pruned after extraction (prune_raw) because (url, sha256, ingest_version)
plus the immutable public buckets preserve reproducibility.
"""

import datetime as dt
import hashlib
from pathlib import Path

import polars as pl

MANIFEST_SCHEMA: dict[str, pl.DataType] = {
    "url": pl.Utf8,
    "local_path": pl.Utf8,
    "sha256": pl.Utf8,
    "bytes": pl.Int64,
    "source": pl.Utf8,
    "model": pl.Utf8,
    "init_time": pl.Datetime(time_unit="us", time_zone="UTC"),
    "downloaded_at": pl.Datetime(time_unit="us", time_zone="UTC"),
    "status": pl.Utf8,
}


def sha256_of(path: Path, chunk_bytes: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(chunk_bytes):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: Path) -> pl.DataFrame:
    if path.exists():
        return pl.read_parquet(path)
    return pl.DataFrame(schema=MANIFEST_SCHEMA)


def append(path: Path, rows: list[dict]) -> pl.DataFrame:
    """Append rows and rewrite atomically (manifest stays small: one row/artifact)."""
    df = pl.concat([load(path), pl.DataFrame(rows, schema=MANIFEST_SCHEMA)])
    tmp = path.with_suffix(".tmp.parquet")
    df.write_parquet(tmp)
    tmp.replace(path)
    return df


def is_fetched(manifest: pl.DataFrame, url: str) -> bool:
    """True when the artifact was already fetched or verified (skip on re-run)."""
    if manifest.height == 0:
        return False
    return (
        manifest.filter(
            (pl.col("url") == url) & pl.col("status").is_in(["fetched", "verified", "pruned"])
        ).height
        > 0
    )


def row(
    url: str,
    local_path: Path | None,
    sha256: str,
    size_bytes: int,
    source: str,
    model: str | None,
    init_time: dt.datetime | None,
    status: str = "fetched",
) -> dict:
    return {
        "url": url,
        "local_path": str(local_path) if local_path else None,
        "sha256": sha256,
        "bytes": size_bytes,
        "source": source,
        "model": model,
        "init_time": init_time,
        "downloaded_at": dt.datetime.now(dt.UTC),
        "status": status,
    }
