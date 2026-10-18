from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/sack-2021-melilite-thermochemistry.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _context(document: dict, observation_id: str) -> dict:
    return next(
        row
        for row in document["species"]["gehlenite"]["context"]
        if row["observation_id"] == observation_id
    )


def test_sack_2021_tables_match_600_dpi_render_pins() -> None:
    document = _document()
    species = document["species"]["gehlenite"]

    table1 = _context(
        document, "sack_2021_table1_ordered_melilite_model_parameter_mapping"
    )
    table2 = _context(
        document, "sack_2021_table2_quadrilateral_melilite_model_parameter_mapping"
    )
    table3 = _context(
        document, "sack_2021_table3_disordered_melilite_model_parameter_mapping"
    )
    table4 = _context(document, "sack_2021_table4_adopted_thermodynamic_inputs")

    assert len(table1["values"]["rows_as_printed"]) == 6
    assert len(table2["values"]["rows_as_printed"]) == 26
    assert len(table3["values"]["rows_as_printed"]) == 21
    assert table2["values"]["additional_quartic_parameter_families_as_printed"][
        "count"
    ] == 30
    assert table2["values"]["rows_as_printed"][11]["equivalent_parameter"] == (
        "W25 − W12 − W14"
    )

    thermo_rows = table4["values"]["enthalpy_entropy_volume_rows_as_printed"]
    cp_rows = table4["values"]["heat_capacity_rows_as_printed"]
    assert len(thermo_rows) == 8
    assert len(cp_rows) == 8

    # Literal cells below were read from the 600 dpi render of printed page 446.
    assert thermo_rows[0]["delta_fH_J_as_printed"] == "-4228730"
    assert thermo_rows[1]["entropy_J_per_K_as_printed"] == "91.725"
    assert thermo_rows[2]["volume_J_per_bar_as_printed"] == "0.0"
    assert thermo_rows[7]["delta_fH_J_as_printed"] == "-3985158"
    assert cp_rows[0]["k0"] == "439.37"
    assert cp_rows[7]["k3_x_10^-7"] == "58.5"
    assert cp_rows[2]["k3_x_10^-7"] is None

    figures = [row for row in species["context"] if row["type"] == "figure_record"]
    assert len(figures) == 9
    assert all(
        row["values"]["digitisation"] == "deferred to a figure pass"
        and "numeric_points" not in row["values"]
        for row in figures
    )
    figure_by_number = {row["locator"]["figure"]: row for row in figures}
    assert figure_by_number["3"]["values"]["series"][0]["marker_count"] == 8
    assert figure_by_number["5"]["values"]["series"][0]["marker_count"] == 1
    assert [
        row["marker_count"] for row in figure_by_number["6"]["values"]["series"][:2]
    ] == [3, 3]
    assert [
        row["marker_count"] for row in figure_by_number["9"]["values"]["series"][:2]
    ] == [29, 14]
    assert species["observations"] == []


def test_sack_2021_context_only_extract_migrates_without_hard_issues() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    assert result.validation is not None
    assert not result.validation.hard_issues
    assert len(result.works) == 1
    assert len(result.observations) == 0
