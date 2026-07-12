import math
import types

import pytest

import simulator.evaporation as evaporation_module
from simulator.accounting import AccountingError, MaterialLot
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.state import (
    Atmosphere,
    CampaignPhase,
    EvaporationFlux,
    MOLAR_MASS,
    PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNTS,
)


def _gas_train_sim(mass_kg=100.0):
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {"oxide": {"label": "Oxide", "composition_wt_pct": {"FeO": 100.0}}},
        {
            "metals": {
                "Fe": {
                    "parent_oxide": "FeO",
                },
            },
            "oxide_vapors": {},
        },
    )
    sim.load_batch("oxide", mass_kg=mass_kg)
    return sim


def _sio_train_sim():
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {"silica": {"label": "Silica", "composition_wt_pct": {"SiO2": 100.0}}},
        {
            "metals": {},
            "oxide_vapors": {
                "SiO": {
                    "parent_oxide": "SiO2",
                    "stoich_oxide_per_vapor": (
                        MOLAR_MASS["SiO2"] / MOLAR_MASS["SiO"]),
                    "stoich_O2_per_vapor": (
                        0.5 * MOLAR_MASS["O2"] / MOLAR_MASS["SiO"]),
                    "condensation_products_mol_per_mol_vapor": {
                        "Si": 0.5,
                        "SiO2": 0.5,
                    },
                },
            },
        },
    )
    sim.load_batch("silica", mass_kg=1000.0)
    return sim


def _sio_o2_kg(sio_kg):
    return sio_kg * 0.5 * MOLAR_MASS["O2"] / MOLAR_MASS["SiO"]


def _cro2_train_sim():
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    cr2o3_mw = MOLAR_MASS["Cr2O3"]
    cro2_mw = MOLAR_MASS["CrO2"]
    o2_mw = MOLAR_MASS["O2"]
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {"chromia": {"label": "Chromia", "composition_wt_pct": {"Cr2O3": 100.0}}},
        {
            "metals": {},
            "oxide_vapors": {
                "CrO2": {
                    "parent_oxide": "Cr2O3",
                    "stoich_oxide_per_vapor": 0.5 * cr2o3_mw / cro2_mw,
                    "stoich_O2_per_vapor": -0.25 * o2_mw / cro2_mw,
                    "condensation_products_mol_per_mol_vapor": {
                        "Cr2O3": 0.5,
                        "O2": 0.25,
                    },
                    "condensation_product_accounts": {
                        "Cr2O3": "terminal.chromium_condensed_oxide_stored",
                        "O2": "process.overhead_gas",
                    },
                },
            },
        },
    )
    sim.load_batch("chromia", mass_kg=1000.0)
    return sim


def _bypass_analytic_depletion(sim):
    sim._apply_analytic_evaporation_depletion = lambda flux: flux
    sim.campaign_mgr.check_endpoint = lambda *args, **kwargs: False


def test_turbine_venting_uses_actual_o2_not_total_evaporation_mass():
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {"oxide": {"label": "Oxide", "composition_wt_pct": {"SiO2": 100.0}}},
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("oxide")
    flux = EvaporationFlux(species_kg_hr={"SiO": 100.0}, total_kg_hr=100.0)
    turbine = types.SimpleNamespace(max_O2_flow_kg_hr=1.0)

    overhead = sim.overhead_model.update(
        flux,
        sim.melt,
        sim.train,
        turbine_spec=turbine,
        actual_O2_kg_hr=2.0,
    )

    assert overhead.O2_vented_kg_hr == pytest.approx(1.0)
    assert overhead.turbine_flow_kg_hr == pytest.approx(1.0)


def test_mre_anode_o2_is_not_turbine_throughput():
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {"oxide": {"label": "Oxide", "composition_wt_pct": {"SiO2": 100.0}}},
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("oxide")
    turbine = types.SimpleNamespace(max_O2_flow_kg_hr=0.1)

    overhead = sim.overhead_model.update(
        EvaporationFlux(),
        sim.melt,
        sim.train,
        turbine_spec=turbine,
        actual_O2_kg_hr=0.0,
        actual_O2_mol_hr=0.0,
        mre_anode_O2_mol_hr=10.0,
    )

    assert overhead.mre_anode_O2_mol_hr == pytest.approx(10.0)
    assert overhead.turbine_flow_kg_hr == pytest.approx(0.0)
    assert overhead.O2_vented_kg_hr == pytest.approx(0.0)


def test_gas_train_o2_routes_through_terminal_ledger_not_stage6():
    # Re-speciation contract: SiO still credits overhead O2; metal O stays internal.
    sim = _sio_train_sim()
    sio_kg = 100.0
    o2_from_sio_kg = _sio_o2_kg(sio_kg)
    flux = EvaporationFlux(species_kg_hr={"SiO": sio_kg}, total_kg_hr=sio_kg)

    sim._route_to_condensation(flux)
    sim._update_melt_composition(flux)

    assert sim.atom_ledger.kg_by_account("process.overhead_gas").get(
        "O2", 0.0
    ) == pytest.approx(o2_from_sio_kg)
    assert sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_stored").get("O2", 0.0) == pytest.approx(0.0)
    assert sim._oxygen_total_kg() == pytest.approx(0.0)
    assert sim.oxygen_cumulative_kg == pytest.approx(0.0)
    assert all("O2" not in stage.collected_kg for stage in sim.train.stages)


