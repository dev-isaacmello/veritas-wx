"""The reconciliation identity is a hard guard, not a warning (risk R9)."""

import io
import json

import pytest

from veritas_wx.runlog import ReconciliationError, log_stage


def test_balanced_stage_passes_and_emits_json():
    buf = io.StringIO()
    rec = log_stage("qc.range", rows_in=100, rows_out=100, dropped={}, stream=buf, model="gfs")
    parsed = json.loads(buf.getvalue())
    assert parsed["stage"] == "qc.range"
    assert parsed["model"] == "gfs"
    assert rec["rows_in"] == 100


def test_itemized_drops_reconcile():
    rec = log_stage(
        "match.fact",
        rows_in=1000,
        rows_out=870,
        dropped={"delta_z_excedido": 100, "precip_incompleta": 30},
        stream=io.StringIO(),
    )
    assert sum(rec["dropped"].values()) == 130


def test_silent_loss_raises():
    with pytest.raises(ReconciliationError, match="match.fact"):
        log_stage("match.fact", rows_in=1000, rows_out=990, dropped={}, stream=io.StringIO())


def test_double_counting_raises():
    with pytest.raises(ReconciliationError):
        log_stage("x", rows_in=10, rows_out=10, dropped={"dup": 5}, stream=io.StringIO())


def test_negative_counts_rejected():
    with pytest.raises(ValueError):
        log_stage("x", rows_in=-1, rows_out=0, stream=io.StringIO())
    with pytest.raises(ValueError):
        log_stage("x", rows_in=1, rows_out=0, dropped={"y": -1}, stream=io.StringIO())
