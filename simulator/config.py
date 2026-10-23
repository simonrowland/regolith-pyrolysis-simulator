"""Central loader for simulator configuration YAML."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

from simulator.yaml_cache import load_cached_safe_yaml
from simulator.condensation_routing import COLLECTOR_STAGE_KEY_BY_NUMBER, STAGE3_ROUTES
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
from simulator.scalar_boundary import is_declared_real_scalar


DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_ALPHAMELTS_MODEL = "MELTSv1.0.2"
ENGINE_MODEL_UNAVAILABLE = "unknown"


def resolve_alphamelts_subprocess_model(
    model_name: str | None,
) -> tuple[str, str]:
    """Resolve the verified AlphaMELTS subprocess model and calc mode."""
    model = str(model_name or "").strip() or DEFAULT_ALPHAMELTS_MODEL
    if model != DEFAULT_ALPHAMELTS_MODEL:
        raise ValueError(
            f"AlphaMELTS subprocess model {model!r} has no verified "
            "ALPHAMELTS_CALC_MODE mapping"
        )
    return model, "MELTS"


def resolve_alphamelts_python_api_model(
    model_name: str | None,
) -> tuple[str, int]:
    """Resolve exact PetThermoTools model names to their payload codes."""
    # The subprocess resolver accepts only the default, so stripping there
    # cannot change the computation. This transport accepts several models;
    # stripping here would turn a name that previously fell through to code 1
    # into code 2/3/4 under the unchanged replay identity.
    if model_name is not None and not isinstance(model_name, str):
        raise ValueError(
            f"AlphaMELTS Python API model {model_name!r} is not verified"
        )
    model = (
        DEFAULT_ALPHAMELTS_MODEL
        if model_name is None or model_name == ""
        else model_name
    )
    model_codes = {
        DEFAULT_ALPHAMELTS_MODEL: 1,
        "pMELTS": 2,
        "MELTSv1.1.0": 3,
        "MELTSv1.2.0": 4,
    }
    try:
        model_code = model_codes[model]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            f"AlphaMELTS Python API model {model!r} is not verified"
        ) from exc
    return model, model_code


def resolve_grid_engine_model(model_name: str | None, backend_name: str) -> str:
    """Resolve the model identity used by the grid producer and writer."""
    if backend_name == "subprocess":
        return resolve_alphamelts_subprocess_model(model_name)[0]
    if backend_name == "thermoengine":
        model = str(model_name or "") or DEFAULT_ALPHAMELTS_MODEL
        if model == ENGINE_MODEL_UNAVAILABLE:
            raise ValueError("ThermoEngine model identity cannot be unavailable")
        return model
    raise ValueError(f"unsupported grid backend: {backend_name!r}")


_FUNCTIONAL_DATA_DIGEST_CONFIGS = frozenset({"setpoints", "vapor_pressures"})
_FUNCTIONAL_DATA_DIGEST_PREFIX = b"functional-data-yaml-v1\0"


def _resolve_mre_canonical_voltage_token(species: Any, value: Any) -> Any:
    from simulator.mre_ladder import (
        CANONICAL_DECOMPOSITION_VOLTAGE_TOKEN,
        DECOMP_VOLTAGES,
    )

    if not (
        isinstance(value, str)
        and value.strip().lower() == CANONICAL_DECOMPOSITION_VOLTAGE_TOKEN
    ):
        return value
    if not species or isinstance(species, bool):
        return value
    if isinstance(species, str):
        return DECOMP_VOLTAGES.get(species, value)
    if isinstance(species, (list, tuple)) and len(species) == 1:
        item = species[0]
        if isinstance(item, str):
            return DECOMP_VOLTAGES.get(item, value)
    return value


def _resolve_mre_ladder_tokens(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return [_resolve_mre_ladder_tokens(item) for item in value]
    if not isinstance(value, Mapping):
        return value

    resolved = {key: _resolve_mre_ladder_tokens(value[key]) for key in value}
    sequence_block = resolved.get("mre_voltage_sequence")
    if isinstance(sequence_block, Mapping):
        entries = sequence_block.get("sequence")
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict):
                    entry["decomposition_V"] = _resolve_mre_canonical_voltage_token(
                        entry.get("species"),
                        entry.get("decomposition_V"),
                    )
    holds_block = resolved.get("mre_stepped_voltage_holds")
    if isinstance(holds_block, Mapping):
        entries = holds_block.get("sequence")
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict):
                    entry["voltage"] = _resolve_mre_canonical_voltage_token(
                        entry.get("species"),
                        entry.get("voltage"),
                    )
    return resolved


@dataclass(frozen=True)
class ConfigBundle:
    setpoints: dict[str, Any]
    feedstocks: dict[str, Any]
    vapor_pressures: dict[str, Any]
    foulant_thermo: dict[str, Any]
    materials: dict[str, Any]
    species_catalog: dict[str, Any]
    source_paths: dict[str, Path]
    digests: dict[str, str]


def _functional_data_ready(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("functional data YAML digest rejects NaN and infinity")
        return value
    if isinstance(value, (list, tuple)):
        return [_functional_data_ready(item) for item in value]
    if isinstance(value, Mapping):
        keys = list(value)
        if not all(isinstance(key, str) for key in keys):
            raise TypeError("functional data YAML digest mapping keys must be strings")
        return {key: _functional_data_ready(value[key]) for key in sorted(keys)}
    raise TypeError(
        f"functional data YAML digest unsupported type: {type(value).__name__}"
    )


def functional_data_yaml_digest(value: Any) -> str:
    value = _resolve_mre_ladder_tokens(value)
    canonical = json.dumps(
        _functional_data_ready(value),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(_FUNCTIONAL_DATA_DIGEST_PREFIX + canonical).hexdigest()


@lru_cache(maxsize=32)
def _parse_required_yaml(
    raw: bytes,
    functional_digest: bool,
    path: Path,
) -> tuple[dict[str, Any], str]:
    digest = sha256(raw).hexdigest()
    parsed = load_cached_safe_yaml(
        raw.decode("utf-8"),
        content_sha256=digest,
    )
    if not isinstance(parsed, dict):
        raise TypeError(
            f"required config file must have a mapping root: {path}; "
            f"got {type(parsed).__name__}"
        )
    digest = (
        # #89 review-fold: digest the ACTUAL parsed root, not the `or {}` fallback,
        # so a degenerate root ({}, [], null, empty file, scalar) hashes distinctly
        # and a real root-structure change cannot collide. Neutral for real mappings.
        functional_data_yaml_digest(parsed)
        if functional_digest
        else digest
    )
    return parsed, digest


def _load_required_yaml(
    path: Path,
    *,
    functional_digest: bool = False,
) -> tuple[dict[str, Any], str]:
    if not path.exists():
        raise FileNotFoundError(f"required config file missing: {path}")
    raw = path.read_bytes()
    loaded, digest = _parse_required_yaml(raw, functional_digest, path)
    # Cached parse results never escape: callers historically receive a fresh,
    # mutable mapping on every load and may safely modify it in-place.
    return deepcopy(loaded), digest


def resolve_collector_bands_C(
    setpoints: Mapping[str, Any] | None = None,
    *,
    require_complete: bool = False,
) -> dict[int, tuple[float, float]]:
    """Resolve collector bands from YAML; partial callers inherit omitted stages.

    Species reference/dew temperatures are a different input. There is no
    in-code band fallback: even the no-argument train reads shipped setpoints.
    File loaders require complete inputs; only in-memory callers may inherit
    omitted stages. The inherited YAML is itself loaded and validated strictly.
    """
    defaults = (
        _load_setpoints_file(DEFAULT_DATA_DIR / "setpoints.yaml")
        if not require_complete or setpoints is None else setpoints
    )
    supplied = setpoints if setpoints is not None else defaults
    if require_complete and "condensation_train" not in supplied:
        raise ValueError("missing required config key: condensation_train")
    train = supplied.get(
        "condensation_train", {},
    )
    if not isinstance(train, Mapping):
        raise ValueError("condensation_train must be a mapping")
    if require_complete and "metals_train" not in train:
        raise ValueError("missing required config key: condensation_train.metals_train")
    stages = train.get("metals_train", {})
    if not isinstance(stages, Mapping):
        raise ValueError("condensation_train.metals_train must be a mapping")
    bands = {}
    default_stages = defaults["condensation_train"]["metals_train"]
    for number, key in COLLECTOR_STAGE_KEY_BY_NUMBER.items():
        if require_complete and key not in stages:
            raise ValueError(f"missing required config key: condensation_train.metals_train.{key}")
        stage = stages.get(key, default_stages.get(key))
        label = f"condensation_train.metals_train.{key}.temp_range_C"
        if not isinstance(stage, Mapping):
            raise ValueError(f"{label} stage must be a mapping")
        if require_complete and "temp_range_C" not in stage:
            raise ValueError(f"missing required config key: {label}")
        band = stage.get("temp_range_C")
        if not isinstance(band, (list, tuple)) or len(band) != 2:
            raise ValueError(f"{label} must contain two finite temperatures")
        if not all(is_declared_real_scalar(value, allow_numeric_str=True) for value in band):
            raise ValueError(f"{label} endpoints must be numeric, not boolean")
        low, high = map(float, band)
        if not (
            math.isfinite(low) and math.isfinite(high)
            and low > -CELSIUS_TO_KELVIN_OFFSET and low <= high
        ):
            raise ValueError(f"{label} must be finite, above absolute zero and ordered")
        limit = stage.get("max_service_T_C", default_stages[key].get("max_service_T_C"))
        if limit is not None:
            if not (
                is_declared_real_scalar(limit, allow_numeric_str=True)
                and math.isfinite(float(limit))
            ):
                raise ValueError(f"{label} has invalid max_service_T_C")
            if high > float(limit):
                raise ValueError(f"{label} exceeds declared max_service_T_C={limit}")
        bands[number] = (low, high)
    return bands


def _validate_collection_commands(setpoints: Mapping[str, Any]) -> None:
    """Complete recipe files declare each campaign and substage's diverter."""
    bypass = setpoints.get('condensation_train', {}).get('stage3_bypass')
    required = ('liner_material', 'length_m', 'inner_diameter_m', 'wall_temperature_C')
    if not isinstance(bypass, Mapping) or any(key not in bypass for key in required):
        from simulator.recipe_errors import MalformedRecipeError
        raise MalformedRecipeError('missing required stage3_bypass equipment inputs')
    for phase, cfg in setpoints.get('campaigns', {}).items():
        if not isinstance(cfg, Mapping) or 'temp_range_C' not in cfg:
            continue
        commands = [(phase, cfg)]
        commands.extend((f'{phase}.{name}', cfg[name])
                        for name in ('Na_phase', 'K_phase') if name in cfg)
        commands.extend((f'{phase}.{stage.get("name")}', stage)
                        for stage in cfg.get('stages', []) if isinstance(stage, Mapping))
        for name, command in commands:
            if command.get('stage3_route') not in STAGE3_ROUTES:
                raise ValueError(f'{name}.stage3_route must be divert or through')


