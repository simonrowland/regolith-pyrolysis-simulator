from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from simulator.config import DEFAULT_DATA_DIR
from simulator.optimize.objective import composition_target_eval_metadata
from simulator.optimize import study
from simulator.optimize.physics import GATE_ORDER
from simulator.optimize.profiles import (
    KNOWN_OBJECTIVE_METRICS,
    ProfileValidationError,
    physics_constraints_from_profile,
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
            if objective.get("type", "legacy_metric") == "legacy_metric":
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


@pytest.mark.parametrize("gate", ("delivered_stream_purity", "knudsen_viscous"))
def test_runtime_loader_refuses_stale_melt_pool_gates(gate: str) -> None:
    profile = _composition_profile()
    profile["constraints"]["gates"].append(gate)

    with pytest.raises(ProfileValidationError) as excinfo:
        physics_constraints_from_profile(profile, source="stale-profile.yaml")

    message = str(excinfo.value)
    assert gate in message
    assert "residual_rump_at_stop" in message
    assert "FORCE_PROFILES=1" in message


def test_unknown_constraint_key_raises_named_error() -> None:
    profile = _profile_copy("lunar_mare_low_ti")
    profile["constraints"]["mystery_threshold"] = 1.0

    with pytest.raises(ProfileValidationError, match="unknown constraints key"):
        validate_profile(profile, expected_feedstock="lunar_mare_low_ti")


def test_c5_enabled_unknown_target_without_voltage_raises_named_error() -> None:
    profile = _profile_copy("lunar_mare_low_ti")
    profile["run"].update(
        {
            "c5_enabled": True,
            "mre_target_species": "Bogus",
            "mre_max_voltage_V": 0.0,
        }
    )

    with pytest.raises(ProfileValidationError, match="Bogus"):
        validate_profile(profile, expected_feedstock="lunar_mare_low_ti")


def test_composition_target_validates_and_resolves_fe_tier() -> None:
    profile = _composition_profile()

    validated = validate_profile(profile, expected_feedstock="lunar_mare_low_ti")

    objective = validated["objectives"][0]
    fe_row = objective["target"]["composition_window"]["oxides"][
        "Fe_total_as_Fe2O3_wt_pct"
    ]
    assert objective["type"] == "composition_target"
    assert objective["metric"] == "composition_target:pc-glass-clear-test"
    assert fe_row["tier"] == "clear_container"
    assert fe_row["min"] == pytest.approx(0.0)
    assert fe_row["max"] == pytest.approx(1.0)
    assert fe_row["strict"] is True
    assert fe_row["weight"] == pytest.approx(1.0)
    assert fe_row["needs_experiment"] is True
    assert "provenance" in fe_row


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda p: p["objectives"][0].update({"type": "mystery"}), "unknown objective type"),
        (
            lambda p: p["objectives"][0]["target"]["species_vector"].update(
                {"Fe": "free", "Si": "free", "Al": "free", "Ca": "free"}
            ),
            "all-free",
        ),
        (
            lambda p: p["objectives"][0]["target"]["composition_window"].update({"oxides": {}}),
            "at least one oxide or ratio row",
        ),
        (
            lambda p: p["objectives"][0]["target"]["composition_window"]["oxides"]["SiO2"].update(
                {"weight": 0.0}
            ),
            "weight must be positive",
        ),
        (
            lambda p: p["objectives"][0]["target"].update({"pool": "unknown_pool"}),
            "unknown pool id",
        ),
        (
            lambda p: p["objectives"][0]["target"]["composition_window"]["oxides"][
                "Fe_total_as_Fe2O3_wt_pct"
            ].update({"tier": "unknown_tier"}),
            "unknown tier",
        ),
        (
            lambda p: p["objectives"][0]["target"].update(
                {"score_weights": {"extraction": 0.0, "composition": 0.0}}
            ),
            "at least one positive branch",
        ),
        (
            lambda p: p["objectives"][0]["target"].update(
                {"score_weights": {"extraction": 1.0, "composition": 1.0}}
            ),
            "score_weight_sum_not_one",
        ),
        (
            lambda p: p["objectives"][0]["target"].update(
                {"score_weights": {"extraction": 0.25, "composition": 0.25}}
            ),
            "score_weight_sum_not_one",
        ),
        (
            lambda p: p["objectives"][0]["target"].update({"surprise": True}),
            "unknown objectives\\[0\\].target key",
        ),
        (
            lambda p: p["objectives"][0]["target"].update({"require_coating_gate": "false"}),
            "require_coating_gate must be bool",
        ),
        (
            lambda p: p["objectives"][0]["target"]["composition_window"].update(
                {"mode": "soft_distance", "exploratory": True}
            ),
            "soft_distance requires exploratory non-menu target",
        ),
    ],
)
def test_composition_target_degenerate_profiles_fail_loud(mutation, message: str) -> None:
    profile = _composition_profile()
    mutation(profile)

    with pytest.raises(ProfileValidationError, match=message):
        validate_profile(profile, expected_feedstock="lunar_mare_low_ti")


