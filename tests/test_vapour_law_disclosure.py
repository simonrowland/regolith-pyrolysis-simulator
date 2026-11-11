"""Run-scoped disclosure of an admitted diagnostic vapour law.

Invariant: every execution of an admitted diagnostic or extrapolated vapour
law is registered exactly once, at ``CompiledPressureEvaluator.evaluate``,
into a run-scoped disclosure that travels with every published, priced, or
ranked result. A cache hit does not execute the law; it re-registers the
disclosure stored as metadata on the cached value. Typed-carrier notices are
a list: distinct notices are kept, exact duplicates are dropped. Bare-float
helpers do not carry notices.

The Fe pseudo law is ``log10(P/Pa) = A - B / (T + C)`` with
A=9.408547, B=23312.312336, C=1000. At the 1750 K anchor, T+C=2750 and
``10 ** (9.408547 - 23312.312336/2750) = 8.537731922030234`` Pa. The anchor
rail is 0.6585350200427053 Pa (gap 1.1127636390496998 dex, 1.113 dex at
3 d.p.) and the VapoRock comparator is 0.3004593572618527 Pa (gap
1.4535567803078235 dex, 1.454 dex at 3 d.p.).
"""

from __future__ import annotations

import csv
import inspect
import math
import subprocess
import threading
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from simulator.chemistry.ellingham_graph import effective_equilibrium_pressure_Pa
from simulator.optimize.evalspec import cache_key
from simulator.optimize.objective import ObjectiveDefinition
from simulator.optimize.results_store import ResultStore
from simulator.optimize.study import (
    StudyRecord,
    _flagged_law_leaderboard_row,
    _rank_key,
    _scored_result_from_journal_payload,
    _scored_result_journal_payload,
    _write_leaderboard,
)
from simulator.recipe import RecipePatch, RecipeSchema
from simulator.reduced_real_determinism import PT0DeterminismStore
from simulator.vapour_rail.catalog import CompiledPressureEvaluator
from simulator.vapour_rail.run_disclosure import (
    active_snapshot,
    disclosure_scope,
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
    assert calls[0].endswith("register_executed_law(self)")
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
        _fe_pressure("FeO_association_gas")
        cold_snapshot = cold.snapshot()
        memory._store("freeze_gate_curve", key, payload)
    with disclosure_scope(inherit=False) as warm:
        assert warm.snapshot() == ()
        memory._lookup("freeze_gate_curve", key)
        assert warm.snapshot() == cold_snapshot

    db_path = tmp_path / "pt1-disclosure.sqlite"
    persistent = PT0DeterminismStore("capture", db_path=db_path)
    with disclosure_scope(inherit=False) as cold:
        _fe_pressure("FeO_association_gas")
        cold_snapshot = cold.snapshot()
        persistent._store("freeze_gate_curve", key, payload)
    replay = PT0DeterminismStore("replay", db_path=db_path)
    with disclosure_scope(inherit=False) as warm:
        replay._lookup("freeze_gate_curve", key)
        assert warm.snapshot() == cold_snapshot


def test_optimizer_cache_and_journal_reregister_metadata(tmp_path: Path) -> None:
    with disclosure_scope(inherit=False) as cold:
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
    assert "law_disclosures" not in empty_payload
    flagged_payload = _scored_result_journal_payload(stored)
    assert flagged_payload["law_disclosures"] == [dict(item) for item in cold_snapshot]
    with disclosure_scope(inherit=False) as replayed:
        restored = _scored_result_from_journal_payload(flagged_payload)
        assert restored.law_disclosures == cold_snapshot
        assert replayed.snapshot() == cold_snapshot
    with disclosure_scope(inherit=False) as untouched:
        _scored_result_from_journal_payload(empty_payload)
        assert untouched.snapshot() == ()


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
