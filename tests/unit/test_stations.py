"""Unit tests for station curation v0 (T3) — no network, hand-built fixtures."""

import polars as pl
import pytest

from veritas_wx.contracts.schemas import STATIONS_V1
from veritas_wx.contracts.validate import validate
from veritas_wx.ingest.static.stations import (
    count_cells_with_min_stations,
    dedupe_cross_network,
    exclude_network_phase1,
    flag_elev_review,
    flag_out_of_bbox,
    haversine_km,
    inmet_to_canonical,
    parse_isd_history,
    to_canonical,
)

ONE_DEG_LAT_KM = 111.19


def station(
    station_id: str,
    lat: float | None,
    lon: float | None,
    *,
    network: str | None = None,
    elev_station: float | None = None,
    elev_dem: float | None = None,
    status: str = "included",
    exclusion_reason: str | None = None,
) -> dict:
    return {
        "station_id": station_id,
        "network": network or station_id.split(":", 1)[0],
        "native_id": station_id.split(":", 1)[1],
        "name": station_id,
        "uf": None,
        "lat": lat,
        "lon": lon,
        "elev_station": elev_station,
        "elev_dem": elev_dem,
        "koppen": None,
        "cross_ref": None,
        "status": status,
        "exclusion_reason": exclusion_reason,
        "source_meta": "fixture",
        "ingest_version": "0.1.0+test.deadbeef",
    }


def frame(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=STATIONS_V1)


def row(df: pl.DataFrame, station_id: str) -> dict:
    sub = df.filter(pl.col("station_id") == station_id)
    assert sub.height == 1, f"expected exactly one row for {station_id}"
    return sub.row(0, named=True)




def test_haversine_golden_one_degree_latitude():
    assert haversine_km(0.0, 0.0, 1.0, 0.0) == pytest.approx(ONE_DEG_LAT_KM, abs=0.05)
    assert haversine_km(0.0, 0.0, 0.0, 1.0) == pytest.approx(ONE_DEG_LAT_KM, abs=0.05)


def test_haversine_zero_and_symmetry():
    assert haversine_km(-15.79, -47.93, -15.79, -47.93) == 0.0
    d_ab = haversine_km(-15.0, -48.0, -16.0, -49.0)
    d_ba = haversine_km(-16.0, -49.0, -15.0, -48.0)
    assert d_ab == pytest.approx(d_ba, rel=1e-12)




def test_dedupe_within_2km_inmet_wins_and_cross_ref_both_ways():
    one_km_in_deg = 1.0 / 111.1949
    df = frame(
        [
            station("inmet:A001", -15.0, -48.0),
            station("isd:829830-99999", -15.0 + one_km_in_deg, -48.0),
        ]
    )
    out = dedupe_cross_network(df, max_km=2.0)

    assert out.height == df.height
    inmet = row(out, "inmet:A001")
    isd = row(out, "isd:829830-99999")

    assert inmet["status"] == "included"
    assert inmet["cross_ref"] == "isd:829830-99999"
    assert inmet["exclusion_reason"] is None

    assert isd["status"] == "excluded"
    assert isd["exclusion_reason"] == "duplicate_of:inmet:A001"
    assert isd["cross_ref"] == "inmet:A001"


def test_dedupe_beyond_2km_no_change():
    ten_km_in_deg = 10.0 / 111.1949
    df = frame(
        [
            station("inmet:A001", -15.0, -48.0),
            station("isd:829830-99999", -15.0 + ten_km_in_deg, -48.0),
        ]
    )
    out = dedupe_cross_network(df, max_km=2.0)

    assert row(out, "inmet:A001")["status"] == "included"
    isd = row(out, "isd:829830-99999")
    assert isd["status"] == "included"
    assert isd["cross_ref"] is None
    assert isd["exclusion_reason"] is None


def test_dedupe_ignores_already_excluded_counterparts():
    one_km_in_deg = 1.0 / 111.1949
    df = frame(
        [
            station(
                "inmet:A002", -20.0, -50.0, status="excluded", exclusion_reason="inactive"
            ),
            station("isd:820000-99999", -20.0 + one_km_in_deg, -50.0),
        ]
    )
    out = dedupe_cross_network(df, max_km=2.0)
    assert row(out, "isd:820000-99999")["status"] == "included"
    assert row(out, "inmet:A002")["exclusion_reason"] == "inactive"




