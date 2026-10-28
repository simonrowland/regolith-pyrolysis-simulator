from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from simulator.battery import migrate as battery_migrate

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = REPO_ROOT / "data/literature/extracts/kroger-janetzko-kreitlow-1958-glass-formation-heat.yaml"

_RENDER_PINS = {
    "kroger_1958_direct_fourcault_detag": ("123.6", "cal/g", 222, None),
    "kroger_1958_direct_fourcault_delog_oxidising": ("125.6", "cal/g", 222, None),
    "kroger_1958_direct_libbey_owens_oxidising": ("111.8", "cal/g", 222, None),
    "kroger_1958_direct_green_bottle_table8a": ("113.80", "cal/g", 224, "8a"),
    "kroger_1958_direct_sulfatglas_I": ("209.5", "cal/g", 226, "12a"),
    "kroger_1958_direct_speiser_wirtschaftsglas": ("109.6", "cal/g", 226, "10"),
    "kroger_1958_solution_formation_libbey_reducing": ("12041", "kcal/100 kg glass", 224, "7"),
    "kroger_1958_solution_formation_rohrenglas_I": ("12760", "kcal/100 kg glass", 228, "16"),
}

_SULFATGLAS_KG_PINS = {
    "kroger_1958_direct_sulfatglas_I": {
        "SiO2": "67.85",
        "Na2O": "11.56",
        "Na2SO4": "2.60",
        "CaO": "15.20",
    },
    "kroger_1958_direct_sulfatglas_II": {
        "SiO2": "74.15",
        "Na2O": "12.43",
        "Na2SO4": "3.00",
        "CaO": "11.22",
    },
    "kroger_1958_direct_sulfatglas_III": {
        "SiO2": "68.34",
        "Na2O": "13.40",
        "Na2SO4": "3.50",
        "CaO": "13.76",
        "Al2O3·2SiO2": "3.56",
    },
}

_ROHR_II_ANALYSED = {
    "SiO2": "70.2",
    "Na2O": "12.5",
    "K2O": "1.76",
    "CaO": "5.78",
    "MgO": "3.42",
    "BaO": "1.55",
    "ZnO": "1.31",
    "R2O3": "3.38",
}
_ROHR_II_CALCULATED = {"Na2SO4": "0.14", "Sb2O3": "0.05"}


def _load():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations(document):
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species["observations"]
    }


def _render_pin_grid(document):
    observations = _observations(document)
    return {
        observation_id: (
            observations[observation_id]["values"]["value_as_printed"].get(
                "value", observations[observation_id]["values"]["value_as_printed"].get("value_as_printed")
            ),
            observations[observation_id]["values"]["value_as_printed"]["unit"],
            observations[observation_id]["locator"]["page"],
            observations[observation_id]["locator"].get("table"),
        )
        for observation_id in _RENDER_PINS
    }


def _sulfatglas_kg_grid(document):
    observations = _observations(document)
    return {
        observation_id: observations[observation_id]["point_conditions"]["printed_composition"]["state"]["value"]
        for observation_id in _SULFATGLAS_KG_PINS
    }


def test_render_checked_cells_and_complete_printed_row_count():
    document = _load()
    observations = _observations(document)
    assert len(observations) == 35
    assert _render_pin_grid(document) == _RENDER_PINS

    # Conflicting summaries are retained as printed, not reconciled by inference.
    green = observations["kroger_1958_direct_green_bottle_table8a"]["values"]
    assert green["value_as_printed"] == {"value": "113.80", "unit": "cal/g"}
    assert green["table16_summary_as_printed"]["value_as_printed"] == "10700"
    libbey = observations["kroger_1958_reduced_libbey_owens"]["values"]
    assert libbey["value_as_printed"]["value"] == "124.28"
    assert libbey["table7_as_printed"]["value_as_printed"] == "12428"
    assert libbey["table16_summary_as_printed"]["value_as_printed"] == "12420"


def test_render_pin_rejects_a_mutated_stored_cell():
    document = _load()
    mutated = deepcopy(document)
    _observations(mutated)["kroger_1958_direct_sulfatglas_I"]["values"]["value_as_printed"]["value"] = "209.6"
    assert _render_pin_grid(document) == _RENDER_PINS
    assert _render_pin_grid(mutated) != _RENDER_PINS


def test_sulfatglas_compositions_are_pinned_as_printed_kilogram_maps():
    document = _load()
    observations = _observations(document)
    assert _sulfatglas_kg_grid(document) == _SULFATGLAS_KG_PINS
    for observation_id, expected in _SULFATGLAS_KG_PINS.items():
        condition = observations[observation_id]["point_conditions"]["printed_composition"]
        assert condition["state"] == {"tag": "value", "value": expected}
        assert "kilograms" in condition["locator"]["note"]

    mutated = deepcopy(_load())
    for observation_id in _SULFATGLAS_KG_PINS:
        condition = _observations(mutated)[observation_id]["point_conditions"]["printed_composition"]
        condition["state"]["value"] = {
            "basis": "as printed",
            "amount_basis": "mass_percent",
            "components": list(_SULFATGLAS_KG_PINS[observation_id].items()),
        }
    assert _sulfatglas_kg_grid(mutated) != _SULFATGLAS_KG_PINS


