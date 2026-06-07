"""Deny-by-default loader for feedstock-specific optimizer profiles."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from simulator.config import DEFAULT_DATA_DIR
from simulator.optimize.objective import ObjectiveProfileError, objective_definitions
from simulator.optimize.physics import GATE_ORDER, PhysicsConstraintSet, ThresholdSpec
from simulator.optimize.recipe import RecipePatch, RecipeSchema, RecipeValidationError


PROFILE_SCHEMA_VERSION = "profile-schema-v1"
PROFILE_DIRNAME = "optimize_profiles"
VALID_FIDELITIES = ("stub", "fast", "high", "auto")
KNOWN_STUDY_CONSTRAINTS = frozenset({"physics", "stub_smoke"})
KNOWN_OBJECTIVE_METRICS = frozenset(
    {
        "pure_silica_glass_kg",
        "metals_plus_o2_kg",
        "metals_total_kg",
        "O2_kg",
        "o2_kg",
        "oxygen_kg",
        "oxygen_stored_kg",
        "oxygen_vented_kg",
        "energy_kWh",
        "energy_total_kWh",
        "duration_h",
        "total_hours",
    }
)

_TOP_LEVEL_KEYS = frozenset(
    {
        "profile_id",
        "profile_schema_version",
        "feedstock",
        "description",
        "north_star_rationale",
        "objective_emphasis",
        "objectives",
        "constraints",
        "study_constraints",
        "run",
        "fidelities",
        "seed_recipes",
        "early_tap_mode",
        "staged",
        "staged_strategy",
    }
)
_OBJECTIVE_KEYS = frozenset({"metric", "sense", "units", "weight", "rationale"})
_THRESHOLD_CONSTRAINT_KEYS = MappingProxyType({
    "stream_purity_min": "stream_purity_min",
    "coating_min_campaigns_to_resinter": "coating_min_campaigns_to_resinter",
    "extraction_min_fraction": "extraction_min_fraction",
    "knudsen_max": "knudsen_max",
    "furnace_T_max_C": "furnace_T_max_C",
})
_CONSTRAINT_KEYS = frozenset({
    "gates",
    "target_species",
    *_THRESHOLD_CONSTRAINT_KEYS,
})
_SEED_KEYS = frozenset(
    {"id", "source_campaign", "source_campaigns", "rationale", "patch"}
)
_RUN_KEYS = frozenset(
    {
        "campaign",
        "hours",
        "mass_kg",
        "additives_kg",
        "track",
        "backend_name",
        "c5_enabled",
        "mre_max_voltage_V",
        "mre_target_species",
        "reduced_real_cache",
        "runtime_campaign_overrides",
        "chemistry_kernel",
    }
)
_REDUCED_REAL_CACHE_KEYS = frozenset({
    "db_path",
    "miss_policy",
    "authorized_backend_name",
    "authorized_backend_version",
})
_REDUCED_REAL_MISS_POLICIES = frozenset({"fail-loud", "live-fill"})


class ProfileValidationError(ValueError):
    """Raised when an optimizer profile file is malformed or unsafe."""


class ValidatedProfile(Mapping[str, Any]):
    def __init__(self, data: Mapping[str, Any], *, source: str | Path) -> None:
        self._data = MappingProxyType(dict(data))
        self.source = str(source)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


def load_profile(
    feedstock_or_path: str | Path,
    *,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    schema: RecipeSchema | None = None,
) -> Mapping[str, Any]:
    path = _resolve_profile_path(feedstock_or_path, data_dir=Path(data_dir))
    raw = _load_yaml_mapping(path)
    expected = path.stem if path.parent.name == PROFILE_DIRNAME else None
    return validate_profile(raw, expected_feedstock=expected, source=path, schema=schema)


def load_profiles(
    *,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    schema: RecipeSchema | None = None,
) -> Mapping[str, Mapping[str, Any]]:
    profiles = {
        path.stem: validate_profile(
            _load_yaml_mapping(path),
            expected_feedstock=path.stem,
            source=path,
            schema=schema,
        )
        for path in _profile_paths(Path(data_dir))
    }
    return MappingProxyType(profiles)


def validate_profile_catalog(
    *,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    schema: RecipeSchema | None = None,
) -> Mapping[str, Mapping[str, Any]]:
    data_root = Path(data_dir)
    expected = set(_feedstock_ids(data_root))
    found = {path.stem for path in _profile_paths(data_root)}
    missing = sorted(expected - found)
    extras = sorted(found - expected)
    if missing or extras:
        parts = []
        if missing:
            parts.append(f"missing profiles: {', '.join(missing)}")
        if extras:
            parts.append(f"extra profiles: {', '.join(extras)}")
        raise ProfileValidationError("; ".join(parts))
    return load_profiles(data_dir=data_root, schema=schema)


def validate_profile(
    raw: Mapping[str, Any],
    *,
    expected_feedstock: str | None = None,
    source: str | Path = "<profile>",
    schema: RecipeSchema | None = None,
) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise ProfileValidationError(f"{source}: profile must be a mapping")
    profile = dict(raw)
    _reject_unknown_keys(profile, _TOP_LEVEL_KEYS, source=source, where="profile")

    required = {
        "profile_id",
        "profile_schema_version",
        "feedstock",
        "objectives",
        "constraints",
        "run",
        "fidelities",
        "seed_recipes",
    }
    _require_keys(profile, required, source=source, where="profile")

    if profile["profile_schema_version"] != PROFILE_SCHEMA_VERSION:
        raise ProfileValidationError(
            f"{source}: profile_schema_version must be {PROFILE_SCHEMA_VERSION!r}"
        )
    if expected_feedstock is not None and profile["feedstock"] != expected_feedstock:
        raise ProfileValidationError(
            f"{source}: feedstock {profile['feedstock']!r} does not match {expected_feedstock!r}"
        )

    _validate_objectives(profile, source=source)
    _validate_constraints(profile["constraints"], source=source)
    _validate_study_constraints(profile.get("study_constraints"), source=source)
    _validate_run(profile["run"], source=source, where="run")
    _validate_fidelities(
        profile["fidelities"],
        source=source,
        base_run=profile["run"],
    )
    _validate_seed_recipes(
        profile["seed_recipes"],
        source=source,
        schema=schema or RecipeSchema(),
    )
    if "early_tap_mode" in profile and not isinstance(profile["early_tap_mode"], bool):
        raise ProfileValidationError(f"{source}: early_tap_mode must be boolean")
    return ValidatedProfile(profile, source=source)


def physics_constraints_from_profile(
    profile: Mapping[str, Any],
    *,
    source: str | Path | None = None,
) -> PhysicsConstraintSet:
    raw_constraints = profile.get("constraints", {})
    profile_source = _profile_source(profile, source)
    if not isinstance(raw_constraints, Mapping):
        raise ProfileValidationError(f"{profile_source}: constraints must be a mapping")
    _validate_constraints(raw_constraints, source=profile_source)
    base = PhysicsConstraintSet()
    updates: dict[str, Any] = {}
    for key, attr in _THRESHOLD_CONSTRAINT_KEYS.items():
        if key not in raw_constraints:
            continue
        threshold = getattr(base, attr)
        updates[attr] = ThresholdSpec(
            id=threshold.id,
            value=float(raw_constraints[key]),
            units=threshold.units,
            source="profile",
            source_ref=f"{profile_source}:constraints.{key}",
            tolerance=threshold.tolerance,
        )
    if "target_species" in raw_constraints:
        updates["target_species"] = tuple(
            str(species) for species in raw_constraints["target_species"]
        )
    updates["active_gates"] = tuple(str(gate) for gate in raw_constraints["gates"])
    return replace(base, **updates)


def _resolve_profile_path(feedstock_or_path: str | Path, *, data_dir: Path) -> Path:
    candidate = Path(feedstock_or_path)
    if candidate.exists() or candidate.suffix in {".yaml", ".yml"}:
        return candidate
    return data_dir / PROFILE_DIRNAME / f"{feedstock_or_path}.yaml"


def _load_yaml_mapping(path: Path) -> Mapping[str, Any]:
    try:
        loaded = yaml.safe_load(path.read_text())
    except OSError as exc:
        raise ProfileValidationError(f"{path}: cannot read profile") from exc
    except yaml.YAMLError as exc:
        raise ProfileValidationError(f"{path}: invalid YAML") from exc
    if not isinstance(loaded, Mapping):
        raise ProfileValidationError(f"{path}: profile YAML must be a mapping")
    return loaded


def _feedstock_ids(data_dir: Path) -> tuple[str, ...]:
    path = data_dir / "feedstocks.yaml"
    loaded = yaml.safe_load(path.read_text())
    if not isinstance(loaded, Mapping):
        raise ProfileValidationError(f"{path}: feedstocks.yaml must be a mapping")
    return tuple(str(key) for key in loaded)


def _profile_paths(data_dir: Path) -> tuple[Path, ...]:
    profile_dir = data_dir / PROFILE_DIRNAME
    if not profile_dir.exists():
        raise ProfileValidationError(f"{profile_dir}: profile directory missing")
    return tuple(sorted(profile_dir.glob("*.yaml")))


def _validate_objectives(profile: Mapping[str, Any], *, source: str | Path) -> None:
    raw_objectives = profile["objectives"]
    if not isinstance(raw_objectives, list) or not raw_objectives:
        raise ProfileValidationError(f"{source}: objectives must be a non-empty list")
    seen: set[str] = set()
    for index, objective in enumerate(raw_objectives):
        where = f"objectives[{index}]"
        if not isinstance(objective, Mapping):
            raise ProfileValidationError(f"{source}: {where} must be a mapping")
        _reject_unknown_keys(objective, _OBJECTIVE_KEYS, source=source, where=where)
        _require_keys(objective, {"metric", "sense", "units", "weight"}, source=source, where=where)
        metric = objective["metric"]
        if metric not in KNOWN_OBJECTIVE_METRICS:
            raise ProfileValidationError(f"{source}: unknown objective metric {metric!r}")
        if metric in seen:
            raise ProfileValidationError(f"{source}: duplicate objective metric {metric!r}")
        seen.add(metric)
        _validate_weight(objective["weight"], source=source, where=where)
        if "rationale" in objective and not isinstance(objective["rationale"], str):
            raise ProfileValidationError(f"{source}: {where}.rationale must be a string")
    try:
        objective_definitions(profile)
    except ObjectiveProfileError as exc:
        raise ProfileValidationError(f"{source}: {exc}") from exc


def _validate_constraints(raw: Any, *, source: str | Path) -> None:
    if not isinstance(raw, Mapping):
        raise ProfileValidationError(f"{source}: constraints must be a mapping")
    _reject_unknown_keys(raw, _CONSTRAINT_KEYS, source=source, where="constraints")
    gates = raw.get("gates")
    if not isinstance(gates, list) or not gates:
        raise ProfileValidationError(f"{source}: constraints.gates must be a non-empty list")
    known = set(GATE_ORDER)
    for gate in gates:
        if gate not in known:
            raise ProfileValidationError(f"{source}: unknown constraint gate {gate!r}")
    for key in _THRESHOLD_CONSTRAINT_KEYS:
        if key not in raw:
            continue
        _validate_constraint_threshold(raw[key], source=source, where=f"constraints.{key}")
    for key in ("stream_purity_min", "extraction_min_fraction"):
        if key in raw:
            value = float(raw[key])
            if value < 0.0 or value > 1.0:
                raise ProfileValidationError(
                    f"{source}: constraints.{key} must be between 0 and 1"
                )
    if "target_species" in raw:
        _validate_target_species(raw["target_species"], source=source)


def _validate_constraint_threshold(raw: Any, *, source: str | Path, where: str) -> None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ProfileValidationError(f"{source}: {where} must be numeric")
    value = float(raw)
    if not math.isfinite(value) or value <= 0.0:
        raise ProfileValidationError(f"{source}: {where} must be positive finite")


def _validate_target_species(raw: Any, *, source: str | Path) -> None:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, list):
        raise ProfileValidationError(f"{source}: constraints.target_species must be a non-empty list")
    if not raw:
        raise ProfileValidationError(f"{source}: constraints.target_species must be a non-empty list")
    for index, species in enumerate(raw):
        if not isinstance(species, str) or not species.strip():
            raise ProfileValidationError(
                f"{source}: constraints.target_species[{index}] must be a non-empty string"
            )


def _validate_study_constraints(raw: Any, *, source: str | Path) -> None:
    if raw is None:
        return
    if raw not in KNOWN_STUDY_CONSTRAINTS:
        raise ProfileValidationError(f"{source}: unknown study_constraints {raw!r}")


def _validate_run(raw: Any, *, source: str | Path, where: str) -> None:
    if not isinstance(raw, Mapping):
        raise ProfileValidationError(f"{source}: {where} must be a mapping")
    _reject_unknown_keys(raw, _RUN_KEYS, source=source, where=where)
    if "hours" in raw and _bad_positive_number(raw["hours"]):
        raise ProfileValidationError(f"{source}: {where}.hours must be positive")
    if "mass_kg" in raw and _bad_positive_number(raw["mass_kg"]):
        raise ProfileValidationError(f"{source}: {where}.mass_kg must be positive")
    if "c5_enabled" in raw and not isinstance(raw["c5_enabled"], bool):
        raise ProfileValidationError(f"{source}: {where}.c5_enabled must be bool")
    if "mre_max_voltage_V" in raw and _bad_non_negative_number(raw["mre_max_voltage_V"]):
        raise ProfileValidationError(
            f"{source}: {where}.mre_max_voltage_V must be non-negative finite"
        )
    if "mre_target_species" in raw and not isinstance(raw["mre_target_species"], str):
        raise ProfileValidationError(
            f"{source}: {where}.mre_target_species must be a string"
        )
    backend_name = str(raw.get("backend_name", ""))
    cache_config = raw.get("reduced_real_cache")
    if backend_name == "cached-real":
        _validate_reduced_real_cache_config(
            cache_config,
            source=source,
            where=f"{where}.reduced_real_cache",
        )
    elif cache_config is not None:
        raise ProfileValidationError(
            f"{source}: {where}.reduced_real_cache requires "
            "backend_name='cached-real'"
        )


def _validate_reduced_real_cache_config(
    raw: Any,
    *,
    source: str | Path,
    where: str,
) -> None:
    if not isinstance(raw, Mapping):
        raise ProfileValidationError(f"{source}: {where} must be a mapping")
    _reject_unknown_keys(raw, _REDUCED_REAL_CACHE_KEYS, source=source, where=where)
    db_path = raw.get("db_path")
    if not isinstance(db_path, str) or not db_path.strip():
        raise ProfileValidationError(f"{source}: {where}.db_path must be a path string")
    authorized_backend_name = raw.get("authorized_backend_name")
    if (
        not isinstance(authorized_backend_name, str)
        or not authorized_backend_name.strip()
    ):
        raise ProfileValidationError(
            f"{source}: {where}.authorized_backend_name must be a non-empty string"
        )
    authorized_backend_version = raw.get("authorized_backend_version")
    if (
        not isinstance(authorized_backend_version, str)
        or not authorized_backend_version.strip()
    ):
        raise ProfileValidationError(
            f"{source}: {where}.authorized_backend_version must be a non-empty string"
        )
    miss_policy = str(raw.get("miss_policy", "fail-loud")).strip().lower()
    miss_policy = miss_policy.replace("_", "-")
    if miss_policy not in _REDUCED_REAL_MISS_POLICIES:
        raise ProfileValidationError(
            f"{source}: {where}.miss_policy must be one of "
            f"{', '.join(sorted(_REDUCED_REAL_MISS_POLICIES))}"
        )


def _validate_fidelities(
    raw: Any,
    *,
    source: str | Path,
    base_run: Any,
) -> None:
    if not isinstance(raw, Mapping) or not raw:
        raise ProfileValidationError(f"{source}: fidelities must be a non-empty mapping")
    for fidelity, options in raw.items():
        if fidelity not in VALID_FIDELITIES:
            raise ProfileValidationError(f"{source}: unknown fidelity {fidelity!r}")
        if not isinstance(options, Mapping):
            _validate_run(options, source=source, where=f"fidelities.{fidelity}")
            continue
        merged = _merged_run_options_for_validation(base_run, options)
        _validate_run(
            merged,
            source=source,
            where=f"fidelities.{fidelity}",
        )


def _merged_run_options_for_validation(
    base_run: Any,
    selected: Mapping[str, Any],
) -> dict[str, Any]:
    inherited_cache = (
        isinstance(base_run, Mapping)
        and "reduced_real_cache" in base_run
        and "reduced_real_cache" not in selected
    )
    merged = dict(base_run if isinstance(base_run, Mapping) else {})
    merged.update(selected)
    if inherited_cache and str(merged.get("backend_name", "")) != "cached-real":
        merged.pop("reduced_real_cache", None)
    return merged


def _validate_seed_recipes(
    raw: Any,
    *,
    source: str | Path,
    schema: RecipeSchema,
) -> None:
    if not isinstance(raw, list) or not raw:
        raise ProfileValidationError(f"{source}: seed_recipes must be a non-empty list")
    for index, seed in enumerate(raw):
        where = f"seed_recipes[{index}]"
        if not isinstance(seed, Mapping):
            raise ProfileValidationError(f"{source}: {where} must be a mapping")
        _reject_unknown_keys(seed, _SEED_KEYS, source=source, where=where)
        _require_keys(seed, {"id", "patch"}, source=source, where=where)
        if "source_campaign" not in seed and "source_campaigns" not in seed:
            raise ProfileValidationError(
                f"{source}: {where} requires source_campaign or source_campaigns"
            )
        patch = seed["patch"]
        if not isinstance(patch, Mapping):
            raise ProfileValidationError(f"{source}: {where}.patch must be a mapping")
        try:
            RecipePatch.from_nested(patch).validated(schema)
        except RecipeValidationError as exc:
            raise ProfileValidationError(f"{source}: malformed seed recipe: {exc}") from exc


def _validate_weight(raw: Any, *, source: str | Path, where: str) -> None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ProfileValidationError(f"{source}: {where}.weight must be numeric")
    value = float(raw)
    if not math.isfinite(value) or value <= 0.0:
        raise ProfileValidationError(f"{source}: {where}.weight must be positive")


def _bad_positive_number(raw: Any) -> bool:
    return (
        isinstance(raw, bool)
        or not isinstance(raw, (int, float))
        or not math.isfinite(float(raw))
        or float(raw) <= 0.0
    )


def _bad_non_negative_number(raw: Any) -> bool:
    return (
        isinstance(raw, bool)
        or not isinstance(raw, (int, float))
        or not math.isfinite(float(raw))
        or float(raw) < 0.0
    )


def _reject_unknown_keys(
    mapping: Mapping[str, Any],
    allowed: frozenset[str],
    *,
    source: str | Path,
    where: str,
) -> None:
    for key in mapping:
        if key not in allowed:
            raise ProfileValidationError(f"{source}: unknown {where} key {key!r}")


def _profile_source(profile: Mapping[str, Any], source: str | Path | None) -> str:
    if source is not None:
        return str(source)
    return str(getattr(profile, "source", "<profile>"))


def _require_keys(
    mapping: Mapping[str, Any],
    required: set[str],
    *,
    source: str | Path,
    where: str,
) -> None:
    missing = sorted(required - set(mapping))
    if missing:
        raise ProfileValidationError(
            f"{source}: {where} missing required keys: {', '.join(missing)}"
        )