def test_o2_venting_moves_between_terminal_ledger_accounts():
    # Re-speciation contract: use SiO to exercise overhead O2 vent routing.
    sim = _sio_train_sim()
    sio_kg = 100.0
    o2_from_sio_kg = _sio_o2_kg(sio_kg)
    stored_o2_kg = 10.0
    vented_o2_kg = o2_from_sio_kg - stored_o2_kg
    flux = EvaporationFlux(species_kg_hr={"SiO": sio_kg}, total_kg_hr=sio_kg)
    turbine = types.SimpleNamespace(max_O2_flow_kg_hr=10.0)

    sim._route_to_condensation(flux)
    overhead = sim.overhead_model.update(
        flux,
        sim.melt,
        sim.train,
        turbine_spec=turbine,
        actual_O2_kg_hr=sim.atom_ledger.kg_by_account(
            "process.overhead_gas").get("O2", 0.0),
    )
    sim._dispatch_overhead_bleed(
        turbine_spec=turbine,
        force_drain_all=True,
        o2_vented_kg=overhead.O2_vented_kg_hr,
    )

    assert overhead.O2_vented_kg_hr == pytest.approx(vented_o2_kg)
    assert sim.atom_ledger.kg_by_account("terminal.oxygen_melt_offgas_stored")[
        "O2"
    ] == pytest.approx(stored_o2_kg)
    assert sim.atom_ledger.kg_by_account("terminal.oxygen_melt_offgas_vented_to_vacuum")[
        "O2"
    ] == pytest.approx(vented_o2_kg)
    assert sim._oxygen_total_kg() == pytest.approx(o2_from_sio_kg)
    assert sim.O2_stored_cumulative_kg == pytest.approx(stored_o2_kg)
    assert sim.O2_vented_cumulative_kg == pytest.approx(vented_o2_kg)


def test_step_does_not_double_credit_gas_train_ledger_o2():
    # Re-speciation contract: SiO preserves the overhead O2 source for this invariant.
    sim = _sio_train_sim()
    sio_kg = 100.0
    o2_from_sio_kg = _sio_o2_kg(sio_kg)
    flux = EvaporationFlux(species_kg_hr={"SiO": sio_kg}, total_kg_hr=sio_kg)
    sim.melt.campaign = CampaignPhase.C2A
    sim._update_temperature = lambda: None
    sim._get_equilibrium = lambda: object()
    sim._calculate_evaporation = lambda equilibrium: flux
    _bypass_analytic_depletion(sim)

    sim.step()

    assert sim._oxygen_total_kg() == pytest.approx(o2_from_sio_kg)


def test_step_vents_terminal_stored_evaporation_o2_when_turbine_limited():
    # Re-speciation contract: SiO O2 still enters overhead and can be vented.
    sim = _sio_train_sim()
    sio_kg = 100.0
    o2_from_sio_kg = _sio_o2_kg(sio_kg)
    stored_o2_kg = 10.0
    vented_o2_kg = o2_from_sio_kg - stored_o2_kg
    flux = EvaporationFlux(species_kg_hr={"SiO": sio_kg}, total_kg_hr=sio_kg)
    sim.melt.campaign = CampaignPhase.C2A
    sim._update_temperature = lambda: None
    sim._get_equilibrium = lambda: object()
    sim._calculate_evaporation = lambda equilibrium: flux
    _bypass_analytic_depletion(sim)
    sim._get_turbine_spec = lambda: types.SimpleNamespace(
        max_O2_flow_kg_hr=10.0)

    sim.step()

    assert sim.overhead.O2_vented_kg_hr == pytest.approx(vented_o2_kg)
    assert sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_stored")["O2"] == pytest.approx(stored_o2_kg)
    assert sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_vented_to_vacuum"
    )["O2"] == pytest.approx(vented_o2_kg)
    assert sim._oxygen_total_kg() == pytest.approx(o2_from_sio_kg)