def test_count_cells_two_of_three_stations_share_a_cell():
    df = frame(
        [
            station("inmet:A001", -15.05, -47.95),
            station("inmet:A002", -15.20, -47.90),
            station("isd:820000-99999", -10.0, -40.0),
        ]
    )
    assert count_cells_with_min_stations(df, res=0.25, min_n=2) == 1


def test_count_cells_excluded_stations_do_not_count():
    df = frame(
        [
            station("inmet:A001", -15.05, -47.95),
            station(
                "inmet:A002", -15.20, -47.90, status="excluded", exclusion_reason="inactive"
            ),
        ]
    )
    assert count_cells_with_min_stations(df, res=0.25, min_n=2) == 0




def test_bbox_rule_excludes_outside_and_keeps_inside():
    df = frame(
        [
            station("inmet:A001", -15.0, -48.0),
            station("inmet:A899", -35.5, -55.0),
            station("isd:999999-11111", 0.92, -29.35),
        ]
    )
    out = flag_out_of_bbox(df)

    assert row(out, "inmet:A001")["status"] == "included"
    south = row(out, "inmet:A899")
    assert (south["status"], south["exclusion_reason"]) == ("excluded", "coords_out_of_brazil")
    spsp = row(out, "isd:999999-11111")
    assert (spsp["status"], spsp["exclusion_reason"]) == ("excluded", "coords_out_of_brazil")


def test_bbox_rule_null_coords_and_existing_exclusions():
    df = frame(
        [
            station("inmet:A777", None, None),
            station(
                "inmet:A778", -35.5, -55.0, status="excluded", exclusion_reason="inactive"
            ),
        ]
    )
    out = flag_out_of_bbox(df)
    nul = row(out, "inmet:A777")
    assert (nul["status"], nul["exclusion_reason"]) == ("excluded", "invalid_coords")
    assert row(out, "inmet:A778")["exclusion_reason"] == "inactive"




def test_exclude_network_phase1_benches_isd_keeps_inmet():
    df = frame(
        [
            station("inmet:A001", -15.0, -48.0),
            station("isd:820980-99999", -1.4, -48.4),
            station("isd:829830-99999", -23.0, -47.1, status="review"),
            station(
                "isd:834840-99999", -35.5, -55.0, status="excluded", exclusion_reason="inactive"
            ),
        ]
    )
    out = exclude_network_phase1(df, "isd", "isd_archive_frozen")

    assert row(out, "inmet:A001")["status"] == "included"
    benched = row(out, "isd:820980-99999")
    assert (benched["status"], benched["exclusion_reason"]) == ("excluded", "isd_archive_frozen")
    review = row(out, "isd:829830-99999")
    assert (review["status"], review["exclusion_reason"]) == ("excluded", "isd_archive_frozen")
    assert row(out, "isd:834840-99999")["exclusion_reason"] == "inactive"




def test_elev_mismatch_over_100m_goes_to_review():
    df = frame(
        [
            station("inmet:A001", -15.0, -48.0, elev_station=900.0, elev_dem=750.0),
            station("inmet:A002", -16.0, -49.0, elev_station=950.0, elev_dem=900.0),
            station("inmet:A003", -17.0, -50.0, elev_station=500.0, elev_dem=None),
        ]
    )
    out = flag_elev_review(df, max_diff_m=100.0)

    reviewed = row(out, "inmet:A001")
    assert reviewed["status"] == "review"
    assert reviewed["exclusion_reason"] == "elev_diff_gt_100m"
    assert row(out, "inmet:A002")["status"] == "included"
    assert row(out, "inmet:A003")["status"] == "included"


def test_elev_diff_exactly_at_threshold_is_not_review():
    df = frame([station("inmet:A004", -15.0, -48.0, elev_station=850.0, elev_dem=750.0)])
    assert row(flag_elev_review(df, max_diff_m=100.0), "inmet:A004")["status"] == "included"




