"""Physics-grounded regression tests for yield-recipe-investigation R1–R3."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from engines.builtin.electrolysis_step import BuiltinElectrolysisStepProvider
from engines.builtin.metallothermic_step import BuiltinMetallothermicStepProvider
from simulator import mre_ladder
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.core import PyrolysisSimulator
from simulator.electrolysis import ELECTRONS_PER_OXIDE, FARADAY, MOLAR_MASS
from simulator.melt_backend.base import StubBackend
from simulator.run_executor import RunExecutor
from simulator.session import SimSessionConfig
from simulator.state import CampaignPhase, STOICH_RATIOS

FEEDSTOCK = "lunar_mare_low_ti"
MASS_BALANCE_MAX_PCT = 5e-12


def _load(name: str) -> dict:
    return yaml.safe_load((Path(__file__).parent.parent / "data" / name).read_text())


def _session_config(
    *,
    hours: int = 2000,
    c5_enabled: bool = True,
    mre_target_species: str = "",
) -> SimSessionConfig:
    return SimSessionConfig(
        feedstock_id=FEEDSTOCK,
        feedstocks=_load("feedstocks.yaml"),
        setpoints=_load("setpoints.yaml"),
        vapor_pressures=_load("vapor_pressures.yaml"),
        campaign="C0",
        hours=hours,
        backend_name="stub",
        c5_enabled=c5_enabled,
        mre_target_species=mre_target_species if c5_enabled else "",
        mre_max_voltage_V=1.6 if c5_enabled else 0.0,
    )


def _run_pyrolysis_track(
    *,
    hours: int = 2000,
    c5_enabled: bool = True,
    mre_target_species: str = "",
):
    return RunExecutor().execute(
        _session_config(
            hours=hours,
            c5_enabled=c5_enabled,
            mre_target_species=mre_target_species,
        )
    )


def _initial_feo_kg(sim) -> float:
    if sim.record.snapshots:
        for snapshot in sim.record.snapshots:
            feo_pct = snapshot.composition_wt_pct.get("FeO", 0.0)
            if feo_pct > 0.0 and snapshot.melt_mass_kg > 0.0:
                return snapshot.melt_mass_kg * feo_pct / 100.0
    feed = sim.feedstocks[FEEDSTOCK]
    comp = feed.get("composition_wt_pct", {})
    feo_wt = float(comp.get("FeO", 0.0))
    return sim.record.batch_mass_kg * feo_wt / 100.0


def test_c5_limited_mre_current_matches_faraday_scale():
    assert mre_ladder.C5_LIMITED_MRE_CURRENT_A == pytest.approx(1000.0)
    n_e = ELECTRONS_PER_OXIDE["FeO"]
    moles_per_hr = 1000.0 * 3600.0 / (n_e * FARADAY)
    feo_kg_per_hr = moles_per_hr * MOLAR_MASS["FeO"] / 1000.0
    assert feo_kg_per_hr == pytest.approx(1.34, rel=0.02)


def test_c5_provider_faraday_throughput_at_limited_current():
    from tests.chemistry.conftest import _build_sim, _load_yaml

    sim = _build_sim(
        FEEDSTOCK,
        _load_yaml("vapor_pressures.yaml"),
        _load_yaml("feedstocks.yaml"),
        _load_yaml("setpoints.yaml"),
    )
    provider = BuiltinElectrolysisStepProvider()
    feo_kg = sim.melt.composition_kg["FeO"]
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": {
                "FeO": feo_kg / MOLAR_MASS["FeO"] * 1000.0,
            }
        },
        species_formula_registry=sim.species_formula_registry,
    )
    request = IntentRequest(
        intent=ChemistryIntent.ELECTROLYSIS_STEP,
        account_view=view,
        temperature_C=1575.0,
        pressure_bar=0.05,
        control_inputs={
            "voltage_V": 1.0,
            "current_A": mre_ladder.C5_LIMITED_MRE_CURRENT_A,
            "dt_hr": 1.0,
            "allowed_oxides": ["FeO"],
        },
    )
    result = provider.dispatch(request)
    reduced = float(result.diagnostic.get("oxides_reduced_kg", {}).get("FeO", 0.0))
    n_e = ELECTRONS_PER_OXIDE["FeO"]
    faraday_cap = (
        mre_ladder.C5_LIMITED_MRE_CURRENT_A
        * 3600.0
        / (n_e * FARADAY)
        * MOLAR_MASS["FeO"]
        / 1000.0
    )
    assert reduced > 0.0
    assert reduced <= feo_kg
    assert reduced <= faraday_cap
    assert reduced >= faraday_cap * 0.30


def test_na_shuttle_janaf_feo_crossover_is_below_practical_c3_temperature():
    provider = BuiltinMetallothermicStepProvider
    crossover_C = provider._crossover_temperature_C("Na", "Fe")
    assert crossover_C == pytest.approx(1173.4, abs=0.1)
    assert crossover_C < 1200.0


def test_pyrolysis_track_c5_reduces_feo_without_additives():
    result = _run_pyrolysis_track()
    sim = result.simulator
    feo_initial = _initial_feo_kg(sim)
    feo_left = sim.melt.composition_kg.get("FeO", 0.0)
    reduced_pct = (feo_initial - feo_left) / feo_initial * 100.0

    assert feo_initial > 100.0
    assert reduced_pct > 80.0
    assert sim.melt.composition_kg.get("Al2O3", 0.0) > 100.0
    assert sim.melt.composition_kg.get("MgO", 0.0) > 50.0
    assert max(abs(s.mass_balance_error_pct) for s in result.snapshots) < MASS_BALANCE_MAX_PCT


def test_c3_k_entry_transfers_condensed_na_without_native_melt_banking():
    backend = StubBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        _load("setpoints.yaml"),
        _load("feedstocks.yaml"),
        _load("vapor_pressures.yaml"),
    )
    sim.load_batch(FEEDSTOCK, mass_kg=1000.0)
    melt_na2o_before = sim.atom_ledger.kg_by_account("process.cleaned_melt").get(
        "Na2O", 0.0
    )
    o2_before = sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_stored"
    ).get("O2", 0.0)
    condensed_na_kg = 1.9
    sim.atom_ledger.load_external(
        "process.condensation_train",
        {"Na": condensed_na_kg},
        source="test recovered Na",
    )
    sim._init_shuttle_inventory(CampaignPhase.C3_K)

    reagent_na = sim.atom_ledger.kg_by_account("process.reagent_inventory").get(
        "Na", 0.0
    )
    assert reagent_na >= condensed_na_kg
    assert sim.shuttle_Na_inventory_kg == pytest.approx(reagent_na)
    assert sim.atom_ledger.kg_by_account("process.cleaned_melt").get(
        "Na2O", 0.0
    ) == pytest.approx(melt_na2o_before)
    assert sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_stored"
    ).get("O2", 0.0) == pytest.approx(o2_before)


def test_c3_shuttle_injects_na_from_condensed_alkali_alone():
    backend = StubBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        _load("setpoints.yaml"),
        _load("feedstocks.yaml"),
        _load("vapor_pressures.yaml"),
    )
    sim.load_batch(
        FEEDSTOCK,
        mass_kg=1000.0,
        additives_kg={"K": 0.0, "Na": 0.0},
    )
    condensed_na_kg = 1.9
    sim.atom_ledger.load_external(
        "process.condensation_train",
        {"Na": condensed_na_kg},
        source="test recovered Na",
    )
    sim._init_shuttle_inventory(CampaignPhase.C3_K)
    assert sim.shuttle_Na_inventory_kg == pytest.approx(condensed_na_kg)

    sim.melt.campaign = CampaignPhase.C3_K
    sim.melt.temperature_C = 1150.0
    process_before = sim.shuttle_Na_inventory_kg
    sim._shuttle_inject_Na(target_stage="feo_cleanup", liquid_fraction=1.0)

    assert sim.shuttle_Na_inventory_kg < process_before
    assert sim._shuttle_injected_this_hr > 0.0
    assert sim.atom_ledger.kg_by_account("process.metal_phase").get("Fe", 0.0) > 0.0
    sim.atom_ledger.assert_balanced()


def test_pc_extract_fe_target_has_fe_product_after_full_pyrolysis_track():
    result = _run_pyrolysis_track()
    products = result.simulator.product_ledger()
    feo_initial = _initial_feo_kg(result.simulator)
    feo_left = result.simulator.melt.composition_kg.get("FeO", 0.0)
    fe_product = products.get("Fe", 0.0)

    assert (feo_initial - feo_left) / feo_initial > 0.80
    assert fe_product > 80.0


def test_pc_extract_al_remains_infeasible_at_1p6v_c5_cap():
    result = _run_pyrolysis_track()
    al_left = result.simulator.melt.composition_kg.get("Al2O3", 0.0)
    al_product = result.simulator.product_ledger().get("Al", 0.0)
    assert al_left > 100.0
    assert al_product < 5.0