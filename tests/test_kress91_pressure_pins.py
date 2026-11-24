from __future__ import annotations

import pytest

import simulator.fe_redox as fe_redox_module
from simulator.fe_redox import (
    kress91_fe3_over_sigma_fe,
    kress91_ln_fO2_temperature_delta,
    melt_mol_fractions_for_kress91,
)


COMPOSITIONS = (
    {
        "SiO2": 50.0,
        "TiO2": 1.0,
        "Al2O3": 15.0,
        "FeO": 8.0,
        "Fe2O3": 3.0,
        "MgO": 7.0,
        "CaO": 10.0,
        "Na2O": 4.0,
        "K2O": 1.0,
        "P2O5": 1.0,
    },
    {
        "SiO2": 42.0,
        "TiO2": 2.0,
        "Al2O3": 12.0,
        "FeO": 14.0,
        "Fe2O3": 2.0,
        "MgO": 10.0,
        "CaO": 8.0,
        "Na2O": 7.0,
        "K2O": 2.0,
        "P2O5": 1.0,
    },
)
PRESSURES_BAR = (0.1, 1.0, 10_000.0)
TEMPERATURES_K = (1473.15, 1873.15, 2200.0)

# Exact pre-move float.hex() outputs: (composition index, T K, P bar).
_FORWARD_HEX = {
    (0, 1473.15, 0.1): "0x1.e7cf5e992c4e6p-3",
    (0, 1473.15, 1.0): "0x1.e7cb7963f1ac7p-3",
    (0, 1473.15, 10_000.0): "0x1.5a7a86cbd9255p-3",
    (0, 1873.15, 0.1): "0x1.c5d45dd8a7689p-5",
    (0, 1873.15, 1.0): "0x1.c5d08200e885dp-5",
    (0, 1873.15, 10_000.0): "0x1.3ee62c787bb93p-5",
    (0, 2200.0, 0.1): "0x1.9dca144b5abc6p-6",
    (0, 2200.0, 1.0): "0x1.9dc6c684b5244p-6",
    (0, 2200.0, 10_000.0): "0x1.29425b3ec5e34p-6",
    (1, 1473.15, 0.1): "0x1.07d81b0e2585cp-2",
    (1, 1473.15, 1.0): "0x1.07d60d79ee559p-2",
    (1, 1473.15, 10_000.0): "0x1.799971175d25ap-3",
    (1, 1873.15, 0.1): "0x1.f4c09215f65cep-5",
    (1, 1873.15, 1.0): "0x1.f4bc56b76ab49p-5",
    (1, 1873.15, 10_000.0): "0x1.6081a8d1415a0p-5",
    (1, 2200.0, 0.1): "0x1.ca156567d8fdep-6",
    (1, 2200.0, 1.0): "0x1.ca11bfb0afb01p-6",
    (1, 2200.0, 10_000.0): "0x1.49562e3b10af7p-6",
}

# Exact pre-move temperature-rebase outputs from reference T=1473.15 K,
# reference pressure=1 bar; keys are (target T K, target P bar).
_REBASE_HEX = {
    (1473.15, 0.1): "-0x1.b61ce5f488d0fp-13",
    (1473.15, 1.0): "-0x0.0p+0",
    (1473.15, 10_000.0): "0x1.1804e1b839a48p+1",
    (1873.15, 0.1): "0x1.113a5a0d18fc0p+3",
    (1873.15, 1.0): "0x1.113bd246a3d4fp+3",
    (1873.15, 10_000.0): "0x1.4da86d176a7d2p+3",
    (2200.0, 0.1): "0x1.969a7605db266p+3",
    (2200.0, 1.0): "0x1.969bcc62942e6p+3",
    (2200.0, 10_000.0): "0x1.cdca6f4a9848ep+3",
}


