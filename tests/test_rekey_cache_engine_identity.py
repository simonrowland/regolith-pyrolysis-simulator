import hashlib
import sqlite3
from pathlib import Path

import pytest

from scripts import rekey_cache_engine_identity as rekey


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


def test_failed_rekey_retry_reuses_content_addressed_backup(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    _write_rekeyable_rows(db_path)

    for _attempt in range(2):
        with pytest.raises(sqlite3.IntegrityError):
            rekey.rekey_cache(db_path, target_corpus_version="target")
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


def test_rekey_locks_identity_decision_before_backup(tmp_path: Path, monkeypatch) -> None:
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
                    "corpus_version": "target",
                },
                "corpus_version": "target",
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
                (raced_key_hash, raced_key_hash, raced_key_bytes, "target"),
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

    result = rekey.rekey_cache(db_path, target_corpus_version="target")

    assert race_outcomes == ["locked"]
    assert result.rows_before == 1
    assert result.rows_updated == 1
    assert result.backup_path is not None
    with sqlite3.connect(result.backup_path) as backup_conn:
        (backup_key_bytes,) = backup_conn.execute(
            f"SELECT key_bytes FROM {rekey.PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()
    assert rekey._json_loads(bytes(backup_key_bytes))["engine_version"] == "one"