def test_composition_target_windowless_extraction_only_validates() -> None:
    profile = _composition_profile()
    target = profile["objectives"][0]["target"]
    target["species_vector"] = {"Fe": "extract", "Si": "free"}
    target["extraction"]["completeness_min"] = {"Fe": 0.5}
    target.pop("composition_window")
    target["score_weights"] = {"extraction": 1.0}

    validated = validate_profile(profile, expected_feedstock="lunar_mare_low_ti")
    target = validated["objectives"][0]["target"]

    assert "composition_window" not in target
    assert target["score_weights"]["extraction"] == pytest.approx(1.0)
    assert target["score_weights"]["composition"] == pytest.approx(0.0)


def test_composition_target_windowless_requires_extraction_only_split() -> None:
    profile = _composition_profile()
    target = profile["objectives"][0]["target"]
    target["species_vector"] = {"Fe": "extract", "Si": "free"}
    target["extraction"]["completeness_min"] = {"Fe": 0.5}
    target.pop("composition_window")
    target["score_weights"] = {"extraction": 0.5, "composition": 0.5}

    with pytest.raises(ProfileValidationError, match="window is empty"):
        validate_profile(profile, expected_feedstock="lunar_mare_low_ti")


def test_composition_target_to_window_requires_corresponding_row() -> None:
    valid = _composition_profile()
    target = valid["objectives"][0]["target"]
    target["species_vector"] = {"Fe": "to_window", "Si": "retain"}
    target["extraction"]["completeness_min"] = {}
    target["score_weights"] = {"extraction": 0.0, "composition": 1.0}
    target["composition_window"]["oxides"] = {
        "FeO_total": {"min": 0.0, "max": 0.5, "weight": 1.0}
    }

    validate_profile(valid, expected_feedstock="lunar_mare_low_ti")

    invalid = _composition_profile()
    target = invalid["objectives"][0]["target"]
    target["species_vector"] = {"Fe": "to_window", "Si": "retain"}
    target["extraction"]["completeness_min"] = {}
    target["score_weights"] = {"extraction": 0.0, "composition": 1.0}
    target["composition_window"]["oxides"] = {
        "SiO2": {"min": 45.0, "max": 75.0, "weight": 1.0}
    }

    with pytest.raises(ProfileValidationError, match="to_window requires"):
        validate_profile(invalid, expected_feedstock="lunar_mare_low_ti")


def test_composition_target_all_soft_requires_exploratory_field() -> None:
    profile = _composition_profile()
    for row in profile["objectives"][0]["target"]["composition_window"]["oxides"].values():
        row["strict"] = False

    with pytest.raises(ProfileValidationError, match="needs at least one strict row"):
        validate_profile(profile, expected_feedstock="lunar_mare_low_ti")

    exploratory = _composition_profile()
    exploratory["objectives"][0]["id"] = "glass-clear-explore"
    exploratory["objectives"][0]["metric"] = "composition_target:glass-clear-explore"
    window = exploratory["objectives"][0]["target"]["composition_window"]
    window["exploratory"] = True
    for row in window["oxides"].values():
        row["strict"] = False

    validated = validate_profile(exploratory, expected_feedstock="lunar_mare_low_ti")
    assert validated["objectives"][0]["target"]["composition_window"]["exploratory"] is True


