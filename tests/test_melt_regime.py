import math

import pytest

from simulator.melt_regime import (
    MELT_REGIME_EPSILON,
    MeltRegime,
    melt_regime,
)


def test_melt_regime_liquid_fraction_uses_canonical_epsilon():
    assert melt_regime(liquid_fraction=0.0) == MeltRegime.FROZEN
    assert melt_regime(
        liquid_fraction=MELT_REGIME_EPSILON / 2.0
    ) == MeltRegime.FROZEN
    assert melt_regime(
        liquid_fraction=MELT_REGIME_EPSILON * 2.0
    ) == MeltRegime.PARTIAL
    assert melt_regime(
        liquid_fraction=1.0 - MELT_REGIME_EPSILON / 2.0
    ) == MeltRegime.MOLTEN


@pytest.mark.parametrize(
    ("liquid_fraction", "expected"),
    [
        (0.0, MeltRegime.FROZEN),
        (1.0e-12, MeltRegime.FROZEN),
        (math.nextafter(1.0e-12, math.inf), MeltRegime.PARTIAL),
        (1.0 - 1.0e-12, MeltRegime.MOLTEN),
        (1.0, MeltRegime.MOLTEN),
    ],
)
def test_melt_regime_liquid_fraction_boundary_cases(
    liquid_fraction, expected
):
    assert melt_regime(liquid_fraction=liquid_fraction) == expected


@pytest.mark.parametrize(
    "liquid_fraction",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(None, id="none"),
        pytest.param(-0.1, id="negative"),
        pytest.param(float("inf"), id="inf"),
    ],
)
def test_melt_regime_liquid_fraction_invalid_boundaries(liquid_fraction):
    with pytest.raises(ValueError):
        melt_regime(liquid_fraction=liquid_fraction)


def test_melt_regime_can_preserve_legacy_exact_zero_with_diagnostic():
    diagnostic = {}

    regime = melt_regime(
        liquid_fraction=MELT_REGIME_EPSILON / 2.0,
        epsilon=0.0,
        diagnostic=diagnostic,
        diagnostic_site="test.legacy_exact_zero",
        legacy_predicate="liquid_fraction == 0.0",
    )

    assert regime == MeltRegime.PARTIAL
    assert diagnostic["melt_regime_predicate_divergences"] == [
        {
            "site": "test.legacy_exact_zero",
            "canonical_regime": "frozen",
            "effective_regime": "partial",
            "canonical_epsilon": MELT_REGIME_EPSILON,
            "effective_epsilon": 0.0,
            "legacy_predicate": "liquid_fraction == 0.0",
            "liquid_fraction": MELT_REGIME_EPSILON / 2.0,
        }
    ]


@pytest.mark.parametrize(
    "liquid_fraction",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(-0.1, id="negative"),
        pytest.param(float("inf"), id="inf"),
    ],
)
def test_melt_regime_legacy_invalid_liquid_fraction_falls_through_with_diagnostic(
    liquid_fraction,
):
    diagnostic = {}

    regime = melt_regime(
        liquid_fraction=liquid_fraction,
        epsilon=0.0,
        invalid_liquid_fraction_regime=MeltRegime.PARTIAL,
        diagnostic=diagnostic,
        diagnostic_site="test.legacy_invalid_exact_zero",
        legacy_predicate="liquid_fraction == 0.0",
    )

    assert regime == MeltRegime.PARTIAL
    divergence = diagnostic["melt_regime_predicate_divergences"][0]
    assert divergence["site"] == "test.legacy_invalid_exact_zero"
    assert divergence["effective_regime"] == "partial"
    assert divergence["canonical_error"]
    assert divergence["liquid_fraction_invalid"] in {
        "non_finite",
        "out_of_range",
    }


def test_melt_regime_solidus_boundary_edges():
    solidus_K = 1300.0

    assert melt_regime(
        temperature_K=solidus_K,
        solidus_K=solidus_K,
    ) == MeltRegime.FROZEN
    assert melt_regime(
        temperature_K=solidus_K + MELT_REGIME_EPSILON / 2.0,
        solidus_K=solidus_K,
    ) == MeltRegime.FROZEN
    assert melt_regime(
        temperature_K=solidus_K + MELT_REGIME_EPSILON * 2.0,
        solidus_K=solidus_K,
    ) == MeltRegime.PARTIAL


def test_melt_regime_temperature_boundary_modes():
    solidus_K = 1300.0

    assert melt_regime(
        temperature_K=solidus_K,
        solidus_K=solidus_K,
    ) == MeltRegime.FROZEN
    assert melt_regime(
        temperature_K=solidus_K + 1.0e-12,
        solidus_K=solidus_K,
    ) == MeltRegime.FROZEN
    assert melt_regime(
        temperature_K=solidus_K,
        solidus_K=solidus_K,
        solidus_boundary="liquid",
    ) == MeltRegime.PARTIAL
    assert melt_regime(
        temperature_K=solidus_K + 1.0e-12,
        solidus_K=solidus_K,
        solidus_boundary="liquid",
    ) == MeltRegime.PARTIAL


def test_melt_regime_can_preserve_strict_solidus_with_diagnostic():
    diagnostic = {}
    solidus_K = 1300.0

    regime = melt_regime(
        temperature_K=solidus_K + MELT_REGIME_EPSILON / 2.0,
        solidus_K=solidus_K,
        epsilon=0.0,
        diagnostic=diagnostic,
        diagnostic_site="test.strict_solidus",
        legacy_predicate="temperature_C > solidus_T_C",
    )

    assert regime == MeltRegime.PARTIAL
    assert diagnostic["melt_regime_predicate_divergences"][0] == {
        "site": "test.strict_solidus",
        "canonical_regime": "frozen",
        "effective_regime": "partial",
        "canonical_epsilon": MELT_REGIME_EPSILON,
        "effective_epsilon": 0.0,
        "legacy_predicate": "temperature_C > solidus_T_C",
        "temperature_K": solidus_K + MELT_REGIME_EPSILON / 2.0,
        "solidus_K": solidus_K,
    }


def test_melt_regime_rejects_invalid_inputs():
    with pytest.raises(ValueError, match="liquid_fraction"):
        melt_regime(liquid_fraction=-0.1)

    with pytest.raises(ValueError, match="requires"):
        melt_regime()
