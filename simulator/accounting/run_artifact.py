"""Pure runner-payload to durable run-artifact reshaping."""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from numbers import Real
from typing import Any

from simulator.engine_local_config import cache_version_for


ARTIFACT_SCHEMA_VERSION = "0.2.0"
EXECUTION_STATUSES = frozenset({"ok", "partial", "refused", "failed"})
LIFECYCLES = frozenset({"complete", "cancelled"})
# Matches the project's load-bearing <=5e-12% mass-balance closure gate.
CONFIDENCE_MAX_MASS_BALANCE_RESIDUAL_PCT = 5e-12
CONFIDENCE_BACKEND_IDENTITY_FIELDS = (
    "name",
    "cache_version",
    "backend_wire_token",
)


class RunArtifactContractError(ValueError):
    """Raised when a runner payload cannot satisfy the artifact contract."""


def _execution_status(runner_payload: dict[str, Any]) -> str:
    if "status" not in runner_payload:
        raise RunArtifactContractError("runner payload is missing execution status")
    status = runner_payload["status"]
    if not isinstance(status, str) or status not in EXECUTION_STATUSES:
        raise RunArtifactContractError(f"unknown execution status: {status!r}")
    return status


def _recipe_snapshot(runner_payload: dict[str, Any]) -> dict[str, Any] | None:
    raw_snapshot = runner_payload.get("recipe_snapshot")
    source = raw_snapshot if isinstance(raw_snapshot, Mapping) else runner_payload
    setpoints_patch = source.get("setpoints_patch")
    pins = source.get("pins")
    recipe_schema_version = source.get("recipe_schema_version")
    # An EMPTY patch is a truthful snapshot of a default run (nothing was
    # overridden) — only a missing/mistyped patch disqualifies the snapshot.
    if not isinstance(setpoints_patch, Mapping):
        return None
    if pins is None or not recipe_schema_version:
        return None
    return {
        "setpoints_patch": copy.deepcopy(dict(setpoints_patch)),
        "pins": copy.deepcopy(pins),
        "recipe_schema_version": recipe_schema_version,
    }


