from __future__ import annotations

import math
from dataclasses import replace
from decimal import Decimal

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
from simulator.battery.records import Composition, Species
from simulator.battery.score import predict_with_engine
from simulator.diagnostic_helpers.binary_pot_battery import (
    PO2_COMMANDED,
    Po2Request,
    _InternalAnalyticalInputRefusal,
    _new_internal_analytical_core,
    _internal_analytical_vapor_pressure_adapter,
)
from tests.battery import factories as F


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
