"""SQLite/WAL result store for recipe optimizer evaluations.

Selector reads require an explicit current code/data scope from the caller;
stored rows never define "current". The store supports concurrent readers plus
serialized writers via WAL, BEGIN IMMEDIATE, and bounded database-locked retry.
O-P3 may designate one write owner or rely on this SQLite serialization.
"""

from __future__ import annotations

from collections.abc import Sequence as SequenceABC
from dataclasses import MISSING, fields
import json
import math
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Mapping, Sequence

from simulator.corpus_version import current_corpus_version
from simulator.diagnostics import (
    coating_summary_with_grounded_authority,
    wall_deposit_sticking_authority_status,
)
from simulator.optimize.canonical import canonical_json_dumps, normalize_canonical_value
from simulator.optimize.evalspec import (
    EvalSpec,
    PrefixEvalSpec,
    _with_legacy_data_digest_scope,
    cache_key,
)
from simulator.optimize.evaluate import (
    FailureCategory,
    MASS_BALANCE_ABORT_PCT,
    RunReference,
    ScoredResult,
)
from simulator.optimize.objective import (
    ObjectiveValue,
    ObjectiveVector,
    normalize_objective_sense,
)
from simulator.optimize.physics import GateMargin, ThresholdSpec
from simulator.backend_names import canonical_backend_name
from simulator.fidelity_vocabulary import backend_name_denies_authority
from simulator.optimize.result_scope import result_scope_json, selector_where

SCHEMA_VERSION = 4
DEFAULT_BUSY_TIMEOUT_MS = 30000
WRITE_RETRY_ATTEMPTS = 8
WRITE_RETRY_BASE_DELAY_S = 0.05
NON_AUTHORITATIVE_BACKEND_STATUSES = frozenset({"diagnostic_stub", "unavailable"})
APPROXIMATE_REDUCED_REAL_CACHE_STATES = frozenset(
    {"cached_interpolated", "cached_physics_bucket"}
)

__all__ = [
    "DEFAULT_BUSY_TIMEOUT_MS",
    "SCHEMA_VERSION",
    "ResultStore",
    "ResultStoreSchemaError",
    "ResultStoreWriteRejected",
    "ResultsStore",
    "selector_where",
]


class ResultStoreSchemaError(RuntimeError):
    """Raised when an optimizer result-store schema is unsupported."""


class ResultStoreWriteRejected(ValueError):
    """Raised when a result is not admissible for authoritative cache writes."""

    def __init__(self, reasons: Sequence[str]) -> None:
        self.reasons = tuple(dict.fromkeys(str(reason) for reason in reasons))
        super().__init__(
            "result store write rejected: " + ", ".join(self.reasons)
        )


