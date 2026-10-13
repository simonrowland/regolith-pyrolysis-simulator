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
    Engine,
    EvidenceClass,
    Phase,
    Quantity,
    AdmissionStatus,
)
from simulator.battery.identity import Identity
from simulator.battery.records import Composition, Species, State
from simulator.battery.score import predict_with_engine
from simulator.diagnostic_helpers.binary_pot_battery import (
    BinaryPot,
    PO2_COMMANDED,
    Po2Request,
    _InternalAnalyticalInputRefusal,
    _new_internal_analytical_core,
    _internal_analytical_vapor_pressure_adapter,
    composition_kg_and_mol,
    equilibrate_cell,
    open_battery_engine,
)
from simulator.chemistry.melt_activity import single_cation_mole_fractions
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
        if record.get("melt_oxide_parent_formula") == "Na2O"
    )
    assert float(prediction.value) == pytest.approx(
        owner_record["melt_parent_oxide_activity"], rel=1e-12,
    )
    assert owner_record["melt_parent_oxide_activity"] != owner_record[
        "melt_oxide_activity"
    ]


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


def test_internal_analytical_without_oxygen_is_typed_refusal() -> None:
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

    assert prediction.value is None
    assert prediction.refusal_reason is not None
    assert prediction.refusal_detail["engine_reason"] == (
        "internal_analytical_missing_oxygen_condition"
    )


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
