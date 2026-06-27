"""Post-process diagnostic instruments (additive, cache-neutral)."""

from __future__ import annotations

import json
import math
from typing import Any, Mapping, Sequence

_EPS = 1e-12
WALL_STICKING_ALPHA_GROUNDING_TARGET = (
    "data/literature/vacuum_pyrolysis_sticking.yaml"
)
WALL_STICKING_ALPHA_NOTICE_CODE = (
    "wall_deposit_sticking_alpha_provenance"
)
WALL_STICKING_ALPHA_UNCERTIFIED_CODE = (
    "wall_deposit_sticking_alpha_uncertified"
)
WALL_STICKING_ALPHA_MISSING_CODE = (
    "wall_deposit_sticking_alpha_provenance_missing"
)
_WALL_DEPOSIT_AUTHORITY_PAYLOAD_KEYS = frozenset({
    "authoritative",
    "authoritative_for_deposit_mass",
    "authoritative_for_coating",
    "authoritative_for_resinter",
    "deposited_species",
    "uncertified_alpha_species",
})


def _status_bearing_alpha_record(record: Mapping[str, Any]) -> bool:
    citation_status = str(record.get("citation_status", "UNCITED")).upper()
    status = str(record.get("status", "proxy"))
    output_status = str(record.get("output_status", "status_bearing"))
    return (
        citation_status != "CITED"
        or status != "sourced"
        or output_status in {
            "status_bearing",
            "uncertainty_only",
        }
    )


def wall_deposit_sticking_authority_is_payload(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and _WALL_DEPOSIT_AUTHORITY_PAYLOAD_KEYS.issubset(value.keys())
    )


def wall_deposit_sticking_authority_matches_deposits(
    authority: Mapping[str, Any],
    wall_deposit_kg: Mapping[Any, Any],
) -> bool:
    if not wall_deposit_sticking_authority_is_payload(authority):
        return False
    deposited_raw = authority.get("deposited_species")
    if isinstance(deposited_raw, str) or not isinstance(deposited_raw, Sequence):
        return False
    deposited_species = {str(species) for species in deposited_raw}
    expected_species = set(_positive_wall_deposit_species(wall_deposit_kg))
    return deposited_species == expected_species


