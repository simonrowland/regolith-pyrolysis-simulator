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
        material_origin="feedstock",
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
    assert diagnostic["headspace_temperature_K"].hex() == "0x1.d44999999999ap+10"


def test_dispatch_defaults_headspace_temperature_to_request_temperature(
    vapor_pressure_data, feedstocks_data, setpoints_data
) -> None:
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    result = sim._chem_kernel.dispatch(
        ChemistryIntent.OVERHEAD_GAS_EQUILIBRIUM,
        temperature_C=1500.0,
        pressure_bar=1.0,
        control_inputs={"headspace_volume_m3": 0.085},
    )

    assert result.diagnostic["headspace_temperature_K"].hex() == (
        "0x1.bb4999999999ap+10"
    )


def test_eos_partial_pressures_are_bit_pinned() -> None:
    partials = BuiltinOverheadGasEquilibriumProvider.compute_partial_pressures_bar(
        {"O2": 2.0, "SiO": 1.0}, 0.085, 1873.15
    )

    assert {species: pressure.hex() for species, pressure in partials.items()} == {
        "O2": "0x1.d50f26be096a2p+1",
        "SiO": "0x1.d50f26be096a2p+0",
    }


@pytest.mark.parametrize(
    ("volume_m3", "temperature_K"),
    [(0.0, 1873.15), (0.085, 0.0), (0.0, 0.0)],
)
def test_eos_zero_volume_or_temperature_shortcuts_to_empty(
    volume_m3: float, temperature_K: float
) -> None:
    assert BuiltinOverheadGasEquilibriumProvider.compute_partial_pressures_bar(
        {"O2": 2.0}, volume_m3, temperature_K
    ) == {}


@pytest.mark.parametrize(
    ("control", "value"),
    [("headspace_volume_m3", "bad-volume"), ("headspace_temperature_K", "bad-temp")],
)
def test_dispatch_rejects_malformed_headspace_volume_and_temperature(
    vapor_pressure_data, feedstocks_data, setpoints_data, control: str, value: str
) -> None:
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    with pytest.raises(TypeError, match="is missing"):
        sim._chem_kernel.dispatch(
            ChemistryIntent.OVERHEAD_GAS_EQUILIBRIUM,
            temperature_C=1500.0,
            pressure_bar=1.0,
            control_inputs={control: value},
        )
