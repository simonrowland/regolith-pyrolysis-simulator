from __future__ import annotations

import math
import re
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from simulator.battery.residue import (
    _SOSSI_GEOMETRIES,
    _sossi_geometry_areas,
    _sossi_initial_inventory,
)


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data/literature/extracts/kems-012-sossi-2019.yaml"


def test_sossi_start_inventory_contains_table1_host_and_starting_trace() -> None:
    source = yaml.safe_load(SOURCE.read_text())
    experiment = source["experiments"][0]
    starting_trace_ppm = {
        element: source["species"][element]["observations"][0]["values"][
            "starting_measured_ppm"
        ]
        for element in ("Mn", "Ti")
    }
    inventory, sample_mass_kg, inventory_mass_kg = _sossi_initial_inventory(
        experiment, starting_trace_ppm
    )

    assert sample_mass_kg == 25.0e-6
    assert {
        "SiO2",
        "Al2O3",
        "MgO",
        "FeO",
        "CaO",
        "MnO",
        "TiO2",
    } <= set(inventory)
    assert inventory["MnO"] > 0.0
    assert inventory["TiO2"] > 0.0
    assert inventory_mass_kg > 0.0
    assert experiment["sample"]["printed_composition"]["locator"]["table"] == "1"
    assert experiment["sample"]["printed_composition"]["state"]["value"] == {
        "SiO2": 40.6,
        "Al2O3": 10.67,
        "MgO": 15.4,
        "FeO": 16.26,
        "CaO": 16.82,
    }


def test_sossi_geometry_is_declared_from_bead_mass_without_residue_inputs() -> None:
    areas = _sossi_geometry_areas(25.0e-6)

    assert tuple(areas) == _SOSSI_GEOMETRIES
    assert all(value > 0.0 for value in areas.values())
    assert areas["pt_loop_bead_sphere_constant"] == areas[
        "pt_loop_bead_sphere_shrinking"
    ]
    radius_m = (3.0 * 25.0e-6 / (4.0 * math.pi * 2700.0)) ** (1.0 / 3.0)
    assert areas["pt_loop_bead_sphere_constant"] == pytest.approx(
        4.0 * math.pi * radius_m**2
    )
    assert areas["pt_loop_bead_disk_equivalent_volume"] != areas[
        "pt_loop_bead_sphere_constant"
    ]


def test_sossi_prediction_provenance_pins_engine_binding_identity(monkeypatch) -> None:
    pytest.importorskip("openimcc")
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE", raising=False)
    import openimcc

    import simulator.battery.residue as residue
    from simulator.melt_backend import openimcc_bridge
    from simulator.chemistry.melt_activity import melt_oxide_activity

    monkeypatch.setattr(
        openimcc_bridge, "DEFAULT_BACKEND_PACK_NAME", "published-core"
    )

    def no_evaporation(inventory, *_args, **_kwargs):
        return SimpleNamespace(
            residue_mol=dict(inventory), buffer_oxygen_exchange_mol=0.0
        )

    monkeypatch.setattr(residue, "integrate_residue_inventory", no_evaporation)
    source = yaml.safe_load(SOURCE.read_text())
    experiment = source["experiments"][0]
    experiment_id = experiment["experiment_id"]
    starting_trace = {
        element: source["species"][element]["observations"][0]["values"][
            "starting_measured_ppm"
        ]
        for element in ("Mn", "Ti")
    }
    initial_inventory, _, _ = residue._sossi_initial_inventory(
        experiment, starting_trace
    )
    temperature_K = residue._hashimoto_point_value(
        experiment["thermal_schedule"]["setpoints_and_holds"][0]["temperature_K"]
    )
    expected_mno_activity = melt_oxide_activity(
        "MnO", initial_inventory, temperature_K=temperature_K
    )
    assert expected_mno_activity is not None
    evaluated_compositions: list[dict[str, float]] = []
    gas_activity_inputs: list[dict[str, float]] = []
    original_melt_evaluate = openimcc_bridge._evaluate_uncached
    original_gas_evaluate = openimcc.evaluate_gas

    def capture_melt_evaluate(**kwargs):
        evaluated_compositions.append(dict(kwargs["composition_mol"]))
        return original_melt_evaluate(**kwargs)

    def capture_gas_evaluate(activities, *args, **kwargs):
        gas_activity_inputs.append(dict(activities))
        return original_gas_evaluate(activities, *args, **kwargs)

    monkeypatch.setattr(
        residue,
        "_evaluate_openimcc_isolated",
        lambda evaluate, *, timeout_s, **kwargs: evaluate(**kwargs),
    )
    monkeypatch.setattr(openimcc_bridge, "_evaluate_uncached", capture_melt_evaluate)
    monkeypatch.setattr(openimcc, "evaluate_gas", capture_gas_evaluate)
    catalog = yaml.safe_load((ROOT / "data/vapor_pressures.yaml").read_text())
    prediction = residue._predict_sossi_residue_cohort(
        [experiment],
        catalog,
        starting_trace_ppm_by_experiment={experiment_id: starting_trace},
        buffered_fO2_log_by_experiment={experiment_id: -10.0},
        engine="openimcc",
    )[0]

    gas_pack = openimcc.load_gas_datapack()
    package_identity = openimcc.engine_binding_identity(
        openimcc_bridge._load_pack("published-core"), gas_pack
    )
    expected_identity = {
        "engine_binding_digest": package_identity.digest,
        "melt_binding_digest": package_identity.melt_binding_digest,
        "condensate_table_digest": package_identity.condensate_table_digest,
        "gas_table_digest": package_identity.gas_table_digest,
    }
    assert prediction.provenance["engine_binding_identity"] == expected_identity
    assert "gas_pack_digest" not in prediction.provenance
    assert "liquid_pack_digest" not in prediction.provenance
    assert evaluated_compositions
    assert all("MnO" not in composition for composition in evaluated_compositions)
    mno_inputs = [activities for activities in gas_activity_inputs if "MnO" in activities]
    assert mno_inputs
    assert mno_inputs[0]["MnO"] == pytest.approx(
        expected_mno_activity.thermodynamic_parent_activity()
    )
    mno_extension = prediction.provenance["melt_activity_extensions"]["MnO"]
    assert mno_extension["authority_level"] == expected_mno_activity.evidence_tier
    assert mno_extension["certified_band_K"] is None
    assert mno_extension["activity_provenance"] == expected_mno_activity.provenance()


