"""Only measured rows with a printed gas formula become molar yields."""

from decimal import Decimal

from simulator.battery.enums import Phase, Quantity, ValueKind
from simulator.battery.migrate import REPO_ROOT, Migrator, select_declared_source


def test_murchison_specific_gas_yield_uses_printed_species_and_initial_mass() -> None:
    migrator = Migrator(REPO_ROOT)
    migrator._migrate_extract(
        REPO_ROOT / "data/literature/extracts/murchison-degassing-2023-springer.yaml"
    )

    rows = [
        observation
        for observation_id, observation in migrator.result.observations.items()
        if observation_id.startswith("murchison-degassing-2023-springer::")
        and observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.EVOLVED_GAS_YIELD
    ]
    assert len(rows) == 104
    assert all(row.value.kind is ValueKind.POINT for row in rows)

    point = next(
        observation
        for observation in rows
        if observation.identity.species.formula == "H2"
        and observation.identity.temperature_K is not None
        and observation.identity.temperature_K.value == Decimal("473.15")
    )
    assert point.identity.species.phase.is_value
    assert point.identity.species.phase.value is Phase.G
    assert point.value.kind is ValueKind.POINT
    assert point.value.point == Decimal("0.002470238095238095238095238095")
    assert point.derivation is not None
    assert point.derivation.relation == "specific_gas_yield_H2_ug_g_to_mol_per_initial_kg"
    parameters = dict(point.derivation.parameters)
    assert parameters["original"].state.value == Decimal("4.98")
    assert parameters["molar_mass_g_mol"].state.value == Decimal("2.0160")
    assert parameters["factor"].state.value == Decimal(
        "0.0004960317460317460317460317460"
    )

    quoted = [
        observation
        for observation_id, observation in migrator.result.observations.items()
        if observation_id.startswith("murchison-degassing-2023-springer::")
        and observation.identity.quantity.is_unknown
        and "specific_gas_yield conversion has no verified input basis"
        in (observation.identity.quantity.reason or "")
    ]
    assert len(quoted) == 8


def test_specific_gas_yield_refuses_a_missing_printed_gas_species() -> None:
    selection = select_declared_source(
        Quantity.EVOLVED_GAS_YIELD,
        "ug/g",
        {"yield_ug_g": "4.98"},
    )

    assert selection.value.kind is ValueKind.UNAVAILABLE
    assert selection.reason == "yield_ug_g conversion requires the printed gas_species"
