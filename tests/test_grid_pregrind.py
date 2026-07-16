from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import scripts.grid_pregrind as grid_pregrind
import scripts.grid_pregrind_writer as grid_pregrind_writer
from scripts.grid_pregrind import (
    DEFAULT_FEEDSTOCKS,
    build_grid_points,
    composition_wt_pct_to_mol,
    expand_composition_axes,
    generate_simplex_grid,
    kress91_partitioned_composition_mol,
    load_feedstock_box,
    point_inputs,
    temperature_grid,
)
from scripts.grid_pregrind_writer import (
    CACHE_V2_SCHEMA_VERSION,
    COMPONENT_FIELDS,
    FINDER_INPUT_FIELDS,
    GridCacheWriter,
    cache_v2_identity_manifest,
    cache_v2_key_hash,
    cache_v2_key_hash_from_grid_row,
    canonical_input_vector,
    expedited_key,
)
from scripts.grind_harvest import harvest_snapshot
from simulator.melt_backend.thermoengine import ThermoEngineBackend


def test_feedstock_simplex_grid_sums_deduplicates_and_respects_bounds():
    bounds = load_feedstock_box(DEFAULT_FEEDSTOCKS)
    points = generate_simplex_grid(bounds)

    assert points
    keys = {
        tuple(point.get(oxide, 0.0) for oxide in bounds)
        for point in points
    }
    assert len(keys) == len(points)
    for point in points:
        assert math.isclose(sum(point.values()), 100.0, abs_tol=1e-12)
        for oxide, (lower, upper) in bounds.items():
            value = point.get(oxide, 0.0)
            assert lower <= value <= upper
            assert math.isclose(value / 10.0, round(value / 10.0), abs_tol=1e-12)

    expanded, spec = expand_composition_axes(DEFAULT_FEEDSTOCKS, points)
    assert expanded
    assert len(spec["cr2o3_levels_wt_pct"]) in {2, 3}
    for point in expanded:
        assert len(point) == 14
        assert math.isclose(sum(point.values()), 100.0, abs_tol=1e-12)


def test_grid_seed_is_deterministic_and_temperature_band_is_dense():
    compositions = [
        {"SiO2": 50.0, "FeO": 25.0, "MgO": 25.0},
        {"SiO2": 45.0, "FeO": 30.0, "MgO": 25.0},
    ]
    temperatures = temperature_grid()

    assert len(temperatures) == 27
    assert 1125.0 in temperatures
    assert 1425.0 not in temperatures
    total_a, first = build_grid_points(
        compositions, temperatures, [-9.0, -8.0], seed=178, limit=20
    )
    total_b, second = build_grid_points(
        compositions, temperatures, [-9.0, -8.0], seed=178, limit=20
    )
    _total_c, third = build_grid_points(
        compositions, temperatures, [-9.0, -8.0], seed=179, limit=20
    )
    assert total_a == total_b == len(compositions) * len(temperatures) * 2
    assert first == second
    assert first != third
    assert {point.pressure_bar for point in first} == {1.0}


def test_backend_selector_defaults_to_subprocess_and_accepts_thermoengine():
    default_args = grid_pregrind.parser().parse_args([])
    thermoengine_args = grid_pregrind.parser().parse_args(
        [
            "--backend",
            "thermoengine",
            "--thermoengine-equilibrate-timeout-s",
            "91",
            "--thermoengine-health-timeout-s",
            "31",
        ]
    )

    assert default_args.backend == "subprocess"
    assert grid_pregrind.backend_config(default_args)["mode"] == "subprocess"
    assert grid_pregrind.backend_config(default_args)[
        "vapor_transport_pO2_bar"
    ] == pytest.approx(1.0e-9)
    explicit_subprocess_args = grid_pregrind.parser().parse_args(
        ["--backend", "subprocess"]
    )
    point = grid_pregrind.GridPoint(
        ordinal=0,
        temperature_C=1400.0,
        intended_fO2_log=-9.0,
        pressure_bar=1.0,
        composition_wt_pct={"SiO2": 50.0, "FeO": 25.0, "MgO": 25.0},
    )
    assert grid_pregrind.point_inputs(
        point, default_args
    ) == grid_pregrind.point_inputs(point, explicit_subprocess_args)
    assert thermoengine_args.backend == "thermoengine"
    subprocess_config = grid_pregrind.backend_config(default_args)
    subprocess_config.pop("vapor_transport_pO2_bar")
    assert grid_pregrind.backend_config(thermoengine_args) == {
        **subprocess_config,
        "grid_backend_name": "thermoengine",
        "mode": "thermoengine",
        "thermoengine_equilibrate_timeout_s": 91.0,
        "thermoengine_health_timeout_s": 31.0,
    }
    thermoengine_inputs = grid_pregrind.point_inputs(point, thermoengine_args)
    assert thermoengine_inputs["subprocess_run_mode"] == "isothermal"
    assert thermoengine_inputs["fO2_log"] == point.intended_fO2_log
    assert 0.0 < thermoengine_inputs["kress91_fixed_ferric_fraction"] < 1.0
    assert thermoengine_inputs["composition_mol"]["FeO"] > 0.0
    assert thermoengine_inputs["composition_mol"]["Fe2O3"] > 0.0

    worker = grid_pregrind._ThermoEngineSpawnContext().Process()
    assert worker.daemon is False
    worker.daemon = True
    assert worker.daemon is False


def test_kress_partition_preserves_iron_and_moves_target_into_composition():
    composition = {
        "SiO2": 50.0,
        "TiO2": 1.5,
        "Al2O3": 15.0,
        "FeO": 10.0,
        "Fe2O3": 0.0,
        "MgO": 7.0,
        "CaO": 11.0,
        "Na2O": 3.0,
        "K2O": 0.3,
    }
    baseline = composition_wt_pct_to_mol(composition)
    reducing = kress91_partitioned_composition_mol(
        composition, temperature_C=1400.0, intended_fO2_log=-12.0
    )
    oxidizing = kress91_partitioned_composition_mol(
        composition, temperature_C=1400.0, intended_fO2_log=-5.0
    )
    total_fe = baseline["FeO"] + 2.0 * baseline["Fe2O3"]
    for partitioned in (reducing, oxidizing):
        assert math.isclose(
            partitioned["FeO"] + 2.0 * partitioned["Fe2O3"],
            total_fe,
            rel_tol=0.0,
            abs_tol=1.0e-12,
        )
    assert oxidizing["Fe2O3"] > reducing["Fe2O3"] > 0.0
    assert oxidizing["FeO"] < reducing["FeO"]


def test_thermoengine_fixed_ferric_grind_prep_solves_mare_matrix_when_installed():
    backend = ThermoEngineBackend()
    try:
        available = backend.initialize(
            {
                "thermoengine_equilibrate_timeout_s": 90.0,
                "thermoengine_health_timeout_s": 30.0,
            }
        )
    except ImportError as exc:
        pytest.skip(f"ThermoEngine transport unavailable: {exc}")
    if not available:
        pytest.skip("ThermoEngine transport unavailable")

    feedstocks = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "data" / "feedstocks.yaml")
        .read_text(encoding="utf-8")
    )
    args = grid_pregrind.parser().parse_args(["--backend", "thermoengine"])
    try:
        for feedstock_id in (
            "lunar_mare_low_ti",
            "lunar_mare_high_ti",
            "lunar_highland",
            "lunar_mare_lms1",
        ):
            composition = feedstocks[feedstock_id]["composition_wt_pct"]
            for temperature_C in (1500.0, 1600.0, 1700.0):
                point = grid_pregrind.GridPoint(
                    ordinal=0,
                    temperature_C=temperature_C,
                    intended_fO2_log=-8.0,
                    pressure_bar=1.0,
                    composition_wt_pct=composition,
                )
                inputs = point_inputs(point, args)
                result = backend.equilibrate(
                    temperature_C=temperature_C,
                    composition_kg=None,
                    fO2_log=None,
                    pressure_bar=1.0,
                    composition_mol=inputs["composition_mol"],
                    composition_mol_by_account=inputs[
                        "composition_mol_by_account"
                    ],
                    species_formula_registry=None,
                )

                assert inputs["composition_mol"]["FeO"] > 0.0
                assert inputs["composition_mol"]["Fe2O3"] > 0.0
                assert result.status == "ok", (feedstock_id, temperature_C)
                assert math.isfinite(result.fO2_log)
                assert result.ledger_transition is None
                assert result.chem_potentials
                assert result.phase_affinities
    finally:
        backend.close()


def test_iron_free_points_retain_engine_fo2_constraint_axis_before_sharding():
    composition = {"SiO2": 50.0, "MgO": 25.0, "CaO": 25.0}
    total, points = build_grid_points(
        [composition], [1200.0, 1300.0], [-12.0, -9.0, -5.0], seed=178
    )

    assert total == len(points) == 6
    assert {point.intended_fO2_log for point in points} == {-12.0, -9.0, -5.0}
    assert {point.ordinal % 3 for point in points} == {0, 1, 2}


def test_intended_fo2_is_serialized_and_partitioned_couple_keys_the_point():
    composition = {"SiO2": 50.0, "FeO": 25.0, "MgO": 25.0}
    _total, points = build_grid_points(
        [composition], [1400.0], [-12.0, -5.0], seed=178
    )
    args = SimpleNamespace(
        model="MELTSv1.0.2",
        timeout_s=20.0,
        thermoengine_health_timeout_s=8.0,
    )
    vectors = [point_inputs(point, args) for point in points]

    assert {item["pressure_bar"] for item in vectors} == {1.0}
    assert {item["fO2_log"] for item in vectors} == {-12.0, -5.0}
    assert {item["subprocess_run_mode"] for item in vectors} == {"isothermal"}
    assert vectors[0]["composition_mol"] != vectors[1]["composition_mol"]
    assert expedited_key(vectors[0]) != expedited_key(vectors[1])
    assert "intended_fO2_log" not in canonical_input_vector(vectors[0])


def test_epoch3_fo2_materialization_yields_eight_absolute_keys(tmp_path):
    composition = {
        "SiO2": 45.0,
        "Al2O3": 15.0,
        "FeO": 10.0,
        "Fe2O3": 5.0,
        "MgO": 10.0,
        "CaO": 10.0,
        "Na2O": 5.0,
        "TiO2": 0.0,
        "Cr2O3": 0.0,
        "MnO": 0.0,
        "NiO": 0.0,
        "CoO": 0.0,
        "K2O": 0.0,
        "P2O5": 0.0,
    }
    levels = tuple(float(value) for value in range(-12, -4))
    _total, points = build_grid_points(
        [composition], [1400.0], levels, seed=178
    )
    args = SimpleNamespace(
        model="MELTSv1.0.2",
        timeout_s=20.0,
        thermoengine_health_timeout_s=8.0,
    )
    database = tmp_path / "epoch3-slice.db"
    with GridCacheWriter(database, engine_epoch=3) as writer:
        batch_id = writer.ensure_batch(
            label="epoch3-verification-slice",
            kind="fixed",
            seed=178,
            params={"engine_fO2_constraint": "absolute"},
        )
        for point in points:
            assert writer.materialize_key(
                point_inputs(point, args),
                batch_id=batch_id,
                shuffle_rank=point.ordinal,
                shard=0,
                intended_fO2_log=point.intended_fO2_log,
            )
        rows = writer.connection.execute(
            "SELECT canonical_vector, intended_fO2_log, expedited_key FROM grid_keys "
            "ORDER BY intended_fO2_log"
        ).fetchall()

    persisted = [json.loads(row[0])["fO2_log"] for row in rows]
    intended = [row[1] for row in rows]
    assert persisted == intended == list(levels)
    assert len({row[2] for row in rows}) == 8


