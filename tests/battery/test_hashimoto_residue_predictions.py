from __future__ import annotations

import json
import math
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

import pytest
import yaml

from simulator.battery.residue import (
    _HASHIMOTO_GEOMETRIES,
    _HASHIMOTO_PRIMARY_ALPHA_ARM,
    _HASHIMOTO_PRIMARY_GEOMETRY,
    _hashimoto_primary_policy,
    _predict_hashimoto_residue_cohort,
)


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data/literature/extracts/kems-015-hashimoto-1983.yaml"
STORE = ROOT / "data/literature/extracts-v2/kems-015-hashimoto-1983.yaml"
PRIMARY_TABLE3_PREFIX = "::hashimoto_1983_table3_residue_composition_series::"
OXIDES = ("FeO", "MgO", "SiO2", "CaO", "Al2O3")


def _no_pO2_balance_floor() -> dict[str, float | int | None]:
    return {
        "steps_at_floor": 0,
        "first_step": None,
        "fraction_of_time_at_floor": 0.0,
        "time_at_floor_s": 0.0,
        "pO2_bar": None,
    }


@pytest.fixture(autouse=True)
def _close_openimcc_worker_between_tests():
    from simulator.battery.residue import _close_openimcc_evaluation_worker

    _close_openimcc_evaluation_worker()
    yield
    _close_openimcc_evaluation_worker()


def _hanging_openimcc_engine(**_kwargs):
    import threading

    threading.Event().wait(30)


def _first_openimcc_engine(**_kwargs):
    return "first evaluator"


def _second_openimcc_engine(**_kwargs):
    return "second evaluator"


def _slow_starting_openimcc_worker(connection, evaluate):
    import time

    time.sleep(0.2)
    from simulator.battery.residue import _openimcc_evaluation_worker

    _openimcc_evaluation_worker(connection, evaluate)


def _fake_openimcc_bridge_result(**_kwargs):
    from simulator.melt_backend.openimcc_bridge import OpenImccBridgeResult
    from simulator.melt_backend.imcc_adapter_labels import ImccAdapterLabels

    return OpenImccBridgeResult(
        parent_oxide_activities={"SiO2": 0.75, "MgO": 0.25},
        parent_oxides=("SiO2", "MgO"),
        flags=("fixture-flag",),
        notices=("fixture-notice",),
        acid_sink_ratio=0.125,
        pack_model_id="fixture-model",
        pack_version="fixture-version",
        pack_digest="fixture-digest",
        openimcc_version="fixture-openimcc",
        envelope_status="in_domain",
        extrapolated=False,
        labels=ImccAdapterLabels(
            identity={"model_id": "fixture-model"},
            coverage={"SiO2": "covered"},
            trust="internal-analytical",
            envelope_status="in_domain",
            flags=("fixture-flag",),
            notices=("fixture-notice",),
            acid_sink_ratio=0.125,
        ),
        coverage={"SiO2": "covered"},
        activity_coefficients={
            "Si": {
                "value": Decimal("1.25"),
                "coefficient_basis": "single_cation",
                "standard_state": {"convention": "raoultian_pure_endmember"},
            }
        },
        parent_oxide_x_star_ratios={"SiO2": 0.875},
    )


def test_openimcc_bridge_result_round_trips_through_isolated_worker():
    from simulator.battery.residue import _evaluate_openimcc_isolated

    direct = _fake_openimcc_bridge_result()
    isolated = _evaluate_openimcc_isolated(
        _fake_openimcc_bridge_result, timeout_s=10.0
    )

    assert isolated == direct
    assert set(isolated.to_transport()) == {
        "parent_oxide_activities",
        "parent_oxides",
        "flags",
        "notices",
        "acid_sink_ratio",
        "pack_model_id",
        "pack_version",
        "pack_digest",
        "openimcc_version",
        "envelope_status",
        "extrapolated",
        "labels",
        "coverage",
        "activity_coefficients",
        "parent_oxide_x_star_ratios",
    }
    assert isinstance(isolated.parent_oxide_activities, MappingProxyType)
    assert isinstance(isolated.coverage, MappingProxyType)
    assert isinstance(isolated.activity_coefficients, MappingProxyType)
    assert isinstance(isolated.parent_oxide_x_star_ratios, MappingProxyType)


