from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator
from simulator.battery.records import ValueKind

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = REPO_ROOT / "data/literature/extracts/rb86-rogez-etal-1983.yaml"

# Table VI values transcribed from the render of PDF page 9 (published p. 126).
TABLE_VI_RENDER_GRID = (
    ("0", "1", "0"),
    ("0.1", "0,847", "0,010"),
    ("0.2", "0,674", "0,038"),
    ("0.3", "0,513", "0,086"),
    ("0.4", "0,380", "0,152"),
    ("0.5", "0,253", "0,250"),
    ("0.6", "0,152", "0,379"),
    ("0.7", "0,079", "0,537"),
    ("0.8", "0,035", "0,706"),
    ("0.9", "0,010", "0,871"),
    ("1", "0", "1"),
)

TABLE_V_RENDER_GRID = (
    ("0,003", "0,366"),
    ("0,025", "-0,096"),
    ("0,1", "0,637"),
    ("0,3", "1,807"),
    ("0,5", "2,840"),
    ("0,6", "3,434"),
    ("0,7", "3,752"),
    ("0,9", "5,242"),
    ("0,975", "6,496"),
    ("0,997", "5,113"),
)


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _context(document, table):
    return next(
        row
        for row in document["species"]["Na2Al2Si6O16"]["context"]
        if row["locator"]["table"] == table
    )


def _activity_grid(document):
    rows = {}
    for formula, bundle in document["species"].items():
        for observation in bundle["observations"]:
            if observation.get("type") != "activity_coefficient":
                continue
            values = observation["values"]
            x_or = values["composition_mol"]["K2Al2Si6O16"]
            rows.setdefault(x_or, {})[formula] = values["activity_as_printed"]
    return tuple(
        (x_or, cells["Na2Al2Si6O16"], cells["K2Al2Si6O16"])
        for x_or, cells in sorted(rows.items(), key=lambda item: Decimal(item[0]))
    )


def _unprinted_pressure_absent(observations):
    return all(
        "total_pressure_Pa" not in observation.point_conditions
        for observation in observations
    )


def test_render_grids_and_printed_table_row_counts():
    document = _document()
    assert _activity_grid(document) == TABLE_VI_RENDER_GRID
    assert all(
        observation["locator"]["page"] == 9
        for bundle in document["species"].values()
        for observation in bundle["observations"]
        if observation.get("type") == "activity_coefficient"
    )

    table_v = _context(document, "V")
    table_v_grid = tuple(
        (
            row["xAb_as_printed"],
            row["ln_I23_squared_xOr_over_I39_squared_xAb_as_printed"],
        )
        for row in table_v["values"]["rows"]
    )
    assert table_v_grid == TABLE_V_RENDER_GRID
    assert table_v["locator"]["page"] == 8
    assert _context(document, "VI")["locator"]["page"] == 9

    expected_rows = {"I": 18, "II": 7, "III": 6, "IV": 6, "V": 10, "VI": 11}
    for table, row_count in expected_rows.items():
        assert len(_context(document, table)["values"]["rows"]) == row_count
    table_iv = _context(document, "IV")
    assert table_iv["values"]["rows"][-1]["mixing_enthalpy_plusminus_width_kJ_mol_as_printed"] == "0"
    assert table_iv["values"]["uncertainty_notice"] == "confidence level not stated"
    assert len(_context(document, "Annexe")["values"]["rows"]) == 318

    addendum = _context(document, "Addendum")
    assert addendum["locator"] == {
        "page": 11,
        "pdf_page_index": 10,
        "published_page": 128,
        "table": "Addendum",
        "section": "formula-unit convention",
    }
    assert addendum["values"]["calorimetry_component_basis"] == (
        "NaAlSi3O8 / KAlSi3O8"
    )
    assert addendum["values"]["spectrometry_component_basis"] == (
        "Na2O, Al2O3, 6SiO2 / K2O, Al2O3, 6SiO2"
    )
    assert addendum["values"]["spectrometry_formula_units"] == {
        "albite": "Na2Al2Si6O16",
        "orthose": "K2Al2Si6O16",
    }

    mutated = deepcopy(document)
    first_activity = mutated["species"]["Na2Al2Si6O16"]["observations"][0]
    first_activity["values"]["activity_as_printed"] = "0,999"
    assert _activity_grid(mutated) != TABLE_VI_RENDER_GRID


def test_isolated_migration_maps_printed_conditions_and_omits_unprinted_pressure():
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    observations = list(result.observations.values())
    assert len(observations) == 22
    assert result.validation.hard_issues == ()
    assert result.queue == []
    assert all(
        observation.value.kind is not ValueKind.UNAVAILABLE
        for observation in observations
    )

    expected_species_and_basis = {
        "c02": ("Na2Al2Si6O16", "Na2O, Al2O3, 6SiO2"),
        "c03": ("K2Al2Si6O16", "K2O, Al2O3, 6SiO2"),
    }
    for observation in observations:
        expected_species, expected_basis = expected_species_and_basis[
            observation.observation_id[-3:]
        ]
        assert observation.identity.species.formula == expected_species
        assert observation.identity.reference_state.is_value
        assert observation.identity.reference_state.value.endmember.formula == expected_species
        assert observation.identity.reference_state.value.component_basis == expected_basis
        assert set(observation.identity.composition.value.as_map()) == {
            "Na2Al2Si6O16", "K2Al2Si6O16"
        }

    for observation in observations:
        assert observation.identity.temperature_K.is_value
        assert observation.identity.temperature_K.value == Decimal("1300")
        assert observation.identity.composition.is_value
        assert observation.identity.total_pressure_Pa.is_unknown
        assert observation.identity.fO2_Pa.is_unknown
        assert observation.point_conditions["temperature_K"].state.is_value
        assert observation.point_conditions["composition"].state.is_value
        assert observation.point_conditions["fO2_Pa"].state.is_unknown
    assert _unprinted_pressure_absent(observations)

    mutated = deepcopy(observations)
    first = mutated[0]
    mutated[0] = replace(
        first,
        point_conditions={
            **first.point_conditions,
            "total_pressure_Pa": first.point_conditions["fO2_Pa"],
        },
    )
    with pytest.raises(AssertionError):
        assert _unprinted_pressure_absent(mutated)
