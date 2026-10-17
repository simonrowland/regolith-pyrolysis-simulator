"""Source pins and isolated migration checks for Shornikov (2019)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Phase, ValueKind
from simulator.battery.identity import profile_for
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/shornikov-2019-lunar-meteorite-evaporation-model.yaml"
)


def test_table_rows_are_source_pinned_and_migrate_with_temperature():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = [
        (formula, row)
        for formula, block in document["species"].items()
        for row in block.get("observations", [])
    ]
    table1 = [
        (formula, row)
        for formula, row in observations
        if row["locator"].get("table") == "1"
    ]
    assert len(table1) == 113
    assert {row["values"]["table_row_number"] for _, row in table1} == set(
        range(1, 48)
    )
    assert {row["phase"] for _, row in table1} == {"cr", "l", "g"}

    # These three cells were read from the page 4 render: Table 1 row 1,
    # solid CaO, liquid CaO and gas Ca, respectively.
    rendered_cells = (
        ("CaO", "shornikov_2019_table1_r01_solid_CaO", "-421.511", Phase.CR),
        ("CaO", "shornikov_2019_table1_r01_liquid_CaO", "-409.450", Phase.L),
        ("Ca", "shornikov_2019_table1_r01_gas_Ca", "-21.080", Phase.G),
    )
    for formula, observation_id, value, phase in rendered_cells:
        row = next(
            row
            for candidate_formula, row in table1
            if candidate_formula == formula
            and row["observation_id"] == observation_id
        )
        assert row["values"]["value"] == value
        assert row["locator"]["pdf_page_index"] == 3
        assert row["locator"]["published_page"] == 868
        assert row["phase"] == phase.value
        assert row["values"]["method_class"] == "quoted_attributed"
        assert row["values"]["attribution"]

    table2 = document["species"]["CaO"]["context"]
    assert len(table2) == 5
    assert all(
        row["values"]["method_class"] == "quoted_attributed" for row in table2
    )
    assert all(row["values"]["attribution_as_printed"] for row in table2)
    assert table2[0]["values"]["oxide_concentration_mol_pct_as_printed"] == {
        "CaO": "14.43",
        "MgO": "25.60",
        "Al2O3": "23.83",
        "SiO2": "36.14",
    }
    assert table2[0]["values"]["oxide_cells_printed_as_dash"] == ["FeO", "TiO2"]
    assert table2[3]["values"]["evaporation_temperature_as_printed"] == "1850-2400"
    assert table2[4]["values"]["evaporation_temperature_as_printed"] == "1700-2300"

    migrator = Migrator(root=ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    migrated = [
        row
        for row in result.observations.values()
        if row.source_id == document["source_id"]
    ]
    assert len(migrated) == 113
    assert not result.registry_issues
    assert not result.validation.hard_issues
    assert not [row for row in migrated if row.value.kind is ValueKind.UNAVAILABLE]

    for row in migrated:
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal("2000")
        temperature = row.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == Decimal("2000")
        assert row.identity.per.is_value
        assert row.identity.per.value.value == "mol_species"
        assert "temperature_K" in profile_for(row.identity).required
        assert row.identity.composition.is_not_applicable
        assert row.identity.total_pressure_Pa.is_not_applicable
        assert row.identity.fO2_Pa.is_not_applicable

    first = migrated[0].identity
    assert first.standard_pressure_Pa.is_unknown
    assert first.reaction.is_unknown
    assert first.formation_elements.is_unknown
