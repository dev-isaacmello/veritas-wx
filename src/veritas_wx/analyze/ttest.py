"""Autocorrelation-robust t-tests over the daily score series (ADR-0004 item 2).

Exploratory diagnostics that cross-check the moving block bootstrap
(``analyze.bootstrap``, the pre-registered primary CI engine) at a fraction
of the cost, and provide Diebold-Mariano-style paired model comparisons.
The unit of inference is the DAY, matching the bootstrap's resample unit:
every function takes the daily time series of a score (or of a score
difference) as a 1-D array, ordered in time with a uniform (daily) step.
Nothing here touches metrics_registry.yaml — these methods are not part of
the confirmatory FDR protocol.

Three estimators of the standard error of the series mean:

- :func:`iid_ttest` — the classic t-test; honest only when daily scores are
  serially independent, which daily forecast errors usually are not.
- :func:`geer_ar2_ttest` — inflates the standard error by the factor k of
  Geer (2016, Tellus A 68:30229), derived from a stationary AR(2) fit to the
  series (lag-1/lag-2 autocorrelations). Well-motivated when the AR(2)
  assumption holds and the effective sample size is not too small; tends to
  be over-optimistic otherwise.
- :func:`lazarus_hac_ttest` — the nonparametric equal-weighted-cosine (EWC)
  HAC long-run variance estimator with the settings recommended by Lazarus,
  Lewis, Stock & Watson (2018, JBES 36:541-559): v = v_0 * n^(2/3) type-II
  DCT coefficients, v_0 = 0.4 by default (their Table 2b guides other
  choices), t-distribution with v degrees of freedom.

:func:`paired_diff_ttest` applies any of the three to the daily series of
score DIFFERENCES between two models — the Diebold-Mariano construction with
a modern HAC estimator by default.

Pure functions only: arrays in, a :class:`TTestResult` out; every estimate
travels with its confidence interval (non-negotiable #3).

Portions derived from WeatherBench-X (Copyright 2023 Google LLC, Apache
License 2.0), adapted from weatherbenchX/statistical_inference/t_test.py:
the AR(2) inflation factor of GeerAR2Corrected, the EWC HAC estimator of
LazarusHACEWC, the constant-series (zero-variance) handling; and from
weatherbenchX/statistical_inference/base.py: the paired baseline-comparison
construction (test on the per-unit difference series).
"""

import dataclasses
import math

import numpy as np
import scipy.fft
import scipy.stats

DEFAULT_LAZARUS_V0 = 0.4


@dataclasses.dataclass(frozen=True)
class TTestResult:
    """A mean estimate that never travels without its confidence interval.

    Attributes
    ----------
    estimate:
        Sample mean of the input series.
    ci_low, ci_high:
        Two-sided ``1 - alpha`` confidence interval for the underlying mean,
        ``estimate -/+ t_{1-alpha/2, dof} * stderr``.
    p_value:
        Two-sided p-value for H0: underlying mean == 0 (the natural null for
        score differences). A constant series yields 1.0 when the mean is
        exactly 0 and 0.0 otherwise, following the WeatherBench-X convention.
    stderr:
        Standard error of the mean under the method's dependence model.
    n_eff:
        Effective sample size. ``n`` for iid; ``n / k^2`` for Geer AR(2)
        (k is the standard-error inflation factor); ``v + 1`` for the HAC
        test, whose t-distribution has ``v`` degrees of freedom.
    method:
        "iid", "geer_ar2", "lazarus_hac", or "paired_diff_<method>".
    alpha:
        Two-sided significance level (0.05 => 95% CI).
    n:
        Number of daily values in the input series.
    """

    estimate: float
    ci_low: float
    ci_high: float
    p_value: float
    stderr: float
    n_eff: float
    method: str
    alpha: float
    n: int


