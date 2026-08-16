"""Rail-owned MELTS SiO2 calibration band and failed-constraint reporting."""

from __future__ import annotations

import pytest

from engines.alphamelts.domain import (
    CONSTRAINT_MAJOR_OXIDE_SUM,
    CONSTRAINT_OXIDE_BASIS,
    CONSTRAINT_SILICATE_NETWORK_BAND,
    DEFAULT_SILICATE_NETWORK_BAND_WT_PCT,
    AlphaMELTSDomainGate,
    DomainGateAssessment,
)


def _in_band_basalt() -> dict[str, float]:
    return {
        "SiO2": 50.0,
        "Al2O3": 15.0,
        "FeO": 10.0,
        "MgO": 10.0,
        "CaO": 10.0,
        "Na2O": 5.0,
    }


def test_default_band_matches_historical_30_80() -> None:
    assert DEFAULT_SILICATE_NETWORK_BAND_WT_PCT == (30.0, 80.0)


def test_assess_default_band_is_golden_neutral_with_validate() -> None:
    composition = {"SiO2": 10.0, "MgO": 45.0, "FeO": 45.0}
    valid, warnings, reason = AlphaMELTSDomainGate.validate_with_reason(composition)
    assessment = AlphaMELTSDomainGate.assess(composition)

    assert isinstance(assessment, DomainGateAssessment)
    assert assessment.valid is valid is False
    assert list(assessment.warnings) == warnings
    assert assessment.reason == reason == "silicate_window"
    assert assessment.failed_constraints == (CONSTRAINT_SILICATE_NETWORK_BAND,)
    assert assessment.silicate_network_band_wt_pct == (30.0, 80.0)


def test_widened_rail_band_admits_low_silica_that_default_refuses() -> None:
    # UPDATED 2026-08-16: was SiO2 10.0 with band (0, 100), which asserted the
    # band could be opened down to 10 wt%. The rump-hotwire measurement showed
    # alphaMELTS SIGABRTs there, so a sub-floor band is now refused outright.
    # Widening is still real -- it just goes UPWARD from the default max.
    composition = {"SiO2": 85.0, "MgO": 7.5, "FeO": 7.5}
    default = AlphaMELTSDomainGate.assess(composition)
    widened = AlphaMELTSDomainGate.assess(
        composition, silicate_network_band=(34.0, 100.0)
    )

    assert default.valid is False
    assert CONSTRAINT_SILICATE_NETWORK_BAND in default.failed_constraints
    assert widened.valid is True
    assert widened.failed_constraints == ()
    assert widened.silicate_network_band_wt_pct == (34.0, 100.0)


def test_failed_constraints_distinguish_band_from_oxide_basis_and_major_sum() -> None:
    oxide_basis = AlphaMELTSDomainGate.assess(
        {"SiO2": 50.0, "MgO": 50.0, "Fe": 5.0},
        silicate_network_band=(34.0, 100.0),
    )
    major_sum = AlphaMELTSDomainGate.assess(
        {"SiO2": 40.0, "FeO": 1.0},
        silicate_network_band=(34.0, 100.0),
    )
    both = AlphaMELTSDomainGate.assess(
        {"SiO2": 10.0, "Fe": 90.0},
        silicate_network_band=DEFAULT_SILICATE_NETWORK_BAND_WT_PCT,
    )

    assert oxide_basis.valid is False
    assert oxide_basis.failed_constraints == (CONSTRAINT_OXIDE_BASIS,)
    assert oxide_basis.reason == "forbidden_species"

    assert major_sum.valid is False
    assert major_sum.failed_constraints == (CONSTRAINT_MAJOR_OXIDE_SUM,)
    assert major_sum.reason == "major_sum"

    assert CONSTRAINT_OXIDE_BASIS in both.failed_constraints
    assert CONSTRAINT_SILICATE_NETWORK_BAND in both.failed_constraints
    assert CONSTRAINT_MAJOR_OXIDE_SUM in both.failed_constraints
    assert both.reason == "forbidden_species"


def test_validate_accepts_rail_band_without_changing_two_tuple_contract() -> None:
    valid, warnings = AlphaMELTSDomainGate.validate(
        {"SiO2": 40.0, "MgO": 30.0, "FeO": 30.0},
        silicate_network_band=(34.0, 90.0),
    )
    assert valid is True
    assert warnings == []


def test_invalid_rail_band_fails_loud() -> None:
    with pytest.raises(ValueError, match="finite min<=max"):
        AlphaMELTSDomainGate.assess(_in_band_basalt(), silicate_network_band=(80.0, 30.0))


def test_two_component_alkali_silica_is_not_refused_by_this_gate() -> None:
    """Crash-guard compositions stay computable here; only the adapter blocks them."""
    assessment = AlphaMELTSDomainGate.assess({"SiO2": 70.0, "Na2O": 30.0})
    assert assessment.valid is True
    assert assessment.failed_constraints == ()
