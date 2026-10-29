"""Migration pins for Shornikov et al. (2023), LPSC abstract 2806."""

from pathlib import Path

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2023-natural-cai-evaporation.yaml"


def test_shornikov_2023_observations_and_table_1_context_migrate() -> None:
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()

    result = migrator.result
    assert len(result.observations) == 16
    contexts = [
        context
        for work_contexts in result.context_by_work.values()
        for context in work_contexts
    ]
    table = next(
        context
        for context in contexts
        if context["observation_id"] == "shornikov_2023_table1_initial_compositions"
    )
    # Table 1 (printed p. 1) gives type A CaO = 36.2 wt% and MgO = 5.0 wt%.
    rows = table["values"]["rows"]
    assert rows[0]["type_A_wt_pct"] == 36.2
    assert rows[1]["type_A_wt_pct"] == 5.0
    assert not result.validation.hard_issues
