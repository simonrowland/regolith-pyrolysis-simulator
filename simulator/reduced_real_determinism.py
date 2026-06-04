"""PT-0 reduced-real determinism proof helpers.

Opt-in only. This module builds canonical request keys and a minimal
in-memory write-through/replay store for the determinism proof gate.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import math
import sqlite3
import subprocess
from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any

from simulator.chemistry.kernel import ChemistryIntent
from simulator.melt_backend.base import EquilibriumResult
from simulator.melt_backend.sulfsat import SulfurSaturationResult


SCHEMA_VERSION = "pt0-reduced-real-determinism-v1"
PT1_STORE_SCHEMA_VERSION = "pt1-reduced-real-equilibrium-store-v1"
PT1_EQUILIBRIUM_TABLE = "reduced_real_equilibrium_payloads"
PT1_METADATA_TABLE = "reduced_real_metadata"
CACHE_STATES = ("cached_exact", "cached_interpolated", "live_fill")
_CLEANED_MELT_ACCOUNT = "process.cleaned_melt"
_T_K_QUANTUM = 0.01
_FO2_LOG_QUANTUM = 0.001
_PRESSURE_BAR_QUANTUM = 0.00001
_COMPOSITION_SIG_FIGS = 5
_TRACE_CUTOFF = 1.0e-12


class PT0CacheMiss(RuntimeError):
    """Replay requested a key that the write-through run did not capture."""


class PT0CacheCollision(RuntimeError):
    """One canonical key produced different payload bytes."""


class PT1PersistentStoreCorrupt(PT0CacheCollision):
    """Persistent PT-1 row failed verify-on-hit integrity checks."""


class PT0DeterminismStore:
    """PT-0 capture/replay store, optionally backed by the PT-1 SQLite DB."""

    def __init__(
        self,
        mode: str = "capture",
        *,
        db_path: str | Path | None = None,
    ) -> None:
        if mode not in {"capture", "replay"}:
            raise ValueError("PT0DeterminismStore mode must be capture or replay")
        self.mode = mode
        self.persistent_path = Path(db_path) if db_path is not None else None
        self.persistent_store = (
            PT1PersistentEquilibriumStore(self.persistent_path)
            if self.persistent_path is not None
            else None
        )
        self.entries: dict[str, dict[str, Any]] = {}
        self.capture_sequence: list[dict[str, Any]] = []
        self.replay_sequence: list[dict[str, Any]] = []
        self.misses: list[dict[str, Any]] = []
        self.hits: int = 0
        self.live_fills: int = 0
        self.quantize_live_controls: bool = True

    @property
    def capture_enabled(self) -> bool:
        return self.mode == "capture"

    @property
    def replay_enabled(self) -> bool:
        return self.mode == "replay"

    def clone_for_replay(self) -> "PT0DeterminismStore":
        clone = PT0DeterminismStore("replay", db_path=self.persistent_path)
        clone.entries = copy.deepcopy(self.entries)
        clone.capture_sequence = copy.deepcopy(self.capture_sequence)
        clone.quantize_live_controls = self.quantize_live_controls
        return clone

    def quantized_controls(
        self,
        sim: Any,
        *,
        fO2_log: float | None,
    ) -> dict[str, float | None]:
        T_K = _quantize(
            float(sim.melt.temperature_C) + 273.15,
            _T_K_QUANTUM,
            2,
        )
        pressure_bar = _quantize(
            float(sim.melt.p_total_mbar) / 1000.0,
            _PRESSURE_BAR_QUANTUM,
            8,
        )
        if fO2_log is None:
            fO2_log = sim._compute_intrinsic_melt_fO2(T_K)
        return {
            "temperature_C": None if T_K is None else float(T_K) - 273.15,
            "pressure_bar": pressure_bar,
            "fO2_log": _quantize(fO2_log, _FO2_LOG_QUANTUM, 6),
        }

    def quantized_pO2_bar(self, sim: Any) -> float:
        return _sigfig(float(sim._commanded_pO2_bar()), _COMPOSITION_SIG_FIGS)

    def canonical_composition_mol_by_account(
        self,
        sim: Any,
        composition_by_account: Mapping[str, Mapping[str, float]] | None = None,
    ) -> dict[str, dict[str, float]]:
        if composition_by_account is None:
            composition_by_account = sim.atom_ledger.mol_by_account()
        return canonicalized_composition_mol_by_account(composition_by_account)

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

    def capture_equilibrium(self, sim: Any, result: EquilibriumResult) -> None:
        key = canonical_replay_key(
            sim,
            artifact="equilibrium_post_record",
            intent=ChemistryIntent.BACKEND_EQUILIBRIUM,
            fO2_log=sim._compute_intrinsic_melt_fO2(),
            fe_redox_policy="intrinsic",
        )
        payload = equilibrium_payload(sim, result)
        self._store("equilibrium_post_record", key, payload)

    def replay_equilibrium(self, sim: Any) -> EquilibriumResult:
        key = canonical_replay_key(
            sim,
            artifact="equilibrium_post_record",
            intent=ChemistryIntent.BACKEND_EQUILIBRIUM,
            fO2_log=sim._compute_intrinsic_melt_fO2(),
            fe_redox_policy="intrinsic",
        )
        payload = self._lookup("equilibrium_post_record", key)
        result = equilibrium_from_payload(payload)
        sim._last_backend_status = getattr(result, "status", "ok")
        sim._last_vapor_pressures_source = dict(
            payload.get("last_vapor_pressures_source") or {}
        )
        sim._last_vapor_pressure_diagnostic = dict(
            payload.get("last_vapor_pressure_diagnostic") or {}
        )
        sulfur = getattr(result, "sulfur_saturation", None)
        sim._last_sulfur_saturation_result = sulfur
        if getattr(result, "fO2_log", None) is not None:
            sim.melt.fO2_log = float(result.fO2_log)
        return result

    def capture_gate_curve(
        self,
        sim: Any,
        *,
        fO2_log: float,
        curve: Mapping[str, Any],
    ) -> None:
        key = canonical_replay_key(
            sim,
            artifact="freeze_gate_curve",
            intent=ChemistryIntent.GATE_LIQUID_FRACTION,
            fO2_log=fO2_log,
            fe_redox_policy="intrinsic",
        )
        self._store("freeze_gate_curve", key, {"curve": _curve_payload(curve)})

    def replay_gate_curve(self, sim: Any, *, fO2_log: float) -> dict[str, Any]:
        key = canonical_replay_key(
            sim,
            artifact="freeze_gate_curve",
            intent=ChemistryIntent.GATE_LIQUID_FRACTION,
            fO2_log=fO2_log,
            fe_redox_policy="intrinsic",
        )
        payload = self._lookup("freeze_gate_curve", key)
        return _curve_from_payload(payload["curve"])

    def summary(self) -> dict[str, Any]:
        by_artifact = Counter(
            record["artifact"] for record in self.capture_sequence
        )
        return {
            "mode": self.mode,
            "entries": len(self.entries),
            "capture_calls": len(self.capture_sequence),
            "replay_calls": len(self.replay_sequence),
            "hits": self.hits,
            "misses": len(self.misses),
            "live_fills": self.live_fills,
            "cache_states": CACHE_STATES,
            "capture_calls_by_artifact": dict(sorted(by_artifact.items())),
            "key_drift_histogram": self.key_drift_histogram(),
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

    def key_drift_histogram(self) -> dict[str, int]:
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
    ) -> None:
        key_bytes = canonical_json_bytes(key)
        payload_bytes = canonical_json_bytes(payload)
        key_hash = _sha256(key_bytes)
        payload_hash = _sha256(payload_bytes)
        self.capture_sequence.append(
            {"artifact": artifact, "key": copy.deepcopy(dict(key)), "hash": key_hash}
        )
        existing = self.entries.get(key_hash)
        if existing is not None:
            if existing["payload_hash"] != payload_hash:
                raise PT0CacheCollision(
                    f"PT-0 {artifact} key collision with different payload: "
                    f"{key_hash}"
                )
            self._verify_entry(artifact, key, key_bytes, key_hash, existing)
            if self.persistent_store is not None:
                self.persistent_store.put(
                    artifact=artifact,
                    key=key,
                    key_bytes=key_bytes,
                    key_hash=key_hash,
                    payload=payload,
                    payload_bytes=payload_bytes,
                    payload_hash=payload_hash,
                )
            return
        if self.persistent_store is not None:
            self.persistent_store.put(
                artifact=artifact,
                key=key,
                key_bytes=key_bytes,
                key_hash=key_hash,
                payload=payload,
                payload_bytes=payload_bytes,
                payload_hash=payload_hash,
            )
        self.entries[key_hash] = {
            "artifact": artifact,
            "key": copy.deepcopy(dict(key)),
            "key_hash": key_hash,
            "key_bytes": key_bytes.decode("utf-8"),
            "payload": copy.deepcopy(dict(payload)),
            "payload_hash": payload_hash,
            "cache_state": "live_fill",
        }
        self.live_fills += 1

    def _lookup(self, artifact: str, key: Mapping[str, Any]) -> dict[str, Any]:
        key_bytes = canonical_json_bytes(key)
        key_hash = _sha256(key_bytes)
        self.replay_sequence.append(
            {"artifact": artifact, "key": copy.deepcopy(dict(key)), "hash": key_hash}
        )
        entry = self.entries.get(key_hash)
        if entry is None and self.persistent_store is not None:
            entry = self.persistent_store.get(
                artifact=artifact,
                key=key,
                key_bytes=key_bytes,
                key_hash=key_hash,
            )
            if entry is not None:
                self.entries[key_hash] = copy.deepcopy(entry)
        if entry is None:
            miss = {
                "artifact": artifact,
                "key_hash": key_hash,
                "sequence_index": len(self.replay_sequence) - 1,
                "drift_fields": self._drift_fields_for_latest_replay(),
            }
            self.misses.append(miss)
            raise PT0CacheMiss(f"PT-0 cached replay miss: {miss}")
        self._verify_entry(artifact, key, key_bytes, key_hash, entry)
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
        self.replay_sequence[-1]["cache_state"] = "cached_exact"
        return copy.deepcopy(entry["payload"])

    def _verify_entry(
        self,
        artifact: str,
        key: Mapping[str, Any],
        key_bytes: bytes,
        key_hash: str,
        entry: Mapping[str, Any],
    ) -> None:
        if entry.get("artifact") != artifact:
            raise PT0CacheCollision(
                f"PT-0 stored artifact mismatch for {artifact}: {key_hash}"
            )
        if entry.get("key_hash") not in {None, key_hash}:
            raise PT0CacheCollision(
                f"PT-0 stored key hash mismatch for {artifact}: {key_hash}"
            )
        stored_key_bytes = entry.get("key_bytes")
        if isinstance(stored_key_bytes, str):
            stored_key_bytes = stored_key_bytes.encode("utf-8")
        if stored_key_bytes is None:
            stored_key_bytes = canonical_json_bytes(entry["key"])
        if stored_key_bytes != key_bytes or canonical_json_bytes(key) != key_bytes:
            raise PT0CacheCollision(
                f"PT-0 stored key bytes mismatch for {artifact}: {key_hash}"
            )
        payload_bytes = canonical_json_bytes(entry["payload"])
        if entry.get("payload_hash") != _sha256(payload_bytes):
            raise PT0CacheCollision(
                f"PT-0 stored payload bytes mismatch for {artifact}: {key_hash}"
            )

    def _drift_fields_for_latest_replay(self) -> list[str]:
        index = len(self.replay_sequence) - 1
        if index >= len(self.capture_sequence):
            return ["<extra_replay_call>"]
        return _diff_top_fields(
            self.capture_sequence[index]["key"],
            self.replay_sequence[index]["key"],
        )


class PT1PersistentEquilibriumStore:
    """Content-addressed SQLite store for PT-0 exact reduced-real payloads."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            self._initialize(conn)

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
    ) -> None:
        with self._connect() as conn:
            self._initialize(conn)
            existing = self._fetch(conn, key_hash)
            if existing is not None:
                entry = self._entry_from_row(
                    existing,
                    artifact=artifact,
                    key=key,
                    key_bytes=key_bytes,
                    key_hash=key_hash,
                )
                if (
                    entry["payload_hash"] != payload_hash
                    or canonical_json_bytes(entry["payload"]) != payload_bytes
                ):
                    raise PT1PersistentStoreCorrupt(
                        f"PT-1 payload collision for {artifact}: {key_hash}"
                    )
                return
            conn.execute(
                f"""
                INSERT INTO {PT1_EQUILIBRIUM_TABLE} (
                    key_hash,
                    artifact,
                    store_schema_version,
                    request_schema_version,
                    key_sha256,
                    payload_sha256,
                    key_bytes,
                    payload_bytes,
                    code_version,
                    engine_version,
                    data_digests_json,
                    created_at,
                    git_dirty
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key_hash,
                    artifact,
                    PT1_STORE_SCHEMA_VERSION,
                    str(key.get("schema_version")),
                    key_hash,
                    payload_hash,
                    sqlite3.Binary(key_bytes),
                    sqlite3.Binary(payload_bytes),
                    str(key.get("code_version")),
                    _none_or_str(key.get("engine_version")),
                    canonical_json_bytes(key.get("data_digests", {})).decode("utf-8"),
                    datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    _git_dirty(),
                ),
            )

    def get(
        self,
        *,
        artifact: str,
        key: Mapping[str, Any],
        key_bytes: bytes,
        key_hash: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            self._initialize(conn)
            row = self._fetch(conn, key_hash)
            if row is None:
                return None
            return self._entry_from_row(
                row,
                artifact=artifact,
                key=key,
                key_bytes=key_bytes,
                key_hash=key_hash,
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

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
                key_bytes BLOB NOT NULL,
                payload_bytes BLOB NOT NULL,
                code_version TEXT NOT NULL,
                engine_version TEXT,
                data_digests_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                git_dirty INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{PT1_EQUILIBRIUM_TABLE}_artifact
            ON {PT1_EQUILIBRIUM_TABLE}(artifact)
            """
        )
        metadata = conn.execute(
            f"SELECT value FROM {PT1_METADATA_TABLE} WHERE key = ?",
            ("store_schema_version",),
        ).fetchone()
        if metadata is not None and metadata["value"] != PT1_STORE_SCHEMA_VERSION:
            raise PT1PersistentStoreCorrupt(
                "PT-1 persistent store schema version drift: "
                f"{metadata['value']} != {PT1_STORE_SCHEMA_VERSION}"
            )
        conn.execute(
            f"""
            INSERT OR IGNORE INTO {PT1_METADATA_TABLE} (key, value)
            VALUES (?, ?)
            """,
            ("store_schema_version", PT1_STORE_SCHEMA_VERSION),
        )

    def _fetch(
        self,
        conn: sqlite3.Connection,
        key_hash: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
            f"""
            SELECT
                key_hash,
                artifact,
                store_schema_version,
                request_schema_version,
                key_sha256,
                payload_sha256,
                key_bytes,
                payload_bytes,
                code_version,
                engine_version,
                data_digests_json
            FROM {PT1_EQUILIBRIUM_TABLE}
            WHERE key_hash = ?
            """,
            (key_hash,),
        ).fetchone()

    def _entry_from_row(
        self,
        row: sqlite3.Row,
        *,
        artifact: str,
        key: Mapping[str, Any],
        key_bytes: bytes,
        key_hash: str,
    ) -> dict[str, Any]:
        row_key_bytes = _sqlite_bytes(row["key_bytes"])
        row_payload_bytes = _sqlite_bytes(row["payload_bytes"])
        if row["store_schema_version"] != PT1_STORE_SCHEMA_VERSION:
            raise PT1PersistentStoreCorrupt(
                "PT-1 row store schema version drift: "
                f"{row['store_schema_version']} != {PT1_STORE_SCHEMA_VERSION}"
            )
        if row["request_schema_version"] != str(key.get("schema_version")):
            raise PT1PersistentStoreCorrupt(
                "PT-1 row request schema version drift: "
                f"{row['request_schema_version']} != {key.get('schema_version')}"
            )
        if row["artifact"] != artifact:
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row artifact mismatch for {artifact}: {key_hash}"
            )
        if row["key_hash"] != key_hash or row["key_sha256"] != key_hash:
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row key hash mismatch for {artifact}: {key_hash}"
            )
        if row_key_bytes != key_bytes:
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row canonical request bytes mismatch: {key_hash}"
            )
        if _sha256(row_payload_bytes) != row["payload_sha256"]:
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row payload bytes mismatch: {key_hash}"
            )
        row_key = json.loads(row_key_bytes.decode("utf-8"))
        row_payload = json.loads(row_payload_bytes.decode("utf-8"))
        if canonical_json_bytes(row_key) != row_key_bytes:
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row non-canonical request bytes: {key_hash}"
            )
        if canonical_json_bytes(row_payload) != row_payload_bytes:
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row non-canonical payload bytes: {key_hash}"
            )
        if row_key != _json_ready(key):
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row canonical request mismatch: {key_hash}"
            )
        if row["code_version"] != str(key.get("code_version")):
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row code VERSION drift: {key_hash}"
            )
        if _none_or_str(key.get("engine_version")) != row["engine_version"]:
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row engine version drift: {key_hash}"
            )
        data_digests_json = canonical_json_bytes(
            key.get("data_digests", {})
        ).decode("utf-8")
        if row["data_digests_json"] != data_digests_json:
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row data digest drift: {key_hash}"
            )
        return {
            "artifact": artifact,
            "key": copy.deepcopy(dict(row_key)),
            "key_hash": key_hash,
            "key_bytes": row_key_bytes.decode("utf-8"),
            "payload": copy.deepcopy(dict(row_payload)),
            "payload_hash": row["payload_sha256"],
            "cache_state": "live_fill",
        }


