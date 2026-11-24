"""Append-only frozen setup and prediction bundles for benchtop runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from simulator.diagnostic_helpers.reproduction_compare import content_digest


def _freeze_field_run(
    run_id: str,
    *,
    setup: Mapping[str, Any],
    resolved: Mapping[str, Any],
    engine_commit: str,
    provider_runtime_identity: Mapping[str, Any] | str,
    predicted_series: Mapping[str, Any],
    flags: Any,
    root: Path = Path("data/field-preregistration"),
    version: int = 1,
    predecessor_digest: str | None = None,
) -> Path:
    """Write one immutable version and return its directory."""

    run_id = str(run_id).strip()
    if not run_id or run_id in {".", ".."} or "/" in run_id or "\\" in run_id:
        raise ValueError("run_id must be one path component")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("version must be a positive integer")
    if version == 1 and predecessor_digest is not None:
        raise ValueError("v1 cannot have a predecessor digest")
    if version > 1:
        previous_digest_path = root / run_id / f"v{version - 1}" / "bundle.sha256"
        if not predecessor_digest or not previous_digest_path.is_file():
            raise ValueError("a later version requires its predecessor bundle digest")
        if previous_digest_path.read_text(encoding="utf-8").strip() != predecessor_digest:
            raise ValueError("predecessor_digest does not match the prior version")
    if not isinstance(setup, Mapping) or not isinstance(resolved, Mapping):
        raise ValueError("setup and resolved values must be mappings")
    if not isinstance(predicted_series, Mapping):
        raise ValueError("predicted_series must be a mapping")

    directory = root / run_id / f"v{version}"
    _create_version_directory(directory)
    setup_value = _plain_value(setup)
    resolved_value = _plain_value(resolved)
    bundle: dict[str, Any] = {
        "schema_version": "field_preregistration_bundle.v1",
        "run_id": run_id,
        "version": version,
        "engine_commit": str(engine_commit),
        "provider_runtime_identity": _plain_value(provider_runtime_identity),
        "setup_digest": content_digest(setup_value),
        "resolved_digest": content_digest(resolved_value),
        "predicted_series": _plain_value(predicted_series),
        "flags": _plain_value(flags),
    }
    if predecessor_digest is not None:
        bundle["predecessor_digest"] = str(predecessor_digest)

    (directory / "setup.yaml").write_text(
        yaml.safe_dump(setup_value, sort_keys=False),
        encoding="utf-8",
    )
    (directory / "resolved.yaml").write_text(
        yaml.safe_dump(resolved_value, sort_keys=False),
        encoding="utf-8",
    )
    (directory / "bundle.json").write_text(
        json.dumps(bundle, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (directory / "bundle.sha256").write_text(
        content_digest(bundle) + "\n",
        encoding="utf-8",
    )
    return directory


def _create_version_directory(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=False)


def _plain_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_value(item) for item in value]
    return value