def test_real_openimcc_bridge_result_round_trips_when_available():
    pytest.importorskip("openimcc")
    from simulator.battery.residue import _evaluate_openimcc_isolated
    from simulator.melt_backend import openimcc_bridge

    kwargs = {
        "composition_mol": {"SiO2": 1.0, "MgO": 1.0},
        "temperature_K": 1800.0,
        "allow_extrapolation": True,
        "allow_out_of_envelope": True,
    }
    direct = openimcc_bridge.evaluate(**kwargs)
    isolated = _evaluate_openimcc_isolated(
        openimcc_bridge.evaluate, timeout_s=30.0, **kwargs
    )

    assert isolated == direct


def test_isolated_openimcc_worker_still_times_out():
    from simulator.battery.residue import _evaluate_openimcc_isolated

    with pytest.raises(TimeoutError, match="timed out"):
        _evaluate_openimcc_isolated(_hanging_openimcc_engine, timeout_s=0.05)


def test_isolated_openimcc_worker_startup_is_outside_call_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import simulator.battery.residue as residue

    monkeypatch.setattr(
        residue, "_openimcc_evaluation_worker", _slow_starting_openimcc_worker
    )
    first = residue._evaluate_openimcc_isolated(
        _first_openimcc_engine, timeout_s=0.05
    )
    process = residue._OPENIMCC_EVALUATION_WORKER[0]
    second = residue._evaluate_openimcc_isolated(
        _first_openimcc_engine, timeout_s=0.05
    )

    assert first == second == "first evaluator"
    assert residue._OPENIMCC_EVALUATION_WORKER[0] is process


def test_isolated_openimcc_worker_restarts_for_a_different_evaluator() -> None:
    from simulator.battery.residue import _evaluate_openimcc_isolated

    assert _evaluate_openimcc_isolated(
        _first_openimcc_engine, timeout_s=5.0
    ) == "first evaluator"
    assert _evaluate_openimcc_isolated(
        _second_openimcc_engine, timeout_s=5.0
    ) == "second evaluator"


def test_isolated_openimcc_worker_start_failure_is_typed_and_recoverable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import multiprocessing

    import simulator.battery.residue as residue

    spawn_context = multiprocessing.get_context("spawn")
    connections = []
    started_pids = []
    stopped_before_close = []

    class ContextProxy:
        def Pipe(self, **kwargs):
            pair = spawn_context.Pipe(**kwargs)
            connections.extend(pair)
            return pair

        def Process(self, **kwargs):
            process = spawn_context.Process(**kwargs)
            if not stopped_before_close:
                class ProcessProxy:
                    def __init__(self, delegate):
                        self._delegate = delegate

                    def __getattr__(self, name):
                        return getattr(self._delegate, name)

                    def start(self):
                        self._delegate.start()
                        started_pids.append(self._delegate.pid)
                        raise OSError(24, "forced descriptor exhaustion")

                    def close(self):
                        stopped_before_close.append(
                            not self._delegate.is_alive()
                        )
                        self._delegate.close()

                return ProcessProxy(process)
            return process

    monkeypatch.setattr(
        residue.multiprocessing, "get_context", lambda _method: ContextProxy()
    )

    with pytest.raises(residue.ResidueEngineTransportError) as exc_info:
        residue._evaluate_openimcc_isolated(_first_openimcc_engine, timeout_s=5.0)

    assert exc_info.value.reason == "engine_transport_error:openimcc"
    assert len(connections) == 2
    assert all(connection.closed for connection in connections)
    assert len(started_pids) == 1
    assert started_pids[0] is not None
    assert stopped_before_close == [True]
    assert residue._OPENIMCC_EVALUATION_WORKER is None

    assert residue._evaluate_openimcc_isolated(
        _first_openimcc_engine, timeout_s=5.0
    ) == "first evaluator"


