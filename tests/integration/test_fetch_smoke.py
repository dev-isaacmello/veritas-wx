"""Live smoke tests against the real buckets (marked network; excluded in CI).

These validate the layout/shortName assumptions on RECENT data; the T2 audit
covers the historical window. Failures here mean the fetchers' assumptions
are wrong NOW — stop and fix before any volume download.
"""

import datetime as dt

import httpx
import numpy as np
import pytest

from veritas_wx.ingest.forecasts import ecmwf_opendata, gfs
from veritas_wx.match.extract import decode_messages

pytestmark = pytest.mark.network


def _recent_init(days_back: int = 2) -> dt.datetime:
    d = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=days_back)
    return dt.datetime(d.year, d.month, d.day, 0, tzinfo=dt.UTC)


@pytest.fixture(scope="module")
def client() -> httpx.Client:
    with httpx.Client(follow_redirects=True) as c:
        yield c


def test_gfs_fetch_and_decode_smoke(client):
    blob, selected = gfs.fetch_fields(client, _recent_init(), 6)
    assert len(selected) == 4
    fields = decode_messages(blob)
    names = {f.short_name for f in fields}
    assert len(fields) == 4, f"expected 4 fields, got {names}"
    t2 = next(f for f in fields if f.short_name in ("2t", "t"))
    assert 200.0 < float(np.nanmean(t2.values)) < 330.0
    assert t2.values.shape == (721, 1440)


def test_ecmwf_hres_fetch_and_decode_smoke(client):
    blob, selected = ecmwf_opendata.fetch_fields(client, _recent_init(), 6, "hres")
    fields = decode_messages(blob)
    names = {f.short_name for f in fields}
    assert {"2t", "10u", "10v", "tp"} <= names, f"got {names}"
    t2 = next(f for f in fields if f.short_name == "2t")
    assert 200.0 < float(np.nanmean(t2.values)) < 330.0


def test_aifs_fetch_and_decode_smoke(client):
    blob, selected = ecmwf_opendata.fetch_fields(client, _recent_init(), 6, "aifs")
    fields = decode_messages(blob)
    names = {f.short_name for f in fields}
    assert {"2t", "10u", "10v", "tp"} <= names, f"got {names}"
