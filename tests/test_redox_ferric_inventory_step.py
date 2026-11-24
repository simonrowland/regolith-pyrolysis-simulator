"""Pins for the melt fO2 step shared by the two oxygen-reservoir sites.

A reducing source term with ferric still on the ledger books
min(demand, bookable O2) through the Fe split, whatever C_m is.
Bookable O2 is 1/2 mol per mol Fe2O3 (Kress & Carmichael 1991:
Fe2O3 → 2 FeO + 1/2 O2). The published fO2 is the Kress inverse of
the post-split ledger, floored at Fe-metal saturation on the melt's
a_FeO. The tangent Δln fO2 = n_O2 / C_m is the predictor for an
oxidizing step, for the exchange, and for a reducing step whose
ferric inventory is already empty.
"""

from __future__ import annotations

import math

import pytest

from engines.builtin.fe_redox_respeciation import (
    fe2o3_mol_for_ferric_o2,
    ferric_couple_o2_mol,
)
from simulator.accounting import resolve_species_formula
from simulator.accounting.ledger import LedgerTransition
from simulator.accounting.lots import MaterialLot
from simulator.core import OXYGEN_RESERVOIR_NOOP_MOL, PyrolysisSimulator
from simulator.feedstock_composition import iron_oxide_values
from tests.magemin_test_support import close_test_owned_magemin_backend
from tests.test_sso_r_r20_state import _make_sim


pytestmark = [pytest.mark.serial, pytest.mark.xdist_group("serial")]

_BASE_LOG = -4.0
_CAPACITY_MOL_PER_LN = 1.0e-3
_STEP_MOL = 1.0e-4
_TEMPERATURE_C = 1600.0
_TEMPERATURE_K = _TEMPERATURE_C + 273.15


@pytest.fixture
def sim():
    owned = _make_sim()
    try:
        yield owned
    finally:
        close_test_owned_magemin_backend(owned)


def _oxygen_atoms(simulator: PyrolysisSimulator) -> float:
    return math.fsum(
        float(simulator.atom_ledger.atom_moles_by_account(account).get("O", 0.0))
        for account in simulator.atom_ledger.mol_by_account()
    )


def _bookable_ferric_o2(simulator: PyrolysisSimulator) -> float:
    melt_mol = simulator.atom_ledger.project_account_mol("process.cleaned_melt")
    _feo_mol, fe2o3_mol = iron_oxide_values(melt_mol)
    return ferric_couple_o2_mol(fe2o3_mol)


def _pin_tangent(
    simulator: PyrolysisSimulator,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fe2o3_mol: float,
    base_log: float = _BASE_LOG,
    capacity_mol_per_ln: float = _CAPACITY_MOL_PER_LN,
) -> None:
    simulator.melt.temperature_C = _TEMPERATURE_C
    simulator.melt.p_total_mbar = 10.0
    simulator.melt.oxygen_reservoir.melt_intrinsic_fO2_log = base_log
    simulator.melt.oxygen_reservoir.reference_T_K = _TEMPERATURE_K
    simulator._sync_oxygen_reservoir_mirror()
    if fe2o3_mol > 0.0:
        simulator.atom_ledger.load_external_mol(
            "process.cleaned_melt",
            {"Fe2O3": fe2o3_mol},
            source="test ferric inventory above the Euler demand",
            material_origin="feedstock",
        )
    monkeypatch.setattr(
        simulator,
        "_melt_redox_capacity_mol_per_ln_fO2",
        lambda **_kwargs: capacity_mol_per_ln,
    )
    monkeypatch.setattr(
        simulator,
        "_melt_redox_liquid_fraction_factor",
        lambda *_args, **_kwargs: 1.0,
    )
    # The Fe split is skipped as solid when the gate curve is absent.
    # Booking tests force the liquid gate so they measure the split.
    monkeypatch.setattr(
        simulator,
        "_melt_redox_temperature_shift_is_liquid",
        lambda *_args, **_kwargs: True,
    )


@pytest.mark.parametrize(
    ("gap", "exhausted"),
    [("two_ulp", False), ("1e-9", True)],
)
def test_ferric_handover_guard_scales_the_source_comparison(
    sim: PyrolysisSimulator,
    monkeypatch: pytest.MonkeyPatch,
    gap: str,
    exhausted: bool,
) -> None:
    _pin_tangent(sim, monkeypatch, fe2o3_mol=10.0)
    bookable = _bookable_ferric_o2(sim)
    demand = (
        math.nextafter(math.nextafter(bookable, math.inf), math.inf)
        if gap == "two_ulp"
        else bookable + 1e-9
    )

    sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:test_roundoff": -demand},
        temperature_K=_TEMPERATURE_K,
    )

    assert sim._ferric_tangent_exhausted is exhausted
    if exhausted:
        assert sim._ferric_exhausted_bookable_o2_mol == pytest.approx(bookable)


@pytest.mark.parametrize(
    ("gap", "exhausted"),
    [("two_ulp", False), ("1e-9", True)],
)
def test_ferric_tangent_guard_scales_the_source_comparison(
    sim: PyrolysisSimulator,
    monkeypatch: pytest.MonkeyPatch,
    gap: str,
    exhausted: bool,
) -> None:
    bookable = 5.0
    monkeypatch.setattr(sim, "_bookable_ferric_o2_mol", lambda: bookable)
    demand = (
        math.nextafter(math.nextafter(bookable, math.inf), math.inf)
        if gap == "two_ulp"
        else bookable + 1e-9
    )

    _, _, ferric_exhausted = sim._melt_redox_tangent_fO2_step(
        signed_o2_equiv_mol=-demand,
        base_fO2_log=_BASE_LOG,
        capacity_mol_per_ln=1.0,
        apply_pressure_ceiling=False,
    )

    assert ferric_exhausted is exhausted


