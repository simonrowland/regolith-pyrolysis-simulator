"""Executable valve-state and inventory checks for owner decision d-247."""

from types import SimpleNamespace

import pytest

from simulator.account_ids import C0_CRYO_BIN_ACCOUNTS
from simulator.accounting import AccountingQueries
from simulator.accounting.formulas import resolve_species_formula
from simulator.chemistry.kernel import ChemistryIntent
from simulator.condensation import DepositionInputRefusal
from simulator.runner import PyrolysisRun
from simulator.state import CampaignPhase, CondensationTrain, EvaporationFlux


def _start(feedstock="lunar_mare_low_ti"):
    return PyrolysisRun(
        feedstock_id=feedstock, campaign="C0", hours=24, additives_kg={},
        allow_fallback_vapor=True, allow_unmeasured_alpha_fallback=True,
    )._start_session()


def test_valve_is_closed_until_c0_starts():
    assert CondensationTrain.create_default().volatiles_gate_sealed
    assert not _start().simulator.train.volatiles_gate_sealed


@pytest.mark.parametrize("feedstock", [
    "lunar_mare_low_ti", "ci_carbonaceous_chondrite",
])
def test_c0_exit_seals_inventory_before_c0b(feedstock, monkeypatch):
    session = _start(feedstock)
    sim = session.simulator
    forced_drains = []
    dispatch_bleed = sim._dispatch_overhead_bleed

    def record_bleed(**kwargs):
        if kwargs.get("force_drain_all"):
            forced_drains.append(sim.melt.hour)
        return dispatch_bleed(**kwargs)

    monkeypatch.setattr(sim, "_dispatch_overhead_bleed", record_bleed)
    assert not sim.train.volatiles_gate_sealed
    for _ in range(24):
        if sim.melt.campaign != CampaignPhase.C0:
            break
        session.advance()
    assert sim.melt.campaign == CampaignPhase.C0B
    assert sim.train.volatiles_gate_sealed
    frozen = {
        account: sim.atom_ledger.mol_by_account(account)
        for account in C0_CRYO_BIN_ACCOUNTS.values()
    }
    assert sum(frozen[C0_CRYO_BIN_ACCOUNTS["warm"]].values()) > 0.0
    if feedstock == "ci_carbonaceous_chondrite":
        assert frozen[C0_CRYO_BIN_ACCOUNTS["water"]]["H2O"] > 0.0
        assert frozen[C0_CRYO_BIN_ACCOUNTS["co2_so2"]]["CO2"] > 0.0
    before = dict(sim.train.stages[4].collected_kg)
    for _ in range(4):
        if sim.melt.campaign != CampaignPhase.C0B:
            break
        session.advance()
    assert {
        account: sim.atom_ledger.mol_by_account(account)
        for account in C0_CRYO_BIN_ACCOUNTS.values()
    } == frozen
    assert sim.train.stages[4].collected_kg.get("Na", 0.0) > before.get("Na", 0.0)
    assert abs(sim._make_snapshot().mass_balance_error_pct) < 5e-12
    assert forced_drains == []

    cryo_transitions = [
        transition for transition in sim.atom_ledger.transitions
        if any(lot.account in C0_CRYO_BIN_ACCOUNTS.values() for lot in transition.credits)
    ]
    assert cryo_transitions
    assert all(transition.name.startswith("condense_") for transition in cryo_transitions)


def test_noncondensables_pass_to_pump_without_cryo_credits():
    sim = _start().simulator
    rates = {"H2": 0.1, "He": 0.2, "N2": 0.3, "CO": 0.4, "CH4": 0.5}
    flux = EvaporationFlux(species_kg_hr=rates)
    flux.update_totals()
    sim._configure_condensation_operating_conditions(flux)
    before = sim.atom_ledger.mol_by_account()
    route = sim.condensation_model.route(flux, sim.melt)
    assert route.remaining_by_species == rates
    assert route.condensed_by_stage_species == {}
    assert route.condensed_for_species("H2") == 0.0
    assert sim.atom_ledger.mol_by_account() == before


