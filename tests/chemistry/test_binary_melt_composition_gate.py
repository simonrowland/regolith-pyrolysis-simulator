"""d-006 binary-melt composition gate (SC-146 fix, not SC-17).

A binary melt (``system: K2O-SiO2``) is FULLY DETERMINED by one published
mole fraction; the second component is 1 - x by closure. The admissibility
gate must complete such a specification instead of refusing it as
``missing_condition:melt_composition``. A genuinely under-specified row
(ternary with one x, binary with no x, out-of-range or contradictory x)
must still refuse with the specific typed refusal.

Comparison basis: extracts publish the parent-oxide activity (a_K2O vs pure
liquid K2O); the engine returns the single-cation activity. Per
docs/chemistry-methods.md §3.1 the di-cation activity is the square of the
single-cation one, so activity comparisons land on the parent basis. Off the
gamma anchor temperature the record stays assumed-input (numeric, residual
recorded, never certified) — this second gate is pinned here so the d-006
admission cannot silently start certifying off-anchor comparisons.
"""

from __future__ import annotations

import math

import pytest

from simulator.chemistry.melt_activity import MELT_OXIDE_ACTIVITY_COEFFICIENTS
from simulator.diagnostic_helpers.extract_reproduction import (
    SCORING_STATUSES,
    AdoptedObservation,
    _melt_recipe_mol,
    evaluate_observation,
    load_vapor_pressure_data,
    residual_dex,
    geometry_assumption_text,
)


def _closure(values: dict):
    # Imported lazily so red-by-revert shows assertion failures on the
    # evaluation-level tests instead of a collection-time ImportError.
    from simulator.diagnostic_helpers.extract_reproduction import (
        _binary_system_closure_mol,
    )

    return _binary_system_closure_mol(values)


def _activity_obs(
    *,
    species_id: str,
    observation_id: str,
    standard_state: str,
    T_K: float,
    values: dict,
) -> AdoptedObservation:
    return AdoptedObservation(
        species_id=species_id,
        source_id="fixture-source",
        observation_id=observation_id,
        obs_type="activity_coefficient",
        review_status="draft",
        phase=f"{values.get('system', 'melt')} binary silicate melt",
        regime="experimental_melt_activity",
        standard_state=standard_state,
        T_range_K=(T_K, T_K),
        units="dimensionless activity",
        uncertainty={"note": "fixture: no stated uncertainty"},
        locator={"note": "fixture"},
        values=values,
        equipment={},
        disagreement_dex=None,
        is_priority_winner=True,
        geometry_assumption=geometry_assumption_text(),
        condensed_form={"state": "liquid_melt", "metastable": False,
                        "basis": "explicit_author"},
    )


def test_binary_closure_completes_single_mole_fraction() -> None:
    recipe, source, error = _closure(
        {"system": "K2O-SiO2", "x_SiO2_as_published": 0.5}
    )
    assert error is None
    assert recipe == {"SiO2": 0.5, "K2O": pytest.approx(0.5)}
    assert "proxy" not in source.lower() and "modeled" not in source.lower()

    # The published fraction may name the other component; closure is symmetric.
    recipe2, _, error2 = _closure(
        {"system": "Na2O-SiO2", "x_Na2O_as_published": 0.3}
    )
    assert error2 is None
    assert recipe2 == {"Na2O": 0.3, "SiO2": pytest.approx(0.7)}


def test_binary_closure_via_melt_recipe_gate() -> None:
    class _Obs:
        values = {"system": "K2O-SiO2", "x_SiO2_as_published": 0.63}
        phase = "K2O-SiO2 binary silicate melt"

    recipe, source, error = _melt_recipe_mol(_Obs())
    assert error is None
    assert recipe == {"SiO2": 0.63, "K2O": pytest.approx(0.37)}
    assert "binary system closure" in source


def test_genuinely_incomplete_compositions_still_refuse() -> None:
    # Ternary with one mole fraction: composition NOT determined.
    recipe, _, error = _closure(
        {"system": "CaO-Al2O3-SiO2", "x_SiO2_as_published": 0.5}
    )
    assert (recipe, error) == (None, None)  # falls through to the standard refusal

    class _Ternary:
        values = {"system": "CaO-Al2O3-SiO2", "x_SiO2_as_published": 0.5}
        phase = "CMAS melt"

    recipe, source, error = _melt_recipe_mol(_Ternary())
    assert recipe is None
    assert error == "missing_condition:melt_composition"

    # Binary with NO published mole fraction: still missing.
    class _BinaryNoX:
        values = {"system": "K2O-SiO2"}
        phase = "K2O-SiO2 binary silicate melt"

    recipe, _, error = _melt_recipe_mol(_BinaryNoX())
    assert recipe is None
    assert error == "missing_condition:melt_composition"

    # Out-of-range mole fraction: invalid, not completed.
    recipe, _, error = _closure(
        {"system": "K2O-SiO2", "x_SiO2_as_published": 1.2}
    )
    assert recipe is None
    assert error == "invalid_x_SiO2_as_published"

    # Both fractions published but not closing to 1: contradictory, refused.
    recipe, _, error = _closure(
        {"system": "K2O-SiO2", "x_SiO2_as_published": 0.6,
         "x_K2O_as_published": 0.6}
    )
    assert recipe is None
    assert error == "invalid_binary_mole_fraction_closure"