def test_ferric_lock_reappearance_ignores_two_ulp_inventory_noise(
    sim: PyrolysisSimulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen = 5.0
    sim._ferric_tangent_exhausted = True
    sim._ferric_exhausted_bookable_o2_mol = frozen
    current = math.nextafter(math.nextafter(frozen, math.inf), math.inf)
    monkeypatch.setattr(sim, "_bookable_ferric_o2_mol", lambda: current)

    assert sim._ferric_inventory_reappeared() is False

    monkeypatch.setattr(
        sim,
        "_bookable_ferric_o2_mol",
        lambda: frozen + 1e-9,
    )
    assert sim._ferric_inventory_reappeared() is True


def test_in_inventory_reducing_source_step_is_demand_over_capacity(
    sim: PyrolysisSimulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1 mol Fe2O3 books 0.5 mol O2. The demand is 1e-4 mol, inside the
    # inventory. The split books that O2. fO2 is the ledger, not n/C_m.
    _pin_tangent(sim, monkeypatch, fe2o3_mol=1.0)
    assert _bookable_ferric_o2(sim) > _STEP_MOL
    before_atoms = _oxygen_atoms(sim)
    before_fe2o3 = sim.atom_ledger.mol_by_account("process.cleaned_melt")["Fe2O3"]

    reservoir = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:test_reducing": -_STEP_MOL},
        temperature_K=_TEMPERATURE_K,
    )

    euler = _BASE_LOG + (-_STEP_MOL / _CAPACITY_MOL_PER_LN) / math.log(10.0)
    published = sim._ledger_consistent_melt_fO2_log(_TEMPERATURE_K)
    assert reservoir.melt_intrinsic_fO2_log == pytest.approx(published)
    assert reservoir.melt_intrinsic_fO2_log != pytest.approx(euler)
    assert reservoir.melt_redox_capacity_mol_per_ln_fO2 == pytest.approx(
        _CAPACITY_MOL_PER_LN
    )
    assert reservoir.redox_source_delta_ln_fO2 == pytest.approx(
        (published - _BASE_LOG) * math.log(10.0)
    )
    assert reservoir.redox_source_terms_applied is True
    assert reservoir.redox_source_skip_reason == ""
    assert sim._ferric_tangent_exhausted is False
    assert sim.atom_ledger.mol_by_account("process.cleaned_melt")["Fe2O3"] == (
        pytest.approx(before_fe2o3 - fe2o3_mol_for_ferric_o2(_STEP_MOL))
    )
    assert _oxygen_atoms(sim) == pytest.approx(before_atoms, abs=1.0e-9)


def test_oxidizing_source_step_is_demand_over_capacity(
    sim: PyrolysisSimulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_tangent(sim, monkeypatch, fe2o3_mol=0.0)
    before_atoms = _oxygen_atoms(sim)

    reservoir = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:test_oxidizing": _STEP_MOL},
        temperature_K=_TEMPERATURE_K,
    )

    expected = _BASE_LOG + (_STEP_MOL / _CAPACITY_MOL_PER_LN) / math.log(10.0)
    assert reservoir.melt_intrinsic_fO2_log == pytest.approx(expected)
    assert reservoir.melt_intrinsic_fO2_log < math.log10(10.0 / 1000.0)
    assert reservoir.redox_source_terms_applied is True
    assert reservoir.redox_source_skip_reason == ""
    assert _oxygen_atoms(sim) == pytest.approx(before_atoms, abs=0.0, rel=0.0)


def test_in_inventory_exchange_step_is_ledger_dn_over_capacity(
    sim: PyrolysisSimulator,
) -> None:
    sim.melt.temperature_C = _TEMPERATURE_C
    sim.melt.p_total_mbar = 10.0
    sim.atom_ledger.load_external_mol(
        "process.cleaned_melt",
        {"Fe2O3": 2.0},
        source="test ferric inventory above one exchange step",
        material_origin="feedstock",
    )
    head_o2_mol = 1.0e-2
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"O2": head_o2_mol},
        source="test headspace O2 for a small reducing exchange",
        material_origin="feedstock",
    )
    sim.atom_ledger.load_external_mol(
        "reservoir.fo2_buffer",
        {"O2": 1.0},
        source="test buffer O2 backing a reducing exchange",
        material_origin="feedstock",
    )
    drive_pO2 = sim._overhead_exchange_drive_pO2_bar()
    log_drive_pO2 = max(drive_pO2, sim._oxygen_numerical_floor_bar())
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = (
        math.log10(log_drive_pO2) + 0.25
    )
    sim.melt.oxygen_reservoir.reference_T_K = _TEMPERATURE_K
    sim._sync_oxygen_reservoir_mirror()
    before = sim._current_melt_redox_fO2_log()
    before_atoms = _oxygen_atoms(sim)
    bookable = _bookable_ferric_o2(sim)

    reservoir = sim._apply_oxygen_reservoir_exchange()

    assert reservoir.exchange_direction == "melt_to_headspace"
    assert reservoir.exchange_o2_mol > OXYGEN_RESERVOIR_NOOP_MOL
    assert reservoir.exchange_o2_mol <= bookable
    capacity = reservoir.melt_redox_capacity_mol_per_ln_fO2
    assert capacity > OXYGEN_RESERVOIR_NOOP_MOL
    expected = before - (
        reservoir.exchange_o2_mol / capacity
    ) / math.log(10.0)
    assert reservoir.melt_intrinsic_fO2_log == pytest.approx(expected)
    assert _oxygen_atoms(sim) == pytest.approx(before_atoms)


# Robinot default, minute 42. The uncapped division lands at -19.74.
# The ferric couple on that hour can book 1.790e-4 mol O2.
_M42_BASE = -1.886
_M42_DEMAND_MOL = 0.001216111
_M42_UNCAPPED_DEX = 17.853327
_M42_BOOKABLE_MOL = 1.790e-4


def test_minute42_reducing_step_stops_at_ferric_inventory(
    sim: PyrolysisSimulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capacity = _M42_DEMAND_MOL / (_M42_UNCAPPED_DEX * math.log(10.0))
    _pin_tangent(
        sim,
        monkeypatch,
        fe2o3_mol=fe2o3_mol_for_ferric_o2(_M42_BOOKABLE_MOL),
        base_log=_M42_BASE,
        capacity_mol_per_ln=capacity,
    )
    # The diagnosis's chamber is ~13 mbar, so -1.886 is the gas ceiling
    # rather than a point above it.
    sim.melt.p_total_mbar = 13.0
    assert _bookable_ferric_o2(sim) == pytest.approx(_M42_BOOKABLE_MOL)
    before_atoms = _oxygen_atoms(sim)
    before_fe2o3 = sim.atom_ledger.mol_by_account("process.cleaned_melt")["Fe2O3"]

    reservoir = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:evaporative_oxygen_loss": -_M42_DEMAND_MOL},
        temperature_K=_TEMPERATURE_K,
    )

    # The inventory-capped Euler (about -4.51 from this base) and the
    # uncapped landing near -19.74 are predictors. The published state
    # is the ledger after the bookable ferric is removed.
    limited_dex = _M42_BOOKABLE_MOL / capacity / math.log(10.0)
    capped_euler = _M42_BASE - limited_dex
    uncapped = _M42_BASE - _M42_UNCAPPED_DEX
    published = sim._ledger_consistent_melt_fO2_log(_TEMPERATURE_K)
    assert uncapped == pytest.approx(-19.739327, abs=1.0e-4)
    assert reservoir.melt_intrinsic_fO2_log == pytest.approx(published)
    assert reservoir.melt_intrinsic_fO2_log != pytest.approx(capped_euler)
    assert reservoir.melt_intrinsic_fO2_log != pytest.approx(uncapped)
    assert reservoir.redox_source_terms_applied is True
    assert reservoir.redox_source_skip_reason == ""
    assert reservoir.ferric_divergence["ferric_tangent_reasons"] == [
        "ferric_inventory_handover",
    ]
    assert reservoir.melt_redox_capacity_mol_per_ln_fO2 == pytest.approx(
        OXYGEN_RESERVOIR_NOOP_MOL
    )
    assert sim._ferric_tangent_exhausted is True
    assert sim._ferric_exhausted_bookable_o2_mol == pytest.approx(
        _M42_BOOKABLE_MOL
    )
    assert sim.atom_ledger.mol_by_account("process.cleaned_melt")["Fe2O3"] == (
        pytest.approx(
            before_fe2o3 - fe2o3_mol_for_ferric_o2(_M42_BOOKABLE_MOL),
            abs=1.0e-9,
        )
    )
    assert _bookable_ferric_o2(sim) < 1.0e-9
    assert _oxygen_atoms(sim) == pytest.approx(before_atoms, abs=1.0e-9)

    sim._reset_redox_source_diagnostics_for_hour()
    assert sim._ferric_tangent_exhausted is True
    second = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:evaporative_oxygen_loss": -_M42_DEMAND_MOL},
        temperature_K=_TEMPERATURE_K,
    )
    assert second.melt_intrinsic_fO2_log == pytest.approx(published)
    assert second.redox_source_terms_applied is False
    assert second.redox_source_skip_reason == "no_melt_redox_capacity"