def test_overhead_o2_not_double_counted_across_ticks():
    # Two consecutive steps with evaporation O2. Tick 1's overhead O2 is
    # left undrained (drain stubbed out), so it carries into tick 2's
    # process.overhead_gas holdup. The turbine/vent decision must be fed
    # the ledger holdup itself -- never holdup max()'d with a per-tick
    # production counter, which would let carried-over O2 read as fresh
    # throughput. The invariant: over tick 2, turbine throughput + vent
    # equals the ledger O2 delta into the terminal accounts.
    # Re-speciation contract: SiO still supplies overhead O2 across ticks.
    sim = _sio_train_sim()
    sio_kg = 100.0
    o2_from_sio_kg = _sio_o2_kg(sio_kg)
    flux = EvaporationFlux(species_kg_hr={"SiO": sio_kg}, total_kg_hr=sio_kg)
    sim.melt.campaign = CampaignPhase.C2A
    sim._update_temperature = lambda: None
    sim._get_equilibrium = lambda: object()
    sim._calculate_evaporation = lambda equilibrium: flux
    _bypass_analytic_depletion(sim)
    sim._get_turbine_spec = lambda: types.SimpleNamespace(
        max_O2_flow_kg_hr=10.0)

    def _terminal_o2_kg():
        return sum(
            sim.atom_ledger.kg_by_account(acct).get("O2", 0.0)
            for acct in (
                "terminal.oxygen_stage0_stored",
                "terminal.oxygen_melt_offgas_stored",
                "terminal.oxygen_melt_offgas_vented_to_vacuum",
                "terminal.oxygen_mre_anode_stored",
            )
        )

    # Tick 1: leave the melt/offgas O2 sitting in process.overhead_gas.
    drained = sim._dispatch_overhead_bleed
    sim._dispatch_overhead_bleed = lambda *args, **kwargs: None
    sim.step()
    sim._dispatch_overhead_bleed = drained

    carried_over_kg = sim.atom_ledger.kg_by_account(
        "process.overhead_gas")["O2"]
    assert carried_over_kg == pytest.approx(o2_from_sio_kg)

    # Capture the O2 quantity the turbine model is actually fed on tick 2,
    # and the ledger holdup that exists at that instant.
    seen = {}
    real_update = sim.overhead_model.update

    def _spy_update(*args, **kwargs):
        seen["fed_kg"] = kwargs["actual_O2_kg_hr"]
        seen["holdup_kg"] = sim._ledger_o2_kg("process.overhead_gas")
        return real_update(*args, **kwargs)

    sim.overhead_model.update = _spy_update

    terminal_before = _terminal_o2_kg()
    sim.step()
    sim.overhead_model.update = real_update

    # The turbine sees exactly the finite ledger holdup -- carried-over O2
    # plus this tick's production -- and nothing else.
    assert seen["fed_kg"] == pytest.approx(seen["holdup_kg"])
    assert seen["fed_kg"] > carried_over_kg  # tick-2 production also present

    # Invariant: throughput + vent over tick 2 equals the ledger O2 delta
    # into terminal accounts (not holdup + per-tick production summed).
    o2_per_mol = MOLAR_MASS["O2"] / 1000.0
    throughput_mol_hr = (
        sim.overhead.turbine_flow_mol_hr + sim.overhead.O2_vented_mol_hr)
    terminal_delta_kg = _terminal_o2_kg() - terminal_before

    assert throughput_mol_hr * o2_per_mol == pytest.approx(terminal_delta_kg)
    assert terminal_delta_kg == pytest.approx(seen["holdup_kg"])
    # Carried-over O2 flowed through exactly once -- not double-counted.
    assert sim.atom_ledger.kg_by_account(
        "process.overhead_gas").get("O2", 0.0) == pytest.approx(0.0)


def test_partial_sio_condensation_keeps_overhead_gas_in_mass_balance():
    sim = _sio_train_sim()
    flux = EvaporationFlux(species_kg_hr={"SiO": 100.0}, total_kg_hr=100.0)

    sim._route_to_condensation(flux)
    sim._update_melt_composition(flux)
    snapshot = sim._make_snapshot()

    condensed = sim.atom_ledger.kg_by_account("process.condensation_train")
    overhead = sim.atom_ledger.kg_by_account("process.overhead_gas")["SiO"]
    wall_total = sum(
        sum(sim.atom_ledger.kg_by_account(account).values())
        for account in PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNTS
    )
    stage_totals = sim.train.total_by_species()
    products = sim.product_ledger()
    condensed_total = sum(condensed.values())

    assert overhead > 0.0
    assert wall_total > 0.0
    assert "SiO" not in stage_totals
    assert stage_totals["Si"] == pytest.approx(condensed["Si"])
    assert stage_totals["SiO2"] == pytest.approx(condensed["SiO2"])
    assert condensed_total + overhead + wall_total == pytest.approx(100.0)
    assert products["SiO"] == pytest.approx(overhead)
    assert products["Si"] == pytest.approx(condensed["Si"])
    assert products["SiO2"] == pytest.approx(condensed["SiO2"])
    assert snapshot.mass_balance_error_pct == pytest.approx(0.0)


def test_step_drains_uncondensed_overhead_vapor_each_tick():
    sim = _sio_train_sim()
    flux = EvaporationFlux(species_kg_hr={"SiO": 100.0}, total_kg_hr=100.0)
    sim.melt.campaign = CampaignPhase.C2A
    sim._update_temperature = lambda: None
    sim._get_equilibrium = lambda: object()
    sim._calculate_evaporation = lambda equilibrium: flux
    _bypass_analytic_depletion(sim)
    sim._get_turbine_spec = lambda: types.SimpleNamespace(
        max_O2_flow_kg_hr=1.0e9)

    sim.step()
    first_terminal = sim.atom_ledger.kg_by_account(
        "terminal.offgas").get("SiO", 0.0)
    sim.step()

    assert sim.atom_ledger.kg_by_account(
        "process.overhead_gas").get("SiO", 0.0) == pytest.approx(0.0)
    assert sim.atom_ledger.kg_by_account("terminal.offgas")[
        "SiO"
    ] == pytest.approx(2.0 * first_terminal)


def test_condensation_projection_waits_for_ledger_credit():
    sim = _sio_train_sim()
    flux = EvaporationFlux(species_kg_hr={"SiO": 100.0}, total_kg_hr=100.0)
    ledger_melt = sim.atom_ledger.kg_by_account("process.cleaned_melt")
    sim.atom_ledger.transfer(
        "empty_cleaned_melt_before_route",
        debits=(
            MaterialLot(
                "process.cleaned_melt",
                ledger_melt,
                source="test empties ledger melt",
            ),
        ),
        credits=(
            MaterialLot(
                "terminal.slag",
                ledger_melt,
                source="test empties ledger melt",
            ),
        ),
        reason="force no available parent oxide",
    )

    sim._route_to_condensation(flux)

    assert sim.atom_ledger.kg_by_account(
        "process.condensation_train").get("SiO", 0.0) == pytest.approx(0.0)
    assert all(
        stage.collected_kg.get("SiO", 0.0) == pytest.approx(0.0)
        for stage in sim.train.stages
    )
    assert all(
        stage.collected_kg.get("Si", 0.0) == pytest.approx(0.0)
        and stage.collected_kg.get("SiO2", 0.0) == pytest.approx(0.0)
        for stage in sim.train.stages
    )


