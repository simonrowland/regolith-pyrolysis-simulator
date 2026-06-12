from __future__ import annotations

import copy
from dataclasses import replace
import json
import math
from pathlib import Path
import subprocess
import sys

import pytest

from simulator.config import load_config_bundle
from simulator.optimize.evalspec import (
    EvalSpec,
    cache_key,
    canonical_evalspec_json,
    canonical_feedstock_recipe_json,
    current_code_version,
    feedstock_recipe_digest,
)
from simulator.optimize.evaluate import EvaluationInputError, _build_eval_inputs
from simulator.optimize.physics import PhysicsConstraintSet, ThresholdSpec
from simulator.optimize.profiles import ProfileValidationError
from simulator.optimize.recipe import RecipePatch, RecipeSchema
from simulator.runner import PyrolysisRun


PINNED_EVALSPEC_JSON = (
    b'{"additives_kg":{"CaO":"1.500000000"},"backend_name":"stub",'
    b'"c5_enabled":false,"campaign":"C0","chemistry_kernel":{'
    b'"allow_builtin_fallback":false,"engine":"builtin",'
    b'"pressure_Pa":"0.001000000"},"code_version":"0.5.6",'
    b'"data_digests":{"feedstocks":"feedstock-digest",'
    b'"profile":"profile-digest","setpoints":"setpoints-digest",'
    b'"vapor_pressures":"vapor-digest"},"feedstock_id":"lunar_mare_low_ti",'
    b'"feedstock_recipe_digest":"feedstock-recipe-digest","fidelity":"fast",'
    b'"hours":24,"mass_kg":"1000.000000000","mre_max_voltage_V":"0.000000000",'
    b'"mre_target_species":"","profile_id":"oxygen-yield-v1",'
    b'"recipe_id":"recipe-id","runtime_campaign_overrides":{"C0":{'
    b'"hold_time_h":"1.000000000"}},"track":"pyrolysis"}'
)
PINNED_FEEDSTOCK_JSON = (
    b'[["Al2O3","13.500000000"],["FeO","16.500000000"],["SiO2","44.500000000"]]'
)


def _base_spec(**overrides: object) -> EvalSpec:
    data = {
        "recipe_id": "recipe-id",
        "feedstock_recipe_digest": "feedstock-recipe-digest",
        "feedstock_id": "lunar_mare_low_ti",
        "profile_id": "oxygen-yield-v1",
        "fidelity": "fast",
        "code_version": current_code_version(),
        "data_digests": {
            "setpoints": "setpoints-digest",
            "feedstocks": "feedstock-digest",
            "vapor_pressures": "vapor-digest",
            "profile": "profile-digest",
        },
        "chemistry_kernel": {
            "engine": "builtin",
            "allow_builtin_fallback": False,
            "pressure_Pa": 0.001,
        },
        "campaign": "C0",
        "hours": 24,
        "mass_kg": 1000.0,
        "additives_kg": {"CaO": 1.5},
        "track": "pyrolysis",
        "backend_name": "stub",
        "runtime_campaign_overrides": {"C0": {"hold_time_h": 1.0}},
    }
    data.update(overrides)
    return EvalSpec(**data)


