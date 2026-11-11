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
committed step poisons the session. A cache or memo hit with no stored
disclosure field is unknown and is a miss. Registration is the one
``CompiledPressureEvaluator.evaluate`` call. A hit does not execute the law; it
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
import hashlib
import inspect
import math
import sqlite3
import subprocess
import threading
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
    disclosure_scope,
    open_computation,
    publish_stored_disclosures,
    reregister_cached_disclosures,
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
    assert len(calls) == 1
    assert calls[0].startswith("simulator/vapour_rail/catalog.py:")
    assert calls[0].endswith("register_executed_law(self, evaluation)")
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


def test_cache_publication_paths_use_the_disclosure_chokepoint() -> None:
    reduced = (_ROOT / "simulator/reduced_real_determinism.py").read_text(encoding="utf-8")
    assert reduced.count('return copy.deepcopy(entry["payload"])') == 1
    assert 'return copy.deepcopy(attempt["payload"])' not in reduced
    accept = reduced.split("def _accept_cached_hit", 1)[1].split("\n    def ", 1)[0]
    assert 'return copy.deepcopy(entry["payload"])' in accept
    assert "def _active_law_disclosures" not in reduced

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
    assert body.index(bind) < body.index("replay_gate_curve(")
    assert body.index(bind) < body.index("publish_stored_disclosures(cache, owner=self)")
    assert body.index(bind) < body.index(
        "publish_stored_disclosures(process_cached, owner=self)"
    )
    assert body.index("recorded_law_disclosures(cache)") < body.index(live_open)
    assert body.index("recorded_law_disclosures(process_cached)") < body.index(
        live_open
    )
    assert body.count("publish_stored_disclosures(") == 2
    assert body.index("stored_disclosures_known(cache)") < body.index(
        "publish_stored_disclosures(cache, owner=self)"
    )
    assert body.index("stored_disclosures_known(process_cached)") < body.index(
        "publish_stored_disclosures(process_cached, owner=self)"
    )
    assert body.index("publish_stored_disclosures(cache, owner=self)") < body.index(
        live_open
    )
    assert body.index(
        "publish_stored_disclosures(process_cached, owner=self)"
    ) < body.index(live_open)
    assert body.index(live_open) < body.rindex("store.capture_gate_curve(")
    assert "with open_computation()" not in body
    assert "with computation_disclosure()" not in body
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
    assert store.entries[_key_hash(fe_a_key)]["law_disclosures"][0]["evaluation_count"] == 1
    assert store.entries[_key_hash(clean_key)]["law_disclosures"] == ()
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
            capture.entries[_key_hash(fe_a_key)]["law_disclosures"] = None
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


_STANDALONE_HIT_CLASSES = (
    "equilibrium_replay",
    "equilibrium_write_through",
    "gate_replay",
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
        reregister_cached_disclosures(self.disclosures)
        return {"status": "replayed"}

    def cached_equilibrium(self, _sim):
        self.calls.append("cached_equilibrium")
        reregister_cached_disclosures(self.disclosures)
        return {"status": "cached"}

    def replay_gate_curve(self, _sim, **_kwargs):
        self.calls.append("replay_gate_curve")
        reregister_cached_disclosures(self.disclosures)
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
    if kind in {"gate_replay", "local_memo", "shared_memo"}:
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
    if kind in {"local_memo", "shared_memo"}:
        assert calls == []
        assert payload["curve"]["tag"] == "cached"
    elif kind == "gate_replay":
        assert calls == ["replay_gate_curve"]
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
    if kind in {"local_memo", "shared_memo"}:
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
