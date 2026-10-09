"""Oxide assignment from a formula token or an oxide weight percent."""
from __future__ import annotations

from collections import defaultdict

import pytest

from simulator.accounting.formulas import (
    ATOMIC_WEIGHTS_G_PER_MOL,
    parse_formula,
)
from simulator.accounting.oxide_assignment import (
    REASON_MASS_UNCLOSED,
    REASON_OXIDATION_AMBIGUOUS,
    REASON_UNPARSED_TOKEN,
    assign_phase_oxides,
)
from simulator.melt_backend.alphamelts import AlphaMELTSBackend
from tests.test_alphamelts_backend import (
    _parse_subprocess_fixture,
    _system_main_fixture,
)


def _element_moles(oxide_moles: dict[str, float]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for oxide, amount in oxide_moles.items():
        for element, count in parse_formula(oxide).elements.items():
            totals[element] += float(count) * float(amount)
    return totals


def test_annotated_olivine_formula_closes_elements_and_mass():
    mass_kg = 0.04
    token = "(Mg0.8Fe''0.2)2SiO4"
    assignment = assign_phase_oxides([{
        "phase": "olivine",
        "mass_kg": mass_kg,
        "formula": token,
    }])

    assert assignment.refusals == ()
    oxides = assignment.species_mol["olivine"]
    # Two formula units of the site occupancies Mg0.8 Fe0.2, plus SiO4.
    # These counts are the stoichiometry, not the converter's scaled text.
    occupancy = 2.0
    per_formula = {
        "Mg": 0.8 * occupancy,
        "Fe": 0.2 * occupancy,
        "Si": 1.0,
        "O": 4.0,
    }
    molar_mass_kg = sum(
        ATOMIC_WEIGHTS_G_PER_MOL[element] * count
        for element, count in per_formula.items()
    ) / 1000.0
    formula_moles = mass_kg / molar_mass_kg
    got = _element_moles(oxides)
    for element, count in per_formula.items():
        assert got[element] == pytest.approx(count * formula_moles)
    assert sum(assignment.species_kg["olivine"].values()) == pytest.approx(mass_kg)
    assert oxides["FeO"] == pytest.approx(0.2 * occupancy * formula_moles)
    assert "Fe2O3" not in oxides


def test_engine_reported_iron_split_closes():
    mass_kg = 0.02
    assignment = assign_phase_oxides([{
        "phase": "spinel",
        "mass_kg": mass_kg,
        "formula": "Fe'''2Fe''O4",
    }])

    assert assignment.refusals == ()
    oxides = assignment.species_mol["spinel"]
    formula = parse_formula("Fe3O4")
    formula_moles = mass_kg / formula.molar_mass_kg_per_mol()
    got = _element_moles(oxides)
    for element, count in formula.elements.items():
        assert got[element] == pytest.approx(float(count) * formula_moles)
    assert oxides["FeO"] == pytest.approx(formula_moles)
    assert oxides["Fe2O3"] == pytest.approx(formula_moles)
    assert sum(assignment.species_kg["spinel"].values()) == pytest.approx(mass_kg)


def test_bare_fayalite_assigns_all_feo():
    assignment = assign_phase_oxides([{
        "phase": "olivine",
        "mass_kg": 0.01,
        "formula": "Fe2SiO4",
    }])

    assert assignment.refusals == ()
    assert "FeO" in assignment.species_mol["olivine"]
    assert "Fe2O3" not in assignment.species_mol["olivine"]


def test_unannotated_magnetite_is_refused():
    assignment = assign_phase_oxides([{
        "phase": "spinel",
        "mass_kg": 1.0,
        "formula": "Fe3O4",
    }])

    assert "spinel" not in assignment.species_mol
    assert len(assignment.refusals) == 1
    refusal = assignment.refusals[0]
    assert refusal.phase == "spinel"
    assert refusal.reason == REASON_OXIDATION_AMBIGUOUS
    assert refusal.token == "Fe3O4"


def test_named_endmember_token_is_refused():
    assignment = assign_phase_oxides([{
        "phase": "olivine",
        "mass_kg": 1.0,
        "formula": "fo",
    }])

    assert "olivine" not in assignment.species_mol
    assert assignment.refusals[0].reason == REASON_UNPARSED_TOKEN
    assert assignment.refusals[0].token == "fo"


def test_refused_instance_drops_the_whole_phase_and_keeps_its_neighbor():
    assignment = assign_phase_oxides([
        {
            "phase": "olivine",
            "mass_kg": 0.04,
            "formula": "(Mg0.8Fe''0.2)2SiO4",
        },
        {
            "phase": "olivine",
            "mass_kg": 0.01,
            "formula": "fo",
        },
        {
            "phase": "liquid",
            "mass_kg": 0.1,
            "composition_wt_pct": {"SiO2": 50.0, "MgO": 50.0},
        },
    ])

    assert "olivine" not in assignment.species_mol
    assert assignment.species_kg["liquid"]["SiO2"] == pytest.approx(0.05)
    assert assignment.species_kg["liquid"]["MgO"] == pytest.approx(0.05)
    assert any(refusal.phase == "olivine" for refusal in assignment.refusals)


def test_weight_percent_that_does_not_reproduce_mass_is_refused():
    assignment = assign_phase_oxides([{
        "phase": "liquid",
        "mass_kg": 1.0,
        "composition_wt_pct": {"SiO2": 40.0, "MgO": 40.0},
    }])

    assert "liquid" not in assignment.species_mol
    assert assignment.refusals[0].reason == REASON_MASS_UNCLOSED


def test_unparsable_weight_percent_name_is_not_skipped():
    assignment = assign_phase_oxides([{
        "phase": "liquid",
        "mass_kg": 1.0,
        "composition_wt_pct": {"SiO2": 50.0, "fo": 50.0},
    }])

    assert "liquid" not in assignment.species_mol
    assert assignment.refusals[0].reason == REASON_UNPARSED_TOKEN
    assert assignment.refusals[0].token == "fo"


def test_adapter_records_a_refused_phase_without_changing_liquid_fraction():
    backend = AlphaMELTSBackend()
    phase = (
        "index 1 Pressure 1.00 Temperature 1400.00 SiO2 FeO MgO\n"
        "liquid1 90.0 -1.0 1.0 1.0 1.0 1.2 50.0 16.0 34.0\n"
        "olivine0 10.0 -1.0 1.0 1.0 1.0 fo 0.0 0.0 0.0\n"
    )
    system = _system_main_fixture(
        temperature_C=1400.0,
        system_mass_g=100.0,
    )
    result = _parse_subprocess_fixture(
        backend,
        (
            "<> Stable liquid assemblage achieved.\n"
            "Initial alphaMELTS calculation at: P 1.000000 (bars), "
            "T 1400.000000 (C)\n"
            "liquid: SiO2 FeO MgO\n"
            "90.0 g 50.0 16.0 34.0\n"
            "olivine0: 10.0 g\n"
            "Melt fraction = 0.9\n"
        ),
        temperature_C=1400.0,
        total_input_kg=0.1,
        system_output=system,
        table_outputs={
            "System_main_tbl.txt": system,
            "Phase_main_tbl.txt": phase,
            "Solid_comp_tbl.txt": (
                "index Pressure Temperature mass SiO2 FeO MgO\n"
                "1 1.00 1400.00 0.0 ---\n"
            ),
            "Bulk_comp_tbl.txt": (
                "index Pressure Temperature mass SiO2 FeO MgO\n"
                "1 1.00 1400.00 100.0 50 16 34\n"
            ),
            "Liquid_comp_tbl.txt": (
                "index Pressure Temperature mass SiO2 FeO MgO\n"
                "1 1.00 1400.00 90.0 50 16 34\n"
            ),
        },
    )

    assert result.status == "ok"
    assert result.liquid_fraction == pytest.approx(0.9)
    assert result.phase_masses_kg["olivine"] == pytest.approx(0.01)
    assert "olivine" not in result.phase_species_mol
    assert "liquid" in result.phase_species_mol
    refusals = result.diagnostics["phase_oxide_assignment_refusals"]
    assert refusals[0]["phase"] == "olivine"
    assert refusals[0]["reason"] == REASON_UNPARSED_TOKEN
    assert refusals[0]["token"] == "fo"
