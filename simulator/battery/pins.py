"""v2.1 pin_band_records: independent baseline, never regenerated from residuals.

never_widen: a live residual outside its pin_band is a pin FAILURE, never a
re-centre. Changed identity preserves the old pin as a tombstone. Missing
live result is a coverage failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from simulator.battery.enums import MetricOperation, ResidualStatus
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
) -> list[dict[str, Any]]:
    """A live residual outside its pin_band is a FAILURE, never a re-centre.

    Missing live result for a non-tombstone pin is a coverage failure.
    """

    by_key: dict[str, Residual] = {}
    for residual in residuals:
        by_key[residual.key] = residual
    failures: list[dict[str, Any]] = []
    for record in records:
        live = by_key.get(record.key)
        if live is None:
            for alias in record.aliases:
                live = by_key.get(alias)
                if live is not None:
                    break
        if record.tombstone:
            continue
        if live is None:
            failures.append(
                {
                    "key": record.key,
                    "reason": "coverage_failure",
                    "live": None,
                    "centre": None if record.centre is None else str(record.centre),
                    "pin_band": None
                    if record.pin_band_value is None
                    else str(record.pin_band_value),
                }
            )
            continue
        if live.status.value != record.expected_outcome:
            if record.expected_outcome == ResidualStatus.REFUSED.value:
                if live.status is ResidualStatus.REFUSED:
                    continue
                failures.append(
                    {
                        "key": record.key,
                        "reason": "expected_refusal_resurrected",
                        "live": live.status.value,
                        "centre": None if record.centre is None else str(record.centre),
                        "pin_band": None
                        if record.pin_band_value is None
                        else str(record.pin_band_value),
                    }
                )
                continue
        if record.centre is None or record.pin_band_value is None:
            continue
        value = live_numeric(live)
        if value is None:
            failures.append(
                {
                    "key": record.key,
                    "reason": "coverage_failure",
                    "live": live.status.value,
                    "centre": str(record.centre),
                    "pin_band": str(record.pin_band_value),
                }
            )
            continue
        if abs(value - record.centre) > record.pin_band_value:
            failures.append(
                {
                    "key": record.key,
                    "reason": "outside_pin_band",
                    "live": str(value),
                    "centre": str(record.centre),
                    "pin_band": str(record.pin_band_value),
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


def _status_token(status: object) -> str:
    text = str(status or "")
    if text in {s.value for s in ResidualStatus}:
        return text
    if text in {"typed-refusal", "typed_refusal", "refused"}:
        return ResidualStatus.REFUSED.value
    if text == "match":
        return ResidualStatus.MATCH.value
    if text == "mismatch":
        return ResidualStatus.MISMATCH.value
    return ResidualStatus.REFUSED.value


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
                    expected_outcome=_status_token(point.get("status")),
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
                    expected_outcome=_status_token(point.get("status")),
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
