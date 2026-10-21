"""Independent mol hand balances through the real provider/kernel path."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from engines.builtin.evaporation_transition import BuiltinEvaporationTransitionProvider
from simulator.account_ids import CROWN_STATION_ACCOUNTS
from simulator.accounting.ledger import AtomLedger, KNOWN_LEDGER_ACCOUNTS
from simulator.accounting.queries import AccountingQueries
from simulator.accounting.yield_disposition import build_yield_disposition
from simulator.chemistry.kernel import (
    AccountFilterViolation, AtomBalanceError, ChemistryIntent, ChemistryKernel,
    LedgerTransitionProposal, ProposalRejected, ProviderRegistry, UnauthorizedIntentError,
)
from simulator.chemistry.kernel.account_filters import build_provider_account_view
from simulator.chemistry.kernel.validation import validate_proposal_accounts
from simulator.condensation import CrownStationRefusal, crown_carrier_allocation
from simulator.core import PyrolysisSimulator

INTENT = ChemistryIntent.EVAPORATION_TRANSITION
MELT = "process.cleaned_melt"
TRAIN = "process.condensation_train"
SKY = "terminal.offgas"
VENT = "terminal.oxygen_melt_offgas_vented_to_vacuum"


def _system(feed, dish=None):
    ledger = AtomLedger(allowed_accounts=KNOWN_LEDGER_ACCOUNTS)
    ledger.load_external_mol(MELT, feed, material_origin="feedstock")
    for station, stock in (dish or {}).items():
        ledger.load_external_mol(CROWN_STATION_ACCOUNTS[station], stock,
                                 material_origin="feedstock")
    registry = ProviderRegistry()
    registry.register(BuiltinEvaporationTransitionProvider(), [INTENT])
    kernel = ChemistryKernel(ledger, registry, {})
    sim = SimpleNamespace(atom_ledger=ledger)
    return ledger, kernel, AccountingQueries(sim), sim


def _allocation(forward, *, capture=1., stock=0., reemission=0., return_view=0.):
    return crown_carrier_allocation(
        forward, reemission, stock, 1., view_factor_from_melt=capture,
        return_view_factor=return_view, sticking=1., return_accommodation=1.)


def _dispatch(kernel, allocations=None, parent=None, *, station="mg", operation="collect", duration_s=1.):
    return kernel.dispatch(INTENT, temperature_C=1726.85, pressure_bar=0.,
                           control_inputs={
                               "crown_station": station, "crown_operation": operation,
                               "crown_allocations": allocations or {},
                               "parent_debit_mol": parent or {}, "duration_s": duration_s,
                           })


def _commit(kernel, *args, **kwargs):
    result = _dispatch(kernel, *args, **kwargs)
    if result.transition is not None:
        kernel.commit_batch(INTENT, result.transition,
                            transition_source="crown hand balance")
    return result


def _closed(ledger, sim):
    ledger.assert_balanced()
    total = sum(ledger.total_kg_by_account().values())
    # The actual run mass query must include the new process inventories.
    assert PyrolysisSimulator._flow_mass_out_kg(sim) == pytest.approx(total, rel=5e-14)
    payload = build_yield_disposition(sim)
    assert payload["closure"]["maximum_residual_fraction"] * 100 <= 5e-12
    for transition in ledger.transitions:
        assert abs(transition.debit_mass_kg() - transition.credit_mass_kg()) / total * 100 <= 5e-12
    # Raw ledger balances retain signed cancellation dust by policy. The
    # canonical physical-inventory projection refuses material negatives.
    assert all(n >= 0 for account in ledger.mol_by_account()
               for n in ledger.project_account_mol(account).values())
    return payload


@pytest.mark.parametrize("station, metal, bound", [("mg", "Mg", .6583), ("ca", "Ca", .3992)])
@pytest.mark.parametrize("atomic", [False, True])
def test_one_mol_hand_balance_and_oxygen_bound(station, metal, bound, atomic):
    ledger, kernel, query, sim = _system({metal + "O": 1.})
    oxygen = {"O": _allocation(1., capture=0.)} if atomic else {"O2": _allocation(.5, capture=0.)}
    result = _commit(kernel, {metal: _allocation(1.), **oxygen}, {metal + "O": 1.}, station=station)
    assert result.transition.debits == {MELT: {metal + "O": 1.}}
    assert ledger.mol_by_account(CROWN_STATION_ACCOUNTS[station]) == {metal: 1.}
    assert ledger.mol_by_account(VENT) == ({} if atomic else {"O2": .5})
    assert ledger.mol_by_account(SKY) == ({"O": 1.} if atomic else {})
    vent = query.vented_free_oxygen_mol()
    assert vent["O2_equivalent_mol"] == .5
    assert vent["oxygen_atom_mol"] == 1.
    # CIAAW/NIST molar masses: 15.999 g O per 24.305 g Mg / 40.078 g Ca.
    # kg/kg equals t/t. These upper bounds are rounded upward in the brief.
    assert vent["oxygen_kg"] / ledger.total_kg_by_account(CROWN_STATION_ACCOUNTS[station]) <= bound
    assert query.crown_products_mol()[station]["elemental_metal_mass_fraction"] == 1.
    _closed(ledger, sim)


def test_returns_reduce_parent_debit_once_and_sky_is_not_product():
    ledger, kernel, query, sim = _system({"MgO": 1.})
    metal = _allocation(1., capture=.5, reemission=.2, return_view=.5)
    # Independently: E=1, R=.2, return=.1, D=.9, dish=.3, sky=.6.
    oxygen = crown_carrier_allocation(.6, 0., 0., 1., view_factor_from_melt=.5,
                                     return_view_factor=.5, sticking=0., return_accommodation=1.)
    _commit(kernel, {"Mg": metal, "O2": oxygen}, {"MgO": .9})
    assert ledger.mol_by_account(MELT)["MgO"] == pytest.approx(.1)
    assert ledger.mol_by_account("process.mg_crown")["Mg"] == pytest.approx(.3)
    assert ledger.mol_by_account(SKY)["Mg"] == pytest.approx(.6)
    assert ledger.mol_by_account(VENT)["O2"] == pytest.approx(.45)
    assert query.crown_products_mol()["mg"]["products_mol"]["Mg"] == pytest.approx(.3)
    payload = _closed(ledger, sim)
    row = next(r for r in payload["fraction_table"]["rows"] if r["element"] == "Mg")
    assert row["destination_fractions"]["product_condensed"] == pytest.approx(.3)
    assert row["destination_fractions"]["offgas_vented"] == pytest.approx(.6)


def test_oxide_carriers_keep_formula_and_captured_oxygen_reduces_venting():
    ledger, kernel, query, sim = _system({"MgO": 1.})
    # 0.4 Mg + 0.3 MgO + 0.15 Mg2 contain 1 Mg and 0.3 O.
    # The remaining oxygen is 0.3 O + 0.2 O2; no getter is assumed.
    allocations = {"Mg": _allocation(.4, capture=.75),
                   "MgO": _allocation(.3, capture=2/3),
                   "Mg2": _allocation(.15, capture=2/3),
                   "O": _allocation(.3, capture=0.), "O2": _allocation(.2, capture=0.)}
    _commit(kernel, allocations, {"MgO": 1.})
    assert ledger.mol_by_account("process.mg_crown") == pytest.approx({"Mg": .3, "MgO": .2, "Mg2": .1})
    assert ledger.mol_by_account(SKY) == pytest.approx({"Mg": .1, "MgO": .1, "Mg2": .05, "O": .3})
    assert query.vented_free_oxygen_mol()["O2_equivalent_mol"] == pytest.approx(.35)
    assert query.vented_free_oxygen_mol()["oxygen_kg"] < .015999
    purity = query.crown_products_mol()["mg"]["elemental_metal_mass_fraction"]
    # The retained Mg + Mg2 are 0.5 mol Mg atoms, and MgO is 0.2 mol.
    # CIAAW atomic weights give 12.1525 g elemental Mg / 20.2133 g total.
    assert purity == pytest.approx(12.1525 / 20.2133)
    _closed(ledger, sim)


def test_joint_source_station_route_integrates_area_and_time_once():
    from simulator.condensation import crown_station
    from simulator.refractory_vaporization import solve_melt_vaporization
    from tests.test_crown_station import ALPHA, LAWS, GEOMETRY, ACTIVITIES

    ledger, kernel, _, sim = _system({"MgO": 1., "CaO": 1.})
    area, duration = 2., 3.
    geometry = GEOMETRY | {"source_temperature_K": 2100., "melt_area_m2": area,
                           "collector_area_m2": area, "duration_s": duration}

    def boundary(pressures, forward):
        station = crown_station(pressures, ALPHA, LAWS, {}, **geometry)
        return {s: a.net_source_mol_s / area for s, a in station.allocations.items()}

    source = solve_melt_vaporization(2100., ACTIVITIES, ALPHA, net_flux_boundary=boundary)
    station = crown_station(source.pressures_pa, ALPHA, LAWS, {}, **geometry)
    _commit(kernel, station.allocations,
            {s: n * area * duration for s, n in source.oxide_debit_mol_m2_s.items()},
            duration_s=duration)
    for species, allocation in station.allocations.items():
        assert ledger.project_account_mol("process.mg_crown").get(species, 0.) == pytest.approx(allocation.inventory_mol)
    _closed(ledger, sim)


def test_dishes_are_isolated_and_harvest_once():
    ledger, kernel, query, sim = _system({"MgO": 1.}, {"ca": {"Mg": .2, "Ca": .8}})
    allocations = {"Mg": _allocation(1.), "O2": _allocation(.5, capture=0.)}
    _commit(kernel, allocations, {"MgO": 1.})
    with pytest.raises(CrownStationRefusal, match="stale"):
        _dispatch(kernel, allocations, {"MgO": 1.})
    assert ledger.mol_by_account(TRAIN) == {}
    _commit(kernel, operation="harvest")
    assert ledger.mol_by_account(TRAIN) == {"Mg": 1.}
    assert ledger.mol_by_account("process.ca_crown") == {"Mg": .2, "Ca": .8}
    assert _commit(kernel, operation="harvest").transition is None
    assert query.crown_products_mol()["mg"]["harvested_mol"] == {"Mg": 1.}
    assert query.crown_products_mol()["mg"]["inventory_mol"] == {}
    _commit(kernel, operation="harvest", station="ca")
    assert ledger.mol_by_account(TRAIN) == pytest.approx({"Mg": 1.2, "Ca": .8})
    assert _commit(kernel, operation="harvest", station="ca").transition is None
    _closed(ledger, sim)


@pytest.mark.parametrize("capture", [0., 1.])
@pytest.mark.parametrize("operation", ["collect", "harvest"])
def test_crown_commit_rejects_replay_and_two_predispatched_allocations(capture, operation):
    ledger, kernel, _, sim = _system({"MgO": 2.}, {"mg": {"Mg": 1.}})
    controls = {"operation": operation}
    if operation == "collect":
        controls.update(allocations={"Mg": _allocation(1., stock=1., capture=capture),
                                     "O2": _allocation(.5, capture=0.)},
                        parent={"MgO": 1.})
    first = _dispatch(kernel, **controls).transition
    duplicate = _dispatch(kernel, **controls).transition
    kernel.commit_batch(INTENT, first)
    before = ledger.mol_by_account()
    with pytest.raises(UnauthorizedIntentError):
        kernel.commit_batch(INTENT, first)
    with pytest.raises(ProposalRejected, match="stale crown"):
        kernel.commit_batch(INTENT, duplicate)
    assert ledger.mol_by_account() == before
    _closed(ledger, sim)


def test_harvested_mg_uses_existing_c6_reagent_recovery():
    from tests.chemistry.conftest import _build_sim, _load_yaml

    sim = _build_sim("lunar_mare_low_ti", _load_yaml("vapor_pressures.yaml"),
                     _load_yaml("feedstocks.yaml"), _load_yaml("setpoints.yaml"))
    sim.atom_ledger.load_external_mol("process.mg_crown", {"Mg": 1.}, material_origin="feedstock")
    _commit(sim._chem_kernel, operation="harvest")
    before = sim.atom_ledger.mol_by_account("process.reagent_inventory").get("Mg", 0.)
    assert sim._transfer_condensed_species("Mg") > 0
    assert sim.atom_ledger.mol_by_account("process.reagent_inventory")["Mg"] == pytest.approx(before + 1.)
    assert sim._transfer_condensed_species("Mg") == 0.
    assert _commit(sim._chem_kernel, operation="harvest").transition is None
    sim.atom_ledger.assert_balanced()


def test_stock_reemission_is_a_dish_debit_not_second_melt_debit():
    ledger, kernel, query, sim = _system({"MgO": 1.}, {"mg": {"Mg": .2}})
    _commit(kernel, {"Mg": _allocation(0., capture=0., stock=.2, reemission=1.)})
    assert ledger.mol_by_account(MELT) == {"MgO": 1.}
    assert query.crown_products_mol()["mg"]["products_mol"].get("Mg", 0.) == 0.
    assert ledger.mol_by_account(SKY) == {"Mg": .2}
    _closed(ledger, sim)


def test_no_negative_inventory_or_unbalanced_source_and_no_forged_commit():
    ledger, kernel, _, sim = _system({"MgO": .5})
    allocations = {"Mg": _allocation(1.), "O2": _allocation(.5, capture=0.)}
    proposal = _dispatch(kernel, allocations, {"MgO": 1.}).transition
    before = ledger.mol_by_account()
    with pytest.raises(ProposalRejected):
        kernel.commit_batch(INTENT, proposal)
    assert ledger.mol_by_account() == before
    with pytest.raises(AtomBalanceError):
        _dispatch(kernel, allocations, {"MgO": .5})
    with pytest.raises(UnauthorizedIntentError):
        kernel.commit_batch(INTENT, LedgerTransitionProposal(debits={MELT: {"MgO": .5}}, credits={}))
    with pytest.raises(UnauthorizedIntentError):
        kernel.commit_batch(ChemistryIntent.CONDENSATION_ROUTE, proposal)
    _closed(ledger, sim)


def test_filters_and_no_undeclared_account_authority():
    ledger, _, _, _ = _system({"MgO": 1.}, {"mg": {"Mg": 1.}, "ca": {"Ca": 2.}})
    view = build_provider_account_view(ledger, frozenset({"process.mg_crown"}), {})
    assert set(view.accounts) == {"process.mg_crown"}
    with pytest.raises(AccountFilterViolation):
        validate_proposal_accounts(LedgerTransitionProposal(debits={"process.ca_crown": {"Ca": 1.}}, credits={}),
                                   frozenset({"process.mg_crown"}))


def test_no_implicit_oxidation_or_invalid_operation():
    _, kernel, _, _ = _system({"MgO": 1.})
    with pytest.raises(CrownStationRefusal, match="oxidation"):
        _dispatch(kernel, {"Mg": replace(_allocation(1.), oxidized_mol_s=.1)}, {"MgO": 1.})
    with pytest.raises(CrownStationRefusal, match="unknown crown station"):
        _dispatch(kernel, station="fe")
    with pytest.raises(CrownStationRefusal, match="cannot also collect"):
        _dispatch(kernel, {"Mg": _allocation(1.)}, operation="harvest")