def wall_sticking_alpha_provenance_notice(
    alpha_s_by_species: Mapping[str, float],
    alpha_provenance_by_species: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a provenance payload for wall-deposition alpha_s values."""

    species_alpha = {
        str(species): float(alpha_s)
        for species, alpha_s in alpha_s_by_species.items()
        if _finite_float(alpha_s) is not None
    }
    if not species_alpha:
        return {}
    species = sorted(species_alpha)
    provenance = {
        str(item): value
        for item, value in (alpha_provenance_by_species or {}).items()
    }
    records = [
        record
        for by_segment in provenance.values()
        if isinstance(by_segment, Mapping)
        for record in by_segment.values()
        if isinstance(record, Mapping)
    ]
    status_bearing = [
        record
        for record in records
        if _status_bearing_alpha_record(record)
    ]
    source_classes = sorted({
        str(record.get("source_class", ""))
        for record in records
        if record.get("source_class")
    })
    has_status_bearing = bool(status_bearing)
    return {
        "severity": "warning" if has_status_bearing else "info",
        "code": (
            WALL_STICKING_ALPHA_UNCERTIFIED_CODE
            if has_status_bearing
            else WALL_STICKING_ALPHA_NOTICE_CODE
        ),
        "source_class": (
            "status_bearing_material_alpha"
            if has_status_bearing
            else "sourced_material_alpha"
        ) if provenance else (
            "status_bearing_sticking_alpha"
            if has_status_bearing
            else "assumption_ungrounded_fitted_coefficient"
        ),
        "source_classes": source_classes,
        "source": (
            "data/literature/vacuum_pyrolysis_sticking.yaml::species; "
            "data/materials.yaml::liner_materials.*.alpha_s_by_species; "
            "data/materials.yaml::default_alpha_s_by_species"
        ),
        "usage": [
            "_stage_alpha_s",
            "_wall_alpha_s",
            "_pressure_isolated_capture_budget_kg",
        ],
        "species": species,
        "alpha_s_by_species": {
            item: species_alpha[item]
            for item in species
        },
        # Reported numbers are the wall-path (_wall_alpha_s) values. The
        # _pressure_isolated_capture_budget_kg path reads the same literature
        # sidecar defaults, so material-specific wall overrides can still
        # differ from the capture-budget alpha_s — do not equate the two.
        "alpha_s_source": "_wall_alpha_s",
        "alpha_s_provenance_by_species": provenance,
        "capture_budget_alpha_s_source": WALL_STICKING_ALPHA_GROUNDING_TARGET,
        "authoritative_for_deposit_mass": not has_status_bearing,
        "deposit_output_status": (
            "status_bearing"
            if has_status_bearing
            else "sourced_with_surface_proxy"
        ),
        "resinter_output_status": (
            "status_bearing"
            if has_status_bearing
            else "sourced_with_surface_proxy"
        ),
        "status_bearing_alpha_count": len(status_bearing),
        "message": (
            "Wall-deposition sticking alpha_s values are read from the "
            "literature sidecar where available; UNCERTIFIED or fail-closed "
            "material cells remain status-bearing for fouling and resinter "
            "verdicts."
        ),
        "grounding_target": WALL_STICKING_ALPHA_GROUNDING_TARGET,
    }


def wall_deposit_sticking_authority_status(
    wall_deposit_kg: Mapping[Any, Any],
    alpha_notice: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return authority status for wall-deposit derived fouling readouts."""

    deposited_species = _positive_wall_deposit_species(wall_deposit_kg)
    notice = dict(alpha_notice or {})
    if wall_deposit_sticking_authority_is_payload(alpha_notice):
        payload_species = _payload_deposited_species(notice)
        if payload_species == deposited_species:
            notice = _plain_mapping(notice)
    if not deposited_species:
        return _wall_deposit_authority_payload(
            authoritative=True,
            code=WALL_STICKING_ALPHA_NOTICE_CODE,
            deposited_species=(),
            uncertified_species=(),
            provenance={},
        )

    provenance = _alpha_provenance_by_species(notice)
    status_bearing_species = _status_bearing_alpha_species(provenance)
    missing_species = tuple(
        species
        for species in deposited_species
        if not _alpha_species_has_provenance_record(provenance.get(species))
    )
    if str(notice.get("code", "")) == WALL_STICKING_ALPHA_MISSING_CODE:
        missing_species = deposited_species
    if missing_species:
        return _wall_deposit_authority_payload(
            authoritative=False,
            code=WALL_STICKING_ALPHA_MISSING_CODE,
            deposited_species=deposited_species,
            uncertified_species=missing_species,
            provenance=_provenance_subset(provenance, deposited_species),
            message=(
                "Wall-deposit sticking alpha authority missing; provenance is "
                "missing, so coating and fouling readouts are non-authoritative "
                "until the coefficient status travels with the deposit."
            ),
        )

    uncertified_species = tuple(
        species for species in deposited_species if species in status_bearing_species
    )
    if uncertified_species:
        return _wall_deposit_authority_payload(
            authoritative=False,
            code=WALL_STICKING_ALPHA_UNCERTIFIED_CODE,
            deposited_species=deposited_species,
            uncertified_species=uncertified_species,
            provenance=_provenance_subset(provenance, deposited_species),
        )

    return _wall_deposit_authority_payload(
        authoritative=True,
        code=WALL_STICKING_ALPHA_NOTICE_CODE,
        deposited_species=deposited_species,
        uncertified_species=(),
        provenance=_provenance_subset(provenance, deposited_species),
    )


def coating_summary_with_grounded_authority(
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a coating summary where positive deposits trust provenance only."""

    result = dict(summary)
    wall_deposit = _coating_wall_deposit_payload(result)
    total_kg = _sum_wall_deposit_kg(wall_deposit)
    if total_kg is None or total_kg <= _EPS:
        return result

    authority_input = result.get("wall_deposit_sticking_authority")
    if isinstance(wall_deposit, Mapping):
        authority = wall_deposit_sticking_authority_status(
            wall_deposit,
            authority_input if isinstance(authority_input, Mapping) else {},
        )
    else:
        authority = _wall_deposit_authority_payload(
            authoritative=False,
            code=WALL_STICKING_ALPHA_MISSING_CODE,
            deposited_species=(),
            uncertified_species=(),
            provenance={},
            message=(
                "Wall-deposit sticking alpha authority missing; provenance is "
                "missing, so coating and fouling readouts are non-authoritative "
                "until the coefficient status travels with the deposit."
            ),
        )

    authoritative = bool(authority.get("authoritative_for_coating", False))
    result["coating_authoritative"] = authoritative
    result["coating_status"] = "available" if authoritative else "warning"
    result["coating_output_status"] = str(
        authority.get("output_status")
        or ("authoritative" if authoritative else "status_bearing")
    )
    result["coating_status_reason"] = (
        "" if authoritative else str(authority.get("message", "non-authoritative coating"))
    )
    result["wall_deposit_sticking_authority"] = _plain_mapping(authority)
    return result


def _coating_wall_deposit_payload(summary: Mapping[str, Any]) -> Any:
    for key in (
        "wall_deposit_kg_by_segment_species",
        "wall_deposit_kg_by_zone_species",
        "wall_deposit_kg",
    ):
        if key in summary:
            return summary[key]
    return None


def _sum_wall_deposit_kg(value: Any) -> float | None:
    if isinstance(value, Mapping):
        total = 0.0
        found = False
        for nested in value.values():
            subtotal = _sum_wall_deposit_kg(nested)
            if subtotal is not None:
                total += subtotal
                found = True
        return total if found else None
    if isinstance(value, (list, tuple)):
        total = 0.0
        found = False
        for nested in value:
            subtotal = _sum_wall_deposit_kg(nested)
            if subtotal is not None:
                total += subtotal
                found = True
        return total if found else None
    return _finite_float(value)


def _wall_deposit_authority_payload(
    *,
    authoritative: bool,
    code: str,
    deposited_species: Sequence[str],
    uncertified_species: Sequence[str],
    provenance: Mapping[str, Any],
    message: str | None = None,
) -> dict[str, Any]:
    if message is None:
        if authoritative:
            message = (
                "Deposited wall species use cited/sourced sticking alpha_s "
                "provenance for coating and fouling readouts."
            )
        else:
            message = (
                "Deposited wall species include UNCERTIFIED or status-bearing "
                "sticking alpha_s; coating and fouling readouts are "
                "non-authoritative."
            )
    return {
        "authoritative": authoritative,
        "authoritative_for_deposit_mass": authoritative,
        "authoritative_for_coating": authoritative,
        "authoritative_for_resinter": authoritative,
        "severity": "info" if authoritative else "warning",
        "code": code,
        "output_status": (
            "sourced_with_surface_proxy" if authoritative else "status_bearing"
        ),
        "deposited_species": list(deposited_species),
        "uncertified_alpha_species": list(uncertified_species),
        "status_bearing_alpha_count": len(uncertified_species),
        "alpha_s_provenance_by_species": _plain_mapping(provenance),
        "grounding_target": WALL_STICKING_ALPHA_GROUNDING_TARGET,
        "message": message,
    }


def _payload_deposited_species(payload: Mapping[str, Any]) -> tuple[str, ...]:
    raw = payload.get("deposited_species")
    if isinstance(raw, str):
        return (raw,) if raw else ()
    if not isinstance(raw, Sequence):
        return ()
    return tuple(sorted(str(species) for species in raw))


def _provenance_subset(
    provenance: Mapping[str, Mapping[str, Any]],
    species: Sequence[str],
) -> dict[str, Any]:
    return {
        item: _plain_mapping(provenance.get(item, {}))
        for item in species
        if item in provenance
    }


def _plain_mapping(values: Mapping[Any, Any]) -> dict[Any, Any]:
    return {
        key: _plain_value(value)
        for key, value in values.items()
    }


def _plain_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _plain_mapping(value)
    if isinstance(value, (set, frozenset)):
        return sorted((_plain_value(item) for item in value), key=repr)
    if isinstance(value, (list, tuple)):
        return [_plain_value(item) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        return str(value)
    return value


def _positive_wall_deposit_species(
    wall_deposit_kg: Mapping[Any, Any],
) -> tuple[str, ...]:
    species: set[str] = set()
    for key, value in wall_deposit_kg.items():
        if isinstance(key, tuple) and len(key) == 2:
            if _positive_number(value):
                species.add(str(key[1]))
            continue
        if isinstance(value, Mapping):
            for nested_species, kg in value.items():
                if _positive_number(kg):
                    species.add(str(nested_species))
            continue
        if _positive_number(value):
            species.add(str(key))
    return tuple(sorted(species))


def _alpha_provenance_by_species(
    alpha_notice: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    raw = alpha_notice.get("alpha_s_provenance_by_species")
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(species): by_segment
        for species, by_segment in raw.items()
        if isinstance(by_segment, Mapping)
    }


def _status_bearing_alpha_species(
    provenance: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    result: set[str] = set()
    for species, by_segment in provenance.items():
        for record in by_segment.values():
            if isinstance(record, Mapping) and _status_bearing_alpha_record(record):
                result.add(str(species))
                break
    return result


def _alpha_species_has_provenance_record(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    return any(
        isinstance(record, Mapping)
        and _finite_float(record.get("alpha_s")) is not None
        for record in value.values()
    )


def _positive_number(value: Any) -> bool:
    number = _finite_float(value)
    return number is not None and number > _EPS


def wall_deposit_remobilization_by_segment_species(
    sim: Any,
    *,
    snapshots: Sequence[Any] | None = None,
    cumulative_deposits_kg: Mapping[tuple[str, str], float] | None = None,
    through_hour: int | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Tag whether later segment wall temperatures exceed species condensation thresholds.

    Each row exposes ``thermal_remobilization_threshold_exceeded`` — a boolean
    comparing later max segment ``pipe_segment_temperatures_C`` against the
    ~1 mbar operator-routing condensation setpoint. Pressure, Knudsen number,
    regime factor, and vapor flux are **not** modeled; ``re_evaporated_kg`` is
    always ``None``. This is a thermal threshold flag, not a mass-transfer or
    re-evaporation result.

    Read-only diagnostic: does not mutate ledger, scores, or cache keys.
    """
    if snapshots is None:
        record = getattr(sim, "record", None)
        snapshots = tuple(getattr(record, "snapshots", ()) or ())
    else:
        snapshots = tuple(snapshots)

    if cumulative_deposits_kg is None:
        cumulative_deposits_kg = _cumulative_wall_deposit_kg(
            snapshots,
            through_hour=through_hour,
        )
    else:
        cumulative_deposits_kg = {
            (str(segment), str(species)): float(kg)
            for (segment, species), kg in cumulative_deposits_kg.items()
            if float(kg) > _EPS
        }

    if not cumulative_deposits_kg:
        return {}

    deposit_last_hour = _deposit_last_hour_by_segment_species(
        snapshots,
        through_hour=through_hour,
    )
    history_hours = _operating_history_hours(sim, snapshots)
    condensation_model = getattr(sim, "condensation_model", None)
    instance_temps = getattr(condensation_model, "condensation_temperatures_C", None)
    vapor_pressure_data = getattr(condensation_model, "vapor_pressure_data", None)
    from simulator.condensation import _species_condensation_temperature_C

    result: dict[str, dict[str, dict[str, Any]]] = {}
    for (segment, species), deposited_kg in cumulative_deposits_kg.items():
        last_hour = deposit_last_hour.get((segment, species))
        later_max_T_C = _later_max_segment_temperature_C(
            segment,
            deposit_last_hour=last_hour,
            history_hours=history_hours,
            through_hour=through_hour,
        )
        condensation_T_C = _species_condensation_temperature_C(
            species,
            temps=instance_temps,
            vapor_pressure_data=vapor_pressure_data,
        )
        threshold_exceeded = (
            later_max_T_C is not None
            and later_max_T_C > condensation_T_C
        )
        result.setdefault(segment, {})[species] = {
            "deposited_kg": float(deposited_kg),
            "deposit_last_hour": last_hour,
            "later_max_T_C": later_max_T_C,
            "condensation_T_C": float(condensation_T_C),
            "thermal_remobilization_threshold_exceeded": bool(threshold_exceeded),
            "re_evaporated_kg": None,
            "pressure_and_flux_modeled": False,
        }
    return result


def _cumulative_wall_deposit_kg(
    snapshots: Sequence[Any],
    *,
    through_hour: int | None,
) -> dict[tuple[str, str], float]:
    totals: dict[tuple[str, str], float] = {}
    for snapshot in snapshots:
        hour = _snapshot_hour(snapshot)
        if through_hour is not None and hour > through_hour:
            continue
        raw = getattr(snapshot, "wall_deposit_by_segment_species_delta", None)
        if not isinstance(raw, Mapping):
            continue
        for key, kg in raw.items():
            if not isinstance(key, tuple) or len(key) != 2:
                continue
            segment, species = str(key[0]), str(key[1])
            amount = _finite_float(kg)
            if amount is None or amount <= _EPS:
                continue
            pair = (segment, species)
            totals[pair] = totals.get(pair, 0.0) + amount
    return totals


def _deposit_last_hour_by_segment_species(
    snapshots: Sequence[Any],
    *,
    through_hour: int | None,
) -> dict[tuple[str, str], int]:
    last_hour: dict[tuple[str, str], int] = {}
    for snapshot in snapshots:
        hour = _snapshot_hour(snapshot)
        if through_hour is not None and hour > through_hour:
            continue
        raw = getattr(snapshot, "wall_deposit_by_segment_species_delta", None)
        if not isinstance(raw, Mapping):
            continue
        for key, kg in raw.items():
            if not isinstance(key, tuple) or len(key) != 2:
                continue
            amount = _finite_float(kg)
            if amount is None or amount <= _EPS:
                continue
            pair = (str(key[0]), str(key[1]))
            last_hour[pair] = hour
    return last_hour


def _operating_history_hours(
    sim: Any,
    snapshots: Sequence[Any],
) -> list[tuple[int, Mapping[str, Any]]]:
    model = getattr(sim, "condensation_model", None)
    history = tuple(getattr(model, "operating_history", ()) or ())
    snapshot_hours = [_snapshot_hour(snapshot) for snapshot in snapshots]
    resolved: list[tuple[int, Mapping[str, Any]]] = []
    for index, entry in enumerate(history):
        if not isinstance(entry, Mapping):
            continue
        hour = _resolve_operating_history_hour(entry, index, snapshot_hours)
        if hour is None:
            continue
        resolved.append((hour, entry))
    return resolved


def _resolve_operating_history_hour(
    entry: Mapping[str, Any],
    index: int,
    snapshot_hours: Sequence[int],
) -> int | None:
    if "hour" in entry:
        return _positive_int(entry["hour"])
    if "campaign_hour" in entry:
        campaign_hour = _positive_int(entry["campaign_hour"])
        if campaign_hour is not None:
            return campaign_hour
    if index < len(snapshot_hours):
        return int(snapshot_hours[index])
    if snapshot_hours:
        return int(snapshot_hours[-1])
    return index + 1 if index >= 0 else None


def _later_max_segment_temperature_C(
    segment: str,
    *,
    deposit_last_hour: int | None,
    history_hours: Sequence[tuple[int, Mapping[str, Any]]],
    through_hour: int | None,
) -> float | None:
    if deposit_last_hour is None:
        return None
    later_max: float | None = None
    for hour, entry in history_hours:
        if hour <= deposit_last_hour:
            continue
        if through_hour is not None and hour > through_hour:
            continue
        segment_temperatures = entry.get("pipe_segment_temperatures_C", {}) or {}
        if not isinstance(segment_temperatures, Mapping):
            continue
        temperature = _finite_float(segment_temperatures.get(segment))
        if temperature is None:
            continue
        later_max = (
            temperature
            if later_max is None
            else max(later_max, temperature)
        )
    return later_max


def _snapshot_hour(snapshot: Any) -> int:
    hour = getattr(snapshot, "hour", None)
    if hour is None:
        return 0
    return int(hour)


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


__all__ = [
    "coating_summary_with_grounded_authority",
    "wall_deposit_sticking_authority_status",
    "wall_deposit_remobilization_by_segment_species",
    "wall_sticking_alpha_provenance_notice",
]
