"""t-325 committed char-as-reductant chemistry (carbonaceous feedstocks).

Gates (owner-corrected after round-1 STOP):
1. CI solid-char account, lance-first stoichiometry, un-lanced survival,
   FeO+C->Fe+CO with stoichiometric Fe/CO from the committed char inventory,
   and full C-atom closure across accounts.
2. Lunar mare (organics-free) is a zero-behavior control for char accounts.
3. Expectations are derived from the feedstock's committed refractory-C
   inventory — not invented yield thresholds.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.account_ids import SOLID_CHAR_CARBON_ACCOUNT
from simulator.accounting.formulas import resolve_species_formula
from simulator.core import (
    CHAR_SPECIES,
    FEO_CHAR_REDUCTION_MIN_T_C,
    OXYGEN_MOLAR_MASS_KG_PER_MOL,
    PyrolysisSimulator,
)
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.run_executor import RunExecutor
from simulator.runner import PyrolysisRun, _c0_char_diagnostic
from simulator.state import CampaignPhase


DATA = Path(__file__).resolve().parent.parent / "data"


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((DATA / name).read_text())


def _build_sim(feedstock_key: str) -> PyrolysisSimulator:
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
    sim.load_batch(feedstock_key, mass_kg=1000.0)
    return sim


def _total_c_atoms(sim: PyrolysisSimulator) -> float:
    registry = sim.species_formula_registry
    total = 0.0
    for _account, species_mol in sim.atom_ledger.mol_by_account().items():
        for species, mol in species_mol.items():
            total += resolve_species_formula(
                species, registry
            ).atom_moles(float(mol)).get("C", 0.0)
    return total


def _committed_char_mol(sim: PyrolysisSimulator) -> float:
    return max(
        0.0,
        float(
            sim.atom_ledger.mol_by_account(SOLID_CHAR_CARBON_ACCOUNT).get(
                CHAR_SPECIES, 0.0
            )
            or 0.0
        ),
    )


def _raw_feed_c_atoms(sim: PyrolysisSimulator) -> float:
    total = 0.0
    for species, kg in sim.inventory.raw_components_kg.items():
        formula = resolve_species_formula(species, sim.species_formula_registry)
        species_mol = float(kg) / formula.molar_mass_kg_per_mol()
        total += formula.atom_moles(species_mol).get("C", 0.0)
    return total


def test_ci_commits_refractory_char_account_from_sephton_partition() -> None:
    """CI Stage-0 withholds f_refractory * organic C as solid char."""
    sim = _build_sim("ci_carbonaceous_chondrite")
    partition = (
        sim._load_carbon_partition_config()
        .get("phase_partitions", {})
        .get("ci_carbonaceous_chondrite", {})
    )
    f_ref = float(partition["f_refractory_organic_C"]["floor"])
    assert f_ref == pytest.approx(0.39)

    char_mol = _committed_char_mol(sim)
    carrier_kg = sim.inventory.raw_components_kg["carbonaceous_organic"]
    carrier_formula = resolve_species_formula(
        "carbonaceous_organic", sim.species_formula_registry
    )
    carrier_mol = carrier_kg / carrier_formula.molar_mass_kg_per_mol()
    carrier_c_mol = carrier_formula.atom_moles(carrier_mol)["C"]
    expected_char_mol = carrier_c_mol * f_ref
    assert char_mol == pytest.approx(expected_char_mol)

    # Labile C went to CO2; solid char is not also in offgas as CO2.
    co2_mol = float(
        sim.atom_ledger.mol_by_account("terminal.offgas").get("CO2", 0.0) or 0.0
    )
    assert co2_mol >= carrier_c_mol - expected_char_mol
    assert _total_c_atoms(sim) == pytest.approx(_raw_feed_c_atoms(sim))


def test_ci_total_c_atom_closure_across_all_accounts() -> None:
    sim = _build_sim("ci_carbonaceous_chondrite")
    total_before = _total_c_atoms(sim)
    assert total_before == pytest.approx(_raw_feed_c_atoms(sim))
    assert abs(sim._make_snapshot().mass_balance_error_pct) < 5e-12

    # FeO + C path moves C from solid char to CO; total C must hold.
    sim.melt.temperature_C = FEO_CHAR_REDUCTION_MIN_T_C + 50.0
    char_before = _committed_char_mol(sim)
    diag = sim._apply_char_feo_reduction()
    assert diag["extent_mol"] == pytest.approx(char_before)
    assert _total_c_atoms(sim) == pytest.approx(total_before)
    assert _committed_char_mol(sim) == pytest.approx(0.0)
    assert float(
        sim.atom_ledger.mol_by_account("terminal.offgas").get("CO", 0.0) or 0.0
    ) == pytest.approx(char_before)
    fe_mol = float(
        sim.atom_ledger.mol_by_account("process.metal_phase").get("Fe", 0.0)
        or 0.0
    )
    assert fe_mol == pytest.approx(char_before)
    assert float(
        sim.atom_ledger.mol_by_account("process.cleaned_melt").get("FeO", 0.0)
        or 0.0
    ) == pytest.approx(diag["feo_mol_before"] - char_before)
    fe_kg = fe_mol * resolve_species_formula(
        "Fe", sim.species_formula_registry
    ).molar_mass_kg_per_mol()
    # Stoichiometric Fe from committed char inventory (1:1 FeO+C->Fe+CO).
    assert fe_kg == pytest.approx(
        char_before
        * resolve_species_formula(
            "Fe", sim.species_formula_registry
        ).molar_mass_kg_per_mol()
    )
    assert abs(sim._make_snapshot().mass_balance_error_pct) < 5e-12


def test_ci_lance_first_oxidizes_char_to_co2_and_clears_inventory() -> None:
    sim = _build_sim("ci_carbonaceous_chondrite")
    char_before = _committed_char_mol(sim)
    total_before = _total_c_atoms(sim)
    # CO2 basis: 1 mol O2 per mol C (configured lance_oxidation).
    sim.atom_ledger.load_external_mol(
        "reservoir.fo2_buffer",
        {"O2": char_before},
        source="test lance dose",
    )
    diag = sim._apply_char_lance_oxidation(o2_available_mol=char_before)
    assert diag["extent_mol"] == pytest.approx(char_before)
    assert diag["o2_consumed_mol"] == pytest.approx(char_before)
    assert diag["product_species"] == "CO2"
    assert _committed_char_mol(sim) == pytest.approx(0.0)
    assert _total_c_atoms(sim) == pytest.approx(total_before)


def test_ci_unlanced_char_survives_below_ellingham_crossover() -> None:
    sim = _build_sim("ci_carbonaceous_chondrite")
    char_before = _committed_char_mol(sim)
    sim.melt.temperature_C = FEO_CHAR_REDUCTION_MIN_T_C - 50.0
    diag = sim._apply_char_feo_reduction()
    assert diag["status"] == "skipped"
    assert diag["reason"] == "below_ellingham_crossover_T"
    assert _committed_char_mol(sim) == pytest.approx(char_before)


def test_unlanced_char_warns_when_susceptible_oxide_is_present() -> None:
    sim = _build_sim("ci_carbonaceous_chondrite")
    sim.atom_ledger.load_external(
        "process.cleaned_melt",
        {"P2O5": 0.1},
        source="test susceptible oxide",
    )
    sim.melt.temperature_C = FEO_CHAR_REDUCTION_MIN_T_C - 50.0
    diag = sim._apply_char_feo_reduction()

    assert diag["contamination_risk"]["status"] == "WARN"
    assert diag["contamination_risk"]["threshold_basis"].startswith(
        "thermodynamic-onset"
    )


def test_c0_bubbler_dose_reaches_char_even_when_melt_is_at_target() -> None:
    sim = _build_sim("ci_carbonaceous_chondrite")
    char_before = _committed_char_mol(sim)
    sim.melt.temperature_C = 1600.0
    sim.melt.campaign = CampaignPhase.C0
    sim._refresh_oxygen_reservoir_without_exchange(
        melt_intrinsic_fO2_log=-8.0,
        reference_T_K=1873.15,
    )
    sim.campaign_mgr.overrides.setdefault("C0", {}).update({
        "o2_bubbler_kg_per_hr": char_before * OXYGEN_MOLAR_MASS_KG_PER_MOL,
        "o2_bubbler_eta_absorb_default": 1.0,
        "o2_bubbler_target_fO2_log": -8.0,
    })

    diagnostic = sim._apply_o2_bubbler()

    assert diagnostic["reason"] == "char_lance_only"
    assert diagnostic["char_lance"]["extent_mol"] == pytest.approx(char_before)
    assert diagnostic["fe_redox_absorbed_mol"] == pytest.approx(0.0)
    assert _committed_char_mol(sim) == pytest.approx(0.0)


def test_partial_live_bubbler_leaves_exact_char_for_feo_reduction() -> None:
    run = PyrolysisRun(
        feedstock_id="ci_carbonaceous_chondrite",
        campaign="C0",
        hours=1,
        allow_fallback_vapor=True,
    )
    execution = RunExecutor().execute(run._session_config())
    sim = execution.simulator
    char_before = _committed_char_mol(sim)
    half = 0.5 * char_before
    sim.melt.temperature_C = 1600.0
    sim.melt.campaign = CampaignPhase.C0
    sim._refresh_oxygen_reservoir_without_exchange(
        melt_intrinsic_fO2_log=-8.0,
        reference_T_K=1873.15,
    )
    sim.campaign_mgr.overrides.setdefault("C0", {}).update({
        "o2_bubbler_kg_per_hr": half * OXYGEN_MOLAR_MASS_KG_PER_MOL,
        "o2_bubbler_eta_absorb_default": 1.0,
        "o2_bubbler_target_fO2_log": -8.0,
    })

    bubbler = sim._apply_o2_bubbler()
    residual = _committed_char_mol(sim)

    assert bubbler["char_lance"]["extent_mol"] == pytest.approx(half)
    assert bubbler["char_lance_o2_mol"] == pytest.approx(half)
    assert bubbler["fe_redox_absorbed_mol"] == pytest.approx(0.0)
    assert residual == pytest.approx(char_before - half)
    diagnostic = _c0_char_diagnostic(
        sim,
        [sim._make_snapshot()],
        feedstock_id="ci_carbonaceous_chondrite",
    )
    lance = diagnostic["lance_stoichiometry"]
    assert lance["residual_basis"].startswith("live_post_lance_solid_char_ledger")
    assert lance["C_plus_O2_to_CO2"]["un_lanced_char_C_mol"] == pytest.approx(
        residual
    )
    assert diagnostic["FeO_reduction_potential"]["basis"] == (
        "live_post_lance_solid_char_ledger_residual"
    )
    reduction = sim._apply_char_feo_reduction()
    assert reduction["extent_mol"] == pytest.approx(residual)
    assert reduction["fe_product_mol"] == pytest.approx(residual)
    assert reduction["co_product_mol"] == pytest.approx(residual)
    assert _committed_char_mol(sim) == pytest.approx(0.0)


def test_co_basis_lance_uses_half_mole_o2_per_mole_char(monkeypatch) -> None:
    sim = _build_sim("ci_carbonaceous_chondrite")
    char_before = _committed_char_mol(sim)
    half_char = 0.5 * char_before
    monkeypatch.setattr(
        sim,
        "_char_lance_basis",
        lambda: ("C_plus_half_O2_to_CO", 0.5, "CO"),
    )
    o2_mol = 0.5 * half_char
    sim.atom_ledger.load_external_mol(
        "reservoir.fo2_buffer", {"O2": o2_mol}, source="test CO-basis lance"
    )

    diagnostic = sim._apply_char_lance_oxidation(o2_available_mol=o2_mol)

    assert diagnostic["extent_mol"] == pytest.approx(half_char)
    assert diagnostic["o2_consumed_mol"] == pytest.approx(o2_mol)
    assert float(
        sim.atom_ledger.mol_by_account("terminal.offgas").get("CO", 0.0) or 0.0
    ) == pytest.approx(half_char)


def test_ci_c0_char_diagnostic_reads_committed_ledger_inventory() -> None:
    payload = PyrolysisRun(
        feedstock_id="ci_carbonaceous_chondrite",
        campaign="C0",
        hours=1,
        allow_fallback_vapor=True,
    ).run()
    diagnostic = payload["run_metadata"]["c0_char_diagnostic"]
    assert diagnostic["inventory"]["formed_refractory_char_C_mol"] > 0.0
    assert diagnostic["inventory"]["refractory_char_C_mol"] == pytest.approx(
        958.7221688514541
    )
    # Default C0 has no bubbler dose; un-lanced residual equals inventory.
    assert diagnostic["lance_stoichiometry"]["C_plus_O2_to_CO2"][
        "un_lanced_char_C_mol"
    ] == pytest.approx(958.7221688514541)
    assert diagnostic["status"] == "WARN"
    assert diagnostic["contamination_risk"]["status"] == "WARN"
    assert "SiO2+C" in diagnostic["contamination_risk"]["out_of_scope"]


def test_c0_diagnostic_does_not_resurrect_consumed_char() -> None:
    run = PyrolysisRun(
        feedstock_id="ci_carbonaceous_chondrite",
        campaign="C0",
        hours=1,
        allow_fallback_vapor=True,
    )
    execution = RunExecutor().execute(run._session_config())
    sim = execution.simulator
    char_before = _committed_char_mol(sim)
    sim.melt.temperature_C = 1600.0
    sim._refresh_oxygen_reservoir_without_exchange(
        melt_intrinsic_fO2_log=-8.0,
        reference_T_K=1873.15,
    )
    sim.campaign_mgr.overrides.setdefault("C0", {}).update({
        "o2_bubbler_kg_per_hr": char_before * OXYGEN_MOLAR_MASS_KG_PER_MOL,
        "o2_bubbler_eta_absorb_default": 1.0,
        "o2_bubbler_target_fO2_log": -8.0,
    })
    sim._apply_o2_bubbler()
    c0_snapshot = sim._make_snapshot()

    diagnostic = _c0_char_diagnostic(
        sim,
        [c0_snapshot],
        feedstock_id="ci_carbonaceous_chondrite",
    )

    assert diagnostic["inventory"]["formed_refractory_char_C_mol"] == pytest.approx(
        char_before
    )
    assert diagnostic["inventory"]["refractory_char_C_mol"] == pytest.approx(0.0)
    assert diagnostic["contamination_risk"]["status"] == "OK"


def test_lunar_mare_has_zero_solid_char_account() -> None:
    sim = _build_sim("lunar_mare_low_ti")
    assert _committed_char_mol(sim) == pytest.approx(0.0)
    assert SOLID_CHAR_CARBON_ACCOUNT not in {
        acc
        for acc, sp in sim.atom_ledger.mol_by_account().items()
        if any(abs(float(v)) > 0.0 for v in sp.values())
    } or _committed_char_mol(sim) == 0.0

    payload = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=1,
        allow_fallback_vapor=True,
    ).run()
    assert "c0_char_diagnostic" not in payload["run_metadata"]
    # FeO+C is a no-op on organics-free lunar.
    sim.melt.temperature_C = 900.0
    diag = sim._apply_char_feo_reduction()
    assert diag["extent_mol"] == pytest.approx(0.0)
    assert float(
        sim.atom_ledger.mol_by_account("process.metal_phase").get("Fe", 0.0)
        or 0.0
    ) == pytest.approx(0.0)


def test_partial_lance_leaves_unlanced_char_for_feo_reduction() -> None:
    sim = _build_sim("ci_carbonaceous_chondrite")
    char_before = _committed_char_mol(sim)
    total_before = _total_c_atoms(sim)
    half = 0.5 * char_before
    sim.atom_ledger.load_external_mol(
        "reservoir.fo2_buffer",
        {"O2": half},
        source="test half lance",
    )
    lance = sim._apply_char_lance_oxidation(o2_available_mol=half)
    assert lance["extent_mol"] == pytest.approx(half)
    residual = _committed_char_mol(sim)
    assert residual == pytest.approx(char_before - half)

    sim.melt.temperature_C = 900.0
    red = sim._apply_char_feo_reduction()
    assert red["extent_mol"] == pytest.approx(residual)
    assert _committed_char_mol(sim) == pytest.approx(0.0)
    assert _total_c_atoms(sim) == pytest.approx(total_before)
    fe_kg = red["fe_product_mol"] * resolve_species_formula(
        "Fe", sim.species_formula_registry
    ).molar_mass_kg_per_mol()
    # Stoichiometric Fe from the residual (un-lanced) committed char only.
    assert fe_kg == pytest.approx(
        residual
        * resolve_species_formula(
            "Fe", sim.species_formula_registry
        ).molar_mass_kg_per_mol()
    )
