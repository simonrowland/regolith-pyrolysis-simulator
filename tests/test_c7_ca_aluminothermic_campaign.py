from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from simulator.campaigns import CampaignManager
from simulator.extraction import ExtractionMixin
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


def test_c7_enabled_run_predicts_after_flagged_transport_and_closes_mass(
    tmp_path, monkeypatch
):
    # d-209 removes the regime-only transport zero. The computed JANAF
    # margin (including product backpressure) now governs the actual extent.
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

    # 2026-07-11 0.5.10 E-MOVE: BCD state-cap endpoint semantics finish the
    # one-hour C7 hold before the requested two-hour budget is consumed.
    assert execution.status == "partial"
    worst = max(
        abs(snapshot.mass_balance_error_pct or 0.0)
        for snapshot in execution.snapshots
    )
    assert worst <= MASS_BALANCE_HARD_GATE_PCT
    assert report["enabled"] is True

    # Removing Ca while retaining REEs enriches the terminal ceramic.
    products = report["products"]
    assert products["Ca_metal"]["kg"] > 0.0
    assert products["calcium_aluminate_cement_slag"]["kg"] > 0.0
    ree = products["residual_REE_enriched_terminal_ceramic"]
    assert ree["REE_enrichment_factor"] > 1.0
    assert ree["REE_retention_fraction"] == pytest.approx(1.0)
    assert ree["mass_removal_extent_fraction"] > 0.0
    assert ree["REE_partition_source_ids"] == ["REF-056", "REF-057"]
    assert ree["REE_enrichment_derivation"] == (
        "E=(R1/M1)/(R0/M0); X=1-M1/M0; retention=R1/R0"
    )
    assert ree["REE_oxides_wt_pct_after_C7"] > ree["REE_oxides_wt_pct_before_C7"]

    # The transport flag is visible; the unchanged thermodynamic resolver
    # must independently authorize every committed reduction.
    assert document["c7_refusal_diagnostic"] == {}
    diagnostic = report["diagnostic"]
    step = next(
        row for row in diagnostic["raw_diagnostics"]
        if "computed_thermo_margin_kj_per_mol_o2" in row
    )
    assert step["computed_thermo_margin_kj_per_mol_o2"] > 0.0
    assert diagnostic["r_transport"] > 0.0
    assert diagnostic["transport_ca_mol"] > 0.0
    assert diagnostic["c7_transport_refusal"] == ""
    assert diagnostic["ledger_yields_authorized"] is True
    assert diagnostic["p_bulk_transport_domain"] == "out_of_domain_transitional"
    assert diagnostic["authority_reason"] == "viscous_p_bulk_transport_out_of_domain"

    # The partial-extent request leaves some imported Al credit unused.
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

    # Same state-cap endpoint semantics as the direct C7 prediction pin above.
    assert execution.status == "partial"
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


def test_c7_zero_ree_baseline_reports_undefined_enrichment():
    before = {
        "REE_oxides_kg": 0.0,
        "REE_oxides_wt_pct": 0.0,
        "residual_terminal_ceramic_kg": 100.0,
    }
    after = {
        "REE_oxides_kg": 0.0,
        "REE_oxides_wt_pct": 0.0,
        "residual_terminal_ceramic_kg": 50.0,
    }

    extent = ExtractionMixin._c7_ree_enrichment_extent(before, after)

    assert extent["REE_enrichment_factor"] is None
    assert extent["REE_retention_fraction"] is None
    assert extent["mass_removal_extent_fraction"] == pytest.approx(0.5)
