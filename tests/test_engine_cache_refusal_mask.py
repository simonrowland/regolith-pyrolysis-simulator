"""Contract tests for the refusal mask. The controller runs these.

A masked cell returns the typed refusal without a second engine call.
Exact outputs stay on the engine + version + request key. The mask is a
separate table. A sampled key is solved live; a different outcome deletes
the cell. ``clear_refusal_mask`` drops one engine's cells and audit rows
for one class.
"""

import json
import sqlite3
import threading

import pytest

from simulator import engine_cache


_OUTPUT_COLUMNS = (
    "key", "value", "engine", "chem_version", "source_box", "row_seq", "provenance_id",
)
_PROBE_CLASSES = (
    ("internal-analytical", "refusal:unavailable"),
    ("alphamelts", "exception:ValueError"),
    ("alphamelts", "refusal:engine_crash"),
    ("thermoengine", "exception:ThermoEngineFO2OmittedError"),
    ("thermoengine", "exception:ThermoEngineNonFiniteField"),
    ("thermoengine", "exception:ThermoEngineOutOfDomainError"),
    ("thermoengine", "refusal:unavailable"),
)


def _enable(tmp_path, monkeypatch):
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_MODE", raising=False)
    engine_cache.engine_cache_counters(reset=True)
    engine_cache._MEMORY_CACHE.clear()


def _refusal(reason):
    return {"status": "refusal", "refusal_reason": reason}


def _call(engine, request, solve):
    return engine_cache.cached_engine_call(engine, request, solve)


def _same_cell_sample(engine, base=1000.0):
    """One selected key and one unselected key inside a single 1 K cell."""

    version = engine_cache.ENGINE_CHEMISTRY_VERSION[engine]
    selected = None
    unselected = None
    cell = None
    for index in range(400):
        request = {"temperature_K": base + index * 0.001}
        packed = engine_cache.refusal_mask_cell(request)
        assert packed is not None
        if cell is None:
            cell = packed
        assert packed == cell
        key = engine_cache.engine_cache_key(engine, version, request)
        if engine_cache.mask_verify_selected(key):
            if selected is None:
                selected = request
        elif unselected is None:
            unselected = request
        if selected is not None and unselected is not None:
            return selected, unselected
    raise AssertionError(f"no sampled pair for {engine} at {base}")


def _mask_rows(path):
    with sqlite3.connect(path) as connection:
        present = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "engine_refusal_mask" not in present:
            return [], []
        cells = connection.execute(
            "SELECT engine, chem_version, refusal_class, hits, verifies "
            "FROM engine_refusal_mask ORDER BY engine, refusal_class"
        ).fetchall()
        audit = connection.execute(
            "SELECT disposition, engine, refusal_class, observed "
            "FROM engine_refusal_audit ORDER BY disposition, engine"
        ).fetchall()
        return cells, audit


def test_coarse_quanta_match_the_control_preset():
    from simulator.reduced_real_determinism import (
        ControlQuantization,
        _quantize,
        _sigfig,
    )

    coarse = ControlQuantization.from_name("coarse")
    assert engine_cache._MASK_T_QUANTUM == coarse.t_k_quantum
    assert engine_cache._MASK_PRESSURE_QUANTUM == coarse.pressure_bar_quantum
    assert engine_cache._MASK_LOG_FO2_QUANTUM == coarse.log_fo2_quantum
    assert engine_cache._MASK_COMPOSITION_SIG_FIGS == coarse.composition_sig_figs
    assert engine_cache._MASK_T_DECIMALS == coarse.t_k_decimals
    assert engine_cache._MASK_PRESSURE_DECIMALS == coarse.pressure_bar_decimals
    assert engine_cache._MASK_LOG_FO2_DECIMALS == coarse.log_fo2_decimals
    samples = [None, 0.0, 1.0, 1.2345, 1000.4, 1000.6, 1e-8, 50.04]
    for value in samples:
        assert engine_cache._mask_quantize(
            value, coarse.t_k_quantum, coarse.t_k_decimals,
        ) == _quantize(value, coarse.t_k_quantum, coarse.t_k_decimals)
        assert engine_cache._mask_sigfig(
            value, coarse.composition_sig_figs,
        ) == _sigfig(value, coarse.composition_sig_figs)
    assert engine_cache._mask_quantize(float("nan"), 1.0, 0) is None
    assert engine_cache._mask_sigfig(float("inf"), 3) is None


