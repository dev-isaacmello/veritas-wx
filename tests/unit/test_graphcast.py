"""GraphCast selective-read tests against a synthetic HDF5 mimicking the real
GRAP_v100_GFS layout (verified 2026-07-23): per-timestep chunks, apcp in
METERS, epoch-second time axis."""

import datetime as dt

import h5py
import numpy as np
import pytest

from veritas_wx.ingest.forecasts.graphcast import (
    N_STEPS,
    GraphCastRun,
    object_url,
)

INIT = dt.datetime(2025, 8, 1, 0, tzinfo=dt.UTC)
NLAT, NLON = 5, 8


@pytest.fixture
def run_file(tmp_path):
    """Synthetic run file at the real key layout under a local base dir."""
    path = tmp_path / "GRAP_v100_GFS/2025/0801/GRAP_v100_GFS_2025080100_f000_f240_06.nc"
    path.parent.mkdir(parents=True)
    times = np.array(
        [int((INIT + dt.timedelta(hours=6 * i)).timestamp()) for i in range(N_STEPS)],
        dtype=np.int64,
    )
    with h5py.File(path, "w") as h5:
        h5.create_dataset("time", data=times)
        h5.create_dataset("latitude", data=np.linspace(90, -90, NLAT, dtype=np.float32))
        h5.create_dataset("longitude", data=np.linspace(0, 359, NLON, dtype=np.float32))
        for name, units in (("t2", "K"), ("u10", "m s-1"), ("v10", "m s-1"), ("apcp", "m")):
            # value == step index everywhere: lets tests verify WHICH chunk was read
            data = np.tile(
                np.arange(N_STEPS, dtype=np.float32)[:, None, None], (1, NLAT, NLON)
            )
            ds = h5.create_dataset(name, data=data, chunks=(1, NLAT, NLON))
            ds.attrs["units"] = units
    return tmp_path


def test_object_url_matches_audited_layout():
    url = object_url(INIT)
    assert url == (
        "https://noaa-oar-mlwp-data.s3.amazonaws.com/GRAP_v100_GFS/2025/0801/"
        "GRAP_v100_GFS_2025080100_f000_f240_06.nc"
    )


def test_fields_at_lead_reads_correct_step_and_names(run_file):
    run = GraphCastRun(INIT, base=str(run_file))
    fields = {f.short_name: f for f in run.fields_at_lead(24)}
    assert set(fields) == {"2t", "10u", "10v", "tp"}
    # step index 24/6 == 4 -> every value must be 4.0
    assert float(fields["2t"].values[0, 0]) == 4.0
    assert fields["tp"].units == "m"  # extract converts by units, module must not
    assert fields["2t"].units == "K"
    run.close()


def test_time_axis_mismatch_raises(run_file):
    run = GraphCastRun(INIT, base=str(run_file))
    run._times = run._times + 3600  # simulate a shifted/wrong file
    with pytest.raises(ValueError, match="time axis mismatch"):
        run.fields_at_lead(6)
    run.close()


def test_bad_lead_raises(run_file):
    run = GraphCastRun(INIT, base=str(run_file))
    with pytest.raises(ValueError, match="lead must be"):
        run.fields_at_lead(7)
    with pytest.raises(ValueError, match="lead must be"):
        run.fields_at_lead(246)
    run.close()


def test_wrong_units_refused(tmp_path, run_file):
    path = (
        run_file / "GRAP_v100_GFS/2025/0801/GRAP_v100_GFS_2025080100_f000_f240_06.nc"
    )
    with h5py.File(path, "r+") as h5:
        h5["apcp"].attrs["units"] = "kg m-2"  # a silent mm-vs-m mixup would be 1000x
    with pytest.raises(ValueError, match="refusing to guess"):
        GraphCastRun(INIT, base=str(run_file))
