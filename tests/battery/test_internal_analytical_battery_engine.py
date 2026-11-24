from __future__ import annotations

import csv
import json
import math
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.enums import (
    AmountBasis,
    Authority,
    Engine,
    EvidenceClass,
    ExecutionState,
    NoticeKind,
    Phase,
    Quantity,
    Rail,
    AdmissionStatus,
    RefusalReason,
    ResidualStatus,
)
from simulator.battery.identity import Identity
from simulator.battery.records import Composition, Species, State, Value
from simulator.battery import score as battery_score
from simulator.battery.score import predict_with_engine
from simulator.chemistry.kernel import ChemistryIntent
from simulator.diagnostic_helpers.binary_pot_battery import (
    BinaryPot,
    PO2_COMMANDED,
    Po2Request,
    _InternalAnalyticalInputRefusal,
    _new_internal_analytical_core,
    _internal_analytical_vapor_pressure_adapter,
    close_battery_engine_handle,
    composition_kg_and_mol,
    equilibrate_cell,
    open_battery_engine,
)
from simulator.chemistry.melt_activity import (
    melt_oxide_activity,
    single_cation_mole_fractions,
)
from tests.battery import factories as F
from tests.battery.test_melt_activity_requirements import _case, _composition


def test_internal_analytical_cell_reports_activity_not_gamma() -> None:
    point_id = "tsaplin2000_a_sio2_x0805_1473"
    benchmark_path = (
        Path(__file__).resolve().parents[2]
        / "benchmarks"
        / "results"
        / "melt_activity"
        / "benchmark-results.csv"
    )
    with benchmark_path.open(newline="", encoding="utf-8") as stream:
        benchmark = next(
            row
            for row in csv.DictReader(stream)
            if row["point_id"] == point_id
            and row["engine"] == "internal_analytic"
        )

    pot = BinaryPot(
        pot_id=point_id,
        kato_1993_table4_system=None,
        why="battery activity versus benchmark check",
        composition_wt_pct={"SiO2": 80.007793, "Na2O": 19.992207},
    )
    cell = equilibrate_cell(
        open_battery_engine("internal-analytical"),
        pot,
        temperature_K=1473.0,
        po2=Po2Request(mode=PO2_COMMANDED, po2_bar=1e-10),
        timeout_s=30.0,
        isolated=False,
    )
    _, composition_mol = composition_kg_and_mol(pot.composition_wt_pct)
    x_sio2 = single_cation_mole_fractions(composition_mol)["SiO2"]
    assert "SiO" in cell.melt_activities, (
        cell.status,
        cell.refusal_reason,
        cell.engine_reason,
        cell.melt_activities,
    )
    field_value = cell.melt_activities["SiO"]
    benchmark_value = float(benchmark["prediction"])
    benchmark_standard_state = json.loads(benchmark["details"])[
        "activity_standard_state"
    ]

    assert cell.status == "ok"
    assert x_sio2 == pytest.approx(benchmark_value, rel=1e-12)
    assert field_value == pytest.approx(benchmark_value, rel=1e-12)
    assert field_value != pytest.approx(benchmark_value / x_sio2, rel=0.1)
    assert cell.activities_standard_state == benchmark_standard_state


def _markova_first_interval_moles() -> dict[str, float]:
    # Markova 1984 first-interval residual-melt composition used by the
    # residue prediction regressions.
    markova_wt_pct = {
        "SiO2": 44.02,
        "Al2O3": 4.56,
        "FeO": 8.04,
        "MgO": 36.44,
        "CaO": 6.95,
    }
    return composition_kg_and_mol(markova_wt_pct)[1]


def test_internal_analytical_low_temperature_result_ignores_prior_core_diagnostic() -> None:
    """A 100 C Markova request cannot publish a prior call's activity map."""
    from copy import deepcopy

    stale_diagnostic = {
        "activities": {"SiO2": 0.25},
        "vapor_pressure_authority": {"status": "authoritative"},
    }
    outputs = []
    for prior_diagnostic in (stale_diagnostic, {}):
        core = _new_internal_analytical_core()
        core._last_vapor_pressure_diagnostic = deepcopy(prior_diagnostic)
        output = _internal_analytical_vapor_pressure_adapter(
            core=core,
            temperature_C=100.0,
            pressure_bar=0.0,
            composition_kg=None,
            composition_mol=_markova_first_interval_moles(),
            fO2_log=0.0,
            po2_request=Po2Request(mode=PO2_COMMANDED, po2_bar=1.0),
        )
        outputs.append(vars(output))

    assert outputs[0] == outputs[1]
    assert outputs[0]["activity_coefficients"] == {}
    assert "SiO2" not in outputs[0]["diagnostics"]["vapor_pressure_diagnostic"].get(
        "activities", {}
    )


