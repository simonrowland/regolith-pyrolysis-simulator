"""Melt-activity observations are not held to the engine_point contract.

An activity or activity coefficient needs a normalized composition, a
point temperature, and a reference state that matches the engine. Oxygen
is required when the composition or the measured species holds a
multivalent element. Pressure is not an input, and a missing required
input is refused rather than defaulted.
"""

from dataclasses import replace
from decimal import Decimal

import pytest

from simulator.battery.consumer_inputs import REQUIREMENTS, collect_consumer_inputs
from simulator.battery.enums import (
    AmountBasis,
    BenchIdentityBasis,
    Phase,
    Quantity,
    ReferenceStateConvention,
    ValueKind,
)
from simulator.battery.generators import melt_activity_requests
from simulator.battery.identity import Identity
from simulator.battery.records import (
    Bench,
    BenchIdentity,
    Composition,
    Sample,
    Species,
    StandardState,
    State,
    Value,
)
from simulator.battery.waypoints import (
    MELT_ACTIVITY_ENGINES,
    GapReason,
    ReadinessStatus,
    consumer_readiness,
)
from tests.battery import factories as f


def _composition(*pairs: tuple[str, str]) -> Composition:
    return Composition(
        "oxides",
        tuple((name, Decimal(amount)) for name, amount in pairs),
        AmountBasis.MOLE_FRACTION,
    )


def _reference_state(
    formula: str = "Na2O",
    basis: str = "oxide",
    phase: Phase = Phase.L,
    convention: ReferenceStateConvention = ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER,
) -> State:
    return State.of(StandardState(
        convention,
        Species(formula, phase),
        basis,
        Decimal("1"),
    ))


def _activity_identity(
    composition: Composition | None,
    *,
    known_reference: bool = True,
    formula: str = "Na2O",
    basis: str = "oxide",
    species_formula: str | None = None,
    phase: Phase = Phase.L,
    convention: ReferenceStateConvention = ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER,
) -> Identity:
    composition_state = (
        State.unknown("no composition mapped from source")
        if composition is None
        else State.of(composition)
    )
    return Identity(
        quantity=Quantity.ACTIVITY,
        species=Species(species_formula or formula, Phase.L),
        per=State.unknown("test"),
        temperature_K=State.of(Decimal("1473.15")),
        reference_state=(
            _reference_state(formula, basis, phase, convention)
            if known_reference
            else State.unknown("qualifier does not name a reference_state")
        ),
        composition=composition_state,
        fO2_Pa=State.unknown("test"),
        total_pressure_Pa=State.unknown("test"),
        subtype=State.not_applicable("activity does not use subtype"),
    )


def _case(
    *,
    composition: Composition | None,
    identity_composition: Composition | None = None,
    oxygen: Decimal | None = None,
    pressure: Value | None = None,
    temperature: Value | None = None,
    quantity: Quantity = Quantity.ACTIVITY,
    known_reference: bool = True,
    formula: str = "Na2O",
    basis: str = "oxide",
    species_formula: str | None = None,
    phase: Phase = Phase.L,
    convention: ReferenceStateConvention = ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER,
):
    """One activity row. Pressure, when set, is an interval so engine_point cannot use it."""
    sample_composition = composition
    experiment = replace(
        f.kems_experiment(),
        sample=Sample(
            initial_composition=None if sample_composition is None else f.located(sample_composition),
        ),
        thermal_schedule=None,
        pressure_environment=replace(
            f.kems_experiment().pressure_environment,
            total_pressure_Pa=f.located(
                pressure if pressure is not None else Value(
                    ValueKind.INTERVAL, interval_low=Decimal("1"), interval_high=Decimal("10"),
                )
            ),
        ),
    )
    bench = Bench(
        "bench", "work-1", BenchIdentity(BenchIdentityBasis.DESCRIBED_IN_THIS_WORK),
    )
    point = {
        "temperature_K": f.located(
            temperature if temperature is not None else Decimal("1473.15")
        )
    }
    if oxygen is not None:
        point["fO2_log"] = f.located(oxygen)
    identity = _activity_identity(
        identity_composition if identity_composition is not None else composition,
        known_reference=known_reference,
        formula=formula,
        basis=basis,
        species_formula=species_formula,
        phase=phase,
        convention=convention,
    )
    if quantity is not Quantity.ACTIVITY:
        identity = replace(identity, quantity=State.of(quantity))
    observation = replace(
        f.observation("obs", experiment.experiment_id, identity, Decimal("1e-7")),
        point_conditions=point,
        source_id="ts1985",
    )
    return experiment, bench, observation


def _melt(experiment, bench, observation):
    return melt_activity_requests(collect_consumer_inputs(experiment, bench, observation))


def test_requirement_tuple_does_not_include_pressure_or_oxygen():
    assert REQUIREMENTS["melt_activity"] == ("normalized_composition", "temperature_K")
    assert "pressure_boundary" not in REQUIREMENTS["melt_activity"]
    assert "oxygen_condition" not in REQUIREMENTS["melt_activity"]