def test_adjusted_kress_partition_and_row_provenance_stay_coupled(
    monkeypatch,
    tmp_path,
):
    from simulator import fe_redox

    composition = {
        "SiO2": 50.0,
        "TiO2": 0.0,
        "Al2O3": 0.0,
        "Fe2O3": 0.0,
        "Cr2O3": 0.0,
        "FeO": 25.0,
        "MnO": 0.0,
        "MgO": 25.0,
        "NiO": 0.0,
        "CoO": 0.0,
        "CaO": 0.0,
        "Na2O": 0.0,
        "K2O": 0.0,
        "P2O5": 0.0,
    }
    point = grid_pregrind.GridPoint(
        ordinal=0,
        temperature_C=1100.0,
        intended_fO2_log=-9.0,
        pressure_bar=1.0,
        composition_wt_pct=composition,
    )
    args = SimpleNamespace(
        model="MELTSv1.0.2",
        timeout_s=20.0,
        thermoengine_health_timeout_s=8.0,
    )
    applied_temperatures_K: list[float] = []
    original_split = fe_redox.kress91_split

    def capture_split(*args, **kwargs):
        applied_temperatures_K.append(float(kwargs["T_K"]))
        return original_split(*args, **kwargs)

    monkeypatch.setattr(fe_redox, "kress91_split", capture_split)

    inputs = point_inputs(point, args)
    provenance = inputs["kress91_partition_provenance"]

    assert inputs["temperature_C"] == 1100.0
    assert provenance["requested_temperature_C"] == inputs["temperature_C"]
    assert provenance["applied_temperature_C"] == 1200.0
    assert provenance["action_reason"] == "adjusted_to_liquid_authority_gate"
    assert applied_temperatures_K == [
        pytest.approx(provenance["applied_temperature_C"] + 273.15)
    ]
    assert inputs["composition_mol"] != composition_wt_pct_to_mol(composition)
    with GridCacheWriter(tmp_path / "grid.db") as writer:
        batch_id = writer.ensure_batch(
            label="kress-provenance",
            kind="fixed",
            seed=178,
            params={"test": True},
        )
        assert writer.materialize_key(
            inputs,
            batch_id=batch_id,
            shuffle_rank=0,
            shard=0,
            intended_fO2_log=point.intended_fO2_log,
        )
        # A pre-t299 populated row already has the legacy provenance JSON but
        # lacks the dedicated fixed-ferric scalar columns. Re-materialization
        # must backfill those columns without treating unchanged provenance as
        # drift.
        writer.connection.execute(
            "UPDATE grid_keys SET kress91_fixed_ferric_fraction = NULL, "
            "kress91_fixed_ferric_fraction_repr = NULL"
        )
        assert not writer.materialize_key(
            inputs,
            batch_id=batch_id,
            shuffle_rank=0,
            shard=0,
            intended_fO2_log=point.intended_fO2_log,
        )
        writer.connection.execute(
            "UPDATE grid_keys SET kress91_partition_provenance_json = NULL, "
            "kress91_fixed_ferric_fraction = NULL, "
            "kress91_fixed_ferric_fraction_repr = NULL"
        )
        assert not writer.materialize_key(
            inputs,
            batch_id=batch_id,
            shuffle_rank=0,
            shard=0,
            intended_fO2_log=point.intended_fO2_log,
        )
        pending = writer.pending_rows(batch_id=batch_id)

    assert pending[0]["inputs"]["kress91_partition_provenance"] == provenance
    assert pending[0]["inputs"]["kress91_fixed_ferric_fraction"] == pytest.approx(
        inputs["kress91_fixed_ferric_fraction"]
    )
    assert pending[0]["inputs"]["composition_mol"] == inputs["composition_mol"]


def _inputs(temperature_C: float) -> dict:
    composition = {
        "SiO2": 10.0,
        "TiO2": 0.0,
        "Al2O3": 0.0,
        "Fe2O3": 1.0,
        "Cr2O3": 0.0,
        "FeO": 5.0,
        "MnO": 0.0,
        "MgO": 0.0,
        "NiO": 0.0,
        "CoO": 0.0,
        "CaO": 1.0,
        "Na2O": 0.0,
        "K2O": 0.0,
        "P2O5": 0.0,
    }
    values = {
        "temperature_C": temperature_C,
        "kress91_fixed_ferric_fraction": 2.0 / 7.0,
        "kress91_partition_provenance": (
            grid_pregrind.kress91_partition_authority_record(
                temperature_C=temperature_C
            )
        ),
        "composition_kg": None,
        "fO2_log": -9.0,
        "pressure_bar": 1.0,
        "composition_mol": composition,
        "composition_mol_by_account": {"process.cleaned_melt": composition},
        "species_formula_registry": None,
        "mode": "subprocess",
        "subprocess_run_mode": "isothermal",
        "redox_buffer": None,
        "fO2_offset": None,
        "Fe3Fet_Liq": None,
        "model": "MELTSv1.0.2",
        "timeout_s": 20.0,
        "require_petthermotools": False,
        "thermoengine_health_timeout_s": 8.0,
    }
    values.update({name: None for name in FINDER_INPUT_FIELDS})
    return values


def _output(status: str = "ok") -> dict:
    return {
        "status": status,
        "status_kind": "success" if status == "ok" else "refusal",
        "refusal_reason": None if status == "ok" else "test_refusal",
        "raw_payload": json.dumps({"stdout": "native output\n"}),
        "raw_payload_format": "alphamelts-subprocess-capture-v1",
        "timing_s": 1.2345678901234567,
        "engine_version": "alphaMELTS test",
        "engine_mode": "subprocess",
        "engine_model": "MELTSv1.0.2",
        "run_mode": "isothermal",
        "applied_timeout_s": 20.0,
        "native_input": {"SiO2": 66.66666666666667},
        "generic": {
            "temperature_C": 1200.1234567890123,
            "requested_temperature_C": 1200.0,
            "pressure_bar": 1.0,
            "phases_present": ["liquid"],
            "phase_masses_kg": {"liquid": 0.1},
            "phase_species_mol": {
                "olivine0": {"(Mg0.8Fe0.2)2SiO4": 0.25},
                "olivine1": {"(Mg0.6Fe0.4)2SiO4": 0.35},
            },
            "phase_species_kg": {
                "olivine0": {"(Mg0.8Fe0.2)2SiO4": 0.04},
                "olivine1": {"(Mg0.6Fe0.4)2SiO4": 0.06},
            },
            "phase_instances": [
                {
                    "instance_id": "olivine0",
                    "phase": "olivine",
                    "solver_basis_mass_kg": 0.04,
                    "physical_mass_kg": 0.04,
                    "formula_or_endmember_token": "(Mg0.8Fe0.2)2SiO4",
                    "composition_wt_pct": {"SiO2": 40.0, "FeO": 10.0},
                    "reference_basis": "alphamelts_solver_phase_amount",
                },
                {
                    "instance_id": "olivine1",
                    "phase": "olivine",
                    "solver_basis_mass_kg": 0.06,
                    "physical_mass_kg": 0.06,
                    "formula_or_endmember_token": "(Mg0.6Fe0.4)2SiO4",
                    "composition_wt_pct": {"SiO2": 35.0, "FeO": 30.0},
                    "reference_basis": "alphamelts_solver_phase_amount",
                }
            ],
            "phase_compositions": {},
            "liquid_fraction": 1.0,
            "phase_assemblage_available": True,
            "liquid_composition_wt_pct": {"SiO2": 66.66666666666667},
            "liquid_viscosity_Pa_s": 2.5,
            "liquid_density_kg_m3": 2650.0,
            "system_enthalpy": -1059377.10,
            "system_entropy": 268.91,
            "system_volume": 34.56e-6,
            "system_heat_capacity_Cp": 143.47,
            "system_dVdP": -183.54,
            "system_dVdT": 2897.97,
            "system_fO2_delta_QFM": -4.229,
            "system_solid_density_rhos": None,
            "system_phi": 1.0,
            "system_chisqr": None,
            "phase_thermo": {
                "liquid": {
                    "enthalpy_J": -1059377.10,
                    "volume_m3": 34.56e-6,
                    "density_kg_m3": 2893.824,
                    "reference_mass_kg": 0.1,
                    "reference_basis": "alphamelts_solver_phase_amount",
                }
            },
            "chem_potentials": None,
            "phase_affinities": None,
            "solid_composition_wt_pct": {},
            "bulk_composition_wt_pct": {"SiO2": 49.3753},
            "vapor_pressures_Pa": {"SiO": 12.5},
            "vapor_pressures_source": {
                "SiO": "builtin_authoritative:test"
            },
            "activity_coefficients": {},
            "fO2_log": -9.0,
            "warnings": [],
            "ledger_transition": None,
            "status": status,
            "sulfur_saturation": None,
            "liquidus_T_C": None,
            "diagnostics": {},
        },
        "alphamelts": {
            "backend_status": status,
            "backend_warnings": [],
            "engine_version": "alphaMELTS test",
            "mode": "subprocess",
            "fO2_log": -9.0,
            "intrinsic_fO2_log": -9.0,
        },
        "finder": {},
        "host": "test-host",
    }


def test_subprocess_crash_is_failure_not_refusal():
    assert grid_pregrind._status_kind(
        "out_of_domain", "subprocess_died"
    ) == "failure"
    assert grid_pregrind._status_kind(
        "out_of_domain", "no_convergence"
    ) == "refusal"


def test_stale_explicit_fo2_key_is_typed_refusal_before_engine(monkeypatch):
    class NoCallBackend:
        _model = "MELTSv1.0.2"

        def equilibrate(self, **_kwargs):
            pytest.fail("stale key reached engine")

    monkeypatch.setattr(grid_pregrind, "_WORKER_BACKEND", NoCallBackend())
    monkeypatch.setattr(grid_pregrind, "_WORKER_MODULE", SimpleNamespace())
    inputs = {**_inputs(1400.0), "intended_fO2_log": -8.0}

    _key_id, output = grid_pregrind._run_point(
        grid_pregrind.WorkerJob(7, 0, inputs)
    )

    assert output["status_kind"] == "refusal"
    assert output["refusal_reason"] == "stale_explicit_fo2_key"
    raw = json.loads(output["raw_payload"])
    assert raw["engine_invoked"] is False
    assert raw["preflight_refusal"]["persisted_fO2_log"] == -9.0
    assert raw["preflight_refusal"]["intended_fO2_log"] == -8.0


@pytest.mark.parametrize("oxide", ["CaO", "FeO", "Fe2O3"])
def test_grind_zero_component_boundary_refuses_before_engine(oxide, monkeypatch):
    class NoCallBackend:
        _model = "MELTSv1.0.2"

        def equilibrate(self, **_kwargs):
            pytest.fail("zero-component GRIND cell reached engine")

    monkeypatch.setattr(grid_pregrind, "_WORKER_BACKEND", NoCallBackend())
    monkeypatch.setattr(grid_pregrind, "_WORKER_MODULE", SimpleNamespace())
    inputs = {**_inputs(1400.0), "intended_fO2_log": -9.0}
    inputs["composition_mol"] = dict(inputs["composition_mol"])
    inputs["composition_mol"][oxide] = 0.0

    _key_id, output = grid_pregrind._run_point(
        grid_pregrind.WorkerJob(7, 0, inputs)
    )

    assert output["status_kind"] == "refusal"
    assert output["refusal_reason"] == "zero_component_boundary"
    raw = json.loads(output["raw_payload"])
    assert raw["engine_invoked"] is False
    assert raw["preflight_refusal"]["zero_boundary_components"] == [oxide]
    assert raw["preflight_refusal"]["boundary_predicate"] == "component_mol == 0.0"


def test_grind_missing_boundary_component_refuses_before_engine(monkeypatch):
    monkeypatch.setattr(grid_pregrind, "_WORKER_BACKEND", SimpleNamespace())
    monkeypatch.setattr(grid_pregrind, "_WORKER_MODULE", SimpleNamespace())
    inputs = {**_inputs(1400.0), "intended_fO2_log": -9.0}
    inputs["composition_mol"] = dict(inputs["composition_mol"])
    del inputs["composition_mol"]["Fe2O3"]

    _key_id, output = grid_pregrind._run_point(
        grid_pregrind.WorkerJob(7, 0, inputs)
    )

    assert output["status_kind"] == "refusal"
    assert output["refusal_reason"] == "zero_component_boundary"
    raw = json.loads(output["raw_payload"])
    assert raw["preflight_refusal"]["zero_boundary_components"] == ["Fe2O3"]


def test_grind_positive_subthreshold_component_is_not_refused(monkeypatch):
    inputs = {**_inputs(1400.0), "intended_fO2_log": -9.0}
    inputs["composition_mol"] = dict(inputs["composition_mol"])
    inputs["composition_mol"]["Fe2O3"] = 1.0e-300

    class CalledBackend:
        _mode = "subprocess"
        _model = "MELTSv1.0.2"
        _timeout_s = 5.0
        _equilibrate_subprocess = staticmethod(lambda *_args, **_kwargs: None)

        def equilibrate(self, **_kwargs):
            raise RuntimeError("positive boundary reached backend")

    monkeypatch.setattr(grid_pregrind, "_WORKER_BACKEND", CalledBackend())
    monkeypatch.setattr(
        grid_pregrind,
        "_WORKER_MODULE",
        SimpleNamespace(subprocess=SimpleNamespace(run=lambda *_args, **_kwargs: None)),
    )

    _key_id, output = grid_pregrind._run_point(
        grid_pregrind.WorkerJob(7, 0, inputs)
    )

    assert output["status_kind"] == "failure"
    assert "positive boundary reached backend" in output["raw_payload"]


