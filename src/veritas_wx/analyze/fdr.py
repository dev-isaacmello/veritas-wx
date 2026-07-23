"""Benjamini-Hochberg false discovery rate control (registry: fdr.method).

Pure array-in/array-out. Applied over the pre-registered test family of the
run (metrics_registry.yaml ``families``); ``n_family`` is recorded alongside
every result row by the caller.
"""

import numpy as np


def benjamini_hochberg(p: np.ndarray, q: float = 0.05) -> tuple[np.ndarray, np.ndarray]:
    """Benjamini-Hochberg step-up FDR adjustment.

    With ``p_(1) <= ... <= p_(n)`` the sorted raw p-values, the adjusted
    values are::

        p_adj_(i) = min_{j >= i} ( n * p_(j) / j )     (capped at 1)

    i.e. the raw step-up quantities ``n * p_(i) / i`` with monotonicity
    enforced by a cumulative minimum from the largest rank down. The null
    hypothesis ``i`` is rejected iff ``p_adj_i <= q`` — equivalent to the
    classic BH rule "reject all ``p_(i)`` with ``i <= k``, where ``k`` is the
    largest rank with ``p_(k) <= k * q / n``".

    Parameters
    ----------
    p:
        Raw p-values, any order, each in [0, 1]. The output arrays are in the
        SAME order as the input.
    q:
        Target false discovery rate (registry default: 0.05).

    Returns
    -------
    (p_adj, rejected):
        ``p_adj`` — BH-adjusted p-values, input order, monotone in the raw
        ranks, capped at 1.0; ``rejected`` — boolean array, ``p_adj <= q``.

    Reference: Y. Benjamini, Y. Hochberg (1995), "Controlling the False
    Discovery Rate", JRSS-B 57(1), 289-300.
    """
    p = np.asarray(p, dtype=np.float64)
    if p.ndim != 1:
        raise ValueError(f"benjamini_hochberg expects a 1-D array, got shape {p.shape}")
    if not (0.0 <= q <= 1.0):
        raise ValueError(f"q must be in [0, 1], got {q}")
    n = p.size
    if n == 0:
        return np.empty(0, dtype=np.float64), np.empty(0, dtype=bool)
    if np.any(~np.isfinite(p)) or np.any(p < 0.0) or np.any(p > 1.0):
        raise ValueError("p-values must be finite and within [0, 1]")

    order = np.argsort(p, kind="stable")
    ranks = np.arange(1, n + 1, dtype=np.float64)
    stepup = p[order] * n / ranks
    adj_sorted = np.minimum.accumulate(stepup[::-1])[::-1]
    adj_sorted = np.minimum(adj_sorted, 1.0)

    p_adj = np.empty(n, dtype=np.float64)
    p_adj[order] = adj_sorted
    rejected = p_adj <= q
    return p_adj, rejected