def canonical_replay_key(
    sim: Any,
    *,
    artifact: str,
    intent: ChemistryIntent,
    fO2_log: float | None,
    fe_redox_policy: str,
) -> dict[str, Any]:
    if intent == ChemistryIntent.GATE_LIQUID_FRACTION:
        register_gate = getattr(
            sim, "_register_freeze_gate_liquid_fraction_providers", None
        )
        if callable(register_gate):
            register_gate()
    T_K = _quantize(float(sim.melt.temperature_C) + 273.15, _T_K_QUANTUM, 2)
    pressure_bar = _quantize(
        float(sim.melt.p_total_mbar) / 1000.0,
        _PRESSURE_BAR_QUANTUM,
        8,
    )
    if fO2_log is None:
        fO2_log = sim._compute_intrinsic_melt_fO2(T_K)
    sulfur_inventory = {
        "salt_phase": _positive_float_map(
            getattr(sim.inventory, "salt_phase_kg", {}) or {}
        ),
        "sulfide_matte": _positive_float_map(
            getattr(sim.inventory, "sulfide_matte_kg", {}) or {}
        ),
    }
    provider = _provider_identity(sim, intent)
    vapor_provider = _provider_identity(sim, ChemistryIntent.VAPOR_PRESSURE)
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact": str(artifact),
        "intent": intent.value,
        "composition_mol_fraction": _composition_mol_fraction(sim),
        "controls": {
            "T_K": T_K,
            "log_fO2": _quantize(fO2_log, _FO2_LOG_QUANTUM, 6),
            "pressure_bar": pressure_bar,
            "pO2_bar": _sigfig(
                float(sim._commanded_pO2_bar()), _COMPOSITION_SIG_FIGS
            ),
        },
        "redox": {
            "fe_redox_policy": str(fe_redox_policy),
            "fe_split": _fe_split(sim),
        },
        "backend": {
            "backend_name": type(getattr(sim, "backend", None)).__name__,
            "backend_class": _qualified_type(getattr(sim, "backend", None)),
        },
        "provider": provider,
        "vapor_pressure_provider": vapor_provider,
        "sulfur_side": {
            "S_input_ppm": _sigfig(sim._stage0_sulfur_input_ppm(), 6),
            "stage0_inventory_digest": _digest(sulfur_inventory),
            **_sulfsat_identity(getattr(sim, "_sulfsat_gate", None)),
        },
        "model": {
            "model": provider.get("model"),
            "mode": provider.get("mode"),
        },
        "data_digests": _data_digests(sim),
        "code_version": _code_version(),
        "engine_version": provider.get("engine_version"),
    }


