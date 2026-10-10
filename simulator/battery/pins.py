"""v2.1 pin_band_records: independent baseline, never regenerated from residuals.

never_widen: a live residual outside its pin_band is a pin FAILURE, never a
re-centre. Changed identity preserves the old pin as a tombstone. Missing
live result is a coverage failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from simulator.battery.enums import (
    Engine,
    MetricOperation,
    Quantity,
    ResidualStatus,
    residual_status_token,
)
from simulator.battery.compilation_tier import parent_observation_id
from simulator.battery.migrate import REPO_ROOT, load_yaml
from simulator.battery.records import Residual, as_decimal

PINS_PATH = REPO_ROOT / "data" / "battery" / "pins.yaml"
GIBBS_LEDGER = REPO_ROOT / "data" / "literature" / "gibbs_battery_residual_ledger.yaml"
DIFFERENTIAL_LEDGER = (
    REPO_ROOT / "data" / "literature" / "species_rail_differential_ledger.yaml"
)
VAPOUR_PINS = REPO_ROOT / "data" / "vapour_rail_validation_pins.yaml"


class PinWidenError(ValueError):
    """Raised when a pin_band would widen relative to the committed baseline."""


class PinChannel(StrEnum):
    INTERNAL_ANALYTICAL = "internal-analytical"
    NASA_CEA_9 = "nasa_cea_9"
    VAPOUR_RAIL_PSAT = "vapour_rail_psat"
    ELLINGHAM = "ellingham"
    NASA_CEA_VS_ELLINGHAM = "nasa_cea_vs_ellingham"
    VAPOROCK = "vaporock"
    MASS_SPEC = "mass_spec"
    MELTS = "melts"
    ALPHAMELTS = "alphamelts"
    THERMOENGINE = "thermoengine"
    MAGEMIN = "magemin"
    OPENIMCC = "openimcc"
    TABLE_SELF_CHECK = "table_self_check"


# Legacy residual channel map used by battery score reporting. Compilation pin
# checks use the explicit per-reference sidecar channel map below.
PIN_CHANNEL_ENGINES: Mapping[PinChannel, Engine] = {
    PinChannel.INTERNAL_ANALYTICAL: Engine.INTERNAL_ANALYTICAL,
    PinChannel.NASA_CEA_9: Engine.INTERNAL_ANALYTICAL,
    PinChannel.ELLINGHAM: Engine.INTERNAL_ANALYTICAL,
    PinChannel.VAPOROCK: Engine.VAPOROCK,
    PinChannel.ALPHAMELTS: Engine.ALPHAMELTS,
    PinChannel.THERMOENGINE: Engine.THERMOENGINE,
    PinChannel.MAGEMIN: Engine.MAGEMIN,
    PinChannel.OPENIMCC: Engine.OPENIMCC,
}
PIN_COMPILATION_CHANNEL_ENGINES: Mapping[PinChannel, Engine] = {
    PinChannel.NASA_CEA_9: Engine.NASA_CEA_9,
    PinChannel.ELLINGHAM: Engine.ELLINGHAM,
}


@dataclass(frozen=True)
class PinBandRecord:
    key: str
    expected_outcome: str
    evidence: str
    aliases: tuple[str, ...] = ()
    centre: Decimal | None = None
    metric_operation: str | None = None
    metric_unit: str | None = None
    pin_band_value: Decimal | None = None
    pin_band_unit: str | None = None
    expected_admission: str | None = None
    tombstone: bool = False
    old_key: str | None = None

    def width(self) -> Decimal | None:
        return self.pin_band_value


def _dec(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return as_decimal(value)
    except (TypeError, ValueError, ArithmeticError):
        return None


def pin_from_plain(payload: Mapping[str, Any]) -> PinBandRecord:
    metric = payload.get("metric") or {}
    band = payload.get("pin_band") or {}
    aliases = payload.get("aliases") or ()
    return PinBandRecord(
        key=str(payload["key"]),
        expected_outcome=str(payload["expected_outcome"]),
        evidence=str(payload["evidence"]),
        aliases=tuple(str(a) for a in aliases),
        centre=_dec(payload.get("centre")),
        metric_operation=None if not metric else str(metric.get("operation")),
        metric_unit=None if not metric else str(metric.get("unit")),
        pin_band_value=_dec(band.get("value") if isinstance(band, Mapping) else None),
        pin_band_unit=None if not isinstance(band, Mapping) else str(band.get("unit") or ""),
        expected_admission=(
            None if payload.get("expected_admission") is None else str(payload["expected_admission"])
        ),
        tombstone=bool(payload.get("tombstone")),
        old_key=None if payload.get("old_key") is None else str(payload["old_key"]),
    )


def pin_to_plain(record: PinBandRecord) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "key": record.key,
        "expected_outcome": record.expected_outcome,
        "evidence": record.evidence,
    }
    if record.aliases:
        payload["aliases"] = list(record.aliases)
    if record.centre is not None:
        payload["centre"] = str(record.centre)
    if record.metric_operation and record.metric_unit:
        payload["metric"] = {
            "operation": record.metric_operation,
            "unit": record.metric_unit,
        }
    if record.pin_band_value is not None:
        payload["pin_band"] = {
            "value": str(record.pin_band_value),
            "unit": record.pin_band_unit or "",
        }
    if record.expected_admission is not None:
        payload["expected_admission"] = record.expected_admission
    if record.tombstone:
        payload["tombstone"] = True
    if record.old_key:
        payload["old_key"] = record.old_key
    return payload


def load_pins(path: Path | None = None) -> dict[str, Any]:
    target = path or PINS_PATH
    doc = load_yaml(target)
    if not isinstance(doc, Mapping):
        raise ValueError(f"{target} is not a mapping")
    records = [pin_from_plain(row) for row in doc.get("pin_band_records") or [] if isinstance(row, Mapping)]
    key_map = {str(k): str(v) for k, v in (doc.get("key_map") or {}).items()}
    for record in records:
        if record.old_key:
            key_map.setdefault(record.old_key, record.key)
        for alias in record.aliases:
            key_map.setdefault(alias, record.key)
    return {
        "never_widen": bool(doc.get("never_widen", True)),
        "pin_band_records": records,
        "key_map": key_map,
        "raw": doc,
    }


def assert_never_widen(
    proposed: Sequence[PinBandRecord],
    baseline: Sequence[PinBandRecord],
) -> None:
    """Existing widths may shrink only. A wider band is a pin failure."""

    by_key = {row.key: row for row in baseline}
    for alias_row in baseline:
        for alias in alias_row.aliases:
            by_key.setdefault(alias, alias_row)
    for row in proposed:
        prior = by_key.get(row.key)
        if prior is None:
            for alias in row.aliases:
                prior = by_key.get(alias)
                if prior is not None:
                    break
        if prior is None or prior.width() is None or row.width() is None:
            continue
        if row.width() > prior.width():
            raise PinWidenError(
                f"pin {row.key} widened {prior.width()} → {row.width()}"
            )


def live_numeric(residual: Residual) -> Decimal | None:
    if residual.numeric is None:
        return None
    return residual.numeric.value


def pin_failures(
    residuals: Sequence[Residual],
    records: Sequence[PinBandRecord],
    *,
    compilation_comparisons: Iterable[Mapping[str, object]] | None = None,
    comparisons: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Check residual pins and optional compilation-comparison sidecar pins.

    A live value outside its pin_band is a FAILURE, never a re-centre.
    """

    return _pin_failures_from_live(
        records,
        residuals,
        compilation_comparisons=compilation_comparisons,
        comparisons=comparisons,
    )