def _source_data():
    source = yaml.safe_load(SOURCE.read_text())
    store = yaml.safe_load(STORE.read_text())
    measured_rows = [
        observation
        for observation in store["observations"]
        if PRIMARY_TABLE3_PREFIX in observation["observation_id"]
        and observation["value"].get("kind") == "point"
    ]
    experiment_ids = sorted(
        {
            observation["experiment_id"].split("::experiment::")[-1]
            for observation in measured_rows
        }
    )
    experiments_by_id = {
        experiment["experiment_id"]: experiment
        for experiment in source["experiments"]
    }
    experiments = [experiments_by_id[experiment_id] for experiment_id in experiment_ids]
    geometry_record = next(
        item
        for item in source["metadata"]
        if item["observation_id"] == "hashimoto_1983_fe_fcmas_free_evap_geometry_quoted"
    )
    return (
        experiments,
        measured_rows,
        geometry_record["values"]["starting_preform_mm"],
    )


def test_hashimoto_cohort_has_complete_vectors_bands_and_value_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("openimcc")
    import simulator.battery.residue as residue
    from simulator.melt_backend import openimcc_bridge

    monkeypatch.setattr(
        openimcc_bridge, "DEFAULT_BACKEND_PACK_NAME", "published-core"
    )
    from openimcc import load_gas_datapack

    # Keep the 24-vector provenance/arm mapping test fast; real refined physics
    # for one vector is covered by the activity-refresh test below.
    def integrate_at_steps(
        inventory,
        _channels,
        _pressure_model,
        *,
        temperature_K,
        duration_s,
        steps,
        geometry_policy_id,
        initial_area_m2,
    ):
        values = residue._hashimoto_project_oxide_wt_pct(inventory)
        shift = 0.8 / steps
        values["FeO"] += shift
        values["MgO"] -= shift
        return (
            values,
            (0.1,),
            (),
            None,
            {"Fe": 0.0, "O": 0.0},
            _no_pO2_balance_floor(),
        )

    monkeypatch.setattr(residue, "_hashimoto_integrate_geometry", integrate_at_steps)
    experiments, measured_rows, preform_dimensions_mm = _source_data()
    assert len(experiments) == 24
    assert len(measured_rows) == 120
    assert all(
        sum(row["experiment_id"].endswith(experiment["experiment_id"]) for row in measured_rows)
        == 5
        for experiment in experiments
    )
    catalog = yaml.safe_load((ROOT / "data/vapor_pressures.yaml").read_text())
    predictions = _predict_hashimoto_residue_cohort(
        experiments,
        catalog,
        preform_dimensions_mm=preform_dimensions_mm,
        code_revision="test-revision",
    )

    assert len(predictions) == 48
    primary = [row for row in predictions if row.alpha_arm == _HASHIMOTO_PRIMARY_ALPHA_ARM]
    runtime = [row for row in predictions if row.alpha_arm == "alpha_runtime_catalog"]
    assert len(primary) == len(runtime) == 24
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _OpenImccBatteryBackend,
    )

    residue_identity = primary[0].provenance["engine_binding_identity"]
    expected_pack = openimcc_bridge._load_pack("published-core")
    expected_package_identity = openimcc_bridge.engine_binding_identity(
        expected_pack, load_gas_datapack()
    )
    assert residue_identity["melt_binding_digest"] == (
        expected_package_identity["melt_binding_digest"]
    )
    assert set(residue_identity) == {
        "engine_binding_digest",
        "melt_binding_digest",
        "condensate_table_digest",
        "gas_table_digest",
    }
    assert all(
        row.provenance["engine_binding_identity"] == residue_identity
        for row in predictions
    )
    assert not {
        "gas_table_sha256",
        "condensate_table_sha256",
    } & set(primary[0].provenance["pack_digest"])
    binary_backend = _OpenImccBatteryBackend("openimcc")
    assert binary_backend._identity["engine_binding_identity"] == residue_identity
    assert sum(len(row.primary_oxide_wt_pct) for row in primary) == 120
    assert all(tuple(row.geometry_oxide_wt_pct) == _HASHIMOTO_GEOMETRIES for row in predictions)

    required_provenance = {
        "experiment_id",
        "source_id",
        "run_locator",
        "temperature_K",
        "duration_s",
        "sample_mass_kg",
        "starting_composition_wt_pct",
        "starting_preform_mm",
        "sample_surface_area_status",
        "primary_geometry_policy_id",
        "geometry_primary_argument",
        "geometry_policies",
        "geometry_policy_id",
        "area_m2_initial",
        "area_evolution",
        "density_kg_m3",
        "density_source",
        "alpha_arm",
        "alpha_source",
        "author_alpha_assumption",
        "alpha_by_channel",
        "runtime_catalog_omissions",
        "oxygen_model",
        "oxygen_boundary",
        "total_pressure_Pa",
        "surface_pO2_bar_range_by_geometry",
        "surface_pO2_balance_floor",
        "surface_pO2_balance_floor_by_geometry",
        "engine_floor_inversion_diagnostics",
        "liquid_rows_used",
        "d060_pending",
        "datapack_version",
        "pack_digest",
        "code_revision",
        "integration",
        "component_exhausted_by_geometry",
        "atom_closure_mol_by_geometry",
        "geometry_refusal_by_geometry",
        "run_refusal",
        "prediction_status",
        "assumption_flags",
    }
    for prediction in predictions:
        assert set(prediction.primary_oxide_wt_pct) == set(OXIDES)
        assert set(prediction.sensitivity_band_wt_pct) == set(OXIDES)
        assert prediction.primary_geometry_policy_id == _HASHIMOTO_PRIMARY_GEOMETRY
        assert prediction.primary_oxide_wt_pct == prediction.geometry_oxide_wt_pct[
            _HASHIMOTO_PRIMARY_GEOMETRY
        ]
        provenance = prediction.provenance
        assert required_provenance <= set(provenance)
        assert provenance["source_id"] == "kems-015-hashimoto-1983"
        assert provenance["sample_mass_kg"] == pytest.approx(1.0e-4)
        assert provenance["starting_preform_mm"] == preform_dimensions_mm
        assert provenance["sample_surface_area_status"] == "not_tabulated"
        assert provenance["primary_geometry_policy_id"] == _HASHIMOTO_PRIMARY_GEOMETRY
        assert "without using residue observations" in provenance["geometry_primary_argument"]
        assert provenance["density_kg_m3"] == 2700.0
        assert provenance["density_source"]
        assert provenance["d060_pending"] is True
        assert provenance["code_revision"] == "test-revision"
        integration = provenance["integration"]
        assert integration["refinement_status"] == "converged"
        assert set(integration["refinement_status_by_geometry"]) == set(
            _HASHIMOTO_GEOMETRIES
        )
        assert set(integration["steps_tried_by_geometry"]) == set(
            _HASHIMOTO_GEOMETRIES
        )
        assert set(integration["max_difference_wt_pct_by_geometry"]) == set(
            _HASHIMOTO_GEOMETRIES
        )
        assert set(integration["process_cpu_seconds_by_geometry"]) == set(
            _HASHIMOTO_GEOMETRIES
        )
        assert all(
            steps and steps[-1] == integration["accepted_steps_by_geometry"][geometry]
            and steps[-1] <= 256
            for geometry, steps in integration["steps_tried_by_geometry"].items()
        )
        assert all(
            len(steps) >= 2
            and len(integration["max_difference_wt_pct_by_geometry"][geometry])
            == len(steps) - 1
            for geometry, steps in integration["steps_tried_by_geometry"].items()
        )
        assert all(value >= 0 for value in integration["process_cpu_seconds_by_geometry"].values())
        assert provenance["notices"] == integration["refinement_notices"]
        assert "sqrt(binary64 epsilon)" in provenance["integration"][
            "component_exhaustion_rule"
        ]
        assert "16 ULPs" in provenance["integration"]["component_exhaustion_rule"]
        assert provenance["integration"]["component_exhaustion_floor_mol"] > 0.0
        assert "two half steps" in provenance["integration"][
            "engine_nonconvergence_retry"
        ]
        assert set(provenance["component_exhausted_by_geometry"]) == set(
            _HASHIMOTO_GEOMETRIES
        )
        assert set(provenance["atom_closure_mol_by_geometry"]) == set(
            _HASHIMOTO_GEOMETRIES
        )
        assert set(provenance["geometry_refusal_by_geometry"]) == set(
            _HASHIMOTO_GEOMETRIES
        )
        expected_run_refusal = provenance["geometry_refusal_by_geometry"][
            _HASHIMOTO_PRIMARY_GEOMETRY
        ] or next(
            (
                refusal
                for refusal in provenance["geometry_refusal_by_geometry"].values()
                if refusal
            ),
            None,
        )
        assert provenance["run_refusal"] == expected_run_refusal
        any_refusal = any(provenance["geometry_refusal_by_geometry"].values())
        assert provenance["prediction_status"] == (
            "partial_diagnostic" if any_refusal else "complete"
        )
        for geometry in _HASHIMOTO_GEOMETRIES:
            assert max(
                abs(value)
                for value in provenance["atom_closure_mol_by_geometry"][geometry].values()
            ) < 1.0e-12
        assert provenance["oxygen_model"].startswith("R1a engine-consistent")
        assert provenance["oxygen_boundary"] == "unbuffered vacuum; fO2 control none"
        assert set(provenance["surface_pO2_balance_floor_by_geometry"]) == set(
            _HASHIMOTO_GEOMETRIES
        )
        assert provenance["surface_pO2_balance_floor"] == {
            "steps_at_floor": 0,
            "first_step": None,
            "fraction_of_time_at_floor": 0.0,
            "time_at_floor_s": 0.0,
            "pO2_bar": None,
        }
        assert set(provenance["geometry_policies"]) == set(_HASHIMOTO_GEOMETRIES)
        flags = provenance["assumption_flags"]
        assert flags["area_assumed_sphere_constant"] is True
        assert flags["area_assumed_sphere_shrinking"] is True
        assert flags["area_assumed_disk_4mm_constant"] is True
        assert flags["density_fallback_2700_kg_m3"] is True
        assert flags["oxygen_unbuffered_vacuum"] is True
        assert flags["feo_melt_redox_stack2_pending"] is True
        assert flags["d060_pending"] is True
        for oxide in OXIDES:
            geometry_values = [
                prediction.geometry_oxide_wt_pct[geometry][oxide]
                for geometry in _HASHIMOTO_GEOMETRIES
            ]
            assert prediction.sensitivity_band_wt_pct[oxide] == pytest.approx(
                (min(geometry_values), max(geometry_values))
            )
        liquid_rows = provenance["liquid_rows_used"]
        assert {row["channel"] for row in liquid_rows} >= {"Fe", "Mg", "SiO", "Ca", "Al", "O", "O2"}
        assert all("openimcc_gas_row" in row and "gas_source" in row for row in liquid_rows)
        assert all("liquid_row" in row for row in liquid_rows)

    common_by_run = {row.experiment_id: row for row in primary}
    runtime_by_run = {row.experiment_id: row for row in runtime}
    assert set(common_by_run) == set(runtime_by_run)
    assert all(
        common_by_run[run_id].provenance["assumption_flags"][
            "alpha_assumed_unity_ratio_not_measured_alpha"
        ]
        for run_id in common_by_run
    )
    assert all(
        not runtime_by_run[run_id].provenance["assumption_flags"][
            "alpha_assumed_unity_ratio_not_measured_alpha"
        ]
        and runtime_by_run[run_id].provenance["assumption_flags"][
            "alpha_assumed_runtime_catalog_system_mismatch"
        ]
        for run_id in common_by_run
    )
    fe_runtime_alpha = next(
        record["alpha_value"]
        for record in runtime_by_run[next(iter(runtime_by_run))].provenance["alpha_by_channel"]
        if record["channel"] == "Fe"
    )
    assert math.isclose(fe_runtime_alpha, 0.02, rel_tol=1.0e-12)