def test_internal_analytical_oxygen_balance_uses_configured_pack(monkeypatch) -> None:
    from types import SimpleNamespace

    from simulator.diagnostic_helpers import binary_pot_battery as battery

    created: list[tuple[str, str | None]] = []

    class OpenImccBackendStub:
        def __init__(self, engine_name: str, *, pack_name: str | None = None) -> None:
            created.append((engine_name, pack_name))
            self.pack_name = pack_name

    seen: dict[str, object] = {}

    def capture_adapter(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(status="ok")

    monkeypatch.setattr(battery, "_OpenImccBatteryBackend", OpenImccBackendStub)
    monkeypatch.setattr(
        battery, "_internal_analytical_vapor_pressure_adapter", capture_adapter
    )
    backend = battery._InternalAnalyticalBatteryBackend(
        oxygen_balance_pack="non-default-oxygen-pack"
    )

    backend.equilibrate(
        temperature_C=1200.0,
        po2_request=battery.Po2Request(
            mode=battery.PO2_OXYGEN_BALANCE_EFFUSION,
            po2_bar=1.0e-6,
        ),
    )

    assert created == [("openimcc", "non-default-oxygen-pack")]
    assert getattr(seen["oxygen_balance_backend"], "pack_name") == (
        "non-default-oxygen-pack"
    )


def test_internal_analytical_battery_results_are_independent_of_request_order() -> None:
    """Every request keeps its full result across low-T, normal, and CO2 paths."""
    from copy import deepcopy
    from types import SimpleNamespace

    from simulator.diagnostic_helpers.binary_pot_battery import (
        _InternalAnalyticalBatteryBackend,
    )
    from simulator.state import Atmosphere

    composition_mol = _markova_first_interval_moles()
    request_specs = {
        "low_temperature": {
            "temperature_C": 100.0,
            "pressure_bar": 0.0,
            "fO2_log": 0.0,
            "po2_bar": 1.0,
            "co2_background": False,
        },
        "normal": {
            "temperature_C": 1200.0,
            "pressure_bar": 0.0,
            "fO2_log": 0.0,
            "po2_bar": 1.0,
            "co2_background": False,
        },
        "co2_notice": {
            "temperature_C": 1000.0,
            "pressure_bar": 1.0,
            "fO2_log": math.log10(0.5),
            "po2_bar": 0.5,
            "co2_background": True,
        },
    }
    orders = (
        ("normal", "co2_notice", "low_temperature"),
        ("low_temperature", "co2_notice", "normal"),
    )
    outputs_by_order: list[dict[str, dict[str, object]]] = []
    for order in orders:
        backend = _InternalAnalyticalBatteryBackend()
        core = backend._core
        outputs: dict[str, dict[str, object]] = {}
        for request_name in order:
            spec = request_specs[request_name]
            if spec["co2_background"]:
                core.melt.atmosphere = Atmosphere.CONTROLLED_O2
                core.melt.temperature_C = float(spec["temperature_C"])
                core.melt.p_total_mbar = float(spec["pressure_bar"]) * 1000.0
                core.melt.pO2_mbar = float(spec["po2_bar"]) * 1000.0
                core.melt.background_gas_species = "CO2"
                core.melt.background_gas_mole_fraction = 1.0
                core._headspace_equilibrated_pO2_bar(0.0)
                assert core._headspace_co2_dissociation_diagnostic["notices"]
            else:
                core._headspace_co2_dissociation_diagnostic = {}
            result = backend.equilibrate(
                temperature_C=float(spec["temperature_C"]),
                pressure_bar=float(spec["pressure_bar"]),
                composition_mol=composition_mol,
                fO2_log=float(spec["fO2_log"]),
                po2_request=Po2Request(
                    mode=PO2_COMMANDED,
                    po2_bar=float(spec["po2_bar"]),
                ),
            )
            outputs[request_name] = deepcopy(vars(result))
            if request_name == "normal":
                hot_diagnostic = dict(
                    backend._core._last_vapor_pressure_diagnostic
                )
                assert hot_diagnostic.get("activities")
                backend._core._last_high_t_melt_activity = {
                    "provider": "previous_call",
                }
                with pytest.raises(RuntimeError, match="liquid_fraction_invalid"):
                    backend._core._refresh_vapor_pressures_from_kernel(
                        SimpleNamespace(
                            vapor_pressures_Pa={},
                            vapor_pressures_source={},
                            liquid_fraction=float("nan"),
                            warnings=[],
                        ),
                        observation_pO2_bar=1.0,
                    )
                assert backend._core._last_vapor_pressure_diagnostic == {}
                assert backend._core._last_high_t_melt_activity == {}
        outputs_by_order.append(outputs)

    assert outputs_by_order[0] == outputs_by_order[1]
    assert outputs_by_order[0]["co2_notice"]["diagnostics"][
        "vapor_pressure_diagnostic"
    ]["co2_dissociation"]["notices"]


def test_internal_analytical_scores_multi_cation_parent_activity(monkeypatch) -> None:
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery._call_with_hard_timeout",
        lambda call, _timeout: call(),
    )
    experiment, bench, observation = _case(
        composition=_composition(("SiO2", "0.6"), ("Na2O", "0.4")),
        oxygen=Decimal("-10"),
        formula="Na2O",
    )
    observation = replace(
        observation,
        identity=replace(
            observation.identity, fO2_Pa=State.of(Decimal("0.00001")),
        ),
    )
    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL,
        observation,
        experiment=experiment,
        bench=bench,
        handles={},
        isolated=False,
    )

    assert prediction.value is not None, prediction.refusal_detail
    assert prediction.identity.species.formula == "Na2O"
    assert prediction.refusal_reason is None

    from simulator.battery.generators.bench import activity_request_for_engine

    request = activity_request_for_engine(experiment, observation, "internal-analytical")
    assert request is not None and request.payload is not None
    handle = open_battery_engine("internal-analytical")
    direct = handle.backend.equilibrate(
        temperature_C=request.payload["temperature_C"],
        composition_mol=request.payload["composition_mol"],
        fO2_log=request.payload["fO2_log"],
        po2_request=Po2Request(mode=PO2_COMMANDED, po2_bar=1e-10),
    )
    diagnostic = direct.diagnostics["vapor_pressure_diagnostic"]
    owner_record = next(
        record
        for record in diagnostic["vapor_pressure_numerator_provenance"].values()
        if record.get("melt_oxide_component") == "NaO0.5"
    )
    parent_activity = melt_oxide_activity("Na2O", request.payload["composition_mol"])
    assert parent_activity is not None
    assert "melt_oxide_parent_formula" not in owner_record
    assert "melt_parent_oxide_activity" not in owner_record
    assert float(prediction.value) == pytest.approx(
        parent_activity.thermodynamic_parent_activity(), rel=1e-12,
    )
    assert parent_activity.thermodynamic_parent_activity() != pytest.approx(
        owner_record["melt_oxide_activity"]
    )


