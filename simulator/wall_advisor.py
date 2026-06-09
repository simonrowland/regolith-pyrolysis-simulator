from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DEFAULT_WALL_MATERIALS_PATH = DATA_DIR / "wall_materials.yaml"

WALL_ZONE_TEMPERATURES_C = {
    "hottest": 1800.0,
    "hot": 1650.0,
    "rest": 1500.0,
}

_ATTACK_KEY_BY_SPECIES = {
    "SiO": "SiO",
    "alkali": "alkali_NaK",
    "Fe": "Fe_FeO",
}
_STICKINESS_KEY_BY_SPECIES = {
    "SiO": "SiO",
    "alkali": "alkali",
    "Fe": "Fe",
}
_SPECIES_ALIASES = {
    "K": "alkali",
    "Na": "alkali",
    "NaK": "alkali",
    "alkali_NaK": "alkali",
    "FeO": "Fe",
}


@dataclass(frozen=True)
class WallServiceTemperature:
    continuous_C: float | None
    max_operating_C: float | None
    peak_C: float | None
    degradation_onset_C: float | None
    evidence: str
    citations: tuple[str, ...]
    note: str


@dataclass(frozen=True)
class WallCompatibilityCell:
    value: str | None
    evidence: str
    citations: tuple[str, ...]
    note: str
    uncharacterized: bool

    @property
    def severity(self) -> str | None:
        return self.value

    @property
    def stickiness_class(self) -> str | None:
        return self.value

    @property
    def classification(self) -> str | None:
        return self.value


@dataclass(frozen=True)
class WallSpeciesAssessment:
    species: str
    chemical_attack: WallCompatibilityCell
    stickiness: WallCompatibilityCell


@dataclass(frozen=True)
class WallMaterialAssessment:
    material_id: str
    label: str
    role: str
    zone: str | None
    zone_temperature_C: float
    service_temp: WallServiceTemperature
    temp_ok: bool
    limiting_temperature_C: float | None
    species: dict[str, WallSpeciesAssessment]
    rollup: str


def load_wall_materials(path: Path | str = DEFAULT_WALL_MATERIALS_PATH) -> dict[str, Any]:
    with Path(path).open() as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("materials"), dict):
        raise ValueError(f"wall materials data is malformed: {path}")
    return data


def advise_wall_materials(
    active_species: Iterable[str],
    *,
    zone_temperature_C: float | None = None,
    zone: str | None = None,
    wall_temp_offset_C: float = 0.0,
    data_path: Path | str = DEFAULT_WALL_MATERIALS_PATH,
) -> list[WallMaterialAssessment]:
    resolved_zone = _normalize_zone(zone)
    temperature_C = _resolve_zone_temperature(
        zone_temperature_C=zone_temperature_C,
        zone=resolved_zone,
        wall_temp_offset_C=wall_temp_offset_C,
    )
    species = _normalize_active_species(active_species)
    data = load_wall_materials(data_path)
    assessments = []
    for material_id, entry in data["materials"].items():
        service_temp = _service_temperature(entry["service_temp"])
        limiting_temperature_C = service_temp.max_operating_C
        temp_ok = limiting_temperature_C is not None and temperature_C <= limiting_temperature_C
        species_assessments = {
            name: _species_assessment(name, entry) for name in species
        }
        rollup = _rollup(temp_ok, species_assessments.values())
        assessments.append(
            WallMaterialAssessment(
                material_id=material_id,
                label=entry["label"],
                role=entry["role"],
                zone=resolved_zone,
                zone_temperature_C=temperature_C,
                service_temp=service_temp,
                temp_ok=temp_ok,
                limiting_temperature_C=limiting_temperature_C,
                species=species_assessments,
                rollup=rollup,
            )
        )
    return assessments


def normalize_vapor_species(species: str) -> str:
    return _SPECIES_ALIASES.get(species, species)


def _normalize_active_species(active_species: Iterable[str]) -> tuple[str, ...]:
    normalized = []
    seen = set()
    for species in active_species:
        name = normalize_vapor_species(species)
        if name not in seen:
            normalized.append(name)
            seen.add(name)
    return tuple(normalized)


def _normalize_zone(zone: str | None) -> str | None:
    if zone is None:
        return None
    key = zone.strip().lower()
    if key not in WALL_ZONE_TEMPERATURES_C:
        raise ValueError(f"unknown wall zone: {zone}")
    return key


