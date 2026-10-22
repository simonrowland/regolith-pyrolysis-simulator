"""C0 subsolidus bakeout ledger: exact hosts, one mass, no hour-zero credit."""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
from simulator.accounting import resolve_species_formula
from simulator.accounting.formulas import load_species_formulas
from simulator.accounting.stage0_inventory import STAGE0_RELEASE_KINETICS
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.condensation import GAS_CONSTANT_J_MOL_K
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.runner import build_per_hour_summary
from simulator.state import CampaignPhase, CondensationTrain, EvaporationFlux
from simulator.subsolidus_bakeout import (
    CLEANED_MELT_ACCOUNT,
    FOULANT_ACCOUNT,
    METAL_ACCOUNT,
    OPENIMCC_UNAVAILABLE_REASON,
    OVERHEAD_GAS_ACCOUNT,
    SALT_FEED_ACCOUNT,
    catalog_temperature_domain,
    first_melt_backstop,
    mol_maps_for_extents,
    partition_openimcc_results,
    production_seam_refusal,
    seam_refusal,
    throttle_mass_rate_kg_hr,
)
from simulator.vapour_rail.channels import (
    CHANNEL_REGISTRY,
    CHANNEL_S2,
    ChannelVerdictKind,
    resolve_channel_potential,
)

_DATA = Path(__file__).resolve().parents[1] / "data"
_REGISTRY = load_species_formulas(_DATA / "species_catalog.yaml")


def _sim(feedstocks):
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    return PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        feedstocks,
        {"metals": {}, "oxide_vapors": {}},
    )


def _load(feedstock, *, mass_kg=1000.0, additives_kg=None):
    sim = _sim({"feed": feedstock})
    sim.load_batch("feed", mass_kg=mass_kg, additives_kg=additives_kg or {})
    return sim


def test_hour_zero_troilite_stays_mutable_and_is_counted_once():
    sim = _load({
        "label": "troilite host",
        "composition_wt_pct": {
            "SiO2": 90.0,
            "FeS_troilite": 8.0,
            "S": 2.0,
        },
    })
    registry = sim.species_formula_registry
    troilite_kg = sim.inventory.raw_components_kg["FeS_troilite"]
    sulfur_kg = sim.inventory.raw_components_kg["S"]

    salt = sim.atom_ledger.kg_by_account("process.stage0_salt_feed")
    matte = sim.atom_ledger.kg_by_account("terminal.stage0_sulfide_matte")
    offgas = sim.atom_ledger.kg_by_account("terminal.offgas")
    overhead = sim.atom_ledger.kg_by_account("process.overhead_gas")

    assert salt.get("FeS_troilite", 0.0) == pytest.approx(troilite_kg, abs=1e-9)
    assert matte.get("FeS_troilite", 0.0) == pytest.approx(0.0, abs=1e-12)
    assert matte.get("S", 0.0) == pytest.approx(sulfur_kg, abs=1e-9)
    assert offgas.get("S2", 0.0) == pytest.approx(0.0, abs=1e-12)
    assert overhead.get("S2", 0.0) == pytest.approx(0.0, abs=1e-12)
    assert salt.get("S", 0.0) == pytest.approx(0.0, abs=1e-12)

    assert sim.record.initial_inventory.sulfide_matte_kg["FeS_troilite"] == (
        pytest.approx(troilite_kg, abs=1e-9)
    )
    assert sim.inventory.sulfide_matte_kg["FeS_troilite"] == pytest.approx(
        troilite_kg, abs=1e-9
    )

    troilite_mol = troilite_kg / resolve_species_formula(
        "FeS_troilite", registry
    ).molar_mass_kg_per_mol()
    live_mol = sim.atom_ledger.mol_by_account("process.stage0_salt_feed")
    assert live_mol.get("FeS_troilite", 0.0) == pytest.approx(troilite_mol, abs=1e-9)

    first = sim._make_snapshot()
    second = sim._make_snapshot()
    assert first.mass_balance_error_pct == pytest.approx(0.0, abs=5e-12)
    assert second.mass_out_kg == pytest.approx(first.mass_out_kg, abs=1e-12)
    assert second.mass_balance_error_pct == pytest.approx(0.0, abs=5e-12)


def test_hour_zero_sulfates_stay_mutable_without_offgas_credit():
    sim = _load({
        "label": "sulfate hosts",
        "composition_wt_pct": {
            "SiO2": 90.0,
            "CaSO4": 6.0,
            "MgSO4": 4.0,
        },
    })
    caso4_kg = sim.inventory.raw_components_kg["CaSO4"]
    mgso4_kg = sim.inventory.raw_components_kg["MgSO4"]
    salt = sim.atom_ledger.kg_by_account("process.stage0_salt_feed")
    offgas = sim.atom_ledger.kg_by_account("terminal.offgas")
    melt = sim.atom_ledger.kg_by_account("process.cleaned_melt")

    assert salt.get("CaSO4", 0.0) == pytest.approx(caso4_kg, abs=1e-9)
    assert salt.get("MgSO4", 0.0) == pytest.approx(mgso4_kg, abs=1e-9)
    assert sim.record.initial_inventory.cation_sulfate_feed_kg["CaSO4"] == (
        pytest.approx(caso4_kg, abs=1e-9)
    )
    assert sim.record.initial_inventory.cation_sulfate_feed_kg["MgSO4"] == (
        pytest.approx(mgso4_kg, abs=1e-9)
    )
    assert offgas.get("SO2", 0.0) == pytest.approx(0.0, abs=1e-12)
    assert offgas.get("O2", 0.0) == pytest.approx(0.0, abs=1e-12)
    assert melt.get("CaO", 0.0) == pytest.approx(0.0, abs=1e-12)
    assert melt.get("MgO", 0.0) == pytest.approx(0.0, abs=1e-12)

    snapshot = sim._make_snapshot()
    assert snapshot.mass_balance_error_pct == pytest.approx(0.0, abs=5e-12)


