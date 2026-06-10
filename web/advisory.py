"""Web payload adapters for wall and ceramic advisory panels."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from simulator.ceramic_classifier import (
    CeramicClassification,
    CeramicMatch,
    CeramicServiceTemperature,
    classify_ceramic_rump,
)
from simulator.wall_advisor import (
    WALL_ZONE_TEMPERATURES_C,
    WallCompatibilityCell,
    WallMaterialAssessment,
    WallOperatingPoint,
    WallReactiveVerdict,
    WallServiceTemperature,
    advise_wall_materials,
    resolve_wall_operating_point,
)

WALL_ZONE_ORDER = ("hottest", "hot", "rest")
WALL_ZONE_LABELS = {
    "hottest": "Hottest",
    "hot": "Hot",
    "rest": "Rest",
}
_NON_DEPOSIT_FLUE_SPECIES = {"O2", "N2", "CO2"}
_OXIDE_FORMULA_RE = re.compile(r"^(?:[A-Z][a-z]?\d*)*O\d*$")


def active_wall_species_from_flue(
    flue_composition_kg_hr: Mapping[str, Any] | None,
) -> list[str]:
    if not isinstance(flue_composition_kg_hr, Mapping):
        return []
    active: list[str] = []
    for species, value in flue_composition_kg_hr.items():
        label = str(species)
        if label in _NON_DEPOSIT_FLUE_SPECIES:
            continue
        amount = _float_or_none(value)
        if amount is not None and amount > 0.0:
            active.append(label)
    return sorted(set(active))


def wall_advisory_payload(
    active_species: Iterable[str] | None,
    *,
    wall_temp_offset_C: float = 0.0,
    pO2_mbar: float | None = None,
    p_buffer_mbar: float | None = None,
) -> dict[str, Any]:
    species = _normalize_species(active_species)
    operating_point = resolve_wall_operating_point(
        pO2_mbar=pO2_mbar,
        p_buffer_mbar=p_buffer_mbar,
    )
    if not species:
        return {
            "status": "n/a",
            "message": "n/a",
            "active_species": [],
            "operating_point": _operating_point_payload(operating_point),
            "zones": [],
        }

    zones = []
    for zone in WALL_ZONE_ORDER:
        assessments = advise_wall_materials(
            species,
            zone=zone,
            wall_temp_offset_C=wall_temp_offset_C,
            pO2_mbar=pO2_mbar,
            p_buffer_mbar=p_buffer_mbar,
        )
        zone_temperature_C = (
            assessments[0].zone_temperature_C
            if assessments
            else WALL_ZONE_TEMPERATURES_C[zone] + wall_temp_offset_C
        )
        zones.append(
            {
                "zone": zone,
                "label": WALL_ZONE_LABELS[zone],
                "temperature_C": _round_or_none(zone_temperature_C),
                "materials": [
                    _wall_material_payload(assessment)
                    for assessment in assessments
                ],
            }
        )
    return {
        "status": "ok",
        "message": "",
        "active_species": species,
        "operating_point": _operating_point_payload(operating_point),
        "zones": zones,
    }


def _operating_point_payload(
    operating_point: WallOperatingPoint,
) -> dict[str, Any]:
    return {
        "pO2_mbar": _round_or_none(operating_point.pO2_mbar),
        "p_buffer_mbar": _round_or_none(operating_point.p_buffer_mbar),
        "po2_regime": operating_point.po2_regime,
        "pressure_regime": operating_point.pressure_regime,
    }


def ceramic_rump_payload(
    composition_wt_pct: Mapping[str, Any] | None,
    *,
    tolerance_wt_pct: float | None = None,
) -> dict[str, Any]:
    composition = _positive_float_mapping(composition_wt_pct)
    if not composition:
        return {
            "status": "n/a",
            "reason": "n/a",
            "composition_wt_pct": {},
            "match": None,
        }
    kwargs: dict[str, Any] = {}
    if tolerance_wt_pct is not None:
        kwargs["tolerance_wt_pct"] = float(tolerance_wt_pct)
    classification = classify_ceramic_rump(composition, **kwargs)
    return _ceramic_classification_payload(classification, composition)


def oxide_wt_pct_from_kg(species_kg: Mapping[str, Any] | None) -> dict[str, float]:
    if not isinstance(species_kg, Mapping):
        return {}
    oxide_kg = {
        str(species): amount
        for species, value in species_kg.items()
        if _is_oxide_species(str(species))
        for amount in [_float_or_none(value)]
        if amount is not None and amount > 0.0
    }
    total = sum(oxide_kg.values())
    if total <= 0.0:
        return {}
    return {
        species: round(amount / total * 100.0, 3)
        for species, amount in sorted(oxide_kg.items())
    }


def _wall_material_payload(
    assessment: WallMaterialAssessment,
) -> dict[str, Any]:
    return {
        "material_id": assessment.material_id,
        "label": assessment.label,
        "role": assessment.role,
        "rollup": assessment.rollup,
        "temp_verdict": "ok" if assessment.temp_ok else "temperature-limited",
        "temp_ok": assessment.temp_ok,
        "zone_temperature_C": _round_or_none(assessment.zone_temperature_C),
        "limiting_temperature_C": _round_or_none(
            assessment.limiting_temperature_C
        ),
        "service_temp": _wall_service_temp_payload(assessment.service_temp),
        "species": [
            {
                "species": species_name,
                "chemical_attack": _wall_cell_payload(
                    species_assessment.chemical_attack
                ),
                "stickiness": _wall_cell_payload(
                    species_assessment.stickiness
                ),
                "reactive": _wall_reactive_payload(
                    species_assessment.reactive
                ),
            }
            for species_name, species_assessment
            in sorted(assessment.species.items())
        ],
    }


def _wall_reactive_payload(verdict: WallReactiveVerdict) -> dict[str, Any]:
    return {
        "verdict": verdict.verdict,
        "sign": verdict.sign,
        "product_phase": verdict.product_phase,
        "net_liner_delta": verdict.net_liner_delta,
        "regime": verdict.regime_raw,
        "basis": verdict.basis,
        "needs_experiment": verdict.needs_experiment,
        "matched": verdict.matched,
    }


def _wall_service_temp_payload(
    service_temp: WallServiceTemperature,
) -> dict[str, Any]:
    return {
        "continuous_C": _round_or_none(service_temp.continuous_C),
        "max_operating_C": _round_or_none(service_temp.max_operating_C),
        "peak_C": _round_or_none(service_temp.peak_C),
        "degradation_onset_C": _round_or_none(
            service_temp.degradation_onset_C
        ),
        "evidence": service_temp.evidence,
        "note": service_temp.note,
    }


def _wall_cell_payload(cell: WallCompatibilityCell) -> dict[str, Any]:
    display = "uncharacterized" if cell.uncharacterized else cell.value
    return {
        "value": cell.value,
        "display": display or "n/a",
        "evidence": cell.evidence,
        "note": cell.note,
        "uncharacterized": cell.uncharacterized,
        "regime": cell.regime_raw,
        "verdict_eligible": cell.verdict_eligible,
    }


def _ceramic_classification_payload(
    classification: CeramicClassification,
    composition: Mapping[str, float],
) -> dict[str, Any]:
    return {
        "status": classification.status,
        "reason": classification.reason,
        "tolerance_wt_pct": classification.tolerance_wt_pct,
        "composition_wt_pct": {
            species: round(value, 3)
            for species, value in sorted(composition.items())
        },
        "match": (
            _ceramic_match_payload(classification.match)
            if classification.match is not None
            else None
        ),
    }


def _ceramic_match_payload(match: CeramicMatch) -> dict[str, Any]:
    return {
        "ceramic_id": match.ceramic_id,
        "label": match.label,
        "composition_kind": match.composition_kind,
        "service_temp": _ceramic_service_temp_payload(match.service_temp),
        "liner_suitability": dict(match.liner_suitability),
    }


def _ceramic_service_temp_payload(
    service_temp: CeramicServiceTemperature,
) -> dict[str, Any]:
    usable = service_temp.usable_service_C
    kind = service_temp.kind
    if usable is not None:
        display = f"Usable service: {usable:g} C"
        usable_service = True
    elif service_temp.value_C is not None:
        display = (
            f"{kind}: {service_temp.value_C:g} C; "
            "not a usable service rating"
        )
        usable_service = False
    else:
        display = f"{kind}; not a usable service rating"
        usable_service = False
    return {
        "value_C": _round_or_none(service_temp.value_C),
        "kind": kind,
        "usable_service_C": _round_or_none(usable),
        "usable_service": usable_service,
        "display": display,
        "note": service_temp.note,
    }


def _normalize_species(active_species: Iterable[str] | None) -> list[str]:
    if active_species is None:
        return []
    species = []
    for value in active_species:
        label = str(value).strip()
        if label:
            species.append(label)
    return sorted(set(species))


def _positive_float_mapping(
    values: Mapping[str, Any] | None,
) -> dict[str, float]:
    if not isinstance(values, Mapping):
        return {}
    result: dict[str, float] = {}
    for key, value in values.items():
        amount = _float_or_none(value)
        if amount is not None and amount > 0.0:
            result[str(key)] = amount
    return result


def _is_oxide_species(species: str) -> bool:
    if species in {"O2", "H2O", "CO2"}:
        return False
    if species == "REE_oxides":
        return True
    return bool(_OXIDE_FORMULA_RE.fullmatch(species))


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 3)
