"""Only measured rows with a printed gas formula become molar yields."""

from decimal import Decimal

from simulator.battery.enums import Phase, Quantity, ValueKind
from simulator.battery.migrate import REPO_ROOT, Migrator, select_declared_source


def test_specific_gas_yield_numeric_value_uses_each_printed_species() -> None:
    expected_by_species = [
        ("H2", "4.98", "0.002470238095238095238095238095"),
        ("N2", "352.0", "0.01256514599842935675019633041"),
        ("CH4", "8.19", "0.0005105030231253506202081904881"),
        ("CO", "109.0", "0.003891467333095323376755968090"),
        ("CO2", "4640.0", "0.1054329796178054488854552478"),
        ("H2S", "8.46", "0.0002482685761239582110576358728"),
        ("COS", "10.9", "0.0001814549692025969702014316631"),
        ("H2O", "2890.0", "0.1604218706633361087982237025"),
    ]
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

    for species, printed_ug_g, expected_mol_per_kg in expected_by_species:
        row = next(
            observation
            for observation in rows
            if observation.identity.species.formula == species
            and observation.derivation is not None
            and observation.derivation.relation.startswith("specific_gas_yield_")
            and dict(observation.derivation.parameters)["original"].state.value
            == Decimal(printed_ug_g)
        )
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == Decimal(expected_mol_per_kg)


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