class ResultStore:
    """Persistent SHA-256-keyed optimizer run store.

    Selector reads are scoped only to caller-declared current code/data
    provenance. Exact lookup remains keyed solely by EvalSpec.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        current_code_version: str | None = None,
        current_data_digests: Mapping[str, str] | None = None,
        current_result_scope: Mapping[str, Any] | None = None,
        code_version: str | None = None,
        data_digests: Mapping[str, str] | None = None,
        result_scope: Mapping[str, Any] | None = None,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    ) -> None:
        if code_version is not None:
            if current_code_version is not None and current_code_version != code_version:
                raise ValueError("current_code_version conflicts with code_version")
            current_code_version = code_version
        if data_digests is not None:
            current_data_digests_json = _canonical_json(data_digests)
            if (
                current_data_digests is not None
                and _canonical_json(current_data_digests) != current_data_digests_json
            ):
                raise ValueError("current_data_digests conflicts with data_digests")
            current_data_digests = data_digests
        if result_scope is not None:
            if (
                current_result_scope is not None
                and _canonical_json(current_result_scope) != _canonical_json(result_scope)
            ):
                raise ValueError("current_result_scope conflicts with result_scope")
            current_result_scope = result_scope
        self.path = Path(path)
        self.busy_timeout_ms = int(busy_timeout_ms)
        self._write_lock = threading.Lock()
        self._scope_code_version = current_code_version
        self._scope_data_digests_json = (
            _canonical_json(current_data_digests)
            if current_data_digests is not None
            else None
        )
        self._scope_result_scope_json = _canonical_json(current_result_scope or {})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def initialize(self) -> None:
        with self._write_lock:
            self._execute_write(self._initialize)

    @property
    def schema_version(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM store_meta WHERE key = 'schema_version'"
            ).fetchone()
        return int(row["value"])

    def set_current_scope(
        self,
        *,
        code_version: str,
        data_digests: Mapping[str, str],
        result_scope: Mapping[str, Any] | None = None,
    ) -> None:
        self._scope_code_version = code_version
        self._scope_data_digests_json = _canonical_json(data_digests)
        self._scope_result_scope_json = _canonical_json(result_scope or {})

    def set_current_version(
        self,
        *,
        code_version: str,
        data_digests: Mapping[str, str],
        result_scope: Mapping[str, Any] | None = None,
    ) -> None:
        self.set_current_scope(
            code_version=code_version,
            data_digests=data_digests,
            result_scope=result_scope,
        )

    def store(
        self,
        eval_spec: EvalSpec,
        scored_result: ScoredResult,
        *,
        created_at: str,
    ) -> None:
        key = cache_key(eval_spec)
        if scored_result.cache_key is not None and scored_result.cache_key != key:
            raise ValueError("scored_result.cache_key does not match eval_spec")
        if scored_result.eval_spec is not None and scored_result.eval_spec != eval_spec:
            raise ValueError("scored_result.eval_spec does not match eval_spec")
        _validate_result_artifact(eval_spec, scored_result)
        _assert_cache_write_admissible(scored_result)
        objectives = _serialize_objectives(scored_result.objectives)
        result_blob = _result_blob(scored_result)
        corpus_version = current_corpus_version()
        with self._write_lock:
            def write(conn: sqlite3.Connection) -> None:
                conn.execute(
                    """
                    INSERT INTO results (
                        cache_key, feedstock_id, recipe_id, profile_id, fidelity,
                        code_version, corpus_version, data_digests, result_scope,
                        feasible, failure_category, objectives, feasibility_margins,
                        failing_gates, candidate_id,
                        result_blob, run_reference, eval_spec, notes, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        feedstock_id = excluded.feedstock_id,
                        recipe_id = excluded.recipe_id,
                        profile_id = excluded.profile_id,
                        fidelity = excluded.fidelity,
                        code_version = excluded.code_version,
                        corpus_version = excluded.corpus_version,
                        data_digests = excluded.data_digests,
                        result_scope = excluded.result_scope,
                        feasible = excluded.feasible,
                        failure_category = excluded.failure_category,
                        objectives = excluded.objectives,
                        feasibility_margins = excluded.feasibility_margins,
                        failing_gates = excluded.failing_gates,
                        candidate_id = excluded.candidate_id,
                        result_blob = excluded.result_blob,
                        run_reference = excluded.run_reference,
                        eval_spec = excluded.eval_spec,
                        notes = excluded.notes,
                        created_at = excluded.created_at
                    """,
                    (
                        key,
                        eval_spec.feedstock_id,
                        eval_spec.recipe_id,
                        eval_spec.profile_id,
                        eval_spec.fidelity,
                        eval_spec.code_version,
                        corpus_version,
                        _canonical_json(eval_spec.data_digests),
                        result_scope_json(eval_spec),
                        int(scored_result.feasible),
                        (
                            scored_result.failure_category.value
                            if scored_result.failure_category is not None
                            else None
                        ),
                        _json_dump(objectives),
                        _json_dump(_serialize_margins(scored_result.feasibility_margins)),
                        _json_dump(list(scored_result.failing_gates)),
                        scored_result.candidate_id,
                        _json_dump(result_blob),
                        _json_dump(_serialize_run_reference(scored_result.run_reference)),
                        _json_dump(_serialize_eval_spec(eval_spec)),
                        _json_dump(list(scored_result.notes)),
                        created_at,
                    ),
                )
                conn.execute(
                    "DELETE FROM objective_values WHERE cache_key = ?",
                    (key,),
                )
                conn.executemany(
                    """
                    INSERT INTO objective_values (
                        cache_key, metric, sense, value, units, ordinal
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            key,
                            objective["metric"],
                            objective["sense"],
                            objective["value"],
                            objective["units"],
                            objective["ordinal"],
                        )
                        for objective in objectives
                    ],
                )

            self._execute_write(write)

    def lookup(self, eval_spec: EvalSpec) -> ScoredResult | None:
        key = cache_key(eval_spec)
        return self.fetch(key)

    def fetch(self, cache_key_value: str) -> ScoredResult | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM results WHERE cache_key = ?",
                (cache_key_value,),
            ).fetchone()
        if row is None:
            return None
        if row["corpus_version"] != current_corpus_version():
            return None
        return _row_to_scored_result(row)

    def query(
        self,
        feedstock_id: str,
        *,
        profile_id: str | None = None,
        fidelity: str | None = None,
        code_version: str | None = None,
        data_digests: Mapping[str, str] | None = None,
        result_scope: Mapping[str, Any] | None = None,
    ) -> list[ScoredResult]:
        where, params = self._selector_where(
            feedstock_id,
            profile_id=profile_id,
            fidelity=fidelity,
            code_version=code_version,
            data_digests=data_digests,
            result_scope=result_scope,
        )
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM results
                WHERE {where} AND corpus_version = ?
                ORDER BY created_at DESC, cache_key ASC
                """,
                (*params, current_corpus_version()),
            ).fetchall()
        return [_row_to_scored_result(row) for row in rows]

    def best(
        self,
        feedstock_id: str,
        *,
        objective_metric: str | None = None,
        profile_id: str | None = None,
        fidelity: str | None = None,
        code_version: str | None = None,
        data_digests: Mapping[str, str] | None = None,
        result_scope: Mapping[str, Any] | None = None,
    ) -> ScoredResult | None:
        metric = objective_metric or self._default_objective_metric(
            feedstock_id,
            profile_id=profile_id,
            fidelity=fidelity,
            code_version=code_version,
            data_digests=data_digests,
            result_scope=result_scope,
        )
        if metric is None:
            return None
        where, params = self._selector_where(
            feedstock_id,
            profile_id=profile_id,
            fidelity=fidelity,
            code_version=code_version,
            data_digests=data_digests,
            result_scope=result_scope,
        )
        with self._connect() as conn:
            objective = conn.execute(
                f"""
                SELECT ov.sense
                FROM results r
                JOIN objective_values ov ON ov.cache_key = r.cache_key
                WHERE {where} AND r.feasible = 1 AND r.corpus_version = ?
                    AND ov.metric = ?
                GROUP BY ov.sense
                ORDER BY ov.sense ASC
                LIMIT 2
                """,
                (*params, current_corpus_version(), metric),
            ).fetchall()
            if not objective:
                return None
            if len(objective) != 1:
                raise ValueError(f"objective {metric!r} has conflicting senses")
            sense = normalize_objective_sense(str(objective[0]["sense"]))
            value_order = "ASC" if sense == "minimize" else "DESC"
            row = conn.execute(
                f"""
                SELECT r.*
                FROM results r
                JOIN objective_values ov ON ov.cache_key = r.cache_key
                WHERE {where} AND r.feasible = 1 AND r.corpus_version = ?
                    AND ov.metric = ?
                ORDER BY ov.value {value_order}, r.cache_key ASC
                LIMIT 1
                """,
                (*params, current_corpus_version(), metric),
            ).fetchone()
        return _row_to_scored_result(row) if row is not None else None

    def _default_objective_metric(
        self,
        feedstock_id: str,
        *,
        profile_id: str | None,
        fidelity: str | None,
        code_version: str | None,
        data_digests: Mapping[str, str] | None,
        result_scope: Mapping[str, Any] | None,
    ) -> str | None:
        where, params = self._selector_where(
            feedstock_id,
            profile_id=profile_id,
            fidelity=fidelity,
            code_version=code_version,
            data_digests=data_digests,
            result_scope=result_scope,
        )
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT ov.metric
                FROM results r
                JOIN objective_values ov ON ov.cache_key = r.cache_key
                WHERE {where} AND r.feasible = 1 AND r.corpus_version = ?
                ORDER BY ov.ordinal ASC, ov.metric ASC, r.cache_key ASC
                LIMIT 1
                """,
                (*params, current_corpus_version()),
            ).fetchone()
        return str(row["metric"]) if row is not None else None

    def _selector_where(
        self,
        feedstock_id: str,
        *,
        profile_id: str | None,
        fidelity: str | None,
        code_version: str | None,
        data_digests: Mapping[str, str] | None,
        result_scope: Mapping[str, Any] | None,
    ) -> tuple[str, tuple[Any, ...]]:
        active_code_version = code_version or self._scope_code_version
        active_data_digests = (
            _canonical_json(data_digests)
            if data_digests is not None
            else self._scope_data_digests_json
        )
        active_result_scope = (
            _canonical_json(result_scope)
            if result_scope is not None
            else self._scope_result_scope_json
        )
        return selector_where(
            feedstock_id,
            profile_id=profile_id,
            fidelity=fidelity,
            code_version=active_code_version,
            data_digests_json=active_data_digests,
            result_scope_json=active_result_scope,
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        return conn

    def _initialize(self, conn: sqlite3.Connection) -> None:
        self._create_schema(conn)
        row = conn.execute(
            "SELECT value FROM store_meta WHERE key = 'schema_version'"
        ).fetchone()
        version = int(row["value"]) if row is not None else 0
        if version > SCHEMA_VERSION:
            raise ResultStoreSchemaError(
                f"result store schema {version} is newer than supported {SCHEMA_VERSION}"
            )
        self._migrate(conn, version)

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS store_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS results (
                cache_key TEXT PRIMARY KEY,
                feedstock_id TEXT NOT NULL,
                recipe_id TEXT NOT NULL,
                profile_id TEXT NOT NULL,
                fidelity TEXT NOT NULL,
                code_version TEXT NOT NULL,
                corpus_version TEXT,
                data_digests TEXT NOT NULL,
                result_scope TEXT NOT NULL DEFAULT '{}',
                feasible INTEGER NOT NULL CHECK (feasible IN (0, 1)),
                failure_category TEXT,
                objectives TEXT NOT NULL,
                feasibility_margins TEXT NOT NULL,
                failing_gates TEXT NOT NULL,
                candidate_id TEXT,
                result_blob TEXT NOT NULL,
                run_reference TEXT NOT NULL,
                eval_spec TEXT NOT NULL,
                notes TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS objective_values (
                cache_key TEXT NOT NULL,
                metric TEXT NOT NULL,
                sense TEXT NOT NULL CHECK (sense IN ('minimize', 'maximize')),
                value REAL NOT NULL,
                units TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                PRIMARY KEY (cache_key, metric),
                FOREIGN KEY (cache_key) REFERENCES results(cache_key)
                    ON DELETE CASCADE
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_results_selector
                ON results(feedstock_id, recipe_id, fidelity)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_objective_values_metric
                ON objective_values(metric, sense, ordinal, value, cache_key)
            """,
        )
        for statement in statements:
            conn.execute(statement)

    def _migrate(self, conn: sqlite3.Connection, version: int) -> None:
        if version < 1:
            conn.execute(
                """
                INSERT INTO store_meta(key, value)
                VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )
            version = 1
        if version < 2:
            if not _column_exists(conn, "objective_values", "ordinal"):
                conn.execute(
                    "ALTER TABLE objective_values ADD COLUMN ordinal INTEGER NOT NULL DEFAULT 0"
                )
            _migrate_objective_values_v2(conn)
            conn.execute(
                """
                INSERT INTO store_meta(key, value)
                VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )
            version = 2
        if version < 3:
            if not _column_exists(conn, "results", "result_scope"):
                conn.execute(
                    "ALTER TABLE results ADD COLUMN result_scope TEXT NOT NULL DEFAULT '{}'"
                )
            conn.execute(
                """
                INSERT INTO store_meta(key, value)
                VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )
            version = 3
        if version < 4:
            if not _column_exists(conn, "results", "corpus_version"):
                conn.execute("ALTER TABLE results ADD COLUMN corpus_version TEXT")
            conn.execute(
                """
                INSERT INTO store_meta(key, value)
                VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )
            version = 4
        if version != SCHEMA_VERSION:
            raise ResultStoreSchemaError(f"unsupported result store schema {version}")
        self._refresh_current_selector_index(conn)

    def _refresh_current_selector_index(self, conn: sqlite3.Connection) -> None:
        conn.execute("DROP INDEX IF EXISTS idx_results_current_selector")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_results_current_selector
                ON results(feedstock_id, profile_id, fidelity, code_version, data_digests, result_scope)
            """
        )

    def _execute_write(self, operation: Any) -> Any:
        last_error: sqlite3.OperationalError | None = None
        for attempt in range(WRITE_RETRY_ATTEMPTS):
            conn: sqlite3.Connection | None = None
            try:
                conn = self._connect()
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("BEGIN IMMEDIATE")
                result = operation(conn)
                conn.commit()
                return result
            except sqlite3.OperationalError as exc:
                if conn is not None:
                    try:
                        conn.rollback()
                    except sqlite3.Error:
                        pass
                if not _is_locked_error(exc) or attempt == WRITE_RETRY_ATTEMPTS - 1:
                    raise
                last_error = exc
                time.sleep(WRITE_RETRY_BASE_DELAY_S * (2**attempt))
            finally:
                if conn is not None:
                    conn.close()
        raise last_error if last_error is not None else RuntimeError("write retry failed")


def _row_to_scored_result(row: sqlite3.Row) -> ScoredResult:
    failure_category = row["failure_category"]
    failure = FailureCategory(failure_category) if failure_category is not None else None
    margins = _deserialize_grounding_margins(row["feasibility_margins"])
    feasible = grounded_result_feasible(
        bool(row["feasible"]),
        failure_category=failure,
        margins=margins,
    )
    objectives = _deserialize_objectives(_json_load(row["objectives"]))
    return ScoredResult(
        candidate_id=row["candidate_id"],
        eval_spec=_deserialize_eval_spec(_json_load(row["eval_spec"])),
        cache_key=row["cache_key"],
        feasible=feasible,
        failure_category=failure,
        objectives=objectives if feasible else None,
        feasibility_margins=margins,
        failing_gates=grounded_failing_gates(
            tuple(_json_load(row["failing_gates"])),
            margins=margins,
        ),
        run_reference=_deserialize_run_reference(
            _json_load(row["run_reference"]),
            _json_load(row["result_blob"]),
        ),
        notes=tuple(_json_load(row["notes"])),
    )


def _validate_result_artifact(eval_spec: EvalSpec, scored_result: ScoredResult) -> None:
    if scored_result.cache_key is None:
        raise ValueError("result artifact missing cache_key")
    if scored_result.cache_key != cache_key(eval_spec):
        raise ValueError("scored_result.cache_key does not match eval_spec")
    if scored_result.feasible:
        if scored_result.objectives is None:
            raise ValueError("result artifact missing objectives")
    elif scored_result.failure_category is None:
        raise ValueError("result artifact missing failure_category")
    if not scored_result.feasibility_margins:
        raise ValueError("result artifact missing feasibility_margins")
    if _artifact_backend_status(scored_result) is None:
        raise ValueError("result artifact missing backend_status")


def grounded_result_feasible(
    stored_feasible: bool,
    *,
    failure_category: FailureCategory | str | None,
    margins: Mapping[str, GateMargin],
) -> bool:
    """Re-derive feasible from grounded margins, failing closed for legacy gaps.

    SC-42 intentionally drops legacy/corrupt rows with stored feasible=1 but
    missing, empty, or malformed feasibility_margins from feasible surfaces.
    Stored-infeasible rows without re-derivable margins remain infeasible.
    """

    if failure_category not in (None, ""):
        return False
    if margins:
        return all(bool(margin.feasible) for margin in margins.values())
    return False


def grounded_failing_gates(
    stored_failing_gates: Sequence[str],
    *,
    margins: Mapping[str, GateMargin],
) -> tuple[str, ...]:
    if margins:
        return tuple(
            str(gate)
            for gate, margin in margins.items()
            if not bool(margin.feasible)
        )
    return tuple(str(gate) for gate in stored_failing_gates)


def reground_scored_result(scored_result: ScoredResult) -> ScoredResult:
    feasible = grounded_result_feasible(
        scored_result.feasible,
        failure_category=scored_result.failure_category,
        margins=scored_result.feasibility_margins,
    )
    failing_gates = grounded_failing_gates(
        scored_result.failing_gates,
        margins=scored_result.feasibility_margins,
    )
    if feasible == scored_result.feasible and failing_gates == scored_result.failing_gates:
        return scored_result
    return ScoredResult(
        candidate_id=scored_result.candidate_id,
        eval_spec=scored_result.eval_spec,
        cache_key=scored_result.cache_key,
        feasible=feasible,
        failure_category=None if feasible else scored_result.failure_category,
        objectives=scored_result.objectives if feasible else None,
        feasibility_margins=scored_result.feasibility_margins,
        failing_gates=failing_gates,
        run_reference=scored_result.run_reference,
        notes=scored_result.notes,
    )


def _assert_cache_write_admissible(scored_result: ScoredResult) -> None:
    if not scored_result.feasible:
        return
    reasons: list[str] = []
    backend_status = _artifact_backend_status(scored_result)
    backend_authoritative = _artifact_backend_authoritative(scored_result)
    if backend_authoritative is not True:
        reasons.append("non_authoritative_backend")
    contradiction = _backend_authority_contradiction(
        backend_status,
        backend_authoritative,
    )
    if contradiction is not None:
        reasons.append(contradiction)
    backend_name = _artifact_backend_name(scored_result)
    name_rejection = _backend_name_admission_rejection(backend_name)
    if name_rejection is not None:
        reasons.append(name_rejection)
    approximate_cache = _approximate_reduced_real_cache_state(scored_result)
    if approximate_cache is not None:
        reasons.append(f"approximate_reduced_real_cache_state:{approximate_cache}")
    closure_rejection = _mass_balance_closure_rejection(scored_result)
    if closure_rejection is not None:
        reasons.append(closure_rejection)
    if _has_out_of_domain_provenance(scored_result):
        reasons.append("out_of_domain_provenance")
    if reasons:
        raise ResultStoreWriteRejected(reasons)


def _artifact_backend_status(scored_result: ScoredResult) -> str | None:
    for carrier in _admission_carriers(scored_result):
        status = _extract_backend_status(carrier)
        if status is not None:
            return status
    return None


def _artifact_backend_name(scored_result: ScoredResult) -> str | None:
    for carrier in _admission_carriers(scored_result):
        name = _extract_backend_name(carrier)
        if name is not None:
            return canonical_backend_name(name)
    return None


def _extract_backend_name(carrier: Any) -> str | None:
    raw = _carrier_value(carrier, "backend_name")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _backend_name_admission_rejection(backend_name: str | None) -> str | None:
    if backend_name is None:
        return None
    if backend_name_denies_authority(backend_name):
        return f"backend_name_non_authoritative:{backend_name}"
    return None


def _artifact_backend_authoritative(scored_result: ScoredResult) -> bool | None:
    for carrier in _admission_carriers(scored_result):
        raw = _carrier_value(carrier, "backend_authoritative")
        if raw is not None:
            return _strict_bool(raw)
    return None


def _backend_authority_contradiction(
    backend_status: str | None,
    backend_authoritative: bool | None,
) -> str | None:
    status = str(backend_status or "").strip()
    if (
        backend_authoritative is True
        and status.lower() in NON_AUTHORITATIVE_BACKEND_STATUSES
    ):
        return f"backend_authority_contradicts_status:{status}"
    return None


def _approximate_reduced_real_cache_state(scored_result: ScoredResult) -> str | None:
    for carrier in _admission_carriers(scored_result):
        for state in _cache_states_from_carrier(carrier):
            normalized = str(state).strip()
            if normalized in APPROXIMATE_REDUCED_REAL_CACHE_STATES:
                return normalized
    return None


def _cache_states_from_carrier(carrier: Any) -> tuple[Any, ...]:
    states: list[Any] = []
    for key in ("cache_state", "reduced_real_cache_state"):
        state = _carrier_value(carrier, key)
        if state is not None:
            states.append(state)
    for key in ("per_hour", "per_hour_summary"):
        per_hour = _carrier_value(carrier, key)
        if not isinstance(per_hour, SequenceABC) or isinstance(per_hour, (str, bytes)):
            continue
        for entry in per_hour:
            if entry is None:
                continue
            state = _carrier_value(entry, "reduced_real_cache_state")
            if state is not None:
                states.append(state)
    return tuple(states)


def _admission_carriers(scored_result: ScoredResult) -> tuple[Any, ...]:
    carriers: list[Any] = []
    run_reference = getattr(scored_result, "run_reference", None)
    if run_reference is not None:
        carriers.extend(
            (
                run_reference,
                getattr(run_reference, "trace", None),
                getattr(run_reference, "product_summary", None),
            )
        )
    if hasattr(scored_result, "result_blob"):
        carriers.append(getattr(scored_result, "result_blob"))
    return tuple(carrier for carrier in carriers if carrier is not None)


def _carrier_value(carrier: Any, key: str) -> Any:
    if carrier is None:
        return None
    if isinstance(carrier, Mapping):
        return carrier.get(key)
    return getattr(carrier, key, None)


def _has_carrier_key(carrier: Any, key: str) -> bool:
    if carrier is None:
        return False
    if isinstance(carrier, Mapping):
        return key in carrier
    return hasattr(carrier, key)


def _mass_balance_closure_rejection(scored_result: ScoredResult) -> str | None:
    payloads = _iter_mass_balance_payloads(scored_result)
    if not payloads:
        return "no_mass_balance_proof"
    for payload in payloads:
        rejection = _mass_balance_payload_rejection(payload)
        if rejection is not None:
            return rejection
    return None


def _iter_mass_balance_payloads(scored_result: ScoredResult) -> tuple[Any, ...]:
    payloads: list[Any] = []
    for carrier in _admission_carriers(scored_result):
        payloads.extend(_mass_balance_payloads_from_carrier(carrier))
    return tuple(payloads)


def _mass_balance_payloads_from_carrier(carrier: Any) -> tuple[Any, ...]:
    payloads: list[Any] = []
    if _has_carrier_key(carrier, "mass_balance_error_pct") or _has_carrier_key(
        carrier, "mass_balance_error_category"
    ):
        payloads.append(carrier)

    mass_closure = _carrier_value(carrier, "mass_closure")
    if mass_closure is not None:
        payloads.append(mass_closure)
    product_yield_table = _carrier_value(carrier, "product_yield_table")
    if product_yield_table is not None:
        nested = _carrier_value(product_yield_table, "mass_closure")
        if nested is not None:
            payloads.append(nested)

    payloads.extend(_mass_balance_snapshot_payloads(_carrier_value(carrier, "snapshots")))
    record = _carrier_value(carrier, "record")
    if record is not None:
        payloads.extend(
            _mass_balance_snapshot_payloads(_carrier_value(record, "snapshots"))
        )
    return tuple(payloads)


def _mass_balance_snapshot_payloads(snapshots: Any) -> tuple[Any, ...]:
    if not isinstance(snapshots, SequenceABC) or isinstance(snapshots, (str, bytes)):
        return ()
    return tuple(snapshots)


def _mass_balance_payload_rejection(payload: Any) -> str | None:
    category = str(_carrier_value(payload, "mass_balance_error_category") or "")
    if category:
        return f"mass_balance_closure_breach:{category}"
    status = str(_carrier_value(payload, "status") or "")
    if status and status != "closed":
        if status == "open":
            return "mass_balance_closure_breach:open"
        return f"mass_balance_closure_not_closed:{status}"
    raw = _first_present(
        payload,
        (
            "mass_balance_error_pct",
            "balance_error_pct",
            "closure_pct",
        ),
    )
    if raw is None:
        return None
    try:
        closure_pct = float(raw)
    except (TypeError, ValueError):
        return "mass_balance_closure_nonfinite"
    if not math.isfinite(closure_pct):
        return "mass_balance_closure_nonfinite"
    if abs(closure_pct) > MASS_BALANCE_ABORT_PCT:
        return (
            "mass_balance_closure_breach:"
            f"{closure_pct:.12g}%>{MASS_BALANCE_ABORT_PCT:.12g}%"
        )
    return None


def _first_present(carrier: Any, keys: Sequence[str]) -> Any:
    for key in keys:
        if _has_carrier_key(carrier, key):
            return _carrier_value(carrier, key)
    return None


def _has_out_of_domain_provenance(scored_result: ScoredResult) -> bool:
    if scored_result.failure_category is FailureCategory.OUT_OF_DOMAIN:
        return True
    for carrier in _admission_carriers(scored_result):
        if _extract_backend_status(carrier) == "out_of_domain":
            return True
        if _extract_backend_status_reason(carrier) is not None:
            return True
        if _contains_out_of_domain_marker(carrier):
            return True
    return False


def _extract_backend_status_reason(carrier: Any) -> str | None:
    if carrier is None:
        return None
    raw = _carrier_value(carrier, "backend_status_reason")
    if raw is not None:
        return str(raw)
    for key in ("per_hour", "hours"):
        nested = _carrier_value(carrier, key)
        if isinstance(nested, SequenceABC) and not isinstance(nested, (str, bytes)):
            for item in reversed(tuple(nested)):
                reason = _extract_backend_status_reason(item)
                if reason is not None:
                    return reason
    for key in ("trace", "backend_diagnostics", "diagnostics"):
        reason = _extract_backend_status_reason(_carrier_value(carrier, key))
        if reason is not None:
            return reason
    return None


def _contains_out_of_domain_marker(
    carrier: Any,
    *,
    depth: int = 0,
    seen: set[int] | None = None,
) -> bool:
    if carrier is None or depth > 6:
        return False
    if seen is None:
        seen = set()
    marker = id(carrier)
    if marker in seen:
        return False
    seen.add(marker)
    if isinstance(carrier, Mapping):
        if carrier.get("out_of_domain_crash_point") is not None:
            return True
        if carrier.get("backend_status_reason") not in (None, ""):
            return True
        values = carrier.values()
    elif isinstance(carrier, SequenceABC) and not isinstance(carrier, (str, bytes)):
        values = carrier
    else:
        values = (
            getattr(carrier, attr)
            for attr in (
                "trace",
                "backend_diagnostics",
                "diagnostics",
                "out_of_domain_crash_point",
                "backend_status_reason",
            )
            if hasattr(carrier, attr)
        )
    return any(
        _contains_out_of_domain_marker(value, depth=depth + 1, seen=seen)
        for value in values
    )


def _extract_backend_status(carrier: Any) -> str | None:
    if carrier is None:
        return None
    if isinstance(carrier, Mapping):
        raw = carrier.get("backend_status")
        if raw is not None:
            return str(raw)
        for key in ("per_hour", "hours"):
            nested = carrier.get(key)
            status = _extract_latest_backend_status(nested)
            if status is not None:
                return status
        for key in ("trace", "backend_diagnostics", "diagnostics"):
            status = _extract_backend_status(carrier.get(key))
            if status is not None:
                return status
        return None
    raw = getattr(carrier, "backend_status", None)
    if raw is not None:
        return str(raw)
    for attr in ("per_hour", "hours"):
        status = _extract_latest_backend_status(getattr(carrier, attr, None))
        if status is not None:
            return status
    for attr in ("trace", "backend_diagnostics", "diagnostics"):
        status = _extract_backend_status(getattr(carrier, attr, None))
        if status is not None:
            return status
    return None


def _extract_latest_backend_status(value: Any) -> str | None:
    if not isinstance(value, SequenceABC) or isinstance(value, (str, bytes)) or not value:
        return None
    return _extract_backend_status(value[-1])


def _serialize_eval_spec(eval_spec: EvalSpec) -> dict[str, Any]:
    payload = {
        field.name: _jsonable(getattr(eval_spec, field.name))
        for field in fields(type(eval_spec))
    }
    if not eval_spec.allow_fallback_vapor:
        payload.pop("vapor_pressure_fallback_provider_id", None)
    if not eval_spec.stop_at_stage0_exit:
        payload.pop("stop_at_stage0_exit", None)
    return payload


def _deserialize_eval_spec(payload: Mapping[str, Any]) -> EvalSpec:
    eval_spec_type = payload.get("eval_spec_type")
    if eval_spec_type == "prefix":
        return PrefixEvalSpec(**_eval_spec_kwargs(PrefixEvalSpec, payload))
    if eval_spec_type is not None:
        raise ResultStoreSchemaError(f"unknown eval_spec_type {eval_spec_type!r}")
    return EvalSpec(**_eval_spec_kwargs(EvalSpec, payload))


def _eval_spec_kwargs(eval_spec_cls: type[EvalSpec], payload: Mapping[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field in fields(eval_spec_cls):
        if field.name in payload:
            values[field.name] = payload[field.name]
        elif field.default is not MISSING:
            values[field.name] = field.default
        elif field.default_factory is not MISSING:
            values[field.name] = field.default_factory()
        else:
            raise KeyError(field.name)
    # Legacy rows (pre materials/species_catalog cache scope) get sentinel
    # digests so they deserialize, but their stored cache_key was computed
    # without those keys — so cache_key(deserialized_legacy_spec) != the stored
    # key, and lookup() by re-derived key self-misses a legacy row (it recomputes
    # instead). That is the SAFE direction (cache-miss -> recompute, never
    # stale-reuse); legacy rows remain reachable by their stored key via fetch().
    values["data_digests"] = _with_legacy_data_digest_scope(values["data_digests"])
    return values


def _serialize_objectives(objectives: ObjectiveVector | None) -> list[dict[str, Any]]:
    if objectives is None:
        return []
    return [
        {
            "metric": value.metric,
            "sense": value.sense,
            "value": value.value,
            "units": value.units,
            "ordinal": value.ordinal,
        }
        for value in objectives.values
    ]


def _deserialize_objectives(payload: Sequence[Mapping[str, Any]]) -> ObjectiveVector | None:
    if not payload:
        return None
    return ObjectiveVector(
        tuple(
            ObjectiveValue(
                metric=str(item["metric"]),
                sense=_objective_payload_sense(item),
                value=(
                    None
                    if item.get("value") is None
                    else float(item["value"])
                ),
                units=str(item.get("units", "")),
                ordinal=int(item.get("ordinal", ordinal)),
            )
            for ordinal, item in enumerate(payload)
        )
    )


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row["name"] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def _migrate_objective_values_v2(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT cache_key, objectives FROM results").fetchall()
    for row in rows:
        payload = _json_load(row["objectives"])
        if not payload:
            continue
        updated_payload: list[dict[str, Any]] = []
        for ordinal, item in enumerate(payload):
            updated = dict(item)
            updated["sense"] = _objective_payload_sense(updated)
            updated["ordinal"] = int(updated.get("ordinal", ordinal))
            updated_payload.append(updated)
            conn.execute(
                """
                UPDATE objective_values
                SET sense = ?, ordinal = ?
                WHERE cache_key = ? AND metric = ?
                """,
                (
                    updated["sense"],
                    updated["ordinal"],
                    row["cache_key"],
                    str(updated["metric"]),
                ),
            )
        conn.execute(
            "UPDATE results SET objectives = ? WHERE cache_key = ?",
            (_json_dump(updated_payload), row["cache_key"]),
        )


def _is_locked_error(exc: sqlite3.OperationalError) -> bool:
    return "database is locked" in str(exc).lower() or "database schema is locked" in str(exc).lower()


def _objective_payload_sense(item: Mapping[str, Any]) -> str:
    if "sense" not in item:
        raise ValueError("objective sense is required")
    return normalize_objective_sense(str(item["sense"]))


def _serialize_margins(margins: Mapping[str, GateMargin]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for gate, margin in margins.items():
        payload = {
            "gate": margin.gate,
            "feasible": margin.feasible,
            "margin": _json_number(margin.margin, f"{gate}.margin"),
            "threshold": {
                "id": margin.threshold.id,
                "value": margin.threshold.value,
                "units": margin.threshold.units,
                "source": margin.threshold.source,
                "source_ref": margin.threshold.source_ref,
                "tolerance": margin.threshold.tolerance,
            },
            "observed": _json_number(margin.observed, f"{gate}.observed"),
            "detail": margin.detail,
        }
        _add_margin_status_fields(payload, margin)
        result[gate] = payload
    return result


def _add_margin_status_fields(payload: dict[str, Any], margin: GateMargin) -> None:
    payload["status"] = margin.status
    payload["authoritative"] = bool(margin.authoritative)
    payload["output_status"] = margin.output_status
    if margin.status_reason:
        payload["status_reason"] = margin.status_reason
    if margin.status_payload:
        payload["status_payload"] = _jsonable(margin.status_payload)


def _deserialize_margins(payload: Mapping[str, Mapping[str, Any]]) -> dict[str, GateMargin]:
    margins: dict[str, GateMargin] = {}
    for gate, item in payload.items():
        threshold = item["threshold"]
        gate_name = str(item["gate"])
        authoritative_default = not (
            gate_name == "coating" and "authoritative" not in item
        )
        authoritative_raw = (
            item["authoritative"]
            if "authoritative" in item
            else authoritative_default
        )
        status_value = str(item.get("status", "available"))
        authoritative_value = _strict_bool(authoritative_raw)
        output_status = str(item.get("output_status", "authoritative"))
        status_reason = str(item.get("status_reason", ""))
        status_payload = (
            _jsonable(item.get("status_payload", {}))
            if isinstance(item.get("status_payload", {}), Mapping)
            else {}
        )
        observed_value = _decode_json_number(item["observed"], f"{gate}.observed")
        threshold_value = float(threshold["value"])
        threshold_tolerance = float(threshold.get("tolerance", 0.0))
        feasible_value = bool(item["feasible"])
        if gate_name == "coating":
            grounded_authority = _coating_margin_grounded_authority(status_payload)
            if grounded_authority is not None:
                authoritative_value = bool(
                    grounded_authority.get("authoritative_for_coating", False)
                )
                feasible_value = (
                    observed_value >= threshold_value - threshold_tolerance
                    if authoritative_value
                    else True
                )
                status_value = "available" if authoritative_value else "warning"
                output_status = str(
                    grounded_authority.get("output_status")
                    or ("authoritative" if authoritative_value else "status_bearing")
                )
                status_reason = (
                    ""
                    if authoritative_value
                    else str(grounded_authority.get("message", "non-authoritative coating"))
                )
                status_payload = _jsonable(grounded_authority)
        margins[str(gate)] = GateMargin(
            gate=gate_name,
            feasible=feasible_value,
            margin=_decode_json_number(item["margin"], f"{gate}.margin"),
            threshold=ThresholdSpec(
                id=str(threshold["id"]),
                value=threshold_value,
                units=str(threshold["units"]),
                source=threshold["source"],
                source_ref=str(threshold["source_ref"]),
                tolerance=threshold_tolerance,
            ),
            observed=observed_value,
            detail=str(item["detail"]),
            status=status_value,
            authoritative=authoritative_value,
            output_status=output_status,
            status_reason=status_reason,
            status_payload=status_payload,
        )
    return margins


def _deserialize_grounding_margins(payload: Any) -> dict[str, GateMargin]:
    try:
        decoded = _json_load(payload) if isinstance(payload, str) else payload
    except (TypeError, ValueError):
        return {}
    if not isinstance(decoded, Mapping):
        return {}
    try:
        return _deserialize_margins(decoded)
    except (KeyError, TypeError, ValueError):
        return {}


def _coating_margin_grounded_authority(
    status_payload: Mapping[str, Any],
) -> dict[str, Any] | None:
    deposited_species = _coating_margin_deposited_species(status_payload)
    if not deposited_species:
        return None
    wall_deposit = {species: 1.0 for species in deposited_species}
    return wall_deposit_sticking_authority_status(wall_deposit, status_payload)


def _coating_margin_deposited_species(
    status_payload: Mapping[str, Any],
) -> tuple[str, ...]:
    raw = status_payload.get("deposited_species")
    if isinstance(raw, str):
        return (raw,) if raw else ()
    if not isinstance(raw, SequenceABC):
        return ()
    return tuple(sorted(str(species) for species in raw if str(species)))


def _serialize_run_reference(run_reference: RunReference | None) -> dict[str, Any] | None:
    if run_reference is None:
        return None
    return {
        "status": run_reference.status,
        "error_message": run_reference.error_message,
        "reason": run_reference.reason,
        "product_summary": _jsonable(run_reference.product_summary),
        "backend_name": run_reference.backend_name,
        "backend_status": run_reference.backend_status,
        "backend_authoritative": run_reference.backend_authoritative,
        "evidence_class": run_reference.evidence_class,
        "cache_state": run_reference.cache_state,
        "runtime_status": run_reference.runtime_status,
        "label_source": run_reference.label_source,
        "degradation_reason": run_reference.degradation_reason,
        "degraded_from": list(run_reference.degraded_from),
        "backend_real_active": run_reference.backend_real_active,
        "certification_allowed": run_reference.certification_allowed,
        "contributors": _jsonable(run_reference.contributors),
    }


def _deserialize_run_reference(
    payload: Mapping[str, Any] | None,
    result_blob: Any,
) -> RunReference | None:
    if payload is None:
        return None
    product_summary = payload.get("product_summary", {})
    if isinstance(product_summary, Mapping):
        product_summary = coating_summary_with_grounded_authority(product_summary)
    else:
        product_summary = {}
    return RunReference(
        status=str(payload["status"]),
        error_message=str(payload.get("error_message", "")),
        reason=str(payload.get("reason", "")),
        trace=result_blob,
        product_summary=product_summary,
        backend_name=(
            str(payload["backend_name"])
            if payload.get("backend_name") is not None
            else None
        ),
        backend_status=(
            str(payload["backend_status"])
            if payload.get("backend_status") is not None
            else None
        ),
        backend_authoritative=payload.get("backend_authoritative"),
        evidence_class=(
            str(payload["evidence_class"])
            if payload.get("evidence_class") is not None
            else None
        ),
        cache_state=(
            str(payload["cache_state"])
            if payload.get("cache_state") is not None
            else None
        ),
        runtime_status=(
            str(payload["runtime_status"])
            if payload.get("runtime_status") is not None
            else None
        ),
        label_source=(
            str(payload["label_source"])
            if payload.get("label_source") is not None
            else None
        ),
        degradation_reason=(
            str(payload["degradation_reason"])
            if payload.get("degradation_reason") is not None
            else None
        ),
        degraded_from=tuple(str(item) for item in payload.get("degraded_from", ())),
        backend_real_active=payload.get("backend_real_active"),
        certification_allowed=payload.get("certification_allowed"),
        contributors=tuple(
            dict(item) for item in payload.get("contributors", ())
        ),
    )


def _result_blob(scored_result: ScoredResult) -> Any:
    if hasattr(scored_result, "result_blob"):
        return _jsonable(getattr(scored_result, "result_blob"))
    if scored_result.run_reference is None:
        return None
    try:
        return _jsonable(scored_result.run_reference.trace)
    except (TypeError, ValueError):
        return _storage_run_reference_trace(scored_result.run_reference)


def _storage_run_reference_trace(run_reference: RunReference) -> dict[str, Any]:
    trace: dict[str, Any] = {}
    source_trace = (
        run_reference.trace
        if isinstance(run_reference.trace, Mapping)
        else {}
    )
    for key in (
        "vapor_pressure_source_report",
        "vapor_pressure_provider_id",
        "vapor_pressure_fallback_provider_id",
        "allow_fallback_vapor",
        "force_builtin_vapor_pressure",
        "builtin_fallback",
        "kernel_fallback_used",
    ):
        if key in source_trace:
            trace[key] = _jsonable(source_trace[key])
    if run_reference.backend_status is not None:
        trace["backend_status"] = run_reference.backend_status
    if run_reference.backend_authoritative is not None:
        trace["backend_authoritative"] = run_reference.backend_authoritative
    for key in (
        "backend_name",
        "evidence_class",
        "cache_state",
        "runtime_status",
        "label_source",
        "degradation_reason",
        "backend_real_active",
        "certification_allowed",
    ):
        value = getattr(run_reference, key)
        if value is not None:
            trace[key] = value
    if run_reference.degraded_from:
        trace["degraded_from"] = list(run_reference.degraded_from)
    if run_reference.contributors:
        trace["contributors"] = _jsonable(run_reference.contributors)
    return trace


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((_jsonable(item) for item in value), key=repr)
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        return str(value)
    return value


def _strict_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        if value in (0, 1):
            return bool(value)
        return False
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
        return False
    return False


def _canonical_json(value: Any) -> str:
    return canonical_json_dumps(normalize_canonical_value(value))


def _json_dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _json_load(value: str) -> Any:
    return json.loads(value)


def _json_number(value: float, label: str) -> float | str:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not numeric") from exc
    if math.isnan(numeric):
        raise ValueError(f"{label} is NaN")
    if math.isinf(numeric):
        return "+inf" if numeric > 0.0 else "-inf"
    return numeric


def _decode_json_number(value: Any, label: str) -> float:
    if value == "+inf":
        return math.inf
    if value == "-inf":
        return -math.inf
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not numeric") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"{label} is non-finite")
    return numeric


ResultsStore = ResultStore
