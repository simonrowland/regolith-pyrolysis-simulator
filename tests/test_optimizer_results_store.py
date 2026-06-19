from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, replace
import json
import math
import multiprocessing
import queue
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from simulator.corpus_version import current_corpus_version
from simulator.fidelity_vocabulary import FidelityVocabularyTranslationError
from simulator.optimize.evalspec import EvalSpec, cache_key, current_code_version
from simulator.optimize.evaluate import FailureCategory, RunReference, ScoredResult
from simulator.optimize.objective import ObjectiveValue, ObjectiveVector
from simulator.optimize.physics import GateMargin, ThresholdSpec
from simulator.optimize.result_scope import result_scope_payload, selector_where
from simulator.optimize.results_store import ResultStore, SCHEMA_VERSION


def _base_spec(**overrides: object) -> EvalSpec:
    data = {
        "recipe_id": "recipe-id",
        "feedstock_recipe_digest": "feedstock-recipe-digest",
        "feedstock_id": "lunar_mare_low_ti",
        "profile_id": "oxygen-yield-v1",
        "fidelity": "fast",
        "code_version": current_code_version(),
        "data_digests": {
            "setpoints": "setpoints-digest",
            "feedstocks": "feedstock-digest",
            "materials": "materials-digest",
            "vapor_pressures": "vapor-digest",
            "species_catalog": "species-catalog-digest",
            "profile": "profile-digest",
        },
        "chemistry_kernel": {
            "engine": "builtin",
            "allow_builtin_fallback": False,
            "pressure_Pa": 0.001,
        },
        "campaign": "C0",
        "hours": 24,
        "mass_kg": 1000.0,
        "additives_kg": {"CaO": 1.5},
        "track": "pyrolysis",
        "backend_name": "stub",
        "runtime_campaign_overrides": {"C0": {"hold_time_h": 1.0}},
    }
    data.update(overrides)
    return EvalSpec(**data)


def _margin(
    gate: str = "delivered_stream_purity",
    feasible: bool = True,
    *,
    margin: float | None = None,
    observed: float | None = None,
) -> GateMargin:
    return GateMargin(
        gate=gate,
        feasible=feasible,
        margin=margin if margin is not None else (0.25 if feasible else -0.25),
        threshold=ThresholdSpec(
            id=f"{gate}-threshold",
            value=0.95,
            units="fraction",
            source="profile",
            source_ref="test profile",
        ),
        observed=observed if observed is not None else (0.98 if feasible else 0.90),
        detail="test margin",
    )


def _objectives(oxygen: float = 10.0, energy: float = 2.0) -> ObjectiveVector:
    return ObjectiveVector(
        (
            ObjectiveValue("oxygen_kg", "maximize", oxygen, "kg", ordinal=0),
            ObjectiveValue("energy_kWh", "minimize", energy, "kWh", ordinal=1),
        )
    )


def _scored(
    spec: EvalSpec,
    *,
    candidate_id: str = "candidate-a",
    oxygen: float = 10.0,
    energy: float = 2.0,
    objectives: ObjectiveVector | None = None,
    margins: Mapping[str, GateMargin] | None = None,
    result_blob: dict[str, object] | None = None,
) -> ScoredResult:
    return ScoredResult(
        candidate_id=candidate_id,
        eval_spec=spec,
        cache_key=cache_key(spec),
        feasible=True,
        objectives=objectives or _objectives(oxygen, energy),
        feasibility_margins=margins or {"delivered_stream_purity": _margin()},
        failing_gates=(),
        run_reference=RunReference(
            status="ok",
            trace=result_blob
            or {"backend_status": "ok", "hours": [{"hour": 1, "oxygen_kg": oxygen}]},
            product_summary={"oxygen_kg": oxygen},
        ),
        notes=("stored",),
    )


def _eval_spec_payload(spec: EvalSpec) -> dict[str, object]:
    return {field.name: _plain(getattr(spec, field.name)) for field in fields(EvalSpec)}


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _process_store_writer(
    db_path: str,
    spec_payload: dict[str, object],
    start: multiprocessing.synchronize.Event,
    errors: multiprocessing.queues.Queue,
    offset: int,
    count: int,
) -> None:
    try:
        spec = EvalSpec(**spec_payload)
        store = ResultStore(
            db_path,
            current_code_version=spec.code_version,
            current_data_digests=spec.data_digests,
            busy_timeout_ms=10000,
        )
        start.wait(10)
        for idx in range(count):
            recipe_idx = offset + idx
            next_spec = replace(spec, recipe_id=f"process-{recipe_idx}")
            store.store(
                next_spec,
                _scored(
                    next_spec,
                    candidate_id=f"process-{recipe_idx}",
                    oxygen=float(recipe_idx),
                ),
                created_at=f"tp{recipe_idx}",
            )
    except BaseException as exc:  # pragma: no cover - asserted in parent process
        errors.put(repr(exc))