def _assert_internal_activity_residual_matches_production(
    *,
    source_id: str,
    observation_id: str,
) -> None:
    from simulator.battery.generators.bench import activity_request_for_engine

    root = Path(__file__).resolve().parents[2]
    context = battery_score.load_score_context(root, sources=(source_id,))
    observation = context.observations[observation_id]
    residual, _ = battery_score.compile_residual(
        observation,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        comparison_ids={observation_id},
    )
    assert residual.execution.state is ExecutionState.PRODUCED
    assert residual.numeric is not None

    converted = battery_score.fusion_comparison_reference(
        observation,
        engine=Engine.INTERNAL_ANALYTICAL,
    )
    identity = converted.identity
    temperature = battery_score.temperature_of(identity)
    assert temperature is not None and converted.value.point is not None
    experiment = context.experiments[converted.experiment_id]
    request = activity_request_for_engine(
        experiment, converted, Engine.INTERNAL_ANALYTICAL.value
    )
    assert request is not None and request.payload is not None

    expected = melt_oxide_activity(
        identity.species.formula,
        request.payload["composition_mol"],
        temperature_K=float(temperature),
    )
    assert expected is not None
    expected_residual, _, _ = battery_score.populate_numeric(
        quantity=Quantity.ACTIVITY,
        candidate=Decimal(str(expected.thermodynamic_parent_activity())),
        reference=converted.value.point,
        source_relation=residual.source_relation,
        operation_override=residual.numeric.operation,
    )
    assert expected_residual is not None
    assert residual.numeric.value == pytest.approx(expected_residual.value, rel=1e-9)
    fallback = next(
        notice for notice in residual.notices if notice.kind is NoticeKind.FALLBACK
    )
    assert fallback.authority is Authority.BRIDGE
    assert fallback.reason == "melt_oxide_activity has no certified evidence tier"


def test_internal_analytical_scores_guo_2021_mgo_row() -> None:
    # d3285901c restores Table 5 measured activities and 2f342093c restores
    # their printed slag compositions. Table 5 p. 2728 row 2 has a(MgO)=0.5121
    # and the printed MgO slag composition below.
    source_id = "guo-2021-mgo-activity-cmas-slag"
    root = Path(__file__).resolve().parents[2]
    context = battery_score.load_score_context(root, sources=(source_id,))
    expected_composition = {
        "CaO": Decimal("0.404508756338"),
        "SiO2": Decimal("0.330114102073"),
        "MgO": Decimal("0.145390940259"),
        "Al2O3": Decimal("0.119986201331"),
    }
    matches = [
        observation
        for observation in context.observations.values()
        if observation.source_id == source_id
        and observation.locator is not None
        and observation.locator.table == "5"
        and observation.identity.species.formula == "MgO"
        and observation.identity.composition is not None
        and observation.identity.composition.is_value
        and observation.identity.composition.value.as_map() == expected_composition
    ]
    assert len(matches) == 1
    observation = matches[0]
    assert observation.value.point == Decimal("0.5121")
    _assert_internal_activity_residual_matches_production(
        source_id=source_id,
        observation_id=observation.observation_id,
    )


def test_internal_analytical_scores_cho_suito_1994_sio2_row() -> None:
    # d-338: the paper prints SiO2(s) and leaves its polymorph unstated.
    # The scorer takes the JANAF crystal stable at 1873 K. The internal
    # activity residual is the pre-d-165 production value.
    root = Path(__file__).resolve().parents[2]
    context = battery_score.load_score_context(
        root, sources=("cho-suito-1994-cas-slag-activities",)
    )
    observation_id = (
        "cho-suito-1994-cas-slag-activities::"
        "cho_suito_1994_t5_acs2_sio2_cn"
    )
    observation = context.observations[observation_id]
    assert observation.identity.reference_state.value.endmember.polymorph.is_unknown
    residual, _ = battery_score.compile_residual(
        observation,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        comparison_ids={observation_id},
    )
    assert residual.execution.state is ExecutionState.PRODUCED
    assert residual.status is ResidualStatus.NO_BAND
    assert residual.numeric is not None
    assert residual.numeric.value == Decimal("0.3319086250579114")
    assert residual.score_eligible is False
    assert residual.refusal is not None
    assert residual.refusal.reason is not RefusalReason.IDENTITY_INCOMPLETE
    assert residual.refusal.detail["reason"] == "conversion_uncertainty_unprinted"
    assert residual.refusal.detail["crystal_table"] == "O-035"
    assert any(
        notice.reason.startswith(
            "reference_polymorph_inferred_stable_phase:"
        )
        and '"phase": "cristobalite_high"' in notice.reason
        and '"table_id": "O-035"' in notice.reason
        for notice in residual.notices
    )
    assert observation.identity.reference_state.value.endmember.polymorph.is_unknown


