from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "vienna-2004-sulfur-loading-law-glass"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"


def test_printed_sulfur_data_is_preserved_in_context_without_a_store_token() -> None:
    document = yaml.safe_load(EXTRACT.read_text())
    species = document["species"]["S"]
    assert species["observations"] == []
    context = species["context"]

    table_2_1 = next(row for row in context if row["observation_id"] == "vienna_2004_table_2_1_melter_segments")
    assert len(table_2_1["values"]["rows"]) == 13
    assert table_2_1["values"]["rows"][0]["SO3_retention_percent"] == 74
    assert table_2_1["values"]["rows"][-1]["SO3_retention_percent"] == "Not Reported"

    table_2_5 = next(row for row in context if row["observation_id"] == "vienna_2004_table_2_5_rsm_glass_sulfur")
    assert [row["measured_SO3"] for row in table_2_5["values"]["rows"]] == [0.68, 0.66, 0.70, 0.89]
    assert table_2_5["values"]["temperature_as_printed"] == "1147 to 1162 °C; nominal 1150 °C"

    table_2_6 = next(row for row in context if row["observation_id"] == "vienna_2004_table_2_6_ev16_glass_sulfur")
    assert [row["XRF_glass_SO3"] for row in table_2_6["values"]["rows"]] == [0.74, 0.75, 0.76]
    assert [row["ICP_glass_SO3"] for row in table_2_6["values"]["rows"]] == [0.79, 0.77, 0.76]

    table_3_6 = next(row for row in context if row["observation_id"] == "vienna_2004_table_3_6_lsm_sulfur")
    assert len(table_3_6["values"]["rows"]) == 16
    assert [
        (row["test_id"], row["measured_SO3"])
        for row in table_3_6["values"]["rows"]
    ] == [
        ("S6-1-1", 0.46),
        ("M6-1-1", 0.49),
        ("L6-1-2", 0.66),
        ("S9-1-1", 0.63),
        ("M9-1-1", 0.55),
        ("L9-1-2", 0.73),
        ("S12-1-1", 0.70),
        ("M12-1-1", 0.82),
        ("L12-1-2", 0.74),
        ("C12(b)", 0.84),
        ("C15(b)", 1.10),
        ("NS12-2-1", 0.85),
        ("NS15-1-1", 1.15),
        ("NS18-1-1", 1.26),
        ("NS21-1-1", 1.26),
        ("NM12Sg(c)", 0.82),
    ]
    assert table_3_6["values"]["nominal_base_glass_formulations_from_table_3_3"]["LAWC22"]["Na2O"] == 14.58
    assert table_3_6["values"]["nominal_base_glass_formulations_from_table_3_3"]["LAWN-1"]["SO3"] == 0.52

    prose = next(row for row in context if row["observation_id"] == "vienna_2004_ev16_1999_text_measurements")
    assert [row["SO3_in_glass"] for row in prose["values"]["measured_values"]] == [0.49, 0.58]
    relation = next(row for row in context if row["observation_id"] == "vienna_2004_sulfur_loading_relation_context")
    assert relation["values"]["relation_as_printed"] == "g_Na2O × g_SO3 ≤ 5"

    figures = [row for row in context if row["type"] == "figure_record"]
    assert len(figures) == 6
    assert all(row["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all(row["values"].get("points") == [] for row in figures)
    assert all(row["locator"]["page"] == row["locator"]["pdf_page_index"] + 1 for row in figures)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.observations
    assert not migrator.result.validation.hard_issues