def test_limited_carbon_still_gives_feso4_the_post_caso4_remainder():
    """CaSO4 reserves the carbon its old cleanup would have used.

    FeSO4 then sees that reduced remainder and keeps the Fe2O3 branch.
    """
    mass_kg = 1000.0
    feedstock = {
        "label": "shared sulfate carbon",
        "stage0_profile": "mars_carbon_cleanup",
        "stage0_carbon_cleanup": {
            "carbon_reductant_kg_per_tonne": 1.0,
            "reactions": ["sulfate_so3_to_so2_co"],
        },
        "composition_wt_pct": {
            "SiO2": 80.0,
            "CaSO4": 10.0,
            "FeSO4": 10.0,
        },
    }
    sim = _sim({"feed": feedstock})
    registry = sim.species_formula_registry
    caso4_kg = mass_kg * 0.10
    feso4_kg = mass_kg * 0.10
    # Provisional carbon; replaced with the exact shared budget below.
    sim = _sim({"feed": feedstock})
    caso4_mol = caso4_kg / resolve_species_formula(
        "CaSO4", registry
    ).molar_mass_kg_per_mol()
    feso4_mol = feso4_kg / resolve_species_formula(
        "FeSO4", registry
    ).molar_mass_kg_per_mol()
    c_molar = resolve_species_formula("C", registry).molar_mass_kg_per_mol()
    # CaSO4 cleanup would take 1 mol C per mol host. Give it the full host
    # plus half a mol C per mol FeSO4, which is the Fe2O3 stoichiometry.
    carbon_mol = caso4_mol + 0.5 * feso4_mol
    carbon_kg = carbon_mol * c_molar
    feedstock["stage0_carbon_cleanup"]["carbon_reductant_kg_per_tonne"] = (
        carbon_kg * 1000.0 / mass_kg
    )
    sim = _sim({"feed": feedstock})
    sim.load_batch("feed", mass_kg=mass_kg, additives_kg={"C": carbon_kg})

    caso4_kg = sim.inventory.raw_components_kg["CaSO4"]
    feso4_kg = sim.inventory.raw_components_kg["FeSO4"]
    caso4_mol = caso4_kg / resolve_species_formula(
        "CaSO4", registry
    ).molar_mass_kg_per_mol()
    feso4_mol = feso4_kg / resolve_species_formula(
        "FeSO4", registry
    ).molar_mass_kg_per_mol()
    fe2o3_molar = resolve_species_formula(
        "Fe2O3", registry
    ).molar_mass_kg_per_mol()
    so2_molar = resolve_species_formula("SO2", registry).molar_mass_kg_per_mol()
    co_molar = resolve_species_formula("CO", registry).molar_mass_kg_per_mol()

    salt = sim.atom_ledger.kg_by_account("process.stage0_salt_feed")
    assert salt.get("CaSO4", 0.0) == pytest.approx(caso4_kg, abs=1e-6)
    assert salt.get("FeSO4", 0.0) == pytest.approx(0.0, abs=1e-9)
    assert "FeSO4" not in sim.inventory.cation_sulfate_feed_kg

    melt = sim.atom_ledger.kg_by_account("process.cleaned_melt")
    offgas = sim.atom_ledger.kg_by_account("terminal.offgas")
    # 2 FeSO4 + C -> Fe2O3 + 2 SO2 + CO, so products are half the feed moles.
    assert melt.get("Fe2O3", 0.0) == pytest.approx(
        0.5 * feso4_mol * fe2o3_molar, abs=1e-6
    )
    assert melt.get("CaO", 0.0) == pytest.approx(0.0, abs=1e-9)
    assert offgas.get("SO2", 0.0) == pytest.approx(feso4_mol * so2_molar, abs=1e-6)
    assert offgas.get("CO", 0.0) == pytest.approx(0.5 * feso4_mol * co_molar, abs=1e-6)
    assert sim._stage0_bakeout_reserved_carbon_kg == pytest.approx(
        caso4_mol * c_molar, abs=1e-6
    )

    snapshot = sim._make_snapshot()
    assert snapshot.mass_balance_error_pct == pytest.approx(0.0, abs=5e-12)


def _molar(species: str) -> float:
    return resolve_species_formula(species, _REGISTRY).molar_mass_kg_per_mol()


def _hk_driver_mol_h(
    *,
    species: str,
    temperature_K: float,
    p_eq_Pa: float,
    p_bulk_Pa: float,
    alpha: float,
    area_m2: float,
) -> float:
    """Free-molecular Hertz-Knudsen driver rate. Gas and melt resistance are off."""
    molar = _molar(species)
    k_hk = math.sqrt(
        molar / (2.0 * math.pi * GAS_CONSTANT_J_MOL_K * temperature_K)
    )
    delta_p = max(0.0, p_eq_Pa - p_bulk_Pa)
    flux_kg_s_m2 = delta_p * alpha * k_hk
    return flux_kg_s_m2 * area_m2 * 3600.0 / molar


def _bakeout_view() -> ProviderAccountView:
    return ProviderAccountView(
        accounts={"process.cleaned_melt": {}},
        species_formula_registry=_REGISTRY,
    )


def _dispatch_bakeout(**controls):
    provider = BuiltinEvaporationFluxProvider()
    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=_bakeout_view(),
        temperature_C=float(controls.pop("temperature_C")),
        pressure_bar=1.0e-6,
        fO2_log=None,
        control_inputs={"solid_host_bakeout": True, **controls},
    )
    return provider.dispatch(request)


def _troilite_reaction(**overrides):
    reaction = {
        "reaction_id": "troilite_decomposition",
        "status": "supported",
        "driving_gas_species_id": "S2",
        "host_species_id": "FeS_troilite",
        "host_remaining_mol": 2.0,
        "extent_bound_mol_rxn": 2.0,
        "stoichiometry": [
            {
                "species_id": "FeS_troilite",
                "phase": "troilite",
                "nu_mol_per_mol_rxn": -1.0,
            },
            {"species_id": "Fe", "phase": "metal", "nu_mol_per_mol_rxn": 1.0},
            {"species_id": "S2", "phase": "gas", "nu_mol_per_mol_rxn": 0.5},
        ],
    }
    reaction.update(overrides)
    return reaction


def _sulfate_reaction(**overrides):
    reaction = {
        "reaction_id": "anhydrite_decomposition",
        "status": "supported",
        "driving_gas_species_id": "SO2",
        "host_species_id": "CaSO4",
        "host_remaining_mol": 5.0,
        "extent_bound_mol_rxn": 5.0,
        "stoichiometry": [
            {
                "species_id": "CaSO4",
                "phase": "anhydrite",
                "nu_mol_per_mol_rxn": -1.0,
            },
            {"species_id": "CaO", "phase": "crystal", "nu_mol_per_mol_rxn": 1.0},
            {"species_id": "SO2", "phase": "gas", "nu_mol_per_mol_rxn": 1.0},
            {"species_id": "O2", "phase": "gas", "nu_mol_per_mol_rxn": 0.5},
        ],
    }
    reaction.update(overrides)
    return reaction


def test_solid_host_below_400_k_is_unavailable():
    result = _dispatch_bakeout(
        temperature_C=25.0,
        vapour_batch_flux_pressures_Pa={"S2": 10.0},
        solid_host_area_m2=1.0,
        max_throughput_kg_hr=10.0,
        dt_hr=1.0,
        alpha={"S2": 0.2},
        molar_mass_kg_mol={"S2": _molar("S2")},
        reactions=[_troilite_reaction()],
    )
    assert result.status == "unavailable"
    assert result.transition is None
    assert result.diagnostic["reason"] == "below_400_K"
    assert result.diagnostic.get("released_mol_by_species", {}) == {}


def test_solid_host_missing_batch_pressure_keeps_the_seam_reason():
    result = _dispatch_bakeout(
        temperature_C=626.85,
        solid_host_area_m2=1.0,
        max_throughput_kg_hr=10.0,
        dt_hr=1.0,
        alpha={"S2": 0.2},
        molar_mass_kg_mol={"S2": _molar("S2")},
        reactions=[_troilite_reaction()],
    )
    assert result.status == "unavailable"
    assert result.diagnostic["reason"] == "missing_vapour_batch_flux_pressures_Pa"
    assert result.transition is None


