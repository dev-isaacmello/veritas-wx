"""Golden tests for ONI and MJO RMM parsers on realistic fixture excerpts."""

import datetime as dt

import pytest

from veritas_wx.ingest.static.indices import parse_oni, parse_rmm

ONI_FIXTURE = """\
 SEAS    YR    TOTAL   ANOM
 DJF  1950   24.72  -1.53
 JFM  1950   25.17  -1.34
 NDJ  2023   28.28   1.95
"""


def test_oni_center_month_mapping_by_hand():
    df, dropped = parse_oni(ONI_FIXTURE)
    assert df.height == 3 and dropped["malformed_line"] == 0
    rows = {(r["year"], r["month"]): r["oni"] for r in df.to_dicts()}
    assert rows[(1950, 1)] == pytest.approx(-1.53)
    assert rows[(2023, 12)] == pytest.approx(1.95)


def test_oni_header_not_counted_as_drop():
    df, dropped = parse_oni(" SEAS    YR    TOTAL   ANOM\n")
    assert df.height == 0 and dropped["malformed_line"] == 0


RMM_FIXTURE = """\
RMM values up to real time
year month day RMM1 RMM2 phase amplitude. Missing Value= 1.E36
1974 6 1 1.63 0.11 5 1.63377 Final_value:_OLR_&_NCEP_winds
1974 6 2 1.05 0.42 5 1.13407 Final_value:_OLR_&_NCEP_winds
1978 3 17 1.E36 1.E36 999 1.E36 Missing_value:_untrustworthy_source_data
2025 7 1 -0.5 -0.3 6 0.58310 Prelim_value:_OLR_&_ACCESS_winds
"""


def test_rmm_parse_golden():
    df, dropped = parse_rmm(RMM_FIXTURE)
    assert df.height == 3
    assert dropped["missing_value"] == 1
    first = df.to_dicts()[0]
    assert first["date"] == dt.date(1974, 6, 1)
    assert first["phase"] == 5
    assert first["amplitude"] == pytest.approx(1.63377)


def test_rmm_amplitude_and_phase_types():
    df, _ = parse_rmm(RMM_FIXTURE)
    assert str(df.schema["phase"]) == "Int8"
    assert df.filter(df["date"] == dt.date(2025, 7, 1))["rmm1"][0] == pytest.approx(-0.5)


def test_empty_inputs():
    for parser in (parse_oni, parse_rmm):
        df, _ = parser("")
        assert df.height == 0
