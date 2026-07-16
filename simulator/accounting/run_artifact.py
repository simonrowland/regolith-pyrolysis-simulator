"""Pure runner-payload to durable run-artifact reshaping."""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from numbers import Real
from typing import Any

from simulator.cost_parameters import canonical_energy_cost_block
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


def _canonical_energy_cost_totals(
    per_hour: list[dict[str, Any]],
    cost_block: Mapping[str, Any],
    run_metadata: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not per_hour:
        return None
    energy_fields = (
        "energy_electrical_kWh",
        "energy_evaporation_thermal_kWh",
    )
    totals: dict[str, float] = {}
    for field_name in energy_fields:
        values: list[float] = []
        for row in per_hour:
            value = row.get(field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, Real)
                or not math.isfinite(float(value))
            ):
                return None
            values.append(float(value))
        totals[field_name] = sum(values)
    electrical_price = float(cost_block["electrical_cost_per_kWh"])
    process_electrical_kWh = totals["energy_electrical_kWh"]
    pumping_diagnostic = None
    cost_rollup = run_metadata.get("cost_rollup_diagnostic")
    if isinstance(cost_rollup, Mapping):
        pumping_diagnostic = cost_rollup.get("pumping_diagnostic")

    pumping_electrical_kWh = None
    pumping_status = None
    if isinstance(pumping_diagnostic, Mapping):
        raw_status = pumping_diagnostic.get("status")
        pumping_status = (
            str(raw_status).strip() if raw_status is not None else "missing"
        ) or "missing"
        if pumping_status in {"ok", "resolved"}:
            candidate = pumping_diagnostic.get("pumping_electrical_kWh")
            if (
                isinstance(candidate, bool)
                or not isinstance(candidate, Real)
                or not math.isfinite(float(candidate))
                or float(candidate) < 0.0
            ):
                return None
            pumping_electrical_kWh = float(candidate)

    total_electrical_kWh = process_electrical_kWh
    result: dict[str, Any] = {
        "process_electrical_energy_kWh": process_electrical_kWh,
        "process_electrical_cost_usd": process_electrical_kWh * electrical_price,
        "evaporation_thermal_energy_kWh": totals[
            "energy_evaporation_thermal_kWh"
        ],
    }
    if pumping_electrical_kWh is None:
        if pumping_status is None:
            result["basis_note"] = (
                "pumping electrical energy not emitted; electrical totals exclude pumping"
            )
        else:
            result["basis_note"] = (
                "pumping electrical energy excluded; "
                f"diagnostic status={pumping_status}"
            )
    else:
        total_electrical_kWh += pumping_electrical_kWh
        result.update(
            {
                "pumping_electrical_energy_kWh": pumping_electrical_kWh,
                "pumping_electrical_cost_usd": (
                    pumping_electrical_kWh * electrical_price
                ),
            }
        )
    electrical = total_electrical_kWh * electrical_price
    solar_heat = (
        totals["energy_evaporation_thermal_kWh"]
        * float(cost_block["solar_heat_cost_per_kWh"])
    )
    result.update({
        "electrical_energy_kWh": total_electrical_kWh,
        "electrical_cost_usd": electrical,
        "solar_heat_cost_usd": solar_heat,
        "total_cost_usd": electrical + solar_heat,
    })
    return result


