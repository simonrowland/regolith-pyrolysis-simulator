"""Pins Chen et al. (2022) Table II readings and condition migration."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from tests.battery.test_migrate import _migrate_real_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT_NAME = "chen-2022-mgo-sio2-tio2-phase-equilibria.yaml"
EXTRACT = ROOT / "data" / "literature" / "extracts" / EXTRACT_NAME


def _observations_by_id(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species["observations"]
    }


def test_table_ii_render_pins_and_complete_row_migration(tmp_path: Path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = _observations_by_id(document)

    # Table II, 600 dpi renders: pp. 678-679 (PDF pages 3-4; indexes 2-3).
    pins = {
        "chen22_C1_liquid_tio2": (45.9, 678, 2, "C1, Liquid"),
        "chen22_C52_liquid_sio2": (58.0, 678, 2, "C52, Liquid"),
        "chen22_C53_cristobalite_tio2": (3.6, 678, 2, "C53, Cristobalite"),
        "chen22_C66_mgo_2tio2_mgo": (20.0, 678, 2, "C66, MgO·2TiO2"),
    }
    for observation_id, (value, folio, page_index, row) in pins.items():
        observation = rows[observation_id]
        assert observation["values"]["value"] == value
        assert observation["locator"]["published_page"] == folio
        assert observation["locator"]["pdf_page_index"] == page_index
        assert observation["locator"]["row"] == row

    assert sum(len(species["observations"]) for species in document["species"].values()) == 198
    assert {container: len(block["observations"]) for container, block in document["species"].items()} == {
        "SiO2": 96,
        "MgO": 54,
        "TiO2": 48,
    }
    containers = {
        observation["observation_id"]: container
        for container, block in document["species"].items()
        for observation in block["observations"]
    }
    expected_containers = {
        "chen22_C5_liquid_tio2": "TiO2",  # liquid with one coexisting solid
        "chen22_C73_liquid_tio2": "MgO",  # liquid with one coexisting MgO·SiO2 solid
        "chen22_C7_liquid_tio2": "SiO2",  # liquid in a three-phase invariant
        "chen22_C45_liquid_tio2": "SiO2",  # liquid with two solids
        "chen22_C65_mgo_2tio2_sio2": "TiO2",  # solid compound has more TiO2
        "chen22_C58_2mgo_sio2_mgo": "MgO",
    }
    assert {key: containers[key] for key in expected_containers} == expected_containers
    assert len(
        [
            observation
            for observation in rows.values()
            if isinstance(observation["values"]["value"], str)
        ]
    ) == 11

    figures = [
        row
        for row in document["species"]["SiO2"]["context"]
        if row["type"] == "figure_record"
    ]
    assert len(figures) == 6
    assert all(
        figure["values"]["digitisation"] == "deferred to a figure pass"
        and figure["values"]["numeric_points"] == "none"
        for figure in figures
    )

    result = _migrate_real_extract(tmp_path, EXTRACT_NAME)
    assert len(result.observations) == 198
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert sum(row.value.kind.value == "point" for row in result.observations.values()) == 187
    assert sum(row.value.kind.value == "unavailable" for row in result.observations.values()) == 11

    migrated_containers = {
        observation_id: container
        for container, block in document["species"].items()
        for observation_id in (
            row["observation_id"] for row in block["observations"]
        )
    }
    assemblages_by_sample: dict[str, set[str]] = {}
    for observation_id, migrated in result.observations.items():
        source_observation_id = observation_id.split("::", 1)[-1]
        conditions = migrated.point_conditions or {}
        assert migrated.identity.species.formula == migrated_containers[source_observation_id]
        temperature = conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.locator is not None
        assert temperature.state.value == Decimal("1773.15")
        for key in (
            "sample_id",
            "assemblage_id",
            "assemblage_as_published",
            "system_as_published",
            "atmosphere",
            "initial_composition_as_published",
            "phase_composition_as_published",
        ):
            assert key in conditions
            assert conditions[key].state.is_value
            assert conditions[key].locator is not None
        sample_id = conditions["sample_id"].state.value
        assemblage_id = conditions["assemblage_id"].state.value
        assert assemblage_id == f"chen22_{sample_id}"
        assemblages_by_sample.setdefault(sample_id, set()).add(assemblage_id)
        assert set(conditions["phase_composition_as_published"].state.value) == {
            "TiO2",
            "SiO2",
            "MgO",
        }
        assert set(conditions["initial_composition_as_published"].state.value) == {
            "TiO2",
            "SiO2",
            "MgO",
        }
    assert len(assemblages_by_sample) == 32
    assert all(len(assemblage_ids) == 1 for assemblage_ids in assemblages_by_sample.values())
