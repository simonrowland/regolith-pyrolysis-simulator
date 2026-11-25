from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from functools import wraps

import pytest

from simulator import engine_cache
from simulator.battery.enums import Engine, NoticeKind
from simulator.diagnostic_helpers.binary_pot_battery import (
    BATTERY_ENGINE_NAMES,
    ARM_HEADLINE,
    EquilibrateCell,
    Po2Request,
    close_battery_engine_handle,
    equilibrate_cell,
    load_binary_pots,
    open_battery_engine,
)
from simulator.melt_backend.base import (
    EquilibriumResult,
    equilibrium_to_hot,
    vapor_pressure_source_code,
)
from tests.battery.cache_test_support import open_available_engine

pytestmark = pytest.mark.engine_cache_off


_ENGINE_NAMES = tuple(name for name in BATTERY_ENGINE_NAMES if name != "cached-real")
_POTS, _ = load_binary_pots()
_POT_BY_ID = {pot.pot_id: pot for pot in _POTS}
_REAL_POTS = (
    _POT_BY_ID["mgo_sio2_40_60"],
    _POT_BY_ID["feo_mgo_sio2_30_20_50"],
)


def _hot_consumer_view(cell: EquilibrateCell) -> dict:
    view = cell.as_payload()
    # Request decoration and process measurements are restored by the caller.
    for name in ("pot_id", "engine", "temperature_K", "po2", "arm",
                 "wall_s", "cpu_s", "hostname"):
        view.pop(name)
    view["vapor_pressure_backend_status"] = vapor_pressure_source_code(
        cell.vapor_pressure_backend_status
    )
    # Coefficient values have their own map; scorers read these semantics only.
    view["melt_activity_coefficient_details"] = {
        name: {
            "coefficient_basis": row.get("coefficient_basis"),
            "standard_state": {
                key: (row.get("standard_state") or {}).get(key)
                for key in ("convention", "phase", "component_basis")
            },
        }
        for name, row in cell.melt_activity_coefficient_details.items()
    }
    return view


def _equilibrium_consumer_view(result) -> dict:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        classify_equilibrate_outcome,
        crash_floor_engine_annotation,
        engine_flags_from_result,
        extract_reported_quantities,
        extract_vapor_authority,
        reported_activity_coefficients,
    )

    diagnostics = getattr(result, "diagnostics", {}) or {}
    vapor = diagnostics.get("vapor_pressure_diagnostic") or {}
    return {
        "classification": classify_equilibrate_outcome(result),
        "quantities": extract_reported_quantities(result),
        "coefficients": reported_activity_coefficients(result),
        "vapor_authority": extract_vapor_authority(result),
        "flags": engine_flags_from_result(result),
        "crash_annotation": crash_floor_engine_annotation(diagnostics),
        "returncode": (diagnostics.get("subprocess_failure") or {}).get("returncode"),
        "activities_standard_state": vapor.get("activities_standard_state"),
        "pressure_omissions": vapor.get("below_reporting_threshold_omissions"),
        "floor_notices": vapor.get("pO2_floor_inversion_notices_by_species"),
        "d207_notices": vapor.get("d207_notices"),
        "liquid_composition": getattr(result, "liquid_composition_wt_pct", {}),
        "fe_redox_split": getattr(result, "fe_redox_split", {}),
    }


