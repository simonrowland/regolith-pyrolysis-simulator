from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from tests.battery.test_migrate import REPO_ROOT, _migrate_real_extract


def test_fe_neutral_table2_pressure_is_explicitly_model_derived(
    tmp_path: Path,
) -> None:
    source_id = "shornikov-2008-redox-oxide-evaporation"
    source = yaml.safe_load(
        (REPO_ROOT / "data" / "literature" / "extracts" / f"{source_id}.yaml")
        .read_text(encoding="utf-8")
    )
    fe = next(
        observation
        for observation in source["species"]["Fe_table2"]["observations"]
        if observation["observation_id"] == "shornikov_2008_t2_fe_neutral"
    )
    assert fe["values"]["method_class"] == "model_derived"
    assert fe["values"]["evidence_class"] == "model_derived"
    assert fe["values"]["model"] == (
        "Gibbs energy minimization using the activity data [15] and thermodynamic data [18, 19]."
    )

    result = _migrate_real_extract(tmp_path, f"{source_id}.yaml", write=False)
    rows = [
        observation
        for observation in result.observations.values()
        if observation.source_id == source_id
        and "shornikov_2008_t2_fe_neutral" in observation.observation_id
    ]
    assert len(rows) == 2
    assert result.validation is not None
    assert len(result.validation.hard_issues) == 0
    assert [row.value.point for row in rows] == [
        Decimal("0.014793450"),
        Decimal("0.043265775"),
    ]
