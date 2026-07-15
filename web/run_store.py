"""Durable JSON store for backend-free run artifacts."""

from __future__ import annotations

import fcntl
import json
import logging
import math
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

from flask import current_app

from simulator.accounting.run_artifact import build_run_artifact


DEFAULT_RETENTION = 100
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SAVE_LOCK = threading.Lock()
_LOG = logging.getLogger(__name__)


class RunStoreCorruptionError(RuntimeError):
    """Raised when a stored run exists but is not a decodable JSON object."""

    def __init__(self, run_id: str, path: Path, detail: str) -> None:
        self.run_id = run_id
        self.path = path
        super().__init__(f"corrupt run artifact {run_id!r}: {detail}")


class DuplicateRunArtifactError(RuntimeError):
    """Raised when persistence cannot prove this payload won the first write."""


class RunArtifactStore:
    def __init__(self, runs_dir: str | Path, *, keep: int = DEFAULT_RETENTION) -> None:
        self.runs_dir = Path(runs_dir)
        self.keep = max(0, int(keep))

    def save(self, run_id: str, artifact: dict[str, Any]) -> bool:
        destination = self._path(run_id)
        claim_path = destination.with_suffix(".write-lock")
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        with _SAVE_LOCK:
            with claim_path.open("a", encoding="utf-8") as claim_handle:
                fcntl.flock(claim_handle.fileno(), fcntl.LOCK_EX)
                if destination.exists():
                    _LOG.warning(
                        "run artifact %s already exists; duplicate save ignored",
                        run_id,
                    )
                    return False
                temp_path: Path | None = None
                try:
                    fd, raw_temp_path = tempfile.mkstemp(
                        prefix=f".{run_id}.", suffix=".tmp", dir=self.runs_dir
                    )
                    temp_path = Path(raw_temp_path)
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        json.dump(
                            artifact,
                            handle,
                            indent=2,
                            sort_keys=True,
                            allow_nan=False,
                        )
                        handle.write("\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temp_path, destination)
                finally:
                    if temp_path is not None:
                        temp_path.unlink(missing_ok=True)
        self._apply_retention()
        return True

    def load(self, run_id: str) -> dict[str, Any] | None:
        path = self._path(run_id)
        try:
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            raise RunStoreCorruptionError(run_id, path, str(exc)) from exc
        if not isinstance(payload, dict):
            raise RunStoreCorruptionError(
                run_id,
                path,
                f"expected a JSON object, got {type(payload).__name__}",
            )
        self._validate_nested_shape(run_id, path, payload)
        return payload

    @staticmethod
    def _validate_nested_shape(
        run_id: str, path: Path, artifact: dict[str, Any]
    ) -> None:
        for key in ("header", "terminal"):
            if key in artifact and not isinstance(artifact[key], dict):
                raise RunStoreCorruptionError(
                    run_id,
                    path,
                    f"expected {key} to be an object, got {type(artifact[key]).__name__}",
                )
        timesteps = artifact.get("timesteps", [])
        if not isinstance(timesteps, list):
            raise RunStoreCorruptionError(
                run_id,
                path,
                f"expected timesteps to be an array, got {type(timesteps).__name__}",
            )
        for index, timestep in enumerate(timesteps):
            if not isinstance(timestep, dict):
                raise RunStoreCorruptionError(
                    run_id,
                    path,
                    f"expected timesteps[{index}] to be an object, got {type(timestep).__name__}",
                )
            summary = timestep.get("summary", {})
            if not isinstance(summary, dict):
                raise RunStoreCorruptionError(
                    run_id,
                    path,
                    f"expected timesteps[{index}].summary to be an object, got {type(summary).__name__}",
                )
            metal_yields = summary.get("metal_yields_kg", {})
            if not isinstance(metal_yields, dict):
                raise RunStoreCorruptionError(
                    run_id,
                    path,
                    "expected "
                    f"timesteps[{index}].summary.metal_yields_kg to be an object, "
                    f"got {type(metal_yields).__name__}",
                )

    def list_runs(self) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        if not self.runs_dir.exists():
            return summaries
        for path in self.runs_dir.glob("*.json"):
            try:
                artifact = self.load(path.stem)
                if artifact is not None:
                    summaries.append(self._summary(artifact, path.stem))
            except RunStoreCorruptionError as exc:
                quarantine_path = self._quarantine(path)
                _LOG.error("%s; quarantined at %s", exc, quarantine_path)
                continue
        return sorted(
            summaries,
            key=lambda row: str(row.get("created_at") or ""),
            reverse=True,
        )

    def _path(self, run_id: str) -> Path:
        value = str(run_id)
        if not _RUN_ID_RE.fullmatch(value):
            raise ValueError("run_id must use only letters, digits, dot, underscore, or hyphen")
        return self.runs_dir / f"{value}.json"

    @staticmethod
    def _quarantine(path: Path) -> Path:
        candidate = path.with_suffix(f"{path.suffix}.corrupt")
        index = 1
        while candidate.exists():
            candidate = path.with_suffix(f"{path.suffix}.corrupt.{index}")
            index += 1
        os.replace(path, candidate)
        return candidate

    @staticmethod
    def _summary(artifact: dict[str, Any], fallback_run_id: str) -> dict[str, Any]:
        header = artifact.get("header", {}) or {}
        timesteps = artifact.get("timesteps", []) or []
        summaries = [
            row.get("summary", {}) or {}
            for row in timesteps
            if isinstance(row, dict)
        ]
        temperatures = [
            row.get("T_C")
            for row in summaries
            if isinstance(row.get("T_C"), (int, float))
            and not isinstance(row.get("T_C"), bool)
        ]
        final_summary = summaries[-1] if summaries else {}
        metal_yields = final_summary.get("metal_yields_kg", {}) or {}
        headline_yields: dict[str, int | float] = {}
        summary_parts: list[str] = []
        fe_kg = metal_yields.get("Fe")
        if RunArtifactStore._is_finite_number(fe_kg):
            headline_yields["Fe"] = fe_kg
            summary_parts.append(f"Fe {fe_kg:g} kg")
        o2_kg = final_summary.get("O2_source_side_potential_kg_cumulative")
        if not RunArtifactStore._is_finite_number(o2_kg):
            o2_kg = final_summary.get("O2_yield_kg_cumulative")
        if RunArtifactStore._is_finite_number(o2_kg):
            headline_yields["O2"] = o2_kg
            summary_parts.append(f"O₂ (source-side) {o2_kg:g} kg")
        execution_status = artifact.get("execution_status")
        result = {
            "run_id": header.get("run_id", fallback_run_id),
            "name": header.get("name"),
            "feedstock_id": header.get("feedstock_id"),
            "campaign_chain": header.get("campaign_chain", []),
            "peak_T_C": max(temperatures) if temperatures else None,
            "headline_yields_kg": headline_yields,
            "execution_status": execution_status,
            "status": execution_status,
            "created_at": header.get("created_at"),
            "starred": bool(header.get("starred", False)),
            "summary": " · ".join(summary_parts),
        }
        if header.get("folder") is not None:
            result["folder"] = header["folder"]
        return result

    @staticmethod
    def _is_finite_number(value: Any) -> bool:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )

    def _apply_retention(self) -> None:
        unstarred: list[tuple[str, Path]] = []
        for path in self.runs_dir.glob("*.json"):
            try:
                with path.open(encoding="utf-8") as handle:
                    artifact = json.load(handle)
                header = artifact.get("header", {}) or {}
                if not bool(header.get("starred", False)):
                    created_at = str(header.get("created_at") or "")
                    unstarred.append((created_at, path))
            except (OSError, json.JSONDecodeError, AttributeError):
                continue
        unstarred.sort(key=lambda item: item[0], reverse=True)
        for _created_at, path in unstarred[self.keep:]:
            path.unlink(missing_ok=True)


def get_run_store() -> RunArtifactStore:
    runs_dir = current_app.config.get("RUN_ARTIFACT_DIR")
    if runs_dir is None:
        runs_dir = Path(current_app.instance_path) / "runs"
    keep = current_app.config.get("RUN_ARTIFACT_RETENTION", DEFAULT_RETENTION)
    return RunArtifactStore(runs_dir, keep=keep)


def save(run_id: str, artifact: dict[str, Any]) -> bool:
    return get_run_store().save(run_id, artifact)


def load(run_id: str) -> dict[str, Any] | None:
    return get_run_store().load(run_id)


def list_runs() -> list[dict[str, Any]]:
    return get_run_store().list_runs()


def persist_run_artifact(
    runner_payload: dict[str, Any],
    run_id: str,
    name: str | None = None,
    *,
    store: RunArtifactStore | None = None,
) -> dict[str, Any]:
    artifact = build_run_artifact(runner_payload, run_id=run_id, name=name)
    stored = (store.save if store is not None else save)(run_id, artifact)
    if not stored:
        raise DuplicateRunArtifactError(
            f"run artifact {run_id!r} was not written because the id already exists"
        )
    return artifact