def _process_store_reader(
    db_path: str,
    spec_payload: dict[str, object],
    start: multiprocessing.synchronize.Event,
    errors: multiprocessing.queues.Queue,
    iterations: int,
) -> None:
    try:
        spec = EvalSpec(**spec_payload)
        store = ResultStore(
            db_path,
            current_code_version=spec.code_version,
            current_data_digests=spec.data_digests,
            busy_timeout_ms=10000,
        )
        start.wait(10)
        for _ in range(iterations):
            store.query(spec.feedstock_id, profile_id=spec.profile_id, fidelity=spec.fidelity)
    except BaseException as exc:  # pragma: no cover - asserted in parent process
        errors.put(repr(exc))


def _infeasible(spec: EvalSpec) -> ScoredResult:
    return ScoredResult(
        candidate_id="candidate-bad",
        eval_spec=spec,
        cache_key=cache_key(spec),
        feasible=False,
        failure_category=FailureCategory.INFEASIBLE_RECIPE,
        feasibility_margins={"delivered_stream_purity": _margin(feasible=False)},
        failing_gates=("delivered_stream_purity",),
        run_reference=RunReference(
            status="ok",
            trace={"backend_status": "ok", "hours": [{"hour": 1, "oxygen_kg": 0.0}]},
            product_summary={"oxygen_kg": 0.0},
        ),
    )


