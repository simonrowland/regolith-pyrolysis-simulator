#!/usr/bin/env python3
"""Convert the legacy reduced-real corpus to the reviewed v0.2.0 schema."""

from __future__ import annotations

import argparse
import base64
import copy
import dataclasses
import hashlib
import json
import math
import os
import pathlib
import random
import sqlite3
import struct
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


DESIGN_VERSION = "0.2.0"
DESTINATION_SCHEMA_VERSION = "rr-cache-schema-v0.2.0"
LEGACY_CORPUS_VERSION = "legacy-pre-corpus-v1"
LEGACY_STATE_SCHEMA = "rr-state-key-legacy-v1"
EXPECTED_COUNTS = {
    "source": 2531,
    "rr_input_states": 2531,
    "rr_legacy_compatibility": 2531,
    "rr_alphamelts_outputs": 2531,
    "rr_magemin_outputs": 0,
    "rr_vaporock_outputs": 2126,
    "rr_sulfsat_outputs": 105,
}

BACKEND_REACTIVE_ACCOUNTS = (
    "process.cleaned_melt",
    "process.spent_reductant_residue",
    "process.metal_phase",
    "process.overhead_gas",
)
SPECIES_ORDER = (
    "SiO2",
    "TiO2",
    "Al2O3",
    "FeO",
    "Fe2O3",
    "FeOt",
    "O",
    "MgO",
    "CaO",
    "Na2O",
    "K2O",
    "Cr2O3",
    "MnO",
    "P2O5",
    "NiO",
    "CoO",
    "H2O",
    "CO2",
    "S",
)
LEGACY_COMPOSITION_SPECIES = {
    "Al2O3",
    "CaO",
    "Cr2O3",
    "FeO",
    "K2O",
    "MgO",
    "MnO",
    "Na2O",
    "NiO",
    "P2O5",
    "SiO2",
    "TiO2",
}
SELECTED_VAPOR_SPECIES = (
    "Al",
    "Ca",
    "CrO2",
    "Fe",
    "K",
    "Mg",
    "Na",
    "Si",
    "SiO",
    "Ti",
)
VAPOROCK_FULL_SPECIES = (
    "Al",
    "Al2",
    "Al2O",
    "Al2O2",
    "AlO",
    "AlO2",
    "Ca",
    "Ca2",
    "CaO_gas",
    "Cr",
    "CrO",
    "CrO2",
    "CrO3",
    "Fe",
    "FeO_gas",
    "K",
    "K2",
    "KO",
    "Mg",
    "Mg2",
    "MgO_gas",
    "Na",
    "Na2",
    "NaO",
    "O",
    "O2",
    "Si",
    "Si2",
    "Si3",
    "SiO",
    "SiO2_gas",
    "Ti",
    "TiO",
    "TiO2_gas",
)

ALPHA_ENGINE_DEFAULTS = {
    "model": "MELTSv1.0.2",
    "mode": None,
    "redox_buffer": None,
    "fO2_offset": None,
    "Fe3Fet_Liq": None,
    "require_petthermotools": None,
    "backend": None,
    "provider": None,
}
POINT_SOLVER_CONFIG = {
    "min_T_C": None,
    "max_T_C": None,
    "scan_step_C": None,
    "tolerance_C": None,
    "solid_epsilon": None,
    "liquid_epsilon": None,
    "monotonicity_tolerance": None,
    "monotone_smoothing_max": None,
    "max_bisection_iterations": None,
}


DDL = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE rr_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE rr_engine_epochs (
    engine_name TEXT NOT NULL
        CHECK (engine_name IN ('alphamelts', 'magemin', 'vaporock', 'sulfsat')),
    engine_epoch INTEGER NOT NULL CHECK (engine_epoch >= 0),
    is_current INTEGER NOT NULL CHECK (is_current IN (0, 1)),
    owner_reason TEXT NOT NULL,
    assessed_engine_version TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    PRIMARY KEY (engine_name, engine_epoch)
) STRICT;

CREATE UNIQUE INDEX rr_one_current_epoch_per_engine
    ON rr_engine_epochs(engine_name)
    WHERE is_current = 1;