def _resolve_zone_temperature(
    *,
    zone_temperature_C: float | None,
    zone: str | None,
    wall_temp_offset_C: float,
) -> float:
    if zone_temperature_C is not None:
        return float(zone_temperature_C) + float(wall_temp_offset_C)
    if zone is None:
        raise ValueError("zone_temperature_C is required when no wall zone is provided")
    return WALL_ZONE_TEMPERATURES_C[zone] + float(wall_temp_offset_C)


def _service_temperature(cell: dict[str, Any]) -> WallServiceTemperature:
    return WallServiceTemperature(
        continuous_C=_optional_float(cell.get("continuous_C")),
        max_operating_C=_optional_float(cell.get("max_operating_C")),
        peak_C=_optional_float(cell.get("peak_C")),
        degradation_onset_C=_optional_float(cell.get("degradation_onset_C")),
        evidence=str(cell.get("evidence", "uncharacterized")),
        citations=tuple(cell.get("citations") or ()),
        note=str(cell.get("note") or ""),
    )


def _species_assessment(species: str, entry: dict[str, Any]) -> WallSpeciesAssessment:
    attack_key = _ATTACK_KEY_BY_SPECIES.get(species)
    stickiness_key = _STICKINESS_KEY_BY_SPECIES.get(species)
    return WallSpeciesAssessment(
        species=species,
        chemical_attack=_chemical_attack_cell(
            entry.get("chemical_attack", {}).get(attack_key)
        ),
        stickiness=_stickiness_cell(
            entry.get("stickiness", {}).get(stickiness_key)
        ),
    )


def _chemical_attack_cell(cell: dict[str, Any] | None) -> WallCompatibilityCell:
    if not isinstance(cell, dict):
        return _uncharacterized_cell(value=None)
    severity = cell.get("severity")
    evidence = str(cell.get("evidence", "uncharacterized"))
    uncharacterized = evidence == "uncharacterized" or severity is None
    return WallCompatibilityCell(
        value=None if uncharacterized else str(severity),
        evidence=evidence,
        citations=tuple(cell.get("citations") or ()),
        note=str(cell.get("note") or ""),
        uncharacterized=uncharacterized,
    )


def _stickiness_cell(cell: dict[str, Any] | None) -> WallCompatibilityCell:
    if not isinstance(cell, dict):
        return _uncharacterized_cell(value="uncharacterized")
    stickiness_class = str(cell.get("class", "uncharacterized"))
    evidence = str(cell.get("evidence", "uncharacterized"))
    uncharacterized = evidence == "uncharacterized" or stickiness_class == "uncharacterized"
    return WallCompatibilityCell(
        value="uncharacterized" if uncharacterized else stickiness_class,
        evidence=evidence,
        citations=tuple(cell.get("citations") or ()),
        note=str(cell.get("note") or ""),
        uncharacterized=uncharacterized,
    )


def _uncharacterized_cell(*, value: str | None) -> WallCompatibilityCell:
    return WallCompatibilityCell(
        value=value,
        evidence="uncharacterized",
        citations=(),
        note="No characterized data for this vapor species/material cell.",
        uncharacterized=True,
    )


def _rollup(temp_ok: bool, species: Iterable[WallSpeciesAssessment]) -> str:
    if not temp_ok:
        return "temperature-limited"
    worst_attack = "low"
    worst_stickiness = "sheds"
    for assessment in species:
        if assessment.chemical_attack.uncharacterized or assessment.stickiness.uncharacterized:
            return "uncharacterized"
        worst_attack = _max_rank(
            worst_attack,
            assessment.chemical_attack.value,
            {"low": 0, "moderate": 1, "high": 2},
        )
        worst_stickiness = _max_rank(
            worst_stickiness,
            assessment.stickiness.value,
            {"sheds": 0, "moderate": 1, "strongly-adhering": 2},
        )
    if worst_attack == "high" or worst_stickiness == "strongly-adhering":
        return "risky"
    if worst_attack == "moderate" or worst_stickiness == "moderate":
        return "caution"
    return "usable"


def _max_rank(current: str, candidate: str | None, ranks: dict[str, int]) -> str:
    if candidate is None:
        return current
    if ranks.get(candidate, -1) > ranks.get(current, -1):
        return candidate
    return current


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
