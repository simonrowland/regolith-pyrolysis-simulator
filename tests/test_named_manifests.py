from __future__ import annotations

import json

import pytest

from tests.helpers.named_manifests import (
    assert_named_rows,
    load_named_manifest,
    write_named_manifest,
)


def test_named_manifest_round_trips_sorted_rows(tmp_path) -> None:
    path = tmp_path / "rows.jsonl"
    write_named_manifest(path, {"row-b": {"value": 2}, "row-a": {"value": 1}})

    assert [json.loads(line)["identity"] for line in path.read_text().splitlines()] == [
        "row-a",
        "row-b",
    ]
    assert load_named_manifest(path) == {
        "row-a": {"value": 1},
        "row-b": {"value": 2},
    }


def test_named_manifest_mismatch_reports_added_removed_and_changed_ids() -> None:
    with pytest.raises(AssertionError) as caught:
        assert_named_rows(
            {"kept": 1, "added": 2, "changed": "new"},
            {"kept": 1, "removed": 3, "changed": "old"},
            label="fixture rows",
        )

    assert str(caught.value) == (
        "fixture rows differs from its named manifest; added rows: ['added']; "
        "removed rows: ['removed']; changed rows: ['changed']"
    )
