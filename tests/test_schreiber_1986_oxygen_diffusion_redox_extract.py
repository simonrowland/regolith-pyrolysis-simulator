from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/schreiber-1986-oxygen-diffusion-redox.yaml"


def _assert_diffusion_and_figure_records(document: dict) -> None:
    oxygen_context = document["species"]["O"]["context"]
    coefficient = next(row for row in oxygen_context if row["observation_id"] == "schreiber_1986_fitted_chemical_oxygen_diffusion_coefficient")
    values = coefficient["values"]
    assert values["value_as_prose"] == "about 9 x 10^-6"
    assert values["approximate"] is True
    assert values["value_as_figure2_label"] == "9 x 10^-6"
    assert values["conditions_as_published"]["temperature"] == "1400 °C"
    fig1 = next(row for row in oxygen_context if row["observation_id"] == "schreiber_1986_figure1_oxygen_diffusion_compensation")
    assert fig1["values"]["numeric_points"] == "none"
    assert [series["marker_count"] for series in fig1["values"]["series"]] == [24, 15, 0]
    cerium_context = document["species"]["Ce"]["context"]
    fig2 = next(row for row in cerium_context if row["observation_id"] == "schreiber_1986_figure2_cerium_redox_kinetics")
    assert fig2["values"]["quantity"] == "valence_ratio"
    assert fig2["values"]["oxygen_fugacity_as_published"].startswith("Initial equilibration: about 10^-4 atm")
    assert fig2["values"]["composition_as_published"].endswith("Both printed values are retained.")
    assert fig2["values"]["numeric_points"] == "none"
    assert fig2["values"]["series"][0]["marker_count"] == 9


def test_printed_coefficient_and_deferred_figure_series_are_retained() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_diffusion_and_figure_records(document)


def test_coefficient_mutation_breaks_printed_value_pin() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    coefficient = document["species"]["O"]["context"][0]
    coefficient["values"]["value_as_prose"] = "about 8 x 10^-6"
    with pytest.raises(AssertionError):
        _assert_diffusion_and_figure_records(document)


def test_isolated_migration_has_no_scored_figure_values_or_hard_issues() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    assert len(migrator.result.observations) == 0
