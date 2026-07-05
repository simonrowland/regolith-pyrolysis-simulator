"""Tests for the builtin OVERHEAD_BLEED provider."""

from __future__ import annotations

import pytest

from engines.builtin.overhead_bleed import BuiltinOverheadBleedProvider
from simulator.accounting import resolve_species_formula
from simulator.chemistry.kernel import ChemistryIntent
from tests.chemistry.conftest import _build_sim


def test_provider_declares_overhead_bleed_authority():
    provider = BuiltinOverheadBleedProvider()
    profile = provider.capability_profile()

    assert profile.intents == frozenset({ChemistryIntent.OVERHEAD_BLEED})
    assert profile.is_authoritative_for == frozenset(
        {ChemistryIntent.OVERHEAD_BLEED}
    )
    assert profile.declared_accounts == frozenset(
        {
            "process.overhead_gas",
            "terminal.offgas",
            "terminal.oxygen_melt_offgas_stored",
            "terminal.oxygen_melt_offgas_vented_to_vacuum",
            "terminal.oxygen_bubbler_external_vented_to_vacuum",
        }
    )


def test_force_drain_bleed_commits_pure_move_o2_partition(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"O2": 5.0},
        source="test overhead oxygen",
    )

    result = sim._dispatch_overhead_bleed(
        force_drain_all=True,
        o2_vented_kg=3.0,
    )
    proof = dict(result.transition.atom_balance_proof)

    assert result.status == "ok"
    assert result.transition is not None
    assert result.transition.debits["process.overhead_gas"]["O2"] > 0.0
    assert "O2" not in result.transition.credits.get("terminal.offgas", {})
    assert abs(proof.get("O", 0.0)) <= 1.0e-12
    assert sim.atom_ledger.kg_by_account("process.overhead_gas").get(
        "O2", 0.0
    ) == pytest.approx(0.0)
    assert sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_stored"
    )["O2"] == pytest.approx(2.0)
    assert sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_vented_to_vacuum"
    )["O2"] == pytest.approx(3.0)


def test_bleed_conductance_caps_partial_drain(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"O2": 1.0},
        source="test overhead oxygen",
    )

    result = sim._dispatch_and_commit(
        ChemistryIntent.OVERHEAD_BLEED,
        control_inputs={
            "p_total_bar": 2.0,
            "p_downstream_bar": 1.0,
            "bleed_conductance_kg_s_per_bar": 0.01,
            "dt_hr": 1.0 / 3600.0,
            "force_drain_all": False,
            "max_o2_flow_kg_hr": 100.0,
        },
    )

    diagnostic = dict(result.diagnostic or {})
    assert diagnostic["bled_o2_kg"] == pytest.approx(0.01)
    assert sim.atom_ledger.kg_by_account("process.overhead_gas")[
        "O2"
    ] == pytest.approx(0.99)
    assert sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_stored"
    )["O2"] == pytest.approx(0.01)
    assert sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_vented_to_vacuum"
    ).get("O2", 0.0) == pytest.approx(0.0)


def test_external_o2_bleed_is_not_stored_as_melt_offgas_product(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    o2_molar_mass = resolve_species_formula(
        "O2",
        sim.species_formula_registry,
    ).molar_mass_kg_per_mol()
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"O2": 10.0 * o2_molar_mass},
        source="test mixed overhead oxygen",
    )

    result = sim._dispatch_and_commit(
        ChemistryIntent.OVERHEAD_BLEED,
        control_inputs={
            "force_drain_all": True,
            "external_o2_in_overhead_mol": 4.0,
            "max_o2_flow_kg_hr": 100.0,
        },
    )

    diagnostic = dict(result.diagnostic or {})
    assert diagnostic["bled_o2_mol"] == pytest.approx(10.0)
    assert diagnostic["external_o2_bled_mol"] == pytest.approx(4.0)
    assert sim.atom_ledger.mol_by_account(
        "terminal.oxygen_melt_offgas_stored"
    )["O2"] == pytest.approx(6.0)
    assert sim.atom_ledger.mol_by_account(
        "terminal.oxygen_bubbler_external_vented_to_vacuum"
    )["O2"] == pytest.approx(4.0)
    assert sim.atom_ledger.mol_by_account(
        "terminal.oxygen_melt_offgas_vented_to_vacuum"
    ).get("O2", 0.0) == pytest.approx(0.0)