def test_solid_host_missing_area_alpha_and_pump_are_typed_refusals():
    common = {
        "temperature_C": 626.85,
        "vapour_batch_flux_pressures_Pa": {"S2": 10.0},
        "dt_hr": 1.0,
        "molar_mass_kg_mol": {"S2": _molar("S2")},
        "reactions": [_troilite_reaction()],
    }
    missing_area = _dispatch_bakeout(
        **common,
        max_throughput_kg_hr=10.0,
        alpha={"S2": 0.2},
    )
    assert missing_area.status == "refused"
    assert missing_area.diagnostic["reason"] == "missing_solid_area"

    missing_alpha = _dispatch_bakeout(
        **common,
        solid_host_area_m2=0.4,
        max_throughput_kg_hr=10.0,
        allow_unmeasured_alpha_fallback=True,
        alpha={},
    )
    assert missing_alpha.status == "refused"
    assert missing_alpha.diagnostic["reason"] == (
        "missing_sourced_evaporation_coefficient"
    )
    assert missing_alpha.diagnostic.get("released_mol_by_species", {}) == {}

    missing_pump = _dispatch_bakeout(
        **common,
        solid_host_area_m2=0.4,
        max_throughput_kg_hr=0.0,
        alpha={"S2": 1.0},
    )
    assert missing_pump.status == "refused"
    assert missing_pump.diagnostic["reason"] == "missing_pump_throughput"


def test_explicit_alpha_sets_the_unconstrained_s2_demand():
    temperature_K = 900.0
    alpha = 1.0
    area_m2 = 0.25
    p_eq = 12.0
    dt_hr = 1.0
    result = _dispatch_bakeout(
        temperature_C=temperature_K - 273.15,
        vapour_batch_flux_pressures_Pa={"S2": p_eq},
        overhead_partials_Pa={"S2": 2.0},
        solid_host_area_m2=area_m2,
        max_throughput_kg_hr=1.0e6,
        dt_hr=dt_hr,
        alpha={"S2": alpha},
        molar_mass_kg_mol={"S2": _molar("S2"), "Fe": _molar("Fe")},
        reactions=[
            _troilite_reaction(
                extent_bound_mol_rxn=1.0e6,
                host_remaining_mol=1.0e6,
            )
        ],
    )
    driver_mol_h = _hk_driver_mol_h(
        species="S2",
        temperature_K=temperature_K,
        p_eq_Pa=p_eq,
        p_bulk_Pa=2.0,
        alpha=alpha,
        area_m2=area_m2,
    )
    extent = driver_mol_h * dt_hr / 0.5
    assert result.status == "ok"
    assert result.transition is None
    assert result.diagnostic["hk_demand_mol_by_species"]["S2"] == pytest.approx(
        extent * 0.5, rel=1e-12
    )
    assert result.diagnostic["hk_demand_mol_hr_by_species"]["S2"] == pytest.approx(
        driver_mol_h, rel=1e-12
    )
    assert result.diagnostic["hk_demand_peak_mol_hr_by_species"]["S2"] == (
        pytest.approx(driver_mol_h, rel=1e-12)
    )
    assert result.diagnostic["released_mol_by_species"]["S2"] == pytest.approx(
        extent * 0.5, rel=1e-12
    )
    assert result.diagnostic["release_mol_hr_by_species"]["S2"] == pytest.approx(
        driver_mol_h, rel=1e-12
    )
    assert "Fe" not in result.diagnostic["released_mol_by_species"]
    assert result.diagnostic["accepted_extent_mol_by_reaction"][
        "troilite_decomposition"
    ] == pytest.approx(extent, rel=1e-12)


def test_inventory_cap_and_pump_scale_share_one_extent():
    temperature_K = 1000.0
    alpha = 0.4
    p_eq = 20.0
    per_m2 = _hk_driver_mol_h(
        species="SO2",
        temperature_K=temperature_K,
        p_eq_Pa=p_eq,
        p_bulk_Pa=0.0,
        alpha=alpha,
        area_m2=1.0,
    )
    area_m2 = 2.0 / per_m2
    molar = {"SO2": _molar("SO2"), "O2": _molar("O2"), "CaO": _molar("CaO")}
    unconstrained_extent = 2.0
    inventory_extent = 1.0
    gas_per_extent = molar["SO2"] + 0.5 * molar["O2"]
    inventory_gas_kg_h = inventory_extent * gas_per_extent
    peak_kg_h = unconstrained_extent * gas_per_extent
    throughput = 0.5 * inventory_gas_kg_h
    result = _dispatch_bakeout(
        temperature_C=temperature_K - 273.15,
        vapour_batch_flux_pressures_Pa={"SO2": p_eq},
        solid_host_area_m2=area_m2,
        max_throughput_kg_hr=throughput,
        dt_hr=1.0,
        alpha={"SO2": alpha},
        molar_mass_kg_mol=molar,
        reactions=[
            _sulfate_reaction(host_remaining_mol=1.0, extent_bound_mol_rxn=10.0)
        ],
    )
    assert result.status == "ok"
    demand = result.diagnostic["hk_demand_mol_by_species"]
    released = result.diagnostic["released_mol_by_species"]
    assert demand["SO2"] == pytest.approx(unconstrained_extent, rel=1e-9)
    assert demand["O2"] == pytest.approx(0.5 * unconstrained_extent, rel=1e-9)
    assert "CaO" not in demand
    assert result.diagnostic["pump_peak_demand_kg_hr"] == pytest.approx(
        peak_kg_h, rel=1e-9
    )
    assert result.diagnostic["pump_scale"] == pytest.approx(
        throughput / peak_kg_h, rel=1e-12
    )
    accepted = min(inventory_extent, unconstrained_extent * throughput / peak_kg_h)
    assert released["SO2"] == pytest.approx(accepted, rel=1e-9)
    assert released["O2"] == pytest.approx(0.5 * accepted, rel=1e-9)
    assert released["SO2"] < demand["SO2"]
    assert "CaO" not in released
    assert result.diagnostic["release_mol_hr_by_species"]["O2"] == pytest.approx(
        released["O2"], rel=1e-12
    )


def test_step_average_fits_but_within_step_peak_exceeds_capacity():
    """Inventory hour-average can sit under the pump while the initial rate does not."""
    temperature_K = 1000.0
    alpha = 0.4
    p_eq = 20.0
    per_m2 = _hk_driver_mol_h(
        species="SO2",
        temperature_K=temperature_K,
        p_eq_Pa=p_eq,
        p_bulk_Pa=0.0,
        alpha=alpha,
        area_m2=1.0,
    )
    area_m2 = 2.0 / per_m2
    molar = {"SO2": _molar("SO2"), "O2": _molar("O2"), "CaO": _molar("CaO")}
    gas_per_extent = molar["SO2"] + 0.5 * molar["O2"]
    unconstrained_extent = 2.0
    inventory_extent = 1.0
    peak_kg_h = unconstrained_extent * gas_per_extent
    inventory_average_kg_h = inventory_extent * gas_per_extent
    throughput = 1.5 * inventory_average_kg_h
    assert inventory_average_kg_h < throughput < peak_kg_h
    result = _dispatch_bakeout(
        temperature_C=temperature_K - 273.15,
        vapour_batch_flux_pressures_Pa={"SO2": p_eq},
        solid_host_area_m2=area_m2,
        max_throughput_kg_hr=throughput,
        dt_hr=1.0,
        alpha={"SO2": alpha},
        molar_mass_kg_mol=molar,
        reactions=[
            _sulfate_reaction(host_remaining_mol=1.0, extent_bound_mol_rxn=10.0)
        ],
    )
    scale = throughput / peak_kg_h
    assert result.status == "ok"
    assert result.diagnostic["pump_peak_demand_kg_hr"] == pytest.approx(peak_kg_h)
    assert result.diagnostic["pump_scale"] == pytest.approx(scale)
    assert result.diagnostic["pump_scale"] < 1.0
    accepted = min(inventory_extent, unconstrained_extent * scale)
    assert result.diagnostic["accepted_extent_mol_by_reaction"][
        "anhydrite_decomposition"
    ] == pytest.approx(accepted)
    assert result.diagnostic["inventory_capped_extent_mol_by_reaction"][
        "anhydrite_decomposition"
    ] == pytest.approx(inventory_extent)
    assert result.diagnostic["released_mol_by_species"]["SO2"] == pytest.approx(
        accepted
    )
    assert result.diagnostic["released_mol_by_species"]["O2"] == pytest.approx(
        0.5 * accepted
    )


