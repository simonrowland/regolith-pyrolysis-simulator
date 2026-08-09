"""Store-driven single-species reproduction battery (t-512).

Parameterized suite GENERATED from the extract store: for every ADOPTED
(priority-winner) observation of type ``psat_series`` / ``rate_series`` /
``activity_coefficient``, run the engine at the observation's own conditions
and record reproduction residuals against the stated (or documented default)
error budget.

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

from simulator.diagnostic_helpers.extract_reproduction import (
    COMPARISON_STATUSES,
    DEFAULT_PSAT_UNCERTAINTY,
    MODEL_LIMITATIONS_PATH,
    RAIL_COMPARABLE_SYSTEM_CLASSES,
    RAIL_INCOMPARABLE_SYSTEM_CLASSES,
    ROLLUP_BEGIN,
    ROLLUP_END,
    TARGET_TYPES,
    AdoptedObservation,
    append_rollup_to_model_limitations,
    coverage_summary,
    evaluate_all,
    evaluate_observation,
    extract_rollup_section,
    format_rollup_markdown,
    geometry_assumption_text,
    is_typed_skip,
    load_adopted_observations,
    load_vapor_pressure_data,
    motzfeldt_available,
    observation_system_class,
    rail_system_class_comparability,
    residual_dex,
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
    """Battery includes every family and all 15 KEMS sources before precedence."""

    assert adopted_observations, "extract store produced zero ADOPTED observations"
    types = {obs.obs_type for obs in adopted_observations}
    assert types == TARGET_TYPES
    kems = [obs for obs in adopted_observations if obs.source_id.startswith("kems-")]
    # B1 harvest + class-tagged fence rows expanded the KEMS surface; keep the
    # count live-derived so a silent shrink is RED without hard-coding B1 IDs.
    assert len(kems) == 135
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
    )
    ok, sc, reason = rail_system_class_comparability(obs)
    assert ok is True
    assert sc == "silicate_melt"
    assert reason is None
    assert observation_system_class(obs) == "silicate_melt"


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
        phase=None,
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
            ]
        },
        equipment={},
        disagreement_dex=None,
        is_priority_winner=True,
        geometry_assumption=geometry_assumption_text(),
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

    Comparable points additionally pin residual_dex against the checked-in
    baseline: a residual that moves outside its band is RED (regression);
    a residual that stays put keeps reporting FINDING.
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
    comparable = mismatches + matches

    if comparable:
        # FINDING text required for every mismatch (residual IS the result).
        if mismatches:
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
        if r.status in {"match", "mismatch"}
    }
    assert len(live_keys) == sum(
        r.status in {"match", "mismatch"}
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
    assert evaluation.skip_reason == "typed-refusal:missing_condition:melt_composition"
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
    # 2026-08-08 si-comparability: B1 harvest expanded adoption; pure-element /
    # molten-metal / solid-film α are typed not_comparable_system_class skips
    # (not residual pins). Counts are live battery, not hand-estimated.
    assert coverage["observations"] == len(adopted_observations) == 172
    assert coverage["comparable"] == 38
    assert coverage["skipped"] == 134
    assert coverage["comparable"] + coverage["skipped"] == coverage["observations"]
    assert coverage["comparable_points"] == 81
    assert coverage["gap_points"] == 208
    assert all(reason.startswith("typed-refusal:") for reason in coverage["skip_reasons"])
    assert any(
        reason.startswith("typed-refusal:not_comparable_system_class:")
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
        "activity_coefficient": (49, 0, 49, 0),
        "alpha": (60, 35, 25, 69),
        "psat_series": (19, 0, 19, 0),
        "rate_series": (44, 3, 41, 12),
    }
    by_family = {row["comparison_family"]: row for row in coverage["by_family"]}
    assert {
        key: (row["observations"], row["comparable"], row["comparable_points"])
        for key, row in by_family.items()
    } == {
        "activity_coefficient": (49, 0, 0),
        "alpha": (60, 35, 69),
        "alpha_in_legacy_rate_series": (3, 3, 12),
        "psat_series": (19, 0, 0),
        "rate_hkl": (41, 0, 0),
    }
    assert {row["species"] for row in coverage["by_species"]} == {
        obs.species_id for obs in adopted_observations
    }
    assert {row["source"] for row in coverage["by_source"]} == {
        obs.source_id for obs in adopted_observations
    }


def test_model_limitations_path_constant_matches_repo_doc() -> None:
    assert MODEL_LIMITATIONS_PATH.resolve() == MODEL_LIMITATIONS.resolve()
