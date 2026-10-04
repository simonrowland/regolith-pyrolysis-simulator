"""b-674: extract rows without an observation_id.

Before (pinned in 7cdec7a80 at work-v064-green 8089eadbf): the migrator
fell back to ``"<source_id>:missing"`` for every id-less row, so identical
id-less rows were silently merged via dedupe aliases and no typed issue was
raised. After: an id-less row is refused at identity resolution with one
typed hard issue (``invalid_identity``) per row and is not migrated.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from simulator.battery.enums import RefusalReason
from simulator.battery.migrate import migrate
from tests.battery.test_migrate import FIXTURE_EXTRACT, _write_min_tree

_FALLBACK_ID = "fixture-source::fixture-source:missing"


def _idless_extract(*, rows: int, differ: bool, keep_one_id: bool = False) -> dict:
    extract = deepcopy(FIXTURE_EXTRACT)
    base = extract["species"]["Na"]["observations"][0]
    base.pop("observation_id")
    extract["fidelity_samples"] = []
    observations = []
    for index in range(rows):
        row = deepcopy(base)
        if differ:
            row["values"]["series"] = [
                {"T_K": 1200.0, "pressure_atm": 1.0 + index}
            ]
        observations.append(row)
    if keep_one_id:
        observations[0]["observation_id"] = "na_psat"
    extract["species"]["Na"]["observations"] = observations
    return extract


def _run(tmp_path: Path, extract: dict):
    root = tmp_path / "tree"
    root.mkdir(parents=True)
    return migrate(_write_min_tree(root, extract), write=False)


def _idless_issues(result) -> list:
    return [
        issue
        for issue in result.registry_issues
        if issue.path.endswith(".observation_id")
        and "b-674" in issue.detail
    ]


def _fallback_ids(result) -> list[str]:
    return sorted(oid for oid in result.observations if oid.startswith(_FALLBACK_ID))


def test_single_idless_row_is_refused_with_typed_issue(tmp_path: Path) -> None:
    result = _run(tmp_path, _idless_extract(rows=1, differ=False))
    assert not _fallback_ids(result)
    assert not result.observations
    issues = _idless_issues(result)
    assert len(issues) == 1
    assert issues[0].reason is RefusalReason.INVALID_IDENTITY
    assert issues[0].path == (
        "extract[data/literature/extracts/fixture-source.yaml].species[Na].observations[0].observation_id"
    )
    assert issues[0] in result.validation.hard_issues


def test_identical_idless_rows_are_not_merged(tmp_path: Path) -> None:
    result = _run(tmp_path, _idless_extract(rows=2, differ=False))
    assert not _fallback_ids(result)
    assert not result.dedupe_aliases
    assert [issue.path for issue in _idless_issues(result)] == [
        "extract[data/literature/extracts/fixture-source.yaml].species[Na].observations[0].observation_id",
        "extract[data/literature/extracts/fixture-source.yaml].species[Na].observations[1].observation_id",
    ]


def test_differing_idless_rows_each_get_an_issue(tmp_path: Path) -> None:
    result = _run(tmp_path, _idless_extract(rows=2, differ=True))
    assert not result.observations
    assert len(_idless_issues(result)) == 2


def test_identified_sibling_still_migrates(tmp_path: Path) -> None:
    result = _run(tmp_path, _idless_extract(rows=2, differ=True, keep_one_id=True))
    assert not _fallback_ids(result)
    assert result.observations
    assert all(oid.startswith("fixture-source::na_psat") for oid in result.observations)
    assert [issue.path for issue in _idless_issues(result)] == [
        "extract[data/literature/extracts/fixture-source.yaml].species[Na].observations[1].observation_id",
    ]