def test_sossi_ti_only_cohort_still_evaluates_gas(monkeypatch) -> None:
    openimcc = pytest.importorskip("openimcc")

    import simulator.battery.residue as residue
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    from simulator.melt_backend import openimcc_bridge
    from simulator.melt_backend.openimcc_bridge import OpenImccBridgeResult

    source = yaml.safe_load(SOURCE.read_text())
    experiment = source["experiments"][0]
    experiment_id = experiment["experiment_id"]
    starting_trace = {
        "Ti": source["species"]["Ti"]["observations"][0]["values"][
            "starting_measured_ppm"
        ]
    }
    original_channels = battery._openimcc_gas_channels_and_omission_notices
    selected: list[str] = []

    def ti_channels(parents, gas_pack):
        channels, omissions = original_channels(parents, gas_pack)
        result = [row for row in channels if str(row[1][0]) == "TiO2"]
        assert result
        selected.extend(str(row[0]) for row in result)
        return result, omissions

    state = SimpleNamespace(
        parent_oxide_activities={"SiO2": 0.2, "FeO": 0.1, "TiO2": 0.01},
        parent_oxides=("SiO2", "FeO", "TiO2"),
        pack_model_id="fixture-model",
        pack_version="fixture-version",
        pack_digest="fixture-digest",
        openimcc_version="fixture-openimcc",
        flags=(),
        extrapolated=False,
    )
    gas_calls: list[dict[str, object]] = []

    def evaluate_gas(*_args, **kwargs):
        gas_calls.append(kwargs)
        return {species: 1.0e-6 for species in selected}

    def isolated(_evaluate, **_kwargs):
        return SimpleNamespace(to_hot=lambda: {"fixture": True})

    def no_evaporation(inventory, *_args, **_kwargs):
        return SimpleNamespace(
            residue_mol=dict(inventory), buffer_oxygen_exchange_mol=0.0
        )

    monkeypatch.setattr(
        battery, "_openimcc_gas_channels_and_omission_notices", ti_channels
    )
    monkeypatch.setattr(residue, "_evaluate_openimcc_isolated", isolated)
    monkeypatch.setattr(openimcc, "evaluate_gas", evaluate_gas)
    monkeypatch.setattr(
        OpenImccBridgeResult,
        "from_hot",
        classmethod(lambda _cls, _payload: state),
    )
    monkeypatch.setattr(residue, "integrate_residue_inventory", no_evaporation)
    catalog = yaml.safe_load((ROOT / "data/vapor_pressures.yaml").read_text())

    prediction = residue._predict_sossi_residue_cohort(
        [experiment],
        catalog,
        starting_trace_ppm_by_experiment={experiment_id: starting_trace},
        buffered_fO2_log_by_experiment={experiment_id: -10.0},
        engine="openimcc",
    )[0]

    assert gas_calls
    assert prediction.provenance.get("run_refusal") is None
    assert "Mn" not in prediction.channel_missing_elements
    assert prediction.provenance["element_refusals"]["Mn"] == {
        "reason": "starting_component_ppm_missing",
        "species": "Mn",
        "row": experiment_id,
        "detail": (
            "missing positive starting_component_ppm for Mn in "
            f"{experiment_id}"
        ),
    }
    assert prediction.primary_element_ppm["Ti"] > 0.0


def test_sossi_missing_trace_rejects_restored_bare_lookup_mutation(monkeypatch) -> None:
    pytest.importorskip("openimcc")
    import simulator.battery.residue as residue

    source = yaml.safe_load(SOURCE.read_text())
    experiment = source["experiments"][0]

    def bare_lookup_mutant(_experiment, starting_trace_ppm):
        return starting_trace_ppm["Mn"]

    monkeypatch.setattr(residue, "_sossi_initial_inventory", bare_lookup_mutant)
    with pytest.raises(KeyError, match="Mn"):
        residue._predict_sossi_residue_cohort(
            [experiment],
            yaml.safe_load((ROOT / "data/vapor_pressures.yaml").read_text()),
            starting_trace_ppm_by_experiment={experiment["experiment_id"]: {"Ti": 1.0}},
            buffered_fO2_log_by_experiment={experiment["experiment_id"]: -10.0},
            engine="openimcc",
        )


def test_sossi_openimcc_timeout_is_typed_and_later_runs_continue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("openimcc")
    import simulator.battery.residue as residue
    from simulator.melt_backend import openimcc_bridge

    source = yaml.safe_load(SOURCE.read_text())
    experiments = source["experiments"][:2]
    starting_trace = {
        element: source["species"][element]["observations"][0]["values"][
            "starting_measured_ppm"
        ]
        for element in ("Mn", "Ti")
    }
    experiment_ids = {experiment["experiment_id"] for experiment in experiments}
    monkeypatch.setattr(
        openimcc_bridge, "DEFAULT_BACKEND_PACK_NAME", "published-core"
    )

    def timeout(*_args, **_kwargs):
        raise TimeoutError("fixture engine deadline")

    monkeypatch.setattr(residue, "_evaluate_openimcc_isolated", timeout)
    predictions = residue._predict_sossi_residue_cohort(
        experiments,
        yaml.safe_load((ROOT / "data/vapor_pressures.yaml").read_text()),
        starting_trace_ppm_by_experiment={
            experiment_id: starting_trace for experiment_id in experiment_ids
        },
        buffered_fO2_log_by_experiment={experiment_id: -10.0 for experiment_id in experiment_ids},
        engine="openimcc",
    )

    assert {prediction.experiment_id for prediction in predictions} == experiment_ids
    assert all(
        prediction.provenance["run_refusal"]["reason"]
        == "engine_timeout:openimcc:sossi_residue_cohort"
        for prediction in predictions
    )