def test_no_iron_without_oxygen_is_ready_and_engine_point_stays_gap():
    # SiO2 is a pure-liquid-oxide activity for every activity engine. Na2O is not.
    experiment, bench, observation = _case(
        composition=_composition(("Na2O", "0.4"), ("SiO2", "0.6")),
        formula="SiO2",
    )
    results = _melt(experiment, bench, observation)
    assert [item.readiness.engine for item in results] == list(MELT_ACTIVITY_ENGINES)
    assert all(item.readiness.status is ReadinessStatus.READY for item in results)
    assert all(item.readiness.gaps == () for item in results)
    payload = results[0].payload
    assert payload["engine"] == "alphamelts"
    assert payload["temperature_C"] == pytest.approx(1473.15 - 273.15)
    assert payload["composition_mol"]["Na2O"] == pytest.approx(0.4)
    assert payload["composition_mol"]["SiO2"] == pytest.approx(0.6)
    assert "fO2_log" not in payload
    assert "pressure_bar" not in payload
    assert results[0].provenance["output_routes"]["pressure_bar"]["authority"] == "not_an_input"
    engine_point = [
        item for item in consumer_readiness(experiment, bench, observation)
        if item.consumer == "engine_point"
    ]
    assert engine_point
    assert all(item.status is ReadinessStatus.GAP for item in engine_point)
    assert all(
        any(gap.waypoint == "oxygen_condition" for gap in item.gaps)
        for item in engine_point
    )


def test_iron_without_oxygen_is_a_typed_refusal():
    experiment, bench, observation = _case(
        composition=_composition(("FeO", "0.2"), ("SiO2", "0.8")),
        formula="SiO2",
    )
    results = _melt(experiment, bench, observation)
    assert all(item.payload is None for item in results)
    assert all(item.readiness.status is ReadinessStatus.GAP for item in results)
    gaps = results[0].readiness.gaps
    oxygen = [gap for gap in gaps if gap.waypoint == "oxygen_condition"]
    assert len(oxygen) == 1
    assert oxygen[0].reason is GapReason.MISSING_EVIDENCE
    assert "FeO" in oxygen[0].missing
    assert not any(gap.waypoint == "pressure_boundary" for gap in gaps)


def test_iron_with_point_oxygen_is_ready():
    experiment, bench, observation = _case(
        composition=_composition(("FeO", "0.2"), ("SiO2", "0.8")),
        oxygen=Decimal("-9"),
        formula="SiO2",
    )
    results = _melt(experiment, bench, observation)
    assert all(item.readiness.status is ReadinessStatus.READY for item in results)
    assert results[0].payload["fO2_log"] == pytest.approx(-9)
    assert "pressure_bar" not in results[0].payload


def test_zero_iron_does_not_demand_oxygen():
    experiment, bench, observation = _case(
        composition=_composition(("FeO", "0"), ("Na2O", "0.4"), ("SiO2", "0.6")),
        formula="SiO2",
    )
    results = _melt(experiment, bench, observation)
    assert all(item.readiness.status is ReadinessStatus.READY for item in results)
    assert "fO2_log" not in results[0].payload


def test_missing_composition_is_refused_and_not_defaulted():
    experiment, bench, observation = _case(
        composition=None, identity_composition=None, formula="SiO2",
    )
    # _case copies composition into the identity. Pass unknown on both sides.
    observation = replace(
        observation,
        identity=replace(
            observation.identity,
            composition=State.unknown("no composition mapped from source"),
        ),
    )
    results = _melt(experiment, bench, observation)
    assert all(item.payload is None for item in results)
    assert all(item.readiness.status is ReadinessStatus.GAP for item in results)
    assert results[0].readiness.gaps[0].waypoint == "normalized_composition"
    assert results[0].readiness.gaps[0].reason is GapReason.MISSING_EVIDENCE
    assert not any(gap.waypoint == "oxygen_condition" for gap in results[0].readiness.gaps)


def test_identity_composition_outranks_a_different_sample_bulk():
    sample = _composition(("K2O", "0.333"), ("SiO2", "0.667"))
    row = _composition(("K2O", "0.25"), ("SiO2", "0.75"))
    experiment, bench, observation = _case(
        composition=sample, identity_composition=row, formula="SiO2",
    )
    results = _melt(experiment, bench, observation)
    assert results[0].readiness.status is ReadinessStatus.READY
    assert results[0].payload["composition_mol"]["K2O"] == pytest.approx(0.25)
    assert results[0].provenance["output_routes"]["composition_mol"]["route"] == "identity_composition"
    engine_point_inputs = collect_consumer_inputs(experiment, bench, observation)
    sample_route = engine_point_inputs.waypoints["normalized_composition"].selected
    assert sample_route.route == "normalized_initial_composition"
    assert float(sample_route.value["K2O"]) == pytest.approx(0.333)


