"""Pin Shornikov's crystalline vaporization coefficient recommendations."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2015-cai-vaporization-coefficients.yaml"
METHOD_QUOTE = (
    "Based on the analysis of theoretical and experimental data, the following "
    "vaporization coefficients are suggested for crystalline oxides contained "
    "in Ca–Al–inclusions in chondrites:"
)
RECOMMENDATION_IDS = (
    "shornikov_2015_cai_vaporization_coefficients_p007_t05_r07_c08",
    "shornikov_2015_cai_vaporization_coefficients_p007_t05_r06_c08",
    "shornikov_2015_cai_vaporization_coefficients_p007_t05_r05_c08",
    "shornikov_2015_al2o3_cr_recommendation_prose",
    "shornikov_2015_cai_vaporization_coefficients_p007_t05_r03_c08",
    "shornikov_2015_cai_vaporization_coefficients_p007_t05_r04_c08",
    "shornikov_2015_cai_vaporization_coefficients_p007_t05_r08_c08",
)


def _observations(document: dict) -> dict[str, dict]:
    return {
        observation["observation_id"]: observation
        for species in document["species"].values()
        for observation in species.get("observations", [])
    }


def _assert_recommendations_are_assessed(document: dict) -> None:
    rows = _observations(document)
    for observation_id in RECOMMENDATION_IDS:
        row = rows[observation_id]
        assert row["values"]["method_class"] == "compilation_assessed"
        method = row["derivation"]["method"]
        assert method["state"]["value"] == METHOD_QUOTE
        assert method["locator"]["published_page"] == 1080
        assert method["locator"]["section"] == "Abstract"


def test_sc456_recommendations_pin_compilation_assessed_class() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_recommendations_are_assessed(document)


def test_sc456_recommendation_class_mutation_rejects_quoted_reversion() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated = copy.deepcopy(document)
    row = _observations(mutated)[RECOMMENDATION_IDS[0]]
    row["values"]["method_class"] = "quoted_attributed"
    with pytest.raises(AssertionError):
        _assert_recommendations_are_assessed(mutated)


def test_sc456_prose_table_consistency_notes_keep_both_observations() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = _observations(document)

    al_table_id = "shornikov_2015_cai_vaporization_coefficients_p007_t05_r05_c08"
    al_prose_id = "shornikov_2015_al2o3_cr_recommendation_prose"
    assert rows[al_table_id]["values"]["alpha_as_printed"] == "0.32 ± 0.02"
    assert rows[al_prose_id]["values"]["alpha_as_printed"] == "0.33 ± 0.02"
    assert rows[al_prose_id]["locator"]["page"] == 4
    for observation_id, other_id, other_value in (
        (al_table_id, al_prose_id, "0.33 ± 0.02"),
        (al_prose_id, al_table_id, "0.32 ± 0.02"),
    ):
        note = rows[observation_id]["values"]["consistency_note"]
        assert other_id in note
        assert other_value in note

    fe_table_id = "shornikov_2015_cai_vaporization_coefficients_p007_t05_r08_c08"
    fe_prose_id = "shornikov_2015_feo_cr_prose"
    assert rows[fe_table_id]["values"]["alpha_as_printed"] == "1.00 ± 0.30"
    assert rows[fe_prose_id]["values"]["alpha_as_printed"] == "1.00 ± 0.20"
    assert rows[fe_prose_id]["locator"]["page"] == 4
    for observation_id, other_id, other_value in (
        (fe_table_id, fe_prose_id, "1.00 ± 0.20"),
        (fe_prose_id, fe_table_id, "1.00 ± 0.30"),
    ):
        note = rows[observation_id]["values"]["consistency_note"]
        assert other_id in note
        assert other_value in note


def test_sc456_recommendation_extract_migrates_without_hard_issues() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()
