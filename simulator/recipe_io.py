"""Recipe-file IO for optimizer setpoints patches."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Mapping

import yaml

from simulator.optimize.recipe import (
    RecipePatch,
    RecipeSchema,
    RecipeValidationError,
)


RECIPE_LIBRARY_DIR = Path(__file__).resolve().parent.parent / "data" / "recipes"
_RECIPE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class RecipeIOError(ValueError):
    """Raised when a recipe file is missing, malformed, or unsafe."""


def load_recipe_patch(
    path: str | Path,
    *,
    schema: RecipeSchema | None = None,
) -> dict[str, Any]:
    """Load and validate a recipe YAML file as a setpoints patch."""

    recipe_path = Path(path)
    payload = _load_recipe_mapping(recipe_path)
    return normalize_recipe_patch(payload, source=str(recipe_path), schema=schema)


def normalize_recipe_patch(
    payload: Mapping[str, Any],
    *,
    source: str = "recipe payload",
    schema: RecipeSchema | None = None,
) -> dict[str, Any]:
    """Validate a nested optimizer recipe and return canonical patch data."""

    if not isinstance(payload, Mapping):
        raise RecipeIOError(f"{source} must be a YAML mapping")
    if not payload:
        raise RecipeIOError(f"{source} must not be empty")

    active_schema = schema or RecipeSchema()
    _validate_top_level_keys(payload, source=source, schema=active_schema)
    try:
        patch = RecipePatch.from_nested(payload)
        normalized = active_schema.to_setpoints_patch(patch)
    except RecipeValidationError as exc:
        raise RecipeIOError(f"invalid recipe {source}: {exc}") from exc

    if not normalized:
        raise RecipeIOError(f"{source} produced an empty setpoints_patch")
    _validate_optimizer_shape(payload, normalized, source=source)
    return copy.deepcopy(normalized)


def recipe_library_path(
    name: str,
    *,
    library_dir: Path = RECIPE_LIBRARY_DIR,
) -> Path:
    """Return the canonical data/recipes path for a recipe name."""

    recipe_name = str(name)
    if recipe_name.endswith(".yaml"):
        recipe_name = recipe_name[:-5]
    if not _RECIPE_NAME_RE.fullmatch(recipe_name):
        raise RecipeIOError(
            "recipe name must be a file stem containing only letters, "
            "numbers, '.', '_', and '-'"
        )
    return library_dir / f"{recipe_name}.yaml"


def write_recipe_patch(path: str | Path, payload: Mapping[str, Any]) -> Path:
    """Validate and write a canonical recipe YAML file."""

    recipe_path = Path(path)
    normalized = normalize_recipe_patch(payload, source=str(recipe_path))
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text(
        yaml.safe_dump(normalized, sort_keys=True),
        encoding="utf-8",
    )
    return recipe_path


def save_recipe_to_library(
    source: str | Path,
    name: str,
    *,
    library_dir: Path = RECIPE_LIBRARY_DIR,
) -> Path:
    """Normalize an optimizer winner recipe into the named recipe library."""

    source_path = Path(source)
    if source_path.is_dir():
        source_path = source_path / "winner.recipe.yaml"
    normalized = load_recipe_patch(source_path)
    destination = recipe_library_path(name, library_dir=library_dir)
    return write_recipe_patch(destination, normalized)


def _load_recipe_mapping(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        raise RecipeIOError(f"recipe file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise RecipeIOError(f"recipe file malformed YAML ({path}): {exc}") from exc
    except OSError as exc:
        raise RecipeIOError(f"recipe file unreadable ({path}): {exc}") from exc
    if not isinstance(payload, Mapping):
        raise RecipeIOError(f"recipe file {path} must contain a YAML mapping")
    if not payload:
        raise RecipeIOError(f"recipe file {path} must not be empty")
    return payload


def _validate_top_level_keys(
    payload: Mapping[str, Any],
    *,
    source: str,
    schema: RecipeSchema,
) -> None:
    unknown: list[str] = []
    for key in payload:
        if not isinstance(key, str):
            raise RecipeIOError(f"{source} top-level recipe keys must be strings")
        if key not in _allowed_top_level_keys(schema):
            unknown.append(key)
    if unknown:
        allowed = ", ".join(sorted(_allowed_top_level_keys(schema)))
        raise RecipeIOError(
            f"{source} has unknown top-level recipe key(s): "
            f"{', '.join(sorted(unknown))}; allowed: {allowed}"
        )


def _allowed_top_level_keys(schema: RecipeSchema) -> set[str]:
    keys = {spec.path[0] for spec in schema.allowlist}
    keys.update(path[0] for path in schema.FORBIDDEN_EXACT_PATH_EXCEPTIONS)
    return keys


def _validate_optimizer_shape(
    payload: Mapping[str, Any],
    normalized: Mapping[str, Any],
    *,
    source: str,
) -> None:
    if _plain_data(payload) == _plain_data(normalized):
        return
    raise RecipeIOError(
        f"{source} is not an optimizer setpoints_patch shape; "
        "normalize it with scripts/save_recipe.py"
    )


def _plain_data(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain_data(item) for item in value]
    return value