@dataclass(frozen=True)
class _PinLiveResidual:
    key: str
    reference: str
    quantity: Quantity | None
    engine: Engine | None
    status: ResidualStatus
    value: Decimal | None
    source_relation: str | None
    call_evidence: str | None


@dataclass(frozen=True)
class _PinLiveComparison:
    key: str
    reference: str
    quantity: Quantity
    engine: Engine
    status: ResidualStatus
    value: Decimal | None
    band: Decimal | None
    source_relation: str = "compilation_comparison"
    call_evidence: str | None = None


def _pin_live_residual(row: Residual | Mapping[str, object]) -> _PinLiveResidual | None:
    if isinstance(row, Residual):
        key = row.key
        reference = row.reference
        status = row.status
        value = live_numeric(row)
        request = row.candidate_request
        quantity_fallback = None if request is None else request.quantity
        engine_fallback = None if request is None else request.engine
        source_relation = row.source_relation.value
        call_evidence = row.execution.call_evidence
    else:
        key = str(row.get("key") or "")
        reference = str(row.get("reference") or "")
        if not key or not reference:
            return None
        status = ResidualStatus(str(row.get("status") or ""))
        numeric = row.get("numeric")
        value = (
            as_decimal(numeric["value"])
            if isinstance(numeric, Mapping) and numeric.get("value") is not None
            else None
        )
        request = row.get("candidate_request")
        quantity_fallback = None
        engine_fallback = None
        if isinstance(request, Mapping):
            try:
                quantity_fallback = Quantity(str(request.get("quantity") or ""))
            except ValueError:
                pass
            try:
                engine_fallback = Engine(str(request.get("engine") or ""))
            except ValueError:
                pass
        relation = row.get("source_relation")
        source_relation = None if relation is None else str(relation)
        execution = row.get("execution")
        evidence = execution.get("call_evidence") if isinstance(execution, Mapping) else None
        call_evidence = None if evidence is None else str(evidence)

    parts = key.rsplit("::", 3)
    quantity: Quantity | None = None
    engine: Engine | None = None
    if parts:
        try:
            engine = Engine(parts[-1])
        except ValueError:
            pass
    if len(parts) == 4:
        try:
            quantity = Quantity(parts[1])
        except ValueError:
            pass
    return _PinLiveResidual(
        key=key,
        reference=reference,
        quantity=quantity or quantity_fallback,
        engine=engine or engine_fallback,
        status=status,
        value=value,
        source_relation=source_relation,
        call_evidence=call_evidence,
    )


