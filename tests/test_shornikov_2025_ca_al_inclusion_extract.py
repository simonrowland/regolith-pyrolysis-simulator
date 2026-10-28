"""Rendered Table 2 pins for Shornikov (2025)."""

from decimal import Decimal
from pathlib import Path

import yaml

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


def test_table6_spinel_decomposition_temperatures_match_render() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    observations = {
        observation.observation_id.rsplit("::", 1)[-1]: observation
        for observation in migrator.result.observations.values()
        if "::shornikov_2025_table6_" in observation.observation_id
    }
    expected = {
        "shornikov_2025_table6_richet_decomposition": Decimal("2408"),
        "shornikov_2025_table6_shornikov_decomposition": Decimal("2420"),
    }
    assert set(observations) == set(expected)
    for observation_id, temperature in expected.items():
        observation = observations[observation_id]
        assert observation.identity.species.formula == "MgAl2O4"
        assert observation.value.point == temperature
        assert observation.locator.page == 10
        assert observation.locator.published_page == 819
        assert observation.locator.pdf_page_index == 9
        assert observation.locator.table == "6"
        assert observation.uncertainty.kind.value == "none"
    assert not migrator.result.validation.hard_issues

    extract = yaml.safe_load(EXTRACT.read_text())
    rows = {
        row["observation_id"]: row["values"]
        for row in extract["species"]["MgAl2O4"]["observations"]
    }
    assert rows["shornikov_2025_table6_richet_decomposition"]["delta_H_m_as_printed"] == "53.50 ± 5.50"
    assert rows["shornikov_2025_table6_richet_decomposition"]["delta_S_m_as_printed"] == "22.22 ± 2.28"
    assert rows["shornikov_2025_table6_shornikov_decomposition"]["delta_H_m_as_printed"] == "55.81 ± 4.62"
    assert rows["shornikov_2025_table6_shornikov_decomposition"]["delta_S_m_as_printed"] == "23.06 ± 1.73"