def equilibrium_payload(sim: Any, result: EquilibriumResult) -> dict[str, Any]:
    transition = getattr(result, "ledger_transition", None)
    if transition is not None:
        raise PT0CacheCollision(
            "PT-0 equilibrium replay does not support cached ledger transitions"
        )
    payload = {
        "equilibrium_result": {
            field.name: _json_ready(getattr(result, field.name))
            for field in dataclasses.fields(EquilibriumResult)
            if field.name != "ledger_transition"
        },
        "last_vapor_pressures_source": dict(
            getattr(sim, "_last_vapor_pressures_source", {}) or {}
        ),
        "last_vapor_pressure_diagnostic": _json_ready(
            getattr(sim, "_last_vapor_pressure_diagnostic", {}) or {}
        ),
    }
    sulfur = getattr(result, "sulfur_saturation", None)
    payload["equilibrium_result"]["sulfur_saturation"] = _json_ready(sulfur)
    if hasattr(result, "alphamelts_diagnostics"):
        payload["alphamelts_diagnostics"] = _json_ready(
            getattr(result, "alphamelts_diagnostics")
        )
    return payload


def equilibrium_from_payload(payload: Mapping[str, Any]) -> EquilibriumResult:
    data = dict(payload["equilibrium_result"])
    sulfur_data = data.pop("sulfur_saturation", None)
    data["ledger_transition"] = None
    result = EquilibriumResult(**data)
    if sulfur_data is not None:
        result.sulfur_saturation = SulfurSaturationResult(**dict(sulfur_data))
    if "alphamelts_diagnostics" in payload:
        setattr(result, "alphamelts_diagnostics", payload["alphamelts_diagnostics"])
    return result


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _json_ready(value),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _curve_payload(curve: Mapping[str, Any]) -> dict[str, Any]:
    return {
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


def _curve_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source": str(payload["source"]),
        "solidus_T_C": float(payload["solidus_T_C"]),
        "liquidus_T_C": float(payload["liquidus_T_C"]),
        "path": tuple(
            (float(point["temperature_C"]), float(point["liquid_fraction"]))
            for point in payload.get("path", ())
        ),
    }


