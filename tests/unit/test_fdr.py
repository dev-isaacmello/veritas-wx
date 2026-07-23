"""Golden (hand-computed) + property tests for Benjamini-Hochberg."""

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from veritas_wx.analyze.fdr import benjamini_hochberg


def test_golden_hand_computed():
    """n=4, sorted input. Step-up quantities n*p_i/i:

        0.005*4/1 = 0.020
        0.010*4/2 = 0.020
        0.030*4/3 = 0.040
        0.040*4/4 = 0.040

    Cumulative min from the back changes nothing here =>
    p_adj = [0.02, 0.02, 0.04, 0.04]; all <= q=0.05 => all rejected.
    """
    p = np.array([0.005, 0.01, 0.03, 0.04])
    p_adj, rejected = benjamini_hochberg(p, q=0.05)
    np.testing.assert_allclose(p_adj, [0.02, 0.02, 0.04, 0.04])
    assert rejected.all()


def test_golden_unsorted_input_returns_input_order():
    """Same four p-values, shuffled: output must align with the INPUT order.

    Input [0.04, 0.005, 0.03, 0.01] corresponds, sorted, to the golden case
    above; mapping the sorted adjustments back:
        0.04 -> 0.04, 0.005 -> 0.02, 0.03 -> 0.04, 0.01 -> 0.02.
    """
    p = np.array([0.04, 0.005, 0.03, 0.01])
    p_adj, rejected = benjamini_hochberg(p, q=0.05)
    np.testing.assert_allclose(p_adj, [0.04, 0.02, 0.04, 0.02])
    assert rejected.all()


def test_golden_with_step_up_monotonicity_binding():
    """Case where the cumulative-min actually rewrites an early value.

    p = [0.01, 0.02, 0.021]: raw step-up = [0.03, 0.03, 0.021];
    cummin from the back => [0.021, 0.021, 0.021].
    """
    p = np.array([0.01, 0.02, 0.021])
    p_adj, _ = benjamini_hochberg(p)
    np.testing.assert_allclose(p_adj, [0.021, 0.021, 0.021])


def test_empty_input():
    p_adj, rejected = benjamini_hochberg(np.array([]))
    assert p_adj.size == 0 and rejected.size == 0


def test_invalid_p_raises():
    with pytest.raises(ValueError):
        benjamini_hochberg(np.array([0.1, 1.5]))
    with pytest.raises(ValueError):
        benjamini_hochberg(np.array([-0.1]))
    with pytest.raises(ValueError):
        benjamini_hochberg(np.array([np.nan]))


@given(
    st.lists(st.floats(min_value=0.0, max_value=1.0, allow_nan=False), min_size=1, max_size=50)
)
def test_property_adjusted_at_least_raw_and_capped(ps):
    p = np.array(ps)
    p_adj, _ = benjamini_hochberg(p)
    assert np.all(p_adj >= p - 1e-15)
    assert np.all(p_adj <= 1.0)


@given(
    st.lists(st.floats(min_value=0.0, max_value=1.0, allow_nan=False), min_size=2, max_size=50)
)
def test_property_monotone_in_raw_order(ps):
    """Sorting by raw p must leave the adjusted values non-decreasing."""
    p = np.array(ps)
    p_adj, _ = benjamini_hochberg(p)
    order = np.argsort(p, kind="stable")
    assert np.all(np.diff(p_adj[order]) >= -1e-15)


@given(
    st.lists(st.floats(min_value=0.0, max_value=1.0, allow_nan=False), min_size=1, max_size=50)
)
def test_property_q_one_rejects_everything(ps):
    """With q=1 every hypothesis with p <= 1 (i.e. all of them) is rejected."""
    p = np.array(ps)
    _, rejected = benjamini_hochberg(p, q=1.0)
    assert rejected.all()


@given(
    st.lists(st.floats(min_value=0.0, max_value=1.0, allow_nan=False), min_size=1, max_size=30),
    st.randoms(),
)
def test_property_permutation_equivariant(ps, rnd):
    """Shuffling the input shuffles the output identically."""
    p = np.array(ps)
    perm = list(range(len(ps)))
    rnd.shuffle(perm)
    perm = np.array(perm)
    base_adj, base_rej = benjamini_hochberg(p)
    perm_adj, perm_rej = benjamini_hochberg(p[perm])
    np.testing.assert_allclose(perm_adj, base_adj[perm])
    assert np.array_equal(perm_rej, base_rej[perm])
