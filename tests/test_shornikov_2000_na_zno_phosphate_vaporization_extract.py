from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2000-na-zno-phosphate-vaporization.yaml"


def test_shornikov_2000_verified_figure_points_migrate_as_context() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert validate_extract_document(document, path=EXTRACT) == []

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    assert migrator.result.observations == {}
    migrated = [
        row for rows in migrator.result.context_by_work.values() for row in rows
    ]
    assert len(migrated) == 38
    by_id = {row["observation_id"]: row for row in migrated}
    pins = (
        ("fig1c_1_na2o_01", "1(c)", 33.23, -14.17, "mole %", "log a_i(i)"),
        ("fig2_1_01", "2", 33.35, 33.29, "mole %", "mole %"),
        ("fig3_1_01", "3", -0.03, -31.94, "mole %", "ΔG_T / RT"),
    )
    for suffix, figure, x_value, y_value, x_unit, y_unit in pins:
        row = by_id[f"shornikov_2000_{suffix}_context"]
        assert row["type"] == "figure_read_point"
        assert row["locator"]["figure"] == figure
        assert row["values"]["method_class"] == "figure_only"
        assert row["values"]["evidence_class"] == "figure_only"
        assert row["values"]["x_value"] == x_value
        assert row["values"]["x_unit"] == x_unit
        assert row["values"]["y_value"] == y_value
        assert row["values"]["y_unit"] == y_unit
        assert row["values"]["uncertainty"]["x"] > 0
        assert row["values"]["uncertainty"]["y"] > 0
        assert row["values"]["pixel_center"]["x"] > 0
        assert row["values"]["pixel_center"]["y"] > 0

    figure_points = [row for row in migrated if row["type"] == "figure_read_point"]
    assert {figure: sum(row["locator"]["figure"] == figure for row in figure_points)
            for figure in ("1(c)", "2", "3")} == {"1(c)": 8, "2": 18, "3": 9}
    candidates = document["extraction"]["figure_read_candidates"]
    assert [candidate["note"].split("; ")[-1] for candidate in candidates] == [
        "8 points read and verified.",
        "18 points read and verified.",
        "9 points read and verified.",
    ]
