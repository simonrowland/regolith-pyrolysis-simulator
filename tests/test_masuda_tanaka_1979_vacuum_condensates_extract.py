from __future__ import annotations

import csv
from copy import deepcopy
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
SID = "masuda-tanaka-1979-vacuum-condensates"
EXTRACT = ROOT / "data/literature/extracts" / f"{SID}.yaml"
CELL_AUDIT = ROOT / "data/literature/extracts/audits" / f"{SID}.cells.csv"


def test_masuda_tables_render_pins_and_residue_migration() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))

    assert check_all_fidelity_samples_match(document, label=SID) == []
    table1 = document["species"]["SiO2"]["context"][0]
    table2 = document["species"]["SiO2"]["context"][1]
    assert table1["values"]["row_count_as_printed"] == 22
    assert len(table1["values"]["rows_as_published"]) == 22
    assert table2["values"]["row_count_as_printed"] == 21
    assert len(table2["values"]["rows_as_published"]) == 21
    assert table1["values"]["render_pins"] == {
        "IU-1 condensate La ppm": "13.2",
        "9-26-5 residue Ce ppm": "115",
        "JB-1 K2O percent": "1.44",
    }
    assert table2["values"]["render_pins"] == {
        "Ce, 6-16-3": "0.587",
        "K, 9-26-4": "1.38",
        "Rb, 9-26-5": "0.014",
    }
    with CELL_AUDIT.open(newline="", encoding="utf-8") as stream:
        cells = list(csv.DictReader(stream))
    assert len(cells) == 327
    assert {(cell["table"], cell["row_label"], cell["row"]) for cell in cells} >= {
        ("1", "La", "1"),
        ("1", "Distance from source (cm)", "22"),
        ("2", "La", "1"),
        ("2", "K", "21"),
    }

    changed = deepcopy(document)
    changed["species"]["SiO2"]["context"][0]["values"]["render_pins"]["IU-1 condensate La ppm"] = "13.3"
    assert check_all_fidelity_samples_match(changed, label=SID)

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    source_rows = [row for row in result.observations.values() if row.source_id == SID]
    experiment = next(iter(result.experiments.values()))
    bench = next(iter(result.benches.values()))

    assert not result.validation.hard_issues, result.validation.hard_issues
    assert len(source_rows) == 13
    assert experiment.pressure_environment.total_pressure_Pa.locator.page == 2
    assert experiment.pressure_environment.total_pressure_Pa.locator.published_page == 14
    assert bench.cell_material_and_liner.locator.page == 2
    assert bench.cell_material_and_liner.locator.published_page == 14
    schedule = document["experiments"][0]["thermal_schedule"]
    assert schedule["heating_filament_temperature_C"]["locator"]["page"] == 2
    assert schedule["temperature_range_C"]["locator"]["page"] == 3
    assert schedule["sample_mass_as_printed"]["locator"]["page"] == 3
    assert schedule["usual_evaporation_duration_as_printed"]["locator"]["page"] == 3
    assert schedule["interrupted_run_duration"]["locator"]["page"] == 3
    assert all(row.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION for row in source_rows)
    assert {row.identity.species.formula for row in source_rows} == {
        "La", "Ce", "Nd", "Sm", "Eu", "Gd", "Dy", "Er", "Yb", "Lu", "Ba", "Rb", "Sr"
    }
    assert all(row.value.point is not None for row in source_rows)
    assert all(row.identity.temperature_K.is_unknown for row in source_rows)
    assert all(row.identity.total_pressure_Pa.is_unknown for row in source_rows)
    assert all(row.identity.fO2_Pa.is_unknown for row in source_rows)
    assert all(row.identity.composition.is_unknown for row in source_rows)
    assert all(row.identity.exposure.is_value for row in source_rows)
    assert all(row.identity.exposure.value.duration_s.is_unknown for row in source_rows)
    assert all(row.identity.exposure.value.area_m2.is_unknown for row in source_rows)
    assert all(row.identity.exposure.value.schedule is None for row in source_rows)
    assert all(row.identity.sample_mass_kg.is_unknown for row in source_rows)
    assert all(row.locator.page == 5 and row.locator.table == "1" for row in source_rows)
    assert all(row.point_conditions["T_C"].state.is_unknown for row in source_rows)
    assert all(row.point_conditions["T_C"].locator.page == 3 for row in source_rows)
    assert all(row.point_conditions["fO2_Pa"].state.is_unknown for row in source_rows)
    assert all(row.point_conditions["composition"].state.is_unknown for row in source_rows)
    assert all(row.point_conditions["composition"].locator.page == 5 for row in source_rows)
    assert all(row.point_conditions["composition"].locator.table == "1" for row in source_rows)
    assert all(row.point_conditions["exposure"].state.is_unknown for row in source_rows)
    assert all(row.point_conditions["sample_mass_kg"].state.is_unknown for row in source_rows)
    assert (table1["locator"]["page"], table1["locator"]["table"]) == (5, "1")
    assert (table2["locator"]["page"], table2["locator"]["table"]) == (6, "2")
    by_formula = {row.identity.species.formula: row for row in source_rows}
    assert str(by_formula["Ce"].value.point) == "115"
    assert str(by_formula["La"].value.point) == "69.0"
    assert str(by_formula["Lu"].value.point) == "0.594"
