"""Post-process diagnostic instruments (additive, cache-neutral)."""

from __future__ import annotations

import json
import math
from typing import Any, Mapping, Sequence

_EPS = 1e-12
PRESSURE_COATING_PARETO_SPECIES = ("Na", "K", "SiO", "Fe")
_PRESSURE_SWEEP_MIN_PA = 1.0e-3
_PRESSURE_SWEEP_MAX_PA = 1500.0
_CURRENT_SETPOINT_LOW_PA = 500.0
_CURRENT_SETPOINT_HIGH_PA = 1500.0
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
WALL_SURFACE_GEOMETRY_PROVENANCE_CODE = (
    "wall_deposit_surface_geometry_provenance"
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
        not _valid_sticking_probability(record.get("alpha_s"))
        or citation_status != "CITED"
        or status != "sourced"
        or output_status in {
            "status_bearing",
            "uncertainty_only",
        }
    )


def _valid_sticking_probability(value: Any) -> bool:
    # alpha_s is a dimensionless sticking/accommodation probability, so its
    # physically admissible range is the closed interval [0, 1].
    number = _finite_float(value)
    return number is not None and 0.0 <= number <= 1.0


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


def _surface_geometry_provenance_notice(
    alpha_notice: Mapping[str, Any],
) -> dict[str, Any]:
    for key in (
        "surface_geometry_provenance",
        "stage_area_geometry_provenance_notice",
    ):
        raw = alpha_notice.get(key)
        if isinstance(raw, Mapping):
            return _plain_mapping(raw)
    return {}


