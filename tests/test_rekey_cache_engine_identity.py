import hashlib
import sqlite3
from pathlib import Path

import pytest

from scripts import rekey_cache_engine_identity as rekey


def _write_convergent_rows(db_path: Path) -> None:
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
    for engine_version in ("one", "two"):
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
    _write_convergent_rows(db_path)

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