def test_ordinary_gas_and_solid_share_one_throughput():
    """One pump scale covers the solid peak and gas already admitted this step."""
    temperature_K = 1000.0
    alpha = 0.4
    p_eq = 20.0
    per_m2 = _hk_driver_mol_h(
        species="SO2",
        temperature_K=temperature_K,
        p_eq_Pa=p_eq,
        p_bulk_Pa=0.0,
        alpha=alpha,
        area_m2=1.0,
    )
    area_m2 = 2.0 / per_m2
    molar = {"SO2": _molar("SO2"), "O2": _molar("O2"), "CaO": _molar("CaO")}
    gas_per_extent = molar["SO2"] + 0.5 * molar["O2"]
    solid_peak = 2.0 * gas_per_extent
    admitted = solid_peak
    throughput = 1.5 * solid_peak
    assert solid_peak < throughput < solid_peak + admitted
    result = _dispatch_bakeout(
        temperature_C=temperature_K - 273.15,
        vapour_batch_flux_pressures_Pa={"SO2": p_eq},
        solid_host_area_m2=area_m2,
        max_throughput_kg_hr=throughput,
        dt_hr=1.0,
        alpha={"SO2": alpha},
        molar_mass_kg_mol=molar,
        admitted_gas_kg_hr={"Na": admitted},
        reactions=[
            _sulfate_reaction(host_remaining_mol=1.0, extent_bound_mol_rxn=10.0)
        ],
    )
    combined = solid_peak + admitted
    scale = throughput / combined
    assert result.status == "ok"
    assert result.diagnostic["pump_peak_demand_kg_hr"] == pytest.approx(combined)
    assert result.diagnostic["admitted_gas_kg_hr"] == pytest.approx(admitted)
    assert result.diagnostic["pump_scale"] == pytest.approx(scale)
    assert result.diagnostic["pump_scale"] < 1.0
    accepted = min(1.0, 2.0 * scale)
    assert result.diagnostic["accepted_extent_mol_by_reaction"][
        "anhydrite_decomposition"
    ] == pytest.approx(accepted)
    assert result.diagnostic["released_mol_by_species"]["O2"] == pytest.approx(
        0.5 * accepted
    )


def test_joint_release_stays_within_pipe_capacity():
    temperature_K = 1000.0
    alpha = 0.4
    p_eq = 20.0
    per_m2 = _hk_driver_mol_h(
        species="SO2",
        temperature_K=temperature_K,
        p_eq_Pa=p_eq,
        p_bulk_Pa=0.0,
        alpha=alpha,
        area_m2=1.0,
    )
    area_m2 = 2.0 / per_m2
    gas_per_extent = _molar("SO2") + 0.5 * _molar("O2")
    solid_peak = 2.0 * gas_per_extent
    admitted = solid_peak
    throughput = 1.5 * solid_peak
    scale = throughput / (solid_peak + admitted)
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        yaml.safe_load((_DATA / "setpoints.yaml").read_text()),
        {"feed": {
            "label": "joint",
            # FeO is the melt parent, so the real condensation route can
            # commit Fe. SO2 and O2 are not in that flux.
            "composition_wt_pct": {"SiO2": 82.0, "FeO": 10.0, "CaSO4": 8.0},
        }},
        yaml.safe_load((_DATA / "vapor_pressures.yaml").read_text()),
    )
    sim.load_batch("feed", mass_kg=1000.0)
    salt_before = sim.atom_ledger.mol_by_account(SALT_FEED_ACCOUNT)["CaSO4"]
    priced_temperature_C = temperature_K - 273.15
    _arm_c0(sim, temperature_C=priced_temperature_C)
    # The C0 ramp adds 50 C this hour. Restore the temperature the area was
    # priced at after that update so the Hertz-Knudsen extent stays 2 mol/h.
    original_update = sim._update_temperature

    def _hold_priced_temperature():
        original_update()
        sim.melt.temperature_C = priced_temperature_C

    sim._update_temperature = _hold_priced_temperature

    def _evaporate(_equilibrium, overhead_partials_override_Pa=None):
        return EvaporationFlux(
            species_kg_hr={"Fe": admitted},
            total_kg_hr=admitted,
        )

    sim._calculate_evaporation = _evaporate
    seen = {}
    transport_calls = []
    real_estimate = sim.overhead_model.estimate_transport_state

    def _estimate(flux, melt, *args, **kwargs):
        state = real_estimate(flux, melt, *args, **kwargs)
        transport_calls.append({
            "species": {
                str(name): float(rate)
                for name, rate in flux.species_kg_hr.items()
                if float(rate) > 0.0
            },
            "salt_mol": sim.atom_ledger.mol_by_account(
                SALT_FEED_ACCOUNT
            ).get("CaSO4"),
            "total_kg_hr": float(flux.total_kg_hr),
            "state": state,
        })
        return state

    sim.overhead_model.estimate_transport_state = _estimate
    real_route = sim._route_to_condensation

    def _route(flux):
        seen["species"] = set(flux.species_kg_hr)
        seen["fe_kg_hr"] = flux.species_kg_hr.get("Fe")
        seen["salt_mol"] = sim.atom_ledger.mol_by_account(
            SALT_FEED_ACCOUNT
        ).get("CaSO4")
        return real_route(flux)

    sim._route_to_condensation = _route
    original = sim._apply_c0_bakeout

    def _evaluator(**_kwargs):
        return [{
            "status": "supported",
            "reaction_id": "anhydrite_decomposition",
            "driving_gas_species_id": "SO2",
            "extent_bound_mol_rxn": 10.0,
            "equilibrium_gas_partial_pressures_Pa": {"SO2": p_eq},
            "stoichiometry": _sulfate_reaction()["stoichiometry"],
        }]

    def _bakeout(**kwargs):
        sim._volatiles_train_spec = type("Train", (), {
            "max_throughput_kg_hr": throughput,
        })()
        kwargs["seam_refusal_for"] = lambda _species, _temperature_K: None
        kwargs["evaluator_loader"] = lambda: _evaluator
        kwargs["solid_host_area_m2"] = area_m2
        kwargs["alpha"] = {"SO2": alpha}
        return original(**kwargs)

    sim._apply_c0_bakeout = _bakeout
    sim._apply_analytic_evaporation_depletion = lambda flux: flux
    sim.step()
    pump_admitted = 2.0 * scale
    decision = [
        call for call in transport_calls
        if set(call["species"]) >= {"Fe", "SO2", "O2"}
        and call["salt_mol"] == pytest.approx(salt_before)
    ]
    assert decision
    demand_kg_hr = decision[0]["total_kg_hr"]
    capacity_kg_hr = decision[0]["state"]["pipe_conductance_kg_hr"]
    assert demand_kg_hr > capacity_kg_hr
    transport_scale = capacity_kg_hr / demand_kg_hr
    accepted = pump_admitted * transport_scale
    fe_kg_hr = admitted * scale * transport_scale
    so2_kg_hr = accepted * _molar("SO2")
    o2_kg_hr = accepted * 0.5 * _molar("O2")
    assert fe_kg_hr + so2_kg_hr + o2_kg_hr == pytest.approx(capacity_kg_hr)
    assert decision[0]["species"]["Fe"] == pytest.approx(admitted * scale)
    assert decision[0]["species"]["SO2"] == pytest.approx(
        pump_admitted * _molar("SO2")
    )
    assert decision[0]["species"]["O2"] == pytest.approx(
        pump_admitted * 0.5 * _molar("O2")
    )
    assert seen["fe_kg_hr"] == pytest.approx(fe_kg_hr)
    assert "SO2" not in seen["species"]
    assert "O2" not in seen["species"]
    assert seen["salt_mol"] == pytest.approx(salt_before - accepted)
    assert salt_before > seen["salt_mol"] > 0.0
    cap_record = sim._last_subsolidus_bakeout["transport_cap"]
    assert cap_record["demand_kg_hr"] == pytest.approx(demand_kg_hr)
    assert cap_record["capacity_kg_hr"] == pytest.approx(capacity_kg_hr)
    assert cap_record["scale"] == pytest.approx(transport_scale)
    ordinary = EvaporationFlux(species_kg_hr={"Fe": fe_kg_hr})
    ordinary.update_totals()
    combined = EvaporationFlux(species_kg_hr={
        "Fe": fe_kg_hr,
        "SO2": so2_kg_hr,
        "O2": o2_kg_hr,
    })
    combined.update_totals()
    assert combined.total_kg_hr == pytest.approx(capacity_kg_hr)
    held = sim._effective_transport_capacity_this_tick
    downstream = sim._headspace_downstream_pressure_bar(held)
    ordinary_state = real_estimate(
        ordinary,
        sim.melt,
        p_downstream_bar=downstream,
        effective_transport_capacity=held,
    )
    combined_state = real_estimate(
        combined,
        sim.melt,
        p_downstream_bar=downstream,
        effective_transport_capacity=held,
    )
    headspace = sim._melt_headspace_composition_mbar
    assert set(headspace) >= {"Fe", "SO2", "O2"}
    assert headspace["SO2"] > 0.0
    assert headspace["O2"] > 0.0
    assert sum(headspace.values()) == pytest.approx(
        combined_state["vapor_pressure_mbar"]
    )
    assert combined_state["vapor_pressure_mbar"] != pytest.approx(
        ordinary_state["vapor_pressure_mbar"]
    )
    assert sim.overhead.pipe_conductance_kg_hr == pytest.approx(
        combined_state["pipe_conductance_kg_hr"]
    )
    assert sim.record.snapshots[-1].mass_balance_error_pct == pytest.approx(
        0.0, abs=5e-12
    )
    assert sim._subsolidus_pending_release is None
    assert sim._last_subsolidus_bakeout["committed"] is True
    assert sim._subsolidus_bakeout_by_hour[-1]["committed"] is True


