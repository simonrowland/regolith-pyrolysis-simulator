import math
import types

import pytest

import simulator.evaporation as evaporation_module
from simulator.accounting import AccountingError, MaterialLot
from simulator.condensation import CondensationRouteResult
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
                    "formula": "CrO2",
                    "molar_mass_g_mol": cro2_mw * 1000.0,
                    "parent_oxide": "Cr2O3",
                    "fit_target": "pure_component_psat",
                    "condensation_T_C_at_1mbar": 1800.0,
                    "antoine": {"A": 10.0, "B": 20000.0, "C": 0.0},
                    "valid_range_K": [1500.0, 2200.0],
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
    sim.condensation_model.condensation_temperatures_C["CrO2"] = 1800.0
    return sim


def _bypass_analytic_depletion(sim):
    sim._apply_analytic_evaporation_depletion = lambda flux: flux
    sim.campaign_mgr.check_endpoint = lambda *args, **kwargs: False


def test_overhead_model_does_not_recompute_provider_partition():
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
    overhead = sim.overhead_model.update(
        flux,
        sim.melt,
        sim.train,
        actual_O2_kg_hr=2.0,
    )

    assert overhead.O2_vented_kg_hr == 0.0
    assert overhead.turbine_flow_kg_hr == 0.0
    assert overhead.melt_offgas_O2_mol_hr == pytest.approx(
        2.0 / (MOLAR_MASS["O2"] / 1000.0)
    )


@pytest.mark.parametrize(
    ("finite_headspace_enabled", "expected_force_drain"),
    [(True, False), (False, True)],
)
def test_provider_partition_projection_tracks_changes_on_both_runtime_paths(
    finite_headspace_enabled,
    expected_force_drain,
):
    sim = _sio_train_sim()
    sim.melt.campaign = CampaignPhase.C2A
    sim._overhead_headspace_config["enabled"] = finite_headspace_enabled
    sim.overhead_model._finite_headspace_enabled = finite_headspace_enabled
    sim._update_temperature = lambda: None
    sim._get_equilibrium = lambda: object()
    sim._calculate_evaporation = lambda equilibrium: EvaporationFlux()
    _bypass_analytic_depletion(sim)
    partitions = iter(((1.0, 2.0), (4.0, 5.0)))
    force_drain_values = []

    def _provider_result(**kwargs):
        force_drain_values.append(bool(kwargs.get("force_drain_all", False)))
        admitted_mol, vented_mol = next(partitions)
        return types.SimpleNamespace(
            diagnostic={
                "bled_o2_mol": admitted_mol + vented_mol,
                "bled_o2_kg": (
                    admitted_mol + vented_mol
                ) * MOLAR_MASS["O2"],
                "melt_o2_bled_mol": admitted_mol + vented_mol,
                "o2_stored_mol": admitted_mol,
                "o2_vented_mol": vented_mol,
                "melt_o2_vented_mol": vented_mol,
                "external_o2_vented_mol": 0.0,
                "o2_relieved_mol": vented_mol,
                "o2_held_mol": 0.0,
            }
        )

    sim._dispatch_overhead_bleed = _provider_result

    sim.step()
    assert sim.overhead.turbine_flow_mol_hr == pytest.approx(1.0)
    assert sim.overhead.O2_vented_mol_hr == pytest.approx(2.0)

    sim.step()
    assert sim.overhead.turbine_flow_mol_hr == pytest.approx(4.0)
    assert sim.overhead.O2_vented_mol_hr == pytest.approx(5.0)
    assert force_drain_values == [expected_force_drain, expected_force_drain]