def _composition_mol_fraction(sim: Any) -> list[tuple[str, float]]:
    cleaned = sim.atom_ledger.mol_by_account(_CLEANED_MELT_ACCOUNT)
    return _composition_mol_fraction_from_mol(cleaned)


def _composition_mol_fraction_from_mol(
    cleaned: Mapping[str, float],
) -> list[tuple[str, float]]:
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
        result.append((species, _sigfig(fraction, _COMPOSITION_SIG_FIGS)))
    return result


def canonicalized_composition_mol_by_account(
    composition_by_account: Mapping[str, Mapping[str, float]],
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
    fractions = _composition_mol_fraction_from_mol(cleaned)
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


def _fe_split(sim: Any) -> dict[str, float]:
    fractions = dict(_composition_mol_fraction(sim))
    return {
        "FeO": fractions.get("FeO", 0.0),
        "Fe2O3": fractions.get("Fe2O3", 0.0),
    }


def _provider_identity(sim: Any, intent: ChemistryIntent) -> dict[str, Any]:
    registry = getattr(sim, "_chem_registry", None)
    auth = registry.authoritative_for(intent) if registry is not None else None
    fallback = registry.fallback_for(intent) if registry is not None else None
    fallback_allowed = False
    kernel = getattr(sim, "_chem_kernel", None)
    if kernel is not None:
        fallback_allowed = intent in getattr(kernel, "allow_fallback_intents", ())
    resolved = auth
    role = "authoritative"
    if resolved is None and fallback is not None and fallback_allowed:
        resolved = fallback
        role = "fallback"
    return {
        "resolved_provider_id": _provider_id(resolved),
        "resolved_role": role if resolved is not None else "none",
        "authoritative_provider_id": _provider_id(auth),
        "fallback_provider_id": _provider_id(fallback),
        "fallback_allowed": bool(fallback_allowed),
        "model": _provider_model(resolved),
        "mode": _provider_mode(resolved),
        "engine_version": _provider_engine_version(resolved),
    }


def _provider_id(provider: Any) -> str | None:
    if provider is None:
        return None
    profile = provider.capability_profile()
    return str(profile.provider_id)


def _provider_model(provider: Any) -> str | None:
    if provider is None:
        return None
    return str(getattr(provider, "name", type(provider).__name__))


def _provider_mode(provider: Any) -> str | None:
    if provider is None:
        return None
    backend = getattr(provider, "_backend", None)
    mode = getattr(backend, "_bridge", None)
    if mode is not None:
        return str(mode)
    if _provider_id(provider) == "magemin-shadow":
        return "subprocess"
    return str(type(provider).__name__)


def _provider_engine_version(provider: Any) -> str | None:
    if provider is None:
        return None
    getter = getattr(provider, "_engine_version", None)
    if callable(getter):
        try:
            return str(getter())
        except Exception:  # noqa: BLE001 - diagnostic only
            return "unavailable"
    backend = getattr(provider, "_backend", None)
    getter = getattr(backend, "get_engine_version", None)
    if callable(getter):
        try:
            return str(getter())
        except Exception:  # noqa: BLE001 - diagnostic only
            return "unavailable"
    return "unavailable"


def _sulfsat_identity(gate: Any) -> dict[str, Any]:
    return {
        "sulfsat_provider": type(gate).__name__,
        "sulfsat_available": _sulfsat_available(gate),
        "sulfsat_package_version": _sulfsat_package_version(gate),
        "sulfsat_calibration_version": _sulfsat_calibration_version(gate),
    }


def _sulfsat_available(gate: Any) -> bool:
    checker = getattr(gate, "is_available", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:  # noqa: BLE001 - diagnostic only
            return False
    return False


def _sulfsat_package_version(gate: Any) -> str:
    getter = getattr(gate, "package_version", None)
    if callable(getter):
        try:
            return str(getter())
        except Exception:  # noqa: BLE001 - diagnostic only
            return "unavailable"
    module = getattr(gate, "_module", None)
    version = getattr(module, "__version__", None)
    if version is not None:
        return str(version)
    return "unavailable"


def _sulfsat_calibration_version(gate: Any) -> str:
    getter = getattr(gate, "calibration_version", None)
    if callable(getter):
        try:
            return str(getter())
        except Exception:  # noqa: BLE001 - diagnostic only
            return "unavailable"
    return "unavailable"


def _data_digests(sim: Any) -> dict[str, str]:
    return {
        "setpoints": _digest(getattr(sim, "setpoints", {})),
        "feedstocks": _digest(getattr(sim, "feedstocks", {})),
        "vapor_pressures": _digest(getattr(sim, "vapor_pressures", {})),
        "species_formula_registry": _digest(
            getattr(sim, "species_formula_registry", {})
        ),
    }


def _code_version() -> str:
    root = Path(__file__).resolve().parent.parent
    path = root / "VERSION"
    if path.exists():
        return path.read_text().strip()
    return "unknown"


def _git_dirty() -> int:
    root = Path(__file__).resolve().parent.parent
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


def _none_or_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _sqlite_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, str):
        return value.encode("utf-8")
    return bytes(value)


def _qualified_type(value: Any) -> str:
    if value is None:
        return "None"
    cls = type(value)
    return f"{cls.__module__}.{cls.__qualname__}"


def _positive_float_map(value: Mapping[str, Any]) -> dict[str, float]:
    return {
        str(key): float(item)
        for key, item in dict(value or {}).items()
        if item is not None and float(item) > 0.0
    }


def _json_ready(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _json_ready(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite value in PT-0 payload: {value!r}")
        return value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return repr(value)


def _digest(value: Any) -> str:
    return _sha256(canonical_json_bytes(value))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def _diff_top_fields(left: Mapping[str, Any], right: Mapping[str, Any]) -> list[str]:
    fields = []
    keys = sorted(set(left) | set(right))
    for key in keys:
        if _json_ready(left.get(key)) != _json_ready(right.get(key)):
            fields.append(str(key))
    return fields