def test_under_specified_observation_keeps_typed_refusal() -> None:
    obs = _activity_obs(
        species_id="SiO2",
        observation_id="fixture_ternary_one_x",
        standard_state="liquid SiO2",
        T_K=1873.0,
        values={
            "activity": 0.4,
            "oxide_formula_as_published": "SiO2",
            "system": "CaO-Al2O3-SiO2",
            "x_SiO2_as_published": 0.5,
            "method_class": "measured_direct",
            "system_class": "silicate_melt",
        },
    )
    evaluation = evaluate_observation(obs, vapor_pressure_data=load_vapor_pressure_data())
    assert evaluation.skip_reason == "typed-refusal:missing_condition:melt_composition"
    assert "typed-refusal:missing_condition:melt_composition" in evaluation.skip_reasons
    assert not any(r.status in SCORING_STATUSES for r in evaluation.records)


def test_binary_sio2_activity_row_becomes_comparable() -> None:
    # ms2000-044 Na2O-SiO2 x_SiO2=0.709 T=1673 K row shape.
    obs = _activity_obs(
        species_id="SiO2",
        observation_id="fixture_binary_sio2_activity",
        standard_state="liquid SiO2",
        T_K=1673.0,
        values={
            "activity": 0.536,
            "oxide_formula_as_published": "SiO2",
            "system": "Na2O-SiO2",
            "x_SiO2_as_published": 0.709,
            "method_class": "measured_direct",
            "system_class": "silicate_melt",
        },
    )
    evaluation = evaluate_observation(obs, vapor_pressure_data=load_vapor_pressure_data())
    assert evaluation.skip_reason is None
    scored = [r for r in evaluation.records if r.status in SCORING_STATUSES]
    assert len(scored) == 1
    record = scored[0]
    assert record.expected_value == pytest.approx(0.536)
    # gamma_SiO2 = 1.0; single-cation X = 0.709 / (0.709 + 2*0.291).
    expected_x = 0.709 / (0.709 + 2.0 * 0.291)
    assert record.actual_value == pytest.approx(expected_x, rel=1e-9)
    assert residual_dex(record) == pytest.approx(
        abs(math.log10(expected_x / 0.536)), rel=1e-9
    )


def test_binary_alkali_activity_compares_on_parent_basis() -> None:
    # ms2000-044 K2O-SiO2 x_SiO2=0.5 T=1473 K row shape.
    obs = _activity_obs(
        species_id="K2O",
        observation_id="fixture_binary_k2o_activity",
        standard_state="liquid K2O",
        T_K=1473.0,
        values={
            "activity": 5.38e-8,
            "oxide_formula_as_published": "K2O",
            "system": "K2O-SiO2",
            "x_SiO2_as_published": 0.5,
            "method_class": "measured_direct",
            "system_class": "silicate_melt",
        },
    )
    evaluation = evaluate_observation(obs, vapor_pressure_data=load_vapor_pressure_data())
    records = [r for r in evaluation.records if r.actual_value is not None]
    assert len(records) == 1
    record = records[0]
    gamma = MELT_OXIDE_ACTIVITY_COEFFICIENTS["K2O"].gamma
    x_single = (2.0 * 0.5) / (0.5 + 2.0 * 0.5)
    a_single = gamma * x_single
    # Parent-basis identity: a_K2O = a_KO0.5 ** 2 (chemistry-methods §3.1).
    assert record.actual_value == pytest.approx(a_single**2, rel=1e-9)
    assert record.actual_value != pytest.approx(a_single, rel=1e-6)
    # Off the 1500 K gamma anchor the record is numeric but NOT certified.
    assert record.status == "assumed-input"
    assert evaluation.skip_reason is not None
    assert not any(r.status in SCORING_STATUSES for r in evaluation.records)


def test_anchor_temperature_alkali_row_is_scored_on_parent_basis() -> None:
    # Same row at the Na2O anchor T=1673 K: scored comparison, parent basis.
    obs = _activity_obs(
        species_id="Na2O",
        observation_id="fixture_binary_na2o_activity_anchor",
        standard_state="liquid Na2O",
        T_K=1673.0,
        values={
            "activity": 3.24e-8,
            "oxide_formula_as_published": "Na2O",
            "system": "Na2O-SiO2",
            "x_SiO2_as_published": 0.709,
            "method_class": "measured_direct",
            "system_class": "silicate_melt",
        },
    )
    evaluation = evaluate_observation(obs, vapor_pressure_data=load_vapor_pressure_data())
    scored = [r for r in evaluation.records if r.status in SCORING_STATUSES]
    assert len(scored) == 1
    gamma = MELT_OXIDE_ACTIVITY_COEFFICIENTS["Na2O"].gamma
    x_single = (2.0 * 0.291) / (0.709 + 2.0 * 0.291)
    assert scored[0].actual_value == pytest.approx((gamma * x_single) ** 2, rel=1e-9)