def test_rohrenglas_ii_keeps_analysed_and_calculated_cells_separate():
    document = _load()
    observations = _observations(document)
    row = observations["kroger_1958_direct_rohrenglas_II"]
    analysed = row["point_conditions"]["printed_composition"]
    calculated = row["point_conditions"]["calculated_composition"]
    dashes = row["point_conditions"]["analysed_composition_dashes"]
    assert analysed["state"]["value"]["components"] == [
        [key, value] for key, value in _ROHR_II_ANALYSED.items()
    ]
    assert analysed["state"]["value"]["amount_basis"] == "mass_percent"
    assert calculated["state"]["value"] == _ROHR_II_CALCULATED
    assert "analysed Na2SO4 and Sb2O3 cells are —" in calculated["locator"]["note"]
    assert dashes["state"]["value"] == {"Na2SO4": "—", "Sb2O3": "—"}

    migrator = battery_migrate.Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    migrated = migrator.result.observations[
        f"{document['source_id']}::kroger_1958_direct_rohrenglas_II"
    ]
    migrated_analysed = migrated.point_conditions["printed_composition"]
    migrated_calculated = migrated.point_conditions["calculated_composition"]
    migrated_dashes = migrated.point_conditions["analysed_composition_dashes"]
    assert migrated_analysed.state.is_value
    assert migrated_analysed.state.value["amount_basis"] == "mass_percent"
    assert dict(migrated_analysed.state.value["components"]) == _ROHR_II_ANALYSED
    assert migrated_calculated.state.is_value
    assert {key: str(value) for key, value in migrated_calculated.state.value.items()} == _ROHR_II_CALCULATED
    assert "analysed Na2SO4 and Sb2O3 cells are —" in migrated_calculated.locator.note
    assert migrated_dashes.state.is_value
    assert {key: str(value) for key, value in migrated_dashes.state.value.items()} == {
        "Na2SO4": "—",
        "Sb2O3": "—",
    }


def test_speiser_table_10_locators_use_page_226_pdf_page_5():
    document = _load()
    row = _observations(document)["kroger_1958_direct_speiser_wirtschaftsglas"]
    assert row["locator"]["page"] == 226
    assert row["locator"]["pdf_page_index"] == 5
    for key in ("printed_composition", "quench_state"):
        locator = row["point_conditions"][key]["locator"]
        assert locator["page"] == 226
        assert locator["pdf_page_index"] == 5
        assert locator["table"] == "10"


def test_isolated_migration_preserves_unknown_quantity_conditions_and_lineage():
    document = _load()
    source_rows = _observations(document)
    migrator = battery_migrate.Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert len(result.observations) == 35
    assert len(result.validation.hard_issues) == 0
    assert _render_pin_grid(document) == _RENDER_PINS
    assert set(result.observations) == {
        f"{document['source_id']}::{observation_id}" for observation_id in source_rows
    }
    assert len([obs for obs in result.observations.values() if obs.value.kind.value == "unavailable"]) == 35
    assert all(obs.identity.quantity.is_unknown for obs in result.observations.values())
    assert all(obs.identity.species.phase.is_value for obs in result.observations.values())
    assert all(obs.identity.quantity.reason.startswith("unsupported quantity '") for obs in result.observations.values())
    assert all(obs.identity.total_pressure_Pa is None for obs in result.observations.values())

    for observation_id, (_value, _unit, page, table) in _RENDER_PINS.items():
        migrated = result.observations[f"{document['source_id']}::{observation_id}"]
        assert migrated.locator.page == page
        assert migrated.locator.table == table

    for observation_id, observation in result.observations.items():
        row_id = observation_id.split("::", 1)[1]
        source = source_rows[row_id]
        temperature = observation.point_conditions["temperature_C"]
        assert temperature.state.is_value and str(temperature.state.value) == "20"
        assert temperature.locator.page == source["point_conditions"]["temperature_C"]["locator"]["page"]
        assert "total_pressure_Pa" not in observation.point_conditions
        assert source["values"]["quantity"] in {
            "heat_of_glass_formation_from_batch",
            "heat_of_solution_as_printed",
        }
        if "printed_composition" in source["point_conditions"]:
            composition = observation.point_conditions["printed_composition"]
            assert composition.state.is_value
            assert composition.locator.page == source["point_conditions"]["printed_composition"]["locator"]["page"]
            if row_id in _SULFATGLAS_KG_PINS:
                assert {
                    key: str(value)
                    for key, value in composition.state.value.items()
                } == _SULFATGLAS_KG_PINS[row_id]
                assert "kilograms" in composition.locator.note
            quench = observation.point_conditions["quench_state"]
            assert quench.state.is_value and quench.state.value.value == "not_stated"
            assert quench.locator.page == source["point_conditions"]["quench_state"]["locator"]["page"]

    for row in source_rows.values():
        for parent_id in row.get("derived_from", []):
            assert parent_id in source_rows

    for observation in result.observations.values():
        for parent_id in observation.derived_from or ():
            assert parent_id in result.observations