def test_sossi_worker_start_failure_after_residue_scoring_worker(
    monkeypatch: pytest.MonkeyPatch,
    _close_openimcc_worker_between_tests,
) -> None:
    pytest.importorskip("openimcc")
    import simulator.battery.residue as residue
    from simulator.melt_backend import openimcc_bridge

    monkeypatch.setattr(
        openimcc_bridge, "DEFAULT_BACKEND_PACK_NAME", "published-core"
    )
    monkeypatch.setattr(
        residue,
        "integrate_residue_inventory",
        lambda inventory, *_args, **_kwargs: SimpleNamespace(
            residue_mol=dict(inventory), buffer_oxygen_exchange_mol=0.0
        ),
    )
    source = yaml.safe_load(SOURCE.read_text())
    experiment = source["experiments"][0]
    starting_trace = {
        element: source["species"][element]["observations"][0]["values"][
            "starting_measured_ppm"
        ]
        for element in ("Mn", "Ti")
    }
    experiment_id = experiment["experiment_id"]

    residue._predict_sossi_residue_cohort(
        [experiment],
        yaml.safe_load((ROOT / "data/vapor_pressures.yaml").read_text()),
        starting_trace_ppm_by_experiment={experiment_id: starting_trace},
        buffered_fO2_log_by_experiment={experiment_id: -10.0},
        engine="openimcc",
    )

    assert residue._OPENIMCC_EVALUATION_WORKER is not None
    _close_openimcc_worker_between_tests()
    _assert_sossi_worker_start_failure_recorded(monkeypatch)


def test_sossi_worker_start_failure_is_recorded_as_transport_error(
    monkeypatch: pytest.MonkeyPatch,
    _close_openimcc_worker_between_tests,
) -> None:
    _assert_sossi_worker_start_failure_recorded(monkeypatch)


def _assert_sossi_worker_start_failure_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("openimcc")
    import multiprocessing

    import simulator.battery.residue as residue
    from simulator.melt_backend import openimcc_bridge

    assert residue._OPENIMCC_EVALUATION_WORKER is None

    spawn_context = multiprocessing.get_context("spawn")
    connections = []

    class ContextProxy:
        def Pipe(self, **kwargs):
            pair = spawn_context.Pipe(**kwargs)
            connections.extend(pair)
            return pair

        def Process(self, **kwargs):
            process = spawn_context.Process(**kwargs)

            def fail_start():
                raise OSError(24, "forced descriptor exhaustion")

            process.start = fail_start
            return process

    monkeypatch.setattr(
        residue.multiprocessing, "get_context", lambda _method: ContextProxy()
    )
    monkeypatch.setattr(
        openimcc_bridge, "DEFAULT_BACKEND_PACK_NAME", "published-core"
    )
    source = yaml.safe_load(SOURCE.read_text())
    experiment = source["experiments"][0]
    starting_trace = {
        element: source["species"][element]["observations"][0]["values"][
            "starting_measured_ppm"
        ]
        for element in ("Mn", "Ti")
    }
    experiment_id = experiment["experiment_id"]

    prediction = residue._predict_sossi_residue_cohort(
        [experiment],
        yaml.safe_load((ROOT / "data/vapor_pressures.yaml").read_text()),
        starting_trace_ppm_by_experiment={experiment_id: starting_trace},
        buffered_fO2_log_by_experiment={experiment_id: -10.0},
        engine="openimcc",
    )[0]

    assert prediction.provenance["run_refusal"]["reason"] == (
        "engine_transport_error:openimcc"
    )
    assert len(connections) == 2
    assert all(connection.closed for connection in connections)
    assert residue._OPENIMCC_EVALUATION_WORKER is None


def test_sossi_scorer_projects_mn_and_types_unsupported_elements(monkeypatch) -> None:
    from simulator.battery.enums import (
        Engine,
        MetricOperation,
        NoticeKind,
        RefusalReason,
    )
    from simulator.battery.score import (
        _residue_prediction_cache_entry,
        _sossi_residue_prediction,
        load_score_context,
        metric_operation_for_identity,
        point_magnitude,
    )
    import simulator.battery.score as score_module
    import simulator.battery.residue as residue

    context = load_score_context(sources=("kems-012-sossi-2019",))
    monkeypatch.setattr(
        score_module.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("residue scoring must not require git"),
    )
    rows = [
        row
        for row in context.observations.values()
        if row.source_id == "kems-012-sossi-2019"
        and row.admission.status.value == "admitted"
        and point_magnitude(row.value) is not None
        and row.identity.species.formula in {"Mn", "Gd"}
    ]
    manganese = next(row for row in rows if row.identity.species.formula == "Mn")
    gadolinium = next(row for row in rows if row.identity.species.formula == "Gd")
    expected_run = manganese.experiment_id.rsplit("::", 1)[-1]

    def fake_cohort(experiments, *_args, **_kwargs):
        return tuple(
            SimpleNamespace(
                experiment_id=item["experiment_id"],
                primary_element_ppm={"Mn": 900.0, "Ti": 450.0},
                sensitivity_band_ppm={"Mn": (800.0, 1000.0), "Ti": (400.0, 500.0)},
                channel_missing_elements=(),
                provenance={
                    "integration": {"primary": {"status": "converged"}},
                    "melt_activity_extensions": {
                        "MnO": {
                            "reason": "MnO is outside the IMCC-SF04 melt-activity domain",
                            "authority_level": "UNCERTIFIED",
                            "certified_band_K": None,
                        }
                    },
                },
            )
            for item in experiments
        )

    monkeypatch.setattr(residue, "_predict_sossi_residue_cohort", fake_cohort)
    cache = {}
    prediction = _sossi_residue_prediction(
        context, manganese, Engine.OPENIMCC, cache
    )
    refusal = _sossi_residue_prediction(
        context, gadolinium, Engine.OPENIMCC, cache
    )

    cached_cohort = _residue_prediction_cache_entry(
        cache, "kems-012-sossi-2019", Engine.OPENIMCC
    )
    assert cached_cohort is not None
    assert expected_run in cached_cohort
    assert prediction.execution.state.value == "produced"
    assert prediction.value == 900.0
    assert prediction.identity == manganese.identity
    assert prediction.version is None
    assert "code_revision" not in prediction.refusal_detail
    assert any(
        notice.reason == "bc_open_furnace_langmuir_limit_diagnostic"
        for notice in prediction.notices
    )
    assert any(
        notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        and notice.reason == "MnO is outside the IMCC-SF04 melt-activity domain"
        and "authority_level=UNCERTIFIED" in notice.band
        and "certified_band_K=none" in notice.band
        for notice in prediction.notices
    )
    assert metric_operation_for_identity(manganese.identity) is MetricOperation.DEX
    assert refusal.refusal_reason is RefusalReason.OUTSIDE_SUPPORTED_SPECIES
    assert refusal.refusal_detail["reason"] == "channel_missing"


