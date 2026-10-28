from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.migrate import migrate
from tests.battery.test_migrate import _write_min_tree
from tools.validate_literature_extracts import validate_extract_file


REPO_ROOT = Path(__file__).resolve().parents[2]
EXTRACT_PATH = (
    REPO_ROOT
    / "data"
    / "literature"
    / "extracts"
    / "shornikov-2000-na-zno-phosphate-vaporization.yaml"
)
AUDIT_PATH = (
    REPO_ROOT
    / "data"
    / "literature"
    / "extracts"
    / "audits"
    / "shornikov-2000-na-zno-phosphate-vaporization.audit.md"
)


def test_shornikov_2000_context_only_extract_validates_and_migrates(tmp_path: Path) -> None:
    doc = yaml.safe_load(EXTRACT_PATH.read_text(encoding="utf-8"))
    assert validate_extract_file(EXTRACT_PATH) == []
    expected_candidates = {
        "shornikov_2000_fig1c_na2o_activity": "melt_activity",
        "shornikov_2000_fig2_evaporation_composition": "residue_composition",
        "shornikov_2000_fig3_melt_formation_gibbs_energy": "thermochemistry",
    }
    expected = set(expected_candidates.items())
    extraction_candidates = doc["extraction"]["figure_read_candidates"]
    context_candidates = doc["species"]["Na"]["context"][2]["values"][
        "figure_read_candidates"
    ]
    assert {
        (row["candidate_id"], row["rail"]) for row in extraction_candidates
    } == expected
    assert {
        (row["candidate_id"], row["rail"]) for row in context_candidates
    } == expected
    assert len(extraction_candidates) == len(context_candidates) == 3
    assert all(row["figure"] and row["x_axis_as_printed"] for row in extraction_candidates)
    assert "1(c), p. 60" in AUDIT_PATH.read_text(encoding="utf-8")
    assert sum(
        len(block["observations"])
        for block in doc["species"].values()
    ) == 0

    root = _write_min_tree(tmp_path, doc)
    result = migrate(root, write=False)

    assert result.observations == {}
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.context_by_work) == 1
    assert sum(len(rows) for rows in result.context_by_work.values()) == 3
