from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery import migrate as battery_migrate
from simulator.battery.identity import profile_for
from tools import validate_literature_extracts

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = REPO_ROOT / "data/literature/extracts/lee-1978-mgo-cr2o3-langmuir-knudsen-vaporization.yaml"

# Full Table 1 transcription checked against the PDF render, physical page 5.
TABLE_1_RENDER_GRID = (
    ("ThO2", "2.01", "7.01", "6.0 × 10⁻¹", Decimal("0.60")),
    ("MgO", "7.02", "21.57", "1.7 × 10⁻¹", Decimal("0.17")),
    ("Y2O3", "9.83", "26.67", "8.4 × 10⁻²", Decimal("0.084")),
    ("Yb2O3", "10.38", "27.00", "7.8 × 10⁻²", Decimal("0.078")),
    ("MgCr2O4", "10.63", "29.17", "7.5 × 10⁻²", Decimal("0.075")),
    ("Cr2O3", "17.75", "46.82", "9.7 × 10⁻³", Decimal("0.0097")),
    ("Al2O3", "34.23", "79.53", "2.0 × 10⁻⁴", Decimal("0.00020")),
)


def _table_1_grid(document):
    alpha_by_species = {
        species: data["observations"][0]["values"]
        for species, data in document["species"].items()
        if data.get("observations")
    }
    refusal = next(
        context
        for context in document["species"]["MgO"]["context"]
        if context["observation_id"] == "lee_1978_table1_fit_parameters_schema_gap"
    )
    fits = {row["species"]: row for row in refusal["values"]["rows_as_printed"]}
    return tuple(
        (
            species,
            fits[species]["delta_S_star_minus_delta_S_degree_as_printed"],
            fits[species]["delta_H_star_minus_delta_H_degree_as_printed"],
            alpha_by_species[species]["alpha_as_printed"],
            Decimal(str(alpha_by_species[species]["alpha"])),
        )
        for species, *_ in TABLE_1_RENDER_GRID
    )


def test_full_table_1_render_grid_and_mutation_pin() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert validate_literature_extracts.validate_extract_document(
        document, expected_source_id=EXTRACT.stem
    ) == []
    assert _table_1_grid(document) == TABLE_1_RENDER_GRID
    assert len(_table_1_grid(document)) == 7

    mutated = deepcopy(document)
    mutated["species"]["MgCr2O4"]["observations"][0]["values"]["alpha"] = 0.078
    with pytest.raises(AssertionError):
        assert _table_1_grid(mutated) == TABLE_1_RENDER_GRID


def test_isolated_migration_preserves_rows_and_only_printed_conditions() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = battery_migrate.Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    observations = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in migrator.result.observations.values()
    }
    assert len(observations) == 7
    assert len(migrator.result.validation.hard_issues) == 0
    assert len(migrator.result.queue) == 14

    expected_by_id = {
        f"lee_1978_table1_{species.casefold()}_alpha": expected[-1]
        for species, *expected in TABLE_1_RENDER_GRID
    }
    own_measurement_species = {"mgo", "mgcr2o4", "cr2o3"}
    for observation_id, expected in expected_by_id.items():
        observation = observations[observation_id]
        assert observation.value.kind.value == "point"
        assert observation.value.point == expected
        temperature = observation.point_conditions["temperature_K"]
        assert temperature.state.is_unknown
        assert temperature.locator.page == 5
        assert temperature.locator.table == "1"
        oxygen = observation.point_conditions["fO2_log"]
        assert oxygen.state.is_unknown
        assert oxygen.locator.page == 5
        composition = observation.point_conditions["composition"]
        assert composition.state.is_unknown
        assert composition.locator.page == 5
        # The strict bound stays in its printed text channel; an exact Pa
        # point is absent from the migrated conditions and identity.
        assert "total_pressure_Pa" not in observation.point_conditions
        assert observation.identity.temperature_K.is_unknown
        assert observation.identity.fO2_Pa.is_unknown
        assert observation.identity.total_pressure_Pa.is_unknown
        assert observation.identity.composition.is_unknown

        required = profile_for(observation.identity).required
        assert {"temperature_K", "total_pressure_Pa", "fO2_Pa", "composition"} <= required

        if observation_id.split("_table1_", 1)[1].removesuffix("_alpha") in own_measurement_species:
            pressure = observation.point_conditions["total_pressure_as_printed"]
            assert pressure.state.is_value
            assert pressure.state.value == "<10^-5 torr"
            assert pressure.locator.page == 1
        else:
            assert "total_pressure_as_printed" not in observation.point_conditions

    for species, block in document["species"].items():
        row = block["observations"][0]
        conditions = row["point_conditions"]
        assert conditions["temperature_K"]["locator"]["page"] == 5
        assert conditions["fO2_log"]["state"]["tag"] == "unknown"
        assert conditions["composition"]["state"]["tag"] == "unknown"
        assert conditions["total_pressure_Pa"]["state"]["tag"] == "unknown"
        if species.casefold() in own_measurement_species:
            assert conditions["total_pressure_as_printed"]["state"]["value"] == (
                "<10^-5 torr"
            )