def test_activity_coefficient_uses_the_same_contract():
    experiment, bench, observation = _case(
        composition=_composition(("Na2O", "0.4"), ("SiO2", "0.6")),
        quantity=Quantity.ACTIVITY_COEFFICIENT,
        formula="SiO2",
    )
    results = _melt(experiment, bench, observation)
    assert all(item.readiness.status is ReadinessStatus.READY for item in results)
    assert results[0].payload["quantity"] == "activity_coefficient"
    assert results[0].payload["reference_state"] == "raoultian_pure_liquid_endmember"
    activity = _melt(*_case(
        composition=_composition(("Na2O", "0.4"), ("SiO2", "0.6")),
        formula="SiO2",
    ))
    assert activity[0].payload["quantity"] == "activity"
    assert activity[0].payload["quantity"] != results[0].payload["quantity"]
    assert activity[0].payload["reference_state"] == results[0].payload["reference_state"]
    imcc = next(item for item in results if item.readiness.engine == "imcc_sf04")
    assert imcc.payload["reference_state"] == "raoultian_pure_liquid_oxide_parent"
    assert imcc.payload["quantity"] == "activity_coefficient"


def test_other_quantities_are_not_applicable_and_do_not_loosen_engine_point():
    experiment, bench, observation = _case(
        composition=_composition(("Na2O", "0.4"), ("SiO2", "0.6")),
    )
    observation = replace(observation, identity=f.o2_identity())
    results = _melt(experiment, bench, observation)
    assert all(item.readiness.status is ReadinessStatus.NOT_APPLICABLE for item in results)
    assert all(item.payload is None for item in results)
    assert all(item.readiness.gaps == () for item in results)
    engine_point = [
        item for item in consumer_readiness(experiment, bench, observation)
        if item.consumer == "engine_point" and item.engine == "imcc_sf04"
    ]
    assert engine_point[0].status is not ReadinessStatus.READY


def test_unknown_reference_state_is_not_consumed():
    experiment, bench, observation = _case(
        composition=_composition(("Na2O", "0.4"), ("SiO2", "0.6")),
        known_reference=False,
    )
    results = _melt(experiment, bench, observation)
    assert all(item.payload is None for item in results)
    assert all(item.readiness.status is ReadinessStatus.NOT_APPLICABLE for item in results)
    assert results[0].readiness.gaps[0].waypoint == "reference_state"
    assert results[0].readiness.gaps[0].reason is GapReason.MISSING_EVIDENCE


@pytest.mark.parametrize("oxide", ["TiO2", "Cr2O3", "MnO", "V2O3", "Eu2O3", "CeO2", "SO3", "Ga2O3"])
def test_multivalent_oxide_without_oxygen_is_a_typed_refusal(oxide):
    experiment, bench, observation = _case(
        composition=_composition((oxide, "0.2"), ("SiO2", "0.8")),
        formula="SiO2",
    )
    results = _melt(experiment, bench, observation)
    assert all(item.payload is None for item in results)
    assert all(item.readiness.status is ReadinessStatus.GAP for item in results)
    oxygen = [gap for gap in results[0].readiness.gaps if gap.waypoint == "oxygen_condition"]
    assert len(oxygen) == 1
    assert oxygen[0].reason is GapReason.MISSING_EVIDENCE
    assert oxide in oxygen[0].missing
    engine_point = [
        item for item in consumer_readiness(experiment, bench, observation)
        if item.consumer == "engine_point"
    ]
    assert engine_point
    assert all(item.status is ReadinessStatus.GAP for item in engine_point)


@pytest.mark.parametrize("alias", ["FeO_tot", "FeO*", "FeOT", "FeOt", "FeO_total", "feo_tot"])
def test_iron_alias_without_oxygen_is_a_typed_refusal(alias):
    experiment, bench, observation = _case(
        composition=_composition((alias, "0.2"), ("SiO2", "0.8")),
        formula="SiO2",
    )
    results = _melt(experiment, bench, observation)
    assert all(item.payload is None for item in results)
    oxygen = [gap for gap in results[0].readiness.gaps if gap.waypoint == "oxygen_condition"]
    assert len(oxygen) == 1
    assert alias in oxygen[0].missing


def test_zero_iron_does_not_hide_another_multivalent_oxide():
    experiment, bench, observation = _case(
        composition=_composition(("FeO", "0"), ("TiO2", "0.2"), ("SiO2", "0.8")),
        formula="SiO2",
    )
    results = _melt(experiment, bench, observation)
    assert all(item.payload is None for item in results)
    oxygen = [gap for gap in results[0].readiness.gaps if gap.waypoint == "oxygen_condition"]
    assert len(oxygen) == 1
    assert "TiO2" in oxygen[0].missing
    assert "FeO" not in oxygen[0].missing


def test_measured_gallium_requires_oxygen_when_the_bulk_is_cmas():
    experiment, bench, observation = _case(
        composition=_composition(
            ("Al2O3", "0.1"), ("CaO", "0.2"), ("MgO", "0.1"), ("SiO2", "0.6"),
        ),
        formula="SiO2",
        species_formula="Ga",
    )
    results = _melt(experiment, bench, observation)
    assert all(item.payload is None for item in results)
    oxygen = [gap for gap in results[0].readiness.gaps if gap.waypoint == "oxygen_condition"]
    assert len(oxygen) == 1
    assert "Ga" in oxygen[0].missing
    assert "fO2_log" not in (results[0].payload or {})


