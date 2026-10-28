import csv
from copy import deepcopy
from pathlib import Path

import pytest
import yaml


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data/literature/extracts/rego-1988-silicate-na2o-activities.yaml"
)
AUDIT = EXTRACT.with_name("rego-1988-silicate-na2o-activities-figure-audit.csv")
FIGURE_CROPS = {
    "3": EXTRACT.with_name("rego-1988-silicate-na2o-activities-fig3-600dpi.png"),
    "5": EXTRACT.with_name("rego-1988-silicate-na2o-activities-fig5-600dpi.png"),
    "6": EXTRACT.with_name("rego-1988-silicate-na2o-activities-fig6-600dpi.png"),
}
# Three marker centres, both pixel reads, and the stored 2-significant-figure composition.
FIGURE_PINS = {
    "rego_1988_fig3_sq_01": {
        "figure": "3",
        "series": "-7.02",
        "marker": "open square",
        "other": "CaO",
        "read1": ("3879.72", "511.34"),
        "read2": ("3877.00", "511.00"),
        "stored": {"Na2O": "0.09", "CaO": "0.42", "SiO2": "0.49"},
    },
    "rego_1988_fig3_cir_03": {
        "figure": "3",
        "series": "-7.56",
        "marker": "open circle",
        "other": "CaO",
        "read1": ("3718.88", "1097.68"),
        "read2": ("3720.00", "1097.50"),
        "stored": {"Na2O": "0.14", "CaO": "0.24", "SiO2": "0.62"},
    },
    "rego_1988_fig5_dia_03": {
        "figure": "5",
        "series": "-7.72",
        "marker": "open diamond",
        "other": "MgO",
        "read1": ("1546.75", "3623.72"),
        "read2": ("1546.50", "3623.00"),
        "stored": {"Na2O": "0.16", "MgO": "0.2", "SiO2": "0.64"},
    },
}
FIG6_PRINTED_LEGEND = (
    ("-7.53", "open inverted triangle", "10 wt pct", 4),
    ("-7.53", "filled inverted triangle", "20 wt pct", 4),
    ("-7.56", "open square", "10 wt pct", 4),
    ("-7.56", "filled square", "20 wt pct", 4),
    ("-7.72", "open circle", "10 wt pct", 4),
    ("-7.72", "filled circle", "20 wt pct", 4),
    ("-7.97", "open triangle", "10 wt pct", 2),
    ("-7.97", "filled triangle", "20 wt pct", 0),
)
DEFERRED_FIGURES = {
    "rego_1988_fig1_equilibration_apparatus",
    "rego_1988_fig2_approach_to_equilibrium_compositions",
    "rego_1988_fig7_na2o_activity_ratio_model_comparison",
    "rego_1988_fig8_sodium_capacity",
}


def _audit_rows() -> list[dict[str, str]]:
    with AUDIT.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _stored_composition(row: dict[str, str]) -> dict[str, str]:
    stored = {
        "Na2O": row["stored_X_Na2O"],
        row["other_oxide"]: row["stored_X_other"],
        "SiO2": row["stored_X_SiO2"],
    }
    if row["stored_X_Al2O3"]:
        stored["Al2O3"] = row["stored_X_Al2O3"]
    return stored


def _assert_figure_rows_match(extract: dict, rows: list[dict[str, str]]) -> None:
    observations = extract["species"]["Na2O"]["observations"]
    by_id = {row["observation_id"]: row for row in observations}
    assert len(by_id) == len(observations) == len(rows) == 85
    for row in rows:
        observation = by_id[row["point_id"]]
        values = observation["values"]
        composition = {
            key: value
            for key, value in values["composition_mol"].items()
            if key != "basis"
        }
        assert composition == _stored_composition(row)
        assert observation["locator"]["figure"] == row["figure"]
        assert observation["locator"]["series"] == row["series_label"]
        assert observation["locator"]["marker_symbol"] == row["marker"]
        assert values["activity"] == row["series_label"]
        assert values["printed_form"] == "log10"
        assert values["method_class"] == "figure_only"
        assert observation["regime"] == "quench_equilibration"
        assert values["T_C"] == 1400
        assert all(row[f"read{index}_{axis}_px"] for index in (1, 2) for axis in ("x", "y"))
        assert row["render_dpi"] == "600"


