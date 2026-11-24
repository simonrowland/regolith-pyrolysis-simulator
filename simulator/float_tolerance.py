"""Small binary64 tolerances for comparisons of accumulated values."""

from __future__ import annotations

import math
import sys
from collections.abc import Iterable


def sum_roundoff_slack(terms: Iterable[float], *, noop: float) -> float:
    """Return a magnitude-scaled slack for a comparison of float sums.

    ``terms`` are the signed terms used by the compared sums, all in the same
    unit. ``noop`` is the existing absolute floor in that unit.
    """
    values = tuple(float(term) for term in terms)
    if not values:
        return noop

    magnitude = math.fsum(abs(value) for value in values)
    term_count = len(values)
    scaled_slack = (term_count / 2.0 + 1.0) * sys.float_info.epsilon * magnitude

    # For a binary64 sum of N terms, the absolute summation error is bounded
    # by (N−1)·eps·Σ|terms|. For the two sums in a comparison, N/2 terms per
    # side give the requested k=N/2+1 allowance for both reductions and the
    # final comparison. eps is dimensionless, so k·eps·M retains mol, kg, or
    # another shared unit; ``noop`` has that same unit. At the C4 values
    # 5.089568621263581 and 5.089568621263579, N=2 gives about 4.5e-15 of
    # slack, so the 1.78e-15 two-ulp gap passes while a 1e-9 overdraw remains
    # larger than the bound.
    return max(noop, scaled_slack)
