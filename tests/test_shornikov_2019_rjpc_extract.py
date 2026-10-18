"""Rendered source pins and isolated migration for the RJPC perovskite paper."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2019-perovskite-evaporation-rjpc.yaml"

if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import validate_literature_extracts as vle  # noqa: E402


def test_table3_model_values_are_render_pinned_and_stay_context_only():
    """Table 3 is a 13-row model table, not a duplicate measured series."""
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    table = next(
        row
        for row in document["species"]["O"]["context"]
        if row["observation_id"] == "shornikov_2019_rjpc_table3_atomic_oxygen_calculations"
    )
    points = table["values"]
    assert len(points) == 13
    assert [point["T_K"] for point in points] == [
        1791, 1845, 1875, 1920, 1941, 1956, 1976,
        1992, 2027, 2037, 2051, 2100, 2182,
    ]
    equation_fields = (
        "equation_22_23_as_printed",
        "equation_24_as_printed",
        "equation_25_as_printed",
        "equation_26_as_printed",
        "equation_27_as_printed",
        "equation_28_as_printed",
    )
    assert sum(point[field] != "—" for point in points for field in equation_fields) == 68

    # These three literal cells were checked against the 600-dpi render of
    # published p. 1029, Table 3. They span the first, middle, and last rows.
    rendered_cells = (
        (0, "equation_22_23_as_printed", "1.26 × 10–10"),
        (3, "equation_24_as_printed", "1.29 × 10–9"),
        (12, "equation_28_as_printed", "7.78 × 10–8"),
    )
    for index, field, expected in rendered_cells:
        point = points[index]
        assert point[field] == expected
        assert point["locator"]["published_page"] == 1029
        assert point["locator"]["pdf_page_index"] == 5
        assert point["locator"]["table"] == "3"

    # The three source fidelity samples are independent literal pins. A
    # one-cell mutation must make the generic fidelity check go red.
    assert vle.check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []
    points[0]["equation_22_23_as_printed"] = "9.99 × 10–10"
    mutation_errors = vle.check_all_fidelity_samples_match(
        document, label=f"{EXTRACT.stem}-mutated"
    )
    assert any("mismatch" in error for error in mutation_errors), mutation_errors

    migrator = Migrator(root=ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    migrated = [
        observation
        for observation in result.observations.values()
        if observation.source_id == document["source_id"]
    ]
    assert migrated == []
    assert not result.registry_issues
    assert not result.validation.hard_issues


def test_table4_fits_are_schema_refusals_and_measurements_are_not_duplicated():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    refusals = [
        row
        for block in document["species"].values()
        for row in block["context"]
        if row["type"] == "fit_refusal"
    ]
    assert len(refusals) == 8
    assert all(
        row["values"]["reason"] == "schema_gap_function_valued_fit"
        for row in refusals
    )
    assert all(not block["observations"] for block in document["species"].values())

    pressure_cross_references = [
        row
        for block in document["species"].values()
        for row in block["context"]
        if row["type"] == "cross_reference"
        and row["values"].get("quantity") == "existing_partial_pressure_series"
    ]
    assert len(pressure_cross_references) == 7
    assert sum(row["values"]["matching_numeric_points"] for row in pressure_cross_references) == 76