def test_zero_ferric_inventory_skips_the_reducing_step(
    sim: PyrolysisSimulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_tangent(sim, monkeypatch, fe2o3_mol=0.0)
    assert _bookable_ferric_o2(sim) == pytest.approx(0.0)
    before_atoms = _oxygen_atoms(sim)

    reservoir = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:test_reducing": -_STEP_MOL},
        temperature_K=_TEMPERATURE_K,
    )

    assert reservoir.melt_intrinsic_fO2_log == pytest.approx(_BASE_LOG)
    assert reservoir.redox_source_terms_applied is False
    assert reservoir.redox_source_skip_reason == "ferric_couple_exhausted"
    assert reservoir.ferric_divergence["ferric_tangent_reasons"] == [
        "ferric_couple_exhausted",
        "ferric_inventory_handover",
    ]
    assert sim._redox_source_breakdown_diagnostic()["ferric_divergence"][
        "ferric_tangent_reasons"
    ] == [
        "ferric_couple_exhausted",
        "ferric_inventory_handover",
    ]
    assert reservoir.melt_redox_capacity_mol_per_ln_fO2 == pytest.approx(
        OXYGEN_RESERVOIR_NOOP_MOL
    )
    assert sim._ferric_tangent_exhausted is True
    assert _oxygen_atoms(sim) == pytest.approx(before_atoms, abs=0.0, rel=0.0)


def _arm_reducing_exchange(
    simulator: PyrolysisSimulator,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fe2o3_mol: float,
) -> None:
    simulator.melt.temperature_C = _TEMPERATURE_C
    simulator.melt.p_total_mbar = 10.0
    if fe2o3_mol > 0.0:
        simulator.atom_ledger.load_external_mol(
            "process.cleaned_melt",
            {"Fe2O3": fe2o3_mol},
            source="test ferric inventory for a capped exchange",
            material_origin="feedstock",
        )
    head_o2_mol = 1.0
    simulator.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"O2": head_o2_mol},
        source="test headspace O2 for a capped exchange",
        material_origin="feedstock",
    )
    simulator.atom_ledger.load_external_mol(
        "reservoir.fo2_buffer",
        {"O2": 5.0},
        source="test buffer O2 the exchange must still move",
        material_origin="feedstock",
    )
    monkeypatch.setattr(
        simulator,
        "_melt_redox_capacity_mol_per_ln_fO2",
        lambda **_kwargs: 1.0,
    )
    monkeypatch.setattr(
        simulator,
        "_melt_redox_liquid_fraction_factor",
        lambda *_args, **_kwargs: 1.0,
    )
    drive_pO2 = simulator._overhead_exchange_drive_pO2_bar()
    log_drive_pO2 = max(drive_pO2, simulator._oxygen_numerical_floor_bar())
    simulator.melt.oxygen_reservoir.melt_intrinsic_fO2_log = (
        math.log10(log_drive_pO2) + 1.0
    )
    simulator.melt.oxygen_reservoir.reference_T_K = _TEMPERATURE_K
    simulator._sync_oxygen_reservoir_mirror()