def test_worker_failure_output_records_positive_wall_time(monkeypatch):
    monkeypatch.setattr(grid_pregrind.time, "monotonic", lambda: 15.25)

    output = grid_pregrind._worker_failure_output(
        RuntimeError("synthetic failure"),
        started=10.0,
        captures=[],
        native_input=None,
    )

    assert output["status_kind"] == "failure"
    assert output["timing_s"] == pytest.approx(5.25)
    assert output["failure_reason_code"] == "exception_runtimeerror"
    assert output["failure_message"] == "synthetic failure"

    parent_fallback = grid_pregrind._worker_failure_output(
        RuntimeError("worker transport failure"),
        started=10.0,
        captures=[],
        native_input=None,
        backend_name="thermoengine",
    )
    assert parent_fallback["engine_mode"] == "thermoengine"
    assert parent_fallback["alphamelts"] == {}
    assert (
        parent_fallback["raw_payload_format"]
        == grid_pregrind.THERMOENGINE_RAW_PAYLOAD_FORMAT
    )

    long_message = "x" * (grid_pregrind.FAILURE_MESSAGE_MAX_LENGTH + 20)
    bounded = grid_pregrind._worker_failure_output(
        RuntimeError(long_message),
        started=10.0,
        captures=[],
        native_input=None,
        backend_name="thermoengine",
    )
    assert len(bounded["failure_message"]) == grid_pregrind.FAILURE_MESSAGE_MAX_LENGTH
    assert json.loads(bounded["raw_payload"])["exception"]["message"] == long_message


def test_thermoengine_grind_uses_fixed_ferric_intrinsic_open_loop(monkeypatch):
    calls = []
    result = SimpleNamespace(**dict(_output()["generic"]))

    class Backend:
        _model = "MELTSv1.0.2"

        def equilibrate(self, **kwargs):
            calls.append(kwargs)
            return result

    inputs = {
        **_inputs(1600.0),
        "mode": "thermoengine",
        "subprocess_run_mode": None,
        "intended_fO2_log": -8.0,
        "fO2_log": -8.0,
    }
    monkeypatch.setattr(grid_pregrind, "_WORKER_BACKEND_NAME", "thermoengine")
    monkeypatch.setattr(grid_pregrind, "_WORKER_BACKEND", Backend())
    monkeypatch.setattr(grid_pregrind, "_WORKER_MODULE", None)
    monkeypatch.setattr(grid_pregrind, "_WORKER_ALLOW_ZERO_COMPONENT_BOUNDARY", True)
    monkeypatch.setattr(grid_pregrind, "_WORKER_ENGINE_VERSION", "fixture-engine")

    _grid_key_id, output = grid_pregrind._run_point(
        grid_pregrind.WorkerJob(7, 0, inputs)
    )

    assert calls[0]["fO2_log"] is None
    assert calls[0]["composition_mol"]["FeO"] > 0.0
    assert calls[0]["composition_mol"]["Fe2O3"] > 0.0
    constraint = json.loads(output["raw_payload"])["fO2_constraint"]
    assert constraint == {
        "adapter_fO2_log_argument": None,
        "fixed_ferric_fraction": pytest.approx(2.0 / 7.0),
        "intended_fO2_log": -8.0,
        "path": "Kress91FixedFerricIntrinsic",
        "solved_fO2_log": result.fO2_log,
    }


def test_run_point_applies_stored_timeout_and_captures_subprocess_budget(monkeypatch):
    result = SimpleNamespace(**dict(_output()["generic"]))

    class FakeBackend:
        _mode = "subprocess"
        _model = "MELTSv1.0.2"
        _timeout_s = 5.0

        def _equilibrate_subprocess(
            self,
            temperature_C,
            composition_wt_pct,
            fO2_log,
            pressure_bar,
            warnings=None,
            *,
            total_input_kg=None,
            diagnostics=None,
            run_mode,
        ):
            del (
                temperature_C,
                composition_wt_pct,
                fO2_log,
                pressure_bar,
                warnings,
                total_input_kg,
                diagnostics,
                run_mode,
            )
            module.subprocess.run(
                ["fake-alphamelts"],
                input="fixture",
                timeout=self._timeout_s,
            )
            return result

        def equilibrate(self, **kwargs):
            return self._equilibrate_subprocess(
                kwargs["temperature_C"],
                kwargs["composition_mol"],
                kwargs["fO2_log"],
                kwargs["pressure_bar"],
                total_input_kg=(
                    sum(float(v) for v in kwargs["composition_kg"].values())
                    if kwargs.get("composition_kg")
                    else 100.0
                ),
                diagnostics={},
                run_mode=kwargs["subprocess_run_mode"],
            )

    module = SimpleNamespace(
        subprocess=SimpleNamespace(
            run=lambda *args, **kwargs: SimpleNamespace(
                returncode=0,
                stdout="",
                stderr="",
            )
        )
    )
    backend = FakeBackend()
    monkeypatch.setattr(grid_pregrind, "_WORKER_BACKEND", backend)
    monkeypatch.setattr(grid_pregrind, "_WORKER_MODULE", module)
    monkeypatch.setattr(grid_pregrind, "_WORKER_ENGINE_VERSION", "fixture-engine")

    grid_key_id, output = grid_pregrind._run_point(
        grid_pregrind.WorkerJob(
            grid_key_id=7,
            shuffle_rank=0,
            inputs={**_inputs(1200.0), "intended_fO2_log": -9.0},
        )
    )

    capture = json.loads(output["raw_payload"])["captures"][0]
    assert grid_key_id == 7
    assert backend._timeout_s == pytest.approx(20.0)
    assert capture["timeout"] == pytest.approx(20.0)
    assert output["applied_timeout_s"] == pytest.approx(20.0)
    assert output["run_mode"] == "isothermal"


def test_thermoengine_failure_reinitializes_before_next_point(monkeypatch):
    result = SimpleNamespace(**dict(_output()["generic"]))

    class FailingBackend:
        _model = "MELTSv1.0.2"

        def equilibrate(self, **_kwargs):
            raise RuntimeError("forced ThermoEngine failure")

    class ReinitializedBackend:
        _model = "MELTSv1.0.2"

        def equilibrate(self, **_kwargs):
            return result

    reinitializations = []

    def fake_initialize(config, assumed_queued_run_mode=None):
        reinitializations.append((dict(config), assumed_queued_run_mode))
        monkeypatch.setattr(
            grid_pregrind, "_WORKER_BACKEND", ReinitializedBackend()
        )
        monkeypatch.setattr(
            grid_pregrind, "_WORKER_ENGINE_VERSION", "reinitialized-engine"
        )

    inputs = {
        **_inputs(1400.0),
        "mode": "thermoengine",
        "subprocess_run_mode": None,
        "intended_fO2_log": -9.0,
    }
    job = grid_pregrind.WorkerJob(grid_key_id=7, shuffle_rank=0, inputs=inputs)
    monkeypatch.setattr(grid_pregrind, "_WORKER_BACKEND_NAME", "thermoengine")
    monkeypatch.setattr(grid_pregrind, "_WORKER_BACKEND", FailingBackend())
    monkeypatch.setattr(grid_pregrind, "_WORKER_MODULE", None)
    monkeypatch.setattr(grid_pregrind, "_WORKER_CONFIG", {"grid_backend_name": "thermoengine"})
    monkeypatch.setattr(grid_pregrind, "_WORKER_ALLOW_ZERO_COMPONENT_BOUNDARY", True)
    monkeypatch.setattr(grid_pregrind, "_worker_initialize", fake_initialize)

    _grid_key_id, failed = grid_pregrind._run_point(job)
    _grid_key_id, recovered = grid_pregrind._run_point(job)

    assert failed["status_kind"] == "failure"
    assert reinitializations == [({"grid_backend_name": "thermoengine"}, None)]
    assert recovered["status_kind"] == "success"
    assert recovered["engine_version"] == "reinitialized-engine"


@pytest.mark.parametrize("timeout_s", [None, 0.0, -1.0, math.nan])
def test_queued_job_timeout_must_be_positive_and_finite(timeout_s):
    inputs = _inputs(1200.0)
    inputs["timeout_s"] = timeout_s

    with pytest.raises(ValueError, match="timeout_s"):
        grid_pregrind._job_runtime_settings(
            grid_pregrind.WorkerJob(
                grid_key_id=7,
                shuffle_rank=0,
                inputs=inputs,
            )
        )


@pytest.mark.parametrize("existing_only", [False, True])
def test_existing_grid_database_adds_nullable_runmode_output_columns(
    tmp_path,
    existing_only,
):
    database = tmp_path / "legacy-grid.db"
    with GridCacheWriter(database):
        pass
    connection = sqlite3.connect(database)
    connection.execute("DROP INDEX idx_alphamelts_outputs_failure_reason")
    for table, column in (
        ("grid_keys", "kress91_fixed_ferric_fraction"),
        ("grid_keys", "kress91_fixed_ferric_fraction_repr"),
        ("grid_keys", "subprocess_run_mode"),
        ("alphamelts_outputs", "failure_reason_code"),
        ("alphamelts_outputs", "failure_message"),
        ("alphamelts_outputs", "generic_requested_temperature_C"),
        ("alphamelts_outputs", "generic_requested_temperature_C_repr"),
        ("alphamelts_outputs", "generic_liquid_density_kg_m3"),
        ("alphamelts_outputs", "generic_liquid_density_kg_m3_repr"),
        ("alphamelts_outputs", "generic_system_enthalpy"),
        ("alphamelts_outputs", "generic_system_enthalpy_repr"),
        ("alphamelts_outputs", "generic_phase_thermo_json"),
        ("alphamelts_outputs", "generic_chem_potentials_json"),
        ("alphamelts_outputs", "generic_phase_affinities_json"),
        ("alphamelts_outputs", "generic_phase_instances_json"),
        ("alphamelts_outputs", "generic_solid_composition_wt_pct_json"),
            ("alphamelts_outputs", "generic_bulk_composition_wt_pct_json"),
            ("alphamelts_outputs", "te_liquid_activities_json"),
            ("alphamelts_outputs", "te_system_dVdP_m3_bar"),
            ("alphamelts_outputs", "te_system_dVdP_m3_bar_repr"),
            ("alphamelts_outputs", "te_system_dVdT_m3_K"),
            ("alphamelts_outputs", "te_system_dVdT_m3_K_repr"),
            ("alphamelts_outputs", "te_solver_status"),
            ("alphamelts_outputs", "te_solver_converged"),
            ("alphamelts_outputs", "te_solver_iterations"),
            ("alphamelts_outputs", "te_solver_iterations_available"),
            ("alphamelts_outputs", "te_fO2_solve_count"),
            ("alphamelts_outputs", "te_phase_universe_size"),
            ("alphamelts_outputs", "run_mode"),
        ("alphamelts_outputs", "applied_timeout_s"),
        ("alphamelts_outputs", "applied_timeout_s_repr"),
    ):
        connection.execute(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
    connection.execute(
        "UPDATE metadata SET value = '72' "
        "WHERE key = 'schema_output_field_count'"
    )
    connection.commit()
    connection.close()

    with GridCacheWriter(database, existing_only=existing_only) as writer:
        grid_columns = {
            row[1]
            for row in writer.connection.execute('PRAGMA table_info("grid_keys")')
        }
        output_columns = {
            row[1]
            for row in writer.connection.execute(
                'PRAGMA table_info("alphamelts_outputs")'
            )
        }
        output_field_count = writer.connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_output_field_count'"
        ).fetchone()[0]

    assert "subprocess_run_mode" in grid_columns
    assert {
        "kress91_fixed_ferric_fraction",
        "kress91_fixed_ferric_fraction_repr",
    } <= grid_columns
    assert {
        "failure_reason_code",
        "failure_message",
        "generic_requested_temperature_C",
        "generic_requested_temperature_C_repr",
        "generic_liquid_density_kg_m3",
        "generic_liquid_density_kg_m3_repr",
        "generic_system_enthalpy",
        "generic_system_enthalpy_repr",
        "generic_phase_thermo_json",
        "generic_chem_potentials_json",
        "generic_phase_affinities_json",
        "generic_phase_instances_json",
        "generic_solid_composition_wt_pct_json",
            "generic_bulk_composition_wt_pct_json",
            "te_liquid_activities_json",
            "te_system_dVdP_m3_bar",
            "te_system_dVdP_m3_bar_repr",
            "te_system_dVdT_m3_K",
            "te_system_dVdT_m3_K_repr",
            "te_solver_status",
            "te_solver_converged",
            "te_solver_iterations",
            "te_solver_iterations_available",
            "te_fO2_solve_count",
            "te_phase_universe_size",
            "run_mode",
        "applied_timeout_s",
        "applied_timeout_s_repr",
    } <= output_columns
    assert output_field_count == "84"


