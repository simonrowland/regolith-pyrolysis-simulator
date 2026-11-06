from pathlib import Path

import yaml


EXTRACT = (
    Path(__file__).parents[1]
    / "data/literature/extracts/moeller-2007-silicon-oxide-evaporation-condensation.yaml"
)


def _context(document: dict, species: str, observation_id: str) -> dict:
    return next(
        row
        for row in document["species"][species]["context"]
        if row["observation_id"] == observation_id
    )


def test_moeller_2007_printed_table_rows_and_locator_pins() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))

    table_22 = _context(document, "SiO2", "moeller2007_table22_to_band_positions")
    assert table_22["locator"] == {"page": 43, "pdf_page_index": 42, "published_page": 38, "table": "2.2"}
    assert len(table_22["values"]["rows"]) == 6
    assert table_22["values"]["rows"][0]["rocking_mode_cm_1"] == "450"

    table_23 = _context(document, "SiO2", "moeller2007_table23_lo_band_positions")
    assert table_23["locator"] == {"page": 43, "pdf_page_index": 42, "published_page": 38, "table": "2.3"}
    assert len(table_23["values"]["rows"]) == 3
    assert table_23["values"]["rows"][2]["stretching_mode_cm_1"] == "1170; 1159,9"

    table_45 = _context(document, "SiO", "moeller2007_table45_to_band_positions")
    assert table_45["locator"] == {"page": 82, "pdf_page_index": 81, "published_page": 77, "table": "4.5"}
    assert len(table_45["values"]["rows"]) == 4
    assert table_45["values"]["rows"][3]["nu_TO_cm_1"] == "994"

    table_46 = _context(document, "SiO", "moeller2007_table46_anneal_series")
    assert table_46["locator"] == {"page": 86, "pdf_page_index": 85, "published_page": 81, "table": "4.6"}
    assert len(table_46["values"]["rows"]) == 8
    assert table_46["values"]["rows"][7]["nu_TO_cm_1"] == "1076"

    table_a2 = _context(
        document, "SiO", "moeller2007_tableA2_anneal_rocking_bending"
    )
    assert table_a2["locator"] == {
        "page": 100,
        "pdf_page_index": 99,
        "published_page": 95,
        "table": "A.2",
    }
    assert len(table_a2["values"]["rows"]) == 16
    assert table_a2["values"]["rows"][1]["resonance_frequency_cm_1"] == "422"
    assert table_a2["values"]["rows"][15]["resonance_frequency_cm_1"] == "802"

    deposition_fit = _context(
        document, "SiO", "moeller2007_table42_fitted_band_positions_and_thickness"
    )
    assert deposition_fit["locator"] == {"page": 73, "pdf_page_index": 72, "published_page": 68, "table": "4.2"}
    assert deposition_fit["values"]["fitted_film_thickness_nm"] == "166"
    assert deposition_fit["values"]["film_thickness_from_deposition_rates_nm"] == "104,8"

    conditions = _context(
        document, "SiO", "moeller2007_text_deposition_conditions"
    )["values"]["printed_conditions"]
    assert conditions["source_temperature"].startswith("not tabulated")
    assert "source_temperature_K" not in conditions
