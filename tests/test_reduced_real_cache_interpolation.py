from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

import simulator.reduced_real_cache_interpolation as rci
import simulator.reduced_real_determinism as rrd
from simulator.chemistry.kernel import ChemistryIntent
from simulator.melt_backend.base import EquilibriumResult
from simulator.reduced_real_determinism import (
    PT0DeterminismStore,
    canonical_json_bytes,
    canonical_physics_bucket_key_from_replay_key,
)
from tests.test_reduced_real_pt0_determinism import (
    _CountingSilicateEquilibriumProvider,
    _c3a_ladder_key,
    _lookup_c3a_payload,
    _put_c3a_payload,
    _silicate_equilibrium_key,
)


def _interpolation_key(
    label: str,
    *,
    feo_fraction: float,
    temperature_K: float,
    pO2_bar: float = 1.0e-6,
) -> dict:
    key = _c3a_ladder_key(
        label,
        feo_fraction=feo_fraction,
        temperature_K=temperature_K,
    )
    key["controls"]["pO2_bar"] = pO2_bar
    key["controls"]["pressure_bar"] = 0.01
    key["vapor_pressure_provider"] = {
        "resolved_provider_id": "builtin-vapor-pressure",
        "resolved_role": "authoritative",
        "authoritative_provider_id": "builtin-vapor-pressure",
        "fallback_provider_id": None,
        "fallback_allowed": False,
        "model": "BuiltinVaporPressureProvider",
        "mode": "BuiltinVaporPressureProvider",
        "engine_version": "test-v1",
    }
    return key


def _interpolation_payload(
    *,
    liquid_fraction: float,
    sio_pa: float,
    phases: list[str] | None = None,
    status: str = "ok",
) -> dict:
    return {
        "equilibrium_result": {
            "status": status,
            "phases_present": phases or ["liquid"],
            "phase_masses_kg": {"liquid": liquid_fraction, "solid": 1.0 - liquid_fraction},
            "liquid_fraction": liquid_fraction,
            "vapor_pressures_Pa": {"SiO": sio_pa, "O2": sio_pa * 0.1},
            "vapor_pressures_source": {"SiO": "exact", "O2": "exact"},
            "temperature_C": 1000.0,
            "pressure_bar": 0.01,
            "fO2_log": -9.0,
            "liquid_composition_wt_pct": {"SiO2": 45.0, "FeO": 10.0},
            "activity_coefficients": {"SiO2": 1.0},
            "warnings": [],
        }
    }


def _put_interpolation_row(
    db_path: Path,
    key: dict,
    *,
    liquid_fraction: float,
    sio_pa: float,
    phases: list[str] | None = None,
    status: str = "ok",
) -> None:
    payload = _interpolation_payload(
        liquid_fraction=liquid_fraction,
        sio_pa=sio_pa,
        phases=phases,
        status=status,
    )
    key_bytes = canonical_json_bytes(key)
    payload_bytes = canonical_json_bytes(payload)
    rrd.PT1PersistentEquilibriumStore(db_path).put(
        artifact=str(key["artifact"]),
        key=key,
        key_bytes=key_bytes,
        key_hash=hashlib.sha256(key_bytes).hexdigest(),
        payload=payload,
        payload_bytes=payload_bytes,
        payload_hash=hashlib.sha256(payload_bytes).hexdigest(),
    )


def _candidate(key: dict, payload: dict, *, label: str | None = None) -> dict:
    candidate = {
        "key": key,
        "key_hash": hashlib.sha256(canonical_json_bytes(key)).hexdigest(),
        "payload": payload,
    }
    if label is not None:
        candidate["label"] = label
    return candidate


