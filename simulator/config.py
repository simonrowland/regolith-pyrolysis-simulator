"""Central loader for simulator configuration YAML."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

import yaml


DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
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


def _load_required_yaml(
    path: Path,
    *,
    functional_digest: bool = False,
) -> tuple[dict[str, Any], str]:
    if not path.exists():
        raise FileNotFoundError(f"required config file missing: {path}")
    raw = path.read_bytes()
    parsed = yaml.safe_load(raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise TypeError(
            f"required config file must have a mapping root: {path}; "
            f"got {type(parsed).__name__}"
        )
    loaded = parsed
    digest = (
        # #89 review-fold: digest the ACTUAL parsed root, not the `or {}` fallback,
        # so a degenerate root ({}, [], null, empty file, scalar) hashes distinctly
        # and a real root-structure change cannot collide. Neutral for real mappings.
        functional_data_yaml_digest(parsed)
        if functional_digest
        else sha256(raw).hexdigest()
    )
    return loaded, digest


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