def _artifact_copy(scored: ScoredResult, **overrides: object) -> object:
    data = {
        "candidate_id": scored.candidate_id,
        "eval_spec": scored.eval_spec,
        "cache_key": scored.cache_key,
        "feasible": scored.feasible,
        "failure_category": scored.failure_category,
        "objectives": scored.objectives,
        "feasibility_margins": scored.feasibility_margins,
        "failing_gates": scored.failing_gates,
        "run_reference": scored.run_reference,
        "notes": scored.notes,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_round_trip_lossless_lookup(tmp_path) -> None:
    spec = _base_spec()
    scored = _scored(
        spec,
        result_blob={"backend_status": "ok", "hours": [{"hour": 1}], "status": "ok"},
    )
    store = ResultStore(
        tmp_path / "results.sqlite",
        current_code_version=spec.code_version,
        current_data_digests=spec.data_digests,
    )

    store.store(spec, scored, created_at="2026-05-31T00:00:00Z")

    loaded = store.lookup(spec)
    with sqlite3.connect(tmp_path / "results.sqlite") as conn:
        row = conn.execute(
            "SELECT corpus_version FROM results WHERE cache_key = ?",
            (cache_key(spec),),
        ).fetchone()
    assert loaded == scored
    assert loaded is not None
    assert row[0] == current_corpus_version()
    assert loaded.run_reference is not None
    assert loaded.run_reference.trace == {
        "backend_status": "ok",
        "hours": [{"hour": 1}],
        "status": "ok",
    }
    assert loaded.run_reference.product_summary == {"oxygen_kg": 10.0}


def test_lookup_deserializes_legacy_evalspec_digest_scope(tmp_path) -> None:
    spec = _base_spec()
    store = ResultStore(
        tmp_path / "results.sqlite",
        current_code_version=spec.code_version,
        current_data_digests=spec.data_digests,
    )
    store.store(
        spec,
        _scored(spec, candidate_id="stored", oxygen=1.0),
        created_at="2026-06-18T00:00:00Z",
    )
    key = cache_key(spec)
    with sqlite3.connect(tmp_path / "results.sqlite") as conn:
        payload = json.loads(
            conn.execute(
                "SELECT eval_spec FROM results WHERE cache_key = ?",
                (key,),
            ).fetchone()[0]
        )
        payload["data_digests"].pop("materials")
        payload["data_digests"].pop("species_catalog")
        conn.execute(
            "UPDATE results SET eval_spec = ? WHERE cache_key = ?",
            (json.dumps(payload), key),
        )

    loaded = store.fetch(key)

    assert loaded is not None
    assert loaded.eval_spec is not None
    assert loaded.eval_spec.data_digests["materials"] == (
        "legacy-missing-materials-digest"
    )
    assert loaded.eval_spec.data_digests["species_catalog"] == (
        "legacy-missing-species-catalog-digest"
    )


def test_strict_eval_spec_storage_omits_inactive_vapor_fallback_provider(tmp_path) -> None:
    spec = _base_spec(
        vapor_pressure_provider_id="vaporock",
        vapor_pressure_fallback_provider_id="builtin-vapor-pressure",
        allow_fallback_vapor=False,
        force_builtin_vapor_pressure=False,
    )
    store = ResultStore(
        tmp_path / "results.sqlite",
        current_code_version=spec.code_version,
        current_data_digests=spec.data_digests,
    )

    store.store(spec, _scored(spec), created_at="2026-06-15T00:00:00Z")

    with sqlite3.connect(tmp_path / "results.sqlite") as conn:
        row = conn.execute("SELECT eval_spec FROM results").fetchone()
    payload = json.loads(str(row[0]))
    loaded = store.lookup(spec)

    assert payload["vapor_pressure_provider_id"] == "vaporock"
    assert payload["allow_fallback_vapor"] is False
    assert payload["force_builtin_vapor_pressure"] is False
    assert "vapor_pressure_fallback_provider_id" not in payload
    assert loaded is not None
    assert loaded.eval_spec == spec


def test_result_store_eval_spec_omits_default_stage0_exit_stop(tmp_path) -> None:
    full_run = _base_spec(stop_at_stage0_exit=False)
    stage0_run = _base_spec(stop_at_stage0_exit=True)
    store = ResultStore(
        tmp_path / "results.sqlite",
        current_code_version=full_run.code_version,
        current_data_digests=full_run.data_digests,
    )

    store.store(full_run, _scored(full_run), created_at="2026-06-17T00:00:00Z")
    store.store(stage0_run, _scored(stage0_run), created_at="2026-06-17T00:00:01Z")

    with sqlite3.connect(tmp_path / "results.sqlite") as conn:
        rows = dict(
            conn.execute(
                "SELECT cache_key, eval_spec FROM results"
            ).fetchall()
        )

    full_payload = json.loads(rows[cache_key(full_run)])
    stage0_payload = json.loads(rows[cache_key(stage0_run)])

    assert "stop_at_stage0_exit" not in full_payload
    assert stage0_payload["stop_at_stage0_exit"] is True
    assert store.lookup(full_run).eval_spec == full_run
    assert store.lookup(stage0_run).eval_spec == stage0_run


def test_not_run_backend_labels_round_trip(tmp_path) -> None:
    spec = _base_spec()
    scored = _scored(
        spec,
        result_blob={
            "backend_status": "not_run",
            "backend_authoritative": False,
            "execution_status": "not_run",
        },
    )
    store = ResultStore(
        tmp_path / "results.sqlite",
        current_code_version=spec.code_version,
        current_data_digests=spec.data_digests,
    )

    store.store(spec, scored, created_at="2026-05-31T00:00:00Z")

    loaded = store.lookup(spec)
    assert loaded is not None
    assert loaded.run_reference is not None
    assert loaded.run_reference.backend_status == "not_run"
    assert loaded.run_reference.backend_authoritative is False
    assert loaded.run_reference.trace == {
        "backend_status": "not_run",
        "backend_authoritative": False,
        "execution_status": "not_run",
    }


def test_lookup_rejects_poisoned_run_reference_canonical_fields(tmp_path) -> None:
    spec = _base_spec()
    db_path = tmp_path / "results.sqlite"
    store = ResultStore(
        db_path,
        current_code_version=spec.code_version,
        current_data_digests=spec.data_digests,
    )
    store.store(spec, _scored(spec), created_at="2026-05-31T00:00:00Z")

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT run_reference FROM results WHERE cache_key = ?",
            (cache_key(spec),),
        ).fetchone()
        payload = json.loads(row[0])
        payload.update(
            {
                "backend_name": "stub",
                "backend_status": "ok",
                "backend_authoritative": True,
                "certification_allowed": True,
            }
        )
        conn.execute(
            "UPDATE results SET run_reference = ? WHERE cache_key = ?",
            (json.dumps(payload), cache_key(spec)),
        )

    with pytest.raises(FidelityVocabularyTranslationError, match="certification_allowed"):
        store.lookup(spec)