def test_one_quantum_step_changes_the_cell():
    inside = {
        "temperature_K": 1000.1,
        "pressure_bar": 1.0001,
        "fO2_log": -8.004,
        "composition_wt_pct": {"SiO2": 50.04, "MgO": 0.0},
        "engine_settings": {"temperature_K": 1000.1},
    }
    also_inside = {
        "temperature_K": 1000.4,
        "pressure_bar": 1.0001,
        "fO2_log": -8.004,
        "composition_wt_pct": {"SiO2": 50.04, "MgO": 0.0},
        "engine_settings": {"temperature_K": 1000.1},
    }
    stepped = dict(inside)
    stepped["temperature_K"] = 1001.0
    exact_settings = dict(inside)
    exact_settings["engine_settings"] = {"temperature_K": 1000.2}
    assert engine_cache.refusal_mask_cell(inside) == engine_cache.refusal_mask_cell(also_inside)
    assert engine_cache.refusal_mask_cell(inside) != engine_cache.refusal_mask_cell(stepped)
    assert engine_cache.refusal_mask_cell(inside) != engine_cache.refusal_mask_cell(exact_settings)
    assert engine_cache._coarsen_request({"temperature_K": True}) == {"temperature_K": True}
    assert engine_cache.refusal_mask_cell({"note": float("nan")}) is None
    assert engine_cache.refusal_mask_cell({1: "x"}) is None


def test_allowlist_covers_the_probe_classes_and_not_the_transient_ones():
    for item in _PROBE_CLASSES:
        assert item in engine_cache.MASKABLE_REFUSAL_CLASSES
    assert ("alphamelts", "refusal:unavailable") not in engine_cache.MASKABLE_REFUSAL_CLASSES
    assert ("alphamelts", "refusal:engine_timeout") not in engine_cache.MASKABLE_REFUSAL_CLASSES
    assert ("magemin", "exception:ValueError") not in engine_cache.MASKABLE_REFUSAL_CLASSES
    assert ("vaporock", "refusal:unavailable") not in engine_cache.MASKABLE_REFUSAL_CLASSES
    assert engine_cache.MASK_VERIFY_EVERY == 32


def test_sampler_is_deterministic():
    version = engine_cache.ENGINE_CHEMISTRY_VERSION["alphamelts"]
    flags = []
    for index in range(320):
        request = {"temperature_K": 2000.0 + index}
        key = engine_cache.engine_cache_key("alphamelts", version, request)
        flags.append(engine_cache.mask_verify_selected(key))
        assert engine_cache.mask_verify_selected(key) is flags[-1]
    assert 0 < sum(flags) < len(flags)