def _pin_live_comparison(row: Mapping[str, object]) -> _PinLiveComparison | None:
    reference = parent_observation_id(str(row.get("reference_id") or ""))
    key = str(row.get("comparison_key") or "")
    try:
        quantity = Quantity(str(row.get("quantity") or ""))
        engine = Engine(str(row.get("comparison_channel") or ""))
    except ValueError:
        return None
    status = residual_status_token(row.get("status")) or ResidualStatus.REFUSED
    if not reference or not key or engine not in {
        Engine.NASA_CEA_9,
        Engine.ELLINGHAM,
    }:
        return None
    value = _dec(row.get("value"))
    return _PinLiveComparison(
        key=key,
        reference=reference,
        quantity=quantity,
        engine=engine,
        status=status,
        value=value,
        band=_dec(row.get("band")),
    )


def _pin_comparison_key(key: str) -> str | None:
    parts = key.split("::")
    if len(parts) != 4:
        return None
    compilation_id, point, channel, quantity = parts
    record_id, separator, temperature = point.rpartition(":T=")
    if not separator or not record_id:
        return None
    try:
        temperature_K = float(temperature)
    except ValueError:
        return None
    from simulator.diagnostic_helpers.species_rail_differential import (
        _compilation_comparison_key,
    )

    return _compilation_comparison_key(
        compilation_id,
        record_id,
        temperature_K,
        channel,
        quantity,
    )


def _pin_channel_engine(token: str | None) -> Engine | None:
    if token is None:
        return None
    try:
        channel = PinChannel(token)
    except ValueError:
        return None
    return PIN_CHANNEL_ENGINES.get(channel)


def _pin_compilation_engine(token: str | None) -> Engine | None:
    if token is None:
        return None
    try:
        channel = PinChannel(token)
    except ValueError:
        return None
    return PIN_COMPILATION_CHANNEL_ENGINES.get(channel)


