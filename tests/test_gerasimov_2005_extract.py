from pathlib import Path

import yaml


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data/literature/extracts/gerasimov-2005-laser-impact-melts.yaml"
)


def test_gerasimov_table_1_compositions_match_rendered_page_354():
    """Pin the Table 1 oxide rows to the page 4 render (printed p. 354)."""
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = document["species"]["silicate_melt"]["observations"]
    assert len(observations) == 2
    assert {row["values"]["table_row_count"] for row in observations} == {9}

    condensates = {
        row["values"]["sample_id"]: row["values"]["condensate_wt_percent"]
        for row in observations
    }
    assert condensates == {
        "augite": {
            "SiO2": 54.69,
            "TiO2": 0.74,
            "Al2O3": 7.57,
            "FeO": 8.11,
            "MgO": 12.06,
            "CaO": 12.33,
            "Na2O": 4.51,
        },
        "obsidian": {
            "SiO2": 62.16,
            "TiO2": 1.13,
            "Al2O3": 12.56,
            "FeO": 5.27,
            "MgO": 6.58,
            "CaO": 4.23,
            "Na2O": 7.25,
            "K2O": 1.08,
        },
    }
    starting = {row["values"]["sample_id"]: row["values"] for row in observations}
    assert starting["augite"]["composition_wt_pct"]["SiO2"] == 49.29
    assert starting["augite"]["starting_oxide_sigma_wt_percent"]["SiO2"] == 0.10
    assert starting["augite"]["starting_analyses_count"] == 18
    assert starting["obsidian"]["composition_wt_pct"]["K2O"] == 0.53
    assert starting["obsidian"]["starting_oxide_sigma_wt_percent"]["K2O"] == 0.04
    assert starting["obsidian"]["starting_analyses_count"] == 10
    assert observations[0]["values"]["censored_condensate_wt_percent"] == {"MnO": "<0.10"}
    assert "K2O" not in starting["augite"]["composition_wt_pct"]
    assert "MnO" not in starting["obsidian"]["composition_wt_pct"]
