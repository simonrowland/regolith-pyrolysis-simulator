from __future__ import annotations

import copy
import hashlib
import sqlite3
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
    result = EquilibriumResult(
        status=status,
        phases_present=phases or ["liquid"],
        phase_masses_kg={"liquid": liquid_fraction, "solid": 1.0 - liquid_fraction},
        liquid_fraction=liquid_fraction,
        vapor_pressures_Pa={"SiO": sio_pa, "O2": sio_pa * 0.1},
        vapor_pressures_source={"SiO": "exact", "O2": "exact"},
        temperature_C=1000.0,
        pressure_bar=0.01,
        fO2_log=-9.0,
        liquid_composition_wt_pct={"SiO2": 45.0, "FeO": 10.0},
        activity_coefficients={"SiO2": 1.0},
    )
    result_payload = {
        field.name: rrd._json_ready(getattr(result, field.name))
        for field in rrd._equilibrium_result_payload_fields()
        if not (
            field.name in rrd._EQUILIBRIUM_RESULT_FIELDS_OMITTED_WHEN_NONE
            and getattr(result, field.name) is None
        )
    }
    payload = {
        "authority": {
            "schema": "reduced-real-authority-v1",
            "evidence_class": "melts",
            "backend_family": "alphamelts",
            "backend": {
                "backend_name": "AlphaMELTSBackend",
                "backend_class": "simulator.melt_backend.alphamelts.AlphaMELTSBackend",
            },
            "provider": rrd._provider_authority_payload(
                resolved_provider_id="alphamelts-diagnostic",
                resolved_role="authoritative",
                authoritative_provider_id="alphamelts-diagnostic",
                fallback_provider_id=None,
                fallback_allowed=False,
                model="MELTSv1.0.2",
                mode="subprocess",
            ),
            "vapor_pressure": rrd._provider_authority_payload(
                resolved_provider_id="builtin-vapor-pressure",
                resolved_role="authoritative",
                authoritative_provider_id="builtin-vapor-pressure",
                fallback_provider_id=None,
                fallback_allowed=False,
                model="BuiltinVaporPressureProvider",
                mode="BuiltinVaporPressureProvider",
            ),
        },
        "equilibrium_result": result_payload,
    }
    payload[rrd._EQUILIBRIUM_REPLAY_SIDE_FIELDS[0]] = dict(
        result_payload["vapor_pressures_source"]
    )
    payload[rrd._EQUILIBRIUM_REPLAY_SIDE_FIELDS[1]] = {}
    return payload


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
        artifact="equilibrium_post_record",
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


def test_interpolation_refuses_dependency_vector_mismatch() -> None:
    query = _interpolation_key("query", feo_fraction=0.20, temperature_K=1500.0)
    candidate_key = copy.deepcopy(query)
    candidate_key["model"]["model"] = "other-binding"
    candidate = _candidate(
        candidate_key,
        _interpolation_payload(liquid_fraction=0.7, sio_pa=7.0),
    )

    assert rci.greedy_nearest_neighbors(query, [candidate]) == []


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


