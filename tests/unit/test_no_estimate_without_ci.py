"""Guardian of non-negotiable #3: no public estimate without a confidence interval.

Introspects ``veritas_wx.analyze.metrics.core``: EVERY public function (name
not starting with ``_``, excluding the ``*_stat`` bootstrap building blocks)
must return a ``BootstrapResult`` or a DataFrame carrying ``ci_low``/
``ci_high`` columns — checked both by return annotation and by actually
calling each function on a synthetic matched-pairs frame.

A new public function added to the module without an interval-bearing return
type fails this test automatically.
"""

import inspect
from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

import veritas_wx.analyze.metrics.core as core
from veritas_wx.analyze.bootstrap import BootstrapResult
from veritas_wx.analyze.decompose import representativeness_decomposition


def _public_functions() -> dict[str, object]:
    fns = {}
    for name, obj in inspect.getmembers(core, inspect.isfunction):
        if obj.__module__ != core.__name__:
            continue
        if name.startswith("_") or name.endswith("_stat"):
            continue
        fns[name] = obj
    return fns


@pytest.fixture()
def pairs() -> pl.DataFrame:
    rng = np.random.default_rng(0)
    n_days = 15
    days = [date(2025, 7, 1) + timedelta(days=i) for i in range(n_days)]
    frames = []
    for sid in ("sta_a", "sta_b"):
        obs = rng.normal(290.0, 5.0, n_days)
        frames.append(
            pl.DataFrame(
                {
                    "station_id": [sid] * n_days,
                    "day": days,
                    "obs": obs,
                    "fcst": obs + rng.normal(0.5, 1.0, n_days),
                    "obs_pct": np.linspace(1.0, 100.0, n_days),
                    "repr_floor": rng.uniform(0.1, 0.5, n_days),
                }
            )
        )
    return pl.concat(frames)


def test_module_exposes_the_registered_metrics():
    names = set(_public_functions())
    assert {"mae", "rmse", "bias", "variance_ratio", "bias_by_percentile"} <= names


def test_every_public_function_declares_interval_bearing_return_type():
    for name, fn in _public_functions().items():
        annotation = inspect.signature(fn).return_annotation
        assert annotation is not inspect.Signature.empty, f"{name}: missing return annotation"
        assert annotation in (BootstrapResult, pl.DataFrame, "BootstrapResult", "pl.DataFrame"), (
            f"{name}: public metric must return BootstrapResult or a CI-bearing "
            f"DataFrame, declares {annotation!r}"
        )


def test_every_public_function_returns_estimate_with_ci(pairs):
    """Call each public function for real; the result must carry its interval."""
    fns = _public_functions()
    assert fns, "no public functions found — introspection is broken"
    for name, fn in fns.items():
        result = fn(pairs, rng=np.random.default_rng(1), n_boot=25, block_len=3)
        if isinstance(result, BootstrapResult):
            assert np.isfinite(result.estimate), f"{name}: non-finite estimate"
            assert result.ci_low <= result.ci_high, f"{name}: inverted CI"
            assert result.alpha == 0.05
            assert result.n_boot == 25
        elif isinstance(result, pl.DataFrame):
            assert {"ci_low", "ci_high"} <= set(result.columns), (
                f"{name}: DataFrame result lacks ci_low/ci_high columns"
            )
        else:
            pytest.fail(
                f"{name}: returned {type(result).__name__} — public metrics must return "
                "BootstrapResult or a CI-bearing DataFrame (non-negotiable #3)"
            )


def test_stat_functions_are_the_only_bare_float_returns():
    """*_stat building blocks return float; nothing else in the module may."""
    for name, obj in inspect.getmembers(core, inspect.isfunction):
        if obj.__module__ != core.__name__ or name.startswith("_"):
            continue
        annotation = inspect.signature(obj).return_annotation
        if annotation in (float, "float"):
            assert name.endswith("_stat"), (
                f"{name}: bare-float return is reserved for *_stat building blocks"
            )


def test_decomposition_also_carries_intervals(pairs):
    """Same non-negotiable, applied to the decomposition module's public API."""
    out = representativeness_decomposition(
        pairs, rng=np.random.default_rng(2), n_boot=25, block_len=3
    )
    assert {"ci_low", "ci_high"} <= set(out.columns)
    assert out.height == 3