def test_rego_1988_keeps_printed_log_labels_and_digitised_marker_counts() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    nao = extract["species"]["Na2O"]
    assert len(nao["observations"]) == 85

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
    assert sum(series["marker_count"] for series in values["series"]) == 30
    assert values["digitisation"].startswith("Marker centres read at 600 dpi")
    assert values["numeric_points"] == AUDIT.name
    assert values["composition_state"] == "digitised"
    assert "schema_gap" not in values

    magnesia = next(
        row
        for row in nao["context"]
        if row["observation_id"] == "rego_1988_fig5_na2o_mgo_sio2_isoactivity"
    )
    assert sum(series["marker_count"] for series in magnesia["values"]["series"]) == 29
    assert magnesia["values"]["composition_state"] == "digitised"

    blast_furnace = next(
        row
        for row in nao["context"]
        if row["observation_id"] == "rego_1988_fig6_na2o_blast_furnace_slag_isoactivity"
    )
    assert blast_furnace["values"]["species"] == "Na2O"
    assert "schema_gap" not in blast_furnace["values"]
    assert sum(
        series["marker_count"] for series in blast_furnace["values"]["series"]
    ) == 26
    assert blast_furnace["values"]["composition_state"] == "digitised"


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
        if figure["observation_id"] in DEFERRED_FIGURES:
            assert values["digitisation"] == "deferred to a figure pass"
            assert values["numeric_points"] == "none"
        elif figure["observation_id"] == "rego_1988_fig4_sio2_isoactivity_model":
            assert (
                values["digitisation"]
                == "curves only, Gibbs-Duhem model, no discrete nodes, not sampled"
            )
            assert values["numeric_points"] == "none"
        else:
            assert values["digitisation"].startswith("Marker centres read at 600 dpi")
            assert values["numeric_points"] == AUDIT.name

    approach = next(
        row
        for row in nao["context"]
        if row["observation_id"] == "rego_1988_fig2_approach_to_equilibrium_compositions"
    )
    assert approach["values"]["composition_state"] == "unknown"
    assert approach["values"]["composition_unknown_reason"]


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


def _assert_fig6_activities_match_printed_legend(extract: dict) -> None:
    """Figure 6 activities come from the printed legend, not from the audit CSV."""

    figure = next(
        row
        for row in extract["species"]["Na2O"]["context"]
        if row["observation_id"] == "rego_1988_fig6_na2o_blast_furnace_slag_isoactivity"
    )
    series = figure["values"]["series"]
    assert [
        (
            item["activity_label_as_published"],
            item["marker"],
            item["Al2O3_content_as_published"],
            item["marker_count"],
        )
        for item in series
    ] == list(FIG6_PRINTED_LEGEND)
    by_marker = {item["marker"]: item["activity_label_as_published"] for item in series}
    assert len(by_marker) == len(series)
    figure_rows = [
        row
        for row in extract["species"]["Na2O"]["observations"]
        if row["locator"]["figure"] == "6"
    ]
    assert len(figure_rows) == 26
    for row in figure_rows:
        printed = by_marker[row["locator"]["marker_symbol"]]
        assert row["values"]["activity"] == printed
        assert row["locator"]["series"] == printed