def test_composition_target_metric_and_evalspec_metadata_round_trip(tmp_path) -> None:
    spec = _base_spec(
        target_spec_id="pc-glass-clear",
        target_spec_digest="target-digest",
        target_maturity={"mode": "campaign_hours", "campaign": "C2B", "hours": 24},
        target_provenance={
            "thermal_window": "C2B window 1260-1480 C",
            "composition_window": {
                "oxides": {
                    "Fe2O3": {
                        "tier": "clear_container",
                        "needs_experiment": True,
                        "min": 0.0,
                        "max": 1.0,
                    }
                }
            }
        },
    )
    objectives = ObjectiveVector(
        (
            ObjectiveValue(
                "composition_target:pc-glass-clear",
                "maximize",
                1.0,
                "score_0_1",
                ordinal=0,
            ),
        )
    )
    store = ResultStore(
        tmp_path / "results.sqlite",
        current_code_version=spec.code_version,
        current_data_digests=spec.data_digests,
    )

    store.store(
        spec,
        _scored(spec, objectives=objectives),
        created_at="2026-06-10T00:00:00Z",
    )

    loaded = store.lookup(spec)
    assert loaded is not None
    assert loaded.eval_spec is not None
    assert loaded.eval_spec.target_spec_id == "pc-glass-clear"
    assert loaded.eval_spec.target_spec_digest == "target-digest"
    assert loaded.eval_spec.target_maturity["campaign"] == "C2B"
    row = loaded.eval_spec.target_provenance["composition_window"]["oxides"]["Fe2O3"]
    assert row["tier"] == "clear_container"
    assert row["needs_experiment"] is True
    assert loaded.eval_spec.target_provenance["thermal_window"] == "C2B window 1260-1480 C"
    assert loaded.objectives is not None
    assert loaded.objectives.as_mapping()["composition_target:pc-glass-clear"] == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("bad_result", "message"),
    (
        (
            lambda spec: replace(_scored(spec), cache_key=None),
            "result artifact missing cache_key",
        ),
        (
            lambda spec: _artifact_copy(_scored(spec), objectives=None),
            "result artifact missing objectives",
        ),
        (
            lambda spec: replace(_infeasible(spec), failure_category=None),
            "result artifact missing failure_category",
        ),
        (
            lambda spec: replace(_scored(spec), feasibility_margins={}),
            "result artifact missing feasibility_margins",
        ),
        (
            lambda spec: replace(
                _scored(spec),
                run_reference=RunReference(status="ok", trace={"hours": [{"hour": 1}]}),
            ),
            "result artifact missing backend_status",
        ),
    ),
)
def test_store_result_artifact_missing_required_fields_raise_named(
    tmp_path,
    bad_result,
    message: str,
) -> None:
    spec = _base_spec()
    store = ResultStore(tmp_path / "results.sqlite")

    with pytest.raises(ValueError, match=message):
        store.store(spec, bad_result(spec), created_at="2026-01-01T00:00:00Z")

    assert store.lookup(spec) is None


def test_store_rejects_nan_margin_numbers(tmp_path) -> None:
    spec = _base_spec()
    store = ResultStore(tmp_path / "results.sqlite")
    scored = _scored(
        spec,
        margins={"delivered_stream_purity": _margin(margin=math.nan)},
    )

    with pytest.raises(ValueError, match="delivered_stream_purity.margin is NaN"):
        store.store(spec, scored, created_at="2026-01-01T00:00:00Z")

    assert store.lookup(spec) is None


def test_lookup_miss_returns_none(tmp_path) -> None:
    store = ResultStore(tmp_path / "results.sqlite")
    assert store.lookup(_base_spec()) is None


def test_result_store_mre_policy_collision_misses(tmp_path) -> None:
    off = _base_spec(c5_enabled=False, mre_max_voltage_V=0.0, mre_target_species="")
    enabled = _base_spec(c5_enabled=True, mre_max_voltage_V=0.0, mre_target_species="")
    si_target = _base_spec(
        c5_enabled=True,
        mre_max_voltage_V=1.45,
        mre_target_species="SiO2",
    )
    ti_target = _base_spec(
        c5_enabled=True,
        mre_max_voltage_V=1.70,
        mre_target_species="TiO2",
    )
    specs = (off, enabled, si_target, ti_target)
    store = ResultStore(tmp_path / "results.sqlite")

    assert len({cache_key(spec) for spec in specs}) == 4
    store.store(off, _scored(off, candidate_id="off"), created_at="t0")

    assert store.lookup(off) is not None
    assert store.lookup(enabled) is None
    assert store.lookup(si_target) is None
    assert store.lookup(ti_target) is None

    for idx, spec in enumerate(specs[1:], start=1):
        store.store(spec, _scored(spec, candidate_id=f"mre-{idx}"), created_at=f"t{idx}")

    with sqlite3.connect(tmp_path / "results.sqlite") as conn:
        row_count = conn.execute("SELECT count(*) FROM results").fetchone()[0]
    assert row_count == 4


