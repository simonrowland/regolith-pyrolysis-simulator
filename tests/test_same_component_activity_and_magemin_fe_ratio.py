"""Same-component activity coefficients and MAGEMin liquid Fe3+/Fe2+.

Expected gamma and Fe ratios are the arithmetic of the recorded inputs.
They are not a second call of the production formula.
"""

from decimal import Decimal

import pytest

from engines.alphamelts.thermoengine import endmember_same_component_coefficients
from simulator.accounting.oxide_mass import oxide_molar_mass_g_mol
from simulator.battery.enums import Engine, ExecutionState, RefusalReason
from simulator.battery.migrate import fe3_fe2_ratio_from_oxide_wt_pct
from simulator.battery.score import (
    _MELTS_FE_REDOX_ENGINES,
    _REPORTED_SPLIT_FE_REDOX_ENGINES,
    predict_with_engine,
)
from simulator.chemistry.melt_activity import (
    SAME_COMPONENT_MOLE_FRACTION,
    SAME_COMPONENT_REFERENCE_CONVENTION,
    SAME_COMPONENT_REFERENCE_PHASE,
    SameComponentActivityCoefficient,
    SameComponentActivityRefusal,
    same_component_activity_coefficient,
    same_component_activity_coefficients_from_moles,
)
from simulator.fe_redox import (
    FE3_FE2_ATOM_RATIO_KEY,
    PhaseFe3Fe2,
    PhaseFe3Fe2Refusal,
    ig_liquid_endmember_formula_mass_g_mol,
    phase_fe3_fe2_from_endmember_moles,
    phase_fe3_fe2_from_endmember_wt_fraction,
)
from simulator.melt_backend.alphamelts import (
    liquid_phase_same_component_coefficients,
)
from simulator.melt_backend.base import EquilibriumResult
from simulator.melt_backend.magemin import magemin_liquid_fe3_fe2_from_matlab
from simulator.melt_backend.vaporock import (
    vaporock_oxide_same_component_coefficients,
)
from tests.battery.test_redox_extract_scoring import (
    _borisov_row4,
    _ok_cell,
    _patch_melts_cell,
)


def _reference_kwargs(component: str) -> dict[str, str]:
    return {
        "normalization": SAME_COMPONENT_MOLE_FRACTION,
        "reference_convention": SAME_COMPONENT_REFERENCE_CONVENTION,
        "reference_phase": SAME_COMPONENT_REFERENCE_PHASE,
        "reference_component_basis": component,
    }


def test_ideal_component_has_unit_gamma_and_nonideal_is_not_the_activity():
    ideal = same_component_activity_coefficient(
        activity_component="SiO2",
        activity=0.4,
        mole_fraction_component="SiO2",
        mole_fraction=0.4,
        **_reference_kwargs("SiO2"),
    )
    assert isinstance(ideal, SameComponentActivityCoefficient)
    assert ideal.gamma == pytest.approx(1.0)

    # a = 0.5, X = 0.25, gamma = 2. Returning the activity would be 0.5.
    nonideal = same_component_activity_coefficient(
        activity_component="SiO2",
        activity=0.5,
        mole_fraction_component="SiO2",
        mole_fraction=0.25,
        **_reference_kwargs("SiO2"),
    )
    assert isinstance(nonideal, SameComponentActivityCoefficient)
    assert nonideal.gamma == pytest.approx(2.0)
    assert nonideal.gamma != pytest.approx(0.5)
    assert nonideal.detail()["coefficient_basis"] == "mole_fraction"
    assert nonideal.detail()["standard_state"] == {
        "convention": "raoultian_pure_endmember",
        "phase": "l",
        "component_basis": "SiO2",
    }
    assert nonideal.detail()["normalization"] == "mole_fraction"


def test_different_component_or_missing_fraction_refuses():
    mismatch = same_component_activity_coefficient(
        activity_component="SiO2",
        activity=0.5,
        mole_fraction_component="MgO",
        mole_fraction=0.25,
        **_reference_kwargs("SiO2"),
    )
    assert isinstance(mismatch, SameComponentActivityRefusal)
    assert mismatch.reason == "component_basis_mismatch"

    missing = same_component_activity_coefficient(
        activity_component="SiO2",
        activity=0.5,
        mole_fraction_component="SiO2",
        mole_fraction=None,
        **_reference_kwargs("SiO2"),
    )
    assert isinstance(missing, SameComponentActivityRefusal)
    assert missing.reason == "mole_fraction_missing"


