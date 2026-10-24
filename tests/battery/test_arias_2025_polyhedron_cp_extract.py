import csv
from io import StringIO
from pathlib import Path

import yaml

from tests.battery.test_migrate import _migrate_real_extract


EXTRACT = Path(__file__).resolve().parents[2] / (
    "data/literature/extracts/arias-2025-polyhedron-oxide-heat-capacity.yaml"
)


def test_arias_model_tables_and_deferred_figures_are_context_only(tmp_path: Path) -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    species = extract["species"]["SiO2"]
    assert species["observations"] == []

    context = species["context"]
    table_a = next(
        item for item in context
        if item["observation_id"] == "arias_2025_table1_a_polyhedron_cp_values"
    )
    table_b = next(
        item for item in context
        if item["observation_id"] == "arias_2025_table1_b_polyhedron_cp_values"
    )
    assert table_a["values"]["quantity"] == "cp"
    assert table_a["values"]["method_class"] == "model_derived"
    assert table_b["values"]["quantity"] == "cp"
    assert table_b["values"]["method_class"] == "model_derived"
    table_a_cells = list(csv.DictReader(StringIO(table_a["values"]["table_csv_as_printed"])))
    table_b_cells = list(csv.DictReader(StringIO(table_b["values"]["table_csv_as_printed"])))
    assert len({cell["origin_row"] for cell in table_a_cells}) == 23
    assert len({cell["origin_row"] for cell in table_b_cells}) == 23
    assert len(table_a_cells) == 437
    assert len(table_b_cells) == 391
    assert next(cell["text"] for cell in table_a_cells if cell["origin_row"] == "2" and cell["origin_column"] == "2") == "55.68"
    assert next(cell["text"] for cell in table_a_cells if cell["origin_row"] == "2" and cell["origin_column"] == "4") == "60.02"
    assert next(cell["text"] for cell in table_b_cells if cell["origin_row"] == "2" and cell["origin_column"] == "2") == "72.43"

    coefficients = next(
        item for item in context
        if item["observation_id"] == "arias_2025_table2_polyhedron_cp_coefficients"
    )
    rows = coefficients["values"]["rows"]
    assert len(rows) == 20
    assert rows["Ti-oct"] == [178.0, -0.124, -194000.0, -1590.0, 0.000109, -3.71e-08]
    assert rows["K-multi"] == [1140.0, -1.35, 9150000.0, -15400.0, 0.00108, -3.43e-07]
    figure_records = [item for item in context if item["type"] == "figure_record"]
    assert {item["locator"]["figure"] for item in figure_records} == set("12345678")
    assert all(
        item.get("digitisation", item["values"].get("digitisation"))
        == "deferred to a figure pass"
        for item in figure_records
    )

    migrated = _migrate_real_extract(tmp_path, EXTRACT.name)
    assert migrated.observations == {}
    assert not migrated.validation.hard_issues
