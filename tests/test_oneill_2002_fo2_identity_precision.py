"""Keep the exact fO2 conversion input used by O'Neill et al. (2002)."""

from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/"
    / "oneill-2002-feo-activity-coefficients-cmas.yaml"
)


def _invalid_fo2_issues(migrator: Migrator):
    assert migrator.result.validation is not None
    return [
        issue
        for issue in migrator.result.validation.hard_issues
        if issue.reason.value == "invalid_identity"
        and issue.path.endswith(".fO2_Pa")
    ]


def _migrate(path: Path) -> Migrator:
    migrator = Migrator(root=ROOT)
    migrator._migrate_extract(path)
    migrator.finalize()
    return migrator


def test_oneill_rows_keep_valid_fo2_identity_and_float_mutation_fails(tmp_path: Path):
    migrator = _migrate(EXTRACT)
    assert not _invalid_fo2_issues(migrator)

    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    document["species"]["FeO"]["observations"][0]["values"][
        "oxygen_partial_pressure"
    ]["value"] = float(
        document["species"]["FeO"]["observations"][0]["values"][
            "oxygen_partial_pressure"
        ]["value"]
    )
    mutated = tmp_path / EXTRACT.name
    mutated.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    assert len(_invalid_fo2_issues(_migrate(mutated))) == 19
