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
from simulator.physical_constants import FARADAY
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


def test_mre_off_switch_floor_tracks_runtime_ladder() -> None:
    min_rung = min_decomposition_voltage()

    assert min_rung == pytest.approx(0.023465, abs=1e-6)
    assert EVALUATE_MODULE._canonical_mre_voltage_cap(
        min_rung - 1.0e-5
    ) == pytest.approx(0.0)
    assert EVALUATE_MODULE._canonical_mre_voltage_cap(min_rung) == pytest.approx(
        min_rung
    )


@pytest.mark.parametrize("cap", [float("nan"), float("inf"), float("-inf")])
def test_mre_off_switch_rejects_nonfinite_caps(cap: float) -> None:
    assert EVALUATE_MODULE._canonical_mre_voltage_cap(cap) == pytest.approx(0.0)


def test_static_fallback_table_excludes_ferric_full_reduction_rung() -> None:
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
    assert min_decomposition_voltage() == pytest.approx(0.023465, abs=1e-6)


def test_mre_decomp_voltage_provenance_sidecar_covers_each_rung() -> None:
    assert set(MRE_DECOMP_VOLTAGE_PROVENANCE) == set(DECOMP_VOLTAGES)

    graph_promoted = {
        "Na2O",
        "K2O",
        "FeO",
        "Cr2O3",
        "SiO2",
        "TiO2",
        "Al2O3",
        "MgO",
        "CaO",
    }
    for oxide, voltage in DECOMP_VOLTAGES.items():
        row = MRE_DECOMP_VOLTAGE_PROVENANCE[oxide]
        n_e = ELECTRONS_PER_OXIDE[oxide]
        expected_delta_gf = (
            -float(row["standard_voltage_V"]) * n_e * FARADAY / 1000.0
        )

        assert row["electrons_per_formula"] == n_e
        assert row["delta_gf_relation"] == "DeltaGf = -E*n*F"
        assert row["delta_gf_kJ_per_mol_formula"] == pytest.approx(
            expected_delta_gf,
            rel=1e-10,
        )
        if oxide in graph_promoted:
            assert row["standard_voltage_authority"] == "ellingham_graph"
            assert row["standard_voltage_authoritative"] is True
            assert row["status"] == "authoritative_ellingham_graph"
            assert row["standard_voltage_status"] == "ok"
            assert row["delta_g_relation"] == "DeltaG_dissoc = -E*4F"
        else:
            if oxide == "MnO":
                assert row["standard_voltage_authority"] == "ellingham_graph"
                assert row["standard_voltage_authoritative"] is False
                assert row["status"] == "diagnostic_ellingham_graph"
                assert row["standard_voltage_status"] == (
                    "diagnostic_reconstructed_mn_row_not_authoritative_for_mre"
                )
                assert row["delta_g_relation"] == "DeltaG_dissoc = -E*4F"
            else:
                assert row["standard_voltage_V"] == pytest.approx(voltage)
                assert row["standard_voltage_authority"] == "ellingham_fallback"
                assert row["standard_voltage_authoritative"] is False
                assert row["status"] == "ellingham_fallback"
                continue
            assert row["standard_voltage_authoritative"] is False


def test_fallback_ladder_voltages_pin_canonical_literals() -> None:
    # Missing-YAML fallback uses the same graph-first canonical resolver as the
    # published ladder. NiO remains a flagged static fallback because it is not
    # covered by the Ellingham graph.
    from simulator.mre_ladder import MRE_VOLTAGE_LADDER_FALLBACK

    expected_voltage_by_species = {
        "NiO": 0.39,
        "FeO": 0.804340,
        "MnO": 1.254731,
        "Cr2O3": 1.118868,
        "SiO2": 1.491058,
        "TiO2": 1.575521,
        "MgO": 1.792604,
        "Al2O3": 1.857324,
        "CaO": 2.208316,
    }

    for rung in MRE_VOLTAGE_LADDER_FALLBACK:
        species = rung["species"]
        assert len(species) == 1, f"fallback rung expects a single species: {rung}"
        oxide = species[0]
        assert rung["voltage"] == pytest.approx(
            expected_voltage_by_species[oxide],
            abs=1e-6,
        ), (
            f"fallback voltage for {oxide} drifted from the canonical literal"
        )
        if oxide == "NiO":
            assert rung["voltage_authority"] == "ellingham_fallback"
        elif oxide == "MnO":
            assert rung["voltage_authority"] == "ellingham_graph"
            assert rung["voltage_authoritative"] is False
            assert rung["voltage_status"] == (
                "diagnostic_reconstructed_mn_row_not_authoritative_for_mre"
            )
        else:
            assert rung["voltage_authority"] == "ellingham_graph"


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
