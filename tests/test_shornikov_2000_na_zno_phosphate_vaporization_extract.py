from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2000-na-zno-phosphate-vaporization.yaml"


def _rows(document):
    return [
        row
        for container in document["species"].values()
        for kind in ("observations", "context")
        for row in container.get(kind, [])
    ]


def _figure_label(row):
    locator = row["locator"]
    figure = str(locator["figure"])
    if figure == "1(c)" or (figure == "1" and locator.get("panel") == "c"):
        return "1(c)"
    return figure


def _figure_points(document):
    result = []
    for row in _rows(document):
        locator = row.get("locator", {})
        values = row.get("values", {})
        record_id = row.get("observation_id", "")
        if row.get("type") == "figure_read_point":
            result.append(row)
        elif (
            row.get("type") == "activity_coefficient"
            and values.get("admission_status") == "admitted"
            and locator.get("figure") == "1"
            and locator.get("panel") == "c"
        ):
            result.append(row)
        elif record_id.startswith("shornikov_2000_fig3_series1_"):
            result.append(row)
    return result


def test_shornikov_2000_reconciled_figure_points_and_migration() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert validate_extract_document(document, path=EXTRACT) == []

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    assert len(migrator.result.observations) == 11
    migrated_context = [
        row for rows in migrator.result.context_by_work.values() for row in rows
    ]
    assert len(migrated_context) == 36
    assert sum(row.get("type") == "figure_read_point" for row in migrated_context) == 35

    points = _figure_points(document)
    assert {
        figure: sum(_figure_label(row) == figure for row in points)
        for figure in ("1(c)", "2", "3")
    } == {"1(c)": 8, "2": 18, "3": 9}

    by_id = {row["observation_id"]: row for row in _rows(document)}
    # Preserve the independent Batch4 reads as comparison pins. Green's tighter
    # render reads remain canonical for the overlapping points.
    overlap_pins = (
        (
            "shornikov_2000_fig1c_1_na2o_01_context",
            "shornikov_2000_fig1c_na2o_x33",
            33.23,
            -14.17,
            33.24,
            -14.186,
            0.15,
            0.03,
        ),
        (
            "shornikov_2000_fig3_1_01_context",
            "shornikov_2000_fig3_series1_x0",
            -0.03,
            -31.94,
            -0.02,
            -31.966,
            0.15,
            0.04,
        ),
    )
    for batch_id, green_id, batch_x, batch_y, green_x, green_y, x_bound, y_bound in overlap_pins:
        selected = by_id[green_id]
        selected_values = selected["values"]
        actual_x = selected_values.get("ZnO_mol_percent", selected_values.get("x_value"))
        actual_y = selected_values.get("log10_activity", selected_values.get("delta_fG_over_RT"))
        assert actual_x == green_x
        assert float(actual_y) == green_y
        assert abs(batch_x - actual_x) <= x_bound
        assert abs(batch_y - float(actual_y)) <= y_bound
        assert by_id.get(batch_id) is None

    figure2 = by_id["shornikov_2000_fig2_1_01_context"]
    assert figure2["type"] == "figure_read_point"
    assert figure2["locator"]["figure"] == "2"
    assert figure2["values"]["method_class"] == "figure_only"
    assert figure2["values"]["evidence_class"] == "figure_only"
    assert figure2["values"]["x_value"] == 33.35
    assert figure2["values"]["x_unit"] == "mole %"
    assert figure2["values"]["y_value"] == 33.29
    assert figure2["values"]["y_unit"] == "mole %"
    assert figure2["values"]["uncertainty"]["x"] > 0
    assert figure2["values"]["uncertainty"]["y"] > 0
    assert figure2["values"]["pixel_center"]["x"] > 0
    assert figure2["values"]["pixel_center"]["y"] > 0

    candidates = document["extraction"]["figure_read_candidates"]
    assert [candidate["note"].split("; ")[-1] for candidate in candidates] == [
        "8 points read and verified.",
        "18 points read and verified.",
        "9 points read and verified.",
    ]
