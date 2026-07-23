"""GRIB index parsing + byte-range selection — download ONLY the fields we need.

A global 0.25° GRIB2 file holds hundreds of fields; we need 4-5. Both NOAA and
ECMWF publish sidecar indexes enabling HTTP Range requests per field:

  GFS ``.idx`` (text)::

      4:1051817:d=2025070100:TMP:2 m above ground:6 hour fcst:

  ECMWF ``.index`` (JSON lines)::

      {"type": "fc", "step": "6", "param": "2t", "_offset": 123, "_length": 456, ...}

Parsers are pure (tested on fixture strings); the network layer consumes the
selected ranges.
"""

import json
from dataclasses import dataclass

# Fields needed in Phase 1, per source convention
GFS_WANTED: frozenset[tuple[str, str]] = frozenset(
    {
        ("TMP", "2 m above ground"),
        ("UGRD", "10 m above ground"),
        ("VGRD", "10 m above ground"),
        ("APCP", "surface"),
    }
)
ECMWF_WANTED: frozenset[str] = frozenset({"2t", "10u", "10v", "tp"})


@dataclass(frozen=True)
class IdxEntry:
    """One GRIB message in the file: [start, stop) byte range; stop None => EOF."""

    var: str
    level: str
    start: int
    stop: int | None
    meta: str  # remaining descriptor (e.g. "6 hour fcst" / raw json)


def http_range(entry: IdxEntry) -> str:
    """HTTP Range header value for this message (inclusive end per RFC 9110)."""
    if entry.stop is None:
        return f"bytes={entry.start}-"
    return f"bytes={entry.start}-{entry.stop - 1}"


def parse_gfs_idx(text: str) -> list[IdxEntry]:
    """Parse a GFS ``.idx`` sidecar. Stop offsets come from the NEXT line."""
    raw: list[tuple[int, str, str, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split(":")
        if len(parts) < 6:
            raise ValueError(f"malformed GFS idx line: {line!r}")
        raw.append((int(parts[1]), parts[3], parts[4], parts[5]))

    entries: list[IdxEntry] = []
    for i, (start, var, level, meta) in enumerate(raw):
        stop = raw[i + 1][0] if i + 1 < len(raw) else None
        entries.append(IdxEntry(var=var, level=level, start=start, stop=stop, meta=meta))
    return entries


def select_gfs(
    entries: list[IdxEntry],
    wanted: frozenset[tuple[str, str]] = GFS_WANTED,
) -> list[IdxEntry]:
    """Keep only the (var, level) pairs we need, preserving file order."""
    return [e for e in entries if (e.var, e.level) in wanted]


def parse_ecmwf_index(text: str) -> list[IdxEntry]:
    """Parse an ECMWF Open Data ``.index`` (JSON lines with _offset/_length)."""
    entries: list[IdxEntry] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        entries.append(
            IdxEntry(
                var=rec["param"],
                level=rec.get("levtype", ""),
                start=int(rec["_offset"]),
                stop=int(rec["_offset"]) + int(rec["_length"]),
                meta=json.dumps({k: v for k, v in rec.items() if not k.startswith("_")}),
            )
        )
    return entries


def select_ecmwf(
    entries: list[IdxEntry],
    step: int,
    wanted: frozenset[str] = ECMWF_WANTED,
) -> list[IdxEntry]:
    """Keep surface params for one forecast step (ECMWF indexes carry all steps)."""
    out: list[IdxEntry] = []
    for e in entries:
        if e.var not in wanted:
            continue
        meta = json.loads(e.meta)
        if str(meta.get("step")) == str(step):
            out.append(e)
    return out


def coalesce(entries: list[IdxEntry], max_gap: int = 0) -> list[tuple[int, int | None]]:
    """Merge adjacent/overlapping ranges into fewer HTTP requests.

    Returns (start, stop) tuples, stop exclusive (None => EOF). Entries with
    unknown stop can only terminate the final merged range.
    """
    if not entries:
        return []
    ordered = sorted(entries, key=lambda e: e.start)
    merged: list[tuple[int, int | None]] = [(ordered[0].start, ordered[0].stop)]
    for e in ordered[1:]:
        start, stop = merged[-1]
        if stop is not None and e.start <= stop + max_gap:
            new_stop = None if e.stop is None else max(stop, e.stop)
            merged[-1] = (start, new_stop)
        else:
            merged.append((e.start, e.stop))
    return merged