def test_thermoengine_endmember_gamma_uses_that_endmembers_fraction():
    gammas, details = endmember_same_component_coefficients(
        {"SiO2": 0.5, "Mg2SiO4": 0.2},
        {"SiO2": 0.25},
    )
    assert gammas["SiO2"] == pytest.approx(2.0)
    assert gammas["SiO2"] != pytest.approx(0.5)
    assert "Mg2SiO4" not in gammas
    assert details["SiO2"]["standard_state"]["component_basis"] == "SiO2"


def test_alphamelts_pairs_activity_with_the_same_suffix_only():
    gammas, details = liquid_phase_same_component_coefficients(
        {
            "activity_SiO2": 0.5,
            "activity0_SiO2": 0.9,
            "X_SiO2": 0.25,
            "activity_Mg2SiO4": 0.3,
            "X_MgO": 0.25,
        }
    )
    assert set(gammas) == {"SiO2"}
    assert gammas["SiO2"] == pytest.approx(2.0)
    # activity0_ is the pure-endmember reference activity, not a_i.
    assert gammas["SiO2"] != pytest.approx(3.6)
    assert details["SiO2"]["standard_state"]["component_basis"] == "SiO2"

    unpaired = liquid_phase_same_component_coefficients(
        {"activity_SiO2": 0.5, "X_MgO": 0.25}
    )
    assert unpaired[0] == {}


def test_vaporock_gamma_normalizes_only_the_reported_oxides():
    # Weights equal the formula masses, so each reported oxide is 1 mol
    # and X = 1/2. FeO is in the composition and has no activity, so it
    # is not part of that sum. gamma(SiO2) = 0.5 / 0.5 = 1.
    # gamma(MgO) = 0.25 / 0.5 = 0.5.
    sio2_mass = oxide_molar_mass_g_mol("SiO2")
    mgo_mass = oxide_molar_mass_g_mol("MgO")
    gammas, details = vaporock_oxide_same_component_coefficients(
        {"SiO2": 0.5, "MgO": 0.25},
        {"SiO2": sio2_mass, "MgO": mgo_mass, "FeO": 10.0},
    )
    assert gammas["SiO2"] == pytest.approx(1.0)
    assert gammas["MgO"] == pytest.approx(0.5)
    assert gammas["MgO"] != pytest.approx(0.25)
    assert "FeO" not in gammas
    assert details["MgO"]["standard_state"]["component_basis"] == "MgO"
    assert details["MgO"]["coefficient_basis"] == "mole_fraction"

    refused, _details = vaporock_oxide_same_component_coefficients(
        {"SiO2": 0.5, "MgO": 0.2},
        {"SiO2": sio2_mass},
    )
    assert refused == {}


def test_extra_positive_mole_refuses_the_whole_set():
    gammas, _details, refusals = same_component_activity_coefficients_from_moles(
        {"SiO2": 0.5},
        {"SiO2": 1.0, "MgO": 1.0},
        reference_convention=SAME_COMPONENT_REFERENCE_CONVENTION,
        reference_phase=SAME_COMPONENT_REFERENCE_PHASE,
    )
    assert gammas == {}
    assert refusals[0].reason == "normalization_mismatch"


def test_coefficient_reference_survives_cache_replay():
    result = EquilibriumResult(
        temperature_C=1200.0,
        pressure_bar=1.0,
        liquid_fraction=1.0,
    )
    result.reported_activity_coefficients = {"SiO2": 2.0}
    result.activity_coefficient_details = {
        "SiO2": {
            "value": 2.0,
            "coefficient_basis": "mole_fraction",
            "standard_state": {
                "convention": "raoultian_pure_endmember",
                "phase": "l",
                "component_basis": "SiO2",
            },
            "activity": 0.5,
            "mole_fraction": 0.25,
            "normalization": "mole_fraction",
        }
    }
    restored = EquilibriumResult.from_engine_cache_value(
        result.to_engine_cache_value()
    )
    detail = restored.activity_coefficient_details["SiO2"]
    assert detail["coefficient_basis"] == "mole_fraction"
    assert detail["standard_state"]["convention"] == "raoultian_pure_endmember"
    assert detail["standard_state"]["phase"] == "l"
    assert detail["standard_state"]["component_basis"] == "SiO2"
    assert restored.reported_activity_coefficients["SiO2"] == pytest.approx(2.0)


