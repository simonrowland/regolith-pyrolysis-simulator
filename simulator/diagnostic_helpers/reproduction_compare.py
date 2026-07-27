"""Shared, diagnostic-only literature reproduction comparisons."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Mapping, Sequence


COMPARISON_STATUSES = frozenset(
    {
        "match",
        "mismatch",
        "unsupported-observable",
        "unsupported-speciation",
        "assumed-input",
        "out-of-domain",
    }
)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("comparison digests require finite floats")
    return value


def content_digest(value: Any) -> str:
    """Return a stable SHA-256 digest of canonical JSON content."""

    payload = json.dumps(
        _jsonable(value),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ComparisonRecord:
    """Minimum comparison record owned by the reproduction chain contract."""

    case_id: str
    source_id: str
    observable_id: str
    species: str | None
    coordinate: Mapping[str, Any]
    expected_value: float | None
    expected_uncertainty: Mapping[str, Any] | None
    actual_value: float | None
    units: str
    residual: float | None
    status: str
    evidence_scope: str
    source_locator: Mapping[str, Any] | str
    recipe_digest: str
    observation_digest: str
    runtime_digest: str

    def __post_init__(self) -> None:
        if self.status not in COMPARISON_STATUSES:
            raise ValueError(f"unsupported comparison status: {self.status!r}")
        if not self.coordinate:
            raise ValueError("temperature/time/window coordinate is required")
        if self.expected_value is not None and not math.isfinite(self.expected_value):
            raise ValueError("expected_value must be finite")
        if self.actual_value is not None and not math.isfinite(self.actual_value):
            raise ValueError("actual_value must be finite")
        if self.residual is not None and not math.isfinite(self.residual):
            raise ValueError("residual must be finite")

    def as_dict(self) -> dict[str, Any]:
        """Serialize with the chain contract's literal coordinate field."""

        return {
            "case_id": self.case_id,
            "source_id": self.source_id,
            "observable_id": self.observable_id,
            "species": self.species,
            "temperature/time/window": dict(self.coordinate),
            "expected_value": self.expected_value,
            "expected_uncertainty": (
                dict(self.expected_uncertainty)
                if self.expected_uncertainty is not None
                else None
            ),
            "actual_value": self.actual_value,
            "units": self.units,
            "residual": self.residual,
            "status": self.status,
            "evidence_scope": self.evidence_scope,
            "source_locator": self.source_locator,
            "recipe_digest": self.recipe_digest,
            "observation_digest": self.observation_digest,
            "runtime_digest": self.runtime_digest,
        }


def _validated_uncertainty(
    uncertainty: Mapping[str, Any],
) -> tuple[str, float]:
    kind = str(uncertainty.get("kind") or "")
    value = uncertainty.get("value")
    if value is None:
        raise ValueError("numeric observations require uncertainty.value")
    tolerance = float(value)
    if not math.isfinite(tolerance) or tolerance < 0.0:
        raise ValueError("uncertainty.value must be finite and non-negative")
    if kind not in {"absolute", "relative_fraction", "log10_decades"}:
        raise ValueError(f"unsupported uncertainty kind: {kind!r}")
    return kind, tolerance


def _matches_uncertainty(
    expected: float,
    actual: float,
    uncertainty: Mapping[str, Any],
) -> bool:
    kind, tolerance = _validated_uncertainty(uncertainty)
    if kind == "absolute":
        return abs(actual - expected) <= tolerance
    if kind == "relative_fraction":
        return abs(actual - expected) <= abs(expected) * tolerance
    if kind == "log10_decades":
        if expected <= 0.0 or actual <= 0.0:
            return False
        return abs(math.log10(actual / expected)) <= tolerance
    raise AssertionError(f"validated uncertainty kind is unhandled: {kind!r}")


def compare_values(
    *,
    case_id: str,
    source_id: str,
    observable_id: str,
    species: str | None,
    coordinate: Mapping[str, Any],
    expected_value: float | None,
    expected_uncertainty: Mapping[str, Any] | None,
    actual_value: float | None,
    units: str,
    evidence_scope: str,
    source_locator: Mapping[str, Any] | str,
    recipe: Mapping[str, Any],
    observation: Mapping[str, Any],
    runtime: Mapping[str, Any],
    unsupported_speciation: bool = False,
    assumed_input: bool = False,
    out_of_domain: bool = False,
) -> ComparisonRecord:
    """Compare one independent observation to one runtime value."""

    residual = (
        float(actual_value) - float(expected_value)
        if actual_value is not None and expected_value is not None
        else None
    )
    if expected_value is not None and expected_uncertainty is None:
        raise ValueError("numeric observations require cited uncertainty")
    if expected_value is not None:
        _validated_uncertainty(expected_uncertainty)
    if out_of_domain:
        status = "out-of-domain"
    elif unsupported_speciation:
        status = "unsupported-speciation"
    elif expected_value is None or actual_value is None:
        status = "unsupported-observable"
    elif assumed_input:
        status = "assumed-input"
    else:
        status = (
            "match"
            if _matches_uncertainty(
                float(expected_value),
                float(actual_value),
                expected_uncertainty,
            )
            else "mismatch"
        )

    return ComparisonRecord(
        case_id=str(case_id),
        source_id=str(source_id),
        observable_id=str(observable_id),
        species=str(species) if species is not None else None,
        coordinate=dict(coordinate),
        expected_value=(
            float(expected_value) if expected_value is not None else None
        ),
        expected_uncertainty=(
            dict(expected_uncertainty)
            if expected_uncertainty is not None
            else None
        ),
        actual_value=float(actual_value) if actual_value is not None else None,
        units=str(units),
        residual=residual,
        status=status,
        evidence_scope=str(evidence_scope),
        source_locator=source_locator,
        recipe_digest=content_digest(recipe),
        observation_digest=content_digest(observation),
        runtime_digest=content_digest(runtime),
    )


def records_to_json(
    records: Sequence[ComparisonRecord],
    *,
    indent: int = 2,
) -> str:
    return json.dumps(
        [record.as_dict() for record in records],
        allow_nan=False,
        ensure_ascii=True,
        indent=indent,
        sort_keys=False,
    )


def _markdown_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    return str(value)


def records_to_markdown(records: Sequence[ComparisonRecord]) -> str:
    """Return a concise residual table suitable for saved reports."""

    lines = [
        "| case | observable | species | temperature/time/window | expected | "
        "actual | residual | units | status | evidence | source |",
        "|---|---|---|---|---:|---:|---:|---|---|---|---|",
    ]
    for record in records:
        locator = _markdown_value(record.source_locator).replace("|", "\\|")
        coordinate = _markdown_value(record.coordinate).replace("|", "\\|")
        lines.append(
            f"| {record.case_id} | {record.observable_id} | "
            f"{record.species or '-'} | {coordinate} | "
            f"{_markdown_value(record.expected_value)} | "
            f"{_markdown_value(record.actual_value)} | "
            f"{_markdown_value(record.residual)} | {record.units} | "
            f"{record.status} | {record.evidence_scope} | {locator} |"
        )
    return "\n".join(lines)