def test_greedy_nn_prefers_along_trajectory_neighbors() -> None:
    query = _interpolation_key("query", feo_fraction=0.20, temperature_K=1500.0)
    same_comp_far = _interpolation_key("same-far", feo_fraction=0.20, temperature_K=1700.0)
    same_comp_near = _interpolation_key("same-near", feo_fraction=0.20, temperature_K=1510.0)
    diff_comp_near = _interpolation_key("diff-near", feo_fraction=0.21, temperature_K=1505.0)
    candidates = [
        _candidate(
            same_comp_far,
            _interpolation_payload(liquid_fraction=0.8, sio_pa=8.0),
            label="same-far",
        ),
        _candidate(
            diff_comp_near,
            _interpolation_payload(liquid_fraction=0.8, sio_pa=8.0),
            label="diff-near",
        ),
        _candidate(
            same_comp_near,
            _interpolation_payload(liquid_fraction=0.8, sio_pa=8.0),
            label="same-near",
        ),
    ]
    neighbors = rci.greedy_nearest_neighbors(
        query,
        candidates,
        k=2,
        max_distance=10.0,
    )
    assert [neighbor["label"] for neighbor in neighbors] == ["same-near", "same-far"]


def test_linear_interpolation_brackets_temperature() -> None:
    query = _interpolation_key("query", feo_fraction=0.20, temperature_K=1500.0)
    low = _interpolation_key("low", feo_fraction=0.20, temperature_K=1400.0)
    high = _interpolation_key("high", feo_fraction=0.20, temperature_K=1600.0)
    neighbors = [
        _candidate(low, _interpolation_payload(liquid_fraction=0.7, sio_pa=7.0)),
        _candidate(high, _interpolation_payload(liquid_fraction=0.9, sio_pa=9.0)),
    ]
    weights = rci.barycentric_interpolation_weights(query, neighbors)
    assert weights is not None
    assert weights["mode"] == "along_trajectory_T"
    assert weights["weights"] == pytest.approx([0.5, 0.5], abs=1.0e-9)
    payload = rci.interpolate_equilibrium_payload(
        query,
        neighbors,
        weights=weights["weights"],
    )
    assert payload["equilibrium_result"]["liquid_fraction"] == pytest.approx(0.8)
    assert payload["equilibrium_result"]["vapor_pressures_Pa"]["SiO"] == pytest.approx(8.0)


def test_validity_gate_refuses_phase_assemblage_mismatch() -> None:
    query = _interpolation_key("query", feo_fraction=0.20, temperature_K=1500.0)
    neighbors = [
        _candidate(
            _interpolation_key("a", feo_fraction=0.20, temperature_K=1400.0),
            _interpolation_payload(liquid_fraction=0.8, sio_pa=7.0, phases=["liquid"]),
        ),
        _candidate(
            _interpolation_key("b", feo_fraction=0.20, temperature_K=1600.0),
            _interpolation_payload(liquid_fraction=0.8, sio_pa=9.0, phases=["liquid", "solid"]),
        ),
    ]
    gate = rci.interpolation_validity_gate(query, neighbors)
    assert gate["accepted"] is False
    assert gate["refusal_reason"] == "phase_assemblage_mismatch"


def test_validity_gate_refuses_phase_boundary_proximity() -> None:
    query = _interpolation_key("query", feo_fraction=0.20, temperature_K=1500.0)
    neighbors = [
        _candidate(
            _interpolation_key("low", feo_fraction=0.20, temperature_K=1400.0),
            _interpolation_payload(liquid_fraction=0.01, sio_pa=7.0),
        ),
        _candidate(
            _interpolation_key("high", feo_fraction=0.20, temperature_K=1600.0),
            _interpolation_payload(liquid_fraction=0.8, sio_pa=9.0),
        ),
    ]
    gate = rci.interpolation_validity_gate(query, neighbors)
    assert gate["accepted"] is False
    assert gate["refusal_reason"] == "phase_boundary_proximity"


