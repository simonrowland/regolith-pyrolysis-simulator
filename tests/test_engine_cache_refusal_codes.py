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
        "SILICATE_WINDOW",
        "INVALID_INPUT",
        "UNSUPPORTED_INPUT",
        "CLAMPED_OPERATING_POINT",
        "MAGEMIN_MODE_VECTOR_MASS_DEFICIT",
    ],
)
def test_cache_refusal_literal_matches_domain_reason(name: str) -> None:
    literal = getattr(engine_cache, f"REFUSAL_{name}")
    assert literal == OutOfDomainReason[name].value
    assert literal in engine_cache.CACHEABLE_REFUSAL_CODES


def test_domain_major_sum_literal_is_not_the_wt_pct_token() -> None:
    """OutOfDomainReason.MAJOR_SUM is "major_sum". REFUSAL_MAJOR_SUM is the
    older sum_below_95_wt_pct token. Both stay on the allowlist.
    """

    assert engine_cache.REFUSAL_DOMAIN_MAJOR_SUM == OutOfDomainReason.MAJOR_SUM.value
    assert engine_cache.REFUSAL_DOMAIN_MAJOR_SUM == "major_sum"
    assert engine_cache.REFUSAL_MAJOR_SUM == "sum_below_95_wt_pct"
    assert engine_cache.REFUSAL_DOMAIN_MAJOR_SUM in engine_cache.CACHEABLE_REFUSAL_CODES
    assert engine_cache.REFUSAL_MAJOR_SUM in engine_cache.CACHEABLE_REFUSAL_CODES


def test_probe_refusal_literals_match_their_owners() -> None:
    """Adapter tokens are repeated in the leaf. The owners are the pin."""

    from engines.alphamelts.thermoengine import ThermoEngineRefusalCause
    from simulator.melt_backend.alphamelts import (
        ALPHAMELTS_REASON_NO_CONVERGENCE,
        ALPHAMELTS_REASON_SUBPROCESS_DIED,
    )

    pairs = (
        (
            engine_cache.REFUSAL_NO_CONVERGENCE,
            ALPHAMELTS_REASON_NO_CONVERGENCE,
        ),
        (
            engine_cache.REFUSAL_SUBPROCESS_DIED,
            ALPHAMELTS_REASON_SUBPROCESS_DIED,
        ),
        (
            engine_cache.REFUSAL_FO2_NONMONOTONIC_BUFFERED,
            ThermoEngineRefusalCause.FO2_NONMONOTONIC_BUFFERED.value,
        ),
    )
    for literal, owner in pairs:
        assert literal == owner
        assert literal in engine_cache.CACHEABLE_REFUSAL_CODES


def test_unknown_gas_activity_code_is_not_a_cache_literal() -> None:
    """The activity-key miss stays a package token, not a leaf literal.

    evaluate_gas raises ImccGasUnknownActivityError for a key that is neither
    a recognised parent nor a species. Round 2 did not allowlist that code,
    and engine_cache does not repeat it.
    """

    pytest.importorskip("openimcc")
    from openimcc.gas import ImccGasUnknownActivityError

    code = ImccGasUnknownActivityError.code
    assert code == "imcc_gas_unknown_activity"
    assert code not in engine_cache.CACHEABLE_REFUSAL_CODES
    leaf_literals = {
        value
        for name, value in vars(engine_cache).items()
        if name.startswith("REFUSAL_") and isinstance(value, str)
    }
    assert code not in leaf_literals