def test_hashimoto_without_melt_pack_identity_has_no_sf04_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("openimcc")
    import simulator.battery.residue as residue

    def integrate_at_steps(inventory, _channels, _pressure_model, *, steps, **_kwargs):
        values = residue._hashimoto_project_oxide_wt_pct(inventory)
        shift = 0.8 / steps
        values["FeO"] += shift
        values["MgO"] -= shift
        return (
            values,
            (0.1,),
            (),
            None,
            {"Fe": 0.0, "O": 0.0},
            _no_pO2_balance_floor(),
        )

    monkeypatch.setattr(residue, "_hashimoto_integrate_geometry", integrate_at_steps)
    experiments, _measured_rows, preform_dimensions_mm = _source_data()
    catalog = yaml.safe_load((ROOT / "data/vapor_pressures.yaml").read_text())
    predictions = _predict_hashimoto_residue_cohort(
        [experiments[0]],
        catalog,
        preform_dimensions_mm=preform_dimensions_mm,
        code_revision="test-revision",
        engine="internal-analytical",
        _allow_partial_cohort_for_test=True,
    )

    provenance = predictions[0].provenance
    assert provenance["datapack_version"] is None
    assert provenance["openimcc_model_id"] is None


def test_hashimoto_internal_probe_diagnostic_does_not_survive_channel_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from simulator.diagnostic_helpers import binary_pot_battery
    import simulator.battery.residue as residue

    detail = {
        "reason": "melt_dissociation_pO2_floor_inverted_through_mass_action",
        "pO2_bar_used": 0.25,
        "certified_band": {"pO2_bar": [0.1, 1.0]},
    }
    diagnostic_calls = []

    def report_floor_diagnostic(**kwargs):
        diagnostic_calls.append(kwargs)
        return SimpleNamespace(
            vapor_pressures_Pa={},
            diagnostics={
                "vapor_pressure_diagnostic": {
                    "pO2_floor_inversion_notices_by_species": {"Mg": detail}
                }
            },
        )

    monkeypatch.setattr(
        binary_pot_battery, "_new_internal_analytical_core", lambda: object()
    )
    monkeypatch.setattr(
        binary_pot_battery,
        "_internal_analytical_vapor_pressure_adapter",
        report_floor_diagnostic,
    )
    experiments, _measured_rows, preform_dimensions_mm = _source_data()
    catalog = yaml.safe_load((ROOT / "data/vapor_pressures.yaml").read_text())

    with pytest.raises(residue.ResidueInventoryRefusal) as exc_info:
        residue._predict_hashimoto_residue_cohort(
            experiments[:1],
            catalog,
            preform_dimensions_mm=preform_dimensions_mm,
            code_revision="floor-diagnostic-test",
            engine="internal-analytical",
            _allow_partial_cohort_for_test=True,
        )

    assert exc_info.value.reason == "hashimoto_internal_analytical_channels_missing"
    assert diagnostic_calls
    assert all(call["include_diagnostic_shadows"] is False for call in diagnostic_calls)
    refusal_detail = json.loads(exc_info.value.detail)
    assert refusal_detail["engine_floor_inversion_diagnostics"] == []