def test_ig_liquid_fe_ratio_is_ferric_atoms_over_ferrous_atoms():
    # fa2L is 4 Fe(II). hmL is 1 Fe(III). 1 mol of each is 1/4.
    # Swapped valence labels would be 4. Bulk oxygen is not an input.
    mixed = phase_fe3_fe2_from_endmember_moles(
        {"fa2L": 1.0, "hmL": 1.0, "q4L": 3.0},
        database="ig",
    )
    assert isinstance(mixed, PhaseFe3Fe2)
    assert mixed.n_fe2 == pytest.approx(4.0)
    assert mixed.n_fe3 == pytest.approx(1.0)
    assert mixed.ratio == pytest.approx(0.25)
    assert mixed.ratio != pytest.approx(4.0)

    mass_fa = ig_liquid_endmember_formula_mass_g_mol("fa2L")
    mass_hm = ig_liquid_endmember_formula_mass_g_mol("hmL")
    weighted = phase_fe3_fe2_from_endmember_wt_fraction(
        {"fa2L": mass_fa, "hmL": mass_hm},
        database="ig",
    )
    assert isinstance(weighted, PhaseFe3Fe2)
    assert weighted.ratio == pytest.approx(mixed.ratio)
    treated_as_moles = phase_fe3_fe2_from_endmember_moles(
        {"fa2L": mass_fa, "hmL": mass_hm},
        database="ig",
    )
    assert isinstance(treated_as_moles, PhaseFe3Fe2)
    assert treated_as_moles.ratio != pytest.approx(0.25)

    ferrous = phase_fe3_fe2_from_endmember_moles(
        {"fa2L": 2.0, "unknown": 0.0},
        database="ig",
    )
    assert isinstance(ferrous, PhaseFe3Fe2)
    assert ferrous.ratio == 0.0

    ferric = phase_fe3_fe2_from_endmember_moles({"hmL": 1.0}, database="ig")
    assert isinstance(ferric, PhaseFe3Fe2Refusal)
    assert ferric.reason == "ferrous_absent"

    unknown = phase_fe3_fe2_from_endmember_moles(
        {"notAnEndmember": 1.0},
        database="ig",
    )
    assert isinstance(unknown, PhaseFe3Fe2Refusal)
    assert unknown.reason == "unverified_endmember"

    other_database = phase_fe3_fe2_from_endmember_wt_fraction(
        {"fa2L": 1.0},
        database="mp",
    )
    assert isinstance(other_database, PhaseFe3Fe2Refusal)
    assert other_database.reason == "unverified_database"

    no_iron = phase_fe3_fe2_from_endmember_moles({"q4L": 1.0}, database="ig")
    assert isinstance(no_iron, PhaseFe3Fe2Refusal)
    assert no_iron.reason == "no_iron"


def test_magemin_matlab_liquid_row_is_not_an_input_split_or_a_sum():
    mass_fa = ig_liquid_endmember_formula_mass_g_mol("fa2L")
    mass_hm = ig_liquid_endmember_formula_mass_g_mol("hmL")
    text = "\n".join(
        (
            "End-members fractions[wt fr]",
            "fa2L hmL fo2L -",
            f"liq {mass_fa:.5f} {mass_hm:.5f} 0.00000 -",
            "fa2L -",
            "ol 99.00000 -",
            "",
            "Site fractions:",
        )
    )
    decided = magemin_liquid_fe3_fe2_from_matlab(text, database="ig")
    assert isinstance(decided, PhaseFe3Fe2)
    assert decided.ratio == pytest.approx(0.25)
    assert decided.ratio != pytest.approx(99.0)

    repeated = "\n".join(
        (
            "End-members fractions[wt fr]",
            "fa2L -",
            "liq 1.00000 -",
            "hmL -",
            "liq 1.00000 -",
        )
    )
    assert magemin_liquid_fe3_fe2_from_matlab(repeated, database="ig") is None
    assert magemin_liquid_fe3_fe2_from_matlab("no table", database="ig") is None

    unknown = magemin_liquid_fe3_fe2_from_matlab(
        "\n".join(
            (
                "End-members fractions[wt fr]",
                "mystery -",
                "liq 1.00000 -",
            )
        ),
        database="ig",
    )
    assert isinstance(unknown, PhaseFe3Fe2Refusal)
    assert unknown.reason == "unverified_endmember"

    other = magemin_liquid_fe3_fe2_from_matlab(text, database="mp")
    assert isinstance(other, PhaseFe3Fe2Refusal)
    assert other.reason == "unverified_database"


