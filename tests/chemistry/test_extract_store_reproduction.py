"""Store-driven single-species reproduction battery (t-512).

Parameterized suite GENERATED from the extract store: for every ADOPTED
(priority-winner) observation of type ``psat_series`` / ``rate_series`` /
``activity_coefficient`` / ``alpha`` / ``gibbs_table`` / ``transition_point``,
run the engine at the observation's own conditions and record reproduction
residuals against the stated (or documented default) error budget.

Follows the shape of ``test_kems_reproduction.py`` + ``test_langmuir_knudsen.py``:
shared comparison records, typed status vocabulary, no silent pass on
engine refusal. Negative results (mismatches, large residual dex) are
FINDINGs for ``docs/model-limitations.md`` — tolerances are never weakened.

**Regression gate (P1 fix):** each comparable point pins ``residual_dex`` in
``extract_store_reproduction_residual_baselines.yaml``. A residual that moves
outside its band is RED; a residual that stays put keeps reporting FINDING
(the residual IS the result). The rollup test diffs a temp regeneration
against the committed ``docs/model-limitations.md`` — it does not write the
artifact it validates.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

import simulator.diagnostic_helpers.extract_reproduction as extract_reproduction
from simulator.diagnostic_helpers.extract_reproduction import (
    COMPARISON_STATUSES,
    CONDENSED_FORM_STATES,
    DEFAULT_PSAT_UNCERTAINTY,
    DEFAULT_TRANSITION_POINT_UNCERTAINTY,
    MODEL_LIMITATIONS_PATH,
    ORDERING_VERDICT_STATUSES,
    RAIL_COMPARABLE_SYSTEM_CLASSES,
    RAIL_INCOMPARABLE_SYSTEM_CLASSES,
    RAIL_TARGET_CONDENSED_FORM,
    ROLLUP_BEGIN,
    ROLLUP_END,
    SCORING_STATUSES,
    SELF_AGREEMENT_STATUS,
    TARGET_TYPES,
    AdoptedObservation,
    append_rollup_to_model_limitations,
    coverage_summary,
    evaluate_all,
    evaluate_observation,
    extract_rollup_section,
    form_correction_delta_log10_alpha,
    format_rollup_markdown,
    geometry_assumption_text,
    is_typed_skip,
    load_adopted_observations,
    load_vapor_pressure_data,
    motzfeldt_available,
    observation_condensed_form_state,
    observation_form_transition_context,
    observation_is_sossi_fegley_2018_table2,
    observation_system_class,
    parse_ordering_claim,
    parse_published_gamma_range,
    self_agreement_excluded,
    rail_alpha_comparability,
    rail_condensed_form_comparability,
    rail_system_class_comparability,
    residual_dex,
    residual_K,
    resolve_chamber_pressure_pa,
    resolve_pO2_bar,
    resolve_uncertainty,
    rollup_species_error_bars,
)
from simulator.diagnostic_helpers.reproduction_compare import compare_values

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_LIMITATIONS = REPO_ROOT / "docs" / "model-limitations.md"
BASELINES_PATH = (
    Path(__file__).resolve().parent / "extract_store_reproduction_residual_baselines.yaml"
)
# Escape hatch: write the committed rollup section (off by default).
REGEN_ENV = "RPS_T512_REGEN_ROLLUP"


def _record_pin_key(record) -> tuple[str, str]:
    return (str(record.case_id), str(record.observable_id))


def _baseline_pin_key(point: dict) -> tuple[str, str]:
    return (str(point["case_id"]), str(point["key"]))


@pytest.fixture(scope="module")
def vapor_pressure_data() -> dict:
    return load_vapor_pressure_data()


@pytest.fixture(scope="module")
def adopted_observations() -> list[AdoptedObservation]:
    return load_adopted_observations()


@pytest.fixture(scope="module")
def battery_evaluations(adopted_observations, vapor_pressure_data):
    return [
        evaluate_observation(obs, vapor_pressure_data=vapor_pressure_data)
        for obs in adopted_observations
    ]


@pytest.fixture(scope="module")
def residual_baselines() -> dict:
    raw = yaml.safe_load(BASELINES_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    points = raw.get("points") or []
    keys = [_baseline_pin_key(p) for p in points]
    assert len(keys) == len(set(keys)), "duplicate residual baseline identity"
    by_key = {key: point for key, point in zip(keys, points, strict=True)}
    assert by_key, f"empty residual baselines at {BASELINES_PATH}"
    return {"meta": raw, "by_key": by_key}


def test_store_yields_adopted_target_type_observations(
    adopted_observations: list[AdoptedObservation],
) -> None:
    """Battery includes every family and all KEMS sources before precedence."""

    assert adopted_observations, "extract store produced zero ADOPTED observations"
    types = {obs.obs_type for obs in adopted_observations}
    assert types == TARGET_TYPES
    kems = [obs for obs in adopted_observations if obs.source_id.startswith("kems-")]
    # B1 harvest + class-tagged fence rows expanded the KEMS surface; keep the
    # count live-derived so a silent shrink is RED without hard-coding B1 IDs.
    # 2026-08-26 corpus integration: Sossi remine + metadata completion added
    # kems-source observations (kems-012 alone now carries 53 adopted rows).
    assert len(kems) == 198
    assert len({obs.source_id for obs in kems}) == 20
    for obs in adopted_observations:
        assert obs.is_priority_winner or obs.adoption_basis == "mass_spec_extract"
        assert obs.source_id
        assert obs.observation_id
        assert obs.species_id


def test_geometry_assumption_is_stated_when_motzfeldt_absent() -> None:
    text = geometry_assumption_text()
    if not motzfeldt_available():
        assert "motzfeldt.py absent" in text
        assert "pure-component" in text or "unit-activity" in text
    else:
        assert "motzfeldt.py available" in text


def test_default_uncertainty_is_documented_per_observation() -> None:
    class _Stub:
        uncertainty = None
        disagreement_dex = None

    unc = resolve_uncertainty(_Stub(), kind_hint="psat")  # type: ignore[arg-type]
    assert unc["defaulted"] is True
    assert unc["kind"] == DEFAULT_PSAT_UNCERTAINTY["kind"]
    assert unc["value"] == DEFAULT_PSAT_UNCERTAINTY["value"]
    assert "rationale" in unc

    tp_unc = resolve_uncertainty(_Stub(), kind_hint="transition_point")  # type: ignore[arg-type]
    assert tp_unc["defaulted"] is True
    assert tp_unc["kind"] == "absolute"
    assert tp_unc["value"] == DEFAULT_TRANSITION_POINT_UNCERTAINTY["value"]
    assert tp_unc["kind"] != "log10_decades"


def test_engine_refusal_never_silent_pass_and_never_bare_failure() -> None:
    """Synthetic unsupported species must yield typed gap status, not match."""

    obs = AdoptedObservation(
        species_id="ZzNotASpecies",
        source_id="fixture-source",
        observation_id="fixture_psat",
        obs_type="psat_series",
        review_status="draft",
        phase="solid",
        regime=None,
        standard_state=None,
        T_range_K=(1000.0, 1200.0),
        units="Pa",
        uncertainty=None,
        locator={"note": "fixture"},
        values={
            "points": [{"T_K": 1100.0, "p_Pa": 1.0}],
            "gas_species": "ZzNotASpecies",
        },
        equipment={},
        disagreement_dex=None,
        is_priority_winner=True,
        geometry_assumption=geometry_assumption_text(),
    )
    evaluation = evaluate_observation(obs, vapor_pressure_data=load_vapor_pressure_data())
    assert evaluation.records, "refusal must still emit a comparison record"
    assert all(r.status != "match" for r in evaluation.records)
    assert all(r.status in COMPARISON_STATUSES for r in evaluation.records)
    assert evaluation.skip_reason is not None
    assert is_typed_skip(evaluation.skip_reason) or evaluation.skip_reason


def _alpha_fixture(
    *,
    species_id: str,
    observation_id: str,
    phase: str | None,
    values: dict,
    condensed_form: dict | None = None,
) -> AdoptedObservation:
    return AdoptedObservation(
        species_id=species_id,
        source_id="fixture-source",
        observation_id=observation_id,
        obs_type="alpha",
        review_status="draft",
        phase=phase,
        regime=None,
        standard_state=None,
        T_range_K=(1700.0, 1700.0),
        units="dimensionless",
        uncertainty=None,
        locator={"note": "fixture"},
        values=values,
        equipment={},
        disagreement_dex=None,
        is_priority_winner=True,
        geometry_assumption=geometry_assumption_text(),
        condensed_form=condensed_form,
    )


def test_pure_element_alpha_is_not_comparable_to_silicate_melt_rail() -> None:
    """Safarian-class pure Si α must not residual-pin the melt carrier."""

    assert "pure_element_condensed" in RAIL_INCOMPARABLE_SYSTEM_CLASSES
    assert "silicate_melt" in RAIL_COMPARABLE_SYSTEM_CLASSES
    obs = _alpha_fixture(
        species_id="Si",
        observation_id="fixture_pure_si_alpha",
        phase="pure_elemental_Si_liquid",
        values={
            "alpha": 1.0,
            "system_class": "pure_element_condensed",
            "transformation_class": "congruent_no_transformation",
            "material": "pure_elemental_Si",
        },
        condensed_form={"state": "liquid_melt", "metastable": False},
    )
    ok, sc, reason = rail_system_class_comparability(obs)
    assert ok is False
    assert sc == "pure_element_condensed"
    assert reason == "not_comparable_system_class:pure_element_condensed"
    evaluation = evaluate_observation(obs, vapor_pressure_data=load_vapor_pressure_data())
    assert evaluation.skip_reason == (
        "typed-refusal:not_comparable_system_class:pure_element_condensed"
    )
    assert all(r.status == "out-of-domain" for r in evaluation.records)
    assert not any(r.status in {"match", "mismatch"} for r in evaluation.records)


def test_silicate_melt_alpha_remains_comparable() -> None:
    obs = _alpha_fixture(
        species_id="Fe",
        observation_id="fixture_melt_fe_alpha",
        phase="silicate_melt",
        values={
            "alpha": 0.02,
            "system_class": "silicate_melt",
            "transformation_class": "redox_reduction_required",
            "material": "FCMAS_silicate_melt",
        },
        condensed_form={"state": "liquid_melt", "metastable": False},
    )
    ok, sc, reason = rail_system_class_comparability(obs)
    assert ok is True
    assert sc == "silicate_melt"
    assert reason is None
    assert observation_system_class(obs) == "silicate_melt"
    form_ok, form_state, form_skip, _ = rail_condensed_form_comparability(obs)
    assert form_ok is True
    assert form_state == RAIL_TARGET_CONDENSED_FORM
    assert form_skip is None
    pin_ok, pin_skip, _ = rail_alpha_comparability(obs)
    assert pin_ok is True
    assert pin_skip is None


@pytest.mark.parametrize("species", ("Ca", "Ti", "Na"))
def test_marked_ceiling_receipt_survives_alpha_report_runtime(
    species,
    monkeypatch,
) -> None:
    captured_runtime: list[dict] = []

    def _capture_compare_values(**kwargs):
        captured_runtime.append(dict(kwargs["runtime"]))
        return compare_values(**kwargs)

    monkeypatch.setattr(
        extract_reproduction,
        "compare_values",
        _capture_compare_values,
    )
    obs = _alpha_fixture(
        species_id=species,
        observation_id=f"fixture_{species.lower()}_ceiling_alpha",
        phase="silicate_melt",
        values={
            "alpha": 1.0,
            "system_class": "silicate_melt",
            "transformation_class": "redox_reduction_required",
            "material": "fixture_silicate_melt",
        },
        condensed_form={"state": "liquid_melt", "metastable": False},
    )

    evaluation = evaluate_observation(
        obs,
        vapor_pressure_data=load_vapor_pressure_data(),
    )

    assert evaluation.records
    assert any(
        runtime.get("alpha_context", {}).get("alpha_authority_status")
        == "analytical_upper_bound"
        for runtime in captured_runtime
    )
    assert {record.status for record in evaluation.records} == {"assumed-input"}
    assert evaluation.skip_reason == (
        "typed-refusal:analytical_upper_bound_not_measurement"
    )
    assert not any(
        record.status in {"match", "mismatch"} for record in evaluation.records
    )


def test_crystalline_form_is_not_pin_bearing_even_when_class_comparable() -> None:
    """Costa-class solid olivine α must not residual-pin the liquid-melt rail."""

    assert "crystalline" in CONDENSED_FORM_STATES
    obs = _alpha_fixture(
        species_id="Fe",
        observation_id="fixture_olivine_fe_alpha",
        phase="solid_solution_olivine",
        values={
            "alpha": 0.02,
            "system_class": "solid_solution_silicate",
            "material": "Fo93Fa7_olivine",
        },
        condensed_form={
            "state": "crystalline",
            "polymorph_name": "olivine",
            "metastable": False,
            "solution_character": "solid_solution",
            "basis": "explicit_author",
        },
    )
    class_ok, sc, _ = rail_system_class_comparability(obs)
    assert class_ok is True
    assert sc == "solid_solution_silicate"
    form_ok, state, reason, _ = rail_condensed_form_comparability(obs)
    assert form_ok is False
    assert state == "crystalline"
    assert reason == "not_comparable_condensed_form:crystalline"
    pin_ok, pin_skip, _ = rail_alpha_comparability(obs)
    assert pin_ok is False
    assert pin_skip == "not_comparable_condensed_form:crystalline"
    evaluation = evaluate_observation(obs, vapor_pressure_data=load_vapor_pressure_data())
    assert evaluation.skip_reason == (
        "typed-refusal:not_comparable_condensed_form:crystalline"
    )
    assert all(r.status == "out-of-domain" for r in evaluation.records)
    assert not any(r.status in {"match", "mismatch"} for r in evaluation.records)


def test_unresolved_form_fail_closed() -> None:
    obs = _alpha_fixture(
        species_id="Na",
        observation_id="fixture_yu_unresolved",
        phase="silicate_melt",
        values={"alpha": 0.1, "system_class": "silicate_melt"},
        condensed_form={"state": "unresolved", "metastable": True, "basis": "mixed_evidence"},
    )
    form_ok, state, reason, _ = rail_condensed_form_comparability(obs)
    assert form_ok is False
    assert state == "unresolved"
    assert reason == "form_unresolved"
    assert observation_condensed_form_state(obs) == "unresolved"


def test_form_correction_algebra_sign_and_units() -> None:
    """Δlog10 α = (ν_c/ν_g) · ΔG / (R T ln 10); 10 kJ/mol @ 1700 K ≈ 0.307 dex."""

    import math

    R = 8.314462618
    T = 1700.0
    delta_g = -10000.0  # solid below Tm: G_s - G_l < 0
    got = form_correction_delta_log10_alpha(
        T_K=T, delta_G_o_minus_r_J_mol=delta_g, nu_c=1.0, nu_g=1.0
    )
    expected = delta_g / (R * T * math.log(10.0))
    assert abs(got - expected) < 1e-12
    assert got < 0.0  # solid→liquid equivalent α shrinks
    assert abs(abs(got) - 0.307) < 0.005  # ~0.307 dex at 1700 K


def _form_gate_fixture(
    *,
    observation_id: str,
    values: dict,
    condensed_form: dict | None,
    T_range_K: tuple[float, float] | None = (1700.0, 1700.0),
    species_id: str = "Fe",
    phase: str = "silicate_melt",
) -> AdoptedObservation:
    return AdoptedObservation(
        species_id=species_id,
        source_id="fixture-source",
        observation_id=observation_id,
        obs_type="alpha",
        review_status="draft",
        phase=phase,
        regime=None,
        standard_state=None,
        T_range_K=T_range_K,
        units="dimensionless",
        uncertainty=None,
        locator={"note": "fixture"},
        values=values,
        equipment={},
        disagreement_dex=None,
        is_priority_winner=True,
        geometry_assumption=geometry_assumption_text(),
        condensed_form=condensed_form,
    )


def test_typed_liquid_claim_below_liquidus_fails_closed() -> None:
    """LABEL-TRUST (grok P1): a typed liquid_melt row whose whole T_range sits
    below its own typed liquidus must NOT pin the rail — downgrade to
    form_unresolved with the conflict named."""

    obs = _form_gate_fixture(
        observation_id="fixture_mislabeled_liquid",
        values={"alpha": 0.5, "system_class": "silicate_melt"},
        condensed_form={
            "state": "liquid_melt",
            "basis": "explicit_author",
            "transition_context": {"liquidus_K": 1823.0},
        },
        T_range_K=(1600.0, 1700.0),
    )
    assert observation_form_transition_context(obs) == {"liquidus_K": 1823.0}
    form_ok, state, reason, detail = rail_condensed_form_comparability(obs)
    assert form_ok is False
    assert state == "liquid_melt"
    assert reason == "form_unresolved:claim_conflict:liquid_melt_below_liquidus"
    assert detail["form_T_consistency"]["conflict"] == "liquid_melt_below_liquidus"
    pin_ok, pin_skip, axes = rail_alpha_comparability(obs)
    assert pin_ok is False
    assert pin_skip == "form_unresolved:claim_conflict:liquid_melt_below_liquidus"
    assert axes["form_T_consistency"]["checked"] is True
    evaluation = evaluate_observation(obs, vapor_pressure_data=load_vapor_pressure_data())
    assert evaluation.skip_reason == (
        "typed-refusal:form_unresolved:claim_conflict:liquid_melt_below_liquidus"
    )
    assert not any(r.status in {"match", "mismatch"} for r in evaluation.records)


def test_liquid_claim_straddling_liquidus_aggregate_fails_closed() -> None:
    """A scalar-α row claiming liquid_melt over a straddling T_range aggregates
    subliquidus measurements into the value — fail closed, no midpoint rescue."""

    obs = _form_gate_fixture(
        observation_id="fixture_straddling_liquid_scalar",
        values={"alpha": 0.5, "system_class": "silicate_melt"},
        condensed_form={
            "state": "liquid_melt",
            "basis": "explicit_author",
            "transition_context": {"liquidus_K": 1823.0},
        },
        T_range_K=(1773.15, 2073.15),
    )
    form_ok, _, reason, _ = rail_condensed_form_comparability(obs)
    assert form_ok is False
    assert reason == "form_unresolved:claim_conflict:liquid_melt_straddles_liquidus"
    evaluation = evaluate_observation(obs, vapor_pressure_data=load_vapor_pressure_data())
    # The synthetic midpoint (1923.15 K) is above the liquidus but is NOT a
    # measured point T — the aggregate verdict must stick.
    assert not any(r.status in {"match", "mismatch"} for r in evaluation.records)


def test_liquid_claim_consistent_with_typed_liquidus_passes() -> None:
    """Sossi-class row: liquid_melt at/above the typed liquidus cross-checks
    clean and stays pin-bearing (the P1 check is not a blanket demotion)."""

    obs = _form_gate_fixture(
        observation_id="fixture_consistent_liquid",
        values={"alpha": 0.5, "system_class": "silicate_melt"},
        condensed_form={
            "state": "liquid_melt",
            "basis": "explicit_author",
            "transition_context": {"liquidus_K": 1573.0},
        },
        T_range_K=(1573.15, 1823.15),
    )
    form_ok, state, reason, detail = rail_condensed_form_comparability(obs)
    assert form_ok is True
    assert state == RAIL_TARGET_CONDENSED_FORM
    assert reason is None
    assert detail["form_T_consistency"]["checked"] is True
    assert detail["form_T_consistency"]["conflict"] is None
    pin_ok, pin_skip, _ = rail_alpha_comparability(obs)
    assert pin_ok is True
    assert pin_skip is None


def test_straddling_partially_molten_whole_row_exclusion_is_labeled() -> None:
    """Richter-2002 b1 shape: straddling row whose payload is a single adopted
    α cannot split; the exclusion must carry the typed straddles_transition
    reason (never a silent unlabeled exclusion)."""

    obs = _form_gate_fixture(
        observation_id="fixture_straddling_partial_scalar",
        values={"alpha": 0.04, "system_class": "silicate_melt"},
        condensed_form={
            "state": "partially_molten",
            "basis": "temperature_inferred",
            "transition_context": {"liquidus_K": 1823.0},
        },
        T_range_K=(1773.15, 2073.15),
    )
    form_ok, _, reason, detail = rail_condensed_form_comparability(obs)
    assert form_ok is False
    assert reason == "not_comparable_condensed_form:partially_molten:straddles_transition"
    assert detail["form_T_consistency"]["straddles"] == "liquidus_K"
    # Synthetic midpoint (1923.15 K, molten side) must NOT rescue the row:
    # the adopted α is not a per-point measurement.
    form_ok_pt, _, reason_pt, _ = rail_condensed_form_comparability(obs, T_K=1923.15)
    assert form_ok_pt is False
    assert "straddles_transition" in reason_pt
    evaluation = evaluate_observation(obs, vapor_pressure_data=load_vapor_pressure_data())
    assert evaluation.skip_reason == (
        "typed-refusal:not_comparable_condensed_form:partially_molten:straddles_transition"
    )
    assert not any(r.status in {"match", "mismatch"} for r in evaluation.records)


def test_straddling_partially_molten_per_point_series_splits() -> None:
    """State-at-measurement split (grok P2): a genuine per-point (T, α) series
    on a partially_molten row keeps its molten-side points as pin-bearing
    liquid_melt; the subliquidus point stays excluded with a typed reason."""

    obs = _form_gate_fixture(
        observation_id="fixture_straddling_partial_series",
        values={
            "system_class": "silicate_melt",
            "series": [
                {"T_K": 1773.15, "alpha": 0.04},
                {"T_K": 1973.15, "alpha": 0.10},
                {"T_K": 2073.15, "alpha": 0.20},
            ],
        },
        condensed_form={
            "state": "partially_molten",
            "basis": "temperature_inferred",
            "transition_context": {"liquidus_K": 1823.0},
        },
        T_range_K=(1773.15, 2073.15),
    )
    # Observation-level verdict is still the labeled whole-row exclusion…
    form_ok, _, reason, _ = rail_condensed_form_comparability(obs)
    assert form_ok is False
    assert reason == "not_comparable_condensed_form:partially_molten:straddles_transition"
    # …but measured point Ts split at the boundary.
    ok_below, _, skip_below, _ = rail_condensed_form_comparability(
        obs, T_K=1773.15, point_T_is_measured=True
    )
    assert ok_below is False
    assert skip_below == "not_comparable_condensed_form:partially_molten"
    ok_above, state_above, skip_above, detail_above = rail_condensed_form_comparability(
        obs, T_K=1973.15, point_T_is_measured=True
    )
    assert ok_above is True
    assert state_above == RAIL_TARGET_CONDENSED_FORM
    assert skip_above is None
    assert detail_above["form_point_resolution"] == (
        "partially_molten_point_liquid_side_of_liquidus"
    )
    evaluation = evaluate_observation(obs, vapor_pressure_data=load_vapor_pressure_data())
    by_T = {r.coordinate["temperature_K"]: r for r in evaluation.records}
    assert by_T[1973.15].status in {"match", "mismatch"}
    assert by_T[2073.15].status in {"match", "mismatch"}
    assert by_T[1773.15].status == "out-of-domain"


def test_liquid_claim_per_point_below_liquidus_is_excluded() -> None:
    """Per-point LABEL-TRUST: a liquid_melt-claimed series keeps molten-side
    points but fails subliquidus points closed with the conflict named."""

    obs = _form_gate_fixture(
        observation_id="fixture_liquid_series_partial_conflict",
        values={
            "system_class": "silicate_melt",
            "series": [
                {"T_K": 1773.15, "alpha": 0.04},
                {"T_K": 1973.15, "alpha": 0.10},
            ],
        },
        condensed_form={
            "state": "liquid_melt",
            "basis": "explicit_author",
            "transition_context": {"liquidus_K": 1823.0},
        },
        T_range_K=(1773.15, 1973.15),
    )
    ok_below, _, skip_below, _ = rail_condensed_form_comparability(
        obs, T_K=1773.15, point_T_is_measured=True
    )
    assert ok_below is False
    assert skip_below == "form_unresolved:claim_conflict:liquid_melt_below_liquidus"
    evaluation = evaluate_observation(obs, vapor_pressure_data=load_vapor_pressure_data())
    by_T = {r.coordinate["temperature_K"]: r for r in evaluation.records}
    assert by_T[1973.15].status in {"match", "mismatch"}
    assert by_T[1773.15].status == "out-of-domain"


def test_dual_axis_skip_records_both_reasons() -> None:
    """Wetzel shape (grok P2): class ∧ form both exclude — the ledger primary
    reason must show both axes, not silently prefer class over form."""

    obs = _form_gate_fixture(
        observation_id="fixture_dual_axis_skip",
        phase="solid_sio_film_growth",
        values={"alpha": 0.5, "system_class": "solid_film_growth"},
        condensed_form={"state": "glass_amorphous", "basis": "explicit_author"},
    )
    class_ok, _, class_skip = rail_system_class_comparability(obs)
    form_ok, _, form_skip, _ = rail_condensed_form_comparability(obs)
    assert class_ok is False and form_ok is False
    pin_ok, pin_skip, axes = rail_alpha_comparability(obs)
    assert pin_ok is False
    assert pin_skip == f"{class_skip}+{form_skip}"
    assert pin_skip == (
        "not_comparable_system_class:solid_film_growth"
        "+not_comparable_condensed_form:glass_amorphous"
    )
    assert axes["skip_reasons_all"] == [class_skip, form_skip]
    evaluation = evaluate_observation(obs, vapor_pressure_data=load_vapor_pressure_data())
    assert evaluation.skip_reason == f"typed-refusal:{pin_skip}"
    assert "glass_amorphous" in evaluation.skip_reason


def test_wetzel_stale_duplicate_hits_system_class_and_form_gate() -> None:
    observation = next(
        row
        for row in load_adopted_observations()
        if row.source_id == "wetzel-gail-2013-sio-arrhenius"
        and row.observation_id == "wetzel_gail_2013_sio_arrhenius"
    )
    evaluation = evaluate_observation(
        observation,
        vapor_pressure_data=load_vapor_pressure_data(),
    )
    assert not any(
        record.status in {"match", "mismatch"} for record in evaluation.records
    )
    assert "not_comparable_system_class:solid_film_growth" in evaluation.skip_reason
    assert "not_comparable_condensed_form:glass_amorphous" in evaluation.skip_reason


def test_solid_claim_above_liquidus_conflicts_fail_closed() -> None:
    """A crystalline claim entirely above its typed liquidus contradicts its
    own transitions — downgrade to form_unresolved naming the conflict."""

    obs = _form_gate_fixture(
        observation_id="fixture_solid_above_liquidus",
        values={"alpha": 0.5, "system_class": "silicate_melt"},
        condensed_form={
            "state": "crystalline",
            "basis": "explicit_author",
            "transition_context": {"liquidus_K": 2000.0},
        },
        T_range_K=(2100.0, 2200.0),
    )
    form_ok, state, reason, _ = rail_condensed_form_comparability(obs)
    assert form_ok is False
    assert state == "crystalline"
    assert reason == "form_unresolved:claim_conflict:crystalline_above_liquidus"


def test_untyped_transition_context_keeps_face_value_trust() -> None:
    """Without typed transitions there is nothing to cross-check — the claim
    is taken at face value (documented residual trust gap, not a silent one:
    runtime simply carries no form_T_consistency detail)."""

    obs = _form_gate_fixture(
        observation_id="fixture_untransitions_liquid",
        values={"alpha": 0.5, "system_class": "silicate_melt"},
        condensed_form={"state": "liquid_melt", "basis": "explicit_author"},
    )
    assert observation_form_transition_context(obs) == {}
    form_ok, _, reason, detail = rail_condensed_form_comparability(obs)
    assert form_ok is True
    assert reason is None
    assert detail is None


def test_comparison_vocabulary_matches_kems_precedent() -> None:
    record = compare_values(
        case_id="c",
        source_id="s",
        observable_id="o",
        species="Fe",
        coordinate={"temperature_K": 1800.0},
        expected_value=1.0,
        expected_uncertainty={"kind": "absolute", "value": 0.1},
        actual_value=1.05,
        units="Pa",
        evidence_scope="extract-store-adopted",
        source_locator={},
        recipe={},
        observation={},
        runtime={},
    )
    assert record.status == "match"
    assert set(record.as_dict()) >= {
        "case_id",
        "source_id",
        "observable_id",
        "species",
        "expected_value",
        "actual_value",
        "residual",
        "status",
    }


def test_total_chamber_pressure_is_not_relabelled_as_pO2() -> None:
    """Total mbar vacuum converts to Pa but never masquerades as oxygen fugacity."""

    obs = AdoptedObservation(
        species_id="X",
        source_id="s",
        observation_id="o",
        obs_type="psat_series",
        review_status=None,
        phase=None,
        regime=None,
        standard_state=None,
        T_range_K=None,
        units=None,
        uncertainty=None,
        locator={},
        values={},
        equipment={"chamber_pressure": {"value": 2.0, "units": "mbar"}},
        disagreement_dex=None,
        is_priority_winner=True,
        geometry_assumption=geometry_assumption_text(),
    )
    pO2, note = resolve_pO2_bar(obs)
    pressure_pa, pressure_note = resolve_chamber_pressure_pa(obs)
    assert pO2 is None
    assert "pO2_boundary" in note
    assert pressure_pa == pytest.approx(200.0)
    assert "mbar→Pa" in pressure_note


def test_A_Pa_runtime_coefficients_are_parsed_not_no_usable_payload() -> None:
    """P2: A_Pa-only Antoine runtime coefficients must parse (Yb Hab64 shape)."""

    from simulator.diagnostic_helpers.extract_reproduction import (
        _literature_pressure_points,
    )

    obs = AdoptedObservation(
        species_id="Yb_metal_and_YbO",
        source_id="habermann-daane-1964",
        observation_id="Hab64_Yb_metal_antoine",
        obs_type="psat_series",
        review_status=None,
        phase="solid",
        regime=None,
        standard_state=None,
        T_range_K=(623.0, 931.0),
        units="Pa",
        uncertainty=None,
        locator={},
        values={
            "gas_species": "Yb",
            "runtime_form": "log10(P_Pa) = A_Pa - B/(T+C)",
            "runtime_coefficients": {
                "A_Pa": 10.4199,
                "B": 7696.0,
                "C": 0.0,
            },
        },
        equipment={},
        disagreement_dex=None,
        is_priority_winner=True,
        geometry_assumption=geometry_assumption_text(),
    )
    points, skip, drops = _literature_pressure_points(obs)
    assert skip is None, f"A_Pa payload mis-labelled: {skip}"
    assert points and len(points) >= 2
    assert all(p["P_Pa"] > 0 for p in points)
    del drops


def test_point_level_drops_emit_gap_records_not_silent() -> None:
    """P3: mixed series with rate-only rows must not vanish without a record."""

    obs = AdoptedObservation(
        species_id="Fe",
        source_id="fixture",
        observation_id="mixed_alpha_rate",
        obs_type="rate_series",
        review_status=None,
        phase="silicate_melt",
        regime=None,
        standard_state=None,
        T_range_K=(1973.0, 1973.0),
        units="alpha",
        uncertainty=None,
        locator={},
        values={
            "series": [
                {"T_K": 1973.0, "alpha": 0.23, "sigma": 0.02},
                {"T_K": 2073.0, "rate": 1.0e-6},  # drop: rate without alpha
            ],
            "system_class": "silicate_melt",
        },
        equipment={},
        disagreement_dex=None,
        is_priority_winner=True,
        geometry_assumption=geometry_assumption_text(),
        condensed_form={"state": "liquid_melt", "metastable": False},
    )
    evaluation = evaluate_observation(obs, vapor_pressure_data=load_vapor_pressure_data())
    drop_records = [
        r
        for r in evaluation.records
        if "dropped_point" in r.observable_id or "point-drop" in str(r.coordinate)
    ]
    assert drop_records, "rate-only point must surface as a gap record"
    assert any(
        "point drop" in n or "rate_or_flux_without_alpha" in n
        for n in evaluation.runtime_notes
    )
    comparable = [r for r in evaluation.records if r.status in {"match", "mismatch"}]
    assert comparable, "alpha-bearing point should still compare"


def _observation_params() -> list[pytest.ParameterSet]:
    try:
        observations = load_adopted_observations()
    except SystemExit as exc:
        return [
            pytest.param(
                None,
                id=f"store-load-failed:{exc}",
            )
        ]
    if not observations:
        return [
            pytest.param(
                None,
                id="empty-store",
            )
        ]
    return [pytest.param(obs, id=obs.param_id()) for obs in observations]


@pytest.mark.parametrize("obs", _observation_params())
def test_adopted_observation_reproduction(
    obs: AdoptedObservation | None,
    vapor_pressure_data,
    residual_baselines,
) -> None:
    """Per-observation engine reproduction within stated/default error budget.

    Mismatches are FINDINGs (recorded on the evaluation), not excuses to
    widen the budget. Engine refusals and non-numeric payloads skip with a
    typed reason — never a silent pass.

    Comparable points additionally pin residual_dex (or residual_K for
    transition_point) against the checked-in baseline: a residual that
    moves outside its band is RED (regression); a residual that stays put
    keeps reporting FINDING.
    """

    if obs is None:
        pytest.skip("no observation")
    evaluation = evaluate_observation(obs, vapor_pressure_data=vapor_pressure_data)

    # Never silent: every adopted observation yields ≥1 record OR a typed skip.
    assert evaluation.records or evaluation.skip_reason, (
        f"{obs.param_id()}: silent evaluation (no records, no skip reason)"
    )
    for record in evaluation.records:
        assert record.status in COMPARISON_STATUSES

    mismatches = [r for r in evaluation.records if r.status == "mismatch"]
    matches = [r for r in evaluation.records if r.status == "match"]
    ordering_scored = [
        r for r in evaluation.records if r.status in {"ordering-pass", "ordering-fail"}
    ]
    comparable = mismatches + matches + ordering_scored

    if comparable:
        # FINDING text required for every mismatch (residual IS the result).
        if mismatches or any(r.status == "ordering-fail" for r in comparable):
            assert evaluation.findings, (
                f"{obs.param_id()}: mismatch without FINDING text "
                f"(statuses={[r.status for r in evaluation.records]})"
            )
            # SiO extrapolated α must be disclosed on FINDING lines (P2).
            for record in mismatches:
                # runtime is digested; check FINDING text for extrapolated tag
                # when the baseline marks extrapolated.
                pin = residual_baselines["by_key"].get(_record_pin_key(record))
                if pin and pin.get("extrapolated"):
                    assert any(
                        "extrapolated: true" in f for f in evaluation.findings
                    ), f"{record.observable_id}: extrapolated α FINDING missing tag"

        # Pin residual_dex for every comparable point covered by baselines.
        # Points without a baseline yet are a RED signal (new comparable
        # point must be pinned, not silently accepted).
        by_key = residual_baselines["by_key"]
        for record in comparable:
            key = _record_pin_key(record)
            assert key in by_key, (
                f"{obs.param_id()}: comparable point {key!r} has no residual "
                f"baseline — pin residual_dex in {BASELINES_PATH.name} "
                f"(residual_dex={residual_dex(record)!r}, residual={record.residual!r})"
            )
            pin = by_key[key]
            assert pin.get("status", record.status) == record.status, (
                f"{key}: live status {record.status!r} != pin status {pin.get('status')!r}"
            )
            if str(pin.get("residual_unit") or "") == "K":
                measured_K = residual_K(record)
                assert measured_K is not None, (
                    f"{key}: residual_K is None (expected={record.expected_value}, "
                    f"actual={record.actual_value}, units={record.units})"
                )
                expected_K = float(pin["residual_K"])
                band_K = float(pin["band_K"])
                assert abs(measured_K - expected_K) <= band_K, (
                    f"RESIDUAL REGRESSION {key}: residual_K moved "
                    f"measured={measured_K:.6g} pin={expected_K:.6g} "
                    f"band=±{band_K:g} K (Δ={abs(measured_K - expected_K):.6g}). "
                    f"actual={record.actual_value} expected_lit={record.expected_value} "
                    f"residual={record.residual}. The residual IS the result — if the "
                    f"engine changed honestly, update the pin with a mechanism comment; "
                    f"do not widen band_K to hide the move."
                )
                continue
            if record.status in {"ordering-pass", "ordering-fail"}:
                # Ordering pins the pair-count residual (0 ⇒ pass). residual_dex
                # is defined only when both sides are positive.
                if pin.get("residual") is not None and record.residual is not None:
                    band_r = float(pin.get("band_residual") or pin.get("band_dex") or 0.01)
                    assert abs(record.residual - float(pin["residual"])) <= band_r, (
                        f"RESIDUAL REGRESSION {key}: ordering residual moved "
                        f"measured={record.residual} pin={pin['residual']} "
                        f"band=±{band_r}"
                    )
                measured = residual_dex(record)
                if measured is None:
                    continue
            else:
                measured = residual_dex(record)
            assert measured is not None, (
                f"{key}: residual_dex is None (expected={record.expected_value}, "
                f"actual={record.actual_value})"
            )
            expected_dex = float(pin["residual_dex"])
            band = float(pin["band_dex"])
            assert abs(measured - expected_dex) <= band, (
                f"RESIDUAL REGRESSION {key}: residual_dex moved "
                f"measured={measured:.6g} pin={expected_dex:.6g} "
                f"band=±{band:g} (Δ={abs(measured - expected_dex):.6g}). "
                f"actual={record.actual_value} expected_lit={record.expected_value} "
                f"residual={record.residual}. The residual IS the result — if the "
                f"engine changed honestly, update the pin with a mechanism comment; "
                f"do not widen band_dex to hide the move."
            )
            # Optional residual (signed) pin when present.
            if pin.get("residual") is not None and record.residual is not None:
                band_r = float(pin.get("band_residual") or band)
                assert abs(record.residual - float(pin["residual"])) <= band_r, (
                    f"RESIDUAL REGRESSION {key}: signed residual moved "
                    f"measured={record.residual} pin={pin['residual']} "
                    f"band=±{band_r}"
                )
        return

    # Only gap statuses remain — assert the typed roadmap entry; do not hide it
    # behind pytest's skipped-test count.
    statuses = {r.status for r in evaluation.records}
    assert is_typed_skip(evaluation.skip_reason), (
        f"{obs.param_id()}: no comparable points and no typed skip; "
        f"statuses={sorted(statuses)} reason={evaluation.skip_reason!r}"
    )
    assert evaluation.skip_reasons


def test_pinned_residuals_cover_all_live_comparable_points(
    battery_evaluations,
    residual_baselines,
) -> None:
    """Baseline file and live battery must agree on the comparable set."""

    live_keys = {
        _record_pin_key(r)
        for ev in battery_evaluations
        for r in ev.records
        if r.status in SCORING_STATUSES
    }
    assert len(live_keys) == sum(
        r.status in SCORING_STATUSES
        for ev in battery_evaluations
        for r in ev.records
    ), "duplicate live residual identity"
    pin_keys = set(residual_baselines["by_key"])
    assert live_keys == pin_keys, (
        f"baseline/live comparable-key mismatch: "
        f"live_only={sorted(live_keys - pin_keys)} "
        f"pin_only={sorted(pin_keys - live_keys)}"
    )


def test_measured_rate_series_executes_hkl(monkeypatch) -> None:
    import simulator.diagnostic_helpers.extract_reproduction as er

    real_hkl = er.langmuir_molar_flux
    calls: list[tuple[float, float]] = []

    def _spy(T_K, p_eq_pa, p_bulk_pa, alpha, *, molar_mass_kg_mol):
        calls.append((float(T_K), float(p_eq_pa)))
        return real_hkl(
            T_K,
            p_eq_pa,
            p_bulk_pa,
            alpha,
            molar_mass_kg_mol=molar_mass_kg_mol,
        )

    monkeypatch.setattr(er, "langmuir_molar_flux", _spy)
    obs = next(
        row
        for row in load_adopted_observations()
        if row.observation_id == "richter_2007_mg_rate_series_geometry"
    )
    evaluation = evaluate_observation(obs, vapor_pressure_data=load_vapor_pressure_data())
    comparable = [r for r in evaluation.records if r.status in {"match", "mismatch"}]
    assert len(calls) == 4
    assert not comparable
    assert {r.status for r in evaluation.records} == {"assumed-input"}
    # 2026-08-26: metadata completion supplied this row's melt composition, so
    # the refusal ladder progressed to the NEXT missing condition — the same
    # pO2 gap that blocks the DeMaria psat series. Still correctly refused.
    assert evaluation.skip_reason == "typed-refusal:missing_condition:pO2_boundary"
    assert "typed-refusal:missing_condition:pO2_boundary" in evaluation.skip_reasons
    assert all(r.observable_id.endswith(":rate") for r in evaluation.records)


def test_numeric_activity_executes_melt_activity_model(monkeypatch) -> None:
    import simulator.diagnostic_helpers.extract_reproduction as er

    real_activity = er.melt_oxide_activity
    calls: list[str] = []

    def _spy(parent_oxide, account_mol, **kwargs):
        calls.append(str(parent_oxide))
        return real_activity(parent_oxide, account_mol, **kwargs)

    monkeypatch.setattr(er, "melt_oxide_activity", _spy)
    obs = next(
        row
        for row in load_adopted_observations()
        if row.observation_id == "demaria_1971_fe_lunar_basalt_kems_main_cell"
    )
    evaluation = evaluate_observation(obs, vapor_pressure_data=load_vapor_pressure_data())
    comparable = [r for r in evaluation.records if r.status in {"match", "mismatch"}]
    assert calls == ["FeO"]
    assert not comparable
    assert {r.status for r in evaluation.records} == {"assumed-input"}
    assert (
        "typed-refusal:missing_capability:documented_melt_activity_coefficient:FeO"
        in evaluation.skip_reasons
    )


def test_psat_standard_state_selects_one_compatible_vapor_rail(monkeypatch) -> None:
    import simulator.diagnostic_helpers.extract_reproduction as er

    pure_calls: list[tuple[str, float]] = []
    melt_calls: list[tuple[str, float, float]] = []

    def _pure(species, T_K, vapor_pressure_data):
        pure_calls.append((str(species), float(T_K)))
        return 2.0, None

    def _melt(species, T_K, pO2_bar, vapor_pressure_data, **kwargs):
        melt_calls.append((str(species), float(T_K), float(pO2_bar)))
        return 3.0, None, {"provider_status": "ok"}

    monkeypatch.setattr(er, "_engine_pure_psat_pa", _pure)
    monkeypatch.setattr(er, "_engine_melt_psat_pa", _melt)
    common = dict(
        species_id="Mg",
        source_id="synthetic-standard-state",
        obs_type="psat_series",
        review_status="accepted",
        regime="equilibrium",
        T_range_K=(1800.0, 1800.0),
        units="Pa",
        uncertainty={"kind": "relative_fraction", "value": 0.1},
        locator={"record": "synthetic-standard-state"},
        equipment={},
        disagreement_dex=None,
        is_priority_winner=True,
        geometry_assumption="synthetic standard-state routing test",
    )
    pure_obs = AdoptedObservation(
        **common,
        observation_id="pure",
        phase="pure metal",
        standard_state="pure Mg metal",
        values={"points": [{"T_K": 1800.0, "p_Pa": 2.0}]},
    )
    melt_obs = AdoptedObservation(
        **common,
        observation_id="melt",
        phase="silicate melt",
        standard_state="silicate melt at stated pO2",
        values={
            "points": [{"T_K": 1800.0, "p_Pa": 3.0}],
            "composition_mol": {"MgO": 1.0},
            "pO2_bar": 1.0e-8,
        },
    )
    pure_eval = evaluate_observation(pure_obs, vapor_pressure_data={"metals": {}})
    assert pure_calls == [("Mg", 1800.0)]
    assert not melt_calls
    assert pure_eval.records[0].status == "match"

    melt_eval = evaluate_observation(melt_obs, vapor_pressure_data={"metals": {}})
    assert pure_calls == [("Mg", 1800.0)]
    assert melt_calls == [("Mg", 1800.0, 1.0e-8)]
    assert melt_eval.records[0].status == "match"


def test_engine_value_mutation_moves_residual_outside_band_goes_red(
    residual_baselines,
    monkeypatch,
) -> None:
    """P1 proof: 10× grounded_alpha mutation must trip the residual pin (RED).

    Reviewer reproduced the old defect: with only FINDING-text asserts the
    param test stayed GREEN under 10× Fe α (0.02 → 0.2). This test documents
    that the residual pin closes that hole.
    """

    import simulator.diagnostic_helpers.extract_reproduction as er
    import simulator.chemistry.langmuir_knudsen as lk

    real_grounded = lk.grounded_alpha

    def _ten_x(species: str, T_K: float):
        value, ctx = real_grounded(species, T_K)
        if value is None:
            return value, ctx
        return float(value) * 10.0, ctx

    monkeypatch.setattr(lk, "grounded_alpha", _ten_x)
    # extract_reproduction imports grounded_alpha by name — patch both.
    monkeypatch.setattr(er, "grounded_alpha", _ten_x)

    observations = [
        o
        for o in load_adopted_observations()
        if o.species_id == "Fe" and o.obs_type == "rate_series"
    ]
    assert observations, "Fe rate_series observation missing from store"
    obs = observations[0]
    evaluation = evaluate_observation(
        obs, vapor_pressure_data=load_vapor_pressure_data()
    )
    mismatches = [r for r in evaluation.records if r.status == "mismatch"]
    # Under 10×, Fe actual≈0.2 vs lit≈0.23 — may still mismatch on σ=0.02, or match.
    comparable = [
        r for r in evaluation.records if r.status in {"match", "mismatch"}
    ]
    assert comparable, "mutation must still produce comparable records"
    # Residual pin must fail for at least one Fe point.
    by_key = residual_baselines["by_key"]
    reds: list[str] = []
    for record in comparable:
        pin = by_key.get(_record_pin_key(record))
        if pin is None:
            continue
        measured = residual_dex(record)
        if measured is None:
            reds.append(f"{record.observable_id}: residual_dex=None under mutation")
            continue
        if abs(measured - float(pin["residual_dex"])) > float(pin["band_dex"]):
            reds.append(
                f"{record.observable_id}: measured_dex={measured:.4g} "
                f"pin={pin['residual_dex']:.4g} band=±{pin['band_dex']}"
            )
    assert reds, (
        "mutation proof FAILED: 10× grounded_alpha did not move residual_dex "
        "outside the pin band — the battery still has no red path. "
        f"records={[(r.observable_id, r.actual_value, residual_dex(r)) for r in comparable]}"
    )
    # Also assert the public param-style check would raise.
    with pytest.raises(AssertionError, match="RESIDUAL REGRESSION"):
        for record in comparable:
            pin = by_key[_record_pin_key(record)]
            measured = residual_dex(record)
            assert measured is not None
            assert abs(measured - float(pin["residual_dex"])) <= float(pin["band_dex"]), (
                f"RESIDUAL REGRESSION {record.observable_id}: residual_dex moved "
                f"measured={measured:.6g} pin={float(pin['residual_dex']):.6g} "
                f"band=±{float(pin['band_dex']):g}"
            )


def test_hkl_assumption_diagnostic_is_not_promoted_or_pinned(
    monkeypatch, residual_baselines
) -> None:
    import simulator.diagnostic_helpers.extract_reproduction as er

    real_hkl = er.langmuir_molar_flux

    def _ten_x(*args, **kwargs):
        return 10.0 * real_hkl(*args, **kwargs)

    monkeypatch.setattr(er, "langmuir_molar_flux", _ten_x)
    obs = next(
        row
        for row in load_adopted_observations()
        if row.observation_id == "richter_2007_mg_rate_series_geometry"
    )
    evaluation = evaluate_observation(obs, vapor_pressure_data=load_vapor_pressure_data())
    assert {record.status for record in evaluation.records} == {"assumed-input"}
    assert all(
        _record_pin_key(record) not in residual_baselines["by_key"]
        for record in evaluation.records
    )
    assert evaluation.skip_reasons


@pytest.mark.parametrize(
    ("observation_id", "skip_reason"),
    (
        (
            "stolyarova_1992_binary_wilson_model_parameters_table2",
            "typed-refusal:thermodynamic_model_parameter_not_activity_measurement",
        ),
        (
            "halwax_2024_cao_third_law_formation_enthalpy",
            "typed-refusal:pure_solid_thermochemistry_not_melt_activity",
        ),
        (
            "halwax_2024_mgo_third_law_formation_enthalpy",
            "typed-refusal:pure_solid_thermochemistry_not_melt_activity",
        ),
    ),
)
def test_recovered_gibbs_evidence_is_covered_but_never_pin_bearing(
    observation_id: str,
    skip_reason: str,
) -> None:
    observation = next(
        row
        for row in load_adopted_observations()
        if row.observation_id == observation_id
    )
    evaluation = evaluate_observation(
        observation,
        vapor_pressure_data=load_vapor_pressure_data(),
    )
    assert evaluation.records == []
    assert evaluation.skip_reason == skip_reason
    assert evaluation.skip_reasons == [skip_reason]


def test_transition_point_is_an_adopted_target_type(
    adopted_observations: list[AdoptedObservation],
) -> None:
    assert "transition_point" in TARGET_TYPES
    rows = [obs for obs in adopted_observations if obs.obs_type == "transition_point"]
    assert len(rows) == 64
    assert all(obs.is_priority_winner for obs in rows)
    kinds = {str(obs.values.get("property_kind")) for obs in rows}
    assert "normal_boiling_point" in kinds
    assert "melting_point" in kinds


def test_melting_point_is_typed_refusal_not_a_fabricated_psat(
    vapor_pressure_data,
) -> None:
    obs = next(
        row
        for row in load_adopted_observations()
        if row.observation_id == "Na_melting_point"
    )
    evaluation = evaluate_observation(obs, vapor_pressure_data=vapor_pressure_data)
    assert evaluation.skip_reason == "typed-refusal:no_engine_melting_point_model"
    assert evaluation.records
    assert all(r.status != "match" for r in evaluation.records)
    assert all(r.actual_value is None for r in evaluation.records)


def test_normal_boiling_point_inverts_antoine_in_kelvin(
    vapor_pressure_data,
) -> None:
    obs = next(
        row
        for row in load_adopted_observations()
        if row.observation_id == "Na_normal_boiling_point"
    )
    evaluation = evaluate_observation(obs, vapor_pressure_data=vapor_pressure_data)
    comparable = [r for r in evaluation.records if r.status in {"match", "mismatch"}]
    assert comparable, evaluation.skip_reason
    record = comparable[0]
    assert record.units == "K"
    assert residual_dex(record) is None
    assert residual_K(record) is not None
    # Sanity: Na NIST 1156 K vs sidecar inversion ~1179.58 K (short
    # extrapolation past the 924–1118 K certified window).
    assert record.expected_value == pytest.approx(1156.0)
    assert record.actual_value == pytest.approx(1179.584731, abs=1e-4)
    assert record.residual == pytest.approx(23.584731, abs=1e-4)


def test_silicate_melt_class_does_not_pin_a_pure_component_nbp(
    vapor_pressure_data,
) -> None:
    """Class gate is not weakened: a silicate-melt row is the wrong carrier."""

    obs = AdoptedObservation(
        species_id="Na",
        source_id="fixture-source",
        observation_id="fixture_nbp",
        obs_type="transition_point",
        review_status="draft",
        phase="silicate melt",
        regime=None,
        standard_state=None,
        T_range_K=None,
        units="K",
        uncertainty=None,
        locator={"note": "fixture"},
        values={
            "property_kind": "normal_boiling_point",
            "value_K": 1156.0,
            "system_class": "silicate_melt",
            "pressure_basis_Pa": 101325,
        },
        equipment={},
        disagreement_dex=None,
        is_priority_winner=True,
        geometry_assumption=geometry_assumption_text(),
    )
    evaluation = evaluate_observation(obs, vapor_pressure_data=vapor_pressure_data)
    assert evaluation.skip_reason is not None
    assert "not_comparable_system_class:silicate_melt" in evaluation.skip_reason
    assert all(r.status not in {"match", "mismatch"} for r in evaluation.records)


def test_battery_rollup_matches_committed_model_limitations(
    battery_evaluations,
    adopted_observations: list[AdoptedObservation],
    tmp_path,
) -> None:
    """Rollup must NOT self-heal the committed doc.

    Generate the section (and a full-file rewrite on a *temp* path) and
    compare against the committed ``docs/model-limitations.md`` rollup
    markers. Drift is RED with a regenerate instruction.

    Regen escape hatch (off by default)::

        RPS_T512_REGEN_ROLLUP=1 pytest tests/chemistry/test_extract_store_reproduction.py\\
            -k test_battery_rollup_matches_committed_model_limitations
    """

    assert battery_evaluations
    rows = rollup_species_error_bars(battery_evaluations)
    assert rows, "rollup produced no species rows"
    species = {row["species"] for row in rows}
    assert species == {obs.species_id for obs in adopted_observations}

    generated_section = format_rollup_markdown(
        rows, evaluations=battery_evaluations
    )
    assert generated_section.startswith(ROLLUP_BEGIN)
    assert generated_section.endswith(ROLLUP_END)
    assert "Extract-store single-species reproduction battery (t-512)" in generated_section
    assert "Observations:" in generated_section
    assert "Coverage by observation type" in generated_section
    assert "Coverage by species" in generated_section
    assert "Coverage by source" in generated_section
    assert "Combined propagated uncertainty" in generated_section
    assert "not computable (engine uncertainty unavailable)" in generated_section
    for row in rows:
        assert row["species"] in generated_section

    # SiO extrapolated FINDINGs must be disclosed in the generated section.
    if any(r["species"] == "SiO" and r.get("n_mismatch") for r in rows):
        assert "extrapolated: true" in generated_section

    # Write to TEMP only — never the committed path during the default test.
    temp_doc = tmp_path / "model-limitations.md"
    # Seed temp with the committed file so marker replace path is exercised.
    temp_doc.write_text(MODEL_LIMITATIONS.read_text(encoding="utf-8"), encoding="utf-8")
    append_rollup_to_model_limitations(
        rows,
        evaluations=battery_evaluations,
        path=temp_doc,
    )
    temp_text = temp_doc.read_text(encoding="utf-8")
    assert temp_text.count(ROLLUP_BEGIN) == 1
    temp_section = extract_rollup_section(temp_text)
    assert temp_section is not None
    assert temp_section.strip() == generated_section.strip()

    # Idempotent replace on the temp path.
    append_rollup_to_model_limitations(
        rows,
        evaluations=battery_evaluations,
        path=temp_doc,
    )
    assert temp_doc.read_text(encoding="utf-8").count(ROLLUP_BEGIN) == 1

    if os.environ.get(REGEN_ENV, "").strip() in {"1", "true", "yes"}:
        # Explicit escape hatch: rewrite the committed deliverable.
        append_rollup_to_model_limitations(
            rows,
            evaluations=battery_evaluations,
            path=MODEL_LIMITATIONS,
        )
        return

    committed = MODEL_LIMITATIONS.read_text(encoding="utf-8")
    committed_section = extract_rollup_section(committed)
    assert committed_section is not None, (
        f"committed {MODEL_LIMITATIONS} lacks rollup markers; "
        f"regenerate with {REGEN_ENV}=1"
    )
    if committed_section.strip() != generated_section.strip():
        # Produce a short diagnostic without dumping the whole doc.
        c_lines = committed_section.strip().splitlines()
        g_lines = generated_section.strip().splitlines()
        drift = []
        for i, (a, b) in enumerate(zip(c_lines, g_lines)):
            if a != b:
                drift.append(f"  line {i}: committed={a!r}\n           generated={b!r}")
                if len(drift) >= 8:
                    break
        if len(c_lines) != len(g_lines):
            drift.append(
                f"  length committed={len(c_lines)} generated={len(g_lines)}"
            )
        pytest.fail(
            "docs/model-limitations.md t-512 rollup drifted from the live battery.\n"
            "The test does NOT rewrite the committed file (no self-heal).\n"
            f"Regenerate with:\n"
            f"  {REGEN_ENV}=1 pytest tests/chemistry/test_extract_store_reproduction.py "
            f"-k test_battery_rollup_matches_committed_model_limitations -n0\n"
            f"then review the diff and restage.\n"
            + "\n".join(drift)
        )

    # Committed path must be unchanged by this test (no side effect).
    assert MODEL_LIMITATIONS.read_text(encoding="utf-8") == committed


def test_evaluate_all_covers_every_adopted_observation(
    adopted_observations: list[AdoptedObservation],
    vapor_pressure_data,
) -> None:
    evaluations = evaluate_all(
        observations=adopted_observations,
        vapor_pressure_data=vapor_pressure_data,
    )
    assert len(evaluations) == len(adopted_observations)
    for ev in evaluations:
        for record in ev.records:
            assert record.status in COMPARISON_STATUSES
        # No silent pass: skip reason or at least one record.
        assert ev.records or ev.skip_reason


def test_coverage_ledger_is_observation_first_and_exact(
    battery_evaluations,
    adopted_observations: list[AdoptedObservation],
) -> None:
    coverage = coverage_summary(battery_evaluations)
    # 2026-08-11 evidence recovery: Gibbs tables are coverage-only typed skips;
    # qualitative rate bounds add rows without numeric residuals; Sossi Na
    # analytical ceilings remove four comparable points. Counts are live
    # battery, not hand-estimated.
    # 2026-08-26 corpus integration: five worker slices merged (DeMaria pO2,
    # Sossi remine, metadata completion, transition_point, observable paths)
    # plus the DOI-keyed self-agreement guard. Numbers are the LIVE merged
    # battery, measured after the merge — neither worker's own pinned view.
    assert coverage["observations"] == len(adopted_observations) == 299
    assert coverage["comparable"] == 42
    assert coverage["skipped"] == 257
    assert coverage["comparable"] + coverage["skipped"] == coverage["observations"]
    assert coverage["comparable_points"] == 90
    assert coverage["gap_points"] == 302
    assert all(reason.startswith("typed-refusal:") for reason in coverage["skip_reasons"])
    assert any(
        reason.startswith("typed-refusal:not_comparable_system_class:")
        for reason in coverage["skip_reasons"]
    )
    assert any(
        reason.startswith("typed-refusal:not_comparable_condensed_form:")
        or reason == "typed-refusal:form_unresolved"
        for reason in coverage["skip_reasons"]
    )

    by_type = {row["type"]: row for row in coverage["by_type"]}
    assert {
        key: (
            row["observations"],
            row["comparable"],
            row["skipped"],
            row["comparable_points"],
        )
        for key, row in by_type.items()
    } == {
        "activity_coefficient": (75, 1, 74, 1),
        "alpha": (60, 16, 44, 40),
        "gibbs_table": (24, 0, 24, 0),
        "psat_series": (19, 3, 16, 18),
        "rate_series": (57, 7, 50, 16),
        "transition_point": (64, 15, 49, 15),
    }
    by_family = {row["comparison_family"]: row for row in coverage["by_family"]}
    assert {
        key: (row["observations"], row["comparable"], row["comparable_points"])
        for key, row in by_family.items()
    } == {
        "activity_coefficient": (52, 1, 1),
        "activity_self_agreement": (9, 0, 0),
        "alpha": (60, 16, 40),
        "alpha_in_legacy_rate_series": (3, 3, 12),
        "gibbs_table": (24, 0, 0),
        "ordering_activity": (14, 0, 0),
        "ordering_bound": (17, 4, 4),
        "psat_series": (19, 3, 18),
        "rate_hkl": (36, 0, 0),
        "relative_volatility": (1, 0, 0),
        "transition_point": (64, 15, 15),
    }
    assert {row["species"] for row in coverage["by_species"]} == {
        obs.species_id for obs in adopted_observations
    }
    assert {row["source"] for row in coverage["by_source"]} == {
        obs.source_id for obs in adopted_observations
    }


def test_model_limitations_path_constant_matches_repo_doc() -> None:
    assert MODEL_LIMITATIONS_PATH.resolve() == MODEL_LIMITATIONS.resolve()


def test_published_gamma_range_parser_handles_latex_and_refuses_smashed_ocr() -> None:
    assert parse_published_gamma_range("0.28–0.37") == pytest.approx((0.28, 0.37))
    assert parse_published_gamma_range("0.02") == pytest.approx((0.02, 0.02))
    lo, hi = parse_published_gamma_range(r"$6.3 \times 10^{-5}$ – $7.1 \times 10^{-4}$")
    assert lo == pytest.approx(6.3e-5)
    assert hi == pytest.approx(7.1e-4)
    lo, hi = parse_published_gamma_range(r"$10^{-6}$ – $10^{-10}$")
    assert {lo, hi} == {1e-6, 1e-10}
    assert parse_published_gamma_range("1.2–20.80.3–5.20.05–1.65") is None


def test_self_agreement_is_keyed_on_table2_provenance_not_species_name() -> None:
    from simulator.chemistry.melt_activity import MELT_OXIDE_ACTIVITY_COEFFICIENTS

    table2 = AdoptedObservation(
        species_id="SiO2",
        source_id="kems-041-sossi-fegley-2018",
        observation_id="sossi_fegley_2018_table2_gamma_SiO2__SiO_2_",
        obs_type="activity_coefficient",
        review_status="draft",
        phase="complex_silicate_melt",
        regime="compiled_melt_activity",
        standard_state=None,
        T_range_K=(1573.0, 1773.0),
        units="dimensionless",
        uncertainty=None,
        locator={"table": "2"},
        values={"gamma_range_as_published": "0.9–1.1"},
        equipment={},
        disagreement_dex=None,
        is_priority_winner=True,
        geometry_assumption="fixture",
        condensed_form={"state": "liquid_melt", "metastable": False},
    )
    sio2 = MELT_OXIDE_ACTIVITY_COEFFICIENTS["SiO2"]
    assert observation_is_sossi_fegley_2018_table2(table2)
    assert self_agreement_excluded(table2, sio2.citation)
    na2o = MELT_OXIDE_ACTIVITY_COEFFICIENTS["Na2O"]
    assert not self_agreement_excluded(table2, na2o.citation)


def test_gamma_range_path_self_agreement_is_computed_and_never_scored() -> None:
    obs = AdoptedObservation(
        species_id="SiO2",
        source_id="kems-041-sossi-fegley-2018",
        observation_id="sossi_fegley_2018_table2_gamma_SiO2__SiO_2_",
        obs_type="activity_coefficient",
        review_status="draft",
        phase="complex_silicate_melt_CMAS",
        regime="compiled_melt_activity",
        standard_state=None,
        T_range_K=(1573.0, 1773.0),
        units="dimensionless",
        uncertainty=None,
        locator={"table": "2"},
        values={
            "quantity": "activity_coefficient_range",
            "oxide_formula_as_published": r"$SiO_2$",
            "gamma_range_as_published": "0.9–1.1",
            "system_class": "silicate_melt",
        },
        equipment={},
        disagreement_dex=None,
        is_priority_winner=True,
        geometry_assumption="fixture",
        condensed_form={"state": "liquid_melt", "metastable": False},
    )
    evaluation = evaluate_observation(obs, vapor_pressure_data=load_vapor_pressure_data())
    assert evaluation.records
    record = evaluation.records[0]
    assert record.status == SELF_AGREEMENT_STATUS
    assert record.expected_value is not None
    assert record.actual_value == pytest.approx(1.0)
    assert record.status not in SCORING_STATUSES
    assert evaluation.skip_reason == "typed-refusal:self_agreement_excluded"


def test_ordering_bound_emits_declared_verdict() -> None:
    obs = AdoptedObservation(
        species_id="Al",
        source_id="fixture-ordering",
        observation_id="al_not_lost_until_mg",
        obs_type="rate_series",
        review_status="draft",
        phase="silicate_melt",
        regime="langmuir_free_evaporation",
        standard_state=None,
        T_range_K=(1873.15, 2173.15),
        units="qualitative bound",
        uncertainty=None,
        locator={"note": "fixture"},
        values={
            "quantity": "qualitative_non_loss_bound",
            "system_class": "silicate_melt",
            "semantics": "bound_not_point_ordering",
            "component": "Al",
            "comparison_component": "Mg",
            "bound": "no_significant_loss_until_Mg_virtually_exhausted",
            "composition_wt_pct": {
                "SiO2": 45.0,
                "MgO": 10.0,
                "Al2O3": 15.0,
                "CaO": 12.0,
                "FeO": 18.0,
            },
        },
        equipment={},
        disagreement_dex=None,
        is_priority_winner=True,
        geometry_assumption="fixture",
        condensed_form={"state": "liquid_melt", "metastable": False},
    )
    claim = parse_ordering_claim(obs)
    assert claim is not None
    assert claim["kind"] == "later_than"
    evaluation = evaluate_observation(obs, vapor_pressure_data=load_vapor_pressure_data())
    assert evaluation.records
    record = evaluation.records[0]
    assert record.status in ORDERING_VERDICT_STATUSES | {"out-of-domain"}
    assert record.status != "unsupported-observable"


def test_published_cro_is_not_cr2o3_self_agreement() -> None:
    """CrO (Cr2+) is a different oxide from the engine's Cr2O3/CrO1.5 coefficient."""

    obs = AdoptedObservation(
        species_id="Cr",
        source_id="kems-041-sossi-fegley-2018",
        observation_id="sossi_fegley_2018_table2_gamma_Cr_CrO",
        obs_type="activity_coefficient",
        review_status="draft",
        phase="complex_silicate_melt_CAS",
        regime="compiled_melt_activity",
        standard_state=None,
        T_range_K=(1773.0, 1773.0),
        units="dimensionless",
        uncertainty=None,
        locator={"table": "2"},
        values={
            "quantity": "activity_coefficient_range",
            "oxide_formula_as_published": "CrO",
            "gamma_range_as_published": "1.9–7.2",
            "system_class": "silicate_melt",
        },
        equipment={},
        disagreement_dex=None,
        is_priority_winner=True,
        geometry_assumption="fixture",
        condensed_form={"state": "liquid_melt", "metastable": False},
    )
    evaluation = evaluate_observation(obs, vapor_pressure_data=load_vapor_pressure_data())
    assert evaluation.records[0].status != SELF_AGREEMENT_STATUS
    assert evaluation.skip_reason == (
        "typed-refusal:missing_capability:melt_activity_gamma:CrO"
    )


def test_clausing_payload_is_geometry_not_a_rate() -> None:
    obs = AdoptedObservation(
        species_id="Fe",
        source_id="fixture-clausing",
        observation_id="clausing_table",
        obs_type="rate_series",
        review_status="draft",
        phase="method_monograph",
        regime="kems_effusion",
        standard_state=None,
        T_range_K=(300.0, 300.0),
        units="dimensionless",
        uncertainty=None,
        locator={"table": "I"},
        values={
            "quantity": "clausing_factor_table",
            "points": [{"L_over_r": 1.0, "clausing_factor": 0.672}],
        },
        equipment={},
        disagreement_dex=None,
        is_priority_winner=True,
        geometry_assumption="fixture",
    )
    evaluation = evaluate_observation(obs, vapor_pressure_data=load_vapor_pressure_data())
    assert evaluation.skip_reason == (
        "typed-refusal:unsupported_observable:clausing_factor_not_species_rate"
    )
    assert evaluation.records
    assert evaluation.records[0].status == "unsupported-observable"