@pytest.mark.parametrize(
    ("convention", "phase", "basis", "formula"),
    [
        (ReferenceStateConvention.HENRIAN_LIQUID, Phase.L, "oxide", "Na2O"),
        (ReferenceStateConvention.HENRIAN_SOLID, Phase.CR, "oxide", "Na2O"),
        (ReferenceStateConvention.HYPOTHETICAL_1WT_PCT, Phase.L, "oxide", "Na2O"),
        (ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER, Phase.CR, "oxide", "Na2O"),
        (ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER, Phase.G, "oxide", "K"),
        (ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER, Phase.L, "single_cation", "NaO0.5"),
    ],
)
def test_mismatched_reference_state_is_refused(convention, phase, basis, formula):
    experiment, bench, observation = _case(
        composition=_composition(("Na2O", "0.4"), ("SiO2", "0.6")),
    )
    state = StandardState(convention, Species(formula, phase), basis, Decimal("1"))
    observation = replace(
        observation,
        identity=replace(observation.identity, reference_state=State.of(state)),
    )
    results = _melt(experiment, bench, observation)
    assert all(item.payload is None for item in results)
    assert all(item.readiness.status is ReadinessStatus.NOT_APPLICABLE for item in results)
    gap = results[0].readiness.gaps[0]
    assert gap.waypoint == "reference_state"
    assert gap.reason is GapReason.REFERENCE_STATE_MISMATCH
    assert gap.missing[0].startswith(convention.value)
    assert gap.missing[1] == "raoultian_pure_liquid_endmember"
    assert gap.missing[0] != gap.missing[1]
    imcc = next(item for item in results if item.readiness.engine == "imcc_sf04")
    assert imcc.payload is None
    assert imcc.readiness.gaps[0].missing[1] == "raoultian_pure_liquid_oxide_parent"


def test_accepted_reference_is_named_on_the_payload():
    experiment, bench, observation = _case(
        composition=_composition(("Na2O", "0.4"), ("SiO2", "0.6")),
        formula="SiO2",
    )
    results = _melt(experiment, bench, observation)
    assert results[0].payload["reference_state"] == "raoultian_pure_liquid_endmember"
    assert results[0].payload["quantity"] == "activity"
    assert "fO2_log" not in results[0].payload


def test_scorer_refuses_a_multivalent_gap_before_the_engine_default(monkeypatch):
    def _opened(*_args, **_kwargs):
        raise AssertionError("activity gap must not open an engine")

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine",
        _opened,
    )
    experiment, _bench, observation = _case(
        composition=_composition(("TiO2", "0.2"), ("SiO2", "0.8")),
        formula="SiO2",
    )
    from simulator.battery.enums import Engine
    from simulator.battery.score import predict_with_engine

    prediction = predict_with_engine(
        Engine.ALPHAMELTS, observation, experiment=experiment, isolated=False,
    )
    assert prediction.value is None
    assert prediction.refusal_detail["reason"] == "missing_evidence"
    assert any("TiO2" in gap["missing"] for gap in prediction.refusal_detail["gaps"])
    assert prediction.refusal_detail["reason"] != "engine_default"


def test_scorer_compares_activity_and_coefficient_to_different_maps(monkeypatch):
    seen = {}

    def _open(name):
        return type("Handle", (), {
            "name": name,
            "available": True,
            "unavailable_reason": None,
            "supports_intrinsic_fO2": False,
        })()

    def _cell(_handle, _pot, *, po2, **_kwargs):
        seen["mode"] = po2.mode
        seen["po2_bar"] = po2.po2_bar
        seen["pressure_bar"] = _kwargs.get("physical_pressure_bar")
        return type("Cell", (), {
            "status": "ok",
            "refusal_reason": None,
            "melt_activities": {"Na2O": 0.2},
            "melt_activity_coefficients": {"Na2O": 0.5},
            "gas_partial_pressures_Pa": {},
            "hostname": "test",
            "exit_code": 0,
            "exit_signal": None,
            "notices": [],
            "authority": None,
            "certified_band": None,
        })()

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine",
        _open,
    )
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.equilibrate_cell",
        _cell,
    )
    from simulator.battery.enums import Engine
    from simulator.battery.score import melt_quantity_report, predict_with_engine

    # a = gamma * x on the Na2O 0.4 row: 0.5 * 0.4 = 0.2.
    assert melt_quantity_report(
        Quantity.ACTIVITY, {"Na2O": 0.2}, {"Na2O": 0.5},
    )["Na2O"] == pytest.approx(0.2)
    assert melt_quantity_report(
        Quantity.ACTIVITY_COEFFICIENT, {"Na2O": 0.2}, {"Na2O": 0.5},
    )["Na2O"] == pytest.approx(0.5)
    assert melt_quantity_report(Quantity.ACTIVITY_COEFFICIENT, {"Na2O": 0.2}, {}) is None

    experiment, _bench, observation = _case(
        composition=_composition(("Na2O", "0.4"), ("SiO2", "0.6")),
    )
    activity = predict_with_engine(
        Engine.IMCC_SF04, observation, experiment=experiment, isolated=False,
    )
    assert seen["mode"] == "not_an_input"
    assert seen["po2_bar"] is None
    assert seen["pressure_bar"] is None
    assert activity.value == Decimal("0.2")

    coefficient_observation = replace(
        observation,
        identity=replace(observation.identity, quantity=State.of(Quantity.ACTIVITY_COEFFICIENT)),
    )
    coefficient = predict_with_engine(
        Engine.IMCC_SF04, coefficient_observation, experiment=experiment, isolated=False,
    )
    assert coefficient.value == Decimal("0.5")
    assert coefficient.value != activity.value