def test_sio_vapor_requires_explicit_stoich_metadata():
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {"silica": {"label": "Silica", "composition_wt_pct": {"SiO2": 100.0}}},
        {
            "metals": {},
            "oxide_vapors": {
                "SiO": {
                    "parent_oxide": "SiO2",
                },
            },
        },
    )
    sim.load_batch("silica", mass_kg=1000.0)
    flux = EvaporationFlux(species_kg_hr={"SiO": 100.0}, total_kg_hr=100.0)

    with pytest.raises(AccountingError, match="SiO.*stoich"):
        sim._route_to_condensation(flux)

    assert all("SiO" not in stage.collected_kg for stage in sim.train.stages)


def test_vapor_species_without_parent_oxide_fails_before_flux():
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {"oxide": {"label": "Oxide", "composition_wt_pct": {"FeO": 100.0}}},
        {
            "metals": {
                "Fe": {
                    "molar_mass_g_mol": MOLAR_MASS["Fe"],
                },
            },
            "oxide_vapors": {},
        },
    )
    sim.load_batch("oxide", mass_kg=1000.0)
    sim.melt.temperature_C = 1000.0
    equilibrium = types.SimpleNamespace(vapor_pressures_Pa={"Fe": 1.0e5})

    with pytest.raises(AccountingError, match="parent_oxide"):
        sim._calculate_evaporation(equilibrium)


def test_sio_vapor_explicit_stoich_must_mass_close():
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {"silica": {"label": "Silica", "composition_wt_pct": {"SiO2": 100.0}}},
        {
            "metals": {},
            "oxide_vapors": {
                "SiO": {
                    "parent_oxide": "SiO2",
                    "stoich_oxide_per_vapor": 1.36,
                    "stoich_O2_per_vapor": 0.20,
                },
            },
        },
    )
    sim.load_batch("silica", mass_kg=1000.0)
    flux = EvaporationFlux(species_kg_hr={"SiO": 100.0}, total_kg_hr=100.0)

    with pytest.raises(AccountingError, match="conserve mass"):
        sim._route_to_condensation(flux)

    assert all("SiO" not in stage.collected_kg for stage in sim.train.stages)


def test_intact_oxide_vapor_allows_zero_o2_stoich():
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {"feo": {"label": "FeO", "composition_wt_pct": {"FeO": 100.0}}},
        {
            "metals": {},
            "oxide_vapors": {
                "FeO": {
                    "parent_oxide": "FeO",
                    "stoich_oxide_per_vapor": 1.0,
                    "stoich_O2_per_vapor": 0.0,
                },
            },
        },
    )
    sim.load_batch("feo", mass_kg=1000.0)
    flux = EvaporationFlux(species_kg_hr={"FeO": 100.0}, total_kg_hr=100.0)

    sim._route_to_condensation(flux)
    sim._update_melt_composition(flux)
    sim._dispatch_overhead_bleed(
        turbine_spec=types.SimpleNamespace(max_O2_flow_kg_hr=0.0),
        force_drain_all=True,
        o2_vented_kg=0.0,
    )

    products = sim.product_ledger()
    assert products["FeO"] == pytest.approx(100.0)
    assert sim._oxygen_terminal_partition_kg()["total"] == pytest.approx(0.0)
    assert sim._make_snapshot().mass_balance_error_pct == pytest.approx(0.0)


def test_explicit_vapor_stoich_must_conserve_atoms_not_just_mass():
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {"silica": {"label": "Silica", "composition_wt_pct": {"SiO2": 100.0}}},
        {
            "metals": {},
            "oxide_vapors": {
                "Fe": {
                    "parent_oxide": "SiO2",
                    "stoich_oxide_per_vapor": 2.0,
                    "stoich_O2_per_vapor": 1.0,
                },
            },
        },
    )
    sim.load_batch("silica", mass_kg=1000.0)
    flux = EvaporationFlux(species_kg_hr={"Fe": 100.0}, total_kg_hr=100.0)

    with pytest.raises(AccountingError, match="conserve .* atoms"):
        sim._route_to_condensation(flux)


def test_cro2_condenses_to_terminal_chromium_oxide_account():
    sim = _cro2_train_sim()
    sim.atom_ledger.load_external(
        "process.overhead_gas", {"O2": 1.0}, source="test pO2 buffer"
    )
    flux = EvaporationFlux(species_kg_hr={"CrO2": 1.0}, total_kg_hr=1.0)

    sim._route_to_condensation(flux)
    sim._update_melt_composition(flux)

    chromium = sim.atom_ledger.kg_by_account(
        "terminal.chromium_condensed_oxide_stored"
    )
    train = sim.atom_ledger.kg_by_account("process.condensation_train")
    stage_totals = sim.train.total_by_species()

    assert chromium["Cr2O3"] > 0.9
    assert "Cr2O3" not in train
    assert stage_totals["Cr2O3"] == pytest.approx(chromium["Cr2O3"])
    assert stage_totals.get("O2", 0.0) == pytest.approx(0.0)