CREATE TABLE rr_migration_checkpoints (
    source_db_sha256 TEXT PRIMARY KEY,
    source_schema_version TEXT NOT NULL,
    destination_schema_version TEXT NOT NULL,
    last_legacy_key_hash TEXT,
    converted_source_rows INTEGER NOT NULL CHECK (converted_source_rows >= 0),
    source_row_count INTEGER NOT NULL CHECK (source_row_count >= 0),
    status TEXT NOT NULL CHECK (status IN ('running', 'complete', 'failed')),
    report_path TEXT,
    updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE rr_input_states (
    state_id INTEGER PRIMARY KEY,
    key_schema_version TEXT NOT NULL,
    corpus_version TEXT NOT NULL,
    input_completeness TEXT NOT NULL
        CHECK (input_completeness IN ('complete', 'legacy-cleaned-fraction-only')),
    composition_input_kind TEXT NOT NULL
        CHECK (composition_input_kind IN ('account_mol', 'unscoped_mol', 'kg_fallback', 'legacy_fraction')),
    composition_mol_by_account_json TEXT NOT NULL,
    composition_mol_by_account_sha256 TEXT NOT NULL,
    composition_total_mol REAL,
    legacy_composition_kg_json TEXT,
    species_registry_digest TEXT,
    requested_temperature_C REAL,
    applied_temperature_C REAL NOT NULL,
    requested_pressure_bar REAL,
    applied_pressure_bar REAL NOT NULL,
    requested_fO2_log REAL,
    applied_fO2_log REAL NOT NULL,
    intrinsic_fO2_log REAL NOT NULL,
    commanded_pO2_bar REAL,
    transport_pO2_bar REAL,
    sulfur_input_ppm REAL,
    control_provenance_json TEXT,
    canonical_state_bytes BLOB NOT NULL,
    state_key_sha256 TEXT NOT NULL,
    legacy_key_sha256 TEXT UNIQUE,
    legacy_request_schema_version TEXT,
    legacy_key_shape_json TEXT,
    created_at TEXT NOT NULL,
    CHECK (
        (
            input_completeness = 'complete'
            AND composition_input_kind <> 'legacy_fraction'
            AND composition_total_mol IS NOT NULL
        )
        OR (
            input_completeness = 'legacy-cleaned-fraction-only'
            AND composition_input_kind = 'legacy_fraction'
            AND composition_total_mol IS NULL
            AND legacy_key_sha256 IS NOT NULL
            AND legacy_request_schema_version IS NOT NULL
            AND legacy_key_shape_json IS NOT NULL
        )
    ),
    UNIQUE (key_schema_version, corpus_version, state_key_sha256)
) STRICT;

CREATE INDEX rr_input_state_controls
    ON rr_input_states(applied_temperature_C, applied_pressure_bar, applied_fO2_log);
CREATE INDEX rr_input_state_composition
    ON rr_input_states(composition_mol_by_account_sha256);

CREATE TABLE rr_legacy_compatibility (
    state_id INTEGER PRIMARY KEY REFERENCES rr_input_states(state_id),
    legacy_payload_sha256 TEXT NOT NULL,
    compatibility_shape_json TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE rr_alphamelts_outputs (
    output_id INTEGER PRIMARY KEY,
    state_id INTEGER NOT NULL REFERENCES rr_input_states(state_id),
    engine_name TEXT NOT NULL DEFAULT 'alphamelts' CHECK (engine_name = 'alphamelts'),
    engine_epoch INTEGER NOT NULL,
    artifact_kind TEXT NOT NULL CHECK (artifact_kind IN ('equilibrium_point', 'freeze_curve')),
    consumer_id TEXT NOT NULL CHECK (consumer_id IN (
        'silicate_equilibrium', 'silicate_liquidus', 'gate_liquid_fraction',
        'equilibrium_crystallization', 'fractional_crystallization',
        'decompression_path'
    )),
    engine_config_sha256 TEXT NOT NULL,
    engine_config_json TEXT NOT NULL,
    solver_config_sha256 TEXT NOT NULL,
    solver_config_json TEXT NOT NULL,
    budget_config_sha256 TEXT NOT NULL DEFAULT 'none',
    budget_config_json TEXT,
    native_input_json TEXT NOT NULL,
    result_class TEXT NOT NULL CHECK (result_class IN ('success', 'physics_refusal', 'budget_refusal')),
    status TEXT NOT NULL,
    authoritative INTEGER NOT NULL CHECK (authoritative IN (0, 1)),
    negative_class TEXT,
    refusal_reason TEXT,
    temperature_C REAL,
    pressure_bar REAL,
    fO2_log REAL,
    phases_present_json TEXT,
    phase_masses_kg_json TEXT,
    phase_modes_wt_pct_json TEXT,
    phase_species_mol_json TEXT,
    phase_species_kg_json TEXT,
    phase_compositions_json TEXT,
    liquid_fraction REAL,
    phase_assemblage_available INTEGER,
    liquid_composition_wt_pct_json TEXT,
    activity_coefficients_json TEXT,
    result_liquidus_T_C REAL,
    result_warnings_json TEXT,
    result_diagnostics_json TEXT,
    diagnostic_liquidus_T_C REAL,
    diagnostic_liquidus_T_K REAL,
    diagnostic_solidus_T_C REAL,
    liquid_fraction_path_json TEXT,
    applied_fe3fet REAL,
    fe_redox_policy TEXT,
    intrinsic_fO2_log REAL,
    backend_status TEXT,
    backend_status_reason TEXT,
    backend_warnings_json TEXT,
    backend_diagnostics_json TEXT,
    finder_iterations INTEGER,
    finder_samples_json TEXT,
    finder_diagnostics_json TEXT,
    curve_source TEXT,
    curve_solidus_T_C REAL,
    curve_liquidus_T_C REAL,
    curve_path_json TEXT,
    composition_derived TEXT,
    control_audit_json TEXT,
    engine_version_metadata TEXT,
    adapter_version_metadata TEXT,
    code_version TEXT NOT NULL,
    data_digests_json TEXT NOT NULL,
    output_sha256 TEXT NOT NULL,
    raw_payload BLOB,
    raw_payload_format TEXT,
    raw_payload_sha256 TEXT,
    capture_provenance_json TEXT,
    created_at TEXT NOT NULL,
    git_dirty INTEGER NOT NULL CHECK (git_dirty IN (0, 1)),
    FOREIGN KEY (engine_name, engine_epoch)
        REFERENCES rr_engine_epochs(engine_name, engine_epoch),
    CHECK (
        (result_class = 'success' AND negative_class IS NULL AND refusal_reason IS NULL)
        OR (
            result_class IN ('physics_refusal', 'budget_refusal')
            AND authoritative = 0
            AND negative_class IS NOT NULL AND length(trim(negative_class)) > 0
            AND refusal_reason IS NOT NULL AND length(trim(refusal_reason)) > 0
        )
    ),
    CHECK (
        (result_class = 'budget_refusal' AND budget_config_sha256 <> 'none' AND budget_config_json IS NOT NULL)
        OR (result_class <> 'budget_refusal' AND budget_config_sha256 = 'none' AND budget_config_json IS NULL)
    ),
    CHECK (
        (raw_payload IS NULL AND raw_payload_format IS NULL AND raw_payload_sha256 IS NULL)
        OR (raw_payload IS NOT NULL AND raw_payload_format IS NOT NULL AND raw_payload_sha256 IS NOT NULL)
    ),
    UNIQUE (
        state_id, engine_epoch, artifact_kind, consumer_id,
        engine_config_sha256, solver_config_sha256, budget_config_sha256
    )
) STRICT;

CREATE INDEX rr_alphamelts_exact
    ON rr_alphamelts_outputs(
        state_id, engine_epoch, artifact_kind, consumer_id,
        engine_config_sha256, solver_config_sha256, budget_config_sha256
    );
CREATE TABLE rr_magemin_outputs (
    output_id INTEGER PRIMARY KEY,
    state_id INTEGER NOT NULL REFERENCES rr_input_states(state_id),
    engine_name TEXT NOT NULL DEFAULT 'magemin' CHECK (engine_name = 'magemin'),
    engine_epoch INTEGER NOT NULL,
    artifact_kind TEXT NOT NULL CHECK (artifact_kind IN ('equilibrium_point', 'freeze_curve')),
    consumer_id TEXT NOT NULL CHECK (consumer_id IN (
        'silicate_equilibrium', 'silicate_liquidus', 'gate_liquid_fraction',
        'equilibrium_crystallization', 'fractional_crystallization',
        'decompression_path'
    )),
    engine_config_sha256 TEXT NOT NULL,
    engine_config_json TEXT NOT NULL,
    solver_config_sha256 TEXT NOT NULL,
    solver_config_json TEXT NOT NULL,
    budget_config_sha256 TEXT NOT NULL DEFAULT 'none',
    budget_config_json TEXT,
    native_input_json TEXT NOT NULL,
    result_class TEXT NOT NULL CHECK (result_class IN ('success', 'physics_refusal', 'budget_refusal')),
    status TEXT NOT NULL,
    authoritative INTEGER NOT NULL CHECK (authoritative IN (0, 1)),
    negative_class TEXT,
    refusal_reason TEXT,
    temperature_C REAL,
    pressure_bar REAL,
    fO2_log REAL,
    phases_present_json TEXT,
    phase_masses_kg_json TEXT,
    phase_species_mol_json TEXT,
    phase_species_kg_json TEXT,
    phase_compositions_json TEXT,
    liquid_fraction REAL,
    phase_assemblage_available INTEGER,
    liquid_composition_wt_pct_json TEXT,
    vapor_pressures_Pa_json TEXT,
    vapor_pressures_source_json TEXT,
    activity_coefficients_json TEXT,
    result_liquidus_T_C REAL,
    result_warnings_json TEXT,
    result_diagnostics_json TEXT,
    diagnostic_liquidus_T_C REAL,
    diagnostic_liquidus_T_K REAL,
    diagnostic_solidus_T_C REAL,
    phase_modes_wt_pct_json TEXT,
    backend_status TEXT,
    backend_status_reason TEXT,
    backend_warnings_json TEXT,
    projection_diagnostics_json TEXT,
    bulk_projection_diagnostics_json TEXT,
    operating_point_diagnostics_json TEXT,
    finder_iterations INTEGER,
    finder_samples_json TEXT,
    finder_diagnostics_json TEXT,
    curve_source TEXT,
    curve_solidus_T_C REAL,
    curve_liquidus_T_C REAL,
    curve_path_json TEXT,
    composition_derived TEXT,
    control_audit_json TEXT,
    engine_version_metadata TEXT,
    bridge_version_metadata TEXT,
    adapter_version_metadata TEXT,
    code_version TEXT NOT NULL,
    data_digests_json TEXT NOT NULL,
    output_sha256 TEXT NOT NULL,
    capture_provenance_json TEXT,
    created_at TEXT NOT NULL,
    git_dirty INTEGER NOT NULL CHECK (git_dirty IN (0, 1)),
    FOREIGN KEY (engine_name, engine_epoch)
        REFERENCES rr_engine_epochs(engine_name, engine_epoch),
    CHECK (
        (result_class = 'success' AND negative_class IS NULL AND refusal_reason IS NULL)
        OR (
            result_class IN ('physics_refusal', 'budget_refusal')
            AND authoritative = 0
            AND negative_class IS NOT NULL AND length(trim(negative_class)) > 0
            AND refusal_reason IS NOT NULL AND length(trim(refusal_reason)) > 0
        )
    ),
    CHECK (
        (result_class = 'budget_refusal' AND budget_config_sha256 <> 'none' AND budget_config_json IS NOT NULL)
        OR (result_class <> 'budget_refusal' AND budget_config_sha256 = 'none' AND budget_config_json IS NULL)
    ),
    UNIQUE (
        state_id, engine_epoch, artifact_kind, consumer_id,
        engine_config_sha256, solver_config_sha256, budget_config_sha256
    )
) STRICT;

CREATE INDEX rr_magemin_exact
    ON rr_magemin_outputs(
        state_id, engine_epoch, artifact_kind, consumer_id,
        engine_config_sha256, solver_config_sha256, budget_config_sha256
    );
CREATE TABLE rr_vaporock_outputs (
    output_id INTEGER PRIMARY KEY,
    state_id INTEGER NOT NULL REFERENCES rr_input_states(state_id),
    engine_name TEXT NOT NULL DEFAULT 'vaporock' CHECK (engine_name = 'vaporock'),
    engine_epoch INTEGER NOT NULL,
    artifact_kind TEXT NOT NULL CHECK (artifact_kind = 'equilibrium_point'),
    consumer_id TEXT NOT NULL CHECK (consumer_id = 'silicate_equilibrium'),
    engine_config_sha256 TEXT NOT NULL,
    engine_config_json TEXT NOT NULL,
    native_input_json TEXT NOT NULL,
    result_class TEXT NOT NULL CHECK (result_class IN ('success', 'physics_refusal')),
    status TEXT NOT NULL,
    authoritative INTEGER NOT NULL CHECK (authoritative IN (0, 1)),
    negative_class TEXT,
    refusal_reason TEXT,
    temperature_C REAL,
    pressure_bar REAL,
    fO2_log REAL,
    phases_present_json TEXT,
    phase_masses_kg_json TEXT,
    phase_species_mol_json TEXT,
    phase_species_kg_json TEXT,
    phase_compositions_json TEXT,
    liquid_fraction REAL,
    phase_assemblage_available INTEGER,
    liquid_composition_wt_pct_json TEXT,
    vapor_pressures_Pa_json TEXT,
    vapor_pressures_source_json TEXT,
    activity_coefficients_json TEXT,
    result_liquidus_T_C REAL,
    result_warnings_json TEXT,
    result_diagnostics_json TEXT,
    vaporock_full_speciation_Pa_json TEXT,
    transport_pO2_bar REAL,
    backend_status TEXT,
    backend_warnings_json TEXT,
    pressure_control_authoritative INTEGER,
    pressure_control_reason TEXT,
    requested_pressure_bar REAL,
    projection_diagnostics_json TEXT,
    backend_vapor_pressures_Pa_json TEXT,
    backend_vapor_pressures_source_json TEXT,
    thermoengine_vapor_pressures_confirmed_json TEXT,
    vapor_pressure_zero_reason TEXT,
    kernel_vapor_pressure_warnings_json TEXT,
    control_audit_json TEXT,
    engine_version_metadata TEXT,
    adapter_version_metadata TEXT,
    code_version TEXT NOT NULL,
    data_digests_json TEXT NOT NULL,
    output_sha256 TEXT NOT NULL,
    capture_provenance_json TEXT,
    created_at TEXT NOT NULL,
    git_dirty INTEGER NOT NULL CHECK (git_dirty IN (0, 1)),
    FOREIGN KEY (engine_name, engine_epoch)
        REFERENCES rr_engine_epochs(engine_name, engine_epoch),
    CHECK (
        (result_class = 'success' AND negative_class IS NULL AND refusal_reason IS NULL)
        OR (
            result_class = 'physics_refusal'
            AND authoritative = 0
            AND negative_class IS NOT NULL AND length(trim(negative_class)) > 0
            AND refusal_reason IS NOT NULL AND length(trim(refusal_reason)) > 0
        )
    ),
    UNIQUE (
        state_id, engine_epoch, artifact_kind, consumer_id,
        engine_config_sha256
    )
) STRICT;

CREATE INDEX rr_vaporock_exact
    ON rr_vaporock_outputs(
        state_id, engine_epoch, artifact_kind, consumer_id,
        engine_config_sha256
    );

CREATE TABLE rr_sulfsat_outputs (
    output_id INTEGER PRIMARY KEY,
    state_id INTEGER NOT NULL REFERENCES rr_input_states(state_id),
    engine_name TEXT NOT NULL DEFAULT 'sulfsat' CHECK (engine_name = 'sulfsat'),
    engine_epoch INTEGER NOT NULL,
    artifact_kind TEXT NOT NULL CHECK (artifact_kind = 'equilibrium_point'),
    consumer_id TEXT NOT NULL CHECK (consumer_id = 'silicate_equilibrium'),
    engine_config_sha256 TEXT NOT NULL,
    engine_config_json TEXT NOT NULL,
    applied_fe3fet_liq REAL,
    applied_fe3fet_source TEXT,
    native_input_json TEXT NOT NULL,
    result_class TEXT NOT NULL CHECK (result_class IN ('success', 'physics_refusal')),
    status TEXT NOT NULL,
    authoritative INTEGER NOT NULL CHECK (authoritative IN (0, 1)),
    negative_class TEXT,
    refusal_reason TEXT,
    SCSS_ppm REAL,
    SCAS_ppm REAL,
    S6_fraction REAL,
    S_in_sulfide_ppm REAL,
    S_in_sulfate_ppm REAL,
    calibration_status TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    control_audit_json TEXT,
    engine_version_metadata TEXT,
    calibration_version_metadata TEXT,
    adapter_version_metadata TEXT,
    code_version TEXT NOT NULL,
    data_digests_json TEXT NOT NULL,
    output_sha256 TEXT NOT NULL,
    capture_provenance_json TEXT,
    created_at TEXT NOT NULL,
    git_dirty INTEGER NOT NULL CHECK (git_dirty IN (0, 1)),
    FOREIGN KEY (engine_name, engine_epoch)
        REFERENCES rr_engine_epochs(engine_name, engine_epoch),
    CHECK (
        (result_class = 'success' AND negative_class IS NULL AND refusal_reason IS NULL)
        OR (
            result_class = 'physics_refusal'
            AND authoritative = 0
            AND negative_class IS NOT NULL AND length(trim(negative_class)) > 0
            AND refusal_reason IS NOT NULL AND length(trim(refusal_reason)) > 0
        )
    ),
    UNIQUE (
        state_id, engine_epoch, artifact_kind, consumer_id,
        engine_config_sha256
    )
) STRICT;

CREATE INDEX rr_sulfsat_exact
    ON rr_sulfsat_outputs(
        state_id, engine_epoch, artifact_kind, consumer_id,
        engine_config_sha256
    );
"""


class ConversionError(RuntimeError):
    pass


class CanonicalizationError(ConversionError):
    pass


class UnknownFieldError(ConversionError):
    pass


class ParityError(ConversionError):
    pass


class RetryingConnection(sqlite3.Connection):
    busy_retry_count: int

    def _busy_retry(self, operation: Any) -> Any:
        for attempt in range(6):
            try:
                return operation()
            except sqlite3.OperationalError as exc:
                code = getattr(exc, "sqlite_errorcode", None)
                busy = code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                if not busy:
                    message = str(exc).lower()
                    busy = "database is locked" in message or "database is busy" in message
                if not busy or attempt == 5:
                    raise
                self.busy_retry_count += 1
                ceiling = min(0.25, 0.01 * (2**attempt))
                time.sleep(random.uniform(ceiling / 2.0, ceiling))

    def execute(self, sql: str, parameters: Iterable[Any] = (), /) -> sqlite3.Cursor:
        return self._busy_retry(lambda: super(RetryingConnection, self).execute(sql, parameters))

    def commit(self) -> None:
        self._busy_retry(lambda: super(RetryingConnection, self).commit())


class _Missing:
    pass


MISSING = _Missing()


class JsonNumber(str):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utf8_sorted(values: Iterable[str]) -> list[str]:
    return sorted(values, key=lambda item: item.encode("utf-8"))


def _f64(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CanonicalizationError(f"{path}: expected finite number")
    result = float(value)
    if not math.isfinite(result):
        raise CanonicalizationError(f"{path}: non-finite float")
    if result == 0.0:
        result = 0.0
    return result


def f64_bytes(value: float) -> bytes:
    return struct.pack(">d", _f64(value, "float"))


def _identity_value(value: Any) -> Any:
    if value is None or type(value) in (bool, str, int):
        return value
    if type(value) is float:
        materialized = _f64(value, "identity")
        return {"$f64": materialized.hex()}
    if type(value) in (list, tuple):
        return [_identity_value(item) for item in value]
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise CanonicalizationError("non-string key")
        return {
            key: _identity_value(value[key])
            for key in _utf8_sorted(value)
        }
    raise CanonicalizationError(type(value).__name__)


def encode(schema_id: str, value: Any) -> bytes:
    envelope = {"schema": schema_id, "value": _identity_value(value)}
    return json.dumps(
        envelope,
        ensure_ascii=False,
        sort_keys=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _display_value(value: Any) -> Any:
    if value is None or type(value) in (bool, str, int):
        return value
    if isinstance(value, float):
        return _f64(value, "display")
    if isinstance(value, (list, tuple)):
        return [_display_value(item) for item in value]
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise CanonicalizationError("non-string display key")
        return {key: _display_value(value[key]) for key in _utf8_sorted(value)}
    raise CanonicalizationError(type(value).__name__)


def display_json(value: Any) -> str:
    return json.dumps(
        _display_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def materialize_alphamelts_engine_config(
    supplied: Mapping[str, Any], *, resolved_mode: str | None = None
) -> dict[str, Any]:
    unknown = set(supplied) - set(ALPHA_ENGINE_DEFAULTS)
    if unknown:
        raise UnknownFieldError(f"AlphaMELTS config unknown fields: {sorted(unknown)}")
    result = dict(ALPHA_ENGINE_DEFAULTS)
    result.update(dict(supplied))
    if result["mode"] is None:
        if not resolved_mode:
            raise CanonicalizationError("AlphaMELTS mode was omitted without a resolved mode")
        result["mode"] = resolved_mode
    if result["require_petthermotools"] is None:
        result["require_petthermotools"] = result["mode"] == "python_api"
    if result["mode"] != "python_api" and result["require_petthermotools"] is True:
        raise CanonicalizationError(
            "require_petthermotools is mode-inapplicable outside python_api"
        )
    for name in ("fO2_offset", "Fe3Fet_Liq"):
        if result[name] is not None:
            result[name] = _f64(result[name], f"AlphaMELTS config {name}")
    if result["redox_buffer"] is None and result["fO2_offset"] is not None:
        raise CanonicalizationError("fO2_offset requires redox_buffer")
    return result


def ordered_account_species_vector(
    composition: Mapping[str, Mapping[str, Any]],
) -> list[list[Any]]:
    extension_accounts = _utf8_sorted(set(composition) - set(BACKEND_REACTIVE_ACCOUNTS))
    accounts = list(BACKEND_REACTIVE_ACCOUNTS) + extension_accounts
    result: list[list[Any]] = []
    for account in accounts:
        species_map = composition.get(account, {})
        extension_species = _utf8_sorted(set(species_map) - set(SPECIES_ORDER))
        species = list(SPECIES_ORDER) + extension_species
        result.append(
            [
                account,
                [
                    [name, _f64(species_map.get(name, 0.0), f"{account}/{name}")]
                    for name in species
                ],
            ]
        )
    return result


def _parse_json_with_number_tokens(raw: bytes) -> tuple[Any, dict[str, str]]:
    try:
        parsed = json.loads(
            raw,
            parse_int=JsonNumber,
            parse_float=JsonNumber,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConversionError(f"invalid JSON: {exc}") from exc
    tokens: dict[str, str] = {}

    def materialize(value: Any, pointer: str) -> Any:
        if isinstance(value, JsonNumber):
            token = str(value)
            number = float(token)
            if not math.isfinite(number):
                raise CanonicalizationError(f"{pointer}: non-finite JSON number")
            tokens[pointer] = token
            return number
        if isinstance(value, list):
            return [materialize(item, f"{pointer}/{index}") for index, item in enumerate(value)]
        if isinstance(value, dict):
            return {
                key: materialize(item, f"{pointer}/{_pointer_escape(key)}")
                for key, item in value.items()
            }
        return value

    return materialize(parsed, ""), tokens


def _pointer_escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _pointer_parts(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise ValueError(pointer)
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def _pointer_get(value: Any, pointer: str) -> Any:
    current = value
    for part in _pointer_parts(pointer):
        if isinstance(current, dict):
            if part not in current:
                return MISSING
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return MISSING
        else:
            return MISSING
    return current


def _path_state(value: Any, pointer: str) -> str:
    item = _pointer_get(value, pointer)
    if item is MISSING:
        return "absent"
    if item is None:
        return "null"
    if item == {}:
        return "empty_object"
    if item == []:
        return "empty_array"
    return "present"


def _legacy_json_bytes(value: Any, number_tokens: Mapping[str, str]) -> bytes:
    def render(item: Any, pointer: str) -> str:
        if pointer in number_tokens:
            token = number_tokens[pointer]
            parsed = float(token)
            if not math.isfinite(parsed):
                raise CanonicalizationError(f"{pointer}: invalid compatibility number token")
            if not isinstance(item, (int, float)) or isinstance(item, bool):
                raise CanonicalizationError(f"{pointer}: number token applied to non-number")
            if f64_bytes(parsed) != f64_bytes(float(item)):
                raise CanonicalizationError(f"{pointer}: number token bits disagree")
            return token
        if item is None:
            return "null"
        if item is True:
            return "true"
        if item is False:
            return "false"
        if isinstance(item, str):
            return json.dumps(item, ensure_ascii=True, allow_nan=False)
        if isinstance(item, int):
            return str(item)
        if isinstance(item, float):
            return json.dumps(_f64(item, pointer), allow_nan=False)
        if isinstance(item, list):
            return "[" + ",".join(render(child, f"{pointer}/{index}") for index, child in enumerate(item)) + "]"
        if isinstance(item, dict):
            keys = sorted(item)
            return "{" + ",".join(
                json.dumps(key, ensure_ascii=True) + ":" + render(item[key], f"{pointer}/{_pointer_escape(key)}")
                for key in keys
            ) + "}"
        raise CanonicalizationError(f"legacy serializer unsupported {type(item).__name__}")

    return render(value, "").encode("utf-8")


def _expect_keys(value: Any, expected: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConversionError(f"{path}: expected object")
    unknown = set(value) - expected
    missing = expected - set(value)
    if unknown:
        raise UnknownFieldError(f"{path}: unknown fields {sorted(unknown)}")
    if missing:
        raise ConversionError(f"{path}: missing fields {sorted(missing)}")
    return value


def _expect_subset_keys(
    value: Any, required: set[str], optional: set[str], path: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConversionError(f"{path}: expected object")
    unknown = set(value) - required - optional
    missing = required - set(value)
    if unknown:
        raise UnknownFieldError(f"{path}: unknown fields {sorted(unknown)}")
    if missing:
        raise ConversionError(f"{path}: missing fields {sorted(missing)}")
    return value


def _validate_float_map(value: Any, path: str) -> None:
    if not isinstance(value, dict):
        raise ConversionError(f"{path}: expected object")
    for key, item in value.items():
        if not isinstance(key, str):
            raise ConversionError(f"{path}: non-string member")
        _f64(item, f"{path}/{key}")


def _validate_string_map(value: Any, path: str) -> None:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in value.items()
    ):
        raise ConversionError(f"{path}: expected string map")


def _validate_legacy_key(key: dict[str, Any]) -> None:
    _expect_keys(
        key,
        {
            "artifact",
            "backend",
            "code_version",
            "composition_mol_fraction",
            "controls",
            "data_digests",
            "engine_version",
            "intent",
            "model",
            "provider",
            "redox",
            "schema_version",
            "source_module_digest",
            "sulfur_side",
            "vapor_pressure_provider",
        },
        "key",
    )
    _expect_keys(key["backend"], {"backend_class", "backend_name", "backend_version"}, "key/backend")
    _expect_keys(key["controls"], {"T_K", "log_fO2", "pO2_bar", "pressure_bar"}, "key/controls")
    _expect_keys(key["data_digests"], {"feedstocks", "setpoints", "species_formula_registry", "vapor_pressures"}, "key/data_digests")
    _expect_keys(key["model"], {"mode", "model"}, "key/model")
    provider_fields = {
        "authoritative_provider_id",
        "engine_version",
        "fallback_allowed",
        "fallback_provider_id",
        "mode",
        "model",
        "resolved_provider_id",
        "resolved_role",
    }
    _expect_keys(key["provider"], provider_fields, "key/provider")
    _expect_keys(key["vapor_pressure_provider"], provider_fields, "key/vapor_pressure_provider")
    _expect_keys(key["redox"], {"fe_redox_policy", "fe_split"}, "key/redox")
    _expect_keys(key["redox"]["fe_split"], {"FeO", "Fe2O3"}, "key/redox/fe_split")
    _expect_keys(key["source_module_digest"], {"algorithm", "module_set", "paths", "sha256"}, "key/source_module_digest")
    _expect_keys(
        key["sulfur_side"],
        {
            "S_input_ppm",
            "stage0_inventory_digest",
            "sulfsat_available",
            "sulfsat_calibration_version",
            "sulfsat_package_version",
            "sulfsat_provider",
        },
        "key/sulfur_side",
    )
    composition = key["composition_mol_fraction"]
    if not isinstance(composition, list):
        raise ConversionError("key/composition_mol_fraction: expected list")
    species: list[str] = []
    for index, pair in enumerate(composition):
        if not isinstance(pair, list) or len(pair) != 2 or not isinstance(pair[0], str):
            raise ConversionError(f"key/composition_mol_fraction/{index}: invalid pair")
        if pair[0] not in LEGACY_COMPOSITION_SPECIES:
            raise UnknownFieldError(f"key/composition_mol_fraction/{index}: unknown species {pair[0]}")
        _f64(pair[1], f"key/composition_mol_fraction/{index}/1")
        species.append(pair[0])
    if species != _utf8_sorted(species) or len(species) != len(set(species)):
        raise ConversionError("legacy composition species order/uniqueness mismatch")
    for name in ("T_K", "log_fO2", "pO2_bar", "pressure_bar"):
        _f64(key["controls"][name], f"key/controls/{name}")
    for name in ("FeO", "Fe2O3"):
        _f64(key["redox"]["fe_split"][name], f"key/redox/fe_split/{name}")
    _f64(key["sulfur_side"]["S_input_ppm"], "key/sulfur_side/S_input_ppm")


EQ_REQUIRED = {
    "activity_coefficients",
    "fO2_log",
    "liquid_composition_wt_pct",
    "liquid_fraction",
    "liquid_viscosity_Pa_s",
    "liquidus_T_C",
    "phase_assemblage_available",
    "phase_compositions",
    "phase_masses_kg",
    "phase_species_kg",
    "phase_species_mol",
    "phases_present",
    "pressure_bar",
    "status",
    "sulfur_saturation",
    "temperature_C",
    "vapor_pressures_Pa",
    "vapor_pressures_source",
    "warnings",
}
ALPHA_REQUIRED = {
    "activity_coefficients",
    "applied_fe3fet",
    "backend_status",
    "backend_warnings",
    "engine_version",
    "fO2_log",
    "fe_redox_policy",
    "intrinsic_fO2_log",
    "liquid_composition_wt_pct",
    "liquid_fraction",
    "liquid_fraction_path",
    "liquidus_T_C",
    "liquidus_T_K",
    "mode",
    "phase_masses_kg",
    "phase_modes_wt_pct",
    "phases_present",
    "solidus_T_C",
}
VAPO_REQUIRED = {
    "activities",
    "backend_status",
    "backend_vapor_pressures_Pa",
    "backend_vapor_pressures_source",
    "backend_warnings",
    "engine_version",
    "mode",
    "pO2_bar",
    "vapor_pressures_Pa",
    "vapor_pressures_source",
    "vaporock_full_speciation_Pa",
}


def _validate_legacy_payload(
    payload: dict[str, Any], *, validate_followers: bool = True
) -> None:
    _expect_keys(
        payload,
        {
            "alphamelts_diagnostics",
            "equilibrium_result",
            "last_vapor_pressure_diagnostic",
            "last_vapor_pressures_source",
        },
        "payload",
    )
    eq = _expect_subset_keys(payload["equilibrium_result"], EQ_REQUIRED, {"diagnostics"}, "payload/equilibrium_result")
    alpha = _expect_subset_keys(
        payload["alphamelts_diagnostics"],
        ALPHA_REQUIRED,
        {"backend_diagnostics", "backend_status_reason"},
        "payload/alphamelts_diagnostics",
    )
    vapo = payload["last_vapor_pressure_diagnostic"]
    if not isinstance(vapo, dict):
        raise ConversionError("payload/last_vapor_pressure_diagnostic: expected object")
    if validate_followers and vapo:
        vapo = _expect_subset_keys(
            vapo,
            VAPO_REQUIRED,
            {
                "thermoengine_vapor_pressures_confirmed",
                "vapor_pressure_zero_reason",
                "pressure_control_authoritative",
                "pressure_control_reason",
                "requested_pressure_bar",
                "projection_diagnostics",
                "kernel_vapor_pressure_warnings",
            },
            "payload/last_vapor_pressure_diagnostic",
        )
        alternatives = {
            "thermoengine_vapor_pressures_confirmed",
            "vapor_pressure_zero_reason",
        } & set(vapo)
        if len(alternatives) != 1:
            raise ConversionError("VapoRock confirmation/zero-reason alternatives invalid")
        _validate_float_map(vapo["vapor_pressures_Pa"], "payload/vapo/vapor_pressures_Pa")
        _validate_string_map(vapo["vapor_pressures_source"], "payload/vapo/vapor_pressures_source")
        _validate_float_map(vapo["vaporock_full_speciation_Pa"], "payload/vapo/vaporock_full_speciation_Pa")
        _validate_float_map(vapo["activities"], "payload/vapo/activities")
        _validate_float_map(
            vapo["backend_vapor_pressures_Pa"],
            "payload/vapo/backend_vapor_pressures_Pa",
        )
        _validate_string_map(
            vapo["backend_vapor_pressures_source"],
            "payload/vapo/backend_vapor_pressures_source",
        )
    _validate_float_map(eq["liquid_composition_wt_pct"], "payload/equilibrium_result/liquid_composition_wt_pct")
    _validate_float_map(eq["phase_masses_kg"], "payload/equilibrium_result/phase_masses_kg")
    _validate_float_map(eq["vapor_pressures_Pa"], "payload/equilibrium_result/vapor_pressures_Pa")
    _validate_string_map(eq["vapor_pressures_source"], "payload/equilibrium_result/vapor_pressures_source")
    _validate_float_map(alpha["liquid_composition_wt_pct"], "payload/alphamelts/liquid_composition_wt_pct")
    _validate_float_map(alpha["phase_masses_kg"], "payload/alphamelts/phase_masses_kg")
    _validate_float_map(alpha["phase_modes_wt_pct"], "payload/alphamelts/phase_modes_wt_pct")
    sulfur = eq["sulfur_saturation"]
    if validate_followers and sulfur is not None:
        sulfur = _expect_keys(
            sulfur,
            {
                "SCSS_ppm",
                "SCAS_ppm",
                "S6_fraction",
                "S_in_sulfide_ppm",
                "S_in_sulfate_ppm",
                "calibration_status",
                "warnings",
            },
            "payload/equilibrium_result/sulfur_saturation",
        )
        for name in ("SCSS_ppm", "SCAS_ppm", "S6_fraction", "S_in_sulfide_ppm", "S_in_sulfate_ppm"):
            _f64(sulfur[name], f"payload/sulfur/{name}")
    if eq["liquid_composition_wt_pct"] != alpha["liquid_composition_wt_pct"]:
        raise ConversionError("duplicate liquid composition mismatch")
    if eq["liquid_fraction"] != alpha["liquid_fraction"]:
        raise ConversionError("duplicate liquid fraction mismatch")
    if eq["phases_present"] != alpha["phases_present"]:
        raise ConversionError("duplicate phases-present mismatch")
    if eq["phase_masses_kg"] != alpha["phase_masses_kg"]:
        raise ConversionError("duplicate phase-mass mismatch")
    if eq["activity_coefficients"] != alpha["activity_coefficients"]:
        raise ConversionError("duplicate activity-coefficient mismatch")
    if f64_bytes(eq["fO2_log"]) != f64_bytes(alpha["fO2_log"]):
        raise ConversionError("duplicate fO2 mismatch")
    if validate_followers and vapo:
        if eq["vapor_pressures_Pa"] != vapo["vapor_pressures_Pa"]:
            raise ConversionError("duplicate VapoRock pressure mismatch")
        if eq["vapor_pressures_source"] != vapo["vapor_pressures_source"]:
            raise ConversionError("duplicate VapoRock source mismatch")
    if validate_followers and eq["vapor_pressures_source"] != payload["last_vapor_pressures_source"]:
        raise ConversionError("top-level vapor source alias mismatch")


def _validate_physics_key(row: sqlite3.Row, key: dict[str, Any]) -> None:
    raw = row["physics_key_bytes"]
    if raw is None:
        return
    physics = json.loads(bytes(raw))
    _expect_keys(physics, {"schema_version", "physics_bucket", "replay_scope"}, "physics_key")
    if physics["schema_version"] != row["physics_bucket_schema_version"]:
        raise ConversionError("physics bucket schema version mismatch")
    if sha256_bytes(bytes(raw)) != row["physics_bucket_sha256"]:
        raise ConversionError("physics bucket SHA mismatch")
    bucket = physics["physics_bucket"]
    expected_bucket = {
        "artifact": key["artifact"],
        "composition_mol_fraction": key["composition_mol_fraction"],
        "controls": key["controls"],
        "intent": key["intent"],
        "sulfur": {"S_input_ppm": key["sulfur_side"]["S_input_ppm"]},
    }
    if bucket != expected_bucket:
        raise ConversionError("physics key duplicate fields mismatch")
    replay = physics["replay_scope"]
    expected_replay = {
        "backend": key["backend"],
        "data_digests": {
            "species_formula_registry": key["data_digests"]["species_formula_registry"],
            "vapor_pressures": key["data_digests"]["vapor_pressures"],
        },
        "engine_version": key["engine_version"],
        "exact_replay_schema_version": key["schema_version"],
        "provider": key["provider"],
        "vapor_pressure_provider": key["vapor_pressure_provider"],
    }
    if "sulfsat" in replay:
        expected_replay["sulfsat"] = {
            name: key["sulfur_side"][name]
            for name in (
                "sulfsat_available",
                "sulfsat_calibration_version",
                "sulfsat_package_version",
                "sulfsat_provider",
            )
        }
    if replay != expected_replay:
        raise ConversionError("physics replay-scope duplicate fields mismatch")
    replay_bytes = json.dumps(replay, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    if sha256_bytes(replay_bytes) != row["replay_scope_sha256"]:
        raise ConversionError("replay-scope SHA mismatch")


def _compatibility_paths(
    payload: dict[str, Any], *, include_followers: bool = True
) -> list[list[str]]:
    paths = {
        "/alphamelts_diagnostics/activity_coefficients",
        "/alphamelts_diagnostics/backend_diagnostics",
        "/alphamelts_diagnostics/backend_status_reason",
        "/alphamelts_diagnostics/backend_warnings",
        "/alphamelts_diagnostics/liquid_fraction_path",
        "/alphamelts_diagnostics/phase_masses_kg/olivine",
        "/alphamelts_diagnostics/phase_modes_wt_pct/olivine",
        "/alphamelts_diagnostics/solidus_T_C",
        "/equilibrium_result/activity_coefficients",
        "/equilibrium_result/diagnostics",
        "/equilibrium_result/liquid_viscosity_Pa_s",
        "/equilibrium_result/liquidus_T_C",
        "/equilibrium_result/phase_compositions",
        "/equilibrium_result/phase_masses_kg/olivine",
        "/equilibrium_result/phase_species_kg",
        "/equilibrium_result/phase_species_mol",
        "/equilibrium_result/sulfur_saturation",
        "/equilibrium_result/vapor_pressures_Pa",
        "/equilibrium_result/vapor_pressures_source",
        "/equilibrium_result/warnings",
        "/last_vapor_pressure_diagnostic",
        "/last_vapor_pressure_diagnostic/activities",
        "/last_vapor_pressure_diagnostic/backend_vapor_pressures_Pa",
        "/last_vapor_pressure_diagnostic/backend_vapor_pressures_source",
        "/last_vapor_pressure_diagnostic/backend_warnings",
        "/last_vapor_pressure_diagnostic/kernel_vapor_pressure_warnings",
        "/last_vapor_pressure_diagnostic/pressure_control_authoritative",
        "/last_vapor_pressure_diagnostic/pressure_control_reason",
        "/last_vapor_pressure_diagnostic/projection_diagnostics",
        "/last_vapor_pressure_diagnostic/requested_pressure_bar",
        "/last_vapor_pressure_diagnostic/thermoengine_vapor_pressures_confirmed",
        "/last_vapor_pressure_diagnostic/vapor_pressure_zero_reason",
        "/last_vapor_pressure_diagnostic/vapor_pressures_Pa",
        "/last_vapor_pressure_diagnostic/vapor_pressures_source",
        "/last_vapor_pressure_diagnostic/vaporock_full_speciation_Pa",
        "/last_vapor_pressures_source",
    }
    for prefix in (
        "/equilibrium_result/vapor_pressures_Pa",
        "/equilibrium_result/vapor_pressures_source",
        "/last_vapor_pressure_diagnostic/vapor_pressures_Pa",
        "/last_vapor_pressure_diagnostic/vapor_pressures_source",
        "/last_vapor_pressures_source",
    ):
        paths.update(f"{prefix}/{species}" for species in SELECTED_VAPOR_SPECIES)
    paths.update(
        f"/last_vapor_pressure_diagnostic/vaporock_full_speciation_Pa/{species}"
        for species in VAPOROCK_FULL_SPECIES
    )
    eq = payload["equilibrium_result"]
    vapo = payload["last_vapor_pressure_diagnostic"]
    dynamic_maps = {
        "/alphamelts_diagnostics/activity_coefficients": payload[
            "alphamelts_diagnostics"
        ]["activity_coefficients"],
        "/alphamelts_diagnostics/phase_masses_kg": payload[
            "alphamelts_diagnostics"
        ]["phase_masses_kg"],
        "/alphamelts_diagnostics/phase_modes_wt_pct": payload[
            "alphamelts_diagnostics"
        ]["phase_modes_wt_pct"],
        "/equilibrium_result/activity_coefficients": eq[
            "activity_coefficients"
        ],
        "/equilibrium_result/phase_compositions": eq["phase_compositions"],
        "/equilibrium_result/phase_masses_kg": eq["phase_masses_kg"],
        "/equilibrium_result/phase_species_kg": eq["phase_species_kg"],
        "/equilibrium_result/phase_species_mol": eq["phase_species_mol"],
        "/equilibrium_result/vapor_pressures_Pa": eq["vapor_pressures_Pa"],
        "/equilibrium_result/vapor_pressures_source": eq["vapor_pressures_source"],
        "/last_vapor_pressures_source": payload["last_vapor_pressures_source"],
    }
    if include_followers and vapo:
        dynamic_maps.update(
            {
                "/last_vapor_pressure_diagnostic/vapor_pressures_Pa": vapo["vapor_pressures_Pa"],
                "/last_vapor_pressure_diagnostic/vapor_pressures_source": vapo["vapor_pressures_source"],
                "/last_vapor_pressure_diagnostic/vaporock_full_speciation_Pa": vapo["vaporock_full_speciation_Pa"],
                "/last_vapor_pressure_diagnostic/activities": vapo["activities"],
                "/last_vapor_pressure_diagnostic/backend_vapor_pressures_Pa": vapo[
                    "backend_vapor_pressures_Pa"
                ],
                "/last_vapor_pressure_diagnostic/backend_vapor_pressures_source": vapo[
                    "backend_vapor_pressures_source"
                ],
            }
        )
    def add_dynamic_paths(prefix: str, value: Any) -> None:
        if not isinstance(value, dict):
            return
        for name, child in value.items():
            child_path = f"{prefix}/{_pointer_escape(name)}"
            paths.add(child_path)
            add_dynamic_paths(child_path, child)

    for prefix, values in dynamic_maps.items():
        add_dynamic_paths(prefix, values)
    sulfur_prefix = "/equilibrium_result/sulfur_saturation"
    paths.update(
        f"{sulfur_prefix}/{name}"
        for name in (
            "SCSS_ppm",
            "SCAS_ppm",
            "S6_fraction",
            "S_in_sulfide_ppm",
            "S_in_sulfate_ppm",
            "calibration_status",
            "warnings",
        )
    )
    return [[path, _path_state(payload, path)] for path in _utf8_sorted(paths)]


def _compat_slice(shape: dict[str, Any], prefixes: Sequence[str]) -> dict[str, Any]:
    return {
        "paths": [
            pair
            for pair in shape["paths"]
            if any(pair[0].startswith(prefix) for prefix in prefixes)
        ],
        "compatibility_value_tokens": [
            pair
            for pair in shape["compatibility_value_tokens"]
            if any(pair[0].startswith(prefix) for prefix in prefixes)
        ],
    }


def _alpha_compat_slice(shape: dict[str, Any]) -> dict[str, Any]:
    excluded = (
        "/equilibrium_result/sulfur_saturation",
        "/equilibrium_result/vapor_pressures_Pa",
        "/equilibrium_result/vapor_pressures_source",
    )
    return {
        "paths": [
            pair
            for pair in shape["paths"]
            if (
                pair[0].startswith("/alphamelts_diagnostics")
                or pair[0].startswith("/equilibrium_result")
            )
            and not pair[0].startswith(excluded)
        ],
        "compatibility_value_tokens": [
            pair
            for pair in shape["compatibility_value_tokens"]
            if pair[0].startswith("/equilibrium_result")
            and not pair[0].startswith(excluded)
        ],
    }


def _output_hash(
    record: dict[str, Any],
    fields: Sequence[str],
    compat: dict[str, Any],
    structured_json_values: Mapping[str, Any],
) -> str:
    visible: dict[str, Any] = {}
    for field in fields:
        value = record.get(field)
        if field.endswith("_json") and value is not None:
            if field not in structured_json_values:
                raise CanonicalizationError(
                    f"output identity lacks materialized value for {field}"
                )
            value = structured_json_values[field]
        if field in {
            "authoritative",
            "phase_assemblage_available",
            "pressure_control_authoritative",
        } and value is not None:
            value = bool(value)
        visible[field] = value
    visible["legacy_compatibility"] = compat
    return sha256_bytes(encode("rr-output-v1", visible))


ALPHA_OUTPUT_FIELDS = (
    "engine_name", "engine_epoch", "artifact_kind", "consumer_id",
    "engine_config_sha256", "solver_config_sha256", "budget_config_sha256",
    "result_class", "status", "authoritative", "negative_class", "refusal_reason",
    "temperature_C", "pressure_bar", "fO2_log", "phases_present_json",
    "phase_masses_kg_json", "phase_modes_wt_pct_json", "phase_species_mol_json",
    "phase_species_kg_json", "phase_compositions_json", "liquid_fraction",
    "phase_assemblage_available", "liquid_composition_wt_pct_json",
    "activity_coefficients_json", "result_liquidus_T_C", "result_warnings_json",
    "result_diagnostics_json", "diagnostic_liquidus_T_C", "diagnostic_liquidus_T_K",
    "diagnostic_solidus_T_C", "liquid_fraction_path_json", "applied_fe3fet",
    "fe_redox_policy", "intrinsic_fO2_log", "backend_status",
    "backend_status_reason", "backend_warnings_json", "backend_diagnostics_json",
    "finder_iterations", "finder_samples_json", "finder_diagnostics_json",
    "curve_source", "curve_solidus_T_C", "curve_liquidus_T_C", "curve_path_json",
    "composition_derived",
)
VAPO_OUTPUT_FIELDS = (
    "engine_name", "engine_epoch", "artifact_kind", "consumer_id",
    "engine_config_sha256", "result_class", "status", "authoritative",
    "negative_class", "refusal_reason", "temperature_C", "pressure_bar", "fO2_log",
    "phases_present_json", "phase_masses_kg_json", "phase_species_mol_json",
    "phase_species_kg_json", "phase_compositions_json", "liquid_fraction",
    "phase_assemblage_available", "liquid_composition_wt_pct_json",
    "vapor_pressures_Pa_json", "vapor_pressures_source_json",
    "activity_coefficients_json", "result_liquidus_T_C", "result_warnings_json",
    "result_diagnostics_json", "vaporock_full_speciation_Pa_json",
    "transport_pO2_bar", "backend_status", "backend_warnings_json",
    "pressure_control_authoritative", "pressure_control_reason", "requested_pressure_bar",
    "projection_diagnostics_json", "backend_vapor_pressures_Pa_json",
    "backend_vapor_pressures_source_json", "thermoengine_vapor_pressures_confirmed_json",
    "vapor_pressure_zero_reason", "kernel_vapor_pressure_warnings_json",
)
SULF_OUTPUT_FIELDS = (
    "engine_name", "engine_epoch", "artifact_kind", "consumer_id",
    "engine_config_sha256", "applied_fe3fet_liq", "applied_fe3fet_source",
    "result_class", "status", "authoritative", "negative_class", "refusal_reason",
    "SCSS_ppm", "SCAS_ppm", "S6_fraction", "S_in_sulfide_ppm",
    "S_in_sulfate_ppm", "calibration_status", "warnings_json",
)


@dataclasses.dataclass
class MaterializedRow:
    source_rowid: int
    source_key_hash: str
    source_key_bytes: bytes
    source_payload_bytes: bytes
    state_identity: dict[str, Any]
    composition_vector: list[list[Any]]
    engine_configs: dict[str, dict[str, Any]]
    output_identity_json: dict[str, dict[str, Any]]
    json_projection_sources: dict[str, dict[str, Any]]
    scalar_projection_sources: dict[str, dict[str, Any]]
    projection_snapshot: dict[str, dict[str, Any] | None]
    solver_config: dict[str, Any]
    compatibility_shape: dict[str, Any]
    hub: dict[str, Any]
    compatibility: dict[str, Any]
    alpha: dict[str, Any]
    vaporock: dict[str, Any] | None
    sulfsat: dict[str, Any] | None


def _legacy_result_metadata(
    engine: str,
    status_fields: Mapping[str, Any],
    *,
    success_statuses: Mapping[str, frozenset[str]],
    physics_refusal_statuses: Mapping[str, frozenset[str]],
) -> dict[str, Any]:
    if not status_fields:
        raise ConversionError(f"{engine}: result status fields must be non-empty")
    if not (
        set(status_fields)
        == set(success_statuses)
        == set(physics_refusal_statuses)
    ):
        raise ConversionError(f"{engine}: result-status schema mismatch")
    normalized: dict[str, str] = {}
    for field, raw_status in status_fields.items():
        if not isinstance(raw_status, str) or not raw_status.strip():
            raise ConversionError(f"{engine}/{field}: expected non-empty status string")
        normalized[field] = raw_status.strip().lower()
        supported = success_statuses[field] | physics_refusal_statuses[field]
        if normalized[field] not in supported:
            raise ConversionError(
                f"{engine}/{field}: unsupported legacy result status "
                f"{normalized[field]!r}"
            )
    if all(
        normalized[field] in success_statuses[field]
        for field in normalized
    ):
        return {
            "result_class": "success",
            "authoritative": 1,
            "negative_class": None,
            "refusal_reason": None,
        }
    detail = ", ".join(
        f"{field}={normalized[field]!r}" for field in sorted(normalized)
    )
    return {
        "result_class": "physics_refusal",
        "authoritative": 0,
        "negative_class": "legacy_non_success_status",
        "refusal_reason": f"{engine} legacy result is not successful: {detail}",
    }


def _alpha_legacy_result_metadata(
    equilibrium_result: Mapping[str, Any],
    alpha_diagnostics: Mapping[str, Any],
) -> dict[str, Any]:
    return _legacy_result_metadata(
        "alphamelts",
        {
            "equilibrium_result.status": equilibrium_result.get("status"),
            "alphamelts_diagnostics.backend_status": alpha_diagnostics.get(
                "backend_status"
            ),
        },
        success_statuses={
            "equilibrium_result.status": frozenset({"ok"}),
            "alphamelts_diagnostics.backend_status": frozenset({"ok"}),
        },
        physics_refusal_statuses={
            "equilibrium_result.status": frozenset({"out_of_domain"}),
            "alphamelts_diagnostics.backend_status": frozenset(
                {"out_of_domain"}
            ),
        },
    )


def _vaporock_legacy_result_metadata(
    vaporock_diagnostics: Mapping[str, Any],
) -> dict[str, Any]:
    return _legacy_result_metadata(
        "vaporock",
        {
            "last_vapor_pressure_diagnostic.backend_status": (
                vaporock_diagnostics.get("backend_status")
            )
        },
        success_statuses={
            "last_vapor_pressure_diagnostic.backend_status": frozenset({"ok"})
        },
        physics_refusal_statuses={
            "last_vapor_pressure_diagnostic.backend_status": frozenset(
                {"out_of_domain"}
            )
        },
    )


def _sulfsat_legacy_result_metadata(
    sulfur_saturation: Mapping[str, Any],
) -> dict[str, Any]:
    return _legacy_result_metadata(
        "sulfsat",
        {
            "sulfur_saturation.calibration_status": sulfur_saturation.get(
                "calibration_status"
            )
        },
        success_statuses={
            "sulfur_saturation.calibration_status": frozenset({"in_range"})
        },
        physics_refusal_statuses={
            "sulfur_saturation.calibration_status": frozenset({"out_of_range"})
        },
    )


def materialize_legacy_row(
    row: sqlite3.Row,
    source_db_sha256: str,
    *,
    include_followers: bool = True,
) -> MaterializedRow:
    key_bytes = bytes(row["key_bytes"])
    payload_bytes = bytes(row["payload_bytes"])
    key_sha = sha256_bytes(key_bytes)
    payload_sha = sha256_bytes(payload_bytes)
    if key_sha != row["key_hash"] or key_sha != row["key_sha256"]:
        raise ConversionError("legacy key SHA columns disagree")
    if payload_sha != row["payload_sha256"]:
        raise ConversionError("legacy payload SHA disagrees")
    key, key_tokens = _parse_json_with_number_tokens(key_bytes)
    payload, payload_tokens = _parse_json_with_number_tokens(payload_bytes)
    _validate_legacy_key(key)
    _validate_legacy_payload(payload, validate_followers=include_followers)
    _validate_physics_key(row, key)
    if row["artifact"] != "equilibrium_post_record" or key["artifact"] != row["artifact"]:
        raise ConversionError("legacy artifact mismatch")
    if row["request_schema_version"] != key["schema_version"]:
        raise ConversionError("legacy request schema mismatch")
    if row["code_version"] != key["code_version"]:
        raise ConversionError("legacy code version mismatch")
    if row["engine_version"] != key["engine_version"]:
        raise ConversionError("legacy engine version mismatch")
    data_digests = json.loads(row["data_digests_json"])
    if data_digests != key["data_digests"]:
        raise ConversionError("legacy data digest projection mismatch")

    composition_pairs = [
        [pair[0], _f64(pair[1], f"composition/{pair[0]}")]
        for pair in key["composition_mol_fraction"]
    ]
    composition_map = {
        "process.cleaned_melt": {name: value for name, value in composition_pairs}
    }
    composition_vector = [["process.cleaned_melt", composition_pairs]]
    composition_json = display_json(composition_map)
    composition_hash = sha256_bytes(encode("rr-account-mol-v1", composition_vector))
    controls = key["controls"]
    applied_temperature_c = _f64(controls["T_K"], "controls/T_K") - 273.15
    applied_temperature_c = _f64(applied_temperature_c, "applied_temperature_C")
    applied_pressure = _f64(controls["pressure_bar"], "controls/pressure_bar")
    applied_fo2 = _f64(controls["log_fO2"], "controls/log_fO2")
    commanded_po2 = _f64(controls["pO2_bar"], "controls/pO2_bar")
    sulfur_ppm = _f64(key["sulfur_side"]["S_input_ppm"], "sulfur/S_input_ppm")

    selected_key_tokens = []
    token_paths = {
        "/controls/T_K",
        "/controls/log_fO2",
        "/controls/pO2_bar",
        "/controls/pressure_bar",
        "/sulfur_side/S_input_ppm",
    }
    token_paths.update(
        f"/composition_mol_fraction/{index}/1" for index in range(len(composition_pairs))
    )
    for pointer in _utf8_sorted(token_paths):
        if pointer not in key_tokens:
            raise CanonicalizationError(f"missing legacy key number token {pointer}")
        selected_key_tokens.append([pointer, key_tokens[pointer]])
    legacy_key_shape = {
        "schema": "rr-legacy-key-shape-v1",
        "composition_species": [pair[0] for pair in composition_pairs],
        "number_tokens": selected_key_tokens,
        "source_canonical_json": {
            "ensure_ascii": True,
            "sort_keys": True,
            "separators": [",", ":"],
        },
    }
    control_provenance = {
        "legacy_controls": key["controls"],
        "legacy_quantized": True,
        "legacy_redox": key["redox"],
        "requested_controls_unavailable": True,
        "transport_pO2_unavailable": True,
    }
    state_identity = {
        "legacy_key_sha256": key_sha,
        "legacy_request_schema_version": key["schema_version"],
        "composition_input_kind": "legacy_fraction",
        "composition_mol_by_account": composition_vector,
        "composition_total_mol": None,
        "legacy_composition_kg": None,
        "species_registry_digest": key["data_digests"]["species_formula_registry"],
        "requested_temperature_C": None,
        "applied_temperature_C": applied_temperature_c,
        "requested_pressure_bar": None,
        "applied_pressure_bar": applied_pressure,
        "requested_fO2_log": None,
        "applied_fO2_log": applied_fo2,
        "intrinsic_fO2_log": applied_fo2,
        "commanded_pO2_bar": commanded_po2,
        "transport_pO2_bar": None,
        "sulfur_input_ppm": sulfur_ppm,
    }
    state_bytes = encode(LEGACY_STATE_SCHEMA, state_identity)

    eq = payload["equilibrium_result"]
    alpha_diag = payload["alphamelts_diagnostics"]
    vapo_diag = payload["last_vapor_pressure_diagnostic"]
    alpha_result_metadata = _alpha_legacy_result_metadata(eq, alpha_diag)
    alpha_config = materialize_alphamelts_engine_config(
        {
            "model": key["model"]["model"],
            "mode": alpha_diag["mode"],
            "redox_buffer": None,
            "fO2_offset": None,
            "Fe3Fet_Liq": None,
            "require_petthermotools": False,
            "backend": {
                name: key["backend"][name]
                for name in ("backend_class", "backend_name")
            },
            "provider": {
                name: key["provider"][name]
                for name in (
                    "resolved_provider_id",
                    "resolved_role",
                    "authoritative_provider_id",
                    "fallback_provider_id",
                    "fallback_allowed",
                    "model",
                    "mode",
                )
            },
        }
    )
    vapo_config = {
        "entry_point": key["vapor_pressure_provider"]["resolved_provider_id"],
        "mode": vapo_diag.get("mode", "vaporock") if vapo_diag else "vaporock",
        "database_digest": None,
        "temperature_units": "C",
        "pressure_units": "bar",
        "vapor_pressure_units": "bar",
        "allowed_species": list(VAPOROCK_FULL_SPECIES),
        "data_digest": key["data_digests"]["vapor_pressures"],
        "provider": {
            name: key["vapor_pressure_provider"][name]
            for name in (
                "resolved_provider_id",
                "resolved_role",
                "authoritative_provider_id",
                "fallback_provider_id",
                "fallback_allowed",
                "model",
                "mode",
            )
        },
    }
    sulfsat_config = {
        "Fe3Fet_Liq": None,
        "calibration_version": key["sulfur_side"]["sulfsat_calibration_version"],
        "SCSS_model": "Smythe17",
        "SCAS_model": "Chowdhury-Dasgupta-2019",
        "S6_model": "Jugo-2010",
        "Fe_FeNiCu_Sulf": 0.65,
        "provider": key["sulfur_side"]["sulfsat_provider"],
        "available": key["sulfur_side"]["sulfsat_available"],
    }
    solver_config = dict(POINT_SOLVER_CONFIG)
    alpha_config_json = display_json(alpha_config)
    solver_config_json = display_json(solver_config)
    alpha_config_hash = sha256_bytes(encode("rr-engine-config-v1", alpha_config))
    solver_config_hash = sha256_bytes(encode("rr-solver-config-v1", solver_config))
    vapo_config_hash = sha256_bytes(encode("rr-engine-config-v1", vapo_config))
    sulfsat_config_hash = sha256_bytes(encode("rr-engine-config-v1", sulfsat_config))

    viscosity_pointer = "/equilibrium_result/liquid_viscosity_Pa_s"
    if viscosity_pointer not in payload_tokens:
        raise CanonicalizationError("missing legacy viscosity number token")
    if f64_bytes(float(payload_tokens[viscosity_pointer])) != f64_bytes(eq["liquid_viscosity_Pa_s"]):
        raise CanonicalizationError("legacy viscosity token bits disagree")
    compatibility_shape = {
        "schema": "rr-compat-shape-v1",
        "paths": _compatibility_paths(
            payload, include_followers=include_followers
        ),
        "compatibility_value_tokens": [
            [viscosity_pointer, payload_tokens[viscosity_pointer]]
        ],
    }
    compatibility_shape_json = display_json(compatibility_shape)

    capture_common = {
        "legacy_source": {
            "source_db_sha256": source_db_sha256,
            "source_rowid": int(row["legacy_rowid"]),
            "key_hash": row["key_hash"],
            "store_schema_version": row["store_schema_version"],
            "artifact": key["artifact"],
            "intent": key["intent"],
            "backend": key["backend"],
            "provider": key["provider"],
            "vapor_pressure_provider": key["vapor_pressure_provider"],
            "model": key["model"],
            "source_module_digest": key["source_module_digest"],
            "sulfur_side": key["sulfur_side"],
        }
    }
    alpha_native_input = {
        "composition_mol_fraction": composition_pairs,
        "T_K": _f64(controls["T_K"], "native/T_K"),
        "pressure_bar": applied_pressure,
        "fO2_log": applied_fo2,
        "pO2_bar": commanded_po2,
        "fe_redox_policy": key["redox"]["fe_redox_policy"],
        "fe_split": key["redox"]["fe_split"],
    }
    hub = {
        "state_id": int(row["legacy_rowid"]),
        "key_schema_version": LEGACY_STATE_SCHEMA,
        "corpus_version": LEGACY_CORPUS_VERSION,
        "input_completeness": "legacy-cleaned-fraction-only",
        "composition_input_kind": "legacy_fraction",
        "composition_mol_by_account_json": composition_json,
        "composition_mol_by_account_sha256": composition_hash,
        "composition_total_mol": None,
        "legacy_composition_kg_json": None,
        "species_registry_digest": key["data_digests"]["species_formula_registry"],
        "requested_temperature_C": None,
        "applied_temperature_C": applied_temperature_c,
        "requested_pressure_bar": None,
        "applied_pressure_bar": applied_pressure,
        "requested_fO2_log": None,
        "applied_fO2_log": applied_fo2,
        "intrinsic_fO2_log": applied_fo2,
        "commanded_pO2_bar": commanded_po2,
        "transport_pO2_bar": None,
        "sulfur_input_ppm": sulfur_ppm,
        "control_provenance_json": display_json(control_provenance),
        "canonical_state_bytes": state_bytes,
        "state_key_sha256": sha256_bytes(state_bytes),
        "legacy_key_sha256": key_sha,
        "legacy_request_schema_version": row["request_schema_version"],
        "legacy_key_shape_json": display_json(legacy_key_shape),
        "created_at": row["created_at"],
    }
    compatibility = {
        "state_id": int(row["legacy_rowid"]),
        "legacy_payload_sha256": payload_sha,
        "compatibility_shape_json": compatibility_shape_json,
        "created_at": row["created_at"],
    }
    alpha_control_audit = {
        "legacy_key_controls": key["controls"],
        "legacy_model": key["model"],
        "legacy_provider": key["provider"],
        "legacy_redox": key["redox"],
    }
    alpha = {
        "output_id": int(row["legacy_rowid"]),
        "state_id": int(row["legacy_rowid"]),
        "engine_name": "alphamelts",
        "engine_epoch": 0,
        "artifact_kind": "equilibrium_point",
        "consumer_id": "silicate_equilibrium",
        "engine_config_sha256": alpha_config_hash,
        "engine_config_json": alpha_config_json,
        "solver_config_sha256": solver_config_hash,
        "solver_config_json": solver_config_json,
        "budget_config_sha256": "none",
        "budget_config_json": None,
        "native_input_json": display_json(alpha_native_input),
        **alpha_result_metadata,
        "status": str(eq["status"]).strip().lower(),
        "temperature_C": _f64(eq["temperature_C"], "alpha/temperature_C"),
        "pressure_bar": _f64(eq["pressure_bar"], "alpha/pressure_bar"),
        "fO2_log": _f64(eq["fO2_log"], "alpha/fO2_log"),
        "phases_present_json": display_json(eq["phases_present"]),
        "phase_masses_kg_json": display_json(eq["phase_masses_kg"]),
        "phase_modes_wt_pct_json": display_json(alpha_diag["phase_modes_wt_pct"]),
        "phase_species_mol_json": display_json(eq["phase_species_mol"]),
        "phase_species_kg_json": display_json(eq["phase_species_kg"]),
        "phase_compositions_json": display_json(eq["phase_compositions"]),
        "liquid_fraction": _f64(eq["liquid_fraction"], "alpha/liquid_fraction"),
        "phase_assemblage_available": int(bool(eq["phase_assemblage_available"])),
        "liquid_composition_wt_pct_json": display_json(eq["liquid_composition_wt_pct"]),
        "activity_coefficients_json": display_json(eq["activity_coefficients"]),
        "result_liquidus_T_C": None if eq["liquidus_T_C"] is None else _f64(eq["liquidus_T_C"], "alpha/result_liquidus_T_C"),
        "result_warnings_json": display_json(eq["warnings"]),
        "result_diagnostics_json": None if "diagnostics" not in eq else display_json(eq["diagnostics"]),
        "diagnostic_liquidus_T_C": _f64(alpha_diag["liquidus_T_C"], "alpha/diagnostic_liquidus_T_C"),
        "diagnostic_liquidus_T_K": _f64(alpha_diag["liquidus_T_K"], "alpha/diagnostic_liquidus_T_K"),
        "diagnostic_solidus_T_C": None if alpha_diag["solidus_T_C"] is None else _f64(alpha_diag["solidus_T_C"], "alpha/diagnostic_solidus_T_C"),
        "liquid_fraction_path_json": display_json(alpha_diag["liquid_fraction_path"]),
        "applied_fe3fet": None if alpha_diag["applied_fe3fet"] is None else _f64(alpha_diag["applied_fe3fet"], "alpha/applied_fe3fet"),
        "fe_redox_policy": alpha_diag["fe_redox_policy"],
        "intrinsic_fO2_log": _f64(alpha_diag["intrinsic_fO2_log"], "alpha/intrinsic_fO2_log"),
        "backend_status": alpha_diag["backend_status"],
        "backend_status_reason": alpha_diag.get("backend_status_reason"),
        "backend_warnings_json": display_json(alpha_diag["backend_warnings"]),
        "backend_diagnostics_json": None if "backend_diagnostics" not in alpha_diag else display_json(alpha_diag["backend_diagnostics"]),
        "finder_iterations": None,
        "finder_samples_json": None,
        "finder_diagnostics_json": None,
        "curve_source": None,
        "curve_solidus_T_C": None,
        "curve_liquidus_T_C": None,
        "curve_path_json": None,
        "composition_derived": None,
        "control_audit_json": display_json(alpha_control_audit),
        "engine_version_metadata": alpha_diag["engine_version"],
        "adapter_version_metadata": key["backend"]["backend_version"],
        "code_version": row["code_version"],
        "data_digests_json": row["data_digests_json"],
        "output_sha256": "",
        "raw_payload": None,
        "raw_payload_format": None,
        "raw_payload_sha256": None,
        "capture_provenance_json": display_json(capture_common),
        "created_at": row["created_at"],
        "git_dirty": int(row["git_dirty"]),
    }
    alpha_identity_json = {
        "phases_present_json": eq["phases_present"],
        "phase_masses_kg_json": eq["phase_masses_kg"],
        "phase_modes_wt_pct_json": alpha_diag["phase_modes_wt_pct"],
        "phase_species_mol_json": eq["phase_species_mol"],
        "phase_species_kg_json": eq["phase_species_kg"],
        "phase_compositions_json": eq["phase_compositions"],
        "liquid_composition_wt_pct_json": eq["liquid_composition_wt_pct"],
        "activity_coefficients_json": eq["activity_coefficients"],
        "result_warnings_json": eq["warnings"],
        "result_diagnostics_json": eq.get("diagnostics"),
        "liquid_fraction_path_json": alpha_diag["liquid_fraction_path"],
        "backend_warnings_json": alpha_diag["backend_warnings"],
        "backend_diagnostics_json": alpha_diag.get("backend_diagnostics"),
    }
    alpha["output_sha256"] = _output_hash(
        alpha,
        ALPHA_OUTPUT_FIELDS,
        _alpha_compat_slice(compatibility_shape),
        alpha_identity_json,
    )

    vaporock: dict[str, Any] | None = None
    vapo_identity_json: dict[str, Any] = {}
    vapo_native_input: dict[str, Any] = {}
    vapo_control_audit: dict[str, Any] = {}
    if include_followers and vapo_diag:
        vapo_result_metadata = _vaporock_legacy_result_metadata(vapo_diag)
        vapo_native_input = {
            "activities": vapo_diag["activities"],
            "liquid_composition_wt_pct": eq["liquid_composition_wt_pct"],
            "temperature_C": eq["temperature_C"],
            "pressure_bar": eq["pressure_bar"],
            "fO2_log": eq["fO2_log"],
            "pO2_bar": vapo_diag["pO2_bar"],
        }
        vaporock = {
            "output_id": int(row["legacy_rowid"]),
            "state_id": int(row["legacy_rowid"]),
            "engine_name": "vaporock",
            "engine_epoch": 0,
            "artifact_kind": "equilibrium_point",
            "consumer_id": "silicate_equilibrium",
            "engine_config_sha256": vapo_config_hash,
            "engine_config_json": display_json(vapo_config),
            "native_input_json": display_json(vapo_native_input),
            **vapo_result_metadata,
            "status": str(vapo_diag["backend_status"]).strip().lower(),
            "temperature_C": _f64(eq["temperature_C"], "vapo/temperature_C"),
            "pressure_bar": _f64(eq["pressure_bar"], "vapo/pressure_bar"),
            "fO2_log": _f64(eq["fO2_log"], "vapo/fO2_log"),
            "phases_present_json": display_json(eq["phases_present"]),
            "phase_masses_kg_json": display_json(eq["phase_masses_kg"]),
            "phase_species_mol_json": display_json(eq["phase_species_mol"]),
            "phase_species_kg_json": display_json(eq["phase_species_kg"]),
            "phase_compositions_json": display_json(eq["phase_compositions"]),
            "liquid_fraction": _f64(eq["liquid_fraction"], "vapo/liquid_fraction"),
            "phase_assemblage_available": int(bool(eq["phase_assemblage_available"])),
            "liquid_composition_wt_pct_json": display_json(eq["liquid_composition_wt_pct"]),
            "vapor_pressures_Pa_json": display_json(vapo_diag["vapor_pressures_Pa"]),
            "vapor_pressures_source_json": display_json(vapo_diag["vapor_pressures_source"]),
            "activity_coefficients_json": display_json(vapo_diag["activities"]),
            "result_liquidus_T_C": None if eq["liquidus_T_C"] is None else _f64(eq["liquidus_T_C"], "vapo/result_liquidus_T_C"),
            "result_warnings_json": display_json(eq["warnings"]),
            "result_diagnostics_json": None if "diagnostics" not in eq else display_json(eq["diagnostics"]),
            "vaporock_full_speciation_Pa_json": display_json(vapo_diag["vaporock_full_speciation_Pa"]),
            "transport_pO2_bar": _f64(vapo_diag["pO2_bar"], "vapo/transport_pO2_bar"),
            "backend_status": vapo_diag["backend_status"],
            "backend_warnings_json": display_json(vapo_diag["backend_warnings"]),
            "pressure_control_authoritative": None if "pressure_control_authoritative" not in vapo_diag else int(bool(vapo_diag["pressure_control_authoritative"])),
            "pressure_control_reason": vapo_diag.get("pressure_control_reason"),
            "requested_pressure_bar": None if "requested_pressure_bar" not in vapo_diag else _f64(vapo_diag["requested_pressure_bar"], "vapo/requested_pressure_bar"),
            "projection_diagnostics_json": None if "projection_diagnostics" not in vapo_diag else display_json(vapo_diag["projection_diagnostics"]),
            "backend_vapor_pressures_Pa_json": display_json(vapo_diag["backend_vapor_pressures_Pa"]),
            "backend_vapor_pressures_source_json": display_json(vapo_diag["backend_vapor_pressures_source"]),
            "thermoengine_vapor_pressures_confirmed_json": None if "thermoengine_vapor_pressures_confirmed" not in vapo_diag else display_json(vapo_diag["thermoengine_vapor_pressures_confirmed"]),
            "vapor_pressure_zero_reason": vapo_diag.get("vapor_pressure_zero_reason"),
            "kernel_vapor_pressure_warnings_json": None if "kernel_vapor_pressure_warnings" not in vapo_diag else display_json(vapo_diag["kernel_vapor_pressure_warnings"]),
            "control_audit_json": "",
            "engine_version_metadata": vapo_diag["engine_version"],
            "adapter_version_metadata": key["vapor_pressure_provider"]["engine_version"],
            "code_version": row["code_version"],
            "data_digests_json": row["data_digests_json"],
            "output_sha256": "",
            "capture_provenance_json": display_json(capture_common),
            "created_at": row["created_at"],
            "git_dirty": int(row["git_dirty"]),
        }
        vapo_control_audit = {
            "legacy_vapor_pressure_provider": key["vapor_pressure_provider"],
            "legacy_pO2_bar": vapo_diag["pO2_bar"],
        }
        vaporock["control_audit_json"] = display_json(vapo_control_audit)
        vapo_identity_json = {
            "phases_present_json": eq["phases_present"],
            "phase_masses_kg_json": eq["phase_masses_kg"],
            "phase_species_mol_json": eq["phase_species_mol"],
            "phase_species_kg_json": eq["phase_species_kg"],
            "phase_compositions_json": eq["phase_compositions"],
            "liquid_composition_wt_pct_json": eq["liquid_composition_wt_pct"],
            "vapor_pressures_Pa_json": vapo_diag["vapor_pressures_Pa"],
            "vapor_pressures_source_json": vapo_diag["vapor_pressures_source"],
            "activity_coefficients_json": vapo_diag["activities"],
            "result_warnings_json": eq["warnings"],
            "result_diagnostics_json": eq.get("diagnostics"),
            "vaporock_full_speciation_Pa_json": vapo_diag["vaporock_full_speciation_Pa"],
            "backend_warnings_json": vapo_diag["backend_warnings"],
            "projection_diagnostics_json": vapo_diag.get("projection_diagnostics"),
            "backend_vapor_pressures_Pa_json": vapo_diag["backend_vapor_pressures_Pa"],
            "backend_vapor_pressures_source_json": vapo_diag["backend_vapor_pressures_source"],
            "thermoengine_vapor_pressures_confirmed_json": vapo_diag.get("thermoengine_vapor_pressures_confirmed"),
            "kernel_vapor_pressure_warnings_json": vapo_diag.get("kernel_vapor_pressure_warnings"),
        }
        vaporock["output_sha256"] = _output_hash(
            vaporock,
            VAPO_OUTPUT_FIELDS,
            _compat_slice(
                compatibility_shape,
                (
                    "/last_vapor_pressure_diagnostic",
                    "/last_vapor_pressures_source",
                    "/equilibrium_result/vapor_pressures",
                ),
            ),
            vapo_identity_json,
        )

    sulfur = eq["sulfur_saturation"]
    sulfsat: dict[str, Any] | None = None
    sulf_identity_json: dict[str, Any] = {}
    sulf_native_input: dict[str, Any] = {}
    sulf_control_audit: dict[str, Any] = {}
    if include_followers and sulfur is not None:
        sulf_result_metadata = _sulfsat_legacy_result_metadata(sulfur)
        sulf_native_input = {
            "liquid_composition_wt_pct": eq["liquid_composition_wt_pct"],
            "T_K": _f64(controls["T_K"], "sulfsat/T_K"),
            "P_bar": applied_pressure,
            "fO2_log": applied_fo2,
            "S_input_ppm": sulfur_ppm,
            "Fe3Fet_Liq": alpha_diag["applied_fe3fet"],
        }
        sulfsat = {
            "output_id": int(row["legacy_rowid"]),
            "state_id": int(row["legacy_rowid"]),
            "engine_name": "sulfsat",
            "engine_epoch": 0,
            "artifact_kind": "equilibrium_point",
            "consumer_id": "silicate_equilibrium",
            "engine_config_sha256": sulfsat_config_hash,
            "engine_config_json": display_json(sulfsat_config),
            "applied_fe3fet_liq": None if alpha_diag["applied_fe3fet"] is None else _f64(alpha_diag["applied_fe3fet"], "sulfsat/applied_fe3fet_liq"),
            "applied_fe3fet_source": None if alpha_diag["applied_fe3fet"] is None else "alphamelts_diagnostics.applied_fe3fet",
            "native_input_json": display_json(sulf_native_input),
            **sulf_result_metadata,
            "status": str(sulfur["calibration_status"]).strip().lower(),
            "SCSS_ppm": _f64(sulfur["SCSS_ppm"], "sulfsat/SCSS_ppm"),
            "SCAS_ppm": _f64(sulfur["SCAS_ppm"], "sulfsat/SCAS_ppm"),
            "S6_fraction": _f64(sulfur["S6_fraction"], "sulfsat/S6_fraction"),
            "S_in_sulfide_ppm": _f64(sulfur["S_in_sulfide_ppm"], "sulfsat/S_in_sulfide_ppm"),
            "S_in_sulfate_ppm": _f64(sulfur["S_in_sulfate_ppm"], "sulfsat/S_in_sulfate_ppm"),
            "calibration_status": sulfur["calibration_status"],
            "warnings_json": display_json(sulfur["warnings"]),
            "control_audit_json": "",
            "engine_version_metadata": key["sulfur_side"]["sulfsat_package_version"],
            "calibration_version_metadata": key["sulfur_side"]["sulfsat_calibration_version"],
            "adapter_version_metadata": key["sulfur_side"]["sulfsat_provider"],
            "code_version": row["code_version"],
            "data_digests_json": row["data_digests_json"],
            "output_sha256": "",
            "capture_provenance_json": display_json(capture_common),
            "created_at": row["created_at"],
            "git_dirty": int(row["git_dirty"]),
        }
        sulf_control_audit = {
            "applied_input_completeness": "legacy-stored-engine-inputs",
            "legacy_sulfur_side": key["sulfur_side"],
        }
        sulfsat["control_audit_json"] = display_json(sulf_control_audit)
        sulf_identity_json = {"warnings_json": sulfur["warnings"]}
        sulfsat["output_sha256"] = _output_hash(
            sulfsat,
            SULF_OUTPUT_FIELDS,
            _compat_slice(compatibility_shape, ("/equilibrium_result/sulfur_saturation",)),
            sulf_identity_json,
        )

    materialized = MaterializedRow(
        source_rowid=int(row["legacy_rowid"]),
        source_key_hash=row["key_hash"],
        source_key_bytes=key_bytes,
        source_payload_bytes=payload_bytes,
        state_identity=state_identity,
        composition_vector=composition_vector,
        engine_configs={"alphamelts": alpha_config, "vaporock": vapo_config, "sulfsat": sulfsat_config},
        output_identity_json={
            "alphamelts": alpha_identity_json,
            "vaporock": vapo_identity_json,
            "sulfsat": sulf_identity_json,
        },
        json_projection_sources={
            "hub": {
                "composition_mol_by_account_json": composition_map,
                "control_provenance_json": control_provenance,
                "legacy_key_shape_json": legacy_key_shape,
            },
            "compatibility": {
                "compatibility_shape_json": compatibility_shape,
            },
            "alphamelts": {
                **alpha_identity_json,
                "native_input_json": alpha_native_input,
                "control_audit_json": alpha_control_audit,
                "capture_provenance_json": capture_common,
            },
            "vaporock": (
                {
                    **vapo_identity_json,
                    "native_input_json": vapo_native_input,
                    "control_audit_json": vapo_control_audit,
                    "capture_provenance_json": capture_common,
                }
                if vaporock is not None
                else {}
            ),
            "sulfsat": (
                {
                    **sulf_identity_json,
                    "native_input_json": sulf_native_input,
                    "control_audit_json": sulf_control_audit,
                    "capture_provenance_json": capture_common,
                }
                if sulfsat is not None
                else {}
            ),
        },
        scalar_projection_sources={
            "hub": {
                "composition_input_kind": state_identity["composition_input_kind"],
                "composition_total_mol": state_identity["composition_total_mol"],
                "legacy_composition_kg_json": None,
                "species_registry_digest": state_identity["species_registry_digest"],
                "requested_temperature_C": state_identity["requested_temperature_C"],
                "applied_temperature_C": applied_temperature_c,
                "requested_pressure_bar": state_identity["requested_pressure_bar"],
                "applied_pressure_bar": applied_pressure,
                "requested_fO2_log": state_identity["requested_fO2_log"],
                "applied_fO2_log": applied_fo2,
                "intrinsic_fO2_log": applied_fo2,
                "commanded_pO2_bar": commanded_po2,
                "transport_pO2_bar": state_identity["transport_pO2_bar"],
                "sulfur_input_ppm": sulfur_ppm,
            },
            "alphamelts": {
                "temperature_C": _f64(eq["temperature_C"], "source/alpha/temperature_C"),
                "pressure_bar": _f64(eq["pressure_bar"], "source/alpha/pressure_bar"),
                "fO2_log": _f64(eq["fO2_log"], "source/alpha/fO2_log"),
                "liquid_fraction": _f64(eq["liquid_fraction"], "source/alpha/liquid_fraction"),
                "phase_assemblage_available": int(bool(eq["phase_assemblage_available"])),
                "result_liquidus_T_C": None if eq["liquidus_T_C"] is None else _f64(eq["liquidus_T_C"], "source/alpha/result_liquidus_T_C"),
                "diagnostic_liquidus_T_C": _f64(alpha_diag["liquidus_T_C"], "source/alpha/diagnostic_liquidus_T_C"),
                "diagnostic_liquidus_T_K": _f64(alpha_diag["liquidus_T_K"], "source/alpha/diagnostic_liquidus_T_K"),
                "diagnostic_solidus_T_C": None if alpha_diag["solidus_T_C"] is None else _f64(alpha_diag["solidus_T_C"], "source/alpha/diagnostic_solidus_T_C"),
                "applied_fe3fet": None if alpha_diag["applied_fe3fet"] is None else _f64(alpha_diag["applied_fe3fet"], "source/alpha/applied_fe3fet"),
                "intrinsic_fO2_log": _f64(alpha_diag["intrinsic_fO2_log"], "source/alpha/intrinsic_fO2_log"),
            },
            "vaporock": (
                {
                    "temperature_C": _f64(eq["temperature_C"], "source/vapo/temperature_C"),
                    "pressure_bar": _f64(eq["pressure_bar"], "source/vapo/pressure_bar"),
                    "fO2_log": _f64(eq["fO2_log"], "source/vapo/fO2_log"),
                    "liquid_fraction": _f64(eq["liquid_fraction"], "source/vapo/liquid_fraction"),
                    "phase_assemblage_available": int(bool(eq["phase_assemblage_available"])),
                    "result_liquidus_T_C": None if eq["liquidus_T_C"] is None else _f64(eq["liquidus_T_C"], "source/vapo/result_liquidus_T_C"),
                    "transport_pO2_bar": _f64(vapo_diag["pO2_bar"], "source/vapo/transport_pO2_bar"),
                    "pressure_control_authoritative": None if "pressure_control_authoritative" not in vapo_diag else int(bool(vapo_diag["pressure_control_authoritative"])),
                    "requested_pressure_bar": None if "requested_pressure_bar" not in vapo_diag else _f64(vapo_diag["requested_pressure_bar"], "source/vapo/requested_pressure_bar"),
                }
                if vaporock is not None
                else {}
            ),
            "sulfsat": (
                {
                    "applied_fe3fet_liq": None if alpha_diag["applied_fe3fet"] is None else _f64(alpha_diag["applied_fe3fet"], "source/sulf/applied_fe3fet_liq"),
                    "SCSS_ppm": _f64(sulfur["SCSS_ppm"], "source/sulf/SCSS_ppm"),
                    "SCAS_ppm": _f64(sulfur["SCAS_ppm"], "source/sulf/SCAS_ppm"),
                    "S6_fraction": _f64(sulfur["S6_fraction"], "source/sulf/S6_fraction"),
                    "S_in_sulfide_ppm": _f64(sulfur["S_in_sulfide_ppm"], "source/sulf/S_in_sulfide_ppm"),
                    "S_in_sulfate_ppm": _f64(sulfur["S_in_sulfate_ppm"], "source/sulf/S_in_sulfate_ppm"),
                }
                if sulfsat is not None
                else {}
            ),
        },
        projection_snapshot=copy.deepcopy(
            {
                "hub": hub,
                "compatibility": compatibility,
                "alphamelts": alpha,
                "vaporock": vaporock,
                "sulfsat": sulfsat,
            }
        ),
        solver_config=solver_config,
        compatibility_shape=compatibility_shape,
        hub=hub,
        compatibility=compatibility,
        alpha=alpha,
        vaporock=vaporock,
        sulfsat=sulfsat,
    )
    _writer_gate(materialized)
    return materialized


def _same_bits(left: Any, right: Any) -> bool:
    if type(left) is float or type(right) is float:
        try:
            return f64_bytes(float(left)) == f64_bytes(float(right))
        except (TypeError, ValueError, CanonicalizationError):
            return False
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(_same_bits(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_bits(a, b) for a, b in zip(left, right, strict=True))
    return left == right


def _require_json_projection(text: str | None, value: Any, label: str) -> None:
    if text is None:
        if value is not None:
            raise CanonicalizationError(f"{label}: missing JSON projection")
        return
    if text != display_json(value):
        raise CanonicalizationError(f"{label}: noncanonical display projection")
    if not _same_bits(json.loads(text), _display_value(value)):
        raise CanonicalizationError(f"{label}: display projection bit mismatch")


def _writer_gate(row: MaterializedRow) -> None:
    actual_records = {
        "hub": row.hub,
        "compatibility": row.compatibility,
        "alphamelts": row.alpha,
        "vaporock": row.vaporock,
        "sulfsat": row.sulfsat,
    }
    for label, expected in row.projection_snapshot.items():
        actual = actual_records[label]
        if expected is None or actual is None:
            if expected is not actual:
                raise CanonicalizationError(f"{label}: output presence mutated")
            continue
        if set(expected) != set(actual):
            raise CanonicalizationError(f"{label}: projection members mutated")
        for name, expected_value in expected.items():
            if not _same_bits(expected_value, actual[name]):
                raise CanonicalizationError(f"{label}.{name}: projection mutated")
    for label, fields in row.json_projection_sources.items():
        record = actual_records[label]
        if record is None and fields:
            raise CanonicalizationError(f"{label}: JSON source exists without row")
        if record is None:
            continue
        for name, source_value in fields.items():
            _require_json_projection(
                record[name], source_value, f"{label}.{name}"
            )
    for label, fields in row.scalar_projection_sources.items():
        record = actual_records[label]
        if record is None and fields:
            raise CanonicalizationError(f"{label}: scalar source exists without row")
        if record is None:
            continue
        for name, source_value in fields.items():
            if not _same_bits(record[name], source_value):
                raise CanonicalizationError(
                    f"{label}.{name}: typed scalar disagrees with source"
                )
    hub = row.hub
    if hub["canonical_state_bytes"] != encode(LEGACY_STATE_SCHEMA, row.state_identity):
        raise CanonicalizationError("canonical state bytes mismatch")
    if hub["state_key_sha256"] != sha256_bytes(hub["canonical_state_bytes"]):
        raise CanonicalizationError("state key hash mismatch")
    if hub["composition_mol_by_account_sha256"] != sha256_bytes(encode("rr-account-mol-v1", row.composition_vector)):
        raise CanonicalizationError("composition hash mismatch")
    for engine, record in (
        ("alphamelts", row.alpha),
        ("vaporock", row.vaporock),
        ("sulfsat", row.sulfsat),
    ):
        if record is None:
            continue
        config = row.engine_configs[engine]
        _require_json_projection(record["engine_config_json"], config, f"{engine} config")
        expected_config_hash = sha256_bytes(encode("rr-engine-config-v1", config))
        if record["engine_config_sha256"] != expected_config_hash:
            raise CanonicalizationError(f"{engine} config hash mismatch")
    _require_json_projection(row.alpha["solver_config_json"], row.solver_config, "Alpha solver")
    if row.alpha["solver_config_sha256"] != sha256_bytes(encode("rr-solver-config-v1", row.solver_config)):
        raise CanonicalizationError("Alpha solver hash mismatch")
    if row.alpha["budget_config_sha256"] != "none" or row.alpha["budget_config_json"] is not None:
        raise CanonicalizationError("success row has budget identity")
    if row.compatibility["compatibility_shape_json"] != display_json(row.compatibility_shape):
        raise CanonicalizationError("compatibility shape projection mismatch")
    for label, record in (
        ("hub", row.hub),
        ("compatibility", row.compatibility),
        ("alphamelts", row.alpha),
        ("vaporock", row.vaporock),
        ("sulfsat", row.sulfsat),
    ):
        if record is None:
            continue
        for name, text in record.items():
            if not name.endswith("_json") or text is None or name == "data_digests_json":
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise CanonicalizationError(f"{label}.{name}: invalid JSON") from exc
            if text != display_json(parsed):
                raise CanonicalizationError(
                    f"{label}.{name}: noncanonical display projection"
                )
    expected_hashes = (
        (
            row.alpha,
            ALPHA_OUTPUT_FIELDS,
            _alpha_compat_slice(row.compatibility_shape),
            row.output_identity_json["alphamelts"],
        ),
        (
            row.vaporock,
            VAPO_OUTPUT_FIELDS,
            _compat_slice(row.compatibility_shape, ("/last_vapor_pressure_diagnostic", "/last_vapor_pressures_source", "/equilibrium_result/vapor_pressures")),
            row.output_identity_json["vaporock"],
        ),
        (
            row.sulfsat,
            SULF_OUTPUT_FIELDS,
            _compat_slice(row.compatibility_shape, ("/equilibrium_result/sulfur_saturation",)),
            row.output_identity_json["sulfsat"],
        ),
    )
    for record, fields, compat, structured in expected_hashes:
        if record is not None and record["output_sha256"] != _output_hash(
            record, fields, compat, structured
        ):
            raise CanonicalizationError(f"{record['engine_name']} output hash mismatch")


def _json_column(row: Mapping[str, Any], name: str) -> Any:
    value = row[name]
    return None if value is None else json.loads(value)


def _shape_index(shape: dict[str, Any]) -> dict[str, str]:
    if shape.get("schema") != "rr-compat-shape-v1":
        raise ParityError("unsupported compatibility shape schema")
    result: dict[str, str] = {}
    previous: bytes | None = None
    for pair in shape.get("paths", []):
        if not isinstance(pair, list) or len(pair) != 2:
            raise ParityError("invalid compatibility path entry")
        pointer, state = pair
        if not isinstance(pointer, str) or state not in {
            "absent", "null", "empty_object", "empty_array", "present"
        }:
            raise ParityError("invalid compatibility path/state")
        encoded = pointer.encode("utf-8")
        if previous is not None and encoded <= previous:
            raise ParityError("compatibility paths are not strictly UTF-8 sorted")
        if pointer in result:
            raise ParityError("duplicate compatibility path")
        result[pointer] = state
        previous = encoded
    return result


def _shape_value(
    states: Mapping[str, str], pointer: str, typed: Any = MISSING
) -> Any:
    if pointer not in states:
        raise ParityError(f"compatibility path missing from ledger: {pointer}")
    state = states[pointer]
    if state == "absent":
        if typed is not MISSING and typed is not None:
            raise ParityError(f"{pointer}: typed value exists for absent path")
        return MISSING
    if state == "null":
        if typed is not MISSING and typed is not None:
            raise ParityError(f"{pointer}: typed value disagrees with null path")
        return None
    if state == "empty_object":
        if typed is not MISSING and typed != {}:
            raise ParityError(f"{pointer}: typed value disagrees with empty object")
        return {}
    if state == "empty_array":
        if typed is not MISSING and typed != []:
            raise ParityError(f"{pointer}: typed value disagrees with empty array")
        return []
    if typed is MISSING or typed is None or typed == {} or typed == []:
        raise ParityError(f"{pointer}: present path lacks a present typed value")
    return typed


def _new_rows(connection: sqlite3.Connection, state_id: int) -> dict[str, sqlite3.Row | None]:
    result: dict[str, sqlite3.Row | None] = {}
    for name in (
        "rr_input_states",
        "rr_legacy_compatibility",
        "rr_alphamelts_outputs",
        "rr_vaporock_outputs",
        "rr_sulfsat_outputs",
    ):
        result[name] = connection.execute(
            f"SELECT * FROM {name} WHERE state_id = ?", (state_id,)
        ).fetchone()
    return result


def _capture_source(alpha: Mapping[str, Any]) -> dict[str, Any]:
    capture = _json_column(alpha, "capture_provenance_json")
    try:
        return capture["legacy_source"]
    except (TypeError, KeyError) as exc:
        raise ParityError("Alpha capture provenance lacks legacy source") from exc


def reconstruct_legacy_key(connection: sqlite3.Connection, state_id: int) -> bytes:
    rows = _new_rows(connection, state_id)
    hub = rows["rr_input_states"]
    alpha = rows["rr_alphamelts_outputs"]
    if hub is None or alpha is None:
        raise ParityError("missing hub or Alpha row")
    capture = _capture_source(alpha)
    shape = _json_column(hub, "legacy_key_shape_json")
    if shape.get("schema") != "rr-legacy-key-shape-v1":
        raise ParityError("unsupported legacy key shape schema")
    species = shape.get("composition_species")
    if not isinstance(species, list) or species != _utf8_sorted(species):
        raise ParityError("legacy key composition order invalid")
    composition = _json_column(hub, "composition_mol_by_account_json")
    cleaned = composition.get("process.cleaned_melt")
    if not isinstance(cleaned, dict) or set(cleaned) != set(species):
        raise ParityError("legacy key composition shape disagrees with hub")
    provenance = _json_column(hub, "control_provenance_json")
    controls = provenance["legacy_controls"]
    key = {
        "artifact": capture["artifact"],
        "backend": capture["backend"],
        "code_version": alpha["code_version"],
        "composition_mol_fraction": [[name, cleaned[name]] for name in species],
        "controls": controls,
        "data_digests": json.loads(alpha["data_digests_json"]),
        "engine_version": alpha["engine_version_metadata"],
        "intent": capture["intent"],
        "model": capture["model"],
        "provider": capture["provider"],
        "redox": provenance["legacy_redox"],
        "schema_version": hub["legacy_request_schema_version"],
        "source_module_digest": capture["source_module_digest"],
        "sulfur_side": capture["sulfur_side"],
        "vapor_pressure_provider": capture["vapor_pressure_provider"],
    }
    number_tokens = shape.get("number_tokens")
    if not isinstance(number_tokens, list):
        raise ParityError("legacy key number-token ledger missing")
    tokens = {pair[0]: pair[1] for pair in number_tokens}
    if len(tokens) != len(number_tokens):
        raise ParityError("duplicate legacy key number token")
    return _legacy_json_bytes(key, tokens)


def reconstruct_legacy_payload(connection: sqlite3.Connection, state_id: int) -> bytes:
    rows = _new_rows(connection, state_id)
    compatibility = rows["rr_legacy_compatibility"]
    alpha = rows["rr_alphamelts_outputs"]
    vapo = rows["rr_vaporock_outputs"]
    sulf = rows["rr_sulfsat_outputs"]
    if compatibility is None or alpha is None:
        raise ParityError("missing compatibility or Alpha row")
    shape = _json_column(compatibility, "compatibility_shape_json")
    states = _shape_index(shape)
    token_pairs = shape.get("compatibility_value_tokens")
    if not isinstance(token_pairs, list):
        raise ParityError("compatibility value-token ledger missing")
    tokens = {pair[0]: pair[1] for pair in token_pairs}
    if set(tokens) != {"/equilibrium_result/liquid_viscosity_Pa_s"}:
        raise ParityError("unsupported compatibility value-token paths")
    viscosity = float(tokens["/equilibrium_result/liquid_viscosity_Pa_s"])

    if vapo is None:
        vapor_pressures = _shape_value(states, "/equilibrium_result/vapor_pressures_Pa")
        vapor_sources = _shape_value(states, "/equilibrium_result/vapor_pressures_source")
    else:
        vapor_pressures = _shape_value(
            states,
            "/equilibrium_result/vapor_pressures_Pa",
            _json_column(vapo, "vapor_pressures_Pa_json"),
        )
        vapor_sources = _shape_value(
            states,
            "/equilibrium_result/vapor_pressures_source",
            _json_column(vapo, "vapor_pressures_source_json"),
        )
    if vapor_pressures is MISSING or vapor_sources is MISSING:
        raise ParityError("required equilibrium vapor maps are absent")

    sulfur_state = states["/equilibrium_result/sulfur_saturation"]
    if sulf is None:
        sulfur_value = _shape_value(states, "/equilibrium_result/sulfur_saturation")
    else:
        sulfur_warnings = _shape_value(
            states,
            "/equilibrium_result/sulfur_saturation/warnings",
            _json_column(sulf, "warnings_json"),
        )
        if sulfur_warnings is MISSING:
            raise ParityError("SulfSat warning path unexpectedly absent")
        sulfur_typed = {
            "SCSS_ppm": sulf["SCSS_ppm"],
            "SCAS_ppm": sulf["SCAS_ppm"],
            "S6_fraction": sulf["S6_fraction"],
            "S_in_sulfide_ppm": sulf["S_in_sulfide_ppm"],
            "S_in_sulfate_ppm": sulf["S_in_sulfate_ppm"],
            "calibration_status": sulf["calibration_status"],
            "warnings": sulfur_warnings,
        }
        sulfur_value = _shape_value(
            states, "/equilibrium_result/sulfur_saturation", sulfur_typed
        )
    if sulfur_state == "present" and sulfur_value is MISSING:
        raise ParityError("present SulfSat path failed reconstruction")

    diagnostics = _shape_value(
        states,
        "/equilibrium_result/diagnostics",
        _json_column(alpha, "result_diagnostics_json"),
    )
    eq: dict[str, Any] = {
        "activity_coefficients": _json_column(alpha, "activity_coefficients_json"),
        "fO2_log": alpha["fO2_log"],
        "liquid_composition_wt_pct": _json_column(alpha, "liquid_composition_wt_pct_json"),
        "liquid_fraction": alpha["liquid_fraction"],
        "liquid_viscosity_Pa_s": viscosity,
        "liquidus_T_C": alpha["result_liquidus_T_C"],
        "phase_assemblage_available": bool(alpha["phase_assemblage_available"]),
        "phase_compositions": _json_column(alpha, "phase_compositions_json"),
        "phase_masses_kg": _json_column(alpha, "phase_masses_kg_json"),
        "phase_species_kg": _json_column(alpha, "phase_species_kg_json"),
        "phase_species_mol": _json_column(alpha, "phase_species_mol_json"),
        "phases_present": _json_column(alpha, "phases_present_json"),
        "pressure_bar": alpha["pressure_bar"],
        "status": alpha["status"],
        "sulfur_saturation": sulfur_value,
        "temperature_C": alpha["temperature_C"],
        "vapor_pressures_Pa": vapor_pressures,
        "vapor_pressures_source": vapor_sources,
        "warnings": _json_column(alpha, "result_warnings_json"),
    }
    if diagnostics is not MISSING:
        eq["diagnostics"] = diagnostics

    backend_diagnostics = _shape_value(
        states,
        "/alphamelts_diagnostics/backend_diagnostics",
        _json_column(alpha, "backend_diagnostics_json"),
    )
    backend_status_reason = _shape_value(
        states,
        "/alphamelts_diagnostics/backend_status_reason",
        alpha["backend_status_reason"],
    )
    alpha_config = _json_column(alpha, "engine_config_json")
    alpha_diag: dict[str, Any] = {
        "activity_coefficients": _json_column(alpha, "activity_coefficients_json"),
        "applied_fe3fet": alpha["applied_fe3fet"],
        "backend_status": alpha["backend_status"],
        "backend_warnings": _json_column(alpha, "backend_warnings_json"),
        "engine_version": alpha["engine_version_metadata"],
        "fO2_log": alpha["fO2_log"],
        "fe_redox_policy": alpha["fe_redox_policy"],
        "intrinsic_fO2_log": alpha["intrinsic_fO2_log"],
        "liquid_composition_wt_pct": _json_column(alpha, "liquid_composition_wt_pct_json"),
        "liquid_fraction": alpha["liquid_fraction"],
        "liquid_fraction_path": _json_column(alpha, "liquid_fraction_path_json"),
        "liquidus_T_C": alpha["diagnostic_liquidus_T_C"],
        "liquidus_T_K": alpha["diagnostic_liquidus_T_K"],
        "mode": alpha_config["mode"],
        "phase_masses_kg": _json_column(alpha, "phase_masses_kg_json"),
        "phase_modes_wt_pct": _json_column(alpha, "phase_modes_wt_pct_json"),
        "phases_present": _json_column(alpha, "phases_present_json"),
        "solidus_T_C": alpha["diagnostic_solidus_T_C"],
    }
    if backend_diagnostics is not MISSING:
        alpha_diag["backend_diagnostics"] = backend_diagnostics
    if backend_status_reason is not MISSING:
        alpha_diag["backend_status_reason"] = backend_status_reason

    whole_vapo_state = states["/last_vapor_pressure_diagnostic"]
    if vapo is None:
        last_vapo = _shape_value(states, "/last_vapor_pressure_diagnostic")
        if last_vapo is MISSING:
            raise ParityError("legacy payload requires top-level Vapo diagnostic")
        last_sources = _shape_value(states, "/last_vapor_pressures_source")
    else:
        vapo_config = _json_column(vapo, "engine_config_json")
        last_vapo_typed: dict[str, Any] = {
            "activities": _json_column(vapo, "activity_coefficients_json"),
            "backend_status": vapo["backend_status"],
            "backend_vapor_pressures_Pa": _json_column(vapo, "backend_vapor_pressures_Pa_json"),
            "backend_vapor_pressures_source": _json_column(vapo, "backend_vapor_pressures_source_json"),
            "backend_warnings": _json_column(vapo, "backend_warnings_json"),
            "engine_version": vapo["engine_version_metadata"],
            "mode": vapo_config["mode"],
            "pO2_bar": vapo["transport_pO2_bar"],
            "vapor_pressures_Pa": _json_column(vapo, "vapor_pressures_Pa_json"),
            "vapor_pressures_source": _json_column(vapo, "vapor_pressures_source_json"),
            "vaporock_full_speciation_Pa": _json_column(vapo, "vaporock_full_speciation_Pa_json"),
        }
        optional_vapo = {
            "thermoengine_vapor_pressures_confirmed": _json_column(vapo, "thermoengine_vapor_pressures_confirmed_json"),
            "vapor_pressure_zero_reason": vapo["vapor_pressure_zero_reason"],
            "pressure_control_authoritative": None if vapo["pressure_control_authoritative"] is None else bool(vapo["pressure_control_authoritative"]),
            "pressure_control_reason": vapo["pressure_control_reason"],
            "requested_pressure_bar": vapo["requested_pressure_bar"],
            "projection_diagnostics": _json_column(vapo, "projection_diagnostics_json"),
            "kernel_vapor_pressure_warnings": _json_column(vapo, "kernel_vapor_pressure_warnings_json"),
        }
        for name, typed in optional_vapo.items():
            pointer = f"/last_vapor_pressure_diagnostic/{name}"
            value = _shape_value(states, pointer, typed)
            if value is not MISSING:
                last_vapo_typed[name] = value
        last_vapo = _shape_value(
            states, "/last_vapor_pressure_diagnostic", last_vapo_typed
        )
        last_sources = _shape_value(
            states,
            "/last_vapor_pressures_source",
            _json_column(vapo, "vapor_pressures_source_json"),
        )
    if whole_vapo_state == "present" and last_vapo is MISSING:
        raise ParityError("present Vapo diagnostic failed reconstruction")
    if last_sources is MISSING:
        raise ParityError("legacy top-level vapor source alias is absent")

    payload = {
        "alphamelts_diagnostics": alpha_diag,
        "equilibrium_result": eq,
        "last_vapor_pressure_diagnostic": last_vapo,
        "last_vapor_pressures_source": last_sources,
    }
    return _legacy_json_bytes(payload, tokens)


def _compare_sql_record(
    expected: Mapping[str, Any], actual: sqlite3.Row, table: str
) -> None:
    if set(expected) != set(actual.keys()):
        missing = set(expected) - set(actual.keys())
        extra = set(actual.keys()) - set(expected)
        raise CanonicalizationError(f"{table}: SQL columns differ missing={missing} extra={extra}")
    for name, expected_value in expected.items():
        actual_value = actual[name]
        if isinstance(expected_value, float):
            if actual_value is None or f64_bytes(expected_value) != f64_bytes(actual_value):
                raise CanonicalizationError(f"{table}.{name}: REAL bits differ")
        elif isinstance(expected_value, bytes):
            if bytes(actual_value) != expected_value:
                raise CanonicalizationError(f"{table}.{name}: BLOB differs")
        elif actual_value != expected_value:
            raise CanonicalizationError(f"{table}.{name}: stored projection differs")


def verify_inserted_row(
    connection: sqlite3.Connection, materialized: MaterializedRow
) -> dict[str, bool]:
    rows = _new_rows(connection, materialized.source_rowid)
    expected_records = {
        "rr_input_states": materialized.hub,
        "rr_legacy_compatibility": materialized.compatibility,
        "rr_alphamelts_outputs": materialized.alpha,
        "rr_vaporock_outputs": materialized.vaporock,
        "rr_sulfsat_outputs": materialized.sulfsat,
    }
    for table, expected in expected_records.items():
        actual = rows[table]
        if expected is None:
            if actual is not None:
                raise CanonicalizationError(f"{table}: unexpected row")
        elif actual is None:
            raise CanonicalizationError(f"{table}: missing row")
        else:
            _compare_sql_record(expected, actual, table)
    key_bytes = reconstruct_legacy_key(connection, materialized.source_rowid)
    payload_bytes = reconstruct_legacy_payload(connection, materialized.source_rowid)
    key_ok = key_bytes == materialized.source_key_bytes
    payload_ok = payload_bytes == materialized.source_payload_bytes
    if not key_ok or not payload_ok:
        parts = []
        if not key_ok:
            parts.append("key")
        if not payload_ok:
            parts.append("payload")
        raise ParityError(f"byte parity failed for {' and '.join(parts)}")
    if sha256_bytes(key_bytes) != materialized.hub["legacy_key_sha256"]:
        raise ParityError("reconstructed key SHA mismatch")
    if sha256_bytes(payload_bytes) != materialized.compatibility["legacy_payload_sha256"]:
        raise ParityError("reconstructed payload SHA mismatch")
    return {"key": True, "payload": True}


def _insert_record(
    connection: sqlite3.Connection, table: str, record: Mapping[str, Any]
) -> None:
    columns = list(record)
    placeholders = ",".join("?" for _ in columns)
    connection.execute(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
        tuple(record[column] for column in columns),
    )


def _insert_materialized(
    connection: sqlite3.Connection, materialized: MaterializedRow
) -> None:
    _insert_record(connection, "rr_input_states", materialized.hub)
    _insert_record(connection, "rr_legacy_compatibility", materialized.compatibility)
    _insert_record(connection, "rr_alphamelts_outputs", materialized.alpha)
    if materialized.vaporock is not None:
        _insert_record(connection, "rr_vaporock_outputs", materialized.vaporock)
    if materialized.sulfsat is not None:
        _insert_record(connection, "rr_sulfsat_outputs", materialized.sulfsat)


def open_source_readonly(path: pathlib.Path) -> sqlite3.Connection:
    resolved = path.resolve()
    connection = sqlite3.connect(
        resolved.as_uri() + "?mode=ro", uri=True, timeout=5.0
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
        connection.close()
        raise ConversionError("source connection is not query-only")
    return connection


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _schema_dump_digest(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE sql IS NOT NULL
        ORDER BY type, name
        """
    ).fetchall()
    value = [list(row) for row in rows]
    return sha256_bytes(display_json(value).encode("utf-8"))


def _source_snapshot(
    path: pathlib.Path,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owned_connection = connection is None
    if connection is None:
        connection = open_source_readonly(path)
        connection.execute("BEGIN")
    try:
        serialized = connection.serialize()
    finally:
        if owned_connection:
            connection.close()
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "sha256": sha256_bytes(serialized),
        "size": len(serialized),
        "mtime_ns": stat.st_mtime_ns,
        "main_file_sha256": file_sha256(path),
        "main_file_size": stat.st_size,
    }


def _paths_alias(left: pathlib.Path, right: pathlib.Path) -> bool:
    if left.resolve() == right.resolve():
        return True
    return left.exists() and right.exists() and os.path.samefile(left, right)


def _sqlite_sidecars(path: pathlib.Path) -> tuple[pathlib.Path, ...]:
    return tuple(
        pathlib.Path(str(path) + suffix).resolve()
        for suffix in ("-wal", "-shm", "-journal")
    )


def _validate_sqlite_path_families(
    source: pathlib.Path,
    destination: pathlib.Path,
    report: pathlib.Path,
) -> None:
    for candidate, label in ((destination, "destination"), (report, "report")):
        if any(_paths_alias(candidate, sidecar) for sidecar in _sqlite_sidecars(source)):
            raise ConversionError(f"{label} aliases a source SQLite sidecar")
    if _paths_alias(source, destination) or any(
        _paths_alias(source, sidecar) for sidecar in _sqlite_sidecars(destination)
    ):
        raise ConversionError("destination SQLite family overlaps the source")
    if any(
        _paths_alias(report, candidate)
        for candidate in (destination, *_sqlite_sidecars(destination))
    ):
        raise ConversionError("report aliases the destination or a destination SQLite sidecar")


def _checkpoint_destination(
    connection: sqlite3.Connection,
    path: pathlib.Path,
) -> dict[str, Any]:
    row = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if row is None or len(row) != 3:
        return {
            "busy": None,
            "log_frames": None,
            "checkpointed_frames": None,
            "wal_size": None,
            "verified": False,
        }
    busy, log_frames, checkpointed_frames = (int(value) for value in row)
    wal_path = pathlib.Path(str(path) + "-wal")
    wal_size = wal_path.stat().st_size if wal_path.exists() else 0
    return {
        "busy": busy,
        "log_frames": log_frames,
        "checkpointed_frames": checkpointed_frames,
        "wal_size": wal_size,
        "verified": busy == 0
        and log_frames == checkpointed_frames
        and wal_size == 0,
    }


def _source_rows(
    connection: sqlite3.Connection,
    row_limit: int | None,
    row_ids: Sequence[int] | None = None,
) -> list[sqlite3.Row]:
    sql = "SELECT rowid AS legacy_rowid, * FROM reduced_real_equilibrium_payloads ORDER BY rowid"
    params: tuple[Any, ...] = ()
    if row_limit is not None and row_ids is not None:
        raise ValueError("row_limit and row_ids are mutually exclusive")
    if row_ids is not None:
        selected = tuple(int(value) for value in row_ids)
        if not selected or len(selected) != len(set(selected)) or min(selected) <= 0:
            raise ValueError("row_ids must be unique positive integers")
        placeholders = ",".join("?" for _ in selected)
        sql = (
            "SELECT rowid AS legacy_rowid, * "
            "FROM reduced_real_equilibrium_payloads "
            f"WHERE rowid IN ({placeholders}) ORDER BY rowid"
        )
        params = selected
    if row_limit is not None:
        if row_limit <= 0:
            raise ValueError("row_limit must be positive")
        sql += " LIMIT ?"
        params = (row_limit,)
    return connection.execute(sql, params).fetchall()


def _source_schema_version(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        "SELECT value FROM reduced_real_metadata WHERE key='store_schema_version'"
    ).fetchone()
    if row is None:
        raise ConversionError("source store_schema_version metadata missing")
    return str(row[0])


def _ensure_destination_schema(
    connection: sqlite3.Connection,
    *,
    source_sha256: str,
    created_at: str,
) -> None:
    has_metadata = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rr_metadata'"
    ).fetchone()
    ddl_sha = sha256_bytes(DDL.encode("utf-8"))
    if not has_metadata:
        connection.executescript(DDL)
        metadata = {
            "store_schema_version": DESTINATION_SCHEMA_VERSION,
            "design_version": DESIGN_VERSION,
            "ddl_sha256": ddl_sha,
            "source_db_sha256": source_sha256,
        }
        for key, value in metadata.items():
            connection.execute(
                "INSERT INTO rr_metadata(key, value) VALUES (?, ?)", (key, value)
            )
        reason = "legacy corpus import; no chemistry reassessment"
        for engine in ("alphamelts", "magemin", "vaporock", "sulfsat"):
            connection.execute(
                """
                INSERT INTO rr_engine_epochs(
                    engine_name, engine_epoch, is_current, owner_reason,
                    assessed_engine_version, created_at, created_by
                ) VALUES (?, 0, 1, ?, NULL, ?, 'scripts/cache_convert.py')
                """,
                (engine, reason, created_at),
            )
        connection.commit()
        return
    metadata = dict(connection.execute("SELECT key, value FROM rr_metadata"))
    expected = {
        "store_schema_version": DESTINATION_SCHEMA_VERSION,
        "design_version": DESIGN_VERSION,
        "ddl_sha256": ddl_sha,
        "source_db_sha256": source_sha256,
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise ConversionError(f"destination metadata mismatch for {key}")
    epoch_rows = connection.execute(
        "SELECT engine_name, engine_epoch, is_current, owner_reason FROM rr_engine_epochs ORDER BY engine_name"
    ).fetchall()
    if len(epoch_rows) != 4 or any(
        row[1] != 0
        or row[2] != 1
        or row[3] != "legacy corpus import; no chemistry reassessment"
        for row in epoch_rows
    ):
        raise ConversionError("destination engine epoch metadata mismatch")


def _open_destination(
    path: pathlib.Path, *, source_sha256: str, created_at: str
) -> RetryingConnection:
    connection = sqlite3.connect(
        path, timeout=5.0, factory=RetryingConnection
    )
    connection.busy_retry_count = 0
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    if str(mode).lower() != "wal":
        connection.close()
        raise ConversionError(f"destination refused WAL mode: {mode}")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA busy_timeout=5000")
    _ensure_destination_schema(
        connection, source_sha256=source_sha256, created_at=created_at
    )
    return connection


def _failure_diagnostic(row: sqlite3.Row, exc: BaseException) -> dict[str, Any]:
    return {
        "legacy_rowid": int(row["legacy_rowid"]),
        "key_hash": row["key_hash"],
        "error_type": type(exc).__name__,
        "error": str(exc),
        "raw_key_b64": base64.b64encode(bytes(row["key_bytes"])).decode("ascii"),
        "raw_payload_b64": base64.b64encode(bytes(row["payload_bytes"])).decode("ascii"),
    }


def _new_report(source: dict[str, Any], selected_count: int) -> dict[str, Any]:
    table_names = (
        "rr_input_states",
        "rr_legacy_compatibility",
        "rr_alphamelts_outputs",
        "rr_magemin_outputs",
        "rr_vaporock_outputs",
        "rr_sulfsat_outputs",
    )
    return {
        "design_version": DESIGN_VERSION,
        "destination_schema_version": DESTINATION_SCHEMA_VERSION,
        "source": source,
        "selected_source_rows": selected_count,
        "alpha_preflight": {
            "checked": 0,
            "failed": 0,
            "fully_green": False,
            "runtime_s": 0.0,
        },
        "tables": {
            name: {
                "converted": 0,
                "parity_failed": 0,
                "rejected": 0,
                "skipped": 0,
            }
            for name in table_names
        },
        "destination_counts": {},
        "result_class_counts": {
            "alphamelts": {"success": 0, "physics_refusal": 0, "budget_refusal": 0},
            "magemin": {"success": 0, "physics_refusal": 0, "budget_refusal": 0},
            "vaporock": {"success": 0, "physics_refusal": 0},
            "sulfsat": {"success": 0, "physics_refusal": 0},
        },
        "field_presence_census": {},
        "parity_rows": [],
        "failures": [],
        "first_failing_key": None,
        "duplicate_equality_checks": {
            "liquid_composition": selected_count,
            "liquid_fraction": selected_count,
            "phases_present": selected_count,
            "phase_masses": selected_count,
            "activity_coefficients": selected_count,
            "fO2": selected_count,
            "vapor_pressures": selected_count,
            "vapor_sources": selected_count,
            "top_level_vapor_source_alias": selected_count,
            "mismatches": 0,
        },
        "explicit_drops": {
            "physics_bucket_schema_version": {
                "count": selected_count,
                "reason": "derived from lossy legacy key; rebuildable only under reviewed future policy",
            },
            "physics_bucket_sha256": {
                "count": selected_count,
                "reason": "derived legacy bucket hash; validated before drop",
            },
            "replay_scope_sha256": {
                "count": selected_count,
                "reason": "provider/config provenance is typed; validated and rebuildable",
            },
            "physics_key_bytes": {
                "count": selected_count,
                "reason": "duplicate/derived compatibility object; all leaves validated",
            },
            "equilibrium_result.liquid_viscosity_Pa_s": {
                "count": selected_count,
                "reason": "SC-50 unsourced constant; exact token retained only in compatibility ledger",
            },
            "last_vapor_pressures_source": {
                "count": selected_count,
                "reason": "physical duplicate validated; compatibility alias reconstructed",
            },
            "legacy_key_bytes": {
                "count": selected_count,
                "reason": "omitted only after complete mapping and byte-parity reconstruction",
            },
            "legacy_payload_bytes": {
                "count": selected_count,
                "reason": "omitted only after complete mapping and byte-parity reconstruction",
            },
            "ledger_transition": {
                "count": 0,
                "reason": "diagnostic engines have no ledger authority; any non-null value is a hard error",
            },
        },
        "wal_retry_statistics": {"busy_or_locked_retries": 0},
        "foreign_key_check": [],
        "integrity_check": None,
        "row_count_reconciliation": {},
        "status": "running",
        "runtime_s": 0.0,
    }


def _mark_failed_tables(
    report: dict[str, Any],
    materialized: MaterializedRow | None,
    exc: BaseException,
    *,
    phase: str,
) -> None:
    if materialized is None:
        if phase == "alpha":
            tables = (
                "rr_input_states",
                "rr_legacy_compatibility",
                "rr_alphamelts_outputs",
            )
        else:
            message = str(exc).lower()
            if (
                "last_vapor_pressure" in message
                or "payload/vapo" in message
                or "vaporock" in message
            ):
                tables = ("rr_vaporock_outputs",)
            elif "sulfur_saturation" in message or "sulfsat" in message:
                tables = ("rr_sulfsat_outputs",)
            else:
                tables = (
                    "rr_input_states",
                    "rr_legacy_compatibility",
                    "rr_alphamelts_outputs",
                )
        for table in tables:
            report["tables"][table]["rejected"] += 1
        return
    for table in (
        "rr_input_states",
        "rr_legacy_compatibility",
        "rr_alphamelts_outputs",
    ):
        report["tables"][table]["parity_failed"] += 1
    if materialized.vaporock is not None:
        report["tables"]["rr_vaporock_outputs"]["parity_failed"] += 1
    if materialized.sulfsat is not None:
        report["tables"]["rr_sulfsat_outputs"]["parity_failed"] += 1


def _count_table_event(
    report: dict[str, Any], materialized: MaterializedRow, event: str
) -> None:
    for table in (
        "rr_input_states",
        "rr_legacy_compatibility",
        "rr_alphamelts_outputs",
    ):
        report["tables"][table][event] += 1
    if materialized.vaporock is not None:
        report["tables"]["rr_vaporock_outputs"][event] += 1
    if materialized.sulfsat is not None:
        report["tables"]["rr_sulfsat_outputs"][event] += 1


def _write_report(path: pathlib.Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _checkpoint_value(rowid: int, key_hash: str) -> str:
    return f"rowid:{rowid}:{key_hash}"


def _checkpoint_rowid(value: str | None) -> int:
    if value is None:
        return 0
    parts = value.split(":", 2)
    if len(parts) != 3 or parts[0] != "rowid":
        raise ConversionError("destination checkpoint is not legacy-rowid based")
    return int(parts[1])


def _upsert_checkpoint(
    connection: sqlite3.Connection,
    *,
    source_sha256: str,
    source_schema_version: str,
    last_value: str | None,
    source_row_count: int,
    status: str,
    report_path: pathlib.Path,
) -> None:
    converted = connection.execute(
        "SELECT COUNT(*) FROM rr_alphamelts_outputs"
    ).fetchone()[0]
    existing = connection.execute(
        "SELECT 1 FROM rr_migration_checkpoints WHERE source_db_sha256=?",
        (source_sha256,),
    ).fetchone()
    values = (
        source_schema_version,
        DESTINATION_SCHEMA_VERSION,
        last_value,
        converted,
        source_row_count,
        status,
        str(report_path.resolve()),
        _utc_now(),
        source_sha256,
    )
    if existing:
        connection.execute(
            """
            UPDATE rr_migration_checkpoints
            SET source_schema_version=?, destination_schema_version=?,
                last_legacy_key_hash=?, converted_source_rows=?, source_row_count=?,
                status=?, report_path=?, updated_at=?
            WHERE source_db_sha256=?
            """,
            values,
        )
    else:
        connection.execute(
            """
            INSERT INTO rr_migration_checkpoints(
                source_schema_version, destination_schema_version,
                last_legacy_key_hash, converted_source_rows, source_row_count,
                status, report_path, updated_at, source_db_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )


def _preflight_alpha(
    rows: Sequence[sqlite3.Row], source_sha256: str, report: dict[str, Any]
) -> bool:
    started = time.monotonic()
    for source_row in rows:
        try:
            materialized = materialize_legacy_row(
                source_row, source_sha256, include_followers=False
            )
            if materialized.alpha["engine_name"] != "alphamelts":
                raise ConversionError("primary row did not materialize as AlphaMELTS")
            for pointer, state in materialized.compatibility_shape["paths"]:
                path_counts = report["field_presence_census"].setdefault(
                    pointer,
                    {
                        "absent": 0,
                        "null": 0,
                        "empty_object": 0,
                        "empty_array": 0,
                        "present": 0,
                    },
                )
                path_counts[state] += 1
            report["alpha_preflight"]["checked"] += 1
        except Exception as exc:
            report["alpha_preflight"]["failed"] += 1
            _mark_failed_tables(
                report, None, exc, phase="alpha"
            )
            report["failures"].append(_failure_diagnostic(source_row, exc))
            if report["first_failing_key"] is None:
                report["first_failing_key"] = source_row["key_hash"]
    report["alpha_preflight"]["runtime_s"] = round(
        time.monotonic() - started, 6
    )
    report["alpha_preflight"]["fully_green"] = (
        report["alpha_preflight"]["failed"] == 0
        and report["alpha_preflight"]["checked"] == len(rows)
    )
    return bool(report["alpha_preflight"]["fully_green"])


def convert_database(
    source: pathlib.Path | str,
    destination: pathlib.Path | str,
    report_path: pathlib.Path | str,
    *,
    row_limit: int | None = None,
    row_ids: Sequence[int] | None = None,
    enforce_expected_counts: bool = True,
    require_sibling: bool = True,
    batch_size: int = 50,
) -> dict[str, Any]:
    started = time.monotonic()
    source_path = pathlib.Path(source).resolve()
    destination_path = pathlib.Path(destination).resolve()
    report_file = pathlib.Path(report_path).resolve()
    if not source_path.is_file():
        raise ConversionError(f"source database not found: {source_path}")
    if len({source_path, destination_path, report_file}) != 3:
        raise ConversionError("source, destination, and report paths must all differ")
    for candidate, label in (
        (destination_path, "destination"),
        (report_file, "report"),
    ):
        if candidate.exists() and os.path.samefile(source_path, candidate):
            raise ConversionError(f"{label} is the same file as the source")
    if report_file.exists() and destination_path.exists() and os.path.samefile(
        report_file, destination_path
    ):
        raise ConversionError("report is the same file as the destination")
    _validate_sqlite_path_families(source_path, destination_path, report_file)
    if require_sibling and (
        source_path.parent != destination_path.parent
        or destination_path.parent != report_file.parent
    ):
        raise ConversionError("destination and report must be alongside source")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    source_connection = open_source_readonly(source_path)
    try:
        source_connection.execute("BEGIN")
        source_before = _source_snapshot(source_path, source_connection)
        source_schema_version = _source_schema_version(source_connection)
        source_before["schema_dump_sha256"] = _schema_dump_digest(source_connection)
        source_before["table_row_count"] = source_connection.execute(
            "SELECT COUNT(*) FROM reduced_real_equilibrium_payloads"
        ).fetchone()[0]
        source_rows = _source_rows(source_connection, row_limit, row_ids)
        if row_ids is not None and len(source_rows) != len(row_ids):
            raise ConversionError("one or more selected legacy rowids are absent")
        report = _new_report(source_before, len(source_rows))
        if enforce_expected_counts and source_before["table_row_count"] != EXPECTED_COUNTS["source"]:
            report["failures"].append(
                {
                    "error_type": "RowCountError",
                    "error": f"source count {source_before['table_row_count']} != {EXPECTED_COUNTS['source']}",
                }
            )
            report["status"] = "failed"
            report["runtime_s"] = round(time.monotonic() - started, 6)
            _write_report(report_file, report)
            return report
        if not _preflight_alpha(source_rows, source_before["sha256"], report):
            report["status"] = "failed"
            report["runtime_s"] = round(time.monotonic() - started, 6)
            _write_report(report_file, report)
            return report

        destination_path.parent.mkdir(parents=True, exist_ok=True)
        destination_connection = _open_destination(
            destination_path,
            source_sha256=source_before["sha256"],
            created_at=_utc_now(),
        )
        try:
            checkpoint = destination_connection.execute(
                "SELECT * FROM rr_migration_checkpoints WHERE source_db_sha256=?",
                (source_before["sha256"],),
            ).fetchone()
            if checkpoint is not None:
                if checkpoint["source_schema_version"] != source_schema_version:
                    raise ConversionError("checkpoint source schema mismatch")
                if checkpoint["destination_schema_version"] != DESTINATION_SCHEMA_VERSION:
                    raise ConversionError("checkpoint destination schema mismatch")
                if checkpoint["source_row_count"] != len(source_rows):
                    raise ConversionError("checkpoint source-row count mismatch")
                _checkpoint_rowid(checkpoint["last_legacy_key_hash"])

            contiguous_rowid = 0
            contiguous_key_hash: str | None = None
            contiguous_open = True
            for batch_start in range(0, len(source_rows), batch_size):
                batch = source_rows[batch_start : batch_start + batch_size]
                destination_connection.execute("BEGIN IMMEDIATE")
                for source_row in batch:
                    materialized: MaterializedRow | None = None
                    savepoint = f"legacy_row_{int(source_row['legacy_rowid'])}"
                    destination_connection.execute(f"SAVEPOINT {savepoint}")
                    try:
                        materialized = materialize_legacy_row(
                            source_row, source_before["sha256"]
                        )
                        existing = destination_connection.execute(
                            "SELECT state_id FROM rr_input_states WHERE legacy_key_sha256=?",
                            (materialized.hub["legacy_key_sha256"],),
                        ).fetchone()
                        if existing is None:
                            _insert_materialized(destination_connection, materialized)
                            event = "converted"
                        else:
                            if int(existing["state_id"]) != materialized.source_rowid:
                                raise ConversionError("legacy key maps to different state_id")
                            event = "skipped"
                        parity = verify_inserted_row(
                            destination_connection, materialized
                        )
                        for engine, accepted in (
                            ("alphamelts", materialized.alpha),
                            ("vaporock", materialized.vaporock),
                            ("sulfsat", materialized.sulfsat),
                        ):
                            if accepted is not None:
                                report["result_class_counts"][engine][
                                    accepted["result_class"]
                                ] += 1
                        destination_connection.execute(f"RELEASE {savepoint}")
                        _count_table_event(report, materialized, event)
                        report["parity_rows"].append(
                            {
                                "legacy_rowid": materialized.source_rowid,
                                "key_hash": materialized.source_key_hash,
                                "key_byte_equal": parity["key"],
                                "payload_byte_equal": parity["payload"],
                                "status": event,
                            }
                        )
                        if contiguous_open:
                            contiguous_rowid = materialized.source_rowid
                            contiguous_key_hash = materialized.source_key_hash
                    except Exception as exc:
                        destination_connection.execute(
                            f"ROLLBACK TO {savepoint}"
                        )
                        destination_connection.execute(f"RELEASE {savepoint}")
                        contiguous_open = False
                        _mark_failed_tables(
                            report,
                            materialized,
                            exc,
                            phase="followers",
                        )
                        report["failures"].append(_failure_diagnostic(source_row, exc))
                        if report["first_failing_key"] is None:
                            report["first_failing_key"] = source_row["key_hash"]
                last_value = (
                    None
                    if contiguous_rowid == 0 or contiguous_key_hash is None
                    else _checkpoint_value(contiguous_rowid, contiguous_key_hash)
                )
                _upsert_checkpoint(
                    destination_connection,
                    source_sha256=source_before["sha256"],
                    source_schema_version=source_schema_version,
                    last_value=last_value,
                    source_row_count=len(source_rows),
                    status="running",
                    report_path=report_file,
                )
                destination_connection.commit()

            table_names = tuple(report["tables"])
            report["destination_counts"] = {
                table: destination_connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in table_names
            }
            report["foreign_key_check"] = [
                list(row)
                for row in destination_connection.execute(
                    "PRAGMA foreign_key_check"
                ).fetchall()
            ]
            integrity = destination_connection.execute(
                "PRAGMA integrity_check"
            ).fetchall()
            report["integrity_check"] = [row[0] for row in integrity]
            count_failures: dict[str, dict[str, int]] = {}
            expected = EXPECTED_COUNTS if enforce_expected_counts else {
                "rr_input_states": len(source_rows),
                "rr_legacy_compatibility": len(source_rows),
                "rr_alphamelts_outputs": len(source_rows),
                "rr_magemin_outputs": 0,
                "rr_vaporock_outputs": sum(
                    1
                    for row in source_rows
                    if json.loads(bytes(row["payload_bytes"]))["last_vapor_pressure_diagnostic"]
                ),
                "rr_sulfsat_outputs": sum(
                    1
                    for row in source_rows
                    if json.loads(bytes(row["payload_bytes"]))["equilibrium_result"]["sulfur_saturation"] is not None
                ),
            }
            for table, actual in report["destination_counts"].items():
                wanted = expected[table]
                report["row_count_reconciliation"][table] = {
                    "expected": wanted,
                    "actual": actual,
                    "match": actual == wanted,
                }
                if actual != wanted:
                    count_failures[table] = {"expected": wanted, "actual": actual}
            clean = (
                not report["failures"]
                and not report["foreign_key_check"]
                and report["integrity_check"] == ["ok"]
                and not count_failures
            )
            final_status = "complete" if clean else "failed"
            last = source_rows[-1] if clean and source_rows else None
            last_value = (
                None
                if last is None
                else _checkpoint_value(int(last["legacy_rowid"]), last["key_hash"])
            )
            _upsert_checkpoint(
                destination_connection,
                source_sha256=source_before["sha256"],
                source_schema_version=source_schema_version,
                last_value=last_value,
                source_row_count=len(source_rows),
                status=final_status,
                report_path=report_file,
            )
            destination_connection.commit()
            destination_checkpoint = _checkpoint_destination(
                destination_connection, destination_path
            )
            report["destination_checkpoint"] = destination_checkpoint
            if not destination_checkpoint["verified"]:
                final_status = "failed"
                report["failures"].append(
                    {
                        "error_type": "DestinationCheckpointError",
                        "error": "destination WAL checkpoint did not fully truncate",
                        "checkpoint": destination_checkpoint,
                    }
                )
                _upsert_checkpoint(
                    destination_connection,
                    source_sha256=source_before["sha256"],
                    source_schema_version=source_schema_version,
                    last_value=last_value,
                    source_row_count=len(source_rows),
                    status="failed",
                    report_path=report_file,
                )
                destination_connection.commit()
            report["wal_retry_statistics"]["busy_or_locked_retries"] = (
                destination_connection.busy_retry_count
            )
            report["status"] = final_status
        finally:
            destination_connection.close()
    finally:
        source_connection.close()

    try:
        source_after_connection = open_source_readonly(source_path)
        try:
            source_after_connection.execute("BEGIN")
            source_after = _source_snapshot(source_path, source_after_connection)
            source_after["schema_dump_sha256"] = _schema_dump_digest(
                source_after_connection
            )
            source_after["table_row_count"] = source_after_connection.execute(
                "SELECT COUNT(*) FROM reduced_real_equilibrium_payloads"
            ).fetchone()[0]
        finally:
            source_after_connection.close()
    except Exception as exc:
        source_after = {
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    report["source_after"] = source_after
    source_identity_fields = (
        "path",
        "sha256",
        "schema_dump_sha256",
        "table_row_count",
    )
    if any(
        source_after.get(field) != source_before.get(field)
        for field in source_identity_fields
    ):
        report["failures"].append(
            {
                "error_type": "SourceMutationError",
                "error": "source logical identity, schema, or row count changed during conversion",
            }
        )
        report["status"] = "failed"
        failed_connection = _open_destination(
            destination_path,
            source_sha256=source_before["sha256"],
            created_at=_utc_now(),
        )
        try:
            failed_connection.execute(
                """
                UPDATE rr_migration_checkpoints
                SET status='failed', updated_at=?
                WHERE source_db_sha256=?
                """,
                (_utc_now(), source_before["sha256"]),
            )
            failed_connection.commit()
            failed_checkpoint = _checkpoint_destination(
                failed_connection, destination_path
            )
            report["destination_checkpoint_after_source_mutation"] = failed_checkpoint
            if not failed_checkpoint["verified"]:
                report["failures"].append(
                    {
                        "error_type": "DestinationCheckpointError",
                        "error": "failed checkpoint update remains in destination WAL",
                        "checkpoint": failed_checkpoint,
                    }
                )
        finally:
            failed_connection.close()
    report["destination"] = {
        "path": str(destination_path),
        "sha256": file_sha256(destination_path),
        "size": destination_path.stat().st_size,
    }
    report["runtime_s"] = round(time.monotonic() - started, 6)
    _write_report(report_file, report)
    return report


def _summary(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": report["status"],
        "tables": report["tables"],
        "runtime_s": report["runtime_s"],
        "first_failing_key": report["first_failing_key"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=pathlib.Path)
    parser.add_argument("--dest", required=True, type=pathlib.Path)
    parser.add_argument("--report", required=True, type=pathlib.Path)
    parser.add_argument("--batch-size", type=int, default=50)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        report = convert_database(
            arguments.source,
            arguments.dest,
            arguments.report,
            batch_size=arguments.batch_size,
        )
    except Exception as exc:
        print(
            json.dumps(
                {"status": "failed", "error_type": type(exc).__name__, "error": str(exc)},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(_summary(report), sort_keys=True))
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
