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
    meta: str


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
    """Keep only the (var, level) pairs we need, preserving file order.

    Field-observed NCEP quirk (2026-07): pgrb2 files can ship the SAME message
    descriptor twice (e.g. two "APCP:surface:0-6 hour acc fcst" entries at
    different offsets). We keep the FIRST occurrence of an exact
    (var, level, meta) descriptor — deterministic, and downstream
    by_short_name() would refuse duplicates anyway.
    """
    seen: set[tuple[str, str, str]] = set()
    out: list[IdxEntry] = []
    for e in entries:
        if (e.var, e.level) not in wanted:
            continue
        key = (e.var, e.level, e.meta)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def pick_gfs_apcp_bucket(entries: list[IdxEntry], lead_hours: int) -> list[IdxEntry]:
    """Keep exactly ONE APCP entry: the 6-h bucket ``(lead-6)-lead hour acc``.

    Historical pgrb2 files carry TWO APCP records at 6-hourly leads (e.g.
    "0-24 hour acc" AND "18-24 hour acc" at f024). select_gfs() dedupes exact
    descriptors but both survive when metas differ; fetching both makes the
    decoder see duplicate 'tp' and refuse. The GFS precip convention
    (PER_STEP_6H, match/precip.py) needs the 6-h bucket and nothing else.
    Non-APCP entries pass through untouched. Raises when the bucket is absent
    (a silent fallback to the wrong accumulation window would corrupt
    precip_24h sums downstream).
    """
    wanted_meta = f"{lead_hours - 6}-{lead_hours} hour acc fcst"
    out: list[IdxEntry] = []
    apcp_found = False
    for e in entries:
        if e.var != "APCP":
            out.append(e)
        elif e.meta == wanted_meta:
            out.append(e)
            apcp_found = True
    if any(e.var == "APCP" for e in entries) and not apcp_found:
        metas = [e.meta for e in entries if e.var == "APCP"]
        raise ValueError(
            f"GFS f{lead_hours:03d}: no APCP 6-h bucket '{wanted_meta}' in idx "
            f"(present: {metas})"
        )
    return out


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