def test_explicit_ferric_to_wustite_vapor_stoich_is_atom_checked():
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    oxide_per_feo = 0.5 * MOLAR_MASS["Fe2O3"] / MOLAR_MASS["FeO"]
    o2_per_feo = 0.25 * MOLAR_MASS["O2"] / MOLAR_MASS["FeO"]
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {"ferric": {"label": "Ferric", "composition_wt_pct": {"Fe2O3": 100.0}}},
        {
            "metals": {},
            "oxide_vapors": {
                "FeO": {
                    "parent_oxide": "Fe2O3",
                    "stoich_oxide_per_vapor": oxide_per_feo,
                    "stoich_O2_per_vapor": o2_per_feo,
                },
            },
        },
    )
    sim.load_batch("ferric", mass_kg=1000.0)
    flux = EvaporationFlux(species_kg_hr={"FeO": 100.0}, total_kg_hr=100.0)

    sim._route_to_condensation(flux)
    sim._update_melt_composition(flux)
    sim._dispatch_overhead_bleed(
        turbine_spec=types.SimpleNamespace(max_O2_flow_kg_hr=0.0),
        force_drain_all=True,
        o2_vented_kg=0.0,
    )

    products = sim.product_ledger()
    assert products["FeO"] == pytest.approx(100.0)
    assert sim._oxygen_terminal_partition_kg()["total"] == pytest.approx(
        100.0 * o2_per_feo)
    assert sim._make_snapshot().mass_balance_error_pct == pytest.approx(0.0)


def test_condensation_route_cannot_return_more_remaining_vapor_than_input():
    sim = _sio_train_sim()
    sp_data = sim.vapor_pressures["oxide_vapors"]["SiO"]

    with pytest.raises(AccountingError, match="unphysical remaining"):
        sim._credit_evaporation_transition("SiO", 100.0, 100.1, sp_data)


def test_elemental_stoich_fallback_is_mass_and_atom_checked(monkeypatch):
    sim = _gas_train_sim()
    monkeypatch.setitem(evaporation_module.STOICH_RATIOS, "FeO", (0.70, 0.25))

    with pytest.raises(AccountingError, match="STOICH_RATIOS"):
        sim._evaporation_stoich("Fe", {"parent_oxide": "FeO"})


def _sio_o2_train_sim():
    """SiO + O2 train with explicit Antoine data so the builtin
    _internal_analytical_equilibrium actually emits a SiO vapor pressure and the
    SiO √pO₂ suppression path is exercised."""
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {
            "campaigns": {},
            "chemistry_kernel": {
                "allow_fallback_vapor": True,
                "allow_unmeasured_alpha_fallback": True,
            },
        },
        {"silica": {"label": "Silica", "composition_wt_pct": {"SiO2": 100.0}}},
        {
            "metals": {},
            "oxide_vapors": {
                "SiO": {
                    "parent_oxide": "SiO2",
                    "molar_mass_g_mol": MOLAR_MASS["SiO"],
                    "antoine": {"A": 11.817, "B": 18700, "C": 0},
                    "valid_range_K": [1400, 2200],
                    "stoich_oxide_per_vapor": (
                        MOLAR_MASS["SiO2"] / MOLAR_MASS["SiO"]),
                    "stoich_O2_per_vapor": (
                        0.5 * MOLAR_MASS["O2"] / MOLAR_MASS["SiO"]),
                    "condensation_products_mol_per_mol_vapor": {
                        "Si": 0.5,
                        "SiO2": 0.5,
                    },
                },
            },
        },
    )
    sim.load_batch("silica", mass_kg=1000.0)
    return sim


def test_internal_analytical_equilibrium_ok_paths_are_vapor_only_without_liquid_fraction():
    sim = _sio_o2_train_sim()

    sim.melt.temperature_C = 25.0
    cold = sim._internal_analytical_equilibrium()
    assert cold.status == "ok"
    assert cold.liquid_fraction is None
    assert cold.phase_assemblage_available is False

    sim.melt.temperature_C = 1600.0
    hot = sim._internal_analytical_equilibrium()
    assert hot.status == "ok"
    assert hot.liquid_fraction is None
    assert hot.phase_assemblage_available is False
    assert hot.vapor_pressures_Pa


