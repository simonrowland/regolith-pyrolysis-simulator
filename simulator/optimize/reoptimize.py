"""Re-optimize a saved study from manifest + study.profile.yaml only."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import yaml

from simulator.backend_names import canonical_backend_name
from simulator.config import DEFAULT_DATA_DIR
from simulator.optimize.objective import (
    COMPOSITION_TARGET_METRIC_PREFIX,
    canonical_objective_metric,
)
from simulator.optimize.physics import GATE_ORDER
from simulator.optimize.profiles import KNOWN_OBJECTIVE_METRICS, PROFILE_DIRNAME
from simulator.optimize.recipe import PATH_ALIASES, RecipePatch, RecipeSchema, RecipeValidationError

GOALS_SOURCE_BUNDLED = "bundled_profile"
GOALS_SOURCE_CURRENT = "current_local_profile"
GOALS_SOURCES = frozenset({GOALS_SOURCE_BUNDLED, GOALS_SOURCE_CURRENT})
MANIFEST_NAME = "study.manifest.json"
PROFILE_NAME = "study.profile.yaml"

_STRATEGY_ALIASES = {
    "RandomStrategy": "random",
    "random": "random",
    "MorrisScreenStrategy": "screen",
    "morris-screen": "screen",
    "screen": "screen",
    "StagedStrategy": "staged",
    "staged": "staged",
    "OptunaTPEStrategy": "bayes",
    "optuna-tpe": "bayes",
    "bayes": "bayes",
    "OptunaNSGA2Strategy": "nsga2",
    "optuna-nsga2": "nsga2",
    "nsga2": "nsga2",
}


class ReoptimizeError(ValueError):
    """Raised when a re-optimize request cannot be prepared or submitted."""


class ReoptimizeVocabularyDriftError(ReoptimizeError):
    """Raised when a bundled profile names identifiers absent from current vocabulary."""

    def __init__(self, identifiers: Sequence[str]) -> None:
        unique = tuple(sorted(dict.fromkeys(str(item) for item in identifiers if item)))
        self.identifiers = unique
        listed = ", ".join(unique) if unique else "<none>"
        super().__init__(
            "profile vocabulary drift; unknown identifiers: " + listed
        )


@dataclass(frozen=True)
class ReoptimizePrefill:
    source_study_id: str
    feedstock_id: str
    profile_id: str
    strategy: str | None
    seed: int | None
    budget: int | None
    fidelity: str | None
    parallel: int | None
    drifted_identifiers: tuple[str, ...]


@dataclass(frozen=True)
class ReoptimizePlan:
    source_study_id: str
    goals_source: str
    feedstock_id: str
    profile_id: str
    profile_arg: str
    strategy: str
    seed: int
    budget: int
    fidelity: str
    parallel: int
    reoptimized_from: str


def load_reoptimize_prefill(source_dir: Path | str) -> ReoptimizePrefill:
    """Read operator-visible run params from manifest + profile. Never opens sqlite."""

    manifest, profile, source_dir = _load_source(source_dir)
    source_study_id = _source_study_id(manifest)
    return ReoptimizePrefill(
        source_study_id=source_study_id,
        feedstock_id=_source_feedstock_id(manifest, profile),
        profile_id=_source_profile_id(manifest, profile),
        strategy=_strategy_key(manifest),
        seed=_optional_int(manifest.get("seed")),
        budget=_optional_int(manifest.get("budget")),
        fidelity=_optional_fidelity(manifest.get("fidelity")),
        parallel=_optional_int(manifest.get("parallel")),
        drifted_identifiers=collect_vocabulary_drift(profile),
    )


def plan_reoptimize(
    source_dir: Path | str,
    *,
    goals_source: str,
    strategy: str,
    seed: int,
    budget: int,
    fidelity: str,
    parallel: int,
    data_dir: Path | str = DEFAULT_DATA_DIR,
) -> ReoptimizePlan:
    """Build a new-study plan from an operator-submitted re-optimize request.

    Run params are the operator values. They are never filled from the bundle
    here; prefill is a separate read used to populate the form.
    """

    resolved_goals = _require_goals_source(goals_source)
    manifest, profile, source_dir = _load_source(source_dir)
    source_study_id = _source_study_id(manifest)
    feedstock_id = _source_feedstock_id(manifest, profile)
    if resolved_goals == GOALS_SOURCE_BUNDLED:
        drifted = collect_vocabulary_drift(profile)
        if drifted:
            raise ReoptimizeVocabularyDriftError(drifted)
        profile_id = _source_profile_id(manifest, profile)
        profile_arg = str(source_dir / PROFILE_NAME)
    else:
        profile_path, profile_id = _current_local_profile(
            profile_id=_source_profile_id(manifest, profile),
            feedstock_id=feedstock_id,
            data_dir=Path(data_dir),
        )
        profile_arg = str(profile_path)
    return ReoptimizePlan(
        source_study_id=source_study_id,
        goals_source=resolved_goals,
        feedstock_id=feedstock_id,
        profile_id=profile_id,
        profile_arg=profile_arg,
        strategy=str(strategy),
        seed=int(seed),
        budget=int(budget),
        fidelity=str(canonical_backend_name(str(fidelity))),
        parallel=int(parallel),
        reoptimized_from=source_study_id,
    )


def collect_vocabulary_drift(profile: Mapping[str, Any]) -> tuple[str, ...]:
    """Return every named knob path, gate, or objective metric missing from current vocabulary."""

    known_knobs = _known_knob_paths()
    known_gates = set(GATE_ORDER)
    drifted: list[str] = []
    seen: set[str] = set()

    def note(identifier: str) -> None:
        if identifier and identifier not in seen:
            seen.add(identifier)
            drifted.append(identifier)

    for path in _profile_knob_paths(profile):
        if path not in known_knobs:
            note(path)
    constraints = profile.get("constraints")
    if isinstance(constraints, Mapping):
        gates = constraints.get("gates")
        if isinstance(gates, Sequence) and not isinstance(gates, (str, bytes)):
            for gate in gates:
                name = str(gate)
                if name and name not in known_gates:
                    note(name)
    objectives = profile.get("objectives")
    if isinstance(objectives, Sequence) and not isinstance(objectives, (str, bytes)):
        for objective in objectives:
            if not isinstance(objective, Mapping):
                continue
            metric = objective.get("metric")
            if metric is None or metric == "":
                continue
            name = str(metric)
            if not _objective_metric_known(name):
                note(name)
    return tuple(drifted)


def _load_source(source_dir: Path | str) -> tuple[dict[str, Any], dict[str, Any], Path]:
    root = Path(source_dir)
    if not root.is_dir():
        raise ReoptimizeError(f"re-optimize source directory not found: {root}")
    manifest = _load_json_mapping(root / MANIFEST_NAME)
    profile = _load_yaml_mapping(root / PROFILE_NAME)
    return manifest, profile, root


def _load_json_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ReoptimizeError(f"re-optimize requires {path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReoptimizeError(f"{path.name} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ReoptimizeError(f"{path.name} must be a mapping")
    return payload


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ReoptimizeError(f"re-optimize requires {path.name}")
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ReoptimizeError(f"{path.name} is not valid YAML") from exc
    if not isinstance(payload, dict):
        raise ReoptimizeError(f"{path.name} must be a mapping")
    return payload


def _source_study_id(manifest: Mapping[str, Any]) -> str:
    raw = manifest.get("study_id")
    if raw is None or str(raw).strip() == "":
        raise ReoptimizeError("study.manifest.json missing study_id")
    return str(raw)


def _source_feedstock_id(manifest: Mapping[str, Any], profile: Mapping[str, Any]) -> str:
    raw = (
        manifest.get("feedstock_id")
        or profile.get("feedstock")
        or profile.get("feedstock_id")
    )
    if raw is None or str(raw).strip() == "":
        raise ReoptimizeError("re-optimize source missing feedstock_id")
    return str(raw)


def _source_profile_id(manifest: Mapping[str, Any], profile: Mapping[str, Any]) -> str:
    nested = manifest.get("profile")
    nested_id = None
    if isinstance(nested, Mapping):
        nested_id = nested.get("id") or nested.get("profile_id")
    raw = nested_id or profile.get("profile_id") or manifest.get("profile_id")
    if raw is None or str(raw).strip() == "":
        raise ReoptimizeError("re-optimize source missing profile_id")
    return str(raw)


def _strategy_key(manifest: Mapping[str, Any]) -> str | None:
    payload = manifest.get("strategy")
    if not isinstance(payload, Mapping):
        raw = manifest.get("strategy")
        if raw is None or str(raw).strip() == "":
            return None
        return _STRATEGY_ALIASES.get(str(raw), str(raw))
    config = payload.get("config")
    config_strategy = None
    if isinstance(config, Mapping):
        config_strategy = config.get("strategy")
    raw = config_strategy or payload.get("name") or payload.get("class")
    if raw is None or str(raw).strip() == "":
        return None
    return _STRATEGY_ALIASES.get(str(raw), str(raw))


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("+-").isdigit():
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _optional_fidelity(value: Any) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    return str(canonical_backend_name(str(value)))


def _require_goals_source(goals_source: str) -> str:
    resolved = str(goals_source or "").strip()
    if resolved not in GOALS_SOURCES:
        raise ReoptimizeError(
            "goals_source must be bundled_profile or current_local_profile"
        )
    return resolved


def _current_local_profile(
    *,
    profile_id: str,
    feedstock_id: str,
    data_dir: Path,
) -> tuple[Path, str]:
    matches: list[tuple[Path, str]] = []
    profiles_dir = data_dir / PROFILE_DIRNAME
    if profiles_dir.is_dir():
        for path in sorted(profiles_dir.glob("*.yaml")):
            try:
                payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except (OSError, UnicodeDecodeError, yaml.YAMLError):
                continue
            if not isinstance(payload, Mapping):
                continue
            if str(payload.get("profile_id") or "") != profile_id:
                continue
            feedstock = str(payload.get("feedstock") or payload.get("feedstock_id") or "")
            if feedstock and feedstock != feedstock_id:
                raise ReoptimizeError(
                    f"current local profile {profile_id} feedstock {feedstock} "
                    f"does not match source feedstock {feedstock_id}"
                )
            matches.append((path, profile_id))
    if not matches:
        raise ReoptimizeError(
            f"current local profile not found for profile_id {profile_id}"
        )
    if len(matches) > 1:
        raise ReoptimizeError(
            f"current local profile_id {profile_id} is not unique"
        )
    return matches[0]


def _known_knob_paths() -> frozenset[str]:
    schema = RecipeSchema()
    paths = {".".join(spec.path) for spec in schema.allowlist}
    paths.update(".".join(old) for old in PATH_ALIASES)
    paths.update(".".join(alias.canonical_path) for alias in PATH_ALIASES.values())
    return frozenset(paths)


def _objective_metric_known(metric: str) -> bool:
    if metric.startswith(COMPOSITION_TARGET_METRIC_PREFIX):
        return True
    canonical = canonical_objective_metric(metric)
    return metric in KNOWN_OBJECTIVE_METRICS or canonical in KNOWN_OBJECTIVE_METRICS


def _profile_knob_paths(profile: Mapping[str, Any]) -> tuple[str, ...]:
    paths: list[str] = []
    pinned = profile.get("pinned_paths")
    if isinstance(pinned, Sequence) and not isinstance(pinned, (str, bytes)):
        for item in pinned:
            if isinstance(item, str) and item:
                paths.append(item)
    seeds = profile.get("seed_recipes")
    if isinstance(seeds, Sequence) and not isinstance(seeds, (str, bytes)):
        for seed in seeds:
            if not isinstance(seed, Mapping):
                continue
            patch = seed.get("patch")
            if isinstance(patch, Mapping):
                paths.extend(_patch_knob_paths(patch))
    return tuple(paths)


def _patch_knob_paths(patch: Mapping[str, Any]) -> tuple[str, ...]:
    try:
        recipe = RecipePatch.from_nested(patch)
    except (RecipeValidationError, TypeError, ValueError):
        return tuple(_walk_leaf_paths(patch))
    return tuple(".".join(path) for path in recipe.values)


def _walk_leaf_paths(node: Mapping[str, Any], prefix: tuple[str, ...] = ()) -> list[str]:
    paths: list[str] = []
    for key, value in node.items():
        if not isinstance(key, str) or not key:
            continue
        path = prefix + (key,)
        if isinstance(value, Mapping):
            paths.extend(_walk_leaf_paths(value, path))
        else:
            paths.append(".".join(path))
    return paths