def test_cache_v2_immutable_metadata_written_checked_and_refuses_drift(tmp_path):
    database = tmp_path / "cache-v2-metadata.db"
    with GridCacheWriter(database) as writer:
        metadata = dict(
            writer.connection.execute(
                "SELECT key, value FROM metadata WHERE key LIKE 'cache_v2_%' "
                "OR key = 'corpus_version'"
            )
        )

    manifest = cache_v2_identity_manifest()
    assert metadata["cache_v2_schema_version"] == CACHE_V2_SCHEMA_VERSION
    assert json.loads(metadata["cache_v2_identity_manifest"]) == manifest
    assert manifest["identity"] == {
        "fields": ["engine_name", "engine_version", "quantized_inputs"],
        "cache_lever": "corpus_version",
        "optimizer_identity_included": False,
    }
    quantized_fields = [item["field"] for item in manifest["quantized_inputs"]]
    assert quantized_fields[:14] == [
        f"component_{component}_mol"
        for component in COMPONENT_FIELDS
    ]
    assert "timeout_s" not in quantized_fields
    assert len(manifest["outputs"]) == 84
    output_specs = {item["field"]: item for item in manifest["outputs"]}
    assert output_specs["thermoengine.system_dVdP_m3_bar"]["units"] == "m3/bar"
    assert output_specs["thermoengine.system_dVdT_m3_K"]["units"] == "m3/K"
    assert output_specs["thermoengine.liquid_activities"]["reference_basis"] == (
        "ThermoEngine solved liquid endmember activity"
    )
    assert {
        item["field"] for item in manifest["outputs"]
    } == {
        *(f"generic.{field}" for field in grid_pregrind_writer.GENERIC_OUTPUT_FIELDS),
        *(
            f"thermoengine.{field}"
            for field in grid_pregrind_writer.THERMOENGINE_OUTPUT_FIELDS
        ),
        *(
            f"alphamelts.{field}"
            for field in grid_pregrind_writer.ALPHAMELTS_DIAGNOSTIC_OUTPUT_FIELDS
        ),
        *(f"finder.{field}" for field in grid_pregrind_writer.FINDER_OUTPUT_FIELDS),
    }
    output_specs = {item["field"]: item for item in manifest["outputs"]}
    assert output_specs["generic.system_dVdP"]["units"] == (
        "AlphaMELTS System_main dVdP*10^6 as printed"
    )
    assert output_specs["generic.system_dVdT"]["units"] == (
        "AlphaMELTS System_main dVdT*10^6 as printed"
    )
    assert manifest["flags"]["clamp_extrapolation_bits"]
    for dictionary in manifest["dictionaries"].values():
        assert dictionary["values"]
        assert len(dictionary["sha256"]) == 64
        encoded = json.dumps(
            dictionary["values"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        assert dictionary["sha256"] == hashlib.sha256(encoded).hexdigest()
        assert dictionary["unknown_value_policy"] == "refuse"
    assert manifest["dictionaries"]["regime"]["values"] == [
        item.value for item in grid_pregrind_writer.MeltRegime
    ]
    assert manifest["dictionaries"]["evidence_class"]["values"] == [
        item.value for item in grid_pregrind_writer.EvidenceClass
    ]
    assert manifest["dictionaries"]["backend"]["values"] == [
        item.value for item in grid_pregrind_writer.CacheV2GridBackend
    ]
    assert manifest["dictionaries"]["tier"]["values"] == [
        item.value for item in grid_pregrind_writer.CacheV2ConfidenceTier
    ]
    assert manifest["dictionaries"]["notice"]["values"] == [
        item.value for item in grid_pregrind_writer.CacheV2Notice
    ]
    assert {
        "H2O",
        "CO2",
        "CO",
        "CH4",
        "NH3",
        "HCN",
        "SO2",
        "H2S",
    } <= set(manifest["dictionaries"]["species"]["values"])

    with GridCacheWriter(database, existing_only=True):
        pass
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE metadata SET value = 'drifted' "
        "WHERE key = 'cache_v2_identity_manifest'"
    )
    connection.commit()
    connection.close()
    for existing_only in (False, True):
        with pytest.raises(
            ValueError,
            match="cache_v2 immutable metadata mismatch",
        ):
            GridCacheWriter(database, existing_only=existing_only)


def test_cache_v2_key_hash_round_trips_stored_typed_inputs(tmp_path):
    database = tmp_path / "cache-v2-key-hash.db"
    inputs = _inputs(1200.0)
    with GridCacheWriter(database) as writer:
        writer.seed_id_block(0)
        batch_id = writer.ensure_batch(
            label="cache-v2-key-hash",
            kind="fixed",
            seed=178,
            params={"full_grid_points": 1, "shard_count": 3},
        )
        assert writer.materialize_key(
            inputs,
            batch_id=batch_id,
            shuffle_rank=0,
            shard=0,
        )
        row = writer.connection.execute("SELECT * FROM grid_keys").fetchone()
        assert row is not None
        assert row["key_hash"] == cache_v2_key_hash(inputs)
        assert row["key_hash"] == cache_v2_key_hash_from_grid_row(row)


@pytest.mark.parametrize(
    "namespace, field, value, reason",
    [
        (
            "generic",
            "activity_coefficients",
            {"unknown_species": 1.0},
            "cache_v2_unknown_species",
        ),
        (
            "generic",
            "phase_species_mol",
            {"olivine0": {"undeclared_formula_token": 1.0}},
            "cache_v2_unknown_species",
        ),
        (
            "generic",
            "phase_species_mol",
            {"olivine0": {"(Mg0.6Fe0.4)2SiO4": 1.0}},
            "cache_v2_unknown_species",
        ),
        (
            "generic",
            "phase_species_mol",
            {"unknown0": {"(Mg0.8Fe0.2)2SiO4": 1.0}},
            "cache_v2_unknown_species",
        ),
        (
            "thermoengine",
            "liquid_activities",
            {"future_bogus_endmember": 1.0},
            "cache_v2_unknown_thermoengine_liquid_endmember",
        ),
        (
            "alphamelts",
            "phase_masses_kg",
            {"future bogus phase": 1.0},
            "cache_v2_unknown_phase",
        ),
        (
            "alphamelts",
            "activity_coefficients",
            {"future bogus species": 1.0},
            "cache_v2_unknown_species",
        ),
        (
            "output",
            "engine_mode",
            "future bogus backend",
            "cache_v2_unknown_backend",
        ),
    ],
)
def test_cache_v2_unknown_dictionary_values_are_contained(
    tmp_path, namespace, field, value, reason
):
    output = _output()
    if namespace == "output":
        output[field] = value
    else:
        output.setdefault(namespace, {})[field] = value
    with GridCacheWriter(tmp_path / f"unknown-{field}.db") as writer:
        values = writer._output_values(output)
    assert values["status"] == "error"
    assert values["status_kind"] == "failure"
    assert values["failure_reason_code"] == reason
    assert reason in values["refusal_reason"]
    assert len(values["failure_message"]) <= 512
    assert json.loads(values["generic_phases_present_json"] or "[]") == []
    assert json.loads(values["te_liquid_activities_json"] or "{}") == {}


def test_cache_v2_h2o_species_and_liquid_endmember_write_successfully(tmp_path):
    output = _output()
    output["generic"]["phase_species_mol"]["olivine0"]["H2O"] = 0.125
    output["generic"]["phase_species_kg"]["olivine0"]["H2O"] = 0.00225
    output["generic"]["vapor_pressures_Pa"]["H2O"] = 2.0
    output["generic"]["vapor_pressures_source"]["H2O"] = "thermoengine:test"
    output["generic"]["activity_coefficients"]["H2O"] = 0.8
    output["thermoengine"] = {"liquid_activities": {"H2O": 0.7}}

    with GridCacheWriter(
        tmp_path / "h2o-speciation.db", backend_name="subprocess"
    ) as writer:
        writer.seed_id_block(0)
        batch_id = writer.ensure_batch(
            label="h2o-speciation",
            kind="fixed",
            seed=335,
            params={"full_grid_points": 1, "shard_count": 3},
        )
        assert writer.materialize_key(
            _inputs(1200.0),
            batch_id=batch_id,
            shuffle_rank=0,
            shard=0,
        )
        grid_key_id = writer.pending_rows(batch_id=batch_id)[0]["grid_key_id"]
        assert writer.write_result(grid_key_id, output)
        row = writer.connection.execute(
            "SELECT * FROM alphamelts_outputs"
        ).fetchone()

    assert row["status"] == "ok"
    assert json.loads(row["generic_phase_species_mol_json"])["olivine0"][
        "H2O"
    ] == pytest.approx(0.125)
    assert json.loads(row["generic_vapor_pressures_Pa_json"])["H2O"] == 2.0
    assert json.loads(row["te_liquid_activities_json"])["H2O"] == 0.7


def test_cache_v2_thermoengine_phase_dictionary_covers_grounded_vocabulary(
    tmp_path,
):
    native_labels = {
        native
        for _canonical, native in (
            grid_pregrind_writer.CACHE_V2_THERMOENGINE_PHASE_LABELS
        )
    }
    canonical_labels = {
        canonical
        for canonical, _native in (
            grid_pregrind_writer.CACHE_V2_THERMOENGINE_PHASE_LABELS
        )
    }

    assert len(native_labels) == len(canonical_labels) == 54
    assert "Solid Alloy" in native_labels
    assert "solid alloy" in canonical_labels
    assert "alloy-solid" in grid_pregrind_writer.CACHE_V2_PHASE_DICTIONARY
    assert canonical_labels <= set(grid_pregrind_writer.CACHE_V2_PHASE_DICTIONARY)
    assert "ThermoEngine MELTSv1.0.2 MELTSmodel.get_phase_names()" in (
        cache_v2_identity_manifest()["dictionary_sources"]["phase"]
    )

    output = _output()
    output["generic"]["phases_present"] = ["solid alloy"]
    output["generic"]["phase_masses_kg"] = {"solid alloy": 0.1}
    with GridCacheWriter(tmp_path / "solid-alloy.db") as writer:
        values = writer._output_values(output)
    assert json.loads(values["generic_phases_present_json"]) == ["solid alloy"]


def test_concurrent_partial_creator_is_refused_before_schema_mutation(tmp_path):
    database = tmp_path / "cache-v2-concurrent-create.db"
    creator = sqlite3.connect(database)
    creator.execute("BEGIN IMMEDIATE")
    creator.execute("CREATE TABLE replacement_marker(value TEXT)")
    errors = []

    def open_writer():
        try:
            GridCacheWriter(database)
        except Exception as exc:  # captured for assertion in the main thread
            errors.append(exc)

    thread = threading.Thread(target=open_writer)
    thread.start()
    time.sleep(0.05)
    creator.commit()
    creator.close()
    thread.join(timeout=5.0)

    assert not thread.is_alive()
    assert len(errors) == 1
    assert "missing tables" in str(errors[0])
    connection = sqlite3.connect(database)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    connection.close()
    assert tables == {"replacement_marker"}


def test_legacy_grid_without_cache_v2_metadata_remains_readable(tmp_path):
    database = tmp_path / "legacy-no-cache-v2-metadata.db"
    with GridCacheWriter(database):
        pass
    connection = sqlite3.connect(database)
    connection.execute(
        "DELETE FROM metadata WHERE key LIKE 'cache_v2_%' "
        "OR key = 'corpus_version'"
    )
    connection.commit()
    connection.close()

    with GridCacheWriter(database, existing_only=True) as writer:
        assert writer.counts()["total"] == 0


def test_legacy_cache_v2_descriptive_manifest_remains_readable(tmp_path):
    database = tmp_path / "legacy-descriptive-manifest.db"
    with GridCacheWriter(database):
        pass

    legacy_manifest = cache_v2_identity_manifest()
    legacy_manifest["dictionaries"]["species"]["values"] = legacy_manifest[
        "dictionaries"
    ]["species"]["values"][:29]
    legacy_manifest["dictionaries"]["phase"]["values"].remove("alloy-solid")
    legacy_manifest["dictionaries"]["evidence_class"]["values"].append("unknown")
    for dictionary in legacy_manifest["dictionaries"].values():
        encoded = json.dumps(
            dictionary["values"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        dictionary["sha256"] = hashlib.sha256(encoded).hexdigest()
    legacy_manifest["dictionary_sources"]["species"] = "legacy observed union"
    legacy_manifest["dictionary_policy"]["unknown_species"] = "refuse"
    manifest_json = grid_pregrind_writer.canonical_json(legacy_manifest)

    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE metadata SET value = ? WHERE key = 'cache_v2_identity_manifest'",
        (manifest_json,),
    )
    connection.execute(
        "UPDATE metadata SET value = ? "
        "WHERE key = 'cache_v2_identity_manifest_sha256'",
        (hashlib.sha256(manifest_json.encode("utf-8")).hexdigest(),),
    )
    connection.commit()
    connection.close()

    with GridCacheWriter(database, existing_only=True) as writer:
        assert writer.counts()["total"] == 0
        metadata = dict(
            writer.connection.execute(
                "SELECT key, value FROM metadata WHERE key LIKE 'cache_v2_%'"
            )
        )
        current_manifest = grid_pregrind_writer.canonical_json(
            cache_v2_identity_manifest()
        )
        assert metadata["cache_v2_identity_manifest"] == current_manifest
        assert metadata["cache_v2_identity_manifest_sha256"] == hashlib.sha256(
            current_manifest.encode("utf-8")
        ).hexdigest()


def _prepared_drain_database(database, temperatures=(1200.0,)):
    with GridCacheWriter(database) as writer:
        writer.seed_id_block(0)
        batch_id = writer.ensure_batch(
            label="fixed-v2",
            kind="fixed",
            seed=178,
            params={
                "full_grid_points": len(temperatures),
                "shard_count": 3,
                "kress91_partition": {
                    "implementation": "fixture:kress91_split",
                    "version": "fixture-v1",
                },
            },
        )
        for shuffle_rank, temperature_C in enumerate(temperatures):
            assert writer.materialize_key(
                _inputs(temperature_C),
                batch_id=batch_id,
                shuffle_rank=shuffle_rank,
                shard=0,
                intended_fO2_log=-9.0,
            )


class _ImmediateResult:
    def __init__(self, value):
        self.value = value

    def ready(self):
        return True

    def get(self):
        return self.value


class _ImmediatePool:
    def __init__(self, processes, initializer, initargs):
        del initializer
        self.processes = processes
        self.initargs = initargs
        self.submissions = 0

    def apply_async(self, function, args):
        self.submissions += 1
        return _ImmediateResult(function(*args))

    def close(self):
        pass

    def terminate(self):
        pass

    def join(self):
        pass


class _ImmediateContext:
    def __init__(self):
        self.pool = None

    def Pool(self, *, processes, initializer, initargs):
        self.pool = _ImmediatePool(processes, initializer, initargs)
        return self.pool


def test_unknown_phase_is_per_point_failure_and_pool_continues(
    tmp_path, monkeypatch
):
    database = tmp_path / "unknown-phase-contained.db"
    status = tmp_path / "status.json"
    _prepared_drain_database(database, temperatures=(1200.0, 1250.0, 1300.0))
    context = _ImmediateContext()

    monkeypatch.setattr(grid_pregrind, "_STOP_REQUESTED", False)
    monkeypatch.setattr(
        grid_pregrind.multiprocessing,
        "get_context",
        lambda method: context if method == "spawn" else None,
    )

    def fake_run_point(job):
        output = _output()
        if job.shuffle_rank == 0:
            output["generic"]["phases_present"] = ["future bogus phase"]
            output["generic"]["phase_masses_kg"] = {"future bogus phase": 0.1}
        return job.grid_key_id, output

    monkeypatch.setattr(grid_pregrind, "_run_point", fake_run_point)
    args = SimpleNamespace(
        backend="subprocess",
        workers=2,
        heartbeat_s=60.0,
        limit=None,
        status_json=status,
        seed=178,
        db=database,
        commit_every=10,
        assume_queued_run_mode=None,
        model="MELTSv1.0.2",
        timeout_s=20.0,
        thermoengine_health_timeout_s=8.0,
        thermoengine_equilibrate_timeout_s=60.0,
        allow_zero_component_boundary=False,
    )

    with GridCacheWriter(database, existing_only=True) as writer:
        batch_id = writer.connection.execute(
            "SELECT batch_id FROM batches WHERE label = 'fixed-v2'"
        ).fetchone()[0]
        result = grid_pregrind.run_cycle(
            args,
            writer,
            batch_id=batch_id,
            grid_total=3,
            shard=0,
        )
        rows = writer.connection.execute(
            "SELECT status, status_kind, failure_reason_code, failure_message, "
            "generic_phases_present_json FROM alphamelts_outputs "
            "ORDER BY grid_key_id"
        ).fetchall()

    assert context.pool.submissions == 3
    assert result == {
        "existing": 0,
        "completed": 3,
        "inserted": 3,
        "success": 2,
        "refusal": 0,
        "failure": 1,
    }
    assert rows[0]["status"] == "error"
    assert rows[0]["status_kind"] == "failure"
    assert rows[0]["failure_reason_code"] == "cache_v2_unknown_phase"
    assert "future bogus phase" in rows[0]["failure_message"]
    assert len(rows[0]["failure_message"]) <= 512
    assert json.loads(rows[0]["generic_phases_present_json"] or "[]") == []
    assert [row["status"] for row in rows[1:]] == ["ok", "ok"]


@pytest.mark.parametrize(
    "kind, failure_reason_code",
    [
        ("species", "cache_v2_unknown_species"),
        ("species_activity", "cache_v2_unknown_species"),
        ("phase_affinity", "cache_v2_unknown_phase"),
        ("alphamelts_phase", "cache_v2_unknown_phase"),
        ("alphamelts_species", "cache_v2_unknown_species"),
        ("backend", "cache_v2_unknown_backend"),
        ("backend_and_phase", "cache_v2_unknown_backend"),
        (
            "thermoengine_liquid_endmember",
            "cache_v2_unknown_thermoengine_liquid_endmember",
        ),
    ],
)
def test_unknown_nonphase_dictionary_is_per_point_failure_and_pool_continues(
    tmp_path, monkeypatch, kind, failure_reason_code
):
    database = tmp_path / f"unknown-{kind}-contained.db"
    status = tmp_path / f"status-{kind}.json"
    _prepared_drain_database(database, temperatures=(1200.0, 1250.0, 1300.0))
    context = _ImmediateContext()

    monkeypatch.setattr(grid_pregrind, "_STOP_REQUESTED", False)
    monkeypatch.setattr(
        grid_pregrind.multiprocessing,
        "get_context",
        lambda method: context if method == "spawn" else None,
    )

    def fake_run_point(job):
        output = _output()
        if job.shuffle_rank == 0:
            if kind == "species":
                output["generic"]["vapor_pressures_Pa"] = {
                    "future bogus species": 1.0
                }
                output["generic"]["vapor_pressures_source"] = {
                    "future bogus species": "future:test"
                }
            elif kind == "species_activity":
                output["generic"]["activity_coefficients"] = {
                    "future bogus activity": 1.0
                }
            elif kind == "phase_affinity":
                output["generic"]["phase_affinities"] = {
                    "future bogus phase": {"affinity_J": 1.0}
                }
            elif kind == "alphamelts_phase":
                output["alphamelts"]["phase_masses_kg"] = {
                    "future bogus phase": 1.0
                }
            elif kind == "alphamelts_species":
                output["alphamelts"]["activity_coefficients"] = {
                    "future bogus species": 1.0
                }
            elif kind == "backend":
                output["engine_mode"] = "future bogus backend"
            elif kind == "backend_and_phase":
                output["engine_mode"] = "future bogus backend"
                output["generic"]["phases_present"] = ["future bogus phase"]
            else:
                output["thermoengine"] = {
                    "liquid_activities": {"future bogus endmember": 1.0}
                }
        return job.grid_key_id, output

    monkeypatch.setattr(grid_pregrind, "_run_point", fake_run_point)
    args = SimpleNamespace(
        backend="subprocess",
        workers=2,
        heartbeat_s=60.0,
        limit=None,
        status_json=status,
        seed=178,
        db=database,
        commit_every=10,
        assume_queued_run_mode=None,
        model="MELTSv1.0.2",
        timeout_s=20.0,
        thermoengine_health_timeout_s=8.0,
        thermoengine_equilibrate_timeout_s=60.0,
        allow_zero_component_boundary=False,
    )

    with GridCacheWriter(
        database, existing_only=True, backend_name="subprocess"
    ) as writer:
        batch_id = writer.connection.execute(
            "SELECT batch_id FROM batches WHERE label = 'fixed-v2'"
        ).fetchone()[0]
        result = grid_pregrind.run_cycle(
            args,
            writer,
            batch_id=batch_id,
            grid_total=3,
            shard=0,
        )
        rows = writer.connection.execute(
            "SELECT status, engine_mode, failure_reason_code, failure_message, "
            "generic_phases_present_json, te_liquid_activities_json "
            "FROM alphamelts_outputs ORDER BY grid_key_id"
        ).fetchall()

    assert context.pool.submissions == 3
    assert result == {
        "existing": 0,
        "completed": 3,
        "inserted": 3,
        "success": 2,
        "refusal": 0,
        "failure": 1,
    }
    assert rows[0]["status"] == "error"
    assert rows[0]["failure_reason_code"] == failure_reason_code
    assert rows[0]["engine_mode"] == "subprocess"
    assert "future bogus" in rows[0]["failure_message"]
    assert len(rows[0]["failure_message"]) <= 512
    assert json.loads(rows[0]["generic_phases_present_json"] or "[]") == []
    assert json.loads(rows[0]["te_liquid_activities_json"] or "{}") == {}
    assert [row["status"] for row in rows[1:]] == ["ok", "ok"]


def test_drain_only_uses_prepared_queue_without_importing_fe_redox(
    tmp_path, monkeypatch
):
    database = tmp_path / "prepared.db"
    status = tmp_path / "status.json"
    _prepared_drain_database(database)
    context = _ImmediateContext()
    sys.modules.pop("simulator.fe_redox", None)

    monkeypatch.setattr(grid_pregrind.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(
        grid_pregrind,
        "probe_engine",
        lambda _config: {
            "available": True,
            "engine_version": "fixture-engine",
            "mode": "subprocess",
        },
    )
    monkeypatch.setattr(
        grid_pregrind.multiprocessing,
        "get_context",
        lambda method: context if method == "spawn" else None,
    )

    def fake_run_point(job):
        assert "simulator.fe_redox" not in sys.modules
        assert job.inputs["timeout_s"] == pytest.approx(20.0)
        return job.grid_key_id, _output()

    monkeypatch.setattr(grid_pregrind, "_run_point", fake_run_point)
    monkeypatch.setattr(
        grid_pregrind,
        "kress91_partition_parameters",
        lambda: pytest.fail("drain-only imported or recomputed Kress91 metadata"),
    )
    monkeypatch.setattr(
        grid_pregrind,
        "build_grid_points",
        lambda *_args, **_kwargs: pytest.fail("drain-only generated a grid"),
    )
    monkeypatch.setattr(
        GridCacheWriter,
        "ensure_batch",
        lambda *_args, **_kwargs: pytest.fail("drain-only upserted a batch"),
    )
    monkeypatch.setattr(
        GridCacheWriter,
        "materialize_key",
        lambda *_args, **_kwargs: pytest.fail("drain-only materialized a key"),
    )

    assert (
        grid_pregrind.main(
            [
                "--drain-only",
                "--db",
                str(database),
                "--status-json",
                str(status),
                "--workers",
                "2",
                "--timeout-s",
                "5",
                "--limit",
                "6",
                "--shard",
                "0",
            ]
        )
        == 0
    )
    assert "simulator.fe_redox" not in sys.modules
    assert context.pool.processes == 2
    assert context.pool.initargs[0]["timeout_s"] == pytest.approx(5.0)
    assert context.pool.submissions == 1

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM grid_keys").fetchone()[0] == 1
        assert (
            connection.execute("SELECT COUNT(*) FROM alphamelts_outputs").fetchone()[0]
            == 1
        )
        run_mode, applied_timeout_s = connection.execute(
            "SELECT run_mode, applied_timeout_s FROM alphamelts_outputs"
        ).fetchone()
        metadata = json.loads(
            connection.execute(
                "SELECT value FROM metadata WHERE key = 'last_drain_run'"
            ).fetchone()[0]
        )
    assert metadata["workers"] == 2
    assert run_mode == "isothermal"
    assert applied_timeout_s == pytest.approx(20.0)
    assert metadata["engine_probe"]["engine_version"] == "fixture-engine"
    assert metadata["batch_params_refs"][0]["params_source"] == "batches.params_json"
    assert metadata["batch_params_refs"][0]["kress91_partition"]["version"] == "fixture-v1"


def test_drain_only_refuses_database_without_materialized_queue(tmp_path, monkeypatch):
    database = tmp_path / "empty.db"
    with GridCacheWriter(database):
        pass
    sys.modules.pop("simulator.fe_redox", None)
    monkeypatch.setattr(grid_pregrind.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(
        grid_pregrind,
        "probe_engine",
        lambda _config: pytest.fail("empty drain queue reached the engine probe"),
    )

    with pytest.raises(
        SystemExit,
        match="DRAIN-ONLY REFUSED: database has no materialized queue",
    ):
        grid_pregrind.main(["--drain-only", "--db", str(database)])
    assert "simulator.fe_redox" not in sys.modules


def test_existing_prepared_database_requires_explicit_drain_only(tmp_path, monkeypatch):
    database = tmp_path / "prepared.db"
    _prepared_drain_database(database)
    sys.modules.pop("simulator.fe_redox", None)
    monkeypatch.setattr(grid_pregrind.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(
        grid_pregrind,
        "load_feedstock_box",
        lambda *_args, **_kwargs: pytest.fail("guard allowed grid regeneration"),
    )

    with pytest.raises(SystemExit, match="use --drain-only"):
        grid_pregrind.main(["--db", str(database)])
    assert "simulator.fe_redox" not in sys.modules


def test_writer_round_trip_and_resume_skip(tmp_path):
    database = tmp_path / "grind.db"
    inputs = _inputs(1200.1234567890123)
    key = expedited_key(inputs)

    with GridCacheWriter(database) as writer:
        batch_id = writer.ensure_batch(
            label="fixed", kind="fixed", seed=178, params={"test": True}
        )
        assert writer.materialize_key(
            inputs,
            batch_id=batch_id,
            shuffle_rank=0,
            shard=0,
            intended_fO2_log=-9.0,
        )
        grid_key_id = writer.pending_rows(batch_id=batch_id)[0]["grid_key_id"]
        assert writer.write_result(grid_key_id, _output())
        writer.commit()
        assert writer.existing_keys([key]) == {key}
        assert not writer.write_result(grid_key_id, _output())
        writer.commit()
        assert writer.pending_rows(batch_id=batch_id) == []

        row = writer.connection.execute(
            "SELECT h.temperature_C, h.temperature_C_repr, "
            "h.intended_fO2_log, h.intended_fO2_log_repr, "
            "o.timing_s, o.timing_s_repr, o.raw_payload, "
            "o.generic_liquid_composition_wt_pct_json, "
            "o.generic_requested_temperature_C, "
            "o.generic_liquid_viscosity_Pa_s, "
            "o.generic_liquid_density_kg_m3, o.generic_fO2_log, "
            "o.generic_system_enthalpy, o.generic_system_enthalpy_repr, "
            "o.generic_phase_thermo_json, "
            "o.generic_chem_potentials_json, "
            "o.generic_phase_affinities_json, "
            "o.generic_phase_instances_json, "
            "o.generic_bulk_composition_wt_pct_json, "
            "o.te_liquid_activities_json, o.te_solver_status, "
            "o.alpha_intrinsic_fO2_log "
            "FROM grid_keys h JOIN alphamelts_outputs o ON o.grid_key_id = h.id"
        ).fetchone()
        assert row["temperature_C"] == inputs["temperature_C"]
        assert float(row["temperature_C_repr"]) == inputs["temperature_C"]
        assert row["intended_fO2_log"] == -9.0
        assert row["intended_fO2_log_repr"] == "-9.0"
        assert row["timing_s"] == _output()["timing_s"]
        assert float(row["timing_s_repr"]) == _output()["timing_s"]
        assert json.loads(row["raw_payload"])["stdout"] == "native output\n"
        assert json.loads(row["generic_liquid_composition_wt_pct_json"])[
            "SiO2"
        ] == 66.66666666666667
        assert row["generic_requested_temperature_C"] == 1200.0
        assert row["generic_liquid_viscosity_Pa_s"] == 2.5
        assert row["generic_liquid_density_kg_m3"] == 2650.0
        assert row["generic_fO2_log"] == -9.0
        assert row["generic_system_enthalpy"] == -1059377.10
        assert row["generic_system_enthalpy_repr"] == "-1059377.1"
        assert json.loads(row["generic_phase_thermo_json"])["liquid"][
            "density_kg_m3"
        ] == 2893.824
        assert row["generic_chem_potentials_json"] is None
        assert row["generic_phase_affinities_json"] is None
        phase_instances = json.loads(row["generic_phase_instances_json"])
        assert phase_instances == _output()["generic"]["phase_instances"]
        assert json.loads(row["generic_bulk_composition_wt_pct_json"])[
            "SiO2"
        ] == 49.3753
        assert row["te_liquid_activities_json"] is None
        assert row["te_solver_status"] is None
        assert row["alpha_intrinsic_fO2_log"] == -9.0
        assert writer.counts() == {
            "success": 1,
            "refusal": 0,
            "failure": 0,
            "total": 1,
        }


def test_writer_populates_thermoengine_only_json_without_scalar_padding(tmp_path):
    database = tmp_path / "thermoengine-extras.db"
    output = _output()
    output["engine_mode"] = "thermoengine"
    output["generic"]["chem_potentials"] = {
        "liquid": {
            "basis": "chemical_potential",
            "units": "J/mol",
            "source_basis": "chemical_potential_J_mol",
            "components": {"SiO2": -1234567.8901234567},
        }
    }
    output["generic"]["phase_affinities"] = {
        "quartz": {
            "affinity_J": 321.125,
            "state": "undersaturated",
            "phase_scope": "not_in_equilibrium_assemblage",
            "composition_formula": "SiO2",
        }
    }
    output["generic"]["phase_thermo"]["liquid"].update(
        {"dVdP_m3_bar": -1.25e-10, "dVdT_m3_K": 2.5e-9}
    )
    output["thermoengine"] = {
        "liquid_activities": {"SiO2": 0.73},
        "system_dVdP_m3_bar": -1.25e-10,
        "system_dVdT_m3_K": 2.5e-9,
        "solver_status": "success, Optimal residual norm.",
        "solver_converged": True,
        "solver_iterations": None,
        "solver_iterations_available": False,
        "fO2_solve_count": 1,
        "phase_universe_size": 42,
    }

    with GridCacheWriter(database) as writer:
        batch_id = writer.ensure_batch(
            label="thermoengine", kind="fixed", seed=178, params={"test": True}
        )
        inputs = _inputs(1400.0)
        assert writer.materialize_key(
            inputs,
            batch_id=batch_id,
            shuffle_rank=0,
            shard=0,
            intended_fO2_log=-9.0,
        )
        grid_key_id = writer.pending_rows(batch_id=batch_id)[0]["grid_key_id"]
        assert writer.write_result(grid_key_id, output)
        row = writer.connection.execute(
            "SELECT generic_chem_potentials_json, "
            "generic_phase_affinities_json, generic_phase_thermo_json, "
            "te_liquid_activities_json, te_system_dVdP_m3_bar, "
            "te_system_dVdT_m3_K, te_solver_status, "
            "te_solver_converged, te_solver_iterations, "
            "te_solver_iterations_available, te_fO2_solve_count, "
            "te_phase_universe_size, failure_reason_code, "
            "failure_message FROM alphamelts_outputs"
        ).fetchone()
        sample = writer.sample_row()
        columns = {
            item[1]
            for item in writer.connection.execute(
                'PRAGMA table_info("alphamelts_outputs")'
            )
        }

    assert json.loads(row["generic_chem_potentials_json"]) == {
        "liquid": {
            "basis": "chemical_potential",
            "units": "J/mol",
            "source_basis": "chemical_potential_J_mol",
            "components": {"SiO2": -1234567.8901234567},
        }
    }
    assert json.loads(row["generic_phase_affinities_json"]) == {
        "quartz": {
            "affinity_J": 321.125,
            "state": "undersaturated",
            "phase_scope": "not_in_equilibrium_assemblage",
            "composition_formula": "SiO2",
        }
    }
    assert json.loads(row["generic_phase_thermo_json"])["liquid"] == (
        output["generic"]["phase_thermo"]["liquid"]
    )
    assert json.loads(row["te_liquid_activities_json"]) == {"SiO2": 0.73}
    assert row["te_system_dVdP_m3_bar"] == -1.25e-10
    assert row["te_system_dVdT_m3_K"] == 2.5e-9
    assert row["te_solver_status"] == "success, Optimal residual norm."
    assert row["te_solver_converged"] == 1
    assert row["te_solver_iterations"] is None
    assert row["te_solver_iterations_available"] == 0
    assert row["te_fO2_solve_count"] == 1
    assert row["te_phase_universe_size"] == 42
    assert row["failure_reason_code"] is None
    assert row["failure_message"] is None
    assert sample["intended_fO2_log"] == -9.0
    assert sample["kress91_fixed_ferric_fraction"] == pytest.approx(2.0 / 7.0)
    assert sample["adapter_fO2_log_argument"] is None
    assert sample["solved_fO2_log"] == output["generic"]["fO2_log"]
    assert not any(
        name.startswith("generic_chem_potential_")
        or name.startswith("generic_phase_affinity_")
        for name in columns
    )


def test_writer_surfaces_bounded_failure_diagnostics(tmp_path):
    database = tmp_path / "thermoengine-failure.db"
    message = "ThermoEngine returned a non-finite fO2 echo " + "x" * 600
    output = grid_pregrind._worker_failure_output(
        RuntimeError(message),
        started=grid_pregrind.time.monotonic(),
        captures=[],
        native_input=None,
        backend_name="thermoengine",
    )

    with GridCacheWriter(database, backend_name="thermoengine") as writer:
        batch_id = writer.ensure_batch(
            label="thermoengine-failure",
            kind="fixed",
            seed=178,
            params={"test": True},
        )
        assert writer.materialize_key(
            _inputs(1400.0),
            batch_id=batch_id,
            shuffle_rank=0,
            shard=0,
            intended_fO2_log=-9.0,
        )
        grid_key_id = writer.pending_rows(batch_id=batch_id)[0]["grid_key_id"]
        assert writer.write_result(grid_key_id, output)
        row = writer.connection.execute(
            "SELECT failure_reason_code, failure_message, raw_payload "
            "FROM alphamelts_outputs"
        ).fetchone()
        histogram = writer.selected_result_histogram([grid_key_id])

    assert row["failure_reason_code"] == "thermoengine_nonfinite_fo2_echo"
    assert len(row["failure_message"]) == grid_pregrind.FAILURE_MESSAGE_MAX_LENGTH
    assert json.loads(row["raw_payload"])["exception"]["message"] == message
    assert histogram["failure_reason_code"] == {
        "thermoengine_nonfinite_fo2_echo": 1
    }


def test_writer_refuses_engine_blending_on_open_and_write(tmp_path):
    database = tmp_path / "dedicated-thermoengine.db"
    thermoengine_output = _output()
    thermoengine_output["engine_mode"] = "thermoengine"

    with GridCacheWriter(database, backend_name="thermoengine") as writer:
        batch_id = writer.ensure_batch(
            label="thermoengine", kind="fixed", seed=178, params={"test": True}
        )
        assert writer.materialize_key(
            _inputs(1400.0),
            batch_id=batch_id,
            shuffle_rank=0,
            shard=0,
            intended_fO2_log=-9.0,
        )
        grid_key_id = writer.pending_rows(batch_id=batch_id)[0]["grid_key_id"]
        writer.commit()
        subprocess_writer = GridCacheWriter(
            database, engine_epoch=2, backend_name="subprocess"
        )
        with pytest.raises(ValueError, match="engine blend refused"):
            writer.write_result(grid_key_id, _output())
        assert writer.write_result(grid_key_id, thermoengine_output)
        writer.commit()
        with subprocess_writer:
            with pytest.raises(ValueError, match="engine blend refused"):
                subprocess_writer.write_result(grid_key_id, _output())

    with pytest.raises(ValueError, match="dedicated database"):
        GridCacheWriter(
            database,
            engine_epoch=2,
            existing_only=True,
            backend_name="subprocess",
        )


def test_incremental_harvest_tracks_source_id_and_skips_replay(tmp_path):
    source = tmp_path / "source.db"
    accumulator = tmp_path / "accumulator.db"
    with GridCacheWriter(source) as writer:
        writer.seed_id_block(0)
        batch_id = writer.ensure_batch(
            label="fixed", kind="fixed", seed=178, params={"test": True}
        )
        writer.materialize_key(
            _inputs(1100.0),
            batch_id=batch_id,
            shuffle_rank=0,
            shard=0,
            intended_fO2_log=-9.0,
        )
        writer.materialize_key(
            _inputs(1150.0),
            batch_id=batch_id,
            shuffle_rank=3,
            shard=0,
            intended_fO2_log=-8.0,
        )
        for row in writer.pending_rows(batch_id=batch_id):
            writer.write_result(row["grid_key_id"], _output())
        writer.commit()

    first = harvest_snapshot(source, accumulator, source_host="studio-1")
    second = harvest_snapshot(source, accumulator, source_host="studio-1")

    assert first["pulled"] == first["inserted"] == 2
    assert first["accumulator_total"] == 2
    assert second["pulled"] == second["inserted"] == 0
    assert second["last_seen_after"] == first["last_seen_after"]

    with sqlite3.connect(accumulator) as connection:
        assert connection.execute(
            "SELECT last_seen_id FROM harvest_state "
            "WHERE source_host='studio-1' AND source_table='alphamelts_outputs'"
        ).fetchone()[0] == 1_000_000_001
        assert connection.execute(
            "SELECT MIN(id) FROM alphamelts_outputs"
        ).fetchone()[0] == 1_000_000_000


def _write_harvest_source(path, *, shard, temperature_C, output):
    with GridCacheWriter(path) as writer:
        writer.seed_id_block(shard)
        batch_id = writer.ensure_batch(
            label="fixed", kind="fixed", seed=178, params={"test": True}
        )
        writer.materialize_key(
            _inputs(temperature_C),
            batch_id=batch_id,
            shuffle_rank=shard,
            shard=shard,
            intended_fO2_log=-9.0,
        )
        row = writer.pending_rows(batch_id=batch_id)[0]
        writer.write_result(row["grid_key_id"], output)
        writer.commit()


def test_harvest_reconciles_lower_ids_from_a_later_database(tmp_path):
    high = tmp_path / "high.db"
    low = tmp_path / "low.db"
    accumulator = tmp_path / "accumulator.db"
    _write_harvest_source(high, shard=2, temperature_C=1200.0, output=_output())
    _write_harvest_source(low, shard=0, temperature_C=1100.0, output=_output())

    high_summary = harvest_snapshot(high, accumulator, source_host="studio-1")
    low_summary = harvest_snapshot(low, accumulator, source_host="studio-1")

    assert high_summary["inserted"] == 1
    assert low_summary["inserted"] == 1
    with sqlite3.connect(accumulator) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM alphamelts_outputs"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(DISTINCT source_database) FROM harvest_pulled_rows"
        ).fetchone()[0] == 2


def test_harvest_records_payload_conflict_as_distinct_terminal_state(tmp_path):
    first = tmp_path / "first.db"
    conflicting = tmp_path / "conflicting.db"
    accumulator = tmp_path / "accumulator.db"
    _write_harvest_source(first, shard=0, temperature_C=1200.0, output=_output())
    _write_harvest_source(
        conflicting,
        shard=1,
        temperature_C=1200.0,
        output=_output("refused"),
    )

    harvest_snapshot(first, accumulator, source_host="studio-1")
    conflict_summary = harvest_snapshot(
        conflicting, accumulator, source_host="studio-1"
    )

    assert conflict_summary["canonical_conflicts_recorded"] == 1
    # Conflicts must never inflate the successful-pull counter.
    assert conflict_summary["pulled"] == 0
    assert conflict_summary["inserted"] == 0
    assert conflict_summary["attempted"] == 1
    with sqlite3.connect(accumulator) as connection:
        connection.row_factory = sqlite3.Row
        conflict = connection.execute("SELECT * FROM harvest_conflicts").fetchone()
        assert conflict is not None
        assert json.loads(conflict["incoming_row_json"])["status"] == "refused"
        assert json.loads(conflict["existing_row_json"])["status"] == "ok"
        # Settled conflict advances the window (consumed) without counting as pull.
        receipt = connection.execute(
            "SELECT terminal_state FROM harvest_pulled_rows "
            "WHERE source_database=?",
            (str(conflicting.resolve()),),
        ).fetchone()
        assert receipt is not None
        assert receipt["terminal_state"] == "conflict"


def test_harvest_migrates_legacy_cursor_without_trusting_it(tmp_path):
    source = tmp_path / "source.db"
    accumulator = tmp_path / "accumulator.db"
    _write_harvest_source(source, shard=0, temperature_C=1100.0, output=_output())
    with GridCacheWriter(accumulator) as writer:
        writer.connection.execute(
            "CREATE TABLE harvest_state("
            "source_host TEXT NOT NULL, source_table TEXT NOT NULL, "
            "last_seen_id INTEGER NOT NULL, updated_at TEXT NOT NULL, "
            "PRIMARY KEY(source_host, source_table))"
        )
        writer.connection.execute(
            "INSERT INTO harvest_state VALUES (?, ?, ?, ?)",
            ("studio-1", "alphamelts_outputs", 3_000_000_000, "legacy"),
        )
        writer.commit()

    summary = harvest_snapshot(source, accumulator, source_host="studio-1")

    assert summary["inserted"] == 1
    with sqlite3.connect(accumulator) as connection:
        assert connection.execute(
            "SELECT last_seen_id FROM harvest_state_legacy_v1"
        ).fetchone()[0] == 3_000_000_000


def test_harvest_database_generation_detects_same_path_replacement(tmp_path):
    source = tmp_path / "source.db"
    accumulator = tmp_path / "accumulator.db"
    _write_harvest_source(source, shard=0, temperature_C=1100.0, output=_output())
    with sqlite3.connect(source) as connection:
        first_generation = connection.execute(
            "SELECT value FROM metadata WHERE key='database_id'"
        ).fetchone()[0]
    harvest_snapshot(source, accumulator, source_host="studio-1")

    source.unlink()
    _write_harvest_source(source, shard=0, temperature_C=1100.0, output=_output())
    with sqlite3.connect(source) as connection:
        second_generation = connection.execute(
            "SELECT value FROM metadata WHERE key='database_id'"
        ).fetchone()[0]
    replacement = harvest_snapshot(source, accumulator, source_host="studio-1")

    assert second_generation != first_generation
    assert replacement["pulled"] == 1
    with sqlite3.connect(accumulator) as connection:
        assert connection.execute(
            "SELECT COUNT(DISTINCT source_generation) FROM harvest_state"
        ).fetchone()[0] == 2


def test_harvest_restored_id_reuse_re_reconciles_by_fingerprint(tmp_path):
    """Same generation + reused source id with different content must not vanish.

    Restoring an older DB copy preserves database_id; content-blind pulled-id
    receipts previously treated the reused id as done and dropped the new row.
    """
    source = tmp_path / "source.db"
    accumulator = tmp_path / "accumulator.db"
    _write_harvest_source(source, shard=0, temperature_C=1100.0, output=_output())
    with sqlite3.connect(source) as connection:
        generation = connection.execute(
            "SELECT value FROM metadata WHERE key='database_id'"
        ).fetchone()[0]
        original_id = connection.execute(
            "SELECT id FROM alphamelts_outputs"
        ).fetchone()[0]
        original_fp_status = connection.execute(
            "SELECT status FROM alphamelts_outputs WHERE id = ?",
            (original_id,),
        ).fetchone()[0]

    first = harvest_snapshot(source, accumulator, source_host="studio-1")
    assert first["pulled"] == 1
    assert first["inserted"] == 1
    assert original_fp_status == "ok"

    # Simulate restore that preserves generation + raw id but rewrites payload
    # (content-blind receipts would skip; fingerprint forces re-reconcile).
    with sqlite3.connect(source) as connection:
        connection.execute(
            "UPDATE alphamelts_outputs SET status = ?, status_kind = ?, "
            "refusal_reason = ? WHERE id = ?",
            ("refused", "refusal", "restored_payload", original_id),
        )
        # Generation intentionally unchanged (restore preserves database_id).
        still = connection.execute(
            "SELECT value FROM metadata WHERE key='database_id'"
        ).fetchone()[0]
        assert still == generation
        connection.commit()

    second = harvest_snapshot(source, accumulator, source_host="studio-1")

    # Must not silently report pulled=0 with no conflict.
    assert second["attempted"] == 1
    assert second["pulled"] == 0
    assert second["canonical_conflicts_recorded"] == 1
    with sqlite3.connect(accumulator) as connection:
        connection.row_factory = sqlite3.Row
        conflict = connection.execute("SELECT * FROM harvest_conflicts").fetchone()
        assert conflict is not None
        assert json.loads(conflict["incoming_row_json"])["status"] == "refused"
        # First-wins accumulator retained; conflict is loud, not silent loss.
        assert connection.execute(
            "SELECT status FROM alphamelts_outputs WHERE id = ?",
            (original_id,),
        ).fetchone()[0] == "ok"
        receipt = connection.execute(
            "SELECT terminal_state FROM harvest_pulled_rows "
            "WHERE source_row_id = ?",
            (original_id,),
        ).fetchone()
        assert receipt["terminal_state"] == "conflict"


def _rewrite_source_key_preserving_ids(
    path, *, temperature_C: float, output_status: str = "ok"
) -> str:
    """Simulate a restore: keep database_id + raw ids, change canonical key."""
    with GridCacheWriter(path) as writer:
        connection = writer.connection
        generation = connection.execute(
            "SELECT value FROM metadata WHERE key='database_id'"
        ).fetchone()[0]
        grid = connection.execute(
            "SELECT id, batch_id, shuffle_rank, shard FROM grid_keys"
        ).fetchone()
        out_id = int(
            connection.execute("SELECT id FROM alphamelts_outputs").fetchone()[0]
        )
        values = writer._grid_key_values(
            _inputs(temperature_C),
            batch_id=int(grid["batch_id"]),
            shuffle_rank=int(grid["shuffle_rank"]),
            shard=int(grid["shard"]),
            intended_fO2_log=-9.0,
        )
        assignments = ", ".join(f'"{column}" = ?' for column in values)
        connection.execute(
            f"UPDATE grid_keys SET {assignments} WHERE id = ?",
            (*values.values(), int(grid["id"])),
        )
        connection.execute(
            "UPDATE alphamelts_outputs SET expedited_key = ?, status = ?, "
            "status_kind = ?, refusal_reason = ? WHERE id = ?",
            (
                values["expedited_key"],
                output_status,
                "success" if output_status == "ok" else "refusal",
                None if output_status == "ok" else "restored_payload",
                out_id,
            ),
        )
        still = connection.execute(
            "SELECT value FROM metadata WHERE key='database_id'"
        ).fetchone()[0]
        assert still == generation
        writer.commit()
        return str(values["expedited_key"])


def test_harvest_restore_reconciles_by_canonical_key_not_raw_id(tmp_path):
    """After restore (fingerprint mismatch), id identity is void.

    Reconcile purely by canonical key:
      (a) same-key same-id  → already-pulled / equivalent, no wedge
      (b) new-key reused-id → NEW row under its key, id-reuse audited
      (c) new-key new-id    → NEW row, normal pull
    No abort path may precede this reconciliation.
    """
    source = tmp_path / "source.db"
    accumulator = tmp_path / "accumulator.db"
    _write_harvest_source(source, shard=0, temperature_C=1100.0, output=_output())
    with sqlite3.connect(source) as connection:
        original_id = int(
            connection.execute("SELECT id FROM alphamelts_outputs").fetchone()[0]
        )
        original_key = connection.execute(
            "SELECT expedited_key FROM alphamelts_outputs"
        ).fetchone()[0]
        generation = connection.execute(
            "SELECT value FROM metadata WHERE key='database_id'"
        ).fetchone()[0]

    first = harvest_snapshot(source, accumulator, source_host="studio-1")
    assert first["inserted"] == 1
    assert first["pulled"] == 1

    # (a) same-key same-id: restore rewrites payload-equivalent science at same id.
    # Fingerprint matches → nothing pending; must not abort or double-insert.
    with sqlite3.connect(source) as connection:
        connection.execute(
            "UPDATE alphamelts_outputs SET host = ? WHERE id = ?",
            ("restored-host", original_id),
        )
        assert (
            connection.execute(
                "SELECT value FROM metadata WHERE key='database_id'"
            ).fetchone()[0]
            == generation
        )
        connection.commit()
    case_a = harvest_snapshot(source, accumulator, source_host="studio-1")
    assert case_a["attempted"] == 0
    assert case_a["inserted"] == 0
    assert case_a["pulled"] == 0
    assert case_a["canonical_conflicts_recorded"] == 0
    with sqlite3.connect(accumulator) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM alphamelts_outputs").fetchone()[0]
            == 1
        )

    # (b) new-key reused-id: previously pulled id now carries a different key.
    # Must pull as a NEW row (remap local id), audit id-reuse, never abort.
    new_key_b = _rewrite_source_key_preserving_ids(source, temperature_C=1500.0)
    assert new_key_b != original_key
    with sqlite3.connect(source) as connection:
        reused_id = int(
            connection.execute("SELECT id FROM alphamelts_outputs").fetchone()[0]
        )
        assert reused_id == original_id
        assert (
            connection.execute(
                "SELECT value FROM metadata WHERE key='database_id'"
            ).fetchone()[0]
            == generation
        )

    case_b = harvest_snapshot(source, accumulator, source_host="studio-1")
    assert case_b["attempted"] == 1
    assert case_b["inserted"] == 1
    assert case_b["pulled"] == 1
    # Id-reuse is audited in harvest_conflicts but is not a payload conflict:
    # the new science is retained, so do not inflate the failure counter.
    assert case_b["canonical_conflicts_recorded"] == 0
    with sqlite3.connect(accumulator) as connection:
        connection.row_factory = sqlite3.Row
        assert (
            connection.execute("SELECT COUNT(*) FROM alphamelts_outputs").fetchone()[0]
            == 2
        )
        keys = {
            row[0]
            for row in connection.execute(
                "SELECT expedited_key FROM alphamelts_outputs"
            )
        }
        assert original_key in keys
        assert new_key_b in keys
        remapped = connection.execute(
            "SELECT id, source_row_id, expedited_key FROM alphamelts_outputs "
            "WHERE expedited_key = ?",
            (new_key_b,),
        ).fetchone()
        assert remapped is not None
        assert int(remapped["source_row_id"]) == original_id
        # Local PK remapped away from the reused source id.
        assert int(remapped["id"]) != original_id
        audit = connection.execute(
            "SELECT existing_output_id, expedited_key, "
            "existing_row_json, incoming_row_json FROM harvest_conflicts "
            "WHERE source_row_id = ?",
            (original_id,),
        ).fetchone()
        assert audit is not None
        assert audit["expedited_key"] == new_key_b
        assert int(audit["existing_output_id"]) == original_id
        assert json.loads(audit["existing_row_json"])["expedited_key"] == original_key
        assert json.loads(audit["incoming_row_json"])["expedited_key"] == new_key_b
        receipt = connection.execute(
            "SELECT terminal_state, expedited_key FROM harvest_pulled_rows "
            "WHERE source_row_id = ?",
            (original_id,),
        ).fetchone()
        assert receipt["terminal_state"] == "pulled"
        assert receipt["expedited_key"] == new_key_b

    # (c) new-key new-id: restored source also gains a never-seen id+key.
    with GridCacheWriter(source) as writer:
        batch_id = int(
            writer.connection.execute("SELECT batch_id FROM batches").fetchone()[0]
        )
        writer.materialize_key(
            _inputs(1600.0),
            batch_id=batch_id,
            shuffle_rank=1,
            shard=0,
            intended_fO2_log=-9.0,
        )
        pending = [
            row
            for row in writer.pending_rows(batch_id=batch_id)
            if int(row["grid_key_id"]) != original_id
        ]
        assert len(pending) == 1
        writer.write_result(pending[0]["grid_key_id"], _output())
        new_id_c = int(pending[0]["grid_key_id"])
        new_key_c = str(pending[0]["expedited_key"])
        assert new_id_c != original_id
        assert new_key_c not in {original_key, new_key_b}
        still = writer.connection.execute(
            "SELECT value FROM metadata WHERE key='database_id'"
        ).fetchone()[0]
        assert still == generation
        writer.commit()

    case_c = harvest_snapshot(source, accumulator, source_host="studio-1")
    assert case_c["attempted"] == 1
    assert case_c["inserted"] == 1
    assert case_c["pulled"] == 1
    assert case_c["canonical_conflicts_recorded"] == 0
    with sqlite3.connect(accumulator) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM alphamelts_outputs").fetchone()[0]
            == 3
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM alphamelts_outputs WHERE expedited_key = ?",
                (new_key_c,),
            ).fetchone()[0]
            == 1
        )
        # No wedge: a further pass is idle.
    idle = harvest_snapshot(source, accumulator, source_host="studio-1")
    assert idle["attempted"] == 0
    assert idle["inserted"] == 0
    assert idle["pulled"] == 0


def test_harvest_conflict_limit_does_not_starve_later_rows(tmp_path):
    """Settled conflicts must advance --limit so later source rows are harvested."""
    first = tmp_path / "first.db"
    multi = tmp_path / "multi.db"
    accumulator = tmp_path / "accumulator.db"

    # Canonical key K lands first (wins).
    _write_harvest_source(first, shard=0, temperature_C=1200.0, output=_output())
    harvest_snapshot(first, accumulator, source_host="studio-1")

    # Second source: low-id conflict on same key K, plus a later distinct ok row.
    with GridCacheWriter(multi) as writer:
        writer.seed_id_block(1)
        batch_id = writer.ensure_batch(
            label="fixed", kind="fixed", seed=178, params={"test": True}
        )
        writer.materialize_key(
            _inputs(1200.0),
            batch_id=batch_id,
            shuffle_rank=0,
            shard=1,
            intended_fO2_log=-9.0,
        )
        writer.materialize_key(
            _inputs(1300.0),
            batch_id=batch_id,
            shuffle_rank=1,
            shard=1,
            intended_fO2_log=-9.0,
        )
        pending = sorted(
            writer.pending_rows(batch_id=batch_id),
            key=lambda row: int(row["grid_key_id"]),
        )
        assert len(pending) == 2
        writer.write_result(pending[0]["grid_key_id"], _output("refused"))
        writer.write_result(pending[1]["grid_key_id"], _output())
        writer.commit()
        low_id = int(pending[0]["grid_key_id"])
        high_id = int(pending[1]["grid_key_id"])
        assert low_id < high_id

    # Three limited harvests: conflict must not permanently occupy the window.
    summaries = [
        harvest_snapshot(multi, accumulator, source_host="studio-1", limit=1)
        for _ in range(3)
    ]

    assert summaries[0]["canonical_conflicts_recorded"] == 1
    assert summaries[0]["pulled"] == 0
    assert summaries[0]["attempted"] == 1

    assert summaries[1]["inserted"] == 1
    assert summaries[1]["pulled"] == 1
    assert summaries[1]["canonical_conflicts_recorded"] == 0

    # Third pass is idle — nothing left pending.
    assert summaries[2]["attempted"] == 0
    assert summaries[2]["pulled"] == 0
    assert summaries[2]["inserted"] == 0

    with sqlite3.connect(accumulator) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM alphamelts_outputs"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM harvest_conflicts"
        ).fetchone()[0] == 1
        states = {
            row[0]
            for row in connection.execute(
                "SELECT terminal_state FROM harvest_pulled_rows "
                "WHERE source_database=?",
                (str(multi.resolve()),),
            )
        }
        assert "conflict" in states
        assert "pulled" in states


