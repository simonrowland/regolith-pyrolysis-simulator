"""engine_cache's refusal-code literals must equal the shared enum values.

engine_cache is a standard-library leaf (see
tests/test_import_boundary.py::test_engine_cache_is_a_standard_library_leaf),
so it repeats these codes as literals instead of importing
engines.domain_reason. This test is the binding: renaming an enum value
without updating the cache allowlist fails here, not silently as a cache miss.
"""

from __future__ import annotations

import pytest

from engines.domain_reason import OutOfDomainReason
from simulator import engine_cache


@pytest.mark.parametrize(
    "name",
    [
        "FORBIDDEN_SPECIES",
        "LIQUID_STATE",
        "INVALID_PRESSURE_BAR",
        "INVALID_FO2_LOG",
        "SUM_PRESSURE_SANITY",
        "SPECIATION_NO_FINITE_VALUES",
    ],
)
def test_cache_refusal_literal_matches_domain_reason(name: str) -> None:
    literal = getattr(engine_cache, f"REFUSAL_{name}")
    assert literal == OutOfDomainReason[name].value
    assert literal in engine_cache.CACHEABLE_REFUSAL_CODES