def test_sossi_scorer_passes_the_checked_fO2_to_the_residue_cohort(monkeypatch) -> None:
    import simulator.battery.score as score_module
    import simulator.battery.residue as residue
    from simulator.battery.enums import Engine
    from simulator.battery.oxygen_identity import check_sossi_table2_gas_equilibrium
    from simulator.battery.score import (
        _sossi_residue_prediction,
        load_score_context,
        point_magnitude,
    )

    context = load_score_context(sources=("kems-012-sossi-2019",))
    manganese = next(
        row
        for row in context.observations.values()
        if row.source_id == "kems-012-sossi-2019"
        and row.admission.status.value == "admitted"
        and row.identity.species.formula == "Mn"
        and point_magnitude(row.value) is not None
        and row.experiment_id.rsplit("::", 1)[-1]
        == "sossi-2019-run-p09-06-18a"
    )
    experiment_id = manganese.experiment_id.rsplit("::", 1)[-1]
    checked_log = Decimal("-9.22")

    def check_with_probe(experiments, series):
        checks = dict(check_sossi_table2_gas_equilibrium(experiments, series))
        key = next(key for key, check in checks.items() if check.run_id == "P09/06/18a")
        checks[key] = replace(
            checks[key], checked_log10_fO2_bar=checked_log
        )
        return checks

    monkeypatch.setattr(
        score_module,
        "check_sossi_table2_gas_equilibrium",
        check_with_probe,
    )
    monkeypatch.setattr(
        score_module.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("residue scoring must not require git"),
    )
    passed_fO2 = {}

    def capture_cohort(
        experiments,
        *_args,
        buffered_fO2_log_by_experiment,
        **_kwargs,
    ):
        passed_fO2.update(buffered_fO2_log_by_experiment)
        return tuple(
            SimpleNamespace(
                experiment_id=experiment["experiment_id"],
                primary_element_ppm={"Mn": 900.0, "Ti": 450.0},
                sensitivity_band_ppm={"Mn": (800.0, 1000.0), "Ti": (400.0, 500.0)},
                channel_missing_elements=(),
                provenance={"integration": {"primary": {"status": "converged"}}},
            )
            for experiment in experiments
        )

    monkeypatch.setattr(residue, "_predict_sossi_residue_cohort", capture_cohort)
    prediction = _sossi_residue_prediction(
        context,
        manganese,
        Engine.OPENIMCC,
        {},
        test_experiment_ids=frozenset({manganese.experiment_id}),
    )

    assert prediction.execution.state.value == "produced", prediction.refusal_detail
    assert passed_fO2[experiment_id] == float(checked_log)
    assert prediction.refusal_detail["predicted_element_ppm"] == 900.0
    assert prediction.refusal_detail["sensitivity_band_ppm_for_cell"] == (800.0, 1000.0)


