"""Printed prose mass-loss summaries and deferred figure records."""

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass, Quantity, ValueKind
from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/yang-teng-seetharaman-2012-vanadium-evaporation.yaml"
EXPECTED = {
    "yang_2012_results_oxygen_mass_loss_fraction": "1",
    "yang_2012_results_air_mass_loss_fraction": "0.9",
    "yang_2012_results_co2_mass_loss_fraction": "0.35",
}


def test_printed_mass_loss_values_migrate_with_species_phase_temperature_and_atmosphere() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = document["species"]["V2O5"]["observations"]
    assert len(observations) == 3
    assert {row["observation_id"]: row["values"]["mass_loss_fraction"] for row in observations} == EXPECTED

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert len(result.observations) == 3
    assert not result.validation.hard_issues
    assert not result.registry_issues
    by_id = {row.observation_id.rsplit("::", 1)[-1]: row for row in result.observations.values()}
    for observation_id, expected in EXPECTED.items():
        row = by_id[observation_id]
        assert row.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
        assert row.identity.species.formula == "V2O5"
        assert row.identity.species.phase.value.value == "l"
        assert row.evidence.class_.value is EvidenceClass.MEASURED_DIRECT
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == Decimal(expected)
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal("1873")
        temperature = row.point_conditions["temperature_K"]
        assert temperature.state.value == Decimal("1873")
        assert temperature.locator is not None
        assert row.point_conditions["atmosphere"].state.is_value
        assert row.point_conditions["atmosphere"].locator is not None


def test_all_thirteen_figures_are_deferred_without_numeric_points() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    figures = [row for row in document["species"]["V2O5"]["context"] if row["type"] == "figure_record"]
    assert len(figures) == 13
    assert {row["locator"]["figure"] for row in figures} == {str(n) for n in range(1, 14)}
    assert all(row["values"].get("digitisation") == "deferred to a figure pass" for row in figures)
    assert all(all("marker_count" in series for series in row["values"]["series"]) for row in figures)
