"""Recipe-file IO for optimizer setpoints patches."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Mapping

import yaml

from simulator.cost_parameters import (
    RECIPE_COST_PARAMETERS_KEY,
    recipe_cost_parameters_from_payload,
)
from simulator.optimize.recipe import (
    RecipePatch,
    RecipeSchema,
    RecipeValidationError,
)


RECIPE_LIBRARY_DIR = Path(__file__).resolve().parent.parent / "data" / "recipes"
_RECIPE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_METADATA_REQUIRED_KEYS = frozenset({"title", "headline_recipe", "headline_results"})
_METADATA_OPTIONAL_KEYS = frozenset({"created_utc", "feedstock", "campaign"})
_METADATA_KEYS = _METADATA_REQUIRED_KEYS | _METADATA_OPTIONAL_KEYS


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


def read_recipe_metadata(path: str | Path) -> dict[str, Any]:
    """Read only the optional UI metadata block from a recipe YAML file."""

    recipe_path = Path(path)
    payload = _load_recipe_mapping(recipe_path)
    if "metadata" not in payload:
        return {}
    return _validate_recipe_metadata(payload["metadata"], source=str(recipe_path))


def read_recipe_cost_parameters(path: str | Path) -> dict[str, Any]:
    recipe_path = Path(path)
    payload = _load_recipe_mapping(recipe_path)
    return recipe_cost_parameters_from_payload(payload, source=str(recipe_path))


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
    if "metadata" in payload:
        _validate_recipe_metadata(payload["metadata"], source=source)
    recipe_payload = _recipe_payload_without_metadata(payload)
    if not recipe_payload:
        raise RecipeIOError(f"{source} produced an empty setpoints_patch")
    _validate_top_level_keys(recipe_payload, source=source, schema=active_schema)
    try:
        patch = RecipePatch.from_nested(recipe_payload)
        normalized = active_schema.to_setpoints_patch(patch)
    except RecipeValidationError as exc:
        raise RecipeIOError(f"invalid recipe {source}: {exc}") from exc

    if not normalized:
        raise RecipeIOError(f"{source} produced an empty setpoints_patch")
    _validate_optimizer_shape(recipe_payload, normalized, source=source)
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


def write_recipe_patch(
    path: str | Path,
    payload: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any] | None = None,
) -> Path:
    """Validate and write a canonical recipe YAML file."""

    recipe_path = Path(path)
    if metadata is None and "metadata" in payload:
        metadata = payload["metadata"]
    cost_parameters = recipe_cost_parameters_from_payload(
        payload,
        source=str(recipe_path),
    )
    normalized = normalize_recipe_patch(payload, source=str(recipe_path))
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    document: dict[str, Any] = {
        RECIPE_COST_PARAMETERS_KEY: cost_parameters,
    }
    if metadata is not None:
        document = {
            "metadata": _validate_recipe_metadata(
                metadata,
                source=str(recipe_path),
            ),
            **document,
        }
    document.update(normalized)
    recipe_path.write_text(
        yaml.safe_dump(document, sort_keys=False),
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
    source_payload = _load_recipe_mapping(source_path)
    normalized = normalize_recipe_patch(source_payload, source=str(source_path))
    recipe_cost_parameters_from_payload(source_payload, source=str(source_path))
    destination = recipe_library_path(name, library_dir=library_dir)
    output_payload = dict(normalized)
    if RECIPE_COST_PARAMETERS_KEY in source_payload:
        output_payload = {
            RECIPE_COST_PARAMETERS_KEY: source_payload[RECIPE_COST_PARAMETERS_KEY],
            **output_payload,
        }
    write_recipe_patch(destination, output_payload)
    if RECIPE_COST_PARAMETERS_KEY in source_payload:
        source_block = _top_level_yaml_block(
            source_path.read_bytes(),
            RECIPE_COST_PARAMETERS_KEY,
        )
        destination_bytes = destination.read_bytes()
        destination.write_bytes(
            _replace_top_level_yaml_block(
                destination_bytes,
                RECIPE_COST_PARAMETERS_KEY,
                source_block,
            )
        )
    return destination


def _top_level_yaml_block(document: bytes, key: str) -> bytes:
    lines = document.splitlines(keepends=True)
    encoded_key = re.escape(key.encode("utf-8"))
    key_pattern = (
        rb"^(?:"
        + encoded_key
        + rb"|'"
        + encoded_key
        + rb"'|\""
        + encoded_key
        + rb"\")\s*:"
    )
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if re.match(key_pattern, line)
        ),
        None,
    )
    if start is None:
        raise RecipeIOError(f"recipe file is missing raw {key} block")
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].strip()
            and not lines[index].startswith((b" ", b"\t", b"#"))
        ),
        len(lines),
    )
    return b"".join(lines[start:end])


def _replace_top_level_yaml_block(document: bytes, key: str, replacement: bytes) -> bytes:
    current = _top_level_yaml_block(document, key)
    start = document.index(current)
    return document[:start] + replacement + document[start + len(current) :]


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


def _recipe_payload_without_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key not in {"metadata", RECIPE_COST_PARAMETERS_KEY}
    }


def _validate_recipe_metadata(value: Any, *, source: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RecipeIOError(f"{source} metadata must be a mapping")
    unknown = {str(key) for key in value if key not in _METADATA_KEYS}
    if unknown:
        allowed = ", ".join(sorted(_METADATA_KEYS))
        raise RecipeIOError(
            f"{source} metadata has unknown key(s): "
            f"{', '.join(sorted(unknown))}; allowed: {allowed}"
        )
    missing = _METADATA_REQUIRED_KEYS - set(value)
    if missing:
        raise RecipeIOError(
            f"{source} metadata missing required key(s): "
            f"{', '.join(sorted(missing))}"
        )
    metadata = {str(key): _plain_data(item) for key, item in value.items()}
    title = metadata.get("title")
    if not isinstance(title, str) or not title.strip():
        raise RecipeIOError(f"{source} metadata.title must be a non-empty string")
    for key in ("created_utc", "feedstock", "campaign"):
        if key in metadata and not isinstance(metadata[key], str):
            raise RecipeIOError(f"{source} metadata.{key} must be a string")
    for key in ("headline_recipe", "headline_results"):
        if not isinstance(metadata.get(key), Mapping):
            raise RecipeIOError(f"{source} metadata.{key} must be a mapping")
    _validate_metadata_json_value(metadata, source=source, path=("metadata",))
    return copy.deepcopy(metadata)


def _validate_metadata_json_value(
    value: Any,
    *,
    source: str,
    path: tuple[str, ...],
) -> None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                dotted = ".".join(path)
                raise RecipeIOError(f"{source} {dotted} keys must be strings")
            _validate_metadata_json_value(
                item,
                source=source,
                path=(*path, key),
            )
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_metadata_json_value(
                item,
                source=source,
                path=(*path, str(index)),
            )
        return
    dotted = ".".join(path)
    raise RecipeIOError(f"{source} {dotted} must contain only JSON-like values")


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
    keys.add(RECIPE_COST_PARAMETERS_KEY)
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
