from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "sugawara-2024-na-silicate-molybdate-miscibility"
EXTRACT = ROOT / f"data/literature/extracts/{SOURCE_ID}.yaml"


def test_render_verified_tables_are_preserved_as_context_and_migrate_cleanly() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    context = document["species"]["SiO2"]["context"]
    rows = {row["observation_id"]: row for row in context}
    table1 = rows["sugawara_2024_table1_experimental_compositions_and_run_products"]
    table2 = rows["sugawara_2024_table2_phase_proportions_and_quenched_compositions"]
    table4 = rows["sugawara_2024_table4_calphad_interaction_parameters"]

    assert len(table1["values"]["rows_as_printed"]) == table1["values"]["printed_row_count"] == 11
    assert len(table2["values"]["rows_as_printed"]) == table2["values"]["printed_row_count"] == 8
    assert len(table4["values"]["rows_as_printed"]) == table4["values"]["printed_row_count"] == 6

    # These values were independently checked against the rendered PDF pages.
    assert table1["values"]["rows_as_printed"][0][1:4] == ["43.50", "43.50", "13.00"]
    assert table1["locator"]["page"] == 2 and table1["locator"]["table"] == "1"
    assert table2["values"]["rows_as_printed"][0][1:6] == ["1200", "72.9", "26.5", "", "0.6"]
    assert table2["values"]["rows_as_printed"][0][6:] == [
        ["51.01", "11.53"],
        ["40.37", "48.50"],
        ["8.62", "39.97"],
    ]
    assert table2["locator"]["page"] == 4 and table2["locator"]["table"] == "2"
    assert table2["values"]["quench_state"] == "asserted_altered"
    assert table4["values"]["rows_as_printed"][0] == ["Mo2O6-Na2MoO4", "-161,000+72 T", "10,000", "0"]
    assert table4["values"]["rows_as_printed"][4] == ["Na4Si2O6-Na2MoO4", "12,000", "0", "10,000"]
    assert table4["locator"]["page"] == 6 and table4["locator"]["table"] == "4"

    # Table 2 has no total pressure or fO2 in print; neither is added as a condition.
    assert "total_pressure_Pa" not in table2["values"]
    assert "fO2_Pa" not in table2["values"]
    assert table2["values"]["schema_gap"]

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert not result.validation.hard_issues
    assert len(result.observations) == 0
    assert sum(len(work_context) for work_context in result.context_by_work.values()) == 4
    assert len(result.queue) == 1
    assert result.queue[0].why == "extract yielded no observations"
    assert result.queue[0].axes == ["document"]
