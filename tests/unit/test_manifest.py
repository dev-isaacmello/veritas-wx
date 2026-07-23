"""Manifest: checksums, idempotency, atomic append."""

import datetime as dt

from veritas_wx.ingest import manifest


def test_sha256_known_vector(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"abc")
    assert (
        manifest.sha256_of(p)
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_append_load_roundtrip_and_idempotency(tmp_path):
    path = tmp_path / "manifest.parquet"
    assert manifest.load(path).height == 0

    r = manifest.row(
        url="s3://bucket/x.grib2",
        local_path=tmp_path / "x.grib2",
        sha256="deadbeef",
        size_bytes=123,
        source="s3://bucket",
        model="gfs",
        init_time=dt.datetime(2025, 7, 1, tzinfo=dt.UTC),
    )
    df = manifest.append(path, [r])
    assert df.height == 1

    loaded = manifest.load(path)
    assert manifest.is_fetched(loaded, "s3://bucket/x.grib2")
    assert not manifest.is_fetched(loaded, "s3://bucket/y.grib2")


def test_pruned_still_counts_as_fetched(tmp_path):
    path = tmp_path / "manifest.parquet"
    r = manifest.row(
        url="s3://b/z", local_path=None, sha256="ff", size_bytes=1,
        source="s3://b", model=None, init_time=None, status="pruned",
    )
    df = manifest.append(path, [r])
    assert manifest.is_fetched(df, "s3://b/z")
