"""Pure runner-payload to durable run-artifact reshaping."""

from __future__ import annotations

from typing import Any


ARTIFACT_SCHEMA_VERSION = "0.1.0"


def _campaign_chain(per_hour: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    chain: list[str] = []
    for row in per_hour:
        campaign = row.get("campaign")
        if campaign and campaign not in seen:
            seen.add(campaign)
            chain.append(campaign)
    return chain


def build_run_artifact(
    runner_payload: dict[str, Any],
    *,
    run_id: str,
    name: str | None = None,
) -> dict[str, Any]:
    """Repackage a completed runner payload without running the engine."""
    run_metadata = runner_payload.get("run_metadata", {}) or {}
    per_hour = runner_payload.get("per_hour_summary", []) or []
    status = runner_payload.get("status", "ok")

    artifact: dict[str, Any] = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "execution_status": status,
        "lifecycle": "complete",
    }
    if status != "ok":
        artifact["failure"] = {
            "reason": runner_payload.get("reason"),
            "error_message": runner_payload.get("error_message"),
        }

    artifact["header"] = {
        "run_id": str(run_id),
        "name": name if name is not None else str(run_id),
        "created_at": run_metadata.get("started_at_utc"),
        "seed": run_metadata.get("seed"),
        "feedstock_id": run_metadata.get("feedstock_id"),
        "charge_mass_kg": run_metadata.get("mass_kg"),
        "c3_dose": run_metadata.get("c3_alkali_credit_dose_kg_by_species"),
        "campaign_chain": _campaign_chain(per_hour),
        "engine_identity": {
            "name": run_metadata.get("backend"),
            "cache_version": run_metadata.get("kernel_commit_sha"),
            "backend_wire_token": run_metadata.get("backend"),
        },
        "recipe_snapshot": {
            "note": "not captured in this payload; W-A4 emits the importable recipe"
        },
        "target_snapshot": None,
    }
    artifact["timesteps"] = [
        {"hour": row.get("hour"), "summary": row}
        for row in per_hour
    ]
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
            "basis": "final per-hour mass_balance_pct",
        },
    }
    return artifact