def test_expedited_key_normalizes_negative_zero_recursively():
    positive = _inputs(0.0)
    negative = _inputs(-0.0)
    negative["composition_mol"] = dict(negative["composition_mol"], TiO2=-0.0)
    negative["composition_mol_by_account"] = {
        "process.cleaned_melt": negative["composition_mol"]
    }
    assert canonical_input_vector(negative) == canonical_input_vector(positive)
    assert expedited_key(negative) == expedited_key(positive)


def test_numeric_expedited_key_prefix_is_a_valid_retry_selector(tmp_path):
    database = tmp_path / "grind.db"
    selected_inputs = None
    selected_key = None
    for offset in range(1000):
        candidate = _inputs(1000.0 + offset)
        key = expedited_key(candidate)
        if key[:3].isdigit():
            selected_inputs = candidate
            selected_key = key
            break
    assert selected_inputs is not None and selected_key is not None

    with GridCacheWriter(database) as writer:
        batch_id = writer.ensure_batch(
            label="fixed", kind="fixed", seed=178, params={"test": True}
        )
        writer.materialize_key(
            selected_inputs,
            batch_id=batch_id,
            shuffle_rank=0,
            shard=0,
        )
        expected = writer.connection.execute(
            "SELECT id FROM grid_keys WHERE expedited_key = ?", (selected_key,)
        ).fetchone()[0]

        assert writer.select_grid_key_ids(selectors=[selected_key[:3]]) == [expected]


