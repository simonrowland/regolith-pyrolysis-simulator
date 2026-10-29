"""Migration pins for Shornikov (2001), Figures 2 and 3."""

from decimal import Decimal
from pathlib import Path

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2001-mgalumina-silica-vaporization.yaml"


def test_shornikov_2001_figure_observations_keep_printed_temperatures() -> None:
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()

    observations = {
        observation.observation_id.split("::")[1]: observation
        for observation in migrator.result.observations.values()
    }
    assert len(observations) == 14
    mg = observations["shornikov_2001_f2_mgo_ma_s_2050k"]
    alumina = observations["shornikov_2001_f2_al2o3_ca_s_1933k"]

    # Figure 2 (printed p. 327) captions curves 1 at 2050 K and 5 at 1933 K.
    assert mg.point_conditions["temperature_K"].state.value == Decimal("2050.0")
    assert alumina.point_conditions["temperature_K"].state.value == Decimal("1933.0")
    assert mg.locator.published_page == alumina.locator.published_page == 327
    assert mg.locator.figure == alumina.locator.figure == "2"
    assert not migrator.result.validation.hard_issues