def test_fe_redox_and_sio_use_coupled_oxygen_reservoirs():
    """SSO-R Phase 1 couples, but does not force-equalize, melt/headspace O2.

    The old pin asserted independent intrinsic and commanded oxygen signals.
    That was the bug: Fe-redox and SiO transport could read unrelated values in
    the same tick. The corrected physical invariant is non-equilibrium with a
    conserved O2 exchange that reduces the oxygen-potential gap.
    """
    sim = _sio_o2_train_sim()
    sim.melt.temperature_C = 1600.0
    sim.melt.campaign = CampaignPhase.C2B
    sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
    sim.melt.pO2_mbar = 1.5
    sim.melt.p_total_mbar = 1.5
    sim._overhead_headspace_config["enabled"] = True

    sim.vapor_pressures["metals"]["Fe"] = {
        "parent_oxide": "FeO",
        "molar_mass_g_mol": MOLAR_MASS["Fe"],
        "antoine": {"A": 10.0, "B": 20000.0, "C": 0.0},
        "valid_range_K": [1400.0, 2200.0],
    }
    feo_kg = 0.1
    headspace_o2_mol = 0.1
    sim.inventory.melt_oxide_kg["FeO"] = feo_kg
    sim.melt.composition_kg["FeO"] = feo_kg
    sim.atom_ledger.load_external_mol(
        "process.cleaned_melt",
        {"FeO": feo_kg / (MOLAR_MASS["FeO"] / 1000.0)},
        source="test FeO redox capacity",
    )
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"O2": headspace_o2_mol},
        source="test finite headspace O2 holdup",
    )

    before_fO2 = sim._compute_intrinsic_melt_fO2()
    before_headspace = sim._headspace_transport_pO2_bar_from_ledger(
        sim._headspace_ledger_pO2_bar_from_o2_mol(headspace_o2_mol)
    )
    before_gap = abs(math.log10(before_headspace) - before_fO2)

    reservoir = sim._apply_oxygen_reservoir_exchange()

    assert reservoir.exchange_transition_name == "oxygen_reservoir_exchange"
    assert reservoir.exchange_clamped is False
    assert reservoir.exchange_direction in {
        "melt_to_headspace",
        "headspace_to_melt",
    }
    transitions = [
        t for t in sim.atom_ledger.transitions
        if t.name == "oxygen_reservoir_exchange"
    ]
    assert transitions
    transitions[-1].validate_conservation(sim.atom_ledger.registry)
    touched = {
        lot.account
        for lot in transitions[-1].debits + transitions[-1].credits
    }
    assert touched == {"reservoir.fo2_buffer", "process.overhead_gas"}

    after_gap = abs(
        math.log10(reservoir.headspace_transport_pO2_bar)
        - reservoir.melt_intrinsic_fO2_log
    )
    assert after_gap < before_gap
    assert reservoir.headspace_transport_pO2_bar != pytest.approx(
        10.0 ** reservoir.melt_intrinsic_fO2_log
    )

    real_transport = sim._headspace_transport_pO2_bar
    calls = []

    def _spy_transport():
        value = real_transport()
        calls.append(value)
        return value

    sim._headspace_transport_pO2_bar = _spy_transport

    equilibrium = sim._internal_analytical_equilibrium()
    assert calls, "_internal_analytical_equilibrium must consult _headspace_transport_pO2_bar"
    assert calls[0] == pytest.approx(reservoir.headspace_transport_pO2_bar)
    assert equilibrium.fO2_log == pytest.approx(
        reservoir.melt_intrinsic_fO2_log
    )
    assert equilibrium.activity_coefficients["Fe"] > 0.0
    assert equilibrium.vapor_pressures_Pa.get("SiO", 0.0) > 0.0

    sim._headspace_transport_pO2_bar = real_transport


def test_evaporation_flux_does_not_reapply_commanded_po2():
    """EVAPORATION_FLUX consumes P_eq; pO2 is owned by VAPOR_PRESSURE."""
    sim = _sio_o2_train_sim()
    sim.melt.temperature_C = 1600.0
    sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
    sim.melt.pO2_mbar = 1.5
    sim.overhead.composition = {"O2": 1.5}
    sim._overhead_headspace_config["enabled"] = True

    real_commanded = sim._commanded_pO2_bar
    calls = []

    def _spy_commanded():
        calls.append(True)
        return real_commanded()

    sim._commanded_pO2_bar = _spy_commanded
    equilibrium = sim._internal_analytical_equilibrium()
    calls.clear()
    flux = sim._calculate_evaporation(equilibrium)
    sim._commanded_pO2_bar = real_commanded

    assert flux is not None
    assert not calls, "_calculate_evaporation must not reapply gas pO2"


def test_equilibrium_does_not_emit_o2_vapor_species():
    """_internal_analytical_equilibrium only ever writes metal + declared oxide vapors
    into vapor_pressures_Pa -- never an 'O2' key. This pins the
    Finding 2 dead-branch removal in _calculate_evaporation: the
    `if species == 'O2'` ambient-pressure branch was unreachable because
    'O2' is never a vapor species. Do not re-add it."""
    sim = _sio_o2_train_sim()
    sim.melt.temperature_C = 1600.0

    equilibrium = sim._get_equilibrium()

    assert "O2" not in equilibrium.vapor_pressures_Pa
    # The suppression path is still exercised: a real oxide vapor is there.
    assert equilibrium.vapor_pressures_Pa.get("SiO", 0.0) > 0.0


def test_commanded_po2_has_no_synthetic_floor_in_hard_vacuum():
    """An uncontrolled hard-vacuum run must NOT get a synthetic setpoint
    floor on pO₂ -- the setpoint only floors under active O₂ control. Under
    HARD_VACUUM the commanded pO₂ is the numerical vacuum floor for the
    whole campaign (the turbine-control feedback loop is not wired)."""
    sim = _sio_o2_train_sim()
    sim.melt.atmosphere = Atmosphere.HARD_VACUUM
    sim.melt.pO2_mbar = 1.5  # stale/irrelevant setpoint under hard vacuum
    sim.overhead.composition = {"O2": 0.0}

    # Only the numerical divide-by-zero guard applies, never the setpoint.
    assert sim._commanded_pO2_bar() == pytest.approx(1e-9)

    # Under active O₂ control the same setpoint DOES floor the value.
    sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
    assert sim._commanded_pO2_bar() == pytest.approx(1.5 / 1000.0)