def test_internal_analytical_scores_feo_with_ideal_fallback_notice() -> None:
    from simulator.battery.generators.bench import activity_request_for_engine

    temperature = Decimal("1873")
    experiment, bench, observation = _case(
        composition=_composition(
            ("CaO", "0.4"), ("SiO2", "0.4"), ("FeO", "0.2")
        ),
        temperature=Value.point_of(temperature),
        formula="FeO",
    )
    observation = replace(
        observation,
        identity=replace(
            observation.identity,
            temperature_K=State.of(temperature),
        ),
    )
    request = activity_request_for_engine(
        experiment, observation, Engine.INTERNAL_ANALYTICAL.value
    )
    assert request is not None and request.payload is not None
    expected = melt_oxide_activity(
        "FeO",
        request.payload["composition_mol"],
        temperature_K=float(temperature),
    )
    assert expected is not None

    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL,
        observation,
        experiment=experiment,
        bench=bench,
        handles={},
        isolated=False,
    )

    assert prediction.execution.state is ExecutionState.PRODUCED
    assert float(prediction.value) == pytest.approx(
        expected.thermodynamic_parent_activity(), rel=1e-9
    )
    notice = next(
        notice for notice in prediction.notices if notice.kind is NoticeKind.FALLBACK
    )
    assert notice.authority is Authority.BRIDGE
    assert notice.reason == expected.warning


def test_internal_analytical_types_unsupported_activity_species() -> None:
    experiment, bench, observation = _case(
        composition=_composition(("ZrO2", "0.5"), ("SiO2", "0.5")),
        formula="ZrO2",
    )

    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL,
        observation,
        experiment=experiment,
        bench=bench,
        handles={},
        isolated=False,
    )

    assert prediction.execution.state is ExecutionState.UNSUPPORTED
    assert prediction.refusal_reason is RefusalReason.OUTSIDE_SUPPORTED_SPECIES
    assert prediction.refusal_detail["formula"] == "ZrO2"


def test_internal_analytical_types_unsupported_activity_composition() -> None:
    experiment, bench, observation = _case(
        composition=_composition(("MgO", "0.5"), ("ZrO2", "0.5")),
        formula="MgO",
    )

    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL,
        observation,
        experiment=experiment,
        bench=bench,
        handles={},
        isolated=False,
    )

    assert prediction.execution.state is ExecutionState.UNSUPPORTED
    assert prediction.refusal_reason is RefusalReason.OUTSIDE_SUPPORTED_SPECIES
    assert prediction.refusal_detail["unsupported_composition_components"] == ["ZrO2"]


def test_internal_analytical_predicts_below_gamma_band_with_notice() -> None:
    from simulator.battery.generators.bench import activity_request_for_engine

    temperature = Decimal("1600")
    experiment, bench, observation = _case(
        composition=_composition(("Na2O", "0.4"), ("SiO2", "0.6")),
        temperature=Value.point_of(temperature),
        formula="Na2O",
    )
    observation = replace(
        observation,
        identity=replace(
            observation.identity,
            temperature_K=State.of(temperature),
        ),
    )
    request = activity_request_for_engine(
        experiment, observation, Engine.INTERNAL_ANALYTICAL.value
    )
    assert request is not None and request.payload is not None
    expected = melt_oxide_activity(
        "Na2O",
        request.payload["composition_mol"],
        temperature_K=float(temperature),
    )
    assert expected is not None

    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL,
        observation,
        experiment=experiment,
        bench=bench,
        handles={},
        isolated=False,
    )

    assert prediction.execution.state is ExecutionState.PRODUCED
    assert float(prediction.value) == pytest.approx(
        expected.thermodynamic_parent_activity(), rel=1e-9
    )
    assert prediction.authority is Authority.EXTRAPOLATED
    notice = next(
        notice
        for notice in prediction.notices
        if notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
    )
    assert notice.authority is Authority.EXTRAPOLATED
    assert "constant_gamma_extrapolated_out_of_domain" in notice.reason
    assert notice.band == "[1673, 1673] K"


def test_internal_analytical_keeps_missing_activity_composition_typed() -> None:
    experiment, bench, observation = _case(
        composition=None,
        formula="MgO",
    )

    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL,
        observation,
        experiment=experiment,
        bench=bench,
        handles={},
        isolated=False,
    )

    assert prediction.value is None
    assert prediction.execution.state is ExecutionState.NOT_PROBED
    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert prediction.refusal_detail["reason"] == "missing_evidence"