@pytest.mark.parametrize(("inlet_mol_hr", "net_flux", "area_m2", "residence_s"), [
    (0.01, 1e-6, 0.01, 1.0),
    (0.01, 1e-6, 0.01, 7.0),
    (1e-5, 1e-6, 0.01, 1.0),
    (0.01, 0.0, 0.01, 1.0),
    (0.01, 1e-6, 0.0, 1.0),
])
def test_cryo_stream_capture_converts_seconds_to_hours(
    monkeypatch, inlet_mol_hr, net_flux, area_m2, residence_s,
):
    sim = _start().simulator
    model = sim.condensation_model
    mass_per_mol = resolve_species_formula("Na").molar_mass_kg_per_mol()
    flux = EvaporationFlux(species_kg_hr={"Na": inlet_mol_hr * mass_per_mol})
    flux.update_totals()
    sim._configure_condensation_operating_conditions(flux)
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0, species_partial_pressures_mbar={"Na": 1.0},
        stage_area_m2_by_stage={"terminal": area_m2},
    )
    for row in model.c0_cryo_stages:
        model.residence_time_s[row["stage"].stage_number] = residence_s
    monkeypatch.setattr(
        "simulator.condensation._series_resistance_deposition_flux_mol_m2_s",
        lambda *args, **kwargs: net_flux,
    )
    route = model.route(flux, sim.melt)
    # Prescribed J [mol/(m2 s)] times three areas [m2] times 3600 [s/h]
    # gives mol/h, independent of parcel residence. Supply clips the sum;
    # zero area or flux captures zero. The review probe gives 1.08e-4 mol/h.
    expected = min(inlet_mol_hr, net_flux * area_m2 * 3600 * len(model.c0_cryo_stages))
    assert route.condensed_for_species("Na") / mass_per_mol == pytest.approx(expected)
    assert route.remaining_by_species["Na"] / mass_per_mol == pytest.approx(inlet_mol_hr - expected)


def test_stage0_batch_capture_uses_the_existing_one_hour_process_step(monkeypatch):
    sim = _start().simulator
    mass_per_mol = resolve_species_formula("Na").molar_mass_kg_per_mol()
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas", {"Na": 0.01}, material_origin="feedstock",
    )
    monkeypatch.setattr(
        "simulator.condensation._series_resistance_deposition_flux_mol_m2_s",
        lambda *args, **kwargs: 1e-6,
    )
    monkeypatch.setattr(sim.condensation_model, "_stage_area_m2_for_stage_number", lambda stage: 0.01)
    before = sum(sim.atom_ledger.mol_by_account(account).get("Na", 0.0)
                 for account in C0_CRYO_BIN_ACCOUNTS.values())
    sim._route_stage0_volatiles_to_cryo({"Na": 0.01 * mass_per_mol})
    after = sum(sim.atom_ledger.mol_by_account(account).get("Na", 0.0)
                for account in C0_CRYO_BIN_ACCOUNTS.values())
    assert after - before == pytest.approx(1e-6 * 0.01 * 3600 * 3)


@pytest.mark.parametrize(("species", "reason"), [
    ("SiO2_gas", "not a wall saturation curve"),
    ("Na", "outside its finite positive-denominator branch"),
])
def test_missing_wall_thermodynamics_withholds_numeric_route(species, reason):
    sim = _start().simulator
    flux = EvaporationFlux(species_kg_hr={species: 1e-6})
    flux.update_totals()
    sim._configure_condensation_operating_conditions(flux)
    model = sim.condensation_model
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0, species_partial_pressures_mbar={species: 1.0},
    )
    route = model.route(flux, sim.melt)
    refusal = route.condensation_refusals_by_species[species]
    assert refusal["status"] == refusal["output_status"] == "refused"
    assert refusal["refusal_type"] == "WallSaturationPressureRefusal"
    assert reason in refusal["reason"]
    assert route.retained_in_source_by_species[species] == pytest.approx(1e-6)
    assert route.remaining_by_species[species] == 0.0
    assert route.condensed_for_species(species) == 0.0
    before = sim.atom_ledger.mol_by_account()
    outcome = sim._route_evaporated_species_to_condensation(route, species, 1e-6)
    assert outcome["evaporation_transition"] is None
    assert sim.atom_ledger.mol_by_account() == before
    assert model.last_condensation_refusals_by_species[species] == refusal
    from simulator.diagnostics import condensation_refusals_diagnostic
    assert condensation_refusals_diagnostic(sim)["by_species"][species]["status"] == "refused"


def test_already_evolved_silica_refuses_before_any_startup_capture(monkeypatch):
    sim = _start().simulator
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas", {"CO2": 0.01, "SiO2_gas": 0.01}, material_origin="feedstock",
    )
    before = sim.atom_ledger.mol_by_account()
    commits = []
    monkeypatch.setattr(sim, "_dispatch_condensation_route", lambda *args: commits.append(args))
    with pytest.raises(DepositionInputRefusal, match="not a wall saturation curve") as refused:
        sim._route_stage0_volatiles_to_cryo()
    assert refused.value.value == "SiO2_gas"
    assert commits == []
    assert sim.atom_ledger.mol_by_account() == before