def test_printed_oxygen_is_commanded_and_not_the_engine_default(monkeypatch):
    seen = {}

    def _open(name):
        return type("Handle", (), {
            "name": name,
            "available": True,
            "unavailable_reason": None,
            "supports_intrinsic_fO2": False,
        })()

    def _cell(_handle, _pot, *, po2, **_kwargs):
        seen["mode"] = po2.mode
        seen["po2_bar"] = po2.po2_bar
        return type("Cell", (), {
            "status": "ok",
            "refusal_reason": None,
            "melt_activities": {"Na2O": 0.2},
            "melt_activity_coefficients": {"Na2O": 0.5},
            "gas_partial_pressures_Pa": {},
            "hostname": "test",
            "exit_code": 0,
            "exit_signal": None,
            "notices": [],
            "authority": None,
            "certified_band": None,
        })()

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine",
        _open,
    )
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.equilibrate_cell",
        _cell,
    )
    from simulator.battery.enums import Engine
    from simulator.battery.score import predict_with_engine

    experiment, _bench, observation = _case(
        composition=_composition(("FeO", "0.2"), ("SiO2", "0.8")),
        oxygen=Decimal("-7"),
    )
    prediction = predict_with_engine(
        Engine.IMCC_SF04, observation, experiment=experiment, isolated=False,
    )
    assert seen["mode"] == "commanded"
    assert seen["po2_bar"] == pytest.approx(1e-7)
    assert seen["mode"] != "engine_default"
    assert prediction.value == Decimal("0.2")


def test_coefficient_without_a_gamma_map_is_refused(monkeypatch):
    def _open(name):
        return type("Handle", (), {
            "name": name,
            "available": True,
            "unavailable_reason": None,
            "supports_intrinsic_fO2": False,
        })()

    def _cell(_handle, _pot, *, po2, **_kwargs):
        return type("Cell", (), {
            "status": "ok",
            "refusal_reason": None,
            "melt_activities": {"Na2O": 0.2},
            "melt_activity_coefficients": {},
            "gas_partial_pressures_Pa": {},
            "hostname": "test",
            "exit_code": 0,
            "exit_signal": None,
            "notices": [],
            "authority": None,
            "certified_band": None,
        })()

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine",
        _open,
    )
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.equilibrate_cell",
        _cell,
    )
    from simulator.battery.enums import Engine
    from simulator.battery.score import predict_with_engine

    experiment, _bench, observation = _case(
        composition=_composition(("Na2O", "0.4"), ("SiO2", "0.6")),
        quantity=Quantity.ACTIVITY_COEFFICIENT,
    )
    prediction = predict_with_engine(
        Engine.IMCC_SF04, observation, experiment=experiment, isolated=False,
    )
    assert prediction.value is None
    assert prediction.refusal_detail["reason"] == "engine_reported_activity_not_coefficient"


def test_temperature_interval_is_refused():
    experiment, bench, observation = _case(
        composition=_composition(("Na2O", "0.4"), ("SiO2", "0.6")),
        temperature=Value(ValueKind.INTERVAL, interval_low=Decimal("1400"), interval_high=Decimal("1500")),
        formula="SiO2",
    )
    results = _melt(experiment, bench, observation)
    assert results[0].readiness.status is ReadinessStatus.GAP
    assert any(
        gap.waypoint == "temperature_K" and gap.reason is GapReason.INTERVAL_NEEDS_POINT
        for gap in results[0].readiness.gaps
    )
    assert results[0].payload is None


def _by_engine(results):
    return {item.readiness.engine: item for item in results}


_MELTS_ACTIVITY = ("alphamelts", "thermoengine")
_IMCC_ACTIVITY = ("imcc_sf04", "imcc_sf04_ext")


def _assert_melts_parent_oxide_refused(item, formula: str) -> None:
    assert item.payload is None
    assert item.readiness.status is ReadinessStatus.NOT_APPLICABLE
    gap = item.readiness.gaps[0]
    assert gap.waypoint == "reference_state"
    assert gap.reason is GapReason.REFERENCE_STATE_MISMATCH
    assert formula in gap.missing[0]
    assert "typed-refusal:melts_endmember_not_parent_oxide" in gap.missing[1]
    assert formula in gap.missing[1]


