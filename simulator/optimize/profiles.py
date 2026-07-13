"""Deny-by-default loader for feedstock-specific optimizer profiles."""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from simulator.backend_names import (
    ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
    canonical_backend_name,
)
from simulator.backends import CACHE_TIER_CEILINGS, DEFAULT_CACHE_TIER_CEILING
from simulator.config import DEFAULT_DATA_DIR
from simulator.feedstock_guard import is_blocked_feedstock
from simulator.furnace_materials import FURNACE_MAX_T_BOUNDS_C
from simulator.optimize.objective import (
    COMPOSITION_TARGET_METRIC_PREFIX,
    COMPOSITION_TARGET_TYPE,
    ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
    LEGACY_ENERGY_KWH_METRIC,
    ObjectiveProfileError,
    canonical_objective_metric,
    normalize_composition_target_objective,
    objective_definitions,
    objective_importance_evidence,
    objective_type,
)
from simulator.optimize.physics import GATE_ORDER, PhysicsConstraintSet, ThresholdSpec
from simulator.optimize.product_pools import forbidden_gates_for_pool, product_pool_class
from simulator.optimize.recipe import RecipePatch, RecipeSchema, RecipeValidationError
from simulator.chemistry.kernel.config import normalize_chemistry_kernel_config
from simulator.mre_ladder import max_voltage_for_target, parse_ladder_from_setpoints