def test_interpolation_reports_weighted_neighbor_operating_point() -> None:
    query = _interpolation_key("query", feo_fraction=0.20, temperature_K=1500.0)
    query["controls"]["pressure_bar"] = 0.01
    query["controls"]["log_fO2"] = -12.0
    low_payload = _interpolation_payload(liquid_fraction=0.7, sio_pa=7.0)
    high_payload = _interpolation_payload(liquid_fraction=0.9, sio_pa=9.0)
    low_payload["equilibrium_result"]["temperature_C"] = 900.0
    low_payload["equilibrium_result"]["pressure_bar"] = 0.5
    low_payload["equilibrium_result"]["fO2_log"] = -8.0
    high_payload["equilibrium_result"]["temperature_C"] = 1100.0
    high_payload["equilibrium_result"]["pressure_bar"] = 1.5
    high_payload["equilibrium_result"]["fO2_log"] = -6.0
    neighbors = [
        _candidate(
            _interpolation_key("low", feo_fraction=0.20, temperature_K=1400.0),
            low_payload,
        ),
        _candidate(
            _interpolation_key("high", feo_fraction=0.20, temperature_K=1600.0),
            high_payload,
        ),
    ]

    payload = rci.interpolate_equilibrium_payload(
        query,
        neighbors,
        weights=[0.5, 0.5],
    )
    result = payload["equilibrium_result"]

    assert result["temperature_C"] == pytest.approx(1000.0)
    assert result["pressure_bar"] == pytest.approx(1.0)
    assert result["fO2_log"] == pytest.approx(-7.0)
    assert result["status"] == "out_of_domain"
    assert result["diagnostics"]["operating_point_clamped"] is True
    assert result["diagnostics"]["requested_fO2_log"] == -12.0
    assert result["diagnostics"]["solved_fO2_log"] == pytest.approx(-7.0)
    assert (
        result["diagnostics"]["authoritative_for_requested_conditions"]
        is False
    )


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
        "equilibrium_post_record",
        query,
        physics_bucket_key=canonical_physics_bucket_key_from_replay_key(query),
    )

    assert payload is not None
    assert store.hits == 1
    assert store.last_cache_state == "cached_interpolated"
    assert store.replay_sequence[-1]["cache_state"] == "cached_interpolated"
    uncertainty = store.replay_sequence[-1]["interpolation_uncertainty"]
    assert uncertainty["dtype"] == "float64"
    assert uncertainty["vector"]["cache_distance"] > 0.0
    payload_bytes = canonical_json_bytes(payload)
    assert b"interpolation_uncertainty" in canonical_json_bytes(store.replay_sequence[-1])
    assert b"interpolation_uncertainty" not in payload_bytes
    without_diagnostics = rci.interpolate_equilibrium_payload(
        query,
        [
            _candidate(low, _interpolation_payload(liquid_fraction=0.7, sio_pa=7.0)),
            _candidate(high, _interpolation_payload(liquid_fraction=0.71, sio_pa=7.05)),
        ],
        weights=[0.5, 0.5],
    )
    # The stored lookup adds the actual neighbor key hashes to its
    # served_from provenance. The direct producer call has no stored keys, so
    # compare the synthesized query result and its other projection separately.
    assert canonical_json_bytes(payload["equilibrium_result"]) == (
        canonical_json_bytes(without_diagnostics["equilibrium_result"])
    )
    assert payload["last_vapor_pressures_source"] == (
        without_diagnostics["last_vapor_pressures_source"]
    )
    served_from = payload["last_vapor_pressure_diagnostic"]["served_from"]
    assert served_from["source"] == "cached_interpolated"
    assert len(served_from["interpolation_neighbor_key_hashes"]) == 2
    assert all(
        len(key_hash) == 64
        for key_hash in served_from["interpolation_neighbor_key_hashes"]
    )
    summary = store.summary()
    assert summary["interpolation_uncertainty_points"] == 1
    assert summary["interpolation_uncertainty_ranked_table_drain"]["selected"]
    assert payload["equilibrium_result"]["liquid_fraction"] == pytest.approx(0.705)
    assert payload["equilibrium_result"]["vapor_pressures_Pa"]["SiO"] == pytest.approx(7.025)
    assert "cached_interpolated_linear_estimate" in payload["equilibrium_result"]["warnings"]
    assert "requested_fO2_log" not in payload["equilibrium_result"]
    assert "printed_fO2_echo_log" not in payload["equilibrium_result"]
    # The expected bytes intentionally changed when interpolation stopped
    # copying neighbor engine rows. Pin the query-owned shape: only the
    # weighted fields and explicit interpolation disclosures survive, while
    # requested_temperature_C belongs to this query.
    result = payload["equilibrium_result"]
    assert result["requested_temperature_C"] == pytest.approx(
        query["controls"]["T_K"] - 273.15
    )
    assert result["phase_assemblage_available"] is False
    assert result["status"] == "out_of_domain"
    assert not {
        "phases_present",
        "phase_species_mol",
        "phase_species_kg",
        "phase_compositions",
        "phase_thermo",
        "phase_instances",
        "system_enthalpy",
        "system_entropy",
        "system_volume",
        "solid_composition_wt_pct",
        "bulk_composition_wt_pct",
        "chem_potentials",
        "phase_affinities",
        "liquid_viscosity_Pa_s",
        "liquid_density_kg_m3",
        "liquidus_T_C",
        "sulfur_saturation",
        "ledger_transition",
    } & set(result)


