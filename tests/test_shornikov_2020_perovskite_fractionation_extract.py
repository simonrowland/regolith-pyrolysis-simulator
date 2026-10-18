"""Rendered-source pins and isolated migration for the perovskite fractionation extract."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2020-perovskite-chemical-fractionation.yaml"

if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import validate_literature_extracts as vle  # noqa: E402


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _context_rows(document: dict) -> list[dict]:
    return [
        row
        for species in document["species"].values()
        for row in species.get("context", [])
    ]


def test_table_constants_and_text_values_are_render_pinned_and_mutation_sensitive():
    document = _document()
    refusals = [
        item
        for row in _context_rows(document)
        if row["type"] == "refusal"
        for item in row["values"].get("refused", [])
        if item.get("reason") == "schema_gap_reaction_equilibrium_constant"
    ]
    expected_constants = {
        "Equation (3), reaction equilibrium constant": "7.267×10⁻⁹",
        "Equation (4), reaction equilibrium constant": "2.372×10⁻⁶",
        "Equation (5), reaction equilibrium constant": "3.573×10⁻⁴",
        "Equation (6), reaction equilibrium constant": "1.917×10⁻⁷",
        "Equation (7), reaction equilibrium constant": "5.351×10⁻¹⁰",
        "Equation (8), reaction equilibrium constant": "3.197×10⁻³",
        "Equation (9), reaction equilibrium constant": "2.092×10⁻⁸",
        "Equation (10), reaction equilibrium constant": "1.872×10⁻⁵",
        "Equation (11), reaction equilibrium constant": "8.551×10⁵",
        "Equation (12), reaction equilibrium constant": "1.225×10⁻⁷",
        "Equation (13), reaction equilibrium constant": "2.264×10⁻⁵",
        "Equation (14), reaction equilibrium constant": "3.054×10¹⁴",
    }
    assert {item["item"]: item["value_as_printed"] for item in refusals} == expected_constants

    figure_rows = [
        row for row in _context_rows(document) if row["type"] == "figure_record"
    ]
    assert len(figure_rows) == 6
    assert all(
        row["values"]["digitisation"] == "deferred to a figure pass"
        and "points" not in row["values"]
        for row in figure_rows
    )

    assert vle.check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []
    mutated = deepcopy(document)
    mutated["species"]["CaO"]["observations"][0]["values"][
        "pressure_atm_as_printed"
    ] = "9.99×10⁻⁵"
    errors = vle.check_all_fidelity_samples_match(
        mutated, label=f"{EXTRACT.stem}-mutated"
    )
    assert any("mismatch" in error for error in errors), errors


def test_text_observations_migrate_with_located_temperature_and_incomplete_composition():
    document = _document()
    migrator = Migrator(root=ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert result.validation is not None
    assert not result.validation.hard_issues
    assert not result.registry_issues

    rows = {
        row.observation_id.split("::", 1)[-1]: row
        for row in result.observations.values()
        if row.source_id == document["source_id"]
    }
    expected_ids = {
        "shornikov_2020_fractionation_cao_vapor_pressure_2278k",
        "shornikov_2020_fractionation_tio2_vapor_pressure_2278k",
        "shornikov_2020_fractionation_zhang_residual_melt_tio2",
        "shornikov_2020_fractionation_zhang_atomic_oxygen_pressure_2278k",
        "shornikov_2020_fractionation_zhang_weight_loss",
    }
    assert set(rows) == expected_ids
    for row in rows.values():
        assert row.identity.temperature_K.is_value
        temperature = row.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.locator is not None

    for observation_id in (
        "shornikov_2020_fractionation_zhang_residual_melt_tio2",
        "shornikov_2020_fractionation_zhang_weight_loss",
    ):
        composition = rows[observation_id].point_conditions["composition"]
        assert composition.state.is_unknown
        assert composition.locator is not None