def test_lab_overlay_result_scope_selector_isolates_non_empty_scopes(tmp_path) -> None:
    industrial = _base_spec(recipe_id="industrial")
    lab = replace(
        industrial,
        recipe_id="lab-robinot",
        lab_alpha_digest="robinot-alpha-v1",
        geometry_digest="robinot-geometry-v1",
        effective_exposed_area_m2=0.000314,
        area_basis="gram_lab_exposed_melt",
        oxide_vapor_ceiling_digest="oxide-ceiling-v1",
        sink_channel_evidence_digests={
            "deposit_gettering_diagnostic": "deposit-evidence-v1",
            "plume_oxidation_diagnostic": "plume-evidence-v1",
        },
    )
    sink_mode = replace(
        industrial,
        recipe_id="lab-sink-mode",
        chemistry_kernel={
            **industrial.chemistry_kernel,
            "oxygen_sink_channel_mode": "deposit_gettering_diagnostic",
        },
    )
    store = ResultStore(
        tmp_path / "results.sqlite",
        current_code_version=industrial.code_version,
        current_data_digests=industrial.data_digests,
    )

    store.store(industrial, _scored(industrial, candidate_id="industrial"), created_at="t0")
    store.store(lab, _scored(lab, candidate_id="lab"), created_at="t1")
    store.store(sink_mode, _scored(sink_mode, candidate_id="sink-mode"), created_at="t2")

    assert store.lookup(lab) is not None
    assert store.lookup(sink_mode) is not None
    assert [row.candidate_id for row in store.query(industrial.feedstock_id)] == [
        "sink-mode",
        "lab",
        "industrial"
    ]
    assert [
        row.candidate_id
        for row in store.query(lab.feedstock_id, result_scope=result_scope_payload(lab))
    ] == ["lab"]
    assert [
        row.candidate_id
        for row in store.query(
            sink_mode.feedstock_id,
            result_scope=result_scope_payload(sink_mode),
        )
    ] == ["sink-mode"]

    with sqlite3.connect(tmp_path / "results.sqlite") as conn:
        scopes = {
            row[0]: json.loads(row[1])
            for row in conn.execute(
                "SELECT candidate_id, result_scope FROM results ORDER BY candidate_id"
            )
        }
    assert scopes["industrial"] == {}
    assert scopes["lab"]["effective_exposed_area_m2"] == "0.000314000"
    assert scopes["lab"]["sink_channel_evidence_digests"] == {
        "deposit_gettering_diagnostic": "deposit-evidence-v1",
        "plume_oxidation_diagnostic": "plume-evidence-v1",
    }
    assert scopes["sink-mode"] == {
        "oxygen_sink_channel_mode": "deposit_gettering_diagnostic"
    }


def test_empty_result_scope_selector_matches_base_selector_byte_for_byte() -> None:
    expected_where = (
        "feedstock_id = ? AND code_version = ? AND data_digests = ? "
        "AND profile_id = ? AND fidelity = ?"
    )
    expected_params = (
        "lunar_mare_low_ti",
        "code-version",
        "data-digests-json",
        "oxygen-yield-v1",
        "fast",
    )
    base_kwargs = {
        "profile_id": "oxygen-yield-v1",
        "fidelity": "fast",
        "code_version": "code-version",
        "data_digests_json": "data-digests-json",
    }

    assert selector_where("lunar_mare_low_ti", **base_kwargs) == (
        expected_where,
        expected_params,
    )
    assert selector_where("lunar_mare_low_ti", result_scope={}, **base_kwargs) == (
        expected_where,
        expected_params,
    )
    assert selector_where(
        "lunar_mare_low_ti",
        result_scope_json="{}",
        **base_kwargs,
    ) == (
        expected_where,
        expected_params,
    )

    scoped_where, scoped_params = selector_where(
        "lunar_mare_low_ti",
        result_scope={"lab_alpha_digest": "robinot-alpha-v1"},
        **base_kwargs,
    )
    assert scoped_where == (
        "feedstock_id = ? AND code_version = ? AND data_digests = ? "
        "AND result_scope = ? AND profile_id = ? AND fidelity = ?"
    )
    assert scoped_params == (
        "lunar_mare_low_ti",
        "code-version",
        "data-digests-json",
        '{"lab_alpha_digest":"robinot-alpha-v1"}',
        "oxygen-yield-v1",
        "fast",
    )


def test_idempotent_upsert_latest_wins(tmp_path) -> None:
    spec = _base_spec()
    store = ResultStore(tmp_path / "results.sqlite")
    store.store(spec, _scored(spec, candidate_id="old", oxygen=1.0), created_at="t1")
    store.store(spec, _scored(spec, candidate_id="new", oxygen=2.0), created_at="t2")

    with sqlite3.connect(tmp_path / "results.sqlite") as conn:
        row_count = conn.execute("SELECT count(*) FROM results").fetchone()[0]

    loaded = store.lookup(spec)
    assert row_count == 1
    assert loaded is not None
    assert loaded.candidate_id == "new"
    assert loaded.objectives is not None
    assert loaded.objectives.as_mapping()["oxygen_kg"] == 2.0


def test_query_exact_selector_and_version_scoped(tmp_path) -> None:
    current = _base_spec(recipe_id="current-recipe")
    wrong_profile = replace(current, recipe_id="wrong-profile", profile_id="other")
    wrong_fidelity = replace(current, recipe_id="wrong-fidelity", fidelity="full")
    stale_code = replace(current, recipe_id="stale-code", code_version="old-version")
    stale_data = replace(
        current,
        recipe_id="stale-data",
        data_digests={**current.data_digests, "profile": "old-profile-digest"},
    )
    store = ResultStore(
        tmp_path / "results.sqlite",
        current_code_version=current.code_version,
        current_data_digests=current.data_digests,
    )

    for idx, spec in enumerate(
        (current, wrong_profile, wrong_fidelity, stale_code, stale_data)
    ):
        store.store(
            spec,
            _scored(spec, candidate_id=f"candidate-{idx}", oxygen=float(idx + 1)),
            created_at=f"t{idx}",
        )

    results = store.query(
        current.feedstock_id,
        profile_id=current.profile_id,
        fidelity=current.fidelity,
    )

    assert [result.candidate_id for result in results] == ["candidate-0"]
    assert store.lookup(stale_code) is not None


