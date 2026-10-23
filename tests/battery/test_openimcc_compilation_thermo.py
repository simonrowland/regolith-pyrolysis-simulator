"""OpenIMCC compilation thermo stays on its card phase and standard basis."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import openimcc.gas
import pytest
from openimcc.gas import species_thermo

from simulator.battery.compilation_tier import predict_thermo_attempt
from simulator.battery.enums import (
    Engine,
    PerBasis,
    Phase,
    Polymorph,
    Quantity,
    RefusalReason,
    QUANTITY_UNITS,
)
from simulator.battery.identity import ideal_gas_entropy_pressure_shift_J_per_mol_K
from simulator.battery.records import Species, State
from tests.battery import factories as F


def _observation(
    formula: str,
    phase: Phase,
    quantity: Quantity,
    *,
    temperature_K: str = "1600",
    polymorph: Polymorph | None = None,
    fusion: bool = False,
    pressure_Pa: str | None = "100000",
    charge: State[int] | int | None = None,
):
    identity = F.o2_identity(Decimal(temperature_K))
    identity = replace(
        identity,
        quantity=quantity,
        per=(
            State.not_applicable("fusion endmember difference")
            if fusion
            else State.of(PerBasis.MOL_SPECIES)
        ),
        standard_pressure_Pa=(
            State.not_applicable("standard fusion reference")
            if pressure_Pa is None
            else State.of(Decimal(pressure_Pa))
        ),
        species=replace(
            Species(formula, phase, charge=charge),
            polymorph=None if polymorph is None else State.of(polymorph),
        ),
        reaction=State.not_applicable("pure standard-state thermo"),
        formation_elements=State.not_applicable("not a formation quantity"),
        subtype=(
            State.of("solid_to_liquid")
            if fusion
            else State.of("H(T)-H(298.15 K)")
            if quantity is Quantity.H_MINUS_H298
            else State.not_applicable("not an enthalpy increment")
        ),
    )
    experiment = F.tabulation_experiment()
    return F.observation(
        f"openimcc-{formula}-{phase.value}-{quantity.value}",
        experiment.experiment_id,
        identity,
        Decimal("0"),
    )


def test_openimcc_gas_entropy_shifts_from_one_bar_to_observation_pressure() -> None:
    observation = _observation(
        "K", Phase.G, Quantity.S, temperature_K="1600", pressure_Pa="101325"
    )

    attempt = predict_thermo_attempt(Engine.OPENIMCC, observation)
    card = species_thermo("K", "g", 1600.0)
    shift = ideal_gas_entropy_pressure_shift_J_per_mol_K(100000, 101325)

    assert attempt.value is not None
    assert float(attempt.value) == pytest.approx(card.S_J_molK + float(shift))
    assert attempt.unit == QUANTITY_UNITS[Quantity.S]


def test_openimcc_condensed_heat_capacity_uses_liquid_card() -> None:
    observation = _observation("FeO", Phase.L, Quantity.CP, temperature_K="1800")

    attempt = predict_thermo_attempt(Engine.OPENIMCC, observation)
    card = species_thermo("FeO", "l", 1800.0)

    assert attempt.value is not None
    assert float(attempt.value) == pytest.approx(card.Cp_J_molK)
    assert attempt.unit == QUANTITY_UNITS[Quantity.CP]


def test_openimcc_condensed_card_refuses_a_different_standard_pressure() -> None:
    observation = _observation(
        "FeO", Phase.L, Quantity.CP, temperature_K="1800", pressure_Pa="101325"
    )

    attempt = predict_thermo_attempt(Engine.OPENIMCC, observation)

    assert attempt.value is None
    assert attempt.refusal_reason is RefusalReason.OUTSIDE_SUPPORTED_SPECIES


def test_openimcc_h_minus_h298_removes_the_source_formation_anchor() -> None:
    observation = _observation("K", Phase.G, Quantity.H_MINUS_H298)

    attempt = predict_thermo_attempt(Engine.OPENIMCC, observation)
    card = species_thermo("K", "g", 1600.0)

    # NIST-JANAF K-005 gives ΔfH°(298.15 K) = 89.000 kJ/mol.
    assert attempt.value is not None
    assert float(attempt.value) == pytest.approx(card.H_app_kJ_mol - 89.0)
    assert attempt.unit == QUANTITY_UNITS[Quantity.H_MINUS_H298]


def test_openimcc_refuses_an_unsupported_species_without_zero() -> None:
    observation = _observation("Unobtanium", Phase.G, Quantity.CP)

    attempt = predict_thermo_attempt(Engine.OPENIMCC, observation)

    assert attempt.value is None
    assert attempt.refusal_reason is RefusalReason.OUTSIDE_SUPPORTED_SPECIES


def test_openimcc_refuses_charged_al_before_card_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_call(*_args, **_kwargs):
        pytest.fail("charged species must be refused before OpenIMCC card lookup")

    monkeypatch.setattr(openimcc.gas, "species_thermo", unexpected_call)
    observation = _observation("Al", Phase.G, Quantity.S, charge=1)

    attempt = predict_thermo_attempt(Engine.OPENIMCC, observation)

    assert attempt.value is None
    assert attempt.refusal_reason is RefusalReason.OUTSIDE_SUPPORTED_SPECIES
    assert attempt.refusal_detail["reason"] == "vaporock-charged-species-not-in-janaf-table"


def test_openimcc_refuses_unknown_charge_before_card_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_call(*_args, **_kwargs):
        pytest.fail("unknown charge must be refused before OpenIMCC card lookup")

    monkeypatch.setattr(openimcc.gas, "species_thermo", unexpected_call)
    observation = _observation(
        "K", Phase.G, Quantity.CP, charge=State.unknown("charge not reported")
    )

    attempt = predict_thermo_attempt(Engine.OPENIMCC, observation)

    assert attempt.value is None
    assert attempt.refusal_reason is RefusalReason.IDENTITY_UNKNOWN
    assert attempt.refusal_detail["reason"] == "vaporock-gas-charge-unknown"


def test_openimcc_neutral_al_still_uses_gas_card() -> None:
    observation = _observation("Al", Phase.G, Quantity.S)

    attempt = predict_thermo_attempt(Engine.OPENIMCC, observation)

    assert attempt.value is not None
    assert "Al(g)" in attempt.call_evidence


def test_openimcc_fusion_refuses_charged_species_before_card_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_call(*_args, **_kwargs):
        pytest.fail("charged fusion species must be refused before card lookup")

    monkeypatch.setattr(openimcc.gas, "species_thermo", unexpected_call)
    observation = _observation(
        "Al", Phase.CR, Quantity.ENTHALPY_OF_FUSION, fusion=True, charge=1
    )

    attempt = predict_thermo_attempt(Engine.OPENIMCC, observation)

    assert attempt.value is None
    assert attempt.refusal_reason is RefusalReason.OUTSIDE_SUPPORTED_SPECIES
    assert attempt.refusal_detail["reason"] == "vaporock-charged-species-not-in-janaf-table"


@pytest.mark.parametrize("formula", ["K", "Na", "Mg"])
@pytest.mark.parametrize("phase", [Phase.L, Phase.CR])
def test_openimcc_does_not_relabel_condensed_alkali_or_magnesium_as_gas(
    formula: str, phase: Phase
) -> None:
    observation = _observation(formula, phase, Quantity.CP)

    attempt = predict_thermo_attempt(Engine.OPENIMCC, observation)

    assert attempt.value is None
    assert attempt.refusal_reason is RefusalReason.OUTSIDE_SUPPORTED_SPECIES
    assert attempt.refusal_detail["phase"] == phase.value


def test_openimcc_gas_cards_exist_for_the_phase_pilot_species() -> None:
    for formula in ("K", "Na", "Mg"):
        observation = _observation(formula, Phase.G, Quantity.CP)

        attempt = predict_thermo_attempt(Engine.OPENIMCC, observation)

        assert attempt.value is not None
        assert f"{formula}(g)" in attempt.call_evidence


@pytest.mark.parametrize(
    "quantity",
    [Quantity.DELTA_FG, Quantity.DELTA_FH, Quantity.LOG10_KF],
)
def test_openimcc_apparent_formation_properties_are_never_routed(
    monkeypatch: pytest.MonkeyPatch, quantity: Quantity
) -> None:
    def unexpected_call(*_args, **_kwargs):
        pytest.fail("apparent OpenIMCC G/H must not route as formation data")

    monkeypatch.setattr(openimcc.gas, "species_thermo", unexpected_call)
    observation = _observation("K", Phase.G, quantity)

    attempt = predict_thermo_attempt(Engine.OPENIMCC, observation)

    assert attempt.value is None
    assert attempt.refusal_reason is RefusalReason.UNSUPPORTED


@pytest.mark.parametrize(
    ("quantity", "expected"),
    [
        (Quantity.ENTHALPY_OF_FUSION, "H"),
        (Quantity.ENTROPY_OF_FUSION, "S"),
    ],
)
def test_openimcc_fusion_uses_both_cards_at_the_transition_temperature(
    quantity: Quantity, expected: str
) -> None:
    temperature = 1986.0
    observation = _observation(
        "SiO2",
        Phase.CR,
        quantity,
        temperature_K=str(temperature),
        fusion=True,
        pressure_Pa=None,
    )
    solid = species_thermo("SiO2", "cr", temperature)
    liquid = species_thermo("SiO2", "l", temperature)
    if expected == "H":
        reference = liquid.H_app_kJ_mol - solid.H_app_kJ_mol
    else:
        reference = liquid.S_J_molK - solid.S_J_molK

    attempt = predict_thermo_attempt(Engine.OPENIMCC, observation)

    assert attempt.value is not None
    assert float(attempt.value) == pytest.approx(reference)
    assert len(attempt.coefficient_sources) == 2


def test_openimcc_fusion_refuses_when_a_phase_card_is_missing() -> None:
    observation = _observation(
        "MgO",
        Phase.CR,
        Quantity.ENTHALPY_OF_FUSION,
        temperature_K="1986",
        polymorph=Polymorph.PERICLASE,
        fusion=True,
        pressure_Pa=None,
    )

    attempt = predict_thermo_attempt(Engine.OPENIMCC, observation)

    assert attempt.value is None
    assert attempt.refusal_reason is RefusalReason.ENGINE_DOES_NOT_REPORT_FUSION
