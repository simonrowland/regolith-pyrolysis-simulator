from __future__ import annotations

import math
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

PO2_REGIMES = ("oxidizing", "buffered", "reducing")
PRESSURE_REGIMES = ("vacuum", "millibar_sweep")

# Operating-point thresholds, anchored on the recipe catalog rather than feel:
# - The dosed-O2 SiO2-hold excursion runs pO2 3.0-15.0 mbar
#   (data/setpoints.yaml, pO2_mbar: [3.0, 15.0]); at or above its floor the
#   wall sees the oxidizing branch (e.g. SiC passive SiO2 scale).
# - The pO2-managed Fe path and bakeout windows run 0.5-2.3 mbar
#   (data/setpoints.yaml C2B pO2_mbar [0.8, 2.3]; pO2_bakeout_mbar [0.5, 1.5]):
#   the deposit/locally-O-buffered branch.
# - Below that the overhead is the project-default low-pO2 reducing branch.
# - Buffer gas at or above ~5 mbar is the viscous-flow sweep band
#   (CLAUDE.md SS4: ~5-15 mbar pN2 keeps Kn << 0.01); below it transport is
#   ballistic/vacuum-like.
DOSED_O2_PO2_MIN_MBAR = 3.0
BUFFERED_PO2_MIN_MBAR = 0.5
VISCOUS_SWEEP_MIN_MBAR = 5.0


@dataclass(frozen=True)
class WallOperatingPoint:
    """Normalized overhead-gas operating point the advisor evaluates against."""

    pO2_mbar: float | None
    p_buffer_mbar: float | None
    po2_regime: str
    pressure_regime: str


@dataclass(frozen=True)
class NormalizedRegime:
    """A raw wall_materials.yaml ``regime`` value split onto the two clean axes.

    ``po2_regime`` / ``pressure_regime`` of ``None`` mean the raw value does
    not constrain that axis. ``verdict_eligible`` False marks data-provenance
    rows (air analogs, thin-film morphology tags) that must never drive an
    advisor verdict.
    """

    raw: str
    po2_regime: str | None
    pressure_regime: str | None
    verdict_eligible: bool


# Explicit normalization of every regime value present in
# data/wall_materials.yaml (reactive_exchange rows + stickiness provenance).
# Fail-loud on anything not listed: no substring inference, ever.
REGIME_NORMALIZATION: dict[str, NormalizedRegime] = {
    "oxidizing": NormalizedRegime("oxidizing", "oxidizing", None, True),
    "buffered": NormalizedRegime("buffered", "buffered", None, True),
    "reducing_vacuum": NormalizedRegime("reducing_vacuum", "reducing", "vacuum", True),
    "vacuum": NormalizedRegime("vacuum", None, "vacuum", True),
    "low-pO2": NormalizedRegime("low-pO2", "reducing", None, True),
    # Air-provenance rows are data provenance only: molten pyrolysis is always
    # pumped-down low-pO2, so an air analog may be shown but never drives a
    # verdict (owner decision 2026-06-09).
    "air": NormalizedRegime("air", "oxidizing", None, False),
    # Nanofab thin-film morphology tag, not an operating regime; provenance only.
    "thin-film": NormalizedRegime("thin-film", None, "vacuum", False),
}


def normalize_regime(raw: Any) -> NormalizedRegime:
    if not isinstance(raw, str) or raw not in REGIME_NORMALIZATION:
        raise ValueError(
            f"unmapped wall-materials regime value {raw!r}; add an explicit "
            "entry to REGIME_NORMALIZATION (no substring inference)"
        )
    return REGIME_NORMALIZATION[raw]


def resolve_wall_operating_point(
    pO2_mbar: float | None = None,
    p_buffer_mbar: float | None = None,
) -> WallOperatingPoint:
    """Map the recipe overhead knobs onto the normalized regime axes.

    ``None`` means the knob is not set, which is the project default operating
    point: low-pO2 reducing vacuum (owner decision 2026-06-09). CO2 buffer is
    generic buffer gas: pass its pressure as ``p_buffer_mbar``.
    """
    po2 = _validated_knob("pO2_mbar", pO2_mbar)
    buffer_pressure = _validated_knob("p_buffer_mbar", p_buffer_mbar)
    if po2 is None:
        po2_regime = "reducing"
    elif po2 >= DOSED_O2_PO2_MIN_MBAR:
        po2_regime = "oxidizing"
    elif po2 >= BUFFERED_PO2_MIN_MBAR:
        po2_regime = "buffered"
    else:
        po2_regime = "reducing"
    if buffer_pressure is not None and buffer_pressure >= VISCOUS_SWEEP_MIN_MBAR:
        pressure_regime = "millibar_sweep"
    else:
        pressure_regime = "vacuum"
    return WallOperatingPoint(
        pO2_mbar=po2,
        p_buffer_mbar=buffer_pressure,
        po2_regime=po2_regime,
        pressure_regime=pressure_regime,
    )


