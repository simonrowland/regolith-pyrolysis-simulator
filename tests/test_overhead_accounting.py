import types

import pytest

import simulator.evaporation as evaporation_module
from simulator.accounting import AccountingError, MaterialLot
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import StubBackend
from simulator.state import (
    Atmosphere,
    CampaignPhase,
    EvaporationFlux,
    MOLAR_MASS,
)


def _gas_train_sim(mass_kg=100.0):
    backend = StubBackend()
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
    backend = StubBackend()
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


def _cro2_train_sim():
    backend = StubBackend()
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


def test_turbine_venting_uses_actual_o2_not_total_evaporation_mass():
    backend = StubBackend()
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
    backend = StubBackend()
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
    sim = _gas_train_sim()
    flux = EvaporationFlux(species_kg_hr={"Fe": 55.84}, total_kg_hr=55.84)

    sim._route_to_condensation(flux)
    sim._update_melt_composition(flux)

    assert sim.atom_ledger.kg_by_account("process.overhead_gas")[
        "O2"
    ] == pytest.approx(16.0)
    assert sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_stored").get("O2", 0.0) == pytest.approx(0.0)
    assert sim._oxygen_total_kg() == pytest.approx(0.0)
    assert sim.oxygen_cumulative_kg == pytest.approx(0.0)
    assert all("O2" not in stage.collected_kg for stage in sim.train.stages)


def test_o2_venting_moves_between_terminal_ledger_accounts():
    sim = _gas_train_sim()
    flux = EvaporationFlux(species_kg_hr={"Fe": 55.84}, total_kg_hr=55.84)
    turbine = types.SimpleNamespace(max_O2_flow_kg_hr=10.0)

    sim._route_to_condensation(flux)
    overhead = sim.overhead_model.update(
        flux,
        sim.melt,
        sim.train,
        turbine_spec=turbine,
        actual_O2_kg_hr=sim.atom_ledger.kg_by_account(
            "process.overhead_gas")["O2"],
    )
    sim._dispatch_overhead_bleed(
        turbine_spec=turbine,
        force_drain_all=True,
        o2_vented_kg=overhead.O2_vented_kg_hr,
    )

    assert overhead.O2_vented_kg_hr == pytest.approx(6.0)
    assert sim.atom_ledger.kg_by_account("terminal.oxygen_melt_offgas_stored")[
        "O2"
    ] == pytest.approx(10.0)
    assert sim.atom_ledger.kg_by_account("terminal.oxygen_melt_offgas_vented_to_vacuum")[
        "O2"
    ] == pytest.approx(6.0)
    assert sim._oxygen_total_kg() == pytest.approx(16.0)
    assert sim.O2_stored_cumulative_kg == pytest.approx(10.0)
    assert sim.O2_vented_cumulative_kg == pytest.approx(6.0)


def test_step_does_not_double_credit_gas_train_ledger_o2():
    sim = _gas_train_sim()
    flux = EvaporationFlux(species_kg_hr={"Fe": 55.84}, total_kg_hr=55.84)
    sim.melt.campaign = CampaignPhase.C2A
    sim._update_temperature = lambda: None
    sim._get_equilibrium = lambda: object()
    sim._calculate_evaporation = lambda equilibrium: flux
    _bypass_analytic_depletion(sim)

    sim.step()

    assert sim._oxygen_total_kg() == pytest.approx(16.0)


def test_step_vents_terminal_stored_evaporation_o2_when_turbine_limited():
    sim = _gas_train_sim()
    flux = EvaporationFlux(species_kg_hr={"Fe": 55.84}, total_kg_hr=55.84)
    sim.melt.campaign = CampaignPhase.C2A
    sim._update_temperature = lambda: None
    sim._get_equilibrium = lambda: object()
    sim._calculate_evaporation = lambda equilibrium: flux
    _bypass_analytic_depletion(sim)
    sim._get_turbine_spec = lambda: types.SimpleNamespace(
        max_O2_flow_kg_hr=10.0)

    sim.step()

    assert sim.overhead.O2_vented_kg_hr == pytest.approx(6.0)
    assert sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_stored")["O2"] == pytest.approx(10.0)
    assert sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_vented_to_vacuum"
    )["O2"] == pytest.approx(6.0)
    assert sim._oxygen_total_kg() == pytest.approx(16.0)


