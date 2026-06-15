"""Markdown renderer for Stage-0 foulant by-group reporting."""

from __future__ import annotations

from typing import Any, Mapping

_GROUP_DISPLAY_ORDER = (
    ("trapped_gasses", "Trapped gasses"),
    ("refractory_carbon", "Refractory carbon"),
    ("other_mineral_contaminant", "Other mineral contaminant"),
)

_PARTITION_FIELDS = (
    ("escaped_kg", "escaped"),
    ("retained_kg", "retained"),
    ("wall_deposit_kg", "wall-deposited"),
    ("rump_kg", "rump"),
    ("burned_kg", "burned"),
)


def _format_kg(value: Any) -> str:
    try:
        kg = float(value)
    except (TypeError, ValueError):
        kg = 0.0
    if abs(kg) < 1.0e-9:
        return "0"
    if abs(kg) < 1.0:
        return f"{kg:.3e}"
    return f"{kg:.3f}"


def _format_value(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return f"{float(value):.6g}"
    return str(value)


def _format_residual_interval(interval: Any) -> str:
    if not isinstance(interval, Mapping):
        return "none"
    low = _format_kg(interval.get("low_kg", 0.0))
    high = _format_kg(interval.get("high_kg", 0.0))
    reasons = interval.get("reasons") or ()
    if reasons:
        return f"{low}-{high} kg ({', '.join(str(r) for r in reasons)})"
    return f"{low}-{high} kg"


def _partition_line(payload: Mapping[str, Any]) -> str:
    fields = [
        f"{label}={_format_kg(payload.get(key, 0.0))} kg"
        for key, label in _PARTITION_FIELDS
    ]
    fields.append(
        "residual_interval="
        f"{_format_residual_interval(payload.get('residual_interval'))}"
    )
    return "; ".join(fields)


def _verdict_a_flags(verdicts: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if not isinstance(verdicts, Mapping):
        return []
    verdict_a = verdicts.get("verdict_a")
    if not isinstance(verdict_a, Mapping):
        return []
    flags = verdict_a.get("flags") or ()
    return [flag for flag in flags if isinstance(flag, Mapping)]


def _verdict_a_steps(verdicts: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if not isinstance(verdicts, Mapping):
        return []
    verdict_a = verdicts.get("verdict_a")
    if not isinstance(verdict_a, Mapping):
        return []
    steps = verdict_a.get("step_resolved") or ()
    return [step for step in steps if isinstance(step, Mapping)]


def _format_verdict_a_flag(flag: Mapping[str, Any]) -> str:
    provenance = str(flag.get("noise_floor_status") or "not_evaluated")
    return (
        f"{flag.get('property', 'unknown')}: "
        f"rung={flag.get('level', 'INFO')}; "
        f"metric={flag.get('metric', 'unknown')}; "
        f"grounded={_format_value(flag.get('grounded'))}; "
        f"correctable={_format_value(flag.get('correctable'))}; "
        f"provenance={provenance}; "
        f"contaminant={flag.get('contaminant', 'unknown')}; "
        f"before={_format_value(flag.get('perturbation_before'))}; "
        f"after={_format_value(flag.get('perturbation_after'))}"
    )


def _format_step_flag(flag: Mapping[str, Any]) -> str:
    if "cleared" not in flag:
        state = "UNKNOWN"
    else:
        state = "CLEAR" if bool(flag.get("cleared")) else "ACTIVE"
    clear_hour = flag.get("clear_hour")
    return (
        f"{flag.get('property', 'unknown')} {state} "
        f"rung={flag.get('level', 'INFO')} "
        f"metric={flag.get('metric', 'unknown')} "
        f"grounded={_format_value(flag.get('grounded'))} "
        f"correctable={_format_value(flag.get('correctable'))} "
        f"provenance={flag.get('noise_floor_status', 'not_evaluated')} "
        f"clear_hour={_format_value(clear_hour)}"
    )


def _append_verdict_a(
    lines: list[str],
    verdicts: Mapping[str, Any] | None,
) -> None:
    lines.append("## Verdict (a): property-impact flags")
    flags = _verdict_a_flags(verdicts)
    if not flags:
        lines.append("- provenance=not_evaluated; flags=none")
    else:
        for flag in flags:
            lines.append(f"- {_format_verdict_a_flag(flag)}")
    lines.append("")

    lines.append("## Verdict (a): per-property clear steps")
    steps = _verdict_a_steps(verdicts)
    if not steps:
        lines.append("- clear-step surface: not_reported")
    else:
        for step in steps:
            hour = step.get("hour", "unknown")
            step_flags = [
                flag
                for flag in (step.get("flags") or ())
                if isinstance(flag, Mapping)
            ]
            if not step_flags:
                lines.append(f"- hour {hour}: flags=none")
                continue
            for flag in step_flags:
                lines.append(f"- hour {hour}: {_format_step_flag(flag)}")
    lines.append("")


def _append_verdict_b(
    lines: list[str],
    verdicts: Mapping[str, Any] | None,
) -> None:
    lines.append("## Verdict (b): domain status")
    verdict_b = verdicts.get("verdict_b") if isinstance(verdicts, Mapping) else None
    if not isinstance(verdict_b, Mapping):
        lines.append("- backend_status=not_evaluated; hard_gate_failed=unknown")
    else:
        lines.append(
            "- "
            f"backend_status={verdict_b.get('backend_status', 'unknown')}; "
            f"layer_a_state={verdict_b.get('layer_a_state', 'unknown')}; "
            "stripped_domain_valid="
            f"{_format_value(verdict_b.get('stripped_domain_valid'))}; "
            f"hard_gate_failed={_format_value(verdict_b.get('hard_gate_failed'))}"
        )
    lines.append("")


def format_stage0_foulant_report_markdown(
    partition_by_group: Mapping[str, Mapping[str, Any]],
    *,
    hourly_by_group: Mapping[str, Mapping[str, Any]] | None = None,
    verdicts: Mapping[str, Any] | None = None,
    feedstock_id: str | None = None,
    campaign: str | None = None,
    title: str = "Stage-0 Foulant By-Group Report",
) -> str:
    """Render R0 foulant partition, hourly deltas, and REFINE verdicts."""
    lines: list[str] = [f"# {title}"]
    meta = []
    if feedstock_id:
        meta.append(f"feedstock=`{feedstock_id}`")
    if campaign:
        meta.append(f"campaign=`{campaign}`")
    if meta:
        lines.append("**Run**: " + "; ".join(meta))
    lines.append("")

    lines.append("## Yield bakeoff by group")
    for group, label in _GROUP_DISPLAY_ORDER:
        payload = partition_by_group.get(group, {}) or {}
        lines.append(f"- **{label}**: {_partition_line(payload)}")
    lines.append("")

    if hourly_by_group is not None:
        lines.append("## Current-hour deltas by group")
        for group, label in _GROUP_DISPLAY_ORDER:
            payload = hourly_by_group.get(group, {}) or {}
            lines.append(f"- **{label}**: {_partition_line(payload)}")
        lines.append("")

    _append_verdict_a(lines, verdicts)
    _append_verdict_b(lines, verdicts)
    return "\n".join(lines).rstrip() + "\n"
