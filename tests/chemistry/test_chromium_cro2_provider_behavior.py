from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from engines.builtin.condensation_route import BuiltinCondensationRouteProvider
from engines.builtin.evaporation_transition import (
    BuiltinEvaporationTransitionProvider,
)
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.vaporock import VapoRockBackend
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.state import CampaignPhase, MOLAR_MASS


ROOT = Path(__file__).resolve().parents[2]


def _registry():
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {"chromia": {"label": "Chromia", "composition_wt_pct": {"Cr2O3": 100.0}}},
        {"metals": {}, "oxide_vapors": {}},
    )
    return sim.species_formula_registry


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((ROOT / "data" / name).read_text())


def _cro2_sp_data():
    return {
        "parent_oxide": "Cr2O3",
        "stoich_oxide_per_vapor": 0.5 * MOLAR_MASS["Cr2O3"] / MOLAR_MASS["CrO2"],
        "stoich_O2_per_vapor": -0.25 * MOLAR_MASS["O2"] / MOLAR_MASS["CrO2"],
        "condensation_products_mol_per_mol_vapor": {
            "Cr2O3": 0.5,
            "O2": 0.25,
        },
        "condensation_product_accounts": {
            "Cr2O3": "terminal.chromium_condensed_oxide_stored",
            "O2": "process.overhead_gas",
        },
    }


def test_evaporation_transition_debits_o2_reactant_for_cro2():
    registry = _registry()
    provider = BuiltinEvaporationTransitionProvider()
    sp_data = _cro2_sp_data()
    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_TRANSITION,
        temperature_C=1600.0,
        pressure_bar=1e-6,
        fO2_log=-8.0,
        account_view=ProviderAccountView(
            accounts={
                "process.cleaned_melt": {"Cr2O3": 10.0},
                "process.overhead_gas": {"O2": 10.0},
                "process.condensation_train": {},
            },
            species_formula_registry=registry,
        ),
        control_inputs={
            "species": "CrO2",
            "stoich": {
                "parent_oxide": "Cr2O3",
                "oxide_per_product_kg": sp_data["stoich_oxide_per_vapor"],
                "O2_per_product_kg": sp_data["stoich_O2_per_vapor"],
            },
            "sp_data": sp_data,
            "rate_kg_hr": MOLAR_MASS["CrO2"] / 1000.0,
            "remaining_kg_hr": MOLAR_MASS["CrO2"] / 1000.0,
            "dt_hr": 1.0,
            "available_kg": 1.0,
        },
    )

    result = provider.dispatch(request)

    assert result.status == "ok"
    transition = result.transition
    assert transition is not None
    assert transition.debits["process.cleaned_melt"]["Cr2O3"] == pytest.approx(0.5)
    assert transition.debits["process.overhead_gas"]["O2"] == pytest.approx(0.25)
    assert transition.credits["process.overhead_gas"]["CrO2"] == pytest.approx(1.0)


def test_condensation_route_sends_cro2_chromia_to_terminal_and_o2_to_overhead():
    registry = _registry()
    provider = BuiltinCondensationRouteProvider()
    request = IntentRequest(
        intent=ChemistryIntent.CONDENSATION_ROUTE,
        temperature_C=1200.0,
        pressure_bar=1e-6,
        fO2_log=-8.0,
        account_view=ProviderAccountView(
            accounts={
                "process.overhead_gas": {"CrO2": 1.0},
                "process.condensation_train": {},
                "terminal.chromium_condensed_oxide_stored": {},
            },
            species_formula_registry=registry,
        ),
        control_inputs={
            "species": "CrO2",
            "condensed_kg": MOLAR_MASS["CrO2"] / 1000.0,
            "sp_data": _cro2_sp_data(),
            "dt_hr": 1.0,
        },
    )

    result = provider.dispatch(request)

    assert result.status == "ok"
    transition = result.transition
    assert transition is not None
    assert transition.debits["process.overhead_gas"]["CrO2"] == pytest.approx(1.0)
    assert transition.credits["terminal.chromium_condensed_oxide_stored"][
        "Cr2O3"
    ] == pytest.approx(0.5)
    assert transition.credits["process.overhead_gas"]["O2"] == pytest.approx(0.25)
    assert "Cr2O3" not in transition.credits.get("process.condensation_train", {})


def test_vaporock_adapter_accepts_cro2_output_species():
    backend = VapoRockBackend()

    assert "CrO2" in backend.get_vapor_species()
    assert backend._strip_gas_suffix("CrO2(g)") == "CrO2"


def _total_chromium_mol(sim: PyrolysisSimulator) -> float:
    return sum(
        sim.atom_ledger.atom_moles_by_account(account).get("Cr", 0.0)
        for account in sim.atom_ledger.kg_by_account()
    )


def test_lunar_mare_c2a_hot_cro2_trace_closes_chromium_atoms():
    setpoints = copy.deepcopy(_load_yaml("setpoints.yaml"))
    setpoints.setdefault("chemistry_kernel", {})["allow_fallback_vapor"] = True
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        setpoints,
        _load_yaml("feedstocks.yaml"),
        _load_yaml("vapor_pressures.yaml"),
    )
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    initial_cr_mol = _total_chromium_mol(sim)

    # CrO2 vaporization consumes O2. Keep the trace ledger-closed by making the
    # finite pO2 buffer explicit in both the ledger and the run mass input.
    sim.record.additives_kg["O2"] = 1.0
    sim.atom_ledger.load_external(
        "process.overhead_gas", {"O2": 1.0}, source="IW pO2 buffer"
    )
    sim.melt.temperature_C = 1500.0
    sim.start_campaign(CampaignPhase.C2A)

    closures = []
    for _ in range(12):
        snapshot = sim.step()
        closures.append(abs(snapshot.mass_balance_error_pct))

    terminal_chromia = sim.atom_ledger.kg_by_account(
        "terminal.chromium_condensed_oxide_stored"
    )
    train = sim.atom_ledger.kg_by_account("process.condensation_train")
    stage_2 = sim.train.stages[2].collected_kg
    later_stages = [
        stage.collected_kg for stage in sim.train.stages if stage.stage_number != 2
    ]

    assert terminal_chromia["Cr2O3"] > 0.0
    assert "Cr2O3" not in train
    assert stage_2["Cr2O3"] == pytest.approx(terminal_chromia["Cr2O3"])
    assert all("Cr2O3" not in stage for stage in later_stages)
    assert _total_chromium_mol(sim) == pytest.approx(initial_cr_mol, abs=1e-9)
    assert max(closures) <= 5e-12