def test_competing_branches_share_one_host_inventory():
    temperature_K = 900.0
    alpha = 0.25
    p_eq = 8.0
    per_m2 = _hk_driver_mol_h(
        species="S2",
        temperature_K=temperature_K,
        p_eq_Pa=p_eq,
        p_bulk_Pa=0.0,
        alpha=alpha,
        area_m2=1.0,
    )
    area_m2 = 0.4 / per_m2
    result = _dispatch_bakeout(
        temperature_C=temperature_K - 273.15,
        vapour_batch_flux_pressures_Pa={"S2": p_eq},
        solid_host_area_m2=area_m2,
        max_throughput_kg_hr=1.0e6,
        dt_hr=1.0,
        alpha={"S2": alpha},
        molar_mass_kg_mol={"S2": _molar("S2")},
        reactions=[
            _troilite_reaction(
                reaction_id="b_branch",
                host_remaining_mol=1.0,
                extent_bound_mol_rxn=10.0,
            ),
            _troilite_reaction(
                reaction_id="a_branch",
                host_remaining_mol=1.0,
                extent_bound_mol_rxn=10.0,
            ),
        ],
    )
    accepted = result.diagnostic["accepted_extent_mol_by_reaction"]
    assert accepted["a_branch"] == pytest.approx(0.8, rel=1e-9)
    assert accepted["b_branch"] == pytest.approx(0.2, rel=1e-9)
    assert accepted["a_branch"] + accepted["b_branch"] == pytest.approx(1.0, rel=1e-9)


def test_refused_reaction_does_not_enter_released_demand():
    result = _dispatch_bakeout(
        temperature_C=626.85,
        vapour_batch_flux_pressures_Pa={"S2": 10.0},
        solid_host_area_m2=1.0,
        max_throughput_kg_hr=10.0,
        dt_hr=1.0,
        alpha={"S2": 0.2},
        molar_mass_kg_mol={"S2": _molar("S2")},
        reactions=[_troilite_reaction(status="thermochemistry_missing")],
    )
    assert result.status == "unavailable"
    assert result.diagnostic.get("released_mol_by_species", {}) == {}
    assert result.diagnostic.get("hk_demand_mol_by_species", {}) == {}


def _net_element_mol(debits, credits) -> dict[str, float]:
    net: dict[str, float] = {}
    for sign, side in ((-1.0, debits), (1.0, credits)):
        for species_mol in side.values():
            for species, mol in species_mol.items():
                atoms = resolve_species_formula(species, _REGISTRY).atom_moles(mol)
                for element, amount in atoms.items():
                    net[element] = net.get(element, 0.0) + sign * amount
    return net


def _commit(sim, family: str, debits, credits):
    return sim._dispatch_and_commit(
        ChemistryIntent.STAGE0_PRETREATMENT,
        control_inputs={
            "reaction_family": family,
            "mol_debits": debits,
            "mol_credits": credits,
        },
    )


def _s2_fraction(sim) -> float:
    sim._update_target_inventory_diagnostic()
    row = next(
        item
        for item in sim._last_target_inventory_diagnostic["targets"]
        if item["key"] == "S2"
    )
    return float(row["remaining_fraction"])


def _campaign_sim(feedstock_key: str, feedstock: dict):
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        yaml.safe_load((_DATA / "setpoints.yaml").read_text()),
        {feedstock_key: feedstock},
        yaml.safe_load((_DATA / "vapor_pressures.yaml").read_text()),
    )
    sim.load_batch(feedstock_key, mass_kg=1000.0)
    return sim


def _arm_c0(sim, temperature_C: float = 600.0) -> None:
    sim.melt.campaign = CampaignPhase.C0
    sim.melt.temperature_C = temperature_C


