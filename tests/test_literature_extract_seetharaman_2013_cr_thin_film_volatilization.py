"""Source pins and isolated migration for Seetharaman et al. (2013)."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import (
    check_all_fidelity_samples_match,
    validate_extract_document,
)


ROOT = Path(__file__).resolve().parents[1]
SID = "seetharaman-2013-cr-thin-film-volatilization"
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"

TABLE1 = (
    ("Pt-wire", "99.99", "Alfa-Aesar, Germany"),
    ("Cr2O3", "99.8", "Sigma-Aldrich"),
    ("SiO2", "99 (reagent grade)", "Sigma-Aldrich"),
    ("MgO", "99", "Sigma-Aldrich"),
    ("CaO", "99", "Sigma-Aldrich"),
    ("Al2O3", "99.8", "Sigma-Aldrich"),
)
TABLE2 = (("45.0", "20.0", "20.0", "8.0", "7.0"),)
TABLE3 = (
    ("ASlag-Liq#1", ("61.0", "71.9", "78.0")),
    ("CaMg2Al16O27_Solid", ("16.9", "0.0", "0.0")),
    ("ASpinel", ("22.1", "28.1", "21.9")),
)
TABLE4 = (
    ("Spinel", ("61.82", "8.36", "29.65", "0.10", "0.04", "0.03")),
    (
        "Matrix (glassy phase per adjacent prose)",
        ("66.59", "3.07", "17.84", "8.03", "4.47", "0.00"),
    ),
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _context_by_id(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for payload in document["species"].values()
        for row in payload.get("context", [])
    }


def _assert_table_rows_match_render(document: dict) -> None:
    rows = _context_by_id(document)
    table1 = rows["seetharaman2013_table1_reagent_purity"]["values"]["rows"]
    assert tuple(
        (row["chemical"], row["purity_percent"], row["supplier"])
        for row in table1
    ) == TABLE1

    table2 = rows["seetharaman2013_table2_initial_slag_composition"]["values"]
    assert table2["basicity_CaO_over_SiO2"] == "1"
    assert tuple(tuple(row["mass_percent"]) for row in table2["rows"]) == TABLE2

    table3 = rows["seetharaman2013_table3_factsage_condensed_phases"]["values"]
    assert tuple(table3["temperature_headers_as_published"]) == (
        "1673 K (1400 °C)",
        "1773 K (1500 °C)",
        "1873 K (1600 °C)",
    )
    assert tuple(
        (row["phase"], tuple(row["amounts_wt_percent"]))
        for row in table3["rows"]
    ) == TABLE3

    table4 = rows["seetharaman2013_table4_wds_phase_compositions"]["values"]
    assert (table4["treatment_temperature_K"], table4["treatment_duration_h"]) == (
        "1873",
        "24",
    )
    assert tuple(
        (row["phase"], tuple(row["values"])) for row in table4["rows"]
    ) == TABLE4


def test_tables_match_independent_600_dpi_transcription_and_row_counts() -> None:
    document = _document()
    rows = _context_by_id(document)
    assert len(rows["seetharaman2013_table1_reagent_purity"]["values"]["rows"]) == 6
    assert len(rows["seetharaman2013_table2_initial_slag_composition"]["values"]["rows"]) == 1
    assert len(rows["seetharaman2013_table3_factsage_condensed_phases"]["values"]["rows"]) == 3
    assert len(rows["seetharaman2013_table4_wds_phase_compositions"]["values"]["rows"]) == 2
    _assert_table_rows_match_render(document)


def test_independent_table_and_figure_pins_reject_mutations() -> None:
    document = _document()
    _assert_table_rows_match_render(document)
    assert check_all_fidelity_samples_match(document, label=SID) == []

    changed_table = deepcopy(document)
    table4 = _context_by_id(changed_table)[
        "seetharaman2013_table4_wds_phase_compositions"
    ]
    table4["values"]["rows"][0]["values"][5] = "0.04"
    with pytest.raises(AssertionError):
        _assert_table_rows_match_render(changed_table)
    assert check_all_fidelity_samples_match(changed_table, label=f"{SID}-mutated")

    changed_figure = deepcopy(document)
    figure2 = _context_by_id(changed_figure)["seetharaman2013_figure2_mass_loss"]
    figure2["series"][0]["label"] = "0.5mm Air"
    with pytest.raises(AssertionError):
        assert [
            series["label"] for series in figure2["series"]
        ] == ["0.5mm O2", "1.0mm Air", "0.5mm Air"]


def test_figure_legends_conditions_and_undigitized_points_match_renders() -> None:
    document = _document()
    rows = _context_by_id(document)
    figures = [row for row in rows.values() if row["type"] == "figure_record"]
    assert len(figures) == 9
    assert all(row["numeric_points"] == "none" for row in figures)

    assert [
        (series["label"], series["line_style"])
        for series in rows["seetharaman2013_figure2_mass_loss"]["series"]
    ] == [
        ("0.5mm O2", "dashed"),
        ("1.0mm Air", "dash-dot"),
        ("0.5mm Air", "solid"),
    ]
    assert rows["seetharaman2013_figure2_mass_loss"]["values"][
        "legend_sample_as_published"
    ] == rows["seetharaman2013_figure2_mass_loss"]["series"][0]["label"]
    assert [
        (series["label"], series["legend_swatch"])
        for series in rows["seetharaman2013_figure6_cr_content_time"]["series"]
    ] == [("2h O2", "light gray"), ("6h O2", "dark gray")]
    assert [
        (series["label"], series["line_style"])
        for series in rows["seetharaman2013_figure7_mass_loss"]["series"]
    ] == [
        ("1873K O2", "dash-dot"),
        ("1873K Air", "dashed"),
        ("1873K Ar", "solid"),
    ]
    assert [
        (series["label"], series["legend_color"])
        for series in rows["seetharaman2013_figure9_cr_content_soak"]["series"]
    ] == [
        ("Cr original", "orange"),
        ("2h Ar", "light blue"),
        ("4h Air", "purple"),
        ("2h O2", "green"),
        ("6h O2", "blue"),
        ("24h O2", "black"),
    ]

    model_figure = rows["seetharaman2013_figure1_calculated_cro3_pressure"]
    assert model_figure["values"]["oxygen_partial_pressure_Pa_as_modelled"] == "9 × 10^4"
    assert "not an experimental oxygen pressure" in model_figure["values"]["note"]
    assert rows["seetharaman2013_experimental_method_geometry_conditions"]["values"][
        "numeric_experimental_oxygen_partial_pressure"
    ] == "not stated"

    for record in rows.values():
        conditions = record.get("point_conditions", {})
        assert "total_pressure_Pa" not in conditions
        assert "oxygen_partial_pressure" not in conditions
        values = record.get("values", {})
        if "total_pressure" in values:
            assert values["total_pressure"] == "not stated"


def test_extract_validates_and_real_isolated_migration_has_no_unknown_rows() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=SID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []
    assert check_all_fidelity_samples_match(document, label=SID) == []

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert not result.validation.hard_issues
    assert not result.registry_issues
    assert result.observations == {}
    assert len(result.queue) == 1
    assert result.queue[0].observation_id == f"{SID}::typed_absence"
    assert result.queue[0].axes == ["document"]
    assert result.queue[0].why == "extract yielded no observations"
    context_ids = {
        row["observation_id"]
        for rows in result.context_by_work.values()
        for row in rows
    }
    assert context_ids == set(_context_by_id(document))

    # All measurable Cr at.% and Cr-specific loss rows are typed-refused context;
    # no observation exists whose quantity or phase could be unknown or inferred.
    assert not document["species"]["Cr"]["observations"]
