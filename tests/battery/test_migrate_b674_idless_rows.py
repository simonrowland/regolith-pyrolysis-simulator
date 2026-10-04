"""b-674: extract rows without an observation_id.

PIN (current behaviour at work-v064-green 8089eadbf): the migrator falls
back to ``"<source_id>:missing"`` for every id-less row, so identical
id-less rows are silently merged (the second row's points become dedupe aliases of the
first), and no row raises a typed issue. Differing id-less rows only stay
apart because series points carry a payload hash. These tests capture that
behaviour before the change.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from simulator.battery.migrate import migrate
from tests.battery.test_migrate import FIXTURE_EXTRACT, _write_min_tree

_FALLBACK_ID = "fixture-source::fixture-source:missing"


def _idless_extract(*, rows: int, differ: bool) -> dict:
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
    extract["species"]["Na"]["observations"] = observations
    return extract


def _run(tmp_path: Path, extract: dict):
    root = tmp_path / "tree"
    root.mkdir(parents=True)
    return migrate(_write_min_tree(root, extract), write=False)


def _fallback_ids(result) -> list[str]:
    return sorted(oid for oid in result.observations if oid.startswith(_FALLBACK_ID))


def test_single_idless_row_lands_under_fallback_id(tmp_path: Path) -> None:
    result = _run(tmp_path, _idless_extract(rows=1, differ=False))
    assert _fallback_ids(result) == sorted(result.observations)
    assert len(result.observations) == 2  # two series points
    assert not result.dedupe_aliases
    assert not result.registry_issues


def test_identical_idless_rows_are_silently_merged(tmp_path: Path) -> None:
    result = _run(tmp_path, _idless_extract(rows=2, differ=False))
    assert _fallback_ids(result) == sorted(result.observations)
    aliased = sorted(alias.observation_id for alias in result.dedupe_aliases)
    assert aliased == [
        oid for oid in _fallback_ids(result) if oid != _FALLBACK_ID
    ]
    assert not result.registry_issues


def test_differing_idless_rows_land_without_issue(tmp_path: Path) -> None:
    result = _run(tmp_path, _idless_extract(rows=2, differ=True))
    assert len(_fallback_ids(result)) == 2
    assert _fallback_ids(result) == sorted(result.observations)
    assert not result.dedupe_aliases
    assert not result.registry_issues