@pytest.mark.parametrize("engine_name", _ENGINE_NAMES)
def test_hot_round_trip_preserves_real_engine_consumer_view(
    engine_name, tmp_path, monkeypatch, request
) -> None:
    # Cold cell is a live engine solve. The round-trip below uses this test's
    # own cache, not the runner file.
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE", raising=False)
    handle = open_available_engine(Engine(engine_name), nodeid=request.node.nodeid)
    results = []
    original = handle.backend.equilibrate

    @wraps(original)
    def record_result(*args, **kwargs):
        result = original(*args, **kwargs)
        results.append(result)
        return result

    monkeypatch.setattr(handle.backend, "equilibrate", record_result)
    try:
        cold = equilibrate_cell(
            handle, _REAL_POTS[0], temperature_K=2000.0,
            po2=Po2Request(mode="commanded", po2_bar=1e-8),
            physical_pressure_bar=1.0,
            timeout_s=90.0, isolated=False,
        )
        assert cold.status in {"ok", "refusal"}, cold.as_payload()
        if engine_name == "internal-analytical":
            assert cold.status == "ok", cold.as_payload()
        monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "hot.sqlite"))
        monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
        engine_cache.engine_cache_counters(reset=True)
        payload = engine_cache.cached_engine_call(engine_name, {}, cold.to_hot)
        engine_cache._MEMORY_CACHE.clear()

        def unexpected_solve():
            pytest.fail("warm cell must be decoded from SQLite")

        warm = EquilibrateCell.from_hot(
            engine_cache.cached_engine_call(engine_name, {}, unexpected_solve)
        )
        assert warm.vapor_pressure_backend_status == vapor_pressure_source_code(
            cold.vapor_pressure_backend_status
        )
        assert _hot_consumer_view(warm) == _hot_consumer_view(cold)
        assert _hot_consumer_view(EquilibrateCell.from_hot(payload)) == _hot_consumer_view(cold)
        assert engine_cache.engine_cache_counters()[engine_name]["hits"] == 1
        if engine_name == "internal-analytical":
            cold_provenance = json.loads(cold.vapor_pressure_backend_status_reason)
            warm_provenance = json.loads(warm.vapor_pressure_backend_status_reason)
            d207_fields = {
                "phase_basis_source",
                "phase_basis_detail",
                "phase_basis_notice",
                "notices",
            }
            assert d207_fields.issubset(cold_provenance)
            assert {name: warm_provenance[name] for name in d207_fields} == {
                name: cold_provenance[name] for name in d207_fields
            }
            assert warm_provenance == cold_provenance

        result = results[-1]
        if engine_name == "internal-analytical":
            vapor_diagnostic = (
                (getattr(result, "diagnostics", {}) or {}).get(
                    "vapor_pressure_diagnostic"
                )
                or {}
            )
            assert isinstance(vapor_diagnostic.get("d207_notices"), list)
        engine_cache.cached_engine_call(
            engine_name, {"call": "equilibrium-consumer-view"},
            lambda: equilibrium_to_hot(vars(result), engine_id=engine_name),
        )
        engine_cache._MEMORY_CACHE.clear()
        decoded_result = EquilibriumResult.from_hot(
            engine_cache.cached_engine_call(
                engine_name, {"call": "equilibrium-consumer-view"}, unexpected_solve
            ), engine_id=engine_name,
        )
        assert _equilibrium_consumer_view(decoded_result) == _equilibrium_consumer_view(result)
    finally:
        close_battery_engine_handle(handle)


@pytest.mark.parametrize("field,value", (
    ("vapor_pressure_backend_status_reason", '{"source":"antoine_fallback","version":"record"}'),
    ("engine_reason", "liquid reference with the complete engine warning"),
    ("vapor_pressures_source", {"SiO": "antoine_fallback_from_vaporock:detail", "Mg": "engine_lineage"}),
    ("vapor_pressure_backend_status", "fallback"),
    ("vapor_pressure_backend_status", "builtin_authoritative"),
    ("vapor_pressure_backend_status", "openimcc"),
))
def test_hot_round_trip_keeps_consumer_field_contract(field, value) -> None:
    cell = EquilibrateCell(
        pot_id="text", engine="internal-analytical", temperature_K=1700.0,
        po2=Po2Request(mode="engine_default", po2_bar=None), status="ok",
        refusal_reason=None, engine_status="ok", engine_reason=None,
        melt_activities={}, gas_partial_pressures_Pa={}, liquid_fraction=1.0,
        wall_s=0.0, cpu_s=0.0, hostname="test",
    )
    setattr(cell, field, value)
    hot, provenance = engine_cache.pack_hot(cell.to_hot().hot)
    restored = EquilibrateCell.from_hot(engine_cache.unpack_hot(hot, provenance))
    expected = vapor_pressure_source_code(value) if field == "vapor_pressure_backend_status" else value
    assert getattr(restored, field) == expected
    if field == "vapor_pressures_source":
        assert b"engine_lineage" not in hot


def test_hot_cell_version_record_is_interned_once(tmp_path, monkeypatch) -> None:
    path = tmp_path / "versions.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    version = json.dumps({"source": "antoine_fallback", "version": "record" * 100})
    cell = EquilibrateCell(
        pot_id="version", engine="internal-analytical", temperature_K=1700.0,
        po2=Po2Request(mode="engine_default", po2_bar=None), status="ok",
        refusal_reason=None, engine_status="ok", engine_reason=None,
        melt_activities={}, gas_partial_pressures_Pa={}, liquid_fraction=1.0,
        wall_s=0.0, cpu_s=0.0, hostname="test",
        vapor_pressure_backend_status_reason=version,
    )
    legacy = cell.to_engine_cache_value()
    legacy[11][3] = "fallback"
    legacy_hot, _ = engine_cache.pack_hot(legacy)
    for temperature in (1700.0, 1800.0):
        engine_cache.cached_engine_call(
            cell.engine, {"temperature_K": temperature}, cell.to_hot
        )
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM provenance").fetchone()[0] == 1
        rows = connection.execute("SELECT value,provenance_id FROM engine_outputs").fetchall()
    assert len(rows) == 2
    assert rows[0] == rows[1]
    assert len(rows[0][0]) == len(legacy_hot) + 1
    assert version.encode() not in rows[0][0]