def test_internal_analytical_request_forwards_point_oxygen_without_redox() -> None:
    from simulator.battery.generators.bench import activity_request_for_engine

    experiment, _bench, observation = _case(
        composition=_composition(("SiO2", "0.6"), ("Na2O", "0.4")),
        oxygen=Decimal("-10"),
        formula="SiO2",
    )
    request = activity_request_for_engine(experiment, observation, "internal-analytical")

    assert request is not None and request.payload is not None
    assert request.payload["fO2_log"] == -10.0


def test_internal_analytical_forwards_supplied_oxygen_without_redox(monkeypatch) -> None:
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery._call_with_hard_timeout",
        lambda call, _timeout: call(),
    )
    experiment, bench, observation = _case(
        composition=_composition(("SiO2", "0.6"), ("Na2O", "0.4")),
        oxygen=Decimal("-10"),
        formula="SiO2",
    )
    observation = replace(
        observation,
        identity=replace(
            observation.identity, fO2_Pa=State.of(Decimal("0.00001")),
        ),
    )
    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL,
        observation,
        experiment=experiment,
        bench=bench,
        handles={},
        isolated=False,
    )

    assert prediction.value is not None, prediction.refusal_detail
    assert prediction.refusal_reason is None


def test_internal_analytical_uses_identity_oxygen_without_redox(monkeypatch) -> None:
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery._call_with_hard_timeout",
        lambda call, _timeout: call(),
    )
    experiment, bench, observation = _case(
        composition=_composition(("SiO2", "0.6"), ("Na2O", "0.4")),
        formula="SiO2",
    )
    observation = replace(
        observation,
        identity=replace(
            observation.identity, fO2_Pa=State.of(Decimal("0.00001")),
        ),
    )
    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL,
        observation,
        experiment=experiment,
        bench=bench,
        handles={},
        isolated=False,
    )

    assert prediction.value is not None, prediction.refusal_detail
    assert prediction.refusal_reason is None


def test_internal_analytical_activity_does_not_require_oxygen() -> None:
    experiment, bench, observation = _case(
        composition=_composition(("SiO2", "0.6"), ("Na2O", "0.4")),
        formula="SiO2",
    )
    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL,
        observation,
        experiment=experiment,
        bench=bench,
        handles={},
        isolated=False,
    )

    assert prediction.value is not None
    assert prediction.execution.state is ExecutionState.PRODUCED
    assert prediction.refusal_reason is None


