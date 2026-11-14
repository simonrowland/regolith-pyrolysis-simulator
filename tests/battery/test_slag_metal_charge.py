"""Slag + metal charge stays elemental, and oxide engines refuse it.

Van Ende 2008 run 1-1 and a Kim 2018-shaped start supply the printed
numbers. Expected moles are the printed phase mass, checked against
``oxide_molar_mass``. A metal rewritten as an oxide, or dropped, fails
that check.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from simulator.battery.composition import (
    SLAG_METAL_CHARGE_BASIS,
    slag_metal_charge_from_mapping,
    slag_metal_charge_moles,
)
from simulator.battery.enums import (
    AmountBasis,
    Engine,
    EvidenceClass,
    NoticeKind,
    Phase,
    PhaseRole,
    Quantity,
    RefusalReason,
)
from simulator.battery.identity import Identity, celsius_to_kelvin
from simulator.battery.migrate import oxide_molar_mass, migrate
from simulator.battery.records import Composition, Species, State
from simulator.battery.score import predict_with_engine
from tests.battery import factories as F
from tests.battery.test_migrate import _write_min_tree

# Van Ende 2008 run 1-1. Table 3 slag, Table 2 alloy, procedure masses.
_SLAG_MASS_G = "15"
_METAL_MASS_G = "25"
_SLAG_WT = {
    "MgO": "10",
    "MnO": "10",
    "CrO1.5": "10",
    "CaO": "42.0",
    "SiO2": "28.0",
}
_METAL_WT = {
    "Mn": "11.8",
    "Cr": "17.5",
    "C": "0.15",
    "Ni": "0.5",
    "Si": "0.3",
    "N": "0.26",
    "Al": "0.008",
}
_T_C = 1600


def _printed_component_mass(phase_mass_g: str, weight_percent: str) -> Decimal:
    return Decimal(phase_mass_g) * Decimal(weight_percent) / Decimal(100)


def _van_ende_charge() -> Composition:
    return slag_metal_charge_moles(
        _SLAG_WT, _METAL_WT, _SLAG_MASS_G, _METAL_MASS_G
    )


def _recovered_mass(composition: Composition, key: str) -> Decimal:
    return dict(composition.components)[key] * oxide_molar_mass(key)


def _mass_closes(composition: Composition, key: str, phase_mass_g: str, weight_percent: str) -> bool:
    """n * M returns the printed component mass.

    M comes from ``oxide_molar_mass``. Decimal's 28-digit division does
    not always multiply back to the last digit when M is a binary float
    written as a Decimal (CaO). One part in 1e20 is that ulp, not a
    second formula.
    """

    printed = _printed_component_mass(phase_mass_g, weight_percent)
    recovered = _recovered_mass(composition, key)
    if printed == 0:
        return recovered == 0
    return abs(recovered - printed) / printed <= Decimal("1e-20")


def test_van_ende_charge_keeps_metals_and_closes_printed_mass() -> None:
    composition = _van_ende_charge()
    keys = dict(composition.components)

    assert composition.basis == SLAG_METAL_CHARGE_BASIS
    assert composition.amount_basis is AmountBasis.MOL_INVENTORY
    assert composition.input_provenance == "printed_starting_bulk"
    assert "Mn" in keys and "MnO" in keys
    assert "Cr" in keys and "Cr2O3" in keys and "CrO1.5" not in keys
    assert "Si" in keys and "SiO2" in keys
    assert "Fe" not in keys
    assert "FeO" not in keys

    assert _mass_closes(composition, "SiO2", _SLAG_MASS_G, _SLAG_WT["SiO2"])
    assert _mass_closes(composition, "Mn", _METAL_MASS_G, _METAL_WT["Mn"])
    assert _mass_closes(composition, "Cr", _METAL_MASS_G, _METAL_WT["Cr"])
    assert _mass_closes(composition, "Cr2O3", _SLAG_MASS_G, _SLAG_WT["CrO1.5"])
    mno_mass = _printed_component_mass(_METAL_MASS_G, _METAL_WT["Mn"]) * (
        oxide_molar_mass("Mn") / oxide_molar_mass("MnO")
    )
    assert abs(_recovered_mass(composition, "Mn") - mno_mass) > Decimal("0.1")


def test_kim_shaped_alloy_keeps_elemental_sulfur() -> None:
    composition = slag_metal_charge_moles(
        {"CaO": "40", "Al2O3": "60"},
        {"C": "5.40", "S": "0.0006"},
        "2",
        "6",
    )
    keys = dict(composition.components)

    assert "C" in keys and "S" in keys
    assert "Fe" not in keys
    assert "SO2" not in keys and "SO3" not in keys
    assert _mass_closes(composition, "S", "6", "0.0006")
    assert _mass_closes(composition, "C", "6", "5.40")
    assert _mass_closes(composition, "CaO", "2", "40")
    assert _mass_closes(composition, "Al2O3", "2", "60")


def test_metal_written_as_oxide_is_refused() -> None:
    metal = dict(_METAL_WT)
    metal["MnO"] = metal.pop("Mn")
    with pytest.raises(ValueError, match="slag_metal_charge_metal_not_element:MnO"):
        slag_metal_charge_moles(_SLAG_WT, metal, _SLAG_MASS_G, _METAL_MASS_G)


def test_slag_element_is_not_oxidised() -> None:
    slag = dict(_SLAG_WT)
    slag["Mn"] = slag.pop("MnO")
    with pytest.raises(ValueError, match="slag_metal_charge_slag_not_oxide:Mn"):
        slag_metal_charge_moles(slag, _METAL_WT, _SLAG_MASS_G, _METAL_MASS_G)


def test_repeated_key_is_not_summed() -> None:
    with pytest.raises(ValueError, match="slag_metal_charge_key_collision:S"):
        slag_metal_charge_moles(
            {"CaO": "40", "S": "1"},
            {"C": "5", "S": "0.1"},
            "2",
            "6",
        )


def test_incomplete_charge_is_not_an_oxide_map() -> None:
    with pytest.raises(ValueError, match="slag_metal_charge_incomplete"):
        slag_metal_charge_from_mapping({"slag_oxide_wt_pct": _SLAG_WT})
    assert slag_metal_charge_from_mapping({"components": [["MnO", "10"]]}) is None


def _charge_body() -> dict:
    return {
        "slag_oxide_wt_pct": dict(_SLAG_WT),
        "metal_element_wt_pct": dict(_METAL_WT),
        "slag_mass_g": _SLAG_MASS_G,
        "metal_mass_g": _METAL_MASS_G,
    }


def _extract(charge: dict) -> dict:
    conditions = {
        "atmosphere": {
            "state": {"tag": "value", "value": "Ar"},
            "locator": {"page": 3, "table": "2", "row": "1-1"},
        }
    }
    return {
        "schema_version": "literature_extract.v1",
        "source_id": "fixture-source",
        "source": {"citation": "Fixture, A. (2026), Test Journal 1:1"},
        "experiments": [
            {
                "experiment_id": "run-1",
                "sample": {
                    "initial_composition": {
                        "state": {"tag": "value", "value": charge},
                        "locator": {"page": 3, "table": "2"},
                    }
                },
            }
        ],
        "species": {
            "Mn": {
                "observations": [
                    {
                        "observation_id": "metal-mn",
                        "experiment": "run-1",
                        "locator": {"page": 4, "table": "4", "row": "1-1"},
                        "values": {
                            "quantity": "equilibrium_phase_composition",
                            "phase_label": "metal",
                            "basis": "wt_percent",
                            "T_C": _T_C,
                            "component_amount": "7.8",
                            "method_class": "measured_direct",
                            "admission_status": "admitted",
                            "point_conditions": conditions,
                        },
                    }
                ]
            },
            "S": {
                "observations": [
                    {
                        "observation_id": "l-s",
                        "experiment": "run-1",
                        "locator": {"page": 4, "table": "4", "row": "1-1"},
                        "values": {
                            "quantity": "distribution_ratio",
                            "phase_label": "slag",
                            "counter_phase_label": "metal",
                            "basis": "wt_percent",
                            "T_C": _T_C,
                            "distribution_ratio": "262",
                            "method_class": "measured_direct",
                            "admission_status": "admitted",
                            "point_conditions": conditions,
                        },
                    }
                ]
            },
        },
    }


def test_charge_reaches_every_observation_with_temperature_and_conditions(
    tmp_path,
) -> None:
    result = migrate(_write_min_tree(tmp_path, extract=_extract(_charge_body())), write=False)
    rows = [
        observation
        for observation in result.observations.values()
        if observation.source_id == "fixture-source"
    ]
    assert {observation.observation_id for observation in rows} == {
        "fixture-source::metal-mn",
        "fixture-source::l-s",
    }
    expected = _van_ende_charge()
    expected_t = celsius_to_kelvin(_T_C)
    for observation in rows:
        assert observation.admission.status.value == "admitted"
        assert observation.identity.temperature_K.is_value
        assert observation.identity.temperature_K.value == expected_t
        atmosphere = observation.point_conditions["atmosphere"]
        assert atmosphere.state.is_value
        assert atmosphere.state.value == "Ar"
        assert atmosphere.locator is not None
        assert atmosphere.locator.table == "2"
        composition = observation.identity.composition
        assert composition is not None and composition.is_value
        assert composition.value.components == expected.components
        assert composition.value.basis == SLAG_METAL_CHARGE_BASIS
        assert composition.value.amount_basis is AmountBasis.MOL_INVENTORY
        assert composition.value.input_provenance == "printed_starting_bulk"
        assert "Mn" in dict(composition.value.components)
        assert "MnO" in dict(composition.value.components)


def test_open_system_notice_names_the_distribution_ratio_row(tmp_path) -> None:
    document = _extract(_charge_body())
    document["experiments"][0]["sample"]["container"] = {
        "state": {"tag": "value", "value": "open crucible"},
        "locator": {"page": 2},
    }
    result = migrate(
        _write_min_tree(tmp_path, extract=document), write=False
    )
    rows = {
        observation.observation_id: observation
        for observation in result.observations.values()
        if observation.source_id == "fixture-source"
    }
    expected = {
        "fixture-source::l-s": Quantity.DISTRIBUTION_RATIO,
        "fixture-source::metal-mn": Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
    }
    assert set(rows) == set(expected)
    for observation_id, quantity in expected.items():
        observation = rows[observation_id]
        composition = observation.identity.composition
        assert composition is not None and composition.is_value
        notices = [
            notice
            for notice in observation.notices
            if notice.reason == "open_system_starting_bulk: open crucible"
        ]
        assert len(notices) == 1
        assert notices[0].affected_quantities == (quantity,)
        assert notices[0].kind is NoticeKind.OUT_OF_CERTIFIED_BAND


def test_oxide_metal_does_not_migrate_as_a_charge(tmp_path) -> None:
    charge = _charge_body()
    charge["metal_element_wt_pct"] = {"MnO": "11.8", "Cr": "17.5"}
    result = migrate(_write_min_tree(tmp_path, extract=_extract(charge)), write=False)
    rows = list(result.observations.values())
    assert rows
    for observation in rows:
        composition = observation.identity.composition
        assert composition is None or not composition.is_value
        if composition is not None and composition.is_value:
            assert "MnO" not in dict(composition.value.components)
    experiment = next(iter(result.experiments.values()))
    initial = experiment.sample.initial_composition
    assert initial is not None and initial.state.is_unknown
    assert "slag_metal_charge_metal_not_element:MnO" in (initial.state.reason or "")


def _phase_identity(formula: str, quantity: Quantity, *, role: PhaseRole, counter: PhaseRole | None, composition: Composition) -> Identity:
    kwargs = {
        "quantity": quantity,
        "species": Species(
            formula, Phase.L
        ),
        "temperature_K": State.of(celsius_to_kelvin(_T_C)),
        "phase_role": State.of(role),
        "subtype": State.of("wt_percent"),
        "composition": State.of(composition),
        "fO2_Pa": State.of(Decimal("10")),
        "total_pressure_Pa": State.of(Decimal("100000")),
    }
    if counter is not None:
        kwargs["counter_phase_role"] = State.of(counter)
    return Identity(**kwargs)


def test_oxide_engines_refuse_the_metal_without_calling_the_assemblage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def assemblage(*args, **kwargs):
        raise AssertionError("phase assemblage must not be called")

    def partition(*args, **kwargs):
        raise AssertionError("metal charge must not be partitioned")

    def redox(*args, **kwargs):
        raise AssertionError("metal charge must not be relabelled as oxides")

    monkeypatch.setattr(
        "simulator.battery.score._equilibrate_phase_assemblage", assemblage
    )
    monkeypatch.setattr(
        "simulator.battery.score.partition_engine_components", partition
    )
    monkeypatch.setattr(
        "simulator.battery.score.element_mass_fraction_redox", redox
    )
    composition = _van_ende_charge()
    cases = (
        (
            Engine.MAGEMIN,
            "Mn",
            Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
            PhaseRole.METAL,
            None,
        ),
        (
            Engine.ALPHAMELTS,
            "SiO2",
            Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
            PhaseRole.SLAG,
            None,
        ),
        (
            Engine.THERMOENGINE,
            "S",
            Quantity.DISTRIBUTION_RATIO,
            PhaseRole.SLAG,
            PhaseRole.METAL,
        ),
    )
    for engine, formula, quantity, role, counter in cases:
        identity = _phase_identity(
            formula, quantity, role=role, counter=counter, composition=composition
        )
        observation = F.observation(
            "charge-row",
            "run-1",
            identity,
            Decimal("7.8"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id="fixture-source",
        )
        prediction = predict_with_engine(engine, observation)
        assert prediction.value is None
        assert prediction.refusal_reason is RefusalReason.ENGINE_CANNOT_ACCEPT_METAL
        assert "Mn" in prediction.refusal_detail["metal_components"]
        assert "SiO2" not in prediction.refusal_detail["metal_components"]
        assert "MnO" not in prediction.refusal_detail["metal_components"]
        assert prediction.refusal_detail["engine"] == engine.value

    kim = slag_metal_charge_moles(
        {"CaO": "40", "Al2O3": "60"},
        {"C": "5.40", "S": "0.0006"},
        "2",
        "6",
    )
    identity = _phase_identity(
        "S",
        Quantity.DISTRIBUTION_RATIO,
        role=PhaseRole.SLAG,
        counter=PhaseRole.METAL,
        composition=kim,
    )
    observation = F.observation(
        "l-s",
        "run-1",
        identity,
        Decimal("262"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="fixture-source",
    )
    prediction = predict_with_engine(Engine.MAGEMIN, observation)
    assert prediction.value is None
    assert prediction.refusal_reason is RefusalReason.ENGINE_CANNOT_ACCEPT_METAL
    assert "S" in prediction.refusal_detail["metal_components"]
    assert "C" in prediction.refusal_detail["metal_components"]
    assert "CaO" not in prediction.refusal_detail["metal_components"]


def test_engines_without_a_phase_assemblage_keep_their_typed_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def assemblage(*args, **kwargs):
        raise AssertionError("phase assemblage must not be called")

    monkeypatch.setattr(
        "simulator.battery.score._equilibrate_phase_assemblage", assemblage
    )
    identity = _phase_identity(
        "Mn",
        Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
        role=PhaseRole.METAL,
        counter=None,
        composition=_van_ende_charge(),
    )
    observation = F.observation(
        "charge-row",
        "run-1",
        identity,
        Decimal("7.8"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="fixture-source",
    )
    prediction = predict_with_engine(Engine.VAPOROCK, observation)
    assert prediction.value is None
    assert (
        prediction.refusal_reason
        is RefusalReason.ENGINE_DOES_NOT_REPORT_PHASE_COMPOSITION
    )


def test_oxide_map_that_names_an_element_is_still_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def assemblage(engine, *, composition_kg, **kwargs):
        calls.append(dict(composition_kg))
        from simulator.melt_backend.base import EquilibriumResult

        return EquilibriumResult(
            liquid_fraction=1.0,
            phase_compositions={"liquid": {"SiO2": 100.0}},
        )

    monkeypatch.setattr(
        "simulator.battery.score._equilibrate_phase_assemblage", assemblage
    )
    composition = Composition(
        basis="printed_oxides",
        components=(("SiO2", Decimal("1")), ("Mn", Decimal("1"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    identity = _phase_identity(
        "SiO2",
        Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
        role=PhaseRole.MELT,
        counter=None,
        composition=composition,
    )
    observation = F.observation(
        "oxide-row",
        "run-1",
        identity,
        Decimal("50"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="fixture-source",
    )
    prediction = predict_with_engine(Engine.MAGEMIN, observation)
    assert prediction.refusal_reason is not RefusalReason.ENGINE_CANNOT_ACCEPT_METAL
    assert calls
    assert "Mn" not in calls[0]
    assert "SiO2" in calls[0]
    assert any(notice.kind is NoticeKind.INPUT_OMITTED for notice in prediction.notices)
