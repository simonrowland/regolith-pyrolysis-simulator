"""SQLite storage for the expedited AlphaMELTS grid grinder."""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import math
import os
import socket
import sqlite3
import time
import urllib.parse
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VARIANT = "alphamelts-expedited-v1"
GRID_REALIZATION_REVISION = "v2-kress-composition-space"

COMPONENT_FIELDS = (
    "SiO2",
    "TiO2",
    "Al2O3",
    "Fe2O3",
    "Cr2O3",
    "FeO",
    "MnO",
    "MgO",
    "NiO",
    "CoO",
    "CaO",
    "Na2O",
    "K2O",
    "P2O5",
)

COMMON_INPUT_FIELDS = (
    "temperature_C",
    "composition_kg",
    "fO2_log",
    "pressure_bar",
    "composition_mol",
    "composition_mol_by_account",
    "species_formula_registry",
)

ALPHAMELTS_CONFIG_FIELDS = (
    "mode",
    "subprocess_run_mode",
    "redox_buffer",
    "fO2_offset",
    "Fe3Fet_Liq",
    "model",
    "timeout_s",
    "require_petthermotools",
    "thermoengine_health_timeout_s",
)

FINDER_INPUT_FIELDS = (
    "finder_min_T_C",
    "finder_max_T_C",
    "finder_scan_step_C",
    "finder_tolerance_C",
    "finder_solid_epsilon",
    "finder_liquid_epsilon",
    "finder_monotonicity_tolerance",
    "finder_monotone_smoothing_max",
    "finder_max_bisection_iterations",
)

INPUT_FIELDS = COMMON_INPUT_FIELDS + ALPHAMELTS_CONFIG_FIELDS + FINDER_INPUT_FIELDS
POINT_PROVENANCE_FIELDS = ("kress91_partition_provenance",)

GENERIC_OUTPUT_FIELDS = (
    "temperature_C",
    "requested_temperature_C",
    "pressure_bar",
    "phases_present",
    "phase_masses_kg",
    "phase_species_mol",
    "phase_species_kg",
    "phase_compositions",
    "liquid_fraction",
    "phase_assemblage_available",
    "liquid_composition_wt_pct",
    "liquid_viscosity_Pa_s",
    "liquid_density_kg_m3",
    "system_enthalpy",
    "system_entropy",
    "system_volume",
    "system_heat_capacity_Cp",
    "system_dVdP",
    "system_dVdT",
    "system_fO2_delta_QFM",
    "system_solid_density_rhos",
    "system_phi",
    "system_chisqr",
    "phase_thermo",
    "chem_potentials",
    "phase_affinities",
    "solid_composition_wt_pct",
    "bulk_composition_wt_pct",
    "vapor_pressures_Pa",
    "vapor_pressures_source",
    "activity_coefficients",
    "fO2_log",
    "warnings",
    "ledger_transition",
    "status",
    "sulfur_saturation",
    "liquidus_T_C",
    "diagnostics",
)

ALPHAMELTS_DIAGNOSTIC_OUTPUT_FIELDS = (
    "activity_coefficients",
    "applied_fe3fet",
    "backend_diagnostics",
    "backend_status",
    "backend_status_reason",
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
)

FINDER_OUTPUT_FIELDS = (
    "liquidus_T_C",
    "liquidus_T_K",
    "solidus_T_C",
    "liquid_fraction",
    "status",
    "warnings",
    "diagnostics",
    "iterations",
    "samples",
    "sample_temperature_C",
    "sample_frac_M",
    "curve_source",
    "curve_solidus_T_C",
    "curve_liquidus_T_C",
    "curve_path_temperature_C",
    "curve_path_liquid_fraction",
)