def test_hashimoto_refinement_rechecks_activities_for_changed_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("openimcc")
    import simulator.battery.residue as residue
    from simulator.melt_backend import openimcc_bridge

    experiments, _measured_rows, preform_dimensions_mm = _source_data()
    catalog = yaml.safe_load((ROOT / "data/vapor_pressures.yaml").read_text())
    activity_compositions: list[tuple[tuple[str, float], ...]] = []
    original_evaluate = openimcc_bridge.evaluate

    def track_activities(*, composition_mol, **kwargs):
        activity_compositions.append(tuple(sorted(composition_mol.items())))
        return original_evaluate(composition_mol=composition_mol, **kwargs)

    monkeypatch.setattr(
        residue,
        "_evaluate_openimcc_isolated",
        lambda evaluate, *, timeout_s, **kwargs: evaluate(**kwargs),
    )
    monkeypatch.setattr(openimcc_bridge, "evaluate", track_activities)
    predictions = _predict_hashimoto_residue_cohort(
        experiments[:1],
        catalog,
        preform_dimensions_mm=preform_dimensions_mm,
        code_revision="single-vector-activity-refresh-test",
        _allow_partial_cohort_for_test=True,
    )

    assert len(predictions) == 2
    assert len(set(activity_compositions)) > 1
    assert all(
        set(prediction.provenance["integration"]["refinement_status_by_geometry"])
        == set(_HASHIMOTO_GEOMETRIES)
        for prediction in predictions
    )