@pytest.mark.parametrize("finite_headspace_enabled", [True, False])
def test_real_provider_partition_changes_across_two_ticks_on_both_paths(
    finite_headspace_enabled,
):
    # Unlike the synthetic-provider test above, this drives the REAL
    # OVERHEAD_BLEED provider through two full steps on each runtime path
    # and holds the projection mirrors to the per-tick LEDGER deltas — the
    # actual consumer contract — with evaporation that changes between
    # ticks so a stale (previous-tick) projection cannot pass.
    sim = _sio_train_sim()
    sim.melt.campaign = CampaignPhase.C2A
    sim._overhead_headspace_config["enabled"] = finite_headspace_enabled
    sim.overhead_model._finite_headspace_enabled = finite_headspace_enabled
    sim._update_temperature = lambda: None
    sim._get_equilibrium = lambda: object()
    fluxes = iter((
        EvaporationFlux(species_kg_hr={"SiO": 100.0}, total_kg_hr=100.0),
        EvaporationFlux(species_kg_hr={"SiO": 40.0}, total_kg_hr=40.0),
    ))
    sim._calculate_evaporation = lambda equilibrium: next(fluxes)
    _bypass_analytic_depletion(sim)
    o2_kg_per_mol = MOLAR_MASS["O2"] / 1000.0

    def _terminal_o2_mol(account):
        return sim.atom_ledger.kg_by_account(account).get(
            "O2", 0.0
        ) / o2_kg_per_mol

    stored_deltas = []
    for _tick in range(2):
        stored_before = _terminal_o2_mol("terminal.oxygen_melt_offgas_stored")
        vented_before = _terminal_o2_mol(
            "terminal.oxygen_melt_offgas_vented_to_vacuum"
        )

        sim.step()

        stored_delta = _terminal_o2_mol(
            "terminal.oxygen_melt_offgas_stored"
        ) - stored_before
        vented_delta = _terminal_o2_mol(
            "terminal.oxygen_melt_offgas_vented_to_vacuum"
        ) - vented_before
        assert sim.overhead.turbine_flow_mol_hr == pytest.approx(stored_delta)
        assert sim.overhead.O2_vented_mol_hr == pytest.approx(vented_delta)
        stored_deltas.append(stored_delta)

    assert stored_deltas[0] > 0.0
    assert all(delta > 0.0 for delta in stored_deltas)


def test_turbine_utilization_is_demand_over_capacity_and_flags_overload():
    # Consumer contract (state.OverheadGas.turbine_utilization_pct, read by
    # the Loop-3b >120% ramp throttle): utilization exceeds 100% exactly
    # when melt-side O2 demand exceeds the cold train's per-tick capacity.
    from simulator.thermal_train import FiniteCapacity

    sim = _sio_train_sim()
    sio_kg = 100.0
    flux = EvaporationFlux(species_kg_hr={"SiO": sio_kg}, total_kg_hr=sio_kg)
    sim._route_to_condensation(flux)

    # Default posture (NoColdTrain) mirrors the legacy no-turbine-spec
    # branch: utilization pinned at 0.0, no overload flag.
    result = sim._dispatch_overhead_bleed(force_drain_all=True)
    sim._project_overhead_bleed_partition(result)
    assert sim.overhead.turbine_utilization_pct == 0.0
    assert sim.overhead.turbine_limited is False

    sim = _sio_train_sim()
    sim._route_to_condensation(flux)
    _capacity, cold_train = sim._cold_train_capacity_policy()
    capacity_kg_hr = 1.0e-3
    sim._cold_train_capacity_policy = lambda: (
        FiniteCapacity(capacity_kg_hr),
        cold_train,
    )

    result = sim._dispatch_overhead_bleed(force_drain_all=True)
    sim._project_overhead_bleed_partition(result)

    diagnostic = result.diagnostic
    demand_mol = (
        diagnostic["o2_admitted_mol"]
        + diagnostic.get("o2_accumulated_mol", 0.0)
        + diagnostic["o2_relieved_mol"]
        + diagnostic["o2_held_mol"]
    )
    capacity_mol = capacity_kg_hr / (MOLAR_MASS["O2"] / 1000.0)
    assert demand_mol > capacity_mol
    assert sim.overhead.turbine_limited is True
    assert sim.overhead.turbine_utilization_pct == pytest.approx(
        demand_mol / capacity_mol * 100.0
    )
    assert sim.overhead.turbine_utilization_pct > 100.0


