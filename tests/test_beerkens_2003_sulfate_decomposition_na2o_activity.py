from pathlib import Path

import yaml


EXTRACT = (
    Path(__file__).parents[1]
    / "data/literature/extracts/beerkens-2003-sulfate-decomposition-na2o-activity.yaml"
)


def test_beerkens_printed_table_values_and_deferred_figures_are_preserved() -> None:
    extract = yaml.safe_load(EXTRACT.read_text())
    sulfur = extract["species"]["S"]["context"]
    table_i = next(row for row in sulfur if row["observation_id"].endswith("table_i_sulfur_saturation_context"))
    table_ii = next(row for row in sulfur if row["observation_id"].endswith("table_ii_sulfite_solubility_context"))

    assert table_i["locator"]["page"] == 3
    assert table_i["values"]["rows"][2]["float_glass_mol_m3"] == 0.635
    assert table_i["values"]["rows"][0]["model_glass_mol_m3"] == "unknown"
    assert table_ii["locator"]["page"] == 6
    assert table_ii["values"]["rows"][0]["model_glass"] == 26
    assert table_ii["values"]["rows"][4]["minimum_budd_curve_model_glass"] == 8

    figures = [row for row in sulfur if row["type"] == "figure_record"]
    assert [row["locator"]["figure"] for row in figures] == ["1", "2", "3", "4", "5", "6"]
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all("marker_count" in series for row in figures for series in row["values"]["series"])


def test_beerkens_printed_activity_point_retains_its_approximation_and_conditions() -> None:
    extract = yaml.safe_load(EXTRACT.read_text())
    activity = extract["species"]["Na2O"]["context"][0]
    assert activity["values"]["value_as_published"] == "about 1.7 × 10^-8"
    assert activity["values"]["temperature_K"] == 1673
    assert activity["values"]["inputs_as_published"]["sulfur_dioxide_partial_pressure_bar"] == 0.666
