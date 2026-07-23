"""Golden tests for GRIB index parsing on realistic fixture excerpts."""

from veritas_wx.ingest.forecasts.gribidx import (
    ECMWF_WANTED,
    GFS_WANTED,
    coalesce,
    http_range,
    parse_ecmwf_index,
    parse_gfs_idx,
    select_ecmwf,
    select_gfs,
)

GFS_IDX = """\
1:0:d=2025070100:PRMSL:mean sea level:6 hour fcst:
2:990253:d=2025070100:CLMR:1 hybrid level:6 hour fcst:
3:1020000:d=2025070100:TMP:2 m above ground:6 hour fcst:
4:2050000:d=2025070100:UGRD:10 m above ground:6 hour fcst:
5:3100000:d=2025070100:VGRD:10 m above ground:6 hour fcst:
6:4200000:d=2025070100:APCP:surface:0-6 hour acc fcst:
7:4900000:d=2025070100:TMP:surface:6 hour fcst:
"""


def test_gfs_idx_parse_and_ranges():
    entries = parse_gfs_idx(GFS_IDX)
    assert len(entries) == 7
    # stop of message N is start of message N+1
    assert entries[0].start == 0 and entries[0].stop == 990253
    assert entries[-1].stop is None  # last message runs to EOF


def test_gfs_select_wanted_fields_only():
    picked = select_gfs(parse_gfs_idx(GFS_IDX))
    assert [(e.var, e.level) for e in picked] == [
        ("TMP", "2 m above ground"),
        ("UGRD", "10 m above ground"),
        ("VGRD", "10 m above ground"),
        ("APCP", "surface"),
    ]
    # surface TMP (skin temperature) must NOT be picked despite var name match
    assert all(not (e.var == "TMP" and e.level == "surface") for e in picked)


def test_http_range_header_is_inclusive():
    entries = parse_gfs_idx(GFS_IDX)
    assert http_range(entries[2]) == "bytes=1020000-2049999"
    assert http_range(entries[-1]) == "bytes=4900000-"


def test_coalesce_merges_contiguous_messages():
    picked = select_gfs(parse_gfs_idx(GFS_IDX))
    merged = coalesce(picked)
    # TMP/UGRD/VGRD/APCP are contiguous (1020000..4900000) -> single range
    assert merged == [(1020000, 4900000)]


ECMWF_INDEX = (
    '{"domain": "g", "date": "20250701", "time": "0000", "step": "6", "levtype": "sfc", '
    '"param": "2t", "_offset": 1000, "_length": 500}\n'
    '{"domain": "g", "date": "20250701", "time": "0000", "step": "6", "levtype": "sfc", '
    '"param": "msl", "_offset": 1500, "_length": 400}\n'
    '{"domain": "g", "date": "20250701", "time": "0000", "step": "6", "levtype": "sfc", '
    '"param": "tp", "_offset": 1900, "_length": 300}\n'
    '{"domain": "g", "date": "20250701", "time": "0000", "step": "12", "levtype": "sfc", '
    '"param": "2t", "_offset": 9000, "_length": 500}\n'
)


def test_ecmwf_index_parse_and_step_selection():
    entries = parse_ecmwf_index(ECMWF_INDEX)
    assert len(entries) == 4
    step6 = select_ecmwf(entries, step=6)
    assert sorted(e.var for e in step6) == ["2t", "tp"]  # msl unwanted; step 12 excluded
    t2 = next(e for e in step6 if e.var == "2t")
    assert (t2.start, t2.stop) == (1000, 1500)
    assert http_range(t2) == "bytes=1000-1499"


def test_wanted_sets_cover_phase1_variables():
    # t2m, wind (u+v), precip — the Phase 1 contract
    assert {v for v, _ in GFS_WANTED} == {"TMP", "UGRD", "VGRD", "APCP"}
    assert ECMWF_WANTED == {"2t", "10u", "10v", "tp"}
