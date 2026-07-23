"""Contract validation must fail loudly, specifically, and list every problem."""

import datetime as dt

import polars as pl
import pytest

from veritas_wx.contracts import FACT_V1, OBS_V1, ContractError, qc_bits, validate
from veritas_wx.contracts.validate import require_non_null


def _fact_row() -> pl.DataFrame:
    utc = dt.UTC
    return pl.DataFrame(
        {
            "station_id": ["inmet:A001"],
            "model": ["gfs"],
            "variable": ["t2m"],
            "init_time": [dt.datetime(2025, 7, 1, 0, tzinfo=utc)],
            "valid_time": [dt.datetime(2025, 7, 1, 6, tzinfo=utc)],
            "lead_hours": [6],
            "fcst_raw": [298.15],
            "fcst_elev_adj": [298.80],
            "obs": [297.95],
            "delta_z": [-100.0],
            "interp_method": ["bilinear"],
            "repr_floor": [None],
            "qc_flags": [0],
            "ingest_version": ["0.1.0+abc1234.deadbeef"],
        },
        schema=FACT_V1,
    )


def test_valid_fact_row_passes():
    df = _fact_row()
    assert validate(df, FACT_V1, "fact") is df


def test_empty_frame_with_schema_passes():
    validate(pl.DataFrame(schema=OBS_V1), OBS_V1, "obs")


def test_missing_column_named_in_error():
    df = _fact_row().drop("repr_floor")
    with pytest.raises(ContractError, match="repr_floor"):
        validate(df, FACT_V1, "fact")


def test_extra_column_rejected_by_default():
    df = _fact_row().with_columns(pl.lit(1).alias("sneaky"))
    with pytest.raises(ContractError, match="sneaky"):
        validate(df, FACT_V1, "fact")
    # but explicitly allowed when a stage adds columns on purpose
    validate(df, FACT_V1, "fact", allow_extra=True)


def test_wrong_dtype_reports_both_types():
    df = _fact_row().with_columns(pl.col("qc_flags").cast(pl.Utf8))
    with pytest.raises(ContractError, match="qc_flags"):
        validate(df, FACT_V1, "fact")


def test_all_problems_reported_at_once():
    df = _fact_row().drop("obs").with_columns(pl.col("lead_hours").cast(pl.Int64))
    with pytest.raises(ContractError) as exc:
        validate(df, FACT_V1, "fact")
    msg = str(exc.value)
    assert "obs" in msg and "lead_hours" in msg


def test_require_non_null():
    df = _fact_row()
    require_non_null(df, ["station_id", "obs"], "fact")  # ok
    with pytest.raises(ContractError, match="repr_floor"):
        require_non_null(df, ["repr_floor"], "fact")


def test_qc_bits_are_frozen_powers_of_two():
    values = list(qc_bits.ALL_BITS.values())
    assert values == [1, 2, 4, 8, 16, 32]
    assert qc_bits.describe(qc_bits.RANGE | qc_bits.SPATIAL) == ["RANGE", "SPATIAL"]
    assert qc_bits.is_clean(0)
    assert not qc_bits.is_clean(qc_bits.DUPLICATE)
    # consumer-chosen rigor: mask out DUPLICATE, record counts as clean
    assert qc_bits.is_clean(qc_bits.DUPLICATE, mask=qc_bits.RANGE | qc_bits.SPATIAL)
