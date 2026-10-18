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
