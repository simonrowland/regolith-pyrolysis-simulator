"""Schema check for the wanted-experiments registry (data/wanted_experiments.yaml).

Structure-only by design: the registry's content is judgment and prose; what the
test guards is that every entry stays SCOREABLE (the ranking is the product of
three declared axes) and stays inside the closed vocabularies, so the registry
cannot silently drift into unranked wishes.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REGISTRY = Path(__file__).resolve().parents[1] / "data" / "wanted_experiments.yaml"

CLAIM_CLASSES = {"ordinal", "ratio", "inventory", "cardinal"}
STATUSES = {"wanted", "partially_satisfied", "satisfied", "superseded"}
REQUIRED = {
    "id",
    "title",
    "claim_class",
    "question",
    "current_evidence",
    "acceptance",
    "feeds",
    "leverage",
    "uncertainty",
    "feasibility",
    "status",
    "results",
}


def _load():
    doc = yaml.safe_load(REGISTRY.read_text())
    assert doc["schema"] == "wanted_experiments.v1"
    experiments = doc["experiments"]
    assert isinstance(experiments, list) and experiments
    return experiments


def test_entries_are_complete_and_scoreable():
    for entry in _load():
        missing = REQUIRED - set(entry)
        assert not missing, f"{entry.get('id')}: missing {sorted(missing)}"
        for axis in ("leverage", "uncertainty", "feasibility"):
            value = entry[axis]
            assert isinstance(value, int) and 1 <= value <= 5, (
                f"{entry['id']}: {axis}={value!r} outside 1-5"
            )
        assert entry["claim_class"] in CLAIM_CLASSES, entry["id"]
        assert entry["status"] in STATUSES, entry["id"]
        assert isinstance(entry["feeds"], list) and entry["feeds"], entry["id"]
        assert isinstance(entry["results"], list), entry["id"]


def test_ids_are_unique_and_stable_format():
    ids = [e["id"] for e in _load()]
    assert len(ids) == len(set(ids)), "duplicate wanted-experiment ids"
    for identifier in ids:
        assert identifier.startswith("WE-") and identifier[3:].isdigit(), identifier


def test_satisfied_entries_carry_results():
    """A satisfied claim with no recorded result would be an unbacked assertion."""

    for entry in _load():
        if entry["status"] in {"satisfied", "partially_satisfied"}:
            assert entry["results"], (
                f"{entry['id']} is {entry['status']} but carries no results"
            )


def test_ranking_is_computable():
    """The registry's whole point: entries rank by the product of the axes."""

    ranked = sorted(
        _load(),
        key=lambda e: e["leverage"] * e["uncertainty"] * e["feasibility"],
        reverse=True,
    )
    scores = [
        e["leverage"] * e["uncertainty"] * e["feasibility"] for e in ranked
    ]
    assert all(1 <= s <= 125 for s in scores)