def _campaign_chain(per_hour: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    chain: list[str] = []
    for row in per_hour:
        campaign = row.get("campaign")
        if campaign and campaign not in seen:
            seen.add(campaign)
            chain.append(campaign)
    return chain


def _terminal_confidence(artifact: Mapping[str, Any]) -> dict[str, Any] | None:
    """Grade only artifact-owned evidence using a fixed three-level ladder.

    A finite numeric mass-balance residual is required; without it confidence is
    omitted. A closure breach, failed vapor source, or refused/failed execution
    grades low. Partial execution, unavailable/non-ok vapor status, or incomplete
    backend identity caps the grade at medium. Only all passing criteria grade high.
    """
    terminal = artifact.get("terminal")
    if not isinstance(terminal, Mapping):
        return None
    closure = terminal.get("mass_balance_closure")
    residual = closure.get("residual_pct") if isinstance(closure, Mapping) else None
    if (
        isinstance(residual, bool)
        or not isinstance(residual, Real)
        or not math.isfinite(float(residual))
    ):
        return None

    hard_degradation = False
    soft_degradation = False
    reasons: list[str] = []
    residual_text = format(float(residual), ".15g")
    if abs(float(residual)) > CONFIDENCE_MAX_MASS_BALANCE_RESIDUAL_PCT:
        hard_degradation = True
        reasons.append(
            f"mass-balance residual {residual_text}% exceeds "
            f"{CONFIDENCE_MAX_MASS_BALANCE_RESIDUAL_PCT:g}% closure gate"
        )
    else:
        reasons.append(
            f"mass-balance residual {residual_text}% within "
            f"{CONFIDENCE_MAX_MASS_BALANCE_RESIDUAL_PCT:g}% closure gate"
        )

    source_report = terminal.get("vapor_pressure_source_report")
    vapor_status = (
        source_report.get("status") if isinstance(source_report, Mapping) else None
    )
    if vapor_status == "ok":
        reasons.append("vapor-pressure sources: ok")
    elif vapor_status == "failed":
        hard_degradation = True
        reasons.append("vapor-pressure sources: failed")
    elif vapor_status is None or vapor_status == "":
        soft_degradation = True
        reasons.append("vapor-pressure source status absent")
    elif isinstance(vapor_status, str):
        soft_degradation = True
        reasons.append(f"vapor-pressure sources: {vapor_status}")
    else:
        soft_degradation = True
        reasons.append("vapor-pressure source status invalid: non-string")

    header = artifact.get("header")
    engine_identity = (
        header.get("engine_identity") if isinstance(header, Mapping) else None
    )
    missing_identity_fields = [
        field
        for field in CONFIDENCE_BACKEND_IDENTITY_FIELDS
        if not isinstance(engine_identity, Mapping)
        or engine_identity.get(field) in (None, "")
    ]
    if missing_identity_fields:
        soft_degradation = True
        reasons.append(
            "backend identity incomplete: "
            + ", ".join(missing_identity_fields)
            + " absent"
        )
    else:
        reasons.append(
            "backend identity complete: name, cache_version, backend_wire_token present"
        )

    execution_status = artifact.get("execution_status")
    if execution_status == "ok":
        reasons.append("execution status: ok")
    elif execution_status == "partial":
        soft_degradation = True
        reasons.append("execution status partial caps confidence at medium")
    else:
        hard_degradation = True
        reasons.append(f"execution status {execution_status} caps confidence at low")

    grade = "low" if hard_degradation else "medium" if soft_degradation else "high"
    return {"grade": grade, "reasons": reasons}


def build_run_artifact(
    runner_payload: dict[str, Any],
    *,
    run_id: str,
    name: str | None = None,
    lifecycle: str = "complete",
) -> dict[str, Any]:
    """Repackage a completed runner payload without running the engine."""
    if lifecycle not in LIFECYCLES:
        raise RunArtifactContractError(f"unknown lifecycle: {lifecycle!r}")
    run_metadata = runner_payload.get("run_metadata", {}) or {}
    per_hour = runner_payload.get("per_hour_summary", []) or []
    status = _execution_status(runner_payload)

    artifact: dict[str, Any] = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "execution_status": status,
        "lifecycle": lifecycle,
    }
    if status != "ok":
        artifact["failure"] = {
            "reason": runner_payload.get("reason"),
            "error_message": runner_payload.get("error_message"),
        }

    backend = run_metadata.get("backend")
    engine_identity = {
        "name": backend,
        "cache_version": cache_version_for(backend) if backend else None,
        "backend_wire_token": backend,
    }
    kernel_commit_sha = run_metadata.get("kernel_commit_sha")
    if kernel_commit_sha is not None:
        engine_identity["kernel_commit_sha"] = kernel_commit_sha

    header = {
        "run_id": str(run_id),
        "name": name if name is not None else str(run_id),
        "created_at": run_metadata.get("started_at_utc"),
        "feedstock_id": run_metadata.get("feedstock_id"),
        "charge_mass_kg": run_metadata.get("mass_kg"),
        "campaign_chain": _campaign_chain(per_hour),
        "engine_identity": engine_identity,
        "target_snapshot": None,
    }
    seed = run_metadata.get("seed")
    if seed is not None:
        header["seed"] = seed
    c3_dose = run_metadata.get("c3_alkali_credit_dose_kg_by_species")
    if isinstance(c3_dose, Mapping):
        # Emit exactly the species the runner recorded: a single-species dose is
        # real data (dropping it loses the dose; padding the other species with
        # 0.0 fabricates a dose that never happened). Omit the block only when
        # no species is present.
        dose_out = {
            f"{species}_kg": c3_dose[species]
            for species in ("Na", "K")
            if c3_dose.get(species) is not None
        }
        if dose_out:
            header["c3_dose"] = dose_out
    recipe_snapshot = _recipe_snapshot(runner_payload)
    if recipe_snapshot is not None:
        header["recipe_snapshot"] = recipe_snapshot
    effective_config = runner_payload.get("effective_config")
    if effective_config is not None:
        header["effective_config"] = copy.deepcopy(effective_config)
    artifact["header"] = header
    per_hour_ledger = runner_payload.get("per_hour_ledger")
    timesteps = []
    for row in per_hour:
        timestep = {"hour": row.get("hour"), "summary": row}
        if isinstance(per_hour_ledger, Mapping):
            ledger = per_hour_ledger.get(str(row.get("hour")))
            if isinstance(ledger, Mapping):
                timestep["ledger"] = copy.deepcopy(dict(ledger))
        timesteps.append(timestep)
    artifact["timesteps"] = timesteps
    artifact["terminal"] = {
        "final_state": runner_payload.get("final_state", {}),
        "final": runner_payload.get("final", {}),
        "stage_purity": runner_payload.get("stage_purity_report", {}),
        "vapor_pressure_source_report": runner_payload.get(
            "vapor_pressure_source_report", {}
        ),
        "run_metadata": run_metadata,
        "mass_balance_closure": {
            "residual_pct": per_hour[-1].get("mass_balance_pct") if per_hour else None,
            "basis": "final-hour percent",
        },
    }
    confidence = _terminal_confidence(artifact)
    if confidence is not None:
        artifact["terminal"]["confidence"] = confidence
    return artifact
