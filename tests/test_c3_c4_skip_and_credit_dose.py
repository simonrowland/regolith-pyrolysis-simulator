"""d-212: credit-line C3 doses, and C3/C4 recipe skips.

The plant bands and the reference deadband live in production code.
These tests check the consequences: a 1 kg dose is not zeroed, skip
routing does not end the batch, and a sub-centigram credit dose is
not reported as unavailable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.campaigns import CampaignManager
from simulator.recipe_io import RecipeIOError, load_recipe_patch_for_charge
from simulator.recipe import (
    C3_ALKALI_DOSING_K_KG_PATH,
    C3_ALKALI_DOSING_NA_KG_PATH,
    RecipePatch,
    RecipeSchema,
    c3_alkali_credit_default_kg,
    c3_alkali_zero_level_kg,
    resolve_c3_alkali_credit_dose_kg,
)
from simulator.runner import _c3_alkali_dosing_kg_by_species
from simulator.state import BatchRecord, CampaignPhase


ROOT = Path(__file__).resolve().parents[1]


def _plant_setpoints() -> dict:
    return yaml.safe_load(
        (ROOT / "data" / "setpoints.yaml").read_text(encoding="utf-8")
    )


def test_one_kilogram_credit_default_is_not_zeroed() -> None:
    setpoints = _plant_setpoints()
    dosing = setpoints["campaigns"]["C3"]["alkali_dosing"]
    assert dosing["Na_kg"] is None
    assert dosing["K_kg"] is None

    na_kg = resolve_c3_alkali_credit_dose_kg(setpoints, "Na", 1.0)
    k_kg = resolve_c3_alkali_credit_dose_kg(setpoints, "K", 1.0)
    assert na_kg == pytest.approx(c3_alkali_credit_default_kg(setpoints, "Na", 1.0))
    assert na_kg == pytest.approx(0.110)
    assert k_kg == pytest.approx(0.023)
    assert na_kg > c3_alkali_zero_level_kg("Na", 1.0)
    assert k_kg > c3_alkali_zero_level_kg("K", 1.0)

    mapped = _c3_alkali_dosing_kg_by_species(setpoints, charge_kg=1.0)
    assert mapped["Na"] == pytest.approx(na_kg)
    assert mapped["K"] == pytest.approx(k_kg)


def test_small_explicit_dose_survives_on_a_small_charge_and_dies_at_reference() -> None:
    setpoints = {
        "campaigns": {
            "C3": {"alkali_dosing": {"Na_kg": 0.5, "K_kg": 0.0}},
        }
    }
    assert _c3_alkali_dosing_kg_by_species(setpoints, charge_kg=1000.0) == {}
    assert _c3_alkali_dosing_kg_by_species(setpoints, charge_kg=1.0) == {
        "Na": pytest.approx(0.5),
    }


def test_scaled_schema_keeps_a_one_kilogram_dose() -> None:
    scaled = RecipeSchema().scaled_for_charge(1.0)
    assert scaled.spec_for(C3_ALKALI_DOSING_NA_KG_PATH).high == pytest.approx(0.140)
    assert scaled.spec_for(C3_ALKALI_DOSING_K_KG_PATH).high == pytest.approx(0.056)
    patch = RecipePatch(
        {C3_ALKALI_DOSING_NA_KG_PATH: 0.110}
    ).validated(scaled)
    rendered = scaled.to_setpoints_patch(patch)
    assert rendered["campaigns"]["C3"]["alkali_dosing"]["Na_kg"] == pytest.approx(
        0.110
    )
    reference = RecipeSchema()
    assert reference.scaled_for_charge(1000.0) is reference
    stripped = RecipePatch(
        {C3_ALKALI_DOSING_NA_KG_PATH: 0.110}
    ).validated(reference)
    assert reference.to_setpoints_patch(stripped) == {}


def test_charge_scaled_recipe_dose_is_not_an_empty_patch(tmp_path: Path) -> None:
    """Studio c3dose: Na 0.110 / K 0.023 on the reference deadband becomes
    an empty patch and the runner refuses it. The same file at 1 kg is
    a real credit dose and must load.
    """
    recipe = tmp_path / "c3dose.yaml"
    recipe.write_text(
        "campaigns:\n"
        "  C3:\n"
        "    alkali_dosing:\n"
        "      Na_kg: 0.110\n"
        "      K_kg: 0.023\n",
        encoding="utf-8",
    )
    loaded = load_recipe_patch_for_charge(recipe, 1.0)
    dosing = loaded["campaigns"]["C3"]["alkali_dosing"]
    assert dosing["Na_kg"] == pytest.approx(0.110)
    assert dosing["K_kg"] == pytest.approx(0.023)
    with pytest.raises(RecipeIOError, match="empty setpoints_patch"):
        load_recipe_patch_for_charge(recipe, 1000.0)


def test_absent_skip_still_enters_c3_and_pauses_before_c4() -> None:
    manager = CampaignManager({"campaigns": {}})
    record = BatchRecord(feedstock_key="lunar_mare_low_ti", path="A")
    assert manager.get_next_campaign(CampaignPhase.C2A, record) == CampaignPhase.C3_K
    assert manager.get_next_campaign(CampaignPhase.C3_NA, record) is None


def test_debug_batch_without_skip_still_enters_c4() -> None:
    manager = CampaignManager({"campaigns": {}})
    record = BatchRecord(feedstock_key="debug_pure_feo")
    assert manager.get_next_campaign(CampaignPhase.C3_NA, record) == CampaignPhase.C4


def test_c3_skip_routes_to_c4_when_c4_is_kept() -> None:
    manager = CampaignManager(
        {"campaigns": {"C3": {"skip": True}, "C4": {"skip": False}}}
    )
    record = BatchRecord(feedstock_key="lunar_mare_low_ti", path="A")
    assert manager.get_next_campaign(CampaignPhase.C2A, record) == CampaignPhase.C4
    assert record.branch == "two"
    staged = BatchRecord(feedstock_key="lunar_mare_low_ti")
    assert (
        manager.get_next_campaign(CampaignPhase.C2A_STAGED, staged)
        == CampaignPhase.C4
    )
    path_b = BatchRecord(feedstock_key="lunar_mare_low_ti", path="B")
    assert manager.get_next_campaign(CampaignPhase.C2B, path_b) == CampaignPhase.C4


def test_c3_and_c4_skip_arm_the_final_c2a() -> None:
    manager = CampaignManager(
        {"campaigns": {"C3": {"skip": "true"}, "C4": {"skip": "true"}}}
    )
    record = BatchRecord(feedstock_key="lunar_mare_low_ti", path="A")
    assert manager.get_next_campaign(CampaignPhase.C2A, record) == CampaignPhase.C2A
    assert record.path == "A_post_mg_boiloff"
    assert manager._pending_post_mg_c2a_overrides == {
        "min_hold_hr": 82.0,
        "max_hours": 160.0,
    }
    # The path marker is already installed, so the final C2A does not loop
    # back into C3. Branch was not set to two, so C6 is not offered.
    assert manager.get_next_campaign(CampaignPhase.C2A, record) == (
        CampaignPhase.COMPLETE
    )


def test_c4_skip_from_c3_na_continues_to_the_final_c2a() -> None:
    manager = CampaignManager({"campaigns": {"C4": {"skip": True}}})
    record = BatchRecord(feedstock_key="debug_pure_feo", path="A_staged")
    nxt = manager.get_next_campaign(CampaignPhase.C3_NA, record)
    assert nxt == CampaignPhase.C2A
    assert nxt != CampaignPhase.COMPLETE
    assert record.path == "A_post_mg_boiloff"
    assert record.branch != "two"
    assert manager._pending_post_mg_c2a_overrides["min_hold_hr"] == 82.0
    assert manager._pending_post_mg_c2a_overrides["max_hours"] == 160.0


def test_plant_default_skips_c4_and_runs_c3() -> None:
    manager = CampaignManager(_plant_setpoints())
    record = BatchRecord(feedstock_key="lunar_mare_low_ti", path="A")
    assert manager.get_next_campaign(CampaignPhase.C2A, record) == CampaignPhase.C3_K
    assert manager.get_next_campaign(CampaignPhase.C3_K, record) == CampaignPhase.C3_NA
    nxt = manager.get_next_campaign(CampaignPhase.C3_NA, record)
    assert nxt == CampaignPhase.C2A
    assert record.path == "A_post_mg_boiloff"


def test_string_false_skip_does_not_skip() -> None:
    manager = CampaignManager(
        {"campaigns": {"C3": {"skip": "false"}, "C4": {"skip": "false"}}}
    )
    record = BatchRecord(feedstock_key="lunar_mare_low_ti", path="A")
    assert manager.get_next_campaign(CampaignPhase.C2A, record) == CampaignPhase.C3_K
    assert manager.get_next_campaign(CampaignPhase.C3_NA, record) is None
