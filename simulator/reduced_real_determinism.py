"""PT-0 reduced-real determinism proof helpers.

Opt-in only. This module builds canonical request keys and a minimal
in-memory write-through/replay store for the determinism proof gate.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import logging
import math
import os
import sqlite3
import subprocess
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import datetime
from datetime import timezone
from decimal import Decimal
from decimal import InvalidOperation
from enum import Enum
from functools import lru_cache
from pathlib import Path
from types import UnionType
from typing import Any, ClassVar, Union, get_args, get_origin, get_type_hints

from simulator.backend_names import (
    ANALYTICAL_BACKEND_CLASS_DISPLAY_NAME,
    ANALYTICAL_BACKEND_QUALIFIED_CLASS_NAME,
    canonical_backend_class_name,
)
from simulator.chemistry.kernel import ChemistryIntent
from simulator.config import (
    DEFAULT_ALPHAMELTS_MODEL,
    functional_data_yaml_digest,
    resolve_alphamelts_subprocess_model,
)
from simulator.grind_preflight import (
    APPROVED_LIVE_VAPOR_SOURCES,
    GrindSourceGateError,
    assert_strict_vapor_pt1_row,
)
from simulator.fe_redox import (
    RedoxState,
    _non_absolute_redox_state_kwargs,
    _kress91_gate_key_referenced_log_fO2,
)
from simulator.fidelity_vocabulary import EvidenceClass
from simulator.scalar_boundary import is_declared_real_scalar
from simulator.melt_backend.base import (
    EquilibriumResult,
    RealBackendAuthority,
    RealBackendFamily,
    project_melt_to_oxide_projection,
)
from simulator.melt_backend.melt_envelope import (
    MELT_EXTRAPOLATION_ENVELOPE_FIELDS,
    consume_melt_extrapolation_envelope,
    has_melt_extrapolation_envelope,
    melt_extrapolation_diagnostic,
)
from simulator.melt_backend.sulfsat import SulfurSaturationResult
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET


_LOGGER = logging.getLogger(__name__)


SCHEMA_VERSION = "pt0-reduced-real-determinism-v1"
PHYSICS_BUCKET_SCHEMA_VERSION = "pt1-reduced-real-physics-bucket-v2"
PT1_LEGACY_STORE_SCHEMA_VERSION = "pt1-reduced-real-equilibrium-store-v2"
PT1_STORE_SCHEMA_VERSION = "pt1-reduced-real-equilibrium-store-v3"
PT1_SUPPORTED_STORE_SCHEMA_VERSIONS = (
    PT1_LEGACY_STORE_SCHEMA_VERSION,
    PT1_STORE_SCHEMA_VERSION,
)
_PT1_ROW_KEY_MAX_NESTING = 1000
PT1_EQUILIBRIUM_TABLE = "reduced_real_equilibrium_payloads"
PT1_METADATA_TABLE = "reduced_real_metadata"
PT1_READ_ONLY_BASE_ALIAS = "pt1_read_only_base"
_PT1_SOURCE_REQUIRED_TEXT_FIELDS = (
    "key_hash",
    "artifact",
    "store_schema_version",
    "request_schema_version",
    "key_sha256",
    "payload_sha256",
    "code_version",
    "data_digests_json",
    "created_at",
)
REPAIR_NOTICE_COLLECTOR_ATTRIBUTE = "_reduced_real_repair_notices"
REPAIR_NOTICES_UNAVAILABLE_NOTICE = {
    "kind": "repair_notices_unavailable_for_legacy_row"
}
DEFAULT_SHARD_BUSY_TIMEOUT_MS = 60_000.0
PHYSICS_BUCKET_LADDER_RUNGS = (
    ("h40", 4.0),
    ("h30", 3.0),
)
PHYSICS_BUCKET_CONTROL_LADDER_RUNGS = (
    ("h40c", 4.0),
    ("h30c", 3.0),
)
PHYSICS_BUCKET_ALL_LADDER_RUNGS = (
    PHYSICS_BUCKET_LADDER_RUNGS + PHYSICS_BUCKET_CONTROL_LADDER_RUNGS
)
CONTROL_RUNG_SIO_ERROR_BUDGET_TERM = (
    "po2_control_coarsening_sio_vapor_relative_error"
)
CONTROL_RUNG_SIO_RELATIVE_ERROR_BUDGET = 1.0e-3
CONTROL_RUNG_SIO_PO2_KNEE_BAR = 1.0e-9
CACHE_STATES = (
    "cached_exact",
    "cached_physics_bucket",
    "cached_interpolated",
    "live_fill",
)
APPROXIMATE_REDUCED_REAL_CACHE_STATES = frozenset(
    {"cached_interpolated", "cached_physics_bucket"}
)
_CLEANED_MELT_ACCOUNT = "process.cleaned_melt"
# One production record for an abandoned freeze-gate dispatch. Digests drop
# this key whole. It is not curve identity: memo records, cache compares,
# and store payloads omit the environment keys. The live curve of the hour
# that retried keeps them, and replay does not restore them.
ENVIRONMENT_RETRIES_KEY = 'environment_retries'
GATE_CURVE_ENVIRONMENT_KEYS = (
    ENVIRONMENT_RETRIES_KEY,
)
GATE_CURVE_NOTICE_KEYS = (
    'composition_projected_notice', 'gate_provider_refusal',
    'gate_provider_step_down', 'alphamelts_reference_pressure',
    *GATE_CURVE_ENVIRONMENT_KEYS,
)
GATE_CURVE_IDENTITY_NOTICE_KEYS = tuple(
    key for key in GATE_CURVE_NOTICE_KEYS
    if key not in GATE_CURVE_ENVIRONMENT_KEYS
)
GATE_CURVE_DOMAIN_KEYS = ('authority', 'certified_band')
PT0_GATE_CURVE_CODEC_VERSION = 3
PT1_EQUILIBRIUM_PAYLOAD_CODEC_VERSION = 2
_EQUILIBRIUM_RESULT_FIELD_TYPES = get_type_hints(EquilibriumResult)
_EQUILIBRIUM_RESULT_FIELDS_OMITTED_WHEN_NONE = frozenset(
    {"requested_fO2_log", "printed_fO2_echo_log"}
)
_EQUILIBRIUM_REPLAY_SIDE_FIELDS = (
    "last_vapor_pressures_source",
    "last_vapor_pressure_diagnostic",
)
_EQUILIBRIUM_AUTHORITY_FIELDS = (
    "schema",
    "evidence_class",
    "backend_family",
    "backend",
    "provider",
    "vapor_pressure",
)
_BACKEND_AUTHORITY_FIELDS = ("backend_name", "backend_class")
_PROVIDER_AUTHORITY_FIELDS = (
    "resolved_provider_id",
    "resolved_role",
    "authoritative_provider_id",
    "fallback_provider_id",
    "fallback_allowed",
    "model",
    "mode",
)


def _equilibrium_result_payload_fields() -> tuple[dataclasses.Field[Any], ...]:
    return tuple(
        field
        for field in dataclasses.fields(EquilibriumResult)
        if field.name != "ledger_transition"
    )


def _provider_authority_payload(
    *,
    resolved_provider_id: str | None,
    resolved_role: str,
    authoritative_provider_id: str | None,
    fallback_provider_id: str | None,
    fallback_allowed: bool,
    model: str | None,
    mode: str | None,
) -> dict[str, Any]:
    return dict(
        zip(
            _PROVIDER_AUTHORITY_FIELDS,
            (
                resolved_provider_id,
                resolved_role,
                authoritative_provider_id,
                fallback_provider_id,
                fallback_allowed,
                model,
                mode,
            ),
        )
    )


@dataclasses.dataclass(frozen=True)
class _DecodedEquilibriumReplayPayload:
    result: EquilibriumResult
    vapor_pressures_source: dict[str, Any]
    vapor_pressure_diagnostic: dict[str, Any]


@dataclasses.dataclass(frozen=True)
class _AdmittedStoredRow:
    entry: dict[str, Any]
    decoded_payload: Any
    stored_columns: dict[str, Any] | None = None
    physics_ladder_values: dict[str, dict[str, float | str]] | None = None
    stale_payload_codec: bool = False


_MAGEMIN_SHADOW_PROVIDER_ID = "magemin-shadow"
_T_K_QUANTUM = 0.01
_FO2_LOG_QUANTUM = 0.001
_PRESSURE_BAR_QUANTUM = 0.00001
_COMPOSITION_SIG_FIGS = 5
_TRACE_CUTOFF = 1.0e-12
_GATE_CURVE_KEY_T_K_SENTINEL = 298.15
_CACHEABLE_EQUILIBRIUM_STATUSES = frozenset({"ok"})
_CACHEABLE_GATE_STATUSES = frozenset({"ok"})
_CACHEABLE_GATE_CALIBRATION_STATUSES = frozenset({"in_range"})
_SOURCE_MODULE_SET_ID = "equilibrium-vapor-melt-backend-v3"
# Modules that can change the equilibrium_post_record payload: core branch
# selection/post hooks, cache serialization, kernel dispatch contracts,
# melt-backend adapters, evaporation curve shaping, AlphaMELTS diagnostics,
# and builtin/VapoRock vapor, flux, or backend-equilibrium providers. Excludes
# unrelated campaign/UI code so cross-commit cache reuse survives non-payload
# edits.
_SOURCE_MODULE_PATTERNS = (
    "simulator/core.py",
    "simulator/evaporation.py",
    "simulator/recipe.py",
    "simulator/reduced_real_determinism.py",
    "simulator/chemistry/kernel/*.py",
    "simulator/melt_backend/*.py",
    "engines/alphamelts/*.py",
    "engines/builtin/_common.py",
    "engines/builtin/backend_equilibrium.py",
    "engines/builtin/evaporation_flux.py",
    "engines/builtin/vapor_pressure.py",
    "engines/builtin/stage0_pretreatment.py",
    "engines/builtin/foulant_disposition.py",
    # data/foulant_thermo.yaml and data/stage0_carbon_partition.yaml stay
    # cache-inert while disposition remains diagnostic-only and absent from
    # cached result payloads; add explicit optional digests if bakeoff reporting
    # is ever cached.
    "engines/vaporock/*.py",
)


def _payload_codec_version(artifact: str) -> int | None:
    if artifact == "freeze_gate_curve":
        return PT0_GATE_CURVE_CODEC_VERSION
    if artifact == "equilibrium_post_record":
        return PT1_EQUILIBRIUM_PAYLOAD_CODEC_VERSION
    return None


def _payload_codec_stamp_matches(
    expected_version: int | None,
    stored_version: Any,
) -> bool:
    return expected_version is None or (
        type(stored_version) is int and stored_version == expected_version
    )


def _is_finite_cache_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def _gate_curve_identity_rejection(
    curve: Mapping[str, Any],
    keys: Sequence[Mapping[str, Any]],
    *,
    reader_context_policy: bool,
    write_integrity_only: bool = False,
) -> str | None:
    if not keys:
        return None
    identity_keys = keys if reader_context_policy else keys[:1]
    magemin_identities = []
    for key in identity_keys:
        if not isinstance(key, Mapping):
            return "gate.key.type"
        identity_key = key.get("freeze_gate_cache_key")
        identity_is_required = (
            getattr(identity_key, "magemin_domain_identity", None) is not None
            or (
                isinstance(identity_key, Mapping)
                and identity_key.get("magemin_domain_identity") is not None
            )
            or (
                isinstance(identity_key, (list, tuple))
                and len(identity_key) == 9
                and identity_key[7] is not None
            )
        )
        try:
            identity = _magemin_identity_from_replay_key(key)
        except (OverflowError, TypeError, ValueError):
            identity = None
        if identity is None and identity_is_required:
            return "gate.key.identity"
        if identity is None:
            continue
        if identity.silica_verdict not in {"in_band", "extrapolated"}:
            return "gate.key.silica_verdict"
        magemin_identities.append(identity)
    extrapolated_identities = [
        identity
        for identity in magemin_identities
        if identity.silica_verdict == "extrapolated"
    ]
    if extrapolated_identities:
        stored_or_query_identity = extrapolated_identities[0]
        if (
            reader_context_policy
            and len(identity_keys) > 1
            and len(magemin_identities) != len(identity_keys)
        ) or any(
            not stored_or_query_identity.matches(identity)
            for identity in magemin_identities
        ):
            return "gate.key.identity_mismatch"
        certified_band = curve.get("certified_band")
        silica_bounds = (
            certified_band.get("sio2_wt_pct")
            if isinstance(certified_band, Mapping)
            else None
        )
        band_shape_valid = (
            isinstance(silica_bounds, (list, tuple))
            and not isinstance(silica_bounds, (str, bytes))
            and len(silica_bounds) == 2
            and all(_is_finite_cache_number(bound) for bound in silica_bounds)
            and silica_bounds[0] < silica_bounds[1]
            and curve.get("authority") == "extrapolated"
        )
        if stored_or_query_identity.certified_band is None:
            return "gate.key.certified_band_mismatch"
        if not band_shape_valid:
            if write_integrity_only:
                return None
            return "gate.key.certified_band_mismatch"
        if (
            tuple(float(bound) for bound in silica_bounds)
            != stored_or_query_identity.certified_band
        ):
            return "gate.key.certified_band_mismatch"
    return None


def _admission_rejection(
    artifact: str,
    payload: Any,
    *,
    payload_codec_version: int | None = None,
    keys: Sequence[Mapping[str, Any]] = (),
    interpolation_synthesized: bool = False,
    reader_context_policy: bool = True,
) -> str | None:
    expected_version = _payload_codec_version(artifact)
    if expected_version is None:
        return None
    if not _payload_codec_stamp_matches(expected_version, payload_codec_version):
        return "payload.codec_version"
    if not isinstance(payload, Mapping):
        return "payload.type"

    def has_finite_json_numbers(value: Any) -> bool:
        if isinstance(value, Mapping):
            return all(
                isinstance(key, str) and has_finite_json_numbers(item)
                for key, item in value.items()
            )
        if isinstance(value, (list, tuple)):
            return all(has_finite_json_numbers(item) for item in value)
        if isinstance(value, bool) or value is None or isinstance(value, str):
            return True
        if isinstance(value, (int, float)):
            return _is_finite_cache_number(value)
        return False

    def valid_string_sequence(value: Any) -> bool:
        return (
            isinstance(value, (list, tuple))
            and not isinstance(value, (str, bytes))
            and all(isinstance(item, str) for item in value)
        )

    def valid_composition_projected_notice(value: Any) -> bool:
        if not isinstance(value, Mapping):
            return False
        required_fields = {
            "kind",
            "reason",
            "authority",
            "certified_band",
            "dropped_components",
        }
        if not required_fields.issubset(value) or any(
            not isinstance(value[field_name], str)
            for field_name in ("kind", "reason", "authority")
        ):
            return False

        def valid_projection_band(band: Any) -> bool:
            return (
                isinstance(band, Mapping)
                and all(
                    field_name in band
                    for field_name in ("engine", "database", "bulk_components")
                )
                and isinstance(band["engine"], str)
                and isinstance(band["database"], str)
                and valid_string_sequence(band["bulk_components"])
            )

        certified_band = value["certified_band"]
        if not isinstance(certified_band, Mapping):
            return False
        if "projection_certified_band" not in value:
            if not valid_projection_band(certified_band):
                return False
        else:
            projection_band = value["projection_certified_band"]
            if not valid_projection_band(projection_band):
                return False
            if any(
                field_name not in certified_band
                for field_name in ("min_T_C", "max_T_C", "source")
            ):
                return False
            if (
                not all(
                    _is_finite_cache_number(certified_band[field_name])
                    for field_name in ("min_T_C", "max_T_C")
                )
                or not isinstance(certified_band["source"], str)
                or not certified_band["source"]
            ):
                return False
            if "engine" in certified_band and not valid_projection_band(
                certified_band
            ):
                return False
        dropped_components = value["dropped_components"]
        if (
            not isinstance(dropped_components, (list, tuple))
            or isinstance(dropped_components, (str, bytes))
            or not dropped_components
        ):
            return False
        for row in dropped_components:
            if (
                not isinstance(row, Mapping)
                or "component" not in row
                or "mass_fraction" not in row
                or not isinstance(row["component"], str)
                or not row["component"]
                or not _is_finite_cache_number(row["mass_fraction"])
            ):
                return False
        if "bounds_source" in value and not isinstance(
            value["bounds_source"], str
        ):
            return False
        if "projected_bounds" in value:
            projected_bounds = value["projected_bounds"]
            if projected_bounds == "invalid" and "bounds_source" in value:
                pass
            elif (
                not isinstance(projected_bounds, (list, tuple))
                or isinstance(projected_bounds, (str, bytes))
                or len(projected_bounds) != 2
                or not all(
                    _is_finite_cache_number(bound)
                    for bound in projected_bounds
                )
            ):
                return False
        if "floor_T_C" in value and not _is_finite_cache_number(
            value["floor_T_C"]
        ):
            return False
        for field_name in (
            "temperature_band_case",
            "temperature_band_status",
            "temperature_band_source",
        ):
            if field_name in value and value[field_name] is not None and not isinstance(
                value[field_name], str
            ):
                return False
        return True

    def has_provider_authority_shape(value: Any) -> bool:
        return (
            isinstance(value, Mapping)
            and all(
                field_name in value
                for field_name in _PROVIDER_AUTHORITY_FIELDS
            )
            and isinstance(value["resolved_provider_id"], str)
            and isinstance(value["resolved_role"], str)
            and all(
                value[field_name] is None or isinstance(value[field_name], str)
                for field_name in (
                    "authoritative_provider_id",
                    "fallback_provider_id",
                    "model",
                    "mode",
                )
            )
            and type(value["fallback_allowed"]) is bool
        )

    if not has_finite_json_numbers(payload):
        return "payload.json_number"
    if artifact == "freeze_gate_curve":
        if reader_context_policy and not keys:
            return "gate.key_missing"
        curve_payload = payload.get("curve")
        if not isinstance(curve_payload, Mapping):
            return "gate.curve.type"
        try:
            curve = _curve_from_payload(curve_payload)
        except Exception as exc:
            return f"gate.curve.decode:{type(exc).__name__}"
        projected_notice = curve.get("composition_projected_notice")
        if (
            "composition_projected_notice" in curve
            and not valid_composition_projected_notice(projected_notice)
        ):
            return "gate.composition_projected_notice"
        identity_rejection = _gate_curve_identity_rejection(
            curve,
            keys,
            reader_context_policy=reader_context_policy,
        )
        if identity_rejection is not None:
            return identity_rejection
        return None
    if artifact == "equilibrium_post_record":
        equilibrium_result = payload.get("equilibrium_result")
        required_result_fields = {
            field.name
            for field in _equilibrium_result_payload_fields()
            if field.name not in _EQUILIBRIUM_RESULT_FIELDS_OMITTED_WHEN_NONE
        }
        if not isinstance(equilibrium_result, Mapping):
            return "equilibrium.result.type"
        missing_result_fields = required_result_fields.difference(
            equilibrium_result
        )
        if missing_result_fields:
            return f"equilibrium.required_field:{sorted(missing_result_fields)[0]}"
        for field_name in _EQUILIBRIUM_REPLAY_SIDE_FIELDS:
            if field_name not in payload:
                return f"equilibrium.required_field:{field_name}"
        authority = payload.get("authority")
        if not interpolation_synthesized:
            if (
                not isinstance(authority, Mapping)
                or any(
                    field_name not in authority
                    for field_name in _EQUILIBRIUM_AUTHORITY_FIELDS
                )
                or any(
                    not isinstance(authority.get(field_name), str)
                    for field_name in (
                        "schema",
                        "evidence_class",
                        "backend_family",
                    )
                )
            ):
                return "equilibrium.authority.shape"
            backend_identity = authority.get("backend")
            provider_identity = authority.get("provider")
            vapor_pressure_identity = authority.get("vapor_pressure")
            if (
                not isinstance(backend_identity, Mapping)
                or any(
                    field_name not in backend_identity
                    or not isinstance(backend_identity[field_name], str)
                    for field_name in _BACKEND_AUTHORITY_FIELDS
                )
                or not has_provider_authority_shape(provider_identity)
                or not has_provider_authority_shape(vapor_pressure_identity)
            ):
                return "equilibrium.authority.provider_shape"
        elif "authority" in payload and not isinstance(authority, Mapping):
            return "equilibrium.authority.type"
        if not interpolation_synthesized and keys:
            try:
                validate_reduced_real_equilibrium_record_key(
                    artifact,
                    keys[0],
                    payload,
                )
            except RuntimeError:
                # The writer-facing validator raises for invalid authority,
                # but a bad stored row is an admission miss.
                return "equilibrium.authority.identity"
        try:
            decoded = _decode_equilibrium_replay_payload(payload)
        except Exception as exc:
            return f"equilibrium.decode:{type(exc).__name__}"
        diagnostics = equilibrium_result.get("diagnostics")
        feo_activity = (
            diagnostics.get("a_FeO_calphad")
            if isinstance(diagnostics, Mapping)
            else None
        )
        if isinstance(feo_activity, Mapping) and "notices" in feo_activity:
            return "equilibrium.diagnostics.a_FeO_calphad.notices"
        provenance_diagnostics = [decoded.vapor_pressure_diagnostic]
        if isinstance(decoded.result.diagnostics, Mapping):
            provenance_diagnostics.append(decoded.result.diagnostics)
        string_fields = {
            "pressure_rail",
            "melt_oxide_component",
            "melt_activity_parent_oxide",
            "melt_activity_flag",
        }
        nullable_string_fields = {"oxygen_fugacity_channel"}
        finite_number_fields = {
            "activity_factor",
            "raw_metal_activity_root",
            "P_reference_Antoine_Pa",
            "pO2_bar",
            "pO2_exponent",
            "melt_oxide_X_single_cation",
        }

        def valid_activity_standard_state(value: Any) -> bool:
            if not isinstance(value, Mapping) or any(
                field_name not in value
                for field_name in (
                    "reference_pressure_bar",
                    "reference_temperature_K",
                )
            ):
                return False
            pressure = value["reference_pressure_bar"]
            temperature = value["reference_temperature_K"]
            return _is_finite_cache_number(pressure) and (
                temperature is None
                or _is_finite_cache_number(temperature)
            )

        for diagnostic in provenance_diagnostics:
            high_t_activity = diagnostic.get("high_t_melt_activity")
            if "high_t_melt_activity" in diagnostic and (
                not isinstance(high_t_activity, Mapping)
                or not _is_finite_cache_number(
                    high_t_activity.get("temperature_K")
                )
                or not _is_finite_cache_number(
                    high_t_activity.get("vaporock_cap_K")
                )
            ):
                return "diagnostic.high_t_melt_activity.shape"
            # VapoRock writes this field as species-to-pressure data; the
            # SiO report publishes its values directly as pressures.
            vaporock_speciation = diagnostic.get(
                "vaporock_full_speciation_Pa"
            )
            if "vaporock_full_speciation_Pa" in diagnostic and (
                not isinstance(vaporock_speciation, Mapping)
                or any(
                    not _is_finite_cache_number(pressure)
                    for pressure in vaporock_speciation.values()
                )
            ):
                return "diagnostic.vaporock_full_speciation_Pa"
            backend_pressures = diagnostic.get("backend_vapor_pressures_Pa")
            if "backend_vapor_pressures_Pa" in diagnostic and (
                not isinstance(backend_pressures, Mapping)
                or any(
                    not _is_finite_cache_number(pressure)
                    for pressure in backend_pressures.values()
                )
            ):
                return "diagnostic.backend_vapor_pressures_Pa"
            # Evaporation's shadow comparison refuses non-finite entries.
            activities = diagnostic.get("activities")
            if "activities" in diagnostic and (
                not isinstance(activities, Mapping)
                or any(
                    not isinstance(species, str)
                    or not _is_finite_cache_number(activity)
                    for species, activity in activities.items()
                )
            ):
                return "diagnostic.activities"
            activity_standard_state_required = (
                isinstance(activities, Mapping) and bool(activities)
            )
            if activity_standard_state_required and (
                "activities_standard_state" not in diagnostic
            ):
                return "diagnostic.activities_standard_state.missing"
            if (
                "activities_standard_state" in diagnostic
                and not valid_activity_standard_state(
                    diagnostic["activities_standard_state"]
                )
            ):
                return "diagnostic.activities_standard_state"
            if "source_reaction_fO2_bar" in diagnostic:
                source_reaction_fO2_bar = diagnostic[
                    "source_reaction_fO2_bar"
                ]
                if (
                    source_reaction_fO2_bar is not None
                    and not _is_finite_cache_number(source_reaction_fO2_bar)
                ):
                    return "diagnostic.source_reaction_fO2_bar"
            if "vapor_pressure_numerator_provenance" not in diagnostic:
                continue
            provenance = diagnostic["vapor_pressure_numerator_provenance"]
            if not isinstance(provenance, Mapping):
                return "diagnostic.vapor_pressure_numerator_provenance.type"
            for species, species_provenance in provenance.items():
                if (
                    not isinstance(species, str)
                    or not isinstance(species_provenance, Mapping)
                    or any(not isinstance(field, str) for field in species_provenance)
                ):
                    return "diagnostic.vapor_pressure_numerator_provenance.species"
                if (
                    "activities_standard_state" in species_provenance
                    and not valid_activity_standard_state(
                        species_provenance["activities_standard_state"]
                    )
                ):
                    return "diagnostic.provenance.activities_standard_state"
                for field_name in string_fields:
                    if field_name in species_provenance and not isinstance(
                        species_provenance[field_name], str
                    ):
                        return f"diagnostic.provenance.{field_name}.type"
                for field_name in nullable_string_fields:
                    value = species_provenance.get(field_name)
                    if (
                        field_name in species_provenance
                        and value is not None
                        and not isinstance(value, str)
                    ):
                        return f"diagnostic.provenance.{field_name}.type"
                for field_name in finite_number_fields:
                    value = species_provenance.get(field_name)
                    if field_name in species_provenance and value is not None:
                        if not _is_finite_cache_number(value):
                            return f"diagnostic.provenance.{field_name}.number"
                if "melt_oxide_gamma_valid_range_K" in species_provenance:
                    valid_range = species_provenance[
                        "melt_oxide_gamma_valid_range_K"
                    ]
                    if (
                        not isinstance(valid_range, (list, tuple))
                        or isinstance(valid_range, (str, bytes))
                        or len(valid_range) != 2
                    ):
                        return "diagnostic.provenance.melt_oxide_gamma_valid_range_K.shape"
                    if not all(
                        _is_finite_cache_number(bound)
                        for bound in valid_range
                    ):
                        return "diagnostic.provenance.melt_oxide_gamma_valid_range_K.number"
                if (
                    "melt_activity_fallback" in species_provenance
                    and type(species_provenance["melt_activity_fallback"]) is not bool
                ):
                    return "diagnostic.provenance.melt_activity_fallback.type"
                refusal = species_provenance.get("openimcc_refusal")
                if refusal is not None:
                    if not isinstance(refusal, Mapping) or any(
                        field_name in refusal
                        and value is not None
                        and not isinstance(value, str)
                        for field_name, value in (
                            ("code", refusal.get("code")),
                            ("detail", refusal.get("detail")),
                            ("reason", refusal.get("reason")),
                        )
                    ):
                        return "diagnostic.provenance.openimcc_refusal"
        return None
    return "payload.artifact"


def _decode_payload_for_admission(
    artifact: str,
    payload: Any,
    *,
    payload_codec_version: int | None = None,
    keys: Sequence[Mapping[str, Any]] = (),
    interpolation_synthesized: bool = False,
    reader_context_policy: bool = True,
) -> Any | None:
    if _admission_rejection(
        artifact,
        payload,
        payload_codec_version=payload_codec_version,
        keys=keys,
        interpolation_synthesized=interpolation_synthesized,
        reader_context_policy=reader_context_policy,
    ) is not None:
        return None
    expected_version = _payload_codec_version(artifact)
    if expected_version is None:
        return payload
    if artifact == "freeze_gate_curve":
        return _curve_from_payload(payload["curve"])
    if artifact == "equilibrium_post_record":
        return _decode_equilibrium_replay_payload(payload)
    return None


_ALPHAMELTS_AUTHORIZED_NAME = "alphamelts"
_ALPHAMELTS_BACKEND_NAME = "AlphaMELTSBackend"
_ALPHAMELTS_BACKEND_CLASS = (
    "simulator.melt_backend.alphamelts.AlphaMELTSBackend"
)
_ALPHAMELTS_PROVIDER_ID = "alphamelts-diagnostic"
_ALPHAMELTS_DEFAULT_MODE = "subprocess"
_THERMOENGINE_AUTHORIZED_NAME = 'thermoengine'
_THERMOENGINE_BACKEND_NAME = 'ThermoEngineBackend'
_THERMOENGINE_BACKEND_CLASS = (
    'simulator.melt_backend.thermoengine.ThermoEngineBackend'
)
_THERMOENGINE_DEFAULT_MODE = 'thermoengine'
_MAGEMIN_BACKEND_IDENTITIES = frozenset(
    {
        (
            "MAGEMinBackend",
            "simulator.melt_backend.magemin.MAGEMinBackend",
        ),
    }
)
_BUILTIN_BACKEND_EQUILIBRIUM_PROVIDER_ID = "builtin-backend-equilibrium"
_INTERNAL_ANALYTICAL_BACKEND_RUNTIME_NAME = "InternalAnalyticalBackend"
_INTERNAL_ANALYTICAL_BACKEND_SERIALIZED_NAME = ANALYTICAL_BACKEND_CLASS_DISPLAY_NAME
_INTERNAL_ANALYTICAL_BACKEND_SERIALIZED_CLASS = ANALYTICAL_BACKEND_QUALIFIED_CLASS_NAME


@dataclasses.dataclass(frozen=True)
class ControlQuantization:
    """Session-scoped reduced-real control/key quantization."""

    t_k_quantum: float
    pressure_bar_quantum: float
    log_fo2_quantum: float
    composition_sig_figs: int

    PRODUCTION: ClassVar["ControlQuantization"]

    def __post_init__(self) -> None:
        for name in (
            "t_k_quantum",
            "pressure_bar_quantum",
            "log_fo2_quantum",
        ):
            raw_value = getattr(self, name)
            if not is_declared_real_scalar(
                raw_value,
                allow_numeric_str=True,
            ):
                raise ValueError(f"{name} must be a positive finite quantum")
            value = float(raw_value)
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be a positive finite quantum")
            object.__setattr__(self, name, value)
        sig_figs = self.composition_sig_figs
        if (
            not is_declared_real_scalar(sig_figs)
            or not isinstance(sig_figs, int)
            or sig_figs < 1
        ):
            raise ValueError("composition_sig_figs must be an int >= 1")

    @classmethod
    def from_name(cls, name: str) -> "ControlQuantization":
        normalized = _normalize_control_quantization_name(name)
        try:
            return _CONTROL_QUANTIZATION_PRESETS[normalized]
        except KeyError as exc:
            choices = ", ".join(sorted(_CONTROL_QUANTIZATION_PRESETS))
            raise ValueError(
                f"unknown control quantization tier {name!r}; "
                f"expected one of {choices}"
            ) from exc

    @property
    def t_k_decimals(self) -> int:
        return _quantize_decimals(self.t_k_quantum)

    @property
    def pressure_bar_decimals(self) -> int:
        return _quantize_decimals(self.pressure_bar_quantum)

    @property
    def log_fo2_decimals(self) -> int:
        return _quantize_decimals(self.log_fo2_quantum)


def _normalize_control_quantization_name(name: str) -> str:
    normalized = str(name).strip().lower().replace("-", "_")
    if not normalized:
        raise ValueError("control quantization tier name must be non-empty")
    return normalized


_PRODUCTION_QUANTIZATION = ControlQuantization(
    t_k_quantum=_T_K_QUANTUM,
    pressure_bar_quantum=_PRESSURE_BAR_QUANTUM,
    log_fo2_quantum=_FO2_LOG_QUANTUM,
    composition_sig_figs=_COMPOSITION_SIG_FIGS,
)
_CONTROL_QUANTIZATION_PRESETS: dict[str, ControlQuantization] = {
    "xx_coarse": ControlQuantization(
        t_k_quantum=10.0,
        pressure_bar_quantum=0.01,
        log_fo2_quantum=0.1,
        composition_sig_figs=2,
    ),
    "coarse": ControlQuantization(
        t_k_quantum=1.0,
        pressure_bar_quantum=0.001,
        log_fo2_quantum=0.01,
        composition_sig_figs=3,
    ),
    "medium": ControlQuantization(
        t_k_quantum=0.1,
        pressure_bar_quantum=0.0001,
        log_fo2_quantum=0.005,
        composition_sig_figs=4,
    ),
    "fine": _PRODUCTION_QUANTIZATION,
}
ControlQuantization.PRODUCTION = _PRODUCTION_QUANTIZATION


class PT0CacheMiss(RuntimeError):
    """Replay requested a key that the write-through run did not capture."""


class PT0CacheCollision(RuntimeError):
    """One canonical key produced different payload bytes."""


class PT1PersistentStoreCorrupt(PT0CacheCollision):
    """Persistent PT-1 row failed verify-on-hit integrity checks."""


class _PT1CacheMergeCollision(RuntimeError):
    """Two current PT-1 rows share a key but disagree on row identity."""


_PT1_CACHE_MERGE_IDENTITY_FIELDS = (
    "artifact",
    "store_schema_version",
    "request_schema_version",
    "key_sha256",
    "payload_sha256",
    "key_bytes",
    "payload_bytes",
    "code_version",
    "corpus_version",
    "engine_version",
    "data_digests_json",
    "git_dirty",
)


class PT0InvalidControls(RuntimeError):
    """PT-0 control quantization received a non-finite process value."""


class PT0NonFinitePayload(ValueError):
    """PT-0 payload canonicalization received a non-finite value."""


class UncacheableKey(ValueError):
    """A replay key cannot be indexed at every physics-ladder rung."""

    category = "invalid-input"

    def __init__(self, rung_tag: str, field: str) -> None:
        self.rung_tag = rung_tag
        self.field = field
        self.reason = (
            f"physics ladder rung {rung_tag} has no finite distance for {field}"
        )
        super().__init__(self.reason)


class UncacheablePayload(ValueError):
    """A fresh cache payload cannot be replayed by the exact reader."""

    category = "invalid-payload"

    def __init__(self, artifact: str, key_hash: str) -> None:
        self.reason = (
            f"{artifact} payload is not replayable for cache key {key_hash}"
        )
        super().__init__(self.reason)


def _authoritative_melt_fO2_log(sim: Any) -> float:
    reader = getattr(sim, "_current_melt_redox_fO2_log", None)
    if callable(reader):
        value = reader()
    else:
        reservoir = getattr(getattr(sim, "melt", None), "oxygen_reservoir", None)
        value = getattr(reservoir, "melt_intrinsic_fO2_log", None)
        if value is None:
            value = getattr(getattr(sim, "melt", None), "melt_fO2_log", None)
    try:
        fO2_log = float(value)
    except (TypeError, ValueError) as exc:
        raise PT0InvalidControls(
            "missing authoritative melt fO2_log for PT-0 cache key"
        ) from exc
    if not math.isfinite(fO2_log):
        raise PT0InvalidControls(
            "non-finite authoritative melt fO2_log for PT-0 cache key: "
            f"{value!r}"
        )
    return fO2_log


def _melt_fO2_log_at_gate_key_reference_T(sim: Any, fO2_log: float) -> float:
    reference_reader = getattr(sim, "_current_melt_redox_reference_T_K", None)
    reference_T_K = reference_reader() if callable(reference_reader) else None
    return _kress91_gate_key_referenced_log_fO2(
        fO2_log,
        reference_T_K=reference_T_K,
    )


# Sentinel for "read the active computation, not an explicit payload".
_FROM_COMPUTATION = object()


def _resolve_law_disclosures(
    value: Any,
) -> tuple[dict[str, Any], ...] | None:
    """Measured disclosures for a cache write.

    Outside a computation the active-run registry is not copied: the result is
    a measured empty tuple. An explicit ``None`` stays unknown.
    """

    from simulator.vapour_rail.run_disclosure import (
        active_computation_snapshot,
        parse_stored_disclosure_field,
    )

    if value is _FROM_COMPUTATION:
        return active_computation_snapshot()
    if value is None:
        return None
    return parse_stored_disclosure_field(value, present=True)


def _prefer_law_disclosures(
    existing: Any,
    incoming: Any,
) -> tuple[dict[str, Any], ...] | None:
    """Keep a measured disclosure, including a measured empty tuple.

    Unknown (None) yields to a measured incoming value. It never becomes [].
    """

    from simulator.vapour_rail.run_disclosure import parse_stored_disclosure_field

    if existing is not None:
        measured = parse_stored_disclosure_field(existing, present=True)
        if measured is not None:
            return measured
    if incoming is None:
        return None
    return parse_stored_disclosure_field(incoming, present=True)


def _law_disclosures_json(items: Any) -> str | None:
    measured = _prefer_law_disclosures(None, items)
    if measured is None:
        return None
    return json.dumps(
        list(measured),
        sort_keys=True,
        separators=(",", ":"),
    )


def _law_disclosures_from_json(raw: Any) -> tuple[dict[str, Any], ...] | None:
    from simulator.vapour_rail.run_disclosure import parse_stored_disclosure_field

    if raw is None:
        return None
    return parse_stored_disclosure_field(raw, present=True)


def _disclosures_known(entry: Mapping[str, Any]) -> bool:
    """False when the row's disclosure is unknown. Empty is known.

    Malformed entries are unknown. Presence of the field is not enough:
    ``[null]`` and ``[{}]`` must miss, not publish as an unflagged hit.
    """

    from simulator.vapour_rail.run_disclosure import stored_disclosures_known

    return stored_disclosures_known(entry)


def recorded_law_disclosures(record: Mapping[str, Any] | None) -> Any:
    """Memo disclosures, or None when the memo never carried the field."""

    if not isinstance(record, Mapping) or "law_disclosures" not in record:
        return None
    return record["law_disclosures"]


class PT0DeterminismStore:
    """PT-0 capture/replay store, optionally backed by the PT-1 SQLite DB."""

    def __init__(
        self,
        mode: str = "capture",
        *,
        db_path: str | Path | None = None,
        read_only_base_db_path: str | Path | None = None,
        strict_vapor_gate: bool = False,
        control_quantization: ControlQuantization | None = None,
    ) -> None:
        if mode not in {"capture", "replay"}:
            raise ValueError("PT0DeterminismStore mode must be capture or replay")
        self.mode = mode
        self.persistent_path = Path(db_path) if db_path is not None else None
        self.read_only_base_db_path = (
            Path(read_only_base_db_path)
            if read_only_base_db_path is not None
            else None
        )
        self.persistent_store = (
            PT1PersistentEquilibriumStore(
                self.persistent_path,
                read_only_base_db_path=self.read_only_base_db_path,
                strict_vapor_gate=strict_vapor_gate,
                control_quantization=control_quantization,
            )
            if self.persistent_path is not None
            else None
        )
        self._entries: dict[str, dict[str, Any]] = {}
        self.physics_bucket_entries: dict[str, str] = {}
        self.capture_sequence: list[dict[str, Any]] = []
        self.replay_sequence: list[dict[str, Any]] = []
        self.misses: list[dict[str, Any]] = []
        self.cache_events: list[dict[str, str]] = []
        self.hits: int = 0
        self.live_fills: int = 0
        self.last_cache_state: str | None = None
        self.quantize_live_controls: bool = True
        self.cache_tier_ceiling: str = "cached_interpolated"
        self.cached_real_miss_policy: str | None = None
        self.strict_vapor_gate = bool(strict_vapor_gate)
        self._control_quantization = (
            control_quantization or _PRODUCTION_QUANTIZATION
        )

    @property
    def control_quantization(self) -> ControlQuantization:
        return self._control_quantization

    @property
    def entries(self) -> dict[str, dict[str, Any]]:
        """Snapshot of the capture table for readers that compare or pickle it.

        The live cache stays on ``_entries``. Mutating the snapshot does not
        write the store.
        """

        return copy.deepcopy(self._entries)

    @property
    def capture_enabled(self) -> bool:
        return self.mode == "capture"

    @property
    def replay_enabled(self) -> bool:
        return self.mode == "replay"

    @property
    def write_through_enabled(self) -> bool:
        return self.capture_enabled and self.persistent_store is not None

    def _clone_for_context(
        self,
        mode: str,
        *,
        persistent_cache: bool,
    ) -> "PT0DeterminismStore":
        clone = PT0DeterminismStore(
            mode,
            db_path=(self.persistent_path if persistent_cache else None),
            read_only_base_db_path=(
                self.read_only_base_db_path if persistent_cache else None
            ),
            strict_vapor_gate=self.strict_vapor_gate,
            control_quantization=self._control_quantization,
        )
        clone._entries = copy.deepcopy(self._entries)
        clone.physics_bucket_entries = copy.deepcopy(self.physics_bucket_entries)
        clone.capture_sequence = copy.deepcopy(self.capture_sequence)
        clone.quantize_live_controls = self.quantize_live_controls
        clone.cache_tier_ceiling = self.cache_tier_ceiling
        clone.cached_real_miss_policy = self.cached_real_miss_policy
        return clone

    def clone_for_replay(self) -> "PT0DeterminismStore":
        return self._clone_for_context("replay", persistent_cache=True)

    def clone_for_forecast(self) -> "PT0DeterminismStore":
        """Capture omits PT1; replay reopens its path.

        Replay initialization may write schema metadata.
        """
        # Capture forecasts compute cache misses against their detached backend
        # and retain fills only in the copied in-memory store. Replay forecasts
        # reopen the live PT1 path, whose initializer may write schema metadata.
        return self._clone_for_context(
            self.mode,
            persistent_cache=self.replay_enabled,
        )

    def quantized_controls(
        self,
        sim: Any,
        *,
        fO2_log: float | None,
    ) -> dict[str, float | None]:
        melt_temperature_C = float(sim.melt.temperature_C)
        T_K = _quantize(
            melt_temperature_C + CELSIUS_TO_KELVIN_OFFSET,
            self._control_quantization.t_k_quantum,
            self._control_quantization.t_k_decimals,
        )
        if T_K is None:
            raise PT0InvalidControls(
                "non-finite melt temperature passed to PT-0 quantization: "
                f"{melt_temperature_C!r}"
            )
        melt_pressure_mbar = float(sim.melt.p_total_mbar)
        pressure_bar = _quantize(
            melt_pressure_mbar / 1000.0,
            self._control_quantization.pressure_bar_quantum,
            self._control_quantization.pressure_bar_decimals,
        )
        if pressure_bar is None:
            raise PT0InvalidControls(
                "non-finite melt pressure passed to PT-0 quantization: "
                f"{melt_pressure_mbar!r} mbar"
            )
        if fO2_log is None:
            fO2_log = _authoritative_melt_fO2_log(sim)
        quantized_fO2_log = _quantize(
            fO2_log,
            self._control_quantization.log_fo2_quantum,
            self._control_quantization.log_fo2_decimals,
        )
        if quantized_fO2_log is None:
            raise PT0InvalidControls(
                "non-finite fO2_log passed to PT-0 quantization: "
                f"{fO2_log!r}"
            )
        return {
            "temperature_C": None if T_K is None else float(T_K) - CELSIUS_TO_KELVIN_OFFSET,
            "pressure_bar": pressure_bar,
            "fO2_log": quantized_fO2_log,
        }

    def quantized_pO2_bar(
        self,
        sim: Any,
        *,
        pO2_bar: float | None = None,
    ) -> float:
        # _sigfig returns None on non-finite input, so a non-finite commanded
        # pO2 would otherwise leak a None into the control surface (the same
        # invalid-control class as T_K / pressure / fO2 above). Commanded 0.0
        # (controlled-O2 off) is finite and preserved by _sigfig; only NaN/inf
        # is refused.
        commanded_pO2_bar = float(
            sim._commanded_pO2_bar() if pO2_bar is None else pO2_bar
        )
        quantized = _sigfig(
            commanded_pO2_bar,
            self._control_quantization.composition_sig_figs,
        )
        if quantized is None:
            raise PT0InvalidControls(
                "non-finite commanded pO2 passed to PT-0 quantization: "
                f"{commanded_pO2_bar!r} bar"
            )
        return quantized

    def canonical_composition_mol_by_account(
        self,
        sim: Any,
        composition_by_account: Mapping[str, Mapping[str, float]] | None = None,
    ) -> dict[str, dict[str, float]]:
        if composition_by_account is None:
            accounts = sim.atom_ledger.mol_by_account()
            composition_by_account = {
                str(account): sim.atom_ledger.project_account_mol(str(account))
                for account in accounts
            }
        return canonicalized_composition_mol_by_account(
            composition_by_account,
            sig_figs=self._control_quantization.composition_sig_figs,
        )

    def canonical_composition_mol(
        self,
        sim: Any,
        composition_by_account: Mapping[str, Mapping[str, float]] | None = None,
    ) -> dict[str, float]:
        totals: dict[str, float] = {}
        for species_mol in self.canonical_composition_mol_by_account(
            sim,
            composition_by_account,
        ).values():
            for species, mol in species_mol.items():
                totals[species] = totals.get(species, 0.0) + float(mol)
        return {
            species: mol
            for species, mol in totals.items()
            if mol > 0.0
        }

    def capture_equilibrium(
        self,
        sim: Any,
        result: EquilibriumResult,
        *,
        repair_notices: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        if uncacheable_replay_disposition(sim) or _openimcc_fallback_is_uncacheable(
            sim
        ):
            self._mark_uncacheable_capture(sim)
            return
        if not self._cached_real_binding_eligible(sim):
            self._mark_uncacheable_capture(sim)
            return
        if not _is_cacheable_equilibrium_result(result):
            self._mark_uncacheable_capture(sim)
            return
        intent = _equilibrium_payload_intent(sim)
        key = self._equilibrium_key(sim)
        expected_dependency_vector = record_dependency_vector(
            sim,
            intent,
            artifact="equilibrium_post_record",
        )
        if dependency_vector_from_replay_key(key) != expected_dependency_vector:
            self._mark_uncacheable_capture(sim)
            return
        payload = canonical_replay_output_projection(
            "equilibrium_post_record", sim, result
        )
        readback_policy_miss = False
        try:
            readback_policy_miss = self._store(
                "equilibrium_post_record",
                key,
                payload,
                engine_version_provenance=_engine_version_provenance(sim, intent),
                repair_notices=_repair_notices_for_capture(sim, repair_notices),
            )
        except (UncacheableKey, UncacheablePayload) as exc:
            self._mark_uncacheable_capture(sim, reason=exc)
            return
        if readback_policy_miss:
            self._record_capture_readback_policy_miss(sim)
        sim._last_reduced_real_cache_state = self.last_cache_state

    def cached_equilibrium(self, sim: Any) -> EquilibriumResult | None:
        if not self.write_through_enabled:
            return None
        if not self._cached_real_binding_eligible(sim):
            self._mark_uncacheable_capture(sim)
            return None
        key = self._equilibrium_key(sim)
        payload = self._lookup_optional(
            "equilibrium_post_record",
            key,
            physics_bucket_key=canonical_physics_bucket_key_from_replay_key(key),
            sim=sim,
            return_decoded=True,
        )
        if payload is None:
            return None
        return self._equilibrium_from_payload(sim, payload)

    def replay_equilibrium(self, sim: Any) -> EquilibriumResult:
        self._authorize_cached_real_replay(sim)
        payload = self._lookup(
            "equilibrium_post_record",
            self._equilibrium_key(sim),
            sim=sim,
            return_decoded=True,
        )
        return self._equilibrium_from_payload(sim, payload)

    def _equilibrium_key(self, sim: Any) -> dict[str, Any]:
        intent = _equilibrium_payload_intent(sim)
        return canonical_replay_key(
            sim,
            artifact="equilibrium_post_record",
            intent=intent,
            # Equilibrium is solved at actual conditions; keep live-T fO2.
            fO2_log=_authoritative_melt_fO2_log(sim),
            fe_redox_policy="intrinsic",
            control_quantization=self._control_quantization,
        )

    def _physics_ladder_key_for_lookup(
        self,
        sim: Any,
        replay_key: Mapping[str, Any],
        rung_tag: str,
    ) -> dict[str, Any]:
        sig_figs = _physics_ladder_sig_figs(rung_tag)
        default_key = canonical_physics_ladder_bucket_key_from_replay_key(
            replay_key,
            rung_tag,
        )
        if sim is None:
            return default_key
        provider_input_builder = getattr(
            sim,
            "_silicate_provider_melt_inputs",
            None,
        )
        if not callable(provider_input_builder):
            return default_key
        intent = _equilibrium_payload_intent(sim)
        provider_inputs = provider_input_builder(
            intent,
            composition_sig_figs=int(sig_figs),
        )
        if provider_inputs is None:
            return default_key
        rung_replay_key = copy.deepcopy(dict(replay_key))
        rung_replay_key["composition_mol_fraction"] = (
            _backend_boundary_composition(
                sim,
                intent,
                None,
                namespace_id=str(replay_key["namespace_id"]),
                sig_figs=int(sig_figs),
                composition_mol=provider_inputs.composition_mol,
            )
        )
        return canonical_physics_ladder_bucket_key_from_replay_key(
            rung_replay_key,
            rung_tag,
        )

    def _equilibrium_from_payload(
        self,
        sim: Any,
        payload: Mapping[str, Any] | _DecodedEquilibriumReplayPayload,
    ) -> EquilibriumResult:
        decoded = (
            payload
            if isinstance(payload, _DecodedEquilibriumReplayPayload)
            else _decode_equilibrium_replay_payload(payload)
        )
        result = _refresh_feo_qualification(sim, decoded.result)
        sim._last_reduced_real_cache_state = self.last_cache_state
        sim._last_backend_status = getattr(result, "status", "ok")
        self._apply_equilibrium_cache_authority(sim, result)
        history = getattr(sim, "_backend_status_history", None)
        if isinstance(history, list):
            history.append(str(sim._last_backend_status))
        sim._last_vapor_pressures_source = dict(decoded.vapor_pressures_source)
        vapor_pressure_diagnostic = dict(decoded.vapor_pressure_diagnostic)
        sim._last_vapor_pressure_diagnostic = vapor_pressure_diagnostic
        # d-401. Replay and the write-through hit both adopt the cached
        # vapour evaluation here, after the hour reset has cleared the
        # stamp. The restored diagnostic's pO2 is the pressure that
        # evaluation consumed. Leaving the stamp unset makes the
        # snapshot ceiling read the post-bleed reservoir. A diagnostic
        # that omits pO2 records absence: this path does not hold the
        # live dispatch argument, and the later reservoir is not a
        # substitute.
        from simulator.evaporation import (
            pressure_vapour_evaluation_consumed,
            remember_evaluated_transport_pO2,
        )

        remember_evaluated_transport_pO2(
            sim,
            pressure_vapour_evaluation_consumed(
                vapor_pressure_diagnostic,
                dispatch_pO2_bar=None,
            ),
        )
        sulfur = getattr(result, "sulfur_saturation", None)
        sim._last_sulfur_saturation_result = sulfur
        replay_fO2_log = getattr(result, "requested_fO2_log", None)
        if replay_fO2_log is None:
            replay_fO2_log = getattr(result, "fO2_log", None)
        if replay_fO2_log is not None:
            sync = getattr(sim, "_sync_oxygen_reservoir_mirror", None)
            if callable(sync):
                sync()
            else:
                fO2_log = float(replay_fO2_log)
                sim.melt.fO2_log = fO2_log
                if hasattr(sim.melt, "melt_fO2_log"):
                    sim.melt.melt_fO2_log = fO2_log
        return result

    def _apply_equilibrium_cache_authority(
        self,
        sim: Any,
        result: EquilibriumResult,
    ) -> None:
        cache_state = str(self.last_cache_state or "")
        if cache_state not in APPROXIMATE_REDUCED_REAL_CACHE_STATES:
            return
        sim._backend_authoritative = False
        diagnostic = {
            "reduced_real_cache_state": cache_state,
            "reduced_real_cache_authoritative": False,
        }
        result.diagnostics = {**dict(result.diagnostics or {}), **diagnostic}
        existing = getattr(sim, "_last_backend_diagnostics", None)
        if isinstance(existing, Mapping):
            sim._last_backend_diagnostics = {**dict(existing), **diagnostic}

    def capture_gate_curve(
        self,
        sim: Any,
        *,
        fO2_log: float,
        curve: Mapping[str, Any],
        repair_notices: Sequence[Mapping[str, Any]] | None = None,
        redox_state: RedoxState | None = None,
        law_disclosures: Any = _FROM_COMPUTATION,
    ) -> None:
        active_record = getattr(
            sim,
            "_freeze_gate_liquid_fraction_cache",
            None,
        )
        provider_outcome = (
            active_record.get("provider_outcome")
            if isinstance(active_record, Mapping)
            else None
        )
        disposition = (
            active_record.get("disposition", "live")
            if isinstance(active_record, Mapping)
            else "live"
        )
        if not gate_curve_memo_is_cacheable(
            sim,
            curve,
            provider_outcome,
            disposition=disposition,
        ):
            self._mark_uncacheable_capture(sim)
            return
        redox_state_kwargs = _non_absolute_redox_state_kwargs(redox_state)
        from simulator.engine_binding_admission import (
            gate_curve_memo_evidence,
            gate_curve_memo_origin_matches,
        )

        if not (
            isinstance(active_record, Mapping)
            and active_record.get("disposition") in {"live", "memo-hit"}
            and isinstance(active_record.get("curve"), Mapping)
            and _identity_gate_curve(active_record["curve"]) == (
                _identity_gate_curve(curve)
            )
            and gate_curve_memo_origin_matches(
                sim,
                curve,
                active_record.get("origin"),
                fresh_evidence=False,
                **redox_state_kwargs,
            )
        ):
            self._mark_uncacheable_capture(sim)
            return
        evidence = gate_curve_memo_evidence(
            sim,
            curve,
            include_selected_bindings=True,
        )
        if (
            not gate_curve_memo_origin_matches(
                sim,
                curve,
                active_record.get("origin"),
                fresh_evidence=True,
                evidence=evidence,
                **redox_state_kwargs,
            )
            or not evidence.eligible
        ):
            self._mark_uncacheable_capture(sim)
            return
        provider_role = _gate_provider_role_for_capture(sim, curve)
        key = canonical_replay_key(
            sim,
            artifact="freeze_gate_curve",
            intent=ChemistryIntent.GATE_LIQUID_FRACTION,
            fO2_log=fO2_log,
            fe_redox_policy="intrinsic",
            provider_role=provider_role,
            control_quantization=self._control_quantization,
            **redox_state_kwargs,
        )
        if dependency_vector_from_replay_key(key) != record_dependency_vector(
            sim,
            ChemistryIntent.GATE_LIQUID_FRACTION,
            artifact="freeze_gate_curve",
            provider_role=provider_role,
        ):
            self._mark_uncacheable_capture(sim)
            return
        readback_policy_miss = False
        try:
            readback_policy_miss = self._store(
                "freeze_gate_curve",
                key,
                {"curve": _curve_payload(curve)},
                engine_version_provenance=_engine_version_provenance(
                    sim,
                    ChemistryIntent.GATE_LIQUID_FRACTION,
                    provider_role=provider_role,
                ),
                repair_notices=_repair_notices_for_capture(sim, repair_notices),
                law_disclosures=law_disclosures,
            )
        except (UncacheableKey, UncacheablePayload) as exc:
            self._mark_uncacheable_capture(sim, reason=exc)
            return
        if readback_policy_miss:
            self._record_capture_readback_policy_miss(sim)
        sim._last_reduced_real_cache_state = self.last_cache_state

    def _mark_uncacheable_capture(
        self,
        sim: Any,
        *,
        reason: UncacheableKey | UncacheablePayload | None = None,
    ) -> None:
        self.last_cache_state = None
        sim._last_reduced_real_cache_state = None
        if reason is not None:
            record_repair_notice(
                sim,
                {
                    "kind": "reduced_real_cache_write_skipped",
                    "category": reason.category,
                    "reason": reason.reason,
                },
            )

    @staticmethod
    def _record_capture_readback_policy_miss(sim: Any) -> None:
        record_repair_notice(
            sim,
            {
                "kind": "reduced_real_cache_capture_readback_miss",
                "reason": "reader policy did not admit the stored row",
            },
        )

    def replay_gate_curve(
        self,
        sim: Any,
        *,
        fO2_log: float,
        redox_state: RedoxState | None = None,
    ) -> dict[str, Any]:
        roles = _gate_provider_roles_for_replay(sim)
        from simulator.engine_binding_admission import EngineBindingAdmissionError

        admitted: list[str] = []
        refusals: list[EngineBindingAdmissionError] = []
        for provider_role in roles:
            try:
                self._authorize_cached_real_replay(
                    sim,
                    provider_role=provider_role,
                    artifact="freeze_gate_curve",
                )
            except EngineBindingAdmissionError as exc:
                refusals.append(exc)
            else:
                admitted.append(provider_role)
        roles = tuple(admitted)
        if not roles:
            if refusals:
                raise refusals[0]
            raise PT0CacheMiss(
                "PT-0 cached replay miss: no admitted gate candidates"
            )
        redox_state_kwargs = _non_absolute_redox_state_kwargs(redox_state)
        keys = tuple(
            canonical_replay_key(
                sim,
                artifact="freeze_gate_curve",
                intent=ChemistryIntent.GATE_LIQUID_FRACTION,
                fO2_log=fO2_log,
                fe_redox_policy="intrinsic",
                provider_role=provider_role,
                control_quantization=self._control_quantization,
                **redox_state_kwargs,
            )
            for provider_role in roles
        )
        payload = self._lookup_first_available(
            "freeze_gate_curve",
            keys,
            sim=sim,
            return_decoded=True,
        )
        sim._last_reduced_real_cache_state = self.last_cache_state
        return payload

    def _cached_real_binding_eligible(
        self,
        sim: Any,
        *,
        provider_role: str | None = None,
        artifact: str = "equilibrium_post_record",
    ) -> bool:
        from simulator.engine_binding_admission import (
            live_binding_cache_eligibility,
        )

        return live_binding_cache_eligibility(
            sim,
            provider_role=provider_role,
            artifact=artifact,
        )

    def gate_curve_cache_eligible(
        self,
        sim: Any,
        curve: Mapping[str, Any],
    ) -> bool:
        if not _is_cacheable_gate_curve(curve):
            return False
        return self._cached_real_binding_eligible(
            sim,
            provider_role=_gate_curve_provider_role(curve),
            artifact="freeze_gate_curve",
        )

    def _authorize_cached_real_replay(
        self,
        sim: Any,
        *,
        provider_role: str | None = None,
        artifact: str = "equilibrium_post_record",
    ) -> None:
        from simulator.engine_binding_admission import (
            authorize_sim_binding_replay,
        )

        authorize_sim_binding_replay(
            sim,
            provider_role=provider_role,
            artifact=artifact,
        )

    def summary(self) -> dict[str, Any]:
        from simulator.interpolation_uncertainty import (
            interpolation_uncertainty_points_from_replay_sequence,
            ranked_table_drain,
        )

        by_artifact = Counter(
            record["artifact"] for record in self.capture_sequence
        )
        state_counts = Counter(
            event["cache_state"] for event in self.cache_events
        )
        state_counts_by_artifact: dict[str, Counter[str]] = {}
        for event in self.cache_events:
            artifact = event["artifact"]
            state_counts_by_artifact.setdefault(artifact, Counter())
            state_counts_by_artifact[artifact][event["cache_state"]] += 1
        summary = {
            "mode": self.mode,
            "entries": len(self._entries),
            "capture_calls": len(self.capture_sequence),
            "replay_calls": len(self.replay_sequence),
            "hits": self.hits,
            "misses": len(self.misses),
            "live_fills": self.live_fills,
            "cache_states": CACHE_STATES,
            "cache_state_counts": {
                state: state_counts.get(state, 0)
                for state in CACHE_STATES
            },
            "cache_state_counts_by_artifact": {
                artifact: {
                    state: counts.get(state, 0)
                    for state in CACHE_STATES
                }
                for artifact, counts in sorted(state_counts_by_artifact.items())
            },
            "capture_calls_by_artifact": dict(sorted(by_artifact.items())),
            "key_drift_histogram": self.key_drift_histogram(),
            "key_drift_histogram_scope": (
                "replay_mode_1_to_1_capture_replay_only"
            ),
            "first_miss": self.misses[0] if self.misses else None,
            "persistent_store": (
                None
                if self.persistent_path is None
                else {
                    "path": str(self.persistent_path),
                    "table": PT1_EQUILIBRIUM_TABLE,
                    "schema_version": PT1_STORE_SCHEMA_VERSION,
                }
            ),
        }
        uncertainty_points = interpolation_uncertainty_points_from_replay_sequence(
            self.replay_sequence
        )
        if uncertainty_points:
            summary["interpolation_uncertainty_points"] = len(uncertainty_points)
            summary["interpolation_uncertainty_ranked_table_drain"] = (
                ranked_table_drain(uncertainty_points)
            )
        return summary

    def key_drift_histogram(self) -> dict[str, int]:
        """Replay-only drift counts for 1:1 capture/replay sequence comparisons."""
        if not self.replay_enabled:
            return {}
        counts: Counter[str] = Counter()
        for index, replay in enumerate(self.replay_sequence):
            if index >= len(self.capture_sequence):
                counts["<extra_replay_call>"] += 1
                continue
            capture = self.capture_sequence[index]
            for field in _diff_top_fields(capture["key"], replay["key"]):
                counts[field] += 1
        return dict(sorted(counts.items()))

    def _store(
        self,
        artifact: str,
        key: Mapping[str, Any],
        payload: Mapping[str, Any],
        *,
        engine_version_provenance: str | None = None,
        repair_notices: Sequence[Mapping[str, Any]] = (),
        law_disclosures: Any = _FROM_COMPUTATION,
    ) -> bool:
        indexability_error = _ladder_indexability(key)
        if indexability_error is not None:
            raise indexability_error
        incoming_disclosures = _resolve_law_disclosures(law_disclosures)
        key_bytes = canonical_json_bytes(key)
        payload_bytes = canonical_json_bytes(payload)
        payload_codec_version = _payload_codec_version(artifact)
        repair_notices_json = encode_repair_notices_json(repair_notices)
        key_hash = _sha256(key_bytes)
        if artifact == "freeze_gate_curve":
            curve_payload = payload.get("curve")
            if isinstance(curve_payload, Mapping):
                identity_rejection = _gate_curve_identity_rejection(
                    curve_payload,
                    (key,),
                    reader_context_policy=False,
                    write_integrity_only=True,
                )
                if identity_rejection is not None:
                    raise UncacheablePayload(artifact, key_hash)
        payload_hash = _sha256(payload_bytes)
        physics_bucket_key = canonical_physics_bucket_key_from_replay_key(key)
        physics_bucket_bytes = canonical_json_bytes(physics_bucket_key)
        physics_bucket_hash = _sha256(physics_bucket_bytes)
        readback_policy_miss = False
        self.capture_sequence.append(
            {
                "artifact": artifact,
                "key": copy.deepcopy(dict(key)),
                "hash": key_hash,
                "physics_bucket_hash": physics_bucket_hash,
            }
        )
        existing = self._entries.get(key_hash)
        existing_decoded = None
        if existing is not None:
            existing_admission = _admit_stored_row(
                existing,
                store_kind="pt0",
                artifact=artifact,
                key=key,
                key_bytes=key_bytes,
                key_hash=key_hash,
            )
            if existing_admission is None:
                existing = None
            else:
                existing = existing_admission.entry
                existing_decoded = existing_admission.decoded_payload
        stale_payload = existing is not None and existing_decoded is None
        if existing is not None:
            if not stale_payload and existing["payload_hash"] != payload_hash:
                raise PT0CacheCollision(
                    f"PT-0 {artifact} key collision with different payload: "
                    f"{key_hash}"
                )
            try:
                effective_notices_json = monotonic_repair_notices_json(
                    existing.get("repair_notices_json"),
                    repair_notices_json,
                )
            except Exception:
                if not stale_payload:
                    raise
                effective_notices_json = repair_notices_json
        else:
            effective_notices_json = repair_notices_json
        if self.persistent_store is not None:
            self.persistent_store.put(
                artifact=artifact,
                key=key,
                key_bytes=key_bytes,
                key_hash=key_hash,
                payload=payload,
                payload_bytes=payload_bytes,
                payload_hash=payload_hash,
                repair_notices_json=repair_notices_json,
                engine_version_provenance=engine_version_provenance,
                physics_bucket_key=physics_bucket_key,
                physics_bucket_bytes=physics_bucket_bytes,
                physics_bucket_hash=physics_bucket_hash,
                law_disclosures=incoming_disclosures,
            )
            persisted = self.persistent_store.get(
                artifact=artifact,
                key=key,
                key_bytes=key_bytes,
                key_hash=key_hash,
            )
            if persisted is None:
                readback_policy_miss = True
            else:
                effective_notices_json = persisted["repair_notices_json"]
                incoming_disclosures = _prefer_law_disclosures(
                    persisted.get("law_disclosures"),
                    incoming_disclosures,
                )
        if existing is not None and not stale_payload:
            existing["repair_notices_json"] = effective_notices_json
            existing["law_disclosures"] = _prefer_law_disclosures(
                existing.get("law_disclosures"),
                incoming_disclosures,
            )
            self._entries[key_hash] = existing
            self.physics_bucket_entries.setdefault(physics_bucket_hash, key_hash)
            return readback_policy_miss
        entry = {
            "artifact": artifact,
            "key": copy.deepcopy(dict(key)),
            "key_hash": key_hash,
            "key_bytes": key_bytes.decode("utf-8"),
            "payload": copy.deepcopy(dict(payload)),
            "payload_hash": payload_hash,
            "payload_codec_version": payload_codec_version,
            "repair_notices_json": effective_notices_json,
            "law_disclosures": incoming_disclosures,
            "physics_bucket_key": copy.deepcopy(dict(physics_bucket_key)),
            "physics_bucket_hash": physics_bucket_hash,
            "cache_state": "live_fill",
        }
        self._entries[key_hash] = entry
        self.physics_bucket_entries.setdefault(physics_bucket_hash, key_hash)
        self.live_fills += 1
        self.last_cache_state = "live_fill"
        self._record_cache_event(artifact, "live_fill")
        return readback_policy_miss

    def _lookup(
        self,
        artifact: str,
        key: Mapping[str, Any],
        *,
        sim: Any | None = None,
        return_decoded: bool = False,
    ) -> Any:
        return self._lookup_first_available(
            artifact,
            tuple(_compatible_replay_keys(key)),
            sim=sim,
            return_decoded=return_decoded,
        )

    def _lookup_first_available(
        self,
        artifact: str,
        keys: tuple[Mapping[str, Any], ...],
        *,
        sim: Any | None = None,
        return_decoded: bool = False,
    ) -> Any:
        if not keys:
            raise PT0CacheMiss(f"PT-0 cached replay miss: no keys for {artifact}")
        if any(_ladder_indexability(key) is not None for key in keys):
            self.last_cache_state = None
            raise PT0CacheMiss(
                f"PT-0 cached replay miss: unindexable query for {artifact}"
            )

        checked: list[tuple[Mapping[str, Any], bytes, str]] = []
        for key in _expand_compatible_replay_keys(keys):
            key_bytes = canonical_json_bytes(key)
            key_hash = _sha256(key_bytes)
            checked.append((key, key_bytes, key_hash))
            entry, admission_decoded = self._entry_for_key(
                artifact,
                key,
                key_bytes,
                key_hash,
                return_decoded=True,
            )
            # This path already matched the complete canonical request key.
            # Dependency-vector completeness is required only when comparing
            # different keys for interpolation neighbors.
            if entry is None:
                continue
            decoded = admission_decoded
            if decoded is None:
                continue
            if not _disclosures_known(entry):
                continue
            self.replay_sequence.append(
                {
                    "artifact": artifact,
                    "key": copy.deepcopy(dict(key)),
                    "hash": key_hash,
                    "cache_state": "cached_exact",
                }
            )
            sequence_index = len(self.replay_sequence) - 1
            if len(self.capture_sequence) <= sequence_index:
                self.capture_sequence.append(
                    {
                        "artifact": artifact,
                        "key": copy.deepcopy(dict(entry["key"])),
                        "hash": key_hash,
                        "persistent": True,
                    }
                )
            self.hits += 1
            self.last_cache_state = "cached_exact"
            self._record_cache_event(artifact, "cached_exact")
            replay_notices = _emit_repair_notices(
                sim,
                (entry.get("repair_notices_json"),),
            )
            if artifact == "freeze_gate_curve" and sim is not None:
                sim._last_reduced_real_replay_notices = replay_notices
            return self._accept_cached_hit(
                entry,
                owner=sim,
                decoded_payload=decoded if return_decoded else None,
            )

        key, _key_bytes, key_hash = checked[0]
        self.replay_sequence.append(
            {"artifact": artifact, "key": copy.deepcopy(dict(key)), "hash": key_hash}
        )
        miss = {
            "artifact": artifact,
            "key_hash": key_hash,
            "sequence_index": len(self.replay_sequence) - 1,
            "drift_fields": self._drift_fields_for_latest_replay(),
        }
        if len(checked) > 1:
            miss["alternate_key_hashes"] = [
                alternate_hash for _, _, alternate_hash in checked[1:]
            ]
        self.misses.append(miss)
        self.last_cache_state = None
        raise PT0CacheMiss(f"PT-0 cached replay miss: {miss}")

    def _lookup_optional(
        self,
        artifact: str,
        key: Mapping[str, Any],
        *,
        physics_bucket_key: Mapping[str, Any] | None = None,
        sim: Any | None = None,
        return_decoded: bool = False,
    ) -> Any | None:
        if _ladder_indexability(key) is not None:
            self.last_cache_state = None
            return None
        tier_ceiling = str(
            getattr(self, "cache_tier_ceiling", "cached_interpolated")
        )
        for candidate_key in _compatible_replay_keys(key):
            key_bytes = canonical_json_bytes(candidate_key)
            key_hash = _sha256(key_bytes)
            entry, decoded = self._entry_for_key(
                artifact,
                candidate_key,
                key_bytes,
                key_hash,
                return_decoded=True,
            )
            cache_state = "cached_exact"
            candidate_physics_bucket_key = (
                physics_bucket_key
                if candidate_key == key and physics_bucket_key is not None
                else canonical_physics_bucket_key_from_replay_key(candidate_key)
            )
            physics_bucket_hash: str | None = None
            physics_bucket_rung: str | None = None
            if entry is None and tier_ceiling != "cached_exact":
                bucket_hit = self._entry_for_physics_bucket(
                    artifact,
                    candidate_physics_bucket_key,
                    query_key=candidate_key,
                )
                entry, decoded = bucket_hit if bucket_hit is not None else (None, None)
                cache_state = "cached_physics_bucket"
                physics_bucket_hash = _sha256(
                    canonical_json_bytes(candidate_physics_bucket_key)
                )
                if entry is None:
                    for rung_tag, _sig_figs in PHYSICS_BUCKET_LADDER_RUNGS:
                        rung_key = self._physics_ladder_key_for_lookup(
                            sim,
                            candidate_key,
                            rung_tag,
                        )
                        ladder_hit = self._entry_for_physics_ladder_bucket(
                            artifact,
                            rung_tag,
                            rung_key,
                            query_key=candidate_key,
                        )
                        entry, decoded = (
                            ladder_hit if ladder_hit is not None else (None, None)
                        )
                        if entry is not None:
                            cache_state = "cached_physics_bucket"
                            candidate_physics_bucket_key = rung_key
                            physics_bucket_hash = _sha256(
                                canonical_json_bytes(rung_key)
                            )
                            physics_bucket_rung = rung_tag
                            break
                if entry is None:
                    for rung_tag, _sig_figs in PHYSICS_BUCKET_CONTROL_LADDER_RUNGS:
                        rung_key = self._physics_ladder_key_for_lookup(
                            sim,
                            candidate_key,
                            rung_tag,
                        )
                        ladder_hit = self._entry_for_physics_ladder_bucket(
                            artifact,
                            rung_tag,
                            rung_key,
                            query_key=candidate_key,
                        )
                        entry, decoded = (
                            ladder_hit if ladder_hit is not None else (None, None)
                        )
                        if entry is not None:
                            cache_state = "cached_physics_bucket"
                            candidate_physics_bucket_key = rung_key
                            physics_bucket_hash = _sha256(
                                canonical_json_bytes(rung_key)
                            )
                            physics_bucket_rung = rung_tag
                            break
            if entry is not None and not replay_dependency_vectors_match(
                candidate_key,
                entry.get("key", {}),
            ):
                entry = None
            if entry is not None and not _disclosures_known(entry):
                entry = None
            if entry is not None:
                break
            if tier_ceiling == "cached_interpolated":
                interpolated = self._lookup_interpolated(
                    artifact,
                    candidate_key,
                    sim=sim,
                    return_decoded=return_decoded,
                )
                if interpolated is not None:
                    return interpolated
        else:
            return None
        replay_event = {
            "artifact": artifact,
            "key": copy.deepcopy(dict(candidate_key)),
            "hash": key_hash,
            "cache_state": cache_state,
        }
        if (
            cache_state == "cached_physics_bucket"
            and candidate_physics_bucket_key is not None
        ):
            replay_event["physics_bucket_hash"] = physics_bucket_hash
            if physics_bucket_rung is not None:
                replay_event["physics_bucket_rung"] = physics_bucket_rung
                if _physics_ladder_coarsens_controls(physics_bucket_rung):
                    replay_event["physics_bucket_error_budget"] = (
                        physics_control_rung_error_budget(
                            candidate_key,
                            entry["key"],
                            physics_bucket_rung,
                            source_payload=entry.get("payload"),
                        )
                    )
            replay_event["source_key_hash"] = str(entry["key_hash"])
        self.replay_sequence.append(replay_event)
        self.hits += 1
        self.last_cache_state = cache_state
        self._record_cache_event(artifact, cache_state)
        if cache_state == "cached_exact":
            _emit_repair_notices(sim, (entry.get("repair_notices_json"),))
        else:
            _emit_repair_notices(
                sim,
                (entry.get("repair_notices_json"),),
                approximation_cache_state=cache_state,
            )
        return self._accept_cached_hit(
            entry,
            owner=sim,
            decoded_payload=decoded if return_decoded else None,
        )

    def _entry_for_key(
        self,
        artifact: str,
        key: Mapping[str, Any],
        key_bytes: bytes,
        key_hash: str,
        *,
        return_decoded: bool = False,
    ) -> Any | None:
        entry = self._entries.get(key_hash)
        decoded = None
        checked_by_persistent_store = False
        if entry is None and self.persistent_store is not None:
            fetched = self.persistent_store._get_with_decoded(
                artifact=artifact,
                key=key,
                key_bytes=key_bytes,
                key_hash=key_hash,
            )
            if fetched is not None:
                entry, decoded = fetched
                checked_by_persistent_store = True
            if entry is not None:
                self._entries[key_hash] = copy.deepcopy(entry)
        if entry is not None and not checked_by_persistent_store:
            admitted = _admit_stored_row(
                entry,
                store_kind="pt0",
                artifact=artifact,
                key=key,
                key_bytes=key_bytes,
                key_hash=key_hash,
                validate_interpolation_domain=True,
            )
            decoded = None if admitted is None else admitted.decoded_payload
            if decoded is None:
                entry = None
            else:
                entry = admitted.entry
        return (entry, decoded) if return_decoded else entry

    def _entry_for_physics_bucket(
        self,
        artifact: str,
        physics_bucket_key: Mapping[str, Any],
        *,
        query_key: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], Any] | None:
        physics_bucket_bytes = canonical_json_bytes(physics_bucket_key)
        physics_bucket_hash = _sha256(physics_bucket_bytes)
        key_hash = self.physics_bucket_entries.get(physics_bucket_hash)
        entry = self._entries.get(key_hash) if key_hash is not None else None
        checked_hashes: set[str] = set()
        if entry is not None:
            checked_hashes.add(str(key_hash))
            admitted = _admit_stored_row(
                entry,
                store_kind="pt0",
                artifact=artifact,
                key_hash=str(key_hash),
                admission_keys=(
                    (query_key,) if query_key is not None else ()
                ),
                physics_bucket_key=physics_bucket_key,
                physics_bucket_bytes=physics_bucket_bytes,
                physics_bucket_hash=physics_bucket_hash,
                validate_interpolation_domain=True,
            )
            if admitted is not None and admitted.decoded_payload is not None:
                return admitted.entry, admitted.decoded_payload
        entry = None
        for candidate_hash, candidate in self._entries.items():
            if candidate_hash in checked_hashes:
                continue
            admitted = _admit_stored_row(
                candidate,
                store_kind="pt0",
                artifact=artifact,
                key_hash=candidate_hash,
                admission_keys=(query_key,) if query_key is not None else (),
                physics_bucket_key=physics_bucket_key,
                physics_bucket_bytes=physics_bucket_bytes,
                physics_bucket_hash=physics_bucket_hash,
                validate_interpolation_domain=True,
            )
            if admitted is not None and admitted.decoded_payload is not None:
                return admitted.entry, admitted.decoded_payload
        if self.persistent_store is not None:
            bucket_hit = self.persistent_store._get_by_physics_bucket(
                artifact=artifact,
                physics_bucket_key=physics_bucket_key,
                physics_bucket_bytes=physics_bucket_bytes,
                physics_bucket_hash=physics_bucket_hash,
                query_key=query_key,
                return_decoded=True,
            )
            if bucket_hit is not None:
                entry, decoded = bucket_hit
                row_hash = str(entry["key_hash"])
                self._entries[row_hash] = copy.deepcopy(entry)
                self.physics_bucket_entries.setdefault(
                    physics_bucket_hash,
                    row_hash,
                )
                return entry, decoded
        return None

    def _accept_cached_hit(
        self,
        entry: Mapping[str, Any],
        *,
        owner: Any = None,
        decoded_payload: Any = None,
    ) -> Any | None:
        """Return the payload after re-registering a measured disclosure.

        Unknown disclosure is not a hit. Every cache-hit return goes through
        this method: exact, physics-bucket, and interpolated. ``owner`` is
        the run that publishes the disclosure when no registry is active.
        """

        from simulator.vapour_rail.run_disclosure import accept_stored_memo

        # One read gate. Unknown stays a miss. The list, including [], is the
        # measurement a later record must store; it is not read from the dict.
        self._accepted_hit_disclosures = None
        published = accept_stored_memo(entry, owner=owner)
        if published is None:
            return None
        self._accepted_hit_disclosures = published
        if decoded_payload is not None:
            return copy.deepcopy(decoded_payload)
        return copy.deepcopy(entry["payload"])

    def _merged_neighbor_disclosures(
        self,
        neighbors: Sequence[Mapping[str, Any]],
    ) -> tuple[dict[str, Any], ...] | None:
        """Additive disclosures of interpolation neighbors, or None if any is unknown."""

        from simulator.vapour_rail.run_disclosure import (
            RunDisclosure,
            parse_stored_disclosure_field,
        )

        scratch = RunDisclosure()
        for neighbor in neighbors:
            if not _disclosures_known(neighbor):
                return None
            parsed = parse_stored_disclosure_field(
                neighbor.get("law_disclosures"),
                present=True,
            )
            if parsed is None:
                return None
            scratch.reregister_public(parsed)
        return scratch.snapshot()

    def _record_cache_event(self, artifact: str, cache_state: str) -> None:
        self.cache_events.append(
            {"artifact": str(artifact), "cache_state": str(cache_state)}
        )

    def _lookup_interpolated(
        self,
        artifact: str,
        key: Mapping[str, Any],
        *,
        sim: Any | None = None,
        return_decoded: bool = False,
    ) -> Any | None:
        from simulator.reduced_real_cache_interpolation import (
            attempt_cached_interpolation,
            replay_scope_for_interpolation,
        )

        if _ladder_indexability(key) is not None:
            return None
        if self.persistent_store is None:
            return None
        key_bytes = canonical_json_bytes(key)
        key_hash = _sha256(key_bytes)
        candidates = self.persistent_store._list_interpolation_candidates(
            artifact=artifact,
            replay_scope_sha256=replay_scope_for_interpolation(key),
            exclude_key_hash=key_hash,
            query_key=key,
        )
        for candidate in candidates:
            self._entries[str(candidate["key_hash"])] = copy.deepcopy(candidate)
        attempt = attempt_cached_interpolation(key, candidates)
        if attempt is None:
            return None
        decoded = _decode_payload_for_admission(
            artifact,
            attempt["payload"],
            payload_codec_version=(
                attempt["neighbors"][0].get("payload_codec_version")
                if attempt.get("neighbors")
                else None
            ),
            keys=(key,),
            interpolation_synthesized=True,
        )
        if decoded is None:
            return None
        merged = self._merged_neighbor_disclosures(attempt["neighbors"])
        if merged is None:
            return None
        replay_event = {
            "artifact": artifact,
            "key": copy.deepcopy(dict(key)),
            "hash": key_hash,
            "cache_state": "cached_interpolated",
            "interpolation_neighbor_key_hashes": [
                str(neighbor.get("key_hash", ""))
                for neighbor in attempt["neighbors"]
            ],
            "interpolation_mode": attempt["weight_info"]["mode"],
            "interpolation_error_estimate": attempt["error_estimate"],
            "interpolation_uncertainty": attempt["uncertainty"],
            "interpolation_gate": attempt["gate"],
        }
        self.replay_sequence.append(replay_event)
        self.hits += 1
        self.last_cache_state = "cached_interpolated"
        self._record_cache_event(artifact, "cached_interpolated")
        _emit_repair_notices(
            sim,
            tuple(
                neighbor.get("repair_notices_json")
                for neighbor in attempt["neighbors"]
            ),
            approximation_cache_state="cached_interpolated",
        )
        return self._accept_cached_hit(
            {
                "payload": attempt["payload"],
                "law_disclosures": merged,
            },
            owner=sim,
            decoded_payload=decoded if return_decoded else None,
        )

    def _entry_for_physics_ladder_bucket(
        self,
        artifact: str,
        rung_tag: str,
        rung_key: Mapping[str, Any],
        *,
        query_key: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], Any] | None:
        rung_bytes = canonical_json_bytes(rung_key)
        rung_hash = _sha256(rung_bytes)
        if self.persistent_store is None:
            return None
        entry = self.persistent_store._get_by_physics_ladder_bucket(
            artifact=artifact,
            rung_tag=rung_tag,
            rung_key=rung_key,
            rung_hash=rung_hash,
            query_key=query_key,
            return_decoded=True,
        )
        if entry is not None:
            entry, decoded = entry
            self._entries[str(entry["key_hash"])] = copy.deepcopy(entry)
            return entry, decoded
        return None

    def _verify_entry(
        self,
        artifact: str,
        key: Mapping[str, Any] | None,
        key_bytes: bytes | None,
        key_hash: str,
        entry: Mapping[str, Any],
        *,
        admission_keys: Sequence[Mapping[str, Any]] | None = None,
        physics_bucket_key: Mapping[str, Any] | None = None,
        physics_bucket_bytes: bytes | None = None,
        physics_bucket_hash: str | None = None,
    ) -> Any | None:
        admitted = _admit_stored_row(
            entry,
            store_kind="pt0",
            artifact=artifact,
            key=key,
            key_bytes=key_bytes,
            key_hash=key_hash,
            admission_keys=admission_keys,
            physics_bucket_key=physics_bucket_key,
            physics_bucket_bytes=physics_bucket_bytes,
            physics_bucket_hash=physics_bucket_hash,
            validate_interpolation_domain=True,
        )
        return None if admitted is None else admitted.decoded_payload

    def _drift_fields_for_latest_replay(self) -> list[str]:
        index = len(self.replay_sequence) - 1
        if index >= len(self.capture_sequence):
            return ["<extra_replay_call>"]
        return _diff_top_fields(
            self.capture_sequence[index]["key"],
            self.replay_sequence[index]["key"],
        )


def _paths_refer_to_same_file(left: Path, right: Path) -> bool:
    left_resolved = left.resolve()
    right_resolved = right.resolve()
    if left_resolved.exists() and right_resolved.exists():
        left_stat = os.stat(left_resolved)
        right_stat = os.stat(right_resolved)
        return (left_stat.st_dev, left_stat.st_ino) == (
            right_stat.st_dev,
            right_stat.st_ino,
        )
    return os.path.normcase(str(left_resolved)) == os.path.normcase(
        str(right_resolved)
    )


def _pt1_row_bytes(value: Any, *, field: str, key_hash: str) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise PT1PersistentStoreCorrupt(
            f"PT-1 row contains invalid {field} bytes: {key_hash}"
        )
    return bytes(value)


def _pt1_row_json(raw: bytes, *, field: str, key_hash: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise PT1PersistentStoreCorrupt(
            f"PT-1 row contains invalid {field} JSON bytes: {key_hash}"
        ) from exc


def _pt1_row_key(raw: bytes, *, key_hash: str) -> Mapping[str, Any]:
    value = _pt1_row_json(raw, field="key", key_hash=key_hash)
    if not isinstance(value, Mapping):
        raise PT1PersistentStoreCorrupt(
            f"PT-1 row request key is not a mapping: {key_hash}"
        )
    pending = [(value, 1)]
    while pending:
        nested, depth = pending.pop()
        if depth > _PT1_ROW_KEY_MAX_NESTING:
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row request key nesting is too deep: {key_hash}"
            )
        if isinstance(nested, Mapping):
            pending.extend((item, depth + 1) for item in nested.values())
        elif isinstance(nested, list):
            pending.extend((item, depth + 1) for item in nested)
    return value


def _admit_stored_row(
    raw_row: Mapping[str, Any],
    *,
    store_kind: str,
    artifact: str | None = None,
    key: Mapping[str, Any] | None = None,
    key_bytes: bytes | None = None,
    key_hash: str | None = None,
    admission_keys: Sequence[Mapping[str, Any]] | None = None,
    expected_replay_scope_sha256: str | None = None,
    physics_bucket_key: Mapping[str, Any] | None = None,
    physics_bucket_bytes: bytes | None = None,
    physics_bucket_hash: str | None = None,
    rung_tag: str | None = None,
    rung_key: Mapping[str, Any] | None = None,
    rung_hash: str | None = None,
    output_columns: Sequence[str] | None = None,
    strict_source_validation: bool = False,
    validate_source_provenance: bool = False,
    approved_sources: frozenset[str] = APPROVED_LIVE_VAPOR_SOURCES,
    validate_interpolation_domain: bool = False,
    reader_context_policy: bool = True,
) -> _AdmittedStoredRow | None:
    """Admit one stored row using a single PT1 integrity-check sequence.

    PT1 checks schema and artifact; stored physics-bucket byte type; query
    bucket/ladder identity and scope; requested rung distance shape; key-byte
    type and hashes; canonical key decoding; derived bucket/ladder indexes
    (validating every stored distance before comparison); payload-byte type,
    checksum, and canonical decoding;
    interpolation domain; repair notices and provenance storage types; strict
    source rules; codec and dependency shape; then a non-finite derived
    requested-rung miss.
    """

    if store_kind not in {"pt0", "pt1"}:
        raise ValueError(f"unknown reduced-real stored-row kind: {store_kind!r}")

    if store_kind == "pt0":
        try:
            stored_artifact = raw_row.get("artifact")
            expected_codec_version = (
                _payload_codec_version(stored_artifact)
                if isinstance(stored_artifact, str)
                else None
            )
            if artifact is not None and stored_artifact != artifact:
                if physics_bucket_hash is not None:
                    return None
                raise PT0CacheCollision(
                    f"PT-0 stored artifact mismatch for {artifact}: {key_hash}"
                )
            if physics_bucket_hash is not None and (
                stored_artifact != artifact
                or raw_row.get("physics_bucket_hash") != physics_bucket_hash
            ):
                return None
            stored_key_hash = raw_row.get("key_hash")
            if stored_key_hash is not None and key_hash is not None and (
                stored_key_hash != key_hash
            ):
                raise PT0CacheCollision(
                    f"PT-0 stored key hash mismatch for {artifact}: {key_hash}"
                )
            stored_key = raw_row["key"]
            if not isinstance(stored_key, Mapping):
                raise PT0CacheCollision(
                    f"PT-0 stored key is not a mapping for {artifact}: {key_hash}"
                )
            stored_key_bytes = raw_row.get("key_bytes")
            if isinstance(stored_key_bytes, str):
                stored_key_bytes = stored_key_bytes.encode("utf-8")
            canonical_key_bytes = canonical_json_bytes(stored_key)
            if stored_key_bytes is None:
                stored_key_bytes = canonical_key_bytes
            expected_key_bytes = (
                canonical_json_bytes(key) if key is not None else canonical_key_bytes
            )
            if (
                stored_key_bytes != canonical_key_bytes
                or (
                    key_bytes is not None
                    and (key_bytes != canonical_key_bytes or expected_key_bytes != key_bytes)
                )
            ):
                raise PT0CacheCollision(
                    f"PT-0 stored key bytes mismatch for {artifact}: {key_hash}"
                )
            stored_bucket = canonical_physics_bucket_key_from_replay_key(stored_key)
            stored_bucket_bytes = canonical_json_bytes(stored_bucket)
            stored_bucket_hash = _sha256(stored_bucket_bytes)
            if (
                raw_row.get("physics_bucket_hash") is not None
                and raw_row["physics_bucket_hash"] != stored_bucket_hash
            ):
                raise PT0CacheCollision(
                    f"PT-0 stored physics bucket hash mismatch for {artifact}: {key_hash}"
                )
            payload = raw_row["payload"]
            payload_bytes = _payload_integrity_bytes(payload)
            if raw_row.get("payload_hash") != _sha256(payload_bytes):
                raise PT0CacheCollision(
                    f"PT-0 stored payload bytes mismatch for {artifact}: {key_hash}"
                )
            if validate_interpolation_domain:
                from simulator.reduced_real_cache_interpolation import (
                    interpolation_key_domain_violation,
                )

                if interpolation_key_domain_violation(
                    stored_key,
                ) is not None:
                    return None
            stored_ladder_values = _physics_ladder_values_from_replay_key(
                stored_key
            )
            if physics_bucket_hash is not None:
                if (
                    stored_bucket != _json_ready(physics_bucket_key)
                    or stored_bucket_bytes != physics_bucket_bytes
                    or _sha256(stored_bucket_bytes) != physics_bucket_hash
                ):
                    return None
            if not _payload_codec_stamp_matches(
                expected_codec_version,
                raw_row.get("payload_codec_version"),
            ):
                return _AdmittedStoredRow(
                    dict(raw_row), None, stale_payload_codec=True
                )
            decoded = _decode_payload_for_admission(
                str(stored_artifact),
                payload,
                payload_codec_version=raw_row.get("payload_codec_version"),
                keys=tuple(
                    [stored_key]
                    + [
                        candidate
                        for candidate in (admission_keys or ())
                        if candidate != stored_key
                    ]
                ),
                reader_context_policy=reader_context_policy,
            )
            if decoded is not None:
                decode_repair_notices_json(raw_row.get("repair_notices_json"))
                if reader_context_policy and not _disclosures_known(raw_row):
                    decoded = None
            return _AdmittedStoredRow(
                dict(raw_row), decoded, physics_ladder_values=stored_ladder_values
            )
        except PT1PersistentStoreCorrupt:
            # A memory row's malformed sidecar is a miss; the same bytes in
            # SQLite are an integrity failure with a distinct typed outcome.
            return None
        except PT0CacheCollision:
            raise
        except Exception:
            return None

    try:
        row = dict(raw_row)
        row_keys = set(row)
        if output_columns is not None:
            row = {
                column: row[column] if column in row_keys else None
                for column in output_columns
            }
            row_keys = set(row)
        raw_key_bytes_are_bytes = isinstance(row.get("key_bytes"), bytes)
        raw_payload_bytes_are_bytes = isinstance(row.get("payload_bytes"), bytes)
        row_hash = str(row["key_hash"])
        row_artifact = str(row["artifact"])
        store_schema_version = row["store_schema_version"]
        if store_schema_version not in PT1_SUPPORTED_STORE_SCHEMA_VERSIONS:
            raise PT1PersistentStoreCorrupt(
                "PT-1 row store schema version drift: "
                f"{store_schema_version} != {PT1_STORE_SCHEMA_VERSION}"
            )
        if artifact is not None and row_artifact != artifact:
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row artifact mismatch for {artifact}: {row_hash}"
            )
        stored_bucket_bytes = None
        if row.get("physics_key_bytes") is not None:
            stored_bucket_bytes = _pt1_row_bytes(
                row["physics_key_bytes"],
                field="physics bucket key",
                key_hash=row_hash,
            )

        if stored_bucket_bytes is not None:
            row["physics_key_bytes"] = stored_bucket_bytes

        def verify_key_hashes() -> None:
            if (
                row["key_hash"] != row["key_sha256"]
                or row["key_sha256"] != row_hash
            ):
                raise PT1PersistentStoreCorrupt(
                    f"PT-1 row key hash mismatch for {row_artifact}: {row_hash}"
                )
            if _sha256(row_key_bytes) != row_hash:
                raise PT1PersistentStoreCorrupt(
                    f"PT-1 row key bytes hash mismatch for {row_artifact}: {row_hash}"
                )
            if key_hash is not None and row_hash != key_hash:
                raise PT1PersistentStoreCorrupt(
                    f"PT-1 row key hash mismatch for {row_artifact}: {key_hash}"
                )
            if key_bytes is not None and row_key_bytes != key_bytes:
                raise PT1PersistentStoreCorrupt(
                    f"PT-1 row canonical request bytes mismatch: {row_hash}"
                )

        def verify_canonical_key(row_key: Mapping[str, Any]) -> None:
            if row["key_bytes"] != row_key_bytes:
                raise PT1PersistentStoreCorrupt(
                    f"PT-1 row key bytes changed while reading: {row_hash}"
                )
            if canonical_json_bytes(row_key) != row_key_bytes:
                raise PT1PersistentStoreCorrupt(
                    f"PT-1 row non-canonical request bytes: {row_hash}"
                )
            if key is not None and row_key != _json_ready(key):
                raise PT1PersistentStoreCorrupt(
                    f"PT-1 row canonical request mismatch: {row_hash}"
                )

        def verify_payload_integrity() -> tuple[bytes, Any]:
            payload_bytes = _pt1_row_bytes(
                row["payload_bytes"], field="payload", key_hash=row_hash
            )
            if _sha256(payload_bytes) != row["payload_sha256"]:
                raise PT1PersistentStoreCorrupt(
                    "PT-1 row payload hash mismatch (stored payload bytes): "
                    f"{row_artifact}: {row_hash}"
                )
            payload_value = _pt1_row_json(
                payload_bytes, field="payload", key_hash=row_hash
            )
            if _payload_integrity_bytes(payload_value) != payload_bytes:
                raise PT1PersistentStoreCorrupt(
                    f"PT-1 row non-canonical payload bytes: {row_hash}"
                )
            return payload_bytes, payload_value

        def finite_ladder_distance(
            value: Any,
            tag: str,
            distance_hash: str = row_hash,
        ) -> float:
            if isinstance(value, bool):
                raise PT1PersistentStoreCorrupt(
                    f"PT-1 row {tag} distance is invalid: {distance_hash}"
                )
            try:
                distance = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise PT1PersistentStoreCorrupt(
                    f"PT-1 row {tag} distance is invalid: {distance_hash}"
                ) from exc
            if not math.isfinite(distance):
                raise PT1PersistentStoreCorrupt(
                    f"PT-1 row {tag} distance is invalid: {distance_hash}"
                )
            return distance

        # Checks against the indexed query happen before JSON decoding. This
        # preserves the PT1 integrity ordering for compound corruptions.
        if physics_bucket_hash is not None:
            if row["physics_bucket_schema_version"] != PHYSICS_BUCKET_SCHEMA_VERSION:
                raise PT1PersistentStoreCorrupt(
                    "PT-1 row physics bucket schema drift: "
                    f"{row['physics_bucket_schema_version']} != "
                    f"{PHYSICS_BUCKET_SCHEMA_VERSION}"
                )
            if row["physics_bucket_sha256"] != physics_bucket_hash:
                raise PT1PersistentStoreCorrupt(
                    f"PT-1 row physics bucket hash mismatch: {physics_bucket_hash}"
                )
            if row["replay_scope_sha256"] != _replay_scope_hash(physics_bucket_key):
                raise PT1PersistentStoreCorrupt(
                    f"PT-1 row replay scope hash mismatch: {physics_bucket_hash}"
                )
            if stored_bucket_bytes is None:
                _pt1_row_bytes(
                    row["physics_key_bytes"],
                    field="physics bucket key",
                    key_hash=row_hash,
                )
            if stored_bucket_bytes != physics_bucket_bytes:
                raise PT1PersistentStoreCorrupt(
                    f"PT-1 row physics bucket bytes mismatch: {physics_bucket_hash}"
                )
        if rung_tag is not None:
            assert rung_key is not None and rung_hash is not None
            if row[_physics_ladder_hash_column(rung_tag)] != rung_hash:
                raise PT1PersistentStoreCorrupt(
                    f"PT-1 row {rung_tag} physics bucket hash mismatch: {rung_hash}"
                )
            if row["replay_scope_sha256"] != _replay_scope_hash(rung_key):
                raise PT1PersistentStoreCorrupt(
                    f"PT-1 row {rung_tag} replay scope hash mismatch: {rung_hash}"
                )
            selected_distance = row.get(_physics_ladder_distance_column(rung_tag))
            if selected_distance is None:
                raise PT1PersistentStoreCorrupt(
                    f"PT-1 row {rung_tag} distance is missing: {rung_hash}"
                )
            selected_indexed_distance = finite_ladder_distance(
                selected_distance,
                rung_tag,
                rung_hash,
            )
        else:
            selected_indexed_distance = None
        if expected_replay_scope_sha256 is not None and (
            row["replay_scope_sha256"] != expected_replay_scope_sha256
        ):
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row replay scope hash mismatch: {row_hash}"
            )

        row_key_bytes = _pt1_row_bytes(
            row["key_bytes"], field="key", key_hash=row_hash
        )

        # Query index metadata and key-byte integrity precede key admission.
        verify_key_hashes()

        row_key = _pt1_row_key(row_key_bytes, key_hash=row_hash)
        verify_canonical_key(row_key)

        derived_bucket_key = canonical_physics_bucket_key_from_replay_key(row_key)
        derived_bucket_bytes = canonical_json_bytes(derived_bucket_key)
        derived_bucket_hash = _sha256(derived_bucket_bytes)
        stored_bucket_schema = row.get("physics_bucket_schema_version")
        derived_replay_scope_sha256 = _replay_scope_hash(derived_bucket_key)
        if stored_bucket_schema is not None:
            if stored_bucket_bytes is None:
                _pt1_row_bytes(
                    row["physics_key_bytes"],
                    field="physics bucket key",
                    key_hash=row_hash,
                )
            if (
                stored_bucket_schema != PHYSICS_BUCKET_SCHEMA_VERSION
                or row["physics_bucket_sha256"] != derived_bucket_hash
                or stored_bucket_bytes != derived_bucket_bytes
            ):
                raise PT1PersistentStoreCorrupt(
                    f"PT-1 row exact key does not reproduce physics bucket: {row_hash}"
                )
            if row["replay_scope_sha256"] != derived_replay_scope_sha256:
                raise PT1PersistentStoreCorrupt(
                    f"PT-1 row exact key replay scope mismatch: {row_hash}"
                )
        elif (
            expected_replay_scope_sha256 is not None
            and expected_replay_scope_sha256 != derived_replay_scope_sha256
        ):
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row exact key replay scope mismatch: {row_hash}"
            )
        if physics_bucket_hash is not None and (
            derived_bucket_hash != physics_bucket_hash
            or derived_bucket_bytes != physics_bucket_bytes
        ):
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row exact key does not reproduce physics bucket: {physics_bucket_hash}"
            )
        ladder_values = _physics_ladder_values_from_replay_key(row_key)
        nonfinite_requested_rung = False
        if rung_tag is not None:
            assert rung_key is not None and rung_hash is not None
            derived_rung_key = canonical_physics_ladder_bucket_key_from_replay_key(
                row_key,
                rung_tag,
            )
            if (
                derived_rung_key != _json_ready(rung_key)
                or ladder_values[rung_tag]["sha256"] != rung_hash
            ):
                raise PT1PersistentStoreCorrupt(
                    f"PT-1 row exact key does not reproduce {rung_tag} index: {rung_hash}"
                )
            derived_selected_distance = float(ladder_values[rung_tag]["distance"])
            if math.isfinite(derived_selected_distance):
                if selected_indexed_distance != derived_selected_distance:
                    raise PT1PersistentStoreCorrupt(
                        f"PT-1 row {rung_tag} distance mismatch: {rung_hash}"
                    )
            else:
                nonfinite_requested_rung = True

        for stored_rung_tag, _sig_figs in PHYSICS_BUCKET_ALL_LADDER_RUNGS:
            if stored_rung_tag == rung_tag:
                continue
            hash_column = _physics_ladder_hash_column(stored_rung_tag)
            distance_column = _physics_ladder_distance_column(stored_rung_tag)
            stored_rung_hash = row.get(hash_column)
            stored_distance = row.get(distance_column)
            if stored_bucket_schema is not None and stored_rung_hash is None:
                raise PT1PersistentStoreCorrupt(
                    f"PT-1 row {stored_rung_tag} physics bucket hash is missing: {row_hash}"
                )
            if stored_bucket_schema is not None and stored_distance is None:
                raise PT1PersistentStoreCorrupt(
                    f"PT-1 row {stored_rung_tag} distance is missing: {row_hash}"
                )
            if stored_rung_hash is not None and (
                stored_rung_hash != ladder_values[stored_rung_tag]["sha256"]
            ):
                raise PT1PersistentStoreCorrupt(
                    f"PT-1 row exact key does not reproduce {stored_rung_tag} hash: {row_hash}"
                )
            if stored_distance is not None:
                row_distance = finite_ladder_distance(
                    stored_distance, stored_rung_tag
                )
                derived_distance = float(ladder_values[stored_rung_tag]["distance"])
                if math.isfinite(derived_distance) and row_distance != derived_distance:
                    raise PT1PersistentStoreCorrupt(
                        f"PT-1 row {stored_rung_tag} distance mismatch: {row_hash}"
                    )

        row_payload_bytes, row_payload = verify_payload_integrity()
        if validate_interpolation_domain:
            from simulator.reduced_real_cache_interpolation import (
                interpolation_key_domain_violation,
            )

            domain_violation = interpolation_key_domain_violation(
                row_key,
            )
            if domain_violation is not None:
                section = (
                    "composition"
                    if domain_violation == "composition_mol_fraction"
                    else "controls"
                )
                raise PT1PersistentStoreCorrupt(
                    "PT-1 row invalid stored equilibrium replay key "
                    f"{section} ({domain_violation}): {row_hash}"
                )
        if rung_tag is not None and _ladder_indexability(
            row_key,
            rung_tag=rung_tag,
        ) is not None:
            nonfinite_requested_rung = True
        all_index_columns = (
            "physics_bucket_schema_version",
            "physics_bucket_sha256",
            "replay_scope_sha256",
            "physics_key_bytes",
            *(
                column
                for derived_rung_tag, _sig_figs in PHYSICS_BUCKET_ALL_LADDER_RUNGS
                for column in (
                    _physics_ladder_hash_column(derived_rung_tag),
                    _physics_ladder_distance_column(derived_rung_tag),
                )
            ),
        )
        if output_columns is not None and (
            store_schema_version != PT1_STORE_SCHEMA_VERSION
            or any(row.get(column) is None for column in all_index_columns)
        ):
            row["store_schema_version"] = PT1_STORE_SCHEMA_VERSION
            row["physics_bucket_schema_version"] = PHYSICS_BUCKET_SCHEMA_VERSION
            row["physics_bucket_sha256"] = derived_bucket_hash
            row["replay_scope_sha256"] = _replay_scope_hash(derived_bucket_key)
            row["physics_key_bytes"] = sqlite3.Binary(derived_bucket_bytes)
            for derived_rung_tag, values in ladder_values.items():
                row[_physics_ladder_hash_column(derived_rung_tag)] = values[
                    "sha256"
                ]
                row[_physics_ladder_distance_column(derived_rung_tag)] = values[
                    "distance"
                ]

        repair_notices_json = _row_repair_notices(row)
        validate_repair_notices_json(repair_notices_json)
        law_disclosures = _law_disclosures_from_json(
            row.get("law_disclosures_json")
        )
        invalid_types = [
            field
            for field in _PT1_SOURCE_REQUIRED_TEXT_FIELDS
            if not isinstance(row.get(field), str)
        ]
        for field in ("corpus_version", "engine_version"):
            if row.get(field) is not None and not isinstance(row[field], str):
                invalid_types.append(field)
        if type(row.get("git_dirty")) is not int:
            invalid_types.append("git_dirty")
        if strict_source_validation:
            if not raw_key_bytes_are_bytes:
                invalid_types.append("key_bytes")
            if not raw_payload_bytes_are_bytes:
                invalid_types.append("payload_bytes")
            if row.get("payload_codec_version") is not None and type(
                row["payload_codec_version"]
            ) is not int:
                invalid_types.append("payload_codec_version")
            if row.get("repair_notices_json") is not None and not isinstance(
                row["repair_notices_json"], str
            ):
                invalid_types.append("repair_notices_json")
        if invalid_types:
            raise PT1PersistentStoreCorrupt(
                "PT-1 cache source row invalid storage types: "
                f"fields={','.join(invalid_types)}"
            )

        provenance_mismatches: list[str] = []
        try:
            datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError):
            provenance_mismatches.append("created_at")
        if row["git_dirty"] not in (0, 1):
            provenance_mismatches.append("git_dirty")
        if provenance_mismatches:
            raise PT1PersistentStoreCorrupt(
                "PT-1 cache source row canonical/provenance mismatch: "
                f"fields={','.join(provenance_mismatches)}"
            )
        # Copy paths consume normalized bytes and validated provenance values.
        row["key_bytes"] = row_key_bytes
        row["payload_bytes"] = row_payload_bytes

        if strict_source_validation:
            if row_artifact == "freeze_gate_curve" and not isinstance(
                row_payload, Mapping
            ):
                raise PT1PersistentStoreCorrupt(
                    "PT-1 cache source gate curve payload must be a mapping: "
                    f"{row_hash}"
                )
            if validate_source_provenance:
                source_mismatches: list[str] = []
                if "artifact" in row_key and row_key["artifact"] != row_artifact:
                    source_mismatches.append("artifact")
                if source_mismatches:
                    raise PT1PersistentStoreCorrupt(
                        "PT-1 cache source row canonical/provenance mismatch: "
                        f"fields={','.join(source_mismatches)}"
                    )
            validate_reduced_real_equilibrium_record_key(
                row_artifact,
                row_key,
                row_payload,
            )
            if isinstance(row_payload, Mapping):
                assert_strict_vapor_pt1_row(
                    artifact=row_artifact,
                    key=row_key,
                    key_hash=row_hash,
                    payload=row_payload,
                    context=f"PT-1 cache shard {row_artifact}:{row_hash}",
                    approved_sources=approved_sources,
                )
        payload_codec_version = row.get("payload_codec_version")
        expected_codec_version = _payload_codec_version(row_artifact)
        if not _payload_codec_stamp_matches(
            expected_codec_version,
            payload_codec_version,
        ):
            return _AdmittedStoredRow(
                {
                    "artifact": row_artifact,
                    "key": copy.deepcopy(dict(row_key)),
                    "key_hash": row_hash,
                    "payload": (
                        copy.deepcopy(dict(row_payload))
                        if isinstance(row_payload, Mapping)
                        else copy.deepcopy(row_payload)
                    ),
                    "repair_notices_json": repair_notices_json,
                    "law_disclosures": law_disclosures,
                },
                None,
                stored_columns=row,
                stale_payload_codec=True,
            )
        if row_artifact == "equilibrium_post_record":
            if (
                not isinstance(row_key.get("namespace_id"), str)
                or not row_key["namespace_id"]
                or (
                    row_key.get("vapor_pressure_provider_selection") is not None
                    and not isinstance(
                        row_key.get("vapor_pressure_provider_selection"),
                        (str, Mapping),
                    )
                )
                or (
                    "sulfur_side" in row_key
                    and not isinstance(row_key["sulfur_side"], Mapping)
                )
                or (
                    "model" in row_key
                    and not isinstance(row_key["model"], Mapping)
                )
            ):
                raise PT1PersistentStoreCorrupt(
                    "PT-1 row invalid stored equilibrium replay key dependency "
                    f"fields: {row_hash}"
                )

        entry = {
            "artifact": row_artifact,
            "key": copy.deepcopy(dict(row_key)),
            "key_hash": row_hash,
            "key_bytes": row_key_bytes.decode("utf-8"),
            "payload": (
                copy.deepcopy(dict(row_payload))
                if isinstance(row_payload, Mapping)
                else copy.deepcopy(row_payload)
            ),
            "payload_hash": row["payload_sha256"],
            "payload_codec_version": payload_codec_version,
            "repair_notices_json": repair_notices_json,
            "law_disclosures": law_disclosures,
            "cache_state": "live_fill",
            "physics_bucket_key": derived_bucket_key,
            "physics_bucket_bytes": derived_bucket_bytes,
            "physics_bucket_hash": derived_bucket_hash,
        }
        if not isinstance(row_payload, Mapping):
            return _AdmittedStoredRow(entry, None, row, ladder_values)

        decode_keys = [row_key]
        if key is not None and _json_ready(key) != row_key:
            decode_keys.append(_json_ready(key))
        for admission_key in admission_keys or ():
            normalized_key = _json_ready(admission_key)
            if normalized_key not in decode_keys:
                decode_keys.append(normalized_key)
        decoded = _decode_payload_for_admission(
            row_artifact,
            row_payload,
            payload_codec_version=payload_codec_version,
            keys=tuple(decode_keys),
            reader_context_policy=reader_context_policy,
        )
        if nonfinite_requested_rung:
            decoded = None
        return _AdmittedStoredRow(entry, decoded, row, ladder_values)
    except (sqlite3.Error, PT1PersistentStoreCorrupt, GrindSourceGateError):
        raise
    except Exception as exc:
        row_hash = key_hash
        try:
            row_hash = str(raw_row.get("key_hash", row_hash))
        except Exception:
            pass
        raise PT1PersistentStoreCorrupt(
            f"PT-1 row contains invalid stored fields: {row_hash}: {exc}"
        ) from exc


def _gate_row_for_write(raw_row: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one final PT1 row's key and index integrity before writing.

    Private ``_write_*`` values carry the caller's inputs to this boundary so
    that serialization failures are converted to the same typed write refusal
    as failures discovered while deriving the stored indexes. Payload replay
    shape remains reader admission policy; extrapolated gate identity is the
    one payload-to-key identity checked here.
    """

    try:
        row = dict(raw_row)
    except Exception as exc:
        raise UncacheablePayload("unknown", "") from exc
    expected_key = row.pop("_write_key", None)
    replace_key = row.pop("_write_replace_key", False)
    expected_payload = row.pop("_write_payload", None)
    input_bucket_key = row.pop("_write_physics_bucket_key", None)
    input_bucket_bytes = row.pop("_write_physics_bucket_bytes", None)
    input_bucket_hash = row.pop("_write_physics_bucket_hash", None)
    rebuild_indexes = row.pop("_write_rebuild_indexes", False)
    artifact = row.get("artifact")
    artifact_name = artifact if isinstance(artifact, str) else "unknown"
    row_columns = set(row)
    legacy_layout = (
        (
            _payload_codec_version(artifact_name) is not None
            and row.get("payload_codec_version") is None
        )
        or not {
            "artifact",
            "payload_codec_version",
            "payload_bytes",
            "payload_sha256",
        }.issubset(row_columns)
    )
    try:
        key_hash = str(row.get("key_hash", ""))
    except Exception as exc:
        raise UncacheablePayload(artifact_name, "") from exc
    stage = "key"

    try:
        if type(replace_key) is not bool or type(rebuild_indexes) is not bool:
            raise TypeError("invalid PT1 write-gate options")
        if not legacy_layout and not isinstance(artifact, str):
            raise TypeError("PT1 write row artifact must be a string")

        if expected_key is not None:
            if not isinstance(expected_key, Mapping):
                raise TypeError("caller key must be a mapping")
            if not legacy_layout:
                indexability_error = _ladder_indexability(expected_key)
                if indexability_error is not None:
                    raise indexability_error
            canonical_key_bytes = canonical_json_bytes(expected_key)
            if replace_key:
                key_hash = _sha256(canonical_key_bytes)
                row["key_hash"] = key_hash
                if "key_sha256" in row_columns:
                    row["key_sha256"] = key_hash
                row["key_bytes"] = sqlite3.Binary(canonical_key_bytes)
                if not legacy_layout:
                    row["store_schema_version"] = PT1_STORE_SCHEMA_VERSION
                stored_key = dict(_json_ready(expected_key))
            else:
                supplied_key_bytes = _pt1_row_bytes(
                    row.get("key_bytes"), field="key", key_hash=key_hash
                )
                if supplied_key_bytes != canonical_key_bytes:
                    raise UncacheableKey(
                        PHYSICS_BUCKET_ALL_LADDER_RUNGS[0][0],
                        "write_candidate_key_bytes",
                    )
                stored_key = _pt1_row_key(
                    supplied_key_bytes, key_hash=key_hash
                )
        else:
            supplied_key_bytes = _pt1_row_bytes(
                row.get("key_bytes"), field="key", key_hash=key_hash
            )
            stored_key = _pt1_row_key(supplied_key_bytes, key_hash=key_hash)
            canonical_key_bytes = canonical_json_bytes(stored_key)

        supplied_key_bytes = _pt1_row_bytes(
            row.get("key_bytes"), field="key", key_hash=key_hash
        )
        if supplied_key_bytes != canonical_key_bytes:
            raise UncacheableKey(
                PHYSICS_BUCKET_ALL_LADDER_RUNGS[0][0],
                "write_candidate_key_bytes",
            )
        key_hash = _sha256(canonical_key_bytes)
        if row.get("key_hash") != key_hash or (
            "key_sha256" in row_columns and row.get("key_sha256") != key_hash
        ):
            raise UncacheableKey(
                PHYSICS_BUCKET_ALL_LADDER_RUNGS[0][0],
                "write_candidate_key_hash",
            )

        if legacy_layout:
            index_columns = {
                "physics_bucket_schema_version",
                "physics_bucket_sha256",
                "replay_scope_sha256",
                "physics_key_bytes",
                *(
                    column
                    for rung_tag, _sig_figs in PHYSICS_BUCKET_ALL_LADDER_RUNGS
                    for column in (
                        _physics_ladder_hash_column(rung_tag),
                        _physics_ladder_distance_column(rung_tag),
                    )
                ),
            }
            if rebuild_indexes and index_columns.intersection(row_columns):
                bucket_key = canonical_physics_bucket_key_from_replay_key(
                    stored_key
                )
                bucket_bytes = canonical_json_bytes(bucket_key)
                ladder_values = _physics_ladder_values_from_replay_key(
                    stored_key
                )
                legacy_indexes = {
                    "physics_bucket_schema_version": PHYSICS_BUCKET_SCHEMA_VERSION,
                    "physics_bucket_sha256": _sha256(bucket_bytes),
                    "replay_scope_sha256": _replay_scope_hash(bucket_key),
                    "physics_key_bytes": sqlite3.Binary(bucket_bytes),
                }
                for rung_tag, values in ladder_values.items():
                    legacy_indexes[_physics_ladder_hash_column(rung_tag)] = values[
                        "sha256"
                    ]
                    legacy_indexes[_physics_ladder_distance_column(rung_tag)] = values[
                        "distance"
                    ]
                for column in index_columns.intersection(row_columns):
                    row[column] = legacy_indexes[column]
            return row

        indexability_error = _ladder_indexability(stored_key)
        if indexability_error is not None:
            raise indexability_error

        derived_bucket_key = canonical_physics_bucket_key_from_replay_key(
            stored_key
        )
        bucket_key = (
            derived_bucket_key
            if input_bucket_key is None
            else input_bucket_key
        )
        canonical_bucket_bytes = canonical_json_bytes(bucket_key)
        derived_bucket_bytes = canonical_json_bytes(derived_bucket_key)
        if canonical_bucket_bytes != derived_bucket_bytes:
            raise UncacheableKey(
                PHYSICS_BUCKET_ALL_LADDER_RUNGS[0][0],
                "write_candidate_physics_bucket_key",
            )
        if input_bucket_bytes is not None and bytes(input_bucket_bytes) != canonical_bucket_bytes:
            raise UncacheableKey(
                PHYSICS_BUCKET_ALL_LADDER_RUNGS[0][0],
                "write_candidate_physics_bucket_bytes",
            )
        bucket_hash = _sha256(canonical_bucket_bytes)
        if input_bucket_hash is not None and input_bucket_hash != bucket_hash:
            raise UncacheableKey(
                PHYSICS_BUCKET_ALL_LADDER_RUNGS[0][0],
                "write_candidate_physics_bucket_hash",
            )

        ladder_values = _physics_ladder_values_from_replay_key(stored_key)
        rebuild_indexes = rebuild_indexes or (
            row.get("store_schema_version") != PT1_STORE_SCHEMA_VERSION
            or row.get("physics_bucket_schema_version") is None
        )
        if rebuild_indexes:
            row["store_schema_version"] = PT1_STORE_SCHEMA_VERSION
            row["physics_bucket_schema_version"] = PHYSICS_BUCKET_SCHEMA_VERSION
            row["physics_bucket_sha256"] = bucket_hash
            row["replay_scope_sha256"] = _replay_scope_hash(bucket_key)
            row["physics_key_bytes"] = sqlite3.Binary(canonical_bucket_bytes)
            for rung_tag, values in ladder_values.items():
                row[_physics_ladder_hash_column(rung_tag)] = values["sha256"]
                row[_physics_ladder_distance_column(rung_tag)] = values[
                    "distance"
                ]
        else:
            stored_bucket_bytes = _pt1_row_bytes(
                row.get("physics_key_bytes"),
                field="physics bucket key",
                key_hash=key_hash,
            )
            if stored_bucket_bytes != canonical_bucket_bytes:
                raise UncacheableKey(
                    PHYSICS_BUCKET_ALL_LADDER_RUNGS[0][0],
                    "write_candidate_physics_bucket_bytes",
                )
            if (
                row.get("physics_bucket_schema_version")
                != PHYSICS_BUCKET_SCHEMA_VERSION
                or row.get("physics_bucket_sha256") != bucket_hash
                or row.get("replay_scope_sha256") != _replay_scope_hash(bucket_key)
            ):
                raise UncacheableKey(
                    PHYSICS_BUCKET_ALL_LADDER_RUNGS[0][0],
                    "write_candidate_physics_bucket_index",
                )
            for rung_tag, values in ladder_values.items():
                indexed_distance = row.get(
                    _physics_ladder_distance_column(rung_tag)
                )
                if (
                    row.get(_physics_ladder_hash_column(rung_tag))
                    != values["sha256"]
                    or isinstance(indexed_distance, bool)
                    or not isinstance(indexed_distance, (int, float))
                    or not math.isfinite(float(indexed_distance))
                    or float(indexed_distance) != float(values["distance"])
                ):
                    raise UncacheableKey(rung_tag, "write_candidate_ladder_index")

        stage = "payload"
        payload_bytes = _pt1_row_bytes(
            row.get("payload_bytes"), field="payload", key_hash=key_hash
        )
        payload_value = _pt1_row_json(
            payload_bytes, field="payload", key_hash=key_hash
        )
        canonical_payload_bytes = _payload_integrity_bytes(payload_value)
        if (
            canonical_payload_bytes != payload_bytes
            or row.get("payload_sha256") != _sha256(payload_bytes)
        ):
            raise UncacheablePayload(artifact_name, key_hash)
        if expected_payload is not None and (
            canonical_json_bytes(expected_payload) != payload_bytes
        ):
            raise UncacheablePayload(artifact_name, key_hash)
        validate_repair_notices_json(row.get("repair_notices_json"))

        if artifact_name == "freeze_gate_curve":
            curve_payload = (
                payload_value.get("curve")
                if isinstance(payload_value, Mapping)
                else None
            )
            if isinstance(curve_payload, Mapping):
                identity_rejection = _gate_curve_identity_rejection(
                    curve_payload,
                    (stored_key,),
                    reader_context_policy=False,
                    write_integrity_only=True,
                )
                if identity_rejection is not None:
                    raise UncacheablePayload(artifact_name, key_hash)
        return row
    except (UncacheableKey, UncacheablePayload):
        raise
    except Exception as exc:
        if stage == "key":
            raise UncacheableKey(
                PHYSICS_BUCKET_ALL_LADDER_RUNGS[0][0],
                "write_candidate_index",
            ) from exc
        raise UncacheablePayload(artifact_name, key_hash) from exc