def test_s2_and_so2_stay_unavailable_until_a_c0_domain_has_an_owner():
    payload = yaml.safe_load((_DATA / "species_catalog.yaml").read_text())
    rows = payload["species"]
    s2_domain = catalog_temperature_domain("S2", rows)
    so2_domain = catalog_temperature_domain("SO2", rows)
    assert s2_domain is None
    assert so2_domain is not None
    envelope_low, envelope_high = 348.0, 1223.0
    assert not (
        so2_domain[0] <= envelope_low and so2_domain[1] >= envelope_high
    )
    assert CHANNEL_REGISTRY[CHANNEL_S2].runtime_owner is None
    s2_potential = resolve_channel_potential(CHANNEL_S2, temperature_K=900.0)
    assert s2_potential.verdict is ChannelVerdictKind.REFUSAL
    assert s2_potential.refusal_code == "refused_sulfur_reservoir_owner_missing"
    assert production_seam_refusal("S2", 900.0) == (
        "refused_sulfur_reservoir_owner_missing"
    )
    assert production_seam_refusal("SO2", 900.0) == "so2_c0_domain_unowned"
    owned = (300.0, 1300.0)
    # A covering domain does not admit either driver while S2's channel
    # refuses and SO2 has no channel. An owner name is not an argument.
    import simulator.subsolidus_bakeout as bakeout_module
    saved_domains = bakeout_module._CATALOG_DOMAIN_CACHE
    bakeout_module._CATALOG_DOMAIN_CACHE = {"S2": owned, "SO2": owned}
    try:
        assert production_seam_refusal("S2", 900.0) == (
            "refused_sulfur_reservoir_owner_missing"
        )
        assert production_seam_refusal("SO2", 900.0) == "so2_c0_domain_unowned"
        assert seam_refusal("S2", 900.0, domain=owned) is None
        assert seam_refusal("SO2", 900.0, domain=owned) == "so2_c0_domain_unowned"

        def _admit(*_args, **_kwargs):
            return SimpleNamespace(
                verdict=ChannelVerdictKind.POINT,
                refusal_code=None,
            )

        import simulator.vapour_rail.channels as channel_module
        original_resolve = channel_module.resolve_channel_potential
        channel_module.resolve_channel_potential = _admit
        try:
            assert production_seam_refusal("S2", 900.0) is None
        finally:
            channel_module.resolve_channel_potential = original_resolve
    finally:
        bakeout_module._CATALOG_DOMAIN_CACHE = saved_domains


def test_throttle_mass_rate_sums_committed_volatiles_only():
    rate = throttle_mass_rate_kg_hr(
        {"Na": 1.5, "K": 0.25, "Fe": 9.0},
        {"S2": 2.0, "SO2": 3.0, "H2O": 0.5, "CaO": 8.0},
    )
    assert rate == pytest.approx(1.5 + 0.25 + 2.0 + 3.0 + 0.5)


def test_partial_fes_and_sulfate_extents_balance_and_close():
    xi_fes = 0.2
    xi_sulfate = 0.15
    troilite_maps = mol_maps_for_extents([{
        "extent_mol": xi_fes,
        "stoichiometry": _troilite_reaction()["stoichiometry"],
    }])
    sulfate_maps = mol_maps_for_extents([{
        "extent_mol": xi_sulfate,
        "stoichiometry": _sulfate_reaction()["stoichiometry"],
    }])
    for debits, credits in (troilite_maps, sulfate_maps):
        net = _net_element_mol(debits, credits)
        assert net
        for element, amount in net.items():
            assert amount == pytest.approx(0.0, abs=1e-12), element

    sim = _load({
        "label": "hosts",
        "composition_wt_pct": {
            "SiO2": 84.0,
            "FeS_troilite": 8.0,
            "CaSO4": 8.0,
        },
    })
    host_before = sim.atom_ledger.mol_by_account(SALT_FEED_ACCOUNT)
    assert host_before["FeS_troilite"] > xi_fes
    assert host_before["CaSO4"] > xi_sulfate
    _commit(sim, "subsolidus_bakeout", *troilite_maps)
    _commit(sim, "subsolidus_bakeout", *sulfate_maps)

    salt = sim.atom_ledger.mol_by_account(SALT_FEED_ACCOUNT)
    metal = sim.atom_ledger.mol_by_account(METAL_ACCOUNT)
    foulant = sim.atom_ledger.mol_by_account(FOULANT_ACCOUNT)
    gas = sim.atom_ledger.mol_by_account(OVERHEAD_GAS_ACCOUNT)
    assert salt["FeS_troilite"] == pytest.approx(host_before["FeS_troilite"] - xi_fes)
    assert salt["CaSO4"] == pytest.approx(host_before["CaSO4"] - xi_sulfate)
    assert metal["Fe"] == pytest.approx(xi_fes)
    assert foulant["CaO"] == pytest.approx(xi_sulfate)
    assert gas["S2"] == pytest.approx(0.5 * xi_fes)
    assert gas["SO2"] == pytest.approx(xi_sulfate)
    assert gas["O2"] == pytest.approx(0.5 * xi_sulfate)
    assert sim._make_snapshot().mass_balance_error_pct == pytest.approx(0.0, abs=5e-12)

    _arm_c0(sim)
    retained = dict(salt)
    sim._apply_c0_bakeout(liquid_fraction=None, evaluator_loader=lambda: None)
    sim._apply_c0_bakeout(liquid_fraction=None, evaluator_loader=lambda: None)
    assert sim.atom_ledger.mol_by_account(SALT_FEED_ACCOUNT)["FeS_troilite"] == pytest.approx(
        retained["FeS_troilite"]
    )
    assert sim.atom_ledger.mol_by_account(SALT_FEED_ACCOUNT)["CaSO4"] == pytest.approx(
        retained["CaSO4"]
    )


def test_partial_release_updates_the_target_and_a_refusal_does_not():
    sim = _load({
        "label": "troilite only",
        "composition_wt_pct": {"SiO2": 90.0, "FeS_troilite": 10.0},
    })
    initial = sim.atom_ledger.mol_by_account(SALT_FEED_ACCOUNT)["FeS_troilite"]
    xi = 0.25 * initial
    debits, credits = mol_maps_for_extents([{
        "extent_mol": xi,
        "stoichiometry": _troilite_reaction()["stoichiometry"],
    }])
    _commit(sim, "subsolidus_bakeout", debits, credits)
    assert sim.melt.hour == 0
    assert _s2_fraction(sim) == pytest.approx((initial - xi) / initial)
    assert sim._last_target_inventory_diagnostic["depletion_hour"] is None
    assert sim._last_target_inventory_diagnostic["would_be_inventory_advance"] is False
    assert sim._last_target_inventory_diagnostic["stage0_release_kinetics"] == (
        STAGE0_RELEASE_KINETICS
    )

    def _refuse(**_kwargs):
        return [{
            "reaction_id": "troilite_decomposition",
            "status": "thermochemistry_missing",
            "driving_gas_species_id": "S2",
            "stoichiometry": _troilite_reaction()["stoichiometry"],
            "extent_bound_mol_rxn": None,
            "equilibrium_gas_partial_pressures_Pa": None,
        }]

    _arm_c0(sim)
    refused = sim._apply_c0_bakeout(
        liquid_fraction=None,
        evaluator_loader=lambda: _refuse,
    )
    assert refused["committed"] is False
    assert refused["reactions"][0]["reason"] == "thermochemistry_missing"
    assert sim.atom_ledger.mol_by_account(SALT_FEED_ACCOUNT)["FeS_troilite"] == pytest.approx(
        initial - xi
    )
    assert _s2_fraction(sim) == pytest.approx((initial - xi) / initial)

    remaining = sim.atom_ledger.mol_by_account(SALT_FEED_ACCOUNT)["FeS_troilite"]
    rest_debits, rest_credits = mol_maps_for_extents([{
        "extent_mol": remaining,
        "stoichiometry": _troilite_reaction()["stoichiometry"],
    }])
    _commit(sim, "subsolidus_bakeout", rest_debits, rest_credits)
    sim.melt.hour = 5
    sim._update_target_inventory_diagnostic()
    assert sim._last_target_inventory_diagnostic["depleted"] is True
    assert sim._last_target_inventory_diagnostic["depletion_hour"] == 5
    assert sim._last_target_inventory_diagnostic["would_be_inventory_advance"] is False
    assert sim._make_snapshot().mass_balance_error_pct == pytest.approx(0.0, abs=5e-12)