PROFILE_SCHEMA_VERSION = "profile-schema-v1"
SSO2_OWNER_RECIPE_ID = "sso2_pn2_fe_drain_silica"
PROFILE_DIRNAME = "optimize_profiles"
VALID_FIDELITIES = (
    ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
    "fast",
    "high",
    "auto",
)
KNOWN_STUDY_CONSTRAINTS = frozenset({"physics"})
DEFAULT_THERMAL_PREHEAT_RAMP_C_PER_HR = 600.0
DEFAULT_COLD_START_TEMPERATURE_C = 25.0
_SETPOINT_CAMPAIGN_ALIASES = {
    "C2A": "C2A_continuous",
    "C2A_staged": "C2A_staged",
    "C3_K": "C3",
    "C3_NA": "C3",
}
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
        LEGACY_ENERGY_KWH_METRIC,
        ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
        "duration_h",
        "total_hours",
        "solar_thermal_flux_h",
        "thermal_flux_h",
        "furnace_time_h",
        "furnace_h",
        "throughput_cost_owner_ratify_usd",
        "furnace_lifespan_consumed_fraction",
        "furnace_lifespan_cost_fraction",
        SSO2_OWNER_RECIPE_ID,
    }
)
CONSTRAINED_MAX_THROUGHPUT_OBJECTIVES: tuple[Mapping[str, Any], ...] = (
    MappingProxyType({
        "metric": "solar_thermal_flux_h",
        "sense": "minimize",
        "units": "K*h",
        "weight": 0.2,
        "rationale": (
            "SSO-3 throughput cost: solar concentrator temperature-time burden"
        ),
    }),
    MappingProxyType({
        "metric": "furnace_lifespan_consumed_fraction",
        "sense": "minimize",
        "units": "fraction/run",
        "weight": 0.2,
        "rationale": (
            "SSO-3 throughput cost: continuous wall-deposition rate consumes "
            "furnace service life without hard-blocking the run"
        ),
    }),
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
        "pinned_paths",
        "early_tap_mode",
        "staged",
        "staged_strategy",
        "two_phase_certify",
        "warm_start_from",
        "code_version",
        "corpus_version",
    }
)
_OBJECTIVE_KEYS = frozenset({"metric", "sense", "units", "weight", "rationale"})
_LEGACY_OBJECTIVE_KEYS = _OBJECTIVE_KEYS | frozenset({"type"})
_THRESHOLD_CONSTRAINT_KEYS = MappingProxyType({
    "stream_purity_min": "stream_purity_min",
    "coating_min_campaigns_to_resinter": "coating_min_campaigns_to_resinter",
    "extraction_min_fraction": "extraction_min_fraction",
    "knudsen_max": "knudsen_max",
    "furnace_T_max_C": "furnace_T_max_C",
    "cycle_time_max_h": "cycle_time_max_h",
})
_CONSTRAINT_KEYS = frozenset({
    "gates",
    "target_species",
    *_THRESHOLD_CONSTRAINT_KEYS,
})
_SEED_KEYS = frozenset(
    {
        "id",
        "source_campaign",
        "source_campaigns",
        "rationale",
        "patch",
        "topology",
        "topology_id",
    }
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
        "lab_schedule",
        "chemistry_kernel",
        "lab_overlay_scope",
        "lab_overlay",
        "allow_fallback_vapor",
        "force_builtin_vapor_pressure",
        "lab_alpha_digest",
        "geometry_digest",
        "effective_exposed_area_m2",
        "area_basis",
        "oxide_vapor_ceiling_digest",
        "sink_channel_evidence_digests",
        "cache_tier_ceiling",
        "miss_policy",
    }
)
_LAB_OVERLAY_SCOPE_KEYS = frozenset(
    {
        "lab_alpha_digest",
        "geometry_digest",
        "effective_exposed_area_m2",
        "area_basis",
        "oxide_vapor_ceiling_digest",
        "sink_channel_evidence_digests",
    }
)
_REDUCED_REAL_CACHE_KEYS = frozenset({
    "db_path",
    "miss_policy",
    "authorized_backend_name",
    "authorized_model",
    "authorized_mode",
    # Provenance/version-authority key consumed by the cached-real runtime
    # (backends.py cached-real config). Added to the generated real profiles +
    # runtime by 7c490d4/8d09d4f but omitted from this validator allowlist —
    # every real grind profile carries it, so its omission fails load_profile()
    # with "unknown run.reduced_real_cache key" (grind-infra sweep Finding 3).
    "authorized_backend_version",
    "cache_tier_ceiling",
    "read_only_base_db_path",
})
_TWO_PHASE_CERTIFY_KEYS = frozenset({
    "enabled",
    "top_k",
    "disagreement_threshold",
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
    expected = set(_feedstock_ids(data_root, include_blocked=False))
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
    profile = canonicalize_profile_backend_names(raw, source=source)
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
    _validate_pool_scoped_constraints(profile, source=source)
    _validate_study_constraints(profile.get("study_constraints"), source=source)
    _validate_run(profile["run"], source=source, where="run")
    _validate_fidelities(
        profile["fidelities"],
        source=source,
        base_run=profile["run"],
    )
    _validate_thermal_window_caps(profile, source=source)
    _validate_two_phase_certify(profile.get("two_phase_certify"), source=source)
    _validate_pinned_paths(profile.get("pinned_paths"), source=source)
    _validate_warm_start_from(profile.get("warm_start_from"), source=source)
    _validate_profile_epoch_stamps(profile, source=source)
    _validate_seed_recipes(
        profile["seed_recipes"],
        source=source,
        schema=schema or RecipeSchema(),
    )
    if "early_tap_mode" in profile and not isinstance(profile["early_tap_mode"], bool):
        raise ProfileValidationError(f"{source}: early_tap_mode must be boolean")
    return ValidatedProfile(profile, source=source)


def canonicalize_profile_backend_names(
    raw: Mapping[str, Any],
    *,
    source: str | Path,
) -> dict[str, Any]:
    profile = _copy_profile_value(raw)
    run = profile.get("run")
    if isinstance(run, Mapping) and "backend_name" in run:
        normalized_run = dict(run)
        normalized_run["backend_name"] = canonical_backend_name(
            str(run["backend_name"])
        )
        profile["run"] = normalized_run
    fidelities = profile.get("fidelities")
    if not isinstance(fidelities, Mapping):
        return profile
    normalized_fidelities: dict[str, Any] = {}
    for raw_name, raw_options in fidelities.items():
        name = str(canonical_backend_name(str(raw_name)))
        if name in normalized_fidelities:
            raise ProfileValidationError(
                f"{source}: duplicate fidelity after alias normalization {name!r}"
            )
        options = _copy_profile_value(raw_options)
        if isinstance(options, Mapping) and "backend_name" in options:
            options = dict(options)
            options["backend_name"] = canonical_backend_name(
                str(options["backend_name"])
            )
        normalized_fidelities[name] = options
    profile["fidelities"] = normalized_fidelities
    return profile


def _copy_profile_value(value: Any) -> Any:
    """Copy authored profile data without pickling immutable mapping views."""
    if isinstance(value, Mapping):
        return {key: _copy_profile_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_profile_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_copy_profile_value(item) for item in value)
    if isinstance(value, set):
        return {_copy_profile_value(item) for item in value}
    if isinstance(value, frozenset):
        return frozenset(_copy_profile_value(item) for item in value)
    return copy.deepcopy(value)


def constrained_max_profile(
    profile: Mapping[str, Any],
    *,
    furnace_T_max_C: float | None = None,
    cycle_time_max_h: float | None = None,
    include_throughput_cost: bool = True,
) -> Mapping[str, Any]:
    """Return a profile overlay for yield under explicit hardware ceilings."""

    overlaid = copy.deepcopy(dict(profile))
    profile_id = str(overlaid.get("profile_id") or overlaid.get("id") or "profile")
    if not profile_id.endswith("-constrained-max"):
        overlaid["profile_id"] = f"{profile_id}-constrained-max"

    constraints = dict(overlaid.get("constraints", {}) or {})
    gates = [str(gate) for gate in constraints.get("gates", [])]
    gates = [gate for gate in gates if gate != "coating"]
    if furnace_T_max_C is not None:
        constraints["furnace_T_max_C"] = float(furnace_T_max_C)
        if "furnace_temperature" not in gates:
            gates.append("furnace_temperature")
    if cycle_time_max_h is not None:
        constraints["cycle_time_max_h"] = float(cycle_time_max_h)
        if "cycle_time" not in gates:
            gates.append("cycle_time")
    constraints["gates"] = gates
    overlaid["constraints"] = constraints

    objectives = [
        _coating_target_as_cost_objective(objective)
        for objective in overlaid.get("objectives", [])
    ]
    if include_throughput_cost:
        seen = {
            str(objective.get("metric"))
            for objective in objectives
            if isinstance(objective, Mapping)
        }
        for objective in CONSTRAINED_MAX_THROUGHPUT_OBJECTIVES:
            if str(objective["metric"]) not in seen:
                objectives.append(dict(objective))
                seen.add(str(objective["metric"]))
    overlaid["objectives"] = objectives
    return MappingProxyType(overlaid)


def _coating_target_as_cost_objective(objective: Any) -> Any:
    if not isinstance(objective, Mapping):
        return objective
    normalized = copy.deepcopy(dict(objective))
    if objective_type(normalized) != COMPOSITION_TARGET_TYPE:
        return normalized
    target = normalized.get("target")
    if isinstance(target, Mapping):
        target_copy = copy.deepcopy(dict(target))
        target_copy["require_coating_gate"] = False
        normalized["target"] = target_copy
    return normalized


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
    _validate_pool_scoped_constraints(profile, source=profile_source)
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


def _feedstock_ids(data_dir: Path, *, include_blocked: bool = True) -> tuple[str, ...]:
    path = data_dir / "feedstocks.yaml"
    loaded = yaml.safe_load(path.read_text())
    if not isinstance(loaded, Mapping):
        raise ProfileValidationError(f"{path}: feedstocks.yaml must be a mapping")
    return tuple(
        str(key)
        for key, feedstock in loaded.items()
        if include_blocked or not is_blocked_feedstock(feedstock)
    )


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
    normalized_objectives: list[Mapping[str, Any]] = []
    for index, objective in enumerate(raw_objectives):
        where = f"objectives[{index}]"
        if not isinstance(objective, Mapping):
            raise ProfileValidationError(f"{source}: {where} must be a mapping")
        kind = objective_type(objective)
        if kind == COMPOSITION_TARGET_TYPE:
            try:
                normalized = normalize_composition_target_objective(
                    objective,
                    where=where,
                )
            except ObjectiveProfileError as exc:
                raise ProfileValidationError(f"{source}: {exc}") from exc
            metric = normalized["metric"]
            if not str(metric).startswith(COMPOSITION_TARGET_METRIC_PREFIX):
                raise ProfileValidationError(
                    f"{source}: {where}.metric must be a composition_target key"
                )
            normalized_objectives.append(normalized)
        elif kind == "legacy_metric":
            _reject_unknown_keys(objective, _LEGACY_OBJECTIVE_KEYS, source=source, where=where)
            if "type" in objective and objective["type"] != "legacy_metric":
                raise ProfileValidationError(f"{source}: unknown objective type {kind!r}")
            _require_keys(objective, {"metric", "sense", "units"}, source=source, where=where)
            metric = str(objective["metric"])
            if metric not in KNOWN_OBJECTIVE_METRICS:
                raise ProfileValidationError(f"{source}: unknown objective metric {metric!r}")
            metric = canonical_objective_metric(metric)
            normalized = dict(objective)
            normalized["metric"] = metric
            normalized_objectives.append(normalized)
        else:
            raise ProfileValidationError(f"{source}: unknown objective type {kind!r}")
        _require_keys(objective, {"metric", "sense", "units"}, source=source, where=where)
        if metric in seen:
            raise ProfileValidationError(f"{source}: duplicate objective metric {metric!r}")
        seen.add(metric)
        if "rationale" in objective and not isinstance(objective["rationale"], str):
            raise ProfileValidationError(f"{source}: {where}.rationale must be a string")
    if isinstance(profile, dict):
        profile["objectives"] = normalized_objectives
    try:
        objective_importance_evidence(profile)
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
    if "furnace_T_max_C" in raw:
        _validate_furnace_temperature_cap(
            raw["furnace_T_max_C"],
            source=source,
            where="constraints.furnace_T_max_C",
        )
    for key in ("stream_purity_min", "extraction_min_fraction"):
        if key in raw:
            value = float(raw[key])
            if value < 0.0 or value > 1.0:
                raise ProfileValidationError(
                    f"{source}: constraints.{key} must be between 0 and 1"
                )
    if "target_species" in raw:
        _validate_target_species(raw["target_species"], source=source)


def _validate_pinned_paths(raw: Any, *, source: str | Path) -> None:
    if raw is None:
        return
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise ProfileValidationError(
            f"{source}: pinned_paths must be a list of dotted paths"
        )
    seen: set[str] = set()
    for index, path in enumerate(raw):
        where = f"pinned_paths[{index}]"
        if not isinstance(path, str) or not path or path.strip() != path:
            raise ProfileValidationError(
                f"{source}: {where} must be a non-empty dotted path string"
            )
        if any(not segment for segment in path.split(".")):
            raise ProfileValidationError(
                f"{source}: {where} must not contain empty path segments"
            )
        if path in seen:
            raise ProfileValidationError(f"{source}: duplicate pinned path {path!r}")
        seen.add(path)


def _validate_warm_start_from(raw: Any, *, source: str | Path) -> None:
    if raw is None:
        return
    if isinstance(raw, str):
        if not raw.strip():
            raise ProfileValidationError(f"{source}: warm_start_from must not be empty")
        return
    if not isinstance(raw, Mapping):
        raise ProfileValidationError(
            f"{source}: warm_start_from must be a path string or mapping"
        )
    allowed = {
        "path",
        "run_dir",
        "store",
        "store_path",
        "pareto",
        "pareto_path",
        "artifact",
        "artifact_path",
    }
    _reject_unknown_keys(raw, allowed, source=source, where="warm_start_from")
    if not any(key in raw for key in allowed):
        raise ProfileValidationError(
            f"{source}: warm_start_from must name a prior run dir, store, or pareto artifact"
        )
    for key, value in raw.items():
        if not isinstance(value, str) or not value.strip():
            raise ProfileValidationError(
                f"{source}: warm_start_from.{key} must be a non-empty path string"
            )


def _validate_profile_epoch_stamps(profile: Mapping[str, Any], *, source: str | Path) -> None:
    for key in ("code_version", "corpus_version"):
        if key in profile and not isinstance(profile[key], str):
            raise ProfileValidationError(f"{source}: {key} must be a string")


def _validate_pool_scoped_constraints(profile: Mapping[str, Any], *, source: str | Path) -> None:
    raw_constraints = profile.get("constraints", {})
    if not isinstance(raw_constraints, Mapping):
        return
    gates = raw_constraints.get("gates")
    if not isinstance(gates, list):
        return
    active_gates = {str(gate) for gate in gates}
    objectives = profile.get("objectives", ())
    if not isinstance(objectives, list):
        return
    for index, objective in enumerate(objectives):
        if not isinstance(objective, Mapping):
            continue
        if objective_type(objective) != COMPOSITION_TARGET_TYPE:
            continue
        target = objective.get("target")
        if not isinstance(target, Mapping):
            continue
        pool = str(target.get("pool", ""))
        try:
            pool_class = product_pool_class(pool)
        except ValueError:
            continue
        for gate in forbidden_gates_for_pool(pool):
            if gate in active_gates:
                raise ProfileValidationError(
                    f"{source}: constraints.gates contains out-of-policy gate {gate!r} "
                    f"for {pool_class} target pool {pool!r}; regenerate with "
                    "FORCE_PROFILES=1"
                )


def _validate_constraint_threshold(raw: Any, *, source: str | Path, where: str) -> None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ProfileValidationError(f"{source}: {where} must be numeric")
    value = float(raw)
    if not math.isfinite(value) or value <= 0.0:
        raise ProfileValidationError(f"{source}: {where} must be positive finite")


def _validate_furnace_temperature_cap(
    raw: Any,
    *,
    source: str | Path,
    where: str,
) -> None:
    value = float(raw)
    low, high = FURNACE_MAX_T_BOUNDS_C
    if value < low or value > high:
        raise ProfileValidationError(
            f"{source}: {where} must be within hardware envelope "
            f"[{low:.0f}, {high:.0f}] degC"
        )


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
    if "allow_fallback_vapor" in raw and not isinstance(
        raw["allow_fallback_vapor"],
        bool,
    ):
        raise ProfileValidationError(
            f"{source}: {where}.allow_fallback_vapor must be bool"
        )
    if "force_builtin_vapor_pressure" in raw and not isinstance(
        raw["force_builtin_vapor_pressure"],
        bool,
    ):
        raise ProfileValidationError(
            f"{source}: {where}.force_builtin_vapor_pressure must be bool"
        )
    if "chemistry_kernel" in raw:
        try:
            normalize_chemistry_kernel_config(raw["chemistry_kernel"])
        except (TypeError, ValueError) as exc:
            raise ProfileValidationError(
                f"{source}: {where}.chemistry_kernel.{exc}"
            ) from exc
    _validate_lab_overlay_scope(raw, source=source, where=where)
    _validate_c5_request(raw, source=source, where=where)
    if "cache_tier_ceiling" in raw:
        cache_tier_ceiling = str(raw["cache_tier_ceiling"]).strip()
        if cache_tier_ceiling not in CACHE_TIER_CEILINGS:
            raise ProfileValidationError(
                f"{source}: {where}.cache_tier_ceiling must be one of "
                f"{', '.join(CACHE_TIER_CEILINGS)}"
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


def _validate_lab_overlay_scope(raw: Mapping[str, Any], *, source: str | Path, where: str) -> None:
    for scope_key in ("lab_overlay_scope", "lab_overlay"):
        scope = raw.get(scope_key)
        if scope is None:
            continue
        if not isinstance(scope, Mapping):
            raise ProfileValidationError(f"{source}: {where}.{scope_key} must be a mapping")
        _reject_unknown_keys(
            scope,
            _LAB_OVERLAY_SCOPE_KEYS,
            source=source,
            where=f"{where}.{scope_key}",
        )
        _validate_lab_overlay_scope(scope, source=source, where=f"{where}.{scope_key}")
    for field in (
        "lab_alpha_digest",
        "geometry_digest",
        "area_basis",
        "oxide_vapor_ceiling_digest",
    ):
        if field in raw and raw[field] is not None and not isinstance(raw[field], str):
            raise ProfileValidationError(f"{source}: {where}.{field} must be a string")
    if (
        "effective_exposed_area_m2" in raw
        and raw["effective_exposed_area_m2"] is not None
        and _bad_positive_number(raw["effective_exposed_area_m2"])
    ):
        raise ProfileValidationError(
            f"{source}: {where}.effective_exposed_area_m2 must be positive finite"
        )
    digests = raw.get("sink_channel_evidence_digests")
    if digests is not None:
        if not isinstance(digests, Mapping):
            raise ProfileValidationError(
                f"{source}: {where}.sink_channel_evidence_digests must be a mapping"
            )
        for key, value in digests.items():
            if not isinstance(key, str) or not isinstance(value, str) or not value:
                raise ProfileValidationError(
                    f"{source}: {where}.sink_channel_evidence_digests must map strings to non-empty strings"
                )


def _validate_c5_request(raw: Mapping[str, Any], *, source: str | Path, where: str) -> None:
    if not bool(raw.get("c5_enabled", False)):
        return
    max_voltage = float(raw.get("mre_max_voltage_V", 0.0) or 0.0)
    if max_voltage > 0.0:
        return
    target = str(raw.get("mre_target_species", "") or "").strip()
    sequence = _canonical_mre_ladder_for_profile(source)
    if target and max_voltage_for_target(target, sequence) > 0.0:
        return
    raise ProfileValidationError(
        f"{source}: {where}.c5_enabled requires positive mre_max_voltage_V "
        "or canonical mre_target_species; invalid "
        f"{where}.mre_target_species {target!r}"
    )


def _canonical_mre_ladder_for_profile(source: str | Path) -> list[dict[str, Any]]:
    source_path = Path(source)
    data_dir = DEFAULT_DATA_DIR
    if source_path.parent.name == PROFILE_DIRNAME:
        data_dir = source_path.parent.parent
    setpoints_path = data_dir / "setpoints.yaml"
    try:
        loaded = yaml.safe_load(setpoints_path.read_text())
    except OSError as exc:
        raise ProfileValidationError(
            f"{source}: cannot read {setpoints_path} for C5 MRE target validation"
        ) from exc
    except yaml.YAMLError as exc:
        raise ProfileValidationError(
            f"{source}: invalid {setpoints_path} for C5 MRE target validation"
        ) from exc
    if not isinstance(loaded, Mapping):
        raise ProfileValidationError(
            f"{source}: {setpoints_path} must be a mapping for C5 MRE target validation"
        )
    return parse_ladder_from_setpoints(dict(loaded))


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
    for key in ("authorized_model", "authorized_mode"):
        value = raw.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ProfileValidationError(
                f"{source}: {where}.{key} must be a non-empty string"
            )
    miss_policy = str(raw.get("miss_policy", "fail-loud")).strip().lower()
    miss_policy = miss_policy.replace("_", "-")
    if miss_policy not in _REDUCED_REAL_MISS_POLICIES:
        raise ProfileValidationError(
            f"{source}: {where}.miss_policy must be one of "
            f"{', '.join(sorted(_REDUCED_REAL_MISS_POLICIES))}"
        )
    cache_tier_ceiling = str(
        raw.get("cache_tier_ceiling", DEFAULT_CACHE_TIER_CEILING)
    ).strip()
    if cache_tier_ceiling not in CACHE_TIER_CEILINGS:
        raise ProfileValidationError(
            f"{source}: {where}.cache_tier_ceiling must be one of "
            f"{', '.join(CACHE_TIER_CEILINGS)}"
        )


def _validate_two_phase_certify(
    raw: Any,
    *,
    source: str | Path,
) -> None:
    if raw is None:
        return
    if not isinstance(raw, Mapping):
        raise ProfileValidationError(f"{source}: two_phase_certify must be a mapping")
    _reject_unknown_keys(
        raw,
        _TWO_PHASE_CERTIFY_KEYS,
        source=source,
        where="two_phase_certify",
    )
    if "top_k" in raw:
        top_k = raw["top_k"]
        if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k <= 0:
            raise ProfileValidationError(
                f"{source}: two_phase_certify.top_k must be a positive integer"
            )
    if "disagreement_threshold" in raw:
        threshold = raw["disagreement_threshold"]
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
            raise ProfileValidationError(
                f"{source}: two_phase_certify.disagreement_threshold must be a number"
            )
        if not math.isfinite(float(threshold)) or float(threshold) < 0.0:
            raise ProfileValidationError(
                f"{source}: two_phase_certify.disagreement_threshold must be finite "
                "and non-negative"
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


def _validate_thermal_window_caps(profile: Mapping[str, Any], *, source: str | Path) -> None:
    run_options = profile.get("run")
    if isinstance(run_options, Mapping):
        _validate_run_thermal_window_caps(
            profile,
            run_options,
            source=source,
            where="run",
        )
    fidelities = profile.get("fidelities")
    if not isinstance(fidelities, Mapping):
        return
    for fidelity, options in fidelities.items():
        if not isinstance(options, Mapping):
            continue
        merged = _merged_run_options_for_validation(run_options, options)
        _validate_run_thermal_window_caps(
            profile,
            merged,
            source=source,
            where=f"fidelities.{fidelity}",
        )


def _validate_run_thermal_window_caps(
    profile: Mapping[str, Any],
    run_options: Mapping[str, Any],
    *,
    source: str | Path,
    where: str,
) -> None:
    campaign = str(run_options.get("campaign", "") or "")
    if not campaign:
        return
    temp_range = _profile_campaign_setting(profile, campaign, "temp_range_C")
    bounds = _numeric_interval_optional(temp_range)
    if bounds is None:
        return
    low_C, high_C = bounds
    if high_C < low_C:
        raise ProfileValidationError(
            f"{source}: {campaign}.temp_range_C must be ascending"
        )
    run_hours = int(float(run_options.get("hours", 24)))
    duration_h = _thermal_window_duration_h(
        _profile_campaign_setting(profile, campaign, "duration_h"),
        run_hours=run_hours,
    )
    preheat_ramp = _thermal_preheat_ramp_C_per_hr(profile, campaign)
    preheat_hours = int(
        math.ceil(
            max(0.0, low_C - DEFAULT_COLD_START_TEMPERATURE_C) / preheat_ramp
        )
    )
    total_hours = int(math.ceil(preheat_hours + duration_h))
    max_hold_hr = _campaign_max_hold_hr_for_profile(source, campaign)
    if max_hold_hr is None or float(total_hours) <= max_hold_hr:
        return
    raise ProfileValidationError(
        f"{source}: thermal_window_campaign_max_hold refusal for {where} "
        f"{campaign}: requested {total_hours:g} h "
        f"(preheat {preheat_hours:g} h + hold {duration_h:g} h) exceeds "
        f"{_setpoint_campaign_key(campaign)}.max_hold_hr {max_hold_hr:g}; "
        "regenerate with FORCE_PROFILES=1"
    )


def _profile_campaign_setting(
    profile: Mapping[str, Any],
    campaign: str,
    key: str,
) -> Any:
    run_value = _campaign_setting(profile.get("run"), campaign, key)
    if run_value is not None:
        return run_value
    for seed in profile.get("seed_recipes", ()) or ():
        if not isinstance(seed, Mapping):
            continue
        if campaign not in seed_source_campaigns(seed):
            continue
        value = _campaign_setting(seed.get("patch"), campaign, key)
        if value is not None:
            return value
    return None


def _campaign_setting(source: Any, campaign: str, key: str) -> Any:
    if not isinstance(source, Mapping):
        return None
    campaigns = source.get("campaigns")
    if isinstance(campaigns, Mapping):
        selected = campaigns.get(campaign)
        if isinstance(selected, Mapping) and key in selected:
            return selected[key]
    selected = source.get(campaign)
    if isinstance(selected, Mapping) and key in selected:
        return selected[key]
    return source.get(key)


def seed_source_campaigns(seed: Mapping[str, Any]) -> frozenset[str]:
    campaigns: set[str] = set()
    source_campaign = seed.get("source_campaign")
    if source_campaign is not None:
        campaigns.add(str(source_campaign))
    source_campaigns = seed.get("source_campaigns")
    if isinstance(source_campaigns, list):
        campaigns.update(str(campaign) for campaign in source_campaigns)
    return frozenset(campaigns)


def _thermal_window_duration_h(value: Any, *, run_hours: int) -> float:
    interval = _numeric_interval_optional(value)
    if interval is None:
        return float(run_hours)
    low, high = interval
    if high < low:
        raise ProfileValidationError(f"duration_h must be ascending; got {value!r}")
    if low <= float(run_hours) <= high:
        return float(run_hours)
    if low == high:
        return low
    return (low + high) / 2.0


def _thermal_preheat_ramp_C_per_hr(profile: Mapping[str, Any], campaign: str) -> float:
    value = _profile_campaign_setting(profile, campaign, "preheat_ramp_C_per_hr")
    if value is None:
        value = _profile_campaign_setting(profile, campaign, "ramp_rate_C_per_hr")
    numeric = _numeric_setting(value, sequence_policy="max")
    if numeric is None:
        return DEFAULT_THERMAL_PREHEAT_RAMP_C_PER_HR
    if numeric <= 0.0:
        raise ProfileValidationError(
            f"{campaign}.preheat_ramp_C_per_hr must be positive"
        )
    return numeric


def _campaign_max_hold_hr_for_profile(source: str | Path, campaign: str) -> float | None:
    data_dir = _data_dir_for_profile_source(source)
    setpoints_path = data_dir / "setpoints.yaml"
    try:
        loaded = yaml.safe_load(setpoints_path.read_text())
    except OSError as exc:
        raise ProfileValidationError(
            f"{source}: cannot read {setpoints_path} for thermal window validation"
        ) from exc
    except yaml.YAMLError as exc:
        raise ProfileValidationError(
            f"{source}: invalid {setpoints_path} for thermal window validation"
        ) from exc
    if not isinstance(loaded, Mapping):
        raise ProfileValidationError(
            f"{source}: {setpoints_path} must be a mapping for thermal window validation"
        )
    campaigns = loaded.get("campaigns")
    if not isinstance(campaigns, Mapping):
        return None
    cfg = campaigns.get(_setpoint_campaign_key(campaign))
    if not isinstance(cfg, Mapping):
        return None
    value = cfg.get("max_hold_hr")
    if value is None or isinstance(value, bool) or isinstance(value, Mapping):
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(amount) or amount <= 0.0:
        return None
    return amount


def _data_dir_for_profile_source(source: str | Path) -> Path:
    source_path = Path(source)
    if source_path.parent.name == PROFILE_DIRNAME:
        return source_path.parent.parent
    return DEFAULT_DATA_DIR


def _setpoint_campaign_key(campaign: str) -> str:
    return _SETPOINT_CAMPAIGN_ALIASES.get(campaign, campaign)


def _numeric_interval_optional(value: Any) -> tuple[float, float] | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        numeric = _numeric_setting(value, sequence_policy="max")
        if numeric is None:
            return None
        return numeric, numeric
    values = [
        numeric
        for item in value
        if (numeric := _finite_float_or_none(item)) is not None
    ]
    if not values:
        return None
    return min(values), max(values)


def _numeric_setting(value: Any, *, sequence_policy: str) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (tuple, list)):
        values = [
            numeric
            for item in value
            if (numeric := _finite_float_or_none(item)) is not None
        ]
        if not values:
            return None
        if sequence_policy == "max":
            return max(values)
        if sequence_policy == "min":
            return min(values)
        return sum(values) / len(values)
    return _finite_float_or_none(value)


def _finite_float_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


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
        if "source_campaigns" in seed:
            source_campaigns = seed["source_campaigns"]
            if not isinstance(source_campaigns, list) or not source_campaigns:
                raise ProfileValidationError(
                    f"{source}: {where}.source_campaigns must be a non-empty "
                    f"list of campaign names; got {source_campaigns!r}"
                )
            for entry in source_campaigns:
                if not isinstance(entry, str) or not entry.strip():
                    raise ProfileValidationError(
                        f"{source}: {where}.source_campaigns entries must be "
                        f"non-empty strings; got {entry!r}"
                    )
        if "topology" in seed and "topology_id" in seed:
            raise ProfileValidationError(
                f"{source}: {where} must use topology or topology_id, not both"
            )
        for topology_key in ("topology", "topology_id"):
            if topology_key not in seed:
                continue
            topology_value = seed[topology_key]
            if not isinstance(topology_value, (str, Mapping)):
                raise ProfileValidationError(
                    f"{source}: {where}.{topology_key} must be a topology id or mapping"
                )
        patch = seed["patch"]
        if not isinstance(patch, Mapping):
            raise ProfileValidationError(f"{source}: {where}.patch must be a mapping")
        try:
            RecipePatch.from_nested(patch).validated(schema)
        except RecipeValidationError as exc:
            raise ProfileValidationError(f"{source}: malformed seed recipe: {exc}") from exc


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