def _hand_built_observation() -> tuple[object, Identity]:
    composition = Composition(
        basis="ordered_complete_mole_inventory",
        components=(
            ("SiO2", Decimal("0.5")),
            ("FeO", Decimal("0.3")),
            ("MgO", Decimal("0.2")),
        ),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    activity = F.activity_identity(
        composition=composition,
        T_K=Decimal("1500"),
        fO2_Pa=Decimal("0.0001"),
        total_P=Decimal("100000"),
    )
    identity = replace(
        activity,
        quantity=Quantity.P_PARTIAL,
        species=Species("Fe", Phase.G),
    )
    observation = F.observation(
        "internal-analytical-hand-built-fe-partial",
        "internal-analytical-test",
        identity,
        Decimal("1e-3"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        admission=AdmissionStatus.ADMITTED,
        source_id="internal-analytical-test",
    )
    return observation, identity


def test_internal_analytical_observation_uses_core_vapor_pressure_intent() -> None:
    from simulator.chemistry.kernel.capabilities import ChemistryIntent

    observation, identity = _hand_built_observation()
    handles: dict[str, object] = {}
    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL,
        observation,
        handles=handles,
        isolated=False,
    )

    assert prediction.value is not None
    assert prediction.unit == "Pa"
    assert prediction.identity == identity
    assert prediction.version is not None
    assert "PyrolysisSimulator._dispatch_only" in prediction.version
    assert ChemistryIntent.VAPOR_PRESSURE.value in prediction.version

    backend = handles[Engine.INTERNAL_ANALYTICAL.value].backend
    core = backend._core
    expected = core._dispatch_only(
        ChemistryIntent.VAPOR_PRESSURE,
        control_inputs={
            "pO2_bar": 1e-9,
            "intrinsic_fO2_log": -9.0,
            "vacuum_floor_bar": core._vacuum_floor_bar(),
            "body": "",
            "process_phase": "hot_train",
            "ambient_pressure_bar": None,
        },
        fO2_log=-9.0,
    ).diagnostic["vapor_pressures_Pa"]["Fe"]
    assert prediction.value == pytest.approx(Decimal(str(expected)), rel=1e-12)


def test_internal_analytical_refuses_missing_composition() -> None:
    with pytest.raises(_InternalAnalyticalInputRefusal) as raised:
        _internal_analytical_vapor_pressure_adapter(
            core=None,
            temperature_C=1500.0 - 273.15,
            pressure_bar=1.0,
            composition_kg=None,
            composition_mol=None,
            fO2_log=-9.0,
            po2_request=Po2Request(mode=PO2_COMMANDED, po2_bar=1e-9),
        )
    assert raised.value.category == 1
    assert raised.value.code == "internal_analytical_missing_composition"


def test_internal_analytical_refuses_missing_oxygen_condition() -> None:
    with pytest.raises(_InternalAnalyticalInputRefusal) as raised:
        _internal_analytical_vapor_pressure_adapter(
            core=None,
            temperature_C=1500.0 - 273.15,
            pressure_bar=1.0,
            composition_kg={"SiO2": 0.5, "FeO": 0.5},
            composition_mol=None,
            fO2_log=None,
            po2_request=None,
        )
    assert raised.value.category == 1
    assert raised.value.code == "internal_analytical_missing_oxygen_condition"


def test_internal_analytical_refuses_nonfinite_temperature() -> None:
    with pytest.raises(_InternalAnalyticalInputRefusal) as raised:
        _internal_analytical_vapor_pressure_adapter(
            core=None,
            temperature_C=math.inf,
            pressure_bar=1.0,
            composition_kg={"SiO2": 0.5, "FeO": 0.5},
            composition_mol=None,
            fO2_log=-9.0,
            po2_request=Po2Request(mode=PO2_COMMANDED, po2_bar=1e-9),
        )
    assert raised.value.category == 2
    assert raised.value.code == "internal_analytical_invalid_temperature"


def test_internal_analytical_residue_adapter_skips_diagnostic_shadow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from engines.vaporock import VapoRockProvider

    original_dispatch = VapoRockProvider.dispatch
    shadow_calls = 0

    def count_shadow_calls(self, request):
        nonlocal shadow_calls
        shadow_calls += 1
        return original_dispatch(self, request)

    monkeypatch.setattr(VapoRockProvider, "dispatch", count_shadow_calls)
    core = _new_internal_analytical_core()
    from simulator.vapour_rail import catalog as vapour_catalog

    original_compile_catalog = vapour_catalog.compile_vapour_rail_catalog
    catalog_compiles = 0

    def count_catalog_compiles(payload, **kwargs):
        nonlocal catalog_compiles
        catalog_compiles += 1
        return original_compile_catalog(payload, **kwargs)

    monkeypatch.setattr(
        vapour_catalog, "compile_vapour_rail_catalog", count_catalog_compiles
    )
    inputs = {
        "temperature_C": 1500.0 - 273.15,
        "pressure_bar": 1.0,
        "composition_kg": None,
        "composition_mol": {"SiO2": 0.5, "FeO": 0.3, "MgO": 0.2},
        "fO2_log": -9.0,
        "po2_request": Po2Request(mode=PO2_COMMANDED, po2_bar=1e-9),
    }

    with_shadow = _internal_analytical_vapor_pressure_adapter(
        core=core,
        **inputs,
    )
    shadow_call_count = shadow_calls
    catalog_compile_count = catalog_compiles
    assert shadow_call_count == 1
    assert catalog_compile_count > 0

    residue_prediction = _internal_analytical_vapor_pressure_adapter(
        core=core,
        **inputs,
        include_diagnostic_shadows=False,
    )

    assert shadow_calls == shadow_call_count
    assert catalog_compiles == catalog_compile_count
    assert residue_prediction.vapor_pressures_Pa == with_shadow.vapor_pressures_Pa


def _magemin_gate_backend(core):
    assert core._liquid_fraction_provider == 'magemin'
    core._register_freeze_gate_liquid_fraction_providers()
    gate_provider = core._chem_registry.fallback_for(
        ChemistryIntent.GATE_LIQUID_FRACTION
    )
    assert gate_provider is not None
    return gate_provider._ensure_backend()


def test_internal_analytical_battery_uses_freeze_gate_phase_basis(
    request,
) -> None:
    from simulator.melt_backend import openimcc_bridge

    handle = open_battery_engine('internal-analytical')
    request.addfinalizer(lambda: close_battery_engine_handle(handle))
    core = handle.backend._core
    magemin = _magemin_gate_backend(core)
    if magemin is None or not magemin.is_available():
        pytest.skip('MAGEMin backend unavailable after initialization')
    request.addfinalizer(magemin.close)

    pot = BinaryPot(
        pot_id='internal-analytical-liquid-phase-contract',
        kato_1993_table4_system=None,
        why='real freeze-gate phase basis through equilibrate_cell',
        composition_wt_pct={
            'SiO2': 47.0,
            'Al2O3': 14.0,
            'FeO': 10.0,
            'MgO': 9.0,
            'CaO': 11.0,
            'Na2O': 3.0,
            'K2O': 1.0,
            'TiO2': 2.0,
            'Cr2O3': 1.0,
            # The original 1 wt% + 1 wt% drops exceeded the projection's
            # 1 wt% limit and selected the Kress fallback. Keep both dropped
            # species while staying inside that limit so MAGEMin supplies the
            # phase basis this receipt exercises.
            'MnO': 0.49,
            'P2O5': 0.49,
        },
    )
    cell = equilibrate_cell(
        handle,
        pot,
        temperature_K=1800.0,
        po2=Po2Request(mode=PO2_COMMANDED, po2_bar=1.0e-9),
        timeout_s=60.0,
        isolated=False,
    )

    authority = core._last_high_t_melt_activity
    assert cell.status == 'ok'
    assert authority["vapour_channel_phase_basis"] == "liquid"
    assert authority["phase_basis_source"] == (
        "freeze_gate_authority:gate_liquid_fraction:fallback:magemin-shadow:"
        "composition_projected"
    )
    phase_notice = authority['phase_basis_notice']
    assert phase_notice['authority'] == 'extrapolated'
    assert {
        row['component'] for row in phase_notice['dropped_components']
    } == {'MnO', 'P2O5'}
    assert authority["vapour_channel_switch_K"] == (
        openimcc_bridge._temperature_domain_K()[0]
    )


@pytest.mark.parametrize(
    (
        "source_id",
        "observation_id",
        "expected_basis",
        "expected_switch",
        "expected_notice",
        "expected_sio_source",
        "expected_gate_reason",
    ),
    [
        pytest.param(
            "stolyarova-1996-cao-alumina-silica-kems",
            "stolyarova-1996-cao-alumina-silica-kems::stolyarova_1996_table2_pSiO_1933k::T=1933.00:h=0bf22dda389c",
            "missing",
            1950.0,
            "openimcc_vapour_phase_signal_missing",
            "builtin_authoritative",
            # L predicts positive SiO2 outside MAGEMin's calibration band
            # with an extrapolation notice, so the solver's downstream
            # refusal is the observed mode-vector mass deficit.
            "magemin_mode_vector_mass_deficit",
            id="stolyarova-magemin-mode-vector-mass-deficit",
        ),
        pytest.param(
            "shornikov-2026-wollastonite-kems",
            "shornikov-2026-wollastonite-kems::shornikov_2026_SiO_partial_pressure_table3_row_09",
            "missing",
            1950.0,
            "openimcc_vapour_phase_signal_missing",
            "builtin_authoritative",
            "magemin_mode_vector_mass_deficit",
            id="wollastonite-high-temperature-provider-refusal",
        ),
        pytest.param(
            "shornikov-2026-wollastonite-kems",
            "shornikov-2026-wollastonite-kems::shornikov_2026_SiO_partial_pressure_table3_row_03",
            "missing",
            1950.0,
            "openimcc_vapour_phase_signal_missing",
            "builtin_authoritative",
            "magemin_mode_vector_mass_deficit",
            id="wollastonite-lower-temperature-provider-refusal",
        ),
        pytest.param(
            "shornikov-2026-wollastonite-kems",
            "shornikov-2026-wollastonite-kems::shornikov_2026_SiO_partial_pressure_table3_row_09",
            "liquid",
            None,
            "openimcc_liquid_sio_below_1950K_band",
            "openimcc",
            None,
            marks=pytest.mark.skip(
                reason=(
                    "0 of 55 measured in-window composition groups yield a "
                    "MAGEMin ig liquid curve (studio probe 2026-11-15)"
                ),
            ),
            id="no-measured-magemin-liquid-group",
        ),
    ],
)
def test_internal_analytical_battery_score_records_vapour_phase_basis(
    tmp_path: Path,
    request,
    source_id: str,
    observation_id: str,
    expected_basis: str,
    expected_switch: float | None,
    expected_notice: str | None,
    expected_sio_source: str,
    expected_gate_reason: str | None,
) -> None:
    parent_core = _new_internal_analytical_core()
    parent_magemin = _magemin_gate_backend(parent_core)
    if parent_magemin is None or not parent_magemin.is_available():
        pytest.skip("MAGEMin backend unavailable in the scorer parent")
    request.addfinalizer(parent_magemin.close)

    root = Path(__file__).resolve().parents[2]
    context = battery_score.load_score_context(root, sources=(source_id,))
    observation = context.observations[observation_id]
    scoped_context = replace(
        context,
        observations={observation_id: observation},
        origins={observation_id: context.origins.get(observation_id, "")},
    )
    output_path = tmp_path / "phase-basis.jsonl"
    written, _metadata = battery_score._score_store_to_jsonl(
        scoped_context,
        output_path,
        root=root,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.SIO_EVOLUTION,
        workers=1,
    )
    scored_rows = [
        json.loads(line)
        for line in output_path.with_name(output_path.name + ".partial")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert written == 1
    scored_row = next(
        (row for row in scored_rows if row.get("reference") == observation_id),
        None,
    )
    assert scored_row is not None
    assert scored_row["status"] == "no_band", scored_row.get("refusal")
    candidate = scored_row["candidate_observation"]
    assert isinstance(candidate, dict)
    engine = candidate["engine"]
    record = json.loads(engine["version"])
    assert record["phase_basis"] == expected_basis
    assert record["phase_basis_source"]
    if expected_basis == "liquid":
        assert record["phase_basis_source"].startswith(
            "freeze_gate_authority:gate_liquid_fraction:fallback:magemin-shadow"
        )
    if expected_switch is None:
        from simulator.melt_backend import openimcc_bridge

        expected_switch = openimcc_bridge._temperature_domain_K()[0]
    assert record["applied_vapour_switch_K"] == expected_switch
    assert record["vapor_pressures_source"]["SiO"].startswith(
        expected_sio_source
    )
    scored_notices = json.dumps(candidate.get("notices") or ())
    if expected_notice is not None:
        assert any(
            notice["code"] == expected_notice for notice in record["notices"]
        )
        assert expected_notice in scored_notices
    if expected_basis == "liquid":
        assert not any(
            notice.get("source") == "vaporock"
            and notice.get("destination") == "antoine_fallback"
            for notice in candidate.get("notices") or ()
        )
    if expected_basis == "missing":
        assert (
            record["phase_basis_source"]
            == "freeze_gate_authority_fallback_or_projected"
        )
        phase_detail = record["phase_basis_detail"]
        assert phase_detail["code"] == "gate_curve_source_not_authoritative"
        assert phase_detail["provider_outcome"] == "stable_refusal"
        gate_evidence = phase_detail["diagnostic"]
        assert gate_evidence["provider"] == "magemin"
        assert gate_evidence["status"] == "out_of_domain"
        assert expected_gate_reason is not None
        assert expected_gate_reason in str(gate_evidence["reason"])


def test_internal_analytical_timeout_kills_magemin_descendants(
    monkeypatch,
    request,
    tmp_path: Path,
) -> None:
    import os
    import signal
    import time
    from concurrent.futures import ThreadPoolExecutor

    import simulator.diagnostic_helpers.binary_pot_battery as battery

    parent_core = _new_internal_analytical_core()
    parent_magemin = _magemin_gate_backend(parent_core)
    if (
        parent_magemin is None
        or not parent_magemin.is_available()
        or parent_magemin._bridge != "subprocess"
    ):
        pytest.skip("MAGEMin subprocess backend unavailable in the parent")
    request.addfinalizer(parent_magemin.close)

    monkeypatch.setenv("REGOLITH_ENGINE_WORKER_INHERIT_PROCESS_GROUP", "0")
    monkeypatch.delenv("REGOLITH_ENGINE_WORKER_INHERIT_PROCESS_GROUP")
    trace_path = tmp_path / "magemin-processes.jsonl"
    sitecustomize_path = tmp_path / "sitecustomize.py"
    sitecustomize_path.write_text(
        "\n".join(
            (
                "import json",
                "import os",
                "import signal",
                "import subprocess",
                "_trace = os.environ.get('D207_MAGEMIN_PROCESS_TRACE')",
                "if _trace:",
                "    _popen = subprocess.Popen",
                "    class _TraceMageminPopen(_popen):",
                "        def __init__(self, args, *positional, **keywords):",
                "            super().__init__(args, *positional, **keywords)",
                "            command = (args[0] if isinstance(args, (tuple, list)) "
                "else args)",
                "            if 'magemin' not in os.path.basename("
                "os.fsdecode(command)).lower():",
                "                return",
                "            os.kill(self.pid, signal.SIGSTOP)",
                "            event = {",
                "                'launcher_pid': os.getpid(),",
                "                'launcher_pgid': os.getpgrp(),",
                "                'binary_pid': self.pid,",
                "                'binary_pgid': os.getpgid(self.pid),",
                "            }",
                "            descriptor = os.open(",
                "                _trace, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600",
                "            )",
                "            try:",
                "                os.write(descriptor, (json.dumps(event) + '\\n').encode())",
                "            finally:",
                "                os.close(descriptor)",
                "    subprocess.Popen = _TraceMageminPopen",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    old_pythonpath = os.environ.get("PYTHONPATH")
    pythonpath = [str(tmp_path)]
    if old_pythonpath:
        pythonpath.append(old_pythonpath)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(pythonpath))
    monkeypatch.setenv("D207_MAGEMIN_PROCESS_TRACE", str(trace_path))

    worker = battery._IsolatedCellWorker("internal-analytical")
    cell_pid = None
    nested_events = []
    future = None
    executor = ThreadPoolExecutor(max_workers=1)

    def read_events():
        if not trace_path.exists():
            return []
        return [
            json.loads(line)
            for line in trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    try:
        future = executor.submit(
            worker.request,
            {
                "pot_id": "d207-magemin-timeout",
                "why": "real isolated-cell MAGEMin descendant cleanup",
                "composition_wt_pct": {"CaO": 48.28, "SiO2": 51.72},
                "temperature_K": 1933.0,
                "engine": "internal-analytical",
                "po2": {"mode": PO2_COMMANDED, "po2_bar": 1.0e-9},
                "timeout_s": 120.0,
            },
            timeout_s=120.0,
        )
        startup_deadline = time.monotonic() + 60.0
        while time.monotonic() < startup_deadline:
            process = worker._worker.process
            if process is not None and process.pid is not None:
                cell_pid = int(process.pid)
                break
            if future.done():
                future.result()
            time.sleep(0.02)
        assert cell_pid is not None, "isolated internal-analytical cell did not start"

        magemin_deadline = time.monotonic() + 90.0
        while time.monotonic() < magemin_deadline:
            nested_events = read_events()
            if nested_events:
                break
            if future.done():
                future.result()
            time.sleep(0.02)
        assert nested_events, "isolated cell did not launch the MAGEMin binary"
        event = nested_events[0]
        assert event["launcher_pid"] != cell_pid
        assert event["launcher_pgid"] == cell_pid
        assert event["binary_pgid"] == cell_pid

        os.kill(cell_pid, signal.SIGPROF)
        with pytest.raises(battery._IsolatedCellWorkerFailure) as raised:
            future.result(timeout=45.0)
        assert raised.value.timed_out is True
        worker.close()

        for pid in (event["launcher_pid"], event["binary_pid"]):
            gone_deadline = time.monotonic() + 5.0
            while time.monotonic() < gone_deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.02)
            else:
                pytest.fail(f"MAGEMin descendant {pid} survived cell timeout")
    finally:
        process_groups = {int(cell_pid)} if cell_pid is not None else set()
        for event in read_events():
            process_groups.add(int(event["launcher_pgid"]))
        for process_group in process_groups:
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if future is not None:
            try:
                future.result(timeout=45.0)
            except BaseException:
                pass
        worker.close()
        executor.shutdown(wait=True)
