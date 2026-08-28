"""Post-process diagnostic instruments (additive, cache-neutral)."""

from __future__ import annotations

import json
import math
from typing import Any, Mapping, Sequence

from simulator.alpha_kinetics import (
    ALPHA_AUTHORITY_STATUS_FIELD,
    ANALYTICAL_UPPER_BOUND_ALPHA_STATUS,
)

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
WALL_STICKING_ALPHA_OUT_OF_DOMAIN_CODE = (
    "wall_deposit_sticking_alpha_out_of_domain"
)
WALL_STICKING_ALPHA_MISSING_CODE = (
    "wall_deposit_sticking_alpha_provenance_missing"
)
WALL_SURFACE_GEOMETRY_PROVENANCE_CODE = (
    "wall_deposit_surface_geometry_provenance"
)
WALL_SATURATION_PRESSURE_REFUSED_CODE = (
    "wall_deposit_saturation_pressure_refused"
)
WALL_VAPOUR_CARRIER_NON_AUTHORITATIVE_CODE = (
    "wall_deposit_vapour_carrier_non_authoritative"
)
WALL_VAPOUR_CARRIER_AUTHORITY_MISSING_CODE = (
    "wall_deposit_vapour_carrier_authority_missing"
)
WALL_DEPOSIT_ALIAS_CONFLICT_CODE = (
    "wall_deposit_payload_alias_conflict"
)

# Distinct from WALL_STICKING_ALPHA_NOTICE_CODE on purpose.  That code says
# "every deposited species carries cited sticking provenance", which is
# VACUOUSLY TRUE when nothing was deposited *and* when nothing was measured.
# This code says the second thing out loud: coverage is unknown, so no
# downstream fouling verdict may be treated as authoritative (b-296).
WALL_DEPOSIT_COVERAGE_UNKNOWN_CODE = (
    "wall_deposit_coverage_unknown"
)
_COATING_WALL_DEPOSIT_KEYS = (
    "wall_deposit_kg_by_segment_species",
    "wall_deposit_kg_by_zone_species",
    "wall_deposit_kg",
)
_WALL_DEPOSIT_AUTHORITY_PAYLOAD_KEYS = frozenset({
    "authoritative",
    "authoritative_for_deposit_mass",
    "authoritative_for_coating",
    "authoritative_for_resinter",
    "deposited_species",
    "uncertified_alpha_species",
    "vapour_carrier_authority_by_species",
    "vapour_carrier_lineage_by_deposited_species",
})


def _status_bearing_alpha_record(record: Mapping[str, Any]) -> bool:
    citation_status = str(record.get("citation_status", "UNCITED")).upper()
    status = str(record.get("status", "proxy"))
    output_status = str(record.get("output_status", "status_bearing"))
    return (
        not _valid_sticking_probability(record.get("alpha_s"))
        or bool(record.get("alpha_s_extrapolated", False))
        or citation_status != "CITED"
        or status != "sourced"
        or output_status in {
            "status_bearing",
            "uncertainty_only",
        }
    )


