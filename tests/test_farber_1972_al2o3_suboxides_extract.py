from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/farber-1972-al2o3-suboxides.yaml"


def test_farber_table2_target_rows_migrate_with_rendered_values_and_printed_conditions():
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})

    observations = migrator.result.observations
    assert len(observations) == 21  # 7 printed species columns × 3 T rows
    measured = [
        observation
        for observation in observations.values()
        if observation.identity.species.formula != "Al2O3"
    ]
    estimated = [
        observation
        for observation in observations.values()
        if observation.identity.species.formula == "Al2O3"
    ]
    assert len(measured) == 18
    assert len(estimated) == 3
    assert all(
        observation.evidence.class_.value.value == "measured_reduced"
        for observation in measured
    )
    assert all(
        observation.admission.status.value == "admitted" for observation in measured
    )
    assert all(
        observation.evidence.class_.value.value == "author_estimate"
        for observation in estimated
    )
    assert all(
        observation.admission.status.value == "rejected" for observation in estimated
    )

    by_species_temperature = {
        (
            observation.identity.species.formula,
            int(observation.identity.temperature_K.value),
        ): observation
        for observation in observations.values()
    }
    # Values are in Pa after the migrator's atm conversion; source anchors are
    # Al 2200 K, O 2300 K, and AlO2 2400 K on rendered PDF page 5.
    assert (
        by_species_temperature["Al", 2200].value.point
        == Decimal("1.8e-7") * Decimal("101325")
    )
    assert (
        by_species_temperature["O", 2300].value.point
        == Decimal("8.7e-7") * Decimal("101325")
    )
    assert (
        by_species_temperature["AlO2", 2400].value.point
        == Decimal("4.9e-8") * Decimal("101325")
    )

    for observation in observations.values():
        temperature = int(observation.identity.temperature_K.value)
        assert temperature in {2200, 2300, 2400}
        assert (
            observation.point_conditions["temperature"].state.value
            == Decimal(temperature)
        )
        composition = observation.identity.composition.value
        assert composition.basis == "pure_oxide_system"
        assert composition.components == (("Al2O3", Decimal("1")),)
        assert observation.identity.total_pressure_Pa.tag.value == "unknown"
        assert observation.identity.fO2_Pa.tag.value == "unknown"
        assert observation.point_conditions.keys() == {
            "temperature_K",
            "temperature",
        }