def test_composition_target_ratio_rows_require_strict_species_companion() -> None:
    profile = _composition_profile()
    window = profile["objectives"][0]["target"]["composition_window"]
    window["oxides"] = {}
    window["ratios"] = [
        {
            "ratio": {
                "numerator": "CaO",
                "denominator": "Al2O3",
                "min": 0.45,
                "max": 0.75,
                "weight": 1.0,
            }
        }
    ]

    with pytest.raises(ProfileValidationError, match="strict per-species oxide band"):
        validate_profile(profile, expected_feedstock="lunar_mare_low_ti")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["objectives"][0]["target"]["extraction"].update(
            {"captured_pool": "captured_stage_3_silica"}
        ),
        lambda p: p["objectives"][0]["target"]["extraction"]["completeness_min"].update(
            {"Fe": 0.9}
        ),
        lambda p: p["objectives"][0]["target"]["composition_window"]["oxides"]["SiO2"].update(
            {"max": 70.0}
        ),
        lambda p: p["objectives"][0]["target"]["maturity"].update({"hours": 48}),
        lambda p: p["objectives"][0]["target"].update(
            {"score_weights": {"extraction": 0.25, "composition": 0.75}}
        ),
        lambda p: p["objectives"][0]["target"].update({"require_coating_gate": False}),
        lambda p: p["objectives"][0]["target"]["composition_window"]["oxides"]["SiO2"].update(
            {"strict": False}
        ),
        lambda p: p["objectives"][0]["target"]["composition_window"].update(
            {
                "ratios": [
                    {
                        "ratio": {
                            "numerator": "CaO",
                            "denominator": "Al2O3",
                            "min": 0.45,
                            "max": 0.75,
                            "weight": 1.0,
                        }
                    }
                ]
            }
        ),
        lambda p: p["objectives"][0]["target"]["composition_window"].update(
            {"exploratory": True}
        ),
    ],
)
def test_composition_target_digest_covers_full_target_spec(mutation) -> None:
    base = validate_profile(_composition_profile(), expected_feedstock="lunar_mare_low_ti")
    edited_raw = _composition_profile()
    mutation(edited_raw)
    edited = validate_profile(edited_raw, expected_feedstock="lunar_mare_low_ti")

    assert composition_target_eval_metadata(base)["target_spec_digest"] != (
        composition_target_eval_metadata(edited)["target_spec_digest"]
    )


def _profile_copy(feedstock: str) -> dict:
    path = DEFAULT_DATA_DIR / "optimize_profiles" / f"{feedstock}.yaml"
    return copy.deepcopy(yaml.safe_load(path.read_text()))


def _composition_profile() -> dict:
    profile = _profile_copy("lunar_mare_low_ti")
    profile["constraints"]["gates"] = [
        gate
        for gate in profile["constraints"]["gates"]
        if gate not in {"delivered_stream_purity", "knudsen_viscous"}
    ]
    profile["profile_id"] = "composition-target-profile-test"
    profile["objectives"] = [
        {
            "type": "composition_target",
            "id": "pc-glass-clear-test",
            "metric": "composition_target:pc-glass-clear-test",
            "sense": "maximize",
            "units": "score_0_1",
            "weight": 1.0,
            "rationale": "test composition target evidence",
            "target": {
                "pool": "residual_rump_at_stop",
                "species_vector": {
                    "Fe": "extract",
                    "Si": "retain",
                    "Al": "retain",
                    "Ca": "retain",
                },
                "extraction": {
                    "basis": "input_element_mol",
                    "captured_pool": "captured_products",
                    "credit_policy": {
                        "additives": "no_product_credit",
                        "vented": "no_product_credit",
                    },
                    "completeness_min": {"Fe": 0.5},
                },
                "composition_window": {
                    "pool": "residual_rump_at_stop",
                    "basis": "oxide_wt_pct",
                    "mode": "hard_window",
                    "oxides": {
                        "SiO2": {
                            "min": 45.0,
                            "max": 75.0,
                            "weight": 2.0,
                            "needs_experiment": True,
                        },
                        "Al2O3": {
                            "min": 5.0,
                            "max": 25.0,
                            "weight": 1.0,
                            "needs_experiment": True,
                        },
                        "CaO": {
                            "min": 5.0,
                            "max": 25.0,
                            "weight": 1.0,
                            "needs_experiment": True,
                        },
                        "Fe_total_as_Fe2O3_wt_pct": {
                            "tier": "clear_container",
                            "needs_experiment": True,
                        },
                    },
                },
                "maturity": {"mode": "campaign_hours", "campaign": "C2B", "hours": 24},
                "constraints": {
                    "coating_min_campaigns_to_resinter": "profile_default",
                    "furnace_T_max_C": "profile_or_study_constraint",
                },
                "score_weights": {"extraction": 0.5, "composition": 0.5},
            },
        }
    ]
    return profile