def test_kress91_forward_pressure_path_stays_bit_identical() -> None:
    for composition_index, composition in enumerate(COMPOSITIONS):
        mol_fractions = melt_mol_fractions_for_kress91(composition)
        for temperature_K in TEMPERATURES_K:
            for pressure_bar in PRESSURES_BAR:
                value = kress91_fe3_over_sigma_fe(
                    fO2_log=-7.25,
                    mol_fractions=mol_fractions,
                    T_K=temperature_K,
                    pressure_bar=pressure_bar,
                )
                assert value.hex() == _FORWARD_HEX[
                    (composition_index, temperature_K, pressure_bar)
                ]


def test_kress91_temperature_rebase_pressure_path_stays_bit_identical() -> None:
    reference_T_K = TEMPERATURES_K[0]
    for target_T_K in TEMPERATURES_K:
        for target_pressure_bar in PRESSURES_BAR:
            value = kress91_ln_fO2_temperature_delta(
                reference_T_K,
                target_T_K,
                reference_pressure_bar=1.0,
                target_pressure_bar=target_pressure_bar,
            )
            assert value.hex() == _REBASE_HEX[(target_T_K, target_pressure_bar)]


def test_kress91_forward_floor_is_owned_by_shared_pressure_helper() -> None:
    for composition in COMPOSITIONS:
        mol_fractions = melt_mol_fractions_for_kress91(composition)
        for temperature_K in TEMPERATURES_K:
            below_floor = kress91_fe3_over_sigma_fe(
                fO2_log=-7.25,
                mol_fractions=mol_fractions,
                T_K=temperature_K,
                pressure_bar=1.0e-12,
            )
            at_floor = kress91_fe3_over_sigma_fe(
                fO2_log=-7.25,
                mol_fractions=mol_fractions,
                T_K=temperature_K,
                pressure_bar=1.0e-9,
            )
            assert below_floor.hex() == at_floor.hex()


def test_kress91_rebase_floor_is_owned_by_shared_pressure_helper() -> None:
    reference_T_K = TEMPERATURES_K[0]
    for target_T_K in TEMPERATURES_K:
        below_floor = kress91_ln_fO2_temperature_delta(
            reference_T_K,
            target_T_K,
            reference_pressure_bar=1.0,
            target_pressure_bar=1.0e-12,
        )
        at_floor = kress91_ln_fO2_temperature_delta(
            reference_T_K,
            target_T_K,
            reference_pressure_bar=1.0,
            target_pressure_bar=1.0e-9,
        )
        assert below_floor.hex() == at_floor.hex()


def test_kress91_pressure_rebase_is_zero_for_identical_endpoints_at_one_bar() -> None:
    for temperature_K in TEMPERATURES_K:
        value = kress91_ln_fO2_temperature_delta(
            temperature_K,
            temperature_K,
            reference_pressure_bar=1.0,
            target_pressure_bar=1.0,
        )
        assert value.hex() == "-0x0.0p+0"


def test_mutating_shared_pressure_owner_breaks_both_consumer_pins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = fe_redox_module._kress91_pressure_terms

    def perturbed(T_K: float, pressure_pa: float) -> tuple[float, float, float]:
        first, second, third = original(T_K, pressure_pa)
        return first * 1.01, second, third

    monkeypatch.setattr(fe_redox_module, "_kress91_pressure_terms", perturbed)
    with pytest.raises(AssertionError):
        test_kress91_forward_pressure_path_stays_bit_identical()
    with pytest.raises(AssertionError):
        test_kress91_temperature_rebase_pressure_path_stays_bit_identical()


def test_mutating_shared_pressure_floor_breaks_both_consumer_pins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_kress91_forward_floor_is_owned_by_shared_pressure_helper()
    test_kress91_rebase_floor_is_owned_by_shared_pressure_helper()
    monkeypatch.setattr(fe_redox_module, "_KRESS91_PRESSURE_FLOOR_BAR", 1.0e-10)
    with pytest.raises(AssertionError):
        test_kress91_forward_floor_is_owned_by_shared_pressure_helper()
    with pytest.raises(AssertionError):
        test_kress91_rebase_floor_is_owned_by_shared_pressure_helper()