def test_missing_openimcc_and_the_seam_block_release():
    sim = _load({
        "label": "troilite",
        "composition_wt_pct": {"SiO2": 92.0, "FeS_troilite": 8.0},
    })
    _arm_c0(sim)
    before = sim.atom_ledger.mol_by_account(SALT_FEED_ACCOUNT)["FeS_troilite"]
    missing = sim._apply_c0_bakeout(
        liquid_fraction=None,
        evaluator_loader=lambda: None,
    )
    assert missing["committed"] is False
    assert missing["reactions"][0]["reason"] == OPENIMCC_UNAVAILABLE_REASON
    assert sim.atom_ledger.mol_by_account(SALT_FEED_ACCOUNT)["FeS_troilite"] == pytest.approx(
        before
    )

    def _supported(**_kwargs):
        return [{
            "reaction_id": "troilite_decomposition",
            "status": "supported",
            "driving_gas_species_id": "S2",
            "stoichiometry": _troilite_reaction()["stoichiometry"],
            "equilibrium_gas_partial_pressures_Pa": {"S2": 12.0},
            "extent_bound_mol_rxn": before,
        }]

    blocked = sim._apply_c0_bakeout(
        liquid_fraction=None,
        evaluator_loader=lambda: _supported,
    )
    assert blocked["committed"] is False
    assert blocked["reactions"][0]["reason"] == (
        "refused_sulfur_reservoir_owner_missing"
    )
    assert blocked["provider_status"] is None
    assert sim.atom_ledger.mol_by_account(SALT_FEED_ACCOUNT)["FeS_troilite"] == pytest.approx(
        before
    )

    admitted = sim._apply_c0_bakeout(
        liquid_fraction=None,
        evaluator_loader=lambda: _supported,
        seam_refusal_for=lambda _species, _temperature_K: None,
    )
    assert admitted["committed"] is False
    assert admitted["provider_status"] == "refused"
    assert admitted["provider_reason"] == "missing_solid_area"
    assert sim.atom_ledger.mol_by_account(SALT_FEED_ACCOUNT)["FeS_troilite"] == pytest.approx(
        before
    )


def test_disagreeing_driver_pressures_are_not_collapsed():
    stoich = _sulfate_reaction()["stoichiometry"]
    magnesium = [
        {"species_id": "MgSO4", "phase": "crystalline anhydrous magnesium sulfate", "nu_mol_per_mol_rxn": -1.0},
        {"species_id": "MgO", "phase": "crystal", "nu_mol_per_mol_rxn": 1.0},
        {"species_id": "SO2", "phase": "gas", "nu_mol_per_mol_rxn": 1.0},
        {"species_id": "O2", "phase": "gas", "nu_mol_per_mol_rxn": 0.5},
    ]
    hosts = ({"species_id": "CaSO4"}, {"species_id": "MgSO4"})

    def _row(reaction_id, pressure):
        return {
            "reaction_id": reaction_id,
            "status": "supported",
            "driving_gas_species_id": "SO2",
            "stoichiometry": stoich if reaction_id.startswith("anhydrite") else magnesium,
            "extent_bound_mol_rxn": 1.0,
            "equilibrium_gas_partial_pressures_Pa": {"SO2": pressure},
        }

    withheld = partition_openimcc_results(
        hosts,
        (_row("anhydrite_decomposition", 10.0), _row("magnesium_sulfate_decomposition", 12.0)),
        host_remaining_mol={"CaSO4": 1.0, "MgSO4": 1.0},
        temperature_K=900.0,
        seam_refusal_for=lambda _species, _temperature_K: None,
    )
    assert withheld["provider_reactions"] == []
    assert withheld["vapour_batch_flux_pressures_Pa"] == {}
    assert {row["reason"] for row in withheld["rows"]} == {
        "conflicting_driver_equilibrium_pressure",
    }

    shared = partition_openimcc_results(
        hosts,
        (_row("anhydrite_decomposition", 10.0), _row("magnesium_sulfate_decomposition", 10.0)),
        host_remaining_mol={"CaSO4": 1.0, "MgSO4": 1.0},
        temperature_K=900.0,
        seam_refusal_for=lambda _species, _temperature_K: None,
    )
    assert shared["vapour_batch_flux_pressures_Pa"] == {"SO2": 10.0}
    assert len(shared["provider_reactions"]) == 2


def test_first_melt_hands_hosts_and_oxides_once_and_leaves_iron():
    assert first_melt_backstop(None) is False
    assert first_melt_backstop(0.0) is False
    assert first_melt_backstop(True) is False
    assert first_melt_backstop(0.2) is True

    sim = _load({
        "label": "handoff",
        "composition_wt_pct": {
            "SiO2": 84.0,
            "FeS_troilite": 8.0,
            "CaSO4": 8.0,
        },
    })
    fes = sim.atom_ledger.mol_by_account(SALT_FEED_ACCOUNT)["FeS_troilite"]
    caso4 = sim.atom_ledger.mol_by_account(SALT_FEED_ACCOUNT)["CaSO4"]
    xi_fe = 0.1
    xi_ca = 0.1
    _commit(sim, "subsolidus_bakeout", *mol_maps_for_extents([{
        "extent_mol": xi_fe,
        "stoichiometry": _troilite_reaction()["stoichiometry"],
    }]))
    _commit(sim, "subsolidus_bakeout", *mol_maps_for_extents([{
        "extent_mol": xi_ca,
        "stoichiometry": _sulfate_reaction()["stoichiometry"],
    }]))
    sim.atom_ledger.load_external(
        SALT_FEED_ACCOUNT,
        {"SO3": 0.2},
        source="unrelated salt",
        material_origin="feedstock",
    )
    sim.atom_ledger.load_external(
        FOULANT_ACCOUNT,
        {"SiO2": 0.3},
        source="unrelated foulant",
        material_origin="feedstock",
    )
    _arm_c0(sim)
    fraction_before = _s2_fraction(sim)
    first = sim._apply_c0_bakeout(
        liquid_fraction=0.2,
        evaluator_loader=lambda: None,
    )
    assert first["handoff"] is True
    assert first["committed"] is False
    melt = sim.atom_ledger.mol_by_account(CLEANED_MELT_ACCOUNT)
    assert melt["FeS_troilite"] == pytest.approx(fes - xi_fe)
    assert melt["CaSO4"] == pytest.approx(caso4 - xi_ca)
    assert melt["CaO"] == pytest.approx(xi_ca)
    assert melt.get("Fe", 0.0) == pytest.approx(0.0, abs=1e-15)
    assert sim.atom_ledger.mol_by_account(METAL_ACCOUNT)["Fe"] == pytest.approx(xi_fe)
    assert sim.atom_ledger.mol_by_account(SALT_FEED_ACCOUNT).get("FeS_troilite", 0.0) == pytest.approx(0.0, abs=1e-15)
    assert sim.atom_ledger.mol_by_account(SALT_FEED_ACCOUNT)["SO3"] > 0.0
    assert sim.atom_ledger.mol_by_account(FOULANT_ACCOUNT).get("CaO", 0.0) == pytest.approx(0.0, abs=1e-15)
    assert sim.atom_ledger.mol_by_account(FOULANT_ACCOUNT)["SiO2"] == pytest.approx(0.3 / _molar("SiO2"))
    assert _s2_fraction(sim) == pytest.approx(fraction_before)
    second = sim._apply_c0_bakeout(
        liquid_fraction=0.2,
        evaluator_loader=lambda: None,
    )
    assert second["handoff"] is False
    assert sim.atom_ledger.mol_by_account(CLEANED_MELT_ACCOUNT)["FeS_troilite"] == pytest.approx(
        fes - xi_fe
    )


