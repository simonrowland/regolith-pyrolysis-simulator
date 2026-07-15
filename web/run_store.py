"""Durable JSON store for backend-free run artifacts."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from flask import current_app

from simulator.accounting.run_artifact import build_run_artifact


DEFAULT_RETENTION = 100
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class RunArtifactStore:
    def __init__(self, runs_dir: str | Path, *, keep: int = DEFAULT_RETENTION) -> None:
        self.runs_dir = Path(runs_dir)
        self.keep = max(0, int(keep))

    def save(self, run_id: str, artifact: dict[str, Any]) -> None:
        destination = self._path(run_id)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        fd, raw_temp_path = tempfile.mkstemp(
            prefix=f".{run_id}.", suffix=".tmp", dir=self.runs_dir
        )
        temp_path = Path(raw_temp_path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(artifact, handle, indent=2, sort_keys=True, allow_nan=False)
                handle.write("\n")
            os.link(temp_path, destination)
        finally:
            temp_path.unlink(missing_ok=True)
        self._apply_retention()

    def load(self, run_id: str) -> dict[str, Any] | None:
        path = self._path(run_id)
        try:
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            return None
        return payload if isinstance(payload, dict) else None

    def list_runs(self) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        if not self.runs_dir.exists():
            return summaries
        for path in self.runs_dir.glob("*.json"):
            try:
                with path.open(encoding="utf-8") as handle:
                    artifact = json.load(handle)
                if isinstance(artifact, dict):
                    summaries.append(self._summary(artifact, path.stem))
            except (OSError, json.JSONDecodeError):
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
        headline_yields = {
            "Fe": metal_yields.get("Fe"),
            "O2": final_summary.get("O2_yield_kg_cumulative"),
        }
        execution_status = artifact.get("execution_status")
        return {
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
            "folder": header.get("folder", "My runs"),
        }

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


def save(run_id: str, artifact: dict[str, Any]) -> None:
    get_run_store().save(run_id, artifact)


def load(run_id: str) -> dict[str, Any] | None:
    return get_run_store().load(run_id)


def list_runs() -> list[dict[str, Any]]:
    return get_run_store().list_runs()


def persist_run_artifact(
    runner_payload: dict[str, Any],
    run_id: str,
    name: str | None = None,
) -> dict[str, Any]:
    artifact = build_run_artifact(runner_payload, run_id=run_id, name=name)
    save(run_id, artifact)
    return artifact
