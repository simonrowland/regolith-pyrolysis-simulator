"""Cached access to the battery scorer's default-policy registry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import os
from pathlib import Path
import re
from typing import Mapping

import yaml

from simulator.battery.enums import (
    DEFAULT_POLICY_NOTICE_KINDS,
    MethodToken,
    NoticeKind,
    Phase,
)
from simulator.yaml_cache import load_cached_safe_yaml


POLICY_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "battery" / "default_policy.yaml"
)
_REQUIRED_POLICY_FIELDS = frozenset(
    {
        "id",
        "condition",
        "units",
        "physical_basis",
        "bias",
        "flag",
        "ruling",
        "version",
        "status",
        "proposed_by",
        "proposed_at",
        "evidence",
        "canonized_by",
        "canonized_at",
        "history",
    }
)
_DEFAULT_POLICY_FLAG_NAMES = frozenset(
    kind.value for kind in DEFAULT_POLICY_NOTICE_KINDS
)


@dataclass(frozen=True)
class Resolved:
    value: object
    units: str
    flag: NoticeKind
    payload: Mapping[str, object]


@lru_cache(maxsize=4)
def _load_policy_table(path: Path) -> dict[str, dict[str, object]]:
    raw = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping) or raw.get("schema_version") != 1:
        raise ValueError("default-policy schema_version must be 1")
    records = raw.get("policies")
    if not isinstance(records, list):
        raise ValueError("default-policy policies must be a list")
    policies: dict[str, dict[str, object]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("default-policy entry must be a mapping")
        missing = _REQUIRED_POLICY_FIELDS - record.keys()
        if missing:
            raise ValueError(
                f"default-policy entry is missing fields: {', '.join(sorted(missing))}"
            )
        policy_id = record.get("id")
        if not isinstance(policy_id, str) or not policy_id:
            raise ValueError("default-policy id must be a non-empty string")
        if policy_id in policies:
            raise ValueError(f"duplicate default-policy id: {policy_id}")
        if ("value" in record) == ("formula" in record):
            raise ValueError(
                f"default-policy entry {policy_id} must have value or formula"
            )
        if "formula" in record and not record.get("formula_owner"):
            raise ValueError(f"default-policy formula has no owner for {policy_id}")
        if record.get("status") not in {"canonical", "proposed", "rejected"}:
            raise ValueError(f"invalid default-policy status for {policy_id}")
        if not isinstance(record.get("history"), list):
            raise ValueError(f"default-policy history must be a list for {policy_id}")
        raw_flags = record.get("flag")
        flags = raw_flags if isinstance(raw_flags, list) else [raw_flags]
        if not flags or any(
            not isinstance(token, str) or token not in _DEFAULT_POLICY_FLAG_NAMES
            for token in flags
        ):
            raise ValueError(f"invalid default-policy flag for {policy_id}")
        policies[policy_id] = dict(record)
    return policies


def load_policy_table() -> Mapping[str, Mapping[str, object]]:
    """Return the process-cached registry keyed by policy id."""

    return _load_policy_table(POLICY_PATH)


def _decimal(value: object, *, context_name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{context_name} must be numeric") from exc
    if not result.is_finite():
        raise ValueError(f"{context_name} must be finite")
    return result


def _resolved_value(
    policy_id: str,
    entry: Mapping[str, object],
    context: Mapping[str, object],
) -> object:
    value = entry.get("value")
    if value is not None:
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            return _decimal(value, context_name=f"{policy_id} value")
        if isinstance(value, str):
            try:
                return _decimal(value, context_name=f"{policy_id} value")
            except ValueError:
                return value
        return value

    formula = entry.get("formula")
    if policy_id == "DP-003":
        if "P_total" not in context:
            raise ValueError("DP-003 requires P_total in Pa")
        assert isinstance(formula, str)
        match = re.fullmatch(r"pO2 = ([0-9]+(?:\.[0-9]+)?) · P_total", formula)
        if match is None:
            raise ValueError("DP-003 formula has an unsupported form")
        # Premise: dry-air oxygen is its mole fraction times total pressure.
        # The fraction is dimensionless, so the result remains Pa; at P=0,
        # pO2=0.
        return Decimal(match.group(1)) * _decimal(
            context["P_total"], context_name="P_total"
        )
    if policy_id == "DP-016":
        inferred_phase = context.get("inferred_phase")
        if not isinstance(inferred_phase, str):
            raise ValueError("DP-016 requires the owning inferred_phase result")
        try:
            return Phase(inferred_phase).value
        except ValueError as exc:
            raise ValueError("DP-016 inferred_phase is not a phase token") from exc
    if policy_id == "DP-107":
        inferred_method = context.get("inferred_method")
        if not isinstance(inferred_method, str):
            raise ValueError("DP-107 requires the owning inferred_method result")
        try:
            return MethodToken(inferred_method).value
        except ValueError as exc:
            raise ValueError("DP-107 inferred_method is not a method token") from exc
    if policy_id == "DP-007":
        selected_area = context.get("selected_area_m2")
        if selected_area is None:
            raise ValueError(
                "DP-007 requires selected_area_m2 from the equipment sizing owner"
            )
        selected_area_m2 = _decimal(
            selected_area, context_name="DP-007 selected_area_m2"
        )
        if selected_area_m2 <= 0:
            raise ValueError("DP-007 selected_area_m2 must be positive")
        return selected_area_m2
    if policy_id == "DP-008":
        if "runtime_default" not in context:
            raise ValueError("DP-008 requires the owning runtime_default")
        return context["runtime_default"]
    if policy_id == "DP-019":
        from simulator.diagnostic_helpers.binary_pot_battery import (
            DP_019_VACUUM_BOUNDARY_BAR,
        )
        from simulator.physical_constants import PA_PER_BAR

        # The vacuum method has a finite engine boundary in bar. Multiplying
        # bar by Pa/bar returns Pa; the source boundary is the limiting vacuum.
        return Decimal(str(DP_019_VACUUM_BOUNDARY_BAR)) * Decimal(
            str(PA_PER_BAR)
        )
    if policy_id == "DP-012" and "printed_value" in context:
        return context["printed_value"]
    raise ValueError(
        f"{policy_id} requires a value from its owning formula result: "
        f"{entry.get('formula_owner', 'no formula owner recorded')}"
    )


def resolve(policy_id: str, **context: object) -> Resolved:
    """Resolve one registered policy and retain its audit metadata."""

    try:
        entry = load_policy_table()[policy_id]
    except KeyError as exc:
        raise KeyError(f"unknown default policy id: {policy_id}") from exc
    if entry.get("status") == "rejected":
        replaced_by = entry.get("replaced_by")
        replacement = (
            " or ".join(str(item) for item in replaced_by)
            if isinstance(replaced_by, list)
            else "no replacement recorded"
        )
        reason = entry.get("rejection_reason") or "rejected by ruling"
        raise ValueError(
            f"default policy {policy_id} is rejected: {reason}; use {replacement}"
        )
    raw_flag = entry["flag"]
    if isinstance(raw_flag, list):
        selected_flag = context.get("flag")
        if selected_flag not in raw_flag:
            raise ValueError(f"{policy_id} requires a flag choice from its table entry")
        flag_token = selected_flag
    else:
        flag_token = raw_flag
    try:
        flag = NoticeKind(str(flag_token))
    except ValueError as exc:
        raise ValueError(f"unrecognized default-policy flag: {flag_token!r}") from exc
    if flag not in DEFAULT_POLICY_NOTICE_KINDS:
        raise ValueError(f"unrecognized default-policy flag: {flag_token!r}")

    value = _resolved_value(policy_id, entry, context)
    units = entry.get("units")
    if not isinstance(units, str) or not units:
        raise ValueError(f"{policy_id} units must be a non-empty string")
    payload: dict[str, object] = {
        "policy_id": policy_id,
        "policy_version": entry["version"],
        "policy_status": entry["status"],
        "value": value,
        "units": units,
        "flag": flag.value,
        "policy": {
            "id": policy_id,
            "version": entry["version"],
            "status": entry["status"],
            "value": (
                format(value.normalize(), "f")
                if isinstance(value, Decimal)
                else str(value)
            ),
            "units": units,
        },
    }
    for key in (
        "condition",
        "formula",
        "formula_owner",
        "proposed_by",
        "proposed_at",
        "evidence",
        "canonized_by",
        "canonized_at",
        "ruling",
        "history",
    ):
        if key in entry:
            payload[key] = entry[key]
    if "flag_context" in entry:
        context_key = entry["flag_context"]
        if not isinstance(context_key, str) or context_key not in context:
            raise ValueError(f"{policy_id} requires its {context_key} context")
        payload["flag_context"] = context[context_key]
    return Resolved(value=value, units=units, flag=flag, payload=payload)


def append_proposal(
    entry: Mapping[str, object],
    *,
    proposed_by: str | None = None,
    proposed_at: str | None = None,
    evidence: object,
) -> str:
    """Append a proposal using the id allocated by the controller."""

    raw = load_cached_safe_yaml(POLICY_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping) or raw.get("schema_version") != 1:
        raise ValueError("default-policy schema_version must be 1")
    policies = raw.get("policies")
    if not isinstance(policies, list):
        raise ValueError("default-policy policies must be a list")
    existing_ids = {
        str(policy.get("id"))
        for policy in policies
        if isinstance(policy, Mapping) and policy.get("id") is not None
    }
    supplied_id = entry.get("id")
    if (
        not isinstance(supplied_id, str)
        or re.fullmatch(r"DP-1\d{2}", supplied_id) is None
    ):
        raise ValueError("proposal must name its allocated DP-1xx id")
    if supplied_id in existing_ids:
        raise ValueError(f"default-policy id {supplied_id} already exists")
    policy_id = supplied_id

    required_proposal_fields = {
        "condition",
        "units",
        "physical_basis",
        "bias",
        "flag",
    }
    if not required_proposal_fields <= entry.keys():
        raise ValueError("proposal is missing condition, units, basis, bias, or flag")
    if ("value" in entry) == ("formula" in entry):
        raise ValueError("proposal must have exactly one of value or formula")
    if "formula" in entry and not entry.get("formula_owner"):
        raise ValueError("formula proposal must name its owning function or constant")
    if any(
        not isinstance(entry.get(field), str) or not entry.get(field)
        for field in ("condition", "units", "physical_basis", "bias")
    ):
        raise ValueError("proposal condition, units, basis, and bias must be non-empty")
    if "value" in entry and entry["value"] is None:
        raise ValueError("proposal value is required")
    raw_flags = entry["flag"]
    flags = raw_flags if isinstance(raw_flags, list) else [raw_flags]
    if not flags or any(
        not isinstance(flag, str) or flag not in _DEFAULT_POLICY_FLAG_NAMES
        for flag in flags
    ):
        raise ValueError("proposal flag must be a closed default-policy token")
    proposer = proposed_by or os.environ.get("GOALFLIGHT_DISPATCH_ID")
    if not proposer:
        raise ValueError("proposed_by or GOALFLIGHT_DISPATCH_ID is required")
    timestamp = proposed_at or datetime.now(timezone.utc).isoformat()
    try:
        parsed_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("proposed_at must be an ISO-8601 UTC timestamp") from exc
    if (
        parsed_timestamp.tzinfo is None
        or parsed_timestamp.utcoffset() != timezone.utc.utcoffset(parsed_timestamp)
    ):
        raise ValueError("proposed_at must be an ISO-8601 UTC timestamp")
    if not evidence:
        raise ValueError("proposal evidence is required")

    proposal = dict(entry)
    version = proposal.get("version", 1)
    if not isinstance(version, (int, str)) or not str(version):
        raise ValueError("proposal version must be a non-empty string or integer")
    proposal.update(
        {
            "id": policy_id,
            "ruling": None,
            "version": version,
            "status": "proposed",
            "proposed_by": proposer,
            "proposed_at": timestamp,
            "evidence": evidence,
            "canonized_by": None,
            "canonized_at": None,
            "history": [],
        }
    )
    output = dict(raw)
    output["policies"] = [*policies, proposal]
    POLICY_PATH.write_text(
        yaml.safe_dump(output, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    _load_policy_table.cache_clear()
    return policy_id