def test_validity_gate_refuses_solver_status_mismatch() -> None:
    query = _interpolation_key("query", feo_fraction=0.20, temperature_K=1500.0)
    neighbors = [
        _candidate(
            _interpolation_key("ok", feo_fraction=0.20, temperature_K=1400.0),
            _interpolation_payload(liquid_fraction=0.8, sio_pa=7.0, status="ok"),
        ),
        _candidate(
            _interpolation_key("bad", feo_fraction=0.20, temperature_K=1600.0),
            _interpolation_payload(liquid_fraction=0.8, sio_pa=9.0, status="not_converged"),
        ),
    ]
    gate = rci.interpolation_validity_gate(query, neighbors)
    assert gate["accepted"] is False
    assert gate["refusal_reason"] == "solver_status_mismatch"


def test_validity_gate_refuses_held_out_disagreement() -> None:
    query = _interpolation_key("query", feo_fraction=0.20, temperature_K=1500.0)
    neighbors = [
        _candidate(
            _interpolation_key("low", feo_fraction=0.20, temperature_K=1400.0),
            _interpolation_payload(liquid_fraction=0.5, sio_pa=1.0),
        ),
        _candidate(
            _interpolation_key("high", feo_fraction=0.20, temperature_K=1600.0),
            _interpolation_payload(liquid_fraction=0.5, sio_pa=100.0),
        ),
    ]
    gate = rci.interpolation_validity_gate(query, neighbors)
    assert gate["accepted"] is False
    assert gate["refusal_reason"] == "held_out_disagreement_exceeded"
    assert (
        gate["neighbor_disagreement"]["relative_error_max"]
        > rci.INTERPOLATION_HELD_OUT_DISAGREEMENT_THRESHOLD
    )


def test_validity_gate_refuses_po2_knee_region() -> None:
    query = _interpolation_key(
        "query",
        feo_fraction=0.20,
        temperature_K=1500.0,
        pO2_bar=1.0001e-9,
    )
    neighbors = [
        _candidate(
            _interpolation_key(
                "knee",
                feo_fraction=0.20,
                temperature_K=1400.0,
                pO2_bar=9.999e-10,
            ),
            _interpolation_payload(liquid_fraction=0.8, sio_pa=7.0),
        ),
        _candidate(
            _interpolation_key(
                "far",
                feo_fraction=0.20,
                temperature_K=1600.0,
                pO2_bar=1.0e-6,
            ),
            _interpolation_payload(liquid_fraction=0.8, sio_pa=9.0),
        ),
    ]
    gate = rci.interpolation_validity_gate(query, neighbors)
    assert gate["accepted"] is False
    assert gate["refusal_reason"] == "pO2_knee_crossing"


def test_anti_extrapolation_refuses_outside_hull() -> None:
    query = _interpolation_key("query", feo_fraction=0.20, temperature_K=1800.0)
    neighbors = [
        _candidate(
            _interpolation_key("low", feo_fraction=0.20, temperature_K=1400.0),
            _interpolation_payload(liquid_fraction=0.8, sio_pa=7.0),
        ),
        _candidate(
            _interpolation_key("high", feo_fraction=0.20, temperature_K=1600.0),
            _interpolation_payload(liquid_fraction=0.8, sio_pa=9.0),
        ),
    ]
    assert rci.barycentric_interpolation_weights(query, neighbors) is None


def test_lookup_optional_returns_cached_interpolated(tmp_path: Path) -> None:
    query = _interpolation_key("query", feo_fraction=0.20, temperature_K=1500.0)
    low = _interpolation_key("low", feo_fraction=0.20, temperature_K=1490.0)
    high = _interpolation_key("high", feo_fraction=0.20, temperature_K=1510.0)
    db_path = tmp_path / "interpolation.sqlite"
    _put_interpolation_row(db_path, low, liquid_fraction=0.7, sio_pa=7.0)
    _put_interpolation_row(db_path, high, liquid_fraction=0.71, sio_pa=7.05)

    store = PT0DeterminismStore("capture", db_path=db_path)
    payload = store._lookup_optional(
        str(query["artifact"]),
        query,
        physics_bucket_key=canonical_physics_bucket_key_from_replay_key(query),
    )

    assert payload is not None
    assert store.last_cache_state == "cached_interpolated"
    assert store.replay_sequence[-1]["cache_state"] == "cached_interpolated"
    assert payload["equilibrium_result"]["liquid_fraction"] == pytest.approx(0.705)
    assert payload["equilibrium_result"]["vapor_pressures_Pa"]["SiO"] == pytest.approx(7.025)
    assert "cached_interpolated_linear_estimate" in payload["equilibrium_result"]["warnings"]


