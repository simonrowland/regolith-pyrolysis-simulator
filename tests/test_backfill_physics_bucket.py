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
    # a/b share ALL physics inputs and differ only on the non-physics axes
    # (code_version / source_module_digest) -> they collapse into one bucket.
    # c differs by pressure, d differs only by setpoints digest -> since the
    # b-029 SC-37 fix, setpoints/feedstock digest drift PARTITIONS the physics
    # bucket (the old expectation that setpoints-a vs setpoints-b collapse was
    # the collision bug itself).
    keys = [
        _replay_key("candidate-a", setpoints_digest="setpoints-shared"),
        _replay_key("candidate-b", setpoints_digest="setpoints-shared"),
        _replay_key("candidate-c", setpoints_digest="setpoints-c", pressure=0.002),
        _replay_key("candidate-d", setpoints_digest="setpoints-d"),
    ]
    for key in keys:
        _insert_legacy_row(db_path, key)

    exact_before = _exact_columns(db_path)
    dry = backfill.run_backfill(db_path, dry_run=True)

    assert dry.total_rows == 4
    assert dry.distinct_physics_bucket_sha256 == 3
    assert dry.distinct_physics_bucket_h40_sha256 == 3
    assert dry.distinct_physics_bucket_h30_sha256 == 3
    assert dry.distinct_physics_bucket_h40c_sha256 == 3
    assert dry.distinct_physics_bucket_h30c_sha256 == 3
    assert dry.rows_needing_backfill == 4
    assert dry.rows_updated == 0
    assert _physics_columns(db_path) == set()

    real = backfill.run_backfill(db_path, dry_run=False)

    assert real.total_rows == 4
    assert real.distinct_physics_bucket_sha256 == 3
    assert real.distinct_physics_bucket_h40_sha256 == 3
    assert real.distinct_physics_bucket_h30_sha256 == 3
    assert real.distinct_physics_bucket_h40c_sha256 == 3
    assert real.distinct_physics_bucket_h30c_sha256 == 3
    assert real.rows_updated == 4
    assert real.rows_already_backfilled == 0
    assert _exact_columns(db_path) == exact_before
    assert _physics_columns(db_path) == set(backfill.PHYSICS_COLUMNS)
    assert _null_physics_column_count(db_path) == 0

    # Lock the axis contract directly: a/b (non-physics axes only) share a
    # bucket; c (pressure) and d (setpoints digest) each get their own.
    bucket_by_label = _bucket_sha_by_code_version(db_path)
    assert bucket_by_label["test-candidate-a"] == bucket_by_label["test-candidate-b"]
    assert bucket_by_label["test-candidate-c"] != bucket_by_label["test-candidate-a"]
    assert bucket_by_label["test-candidate-d"] != bucket_by_label["test-candidate-a"]
    assert bucket_by_label["test-candidate-d"] != bucket_by_label["test-candidate-c"]

    second = backfill.run_backfill(db_path, dry_run=False)

    assert second.total_rows == 4
    assert second.distinct_physics_bucket_sha256 == 3
    assert second.distinct_physics_bucket_h40_sha256 == 3
    assert second.distinct_physics_bucket_h30_sha256 == 3
    assert second.distinct_physics_bucket_h40c_sha256 == 3
    assert second.distinct_physics_bucket_h30c_sha256 == 3
    assert second.rows_updated == 0
    assert second.rows_already_backfilled == 4
    assert _exact_columns(db_path) == exact_before


def test_backfill_physics_bucket_skips_internal_analytical_rows_before_bucket_derivation(tmp_path):
    db_path = tmp_path / "legacy.db"
    _create_legacy_db(db_path)
    trusted = _replay_key("trusted", setpoints_digest="setpoints-trusted")
    analytical_row = _replay_key(
        "internal-analytical",
        setpoints_digest="setpoints-internal-analytical",
    )
    analytical_row["backend"] = {
        "backend_name": "StubBackend",
        "backend_class": "simulator.melt_backend.base.StubBackend",
        "corpus_version": current_corpus_version(),
    }
    _insert_legacy_row(db_path, trusted)
    _insert_legacy_row(db_path, analytical_row)

    stats = backfill.run_backfill(db_path, dry_run=False)

    assert stats.total_rows == 2
    assert stats.invalid_rows == 1
    assert stats.rows_updated == 1
    bucket_by_label = _bucket_sha_by_code_version(db_path)
    assert bucket_by_label["test-trusted"] is not None
    assert bucket_by_label["test-internal-analytical"] is None
    assert _null_physics_column_count(db_path) == 1


def _bucket_sha_by_code_version(db_path: Path) -> dict:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT key_bytes, physics_bucket_sha256 FROM {rrd.PT1_EQUILIBRIUM_TABLE}"
        ).fetchall()
    return {
        json.loads(key_bytes)["code_version"]: sha for key_bytes, sha in rows
    }


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
    payload = {
        "equilibrium_result": {"status": "ok"},
        "last_vapor_pressures_source": {
            "Na": "builtin_authoritative",
            "SiO": "builtin_authoritative",
        },
        "label": key["code_version"],
    }
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
