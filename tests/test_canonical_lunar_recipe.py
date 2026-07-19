from __future__ import annotations

import json
from pathlib import Path

import yaml

from simulator.campaigns import CampaignManager
from simulator.recipe_io import load_recipe_patch
from simulator.runner import PyrolysisRun
from simulator.state import BatchRecord, CampaignPhase, MeltState


ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / "data" / "recipes" / "canonical_lunar_full_yield.yaml"
DEMO = ROOT / "web" / "report_viewer" / "sample-run-artifact.json"
LEGACY_REQUIRED_SPECIES_TOTAL_KG = 134.88620592669162


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def test_canonical_recipe_is_catalog_grounded_and_pinned_in_lunar_profile() -> None:
    setpoints = _yaml(ROOT / "data" / "setpoints.yaml")
    furnace_catalog = _yaml(ROOT / "data" / "furnace_materials.yaml")
    profile = _yaml(ROOT / "data" / "optimize_profiles" / "lunar_mare_low_ti.yaml")
    recipe = load_recipe_patch(RECIPE)

    alumina_ceiling = furnace_catalog["furnace_materials"]["dense_alumina_max"][
        "max_service_T_C"
    ]
    seed = next(
        item
        for item in profile["seed_recipes"]
        if item["id"] == "canonical-lunar-full-yield"
    )

    assert alumina_ceiling == 1843
    assert recipe["furnace_max_T_C"] == alumina_ceiling
    assert recipe["campaigns"]["C2A_continuous"]["temp_range_C"][-1] == alumina_ceiling
    assert recipe["campaigns"]["C3"]["alkali_dosing"] == {
        "K_kg": 56.0,
        "Na_kg": 140.0,
    }
    assert setpoints["campaigns"]["C5"]["allow_mre_voltage_cap_V"] == 0.0
    assert seed["patch"]["campaigns"]["C5"]["allow_mre_voltage_cap_V"] == 0.0


def test_canonical_sequence_recovers_mg_before_final_boiloff_and_skips_mre() -> None:
    recipe = load_recipe_patch(RECIPE)
    setpoints = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        setpoints_patch=recipe,
    )._session_config().setpoints
    manager = CampaignManager(setpoints)
    record = BatchRecord(feedstock_key="debug_pure_feo")

    assert manager.get_next_campaign(CampaignPhase.C0B, record) == CampaignPhase.C2A_STAGED
    assert manager.get_next_campaign(CampaignPhase.C2A_STAGED, record) == CampaignPhase.C3_NA
    c3_melt = MeltState(campaign=CampaignPhase.C3_NA)
    manager.configure_campaign(c3_melt, CampaignPhase.C3_NA)
    assert manager.get_temp_target(CampaignPhase.C3_NA, 0, c3_melt) == (1150.0, 600.0)

    assert manager.get_next_campaign(CampaignPhase.C3_NA, record) == CampaignPhase.C4
    assert manager.c5_enabled is False
    assert manager.get_next_campaign(CampaignPhase.C4, record) == CampaignPhase.C2A

    final_ramp_melt = MeltState(campaign=CampaignPhase.C2A, temperature_C=1670.0)
    manager.configure_campaign(final_ramp_melt, CampaignPhase.C2A)
    assert manager.get_temp_target(CampaignPhase.C2A, 0, final_ramp_melt) == (
        1843.0,
        7.5,
    )
    assert manager._campaign_overrides(CampaignPhase.C2A)["min_hold_hr"] == 82.0
    assert manager._campaign_overrides(CampaignPhase.C2A)["max_hours"] == 160.0
    assert manager.get_next_campaign(CampaignPhase.C2A, record) == CampaignPhase.COMPLETE
    operator_record = BatchRecord(path="A_post_mg_boiloff", branch="two")
    assert manager.get_next_campaign(CampaignPhase.C2A, operator_record) is (
        CampaignPhase.COMPLETE
    )


def test_canonical_demo_yields_are_kg_scale_and_beat_legacy_floor() -> None:
    artifact = json.loads(DEMO.read_text(encoding="utf-8"))
    summaries = [item["summary"] for item in artifact["timesteps"]]
    final_yields = summaries[-1]["metal_yields_kg"]
    sio_kg = sum(item.get("vapor_species_kg_hr", {}).get("SiO", 0.0) for item in summaries)
    required_total_kg = sum(
        (
            final_yields["Fe"],
            final_yields["Mg"],
            final_yields["Na"],
            final_yields["K"],
            sio_kg,
        )
    )

    assert artifact["header"]["run_id"] == "canonical-lunar-full-yield"
    assert artifact["header"]["recipe_snapshot"]["setpoints_patch"] == load_recipe_patch(RECIPE)
    assert artifact["header"]["campaign_chain"] == [
        "C0",
        "C0B",
        "C2A_STAGED",
        "C3_NA",
        "C4",
        "C2A",
    ]
    assert max(item["T_C"] for item in summaries) == 1843.0
    assert "C5" not in artifact["header"]["campaign_chain"]
    assert final_yields["Fe"] > 1.0
    assert sio_kg > 1.0
    assert final_yields["Na"] + final_yields["K"] > 1.0
    assert final_yields["Mg"] > 1.0
    assert required_total_kg >= LEGACY_REQUIRED_SPECIES_TOTAL_KG
    assert max(abs(item["mass_balance_pct"]) for item in summaries) <= 5e-12
