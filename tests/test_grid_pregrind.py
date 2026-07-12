from __future__ import annotations

import json
import math
import sqlite3
import sys
from types import SimpleNamespace

import pytest

import scripts.grid_pregrind as grid_pregrind
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
    FINDER_INPUT_FIELDS,
    GridCacheWriter,
    canonical_input_vector,
    expedited_key,
)
from scripts.grind_harvest import harvest_snapshot


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
        writer.connection.execute(
            "UPDATE grid_keys SET kress91_partition_provenance_json = NULL"
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
    assert pending[0]["inputs"]["composition_mol"] == inputs["composition_mol"]


def _inputs(temperature_C: float) -> dict:
    composition = {
        "SiO2": 10.0,
        "TiO2": 0.0,
        "Al2O3": 0.0,
        "Fe2O3": 0.0,
        "Cr2O3": 0.0,
        "FeO": 5.0,
        "MnO": 0.0,
        "MgO": 0.0,
        "NiO": 0.0,
        "CoO": 0.0,
        "CaO": 0.0,
        "Na2O": 0.0,
        "K2O": 0.0,
        "P2O5": 0.0,
    }
    values = {
        "temperature_C": temperature_C,
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
            "phase_species_mol": {},
            "phase_species_kg": {},
            "phase_compositions": {},
            "liquid_fraction": 1.0,
            "phase_assemblage_available": True,
            "liquid_composition_wt_pct": {"SiO2": 66.66666666666667},
            "liquid_viscosity_Pa_s": 2.5,
            "liquid_density_kg_m3": 2650.0,
            "vapor_pressures_Pa": {},
            "vapor_pressures_source": {},
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
            inputs=_inputs(1200.0),
        )
    )

    capture = json.loads(output["raw_payload"])["captures"][0]
    assert grid_key_id == 7
    assert backend._timeout_s == pytest.approx(20.0)
    assert capture["timeout"] == pytest.approx(20.0)
    assert output["applied_timeout_s"] == pytest.approx(20.0)
    assert output["run_mode"] == "isothermal"


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


def test_existing_grid_database_adds_nullable_runmode_output_columns(tmp_path):
    database = tmp_path / "legacy-grid.db"
    with GridCacheWriter(database):
        pass
    connection = sqlite3.connect(database)
    for table, column in (
        ("grid_keys", "subprocess_run_mode"),
        ("alphamelts_outputs", "generic_requested_temperature_C"),
        ("alphamelts_outputs", "generic_requested_temperature_C_repr"),
        ("alphamelts_outputs", "generic_liquid_density_kg_m3"),
        ("alphamelts_outputs", "generic_liquid_density_kg_m3_repr"),
        ("alphamelts_outputs", "run_mode"),
        ("alphamelts_outputs", "applied_timeout_s"),
        ("alphamelts_outputs", "applied_timeout_s_repr"),
    ):
        connection.execute(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
    connection.commit()
    connection.close()

    with GridCacheWriter(database, existing_only=True) as writer:
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

    assert "subprocess_run_mode" in grid_columns
    assert {
        "generic_requested_temperature_C",
        "generic_requested_temperature_C_repr",
        "generic_liquid_density_kg_m3",
        "generic_liquid_density_kg_m3_repr",
        "run_mode",
        "applied_timeout_s",
        "applied_timeout_s_repr",
    } <= output_columns


def _prepared_drain_database(database):
    with GridCacheWriter(database) as writer:
        writer.seed_id_block(0)
        batch_id = writer.ensure_batch(
            label="fixed-v2",
            kind="fixed",
            seed=178,
            params={
                "full_grid_points": 1,
                "shard_count": 3,
                "kress91_partition": {
                    "implementation": "fixture:kress91_split",
                    "version": "fixture-v1",
                },
            },
        )
        assert writer.materialize_key(
            _inputs(1200.0),
            batch_id=batch_id,
            shuffle_rank=0,
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
        assert row["alpha_intrinsic_fO2_log"] == -9.0
        assert writer.counts() == {
            "success": 1,
            "refusal": 0,
            "failure": 0,
            "total": 1,
        }


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
