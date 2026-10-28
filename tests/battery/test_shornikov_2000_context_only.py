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
    assert doc["extraction"]["figure_read_candidates"] == []
    assert "figure-read-candidate list is empty" in AUDIT_PATH.read_text(
        encoding="utf-8"
    )
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
