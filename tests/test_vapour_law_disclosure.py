"""Run-scoped disclosure of an admitted diagnostic or extrapolated vapour law.

Invariant: every published, priced, or ranked quantity discloses every admitted
diagnostic or extrapolated vapour law its own computation executed. Registration
is the one ``CompiledPressureEvaluator.evaluate`` call. A cache hit does not
execute the law; it re-registers the disclosures stored with that computation,
and evaluation counts add. A stored row with no disclosure field is unknown and
is not a hit. An empty disclosure is a measured zero only when the computation
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
    active_snapshot,
    computation_disclosure,
    disclosure_scope,
    publish_stored_disclosures,
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
    assert "with computation_disclosure()" in head
    assert head.index("replay_enabled") < head.index("with computation_disclosure()")
    assert head.index("cached_equilibrium") < head.index("with computation_disclosure()")
    assert "capture_equilibrium" not in head

    evaporation = (_ROOT / "simulator/evaporation.py").read_text(encoding="utf-8")
    body = evaporation.split("    def _freeze_gate_curve_record(", 1)[1].split(
        "    def _freeze_gate_liquidus_fO2_log(", 1
    )[0]
    assert body.index("recorded_law_disclosures(cache)") < body.index(
        "with computation_disclosure()"
    )
    assert body.index("recorded_law_disclosures(process_cached)") < body.index(
        "with computation_disclosure()"
    )
    assert body.count("publish_stored_disclosures(") == 2
    assert body.index("publish_stored_disclosures(cache)") < body.index(
        "with computation_disclosure()"
    )
    assert body.index("publish_stored_disclosures(process_cached)") < body.index(
        "with computation_disclosure()"
    )
    assert body.index("with computation_disclosure()") < body.rindex(
        "store.capture_gate_curve("
    )

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