def test_exchange_past_ferric_inventory_keeps_the_ledger_dn(
    sim: PyrolysisSimulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fe2o3_mol = 1.0e-6
    _arm_reducing_exchange(sim, monkeypatch, fe2o3_mol=fe2o3_mol)
    bookable = _bookable_ferric_o2(sim)
    assert bookable == pytest.approx(ferric_couple_o2_mol(fe2o3_mol))
    before = sim._current_melt_redox_fO2_log()
    capacity = sim._melt_redox_source_capacity_mol_per_ln_fO2(
        fO2_log=before,
        T_K=_TEMPERATURE_K,
    )
    assert capacity == pytest.approx(1.0)
    before_atoms = _oxygen_atoms(sim)
    before_buffer = sim.atom_ledger.mol_by_account("reservoir.fo2_buffer")["O2"]

    reservoir = sim._apply_oxygen_reservoir_exchange()

    assert reservoir.exchange_direction == "melt_to_headspace"
    assert reservoir.exchange_o2_mol > bookable + OXYGEN_RESERVOIR_NOOP_MOL
    published = sim._ledger_consistent_melt_fO2_log(_TEMPERATURE_K)
    euler = before - (bookable / capacity) / math.log(10.0)
    assert reservoir.melt_intrinsic_fO2_log == pytest.approx(published)
    assert reservoir.melt_intrinsic_fO2_log != pytest.approx(euler)
    assert sim._ferric_tangent_exhausted is True
    assert reservoir.melt_redox_capacity_mol_per_ln_fO2 == pytest.approx(
        OXYGEN_RESERVOIR_NOOP_MOL
    )
    after_buffer = sim.atom_ledger.mol_by_account("reservoir.fo2_buffer")["O2"]
    assert before_buffer - after_buffer == pytest.approx(reservoir.exchange_o2_mol)
    assert _oxygen_atoms(sim) == pytest.approx(before_atoms)


def test_exchange_with_no_ferric_inventory_holds_fo2_and_moves_atoms(
    sim: PyrolysisSimulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm_reducing_exchange(sim, monkeypatch, fe2o3_mol=0.0)
    before = sim._current_melt_redox_fO2_log()
    before_atoms = _oxygen_atoms(sim)
    before_buffer = sim.atom_ledger.mol_by_account("reservoir.fo2_buffer")["O2"]

    reservoir = sim._apply_oxygen_reservoir_exchange()

    assert reservoir.exchange_o2_mol > OXYGEN_RESERVOIR_NOOP_MOL
    assert reservoir.melt_intrinsic_fO2_log == pytest.approx(before)
    assert reservoir.melt_redox_capacity_mol_per_ln_fO2 == pytest.approx(
        OXYGEN_RESERVOIR_NOOP_MOL
    )
    after_buffer = sim.atom_ledger.mol_by_account("reservoir.fo2_buffer")["O2"]
    assert before_buffer - after_buffer == pytest.approx(reservoir.exchange_o2_mol)
    assert _oxygen_atoms(sim) == pytest.approx(before_atoms)


def test_buffer_debit_lowers_fo2_and_a_buffer_credit_does_not(
    sim: PyrolysisSimulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 4 mol Fe2O3 books 2 mol O2, so a 1 mol buffer debit is in inventory.
    _pin_tangent(sim, monkeypatch, fe2o3_mol=4.0, capacity_mol_per_ln=1.0)
    o2 = resolve_species_formula("O2", sim.species_formula_registry)
    one_mol_kg = o2.molar_mass_kg_per_mol()
    debit = LedgerTransition(
        name="evaporate_SiO",
        debits=(
            MaterialLot(
                "reservoir.fo2_buffer",
                {"O2": one_mol_kg},
                source="test oxidative vapour buffer debit",
            ),
        ),
        credits=(
            MaterialLot(
                "process.overhead_gas",
                {"SiO": one_mol_kg},
                source="test oxidative vapour buffer debit",
            ),
        ),
    )
    terms = sim._evaporative_redox_source_terms_from_transition(debit)
    assert terms["redox_source:evaporative_oxygen_loss"] == pytest.approx(-1.0)

    before_fe2o3 = sim.atom_ledger.mol_by_account("process.cleaned_melt")["Fe2O3"]
    reservoir = sim._apply_oxygen_reservoir_redox_source_terms(
        terms,
        temperature_K=_TEMPERATURE_K,
    )
    published = sim._ledger_consistent_melt_fO2_log(_TEMPERATURE_K)
    assert reservoir.melt_intrinsic_fO2_log == pytest.approx(published)
    assert reservoir.melt_intrinsic_fO2_log != pytest.approx(
        _BASE_LOG - 1.0 / math.log(10.0)
    )
    assert sim.atom_ledger.mol_by_account("process.cleaned_melt")["Fe2O3"] == (
        pytest.approx(before_fe2o3 - fe2o3_mol_for_ferric_o2(1.0))
    )
    assert reservoir.redox_source_terms_applied is True
    assert sim._ferric_tangent_exhausted is False

    credit = LedgerTransition(
        name="evaporate_SiO",
        debits=(
            MaterialLot(
                "process.cleaned_melt",
                {"SiO2": one_mol_kg},
                source="test buffer credit is not a debit",
            ),
        ),
        credits=(
            MaterialLot(
                "reservoir.fo2_buffer",
                {"O2": one_mol_kg},
                source="test buffer credit is not a debit",
            ),
        ),
    )
    assert sim._evaporative_redox_source_terms_from_transition(credit) == {}


def test_evaporative_buffer_posting_sum_keeps_roundoff_and_real_source(
    sim: PyrolysisSimulator,
) -> None:
    o2_molar_mass = resolve_species_formula(
        "O2",
        sim.species_formula_registry,
    ).molar_mass_kg_per_mol()

    def source_terms(overhead_credit_mol: float) -> dict[str, float]:
        transition = LedgerTransition(
            name="evaporate_SiO",
            debits=(
                MaterialLot(
                    "process.overhead_gas",
                    {"O2": 10.089568621263579 * o2_molar_mass},
                    source="test overhead oxygen debit",
                ),
                MaterialLot(
                    "reservoir.fo2_buffer",
                    {"O2": 5.0 * o2_molar_mass},
                    source="test buffer oxygen debit",
                ),
            ),
            credits=(
                MaterialLot(
                    "process.overhead_gas",
                    {"O2": overhead_credit_mol * o2_molar_mass},
                    source="test overhead oxygen credit",
                ),
            ),
        )
        return sim._evaporative_redox_source_terms_from_transition(transition)

    near_equality = source_terms(5.089568621263581)
    real_source = source_terms(5.089568622263581)

    assert near_equality == {}
    assert real_source["redox_source:evaporative_oxygen_loss"] < -9e-10


def test_cleaned_melt_reduction_sum_keeps_roundoff_and_real_source(
    sim: PyrolysisSimulator,
) -> None:
    oxygen_mass = resolve_species_formula(
        "O2",
        sim.species_formula_registry,
    ).molar_mass_kg_per_mol()

    def source_terms(credit_mol: float) -> dict[str, float]:
        transition = LedgerTransition(
            name="cleaned_melt_oxygen_exchange",
            debits=(
                MaterialLot(
                    "process.cleaned_melt",
                    {"O2": 5.089568621263581 * oxygen_mass},
                    source="test cleaned-melt oxygen debit",
                ),
            ),
            credits=(
                MaterialLot(
                    "process.cleaned_melt",
                    {"O2": credit_mol * oxygen_mass},
                    source="test cleaned-melt oxygen credit",
                ),
            ),
        )
        return sim._cleaned_melt_reduction_source_terms_from_transition(
            transition,
            label="redox_source:test_cleaned_melt",
            target_oxides=("O2",),
        )

    near_equality = source_terms(5.089568621263579)
    real_source = source_terms(5.089568622263581)

    assert near_equality == {}
    assert real_source["redox_source:test_cleaned_melt"] > 9e-10


def test_evaporative_metal_loss_sum_keeps_roundoff_and_real_source(
    sim: PyrolysisSimulator,
) -> None:
    oxide_mass = resolve_species_formula(
        "Na2O",
        sim.species_formula_registry,
    ).molar_mass_kg_per_mol()

    def source_terms(credit_o2_equiv_mol: float) -> dict[str, float]:
        transition = LedgerTransition(
            name="evaporate_Na",
            debits=(
                MaterialLot(
                    "process.cleaned_melt",
                    {"Na2O": 10.179137242527162 * oxide_mass},
                    source="test cleaned-melt sodium oxide debit",
                ),
            ),
            credits=(
                MaterialLot(
                    "process.cleaned_melt",
                    {"Na2O": 2.0 * credit_o2_equiv_mol * oxide_mass},
                    source="test cleaned-melt sodium oxide credit",
                ),
            ),
        )
        return sim._evaporative_redox_source_terms_from_transition(transition)

    near_equality = source_terms(5.089568621263579)
    real_source = source_terms(5.089568620263581)

    assert near_equality == {}
    assert real_source["redox_source:evaporative_metal_loss"] > 9e-10


def test_load_batch_clears_ferric_tangent_exhaustion(
    sim: PyrolysisSimulator,
) -> None:
    sim._ferric_tangent_exhausted = True
    sim._ferric_exhausted_bookable_o2_mol = 0.0
    sim._ferric_tangent_reasons_this_hr = ["ferric_couple_exhausted"]
    sim.load_batch("lunar_mare_low_ti", mass_kg=1.0)
    assert sim._ferric_tangent_exhausted is False
    assert sim._ferric_exhausted_bookable_o2_mol is None
    assert sim._ferric_tangent_reasons_this_hr == []


def test_ferric_reappearance_reopens_the_tangent(
    sim: PyrolysisSimulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Exhaust, prove the lock, then put Fe2O3 back on the in-step ledger.
    # The next capacity read must clear the flag and a reducing step
    # inside that inventory must move fO2 again.
    _pin_tangent(sim, monkeypatch, fe2o3_mol=0.0)
    skipped = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:test_reducing": -_STEP_MOL},
        temperature_K=_TEMPERATURE_K,
    )
    assert skipped.redox_source_skip_reason == "ferric_couple_exhausted"
    assert sim._ferric_tangent_exhausted is True
    sim._reset_redox_source_diagnostics_for_hour()
    locked = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:test_reducing": -_STEP_MOL},
        temperature_K=_TEMPERATURE_K,
    )
    assert locked.redox_source_skip_reason == "no_melt_redox_capacity"
    assert sim._ferric_tangent_exhausted is True

    sim.atom_ledger.load_external_mol(
        "process.cleaned_melt",
        {"Fe2O3": 1.0},
        source="test ferric iron reappearing after exhaustion",
        material_origin="feedstock",
    )
    reopened = sim._melt_redox_source_capacity_mol_per_ln_fO2(
        fO2_log=sim._current_melt_redox_fO2_log(),
        T_K=_TEMPERATURE_K,
    )
    assert sim._ferric_tangent_exhausted is False
    assert reopened == pytest.approx(_CAPACITY_MOL_PER_LN)

    before = sim._current_melt_redox_fO2_log()
    before_fe2o3 = sim.atom_ledger.mol_by_account("process.cleaned_melt")["Fe2O3"]
    sim._reset_redox_source_diagnostics_for_hour()
    resumed = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:test_reducing": -_STEP_MOL},
        temperature_K=_TEMPERATURE_K,
    )
    euler = before + (-_STEP_MOL / _CAPACITY_MOL_PER_LN) / math.log(10.0)
    published_resumed = sim._ledger_consistent_melt_fO2_log(_TEMPERATURE_K)
    assert resumed.redox_source_terms_applied is True
    assert resumed.redox_source_skip_reason == ""
    assert resumed.melt_intrinsic_fO2_log == pytest.approx(published_resumed)
    assert resumed.melt_intrinsic_fO2_log != pytest.approx(euler)
    assert sim.atom_ledger.mol_by_account("process.cleaned_melt")["Fe2O3"] == (
        pytest.approx(before_fe2o3 - fe2o3_mol_for_ferric_o2(_STEP_MOL))
    )
    assert sim._ferric_tangent_exhausted is False

    # A fresh lock, then an oxidizing source with no new Fe2O3.
    # The source may use C_m as a predictor. It does not clear the
    # lock, and the published fO2 stays the ledger value.
    monkeypatch.setattr(
        sim,
        "_melt_redox_capacity_mol_per_ln_fO2",
        lambda **_kwargs: 1.0,
    )
    bookable = ferric_couple_o2_mol(1.0)
    sim._reset_redox_source_diagnostics_for_hour()
    locked_again = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:test_reducing": -(bookable + 1.0)},
        temperature_K=_TEMPERATURE_K,
    )
    published = sim._ledger_consistent_melt_fO2_log(_TEMPERATURE_K)
    assert locked_again.redox_source_terms_applied is True
    assert locked_again.redox_source_skip_reason == ""
    assert locked_again.melt_intrinsic_fO2_log == pytest.approx(published)
    assert locked_again.melt_redox_capacity_mol_per_ln_fO2 == pytest.approx(
        OXYGEN_RESERVOIR_NOOP_MOL
    )
    assert locked_again.ferric_divergence["ferric_tangent_reasons"] == [
        "ferric_inventory_handover",
    ]
    assert sim._ferric_tangent_exhausted is True

    oxidized = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:test_oxidizing": _STEP_MOL},
        temperature_K=_TEMPERATURE_K,
    )
    assert oxidized.redox_source_terms_applied is True
    assert oxidized.redox_source_skip_reason == ""
    assert sim._ferric_tangent_exhausted is True
    assert oxidized.melt_intrinsic_fO2_log == pytest.approx(published)
    assert oxidized.melt_redox_capacity_mol_per_ln_fO2 == pytest.approx(
        OXYGEN_RESERVOIR_NOOP_MOL
    )
    # The exhaustion step booked the ferric. The oxidizing predictor
    # does not put it back, and it does not clear the lock.
    assert _bookable_ferric_o2(sim) < 1.0e-8

    still_locked = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:test_reducing": -_STEP_MOL},
        temperature_K=_TEMPERATURE_K,
    )
    assert still_locked.redox_source_terms_applied is False
    assert still_locked.redox_source_skip_reason == "no_melt_redox_capacity"
    assert sim._ferric_tangent_exhausted is True
    assert still_locked.melt_intrinsic_fO2_log == pytest.approx(published)


