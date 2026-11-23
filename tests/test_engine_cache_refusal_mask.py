"""Contract tests for the refusal mask. The controller runs these.

A masked cell returns the typed refusal without a second engine call.
Exact outputs stay on the engine + version + request key. The mask is a
separate table. A stored exact row wins over a mask cell. A sampled key
is solved live; a different outcome deletes the cell.
``clear_refusal_mask`` drops one engine's cells and audit rows for one
class.
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
    engine_cache._MASK_DENY.clear()


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
            "SELECT engine, chem_version, refusal_class "
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
    quanta = (
        (coarse.t_k_quantum, coarse.t_k_decimals),
        (coarse.pressure_bar_quantum, coarse.pressure_bar_decimals),
        (coarse.log_fo2_quantum, coarse.log_fo2_decimals),
    )
    bases = (
        0.0, 1.0, -1.0, 1e-8, 1e-4, 0.001, 0.01, 1.5, 10.0,
        50.04, 1000.0, 1e6, -8.0, -0.005,
    )
    for quantum, digits in quanta:
        for base in bases:
            for value in (base, base + 0.5 * quantum, base - 0.5 * quantum, None):
                assert engine_cache._mask_quantize(value, quantum, digits) == _quantize(
                    value, quantum, digits,
                )
    sig_figs = coarse.composition_sig_figs
    for base in bases:
        for value in (base, None):
            assert engine_cache._mask_sigfig(value, sig_figs) == _sigfig(value, sig_figs)
    for bad in (float("nan"), float("inf"), float("-inf")):
        assert engine_cache._mask_quantize(bad, 1.0, 0) == _quantize(bad, 1.0, 0)
        assert engine_cache._mask_sigfig(bad, sig_figs) == _sigfig(bad, sig_figs)


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


def test_battery_po2_names_share_a_significant_figure_bucket():
    low = {
        "po2": 1.234e-8,
        "vapor_transport_pO2_bar": 1.234e-8,
        "po2_bar": 1.234e-8,
    }
    near = {
        "po2": 1.2344e-8,
        "vapor_transport_pO2_bar": 1.2344e-8,
        "po2_bar": 1.2344e-8,
    }
    stepped = {
        "po2": 1.24e-8,
        "vapor_transport_pO2_bar": 1.24e-8,
        "po2_bar": 1.24e-8,
    }
    nested = {"po2": {"mode": "commanded", "po2_bar": 1.234e-8}}
    nested_near = {"po2": {"mode": "commanded", "po2_bar": 1.2344e-8}}
    assert engine_cache.refusal_mask_cell(low) == engine_cache.refusal_mask_cell(near)
    assert engine_cache.refusal_mask_cell(low) != engine_cache.refusal_mask_cell(stepped)
    assert engine_cache.refusal_mask_cell(nested) == engine_cache.refusal_mask_cell(nested_near)


def test_allowlist_covers_the_probe_classes_and_not_the_transient_ones():
    for item in _PROBE_CLASSES:
        assert item in engine_cache.MASKABLE_REFUSAL_CLASSES
    assert ("alphamelts", "exception:ValueError") not in engine_cache.MASKABLE_REFUSAL_CLASSES
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
    answers = iter(("engine_crash", "unavailable", "engine_crash", "engine_crash"))
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
    further = _call("alphamelts", unselected, solve)
    assert "mask_hit" not in further
    assert calls == [1, 1, 1, 1]
    cells, audit = _mask_rows(path)
    assert cells == []
    assert ("false_mask", "alphamelts", "refusal:engine_crash", "refusal:unavailable") in audit
    counts = engine_cache.engine_cache_counters()["alphamelts"]
    assert counts["mask_false:refusal:engine_crash"] == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM engine_outputs").fetchone()[0] == 0


def test_a_success_inside_a_sampled_cell_is_stored_as_a_normal_hit(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    selected, unselected = _same_cell_sample("alphamelts", 1200.0)
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
    neighbor_calls = []

    def neighbor():
        neighbor_calls.append(1)
        return _refusal("engine_crash")

    neighbor_result = _call("alphamelts", unselected, neighbor)
    assert "mask_hit" not in neighbor_result
    assert neighbor_result["refusal_reason"] == "engine_crash"
    assert neighbor_calls == [1]
    cells, audit = _mask_rows(path)
    assert cells == []
    assert ("false_mask", "alphamelts", "refusal:engine_crash", "accepted") in audit


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
    # A test-owned file is readonly only when its mode differs from the
    # runner snapshot. Pin that snapshot so this does not follow the mode
    # the suite inherited, and so the file stays off the runner path.
    engine_cache._RUNNER_CACHE_SNAPSHOT = (str(tmp_path / "runner-not-used"), "")
    engine_cache._MEMORY_CACHE.clear()
    served = _call("internal-analytical", unselected, solve)
    assert served["mask_hit"] == "refusal:unavailable"
    assert calls == [1]
    path = tmp_path / "cache.sqlite"
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
    # The file still holds the cell. This process must not serve it,
    # including from a replay that a drop failed to remove.
    later_calls = []

    def later():
        later_calls.append(1)
        return _refusal("unavailable")

    again = _call("internal-analytical", unselected, later)
    assert "mask_hit" not in again
    assert later_calls == [1]
    version = engine_cache.ENGINE_CHEMISTRY_VERSION["internal-analytical"]
    key = engine_cache.engine_cache_key("internal-analytical", version, unselected)
    path_value = engine_cache._configured_engine_cache_path()
    engine_cache._MEMORY_CACHE[(path_value, key, True)] = engine_cache._MaskReplay(
        "refusal:unavailable",
        {"kind": "refusal", "hot": _refusal("unavailable")},
    )
    remembered = _call("internal-analytical", unselected, later)
    assert "mask_hit" not in remembered
    assert later_calls == [1, 1]
    cells_after, audit_after = _mask_rows(path)
    assert len(cells_after) == 1
    assert audit_after == audit


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
        if isinstance(first.value, ThermoEngineOutOfDomainError):
            assert second.value.cause is first.value.cause
            assert second.value.requested is None
            assert second.value.solved is None
        else:
            assert second.value.args == first.value.args
    with sqlite3.connect(tmp_path / "cache.sqlite") as connection:
        assert connection.execute("SELECT count(*) FROM engine_outputs").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM engine_refusal_mask").fetchone()[0] == 3


def test_alphamelts_value_error_is_not_masked(tmp_path, monkeypatch):
    """Lookup on a battery-shaped request was not cheaper than 19 µs.

    A different message therefore cannot be a false mask: the class is
    never taught. The no-relearn rule for a class that is taught is
    test_selected_key_verifies_and_a_different_outcome_unmasks.
    """

    _enable(tmp_path, monkeypatch)
    selected, unselected = _same_cell_sample("alphamelts", 2100.0)
    messages = iter(("one", "two"))
    calls = []

    def solve():
        calls.append(1)
        raise ValueError(next(messages))

    with pytest.raises(ValueError, match="^one$") as first:
        _call("alphamelts", selected, solve)
    engine_cache._MEMORY_CACHE.clear()
    with pytest.raises(ValueError, match="^two$") as second:
        _call("alphamelts", unselected, solve)
    assert getattr(first.value, "mask_hit", None) is None
    assert getattr(second.value, "mask_hit", None) is None
    assert calls == [1, 1]
    cells, audit = _mask_rows(tmp_path / "cache.sqlite")
    assert cells == []
    assert audit == []
    counts = engine_cache.engine_cache_counters()["alphamelts"]
    assert counts["not_stored:exception:ValueError"] == 2
    assert "mask_hit:exception:ValueError" not in counts


def test_relearn_replaces_the_landed_teacher_until_the_cell_is_mixed(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    _selected, request = _same_cell_sample("alphamelts", 2200.0)
    _call("alphamelts", request, lambda: _refusal("engine_crash"))
    connection = next(iter(engine_cache._CONNECTIONS.values()))
    row = connection.execute(
        "SELECT engine, chem_version, cell, refusal_class, outcome FROM engine_refusal_mask"
    ).fetchone()
    recipe = engine_cache.unpack(bytes(row[4]))
    other_key = b"other-teacher"
    engine_cache._learn_mask_cell(
        connection,
        engine=row[0],
        version=row[1],
        cell=bytes(row[2]),
        refusal_class=row[3],
        recipe=recipe,
        cache_key=other_key,
    )
    landed_keys = connection.execute(
        "SELECT key FROM engine_refusal_audit WHERE disposition='landed'"
    ).fetchall()
    assert landed_keys == [(other_key,)]
    engine_cache._unmask_cell(
        connection,
        engine=row[0],
        version=row[1],
        cell=bytes(row[2]),
        refusal_class=row[3],
        cache_key=other_key,
        observed="accepted",
        path_value=str(tmp_path / "cache.sqlite"),
    )
    engine_cache._learn_mask_cell(
        connection,
        engine=row[0],
        version=row[1],
        cell=bytes(row[2]),
        refusal_class=row[3],
        recipe=recipe,
        cache_key=other_key,
    )
    cells, audit = _mask_rows(tmp_path / "cache.sqlite")
    assert cells == []
    assert ("false_mask", "alphamelts", "refusal:engine_crash", "accepted") in audit
    assert ("landed", "alphamelts", "refusal:engine_crash", "refusal:engine_crash") in audit


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
    # The class is gone before the next call. clear also deletes the
    # witness, so a later refusal of this class is allowed to teach again.
    cleared, _cleared_audit = _mask_rows(path)
    assert cleared == [("thermoengine", "4", "refusal:unavailable")]
    calls = []

    def solve():
        calls.append(1)
        return _refusal("engine_crash")

    # apply forgets this process's _MaskReplay. Nothing clears memory
    # between that command and this call: a remembered mask would skip it.
    assert "mask_hit" not in _call("alphamelts", alpha, solve)
    assert calls == [1]
    cells, _audit = _mask_rows(path)
    assert cells == [
        ("alphamelts", "4", "refusal:engine_crash"),
        ("thermoengine", "4", "refusal:unavailable"),
    ]
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


def test_clear_drops_an_in_process_deny(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    path = tmp_path / "cache.sqlite"
    selected, unselected = _same_cell_sample("internal-analytical", 2700.0)
    _call("internal-analytical", unselected, lambda: _refusal("unavailable"))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_MODE", "readonly")
    # Same snapshot pin as the readonly serve test. The contradiction must
    # not delete the row, including when the suite's snapshot mode is already
    # readonly.
    engine_cache._RUNNER_CACHE_SNAPSHOT = (str(tmp_path / "runner-not-used"), "")
    engine_cache._MEMORY_CACHE.clear()
    assert _call(
        "internal-analytical", selected, lambda: {"answer": 1},
    ) == {"answer": 1}
    with sqlite3.connect(path) as connection:
        saved = connection.execute(
            "SELECT engine, chem_version, cell, refusal_class, outcome "
            "FROM engine_refusal_mask"
        ).fetchone()
    assert saved is not None
    applied = engine_cache.clear_refusal_mask(
        path,
        engine="internal-analytical",
        refusal_class="refusal:unavailable",
        apply=True,
    )
    assert applied["deleted_mask_cells"] == 1
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO engine_refusal_mask "
            "(engine, chem_version, cell, refusal_class, outcome) "
            "VALUES (?, ?, ?, ?, ?)",
            saved,
        )
        connection.commit()
    engine_cache._MEMORY_CACHE.clear()
    served = _call(
        "internal-analytical",
        unselected,
        lambda: pytest.fail("clear left the in-process deny in place"),
    )
    assert served["mask_hit"] == "refusal:unavailable"


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


def test_stored_exact_refusal_wins_over_a_mask_cell(tmp_path, monkeypatch):
    """A cacheable refusal row is served even when its coarse cell is masked.

    Masking is for a key that has no exact row. The neighbor in the same
    cell, which was never stored, still takes the mask.
    """

    _enable(tmp_path, monkeypatch)
    engine = "alphamelts"
    _selected, stored_request = _same_cell_sample(engine, 1800.0)
    version = engine_cache.ENGINE_CHEMISTRY_VERSION[engine]
    cell = engine_cache.refusal_mask_cell(stored_request)
    assert cell is not None
    stored_calls = []

    def store():
        stored_calls.append(1)
        return {"status": "refusal", "refusal_reason": "out_of_basis"}

    stored = _call(engine, stored_request, store)
    assert stored == {"status": "refusal", "refusal_reason": "out_of_basis"}
    assert stored_calls == [1]

    teacher = None
    skip = [stored_request]
    for index in range(400):
        request = {"temperature_K": 1800.0 + index * 0.001}
        if request in skip:
            continue
        if engine_cache.refusal_mask_cell(request) != cell:
            continue
        key = engine_cache.engine_cache_key(engine, version, request)
        if engine_cache.mask_verify_selected(key):
            continue
        teacher = request
        break
    assert teacher is not None
    taught_calls = []

    def teach():
        taught_calls.append(1)
        return _refusal("engine_crash")

    taught = _call(engine, teacher, teach)
    assert taught_calls == [1]
    assert "mask_hit" not in taught

    engine_cache._MEMORY_CACHE.clear()
    replay_calls = []

    def replay_solve():
        replay_calls.append(1)
        return {"status": "refusal", "refusal_reason": "out_of_basis"}

    replay = _call(engine, stored_request, replay_solve)
    assert replay_calls == []
    assert replay["refusal_reason"] == "out_of_basis"
    assert "mask_hit" not in replay
    counts = engine_cache.engine_cache_counters()[engine]
    assert counts["hits"] == 1

    other = None
    skip.append(teacher)
    for index in range(400):
        request = {"temperature_K": 1800.0 + index * 0.001}
        if request in skip:
            continue
        if engine_cache.refusal_mask_cell(request) != cell:
            continue
        key = engine_cache.engine_cache_key(engine, version, request)
        if engine_cache.mask_verify_selected(key):
            continue
        other = request
        break
    assert other is not None
    masked_calls = []

    def masked_solve():
        masked_calls.append(1)
        return {"answer": 1}

    served = _call(engine, other, masked_solve)
    assert masked_calls == []
    assert served["refusal_reason"] == "engine_crash"
    assert served["mask_hit"] == "refusal:engine_crash"


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


def test_isolated_request_keeps_mask_hit_on_a_list_refusal(monkeypatch):
    from simulator.diagnostic_helpers import binary_pot_battery as battery

    seen = {"from_hot": 0}

    class _Cell:
        def as_payload(self):
            return {"status": "refusal", "refusal_reason": "engine_crash"}

    def from_hot(_payload):
        seen["from_hot"] += 1
        return _Cell()

    class _Hot(list):
        pass

    hot = _Hot([1])
    hot.mask_hit = "refusal:engine_crash"
    monkeypatch.setattr(battery.EquilibrateCell, "from_hot", from_hot)
    monkeypatch.setattr(battery, "normalize_engine_settings", lambda _engine, _settings: {})
    monkeypatch.setattr(engine_cache, "engine_cache_settings", lambda _engine, _settings: {})
    monkeypatch.setattr(
        engine_cache, "cached_engine_call", lambda _engine, _request, _solve: hot,
    )
    worker = battery._IsolatedCellWorker.__new__(battery._IsolatedCellWorker)
    worker.engine = "alphamelts"
    worker.engine_settings = {}
    worker._lock = threading.Lock()
    result = worker.request({"temperature_K": 1400.0, "pot_id": "p"}, timeout_s=1.0)
    assert seen["from_hot"] == 1
    assert result["mask_hit"] == "refusal:engine_crash"


def test_production_list_hot_learns_and_replays_the_same_list(tmp_path, monkeypatch):
    from simulator.melt_backend.base import equilibrium_cache_from_value, equilibrium_to_hot

    _enable(tmp_path, monkeypatch)

    def produced(engine, reason):
        return equilibrium_to_hot(
            {
                "status": "unavailable",
                "liquid_fraction": None,
                "phase_assemblage_available": False,
                "diagnostics": {"backend_status_reason": reason},
            },
            engine_id=engine,
        )

    cases = (
        ("thermoengine", "unavailable", "refusal:unavailable", 3000.0),
        ("alphamelts", "engine_crash", "refusal:engine_crash", 3100.0),
    )
    for engine, reason, class_name, base in cases:
        _selected, request = _same_cell_sample(engine, base)
        calls = []
        wrapped = produced(engine, reason)

        def solve(calls=calls, wrapped=wrapped):
            calls.append(1)
            return wrapped

        first = _call(engine, request, solve)
        assert isinstance(first, list)
        assert not isinstance(first, engine_cache._MaskedList)
        assert getattr(first, "mask_hit", None) is None
        engine_cache._MEMORY_CACHE.clear()
        second = _call(engine, request, solve)
        assert calls == [1]
        assert isinstance(second, list)
        assert second.mask_hit == class_name
        assert list(second) == engine_cache.unpack(engine_cache.pack(list(first)))
        restored = equilibrium_cache_from_value(second, engine_id=engine)
        assert restored["status"] == "unavailable"
    cells, audit = _mask_rows(tmp_path / "cache.sqlite")
    assert {row[2] for row in cells} == {"refusal:unavailable", "refusal:engine_crash"}
    assert {row[0] for row in audit} == {"landed"}
    with sqlite3.connect(tmp_path / "cache.sqlite") as connection:
        assert connection.execute("SELECT count(*) FROM engine_outputs").fetchone()[0] == 0


def test_headline_grid_keeps_mask_hit_on_the_result_and_the_exception(monkeypatch):
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE", raising=False)
    from simulator.diagnostic_helpers.binary_pot_battery import (
        BinaryPot,
        EngineHandle,
        Po2Request,
        equilibrate_cell,
    )
    from simulator.melt_backend.base import EquilibriumResult

    pot = BinaryPot(
        pot_id="mask-grid",
        kato_1993_table4_system="Al2O3-SiO2",
        why="mask_hit",
        composition_wt_pct={"SiO2": 50.0, "Al2O3": 50.0},
    )
    po2 = Po2Request(mode="engine_default", po2_bar=None)

    class _MaskedResult:
        def equilibrate(self, **_kwargs):
            result = EquilibriumResult(
                status="unavailable",
                liquid_fraction=None,
                phase_assemblage_available=False,
            )
            result.mask_hit = "refusal:unavailable"
            return result

    result_cell = equilibrate_cell(
        EngineHandle(
            name="thermoengine",
            backend=_MaskedResult(),
            available=True,
            unavailable_reason=None,
            takes_fo2=True,
            supports_intrinsic_fo2=True,
        ),
        pot,
        temperature_K=1500.0,
        po2=po2,
        timeout_s=1.0,
    )
    assert result_cell.mask_hit == "refusal:unavailable"
    assert "mask_hit" not in result_cell.as_payload()
    restored = type(result_cell).from_payload(result_cell.as_payload())
    assert restored.mask_hit is None

    class _MaskedBoom:
        def equilibrate(self, **_kwargs):
            exc = RuntimeError("masked crash")
            exc.mask_hit = "refusal:engine_crash"
            raise exc

    boom_cell = equilibrate_cell(
        EngineHandle(
            name="alphamelts",
            backend=_MaskedBoom(),
            available=True,
            unavailable_reason=None,
            takes_fo2=False,
            supports_intrinsic_fo2=False,
        ),
        pot,
        temperature_K=1600.0,
        po2=po2,
        timeout_s=1.0,
    )
    assert boom_cell.mask_hit == "refusal:engine_crash"
    assert "mask_hit" not in boom_cell.as_payload()


def test_mask_hit_does_not_change_score_json():
    """A mask hit is cache state. Score JSON, residuals, and envelope raw stay put."""

    from dataclasses import replace

    from scripts.calibration_battery import envelope, rail_for
    from simulator.diagnostic_helpers.binary_pot_battery import (
        EquilibrateCell,
        Po2Request,
        pairwise_residuals,
    )
    from simulator.diagnostic_helpers.binary_pot_scoring import (
        ActivityComparator,
        ScoringPot,
        score_scoring_arm,
    )

    taught = EquilibrateCell(
        pot_id="mask-score",
        engine="alphamelts",
        temperature_K=1500.0,
        po2=Po2Request(mode="engine_default", po2_bar=None),
        status="ok",
        refusal_reason=None,
        engine_status="ok",
        engine_reason=None,
        melt_activities={"SiO2": 0.4},
        gas_partial_pressures_Pa={"SiO": 12.0},
        liquid_fraction=1.0,
        wall_s=0.1,
        cpu_s=0.1,
        hostname="test",
        mask_hit=None,
    )
    hit = replace(taught, mask_hit="refusal:engine_crash")
    assert json.dumps(taught.as_payload(), sort_keys=True) == json.dumps(
        hit.as_payload(), sort_keys=True
    )
    partner = replace(
        taught,
        engine="thermoengine",
        melt_activities={"SiO2": 0.5},
        gas_partial_pressures_Pa={"SiO": 20.0},
    )
    taught_residuals = pairwise_residuals([taught, partner])
    hit_residuals = pairwise_residuals([hit, partner])
    assert taught_residuals
    assert taught_residuals == hit_residuals
    pot = ScoringPot(
        pot_id="mask-score",
        source_id="mask-source",
        observation_id="mask-obs",
        sample_no=None,
        temperatures_K=(1500.0,),
        composition_basis="wt_pct",
        composition_conversion="as_printed",
        composition_as_printed={"SiO2": 50.0, "Al2O3": 50.0},
        composition_wt_pct={"SiO2": 50.0, "Al2O3": 50.0},
        why="mask score independence",
    )
    comparator = ActivityComparator(
        source_id=pot.source_id,
        observation_id="mask-obs",
        species="SiO2",
        observable="activity",
        method_class="measured",
        sample_no=None,
        temperature_K=1500.0,
        measured=0.35,
        units="1",
        standard_state=None,
        uncertainty=None,
        doi=None,
        row={"value": 0.35},
    )

    def _raws(cells, comparators):
        return [
            row["raw"]
            for row in score_scoring_arm(
                pots=(pot,),
                cells=cells,
                comparators=comparators,
                envelope=envelope,
                rail_for=rail_for,
            )
        ]

    taught_cell_raw = _raws((taught,), ())
    hit_cell_raw = _raws((hit,), ())
    taught_comparator_raw = _raws((taught,), (comparator,))
    hit_comparator_raw = _raws((hit,), (comparator,))
    assert taught_cell_raw and taught_comparator_raw
    assert taught_cell_raw == hit_cell_raw
    assert taught_comparator_raw == hit_comparator_raw
    assert all("mask_hit" not in row["cell"] for row in taught_cell_raw)
    assert all("mask_hit" not in row["cell"] for row in taught_comparator_raw)


def test_cached_equilibrate_copies_mask_hit_off_a_list_before_decode(monkeypatch):
    from simulator.diagnostic_helpers.binary_pot_battery import (
        EngineHandle,
        _call_cached_backend_equilibrate,
    )
    from simulator.melt_backend.base import equilibrium_cache_value

    hot = engine_cache._MaskedList(equilibrium_cache_value({
        "status": "unavailable",
        "liquid_fraction": None,
        "phase_assemblage_available": False,
    }))
    hot.mask_hit = "refusal:unavailable"

    def cached(_engine, _request, _solve):
        return hot

    monkeypatch.setattr(engine_cache, "cached_engine_call", cached)
    monkeypatch.setattr(
        engine_cache, "_configured_engine_cache_path", lambda: "unused",
    )
    monkeypatch.setattr(
        engine_cache, "engine_cache_settings", lambda _engine, _settings: {},
    )
    handle = EngineHandle(
        name="thermoengine",
        backend=object(),
        available=True,
        unavailable_reason=None,
        takes_fo2=True,
        supports_intrinsic_fo2=True,
    )
    result = _call_cached_backend_equilibrate(handle, {}, lambda: None)
    assert result.mask_hit == "refusal:unavailable"
    assert result.status == "unavailable"