def test_numeric_retry_selector_requires_explicit_disambiguation_on_collision(tmp_path):
    database = tmp_path / "grind.db"
    selected_inputs = None
    selected_key = None
    for offset in range(10_000):
        candidate = _inputs(2000.0 + offset)
        key = expedited_key(candidate)
        if key[:3].isdigit() and not key.startswith("0"):
            selected_inputs = candidate
            selected_key = key
            break
    assert selected_inputs is not None and selected_key is not None
    prefix = selected_key[:3]
    row_id = int(prefix)
    unrelated_inputs = _inputs(999.0)
    assert not expedited_key(unrelated_inputs).startswith(prefix)

    with GridCacheWriter(database) as writer:
        batch_id = writer.ensure_batch(
            label="fixed", kind="fixed", seed=178, params={"test": True}
        )
        writer.connection.execute(
            "DELETE FROM sqlite_sequence WHERE name='grid_keys'"
        )
        writer.connection.execute(
            "INSERT INTO sqlite_sequence(name, seq) VALUES ('grid_keys', ?)",
            (row_id - 1,),
        )
        writer.materialize_key(
            unrelated_inputs,
            batch_id=batch_id,
            shuffle_rank=0,
            shard=0,
        )
        writer.materialize_key(
            selected_inputs,
            batch_id=batch_id,
            shuffle_rank=1,
            shard=0,
        )

        assert writer.select_grid_key_ids(selectors=[prefix]) == [row_id]
        assert writer.select_grid_key_ids(selectors=[f"id:{prefix}"]) == [row_id]
        assert writer.select_grid_key_ids(selectors=[f"key:{prefix}"]) == [
            row_id + 1
        ]