@pytest.mark.parametrize("formula", ["Na2O", "CaO", "MgO", "K2O"])
@pytest.mark.parametrize("basis", ["oxide", "parent", "parent_oxide"])
def test_parent_oxide_row_is_ready_only_for_imcc(formula, basis):
    """Na2O, CaO, MgO, and K2O are IMCC parent activities, not MELTS oxide endmembers."""
    experiment, bench, observation = _case(
        composition=_composition((formula, "0.4"), ("SiO2", "0.6")),
        formula=formula,
        basis=basis,
    )
    results = _by_engine(_melt(experiment, bench, observation))
    for engine in _IMCC_ACTIVITY:
        item = results[engine]
        assert item.readiness.status is ReadinessStatus.READY
        assert item.payload is not None
        assert item.payload["reference_state"] == "raoultian_pure_liquid_oxide_parent"
        assert "fO2_log" not in item.payload
    for engine in _MELTS_ACTIVITY:
        _assert_melts_parent_oxide_refused(results[engine], formula)


@pytest.mark.parametrize("basis", ["oxide", "parent", "parent_oxide"])
def test_feo_parent_row_is_ready_only_for_imcc_when_oxygen_is_printed(basis):
    experiment, bench, observation = _case(
        composition=_composition(("FeO", "0.2"), ("SiO2", "0.8")),
        oxygen=Decimal("-7"),
        formula="FeO",
        basis=basis,
    )
    results = _by_engine(_melt(experiment, bench, observation))
    for engine in _IMCC_ACTIVITY:
        item = results[engine]
        assert item.readiness.status is ReadinessStatus.READY
        assert item.payload["fO2_log"] == pytest.approx(-7)
        assert item.payload["reference_state"] == "raoultian_pure_liquid_oxide_parent"
    for engine in _MELTS_ACTIVITY:
        _assert_melts_parent_oxide_refused(results[engine], "FeO")


def test_single_cation_formula_with_oxide_token_is_not_scored_as_parent_oxide(monkeypatch):
    """NaO0.5 is single-cation even when the basis token says oxide. It is not a(Na2O)."""
    opened: list[str] = []

    def _open(name):
        opened.append(name)
        return type("Handle", (), {
            "name": name,
            "available": True,
            "unavailable_reason": None,
            "supports_intrinsic_fO2": False,
        })()

    def _cell(_handle, _pot, *, po2, **_kwargs):
        return type("Cell", (), {
            "status": "ok",
            "refusal_reason": None,
            "melt_activities": {"Na2O": 0.2},
            "melt_activity_coefficients": {},
            "gas_partial_pressures_Pa": {},
            "hostname": "test",
            "exit_code": 0,
            "exit_signal": None,
            "notices": [],
            "authority": None,
            "certified_band": None,
        })()

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine",
        _open,
    )
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.equilibrate_cell",
        _cell,
    )
    from simulator.battery.enums import Engine
    from simulator.battery.score import predict_with_engine

    experiment, bench, observation = _case(
        composition=_composition(("Na2O", "0.4"), ("SiO2", "0.6")),
        formula="NaO0.5",
        basis="oxide",
        species_formula="Na2O",
    )
    results = _by_engine(_melt(experiment, bench, observation))
    assert set(results) == set(MELT_ACTIVITY_ENGINES)
    for item in results.values():
        assert item.payload is None
        assert item.readiness.status is ReadinessStatus.NOT_APPLICABLE
        gap = item.readiness.gaps[0]
        assert gap.reason is GapReason.REFERENCE_STATE_MISMATCH
        assert "NaO0.5" in gap.missing[0]
    for engine in (Engine.ALPHAMELTS, Engine.THERMOENGINE, Engine.IMCC_SF04, Engine.IMCC_SF04_EXT):
        prediction = predict_with_engine(
            engine, observation, experiment=experiment, isolated=False,
        )
        assert prediction.value is None
        assert prediction.value != Decimal("0.2")
    assert opened == []


def test_na2sio3_endmember_is_not_ready_for_imcc(monkeypatch):
    opened: list[str] = []

    def _open(name):
        opened.append(name)
        return type("Handle", (), {
            "name": name,
            "available": True,
            "unavailable_reason": None,
            "supports_intrinsic_fO2": False,
        })()

    def _cell(_handle, _pot, *, po2, **_kwargs):
        return type("Cell", (), {
            "status": "ok",
            "refusal_reason": None,
            "melt_activities": {"Na2SiO3": 0.2, "SiO2": 0.55},
            "melt_activity_coefficients": {},
            "gas_partial_pressures_Pa": {},
            "hostname": "test",
            "exit_code": 0,
            "exit_signal": None,
            "notices": [],
            "authority": None,
            "certified_band": None,
        })()

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine",
        _open,
    )
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.equilibrate_cell",
        _cell,
    )
    from simulator.battery.enums import Engine
    from simulator.battery.score import predict_with_engine

    experiment, bench, observation = _case(
        composition=_composition(("Na2O", "0.4"), ("SiO2", "0.6")),
        formula="Na2SiO3",
        basis="oxide",
    )
    results = _by_engine(_melt(experiment, bench, observation))
    for engine in _IMCC_ACTIVITY:
        item = results[engine]
        assert item.payload is None
        assert item.readiness.status is ReadinessStatus.NOT_APPLICABLE
        gap = item.readiness.gaps[0]
        assert gap.reason is GapReason.REFERENCE_STATE_MISMATCH
        assert "Na2SiO3" in gap.missing[0]
        assert "Na2SiO3" in gap.missing[1]
    for engine in _MELTS_ACTIVITY:
        _assert_melts_parent_oxide_refused(results[engine], "Na2SiO3")
    for engine in (Engine.IMCC_SF04, Engine.IMCC_SF04_EXT, Engine.ALPHAMELTS):
        prediction = predict_with_engine(
            engine, observation, experiment=experiment, isolated=False,
        )
        assert prediction.value is None
        assert prediction.value != Decimal("0.2")
    assert opened == []