def test_interpolation_strips_neighbor_engine_rows_and_uses_query_temperature() -> None:
    query = _interpolation_key("query", feo_fraction=0.20, temperature_K=1500.0)
    low = _interpolation_key("low", feo_fraction=0.20, temperature_K=1490.0)
    high = _interpolation_key("high", feo_fraction=0.20, temperature_K=1510.0)
    neighbor_only = {
        "phases_present": ["neighbor-solid"],
        "phase_species_mol": {"neighbor-phase": {"FeO": 3.0}},
        "phase_species_kg": {"neighbor-phase": {"FeO": 2.0}},
        "phase_compositions": {"neighbor-phase": {"FeO": 90.0}},
        "liquid_viscosity_Pa_s": 7.0,
        "ledger_transition": {"neighbor": "transition"},
        "sulfur_saturation": {"neighbor": True},
        "liquidus_T_C": 1300.0,
        "liquid_density_kg_m3": 2700.0,
        "system_enthalpy": 1.0,
        "system_entropy": 2.0,
        "system_volume": 3.0,
        "system_heat_capacity_Cp": 4.0,
        "system_dVdP": 5.0,
        "system_dVdT": 6.0,
        "system_fO2_delta_QFM": 7.0,
        "system_solid_density_rhos": 8.0,
        "system_phi": 9.0,
        "system_chisqr": 10.0,
        "phase_thermo": {"neighbor-phase": {"gibbs": 11.0}},
        "solid_composition_wt_pct": {"FeO": 12.0},
        "bulk_composition_wt_pct": {"FeO": 13.0},
        "chem_potentials": {"FeO": {"neighbor": 14.0}},
        "phase_affinities": {"FeO": {"neighbor": 15.0}},
        "phase_instances": [{"name": "neighbor-phase"}],
        "neighbor_engine_extra": {"value": "not-query-evidence"},
    }
    neighbors = []
    for key, liquid_fraction, sio_pa in (
        (low, 0.7, 7.0),
        (high, 0.71, 7.05),
    ):
        payload = _interpolation_payload(
            liquid_fraction=liquid_fraction,
            sio_pa=sio_pa,
        )
        result = payload["equilibrium_result"]
        result.update(copy.deepcopy(neighbor_only))
        result.update({
            "phase_assemblage_available": True,
            "status": "neighbor-status",
            "requested_temperature_C": 999.0,
            "diagnostics": {
                "engine_status": "neighbor-only",
                "source_reaction_fO2_bar": 1.0e-6,
            },
            "temperature_C": key["controls"]["T_K"] - 273.15,
            "pressure_bar": query["controls"]["pressure_bar"],
            "fO2_log": -9.0,
        })
        neighbors.append(_candidate(key, payload))

    payload = rci.interpolate_equilibrium_payload(
        query,
        neighbors,
        weights=[0.5, 0.5],
    )
    result = payload["equilibrium_result"]

    # Interpolation byte identity changes here intentionally. These neighbor
    # engine fields are removed because they describe another state point:
    # phase assemblage/masses/composition/thermo/instances, system
    # thermodynamics, chemical potentials/affinities, liquid properties,
    # ledger transition, sulfur saturation, and unrecognized engine extras.
    # The neighbor's requested_temperature_C is also replaced with this
    # query's coordinate; none of those fields is valid query evidence.
    assert set(result).isdisjoint(neighbor_only)
    assert result["requested_temperature_C"] != 999.0
    assert set(result) == {
        "diagnostics",
        "phase_assemblage_available",
        "status",
        "temperature_C",
        "requested_temperature_C",
        "pressure_bar",
        "fO2_log",
        "liquid_fraction",
        "vapor_pressures_Pa",
        "phase_masses_kg",
        "liquid_composition_wt_pct",
        "activity_coefficients",
        "vapor_pressures_source",
        "warnings",
    }
    assert result["requested_temperature_C"] == pytest.approx(
        query["controls"]["T_K"] - 273.15
    )
    assert result["temperature_C"] == pytest.approx(
        query["controls"]["T_K"] - 273.15
    )
    assert result["phase_assemblage_available"] is False
    assert result["status"] == "ok"
    assert result["diagnostics"] == {}
    assert result["warnings"] == ["cached_interpolated_linear_estimate"]
    assert result["liquid_fraction"] == pytest.approx(0.705)
    assert result["phase_masses_kg"] == pytest.approx(
        {"liquid": 0.705, "solid": 0.295}
    )
    assert result["vapor_pressures_Pa"]["SiO"] == pytest.approx(7.025)
    assert result["liquid_composition_wt_pct"] == {
        "SiO2": 45.0,
        "FeO": 10.0,
    }


