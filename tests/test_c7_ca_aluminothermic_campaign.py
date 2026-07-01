from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from simulator.campaigns import CampaignManager
from simulator.run_executor import RunExecutor
from simulator.runner import PyrolysisRun
from simulator.state import BatchRecord, CampaignPhase, DecisionType


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MASS_BALANCE_HARD_GATE_PCT = 5.0e-12


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((DATA_DIR / name).read_text()) or {}


def _c7_patch(**overrides) -> dict:
    c7 = {
        "enabled": True,
        "al_credit_limit_kg": 20.0,
        "extent_fraction": 0.1,
        "hold_time_h": 1.0,
        "stir_factor": 6.0,
    }
    c7.update(overrides)
    return {"campaigns": {"C7": c7}}


def test_c7_default_off_preserves_c6_completion_path():
    manager = CampaignManager(_load_yaml("setpoints.yaml"))
    record = BatchRecord(branch="two")

    next_campaign = manager.get_next_campaign(CampaignPhase.C6, record)

    assert next_campaign is CampaignPhase.COMPLETE
    assert (DecisionType.C7_PROCEED, "yes") not in record.decisions


def test_c7_enabled_routes_after_c6_with_explicit_proceed_decision():
    setpoints = _load_yaml("setpoints.yaml")
    patched = copy.deepcopy(setpoints)
    patched["campaigns"]["C7"]["enabled"] = True
    manager = CampaignManager(patched)
    record = BatchRecord(branch="two")

    next_campaign = manager.get_next_campaign(CampaignPhase.C6, record)

    assert next_campaign is CampaignPhase.C7_CA_ALUMINOTHERMIC
    assert (DecisionType.C7_PROCEED, "yes") in record.decisions


def test_c7_enabled_run_refuses_on_unfavorable_computed_margin_and_closes_mass(
    tmp_path, monkeypatch
):
    # V23 (pre-0.6): C7 no longer treats the configured thermo_margin scalar
    # SIGN as thermodynamic proof -- the COMPUTED JANAF-Ellingham Al/Ca margin
    # governs. Under the modeled hard-vacuum hold that margin is
    # -153.94 kJ/mol O2 (unfavorable), so C7 correctly REFUSES and yields zero
    # Ca metal even though the config default scalar (+1.0) is what the old
    # sign-reading bug would have proceeded on. No 1100-1300 C reduction
    # setpoint has a positive computed margin, so the honest integration
    # contract here is the refusal path; the favorable-margin transition is
    # covered synthetically at the provider unit level
    # (tests/chemistry/test_builtin_ca_aluminothermic_step_provider.py).
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl"))
    run = PyrolysisRun(
        feedstock_id="targeted_super_kreep_ore",
        campaign="C7_CA_ALUMINOTHERMIC",
        hours=2,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
        setpoints_patch=_c7_patch(),
        run_metadata_overrides={
            "started_at_utc": "2026-06-28T00:00:00Z",
            "kernel_commit_sha": "c7-test",
        },
    )

    execution = RunExecutor().execute(run._session_config())
    document = run._build_output(execution)
    report = document["c7_product_report"]

    assert execution.status == "ok"
    worst = max(
        abs(snapshot.mass_balance_error_pct or 0.0)
        for snapshot in execution.snapshots
    )
    assert worst <= MASS_BALANCE_HARD_GATE_PCT
    assert report["enabled"] is True

    # Refusal => zero aluminothermic products; the feedstock stays as un-C7-
    # enriched rump (REE fraction unchanged, no CaO removed).
    products = report["products"]
    assert products["Ca_metal"]["kg"] == 0.0
    assert products["calcium_aluminate_cement_slag"]["kg"] == 0.0
    ree = products["residual_REE_enriched_terminal_ceramic"]
    assert ree["REE_enrichment_factor"] == pytest.approx(1.0, rel=0.0, abs=1e-12)
    assert ree["REE_oxides_wt_pct_after_C7"] == pytest.approx(
        ree["REE_oxides_wt_pct_before_C7"], rel=0.0, abs=1e-12
    )

    # The refusal is reported honestly: computed margin < 0 drives it, NOT the
    # nominally-positive configured scalar. This is the V23 contract.
    refusal = document["c7_refusal_diagnostic"]
    assert refusal["reason_refused"] == "c7_vacuum_shifted_thermo_margin_unfavorable"
    assert refusal["computed_thermo_margin_kj_per_mol_o2"] == pytest.approx(
        -153.94070649999992, rel=0.0, abs=1e-9
    )
    assert refusal["computed_thermo_margin_kj_per_mol_o2"] < 0.0
    assert refusal["configured_thermo_margin_kj_per_mol_o2"] == pytest.approx(1.0)
    assert refusal["thermo_margin_source"] == "builtin_janaf_ellingham_al_ca"

    # Al credit was imported but stays undrawn because the reduction refused.
    assert report["diagnostic"]["c7_al_credit_input_kg"] == pytest.approx(20.0)
    assert report["diagnostic"]["c7_al_credit_unused_mol"] > 0.0


def test_c7_set_it_to_11_reports_campaign_knob_saturations(tmp_path, monkeypatch):
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl"))
    run = PyrolysisRun(
        feedstock_id="targeted_super_kreep_ore",
        campaign="C7_CA_ALUMINOTHERMIC",
        hours=2,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
        setpoints_patch=_c7_patch(al_fraction=11.0, extent_fraction=11.0),
        run_metadata_overrides={
            "started_at_utc": "2026-06-28T00:00:00Z",
            "kernel_commit_sha": "c7-test",
        },
    )

    execution = RunExecutor().execute(run._session_config())
    document = run._build_output(execution)
    saturation = document["c7_product_report"]["diagnostic"]["c7_knob_saturation"]
    paths = {row["path"] for row in saturation}

    assert execution.status == "ok"
    assert "campaigns.C7.al_fraction" in paths
    assert "campaigns.C7.extent_fraction" in paths
    assert "campaigns.C7.stir_factor" in paths
    assert {
        row["path"]: row["saturated"]
        for row in saturation
        if row["path"] in {
            "campaigns.C7.al_fraction",
            "campaigns.C7.extent_fraction",
        }
    } == {
        "campaigns.C7.al_fraction": True,
        "campaigns.C7.extent_fraction": True,
    }