def test_hashimoto_refinement_flags_the_finest_run_at_the_step_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("openimcc")
    import copy
    import simulator.battery.residue as residue

    def never_converges(
        inventory,
        _channels,
        _pressure_model,
        *,
        temperature_K,
        duration_s,
        steps,
        geometry_policy_id,
        initial_area_m2,
    ):
        values = residue._hashimoto_project_oxide_wt_pct(inventory)
        shift = steps / 1000.0
        values["FeO"] += shift
        values["MgO"] -= shift
        return (
            values,
            (0.1,),
            (),
            None,
            {"Fe": 0.0, "O": 0.0},
            _no_pO2_balance_floor(),
        )

    monkeypatch.setattr(residue, "_hashimoto_integrate_geometry", never_converges)
    experiments, _measured_rows, preform_dimensions_mm = _source_data()
    capped_experiments = copy.deepcopy(experiments)
    for experiment in capped_experiments:
        experiment["thermal_schedule"]["total_duration_s"]["state"]["value"][
            "point"
        ] = "12000"
    catalog = yaml.safe_load((ROOT / "data/vapor_pressures.yaml").read_text())

    predictions = residue._predict_hashimoto_residue_cohort(
        capped_experiments,
        catalog,
        preform_dimensions_mm=preform_dimensions_mm,
        code_revision="injected-cap-refinement-test",
    )

    assert len(predictions) == 48
    for prediction in predictions:
        integration = prediction.provenance["integration"]
        assert set(integration["refinement_status_by_geometry"].values()) == {
            "unconverged_at_cap"
        }
        assert set(integration["accepted_steps_by_geometry"].values()) == {256}
        assert all(
            steps[-1] == 256 and max(steps) <= 256
            for steps in integration["steps_tried_by_geometry"].values()
        )
        assert all(
            notice["reason"] == "residue_time_refinement_unconverged"
            for notice in prediction.provenance["notices"]
        )
        assert len(prediction.provenance["notices"]) == len(_HASHIMOTO_GEOMETRIES)
        assert {
            notice["geometry_policy_id"]
            for notice in prediction.provenance["notices"]
        } == set(_HASHIMOTO_GEOMETRIES)
        assert prediction.provenance["prediction_status"] == "complete"


