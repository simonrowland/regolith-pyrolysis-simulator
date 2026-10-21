"""Migration pins for Shornikov and Archakov (2002), Table 2."""

from decimal import Decimal
from pathlib import Path

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2002-alumina-silica-melt-thermodynamics.yaml"


def test_shornikov_2002_table_2_activity_values_migrate() -> None:
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()

    observations = tuple(migrator.result.observations.values())
    assert len(observations) == 22
    alumina = next(
        observation
        for observation in observations
        if "::shornikov_2002_t2_method_III_alumina_activity::T=1879:" in observation.observation_id
    )
    silica = next(
        observation
        for observation in observations
        if "::shornikov_2002_t2_method_IV_sio2_activity::T=2160:" in observation.observation_id
    )

    # Table 2 (printed p. 1058) gives 0.34 and 0.19 for these activities.
    assert alumina.value.point == Decimal("0.34")
    assert silica.value.point == Decimal("0.19")
    assert alumina.locator.published_page == silica.locator.published_page == 1058
    assert alumina.locator.table == silica.locator.table == "2"
    assert not migrator.result.validation.hard_issues