def test_selector_reads_require_explicit_scope_and_never_infer_current(tmp_path) -> None:
    stale = _base_spec(code_version="v1")
    current = replace(stale, recipe_id="current-recipe", code_version="v2")
    unscoped = ResultStore(tmp_path / "results.sqlite")
    unscoped.store(stale, _scored(stale, candidate_id="stale"), created_at="t1")

    with pytest.raises(ValueError, match="current code_version"):
        unscoped.query(stale.feedstock_id)
    with pytest.raises(ValueError, match="current code_version"):
        unscoped.best(stale.feedstock_id)

    scoped = ResultStore(
        tmp_path / "results.sqlite",
        current_code_version=current.code_version,
        current_data_digests=current.data_digests,
    )

    assert scoped.query(current.feedstock_id) == []
    assert scoped.best(current.feedstock_id) is None
    assert scoped.lookup(stale) is not None


def test_best_returns_best_feasible_with_deterministic_tie_break(tmp_path) -> None:
    spec_a = _base_spec(recipe_id="recipe-a")
    spec_b = replace(spec_a, recipe_id="recipe-b")
    spec_c = replace(spec_a, recipe_id="recipe-c")
    store = ResultStore(
        tmp_path / "results.sqlite",
        current_code_version=spec_a.code_version,
        current_data_digests=spec_a.data_digests,
    )
    store.store(spec_a, _scored(spec_a, candidate_id="a", oxygen=5.0), created_at="t1")
    clean_margins = {
        "delivered_stream_purity": _margin(),
        "coating": _margin("coating", margin=math.inf, observed=math.inf),
    }
    store.store(
        spec_b,
        _scored(spec_b, candidate_id="b", oxygen=7.0, margins=clean_margins),
        created_at="t2",
    )
    store.store(spec_c, _infeasible(spec_c), created_at="t3")

    best = store.best(spec_a.feedstock_id, objective_metric="oxygen_kg")
    loaded_clean = store.lookup(spec_b)

    assert best is not None
    assert best.candidate_id == "b"
    assert loaded_clean is not None
    assert loaded_clean.feasibility_margins["coating"].margin == math.inf
    assert loaded_clean.feasibility_margins["coating"].observed == math.inf


def test_best_defaults_to_profile_primary_and_honors_direction(tmp_path) -> None:
    spec_a = _base_spec(recipe_id="recipe-a")
    spec_b = replace(spec_a, recipe_id="recipe-b")
    store = ResultStore(
        tmp_path / "results.sqlite",
        current_code_version=spec_a.code_version,
        current_data_digests=spec_a.data_digests,
    )
    store.store(
        spec_a,
        _scored(
            spec_a,
            candidate_id="low-oxygen-low-energy",
            objectives=ObjectiveVector(
                (
                    ObjectiveValue("energy_kWh", "minimize", 1.0, "kWh", ordinal=1),
                    ObjectiveValue("oxygen_kg", "maximize", 5.0, "kg", ordinal=0),
                )
            ),
        ),
        created_at="t1",
    )
    store.store(
        spec_b,
        _scored(
            spec_b,
            candidate_id="high-oxygen-high-energy",
            objectives=ObjectiveVector(
                (
                    ObjectiveValue("energy_kWh", "minimize", 9.0, "kWh", ordinal=1),
                    ObjectiveValue("oxygen_kg", "maximize", 8.0, "kg", ordinal=0),
                )
            ),
        ),
        created_at="t2",
    )

    default_best = store.best(spec_a.feedstock_id)
    explicit_energy_best = store.best(spec_a.feedstock_id, objective_metric="energy_kWh")

    assert default_best is not None
    assert default_best.candidate_id == "high-oxygen-high-energy"
    assert explicit_energy_best is not None
    assert explicit_energy_best.candidate_id == "low-oxygen-low-energy"


def test_unknown_objective_sense_raises_at_construction_and_deserialization(tmp_path) -> None:
    spec = _base_spec()
    store = ResultStore(
        tmp_path / "results.sqlite",
        current_code_version=spec.code_version,
        current_data_digests=spec.data_digests,
    )
    with pytest.raises(ValueError, match="objective sense"):
        ObjectiveValue("oxygen_kg", "sideways", 1.0, "kg", ordinal=0)

    store.store(spec, _scored(spec), created_at="t1")
    with sqlite3.connect(tmp_path / "results.sqlite") as conn:
        conn.execute(
            "UPDATE results SET objectives = ? WHERE cache_key = ?",
            (
                '[{"metric":"oxygen_kg","sense":"sideways","value":1.0,"units":"kg","ordinal":0}]',
                cache_key(spec),
            ),
        )

    with pytest.raises(ValueError, match="objective sense"):
        store.lookup(spec)

    with sqlite3.connect(tmp_path / "results.sqlite") as conn:
        conn.execute(
            "UPDATE results SET objectives = ? WHERE cache_key = ?",
            (
                '[{"metric":"oxygen_kg","value":1.0,"units":"kg","ordinal":0}]',
                cache_key(spec),
            ),
        )

    with pytest.raises(ValueError, match="objective sense"):
        store.lookup(spec)