def test_accumulator_absorption_reports_overload_without_turbine_limited():
    # Design semantics pin (owner-ratified cistern cold-battery): while the
    # accumulator absorbs demand beyond per-tick capacity, utilization reads
    # >100% (honest demand-vs-freeze-capacity) but turbine_limited stays
    # False, so the Loop-3b ramp throttle must NOT engage — the cistern's
    # purpose is letting the bake-off run at peak while the cavern buffers.
    # Overflow only lands in relieved/held (=> limited=True) at cavern fill.
    from simulator.thermal_train import FiniteCapacity

    sim = _sio_train_sim()
    sio_kg = 100.0
    flux = EvaporationFlux(species_kg_hr={"SiO": sio_kg}, total_kg_hr=sio_kg)
    sim._route_to_condensation(flux)

    capacity_kg_hr = 1.0e-3
    cold_train = types.SimpleNamespace(
        accumulator_enabled=True,
        # k_relief must be finite-positive (fail-closed provider validation);
        # p_open far above any headspace pO2 keeps relief exactly zero.
        relief={
            "k_relief_kg_hr_Pa": 1.0e-9,
            "p_open_Pa": 1.0e12,
            "vessel_rating_Pa": 1.0e15,
        },
    )
    sim._cold_train_capacity_policy = lambda: (
        FiniteCapacity(capacity_kg_hr),
        cold_train,
    )

    result = sim._dispatch_overhead_bleed(force_drain_all=True)
    sim._project_overhead_bleed_partition(result)

    diagnostic = result.diagnostic
    assert diagnostic["o2_accumulated_mol"] > 0.0
    assert diagnostic["o2_relieved_mol"] == pytest.approx(0.0)
    assert diagnostic["o2_held_mol"] == pytest.approx(0.0)
    assert sim.overhead.turbine_utilization_pct > 100.0
    assert sim.overhead.turbine_limited is False


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
    overhead = sim.overhead_model.update(
        EvaporationFlux(),
        sim.melt,
        sim.train,
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


def test_force_drain_uses_provider_storage_partition():
    # Re-speciation contract: use SiO to exercise overhead O2 vent routing.
    sim = _sio_train_sim()
    sio_kg = 100.0
    o2_from_sio_kg = _sio_o2_kg(sio_kg)
    flux = EvaporationFlux(species_kg_hr={"SiO": sio_kg}, total_kg_hr=sio_kg)

    sim._route_to_condensation(flux)
    overhead = sim.overhead_model.update(
        flux,
        sim.melt,
        sim.train,
        actual_O2_kg_hr=sim.atom_ledger.kg_by_account(
            "process.overhead_gas").get("O2", 0.0),
    )
    result = sim._dispatch_overhead_bleed(
        force_drain_all=True,
    )
    sim.overhead = overhead
    sim._project_overhead_bleed_partition(result)

    assert overhead.O2_vented_kg_hr == 0.0
    assert sim.atom_ledger.kg_by_account("terminal.oxygen_melt_offgas_stored")[
        "O2"
    ] == pytest.approx(o2_from_sio_kg)
    assert sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_vented_to_vacuum"
    ).get("O2", 0.0) == 0.0
    assert sim._oxygen_total_kg() == pytest.approx(o2_from_sio_kg)
    assert sim.O2_stored_cumulative_kg == pytest.approx(o2_from_sio_kg)
    assert sim.O2_vented_cumulative_kg == 0.0


def test_vent_mirror_excludes_co_present_external_bubbler_oxygen():
    sim = _sio_train_sim()
    sio_kg = 1.0
    flux = EvaporationFlux(species_kg_hr={"SiO": sio_kg}, total_kg_hr=sio_kg)
    sim._route_to_condensation(flux)

    external_o2_mol = 4.0
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"O2": external_o2_mol * MOLAR_MASS["O2"] / 1000.0},
        source="test external bubbler passthrough",
    )
    sim._o2_bubbler_external_o2_in_overhead_mol = external_o2_mol

    result = sim._dispatch_overhead_bleed(force_drain_all=True)
    sim._project_overhead_bleed_partition(result)

    assert result.diagnostic["external_o2_vented_mol"] == pytest.approx(
        external_o2_mol
    )
    assert result.diagnostic["melt_o2_vented_mol"] == pytest.approx(0.0)
    assert sim.overhead.O2_vented_mol_hr == pytest.approx(0.0)
    assert sim.overhead.O2_vented_kg_hr == pytest.approx(0.0)


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