def test_exchange_credit_does_not_clear_the_ferric_lock(
    sim: PyrolysisSimulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_tangent(sim, monkeypatch, fe2o3_mol=0.0, capacity_mol_per_ln=1.0)
    skipped = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:test_reducing": -_STEP_MOL},
        temperature_K=_TEMPERATURE_K,
    )
    assert skipped.redox_source_skip_reason == "ferric_couple_exhausted"
    assert sim._ferric_tangent_exhausted is True

    head_o2_mol = 1.0
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"O2": head_o2_mol},
        source="test headspace O2 for an exchange credit after exhaustion",
        material_origin="feedstock",
    )
    drive_pO2 = sim._overhead_exchange_drive_pO2_bar()
    log_drive_pO2 = max(drive_pO2, sim._oxygen_numerical_floor_bar())
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = (
        math.log10(log_drive_pO2) - 1.0
    )
    sim.melt.oxygen_reservoir.reference_T_K = _TEMPERATURE_K
    sim._sync_oxygen_reservoir_mirror()

    reservoir = sim._apply_oxygen_reservoir_exchange()

    published = sim._ledger_consistent_melt_fO2_log(_TEMPERATURE_K)
    assert reservoir.exchange_direction == "headspace_to_melt"
    assert reservoir.exchange_o2_mol < -OXYGEN_RESERVOIR_NOOP_MOL
    assert sim._ferric_tangent_exhausted is True
    assert reservoir.melt_intrinsic_fO2_log == pytest.approx(published)
    assert reservoir.melt_redox_capacity_mol_per_ln_fO2 == pytest.approx(
        OXYGEN_RESERVOIR_NOOP_MOL
    )
    assert "ferric_couple_exhausted" in reservoir.ferric_divergence[
        "ferric_tangent_reasons"
    ]


# Robinot defect hour. 2298.92 K is 2025.77 °C. Holzheid liquid FeO at
# a_Fe = 1 and a_FeO = 1; the known IW value is the owner's result.
_DEFECT_T_K = 2298.92
_DEFECT_METAL_LOSS_MOL = 1.5e-8
_DEFECT_IW_LOG = -5.873099484220312


def test_defect_hour_metal_loss_keeps_the_ferric_lock(
    sim: PyrolysisSimulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SiO sets the lock; the same hour's metal-loss credit must not clear it.

    Premise: wall order is SiO, then the metal-loss oxygen credit, in
    one hour. SiO demand 0.001216111 mol and the uncapped tangent
    17.853327 dex give C_m = 0.001216111 / (17.853327 * ln 10)
    = 2.958e-5 mol per ln. Bookable ferric O2 is 1.790e-4 mol. The
    capped predictor is 1.790e-4 / 2.958e-5 / ln(10) ≈ 2.628 dex, from
    -1.886 to about -4.514. That landing is not the ledger.

    The metal-loss credit is +1.5e-8 mol. On that C_m it is
    1.5e-8 / 2.958e-5 / ln(10) ≈ 2.2e-4 dex. A positive source does not
    clear the lock and must not leave that bump or a finite C_m.

    Published fO2 is the Kress inverse of the ledger Fe3+/ΣFe, floored
    at Fe-metal saturation on the melt's a_FeO. IW(a_wustite=1) at
    2298.92 K is -5.873. That pure line is not the floor: the floor
    converts the melt activity once. The SiO step books the ferric, so
    the bookable O2 afterwards is dust and the frozen snapshot stays
    the pre-release 1.790e-4 mol. Langmuir opens because the source
    capacity is the noop, not because the source pO2 is the 1e-30 bar
    dissociation floor. A control at the finite C_m stays shut.
    """

    from simulator.langmuir_surface_oxygen import apply_langmuir_surface_oxygen
    from simulator.oxygen_buffers import feo_iw_log10_fO2_bar

    capacity = _M42_DEMAND_MOL / (_M42_UNCAPPED_DEX * math.log(10.0))
    assert capacity == pytest.approx(2.958e-5, rel=1.0e-4)
    iw_unit = feo_iw_log10_fO2_bar(_DEFECT_T_K, a_feo=1.0)
    assert iw_unit == pytest.approx(_DEFECT_IW_LOG, abs=1.0e-6)
    _pin_tangent(
        sim,
        monkeypatch,
        fe2o3_mol=fe2o3_mol_for_ferric_o2(_M42_BOOKABLE_MOL),
        base_log=_M42_BASE,
        capacity_mol_per_ln=capacity,
    )
    sim.melt.temperature_C = _DEFECT_T_K - 273.15
    sim.melt.p_total_mbar = 13.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = _M42_BASE
    sim.melt.oxygen_reservoir.reference_T_K = _DEFECT_T_K
    sim._sync_oxygen_reservoir_mirror()
    assert _bookable_ferric_o2(sim) == pytest.approx(_M42_BOOKABLE_MOL)

    sio = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:evaporative_oxygen_loss": -_M42_DEMAND_MOL},
        temperature_K=_DEFECT_T_K,
    )
    assert sim._ferric_tangent_exhausted is True
    published = sim._ledger_consistent_melt_fO2_log(_DEFECT_T_K)
    assert sio.melt_intrinsic_fO2_log == pytest.approx(published)
    assert sio.melt_intrinsic_fO2_log != pytest.approx(_M42_BASE)
    assert _bookable_ferric_o2(sim) < 1.0e-9
    capped_euler = _M42_BASE - (
        _M42_BOOKABLE_MOL / capacity / math.log(10.0)
    )
    assert sio.melt_intrinsic_fO2_log != pytest.approx(-4.5138, abs=1.0e-3)
    assert sio.melt_intrinsic_fO2_log != pytest.approx(capped_euler)
    assert sio.melt_redox_capacity_mol_per_ln_fO2 == pytest.approx(
        OXYGEN_RESERVOIR_NOOP_MOL
    )

    metal = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:evaporative_metal_loss": _DEFECT_METAL_LOSS_MOL},
        temperature_K=_DEFECT_T_K,
    )
    assert sim._ferric_tangent_exhausted is True
    assert sim._ferric_exhausted_bookable_o2_mol == pytest.approx(
        _M42_BOOKABLE_MOL
    )
    assert _bookable_ferric_o2(sim) < 1.0e-9
    assert metal.melt_intrinsic_fO2_log == pytest.approx(published)
    assert metal.melt_intrinsic_fO2_log != pytest.approx(_M42_BASE)
    assert metal.melt_intrinsic_fO2_log != pytest.approx(-4.51, abs=0.02)
    assert metal.melt_redox_capacity_mol_per_ln_fO2 == pytest.approx(
        OXYGEN_RESERVOIR_NOOP_MOL
    )

    source_bar = 10.0 ** metal.melt_intrinsic_fO2_log
    opened = apply_langmuir_surface_oxygen(
        {"Fe": 1.0},
        temperature_K=_DEFECT_T_K,
        provenance_by_species={
            "Fe": {
                "pressure_rail": "condensed_raoult_psat",
                "pO2_bar": source_bar,
            },
        },
        source_reaction_fO2_bar=source_bar,
        melt_redox_capacity_mol_per_ln_fO2=(
            metal.melt_redox_capacity_mol_per_ln_fO2
        ),
    )
    assert opened.applied is True
    closed = apply_langmuir_surface_oxygen(
        {"Fe": 1.0},
        temperature_K=_DEFECT_T_K,
        provenance_by_species={
            "Fe": {
                "pressure_rail": "condensed_raoult_psat",
                "pO2_bar": source_bar,
            },
        },
        source_reaction_fO2_bar=source_bar,
        melt_redox_capacity_mol_per_ln_fO2=capacity,
    )
    assert closed.applied is False


def test_bubbler_o2_credit_clears_the_lock_through_the_ledger(
    sim: PyrolysisSimulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An O2 lance on a locked melt credits Fe2O3, then the predicate clears.

    Premise: the lock was set by a zero-bookable reducing skip, so the
    frozen snapshot is 0 mol O2. The lance does not honor the lock.
    The split books 2 FeO + 1/2 O2 → Fe2O3. Bookable O2 afterwards is
    1/2 mol per mol Fe2O3 credited. The clear is
    bookable_after > frozen + 1e-15 mol, which is
    ``_ferric_inventory_reappeared``. The positive source by itself
    clears nothing.

    Algebra: the target is 2 dex above the base and C_m is 1e-3 mol
    per ln, so the dose is C_m * 2 * ln(10) mol O2 at eta = 1. At
    1600 °C the Fe split is inside the Kress authoritative band.

    Units: the rate is kg/h, divided by the O2 molar mass to mol/h.
    Fe2O3 and bookable O2 are mol. Known value: the frozen bookable
    is 0, so a Fe2O3 credit above 2e-15 mol clears.
    """

    _pin_tangent(sim, monkeypatch, fe2o3_mol=0.0, base_log=-4.0)
    skipped = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:evaporative_oxygen_loss": -_STEP_MOL},
        temperature_K=_TEMPERATURE_K,
    )
    assert skipped.redox_source_skip_reason == "ferric_couple_exhausted"
    assert sim._ferric_tangent_exhausted is True
    assert sim._ferric_exhausted_bookable_o2_mol == pytest.approx(0.0)
    frozen = float(sim._ferric_exhausted_bookable_o2_mol)
    before_fe2o3 = sim.atom_ledger.mol_by_account("process.cleaned_melt").get(
        "Fe2O3",
        0.0,
    )
    sim._reset_redox_source_diagnostics_for_hour()
    monkeypatch.setattr(
        sim,
        "_melt_redox_temperature_shift_is_liquid",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        sim.campaign_mgr,
        "o2_bubbler_controls",
        lambda _campaign: {
            "o2_bubbler_kg_per_hr": 0.01,
            "o2_bubbler_eta_absorb_default": 1.0,
            "o2_bubbler_target_fO2_log": -2.0,
        },
    )

    diagnostic = sim._apply_o2_bubbler()

    assert diagnostic["reason"] == "applied"
    assert float(diagnostic.get("fe_redox_absorbed_mol") or 0.0) > (
        OXYGEN_RESERVOIR_NOOP_MOL
    )
    after_fe2o3 = sim.atom_ledger.mol_by_account("process.cleaned_melt").get(
        "Fe2O3",
        0.0,
    )
    assert after_fe2o3 > before_fe2o3
    assert _bookable_ferric_o2(sim) > frozen + OXYGEN_RESERVOIR_NOOP_MOL
    assert sim._ferric_tangent_exhausted is False
    assert sim._ferric_inventory_reappeared() is False


