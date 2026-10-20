from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/schreiber-1977-tetravalent-cr-diopside.yaml"
OBSERVATION_ID = "schreiber_1977_crvi_fraction_blue_diopside_melt"
EXPECTED_RANGE = ("10", "20")


def _assert_render_pin(document: dict) -> None:
    row = document["species"]["Cr"]["observations"][0]
    assert row["observation_id"] == OBSERVATION_ID
    point = row["values"]["points"][0]
    assert tuple(point["range"]) == EXPECTED_RANGE
    assert point["approximate"] is True
    assert point["value_as_published"] == "about 10-20 percent"
    assert row["locator"] == {
        "page": 3,
        "pdf_page_index": 2,
        "published_page": 524,
        "section": "Results and discussion",
    }


def test_textual_crvi_range_and_fidelity_pin() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_render_pin(document)
    assert check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []


def test_render_pin_fails_after_range_mutation() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    document["species"]["Cr"]["observations"][0]["values"]["points"][0]["range"][1] = "21"
    with pytest.raises(AssertionError):
        _assert_render_pin(document)


def test_isolated_migration_keeps_cr_species_and_interval() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert not result.validation.hard_issues
    assert len(result.observations) == 1
    observation = next(iter(result.observations.values()))
    assert observation.identity.species.formula == "Cr"
    assert observation.identity.quantity.value.value == "valence_ratio"
    assert observation.value.kind.value == "interval"
    assert str(observation.value.interval_low) == EXPECTED_RANGE[0]
    assert str(observation.value.interval_high) == EXPECTED_RANGE[1]
    assert observation.point_conditions["temperature_K"].state.is_unknown
    assert observation.point_conditions["fO2_log"].state.is_unknown
    assert observation.point_conditions["composition"].state.is_unknown
    assert observation.point_conditions["quench_state"].state.is_value