def test_step_force_drain_stores_evaporation_o2_under_provider_authority():
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

    assert sim.overhead.O2_vented_kg_hr == 0.0
    assert sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_stored")["O2"] == pytest.approx(o2_from_sio_kg)
    assert sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_vented_to_vacuum"
    ).get("O2", 0.0) == 0.0
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
    sim._dispatch_overhead_bleed = lambda *args, **kwargs: types.SimpleNamespace(
        diagnostic={}
    )
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
    # The 1500 C wall candidate is ~1e-35 kg, below the provider's 1e-12 kg
    # commit floor. The credited mass therefore closes through the baffle and
    # overhead accounts without fabricating a positive wall deposit.
    assert wall_total == pytest.approx(0.0)
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
                    "condensation_T_C_at_1mbar": 1800.0,
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
        force_drain_all=True,
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
                    "condensation_T_C_at_1mbar": 1800.0,
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
        force_drain_all=True,
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


def test_commanded_po2_uses_upstream_o2_when_above_setpoint():
    sim = _sio_o2_train_sim()
    sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
    sim.melt.pO2_mbar = 0.5
    sim._melt_headspace_composition_mbar = {"O2": 10.0}
    sim.overhead.composition = {"O2": 0.001}
    assert sim._commanded_pO2_bar() == pytest.approx(0.01)


def test_overhead_total_pressure_covers_additive_partial_floors():
    from simulator.state import (
        CondensationTrain,
        EvaporationFlux,
        MeltState,
    )
    from simulator.overhead import OverheadGasModel

    melt = MeltState()
    melt.atmosphere = Atmosphere.CONTROLLED_O2
    melt.pO2_mbar = 1.0
    melt.p_total_mbar = 1.0
    melt.temperature_C = 1500.0
    flux = EvaporationFlux(total_kg_hr=0.1, species_kg_hr={"Na": 0.1})

    gas = OverheadGasModel({"enabled": False}).update(
        flux,
        melt,
        CondensationTrain.create_default(),
    )

    partial_sum = sum(max(0.0, float(v)) for v in gas.composition.values())
    assert gas.pressure_mbar + 1e-12 >= partial_sum


def test_overhead_product_partials_use_mole_fractions_not_mass_fractions():
    from simulator.state import (
        CondensationTrain,
        EvaporationFlux,
        MeltState,
        MOLAR_MASS,
    )
    from simulator.overhead import OverheadGasModel

    melt = MeltState()
    melt.temperature_C = 1500.0
    melt.p_total_mbar = 0.0
    flux = EvaporationFlux(
        total_kg_hr=2.0,
        species_kg_hr={"Na": 1.0, "Fe": 1.0},
    )

    gas = OverheadGasModel({"enabled": False}).update(
        flux,
        melt,
        CondensationTrain.create_default(),
    )

    na_mol_hr = 1.0 / (MOLAR_MASS["Na"] / 1000.0)
    fe_mol_hr = 1.0 / (MOLAR_MASS["Fe"] / 1000.0)
    expected_na_fraction = na_mol_hr / (na_mol_hr + fe_mol_hr)
    total_product_pressure = gas.composition["Na"] + gas.composition["Fe"]

    assert gas.composition["Na"] / total_product_pressure == pytest.approx(
        expected_na_fraction
    )
    assert gas.composition["Na"] > gas.composition["Fe"]