def _surface_geometry_status_bearing(notice: Mapping[str, Any]) -> bool:
    if not notice:
        return False
    if bool(notice.get("provisional", False)):
        return True
    if str(notice.get("output_status", "")).lower() == "status_bearing":
        return True
    if str(notice.get("status", "")).lower() in {"provisional", "proxy"}:
        return True
    if str(notice.get("source_class", "")).lower() == "engineering-default":
        return True
    records = notice.get("stage_area_ratio_provenance_by_stage")
    if isinstance(records, Mapping):
        for record in records.values():
            if isinstance(record, Mapping) and _surface_geometry_status_bearing(
                record
            ):
                return True
    return False


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
    provenance_missing = not records
    has_status_bearing = bool(status_bearing) or provenance_missing
    return {
        "severity": "warning" if has_status_bearing else "info",
        "code": (
            WALL_STICKING_ALPHA_MISSING_CODE
            if provenance_missing
            else WALL_STICKING_ALPHA_UNCERTIFIED_CODE
            if status_bearing
            else WALL_STICKING_ALPHA_NOTICE_CODE
        ),
        "source_class": (
            "status_bearing_material_alpha"
            if has_status_bearing
            else "sourced_material_alpha"
        ) if provenance else (
            "assumption_ungrounded_fitted_coefficient"
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
        "status_bearing_alpha_count": (
            len(species) if provenance_missing else len(status_bearing)
        ),
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
    geometry_notice = _surface_geometry_provenance_notice(notice)
    geometry_status_bearing = _surface_geometry_status_bearing(geometry_notice)
    if not deposited_species:
        return _wall_deposit_authority_payload(
            authoritative=True,
            code=WALL_STICKING_ALPHA_NOTICE_CODE,
            deposited_species=(),
            uncertified_species=(),
            provenance={},
            surface_geometry_provenance=geometry_notice,
            geometry_status_bearing=False,
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
            surface_geometry_provenance=geometry_notice,
            geometry_status_bearing=geometry_status_bearing,
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
            surface_geometry_provenance=geometry_notice,
            geometry_status_bearing=geometry_status_bearing,
        )

    if geometry_status_bearing:
        return _wall_deposit_authority_payload(
            authoritative=False,
            code=WALL_SURFACE_GEOMETRY_PROVENANCE_CODE,
            deposited_species=deposited_species,
            uncertified_species=(),
            provenance=_provenance_subset(provenance, deposited_species),
            surface_geometry_provenance=geometry_notice,
            geometry_status_bearing=True,
            message=(
                "Wall-deposit surface geometry uses provisional or "
                "engineering-default stage areas; coating and fouling readouts "
                "are status-bearing until condenser surface areas are certified."
            ),
        )

    return _wall_deposit_authority_payload(
        authoritative=True,
        code=WALL_STICKING_ALPHA_NOTICE_CODE,
        deposited_species=deposited_species,
        uncertified_species=(),
        provenance=_provenance_subset(provenance, deposited_species),
        surface_geometry_provenance=geometry_notice,
        geometry_status_bearing=False,
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
    surface_geometry_provenance: Mapping[str, Any] | None = None,
    geometry_status_bearing: bool = False,
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
    payload = {
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
    if surface_geometry_provenance:
        payload["surface_geometry_provenance"] = _plain_mapping(
            surface_geometry_provenance)
        payload["surface_geometry_status_bearing"] = bool(
            geometry_status_bearing)
        payload["surface_geometry_code"] = WALL_SURFACE_GEOMETRY_PROVENANCE_CODE
    return payload


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
        and _valid_sticking_probability(record.get("alpha_s"))
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

    deposit_first_hour = _deposit_first_hour_by_segment_species(
        snapshots,
        through_hour=through_hour,
    )
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
        first_hour = deposit_first_hour.get((segment, species))
        later_max_T_C = _later_max_segment_temperature_C(
            segment,
            deposit_last_hour=first_hour,
            history_hours=history_hours,
            through_hour=through_hour,
        )
        try:
            condensation_T_C = _species_condensation_temperature_C(
                species,
                temps=instance_temps,
                vapor_pressure_data=vapor_pressure_data,
            )
        except ValueError:
            result.setdefault(segment, {})[species] = {
                "status": "unavailable",
                "reason": "condensation_temperature_unavailable",
                "deposited_kg": float(deposited_kg),
                "deposit_first_hour": first_hour,
                "deposit_last_hour": last_hour,
                "later_max_T_C": later_max_T_C,
                "condensation_T_C": None,
                "thermal_remobilization_threshold_exceeded": False,
                "re_evaporated_kg": None,
                "pressure_and_flux_modeled": False,
            }
            continue
        threshold_exceeded = (
            later_max_T_C is not None
            and later_max_T_C > condensation_T_C
        )
        result.setdefault(segment, {})[species] = {
            "deposited_kg": float(deposited_kg),
            "deposit_first_hour": first_hour,
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


def _deposit_first_hour_by_segment_species(
    snapshots: Sequence[Any],
    *,
    through_hour: int | None,
) -> dict[tuple[str, str], int]:
    first_hour: dict[tuple[str, str], int] = {}
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
            first_hour.setdefault(pair, hour)
    return first_hour


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
    if index < len(snapshot_hours):
        # Production records campaign_hour before the campaign tick increments,
        # while HourSnapshot.hour is global/post-tick. Index alignment is the
        # supplied conversion into the global snapshot-hour domain.
        return int(snapshot_hours[index])
    if snapshot_hours:
        return None
    if "campaign_hour" in entry:
        campaign_hour = _positive_int(entry["campaign_hour"])
        if campaign_hour is not None:
            return campaign_hour
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


def _pressure_coating_pareto_unavailable(
    target_species: Sequence[str],
    reason: str,
) -> dict[str, Any]:
    return {
        "schema_version": "pressure-coating-pareto-v1",
        "status": "unavailable",
        "reason": reason,
        "gate": {"status": "unavailable"},
        "current": {"status": "unavailable"},
        "by_species": {
            str(species): {"status": "unavailable", "reason": reason}
            for species in target_species
        },
    }


def pressure_coating_pareto_diagnostic(
    sim: Any,
    per_hour: Sequence[Mapping[str, Any]] = (),
    *,
    target_species: Sequence[str] = PRESSURE_COATING_PARETO_SPECIES,
) -> dict[str, Any]:
    from engines.builtin.evaporation_flux import (
        _series_resistance_evaporation_flux_kg_m2_s,
    )
    from simulator.condensation import _knudsen_number
    from simulator.state import MOLAR_MASS
    from simulator.transport_constants import (
        FREE_MOLECULAR_KNUDSEN_MIN,
        VISCOUS_KNUDSEN_MAX,
    )

    condensation_model = getattr(sim, "condensation_model", None)
    latest_evap = dict(getattr(sim, "_last_evaporation_flux_diagnostic", {}) or {})
    series_by_species = dict(latest_evap.get("evaporation_series_resistance") or {})
    knudsen_diagnostic = dict(
        getattr(condensation_model, "last_knudsen_regime_diagnostic", {}) or {}
    )
    segment_records = tuple(knudsen_diagnostic.get("segments", ()) or ())
    if (
        _finite_float(knudsen_diagnostic.get("gas_temperature_C")) is None
        or not str(knudsen_diagnostic.get("carrier_gas") or "")
        or _finite_float(knudsen_diagnostic.get("overhead_pressure_mbar")) is None
        or not segment_records
    ):
        return _pressure_coating_pareto_unavailable(
            target_species,
            "knudsen_regime_diagnostic_unavailable",
        )
    gas_temperature_C = _first_finite(
        knudsen_diagnostic.get("gas_temperature_C"),
        getattr(condensation_model, "gas_temperature_C", None),
        getattr(getattr(sim, "overhead_model", None), "pipe_temperature_C", None),
        getattr(getattr(sim, "melt", None), "temperature_C", 0.0),
    )
    gas_temperature_K = max(float(gas_temperature_C) + 273.15, 1.0)
    carrier_gas = str(
        knudsen_diagnostic.get("carrier_gas")
        or getattr(condensation_model, "carrier_gas", "N2")
        or "N2"
    )
    lengths = _knudsen_characteristic_lengths(
        knudsen_diagnostic,
    )
    if not lengths:
        return _pressure_coating_pareto_unavailable(
            target_species,
            "knudsen_characteristic_length_unavailable",
        )
    controlling = max(
        (
            {
                "name": name,
                "characteristic_length_m": length_m,
                "no_warning_pressure_pa": _pressure_at_kn_threshold(
                    VISCOUS_KNUDSEN_MAX,
                    gas_temperature_K,
                    length_m,
                    carrier_gas,
                ),
                "hard_refusal_pressure_pa": _pressure_at_kn_threshold(
                    FREE_MOLECULAR_KNUDSEN_MIN,
                    gas_temperature_K,
                    length_m,
                    carrier_gas,
                ),
            }
            for name, length_m in lengths
        ),
        key=lambda item: item["no_warning_pressure_pa"],
    )
    controlling_record = next(
        (
            item
            for item in segment_records
            if isinstance(item, Mapping)
            and str(item.get("name") or "segment") == controlling["name"]
        ),
        None,
    )
    controlling_kn = (
        _finite_float(controlling_record.get("knudsen_number"))
        if isinstance(controlling_record, Mapping)
        else None
    )
    controlling_regime = (
        str(controlling_record.get("regime") or "")
        if isinstance(controlling_record, Mapping)
        else ""
    )
    if controlling_kn is None or not controlling_regime:
        return _pressure_coating_pareto_unavailable(
            target_species,
            "controlling_knudsen_segment_unavailable",
        )
    gate_pressure_pa = float(controlling["no_warning_pressure_pa"])
    hard_refusal_pressure_pa = float(controlling["hard_refusal_pressure_pa"])
    pressure_points = _pressure_sweep_points_pa(
        gate_pressure_pa,
        hard_refusal_pressure_pa,
        _first_finite(
            knudsen_diagnostic.get("overhead_pressure_mbar"),
            getattr(getattr(sim, "overhead", None), "pressure_mbar", 0.0),
        )
        * 100.0,
    )
    latest_wall_flux, cumulative_wall = _wall_deposit_fluxes_from_per_hour(per_hour)
    current_pressure_pa = _first_finite(
        knudsen_diagnostic.get("overhead_pressure_mbar"),
        getattr(getattr(sim, "overhead", None), "pressure_mbar", 0.0),
    ) * 100.0

    by_species: dict[str, Any] = {}
    for species in target_species:
        name = str(species)
        series = dict(series_by_species.get(name) or {})
        molar_mass = _molar_mass_kg_mol(sim, name, MOLAR_MASS)
        if not series or molar_mass is None:
            by_species[name] = {
                "status": "unavailable",
                "reason": "species_absent_from_latest_evaporation_series_diagnostic",
                "current_wall_deposit_flux_kg_hr": latest_wall_flux.get(name, 0.0),
                "cumulative_wall_deposit_kg": cumulative_wall.get(name, 0.0),
            }
            continue
        flux_kwargs = {
            "species": name,
            "P_eq_pa": _first_finite(series.get("P_eq_Pa"), 0.0),
            "P_bulk_pa": _first_finite(series.get("P_bulk_Pa"), 0.0),
            "T_surface_K": max(
                _first_finite(
                    getattr(getattr(sim, "melt", None), "temperature_C", 0.0),
                    0.0,
                )
                + 273.15,
                1.0,
            ),
            "molar_mass_kg_mol": molar_mass,
            "alpha_i": _first_finite(series.get("alpha_intrinsic"), 0.0),
            "pipe_diameter_m": _first_finite(
                series.get("transport_length_m"),
                controlling["characteristic_length_m"],
            ),
            "axial_stir_factor": _first_finite(series.get("axial_stir_applied"), 0.0),
            "radial_stir_factor": _first_finite(series.get("radial_stir_applied"), 1.0),
            "cold_skull_envelope": _cold_skull_envelope_for_replay(series),
            "carrier_gas": carrier_gas,
            "T_gas_K": gas_temperature_K,
            "melt_resistance_enabled": bool(
                series.get("melt_resistance_enabled", True)
            ),
            "melt_surface_renewal_base_kg_s_m2_pa": _first_finite(
                series.get("melt_surface_renewal_base_kg_s_m2_pa"),
                1.0e-4,
            ),
            "melt_surface_renewal_source": str(
                series.get("melt_surface_renewal_source")
                or "owner-ratify:melt-side-surface-renewal-v1"
            ),
        }

        def flux_at(pressure_pa: float) -> Any:
            return _series_resistance_evaporation_flux_kg_m2_s(
                **flux_kwargs,
                overhead_pressure_pa=float(pressure_pa),
            )

        gate_flux = flux_at(gate_pressure_pa)
        current_flux = flux_at(current_pressure_pa)
        flux_5mbar = flux_at(_CURRENT_SETPOINT_LOW_PA)
        flux_15mbar = flux_at(_CURRENT_SETPOINT_HIGH_PA)
        by_species[name] = {
            "status": "ok",
            "P_eq_Pa": flux_kwargs["P_eq_pa"],
            "P_bulk_Pa": flux_kwargs["P_bulk_pa"],
            "alpha_intrinsic": flux_kwargs["alpha_i"],
            "transport_length_m": flux_kwargs["pipe_diameter_m"],
            "max_rate_no_warning_pressure_pa": gate_pressure_pa,
            "max_rate_no_warning_pressure_mbar": gate_pressure_pa / 100.0,
            "max_rate_flux_kg_s_m2": gate_flux.flux_kg_s_m2,
            "current_pressure_flux_kg_s_m2": current_flux.flux_kg_s_m2,
            "headroom_vs_current_pressure_factor": _ratio_or_none(
                gate_flux.flux_kg_s_m2,
                current_flux.flux_kg_s_m2,
            ),
            "headroom_vs_5mbar_factor": _ratio_or_none(
                gate_flux.flux_kg_s_m2,
                flux_5mbar.flux_kg_s_m2,
            ),
            "headroom_vs_15mbar_factor": _ratio_or_none(
                gate_flux.flux_kg_s_m2,
                flux_15mbar.flux_kg_s_m2,
            ),
            "current_wall_deposit_flux_kg_hr": latest_wall_flux.get(name, 0.0),
            "cumulative_wall_deposit_kg": cumulative_wall.get(name, 0.0),
            "sweep": [
                {
                    "pressure_pa": pressure_pa,
                    "pressure_mbar": pressure_pa / 100.0,
                    "knudsen_number": _knudsen_number(
                        pressure_pa,
                        gas_temperature_K,
                        float(controlling["characteristic_length_m"]),
                        carrier_gas=carrier_gas,
                    ),
                    "flux_kg_s_m2": flux_at(pressure_pa).flux_kg_s_m2,
                }
                for pressure_pa in pressure_points
            ],
        }

    return {
        "schema_version": "pressure-coating-pareto-v1",
        "status": "ok",
        "pressure_range_pa": {
            "min": _PRESSURE_SWEEP_MIN_PA,
            "max": _PRESSURE_SWEEP_MAX_PA,
        },
        "gate": {
            "no_warning_knudsen_threshold": VISCOUS_KNUDSEN_MAX,
            "no_warning_operator": "<",
            "hard_refusal_knudsen_threshold": FREE_MOLECULAR_KNUDSEN_MIN,
            "hard_refusal_operator": ">=",
            "controlling_segment": controlling["name"],
            "controlling_characteristic_length_m": (
                controlling["characteristic_length_m"]
            ),
            "no_warning_pressure_pa": gate_pressure_pa,
            "no_warning_pressure_mbar": gate_pressure_pa / 100.0,
            "hard_refusal_pressure_pa": hard_refusal_pressure_pa,
            "hard_refusal_pressure_mbar": hard_refusal_pressure_pa / 100.0,
            "characteristic_length_source": (
                "knudsen_regime_diagnostic.segments[*].characteristic_length_m"
            ),
        },
        "current": {
            "overhead_pressure_pa": current_pressure_pa,
            "overhead_pressure_mbar": current_pressure_pa / 100.0,
            "gas_temperature_K": gas_temperature_K,
            "carrier_gas": carrier_gas,
            # At fixed pressure, temperature, and carrier gas, Kn = lambda/L;
            # the smallest characteristic length has the largest Kn and owns
            # both the validity regime and the adjacent numeric Kn claim.
            "knudsen_number": controlling_kn,
            "regime": controlling_regime,
            "segment": controlling["name"],
            "characteristic_length_m": controlling[
                "characteristic_length_m"
            ],
            "knudsen_source": "controlling_segment",
            "distance_from_no_warning_gate_pressure_factor": _ratio_or_none(
                current_pressure_pa,
                gate_pressure_pa,
            ),
            "setpoint_band_distance_from_gate_pressure_factor": {
                "at_5mbar": _ratio_or_none(_CURRENT_SETPOINT_LOW_PA, gate_pressure_pa),
                "at_15mbar": _ratio_or_none(
                    _CURRENT_SETPOINT_HIGH_PA,
                    gate_pressure_pa,
                ),
            },
            "wall_deposit_flux_kg_hr_by_species": latest_wall_flux,
            "wall_deposit_cumulative_kg_by_species": cumulative_wall,
        },
        "by_species": by_species,
    }


def _first_finite(*values: Any) -> float:
    for value in values:
        parsed = _finite_float(value)
        if parsed is not None:
            return parsed
    return 0.0


def _ratio_or_none(numerator: float, denominator: float) -> float | None:
    if denominator <= 0.0:
        return None
    return numerator / denominator


def _pressure_at_kn_threshold(
    threshold: float,
    gas_temperature_K: float,
    characteristic_length_m: float,
    carrier_gas: str,
) -> float:
    from simulator.condensation import _knudsen_number

    if threshold <= 0.0 or characteristic_length_m <= 0.0:
        return math.inf
    low = 1.0e-12
    high = 1.0
    while (
        _knudsen_number(
            high,
            gas_temperature_K,
            characteristic_length_m,
            carrier_gas=carrier_gas,
        )
        > threshold
    ):
        high *= 10.0
    for _ in range(96):
        mid = (low + high) / 2.0
        kn = _knudsen_number(
            mid,
            gas_temperature_K,
            characteristic_length_m,
            carrier_gas=carrier_gas,
        )
        if kn > threshold:
            low = mid
        else:
            high = mid
    return high


def _pressure_sweep_points_pa(
    gate_pressure_pa: float,
    hard_refusal_pressure_pa: float,
    current_pressure_pa: float,
) -> list[float]:
    points = {
        _PRESSURE_SWEEP_MIN_PA,
        _PRESSURE_SWEEP_MAX_PA,
        _CURRENT_SETPOINT_LOW_PA,
        _CURRENT_SETPOINT_HIGH_PA,
        gate_pressure_pa,
        hard_refusal_pressure_pa,
        current_pressure_pa,
    }
    for index in range(15):
        fraction = index / 14.0
        pressure = _PRESSURE_SWEEP_MIN_PA * (
            _PRESSURE_SWEEP_MAX_PA / _PRESSURE_SWEEP_MIN_PA
        ) ** fraction
        points.add(pressure)
    return sorted(
        pressure
        for pressure in points
        if math.isfinite(pressure)
        and _PRESSURE_SWEEP_MIN_PA <= pressure <= _PRESSURE_SWEEP_MAX_PA
    )


def _knudsen_characteristic_lengths(
    diagnostic: Mapping[str, Any],
) -> tuple[tuple[str, float], ...]:
    lengths: list[tuple[str, float]] = []
    for item in diagnostic.get("segments", ()) or ():
        if not isinstance(item, Mapping):
            continue
        length = _finite_float(item.get("characteristic_length_m"))
        if length is not None and length > 0.0:
            lengths.append((str(item.get("name") or "segment"), length))
    return tuple(lengths)


def _molar_mass_kg_mol(
    sim: Any,
    species: str,
    molar_mass_table: Mapping[str, float],
) -> float | None:
    vapor_pressures = getattr(sim, "vapor_pressures", {}) or {}
    for section in ("metals", "oxide_vapors"):
        data = vapor_pressures.get(section, {}) or {}
        species_data = data.get(species, {}) or {}
        if isinstance(species_data, Mapping):
            mass_g_mol = _finite_float(species_data.get("molar_mass_g_mol"))
            if mass_g_mol is not None and mass_g_mol > 0.0:
                return mass_g_mol / 1000.0
    fallback = _finite_float(molar_mass_table.get(species))
    if fallback is not None and fallback > 0.0:
        return fallback / 1000.0
    return None


def _cold_skull_envelope_for_replay(series: Mapping[str, Any]) -> dict[str, float] | None:
    ceiling = _finite_float(series.get("frozen_skull_stir_ceiling"))
    if ceiling is None:
        return None
    return {"frozen_skull_stir_ceiling": ceiling}


def _wall_deposit_fluxes_from_per_hour(
    per_hour: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, float], dict[str, float]]:
    latest: dict[str, float] = {}
    cumulative: dict[str, float] = {}
    rows = [row for row in per_hour if isinstance(row, Mapping)]
    for row in rows:
        for species, kg in _flatten_wall_deposit_species_kg(
            row.get("wall_deposit_delta_kg") or {}
        ).items():
            cumulative[species] = cumulative.get(species, 0.0) + kg
    if rows:
        latest = _flatten_wall_deposit_species_kg(
            rows[-1].get("wall_deposit_delta_kg") or {}
        )
    return dict(sorted(latest.items())), dict(sorted(cumulative.items()))


def _flatten_wall_deposit_species_kg(value: Any) -> dict[str, float]:
    totals: dict[str, float] = {}
    if not isinstance(value, Mapping):
        return totals
    for species_map in value.values():
        if not isinstance(species_map, Mapping):
            continue
        for species, kg in species_map.items():
            amount = _finite_float(kg)
            if amount is None or abs(amount) <= _EPS:
                continue
            name = str(species)
            totals[name] = totals.get(name, 0.0) + amount
    return totals


__all__ = [
    "coating_summary_with_grounded_authority",
    "pressure_coating_pareto_diagnostic",
    "wall_deposit_sticking_authority_status",
    "wall_deposit_remobilization_by_segment_species",
    "wall_sticking_alpha_provenance_notice",
]
