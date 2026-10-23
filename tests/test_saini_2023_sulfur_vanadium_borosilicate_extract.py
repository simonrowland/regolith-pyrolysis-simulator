from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "saini-2023-sulfur-vanadium-borosilicate-structure"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"


def test_borosilicate_measurements_are_preserved_and_redox_is_not_overstated() -> None:
    document = yaml.safe_load(EXTRACT.read_text())
    species = document["species"]["V"]
    assert species["observations"] == []
    context = species["context"]

    table_1 = next(row for row in context if row["observation_id"] == "saini_2023_table_1_glass_compositions")
    sulfur_free = table_1["values"]["sulfur_free_rows"]
    assert [(row["glass_id"], row["measured"]) for row in sulfur_free] == [
        ("0V", {"Na2O": 28.95, "B2O3": 16.26, "SiO2": 46.82, "Al2O3": 7.94}),
        ("1V", {"Na2O": 28.14, "B2O3": 15.83, "SiO2": 45.52, "Al2O3": 7.73, "V2O5": 2.77}),
        ("2.5V", {"Na2O": 27.01, "B2O3": 15.16, "SiO2": 43.61, "Al2O3": 7.40, "V2O5": 6.76}),
        ("5V", {"Na2O": 25.20, "B2O3": 14.16, "SiO2": 40.73, "Al2O3": 6.92, "V2O5": 12.98}),
        ("7.5V", {"Na2O": 23.57, "B2O3": 13.24, "SiO2": 38.06, "Al2O3": 6.46, "V2O5": 18.67}),
        ("9V", {"Na2O": 22.64, "B2O3": 12.71, "SiO2": 36.56, "Al2O3": 6.20, "V2O5": 21.88}),
    ]
    sulfur_glasses = table_1["values"]["sulfur_containing_rows"]
    assert [(row["glass_id"], row["measured"]["SO3"]) for row in sulfur_glasses] == [
        ("1V-1.25S", 1.51),
        ("2.5V-1.75S", 2.06),
        ("5V-3S", 3.39),
        ("7.5V-6S", 6.47),
        ("9V-6.5S", 6.83),
    ]

    table_2 = next(row for row in context if row["observation_id"] == "saini_2023_table_2_aluminum_structure")
    assert [(row["glass_id"], row["AlIV_abundance_percent"], row["AlV_abundance_percent"]) for row in table_2["values"]["rows"]] == [
        ("0V", 97, 3),
        ("1V", 98, 2),
        ("2.5V", 98, 2),
        ("5V", 98, 2),
        ("7.5V", 98, 2),
        ("9V", 98, 2),
    ]

    table_3 = next(row for row in context if row["observation_id"] == "saini_2023_table_3_boron_vanadium_structure")
    assert [
        (row["glass_id"], row["BIII_abundance_percent"], row["BIV_abundance_percent"], row["V51_delta_CS_ppm"], row["V51_CQ_MHz"])
        for row in table_3["values"]["rows"]
    ] == [
        ("0V", 40.7, 59.3, None, None),
        ("1V", 41.8, 58.2, -565, 4.6),
        ("2.5V", 36.8, 63.2, -565, 5.6),
        ("5V", 35.4, 64.6, -564, 6.5),
        ("7.5V", 34.2, 65.8, -556, 6.5),
        ("9V", 38.1, 61.9, -560, 6.5),
    ]

    redox = next(row for row in context if row["observation_id"] == "saini_2023_vanadium_redox_xps_context")
    assert "517.3 eV" in redox["values"]["XPS_as_printed"]
    assert "no numeric V3+/V4+/V5+ fraction or ratio" in redox["reason"]
    assert len([row for row in context if row["type"] == "figure_record"]) == 10
    assert all(row["digitisation"] == "deferred to a figure pass" for row in context if row["type"] == "figure_record")
    assert all(row["values"].get("points") == [] for row in context if row["type"] == "figure_record")

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.observations
    assert not migrator.result.validation.hard_issues
