from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from engines.builtin.electrolysis_step import MRE_DECOMP_VOLTAGE_PROVENANCE
from simulator.accounting.formulas import load_species_formulas
from simulator.electrolysis import (
    DECOMP_VOLTAGES,
    ELECTRONS_PER_OXIDE,
    FERRIC_TO_FERROUS_REFERENCE_V,
    MRE_FIXED_REDUCIBLE_OXIDES,
    min_decomposition_voltage,
)
from simulator.state import FARADAY, OXIDE_TO_METAL


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


def test_mre_off_switch_floor_tracks_runtime_ladder(monkeypatch) -> None:
    for oxide in ("NiO", "Na2O", "K2O", "FeO"):
        monkeypatch.setitem(DECOMP_VOLTAGES, oxide, 0.80)

    assert min_decomposition_voltage() == pytest.approx(0.80)
    assert EVALUATE_MODULE._canonical_mre_voltage_cap(0.79) == pytest.approx(0.0)
    assert EVALUATE_MODULE._canonical_mre_voltage_cap(0.80) == pytest.approx(0.80)


@pytest.mark.parametrize("cap", [float("nan"), float("inf"), float("-inf")])
def test_mre_off_switch_rejects_nonfinite_caps(cap: float) -> None:
    assert EVALUATE_MODULE._canonical_mre_voltage_cap(cap) == pytest.approx(0.0)


def test_fixed_mre_ladder_excludes_ferric_full_reduction_rung() -> None:
    expected = [
        ("NiO", 0.39),
        ("Na2O", 0.5),
        ("K2O", 0.5),
        ("FeO", 0.75),
        ("Cr2O3", 0.95),
        ("MnO", 1.05),
        ("SiO2", 1.45),
        ("TiO2", 1.70),
        ("Al2O3", 1.95),
        ("MgO", 2.2),
        ("CaO", 2.5),
    ]

    assert [(species, DECOMP_VOLTAGES[species]) for species, _ in expected] == expected
    assert "Fe2O3" in DECOMP_VOLTAGES
    assert "Fe2O3" not in MRE_FIXED_REDUCIBLE_OXIDES
    assert FERRIC_TO_FERROUS_REFERENCE_V < DECOMP_VOLTAGES["FeO"]
    grouped = [
        DECOMP_VOLTAGES["NiO"],
        DECOMP_VOLTAGES["Na2O"],
        DECOMP_VOLTAGES["FeO"],
        DECOMP_VOLTAGES["Cr2O3"],
        DECOMP_VOLTAGES["MnO"],
        DECOMP_VOLTAGES["SiO2"],
        DECOMP_VOLTAGES["TiO2"],
        DECOMP_VOLTAGES["Al2O3"],
        DECOMP_VOLTAGES["MgO"],
        DECOMP_VOLTAGES["CaO"],
    ]
    assert DECOMP_VOLTAGES["Na2O"] == pytest.approx(0.5)
    assert DECOMP_VOLTAGES["K2O"] == pytest.approx(0.5)
    assert grouped == sorted(grouped)
    assert len(grouped) == len(set(grouped))
    assert min_decomposition_voltage() == pytest.approx(0.39)


def test_mre_decomp_voltage_provenance_sidecar_covers_each_rung() -> None:
    assert set(MRE_DECOMP_VOLTAGE_PROVENANCE) == set(DECOMP_VOLTAGES)

    uncited = []
    for oxide, voltage in DECOMP_VOLTAGES.items():
        row = MRE_DECOMP_VOLTAGE_PROVENANCE[oxide]
        n_e = ELECTRONS_PER_OXIDE[oxide]
        expected_delta_gf = -float(voltage) * n_e * FARADAY / 1000.0

        assert row["standard_voltage_V"] == pytest.approx(voltage)
        assert row["electrons_per_formula"] == n_e
        assert row["delta_gf_relation"] == "DeltaGf = -E*n*F"
        assert row["delta_gf_kJ_per_mol_formula"] == pytest.approx(
            expected_delta_gf,
            rel=1e-12,
        )
        if row.get("delta_gf_source"):
            assert str(row["status"]).startswith("cited_")
        else:
            uncited.append((oxide, row["status"]))

    assert dict(uncited) == {
        "Na2O": "legacy_uncited_voltage_pending_activity_vapor_grounding",
        "K2O": "legacy_uncited_voltage_pending_activity_vapor_grounding",
        "Fe2O3": "reference_only_uncited_legacy_not_live_full_reduction_rung",
        "MgO": "legacy_uncited_voltage_pending_thermo_source",
        "CaO": "legacy_uncited_voltage_pending_thermo_source",
    }


def test_fallback_ladder_voltages_pin_canonical_literals() -> None:
    # BUG-011 (SC-09): the C5 fallback ladder derives voltages from the single
    # DECOMP_VOLTAGES table. Pin the observed values to canonical literals so
    # this test fails if the source table drifts under the derived fallback.
    from simulator.mre_ladder import MRE_VOLTAGE_LADDER_FALLBACK

    expected_voltage_by_species = {
        "NiO": 0.39,
        "FeO": 0.75,
        "Cr2O3": 0.95,
        "MnO": 1.05,
        "SiO2": 1.45,
        "TiO2": 1.70,
        "Al2O3": 1.95,
        "MgO": 2.2,
        "CaO": 2.5,
    }
    stale_wrong_voltage_by_species = {
        "FeO": 0.6,
    }

    for rung in MRE_VOLTAGE_LADDER_FALLBACK:
        species = rung["species"]
        assert len(species) == 1, f"fallback rung expects a single species: {rung}"
        oxide = species[0]
        assert rung["voltage"] == pytest.approx(
            expected_voltage_by_species[oxide]
        ), (
            f"fallback voltage for {oxide} drifted from the canonical literal"
        )
        stale_wrong_voltage = stale_wrong_voltage_by_species.get(oxide)
        if stale_wrong_voltage is not None:
            assert rung["voltage"] != pytest.approx(stale_wrong_voltage)


def test_fallback_ladder_excludes_alkali_and_ferric_rungs() -> None:
    # Na2O/K2O are pre-depleted by C3 (DISABLED_PRESET_TARGETS); Fe2O3 is
    # represented by live redox speciation rather than fixed full reduction.
    # None belong in the C5 fallback ladder even though they exist in
    # DECOMP_VOLTAGES.
    from simulator.mre_ladder import MRE_VOLTAGE_LADDER_FALLBACK

    fallback_species = {
        sp for rung in MRE_VOLTAGE_LADDER_FALLBACK for sp in rung["species"]
    }
    assert fallback_species.isdisjoint({"Na2O", "K2O", "Fe2O3"})
    assert fallback_species == {
        "NiO", "FeO", "Cr2O3", "MnO", "SiO2", "TiO2", "Al2O3", "MgO", "CaO",
    }
