from __future__ import annotations

import ast
import inspect

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
    # 2026-08-05 carrier batch 1a1ab47: the added carrier route leaves
    # +3.988089247730642e-20 kg Na roundoff in overhead. The exact audit and
    # outward projection agree, while HI-2 above remains well inside contract.
    # 2026-08-05 MC-4 wave 1B (a34318c): the composed Na carrier set routes
    # the tick flux exactly; the residual Na roundoff dust is gone and the
    # audited overhead is now empty. HI-2 above remains <= 1e-12 %.
    # Restored flagged routing leaves only these identified roundoff parcels.
    # 2026-10-13 b-850 round 2: parent-oxide alkali-dimer pressures no longer
    # leave the prior 1.994e-20 kg Na roundoff parcel. The remaining SiO2_gas
    # residual shifts from 9.409239450400745e-14 to 9.409239449650439e-14 kg.
    # 2026-10-13 t-1236: the hour-18 overhead_bleed debit now keeps its
    # 1.5660402193050345e-12 mol SiO2_gas (9.409239449650439e-14 kg) mol-lot
    # entry, so it consumes the residual that the old lot materialization lost.
    # b-894/t-1236 merged-tree measurement: the matching mol-basis credit
    # retains that parcel in terminal.offgas, leaving overhead empty.
    assert set(sim.atom_ledger.kg_by_account("process.overhead_gas")) == set()
    assert set(sim.atom_ledger.project_account_kg("process.overhead_gas")) == set()
    assert sim.atom_ledger.kg_by_account("terminal.offgas")["SiO2_gas"] == pytest.approx(
        9.409239449650439e-14, rel=1.0e-12, abs=0.0,
    )


def test_empty_overhead_expectations_do_not_use_numeric_approx():
    function = ast.parse(
        inspect.getsource(test_toggle_off_existing_path_keeps_mass_balance_closed)
    )
    empty_mapping_names = {
        node.target.id
        for node in ast.walk(function)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and isinstance(node.value, ast.Dict)
        and not node.value.keys
    }
    approx_calls_with_empty_mapping = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "approx"
        and node.args
        and (
            isinstance(node.args[0], ast.Dict)
            and not node.args[0].keys
            or isinstance(node.args[0], ast.Name)
            and node.args[0].id in empty_mapping_names
        )
    ]

    assert not approx_calls_with_empty_mapping


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