def test_hashimoto_engine_refusal_flags_one_run_and_keeps_cohort_moving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("openimcc")
    import simulator.battery.residue as residue

    experiments, _measured_rows, preform_dimensions_mm = _source_data()
    catalog = yaml.safe_load((ROOT / "data/vapor_pressures.yaml").read_text())
    failures_remaining = 6

    def pressure_step(
        inventory,
        _channels,
        _pressure_model,
        *,
        temperature_K,
        duration_s,
        area_evolution_m2,
    ):
        nonlocal failures_remaining
        if failures_remaining:
            failures_remaining -= 1
            raise residue.ResidueEngineNonconvergence(
                "openimcc", "injected per-run engine nonconvergence"
            )
        return residue.ResidueInventoryResult(
            residue_mol=dict(inventory),
            evaporated_mol={},
            pO2_bar_by_step=(None,),
            pO2_balance_bracket_boundary_by_step=(None,),
            pO2_balance_floor_steps=0,
            pO2_balance_floor_first_step=None,
            pO2_balance_floor_time_fraction=0.0,
            pO2_balance_floor_time_s=0.0,
            pO2_balance_floor_pO2_bar=None,
            atom_closure_mol={},
        )

    monkeypatch.setattr(residue, "integrate_residue_inventory", pressure_step)
    predictions = residue._predict_hashimoto_residue_cohort(
        experiments,
        catalog,
        preform_dimensions_mm=preform_dimensions_mm,
        code_revision="injected-nonconvergence-test",
    )

    assert len(predictions) == 48
    assert failures_remaining == 0
    partial = [
        prediction
        for prediction in predictions
        if prediction.provenance["prediction_status"] == "partial_diagnostic"
    ]
    assert len(partial) == 1
    refusal = partial[0].provenance["run_refusal"]
    assert refusal["reason"] == "engine_nonconvergence"
    assert refusal["geometry_policy_id"] == _HASHIMOTO_PRIMARY_GEOMETRY
    assert refusal["composition_mol"]
    assert refusal["step"] == 1
    assert refusal["time_reached_s"] == 0.0
    assert sum(
        len(prediction.primary_oxide_wt_pct)
        for prediction in predictions
        if prediction.alpha_arm == _HASHIMOTO_PRIMARY_ALPHA_ARM
    ) == 120
    assert all(
        prediction.provenance["prediction_status"] == "complete"
        for prediction in predictions
        if prediction is not partial[0]
    )


