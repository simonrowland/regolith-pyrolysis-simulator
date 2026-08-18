"""Rail-owned MELTS SiO2 calibration band and failed-constraint reporting."""

from __future__ import annotations

import pathlib
import pytest

from engines.alphamelts.domain import (
    CONSTRAINT_MAJOR_OXIDE_SUM,
    CONSTRAINT_OXIDE_BASIS,
    CONSTRAINT_SILICATE_NETWORK_BAND,
    DEFAULT_SILICATE_NETWORK_BAND_WT_PCT,
    MELTS_PARENT_OXIDE_NOT_ENDMEMBER,
    AlphaMELTSDomainGate,
    DomainGateAssessment,
    melts_endmember_to_parent_oxide_activity,
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


def test_unrecognised_backend_status_refuses_instead_of_passing():
    """An unknown adapter status must not be reported as a successful run.

    engines/alphamelts/provider.py mapped anything outside its four-token
    whitelist to 'ok'. A repo scan found backend_status is also set to failed,
    missing, stale, not_run, not_attempted, stub, diagnostic_stub, fallback,
    mixed_backend and no_compared_results -- three of those in production code
    at simulator/optimize/fidelity.py. Every one would have been reported as a
    successful equilibrium. Unknown is a missing input, doctrine category (1),
    so it refuses.
    """
    import engines.alphamelts.provider as provider_mod

    src = pathlib.Path(provider_mod.__file__).read_text()
    # Strip comments before grepping: the fix's own comment quotes the old
    # fail-open form, and a guard that trips on its own documentation is a
    # guard that will be deleted rather than believed.
    code = "\n".join(
        line.split("#", 1)[0] for line in src.splitlines()
    )
    assert "else 'ok'" not in code, (
        "unknown backend_status must not default to ok"
    )
    assert "kernel_status = 'unavailable'" in code
    # And the refusal must be visible, not silent.
    assert "unrecognised backend_status" in src


def test_melts_endmember_identity_matches_hand_worked_sio2_al2o3() -> None:
    """SiO2 and Al2O3 are MELTS liquid endmembers: identity conversion.

    Hand-worked: the engine reports a(SiO2)=0.42 and a(Al2O3)=0.25. Because
    those labels *are* the parent oxides, a_parent = a_endmember. Unit check:
    both sides dimensionless. Sanity: the pure-SiO2 limit is 1 = 1.
    """
    activities = {
        "SiO2": 0.42,
        "Al2O3": 0.25,
        "CaSiO3": 0.11,
        "Mg2SiO4": 0.08,
        "Ca3(PO4)2": 0.03,
        "CoSiO3": 0.01,
    }
    a_sio2, sio2_reason = melts_endmember_to_parent_oxide_activity(
        activities, "SiO2"
    )
    a_al2o3, al2o3_reason = melts_endmember_to_parent_oxide_activity(
        activities, "a(Al2O3)"
    )
    assert a_sio2 == pytest.approx(0.42)
    assert sio2_reason == ""
    assert a_al2o3 == pytest.approx(0.25)
    assert al2o3_reason == ""


def test_melts_cao_mgo_are_typed_refusals_not_fabricated_conversions() -> None:
    """CaO/MgO are not MELTS liquid endmembers; do not invent a residual.

    The two fabricated conversions this pins against:
    * stoichiometric proxy  a ≈ ν · a_endmember
      (CaSiO3 carries 1 CaO → 0.11; Mg2SiO4 carries 2 MgO → 0.16)
    * chemical-potential inversion without μ°_oxide
      a(CaO) = a(CaSiO3)/a(SiO2) = 0.11/0.42
      a(MgO) = sqrt(a(Mg2SiO4)/a(SiO2)) = sqrt(0.08/0.42)
    Both require a pure-oxide liquid standard state MELTS does not define.
    """
    activities = {
        "SiO2": 0.42,
        "Al2O3": 0.25,
        "CaSiO3": 0.11,
        "Mg2SiO4": 0.08,
        "Ca3(PO4)2": 0.03,
        "CoSiO3": 0.01,
    }
    a_cao, cao_reason = melts_endmember_to_parent_oxide_activity(
        activities, "CaO"
    )
    a_mgo, mgo_reason = melts_endmember_to_parent_oxide_activity(
        activities, "MgO"
    )
    fabricated_cao_nu = 1.0 * 0.11
    fabricated_cao_ratio = 0.11 / 0.42
    fabricated_mgo_nu = 2.0 * 0.08
    fabricated_mgo_ratio = (0.08 / 0.42) ** 0.5

    assert a_cao is None
    assert a_mgo is None
    assert a_cao != pytest.approx(fabricated_cao_nu)
    assert a_cao != pytest.approx(fabricated_cao_ratio)
    assert a_mgo != pytest.approx(fabricated_mgo_nu)
    assert a_mgo != pytest.approx(fabricated_mgo_ratio)
    assert MELTS_PARENT_OXIDE_NOT_ENDMEMBER in cao_reason
    assert MELTS_PARENT_OXIDE_NOT_ENDMEMBER in mgo_reason
    assert "CaSiO3" in cao_reason
    assert "Ca3(PO4)2" in cao_reason
    assert "Mg2SiO4" in mgo_reason
    assert "standard state the model does not define" in cao_reason
    assert "standard state the model does not define" in mgo_reason
