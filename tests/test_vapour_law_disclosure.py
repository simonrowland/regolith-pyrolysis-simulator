"""Run-scoped disclosure of an admitted diagnostic or extrapolated vapour law.

Invariant: a vapour-law computation is never detached. ``open_computation``
raises before the law runs unless a durable run registry is already bound.
The session owns that registry. A simulator that is itself the run keeps one
via ``bind_simulation_run``, and the snapshot is published on that owner.
A stored disclosure with any malformed or incomplete entry is unknown, never
a measured empty and never an empty authority. A physics cache hit binds the
owning run before it returns, so a known disclosure is re-registered even
when no session was already active. A new ``SimSession.start`` owns a fresh
registry. Projection after a
committed step poisons the session. Every public session verb that runs
the simulator binds that registry, including adjust and decide. A detached
C0 forecast shares the parent's run registry, and ``_predict_c0_exit``
publishes it onto the parent. Metadata overrides are applied before the
measured disclosure is installed, on every run-metadata publisher. A cache or memo hit with no stored
disclosure field is unknown and is a miss. The held curve, the local slot,
and the shared slot are one gate, ``accept_stored_memo``: unknown
recomputes, and a known disclosure is re-registered before the record is
returned. Upper-case ``_CACHE`` stores are in that same inventory. The melt
pressure cache, the KEMS surface cache, the residue pressure closures, and
the Sossi, Hashimoto, and Knudsen rows of the residue-prediction cache are
read only through that gate.
Registration of a new row is ``register_executed_law``. Compiled laws call
it from ``CompiledPressureEvaluator.evaluate``. A published Antoine
continuation that never calls evaluate(), including out-of-band SiO, calls
it once from the provider publication chokepoint. A non-diagnostic law is
registered when the evaluation leaves a certified domain: the temperature
band, the oxygen evaluator band, or the oxygen mass-action floor. An
in-domain non-diagnostic evaluation registers nothing. A hit does not execute
the law; it
re-registers the disclosures stored with that computation, and evaluation
counts add. An empty disclosure is a measured zero only when the computation
recorded one. Attribution is per computation, never the ambient run registry.
Typed-carrier notices stay a list: distinct notices are kept, exact duplicates
are dropped. Bare-float helpers do not carry notices.

The Fe pseudo law is ``log10(P/Pa) = A - B / (T + C)`` with
A=9.408547, B=23312.312336, C=1000. At the 1750 K anchor, T+C=2750 and
``10 ** (9.408547 - 23312.312336/2750) = 8.537731922030234`` Pa. The anchor
rail is 0.6585350200427053 Pa (gap 1.1127636390496998 dex, 1.113 dex at
3 d.p.) and the VapoRock comparator is 0.3004593572618527 Pa (gap
1.4535567803078235 dex, 1.454 dex at 3 d.p.).
"""

from __future__ import annotations

import csv
import functools
import hashlib
import inspect
import math
import sqlite3
import subprocess
import threading
import warnings
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from simulator.chemistry.ellingham_graph import effective_equilibrium_pressure_Pa
from simulator.corpus_version import current_corpus_version
from simulator.optimize.evalspec import cache_key
from simulator.optimize.objective import ObjectiveDefinition
from simulator.optimize.results_store import SCHEMA_VERSION, ResultStore
from simulator.optimize.strategy.protocol import Candidate
from simulator.optimize.study import (
    StudyRecord,
    StudyReplayError,
    _flagged_law_leaderboard_row,
    _rank_key,
    _scored_result_from_journal_payload,
    _scored_result_journal_payload,
    _to_record,
    _write_leaderboard,
)
from simulator.recipe import RecipePatch, RecipeSchema
from simulator.reduced_real_determinism import (
    PT0CacheMiss,
    PT0DeterminismStore,
    PT1PersistentEquilibriumStore,
    PT1_EQUILIBRIUM_TABLE,
    canonical_json_bytes,
    canonical_physics_bucket_key_from_replay_key,
)
from simulator.vapour_rail.catalog import CompiledPressureEvaluator
from simulator.vapour_rail.run_disclosure import (
    LawDisclosureParentError,
    active_snapshot,
    bind_simulation_run,
    computation_disclosure,
    disclosure_metadata,
    accept_stored_memo,
    disclosure_scope,
    open_computation,
    publish_stored_disclosures,
    stored_disclosures_known,
)
from tests.test_optimizer_results_store import _base_spec, _scored

_ROOT = Path(__file__).resolve().parents[1]
_RAIL_GAP = 1.1127636390496998
_VAPOROCK_GAP = 1.4535567803078235
_FE_ASSOCIATION_LAW_ID = "Fe:nasa_cea_9"
_FE_ANTOINE_LAW_ID = "Fe:antoine"


def _live_vapor() -> dict:
    path = _ROOT / "data" / "vapor_pressures.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _fe_pressure(species: str) -> float:
    pressure = effective_equilibrium_pressure_Pa(
        species,
        1800.0,
        1.0e-9,
        vapor_pressure_data=_live_vapor(),
    )
    assert isinstance(pressure, float)
    assert math.isfinite(pressure)
    assert pressure > 0.0
    return pressure


def _assert_fe_disclosure(row: dict, *, law_id: str, count: int) -> None:
    assert row["law_id"] == law_id
    assert row["provenance"] == (
        "diagnostic_only: backsolved from VapoRock, no coefficients fitted"
    )
    assert row["authority"] == "diagnostic/extrapolated"
    assert row["closer"] == "t-1723"
    assert row["certification_route"] == ["d-383", "d-381"]
    assert row["evaluation_count"] == count
    assert isinstance(row["certified_band_K"], list)
    assert len(row["certified_band_K"]) == 2
    gaps = row["anchor_gaps_dex"]
    assert gaps["gap_dex_law_vs_rail"] == pytest.approx(_RAIL_GAP, rel=0, abs=1e-12)
    assert gaps["gap_dex_law_vs_vaporock"] == pytest.approx(
        _VAPOROCK_GAP, rel=0, abs=1e-12
    )
    assert round(gaps["gap_dex_law_vs_rail"], 3) == 1.113
    assert round(gaps["gap_dex_law_vs_vaporock"], 3) == 1.454


def test_ellingham_float_registers_fe_law_once() -> None:
    """The bare-float helper publishes no notice. The run disclosure does."""

    with disclosure_scope(inherit=False) as registry:
        _fe_pressure("FeO_association_gas")
        snapshot = registry.snapshot()
    assert active_snapshot() == ()
    assert len(snapshot) == 1
    _assert_fe_disclosure(snapshot[0], law_id=_FE_ASSOCIATION_LAW_ID, count=1)

    with disclosure_scope(inherit=False) as registry:
        _fe_pressure("FeO_association_gas")
        _fe_pressure("FeO_association_gas")
        snapshot = registry.snapshot()
    assert len(snapshot) == 1
    _assert_fe_disclosure(snapshot[0], law_id=_FE_ASSOCIATION_LAW_ID, count=2)


def test_direct_fe_evaluate_and_sio_use_the_same_registry() -> None:
    from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider

    provider = BuiltinVaporPressureProvider(_live_vapor())
    catalog = provider._vapour_rail_catalog
    fe = catalog.species["Fe"].evaluator
    sio = catalog.species["SiO"].evaluator
    assert fe is not None and sio is not None

    with disclosure_scope(inherit=False) as registry:
        evaluation = fe.evaluate(1750.0)
        assert math.isfinite(evaluation.pressure_pa)
        snapshot = registry.snapshot()
    assert len(snapshot) == 1
    _assert_fe_disclosure(snapshot[0], law_id=_FE_ANTOINE_LAW_ID, count=1)
    assert snapshot[0]["certified_band_K"] == [1400.0, 1900.0]

    with disclosure_scope(inherit=False) as registry:
        sio_evaluation = sio.evaluate(1800.0, source_activity=0.35, pO2_bar=1.0e-9)
        assert math.isfinite(sio_evaluation.pressure_pa)
        assert registry.snapshot() == ()


