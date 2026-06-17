from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import simulator.reduced_real_determinism as rrd
from simulator.corpus_version import current_corpus_version


REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "backfill_physics_bucket",
    REPO_ROOT / "scripts" / "backfill_physics_bucket.py",
)
backfill = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = backfill
SPEC.loader.exec_module(backfill)


def test_backfill_physics_bucket_is_idempotent_additive_and_collapses(tmp_path):
    db_path = tmp_path / "legacy.db"
    _create_legacy_db(db_path)
    keys = [
        _replay_key("candidate-a", setpoints_digest="setpoints-a"),
        _replay_key("candidate-b", setpoints_digest="setpoints-b"),
        _replay_key("candidate-c", setpoints_digest="setpoints-c", pressure=0.002),
    ]
    for key in keys:
        _insert_legacy_row(db_path, key)

    exact_before = _exact_columns(db_path)
    dry = backfill.run_backfill(db_path, dry_run=True)

    assert dry.total_rows == 3
    assert dry.distinct_physics_bucket_sha256 == 2
    assert dry.distinct_physics_bucket_h40_sha256 == 2
    assert dry.distinct_physics_bucket_h30_sha256 == 2
    assert dry.distinct_physics_bucket_h40c_sha256 == 2
    assert dry.distinct_physics_bucket_h30c_sha256 == 2
    assert dry.rows_needing_backfill == 3
    assert dry.rows_updated == 0
    assert _physics_columns(db_path) == set()

    real = backfill.run_backfill(db_path, dry_run=False)

    assert real.total_rows == 3
    assert real.distinct_physics_bucket_sha256 == 2
    assert real.distinct_physics_bucket_h40_sha256 == 2
    assert real.distinct_physics_bucket_h30_sha256 == 2
    assert real.distinct_physics_bucket_h40c_sha256 == 2
    assert real.distinct_physics_bucket_h30c_sha256 == 2
    assert real.rows_updated == 3
    assert real.rows_already_backfilled == 0
    assert _exact_columns(db_path) == exact_before
    assert _physics_columns(db_path) == set(backfill.PHYSICS_COLUMNS)
    assert _null_physics_column_count(db_path) == 0

    second = backfill.run_backfill(db_path, dry_run=False)

    assert second.total_rows == 3
    assert second.distinct_physics_bucket_sha256 == 2
    assert second.distinct_physics_bucket_h40_sha256 == 2
    assert second.distinct_physics_bucket_h30_sha256 == 2
    assert second.distinct_physics_bucket_h40c_sha256 == 2
    assert second.distinct_physics_bucket_h30c_sha256 == 2
    assert second.rows_updated == 0
    assert second.rows_already_backfilled == 3
    assert _exact_columns(db_path) == exact_before


def _create_legacy_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"""
            CREATE TABLE {rrd.PT1_EQUILIBRIUM_TABLE} (
                key_hash TEXT PRIMARY KEY,
                artifact TEXT NOT NULL,
                store_schema_version TEXT NOT NULL,
                request_schema_version TEXT NOT NULL,
                key_sha256 TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                key_bytes BLOB NOT NULL,
                payload_bytes BLOB NOT NULL,
                code_version TEXT NOT NULL,
                corpus_version TEXT,
                engine_version TEXT,
                data_digests_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                git_dirty INTEGER NOT NULL
            )
            """
        )


def _replay_key(
    label: str,
    *,
    setpoints_digest: str,
    pressure: float = 0.001,
) -> dict:
    return {
        "schema_version": rrd.SCHEMA_VERSION,
        "artifact": "equilibrium_result",
        "intent": "silicate_liquidus",
        "code_version": f"test-{label}",
        "corpus_version": current_corpus_version(),
        "backend": {
            "backend_name": "AlphaMELTSBackend",
            "backend_class": "simulator.melt_backend.alphamelts.AlphaMELTSBackend",
            "corpus_version": current_corpus_version(),
        },
        "provider": {"resolved_provider_id": "magemin-shadow"},
        "data_digests": {
            "setpoints": setpoints_digest,
            "source_module_digest": f"source-{label}",
            "species_formula_registry": "species-v1",
            "vapor_pressures": "vapor-v1",
        },
        "composition_mol_fraction": [
            {"species": "SiO2", "mol_fraction": 0.6},
            {"species": "FeO", "mol_fraction": 0.4},
        ],
        "controls": {"T_K": 1500.0, "pressure_bar": pressure},
        "sulfur_side": {"S_input_ppm": 0.0},
    }


def _insert_legacy_row(db_path: Path, key: dict) -> None:
    payload = {"equilibrium_result": {"status": "ok"}, "label": key["code_version"]}
    key_bytes = rrd.canonical_json_bytes(key)
    payload_bytes = rrd.canonical_json_bytes(payload)
    key_hash = hashlib.sha256(key_bytes).hexdigest()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"""
            INSERT INTO {rrd.PT1_EQUILIBRIUM_TABLE} (
                key_hash,
                artifact,
                store_schema_version,
                request_schema_version,
                key_sha256,
                payload_sha256,
                key_bytes,
                payload_bytes,
                code_version,
                corpus_version,
                engine_version,
                data_digests_json,
                created_at,
                git_dirty
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key_hash,
                key["artifact"],
                rrd.PT1_STORE_SCHEMA_VERSION,
                key["schema_version"],
                key_hash,
                hashlib.sha256(payload_bytes).hexdigest(),
                sqlite3.Binary(key_bytes),
                sqlite3.Binary(payload_bytes),
                key["code_version"],
                key["corpus_version"],
                "engine-v1",
                json.dumps(
                    key["data_digests"],
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "2026-06-13T00:00:00Z",
                0,
            ),
        )


def _physics_columns(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {
            row[1]
            for row in conn.execute(
                f"PRAGMA table_info({rrd.PT1_EQUILIBRIUM_TABLE})"
            )
            if row[1] in backfill.PHYSICS_COLUMNS
        }


def _exact_columns(db_path: Path) -> list[tuple]:
    with sqlite3.connect(db_path) as conn:
        return list(
            conn.execute(
                f"""
                SELECT
                    key_hash,
                    key_sha256,
                    payload_sha256,
                    key_bytes,
                    payload_bytes
                FROM {rrd.PT1_EQUILIBRIUM_TABLE}
                ORDER BY key_hash
                """
            )
        )


def _null_physics_column_count(db_path: Path) -> int:
    null_predicate = " OR ".join(
        f"{column} IS NULL"
        for column in backfill.PHYSICS_COLUMNS
    )
    with sqlite3.connect(db_path) as conn:
        (count,) = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM {rrd.PT1_EQUILIBRIUM_TABLE}
            WHERE {null_predicate}
            """
        ).fetchone()
        return int(count)
