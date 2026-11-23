"""Pins for the melt fO2 Euler step shared by the two oxygen-reservoir sites.

The reducing arm of ``_apply_oxygen_reservoir_redox_source_terms`` and the
ledger update in ``_apply_oxygen_reservoir_exchange`` both advance

    Δln fO2 = n_O2 / C_m

with C_m in mol per ln fO2. These tests pin that arithmetic for a step the
ferric couple can actually book (Kress & Carmichael 1991:
Fe2O3 → 2 FeO + 1/2 O2, so the bookable O2 is 1/2 mol per mol Fe2O3).
A later inventory stop must leave an in-inventory step on this same line.
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


def test_in_inventory_reducing_source_step_is_demand_over_capacity(
    sim: PyrolysisSimulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1 mol Fe2O3 books 0.5 mol O2. The demand is 1e-4 mol, so the tangent
    # is inside the inventory and the step is the full division.
    _pin_tangent(sim, monkeypatch, fe2o3_mol=1.0)
    assert _bookable_ferric_o2(sim) > _STEP_MOL
    before_atoms = _oxygen_atoms(sim)
    before_fe2o3 = sim.atom_ledger.mol_by_account("process.cleaned_melt")["Fe2O3"]

    reservoir = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:test_reducing": -_STEP_MOL},
        temperature_K=_TEMPERATURE_K,
    )

    expected = _BASE_LOG + (-_STEP_MOL / _CAPACITY_MOL_PER_LN) / math.log(10.0)
    assert reservoir.melt_intrinsic_fO2_log == pytest.approx(expected)
    assert reservoir.melt_redox_capacity_mol_per_ln_fO2 == pytest.approx(
        _CAPACITY_MOL_PER_LN
    )
    assert reservoir.redox_source_delta_ln_fO2 == pytest.approx(
        -_STEP_MOL / _CAPACITY_MOL_PER_LN
    )
    assert reservoir.redox_source_terms_applied is True
    assert reservoir.redox_source_skip_reason == ""
    assert sim.atom_ledger.mol_by_account("process.cleaned_melt")["Fe2O3"] == (
        pytest.approx(before_fe2o3)
    )
    assert _oxygen_atoms(sim) == pytest.approx(before_atoms, abs=0.0, rel=0.0)


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
    ledger_pO2 = sim._headspace_ledger_pO2_bar_from_o2_mol(head_o2_mol)
    transport_pO2 = sim._headspace_transport_pO2_bar_from_ledger(
        ledger_pO2,
        head_o2_mol=head_o2_mol,
    )
    effective_pO2 = min(
        max(transport_pO2, sim._oxygen_numerical_floor_bar()),
        10.0 ** sim._melt_redox_fO2_upper_bound_log(),
    )
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = (
        math.log10(effective_pO2) + 0.25
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

    limited_dex = _M42_BOOKABLE_MOL / capacity / math.log(10.0)
    expected = _M42_BASE - limited_dex
    uncapped = _M42_BASE - _M42_UNCAPPED_DEX
    assert uncapped == pytest.approx(-19.739327, abs=1.0e-4)
    assert reservoir.melt_intrinsic_fO2_log == pytest.approx(expected)
    assert reservoir.melt_intrinsic_fO2_log != pytest.approx(uncapped)
    assert reservoir.melt_intrinsic_fO2_log > -10.0
    assert reservoir.redox_source_terms_applied is True
    assert reservoir.redox_source_skip_reason == ""
    assert reservoir.ferric_divergence["ferric_tangent_reasons"] == [
        "ferric_inventory_handover",
    ]
    assert reservoir.melt_redox_capacity_mol_per_ln_fO2 == pytest.approx(
        OXYGEN_RESERVOIR_NOOP_MOL
    )
    assert sim._ferric_tangent_exhausted is True
    assert sim.atom_ledger.mol_by_account("process.cleaned_melt")["Fe2O3"] == (
        pytest.approx(before_fe2o3)
    )
    assert _oxygen_atoms(sim) == pytest.approx(before_atoms, abs=0.0, rel=0.0)

    sim._reset_redox_source_diagnostics_for_hour()
    assert sim._ferric_tangent_exhausted is True
    second = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:evaporative_oxygen_loss": -_M42_DEMAND_MOL},
        temperature_K=_TEMPERATURE_K,
    )
    assert second.melt_intrinsic_fO2_log == pytest.approx(expected)
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
    ledger_pO2 = simulator._headspace_ledger_pO2_bar_from_o2_mol(head_o2_mol)
    transport_pO2 = simulator._headspace_transport_pO2_bar_from_ledger(
        ledger_pO2,
        head_o2_mol=head_o2_mol,
    )
    effective_pO2 = min(
        max(transport_pO2, simulator._oxygen_numerical_floor_bar()),
        10.0 ** simulator._melt_redox_fO2_upper_bound_log(),
    )
    simulator.melt.oxygen_reservoir.melt_intrinsic_fO2_log = (
        math.log10(effective_pO2) + 1.0
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
    expected = before - (bookable / capacity) / math.log(10.0)
    assert reservoir.melt_intrinsic_fO2_log == pytest.approx(expected)
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

    reservoir = sim._apply_oxygen_reservoir_redox_source_terms(
        terms,
        temperature_K=_TEMPERATURE_K,
    )
    assert reservoir.redox_source_delta_ln_fO2 == pytest.approx(-1.0)
    assert reservoir.melt_intrinsic_fO2_log == pytest.approx(
        _BASE_LOG - 1.0 / math.log(10.0)
    )

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
    sim._reset_redox_source_diagnostics_for_hour()
    resumed = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:test_reducing": -_STEP_MOL},
        temperature_K=_TEMPERATURE_K,
    )
    expected = before + (-_STEP_MOL / _CAPACITY_MOL_PER_LN) / math.log(10.0)
    assert resumed.redox_source_terms_applied is True
    assert resumed.redox_source_skip_reason == ""
    assert resumed.melt_intrinsic_fO2_log == pytest.approx(expected)
    assert sim._ferric_tangent_exhausted is False

    # A fresh lock, then an oxidizing step with no new Fe2O3.
    # Capacity is 1 mol per ln so booking the 0.5 mol inventory is
    # 0.5/ln(10) dex. The 1e-3 mol/ln pin would publish ~217 dex.
    monkeypatch.setattr(
        sim,
        "_melt_redox_capacity_mol_per_ln_fO2",
        lambda **_kwargs: 1.0,
    )
    bookable = ferric_couple_o2_mol(1.0)
    before_lock = sim._current_melt_redox_fO2_log()
    sim._reset_redox_source_diagnostics_for_hour()
    locked_again = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:test_reducing": -(bookable + 1.0)},
        temperature_K=_TEMPERATURE_K,
    )
    stopped = before_lock - (bookable / 1.0) / math.log(10.0)
    assert locked_again.redox_source_terms_applied is True
    assert locked_again.redox_source_skip_reason == ""
    assert locked_again.melt_intrinsic_fO2_log == pytest.approx(stopped)
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
    assert sim._ferric_tangent_exhausted is False
    oxidized_log = float(oxidized.melt_intrinsic_fO2_log)
    assert oxidized_log == pytest.approx(
        stopped + (_STEP_MOL / 1.0) / math.log(10.0)
    )

    resumed_after = sim._apply_oxygen_reservoir_redox_source_terms(
        {"redox_source:test_reducing": -_STEP_MOL},
        temperature_K=_TEMPERATURE_K,
    )
    # The apply returns the same reservoir object, so the comparison
    # has to use the float captured above.
    assert resumed_after.redox_source_terms_applied is True
    assert resumed_after.redox_source_skip_reason == ""
    assert sim._ferric_tangent_exhausted is False
    assert resumed_after.melt_intrinsic_fO2_log == pytest.approx(
        oxidized_log + (-_STEP_MOL / 1.0) / math.log(10.0)
    )


def test_exchange_credit_reopens_the_tangent(
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
    ledger_pO2 = sim._headspace_ledger_pO2_bar_from_o2_mol(head_o2_mol)
    transport_pO2 = sim._headspace_transport_pO2_bar_from_ledger(
        ledger_pO2,
        head_o2_mol=head_o2_mol,
    )
    effective_pO2 = min(
        max(transport_pO2, sim._oxygen_numerical_floor_bar()),
        10.0 ** sim._melt_redox_fO2_upper_bound_log(),
    )
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = (
        math.log10(effective_pO2) - 1.0
    )
    sim.melt.oxygen_reservoir.reference_T_K = _TEMPERATURE_K
    sim._sync_oxygen_reservoir_mirror()
    before = sim._current_melt_redox_fO2_log()

    reservoir = sim._apply_oxygen_reservoir_exchange()

    assert reservoir.exchange_direction == "headspace_to_melt"
    assert reservoir.exchange_o2_mol < -OXYGEN_RESERVOIR_NOOP_MOL
    assert reservoir.melt_intrinsic_fO2_log > before
    assert sim._ferric_tangent_exhausted is False
    assert "ferric_couple_exhausted" in reservoir.ferric_divergence[
        "ferric_tangent_reasons"
    ]
