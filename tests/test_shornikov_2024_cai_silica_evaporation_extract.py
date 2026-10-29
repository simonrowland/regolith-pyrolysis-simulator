"""Migration pins for Shornikov and Yakovlev (2024), LPI abstract 6197."""

from pathlib import Path

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2024-cai-silica-evaporation.yaml"


def test_shornikov_2024_observations_and_activity_comparison_migrate() -> None:
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()

    result = migrator.result
    assert len(result.observations) == 16
    comparison = next(
        context
        for work_contexts in result.context_by_work.values()
        for context in work_contexts
        if context["observation_id"] == "shornikov_2024_text_activity_comparison"
    )
    # Printed p. 6197 reports 10^5 and 10^11 times lower activities at 2500 K.
    factors = comparison["values"]["activity_factors_lower_than_gehlenite"]
    assert comparison["values"]["temperature_K"] == 2500
    assert factors == {"anorthite": 100_000, "grossular": 100_000_000_000}
    assert not result.validation.hard_issues
