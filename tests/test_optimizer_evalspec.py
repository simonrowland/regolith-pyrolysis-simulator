from __future__ import annotations

import copy
from dataclasses import fields, replace
import importlib.util
import json
import math
import pickle
from pathlib import Path
import subprocess
import sys

import pytest

from simulator.config import load_config_bundle
from simulator.corpus_version import current_corpus_version
from simulator.electrolysis import min_decomposition_voltage
from simulator.lab_schedule import (
    LAB_SCHEDULE_PRESSURE_FLOOR_MBAR,
    LabScheduleValidationError,
    interpolate_schedule_points,
    lab_schedule_digests,
    normalize_lab_schedule,
)
from simulator.optimize.evalspec import (
    DEFAULT_VAPOR_PRESSURE_FALLBACK_PROVIDER_ID,
    DEFAULT_VAPOR_PRESSURE_PROVIDER_ID,
    EvalSpec,
    PrefixEvalSpec,
    cache_key,
    canonical_evalspec_json,
    canonical_feedstock_recipe_json,
    current_code_version,
    feedstock_recipe_digest,
)
import simulator.optimize.evalspec as evalspec_module
import simulator.optimize.evaluate as evaluate_module
import simulator.melt_backend.vaporock as vaporock_module
from simulator.optimize.evaluate import EvaluationInputError, _build_eval_inputs
from simulator.optimize.physics import PhysicsConstraintSet, ThresholdSpec
from simulator.optimize.profiles import ProfileValidationError
from simulator.optimize.recipe import (
    C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_FLOOR,
    C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_PATH,
    C5_ALLOW_MRE_VOLTAGE_CAP_PATH,
    C4_HOLD_TEMP_C_PATH,
    RecipePatch,
    RecipeSchema,
    STAGE0_CARBON_REDUCTANT_KG_PATH,
    STAGE0_REDOX_OXIDANT_KG_PATH,
)
from simulator.campaigns import CampaignManager
from simulator.core import CampaignPhase
from simulator.runner import PyrolysisRun, RunnerError
from simulator.state import MeltState