def test_selected_retry_rejects_unrelated_sqlite_without_mutating_it(tmp_path):
    database = tmp_path / "unrelated.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sentinel(value TEXT)")
        connection.execute("INSERT INTO sentinel VALUES ('unchanged')")
    before = database.read_bytes()
    args = SimpleNamespace(
        db=database,
        engine_epoch=1,
        keys="1",
        retry_failed=None,
        retry_source_epoch=1,
        retry_limit=None,
    )

    with pytest.raises(ValueError, match="not a grid cache"):
        grid_pregrind.run_selected_retry(args)

    assert database.read_bytes() == before
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall() == [("sentinel",)]


def test_actual_pressure_and_explicit_iron_couple_are_expedited_key_axes():
    base = _inputs(1200.0)
    pressure = dict(base, pressure_bar=0.015)
    ferric = dict(base)
    ferric_composition = dict(base["composition_mol"])
    ferric_composition.update({"Fe2O3": 1.0, "FeO": 3.0})
    ferric["composition_mol"] = ferric_composition
    ferric["composition_mol_by_account"] = {
        "process.cleaned_melt": ferric_composition
    }

    assert expedited_key(base) != expedited_key(pressure)
    assert expedited_key(base) != expedited_key(ferric)


def test_job_runtime_settings_run_mode_sourcing(monkeypatch):
    """Pre-run-mode-era keys need the explicit drain assumption; identity is
    never backfilled, so the assumption is recorded as the source."""
    import scripts.grid_pregrind as gp

    class _Job:
        def __init__(self, inputs):
            self.inputs = inputs

    queued = _Job({"timeout_s": 20.0, "subprocess_run_mode": "isothermal"})
    legacy = _Job({"timeout_s": 20.0})

    monkeypatch.setattr(gp, "_ASSUMED_QUEUED_RUN_MODE", None)
    assert gp._job_runtime_settings(queued) == (20.0, "isothermal", "queued")
    import pytest as _pytest
    with _pytest.raises(ValueError, match="subprocess_run_mode"):
        gp._job_runtime_settings(legacy)

    monkeypatch.setattr(gp, "_ASSUMED_QUEUED_RUN_MODE", "isothermal")
    assert gp._job_runtime_settings(legacy) == (
        20.0,
        "isothermal",
        "drain_assumption",
    )
    # A queued mode always wins over the assumption.
    assert gp._job_runtime_settings(queued) == (20.0, "isothermal", "queued")