def test_hashimoto_primary_policy_cannot_choose_the_arm_closest_to_residues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_read_text = Path.read_text

    def block_measured_rows(path: Path, *args, **kwargs):
        if "extracts-v2/kems-015-hashimoto-1983.yaml" in str(path):
            raise AssertionError("primary policy attempted to read measured residues")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", block_measured_rows)
    # This adversarial ordering represents a closest-to-data mutation that would
    # prefer catalog/disk. R2's declared choice is fixed before observations load.
    closest_to_data = ("alpha_runtime_catalog", "disk_4mm_constant")
    assert closest_to_data != _hashimoto_primary_policy()
    assert _hashimoto_primary_policy() == (
        _HASHIMOTO_PRIMARY_ALPHA_ARM,
        _HASHIMOTO_PRIMARY_GEOMETRY,
    )


def test_hashimoto_openimcc_timeout_is_typed_and_cohort_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("openimcc")
    import simulator.battery.residue as residue
    from simulator.diagnostic_helpers import binary_pot_battery
    from simulator.melt_backend import openimcc_bridge

    experiments, _measured_rows, preform_dimensions_mm = _source_data()
    experiment = experiments[0]
    catalog = yaml.safe_load((ROOT / "data/vapor_pressures.yaml").read_text())
    monkeypatch.setitem(binary_pot_battery._ENGINE_OUTER_TIMEOUT_S, "openimcc", 0.02)

    monkeypatch.setattr(openimcc_bridge, "evaluate", _hanging_openimcc_engine)

    def integrate_with_engine_call(
        inventory,
        _channels,
        pressure_model,
        *,
        temperature_K,
        duration_s,
        steps,
        geometry_policy_id,
        initial_area_m2,
    ):
        try:
            pressure_model(inventory, -9.0)
        except residue.ResidueEngineNonconvergence as exc:
            refusal = exc
        else:
            refusal = None
        return (
            residue._hashimoto_project_oxide_wt_pct(inventory),
            (),
            (),
            refusal,
            {},
            _no_pO2_balance_floor(),
        )

    monkeypatch.setattr(
        residue, "_hashimoto_integrate_geometry", integrate_with_engine_call
    )
    predictions = residue._predict_hashimoto_residue_cohort(
        (experiment,),
        catalog,
        preform_dimensions_mm=preform_dimensions_mm,
        code_revision="timeout-fixture",
        _allow_partial_cohort_for_test=True,
    )

    assert {row.alpha_arm for row in predictions} == {
        "alpha_common_unity_sensitivity",
        "alpha_runtime_catalog",
    }
    assert all(
        row.provenance["run_refusal"]["reason"]
        == "engine_timeout:openimcc:hashimoto_residue_cohort"
        for row in predictions
    )
    assert all(
        "Hashimoto residue cohort OpenIMCC timeout" in row.provenance["run_refusal"]["message"]
        for row in predictions
    )