def test_obsolete_exact_row_does_not_hide_current_physics_bucket_row(
    tmp_path: Path,
) -> None:
    query = _interpolation_key("query", feo_fraction=0.20, temperature_K=1500.0)
    bucket = _interpolation_key(
        "bucket", feo_fraction=0.20, temperature_K=1500.01,
    )
    db_path = tmp_path / "obsolete-exact-with-current-bucket.sqlite"
    old_payload = _interpolation_payload(liquid_fraction=0.4, sio_pa=5.0)
    old_payload["equilibrium_result"]["diagnostics"] = {
        "a_FeO_calphad": {
            "notices": [{
                "kind": "liquidus_unknown",
                "authority": "unknown",
                "source": "none:liquidus_gate_not_established",
            }],
        },
    }
    key_bytes = canonical_json_bytes(query)
    rrd.PT1PersistentEquilibriumStore(db_path).put(
        artifact="equilibrium_post_record",
        key=query,
        key_bytes=key_bytes,
        key_hash=hashlib.sha256(key_bytes).hexdigest(),
        payload=old_payload,
        payload_bytes=canonical_json_bytes(old_payload),
        payload_hash=hashlib.sha256(
            canonical_json_bytes(old_payload)
        ).hexdigest(),
    )
    _put_interpolation_row(
        db_path,
        bucket,
        liquid_fraction=0.7,
        sio_pa=7.0,
    )

    store = PT0DeterminismStore("capture", db_path=db_path)
    payload = store._lookup_optional(
        "equilibrium_post_record",
        query,
        physics_bucket_key=canonical_physics_bucket_key_from_replay_key(query),
    )

    assert payload is not None
    assert payload["equilibrium_result"]["liquid_fraction"] == 0.7
    assert store.replay_sequence[-1]["cache_state"] == "cached_physics_bucket"
    assert store.replay_sequence[-1]["physics_bucket_rung"] == "h40"


