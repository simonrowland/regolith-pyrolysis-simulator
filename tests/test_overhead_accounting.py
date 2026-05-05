import types

import pytest

from simulator.accounting import AccountingError, MaterialLot
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import StubBackend
from simulator.state import CampaignPhase, EvaporationFlux, MOLAR_MASS


def _gas_train_sim():
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
    sim.load_batch("oxide", mass_kg=100.0)
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

    assert sim.atom_ledger.kg_by_account("terminal.oxygen_melt_offgas_stored")[
        "O2"
    ] == pytest.approx(16.0)
    assert sim._oxygen_total_kg() == pytest.approx(16.0)
    assert sim.oxygen_cumulative_kg == pytest.approx(16.0)
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
        actual_O2_kg_hr=sim._oxygen_total_kg(),
    )
    sim._debit_vented_oxygen(overhead.O2_vented_kg_hr)

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

    sim.step()

    assert sim._oxygen_total_kg() == pytest.approx(16.0)


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