def test_rego_1988_figure_reads_match_the_audit_csv() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = _audit_rows()
    _assert_figure_rows_match(extract, rows)
    by_point = {row["point_id"]: row for row in rows}
    observations = {
        row["observation_id"]: row
        for row in extract["species"]["Na2O"]["observations"]
    }
    for point_id, pin in FIGURE_PINS.items():
        row = by_point[point_id]
        observation = observations[point_id]
        assert (row["read1_x_px"], row["read1_y_px"]) == pin["read1"]
        assert (row["read2_x_px"], row["read2_y_px"]) == pin["read2"]
        assert row["figure"] == pin["figure"]
        assert row["series_label"] == pin["series"]
        assert row["marker"] == pin["marker"]
        assert row["other_oxide"] == pin["other"]
        assert row["disagreement"] == "0"
        assert _stored_composition(row) == pin["stored"]
        assert observation["values"]["activity"] == pin["series"]
        composition = observation["point_conditions"]["composition"]
        assert composition["amount_basis"] == "mole_fraction"
        assert dict(composition["components"]) == pin["stored"]
        assert observation["point_conditions"]["atmosphere"]["state"] == {
            "tag": "value",
            "value": "pure CO",
        }
        assert observation["point_conditions"]["total_pressure"]["state"]["tag"] == "unknown"
        assert observation["point_conditions"]["oxygen"]["state"]["tag"] == "unknown"
    # A third tight-crop read agreed with the NCC centre for both pairs that
    # had exceeded 2 percent. The stroke-bbox centres were not retained.
    disagreed = [row["point_id"] for row in rows if row["disagreement"] == "1"]
    assert disagreed == []
    settled = {
        "rego_1988_fig3_sq_06": ("3962.50", "1714.50", "0.30070", "0.04714"),
        "rego_1988_fig3_inv_01": ("3650.00", "512.00", "0.02621", "0.42377"),
    }
    for point_id, (x_px, y_px, x_na, x_other) in settled.items():
        row = by_point[point_id]
        assert (row["read2_x_px"], row["read2_y_px"]) == (x_px, y_px)
        assert row["read2_X_Na2O"] == x_na
        assert row["read2_X_other"] == x_other
        assert row["disagreement"] == "0"
    assert {row["figure"] for row in rows} == {"3", "5", "6"}
    assert sum(row["figure"] == "3" for row in rows) == 30
    assert sum(row["figure"] == "5" for row in rows) == 29
    assert sum(row["figure"] == "6" for row in rows) == 26
    for path in FIGURE_CROPS.values():
        assert path.stat().st_size > 10000


def test_rego_1988_fig6_activities_follow_the_printed_legend() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_fig6_activities_match_printed_legend(extract)


def test_rego_1988_fig6_yaml_and_csv_relabel_fails_against_printed_legend() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = _audit_rows()
    relabelled = 0
    for observation in extract["species"]["Na2O"]["observations"]:
        if observation["locator"].get("marker_symbol") != "open square":
            continue
        if observation["locator"].get("figure") != "6":
            continue
        observation["locator"]["series"] = "-7.02"
        observation["values"]["activity"] = "-7.02"
        relabelled += 1
    for row in rows:
        if row["figure"] == "6" and row["marker"] == "open square":
            row["series_label"] = "-7.02"
            relabelled += 1
    assert relabelled == 8
    _assert_figure_rows_match(extract, rows)
    with pytest.raises(AssertionError):
        _assert_fig6_activities_match_printed_legend(extract)


def test_rego_1988_figure_csv_or_yaml_mutation_fails() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = _audit_rows()
    mutated = deepcopy(extract)
    mutated["species"]["Na2O"]["observations"][0]["values"]["composition_mol"]["Na2O"] = "0.99"
    with pytest.raises(AssertionError):
        _assert_figure_rows_match(mutated, rows)
    mutated_rows = deepcopy(rows)
    mutated_rows[0]["stored_X_Na2O"] = "0.99"
    with pytest.raises(AssertionError):
        _assert_figure_rows_match(extract, mutated_rows)
    mutated_rows = deepcopy(rows)
    mutated_rows[0]["read1_x_px"] = "1.00"
    assert mutated_rows[0]["point_id"] == "rego_1988_fig3_sq_01"
    with pytest.raises(AssertionError):
        pin = FIGURE_PINS[mutated_rows[0]["point_id"]]
        assert (mutated_rows[0]["read1_x_px"], mutated_rows[0]["read1_y_px"]) == pin["read1"]