def test_cached_interpolation_uses_query_fo2_and_drops_neighbor_echo(
    tmp_path: Path,
) -> None:
    query = _interpolation_key("query", feo_fraction=0.20, temperature_K=1500.0)
    query["controls"]["log_fO2"] = -9.0
    low = _interpolation_key("low", feo_fraction=0.20, temperature_K=1499.0)
    low["controls"]["log_fO2"] = -9.0001
    high = _interpolation_key("high", feo_fraction=0.20, temperature_K=1501.0)
    high["controls"]["log_fO2"] = -8.9999

    low_payload = _interpolation_payload(liquid_fraction=0.7, sio_pa=7.0)
    low_payload["equilibrium_result"].update(
        requested_fO2_log=-9.0001,
        printed_fO2_echo_log=-9.0,
        diagnostics={
            "requested_fO2_log": -9.0001,
            "printed_fO2_echo_log": -9.0,
            "source_reaction_fO2_bar": 1.0e-6,
            "activities_provider": "neighbor-engine",
        },
    )
    high_payload = _interpolation_payload(liquid_fraction=0.71, sio_pa=7.05)
    high_payload["equilibrium_result"].update(
        requested_fO2_log=-8.9999,
        printed_fO2_echo_log=-9.0,
        diagnostics={
            "requested_fO2_log": -8.9999,
            "printed_fO2_echo_log": -9.0,
            "source_reaction_fO2_bar": 1.0e-6,
            "activities_provider": "neighbor-engine",
        },
    )

    attempt = rci.attempt_cached_interpolation(
        query,
        [_candidate(low, low_payload), _candidate(high, high_payload)],
    )

    assert attempt is not None
    assert attempt["weight_info"]["weights"] == pytest.approx([0.5, 0.5])
    interpolated_result = attempt["payload"]["equilibrium_result"]
    assert interpolated_result["fO2_log"] == pytest.approx(-9.0)
    assert interpolated_result["requested_fO2_log"] == pytest.approx(-9.0)
    assert interpolated_result["printed_fO2_echo_log"] is None
    assert interpolated_result["diagnostics"]["requested_fO2_log"] == pytest.approx(-9.0)
    assert interpolated_result["diagnostics"]["printed_fO2_echo_log"] is None
    assert "source_reaction_fO2_bar" not in interpolated_result["diagnostics"]
    assert "activities_provider" not in interpolated_result["diagnostics"]
    assert attempt["payload"]["last_vapor_pressure_diagnostic"] == {
        "source": "served_not_executed",
        "served_from": {
            "source": "cached_interpolated",
            "interpolation_mode": "linear",
            "interpolation_neighbor_key_hashes": [
                str(neighbor.get("key_hash", ""))
                for neighbor in attempt["neighbors"]
            ],
        },
    }

    store = PT0DeterminismStore(
        "capture",
        db_path=tmp_path / "interpolated-requested-fo2.sqlite",
    )
    store.last_cache_state = "cached_interpolated"
    sim = SimpleNamespace(
        melt=SimpleNamespace(
            fO2_log=-8.0,
            melt_fO2_log=-8.0,
        ),
    )
    replayed = store._equilibrium_from_payload(sim, attempt["payload"])

    assert replayed.fO2_log == pytest.approx(-9.0)
    assert replayed.requested_fO2_log == pytest.approx(-9.0)
    assert replayed.printed_fO2_echo_log is None
    assert replayed.diagnostics["requested_fO2_log"] == pytest.approx(-9.0)
    assert replayed.diagnostics["printed_fO2_echo_log"] is None
    assert sim.melt.fO2_log == pytest.approx(-9.0)
    assert sim.melt.melt_fO2_log == pytest.approx(-9.0)