def test_frozen_c0_exit_then_melting_c0b_does_not_evaluate():
    calls = {"n": 0}

    def _loader():
        calls["n"] += 1
        return None

    sim = _campaign_sim("feed", {
        "label": "frozen then melt",
        "composition_wt_pct": {"SiO2": 92.0, "CaSO4": 8.0},
    })
    salt_before = sim.atom_ledger.mol_by_account(SALT_FEED_ACCOUNT)["CaSO4"]
    _arm_c0(sim, temperature_C=600.0)
    frozen = sim._apply_c0_bakeout(liquid_fraction=0.0, evaluator_loader=_loader)
    assert calls["n"] == 1
    assert frozen["handoff"] is False
    assert frozen["committed"] is False
    assert sim.atom_ledger.mol_by_account(SALT_FEED_ACCOUNT)["CaSO4"] == pytest.approx(
        salt_before
    )

    sim.melt.campaign_hour = 25
    assert sim.campaign_mgr.check_endpoint(
        sim.melt,
        EvaporationFlux(),
        CondensationTrain(),
        sim.record,
    ) is True
    assert sim.campaign_mgr.last_c0_exit["outcome"] == "incomplete"
    assert sim.campaign_mgr.last_c0_exit["backstop"] == "maximum_hold"

    sim.melt.campaign = CampaignPhase.C0B
    melted = sim._apply_c0_bakeout(liquid_fraction=0.2, evaluator_loader=_loader)
    assert calls["n"] == 1
    assert melted["handoff"] is True
    assert melted["committed"] is False
    assert sim.atom_ledger.mol_by_account(SALT_FEED_ACCOUNT).get("CaSO4", 0.0) == (
        pytest.approx(0.0, abs=1e-12)
    )
    assert sim.atom_ledger.mol_by_account(CLEANED_MELT_ACCOUNT)["CaSO4"] == (
        pytest.approx(salt_before)
    )

    hot_calls = {"n": 0}

    def _hot_loader():
        hot_calls["n"] += 1
        return None

    hot = _campaign_sim("feed", {
        "label": "melt in C0",
        "composition_wt_pct": {"SiO2": 92.0, "CaSO4": 8.0},
    })
    _arm_c0(hot)
    handoff = hot._apply_c0_bakeout(liquid_fraction=0.2, evaluator_loader=_hot_loader)
    assert hot_calls["n"] == 0
    assert handoff["handoff"] is True
    assert handoff["committed"] is False


def test_production_refusal_and_redox_notice_stay_on_the_run_record():
    def _one(feedstock_key: str):
        sim = _campaign_sim(feedstock_key, {
            "label": feedstock_key,
            "composition_wt_pct": {"SiO2": 92.0, "CaSO4": 8.0},
        })
        _arm_c0(sim, temperature_C=600.0)
        sim.step()
        snapshot = sim.record.snapshots[-1]
        diag = snapshot.subsolidus_bakeout_diagnostic
        assert diag["committed"] is False
        assert diag["notice"] == "redox_back_oxidation_not_modelled"
        assert diag["reactions"]
        assert all(
            row.get("status") == "unavailable" and row.get("reason")
            for row in diag["reactions"]
        )
        assert sim._subsolidus_bakeout_by_hour[-1]["notice"] == diag["notice"]
        summary = sim._capture_campaign_summary("C0")
        assert summary["subsolidus_bakeout"][-1]["notice"] == diag["notice"]
        assert "subsolidus_bakeout_diagnostic" not in build_per_hour_summary(
            sim, snapshot
        )
        kept = list(sim._subsolidus_bakeout_by_hour)
        sim.melt.campaign = CampaignPhase.C0B
        skipped = sim._apply_c0_bakeout(liquid_fraction=0.0)
        assert skipped["skipped"] == "not_c0"
        assert sim._subsolidus_bakeout_by_hour == kept

    _one("ci_carbonaceous_chondrite")
    _one("cm_carbonaceous_chondrite")

    ordinary = _campaign_sim("s_type_asteroid_silicate", {
        "label": "ordinary",
        "composition_wt_pct": {"SiO2": 92.0, "CaSO4": 8.0},
    })
    _arm_c0(ordinary)
    plain = ordinary._apply_c0_bakeout(
        liquid_fraction=0.0,
        evaluator_loader=lambda: None,
    )
    assert plain["notice"] is None


def test_redox_notice_is_feedstock_key_only_and_phosphorus_stays_put():
    ci = _sim({
        "ci_carbonaceous_chondrite": {
            "label": "ci",
            "composition_wt_pct": {"SiO2": 100.0},
        },
    })
    ci.load_batch("ci_carbonaceous_chondrite", mass_kg=10.0)
    _arm_c0(ci)
    ci_diag = ci._apply_c0_bakeout(liquid_fraction=None, evaluator_loader=lambda: None)
    assert ci_diag["notice"] == "redox_back_oxidation_not_modelled"
    assert ci_diag["committed"] is False

    cm = _sim({
        "cm_carbonaceous_chondrite": {
            "label": "cm",
            "composition_wt_pct": {"SiO2": 100.0},
        },
    })
    cm.load_batch("cm_carbonaceous_chondrite", mass_kg=10.0)
    _arm_c0(cm)
    assert cm._apply_c0_bakeout(
        liquid_fraction=None,
        evaluator_loader=lambda: None,
    )["notice"] == "redox_back_oxidation_not_modelled"

    ordinary = _sim({
        "s_type_asteroid_silicate": {
            "label": "s type",
            "composition_wt_pct": {"SiO2": 95.0, "FeS_troilite": 5.0},
        },
    })
    ordinary.load_batch("s_type_asteroid_silicate", mass_kg=10.0)
    _arm_c0(ordinary)
    ordinary_diag = ordinary._apply_c0_bakeout(
        liquid_fraction=None,
        evaluator_loader=lambda: None,
    )
    assert ordinary_diag["notice"] is None

    lunar = _load({
        "label": "elemental sulfur",
        "composition_wt_pct": {"SiO2": 95.0, "S": 3.0, "P2O5": 2.0},
    })
    assert "FeS_troilite" not in (
        lunar.atom_ledger.mol_by_account(SALT_FEED_ACCOUNT) or {}
    )
    phosphorus_before = sum(
        float(species_kg.get("P2O5", 0.0) or 0.0)
        for species_kg in lunar.atom_ledger.kg_by_account().values()
    )
    assert phosphorus_before > 0.0
    _arm_c0(lunar)
    lunar_diag = lunar._apply_c0_bakeout(
        liquid_fraction=None,
        evaluator_loader=lambda: None,
    )
    assert lunar_diag["notice"] is None
    assert lunar_diag["reactions"] == []
    phosphorus_after = sum(
        float(species_kg.get("P2O5", 0.0) or 0.0)
        for species_kg in lunar.atom_ledger.kg_by_account().values()
    )
    assert phosphorus_after == pytest.approx(phosphorus_before)
    assert lunar.atom_ledger.mol_by_account(OVERHEAD_GAS_ACCOUNT).get("P2", 0.0) == 0.0
