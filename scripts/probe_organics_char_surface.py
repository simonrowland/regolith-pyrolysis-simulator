#!/usr/bin/env python3
"""Real-file probe: pO2-T char survival surface and organic atom closure.

Demonstrates:
- oxygen-lancing limit (no surviving char)
- vacuum limit at moderate T (char maximised)
- high-T weakening via Boudouard even at vacuum pO2
- independent pre/post atom inventory closure on CI
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.accounting.formulas import resolve_species_formula
from simulator.chemistry.organics_pyrolysis import (
    apply_organics_source,
    boudouard_crossover_K,
    char_survival_surface,
)
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend


def _load_yaml(name: str) -> dict:
    import yaml

    return yaml.safe_load((ROOT / "data" / name).read_text())


def _total_atoms(sim: PyrolysisSimulator) -> dict[str, float]:
    totals = {"C": 0.0, "H": 0.0, "O": 0.0, "N": 0.0, "S": 0.0}
    for _account, species_mol in sim.atom_ledger.mol_by_account().items():
        for species, mol in species_mol.items():
            atoms = resolve_species_formula(
                species, sim.species_formula_registry
            ).atom_moles(float(mol))
            for element in totals:
                totals[element] += atoms.get(element, 0.0)
    return totals


def _raw_atoms(sim: PyrolysisSimulator) -> dict[str, float]:
    totals = {"C": 0.0, "H": 0.0, "O": 0.0, "N": 0.0, "S": 0.0}
    for species, kg in sim.inventory.raw_components_kg.items():
        formula = resolve_species_formula(species, sim.species_formula_registry)
        species_mol = float(kg) / formula.molar_mass_kg_per_mol()
        atoms = formula.atom_moles(species_mol)
        for element in totals:
            totals[element] += atoms.get(element, 0.0)
    return totals


def main() -> int:
    temps = [500.0, 800.0, 1200.0]
    pO2 = [1e-12, 1e-8, 1e-6, 1e-4, 0.01, 0.21]
    rows = char_survival_surface(
        temps, pO2, char_c_mol=1.0, co2_mol=0.30
    )
    crossover_C = boudouard_crossover_K() - 273.15

    print("=== CHAR SURVIVAL SURFACE (1 mol nascent C, 0.30 mol CO2) ===")
    print(f"Boudouard Kp=1 at {crossover_C:.2f} C (Voropaev 2023)")
    print(
        f"{'T_C':>8} {'pO2_bar':>12} {'survive':>10} {'a_C':>12} "
        f"{'Kp':>10} {'xi_Boud':>10} {'n_CO':>10}"
    )
    for row in rows:
        print(
            f"{row['temperature_C']:8.1f} {row['pO2_bar']:12.3e} "
            f"{row['survive_fraction']:10.4f} {row['a_C_binding']:12.3e} "
            f"{row['boudouard_Kp']:10.3e} {row['boudouard_extent_mol']:10.4f} "
            f"{row['produced_co_mol']:10.4f}"
        )

    vacuum_500 = next(
        r for r in rows if r["temperature_C"] == 500.0 and r["pO2_bar"] == 1e-12
    )
    vacuum_1200 = next(
        r for r in rows if r["temperature_C"] == 1200.0 and r["pO2_bar"] == 1e-12
    )
    lance_500 = next(
        r for r in rows if r["temperature_C"] == 500.0 and r["pO2_bar"] == 0.21
    )
    print()
    print("Limits:")
    print(
        f"  vacuum 500 C survive={vacuum_500['survive_fraction']:.4f} "
        "(char maximised below Boudouard crossover)"
    )
    print(
        f"  vacuum 1200 C survive={vacuum_1200['survive_fraction']:.4f} "
        "(high-T weakening: Boudouard eats char with pyrolysis CO2)"
    )
    print(
        f"  lance  500 C survive={lance_500['survive_fraction']:.4f} "
        "(oxygen lancing -> no char)"
    )
    if not (
        vacuum_500["survive_fraction"] > vacuum_1200["survive_fraction"]
        and lance_500["survive_fraction"] < 1e-9
    ):
        print("FAIL: surface does not show lance/vacuum/high-T non-monotonicity")
        return 1

    print()
    print("=== CI CLOSURE PROOF ===")
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    setpoints = _load_yaml("setpoints.yaml")
    kernel_cfg = dict(setpoints.get("chemistry_kernel", {}) or {})
    kernel_cfg["allow_fallback_vapor"] = True
    kernel_cfg["allow_unmeasured_alpha_fallback"] = True
    setpoints["chemistry_kernel"] = kernel_cfg
    sim = PyrolysisSimulator(
        backend,
        setpoints,
        _load_yaml("feedstocks.yaml"),
        _load_yaml("vapor_pressures.yaml"),
    )
    sim.load_batch("ci_carbonaceous_chondrite", mass_kg=1000.0)
    pre = _raw_atoms(sim)
    post = _total_atoms(sim)
    print(f"{'el':>4} {'raw_feed':>16} {'ledger':>16} {'delta':>16}")
    closed = True
    for element in ("C", "H", "O", "N", "S"):
        delta = post[element] - pre[element]
        print(
            f"{element:>4} {pre[element]:16.8f} {post[element]:16.8f} "
            f"{delta:16.3e}"
        )
        # O is not a raw-feed identity for the whole batch: other Stage-0
        # reactions may add reagent oxidant. The organic source term
        # itself must still close O.
        if element != "O" and abs(delta) > 1e-8:
            closed = False
    mass_err = float(sim._make_snapshot().mass_balance_error_pct)
    print(f"mass_balance_error_pct = {mass_err:.3e}")
    organics = sim._last_organics_source or {}
    print("organics_source_closed =", organics.get("closed"), organics.get("atom_closure"))
    gas = organics.get("evolved_gas_combined") or {}
    print("evolved_gas reducing_species_mol =", gas.get("reducing_species_mol"))
    print("evolved_gas species_mol =", gas.get("species_mol"))
    if (
        not closed
        or abs(mass_err) >= 5e-12
        or not organics.get("closed")
    ):
        print("FAIL: closure")
        return 1

    # Independent function-level closure on the generic template atoms.
    fn = apply_organics_source(
        {"C": 61.61, "H": 89.29, "O": 7.50, "N": 3.57},
        1050.0,
        1e-12,
        1500.0,
    )
    print("function_level_closed =", fn.closed, fn.atom_closure)
    if not fn.closed:
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