def test_scorer_compares_canonical_oxide_activity_not_raw_labels(monkeypatch):
    """SiO2_Liq is the raw label. The compared number is a(SiO2). Na, K, and Fe are not."""

    def _open(name):
        return type("Handle", (), {
            "name": name,
            "available": True,
            "unavailable_reason": None,
            "supports_intrinsic_fO2": False,
        })()

    def _cell(_handle, _pot, *, po2, **_kwargs):
        return type("Cell", (), {
            "status": "ok",
            "refusal_reason": None,
            "melt_activities": {
                "SiO2_Liq": 0.42,
                "Na": 0.08,
                "K": 0.03,
                "Fe": 0.25,
            },
            "melt_activity_coefficients": {},
            "gas_partial_pressures_Pa": {},
            "hostname": "test",
            "exit_code": 0,
            "exit_signal": None,
            "notices": [],
            "authority": None,
            "certified_band": None,
        })()

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine",
        _open,
    )
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.equilibrate_cell",
        _cell,
    )
    from simulator.battery.enums import Engine
    from simulator.battery.score import match_reported_species, predict_with_engine

    raw = {"SiO2_Liq": 0.42, "Na": 0.08, "K": 0.03, "Fe": 0.25}
    experiment, _bench, observation = _case(
        composition=_composition(("SiO2", "0.6"), ("Al2O3", "0.4")),
        formula="SiO2",
    )
    for engine in (Engine.ALPHAMELTS, Engine.THERMOENGINE, Engine.IMCC_SF04):
        prediction = predict_with_engine(
            engine, observation, experiment=experiment, isolated=False,
        )
        assert prediction.value == Decimal("0.42")
        assert prediction.value != Decimal("0.08")
        assert prediction.value != Decimal("0.03")
        assert prediction.value != Decimal("0.25")

    element_experiment, _bench, element_observation = _case(
        composition=_composition(("SiO2", "0.6"), ("Al2O3", "0.4")),
        formula="SiO2",
        species_formula="Na",
    )
    for engine in (Engine.ALPHAMELTS, Engine.IMCC_SF04):
        element = predict_with_engine(
            engine, element_observation, experiment=element_experiment, isolated=False,
        )
        assert element.value is None
        assert element.value != Decimal("0.08")

    assert match_reported_species("SiO2", raw, oxide_activity=True) == ("SiO2", 0.42)
    assert match_reported_species("Na2O", {"Na": 0.2, "K": 0.03, "Fe": 0.25}, oxide_activity=True) is None
    assert match_reported_species("Na", {"Na": 0.08}, oxide_activity=True) is None
    assert match_reported_species("Na", {"Na": 0.08}) == ("Na", 0.08)


def _fake_engine(monkeypatch, activities, gammas=None):
    opened: list[str] = []

    def _open(name):
        opened.append(name)
        return type("Handle", (), {
            "name": name,
            "available": True,
            "unavailable_reason": None,
            "supports_intrinsic_fO2": False,
        })()

    def _cell(_handle, _pot, *, po2, **_kwargs):
        return type("Cell", (), {
            "status": "ok",
            "refusal_reason": None,
            "melt_activities": dict(activities),
            "melt_activity_coefficients": dict(gammas or {}),
            "gas_partial_pressures_Pa": {},
            "hostname": "test",
            "exit_code": 0,
            "exit_signal": None,
            "notices": [],
            "authority": None,
            "certified_band": None,
        })()

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine",
        _open,
    )
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.equilibrate_cell",
        _cell,
    )
    return opened


