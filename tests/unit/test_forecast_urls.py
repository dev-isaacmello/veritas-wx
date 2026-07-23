"""Golden tests for forecast source URL construction (config-audited layouts)."""

import datetime as dt

from veritas_wx.ingest.forecasts import ecmwf_opendata, gfs

INIT = dt.datetime(2025, 7, 1, 12, tzinfo=dt.UTC)


def test_gfs_urls_by_hand():
    assert (
        gfs.object_key(INIT, 6)
        == "gfs.20250701/12/atmos/gfs.t12z.pgrb2.0p25.f006"
    )
    assert gfs.grib_url(INIT, 240).endswith("gfs.t12z.pgrb2.0p25.f240")
    assert gfs.idx_url(INIT, 6).endswith(".idx")


def test_gfs_lead_zero_padding():
    assert gfs.object_key(INIT, 24).endswith("f024")
    assert gfs.object_key(INIT, 6).endswith("f006")


def test_ecmwf_urls_by_hand():
    assert (
        ecmwf_opendata.object_key(INIT, 6, "hres")
        == "20250701/12z/ifs/0p25/oper/20250701120000-6h-oper-fc.grib2"
    )
    assert (
        ecmwf_opendata.object_key(INIT, 24, "aifs")
        == "20250701/12z/aifs-single/0p25/oper/20250701120000-24h-oper-fc.grib2"
    )
    assert ecmwf_opendata.index_url(INIT, 6, "hres").endswith(
        "20250701120000-6h-oper-fc.index"
    )


def test_ecmwf_unknown_model_raises():
    import pytest

    with pytest.raises(KeyError):
        ecmwf_opendata.object_key(INIT, 6, "gfs")