# ---------------------------------------------------------------------------
# 0.5.4 W4 — Phase A commanded-pO2 floor: full atmosphere × headspace
# decision matrix (0.5.3 Phase A P3 deferral)
# ---------------------------------------------------------------------------
#
# Phase A introduced the commanded-pO2 floor in TWO places, each gated on
# the atmosphere being in ``_O2_CONTROLLED_ATMOSPHERES``:
#
#  1. ``simulator/equilibrium.py::_commanded_pO2_bar`` — the value read
#     by the SiO suppression path (1/√pO2 Ellingham) and the metal-
#     activity-from-fO2 math. Both branches (headspace ON / OFF) apply
#     the floor only in CONTROLLED_O2 / CONTROLLED_O2_FLOW /
#     O2_BACKPRESSURE.
#  2. ``simulator/overhead.py::_update_finite_headspace`` — the writer
#     that ensures the gas inventory and reported P_total honour a
#     non-trivial commanded setpoint. Only fires under the same three
#     atmospheres + ``melt.pO2_mbar > 0.001``.
#
# Phase A landed both via integration-level tests (golden-fixture regen).
# W4 adds explicit branch unit tests pinning the decision matrix so any
# future change to the atmosphere set or the threshold trips a focused
# unit-test failure rather than a fixture-regen surprise.


_O2_FLOORED = (
    Atmosphere.CONTROLLED_O2,
    Atmosphere.CONTROLLED_O2_FLOW,
    Atmosphere.O2_BACKPRESSURE,
)
_O2_NOT_FLOORED = (
    Atmosphere.HARD_VACUUM,
    Atmosphere.PN2_SWEEP,
    Atmosphere.CO2_BACKPRESSURE,
)


def test_phase_a_atmosphere_partition_covers_all_enum_values():
    """Self-guard against future ``Atmosphere`` enum additions silently
    missing the matrix. Adding a new atmosphere value MUST be paired
    with a routing decision in W4's decision matrix; this test trips
    the coverage gap loudly. Codex chunk-review P3 — pin the
    invariant explicitly."""
    full_set = set(_O2_FLOORED) | set(_O2_NOT_FLOORED)
    assert full_set == set(Atmosphere), (
        f"W4 atmosphere partition out of sync with Atmosphere enum: "
        f"missing {set(Atmosphere) - full_set}, "
        f"extra {full_set - set(Atmosphere)}"
    )


def _force_headspace_branch(sim, *, enabled: bool, diagnostic_o2_bar: float = 0.0):
    """Helper: pin the two `_commanded_pO2_bar` branches.

    The Phase A flip routes through `_overhead_headspace_enabled()`
    (reads `_overhead_headspace_config['enabled']`) AND, on the ON
    branch, through `_overhead_gas_equilibrium_diagnostic()` for the
    holdup-derived O₂ partial.

    For `enabled=False`: the helper falls through to the
    `overhead.composition.get('O2', ...)` path. Caller still controls
    `sim.overhead.composition` for that branch.

    For `enabled=True`: the helper monkey-patches the diagnostic
    method to return a chosen holdup-derived O₂ partial in bar so
    we can test the post-flip branch without booting the full
    OVERHEAD_GAS_EQUILIBRIUM provider.
    """
    sim._overhead_headspace_config = {"enabled": enabled}
    if enabled:
        sim._overhead_gas_equilibrium_diagnostic = lambda: {
            "partial_pressures_bar": {"O2": float(diagnostic_o2_bar)},
            "p_O2_bar": float(diagnostic_o2_bar),
        }


@pytest.mark.parametrize("headspace_enabled", [False, True])
@pytest.mark.parametrize("atmosphere", _O2_FLOORED)
def test_commanded_po2_floored_in_o2_controlled_atmospheres(
    atmosphere, headspace_enabled
):
    """Decision matrix row — for each of the three O₂-controlled
    atmospheres AND for BOTH headspace branches (ON / OFF),
    ``melt.pO2_mbar`` is applied as a floor on the commanded pO₂ read
    by ``_commanded_pO2_bar``. Codex chunk-review P2 fix: covers
    both equilibrium.py:62-80 (headspace ON, holdup-derived) AND
    equilibrium.py:82-85 (legacy, gas.composition['O2'] reads)."""
    sim = _sio_o2_train_sim()
    sim.melt.atmosphere = atmosphere
    sim.melt.pO2_mbar = 1.5
    sim.overhead.composition = {"O2": 0.0}
    _force_headspace_branch(sim, enabled=headspace_enabled,
                            diagnostic_o2_bar=0.0)
    # 1.5 mbar setpoint = 0.0015 bar floor; numerical 1e-9 bar guard
    # is dwarfed; holdup-derived 0.0 bar is dwarfed; setpoint wins
    # in both branches.
    assert sim._commanded_pO2_bar() == pytest.approx(1.5 / 1000.0)


@pytest.mark.parametrize("headspace_enabled", [False, True])
@pytest.mark.parametrize("atmosphere", _O2_NOT_FLOORED)
def test_commanded_po2_no_synthetic_floor_outside_o2_control(
    atmosphere, headspace_enabled
):
    """Decision matrix complement — for each non-O₂-controlled
    atmosphere AND for BOTH headspace branches, the setpoint is NOT
    applied as a floor. Effective pO₂ collapses to ``max(holdup-or-
    gas-composition-O₂, 1e-9 bar)``. Codex chunk-review P2 fix:
    exercises both branches; a stale ``pO2_mbar=1.5`` setpoint under
    HARD_VACUUM / PN2_SWEEP / CO2_BACKPRESSURE is ignored regardless
    of headspace toggle (turbine-control feedback loop not wired in
    these modes)."""
    sim = _sio_o2_train_sim()
    sim.melt.atmosphere = atmosphere
    sim.melt.pO2_mbar = 1.5  # stale/irrelevant
    sim.overhead.composition = {"O2": 0.0}
    _force_headspace_branch(sim, enabled=headspace_enabled,
                            diagnostic_o2_bar=0.0)
    assert sim._commanded_pO2_bar() == pytest.approx(1e-9)


