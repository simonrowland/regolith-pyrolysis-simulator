import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from simulator import corpus_version
from simulator import reduced_real_determinism as rrd
from scripts import rekey_cache_engine_identity as rekey
from tests.cache_rekey_fixtures import put_rekey_gate_row
from tests.pt1_index_oracle import independent_ladder_distance


@pytest.fixture
def target_corpus_version(monkeypatch) -> str:
    target = "target"
    monkeypatch.setattr(
        corpus_version,
        "interoperable_corpus_versions",
        lambda: (target,),
    )
    return target


def _write_rekeyable_rows(
    db_path: Path,
    engine_versions: tuple[str, ...] = ("one", "two"),
) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        f"""
        CREATE TABLE {rekey.PT1_EQUILIBRIUM_TABLE} (
            key_hash TEXT PRIMARY KEY,
            key_sha256 TEXT,
            key_bytes BLOB,
            engine_version TEXT,
            corpus_version TEXT
        )
        """
    )
    for engine_version in engine_versions:
        key_bytes = rekey.canonical_json_bytes(
            {
                "backend": {"backend_name": "alphamelts"},
                "engine_version": engine_version,
            }
        )
        key_hash = hashlib.sha256(key_bytes).hexdigest()
        conn.execute(
            f"INSERT INTO {rekey.PT1_EQUILIBRIUM_TABLE} VALUES (?, ?, ?, ?, ?)",
            (key_hash, key_hash, key_bytes, engine_version, None),
        )
    conn.commit()
    conn.close()


