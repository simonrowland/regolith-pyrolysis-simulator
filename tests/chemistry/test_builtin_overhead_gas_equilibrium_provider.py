"""Tests for the builtin OVERHEAD_GAS_EQUILIBRIUM provider."""

from __future__ import annotations

import pytest

from engines.builtin.overhead_gas_equilibrium import (
    BuiltinOverheadGasEquilibriumProvider,
)
from simulator.chemistry.kernel import ChemistryIntent
from simulator.state import GAS_CONSTANT
from tests.chemistry.conftest import _build_sim


def test_provider_declares_diagnostic_overhead_equilibrium_intent():
    provider = BuiltinOverheadGasEquilibriumProvider()
    profile = provider.capability_profile()

    assert profile.intents == frozenset(
        {ChemistryIntent.OVERHEAD_GAS_EQUILIBRIUM}
    )
    assert profile.is_authoritative_for == frozenset(
        {ChemistryIntent.OVERHEAD_GAS_EQUILIBRIUM}
    )
    assert profile.declared_accounts == frozenset({"process.overhead_gas"})


def test_dispatch_reports_ideal_gas_partial_pressures(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"O2": 2.0, "SiO": 1.0},
        source="test overhead gas",
    )

    result = sim._chem_kernel.dispatch(
        ChemistryIntent.OVERHEAD_GAS_EQUILIBRIUM,
        temperature_C=1500.0,
        pressure_bar=1.0,
        control_inputs={
            "headspace_volume_m3": 0.085,
            "headspace_temperature_K": 1873.15,
        },
    )

    scale = GAS_CONSTANT * 1873.15 / (0.085 * 1.0e5)
    diagnostic = dict(result.diagnostic or {})
    partials = dict(diagnostic["partial_pressures_bar"])
    assert result.status == "ok"
    assert result.transition is None
    assert partials["O2"] == pytest.approx(2.0 * scale)
    assert partials["SiO"] == pytest.approx(1.0 * scale)
    assert diagnostic["p_O2_bar"] == pytest.approx(2.0 * scale)
    assert diagnostic["p_total_bar"] == pytest.approx(3.0 * scale)
