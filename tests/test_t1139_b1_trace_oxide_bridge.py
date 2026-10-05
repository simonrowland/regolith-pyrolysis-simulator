"""Trace-element ledger bridge: oxide parents, coverage, mass closure."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.accounting.formulas import parse_formula
from simulator.feedstock_composition import (
    bridge_trace_element_keys,
    feedstock_trace_oxide_bridge,
    normalized_feedstock_component_masses_kg,
    trace_element_disposition,
)
from simulator.trace_oxide_parents import (
    LIQUID_PARENT_OXIDE,
    SIDEROPHILE_IN_METAL_SCOPE_GAP,
    ledger_component_key,
)
from tests.test_t1139_b1_ledger_pins import B1_FEEDSTOCKS


ROOT = Path(__file__).resolve().parents[1]
FEEDSTOCKS = yaml.safe_load((ROOT / "data" / "feedstocks.yaml").read_text())
MASS_KG = 1000.0


def test_oxide_parent_keeps_metal_atoms_and_records_added_oxygen() -> None:
    element_mass_kg = 0.005
    result = bridge_trace_element_keys({"Ga": element_mass_kg, "SiO2": 10.0})
    assert [note.element for note in result.notes] == ["Ga"]
    note = result.notes[0]
    assert note.parent == LIQUID_PARENT_OXIDE["Ga"]
    assert note.parent == ledger_component_key(f"{note.parent}(l)")
    assert "Ga" not in result.masses_kg
    assert result.masses_kg["SiO2"] == pytest.approx(10.0)

    element = parse_formula("Ga")
    parent = parse_formula(note.parent)
    oxygen = parse_formula("O")
    metal_before = element_mass_kg / element.molar_mass_kg_per_mol()
    parent_mol = note.parent_mass_kg / parent.molar_mass_kg_per_mol()
    assert parent_mol * float(parent.elements["Ga"]) == pytest.approx(metal_before)
    oxygen_mol = parent_mol * float(parent.elements["O"])
    assert note.added_o_kg == pytest.approx(
        oxygen_mol * oxygen.molar_mass_kg_per_mol()
    )
    assert note.parent_mass_kg == pytest.approx(element_mass_kg + note.added_o_kg)
    assert result.coverage == ()


def test_existing_parent_oxide_receives_the_converted_mass() -> None:
    result = bridge_trace_element_keys({"Ga": 0.005, "Ga2O3": 1.0})
    note = result.notes[0]
    assert "Ga" not in result.masses_kg
    assert result.masses_kg["Ga2O3"] == pytest.approx(1.0 + note.parent_mass_kg)


def test_elements_without_an_oxide_parent_stay_and_are_covered() -> None:
    result = bridge_trace_element_keys(
        {"F": 0.003, "Cl": 0.002, "S": 0.01, "Se": 0.004, "Ag": 0.001, "Al2O3": 4.0}
    )
    assert result.notes == ()
    dispositions = {row.element: row.disposition for row in result.coverage}
    assert dispositions == {
        "F": "no_oxide_parent",
        "Cl": "no_oxide_parent",
        "S": "no_oxide_parent",
        "Se": "no_oxide_parent",
        "Ag": "no_oxide_parent",
    }
    assert result.masses_kg["F"] == pytest.approx(0.003)
    assert result.masses_kg["S"] == pytest.approx(0.01)
    assert result.masses_kg["Ag"] == pytest.approx(0.001)
    assert result.masses_kg["Al2O3"] == pytest.approx(4.0)


def test_unparsed_component_keys_are_left_in_place() -> None:
    result = bridge_trace_element_keys({"organic_tar": 1.25, "Ga": 0.005})
    assert result.masses_kg["organic_tar"] == pytest.approx(1.25)
    assert "Ga" not in result.masses_kg
    assert "Ga2O3" in result.masses_kg


def test_siderophiles_stay_element_keys_and_are_flagged() -> None:
    masses = {"Ni": 0.02, "Co": 0.01, "Ir": 0.001, "Fe": 3.0, "SiO2": 5.0}
    result = bridge_trace_element_keys(masses)
    assert result.notes == ()
    dispositions = {row.element: row.disposition for row in result.coverage}
    for element in ("Ni", "Co", "Ir"):
        assert dispositions[element] == "siderophile_in_metal_scope_gap"
        assert result.masses_kg[element] == pytest.approx(masses[element])
        assert f"{element}O" not in result.masses_kg
    assert dispositions["Fe"] == "no_oxide_parent"
    assert result.masses_kg["Fe"] == pytest.approx(3.0)
    assert trace_element_disposition("Au") == "siderophile_in_metal_scope_gap"
    assert trace_element_disposition("Ga") == "oxide_parent"
    assert trace_element_disposition("F") == "no_oxide_parent"


def test_normalizer_keeps_the_batch_total_after_adding_oxygen() -> None:
    feedstock = {
        "composition_wt_pct": {"Ga": 0.0005, "SiO2": 99.9895, "F": 0.01}
    }
    bridged = feedstock_trace_oxide_bridge(feedstock, MASS_KG)
    note = bridged.notes[0]
    assert note.element == "Ga"
    pre_scale_total = sum(bridged.masses_kg.values())
    masses = normalized_feedstock_component_masses_kg(feedstock, MASS_KG)
    assert sum(masses.values()) == pytest.approx(MASS_KG)
    scale = MASS_KG / pre_scale_total
    assert masses["Ga2O3"] == pytest.approx(note.parent_mass_kg * scale)
    assert masses["F"] == pytest.approx(bridged.masses_kg["F"] * scale)
    assert "Ga" not in masses
    retained_o_kg = note.added_o_kg * scale
    assert retained_o_kg > 0.0
    assert retained_o_kg < note.added_o_kg


@pytest.mark.parametrize("feedstock_id", B1_FEEDSTOCKS)
def test_every_manifest_parent_exists_after_load(feedstock_id: str) -> None:
    feedstock = FEEDSTOCKS[feedstock_id]
    composition = feedstock.get("composition_wt_pct") or {}
    masses = normalized_feedstock_component_masses_kg(feedstock, MASS_KG)
    bridged = feedstock_trace_oxide_bridge(feedstock, MASS_KG)
    notes = {note.element: note for note in bridged.notes}
    coverage = {row.element: row.disposition for row in bridged.coverage}
    assert sum(masses.values()) == pytest.approx(MASS_KG, abs=1e-9)
    for element, parent in LIQUID_PARENT_OXIDE.items():
        if element not in composition:
            continue
        assert parent in masses, (feedstock_id, parent)
        assert element not in masses, (feedstock_id, element)
        assert notes[element].parent == ledger_component_key(parent)
        assert notes[element].added_o_kg > 0.0
    for element in SIDEROPHILE_IN_METAL_SCOPE_GAP:
        if element not in composition:
            continue
        assert coverage[element] == "siderophile_in_metal_scope_gap"
        assert element in masses
        assert masses[element] > 0.0