def test_concurrent_scopes_do_not_share_disclosures() -> None:
    results: dict[str, tuple] = {}

    def use_fe() -> None:
        with disclosure_scope(inherit=False) as registry:
            _fe_pressure("FeO_association_gas")
            results["fe"] = registry.snapshot()

    def use_sio() -> None:
        with disclosure_scope(inherit=False) as registry:
            _fe_pressure("SiO")
            results["sio"] = registry.snapshot()

    with disclosure_scope(inherit=False) as parent:
        threads = [
            threading.Thread(target=use_fe),
            threading.Thread(target=use_sio),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        parent_snapshot = parent.snapshot()

    assert parent_snapshot == ()
    assert len(results["fe"]) == 1
    _assert_fe_disclosure(results["fe"][0], law_id=_FE_ASSOCIATION_LAW_ID, count=1)
    assert results["sio"] == ()


def test_register_executed_law_has_one_production_call() -> None:
    """Two executions, one writer.

    A compiled law registers inside evaluate(). A direct Antoine continuation
    registers at the provider's publication of that pressure. A third call
    site is a new execution path that has not been named.
    """

    files = subprocess.check_output(
        ["git", "ls-files", "simulator", "engines", "tools", "scripts"],
        cwd=_ROOT,
        text=True,
    ).splitlines()
    calls: list[str] = []
    for relative in files:
        if not relative.endswith(".py"):
            continue
        for lineno, line in enumerate(
            (_ROOT / relative).read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            stripped = line.strip()
            if "register_executed_law(" not in stripped:
                continue
            if stripped.startswith("def "):
                continue
            calls.append(f"{relative}:{lineno}:{stripped}")
    assert len(calls) == 2
    catalog_calls = [item for item in calls if item.startswith("simulator/vapour_rail/catalog.py:")]
    provider_calls = [
        item for item in calls if item.startswith("engines/builtin/vapor_pressure.py:")
    ]
    assert len(catalog_calls) == 1
    assert catalog_calls[0].endswith("register_executed_law(self, evaluation)")
    assert len(provider_calls) == 1
    assert provider_calls[0].endswith("register_executed_law(evaluator, evaluation)")
    provider = (_ROOT / "engines/builtin/vapor_pressure.py").read_text(encoding="utf-8")
    assert provider.count("def _register_direct_flagged_continuations(") == 1
    assert provider.count("_register_direct_flagged_continuations(") == 2
    evaluate = inspect.getsource(CompiledPressureEvaluator.evaluate)
    shadow = inspect.getsource(CompiledPressureEvaluator.evaluate_typed_shadow)
    assert evaluate.count("register_executed_law(") == 1
    assert "register_executed_law(" not in shadow


def _cache_key() -> tuple[dict, dict]:
    return (
        {
            "artifact": "freeze_gate_curve",
            "schema_version": "test",
            "tag": "law-disclosure",
            "namespace_id": "test:law-disclosure",
            "vapor_pressure_provider_selection": "test-vapor-provider",
            "sulfur_side": {},
            "model": {},
        },
        {"curve": {"status": "in_range", "tag": "law-disclosure"}},
    )


def test_pt0_cache_hit_reregisters_without_executing(tmp_path: Path) -> None:
    key, payload = _cache_key()
    memory = PT0DeterminismStore("capture")
    with disclosure_scope(inherit=False) as cold:
        with computation_disclosure():
            _fe_pressure("FeO_association_gas")
            memory._store("freeze_gate_curve", key, payload)
        cold_snapshot = cold.snapshot()
    assert cold_snapshot[0]["evaluation_count"] == 1
    with disclosure_scope(inherit=False) as warm:
        assert warm.snapshot() == ()
        memory._lookup("freeze_gate_curve", key)
        assert warm.snapshot() == cold_snapshot

    db_path = tmp_path / "pt1-disclosure.sqlite"
    persistent = PT0DeterminismStore("capture", db_path=db_path)
    with disclosure_scope(inherit=False) as cold:
        with computation_disclosure():
            _fe_pressure("FeO_association_gas")
            persistent._store("freeze_gate_curve", key, payload)
        cold_snapshot = cold.snapshot()
    replay = PT0DeterminismStore("replay", db_path=db_path)
    with disclosure_scope(inherit=False) as warm:
        replay._lookup("freeze_gate_curve", key)
        assert warm.snapshot() == cold_snapshot


def test_optimizer_cache_and_journal_reregister_metadata(tmp_path: Path) -> None:
    with disclosure_scope(inherit=False) as cold:
        with computation_disclosure():
            _fe_pressure("FeO_association_gas")
        cold_snapshot = cold.snapshot()
    spec = _base_spec()
    stored = replace(_scored(spec), law_disclosures=cold_snapshot)
    assert stored.cache_key == cache_key(spec)
    other = replace(
        stored,
        law_disclosures=(
            {
                "law_id": "SiO:antoine",
                "provenance": "fitted",
                "authority": "cited",
                "certified_band_K": [1400.0, 2273.15],
                "anchor_gaps_dex": {},
                "closer": "",
                "certification_route": [],
                "evaluation_count": 1,
            },
        ),
    )
    assert other.cache_key == stored.cache_key

    store = ResultStore(
        tmp_path / "results.sqlite",
        current_code_version=spec.code_version,
        current_data_digests=spec.data_digests,
    )
    store.store(spec, stored, created_at="2026-11-10T00:00:00Z")
    with disclosure_scope(inherit=False) as warm:
        loaded = store.lookup(spec)
        assert loaded is not None
        assert loaded.law_disclosures == cold_snapshot
        assert warm.snapshot() == cold_snapshot

    empty_payload = _scored_result_journal_payload(_scored(spec))
    assert empty_payload["law_disclosures"] == []
    flagged_payload = _scored_result_journal_payload(stored)
    assert flagged_payload["law_disclosures"] == [dict(item) for item in cold_snapshot]
    with disclosure_scope(inherit=False) as replayed:
        restored = _scored_result_from_journal_payload(flagged_payload)
        assert restored.law_disclosures == cold_snapshot
        assert replayed.snapshot() == cold_snapshot
    with disclosure_scope(inherit=False) as untouched:
        restored_empty = _scored_result_from_journal_payload(empty_payload)
        assert restored_empty.law_disclosures == ()
        assert untouched.snapshot() == ()
    missing = dict(empty_payload)
    del missing["law_disclosures"]
    with disclosure_scope(inherit=False) as refused:
        with pytest.raises(StudyReplayError, match="no law_disclosures"):
            _scored_result_from_journal_payload(missing)
        assert refused.snapshot() == ()


def test_ranked_output_shows_flagged_law_without_dropping_the_metric(
    tmp_path: Path,
) -> None:
    disclosure = {
        "law_id": _FE_ASSOCIATION_LAW_ID,
        "provenance": (
            "diagnostic_only: backsolved from VapoRock, no coefficients fitted"
        ),
        "authority": "diagnostic/extrapolated",
        "certified_band_K": [1400.0, 2500.0],
        "anchor_gaps_dex": {
            "gap_dex_law_vs_rail": _RAIL_GAP,
            "gap_dex_law_vs_vaporock": _VAPOROCK_GAP,
        },
        "closer": "t-1723",
        "certification_route": ["d-383", "d-381"],
        "evaluation_count": 1,
    }
    definitions = (
        ObjectiveDefinition(metric="oxygen_kg", sense="maximize", units="kg"),
    )
    flagged = StudyRecord(
        candidate_id="candidate-flagged",
        patch=RecipePatch({}),
        feasible=True,
        status="ok",
        objectives={"oxygen_kg": 10.0},
        feasibility_margins={},
        cache_key="same-cache-key",
        law_disclosures=(disclosure,),
    )
    clean = StudyRecord(
        candidate_id="candidate-clean",
        patch=RecipePatch({}),
        feasible=True,
        status="ok",
        objectives={"oxygen_kg": 10.0},
        feasibility_margins={},
        cache_key="same-cache-key",
        law_disclosures=(),
    )
    assert _rank_key(flagged, definitions)[:-1] == _rank_key(clean, definitions)[:-1]
    assert _flagged_law_leaderboard_row(flagged) == {
        "flagged_law_ids": _FE_ASSOCIATION_LAW_ID,
        "flagged_law_authority": "diagnostic/extrapolated",
    }
    assert _flagged_law_leaderboard_row(clean) == {
        "flagged_law_ids": "",
        "flagged_law_authority": "",
    }
    path = tmp_path / "leaderboard.csv"
    _write_leaderboard(
        path,
        (flagged, clean),
        (flagged, clean),
        flagged,
        definitions,
        RecipeSchema(),
    )
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    by_id = {row["candidate_id"]: row for row in rows}
    assert by_id["candidate-flagged"]["oxygen_kg"] == "10.0"
    assert by_id["candidate-flagged"]["flagged_law_ids"] == _FE_ASSOCIATION_LAW_ID
    assert (
        by_id["candidate-flagged"]["flagged_law_authority"]
        == "diagnostic/extrapolated"
    )
    assert by_id["candidate-clean"]["oxygen_kg"] == "10.0"
    assert by_id["candidate-clean"]["flagged_law_ids"] == ""
    assert by_id["candidate-clean"]["flagged_law_authority"] == ""


def test_battery_rows_stamp_the_active_disclosure() -> None:
    from simulator.battery.residue import _stamp_law_disclosure_metadata

    source = (_ROOT / "simulator" / "battery" / "residue.py").read_text(
        encoding="utf-8"
    )
    for name in (
        "predict_knudsen_residue_composition",
        "predict_mass_loss_fraction",
    ):
        assert f"@_isolated_law_disclosure\ndef {name}(" in source
    assert source.count("_stamp_law_disclosure_metadata(") == 3

    with disclosure_scope(inherit=False):
        empty: dict[str, str] = {}
        _stamp_law_disclosure_metadata(empty)
        assert "law_disclosures" not in empty
        _fe_pressure("FeO_association_gas")
        stamped: dict[str, str] = {}
        _stamp_law_disclosure_metadata(stamped)
    assert _FE_ASSOCIATION_LAW_ID in stamped["law_disclosures"]
    assert "t-1723" in stamped["law_disclosures"]
    assert "1.1127636390496998" in stamped["law_disclosures"]


def test_source_provenance_collision_keeps_both_diagnostic_notices() -> None:
    from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
    from simulator.vapour_rail.batch import (
        FLUX_ACTIVATION_EPOCH_RG_MANIFEST,
        FluxActivationContext,
    )
    from simulator.vapour_rail.instrumentation import serialize_vapour_answer

    source_marker = {
        "authority": "diagnostic/extrapolated",
        "reason": "source-provenance marker",
        "marker": "source-only",
        "closes_with": "not-the-executed-law",
        "executed_species_id": "Fe",
        "law_provenance": "source marker, not the executed pseudo law",
        "provenance_status": "diagnostic_only",
        "comparators": {
            "gap_dex_law_vs_rail": 9.0,
            "gap_dex_law_vs_vaporock": 9.0,
        },
    }
    provider = BuiltinVaporPressureProvider(_live_vapor())
    catalog = provider._vapour_rail_catalog
    batch = catalog.resolve_batch(
        {"process.cleaned_melt": {"FeO": 1.0}},
        {
            "temperature_K": 1750.0,
            "process_phase": "hot_train",
            "stage": "evaporation",
            "fO2_bar": 1.0e-9,
            "source_reaction_fO2_bar": 1.0e-9,
            "source_reaction_activity_provenance": {
                "Fe": {"diagnostic_admission_notice": source_marker},
            },
        },
        flux_activation_context=FluxActivationContext(
            epoch=FLUX_ACTIVATION_EPOCH_RG_MANIFEST
        ),
    )
    answer = batch.channel("Fe")
    assert answer.refusal_code is None
    published = serialize_vapour_answer(answer)["extra"]["diagnostic_admission_notice"]
    assert isinstance(published, list)
    markers = {item.get("marker") for item in published}
    closers = {item.get("closes_with") for item in published}
    assert "source-only" in markers
    assert "t-1723" in closers
    executed = next(item for item in published if item.get("closes_with") == "t-1723")
    assert executed["comparators"]["gap_dex_law_vs_rail"] == pytest.approx(
        _RAIL_GAP, rel=0, abs=1e-12
    )
    assert executed["comparators"]["gap_dex_law_vs_vaporock"] == pytest.approx(
        _VAPOROCK_GAP, rel=0, abs=1e-12
    )


def test_plural_notice_key_reaches_diagnostics() -> None:
    from types import SimpleNamespace

    from simulator.diagnostics import pressure_coating_pareto_diagnostic

    first = {
        "authority": "diagnostic/extrapolated",
        "closes_with": "t-1723",
        "executed_species_id": "Fe",
        "law_provenance": "backsolved from VapoRock, no coefficients fitted",
        "provenance_status": "diagnostic_only",
        "comparators": {
            "gap_dex_law_vs_rail": _RAIL_GAP,
            "gap_dex_law_vs_vaporock": _VAPOROCK_GAP,
        },
    }
    second = {
        "authority": "diagnostic/extrapolated",
        "closes_with": "t-1723",
        "executed_species_id": "Fe",
        "marker": "additional",
        "law_provenance": "backsolved from VapoRock, no coefficients fitted",
        "provenance_status": "diagnostic_only",
        "comparators": {
            "gap_dex_law_vs_rail": 2.25,
            "gap_dex_law_vs_vaporock": 3.5,
        },
    }
    published = pressure_coating_pareto_diagnostic(
        SimpleNamespace(
            condensation_model=SimpleNamespace(
                last_sticking_alpha_provenance_notice={
                    "vapour_carrier_authority_by_species": {
                        "Fe": {
                            "extra": {
                                "diagnostic_admission_notices": [first, second],
                            }
                        }
                    }
                }
            )
        ),
        target_species=("Fe",),
    )
    notices = published["by_species"]["Fe"]["diagnostic_admission_notice"]
    assert isinstance(notices, list)
    assert len(notices) == 2
    assert sorted(
        item["comparators"]["gap_dex_law_vs_rail"] for item in notices
    ) == pytest.approx([_RAIL_GAP, 2.25], rel=0, abs=1e-12)
    assert any(item.get("marker") == "additional" for item in notices)
    assert any(item.get("closes_with") == "t-1723" for item in notices)


def _tagged_curve(tag: str) -> tuple[dict, dict]:
    key, _payload = _cache_key()
    return (
        {**key, "tag": tag},
        {"curve": {"status": "in_range", "tag": tag}},
    )


def _key_hash(key: dict) -> str:
    return hashlib.sha256(canonical_json_bytes(key)).hexdigest()


def test_live_evaluation_plus_cache_hit_adds_counts() -> None:
    key, payload = _tagged_curve("live-then-hit")
    memory = PT0DeterminismStore("capture")
    with disclosure_scope(inherit=False) as run:
        with computation_disclosure():
            _fe_pressure("FeO_association_gas")
            memory._store("freeze_gate_curve", key, payload)
        assert run.snapshot()[0]["evaluation_count"] == 1
        memory._lookup("freeze_gate_curve", key)
        assert run.snapshot()[0]["evaluation_count"] == 2
        assert run.snapshot()[0]["law_id"] == _FE_ASSOCIATION_LAW_ID


def test_out_of_band_sio_registers_extrapolated_on_the_ranked_candidate() -> None:
    from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider

    provider = BuiltinVaporPressureProvider(_live_vapor())
    sio = provider._vapour_rail_catalog.species["SiO"].evaluator
    assert sio is not None
    with disclosure_scope(inherit=False) as registry:
        evaluation = sio.evaluate(2500.0, source_activity=0.35, pO2_bar=1.0e-9)
        assert evaluation.out_of_range is True
        assert math.isfinite(evaluation.pressure_pa)
        assert evaluation.pressure_pa > 0.0
        snapshot = registry.snapshot()
    assert len(snapshot) == 1
    row = snapshot[0]
    assert row["law_id"] == "SiO:standard_reaction_term"
    assert row["authority"] == "extrapolated"
    assert row["provenance"] == "out_of_range_conservative_continuation"
    assert row["certified_band_K"] == [1400.0, 2273.15]
    assert row["evaluation_count"] == 1
    assert row["closer"] == ""
    scored = replace(_scored(_base_spec()), law_disclosures=snapshot)
    record = _to_record(
        Candidate("sio-oob", RecipePatch({})),
        scored,
        cache_hit=False,
    )
    assert record.objectives["oxygen_kg"] == 10.0
    assert _flagged_law_leaderboard_row(record) == {
        "flagged_law_ids": "SiO:standard_reaction_term",
        "flagged_law_authority": "extrapolated",
    }


# Domain classes for one oxygen-dependent law. Expectations come from the
# inputs, not from reading the evaluation notice back.
# in_band: midpoint temperature, pO2 = 1e-9 bar (inside 1e-30..100).
# below / above: same temperature, pO2 outside that evaluator band.
# floor: pO2 = 1e-30 bar. Negative exponents invert through the mass-action
# floor; non-negative exponents stay inside the evaluator band and do not flag.
# temperature: one kelvin above the certified band, pO2 inside the band.
# temperature_and_below: both domains on one evaluation, one registration.
_OOR_PROVENANCE = "out_of_range_conservative_continuation"
_OXYGEN_BAND_REASON = "pO2_outside_evaluator_band"
_OXYGEN_FLOOR_REASON = "melt_dissociation_pO2_floor_inverted_through_mass_action"
_DOMAIN_PO2 = {
    "in_band": 1.0e-9,
    "below": 1.0e-31,
    "above": 101.0,
    "temperature": 1.0e-9,
    "temperature_and_below": 1.0e-31,
}
# Hand product for the reviewer point. SiO exponent is -1/2 and p_ref is
# 1e-9 bar, so pO2 = 1e-31 bar is 22 decades below the reference and +11 dex
# on the pressure. The in-band value at 1873.15 K, a=0.35, pO2=1e-9 bar is
# 0.9840768670106081 Pa. 0.9840768670106081 * 10**11 = 98407686701.06081 Pa.
_SIO_IN_BAND_PA = 0.9840768670106081
_SIO_OXYGEN_EXTRAPOLATED_PA = 98407686701.06081


@functools.lru_cache(maxsize=1)
def _compiled_catalog():
    from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider

    return BuiltinVaporPressureProvider(_live_vapor())._vapour_rail_catalog


def _non_diagnostic_oxygen_laws() -> list:
    laws = []
    for species in _compiled_catalog().species.values():
        evaluator = species.evaluator
        if evaluator is None:
            continue
        if getattr(evaluator, "diagnostic_admission_notice", None):
            continue
        needs_oxygen = (
            abs(float(evaluator.pO2_exponent)) > 0.0
            or evaluator.o2_channel_term is not None
        )
        if needs_oxygen:
            laws.append(evaluator)
    return laws


def _expected_domain_provenance(exponent: float, domain: str) -> str | None:
    if domain in {"below", "above"}:
        return _OXYGEN_BAND_REASON
    if domain == "floor":
        return _OXYGEN_FLOOR_REASON if exponent < 0.0 else None
    if domain == "temperature":
        return _OOR_PROVENANCE
    if domain == "temperature_and_below":
        return f"{_OOR_PROVENANCE}; {_OXYGEN_BAND_REASON}"
    if domain == "in_band":
        return None
    raise AssertionError(domain)


def _evaluate_oxygen(evaluator, temperature: float, pO2_bar: float, channel: str):
    from simulator.vapour_rail.channels import (
        CHANNEL_O2,
        LEGACY_FO2_PLANE,
        REACTION_PLANE_TRANSPORT_HEADSPACE,
        ReactionThermoInputs,
        o2_potential_from_pO2_bar,
    )

    kwargs: dict = {}
    if evaluator.activity_exponent:
        kwargs["source_activity"] = 0.35
    if channel == "kwargs":
        kwargs["pO2_bar"] = pO2_bar
    elif channel == "typed":
        plane = (
            LEGACY_FO2_PLANE.get(evaluator.oxygen_fugacity_channel or "")
            or REACTION_PLANE_TRANSPORT_HEADSPACE
        )
        potential = o2_potential_from_pO2_bar(
            pO2_bar=pO2_bar,
            temperature_K=temperature,
            reaction_plane=plane,
            pO2_reference_bar=evaluator.pO2_reference_bar or 1.0,
        )
        kwargs["reaction_inputs"] = ReactionThermoInputs(
            reaction_id=None,
            state_fingerprint=None,
            channels={CHANNEL_O2: potential},
        )
    else:
        raise AssertionError(channel)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return evaluator.evaluate(temperature, **kwargs)


@pytest.mark.parametrize("channel", ["kwargs", "typed"])
@pytest.mark.parametrize(
    "domain",
    [
        "in_band",
        "below",
        "above",
        "floor",
        "temperature",
        "temperature_and_below",
    ],
)
def test_every_oxygen_law_registers_its_domain_extrapolation(
    domain: str,
    channel: str,
) -> None:
    """Invariant: an extrapolated domain is disclosed once, on every channel.

    The classes are the temperature band, the oxygen evaluator band, and the
    oxygen mass-action floor. In-domain evaluations of a non-diagnostic law
    register nothing. Deleting the extrapolated-notice arm leaves below, above,
    and the negative-exponent floor unregistered, and drops the oxygen reason
    from temperature_and_below.
    """

    from simulator.physical_constants import MELT_DISSOCIATION_PO2_MIN_BAR

    laws = _non_diagnostic_oxygen_laws()
    assert {evaluator.species_id for evaluator in laws} >= {"SiO", "Na", "AlO2"}
    pO2_bar = (
        MELT_DISSOCIATION_PO2_MIN_BAR
        if domain == "floor"
        else _DOMAIN_PO2[domain]
    )
    mismatches: list[str] = []
    for evaluator in laws:
        low, high = evaluator.valid_temperature_K
        temperature = (
            high + 1.0
            if domain.startswith("temperature")
            else (low + high) / 2.0
        )
        expected = _expected_domain_provenance(float(evaluator.pO2_exponent), domain)
        with disclosure_scope(inherit=False) as registry:
            evaluation = _evaluate_oxygen(evaluator, temperature, pO2_bar, channel)
            snapshot = registry.snapshot()
        if not math.isfinite(evaluation.pressure_pa) or evaluation.pressure_pa <= 0.0:
            mismatches.append(
                f"{evaluator.species_id}: non-positive pressure {evaluation.pressure_pa}"
            )
            continue
        if expected is None:
            if snapshot:
                mismatches.append(
                    f"{evaluator.species_id}: expected no disclosure, got {snapshot}"
                )
            continue
        if len(snapshot) != 1:
            mismatches.append(
                f"{evaluator.species_id}: expected one row, got {snapshot!r}"
            )
            continue
        row = snapshot[0]
        law_id = f"{evaluator.species_id}:{evaluator.evaluator_family}"
        problems = []
        if row["law_id"] != law_id:
            problems.append(f"law_id {row['law_id']}")
        if row["authority"] != "extrapolated":
            problems.append(f"authority {row['authority']}")
        if row["provenance"] != expected:
            problems.append(f"provenance {row['provenance']!r} != {expected!r}")
        if row["evaluation_count"] != 1:
            problems.append(f"count {row['evaluation_count']}")
        if row["certified_band_K"] != [float(low), float(high)]:
            problems.append(f"band {row['certified_band_K']}")
        if row["closer"] != "":
            problems.append(f"closer {row['closer']}")
        if problems:
            mismatches.append(f"{evaluator.species_id}: {', '.join(problems)}")
    assert mismatches == []


def test_diagnostic_admission_stays_one_row_when_oxygen_is_outside_the_band() -> None:
    evaluator = _compiled_catalog().species["FeO_association_gas"].evaluator
    assert evaluator is not None
    assert getattr(evaluator, "diagnostic_admission_notice", None)
    with disclosure_scope(inherit=False) as registry:
        evaluation = _evaluate_oxygen(evaluator, 1800.0, 1.0e-31, "kwargs")
        assert math.isfinite(evaluation.pressure_pa)
        assert evaluation.pressure_pa > 0.0
        snapshot = registry.snapshot()
    assert len(snapshot) == 1
    _assert_fe_disclosure(snapshot[0], law_id=_FE_ASSOCIATION_LAW_ID, count=1)


def test_sio_oxygen_extrapolation_survives_cache_hit_and_ranking() -> None:
    """Reviewer point: 1873.15 K, a=0.35, pO2=1e-31 bar. Cold and warm match."""

    evaluator = _compiled_catalog().species["SiO"].evaluator
    assert evaluator is not None
    assert evaluator.pO2_exponent == -0.5
    assert evaluator.pO2_reference_bar == 1.0e-9
    low, high = evaluator.valid_temperature_K
    assert low <= 1873.15 <= high

    def evaluate(pO2_bar: float):
        return _evaluate_oxygen(evaluator, 1873.15, pO2_bar, "kwargs")

    memory = PT0DeterminismStore("capture")
    key_a, payload_a = _tagged_curve("sio-oxygen-a")
    key_b, payload_b = _tagged_curve("sio-oxygen-b")
    key_clean, payload_clean = _tagged_curve("sio-oxygen-clean")
    with disclosure_scope(inherit=False) as cold:
        with computation_disclosure() as first:
            evaluation = evaluate(1.0e-31)
            assert evaluation.out_of_range is False
            assert evaluation.pressure_pa == pytest.approx(
                _SIO_OXYGEN_EXTRAPOLATED_PA, rel=0, abs=1e-2
            )
            notice = evaluation.extrapolation_notice
            assert isinstance(notice, dict)
            assert notice["reason"] == _OXYGEN_BAND_REASON
            assert notice["authority_level"] == "extrapolated"
            memory._store("freeze_gate_curve", key_a, payload_a)
            one = first.snapshot()
        with computation_disclosure() as second:
            evaluate(1.0e-31)
            memory._store("freeze_gate_curve", key_b, payload_b)
            assert second.snapshot()[0]["evaluation_count"] == 1
        with computation_disclosure() as clean:
            in_band = evaluate(1.0e-9)
            assert in_band.out_of_range is False
            assert in_band.pressure_pa == pytest.approx(_SIO_IN_BAND_PA, rel=0, abs=1e-12)
            assert getattr(in_band, "extrapolation_notice", None) is None
            memory._store("freeze_gate_curve", key_clean, payload_clean)
            assert clean.snapshot() == ()
        cold_snapshot = cold.snapshot()
    assert one[0]["evaluation_count"] == 1
    assert one[0]["law_id"] == "SiO:standard_reaction_term"
    assert one[0]["authority"] == "extrapolated"
    assert one[0]["provenance"] == _OXYGEN_BAND_REASON
    assert one[0]["certified_band_K"] == [1400.0, 2273.15]
    assert cold_snapshot == (
        {**one[0], "evaluation_count": 2},
    )
    stored = memory._entries[_key_hash(key_a)]["law_disclosures"]
    assert stored[0]["provenance"] == _OXYGEN_BAND_REASON
    assert stored[0]["evaluation_count"] == 1
    assert memory._entries[_key_hash(key_clean)]["law_disclosures"] == ()
    with disclosure_scope(inherit=False) as warm:
        memory._lookup("freeze_gate_curve", key_a)
        memory._lookup("freeze_gate_curve", key_b)
        assert warm.snapshot() == cold_snapshot
        memory._lookup("freeze_gate_curve", key_clean)
        assert warm.snapshot() == cold_snapshot
    scored = replace(_scored(_base_spec()), law_disclosures=one)
    record = _to_record(
        Candidate("sio-oxygen", RecipePatch({})),
        scored,
        cache_hit=False,
    )
    assert record.objectives["oxygen_kg"] == 10.0
    assert _flagged_law_leaderboard_row(record) == {
        "flagged_law_ids": "SiO:standard_reaction_term",
        "flagged_law_authority": "extrapolated",
    }


def test_cache_publication_paths_use_the_disclosure_chokepoint() -> None:
    reduced = (_ROOT / "simulator/reduced_real_determinism.py").read_text(encoding="utf-8")
    assert reduced.count('return copy.deepcopy(entry["payload"])') == 1
    assert 'return copy.deepcopy(attempt["payload"])' not in reduced
    accept = reduced.split("def _accept_cached_hit", 1)[1].split("\n    def ", 1)[0]
    assert 'return copy.deepcopy(entry["payload"])' in accept
    assert "accept_stored_memo(" in accept
    assert "reregister_cached_disclosures(" not in accept
    assert "self._accepted_hit_disclosures = published" in accept
    assert "def _active_law_disclosures" not in reduced
    assert "self.entries" not in reduced
    assert "self._entries" in reduced

    core = (_ROOT / "simulator/core.py").read_text(encoding="utf-8")
    head = core.split("    def _get_equilibrium(", 1)[1].split(
        "    def _get_equilibrium_live(", 1
    )[0]
    bind = "with bind_simulation_run(self):"
    live_open = "with open_computation():"
    assert bind in head
    assert live_open in head
    assert "computation_disclosure()" not in head
    assert head.index(bind) < head.index("replay_enabled")
    assert head.index("replay_enabled") < head.index("cached_equilibrium")
    assert head.index("cached_equilibrium") < head.index(live_open)
    assert "capture_equilibrium" not in head

    evaporation = (_ROOT / "simulator/evaporation.py").read_text(encoding="utf-8")
    body = evaporation.split("    def _freeze_gate_curve_record(", 1)[1].split(
        "    def _freeze_gate_liquidus_fO2_log(", 1
    )[0]
    live_open = "with bind_simulation_run(self), open_computation()"
    # Held, local, and shared are the stored-memo returns. Each goes through
    # accept_stored_memo. A raw deepcopy of the hold is the bypass this pins.
    assert "return copy.deepcopy(held" not in body
    assert "publish_stored_disclosures(" not in body
    assert "stored_disclosures_known(" not in body
    assert "recorded_law_disclosures(" not in body
    assert body.count("accept_stored_memo(") == 3
    held_accept = body.index("accept_stored_memo(")
    local_accept = body.index("accept_stored_memo(", held_accept + 1)
    shared_accept = body.index("accept_stored_memo(", local_accept + 1)
    assert held_accept < body.index("replay_gate_curve(")
    assert body.index(bind) < body.index("replay_gate_curve(")
    assert body.index("replay_gate_curve(") < local_accept < shared_accept
    assert shared_accept < body.index(live_open)
    assert body.index(live_open) < body.rindex("store.capture_gate_curve(")
    liquidus = core.split("    def _melt_redox_liquidus_gate_record(", 1)[1].split(
        "    def _melt_redox_liquid_fraction_factor(", 1
    )[0]
    # The core local cache and the core curve memo are the two reads.
    # Both admit through accept_stored_memo before any live recompute,
    # and the curve-memo write keeps the measured field.
    assert liquidus.count("accept_stored_memo(") == 2
    first_accept = liquidus.index("accept_stored_memo(")
    second_accept = liquidus.index("accept_stored_memo(", first_accept + 1)
    live_record = liquidus.index("self._freeze_gate_curve_record(")
    assert first_accept < second_accept < live_record
    memo_write = liquidus.split("memo[key] = gate_curve_memo_record(", 1)[1]
    assert "_carried_disclosures(normalized_record)" in memo_write.split(
        "self._record_composition_projected_liquidus_notice_from_curve",
        1,
    )[0]
    assert "with open_computation()" not in body
    assert "with computation_disclosure()" not in body
    disclosure = (_ROOT / "simulator/vapour_rail/run_disclosure.py").read_text(
        encoding="utf-8"
    )
    gate = disclosure.split("def accept_stored_memo(", 1)[1].split("\ndef ", 1)[0]
    assert gate.index("stored_disclosures_known(") < gate.index(
        "publish_stored_disclosures("
    )
    assert gate.count("def accept_stored_memo(") == 0
    hit_calls: list[str] = []
    reduced_lines = reduced.splitlines()
    for index, line in enumerate(reduced_lines):
        if "_accept_cached_hit(" in line and "def " not in line:
            hit_calls.append("\n".join(reduced_lines[index:index + 8]))
    assert len(hit_calls) == 3
    assert all("owner=sim" in call for call in hit_calls)

    results = (_ROOT / "simulator/optimize/results_store.py").read_text(encoding="utf-8")
    assert "law_disclosures TEXT NOT NULL" not in results
    assert "ADD COLUMN law_disclosures TEXT NOT NULL" not in results
    assert SCHEMA_VERSION == 6


def test_preexisting_optimizer_row_without_disclosure_is_not_a_hit(tmp_path: Path) -> None:
    spec = _base_spec()
    path = tmp_path / "v5.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE store_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO store_meta(key, value) VALUES ('schema_version', '5')"
        )
        conn.execute(
            """
            CREATE TABLE results (
                cache_key TEXT PRIMARY KEY,
                feedstock_id TEXT NOT NULL,
                recipe_id TEXT NOT NULL,
                profile_id TEXT NOT NULL,
                fidelity TEXT NOT NULL,
                code_version TEXT NOT NULL,
                corpus_version TEXT,
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
            )
            """
        )
        conn.execute(
            """
            INSERT INTO results (
                cache_key, feedstock_id, recipe_id, profile_id, fidelity,
                code_version, corpus_version, data_digests, result_scope,
                feasible, objectives, feasibility_margins, failing_gates,
                result_blob, run_reference, eval_spec, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cache_key(spec),
                spec.feedstock_id,
                spec.recipe_id,
                spec.profile_id,
                spec.fidelity,
                spec.code_version,
                current_corpus_version(),
                "{}",
                "{}",
                1,
                "[]",
                "{}",
                "[]",
                "{}",
                "{}",
                "{}",
                "[]",
                "2026-11-10T00:00:00Z",
            ),
        )
    store = ResultStore(
        path,
        current_code_version=spec.code_version,
        current_data_digests=spec.data_digests,
    )
    assert store.schema_version == 6
    with sqlite3.connect(path) as conn:
        raw = conn.execute("SELECT law_disclosures FROM results").fetchone()[0]
    assert raw is None
    assert store.lookup(spec) is None
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE results SET law_disclosures = '[]'")
    with pytest.raises((TypeError, ValueError, KeyError)):
        store.lookup(spec)


def _capture_two_fe_and_one_clean(store: PT0DeterminismStore):
    fe_a_key, fe_a_payload = _tagged_curve("fe-a")
    fe_b_key, fe_b_payload = _tagged_curve("fe-b")
    clean_key, clean_payload = _tagged_curve("clean")
    with disclosure_scope(inherit=False) as cold:
        with computation_disclosure() as first:
            _fe_pressure("FeO_association_gas")
            store._store("freeze_gate_curve", fe_a_key, fe_a_payload)
            assert first.snapshot()[0]["evaluation_count"] == 1
        with computation_disclosure() as second:
            _fe_pressure("FeO_association_gas")
            store._store("freeze_gate_curve", fe_b_key, fe_b_payload)
            assert second.snapshot()[0]["evaluation_count"] == 1
        with computation_disclosure() as clean:
            store._store("freeze_gate_curve", clean_key, clean_payload)
            assert clean.snapshot() == ()
        cold_snapshot = cold.snapshot()
    assert cold_snapshot[0]["evaluation_count"] == 2
    assert store._entries[_key_hash(fe_a_key)]["law_disclosures"][0]["evaluation_count"] == 1
    assert store._entries[_key_hash(clean_key)]["law_disclosures"] == ()
    return fe_a_key, fe_b_key, clean_key, cold_snapshot


def _replay_exact(store, fe_a_key, fe_b_key, clean_key, cold_snapshot) -> None:
    with disclosure_scope(inherit=False) as warm:
        store._lookup("freeze_gate_curve", fe_a_key)
        store._lookup("freeze_gate_curve", fe_b_key)
        assert warm.snapshot() == cold_snapshot
        store._lookup("freeze_gate_curve", clean_key)
        assert warm.snapshot() == cold_snapshot


def _put_equilibrium(db_path: Path, key: dict, payload: dict) -> None:
    key_bytes = canonical_json_bytes(key)
    payload_bytes = canonical_json_bytes(payload)
    PT1PersistentEquilibriumStore(db_path).put(
        artifact="equilibrium_post_record",
        key=key,
        key_bytes=key_bytes,
        key_hash=hashlib.sha256(key_bytes).hexdigest(),
        payload=payload,
        payload_bytes=payload_bytes,
        payload_hash=hashlib.sha256(payload_bytes).hexdigest(),
    )


def _lookup_interpolated(db_path: Path, query: dict):
    store = PT0DeterminismStore("replay", db_path=db_path)
    payload = store._lookup_optional(
        "equilibrium_post_record",
        query,
        physics_bucket_key=canonical_physics_bucket_key_from_replay_key(query),
    )
    return store, payload


@pytest.mark.parametrize(
    "kind",
    [
        "memory_exact",
        "persistent_exact",
        "interpolated",
        "optimizer",
        "journal",
        "memo",
    ],
)
def test_published_cache_classes_obey_disclosure_invariant(kind: str, tmp_path: Path) -> None:
    """Every hit class re-registers only that computation, and absence is a miss."""

    if kind in {"memory_exact", "persistent_exact"}:
        db_path = tmp_path / "exact.sqlite"
        capture = PT0DeterminismStore(
            "capture",
            db_path=db_path if kind == "persistent_exact" else None,
        )
        fe_a_key, fe_b_key, clean_key, cold_snapshot = _capture_two_fe_and_one_clean(
            capture
        )
        replay = (
            PT0DeterminismStore("replay", db_path=db_path)
            if kind == "persistent_exact"
            else capture
        )
        _replay_exact(replay, fe_a_key, fe_b_key, clean_key, cold_snapshot)
        if kind == "memory_exact":
            capture._entries[_key_hash(fe_a_key)]["law_disclosures"] = None
            with disclosure_scope(inherit=False) as refused:
                with pytest.raises(PT0CacheMiss):
                    capture._lookup("freeze_gate_curve", fe_a_key)
                assert refused.snapshot() == ()
            return
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                f"UPDATE {PT1_EQUILIBRIUM_TABLE} SET law_disclosures_json = NULL "
                "WHERE key_hash = ?",
                (_key_hash(fe_a_key),),
            )
        fresh = PT0DeterminismStore("replay", db_path=db_path)
        with disclosure_scope(inherit=False) as refused:
            with pytest.raises(PT0CacheMiss):
                fresh._lookup("freeze_gate_curve", fe_a_key)
            assert refused.snapshot() == ()
            assert fresh.hits == 0
        return

    if kind == "interpolated":
        from tests.test_reduced_real_cache_interpolation import (
            _interpolation_key,
            _interpolation_payload,
        )

        low = _interpolation_key("low", feo_fraction=0.20, temperature_K=1490.0)
        high = _interpolation_key("high", feo_fraction=0.20, temperature_K=1510.0)
        query = _interpolation_key("query", feo_fraction=0.20, temperature_K=1500.0)
        low_payload = _interpolation_payload(liquid_fraction=0.70, sio_pa=7.0)
        high_payload = _interpolation_payload(liquid_fraction=0.71, sio_pa=7.05)
        db_path = tmp_path / "interp.sqlite"
        with disclosure_scope(inherit=False) as cold:
            with computation_disclosure():
                _fe_pressure("FeO_association_gas")
                _put_equilibrium(db_path, low, low_payload)
            with computation_disclosure():
                _fe_pressure("FeO_association_gas")
                _put_equilibrium(db_path, high, high_payload)
            cold_snapshot = cold.snapshot()
        assert cold_snapshot[0]["evaluation_count"] == 2
        with disclosure_scope(inherit=False) as warm:
            store, payload = _lookup_interpolated(db_path, query)
            assert payload is not None
            assert store.last_cache_state == "cached_interpolated"
            assert warm.snapshot() == cold_snapshot
        clean_db = tmp_path / "interp-clean.sqlite"
        with disclosure_scope(inherit=False):
            with computation_disclosure():
                _fe_pressure("FeO_association_gas")
            with computation_disclosure():
                _put_equilibrium(clean_db, low, low_payload)
            with computation_disclosure():
                _put_equilibrium(clean_db, high, high_payload)
        with disclosure_scope(inherit=False) as warm:
            _store, payload = _lookup_interpolated(clean_db, query)
            assert payload is not None
            assert warm.snapshot() == ()
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                f"UPDATE {PT1_EQUILIBRIUM_TABLE} SET law_disclosures_json = NULL "
                "WHERE key_hash = ?",
                (_key_hash(low),),
            )
        with disclosure_scope(inherit=False) as refused:
            store, payload = _lookup_interpolated(db_path, query)
            assert payload is None
            assert store.hits == 0
            assert refused.snapshot() == ()
        return

    if kind == "optimizer":
        spec_a = _base_spec(campaign="fe-a")
        spec_b = _base_spec(campaign="fe-b")
        spec_clean = _base_spec(campaign="clean")
        assert len({cache_key(spec_a), cache_key(spec_b), cache_key(spec_clean)}) == 3
        path = tmp_path / "optimizer.sqlite"
        store = ResultStore(
            path,
            current_code_version=spec_a.code_version,
            current_data_digests=spec_a.data_digests,
        )
        with disclosure_scope(inherit=False) as cold:
            with computation_disclosure() as first:
                _fe_pressure("FeO_association_gas")
                one = first.snapshot()
            with computation_disclosure() as second:
                _fe_pressure("FeO_association_gas")
                two = second.snapshot()
            cold_snapshot = cold.snapshot()
        assert one[0]["evaluation_count"] == 1
        assert cold_snapshot[0]["evaluation_count"] == 2
        store.store(
            spec_a,
            replace(_scored(spec_a), law_disclosures=one),
            created_at="2026-11-10T00:00:00Z",
        )
        store.store(
            spec_b,
            replace(_scored(spec_b), law_disclosures=two),
            created_at="2026-11-10T00:00:01Z",
        )
        store.store(spec_clean, _scored(spec_clean), created_at="2026-11-10T00:00:02Z")
        with disclosure_scope(inherit=False) as warm:
            assert store.lookup(spec_a) is not None
            assert store.lookup(spec_b) is not None
            clean = store.lookup(spec_clean)
            assert clean is not None and clean.law_disclosures == ()
            assert warm.snapshot() == cold_snapshot
        with sqlite3.connect(path) as conn:
            conn.execute(
                "UPDATE results SET law_disclosures = NULL WHERE cache_key = ?",
                (cache_key(spec_a),),
            )
        assert store.lookup(spec_a) is None
        queried = store.query(spec_a.feedstock_id)
        assert cache_key(spec_a) not in {item.cache_key for item in queried}
        best = store.best(spec_a.feedstock_id, objective_metric="oxygen_kg")
        assert best is not None
        assert best.cache_key != cache_key(spec_a)
        return

    if kind == "memo":
        with disclosure_scope(inherit=False) as cold:
            with computation_disclosure() as first:
                _fe_pressure("FeO_association_gas")
                one = first.snapshot()
            with computation_disclosure() as second:
                _fe_pressure("FeO_association_gas")
                two = second.snapshot()
            cold_snapshot = cold.snapshot()
        memo_a = {"law_disclosures": one}
        memo_b = {"law_disclosures": two}
        memo_clean = {"law_disclosures": []}
        memo_unknown = {"curve": {"status": "in_range"}}
        with disclosure_scope(inherit=False) as warm:
            assert publish_stored_disclosures(memo_a) == [dict(item) for item in one]
            assert publish_stored_disclosures(memo_b) == [dict(item) for item in two]
            assert warm.snapshot() == cold_snapshot
            assert publish_stored_disclosures(memo_clean) == []
            assert warm.snapshot() == cold_snapshot
            assert publish_stored_disclosures(memo_unknown) is None
            assert warm.snapshot() == cold_snapshot
        return

    spec_a = _base_spec(campaign="journal-fe-a")
    spec_b = _base_spec(campaign="journal-fe-b")
    spec_clean = _base_spec(campaign="journal-clean")
    with disclosure_scope(inherit=False) as cold:
        with computation_disclosure() as first:
            _fe_pressure("FeO_association_gas")
            one = first.snapshot()
        with computation_disclosure() as second:
            _fe_pressure("FeO_association_gas")
            two = second.snapshot()
        cold_snapshot = cold.snapshot()
    payload_a = _scored_result_journal_payload(
        replace(_scored(spec_a), law_disclosures=one)
    )
    payload_b = _scored_result_journal_payload(
        replace(_scored(spec_b), law_disclosures=two)
    )
    payload_clean = _scored_result_journal_payload(_scored(spec_clean))
    assert payload_clean["law_disclosures"] == []
    with disclosure_scope(inherit=False) as warm:
        _scored_result_from_journal_payload(payload_a)
        _scored_result_from_journal_payload(payload_b)
        _scored_result_from_journal_payload(payload_clean)
        assert warm.snapshot() == cold_snapshot
    missing = dict(payload_a)
    del missing["law_disclosures"]
    with disclosure_scope(inherit=False) as refused:
        with pytest.raises(StudyReplayError, match="no law_disclosures"):
            _scored_result_from_journal_payload(missing)
        assert refused.snapshot() == ()
    unreadable = dict(payload_a)
    unreadable["law_disclosures"] = None
    with pytest.raises(StudyReplayError, match="unreadable"):
        _scored_result_from_journal_payload(unreadable)


@pytest.mark.parametrize("opener", [computation_disclosure, open_computation])
def test_parentless_computation_raises_before_the_law_runs(opener) -> None:
    """A computation with no durable parent raises before Fe executes.

    The body must not run. A detached registry that recorded Fe and then
    reset without publishing is the drop this pin forbids.
    """

    ran = False
    with pytest.raises(LawDisclosureParentError):
        with opener():
            ran = True
            _fe_pressure("FeO_association_gas")
    assert ran is False
    assert active_snapshot() == ()


def test_simulator_owned_run_publishes_every_computation() -> None:
    """No session: the simulator registry keeps both Fe counts."""

    from types import SimpleNamespace

    owner = SimpleNamespace()
    with bind_simulation_run(owner):
        with open_computation() as first:
            _fe_pressure("FeO_association_gas")
            assert first.snapshot()[0]["evaluation_count"] == 1
        with open_computation() as second:
            _fe_pressure("FeO_association_gas")
            assert second.snapshot()[0]["evaluation_count"] == 1
    assert active_snapshot() == ()
    _assert_fe_disclosure(
        owner.law_disclosures[0],
        law_id=_FE_ASSOCIATION_LAW_ID,
        count=2,
    )
    with bind_simulation_run(owner):
        with open_computation():
            _fe_pressure("FeO_association_gas")
    _assert_fe_disclosure(
        owner.law_disclosures[0],
        law_id=_FE_ASSOCIATION_LAW_ID,
        count=3,
    )


def test_bound_session_is_the_parent_of_a_simulation_computation() -> None:
    from types import SimpleNamespace

    owner = SimpleNamespace()
    with disclosure_scope(inherit=False) as session:
        with bind_simulation_run(owner):
            with open_computation():
                _fe_pressure("FeO_association_gas")
        _assert_fe_disclosure(
            session.snapshot()[0],
            law_id=_FE_ASSOCIATION_LAW_ID,
            count=1,
        )
    assert not hasattr(owner, "law_disclosures")
    assert active_snapshot() == ()


def test_every_production_open_computation_binds_a_durable_run() -> None:
    """Every production ``open_computation`` call sits inside a durable bind."""

    import re

    opener = "open_computation("
    offenders: list[str] = []
    disclosure = (_ROOT / "simulator/vapour_rail/run_disclosure.py").read_text(
        encoding="utf-8"
    )
    body = disclosure.split("def open_computation", 1)[1].split("\ndef ", 1)[0]
    assert "LawDisclosureParentError" in body
    assert "RunDisclosure(computation=True)" not in body
    for path in sorted((_ROOT / "simulator").rglob("*.py")):
        if path.name == "run_disclosure.py":
            continue
        func = "<module>"
        bound = False
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.match(r"(?:    )?def ", line) or line.startswith("class "):
                func = line.strip()
                bound = False
            if "bind_simulation_run(" in line:
                bound = True
            if opener in line and "def open_computation" not in line and not bound:
                offenders.append(f"{path.relative_to(_ROOT)}:{lineno} {func}")
    assert offenders == []


def _complete_disclosure_row() -> dict:
    """One public Fe disclosure. Every required field is present."""

    return {
        "law_id": _FE_ASSOCIATION_LAW_ID,
        "provenance": (
            "diagnostic_only: backsolved from VapoRock, no coefficients fitted"
        ),
        "authority": "diagnostic/extrapolated",
        "certified_band_K": [1400.0, 2500.0],
        "anchor_gaps_dex": {
            "gap_dex_law_vs_rail": _RAIL_GAP,
            "gap_dex_law_vs_vaporock": _VAPOROCK_GAP,
        },
        "closer": "t-1723",
        "certification_route": ["d-383", "d-381"],
        "evaluation_count": 1,
    }


_REQUIRED_DISCLOSURE_FIELDS = (
    "law_id",
    "provenance",
    "authority",
    "certified_band_K",
    "anchor_gaps_dex",
    "closer",
    "certification_route",
    "evaluation_count",
)


def _incomplete_disclosure_rows() -> list:
    """Each arm drops or breaks one required field, plus the law-id-only row."""

    rows: list = [
        [{"law_id": "Fe:nasa_cea_9", "evaluation_count": 1}],
        '[{"law_id": "Fe:nasa_cea_9", "evaluation_count": 1}]',
    ]
    complete = _complete_disclosure_row()
    for field in _REQUIRED_DISCLOSURE_FIELDS:
        rows.append([{key: value for key, value in complete.items() if key != field}])
    for field, value in (
        ("authority", ""),
        ("provenance", ""),
        ("certified_band_K", [1400.0]),
        ("anchor_gaps_dex", {"gap_dex_law_vs_rail": "nope"}),
        ("closer", None),
        ("certification_route", "d-383"),
        ("evaluation_count", 0),
        ("evaluation_count", True),
    ):
        broken = dict(complete)
        broken[field] = value
        rows.append([broken])
    return rows


_MALFORMED_DISCLOSURES = (
    None,
    [None],
    [{}],
    [{"law_id": ""}],
    [{"not_a_law": "Fe:nasa_cea_9"}],
    [{"law_id": "Fe:nasa_cea_9"}, None],
    [{"law_id": "Fe:nasa_cea_9"}, {}],
    "null",
    "[null]",
    "[{}]",
    1,
    "not-json",
    *_incomplete_disclosure_rows(),
)


@pytest.mark.parametrize("raw", _MALFORMED_DISCLOSURES)
def test_malformed_stored_disclosure_is_unknown(raw) -> None:
    """One bad entry is unknown on every reader. It is not dropped to ()."""

    from simulator.optimize.study import _journal_law_disclosures
    from simulator.reduced_real_determinism import _disclosures_known

    record = {"law_disclosures": raw}
    assert stored_disclosures_known(record) is False
    assert _disclosures_known(record) is False
    assert disclosure_metadata(raw) is None
    with disclosure_scope(inherit=False) as registry:
        assert publish_stored_disclosures(record) is None
        assert registry.snapshot() == ()
    with pytest.raises(StudyReplayError, match="unreadable"):
        _journal_law_disclosures(record)


@pytest.mark.parametrize(
    "raw",
    [
        [],
        "[]",
        [_complete_disclosure_row()],
    ],
)
def test_well_formed_stored_disclosure_stays_measured(raw) -> None:
    from simulator.optimize.study import _journal_law_disclosures
    from simulator.reduced_real_determinism import _disclosures_known

    record = {"law_disclosures": raw}
    assert stored_disclosures_known(record) is True
    assert _disclosures_known(record) is True
    assert disclosure_metadata(raw) is not None
    measured = _journal_law_disclosures(record)
    assert isinstance(measured, tuple)
    with disclosure_scope(inherit=False) as registry:
        published = publish_stored_disclosures(record)
        assert published is not None
        if published:
            _assert_fe_disclosure(
                registry.snapshot()[0],
                law_id=_FE_ASSOCIATION_LAW_ID,
                count=1,
            )
            assert published[0]["authority"] == "diagnostic/extrapolated"
        else:
            assert registry.snapshot() == ()


def test_session_start_forgets_the_previous_run_disclosure(monkeypatch) -> None:
    from simulator.session import SimSession

    session = SimSession()

    def _start(config, backend=None):
        _fe_pressure("FeO_association_gas")
        return session

    monkeypatch.setattr(session, "_start_impl", _start)
    session.start(object())
    _assert_fe_disclosure(
        session.law_disclosures[0],
        law_id=_FE_ASSOCIATION_LAW_ID,
        count=1,
    )
    session.start(object())
    _assert_fe_disclosure(
        session.law_disclosures[0],
        law_id=_FE_ASSOCIATION_LAW_ID,
        count=1,
    )


def test_projection_failure_poisons_the_session_before_another_advance() -> None:
    session = _probe_session(use_fe=False)
    calls: list[int] = []
    sim = session._sim
    original_step = sim.step

    def counting_step():
        calls.append(len(calls) + 1)
        return original_step()

    sim.step = counting_step

    def broken_summary(*_args):
        raise ValueError("projection broke")

    session._build_per_hour_summary = broken_summary
    with pytest.raises(RuntimeError, match="projection failed"):
        session.advance()
    assert calls == [1]
    with pytest.raises(RuntimeError, match="session is poisoned"):
        session.advance()
    assert calls == [1]


def _probe_simulator(*, use_fe: bool):
    from types import SimpleNamespace

    class _Ledger:
        pass

    sim = SimpleNamespace(
        atom_ledger=_Ledger(),
        melt=SimpleNamespace(hour=1),
        record=SimpleNamespace(snapshots=()),
        _condensation_model=None,
        paused_for_decision=False,
        pending_decision=None,
        _last_campaign_summary=None,
        _last_backend_error="",
        _last_c6_refusal_diagnostic={},
    )

    def step():
        if use_fe:
            _fe_pressure("FeO_association_gas")
        return SimpleNamespace(
            o2_bubbler_diagnostic={
                "status": "refused",
                "reason": "disclosure-probe",
            }
        )

    sim.is_complete = lambda: False
    sim.step = step
    return sim


def _probe_session(*, use_fe: bool):
    from simulator.session import SimSession

    session = SimSession()
    session._sim = _probe_simulator(use_fe=use_fe)
    return session


def test_session_registry_reaches_every_publisher(monkeypatch) -> None:
    """Fe through the bare-float helper shows on every public output.

    The law is executed only by ``effective_equilibrium_pressure_Pa``. Each
    arm calls a real entry point. A session that does not execute the law
    publishes no disclosure.
    """

    import json
    import sys
    import types
    from types import SimpleNamespace

    from simulator.run_executor import RunExecutor
    from simulator.runner import RunnerError, _runner_failure_result
    from simulator.session import SimSession
    from simulator.session_cli import SessionScriptRunner

    monkeypatch.setattr(
        SimSession,
        "_build_per_hour_summary",
        staticmethod(lambda sim, snapshot: {"hour": 1}),
    )

    clean = _probe_session(use_fe=False)
    clean_step = clean.advance()
    assert clean.law_disclosures == ()
    assert "law_disclosures" not in clean_step.per_hour_summary

    session_run = _probe_session(use_fe=True)
    execution = RunExecutor().execute_session(session_run, hours=1)
    assert len(execution.per_hour) == 1
    _assert_fe_disclosure(
        execution.session.law_disclosures[0],
        law_id=_FE_ASSOCIATION_LAW_ID,
        count=1,
    )
    _assert_fe_disclosure(
        execution.per_hour[0]["law_disclosures"][0],
        law_id=_FE_ASSOCIATION_LAW_ID,
        count=1,
    )
    assert execution.simulator.law_disclosures == execution.session.law_disclosures

    cli = SessionScriptRunner()
    cli.session = _probe_session(use_fe=True)
    cli.started = True
    frame = cli._advance(["1"])
    _assert_fe_disclosure(
        frame["law_disclosures"][0],
        law_id=_FE_ASSOCIATION_LAW_ID,
        count=1,
    )
    _assert_fe_disclosure(
        frame["step"]["law_disclosures"][0],
        law_id=_FE_ASSOCIATION_LAW_ID,
        count=1,
    )

    failed = _probe_session(use_fe=True)
    failed_step = failed.advance()
    payload = _runner_failure_result(
        error=RunnerError("disclosure probe"),
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=1,
        mass_kg=1.0,
        additives_kg={},
        track="pyrolysis",
        backend_name="internal-analytical",
        engines={},
        metadata_overrides={"law_disclosures": []},
        execution=SimpleNamespace(
            session=failed,
            simulator=None,
            per_hour=(failed_step.per_hour_summary,),
            backend_status="unavailable",
            backend_authoritative=False,
            refusal_diagnostic={},
            campaigns_elapsed=1.0,
            shadow_trace=(),
        ),
    )
    _assert_fe_disclosure(
        payload["run_metadata"]["law_disclosures"][0],
        law_id=_FE_ASSOCIATION_LAW_ID,
        count=1,
    )
    _assert_fe_disclosure(
        payload["per_hour_summary"][0]["law_disclosures"][0],
        law_id=_FE_ASSOCIATION_LAW_ID,
        count=1,
    )

    from simulator.optimize.evaluate import evaluate
    from simulator.optimize.recipe import RecipePatch as OptimizerRecipePatch
    from tests.test_optimizer_evaluate import PROFILE, _execution

    class _FeExecutor:
        def execute(self, config):
            self.session = _probe_session(use_fe=True)
            self.session.advance()
            return _execution()

    executor = _FeExecutor()
    ranked = evaluate(
        OptimizerRecipePatch(
            {("campaigns", "C0", "temp_range_C"): [20.0, 950.0]}
        ),
        "lunar_mare_low_ti",
        "fast",
        profile=PROFILE,
        executor=executor,
    )
    assert ranked.feasible
    _assert_fe_disclosure(
        dict(ranked.law_disclosures[0]),
        law_id=_FE_ASSOCIATION_LAW_ID,
        count=1,
    )
    _assert_fe_disclosure(
        executor.session.law_disclosures[0],
        law_id=_FE_ASSOCIATION_LAW_ID,
        count=1,
    )

    from simulator.battery.residue import predict_mass_loss_fraction
    from simulator.diagnostic_helpers import binary_pot_battery

    def _point(number: float):
        return SimpleNamespace(
            state=SimpleNamespace(
                is_value=True,
                value=SimpleNamespace(
                    kind=SimpleNamespace(value="point"),
                    point=number,
                ),
            )
        )

    experiment = SimpleNamespace(
        experiment_id="disclosure-probe",
        sample=SimpleNamespace(
            mass_kg=_point(0.001),
            initial_composition=SimpleNamespace(
                state=SimpleNamespace(
                    is_value=True,
                    value=SimpleNamespace(
                        components=(("SiO2", 1.0),),
                        amount_basis="mole_fraction",
                    ),
                )
            ),
        ),
        thermal_schedule=SimpleNamespace(total_duration_s=_point(3600.0)),
        apparatus=SimpleNamespace(
            geometry=SimpleNamespace(exposed_area_m2=_point(1.0e-4))
        ),
        method=SimpleNamespace(
            is_value=True,
            value="langmuir_free_evaporation",
        ),
        pressure_environment=SimpleNamespace(total_pressure_Pa=_point(0.0)),
    )
    observation = SimpleNamespace(
        uncertainty=SimpleNamespace(kind=SimpleNamespace(value="none"))
    )
    fake_openimcc = types.ModuleType("openimcc")
    fake_openimcc.load_gas_datapack = lambda: object()
    monkeypatch.setitem(sys.modules, "openimcc", fake_openimcc)
    monkeypatch.setattr(
        binary_pot_battery,
        "_openimcc_gas_channels_and_omission_notices",
        lambda *_args, **_kwargs: (
            (
                ("SiO", ("SiO2", 1.0, -0.5)),
                ("O2", (None, 0.0, 1.0)),
            ),
            (),
        ),
    )
    import simulator.battery.residue as residue

    monkeypatch.setattr(residue, "_mass_loss_sourced_alphas", lambda *_a, **_k: {})
    monkeypatch.setattr(
        residue,
        "_mass_loss_area_inputs",
        lambda *_a, **_k: (1.0e-4, 1.0e-4, 0.0),
    )
    monkeypatch.setattr(
        residue,
        "_mass_loss_thermal_segments",
        lambda *_a, **_k: (300.0, 1.0, ((1800.0, 60.0),)),
    )
    monkeypatch.setattr(
        residue,
        "_make_residue_pressure_model",
        lambda *_a, **_k: (lambda _inventory, _logp: {}),
    )
    evaluations = {"n": 0}

    def _integrate(inventory, *_args, **_kwargs):
        evaluations["n"] += 1
        _fe_pressure("FeO_association_gas")
        return SimpleNamespace(
            refusal=None,
            evaporated_mol={},
            inventory_mol=dict(inventory),
        )

    monkeypatch.setattr(residue, "_hashimoto_integrate_geometry", _integrate)
    prediction = predict_mass_loss_fraction(
        "internal-analytical",
        experiment,
        temperature_K=1800.0,
        observation=observation,
    )
    assert evaluations["n"] >= 1
    assert prediction.mass_loss_fraction == 0.0
    battery_rows = json.loads(prediction.engine_metadata["law_disclosures"])
    _assert_fe_disclosure(
        battery_rows[0],
        law_id=_FE_ASSOCIATION_LAW_ID,
        count=evaluations["n"],
    )


def _memo_host(calls: list[str]):
    from types import SimpleNamespace

    from simulator.evaporation import EvaporationMixin

    host = object.__new__(EvaporationMixin)
    host._liquid_fraction_provider = "declared"
    host._freeze_gate_held_curve_record = None
    host._pt0_store = None
    host.melt = SimpleNamespace(hour=1, p_total_mbar=1000.0)
    host._current_melt_redox_fO2_log = lambda: -8.0
    host._freeze_gate_redox_key_fO2_log = lambda **_kwargs: _kwargs["fO2_log"]
    host._freeze_gate_cache_key = lambda **_kwargs: "memo-key"
    host._freeze_gate_curve_in_progress = False
    host._invalid_projected_liquidus_notice = None
    host._freeze_gate_last_good_curve = None
    host._freeze_gate_provider_outcome = None
    host._record_composition_projected_liquidus_notice_from_curve = (
        lambda _curve: None
    )
    host._freeze_gate_shared_curve_cache = None

    def dispatch(_reasons, **_kwargs):
        calls.append("recompute")
        host._freeze_gate_provider_outcome = "produced"
        return {"tag": "recomputed", "status": "in_range", "source": "stub"}

    host._freeze_gate_curve_from_gate_dispatch = dispatch
    return host


def _memo_record(*, disclosures):
    record = {
        "key": "memo-key",
        "curve": {"tag": "cached", "status": "in_range"},
        "repair_notices": [],
        "disposition": "live",
        "provider_outcome": "produced",
        "origin": {"producer_pid": 1},
    }
    if disclosures is not None:
        record["law_disclosures"] = disclosures
    return record


def test_preseeded_freeze_gate_memo_without_disclosure_recomputes(
    monkeypatch,
) -> None:
    import simulator.engine_binding_admission as admission
    import simulator.reduced_real_determinism as determinism
    from simulator.evaporation import EvaporationMixin
    from simulator.session import SimSession
    from simulator.vapour_rail.run_disclosure import bind_run_registry

    monkeypatch.setattr(
        determinism, "gate_curve_memo_is_cacheable", lambda *_a, **_k: True
    )
    monkeypatch.setattr(
        admission, "gate_curve_memo_origin_matches", lambda *_a, **_k: True
    )
    monkeypatch.setattr(admission, "gate_curve_memo_evidence", lambda *_a, **_k: {})
    monkeypatch.setattr(
        admission,
        "write_gate_curve_memo_record",
        lambda _self, curve, notices, **kwargs: {
            "curve": dict(curve),
            "origin": {"producer_pid": 1},
            "repair_notices": list(notices),
            "disposition": "live",
            "provider_outcome": kwargs.get("provider_outcome"),
        },
    )
    with disclosure_scope(inherit=False) as cold:
        _fe_pressure("FeO_association_gas")
        stored = [dict(item) for item in cold.snapshot()]
    calls: list[str] = []
    host = _memo_host(calls)

    def _call():
        session = SimSession()
        with bind_run_registry(session._law_disclosure):
            record = EvaporationMixin._freeze_gate_curve_record(host)
        return session, record

    host._freeze_gate_liquid_fraction_cache = _memo_record(disclosures=None)
    missed, missed_record = _call()
    assert calls == ["recompute"]
    assert missed_record["curve"]["tag"] == "recomputed"
    assert missed.law_disclosures == ()

    calls.clear()
    host = _memo_host(calls)
    host._freeze_gate_liquid_fraction_cache = _memo_record(disclosures=stored)
    hit, hit_record = _call()
    assert calls == []
    assert hit_record["curve"]["tag"] == "cached"
    _assert_fe_disclosure(
        hit.law_disclosures[0],
        law_id=_FE_ASSOCIATION_LAW_ID,
        count=1,
    )

    calls.clear()
    host = _memo_host(calls)
    host._freeze_gate_liquid_fraction_cache = _memo_record(disclosures=[])
    zero, zero_record = _call()
    assert calls == []
    assert zero_record["curve"]["tag"] == "cached"
    assert zero.law_disclosures == ()

    calls.clear()
    host = _memo_host(calls)
    host._freeze_gate_liquid_fraction_cache = None
    host._freeze_gate_shared_curve_cache = {
        "memo-key": _memo_record(disclosures=None),
    }
    shared_miss, shared_miss_record = _call()
    assert calls == ["recompute"]
    assert shared_miss_record["curve"]["tag"] == "recomputed"
    assert shared_miss.law_disclosures == ()

    calls.clear()
    host = _memo_host(calls)
    host._freeze_gate_liquid_fraction_cache = None
    host._freeze_gate_shared_curve_cache = {
        "memo-key": _memo_record(disclosures=stored),
    }
    shared_hit, shared_hit_record = _call()
    assert calls == []
    assert shared_hit_record["curve"]["tag"] == "cached"
    _assert_fe_disclosure(
        shared_hit.law_disclosures[0],
        law_id=_FE_ASSOCIATION_LAW_ID,
        count=1,
    )

    calls.clear()
    host = _memo_host(calls)
    host._freeze_gate_held_curve_record = {
        "hour": 1,
        "provider": "declared",
        "record": _memo_record(disclosures=None),
    }
    held_miss, held_miss_record = _call()
    assert calls == ["recompute"]
    assert held_miss_record["curve"]["tag"] == "recomputed"
    assert held_miss.law_disclosures == ()
    assert host._freeze_gate_held_curve_record is None

    calls.clear()
    host = _memo_host(calls)
    host._freeze_gate_held_curve_record = {
        "hour": 1,
        "provider": "declared",
        "record": _memo_record(disclosures=stored),
    }
    held_hit, held_hit_record = _call()
    assert calls == []
    assert held_hit_record["curve"]["tag"] == "cached"
    _assert_fe_disclosure(
        held_hit.law_disclosures[0],
        law_id=_FE_ASSOCIATION_LAW_ID,
        count=1,
    )
    assert host._freeze_gate_held_curve_record is not None

    calls.clear()
    host = _memo_host(calls)
    host._freeze_gate_held_curve_record = {
        "hour": 1,
        "provider": "declared",
        "record": _memo_record(disclosures=[]),
    }
    held_zero, held_zero_record = _call()
    assert calls == []
    assert held_zero_record["curve"]["tag"] == "cached"
    assert held_zero.law_disclosures == ()


def _core_gate_curve(tag: str) -> dict:
    return {
        "tag": tag,
        "source": "unit-test",
        "solidus_T_C": 1000.0,
        "liquidus_T_C": 1300.0,
        "path": ((1000.0, 0.0), (1300.0, 1.0)),
    }


def _core_memo_record(*, disclosures, tag: str = "cached") -> dict:
    record = {
        "key": "memo-key",
        "curve": _core_gate_curve(tag),
        "repair_notices": [],
        "disposition": "live",
        "provider_outcome": "produced",
        "origin": {"producer_pid": 1},
    }
    if disclosures is not None:
        record["law_disclosures"] = disclosures
    return record


def _core_gate_host(calls: list[str], recomputed_disclosures):
    from types import SimpleNamespace

    from simulator.core import PyrolysisSimulator

    host = object.__new__(PyrolysisSimulator)
    host._liquid_fraction_provider = "declared"
    host.melt = SimpleNamespace(
        hour=1,
        p_total_mbar=1000.0,
        campaign_hour=1,
        campaign=SimpleNamespace(name="C0"),
    )
    host._current_melt_redox_fO2_log = lambda: -8.0
    host._freeze_gate_redox_key_fO2_log = lambda **kwargs: kwargs["fO2_log"]
    host._freeze_gate_cache_key = lambda **_kwargs: "memo-key"
    host._freeze_gate_curve_in_progress = False
    host._freeze_gate_liquid_fraction_cache = None
    host._freeze_gate_liquid_fraction_curve_memo = {}
    host._last_reduced_real_replay_notices = None

    def _recompute(**_kwargs):
        calls.append("recompute")
        return _core_memo_record(
            disclosures=recomputed_disclosures,
            tag="recomputed",
        )

    host._freeze_gate_curve_record = _recompute
    return host


def test_core_liquidus_memo_misses_without_disclosure_and_replays_known(
    monkeypatch,
) -> None:
    """The core liquidus reader misses an unknown memo and replays a known one.

    Evaporation's held, local, and shared slots already use accept_stored_memo.
    This arms the same gate on the core local cache and the core curve memo,
    and checks that normalization keeps the measured field on the written memo.
    """

    import simulator.engine_binding_admission as admission
    import simulator.reduced_real_determinism as determinism
    from simulator.core import PyrolysisSimulator
    from simulator.session import SimSession
    from simulator.vapour_rail.run_disclosure import bind_run_registry

    monkeypatch.setattr(
        determinism, "gate_curve_memo_is_cacheable", lambda *_a, **_k: True
    )
    monkeypatch.setattr(
        admission, "gate_curve_memo_origin_matches", lambda *_a, **_k: True
    )
    stored = [dict(item) for item in _fe_disclosure_rows()]

    def _call(host):
        session = SimSession()
        with bind_run_registry(session._law_disclosure):
            record = PyrolysisSimulator._melt_redox_liquidus_gate_record(host)
        return session, record

    calls: list[str] = []
    host = _core_gate_host(calls, [])
    host._freeze_gate_liquid_fraction_cache = _core_memo_record(disclosures=None)
    missed, missed_record = _call(host)
    assert calls == ["recompute"]
    assert missed_record["curve"]["tag"] == "recomputed"
    assert missed.law_disclosures == ()
    assert (
        host._freeze_gate_liquid_fraction_curve_memo["memo-key"]["law_disclosures"]
        == []
    )
    assert "law_disclosures" in host._freeze_gate_liquid_fraction_cache

    calls = []
    host = _core_gate_host(calls, [])
    host._freeze_gate_liquid_fraction_cache = _core_memo_record(disclosures=stored)
    hit, hit_record = _call(host)
    assert calls == []
    assert hit_record["curve"]["tag"] == "cached"
    assert hit_record["law_disclosures"][0]["law_id"] == _FE_ASSOCIATION_LAW_ID
    _assert_fe_disclosure(
        hit.law_disclosures[0],
        law_id=_FE_ASSOCIATION_LAW_ID,
        count=1,
    )

    calls = []
    host = _core_gate_host(calls, [])
    host._freeze_gate_liquid_fraction_cache = _core_memo_record(disclosures=[])
    zero, zero_record = _call(host)
    assert calls == []
    assert zero_record["curve"]["tag"] == "cached"
    assert zero.law_disclosures == ()

    calls = []
    host = _core_gate_host(calls, stored)
    host._freeze_gate_liquid_fraction_curve_memo["memo-key"] = _core_memo_record(
        disclosures=None,
        tag="stale",
    )
    memo_miss, memo_miss_record = _call(host)
    assert calls == ["recompute"]
    assert memo_miss_record["curve"]["tag"] == "recomputed"
    assert memo_miss.law_disclosures == ()
    written = host._freeze_gate_liquid_fraction_curve_memo["memo-key"]
    assert written["curve"]["tag"] == "recomputed"
    assert written["law_disclosures"][0]["law_id"] == _FE_ASSOCIATION_LAW_ID

    calls = []
    reader = _core_gate_host(calls, [])
    reader._freeze_gate_liquid_fraction_curve_memo["memo-key"] = written
    memo_hit, memo_hit_record = _call(reader)
    assert calls == []
    assert memo_hit_record["curve"]["tag"] == "recomputed"
    _assert_fe_disclosure(
        memo_hit.law_disclosures[0],
        law_id=_FE_ASSOCIATION_LAW_ID,
        count=1,
    )
    with bind_run_registry(memo_hit._law_disclosure):
        PyrolysisSimulator._melt_redox_liquidus_gate_record(reader)
    # A second read of the same memo adds the stored count once, not twice.
    _assert_fe_disclosure(
        memo_hit.law_disclosures[0],
        law_id=_FE_ASSOCIATION_LAW_ID,
        count=2,
    )

    calls = []
    host = _core_gate_host(calls, stored)
    host._freeze_gate_liquid_fraction_curve_memo["memo-key"] = _core_memo_record(
        disclosures=[]
    )
    memo_zero, memo_zero_record = _call(host)
    assert calls == []
    assert memo_zero_record["curve"]["tag"] == "cached"
    assert memo_zero.law_disclosures == ()


_STANDALONE_HIT_CLASSES = (
    "equilibrium_replay",
    "equilibrium_write_through",
    "gate_replay",
    "held_memo",
    "local_memo",
    "shared_memo",
    "store_exact",
)


class _DisclosureHitStore:
    """Stand-in store. Re-registration uses the production chokepoint."""

    def __init__(self, *, replay: bool, write_through: bool, disclosures) -> None:
        self.replay_enabled = replay
        self.write_through_enabled = write_through
        self.capture_enabled = False
        self.disclosures = disclosures
        self.calls: list[str] = []

    def replay_equilibrium(self, _sim):
        self.calls.append("replay_equilibrium")
        accept_stored_memo({"law_disclosures": self.disclosures})
        return {"status": "replayed"}

    def cached_equilibrium(self, _sim):
        self.calls.append("cached_equilibrium")
        accept_stored_memo({"law_disclosures": self.disclosures})
        return {"status": "cached"}

    def replay_gate_curve(self, _sim, **_kwargs):
        self.calls.append("replay_gate_curve")
        self._accepted_hit_disclosures = accept_stored_memo(
            {"law_disclosures": self.disclosures}
        )
        return {"status": "in_range", "tag": "replayed"}


def _patch_gate_memo_live(monkeypatch) -> None:
    import simulator.engine_binding_admission as admission
    import simulator.reduced_real_determinism as determinism

    monkeypatch.setattr(
        determinism, "gate_curve_memo_is_cacheable", lambda *_a, **_k: True
    )
    monkeypatch.setattr(
        admission, "gate_curve_memo_origin_matches", lambda *_a, **_k: True
    )
    monkeypatch.setattr(admission, "gate_curve_memo_evidence", lambda *_a, **_k: {})
    monkeypatch.setattr(
        admission,
        "write_gate_curve_memo_record",
        lambda _self, curve, notices, **kwargs: {
            "curve": dict(curve),
            "origin": {"producer_pid": 1},
            "repair_notices": list(notices),
            "disposition": "live",
            "provider_outcome": kwargs.get("provider_outcome"),
        },
    )


def _fe_disclosure_rows() -> tuple[dict, ...]:
    with disclosure_scope(inherit=False) as cold:
        _fe_pressure("FeO_association_gas")
        return tuple(dict(item) for item in cold.snapshot())


def _standalone_hit(kind: str, disclosures, monkeypatch):
    """One production hit with no session registry already bound."""

    from types import SimpleNamespace

    from simulator.core import PyrolysisSimulator
    from simulator.evaporation import EvaporationMixin
    from simulator.reduced_real_determinism import PT0DeterminismStore

    assert active_snapshot() == ()
    if kind in {"gate_replay", "held_memo", "local_memo", "shared_memo"}:
        _patch_gate_memo_live(monkeypatch)
    if kind == "equilibrium_replay":
        store = _DisclosureHitStore(
            replay=True, write_through=False, disclosures=disclosures
        )
        owner = SimpleNamespace(_pt0_store=lambda: store)
        return owner, store.calls, PyrolysisSimulator._get_equilibrium(owner)
    if kind == "equilibrium_write_through":
        store = _DisclosureHitStore(
            replay=False, write_through=True, disclosures=disclosures
        )
        owner = SimpleNamespace(_pt0_store=lambda: store)
        return owner, store.calls, PyrolysisSimulator._get_equilibrium(owner)
    if kind == "gate_replay":
        store = _DisclosureHitStore(
            replay=True, write_through=False, disclosures=disclosures
        )
        calls: list[str] = []
        host = _memo_host(calls)
        host._pt0_store = lambda: store
        return host, store.calls, EvaporationMixin._freeze_gate_curve_record(host)
    if kind == "held_memo":
        calls = []
        host = _memo_host(calls)
        host._freeze_gate_held_curve_record = {
            "hour": 1,
            "provider": "declared",
            "record": _memo_record(disclosures=disclosures),
        }
        return host, calls, EvaporationMixin._freeze_gate_curve_record(host)
    if kind == "local_memo":
        calls = []
        host = _memo_host(calls)
        host._freeze_gate_liquid_fraction_cache = _memo_record(
            disclosures=disclosures
        )
        return host, calls, EvaporationMixin._freeze_gate_curve_record(host)
    if kind == "shared_memo":
        calls = []
        host = _memo_host(calls)
        host._freeze_gate_liquid_fraction_cache = None
        host._freeze_gate_shared_curve_cache = {
            "memo-key": _memo_record(disclosures=disclosures),
        }
        return host, calls, EvaporationMixin._freeze_gate_curve_record(host)
    store = PT0DeterminismStore("capture")
    owner = SimpleNamespace()
    payload = store._accept_cached_hit(
        {"payload": {"tag": "stored"}, "law_disclosures": disclosures},
        owner=owner,
    )
    return owner, [], payload


@pytest.mark.parametrize("kind", _STANDALONE_HIT_CLASSES)
def test_standalone_hit_publishes_a_known_disclosure(kind: str, monkeypatch) -> None:
    """Every physics hit class publishes the stored Fe law on its own owner.

    No session registry is active before the call. A hit must not return the
    value and leave the owner's disclosure empty.
    """

    stored = _fe_disclosure_rows()
    owner, calls, payload = _standalone_hit(kind, stored, monkeypatch)
    assert payload is not None
    if kind in {"held_memo", "local_memo", "shared_memo"}:
        assert calls == []
        assert payload["curve"]["tag"] == "cached"
    elif kind == "gate_replay":
        assert calls == ["replay_gate_curve"]
        assert payload["law_disclosures"][0]["law_id"] == _FE_ASSOCIATION_LAW_ID
    elif kind == "equilibrium_replay":
        assert calls == ["replay_equilibrium"]
        assert payload["status"] == "replayed"
    elif kind == "equilibrium_write_through":
        assert calls == ["cached_equilibrium"]
        assert payload["status"] == "cached"
    else:
        assert payload["tag"] == "stored"
    assert active_snapshot() == ()
    _assert_fe_disclosure(
        owner.law_disclosures[0],
        law_id=_FE_ASSOCIATION_LAW_ID,
        count=1,
    )
    other, _, other_payload = _standalone_hit("store_exact", (), monkeypatch)
    assert other_payload is not None
    assert tuple(getattr(other, "law_disclosures", ()) or ()) == ()
    assert owner.law_disclosures[0]["law_id"] == _FE_ASSOCIATION_LAW_ID
    assert active_snapshot() == ()


@pytest.mark.parametrize("kind", _STANDALONE_HIT_CLASSES)
def test_standalone_hit_leaves_an_incomplete_disclosure_unknown(
    kind: str, monkeypatch
) -> None:
    """A law id and a count are not a known disclosure on any hit class."""

    incomplete = [{"law_id": "Fe:nasa_cea_9", "evaluation_count": 1}]
    owner, calls, payload = _standalone_hit(kind, incomplete, monkeypatch)
    published = tuple(getattr(owner, "law_disclosures", ()) or ())
    assert published == ()
    assert active_snapshot() == ()
    if kind in {"held_memo", "local_memo", "shared_memo"}:
        assert calls == ["recompute"]
        assert payload["curve"]["tag"] == "recomputed"
    elif kind == "store_exact":
        assert payload is None


def test_receipt_and_evaluator_diagnostic_notices_both_survive() -> None:
    from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
    from simulator.vapour_rail.channels import (
        CHANNEL_O2,
        LEGACY_FO2_PLANE,
        REACTION_PLANE_TRANSPORT_HEADSPACE,
        ReactionThermoInputs,
        o2_potential_from_pO2_bar,
    )

    provider = BuiltinVaporPressureProvider(_live_vapor())
    evaluator = provider._vapour_rail_catalog.evaluator_for_hot_train(
        "FeO_association_gas"
    )
    plane = (
        LEGACY_FO2_PLANE.get(evaluator.oxygen_fugacity_channel or "")
        or REACTION_PLANE_TRANSPORT_HEADSPACE
    )
    potential = o2_potential_from_pO2_bar(
        pO2_bar=1.0e-9,
        temperature_K=1800.0,
        reaction_plane=plane,
        pO2_reference_bar=evaluator.pO2_reference_bar or 1.0,
    )
    receipt_notice = {
        "authority": "diagnostic/extrapolated",
        "reason": "oxygen receipt marker",
        "marker": "oxygen-receipt",
        "closes_with": "t-1723",
        "executed_species_id": "Fe",
        "law_provenance": "receipt marker, not the executed pseudo law",
        "provenance_status": "diagnostic_only",
        "comparators": {
            "gap_dex_law_vs_rail": 4.0,
            "gap_dex_law_vs_vaporock": 5.0,
        },
    }
    potential = replace(
        potential,
        observation_or_setpoint_receipt={
            "diagnostic_admission_notice": receipt_notice,
        },
    )
    evaluation = evaluator.evaluate(
        1800.0,
        source_activity=1.0,
        reaction_inputs=ReactionThermoInputs(
            reaction_id=None,
            state_fingerprint=None,
            channels={CHANNEL_O2: potential},
        ),
    )
    assert math.isfinite(evaluation.pressure_pa)
    assert evaluation.pressure_pa > 0.0
    published = evaluation.diagnostic_admission_notice
    assert isinstance(published, list)
    assert len(published) == 2
    by_marker = {item.get("marker"): item for item in published}
    assert "oxygen-receipt" in by_marker
    executed = next(item for item in published if item.get("marker") != "oxygen-receipt")
    assert executed.get("closes_with") == "t-1723"
    duplicate = replace(
        potential,
        observation_or_setpoint_receipt={
            "diagnostic_admission_notice": dict(executed),
        },
    )
    collapsed = evaluator.evaluate(
        1800.0,
        source_activity=1.0,
        reaction_inputs=ReactionThermoInputs(
            reaction_id=None,
            state_fingerprint=None,
            channels={CHANNEL_O2: duplicate},
        ),
    )
    notice = collapsed.diagnostic_admission_notice
    assert isinstance(notice, dict)
    assert notice.get("closes_with") == "t-1723"
    assert notice.get("marker") is None


# Session verbs that execute the simulator. A new public verb must be added
# here or to the read-only set. Executing verbs bind the session registry.
_SESSION_VERBS_THAT_EXECUTE = frozenset({"start", "advance", "decide", "adjust"})
_SESSION_VERBS_THAT_ONLY_READ = frozenset({
    "law_disclosures",
    "simulator",
    "pause",
    "resume",
    "is_complete",
    "snapshot",
    "pending_decision",
    "result_document",
    "per_hour_summaries",
    "operator_decisions",
})


def test_every_public_session_verb_binds_or_is_read_only() -> None:
    """Every session entry is classified. Executing verbs bind the registry."""

    import ast

    from simulator.session import SimSession

    source = inspect.getsource(SimSession)
    tree = ast.parse(source)
    class_node = tree.body[0]
    public: set[str] = set()
    binders: set[str] = set()
    for node in class_node.body:
        if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
            continue
        public.add(node.name)
        for child in ast.walk(node):
            if not isinstance(child, ast.With):
                continue
            for item in child.items:
                expr = item.context_expr
                func = expr.func if isinstance(expr, ast.Call) else expr
                if isinstance(func, ast.Attribute) and func.attr == "_bound_law_disclosure":
                    binders.add(node.name)
    assert public == _SESSION_VERBS_THAT_EXECUTE | _SESSION_VERBS_THAT_ONLY_READ
    assert binders == _SESSION_VERBS_THAT_EXECUTE


def _evaluate_fe_on_private_registry() -> None:
    """The forecast pattern: a detached owner with its own registry."""

    from types import SimpleNamespace

    from simulator.vapour_rail.run_disclosure import RunDisclosure

    private = RunDisclosure()
    context = SimpleNamespace(
        _law_disclosure_registry=private,
        law_disclosures=(),
    )
    with bind_simulation_run(context):
        _fe_pressure("FeO_association_gas")


def _session_for_detached_verb():
    from types import SimpleNamespace

    from simulator.core import CampaignPhase
    from simulator.session import SimSession
    from simulator.state import DecisionPoint, DecisionType

    session = SimSession()
    calls: list[str] = []

    def forecast(field: str) -> None:
        calls.append(field)
        _evaluate_fe_on_private_registry()

    melt = SimpleNamespace(
        campaign=CampaignPhase.C0,
        pO2_mbar=0.0,
        p_total_mbar=1.0,
        atmosphere=None,
        validate_melt_pressures=lambda: None,
    )
    sim = SimpleNamespace(
        melt=melt,
        campaign_mgr=SimpleNamespace(
            overrides={},
            _refuse_unknown_override_fields=lambda *_a, **_k: None,
            runtime_po2_ceiling_mbar=lambda *_a, **_k: 10.0,
            _validate_runtime_pressure_overrides=lambda *_a, **_k: None,
        ),
        _handle_c0_forecast_adjustment=forecast,
        campaign_endpoint_refused=lambda: False,
        pending_decision=DecisionPoint(
            decision_type=DecisionType.TI_RETENTION,
            options=["keep"],
        ),
        apply_decision=lambda *_a, **_k: forecast("decide"),
    )
    session._sim = sim
    return session, calls


@pytest.mark.parametrize(
    "verb",
    ["adjust_pO2", "adjust_campaign_override", "decide"],
)
def test_detached_session_verb_registers_on_the_session(verb: str) -> None:
    """A law evaluated on a private registry still reaches the session.

    adjust and decide are the session verbs that run a forecast outside
    advance. Each arm executes Fe only inside ``bind_simulation_run`` on a
    detached owner. Deleting ``_bound_law_disclosure`` around that verb
    leaves the session snapshot empty.
    """

    session, calls = _session_for_detached_verb()
    if verb == "adjust_pO2":
        session.adjust("pO2_mbar", 0.1)
        assert calls == ["C0.pO2_mbar"]
    elif verb == "adjust_campaign_override":
        session.adjust(
            "campaign_override",
            0.2,
            campaign="C0",
            field="pO2_mbar",
        )
        assert calls == ["C0.pO2_mbar"]
    else:
        session.decide("keep")
        assert calls == ["decide"]
    assert len(session.law_disclosures) == 1
    _assert_fe_disclosure(
        session.law_disclosures[0],
        law_id=_FE_ASSOCIATION_LAW_ID,
        count=1,
    )
    assert session.simulator.law_disclosures == session.law_disclosures


def test_c0_forecast_context_shares_the_parent_registry(monkeypatch) -> None:
    """The detached forecast writes the parent's registry, not a copy.

    Deleting the share in ``_build_c0_forecast_context`` leaves the parent
    snapshot empty after the context evaluates Fe.
    """

    from simulator.chemistry.kernel.registry import ProviderRegistry
    from simulator.core import PyrolysisSimulator
    from simulator.vapour_rail.run_disclosure import RunDisclosure

    monkeypatch.setattr(
        "simulator.core.snapshot_atom_ledger",
        lambda ledger: ledger,
    )
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    registry = RunDisclosure()
    sim._law_disclosure_registry = registry
    sim.law_disclosures = ()
    sim.backend = None
    sim._c0_forecast_backend_factory = None
    sim._pt0_determinism_store = None
    sim.atom_ledger = object()
    sim._chem_registry = ProviderRegistry()
    sim._chem_kernel = None
    sim._build_chemistry_kernel = lambda: None

    context, status = sim._build_c0_forecast_context()
    assert status == ""
    assert context is not None
    assert context is not sim
    assert context._law_disclosure_registry is registry
    with bind_simulation_run(context):
        _fe_pressure("FeO_association_gas")
    assert len(registry.snapshot()) == 1
    _assert_fe_disclosure(
        registry.snapshot()[0],
        law_id=_FE_ASSOCIATION_LAW_ID,
        count=1,
    )


def test_predict_c0_exit_publishes_the_parent_registry() -> None:
    """Dropping the forecast context does not drop its disclosures.

    Deleting the publish at the end of ``_predict_c0_exit`` leaves
    ``sim.law_disclosures`` empty while the registry holds Fe.
    """

    from types import SimpleNamespace

    from simulator.core import PyrolysisSimulator
    from simulator.vapour_rail.run_disclosure import RunDisclosure

    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    registry = RunDisclosure()
    sim._law_disclosure_registry = registry
    sim.law_disclosures = ()
    context = SimpleNamespace(_law_disclosure_registry=registry)

    def evaluate(**_kwargs):
        with bind_simulation_run(context):
            _fe_pressure("FeO_association_gas")
        return {"prediction_status": "ok"}

    context._evaluate_c0_exit_forecast = evaluate
    sim._build_c0_forecast_context = lambda: (context, "")
    prediction = sim._predict_c0_exit()
    assert prediction["prediction_status"] == "ok"
    _assert_fe_disclosure(
        registry.snapshot()[0],
        law_id=_FE_ASSOCIATION_LAW_ID,
        count=1,
    )
    _assert_fe_disclosure(
        sim.law_disclosures[0],
        law_id=_FE_ASSOCIATION_LAW_ID,
        count=1,
    )


def test_every_metadata_override_installs_the_measured_disclosure() -> None:
    """Each override writer installs the measurement after the override."""

    import ast

    path = _ROOT / "simulator" / "runner" / "__init__.py"
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source)
    override_markers = (
        "metadata_overrides.items()",
        "overrides.items()",
        ".update(metadata_overrides)",
    )
    offenders: list[str] = []
    writers: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name == "_install_measured_law_disclosures":
            continue
        start = node.lineno
        end = node.end_lineno or start
        body = lines[start - 1:end]
        hits = [
            start + offset
            for offset, line in enumerate(body)
            if any(marker in line for marker in override_markers)
        ]
        if not hits:
            continue
        writers.append(node.name)
        installs = [
            start + offset
            for offset, line in enumerate(body)
            if "_install_measured_law_disclosures" in line
        ]
        if not any(install > hits[0] for install in installs):
            offenders.append(node.name)
    assert set(writers) == {
        "_build_output_detail",
        "_runner_failure_result",
        "main",
    }
    assert len(writers) == 3
    assert offenders == []


@pytest.mark.parametrize("publisher", ["detail", "failure"])
def test_publishers_keep_measured_disclosures_over_overrides(
    publisher: str,
) -> None:
    """An override of law_disclosures cannot erase Fe.

    ``detail`` calls ``PyrolysisRun._build_output_detail``. ``failure`` calls
    ``_runner_failure_result``. Deleting the installer in either function
    leaves that arm's run_metadata disclosure empty.
    """

    from types import SimpleNamespace

    from simulator.runner import PyrolysisRun, RunnerError, _runner_failure_result
    from simulator.session import SimSession

    session = SimSession()
    session._sim = SimpleNamespace(
        record=SimpleNamespace(
            liquid_fraction_provider="analytical",
            snapshots=(),
        ),
        melt=SimpleNamespace(hour=1),
    )
    with session._bound_law_disclosure():
        _fe_pressure("FeO_association_gas")
    execution = SimpleNamespace(
        session=session,
        simulator=session.simulator,
        per_hour=(),
        snapshots=(),
        backend_status="unavailable",
        backend_authoritative=False,
        refusal_diagnostic={},
        campaigns_elapsed=1.0,
        shadow_trace=(),
        status="failed",
        reason="",
        error_message="",
        reduced_real_cache={},
        envelope_detail_unavailable="",
    )
    if publisher == "failure":
        payload = _runner_failure_result(
            error=RunnerError("disclosure probe"),
            feedstock_id="lunar_mare_low_ti",
            campaign="C0",
            hours=1,
            mass_kg=1.0,
            additives_kg={},
            track="pyrolysis",
            backend_name="internal-analytical",
            engines={},
            metadata_overrides={"law_disclosures": []},
            execution=execution,
        )
    else:
        run = PyrolysisRun(
            feedstock_id="lunar_mare_low_ti",
            campaign="C0",
            hours=1,
            run_metadata_overrides={
                "started_at_utc": "2026-01-01T00:00:00Z",
                "kernel_commit_sha": "disclosure-fixture",
                "law_disclosures": [],
            },
        )
        execution.session._config = SimpleNamespace(
            additives_kg={},
            setpoints={},
        )
        execution.simulator.record = SimpleNamespace(
            liquid_fraction_provider="analytical",
            snapshots=(),
        )
        execution.simulator.melt = SimpleNamespace(hour=1)
        payload = run._build_output_detail(execution)
    _assert_fe_disclosure(
        payload["run_metadata"]["law_disclosures"][0],
        law_id=_FE_ASSOCIATION_LAW_ID,
        count=1,
    )


def test_pt0_entries_are_private() -> None:
    """The raw PT0 dict is not a public attribute."""

    store = PT0DeterminismStore("capture")
    assert not hasattr(store, "entries")
    assert isinstance(store._entries, dict)


def test_cached_reads_reregister_only_through_accept_stored_memo() -> None:
    """No production reader calls reregister_cached_disclosures itself."""

    import subprocess

    listed = subprocess.check_output(
        ["git", "ls-files", "simulator", "engines"],
        cwd=_ROOT,
        text=True,
    )
    offenders: list[str] = []
    definition = 0
    for line in listed.splitlines():
        if not line.endswith(".py"):
            continue
        text = (_ROOT / line).read_text(encoding="utf-8")
        count = text.count("reregister_cached_disclosures(")
        if line.endswith("simulator/vapour_rail/run_disclosure.py"):
            definition = count
            continue
        if count:
            offenders.append(f"{line}:{count}")
    assert offenders == []
    # The definition plus the one call inside publish_stored_disclosures.
    assert definition == 2


def test_cache_inventory_matches_the_production_scan() -> None:
    """A new lru_cache or memo dict fails until CACHE_SITES names it."""

    from simulator.vapour_rail.disclosure_cache_inventory import (
        CACHE_SITES,
        DISCLOSURE_CARRYING,
        scan_cache_sites,
    )

    scanned = scan_cache_sites(_ROOT)
    assert set(scanned) == set(CACHE_SITES)
    for site_id, row in CACHE_SITES.items():
        assert row["how"]
        assert row["carries_disclosure"] is (site_id in DISCLOSURE_CARRYING)
        if row["carries_disclosure"]:
            assert site_id in _DISCLOSURE_EXERCISERS
    residue = (_ROOT / "simulator/battery/residue.py").read_text(encoding="utf-8")
    assert residue.count("_cached_pressure_hit(") == 3
    assert residue.count("_remember_cached_pressures(") == 3
    score = (_ROOT / "simulator/battery/score.py").read_text(encoding="utf-8")
    assert "row.experiment_id: row for row in rows" in score
    assert "refusal_detail=provenance" in score
    sossi = score.split("def _sossi_residue_prediction(", 1)[1].split("\ndef ", 1)[0]
    assert sossi.count("_cached_sossi_prediction(") == 2
    assert "cache[cache_key].get(" not in sossi
    sossi_read = score.split("def _cached_sossi_prediction(", 1)[1].split("\ndef ", 1)[0]
    assert "_accept_cached_sossi_row(" in sossi_read
    assert "accept_stored_memo(" in score.split("def _accept_cached_sossi_row(", 1)[1].split(
        "\ndef ", 1
    )[0]
    melt = (_ROOT / "simulator/diagnostic_helpers/extract_reproduction.py").read_text(
        encoding="utf-8"
    )
    assert melt.count("_MELT_PSAT_CACHE.get(") == 1
    assert melt.count("_MELT_PSAT_CACHE[") == 1
    melt_read = melt.split("def _read_melt_psat_cache(", 1)[1].split("\ndef ", 1)[0]
    assert "_MELT_PSAT_CACHE.get(" in melt_read
    assert "accept_stored_memo(" in melt_read
    melt_body = melt.split("def _engine_melt_psat_pa(", 1)[1].split("\ndef ", 1)[0]
    assert "_MELT_PSAT_CACHE" not in melt_body
    kems = (_ROOT / "simulator/diagnostic_helpers/kems.py").read_text(encoding="utf-8")
    assert kems.count("_surface_cache.get(") == 0
    assert kems.count("_surface_cache[") == 0
    kems_read = kems.split("def _read_kems_surface_cache(", 1)[1].split("\ndef ", 1)[0]
    assert "cache.get(" in kems_read
    assert "accept_stored_memo(" in kems_read
    kems_store = kems.split("def _store_kems_surface_cache(", 1)[1].split(
        "\ndef ", 1
    )[0]
    assert "cache[key]" in kems_store
    kems_eval = kems.split("def evaluate(", 1)[1].split("\ndef ", 1)[0]
    assert "_surface_cache[" not in kems_eval
    assert "_surface_cache.get(" not in kems_eval
    assert kems_eval.count("_read_kems_surface_cache(") == 1
    assert kems_eval.count("_store_kems_surface_cache(") == 1
    hashimoto = score.split("def _hashimoto_residue_prediction(", 1)[1].split(
        "\ndef ", 1
    )[0]
    assert "rows_by_key = cache[cache_key]" not in hashimoto
    assert hashimoto.count("_cached_hashimoto_cohort(") == 2
    knudsen = score.split("def _knudsen_residue_prediction(", 1)[1].split(
        "\ndef ", 1
    )[0]
    assert "result = cache[cache_key]" not in knudsen
    assert "_cached_knudsen_result(" in knudsen
    for reader in ("_cached_hashimoto_cohort", "_cached_knudsen_result"):
        body = score.split(f"def {reader}(", 1)[1].split("\ndef ", 1)[0]
        assert "accept_stored_memo(" in body
        assert ".pop(" in body
    from simulator.vapour_rail.disclosure_cache_inventory import (
        classify_controller_cache_line,
        scan_controller_cache_lines,
        unclassified_controller_cache_lines,
    )

    assert unclassified_controller_cache_lines(_ROOT) == []
    for path, lineno, text in scan_controller_cache_lines(_ROOT):
        classified = classify_controller_cache_line(path, lineno, text)
        assert classified is not None, f"{path}:{lineno}:{text.strip()}"
        kind, reason, site_id = classified
        assert reason
        if kind == "carrying":
            assert site_id in DISCLOSURE_CARRYING
            assert site_id in _DISCLOSURE_EXERCISERS
        else:
            assert kind == "inert"
            assert site_id is None


def _assert_replayed_fe(snapshot) -> None:
    match = [item for item in snapshot if item["law_id"] == _FE_ASSOCIATION_LAW_ID]
    assert match
    _assert_fe_disclosure(
        match[0],
        law_id=_FE_ASSOCIATION_LAW_ID,
        count=match[0]["evaluation_count"],
    )
    assert match[0]["evaluation_count"] >= 1


def _exercise_pt0_memory(_tmp_path, _monkeypatch) -> None:
    store = PT0DeterminismStore("capture")
    fe_a_key, _fe_b, _clean, _cold = _capture_two_fe_and_one_clean(store)
    with disclosure_scope(inherit=False) as run_b:
        store._lookup("freeze_gate_curve", fe_a_key)
        _assert_replayed_fe(run_b.snapshot())


def _exercise_pt1(tmp_path, _monkeypatch) -> None:
    db_path = tmp_path / "inventory-pt1.sqlite"
    capture = PT0DeterminismStore("capture", db_path=db_path)
    fe_a_key, _fe_b, _clean, _cold = _capture_two_fe_and_one_clean(capture)
    replay = PT0DeterminismStore("replay", db_path=db_path)
    with disclosure_scope(inherit=False) as run_b:
        replay._lookup("freeze_gate_curve", fe_a_key)
        _assert_replayed_fe(run_b.snapshot())


def _exercise_result_store(tmp_path, _monkeypatch) -> None:
    spec = _base_spec(campaign="inventory-fe")
    path = tmp_path / "inventory-optimizer.sqlite"
    store = ResultStore(
        path,
        current_code_version=spec.code_version,
        current_data_digests=spec.data_digests,
    )
    with disclosure_scope(inherit=False):
        with computation_disclosure() as first:
            _fe_pressure("FeO_association_gas")
            one = first.snapshot()
    store.store(
        spec,
        replace(_scored(spec), law_disclosures=one),
        created_at="2026-11-11T00:00:00Z",
    )
    with disclosure_scope(inherit=False) as run_b:
        loaded = store.lookup(spec)
        assert loaded is not None
        _assert_replayed_fe(run_b.snapshot())


def _exercise_journal(_tmp_path, _monkeypatch) -> None:
    spec = _base_spec(campaign="inventory-journal")
    with disclosure_scope(inherit=False):
        with computation_disclosure() as first:
            _fe_pressure("FeO_association_gas")
            one = first.snapshot()
    payload = _scored_result_journal_payload(
        replace(_scored(spec), law_disclosures=one)
    )
    with disclosure_scope(inherit=False) as run_b:
        _scored_result_from_journal_payload(payload)
        _assert_replayed_fe(run_b.snapshot())


def _exercise_held(_tmp_path, monkeypatch) -> None:
    stored = _fe_disclosure_rows()
    owner, _calls, payload = _standalone_hit("held_memo", stored, monkeypatch)
    assert payload["law_disclosures"][0]["law_id"] == _FE_ASSOCIATION_LAW_ID
    _assert_replayed_fe(owner.law_disclosures)


def _exercise_local(_tmp_path, monkeypatch) -> None:
    stored = _fe_disclosure_rows()
    owner, _calls, payload = _standalone_hit("local_memo", stored, monkeypatch)
    assert payload["law_disclosures"][0]["law_id"] == _FE_ASSOCIATION_LAW_ID
    _assert_replayed_fe(owner.law_disclosures)


def _exercise_shared(_tmp_path, monkeypatch) -> None:
    stored = _fe_disclosure_rows()
    owner, _calls, payload = _standalone_hit("shared_memo", stored, monkeypatch)
    assert payload["law_disclosures"][0]["law_id"] == _FE_ASSOCIATION_LAW_ID
    _assert_replayed_fe(owner.law_disclosures)


def _exercise_gate_replay(_tmp_path, monkeypatch) -> None:
    stored = _fe_disclosure_rows()
    owner, _calls, payload = _standalone_hit("gate_replay", stored, monkeypatch)
    assert payload["law_disclosures"][0]["law_id"] == _FE_ASSOCIATION_LAW_ID
    _assert_replayed_fe(owner.law_disclosures)


def _exercise_core_slot(monkeypatch, *, memo: bool) -> None:
    import simulator.engine_binding_admission as admission
    import simulator.reduced_real_determinism as determinism
    from simulator.core import PyrolysisSimulator
    from simulator.session import SimSession
    from simulator.vapour_rail.run_disclosure import bind_run_registry

    monkeypatch.setattr(
        determinism, "gate_curve_memo_is_cacheable", lambda *_a, **_k: True
    )
    monkeypatch.setattr(
        admission, "gate_curve_memo_origin_matches", lambda *_a, **_k: True
    )
    stored = [dict(item) for item in _fe_disclosure_rows()]
    host = _core_gate_host([], [])
    record = _core_memo_record(disclosures=stored)
    if memo:
        host._freeze_gate_liquid_fraction_curve_memo["memo-key"] = record
    else:
        host._freeze_gate_liquid_fraction_cache = record
    session = SimSession()
    with bind_run_registry(session._law_disclosure):
        PyrolysisSimulator._melt_redox_liquidus_gate_record(host)
    _assert_replayed_fe(session.law_disclosures)


def _exercise_core_cache(_tmp_path, monkeypatch) -> None:
    _exercise_core_slot(monkeypatch, memo=False)


def _exercise_core_memo(_tmp_path, monkeypatch) -> None:
    _exercise_core_slot(monkeypatch, memo=True)


def _openimcc_activity_state() -> object:
    from types import SimpleNamespace

    return SimpleNamespace(
        parent_oxide_activities={"FeO": 1.0},
        parent_oxides=("FeO",),
        flags=(),
        extrapolated=False,
        pack_model_id="b119-disclosure",
        pack_version="b119-disclosure",
        pack_digest="b119-disclosure",
        openimcc_version="b119-disclosure",
    )


def _counting_openimcc_eval(monkeypatch, calls: dict[str, int]):
    """Replace the worker-pool eval so the production closure stays in-process."""

    import simulator.battery.residue as residue

    def fake_evaluate(_evaluate, *, timeout_s, **_kwargs):
        del timeout_s
        calls["n"] += 1
        _fe_pressure("FeO_association_gas")
        return _openimcc_activity_state()

    monkeypatch.setattr(residue, "_evaluate_openimcc_isolated", fake_evaluate)


def _stored_pressure_record(cache: dict) -> dict:
    record = next(iter(cache.values()))
    assert isinstance(record, dict)
    assert "law_disclosures" in record
    assert "pressures" in record
    return record


def _replay_pressure_closure(model, composition, cache, calls) -> None:
    """Cold run A stores the disclosure. Fresh run B replays it. A stripped row recomputes."""

    with disclosure_scope(inherit=False) as run_a:
        first = model(composition, -10.0)
        assert isinstance(first, dict) and first
        assert calls["n"] == 1
        _assert_replayed_fe(run_a.snapshot())
    stored = _stored_pressure_record(cache)
    with disclosure_scope(inherit=False) as run_b:
        second = model(composition, -10.0)
        assert second == first
        assert calls["n"] == 1
        _assert_replayed_fe(run_b.snapshot())
    stored.pop("law_disclosures")
    with disclosure_scope(inherit=False) as run_c:
        third = model(composition, -10.0)
        assert third == first
        assert calls["n"] == 2
        _assert_replayed_fe(run_c.snapshot())


def _pressure_cache_dict(model) -> dict:
    """The production closure's private pressure store, not a test-owned dict."""

    for cell in model.__closure__ or ():
        value = cell.cell_contents
        if not isinstance(value, dict) or not value:
            continue
        sample = next(iter(value.values()))
        if isinstance(sample, dict) and "pressures" in sample:
            return value
    raise AssertionError("production pressure closure did not store a disclosure")


def _exercise_pressure_cache(_tmp_path, monkeypatch) -> None:
    """Warm ``pressure_cache`` through the Sossi OpenIMCC production closure."""

    from types import SimpleNamespace

    from simulator.battery.residue import _sossi_openimcc_pressure_model

    calls = {"n": 0}
    _counting_openimcc_eval(monkeypatch, calls)
    cache: dict = {}
    bridge = SimpleNamespace(
        _cleaned_melt_policy=lambda composition: (dict(composition), {"policy": "test"}),
        evaluate=lambda **_kwargs: None,
    )
    model = _sossi_openimcc_pressure_model(
        cache,
        temperature_K=1750.0,
        gas_channels=(("Fe", "FeO", 1.0, 0.5),),
        gas_pack=object(),
        pO2_exponents={"Fe": -0.5},
        mno_channel_active=False,
        melt_pack_identity={},
        melt_activity_input_policy={},
        melt_activity_extensions={},
        openimcc_bridge=bridge,
        evaluate_gas=lambda *_args, **_kwargs: {"Fe": 1.0},
        nonconvergence_error=RuntimeError,
    )
    _replay_pressure_closure(model, {"FeO": 1.0}, cache, calls)


def _exercise_pressure_state_cache(_tmp_path, monkeypatch) -> None:
    """Warm ``pressure_state_cache`` through ``_make_residue_pressure_model``."""

    import sys
    from types import SimpleNamespace

    import simulator.battery.residue as residue

    calls = {"n": 0}
    _counting_openimcc_eval(monkeypatch, calls)
    monkeypatch.setitem(
        sys.modules,
        "openimcc",
        SimpleNamespace(evaluate_gas=lambda *_args, **_kwargs: {"Fe": 1.0}),
    )
    monkeypatch.setitem(
        sys.modules,
        "openimcc.kernel",
        SimpleNamespace(ImccNonconvergenceError=RuntimeError),
    )
    model = residue._make_residue_pressure_model(
        "openimcc",
        (("Fe", ("FeO", 1.0, 0.5)),),
        object(),
        temperature_K=1750.0,
        total_pressure_Pa=0.0,
        engine_metadata={},
        engine_flags=set(),
        missing_channels_reason="residue_pressure_channels_missing",
        caller="Knudsen residue prediction",
    )
    cache = _pressure_cache_dict  # bound after the cold call fills it
    composition = {"FeO": 1.0}
    with disclosure_scope(inherit=False) as run_a:
        first = model(composition, -10.0)
        assert isinstance(first, dict) and math.isfinite(first["Fe"])
        assert calls["n"] == 1
        _assert_replayed_fe(run_a.snapshot())
    store = cache(model)
    with disclosure_scope(inherit=False) as run_b:
        second = model(composition, -10.0)
        assert second == first
        assert calls["n"] == 1
        _assert_replayed_fe(run_b.snapshot())
    _stored_pressure_record(store).pop("law_disclosures")
    with disclosure_scope(inherit=False) as run_c:
        third = model(composition, -10.0)
        assert third == first
        assert calls["n"] == 2
        _assert_replayed_fe(run_c.snapshot())


def _one_internal_analytical_sossi_row(monkeypatch):
    from types import SimpleNamespace

    import yaml

    import simulator.battery.residue as residue

    def no_evaporation(inventory, *_args, **_kwargs):
        return SimpleNamespace(
            residue_mol=dict(inventory),
            buffer_oxygen_exchange_mol=0.0,
        )

    monkeypatch.setattr(residue, "integrate_residue_inventory", no_evaporation)
    source = yaml.safe_load(
        (_ROOT / "data/literature/extracts/kems-012-sossi-2019.yaml").read_text(
            encoding="utf-8"
        )
    )
    experiment = source["experiments"][0]
    experiment_id = experiment["experiment_id"]
    starting_trace = {
        element: source["species"][element]["observations"][0]["values"][
            "starting_measured_ppm"
        ]
        for element in ("Mn", "Ti")
    }
    catalog = yaml.safe_load(
        (_ROOT / "data/vapor_pressures.yaml").read_text(encoding="utf-8")
    )
    return residue._predict_sossi_residue_cohort(
        [experiment],
        catalog,
        starting_trace_ppm_by_experiment={experiment_id: starting_trace},
        buffered_fO2_log_by_experiment={experiment_id: -10.0},
        code_revision="b-119-disclosure",
        engine="internal-analytical",
    )[0]


def _sossi_reference(experiment_id: str):
    from types import SimpleNamespace

    return SimpleNamespace(
        experiment_id=experiment_id,
        observation_id="b119-sossi-disclosure",
        source_id="kems-012-sossi-2019",
        locator={"table": "1"},
        read_from="b119-sossi-disclosure",
        identity=SimpleNamespace(species=SimpleNamespace(formula="Mn")),
    )


def _exercise_sossi_row(_tmp_path, monkeypatch) -> None:
    """Warm the scorer cache in run A and read it through the production scorer."""

    from simulator.battery.enums import Engine, ExecutionState
    from simulator.battery.score import _SOSSI_SOURCE_ID, _sossi_residue_prediction

    row = _one_internal_analytical_sossi_row(monkeypatch)
    assert "run_refusal" not in row.provenance
    raw = row.provenance["law_disclosures"]
    assert isinstance(raw, list) and raw
    cache = {
        (_SOSSI_SOURCE_ID, Engine.INTERNAL_ANALYTICAL): {row.experiment_id: row}
    }
    reference = _sossi_reference(row.experiment_id)
    with disclosure_scope(inherit=False) as run_b:
        prediction = _sossi_residue_prediction(
            None, reference, Engine.INTERNAL_ANALYTICAL, cache
        )
        assert prediction.execution.state is ExecutionState.PRODUCED
        assert float(prediction.value) > 0.0
        _assert_replayed_fe(run_b.snapshot())
        assert prediction.refusal_detail["law_disclosures"]
    # A cached row with no disclosure field is unknown. The scorer must not
    # publish it. Recompute refuses immediately: this context has no cohort.
    from types import SimpleNamespace

    stripped = replace(
        row,
        provenance={
            key: value
            for key, value in row.provenance.items()
            if key != "law_disclosures"
        },
    )
    unknown_cache = {
        (_SOSSI_SOURCE_ID, Engine.INTERNAL_ANALYTICAL): {
            row.experiment_id: stripped
        }
    }
    with disclosure_scope(inherit=False) as run_unknown:
        refused = _sossi_residue_prediction(
            SimpleNamespace(observations={}),
            reference,
            Engine.INTERNAL_ANALYTICAL,
            unknown_cache,
        )
        assert refused.execution.state is not ExecutionState.PRODUCED
        assert run_unknown.snapshot() == ()


def _diagnostic_fe_rows(snapshot) -> list[dict]:
    return [
        item
        for item in snapshot
        if str(item.get("law_id") or "").startswith("Fe:")
        and item.get("provenance")
        == "diagnostic_only: backsolved from VapoRock, no coefficients fitted"
    ]


def _exercise_melt_psat(_tmp_path, monkeypatch) -> None:
    """Warm the melt-pressure cache with flagged Fe, then read it in run B."""

    from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider

    import simulator.diagnostic_helpers.extract_reproduction as extract_reproduction

    monkeypatch.setattr(extract_reproduction, "_MELT_PSAT_CACHE", {})
    vapor = _live_vapor()
    calls = {"n": 0}
    dispatch = BuiltinVaporPressureProvider.dispatch

    def counting(provider, request):
        calls["n"] += 1
        return dispatch(provider, request)

    monkeypatch.setattr(BuiltinVaporPressureProvider, "dispatch", counting)
    def _assert_runtime_fe(runtime: dict) -> None:
        published = _diagnostic_fe_rows(runtime["law_disclosures"])
        assert published
        assert {item["law_id"] for item in published} == {
            item["law_id"] for item in fe_rows
        }
        for item in published:
            _assert_fe_disclosure(
                item,
                law_id=item["law_id"],
                count=item["evaluation_count"],
            )

    with disclosure_scope(inherit=False) as run_a:
        pressure_a, reason_a, runtime_a = extract_reproduction._engine_melt_psat_pa(
            "Fe", 1750.0, 1.0e-9, vapor
        )
        assert reason_a is None
        assert isinstance(pressure_a, float) and math.isfinite(pressure_a)
        assert pressure_a > 0.0
        assert calls["n"] == 1
        stored = next(iter(extract_reproduction._MELT_PSAT_CACHE.values()))
        fe_rows = _diagnostic_fe_rows(stored["law_disclosures"])
        assert fe_rows
        for item in fe_rows:
            _assert_fe_disclosure(
                item,
                law_id=item["law_id"],
                count=item["evaluation_count"],
            )
        _assert_runtime_fe(runtime_a)
        run_a_fe = _diagnostic_fe_rows(run_a.snapshot())
        assert {item["law_id"] for item in run_a_fe} == {
            item["law_id"] for item in fe_rows
        }
    with disclosure_scope(inherit=False) as run_b:
        pressure_b, reason_b, runtime_b = extract_reproduction._engine_melt_psat_pa(
            "Fe", 1750.0, 1.0e-9, vapor
        )
        assert reason_b is None
        assert pressure_b == pressure_a
        assert calls["n"] == 1
        _assert_runtime_fe(runtime_b)
        replayed = _diagnostic_fe_rows(run_b.snapshot())
        assert {item["law_id"]: item["evaluation_count"] for item in replayed} == {
            item["law_id"]: item["evaluation_count"] for item in fe_rows
        }
        for item in replayed:
            _assert_fe_disclosure(
                item,
                law_id=item["law_id"],
                count=item["evaluation_count"],
            )
    stored = next(iter(extract_reproduction._MELT_PSAT_CACHE.values()))
    stored.pop("law_disclosures")
    with disclosure_scope(inherit=False) as run_c:
        pressure_c, reason_c, runtime_c = extract_reproduction._engine_melt_psat_pa(
            "Fe", 1750.0, 1.0e-9, vapor
        )
        assert reason_c is None
        assert pressure_c == pressure_a
        assert calls["n"] == 2
        assert _diagnostic_fe_rows(runtime_c["law_disclosures"])
        assert _diagnostic_fe_rows(run_c.snapshot())


def test_standalone_melt_and_rank_publish_fe_without_a_run_scope(monkeypatch) -> None:
    """The reproduction path has no session. The returned runtime is the disclosure.

    An outer scope that is then ignored would hide this. The cold call, the
    warm cache hit, and the ranking metric each publish the Fe law on their
    own runtime. A later run replays the stored row.
    """

    from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider

    import simulator.diagnostic_helpers.extract_reproduction as extract_reproduction

    monkeypatch.setattr(extract_reproduction, "_MELT_PSAT_CACHE", {})
    vapor = _live_vapor()
    calls = {"n": 0}
    dispatch = BuiltinVaporPressureProvider.dispatch

    def counting(provider, request):
        calls["n"] += 1
        return dispatch(provider, request)

    monkeypatch.setattr(BuiltinVaporPressureProvider, "dispatch", counting)
    pressure, reason, runtime = extract_reproduction._engine_melt_psat_pa(
        "Fe", 1750.0, 1.0e-9, vapor
    )
    assert reason is None
    assert isinstance(pressure, float) and pressure > 0.0
    assert calls["n"] == 1
    assert active_snapshot() == ()
    cold = _diagnostic_fe_rows(runtime["law_disclosures"])
    assert cold
    for item in cold:
        _assert_fe_disclosure(
            item,
            law_id=item["law_id"],
            count=item["evaluation_count"],
        )

    again, again_reason, again_runtime = extract_reproduction._engine_melt_psat_pa(
        "Fe", 1750.0, 1.0e-9, vapor
    )
    assert again_reason is None
    assert again == pressure
    assert calls["n"] == 1
    assert active_snapshot() == ()
    warm = _diagnostic_fe_rows(again_runtime["law_disclosures"])
    assert {item["law_id"]: item["evaluation_count"] for item in warm} == {
        item["law_id"]: item["evaluation_count"] for item in cold
    }

    ranked, rank_refusal, rank_runtime = extract_reproduction._engine_species_rank_metric(
        "Fe", 1750.0, 1.0e-9, vapor, None
    )
    assert rank_refusal is None
    assert isinstance(ranked, float) and ranked > 0.0
    assert calls["n"] == 1
    ranked_fe = _diagnostic_fe_rows(rank_runtime["law_disclosures"])
    assert {item["law_id"] for item in ranked_fe} == {item["law_id"] for item in cold}

    with disclosure_scope(inherit=False) as run:
        replayed, replay_reason, replay_runtime = extract_reproduction._engine_melt_psat_pa(
            "Fe", 1750.0, 1.0e-9, vapor
        )
        assert replay_reason is None
        assert replayed == pressure
        assert calls["n"] == 1
        assert _diagnostic_fe_rows(replay_runtime["law_disclosures"])
        snapshot = _diagnostic_fe_rows(run.snapshot())
        assert {item["law_id"]: item["evaluation_count"] for item in snapshot} == {
            item["law_id"]: item["evaluation_count"] for item in cold
        }


def _hashimoto_score_context():
    from decimal import Decimal

    from simulator.battery.enums import EvidenceClass, Phase, Quantity
    from simulator.battery.records import Species, State
    from simulator.battery.score import ScoreContext
    from tests.battery import factories as factories

    identity = replace(
        factories.psat_identity("FeO"),
        quantity=Quantity.RESIDUE_COMPONENT_COMPOSITION,
        species=Species("FeO", Phase.L),
        subtype=State.of("oxide_wt_percent"),
    )
    source = "kems-015-hashimoto-1983"
    experiments = {}
    observations = {}
    for index in range(24):
        experiment_id = f"h-{index:02d}"
        experiments[experiment_id] = {"experiment_id": experiment_id}
        observation_id = f"{source}::{experiment_id}"
        observations[observation_id] = factories.observation(
            observation_id,
            experiment_id,
            identity,
            Decimal("10"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id=source,
        )
    return ScoreContext({}, experiments, observations), observations[f"{source}::h-00"]


def _hashimoto_cohort_rows(experiments, code_revision, engine):
    from types import SimpleNamespace

    experiment_id = str(experiments[0]["experiment_id"])
    values = {"FeO": 11.0, "MgO": 20.0}
    band = {name: (value - 1.0, value + 1.0) for name, value in values.items()}
    geometry = {"sphere_shrinking": dict(values)}
    base = {
        "code_revision": code_revision,
        "engine": engine,
        "geometry_refusal_by_geometry": {},
    }

    def row(arm: str, oxides: dict[str, float]):
        return SimpleNamespace(
            experiment_id=experiment_id,
            alpha_arm=arm,
            primary_geometry_policy_id="sphere_shrinking",
            primary_oxide_wt_pct=dict(oxides),
            geometry_oxide_wt_pct=geometry,
            sensitivity_band_wt_pct=band,
            provenance=dict(base),
        )

    return (
        row("alpha_common_unity_sensitivity", values),
        row("alpha_runtime_catalog", {**values, "FeO": 12.0}),
    )


def _exercise_hashimoto_cohort(_tmp_path, monkeypatch) -> None:
    """Warm the Hashimoto cohort through ``_hashimoto_residue_prediction``."""

    import simulator.battery.residue as residue
    from simulator.battery.enums import Engine, ExecutionState
    from simulator.battery.score import _hashimoto_residue_prediction

    calls = {"n": 0}

    def fake_cohort(experiments, _catalog, *, preform_dimensions_mm, code_revision, engine):
        del preform_dimensions_mm
        calls["n"] += 1
        _fe_pressure("FeO_association_gas")
        return _hashimoto_cohort_rows(experiments, code_revision, engine)

    monkeypatch.setattr(residue, "_predict_hashimoto_residue_cohort", fake_cohort)
    context, reference = _hashimoto_score_context()
    engine = Engine.INTERNAL_ANALYTICAL
    cache: dict = {}
    with disclosure_scope(inherit=False) as run_a:
        prediction = _hashimoto_residue_prediction(context, reference, engine, cache)
        assert prediction.execution.state is ExecutionState.PRODUCED, prediction.refusal_detail
        assert calls["n"] == 1
        assert prediction.refusal_detail["law_disclosures"]
        _assert_replayed_fe(run_a.snapshot())
    with disclosure_scope(inherit=False) as run_b:
        again = _hashimoto_residue_prediction(context, reference, engine, cache)
        assert again.execution.state is ExecutionState.PRODUCED, again.refusal_detail
        assert calls["n"] == 1
        assert again.refusal_detail["law_disclosures"]
        _assert_replayed_fe(run_b.snapshot())
    for row in next(iter(cache.values())).values():
        row.provenance.pop("law_disclosures")
    with disclosure_scope(inherit=False) as run_c:
        recomputed = _hashimoto_residue_prediction(context, reference, engine, cache)
        assert recomputed.execution.state is ExecutionState.PRODUCED, recomputed.refusal_detail
        assert calls["n"] == 2
        _assert_replayed_fe(run_c.snapshot())


def _knudsen_extent_inputs():
    from decimal import Decimal

    from simulator.battery.enums import (
        AdmissionStatus,
        AmountBasis,
        EvidenceClass,
        ExperimentKind,
        MethodToken,
        Phase,
        Quantity,
        UncertaintyKind,
    )
    from simulator.battery.identity import Identity
    from simulator.battery.records import (
        Admission,
        Composition,
        Evidence,
        Experiment,
        FlowRegime,
        Located,
        Locator,
        Observation,
        PressureEnvironment,
        Sample,
        Species,
        State,
        ThermalSchedule,
        Uncertainty,
        Value,
    )
    from simulator.battery.score import ScoreContext

    composition = Composition(
        "synthetic",
        (("FeO", Decimal("0.5")), ("MgO", Decimal("0.5"))),
        AmountBasis.MOLE_FRACTION,
    )
    locator = Locator(table="2", published_page=510)
    reference = Observation(
        "residue",
        "experiment",
        Identity(
            Quantity.RESIDUE_COMPONENT_COMPOSITION,
            Species("FeO", Phase.L),
            subtype=State.of("oxide_wt_percent"),
            temperature_K=State.of(Decimal(2100)),
            composition=State.of(composition),
        ),
        Value.point_of(99),
        Uncertainty(UncertaintyKind.NONE),
        Evidence(State.of(EvidenceClass.MEASURED_DIRECT)),
        Admission(AdmissionStatus.ADMITTED, "synthetic anchor"),
        (),
        source_id="synthetic",
        locator=locator,
    )
    rows = tuple(
        replace(
            reference,
            observation_id=f"mass-{temperature}",
            identity=replace(
                reference.identity,
                quantity=Quantity.MASS_LOSS_FRACTION,
                temperature_K=State.of(Decimal(temperature)),
            ),
            value=Value.point_of(loss),
        )
        for temperature, loss in ((2000, "0.1"), (2100, "0.2"))
    )
    experiment = Experiment(
        "experiment",
        ExperimentKind.LITERATURE,
        State.of(MethodToken.KNUDSEN_EFFUSION),
        Sample(initial_composition=Located(State.of(composition), locator)),
        {
            "temperature_K": Located(
                State.unknown("point temperatures are printed per row"), locator
            )
        },
        PressureEnvironment(
            Located(State.unknown("not published")),
            Located(State.unknown("no sweep")),
            FlowRegime(State.unknown("not published")),
        ),
        thermal_schedule=ThermalSchedule(
            method=Located(State.of("70 K steps, 15 minutes each"), locator)
        ),
    )
    observations = {reference.observation_id: reference}
    observations.update({row.observation_id: row for row in rows})
    context = ScoreContext({}, {experiment.experiment_id: experiment}, observations)
    return context, reference


def _exercise_knudsen_result(_tmp_path, monkeypatch) -> None:
    """Warm the Knudsen result through ``_knudsen_residue_prediction``."""

    import sys
    from types import SimpleNamespace

    import simulator.battery.residue as residue
    from simulator.battery.enums import Engine, ExecutionState
    from simulator.battery.score import _knudsen_residue_prediction
    from simulator.diagnostic_helpers import binary_pot_battery as battery

    calls = {"predict": 0, "fe": 0}
    real = residue.predict_knudsen_residue_composition

    def counting(*args, **kwargs):
        calls["predict"] += 1
        return real(*args, **kwargs)

    def pressure(_inventory, logp):
        if calls["fe"] < calls["predict"]:
            _fe_pressure("FeO_association_gas")
            calls["fe"] += 1
        return {"Fe": 1e-8 * 10 ** (-logp / 2), "O2": 1e5 * 10**logp}

    monkeypatch.setattr(residue, "predict_knudsen_residue_composition", counting)
    monkeypatch.setattr(residue, "_make_residue_pressure_model", lambda *_a, **_k: pressure)
    monkeypatch.setitem(
        sys.modules, "openimcc", SimpleNamespace(load_gas_datapack=lambda: object())
    )
    monkeypatch.setattr(
        battery,
        "_openimcc_gas_channels_and_omission_notices",
        lambda *_args: ([("Fe", ("FeO", 1, 0.5)), ("O2", (None, 1, -1))], ()),
    )
    context, reference = _knudsen_extent_inputs()
    engine = Engine.INTERNAL_ANALYTICAL
    cache: dict = {}
    with disclosure_scope(inherit=False) as run_a:
        prediction = _knudsen_residue_prediction(context, reference, engine, cache)
        assert prediction.execution.state is ExecutionState.PRODUCED, prediction.refusal_detail
        assert calls["predict"] == 1
        assert prediction.refusal_detail["law_disclosures"]
        _assert_replayed_fe(run_a.snapshot())
    with disclosure_scope(inherit=False) as run_b:
        again = _knudsen_residue_prediction(context, reference, engine, cache)
        assert again.execution.state is ExecutionState.PRODUCED, again.refusal_detail
        assert calls["predict"] == 1
        assert again.refusal_detail["law_disclosures"]
        _assert_replayed_fe(run_b.snapshot())
    next(iter(cache.values())).pop("law_disclosures")
    with disclosure_scope(inherit=False) as run_c:
        recomputed = _knudsen_residue_prediction(context, reference, engine, cache)
        assert recomputed.execution.state is ExecutionState.PRODUCED, recomputed.refusal_detail
        assert calls["predict"] == 2
        _assert_replayed_fe(run_c.snapshot())


def _exercise_residue_prediction_cache(tmp_path, monkeypatch) -> None:
    """Every production reader of ``residue_prediction_cache`` replays its disclosure."""

    _exercise_sossi_row(tmp_path, monkeypatch)
    _exercise_hashimoto_cohort(tmp_path, monkeypatch)
    _exercise_knudsen_result(tmp_path, monkeypatch)


# 2300 K is above the shared [1400.0, 2273.15] K band. The provider's SiO
# pressure at that point is the Antoine continuation
# log10(P/Pa) = A - B/(T+C), which does not call catalog evaluate().
# The published law is SiO:antoine. A compiled neighbour such as
# SiO2_gas:nasa_cea_9 does not disclose that pressure.
_SIO_OOB_K = 2300.0
_SIO_PUBLISHED_LAW_ID = "SiO:antoine"
_SIO_PUBLISHED_PROVENANCE = "oxide_vapor_pressure_out_of_validated_range"
_SIO_CERTIFIED_BAND_K = [1400.0, 2273.15]


def _published_sio_rows(rows) -> list[dict]:
    return [
        item
        for item in rows
        if item.get("law_id") == _SIO_PUBLISHED_LAW_ID
        and item.get("authority") == "extrapolated"
    ]


def _assert_published_sio(row: dict) -> None:
    assert row["law_id"] == _SIO_PUBLISHED_LAW_ID
    assert row["authority"] == "extrapolated"
    assert row["provenance"] == _SIO_PUBLISHED_PROVENANCE
    assert row["certified_band_K"] == _SIO_CERTIFIED_BAND_K
    assert row["closer"] == ""
    assert isinstance(row["evaluation_count"], int)
    assert row["evaluation_count"] >= 1
    assert "SiO2_gas" not in row["law_id"]


def _assert_sio_extrapolation_notice(runtime: dict) -> None:
    notice = runtime["extrapolated_beyond_valid_range_K"]["SiO"]
    assert notice["authority_level"] == "extrapolated"
    assert notice["reason"] == "oxide_vapor_pressure_out_of_validated_range"
    assert tuple(notice["valid_range_K"]) == (1400.0, 2273.15)


def _sio_oob_kems_inputs() -> tuple[dict, dict]:
    """A valid KEMS program with two observations at one out-of-band temperature."""

    from simulator.diagnostic_helpers.kems import load_kems_case

    case = load_kems_case(_ROOT / "data/presets/kems/halwax_2024_mgo.yaml")
    case["case_id"] = "sio_oob_disclosure"
    case["oxide"] = {
        "formula": "SiO2",
        "identity": "synthetic silica for the disclosure probe",
        "purity_fraction": case["oxide"]["purity_fraction"],
        "purity_source": case["oxide"]["purity_source"],
    }
    program = case["temperature_program"]
    program["start_K"] = 2200.0
    program["end_K"] = 2400.0
    program["isothermal_hold"]["temperature_K"] = _SIO_OOB_K
    case["measurement_selectors"] = [
        {
            "observable_id": "sio_p",
            "observable": "partial_pressure_pa",
            "species": "SiO",
            "evidence_scope": "species-resolved-clean-oxide-kems",
        }
    ]
    point = {
        "observable_id": "sio_p",
        "species": "SiO",
        "allow_total_pressure_fallback": False,
        "coordinate": {"temperature_K": _SIO_OOB_K},
        "partial_pressure_pa": None,
        "uncertainty": {
            "temperature_K": 5.0,
            "observable_pa": None,
            "observable_status": "not_reported",
        },
        "status": "absent",
        "source_locator": {"figure": 1, "published_page": 1},
        "extraction_method": "synthetic",
        "note": "repeated observation at one out-of-band temperature",
    }
    observations = {
        "schema_version": 1,
        "sources": {
            case["source_id"]: {
                "citation": "synthetic disclosure probe",
                "doi": "10.0000/synthetic",
                "url": "https://example.invalid/sio",
                "extraction_note": "two observations at one out-of-band temperature",
            }
        },
        "cases": {
            case["case_id"]: {
                "case_id": case["case_id"],
                "source_id": case["source_id"],
                "points": [dict(point), dict(point)],
            }
        },
    }
    return case, observations


def _exercise_kems_surface_cache(_tmp_path, monkeypatch) -> None:
    """Warm the KEMS surface in run A and replay it in fresh run B.

    The program's two observations share one out-of-band SiO temperature.
    The first publication is the cold dispatch. The second is the cache hit.
    Both carry SiO:antoine, the Antoine continuation that produced the
    published SiO pressure. Run B reads the stored row and does not dispatch
    again. A row with the disclosure field removed recomputes.
    """

    from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider

    from simulator.diagnostic_helpers.kems import KEMSAdapter

    case, observations = _sio_oob_kems_inputs()
    adapter = KEMSAdapter(_live_vapor())
    calls = {"n": 0}
    dispatch = BuiltinVaporPressureProvider.dispatch

    def counting(provider, request):
        calls["n"] += 1
        return dispatch(provider, request)

    monkeypatch.setattr(BuiltinVaporPressureProvider, "dispatch", counting)

    def published_sio(run):
        found = []
        for runtime in run.runtime_rows:
            match = _published_sio_rows(runtime["law_disclosures"])
            assert match, runtime["law_disclosures"]
            for item in match:
                _assert_published_sio(item)
            found.append(match[0])
            _assert_sio_extrapolation_notice(runtime)
            assert runtime["vapor_pressures_Pa"]["SiO"] > 0.0
        return found

    with disclosure_scope(inherit=False) as run_a:
        first = adapter.evaluate(case, observations)
        assert calls["n"] == 1
        published = published_sio(first)
        assert len(published) == 2
        assert published[0]["law_id"] == published[1]["law_id"]
        stored = next(iter(adapter._surface_cache.values()))
        stored_law = _published_sio_rows(stored["law_disclosures"])
        assert stored_law
        _assert_published_sio(stored_law[0])
        run_a_law = _published_sio_rows(run_a.snapshot())
        assert run_a_law
        # The cold dispatch registers once. The repeated observation replays it.
        assert run_a_law[0]["evaluation_count"] == (
            2 * stored_law[0]["evaluation_count"]
        )

    with disclosure_scope(inherit=False) as run_b:
        second = adapter.evaluate(case, observations)
        assert calls["n"] == 1
        published_sio(second)
        replayed = _published_sio_rows(run_b.snapshot())
        assert replayed[0]["evaluation_count"] == (
            2 * stored_law[0]["evaluation_count"]
        )
        _assert_published_sio(replayed[0])
        assert (
            second.runtime_rows[0]["vapor_pressures_Pa"]
            == first.runtime_rows[0]["vapor_pressures_Pa"]
        )

    stored.pop("law_disclosures")
    with disclosure_scope(inherit=False) as run_c:
        third = adapter.evaluate(case, observations)
        assert calls["n"] == 2
        published_sio(third)
        assert _published_sio_rows(run_c.snapshot())


def test_standalone_kems_publishes_the_sio_antoine_law() -> None:
    """KEMS with no session still publishes the SiO continuation it ranked."""

    from simulator.diagnostic_helpers.kems import KEMSAdapter

    case, observations = _sio_oob_kems_inputs()
    result = KEMSAdapter(_live_vapor()).evaluate(case, observations)
    assert active_snapshot() == ()
    assert result.runtime_rows
    for runtime in result.runtime_rows:
        match = _published_sio_rows(runtime["law_disclosures"])
        assert match, runtime["law_disclosures"]
        for item in match:
            _assert_published_sio(item)
        assert runtime["vapor_pressures_Pa"]["SiO"] > 0.0


def test_every_published_extrapolation_is_disclosed() -> None:
    """Every species pressure published with an extrapolation notice is disclosed.

    The class is the provider's published set at 2300 K, not one neighbour
    law. SiO is the Antoine continuation. Deleting the publication registrar
    drops SiO and any other direct continuation while compiled neighbours
    such as SiO2_gas can still be present.
    """

    from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
    from simulator.chemistry.kernel.capabilities import ChemistryIntent
    from simulator.chemistry.kernel.dto import IntentRequest, ProviderAccountView

    case, _observations = _sio_oob_kems_inputs()
    oxide = str(case["oxide"]["formula"])
    pO2_bar = float(case["provider_inputs"]["pO2_bar"])
    exterior_pa = float(case["exterior_chamber_pressure"]["value_pa"])
    provider = BuiltinVaporPressureProvider(_live_vapor())
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {oxide: 1.0}},
            species_formula_registry={},
        ),
        temperature_C=_SIO_OOB_K - 273.15,
        pressure_bar=max(exterior_pa / 100000.0, 1e-30),
        fO2_log=math.log10(pO2_bar),
        control_inputs={"pO2_bar": pO2_bar, "pO2_admission": "observation"},
    )
    with disclosure_scope(inherit=False) as registry:
        result = provider.dispatch(request)
        snapshot = registry.snapshot()
    assert result.status == "ok"
    diagnostic = result.diagnostic or {}
    pressures = diagnostic.get("vapor_pressures_Pa") or {}
    extrapolations = diagnostic.get("extrapolated_beyond_valid_range_K") or {}
    assert "SiO" in pressures
    assert pressures["SiO"] > 0.0
    assert "SiO" in extrapolations
    published = [
        species
        for species, notice in extrapolations.items()
        if species in pressures and isinstance(notice, dict)
    ]
    assert "SiO" in published
    problems: list[str] = []
    for species in published:
        notice = extrapolations[species]
        rows = [
            item
            for item in snapshot
            if str(item.get("law_id") or "").split(":", 1)[0] == species
        ]
        if not rows:
            problems.append(f"{species}: no disclosure")
            continue
        row = rows[0]
        if species == "SiO":
            chosen = next(
                (item for item in rows if item.get("law_id") == _SIO_PUBLISHED_LAW_ID),
                None,
            )
            if chosen is None:
                problems.append(
                    f"SiO laws {[item.get('law_id') for item in rows]}"
                )
                continue
            row = chosen
        if row.get("authority") != "extrapolated":
            problems.append(f"{species}: authority {row.get('authority')}")
        reason = str(notice.get("reason") or "")
        provenance = str(row.get("provenance") or "")
        # Direct Antoine continuations quote the published notice. Compiled
        # laws quote evaluate()'s out-of-range status instead.
        if str(row.get("law_id") or "").endswith(":antoine"):
            if reason and reason not in provenance:
                problems.append(
                    f"{species}: provenance {provenance!r} lacks {reason!r}"
                )
        elif not provenance:
            problems.append(f"{species}: empty provenance")
        band = notice.get("valid_range_K")
        if (
            isinstance(band, (list, tuple))
            and len(band) == 2
            and row.get("certified_band_K") != [float(band[0]), float(band[1])]
        ):
            problems.append(f"{species}: band {row.get('certified_band_K')}")
    assert problems == []
    sio = next(item for item in snapshot if item["law_id"] == _SIO_PUBLISHED_LAW_ID)
    _assert_published_sio(sio)


