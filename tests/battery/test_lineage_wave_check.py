from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from tools.lineage_wave_check import _load_source_extract, compare, snapshot
from tests.battery.test_migrate import _write_min_tree


def _snapshot() -> dict:
    return {
        "source_id": "fixture-source",
        "observations": [
            {
                "observation_id": "fixture-source::na_psat",
                "quantity": {"tag": "value", "value": "p_sat"},
                "value": {"kind": "series", "series": [["1200", "101325"], ["1300", "202650"]]},
                "value_units": "Pa",
                "source_units": None,
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


def test_snapshot_records_source_unit_declarations(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path / "tree")
    extract_path = root / "data" / "literature" / "extracts" / "fixture-source.yaml"
    extract = yaml.safe_load(extract_path.read_text())
    row = extract["species"]["Na"]["observations"][0]
    row["units"] = "minutes"
    row["values"]["units_as_published"] = "seconds"
    extract_path.write_text(yaml.safe_dump(extract, sort_keys=False))

    result = snapshot("fixture-source", root)
    source_units = result["observations"][0]["source_units"]
    assert source_units["row_units"] == "minutes"
    assert source_units["values_units_as_published"] == "seconds"


def test_snapshot_fails_when_only_v2_extract_exists(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path / "tree")
    legacy = root / "data" / "literature" / "extracts" / "fixture-source.yaml"
    v2 = root / "data" / "literature" / "extracts-v2"
    v2.mkdir()
    legacy.rename(v2 / legacy.name)

    with pytest.raises(FileNotFoundError, match="legacy extract.*missing"):
        snapshot("fixture-source", root)


def test_compare_empty_snapshot_fails() -> None:
    before, after = _snapshot(), _snapshot()
    before["observations"] = []
    after["observations"] = []
    assert not _compare(before, after)


def test_changed_measurement_fails() -> None:
    before, after = _snapshot(), _snapshot()
    after["observations"][0]["value"]["series"][0][1] = "101326"
    assert not _compare(before, after)


def test_changed_migrated_value_unit_fails() -> None:
    before, after = _snapshot(), _snapshot()
    after["observations"][0]["value_units"] = "kPa"
    assert not _compare(before, after)


def test_changed_source_unit_fails() -> None:
    before, after = _snapshot(), _snapshot()
    before["observations"][0]["source_units"] = {"row_units": "minutes"}
    after["observations"][0]["source_units"] = {"row_units": "seconds"}
    assert not _compare(before, after)


def test_added_derivation_output_unit_is_reported(capsys) -> None:
    before, after = _snapshot(), _snapshot()
    after["observations"][0]["derivation_output_unit"] = "days"
    assert _compare(before, after)
    assert "new derivation unit: days" in capsys.readouterr().out


def test_changed_derivation_output_unit_fails() -> None:
    before, after = _snapshot(), _snapshot()
    before["observations"][0]["derivation_output_unit"] = "minutes"
    after["observations"][0]["derivation_output_unit"] = "seconds"
    assert not _compare(before, after)


def test_source_extract_duplicate_keys_fail_with_line_numbers(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.yaml"
    path.write_text(
        "row:\n"
        "  evidence_class: measured_direct\n"
        "  evidence_class: quoted_attributed\n"
        "  relation: identity\n"
        "  relation: derived\n"
    )

    with pytest.raises(
        ValueError,
        match=r"evidence_class.*lines 2 and 3; 'relation'.*lines 4 and 5",
    ):
        _load_source_extract(path)


def test_source_extract_without_duplicate_keys_loads(tmp_path: Path) -> None:
    path = tmp_path / "clean.yaml"
    path.write_text("row:\n  evidence_class: measured_direct\n  quantity: p_sat\n")

    assert _load_source_extract(path) == {
        "row": {"evidence_class": "measured_direct", "quantity": "p_sat"}
    }


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