@pytest.mark.parametrize(
    (
        "mutation",
        "expected_reason",
        "expected_detail",
        "expected_spread",
        "expected_fallback",
        "expected_printed_log",
    ),
    [
        (
            "discrepant",
            "gas_equilibrium_fo2_disagrees_with_printed",
            "computed=",
            True,
            "independently_published_table2_run_log",
            -9.23,
        ),
        (
            "failed",
            "cover_not_printed_mixture",
            "P09/06/18a does not use the cover gas",
            None,
            "independently_published_table2_run_log",
            -9.23,
        ),
        (
            "structured_log",
            "sossi_table2_structured_fo2_disagrees_with_quote",
            "sossi_table2_gas_row_quote_disagrees:P09/06/18a",
            True,
            "independently_published_table2_run_log",
            -9.23,
        ),
        (
            "duplicate",
            "sossi_table2_gas_row_repeated",
            "expected one Table 2 row for P09/06/18a, found 2",
            None,
            "located_run_recorded_fO2",
            None,
        ),
        (
            "missing",
            "sossi_table2_gas_row_missing",
            "expected one Table 2 row for P09/06/18a, found 0",
            None,
            "located_run_recorded_fO2",
            None,
        ),
        (
            "parser_error",
            "sossi_table2_gas_row_quote_invalid",
            "sossi_table2_gas_row_quote_invalid:P09/06/18a",
            None,
            "located_run_recorded_fO2",
            None,
        ),
    ],
)
def test_one_sossi_gas_refusal_stays_scoped_to_its_run_and_cache_selection(
    monkeypatch,
    mutation,
    expected_reason,
    expected_detail,
    expected_spread,
    expected_fallback,
    expected_printed_log,
) -> None:
    from copy import deepcopy

    import simulator.battery.score as score_module
    import simulator.battery.residue as residue
    from simulator.battery.enums import Engine, NoticeKind
    from simulator.battery.oxygen_identity import SossiRunOxygenDecision
    from simulator.battery.score import (
        _residue_prediction_cache_entry,
        _sossi_residue_prediction,
        flagged_strata,
        load_score_context,
        point_magnitude,
    )

    source_id = "kems-012-sossi-2019"
    context = load_score_context(sources=(source_id,))
    rows = {
        (row.experiment_id.rsplit("::", 1)[-1], row.identity.species.formula): row
        for row in context.observations.values()
        if row.source_id == source_id
        and row.admission.status.value == "admitted"
        and row.identity.species.formula in {"Mn", "Ti"}
        and point_magnitude(row.value) is not None
    }
    bad_short = "sossi-2019-run-p09-06-18a"
    valid_short = "sossi-2019-run-p09-06-18b"
    bad_ids = frozenset(
        rows[(bad_short, element)].experiment_id for element in ("Mn", "Ti")
    )
    valid_ids = frozenset(
        rows[(valid_short, element)].experiment_id for element in ("Mn", "Ti")
    )
    test_ids = bad_ids | valid_ids
    original_load = score_module.load_yaml
    drop_buffer = {"value": mutation == "structured_log"}

    def poison_one_run(path):
        value = original_load(path)
        if not str(path).endswith(f"/extracts/{source_id}.yaml"):
            return value
        value = deepcopy(value)
        experiment = next(
            item
            for item in value["experiments"]
            if item["experiment_id"] == bad_short
        )
        table = next(
            item
            for item in value["species"]["Mn"]["observations"]
            if item.get("observation_id")
            == "sossi_2019_mn_table2_open_furnace_residue_ppm_quoted_20260906"
        )
        series = table["values"]["series"]
        row = next(
            item
            for item in series
            if item["run_id"].rstrip("*") == "P09/06/18a"
        )
        if mutation == "discrepant":
            quote, count = re.subn(
                r"(P09/06/18a\*?\s+1400\s+15\s+)130(\s+65\s+-9\.23)",
                r"\g<1>100\g<2>",
                row["locator"]["quote"],
                count=1,
            )
            assert count == 1
            row["locator"]["quote"] = quote
        elif mutation == "failed":
            experiment["pressure_environment"]["sweep_gas"]["state"] = {
                "tag": "value",
                "value": "CO + CO2 + Kr",
            }
        elif mutation == "missing":
            table["values"]["series"] = [item for item in series if item is not row]
        elif mutation == "structured_log":
            row["log10_fO2"]["value"] = -2.0
        elif mutation == "duplicate":
            series.append(deepcopy(row))
        else:
            row["locator"]["quote"] = ""
        if drop_buffer["value"]:
            experiment["fO2_control"]["buffer"] = {
                "state": {"tag": "unknown", "reason": "mutation"}
            }
        return value

    monkeypatch.setattr(score_module, "load_yaml", poison_one_run)
    monkeypatch.setattr(
        score_module.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("residue scoring must not require git"),
    )
    calls = []
    passed_fO2 = {}

    def valid_cohort(
        experiments,
        *_args,
        selected_experiment_ids=None,
        buffered_fO2_log_by_experiment,
        **_kwargs,
    ):
        passed_fO2.update(buffered_fO2_log_by_experiment)
        calls.append(
            (
                frozenset(item["experiment_id"] for item in experiments),
                selected_experiment_ids,
            )
        )
        return tuple(
            residue._SossiResiduePrediction(
                experiment_id=item["experiment_id"],
                primary_element_ppm={"Mn": 900.0, "Ti": 450.0},
                geometry_element_ppm={
                    "Mn": {"sphere_constant": 900.0},
                    "Ti": {"sphere_constant": 450.0},
                },
                sensitivity_band_ppm={
                    "Mn": (800.0, 1000.0),
                    "Ti": (400.0, 500.0),
                },
                channel_missing_elements=(),
                provenance={"integration": {"primary": {"status": "converged"}}},
            )
            for item in experiments
            if selected_experiment_ids is None
            or item["experiment_id"] in selected_experiment_ids
        )

    monkeypatch.setattr(residue, "_predict_sossi_residue_cohort", valid_cohort)
    selection = frozenset({bad_short, valid_short})
    cache = {}
    for element in ("Mn", "Ti"):
        healthy = _sossi_residue_prediction(
            context,
            rows[(valid_short, element)],
            Engine.OPENIMCC,
            cache,
            selected_experiment_ids=selection,
            test_experiment_ids=test_ids,
        )
        assert healthy.execution.state.value == "produced", healthy.refusal_detail
        assert "gas_boundary_assessment" not in healthy.refusal_detail
        assert not any(
            notice.kind is NoticeKind.SOSSI_GAS_BOUNDARY_UNVERIFIED
            for notice in healthy.notices
        )
        fallback = _sossi_residue_prediction(
            context,
            rows[(bad_short, element)],
            Engine.OPENIMCC,
            cache,
            selected_experiment_ids=selection,
            test_experiment_ids=test_ids,
        )
        assert fallback.execution.state.value == "produced", fallback.refusal_detail
        assert fallback.refusal_detail["predicted_element_ppm"] == {
            "Mn": 900.0,
            "Ti": 450.0,
        }[element]
        assessment = fallback.refusal_detail["gas_boundary_assessment"]
        assert assessment["reason"] == "sossi_gas_boundary_unverified"
        assert assessment["original_reason"] == expected_reason
        assert expected_detail in assessment["detail"]
        assert assessment["printed_log10_fO2_bar"] == expected_printed_log
        if expected_spread is None:
            assert assessment["computed_minus_printed_dex"] is None
        else:
            assert assessment["computed_minus_printed_dex"] is not None
        assert assessment["fallback"] == expected_fallback
        assert assessment["engine_log10_fO2_bar"] == pytest.approx(-9.23)
        assert any(
            notice.kind is NoticeKind.SOSSI_GAS_BOUNDARY_UNVERIFIED
            and notice.reason == "sossi_gas_boundary_unverified"
            for notice in fallback.notices
        )
        assert "out_of_certified_band" in flagged_strata(fallback.notices)

    cached = _residue_prediction_cache_entry(
        cache, source_id, Engine.OPENIMCC, selection
    )
    assert cached is not None
    assert set(cached) == {bad_short, valid_short}
    assert "__cohort_error__" not in cached
    assert expected_detail in cached[bad_short].provenance[
        "gas_boundary_assessment"
    ]["detail"]
    assert passed_fO2[bad_short] == pytest.approx(-9.23)
    assert passed_fO2[valid_short] == pytest.approx(-9.23)
    assert calls == [
        (frozenset({bad_short, valid_short}), frozenset({bad_short, valid_short})),
    ]

    if mutation == "parser_error":
        drop_buffer["value"] = True
        unaffected_cache = {}
        unaffected = _sossi_residue_prediction(
            context,
            rows[(valid_short, "Mn")],
            Engine.OPENIMCC,
            unaffected_cache,
            selected_experiment_ids=frozenset({valid_short}),
            test_experiment_ids=test_ids,
        )
        assert unaffected.execution.state.value == "produced"
        unaffected_cohort = _residue_prediction_cache_entry(
            unaffected_cache,
            source_id,
            Engine.OPENIMCC,
            frozenset({valid_short}),
        )
        assert unaffected_cohort is not None
        assert set(unaffected_cohort) == {valid_short}
        assert "__cohort_error__" not in unaffected_cohort

    if mutation == "structured_log":
        passed_fO2.clear()

        def legacy_structured_log(check, **_kwargs):
            return SossiRunOxygenDecision(
                check.experiment_id,
                Decimal("-2.0"),
                "legacy_structured_fO2",
            )

        with monkeypatch.context() as mutation_patch:
            mutation_patch.setattr(
                score_module,
                "resolve_sossi_run_oxygen",
                legacy_structured_log,
            )
            mutated_prediction = _sossi_residue_prediction(
                context,
                rows[(bad_short, "Mn")],
                Engine.OPENIMCC,
                {},
                selected_experiment_ids=selection,
                test_experiment_ids=test_ids,
            )
        assert mutated_prediction.execution.state.value == "produced"
        assert passed_fO2[bad_short] == pytest.approx(-2.0)
        with pytest.raises(AssertionError):
            assert passed_fO2[bad_short] == pytest.approx(-9.23)


