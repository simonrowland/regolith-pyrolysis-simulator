from __future__ import annotations

import copy
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


PINNED_EVALSPEC_JSON = (
    b'{"additives_kg":{"CaO":"1.500000000"},"backend_name":"stub",'
    b'"campaign":"C0","chemistry_kernel":{"allow_builtin_fallback":false,'
    b'"engine":"builtin","pressure_Pa":"0.001000000"},'
    b'"code_version":"0.5.4","data_digests":{"feedstocks":"feedstock-digest",'
    b'"profile":"profile-digest","setpoints":"setpoints-digest",'
    b'"vapor_pressures":"vapor-digest"},"feedstock_id":"lunar_mare_low_ti",'
    b'"feedstock_recipe_digest":"feedstock-recipe-digest","fidelity":"fast",'
    b'"hours":24,"mass_kg":"1000.000000000","profile_id":"oxygen-yield-v1",'
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
    code_version="0.5.4",
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
        ("code_version", "0.5.5"),
        ("campaign", "C2A"),
        ("hours", 48),
        ("mass_kg", 500.0),
        ("additives_kg", {"CaO": 2.5}),
        ("track", "mre_baseline"),
        ("backend_name", "magmin"),
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


@pytest.mark.parametrize("bad_value", (math.nan, math.inf, -math.inf))
def test_cache_key_rejects_nan_and_infinity(bad_value: float) -> None:
    spec = _base_spec(chemistry_kernel={"allow_builtin_fallback": False, "x": bad_value})

    with pytest.raises(ValueError, match="NaN and infinity"):
        cache_key(spec)


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
