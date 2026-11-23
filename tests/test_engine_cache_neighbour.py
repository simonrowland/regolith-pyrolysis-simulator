"""Coarse-cell neighbour hits are an optimizer pass, not a key change.

The exact key stays engine + chemistry version + request. A coarse
ControlQuantization preset may return any stored result in the same cell,
flagged neighbour_hit with that preset. A fine pass and a scoring call,
which never enters the optimizer scope, stay on the exact key.
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

from simulator import engine_cache
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
from simulator.reduced_real_determinism import (
    ControlQuantization,
    _quantize,
    _sigfig,
    coarsen_engine_request,
    optimizer_engine_neighbour,
    optimizer_neighbour_preset,
)

pytestmark = pytest.mark.engine_cache_off


def _use_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_MODE", raising=False)
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()
    return path


def _request(temperature_K: float, *, model: str = "a") -> dict[str, object]:
    return {
        "temperature_K": temperature_K,
        "pressure_bar": 1.0,
        "fO2_log": -11.2,
        "po2": 0.021,
        "composition_wt_pct": {"SiO2": 50.14, "FeO": 10.0},
        "engine_settings": {"model": model},
    }


def _call(request: dict[str, object], answer: dict[str, object]) -> dict[str, object]:
    return engine_cache.cached_engine_call("openimcc", request, lambda: dict(answer))


def test_coarse_cell_snaps_with_the_owner_quantizer() -> None:
    preset = ControlQuantization.from_name("coarse")
    request = {
        "temperature_K": 1700.2,
        "pressure_bar": 1.0004,
        "fO2_log": -11.234,
        "po2": 0.02146,
        "temperature_C": 1427.2,
        "composition_wt_pct": {"SiO2": 50.14},
        "engine_settings": {"model": "a", "temperature_K": 1700.2},
    }
    cell = coarsen_engine_request(request, "coarse")
    assert cell["temperature_K"] == _quantize(
        1700.2, preset.t_k_quantum, preset.t_k_decimals,
    )
    snapped_c = _quantize(
        1427.2 + CELSIUS_TO_KELVIN_OFFSET,
        preset.t_k_quantum,
        preset.t_k_decimals,
    )
    assert snapped_c is not None
    assert cell["temperature_C"] == snapped_c - CELSIUS_TO_KELVIN_OFFSET
    assert cell["pressure_bar"] == _quantize(
        1.0004, preset.pressure_bar_quantum, preset.pressure_bar_decimals,
    )
    assert cell["fO2_log"] == _quantize(
        -11.234, preset.log_fo2_quantum, preset.log_fo2_decimals,
    )
    assert cell["po2"] == _sigfig(0.02146, preset.composition_sig_figs)
    assert cell["composition_wt_pct"]["SiO2"] == _sigfig(
        50.14, preset.composition_sig_figs,
    )
    # A model knob is not a nearby control, including a temperature copied into it.
    assert cell["engine_settings"] == request["engine_settings"]
    assert cell["temperature_K"] != request["temperature_K"]
    assert cell["pressure_bar"] != request["pressure_bar"]
    assert cell["po2"] != request["po2"]


def test_preset_name_is_coarse_only() -> None:
    assert optimizer_neighbour_preset(None) is None
    assert optimizer_neighbour_preset({}) is None
    assert optimizer_neighbour_preset({"control_quantization": "fine"}) is None
    assert optimizer_neighbour_preset({"control_quantization": "XX-COARSE"}) == "xx_coarse"
    assert optimizer_neighbour_preset({"control_quantization": "coarse"}) == "coarse"
    assert optimizer_neighbour_preset({"control_quantization": "medium"}) == "medium"
    coarse = ControlQuantization.from_name("coarse")
    assert optimizer_neighbour_preset({
        "control_quantization": {
            "t_k_quantum": coarse.t_k_quantum,
            "pressure_bar_quantum": coarse.pressure_bar_quantum,
            "log_fo2_quantum": coarse.log_fo2_quantum,
            "composition_sig_figs": coarse.composition_sig_figs,
        },
    }) == "coarse"
    assert optimizer_neighbour_preset({
        "control_quantization": {
            "t_k_quantum": coarse.t_k_quantum * 2,
            "pressure_bar_quantum": coarse.pressure_bar_quantum,
            "log_fo2_quantum": coarse.log_fo2_quantum,
            "composition_sig_figs": coarse.composition_sig_figs,
        },
    }) is None


def test_coarse_pass_hits_a_neighbour_and_leaves_the_exact_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _use_cache(monkeypatch, tmp_path)
    base = _request(1700.0)
    nearby = _request(1700.0 + ControlQuantization.from_name("coarse").t_k_quantum * 0.2)
    assert engine_cache.engine_cache_key("openimcc", 1, base) != engine_cache.engine_cache_key(
        "openimcc", 1, nearby,
    )
    calls = {"nearby": 0}

    def nearby_solve() -> dict[str, object]:
        calls["nearby"] += 1
        return {"answer": "nearby"}

    with optimizer_engine_neighbour({"control_quantization": "coarse"}):
        assert _call(base, {"answer": "base"}) == {"answer": "base"}
        hit = engine_cache.cached_engine_call("openimcc", nearby, nearby_solve)
    assert calls["nearby"] == 0
    assert hit["answer"] == "base"
    assert "neighbour_hit" not in hit
    assert hit.neighbour_hit == "coarse"
    engine_cache._MEMORY_CACHE.clear()
    assert engine_cache.cached_engine_call(
        "openimcc", base, lambda: pytest.fail("exact row was not stored"),
    ) == {"answer": "base"}
    # The neighbour is not written as this request's exact row.
    own = engine_cache.cached_engine_call("openimcc", nearby, nearby_solve)
    assert own == {"answer": "nearby"}
    assert "neighbour_hit" not in own
    assert calls["nearby"] == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM engine_neighbour_cells"
        ).fetchone()[0] >= 1
        stored = connection.execute(
            "SELECT value FROM engine_outputs"
        ).fetchall()
    assert all(b"neighbour_hit" not in bytes(row[0]) for row in stored)


def test_fine_pass_never_returns_a_neighbour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_cache(monkeypatch, tmp_path)
    base = _request(1700.0)
    nearby = _request(1700.2)
    with optimizer_engine_neighbour({"control_quantization": "coarse"}):
        _call(base, {"answer": "base"})
    with optimizer_engine_neighbour({"control_quantization": "fine"}):
        result = _call(nearby, {"answer": "fine"})
    assert result == {"answer": "fine"}
    assert "neighbour_hit" not in result


def test_scoring_call_never_returns_a_neighbour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_cache(monkeypatch, tmp_path)
    base = _request(1700.0)
    nearby = _request(1700.2)
    with optimizer_engine_neighbour({"control_quantization": "coarse"}):
        _call(base, {"answer": "base"})
    # Battery scoring calls the cache with no optimizer scope.
    result = _call(nearby, {"answer": "scored"})
    assert result == {"answer": "scored"}
    assert "neighbour_hit" not in result


def test_fine_store_does_not_write_a_neighbour_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _use_cache(monkeypatch, tmp_path)
    base = _request(1700.0)
    nearby = _request(1700.2)
    with optimizer_engine_neighbour({"control_quantization": "fine"}):
        _call(base, {"answer": "fine-stored"})
        fine_nearby = _call(nearby, {"answer": "fine-own"})
    assert fine_nearby == {"answer": "fine-own"}
    with sqlite3.connect(path) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "engine_neighbour_cells" not in names
    further = _request(1700.4)
    with optimizer_engine_neighbour({"control_quantization": "coarse"}):
        solved = _call(further, {"answer": "coarse-solved"})
    assert solved == {"answer": "coarse-solved"}
    assert "neighbour_hit" not in solved


def test_coarser_preset_reaches_farther_than_coarse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    coarse = ControlQuantization.from_name("coarse")
    wider = ControlQuantization.from_name("xx_coarse")
    assert _quantize(1700.0, coarse.t_k_quantum, coarse.t_k_decimals) != _quantize(
        1704.0, coarse.t_k_quantum, coarse.t_k_decimals,
    )
    assert _quantize(1700.0, wider.t_k_quantum, wider.t_k_decimals) == _quantize(
        1704.0, wider.t_k_quantum, wider.t_k_decimals,
    )
    assert _quantize(1702.0, wider.t_k_quantum, wider.t_k_decimals) == _quantize(
        1700.0, wider.t_k_quantum, wider.t_k_decimals,
    )
    _use_cache(monkeypatch, tmp_path)
    with optimizer_engine_neighbour({"control_quantization": "coarse"}):
        _call(_request(1700.0), {"answer": "base"})
        outside = _call(_request(1704.0), {"answer": "outside-coarse"})
    assert outside == {"answer": "outside-coarse"}
    engine_cache._MEMORY_CACHE.clear()
    with optimizer_engine_neighbour({"control_quantization": "xx_coarse"}):
        hit = engine_cache.cached_engine_call(
            "openimcc",
            _request(1702.0),
            lambda: pytest.fail("xx_coarse pass solved a filled wide cell"),
        )
    assert "neighbour_hit" not in hit
    assert hit.neighbour_hit == "xx_coarse"
    assert hit["answer"] in {"base", "outside-coarse"}


def test_a_different_model_is_not_a_neighbour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_cache(monkeypatch, tmp_path)
    with optimizer_engine_neighbour({"control_quantization": "coarse"}):
        _call(_request(1700.0, model="a"), {"answer": "a"})
        other = _call(_request(1700.2, model="b"), {"answer": "b"})
    assert other == {"answer": "b"}
    assert "neighbour_hit" not in other


def test_list_hot_keeps_its_length_and_carries_the_preset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_cache(monkeypatch, tmp_path)
    quantum = ControlQuantization.from_name("medium").t_k_quantum
    with optimizer_engine_neighbour({"control_quantization": "medium"}):
        stored = engine_cache.cached_engine_call(
            "openimcc",
            _request(1800.0),
            lambda: engine_cache.EngineCacheValue([1, 2, 3]),
        )
        assert stored == [1, 2, 3]
        hit = engine_cache.cached_engine_call(
            "openimcc",
            _request(1800.0 + quantum * 0.2),
            lambda: pytest.fail("medium pass solved a filled cell"),
        )
    assert isinstance(hit, list)
    assert hit == [1, 2, 3]
    assert hit.neighbour_hit == "medium"


def test_readonly_serves_a_neighbour_and_a_missing_index_stays_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _use_cache(monkeypatch, tmp_path)
    base = _request(1700.0)
    nearby = _request(1700.2)
    with optimizer_engine_neighbour({"control_quantization": "coarse"}):
        _call(base, {"answer": "base"})
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_MODE", "readonly")
    engine_cache._MEMORY_CACHE.clear()
    engine_cache._close_connections()
    with optimizer_engine_neighbour({"control_quantization": "coarse"}):
        hit = engine_cache.cached_engine_call(
            "openimcc", nearby, lambda: pytest.fail("readonly coarse pass solved"),
        )
    assert hit["answer"] == "base"
    assert "neighbour_hit" not in hit
    assert hit.neighbour_hit == "coarse"
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT COUNT(*) FROM engine_outputs").fetchone()[0]
    assert rows == 1

    bare = tmp_path / "exact-only.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(bare))
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_MODE", raising=False)
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()
    _call(base, {"answer": "exact"})
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_MODE", "readonly")
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()
    with optimizer_engine_neighbour({"control_quantization": "coarse"}):
        solved = _call(nearby, {"answer": "scored-readonly"})
    assert solved == {"answer": "scored-readonly"}
    with sqlite3.connect(bare) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "engine_neighbour_cells" not in names


def test_exact_store_does_not_create_the_side_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _use_cache(monkeypatch, tmp_path)
    _call(_request(1700.0), {"answer": 1})
    with sqlite3.connect(path) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert names == {
        "engine_outputs",
        "provenance",
        "box_sequences",
        "engine_output_species",
    }


def test_optimizer_evaluate_runs_inside_the_neighbour_scope() -> None:
    source_path = Path(__file__).resolve().parents[1] / "simulator" / "optimize" / "evaluate.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    wrapped = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "evaluate":
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.With):
                continue
            context = child.items[0].context_expr
            if not isinstance(context, ast.Call):
                continue
            func = context.func
            if not isinstance(func, ast.Name) or func.id != "optimizer_engine_neighbour":
                continue
            wrapped = any(
                isinstance(stmt, ast.Assign)
                and isinstance(stmt.value, ast.Call)
                and isinstance(stmt.value.func, ast.Name)
                and stmt.value.func.id == "_execute_run"
                for stmt in child.body
            )
    assert wrapped
    source = source_path.read_text(encoding="utf-8")
    assert "def evaluate(" in source
    assert "_NEIGHBOUR_STAMP" in source


def test_presetless_scope_does_not_write_a_neighbour_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _use_cache(monkeypatch, tmp_path)
    with optimizer_engine_neighbour(None):
        _call(_request(1700.0), {"answer": "plain"})
    with optimizer_engine_neighbour({}):
        _call(_request(1710.0), {"answer": "empty"})
    with sqlite3.connect(path) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "engine_neighbour_cells" not in names


def test_expire_prune_and_drop_delete_neighbour_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _use_cache(monkeypatch, tmp_path)
    with optimizer_engine_neighbour({"control_quantization": "coarse"}):
        _call(_request(1700.0), {"answer": "base"})
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO engine_neighbour_cells "
            "(engine, chem_version, preset, cell, key) VALUES (?,?,?,?,?)",
            ("openimcc", "999", "coarse", b"stale-cell", b"stale-key"),
        )
        connection.commit()
    engine_cache.prune_cache_file(path, apply=True)
    with sqlite3.connect(path) as connection:
        stale = connection.execute(
            "SELECT COUNT(*) FROM engine_neighbour_cells WHERE chem_version='999'"
        ).fetchone()[0]
        kept = connection.execute(
            "SELECT COUNT(*) FROM engine_neighbour_cells"
        ).fetchone()[0]
    assert stale == 0
    assert kept >= 1
    engine_cache.expire_cache_file(path, engine="openimcc", all_rows=True, apply=True)
    with sqlite3.connect(path) as connection:
        left = connection.execute(
            "SELECT COUNT(*) FROM engine_neighbour_cells WHERE engine='openimcc'"
        ).fetchone()[0]
    assert left == 0

    bare = tmp_path / "bare.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(bare))
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()
    _call(_request(1800.0), {"answer": "exact"})
    assert engine_cache.drop_chemistry_version("openimcc", 1) == 1


def test_coarse_evaluate_stamps_the_score_and_a_fine_run_reexecutes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real evaluate() at coarse reuses a neighbour and is not certified.

    The analytical profile is the one evaluate() already runs without a melt
    domain refusal. The coarse preset is attached where a cached-real run
    puts reduced_real_cache, so the scope, the stamp, and the cache key are
    the production ones. A fine request with the same profile_id does not
    reuse that score.
    """

    from types import MappingProxyType

    import simulator.optimize.evaluate as evaluate_module
    from simulator.fidelity_vocabulary import EvidenceClass
    from simulator.optimize.evalspec import cache_key
    from simulator.optimize.evaluate import evaluate
    from simulator.optimize.honesty import optimizer_tier_label
    from simulator.optimize.recipe import RecipePatch, RecipeSchema
    from simulator.optimize.study import (
        Candidate,
        StudyRecord,
        _exact_recipe_winner,
        _light_backend_status_trace,
        _lookup_cached,
        neighbour_scored_result_reusable,
    )
    from tests.test_optimizer_evaluate import PROFILE, _execution

    _use_cache(monkeypatch, tmp_path)
    quantum = ControlQuantization.from_name("coarse").t_k_quantum
    nearby = _request(1700.0 + quantum * 0.2)
    with optimizer_engine_neighbour({"control_quantization": "coarse"}):
        _call(_request(1700.0), {"answer": "base"})
    original_run_options = evaluate_module._run_options

    def install(preset: str) -> None:
        def wrapped(profile: object, fidelity: str) -> MappingProxyType:
            options = dict(original_run_options(profile, fidelity))
            cache = dict(options.get("reduced_real_cache") or {})
            cache["control_quantization"] = preset
            options["reduced_real_cache"] = cache
            return MappingProxyType(options)

        monkeypatch.setattr(evaluate_module, "_run_options", wrapped)

    class _CallingExecutor:
        def __init__(self, request: dict[str, object]) -> None:
            self.request = request
            self.calls = 0
            self.solved = 0

        def execute(self, config: object) -> object:
            self.calls += 1
            self.config = config

            def solve() -> dict[str, str]:
                self.solved += 1
                return {"answer": "solved"}

            engine_cache.cached_engine_call("openimcc", self.request, solve)
            return _execution()

    patch = RecipePatch({("campaigns", "C0", "temp_range_C"): [20.0, 950.0]})
    install("coarse")
    coarse_executor = _CallingExecutor(nearby)
    coarse = evaluate(
        patch,
        "lunar_mare_low_ti",
        "fast",
        profile=PROFILE,
        executor=coarse_executor,
    )
    assert coarse.feasible
    assert coarse_executor.calls == 1
    assert coarse_executor.solved == 0
    assert coarse.neighbour_hits >= 1
    assert coarse.neighbour_preset == "coarse"
    assert coarse.eval_spec is not None
    assert coarse.eval_spec.control_quantization == "coarse"
    assert coarse.eval_spec.profile_id == PROFILE["profile_id"]
    trace = coarse.run_reference.trace
    assert trace["neighbour_hits"] == coarse.neighbour_hits
    assert trace["neighbour_preset"] == "coarse"
    light = _light_backend_status_trace(coarse)
    assert light is not None
    assert light["neighbour_hits"] == coarse.neighbour_hits
    outside_scope = _call(
        _request(1700.0 + quantum * 0.3),
        {"answer": "outside-scope"},
    )
    assert outside_scope == {"answer": "outside-scope"}
    assert not hasattr(outside_scope, "neighbour_hit")
    carrier = {
        "cache_state": "live_fill",
        "backend_name": "alphamelts",
        "backend_status": "ok",
        "backend_authoritative": True,
        "evidence_class": EvidenceClass.MELTS.value,
        "neighbour_hits": coarse.neighbour_hits,
        "neighbour_preset": coarse.neighbour_preset,
    }
    label = optimizer_tier_label(carrier, dict(light))
    assert label["ux_label"] == "COARSE_NEIGHBOUR"
    assert label["certification_allowed"] is False
    certified = dict(carrier)
    certified["neighbour_hits"] = 0
    certified.pop("neighbour_preset")
    assert optimizer_tier_label(certified, {})["ux_label"] == "CERTIFIED"

    install("fine")
    fine_executor = _CallingExecutor(nearby)
    fine = evaluate(
        patch,
        "lunar_mare_low_ti",
        "fast",
        profile=PROFILE,
        executor=fine_executor,
    )
    assert fine.feasible
    assert fine_executor.calls == 1
    assert fine_executor.solved == 1
    assert fine.neighbour_hits == 0
    assert fine.eval_spec is not None
    assert fine.eval_spec.control_quantization == "fine"
    assert fine.eval_spec.profile_id == coarse.eval_spec.profile_id
    assert cache_key(fine.eval_spec) != cache_key(coarse.eval_spec)
    assert neighbour_scored_result_reusable(coarse, requested="coarse")
    assert not neighbour_scored_result_reusable(coarse, requested="fine")
    assert neighbour_scored_result_reusable(fine, requested="fine")

    class _ReplayStore:
        def __init__(self, row: object) -> None:
            self.row = row
            self.lookups = 0

        def lookup(self, spec: object) -> object:
            self.lookups += 1
            return self.row

    store = _ReplayStore(coarse)
    missed = _lookup_cached(
        Candidate("fine-replay", patch),
        PROFILE,
        "lunar_mare_low_ti",
        "fast",
        RecipeSchema(),
        store,
        None,
    )
    assert missed is None
    assert store.lookups == 1
    install("coarse")
    replayed = _lookup_cached(
        Candidate("coarse-replay", patch),
        PROFILE,
        "lunar_mare_low_ti",
        "fast",
        RecipeSchema(),
        store,
        None,
    )
    assert replayed is not None
    assert replayed.neighbour_hits >= 1

    neighbour_row = StudyRecord(
        candidate_id="neighbour",
        patch=patch,
        feasible=True,
        status="ok",
        objectives={"oxygen_kg": 1.0},
        feasibility_margins={},
        trace_summary={"neighbour_hits": coarse.neighbour_hits, "neighbour_preset": "coarse"},
    )
    exact_row = StudyRecord(
        candidate_id="exact",
        patch=patch,
        feasible=True,
        status="ok",
        objectives={"oxygen_kg": 1.0},
        feasibility_margins={},
        trace_summary={},
    )
    assert _exact_recipe_winner((neighbour_row, exact_row)).candidate_id == "exact"
    assert _exact_recipe_winner((neighbour_row,)) is None
    assert _exact_recipe_winner((exact_row,)).candidate_id == "exact"