def _validated(series: np.ndarray, min_n: int, method: str) -> np.ndarray:
    """Coerce to a finite 1-D float64 array of length >= min_n, or raise."""
    x = np.asarray(series, dtype=np.float64)
    if x.ndim != 1:
        raise ValueError(f"{method}: expected a 1-D daily series, got shape {x.shape}")
    if x.size < min_n:
        raise ValueError(f"{method}: need at least {min_n} daily values, got {x.size}")
    if not np.all(np.isfinite(x)):
        raise ValueError(f"{method}: series contains NaN/inf — drop or impute upstream")
    return x


def _result(
    mean: float,
    stderr: float,
    dof: int,
    n: int,
    n_eff: float,
    method: str,
    alpha: float,
) -> TTestResult:
    """Assemble a TTestResult from a mean, its stderr and t degrees of freedom.

    Zero-variance handling follows the WeatherBench-X source: with
    ``stderr == 0`` the CI collapses onto the mean and the p-value is 1.0
    when the mean equals the null (0) and 0.0 otherwise.
    """
    t_dist = scipy.stats.t(df=dof)
    half_width = float(-t_dist.ppf(alpha / 2.0)) * stderr
    if stderr == 0.0:
        p_value = 1.0 if mean == 0.0 else 0.0
    else:
        p_value = float(2.0 * (1.0 - t_dist.cdf(abs(mean / stderr))))
    return TTestResult(
        estimate=mean,
        ci_low=mean - half_width,
        ci_high=mean + half_width,
        p_value=p_value,
        stderr=stderr,
        n_eff=n_eff,
        method=method,
        alpha=alpha,
        n=n,
    )


def _autocorrelation(deviations: np.ndarray, variance: float, lag: int) -> float:
    """Lag-``lag`` autocorrelation estimate from zero-mean deviations.

    Mean of the lagged products (over ``n - lag`` terms) divided by the
    ddof=1 variance, exactly as in the WeatherBench-X source. A zero-variance
    (constant) series returns 0 so that no correction is applied downstream
    instead of propagating NaN.
    """
    if variance == 0.0:
        return 0.0
    return float((deviations[:-lag] * deviations[lag:]).mean() / variance)


def iid_ttest(series: np.ndarray, alpha: float = 0.05) -> TTestResult:
    """The classic t-test for the mean, assuming iid daily values.

    Reference behaviour for the robust variants: on autocorrelated daily
    scores its CI is too narrow (kept public for exactly that comparison,
    mirroring ``iid_bootstrap``). dof = n - 1, n_eff = n.
    """
    x = _validated(series, min_n=2, method="iid_ttest")
    n = x.size
    mean = float(x.mean())
    variance = float(((x - mean) ** 2).sum() / (n - 1))
    stderr = math.sqrt(variance / n)
    return _result(mean, stderr, dof=n - 1, n=n, n_eff=float(n), method="iid", alpha=alpha)


def geer_ar2_ttest(series: np.ndarray, alpha: float = 0.05) -> TTestResult:
    """t-test with the AR(2) standard-error inflation of Geer (2016).

    Fits a stationary AR(2) process to the series via its lag-1/lag-2
    autocorrelations (r1, r2), converts them to Yule-Walker coefficients
    (phi1, phi2) and inflates the iid standard error by

        k = sqrt((1 - r1*phi1 - r2*phi2) / (1 - phi1 - phi2)^2)

    leaving the t degrees of freedom at n - 1 (as in the source; ideally
    they would shrink under strong autocorrelation, which is one reason the
    method is over-optimistic at small effective sample sizes). n_eff is
    reported as n / k^2. Assumes a uniform (daily) time step — the caller
    must not pass a series with gaps silently closed.

    Raises ValueError for a degenerate AR(2) fit (|r1| == 1).
    """
    x = _validated(series, min_n=3, method="geer_ar2_ttest")
    n = x.size
    mean = float(x.mean())
    deviations = x - mean
    variance = float((deviations**2).sum() / (n - 1))
    r1 = _autocorrelation(deviations, variance, lag=1)
    r2 = _autocorrelation(deviations, variance, lag=2)
    denominator = 1.0 - r1**2
    if denominator == 0.0:
        raise ValueError("geer_ar2_ttest: degenerate AR(2) fit (|rho1| == 1)")
    phi1 = r1 * (1.0 - r2) / denominator
    phi2 = (r2 - r1**2) / denominator
    k_squared = (1.0 - r1 * phi1 - r2 * phi2) / (1.0 - phi1 - phi2) ** 2
    k = math.sqrt(k_squared)
    stderr = math.sqrt(variance / n) * k
    n_eff = n / k_squared if k_squared > 0.0 else float(n)
    return _result(mean, stderr, dof=n - 1, n=n, n_eff=n_eff, method="geer_ar2", alpha=alpha)


