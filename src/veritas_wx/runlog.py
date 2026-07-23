"""Structured stage logging with a hard reconciliation identity.

Every pipeline stage MUST account for every row: rows_in == rows_out + sum(dropped).
Silent data loss is the most expensive bug in this domain (risk R9), so a
failed identity raises instead of warning. Drops are always itemized by reason.
"""

import json
import sys
from datetime import UTC, datetime


class ReconciliationError(RuntimeError):
    """rows_in != rows_out + sum(dropped) — a stage lost rows silently."""


def log_stage(
    stage: str,
    rows_in: int,
    rows_out: int,
    dropped: dict[str, int] | None = None,
    stream=None,
    **extra,
) -> dict:
    """Emit one JSON line accounting for a stage's rows; enforce reconciliation.

    ``dropped`` maps reason -> count (e.g. {"delta_z_excedido": 128}).
    Extra keyword fields (model, variable, window...) are included verbatim.
    Returns the record so callers/tests can assert on it.
    """
    dropped = dropped or {}
    if any(n < 0 for n in dropped.values()) or rows_in < 0 or rows_out < 0:
        raise ValueError(f"stage '{stage}': negative row counts are meaningless")

    total_dropped = sum(dropped.values())
    if rows_in != rows_out + total_dropped:
        raise ReconciliationError(
            f"stage '{stage}': rows_in={rows_in} != rows_out={rows_out} "
            f"+ dropped={total_dropped} ({dropped}). A row was lost or double-counted."
        )

    record = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "stage": stage,
        "rows_in": rows_in,
        "rows_out": rows_out,
        "dropped": dropped,
        **extra,
    }
    print(json.dumps(record, ensure_ascii=False), file=stream or sys.stderr)
    return record