def test_wal_busy_timeout_supports_concurrent_reader_and_writer(tmp_path) -> None:
    spec = _base_spec(recipe_id="seed")
    store = ResultStore(
        tmp_path / "results.sqlite",
        current_code_version=spec.code_version,
        current_data_digests=spec.data_digests,
        busy_timeout_ms=2000,
    )
    store.store(spec, _scored(spec, candidate_id="seed"), created_at="t0")
    errors: list[BaseException] = []
    start = threading.Event()

    def writer() -> None:
        try:
            start.wait()
            for idx in range(20):
                next_spec = replace(spec, recipe_id=f"writer-{idx}")
                store.store(
                    next_spec,
                    _scored(next_spec, candidate_id=f"writer-{idx}", oxygen=idx),
                    created_at=f"tw{idx}",
                )
        except BaseException as exc:  # pragma: no cover - asserted in parent thread
            errors.append(exc)

    def reader() -> None:
        try:
            start.wait()
            for _ in range(40):
                store.query(spec.feedstock_id, profile_id=spec.profile_id, fidelity=spec.fidelity)
        except BaseException as exc:  # pragma: no cover - asserted in parent thread
            errors.append(exc)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for thread in threads:
        thread.start()
    start.set()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert store.best(spec.feedstock_id, objective_metric="oxygen_kg") is not None


def test_cross_process_writers_and_reader_are_serialized_without_lost_rows(tmp_path) -> None:
    spec = _base_spec(recipe_id="seed")
    path = tmp_path / "results.sqlite"
    ResultStore(
        path,
        current_code_version=spec.code_version,
        current_data_digests=spec.data_digests,
        busy_timeout_ms=10000,
    ).initialize()
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    errors = ctx.Queue()
    writers = [
        ctx.Process(
            target=_process_store_writer,
            args=(str(path), _eval_spec_payload(spec), start, errors, worker_idx * 10, 6),
        )
        for worker_idx in range(3)
    ]
    reader = ctx.Process(
        target=_process_store_reader,
        args=(str(path), _eval_spec_payload(spec), start, errors, 20),
    )
    processes = [*writers, reader]

    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=30)

    failures: list[str] = []
    for process in processes:
        if process.exitcode != 0:
            failures.append(f"{process.name} exit={process.exitcode}")
    while True:
        try:
            failures.append(errors.get_nowait())
        except queue.Empty:
            break

    store = ResultStore(
        path,
        current_code_version=spec.code_version,
        current_data_digests=spec.data_digests,
    )
    rows = store.query(spec.feedstock_id, profile_id=spec.profile_id, fidelity=spec.fidelity)

    assert failures == []
    assert {row.candidate_id for row in rows} == {
        f"process-{idx}"
        for worker_idx in range(3)
        for idx in range(worker_idx * 10, worker_idx * 10 + 6)
    }


def test_schema_version_stamped_and_migration_smoke(tmp_path) -> None:
    path = tmp_path / "results.sqlite"
    store = ResultStore(path)
    assert store.schema_version == SCHEMA_VERSION

    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE store_meta SET value = '0' WHERE key = 'schema_version'"
        )

    migrated = ResultStore(path)
    assert migrated.schema_version == SCHEMA_VERSION