def test_stoichiometric_activity_floor_matches_wustite_basis_at_1400c() -> None:
    """At 1400 °C, a stoichiometric activity uses the 1.94 multiplier.

    Premise: Holzheid's gamma_stoich/gamma_wustite is 1.94 at 1400 °C.
    ``feo_iw_log10_fO2_bar`` is the wustite-liquid standard, so the
    activity it receives is a_stoich / 1.94.

    Algebra: floor(a) = IW(a_FeO=1) + 2 log10(a/1.94)
    = feo_iw(T, a_feo=a/1.94).

    Units: both sides are log10(fO2/bar), compared in dex.

    Known value: the two calls agree within 1e-9 dex. Plugging a in
    unconverted is too oxidizing by 2 log10(1.94), about 0.58 dex.
    """

    from simulator.fe_redox import fe_metal_saturation_log10_fO2_bar
    from simulator.oxygen_buffers import feo_iw_log10_fO2_bar

    temperature_K = 1400.0 + 273.15
    activity = 0.42
    got = fe_metal_saturation_log10_fO2_bar(
        temperature_K,
        a_feo_stoich=activity,
    )
    expected = feo_iw_log10_fO2_bar(temperature_K, a_feo=activity / 1.94)
    assert got == pytest.approx(expected, abs=1.0e-9)
    pure = fe_metal_saturation_log10_fO2_bar(
        temperature_K,
        a_feo_stoich=1.0,
    )
    assert pure == pytest.approx(
        feo_iw_log10_fO2_bar(temperature_K, a_feo=1.0 / 1.94),
        abs=1.0e-9,
    )