def test_partial_co2_capture_preserves_each_mixed_headspace_origin():
    sim = _start().simulator
    sim.train.volatiles_gate_sealed = True
    ledger = sim.atom_ledger
    headspace = "process.overhead_gas"
    target = C0_CRYO_BIN_ACCOUNTS["co2_so2"]
    mass_per_mol = resolve_species_formula("CO2").molar_mass_kg_per_mol()
    ledger.load_external_mol(headspace, {"CO2": 3.0}, material_origin="feedstock")
    ledger.load_external_mol(headspace, {"He": 1.0}, material_origin="feedstock")
    ledger.load_external_mol(
        "process.stage0_volatile_feed", {"CO2": 1.0}, material_origin="reagent",
    )
    sim._dispatch_and_commit(
        ChemistryIntent.STAGE0_PRETREATMENT,
        control_inputs={
            "reaction_family": "volatilization", "carrier": "CO2",
            "feed_kg": mass_per_mol, "escaped_source_kg_override": mass_per_mol,
            "source_account": "process.stage0_volatile_feed",
            "phase_specs": [{"T_C": 950.0, "p_overhead_bar": 1e-5}],
            "foulant_registry": sim._load_foulant_registry_cached(),
        },
    )
    methods = ledger.origin_attribution_methods_by_account()[headspace]
    assert methods["C"] == methods["O"] == "pool_ratio"
    assert methods["He"] == "tracked"
    before = ledger.origin_atom_moles_by_account()[headspace]
    sim._dispatch_and_commit(
        ChemistryIntent.CONDENSATION_ROUTE,
        control_inputs={
            "species": "CO2", "condensed_kg": mass_per_mol,
            "condensation_bin_fractions": {target: 1.0},
        },
    )
    transition = ledger.transitions[-1]
    debit = transition.debits[0]
    # Four mol CO2 contain C:O = 4:8 mol-atoms with a 3:1 origin ratio.
    # A one-mol capture removes one quarter of each origin's atoms.
    assert dict(debit.origin_atom_moles["feedstock"]) == pytest.approx({"C": 0.75, "O": 1.5})
    assert dict(debit.origin_atom_moles["reagent"]) == pytest.approx({"C": 0.25, "O": 0.5})
    assert debit.attribution_method_by_element == {"C": "pool_ratio", "O": "pool_ratio"}
    after = ledger.origin_atom_moles_by_account()
    for element in ("C", "O"):
        for origin in ("feedstock", "reagent"):
            assert (after[headspace][element][origin] + after[target][element][origin]
                    == pytest.approx(before[element][origin]))
    assert ledger.mol_by_account(headspace)["CO2"] == pytest.approx(3.0)
    assert ledger.mol_by_account(target)["CO2"] == pytest.approx(1.0)


@pytest.mark.parametrize("species", ["CH4_NH3_HCN", "C"])
def test_unresolved_cryo_carrier_is_a_typed_input_refusal(species):
    sim = _start().simulator
    flux = EvaporationFlux(species_kg_hr={species: 1e-6})
    flux.update_totals()
    sim._configure_condensation_operating_conditions(flux)
    before = sim.atom_ledger.mol_by_account()
    with pytest.raises(DepositionInputRefusal) as refused:
        sim.condensation_model.route(flux, sim.melt)
    assert refused.value.parameter == "molar_mass_g_mol"
    assert refused.value.value == species
    assert species in refused.value.reason
    assert "no usable molar mass" in refused.value.reason
    assert refused.value.terminal_refusal
    assert sim.atom_ledger.mol_by_account() == before


@pytest.mark.parametrize(("species", "bin_name"), [
    ("K", "warm"), ("NaCl", "warm"), ("KCl", "warm"),
    ("H2O", "water"), ("CO2", "co2_so2"), ("SO2", "co2_so2"),
])
def test_condensables_reach_temperature_bins(species, bin_name):
    sim = _start().simulator
    flux = EvaporationFlux(species_kg_hr={species: 1e-6})
    flux.update_totals()
    sim._configure_condensation_operating_conditions(flux)
    sim.condensation_model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        species_partial_pressures_mbar={species: 1.0},
    )
    route = sim.condensation_model.route(flux, sim.melt)
    assert route.condensation_authority_by_species[species]["status"] == "missing"
    stage = next(
        number for number, account in route.cryo_bin_accounts.items()
        if account == C0_CRYO_BIN_ACCOUNTS[bin_name]
    )
    assert route.condensed_by_stage_species.get(stage, {}).get(species, 0.0) > 0.0
    assert route.condensed_for_species(species) + route.remaining_by_species[species] == pytest.approx(1e-6)


@pytest.mark.parametrize(("captured", "feed", "fraction", "flagged"), [
    (1.0, 1000.0, 1e-3, False),
    (1.001, 1000.0, 0.001001, True),
    (0.0, 0.0, None, False),
])
def test_d244_purity_boundary_uses_feed_atoms(captured, feed, fraction, flagged):
    ledger = SimpleNamespace(
        atom_moles_by_account=lambda account: (
            {"Na": captured} if account == C0_CRYO_BIN_ACCOUNTS["warm"] else {}
        ),
        external_origin_atom_moles=lambda: {"feedstock": {"Na": feed}},
    )
    result = AccountingQueries(SimpleNamespace(atom_ledger=ledger)).c0_cryo_alkali_purity()
    if fraction is None:
        assert result["fraction_of_feed"] is None
        assert result["status"] == "no_feed_alkali"
    else:
        assert result["fraction_of_feed"] == pytest.approx(fraction)
    assert result["exceeds_limit"] is flagged
