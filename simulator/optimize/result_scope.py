"""Shared SQL selector scope for optimizer result reads."""

from __future__ import annotations

from typing import Any, Mapping

from simulator.chemistry.kernel.config import OXYGEN_SINK_CHANNEL_MODE_KEY
from simulator.optimize.canonical import canonical_json_dumps, normalize_canonical_value
from simulator.optimize.evalspec import EvalSpec, lab_overlay_scope_payload


def selector_where(
    feedstock_id: str | None,
    *,
    profile_id: str | None,
    fidelity: str | None,
    code_version: str | None,
    data_digests: Mapping[str, str] | None = None,
    data_digests_json: str | None = None,
    result_scope: Mapping[str, Any] | None = None,
    result_scope_json: str | None = None,
) -> tuple[str, tuple[Any, ...]]:
    if data_digests is not None and data_digests_json is not None:
        raise ValueError("pass data_digests or data_digests_json, not both")
    if result_scope is not None and result_scope_json is not None:
        raise ValueError("pass result_scope or result_scope_json, not both")
    active_data_digests = (
        _canonical_json(data_digests)
        if data_digests is not None
        else data_digests_json
    )
    active_result_scope = (
        _canonical_json(result_scope)
        if result_scope is not None
        else result_scope_json
    )
    if code_version is None or active_data_digests is None:
        raise ValueError(
            "query/best require current code_version and data_digests scope"
        )
    clauses = [
        "code_version = ?",
        "data_digests = ?",
    ]
    params: list[Any] = [code_version, active_data_digests]
    if active_result_scope not in (None, _canonical_json({})):
        clauses.append("result_scope = ?")
        params.append(active_result_scope)
    if feedstock_id is not None:
        clauses.insert(0, "feedstock_id = ?")
        params.insert(0, feedstock_id)
    if profile_id is not None:
        clauses.append("profile_id = ?")
        params.append(profile_id)
    if fidelity is not None:
        clauses.append("fidelity = ?")
        params.append(fidelity)
    return " AND ".join(clauses), tuple(params)


def result_scope_payload(eval_spec: EvalSpec) -> dict[str, Any]:
    payload = lab_overlay_scope_payload(eval_spec)
    mode = eval_spec.chemistry_kernel.get(OXYGEN_SINK_CHANNEL_MODE_KEY)
    if mode:
        payload[OXYGEN_SINK_CHANNEL_MODE_KEY] = mode
    return payload


def result_scope_json(eval_spec: EvalSpec) -> str:
    return _canonical_json(result_scope_payload(eval_spec))


def _canonical_json(value: Any) -> str:
    return canonical_json_dumps(normalize_canonical_value(value))