def test_failed_rekey_retry_reuses_content_addressed_backup(
    tmp_path: Path,
    target_corpus_version: str,
) -> None:
    db_path = tmp_path / "cache.db"
    _write_rekeyable_rows(db_path)

    for _attempt in range(2):
        with pytest.raises(sqlite3.IntegrityError):
            rekey.rekey_cache(
                db_path,
                target_corpus_version=target_corpus_version,
            )
        assert len(list(tmp_path.glob("cache.db.backup-*"))) == 1

    conn = sqlite3.connect(db_path)
    try:
        unchanged = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM {rekey.PT1_EQUILIBRIUM_TABLE}
            WHERE corpus_version IS NULL
            """
        ).fetchone()[0]
    finally:
        conn.close()
    assert unchanged == 2


def test_rekey_locks_identity_decision_before_backup(
    tmp_path: Path,
    monkeypatch,
    target_corpus_version: str,
) -> None:
    db_path = tmp_path / "cache.db"
    _write_rekeyable_rows(db_path, ("one",))
    original_backup = rekey._backup_db
    race_outcomes: list[str] = []

    def backup_with_racing_identity_update(
        conn: sqlite3.Connection,
        path: Path,
    ) -> tuple[Path, int]:
        raced_key_bytes = rekey.canonical_json_bytes(
            {
                "backend": {
                    "backend_name": "alphamelts",
                    "corpus_version": target_corpus_version,
                },
                "corpus_version": target_corpus_version,
            }
        )
        raced_key_hash = hashlib.sha256(raced_key_bytes).hexdigest()
        racer = sqlite3.connect(path, timeout=0.0)
        try:
            racer.execute(
                f"""
                UPDATE {rekey.PT1_EQUILIBRIUM_TABLE}
                SET key_hash = ?, key_sha256 = ?, key_bytes = ?,
                    engine_version = NULL, corpus_version = ?
                """,
                (
                    raced_key_hash,
                    raced_key_hash,
                    raced_key_bytes,
                    target_corpus_version,
                ),
            )
            racer.commit()
        except sqlite3.OperationalError as exc:
            assert "locked" in str(exc).lower()
            race_outcomes.append("locked")
        else:
            race_outcomes.append("committed")
        finally:
            racer.close()
        return original_backup(conn, path)

    monkeypatch.setattr(rekey, "_backup_db", backup_with_racing_identity_update)

    result = rekey.rekey_cache(
        db_path,
        target_corpus_version=target_corpus_version,
    )

    assert race_outcomes == ["locked"]
    assert result.rows_before == 1
    assert result.rows_updated == 1
    assert result.backup_path is not None
    with sqlite3.connect(result.backup_path) as backup_conn:
        (backup_key_bytes,) = backup_conn.execute(
            f"SELECT key_bytes FROM {rekey.PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()
    assert rekey._json_loads(bytes(backup_key_bytes))["engine_version"] == "one"


def test_rekey_deep_stored_key_is_typed_corruption(
    tmp_path: Path,
    target_corpus_version: str,
) -> None:
    db_path = tmp_path / "deep-key-cache.db"
    original_hash, _payload_bytes = put_rekey_gate_row(
        db_path,
        {
            "backend": {"backend_name": "alphamelts"},
            "engine_version": "one",
        },
        engine_version="one",
    )
    with sqlite3.connect(db_path) as conn:
        (key_bytes,) = conn.execute(
            f"SELECT key_bytes FROM {rekey.PT1_EQUILIBRIUM_TABLE} "
            "WHERE key_hash = ?",
            (original_hash,),
        ).fetchone()
        deeply_nested_key = (
            bytes(key_bytes)[:-1]
            + b',"unread":'
            + b"[" * 1100
            + b"0"
            + b"]" * 1100
            + b"}"
        )
        new_hash = hashlib.sha256(deeply_nested_key).hexdigest()
        conn.execute(
            f"UPDATE {rekey.PT1_EQUILIBRIUM_TABLE} "
            "SET key_hash = ?, key_sha256 = ?, key_bytes = ? "
            "WHERE key_hash = ?",
            (
                new_hash,
                new_hash,
                sqlite3.Binary(deeply_nested_key),
                original_hash,
            ),
        )

    with pytest.raises(rrd.PT1PersistentStoreCorrupt):
        rekey.rekey_cache(
            db_path,
            target_corpus_version=target_corpus_version,
            dry_run=True,
        )


def test_rekey_current_layout_admits_full_row_for_count_and_write(
    tmp_path: Path,
    target_corpus_version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "current-layout-rekey-admission.db"
    put_rekey_gate_row(
        db_path,
        {
            "backend": {"backend_name": "alphamelts"},
            "engine_version": "one",
        },
        engine_version="one",
    )
    admission_rows: list[tuple[set[str], dict[str, object]]] = []
    original_admission = rekey._admit_stored_row

    def record_admission(row: object, **kwargs: object) -> object:
        admission_rows.append((set(row.keys()), dict(kwargs)))  # type: ignore[attr-defined]
        return original_admission(row, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(rekey, "_admit_stored_row", record_admission)
    result = rekey.rekey_cache(
        db_path,
        target_corpus_version=target_corpus_version,
    )

    assert result.rows_before == 1
    assert result.rows_updated == 1
    assert len(admission_rows) == 2
    for columns, kwargs in admission_rows:
        assert {"payload_bytes", "payload_sha256"} <= columns
        assert kwargs["store_kind"] == "pt1"
        assert kwargs["validate_store_schema_version"] is True
        assert kwargs["validate_interpolation_domain"] is True


def test_rekey_current_layout_rejects_null_controls_with_carried_indexes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_corpus_version: str,
) -> None:
    db_path = tmp_path / "current-layout-null-controls-rekey.db"
    original_hash, _payload_bytes = put_rekey_gate_row(
        db_path,
        {
            "backend": {"backend_name": "alphamelts"},
            "engine_version": "one",
        },
        engine_version="one",
    )
    with sqlite3.connect(db_path) as conn:
        (key_bytes,) = conn.execute(
            f"SELECT key_bytes FROM {rekey.PT1_EQUILIBRIUM_TABLE} "
            "WHERE key_hash = ?",
            (original_hash,),
        ).fetchone()
        key = json.loads(bytes(key_bytes))
        key["controls"] = None
        stored_key_bytes = rrd.canonical_json_bytes(key)
        stored_key_hash = rrd._sha256(stored_key_bytes)
        indexes = rekey._physics_columns(key)
        assignments = ["key_hash = ?", "key_sha256 = ?", "key_bytes = ?"]
        values: list[object] = [
            stored_key_hash,
            stored_key_hash,
            sqlite3.Binary(stored_key_bytes),
        ]
        for column, value in indexes.items():
            assignments.append(f"{column} = ?")
            values.append(value)
        conn.execute(
            f"UPDATE {rekey.PT1_EQUILIBRIUM_TABLE} "
            f"SET {', '.join(assignments)} WHERE key_hash = ?",
            (*values, original_hash),
        )

    exact_reader = rrd.PT1PersistentEquilibriumStore(db_path)
    with monkeypatch.context() as query_guard:
        query_guard.setattr(rrd, "_ladder_indexability", lambda *_a, **_kw: None)
        with pytest.raises(
            rrd.PT1PersistentStoreCorrupt,
            match="invalid stored equilibrium replay key controls",
        ):
            exact_reader.get(
                artifact="freeze_gate_curve",
                key=key,
                key_bytes=stored_key_bytes,
                key_hash=stored_key_hash,
            )
    with pytest.raises(
        rrd.PT1PersistentStoreCorrupt,
        match="invalid stored equilibrium replay key controls",
    ):
        rekey.rekey_cache(
            db_path,
            target_corpus_version=target_corpus_version,
            dry_run=True,
        )


def test_rekey_current_layout_rejects_legacy_stamp(
    tmp_path: Path,
    target_corpus_version: str,
) -> None:
    db_path = tmp_path / "current-layout-legacy-stamp.db"
    key_hash, _payload_bytes = put_rekey_gate_row(
        db_path,
        {
            "backend": {"backend_name": "alphamelts"},
            "engine_version": "one",
        },
        engine_version="one",
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"UPDATE {rekey.PT1_EQUILIBRIUM_TABLE} "
            "SET store_schema_version = 'pt1' WHERE key_hash = ?",
            (key_hash,),
        )

    with pytest.raises(
        rrd.PT1PersistentStoreCorrupt,
        match="store schema version drift",
    ):
        rekey.rekey_cache(
            db_path,
            target_corpus_version=target_corpus_version,
            dry_run=True,
        )


def test_rekey_legacy_stamp_with_h40_column_preserves_stamp_and_payload(
    tmp_path: Path,
    target_corpus_version: str,
) -> None:
    db_path = tmp_path / "legacy-pt1-h40-rekey.db"
    _write_rekeyable_rows(db_path, ("one",))
    with sqlite3.connect(db_path) as conn:
        old_hash, original_key_bytes = conn.execute(
            f"SELECT key_hash, key_bytes FROM {rekey.PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()
        off_centre_key = rekey._json_loads(bytes(original_key_bytes))
        off_centre_key["composition_mol_fraction"] = [["SiO2", 0.123456]]
        off_centre_key["controls"] = {"T_K": 1234.567}
        off_centre_key_bytes = rekey.canonical_json_bytes(off_centre_key)
        off_centre_hash = hashlib.sha256(off_centre_key_bytes).hexdigest()
        conn.execute(
            f"UPDATE {rekey.PT1_EQUILIBRIUM_TABLE} "
            "SET key_hash = ?, key_sha256 = ?, key_bytes = ? "
            "WHERE key_hash = ?",
            (
                off_centre_hash,
                off_centre_hash,
                sqlite3.Binary(off_centre_key_bytes),
                old_hash,
            ),
        )
        conn.execute(
            f"ALTER TABLE {rekey.PT1_EQUILIBRIUM_TABLE} "
            "ADD COLUMN store_schema_version TEXT"
        )
        conn.execute(
            f"ALTER TABLE {rekey.PT1_EQUILIBRIUM_TABLE} "
            "ADD COLUMN artifact TEXT"
        )
        conn.execute(
            f"ALTER TABLE {rekey.PT1_EQUILIBRIUM_TABLE} "
            "ADD COLUMN payload_bytes BLOB"
        )
        conn.execute(
            f"ALTER TABLE {rekey.PT1_EQUILIBRIUM_TABLE} "
            "ADD COLUMN physics_bucket_h40_distance REAL"
        )
        conn.execute(
            f"UPDATE {rekey.PT1_EQUILIBRIUM_TABLE} "
            "SET store_schema_version = 'pt1', artifact = ?, payload_bytes = ?",
            ("equilibrium_post_record", sqlite3.Binary(b"opaque legacy payload")),
        )
        before = conn.execute(
            f"SELECT key_hash, key_bytes, payload_bytes "
            f"FROM {rekey.PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()

    result = rekey.rekey_cache(
        db_path,
        target_corpus_version=target_corpus_version,
    )

    assert result.rows_updated == 1
    with sqlite3.connect(db_path) as conn:
        after = conn.execute(
            f"SELECT key_hash, store_schema_version, payload_bytes, "
            "physics_bucket_h40_distance, key_bytes "
            f"FROM {rekey.PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()
    assert after[0] != before[0]
    assert after[1:3] == ("pt1", before[2])
    rekeyed_key = json.loads(bytes(after[4]))
    expected_h40 = independent_ladder_distance(
        rekeyed_key,
        sig_figs=float(dict(rrd.PHYSICS_BUCKET_ALL_LADDER_RUNGS)["h40"]),
        include_controls=False,
    )
    assert expected_h40 > 0.0
    assert after[3] == expected_h40


def test_rekey_compact_table_overlapping_preflight_signature(
    tmp_path: Path,
    target_corpus_version: str,
) -> None:
    db_path = tmp_path / "compact-preflight-overlap-rekey.db"
    _write_rekeyable_rows(db_path, ("one",))
    opaque_payload = b"opaque compact rekey payload"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"ALTER TABLE {rekey.PT1_EQUILIBRIUM_TABLE} "
            "ADD COLUMN artifact TEXT"
        )
        conn.execute(
            f"ALTER TABLE {rekey.PT1_EQUILIBRIUM_TABLE} "
            "ADD COLUMN payload_bytes BLOB"
        )
        conn.execute(
            f"UPDATE {rekey.PT1_EQUILIBRIUM_TABLE} "
            "SET artifact = ?, payload_bytes = ?",
            ("equilibrium_post_record", sqlite3.Binary(opaque_payload)),
        )

    result = rekey.rekey_cache(
        db_path,
        target_corpus_version=target_corpus_version,
    )

    assert result.rows_updated == 1
    with sqlite3.connect(db_path) as conn:
        (stored_payload,) = conn.execute(
            f"SELECT payload_bytes FROM {rekey.PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()
    assert bytes(stored_payload) == opaque_payload


def test_rekey_legacy_stamp_rejects_overflowing_key_as_corrupt(
    tmp_path: Path,
    target_corpus_version: str,
) -> None:
    db_path = tmp_path / "legacy-overflow-key-cache.db"
    _write_rekeyable_rows(db_path, ("one",))
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"ALTER TABLE {rekey.PT1_EQUILIBRIUM_TABLE} "
            "ADD COLUMN store_schema_version TEXT"
        )
        key_hash, key_bytes = conn.execute(
            f"SELECT key_hash, key_bytes FROM {rekey.PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()
        overflow_key_bytes = bytes(key_bytes)[:-1] + b',"overflow":1e400}'
        overflow_hash = hashlib.sha256(overflow_key_bytes).hexdigest()
        conn.execute(
            f"UPDATE {rekey.PT1_EQUILIBRIUM_TABLE} "
            "SET key_hash = ?, key_sha256 = ?, key_bytes = ?, "
            "store_schema_version = 'pt1' WHERE key_hash = ?",
            (
                overflow_hash,
                overflow_hash,
                sqlite3.Binary(overflow_key_bytes),
                key_hash,
            ),
        )

    with pytest.raises(rrd.PT1PersistentStoreCorrupt):
        rekey.rekey_cache(
            db_path,
            target_corpus_version=target_corpus_version,
            dry_run=True,
        )


def test_rekey_wraps_unindexable_stored_key_as_corrupt_and_rolls_back(
    tmp_path: Path,
    target_corpus_version: str,
) -> None:
    db_path = tmp_path / "legacy-subnormal-key-cache.db"
    _write_rekeyable_rows(db_path, ("one",))
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"ALTER TABLE {rekey.PT1_EQUILIBRIUM_TABLE} "
            "ADD COLUMN physics_bucket_h40_distance REAL"
        )
        key_hash, key_bytes = conn.execute(
            f"SELECT key_hash, key_bytes FROM {rekey.PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()
        key = rekey._json_loads(bytes(key_bytes))
        key["controls"] = {"T_K": 5e-324}
        stored_key_bytes = rekey.canonical_json_bytes(key)
        stored_key_hash = hashlib.sha256(stored_key_bytes).hexdigest()
        conn.execute(
            f"UPDATE {rekey.PT1_EQUILIBRIUM_TABLE} "
            "SET key_hash = ?, key_sha256 = ?, key_bytes = ? "
            "WHERE key_hash = ?",
            (
                stored_key_hash,
                stored_key_hash,
                stored_key_bytes,
                key_hash,
            ),
        )
        before = conn.execute(
            f"SELECT key_hash, key_sha256, key_bytes, engine_version, "
            f"corpus_version, physics_bucket_h40_distance "
            f"FROM {rekey.PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()

    with pytest.raises(
        rrd.PT1PersistentStoreCorrupt,
        match="stored request key is not indexable",
    ):
        rekey.rekey_cache(
            db_path,
            target_corpus_version=target_corpus_version,
        )

    with sqlite3.connect(db_path) as conn:
        after = conn.execute(
            f"SELECT key_hash, key_sha256, key_bytes, engine_version, "
            f"corpus_version, physics_bucket_h40_distance "
            f"FROM {rekey.PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()
    assert after == before


@pytest.mark.parametrize(
    "repair_notices_json",
    ['[{"kind":"rekey_repair"}]', "[]", None],
)
def test_rekey_preserves_repair_notice_values(
    tmp_path: Path,
    target_corpus_version: str,
    repair_notices_json: str | None,
) -> None:
    db_path = tmp_path / "notice-cache.db"
    _write_rekeyable_rows(db_path, ("one",))
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"ALTER TABLE {rekey.PT1_EQUILIBRIUM_TABLE} "
            "ADD COLUMN repair_notices_json TEXT"
        )
        conn.execute(
            f"UPDATE {rekey.PT1_EQUILIBRIUM_TABLE} "
            "SET repair_notices_json = ? WHERE engine_version = 'one'",
            (repair_notices_json,),
        )

    result = rekey.rekey_cache(
        db_path,
        target_corpus_version=target_corpus_version,
    )

    assert result.rows_updated == 1
    with sqlite3.connect(db_path) as conn:
        (stored,) = conn.execute(
            f"SELECT repair_notices_json FROM {rekey.PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()
    assert stored == repair_notices_json
