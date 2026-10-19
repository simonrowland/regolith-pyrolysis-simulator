"""Rendered Table 2 pins for Shornikov (2025)."""

from decimal import Decimal
from pathlib import Path

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2025-ca-al-inclusion-thermodynamics.yaml"


def test_table2_melting_temperatures_match_render() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    observations = {
        observation.observation_id.rsplit("::", 1)[-1]: observation
        for observation in migrator.result.observations.values()
        if "::shornikov_2025_table2_" in observation.observation_id
    }
    expected = {
        "shornikov_2025_table2_allibert_melting": Decimal("1875"),
        "shornikov_2025_table2_shornikov_melting": Decimal("1877"),
        "shornikov_2025_table2_zherbetsov_melting": Decimal("1877"),
    }
    assert set(observations) == set(expected)
    for observation_id, value in expected.items():
        observation = observations[observation_id]
        assert observation.value.point == value
        assert observation.locator.page == 5
        assert observation.locator.published_page == 814
        assert observation.locator.pdf_page_index == 4
        assert observation.locator.table == "2"
    assert not migrator.result.validation.hard_issues
