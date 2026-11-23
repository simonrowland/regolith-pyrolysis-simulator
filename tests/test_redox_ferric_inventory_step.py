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

from engines.builtin.fe_redox_respeciation import ferric_couple_o2_mol
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
) -> None:
    simulator.melt.temperature_C = _TEMPERATURE_C
    simulator.melt.p_total_mbar = 10.0
    simulator.melt.oxygen_reservoir.melt_intrinsic_fO2_log = _BASE_LOG
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
        lambda **_kwargs: _CAPACITY_MOL_PER_LN,
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
