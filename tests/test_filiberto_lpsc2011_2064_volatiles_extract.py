"""Pins prior-literature volatile abundances retained as context."""

from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/filiberto-lpsc2011-2064-volatiles.yaml"
SOURCE_ID = "filiberto-lpsc2011-2064-volatiles"
EXPECTED_GENERATORS = {
    "filiberto_2011_quoted_lunar_volatile_abundances": "cited literature [5, 7, 9]",
    "filiberto_2011_quoted_martian_volatile_abundances": "cited literature [3, 18]",
}


def test_cited_planetary_abundances_remain_context_only() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = {
        row["observation_id"]: row
        for row in document["species"]["planetary_volatile_abundances"]["context"]
    }

    for observation_id, generator in EXPECTED_GENERATORS.items():
        row = rows[observation_id]
        assert row["values"]["method_class"] == "quoted_attributed"
        assert row["values"]["generator"] == generator


def test_isolated_migration_has_no_hard_issues() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(
        ROOT,
        EXTRACT,
        index={SOURCE_ID: dict(document["source"], source_id=SOURCE_ID)},
        aliases={},
    )
    migrator.finalize()

    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()