def test_h2o_endmember_is_refused_when_melts_adapter_rejects_composition(monkeypatch):
    """H2O's label is understood, but the MELTS composition adapter rejects it."""

    from engines.alphamelts.domain import (
        MELTS_PARENT_OXIDE_NOT_ENDMEMBER,
        canonical_oxide_activity_map,
        melts_endmember_to_parent_oxide_activity,
    )
    from simulator.battery.enums import Engine
    from simulator.battery.score import predict_with_engine

    present, present_reason = melts_endmember_to_parent_oxide_activity({"H2O": 0.3}, "H2O")
    liquid, _liquid_reason = melts_endmember_to_parent_oxide_activity({"H2O_Liq": 0.31}, "H2O")
    assert present == pytest.approx(0.3)
    assert present_reason == ""
    assert liquid == pytest.approx(0.31)
    assert "H2O" not in canonical_oxide_activity_map({"H2O": 0.3, "SiO2_Liq": 0.42})

    experiment, bench, observation = _case(
        composition=_composition(("H2O", "0.2"), ("SiO2", "0.8")),
        formula="H2O",
    )
    results = _by_engine(_melt(experiment, bench, observation))
    for engine in _MELTS_ACTIVITY:
        item = results[engine]
        assert item.readiness.status is ReadinessStatus.NOT_APPLICABLE
        assert item.payload is None
        gap = item.readiness.gaps[0]
        assert gap.reason is GapReason.REFERENCE_STATE_MISMATCH
        assert "H2O" in gap.missing[1]
    for engine in _IMCC_ACTIVITY:
        item = results[engine]
        assert item.payload is None
        assert item.readiness.status is ReadinessStatus.NOT_APPLICABLE
        gap = item.readiness.gaps[0]
        assert "H2O" in gap.missing[0]
        assert "not_imcc_parent_oxide" in gap.missing[1]
        assert MELTS_PARENT_OXIDE_NOT_ENDMEMBER not in gap.missing[1]

    opened = _fake_engine(
        monkeypatch,
        {"H2O": 0.3, "SiO2_Liq": 0.42, "Na": 0.08},
        {"H2O": 1.7},
    )
    for engine in (Engine.ALPHAMELTS, Engine.THERMOENGINE):
        prediction = predict_with_engine(
            engine, observation, experiment=experiment, isolated=False,
        )
        assert prediction.value is None
        assert prediction.refusal_detail["reason"] == "reference_state_mismatch"
    imcc_before = len(opened)
    imcc = predict_with_engine(
        Engine.IMCC_SF04, observation, experiment=experiment, isolated=False,
    )
    assert imcc.value is None
    assert imcc.value != Decimal("0.3")
    assert len(opened) == imcc_before

    coefficient_observation = replace(
        observation,
        identity=replace(observation.identity, quantity=State.of(Quantity.ACTIVITY_COEFFICIENT)),
    )
    coefficient = predict_with_engine(
        Engine.ALPHAMELTS, coefficient_observation, experiment=experiment, isolated=False,
    )
    assert coefficient.value is None
    assert coefficient.refusal_detail["reason"] == "reference_state_mismatch"
    assert opened == []


def test_scorer_compares_the_admitted_endmember_not_a_different_species(monkeypatch):
    """Only the measured species named by the admitted endmember is scoreable."""

    from simulator.battery.enums import Engine
    from simulator.battery.score import predict_with_engine

    composition = _composition(("Na2O", "0.4"), ("SiO2", "0.6"))

    def _score(engine, species, activities, gammas=None, quantity=Quantity.ACTIVITY):
        opened = _fake_engine(monkeypatch, activities, gammas)
        experiment, _bench, observation = _case(
            composition=composition,
            formula="SiO2",
            species_formula=species,
            quantity=quantity,
        )
        prediction = predict_with_engine(
            engine, observation, experiment=experiment, isolated=False,
        )
        return prediction, opened

    imcc_compound_only, opened = _score(Engine.IMCC_SF04, "Na2SiO3", {"Na2SiO3": 0.2})
    assert imcc_compound_only.value is None
    assert imcc_compound_only.value != Decimal("0.2")
    assert imcc_compound_only.refusal_detail["reason"] == "reference_state_mismatch"
    assert opened == []

    melts_both, opened = _score(
        Engine.ALPHAMELTS,
        "Na2O",
        {"Na2O": 0.2, "SiO2_Liq": 0.42, "Na": 0.08},
    )
    assert melts_both.value is None
    assert melts_both.refusal_detail["reason"] == "reference_state_mismatch"
    assert opened == []

    melts_only_parent, opened = _score(Engine.ALPHAMELTS, "Na2O", {"Na2O": 0.2})
    assert melts_only_parent.value is None
    assert melts_only_parent.value != Decimal("0.2")
    assert melts_only_parent.refusal_detail["reason"] == "reference_state_mismatch"
    assert opened == []

    imcc_both, opened = _score(
        Engine.IMCC_SF04,
        "Na2O",
        {"Na2O": 0.2, "SiO2": 0.55},
    )
    assert imcc_both.value is None
    assert imcc_both.refusal_detail["reason"] == "reference_state_mismatch"
    assert opened == []

    imcc_compound, opened = _score(
        Engine.IMCC_SF04,
        "Na2SiO3",
        {"Na2SiO3": 0.2, "SiO2": 0.55},
    )
    assert imcc_compound.value is None
    assert imcc_compound.refusal_detail["reason"] == "reference_state_mismatch"
    assert opened == []

    melts_compound, opened = _score(
        Engine.ALPHAMELTS,
        "Na2SiO3",
        {"Na2SiO3": 0.2, "SiO2_Liq": 0.42},
    )
    assert melts_compound.value is None
    assert melts_compound.refusal_detail["reason"] == "reference_state_mismatch"
    assert opened == []

    coefficient, opened = _score(
        Engine.IMCC_SF04,
        "Na2SiO3",
        {"Na2SiO3": 0.2, "SiO2": 0.55},
        {"Na2SiO3": 9.9, "SiO2": 1.5},
        Quantity.ACTIVITY_COEFFICIENT,
    )
    assert coefficient.value is None
    assert coefficient.value != Decimal("0.2")
    assert coefficient.refusal_detail["reason"] == "reference_state_mismatch"
    assert opened == []