class PT1PersistentEquilibriumStore:
    """Content-addressed SQLite store for PT-0 exact reduced-real payloads."""

    def __init__(
        self,
        db_path: Path,
        *,
        read_only_base_db_path: Path | None = None,
        shard_busy_timeout_ms: float = DEFAULT_SHARD_BUSY_TIMEOUT_MS,
        strict_vapor_gate: bool = False,
        control_quantization: ControlQuantization | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.read_only_base_db_path = (
            Path(read_only_base_db_path)
            if read_only_base_db_path is not None
            else None
        )
        self._shard_busy_timeout_ms = float(shard_busy_timeout_ms)
        self.strict_vapor_gate = bool(strict_vapor_gate)
        self._control_quantization = (
            control_quantization or _PRODUCTION_QUANTIZATION
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Epoch provenance is captured when the store opens. Rows written later
        # intentionally keep this value even if the worktree changes mid-epoch.
        self._epoch_git_dirty = _git_dirty()
        with self._connect() as conn:
            self._initialize(conn)

    @property
    def control_quantization(self) -> ControlQuantization:
        return self._control_quantization

    def put(
        self,
        *,
        artifact: str,
        key: Mapping[str, Any],
        key_bytes: bytes,
        key_hash: str,
        payload: Mapping[str, Any],
        payload_bytes: bytes,
        payload_hash: str,
        repair_notices_json: str | None = "[]",
        engine_version_provenance: str | None = None,
        physics_bucket_key: Mapping[str, Any] | None = None,
        physics_bucket_bytes: bytes | None = None,
        physics_bucket_hash: str | None = None,
        law_disclosures: Any = _FROM_COMPUTATION,
    ) -> None:
        if not isinstance(key, Mapping):
            raise UncacheableKey(
                PHYSICS_BUCKET_ALL_LADDER_RUNGS[0][0],
                "write_candidate_key",
            )
        indexability_error = _ladder_indexability(key)
        if indexability_error is not None:
            raise indexability_error
        try:
            key_bytes = _pt1_row_bytes(
                key_bytes,
                field="key",
                key_hash=key_hash,
            )
        except PT1PersistentStoreCorrupt as exc:
            raise UncacheableKey(
                PHYSICS_BUCKET_ALL_LADDER_RUNGS[0][0],
                "write_candidate_key_bytes",
            ) from exc
        try:
            payload_bytes = _pt1_row_bytes(
                payload_bytes,
                field="payload",
                key_hash=key_hash,
            )
        except PT1PersistentStoreCorrupt as exc:
            raise UncacheablePayload(artifact, key_hash) from exc
        payload_codec_version = _payload_codec_version(artifact)
        incoming_disclosures = _resolve_law_disclosures(law_disclosures)

        insert_columns = (
            "key_hash",
            "artifact",
            "store_schema_version",
            "request_schema_version",
            "key_sha256",
            "payload_sha256",
            "payload_codec_version",
            "key_bytes",
            "payload_bytes",
            "code_version",
            "corpus_version",
            "engine_version",
            "data_digests_json",
            "repair_notices_json",
            "physics_bucket_schema_version",
            "physics_bucket_sha256",
            "replay_scope_sha256",
            "physics_key_bytes",
            "physics_bucket_h40_sha256",
            "physics_bucket_h40_distance",
            "physics_bucket_h30_sha256",
            "physics_bucket_h30_distance",
            "physics_bucket_h40c_sha256",
            "physics_bucket_h40c_distance",
            "physics_bucket_h30c_sha256",
            "physics_bucket_h30c_distance",
            "created_at",
            "git_dirty",
        )
        candidate_row: dict[str, Any] = dict(
            zip(
                insert_columns,
                (
                    key_hash,
                    artifact,
                    PT1_STORE_SCHEMA_VERSION,
                    SCHEMA_VERSION,
                    key_hash,
                    payload_hash,
                    payload_codec_version,
                    sqlite3.Binary(key_bytes),
                    sqlite3.Binary(payload_bytes),
                    _code_version(),
                    None,
                    _none_or_str(engine_version_provenance),
                    "{}",
                    repair_notices_json,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    self._epoch_git_dirty,
                ),
            )
        )
        candidate_row["law_disclosures_json"] = _law_disclosures_json(
            incoming_disclosures
        )
        candidate_row.update(
            {
                "_write_key": key,
                "_write_payload": payload,
                "_write_physics_bucket_key": physics_bucket_key,
                "_write_physics_bucket_bytes": physics_bucket_bytes,
                "_write_physics_bucket_hash": physics_bucket_hash,
                "_write_rebuild_indexes": True,
            }
        )
        candidate_row = _gate_row_for_write(candidate_row)
        physics_bucket_bytes = bytes(candidate_row["physics_key_bytes"])
        physics_bucket_hash = str(candidate_row["physics_bucket_sha256"])
        physics_bucket_key = _pt1_row_json(
            physics_bucket_bytes,
            field="physics bucket key",
            key_hash=key_hash,
        )

        with self._connect() as conn:
            self._initialize(conn)
            existing = self._fetch(conn, key_hash)
            insert_source = None
            effective_notices_json = repair_notices_json
            existing_admission = None
            if existing is not None:
                existing_admission = _admit_stored_row(
                    existing,
                    store_kind="pt1",
                    artifact=artifact,
                    key=key,
                    key_bytes=key_bytes,
                    key_hash=key_hash,
                )
            if existing is not None:
                assert existing_admission is not None
                entry = existing_admission.entry
                existing_decoded = existing_admission.decoded_payload
                existing_fields = existing_admission.stored_columns
                assert existing_fields is not None
                stale_payload = existing_decoded is None
                if not stale_payload and (
                    entry["payload_hash"] != payload_hash
                    or canonical_json_bytes(entry["payload"]) != payload_bytes
                ):
                    raise UncacheablePayload(artifact, key_hash)
                existing_notices_json = entry["repair_notices_json"]
                effective_notices_json = monotonic_repair_notices_json(
                    existing_notices_json,
                    repair_notices_json,
                )
                local_row = conn.execute(
                    f"SELECT 1 FROM {PT1_EQUILIBRIUM_TABLE} WHERE key_hash = ?",
                    (key_hash,),
                ).fetchone()
                if local_row is not None:
                    write_disclosures = (
                        incoming_disclosures
                        if stale_payload
                        else _prefer_law_disclosures(
                            entry["law_disclosures"],
                            incoming_disclosures,
                        )
                    )
                    candidate_row = dict(existing_fields)
                    candidate_row["repair_notices_json"] = effective_notices_json
                    if stale_payload:
                        candidate_row["payload_sha256"] = payload_hash
                        candidate_row["payload_bytes"] = payload_bytes
                        candidate_row["payload_codec_version"] = payload_codec_version
                    rebuild_indexes = (
                        existing_fields.get("physics_bucket_sha256") is None
                        or any(
                            existing_fields.get(_physics_ladder_hash_column(rung_tag))
                            is None
                            for rung_tag, _sig_figs in PHYSICS_BUCKET_ALL_LADDER_RUNGS
                        )
                    )
                    candidate_row["law_disclosures_json"] = _law_disclosures_json(
                        write_disclosures
                    )
                    candidate_row.update(
                        {
                            "_write_key": key,
                            "_write_payload": payload,
                            "_write_physics_bucket_key": physics_bucket_key,
                            "_write_physics_bucket_bytes": physics_bucket_bytes,
                            "_write_physics_bucket_hash": physics_bucket_hash,
                            "_write_rebuild_indexes": rebuild_indexes,
                        }
                    )
                    candidate_row = _gate_row_for_write(candidate_row)
                    if stale_payload:
                        conn.execute(
                            f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
                            "SET payload_sha256 = ?, payload_bytes = ?, "
                            "payload_codec_version = ?, "
                            "repair_notices_json = ? WHERE key_hash = ?",
                            (
                                payload_hash,
                                sqlite3.Binary(payload_bytes),
                                payload_codec_version,
                                effective_notices_json,
                                key_hash,
                            ),
                        )
                    if effective_notices_json != existing_notices_json:
                        conn.execute(
                            f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
                            "SET repair_notices_json = ? WHERE key_hash = ?",
                            (effective_notices_json, key_hash),
                        )
                    self._update_physics_bucket_columns(
                        conn,
                        key_hash=key_hash,
                        physics_bucket_key=physics_bucket_key,
                        physics_bucket_bytes=bytes(
                            candidate_row["physics_key_bytes"]
                        ),
                        physics_bucket_hash=str(
                            candidate_row["physics_bucket_sha256"]
                        ),
                        ladder_values={
                            rung_tag: {
                                "sha256": candidate_row[
                                    _physics_ladder_hash_column(rung_tag)
                                ],
                                "distance": candidate_row[
                                    _physics_ladder_distance_column(rung_tag)
                                ],
                            }
                            for rung_tag, _sig_figs in PHYSICS_BUCKET_ALL_LADDER_RUNGS
                        },
                    )
                    self._write_law_disclosures(conn, key_hash, write_disclosures)
                    return
                if stale_payload:
                    insert_source = None
                elif effective_notices_json == existing_notices_json:
                    # A read-only row with no disclosure field is not a usable
                    # hit. Copy it locally once this computation measured one.
                    base_disclosures = entry["law_disclosures"]
                    preferred = _prefer_law_disclosures(
                        base_disclosures,
                        incoming_disclosures,
                    )
                    if base_disclosures is not None or preferred is None:
                        return
                    insert_source = existing_fields
                else:
                    insert_source = existing_fields
            source = insert_source
            stored = (
                entry["law_disclosures"]
                if existing is not None and not stale_payload
                else None
            )
            write_disclosures = _prefer_law_disclosures(
                stored,
                incoming_disclosures,
            )
            if source is not None:
                for field in (
                    "request_schema_version",
                    "code_version",
                    "corpus_version",
                    "engine_version",
                    "data_digests_json",
                    "created_at",
                    "git_dirty",
                ):
                    candidate_row[field] = source[field]
                candidate_row["key_bytes"] = source["key_bytes"]
                candidate_row["payload_bytes"] = source["payload_bytes"]
                candidate_row["payload_sha256"] = source["payload_sha256"]
            candidate_row["repair_notices_json"] = effective_notices_json
            candidate_row["law_disclosures_json"] = _law_disclosures_json(
                write_disclosures
            )
            candidate_row.update(
                {
                    "_write_key": key,
                    "_write_payload": payload,
                    "_write_physics_bucket_key": physics_bucket_key,
                    "_write_physics_bucket_bytes": physics_bucket_bytes,
                    "_write_physics_bucket_hash": physics_bucket_hash,
                }
            )
            candidate_row = _gate_row_for_write(candidate_row)
            insert_values = tuple(candidate_row[column] for column in insert_columns)
            columns_sql = ", ".join(insert_columns)
            values_sql = ", ".join("?" for _ in insert_columns)
            conn.execute(
                f"INSERT INTO {PT1_EQUILIBRIUM_TABLE} ({columns_sql}) "
                f"VALUES ({values_sql})",
                insert_values,
            )
            self._write_law_disclosures(conn, key_hash, write_disclosures)

    def _write_law_disclosures(
        self,
        conn: sqlite3.Connection,
        key_hash: str,
        disclosures: Any,
    ) -> None:
        if not self._table_has_column(
            conn, PT1_EQUILIBRIUM_TABLE, "law_disclosures_json"
        ):
            return
        encoded = _law_disclosures_json(disclosures)
        conn.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
            "SET law_disclosures_json = ? WHERE key_hash = ?",
            (encoded, key_hash),
        )

    def get(
        self,
        *,
        artifact: str,
        key: Mapping[str, Any],
        key_bytes: bytes,
        key_hash: str,
    ) -> dict[str, Any] | None:
        admitted = self._get_with_decoded(
            artifact=artifact,
            key=key,
            key_bytes=key_bytes,
            key_hash=key_hash,
        )
        return None if admitted is None else admitted[0]

    def _get_with_decoded(
        self,
        *,
        artifact: str,
        key: Mapping[str, Any],
        key_bytes: bytes,
        key_hash: str,
    ) -> tuple[dict[str, Any], Any] | None:
        if _ladder_indexability(key) is not None:
            return None
        with self._connect() as conn:
            self._initialize(conn)
            for row in self._fetch_candidates(conn, key_hash):
                entry, decoded = self._entry_from_row(
                    row,
                    artifact=artifact,
                    key=key,
                    key_bytes=key_bytes,
                    key_hash=key_hash,
                    return_decoded=True,
                )
                if decoded is None:
                    continue
                return entry, decoded
        return None

    def get_by_physics_bucket(
        self,
        *,
        artifact: str,
        physics_bucket_key: Mapping[str, Any],
        physics_bucket_bytes: bytes,
        physics_bucket_hash: str,
        query_key: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return self._get_by_physics_bucket(
            artifact=artifact,
            physics_bucket_key=physics_bucket_key,
            physics_bucket_bytes=physics_bucket_bytes,
            physics_bucket_hash=physics_bucket_hash,
            query_key=query_key,
        )

    def _get_by_physics_bucket(
        self,
        *,
        artifact: str,
        physics_bucket_key: Mapping[str, Any],
        physics_bucket_bytes: bytes,
        physics_bucket_hash: str,
        query_key: Mapping[str, Any] | None = None,
        return_decoded: bool = False,
    ) -> Any | None:
        if query_key is not None and _ladder_indexability(query_key) is not None:
            return None
        query = f"""
            SELECT *
            FROM {{table}}
            WHERE artifact = ?
              AND physics_bucket_schema_version = ?
              AND physics_bucket_sha256 = ?
              AND replay_scope_sha256 = ?
            ORDER BY key_hash
            """
        params = (
            artifact,
            PHYSICS_BUCKET_SCHEMA_VERSION,
            physics_bucket_hash,
            _replay_scope_hash(physics_bucket_key),
        )
        with self._connect() as conn:
            self._initialize(conn)
            tables = [PT1_EQUILIBRIUM_TABLE]
            read_only_table = self._read_only_equilibrium_table(conn)
            if read_only_table is not None:
                tables.append(read_only_table)
            for table in tables:
                rows = conn.execute(query.format(table=table), params)
                for row in rows:
                    entry, decoded = self._entry_from_physics_bucket_row(
                        row,
                        artifact=artifact,
                        physics_bucket_key=physics_bucket_key,
                        physics_bucket_bytes=physics_bucket_bytes,
                        physics_bucket_hash=physics_bucket_hash,
                        query_key=query_key,
                    )
                    if decoded is not None:
                        return (entry, decoded) if return_decoded else entry
            return None

    def list_interpolation_candidates(
        self,
        *,
        artifact: str,
        replay_scope_sha256: str,
        exclude_key_hash: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._list_interpolation_candidates(
            artifact=artifact,
            replay_scope_sha256=replay_scope_sha256,
            exclude_key_hash=exclude_key_hash,
        )

    def _list_interpolation_candidates(
        self,
        *,
        artifact: str,
        replay_scope_sha256: str,
        exclude_key_hash: str | None = None,
        query_key: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if query_key is not None and _ladder_indexability(query_key) is not None:
            return []
        query = f"""
            SELECT *
            FROM {{table}}
            WHERE artifact = ?
              AND replay_scope_sha256 = ?
            ORDER BY key_hash
            """
        params = (artifact, replay_scope_sha256)
        with self._connect() as conn:
            self._initialize(conn)
            candidates: list[dict[str, Any]] = []
            seen_hashes: set[str] = set()
            tables = [PT1_EQUILIBRIUM_TABLE]
            read_only_table = self._read_only_equilibrium_table(conn)
            if read_only_table is not None:
                tables.append(read_only_table)
            for table in tables:
                rows = conn.execute(query.format(table=table), params)
                for row in rows:
                    row_hash = str(row["key_hash"])
                    if exclude_key_hash is not None and row_hash == exclude_key_hash:
                        continue
                    if row_hash in seen_hashes:
                        continue
                    seen_hashes.add(row_hash)
                    admitted = _admit_stored_row(
                        row,
                        store_kind="pt1",
                        artifact=artifact,
                        expected_replay_scope_sha256=replay_scope_sha256,
                        admission_keys=(query_key,) if query_key is not None else (),
                        validate_interpolation_domain=True,
                    )
                    assert admitted is not None
                    if admitted.stale_payload_codec:
                        continue
                    entry = admitted.entry
                    decoded = admitted.decoded_payload
                    if decoded is None:
                        continue
                    candidates.append(
                        {
                            "artifact": artifact,
                            "key": copy.deepcopy(dict(entry["key"])),
                            "key_hash": row_hash,
                            "payload": copy.deepcopy(dict(entry["payload"])),
                            "payload_codec_version": entry.get(
                                "payload_codec_version"
                            ),
                            "repair_notices_json": entry[
                                "repair_notices_json"
                            ],
                            "law_disclosures": (
                                entry["law_disclosures"]
                                if "law_disclosures" in entry
                                else None
                            ),
                        }
                    )
            return candidates

    def get_by_physics_ladder_bucket(
        self,
        *,
        artifact: str,
        rung_tag: str,
        rung_key: Mapping[str, Any],
        rung_hash: str,
        query_key: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return self._get_by_physics_ladder_bucket(
            artifact=artifact,
            rung_tag=rung_tag,
            rung_key=rung_key,
            rung_hash=rung_hash,
            query_key=query_key,
        )

    def _get_by_physics_ladder_bucket(
        self,
        *,
        artifact: str,
        rung_tag: str,
        rung_key: Mapping[str, Any],
        rung_hash: str,
        query_key: Mapping[str, Any] | None = None,
        return_decoded: bool = False,
    ) -> Any | None:
        if query_key is not None and _ladder_indexability(query_key) is not None:
            return None
        hash_column = _physics_ladder_hash_column(rung_tag)
        distance_column = _physics_ladder_distance_column(rung_tag)
        query = f"""
            SELECT *
            FROM {{table}}
            WHERE artifact = ?
              AND replay_scope_sha256 = ?
              AND {hash_column} = ?
            ORDER BY {distance_column} ASC, key_hash ASC
            """
        params = (
            artifact,
            _replay_scope_hash(rung_key),
            rung_hash,
        )
        with self._connect() as conn:
            self._initialize(conn)
            tables = [PT1_EQUILIBRIUM_TABLE]
            read_only_table = self._read_only_equilibrium_table(conn)
            if read_only_table is not None:
                tables.append(read_only_table)
            best_entry: dict[str, Any] | None = None
            best_decoded: Any = None
            best_sort: tuple[float, str] | None = None
            for table in tables:
                rows = conn.execute(query.format(table=table), params)
                for row in rows:
                    entry, decoded = self._entry_from_physics_ladder_bucket_row(
                        row,
                        artifact=artifact,
                        rung_tag=rung_tag,
                        rung_key=rung_key,
                        rung_hash=rung_hash,
                        query_key=query_key,
                    )
                    if decoded is None:
                        continue
                    if query_key is not None and _physics_ladder_coarsens_controls(
                        rung_tag
                    ):
                        budget = physics_control_rung_error_budget(
                            query_key,
                            entry["key"],
                            rung_tag,
                            source_payload=entry.get("payload"),
                        )
                        if not bool(budget["accepted"]):
                            continue
                    row_distance = float(row[distance_column])
                    sort_key = (row_distance, str(entry["key_hash"]))
                    if best_sort is None or sort_key < best_sort:
                        best_sort = sort_key
                        best_entry = entry
                        best_decoded = decoded
            if return_decoded and best_entry is not None:
                return best_entry, best_decoded
            return best_entry

    def _entry_from_physics_ladder_bucket_row(
        self,
        row: sqlite3.Row,
        *,
        artifact: str,
        rung_tag: str,
        rung_key: Mapping[str, Any],
        rung_hash: str,
        query_key: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], Any]:
        admitted = _admit_stored_row(
            row,
            store_kind="pt1",
            artifact=artifact,
            admission_keys=(query_key,) if query_key is not None else (),
            rung_tag=rung_tag,
            rung_key=rung_key,
            rung_hash=rung_hash,
            validate_interpolation_domain=True,
        )
        assert admitted is not None
        return admitted.entry, admitted.decoded_payload

    def _connect(self) -> sqlite3.Connection:
        timeout_sec = self._shard_busy_timeout_ms / 1000.0
        conn = sqlite3.connect(self.db_path, timeout=timeout_sec)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={int(self._shard_busy_timeout_ms)}")
        if self.read_only_base_db_path is not None:
            journal_mode = str(conn.execute("PRAGMA main.journal_mode").fetchone()[0])
            if journal_mode.lower() != "wal":
                conn.execute("PRAGMA main.journal_mode=WAL")
        self._attach_read_only_base(conn)
        return conn

    def _attach_read_only_base(self, conn: sqlite3.Connection) -> None:
        if self.read_only_base_db_path is None:
            return
        if _paths_refer_to_same_file(
            self.read_only_base_db_path,
            self.db_path,
        ):
            raise ValueError(
                "read_only_base_db_path must not equal db_path; attaching base-as-itself "
                "would route unqualified INSERTs into the read-only base"
            )
        if not self.read_only_base_db_path.exists():
            _LOGGER.warning(
                "read_only_base_db_path does not exist: %s; "
                "epoch will miss base-cache hits",
                self.read_only_base_db_path,
            )
            return
        uri = self._sqlite_readonly_uri(self.read_only_base_db_path)
        conn.execute(
            f"ATTACH DATABASE ? AS {PT1_READ_ONLY_BASE_ALIAS}",
            (uri,),
        )

    @staticmethod
    def _sqlite_readonly_uri(path: Path) -> str:
        return f"{path.resolve().as_uri()}?mode=ro"

    def _read_only_equilibrium_table(self, conn: sqlite3.Connection) -> str | None:
        alias = PT1_READ_ONLY_BASE_ALIAS
        row = conn.execute(
            "SELECT 1 FROM pragma_database_list WHERE name = ?",
            (alias,),
        ).fetchone()
        if row is None:
            return None
        return f"{alias}.{PT1_EQUILIBRIUM_TABLE}"

    def _initialize(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {PT1_METADATA_TABLE} (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {PT1_EQUILIBRIUM_TABLE} (
                key_hash TEXT PRIMARY KEY,
                artifact TEXT NOT NULL,
                store_schema_version TEXT NOT NULL,
                request_schema_version TEXT NOT NULL,
                key_sha256 TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                payload_codec_version INTEGER,
                key_bytes BLOB NOT NULL,
                payload_bytes BLOB NOT NULL,
                code_version TEXT NOT NULL,
                corpus_version TEXT,
                engine_version TEXT,
                data_digests_json TEXT NOT NULL,
                repair_notices_json TEXT,
                physics_bucket_schema_version TEXT,
                physics_bucket_sha256 TEXT,
                replay_scope_sha256 TEXT,
                physics_key_bytes BLOB,
                physics_bucket_h40_sha256 TEXT,
                physics_bucket_h40_distance REAL,
                physics_bucket_h30_sha256 TEXT,
                physics_bucket_h30_distance REAL,
                physics_bucket_h40c_sha256 TEXT,
                physics_bucket_h40c_distance REAL,
                physics_bucket_h30c_sha256 TEXT,
                physics_bucket_h30c_distance REAL,
                created_at TEXT NOT NULL,
                git_dirty INTEGER NOT NULL
            )
            """
        )
        self._ensure_physics_bucket_columns(conn)
        conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{PT1_EQUILIBRIUM_TABLE}_artifact
            ON {PT1_EQUILIBRIUM_TABLE}(artifact)
            """
        )
        conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{PT1_EQUILIBRIUM_TABLE}_engine_version
            ON {PT1_EQUILIBRIUM_TABLE}(engine_version)
            """
        )
        conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{PT1_EQUILIBRIUM_TABLE}_physics
            ON {PT1_EQUILIBRIUM_TABLE}(
                artifact,
                physics_bucket_schema_version,
                physics_bucket_sha256,
                replay_scope_sha256
            )
            """
        )
        for rung_tag, _sig_figs in PHYSICS_BUCKET_ALL_LADDER_RUNGS:
            hash_column = _physics_ladder_hash_column(rung_tag)
            distance_column = _physics_ladder_distance_column(rung_tag)
            conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{PT1_EQUILIBRIUM_TABLE}_{rung_tag}
                ON {PT1_EQUILIBRIUM_TABLE}(
                    artifact,
                    replay_scope_sha256,
                    {hash_column},
                    {distance_column},
                    key_hash
                )
                """
            )
        metadata = conn.execute(
            f"SELECT value FROM {PT1_METADATA_TABLE} WHERE key = ?",
            ("store_schema_version",),
        ).fetchone()
        if (
            metadata is not None
            and metadata["value"] not in PT1_SUPPORTED_STORE_SCHEMA_VERSIONS
        ):
            raise PT1PersistentStoreCorrupt(
                "PT-1 persistent store schema version drift: "
                f"{metadata['value']} != {PT1_STORE_SCHEMA_VERSION}"
            )
        legacy_rows = (
            conn.execute(
                f"SELECT * FROM {PT1_EQUILIBRIUM_TABLE} "
                "WHERE store_schema_version = ?",
                (PT1_LEGACY_STORE_SCHEMA_VERSION,),
            ).fetchall()
            if metadata is None
            or metadata["value"] == PT1_LEGACY_STORE_SCHEMA_VERSION
            else ()
        )
        if legacy_rows:
            row_columns = tuple(
                str(row["name"])
                for row in conn.execute(
                    f"PRAGMA table_info({PT1_EQUILIBRIUM_TABLE})"
                )
            )
            assignments = ", ".join(f'"{column}" = ?' for column in row_columns)
            for legacy_row in legacy_rows:
                candidate_row = dict(legacy_row)
                candidate_row["store_schema_version"] = PT1_STORE_SCHEMA_VERSION
                candidate_row["_write_rebuild_indexes"] = True
                try:
                    writable_row = _gate_row_for_write(candidate_row)
                except (UncacheableKey, UncacheablePayload):
                    continue
                conn.execute(
                    f"UPDATE {PT1_EQUILIBRIUM_TABLE} SET {assignments} "
                    'WHERE "key_hash" = ?',
                    tuple(writable_row[column] for column in row_columns)
                    + (writable_row["key_hash"],),
                )
        if metadata is not None and metadata["value"] != PT1_STORE_SCHEMA_VERSION:
            conn.execute(
                f"UPDATE {PT1_METADATA_TABLE} SET value = ? WHERE key = ?",
                (PT1_STORE_SCHEMA_VERSION, "store_schema_version"),
            )
        conn.execute(
            f"""
            INSERT OR IGNORE INTO {PT1_METADATA_TABLE} (key, value)
            VALUES (?, ?)
            """,
            ("store_schema_version", PT1_STORE_SCHEMA_VERSION),
        )

    def _ensure_physics_bucket_columns(self, conn: sqlite3.Connection) -> None:
        existing = {
            str(row["name"])
            for row in conn.execute(f"PRAGMA table_info({PT1_EQUILIBRIUM_TABLE})")
        }
        columns = {
            "corpus_version": "TEXT",
            "payload_codec_version": "INTEGER",
            "repair_notices_json": "TEXT",
            "physics_bucket_schema_version": "TEXT",
            "physics_bucket_sha256": "TEXT",
            "replay_scope_sha256": "TEXT",
            "physics_key_bytes": "BLOB",
            "physics_bucket_h40_sha256": "TEXT",
            "physics_bucket_h40_distance": "REAL",
            "physics_bucket_h30_sha256": "TEXT",
            "physics_bucket_h30_distance": "REAL",
            "physics_bucket_h40c_sha256": "TEXT",
            "physics_bucket_h40c_distance": "REAL",
            "physics_bucket_h30c_sha256": "TEXT",
            "physics_bucket_h30c_distance": "REAL",
            "law_disclosures_json": "TEXT",
        }
        for name, column_type in columns.items():
            if name not in existing:
                try:
                    conn.execute(
                        f"ALTER TABLE {PT1_EQUILIBRIUM_TABLE} "
                        f"ADD COLUMN {name} {column_type}"
                    )
                except sqlite3.OperationalError as exc:
                    if f"duplicate column name: {name}" not in str(exc).casefold():
                        raise
                    existing = {
                        str(row["name"])
                        for row in conn.execute(
                            f"PRAGMA table_info({PT1_EQUILIBRIUM_TABLE})"
                        )
                    }
                    if name not in existing:
                        raise
                else:
                    existing.add(name)

    def _update_physics_bucket_columns(
        self,
        conn: sqlite3.Connection,
        *,
        key_hash: str,
        physics_bucket_key: Mapping[str, Any],
        physics_bucket_bytes: bytes,
        physics_bucket_hash: str,
        ladder_values: Mapping[str, Mapping[str, Any]],
    ) -> None:
        conn.execute(
            f"""
            UPDATE {PT1_EQUILIBRIUM_TABLE}
            SET physics_bucket_schema_version = ?,
                physics_bucket_sha256 = ?,
                replay_scope_sha256 = ?,
                physics_key_bytes = ?,
                physics_bucket_h40_sha256 = ?,
                physics_bucket_h40_distance = ?,
                physics_bucket_h30_sha256 = ?,
                physics_bucket_h30_distance = ?,
                physics_bucket_h40c_sha256 = ?,
                physics_bucket_h40c_distance = ?,
                physics_bucket_h30c_sha256 = ?,
                physics_bucket_h30c_distance = ?
            WHERE key_hash = ?
              AND (
                  physics_bucket_sha256 IS NULL
                  OR physics_bucket_h40_sha256 IS NULL
                  OR physics_bucket_h30_sha256 IS NULL
                  OR physics_bucket_h40c_sha256 IS NULL
                  OR physics_bucket_h30c_sha256 IS NULL
              )
            """,
            (
                PHYSICS_BUCKET_SCHEMA_VERSION,
                physics_bucket_hash,
                _replay_scope_hash(physics_bucket_key),
                sqlite3.Binary(physics_bucket_bytes),
                ladder_values["h40"]["sha256"],
                ladder_values["h40"]["distance"],
                ladder_values["h30"]["sha256"],
                ladder_values["h30"]["distance"],
                ladder_values["h40c"]["sha256"],
                ladder_values["h40c"]["distance"],
                ladder_values["h30c"]["sha256"],
                ladder_values["h30c"]["distance"],
                key_hash,
            ),
        )

    def _fetch(
        self,
        conn: sqlite3.Connection,
        key_hash: str,
    ) -> sqlite3.Row | None:
        return next(self._fetch_candidates(conn, key_hash), None)

    def _fetch_candidates(
        self,
        conn: sqlite3.Connection,
        key_hash: str,
    ) -> Iterator[sqlite3.Row]:
        tables = [PT1_EQUILIBRIUM_TABLE]
        read_only_table = self._read_only_equilibrium_table(conn)
        if read_only_table is not None:
            tables.append(read_only_table)
        for table in tables:
            repair_column = (
                "repair_notices_json"
                if self._table_has_column(conn, table, "repair_notices_json")
                else "NULL"
            )
            law_column = (
                "law_disclosures_json"
                if self._table_has_column(conn, table, "law_disclosures_json")
                else "NULL"
            )
            codec_version_column = (
                "payload_codec_version"
                if self._table_has_column(conn, table, "payload_codec_version")
                else "NULL"
            )
            physics_index_columns = (
                "physics_bucket_schema_version",
                "physics_bucket_sha256",
                "replay_scope_sha256",
                "physics_key_bytes",
                *(
                    column
                    for rung_tag, _sig_figs in PHYSICS_BUCKET_ALL_LADDER_RUNGS
                    for column in (
                        _physics_ladder_hash_column(rung_tag),
                        _physics_ladder_distance_column(rung_tag),
                    )
                ),
            )
            physics_index_projection = ",\n                    ".join(
                column
                if self._table_has_column(conn, table, column)
                else f"NULL AS {column}"
                for column in physics_index_columns
            )
            row = conn.execute(
                f"""
                SELECT
                    key_hash,
                    artifact,
                    store_schema_version,
                    request_schema_version,
                    key_sha256,
                    payload_sha256,
                    {codec_version_column} AS payload_codec_version,
                    key_bytes,
                    payload_bytes,
                    code_version,
                    corpus_version,
                    engine_version,
                    data_digests_json,
                    created_at,
                    git_dirty,
                    {repair_column} AS repair_notices_json,
                    {law_column} AS law_disclosures_json,
                    {physics_index_projection}
                FROM {table}
                WHERE key_hash = ?
                """,
                (key_hash,),
            ).fetchone()
            if row is not None:
                yield row

    @staticmethod
    def _table_has_column(
        conn: sqlite3.Connection,
        table: str,
        column: str,
    ) -> bool:
        if "." in table:
            schema, table_name = table.split(".", 1)
            rows = conn.execute(f"PRAGMA {schema}.table_info({table_name})")
        else:
            rows = conn.execute(f"PRAGMA table_info({table})")
        return any(str(row["name"]) == column for row in rows)

    def _entry_from_row(
        self,
        row: sqlite3.Row | Mapping[str, Any],
        *,
        artifact: str,
        key: Mapping[str, Any] | None = None,
        key_bytes: bytes | None = None,
        key_hash: str | None = None,
        return_decoded: bool = False,
        admission_keys: Sequence[Mapping[str, Any]] | None = None,
        expected_replay_scope_sha256: str | None = None,
    ) -> Any:
        admitted = _admit_stored_row(
            row,
            store_kind="pt1",
            artifact=artifact,
            key=key,
            key_bytes=key_bytes,
            key_hash=key_hash,
            admission_keys=admission_keys,
            expected_replay_scope_sha256=expected_replay_scope_sha256,
            validate_interpolation_domain=True,
        )
        assert admitted is not None
        return (
            (admitted.entry, admitted.decoded_payload)
            if return_decoded
            else admitted.entry
        )

    def _entry_from_physics_bucket_row(
        self,
        row: sqlite3.Row | Mapping[str, Any],
        *,
        artifact: str,
        physics_bucket_key: Mapping[str, Any],
        physics_bucket_bytes: bytes,
        physics_bucket_hash: str,
        query_key: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], Any]:
        admitted = _admit_stored_row(
            row,
            store_kind="pt1",
            artifact=artifact,
            admission_keys=(query_key,) if query_key is not None else (),
            physics_bucket_key=physics_bucket_key,
            physics_bucket_bytes=physics_bucket_bytes,
            physics_bucket_hash=physics_bucket_hash,
            validate_interpolation_domain=True,
        )
        assert admitted is not None
        return admitted.entry, admitted.decoded_payload


def _pt1_row_admission(
    row: Mapping[str, Any],
) -> bool:
    """Compatibility predicate for tests; row validation has one owner."""

    admitted = _admit_stored_row(row, store_kind="pt1")
    assert admitted is not None
    return admitted.decoded_payload is not None


def _pt1_cache_merge_identity(
    row: Mapping[str, Any],
    *,
    require_capture_epoch_match: bool,
) -> tuple[Any, ...]:
    fields = _PT1_CACHE_MERGE_IDENTITY_FIELDS
    if require_capture_epoch_match:
        fields += ("created_at",)
    return tuple(row[field] for field in fields)


def _copy_pt1_equilibrium_rows(
    source_paths: Iterable[Path],
    target_path: Path,
    *,
    require_capture_epoch_match: bool,
    key_hashes: Iterable[str] | None = None,
    strict_source_validation: bool = False,
) -> dict[str, Any]:
    """Migrate the target, then atomically merge source rows by column name."""

    target_path = Path(target_path).expanduser()
    target_resolved = target_path.resolve()
    target_store = PT1PersistentEquilibriumStore(target_path)
    sources: list[Path] = []
    for source_path in source_paths:
        source_path = Path(source_path).expanduser()
        if source_path.resolve() == target_resolved:
            continue
        if not source_path.exists():
            raise FileNotFoundError(f"PT-1 cache source does not exist: {source_path}")
        sources.append(source_path)

    selected_hashes = None if key_hashes is None else {str(item) for item in key_hashes}
    with target_store._connect() as target_conn:
        target_conn.execute("BEGIN IMMEDIATE")
        target_columns = tuple(
            str(row["name"])
            for row in target_conn.execute(
                f"PRAGMA table_info({PT1_EQUILIBRIUM_TABLE})"
            )
        )
        original_target_rows: dict[str, dict[str, Any]] = {}
        original_target_admission: dict[str, _AdmittedStoredRow] = {}
        for row in target_conn.execute(
            f"SELECT * FROM {PT1_EQUILIBRIUM_TABLE}"
        ):
            admitted = _admit_stored_row(
                row,
                store_kind="pt1",
                validate_interpolation_domain=True,
            )
            assert admitted is not None and admitted.stored_columns is not None
            key_hash = str(admitted.entry["key_hash"])
            original_target_rows[key_hash] = admitted.stored_columns
            original_target_admission[key_hash] = admitted
        planned_rows = dict(original_target_rows)
        planned_admission = {
            key_hash: admitted.decoded_payload is not None
            for key_hash, admitted in original_target_admission.items()
        }
        changed_rows: dict[str, dict[str, Any]] = {}
        source_summaries: list[dict[str, Any]] = []

        for source_path in sources:
            source_uri = f"{source_path.resolve().as_uri()}?mode=ro"
            source_conn = sqlite3.connect(source_uri, uri=True)
            source_conn.row_factory = sqlite3.Row
            try:
                if selected_hashes is None:
                    source_rows = source_conn.execute(
                        f"SELECT * FROM {PT1_EQUILIBRIUM_TABLE}"
                    ).fetchall()
                elif not selected_hashes:
                    source_rows = []
                else:
                    source_rows = []
                    selected_list = sorted(selected_hashes)
                    for start in range(0, len(selected_list), 900):
                        batch = selected_list[start : start + 900]
                        placeholders = ",".join("?" for _ in batch)
                        source_rows.extend(
                            source_conn.execute(
                                f"SELECT * FROM {PT1_EQUILIBRIUM_TABLE} "
                                f"WHERE key_hash IN ({placeholders})",
                                tuple(batch),
                            ).fetchall()
                        )
            finally:
                source_conn.close()

            inserted = 0
            replaced = 0
            for source_row in source_rows:
                incoming_admission = _admit_stored_row(
                    source_row,
                    store_kind="pt1",
                    output_columns=target_columns,
                    strict_source_validation=strict_source_validation,
                    validate_interpolation_domain=True,
                )
                assert (
                    incoming_admission is not None
                    and incoming_admission.stored_columns is not None
                )
                incoming_row = incoming_admission.stored_columns
                key_hash = str(incoming_admission.entry["key_hash"])
                incoming_admitted = incoming_admission.decoded_payload is not None
                existing_row = planned_rows.get(key_hash)
                if existing_row is None:
                    planned_rows[key_hash] = incoming_row
                    planned_admission[key_hash] = incoming_admitted
                    changed_rows[key_hash] = incoming_row
                    inserted += 1
                    continue

                existing_admitted = planned_admission[key_hash]
                if existing_admitted and incoming_admitted:
                    existing_identity = _pt1_cache_merge_identity(
                        existing_row,
                        require_capture_epoch_match=require_capture_epoch_match,
                    )
                    incoming_identity = _pt1_cache_merge_identity(
                        incoming_row,
                        require_capture_epoch_match=require_capture_epoch_match,
                    )
                    if existing_identity != incoming_identity:
                        raise _PT1CacheMergeCollision(
                            f"PT-1 cache collision while merging {key_hash}"
                        )

                    def merge_stored_sidecars() -> dict[str, Any]:
                        merged = dict(incoming_row)
                        merged["repair_notices_json"] = monotonic_repair_notices_json(
                            existing_row.get("repair_notices_json"),
                            incoming_row.get("repair_notices_json"),
                        )
                        merged_disclosures = _prefer_law_disclosures(
                            existing_row.get("law_disclosures_json"),
                            incoming_row.get("law_disclosures_json"),
                        )
                        merged["law_disclosures_json"] = _law_disclosures_json(
                            merged_disclosures
                        )
                        return merged

                    merged_admission = _admit_stored_row(
                        merge_stored_sidecars(),
                        store_kind="pt1",
                        validate_interpolation_domain=True,
                    )
                    assert (
                        merged_admission is not None
                        and merged_admission.stored_columns is not None
                    )
                    merged_row = merged_admission.stored_columns
                    planned_rows[key_hash] = merged_row
                    planned_admission[key_hash] = True
                    if merged_row != existing_row:
                        changed_rows[key_hash] = merged_row
                elif existing_admitted:
                    continue
                else:
                    planned_rows[key_hash] = incoming_row
                    planned_admission[key_hash] = incoming_admitted
                    changed_rows[key_hash] = incoming_row
                    if key_hash in original_target_rows:
                        replaced += 1

            source_summaries.append(
                {
                    "source": str(source_path),
                    "rows": len(source_rows),
                    "inserted_rows": inserted,
                    "replaced_rows": replaced,
                }
            )

        if changed_rows:
            quoted_columns = ", ".join(
                f'"{column}"' for column in target_columns
            )
            placeholders = ", ".join("?" for _ in target_columns)
            insert_statement = (
                f"INSERT INTO {PT1_EQUILIBRIUM_TABLE} "
                f"({quoted_columns}) VALUES ({placeholders})"
            )
            update_assignments = ", ".join(
                f'"{column}" = ?' for column in target_columns
            )
            update_statement = (
                f"UPDATE {PT1_EQUILIBRIUM_TABLE} SET {update_assignments} "
                'WHERE "key_hash" = ?'
            )
            for key_hash, row in changed_rows.items():
                try:
                    writable_row = _gate_row_for_write(row)
                except (UncacheableKey, UncacheablePayload) as exc:
                    raise PT1PersistentStoreCorrupt(
                        "PT-1 stored source row is not copyable after migration: "
                        f"{key_hash}"
                    ) from exc
                values = tuple(writable_row[column] for column in target_columns)
                if key_hash in original_target_rows:
                    target_conn.execute(update_statement, (*values, key_hash))
                else:
                    target_conn.execute(insert_statement, values)

    return {
        "sources": source_summaries,
        "inserted_rows": sum(
            int(summary["inserted_rows"]) for summary in source_summaries
        ),
        "replaced_rows": sum(
            int(summary["replaced_rows"]) for summary in source_summaries
        ),
    }


def _engine_result_namespace_id(
    *,
    artifact: str,
    intent: ChemistryIntent,
    backend: Any,
) -> str:
    family = _typed_backend_family(backend)
    result_shape = (
        "freeze-gate-curve"
        if intent == ChemistryIntent.GATE_LIQUID_FRACTION
        or artifact == "freeze_gate_curve"
        else "equilibrium-composite"
    )
    return f"{family}:{result_shape}"


def _namespace_output_is_extensive(namespace_id: str) -> bool:
    return namespace_id == "alphamelts:equilibrium-composite"


def _effective_magemin_database(
    sim: Any,
    intent: ChemistryIntent,
    provider_role: str | None,
    *,
    resolve_default: bool = True,
) -> str | None:
    candidates: list[Any] = []
    registry = getattr(sim, "_chem_registry", None)
    if registry is not None:
        provider = (
            registry.fallback_for(intent)
            if provider_role == "fallback"
            else registry.authoritative_for(intent)
        )
        candidates.extend((provider, getattr(provider, "_backend", None)))
    backend = getattr(sim, "backend", None)
    candidates.extend((backend, getattr(backend, "_live_backend", None)))
    for candidate in candidates:
        database = getattr(candidate, "_database", None)
        if database is not None and str(database).strip():
            return str(database).strip()
    if resolve_default and any(
        getattr(candidate, "real_backend_family", None)
        is RealBackendFamily.MAGEMIN
        for candidate in candidates
    ):
        from simulator.melt_backend.magemin import MAGEMinBackend

        return MAGEMinBackend._DEFAULT_DATABASE
    return None


def _resolved_backend_for_cache_identity(
    sim: Any,
    intent: ChemistryIntent,
    provider_role: str | None,
) -> Any:
    registry = getattr(sim, "_chem_registry", None)
    if registry is not None:
        provider = (
            registry.fallback_for(intent)
            if provider_role == "fallback"
            else registry.authoritative_for(intent)
        )
        provider_backend = getattr(provider, "_backend", None)
        if provider_backend is not None:
            return provider_backend
        if (
            isinstance(provider, RealBackendAuthority)
            and getattr(provider, "real_backend_family", None)
            is RealBackendFamily.MAGEMIN
        ):
            return provider
        if provider is not None and intent is ChemistryIntent.GATE_LIQUID_FRACTION:
            return None
    backend = getattr(sim, "backend", None)
    return getattr(backend, "_live_backend", None) or backend


def _backend_boundary_composition(
    sim: Any,
    intent: ChemistryIntent,
    provider_role: str | None,
    *,
    namespace_id: str,
    sig_figs: int,
    composition_mol: Mapping[str, float],
) -> list[tuple[str, float]]:
    cleaned = dict(composition_mol)
    backend = _resolved_backend_for_cache_identity(sim, intent, provider_role)
    species_registry = getattr(sim, "species_formula_registry", None)
    family = namespace_id.partition(":")[0]
    if family == "magemin":
        from simulator.melt_backend.magemin import (
            MAGEMinBackend,
            _MAGEMIN_ENGINE_INPUT_BASIS,
        )

        if backend is None or not hasattr(backend, "_build_db_bulk_projection"):
            backend = MAGEMinBackend()
            database = _effective_magemin_database(sim, intent, provider_role)
            if database:
                backend._database = database
        projection = project_melt_to_oxide_projection(
            composition_kg=None,
            composition_mol=cleaned,
            oxide_basis=getattr(
                backend,
                "_MAGEMIN_INPUT_BASIS",
                _MAGEMIN_ENGINE_INPUT_BASIS,
            ),
            species_formula_registry=species_registry,
        )
        bulk = backend._build_db_bulk_projection(projection.oxide_wt_pct)
        return [
            (str(component), float(_sigfig(value, sig_figs) or 0.0))
            for component, value in zip(bulk.order, bulk.vector)
        ]
    if family in {"alphamelts", "thermoengine"}:
        from simulator.melt_backend.alphamelts import MELTS_OXIDE_BASIS

        projection = project_melt_to_oxide_projection(
            composition_kg=None,
            composition_mol=cleaned,
            oxide_basis=MELTS_OXIDE_BASIS,
            species_formula_registry=species_registry,
        )
        normalizer = getattr(backend, "_normalize_composition_to_melts_basis", None)
        if callable(normalizer):
            normalized = normalizer(projection.oxide_wt_pct)
        else:
            # Fail-loud cached-real replay has no live backend instance. Rebuild
            # the ordinary MELTS boundary normalization so its key remains
            # byte-identical to a direct/live-fill producer.
            normalized = {
                oxide: float(projection.oxide_wt_pct.get(oxide, 0.0))
                for oxide in MELTS_OXIDE_BASIS
            }
            total = sum(normalized.values())
            if total <= 0.0:
                raise ValueError("MELTS boundary composition has no basis oxides")
            normalized = {
                oxide: value / total * 100.0
                for oxide, value in normalized.items()
            }
        return [
            (str(component), float(_sigfig(value, sig_figs) or 0.0))
            for component, value in sorted(normalized.items())
        ]
    return _composition_mol_fraction_from_mol(cleaned, sig_figs=sig_figs)


def record_dependency_vector(
    sim: Any,
    intent: ChemistryIntent,
    *,
    artifact: str,
    provider_role: str | None = None,
    resolve_magemin_database_default: bool = False,
) -> dict[str, Any]:
    # PT0 keys keep an unset MAGEMin database as recorded; memo admission opts
    # into resolving that default so lazy and initialized producers compare equal.
    provider = _provider_identity(sim, intent, provider_role=provider_role)
    vapor_provider = _provider_identity(sim, ChemistryIntent.VAPOR_PRESSURE)
    resolved_backend = _resolved_backend_for_cache_identity(
        sim, intent, provider_role
    )
    sulfsat_gate = getattr(sim, "_sulfsat_gate", None)
    sulfsat_identity = getattr(
        sim, "_c0_forecast_sulfsat_identity", None
    )
    if sulfsat_identity is None:
        sulfsat_identity = _sulfsat_identity(sulfsat_gate)
    elif (
        not isinstance(sulfsat_identity, Mapping)
        or not isinstance(
            sulfsat_identity.get("sulfsat_provider_selection"), str
        )
        or type(sulfsat_identity.get("sulfsat_available")) is not bool
    ):
        raise ValueError("invalid C0 forecast SulfSat cache identity")
    return {
        "namespace_id": _engine_result_namespace_id(
            artifact=artifact,
            intent=intent,
            backend=resolved_backend,
        ),
        "vapor_pressure_provider_selection": vapor_provider.get(
            "resolved_provider_id"
        ),
        "sulfur_side": {
            "S_input_ppm": _sigfig(sim._stage0_sulfur_input_ppm(), 6),
            "inventory": _json_ready(
                {
                    "salt_phase": _positive_float_map(
                        getattr(sim.inventory, "salt_phase_kg", {}) or {}
                    ),
                    "sulfide_matte": _positive_float_map(
                        getattr(sim.inventory, "sulfide_matte_kg", {}) or {}
                    ),
                }
            ),
            "sulfsat_available": sulfsat_identity["sulfsat_available"],
            "sulfsat_provider_selection": sulfsat_identity[
                "sulfsat_provider_selection"
            ],
        },
        "model": {
            "model": provider.get("model"),
            "mode": provider.get("mode"),
            "magemin_database": _effective_magemin_database(
                sim,
                intent,
                provider_role,
                resolve_default=resolve_magemin_database_default,
            ),
        },
    }


def record_binding_producer_ids(
    sim: Any,
    *,
    artifact: str,
    provider_role: str | None = None,
) -> tuple[str, ...]:
    """Return non-backend producers selected for one cache artifact."""
    if artifact == "freeze_gate_curve":
        return ()
    if artifact != "equilibrium_post_record":
        raise ValueError(f"unsupported replay artifact: {artifact}")

    vector = record_dependency_vector(
        sim,
        _equilibrium_payload_intent(sim),
        artifact=artifact,
        provider_role=provider_role,
    )
    selected: list[str] = []
    pressure_provider = vector.get("vapor_pressure_provider_selection")
    if pressure_provider == "builtin-vapor-pressure":
        selected.append("builtin-vapor-pressure")
        from engines.builtin.vapor_pressure import (
            openimcc_binding_selected_by_inputs,
        )

        temperature_C = float(sim.melt.temperature_C)
        controls = dict(getattr(sim, "setpoints", {}) or {})
        controls["high_t_melt_activity"] = getattr(
            sim,
            "_high_t_melt_activity",
            controls.get("high_t_melt_activity", "openimcc"),
        )
        if openimcc_binding_selected_by_inputs(
            temperature_K=temperature_C + CELSIUS_TO_KELVIN_OFFSET,
            controls=controls,
            quantity_class="activity",
        ):
            selected.append("openimcc")

    sulfur_side = vector.get("sulfur_side", {})
    sulfur_input = float(sulfur_side.get("S_input_ppm", 0.0) or 0.0)
    if sulfur_input > 0.0 and getattr(sim, "_sulfsat_gate", None) is not None:
        selected.append("sulfsat")
    return tuple(selected)


def replay_scope_dependency_vector(key: Mapping[str, Any]) -> dict[str, Any]:
    sulfur_side = key.get("sulfur_side", {})
    if not isinstance(sulfur_side, Mapping):
        sulfur_side = {}
    vector: dict[str, Any] = {
        "namespace_id": key.get("namespace_id"),
        "vapor_pressure_provider_selection": key.get(
            "vapor_pressure_provider_selection"
        ),
        "model": _json_ready(key.get("model", {})),
    }
    redox = key.get("redox", {})
    if isinstance(redox, Mapping) and redox.get("redox_state_identity") is not None:
        vector["redox_state_identity"] = _json_ready(
            redox["redox_state_identity"]
        )
    sulfur_input_ppm = _sulfur_input_ppm_from_replay_key(key)
    if sulfur_input_ppm and sulfur_input_ppm > 0.0:
        vector["sulfsat"] = {
            name: sulfur_side.get(name)
            for name in (
                "sulfsat_available",
                "sulfsat_provider_selection",
            )
            if sulfur_side.get(name) is not None
        }
    return vector


def dependency_vector_from_replay_key(
    key: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Read the complete dependency selection already carried by a replay key."""

    fields = (
        "namespace_id",
        "vapor_pressure_provider_selection",
        "sulfur_side",
        "model",
    )
    if any(field not in key for field in fields):
        return None
    if not isinstance(key.get("sulfur_side"), Mapping):
        return None
    if not isinstance(key.get("model"), Mapping):
        return None
    return _json_ready({field: key[field] for field in fields})


def replay_dependency_vectors_match(
    query_key: Mapping[str, Any],
    candidate_key: Mapping[str, Any],
) -> bool:
    query_vector = dependency_vector_from_replay_key(query_key)
    return (
        query_vector is not None
        and dependency_vector_from_replay_key(candidate_key) == query_vector
    )


def gate_curve_memo_producer_configuration(
    sim: Any,
    curve: Mapping[str, Any],
    *,
    redox_state: RedoxState | None = None,
) -> dict[str, Any]:
    # Match the provider selection used by gate dispatch before a cache read.
    # Gate providers are otherwise registered lazily by the dispatch path,
    # after an uninitialized receiver has already refused a shared memo.
    _register_gate_providers_for_key(sim)
    provider_role = _gate_curve_provider_role(curve)
    if provider_role is None:
        provider_role = _gate_provider_role_for_key(sim)
    dependency_vector = record_dependency_vector(
        sim,
        ChemistryIntent.GATE_LIQUID_FRACTION,
        artifact="freeze_gate_curve",
        provider_role=provider_role,
        resolve_magemin_database_default=True,
    )
    sulfur_side = dependency_vector["sulfur_side"]
    model = dependency_vector["model"]
    redox_identity = (
        None if redox_state is None else redox_state.cache_identity()
    )
    return {
        "namespace_id": dependency_vector["namespace_id"],
        "vapor_pressure_provider_selection": dependency_vector[
            "vapor_pressure_provider_selection"
        ],
        "sulfur_side": {
            "sulfsat_available": sulfur_side["sulfsat_available"],
            "sulfsat_provider_selection": (
                sulfur_side["sulfsat_provider_selection"]
            ),
        },
        "model": {
            name: model[name]
            for name in ("model", "mode", "magemin_database")
        },
        **(
            {"redox_state_identity": redox_identity}
            if redox_identity is not None
            else {}
        ),
    }


class GateCurveProviderOutcome(str, Enum):
    NO_PROVIDER = "no_provider"
    PRODUCED = "produced"
    STABLE_REFUSAL = "stable_refusal"
    NUMERICAL_FAILURE = "numerical_failure"
    FAILED = "failed"


def gate_curve_provider_outcome_token(
    outcome: GateCurveProviderOutcome | str | None,
) -> str | None:
    """JSON payloads store the enum's value. ``type(member) is str`` is false."""

    if outcome is None:
        return None
    return GateCurveProviderOutcome(outcome).value


_GATE_CURVE_EXTRA_TRANSIENT_PROVIDER_REASONS = frozenset({
    "engine_crash",
    "engine_worker_unavailable",
    "magemin_engine_worker_died",
    "magemin_subprocess_slot_unavailable",
    "magemin_engine_worker_unavailable",
    "magemin_untyped_failure",
    "missing_binary",
    "nonzero_exit",
    "subprocess_died",
    "subprocess_fe_free_absolute_fo2_crash",
    "system_output_missing",
    "thermoengine_adapter_unavailable",
    "worker_pool_error",
    "engineworkerdied",
    "engineworkerunavailable",
    "engineworkerpoollifecycleerror",
    "mageminprocessexit",
    "filenotfounderror",
    "permissionerror",
    "oserror",
})


def gate_curve_provider_diagnostic_reasons(
    diagnostic: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    """Read reason codes through both provider and projected-solve wrappers."""
    from engines.domain_reason import reason_value

    if not isinstance(diagnostic, Mapping):
        return ()
    reasons = []
    for field in (
        "backend_status_reason", "backend_failure_reason_code", "reason", "reason_refused",
        "backend_failure_exception", "backend_failure_disposition",
    ):
        value = reason_value(diagnostic.get(field))
        if value is not None and value.strip():
            reasons.append(value.strip().lower())
    for field in ("backend_diagnostics", "projected_liquidus_diagnostics"):
        reasons.extend(gate_curve_provider_diagnostic_reasons(diagnostic.get(field)))
    return tuple(reasons)


def gate_curve_provider_timeout_reason(
    diagnostic: Mapping[str, Any] | None,
) -> str | None:
    return next(
        (
            reason
            for reason in gate_curve_provider_diagnostic_reasons(diagnostic)
            if "timeout" in reason
            or reason in {
                "aggregate_budget_exceeded",
                "mageminaggregatebudgetexceeded",
            }
        ),
        None,
    )


def failed_provider_attempt_is_environment(attempt: Mapping[str, Any] | None) -> bool:
    """True exactly when this attempt would trigger the freeze-gate retry.

    The retry calls ``gate_curve_provider_timeout_reason``. An attempt stores
    that diagnostic under ``diagnostic`` and the same reason codes, joined,
    on ``reason``. A physics reason (``composition_projected``,
    ``incomplete_cooling_path``) does not match, and a bare environment
    disposition does not either.
    """

    if not isinstance(attempt, Mapping):
        return False
    if gate_curve_provider_timeout_reason(attempt) is not None:
        return True
    diagnostic = attempt.get("diagnostic")
    return isinstance(diagnostic, Mapping) and (
        gate_curve_provider_timeout_reason(diagnostic) is not None
    )


def gate_curve_provider_has_terminal_input(reasons: Sequence[str]) -> bool:
    """Missing or invalid inputs cannot be repaired by changing engines."""
    return any(reason in {
        "missing_input", "invalid_input", "invalid_fo2_log",
        "invalid_pressure_bar", "non_finite_composition",
    } for reason in reasons)


def gate_curve_provider_timed_out(diagnostic: Mapping[str, Any] | None) -> bool:
    return gate_curve_provider_timeout_reason(diagnostic) is not None


def gate_curve_provider_outcome_for_status(
    status: str,
    diagnostic: Mapping[str, Any] | None = None,
) -> GateCurveProviderOutcome:
    """Classify provider diagnostics before deciding whether to reuse a curve.

    No-reason domain refusals remain stable for backends that omit diagnostics;
    an unrecognized reason on a refusal is conservatively treated as failed.
    """

    from engines.domain_reason import (
        STABLE_OUT_OF_DOMAIN_REASONS,
        TRANSIENT_OUT_OF_DOMAIN_REASONS,
        reason_value,
    )

    normalized_status = str(status).strip().lower()
    reasons = gate_curve_provider_diagnostic_reasons(diagnostic)
    if normalized_status == "not_converged" and "engine_not_converged" in reasons:
        backend_diagnostic = (
            diagnostic.get("backend_diagnostics")
            if isinstance(diagnostic, Mapping)
            else None
        )
        mode_deficit_diagnostics = (diagnostic, backend_diagnostic)
        if any(
            isinstance(item, Mapping)
            and item.get("magemin_mode_vector_mass_deficit") is True
            and item.get("solved_fO2_log") is None
            for item in mode_deficit_diagnostics
        ):
            # Preserve the reviewed generic outcome for a failed native fO2
            # readback combined with an incomplete Mode vector. Evaporation
            # applies the hold-or-floor qualification from these details.
            return GateCurveProviderOutcome.FAILED
    # MAGEMinOutputError is unusable solver output; keep its flagged fallback.
    # Other providers' parse-empty contract failures remain fail-closed below.
    magemin_invalid_output = "mageminoutputerror" in reasons
    # Opaque third-party bridge errors carry a numerical disposition so solver
    # failures retain the flagged fallback. Environment exceptions also keep
    # their backend_failure_exception tokens, which remain fail-closed below.
    magemin_bridge_numerical_failure = (
        "magemin_bridge_numerical_failure" in reasons
    )
    if "environment" in reasons:
        return GateCurveProviderOutcome.FAILED
    if gate_curve_provider_has_terminal_input(reasons):
        return GateCurveProviderOutcome.FAILED
    if "parse_empty_output" in reasons and not magemin_invalid_output:
        return GateCurveProviderOutcome.FAILED
    numerical_reasons = {
        "not_converged", "engine_not_converged", "negative_convergence_evidence",
        "no_convergence", "phase_mass_incomplete",
    }
    transient_reasons = {
        reason_value(reason).lower()
        for reason in TRANSIENT_OUT_OF_DOMAIN_REASONS
    } | _GATE_CURVE_EXTRA_TRANSIENT_PROVIDER_REASONS
    if gate_curve_provider_timed_out(diagnostic) or any(
        reason in transient_reasons - numerical_reasons
        and not (
            reason == "engine_crash" and magemin_bridge_numerical_failure
        )
        for reason in reasons
    ):
        return GateCurveProviderOutcome.FAILED
    nested_outcomes = []
    if isinstance(diagnostic, Mapping):
        backend = diagnostic.get("backend_diagnostics")
        if isinstance(backend, Mapping):
            nested_outcomes.append(gate_curve_provider_outcome_for_status(
                backend.get("backend_status") or status, backend,
            ))
        # A projected solve ran on a valid bulk. Its unavailable result is
        # numerical unless the preserved reason identifies an environment failure.
        projected_status = diagnostic.get("projected_liquidus_status")
        if projected_status is not None:
            nested_outcomes.append(
                GateCurveProviderOutcome.NUMERICAL_FAILURE
                if projected_status == "unavailable"
                else gate_curve_provider_outcome_for_status(
                    projected_status, diagnostic.get("projected_liquidus_diagnostics"),
                )
            )
    stable_reasons = {
        reason_value(reason).lower()
        for reason in STABLE_OUT_OF_DOMAIN_REASONS
    }
    if GateCurveProviderOutcome.FAILED in nested_outcomes:
        return GateCurveProviderOutcome.FAILED
    if (
        GateCurveProviderOutcome.NUMERICAL_FAILURE in nested_outcomes
        or normalized_status == "not_converged"
        or magemin_invalid_output
        or magemin_bridge_numerical_failure
        or any(reason in numerical_reasons for reason in reasons)
    ):
        return GateCurveProviderOutcome.NUMERICAL_FAILURE
    if normalized_status == "ok":
        return GateCurveProviderOutcome.PRODUCED
    if normalized_status in {"out_of_domain", "unsupported", "refused"}:
        if not reasons or all(
            reason in stable_reasons
            for reason in reasons
        ):
            return GateCurveProviderOutcome.STABLE_REFUSAL
    return GateCurveProviderOutcome.FAILED


def record_gate_curve_provider_outcome(
    sim: Any,
    outcome: GateCurveProviderOutcome | str,
) -> GateCurveProviderOutcome:
    """Keep one failed provider from being masked by a later fallback."""

    next_outcome = GateCurveProviderOutcome(outcome)
    raw_current = getattr(sim, "_freeze_gate_provider_outcome", None)
    try:
        current = GateCurveProviderOutcome(raw_current)
    except (TypeError, ValueError):
        current = None
    if (
        current is not GateCurveProviderOutcome.FAILED
        or next_outcome is GateCurveProviderOutcome.FAILED
    ):
        sim._freeze_gate_provider_outcome = next_outcome
    return GateCurveProviderOutcome(sim._freeze_gate_provider_outcome)


def gate_curve_provider_outcome_is_reusable(
    outcome: GateCurveProviderOutcome | str | None,
) -> bool:
    if outcome is None:
        return False
    try:
        typed_outcome = GateCurveProviderOutcome(outcome)
    except (TypeError, ValueError):
        return False
    return typed_outcome in {
        GateCurveProviderOutcome.NO_PROVIDER,
        GateCurveProviderOutcome.PRODUCED,
        GateCurveProviderOutcome.STABLE_REFUSAL,
    }


_LAW_DISCLOSURES_OMITTED = object()


def _copy_stored_law_disclosures(raw: Any) -> Any:
    """Copy a disclosure field without turning a bad row into a shorter list."""

    if isinstance(raw, tuple):
        raw = list(raw)
    if not isinstance(raw, list):
        return raw
    copied: list[Any] = []
    for item in raw:
        copied.append(dict(item) if isinstance(item, Mapping) else item)
    return copied


def _identity_gate_curve(curve: Mapping[str, Any]) -> dict[str, Any]:
    """Shallow copy with environment keys removed.

    Those keys stay on the live curve of the hour that retried. Memo
    records and identity compares use this view, so a later hit cannot
    publish that hour's timeout. The input curve is not mutated.
    """

    return {
        key: value
        for key, value in curve.items()
        if key not in GATE_CURVE_ENVIRONMENT_KEYS
    }


def gate_curve_memo_record(
    curve: Mapping[str, Any],
    origin: Mapping[str, Any] | None,
    repair_notices: Sequence[Mapping[str, Any]] | None,
    disposition: str,
    *,
    provider_outcome: GateCurveProviderOutcome | str | None = None,
    law_disclosures: Any = _LAW_DISCLOSURES_OMITTED,
) -> dict[str, Any]:
    """Build the runtime record shared by the producer and core reader.

    ``law_disclosures`` is omitted unless the caller measured one. A passed
    list, including ``[]``, is that measurement and is stored with the
    record. Leaving the field off is unknown, not a measured zero.
    """

    record = {
        "curve": _identity_gate_curve(curve),
        "origin": dict(origin) if isinstance(origin, Mapping) else None,
        "repair_notices": [dict(notice) for notice in repair_notices or ()],
        "disposition": disposition,
    }
    if provider_outcome is not None:
        record["provider_outcome"] = GateCurveProviderOutcome(provider_outcome)
    if law_disclosures is not _LAW_DISCLOSURES_OMITTED:
        record["law_disclosures"] = _copy_stored_law_disclosures(law_disclosures)
    return record


def _gate_curve_live_publication(
    record: Mapping[str, Any],
    curve: Mapping[str, Any],
) -> dict[str, Any]:
    """The hour's curve. ``record`` remains the identity memo."""

    published = dict(record)
    published["curve"] = curve
    return published


def gate_curve_memo_record_is_reusable(record: Mapping[str, Any]) -> bool:
    """Transient provider failures must not become reusable fallback curves."""

    return gate_curve_provider_outcome_is_reusable(
        record.get("provider_outcome")
    )


def gate_curve_memo_is_cacheable(
    sim: Any,
    curve: Mapping[str, Any],
    provider_outcome: GateCurveProviderOutcome | str | None,
    *,
    disposition: str = "live",
) -> bool:
    """Apply the shared admission rule before writing any gate-curve memo."""

    if (
        disposition == "replay"
        or getattr(sim, "_freeze_gate_held_curve_record", None) is not None
        or uncacheable_replay_disposition(sim)
        or not gate_curve_provider_outcome_is_reusable(provider_outcome)
        or not isinstance(curve, Mapping)
    ):
        return False
    return _is_cacheable_gate_curve(curve)


def uncacheable_replay_disposition(sim: Any) -> bool:
    reasons = getattr(sim, "_reduced_real_uncacheable_reasons", None)
    return bool(reasons)


def mark_uncacheable_replay_disposition(sim: Any, reason: str) -> None:
    reasons = getattr(sim, "_reduced_real_uncacheable_reasons", None)
    if not isinstance(reasons, list):
        reasons = []
        sim._reduced_real_uncacheable_reasons = reasons
    text = str(reason)
    if text and text not in reasons:
        reasons.append(text)


def _openimcc_fallback_is_uncacheable(sim: Any) -> bool:
    diagnostic = getattr(sim, "_last_vapor_pressure_diagnostic", None)
    if not isinstance(diagnostic, Mapping):
        return False
    high_t = diagnostic.get("high_t_melt_activity")
    if not isinstance(high_t, Mapping):
        return False
    return bool(high_t.get("fallback")) or str(high_t.get("provider")) == (
        "constant_gamma"
    )


def canonical_replay_key(
    sim: Any,
    *,
    artifact: str,
    intent: ChemistryIntent,
    fO2_log: float | None,
    fe_redox_policy: str,
    provider_role: str | None = None,
    control_quantization: ControlQuantization | None = None,
    redox_state: RedoxState | None = None,
) -> dict[str, Any]:
    quantization = control_quantization or _PRODUCTION_QUANTIZATION
    if intent == ChemistryIntent.GATE_LIQUID_FRACTION:
        if provider_role is None:
            provider_role = _gate_provider_role_for_key(sim)
        else:
            _register_gate_providers_for_key(sim)
    live_T_K = _quantize(
        float(sim.melt.temperature_C) + CELSIUS_TO_KELVIN_OFFSET,
        quantization.t_k_quantum,
        quantization.t_k_decimals,
    )
    if intent == ChemistryIntent.GATE_LIQUID_FRACTION:
        # Gate curves are composition artifacts. Keep the legacy T_K field as
        # a schema sentinel; the redox control below carries the T_STD key.
        T_K = _quantize(
            _GATE_CURVE_KEY_T_K_SENTINEL,
            quantization.t_k_quantum,
            quantization.t_k_decimals,
        )
    else:
        T_K = live_T_K
    raw_pressure_bar = float(sim.melt.p_total_mbar) / 1000.0
    pressure_bar = _quantize(
        raw_pressure_bar,
        quantization.pressure_bar_quantum,
        quantization.pressure_bar_decimals,
    )
    # Class-complete the fO2_log guard below: _quantize() returns None for any
    # non-finite control, so a NaN/inf melt temperature or pressure would
    # otherwise encode `None` straight into the cache key (and a None T_K would
    # flow into _compute_intrinsic_melt_fO2). Refuse here — finite controls
    # quantize to floats, so valid keys stay byte-identical (SC-49: guard every
    # sibling of a class-cutting invalid-control gate, not just the first).
    if live_T_K is None:
        raise PT0InvalidControls(
            "non-finite melt temperature passed to PT-0 cache key "
            f"quantization: {sim.melt.temperature_C!r}"
        )
    if pressure_bar is None:
        raise PT0InvalidControls(
            "non-finite melt pressure passed to PT-0 cache key "
            f"quantization: {sim.melt.p_total_mbar!r}"
        )
    if fO2_log is None:
        fO2_log = _authoritative_melt_fO2_log(sim)
    if intent == ChemistryIntent.GATE_LIQUID_FRACTION:
        # Gate curve replay follows the freeze-gate cache identity. Equilibrium
        # keys intentionally keep live-T fO2 because equilibrium solves actual
        # conditions, not a composition-only curve artifact.
        fO2_log = _melt_fO2_log_at_gate_key_reference_T(sim, fO2_log)
    quantized_fO2_log = _quantize(
        fO2_log,
        quantization.log_fo2_quantum,
        quantization.log_fo2_decimals,
    )
    if quantized_fO2_log is None:
        raise PT0InvalidControls(
            "non-finite fO2_log passed to PT-0 cache key quantization: "
            f"{fO2_log!r}"
        )
    # Same invalid-control class as T_K / pressure / fO2: _sigfig returns None on
    # non-finite input, so a non-finite commanded pO2 would otherwise encode a
    # None straight into the cache key. Commanded 0.0 (controlled-O2 off) is
    # finite and preserved; only NaN/inf is refused.
    commanded_pO2_bar = float(sim._commanded_pO2_bar())
    quantized_pO2_bar = _sigfig(
        commanded_pO2_bar,
        quantization.composition_sig_figs,
    )
    if quantized_pO2_bar is None:
        raise PT0InvalidControls(
            "non-finite commanded pO2 passed to PT-0 cache key quantization: "
            f"{commanded_pO2_bar!r} bar"
        )
    dependency_vector = record_dependency_vector(
        sim,
        intent,
        artifact=artifact,
        provider_role=provider_role,
    )
    namespace_id = dependency_vector["namespace_id"]
    include_amount = _namespace_output_is_extensive(namespace_id)
    transport_getter = getattr(sim, "_vapor_pressure_dispatch_pO2_bar", None)
    transport_pO2 = (
        float(transport_getter())
        if callable(transport_getter)
        else commanded_pO2_bar
    )
    quantized_transport_pO2 = _sigfig(
        transport_pO2,
        quantization.composition_sig_figs,
    )
    if quantized_transport_pO2 is None:
        raise PT0InvalidControls(
            "non-finite vapor transport pO2 passed to PT-0 cache key"
        )
    dependency_vector.pop("namespace_id")
    redox_key: dict[str, Any] = {
        "fe_redox_policy": str(fe_redox_policy),
    }
    redox_identity = (
        None if redox_state is None else redox_state.cache_identity()
    )
    if redox_identity is not None:
        redox_key["redox_state_identity"] = redox_identity
    provider_melt_inputs = sim._silicate_provider_melt_inputs(
        intent,
    )
    if provider_melt_inputs is not None:
        submitted_mol = provider_melt_inputs.composition_mol
    else:
        store = getattr(sim, "_pt0_determinism_store", None)
        canonicalizer = getattr(
            store,
            "canonical_composition_mol_by_account",
            None,
        )
        if store is not None and getattr(store, "quantize_live_controls", False):
            if not callable(canonicalizer):
                raise RuntimeError(
                    "PT-0 live-control quantization requires composition "
                    "canonicalization support"
                )
            submitted_mol = canonicalizer(sim).get(_CLEANED_MELT_ACCOUNT, {})
        else:
            submitted_mol = sim.atom_ledger.project_account_mol(
                _CLEANED_MELT_ACCOUNT
            )
    freeze_gate_cache_key = None
    if intent is ChemistryIntent.GATE_LIQUID_FRACTION:
        freeze_gate_cache_key = sim._freeze_gate_cache_key(
            pressure_bar=pressure_bar,
            fO2_log=quantized_fO2_log,
            redox_state=redox_state,
            provider_melt_inputs=provider_melt_inputs,
        )
    key = {
        "namespace_id": namespace_id,
        "composition_mol_fraction": _backend_boundary_composition(
            sim,
            intent,
            provider_role,
            namespace_id=namespace_id,
            sig_figs=quantization.composition_sig_figs,
            composition_mol=submitted_mol,
        ),
        "controls": {
            "T_K": T_K,
            "log_fO2": quantized_fO2_log,
            "pressure_bar": pressure_bar,
            "pO2_bar": quantized_pO2_bar,
            "vapor_transport_pO2_bar": quantized_transport_pO2,
        },
        "redox": redox_key,
        **dependency_vector,
    }
    if provider_melt_inputs is not None and (
        intent is not ChemistryIntent.GATE_LIQUID_FRACTION
    ):
        key["submitted_formula_identity"] = (
            provider_melt_inputs.formula_identity
        )
    if freeze_gate_cache_key is not None:
        key["freeze_gate_cache_key"] = freeze_gate_cache_key
    if include_amount:
        total_amount_kg = (
            provider_melt_inputs.amount_kg
            if provider_melt_inputs is not None
            else sum(
                float(value)
                for value in sim.atom_ledger.project_account_kg(
                    _CLEANED_MELT_ACCOUNT
                ).values()
                if float(value) > 0.0
            )
        )
        key["total_submitted_amount_kg"] = _sigfig(
            total_amount_kg,
            quantization.composition_sig_figs,
        )
    return key


def _compatible_replay_keys(key: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    return (copy.deepcopy(dict(key)),)


def _expand_compatible_replay_keys(
    keys: tuple[Mapping[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    expanded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in keys:
        for candidate in _compatible_replay_keys(key):
            digest = _sha256(canonical_json_bytes(candidate))
            if digest in seen:
                continue
            seen.add(digest)
            expanded.append(candidate)
    return tuple(expanded)


def _magemin_identity_from_replay_key(
    key: Mapping[str, Any],
) -> Any | None:
    freeze_gate_key = key.get("freeze_gate_cache_key")
    identity = getattr(freeze_gate_key, "magemin_domain_identity", None)
    from simulator.melt_backend.magemin import MAGEMinDomainIdentity

    if isinstance(identity, MAGEMinDomainIdentity):
        return identity
    if isinstance(freeze_gate_key, Mapping):
        identity_payload = freeze_gate_key.get("magemin_domain_identity")
    elif (
        isinstance(freeze_gate_key, (list, tuple))
        and len(freeze_gate_key) == 9
    ):
        # PT-1 stores NamedTuple keys as arrays; the typed d-447 identity is
        # the eighth field in _FlaggedFreezeGateCurveCacheKey.
        identity_payload = freeze_gate_key[7]
    else:
        identity_payload = None
    if isinstance(identity_payload, Mapping):
        band = identity_payload.get("certified_band")
        if isinstance(band, list):
            if len(band) != 2 or any(
                isinstance(bound, bool)
                or not isinstance(bound, (int, float))
                for bound in band
            ):
                return None
            try:
                band = tuple(float(bound) for bound in band)
            except (OverflowError, TypeError, ValueError):
                return None
        elif band is not None:
            return None
        silica_verdict = identity_payload.get("silica_verdict")
        projection_verdict = identity_payload.get(
            "projection_threshold_verdict"
        )
        refusal_reason = identity_payload.get("refusal_reason")
        if (
            not isinstance(silica_verdict, str)
            or not isinstance(projection_verdict, str)
            or (
                refusal_reason is not None
                and not isinstance(refusal_reason, str)
            )
        ):
            return None
        return MAGEMinDomainIdentity(
            silica_verdict,
            projection_verdict,
            band,
            refusal_reason,
        )
    return None


def canonical_physics_bucket_key(
    sim: Any,
    *,
    artifact: str,
    intent: ChemistryIntent,
    fO2_log: float | None,
    fe_redox_policy: str,
    provider_role: str | None = None,
    control_quantization: ControlQuantization | None = None,
    redox_state: RedoxState | None = None,
) -> dict[str, Any]:
    return canonical_physics_bucket_key_from_replay_key(
        canonical_replay_key(
            sim,
            artifact=artifact,
            intent=intent,
            fO2_log=fO2_log,
            fe_redox_policy=fe_redox_policy,
            provider_role=provider_role,
            control_quantization=control_quantization,
            redox_state=redox_state,
        )
    )


def canonical_physics_bucket_key_from_replay_key(
    key: Mapping[str, Any],
) -> dict[str, Any]:
    controls = key.get("controls", {})
    if not isinstance(controls, Mapping):
        controls = {}
    bucket_controls: dict[str, Any] = {
        "T_K": controls.get("T_K"),
        "pressure_bar": controls.get("pressure_bar"),
    }
    if _physics_bucket_consumes_log_fO2(key):
        bucket_controls["log_fO2"] = controls.get("log_fO2")
    if _physics_bucket_consumes_pO2_bar(key):
        bucket_controls["pO2_bar"] = controls.get("pO2_bar")

    sulfur_input_ppm = _sulfur_input_ppm_from_replay_key(key)
    bucket_sulfur: dict[str, Any] = {"S_input_ppm": sulfur_input_ppm}
    stage0_inventory = _stage0_inventory_digest_from_replay_key(key)
    if stage0_inventory is not None:
        bucket_sulfur["inventory"] = stage0_inventory
    replay_scope = replay_scope_dependency_vector(key)
    physics_bucket = {
        "namespace_id": str(key.get("namespace_id")),
        "composition_mol_fraction": _json_ready(
            key.get("composition_mol_fraction", [])
        ),
        "controls": bucket_controls,
        "sulfur": bucket_sulfur,
    }
    return {
        "physics_bucket": physics_bucket,
        "replay_scope": replay_scope,
    }


def canonical_physics_ladder_bucket_key_from_replay_key(
    key: Mapping[str, Any],
    rung_tag: str,
) -> dict[str, Any]:
    sig_figs = _physics_ladder_sig_figs(rung_tag)
    bucket = canonical_physics_bucket_key_from_replay_key(key)
    physics_bucket = copy.deepcopy(dict(bucket["physics_bucket"]))
    controls = dict(physics_bucket.get("controls", {}) or {})
    controls["T_K"] = _sigfig_snap(controls.get("T_K"), sig_figs)
    if _physics_ladder_coarsens_controls(rung_tag):
        if "pressure_bar" in controls:
            controls["pressure_bar"] = _sigfig_snap(
                controls.get("pressure_bar"),
                sig_figs,
            )
        if "pO2_bar" in controls:
            controls["pO2_bar"] = _sigfig_snap(controls.get("pO2_bar"), sig_figs)
    physics_bucket["controls"] = controls
    physics_bucket["composition_mol_fraction"] = [
        [str(species), _sigfig_snap(float(fraction), sig_figs)]
        for species, fraction in _composition_items(
            physics_bucket.get("composition_mol_fraction", [])
        )
    ]
    physics_bucket["precision_rung"] = {
        "tag": rung_tag,
        "composition_mol_fraction_sig_figs": sig_figs,
        "T_K_sig_figs": sig_figs,
        "controls": (
            "pressure-pO2-sigfig-log_fO2-exact"
            if _physics_ladder_coarsens_controls(rung_tag)
            else "c1-exact-pressure-redox-sulfur-namespace"
        ),
    }
    return {
        "physics_bucket": physics_bucket,
        "replay_scope": copy.deepcopy(dict(bucket["replay_scope"])),
    }


def physics_ladder_bucket_distance_from_replay_key(
    key: Mapping[str, Any],
    rung_tag: str,
) -> float:
    sig_figs = _physics_ladder_sig_figs(rung_tag)
    exact_composition = {
        species: float(fraction)
        for species, fraction in _composition_items(
            key.get("composition_mol_fraction", [])
        )
    }
    rung_key = canonical_physics_ladder_bucket_key_from_replay_key(key, rung_tag)
    rung_bucket = rung_key.get("physics_bucket", {})
    if not isinstance(rung_bucket, Mapping):
        rung_bucket = {}
    snapped_composition_rows = rung_bucket.get("composition_mol_fraction", [])
    if any(
        (
            component.get("mol_fraction")
            if isinstance(component, Mapping)
            else component[1]
        )
        is None
        for component in snapped_composition_rows
    ):
        return math.inf
    snapped_composition = _composition_items(
        snapped_composition_rows
    )
    distance = 0.0
    for species, snapped_fraction in snapped_composition:
        if snapped_fraction is None:
            return math.inf
        exact_fraction = exact_composition.get(species, 0.0)
        component_distance = _normalized_sigfig_distance(
            exact_fraction,
            float(snapped_fraction),
            sig_figs,
        )
        if not math.isfinite(component_distance):
            return math.inf
        distance += component_distance
        if not math.isfinite(distance):
            return math.inf
    controls = key.get("controls", {})
    if not isinstance(controls, Mapping):
        controls = {}
    rung_controls = rung_bucket.get("controls", {})
    if not isinstance(rung_controls, Mapping):
        rung_controls = {}
    if "T_K" in controls:
        exact_temperature = controls["T_K"]
        snapped_temperature = rung_controls.get("T_K")
        if exact_temperature is None or snapped_temperature is None:
            return math.inf
        try:
            temperature_distance = _normalized_sigfig_distance(
                float(exact_temperature),
                float(snapped_temperature),
                sig_figs,
            )
        except (OverflowError, TypeError, ValueError):
            return math.inf
        if not math.isfinite(temperature_distance):
            return math.inf
        distance += temperature_distance
    if _physics_ladder_coarsens_controls(rung_tag):
        for control_name in ("pressure_bar", "pO2_bar"):
            if control_name not in controls or control_name not in rung_controls:
                continue
            exact_value = controls.get(control_name)
            snapped_value = rung_controls.get(control_name)
            if exact_value is None or snapped_value is None:
                return math.inf
            try:
                control_distance = _normalized_sigfig_distance(
                    float(exact_value),
                    float(snapped_value),
                    sig_figs,
                )
            except (OverflowError, TypeError, ValueError):
                return math.inf
            if not math.isfinite(control_distance):
                return math.inf
            distance += control_distance
            if not math.isfinite(distance):
                return math.inf
    rounded_distance = float(round(distance, 15))
    return rounded_distance if math.isfinite(rounded_distance) else math.inf


def physics_control_rung_error_budget(
    query_key: Mapping[str, Any],
    source_key: Mapping[str, Any],
    rung_tag: str,
    *,
    source_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _physics_ladder_sig_figs(rung_tag)
    result: dict[str, Any] = {
        "term": CONTROL_RUNG_SIO_ERROR_BUDGET_TERM,
        "rung": rung_tag,
        "budget_relative_error": CONTROL_RUNG_SIO_RELATIVE_ERROR_BUDGET,
        "accepted": True,
        "refusal_reason": None,
    }
    if not _physics_ladder_coarsens_controls(rung_tag):
        result["relative_error"] = 0.0
        return result
    if not (
        _physics_bucket_consumes_pO2_bar(query_key)
        or _physics_bucket_consumes_pO2_bar(source_key)
    ):
        result["relative_error"] = 0.0
        return result

    query_pO2 = _control_float(query_key, "pO2_bar")
    source_pO2 = _control_float(source_key, "pO2_bar")
    result["query_pO2_bar"] = query_pO2
    result["source_pO2_bar"] = source_pO2
    if query_pO2 is None or source_pO2 is None or query_pO2 <= 0.0 or source_pO2 <= 0.0:
        result["accepted"] = False
        result["refusal_reason"] = "pO2_missing_or_nonpositive"
        result["relative_error"] = math.inf
        return result
    if _crosses_sio_po2_knee(query_pO2, source_pO2):
        result["accepted"] = False
        result["refusal_reason"] = "pO2_knee_crossing"
        result["relative_error"] = math.inf
        return result
    if _po2_bucket_spans_sio_knee(query_pO2, _physics_ladder_sig_figs(rung_tag)):
        result["accepted"] = False
        result["refusal_reason"] = "pO2_knee_bucket"
        result["relative_error"] = math.inf
        return result

    relative_error = abs(math.sqrt(query_pO2 / source_pO2) - 1.0)
    source_sio = _payload_sio_pressure(source_payload)
    if source_sio is not None:
        exact_estimate = source_sio * math.sqrt(source_pO2 / query_pO2)
        result["source_SiO_vapor_pressure_Pa"] = source_sio
        result["estimated_exact_SiO_vapor_pressure_Pa"] = exact_estimate
        result["absolute_error_Pa"] = abs(source_sio - exact_estimate)
    result["relative_error"] = float(relative_error)
    if relative_error > CONTROL_RUNG_SIO_RELATIVE_ERROR_BUDGET:
        result["accepted"] = False
        result["refusal_reason"] = "pO2_error_budget_exceeded"
    return result


def validate_reduced_real_equilibrium_record_key(
    artifact: str,
    key: Mapping[str, Any],
    payload: Mapping[str, Any] | None = None,
) -> None:
    if str(artifact) != "equilibrium_post_record":
        return
    namespace_id = str(key.get("namespace_id") or "")
    namespace_family = namespace_id.partition(":")[0]
    authority = payload.get("authority") if isinstance(payload, Mapping) else None
    if not isinstance(authority, Mapping):
        raise RuntimeError(
            "PT-1 reduced-real equilibrium_post_record rows require typed "
            "row authority metadata"
        )
    if authority.get("schema") != "reduced-real-authority-v1":
        raise RuntimeError(
            "PT-1 reduced-real equilibrium_post_record rows require "
            "reduced-real-authority-v1 metadata"
        )
    backend = authority.get("backend", {})
    if not isinstance(backend, Mapping):
        backend = {}
    typed_family = _row_validated_backend_family(backend)
    if typed_family is None:
        rejected_identity = (
            "internal-analytical"
            if _is_internal_analytical_backend_key(backend)
            else "unrecognized"
        )
        raise RuntimeError(
            "PT-1 reduced-real equilibrium_post_record rows require a "
            "row-validated real backend identity; self-asserted evidence_class, "
            "backend_family, and backend labels do not establish authority; "
            f"got {rejected_identity}."
        )
    expected_evidence = {
        RealBackendFamily.ALPHAMELTS: EvidenceClass.MELTS.value,
        RealBackendFamily.THERMOENGINE: EvidenceClass.MELTS.value,
        RealBackendFamily.MAGEMIN: EvidenceClass.MAGEMIN.value,
    }[typed_family]
    evidence_class = str(authority.get("evidence_class") or "")
    backend_family = str(authority.get("backend_family") or "")
    if (
        evidence_class != expected_evidence
        or backend_family != typed_family.value
        or namespace_family != typed_family.value
    ):
        raise RuntimeError(
            "PT-1 reduced-real equilibrium_post_record authority/namespace "
            "mismatch: "
            f"evidence={evidence_class!r}, backend_family={backend_family!r}, "
            f"validated_backend_family={typed_family.value!r}, "
            f"namespace={namespace_id!r}."
        )
    provider = authority.get("provider", {})
    if not isinstance(provider, Mapping):
        provider = {}
    if not _row_provider_matches_family(provider, typed_family):
        resolved_provider_id = str(
            provider.get("resolved_provider_id") or ""
        ).strip()
        raise RuntimeError(
            "PT-1 reduced-real equilibrium_post_record rows require the "
            f"validated {typed_family.value} provider; got "
            f"{resolved_provider_id or '<missing>'}."
        )


def _is_internal_analytical_backend_key(backend: Mapping[str, Any]) -> bool:
    return any(
        str(canonical_backend_class_name(backend.get(field, ""))).strip().split(".")[-1]
        == _INTERNAL_ANALYTICAL_BACKEND_RUNTIME_NAME
        for field in ("backend_name", "backend_class")
    )


def _row_validated_backend_family(
    backend: Mapping[str, Any],
) -> RealBackendFamily | None:
    """Resolve persisted authority only from closed, exact adapter identities."""

    identity = (
        str(backend.get("backend_name") or "").strip(),
        str(backend.get("backend_class") or "").strip(),
    )
    if identity == (_ALPHAMELTS_BACKEND_NAME, _ALPHAMELTS_BACKEND_CLASS):
        return RealBackendFamily.ALPHAMELTS
    if identity == (_THERMOENGINE_BACKEND_NAME, _THERMOENGINE_BACKEND_CLASS):
        return RealBackendFamily.THERMOENGINE
    if identity in _MAGEMIN_BACKEND_IDENTITIES:
        return RealBackendFamily.MAGEMIN
    return None


def _row_provider_matches_family(
    provider: Mapping[str, Any],
    family: RealBackendFamily,
) -> bool:
    """Require an exact provider/role tuple for the validated backend family."""

    resolved = str(provider.get("resolved_provider_id") or "").strip()
    role = str(provider.get("resolved_role") or "").strip()
    authoritative = str(
        provider.get("authoritative_provider_id") or ""
    ).strip()
    fallback = str(provider.get("fallback_provider_id") or "").strip()
    if family in {
        RealBackendFamily.ALPHAMELTS,
        RealBackendFamily.THERMOENGINE,
    }:
        allowed_resolved = {
            _ALPHAMELTS_PROVIDER_ID,
            _BUILTIN_BACKEND_EQUILIBRIUM_PROVIDER_ID,
        }
        return (
            resolved in allowed_resolved
            and role == "authoritative"
            and authoritative == resolved
            and fallback in {"", _MAGEMIN_SHADOW_PROVIDER_ID}
        )
    if family is RealBackendFamily.MAGEMIN:
        if resolved != _MAGEMIN_SHADOW_PROVIDER_ID:
            return False
        if role == "authoritative":
            return authoritative == _MAGEMIN_SHADOW_PROVIDER_ID and fallback in {
                "",
                _MAGEMIN_SHADOW_PROVIDER_ID,
            }
        if role == "fallback":
            return (
                authoritative
                in {
                    _ALPHAMELTS_PROVIDER_ID,
                    _BUILTIN_BACKEND_EQUILIBRIUM_PROVIDER_ID,
                }
                and fallback == _MAGEMIN_SHADOW_PROVIDER_ID
            )
    return False


def canonical_replay_output_projection(
    artifact: str,
    sim: Any,
    result: Any,
) -> dict[str, Any]:
    if artifact == "freeze_gate_curve":
        curve_payload = _curve_payload(
            result,
            include_liquidus_floor=False,
        )
        for domain_key in GATE_CURVE_DOMAIN_KEYS:
            curve_payload.pop(domain_key, None)
        return {"curve": curve_payload}
    if artifact != "equilibrium_post_record":
        raise ValueError(f"unsupported PT-1 replay artifact: {artifact}")
    transition = getattr(result, "ledger_transition", None)
    if transition is not None:
        raise PT0CacheCollision(
            "PT-0 equilibrium replay does not support cached ledger transitions"
        )
    intent = _equilibrium_payload_intent(sim)
    temperature_K = float(result.temperature_C) + CELSIUS_TO_KELVIN_OFFSET
    result_diagnostics = dict(getattr(result, "diagnostics", {}) or {})
    feo_activity = result_diagnostics.get("a_FeO_calphad")
    if isinstance(feo_activity, Mapping):
        captured_feo_activity = dict(feo_activity)
        captured_feo_activity.pop("notices", None)
        result_diagnostics["a_FeO_calphad"] = captured_feo_activity
    payload = {
        "authority": _equilibrium_record_authority(sim, intent),
        "equilibrium_result": {
            field.name: (
                _cache_payload_diagnostic(
                    (
                        result_diagnostics
                        if field.name == "diagnostics"
                        else getattr(result, field.name)
                    ),
                    temperature_K=temperature_K,
                )
                if field.name == "diagnostics"
                and isinstance(result_diagnostics, Mapping)
                else _json_ready(getattr(result, field.name))
            )
            for field in _equilibrium_result_payload_fields()
            if not (
                field.name in _EQUILIBRIUM_RESULT_FIELDS_OMITTED_WHEN_NONE
                and getattr(result, field.name) is None
            )
        },
    }
    payload[_EQUILIBRIUM_REPLAY_SIDE_FIELDS[0]] = dict(
        getattr(sim, "_last_vapor_pressures_source", {}) or {}
    )
    payload[_EQUILIBRIUM_REPLAY_SIDE_FIELDS[1]] = _cache_payload_diagnostic(
        getattr(sim, "_last_vapor_pressure_diagnostic", {}) or {},
        temperature_K=temperature_K,
    )
    sulfur = getattr(result, "sulfur_saturation", None)
    payload["equilibrium_result"]["sulfur_saturation"] = _json_ready(sulfur)
    if hasattr(result, "alphamelts_diagnostics"):
        alphamelts_diagnostics = getattr(result, "alphamelts_diagnostics")
        payload["alphamelts_diagnostics"] = (
            _cache_payload_diagnostic(
                alphamelts_diagnostics,
                temperature_K=temperature_K,
            )
            if isinstance(alphamelts_diagnostics, Mapping)
            else _json_ready(alphamelts_diagnostics)
        )
    return payload


def equilibrium_payload(sim: Any, result: EquilibriumResult) -> dict[str, Any]:
    return canonical_replay_output_projection(
        "equilibrium_post_record", sim, result
    )


def _equilibrium_record_authority(
    sim: Any,
    intent: ChemistryIntent,
) -> dict[str, Any]:
    """Persist typed, non-key authority evidence for admission and replay."""

    backend = _resolved_backend_for_cache_identity(sim, intent, None)
    backend_family = _typed_backend_family(backend)
    evidence_class = {
        "alphamelts": EvidenceClass.MELTS.value,
        "thermoengine": EvidenceClass.MELTS.value,
        "magemin": EvidenceClass.MAGEMIN.value,
    }.get(backend_family, EvidenceClass.INTERNAL_ANALYTICAL.value)
    return dict(
        zip(
            _EQUILIBRIUM_AUTHORITY_FIELDS,
            (
                "reduced-real-authority-v1",
                evidence_class,
                backend_family,
                _typed_backend_identity_for_authority(backend),
                _provider_identity(sim, intent),
                _provider_identity(sim, ChemistryIntent.VAPOR_PRESSURE),
            ),
        )
    )


def _cache_payload_diagnostic(
    value: Mapping[str, Any],
    *,
    temperature_K: float | None = None,
) -> dict[str, Any]:
    _validate_cache_inert_envelopes(value, temperature_K=temperature_K)
    return _json_ready(_strip_cache_inert_diagnostic_keys(value))


def _validate_cache_inert_envelopes(
    value: Any,
    *,
    temperature_K: float | None = None,
) -> None:
    if isinstance(value, Mapping):
        if has_melt_extrapolation_envelope(value):
            consume_melt_extrapolation_envelope(
                value,
                temperature_K=temperature_K,
            )
        for item in value.values():
            _validate_cache_inert_envelopes(
                item,
                temperature_K=temperature_K,
            )
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_cache_inert_envelopes(
                item,
                temperature_K=temperature_K,
            )


def _strip_cache_inert_diagnostic_keys(value: Any) -> Any:
    if isinstance(value, Mapping):
        has_melt_envelope = has_melt_extrapolation_envelope(value)
        return {
            str(key): _strip_cache_inert_diagnostic_keys(item)
            for key, item in value.items()
            if not _is_cache_inert_diagnostic_key(key)
            and not (has_melt_envelope and str(key) == "instrument_status")
        }
    if isinstance(value, (list, tuple)):
        return [_strip_cache_inert_diagnostic_keys(item) for item in value]
    return value


def _is_cache_inert_diagnostic_key(key: Any) -> bool:
    text = str(key)
    return (
        text in MELT_EXTRAPOLATION_ENVELOPE_FIELDS
        or text == "melt_regime_predicate_divergences"
        or text.endswith("_divergences")
    )


def equilibrium_from_payload(payload: Mapping[str, Any]) -> EquilibriumResult:
    data = dict(payload["equilibrium_result"])
    sulfur_data = data.pop("sulfur_saturation", None)
    data["ledger_transition"] = None
    result = EquilibriumResult(**data)
    if sulfur_data is not None:
        result.sulfur_saturation = SulfurSaturationResult(**dict(sulfur_data))
    consumes_melt_envelope = _payload_consumes_melt_envelope(payload, result)
    temperature_K = float(result.temperature_C) + CELSIUS_TO_KELVIN_OFFSET
    canonical_diagnostic = (
        melt_extrapolation_diagnostic(temperature_K, "MELTS-v1.0")
        if consumes_melt_envelope
        else None
    )
    if "alphamelts_diagnostics" in payload:
        alphamelts_diagnostic = payload["alphamelts_diagnostics"]
        if isinstance(alphamelts_diagnostic, Mapping):
            alphamelts_diagnostic = dict(alphamelts_diagnostic)
            if has_melt_extrapolation_envelope(alphamelts_diagnostic):
                consume_melt_extrapolation_envelope(
                    alphamelts_diagnostic,
                    temperature_K=temperature_K,
                )
            if canonical_diagnostic is not None:
                alphamelts_diagnostic.update(canonical_diagnostic)
                consume_melt_extrapolation_envelope(
                    alphamelts_diagnostic,
                    temperature_K=temperature_K,
                )
        setattr(result, "alphamelts_diagnostics", alphamelts_diagnostic)
    if canonical_diagnostic is not None:
        diagnostic = dict(result.diagnostics or {})
        if has_melt_extrapolation_envelope(diagnostic):
            consume_melt_extrapolation_envelope(
                diagnostic,
                temperature_K=temperature_K,
            )
        diagnostic.update(canonical_diagnostic)
        consume_melt_extrapolation_envelope(
            diagnostic,
            temperature_K=temperature_K,
        )
        result.diagnostics = diagnostic
    return result


def _decode_equilibrium_replay_payload(
    payload: Mapping[str, Any],
) -> _DecodedEquilibriumReplayPayload:
    result_data = payload.get("equilibrium_result")
    if not isinstance(result_data, Mapping):
        raise TypeError("equilibrium result must be a mapping")
    result_diagnostics = result_data.get("diagnostics")
    if result_diagnostics is not None and not isinstance(
        result_diagnostics, Mapping
    ):
        raise TypeError("equilibrium result diagnostics must be a mapping")
    authority = payload.get("authority")
    if authority is not None and (
        not isinstance(authority, Mapping)
        or any(not isinstance(key, str) for key in authority)
    ):
        raise TypeError("equilibrium replay authority must be a mapping")

    def matches_type(value: Any, annotation: Any) -> bool:
        if annotation is Any:
            return True
        if annotation is type(None):
            return value is None
        origin = get_origin(annotation)
        if origin in (Union, UnionType):
            return any(matches_type(value, option) for option in get_args(annotation))
        if origin is list:
            (item_type,) = get_args(annotation)
            return isinstance(value, list) and all(
                matches_type(item, item_type) for item in value
            )
        if origin is dict:
            key_type, value_type = get_args(annotation)
            return isinstance(value, Mapping) and all(
                matches_type(key, key_type)
                and matches_type(item, value_type)
                for key, item in value.items()
            )
        if annotation is float:
            return _is_finite_cache_number(value)
        if annotation is int:
            return type(value) is int
        if annotation is bool:
            return type(value) is bool
        if annotation is str:
            return isinstance(value, str)
        try:
            return isinstance(value, annotation)
        except TypeError:
            return False

    for field_name, value in result_data.items():
        field_type = _EQUILIBRIUM_RESULT_FIELD_TYPES.get(field_name)
        if field_type is not None and not matches_type(value, field_type):
            raise TypeError(
                f"equilibrium result field {field_name} has an invalid type"
            )
    result = equilibrium_from_payload(payload)
    source = payload[_EQUILIBRIUM_REPLAY_SIDE_FIELDS[0]]
    diagnostic = payload[_EQUILIBRIUM_REPLAY_SIDE_FIELDS[1]]
    if (
        not isinstance(source, Mapping)
        or not isinstance(diagnostic, Mapping)
        or any(not isinstance(key, str) for key in diagnostic)
    ):
        raise TypeError("equilibrium replay diagnostics must be mappings")
    if any(
        not isinstance(species, str) or not isinstance(source_id, str)
        for species, source_id in source.items()
    ):
        raise TypeError("equilibrium vapor pressure sources must map strings to strings")
    if "pO2_bar" in diagnostic:
        pressure = diagnostic["pO2_bar"]
        if pressure is not None and not matches_type(pressure, float):
            raise TypeError("equilibrium vapor diagnostic pO2_bar must be finite")
    alphamelts_diagnostics = payload.get("alphamelts_diagnostics")
    if alphamelts_diagnostics is not None and not isinstance(
        alphamelts_diagnostics, Mapping
    ):
        raise TypeError("AlphaMELTS replay diagnostics must be a mapping")
    if isinstance(alphamelts_diagnostics, Mapping) and any(
        not isinstance(key, str) for key in alphamelts_diagnostics
    ):
        raise TypeError("AlphaMELTS replay diagnostic keys must be strings")
    replay_diagnostic = dict(diagnostic)
    if replay_diagnostic and _payload_consumes_melt_envelope(payload, result):
        temperature_K = float(result.temperature_C) + CELSIUS_TO_KELVIN_OFFSET
        if has_melt_extrapolation_envelope(replay_diagnostic):
            consume_melt_extrapolation_envelope(
                replay_diagnostic,
                temperature_K=temperature_K,
            )
        replay_diagnostic.update(
            melt_extrapolation_diagnostic(
                temperature_K,
                "MELTS-v1.0",
            )
        )
        consume_melt_extrapolation_envelope(
            replay_diagnostic,
            temperature_K=temperature_K,
        )
    return _DecodedEquilibriumReplayPayload(
        result=result,
        vapor_pressures_source=dict(source),
        vapor_pressure_diagnostic=replay_diagnostic,
    )


def _payload_consumes_melt_envelope(
    payload: Mapping[str, Any],
    result: EquilibriumResult,
) -> bool:
    authority = payload.get("authority")
    if not isinstance(authority, Mapping):
        return False
    if str(authority.get("evidence_class") or "") != EvidenceClass.MELTS.value:
        return False
    diagnostic = result.diagnostics or {}
    return str(diagnostic.get("vapor_pressure_backend_status") or "") != (
        "not_attempted"
    )


def _normalized_status(value: Any) -> str:
    return str(value or "").strip().lower()


def _is_cacheable_equilibrium_result(result: EquilibriumResult) -> bool:
    status = _normalized_status(getattr(result, "status", "ok"))
    return status in _CACHEABLE_EQUILIBRIUM_STATUSES


def _is_cacheable_gate_curve(curve: Mapping[str, Any]) -> bool:
    status = _normalized_status(
        curve.get("status") or curve.get("backend_status")
    )
    if status and status not in _CACHEABLE_GATE_STATUSES:
        return False
    calibration_status = _normalized_status(curve.get("calibration_status"))
    if (
        calibration_status
        and calibration_status not in _CACHEABLE_GATE_CALIBRATION_STATUSES
    ):
        return False
    return True


def canonical_json_bytes(value: Any) -> bytes:
    return _canonical_json_bytes(value, allow_nonfinite=False)


def _payload_integrity_bytes(value: Any) -> bytes:
    """Canonicalize stored bytes for verification before shape admission."""
    return _canonical_json_bytes(value, allow_nonfinite=True)


def _canonical_json_bytes(value: Any, *, allow_nonfinite: bool) -> bytes:
    return json.dumps(
        _json_ready(value, allow_nonfinite=allow_nonfinite),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=allow_nonfinite,
    ).encode("utf-8")


def encode_repair_notices_json(
    notices: Sequence[Mapping[str, Any]],
) -> str:
    """Serialize typed notice mappings with the existing canonical JSON codec."""

    if not all(isinstance(notice, Mapping) for notice in notices):
        raise TypeError("PT-1 repair notices must be typed mappings")
    return canonical_json_bytes(list(notices)).decode("utf-8")


def decode_repair_notices_json(raw: str | None) -> list[dict[str, Any]] | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise PT1PersistentStoreCorrupt(
            "PT-1 repair_notices_json must be TEXT or NULL"
        )
    try:
        notices = json.loads(raw)
    except ValueError as exc:
        raise PT1PersistentStoreCorrupt(
            "PT-1 repair_notices_json is invalid JSON"
        ) from exc
    if not isinstance(notices, list) or not all(
        isinstance(notice, Mapping) for notice in notices
    ):
        raise PT1PersistentStoreCorrupt(
            "PT-1 repair_notices_json must contain a list of typed mappings"
        )
    if encode_repair_notices_json(notices) != raw:
        raise PT1PersistentStoreCorrupt(
            "PT-1 repair_notices_json is not canonical"
        )
    return [copy.deepcopy(dict(notice)) for notice in notices]


def validate_repair_notices_json(raw: str | None) -> str | None:
    decode_repair_notices_json(raw)
    return raw


def monotonic_repair_notices_json(
    existing: str | None,
    incoming: str | None,
) -> str | None:
    old_notices = decode_repair_notices_json(existing)
    new_notices = decode_repair_notices_json(incoming)
    if old_notices is None:
        return incoming
    if new_notices is None:
        return existing
    old_values = {canonical_json_bytes(notice) for notice in old_notices}
    new_values = {canonical_json_bytes(notice) for notice in new_notices}
    return incoming if old_values < new_values else existing


def record_repair_notice(sim: Any, notice: Mapping[str, Any]) -> None:
    """Append a typed repair notice to the runtime-only per-run collector."""

    if not isinstance(notice, Mapping):
        raise TypeError("repair notice must be a typed mapping")
    normalized = json.loads(canonical_json_bytes(notice).decode("utf-8"))
    notices = getattr(sim, REPAIR_NOTICE_COLLECTOR_ATTRIBUTE, None)
    if not isinstance(notices, list):
        notices = []
        setattr(sim, REPAIR_NOTICE_COLLECTOR_ATTRIBUTE, notices)
    encoded = canonical_json_bytes(normalized)
    if all(canonical_json_bytes(existing) != encoded for existing in notices):
        notices.append(normalized)


def _repair_notices_for_capture(
    sim: Any,
    notices: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    source = (
        getattr(sim, REPAIR_NOTICE_COLLECTOR_ATTRIBUTE, ())
        if notices is None
        else notices
    )
    if not isinstance(source, Sequence) or isinstance(source, (str, bytes)):
        raise TypeError("PT-1 repair notice collector must be a sequence")
    encoded = encode_repair_notices_json(source)
    return decode_repair_notices_json(encoded) or []


def _row_repair_notices(row: Mapping[str, Any] | sqlite3.Row) -> str | None:
    keys = row.keys()
    if "repair_notices_json" not in keys:
        return None
    raw = row["repair_notices_json"]
    if raw is not None and not isinstance(raw, str):
        raise PT1PersistentStoreCorrupt(
            "PT-1 repair_notices_json must be TEXT or NULL"
        )
    return raw


def _emit_repair_notices(
    sim: Any | None,
    groups: Sequence[str | None],
    *,
    approximation_cache_state: str | None = None,
) -> list[dict[str, Any]]:
    if sim is None:
        return []
    if not isinstance(getattr(sim, REPAIR_NOTICE_COLLECTOR_ATTRIBUTE, None), list):
        setattr(sim, REPAIR_NOTICE_COLLECTOR_ATTRIBUTE, [])
    collected: list[dict[str, Any]] = []
    seen: set[bytes] = set()
    legacy_history_unavailable = False
    for raw in groups:
        notices = decode_repair_notices_json(raw)
        if notices is None:
            legacy_history_unavailable = True
            continue
        for notice in notices:
            identity = canonical_json_bytes(notice)
            if identity not in seen:
                seen.add(identity)
                collected.append(notice)
    if legacy_history_unavailable:
        collected.append(dict(REPAIR_NOTICES_UNAVAILABLE_NOTICE))
    if approximation_cache_state is not None:
        collected.append(
            {
                "kind": "reduced_real_cache_approximation",
                "cache_state": approximation_cache_state,
            }
        )
    for notice in collected:
        record_repair_notice(sim, notice)
    return copy.deepcopy(collected)


def _curve_payload(
    curve: Mapping[str, Any],
    *,
    include_liquidus_floor: bool = True,
) -> dict[str, Any]:
    is_liquidus_floor = curve.get("is_liquidus_floor", False)
    if type(is_liquidus_floor) is not bool:
        raise PT0CacheCollision(
            "PT-0 gate-curve floor marker must be bool"
        )
    payload = {
        "source": str(curve.get("source", "")),
        "solidus_T_C": _json_ready(curve.get("solidus_T_C")),
        "liquidus_T_C": _json_ready(curve.get("liquidus_T_C")),
        "path": [
            {
                "temperature_C": _json_ready(point[0]),
                "liquid_fraction": _json_ready(point[1]),
            }
            for point in tuple(curve.get("path") or ())
        ],
    }
    if include_liquidus_floor:
        payload["is_liquidus_floor"] = is_liquidus_floor
    for notice_key in GATE_CURVE_IDENTITY_NOTICE_KEYS:
        notice = curve.get(notice_key)
        if isinstance(notice, Mapping):
            payload[notice_key] = _json_ready(notice)
    for domain_key in GATE_CURVE_DOMAIN_KEYS:
        value = curve.get(domain_key)
        if value is not None:
            payload[domain_key] = _json_ready(value)
    return payload


def _curve_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    is_liquidus_floor = payload.get("is_liquidus_floor")
    if not _curve_floor_marker_is_valid(payload, require_present=True):
        raise PT0CacheMiss(
            "PT-0 cached replay miss: gate-curve floor marker is missing or invalid"
        )
    if not isinstance(payload.get("source"), str):
        raise TypeError("gate-curve source must be a string")
    path_payload = payload.get("path")
    if (
        not isinstance(path_payload, (list, tuple))
        or isinstance(path_payload, (str, bytes))
        or not path_payload
    ):
        raise TypeError("measured gate-curve path must be a non-empty sequence")

    solidus = payload.get("solidus_T_C")
    liquidus = payload.get("liquidus_T_C")
    if (
        not _is_finite_cache_number(solidus)
        or not _is_finite_cache_number(liquidus)
    ):
        raise TypeError("gate-curve bounds must be finite numbers")
    path = []
    for point in path_payload:
        if (
            not isinstance(point, Mapping)
            or not _is_finite_cache_number(point.get("temperature_C"))
            or not _is_finite_cache_number(point.get("liquid_fraction"))
        ):
            raise TypeError("gate-curve path points must contain finite numbers")
        path.append(
            (
                float(point["temperature_C"]),
                float(point["liquid_fraction"]),
            )
        )
    curve = {
        "source": payload["source"],
        "solidus_T_C": float(solidus),
        "liquidus_T_C": float(liquidus),
        "path": tuple(path),
    }
    if is_liquidus_floor:
        curve["is_liquidus_floor"] = True
    for notice_key in GATE_CURVE_IDENTITY_NOTICE_KEYS:
        notice = payload.get(notice_key)
        if notice_key in payload:
            if not isinstance(notice, Mapping):
                raise TypeError(f"gate-curve {notice_key} must be a mapping")
            curve[notice_key] = dict(notice)
    step_down = curve.get("gate_provider_step_down")
    if step_down is not None:
        failed_providers = step_down.get("failed_providers")
        realized_provider = step_down.get("realized_provider")
        if (
            not isinstance(failed_providers, (list, tuple))
            or isinstance(failed_providers, (str, bytes))
            or any(not isinstance(item, Mapping) for item in failed_providers)
            or "realized_provider" not in step_down
            or (
                realized_provider is not None
                and not isinstance(realized_provider, str)
            )
            or type(step_down.get("magemin_sourced")) is not bool
        ):
            raise TypeError("gate-provider step-down record has invalid fields")
    for domain_key in GATE_CURVE_DOMAIN_KEYS:
        value = payload.get(domain_key)
        if domain_key not in payload:
            continue
        if domain_key == "authority":
            if not isinstance(value, str):
                raise TypeError("gate-curve authority must be a string")
            curve[domain_key] = value
        else:
            if not isinstance(value, Mapping):
                raise TypeError("gate-curve certified_band must be a mapping")
            curve[domain_key] = dict(value)
    return curve


def _gate_curve_provider_role(curve: Mapping[str, Any]) -> str | None:
    source = str(curve.get("source", ""))
    if ":fallback:" in source:
        return "fallback"
    if source.startswith("gate_liquid_fraction"):
        return "authoritative"
    return None


def _gate_provider_role_for_capture(
    sim: Any,
    curve: Mapping[str, Any],
) -> str:
    keyed_role = _gate_provider_role_for_key(sim)
    curve_role = _gate_curve_provider_role(curve)
    if curve_role is None:
        if keyed_role == "authoritative":
            raise PT0CacheCollision(
                "freeze gate curve source does not identify the "
                "authoritative provider; refusing cached-real capture"
            )
        return keyed_role
    if curve_role != keyed_role:
        if curve_role == "fallback" and _cached_real_config(sim) is None:
            return curve_role
        raise PT0CacheCollision(
            "freeze gate curve provider role mismatch: "
            f"keyed role={keyed_role}, curve source role={curve_role}"
        )
    return keyed_role


def _gate_provider_roles_for_replay(sim: Any) -> tuple[str, ...]:
    primary = _gate_provider_role_for_key(sim)
    roles = [primary]
    if primary == "authoritative" and _gate_fallback_provider_available_for_key(sim):
        roles.append("fallback")
    return tuple(dict.fromkeys(roles))


def _gate_fallback_provider_available_for_key(sim: Any) -> bool:
    _register_gate_providers_for_key(sim)
    registry = getattr(sim, "_chem_registry", None)
    if (
        registry is not None
        and registry.fallback_for(ChemistryIntent.GATE_LIQUID_FRACTION) is not None
    ):
        return True
    cached_real = _cached_real_provider_identity(
        sim,
        ChemistryIntent.GATE_LIQUID_FRACTION,
        provider_role="fallback",
        fallback_allowed=False,
    )
    return cached_real is not None


def _gate_provider_role_for_key(sim: Any) -> str:
    _register_gate_providers_for_key(sim)
    registry = getattr(sim, "_chem_registry", None)
    if (
        registry is not None
        and registry.authoritative_for(
            ChemistryIntent.GATE_LIQUID_FRACTION
        )
        is not None
    ):
        return "authoritative"
    if getattr(sim, '_liquid_fraction_provider', None) == 'magemin':
        return "fallback"
    authorized_name = _cached_real_authorized_backend_name(sim)
    if (
        _is_alphamelts_authorized_name(authorized_name)
        or _is_thermoengine_authorized_name(authorized_name)
    ):
        return "authoritative"
    return "fallback"


def _register_gate_providers_for_key(sim: Any) -> None:
    register_gate = getattr(
        sim, "_register_freeze_gate_liquid_fraction_providers", None
    )
    if callable(register_gate):
        register_gate()


def _composition_mol_fraction(
    sim: Any,
    *,
    sig_figs: int | None = None,
) -> list[tuple[str, float]]:
    cleaned = sim.atom_ledger.project_account_mol(_CLEANED_MELT_ACCOUNT)
    return _composition_mol_fraction_from_mol(cleaned, sig_figs=sig_figs)


def _composition_mol_fraction_from_mol(
    cleaned: Mapping[str, float],
    *,
    sig_figs: int | None = None,
) -> list[tuple[str, float]]:
    composition_sig_figs = sig_figs or _COMPOSITION_SIG_FIGS
    positive = {
        str(species): float(mol)
        for species, mol in dict(cleaned or {}).items()
        if float(mol) > _TRACE_CUTOFF
    }
    total = sum(positive.values())
    if total <= _TRACE_CUTOFF:
        return []
    result = []
    for species, mol in sorted(positive.items()):
        fraction = mol / total
        if fraction <= _TRACE_CUTOFF:
            continue
        result.append((species, _sigfig(fraction, composition_sig_figs)))
    return result


def canonicalized_composition_mol_by_account(
    composition_by_account: Mapping[str, Mapping[str, float]],
    *,
    sig_figs: int | None = None,
) -> dict[str, dict[str, float]]:
    canonical = {
        str(account): {
            str(species): float(mol)
            for species, mol in dict(species_mol or {}).items()
            if float(mol) > 0.0
        }
        for account, species_mol in dict(composition_by_account or {}).items()
    }
    cleaned = canonical.get(_CLEANED_MELT_ACCOUNT, {})
    fractions = _composition_mol_fraction_from_mol(cleaned, sig_figs=sig_figs)
    total_mol = sum(
        float(mol)
        for mol in cleaned.values()
        if float(mol) > _TRACE_CUTOFF
    )
    fraction_total = sum(fraction for _, fraction in fractions)
    if total_mol <= _TRACE_CUTOFF or fraction_total <= _TRACE_CUTOFF:
        canonical[_CLEANED_MELT_ACCOUNT] = {}
        return canonical
    canonical[_CLEANED_MELT_ACCOUNT] = {
        species: total_mol * float(fraction) / fraction_total
        for species, fraction in fractions
        if float(fraction) > 0.0
    }
    return canonical


def _fe_split(sim: Any, *, sig_figs: int | None = None) -> dict[str, float]:
    fractions = dict(_composition_mol_fraction(sim, sig_figs=sig_figs))
    return {
        "FeO": fractions.get("FeO", 0.0),
        "Fe2O3": fractions.get("Fe2O3", 0.0),
    }


def _provider_identity(
    sim: Any,
    intent: ChemistryIntent,
    *,
    provider_role: str | None = None,
) -> dict[str, Any]:
    registry = getattr(sim, "_chem_registry", None)
    auth = registry.authoritative_for(intent) if registry is not None else None
    fallback = registry.fallback_for(intent) if registry is not None else None
    fallback_allowed = False
    kernel = getattr(sim, "_chem_kernel", None)
    if kernel is not None:
        fallback_allowed = intent in getattr(kernel, "allow_fallback_intents", ())
    resolved = auth
    role = "authoritative"
    if provider_role == "fallback" and fallback is not None:
        resolved = fallback
        role = "fallback"
    elif provider_role == "authoritative":
        resolved = auth
        role = "authoritative"
    elif resolved is None and fallback is not None and fallback_allowed:
        resolved = fallback
        role = "fallback"
    if resolved is None:
        cached_real = _cached_real_provider_identity(
            sim,
            intent,
            provider_role=provider_role,
            fallback_allowed=fallback_allowed,
        )
        if cached_real is not None:
            return cached_real
    return _provider_authority_payload(
        resolved_provider_id=_provider_id(resolved),
        resolved_role=role if resolved is not None else "none",
        authoritative_provider_id=_provider_id(auth),
        fallback_provider_id=_provider_id(fallback),
        fallback_allowed=bool(fallback_allowed),
        model=_provider_model(resolved),
        mode=_provider_mode(resolved),
    )


def _engine_version_provenance(
    sim: Any,
    intent: ChemistryIntent,
    *,
    provider_role: str | None = None,
) -> str | None:
    from simulator.engine_binding_admission import pt1_version_provenance

    backend = getattr(sim, "backend", None)
    config = _cached_real_config(sim)
    if config is not None:
        authorized_name = str(
            getattr(config, "authorized_backend_name", "")
        ).strip()
        if (
            _is_alphamelts_authorized_name(authorized_name)
            or _is_thermoengine_authorized_name(authorized_name)
        ):
            version = str(
                getattr(config, "authorized_backend_version", "")
            ).strip()
            return pt1_version_provenance(lambda: version)

    live_backend = getattr(backend, "_live_backend", None)
    if live_backend is not None:
        return _backend_version_for_key(live_backend)

    registry = getattr(sim, "_chem_registry", None)
    provider = registry.authoritative_for(intent) if registry is not None else None
    if provider_role == "fallback" and registry is not None:
        provider = registry.fallback_for(intent)
    provider_version = _provider_engine_version(provider)
    if provider_version:
        return provider_version
    return _backend_version_for_key(backend)


def _cached_real_provider_identity(
    sim: Any,
    intent: ChemistryIntent,
    *,
    provider_role: str | None,
    fallback_allowed: bool,
) -> dict[str, Any] | None:
    config = _cached_real_config(sim)
    if config is None:
        return None
    authorized_name = str(
        getattr(config, "authorized_backend_name", "")
    ).strip()
    is_alphamelts = _is_alphamelts_authorized_name(authorized_name)
    is_thermoengine = _is_thermoengine_authorized_name(authorized_name)
    if not (is_alphamelts or is_thermoengine):
        return None
    alphamelts_intents = {
        ChemistryIntent.SILICATE_LIQUIDUS,
        ChemistryIntent.SILICATE_EQUILIBRIUM,
        ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION,
        ChemistryIntent.GATE_LIQUID_FRACTION,
    }
    if intent not in alphamelts_intents:
        return None
    if intent == ChemistryIntent.GATE_LIQUID_FRACTION and provider_role == "fallback":
        return _provider_authority_payload(
            resolved_provider_id=_MAGEMIN_SHADOW_PROVIDER_ID,
            resolved_role="fallback",
            authoritative_provider_id=None,
            fallback_provider_id=_MAGEMIN_SHADOW_PROVIDER_ID,
            fallback_allowed=bool(fallback_allowed),
            model="MAGEMinShadowProvider",
            mode="subprocess",
        )
    fallback_provider_id = (
        _MAGEMIN_SHADOW_PROVIDER_ID
        if intent == ChemistryIntent.GATE_LIQUID_FRACTION
        else None
    )
    authorized_model = str(
        getattr(config, "authorized_model", "")
    ).strip()
    authorized_mode = str(
        getattr(config, "authorized_mode", "")
    ).strip() or (
        _THERMOENGINE_DEFAULT_MODE
        if is_thermoengine
        else _ALPHAMELTS_DEFAULT_MODE
    )
    if is_alphamelts and authorized_mode == _ALPHAMELTS_DEFAULT_MODE:
        authorized_model, _ = resolve_alphamelts_subprocess_model(
            authorized_model
        )
    else:
        authorized_model = authorized_model or DEFAULT_ALPHAMELTS_MODEL
    return _provider_authority_payload(
        resolved_provider_id=_ALPHAMELTS_PROVIDER_ID,
        resolved_role="authoritative",
        authoritative_provider_id=_ALPHAMELTS_PROVIDER_ID,
        fallback_provider_id=fallback_provider_id,
        fallback_allowed=bool(fallback_allowed),
        model=authorized_model,
        mode=authorized_mode,
    )


def _provider_id(provider: Any) -> str | None:
    if provider is None:
        return None
    profile = provider.capability_profile()
    return str(profile.provider_id)


def _provider_model(provider: Any) -> str | None:
    if provider is None:
        return None
    backend = getattr(provider, "_backend", None)
    if (
        getattr(backend, "real_backend_family", None)
        == RealBackendFamily.ALPHAMELTS
        and str(getattr(backend, "_mode", "")).strip() == "subprocess"
    ):
        model, _ = resolve_alphamelts_subprocess_model(
            getattr(backend, "_model", None)
        )
        return model
    model = getattr(backend, "_model", None)
    if model is not None:
        model_text = str(model).strip()
        if model_text:
            return model_text
    if (
        getattr(backend, "real_backend_family", None)
        == RealBackendFamily.THERMOENGINE
    ):
        return DEFAULT_ALPHAMELTS_MODEL
    return str(getattr(provider, "name", type(provider).__name__))


def _provider_mode(provider: Any) -> str | None:
    if provider is None:
        return None
    backend = getattr(provider, "_backend", None)
    mode = getattr(backend, "_mode", None)
    if mode is not None:
        mode_text = str(mode).strip()
        if mode_text:
            return mode_text
    if _provider_id(provider) == _MAGEMIN_SHADOW_PROVIDER_ID:
        return "subprocess"
    return str(type(provider).__name__)


def _cache_key_engine_version(
    sim: Any,
    intent: ChemistryIntent,
    *,
    provider_identity: Mapping[str, Any],
    provider_role: str | None = None,
) -> str | None:
    if not _is_alphamelts_key_identity(sim, provider_identity):
        return None
    return _engine_version_provenance(sim, intent, provider_role=provider_role)


def _is_alphamelts_key_identity(
    sim: Any,
    provider_identity: Mapping[str, Any],
) -> bool:
    provider_ids = {
        str(provider_identity.get("resolved_provider_id") or ""),
        str(provider_identity.get("authoritative_provider_id") or ""),
    }
    if _ALPHAMELTS_PROVIDER_ID in provider_ids:
        return True
    # A RESOLVED non-alphamelts provider (e.g. the magemin-shadow gate fallback) is NOT an
    # alphamelts key identity even when the cached-real config's authorized backend is
    # alphamelts. Do not fold engine_version into its key — that would change a non-alphamelts
    # cache identity (magemin-shadow / internal-analytical), which must stay
    # byte-identical.
    resolved = str(provider_identity.get("resolved_provider_id") or "").strip()
    if resolved and resolved != _ALPHAMELTS_PROVIDER_ID:
        return False
    backend = getattr(sim, "backend", None)
    live_backend = getattr(backend, "_live_backend", None)
    if _is_alphamelts_backend(live_backend) or _is_alphamelts_backend(backend):
        return True
    config = _cached_real_config(sim)
    if config is None:
        return False
    authorized_name = str(getattr(config, "authorized_backend_name", "")).strip()
    return _is_alphamelts_authorized_name(authorized_name)


def _is_alphamelts_authorized_name(value: Any) -> bool:
    text = str(value or "").strip().lower()
    leaf = text.rsplit(".", 1)[-1]
    return text == _ALPHAMELTS_AUTHORIZED_NAME or leaf == "alphameltsbackend"


def _provider_engine_version(provider: Any) -> str | None:
    from simulator.engine_binding_admission import pt1_version_provenance

    if provider is None:
        return pt1_version_provenance(
            lambda: (_ for _ in ()).throw(AttributeError("version getter missing"))
        )
    getter = getattr(provider, "_engine_version", None)
    if callable(getter):
        return pt1_version_provenance(getter)
    backend = getattr(provider, "_backend", None)
    getter = getattr(backend, "get_engine_version", None)
    if callable(getter):
        return pt1_version_provenance(getter)
    return pt1_version_provenance(
        lambda: (_ for _ in ()).throw(AttributeError("version getter missing"))
    )


def _equilibrium_payload_intent(sim: Any) -> ChemistryIntent:
    registry = getattr(sim, "_chem_registry", None)
    if (
        registry is not None
        and registry.authoritative_for(
            ChemistryIntent.SILICATE_EQUILIBRIUM
        )
        is not None
    ):
        return ChemistryIntent.SILICATE_EQUILIBRIUM
    authorized_name = _cached_real_authorized_backend_name(sim)
    if (
        _is_alphamelts_authorized_name(authorized_name)
        or _is_thermoengine_authorized_name(authorized_name)
    ):
        return ChemistryIntent.SILICATE_EQUILIBRIUM
    return ChemistryIntent.BACKEND_EQUILIBRIUM


def _sulfsat_identity(gate: Any) -> dict[str, Any]:
    return {
        "sulfsat_provider_selection": type(gate).__name__,
        "sulfsat_available": _sulfsat_available(gate),
    }


def _sulfsat_available(gate: Any) -> bool:
    checker = getattr(gate, "is_available", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:  # noqa: BLE001 - diagnostic only
            return False
    return False


def _cached_real_config(sim: Any) -> Any | None:
    backend = getattr(sim, "backend", None)
    if type(backend).__name__ != "CachedRealBackend":
        return None
    return getattr(backend, "config", None)


def _cached_real_authorized_backend_name(sim: Any) -> str | None:
    config = _cached_real_config(sim)
    if config is None:
        return None
    name = str(getattr(config, "authorized_backend_name", "")).strip().lower()
    return name or None


def _data_digests(sim: Any) -> dict[str, str]:
    return {
        "setpoints": functional_data_yaml_digest(getattr(sim, "setpoints", {})),
        "feedstocks": _digest(getattr(sim, "feedstocks", {})),
        "vapor_pressures": functional_data_yaml_digest(
            getattr(sim, "vapor_pressures", {})
        ),
        "species_formula_registry": _digest(
            getattr(sim, "species_formula_registry", {})
        ),
    }


def _solver_data_digests_from_key(key: Mapping[str, Any]) -> dict[str, Any]:
    data_digests = key.get("data_digests", {})
    if not isinstance(data_digests, Mapping):
        data_digests = {}
    return {
        name: data_digests.get(name)
        for name in (
            "setpoints",
            "feedstocks",
            "vapor_pressures",
            "species_formula_registry",
        )
        if data_digests.get(name) is not None
    }


def _stage0_inventory_digest_from_replay_key(key: Mapping[str, Any]) -> Any | None:
    sulfur_side = key.get("sulfur_side", {})
    if not isinstance(sulfur_side, Mapping):
        return None
    return sulfur_side.get("inventory")


def _sulfur_input_ppm_from_replay_key(key: Mapping[str, Any]) -> float:
    sulfur_side = key.get("sulfur_side", {})
    if not isinstance(sulfur_side, Mapping):
        sulfur_side = {}
    try:
        value = float(sulfur_side.get("S_input_ppm", 0.0) or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    return float(_sigfig(value, 6) or 0.0)


def _physics_bucket_consumes_log_fO2(key: Mapping[str, Any]) -> bool:
    return key.get("controls") is not None


def _physics_bucket_consumes_pO2_bar(key: Mapping[str, Any]) -> bool:
    return bool(key.get("vapor_pressure_provider_selection"))


def _control_float(key: Mapping[str, Any], control_name: str) -> float | None:
    controls = key.get("controls", {})
    if not isinstance(controls, Mapping):
        return None
    value = controls.get(control_name)
    if value is None or not is_declared_real_scalar(
        value,
        allow_numeric_str=True,
    ):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _crosses_sio_po2_knee(left: float, right: float) -> bool:
    low = min(float(left), float(right))
    high = max(float(left), float(right))
    return low < CONTROL_RUNG_SIO_PO2_KNEE_BAR < high


def _po2_bucket_spans_sio_knee(pO2_bar: float, sig_figs: float) -> bool:
    center = _sigfig_snap(pO2_bar, sig_figs)
    if center is None:
        return True
    quantum = _sigfig_quantum(float(pO2_bar), sig_figs)
    low = float(center) - 0.5 * quantum
    high = float(center) + 0.5 * quantum
    return low < CONTROL_RUNG_SIO_PO2_KNEE_BAR < high


def _payload_sio_pressure(payload: Mapping[str, Any] | None) -> float | None:
    if not isinstance(payload, Mapping):
        return None
    result = payload.get("equilibrium_result", {})
    if not isinstance(result, Mapping):
        return None
    vapor_pressures = result.get("vapor_pressures_Pa", {})
    if not isinstance(vapor_pressures, Mapping):
        return None
    value = vapor_pressures.get("SiO")
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0.0:
        return None
    return number


def _replay_scope_hash(physics_bucket_key: Mapping[str, Any]) -> str:
    return _digest(physics_bucket_key.get("replay_scope", {}))


def _physics_ladder_values_from_replay_key(
    key: Mapping[str, Any],
) -> dict[str, dict[str, float | str]]:
    values: dict[str, dict[str, float | str]] = {}
    for rung_tag, _sig_figs in PHYSICS_BUCKET_ALL_LADDER_RUNGS:
        rung_key = canonical_physics_ladder_bucket_key_from_replay_key(
            key,
            rung_tag,
        )
        values[rung_tag] = {
            "sha256": _sha256(canonical_json_bytes(rung_key)),
            "distance": physics_ladder_bucket_distance_from_replay_key(
                key,
                rung_tag,
            ),
        }
    return values


def _replay_key_composition_shape(
    key: Mapping[str, Any],
) -> tuple[list[tuple[str, float]] | None, str | None]:
    """Return normalized composition when it is finite and nonnegative."""

    raw_composition = key.get("composition_mol_fraction", [])
    if not isinstance(raw_composition, (list, tuple)):
        return None, "composition_mol_fraction"
    composition: list[tuple[str, float]] = []
    for component in raw_composition:
        if isinstance(component, Mapping):
            species = component.get("species")
            raw_fraction = component.get("mol_fraction")
        elif isinstance(component, (list, tuple)) and len(component) == 2:
            species, raw_fraction = component
        else:
            return None, "composition_mol_fraction"
        if not isinstance(species, str) or not species or not is_declared_real_scalar(
            raw_fraction
        ):
            return None, "composition_mol_fraction"
        try:
            fraction = float(raw_fraction)
        except (OverflowError, TypeError, ValueError):
            return None, "composition_mol_fraction"
        if not math.isfinite(fraction) or fraction < 0.0:
            return None, "composition_mol_fraction"
        composition.append((species, fraction))
    return composition, None


def _replay_key_control_shape(
    key: Mapping[str, Any],
) -> tuple[dict[str, float] | None, str | None]:
    """Return finite numeric controls with string names, or the bad field."""

    raw_controls = key.get("controls", {})
    if not isinstance(raw_controls, Mapping):
        return None, "controls"
    controls: dict[str, float] = {}
    for name, value in raw_controls.items():
        if not isinstance(name, str):
            return None, "controls"
        if not is_declared_real_scalar(value):
            return None, f"controls.{name}"
        try:
            number = float(value)
        except (OverflowError, TypeError, ValueError):
            return None, f"controls.{name}"
        if not math.isfinite(number):
            return None, f"controls.{name}"
        controls[name] = number
    return controls, None


def _ladder_indexability(
    key: Mapping[str, Any],
    *,
    rung_tag: str | None = None,
) -> UncacheableKey | None:
    composition, composition_error = _replay_key_composition_shape(key)
    first_rung = rung_tag or PHYSICS_BUCKET_ALL_LADDER_RUNGS[0][0]
    if composition_error is not None or composition is None:
        return UncacheableKey(
            first_rung,
            composition_error or "composition_mol_fraction",
        )

    controls, controls_error = _replay_key_control_shape(key)
    if controls_error is not None or controls is None:
        return UncacheableKey(first_rung, controls_error or "controls")

    rungs = (
        ((rung_tag, _physics_ladder_sig_figs(rung_tag)),)
        if rung_tag is not None
        else PHYSICS_BUCKET_ALL_LADDER_RUNGS
    )
    for current_rung, sig_figs in rungs:
        for species, fraction in composition:
            if _sigfig_snap(fraction, sig_figs) is None:
                return UncacheableKey(
                    current_rung,
                    f"composition_mol_fraction.{species}",
                )
        snapped_controls = ["T_K"] if "T_K" in controls else []
        if _physics_ladder_coarsens_controls(current_rung):
            if "pressure_bar" in controls:
                snapped_controls.append("pressure_bar")
            if (
                "pO2_bar" in controls
                and _physics_bucket_consumes_pO2_bar(key)
            ):
                snapped_controls.append("pO2_bar")
        for name in snapped_controls:
            if _sigfig_snap(controls[name], sig_figs) is None:
                return UncacheableKey(current_rung, f"controls.{name}")
        try:
            distance = physics_ladder_bucket_distance_from_replay_key(
                key,
                current_rung,
            )
        except (OverflowError, TypeError, ValueError, ZeroDivisionError):
            return UncacheableKey(current_rung, "distance")
        if not math.isfinite(distance):
            return UncacheableKey(current_rung, "distance")
    return None


def _physics_ladder_sig_figs(rung_tag: str) -> float:
    for candidate_tag, sig_figs in PHYSICS_BUCKET_ALL_LADDER_RUNGS:
        if rung_tag == candidate_tag:
            return float(sig_figs)
    raise ValueError(f"unknown physics bucket precision rung: {rung_tag!r}")


def _physics_ladder_coarsens_controls(rung_tag: str) -> bool:
    return any(
        rung_tag == candidate_tag
        for candidate_tag, _sig_figs in PHYSICS_BUCKET_CONTROL_LADDER_RUNGS
    )


def _physics_ladder_hash_column(rung_tag: str) -> str:
    _physics_ladder_sig_figs(rung_tag)
    return f"physics_bucket_{rung_tag}_sha256"


def _physics_ladder_distance_column(rung_tag: str) -> str:
    _physics_ladder_sig_figs(rung_tag)
    return f"physics_bucket_{rung_tag}_distance"


def _composition_items(value: Any) -> list[tuple[str, float]]:
    items: list[tuple[str, float]] = []
    for item in value or []:
        if isinstance(item, Mapping):
            species = item.get("species")
            fraction = item.get("mol_fraction")
        else:
            species, fraction = item
        items.append((str(species), float(fraction)))
    return sorted(items)


@lru_cache(maxsize=1)
def _source_module_digest() -> dict[str, Any]:
    root = _repo_root()
    rel_paths = _source_module_paths(root)
    file_digests = []
    for rel_path in rel_paths:
        file_digests.append(
            {
                "path": rel_path,
                "sha256": _sha256((root / rel_path).read_bytes()),
            }
        )
    return {
        "module_set": _SOURCE_MODULE_SET_ID,
        "algorithm": "sha256",
        "paths": list(rel_paths),
        "sha256": _sha256(canonical_json_bytes(file_digests)),
    }


def _source_module_paths(root: Path) -> tuple[str, ...]:
    rel_paths: set[str] = set()
    missing: list[str] = []
    for pattern in _SOURCE_MODULE_PATTERNS:
        matches = sorted(root.glob(pattern))
        if not matches:
            missing.append(pattern)
            continue
        rel_paths.update(path.relative_to(root).as_posix() for path in matches)
    if missing:
        raise RuntimeError(
            "PT-0 source digest module set has missing patterns: "
            + ", ".join(missing)
        )
    return tuple(sorted(rel_paths))


def _code_version() -> str:
    root = _repo_root()
    path = root / "VERSION"
    if path.exists():
        return path.read_text().strip()
    return "unknown"


def _git_dirty() -> int:
    root = _repo_root()
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--short"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:  # noqa: BLE001 - diagnostic metadata only
        return 1
    return 1 if result.stdout.strip() else 0


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _none_or_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _qualified_type(value: Any) -> str:
    if value is None:
        return "None"
    cls = type(value)
    return f"{cls.__module__}.{cls.__qualname__}"


def _backend_identity_for_key(sim: Any) -> dict[str, str]:
    backend = getattr(sim, "backend", None)
    config = getattr(backend, "config", None)
    if type(backend).__name__ == "CachedRealBackend" and config is not None:
        authorized_name = str(
            getattr(config, "authorized_backend_name", "")
        ).strip()
        if not authorized_name:
            raise RuntimeError(
                "cached-real cache key requires configured authorized backend name"
            )
        return _authorized_backend_identity_for_key(authorized_name)
    return {
        "backend_name": _backend_name_for_key(backend),
        "backend_class": _backend_class_for_key(backend),
    }


def _typed_backend_family(backend: Any) -> str:
    """Return a closed engine family without consulting provider labels."""

    if backend is None:
        return "internal-analytical"
    live_backend = getattr(backend, "_live_backend", None)
    if live_backend is not None and live_backend is not backend:
        return _typed_backend_family(live_backend)
    if isinstance(backend, RealBackendAuthority):
        family = getattr(backend, "real_backend_family", None)
        if isinstance(family, RealBackendFamily):
            return family.value
    return "internal-analytical"


def _typed_backend_identity_for_authority(backend: Any) -> dict[str, str]:
    """Serialize a canonical identity chosen only by typed backend authority."""

    family = _typed_backend_family(backend)
    if family == RealBackendFamily.ALPHAMELTS.value:
        identity = (_ALPHAMELTS_BACKEND_NAME, _ALPHAMELTS_BACKEND_CLASS)
    elif family == RealBackendFamily.THERMOENGINE.value:
        identity = (_THERMOENGINE_BACKEND_NAME, _THERMOENGINE_BACKEND_CLASS)
    elif family == RealBackendFamily.MAGEMIN.value:
        identity = (
            "MAGEMinBackend",
            "simulator.melt_backend.magemin.MAGEMinBackend",
        )
    else:
        identity = (
            _INTERNAL_ANALYTICAL_BACKEND_SERIALIZED_NAME,
            _INTERNAL_ANALYTICAL_BACKEND_SERIALIZED_CLASS,
        )
    return dict(zip(_BACKEND_AUTHORITY_FIELDS, identity))


def _authorized_backend_identity_for_key(
    authorized_name: str,
) -> dict[str, str]:
    name = str(authorized_name).strip()
    if _is_alphamelts_authorized_name(name):
        return {
            "backend_name": _ALPHAMELTS_BACKEND_NAME,
            "backend_class": _ALPHAMELTS_BACKEND_CLASS,
        }
    if _is_thermoengine_authorized_name(name):
        return {
            'backend_name': _THERMOENGINE_BACKEND_NAME,
            'backend_class': _THERMOENGINE_BACKEND_CLASS,
        }
    return {
        "backend_name": name,
        "backend_class": name,
    }


def _backend_name_for_key(backend: Any) -> str:
    if _is_alphamelts_backend(backend):
        return _ALPHAMELTS_BACKEND_NAME
    if _is_thermoengine_backend(backend):
        return _THERMOENGINE_BACKEND_NAME
    if type(backend).__name__ == _INTERNAL_ANALYTICAL_BACKEND_RUNTIME_NAME:
        return _INTERNAL_ANALYTICAL_BACKEND_SERIALIZED_NAME
    return type(backend).__name__


def _backend_class_for_key(backend: Any) -> str:
    if _is_alphamelts_backend(backend):
        return _ALPHAMELTS_BACKEND_CLASS
    if _is_thermoengine_backend(backend):
        return _THERMOENGINE_BACKEND_CLASS
    if type(backend).__name__ == _INTERNAL_ANALYTICAL_BACKEND_RUNTIME_NAME:
        return _INTERNAL_ANALYTICAL_BACKEND_SERIALIZED_CLASS
    return _qualified_type(backend)


def _backend_version_for_key(backend: Any) -> str:
    from simulator.engine_binding_admission import pt1_version_provenance

    getter = getattr(backend, "get_engine_version", None)
    if callable(getter):
        return pt1_version_provenance(getter)
    return pt1_version_provenance(
        lambda: (_ for _ in ()).throw(AttributeError("version getter missing"))
    )


def _is_alphamelts_backend(backend: Any) -> bool:
    if backend is None:
        return False
    if bool(getattr(backend, '_legacy_alphamelts_cache_identity', False)):
        return True
    return any(
        cls.__name__ == _ALPHAMELTS_BACKEND_NAME
        for cls in type(backend).__mro__
    )


def _is_thermoengine_backend(backend: Any) -> bool:
    if backend is None or bool(
        getattr(backend, '_legacy_alphamelts_cache_identity', False)
    ):
        return False
    declared_name = (
        getattr(backend, 'backend_name', None)
        or getattr(backend, 'name', None)
    )
    if str(declared_name or '').strip().lower() == _THERMOENGINE_AUTHORIZED_NAME:
        return True
    return any(
        cls.__name__ == _THERMOENGINE_BACKEND_NAME
        for cls in type(backend).__mro__
    )


def _is_thermoengine_authorized_name(value: Any) -> bool:
    text = str(value or '').strip().lower()
    leaf = text.rsplit('.', 1)[-1]
    return text == _THERMOENGINE_AUTHORIZED_NAME or leaf == (
        _THERMOENGINE_BACKEND_NAME.lower()
    )


def _positive_float_map(value: Mapping[str, Any]) -> dict[str, float]:
    return {
        str(key): float(item)
        for key, item in dict(value or {}).items()
        if item is not None and float(item) > 0.0
    }


def validated_rekey_corpus_version(target_corpus_version: str | None) -> str:
    from simulator.corpus_version import (
        current_corpus_version,
        interoperable_corpus_versions,
    )

    target = (target_corpus_version or current_corpus_version()).strip()
    if not target:
        raise SystemExit("target corpus version must be non-empty")
    allowed = frozenset(interoperable_corpus_versions())
    if target not in allowed:
        raise SystemExit(
            "target corpus version is not declared interoperable in "
            f"data/corpus_version.yaml: {target!r}"
        )
    return target


def _rekey_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def rekey_row_matches_engine(key: Mapping[str, Any], engine: str) -> bool:
    engine_key = str(engine or "").strip().lower()
    if not engine_key:
        return True
    backend = _rekey_dict(key.get("backend"))
    provider = _rekey_dict(key.get("provider"))
    fields = (
        backend.get("backend_name"),
        backend.get("backend_class"),
        provider.get("resolved_provider_id"),
        provider.get("authoritative_provider_id"),
        provider.get("fallback_provider_id"),
        provider.get("model"),
        key.get("engine_version"),
    )
    return any(engine_key in str(value or "").lower() for value in fields)


def rekey_engine_version_provenance(
    key: Mapping[str, Any],
    row_engine_version: Any,
) -> str | None:
    if row_engine_version not in (None, ""):
        return str(row_engine_version)
    provider = _rekey_dict(key.get("provider"))
    backend = _rekey_dict(key.get("backend"))
    for value in (
        key.get("engine_version"),
        provider.get("engine_version"),
        backend.get("backend_version"),
    ):
        if value not in (None, ""):
            return str(value)
    return None


def replace_rekey_cache_identity(
    key: dict[str, Any],
    target_corpus_version: str,
) -> bool:
    changed = False
    for field in ("engine_version", "source_module_digest", "code_version"):
        if field in key:
            key.pop(field, None)
            changed = True
    if key.get("corpus_version") != target_corpus_version:
        key["corpus_version"] = target_corpus_version
        changed = True

    backend = key.get("backend")
    if isinstance(backend, dict):
        if "backend_version" in backend:
            backend.pop("backend_version", None)
            changed = True
        if backend.get("corpus_version") != target_corpus_version:
            backend["corpus_version"] = target_corpus_version
            changed = True

    for section in ("provider", "vapor_pressure_provider"):
        provider = key.get(section)
        if isinstance(provider, dict) and "engine_version" in provider:
            provider.pop("engine_version", None)
            changed = True
    return changed


def cache_row_needs_rekey(
    key: Mapping[str, Any], target_corpus_version: str
) -> bool:
    from simulator.engine_local_config import is_legacy_cache_version

    if key.get("corpus_version") != target_corpus_version:
        return True
    if "source_module_digest" in key:
        return True
    if "code_version" in key:
        return True
    if is_legacy_cache_version(str(key.get("engine_version") or "")):
        return True
    if "engine_version" in key:
        return True
    backend = _rekey_dict(key.get("backend"))
    provider = _rekey_dict(key.get("provider"))
    vapor_provider = _rekey_dict(key.get("vapor_pressure_provider"))
    return any(
        "engine_version" in value or "backend_version" in value
        for value in (backend, provider, vapor_provider)
    )


def _json_ready(
    value: Any,
    path: str = "$",
    *,
    allow_nonfinite: bool = False,
) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _json_ready(
                getattr(value, field.name),
                f"{path}.{field.name}",
                allow_nonfinite=allow_nonfinite,
            )
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {
            str(key): _json_ready(
                item,
                f"{path}.{str(key)}",
                allow_nonfinite=allow_nonfinite,
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _json_ready(
                item,
                f"{path}[{index}]",
                allow_nonfinite=allow_nonfinite,
            )
            for index, item in enumerate(value)
        ]
    if isinstance(value, float):
        if not math.isfinite(value) and not allow_nonfinite:
            raise PT0NonFinitePayload(
                f"non-finite value in PT-0 payload at {path}: {value!r}"
            )
        return value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return repr(value)


def _digest(value: Any) -> str:
    return _sha256(canonical_json_bytes(value))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _quantize_decimals(quantum: float) -> int:
    try:
        decimal = Decimal(str(float(quantum))).normalize()
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid quantization quantum: {quantum!r}") from exc
    if not decimal.is_finite() or decimal <= 0:
        raise ValueError(f"quantization quantum must be positive finite: {quantum!r}")
    return max(0, -decimal.as_tuple().exponent)


def _quantize(value: float | None, quantum: float, digits: int) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    bucket = round(number / quantum) * quantum
    return round(bucket, digits)


def _sigfig(value: float | None, sig_figs: int) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    if number == 0.0:
        return 0.0
    digits = sig_figs - int(math.floor(math.log10(abs(number)))) - 1
    return round(number, digits)


def _sigfig_quantum(value: float, sig_figs: float) -> float:
    number = abs(float(value))
    if number == 0.0:
        return 10.0 ** (1.0 - float(sig_figs))
    return 10.0 ** (math.floor(math.log10(number)) + 1.0 - float(sig_figs))


def _sigfig_snap(value: Any, sig_figs: float) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    if number == 0.0:
        return 0.0
    try:
        quantum = _sigfig_quantum(number, sig_figs)
        if not math.isfinite(quantum) or quantum <= 0.0:
            return None
        snapped = round(number / quantum) * quantum
    except (OverflowError, ValueError, ZeroDivisionError):
        return None
    if not math.isfinite(snapped):
        return None
    if snapped == 0.0:
        return 0.0
    digits = max(
        0,
        int(math.ceil(float(sig_figs) - math.floor(math.log10(abs(snapped))) - 1)),
    )
    try:
        rounded = round(snapped, digits)
    except (OverflowError, ValueError):
        return None
    return rounded if math.isfinite(rounded) else None


def _normalized_sigfig_distance(
    exact_value: float,
    snapped_center: float,
    sig_figs: float,
) -> float:
    exact_number = float(exact_value)
    snapped_number = float(snapped_center)
    if not math.isfinite(exact_number) or not math.isfinite(snapped_number):
        return math.inf
    try:
        quantum = _sigfig_quantum(
            exact_number if exact_number != 0.0 else snapped_number,
            sig_figs,
        )
    except (OverflowError, ValueError):
        return math.inf
    if not math.isfinite(quantum) or quantum <= 0.0:
        return math.inf
    delta = (exact_number - snapped_number) / quantum
    if not math.isfinite(delta):
        return math.inf
    distance = delta * delta
    return distance if math.isfinite(distance) else math.inf


def _diff_top_fields(left: Mapping[str, Any], right: Mapping[str, Any]) -> list[str]:
    fields = []
    keys = sorted(set(left) | set(right))
    for key in keys:
        if _json_ready(left.get(key)) != _json_ready(right.get(key)):
            fields.append(str(key))
    return fields


def _curve_floor_marker_is_valid(
    payload: Mapping[str, Any],
    *,
    require_present: bool,
) -> bool:
    marker = payload.get("is_liquidus_floor")
    return type(marker) is bool or (marker is None and not require_present)


def _refresh_feo_qualification(
    sim: Any,
    result: EquilibriumResult,
) -> EquilibriumResult:
    diagnostics = dict(result.diagnostics or {})
    feo_activity = diagnostics.get("a_FeO_calphad")
    if not isinstance(feo_activity, Mapping):
        return result
    domain_at = getattr(sim, "liquid_feo_domain_at", None)
    if not callable(domain_at):
        raise TypeError(
            "cached a_FeO_calphad requires the current "
            "liquid_feo_domain_at resolver"
        )
    from simulator.fe_redox import _with_liquid_model_below_liquidus_notice

    current_domain = domain_at(
        float(result.temperature_C) + CELSIUS_TO_KELVIN_OFFSET
    )
    qualification = _with_liquid_model_below_liquidus_notice({}, current_domain)
    refreshed_activity = dict(feo_activity)
    notices = qualification.get("notices")
    if notices:
        refreshed_activity["notices"] = notices
    else:
        refreshed_activity.pop("notices", None)
    diagnostics["a_FeO_calphad"] = refreshed_activity
    result.diagnostics = diagnostics
    return result
