from __future__ import annotations

import pytest

from simulator.state import CampaignPhase

from .helpers import run_campaign_headspace, run_c0_headspace


SIO_HEADSPACE_CAMPAIGN = CampaignPhase.C2A
SIO_HEADSPACE_START_TEMPERATURE_C = 1550.0
SIO_HEADSPACE_HOURS = 6


def _run_sio_headspace(*, enabled: bool):
    # Re-speciation contract: SiO, not elemental metal, supplies overhead O2.
    return run_campaign_headspace(
        enabled=enabled,
        hours=SIO_HEADSPACE_HOURS,
        campaign=SIO_HEADSPACE_CAMPAIGN,
        start_temperature_C=SIO_HEADSPACE_START_TEMPERATURE_C,
    )


def test_toggle_on_derives_non_floor_o2_headspace():
    _sim, snapshots, hour_trace, sio_cumulative_kg = _run_sio_headspace(
        enabled=True,
    )

    assert sio_cumulative_kg > 0.0
    assert hour_trace[SIO_HEADSPACE_HOURS]["p_O2_bar"] >= 1.0e-5
    assert max(abs(s.mass_balance_error_pct) for s in snapshots) <= 1.0e-12


def test_toggle_off_existing_path_keeps_mass_balance_closed():
    sim, snapshots, _hour_trace, _sio_cumulative_kg = run_c0_headspace(
        enabled=False,
        hours=24,
    )

    assert max(abs(s.mass_balance_error_pct) for s in snapshots) <= 1.0e-12
    assert sim.atom_ledger.kg_by_account("process.overhead_gas") == {}


def test_finite_headspace_keeps_oxygen_bins_distinct():
    sim, _snapshots, _hour_trace, sio_cumulative_kg = _run_sio_headspace(
        enabled=True,
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
    assert sio_cumulative_kg > 0.0
    assert bins["terminal.oxygen_melt_offgas_stored"] > 0.0
    assert bins["terminal.oxygen_mre_anode_stored"] == pytest.approx(0.0)
