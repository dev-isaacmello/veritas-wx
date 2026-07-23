"""Golden, coverage and width-ordering tests for the autocorrelation-robust t-tests.

Golden values are hand-derived (exact fractions where possible) from the
formulas in the module docstrings, independently of the implementation:
for x = [1, 2, 4, 3, 5] the Geer AR(2) chain gives r1 = 0.1, r2 = 0,
phi1 = 10/99, phi2 = -1/99, k^2 = 9702/8100; the HAC chain at n = 5 keeps
v = 1 cosine coefficient, recomputed here with an explicit cosine basis.
"""

import math

import numpy as np
import pytest

from veritas_wx.analyze.ttest import (
    TTestResult,
    geer_ar2_ttest,
    iid_ttest,
    lazarus_hac_ttest,
    paired_diff_ttest,
)

GOLDEN_SERIES = np.array([1.0, 2.0, 4.0, 3.0, 5.0])
T_CRIT_975_DOF4 = 2.7764451051977943
T_CRIT_975_DOF1 = 12.706204736174705


def test_iid_golden_by_hand():
    """mean 3, ddof=1 variance 2.5, stderr sqrt(2.5/5), dof 4."""
    res = iid_ttest(GOLDEN_SERIES)
    stderr = math.sqrt(0.5)
    assert res.estimate == pytest.approx(3.0)
    assert res.stderr == pytest.approx(stderr)
    assert res.n_eff == pytest.approx(5.0)
    assert res.ci_low == pytest.approx(3.0 - T_CRIT_975_DOF4 * stderr)
    assert res.ci_high == pytest.approx(3.0 + T_CRIT_975_DOF4 * stderr)
    assert res.p_value == pytest.approx(0.013235599563682587)
    assert res.method == "iid" and res.n == 5 and res.alpha == 0.05


def test_geer_golden_by_hand():
    """k^2 = (1 - r1*phi1)/(1 - phi1 - phi2)^2 = (98/99)/(90/99)^2 = 9702/8100."""
    res = geer_ar2_ttest(GOLDEN_SERIES)
    k_squared = 9702.0 / 8100.0
    stderr = math.sqrt(0.5 * k_squared)
    assert res.estimate == pytest.approx(3.0)
    assert res.stderr == pytest.approx(stderr)
    assert res.n_eff == pytest.approx(5.0 / k_squared)
    assert res.ci_low == pytest.approx(3.0 - T_CRIT_975_DOF4 * stderr)
    assert res.ci_high == pytest.approx(3.0 + T_CRIT_975_DOF4 * stderr)
    assert res.p_value == pytest.approx(0.017893242011549848)
    assert res.method == "geer_ar2"


def test_lazarus_golden_by_hand():
    """n = 5 keeps v = int(0.4 * 5^(2/3)) = 1 coefficient; explicit cosine basis."""
    deviations = GOLDEN_SERIES - 3.0
    basis = np.cos(np.pi * (np.arange(5) + 0.5) / 5)
    projection = math.sqrt(2.0 / 5.0) * float((deviations * basis).sum())
    stderr = math.sqrt(projection**2 / 5.0)
    res = lazarus_hac_ttest(GOLDEN_SERIES)
    assert res.estimate == pytest.approx(3.0)
    assert res.stderr == pytest.approx(stderr)
    assert res.n_eff == pytest.approx(2.0)
    assert res.ci_low == pytest.approx(3.0 - T_CRIT_975_DOF1 * stderr)
    assert res.ci_high == pytest.approx(3.0 + T_CRIT_975_DOF1 * stderr)
    assert res.p_value == pytest.approx(0.2499289408435752)
    assert res.method == "lazarus_hac"


def test_iid_series_large_sample_all_methods_cover_zero():
    """N(0,1) iid: every method's CI covers the true mean 0 and n_eff ~= n."""
    rng = np.random.default_rng(0)
    x = rng.standard_normal(4000)
    iid = iid_ttest(x)
    geer = geer_ar2_ttest(x)
    hac = lazarus_hac_ttest(x)
    for res in (iid, geer, hac):
        assert res.ci_low < 0.0 < res.ci_high
        assert res.ci_low <= res.estimate <= res.ci_high
    assert geer.n_eff == pytest.approx(4000, rel=0.10)
    assert geer.stderr == pytest.approx(iid.stderr, rel=0.05)
    assert hac.stderr == pytest.approx(iid.stderr, rel=0.30)