def test_condensation_residual_drives_overhead_partial_pressures(monkeypatch):
    sim = _gas_train_sim()
    sim.vapor_pressures["oxide_vapors"]["FeO"] = {
        "parent_oxide": "FeO",
        "stoich_oxide_per_vapor": 1.0,
        "stoich_O2_per_vapor": 0.0,
    }
    flux = EvaporationFlux(
        total_kg_hr=2.0,
        species_kg_hr={"Fe": 1.0, "FeO": 1.0},
    )
    monkeypatch.setattr(
        sim.condensation_model,
        "route",
        lambda _flux, _melt: CondensationRouteResult(
            remaining_by_species={"Fe": 0.25, "FeO": 0.75},
            condensed_by_stage_species={1: {"Fe": 0.75, "FeO": 0.25}},
            wall_deposit_by_species={"Fe": 0.15},
            wall_deposit_fraction_by_species={"Fe": 0.2},
            wall_deposit_account_fractions_by_species={
                "Fe": {PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNTS[0]: 1.0},
            },
        ),
    )

    residual_flux = sim._route_to_condensation(flux)
    captured_baffle_kg = sim.atom_ledger.kg_by_account(
        "process.condensation_train"
    ).get("Fe", 0.0)
    captured_wall_kg = sim.atom_ledger.kg_by_account(
        PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNTS[0]
    ).get("Fe", 0.0)
    monkeypatch.setattr(
        sim.overhead_model,
        "_vapor_pressure_mbar_from_flux",
        lambda *_args, **_kwargs: 10.0,
    )
    gas = sim.overhead_model.update(residual_flux, sim.melt, sim.train)

    fe_mol_hr = 0.25 / (MOLAR_MASS["Fe"] / 1000.0)
    feo_mol_hr = 0.75 / (MOLAR_MASS["FeO"] / 1000.0)
    expected_fe_mbar = 10.0 * fe_mol_hr / (fe_mol_hr + feo_mol_hr)
    expected_feo_mbar = 10.0 - expected_fe_mbar

    assert residual_flux.species_kg_hr == pytest.approx(
        {"Fe": 0.25, "FeO": 0.75}
    )
    assert captured_wall_kg > 0.0
    assert captured_baffle_kg + captured_wall_kg + residual_flux.species_kg_hr[
        "Fe"
    ] == pytest.approx(1.0, rel=1e-12)
    assert gas.composition["Fe"] == pytest.approx(expected_fe_mbar)
    assert gas.composition["FeO"] == pytest.approx(expected_feo_mbar)


def test_transport_saturation_uses_full_inlet_when_residual_is_nearly_captured():
    sim = _gas_train_sim()
    residual_flux = EvaporationFlux(
        total_kg_hr=1.0e-12,
        species_kg_hr={"Fe": 1.0e-12},
    )
    residual_transport = sim.overhead_model.estimate_transport_state(
        residual_flux, sim.melt
    )
    pipe_capacity_kg_hr = residual_transport["pipe_conductance_kg_hr"]
    inlet_flux = EvaporationFlux(
        total_kg_hr=1.5 * pipe_capacity_kg_hr,
        species_kg_hr={"Fe": 1.5 * pipe_capacity_kg_hr},
    )

    gas = sim.overhead_model.update(
        residual_flux,
        sim.melt,
        sim.train,
        transport_inlet_kg_hr=inlet_flux.total_kg_hr,
    )

    assert gas.transport_saturation_pct == pytest.approx(150.0)
    assert gas.evap_exceeds_transport is True
    assert gas.composition == pytest.approx({
        "Fe": residual_transport["vapor_pressure_mbar"],
    })

    sim.melt.campaign = CampaignPhase.C2A
    sim.melt.campaign_hour = 0.0
    sim.overhead = gas
    sim.campaign_mgr.get_temp_target = lambda *_args: (1600.0, 100.0)
    sim._update_temperature()

    assert sim._last_actual_ramp == pytest.approx(50.0)
    assert "pipe saturated" in sim._last_throttle_reason