PINNED_EVALSPEC_JSON = (
    b'{"additives_kg":{"CaO":"1.500000000"},"allow_fallback_vapor":false,'
    b'"allowlist_version":"allowlist-v9","backend_name":"stub",'
    b'"c5_enabled":false,"campaign":"C0","chemistry_kernel":{'
    b'"allow_builtin_fallback":false,"engine":"builtin",'
    b'"pressure_Pa":"0.001000000"},"code_version":"0.5.7",'
    b'"data_digests":{"corpus_version":"corpus-version-digest",'
    b'"feedstocks":"feedstock-digest","materials":"materials-digest",'
    b'"profile":"profile-digest","setpoints":"setpoints-digest",'
    b'"species_catalog":"species-catalog-digest","vapor_pressures":"vapor-digest"},'
    b'"feedstock_id":"lunar_mare_low_ti",'
    b'"feedstock_recipe_digest":"feedstock-recipe-digest","fidelity":"fast",'
    b'"force_builtin_vapor_pressure":false,"hours":24,'
    b'"mass_kg":"1000.000000000","mre_max_voltage_V":"0.000000000",'
    b'"mre_target_species":"","profile_id":"oxygen-yield-v1",'
    b'"recipe_id":"recipe-id","runtime_campaign_overrides":{'
    b'"C0":{"hold_time_h":"1.000000000"}},"track":"pyrolysis",'
    b'"vapor_pressure_provider_id":"builtin-vapor-pressure"}'
)
PINNED_FEEDSTOCK_JSON = (
    b'[["Al2O3","13.500000000"],["FeO","16.500000000"],["SiO2","44.500000000"]]'
)
STAGE_SIO_TARGET = (
    "campaigns",
    "C2A_staged",
    "stages",
    "sio_window",
    "target_C",
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
            "corpus_version": "corpus-version-digest",
            "setpoints": "setpoints-digest",
            "feedstocks": "feedstock-digest",
            "vapor_pressures": "vapor-digest",
            "materials": "materials-digest",
            "species_catalog": "species-catalog-digest",
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


def _prefix_spec(**overrides: object) -> PrefixEvalSpec:
    base = _base_spec()
    data = {
        field.name: getattr(base, field.name)
        for field in fields(EvalSpec)
    }
    data.update(overrides)
    return PrefixEvalSpec(
        **data,
        prefix_stage_ids=("C0", "C0B"),
        prefix_recipe_ids=("seed-c0", "seed-c0b"),
    )


def _mre_cap_profile(**run_overrides: object) -> dict[str, object]:
    run = {
        "campaign": "C5",
        "hours": 1,
        "mass_kg": 1000.0,
        "backend_name": "stub",
    }
    run.update(run_overrides)
    return {
        "profile_id": "mre-cap-profile",
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
        "run": run,
        "fidelities": {"stub": {"backend_name": "stub"}},
    }


def test_canonical_evalspec_json_and_cache_key_are_byte_stable_cross_run() -> None:
    spec = _base_spec()
    explicit_empty = _base_spec(
        lab_alpha_digest="",
        geometry_digest="",
        effective_exposed_area_m2=None,
        area_basis="",
        oxide_vapor_ceiling_digest="",
        sink_channel_evidence_digests={},
    )

    assert canonical_evalspec_json(spec) == PINNED_EVALSPEC_JSON
    assert canonical_evalspec_json(explicit_empty) == PINNED_EVALSPEC_JSON
    assert cache_key(spec) == cache_key(_base_spec())
    assert cache_key(explicit_empty) == cache_key(spec)

    code = """
import json
from simulator.optimize.evalspec import EvalSpec, cache_key, canonical_evalspec_json
spec = EvalSpec(
    recipe_id="recipe-id",
    feedstock_recipe_digest="feedstock-recipe-digest",
    feedstock_id="lunar_mare_low_ti",
    profile_id="oxygen-yield-v1",
    fidelity="fast",
    code_version="0.5.7",
    data_digests={
        "corpus_version": "corpus-version-digest",
        "setpoints": "setpoints-digest",
        "feedstocks": "feedstock-digest",
        "vapor_pressures": "vapor-digest",
        "materials": "materials-digest",
        "species_catalog": "species-catalog-digest",
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


def test_evalspec_cache_key_changes_when_allowlist_version_changes() -> None:
    assert cache_key(_base_spec(allowlist_version="allowlist-old")) != cache_key(
        _base_spec(allowlist_version="allowlist-new")
    )


def test_build_eval_inputs_keys_schema_allowlist_version_in_production_path() -> None:
    profile = _mre_cap_profile(campaign="C0")
    old_schema = RecipeSchema(allowlist_version="allowlist-old")
    new_schema = RecipeSchema(allowlist_version="allowlist-new")
    assert old_schema.recipe_schema_version == new_schema.recipe_schema_version
    assert old_schema.allowlist == new_schema.allowlist

    old_spec, _ = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        old_schema,
    )
    new_spec, _ = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        new_schema,
    )

    assert old_spec.allowlist_version == "allowlist-old"
    assert new_spec.allowlist_version == "allowlist-new"
    assert old_spec.recipe_id != new_spec.recipe_id
    assert cache_key(old_spec) != cache_key(new_spec)


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


def test_data_corpus_digests_change_evalspec_cache_key() -> None:
    profile = _mre_cap_profile()
    spec, _ = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        RecipeSchema(),
    )

    assert spec.data_digests["corpus_version"] == current_corpus_version()
    assert spec.data_digests["materials"]
    assert spec.data_digests["species_catalog"]

    corpus_changed = replace(
        spec,
        data_digests={**spec.data_digests, "corpus_version": "changed-corpus-version"},
    )
    materials_changed = replace(
        spec,
        data_digests={**spec.data_digests, "materials": "changed-materials"},
    )
    species_catalog_changed = replace(
        spec,
        data_digests={
            **spec.data_digests,
            "species_catalog": "changed-species-catalog",
        },
    )

    assert cache_key(corpus_changed) != cache_key(spec)
    assert cache_key(materials_changed) != cache_key(spec)
    assert cache_key(species_catalog_changed) != cache_key(spec)


def test_evalspec_reduce_rebuild_tolerates_legacy_digest_scope() -> None:
    spec = _base_spec()
    rebuild, args = spec.__reduce__()
    legacy_args = list(args)
    legacy_args[6] = {
        key: value
        for key, value in spec.data_digests.items()
        if key not in {"materials", "species_catalog"}
    }

    restored = rebuild(*legacy_args)

    assert restored.data_digests["materials"] == "legacy-missing-materials-digest"
    assert (
        restored.data_digests["species_catalog"]
        == "legacy-missing-species-catalog-digest"
    )

    # A FULL 6-key payload must round-trip UNCHANGED: fresh specs never acquire
    # sentinels — the legacy scope only patches a 4-key legacy map. Without this
    # the determinant could be silently defeated for live specs.
    restored_full = rebuild(*args)
    assert restored_full.data_digests == spec.data_digests
    assert not any(
        str(v).startswith("legacy-missing")
        for v in restored_full.data_digests.values()
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("recipe_id", "other-recipe"),
        ("feedstock_recipe_digest", "other-feedstock-recipe"),
        ("feedstock_id", "lunar_highlands"),
        ("profile_id", "other-profile"),
        ("fidelity", "accurate"),
        ("code_version", "0.0.0-determinant-mutant"),
        ("allowlist_version", "allowlist-mutant"),
        ("campaign", "C2A"),
        ("hours", 48),
        ("mass_kg", 500.0),
        ("additives_kg", {"CaO": 2.5}),
        ("track", "mre_baseline"),
        ("backend_name", "magmin"),
        ("c5_enabled", True),
        ("stop_at_stage0_exit", True),
        ("mre_max_voltage_V", 1.45),
        ("mre_target_species", "SiO2"),
        ("stage0_redox_oxidant_kg", 12.5),
        ("stage0_carbon_reductant_kg", 7.25),
        ("runtime_campaign_overrides", {"C2A": {"hold_time_h": 2.0}}),
        ("lab_alpha_digest", "robinot-lab-alpha-v1"),
        ("geometry_digest", "robinot-geometry-v1"),
        ("effective_exposed_area_m2", 0.000314),
        ("area_basis", "gram_lab_exposed_melt"),
        ("oxide_vapor_ceiling_digest", "oxide-vapor-ceiling-v1"),
        (
            "sink_channel_evidence_digests",
            {
                "plume_oxidation_diagnostic": "plume-evidence-v1",
                "deposit_gettering_diagnostic": "deposit-evidence-v1",
            },
        ),
        (
            "data_digests",
            {
                "setpoints": "changed",
                "feedstocks": "feedstock-digest",
                "materials": "materials-digest",
                "vapor_pressures": "vapor-digest",
                "species_catalog": "species-catalog-digest",
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
        ("vapor_pressure_provider_code_fingerprint", "provider-source-sha256:changed"),
    ),
)
def test_each_determinant_changes_cache_key(field: str, value: object) -> None:
    assert cache_key(_base_spec(**{field: value})) != cache_key(_base_spec())


def test_stage0_exit_stop_partitions_cache_key_without_default_key_churn() -> None:
    full_run = _base_spec(stop_at_stage0_exit=False)
    stage0_run = _base_spec(stop_at_stage0_exit=True)

    assert cache_key(stage0_run) != cache_key(full_run)
    assert b"stop_at_stage0_exit" not in canonical_evalspec_json(full_run)
    assert b'"stop_at_stage0_exit":true' in canonical_evalspec_json(stage0_run)


def test_stage0_exit_stop_survives_evalspec_reduce_paths() -> None:
    for spec in (
        _base_spec(stop_at_stage0_exit=True),
        _prefix_spec(stop_at_stage0_exit=True),
    ):
        restored = pickle.loads(pickle.dumps(spec))
        assert restored.stop_at_stage0_exit is True


def test_pre_redox_evalspec_reduce_payloads_get_zero_dose_defaults() -> None:
    _, args = _base_spec(
        stage0_redox_oxidant_kg=1.0,
        stage0_carbon_reductant_kg=2.0,
        stop_at_stage0_exit=True,
    ).__reduce__()
    old_args = args[:16] + args[18:-2]
    old_args_with_stop = old_args + (True,)

    restored = evalspec_module._rebuild_eval_spec(*old_args)
    restored_with_stop = evalspec_module._rebuild_eval_spec(*old_args_with_stop)

    assert restored.stage0_redox_oxidant_kg == 0.0
    assert restored.stage0_carbon_reductant_kg == 0.0
    assert restored_with_stop.stop_at_stage0_exit is True
    assert restored_with_stop.stage0_redox_oxidant_kg == 0.0
    assert restored_with_stop.stage0_carbon_reductant_kg == 0.0


def test_pre_redox_prefix_evalspec_reduce_payloads_get_zero_dose_defaults() -> None:
    _, args = _prefix_spec(
        stage0_redox_oxidant_kg=1.0,
        stage0_carbon_reductant_kg=2.0,
        stop_at_stage0_exit=True,
    ).__reduce__()
    old_args = args[:16] + args[18:-2]
    old_args_with_stop = old_args + (True,)

    restored = evalspec_module._rebuild_prefix_eval_spec(*old_args)
    restored_with_stop = evalspec_module._rebuild_prefix_eval_spec(
        *old_args_with_stop
    )

    assert restored.stage0_redox_oxidant_kg == 0.0
    assert restored.stage0_carbon_reductant_kg == 0.0
    assert restored.prefix_stage_ids == ("C0", "C0B")
    assert restored_with_stop.stop_at_stage0_exit is True
    assert restored_with_stop.stage0_redox_oxidant_kg == 0.0
    assert restored_with_stop.stage0_carbon_reductant_kg == 0.0


def test_old_evalspec_reduce_payloads_default_stage0_exit_stop_false() -> None:
    for spec in (
        _base_spec(stop_at_stage0_exit=False),
        _prefix_spec(stop_at_stage0_exit=False),
    ):
        _, new_args = spec.__reduce__()
        old_args = new_args[:-2]
        restored = type(spec)(*old_args)

        assert restored.stop_at_stage0_exit is False
        assert restored.allowlist_version == spec.allowlist_version
        for field in fields(type(spec)):
            if field.name in {"allowlist_version", "stop_at_stage0_exit"}:
                continue
            assert getattr(restored, field.name) == getattr(spec, field.name)


def test_fallback_provider_id_is_not_keyed_when_fallback_disabled() -> None:
    first = _base_spec(
        allow_fallback_vapor=False,
        vapor_pressure_fallback_provider_id="fallback-a",
    )
    second = _base_spec(
        allow_fallback_vapor=False,
        vapor_pressure_fallback_provider_id="fallback-b",
    )
    enabled_first = _base_spec(
        allow_fallback_vapor=True,
        vapor_pressure_fallback_provider_id="fallback-a",
    )
    enabled_second = _base_spec(
        allow_fallback_vapor=True,
        vapor_pressure_fallback_provider_id="fallback-b",
    )

    assert cache_key(first) == cache_key(second)
    assert b"vapor_pressure_fallback_provider_id" not in canonical_evalspec_json(first)
    assert cache_key(enabled_first) != cache_key(enabled_second)


def test_provider_code_fingerprint_splits_cache_key() -> None:
    first = _base_spec(vapor_pressure_provider_code_fingerprint="source-sha256:a")
    second = _base_spec(vapor_pressure_provider_code_fingerprint="source-sha256:b")

    assert cache_key(first) != cache_key(second)
    assert b"source-sha256:a" in canonical_evalspec_json(first)


def test_nested_vapor_fallback_flag_is_not_dual_keyed() -> None:
    base = _base_spec()
    nested_only = _base_spec(
        chemistry_kernel={
            "engine": "builtin",
            "allow_builtin_fallback": False,
            "pressure_Pa": 0.001,
            "allow_fallback_vapor": True,
        },
        allow_fallback_vapor=False,
    )

    assert cache_key(nested_only) == cache_key(base)
    assert b'"allow_fallback_vapor":false' in canonical_evalspec_json(nested_only)
    assert b'"chemistry_kernel":{"allow_fallback_vapor"' not in canonical_evalspec_json(
        nested_only
    )


def test_lab_overlay_scope_serializes_deterministically_and_only_when_non_empty() -> None:
    first = _base_spec(
        lab_alpha_digest="robinot-alpha-v1",
        geometry_digest="robinot-geometry-v1",
        effective_exposed_area_m2=0.000314,
        area_basis="gram_lab_exposed_melt",
        oxide_vapor_ceiling_digest="oxide-ceiling-v1",
        sink_channel_evidence_digests={
            "plume_oxidation_diagnostic": "plume-evidence-v1",
            "deposit_gettering_diagnostic": "deposit-evidence-v1",
        },
    )
    second = _base_spec(
        lab_alpha_digest="robinot-alpha-v1",
        geometry_digest="robinot-geometry-v1",
        effective_exposed_area_m2=0.000314,
        area_basis="gram_lab_exposed_melt",
        oxide_vapor_ceiling_digest="oxide-ceiling-v1",
        sink_channel_evidence_digests={
            "deposit_gettering_diagnostic": "deposit-evidence-v1",
            "plume_oxidation_diagnostic": "plume-evidence-v1",
        },
    )

    payload = json.loads(canonical_evalspec_json(first).decode("utf-8"))

    assert payload["lab_alpha_digest"] == "robinot-alpha-v1"
    assert payload["geometry_digest"] == "robinot-geometry-v1"
    assert payload["effective_exposed_area_m2"] == "0.000314000"
    assert payload["area_basis"] == "gram_lab_exposed_melt"
    assert payload["oxide_vapor_ceiling_digest"] == "oxide-ceiling-v1"
    assert payload["sink_channel_evidence_digests"] == {
        "deposit_gettering_diagnostic": "deposit-evidence-v1",
        "plume_oxidation_diagnostic": "plume-evidence-v1",
    }
    assert canonical_evalspec_json(first) == canonical_evalspec_json(second)
    assert cache_key(first) == cache_key(second)
    assert cache_key(first) != cache_key(_base_spec())


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
        mre_max_voltage_V=1.45,
        mre_target_species="SiO2",
    )
    ti_target = _base_spec(
        c5_enabled=True,
        mre_max_voltage_V=1.70,
        mre_target_species="TiO2",
    )

    assert len({cache_key(off), cache_key(enabled), cache_key(si_target), cache_key(ti_target)}) == 4


def test_stage0_redox_dose_defaults_do_not_churn_canonical_evalspec() -> None:
    assert canonical_evalspec_json(_base_spec()) == PINNED_EVALSPEC_JSON
    assert b"stage0_redox_oxidant_kg" not in PINNED_EVALSPEC_JSON
    assert b"stage0_carbon_reductant_kg" not in PINNED_EVALSPEC_JSON


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
            "mre_max_voltage_V": 1.45,
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
    assert spec.mre_max_voltage_V == pytest.approx(1.45)
    assert spec.mre_target_species == "SiO2"
    assert run_config.c5_enabled is True
    assert run_config.mre_max_voltage_V == pytest.approx(1.45)
    assert run_config.mre_target_species == "SiO2"


def test_build_eval_inputs_mre_cap_zero_is_default_no_mre_cache_neutral() -> None:
    profile = _mre_cap_profile()
    schema = RecipeSchema()
    default_spec, default_run_config = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )
    cap_zero_spec, cap_zero_run_config = _build_eval_inputs(
        RecipePatch({C5_ALLOW_MRE_VOLTAGE_CAP_PATH: 0.0}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )

    assert default_spec.c5_enabled is False
    assert default_spec.mre_max_voltage_V == pytest.approx(0.0)
    assert default_spec.mre_target_species == ""
    assert default_run_config.c5_enabled is False
    assert default_run_config.mre_max_voltage_V == pytest.approx(0.0)
    assert cap_zero_spec.c5_enabled is False
    assert cap_zero_spec.mre_max_voltage_V == pytest.approx(0.0)
    assert cap_zero_spec.mre_target_species == ""
    assert cap_zero_run_config.c5_enabled is False
    assert cap_zero_run_config.mre_max_voltage_V == pytest.approx(0.0)
    assert canonical_evalspec_json(cap_zero_spec) == canonical_evalspec_json(default_spec)
    assert cache_key(cap_zero_spec) == cache_key(default_spec)
    # cap=0 must strip to a cap-absent recipe_id (golden-neutral default).
    assert cap_zero_spec.recipe_id == default_spec.recipe_id


def test_build_eval_inputs_mre_cap_below_min_rung_is_no_mre_cache_neutral() -> None:
    profile = _mre_cap_profile()
    schema = RecipeSchema()
    min_rung = min_decomposition_voltage()
    below_min = min_rung / 2.0
    just_below_min = math.nextafter(min_rung, 0.0)
    default_spec, _ = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )
    cap_zero_spec, _ = _build_eval_inputs(
        RecipePatch({C5_ALLOW_MRE_VOLTAGE_CAP_PATH: 0.0}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )
    below_min_spec, below_min_run_config = _build_eval_inputs(
        RecipePatch({C5_ALLOW_MRE_VOLTAGE_CAP_PATH: below_min}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )
    just_below_spec, just_below_run_config = _build_eval_inputs(
        RecipePatch({C5_ALLOW_MRE_VOLTAGE_CAP_PATH: just_below_min}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )
    min_spec, min_run_config = _build_eval_inputs(
        RecipePatch({C5_ALLOW_MRE_VOLTAGE_CAP_PATH: min_rung}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )

    assert below_min_spec.c5_enabled is False
    assert below_min_spec.mre_max_voltage_V == pytest.approx(0.0)
    assert below_min_run_config.c5_enabled is False
    assert below_min_run_config.mre_max_voltage_V == pytest.approx(0.0)
    assert just_below_spec.c5_enabled is False
    assert just_below_spec.mre_max_voltage_V == pytest.approx(0.0)
    assert just_below_run_config.c5_enabled is False
    assert just_below_run_config.mre_max_voltage_V == pytest.approx(0.0)
    assert {
        default_spec.recipe_id,
        cap_zero_spec.recipe_id,
        below_min_spec.recipe_id,
        just_below_spec.recipe_id,
    } == {default_spec.recipe_id}
    assert {
        cache_key(default_spec),
        cache_key(cap_zero_spec),
        cache_key(below_min_spec),
        cache_key(just_below_spec),
    } == {cache_key(default_spec)}

    assert min_spec.c5_enabled is True
    assert min_spec.mre_max_voltage_V == pytest.approx(min_rung)
    assert min_run_config.c5_enabled is True
    assert min_run_config.mre_max_voltage_V == pytest.approx(min_rung)
    assert min_spec.recipe_id != default_spec.recipe_id
    assert cache_key(min_spec) != cache_key(default_spec)


def test_build_eval_inputs_mre_cap_int_and_float_share_recipe_id() -> None:
    """An int cap (1) and float cap (1.0) run identically and MUST share one
    canonical recipe_id / cache key (no float-vs-int cache fragmentation)."""
    profile = _mre_cap_profile()
    schema = RecipeSchema()
    cap_int_spec, _ = _build_eval_inputs(
        RecipePatch({C5_ALLOW_MRE_VOLTAGE_CAP_PATH: 1}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )
    cap_float_spec, _ = _build_eval_inputs(
        RecipePatch({C5_ALLOW_MRE_VOLTAGE_CAP_PATH: 1.0}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )
    assert cap_int_spec.recipe_id == cap_float_spec.recipe_id
    assert cache_key(cap_int_spec) == cache_key(cap_float_spec)


def test_build_eval_inputs_mre_cap_positive_enables_c5_and_partitions_cache() -> None:
    profile = _mre_cap_profile(c5_enabled=False, mre_target_species="SiO2")
    schema = RecipeSchema()
    default_spec, _ = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )
    cap_145_spec, cap_145_run_config = _build_eval_inputs(
        RecipePatch({C5_ALLOW_MRE_VOLTAGE_CAP_PATH: 1.45}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )
    cap_16_spec, cap_16_run_config = _build_eval_inputs(
        RecipePatch({C5_ALLOW_MRE_VOLTAGE_CAP_PATH: 1.6}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )

    assert cap_145_spec.c5_enabled is True
    assert cap_145_spec.mre_max_voltage_V == pytest.approx(1.45)
    assert cap_145_spec.mre_target_species == ""
    assert cap_145_run_config.c5_enabled is True
    assert cap_145_run_config.mre_max_voltage_V == pytest.approx(1.45)
    assert cap_145_run_config.mre_target_species == ""
    assert cap_16_run_config.c5_enabled is True
    assert cap_16_run_config.mre_max_voltage_V == pytest.approx(1.6)
    assert len({cache_key(default_spec), cache_key(cap_145_spec), cache_key(cap_16_spec)}) == 3


def test_build_eval_inputs_c2a_staged_stage_knob_partitions_cache_and_schedule() -> None:
    profile = _mre_cap_profile(campaign="C2A_staged", hours=9)
    schema = RecipeSchema()
    default_spec, default_config = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )
    staged_spec, staged_config = _build_eval_inputs(
        RecipePatch({STAGE_SIO_TARGET: 1585.0}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )

    assert cache_key(staged_spec) != cache_key(default_spec)
    default_stages = default_config.setpoints["campaigns"]["C2A_staged"]["stages"]
    staged_cfg = staged_config.setpoints["campaigns"]["C2A_staged"]
    staged_stages = staged_cfg["stages"]
    assert default_stages[1]["target_C"] == pytest.approx(1600.0)
    assert staged_stages[1]["name"] == "sio_window"
    assert staged_stages[1]["target_C"] == pytest.approx(1585.0)
    assert staged_cfg["max_hold_hr"] == 9

    target, ramp = CampaignManager(staged_config.setpoints).get_temp_target(
        CampaignPhase.C2A_STAGED,
        4,
        MeltState(),
    )
    assert target == pytest.approx(1585.0)
    assert ramp == pytest.approx(175.0)


def test_build_eval_inputs_c2a_staged_depletion_zero_is_cache_neutral() -> None:
    profile = _mre_cap_profile(campaign="C2A_staged", hours=9)
    schema = RecipeSchema()
    default_spec, default_config = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )
    zero_spec, zero_config = _build_eval_inputs(
        RecipePatch({C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_PATH: 0.0}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )

    assert zero_spec.recipe_id == default_spec.recipe_id
    assert canonical_evalspec_json(zero_spec) == canonical_evalspec_json(default_spec)
    assert cache_key(zero_spec) == cache_key(default_spec)
    assert "C2A_staged" not in default_config.runtime_campaign_overrides
    assert "C2A_staged" not in zero_config.runtime_campaign_overrides


def test_build_eval_inputs_c2a_staged_depletion_floor_partitions_cache_and_runtime() -> None:
    profile = _mre_cap_profile(campaign="C2A_staged", hours=9)
    schema = RecipeSchema()
    default_spec, _ = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )
    subfloor_spec, subfloor_config = _build_eval_inputs(
        RecipePatch({C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_PATH: 0.005}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )
    floor_spec, floor_config = _build_eval_inputs(
        RecipePatch(
            {
                C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_PATH: (
                    C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_FLOOR
                )
            }
        ),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )
    quarter_spec, quarter_config = _build_eval_inputs(
        RecipePatch({C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_PATH: 0.25}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )

    assert subfloor_spec.recipe_id == floor_spec.recipe_id
    assert cache_key(subfloor_spec) == cache_key(floor_spec)
    assert subfloor_config.runtime_campaign_overrides["C2A_staged"][
        "depletion_flux_decay_fraction"
    ] == pytest.approx(C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_FLOOR)
    assert floor_config.runtime_campaign_overrides["C2A_staged"][
        "depletion_flux_decay_fraction"
    ] == pytest.approx(C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_FLOOR)
    assert quarter_config.runtime_campaign_overrides["C2A_staged"][
        "depletion_flux_decay_fraction"
    ] == pytest.approx(0.25)
    assert subfloor_spec.recipe_id != default_spec.recipe_id
    assert quarter_spec.recipe_id != subfloor_spec.recipe_id


def test_build_eval_inputs_c4_hold_temp_knob_partitions_cache_and_runtime() -> None:
    profile = _mre_cap_profile(campaign="C4", hours=1)
    schema = RecipeSchema()
    default_spec, _ = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )
    hold_spec, hold_config = _build_eval_inputs(
        RecipePatch({C4_HOLD_TEMP_C_PATH: 1600.0}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )

    assert cache_key(hold_spec) != cache_key(default_spec)
    assert hold_spec.recipe_id != default_spec.recipe_id
    assert hold_config.setpoints["campaigns"]["C4"]["temp_range_C"] == [
        1580,
        1670,
    ]
    assert hold_config.runtime_campaign_overrides["C4"]["hold_temp_C"] == pytest.approx(
        1600.0
    )

    session = _force_builtin_run_from_config(hold_config)._start_session()
    target, ramp = session.simulator.campaign_mgr.get_temp_target(
        session.simulator.melt.campaign,
        0,
        session.simulator.melt,
    )
    assert target == pytest.approx(1600.0)
    assert ramp == pytest.approx(10.0)


def test_build_eval_inputs_c4_default_hold_temp_is_cache_neutral() -> None:
    profile = _mre_cap_profile(campaign="C4", hours=1)
    schema = RecipeSchema()
    default_spec, default_config = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )
    explicit_default_spec, explicit_default_config = _build_eval_inputs(
        RecipePatch({C4_HOLD_TEMP_C_PATH: 1670.0}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )

    assert explicit_default_spec.recipe_id == default_spec.recipe_id
    assert canonical_evalspec_json(explicit_default_spec) == canonical_evalspec_json(
        default_spec
    )
    assert cache_key(explicit_default_spec) == cache_key(default_spec)
    assert "C4" not in default_config.runtime_campaign_overrides
    assert "C4" not in explicit_default_config.runtime_campaign_overrides


def test_c4_default_hold_temp_anchor_matches_runtime_fallback() -> None:
    # The C4 hold_temp_C absence-normalization treats hold_temp_C ==
    # temp_range_C[-1] as the no-op default (dropped from recipe_id, no runtime
    # override injected). That is only golden-neutral because the C4 runtime, with
    # no hold_temp_C override, falls back to c4_max_temp_C — whose default equals
    # temp_range_C[-1]. The two defaults are coupled BY VALUE, not by shared code,
    # so pin the invariant here: a change to either side (setpoints temp_range_C
    # or the c4_max_temp_C / DEFAULT_C4_HOLD_TEMP_C hardcode) is caught in CI
    # instead of silently making the "default" value de-tune the knob.
    bundle = load_config_bundle()
    anchor = evaluate_module._c4_default_hold_temp_C(bundle.setpoints)
    assert anchor == pytest.approx(evaluate_module.DEFAULT_C4_HOLD_TEMP_C)
    assert anchor == pytest.approx(CampaignManager(bundle.setpoints).c4_max_temp_C)


def test_run_options_with_c4_hold_temp_conflict_raises() -> None:
    # The conflict guard fires when a pre-existing C4 hold_temp_C runtime override
    # disagrees with the patched value (_run_options_with_c4_hold_temp).
    run_options = {"runtime_campaign_overrides": {"C4": {"hold_temp_C": 1600.0}}}
    with pytest.raises(EvaluationInputError, match="hold_temp_C conflicts"):
        evaluate_module._run_options_with_c4_hold_temp(
            run_options,
            RecipePatch({C4_HOLD_TEMP_C_PATH: 1620.0}),
            c4_default_hold_temp_C=1670.0,
        )


def test_run_options_with_c4_hold_temp_matching_override_is_idempotent() -> None:
    # A pre-existing override equal to the patched value must NOT raise; the
    # value is preserved and the caller's mapping is not mutated.
    run_options = {"runtime_campaign_overrides": {"C4": {"hold_temp_C": 1600.0}}}
    merged = evaluate_module._run_options_with_c4_hold_temp(
        run_options,
        RecipePatch({C4_HOLD_TEMP_C_PATH: 1600.0}),
        c4_default_hold_temp_C=1670.0,
    )
    assert merged["runtime_campaign_overrides"]["C4"]["hold_temp_C"] == pytest.approx(
        1600.0
    )
    # caller's run_options dict is untouched (deep-copied internally)
    assert run_options["runtime_campaign_overrides"]["C4"]["hold_temp_C"] == 1600.0



def test_vaporock_eval_provider_probe_does_not_cache_negative(monkeypatch):
    probes = [False, True, False]

    def fake_runtime_available():
        return probes.pop(0)

    monkeypatch.setattr(
        vaporock_module,
        "vaporock_runtime_available",
        fake_runtime_available,
    )
    evaluate_module._vaporock_available.cache_clear()
    try:
        first = evaluate_module._vaporock_available()
        second = evaluate_module._vaporock_available()
        third = evaluate_module._vaporock_available()
    finally:
        evaluate_module._vaporock_available.cache_clear()

    assert first is False
    assert second is True
    assert third is True
    assert probes == [False]


def test_build_eval_inputs_keys_effective_vapor_provider_by_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = {
        "profile_id": "vapor-provider-profile",
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
            "campaign": "C0",
            "hours": 1,
            "mass_kg": 1000.0,
            "backend_name": "stub",
            "allow_fallback_vapor": True,
        },
        "fidelities": {"stub": {"backend_name": "stub"}},
    }

    monkeypatch.setattr(evaluate_module, "_vaporock_available", lambda: True)
    available_spec, _ = evaluate_module._build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        RecipeSchema(),
    )
    monkeypatch.setattr(evaluate_module, "_vaporock_available", lambda: False)
    unavailable_spec, _ = evaluate_module._build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        RecipeSchema(),
    )

    assert available_spec.vapor_pressure_provider_id == DEFAULT_VAPOR_PRESSURE_PROVIDER_ID
    assert unavailable_spec.vapor_pressure_provider_id == (
        DEFAULT_VAPOR_PRESSURE_FALLBACK_PROVIDER_ID
    )
    assert available_spec.allow_fallback_vapor is True
    assert unavailable_spec.allow_fallback_vapor is True
    assert cache_key(available_spec) == cache_key(unavailable_spec)


def test_build_eval_inputs_vaporock_import_visible_init_failure_keys_builtin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = {
        "profile_id": "vapor-provider-init-fail-profile",
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
            "campaign": "C0",
            "hours": 1,
            "mass_kg": 1000.0,
            "backend_name": "stub",
            "allow_fallback_vapor": True,
        },
        "fidelities": {"stub": {"backend_name": "stub"}},
    }
    init_calls = []

    def fake_find_spec(name: str) -> object | None:
        if name in {"vaporock", "thermoengine"}:
            return object()
        return None

    def fake_initialize(self, config):
        init_calls.append(dict(config))
        self._available = False
        self._last_error = "mock VapoRock init failure"
        return False

    def clear_probe_caches() -> None:
        getattr(vaporock_module.vaporock_runtime_available, "cache_clear", lambda: None)()
        evaluate_module._vaporock_available.cache_clear()

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    monkeypatch.setattr(
        vaporock_module.VapoRockBackend,
        "initialize",
        fake_initialize,
    )
    clear_probe_caches()
    try:
        spec, _ = evaluate_module._build_eval_inputs(
            RecipePatch({}),
            "lunar_mare_low_ti",
            "stub",
            profile,
            RecipeSchema(),
        )
        assert init_calls == []
        assert vaporock_module.vaporock_runtime_available() is False
    finally:
        clear_probe_caches()

    assert init_calls == [{}]
    assert spec.vapor_pressure_provider_id == (
        DEFAULT_VAPOR_PRESSURE_FALLBACK_PROVIDER_ID
    )
    assert spec.allow_fallback_vapor is True
    assert b'"vapor_pressure_provider_id":"builtin-vapor-pressure"' in (
        canonical_evalspec_json(spec)
    )


def test_build_eval_inputs_strict_vaporock_unavailable_keeps_vaporock_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = {
        "profile_id": "strict-vapor-provider-profile",
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
            "campaign": "C0",
            "hours": 1,
            "mass_kg": 1000.0,
            "backend_name": "stub",
            "allow_fallback_vapor": False,
            "force_builtin_vapor_pressure": False,
        },
        "fidelities": {"stub": {"backend_name": "stub"}},
    }

    monkeypatch.setattr(evaluate_module, "_vaporock_available", lambda: False)
    spec, _ = evaluate_module._build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        RecipeSchema(),
    )
    canonical = canonical_evalspec_json(spec)

    assert spec.vapor_pressure_provider_id == DEFAULT_VAPOR_PRESSURE_PROVIDER_ID
    assert spec.allow_fallback_vapor is False
    assert spec.force_builtin_vapor_pressure is False
    assert b'"vapor_pressure_provider_id":"builtin-vapor-pressure"' in canonical
    assert b"vaporock" not in canonical


def test_build_eval_inputs_strict_thermal_window_keeps_vaporock_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _c2a_window_profile(1050.0, 1600.0, 24)
    profile["profile_id"] = "strict-thermal-window-vapor-provider-profile"
    profile["run"] = {
        **profile["run"],
        "allow_fallback_vapor": False,
        "force_builtin_vapor_pressure": False,
    }

    monkeypatch.setattr(evaluate_module, "_vaporock_available", lambda: False)
    spec, _ = evaluate_module._build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        RecipeSchema(),
    )
    canonical = canonical_evalspec_json(spec)

    assert spec.runtime_campaign_overrides["C2A_continuous"][
        "thermal_window_low_C"
    ] == pytest.approx(1050.0)
    assert spec.vapor_pressure_provider_id == DEFAULT_VAPOR_PRESSURE_PROVIDER_ID
    assert spec.allow_fallback_vapor is False
    assert spec.force_builtin_vapor_pressure is False
    assert b'"vapor_pressure_provider_id":"builtin-vapor-pressure"' in canonical
    assert b"vaporock" not in canonical


def test_build_eval_inputs_thermal_window_preserves_explicit_force_builtin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _c2a_window_profile(1050.0, 1600.0, 24)
    profile["profile_id"] = "force-builtin-thermal-window-vapor-profile"
    profile["run"] = {
        **profile["run"],
        "allow_fallback_vapor": True,
        "force_builtin_vapor_pressure": True,
    }

    def fail_probe() -> bool:
        raise AssertionError("explicit force-builtin must not probe VapoRock")

    monkeypatch.setattr(evaluate_module, "_vaporock_available", fail_probe)
    spec, _ = evaluate_module._build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        RecipeSchema(),
    )

    assert spec.vapor_pressure_provider_id == (
        DEFAULT_VAPOR_PRESSURE_FALLBACK_PROVIDER_ID
    )
    assert spec.allow_fallback_vapor is True
    assert spec.force_builtin_vapor_pressure is True


def test_build_eval_inputs_force_builtin_short_circuits_vaporock_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = {
        "profile_id": "force-builtin-vapor-profile",
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
            "campaign": "C0",
            "hours": 1,
            "mass_kg": 1000.0,
            "backend_name": "stub",
            "allow_fallback_vapor": True,
            "force_builtin_vapor_pressure": True,
        },
        "fidelities": {"stub": {"backend_name": "stub"}},
    }

    def fail_probe() -> bool:
        raise AssertionError("force-builtin keying must not probe VapoRock")

    monkeypatch.setattr(evaluate_module, "_vaporock_available", fail_probe)
    spec, _ = evaluate_module._build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        RecipeSchema(),
    )

    assert spec.vapor_pressure_provider_id == (
        DEFAULT_VAPOR_PRESSURE_FALLBACK_PROVIDER_ID
    )
    assert spec.force_builtin_vapor_pressure is True


def test_build_eval_inputs_keys_provider_code_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = {
        "profile_id": "vapor-code-profile",
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
            "campaign": "C0",
            "hours": 1,
            "mass_kg": 1000.0,
            "backend_name": "stub",
            "force_builtin_vapor_pressure": True,
        },
        "fidelities": {"stub": {"backend_name": "stub"}},
    }

    monkeypatch.setattr(
        evaluate_module,
        "_vapor_pressure_provider_code_fingerprint",
        lambda provider_id: f"{provider_id}:source-a",
    )
    first_spec, _ = evaluate_module._build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        RecipeSchema(),
    )
    monkeypatch.setattr(
        evaluate_module,
        "_vapor_pressure_provider_code_fingerprint",
        lambda provider_id: f"{provider_id}:source-b",
    )
    second_spec, _ = evaluate_module._build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        RecipeSchema(),
    )

    assert first_spec.vapor_pressure_provider_id == (
        DEFAULT_VAPOR_PRESSURE_FALLBACK_PROVIDER_ID
    )
    assert first_spec.vapor_pressure_provider_code_fingerprint.endswith(":source-a")
    assert second_spec.vapor_pressure_provider_code_fingerprint.endswith(":source-b")
    assert cache_key(first_spec) != cache_key(second_spec)


def test_provider_code_fingerprint_includes_upstream_package_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    versions = {
        "vaporock": "1.0.0",
        "thermoengine": "1.2.0",
    }

    def fake_version(package_name: str) -> str:
        if package_name in versions:
            return versions[package_name]
        raise evaluate_module.importlib_metadata.PackageNotFoundError(package_name)

    monkeypatch.setattr(evaluate_module.importlib_metadata, "version", fake_version)
    evaluate_module._vapor_pressure_provider_code_fingerprint.cache_clear()
    first = evaluate_module._vapor_pressure_provider_code_fingerprint(
        "vaporock"
    )
    versions["vaporock"] = "1.0.1"
    evaluate_module._vapor_pressure_provider_code_fingerprint.cache_clear()
    second = evaluate_module._vapor_pressure_provider_code_fingerprint(
        "vaporock"
    )
    evaluate_module._vapor_pressure_provider_code_fingerprint.cache_clear()

    assert first != second


def test_build_eval_inputs_records_lab_overlay_scope_without_runtime_behavior() -> None:
    profile = {
        "profile_id": "lab-overlay-profile",
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
            "campaign": "C0",
            "hours": 1,
            "mass_kg": 1000.0,
            "backend_name": "stub",
            "lab_overlay_scope": {
                "lab_alpha_digest": "robinot-alpha-v1",
                "geometry_digest": "robinot-geometry-v1",
                "effective_exposed_area_m2": 0.000314,
                "area_basis": "gram_lab_exposed_melt",
                "oxide_vapor_ceiling_digest": "oxide-ceiling-v1",
                "sink_channel_evidence_digests": {
                    "plume_oxidation_diagnostic": "plume-evidence-v1"
                },
            },
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
    payload = json.loads(canonical_evalspec_json(spec).decode("utf-8"))

    assert spec.lab_alpha_digest == "robinot-alpha-v1"
    assert spec.geometry_digest == "robinot-geometry-v1"
    assert spec.effective_exposed_area_m2 == pytest.approx(0.000314)
    assert spec.area_basis == "gram_lab_exposed_melt"
    assert spec.oxide_vapor_ceiling_digest == "oxide-ceiling-v1"
    assert spec.sink_channel_evidence_digests["plume_oxidation_diagnostic"] == (
        "plume-evidence-v1"
    )
    assert payload["effective_exposed_area_m2"] == "0.000314000"
    assert not hasattr(run_config, "lab_overlay_scope")


def test_build_eval_inputs_rejects_zero_mass_with_named_category() -> None:
    profile = {
        "profile_id": "zero-mass-profile",
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
            "campaign": "C0",
            "hours": 1,
            "mass_kg": 0.0,
            "backend_name": "stub",
        },
        "fidelities": {"stub": {"backend_name": "stub"}},
    }

    with pytest.raises(EvaluationInputError, match="zero_input_basis_breach"):
        _build_eval_inputs(
            RecipePatch({}),
            "lunar_mare_low_ti",
            "stub",
            profile,
            RecipeSchema(),
        )


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


def test_build_eval_inputs_keys_disabled_stage0_redox_doses_without_runtime_effect() -> None:
    profile = {
        "profile_id": "redox-dose-profile",
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
            "campaign": "C0",
            "hours": 1,
            "mass_kg": 1000.0,
            "backend_name": "stub",
        },
        "fidelities": {"stub": {"backend_name": "stub"}},
    }
    schema = RecipeSchema()
    patch = RecipePatch(
        {
            STAGE0_REDOX_OXIDANT_KG_PATH: 12.5,
            STAGE0_CARBON_REDUCTANT_KG_PATH: 7.25,
        }
    )

    undosed_spec, undosed_config = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )
    dosed_spec, dosed_config = _build_eval_inputs(
        patch,
        "lunar_mare_low_ti",
        "stub",
        profile,
        schema,
    )

    assert dosed_spec.stage0_redox_oxidant_kg == pytest.approx(12.5)
    assert dosed_spec.stage0_carbon_reductant_kg == pytest.approx(7.25)
    assert schema.to_setpoints_patch(patch) == {}
    assert dict(dosed_config.additives_kg) == dict(undosed_config.additives_kg) == {}
    assert cache_key(dosed_spec) != cache_key(undosed_spec)


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


def test_build_eval_inputs_refuses_unknown_runtime_campaign_override_fields() -> None:
    profile = _c2a_window_profile(None, None, 24)
    profile["run"]["runtime_campaign_overrides"] = {
        "C2A_continuous": {"unused_limit": 1.0}
    }

    with pytest.raises(
        RunnerError,
        match=(
            r"runtime_campaign_overrides\['C2A_continuous'\]\.unused_limit.*"
            r"known overridable fields.*pO2_mbar"
        ),
    ):
        _build_eval_inputs(
            RecipePatch({}),
            "lunar_mare_low_ti",
            "stub",
            profile,
            RecipeSchema(),
        )


@pytest.mark.parametrize(
    ("campaign", "low_C", "high_C", "duration_h"),
    (
        ("C2A_continuous", 1050.0, 1600.0, 24),
        ("C2B", 1320.0, 1480.0, 17),
        ("C4", 1580.0, 1670.0, 10),
        ("C6", 1450.0, 1550.0, 10),
    ),
)
def test_build_eval_inputs_accepts_profile_window_override_shapes(
    campaign: str,
    low_C: float,
    high_C: float,
    duration_h: int,
) -> None:
    spec, run_config = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        _campaign_window_profile(campaign, low_C, high_C, duration_h),
        RecipeSchema(),
    )

    overrides = run_config.runtime_campaign_overrides[campaign]
    assert spec.runtime_campaign_overrides[campaign] == overrides
    assert {
        "thermal_window_low_C",
        "thermal_window_high_C",
        "thermal_window_duration_h",
        "thermal_window_preheat_ramp_C_per_hr",
        "thermal_window_preheat_hours",
        "thermal_window_ramp_C_per_hr",
        "min_hold_hr",
        "max_hours",
    } <= set(overrides)
    assert overrides["min_hold_hr"] == pytest.approx(run_config.hours)
    _force_builtin_run_from_config(run_config)._start_session()


def test_web_default_c4_override_shape_is_allowed_and_controls_target() -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C4",
        hours=1,
        mass_kg=1000.0,
        backend_name="stub",
        runtime_campaign_overrides={
            "C4": {
                "pO2_mbar": 0.2,
                "hold_temp_C": 1600.0,
                "max_hours": 1.0,
                "ramp_rate": 10.0,
            }
        },
        force_builtin_vapor_pressure=True,
        allow_fallback_vapor=True,
    )

    config = run._session_config()
    session = run._start_session()
    target, ramp_rate = session.simulator.campaign_mgr.get_temp_target(
        session.simulator.melt.campaign,
        0,
        session.simulator.melt,
    )

    assert config.runtime_campaign_overrides["C4"]["hold_temp_C"] == pytest.approx(1600.0)
    assert target == pytest.approx(1600.0)
    assert ramp_rate == pytest.approx(10.0)


def test_session_campaign_override_rejects_unknown_fields_at_adjust_time() -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C4",
        hours=1,
        mass_kg=1000.0,
        backend_name="stub",
        force_builtin_vapor_pressure=True,
        allow_fallback_vapor=True,
    )
    session = run._start_session()

    with pytest.raises(
        ValueError,
        match=r"runtime_campaign_overrides\['C4'\]\.unused_limit",
    ):
        session.adjust(
            "campaign_override",
            1.0,
            campaign="C4",
            field="unused_limit",
        )


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


def test_c2a_profile_window_uses_setpoints_furnace_ceiling_when_constraint_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = load_config_bundle()
    setpoints = copy.deepcopy(bundle.setpoints)
    setpoints["furnace_max_T_C"] = 1400.0
    monkeypatch.setattr(
        evaluate_module,
        "load_config_bundle",
        lambda *args, **kwargs: replace(bundle, setpoints=setpoints),
    )

    with pytest.raises(EvaluationInputError, match="furnace_T_max_C 1400 C"):
        evaluate_module._build_eval_inputs(
            RecipePatch({}),
            "lunar_mare_low_ti",
            "stub",
            _c2a_window_profile(1350.0, 1450.0, 18),
            RecipeSchema(),
        )


def test_lab_schedule_uses_setpoints_furnace_ceiling_when_constraint_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = load_config_bundle()
    setpoints = copy.deepcopy(bundle.setpoints)
    setpoints["furnace_max_T_C"] = 1400.0
    monkeypatch.setattr(
        evaluate_module,
        "load_config_bundle",
        lambda *args, **kwargs: replace(bundle, setpoints=setpoints),
    )
    schedule = _lab_schedule(
        duration_h=2.0,
        temperature_points=((0.0, 25.0), (1.0, 1450.0), (2.0, 1400.0)),
        pressure_points=((0.0, 13.0), (1.0, 14.0), (2.0, 15.0)),
        furnace_ceiling_C=1800.0,
    )

    with pytest.raises(
        EvaluationInputError,
        match="lab_schedule_temperature_exceeds_furnace_T_max_C",
    ):
        evaluate_module._build_eval_inputs(
            RecipePatch({}),
            "lunar_mare_low_ti",
            "stub",
            _lab_schedule_profile(schedule),
            RecipeSchema(),
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


def test_lab_schedule_profile_schedules_declared_piecewise_temperature_pressure() -> None:
    schedule = _lab_schedule(
        duration_h=2.0,
        temperature_points=((0.0, 25.0), (1.0, 625.0), (2.0, 1225.0)),
        pressure_points=((0.0, 13.0), (1.0, 14.0), (2.0, 15.0)),
        furnace_ceiling_C=1300.0,
    )
    spec, run_config = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        _lab_schedule_profile(schedule),
        RecipeSchema(),
    )

    assert spec.hours == 2
    assert run_config.hours == 2
    assert spec.lab_schedule["id"] == "test_lab_schedule"
    assert "schedule_digest" in spec.data_digests
    assert "gas_boundary_digest" in spec.data_digests

    session = _force_builtin_run_from_config(run_config)._start_session()
    rows = [session.advance().per_hour_summary for _ in range(run_config.hours)]

    expected_temperatures = [
        _declared_piecewise_value(schedule["melt_temperature_C"], hour)
        for hour in (1.0, 2.0)
    ]
    expected_pressures = [
        _declared_piecewise_value(schedule["chamber_pressure_mbar"], hour)
        for hour in (1.0, 2.0)
    ]
    assert [row["T_C"] for row in rows] == pytest.approx(expected_temperatures)
    assert [
        row["pO2_enforcement"]["p_total_mbar"]
        for row in rows
    ] == pytest.approx(expected_pressures)
    assert spec.lab_schedule["window_semantics"]["preheat_h"] == pytest.approx(0.0)
    assert spec.lab_schedule["window_semantics"]["measured_window_end_h"] == (
        pytest.approx(2.0)
    )


def test_build_eval_inputs_strict_lab_schedule_keeps_vaporock_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule = _lab_schedule(
        duration_h=2.0,
        temperature_points=((0.0, 25.0), (1.0, 625.0), (2.0, 1225.0)),
        pressure_points=((0.0, 13.0), (1.0, 14.0), (2.0, 15.0)),
        furnace_ceiling_C=1300.0,
    )
    profile = _lab_schedule_profile(schedule)
    profile["run"] = {
        **profile["run"],
        "allow_fallback_vapor": False,
        "force_builtin_vapor_pressure": False,
    }

    monkeypatch.setattr(evaluate_module, "_vaporock_available", lambda: False)
    spec, run_config = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        RecipeSchema(),
    )
    canonical = canonical_evalspec_json(spec)

    assert spec.lab_schedule["id"] == "test_lab_schedule"
    assert spec.vapor_pressure_provider_id == DEFAULT_VAPOR_PRESSURE_PROVIDER_ID
    assert spec.allow_fallback_vapor is False
    assert spec.force_builtin_vapor_pressure is False
    assert b'"vapor_pressure_provider_id":"builtin-vapor-pressure"' in canonical
    assert b"vaporock" not in canonical


def test_lab_schedule_digests_keep_gas_boundary_separate() -> None:
    schedule = _lab_schedule(
        duration_h=2.0,
        temperature_points=((0.0, 25.0), (2.0, 1225.0)),
        pressure_points=((0.0, 13.0), (2.0, 15.0)),
        furnace_ceiling_C=1300.0,
    )
    gas_mutation = copy.deepcopy(schedule)
    gas_mutation["gas_boundary"]["background_gas"]["species"] = "He"
    schedule_mutation = copy.deepcopy(schedule)
    schedule_mutation["melt_temperature_C"][-1]["value"] = 1200.0

    base = lab_schedule_digests(normalize_lab_schedule(schedule))
    gas_changed = lab_schedule_digests(normalize_lab_schedule(gas_mutation))
    schedule_changed = lab_schedule_digests(normalize_lab_schedule(schedule_mutation))

    assert gas_changed["schedule_digest"] == base["schedule_digest"]
    assert gas_changed["gas_boundary_digest"] != base["gas_boundary_digest"]
    assert schedule_changed["schedule_digest"] != base["schedule_digest"]
    assert schedule_changed["gas_boundary_digest"] == base["gas_boundary_digest"]


def test_lab_schedule_digest_uses_canonical_physics_not_legacy_or_provenance() -> None:
    schedule = _lab_schedule(
        duration_h=2.0,
        temperature_points=((0.0, 25.0), (2.0, 1225.0)),
        pressure_points=((0.0, 13.0), (2.0, 15.0)),
        furnace_ceiling_C=1300.0,
    )
    schedule["experiment_windows"] = {
        "heating": {"start_h": 0.0, "end_h": 2.0},
        "measured": {"start_h": 0.5, "end_h": 1.5},
        "cooldown": {"duration_h": 0.5, "deposit_sampling": "cooldown_or_post_run"},
    }
    schedule["window_semantics"] = {
        "preheat_h": 0.5,
        "measured_window_start_h": 0.5,
        "measured_window_end_h": 1.5,
        "cooldown_h": 0.5,
        "deposit_sample_basis": "after_cooldown",
    }
    base = lab_schedule_digests(normalize_lab_schedule(schedule))

    legacy_wording = copy.deepcopy(schedule)
    legacy_wording["experiment_windows"]["cooldown"][
        "deposit_sampling"
    ] = "post_run_cooldown"
    point_provenance = copy.deepcopy(schedule)
    point_provenance["melt_temperature_C"][0]["citation_id"] = "other_citation"
    cooldown = copy.deepcopy(schedule)
    cooldown["experiment_windows"]["cooldown"]["duration_h"] = 0.25
    cooldown["window_semantics"]["cooldown_h"] = 0.25
    deposit_basis = copy.deepcopy(schedule)
    deposit_basis["experiment_windows"]["cooldown"]["deposit_sampling"] = "hot"
    deposit_basis["window_semantics"]["deposit_sample_basis"] = "hot"

    assert (
        lab_schedule_digests(normalize_lab_schedule(legacy_wording))[
            "schedule_digest"
        ]
        == base["schedule_digest"]
    )
    assert (
        lab_schedule_digests(normalize_lab_schedule(point_provenance))[
            "schedule_digest"
        ]
        == base["schedule_digest"]
    )
    assert (
        lab_schedule_digests(normalize_lab_schedule(cooldown))["schedule_digest"]
        != base["schedule_digest"]
    )
    assert (
        lab_schedule_digests(normalize_lab_schedule(deposit_basis))[
            "schedule_digest"
        ]
        != base["schedule_digest"]
    )


def test_lab_schedule_deposit_sample_basis_default_is_branch_invariant() -> None:
    schedule = _lab_schedule(
        duration_h=2.0,
        temperature_points=((0.0, 25.0), (2.0, 1225.0)),
        pressure_points=((0.0, 13.0), (2.0, 15.0)),
        furnace_ceiling_C=1300.0,
    )

    explicit_window = copy.deepcopy(schedule)
    explicit_window["window_semantics"] = {
        "preheat_h": 0.0,
        "measured_window_start_h": 0.0,
        "measured_window_end_h": 2.0,
        "cooldown_h": 0.0,
        "deposit_sample_basis": "hot",
    }
    explicit_experiment = copy.deepcopy(schedule)
    explicit_experiment["experiment_windows"] = {
        "measured": {"start_h": 0.0, "end_h": 2.0},
        "cooldown": {"duration_h": 0.0, "deposit_sampling": "hot"},
    }

    normalized_window = normalize_lab_schedule(explicit_window)
    normalized_experiment = normalize_lab_schedule(explicit_experiment)
    assert normalized_window["window_semantics"]["deposit_sample_basis"] == "hot"
    assert normalized_experiment["window_semantics"]["deposit_sample_basis"] == "hot"
    assert (
        lab_schedule_digests(normalized_window)["schedule_digest"]
        == lab_schedule_digests(normalized_experiment)["schedule_digest"]
    )

    missing_window = normalize_lab_schedule(copy.deepcopy(schedule))
    missing_experiment_input = copy.deepcopy(schedule)
    missing_experiment_input["experiment_windows"] = {
        "measured": {"start_h": 0.0, "end_h": 2.0},
    }
    missing_experiment = normalize_lab_schedule(missing_experiment_input)
    assert missing_window["window_semantics"]["deposit_sample_basis"] == "not_reported"
    assert (
        missing_experiment["window_semantics"]["deposit_sample_basis"]
        == "not_reported"
    )
    assert missing_window["window_semantics"] == missing_experiment["window_semantics"]
    assert (
        lab_schedule_digests(missing_window)["schedule_digest"]
        == lab_schedule_digests(missing_experiment)["schedule_digest"]
    )
    assert (
        lab_schedule_digests(missing_window)["schedule_digest"]
        != lab_schedule_digests(normalized_window)["schedule_digest"]
    )


def test_lab_schedule_profile_reports_window_semantics_to_runtime_overrides() -> None:
    schedule = _lab_schedule(
        duration_h=3.0,
        temperature_points=((0.0, 25.0), (1.0, 625.0), (3.0, 1225.0)),
        pressure_points=((0.0, 13.0), (3.0, 15.0)),
        furnace_ceiling_C=1300.0,
    )
    schedule["window_semantics"] = {
        "preheat_h": 0.5,
        "measured_window_start_h": 0.5,
        "measured_window_end_h": 2.5,
        "cooldown_h": 0.5,
        "deposit_sample_basis": "after_cooldown",
    }

    spec, run_config = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        _lab_schedule_profile(schedule),
        RecipeSchema(),
    )

    overrides = run_config.runtime_campaign_overrides["C2A_continuous"]
    window = spec.lab_schedule["window_semantics"]
    assert overrides["thermal_window_preheat_hours"] == pytest.approx(0.5)
    assert window["measured_window_start_h"] == pytest.approx(0.5)
    assert window["measured_window_end_h"] == pytest.approx(2.5)
    assert window["cooldown_h"] == pytest.approx(0.5)
    assert window["deposit_sample_basis"] == "after_cooldown"


def test_lab_schedule_profile_bridges_experiment_windows_to_window_semantics() -> None:
    schedule = _lab_schedule(
        duration_h=3.0,
        temperature_points=((0.0, 25.0), (1.0, 625.0), (3.0, 1225.0)),
        pressure_points=((0.0, 13.0), (3.0, 15.0)),
        furnace_ceiling_C=1300.0,
    )
    schedule["experiment_windows"] = {
        "heating": {"start_h": 0.0, "end_h": 3.0},
        "measured": {"start_h": 0.5, "end_h": 2.5},
        "cooldown": {
            "duration_h": 0.5,
            "deposit_sampling": "cooldown_or_post_run",
        },
    }

    spec, run_config = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        _lab_schedule_profile(schedule),
        RecipeSchema(),
    )

    overrides = run_config.runtime_campaign_overrides["C2A_continuous"]
    window = spec.lab_schedule["window_semantics"]
    assert overrides["thermal_window_preheat_hours"] == pytest.approx(0.5)
    assert window["measured_window_start_h"] == pytest.approx(0.5)
    assert window["measured_window_end_h"] == pytest.approx(2.5)
    assert window["cooldown_h"] == pytest.approx(0.5)
    assert window["deposit_sample_basis"] == "after_cooldown"


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("above_declared_ceiling", "lab_schedule_temperature_exceeds_furnace_ceiling"),
        ("above_constraint_ceiling", "lab_schedule_temperature_exceeds_furnace_T_max_C"),
        (
            "nonmonotonic_pressure",
            "lab_schedule_chamber_pressure_mbar_time_arrays_must_be_monotonic",
        ),
        ("missing_gas_boundary", "lab_schedule_missing_gas_boundary"),
        ("missing_point_unit", "lab_schedule_melt_temperature_C_unit_missing"),
        ("pressure_unit_pa", "lab_schedule_chamber_pressure_mbar_unit_mismatch"),
        ("temperature_unit_k", "lab_schedule_melt_temperature_C_unit_mismatch"),
        (
            "temperature_requires_two_points",
            "lab_schedule_melt_temperature_C_requires_two_points",
        ),
        (
            "pressure_endpoint_mismatch",
            "lab_schedule_chamber_pressure_mbar_time_arrays_must_start_at_0_end_at_duration",
        ),
        (
            "measured_window_negative",
            "lab_schedule_window_semantics_measured_window_negative",
        ),
        (
            "cooldown_overflow",
            "lab_schedule_window_semantics_cooldown_exceeds_duration",
        ),
        (
            "windows_consistency",
            "lab_schedule_experiment_windows_conflict_with_window_semantics",
        ),
        ("time_h_alias", "lab_schedule_melt_temperature_C_time_h_alias_unsupported"),
    ],
)
def test_lab_schedule_profile_fail_loud_rules_are_named(
    mutation: str,
    expected: str,
) -> None:
    schedule = _lab_schedule(
        duration_h=1.0,
        temperature_points=((0.0, 25.0), (1.0, 1200.0)),
        pressure_points=((0.0, 13.0), (1.0, 13.0)),
        furnace_ceiling_C=1300.0,
    )
    constraints = None
    if mutation == "above_declared_ceiling":
        schedule["melt_temperature_C"][-1]["value"] = 1500.0
    elif mutation == "above_constraint_ceiling":
        schedule["melt_temperature_C"][-1]["value"] = 1200.0
        constraints = PhysicsConstraintSet(
            furnace_T_max_C=ThresholdSpec(
                id="furnace_T_max_C",
                value=1000.0,
                units="degC",
                source="test",
                source_ref="tests/test_optimizer_evalspec.py",
            )
        )
    elif mutation == "nonmonotonic_pressure":
        schedule["chamber_pressure_mbar"] = [
            {"t_h": 0.0, "value": 13.0, "unit": "mbar"},
            {"t_h": 0.5, "value": 13.0, "unit": "mbar"},
            {"t_h": 0.4, "value": 13.0, "unit": "mbar"},
        ]
    elif mutation == "missing_gas_boundary":
        schedule.pop("gas_boundary")
    elif mutation == "missing_point_unit":
        schedule["melt_temperature_C"][0].pop("unit")
    elif mutation == "pressure_unit_pa":
        schedule["chamber_pressure_mbar"][0]["unit"] = "Pa"
    elif mutation == "temperature_unit_k":
        schedule["melt_temperature_C"][0]["unit"] = "K"
    elif mutation == "temperature_requires_two_points":
        schedule["melt_temperature_C"] = schedule["melt_temperature_C"][:1]
    elif mutation == "pressure_endpoint_mismatch":
        schedule["chamber_pressure_mbar"][-1]["t_h"] = 0.9
    elif mutation == "measured_window_negative":
        schedule["window_semantics"] = {
            "preheat_h": 0.0,
            "measured_window_start_h": 0.8,
            "measured_window_end_h": 0.7,
            "cooldown_h": 0.0,
            "deposit_sample_basis": "hot",
        }
    elif mutation == "cooldown_overflow":
        schedule["window_semantics"] = {
            "preheat_h": 0.0,
            "measured_window_start_h": 0.0,
            "measured_window_end_h": 0.9,
            "cooldown_h": 0.2,
            "deposit_sample_basis": "after_cooldown",
        }
    elif mutation == "windows_consistency":
        schedule["experiment_windows"] = {
            "heating": {"start_h": 0.0, "end_h": 1.0},
            "measured": {"start_h": 0.0, "end_h": 1.0},
            "cooldown": {
                "duration_h": 0.0,
                "deposit_sampling": "cooldown_or_post_run",
            },
        }
        schedule["window_semantics"] = {
            "preheat_h": 0.0,
            "measured_window_start_h": 0.0,
            "measured_window_end_h": 1.0,
            "cooldown_h": 0.0,
            "deposit_sample_basis": "hot",
        }
    elif mutation == "time_h_alias":
        first = schedule["melt_temperature_C"][0]
        first["time_h"] = first.pop("t_h")

    with pytest.raises(EvaluationInputError, match=expected):
        _build_eval_inputs(
            RecipePatch({}),
            "lunar_mare_low_ti",
            "stub",
            _lab_schedule_profile(schedule),
            RecipeSchema(),
            constraints=constraints,
        )


def test_lab_schedule_pressure_floor_endpoint_is_explicit() -> None:
    schedule = _lab_schedule(
        duration_h=1.0,
        temperature_points=((0.0, 25.0), (1.0, 1200.0)),
        pressure_points=(
            (0.0, LAB_SCHEDULE_PRESSURE_FLOOR_MBAR),
            (1.0, LAB_SCHEDULE_PRESSURE_FLOOR_MBAR),
        ),
        furnace_ceiling_C=1300.0,
    )

    normalized = normalize_lab_schedule(schedule)
    assert normalized["chamber_pressure_mbar"][0]["value"] == pytest.approx(
        LAB_SCHEDULE_PRESSURE_FLOOR_MBAR
    )

    below_floor = copy.deepcopy(schedule)
    below_floor["chamber_pressure_mbar"][0]["value"] = (
        LAB_SCHEDULE_PRESSURE_FLOOR_MBAR / 2.0
    )
    with pytest.raises(
        LabScheduleValidationError,
        match="lab_schedule_pressure_below_implemented_floor",
    ):
        normalize_lab_schedule(below_floor)


def test_lab_schedule_pO2_setpoint_above_total_pressure_clips_per_hour() -> None:
    schedule = _lab_schedule(
        duration_h=1.0,
        temperature_points=((0.0, 25.0), (1.0, 625.0)),
        pressure_points=((0.0, 1.0), (1.0, 1.0)),
        furnace_ceiling_C=700.0,
    )
    profile = _lab_schedule_profile(
        schedule,
        runtime_campaign_overrides={
            "C2A_continuous": {"pO2_mbar": 3.0},
        },
    )
    _, run_config = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "stub",
        profile,
        RecipeSchema(),
    )

    session = _force_builtin_run_from_config(run_config)._start_session()
    row = session.advance().per_hour_summary["pO2_enforcement"]

    assert row["hour"] == 1
    assert row["setpoint_mbar"] == pytest.approx(3.0)
    assert row["achieved_mbar"] == pytest.approx(1.0)
    assert row["limited_by_total_pressure"] is True
    assert row["status"] == "clipped_to_total_pressure"


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


def _lab_schedule(
    *,
    duration_h: float,
    temperature_points: tuple[tuple[float, float], ...],
    pressure_points: tuple[tuple[float, float], ...],
    furnace_ceiling_C: float,
) -> dict[str, object]:
    return {
        "id": "test_lab_schedule",
        "duration_h": duration_h,
        "interpolation": "piecewise_linear",
        "interpolation_source_class": "assumption_with_sensitivity_marker",
        "interpolation_citation_id": "test",
        "interpolation_extraction_note": "test-declared piecewise schedule",
        "furnace_ceiling_C": furnace_ceiling_C,
        "melt_temperature_C": [
            {"t_h": t_h, "value": value, "unit": "C"}
            for t_h, value in temperature_points
        ],
        "chamber_pressure_mbar": [
            {"t_h": t_h, "value": value, "unit": "mbar"}
            for t_h, value in pressure_points
        ],
        "gas_boundary": {
            "background_gas": {
                "species": "Ar",
                "mole_fraction": 1.0,
                "source_class": "literature_sidecar",
                "source_ref": "test-methods",
            },
            "imposed_flow": {
                "value": 0.3,
                "unit": "NL_min",
                "source_class": "literature_sidecar",
                "source_ref": "test-methods",
            },
            "pressure_control": {
                "mode": "flow_through_with_pump",
                "source_class": "literature_sidecar",
                "source_ref": "test-methods",
            },
        },
    }


def _lab_schedule_profile(
    schedule: dict[str, object],
    *,
    runtime_campaign_overrides: dict[str, dict[str, float]] | None = None,
) -> dict[str, object]:
    profile = _campaign_window_profile(
        "C2A_continuous",
        None,
        None,
        int(math.ceil(float(schedule["duration_h"]))),
        profile_id="lab-schedule-test",
    )
    profile["run"] = {
        **profile["run"],
        "lab_schedule": schedule,
    }
    if runtime_campaign_overrides:
        profile["run"]["runtime_campaign_overrides"] = runtime_campaign_overrides
    return profile


def _declared_piecewise_value(
    points: list[dict[str, float]],
    t_h: float,
) -> float:
    for left, right in zip(points, points[1:]):
        if t_h <= right["t_h"]:
            span = right["t_h"] - left["t_h"]
            if span <= 0.0:
                return right["value"]
            frac = (t_h - left["t_h"]) / span
            return left["value"] + frac * (right["value"] - left["value"])
    return points[-1]["value"]


def test_interpolate_schedule_points_refuses_extrapolation() -> None:
    schedule = normalize_lab_schedule(
        _lab_schedule(
            duration_h=1.0,
            temperature_points=((0.0, 25.0), (1.0, 1200.0)),
            pressure_points=((0.0, 13.0), (1.0, 13.0)),
            furnace_ceiling_C=1300.0,
        )
    )
    points = schedule["melt_temperature_C"]

    assert interpolate_schedule_points(points, 0.0) == pytest.approx(25.0)
    assert interpolate_schedule_points(points, 1.0) == pytest.approx(1200.0)
    for sample_time_h in (-0.1, 1.1):
        with pytest.raises(
            LabScheduleValidationError,
            match="lab_schedule_sample_time_outside_declared_window",
        ):
            interpolate_schedule_points(points, sample_time_h)


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
                "materials": "materials-digest",
                "vapor_pressures": "vapor-digest",
                "species_catalog": "species-catalog-digest",
                "profile": "profile-digest",
            }
        )


@pytest.mark.parametrize("missing_key", ("materials", "species_catalog"))
def test_missing_new_required_data_digest_raises(missing_key: str) -> None:
    # Each newly-required cache-determinant key must be enforced on direct
    # construction (the legacy sentinel scope applies ONLY at deserialize/reduce).
    digests = {
        "setpoints": "setpoint-digest",
        "feedstocks": "feedstock-digest",
        "materials": "materials-digest",
        "vapor_pressures": "vapor-digest",
        "species_catalog": "species-catalog-digest",
        "profile": "profile-digest",
    }
    del digests[missing_key]
    with pytest.raises(
        ValueError, match=f"data_digests missing required keys: {missing_key}"
    ):
        _base_spec(data_digests=digests)


def test_empty_required_data_digest_raises() -> None:
    with pytest.raises(ValueError, match=r"data_digests\['setpoints'\] must be non-empty"):
        _base_spec(
            data_digests={
                "setpoints": "",
                "feedstocks": "feedstock-digest",
                "materials": "materials-digest",
                "vapor_pressures": "vapor-digest",
                "species_catalog": "species-catalog-digest",
                "profile": "profile-digest",
            }
        )


@pytest.mark.parametrize("bad_value", (math.nan, math.inf, -math.inf))
def test_feedstock_recipe_digest_rejects_bad_numeric_values(bad_value: float) -> None:
    with pytest.raises(ValueError, match="NaN and infinity"):
        feedstock_recipe_digest({"SiO2": bad_value})