def _validated_knob(name: str, value: float | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"{name} must be a non-negative finite value, got {value!r}")
    return number


def _reactive_regime_matches(
    regime: NormalizedRegime, operating_point: WallOperatingPoint
) -> bool:
    """Reactive exchange is the chemical-attack half of the advice, so it is
    selected by the pO2 axis (owner decision 2026-06-09). The pressure axis
    drives the transport/Knudsen deposition half and is surfaced as the
    operating point's ``pressure_regime``; a raw ``reducing_vacuum`` row's
    vacuum component is provenance, not a match constraint (redox hazards such
    as SiC active oxidation or beta-alumina spall are oxygen-potential driven
    and do not vanish under a millibar buffer sweep)."""
    if not regime.verdict_eligible:
        return False
    return regime.po2_regime is None or regime.po2_regime == operating_point.po2_regime


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
    regime_raw: str | None = None
    verdict_eligible: bool = True

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
class WallReactiveVerdict:
    """Per-operating-point verdict from the regime-gated reactive_exchange data."""

    species: str
    verdict: str
    sign: str | None
    product_phase: str | None
    net_liner_delta: str | None
    regime_raw: str | None
    basis: str
    needs_experiment: bool
    matched: bool


@dataclass(frozen=True)
class WallSpeciesAssessment:
    species: str
    chemical_attack: WallCompatibilityCell
    stickiness: WallCompatibilityCell
    reactive: WallReactiveVerdict


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
    operating_point: WallOperatingPoint


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
    pO2_mbar: float | None = None,
    p_buffer_mbar: float | None = None,
    data_path: Path | str = DEFAULT_WALL_MATERIALS_PATH,
) -> list[WallMaterialAssessment]:
    resolved_zone = _normalize_zone(zone)
    temperature_C = _resolve_zone_temperature(
        zone_temperature_C=zone_temperature_C,
        zone=resolved_zone,
        wall_temp_offset_C=wall_temp_offset_C,
    )
    operating_point = resolve_wall_operating_point(
        pO2_mbar=pO2_mbar,
        p_buffer_mbar=p_buffer_mbar,
    )
    species = _normalize_active_species(active_species)
    data = load_wall_materials(data_path)
    reactive_exchange = data.get("reactive_exchange") or {}
    assessments = []
    for material_id, entry in data["materials"].items():
        service_temp = _service_temperature(entry["service_temp"])
        limiting_temperature_C = service_temp.max_operating_C
        temp_ok = limiting_temperature_C is not None and temperature_C <= limiting_temperature_C
        material_exchange = reactive_exchange.get(material_id) or {}
        species_assessments = {
            name: _species_assessment(name, entry, material_exchange, operating_point)
            for name in species
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
                operating_point=operating_point,
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


def _species_assessment(
    species: str,
    entry: dict[str, Any],
    material_exchange: dict[str, Any],
    operating_point: WallOperatingPoint,
) -> WallSpeciesAssessment:
    attack_key = _ATTACK_KEY_BY_SPECIES.get(species)
    stickiness_key = _STICKINESS_KEY_BY_SPECIES.get(species)
    return WallSpeciesAssessment(
        species=species,
        chemical_attack=_chemical_attack_cell(
            entry.get("chemical_attack", {}).get(attack_key)
        ),
        stickiness=_stickiness_cell(
            entry.get("stickiness", {}).get(stickiness_key),
            operating_point,
        ),
        reactive=_reactive_verdict(
            species,
            material_exchange.get(attack_key) if attack_key else None,
            operating_point,
        ),
    )


_REACTIVE_VERDICT_BY_SIGN = {
    "consolidating": "protective",
    "neutral": "tolerable",
    "volatile_or_revolatilizing": "hazardous",
    "expansive_spalling": "hazardous",
    "uncharacterized": "uncharacterized",
}
# Worst-of ordering for co-matched rows: a known hazard outranks an unknown,
# and an unknown outranks anything benign (fail-closed).
_REACTIVE_VERDICT_RANK = {
    "protective": 0,
    "tolerable": 1,
    "uncharacterized": 2,
    "hazardous": 3,
}


def _reactive_verdict(
    species: str,
    cells: list[dict[str, Any]] | None,
    operating_point: WallOperatingPoint,
) -> WallReactiveVerdict:
    worst: WallReactiveVerdict | None = None
    for cell in cells or ():
        if not isinstance(cell, dict):
            raise ValueError(
                f"malformed reactive_exchange cell for species {species!r}: {cell!r}"
            )
        regime = normalize_regime(cell.get("regime"))
        if not _reactive_regime_matches(regime, operating_point):
            continue
        sign = cell.get("sign")
        if sign not in _REACTIVE_VERDICT_BY_SIGN:
            raise ValueError(
                f"unmapped reactive_exchange sign {sign!r} for species {species!r}"
            )
        effect = cell.get("wall_property_effect") or {}
        candidate = WallReactiveVerdict(
            species=species,
            verdict=_REACTIVE_VERDICT_BY_SIGN[sign],
            sign=sign,
            product_phase=_optional_str(cell.get("product_phase")),
            net_liner_delta=_optional_str(cell.get("net_liner_delta")),
            regime_raw=regime.raw,
            basis=str(effect.get("basis") or ""),
            needs_experiment=bool(cell.get("needs_experiment", True)),
            matched=True,
        )
        if worst is None or (
            _REACTIVE_VERDICT_RANK[candidate.verdict]
            > _REACTIVE_VERDICT_RANK[worst.verdict]
        ):
            worst = candidate
    if worst is not None:
        return worst
    return WallReactiveVerdict(
        species=species,
        verdict="uncharacterized",
        sign=None,
        product_phase=None,
        net_liner_delta=None,
        regime_raw=None,
        basis=(
            "No characterized reactive-exchange row for this material/species "
            f"at the {operating_point.po2_regime}/{operating_point.pressure_regime} "
            "operating point; fail-closed, needs experiment."
        ),
        needs_experiment=True,
        matched=False,
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


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


def _stickiness_cell(
    cell: dict[str, Any] | None,
    operating_point: WallOperatingPoint,
) -> WallCompatibilityCell:
    if not isinstance(cell, dict):
        return _uncharacterized_cell(value="uncharacterized")
    stickiness_class = str(cell.get("class", "uncharacterized"))
    evidence = str(cell.get("evidence", "uncharacterized"))
    uncharacterized = evidence == "uncharacterized" or stickiness_class == "uncharacterized"
    regime_raw, verdict_eligible = _provenance_regime(
        cell.get("provenance"),
        operating_point,
    )
    return WallCompatibilityCell(
        value="uncharacterized" if uncharacterized else stickiness_class,
        evidence=evidence,
        citations=tuple(cell.get("citations") or ()),
        note=str(cell.get("note") or ""),
        uncharacterized=uncharacterized,
        regime_raw=regime_raw,
        verdict_eligible=verdict_eligible,
    )


def _provenance_regime(
    provenance: Any,
    operating_point: WallOperatingPoint,
) -> tuple[str | None, bool]:
    if not isinstance(provenance, dict) or provenance.get("regime") is None:
        return None, True
    regime = normalize_regime(provenance["regime"])
    pressure_matches = (
        regime.pressure_regime is None
        or regime.pressure_regime == operating_point.pressure_regime
    )
    return regime.raw, regime.verdict_eligible and pressure_matches


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
    has_hole = False
    has_reactive_hazard = False
    for assessment in species:
        if assessment.chemical_attack.uncharacterized:
            has_hole = True
        else:
            worst_attack = _max_rank(
                worst_attack,
                assessment.chemical_attack.value,
                {"low": 0, "moderate": 1, "high": 2},
            )
        # A provenance-only or pressure-mismatched stickiness row may be shown
        # but never drives a verdict: for verdict purposes it is a data hole.
        if assessment.stickiness.uncharacterized or not assessment.stickiness.verdict_eligible:
            has_hole = True
        else:
            worst_stickiness = _max_rank(
                worst_stickiness,
                assessment.stickiness.value,
                {"sheds": 0, "moderate": 1, "strongly-adhering": 2},
            )
        if assessment.reactive.verdict == "hazardous":
            has_reactive_hazard = True
        elif assessment.reactive.verdict == "uncharacterized":
            has_hole = True
    if worst_attack == "high" or worst_stickiness == "strongly-adhering" or has_reactive_hazard:
        return "risky"
    if has_hole:
        return "uncharacterized"
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