def test_canonical_evalspec_json_and_cache_key_are_byte_stable_cross_run() -> None:
    spec = _base_spec()

    assert canonical_evalspec_json(spec) == PINNED_EVALSPEC_JSON
    assert cache_key(spec) == cache_key(_base_spec())

    code = """
import json
from simulator.optimize.evalspec import EvalSpec, cache_key, canonical_evalspec_json
spec = EvalSpec(
    recipe_id="recipe-id",
    feedstock_recipe_digest="feedstock-recipe-digest",
    feedstock_id="lunar_mare_low_ti",
    profile_id="oxygen-yield-v1",
    fidelity="fast",
    code_version="0.5.6",
    data_digests={
        "setpoints": "setpoints-digest",
        "feedstocks": "feedstock-digest",
        "vapor_pressures": "vapor-digest",
        "profile": "profile-digest",
    },
    chemistry_kernel={
        "engine": "builtin",
        "allow_builtin_fallback": False,
        "pressure_Pa": 0.001,
    },
    campaign="C0",
    hours=24,
    mass_kg=1000.0,
    additives_kg={"CaO": 1.5},
    track="pyrolysis",
    backend_name="stub",
    runtime_campaign_overrides={"C0": {"hold_time_h": 1.0}},
)
print(json.dumps({
    "canonical": canonical_evalspec_json(spec).decode("utf-8"),
    "key": cache_key(spec),
}, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        text=True,
        capture_output=True,
    )
    fresh = json.loads(completed.stdout)
    assert fresh["canonical"].encode("utf-8") == PINNED_EVALSPEC_JSON
    assert fresh["key"] == cache_key(spec)


def test_code_version_is_sourced_from_version_file() -> None:
    spec = _base_spec()

    assert current_code_version() == Path("VERSION").read_text(encoding="utf-8").strip()
    assert spec.code_version == current_code_version()
    assert b'"code_version":"' + current_code_version().encode("utf-8") + b'"' in (
        canonical_evalspec_json(spec)
    )


def test_feedstock_recipe_digest_is_byte_stable_and_keeps_species_labels() -> None:
    composition = {"SiO2": 44.5, "FeO": 16.5, "Al2O3": 13.5}

    assert canonical_feedstock_recipe_json(composition) == PINNED_FEEDSTOCK_JSON
    assert feedstock_recipe_digest(composition) == feedstock_recipe_digest(dict(composition))
    assert feedstock_recipe_digest({"SiO2": 45.0, "FeO": 18.0}) != (
        feedstock_recipe_digest({"Al2O3": 45.0, "MgO": 18.0})
    )


def test_editing_one_feedstock_composition_changes_only_its_digest() -> None:
    bundle = load_config_bundle()
    feedstocks = copy.deepcopy(bundle.feedstocks)
    feedstock_id = "lunar_mare_low_ti"
    edited = copy.deepcopy(feedstocks)
    edited[feedstock_id]["composition_wt_pct"]["SiO2"] += 0.25

    before = {
        key: feedstock_recipe_digest(value)
        for key, value in feedstocks.items()
    }
    after = {
        key: feedstock_recipe_digest(value)
        for key, value in edited.items()
    }
    changed = {key for key in before if before[key] != after[key]}

    assert changed == {feedstock_id}


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("recipe_id", "other-recipe"),
        ("feedstock_recipe_digest", "other-feedstock-recipe"),
        ("feedstock_id", "lunar_highlands"),
        ("profile_id", "other-profile"),
        ("fidelity", "accurate"),
        ("code_version", "0.0.0-determinant-mutant"),
        ("campaign", "C2A"),
        ("hours", 48),
        ("mass_kg", 500.0),
        ("additives_kg", {"CaO": 2.5}),
        ("track", "mre_baseline"),
        ("backend_name", "magmin"),
        ("c5_enabled", True),
        ("mre_max_voltage_V", 1.4),
        ("mre_target_species", "SiO2"),
        ("runtime_campaign_overrides", {"C2A": {"hold_time_h": 2.0}}),
        (
            "data_digests",
            {
                "setpoints": "changed",
                "feedstocks": "feedstock-digest",
                "vapor_pressures": "vapor-digest",
                "profile": "profile-digest",
            },
        ),
        (
            "chemistry_kernel",
            {
                "engine": "builtin",
                "allow_builtin_fallback": True,
                "pressure_Pa": 0.001,
            },
        ),
    ),
)
def test_each_determinant_changes_cache_key(field: str, value: object) -> None:
    assert cache_key(_base_spec(**{field: value})) != cache_key(_base_spec())


def test_target_spec_fields_split_cache_key_only_when_digest_present() -> None:
    legacy = _base_spec()
    explicit_empty = _base_spec(
        target_spec_id="",
        target_spec_digest="",
        target_maturity={},
    )
    targeted = _base_spec(
        target_spec_id="pc-glass-clear",
        target_spec_digest="target-digest",
        target_maturity={"mode": "campaign_hours", "campaign": "C2B", "hours": 24},
    )
    targeted_with_provenance = _base_spec(
        target_spec_id="pc-glass-clear",
        target_spec_digest="target-digest",
        target_maturity={"mode": "campaign_hours", "campaign": "C2B", "hours": 24},
        target_provenance={
            "composition_window": {
                "oxides": {
                    "Fe2O3": {
                        "tier": "clear_container",
                        "needs_experiment": True,
                        "min": 0.0,
                        "max": 1.0,
                    }
                }
            }
        },
    )

    assert canonical_evalspec_json(legacy) == canonical_evalspec_json(explicit_empty)
    assert b"target_spec_digest" not in canonical_evalspec_json(legacy)
    assert b"target_spec_digest" in canonical_evalspec_json(targeted)
    assert cache_key(targeted) != cache_key(legacy)
    assert canonical_evalspec_json(targeted_with_provenance) == canonical_evalspec_json(targeted)
    assert cache_key(targeted_with_provenance) == cache_key(targeted)


def test_mre_policy_fields_split_cache_keys() -> None:
    off = _base_spec(c5_enabled=False, mre_max_voltage_V=0.0, mre_target_species="")
    enabled = _base_spec(c5_enabled=True, mre_max_voltage_V=0.0, mre_target_species="")
    si_target = _base_spec(
        c5_enabled=True,
        mre_max_voltage_V=1.4,
        mre_target_species="SiO2",
    )
    ti_target = _base_spec(
        c5_enabled=True,
        mre_max_voltage_V=1.5,
        mre_target_species="TiO2",
    )

    assert len({cache_key(off), cache_key(enabled), cache_key(si_target), cache_key(ti_target)}) == 4


def test_build_eval_inputs_populates_mre_policy_from_profile_run_options() -> None:
    profile = {
        "profile_id": "mre-policy-profile",
        "profile_schema_version": "profile-schema-v1",
        "feedstock": "lunar_mare_low_ti",
        "objectives": [
            {
                "metric": "oxygen_kg",
                "sense": "maximize",
                "units": "kg",
                "weight": 1.0,
                "rationale": "test oxygen objective evidence",
            }
        ],
        "constraints": {"gates": ["delivered_stream_purity"]},
        "seed_recipes": [{"id": "seed", "source_campaign": "C0", "patch": {}}],
        "run": {
            "campaign": "C5",
            "hours": 1,
            "mass_kg": 1000.0,
            "backend_name": "stub",
            "c5_enabled": True,
            "mre_max_voltage_V": 1.4,
            "mre_target_species": "SiO2",
        },
        "fidelities": {"stub": {"backend_name": "stub"}},
    }

    spec, run_config = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        RecipeSchema(),
    )

    assert spec.c5_enabled is True
    assert spec.mre_max_voltage_V == pytest.approx(1.4)
    assert spec.mre_target_species == "SiO2"
    assert run_config.c5_enabled is True
    assert run_config.mre_max_voltage_V == pytest.approx(1.4)
    assert run_config.mre_target_species == "SiO2"


def test_build_eval_inputs_projects_c3_alkali_dose_into_evalspec_additives() -> None:
    profile = {
        "profile_id": "c3-dose-profile",
        "profile_schema_version": "profile-schema-v1",
        "feedstock": "lunar_mare_low_ti",
        "objectives": [
            {
                "metric": "oxygen_kg",
                "sense": "maximize",
                "units": "kg",
                "weight": 1.0,
                "rationale": "test oxygen objective evidence",
            }
        ],
        "constraints": {"gates": ["delivered_stream_purity"]},
        "seed_recipes": [{"id": "seed", "source_campaign": "C3_NA", "patch": {}}],
        "run": {
            "campaign": "C3_NA",
            "hours": 1,
            "mass_kg": 1000.0,
            "backend_name": "stub",
        },
        "fidelities": {"stub": {"backend_name": "stub"}},
    }
    schema = RecipeSchema()
    na_dose = ("campaigns", "C3", "alkali_dosing", "Na_kg")
    k_dose = ("campaigns", "C3", "alkali_dosing", "K_kg")

    undosed_spec, undosed_config = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )
    dosed_spec, dosed_config = _build_eval_inputs(
        RecipePatch({na_dose: 12.0, k_dose: 4.0}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )

    assert dict(undosed_spec.additives_kg) == {}
    assert dict(undosed_config.additives_kg) == {}
    assert cache_key(undosed_spec) == cache_key(replace(undosed_spec, additives_kg={}))
    assert dict(dosed_spec.additives_kg) == {"K": 4.0, "Na": 12.0}
    assert dict(dosed_config.additives_kg) == {"K": 4.0, "Na": 12.0}
    assert cache_key(dosed_spec) != cache_key(replace(dosed_spec, additives_kg={}))


def test_c2a_profile_window_schedules_measured_temperature_window() -> None:
    spec, run_config = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        _c2a_window_profile(1050.0, 1600.0, 24),
        RecipeSchema(),
    )

    overrides = run_config.runtime_campaign_overrides["C2A_continuous"]
    assert run_config.hours == 26
    assert spec.hours == 26
    assert overrides["thermal_window_preheat_hours"] == pytest.approx(2.0)
    assert overrides["thermal_window_ramp_C_per_hr"] == pytest.approx(
        (1600.0 - 1050.0) / 24.0
    )

    session = _force_builtin_run_from_config(run_config)._start_session()
    temperatures = [
        session.advance().snapshot.temperature_C
        for _ in range(run_config.hours)
    ]

    assert temperatures[0] == pytest.approx(625.0)
    assert temperatures[1] == pytest.approx(1050.0)
    assert temperatures[-1] == pytest.approx(1600.0)


def test_c2b_profile_window_schedules_measured_temperature_window() -> None:
    spec, run_config = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        _campaign_window_profile("C2B", 1320.0, 1480.0, 17),
        RecipeSchema(),
    )

    overrides = run_config.runtime_campaign_overrides["C2B"]
    assert run_config.hours == 20
    assert spec.hours == 20
    assert overrides["thermal_window_preheat_hours"] == pytest.approx(3.0)
    assert overrides["thermal_window_ramp_C_per_hr"] == pytest.approx(
        (1480.0 - 1320.0) / 17.0
    )
    assert overrides["max_hours"] == pytest.approx(20.0)

    session = _force_builtin_run_from_config(run_config)._start_session()
    temperatures = [
        session.advance().snapshot.temperature_C
        for _ in range(run_config.hours)
    ]

    assert temperatures[0] == pytest.approx(625.0)
    assert temperatures[1] == pytest.approx(1225.0)
    assert temperatures[2] == pytest.approx(1320.0)
    assert temperatures[-1] == pytest.approx(1480.0)
    assert max(temperatures) >= 1320.0


def test_c2b_profile_window_over_campaign_cap_fails_loud() -> None:
    with pytest.raises(ProfileValidationError, match=r"max_hold_hr.*FORCE_PROFILES=1"):
        _build_eval_inputs(
            RecipePatch({}),
            "lunar_mare_low_ti",
            "stub",
            _campaign_window_profile("C2B", 1320.0, 1480.0, 24),
            RecipeSchema(),
        )


def test_c2a_profile_window_splits_cache_key_from_cold_start() -> None:
    cold_spec, _ = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        _c2a_window_profile(None, None, 24),
        RecipeSchema(),
    )
    warm_spec, _ = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        _c2a_window_profile(1050.0, 1600.0, 24),
        RecipeSchema(),
    )

    assert cold_spec.runtime_campaign_overrides == {}
    assert warm_spec.runtime_campaign_overrides["C2A_continuous"][
        "thermal_window_low_C"
    ] == pytest.approx(1050.0)
    assert cache_key(cold_spec) != cache_key(warm_spec)


def test_c2a_profile_window_above_furnace_ceiling_fails_loud() -> None:
    constraints = PhysicsConstraintSet(
        furnace_T_max_C=ThresholdSpec(
            id="furnace_T_max_C",
            value=1300.0,
            units="degC",
            source="test",
            source_ref="tests/test_optimizer_evalspec.py",
        )
    )

    with pytest.raises(EvaluationInputError, match="exceeds furnace_T_max_C"):
        _build_eval_inputs(
            RecipePatch({}),
            "lunar_mare_low_ti",
            "stub",
            _c2a_window_profile(1400.0, 1450.0, 18),
            RecipeSchema(),
            constraints=constraints,
        )


def test_in_window_c2a_run_captures_na_product() -> None:
    _, run_config = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        _c2a_window_profile(1400.0, 1450.0, 18),
        RecipeSchema(),
    )
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign=run_config.campaign,
        hours=run_config.hours,
        mass_kg=run_config.mass_kg,
        backend_name=run_config.backend_name,
        runtime_campaign_overrides=run_config.runtime_campaign_overrides,
        force_builtin_vapor_pressure=True,
        allow_fallback_vapor=True,
    )
    session = run._start_session()
    result = run._run_session(session)

    assert result["status"] == "ok"
    assert session.simulator.product_ledger()["Na"] > 0.0


@pytest.mark.parametrize("bad_value", (math.nan, math.inf, -math.inf))
def test_cache_key_rejects_nan_and_infinity(bad_value: float) -> None:
    spec = _base_spec(chemistry_kernel={"allow_builtin_fallback": False, "x": bad_value})

    with pytest.raises(ValueError, match="NaN and infinity"):
        cache_key(spec)


def _c2a_window_profile(
    low_C: float | None,
    high_C: float | None,
    duration_h: int,
) -> dict[str, object]:
    return _campaign_window_profile(
        "C2A_continuous",
        low_C,
        high_C,
        duration_h,
        profile_id="c2a-thermal-window-test",
    )


def _campaign_window_profile(
    campaign: str,
    low_C: float | None,
    high_C: float | None,
    duration_h: int,
    *,
    profile_id: str | None = None,
) -> dict[str, object]:
    campaign_patch: dict[str, object] = (
        {"p_total_mbar_default": 10.0}
        if campaign == "C2A_continuous"
        else {}
    )
    if low_C is not None and high_C is not None:
        campaign_patch["temp_range_C"] = [low_C, high_C]
        if campaign == "C2A_continuous":
            campaign_patch["duration_h"] = duration_h
    return {
        "profile_id": profile_id or f"{campaign.lower()}-thermal-window-test",
        "profile_schema_version": "profile-schema-v1",
        "feedstock": "lunar_mare_low_ti",
        "objectives": [
            {
                "metric": "oxygen_kg",
                "sense": "maximize",
                "units": "kg",
                "weight": 1.0,
                "rationale": "test oxygen objective evidence",
            }
        ],
        "constraints": {"gates": ["delivered_stream_purity"]},
        "seed_recipes": [
            {
                "id": "seed",
                "source_campaign": campaign,
                "patch": {"campaigns": {campaign: campaign_patch}},
            }
        ],
        "run": {
            "campaign": campaign,
            "hours": duration_h,
            "mass_kg": 1000.0,
            "backend_name": "stub",
        },
        "fidelities": {"stub": {"backend_name": "stub"}},
    }


def _force_builtin_run_from_config(run_config) -> PyrolysisRun:
    return PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign=run_config.campaign,
        hours=run_config.hours,
        mass_kg=run_config.mass_kg,
        backend_name=run_config.backend_name,
        runtime_campaign_overrides=run_config.runtime_campaign_overrides,
        force_builtin_vapor_pressure=True,
        allow_fallback_vapor=True,
    )


def test_non_string_mapping_keys_raise() -> None:
    with pytest.raises(ValueError, match="data_digests keys"):
        _base_spec(data_digests={1: "digest"})

    with pytest.raises(ValueError, match="chemistry_kernel keys"):
        _base_spec(chemistry_kernel={1: "fallback"})

    with pytest.raises(ValueError, match="additives_kg keys"):
        _base_spec(additives_kg={1: 2.0})

    with pytest.raises(ValueError, match="species labels"):
        feedstock_recipe_digest({1: 45.0})


def test_missing_required_data_digest_raises() -> None:
    with pytest.raises(ValueError, match="data_digests missing required keys: setpoints"):
        _base_spec(
            data_digests={
                "feedstocks": "feedstock-digest",
                "vapor_pressures": "vapor-digest",
                "profile": "profile-digest",
            }
        )


def test_empty_required_data_digest_raises() -> None:
    with pytest.raises(ValueError, match=r"data_digests\['setpoints'\] must be non-empty"):
        _base_spec(
            data_digests={
                "setpoints": "",
                "feedstocks": "feedstock-digest",
                "vapor_pressures": "vapor-digest",
                "profile": "profile-digest",
            }
        )


@pytest.mark.parametrize("bad_value", (math.nan, math.inf, -math.inf))
def test_feedstock_recipe_digest_rejects_bad_numeric_values(bad_value: float) -> None:
    with pytest.raises(ValueError, match="NaN and infinity"):
        feedstock_recipe_digest({"SiO2": bad_value})