@pytest.mark.parametrize(
    ("backend_family", "backend_name", "backend_class", "evidence_class", "backend_diagnostics"),
    [
        (
            "thermoengine",
            "ThermoEngineBackend",
            "simulator.melt_backend.thermoengine.ThermoEngineBackend",
            "melts",
            {"fO2_transport": "thermoengine_oxygen_root"},
        ),
        (
            "magemin",
            "MAGEMinBackend",
            "simulator.melt_backend.magemin.MAGEMinBackend",
            "magemin",
            {
                "applied_fO2_buffer": "mw",
                "applied_fO2_buffer_n": 0.0,
                "fO2_clamped": False,
                "authoritative_for_requested_conditions": True,
                "authoritative_for_solved_conditions": True,
            },
        ),
    ],
)
def test_cached_interpolation_preserves_legacy_engine_fo2_diagnostics(
    tmp_path: Path,
    backend_family: str,
    backend_name: str,
    backend_class: str,
    evidence_class: str,
    backend_diagnostics: dict[str, object],
) -> None:
    query = _interpolation_key("query", feo_fraction=0.20, temperature_K=1500.0)
    query["controls"]["log_fO2"] = -9.0
    low = _interpolation_key("low", feo_fraction=0.20, temperature_K=1499.0)
    low["controls"]["log_fO2"] = -9.0001
    high = _interpolation_key("high", feo_fraction=0.20, temperature_K=1501.0)
    high["controls"]["log_fO2"] = -8.9999
    backend = {
        "backend_name": backend_name,
        "backend_class": backend_class,
        "backend_version": "test-v1",
    }
    for key in (query, low, high):
        key["backend"] = copy.deepcopy(backend)

    candidates = []
    for key, requested_fO2_log in ((low, -9.0001), (high, -8.9999)):
        payload = _interpolation_payload(liquid_fraction=0.7, sio_pa=7.0)
        payload["authority"]["backend_family"] = backend_family
        payload["authority"]["backend"] = copy.deepcopy(backend)
        payload["authority"]["evidence_class"] = evidence_class
        result = payload["equilibrium_result"]
        result["warnings"] = [f"neighbor-only:{requested_fO2_log}"]
        result["temperature_C"] = key["controls"]["T_K"] - 273.15
        result["fO2_log"] = -9.0000000005
        result["diagnostics"] = {
            **backend_diagnostics,
            "requested_fO2_log": requested_fO2_log,
            "solved_fO2_log": -9.0000000005,
        }
        candidates.append(_candidate(key, payload))

    attempt = rci.attempt_cached_interpolation(query, candidates)

    assert attempt is not None
    assert attempt["weight_info"]["mode"] == "along_trajectory_T"
    assert attempt["weight_info"]["weights"] == pytest.approx([0.5, 0.5])
    interpolated_result = attempt["payload"]["equilibrium_result"]
    assert interpolated_result["fO2_log"] == -9.0000000005
    assert "requested_fO2_log" not in interpolated_result
    assert "printed_fO2_echo_log" not in interpolated_result
    assert interpolated_result["diagnostics"] == {
        "requested_fO2_log": -9.0,
        "printed_fO2_echo_log": None,
    }
    # These backends store their requested coordinate in legacy diagnostics,
    # not the AlphaMELTS top-level fields. The producer must carry that
    # query-coordinate request forward while removing the neighbor's solve
    # diagnostics and withholding an engine-printed echo.
    assert "applied_fO2_buffer" not in interpolated_result["diagnostics"]
    assert "solved_fO2_log" not in interpolated_result["diagnostics"]
    assert interpolated_result["warnings"] == [
        "cached_interpolated_linear_estimate",
    ]

    store = PT0DeterminismStore(
        "capture",
        db_path=tmp_path / f"interpolated-{backend_family}-fo2.sqlite",
    )
    store.last_cache_state = "cached_interpolated"
    sim = SimpleNamespace(
        melt=SimpleNamespace(
            fO2_log=-8.0,
            melt_fO2_log=-8.0,
        ),
    )
    replayed = store._equilibrium_from_payload(sim, attempt["payload"])

    assert replayed.requested_fO2_log is None
    assert replayed.fO2_log == interpolated_result["fO2_log"]
    assert sim.melt.fO2_log == interpolated_result["fO2_log"]
    assert sim.melt.melt_fO2_log == interpolated_result["fO2_log"]
    assert sim.melt.fO2_log != query["controls"]["log_fO2"]


def test_interpolation_reemits_union_from_every_used_neighbor(tmp_path: Path) -> None:
    query = _interpolation_key("query", feo_fraction=0.20, temperature_K=1500.0)
    low = _interpolation_key("low", feo_fraction=0.20, temperature_K=1490.0)
    high = _interpolation_key("high", feo_fraction=0.20, temperature_K=1510.0)
    db_path = tmp_path / "interpolation-repair-notices.sqlite"
    notices_by_hash = {
        hashlib.sha256(canonical_json_bytes(low)).hexdigest(): {
            "type": "typed_notice",
            "kind": "freeze_gate_curve_point_repaired",
            "site": "SR18",
            "temperature_C": 1490.0,
            "raw_fraction": -0.2,
            "action": "clamp_fraction",
        },
        hashlib.sha256(canonical_json_bytes(high)).hexdigest(): {
            "type": "typed_notice",
            "kind": "freeze_gate_curve_source_replaced",
            "site": "SR19",
            "failed_source": "gate_liquid_fraction",
            "replacement_source": "liquidus_solidus:backend",
        },
    }
    _put_interpolation_row(db_path, low, liquid_fraction=0.7, sio_pa=7.0)
    _put_interpolation_row(db_path, high, liquid_fraction=0.71, sio_pa=7.05)
    with sqlite3.connect(db_path) as conn:
        for key_hash, notice in notices_by_hash.items():
            conn.execute(
                f"""
                UPDATE {rrd.PT1_EQUILIBRIUM_TABLE}
                SET repair_notices_json = ?
                WHERE key_hash = ?
                """,
                (canonical_json_bytes([notice]).decode("utf-8"), key_hash),
            )

    store = PT0DeterminismStore("capture", db_path=db_path)
    candidates = store.persistent_store.list_interpolation_candidates(
        artifact="equilibrium_post_record",
        replay_scope_sha256=rci.replay_scope_for_interpolation(query),
    )
    attempt = rci.attempt_cached_interpolation(query, candidates)
    assert attempt is not None
    expected_union = [
        notices_by_hash[str(neighbor["key_hash"])]
        for neighbor in attempt["neighbors"]
    ]
    replay_sim = SimpleNamespace()
    assert store._lookup_optional(
        "equilibrium_post_record",
        query,
        physics_bucket_key=canonical_physics_bucket_key_from_replay_key(query),
        sim=replay_sim,
    ) is not None

    assert replay_sim._reduced_real_repair_notices == [
        *expected_union,
        {
            "kind": "reduced_real_cache_approximation",
            "cache_state": "cached_interpolated",
        },
    ]