def test_reducing_term_at_the_ceiling_lowers_fo2_and_ferric(
    sim: PyrolysisSimulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A saturated derivative must not refuse a reducing term.

    Premise: at the effervescence bound the Kress derivative can sit
    at the noop while ledger Fe2O3 is still positive. The old capacity
    test skipped both signs. The reducing term is booked against that
    ferric (1/2 mol O2 per mol Fe2O3) and fO2 is republished from the
    ledger, which is below log10(P_total).

    Algebra: 200 mol Fe2O3 books 100 mol O2. The demand is one mol
    past that, so the step exhausts the inventory and the target is
    the wustite floor. C_m is pinned at 0 to force the saturated
    branch rather than the finite tangent.

    Units: Fe2O3 and the demand are mol. fO2 is log10(bar). At 10 mbar
    the bound is log10(0.01) = -2. 1600 °C is inside the Kress band.
    """

    ceiling = -2.0
    fe2o3_mol = 200.0
    _pin_tangent(
        sim,
        monkeypatch,
        fe2o3_mol=fe2o3_mol,
        base_log=ceiling,
        capacity_mol_per_ln=0.0,
    )
    sim.melt.p_total_mbar = 10.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = ceiling
    sim.melt.oxygen_reservoir.reference_T_K = _TEMPERATURE_K
    sim._sync_oxygen_reservoir_mirror()
    monkeypatch.setattr(
        sim,
        "_melt_redox_temperature_shift_is_liquid",
        lambda *_args, **_kwargs: True,
    )
    assert sim._melt_redox_fO2_upper_bound_log() == pytest.approx(ceiling)
    before_fe2o3 = sim.atom_ledger.mol_by_account("process.cleaned_melt")["Fe2O3"]
    assert before_fe2o3 == pytest.approx(fe2o3_mol)
    bookable = _bookable_ferric_o2(sim)
    assert bookable > OXYGEN_RESERVOIR_NOOP_MOL

    reservoir = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:evaporative_oxygen_loss": -(bookable + 1.0)},
        temperature_K=_TEMPERATURE_K,
    )

    after_fe2o3 = sim.atom_ledger.mol_by_account("process.cleaned_melt")["Fe2O3"]
    assert reservoir.redox_source_skip_reason == ""
    assert reservoir.redox_source_terms_applied is True
    assert reservoir.melt_intrinsic_fO2_log < ceiling - 1.0e-6
    assert reservoir.melt_intrinsic_fO2_log == pytest.approx(
        sim._ledger_consistent_melt_fO2_log(_TEMPERATURE_K)
    )
    assert after_fe2o3 < 1.0e-6
    assert after_fe2o3 < before_fe2o3
    assert sim._ferric_tangent_exhausted is True


def test_oxidizing_term_at_the_ceiling_books_excess_o2(
    sim: PyrolysisSimulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An oxidizing term at the bound does not raise fO2; excess leaves.

    Premise: log10(P_total) is the effervescence bound, not a pure-O2
    cover. The source-term step is not integrated past it, including
    when C_m is the noop (the sign-blind skip). It does not empty the
    buffer: the Fe split still has to run. The batch settle books the
    O2 that split did not take.

    Algebra: 2 FeO + 1/2 O2 → Fe2O3, so the couple can take at most
    n_FeO/4 mol O2. A buffer above that has a remainder. The settle
    moves that remainder to overhead. fO2 stays at log10(0.01) = -2.

    Units: mol O2 and log10(bar). The bound is not a measured target.
    """

    ceiling = -2.0
    _pin_tangent(
        sim,
        monkeypatch,
        fe2o3_mol=1.0,
        base_log=ceiling,
        capacity_mol_per_ln=0.0,
    )
    sim.melt.p_total_mbar = 10.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = ceiling
    sim.melt.oxygen_reservoir.reference_T_K = _TEMPERATURE_K
    sim._sync_oxygen_reservoir_mirror()
    monkeypatch.setattr(
        sim,
        "_melt_redox_temperature_shift_is_liquid",
        lambda *_args, **_kwargs: True,
    )
    assert sim._melt_redox_fO2_upper_bound_log() == pytest.approx(ceiling)
    feo_mol = sim.atom_ledger.mol_by_account("process.cleaned_melt").get("FeO", 0.0)
    # n_FeO/4 mol O2 turns every FeO into Fe2O3. Anything above that
    # cannot enter the couple at any fO2.
    max_couple_o2_mol = feo_mol / 4.0
    buffer_mol = max_couple_o2_mol + 0.05
    sim.atom_ledger.load_external_mol(
        "reservoir.fo2_buffer",
        {"O2": buffer_mol},
        source="test oxidizing oxygen already credited to the fO2 buffer",
        material_origin="feedstock",
    )
    sim._fe_redox_internal_o2_capacity_mol_this_hr = buffer_mol
    before_overhead = sim.atom_ledger.mol_by_account("process.overhead_gas").get(
        "O2",
        0.0,
    )
    before_buffer = sim.atom_ledger.mol_by_account("reservoir.fo2_buffer").get(
        "O2",
        0.0,
    )
    before_fe2o3 = sim.atom_ledger.mol_by_account("process.cleaned_melt").get(
        "Fe2O3",
        0.0,
    )
    before_atoms = _oxygen_atoms(sim)

    reservoir = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:evaporative_metal_loss": buffer_mol},
        temperature_K=_TEMPERATURE_K,
    )

    assert reservoir.melt_intrinsic_fO2_log == pytest.approx(ceiling)
    assert reservoir.redox_source_skip_reason == ""
    assert reservoir.redox_source_terms_applied is True
    assert sim.atom_ledger.mol_by_account("reservoir.fo2_buffer").get(
        "O2",
        0.0,
    ) == pytest.approx(before_buffer)
    assert sim.atom_ledger.mol_by_account("process.cleaned_melt").get(
        "Fe2O3",
        0.0,
    ) == pytest.approx(before_fe2o3)

    sim._settle_evaporative_redox_after_batch()

    after_fe2o3 = sim.atom_ledger.mol_by_account("process.cleaned_melt").get(
        "Fe2O3",
        0.0,
    )
    after_buffer = sim.atom_ledger.mol_by_account("reservoir.fo2_buffer").get(
        "O2",
        0.0,
    )
    after_overhead = sim.atom_ledger.mol_by_account("process.overhead_gas").get(
        "O2",
        0.0,
    )
    into_ferric_o2 = ferric_couple_o2_mol(after_fe2o3 - before_fe2o3)
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log == pytest.approx(
        ceiling
    )
    assert after_buffer < before_buffer
    assert after_overhead > before_overhead
    assert after_overhead - before_overhead == pytest.approx(
        (before_buffer - after_buffer) - into_ferric_o2
    )
    assert after_overhead - before_overhead > 0.04
    assert _oxygen_atoms(sim) == pytest.approx(before_atoms)


# The confirm review's lunar mix at the diagnosed hour. Sum 99.1 wt%.
_REVIEW_LUNAR_WT_PCT = {
    "SiO2": 45.5,
    "Al2O3": 14.0,
    "FeO": 16.0,
    "MgO": 10.0,
    "CaO": 11.0,
    "TiO2": 2.0,
    "Na2O": 0.5,
    "K2O": 0.1,
}


