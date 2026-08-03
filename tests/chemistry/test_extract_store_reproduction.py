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
    ROLLUP_BEGIN,
    ROLLUP_END,
    TARGET_TYPES,
    AdoptedObservation,
    append_rollup_to_model_limitations,
    evaluate_all,
    evaluate_observation,
    extract_rollup_section,
    format_rollup_markdown,
    geometry_assumption_text,
    is_typed_skip,
    load_adopted_observations,
    load_vapor_pressure_data,
    motzfeldt_available,
    residual_dex,
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
    by_key = {str(p["key"]): p for p in points}
    assert by_key, f"empty residual baselines at {BASELINES_PATH}"
    return {"meta": raw, "by_key": by_key}


def test_store_yields_adopted_target_type_observations(
    adopted_observations: list[AdoptedObservation],
) -> None:
    """Battery is store-driven: non-empty ADOPTED set of the three types."""

    assert adopted_observations, "extract store produced zero ADOPTED observations"
    types = {obs.obs_type for obs in adopted_observations}
    assert types <= TARGET_TYPES
    assert types & TARGET_TYPES
    for obs in adopted_observations:
        assert obs.is_priority_winner
        assert obs.source_id
        assert obs.observation_id
        assert obs.species_id


def test_geometry_assumption_is_stated_when_motzfeldt_absent() -> None:
    text = geometry_assumption_text()
    if not motzfeldt_available():
        assert "motzfeldt.py absent" in text
        assert "pure-component" in text or "unit-activity" in text
    else:
        assert "motzfeldt.py present" in text


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


def test_resolve_pO2_mbar_not_swallowed_by_bar_substring() -> None:
    """P3: ``"bar" in "mbar"`` must not leave mbar unconverted (1000× bug)."""

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
    assert pO2 == pytest.approx(0.002)
    assert "mbar" in note

    obs_bar = AdoptedObservation(
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
        equipment={"chamber_pressure": {"value": 2.0, "units": "bar"}},
        disagreement_dex=None,
        is_priority_winner=True,
        geometry_assumption=geometry_assumption_text(),
    )
    p_bar, note_bar = resolve_pO2_bar(obs_bar)
    assert p_bar == pytest.approx(2.0)
    assert "bar" in note_bar and "mbar" not in note_bar


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
                id="store-load-failed",
                marks=pytest.mark.skip(reason=f"extract store load failed: {exc}"),
            )
        ]
    if not observations:
        return [
            pytest.param(
                None,
                id="empty-store",
                marks=pytest.mark.skip(reason="no ADOPTED observations in store"),
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

    # Engine refusal / non-runnable payload → typed skip (pytest.skip), not fail.
    if evaluation.skip_reason and all(
        r.status
        in {
            "unsupported-observable",
            "unsupported-speciation",
            "out-of-domain",
            "assumed-input",
        }
        for r in evaluation.records
    ):
        pytest.skip(evaluation.skip_reason)

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
                pin = residual_baselines["by_key"].get(record.observable_id)
                if pin and pin.get("extrapolated"):
                    assert any(
                        "extrapolated: true" in f for f in evaluation.findings
                    ), f"{record.observable_id}: extrapolated α FINDING missing tag"

        # Pin residual_dex for every comparable point covered by baselines.
        # Points without a baseline yet are a RED signal (new comparable
        # point must be pinned, not silently accepted).
        by_key = residual_baselines["by_key"]
        for record in comparable:
            key = record.observable_id
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

    # Only gap statuses remain — treat as typed skip for collection clarity.
    statuses = {r.status for r in evaluation.records}
    pytest.skip(
        evaluation.skip_reason
        or f"no comparable points; statuses={sorted(statuses)}"
    )


def test_pinned_residuals_cover_all_live_comparable_points(
    battery_evaluations,
    residual_baselines,
) -> None:
    """Baseline file and live battery must agree on the comparable set."""

    live_keys = {
        r.observable_id
        for ev in battery_evaluations
        for r in ev.records
        if r.status in {"match", "mismatch"}
    }
    pin_keys = set(residual_baselines["by_key"])
    assert live_keys == pin_keys, (
        f"baseline/live comparable-key mismatch: "
        f"live_only={sorted(live_keys - pin_keys)} "
        f"pin_only={sorted(pin_keys - live_keys)}"
    )


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
        pin = by_key.get(record.observable_id)
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
            pin = by_key[record.observable_id]
            measured = residual_dex(record)
            assert measured is not None
            assert abs(measured - float(pin["residual_dex"])) <= float(pin["band_dex"]), (
                f"RESIDUAL REGRESSION {record.observable_id}: residual_dex moved "
                f"measured={measured:.6g} pin={float(pin['residual_dex']):.6g} "
                f"band=±{float(pin['band_dex']):g}"
            )


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
    assert "Comparable points:" in generated_section
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


def test_model_limitations_path_constant_matches_repo_doc() -> None:
    assert MODEL_LIMITATIONS_PATH.resolve() == MODEL_LIMITATIONS.resolve()