def test_sossi_ladder_fallback_uses_each_runs_own_observation(monkeypatch) -> None:
    from copy import deepcopy

    import simulator.battery.fo2_ladder as fo2_ladder
    import simulator.battery.score as score_module
    import simulator.battery.residue as residue
    from simulator.battery.enums import Engine
    from simulator.battery.fo2_ladder import ResolvedFo2
    from simulator.battery.score import (
        _sossi_residue_prediction,
        load_score_context,
        point_magnitude,
    )

    source_id = "kems-012-sossi-2019"
    bad_short = "sossi-2019-run-p09-06-18a"
    sibling_short = "sossi-2019-run-p09-06-18b"
    context = load_score_context(sources=(source_id,))
    run_rows = {
        row.experiment_id.rsplit("::", 1)[-1]: row
        for row in context.observations.values()
        if row.source_id == source_id
        and row.identity.species.formula == "Mn"
        and row.admission.status.value == "admitted"
        and point_magnitude(row.value) is not None
        and row.experiment_id.rsplit("::", 1)[-1]
        in {bad_short, sibling_short}
    }
    test_ids = frozenset(row.experiment_id for row in run_rows.values())
    original_load = score_module.load_yaml

    def poison_both_runs(path):
        value = original_load(path)
        if not str(path).endswith(f"/extracts/{source_id}.yaml"):
            return value
        value = deepcopy(value)
        for experiment in value["experiments"]:
            if experiment["experiment_id"] in {bad_short, sibling_short}:
                experiment["fO2_control"]["buffer"] = {
                    "state": {"tag": "unknown", "reason": "mutation"}
                }
        table = next(
            item
            for item in value["species"]["Mn"]["observations"]
            if item.get("observation_id")
            == "sossi_2019_mn_table2_open_furnace_residue_ppm_quoted_20260906"
        )
        for row in table["values"]["series"]:
            if row["run_id"].rstrip("*") in {"P09/06/18a", "P09/06/18b"}:
                row["locator"]["quote"] = ""
        return value

    monkeypatch.setattr(score_module, "load_yaml", poison_both_runs)
    monkeypatch.setattr(
        score_module.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("residue scoring must not require git"),
    )
    ladder_inputs = []

    def run_scoped_ladder(experiment, _bench, observation, **_kwargs):
        assert experiment.experiment_id == observation.experiment_id
        short_id = observation.experiment_id.rsplit("::", 1)[-1]
        ladder_inputs.append(short_id)
        value = -8.1 if short_id == bad_short else -8.2
        return ResolvedFo2(value, 3, "buffer_relation", False)

    monkeypatch.setattr(fo2_ladder, "resolve_row_fo2", run_scoped_ladder)
    passed_fO2 = {}

    def capture_cohort(
        experiments,
        *_args,
        buffered_fO2_log_by_experiment,
        **_kwargs,
    ):
        passed_fO2.update(buffered_fO2_log_by_experiment)
        return tuple(
            residue._SossiResiduePrediction(
                experiment_id=item["experiment_id"],
                primary_element_ppm={"Mn": 900.0},
                geometry_element_ppm={"Mn": {"sphere_constant": 900.0}},
                sensitivity_band_ppm={"Mn": (800.0, 1000.0)},
                channel_missing_elements=(),
                provenance={"integration": {"primary": {"status": "converged"}}},
            )
            for item in experiments
        )

    monkeypatch.setattr(residue, "_predict_sossi_residue_cohort", capture_cohort)
    cache = {}
    prediction = _sossi_residue_prediction(
        context,
        run_rows[bad_short],
        Engine.OPENIMCC,
        cache,
        selected_experiment_ids=frozenset({bad_short, sibling_short}),
        test_experiment_ids=test_ids,
    )

    assert prediction.execution.state.value == "produced"
    assert set(ladder_inputs) == {bad_short, sibling_short}
    assert passed_fO2[bad_short] == pytest.approx(-8.1)
    assert passed_fO2[sibling_short] == pytest.approx(-8.2)


