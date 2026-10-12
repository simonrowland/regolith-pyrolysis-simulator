from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data/literature/extracts/suzuki-2026-vacuum-oxygen-extraction.yaml"
)


def _assert_abstract_oxygen_observation(doc: dict) -> None:
    rows = [
        row
        for row in doc["species"]["Fe2O3"]["observations"]
        if row["observation_id"] == "suzuki_2026_hematite_abstract_oxygen_removed"
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["locator"]["pdf_page_index"] == 0
    assert row["type"] == "rate_series"
    assert row["values"]["quantity"] == "measured_oxygen_yield"
    assert row["values"]["oxygen_mass_g_as_printed"] == 0.03
    assert row["values"]["initial_sample_mass_g_as_printed"] == 1
    assert row["values"]["mass_yield_percent"] == 3
    assert row["values"]["normalization_as_printed"] == (
        "0.03 g O / 1 g initial hematite pellet = 3 mass percent."
    )
    assert row["values"]["T_C"] == 1200
    assert row["values"]["total_pressure_Pa"] == 0.00101325
    assert row["values"]["total_pressure_as_printed"] == "10^-8 atm"
    assert row["values"]["method_class"] == "measured_direct"


def test_suzuki_2026_abstract_oxygen_observation_is_pinned_and_mutation_fails() -> None:
    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_abstract_oxygen_observation(doc)

    mutated = deepcopy(doc)
    row = next(
        row
        for row in mutated["species"]["Fe2O3"]["observations"]
        if row["observation_id"] == "suzuki_2026_hematite_abstract_oxygen_removed"
    )
    row["values"]["oxygen_mass_g_as_printed"] = 0.04
    with pytest.raises(AssertionError):
        _assert_abstract_oxygen_observation(mutated)


def test_suzuki_2026_emf_pressure_range_is_measured_context() -> None:
    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = [
        row
        for row in doc["species"]["O2"]["context"]
        if row["observation_id"] == "suzuki_2026_hematite_emf_pO2_range"
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["locator"]["pdf_page_index"] == 4
    assert row["locator"]["equation"] == "2"
    assert row["values"]["range"] == [1.1e-12, 1.4e-12]
    assert row["values"]["range_as_printed"] == "1.1–1.4 × 10−12 atm"
    assert row["values"]["method_class"] == "measured_direct"
    assert row["d-126_reason"] == "migrator has no p_partial range mapping; b-867"
    assert all(
        observation["observation_id"] != "suzuki_2026_hematite_emf_pO2_range"
        for observation in doc["species"]["O2"]["observations"]
    )