def _uncertified_alpha_record(record: Mapping[str, Any]) -> bool:
    citation_status = str(record.get("citation_status", "UNCITED")).upper()
    status = str(record.get("status", "proxy"))
    output_status = str(record.get("output_status", "status_bearing"))
    return (
        not _valid_sticking_probability(record.get("alpha_s"))
        or citation_status != "CITED"
        or status != "sourced"
        or (
            output_status in {"status_bearing", "uncertainty_only"}
            and not bool(record.get("alpha_s_extrapolated", False))
        )
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
    out_of_domain = [
        record
        for record in records
        if bool(record.get("alpha_s_extrapolated", False))
    ]
    uncertified = [
        record for record in records if _uncertified_alpha_record(record)
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
            if uncertified
            else WALL_STICKING_ALPHA_OUT_OF_DOMAIN_CODE
            if out_of_domain
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
        "out_of_domain_alpha_count": len(out_of_domain),
        "out_of_domain_alpha_species": sorted({
            str(record.get("species"))
            for record in out_of_domain
            if record.get("species")
        }),
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

    from simulator.vapour_rail.instrumentation import (
        VAPOUR_CARRIER_AUTHORITY_AUTHORITATIVE,
        VAPOUR_CARRIER_AUTHORITY_MISSING,
        VAPOUR_CARRIER_AUTHORITY_PROVEN_ZERO,
        VAPOUR_CARRIER_AUTHORITY_REFUSED,
        VAPOUR_CARRIER_AUTHORITY_STATUS_BEARING,
        vapour_carrier_authority_severity,
        vapour_carrier_authority_status,
        vapour_carrier_lineage_species,
    )

    deposited_species = _positive_wall_deposit_species(wall_deposit_kg)
    notice = dict(alpha_notice or {})
    if wall_deposit_sticking_authority_is_payload(alpha_notice):
        payload_species = _payload_deposited_species(notice)
        if payload_species == deposited_species:
            notice = _plain_mapping(notice)
    saturation_pressure_refusals = (
        _wall_saturation_pressure_refusals_by_species(notice)
    )
    carrier_authority = _vapour_carrier_authority_by_species(notice)
    carrier_lineage = _vapour_carrier_lineage_by_deposited_species(notice)
    carrier_species = tuple(sorted(
        set(deposited_species) | set(carrier_authority)
    ))
    def _lineage_sources(species: str) -> tuple[str, ...]:
        return vapour_carrier_lineage_species(
            carrier_lineage.get(species),
            default_species=species,
        )

    def _combined_carrier_status(species: str) -> str:
        statuses = (
            vapour_carrier_authority_status(
                carrier_authority.get(source_species),
                expected_species_id=source_species,
            )
            for source_species in _lineage_sources(species)
        )
        return max(
            statuses,
            key=vapour_carrier_authority_severity,
        )

    carrier_status_by_species = {
        species: _combined_carrier_status(species)
        for species in carrier_species
    }
    non_authoritative_carrier_species = tuple(sorted(
        species
        for species, status in carrier_status_by_species.items()
        if status != VAPOUR_CARRIER_AUTHORITY_AUTHORITATIVE
    ))
    refused_carrier_species = tuple(sorted(
        species
        for species, status in carrier_status_by_species.items()
        if status == VAPOUR_CARRIER_AUTHORITY_REFUSED
    ))
    proven_zero_carrier_species = tuple(sorted(
        species
        for species, status in carrier_status_by_species.items()
        if status == VAPOUR_CARRIER_AUTHORITY_PROVEN_ZERO
    ))
    missing_carrier_authority_species = tuple(sorted(
        species
        for species, status in carrier_status_by_species.items()
        if status == VAPOUR_CARRIER_AUTHORITY_MISSING
    ))
    carrier_authority_kwargs = {
        "vapour_carrier_authority_by_species": carrier_authority,
        "vapour_carrier_lineage_by_deposited_species": carrier_lineage,
        "non_authoritative_carrier_species": (
            non_authoritative_carrier_species
        ),
        "refused_carrier_species": refused_carrier_species,
        "proven_zero_carrier_species": proven_zero_carrier_species,
        "missing_carrier_authority_species": (
            missing_carrier_authority_species
        ),
    }
    refused_species = tuple(sorted(saturation_pressure_refusals))
    geometry_notice = _surface_geometry_provenance_notice(notice)
    geometry_status_bearing = _surface_geometry_status_bearing(geometry_notice)
    provenance = _alpha_provenance_by_species(notice)
    alpha_candidate_species = tuple(sorted(
        set(deposited_species)
        | set(carrier_authority)
        | set(provenance)
    ))
    provenance_species = tuple(sorted({
        source_species
        for species in alpha_candidate_species
        for source_species in _lineage_sources(species)
    }))
    uncertified_source_species = _uncertified_alpha_species(provenance)
    out_of_domain_species = tuple(sorted(
        species
        for species in alpha_candidate_species
        if any(
            source_species in _out_of_domain_alpha_species(provenance)
            for source_species in _lineage_sources(species)
        )
    ))
    deposit_pairs = _positive_wall_deposit_segment_species(wall_deposit_kg)
    if deposit_pairs:
        missing_pairs = tuple(
            pair
            for pair in deposit_pairs
            if not all(
                _alpha_segment_species_has_provenance_record(
                    provenance,
                    segment=pair[0],
                    species=source_species,
                )
                for source_species in _lineage_sources(pair[1])
            )
        )
        missing_species = tuple(sorted({species for _, species in missing_pairs}))
    else:
        missing_pairs = ()
        missing_species = tuple(
            species
            for species in alpha_candidate_species
            if not all(
                _alpha_species_has_provenance_record(
                    provenance.get(source_species)
                )
                for source_species in _lineage_sources(species)
            )
        )
    if str(notice.get("code", "")) == WALL_STICKING_ALPHA_MISSING_CODE:
        missing_species = deposited_species
        missing_pairs = deposit_pairs
    uncertified_species = tuple(
        species
        for species in alpha_candidate_species
        if any(
            source_species in uncertified_source_species
            for source_species in _lineage_sources(species)
        )
    )
    carrier_authority_kwargs["out_of_domain_alpha_species"] = (
        out_of_domain_species
    )
    if (
        not deposited_species
        and not refused_species
        and not non_authoritative_carrier_species
        and not missing_species
        and not uncertified_species
        and not out_of_domain_species
    ):
        # deposited_species is derived POSITIVE-ONLY, so an ABSENT projection
        # and a MEASURED ZERO both arrive here as an empty tuple.  Those are
        # different claims and only one of them may be certified:
        #   sum is None  -> nothing was measured; "all deposited species are
        #                   certified" is vacuously true and must NOT be
        #                   reported as authoritative, or a furnace that was
        #                   never inspected reads as never needing re-sinter.
        #   sum is 0.0   -> the projection was populated and totalled zero.
        #                   That is a PROVEN ZERO and the doctrine keeps it
        #                   authoritative; refusing it would turn a genuine
        #                   clean run into an unknown.
        # The evidence the positive-only filter discarded is still available
        # from wall_deposit_kg itself, which is why the discriminator reads
        # the raw projection rather than deposited_species (b-296; same
        # three-state collapse fixed one layer down in _coating_wall_deposit_selection).
        measured_total_kg = _sum_wall_deposit_kg(wall_deposit_kg)
        if measured_total_kg is None:
            return _wall_deposit_authority_payload(
                authoritative=False,
                code=WALL_DEPOSIT_COVERAGE_UNKNOWN_CODE,
                deposited_species=(),
                uncertified_species=(),
                provenance=_provenance_subset(provenance, provenance_species),
                surface_geometry_provenance=geometry_notice,
                geometry_status_bearing=False,
                message=(
                    'wall-deposit coverage unknown: no wall_deposit_kg '
                    'projection was recorded, so no deposited species could '
                    'be certified and no fouling verdict derived from it is '
                    'authoritative'
                ),
                **carrier_authority_kwargs,
            )
        return _wall_deposit_authority_payload(
            authoritative=True,
            code=WALL_STICKING_ALPHA_NOTICE_CODE,
            deposited_species=(),
            uncertified_species=(),
            provenance=_provenance_subset(provenance, provenance_species),
            surface_geometry_provenance=geometry_notice,
            geometry_status_bearing=False,
            **carrier_authority_kwargs,
        )
    if non_authoritative_carrier_species:
        alpha_status_species = tuple(sorted(
            set(missing_species)
            | set(uncertified_species)
            | set(out_of_domain_species)
        ))
        code = (
            WALL_VAPOUR_CARRIER_AUTHORITY_MISSING_CODE
            if missing_carrier_authority_species
            else WALL_VAPOUR_CARRIER_NON_AUTHORITATIVE_CODE
        )
        status_fragments = [
            f"{species}={carrier_status_by_species[species]}"
            for species in non_authoritative_carrier_species
        ]
        if missing_carrier_authority_species:
            carrier_message = (
                "Vapour carrier authority missing for "
                + ", ".join(missing_carrier_authority_species)
                + "; "
            )
        elif deposited_species:
            carrier_message = (
                "Wall deposition consumed vapour carriers without rail "
                "authority (" + ", ".join(status_fragments) + "); "
            )
        else:
            carrier_message = (
                "Wall routing carried non-authoritative vapour evidence ("
                + ", ".join(status_fragments)
                + "); zero wall deposition cannot be certified from that "
                "evidence; "
            )
        payload = _wall_deposit_authority_payload(
            authoritative=False,
            code=code,
            deposited_species=deposited_species,
            uncertified_species=alpha_status_species,
            provenance=_provenance_subset(provenance, provenance_species),
            surface_geometry_provenance=geometry_notice,
            geometry_status_bearing=geometry_status_bearing,
            message=(
                carrier_message
                + "numerical mass remains computed and ledger-visible, but "
                "coating and fouling readouts are non-authoritative."
            ),
            **carrier_authority_kwargs,
        )
        codes = [code]
        if refused_species:
            codes.append(WALL_SATURATION_PRESSURE_REFUSED_CODE)
        if missing_species:
            codes.append(WALL_STICKING_ALPHA_MISSING_CODE)
        if out_of_domain_species:
            codes.append(WALL_STICKING_ALPHA_OUT_OF_DOMAIN_CODE)
        if uncertified_species:
            codes.append(WALL_STICKING_ALPHA_UNCERTIFIED_CODE)
        payload["codes"] = codes
        payload["vapour_carrier_authority_status_by_species"] = (
            carrier_status_by_species
        )
        if refused_species:
            payload["status_bearing_refusal_count"] = len(refused_species)
            payload["wall_saturation_pressure_refused_species"] = list(
                refused_species
            )
            payload["wall_saturation_pressure_refusals_by_species"] = (
                saturation_pressure_refusals
            )
        return payload
    if refused_species:
        alpha_status_species = tuple(sorted(
            set(missing_species)
            | set(uncertified_species)
            | set(out_of_domain_species)
        ))
        codes = [WALL_SATURATION_PRESSURE_REFUSED_CODE]
        if missing_species:
            codes.append(WALL_STICKING_ALPHA_MISSING_CODE)
        if uncertified_species:
            codes.append(WALL_STICKING_ALPHA_UNCERTIFIED_CODE)
        if out_of_domain_species:
            codes.append(WALL_STICKING_ALPHA_OUT_OF_DOMAIN_CODE)
        message = (
            "Wall saturation pressure was refused outside its source-certified "
            "Antoine domain for "
            + ", ".join(refused_species)
            + "; the resulting zero is not physical evidence of zero "
            "deposition, so coating and fouling readouts are non-authoritative."
        )
        if alpha_status_species:
            message += (
                " Concurrent sticking alpha authority is missing or uncertified "
                "for "
                + ", ".join(alpha_status_species)
                + "."
            )
        payload = _wall_deposit_authority_payload(
            authoritative=False,
            code=WALL_SATURATION_PRESSURE_REFUSED_CODE,
            deposited_species=deposited_species,
            uncertified_species=alpha_status_species,
            provenance=_provenance_subset(provenance, provenance_species),
            surface_geometry_provenance=geometry_notice,
            geometry_status_bearing=geometry_status_bearing,
            message=message,
            **carrier_authority_kwargs,
        )
        payload["codes"] = codes
        payload["status_bearing_refusal_count"] = len(refused_species)
        payload["wall_saturation_pressure_refused_species"] = list(
            refused_species
        )
        payload["wall_saturation_pressure_refusals_by_species"] = (
            saturation_pressure_refusals
        )
        return payload
    if (
        out_of_domain_species
        and not missing_species
        and not uncertified_species
    ):
        return _wall_deposit_authority_payload(
            authoritative=False,
            code=WALL_STICKING_ALPHA_OUT_OF_DOMAIN_CODE,
            deposited_species=deposited_species,
            uncertified_species=(),
            provenance=_provenance_subset(provenance, provenance_species),
            surface_geometry_provenance=geometry_notice,
            geometry_status_bearing=geometry_status_bearing,
            message=(
                "Wall-deposit sticking alpha_s was computed outside its "
                "declared temperature domain for "
                + ", ".join(out_of_domain_species)
                + "; mass remains computed and ledger-visible, but coating "
                "and fouling readouts are status-bearing."
            ),
            **carrier_authority_kwargs,
        )
    if missing_species:
        payload = _wall_deposit_authority_payload(
            authoritative=False,
            code=WALL_STICKING_ALPHA_MISSING_CODE,
            deposited_species=deposited_species,
            uncertified_species=missing_species,
            provenance=_provenance_subset(provenance, provenance_species),
            surface_geometry_provenance=geometry_notice,
            geometry_status_bearing=geometry_status_bearing,
            message=(
                "Wall-deposit sticking alpha authority missing; provenance is "
                "missing, so coating and fouling readouts are non-authoritative "
                "until the coefficient status travels with the deposit."
            ),
            **carrier_authority_kwargs,
        )
        if missing_pairs:
            payload["missing_alpha_segment_species"] = [
                {"segment": segment, "species": species}
                for segment, species in missing_pairs
            ]
        return payload

    if uncertified_species:
        return _wall_deposit_authority_payload(
            authoritative=False,
            code=WALL_STICKING_ALPHA_UNCERTIFIED_CODE,
            deposited_species=deposited_species,
            uncertified_species=uncertified_species,
            provenance=_provenance_subset(provenance, provenance_species),
            surface_geometry_provenance=geometry_notice,
            geometry_status_bearing=geometry_status_bearing,
            **carrier_authority_kwargs,
        )

    if geometry_status_bearing:
        return _wall_deposit_authority_payload(
            authoritative=False,
            code=WALL_SURFACE_GEOMETRY_PROVENANCE_CODE,
            deposited_species=deposited_species,
            uncertified_species=(),
            provenance=_provenance_subset(provenance, provenance_species),
            surface_geometry_provenance=geometry_notice,
            geometry_status_bearing=True,
            message=(
                "Wall-deposit surface geometry uses provisional or "
                "engineering-default stage areas; coating and fouling readouts "
                "are status-bearing until condenser surface areas are certified."
            ),
            **carrier_authority_kwargs,
        )

    return _wall_deposit_authority_payload(
        authoritative=True,
        code=WALL_STICKING_ALPHA_NOTICE_CODE,
        deposited_species=deposited_species,
        uncertified_species=(),
        provenance=_provenance_subset(provenance, provenance_species),
        surface_geometry_provenance=geometry_notice,
        geometry_status_bearing=False,
        **carrier_authority_kwargs,
    )


def coating_summary_with_grounded_authority(
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a coating summary with authority rederived from its projection."""

    result = dict(summary)
    wall_deposit = coating_wall_deposit_payload(result)
    alias_conflicts = coating_wall_deposit_alias_conflicts(result)
    total_kg = _sum_wall_deposit_kg(wall_deposit)
    authority_input = result.get("wall_deposit_sticking_authority")
    has_wall_deposit_projection = any(
        key in result for key in _COATING_WALL_DEPOSIT_KEYS
    )
    if (
        total_kg is None
        and has_wall_deposit_projection
        and not alias_conflicts
    ):
        return result

    if isinstance(wall_deposit, Mapping):
        authority = wall_deposit_sticking_authority_status(
            wall_deposit,
            authority_input if isinstance(authority_input, Mapping) else {},
        )
    else:
        authority = wall_deposit_sticking_authority_status(
            {},
            authority_input if isinstance(authority_input, Mapping) else {},
        )

    if alias_conflicts:
        authority = _plain_mapping(authority)
        authority.update({
            "authoritative": False,
            "authoritative_for_deposit_mass": False,
            "authoritative_for_coating": False,
            "authoritative_for_resinter": False,
            "code": WALL_DEPOSIT_ALIAS_CONFLICT_CODE,
            "output_status": "status_bearing",
            "conflicting_wall_deposit_aliases": list(alias_conflicts),
            "message": (
                "Wall-deposit payload aliases contain conflicting positive "
                "values; coating and fouling readouts are non-authoritative "
                "until one canonical ledger projection is supplied."
            ),
        })
        codes = [WALL_DEPOSIT_ALIAS_CONFLICT_CODE]
        for code in authority.get("codes", ()):
            if code not in codes:
                codes.append(str(code))
        authority["codes"] = codes

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


def _coating_wall_deposit_selection(
    summary: Mapping[str, Any],
) -> tuple[Any, tuple[str, ...]]:
    present = [
        (key, summary[key])
        for key in _COATING_WALL_DEPOSIT_KEYS
        if key in summary
    ]
    if not present:
        return None, ()
    # ★ A MEASURED ZERO IS EVIDENCE; AN ABSENT PROJECTION IS NOT.
    # _sum_wall_deposit_kg already distinguishes them -- it returns None when
    # nothing was found and a float (possibly 0.0) when something was -- but
    # `(sum or 0.0) > _EPS` collapsed both into "not positive". The conflict
    # walk then ran over the positive aliases only, so an alias reporting a
    # MEASURED 0.0 kg against another reporting 0.25 kg raised no conflict at
    # all: the contradicting evidence was filtered out before the comparison,
    # and the flattering positive value was published as authoritative.
    #
    # The three-state rule (unknown / measured-zero / positive are distinct
    # authority states) is already this project's invariant on the web coating
    # readout. This is the same rule applied at the site that SELECTS the
    # projection, which is where the contradiction actually has to be caught.
    measured = [
        (key, value)
        for key, value in present
        if _sum_wall_deposit_kg(value) is not None
    ]
    positive = [
        (key, value)
        for key, value in measured
        if (_sum_wall_deposit_kg(value) or 0.0) > _EPS
    ]
    selected_key, selected_value = (positive or measured or present)[0]
    conflicts = tuple(
        key
        for key, value in measured
        if key != selected_key
        and not _wall_deposit_aliases_equivalent(value, selected_value)
    )
    if conflicts:
        conflicts = (selected_key, *conflicts)
    return selected_value, conflicts


def _wall_deposit_aliases_equivalent(left: Any, right: Any) -> bool:
    left_total = _sum_wall_deposit_kg(left)
    right_total = _sum_wall_deposit_kg(right)
    if left_total is None or right_total is None:
        return left_total is right_total
    tolerance = _EPS * max(1.0, abs(left_total), abs(right_total))
    if abs(left_total - right_total) > tolerance:
        return False
    left_species = _wall_deposit_kg_by_species(left)
    right_species = _wall_deposit_kg_by_species(right)
    if left_species is None or right_species is None:
        return True
    for species in set(left_species) | set(right_species):
        left_kg = left_species.get(species, 0.0)
        right_kg = right_species.get(species, 0.0)
        species_tolerance = _EPS * max(1.0, abs(left_kg), abs(right_kg))
        if abs(left_kg - right_kg) > species_tolerance:
            return False
    return True


def _wall_deposit_kg_by_species(value: Any) -> dict[str, float] | None:
    if not isinstance(value, Mapping):
        return None
    totals: dict[str, float] = {}
    found = False
    for key, nested in value.items():
        if isinstance(key, tuple) and len(key) == 2:
            kg = _finite_float(nested)
            if kg is not None:
                species = str(key[1])
                totals[species] = totals.get(species, 0.0) + kg
                found = True
            continue
        if isinstance(nested, Mapping):
            for species, raw_kg in nested.items():
                kg = _finite_float(raw_kg)
                if kg is None:
                    continue
                species_key = str(species)
                totals[species_key] = totals.get(species_key, 0.0) + kg
                found = True
            continue
        kg = _finite_float(nested)
        if kg is not None:
            totals[str(key)] = totals.get(str(key), 0.0) + kg
            found = True
    return totals if found else None


def coating_wall_deposit_payload(summary: Mapping[str, Any]) -> Any:
    """Select the canonical non-empty wall projection across legacy aliases."""

    return _coating_wall_deposit_selection(summary)[0]


def coating_wall_deposit_alias_conflicts(
    summary: Mapping[str, Any],
) -> tuple[str, ...]:
    """Return positive wall aliases that disagree with the selected payload."""

    return _coating_wall_deposit_selection(summary)[1]


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
    vapour_carrier_authority_by_species: Mapping[str, Any] | None = None,
    vapour_carrier_lineage_by_deposited_species: Mapping[str, Any] | None = None,
    non_authoritative_carrier_species: Sequence[str] = (),
    refused_carrier_species: Sequence[str] = (),
    proven_zero_carrier_species: Sequence[str] = (),
    missing_carrier_authority_species: Sequence[str] = (),
    out_of_domain_alpha_species: Sequence[str] = (),
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
        "out_of_domain_alpha_species": list(out_of_domain_alpha_species),
        "out_of_domain_alpha_count": len(out_of_domain_alpha_species),
        "alpha_s_provenance_by_species": _plain_mapping(provenance),
        "vapour_carrier_authority_by_species": _plain_mapping(
            vapour_carrier_authority_by_species or {}
        ),
        "vapour_carrier_lineage_by_deposited_species": _plain_mapping(
            vapour_carrier_lineage_by_deposited_species or {}
        ),
        "non_authoritative_carrier_species": list(
            non_authoritative_carrier_species
        ),
        "refused_carrier_species": list(refused_carrier_species),
        "proven_zero_carrier_species": list(proven_zero_carrier_species),
        "missing_carrier_authority_species": list(
            missing_carrier_authority_species
        ),
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


def _positive_wall_deposit_segment_species(
    wall_deposit_kg: Mapping[Any, Any],
) -> tuple[tuple[str, str], ...]:
    pairs: set[tuple[str, str]] = set()
    for key, value in wall_deposit_kg.items():
        if isinstance(key, tuple) and len(key) == 2:
            if _positive_number(value):
                pairs.add((str(key[0]), str(key[1])))
            continue
        if isinstance(value, Mapping):
            segment = str(key)
            for nested_species, kg in value.items():
                if _positive_number(kg):
                    pairs.add((segment, str(nested_species)))
    return tuple(sorted(pairs))


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


def _wall_saturation_pressure_refusals_by_species(
    alpha_notice: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    raw = alpha_notice.get("wall_saturation_pressure_refusals_by_species")
    if not isinstance(raw, Mapping):
        return {}
    refusals: dict[str, dict[str, Any]] = {}
    for species, by_segment in raw.items():
        if not isinstance(by_segment, Mapping):
            continue
        segment_refusals = {
            str(segment): _plain_mapping(record)
            for segment, record in by_segment.items()
            if (
                isinstance(record, Mapping)
                and str(record.get("status", "")).lower() == "refused"
                and str(record.get("output_status", "")).lower()
                == "status_bearing"
            )
        }
        if segment_refusals:
            refusals[str(species)] = segment_refusals
    return refusals


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


def _out_of_domain_alpha_species(
    provenance: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    result: set[str] = set()
    for species, by_segment in provenance.items():
        if any(
            isinstance(record, Mapping)
            and bool(record.get("alpha_s_extrapolated", False))
            for record in by_segment.values()
        ):
            result.add(str(species))
    return result


def _uncertified_alpha_species(
    provenance: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    result: set[str] = set()
    for species, by_segment in provenance.items():
        if any(
            isinstance(record, Mapping)
            and _uncertified_alpha_record(record)
            for record in by_segment.values()
        ):
            result.add(str(species))
    return result


def _vapour_carrier_authority_by_species(
    notice: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    raw = notice.get("vapour_carrier_authority_by_species")
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(species): record
        for species, record in raw.items()
        if isinstance(record, Mapping)
    }


def _vapour_carrier_lineage_by_deposited_species(
    notice: Mapping[str, Any],
) -> dict[str, str | tuple[str, ...]]:
    from simulator.vapour_rail.instrumentation import (
        vapour_carrier_lineage_species,
    )

    raw = notice.get("vapour_carrier_lineage_by_deposited_species")
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, str | tuple[str, ...]] = {}
    for product_species, carrier_species in raw.items():
        product_key = str(product_species)
        sources = vapour_carrier_lineage_species(carrier_species)
        if not product_key or not sources:
            continue
        result[product_key] = sources[0] if len(sources) == 1 else sources
    return result


def _alpha_species_has_provenance_record(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    return any(
        isinstance(record, Mapping)
        and _valid_sticking_probability(record.get("alpha_s"))
        for record in value.values()
    )


def _alpha_segment_species_has_provenance_record(
    provenance: Mapping[str, Mapping[str, Any]],
    *,
    segment: str,
    species: str,
) -> bool:
    by_segment = provenance.get(species)
    if not isinstance(by_segment, Mapping):
        return False
    record = by_segment.get(segment)
    return isinstance(record, Mapping) and _valid_sticking_probability(
        record.get("alpha_s")
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
    *,
    alpha_authority_status_by_species: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    diagnostic = {
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
    if alpha_authority_status_by_species:
        diagnostic["alpha_authority_status_by_species"] = dict(
            alpha_authority_status_by_species
        )
    return diagnostic


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
    raw_alpha_authority_status_by_species = dict(
        getattr(sim, "_alpha_authority_status_by_species_engaged", {}) or {}
    )
    alpha_authority_status_by_species = {
        str(species): str(status)
        for species, status in raw_alpha_authority_status_by_species.items()
        if status == ANALYTICAL_UPPER_BOUND_ALPHA_STATUS
    }
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
            alpha_authority_status_by_species=(
                alpha_authority_status_by_species
            ),
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
            alpha_authority_status_by_species=(
                alpha_authority_status_by_species
            ),
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
            alpha_authority_status_by_species=(
                alpha_authority_status_by_species
            ),
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
                series.get("melt_resistance_enabled", False)
            ),
            "melt_surface_renewal_base_kg_s_m2_pa": _first_finite(
                series.get("melt_surface_renewal_base_kg_s_m2_pa"),
                0.0,
            ),
            "melt_surface_renewal_source": str(
                series.get("melt_surface_renewal_source")
                or "disabled:missing-species-state-dependent-melt-transfer-inputs"
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
        alpha_authority_status = alpha_authority_status_by_species.get(name)
        by_species[name] = {
            "status": "ok",
            # HKL upper-bound (matrix policy i): R_m disabled / missing melt inputs.
            "authority_class": "upper-bound",
            "authority_reason": (
                "missing-species-state-dependent-melt-transfer-inputs"
            ),
            "P_eq_Pa": flux_kwargs["P_eq_pa"],
            "P_bulk_Pa": flux_kwargs["P_bulk_pa"],
            "alpha_intrinsic": flux_kwargs["alpha_i"],
            **(
                {
                    ALPHA_AUTHORITY_STATUS_FIELD: alpha_authority_status,
                }
                if alpha_authority_status
                == ANALYTICAL_UPPER_BOUND_ALPHA_STATUS
                else {}
            ),
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

    diagnostic = {
        "schema_version": "pressure-coating-pareto-v1",
        "status": "ok",
        "authority_class": "upper-bound",
        "authority_reason": (
            "missing-species-state-dependent-melt-transfer-inputs"
        ),
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
    if alpha_authority_status_by_species:
        diagnostic["alpha_authority_status_by_species"] = (
            alpha_authority_status_by_species
        )
    return diagnostic


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


def condensation_refusals_diagnostic(sim: Any) -> dict[str, Any]:
    """B2 consumer: condensation_refusals_by_species from the condensation model."""

    from simulator.vapour_rail.instrumentation import condensation_refusals_payload

    condensation_model = getattr(sim, "condensation_model", None)
    if condensation_model is None:
        condensation_model = getattr(sim, "_condensation_model", None)
    refusals = {}
    if condensation_model is not None:
        refusals = (
            getattr(condensation_model, "last_condensation_refusals_by_species", {})
            or {}
        )
    return condensation_refusals_payload(refusals)


def condensation_authority_diagnostic(sim: Any) -> dict[str, Any]:
    """Per-carrier rail authority after condensation routing."""

    condensation_model = getattr(sim, "condensation_model", None)
    if condensation_model is None:
        condensation_model = getattr(sim, "_condensation_model", None)
    raw = {}
    if condensation_model is not None:
        raw = dict(
            getattr(
                condensation_model,
                "last_condensation_authority_by_species",
                {},
            )
            or {}
        )
    by_species = {
        str(species): _plain_mapping(record)
        for species, record in sorted(raw.items())
        if isinstance(record, Mapping)
    }
    status_counts: dict[str, int] = {}
    max_closure_error = 0.0
    for record in by_species.values():
        status = str(record.get("status") or "missing")
        status_counts[status] = status_counts.get(status, 0) + 1
        closure_error = _finite_float(record.get("mass_closure_error_kg_hr"))
        if closure_error is not None:
            max_closure_error = max(max_closure_error, abs(closure_error))
    return {
        "schema": "condensation_authority.v1",
        "n_species": len(by_species),
        "status_counts": status_counts,
        "max_mass_closure_error_kg_hr": max_closure_error,
        "by_species": by_species,
    }


def vapour_rail_instrumentation_diagnostic(sim: Any) -> dict[str, Any]:
    """VR-11: channel answers, refusals, solve groups, ceiling, flux overlay."""

    from simulator.vapour_rail.instrumentation import (
        SETPOINTS_T_COND_AUDIT,
        serialize_melt_activity_shadow,
        serialize_vapour_batch,
        source_vapour_ceiling_table,
    )

    batch = getattr(sim, "_last_vapour_batch", None)
    report = getattr(sim, "_last_vapour_batch_report", None)
    if report is None and batch is not None:
        report = serialize_vapour_batch(batch)
    if isinstance(report, Mapping):
        report = _plain_mapping(report)
    overlay = _plain_mapping(
        getattr(sim, "_last_vapour_batch_flux_overlay", {}) or {}
    )
    resolve_error = _plain_mapping(
        getattr(sim, "_last_vapour_batch_resolve_error", {}) or {}
    )
    condensation = condensation_refusals_diagnostic(sim)
    condensation_authority = condensation_authority_diagnostic(sim)
    return {
        "schema": "vapour_rail_instrumentation.v1",
        "vapour_batch": report,
        # Named, bounded consumer for the opt-in t-568 recorder. Ordinary
        # batch resolution leaves its nested batch_shadow null unless the state
        # explicitly gates melt-activity shadow computation on.
        "melt_activity_shadow": serialize_melt_activity_shadow(batch),
        "flux_overlay": overlay,
        "resolve_error": resolve_error or None,
        "condensation_refusals": condensation,
        "condensation_authority": condensation_authority,
        "source_vapour_ceiling_table": source_vapour_ceiling_table(),
        "setpoints_t_cond_audit": dict(SETPOINTS_T_COND_AUDIT),
        # Never default absent proof to True.
        "shadow_equal": (
            overlay["shadow_equal"] if "shadow_equal" in overlay else None
        ),
        "shadow_outcome": overlay.get("shadow_outcome"),
        # b-149: typed silent-zero notes for the hour (also on HourSnapshot).
        "silent_zero": silent_zero_class_diagnostic(sim),
    }


def silent_zero_class_diagnostic(sim: Any) -> dict[str, Any]:
    """b-149 silent-zero class payload (diagnostic only; no behaviour change)."""

    from simulator.silent_zero import silent_zero_diagnostic

    return silent_zero_diagnostic(sim)


__all__ = [
    "coating_summary_with_grounded_authority",
    "condensation_refusals_diagnostic",
    "pressure_coating_pareto_diagnostic",
    "silent_zero_class_diagnostic",
    "vapour_rail_instrumentation_diagnostic",
    "wall_deposit_sticking_authority_status",
    "wall_deposit_remobilization_by_segment_species",
    "wall_sticking_alpha_provenance_notice",
]