def test_mixed_hashimoto_and_sossi_score_keep_separate_engine_cohorts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from simulator.battery.enums import Engine, Quantity, Rail
    from simulator.battery.score import (
        load_score_context,
        point_magnitude,
        quantity_token,
        score_store,
    )
    import simulator.battery.residue as residue

    hashimoto_source = "kems-015-hashimoto-1983"
    sossi_source = "kems-012-sossi-2019"
    context = load_score_context(sources=(hashimoto_source, sossi_source))

    def hashimoto_cohort(experiments, *_args, **_kwargs):
        rows = []
        for experiment in experiments:
            for alpha_arm in (
                residue._HASHIMOTO_PRIMARY_ALPHA_ARM,
                "alpha_runtime_catalog",
            ):
                rows.append(
                    SimpleNamespace(
                        experiment_id=experiment["experiment_id"],
                        alpha_arm=alpha_arm,
                        primary_geometry_policy_id=residue._HASHIMOTO_PRIMARY_GEOMETRY,
                        primary_oxide_wt_pct={
                            oxide: 1.0 for oxide in ("FeO", "MgO", "SiO2", "CaO", "Al2O3")
                        },
                        sensitivity_band_wt_pct={
                            oxide: (0.9, 1.1)
                            for oxide in ("FeO", "MgO", "SiO2", "CaO", "Al2O3")
                        },
                        geometry_oxide_wt_pct={
                            residue._HASHIMOTO_PRIMARY_GEOMETRY: {
                                oxide: 1.0
                                for oxide in ("FeO", "MgO", "SiO2", "CaO", "Al2O3")
                            }
                        },
                        provenance={
                            "geometry_refusal_by_geometry": {},
                            "integration": {"refinement_status": "converged"},
                        },
                    )
                )
        return tuple(rows)

    def sossi_cohort(experiments, *_args, **_kwargs):
        return tuple(
            SimpleNamespace(
                experiment_id=experiment["experiment_id"],
                primary_element_ppm={"Mn": 900.0, "Ti": 450.0},
                sensitivity_band_ppm={"Mn": (800.0, 1000.0), "Ti": (400.0, 500.0)},
                channel_missing_elements=(),
                provenance={
                    "integration": {"primary": {"status": "converged"}},
                },
            )
            for experiment in experiments
        )

    monkeypatch.setattr(residue, "_predict_hashimoto_residue_cohort", hashimoto_cohort)
    monkeypatch.setattr(residue, "_predict_sossi_residue_cohort", sossi_cohort)

    residuals, candidates = score_store(
        context,
        engines=(Engine.OPENIMCC,),
        rail=Rail.RESIDUE_COMPOSITION,
    )
    pinned_score = next(
        row
        for row in residuals
        if row.reference
        == "kems-012-sossi-2019::sossi_2019_mn_table2_open_furnace_residue_ppm_quoted_20260906::T=1573.15:h=9b18699febf8"
    )
    assert pinned_score.key == (
        "kems-012-sossi-2019::sossi_2019_mn_table2_open_furnace_residue_ppm_quoted_20260906::"
        "T=1573.15:h=9b18699febf8:T=1573.15::residue_component_composition::"
        "residue_composition::openimcc"
    )
    assert pinned_score.candidate == (
        "engine:openimcc:kems-012-sossi-2019::"
        "sossi_2019_mn_table2_open_furnace_residue_ppm_quoted_20260906::"
        "T=1573.15:h=9b18699febf8"
    )
    assert candidates[pinned_score.candidate].observation_id == pinned_score.candidate
    assert pinned_score.numeric is not None
    assert float(pinned_score.numeric.value).hex() == "0x1.88fea8359f38fp-4"
    hashimoto_ids = {
        row.observation_id
        for row in context.observations.values()
        if row.source_id == hashimoto_source
        and "hashimoto_1983_table3_residue_composition_series" in row.observation_id
        and row.admission.status.value == "admitted"
        and quantity_token(row.identity) is Quantity.RESIDUE_COMPONENT_COMPOSITION
        and point_magnitude(row.value) is not None
    }
    sossi_ti_ids = {
        row.observation_id
        for row in context.observations.values()
        if row.source_id == sossi_source
        and row.identity.species.formula == "Ti"
        and quantity_token(row.identity) is Quantity.RESIDUE_COMPONENT_COMPOSITION
        and row.admission.status.value == "admitted"
        and point_magnitude(row.value) is not None
    }
    numeric_references = {
        residual.reference for residual in residuals if residual.numeric is not None
    }

    assert len(hashimoto_ids) == 120
    assert len(sossi_ti_ids) == 43
    assert hashimoto_ids <= numeric_references
    assert sossi_ti_ids <= numeric_references


def _liquid_row(name: str, band: tuple[float, float], *, below_floor: bool) -> dict:
    """Provenance shape `_hashimoto_liquid_row_provenance` already stores."""

    return {
        "channel": name,
        "parent": name.split("(")[0],
        "liquid_row": {
            "row": name,
            "source": "recorded-fit",
            "T_fit_K": [band[0], band[1]],
            "extrapolated_below_floor": below_floor,
        },
    }


