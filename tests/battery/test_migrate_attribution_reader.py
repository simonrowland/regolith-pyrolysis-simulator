"""PIN: migrator must prefer values.attribution over quote for Evidence.attribution."""

from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.migrate import migrate
from tests.battery.test_migrate import FIXTURE_EXTRACT, _write_min_tree


AUTHOR = "Hubbard et al. (1972), separate aliquot, Table 2 dagger footnote"
QUOTE = "K | 14163 | Initial† | 4840"


def test_migrator_prefers_values_attribution_over_quote(tmp_path: Path) -> None:
    """Red on green: Evidence.attribution must equal values.attribution, not quote."""
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    row = extract["species"]["Na"]["observations"][0]
    row["observation_id"] = "attributed_obs"
    row["quote"] = QUOTE
    row["values"]["attribution"] = AUTHOR
    assert AUTHOR != QUOTE

    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    matching = [
        obs
        for oid, obs in result.observations.items()
        if "::attributed_obs" in oid or oid.endswith("::attributed_obs")
    ]
    assert matching, "expected migrated observations for attributed_obs"
    for obs in matching:
        assert obs.evidence.attribution == AUTHOR
