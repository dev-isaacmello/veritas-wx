"""Golden tests for observation parsers: INMET payloads and ISD-Lite lines.

Every parser satisfies the reconciliation identity so runlog can enforce it:
    potential_rows == emitted + sum(dropped)
"""

import datetime as dt

import polars as pl
import pytest

from veritas_wx.contracts import OBS_V1, validate
from veritas_wx.ingest.observations.inmet import VAR_MAP, rows_from_payload
from veritas_wx.ingest.observations.isd import rows_from_isd_lite

UTC = dt.UTC


# ---------------------------------------------------------------- INMET

def test_inmet_payload_golden():
    payload = [
        {
            "DT_MEDICAO": "2025-08-01",
            "HR_MEDICAO": "1400",
            "TEM_INS": "25.0",  # 25.0 C -> 298.15 K by hand
            "VEN_VEL": "3.2",
            "CHUVA": "1.4",
        }
    ]
    df, dropped = rows_from_payload(payload, "inmet:A001", "test")
    validate(df, OBS_V1, "obs")
    assert df.height == 3 and sum(dropped.values()) == 0

    by_var = {r["variable"]: r for r in df.to_dicts()}
    assert by_var["t2m"]["value"] == pytest.approx(298.15)
    assert by_var["wind10m"]["value"] == pytest.approx(3.2)
    assert by_var["precip_1h"]["value"] == pytest.approx(1.4)
    assert by_var["t2m"]["valid_time"] == dt.datetime(2025, 8, 1, 14, tzinfo=UTC)
    assert by_var["t2m"]["source"] == "inmet"


def test_inmet_missing_value_is_dropped_and_counted_never_zeroed():
    payload = [
        {"DT_MEDICAO": "2025-08-01", "HR_MEDICAO": "0000",
         "TEM_INS": "20.0", "VEN_VEL": None, "CHUVA": ""},
    ]
    df, dropped = rows_from_payload(payload, "inmet:A001", "test")
    assert df.height == 1  # only t2m
    assert dropped["value_missing"] == 2
    assert 0.0 not in df["value"].to_list()  # missing NEVER becomes zero


def test_inmet_bad_timestamp_drops_whole_record():
    payload = [{"DT_MEDICAO": "not-a-date", "HR_MEDICAO": "9x", "TEM_INS": "20.0"}]
    df, dropped = rows_from_payload(payload, "inmet:A001", "test")
    assert df.height == 0 and dropped["bad_timestamp"] == len(VAR_MAP)


def test_inmet_reconciliation_identity():
    payload = [
        {"DT_MEDICAO": "2025-08-01", "HR_MEDICAO": "0000",
         "TEM_INS": "20.0", "VEN_VEL": "1.0", "CHUVA": "0.0"},
        {"DT_MEDICAO": "2025-08-01", "HR_MEDICAO": "0100",
         "TEM_INS": None, "VEN_VEL": "abc", "CHUVA": "2.0"},
        {"DT_MEDICAO": "bad", "HR_MEDICAO": "0200", "TEM_INS": "20.0"},
    ]
    df, dropped = rows_from_payload(payload, "inmet:A001", "test")
    assert len(payload) * len(VAR_MAP) == df.height + sum(dropped.values())


# ---------------------------------------------------------------- ISD-Lite

ISD_SAMPLE = """\
2025 07 01 12  215  180 10132 250   31  8   -1 -9999
2025 07 01 13 -9999  175 10130 240   28  8    5 -9999
"""


def test_isd_lite_golden():
    df, dropped = rows_from_isd_lite(ISD_SAMPLE, "isd:829830-99999", "test")
    validate(df, OBS_V1, "obs")

    h12 = {r["variable"]: r for r in df.filter(
        pl.col("valid_time") == dt.datetime(2025, 7, 1, 12, tzinfo=UTC)).to_dicts()}
    # 215 -> 21.5 C -> 294.65 K by hand; 31 -> 3.1 m/s; -1 = trace -> 0.0 mm flagged 'T'
    assert h12["t2m"]["value"] == pytest.approx(294.65)
    assert h12["wind10m"]["value"] == pytest.approx(3.1)
    assert h12["precip_1h"]["value"] == 0.0
    assert h12["precip_1h"]["source_qc_raw"] == "T"

    h13 = {r["variable"]: r for r in df.filter(
        pl.col("valid_time") == dt.datetime(2025, 7, 1, 13, tzinfo=UTC)).to_dicts()}
    assert "t2m" not in h13  # -9999 -> missing, no row
    assert h13["precip_1h"]["value"] == pytest.approx(0.5)  # 5 -> 0.5 mm
    assert dropped["value_missing"] == 1  # exactly the missing t2m


def test_isd_lite_reconciliation_identity():
    text = ISD_SAMPLE + "garbage line\n"
    df, dropped = rows_from_isd_lite(text, "isd:X", "test")
    parsed_lines = 3  # 2 valid + 1 malformed
    assert parsed_lines * 3 == df.height + sum(dropped.values())


def test_isd_lite_empty_input():
    df, dropped = rows_from_isd_lite("", "isd:X", "test")
    assert df.height == 0 and sum(dropped.values()) == 0
    validate(df, OBS_V1, "obs")
