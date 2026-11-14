from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "van-kooten-2020-lunar-outgassing"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"


def _assert_quoted_rows_have_attributions(document: dict) -> None:
    observation = next(
        row
        for row in document["species"]["Zn"]["observations"]
        if row["observation_id"] == "van_kooten_2020_zinc_table1_quoted_rows"
    )
    values = observation["values"]
    assert values["evidence_class"] == "quoted_attributed"
    assert observation["attribution"] == (
        "Day et al. (2020b) and Kato et al. (2015), as named by Table 1 reference keys Day20 and Kato17."
    )
    for row in values["rows"]:
        expected = {
            "Day20": "Day et al. (2020b)",
            "Kato17": "Kato et al. (2015)",
        }[row["reference"]]
        assert row["evidence_class"] == "quoted_attributed"
        assert row["attribution"] == expected


def test_table1_prior_studies_keep_attributed_evidence_and_isolated_migration() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_quoted_rows_have_attributions(document)

    reverted = deepcopy(document)
    quoted = next(
        row
        for row in reverted["species"]["Zn"]["observations"]
        if row["observation_id"] == "van_kooten_2020_zinc_table1_quoted_rows"
    )
    del quoted["values"]["rows"][0]["attribution"]
    with pytest.raises(KeyError):
        _assert_quoted_rows_have_attributions(reverted)

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert not result.registry_issues
    assert result.validation is not None
    assert not result.validation.hard_issues