def test_lookup_optional_accepts_fully_liquid_single_phase_rows(tmp_path: Path) -> None:
    query = _interpolation_key("query", feo_fraction=0.20, temperature_K=1500.0)
    low = _interpolation_key("low", feo_fraction=0.20, temperature_K=1490.0)
    high = _interpolation_key("high", feo_fraction=0.20, temperature_K=1510.0)
    db_path = tmp_path / "fully-liquid-interpolation.sqlite"
    _put_interpolation_row(db_path, low, liquid_fraction=1.0, sio_pa=7.0)
    _put_interpolation_row(db_path, high, liquid_fraction=1.0, sio_pa=7.05)

    store = PT0DeterminismStore("capture", db_path=db_path)
    payload = store._lookup_optional(
        str(query["artifact"]),
        query,
        physics_bucket_key=canonical_physics_bucket_key_from_replay_key(query),
    )

    assert payload is not None
    assert store.last_cache_state == "cached_interpolated"
    assert payload["equilibrium_result"]["liquid_fraction"] == pytest.approx(1.0)
    assert "cached_interpolated_linear_estimate" in payload["equilibrium_result"]["warnings"]


def test_cached_interpolated_replay_demotes_backend_authority() -> None:
    store = PT0DeterminismStore("capture")
    store.last_cache_state = "cached_interpolated"
    sim = SimpleNamespace(
        _backend_authoritative=True,
        _last_backend_diagnostics={},
        _last_vapor_pressures_source={},
        _last_vapor_pressure_diagnostic={},
        _last_sulfur_saturation_result=None,
        _backend_status_history=[],
        melt=SimpleNamespace(fO2_log=-9.0),
    )

    result = store._equilibrium_from_payload(
        sim,
        _interpolation_payload(liquid_fraction=0.7, sio_pa=7.0),
    )

    assert sim._backend_authoritative is False
    assert sim._last_backend_diagnostics["reduced_real_cache_state"] == "cached_interpolated"
    assert result.diagnostics["reduced_real_cache_authoritative"] is False


def test_additive_exact_and_rung_paths_remain_intact(tmp_path: Path) -> None:
    query_key = _c3a_ladder_key(
        "query",
        feo_fraction=0.123446,
        temperature_K=1234.46,
    )
    h40_key = _c3a_ladder_key(
        "h40-row",
        feo_fraction=0.123444,
        temperature_K=1234.44,
    )
    db_path = tmp_path / "additive.sqlite"
    _put_c3a_payload(db_path, h40_key, "h40")

    payload, store = _lookup_c3a_payload(db_path, query_key)
    assert payload["label"] == "h40"
    assert store.replay_sequence[-1]["cache_state"] == "cached_physics_bucket"
    assert store.replay_sequence[-1]["physics_bucket_rung"] == "h40"


def test_near_phase_boundary_query_is_refused_not_interpolated(tmp_path: Path) -> None:
    query = _interpolation_key("query", feo_fraction=0.20, temperature_K=1500.0)
    low = _interpolation_key("low", feo_fraction=0.20, temperature_K=1400.0)
    high = _interpolation_key("high", feo_fraction=0.20, temperature_K=1600.0)
    db_path = tmp_path / "boundary.sqlite"
    _put_interpolation_row(db_path, low, liquid_fraction=0.01, sio_pa=7.0)
    _put_interpolation_row(db_path, high, liquid_fraction=0.8, sio_pa=9.0)

    store = PT0DeterminismStore("capture", db_path=db_path)
    payload = store._lookup_optional(
        str(query["artifact"]),
        query,
        physics_bucket_key=canonical_physics_bucket_key_from_replay_key(query),
    )

    assert payload is None
    assert store.replay_sequence == []