def _terminal_confidence(artifact: Mapping[str, Any]) -> dict[str, Any] | None:
    """Grade only artifact-owned evidence using a fixed three-level ladder.

    A finite numeric mass-balance residual is required; without it confidence is
    omitted. A closure breach, failed vapor/backend status, or refused/failed
    execution grades low. Partial execution, non-authoritative evidence,
    unavailable/non-ok status, or incomplete backend identity caps the grade at
    medium. Only all passing criteria grade high.
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
    vapor_status = None
    vapor_authoritative = None
    if isinstance(source_report, Mapping):
        vapor_status = source_report.get("vapor_pressure_backend_status")
        vapor_authoritative = source_report.get(
            "authoritative_for_requested_vapor_pressure"
        )
    if vapor_status == "ok" and vapor_authoritative is True:
        reasons.append("vapor-pressure backend status ok and authoritative")
    elif vapor_status == "failed":
        hard_degradation = True
        reasons.append(
            "vapor-pressure backend status failed; "
            f"authoritative={vapor_authoritative!r}"
        )
    elif not isinstance(source_report, Mapping) or not source_report:
        soft_degradation = True
        reasons.append("vapor-pressure source report absent")
    elif vapor_status in (None, ""):
        soft_degradation = True
        reasons.append(
            "vapor-pressure backend status absent; "
            f"authoritative={vapor_authoritative!r}"
        )
    elif isinstance(vapor_status, str):
        soft_degradation = True
        reasons.append(
            f"vapor-pressure backend status {vapor_status}; "
            f"authoritative={vapor_authoritative!r}"
        )
    else:
        soft_degradation = True
        reasons.append(
            f"vapor-pressure backend status invalid: {vapor_status!r}; "
            f"authoritative={vapor_authoritative!r}"
        )

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

    run_metadata = terminal.get("run_metadata")
    backend_status = (
        run_metadata.get("backend_status")
        if isinstance(run_metadata, Mapping)
        else None
    )
    backend_authoritative = (
        run_metadata.get("backend_authoritative")
        if isinstance(run_metadata, Mapping)
        else None
    )
    backend_name = (
        engine_identity.get("name")
        if isinstance(engine_identity, Mapping)
        and engine_identity.get("name") not in (None, "")
        else None
    )
    if backend_status == "ok":
        reasons.append("backend status: ok")
    elif backend_status == "failed":
        hard_degradation = True
        reasons.append(
            f"backend status failed: {backend_name}"
            if backend_name is not None
            else "backend status failed; name absent"
        )
    elif backend_status in (None, ""):
        soft_degradation = True
        reasons.append("backend status absent")
    else:
        soft_degradation = True
        reasons.append(f"backend status not ok: {backend_status!r}")

    if backend_authoritative is True:
        reasons.append(
            f"backend authoritative: {backend_name}"
            if backend_name is not None
            else "backend authoritative; name absent"
        )
    elif backend_authoritative is False:
        soft_degradation = True
        reasons.append(
            f"backend not authoritative: {backend_name}"
            if backend_name is not None
            else "backend not authoritative; name absent"
        )
    elif backend_authoritative is None:
        soft_degradation = True
        reasons.append("backend authority absent")
    else:
        soft_degradation = True
        reasons.append(f"backend authority invalid: {backend_authoritative!r}")

    execution_status = artifact.get("execution_status")
    if execution_status == "ok":
        reasons.append("execution status: ok")
    elif execution_status == "partial":
        soft_degradation = True
        reasons.append("execution status partial caps confidence at medium")
    else:
        hard_degradation = True
        reasons.append(f"execution status {execution_status} caps confidence at low")

    if artifact.get("lifecycle") == "cancelled":
        reasons.append("lifecycle cancelled")

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
    cost_parameters = runner_payload.get("cost_parameters")
    cost_block = canonical_energy_cost_block(
        cost_parameters if isinstance(cost_parameters, Mapping) else None
    )
    header["cost_block"] = cost_block
    artifact["header"] = header
    # timesteps[].ledger is the per-hour mol-native dump of the W-A0 ratified
    # artifact design ("per-timestep dump -> {ledger, ...} is the PRIMARY web
    # contract" — determinism-as-storage). Consumers: the stored artifact
    # itself (audit/replay reads it engine-free) and the viewer timestep
    # inspector's ledger panel (W-B wave). Do not remove for lack of a UI
    # reader; the dump IS the product.
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
    terminal = {
        "run_metadata": run_metadata,
        "mass_balance_closure": {
            "residual_pct": per_hour[-1].get("mass_balance_pct") if per_hour else None,
            "basis": "final-hour percent",
        },
    }
    for payload_key, artifact_key in (
        ("final_state", "final_state"),
        ("final", "final"),
        ("stage_purity_report", "stage_purity"),
        ("vapor_pressure_source_report", "vapor_pressure_source_report"),
        ("yield_disposition", "yield_disposition"),
    ):
        if payload_key in runner_payload:
            terminal[artifact_key] = runner_payload[payload_key]
    cost_totals = _canonical_energy_cost_totals(per_hour, cost_block, run_metadata)
    if cost_totals is not None:
        terminal["cost_totals"] = cost_totals
    artifact["terminal"] = terminal
    confidence = _terminal_confidence(artifact)
    if confidence is not None:
        artifact["terminal"]["confidence"] = confidence
    return artifact