_DISCLOSURE_EXERCISERS = {
    "store:simulator/reduced_real_determinism.py:_entries": _exercise_pt0_memory,
    "class:simulator/reduced_real_determinism.py:PT1PersistentEquilibriumStore": _exercise_pt1,
    "class:simulator/optimize/results_store.py:ResultStore": _exercise_result_store,
    "fn:simulator/optimize/study.py:_scored_result_from_journal_payload": _exercise_journal,
    "store:simulator/core.py:_freeze_gate_liquid_fraction_cache": _exercise_core_cache,
    "store:simulator/core.py:_freeze_gate_held_curve_record": _exercise_held,
    "store:simulator/core.py:_freeze_gate_liquid_fraction_curve_memo": _exercise_core_memo,
    "store:simulator/evaporation.py:_freeze_gate_liquid_fraction_cache": _exercise_local,
    "store:simulator/evaporation.py:_freeze_gate_held_curve_record": _exercise_held,
    "attr:simulator/evaporation.py:_freeze_gate_shared_curve_cache": _exercise_shared,
    "store:simulator/battery/residue.py:pressure_cache": _exercise_pressure_cache,
    "store:simulator/battery/residue.py:pressure_state_cache": _exercise_pressure_state_cache,
    "store:simulator/battery/score.py:residue_prediction_cache": _exercise_residue_prediction_cache,
    "store:simulator/diagnostic_helpers/extract_reproduction.py:_MELT_PSAT_CACHE": (
        _exercise_melt_psat
    ),
    "store:simulator/diagnostic_helpers/kems.py:_surface_cache": (
        _exercise_kems_surface_cache
    ),
}


@pytest.mark.parametrize("site_id", sorted(_DISCLOSURE_EXERCISERS))
def test_disclosure_cache_replays_into_a_fresh_run(
    site_id: str, tmp_path: Path, monkeypatch
) -> None:
    """Warm a disclosure-bearing cache in run A and read it in fresh run B."""

    _DISCLOSURE_EXERCISERS[site_id](tmp_path, monkeypatch)