def lazarus_hac_ttest(
    series: np.ndarray,
    alpha: float = 0.05,
    v_0: float = DEFAULT_LAZARUS_V0,
) -> TTestResult:
    """t-test with the EWC HAC long-run variance of Lazarus et al. (2018).

    Projects the demeaned series onto its ``v = clip(int(v_0 * n^(2/3)), 1,
    n-1)`` lowest-frequency type-II DCT basis vectors (orthonormal, so with
    v = n - 1 the estimator would reduce to the iid variance) and averages
    the squared coefficients into a long-run variance estimate; the t
    distribution has v degrees of freedom. Nonparametric: no AR assumption,
    robust up to high autocorrelation (rho ~= 0.7) at the recommended
    v_0 = 0.4, trading away some power when autocorrelation is low. n_eff is
    reported as v + 1 (so that dof = n_eff - 1, as in the iid case).
    Assumes a uniform (daily) time step.
    """
    x = _validated(series, min_n=2, method="lazarus_hac_ttest")
    n = x.size
    mean = float(x.mean())
    deviations = x - mean
    v = int(v_0 * n ** (2.0 / 3.0))
    v = max(1, min(v, n - 1))
    projections = scipy.fft.dct(deviations, type=2, norm="ortho")[1 : v + 1]
    long_run_variance = float(np.mean(projections**2))
    stderr = math.sqrt(long_run_variance / n)
    return _result(mean, stderr, dof=v, n=n, n_eff=float(v + 1), method="lazarus_hac", alpha=alpha)


_PAIRED_METHODS = {
    "iid": iid_ttest,
    "geer_ar2": geer_ar2_ttest,
    "lazarus_hac": lazarus_hac_ttest,
}


def paired_diff_ttest(
    series_a: np.ndarray,
    series_b: np.ndarray,
    method: str = "lazarus_hac",
    alpha: float = 0.05,
) -> TTestResult:
    """Paired comparison of two models on their aligned daily score series.

    Diebold-Mariano-style: the chosen t-test is applied to the per-day
    difference ``series_a - series_b``, so the estimate is the mean score
    difference (negative favours model a when lower scores are better) and
    the p-value tests H0: no difference. Both series must come from the SAME
    days in the same order — build them from an exactly matched view
    (non-negotiable #5), never from differently-sampled facts. The default
    method is the HAC test, the modern replacement for the original
    Diebold-Mariano variance estimator.
    """
    a = np.asarray(series_a, dtype=np.float64)
    b = np.asarray(series_b, dtype=np.float64)
    if a.shape != b.shape:
        raise ValueError(
            f"paired_diff_ttest: series must be aligned day by day, got shapes "
            f"{a.shape} vs {b.shape}"
        )
    if method not in _PAIRED_METHODS:
        raise ValueError(
            f"paired_diff_ttest: unknown method {method!r}, expected one of "
            f"{sorted(_PAIRED_METHODS)}"
        )
    result = _PAIRED_METHODS[method](a - b, alpha=alpha)
    return dataclasses.replace(result, method=f"paired_diff_{method}")
