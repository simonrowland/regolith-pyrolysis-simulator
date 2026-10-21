"""Regression pins for Van Ende et al. (2008) context and deferred figures."""

import csv
from io import StringIO
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/van-ende-2008-mn-cr-slag-steel.yaml"


def test_van_ende_tables_preserve_rendered_rows_and_figures_stay_deferred() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    contexts = document["species"]["SiO2"]["context"]
    equations = next(
        row
        for row in contexts
        if row["observation_id"] == "van_ende_2008_equations_2_9_standard_reaction_free_energies"
    )
    assert equations["locator"] == {"page": 2, "pdf_page_index": 1, "published_page": "1332"}
    assert equations["units"] == "(J/mol)"
    assert equations["values"]["reactions"] == [
        {
            "reaction_number": "1",
            "equation_number": "2",
            "reaction_as_printed": "Mn(l) + O_Fe = MnO(l)",
            "delta_G0_as_printed": "−229 589 + 63.74 T",
            "citations": "27–29",
        },
        {
            "reaction_number": "8",
            "equation_number": "9",
            "reaction_as_printed": "Cr(s) + 3/2 O_Fe = CrO1.5(s)",
            "delta_G0_as_printed": "−379 345 + 119.32 T",
            "citations": "34, 35",
        },
    ]
    assert "temperature" in equations["values"]["note"]
    tables = {row["locator"].get("table"): row for row in contexts if row["locator"].get("table")}
    table3 = list(csv.reader(StringIO(tables["3"]["values"]["printed_rows_csv"])))
    table4 = list(csv.reader(StringIO(tables["4"]["values"]["printed_rows_csv"])))

    # Independently transcribed from the 600 dpi render of physical PDF pages 3–4.
    assert len(table3) == 20  # header plus 19 experiments
    assert table3[1] == ["1-1", "10", "1600", "10", "10", "10", "42.0", "28.0", "1.5"]
    assert table3[-1] == ["6-2", "20", "1580", "10", "10", "5", "45.0", "30.0", "1.5"]
    assert len(table4) == 20  # header plus 19 equilibrated runs
    expected_table4 = [
        ["experiment", "steel_Mn_mass_percent", "steel_Cr_mass_percent", "steel_O_ppm", "slag_MgO_mass_percent", "slag_MnO_mass_percent", "slag_CrO1.5_mass_percent", "slag_CaO_mass_percent", "slag_SiO2_mass_percent", "B", "LMn", "LCr"],
        ["1-1", "7.8", "18.1", "—", "11.8", "14.8", "5.3", "40.4", "26.0", "2.01", "1.47", "0.200"],
        ["1-2", "7.2", "17.6", "126.2", "13.8", "14.5", "4.3", "39.0", "26.2", "2.02", "1.56", "0.167"],
        ["1-3", "6.9", "19.2", "134.7", "15.2", "13.7", "5.7", "39.2", "24.6", "2.21", "1.54", "0.203"],
        ["1-4", "9.9", "17.5", "154.1", "12.1", "14.5", "9.0", "41.5", "27.1", "1.98", "1.13", "0.352"],
        ["2-1", "5.8", "20.2", "103.0", "36.7", "11.2", "1.8", "10.8", "37.7", "1.26", "1.50", "0.061"],
        ["2-2", "4.2", "20.0", "60.6", "8.9", "8.7", "0.6", "33.2", "48.6", "0.87", "1.60", "0.021"],
        ["2-3", "4.8", "20.1", "119.7", "12.6", "9.4", "1.1", "38.8", "37.9", "1.36", "1.52", "0.037"],
        ["3-1", "5.1", "20.2", "67.5", "25.3", "8.2", "0.7", "28.5", "37.3", "1.44", "1.25", "0.024"],
        ["3-2", "5.7", "20.7", "80.7", "23.0", "10.3", "1.6", "27.6", "36.6", "1.38", "1.40", "0.053"],
        ["3-3", "7.3", "19.4", "100.3", "12.2", "10.6", "2.1", "43.3", "29.2", "1.90", "1.12", "0.074"],
        ["3-4", "8.3", "19.8", "68.3", "13.4", "8.7", "2.5", "46.9", "27.7", "2.18", "0.81", "0.086"],
        ["4-1", "6.1", "19.6", "92.8", "22.2", "11.5", "1.8", "26.8", "37.7", "1.30", "1.46", "0.063"],
        ["4-2", "7.7", "19.5", "95.2", "16.8", "13.0", "1.7", "30.3", "36.8", "1.28", "1.31", "0.060"],
        ["4-3", "9.3", "19.0", "136.6", "10.1", "14.7", "2.9", "41.7", "28.4", "1.82", "1.22", "0.104"],
        ["4-4", "8.7", "19.6", "140.2", "15.9", "12.6", "3.0", "43.1", "24.9", "2.37", "1.12", "0.105"],
        ["5-1", "6.5", "19.2", "51.3", "24.7", "8.4", "0.6", "26.6", "39.6", "1.30", "1.00", "0.021"],
        ["5-2", "10.6", "18.6", "47.0", "10.7", "7.5", "0.9", "48.2", "31.9", "1.85", "0.55", "0.033"],
        ["6-1", "9.2", "19.8", "98.2", "11.4", "8.0", "1.4", "46.6", "31.4", "1.85", "0.67", "0.048"],
        ["6-2", "8.4", "19.5", "141.0", "12.2", "8.8", "1.8", "44.5", "30.8", "1.84", "0.81", "0.063"],
    ]
    assert table4 == expected_table4

    figures = [row for row in contexts if row["type"] == "figure"]
    assert len(figures) == 13
    for row in figures:
        assert row["locator"]["page"] == row["locator"]["pdf_page_index"] + 1
        assert row["values"]["digitisation"] == "deferred to a figure pass"
        assert row["values"]["series"]
        assert "marker_count" in row["values"]["series"][0]

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not [row for row in migrator.result.observations.values() if row.source_id == EXTRACT.stem]
    migrated_contexts = [
        row
        for rows in migrator.result.context_by_work.values()
        for row in rows
        if row["context_id"].startswith(f"{EXTRACT.stem}::context::")
    ]
    assert len(migrated_contexts) == len(contexts)
    assert not migrator.result.validation.hard_issues
