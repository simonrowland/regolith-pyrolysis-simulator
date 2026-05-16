from __future__ import annotations

import pytest

from .helpers import run_c0_headspace


def test_toggle_on_derives_non_floor_o2_headspace():
    _sim, snapshots, hour_trace, _sio_cumulative_kg = run_c0_headspace(
        enabled=True,
        hours=24,
    )

    assert hour_trace[24]["p_O2_bar"] >= 1.0e-5
    assert max(abs(s.mass_balance_error_pct) for s in snapshots) <= 1.0e-12


def test_toggle_off_existing_path_keeps_mass_balance_closed():
    sim, snapshots, _hour_trace, _sio_cumulative_kg = run_c0_headspace(
        enabled=False,
        hours=24,
    )

    assert max(abs(s.mass_balance_error_pct) for s in snapshots) <= 1.0e-12
    assert sim.atom_ledger.kg_by_account("process.overhead_gas") == {}


def test_finite_headspace_keeps_oxygen_bins_distinct():
    sim, _snapshots, _hour_trace, _sio_cumulative_kg = run_c0_headspace(
        enabled=True,
        hours=24,
    )

    bins = {
        account: sim.atom_ledger.kg_by_account(account).get("O2", 0.0)
        for account in (
            "terminal.oxygen_stage0_stored",
            "terminal.oxygen_mre_anode_stored",
            "terminal.oxygen_melt_offgas_stored",
            "terminal.oxygen_melt_offgas_vented_to_vacuum",
        )
    }
    assert set(bins) == {
        "terminal.oxygen_stage0_stored",
        "terminal.oxygen_mre_anode_stored",
        "terminal.oxygen_melt_offgas_stored",
        "terminal.oxygen_melt_offgas_vented_to_vacuum",
    }
    assert bins["terminal.oxygen_melt_offgas_stored"] > 0.0
    assert bins["terminal.oxygen_mre_anode_stored"] == pytest.approx(0.0)