def test_v2_store_migrates_result_scope_column_before_selector_index(tmp_path) -> None:
    path = tmp_path / "old-v2.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE store_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO store_meta(key, value) VALUES ('schema_version', '2');
            CREATE TABLE results (
                cache_key TEXT PRIMARY KEY,
                feedstock_id TEXT NOT NULL,
                recipe_id TEXT NOT NULL,
                profile_id TEXT NOT NULL,
                fidelity TEXT NOT NULL,
                code_version TEXT NOT NULL,
                data_digests TEXT NOT NULL,
                feasible INTEGER NOT NULL CHECK (feasible IN (0, 1)),
                failure_category TEXT,
                objectives TEXT NOT NULL,
                feasibility_margins TEXT NOT NULL,
                failing_gates TEXT NOT NULL,
                candidate_id TEXT,
                result_blob TEXT NOT NULL,
                run_reference TEXT NOT NULL,
                eval_spec TEXT NOT NULL,
                notes TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE objective_values (
                cache_key TEXT NOT NULL,
                metric TEXT NOT NULL,
                sense TEXT NOT NULL CHECK (sense IN ('minimize', 'maximize')),
                value REAL NOT NULL,
                units TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                PRIMARY KEY (cache_key, metric),
                FOREIGN KEY (cache_key) REFERENCES results(cache_key)
                    ON DELETE CASCADE
            );
            CREATE INDEX idx_results_current_selector
                ON results(feedstock_id, profile_id, fidelity, code_version, data_digests);
            """
        )

    migrated = ResultStore(path)

    assert migrated.schema_version == SCHEMA_VERSION
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(results)")}
        index_columns = [
            row[2]
            for row in conn.execute("PRAGMA index_info(idx_results_current_selector)")
        ]
    assert "result_scope" in columns
    assert "corpus_version" in columns
    assert index_columns == [
        "feedstock_id",
        "profile_id",
        "fidelity",
        "code_version",
        "data_digests",
        "result_scope",
    ]


def test_v3_store_migrates_corpus_version_once_without_rewriting_payloads(
    tmp_path,
) -> None:
    path = tmp_path / "old-v3.sqlite"
    legacy_payloads = {
        "data_digests": '{"feedstocks":"legacy-feedstock","profile":"legacy-profile"}',
        "result_scope": '{"legacy_scope":"kept"}',
        "objectives": '[{"metric":"oxygen_kg","sense":"maximize","value":1.0,"units":"kg","ordinal":0}]',
        "feasibility_margins": '{"mass_balance":{"status":"legacy"}}',
        "failing_gates": '["legacy_gate"]',
        "result_blob": '{"trace":["byte","unchanged"]}',
        "run_reference": '{"status":"ok","backend_status":"ok"}',
        "eval_spec": '{"recipe_id":"legacy-recipe","nested":{"order":["kept"]}}',
        "notes": '["legacy note"]',
    }
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE store_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO store_meta(key, value) VALUES ('schema_version', '3');
            CREATE TABLE results (
                cache_key TEXT PRIMARY KEY,
                feedstock_id TEXT NOT NULL,
                recipe_id TEXT NOT NULL,
                profile_id TEXT NOT NULL,
                fidelity TEXT NOT NULL,
                code_version TEXT NOT NULL,
                data_digests TEXT NOT NULL,
                result_scope TEXT NOT NULL DEFAULT '{}',
                feasible INTEGER NOT NULL CHECK (feasible IN (0, 1)),
                failure_category TEXT,
                objectives TEXT NOT NULL,
                feasibility_margins TEXT NOT NULL,
                failing_gates TEXT NOT NULL,
                candidate_id TEXT,
                result_blob TEXT NOT NULL,
                run_reference TEXT NOT NULL,
                eval_spec TEXT NOT NULL,
                notes TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE objective_values (
                cache_key TEXT NOT NULL,
                metric TEXT NOT NULL,
                sense TEXT NOT NULL CHECK (sense IN ('minimize', 'maximize')),
                value REAL NOT NULL,
                units TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                PRIMARY KEY (cache_key, metric),
                FOREIGN KEY (cache_key) REFERENCES results(cache_key)
                    ON DELETE CASCADE
            );
            """
        )
        conn.execute(
            """
            INSERT INTO results (
                cache_key, feedstock_id, recipe_id, profile_id, fidelity,
                code_version, data_digests, result_scope, feasible,
                failure_category, objectives, feasibility_margins, failing_gates,
                candidate_id, result_blob, run_reference, eval_spec, notes,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-cache-key",
                "lunar_mare_low_ti",
                "legacy-recipe",
                "oxygen-yield-v1",
                "fast",
                "legacy-code",
                legacy_payloads["data_digests"],
                legacy_payloads["result_scope"],
                1,
                None,
                legacy_payloads["objectives"],
                legacy_payloads["feasibility_margins"],
                legacy_payloads["failing_gates"],
                "legacy-candidate",
                legacy_payloads["result_blob"],
                legacy_payloads["run_reference"],
                legacy_payloads["eval_spec"],
                legacy_payloads["notes"],
                "2026-06-01T00:00:00Z",
            ),
        )

    assert ResultStore(path).schema_version == SCHEMA_VERSION
    assert ResultStore(path).schema_version == SCHEMA_VERSION

    with sqlite3.connect(path) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(results)")]
        row = conn.execute(
            """
            SELECT corpus_version, data_digests, result_scope, objectives,
                   feasibility_margins, failing_gates, result_blob,
                   run_reference, eval_spec, notes
            FROM results
            WHERE cache_key = 'legacy-cache-key'
            """
        ).fetchone()

    assert columns.count("corpus_version") == 1
    assert row[0] is None
    assert {
        "data_digests": row[1],
        "result_scope": row[2],
        "objectives": row[3],
        "feasibility_margins": row[4],
        "failing_gates": row[5],
        "result_blob": row[6],
        "run_reference": row[7],
        "eval_spec": row[8],
        "notes": row[9],
    } == legacy_payloads
