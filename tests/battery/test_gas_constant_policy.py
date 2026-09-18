"""Pin the gas-constant policy: source-era R is not CODATA.

A well-meaning unification of JANAF, B1544, and identity onto one R
must fail these tests rather than silently change printed-column
reproduction. Policy: simulator/battery/identity.py module docstring
and docs/chemistry-methods.md §11.
"""

from __future__ import annotations

from decimal import Decimal

from simulator.battery.generators import janaf as janaf_generator
from simulator.battery.generators import usgs_b1544 as b1544_generator
from simulator.battery.identity import R_J_PER_MOL_K, log10K_from_delta_fG_kJ_mol
from simulator.physical_constants import AVOGADRO, BOLTZMANN, GAS_CONSTANT
from simulator.reference_data.hemingway_haas_robinson_1982_usgs_b1544_loader import (
    R_J_MOL_K as B1544_LOADER_R_J_MOL_K,
)


def test_per_source_transcription_constants_are_the_printed_values() -> None:
    assert janaf_generator.JANAF_R_J_PER_MOL_K == Decimal("8.31441")
    assert b1544_generator.B1544_R_J_PER_MOL_K == Decimal("8.3143")
    assert B1544_LOADER_R_J_MOL_K == Decimal("8.3143")
    assert b1544_generator.B1544_R_J_PER_MOL_K == B1544_LOADER_R_J_MOL_K


def test_cross_source_identity_uses_codata_si2019() -> None:
    # Premise: SI 2019 defines R = N_A k_B exactly.
    # Algebra: 6.02214076e23 * 1.380649e-23 = 8.31446261815324 J/(mol·K).
    # Unit check: (1/mol) * (J/K) = J/(mol·K).
    # Sanity: the identity helper's default R is that value, not 8.31441.
    assert GAS_CONSTANT == AVOGADRO * BOLTZMANN
    assert GAS_CONSTANT == 8.31446261815324
    assert R_J_PER_MOL_K == Decimal(str(GAS_CONSTANT))
    assert R_J_PER_MOL_K == Decimal("8.31446261815324")


def test_source_era_constants_are_not_codata() -> None:
    assert janaf_generator.JANAF_R_J_PER_MOL_K != R_J_PER_MOL_K
    assert b1544_generator.B1544_R_J_PER_MOL_K != R_J_PER_MOL_K
    assert janaf_generator.JANAF_R_J_PER_MOL_K != b1544_generator.B1544_R_J_PER_MOL_K


def test_b1544_corundum_printed_log_kf_needs_period_r() -> None:
    # Hemingway, Haas & Robinson 1982 USGS B1544, corundum 298.15 K.
    # Printed log Kf = 277.203. R = 8.3143 reproduces; CODATA does not
    # round to the printed grain.
    delta_fg = Decimal("-1582.242")
    t_k = Decimal("298.15")
    source = log10K_from_delta_fG_kJ_mol(
        delta_fg,
        t_k,
        gas_constant_J_per_mol_K=b1544_generator.B1544_R_J_PER_MOL_K,
    )
    modern = log10K_from_delta_fG_kJ_mol(delta_fg, t_k)
    assert abs(source - Decimal("277.203")) < Decimal("0.001")
    assert abs(modern - Decimal("277.203")) >= Decimal("0.001")


def test_log10k_helper_default_is_identity_codata() -> None:
    delta_fg = Decimal("-5582.653")
    t_k = Decimal("298.15")
    assert log10K_from_delta_fG_kJ_mol(delta_fg, t_k) == log10K_from_delta_fG_kJ_mol(
        delta_fg,
        t_k,
        gas_constant_J_per_mol_K=R_J_PER_MOL_K,
    )
    janaf = log10K_from_delta_fG_kJ_mol(
        delta_fg,
        t_k,
        gas_constant_J_per_mol_K=janaf_generator.JANAF_R_J_PER_MOL_K,
    )
    assert janaf != log10K_from_delta_fG_kJ_mol(delta_fg, t_k)