def test_sossi_below_liquid_floor_reaches_the_scored_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A below-floor liquid row is predicted and the scored residual carries the flag.

    MnO(l) on the gas pack is fitted from 1200 K. No Sossi extract is that cold,
    so the cohort fixture marks one run below that recorded floor.
    """

    from simulator.battery.enums import Authority, Engine, NoticeKind, Quantity, Rail
    from simulator.battery.score import load_score_context, score_store
    import simulator.battery.residue as residue

    context = load_score_context(sources=("kems-012-sossi-2019",))
    # The production selector's nearest MnO(l) interval below its floor.
    certified_band = (1200.0, 1600.0)
    below_floor_run: dict[str, str] = {}

    def fake_cohort(experiments, *_args, **_kwargs):
        below_floor_run["id"] = str(experiments[0]["experiment_id"])
        rows = []
        for experiment in experiments:
            experiment_id = str(experiment["experiment_id"])
            below = experiment_id == below_floor_run["id"]
            rows.append(
                SimpleNamespace(
                    experiment_id=experiment_id,
                    primary_element_ppm={"Mn": 900.0, "Ti": 450.0},
                    sensitivity_band_ppm={
                        "Mn": (800.0, 1000.0),
                        "Ti": (400.0, 500.0),
                    },
                    channel_missing_elements=(),
                    provenance={
                        "integration": {"primary": {"status": "converged"}},
                        "liquid_rows_used": (
                            _liquid_row(
                                "MnO(l)", certified_band, below_floor=below
                            ),
                            _liquid_row(
                                "TiO2(l)", (1500.0, 3000.0), below_floor=False
                            ),
                        ),
                    },
                )
            )
        return tuple(rows)

    monkeypatch.setattr(residue, "_predict_sossi_residue_cohort", fake_cohort)
    residuals, _candidates = score_store(
        context,
        engines=(Engine.OPENIMCC,),
        rail=Rail.RESIDUE_COMPOSITION,
    )

    def manganese(short_id: str):
        matched = []
        for residual in residuals:
            reference = context.observations.get(residual.reference)
            if reference is None:
                continue
            short = reference.experiment_id.rsplit("::", 1)[-1]
            formula = reference.identity.species.formula
            if short == short_id and formula == "Mn":
                matched.append(residual)
        assert len(matched) == 1
        return matched[0]

    flagged = manganese(below_floor_run["id"])
    assert flagged.numeric is not None
    floor_notices = [
        notice
        for notice in flagged.notices
        if notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        and notice.reason == "extrapolated_below_floor"
    ]
    assert len(floor_notices) == 1
    notice = floor_notices[0]
    assert notice.authority is Authority.EXTRAPOLATED
    assert notice.authority.value == "extrapolated"
    assert notice.affected_quantities == (
        Quantity.RESIDUE_COMPONENT_COMPOSITION,
    )
    assert "MnO(l)" in (notice.band or "")
    assert "1200" in (notice.band or "")
    assert "1600" in (notice.band or "")
    assert "3000" not in (notice.band or "")

    # Pick the comparison run from runs that actually carry an Mn residual, in
    # sorted order: iterating a set of ids is hash-order dependent, and some
    # Sossi rows have the source id itself as their short experiment id.
    mn_run_ids = sorted(
        {
            reference.experiment_id.rsplit("::", 1)[-1]
            for residual in residuals
            if (reference := context.observations.get(residual.reference))
            is not None
            and reference.source_id == "kems-012-sossi-2019"
            and reference.identity.species.formula == "Mn"
        }
        - {below_floor_run["id"]}
    )
    assert mn_run_ids
    other_id = mn_run_ids[0]
    unflagged = manganese(other_id)
    assert unflagged.numeric is not None
    assert all(
        notice.reason != "extrapolated_below_floor" for notice in unflagged.notices
    )


def test_hashimoto_below_liquid_floor_reaches_the_scored_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A below-floor Hashimoto liquid row is predicted and the scored residual carries the flag.

    The fixture is the provenance residue.py writes: the boolean nested under
    assumption_flags, and the row under liquid_rows_used. It does not set the
    top-level key. FeO(l) on the gas pack is the 1000-5000 K JANAF interval.
    No Hashimoto extract is below that floor, so one run is marked below it.
    """

    from simulator.battery.enums import (
        AdmissionStatus,
        Authority,
        Engine,
        NoticeKind,
        Quantity,
        Rail,
    )
    from simulator.battery.score import load_score_context, score_store
    import simulator.battery.residue as residue

    context = load_score_context(sources=("kems-015-hashimoto-1983",))
    # The production selector's only FeO(l) interval, and an in-band MgO(l) sibling.
    feo_band = (1000.0, 5000.0)
    mgo_band = (1800.0, 2200.0)
    below_floor_run: dict[str, str] = {}

    def liquid_record(
        channel: str,
        parent: str,
        name: str,
        source: str,
        band: tuple[float, float],
        *,
        below_floor: bool,
    ) -> dict:
        return {
            "channel": channel,
            "parent": parent,
            "liquid_row": {
                "row": name,
                "source": source,
                "T_fit_K": [band[0], band[1]],
                "extrapolated_below_floor": below_floor,
            },
        }

    def fake_cohort(experiments, _catalog, *, engine, **_kwargs):
        below_floor_run["id"] = str(experiments[0]["experiment_id"])
        rows = []
        for experiment in experiments:
            experiment_id = str(experiment["experiment_id"])
            below = experiment_id == below_floor_run["id"]
            values = {
                "FeO": 12.5,
                "MgO": 20.0,
                "SiO2": 30.0,
                "CaO": 4.0,
                "Al2O3": 5.0,
            }
            primary_provenance = {
                "experiment_id": experiment_id,
                "engine": engine,
                "integration": {"refinement_status": "converged"},
                "geometry_refusal_by_geometry": {},
                "liquid_rows_used": (
                    liquid_record(
                        "Fe",
                        "FeO",
                        "FeO(l)",
                        "JANAF",
                        feo_band,
                        below_floor=below,
                    ),
                    liquid_record(
                        "Mg",
                        "MgO",
                        "MgO(l)",
                        "Mg-009-SC-CP",
                        mgo_band,
                        below_floor=False,
                    ),
                ),
                "assumption_flags": {
                    "openimcc_liquid_row_extrapolated": below,
                },
            }
            rows.extend(
                (
                    SimpleNamespace(
                        experiment_id=experiment_id,
                        alpha_arm=residue._HASHIMOTO_PRIMARY_ALPHA_ARM,
                        primary_geometry_policy_id=residue._HASHIMOTO_PRIMARY_GEOMETRY,
                        primary_oxide_wt_pct=values,
                        sensitivity_band_wt_pct={
                            name: (value - 1.0, value + 1.0)
                            for name, value in values.items()
                        },
                        provenance=primary_provenance,
                    ),
                    SimpleNamespace(
                        experiment_id=experiment_id,
                        alpha_arm="alpha_runtime_catalog",
                        primary_geometry_policy_id=residue._HASHIMOTO_PRIMARY_GEOMETRY,
                        primary_oxide_wt_pct=values,
                        geometry_oxide_wt_pct={
                            residue._HASHIMOTO_PRIMARY_GEOMETRY: values
                        },
                        sensitivity_band_wt_pct={
                            name: (value - 1.0, value + 1.0)
                            for name, value in values.items()
                        },
                        provenance={
                            "engine": engine,
                            "alpha_arm": "alpha_runtime_catalog",
                        },
                    ),
                )
            )
        return tuple(rows)

    monkeypatch.setattr(residue, "_predict_hashimoto_residue_cohort", fake_cohort)
    residuals, _candidates = score_store(
        context,
        engines=(Engine.OPENIMCC,),
        rail=Rail.RESIDUE_COMPOSITION,
    )

    def iron(experiment_id: str):
        matched = []
        for residual in residuals:
            reference = context.observations.get(residual.reference)
            if reference is None:
                continue
            if (
                reference.experiment_id == experiment_id
                and reference.identity.species.formula == "FeO"
                and reference.admission.status is AdmissionStatus.ADMITTED
            ):
                matched.append(residual)
        assert len(matched) == 1
        return matched[0]

    flagged = iron(below_floor_run["id"])
    assert flagged.numeric is not None
    floor_notices = [
        notice
        for notice in flagged.notices
        if notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        and notice.reason == "extrapolated_below_floor"
    ]
    assert len(floor_notices) == 1
    notice = floor_notices[0]
    assert notice.origin == "hashimoto-residue-addendum-a"
    assert notice.authority is Authority.EXTRAPOLATED
    assert notice.authority.value == "extrapolated"
    assert notice.affected_quantities == (
        Quantity.RESIDUE_COMPONENT_COMPOSITION,
    )
    assert "FeO(l)" in (notice.band or "")
    assert "1000" in (notice.band or "")
    assert "5000" in (notice.band or "")
    assert "MgO" not in (notice.band or "")

    other_id = next(
        row.experiment_id
        for row in context.observations.values()
        if row.source_id == "kems-015-hashimoto-1983"
        and row.identity.species.formula == "FeO"
        and row.admission.status is AdmissionStatus.ADMITTED
        and row.experiment_id != below_floor_run["id"]
    )
    unflagged = iron(other_id)
    assert unflagged.numeric is not None
    assert all(
        notice.reason != "extrapolated_below_floor" for notice in unflagged.notices
    )