def test_defect_hour_equilibrium_q_books_ferric_and_minute_43_reads_the_floor(
    sim: PyrolysisSimulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Equilibrium q at the ceiling is booked; minute 43 reads the floor.

    Premise: at 13 mbar the effervescence bound is log10(0.013). The
    review mix has equilibrium Fe3+/ΣFe of 0.1542 there, and C_m is
    2.958e-5 mol per ln. Leaving that q on the ledger keeps the
    inverse on the ceiling.

    Algebra: the installed ledger q is the production forward of this
    sim's own wt% fractions at the ceiling, scaled so the bookable O2
    is 1.790e-4 mol. The SiO demand exceeds it. The split removes that
    ferric. q goes to 0, so the published fO2 is the melt-activity
    floor, not -1.886. The same hour's metal-loss credit does not
    put the ceiling back.

    Units: Fe2O3 and bookable O2 are mol. fO2 is log10(bar).
    """

    from simulator.fe_redox import (
        floor_vacuum_pressure_bar,
        kress91_fe3_over_sigma_fe,
        kress91_log10_fO2_from_fe3_over_sigma_fe,
        melt_mol_fractions_for_kress91,
    )

    capacity = _M42_DEMAND_MOL / (_M42_UNCAPPED_DEX * math.log(10.0))
    assert capacity == pytest.approx(2.958e-5, rel=1.0e-4)
    _pin_tangent(
        sim,
        monkeypatch,
        fe2o3_mol=0.0,
        base_log=_M42_BASE,
        capacity_mol_per_ln=capacity,
    )
    sim.melt.temperature_C = _DEFECT_T_K - 273.15
    sim.melt.p_total_mbar = 13.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = _M42_BASE
    sim.melt.oxygen_reservoir.reference_T_K = _DEFECT_T_K
    sim._sync_oxygen_reservoir_mirror()
    ceiling = sim._melt_redox_fO2_upper_bound_log()
    assert ceiling == pytest.approx(_M42_BASE, abs=1.0e-3)
    pressure_bar = floor_vacuum_pressure_bar(
        float(sim.melt.p_total_mbar) / 1000.0,
        floor_bar=sim._vacuum_floor_bar(),
    )
    review_fractions = melt_mol_fractions_for_kress91(_REVIEW_LUNAR_WT_PCT)
    review_q = kress91_fe3_over_sigma_fe(
        fO2_log=ceiling,
        mol_fractions=review_fractions,
        T_K=_DEFECT_T_K,
        pressure_bar=pressure_bar,
    )
    assert review_q == pytest.approx(0.1542, abs=5.0e-4)

    fractions = melt_mol_fractions_for_kress91(sim._melt_oxide_wt_pct())
    q_eq = kress91_fe3_over_sigma_fe(
        fO2_log=ceiling,
        mol_fractions=fractions,
        T_K=_DEFECT_T_K,
        pressure_bar=pressure_bar,
    )
    assert 0.0 < q_eq < 1.0
    inverse = kress91_log10_fO2_from_fe3_over_sigma_fe(
        fe3_over_sigma_fe=q_eq,
        mol_fractions=fractions,
        T_K=_DEFECT_T_K,
        pressure_bar=pressure_bar,
    )
    assert inverse == pytest.approx(ceiling, abs=1.0e-6)
    fe2o3_mol = fe2o3_mol_for_ferric_o2(_M42_BOOKABLE_MOL)
    total_fe_mol = (2.0 * fe2o3_mol) / q_eq
    balances = sim.atom_ledger._balances["process.cleaned_melt"]
    balances["FeO"] = total_fe_mol - 2.0 * fe2o3_mol
    balances["Fe2O3"] = fe2o3_mol
    assert _bookable_ferric_o2(sim) == pytest.approx(_M42_BOOKABLE_MOL)
    before_atoms = _oxygen_atoms(sim)

    sio = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:evaporative_oxygen_loss": -_M42_DEMAND_MOL},
        temperature_K=_DEFECT_T_K,
    )

    published = sim._ledger_consistent_melt_fO2_log(_DEFECT_T_K)
    assert sio.melt_intrinsic_fO2_log == pytest.approx(published)
    assert sio.melt_intrinsic_fO2_log != pytest.approx(_M42_BASE)
    assert published < ceiling - 1.0
    after_fe2o3 = sim.atom_ledger.mol_by_account("process.cleaned_melt").get(
        "Fe2O3",
        0.0,
    )
    assert fe2o3_mol - after_fe2o3 == pytest.approx(
        fe2o3_mol_for_ferric_o2(_M42_BOOKABLE_MOL),
        abs=1.0e-9,
    )
    assert _bookable_ferric_o2(sim) < 1.0e-9
    assert _oxygen_atoms(sim) == pytest.approx(before_atoms, abs=1.0e-9)

    metal = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:evaporative_metal_loss": _DEFECT_METAL_LOSS_MOL},
        temperature_K=_DEFECT_T_K,
    )
    minute_43 = sim._ledger_consistent_melt_fO2_log(_DEFECT_T_K)
    assert metal.melt_intrinsic_fO2_log == pytest.approx(minute_43)
    assert metal.melt_intrinsic_fO2_log != pytest.approx(_M42_BASE)
    assert sim._ferric_tangent_exhausted is True


def test_dust_oxidizing_settle_publishes_ledger_not_the_ceiling(
    sim: PyrolysisSimulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A committed partial split publishes the ledger, not the override.

    Premise: exhaust the ferric inventory so the published fO2 is the
    floor and the source capacity is the noop. The metal-loss counter
    of +1.5e-8 mol then makes the batch settle run the Fe split aimed
    at the effervescence ceiling. That dust of O2 cannot reach the
    ceiling q, so the split commits a partial oxidizing step. The
    override is the ceiling. The post-split q is dust on the lunar Fe
    inventory, and its inverse lies under the floor.

    Writing the override would jump to the ceiling. This is the test
    that mutation kills.

    Units: the counter is mol O2. fO2 is log10(bar).
    """

    _pin_tangent(sim, monkeypatch, fe2o3_mol=1.0, base_log=-4.0)
    bookable = _bookable_ferric_o2(sim)
    exhausted = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:evaporative_oxygen_loss": -(bookable + 1.0)},
        temperature_K=_TEMPERATURE_K,
    )
    ceiling = sim._melt_redox_fO2_upper_bound_log()
    floor = sim._ledger_consistent_melt_fO2_log(_TEMPERATURE_K)
    assert exhausted.melt_intrinsic_fO2_log == pytest.approx(floor)
    assert floor < ceiling - 1.0
    assert sim._ferric_tangent_exhausted is True
    sim._fe_redox_internal_o2_capacity_mol_this_hr = _DEFECT_METAL_LOSS_MOL
    sim._fe_redox_internal_o2_consumed_mol_this_hr = 0.0
    sim.atom_ledger.load_external_mol(
        "reservoir.fo2_buffer",
        {"O2": _DEFECT_METAL_LOSS_MOL},
        source="test dust metal-loss oxygen already on the buffer",
        material_origin="feedstock",
    )

    sim._settle_evaporative_redox_after_batch()

    published = sim._ledger_consistent_melt_fO2_log(_TEMPERATURE_K)
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log == pytest.approx(
        published
    )
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log < ceiling - 1.0
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log != pytest.approx(
        ceiling
    )


def test_floor_and_native_onset_share_melt_activity(
    sim: PyrolysisSimulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At the floor, native Fe appears on the next reducing step.

    Premise: the floor is Fe-metal saturation at the melt's a_FeO,
    converted once onto the wustite basis. Native onset calls
    ``fe_metal_saturation_log10_fO2_bar`` with that same activity.
    At 1400 °C the pure stoichiometric line is about 0.69 dex too
    oxidizing for a lunar melt.

    Algebra: exhaust the ferric inventory so the published fO2 is the
    floor. Read a_FeO from ``kress91_ferrous_feo_activity`` at that
    fO2 and the publisher's pressure. The floor equals
    ``fe_metal_saturation(T, a)`` and lies below the pure line.
    ``native_fe_frac`` is 0 one millidex above the floor and positive
    1e-4 dex below it.

    Units: fO2 is log10(bar).
    """

    from simulator.fe_redox import (
        fe_metal_saturation_log10_fO2_bar,
        floor_vacuum_pressure_bar,
        kress91_ferrous_feo_activity,
    )

    temperature_C = 1400.0
    temperature_K = temperature_C + 273.15
    _pin_tangent(sim, monkeypatch, fe2o3_mol=1.0, base_log=-4.0)
    sim.melt.temperature_C = temperature_C
    sim.melt.oxygen_reservoir.reference_T_K = temperature_K
    sim._sync_oxygen_reservoir_mirror()
    bookable = _bookable_ferric_o2(sim)
    exhausted = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:evaporative_oxygen_loss": -(bookable + 1.0)},
        temperature_K=temperature_K,
    )
    published = sim._ledger_consistent_melt_fO2_log(temperature_K)
    assert exhausted.melt_intrinsic_fO2_log == pytest.approx(published)
    pressure_bar = floor_vacuum_pressure_bar(
        float(sim.melt.p_total_mbar) / 1000.0,
        floor_bar=sim._vacuum_floor_bar(),
    )
    activity = kress91_ferrous_feo_activity(
        comp_wt=sim._melt_oxide_wt_pct(),
        fO2_log=published,
        T_K=temperature_K,
        pressure_bar=pressure_bar,
    )
    assert 0.0 < activity < 0.9
    assert published == pytest.approx(
        fe_metal_saturation_log10_fO2_bar(
            temperature_K,
            a_feo_stoich=activity,
        ),
        abs=1.0e-9,
    )
    pure = fe_metal_saturation_log10_fO2_bar(
        temperature_K,
        a_feo_stoich=1.0,
    )
    assert published < pure - 0.2
    below = sim._native_fe_saturation_state(
        sim._melt_oxide_wt_pct(),
        fe3_over_sigma_fe=0.0,
        T_K=temperature_K,
        pressure_bar=pressure_bar,
        fO2_log=published - 1.0e-4,
    )
    above = sim._native_fe_saturation_state(
        sim._melt_oxide_wt_pct(),
        fe3_over_sigma_fe=0.0,
        T_K=temperature_K,
        pressure_bar=pressure_bar,
        fO2_log=published + 1.0e-3,
    )
    assert below["native_fe_frac"] > 0.0
    assert above["native_fe_frac"] == 0.0
