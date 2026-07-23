"""Golden tests for GRIB index parsing on realistic fixture excerpts."""

import pytest

from veritas_wx.ingest.forecasts.gribidx import (
    ECMWF_WANTED,
    GFS_WANTED,
    coalesce,
    http_range,
    parse_ecmwf_index,
    parse_gfs_idx,
    pick_gfs_apcp_bucket,
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


def test_gfs_duplicate_descriptor_deduped_first_wins():
    # field-observed NCEP quirk: identical APCP descriptor at two offsets
    dup = GFS_IDX + "8:5500000:d=2025070100:APCP:surface:0-6 hour acc fcst:\n"
    picked = select_gfs(parse_gfs_idx(dup))
    apcp = [e for e in picked if e.var == "APCP"]
    assert len(apcp) == 1
    assert apcp[0].start == 4200000  # first occurrence kept


GFS_IDX_F024 = """\
1:0:d=2025080100:TMP:2 m above ground:24 hour fcst:
2:1000000:d=2025080100:UGRD:10 m above ground:24 hour fcst:
3:2000000:d=2025080100:VGRD:10 m above ground:24 hour fcst:
4:3000000:d=2025080100:APCP:surface:0-24 hour acc fcst:
5:3800000:d=2025080100:APCP:surface:18-24 hour acc fcst:
6:4500000:d=2025080100:TMP:surface:24 hour fcst:
"""


def test_apcp_bucket_keeps_only_6h_window():
    """Field-observed (M5 run): historical f024 carries 0-24 AND 18-24 acc;
    both survive select_gfs (distinct metas) and the merged byte range makes
    the decoder see duplicate 'tp'. Only the 6-h bucket may pass."""
    picked = pick_gfs_apcp_bucket(select_gfs(parse_gfs_idx(GFS_IDX_F024)), lead_hours=24)
    apcp = [e for e in picked if e.var == "APCP"]
    assert len(apcp) == 1
    assert apcp[0].meta == "18-24 hour acc fcst"
    assert len(picked) == 4  # non-APCP fields untouched


def test_apcp_bucket_lead6_zero_to_six():
    picked = pick_gfs_apcp_bucket(select_gfs(parse_gfs_idx(GFS_IDX)), lead_hours=6)
    apcp = [e for e in picked if e.var == "APCP"]
    assert len(apcp) == 1 and apcp[0].meta == "0-6 hour acc fcst"


def test_apcp_bucket_missing_raises_never_falls_back():
    """A silent fallback to the 0-24 window would corrupt precip_24h sums."""
    entries = select_gfs(parse_gfs_idx(GFS_IDX_F024))
    only_total = [e for e in entries if e.meta != "18-24 hour acc fcst"]
    with pytest.raises(ValueError, match="no APCP 6-h bucket"):
        pick_gfs_apcp_bucket(only_total, lead_hours=24)


def test_apcp_bucket_no_apcp_passthrough():
    entries = [e for e in select_gfs(parse_gfs_idx(GFS_IDX)) if e.var != "APCP"]
    assert pick_gfs_apcp_bucket(entries, lead_hours=6) == entries


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
