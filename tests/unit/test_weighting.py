"""Station-density weights (Rodwell 2010 eq. 22-23) and weighted metric stats.

Golden geometry: five stations strung 0.02 degrees (~2.2 km) apart in
latitude — every pairwise distance under 10 km, so each sees density ~5 with
alpha_0 = 0.75 degrees (~83 km) — plus one station 5 degrees (~556 km) away
whose kernel contribution to/from the cluster is exp(-(5/0.75)^2) ~= 0.
Unnormalized weights are then ~[0.2 x5, 1.0]; normalizing to mean 1 gives
cluster ~0.6 each and isolated ~3.0.
"""

import math
from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from veritas_wx.analyze.bootstrap import BootstrapResult
from veritas_wx.analyze.metrics.core import bias_stat, mae, mae_stat, rmse_stat
from veritas_wx.analyze.weighting import station_density_weights

CLUSTER_IDS = [f"c{i}" for i in range(5)]


def _cluster_plus_isolated() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "station_id": [*CLUSTER_IDS, "lone"],
            "lat": [-30.0 - 0.02 * i for i in range(5)] + [-35.0],
            "lon": [-51.0] * 6,
        }
    )


def test_isolated_station_outweighs_dense_cluster():
    out = station_density_weights(_cluster_plus_isolated())
    assert out.columns == ["station_id", "weight"]
    by_id = dict(zip(out["station_id"].to_list(), out["weight"].to_list(), strict=True))
    lone = by_id.pop("lone")
    assert lone == pytest.approx(3.0, rel=0.02)
    for w in by_id.values():
        assert w == pytest.approx(0.6, rel=0.02)
        assert lone > 4.0 * w


def test_weights_are_normalized_to_mean_one():
    rng = np.random.default_rng(0)
    scatter = pl.DataFrame(
        {
            "station_id": [f"s{i}" for i in range(40)],
            "lat": rng.uniform(-33.0, -5.0, 40),
            "lon": rng.uniform(-60.0, -40.0, 40),
        }
    )
    out = station_density_weights(scatter)
    assert out["weight"].mean() == pytest.approx(1.0)
    assert (out["weight"] > 0.0).all()


def test_single_station_gets_weight_one():
    one = pl.DataFrame({"station_id": ["a"], "lat": [-30.0], "lon": [-51.0]})
    assert station_density_weights(one)["weight"].item() == pytest.approx(1.0)


def test_empty_input_yields_empty_typed_frame():
    out = station_density_weights(
        pl.DataFrame(schema={"station_id": pl.Utf8, "lat": pl.Float64, "lon": pl.Float64})
    )
    assert out.height == 0
    assert out.schema == {"station_id": pl.Utf8, "weight": pl.Float64}


def test_duplicate_and_null_inputs_raise():
    dup = pl.DataFrame({"station_id": ["a", "a"], "lat": [-30.0, -31.0], "lon": [-51.0, -51.0]})
    with pytest.raises(ValueError, match="duplicated station_id"):
        station_density_weights(dup)
    nulls = pl.DataFrame({"station_id": ["a", "b"], "lat": [-30.0, None], "lon": [-51.0, -51.0]})
    with pytest.raises(ValueError, match="null lat/lon"):
        station_density_weights(nulls)
    with pytest.raises(ValueError, match="alpha_0_degrees"):
        station_density_weights(dup.unique("station_id"), alpha_0_degrees=0.0)


def test_weighted_stats_golden_by_hand():
    """errors [2, 2, 0] with weights [1, 1, 4]:

    unweighted: mae = bias = 4/3, rmse = sqrt(8/3)
    weighted:   mae = bias = (2 + 2 + 0) / 6 = 2/3, rmse = sqrt(8/6)
    """
    plain = pl.DataFrame({"fcst": [3.0, 3.0, 1.0], "obs": [1.0, 1.0, 1.0]})
    weighted = plain.with_columns(pl.Series("weight", [1.0, 1.0, 4.0]))
    assert mae_stat(plain) == pytest.approx(4.0 / 3.0)
    assert bias_stat(plain) == pytest.approx(4.0 / 3.0)
    assert rmse_stat(plain) == pytest.approx(math.sqrt(8.0 / 3.0))
    assert mae_stat(weighted) == pytest.approx(2.0 / 3.0)
    assert bias_stat(weighted) == pytest.approx(2.0 / 3.0)
    assert rmse_stat(weighted) == pytest.approx(math.sqrt(8.0 / 6.0))


def test_density_weights_shift_metric_toward_isolated_station():
    """Cluster stations score error 0, the isolated one error 1.

    Unweighted mae = 1/6; joining the density weights moves it to
    ~3.0 * 1 / 6 = 0.5 — the Southeast-style cluster no longer drowns out
    the lone station.
    """
    weights = station_density_weights(_cluster_plus_isolated())
    pairs = pl.DataFrame(
        {
            "station_id": [*CLUSTER_IDS, "lone"],
            "fcst": [10.0] * 5 + [11.0],
            "obs": [10.0] * 6,
        }
    )
    unweighted = mae_stat(pairs)
    weighted = mae_stat(pairs.join(weights, on="station_id", how="left"))
    assert unweighted == pytest.approx(1.0 / 6.0)
    assert weighted == pytest.approx(0.5, rel=0.02)
    assert weighted > unweighted


def test_public_metric_bootstraps_the_weighted_statistic():
    """The weight column rides through the day-block resampler untouched."""
    n_days = 12
    days = [date(2025, 7, 1) + timedelta(days=i) for i in range(n_days)]
    pairs = pl.concat(
        [
            pl.DataFrame(
                {
                    "station_id": [sid] * n_days,
                    "day": days,
                    "fcst": [10.0 + err] * n_days,
                    "obs": [10.0] * n_days,
                    "weight": [w] * n_days,
                }
            )
            for sid, err, w in (("a", 2.0, 1.0), ("b", 0.0, 3.0))
        ]
    )
    res = mae(pairs, rng=np.random.default_rng(1), n_boot=50, block_len=2)
    assert isinstance(res, BootstrapResult)
    assert res.estimate == pytest.approx(mae_stat(pairs)) == pytest.approx(0.5)
    assert res.ci_low <= res.estimate <= res.ci_high
