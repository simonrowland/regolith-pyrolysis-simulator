"""Configuration helpers for the optional FactSAGE/ChemApp backend."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping, Optional


CONFIG_ENV_VAR = 'FACTSAGE_CONFIG'


class FactSAGEConfigError(ValueError):
    """Raised when a FactSAGE config file is malformed."""


def load_factsage_config(
    config_path: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> dict:
    """
    Load FactSAGE configuration from a JSON file.

    The file path is explicit through ``config_path`` or ``FACTSAGE_CONFIG``.
    When neither is set, return an empty config so callers can fall back.
    """
    env = os.environ if environ is None else environ
    path_value = config_path or env.get(CONFIG_ENV_VAR)

    if not path_value:
        return {}

    path = Path(path_value).expanduser()
    try:
        with open(path) as f:
            loaded = json.load(f)
    except OSError as exc:
        raise FactSAGEConfigError(
            f'Could not read FactSAGE config {path}: {exc}') from exc
    except json.JSONDecodeError as exc:
        raise FactSAGEConfigError(
            f'Invalid JSON in FactSAGE config {path}: {exc}') from exc
    if not isinstance(loaded, dict):
        raise FactSAGEConfigError(
            f'FactSAGE config {path} must contain a JSON object')
    return dict(loaded)
