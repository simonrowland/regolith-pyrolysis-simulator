from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from tools.lineage_wave_check import compare, snapshot
from tests.battery.test_migrate import _write_min_tree


def _snapshot() -> dict:
    return {
        "source_id": "fixture-source",
        "observations": [
            {
                "observation_id": "fixture-source::na_psat",
                "quantity": {"tag": "value", "value": "p_sat"},
                "value": {"kind": "series", "series": [["1200", "101325"], ["1300", "202650"]]},
                "units": "Pa",
                "evidence_class": {"tag": "value", "value": "measured_direct"},
                "admission_status": "admitted",
                "derived_from": None,
                "derivation": None,
            }
        ],
        "comparison_candidate_ids": ["fixture-source::na_psat"],
        "hard_issues": [],
    }


def _compare(before: dict, after: dict) -> bool:
    return compare(before, after)[0]


def test_identical_single_source_snapshot_passes(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path / "tree")
    result = snapshot("fixture-source", root)
    assert result["observations"]
    assert _compare(result, deepcopy(result))


def test_changed_measurement_fails() -> None:
    before, after = _snapshot(), _snapshot()
    after["observations"][0]["value"]["series"][0][1] = "101326"
    assert not _compare(before, after)


def test_removed_observation_fails() -> None:
    before, after = _snapshot(), _snapshot()
    after["observations"] = []
    after["comparison_candidate_ids"] = []
    assert not _compare(before, after)


def test_candidate_set_change_fails() -> None:
    before, after = _snapshot(), _snapshot()
    after["comparison_candidate_ids"] = []
    assert not _compare(before, after)


def test_new_non_lineage_hard_issue_fails() -> None:
    before, after = _snapshot(), _snapshot()
    after["hard_issues"] = [{"path": "extract[fixture].species.Na", "reason": "INVALID_SOURCE", "detail": "new issue"}]
    assert not _compare(before, after)


def test_derived_from_addition_passes_and_is_reported(capsys) -> None:
    before, after = _snapshot(), _snapshot()
    after["observations"][0]["derived_from"] = ["fixture-source::parent"]
    assert _compare(before, after)
    assert "derived_from changes: 1" in capsys.readouterr().out


def test_allow_rekey_requires_one_to_one_measurement_match() -> None:
    before, after = _snapshot(), _snapshot()
    after["observations"][0]["observation_id"] = "fixture-source::renamed"
    after["comparison_candidate_ids"] = ["fixture-source::renamed"]
    assert _compare(before, after) is False  # Default identity gate remains strict.
    assert compare(before, after, allow_rekey=True)[0]