def test_commanded_po2_overhead_composition_o2_carried_when_above_setpoint():
    """The legacy (headspace-OFF) branch reads ``gas.composition['O2']``
    as the primary value and only uses ``melt.pO2_mbar`` as a floor.
    Whichever is higher wins. This is the decision-matrix corner where
    the holdup-derived O₂ exceeds the setpoint — the actual holdup
    drives the answer, the setpoint is just a lower bound."""
    sim = _sio_o2_train_sim()
    sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
    sim.melt.pO2_mbar = 0.5            # 0.0005 bar floor
    sim.overhead.composition = {"O2": 10.0}  # 0.01 bar holdup, above floor
    # max(0.01, 0.0005) = 0.01 bar
    assert sim._commanded_pO2_bar() == pytest.approx(0.01)


def test_commanded_po2_numerical_floor_when_all_inputs_zero():
    """All three inputs at zero: composition['O2']=0, melt.pO2_mbar=0,
    atmosphere = O₂-controlled. The setpoint floor is 0 (no-op); only
    the ``_PO2_VACUUM_FLOOR_BAR=1e-9`` numerical guard remains. This
    is the divide-by-zero guard for the 1/√pO₂ SiO suppression and
    the K/pO₂ Ellingham term — NOT a recipe synthetic floor."""
    sim = _sio_o2_train_sim()
    sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
    sim.melt.pO2_mbar = 0.0
    sim.overhead.composition = {"O2": 0.0}
    assert sim._commanded_pO2_bar() == pytest.approx(1e-9)


@pytest.mark.parametrize("atmosphere", _O2_FLOORED)
def test_overhead_writer_raises_p_total_to_setpoint_in_o2_modes(atmosphere):
    """Phase A chunk-review P1 invariant (commit 03c1c45): under
    finite-headspace ON + O₂-controlled atmosphere + non-trivial
    setpoint, ``gas.pressure_mbar`` must be at least ``melt.pO2_mbar``
    so the reported total pressure never falls below the O₂ partial
    pressure (impossible gas state otherwise). Drives the existing
    ``OverheadGasModel`` via the canonical positional signature
    ``update(evap_flux, melt, train, ...)``."""
    from simulator.state import (
        CondensationTrain,
        EvaporationFlux,
        MeltState,
    )
    from simulator.overhead import OverheadGasModel

    melt = MeltState()
    melt.atmosphere = atmosphere
    melt.pO2_mbar = 1.5
    melt.temperature_C = 1500.0
    train = CondensationTrain.create_default()
    flux = EvaporationFlux()

    model = OverheadGasModel({
        "overhead_headspace": {"enabled": True},
        "headspace_volume_m3": 1.0,
        "headspace_temperature_K": 1773.15,
    })
    gas = model.update(flux, melt, train)

    # Under the documented invariant, both O2 partial and total must
    # be at the setpoint (or higher). P_total < pO2 is impossible.
    assert gas.composition.get("O2", 0.0) >= 1.5, (
        f"O2 floor not applied for {atmosphere}: "
        f"got {gas.composition.get('O2', 0.0)}"
    )
    assert gas.pressure_mbar >= 1.5, (
        f"P_total fell below pO2 floor for {atmosphere}: "
        f"P_total={gas.pressure_mbar} < pO2=1.5"
    )
    assert gas.pressure_mbar >= gas.composition["O2"]


@pytest.mark.parametrize("atmosphere", _O2_NOT_FLOORED)
def test_overhead_writer_leaves_o2_alone_outside_o2_modes(atmosphere):
    """Decision matrix complement — under non-O₂-controlled
    atmospheres, the Phase A commanded-pO2 floor block in
    ``simulator/overhead.py`` (lines 507-514) does NOT fire on
    ``gas.composition['O2']``. Operator must explicitly switch
    atmosphere to CONTROLLED_O2 to make the setpoint stick (mirror at
    session.py / runner.py wall-sweep / campaigns.py). A bare
    ``pO2_mbar=1.5`` set under PN2_SWEEP / HARD_VACUUM /
    CO2_BACKPRESSURE leaves the O₂ partial near vacuum."""
    from simulator.state import (
        CondensationTrain,
        EvaporationFlux,
        MeltState,
    )
    from simulator.overhead import OverheadGasModel

    melt = MeltState()
    melt.atmosphere = atmosphere
    melt.pO2_mbar = 1.5  # stale/intentionally ignored under non-O2 modes
    melt.temperature_C = 1500.0
    train = CondensationTrain.create_default()
    flux = EvaporationFlux()

    model = OverheadGasModel({
        "overhead_headspace": {"enabled": True},
        "headspace_volume_m3": 1.0,
        "headspace_temperature_K": 1773.15,
    })
    gas = model.update(flux, melt, train)

    # The Phase A P1 commanded-pO2 floor block (overhead.py:507-514)
    # MUST NOT have fired for these atmospheres. The O2 partial stays
    # well below the stale 1.5 mbar setpoint (CO2_BACKPRESSURE may
    # write its own ambient CO2 floor into pressure_mbar, but the O2
    # entry specifically does not get the synthetic setpoint).
    assert gas.composition.get("O2", 0.0) < 1.5, (
        f"O2 floor leaked into non-O2-controlled atmosphere {atmosphere}: "
        f"got {gas.composition.get('O2', 0.0)} expected < 1.5"
    )