def test_zero_fe_ratio_survives_the_equilibrium_cache():
    result = EquilibriumResult(
        temperature_C=1400.0,
        pressure_bar=1.0,
        liquid_fraction=1.0,
    )
    result.fe_redox_split = {FE3_FE2_ATOM_RATIO_KEY: 0.0}
    restored = EquilibriumResult.from_engine_cache_value(
        result.to_engine_cache_value()
    )
    assert restored.fe_redox_split[FE3_FE2_ATOM_RATIO_KEY] == 0.0


def test_magemin_is_a_reported_split_engine_and_not_a_melts_buffer():
    assert Engine.MAGEMIN in _REPORTED_SPLIT_FE_REDOX_ENGINES
    assert Engine.MAGEMIN not in _MELTS_FE_REDOX_ENGINES
    assert Engine.VAPOROCK not in _REPORTED_SPLIT_FE_REDOX_ENGINES
    assert Engine.ALPHAMELTS in _MELTS_FE_REDOX_ENGINES
    assert Engine.THERMOENGINE in _MELTS_FE_REDOX_ENGINES


def test_magemin_scores_the_atom_ratio_and_not_an_echoed_input(monkeypatch):
    row = _borisov_row4()
    calls = _patch_melts_cell(
        monkeypatch,
        lambda _handle: _ok_cell(
            fe_redox_split={FE3_FE2_ATOM_RATIO_KEY: 0.25}
        ),
    )
    prediction = predict_with_engine(Engine.MAGEMIN, row, isolated=False)
    assert prediction.value == Decimal("0.25")
    assert prediction.refusal_reason is None
    assert prediction.coefficient_sources == ("magemin",)
    assert "kress91" not in (prediction.execution.call_evidence or "")
    assert calls

    calls.clear()
    ferrous_calls = _patch_melts_cell(
        monkeypatch,
        lambda _handle: _ok_cell(
            fe_redox_split={FE3_FE2_ATOM_RATIO_KEY: 0.0}
        ),
    )
    ferrous = predict_with_engine(Engine.MAGEMIN, row, isolated=False)
    assert ferrous.value == Decimal("0")
    assert ferrous.refusal_reason is None
    assert ferrous_calls

    _patch_melts_cell(monkeypatch, lambda _handle: _ok_cell())
    missing = predict_with_engine(Engine.MAGEMIN, row, isolated=False)
    assert missing.value is None
    assert missing.refusal_reason is RefusalReason.ENGINE_DOES_NOT_REPORT_FE_REDOX
    assert missing.execution.state is ExecutionState.PRODUCED
    assert missing.coefficient_sources == ("magemin",)
    assert "Kress and Carmichael 1991" not in " ".join(
        missing.coefficient_sources
    )


def test_melts_oxide_weights_still_win_over_an_atom_ratio(monkeypatch):
    row = _borisov_row4()
    expected = fe3_fe2_ratio_from_oxide_wt_pct(Decimal("1"), Decimal("9"))
    assert expected is not None
    _patch_melts_cell(
        monkeypatch,
        lambda _handle: _ok_cell(
            fe_redox_split={
                "FeO_wt_pct": 9.0,
                "Fe2O3_wt_pct": 1.0,
                FE3_FE2_ATOM_RATIO_KEY: 99.0,
            }
        ),
    )
    prediction = predict_with_engine(Engine.THERMOENGINE, row, isolated=False)
    assert prediction.value == expected
    assert prediction.value != Decimal("99")


def test_vaporock_fe_redox_still_does_not_equilibrate(monkeypatch):
    row = _borisov_row4()

    def _boom(*_args, **_kwargs):
        raise AssertionError("vaporock fe redox must not equilibrate")

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine",
        _boom,
    )
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.equilibrate_cell",
        _boom,
    )
    prediction = predict_with_engine(Engine.VAPOROCK, row, isolated=False)
    assert prediction.value is None
    assert prediction.refusal_reason is RefusalReason.ENGINE_DOES_NOT_REPORT_FE_REDOX
    assert prediction.execution.state is ExecutionState.UNSUPPORTED
    assert prediction.coefficient_sources == (
        "Kress and Carmichael 1991, doi:10.1007/BF00307328",
    )
