"""Regression pins and isolated migration coverage for Liu et al. (2023)."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import (
    Migrator,
    _kume_formula_unit_oxide,
    oxide_molar_mass,
    wt_pct_to_mole_fraction,
)
from simulator.state import MOLAR_MASS


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/liu-2023-chromium-oxide-activity.yaml"
GUO_EXTRACT = ROOT / "data/literature/extracts/guo-2021-mgo-activity-cmas-slag.yaml"
SLAG_NUMBER_BY_TABLE_ROW = [1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 4, 4]


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _assert_render_pins(document: dict) -> None:
    """Values below were read from the page renders: PDF pp. 2, 3 and 5."""
    table1 = document["species"]["Cr"]["context"][0]["values"]["rows"]
    assert len(table1) == 4
    assert table1[0]["composition_mass_percent_as_published"]["CaF2"] == "4.98"

    table2 = document["species"]["Cr"]["observations"][0]["values"]["points"]
    assert len(table2) == 24
    assert table2[0]["composition_wt_pct"]["MoO2"] == "32.39"

    cro15 = document["species"]["CrO1.5"]["observations"]
    assert len(cro15) == 12
    row1 = cro15[0]["values"]["points"][0]
    row4 = cro15[3]["values"]["points"][0]
    assert row1["activity"] == 1.5519
    assert row1["value_as_printed"] == "1.5519"
    assert row4["activity"] == 3.261
    assert row4["value_as_printed"] == "3.2610"


def test_table_rows_and_render_pins():
    document = _document()
    _assert_render_pins(document)

    table2 = document["species"]["Cr"]["observations"][0]["values"]["points"]
    assert Counter(point["row"] for point in table2) == {
        str(row): 2 for row in range(1, 13)
    }
    for index, point in enumerate(table2):
        assert int(point["slag_number_as_published"]) == SLAG_NUMBER_BY_TABLE_ROW[index // 2]

    for species in ("Cr", "CrO1.5", "CrO"):
        observations = [
            item
            for item in document["species"][species]["observations"]
            if item["observation_id"].startswith("liu23_t3_a_")
        ]
        assert len(observations) == 12
        assert [int(item["values"]["points"][0]["row"]) for item in observations] == list(
            range(1, 13)
        )
        for index, item in enumerate(observations):
            point = item["values"]["points"][0]
            assert int(point["slag_number_as_published"]) == SLAG_NUMBER_BY_TABLE_ROW[index]
            assert point["value"] == point["activity"]

    assert len(document["species"]["CrOx"]["observations"][0]["values"]["points"]) == 12


def test_render_pin_detects_a_mutated_cell():
    mutated = deepcopy(_document())
    point = mutated["species"]["CrO1.5"]["observations"][3]["values"]["points"][0]
    point["activity"] = 3.262
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)


def test_real_migrator_receives_each_rows_conditions_and_lineage():
    migrator = Migrator(root=ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    observations = tuple(
        observation
        for observation in result.observations.values()
        if observation.source_id == EXTRACT.stem
    )

    assert len(observations) == 72
    assert result.validation is not None
    assert not result.validation.hard_issues
    assert sum(o.value.kind.value == "unavailable" for o in observations) == 36
    assert Counter(o.value.kind.value for o in observations) == {
        "point": 36,
        "unavailable": 36,
    }

    for observation in observations:
        assert observation.point_conditions
        assert all(
            condition.locator is not None
            for condition in observation.point_conditions.values()
        )
        temperature = observation.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == Decimal("1823")
        assert temperature.locator is not None

    table3 = [o for o in observations if o.locator is not None and o.locator.table == "3"]
    assert len(table3) == 48
    admitted = [o for o in table3 if o.admission.status.value == "admitted"]
    assert len(admitted) == 36

    representatives = {
        "Cr": next(o for o in admitted if o.identity.species.formula == "Cr"),
        "CrO1.5": next(o for o in admitted if o.identity.species.formula == "CrO1.5"),
        "CrO": next(o for o in admitted if o.identity.species.formula == "CrO"),
    }
    for observation in representatives.values():
        identity = observation.identity
        assert identity.temperature_K.is_value
        assert identity.temperature_K.value == Decimal("1823")
        assert identity.fO2_Pa.is_value
        assert identity.fO2_Pa.value > 0
        assert identity.total_pressure_Pa.is_unknown
    assert representatives["Cr"].identity.composition.is_unknown
    assert representatives["Cr"].point_conditions["composition"].state.is_unknown
    assert (
        "Cu-Cr alloy is not tabulated"
        in representatives["Cr"].point_conditions["composition"].state.reason
    )
    for species in ("CrO1.5", "CrO"):
        composition = representatives[species].identity.composition
        assert composition.is_value
        axes = composition.value.as_map()
        assert axes["CaF2"] > 0
        assert axes["MoO2"] > 0
        assert axes["Cr2O3"] > 0
        if species == "CrO1.5":
            assert "CrO1.5" not in axes
        else:
            assert axes["CrO"] > 0

    for species in ("CrO1.5", "CrO"):
        rows = [
            o for o in admitted
            if o.identity.species.formula == species
        ]
        assert len(rows) == 12
        for observation in rows:
            assert len(observation.derived_from) == 1
            parent = result.observations[observation.derived_from[0]]
            assert parent.identity.species.formula == "Cr"
            assert parent.locator is not None
            parent_row = parent.observation_id.split("::")[1].rsplit("row", 1)[1]
            child_row = observation.observation_id.split("::")[1].rsplit("row", 1)[1]
            assert parent_row == child_row
            assert observation.derivation is not None
            assert observation.derivation.inputs == observation.derived_from

    bound = [
        o for o in admitted
        if o.identity.species.formula in {"CrO1.5", "CrO"}
    ]
    assert len(bound) == 24
    assert all(o.identity.composition.is_value for o in bound)


def test_row_local_slag_maps_bind_to_identity_and_point_conditions():
    migrator = Migrator(root=ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    observations = [
        observation
        for observation in migrator.result.observations.values()
        if observation.source_id == EXTRACT.stem
    ]
    table2 = [o for o in observations if o.locator and o.locator.table == "2"]
    assert len(table2) == 24
    assert all(o.identity.composition is not None for o in table2)
    assert all(o.identity.composition.is_value for o in table2)

    # Independent paper conversions from the reviewer's six-decimal pins.
    row_pins = {
        "1": {
            "MoO2": Decimal("0.187502"),
            "CaF2": Decimal("0.015748"),
            "Cr2O3": Decimal("0.004581"),
        },
        "4": {
            "MoO2": Decimal("0.172948"),
            "CaF2": Decimal("0.019316"),
            "Cr2O3": Decimal("0.001881"),
        },
    }
    for row, expected in row_pins.items():
        first_index = (int(row) - 1) * 2
        representative = table2[first_index]
        composition = representative.identity.composition.value
        assert composition.basis == "printed_oxides"
        axes = composition.as_map()
        assert {
            axis: axes[axis].quantize(Decimal("0.000001")) for axis in expected
        } == expected

    table3_oxides = [
        o for o in observations
        if o.locator and o.locator.table == "3"
        and o.identity.species.formula in {"CrO", "CrO1.5", "CrOx"}
    ]
    assert len(table3_oxides) == 36
    for observation in table3_oxides:
        identity_composition = observation.identity.composition
        point_composition = observation.point_conditions["composition"].state
        assert identity_composition is not None and identity_composition.is_value
        assert point_composition.is_value
        assert point_composition.value == identity_composition.value

    table3_acr = [
        o for o in observations
        if o.locator and o.locator.table == "3"
        and o.identity.species.formula == "Cr"
    ]
    assert len(table3_acr) == 12
    assert all(o.identity.composition.is_unknown for o in table3_acr)
    assert all(
        o.point_conditions["composition"].state.is_unknown for o in table3_acr
    )


def test_guo_table5_keeps_declared_mole_compositions():
    migrator = Migrator(root=ROOT)
    migrator._migrate_extract(GUO_EXTRACT)
    migrator.finalize()
    observations = [
        observation
        for observation in migrator.result.observations.values()
        if observation.source_id == GUO_EXTRACT.stem
        and observation.locator
        and observation.locator.table == "5"
        and observation.identity.species.formula == "MgO"
    ]
    source = yaml.safe_load(GUO_EXTRACT.read_text(encoding="utf-8"))
    declared_rows = source["species"]["MgO"]["observations"][0]["values"]["points"]
    assert len(observations) == len(declared_rows) == 8
    by_row = {
        observation.locator.note.split("Row ")[1].split(";")[0]: observation
        for observation in observations
    }
    for index, row in enumerate(declared_rows, start=1):
        composition = by_row[str(index)].identity.composition
        assert composition is not None and composition.is_value
        assert composition.value.basis == "printed_mole_fraction"
        assert composition.value.as_map() == {
            axis: Decimal(amount)
            for axis, amount in row["composition_mol"].items()
        }


def test_cr_oxide_formula_unit_mapping_and_component_mutation():
    # M(Cr2O3) is twice M(CrO1.5); wt% / M(Cr2O3) therefore yields canonical
    # Cr2O3 formula-unit moles for the printed CrO1.5 component.
    assert oxide_molar_mass("CrO1.5") == Decimal(str(MOLAR_MASS["Cr2O3"]))
    formula_unit_mass = 2 * (
        Decimal(str(MOLAR_MASS["Cr"]))
        + Decimal("1.5") * Decimal(str(MOLAR_MASS["O"]))
    )
    assert abs(Decimal(str(MOLAR_MASS["Cr2O3"])) - formula_unit_mass) < Decimal("1e-12")
    assert _kume_formula_unit_oxide("CrO1.5") == ("Cr2O3", Decimal("0.5"))
    composition = wt_pct_to_mole_fraction(
        {"CrO1.5": Decimal("10"), "SiO2": Decimal("90")}
    ).as_map()
    canonical_cr2o3_moles = Decimal("10") / oxide_molar_mass("CrO1.5")
    silica_moles = Decimal("90") / oxide_molar_mass("SiO2")
    assert composition["Cr2O3"] == canonical_cr2o3_moles / (
        canonical_cr2o3_moles + silica_moles
    )

    from simulator.battery import migrate as migrate_module

    removed = {
        name: migrate_module._COMPOSITION_COMPONENTS.pop(name)
        for name in ("CrO", "CrO1.5", "CaF2", "MoO2")
    }
    try:
        migrator = Migrator(root=ROOT)
        migrator._migrate_extract(EXTRACT)
        migrator.finalize()
        observations = [
            o for o in migrator.result.observations.values()
            if o.source_id == EXTRACT.stem and o.locator
            and o.locator.table == "3"
            and o.identity.species.formula in {"CrO1.5", "CrO"}
            and o.admission.status.value == "admitted"
        ]
        assert len(observations) == 24
        assert all(o.identity.composition.is_unknown for o in observations)
    finally:
        migrate_module._COMPOSITION_COMPONENTS.update(removed)
