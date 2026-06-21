"""Fail-loud gates for strict live vapor-pressure grind runs."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


APPROVED_LIVE_VAPOR_SOURCES = frozenset({"builtin_authoritative"})
VAPOR_ACTIVE_CAMPAIGN_PREFIXES = ("C2A", "C2B", "C4")


class GrindSourceGateError(RuntimeError):
    """Strict grind source-policy violation."""


def assert_strict_vapor_config(
    config: Mapping[str, Any] | None,
    *,
    context: str,
    approved_sources: frozenset[str] = APPROVED_LIVE_VAPOR_SOURCES,
    forbid_fallback_provider_id: bool = False,
) -> None:
    violations = strict_vapor_config_violations(
        config,
        context=context,
        approved_sources=approved_sources,
        forbid_fallback_provider_id=forbid_fallback_provider_id,
    )
    if violations:
        raise GrindSourceGateError("; ".join(violations))


def strict_vapor_config_violations(
    config: Mapping[str, Any] | None,
    *,
    context: str,
    approved_sources: frozenset[str] = APPROVED_LIVE_VAPOR_SOURCES,
    forbid_fallback_provider_id: bool = False,
) -> list[str]:
    if not isinstance(config, Mapping):
        return []

    violations: list[str] = []
    for item_context, item in _config_blocks(config, context):
        force_builtin = item.get("force_builtin_vapor_pressure", False)
        if _truthy(force_builtin):
            violations.append(
                f"{item_context}: force_builtin_vapor_pressure must be False"
            )

        allow_fallback = item.get("allow_fallback_vapor", False)
        if _truthy(allow_fallback):
            violations.append(f"{item_context}: allow_fallback_vapor must be False")

        provider_id = str(item.get("vapor_pressure_provider_id", "") or "").strip()
        if provider_id and _source_authority(provider_id) not in approved_sources:
            violations.append(
                f"{item_context}: vapor_pressure_provider_id must be one of "
                f"{sorted(approved_sources)}, got {provider_id!r}"
            )

        fallback_provider = str(
            item.get("vapor_pressure_fallback_provider_id", "") or ""
        ).strip()
        if forbid_fallback_provider_id and fallback_provider:
            violations.append(
                f"{item_context}: vapor_pressure_fallback_provider_id must be absent "
                "in strict persisted rows"
            )
    return violations


def assert_strict_vapor_source_report(
    report: Mapping[str, Any] | None,
    *,
    context: str,
    approved_sources: frozenset[str] = APPROVED_LIVE_VAPOR_SOURCES,
    require_nonempty: bool = True,
) -> dict[str, Any]:
    if not isinstance(report, Mapping):
        if require_nonempty:
            raise GrindSourceGateError(
                f"{context}: vapor_pressure_source_report must be a non-empty mapping"
            )
        return {"vapor_active": False, "total_species": 0, "summary": {}}

    species = report.get("species")
    if not isinstance(species, Mapping):
        species = {}

    try:
        total_species = int(report.get("total_species", len(species)) or 0)
    except (TypeError, ValueError):
        raise GrindSourceGateError(
            f"{context}: vapor_pressure_source_report.total_species must be an int"
        ) from None

    if total_species <= 0 or not species:
        if require_nonempty:
            raise GrindSourceGateError(
                f"{context}: vapor-active result has empty "
                "vapor_pressure_source_report"
            )
        return {"vapor_active": False, "total_species": total_species, "summary": {}}

    counts = Counter(_source_authority(source) for source in species.values())
    bad_sources = {
        str(source)
        for source in species.values()
        if _source_authority(source) not in approved_sources
    }
    summary = report.get("summary")
    if isinstance(summary, Mapping):
        for source, item in summary.items():
            if _source_authority(source) in approved_sources:
                continue
            count = _summary_count(item)
            if count > 0:
                bad_sources.add(str(source))

    if bad_sources:
        raise GrindSourceGateError(
            f"{context}: vapor_pressure_source_report must be 100% "
            f"{sorted(approved_sources)}; rejected sources={sorted(bad_sources)}"
        )

    approved_count = sum(count for source, count in counts.items() if source in approved_sources)
    if approved_count != total_species:
        raise GrindSourceGateError(
            f"{context}: vapor_pressure_source_report total mismatch: "
            f"{approved_count}/{total_species} approved live sources"
        )

    return {
        "vapor_active": True,
        "total_species": total_species,
        "summary": dict(sorted(counts.items())),
    }


def assert_strict_vapor_provider_identity(
    identity: Mapping[str, Any] | None,
    *,
    context: str,
    approved_sources: frozenset[str] = APPROVED_LIVE_VAPOR_SOURCES,
) -> None:
    if not isinstance(identity, Mapping) or not identity:
        raise GrindSourceGateError(
            f"{context}: vapor_pressure_provider must identify a live source"
        )

    resolved = str(identity.get("resolved_provider_id", "") or "").strip()
    if not resolved or _source_authority(resolved) not in approved_sources:
        raise GrindSourceGateError(
            f"{context}: vapor_pressure_provider.resolved_provider_id must be "
            f"one of {sorted(approved_sources)}, got {resolved!r}"
        )

    authoritative = str(
        identity.get("authoritative_provider_id", "") or ""
    ).strip()
    if authoritative and _source_authority(authoritative) not in approved_sources:
        raise GrindSourceGateError(
            f"{context}: vapor_pressure_provider.authoritative_provider_id "
            f"must be one of {sorted(approved_sources)}, got {authoritative!r}"
        )

    fallback = str(identity.get("fallback_provider_id", "") or "").strip()
    if fallback:
        raise GrindSourceGateError(
            f"{context}: vapor_pressure_provider.fallback_provider_id must be absent "
            "in strict persisted rows"
        )

    if _truthy(identity.get("fallback_allowed", False)):
        raise GrindSourceGateError(
            f"{context}: vapor_pressure_provider.fallback_allowed must be False"
        )


def assert_strict_vapor_pt1_row(
    *,
    artifact: str,
    key: Mapping[str, Any],
    payload: Mapping[str, Any],
    key_hash: str | None = None,
    context: str | None = None,
    approved_sources: frozenset[str] = APPROVED_LIVE_VAPOR_SOURCES,
) -> dict[str, Any]:
    """Canonical strict-vapor gate for one PT-1 equilibrium table row."""

    artifact_name = str(artifact)
    row_context = context or f"PT-1 strict corpus {artifact_name}:{key_hash or '<unknown>'}"
    vapor_active = _pt1_row_requires_vapor_source_provenance(artifact_name, payload)

    vapor_provider = key.get("vapor_pressure_provider")
    if artifact_name == "equilibrium_post_record" or vapor_provider is not None:
        assert_strict_vapor_provider_identity(
            vapor_provider if isinstance(vapor_provider, Mapping) else None,
            context=f"{row_context}:key",
            approved_sources=approved_sources,
        )

    gate_payload: dict[str, Any] = {
        "key": key,
        "payload": payload,
    }
    source_report = _vapor_pressure_source_report_from_pt1_payload(payload)
    if source_report is None:
        if vapor_active:
            raise GrindSourceGateError(
                f"{row_context}: missing last_vapor_pressures_source provenance"
            )
    else:
        gate_payload["vapor_pressure_source_report"] = source_report

    return assert_strict_vapor_result_payload(
        gate_payload,
        context=row_context,
        require_source_report=vapor_active,
        approved_sources=approved_sources,
    )


def _pt1_row_requires_vapor_source_provenance(
    artifact: str,
    payload: Mapping[str, Any],
) -> bool:
    return str(artifact) == "equilibrium_post_record" or "equilibrium_result" in payload


def _vapor_pressure_source_report_from_pt1_payload(
    payload: Mapping[str, Any],
) -> dict[str, object] | None:
    sources = payload.get("last_vapor_pressures_source")
    if not isinstance(sources, Mapping) or not sources:
        return None
    source_by_species = {
        str(species): str(source)
        for species, source in sorted(sources.items())
        if source is not None
    }
    if not source_by_species:
        return None
    counts = Counter(source_by_species.values())
    total = len(source_by_species)
    return {
        "species": source_by_species,
        "summary": {
            source: {
                "count": count,
                "percentage": round(count / total * 100.0, 6) if total else 0.0,
            }
            for source, count in sorted(counts.items())
        },
        "total_species": total,
    }


def vapor_pressure_source_report_from_sim(sim: Any) -> dict[str, object]:
    source_by_species = {
        str(species): str(source)
        for species, source in sorted(
            (getattr(sim, "_last_vapor_pressures_source", {}) or {}).items()
        )
    }
    total = len(source_by_species)
    counts = Counter(source_by_species.values())
    return {
        "species": source_by_species,
        "summary": {
            source: {
                "count": count,
                "percentage": round(count / total * 100.0, 6) if total else 0.0,
            }
            for source, count in sorted(counts.items())
        },
        "total_species": total,
    }


def assert_strict_vapor_result_payload(
    payload: Mapping[str, Any],
    *,
    context: str,
    require_source_report: bool,
    approved_sources: frozenset[str] = APPROVED_LIVE_VAPOR_SOURCES,
) -> dict[str, Any]:
    fallback_path = _kernel_fallback_path(payload)
    if fallback_path is not None:
        raise GrindSourceGateError(
            f"{context}: kernel_fallback_used present at {fallback_path}"
        )

    fallback_provider = _fallback_provider_id_path(payload)
    if fallback_provider is not None:
        path, value = fallback_provider
        raise GrindSourceGateError(
            f"{context}: fallback provider id {value!r} present at {path}"
        )

    provider_violation = _provider_id_policy_violation_path(
        payload, approved_sources
    )
    if provider_violation is not None:
        path, value = provider_violation
        raise GrindSourceGateError(
            f"{context}: vapor_pressure_provider_id must be one of "
            f"{sorted(approved_sources)}, got {value!r} at {path}"
        )

    reports = list(_source_reports(payload))
    if not reports:
        if require_source_report:
            raise GrindSourceGateError(
                f"{context}: vapor-active result missing "
                "vapor_pressure_source_report"
            )
        return {"source_reports": 0, "vapor_active": False, "total_species": 0}

    total_species = 0
    vapor_active = False
    for index, report in enumerate(reports, start=1):
        summary = assert_strict_vapor_source_report(
            report,
            context=f"{context}:vapor_pressure_source_report[{index}]",
            approved_sources=approved_sources,
            require_nonempty=require_source_report,
        )
        total_species += int(summary["total_species"])
        vapor_active = vapor_active or bool(summary["vapor_active"])
    return {
        "source_reports": len(reports),
        "vapor_active": vapor_active,
        "total_species": total_species,
    }


def assert_strict_vapor_result_store(
    db_path: Path,
    *,
    context: str | None = None,
    approved_sources: frozenset[str] = APPROVED_LIVE_VAPOR_SOURCES,
) -> dict[str, int]:
    if not db_path.exists():
        return {"rows": 0, "vapor_active_rows": 0, "source_reports": 0}

    context = context or str(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'results'"
        ).fetchone()
        if table is None:
            return {"rows": 0, "vapor_active_rows": 0, "source_reports": 0}
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(results)").fetchall()
        }
        required = {"cache_key", "eval_spec", "result_blob", "notes"}
        missing = sorted(required - columns)
        if missing:
            raise GrindSourceGateError(
                f"{context}: results table missing columns {missing}"
            )
        rows = conn.execute(
            "SELECT cache_key, eval_spec, result_blob, notes FROM results"
        ).fetchall()

    checked_rows = 0
    vapor_active_rows = 0
    source_reports = 0
    for row in rows:
        checked_rows += 1
        row_context = f"{context}:{row['cache_key']}"
        eval_spec = _json_mapping(row["eval_spec"], f"{row_context}.eval_spec")
        result_blob = _json_value(row["result_blob"], f"{row_context}.result_blob")
        notes = _json_value(row["notes"], f"{row_context}.notes")
        assert_strict_vapor_config(
            eval_spec,
            context=f"{row_context}.eval_spec",
            approved_sources=approved_sources,
            forbid_fallback_provider_id=True,
        )
        vapor_active = eval_spec_is_vapor_active(eval_spec)
        payload = {"result_blob": result_blob, "notes": notes}
        row_summary = assert_strict_vapor_result_payload(
            payload,
            context=row_context,
            require_source_report=vapor_active,
            approved_sources=approved_sources,
        )
        source_reports += int(row_summary["source_reports"])
        if vapor_active or row_summary["vapor_active"]:
            vapor_active_rows += 1
    return {
        "rows": checked_rows,
        "vapor_active_rows": vapor_active_rows,
        "source_reports": source_reports,
    }


def eval_spec_is_vapor_active(eval_spec: Mapping[str, Any]) -> bool:
    campaigns = set(_campaign_strings(eval_spec))
    return any(
        campaign.startswith(VAPOR_ACTIVE_CAMPAIGN_PREFIXES)
        for campaign in campaigns
    )


def _config_blocks(
    config: Mapping[str, Any],
    context: str,
) -> list[tuple[str, Mapping[str, Any]]]:
    blocks: list[tuple[str, Mapping[str, Any]]] = [(context, config)]
    kernel = config.get("chemistry_kernel")
    if isinstance(kernel, Mapping):
        blocks.append((f"{context}.chemistry_kernel", kernel))
    setpoints = config.get("setpoints")
    if isinstance(setpoints, Mapping) and isinstance(
        setpoints.get("chemistry_kernel"),
        Mapping,
    ):
        blocks.append(
            (f"{context}.setpoints.chemistry_kernel", setpoints["chemistry_kernel"])
        )
    return blocks


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"", "0", "false", "no", "off", "none", "null"}:
            return False
        if lowered in {"1", "true", "yes", "on"}:
            return True
    return bool(value)


def _source_authority(source: Any) -> str:
    head = str(source or "").split(":", 1)[0]
    if head == "builtin-vapor-pressure":
        return "builtin_authoritative"
    return head


def _summary_count(item: Any) -> int:
    if isinstance(item, Mapping):
        item = item.get("count", 0)
    try:
        return int(item or 0)
    except (TypeError, ValueError):
        return 0


def _source_reports(payload: Any) -> list[Mapping[str, Any]]:
    reports: list[Mapping[str, Any]] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if key == "vapor_pressure_source_report" and isinstance(value, Mapping):
                reports.append(value)
            else:
                reports.extend(_source_reports(value))
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        for value in payload:
            reports.extend(_source_reports(value))
    return reports


def _kernel_fallback_path(payload: Any, path: str = "$") -> str | None:
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            child_path = f"{path}.{key}"
            if key == "kernel_fallback_used":
                return child_path
            found = _kernel_fallback_path(value, child_path)
            if found is not None:
                return found
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        for index, value in enumerate(payload):
            found = _kernel_fallback_path(value, f"{path}[{index}]")
            if found is not None:
                return found
    return None


def _fallback_provider_id_path(
    payload: Any,
    path: str = "$",
) -> tuple[str, str] | None:
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            child_path = f"{path}.{key}"
            if _provider_id_key(key) and _fallback_provider_value(value):
                return child_path, str(value)
            found = _fallback_provider_id_path(value, child_path)
            if found is not None:
                return found
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        for index, value in enumerate(payload):
            found = _fallback_provider_id_path(value, f"{path}[{index}]")
            if found is not None:
                return found
    return None


def _provider_id_key(key: Any) -> bool:
    text = str(key)
    return text == "vapor_pressure_fallback_provider_id"


def _fallback_provider_value(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    authority = _source_authority(value).strip().lower()
    return "fallback" in authority


def _provider_id_policy_violation_path(
    payload: Any,
    approved_sources: frozenset[str],
    path: str = "$",
) -> tuple[str, str] | None:
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            child_path = f"{path}.{key}"
            if str(key) == "vapor_pressure_provider_id" and isinstance(value, str):
                if _source_authority(value) not in approved_sources:
                    return child_path, value
            found = _provider_id_policy_violation_path(
                value, approved_sources, child_path
            )
            if found is not None:
                return found
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        for index, value in enumerate(payload):
            found = _provider_id_policy_violation_path(
                value, approved_sources, f"{path}[{index}]"
            )
            if found is not None:
                return found
    return None


def _json_mapping(raw: Any, context: str) -> Mapping[str, Any]:
    value = _json_value(raw, context)
    if not isinstance(value, Mapping):
        raise GrindSourceGateError(f"{context}: expected JSON mapping")
    return value


def _json_value(raw: Any, context: str) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise GrindSourceGateError(f"{context}: invalid JSON") from exc


def _campaign_strings(eval_spec: Mapping[str, Any]) -> set[str]:
    campaigns: set[str] = set()
    for key in ("campaign", "target_maturity", "target_provenance", "lab_schedule"):
        campaigns.update(_collect_campaign_strings(eval_spec.get(key)))
    campaigns.update(_collect_campaign_strings(eval_spec.get("runtime_campaign_overrides")))
    return {campaign for campaign in campaigns if campaign}


def _collect_campaign_strings(value: Any) -> set[str]:
    campaigns: set[str] = set()
    if isinstance(value, str):
        campaigns.add(value)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if key_text.startswith(VAPOR_ACTIVE_CAMPAIGN_PREFIXES):
                campaigns.add(key_text)
            if "campaign" in key_text.lower():
                campaigns.update(_collect_campaign_strings(item))
            elif isinstance(item, Mapping):
                campaigns.update(_collect_campaign_strings(item))
            elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
                campaigns.update(_collect_campaign_strings(item))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            campaigns.update(_collect_campaign_strings(item))
    return campaigns
