from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Phase, Quantity
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/norris-2016-trace-volatility-thesis.yaml"


def test_norris_2016_table56_residue_rows_and_render_pins() -> None:
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    expected = {
        "norris_2016_ag_f006_table56_residue": ("304", "46"),
        "norris_2016_bi_f010_table56_residue": ("2.3", "0.3"),
        "norris_2016_ge_f019_table56_residue": ("6.1", "1.6"),
    }
    source_observations = {
        row["observation_id"]: row
        for item in source["species"].values()
        for row in item.get("observations", [])
    }
    for row in source_observations.values():
        oxygen = row["values"]["log10_fO2"]
        assert oxygen["units"] == "log10(atm)"
        assert oxygen["locator"]["page"] == 224
        assert oxygen["locator"]["table"] == "5.4"
        pressure = row["point_conditions"]["total_pressure_Pa"]["locator"]
        assert pressure["page"] == 196
        assert pressure["section"] == "5.2"
    # Render-checked cells from PDF p. 227, Table 5.6.
    for observation_id, (value, sigma) in expected.items():
        row = source_observations[observation_id]
        assert row["locator"]["page"] == 227
        assert row["locator"]["table"] == "5.6"
        assert row["values"]["residue_ppm"] == value
        assert row["uncertainty"]["sigma_ppm"] == sigma

    assert sum(
        len(item.get("observations", [])) for item in source["species"].values()
    ) == 160
    assert len(source["experiments"]) == 10
    table57 = next(
        row for row in source["species"]["basaltic_silicate_melt"]["context"]
        if row["observation_id"] == "norris_2016_table57_volatility_factors"
    )
    assert table57["values"]["admission_status"] == "typed_refusal"
    assert len(table57["values"]["values_by_log_fO2"]) == 4
    assert sum(
        len(elements)
        for elements in table57["values"]["values_by_log_fO2"].values()
    ) == 64

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert not result.validation.hard_issues
    residue_rows = [
        row for row in result.observations.values()
        if row.source_id == source["source_id"]
        and row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
    ]
    assert len(residue_rows) == 160
    for row in residue_rows:
        assert row.identity.species.phase.value is Phase.GLASS
        assert row.identity.temperature_K.value == Decimal("1573.15")
        assert row.identity.composition.is_value
        assert row.identity.total_pressure_Pa.value == Decimal("101325")
        pressure = (
            result.experiments[row.experiment_id]
            .pressure_environment.total_pressure_Pa
        )
        assert pressure.locator.page == 196
        assert pressure.locator.section == "5.2"
        assert pressure.locator.table is None
        assert row.point_conditions["temperature_K"].state.value == Decimal("1573.15")
        assert row.point_conditions["fO2_log"].locator.page == 224
        assert row.point_conditions["fO2_log"].locator.table == "5.4"
        assert row.point_conditions["fO2_log"].state.value in {
            Decimal("-7"),
            Decimal("-9"),
            Decimal("-11"),
            Decimal("-13"),
        }
        assert row.point_conditions["starting_composition"].locator.page == 220

    migrated_by_id = {row.observation_id: row for row in residue_rows}
    for observation_id, (value, _) in expected.items():
        row = next(
            value for key, value in migrated_by_id.items()
            if key.endswith(f"::{observation_id}")
        )
        assert row.value.point == Decimal(value)

    contexts = [
        row
        for rows in result.context_by_work.values()
        for row in rows
    ]
    assert len(contexts) == 4