def _assert_cache_round_trip(cell: EquilibrateCell) -> None:
    encode = getattr(cell, "to_cache_payload", None)
    assert callable(encode), "EquilibrateCell must own its cache payload serializer"
    payload = encode()
    restored = EquilibrateCell.from_cache_payload(payload)
    assert restored == cell
    assert restored.to_cache_payload() == payload


@pytest.mark.parametrize("engine_name", _ENGINE_NAMES)
def test_cache_payload_round_trips_real_engine_cells(
    engine_name: str, request: pytest.FixtureRequest
) -> None:
    handle = open_battery_engine(engine_name)
    if not handle.available:
        reason = handle.unavailable_reason or "engine binary or package is absent"
        close_battery_engine_handle(handle)
        pytest.skip(f"{request.node.nodeid}: {engine_name} unavailable: {reason}")

    try:
        for pot in _REAL_POTS:
            cell = equilibrate_cell(
                handle,
                pot,
                temperature_K=1700.0,
                po2=Po2Request(mode="engine_default", po2_bar=None),
                timeout_s=30.0,
                isolated=False,
            )
            _assert_cache_round_trip(cell)

        if engine_name == "internal-analytical":
            refusal = equilibrate_cell(
                handle,
                _REAL_POTS[0],
                temperature_K=1700.0,
                po2=Po2Request(
                    mode="oxygen_balance_effusion",
                    po2_bar=None,
                    cell_material="invalid-cell-material",
                ),
                timeout_s=30.0,
                isolated=False,
            )
            assert refusal.status == "refusal"
            assert refusal.refusal_reason == "oxygen_balance_cell_material_invalid"
            _assert_cache_round_trip(refusal)
    finally:
        close_battery_engine_handle(handle)


def test_cache_payload_preserves_nested_types_and_presence() -> None:
    cell = EquilibrateCell(
        pot_id="typed-payload",
        engine="internal-analytical",
        temperature_K=1700.0,
        po2=Po2Request(mode="engine_default", po2_bar=None),
        status="refusal",
        refusal_reason="typed_test_refusal",
        engine_status="input_refusal",
        engine_reason="test refusal details",
        melt_activities={},
        gas_partial_pressures_Pa={},
        liquid_fraction=None,
        wall_s=0.0,
        cpu_s=0.0,
        hostname="test",
        arm=ARM_HEADLINE,
        notices=[
            {
                "kind": NoticeKind.INPUT_OMITTED,
                "decimal": Decimal("1.2300"),
                "tuple": ("component", None),
                "list": ["component", None],
                "signed_zero": -0.0,
                "present_none": None,
            }
        ],
        backend_failure_disposition="environment",
    )

    payload = cell.to_cache_payload()
    restored = EquilibrateCell.from_cache_payload(payload)
    worker_restored = EquilibrateCell.from_payload(cell.as_payload())

    assert restored == cell
    assert worker_restored.backend_failure_disposition == "environment"
    assert worker_restored.as_payload()["backend_failure_disposition"] == "environment"
    assert restored.to_cache_payload() == payload
    notice = restored.notices[0]
    assert notice["kind"] is NoticeKind.INPUT_OMITTED
    assert type(notice["decimal"]) is Decimal
    assert str(notice["decimal"]) == "1.2300"
    assert type(notice["tuple"]) is tuple
    assert notice["tuple"] == ("component", None)
    assert type(notice["list"]) is list
    assert notice["list"] == ["component", None]
    assert notice["signed_zero"].hex() == "-0x0.0p+0"
    assert "present_none" in notice and notice["present_none"] is None
    assert "absent" not in notice


def test_cache_payload_reads_cells_written_before_transient_field() -> None:
    cell = EquilibrateCell(
        pot_id="legacy-payload",
        engine="internal-analytical",
        temperature_K=1700.0,
        po2=Po2Request(mode="engine_default", po2_bar=None),
        status="ok",
        refusal_reason=None,
        engine_status="ok",
        engine_reason=None,
        melt_activities={},
        gas_partial_pressures_Pa={},
        liquid_fraction=None,
        wall_s=0.0,
        cpu_s=0.0,
        hostname="test",
    )
    payload = json.loads(cell.to_cache_payload())
    payload["value"][3] = [
        pair
        for pair in payload["value"][3]
        if pair[0] != "backend_failure_disposition"
    ]

    restored = EquilibrateCell.from_cache_payload(json.dumps(payload))

    assert restored.backend_failure_disposition is None