def _load_setpoints_file(path: Path) -> dict[str, Any]:
    """Read complete setpoints without inheriting missing collector inputs."""
    setpoints, _ = _load_required_yaml(path, functional_digest=True)
    resolve_collector_bands_C(setpoints, require_complete=True)
    _validate_collection_commands(setpoints)
    return setpoints


def load_config_bundle(
    data_dir: Path | None = None,
    *,
    setpoints_path: Path | None = None,
    feedstocks_path: Path | None = None,
    vapor_pressures_path: Path | None = None,
    foulant_thermo_path: Path | None = None,
    materials_path: Path | None = None,
    species_catalog_path: Path | None = None,
) -> ConfigBundle:
    root = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
    source_paths = {
        "setpoints": Path(setpoints_path) if setpoints_path else root / "setpoints.yaml",
        "feedstocks": Path(feedstocks_path) if feedstocks_path else root / "feedstocks.yaml",
        "vapor_pressures": (
            Path(vapor_pressures_path)
            if vapor_pressures_path
            else root / "vapor_pressures.yaml"
        ),
        "foulant_thermo": (
            Path(foulant_thermo_path)
            if foulant_thermo_path
            else root / "foulant_thermo.yaml"
        ),
        "materials": Path(materials_path) if materials_path else root / "materials.yaml",
        "species_catalog": (
            Path(species_catalog_path)
            if species_catalog_path
            else root / "species_catalog.yaml"
        ),
    }
    loaded: dict[str, dict[str, Any]] = {}
    digests: dict[str, str] = {}
    for name, path in source_paths.items():
        loaded[name], digests[name] = _load_required_yaml(
            path,
            functional_digest=name in _FUNCTIONAL_DATA_DIGEST_CONFIGS,
        )
    from simulator.vapour_rail.catalog import (
        validate_species_catalog,
        vapor_pressure_compatibility_view,
    )

    if "species" in loaded["species_catalog"]:
        validate_species_catalog(loaded["species_catalog"])
    if loaded["vapor_pressures"].get("schema_version") == 2:
        loaded["vapor_pressures"] = vapor_pressure_compatibility_view(
            loaded["vapor_pressures"]
        )
    resolve_collector_bands_C(loaded["setpoints"], require_complete=True)
    _validate_collection_commands(loaded["setpoints"])
    return ConfigBundle(
        setpoints=loaded["setpoints"],
        feedstocks=loaded["feedstocks"],
        vapor_pressures=loaded["vapor_pressures"],
        foulant_thermo=loaded["foulant_thermo"],
        materials=loaded["materials"],
        species_catalog=loaded["species_catalog"],
        source_paths=source_paths,
        digests=digests,
    )
