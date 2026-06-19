from __future__ import annotations

import inspect
import importlib
from pathlib import Path

import pytest

from simulator.accounting.formulas import load_species_formulas
from simulator.electrolysis import (
    DECOMP_VOLTAGES,
    ELECTRONS_PER_OXIDE,
    min_decomposition_voltage,
)
from simulator.state import OXIDE_TO_METAL


SPECIES_CATALOG = Path(__file__).resolve().parents[1] / "data" / "species_catalog.yaml"
EVALUATE_MODULE = importlib.import_module("simulator.optimize.evaluate")

ALLOWED_EXTRA_OXIDES = {
    "CoO": (
        "reserved-but-excluded: CoO E_decomp about 0.49 V is above the "
        "NiO 0.39 V floor (Holmes 1986); Co is trace siderophile/native "
        "Fe-Ni-Co metal, not a CoO feedstock or MRE target"
    ),
}


def test_decomp_voltage_species_have_required_table_coverage() -> None:
    ladder_species = set(DECOMP_VOLTAGES)
    formula_registry = set(load_species_formulas(SPECIES_CATALOG))
    missing = {
        "ELECTRONS_PER_OXIDE": sorted(ladder_species - set(ELECTRONS_PER_OXIDE)),
        "OXIDE_TO_METAL": sorted(ladder_species - set(OXIDE_TO_METAL)),
        "formula registry": sorted(ladder_species - formula_registry),
    }
    missing = {name: species for name, species in missing.items() if species}

    assert not missing, f"DECOMP_VOLTAGES species missing table coverage: {missing}"


def test_reverse_table_extras_are_explicitly_classified() -> None:
    ladder_species = set(DECOMP_VOLTAGES)
    extras_by_table = {
        "ELECTRONS_PER_OXIDE": set(ELECTRONS_PER_OXIDE) - ladder_species,
        "OXIDE_TO_METAL": set(OXIDE_TO_METAL) - ladder_species,
    }
    allowed = set(ALLOWED_EXTRA_OXIDES)
    unclassified = {
        name: sorted(species - allowed)
        for name, species in extras_by_table.items()
        if species - allowed
    }
    active_extras = set().union(*extras_by_table.values())
    stale_allowances = sorted(allowed - active_extras)

    assert not unclassified, f"Unclassified MRE table extras: {unclassified}"
    assert not stale_allowances, (
        "ALLOWED_EXTRA_OXIDES entries are no longer reverse-table extras: "
        f"{stale_allowances}"
    )


def test_mre_off_switch_floor_is_derived_from_ladder() -> None:
    source = inspect.getsource(EVALUATE_MODULE._canonical_mre_voltage_cap)

    assert "min_decomposition_voltage" in source
    assert "0.39" not in source
    assert min_decomposition_voltage() == min(DECOMP_VOLTAGES.values())
    assert min_decomposition_voltage() == DECOMP_VOLTAGES["NiO"]


def test_decomp_voltage_ordering_matches_raw_thermo_reanchor() -> None:
    expected = [
        ("NiO", 0.39),
        ("Na2O", 0.5),
        ("K2O", 0.5),
        ("FeO", 0.75),
        ("Fe2O3", 0.90),
        ("Cr2O3", 0.95),
        ("MnO", 1.05),
        ("SiO2", 1.45),
        ("TiO2", 1.70),
        ("Al2O3", 1.95),
        ("MgO", 2.2),
        ("CaO", 2.5),
    ]

    assert [(species, DECOMP_VOLTAGES[species]) for species, _ in expected] == expected
    grouped = [
        DECOMP_VOLTAGES["NiO"],
        DECOMP_VOLTAGES["Na2O"],
        DECOMP_VOLTAGES["FeO"],
        DECOMP_VOLTAGES["Fe2O3"],
        DECOMP_VOLTAGES["Cr2O3"],
        DECOMP_VOLTAGES["MnO"],
        DECOMP_VOLTAGES["SiO2"],
        DECOMP_VOLTAGES["TiO2"],
        DECOMP_VOLTAGES["Al2O3"],
        DECOMP_VOLTAGES["MgO"],
        DECOMP_VOLTAGES["CaO"],
    ]
    assert DECOMP_VOLTAGES["Na2O"] == DECOMP_VOLTAGES["K2O"]
    assert grouped == sorted(grouped)
    assert len(grouped) == len(set(grouped))
    assert min_decomposition_voltage() == pytest.approx(0.39)


def test_fallback_ladder_voltages_are_derived_from_decomp_voltages() -> None:
    # BUG-011 (SC-09): the C5 fallback ladder must not carry a second hard-coded
    # copy of the rung voltages -- each rung's voltage is sourced from the single
    # DECOMP_VOLTAGES table. (Value identity, not a brittle source-string check.)
    from simulator.mre_ladder import MRE_VOLTAGE_LADDER_FALLBACK

    for rung in MRE_VOLTAGE_LADDER_FALLBACK:
        species = rung["species"]
        assert len(species) == 1, f"fallback rung expects a single species: {rung}"
        assert rung["voltage"] == DECOMP_VOLTAGES[species[0]], (
            f"fallback voltage for {species[0]} drifted from DECOMP_VOLTAGES"
        )


def test_fallback_ladder_excludes_alkali_and_ferric_rungs() -> None:
    # Na2O/K2O are pre-depleted by C3 (DISABLED_PRESET_TARGETS); Fe2O3 is a
    # deferred single-rung pending SSO-R Phase-2 speciation. None belong in the
    # C5 fallback ladder even though they exist in DECOMP_VOLTAGES.
    from simulator.mre_ladder import MRE_VOLTAGE_LADDER_FALLBACK

    fallback_species = {
        sp for rung in MRE_VOLTAGE_LADDER_FALLBACK for sp in rung["species"]
    }
    assert fallback_species.isdisjoint({"Na2O", "K2O", "Fe2O3"})
    assert fallback_species == {
        "NiO", "FeO", "Cr2O3", "MnO", "SiO2", "TiO2", "Al2O3", "MgO", "CaO",
    }