def test_unselected_key_learns_once_and_does_not_store_an_output(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    _selected, request = _same_cell_sample("alphamelts")
    calls = []

    def solve():
        calls.append(1)
        return _refusal("engine_crash")

    first = _call("alphamelts", request, solve)
    engine_cache._MEMORY_CACHE.clear()
    second = _call("alphamelts", request, solve)
    third = _call("alphamelts", request, solve)
    assert calls == [1]
    assert "mask_hit" not in first
    assert second["mask_hit"] == third["mask_hit"] == "refusal:engine_crash"
    assert second["refusal_reason"] == "engine_crash"
    path = tmp_path / "cache.sqlite"
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert [row[1] for row in connection.execute("PRAGMA table_info(engine_outputs)")] == list(_OUTPUT_COLUMNS)
        assert connection.execute("SELECT count(*) FROM engine_outputs").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM engine_refusal_mask").fetchone()[0] == 1
        hits, verifies = connection.execute(
            "SELECT hits, verifies FROM engine_refusal_mask"
        ).fetchone()
        # The second call is the sqlite short-circuit. The third is the
        # in-process replay and does not bump the file again.
        assert hits == 1
        assert verifies == 0
        landed = connection.execute(
            "SELECT disposition, observed FROM engine_refusal_audit"
        ).fetchall()
    assert landed == [("landed", "refusal:engine_crash")]
    counts = engine_cache.engine_cache_counters()["alphamelts"]
    assert counts["misses"] == 1
    assert counts["stores"] == 0
    assert counts["not_stored:refusal:engine_crash"] == 1
    assert counts["mask_hit:refusal:engine_crash"] == 2
    assert "mask_verify:refusal:engine_crash" not in counts


def test_mask_tables_stay_absent_until_a_class_is_learned(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    assert _call("alphamelts", {"temperature_K": 1.0}, lambda: {"answer": 1}) == {"answer": 1}
    path = tmp_path / "cache.sqlite"
    with sqlite3.connect(path) as connection:
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "engine_refusal_mask" not in names
        assert "engine_refusal_audit" not in names
        assert connection.execute("PRAGMA user_version").fetchone()[0] == engine_cache._CACHE_SCHEMA_VERSION
        assert engine_cache._CACHE_SCHEMA_VERSION == 4


def test_selected_key_verifies_and_a_different_outcome_unmasks(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    selected, unselected = _same_cell_sample("alphamelts", 1100.0)
    answers = iter(("engine_crash", "unavailable", "engine_crash"))
    calls = []

    def solve():
        calls.append(1)
        return _refusal(next(answers))

    _call("alphamelts", selected, solve)
    engine_cache._MEMORY_CACHE.clear()
    neighbor = _call("alphamelts", unselected, solve)
    assert neighbor["mask_hit"] == "refusal:engine_crash"
    assert calls == [1]
    engine_cache._MEMORY_CACHE.clear()
    contradicted = _call("alphamelts", selected, solve)
    assert contradicted["refusal_reason"] == "unavailable"
    assert "mask_hit" not in contradicted
    assert calls == [1, 1]
    path = tmp_path / "cache.sqlite"
    cells, audit = _mask_rows(path)
    assert cells == []
    assert ("false_mask", "alphamelts", "refusal:engine_crash", "refusal:unavailable") in audit
    engine_cache._MEMORY_CACHE.clear()
    again = _call("alphamelts", unselected, solve)
    assert "mask_hit" not in again
    assert again["refusal_reason"] == "engine_crash"
    assert calls == [1, 1, 1]
    cells, audit = _mask_rows(path)
    assert [row[2] for row in cells] == ["refusal:engine_crash"]
    landed = [row for row in audit if row[0] == "landed"]
    assert len(landed) == 1
    assert ("false_mask", "alphamelts", "refusal:engine_crash", "refusal:unavailable") in audit
    counts = engine_cache.engine_cache_counters()["alphamelts"]
    assert counts["mask_false:refusal:engine_crash"] == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM engine_outputs").fetchone()[0] == 0


def test_a_success_inside_a_sampled_cell_is_stored_as_a_normal_hit(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    selected, _unselected = _same_cell_sample("alphamelts", 1200.0)
    calls = []

    def solve():
        calls.append(len(calls))
        if len(calls) == 1:
            return _refusal("engine_crash")
        return {"answer": 9}

    assert "mask_hit" not in _call("alphamelts", selected, solve)
    engine_cache._MEMORY_CACHE.clear()
    assert _call("alphamelts", selected, solve) == {"answer": 9}
    engine_cache._MEMORY_CACHE.clear()
    assert _call("alphamelts", selected, lambda: pytest.fail("stored success was not a hit")) == {"answer": 9}
    assert calls == [0, 1]
    path = tmp_path / "cache.sqlite"
    cells, audit = _mask_rows(path)
    assert cells == []
    assert ("false_mask", "alphamelts", "refusal:engine_crash", "accepted") in audit
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM engine_outputs").fetchone()[0] == 1
    counts = engine_cache.engine_cache_counters()["alphamelts"]
    assert counts["stores"] == 1
    assert counts["hits"] == 1
    assert counts["mask_false:refusal:engine_crash"] == 1


def test_unlisted_classes_solve_every_time_and_write_no_mask(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    cases = (
        ("alphamelts", {"temperature_K": 1300.0}, lambda: _refusal("unavailable")),
        ("alphamelts", {"temperature_K": 1301.0}, lambda: _refusal("engine_timeout")),
        ("magemin", {"temperature_K": 1302.0}, None),
    )
    for engine, request, factory in cases:
        calls = []

        def solve(factory=factory, calls=calls):
            calls.append(1)
            if factory is None:
                raise ValueError("engine failed")
            return factory()

        for _ in range(2):
            if factory is None:
                with pytest.raises(ValueError, match="engine failed"):
                    _call(engine, request, solve)
            else:
                result = _call(engine, request, solve)
                assert "mask_hit" not in result
            engine_cache._MEMORY_CACHE.clear()
        assert calls == [1, 1]
    cells, _audit = _mask_rows(tmp_path / "cache.sqlite")
    assert cells == []
    magemin = engine_cache.engine_cache_counters()["magemin"]
    assert magemin["not_stored:exception:ValueError"] == 2
    assert "mask_hit:exception:ValueError" not in magemin


def test_readonly_serves_a_mask_and_does_not_learn_or_unmask(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    selected, unselected = _same_cell_sample("internal-analytical", 1400.0)
    calls = []

    def solve():
        calls.append(1)
        return _refusal("unavailable")

    _call("internal-analytical", unselected, solve)
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_MODE", "readonly")
    engine_cache._MEMORY_CACHE.clear()
    served = _call("internal-analytical", unselected, solve)
    assert served["mask_hit"] == "refusal:unavailable"
    assert calls == [1]
    path = tmp_path / "cache.sqlite"
    with sqlite3.connect(path) as connection:
        hits = connection.execute("SELECT hits FROM engine_refusal_mask").fetchone()[0]
    assert hits == 0
    other_calls = []

    def other():
        other_calls.append(1)
        return _refusal("unavailable")

    for _ in range(2):
        result = _call("internal-analytical", {"temperature_K": 1500.0}, other)
        assert "mask_hit" not in result
        engine_cache._MEMORY_CACHE.clear()
    assert other_calls == [1, 1]
    engine_cache._MEMORY_CACHE.clear()
    contradicted = _call(
        "internal-analytical", selected, lambda: {"answer": 3},
    )
    assert contradicted == {"answer": 3}
    cells, audit = _mask_rows(path)
    assert len(cells) == 1
    assert audit == [("landed", "internal-analytical", "refusal:unavailable", "refusal:unavailable")]
    counts = engine_cache.engine_cache_counters()["internal-analytical"]
    assert counts["mask_false:refusal:unavailable"] == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM engine_outputs").fetchone()[0] == 0


def test_exception_replay_uses_the_real_types(tmp_path, monkeypatch):
    from engines.alphamelts.thermoengine import (
        ThermoEngineFO2OmittedError,
        ThermoEngineNonFiniteField,
        ThermoEngineOutOfDomainError,
        ThermoEngineRefusalCause,
    )

    _enable(tmp_path, monkeypatch)
    cases = (
        (
            "alphamelts",
            1600.0,
            "exception:ValueError",
            lambda: ValueError("bad composition"),
        ),
        (
            "thermoengine",
            1700.0,
            "exception:ThermoEngineNonFiniteField",
            lambda: ThermoEngineNonFiniteField("liquid endmember fractions are not finite"),
        ),
        (
            "thermoengine",
            1800.0,
            "exception:ThermoEngineFO2OmittedError",
            lambda: ThermoEngineFO2OmittedError("solved fO2 was omitted"),
        ),
        (
            "thermoengine",
            1900.0,
            "exception:ThermoEngineOutOfDomainError",
            lambda: ThermoEngineOutOfDomainError(
                ThermoEngineRefusalCause.FO2_REQUIRES_IRON,
                requested=1.25,
                solved=2.5,
            ),
        ),
    )
    for engine, base, class_name, factory in cases:
        _selected, request = _same_cell_sample(engine, base)
        calls = []

        def solve(factory=factory, calls=calls):
            calls.append(1)
            raise factory()

        with pytest.raises(Exception) as first:
            _call(engine, request, solve)
        engine_cache._MEMORY_CACHE.clear()
        with pytest.raises(type(first.value)) as second:
            _call(engine, request, solve)
        assert calls == [1]
        assert getattr(first.value, "mask_hit", None) is None
        assert second.value.mask_hit == class_name
        assert type(second.value) is type(first.value)
        assert second.value.args == first.value.args
        if isinstance(first.value, ThermoEngineOutOfDomainError):
            assert second.value.cause is first.value.cause
            assert second.value.requested == first.value.requested
            assert second.value.solved == first.value.solved
    with sqlite3.connect(tmp_path / "cache.sqlite") as connection:
        assert connection.execute("SELECT count(*) FROM engine_outputs").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM engine_refusal_mask").fetchone()[0] == 4


def test_a_different_value_error_message_unmasks(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    selected, _unselected = _same_cell_sample("alphamelts", 2100.0)
    messages = iter(("one", "two", "two"))
    calls = []

    def solve():
        calls.append(1)
        raise ValueError(next(messages))

    with pytest.raises(ValueError, match="^one$"):
        _call("alphamelts", selected, solve)
    engine_cache._MEMORY_CACHE.clear()
    with pytest.raises(ValueError, match="^two$") as disagreed:
        _call("alphamelts", selected, solve)
    assert getattr(disagreed.value, "mask_hit", None) is None
    # The disagreed sample deletes the cell, then the miss learns "two".
    # The next selected call confirms that cell and is flagged.
    engine_cache._MEMORY_CACHE.clear()
    with pytest.raises(ValueError, match="^two$") as confirmed:
        _call("alphamelts", selected, solve)
    assert confirmed.value.mask_hit == "exception:ValueError"
    assert calls == [1, 1, 1]
    counts = engine_cache.engine_cache_counters()["alphamelts"]
    assert counts["mask_false:exception:ValueError"] == 1


def test_relearn_keeps_hit_counts_and_replaces_the_landed_teacher(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    _selected, request = _same_cell_sample("alphamelts", 2200.0)
    _call("alphamelts", request, lambda: _refusal("engine_crash"))
    engine_cache._MEMORY_CACHE.clear()
    _call("alphamelts", request, lambda: pytest.fail("mask did not short-circuit"))
    connection = next(iter(engine_cache._CONNECTIONS.values()))
    before = connection.execute(
        "SELECT hits, verifies FROM engine_refusal_mask"
    ).fetchone()
    assert before[0] >= 1
    row = connection.execute(
        "SELECT engine, chem_version, cell, refusal_class, outcome FROM engine_refusal_mask"
    ).fetchone()
    recipe = engine_cache.unpack(bytes(row[4]))
    key = engine_cache.engine_cache_key(
        "alphamelts", engine_cache.ENGINE_CHEMISTRY_VERSION["alphamelts"], request,
    )
    engine_cache._learn_mask_cell(
        connection,
        engine=row[0],
        version=row[1],
        cell=bytes(row[2]),
        refusal_class=row[3],
        recipe=recipe,
        cache_key=key,
    )
    after = connection.execute(
        "SELECT hits, verifies FROM engine_refusal_mask"
    ).fetchone()
    assert after == before
    landed = connection.execute(
        "SELECT count(*) FROM engine_refusal_audit WHERE disposition='landed'"
    ).fetchone()[0]
    assert landed == 1


def test_clear_removes_one_class_and_leaves_the_other(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    path = tmp_path / "cache.sqlite"
    _alpha_selected, alpha = _same_cell_sample("alphamelts", 2300.0)
    _thermo_selected, thermo = _same_cell_sample("thermoengine", 2400.0)
    _call("alphamelts", alpha, lambda: _refusal("engine_crash"))
    _call("thermoengine", thermo, lambda: _refusal("unavailable"))
    dry = engine_cache.clear_refusal_mask(
        path, engine="alphamelts", refusal_class="refusal:engine_crash", apply=False,
    )
    assert dry["dry_run"] is True
    assert dry["mask_cells"] == 1
    assert dry["audit_records"] == 1
    assert dry["deleted_mask_cells"] == 0
    assert dry["deleted_audit_records"] == 0
    engine_cache._MEMORY_CACHE.clear()
    assert _call(
        "alphamelts", alpha, lambda: pytest.fail("dry-run clear deleted the mask"),
    )["mask_hit"] == "refusal:engine_crash"
    applied = engine_cache.clear_refusal_mask(
        path, engine="alphamelts", refusal_class="refusal:engine_crash", apply=True,
    )
    assert applied["deleted_mask_cells"] == 1
    assert applied["deleted_audit_records"] == 1
    calls = []

    def solve():
        calls.append(1)
        return _refusal("engine_crash")

    assert "mask_hit" not in _call("alphamelts", alpha, solve)
    assert calls == [1]
    cells, _audit = _mask_rows(path)
    assert cells == [("thermoengine", "4", "refusal:unavailable", 0, 0)]
    with pytest.raises(ValueError, match="unknown engine"):
        engine_cache.clear_refusal_mask(
            path, engine="not-an-engine", refusal_class="refusal:engine_crash",
        )
    empty = engine_cache.clear_refusal_mask(
        path, engine="magemin", refusal_class="refusal:unavailable", apply=True,
    )
    assert empty["mask_cells"] == 0
    assert empty["deleted_mask_cells"] == 0
    cells_after, _audit_after = _mask_rows(path)
    assert cells_after == cells


def test_clear_of_a_cache_with_no_mask_does_not_create_tables(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    path = tmp_path / "cache.sqlite"
    _call("alphamelts", {"temperature_K": 1.0}, lambda: {"answer": 1})
    report = engine_cache.clear_refusal_mask(
        path, engine="alphamelts", refusal_class="refusal:engine_crash", apply=True,
    )
    assert report["mask_cells"] == 0
    assert report["audit_records"] == 0
    with sqlite3.connect(path) as connection:
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "engine_refusal_mask" not in names
        assert connection.execute("SELECT count(*) FROM engine_outputs").fetchone()[0] == 1


def test_clear_mask_command_defaults_to_dry_run(tmp_path, monkeypatch, capsys):
    from scripts.engine_cache_sync import main

    _enable(tmp_path, monkeypatch)
    path = tmp_path / "cache.sqlite"
    _selected, request = _same_cell_sample("alphamelts", 2500.0)
    _call("alphamelts", request, lambda: _refusal("engine_crash"))
    assert main([
        "clear-mask", str(path), "--engine", "alphamelts",
        "--class", "refusal:engine_crash",
    ]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["command"] == "clear-mask"
    assert dry["dry_run"] is True
    assert dry["mask_cells"] == 1
    assert dry["deleted_mask_cells"] == 0
    assert main([
        "clear-mask", str(path), "--engine", "alphamelts",
        "--class", "refusal:engine_crash", "--apply",
    ]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["deleted_mask_cells"] == 1
    assert applied["deleted_audit_records"] == 1
    assert main([
        "clear-mask", str(path), "--engine", "nope",
        "--class", "refusal:engine_crash",
    ]) == 1
    capsys.readouterr()


def test_merge_copies_output_rows_and_not_mask_tables(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    source = tmp_path / "cache.sqlite"
    _call("alphamelts", {"temperature_K": 2.0}, lambda: {"answer": 1})
    _selected, request = _same_cell_sample("alphamelts", 2600.0)
    _call("alphamelts", request, lambda: _refusal("engine_crash"))
    destination = tmp_path / "merged.sqlite"
    report = engine_cache.merge_cache_files(destination, [source])
    assert report["inserted"] == 1
    with sqlite3.connect(destination) as connection:
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "engine_refusal_mask" not in names
        assert connection.execute("SELECT count(*) FROM engine_outputs").fetchone()[0] == 1


def test_isolated_request_keeps_mask_hit_across_the_cell_projection(monkeypatch):
    from simulator.diagnostic_helpers import binary_pot_battery as battery

    seen = {"from_hot": 0}

    class _Cell:
        def as_payload(self):
            return {"status": "refusal", "refusal_reason": "engine_crash"}

    def from_hot(_payload):
        seen["from_hot"] += 1
        return _Cell()

    monkeypatch.setattr(battery.EquilibrateCell, "from_hot", from_hot)
    monkeypatch.setattr(battery, "normalize_engine_settings", lambda _engine, _settings: {})
    monkeypatch.setattr(engine_cache, "engine_cache_settings", lambda _engine, _settings: {})
    monkeypatch.setattr(
        engine_cache,
        "cached_engine_call",
        lambda _engine, _request, _solve: {
            "status": "refusal",
            "refusal_reason": "engine_crash",
            "refusal_cell": [1],
            "mask_hit": "refusal:engine_crash",
        },
    )
    worker = battery._IsolatedCellWorker.__new__(battery._IsolatedCellWorker)
    worker.engine = "alphamelts"
    worker.engine_settings = {}
    worker._lock = threading.Lock()
    result = worker.request(
        {
            "composition_wt_pct": {"SiO2": 50.0},
            "temperature_K": 1400.0,
            "pot_id": "p",
            "po2": {"mode": "engine_default", "po2_bar": None},
            "arm": "headline",
        },
        timeout_s=1.0,
    )
    assert seen["from_hot"] == 1
    assert result["mask_hit"] == "refusal:engine_crash"
    assert result["pot_id"] == "p"
    assert result["engine"] == "alphamelts"


def test_isolated_request_keeps_mask_hit_on_a_plain_refusal(monkeypatch):
    from simulator.diagnostic_helpers import binary_pot_battery as battery

    def from_hot(_payload):
        raise AssertionError("a plain refusal is not rebuilt as a cell")

    monkeypatch.setattr(battery.EquilibrateCell, "from_hot", from_hot)
    monkeypatch.setattr(battery, "normalize_engine_settings", lambda _engine, _settings: {})
    monkeypatch.setattr(engine_cache, "engine_cache_settings", lambda _engine, _settings: {})
    monkeypatch.setattr(
        engine_cache,
        "cached_engine_call",
        lambda _engine, _request, _solve: {
            "status": "refusal",
            "refusal_reason": "engine_crash",
            "mask_hit": "refusal:engine_crash",
        },
    )
    worker = battery._IsolatedCellWorker.__new__(battery._IsolatedCellWorker)
    worker.engine = "alphamelts"
    worker.engine_settings = {}
    worker._lock = threading.Lock()
    result = worker.request(
        {"temperature_K": 1400.0, "pot_id": "p"},
        timeout_s=1.0,
    )
    assert result["mask_hit"] == "refusal:engine_crash"
    assert result["pot_id"] == "p"
