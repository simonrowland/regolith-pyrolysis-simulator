from pathlib import Path

import yaml


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data/literature/extracts/rego-1988-silicate-na2o-activities.yaml"
)


def test_rego_1988_keeps_printed_log_labels_and_defers_plot_points() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    nao = extract["species"]["Na2O"]
    assert nao["observations"] == []

    figure = next(
        row
        for row in nao["context"]
        if row["observation_id"] == "rego_1988_fig3_na2o_cao_sio2_isoactivity"
    )
    assert figure["locator"]["published_page"] == 657
    assert figure["locator"]["pdf_page_index"] == 2
    values = figure["values"]
    assert [series["activity_label_as_published"] for series in values["series"]] == [
        "-7.02",
        "-7.53",
        "-7.56",
        "-7.72",
        "-7.97",
    ]
    assert all(
        isinstance(series["activity_label_as_published"], str)
        for series in values["series"]
    )
    assert sum(series["marker_count"] for series in values["series"]) == 25
    assert values["digitisation"] == "deferred to a figure pass"
    assert "numeric_points" in values and values["numeric_points"] == "none"

    blast_furnace = next(
        row
        for row in nao["context"]
        if row["observation_id"] == "rego_1988_fig6_na2o_blast_furnace_slag_isoactivity"
    )
    assert blast_furnace["values"]["species"] == "Na2O"
    assert blast_furnace["values"]["schema_gap"]
    assert sum(series["marker_count"] for series in blast_furnace["values"]["series"]) == 26


def test_rego_1988_records_method_standard_state_and_all_deferred_figures() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    species = extract["species"]
    nao = species["Na2O"]
    summary = next(
        row for row in nao["context"] if row["type"] == "measurement_summary"
    )
    assert summary["values"]["standard_state_as_printed"] == "pure liquid Na2O at 1400 C"
    assert summary["values"]["range_as_published"] == "about -8.0 to -7.0"
    assert summary["values"]["approximate"] is True

    method = next(row for row in nao["context"] if row["type"] == "method")
    method_text = method["values"]["method_as_published"]
    assert "Na2O(l) + C(s) = 2Na(g) + CO(g)" in method_text
    assert "pure CO" in method_text
    assert "40 to 60 rpm" in method_text
    assert "3.5 to 4 hours" in method_text

    figures = [
        row
        for species_data in species.values()
        for row in species_data["context"]
        if row["type"] == "figure_record"
    ]
    assert len(figures) == 8
    for figure in figures:
        values = figure["values"]
        assert values["species"] in species
        assert figure["phase"] == "l"
        assert "T_C" in figure or "temperature_as_published" in values
        assert "axes_as_printed" in values or "axes_as_published" in values
        assert values["digitisation"] == "deferred to a figure pass"
        assert values["numeric_points"] == "none"

    for observation_id in (
        "rego_1988_fig3_na2o_cao_sio2_isoactivity",
        "rego_1988_fig5_na2o_mgo_sio2_isoactivity",
        "rego_1988_fig6_na2o_blast_furnace_slag_isoactivity",
    ):
        figure = next(row for row in nao["context"] if row["observation_id"] == observation_id)
        assert figure["values"]["composition_state"] == "unknown"
        assert figure["values"]["composition_unknown_reason"]


def test_rego_1988_tables_text_context_and_fig5_labels_are_pinned() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    species = extract["species"]
    nao = species["Na2O"]

    table_i = next(
        row
        for row in species["SiO2"]["context"]
        if row["observation_id"] == "rego_1988_table_i_sio2_terminal_binary_activities"
    )
    assert table_i["locator"]["published_page"] == 659
    assert table_i["locator"]["pdf_page_index"] == 4
    assert table_i["T_C"] == 1400
    assert table_i["standard_state"] == "pure liquid SiO2 at 1400 C"
    assert table_i["values"]["printed_quantity"] == "log a_SiO2(l)"
    table_i_rows = table_i["values"]["rows"]
    assert table_i_rows[0]["log_a_SiO2(l)_CaO-(SiO2)y"] == "-0.057"
    assert table_i_rows[0]["log_a_SiO2(l)_Na2O-(SiO2)y"] == "-0.982"
    assert table_i_rows[3]["log_a_SiO2(l)_Na2O-(SiO2)y"] == "-0.233"
    assert (
        table_i["values"]["columns"]["Na2O-(SiO2)y"]["method_class"]
        == "quoted_attributed"
    )

    table_ii = next(
        row
        for row in nao["context"]
        if row["observation_id"]
        == "rego_1988_table_ii_calculated_sodium_capacity_and_pressure"
    )
    assert table_ii["locator"]["published_page"] == 661
    assert table_ii["locator"]["pdf_page_index"] == 6
    assert table_ii["values"]["method_class"] == "model_derived"
    assert table_ii["values"]["conditions_as_published"] == {
        "Al2O3": "10 wt pct",
        "basicity": "unit",
    }
    table_ii_rows = table_ii["values"]["rows"]
    assert table_ii_rows[0]["values_as_published"] == ["1.6", "0.9", "0.25"]
    assert table_ii_rows[2]["values_as_published"] == ["2.0", "2.0", "2.0"]
    assert table_ii_rows[3]["values_as_published"] == ["0.008", "0.018", "0.037"]
    assert table_ii_rows[3]["method_class"] == "model_derived"

    fig5 = next(
        row
        for row in nao["context"]
        if row["observation_id"] == "rego_1988_fig5_na2o_mgo_sio2_isoactivity"
    )
    assert [
        series["activity_label_as_published"] for series in fig5["values"]["series"]
    ] == ["-7.02", "-7.53", "-7.56", "-7.72", "-7.97"]

    fig4 = next(
        row
        for row in species["SiO2"]["context"]
        if row["observation_id"] == "rego_1988_fig4_sio2_isoactivity_model"
    )
    reference = fig4["values"]["liquidus_reference"]
    assert reference["activity_as_published"] == 0.92
    assert reference["locator"]["pdf_page_index"] == 4
    assert reference["footnote_locator"]["pdf_page_index"] == 2

    fig7 = next(
        row
        for row in nao["context"]
        if row["observation_id"] == "rego_1988_fig7_na2o_activity_ratio_model_comparison"
    )
    assert all(
        series["method_class"] == "model_derived"
        for series in fig7["values"]["series"][:3]
    )
    assert all(
        series["method_class"] == "measured_reduced"
        for series in fig7["values"]["series"][3:]
    )

    contexts = {row["observation_id"]: row for row in nao["context"]}
    assert contexts["rego_1988_binary_master_composition_range"]["values"][
        "range_as_published"
    ] == "0.21 to 0.33"
    assert contexts["rego_1988_unstirred_reference_binary_difference"]["values"][
        "value_as_published"
    ] == "3 to 5 pct lower in Na2O"
    assert contexts["rego_1988_stirred_composition_variation"]["values"][
        "value_as_published"
    ] == "less than 2 pct"
    vapor = contexts["rego_1988_quoted_binary_na_vapor_pressure_range"]
    assert vapor["values"]["range_as_published"] == "about 10^-2.5 to 10^-1.0 atm"
    assert vapor["values"]["method_class"] == "quoted_attributed"