def test_overhead_o2_not_double_counted_across_ticks():
    # Two consecutive steps with evaporation O2. Tick 1's overhead O2 is
    # left undrained (drain stubbed out), so it carries into tick 2's
    # process.overhead_gas holdup. The turbine/vent decision must be fed
    # the ledger holdup itself -- never holdup max()'d with a per-tick
    # production counter, which would let carried-over O2 read as fresh
    # throughput. The invariant: over tick 2, turbine throughput + vent
    # equals the ledger O2 delta into the terminal accounts.
    sim = _gas_train_sim(mass_kg=200.0)
    flux = EvaporationFlux(species_kg_hr={"Fe": 55.84}, total_kg_hr=55.84)
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
    assert carried_over_kg == pytest.approx(16.0)

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
    stage_totals = sim.train.total_by_species()
    products = sim.product_ledger()
    condensed_total = sum(condensed.values())

    assert overhead > 0.0
    assert "SiO" not in stage_totals
    assert stage_totals["Si"] == pytest.approx(condensed["Si"])
    assert stage_totals["SiO2"] == pytest.approx(condensed["SiO2"])
    assert condensed_total + overhead == pytest.approx(100.0)
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
    backend = StubBackend()
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
    backend = StubBackend()
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
    backend = StubBackend()
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
    backend = StubBackend()
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
    sim._dispatch_overhead_bleed(force_drain_all=True, o2_vented_kg=0.0)

    products = sim.product_ledger()
    assert products["FeO"] == pytest.approx(100.0)
    assert sim._oxygen_terminal_partition_kg()["total"] == pytest.approx(0.0)
    assert sim._make_snapshot().mass_balance_error_pct == pytest.approx(0.0)


def test_explicit_vapor_stoich_must_conserve_atoms_not_just_mass():
    backend = StubBackend()
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
    backend = StubBackend()
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
    sim._dispatch_overhead_bleed(force_drain_all=True, o2_vented_kg=0.0)

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
    _stub_equilibrium actually emits a SiO vapor pressure and the
    SiO √pO₂ suppression path is exercised."""
    backend = StubBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}, "chemistry_kernel": {"allow_fallback_vapor": True}},
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


def test_sio_suppression_uses_commanded_po2():
    """The SiO √pO₂ suppression in _stub_equilibrium references the
    commanded pO₂ from _commanded_pO2_bar. This is the *commanded*
    setpoint, NOT the AtomLedger O₂ holdup: overhead.composition['O2'] is
    itself max(gas O2, setpoint) written by overhead.py."""
    sim = _sio_o2_train_sim()
    sim.melt.temperature_C = 1600.0

    # C2B-style controlled-O₂ atmosphere. overhead.composition['O2'] here
    # plays the role of the value overhead.py would have written:
    # max(gas O2, setpoint). 1.5 mbar.
    sim.melt.campaign = CampaignPhase.C2B
    sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
    sim.melt.pO2_mbar = 1.5
    sim.overhead.composition = {"O2": 1.5}

    setpoint_bar = sim.melt.pO2_mbar / 1000.0

    # Spy on the commanded-pO₂ helper: the equilibrium path routes
    # through it.
    real_commanded = sim._commanded_pO2_bar
    calls = []

    def _spy_commanded():
        value = real_commanded()
        calls.append(value)
        return value

    sim._commanded_pO2_bar = _spy_commanded

    # --- Equilibrium path: pO₂ feeding the SiO √pO₂ suppression ---
    equilibrium = sim._stub_equilibrium()
    assert calls, "_stub_equilibrium must consult _commanded_pO2_bar"
    intrinsic_pO2_bar = 10.0 ** equilibrium.fO2_log
    # fO2_log is now the melt-intrinsic Kress91 surface.  The SiO
    # suppression still consumes the commanded gas pO2 via the helper spy.
    assert intrinsic_pO2_bar != pytest.approx(calls[0])
    # SiO vapor pressure was actually emitted (suppression path exercised).
    assert equilibrium.vapor_pressures_Pa.get("SiO", 0.0) > 0.0

    # The commanded pO₂ under active O₂ control is the setpoint.
    assert calls[0] == pytest.approx(setpoint_bar)

    sim._commanded_pO2_bar = real_commanded


def test_evaporation_flux_consults_commanded_po2_for_oxide_vapors():
    """EVAPORATION_FLUX consumes gas pO2 for oxide-vapor suppression."""
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
    equilibrium = sim._stub_equilibrium()
    calls.clear()
    flux = sim._calculate_evaporation(equilibrium)
    sim._commanded_pO2_bar = real_commanded

    assert flux is not None
    assert calls, "_calculate_evaporation must feed gas pO2 to the flux provider"


def test_equilibrium_does_not_emit_o2_vapor_species():
    """_stub_equilibrium only ever writes metal + declared oxide vapors
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