def _pin_failures_from_payloads(
    rows: Iterable[Mapping[str, object]],
    records: Sequence[PinBandRecord],
    *,
    compilation_comparisons: Iterable[Mapping[str, object]] | None = None,
    comparisons: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Check streamed residual payloads without requiring a score rerun."""

    return _pin_failures_from_live(
        records,
        rows,
        compilation_comparisons=compilation_comparisons,
        comparisons=comparisons,
    )


def _pin_failures_from_live(
    records: Sequence[PinBandRecord],
    rows: Iterable[Residual | Mapping[str, object]],
    *,
    compilation_comparisons: Iterable[Mapping[str, object]] | None = None,
    comparisons: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Join pins by reference, quantity, and their typed comparison channel."""

    pins: list[
        tuple[
            PinBandRecord,
            frozenset[str],
            Quantity | None,
            str | None,
            Engine | None,
            str | None,
        ]
    ] = []
    wanted_references: set[str] = set()
    wanted_keys: set[str] = set()
    wanted_comparison_identities: set[tuple[str, Quantity, Engine]] = set()
    for record in records:
        if record.tombstone:
            continue
        parts = record.key.rsplit("::", 3)
        if len(parts) == 4:
            reference, quantity_token, _rail, channel = parts
            try:
                quantity = Quantity(quantity_token)
            except ValueError:
                quantity = None
        else:
            reference = ""
            quantity = None
            channel = parts[-1] if len(parts) > 1 else None
        references = frozenset(
            item
            for item in (reference, *record.aliases, record.old_key)
            if item
        )
        engine = _pin_channel_engine(channel)
        pins.append((record, references, quantity, channel, engine, reference or None))
        wanted_references.update(references)
        wanted_keys.update(item for item in (record.key, *record.aliases, record.old_key) if item)
        comparison_engine = _pin_compilation_engine(channel)
        if quantity is not None and comparison_engine is not None:
            wanted_comparison_identities.update(
                (item, quantity, comparison_engine) for item in references
            )

    by_reference: dict[str, int] = {}
    by_identity: dict[tuple[str, Quantity, Engine], list[_PinLiveResidual]] = {}
    by_key: dict[str, list[_PinLiveResidual]] = {}
    by_comparison_identity: dict[
        tuple[str, Quantity, Engine], list[_PinLiveComparison]
    ] = {}
    by_comparison_key: dict[str, list[_PinLiveComparison]] = {}
    by_comparison_reference: dict[str, int] = {}
    for row in rows:
        live = _pin_live_residual(row)
        if live is None:
            continue
        if live.key in wanted_keys:
            by_key.setdefault(live.key, []).append(live)
        if live.reference not in wanted_references:
            continue
        by_reference[live.reference] = by_reference.get(live.reference, 0) + 1
        if live.quantity is None or live.engine is None:
            continue
        by_identity.setdefault((live.reference, live.quantity, live.engine), []).append(live)

    if compilation_comparisons is not None:
        for row in compilation_comparisons:
            live = _pin_live_comparison(row)
            if live is None:
                continue
            if live.reference in wanted_references:
                by_comparison_reference[live.reference] = (
                    by_comparison_reference.get(live.reference, 0) + 1
                )
            identity = (live.reference, live.quantity, live.engine)
            if identity in wanted_comparison_identities:
                by_comparison_identity.setdefault(identity, []).append(live)
            if live.key in wanted_keys:
                by_comparison_key.setdefault(live.key, []).append(live)

    failures: list[dict[str, Any]] = []
    for record, references, quantity, channel, engine, reference in pins:
        base = {
            "key": record.key,
            "reference": reference,
            "centre": None if record.centre is None else str(record.centre),
            "pin_band": None
            if record.pin_band_value is None
            else str(record.pin_band_value),
            "channel": channel,
            "quantity": None if quantity is None else quantity.value,
        }
        if channel is not None and engine is None:
            failures.append(
                {
                    **base,
                    "reason": "unmapped_pin_channel",
                    "channel_status": "unmapped pin channel",
                    "live": None,
                }
            )
            continue
        comparison_engine = _pin_compilation_engine(channel)
        is_compilation_comparison = (
            compilation_comparisons is not None and comparison_engine is not None
        )
        if is_compilation_comparison:
            comparison_matches: list[_PinLiveComparison] = []
            comparison_keys = list(
                dict.fromkeys((record.key, *record.aliases, record.old_key))
            )
            for key in tuple(comparison_keys):
                canonical = _pin_comparison_key(key) if key else None
                if canonical and canonical not in comparison_keys:
                    comparison_keys.append(canonical)
            for key in comparison_keys:
                if key:
                    comparison_matches.extend(
                        live
                        for live in by_comparison_key.get(key, ())
                        if live.quantity is quantity
                        and live.engine is comparison_engine
                    )
            if not comparison_matches and quantity is not None:
                for item in references:
                    comparison_matches.extend(
                        by_comparison_identity.get(
                            (item, quantity, comparison_engine), ()
                        )
                    )
            if not comparison_matches:
                failures.append(
                    {
                        **base,
                        "reason": "no_compilation_comparison_for_reference",
                        "channel_status": "mapped",
                        "reference_present": any(
                            by_comparison_reference.get(item, 0)
                            for item in references
                        ),
                        "live": None,
                    }
                )
                continue
            if len(comparison_matches) != 1:
                failures.append(
                    {
                        **base,
                        "reason": "ambiguous_compilation_comparison",
                        "channel_status": "mapped",
                        "live": None,
                        "candidate_keys": [live.key for live in comparison_matches],
                    }
                )
                continue
            live: _PinLiveResidual | _PinLiveComparison = comparison_matches[0]
        else:
            matches: list[_PinLiveResidual] = []
            for key in dict.fromkeys((record.key, *record.aliases, record.old_key)):
                if key:
                    matches.extend(by_key.get(key, ()))
            reference_present = bool(matches) or any(
                by_reference.get(item, 0) for item in references
            )
            if not reference_present:
                failures.append(
                    {
                        **base,
                        "reason": "no_live_residual_for_reference",
                        "channel_status": (
                            "unmapped pin channel" if engine is None else "mapped"
                        ),
                        "reference_present": False,
                        "live": None,
                    }
                )
                continue
            if engine is None:
                failures.append(
                    {
                        **base,
                        "reason": "unmapped_pin_channel",
                        "channel_status": "unmapped pin channel",
                        "live": None,
                    }
                )
                continue
            if not matches and quantity is None:
                failures.append(
                    {
                        **base,
                        "reason": "no_live_residual_for_reference",
                        "channel_status": "mapped",
                        "match_detail": "unrecognized_pin_quantity",
                        "live": None,
                    }
                )
                continue
            if not matches:
                for item in references:
                    matches.extend(by_identity.get((item, quantity, engine), ()))
            if not matches:
                failures.append(
                    {
                        **base,
                        "reason": "no_live_residual_for_reference",
                        "channel_status": "mapped",
                        "reference_present": True,
                        "live": None,
                    }
                )
                continue
            if len(matches) != 1:
                failures.append(
                    {
                        **base,
                        "reason": "ambiguous_live_residual",
                        "channel_status": "mapped",
                        "live": None,
                        "candidate_keys": [live.key for live in matches],
                    }
                )
                continue
            live = matches[0]
        source_engine = live.engine or engine
        source = "unknown" if source_engine is None else source_engine.value
        if live.status.value != record.expected_outcome:
            if record.expected_outcome == ResidualStatus.REFUSED.value:
                if live.status is ResidualStatus.REFUSED:
                    continue
                failures.append(
                    {
                        **base,
                        "reason": "expected_refusal_resurrected",
                        "live": live.status.value,
                        "source": source,
                    }
                )
                continue
        if record.centre is None or record.pin_band_value is None:
            continue
        if live.value is None:
            failures.append(
                {
                    **base,
                    "reason": "coverage_failure",
                    "coverage_reason": "live_numeric_missing",
                    "live": live.status.value,
                    "source": source,
                }
            )
            continue
        comparison = {
            "key": record.key,
            "centre": str(record.centre),
            "pin_band": str(record.pin_band_value),
            "live": str(live.value),
            "source": source,
            "source_relation": (
                live.source_relation
                if is_compilation_comparison
                else live.source_relation or "unknown"
            ),
            "call_evidence": live.call_evidence or "",
        }
        delta = abs(live.value - record.centre)
        if is_compilation_comparison:
            comparison.update(
                {
                    "delta": str(delta),
                    "within_pin_band": str(
                        delta <= record.pin_band_value
                    ).lower(),
                    "comparison_band": (
                        ""
                        if live.band is None
                        else str(live.band)
                    ),
                }
            )
        if comparisons is not None:
            comparisons.append(comparison)
        if delta > record.pin_band_value:
            failures.append(
                {
                    **base,
                    "reason": "outside_pin_band",
                    **{key: value for key, value in comparison.items() if key != "key"},
                }
            )
    return failures


def tombstone_for_changed_identity(old: PinBandRecord, *, new_key: str) -> PinBandRecord:
    """Preserve the old pin as a tombstone. Do not re-centre or mint a replacement."""

    aliases = tuple(dict.fromkeys([*old.aliases, old.key]))
    return PinBandRecord(
        key=old.key,
        expected_outcome=old.expected_outcome,
        evidence=old.evidence,
        aliases=aliases,
        centre=old.centre,
        metric_operation=old.metric_operation,
        metric_unit=old.metric_unit,
        pin_band_value=old.pin_band_value,
        pin_band_unit=old.pin_band_unit,
        expected_admission=old.expected_admission,
        tombstone=True,
        old_key=old.old_key or old.key,
    )


def migrate_pin_records(root: Path | None = None) -> dict[str, Any]:
    """Lift existing pin sets into pin_band_records + exhaustive key_map."""

    root = root or REPO_ROOT
    records: list[PinBandRecord] = []
    key_map: dict[str, str] = {}

    gibbs = load_yaml(root / "data" / "literature" / "gibbs_battery_residual_ledger.yaml")
    if isinstance(gibbs, Mapping):
        for point in gibbs.get("points") or []:
            if not isinstance(point, Mapping):
                continue
            old_key = str(point.get("key") or "")
            if not old_key:
                continue
            engine = str(point.get("engine_channel") or "nasa_cea_9")
            new_key = f"{old_key}::delta_fG::thermochemistry::{engine}"
            key_map[old_key] = new_key
            residual = _dec(point.get("residual_kJ_mol"))
            records.append(
                PinBandRecord(
                    key=new_key,
                    expected_outcome=(
                        residual_status_token(point.get("status"))
                        or ResidualStatus.REFUSED
                    ).value,
                    evidence="data/literature/gibbs_battery_residual_ledger.yaml",
                    aliases=(old_key,),
                    centre=residual,
                    metric_operation=MetricOperation.ABSOLUTE.value,
                    metric_unit="kJ_per_declared_mol_basis",
                    pin_band_value=_dec(point.get("band_kJ_mol")) or Decimal("0.05"),
                    pin_band_unit="kJ_per_declared_mol_basis",
                    expected_admission=None,
                    old_key=old_key,
                )
            )

    differential = load_yaml(
        root / "data" / "literature" / "species_rail_differential_ledger.yaml"
    )
    if isinstance(differential, Mapping):
        for point in differential.get("points") or []:
            if not isinstance(point, Mapping):
                continue
            old_key = str(point.get("key") or "")
            if not old_key:
                continue
            engine = str(point.get("engine_channel") or "nasa_cea_9")
            new_key = f"{old_key}::delta_fG::thermochemistry::{engine}"
            key_map[old_key] = new_key
            residual = _dec(point.get("residual_kJ_mol"))
            records.append(
                PinBandRecord(
                    key=new_key,
                    expected_outcome=(
                        residual_status_token(point.get("status"))
                        or ResidualStatus.REFUSED
                    ).value,
                    evidence="data/literature/species_rail_differential_ledger.yaml",
                    aliases=(old_key,),
                    centre=residual,
                    metric_operation=MetricOperation.ABSOLUTE.value,
                    metric_unit="kJ_per_declared_mol_basis",
                    pin_band_value=_dec(point.get("band_kJ_mol")) or Decimal("0.05"),
                    pin_band_unit="kJ_per_declared_mol_basis",
                    old_key=old_key,
                )
            )

    vapour = load_yaml(root / "data" / "vapour_rail_validation_pins.yaml")
    margin = Decimal("0.01")
    if isinstance(vapour, Mapping):
        policy = vapour.get("policy") or {}
        if isinstance(policy, Mapping) and policy.get("margin_dex") is not None:
            margin = as_decimal(policy["margin_dex"])
        species_block = vapour.get("species") or {}
        if isinstance(species_block, Mapping):
            for species, body in species_block.items():
                if not isinstance(body, Mapping):
                    continue
                validations = body.get("validations") or {}
                if not isinstance(validations, Mapping):
                    continue
                for engine, payload in validations.items():
                    if not isinstance(payload, Mapping):
                        continue
                    pinned = _dec(payload.get("pinned_residual_dex"))
                    if pinned is None:
                        continue
                    old_key = f"vapour_rail_validation_pins::{species}::{engine}"
                    new_key = f"{old_key}::p_sat::vapour::{engine}"
                    key_map[old_key] = new_key
                    records.append(
                        PinBandRecord(
                            key=new_key,
                            expected_outcome=ResidualStatus.MATCH.value,
                            evidence="data/vapour_rail_validation_pins.yaml",
                            aliases=(old_key,),
                            centre=Decimal("0"),
                            metric_operation=MetricOperation.DEX.value,
                            metric_unit="dimensionless",
                            pin_band_value=pinned,
                            pin_band_unit="dimensionless",
                            old_key=old_key,
                        )
                    )

    records.sort(key=lambda row: row.key)
    return {
        "schema_version": "battery_pins.v2.1",
        "never_widen": True,
        "generators": [
            "simulator/battery/pins.py",
            "scripts/battery_score.py",
        ],
        "legacy_pin_sources": [
            "data/literature/gibbs_battery_residual_ledger.yaml",
            "data/literature/species_rail_differential_ledger.yaml",
            "data/vapour_rail_validation_pins.yaml",
        ],
        "key_map": dict(sorted(key_map.items())),
        "pin_band_records": [pin_to_plain(row) for row in records],
    }


def write_pins(payload: Mapping[str, Any], path: Path | None = None) -> Path:
    from simulator.battery.migrate import dump_yaml

    target = path or PINS_PATH
    dump_yaml(dict(payload), target)
    return target