def test_interpolation_candidates_refuse_corrupt_replay_scope(
    tmp_path: Path,
) -> None:
    key = _interpolation_key("stored", feo_fraction=0.20, temperature_K=1500.0)
    db_path = tmp_path / "corrupt-scope.sqlite"
    _put_interpolation_row(db_path, key, liquid_fraction=0.7, sio_pa=7.0)
    corrupt_scope = "0" * 64

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"UPDATE {rrd.PT1_EQUILIBRIUM_TABLE} "
            "SET replay_scope_sha256 = ?",
            (corrupt_scope,),
        )

    store = rrd.PT1PersistentEquilibriumStore(db_path)
    with pytest.raises(
        rrd.PT1PersistentStoreCorrupt,
        match="exact key replay scope mismatch",
    ):
        store.list_interpolation_candidates(
            artifact="equilibrium_post_record",
            replay_scope_sha256=corrupt_scope,
        )


def test_interpolation_refuses_attached_base_dependency_vector_mismatch(
    tmp_path: Path,
) -> None:
    query = _interpolation_key("query", feo_fraction=0.20, temperature_K=1500.0)
    low = _interpolation_key("low", feo_fraction=0.20, temperature_K=1490.0)
    high = _interpolation_key("high", feo_fraction=0.20, temperature_K=1510.0)
    high["model"]["model"] = "other-binding"
    local_db = tmp_path / "local-binding.sqlite"
    base_db = tmp_path / "attached-other-binding.sqlite"
    _put_interpolation_row(local_db, low, liquid_fraction=0.7, sio_pa=7.0)
    _put_interpolation_row(base_db, high, liquid_fraction=0.71, sio_pa=7.05)

    with sqlite3.connect(base_db) as conn:
        stored_scope = conn.execute(
            f"SELECT replay_scope_sha256 FROM {rrd.PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()[0]
    assert stored_scope == rci.replay_scope_for_interpolation(high)
    assert stored_scope != rci.replay_scope_for_interpolation(query)

    store = PT0DeterminismStore(
        "capture",
        db_path=local_db,
        read_only_base_db_path=base_db,
    )
    payload = store._lookup_optional(
        "equilibrium_post_record",
        query,
        physics_bucket_key=canonical_physics_bucket_key_from_replay_key(query),
    )

    assert payload is None
    assert store.hits == 0


def test_interpolation_unions_known_notice_with_legacy_attached_base(
    tmp_path: Path,
) -> None:
    query = _interpolation_key("query", feo_fraction=0.20, temperature_K=1500.0)
    low = _interpolation_key("low", feo_fraction=0.20, temperature_K=1490.0)
    high = _interpolation_key("high", feo_fraction=0.20, temperature_K=1510.0)
    target_db = tmp_path / "local.sqlite"
    base_db = tmp_path / "legacy-base.sqlite"
    notice = {"kind": "local_known_repair"}
    _put_interpolation_row(target_db, low, liquid_fraction=0.7, sio_pa=7.0)
    _put_interpolation_row(base_db, high, liquid_fraction=0.71, sio_pa=7.05)
    with sqlite3.connect(target_db) as conn:
        conn.execute(
            f"UPDATE {rrd.PT1_EQUILIBRIUM_TABLE} SET repair_notices_json = ?",
            (canonical_json_bytes([notice]).decode("utf-8"),),
        )
    with sqlite3.connect(base_db) as conn:
        conn.execute(
            f"ALTER TABLE {rrd.PT1_EQUILIBRIUM_TABLE} "
            "DROP COLUMN repair_notices_json"
        )
        conn.execute(
            f"UPDATE {rrd.PT1_EQUILIBRIUM_TABLE} "
            "SET store_schema_version = 'pt1-reduced-real-equilibrium-store-v2'"
        )
        conn.execute(
            f"UPDATE {rrd.PT1_METADATA_TABLE} "
            "SET value = 'pt1-reduced-real-equilibrium-store-v2' "
            "WHERE key = 'store_schema_version'"
        )

    store = PT0DeterminismStore(
        "capture",
        db_path=target_db,
        read_only_base_db_path=base_db,
    )
    replay_sim = SimpleNamespace()
    assert store._lookup_optional(
        "equilibrium_post_record",
        query,
        physics_bucket_key=canonical_physics_bucket_key_from_replay_key(query),
        sim=replay_sim,
    ) is not None

    assert replay_sim._reduced_real_repair_notices == [
        notice,
        {"kind": "repair_notices_unavailable_for_legacy_row"},
        {
            "kind": "reduced_real_cache_approximation",
            "cache_state": "cached_interpolated",
        },
    ]


def test_cached_interpolated_eval_trace_emits_feasibility_verdict(tmp_path: Path) -> None:
    from simulator.optimize.evaluate import _trace_payload_with_interpolation_feasibility
    from simulator.optimize.physics import GateMargin, ThresholdSpec

    query = _interpolation_key("query", feo_fraction=0.20, temperature_K=1500.0)
    low = _interpolation_key("low", feo_fraction=0.20, temperature_K=1490.0)
    high = _interpolation_key("high", feo_fraction=0.20, temperature_K=1510.0)
    db_path = tmp_path / "trace-verdict.sqlite"
    _put_interpolation_row(db_path, low, liquid_fraction=0.7, sio_pa=7.0)
    _put_interpolation_row(db_path, high, liquid_fraction=0.71, sio_pa=7.05)

    store = PT0DeterminismStore("capture", db_path=db_path)
    assert store._lookup_optional(
        "equilibrium_post_record",
        query,
        physics_bucket_key=canonical_physics_bucket_key_from_replay_key(query),
    )
    trace = _trace_payload_with_interpolation_feasibility(
        {},
        SimpleNamespace(reduced_real_cache=store.summary()),
        {
            "yield": GateMargin(
                gate="yield",
                feasible=True,
                margin=0.01,
                threshold=ThresholdSpec(
                    id="yield_min",
                    value=0.0,
                    units="fraction",
                    source="synthetic",
                    source_ref="exact synthetic trace verdict",
                ),
                observed=0.01,
                detail="synthetic exact margin",
            )
        },
    )

    assert trace["interpolation_feasibility_verdict"]["verdict"] == "indeterminate"
    assert trace["interpolation_feasibility_verdict"]["diagnostic_only"] is True


def test_lookup_optional_accepts_fully_liquid_single_phase_rows(tmp_path: Path) -> None:
    query = _interpolation_key("query", feo_fraction=0.20, temperature_K=1500.0)
    low = _interpolation_key("low", feo_fraction=0.20, temperature_K=1490.0)
    high = _interpolation_key("high", feo_fraction=0.20, temperature_K=1510.0)
    db_path = tmp_path / "fully-liquid-interpolation.sqlite"
    _put_interpolation_row(db_path, low, liquid_fraction=1.0, sio_pa=7.0)
    _put_interpolation_row(db_path, high, liquid_fraction=1.0, sio_pa=7.05)

    store = PT0DeterminismStore("capture", db_path=db_path)
    payload = store._lookup_optional(
        "equilibrium_post_record",
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
        "equilibrium_post_record",
        query,
        physics_bucket_key=canonical_physics_bucket_key_from_replay_key(query),
    )

    assert payload is None
    assert store.replay_sequence == []
