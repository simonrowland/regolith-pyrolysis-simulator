from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DEFAULT_CERAMIC_TYPES_PATH = DATA_DIR / "ceramic_types.yaml"
DEFAULT_ANALYTICAL_TOLERANCE_WT_PCT = 0.5


@dataclass(frozen=True)
class CeramicServiceTemperature:
    value_C: float | None
    kind: str
    usable_service_C: float | None
    citations: tuple[str, ...]
    note: str


@dataclass(frozen=True)
class CeramicMatch:
    ceramic_id: str
    label: str
    composition_kind: str
    service_temp: CeramicServiceTemperature
    liner_suitability: dict[str, Any]


@dataclass(frozen=True)
class CeramicClassification:
    match: CeramicMatch | None
    tolerance_wt_pct: float
    status: str
    reason: str


def load_ceramic_types(path: Path | str = DEFAULT_CERAMIC_TYPES_PATH) -> dict[str, Any]:
    with Path(path).open() as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("ceramics"), dict):
        raise ValueError(f"ceramic type data is malformed: {path}")
    return data


def classify_ceramic_rump(
    composition_wt_pct: Mapping[str, float],
    *,
    tolerance_wt_pct: float = DEFAULT_ANALYTICAL_TOLERANCE_WT_PCT,
    data_path: Path | str = DEFAULT_CERAMIC_TYPES_PATH,
) -> CeramicClassification:
    if tolerance_wt_pct < 0:
        raise ValueError("tolerance_wt_pct must be non-negative")
    composition = {oxide: float(value) for oxide, value in composition_wt_pct.items()}
    data = load_ceramic_types(data_path)
    matches: list[tuple[str, dict[str, Any]]] = []
    for ceramic_id, entry in data["ceramics"].items():
        spec = entry["composition"]
        if _matches_composition(spec, composition, tolerance_wt_pct):
            matches.append((ceramic_id, entry))
    if len(matches) == 1:
        ceramic_id, entry = matches[0]
        return CeramicClassification(
            match=_ceramic_match(ceramic_id, entry),
            tolerance_wt_pct=float(tolerance_wt_pct),
            status="match",
            reason="composition matched source-supported classifier window",
        )
    if len(matches) > 1:
        matched_ids = ", ".join(ceramic_id for ceramic_id, _entry in matches)
        return CeramicClassification(
            match=None,
            tolerance_wt_pct=float(tolerance_wt_pct),
            status="ambiguous",
            reason=f"ambiguous ceramic classifier matches: {matched_ids}",
        )
    return CeramicClassification(
        match=None,
        tolerance_wt_pct=float(tolerance_wt_pct),
        status="no-match",
        reason="composition outside source-supported ceramic windows",
    )


def _matches_composition(
    spec: dict[str, Any],
    composition: Mapping[str, float],
    tolerance_wt_pct: float,
) -> bool:
    kind = spec.get("kind")
    if kind == "point-anchor":
        return _matches_point_anchor(spec, composition, tolerance_wt_pct)
    if kind == "window":
        return _matches_window(spec, composition, tolerance_wt_pct)
    raise ValueError(f"unknown ceramic composition kind: {kind}")


def _matches_point_anchor(
    spec: dict[str, Any],
    composition: Mapping[str, float],
    tolerance_wt_pct: float,
) -> bool:
    target = spec.get("wt_pct") or {}
    defining_oxides = set(spec.get("defining_oxides") or target)
    for oxide, expected in target.items():
        if abs(composition.get(oxide, 0.0) - float(expected)) > tolerance_wt_pct:
            return False
    for oxide, actual in composition.items():
        if oxide not in defining_oxides and abs(float(actual)) > tolerance_wt_pct:
            return False
    return True


def _matches_window(
    spec: dict[str, Any],
    composition: Mapping[str, float],
    tolerance_wt_pct: float,
) -> bool:
    window = spec.get("wt_pct_window") or {}
    defining_oxides = set(spec.get("defining_oxides") or window)
    for oxide, bounds in window.items():
        lower, upper = bounds
        actual = composition.get(oxide)
        if actual is None:
            return False
        if actual < float(lower) - tolerance_wt_pct:
            return False
        if actual > float(upper) + tolerance_wt_pct:
            return False
    for oxide, actual in composition.items():
        if oxide not in defining_oxides and abs(float(actual)) > tolerance_wt_pct:
            return False
    return True


def _ceramic_match(ceramic_id: str, entry: dict[str, Any]) -> CeramicMatch:
    service_temp = _service_temp(entry["service_temp"])
    return CeramicMatch(
        ceramic_id=ceramic_id,
        label=entry["label"],
        composition_kind=entry["composition"]["kind"],
        service_temp=service_temp,
        liner_suitability=dict(entry["liner_suitability"]),
    )


def _service_temp(cell: dict[str, Any]) -> CeramicServiceTemperature:
    kind = str(cell.get("kind", "uncharacterized"))
    value = cell.get("value_C")
    value_C = None if value is None else float(value)
    return CeramicServiceTemperature(
        value_C=value_C,
        kind=kind,
        usable_service_C=value_C if kind == "service" else None,
        citations=tuple(cell.get("citations") or ()),
        note=str(cell.get("note") or ""),
    )
