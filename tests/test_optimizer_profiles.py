from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from simulator.config import DEFAULT_DATA_DIR
from simulator.optimize import study
from simulator.optimize.physics import GATE_ORDER
from simulator.optimize.profiles import (
    KNOWN_OBJECTIVE_METRICS,
    ProfileValidationError,
    validate_profile,
    validate_profile_catalog,
)
from simulator.optimize.recipe import RecipePatch, RecipeSchema


def test_profile_catalog_matches_feedstocks_and_validates_seeds() -> None:
    feedstocks = yaml.safe_load((DEFAULT_DATA_DIR / "feedstocks.yaml").read_text())
    profiles = validate_profile_catalog()

    assert set(profiles) == set(feedstocks)
    for feedstock, profile in profiles.items():
        assert profile["feedstock"] == feedstock
        for objective in profile["objectives"]:
            assert objective["metric"] in KNOWN_OBJECTIVE_METRICS
        assert set(profile["constraints"]["gates"]).issubset(set(GATE_ORDER))
        for seed in profile["seed_recipes"]:
            RecipePatch.from_nested(seed["patch"]).validated(RecipeSchema())


def test_each_profile_drives_stub_study(tmp_path: Path) -> None:
    for feedstock, profile in validate_profile_catalog().items():
        result = study.run(
            profile,
            feedstock,
            "random",
            "stub",
            1,
            1,
            tmp_path / feedstock,
            seed=11,
        )

        assert result.winner is not None
        assert result.pareto


def test_unknown_objective_metric_raises_named_error() -> None:
    profile = _profile_copy("lunar_mare_low_ti")
    profile["objectives"][0]["metric"] = "unobtanium_kg"

    with pytest.raises(ProfileValidationError, match="unknown objective metric"):
        validate_profile(profile, expected_feedstock="lunar_mare_low_ti")


def test_profile_objective_importance_requires_evidence_rationale() -> None:
    profile = _profile_copy("lunar_mare_low_ti")
    profile["objectives"][0].pop("rationale")

    with pytest.raises(ProfileValidationError, match="insufficient-evidence"):
        validate_profile(profile, expected_feedstock="lunar_mare_low_ti")


def test_malformed_seed_recipe_raises_named_error() -> None:
    profile = _profile_copy("lunar_mare_low_ti")
    profile["seed_recipes"][0]["patch"] = {
        "campaigns": {"C0": {"label": "not an optimizer knob"}}
    }

    with pytest.raises(ProfileValidationError, match="malformed seed recipe"):
        validate_profile(profile, expected_feedstock="lunar_mare_low_ti")


def test_unknown_profile_key_raises_named_error() -> None:
    profile = _profile_copy("lunar_mare_low_ti")
    profile["surprise"] = True

    with pytest.raises(ProfileValidationError, match="unknown profile key"):
        validate_profile(profile, expected_feedstock="lunar_mare_low_ti")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda profile: profile.update({"objectivse": profile["objectives"]}),
            "unknown profile key",
        ),
        (lambda profile: profile.pop("run"), "profile missing required keys: run"),
    ],
)
def test_raw_mapping_profile_resolver_uses_profile_validator(mutation, message: str) -> None:
    profile = _profile_copy("lunar_mare_low_ti")
    mutation(profile)

    with pytest.raises(ProfileValidationError, match=message):
        study.resolve_profile(profile, expected_feedstock="lunar_mare_low_ti")


def test_constraint_threshold_overrides_validate_types() -> None:
    profile = _profile_copy("lunar_mare_low_ti")
    profile["constraints"]["furnace_T_max_C"] = "1300"

    with pytest.raises(ProfileValidationError, match="constraints.furnace_T_max_C must be numeric"):
        validate_profile(profile, expected_feedstock="lunar_mare_low_ti")


def test_constraint_target_species_must_be_non_empty_list() -> None:
    profile = _profile_copy("lunar_mare_low_ti")
    profile["constraints"]["target_species"] = []

    with pytest.raises(ProfileValidationError, match="constraints.target_species must be a non-empty list"):
        validate_profile(profile, expected_feedstock="lunar_mare_low_ti")


def test_unknown_constraint_key_raises_named_error() -> None:
    profile = _profile_copy("lunar_mare_low_ti")
    profile["constraints"]["mystery_threshold"] = 1.0

    with pytest.raises(ProfileValidationError, match="unknown constraints key"):
        validate_profile(profile, expected_feedstock="lunar_mare_low_ti")


def _profile_copy(feedstock: str) -> dict:
    path = DEFAULT_DATA_DIR / "optimize_profiles" / f"{feedstock}.yaml"
    return copy.deepcopy(yaml.safe_load(path.read_text()))
