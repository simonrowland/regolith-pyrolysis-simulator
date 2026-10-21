"""Table 1 mass-loss values and temperature binding for Yokokawa et al. (1984)."""

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass, Quantity, ValueKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/yokokawa-1984-alumina-carbon-volatilization.yaml"
EXPECTED = {
    "yokokawa_1984_alumina_carbon_volatilization_p005_t01_r03_c06": "0.711",
    "yokokawa_1984_alumina_carbon_volatilization_p005_t01_r04_c06": "0.920",
    "yokokawa_1984_alumina_carbon_volatilization_p005_t01_r05_c06": "0.564",
    "yokokawa_1984_alumina_carbon_volatilization_p005_t01_r06_c06": "0.694",
    "yokokawa_1984_alumina_carbon_volatilization_p005_t01_r07_c06": "0.684",
    "yokokawa_1984_alumina_carbon_volatilization_p005_t01_r08_c06": "0.756",
    "yokokawa_1984_alumina_carbon_volatilization_p005_t01_r09_c06": "0.888",
    "yokokawa_1984_alumina_carbon_volatilization_p005_t01_r10_c06": "0.896",
    "yokokawa_1984_alumina_carbon_volatilization_p005_t01_r11_c06": "0.989",
    "yokokawa_1984_alumina_carbon_volatilization_p005_t01_r12_c06": "0.752",
    "yokokawa_1984_table1_series_a_event_mass_loss_fraction": "0.86",
    "yokokawa_1984_table1_series_b_event_mass_loss_fraction": "0.86",
    "yokokawa_1984_table1_series_c_event_mass_loss_fraction": "0.85",
    "yokokawa_1984_table1_series_d_event_mass_loss_fraction": "0.76",
    "yokokawa_1984_table1_series_e_event_mass_loss_fraction": "0.67",
    "yokokawa_1984_table1_series_f_event_mass_loss_fraction": "0.97",
}


def test_table1_values_are_retained() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = document["species"]["unknown"]["observations"]
    actual = {
        observation["observation_id"]: observation["values"]["mass_loss_fraction"]
        for observation in observations
        if observation["values"].get("quantity") == "mass_loss_fraction"
    }
    assert actual == EXPECTED
    assert len(observations) == 16
    assert document["fidelity_samples"][0]["value"] == "0.711"
    assert document["fidelity_samples"][1]["value"] == "0.86"


def test_isolated_migration_preserves_values_species_phase_and_temperature() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert len(result.observations) == 16
    assert not result.validation.hard_issues
    assert not result.registry_issues

    by_source_id = {
        observation.observation_id.rsplit("::", 1)[-1]: observation
        for observation in result.observations.values()
    }
    for source_id, expected_value in EXPECTED.items():
        record = by_source_id[source_id]
        assert record.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
        assert record.identity.species.formula == "unknown"
        assert record.identity.species.phase.value.value == "cr"
        assert record.evidence.class_.value is EvidenceClass.MEASURED_TABULATED
        assert record.value.kind is ValueKind.POINT
        assert str(record.value.point) == expected_value
        condition = record.point_conditions["temperature_K"]
        assert condition.locator is not None
        if source_id.endswith(("_r03_c06", "_r04_c06", "_r05_c06", "_r06_c06", "_r07_c06", "_r08_c06", "_r09_c06", "_r10_c06", "_r11_c06", "_r12_c06")):
            assert not condition.state.is_value
        else:
            assert condition.state.is_value
            assert str(condition.state.value) == {
                "yokokawa_1984_table1_series_a_event_mass_loss_fraction": "1908",
                "yokokawa_1984_table1_series_b_event_mass_loss_fraction": "1908",
                "yokokawa_1984_table1_series_c_event_mass_loss_fraction": "1898",
                "yokokawa_1984_table1_series_d_event_mass_loss_fraction": "1853",
                "yokokawa_1984_table1_series_e_event_mass_loss_fraction": "1813",
                "yokokawa_1984_table1_series_f_event_mass_loss_fraction": "1913",
            }[source_id]
        assert record.value.point is not None
        assert record.value.point >= Decimal("0")
        assert record.value.point <= Decimal("1")

    assert by_source_id["yokokawa_1984_table1_series_a_event_mass_loss_fraction"].point_conditions["temperature_K"].state.value == Decimal("1908")