def test_observe_hook_binds_to_real_adapter_signature():
    """SC-68 guard: the grinder's observe_subprocess side-effect must accept
    every call shape the REAL adapter's _equilibrate_subprocess can make —
    fakes in this suite cannot catch signature drift, so bind explicitly."""
    import inspect
    from simulator.melt_backend.alphamelts import AlphaMELTSBackend
    import scripts.grid_pregrind as gp

    adapter_sig = inspect.signature(AlphaMELTSBackend._equilibrate_subprocess)
    # Build a kwargs call using every adapter parameter (minus self).
    src = inspect.getsource(gp._run_point)
    hook_src = src[src.find("def observe_subprocess"):]
    hook_params = set(
        p.strip().split(":")[0].split("=")[0].strip().lstrip("*")
        for p in hook_src[hook_src.find("(") + 1 : hook_src.find(") ->")].split(",")
        if p.strip() and not p.strip().startswith("*")
    ) | {"total_input_kg", "diagnostics", "run_mode"}
    adapter_params = set(adapter_sig.parameters) - {"self"}
    missing = adapter_params - hook_params - {"comp_wt"} | (
        {"composition_wt_pct"} - hook_params if "comp_wt" in adapter_params else set()
    )
    assert not (adapter_params - {"self", "comp_wt", "temperature_C", "fO2_log",
                                  "pressure_bar", "warnings", "total_input_kg",
                                  "diagnostics", "run_mode"}), (
        "adapter grew parameters the grinder hook does not know: "
        f"{adapter_params}"
    )