def test_heavy_species_transport_uses_upstream_mixture_after_total_capture():
    from simulator.overhead import (
        DEFAULT_PIPE_M_AVG_KG_MOL,
        OverheadGasModel,
        _mean_molar_mass_kg_mol,
    )
    from simulator.state import CondensationTrain, MeltState

    model = OverheadGasModel({"enabled": False})
    melt = MeltState(temperature_C=1500.0, p_total_mbar=1.0)
    upstream_flux = EvaporationFlux(
        total_kg_hr=0.1,
        species_kg_hr={"CrO2": 0.1},
    )
    residual_flux = EvaporationFlux()

    upstream_transport = model.estimate_transport_state(upstream_flux, melt)
    fallback_transport = model.estimate_transport_state(residual_flux, melt)
    gas = model.update(
        residual_flux,
        melt,
        CondensationTrain.create_default(),
        transport_inlet_kg_hr=upstream_flux.total_kg_hr,
        transport_inlet_flux=upstream_flux,
    )

    assert _mean_molar_mass_kg_mol(upstream_flux.species_kg_hr) == pytest.approx(
        MOLAR_MASS["CrO2"] / 1000.0
    )
    assert MOLAR_MASS["CrO2"] / 1000.0 != pytest.approx(
        DEFAULT_PIPE_M_AVG_KG_MOL
    )
    assert gas.pipe_conductance_kg_hr == pytest.approx(
        upstream_transport["pipe_conductance_kg_hr"]
    )
    assert gas.pipe_conductance_kg_hr != pytest.approx(
        fallback_transport["pipe_conductance_kg_hr"]
    )
    assert gas.composition == {}


def test_condensation_residual_ignores_prior_holdup_drain_credit(monkeypatch):
    sim = _gas_train_sim()
    prior_holdup_kg = 2.0e-8
    evolved_kg = 3.0e-8
    condensed_kg = 1.0e-8
    residual_kg = evolved_kg - condensed_kg
    retained_wall_kg = 5.0e-13
    sim.atom_ledger.load_external(
        "process.condensation_retained_holdup",
        {"Fe": prior_holdup_kg},
        source="residual-regression prior retained holdup",
    )
    monkeypatch.setattr(
        sim.condensation_model,
        "route",
        lambda _flux, _melt: CondensationRouteResult(
            remaining_by_species={"Fe": residual_kg},
            wall_deposit_by_species={"Fe": retained_wall_kg},
            wall_deposit_fraction_by_species={
                "Fe": retained_wall_kg / condensed_kg,
            },
            wall_deposit_account_fractions_by_species={
                "Fe": {PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNTS[0]: 1.0},
            },
        ),
    )

    residual_flux = sim._route_to_condensation(EvaporationFlux(
        total_kg_hr=evolved_kg,
        species_kg_hr={"Fe": evolved_kg},
    ))

    assert residual_flux.species_kg_hr["Fe"] == pytest.approx(
        residual_kg, rel=0.0, abs=1e-24
    )
    assert sim.atom_ledger.kg_by_account(
        "process.condensation_retained_holdup"
    ).get("Fe", 0.0) == pytest.approx(
        condensed_kg, rel=0.0, abs=1e-24
    )
    assert sim.atom_ledger.kg_by_account(
        "process.condensation_train"
    ).get("Fe", 0.0) == pytest.approx(
        prior_holdup_kg, rel=0.0, abs=1e-24
    )