def test_ar1_series_robust_cis_wider_than_naive():
    """AR(1) phi = 0.5: true k ~= sqrt(3), so robust CIs must be clearly wider."""
    rng = np.random.default_rng(1)
    n = 2000
    x = np.empty(n)
    x[0] = rng.standard_normal()
    for t in range(1, n):
        x[t] = 0.5 * x[t - 1] + rng.standard_normal()
    iid = iid_ttest(x)
    geer = geer_ar2_ttest(x)
    hac = lazarus_hac_ttest(x)
    width_iid = iid.ci_high - iid.ci_low
    assert (geer.ci_high - geer.ci_low) / width_iid > 1.3
    assert (hac.ci_high - hac.ci_low) / width_iid > 1.3
    assert geer.ci_low < 0.0 < geer.ci_high
    assert hac.ci_low < 0.0 < hac.ci_high
    assert geer.n_eff < 0.5 * n


def test_paired_diff_detects_known_offset():
    """b = a + 0.4 + noise: every method finds the -0.4 mean difference."""
    rng = np.random.default_rng(2)
    n = 400
    common = rng.standard_normal(n)
    a = common + rng.normal(0.0, 0.3, n)
    b = a + 0.4 + rng.normal(0.0, 0.2, n)
    for method in ("iid", "geer_ar2", "lazarus_hac"):
        res = paired_diff_ttest(a, b, method=method)
        assert res.estimate == pytest.approx(-0.4, abs=0.05)
        assert res.ci_high < 0.0
        assert res.p_value < 1e-4
        assert res.method == f"paired_diff_{method}"


def test_paired_diff_equals_test_on_difference_series():
    rng = np.random.default_rng(3)
    a = rng.standard_normal(100)
    b = rng.standard_normal(100)
    paired = paired_diff_ttest(a, b, method="geer_ar2")
    direct = geer_ar2_ttest(a - b)
    assert paired.estimate == direct.estimate
    assert paired.stderr == direct.stderr
    assert paired.ci_low == direct.ci_low
    assert paired.p_value == direct.p_value


def test_constant_series_degenerates_gracefully():
    """WBX convention: zero-width CI, p = 0 off the null and p = 1 on it."""
    for fn in (iid_ttest, geer_ar2_ttest, lazarus_hac_ttest):
        res = fn(np.full(50, 2.0))
        assert res.estimate == 2.0 and res.stderr == 0.0
        assert res.ci_low == 2.0 and res.ci_high == 2.0
        assert res.p_value == 0.0
        zero = fn(np.zeros(50))
        assert zero.p_value == 1.0 and zero.ci_low == 0.0 == zero.ci_high


def test_hac_n_eff_is_v_plus_one():
    """n = 50 => v = int(0.4 * 50^(2/3)) = 5 => n_eff = 6."""
    res = lazarus_hac_ttest(np.random.default_rng(4).standard_normal(50))
    assert res.n_eff == 6.0


def test_input_validation():
    with pytest.raises(ValueError, match="1-D"):
        iid_ttest(np.zeros((3, 3)))
    with pytest.raises(ValueError, match="at least 3"):
        geer_ar2_ttest(np.array([1.0, 2.0]))
    with pytest.raises(ValueError, match="NaN"):
        lazarus_hac_ttest(np.array([1.0, np.nan, 2.0]))
    with pytest.raises(ValueError, match="aligned"):
        paired_diff_ttest(np.zeros(5), np.zeros(6))
    with pytest.raises(ValueError, match="unknown method"):
        paired_diff_ttest(np.zeros(5), np.zeros(5), method="bogus")


def test_result_is_frozen_and_interval_bearing():
    res = iid_ttest(GOLDEN_SERIES)
    assert isinstance(res, TTestResult)
    with pytest.raises(Exception):  # noqa: B017
        res.estimate = 0.0