assert len(INPUT_FIELDS) == 25
assert (
    len(GENERIC_OUTPUT_FIELDS)
    + len(ALPHAMELTS_DIAGNOSTIC_OUTPUT_FIELDS)
    + len(FINDER_OUTPUT_FIELDS)
) == 74


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite float cannot be cached: {value!r}")
        return 0.0 if value == 0.0 else value
    if isinstance(value, Enum):
        return to_jsonable(value.value)
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value):
        return {
            field.name: to_jsonable(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {
            str(key): to_jsonable(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    raise TypeError(f"unsupported cache value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        to_jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_input_vector(inputs: Mapping[str, Any]) -> str:
    allowed_fields = INPUT_FIELDS + POINT_PROVENANCE_FIELDS
    missing = [name for name in allowed_fields if name not in inputs]
    extra = sorted(set(inputs) - set(allowed_fields))
    if missing or extra:
        raise ValueError(f"input vector mismatch: missing={missing}, extra={extra}")
    return canonical_json({name: inputs[name] for name in INPUT_FIELDS})


def expedited_key(inputs: Mapping[str, Any]) -> str:
    vector = canonical_input_vector(inputs)
    return hashlib.sha256(vector.encode("utf-8")).hexdigest()


def _float(value: Any) -> float | None:
    if value is None:
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"non-finite float cannot be cached: {value!r}")
    return result


def _repr(value: Any) -> str | None:
    number = _float(value)
    return None if number is None else repr(number)


def _json(value: Any) -> str | None:
    return None if value is None else canonical_json(value)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS id_block_registry (
    block_base INTEGER PRIMARY KEY,
    source_label TEXT NOT NULL UNIQUE
);
INSERT OR IGNORE INTO id_block_registry(block_base, source_label)
    VALUES (0, 'laptop-dev-smoke');
INSERT OR IGNORE INTO id_block_registry(block_base, source_label)
    VALUES (1000000000, 'studio1');
INSERT OR IGNORE INTO id_block_registry(block_base, source_label)
    VALUES (2000000000, 'studio2');
INSERT OR IGNORE INTO id_block_registry(block_base, source_label)
    VALUES (3000000000, 'studio3');

CREATE TABLE IF NOT EXISTS batches (
    batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK(kind IN ('fixed', 'sobol')),
    seed INTEGER NOT NULL,
    params_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    generator_host TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS grid_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    expedited_key TEXT NOT NULL UNIQUE,
    canonical_vector TEXT NOT NULL UNIQUE,
    batch_id INTEGER NOT NULL REFERENCES batches(batch_id),
    shuffle_rank INTEGER NOT NULL,
    shard INTEGER NOT NULL CHECK(shard IN (0, 1, 2)),
    artifact_kind TEXT NOT NULL DEFAULT 'equilibrium',
    temperature_C REAL NOT NULL,
    temperature_C_repr TEXT NOT NULL,
    composition_kg_json TEXT,
    -- Provenance only: excluded from canonical_vector and expedited_key.
    intended_fO2_log REAL,
    intended_fO2_log_repr TEXT,
    kress91_partition_provenance_json TEXT,
    fO2_log REAL NOT NULL,
    fO2_log_repr TEXT NOT NULL,
    pressure_bar REAL NOT NULL,
    pressure_bar_repr TEXT NOT NULL,
    composition_mol_json TEXT NOT NULL,
    composition_mol_by_account_json TEXT NOT NULL,
    composition_total_mol REAL NOT NULL,
    composition_total_mol_repr TEXT NOT NULL,
    component_SiO2_mol REAL NOT NULL,
    component_SiO2_mol_repr TEXT NOT NULL,
    component_TiO2_mol REAL NOT NULL,
    component_TiO2_mol_repr TEXT NOT NULL,
    component_Al2O3_mol REAL NOT NULL,
    component_Al2O3_mol_repr TEXT NOT NULL,
    component_Fe2O3_mol REAL NOT NULL,
    component_Fe2O3_mol_repr TEXT NOT NULL,
    component_Cr2O3_mol REAL NOT NULL,
    component_Cr2O3_mol_repr TEXT NOT NULL,
    component_FeO_mol REAL NOT NULL,
    component_FeO_mol_repr TEXT NOT NULL,
    component_MnO_mol REAL NOT NULL,
    component_MnO_mol_repr TEXT NOT NULL,
    component_MgO_mol REAL NOT NULL,
    component_MgO_mol_repr TEXT NOT NULL,
    component_NiO_mol REAL NOT NULL,
    component_NiO_mol_repr TEXT NOT NULL,
    component_CoO_mol REAL NOT NULL,
    component_CoO_mol_repr TEXT NOT NULL,
    component_CaO_mol REAL NOT NULL,
    component_CaO_mol_repr TEXT NOT NULL,
    component_Na2O_mol REAL NOT NULL,
    component_Na2O_mol_repr TEXT NOT NULL,
    component_K2O_mol REAL NOT NULL,
    component_K2O_mol_repr TEXT NOT NULL,
    component_P2O5_mol REAL NOT NULL,
    component_P2O5_mol_repr TEXT NOT NULL,
    species_formula_registry_json TEXT,
    species_formula_registry_digest TEXT,
    mode TEXT,
    subprocess_run_mode TEXT,
    redox_buffer TEXT,
    fO2_offset REAL,
    fO2_offset_repr TEXT,
    fe3fet_ratio REAL,
    fe3fet_ratio_repr TEXT,
    model TEXT,
    timeout_s REAL NOT NULL,
    timeout_s_repr TEXT NOT NULL,
    require_petthermotools INTEGER NOT NULL,
    thermoengine_health_timeout_s REAL NOT NULL,
    thermoengine_health_timeout_s_repr TEXT NOT NULL,
    finder_min_T_C REAL,
    finder_max_T_C REAL,
    finder_scan_step_C REAL,
    finder_tolerance_C REAL,
    finder_solid_epsilon REAL,
    finder_liquid_epsilon REAL,
    finder_monotonicity_tolerance REAL,
    finder_monotone_smoothing_max REAL,
    finder_max_bisection_iterations INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE(batch_id, shuffle_rank)
);

CREATE TABLE IF NOT EXISTS grid_key_claims (
    grid_key_id INTEGER NOT NULL REFERENCES grid_keys(id),
    engine_epoch INTEGER NOT NULL,
    claim_owner TEXT NOT NULL,
    claimed_at_epoch REAL NOT NULL,
    expires_at_epoch REAL NOT NULL,
    PRIMARY KEY(grid_key_id, engine_epoch)
);

CREATE TABLE IF NOT EXISTS alphamelts_outputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grid_key_id INTEGER NOT NULL REFERENCES grid_keys(id),
    expedited_key TEXT NOT NULL,
    engine_epoch INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    status_kind TEXT NOT NULL,
    refusal_reason TEXT,
    raw_payload TEXT NOT NULL,
    raw_payload_format TEXT NOT NULL,
    timing_s REAL NOT NULL,
    timing_s_repr TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    engine_mode TEXT NOT NULL,
    engine_model TEXT NOT NULL,
    run_mode TEXT,
    applied_timeout_s REAL,
    applied_timeout_s_repr TEXT,
    native_input_json TEXT,
    created_at TEXT NOT NULL,
    host TEXT NOT NULL,
    source_host TEXT,
    source_row_id INTEGER,

    generic_temperature_C REAL,
    generic_temperature_C_repr TEXT,
    generic_requested_temperature_C REAL,
    generic_requested_temperature_C_repr TEXT,
    generic_pressure_bar REAL,
    generic_pressure_bar_repr TEXT,
    generic_phases_present_json TEXT,
    generic_phase_masses_kg_json TEXT,
    generic_phase_species_mol_json TEXT,
    generic_phase_species_kg_json TEXT,
    generic_phase_compositions_json TEXT,
    generic_liquid_fraction REAL,
    generic_liquid_fraction_repr TEXT,
    generic_phase_assemblage_available INTEGER,
    generic_liquid_composition_wt_pct_json TEXT,
    generic_liquid_viscosity_Pa_s REAL,
    generic_liquid_viscosity_Pa_s_repr TEXT,
    generic_liquid_density_kg_m3 REAL,
    generic_liquid_density_kg_m3_repr TEXT,
    generic_system_enthalpy REAL,
    generic_system_enthalpy_repr TEXT,
    generic_system_entropy REAL,
    generic_system_entropy_repr TEXT,
    generic_system_volume REAL,
    generic_system_volume_repr TEXT,
    generic_system_heat_capacity_Cp REAL,
    generic_system_heat_capacity_Cp_repr TEXT,
    generic_system_dVdP REAL,
    generic_system_dVdP_repr TEXT,
    generic_system_dVdT REAL,
    generic_system_dVdT_repr TEXT,
    generic_system_fO2_delta_QFM REAL,
    generic_system_fO2_delta_QFM_repr TEXT,
    generic_system_solid_density_rhos REAL,
    generic_system_solid_density_rhos_repr TEXT,
    generic_system_phi REAL,
    generic_system_phi_repr TEXT,
    generic_system_chisqr REAL,
    generic_system_chisqr_repr TEXT,
    generic_phase_thermo_json TEXT,
    generic_chem_potentials_json TEXT,
    generic_phase_affinities_json TEXT,
    generic_solid_composition_wt_pct_json TEXT,
    generic_bulk_composition_wt_pct_json TEXT,
    generic_vapor_pressures_Pa_json TEXT,
    generic_vapor_pressures_source_json TEXT,
    generic_activity_coefficients_json TEXT,
    generic_fO2_log REAL,
    generic_fO2_log_repr TEXT,
    generic_warnings_json TEXT,
    generic_ledger_transition_json TEXT,
    generic_status TEXT,
    generic_sulfur_saturation_json TEXT,
    generic_liquidus_T_C REAL,
    generic_liquidus_T_C_repr TEXT,
    generic_diagnostics_json TEXT,

    alpha_activity_coefficients_json TEXT,
    alpha_applied_fe3fet REAL,
    alpha_applied_fe3fet_repr TEXT,
    alpha_backend_diagnostics_json TEXT,
    alpha_backend_status TEXT,
    alpha_backend_status_reason TEXT,
    alpha_backend_warnings_json TEXT,
    alpha_engine_version TEXT,
    alpha_fO2_log REAL,
    alpha_fO2_log_repr TEXT,
    alpha_fe_redox_policy TEXT,
    alpha_intrinsic_fO2_log REAL,
    alpha_intrinsic_fO2_log_repr TEXT,
    alpha_liquid_composition_wt_pct_json TEXT,
    alpha_liquid_fraction REAL,
    alpha_liquid_fraction_repr TEXT,
    alpha_liquid_fraction_path_json TEXT,
    alpha_liquidus_T_C REAL,
    alpha_liquidus_T_C_repr TEXT,
    alpha_liquidus_T_K REAL,
    alpha_liquidus_T_K_repr TEXT,
    alpha_mode TEXT,
    alpha_phase_masses_kg_json TEXT,
    alpha_phase_modes_wt_pct_json TEXT,
    alpha_phases_present_json TEXT,
    alpha_solidus_T_C REAL,
    alpha_solidus_T_C_repr TEXT,

    finder_liquidus_T_C REAL,
    finder_liquidus_T_K REAL,
    finder_solidus_T_C REAL,
    finder_liquid_fraction REAL,
    finder_status TEXT,
    finder_warnings_json TEXT,
    finder_diagnostics_json TEXT,
    finder_iterations INTEGER,
    finder_samples_json TEXT,
    finder_sample_temperature_C_json TEXT,
    finder_sample_frac_M_json TEXT,
    curve_source TEXT,
    curve_solidus_T_C REAL,
    curve_liquidus_T_C REAL,
    curve_path_temperature_C_json TEXT,
    curve_path_liquid_fraction_json TEXT,

    UNIQUE(expedited_key, engine_epoch)
);

CREATE INDEX IF NOT EXISTS idx_alphamelts_outputs_input
    ON alphamelts_outputs(grid_key_id, engine_epoch);
CREATE INDEX IF NOT EXISTS idx_alphamelts_outputs_status
    ON alphamelts_outputs(status, engine_epoch);
CREATE INDEX IF NOT EXISTS idx_grid_keys_drain
    ON grid_keys(batch_id, shard, shuffle_rank);
CREATE INDEX IF NOT EXISTS idx_grid_key_claims_expiry
    ON grid_key_claims(expires_at_epoch);
"""


class GridCacheWriter:
    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        engine_epoch: int = 1,
        existing_only: bool = False,
    ):
        self.path = Path(path)
        self.engine_epoch = int(engine_epoch)
        self.claim_owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"
        if self.engine_epoch < 1:
            raise ValueError("engine_epoch must be >= 1")
        if existing_only:
            if not self.path.is_file():
                raise FileNotFoundError(f"database does not exist: {self.path}")
            validation_database = (
                "file:"
                + urllib.parse.quote(str(self.path.resolve()), safe="/")
                + "?mode=ro"
            )
            validation_connection: sqlite3.Connection | None = None
            try:
                validation_connection = sqlite3.connect(
                    validation_database, timeout=30.0, uri=True
                )
                validation_connection.row_factory = sqlite3.Row
                self._validate_connection(validation_connection)
            except sqlite3.DatabaseError as exc:
                raise ValueError(
                    f"cannot validate existing grid cache {self.path}: {exc}"
                ) from exc
            finally:
                if validation_connection is not None:
                    validation_connection.close()
            database = (
                "file:"
                + urllib.parse.quote(str(self.path.resolve()), safe="/")
                + "?mode=rw"
            )
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            database = str(self.path)
        try:
            self.connection = sqlite3.connect(
                database, timeout=30.0, uri=existing_only
            )
        except sqlite3.DatabaseError as exc:
            raise ValueError(f"cannot open grid cache {self.path}: {exc}") from exc
        self.connection.row_factory = sqlite3.Row
        if existing_only:
            try:
                self._validate_existing_database()
            except sqlite3.DatabaseError as exc:
                self.connection.close()
                raise ValueError(
                    f"cannot validate existing grid cache {self.path}: {exc}"
                ) from exc
            except Exception:
                self.connection.close()
                raise
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=30000")
        if existing_only:
            # Retry/drain opens an existing database read-write. Add nullable
            # forward columns only; historical rows remain untouched.
            self._ensure_v2_provenance_columns()
            self._ensure_runmode_output_columns()
            self._ensure_claim_table()
            self._set_metadata("schema_output_field_count", "74")
            self.connection.commit()
        else:
            self.connection.executescript(SCHEMA_SQL)
            self._ensure_v2_provenance_columns()
            self._ensure_runmode_output_columns()
            self._ensure_claim_table()
            self._set_metadata("schema_variant", SCHEMA_VARIANT)
            self._set_metadata(
                "expedited_key_note",
                "variant-local bookkeeping only; recompute reviewed canonical_state_bytes "
                "from typed full-precision inputs; never transplant this hash",
            )
            self._set_metadata("schema_output_field_count", "74")
            self._set_metadata("schema_input_field_count", "25")
            self._set_metadata("grid_realization_revision", GRID_REALIZATION_REVISION)
            self._set_metadata("database_id", str(uuid.uuid4()), overwrite=False)
            self._set_metadata("created_at", utc_now(), overwrite=False)
            self.connection.commit()

    def _validate_existing_database(self) -> None:
        self._validate_connection(self.connection)

    @staticmethod
    def _validate_connection(connection: sqlite3.Connection) -> None:
        required_tables = {
            "metadata",
            "id_block_registry",
            "batches",
            "grid_keys",
            "alphamelts_outputs",
        }
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        missing = sorted(required_tables - tables)
        if missing:
            raise ValueError(
                "existing database is not a grid cache; missing tables: "
                + ", ".join(missing)
            )
        row = connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_variant'"
        ).fetchone()
        if row is None or str(row[0]) != SCHEMA_VARIANT:
            value = None if row is None else str(row[0])
            raise ValueError(
                f"schema variant mismatch: database={value!r}, "
                f"writer={SCHEMA_VARIANT!r}"
            )
        provenance_columns = set(table_columns(connection, "grid_keys"))
        missing_columns = {
            "intended_fO2_log",
            "intended_fO2_log_repr",
        } - provenance_columns
        if missing_columns:
            raise ValueError(
                "existing database lacks v2 provenance columns: "
                + ", ".join(sorted(missing_columns))
            )

    @classmethod
    def has_batch_definitions(cls, path: str | os.PathLike[str]) -> bool:
        database_path = Path(path)
        if not database_path.is_file() or database_path.stat().st_size == 0:
            return False
        database = (
            "file:"
            + urllib.parse.quote(str(database_path.resolve()), safe="/")
            + "?mode=ro"
        )
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(database, uri=True)
            table = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'batches'"
            ).fetchone()
            if table is None:
                return False
            return (
                connection.execute(
                    "SELECT 1 FROM batches "
                    "WHERE seed IS NOT NULL AND params_json IS NOT NULL LIMIT 1"
                ).fetchone()
                is not None
            )
        except sqlite3.DatabaseError as exc:
            raise ValueError(
                f"cannot inspect existing database {database_path}: {exc}"
            ) from exc
        finally:
            if connection is not None:
                connection.close()

    def _ensure_v2_provenance_columns(self) -> None:
        columns = set(table_columns(self.connection, "grid_keys"))
        additions = {
            "intended_fO2_log": "REAL",
            "intended_fO2_log_repr": "TEXT",
            "kress91_partition_provenance_json": "TEXT",
        }
        for name, column_type in additions.items():
            if name not in columns:
                self.connection.execute(
                    f'ALTER TABLE grid_keys ADD COLUMN "{name}" {column_type}'
                )

    def _ensure_runmode_output_columns(self) -> None:
        additions = {
            "grid_keys": {
                "subprocess_run_mode": "TEXT",
            },
            "alphamelts_outputs": {
                "generic_requested_temperature_C": "REAL",
                "generic_requested_temperature_C_repr": "TEXT",
                "generic_liquid_density_kg_m3": "REAL",
                "generic_liquid_density_kg_m3_repr": "TEXT",
                "generic_system_enthalpy": "REAL",
                "generic_system_enthalpy_repr": "TEXT",
                "generic_system_entropy": "REAL",
                "generic_system_entropy_repr": "TEXT",
                "generic_system_volume": "REAL",
                "generic_system_volume_repr": "TEXT",
                "generic_system_heat_capacity_Cp": "REAL",
                "generic_system_heat_capacity_Cp_repr": "TEXT",
                "generic_system_dVdP": "REAL",
                "generic_system_dVdP_repr": "TEXT",
                "generic_system_dVdT": "REAL",
                "generic_system_dVdT_repr": "TEXT",
                "generic_system_fO2_delta_QFM": "REAL",
                "generic_system_fO2_delta_QFM_repr": "TEXT",
                "generic_system_solid_density_rhos": "REAL",
                "generic_system_solid_density_rhos_repr": "TEXT",
                "generic_system_phi": "REAL",
                "generic_system_phi_repr": "TEXT",
                "generic_system_chisqr": "REAL",
                "generic_system_chisqr_repr": "TEXT",
                "generic_phase_thermo_json": "TEXT",
                "generic_chem_potentials_json": "TEXT",
                "generic_phase_affinities_json": "TEXT",
                "generic_solid_composition_wt_pct_json": "TEXT",
                "generic_bulk_composition_wt_pct_json": "TEXT",
                "run_mode": "TEXT",
                "applied_timeout_s": "REAL",
                "applied_timeout_s_repr": "TEXT",
            },
        }
        for table, columns_to_add in additions.items():
            columns = set(table_columns(self.connection, table))
            for name, column_type in columns_to_add.items():
                if name not in columns:
                    self.connection.execute(
                        f'ALTER TABLE "{table}" ADD COLUMN '
                        f'"{name}" {column_type}'
                    )

    def _ensure_claim_table(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS grid_key_claims (
                grid_key_id INTEGER NOT NULL REFERENCES grid_keys(id),
                engine_epoch INTEGER NOT NULL,
                claim_owner TEXT NOT NULL,
                claimed_at_epoch REAL NOT NULL,
                expires_at_epoch REAL NOT NULL,
                PRIMARY KEY(grid_key_id, engine_epoch)
            );
            CREATE INDEX IF NOT EXISTS idx_grid_key_claims_expiry
                ON grid_key_claims(expires_at_epoch);
            """
        )

    def _begin_write_section(self, savepoint_name: str) -> str | None:
        if self.connection.in_transaction:
            self.connection.execute(f"SAVEPOINT {savepoint_name}")
            return savepoint_name
        self.connection.execute("BEGIN IMMEDIATE")
        return None

    def _finish_write_section(self, savepoint_name: str | None) -> None:
        if savepoint_name is None:
            self.connection.commit()
        else:
            self.connection.execute(f"RELEASE SAVEPOINT {savepoint_name}")

    def _rollback_write_section(self, savepoint_name: str | None) -> None:
        if savepoint_name is None:
            self.connection.rollback()
        else:
            self.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
            self.connection.execute(f"RELEASE SAVEPOINT {savepoint_name}")

    def _set_metadata(self, key: str, value: str, *, overwrite: bool = True) -> None:
        existing = self.connection.execute(
            "SELECT value FROM metadata WHERE key = ?", (key,)
        ).fetchone()
        if existing is not None and key == "schema_variant" and existing[0] != value:
            raise ValueError(
                f"schema variant mismatch: database={existing[0]!r}, writer={value!r}"
            )
        if existing is None:
            self.connection.execute(
                "INSERT INTO metadata(key, value) VALUES (?, ?)", (key, value)
            )
        elif overwrite:
            self.connection.execute(
                "UPDATE metadata SET value = ? WHERE key = ?", (value, key)
            )

    def set_run_metadata(self, values: Mapping[str, Any]) -> None:
        for key, value in values.items():
            if str(key) == "schema_variant":
                self._set_metadata("schema_variant", str(value))
            else:
                self._set_metadata(str(key), canonical_json(value))
        self.connection.commit()

    def ensure_batch(
        self,
        *,
        label: str,
        kind: str,
        seed: int,
        params: Mapping[str, Any],
        generator_host: str | None = None,
    ) -> int:
        if kind not in {"fixed", "sobol"}:
            raise ValueError(f"unsupported batch kind: {kind}")
        params_json = canonical_json(params)
        self.connection.execute(
            "INSERT OR IGNORE INTO batches(label, kind, seed, params_json, "
            "created_at, generator_host) VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(label),
                kind,
                int(seed),
                params_json,
                utc_now(),
                generator_host or socket.gethostname(),
            ),
        )
        row = self.connection.execute(
            "SELECT batch_id, kind, seed, params_json FROM batches WHERE label = ?",
            (str(label),),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"failed to materialize batch {label!r}")
        if (
            row["kind"] != kind
            or int(row["seed"]) != int(seed)
            or row["params_json"] != params_json
        ):
            raise ValueError(f"batch definition drift for {label!r}")
        return int(row["batch_id"])

    def seed_id_block(self, shard: int) -> None:
        if shard not in {0, 1, 2}:
            raise ValueError("shard must be 0, 1, or 2")
        for table in ("grid_keys", "alphamelts_outputs"):
            count = int(
                self.connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            )
            if count:
                continue
            base = (int(shard) + 1) * 1_000_000_000
            self.connection.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
            self.connection.execute(
                "INSERT INTO sqlite_sequence(name, seq) VALUES (?, ?)",
                (table, base - 1),
            )

    def materialize_key(
        self,
        inputs: Mapping[str, Any],
        *,
        batch_id: int,
        shuffle_rank: int,
        shard: int,
        intended_fO2_log: float | None = None,
    ) -> bool:
        values = self._grid_key_values(
            inputs,
            batch_id=batch_id,
            shuffle_rank=shuffle_rank,
            shard=shard,
            intended_fO2_log=intended_fO2_log,
        )
        cursor = self._insert_or_ignore("grid_keys", values)
        if cursor.rowcount == 0:
            vector = values["canonical_vector"]
            row = self.connection.execute(
                "SELECT canonical_vector, batch_id, shuffle_rank, shard, "
                "kress91_partition_provenance_json "
                "FROM grid_keys WHERE expedited_key = ?",
                (values["expedited_key"],),
            ).fetchone()
            if row is None or row["canonical_vector"] != vector:
                raise RuntimeError(
                    f"expedited-key collision for {values['expedited_key']}"
                )
            existing_provenance = row["kress91_partition_provenance_json"]
            point_provenance = values["kress91_partition_provenance_json"]
            if existing_provenance is None:
                self.connection.execute(
                    "UPDATE grid_keys "
                    "SET kress91_partition_provenance_json = ? "
                    "WHERE expedited_key = ?",
                    (point_provenance, values["expedited_key"]),
                )
            elif existing_provenance != point_provenance:
                raise ValueError(
                    "Kress91 point provenance drift for expedited key "
                    f"{values['expedited_key']}"
                )
        return cursor.rowcount == 1

    def pending_rows(
        self,
        *,
        batch_id: int,
        shard: int | None = None,
        rank_limit: int | None = None,
        after_shuffle_rank: int = -1,
        fetch_limit: int | None = None,
        grid_key_ids: Sequence[int] | None = None,
    ) -> list[dict[str, Any]]:
        now = time.time()
        if grid_key_ids is not None and not grid_key_ids:
            return []
        if fetch_limit is not None and int(fetch_limit) <= 0:
            return []
        write_section = self._begin_write_section("grid_key_claim_pending")
        try:
            self.connection.execute(
                "DELETE FROM grid_key_claims WHERE expires_at_epoch <= ?",
                (now,),
            )
            clauses = [
                "g.batch_id = ?",
                "o.id IS NULL",
                "c.grid_key_id IS NULL",
                "g.shuffle_rank > ?",
            ]
            parameters: list[Any] = [
                self.engine_epoch,
                self.engine_epoch,
                int(batch_id),
                int(after_shuffle_rank),
            ]
            if shard is not None:
                clauses.append("g.shard = ?")
                parameters.append(int(shard))
            if rank_limit is not None:
                clauses.append("g.shuffle_rank < ?")
                parameters.append(int(rank_limit))
            if grid_key_ids is not None:
                placeholders = ",".join("?" for _ in grid_key_ids)
                clauses.append(f"g.id IN ({placeholders})")
                parameters.extend(int(value) for value in grid_key_ids)
            query = (
                "SELECT g.id, g.expedited_key, g.canonical_vector, "
                "g.kress91_partition_provenance_json, g.intended_fO2_log, "
                "g.shuffle_rank, g.shard, g.timeout_s FROM grid_keys g "
                "LEFT JOIN alphamelts_outputs o ON o.expedited_key = g.expedited_key "
                "AND o.engine_epoch = ? "
                "LEFT JOIN grid_key_claims c ON c.grid_key_id = g.id "
                "AND c.engine_epoch = ? WHERE "
                + " AND ".join(clauses)
                + " ORDER BY g.shuffle_rank"
            )
            if fetch_limit is not None:
                # The drain caller asks in pages, but claims must follow actual
                # worker capacity rather than reserving an entire page at once.
                query += " LIMIT 1"
            rows = self.connection.execute(query, tuple(parameters)).fetchall()
            if not rows:
                self._finish_write_section(write_section)
                return []
            for row in rows:
                lease_s = max(3600.0, float(row["timeout_s"]) * 2.0 + 300.0)
                self.connection.execute(
                    "INSERT INTO grid_key_claims("
                    "grid_key_id, engine_epoch, claim_owner, "
                    "claimed_at_epoch, expires_at_epoch"
                    ") VALUES (?, ?, ?, ?, ?)",
                    (
                        int(row["id"]),
                        self.engine_epoch,
                        self.claim_owner,
                        now,
                        now + lease_s,
                    ),
                )
            self._finish_write_section(write_section)
        except BaseException:
            self._rollback_write_section(write_section)
            raise
        return [
            {
                "grid_key_id": int(row["id"]),
                "expedited_key": str(row["expedited_key"]),
                "inputs": {
                    **json.loads(row["canonical_vector"]),
                    # Provenance is intentionally outside canonical key identity,
                    # but the drain must compare it with the persisted engine input.
                    "intended_fO2_log": row["intended_fO2_log"],
                    "kress91_partition_provenance": (
                        json.loads(row["kress91_partition_provenance_json"])
                        if row["kress91_partition_provenance_json"] is not None
                        else None
                    ),
                },
                "shuffle_rank": int(row["shuffle_rank"]),
                "shard": int(row["shard"]),
            }
            for row in rows
        ]

    def queue_counts(
        self,
        *,
        batch_id: int,
        shard: int | None = None,
        rank_limit: int | None = None,
        grid_key_ids: Sequence[int] | None = None,
    ) -> dict[str, int]:
        extra = "" if shard is None else " AND g.shard = ?"
        parameters: list[Any] = [self.engine_epoch, int(batch_id)]
        if shard is not None:
            parameters.append(int(shard))
        if rank_limit is not None:
            extra += " AND g.shuffle_rank < ?"
            parameters.append(int(rank_limit))
        if grid_key_ids is not None:
            if not grid_key_ids:
                return {"total": 0, "done": 0, "remaining": 0}
            placeholders = ",".join("?" for _ in grid_key_ids)
            extra += f" AND g.id IN ({placeholders})"
            parameters.extend(int(value) for value in grid_key_ids)
        row = self.connection.execute(
            "SELECT COUNT(*) AS total, COUNT(o.id) AS done "
            "FROM grid_keys g LEFT JOIN alphamelts_outputs o "
            "ON o.expedited_key = g.expedited_key AND o.engine_epoch = ? "
            "WHERE g.batch_id = ?" + extra,
            tuple(parameters),
        ).fetchone()
        total = int(row["total"])
        done = int(row["done"])
        return {"total": total, "done": done, "remaining": total - done}

    def select_grid_key_ids(
        self,
        *,
        selectors: Sequence[str] = (),
        refusal_reason: str | None = None,
        source_epoch: int = 1,
        limit: int | None = None,
    ) -> list[int]:
        clauses: list[str] = []
        parameters: list[Any] = []
        joins = ""
        if selectors:
            selector_clauses = []
            for selector in selectors:
                text = str(selector).strip()
                if not text:
                    continue
                if text.startswith("id:"):
                    row_id = text.removeprefix("id:")
                    if not row_id.isdigit():
                        raise ValueError(f"invalid grid-key id selector: {text!r}")
                    selector_clauses.append("g.id = ?")
                    parameters.append(int(row_id))
                elif text.startswith("key:"):
                    key_prefix = text.removeprefix("key:")
                    if not key_prefix:
                        raise ValueError("expedited-key prefix cannot be empty")
                    selector_clauses.append("g.expedited_key LIKE ?")
                    parameters.append(f"{key_prefix}%")
                elif text.isdigit() and self.connection.execute(
                    "SELECT 1 FROM grid_keys WHERE id = ?", (int(text),)
                ).fetchone() is not None:
                    selector_clauses.append("g.id = ?")
                    parameters.append(int(text))
                else:
                    selector_clauses.append("g.expedited_key LIKE ?")
                    parameters.append(f"{text}%")
            if selector_clauses:
                clauses.append("(" + " OR ".join(selector_clauses) + ")")
        if refusal_reason is not None:
            joins = (
                " JOIN alphamelts_outputs source_output "
                "ON source_output.expedited_key = g.expedited_key"
            )
            clauses.extend(
                [
                    "source_output.engine_epoch = ?",
                    "source_output.refusal_reason = ?",
                ]
            )
            parameters.extend([int(source_epoch), str(refusal_reason)])
        if not clauses:
            raise ValueError("at least one key selector or refusal reason is required")
        query = (
            "SELECT DISTINCT g.id FROM grid_keys g"
            + joins
            + " WHERE "
            + " AND ".join(clauses)
            + " ORDER BY g.shuffle_rank, g.id"
        )
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(int(limit))
        return [int(row[0]) for row in self.connection.execute(query, parameters)]

    def selected_result_histogram(
        self, grid_key_ids: Sequence[int]
    ) -> dict[str, dict[str, int]]:
        if not grid_key_ids:
            return {"status": {}, "refusal_reason": {}}
        placeholders = ",".join("?" for _ in grid_key_ids)
        parameters = (self.engine_epoch, *(int(value) for value in grid_key_ids))
        status_rows = self.connection.execute(
            "SELECT status, COUNT(*) FROM alphamelts_outputs "
            f"WHERE engine_epoch = ? AND grid_key_id IN ({placeholders}) "
            "GROUP BY status ORDER BY status",
            parameters,
        )
        reason_rows = self.connection.execute(
            "SELECT COALESCE(refusal_reason, '<none>'), COUNT(*) "
            "FROM alphamelts_outputs "
            f"WHERE engine_epoch = ? AND grid_key_id IN ({placeholders}) "
            "GROUP BY refusal_reason ORDER BY COUNT(*) DESC",
            parameters,
        )
        return {
            "status": {str(name): int(count) for name, count in status_rows},
            "refusal_reason": {
                str(name): int(count) for name, count in reason_rows
            },
        }

    def batches(self) -> list[dict[str, Any]]:
        return [
            {
                "batch_id": int(row["batch_id"]),
                "label": str(row["label"]),
                "kind": str(row["kind"]),
                "seed": int(row["seed"]),
                "params": json.loads(row["params_json"]),
                "created_at": str(row["created_at"]),
                "generator_host": str(row["generator_host"]),
            }
            for row in self.connection.execute(
                "SELECT batch_id, label, kind, seed, params_json, "
                "created_at, generator_host "
                "FROM batches ORDER BY batch_id"
            )
        ]

    def drain_manifest(
        self,
        *,
        shard: int | None,
        rank_limit: int | None = None,
    ) -> dict[str, Any]:
        if shard is not None and shard not in {0, 1, 2}:
            raise ValueError("drain shard must be 0, 1, or 2")
        registry = {
            int(row["block_base"]): str(row["source_label"])
            for row in self.connection.execute(
                "SELECT block_base, source_label FROM id_block_registry"
            )
        }
        expected_registry = {
            0: "laptop-dev-smoke",
            1_000_000_000: "studio1",
            2_000_000_000: "studio2",
            3_000_000_000: "studio3",
        }
        registry_drift = {
            base: {"expected": label, "actual": registry.get(base)}
            for base, label in expected_registry.items()
            if registry.get(base) != label
        }
        if registry_drift:
            raise ValueError(
                "id-block registry mismatch: " + canonical_json(registry_drift)
            )

        bad_shard_rows = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM grid_keys "
                "WHERE shuffle_rank < 0 OR shard != (shuffle_rank % 3)"
            ).fetchone()[0]
        )
        if bad_shard_rows:
            raise ValueError(
                f"id-block/shard sanity failed: {bad_shard_rows} grid keys "
                "disagree with shuffle_rank modulo 3"
            )

        total_keys = int(
            self.connection.execute("SELECT COUNT(*) FROM grid_keys").fetchone()[0]
        )
        if total_keys == 0:
            raise ValueError(
                "database has no materialized queue; run --prepare-only on the prepare host"
            )

        if shard is not None:
            lower = (shard + 1) * 1_000_000_000
            upper = lower + 1_000_000_000
            selected = self.connection.execute(
                "SELECT COUNT(*), MIN(id), MAX(id) FROM grid_keys WHERE shard = ?",
                (shard,),
            ).fetchone()
            selected_count = int(selected[0])
            if selected_count == 0:
                raise ValueError(
                    f"database has no materialized queue for shard {shard}"
                )
            if int(selected[1]) < lower or int(selected[2]) >= upper:
                raise ValueError(
                    "id-block/shard sanity failed: "
                    f"shard {shard} grid-key ids must be in [{lower}, {upper})"
                )
            for table in ("grid_keys", "alphamelts_outputs"):
                row = self.connection.execute(
                    "SELECT seq FROM sqlite_sequence WHERE name = ?", (table,)
                ).fetchone()
                sequence = None if row is None else int(row[0])
                if sequence is None or sequence < lower - 1 or sequence >= upper:
                    raise ValueError(
                        "id-block/shard sanity failed: "
                        f"{table} sequence {sequence!r} is outside shard {shard} block"
                    )
        else:
            invalid_blocks = int(
                self.connection.execute(
                    "SELECT COUNT(*) FROM grid_keys WHERE id >= 1000000000 AND "
                    "(id >= 4000000000 OR shard != CAST(id / 1000000000 AS INTEGER) - 1)"
                ).fetchone()[0]
            )
            if invalid_blocks:
                raise ValueError(
                    f"id-block/shard sanity failed for {invalid_blocks} grid keys"
                )

        batches = {batch["batch_id"]: batch for batch in self.batches()}
        materialized = {
            int(row["batch_id"]): int(row["key_count"])
            for row in self.connection.execute(
                "SELECT batch_id, COUNT(*) AS key_count FROM grid_keys "
                + ("" if shard is None else "WHERE shard = ? ")
                + "GROUP BY batch_id",
                () if shard is None else (shard,),
            )
        }
        result_batches = []
        for batch_id in sorted(materialized):
            key_count = materialized[batch_id]
            batch = batches.get(batch_id)
            if batch is None:
                raise ValueError(
                    f"materialized queue references missing batch {batch_id}"
                )
            params = batch["params"]
            if not isinstance(params, Mapping):
                raise ValueError(f"batch {batch_id} params_json is not an object")
            kress91_partition = params.get("kress91_partition")
            if not isinstance(kress91_partition, Mapping):
                raise ValueError(
                    f"batch {batch_id} has no stored kress91_partition metadata"
                )
            queue = self.queue_counts(
                batch_id=batch_id,
                shard=shard,
                rank_limit=rank_limit,
            )
            result_batches.append(
                {
                    **batch,
                    "params_source": "batches.params_json",
                    "kress91_partition": dict(kress91_partition),
                    "materialized_keys": key_count,
                    "queue": queue,
                }
            )
        return {
            "shard": "all" if shard is None else shard,
            "materialized_keys": sum(materialized.values()),
            "batches": result_batches,
        }

    def existing_keys(self, keys: Iterable[str]) -> set[str]:
        result: set[str] = set()
        batch: list[str] = []
        for key in keys:
            batch.append(str(key))
            if len(batch) == 500:
                result.update(self._existing_key_batch(batch))
                batch.clear()
        if batch:
            result.update(self._existing_key_batch(batch))
        return result

    def _existing_key_batch(self, keys: Sequence[str]) -> set[str]:
        placeholders = ",".join("?" for _ in keys)
        rows = self.connection.execute(
            f"SELECT expedited_key FROM alphamelts_outputs "
            f"WHERE engine_epoch = ? AND expedited_key IN ({placeholders})",
            (self.engine_epoch, *keys),
        )
        return {str(row[0]) for row in rows}

    def _grid_key_values(
        self,
        inputs: Mapping[str, Any],
        *,
        batch_id: int,
        shuffle_rank: int,
        shard: int,
        intended_fO2_log: float | None,
    ) -> dict[str, Any]:
        vector = canonical_input_vector(inputs)
        key = hashlib.sha256(vector.encode("utf-8")).hexdigest()
        composition_mol = inputs["composition_mol"] or {}
        total_mol = sum(float(value) for value in composition_mol.values())
        registry_json = _json(inputs["species_formula_registry"])
        registry_digest = (
            hashlib.sha256(registry_json.encode("utf-8")).hexdigest()
            if registry_json is not None
            else None
        )
        missing_components = [
            component for component in COMPONENT_FIELDS if component not in composition_mol
        ]
        if missing_components:
            raise ValueError(
                f"full 14-component input vector required; missing={missing_components}"
            )
        values = {
            "expedited_key": key,
            "canonical_vector": vector,
            "batch_id": int(batch_id),
            "shuffle_rank": int(shuffle_rank),
            "shard": int(shard),
            "temperature_C": _float(inputs["temperature_C"]),
            "temperature_C_repr": _repr(inputs["temperature_C"]),
            "composition_kg_json": _json(inputs["composition_kg"]),
            "intended_fO2_log": _float(intended_fO2_log),
            "intended_fO2_log_repr": _repr(intended_fO2_log),
            "kress91_partition_provenance_json": _json(
                inputs["kress91_partition_provenance"]
            ),
            "fO2_log": _float(inputs["fO2_log"]),
            "fO2_log_repr": _repr(inputs["fO2_log"]),
            "pressure_bar": _float(inputs["pressure_bar"]),
            "pressure_bar_repr": _repr(inputs["pressure_bar"]),
            "composition_mol_json": _json(composition_mol),
            "composition_mol_by_account_json": _json(
                inputs["composition_mol_by_account"]
            ),
            "composition_total_mol": total_mol,
            "composition_total_mol_repr": repr(total_mol),
            "species_formula_registry_json": registry_json,
            "species_formula_registry_digest": registry_digest,
            "mode": inputs["mode"],
            "subprocess_run_mode": inputs["subprocess_run_mode"],
            "redox_buffer": inputs["redox_buffer"],
            "fO2_offset": _float(inputs["fO2_offset"]),
            "fO2_offset_repr": _repr(inputs["fO2_offset"]),
            "fe3fet_ratio": _float(inputs["Fe3Fet_Liq"]),
            "fe3fet_ratio_repr": _repr(inputs["Fe3Fet_Liq"]),
            "model": inputs["model"],
            "timeout_s": _float(inputs["timeout_s"]),
            "timeout_s_repr": _repr(inputs["timeout_s"]),
            "require_petthermotools": int(bool(inputs["require_petthermotools"])),
            "thermoengine_health_timeout_s": _float(
                inputs["thermoengine_health_timeout_s"]
            ),
            "thermoengine_health_timeout_s_repr": _repr(
                inputs["thermoengine_health_timeout_s"]
            ),
            "finder_min_T_C": _float(inputs["finder_min_T_C"]),
            "finder_max_T_C": _float(inputs["finder_max_T_C"]),
            "finder_scan_step_C": _float(inputs["finder_scan_step_C"]),
            "finder_tolerance_C": _float(inputs["finder_tolerance_C"]),
            "finder_solid_epsilon": _float(inputs["finder_solid_epsilon"]),
            "finder_liquid_epsilon": _float(inputs["finder_liquid_epsilon"]),
            "finder_monotonicity_tolerance": _float(
                inputs["finder_monotonicity_tolerance"]
            ),
            "finder_monotone_smoothing_max": _float(
                inputs["finder_monotone_smoothing_max"]
            ),
            "finder_max_bisection_iterations": inputs[
                "finder_max_bisection_iterations"
            ],
            "created_at": utc_now(),
        }
        for component in COMPONENT_FIELDS:
            value = _float(composition_mol[component])
            values[f"component_{component}_mol"] = value
            values[f"component_{component}_mol_repr"] = _repr(value)
        return values

    def write_result(
        self,
        grid_key_id: int,
        output: Mapping[str, Any],
    ) -> bool:
        row = self.connection.execute(
            "SELECT id, expedited_key FROM grid_keys WHERE id = ?",
            (int(grid_key_id),),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"grid key is not materialized: {grid_key_id}")

        output_values = self._output_values(output)
        output_values.update(
            {
                "grid_key_id": int(row["id"]),
                "expedited_key": str(row["expedited_key"]),
                "engine_epoch": self.engine_epoch,
            }
        )
        write_section = self._begin_write_section("grid_key_claim_result")
        try:
            existing = self.connection.execute(
                "SELECT 1 FROM alphamelts_outputs "
                "WHERE expedited_key = ? AND engine_epoch = ?",
                (str(row["expedited_key"]), self.engine_epoch),
            ).fetchone()
            if existing is not None:
                self.connection.execute(
                    "DELETE FROM grid_key_claims "
                    "WHERE grid_key_id = ? AND engine_epoch = ? "
                    "AND claim_owner = ?",
                    (int(grid_key_id), self.engine_epoch, self.claim_owner),
                )
                self._finish_write_section(write_section)
                return False
            claim = self.connection.execute(
                "SELECT claim_owner, expires_at_epoch FROM grid_key_claims "
                "WHERE grid_key_id = ? AND engine_epoch = ?",
                (int(grid_key_id), self.engine_epoch),
            ).fetchone()
            if claim is None or (
                str(claim["claim_owner"]) != self.claim_owner
                or float(claim["expires_at_epoch"]) <= time.time()
            ):
                raise RuntimeError(
                    f"grid key {grid_key_id} is not claimed by this writer"
                )
            cursor = self._insert_or_ignore("alphamelts_outputs", output_values)
            self.connection.execute(
                "DELETE FROM grid_key_claims "
                "WHERE grid_key_id = ? AND engine_epoch = ? AND claim_owner = ?",
                (int(grid_key_id), self.engine_epoch, self.claim_owner),
            )
            self._finish_write_section(write_section)
            return cursor.rowcount == 1
        except BaseException:
            self._rollback_write_section(write_section)
            raise

    def _output_values(self, output: Mapping[str, Any]) -> dict[str, Any]:
        generic = dict(output.get("generic") or {})
        alpha = dict(output.get("alphamelts") or {})
        finder = dict(output.get("finder") or {})
        values = {
            "status": str(output["status"]),
            "status_kind": str(output["status_kind"]),
            "refusal_reason": output.get("refusal_reason"),
            "raw_payload": str(output["raw_payload"]),
            "raw_payload_format": str(output["raw_payload_format"]),
            "timing_s": _float(output["timing_s"]),
            "timing_s_repr": _repr(output["timing_s"]),
            "engine_version": str(output["engine_version"]),
            "engine_mode": str(output["engine_mode"]),
            "engine_model": str(output["engine_model"]),
            "run_mode": output.get("run_mode"),
            "applied_timeout_s": _float(output.get("applied_timeout_s")),
            "applied_timeout_s_repr": _repr(output.get("applied_timeout_s")),
            "native_input_json": _json(output.get("native_input")),
            "created_at": str(output.get("created_at") or utc_now()),
            "host": str(output.get("host") or socket.gethostname()),
            "source_host": output.get("source_host"),
            "source_row_id": output.get("source_row_id"),
            "generic_temperature_C": _float(generic.get("temperature_C")),
            "generic_temperature_C_repr": _repr(generic.get("temperature_C")),
            "generic_requested_temperature_C": _float(
                generic.get("requested_temperature_C")
            ),
            "generic_requested_temperature_C_repr": _repr(
                generic.get("requested_temperature_C")
            ),
            "generic_pressure_bar": _float(generic.get("pressure_bar")),
            "generic_pressure_bar_repr": _repr(generic.get("pressure_bar")),
            "generic_phases_present_json": _json(generic.get("phases_present")),
            "generic_phase_masses_kg_json": _json(generic.get("phase_masses_kg")),
            "generic_phase_species_mol_json": _json(generic.get("phase_species_mol")),
            "generic_phase_species_kg_json": _json(generic.get("phase_species_kg")),
            "generic_phase_compositions_json": _json(generic.get("phase_compositions")),
            "generic_liquid_fraction": _float(generic.get("liquid_fraction")),
            "generic_liquid_fraction_repr": _repr(generic.get("liquid_fraction")),
            "generic_phase_assemblage_available": (
                None
                if generic.get("phase_assemblage_available") is None
                else int(bool(generic["phase_assemblage_available"]))
            ),
            "generic_liquid_composition_wt_pct_json": _json(
                generic.get("liquid_composition_wt_pct")
            ),
            "generic_liquid_viscosity_Pa_s": _float(
                generic.get("liquid_viscosity_Pa_s")
            ),
            "generic_liquid_viscosity_Pa_s_repr": _repr(
                generic.get("liquid_viscosity_Pa_s")
            ),
            "generic_liquid_density_kg_m3": _float(
                generic.get("liquid_density_kg_m3")
            ),
            "generic_liquid_density_kg_m3_repr": _repr(
                generic.get("liquid_density_kg_m3")
            ),
            "generic_system_enthalpy": _float(generic.get("system_enthalpy")),
            "generic_system_enthalpy_repr": _repr(generic.get("system_enthalpy")),
            "generic_system_entropy": _float(generic.get("system_entropy")),
            "generic_system_entropy_repr": _repr(generic.get("system_entropy")),
            "generic_system_volume": _float(generic.get("system_volume")),
            "generic_system_volume_repr": _repr(generic.get("system_volume")),
            "generic_system_heat_capacity_Cp": _float(
                generic.get("system_heat_capacity_Cp")
            ),
            "generic_system_heat_capacity_Cp_repr": _repr(
                generic.get("system_heat_capacity_Cp")
            ),
            "generic_system_dVdP": _float(generic.get("system_dVdP")),
            "generic_system_dVdP_repr": _repr(generic.get("system_dVdP")),
            "generic_system_dVdT": _float(generic.get("system_dVdT")),
            "generic_system_dVdT_repr": _repr(generic.get("system_dVdT")),
            "generic_system_fO2_delta_QFM": _float(
                generic.get("system_fO2_delta_QFM")
            ),
            "generic_system_fO2_delta_QFM_repr": _repr(
                generic.get("system_fO2_delta_QFM")
            ),
            "generic_system_solid_density_rhos": _float(
                generic.get("system_solid_density_rhos")
            ),
            "generic_system_solid_density_rhos_repr": _repr(
                generic.get("system_solid_density_rhos")
            ),
            "generic_system_phi": _float(generic.get("system_phi")),
            "generic_system_phi_repr": _repr(generic.get("system_phi")),
            "generic_system_chisqr": _float(generic.get("system_chisqr")),
            "generic_system_chisqr_repr": _repr(generic.get("system_chisqr")),
            "generic_phase_thermo_json": _json(generic.get("phase_thermo")),
            "generic_chem_potentials_json": _json(
                generic.get("chem_potentials")
            ),
            "generic_phase_affinities_json": _json(
                generic.get("phase_affinities")
            ),
            "generic_solid_composition_wt_pct_json": _json(
                generic.get("solid_composition_wt_pct")
            ),
            "generic_bulk_composition_wt_pct_json": _json(
                generic.get("bulk_composition_wt_pct")
            ),
            "generic_vapor_pressures_Pa_json": _json(generic.get("vapor_pressures_Pa")),
            "generic_vapor_pressures_source_json": _json(
                generic.get("vapor_pressures_source")
            ),
            "generic_activity_coefficients_json": _json(
                generic.get("activity_coefficients")
            ),
            "generic_fO2_log": _float(generic.get("fO2_log")),
            "generic_fO2_log_repr": _repr(generic.get("fO2_log")),
            "generic_warnings_json": _json(generic.get("warnings")),
            "generic_ledger_transition_json": _json(generic.get("ledger_transition")),
            "generic_status": generic.get("status"),
            "generic_sulfur_saturation_json": _json(generic.get("sulfur_saturation")),
            "generic_liquidus_T_C": _float(generic.get("liquidus_T_C")),
            "generic_liquidus_T_C_repr": _repr(generic.get("liquidus_T_C")),
            "generic_diagnostics_json": _json(generic.get("diagnostics")),
            "alpha_activity_coefficients_json": _json(alpha.get("activity_coefficients")),
            "alpha_applied_fe3fet": _float(alpha.get("applied_fe3fet")),
            "alpha_applied_fe3fet_repr": _repr(alpha.get("applied_fe3fet")),
            "alpha_backend_diagnostics_json": _json(alpha.get("backend_diagnostics")),
            "alpha_backend_status": alpha.get("backend_status"),
            "alpha_backend_status_reason": alpha.get("backend_status_reason"),
            "alpha_backend_warnings_json": _json(alpha.get("backend_warnings")),
            "alpha_engine_version": alpha.get("engine_version"),
            "alpha_fO2_log": _float(alpha.get("fO2_log")),
            "alpha_fO2_log_repr": _repr(alpha.get("fO2_log")),
            "alpha_fe_redox_policy": alpha.get("fe_redox_policy"),
            "alpha_intrinsic_fO2_log": _float(alpha.get("intrinsic_fO2_log")),
            "alpha_intrinsic_fO2_log_repr": _repr(alpha.get("intrinsic_fO2_log")),
            "alpha_liquid_composition_wt_pct_json": _json(
                alpha.get("liquid_composition_wt_pct")
            ),
            "alpha_liquid_fraction": _float(alpha.get("liquid_fraction")),
            "alpha_liquid_fraction_repr": _repr(alpha.get("liquid_fraction")),
            "alpha_liquid_fraction_path_json": _json(alpha.get("liquid_fraction_path")),
            "alpha_liquidus_T_C": _float(alpha.get("liquidus_T_C")),
            "alpha_liquidus_T_C_repr": _repr(alpha.get("liquidus_T_C")),
            "alpha_liquidus_T_K": _float(alpha.get("liquidus_T_K")),
            "alpha_liquidus_T_K_repr": _repr(alpha.get("liquidus_T_K")),
            "alpha_mode": alpha.get("mode"),
            "alpha_phase_masses_kg_json": _json(alpha.get("phase_masses_kg")),
            "alpha_phase_modes_wt_pct_json": _json(alpha.get("phase_modes_wt_pct")),
            "alpha_phases_present_json": _json(alpha.get("phases_present")),
            "alpha_solidus_T_C": _float(alpha.get("solidus_T_C")),
            "alpha_solidus_T_C_repr": _repr(alpha.get("solidus_T_C")),
            "finder_liquidus_T_C": _float(finder.get("liquidus_T_C")),
            "finder_liquidus_T_K": _float(finder.get("liquidus_T_K")),
            "finder_solidus_T_C": _float(finder.get("solidus_T_C")),
            "finder_liquid_fraction": _float(finder.get("liquid_fraction")),
            "finder_status": finder.get("status"),
            "finder_warnings_json": _json(finder.get("warnings")),
            "finder_diagnostics_json": _json(finder.get("diagnostics")),
            "finder_iterations": finder.get("iterations"),
            "finder_samples_json": _json(finder.get("samples")),
            "finder_sample_temperature_C_json": _json(
                finder.get("sample_temperature_C")
            ),
            "finder_sample_frac_M_json": _json(finder.get("sample_frac_M")),
            "curve_source": finder.get("curve_source"),
            "curve_solidus_T_C": _float(finder.get("curve_solidus_T_C")),
            "curve_liquidus_T_C": _float(finder.get("curve_liquidus_T_C")),
            "curve_path_temperature_C_json": _json(
                finder.get("curve_path_temperature_C")
            ),
            "curve_path_liquid_fraction_json": _json(
                finder.get("curve_path_liquid_fraction")
            ),
        }
        return values

    def _insert_or_ignore(
        self, table: str, values: Mapping[str, Any]
    ) -> sqlite3.Cursor:
        columns = tuple(values)
        placeholders = ",".join("?" for _ in columns)
        quoted = ",".join(f'"{name}"' for name in columns)
        return self.connection.execute(
            f'INSERT OR IGNORE INTO "{table}" ({quoted}) VALUES ({placeholders})',
            tuple(values[name] for name in columns),
        )

    def counts(self) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT status_kind, COUNT(*) FROM alphamelts_outputs "
            "WHERE engine_epoch = ? GROUP BY status_kind",
            (self.engine_epoch,),
        )
        counts = {"success": 0, "refusal": 0, "failure": 0}
        for kind, count in rows:
            counts[str(kind)] = int(count)
        counts["total"] = sum(counts.values())
        return counts

    def sample_row(self) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT o.id, o.expedited_key, h.temperature_C, h.pressure_bar, "
            "h.intended_fO2_log, h.fO2_log AS adapter_fO2_log_argument, "
            "o.status, o.status_kind, o.engine_version, "
            "o.timing_s, o.generic_phases_present_json "
            "FROM alphamelts_outputs o JOIN grid_keys h ON h.id = o.grid_key_id "
            "WHERE o.engine_epoch = ? ORDER BY o.id DESC LIMIT 1",
            (self.engine_epoch,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["phases_present"] = json.loads(
            result.pop("generic_phases_present_json") or "[]"
        )
        return result

    def commit(self) -> None:
        self.connection.commit()

    def close(self) -> None:
        self._release_owned_claims()
        self.connection.commit()
        self.connection.close()

    def _release_owned_claims(self) -> None:
        self.connection.execute(
            "DELETE FROM grid_key_claims WHERE claim_owner = ?",
            (self.claim_owner,),
        )

    def __enter__(self) -> "GridCacheWriter":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if exc_type is None:
            self._release_owned_claims()
            self.connection.commit()
        else:
            self.connection.rollback()
            self._release_owned_claims()
            self.connection.commit()
        self.connection.close()


def table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')]