def test_step_overhead_composition_uses_real_condensation_residual():
    sim = _gas_train_sim()
    evolved_kg = 1.0
    flux = EvaporationFlux(
        total_kg_hr=evolved_kg,
        species_kg_hr={"Fe": evolved_kg},
    )
    sim.melt.campaign = CampaignPhase.C2A
    sim._update_temperature = lambda: None
    sim._get_equilibrium = lambda: object()
    sim._calculate_evaporation = lambda _equilibrium: flux
    _bypass_analytic_depletion(sim)
    seen = {}
    real_update = sim.overhead_model.update

    def _spy_update(overhead_flux, *args, **kwargs):
        seen["flux"] = overhead_flux
        seen["transport_inlet_kg_hr"] = kwargs["transport_inlet_kg_hr"]
        return real_update(overhead_flux, *args, **kwargs)

    sim.overhead_model.update = _spy_update
    sim.step()

    residual_kg = seen["flux"].species_kg_hr["Fe"]
    assert 0.0 < residual_kg < evolved_kg
    expected_transport = sim.overhead_model.estimate_transport_state(
        flux, sim.melt
    )
    expected_saturation_pct = (
        evolved_kg / expected_transport["pipe_conductance_kg_hr"] * 100.0
    )
    assert seen["transport_inlet_kg_hr"] == pytest.approx(evolved_kg)
    assert sim.overhead.transport_saturation_pct == pytest.approx(
        expected_saturation_pct
    )
    residual_transport = sim.overhead_model.estimate_transport_state(
        seen["flux"], sim.melt
    )
    assert sim.overhead.composition["Fe"] == pytest.approx(
        residual_transport["vapor_pressure_mbar"]
    )


def test_next_tick_p_bulk_uses_upstream_headspace_after_near_total_capture(
    monkeypatch,
):
    sim = _gas_train_sim()
    evolved_kg = 1.0
    residual_kg = 1.0e-8
    flux = EvaporationFlux(
        total_kg_hr=evolved_kg,
        species_kg_hr={"Fe": evolved_kg},
    )
    monkeypatch.setattr(
        sim.condensation_model,
        "route",
        lambda _flux, _melt: CondensationRouteResult(
            remaining_by_species={"Fe": residual_kg},
            condensed_by_stage_species={1: {"Fe": evolved_kg - residual_kg}},
        ),
    )
    sim.melt.campaign = CampaignPhase.C2A
    sim.melt.temperature_C = 1500.0
    sim._apply_native_fe_saturation_split = lambda **_kwargs: None
    sim._update_temperature = lambda: None
    sim._get_equilibrium = lambda: object()
    sim._calculate_evaporation = lambda _equilibrium: flux
    _bypass_analytic_depletion(sim)

    sim.step()

    upstream_transport = sim.overhead_model.estimate_transport_state(
        flux, sim.melt
    )
    residual_flux = EvaporationFlux(
        total_kg_hr=residual_kg,
        species_kg_hr={"Fe": residual_kg},
    )
    residual_transport = sim.overhead_model.estimate_transport_state(
        residual_flux, sim.melt
    )
    assert sim._melt_headspace_composition_mbar["Fe"] == pytest.approx(
        upstream_transport["vapor_pressure_mbar"]
    )
    assert sim.overhead.composition["Fe"] == pytest.approx(
        residual_transport["vapor_pressure_mbar"]
    )
    assert sim._melt_headspace_composition_mbar["Fe"] > (
        sim.overhead.composition["Fe"] * 1.0e3
    )
    assert sim.record.snapshots[-1].melt_headspace_composition_mbar == pytest.approx(
        sim._melt_headspace_composition_mbar
    )
    assert sim._evaporation_bulk_partial_pressure_pa("Fe") == pytest.approx(
        upstream_transport["vapor_pressure_mbar"] * 100.0
    )

    seen = {}

    def _capture_dispatch(_intent, *, control_inputs):
        seen.update(control_inputs)
        return types.SimpleNamespace(
            status="ok",
            diagnostic={"evaporation_flux_kg_hr": {}},
        )

    sim._dispatch_only = _capture_dispatch
    equilibrium = types.SimpleNamespace(
        vapor_pressures_Pa={"Fe": 100.0},
        vapor_pressures_source={},
        activity_coefficients={},
        diagnostics={},
        liquid_fraction=1.0,
    )
    evaporation_module.EvaporationMixin._calculate_evaporation(sim, equilibrium)

    assert seen["overhead_partials_Pa"]["Fe"] == pytest.approx(
        upstream_transport["vapor_pressure_mbar"] * 100.0
    )
    assert seen["overhead_partials_Pa"]["Fe"] > 0.0


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

    model = OverheadGasModel({"enabled": True, "volume_m3": 1.0})
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

    model = OverheadGasModel({"enabled": True, "volume_m3": 1.0})
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
