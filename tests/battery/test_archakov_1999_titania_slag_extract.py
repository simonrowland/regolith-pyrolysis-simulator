from pathlib import Path

import yaml


EXTRACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "data/literature/extracts/archakov-1999-titania-slag-evaporation.yaml"
)


def test_table1_initial_compositions_match_rendered_rows() -> None:
    """Table 1, PDF p. 2 render: pin the row count and flagged row 14 cells."""
    extract = yaml.safe_load(EXTRACT_PATH.read_text(encoding="utf-8"))
    table = next(
        item
        for item in extract["species"]["CaO"]["context"]
        if item["observation_id"] == "archakov_1999_table1_initial_melt_compositions"
    )
    points = table["values"]["points"]

    assert len(points) == 17
    assert [point["sample_number"] for point in points] == list(range(1, 18))
    assert points[0]["CaO_mol_pct"] == 34.28
    assert points[13]["CaO_mol_pct"] == 33.42
    assert points[13]["TiO2_mol_pct"] == 33.41
    assert points[13]["SiO2_mol_pct"] == 33.17
    assert points[13]["x_SiO2_over_TiO2_as_printed"] == 0.99
    assert points[16]["SiO2_mol_pct"] == 0.00
    assert points[16]["x_SiO2_over_TiO2_as_printed"] == 0.00
    assert points[13]["locator"]["pdf_page_index"] == 1
    assert points[13]["locator"]["note"] == "Table 1 row 14"
    assert extract["source"]["related_sources"][0]["source_id"] == (
        "shornikov-2000-titania-melts"
    )
