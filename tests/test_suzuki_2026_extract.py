from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
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


def _assert_migrated_yield(result) -> None:
    from simulator.battery.enums import EvidenceClass, Quantity

    matches = [
        observation
        for observation in result.observations.values()
        if observation.identity.quantity.value is Quantity.YIELD_FRACTION
    ]
    assert len(matches) == 1
    observation = matches[0]
    assert observation.value.point == Decimal("0.03")
    assert observation.evidence.class_.value is EvidenceClass.MEASURED_DIRECT
    assert observation.observation_id.endswith(
        "suzuki_2026_hematite_abstract_oxygen_removed"
    )
    assert observation.locator.pdf_page_index == 0
    assert observation.identity.species.formula == "Fe2O3"
    assert observation.identity.total_pressure_Pa.value == Decimal("0.00101325")
    assert observation.point_conditions["temperature_K"].state.value == Decimal("1473.15")
    experiment = result.experiments[observation.experiment_id]
    assert experiment.experiment_id.endswith("hematite-vacuum-heating-1200C")
    assert experiment.sample.mass_kg.state.value.point == Decimal("0.001")


def test_suzuki_2026_duration_conversion_provenance_is_migrated(tmp_path: Path) -> None:
    from tests.battery.test_migrate import _migrate_real_extract

    result = _migrate_real_extract(tmp_path, EXTRACT.name)
    experiment = next(
        experiment
        for experiment in result.experiments.values()
        if experiment.experiment_id.endswith("hematite-vacuum-heating-1200C")
    )
    duration = experiment.thermal_schedule.total_duration_s
    assert duration.state.value.point == Decimal("5400")
    assert duration.locator.pdf_page_index == 3
    assert duration.inference.relation == "h_to_s"
    parameters = dict(duration.inference.parameters)
    assert parameters["original"].state.value == Decimal("1.5")
    assert parameters["factor"].state.value == Decimal("3600")
    assert duration.inference.output_unit == "s"
    assert all(value.locator.pdf_page_index == 3 for value in parameters.values())


def test_suzuki_2026_migrated_yield_pins_initial_sample_basis_and_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.battery.test_migrate import _migrate_real_extract
    import simulator.battery.migrate as migrator

    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    raw = next(
        row
        for row in doc["species"]["Fe2O3"]["observations"]
        if row["observation_id"] == "suzuki_2026_hematite_abstract_oxygen_removed"
    )
    values = raw["values"]
    assert values["oxygen_mass_g_as_printed"] == 0.03
    assert values["initial_sample_mass_g_as_printed"] == 1
    assert values["normalization_as_printed"] == (
        "0.03 g O / 1 g initial hematite pellet = 3 mass percent."
    )

    result = _migrate_real_extract(tmp_path / "baseline", EXTRACT.name)
    _assert_migrated_yield(result)

    monkeypatch.setattr(migrator, "_percent_named_fraction_field", lambda *_args: False)
    mutated = _migrate_real_extract(tmp_path / "mutated", EXTRACT.name)
    with pytest.raises(AssertionError):
        _assert_migrated_yield(mutated)