INMET_FIXTURE = [
    {
        "CD_ESTACAO": "A001",
        "DC_NOME": "BRASILIA",
        "SG_ESTADO": "DF",
        "VL_LATITUDE": "-15.78944444",
        "VL_LONGITUDE": "-47.92583333",
        "VL_ALTITUDE": "1160.96",
        "DT_INICIO_OPERACAO": "2000-05-07T21:00:00.000-03:00",
        "DT_FIM_OPERACAO": None,
        "CD_SITUACAO": "Operante",
    },
    {
        "CD_ESTACAO": "A999",
        "DC_NOME": "FANTASMA",
        "SG_ESTADO": "RS",
        "VL_LATITUDE": "-30.0",
        "VL_LONGITUDE": "-51.0",
        "VL_ALTITUDE": None,
        "DT_INICIO_OPERACAO": "2001-01-01T21:00:00.000-03:00",
        "DT_FIM_OPERACAO": None,
        "CD_SITUACAO": "Desativada",
    },
    {
        "CD_ESTACAO": "A941",
        "DC_NOME": "ENCERRADA-EM-2021",
        "SG_ESTADO": "RS",
        "VL_LATITUDE": "-29.0",
        "VL_LONGITUDE": "-52.0",
        "VL_ALTITUDE": "100.0",
        "DT_INICIO_OPERACAO": "2001-01-01T21:00:00.000-03:00",
        "DT_FIM_OPERACAO": "2021-01-07T21:00:00.000-03:00",
        "CD_SITUACAO": "Operante",
    },
]


def test_inmet_canonical_inactive_rule_and_null_elevation():
    df = inmet_to_canonical(
        INMET_FIXTURE, ingest_version="0.1.0+test.deadbeef", inactive_end_cutoff="2025-07-01"
    )
    validate(df, STATIONS_V1, "STATIONS_V1")

    operante = row(df, "inmet:A001")
    assert operante["status"] == "included"
    assert operante["elev_station"] == pytest.approx(1160.96)
    assert operante["uf"] == "DF"

    desativada = row(df, "inmet:A999")
    assert (desativada["status"], desativada["exclusion_reason"]) == ("excluded", "inactive")
    assert desativada["elev_station"] is None

    ended = row(df, "inmet:A941")
    assert (ended["status"], ended["exclusion_reason"]) == ("excluded", "inactive")


ISD_CSV_FIXTURE = (
    '"USAF","WBAN","STATION NAME","CTRY","STATE","ICAO","LAT","LON","ELEV(M)","BEGIN","END"\n'
    '"829830","99999","MANAUS","BR","","SBMN","-03.033","-060.050","+0061.0","19450101","20260115"\n'
    '"820000","99999","VELHA","BR","","","-10.000","-050.000","-0999.0","19450101","20190101"\n'
    '"720534","00161","DENVER","US","CO","KBJC","+39.950","-105.117","+1724.0","20050101","20260115"\n'
    '"829999","99999","SEM COORD","BR","","","","","+0010.0","19450101","20260115"\n'
)


def test_parse_isd_history_filters_and_accounts_every_row():
    df, dropped = parse_isd_history(ISD_CSV_FIXTURE, min_end="20250701")

    assert df.height == 1
    assert dropped == {"not_country": 1, "end_before_min": 1, "invalid_coords": 1}
    assert df.height + sum(dropped.values()) == 4

    canon = to_canonical([], df, ingest_version="0.1.0+test.deadbeef")
    validate(canon, STATIONS_V1, "STATIONS_V1")
    manaus = row(canon, "isd:829830-99999")
    assert manaus["native_id"] == "829830-99999"
    assert manaus["lat"] == pytest.approx(-3.033)
    assert manaus["elev_station"] == pytest.approx(61.0)
    assert manaus["uf"] is None


def test_isd_elevation_sentinel_becomes_null():
    df, _ = parse_isd_history(ISD_CSV_FIXTURE, min_end="20180101")
    canon = to_canonical([], df, ingest_version="0.1.0+test.deadbeef")
    assert row(canon, "isd:820000-99999")["elev_station"] is None
