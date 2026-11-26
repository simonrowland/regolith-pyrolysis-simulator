from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import merge_grind_cache
from scripts.analyze_dose_cache_study import load_provenance, summarize
from scripts.prune_reduced_real_cache import main as prune_reduced_real_cache_main
from scripts.seed_reduced_real_cache import (
    CacheMergeCollision,
    CacheSourceRowInvalid,
    CacheSourceSchemaMismatch,
    payload_count,
    seed_cache,
    validate_source_schema,
)
import simulator.reduced_real_determinism as rrd
from simulator.corpus_version import current_corpus_version
from simulator.optimize.evaluate import _cache_trace_payload
from simulator.reduced_real_determinism import (
    PT1_EQUILIBRIUM_TABLE,
    PT1_METADATA_TABLE,
    PT1_LEGACY_STORE_SCHEMA_VERSION,
    PT1_STORE_SCHEMA_VERSION,
    SCHEMA_VERSION,
    PT1PersistentEquilibriumStore,
    canonical_json_bytes,
)


def _put_cache_row(db_path: Path, *, tag: str) -> str:
    key = {
        "artifact": "freeze_gate_curve",
        "code_version": "test",
        "data_digests": {"fixture": "v1"},
        "schema_version": "test",
        "tag": tag,
    }
    payload = {
        "curve": {
            "source": "unit-test:seed-cache",
            "status": "ok",
            "solidus_T_C": 1200.0,
            "liquidus_T_C": 1300.0,
            "path": [
                {"temperature_C": 1200.0, "liquid_fraction": 0.0},
                {"temperature_C": 1300.0, "liquid_fraction": 1.0},
            ],
            "is_liquidus_floor": False,
            "tag": tag,
        },
    }
    key_bytes = canonical_json_bytes(key)
    payload_bytes = canonical_json_bytes(payload)
    key_hash = hashlib.sha256(key_bytes).hexdigest()
    PT1PersistentEquilibriumStore(db_path).put(
        artifact="freeze_gate_curve",
        key=key,
        key_bytes=key_bytes,
        key_hash=key_hash,
        payload=payload,
        payload_bytes=payload_bytes,
        payload_hash=hashlib.sha256(payload_bytes).hexdigest(),
    )
    return key_hash


def _put_real_equilibrium_cache_row(db_path: Path) -> None:
    key = {
        "schema_version": SCHEMA_VERSION,
        "namespace_id": "alphamelts:equilibrium-composite",
        "artifact": "equilibrium_post_record",
        "intent": "EQUILIBRIUM",
        "vapor_pressure_provider_selection": "builtin-vapor-pressure",
        "code_version": "test",
        "data_digests": {},
    }
    payload = {
        "authority": {
            "schema": "reduced-real-authority-v1",
            "evidence_class": "melts",
            "backend_family": "alphamelts",
            "backend": {
                "backend_name": "AlphaMELTSBackend",
                "backend_class": (
                    "simulator.melt_backend.alphamelts.AlphaMELTSBackend"
                ),
            },
            "provider": {
                "resolved_provider_id": "alphamelts-diagnostic",
                "resolved_role": "authoritative",
                "authoritative_provider_id": "alphamelts-diagnostic",
                "fallback_provider_id": None,
            },
            "vapor_pressure": {
                "resolved_provider_id": "builtin-vapor-pressure",
                "resolved_role": "authoritative",
                "authoritative_provider_id": "builtin-vapor-pressure",
                "fallback_provider_id": None,
                "fallback_allowed": False,
            },
        },
        "equilibrium_result": {"status": "ok"},
        "last_vapor_pressures_source": {"Na": "builtin_authoritative"},
    }
    key_bytes = canonical_json_bytes(key)
    payload_bytes = canonical_json_bytes(payload)
    PT1PersistentEquilibriumStore(db_path).put(
        artifact="equilibrium_post_record",
        key=key,
        key_bytes=key_bytes,
        key_hash=hashlib.sha256(key_bytes).hexdigest(),
        payload=payload,
        payload_bytes=payload_bytes,
        payload_hash=hashlib.sha256(payload_bytes).hexdigest(),
    )


def _put_replayable_equilibrium_cache_row(
    db_path: Path,
    *,
    tag: str | None = None,
) -> str:
    """Persist a production-serialized equilibrium replay payload."""

    from tests.test_reduced_real_pt0_determinism import (
        _pt0_equilibrium_capture_context,
    )

    capture = rrd.PT0DeterminismStore("capture")
    sim, result = _pt0_equilibrium_capture_context(capture)
    sim._last_vapor_pressures_source = {"Na": "builtin_authoritative"}
    key = capture._equilibrium_key(sim)
    if tag is not None:
        key["code_version"] = f"test:{tag}"
    payload = rrd.equilibrium_payload(sim, result)
    key_bytes = canonical_json_bytes(key)
    payload_bytes = canonical_json_bytes(payload)
    key_hash = hashlib.sha256(key_bytes).hexdigest()
    PT1PersistentEquilibriumStore(db_path).put(
        artifact="equilibrium_post_record",
        key=key,
        key_bytes=key_bytes,
        key_hash=key_hash,
        payload=payload,
        payload_bytes=payload_bytes,
        payload_hash=hashlib.sha256(payload_bytes).hexdigest(),
    )
    return key_hash


def _run_reduced_real_prune(capsys, *args: object) -> dict[str, object]:
    assert prune_reduced_real_cache_main([str(arg) for arg in args]) == 0
    return json.loads(capsys.readouterr().out)


def test_reduced_real_prune_is_dry_by_default_and_deletes_only_stale_codecs(
    tmp_path: Path,
    capsys,
) -> None:
    db_path = tmp_path / "reduced-real-prune.sqlite"
    wal_connection = sqlite3.connect(db_path)
    assert (
        wal_connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    )
    freeze_artifact = "freeze_gate_curve"
    equilibrium_artifact = "equilibrium_post_record"
    freeze_version = rrd._payload_codec_version(freeze_artifact)
    equilibrium_version = rrd._payload_codec_version(equilibrium_artifact)
    assert freeze_version is not None
    assert equilibrium_version is not None
    rows = (
        (freeze_artifact, "freeze-current", freeze_version),
        (freeze_artifact, "freeze-missing", None),
        (freeze_artifact, "freeze-stale", freeze_version - 1),
        (equilibrium_artifact, "equilibrium-current", equilibrium_version),
        (equilibrium_artifact, "equilibrium-missing", None),
        (equilibrium_artifact, "equilibrium-stale", equilibrium_version - 1),
    )
    row_hashes: dict[tuple[str, str], str] = {}
    for artifact, tag, _version in rows:
        if artifact == "freeze_gate_curve":
            row_hashes[(artifact, tag)] = _put_cache_row(db_path, tag=tag)
        else:
            row_hashes[(artifact, tag)] = _put_replayable_equilibrium_cache_row(
                db_path, tag=tag
            )

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        columns = [
            str(row[1])
            for row in conn.execute(
                f"PRAGMA table_info({PT1_EQUILIBRIUM_TABLE})"
            )
        ]
        for artifact, tag, version in rows:
            conn.execute(
                f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
                "SET payload_codec_version = ? WHERE key_hash = ?",
                (version, row_hashes[(artifact, tag)]),
            )

        current_freeze = conn.execute(
            f"SELECT * FROM {PT1_EQUILIBRIUM_TABLE} "
            "WHERE key_hash = ?",
            (row_hashes[("freeze_gate_curve", "freeze-current")],),
        ).fetchone()
        unversioned = dict(current_freeze)
        unversioned["key_hash"] = "unversioned-test-row"
        unversioned["artifact"] = "unversioned_test_artifact"
        unversioned["payload_codec_version"] = None
        conn.execute(
            f"INSERT INTO {PT1_EQUILIBRIUM_TABLE} "
            f"({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            tuple(unversioned[column] for column in columns),
        )

    assert Path(f"{db_path}-wal").exists()
    assert Path(f"{db_path}-shm").exists()
    row_count_before = payload_count(db_path)
    dry_run = _run_reduced_real_prune(capsys, db_path)
    assert dry_run["dry_run"] is True
    assert dry_run["rows_by_artifact"] == {
        freeze_artifact: 3,
        equilibrium_artifact: 3,
    }
    assert dry_run["matched_by_artifact"] == {
        freeze_artifact: 2,
        equilibrium_artifact: 2,
    }
    assert dry_run["deleted_by_artifact"] == {
        freeze_artifact: 0,
        equilibrium_artifact: 0,
    }
    assert payload_count(db_path) == row_count_before

    applied = _run_reduced_real_prune(capsys, db_path, "--apply")
    assert applied["apply"] is True
    assert applied["deleted_by_artifact"] == {
        freeze_artifact: 2,
        equilibrium_artifact: 2,
    }
    assert applied["vacuumed"] is True
    assert payload_count(db_path) == 3

    store = PT1PersistentEquilibriumStore(db_path)
    with sqlite3.connect(db_path) as conn:
        remaining_hashes = {
            str(row[0])
            for row in conn.execute(
                f"SELECT key_hash FROM {PT1_EQUILIBRIUM_TABLE}"
            )
        }
        assert conn.execute(
            f"SELECT count(*) FROM {PT1_EQUILIBRIUM_TABLE} "
            "WHERE artifact = 'unversioned_test_artifact'"
        ).fetchone()[0] == 1
    assert remaining_hashes == {
        row_hashes[(freeze_artifact, "freeze-current")],
        row_hashes[(equilibrium_artifact, "equilibrium-current")],
        "unversioned-test-row",
    }
    for artifact, tag in (
        (freeze_artifact, "freeze-current"),
        (equilibrium_artifact, "equilibrium-current"),
    ):
        with sqlite3.connect(db_path) as conn:
            key_bytes = bytes(conn.execute(
                f"SELECT key_bytes FROM {PT1_EQUILIBRIUM_TABLE} WHERE key_hash = ?",
                (row_hashes[(artifact, tag)],),
            ).fetchone()[0])
        key = json.loads(key_bytes.decode("utf-8"))
        entry = store.get(
            artifact=artifact,
            key=key,
            key_bytes=key_bytes,
            key_hash=row_hashes[(artifact, tag)],
        )
        assert entry is not None
    wal_connection.close()


def test_reduced_real_prune_reports_delete_when_vacuum_fails_and_retries(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    db_path = tmp_path / "reduced-real-vacuum-retry.sqlite"
    _put_cache_row(db_path, tag="vacuum-retry")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
            "SET payload_codec_version = NULL"
        )

    real_vacuum = rrd._vacuum_pt1_cache
    vacuum_calls = 0

    def fail_once(connection) -> None:
        nonlocal vacuum_calls
        vacuum_calls += 1
        if vacuum_calls == 1:
            raise sqlite3.OperationalError("disk full")
        real_vacuum(connection)

    monkeypatch.setattr(rrd, "_vacuum_pt1_cache", fail_once)

    assert prune_reduced_real_cache_main([str(db_path), "--apply"]) == 1
    failed_output = capsys.readouterr()
    failed_summary = json.loads(failed_output.out)
    assert failed_summary["deleted_by_artifact"]["freeze_gate_curve"] == 1
    assert "deleted 1, vacuum failed: OperationalError: disk full" in (
        failed_output.err
    )
    assert payload_count(db_path) == 0

    assert prune_reduced_real_cache_main([str(db_path), "--apply"]) == 0
    retry_summary = json.loads(capsys.readouterr().out)
    assert vacuum_calls == 2
    assert retry_summary["deleted_by_artifact"] == {
        "freeze_gate_curve": 0,
        "equilibrium_post_record": 0,
    }
    assert retry_summary["vacuumed"] is True


def test_reduced_real_prune_dry_run_reads_one_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "reduced-real-read-snapshot.sqlite"
    _put_cache_row(db_path, tag="read-snapshot")
    real_connect = sqlite3.connect
    read_transaction_states: list[bool] = []

    class TrackingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.lstrip().upper().startswith(("SELECT", "PRAGMA")):
                read_transaction_states.append(self.in_transaction)
            return super().execute(sql, parameters)

    def tracking_connect(*args, **kwargs):
        return real_connect(*args, factory=TrackingConnection, **kwargs)

    monkeypatch.setattr(rrd.sqlite3, "connect", tracking_connect)

    result = rrd._prune_pt1_payload_codec_rows(db_path)

    assert result["dry_run"] is True
    assert read_transaction_states
    assert all(read_transaction_states)


def test_reduced_real_prune_dry_run_does_not_checkpoint_leftover_wal(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "reduced-real-leftover-wal.sqlite"
    _put_cache_row(db_path, tag="leftover-wal")

    connection = sqlite3.connect(db_path)
    assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute(
        f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
        "SET payload_codec_version = NULL"
    )
    connection.commit()

    wal_path = Path(f"{db_path}-wal")
    shm_path = Path(f"{db_path}-shm")
    database_before = db_path.read_bytes()
    wal_before = wal_path.read_bytes()
    shm_before = shm_path.read_bytes()
    connection.close()

    # Recreate a committed WAL state after the last connection closes, so the
    # dry-run tool is the only connection that opens this database.
    db_path.write_bytes(database_before)
    wal_path.write_bytes(wal_before)
    shm_path.write_bytes(shm_before)
    assert wal_path.exists()

    result = rrd._prune_pt1_payload_codec_rows(db_path)

    assert result["matched_by_artifact"]["freeze_gate_curve"] == 1
    assert db_path.read_bytes() == database_before
    assert wal_path.read_bytes() == wal_before


def test_reduced_real_prune_matches_admission_for_real_codec_stamp(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "reduced-real-real-codec-stamp.sqlite"
    artifact = "equilibrium_post_record"
    current_version = rrd._payload_codec_version(artifact)
    assert current_version is not None
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"CREATE TABLE {PT1_EQUILIBRIUM_TABLE} "
            "(artifact TEXT, payload_codec_version REAL)"
        )
        conn.execute(
            f"INSERT INTO {PT1_EQUILIBRIUM_TABLE} VALUES (?, ?)",
            (artifact, float(current_version)),
        )
        storage_type = conn.execute(
            f"SELECT typeof(payload_codec_version) "
            f"FROM {PT1_EQUILIBRIUM_TABLE} WHERE artifact = ?",
            (artifact,),
        ).fetchone()[0]
    assert storage_type == "real"
    assert rrd._decode_payload_for_admission(
        artifact, {}, payload_codec_version=float(current_version)
    ) is None

    dry_run = rrd._prune_pt1_payload_codec_rows(db_path)
    assert dry_run["matched_by_artifact"][artifact] == 1

    applied = rrd._prune_pt1_payload_codec_rows(db_path, apply=True)
    assert applied["deleted_by_artifact"][artifact] == 1
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            f"SELECT count(*) FROM {PT1_EQUILIBRIUM_TABLE} WHERE artifact = ?",
            (artifact,),
        ).fetchone()[0] == 0


def _put_internal_analytical_cache_row(db_path: Path) -> None:
    corpus_version = current_corpus_version()
    key = {
        "schema_version": SCHEMA_VERSION,
        "namespace_id": "alphamelts:equilibrium-composite",
        "artifact": "equilibrium_post_record",
        "code_version": "test",
        "corpus_version": corpus_version,
        "data_digests": {},
        "backend": {
            "backend_name": "internal-analytical",
            "backend_class": "InternalAnalyticalBackend",
            "corpus_version": corpus_version,
        },
        "provider": {
            "resolved_provider_id": "builtin-backend-equilibrium",
            "authoritative_provider_id": "builtin-backend-equilibrium",
            "fallback_provider_id": None,
        },
    }
    payload = {
        "authority": {
            "schema": "reduced-real-authority-v1",
            "evidence_class": "melts",
            "backend_family": "alphamelts",
            "backend": key["backend"],
            "provider": key["provider"],
            "vapor_pressure": {
                "resolved_provider_id": "builtin-vapor-pressure",
                "resolved_role": "authoritative",
            },
        },
        "equilibrium_result": {"status": "ok"},
    }
    key_bytes = canonical_json_bytes(key)
    payload_bytes = canonical_json_bytes(payload)
    key_hash = hashlib.sha256(key_bytes).hexdigest()
    payload_hash = hashlib.sha256(payload_bytes).hexdigest()
    PT1PersistentEquilibriumStore(db_path)
    with sqlite3.connect(db_path) as con:
        con.execute(
            f"""
            INSERT INTO {PT1_EQUILIBRIUM_TABLE} (
                key_hash, artifact, store_schema_version,
                request_schema_version, key_sha256, payload_sha256,
                key_bytes, payload_bytes, code_version, corpus_version,
                engine_version, data_digests_json, created_at, git_dirty
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key_hash,
                "equilibrium_post_record",
                PT1_STORE_SCHEMA_VERSION,
                SCHEMA_VERSION,
                key_hash,
                payload_hash,
                sqlite3.Binary(key_bytes),
                sqlite3.Binary(payload_bytes),
                "test",
                corpus_version,
                "test",
                "{}",
                "2026-07-12T00:00:00Z",
                0,
            ),
        )


def test_seed_cache_merges_sources_idempotently(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    _put_cache_row(source, tag="a")

    first = seed_cache(target, [source])
    second = seed_cache(target, [source])

    assert first["inserted_rows"] == 1
    assert first["rows_after"] == 1
    assert second["inserted_rows"] == 0
    assert payload_count(target) == 1


@pytest.mark.parametrize(
    ("drop_codec_column", "expected_version"),
    [
        (False, rrd.PT0_GATE_CURVE_CODEC_VERSION),
        (True, None),
    ],
    ids=("current-stamp", "legacy-unmigrated"),
)
def test_seed_cache_preserves_or_carries_missing_payload_codec_version(
    tmp_path: Path,
    drop_codec_column: bool,
    expected_version: int | None,
) -> None:
    source = tmp_path / "codec-source.db"
    target = tmp_path / "codec-target.db"
    _put_cache_row(source, tag="codec-transfer")
    if drop_codec_column:
        with sqlite3.connect(source) as conn:
            conn.execute(
                f"ALTER TABLE {PT1_EQUILIBRIUM_TABLE} "
                "DROP COLUMN payload_codec_version"
            )

    seed_cache(target, [source])

    with sqlite3.connect(target) as conn:
        (stored_version,) = conn.execute(
            f"SELECT payload_codec_version FROM {PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()
    assert stored_version == expected_version


def test_seed_cache_refreshes_stale_stamp_for_identical_row(tmp_path: Path) -> None:
    source = tmp_path / "fresh-codec-source.db"
    target = tmp_path / "stale-codec-target.db"
    _put_cache_row(source, tag="codec-refresh")
    target.write_bytes(source.read_bytes())
    with sqlite3.connect(target) as conn:
        conn.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
            "SET payload_codec_version = ?",
            (rrd.PT0_GATE_CURVE_CODEC_VERSION - 1,),
        )

    seed_cache(target, [source])

    with sqlite3.connect(target) as conn:
        (stored_version,) = conn.execute(
            f"SELECT payload_codec_version FROM {PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()
    assert stored_version == rrd.PT0_GATE_CURVE_CODEC_VERSION


def test_seed_cache_accepts_new_schema_equilibrium_authority(tmp_path: Path) -> None:
    source = tmp_path / "source-real.db"
    target = tmp_path / "target-real.db"
    _put_real_equilibrium_cache_row(source)

    result = seed_cache(target, [source])

    assert result["inserted_rows"] == 1
    assert payload_count(target) == 1


@pytest.mark.parametrize("transport", ["seed", "merge"])
@pytest.mark.parametrize(
    "repair_notices_json",
    ['[{"kind":"seed_repair"}]', "[]", None],
)
def test_seed_and_merge_preserve_repair_notice_values(
    tmp_path: Path,
    transport: str,
    repair_notices_json: str | None,
) -> None:
    source = tmp_path / f"{transport}-notice-source.db"
    target = tmp_path / f"{transport}-notice-target.db"
    _put_cache_row(source, tag=f"notice-{transport}")
    with sqlite3.connect(source) as conn:
        conn.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} SET repair_notices_json = ?",
            (repair_notices_json,),
        )

    if transport == "seed":
        seed_cache(target, [source])
    else:
        assert merge_grind_cache.main([str(target), str(source)]) == 0

    with sqlite3.connect(target) as conn:
        (stored,) = conn.execute(
            f"SELECT repair_notices_json FROM {PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()
    assert stored == repair_notices_json


@pytest.mark.parametrize("transport", ["seed", "merge"])
def test_seed_and_merge_accept_v2_source_without_modifying_it(
    tmp_path: Path,
    transport: str,
) -> None:
    source = tmp_path / f"{transport}-legacy-v2-source.db"
    target = tmp_path / f"{transport}-legacy-v2-target.db"
    _put_cache_row(source, tag=f"legacy-v2-{transport}")
    with sqlite3.connect(source) as conn:
        key_hash, key_bytes, payload_bytes = conn.execute(
            f"SELECT key_hash, key_bytes, payload_bytes "
            f"FROM {PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()
        conn.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} SET store_schema_version = ?",
            (PT1_LEGACY_STORE_SCHEMA_VERSION,),
        )
        conn.execute(
            f"UPDATE {PT1_METADATA_TABLE} SET value = ? "
            "WHERE key = 'store_schema_version'",
            (PT1_LEGACY_STORE_SCHEMA_VERSION,),
        )
        conn.execute(
            f"ALTER TABLE {PT1_EQUILIBRIUM_TABLE} "
            "DROP COLUMN repair_notices_json"
        )
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()

    if transport == "seed":
        result = seed_cache(target, [source])
        assert result["inserted_rows"] == 1
    else:
        assert merge_grind_cache.main([str(target), str(source)]) == 0

    with sqlite3.connect(target) as conn:
        stored = conn.execute(
            f"SELECT store_schema_version, key_bytes, payload_bytes, "
            f"repair_notices_json FROM {PT1_EQUILIBRIUM_TABLE} "
            "WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
    assert stored == (PT1_STORE_SCHEMA_VERSION, key_bytes, payload_bytes, None)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_sha256


def test_seed_cache_ignores_source_only_columns_and_leaves_source_unchanged(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source-with-extra-column.db"
    target = tmp_path / "target-current-schema.db"
    _put_cache_row(source, tag="source-only-column")
    with sqlite3.connect(source) as conn:
        (key_hash,) = conn.execute(
            f"SELECT key_hash FROM {PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()
        conn.execute(
            f"ALTER TABLE {PT1_EQUILIBRIUM_TABLE} "
            "ADD COLUMN source_only_metadata TEXT"
        )
        conn.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
            "SET source_only_metadata = 'ignored' WHERE key_hash = ?",
            (key_hash,),
        )
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()

    summary = seed_cache(target, [source])

    assert summary["inserted_rows"] == 1
    with sqlite3.connect(target) as conn:
        target_columns = {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({PT1_EQUILIBRIUM_TABLE})")
        }
        assert "source_only_metadata" not in target_columns
        assert conn.execute(
            f"SELECT 1 FROM {PT1_EQUILIBRIUM_TABLE} WHERE key_hash = ?",
            (key_hash,),
        ).fetchone() == (1,)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_sha256


def test_seed_cache_refuses_cross_schema_source(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    _put_cache_row(source, tag="cross-schema")
    with sqlite3.connect(source) as con:
        con.execute(
            f"UPDATE {PT1_METADATA_TABLE} SET value=? WHERE key=?",
            ("pt1-old-schema", "store_schema_version"),
        )

    with pytest.raises(CacheSourceSchemaMismatch) as exc_info:
        seed_cache(target, [source])

    message = str(exc_info.value)
    assert str(source) in message
    assert "pt1-old-schema" in message
    assert PT1_STORE_SCHEMA_VERSION in message
    assert payload_count(target) == 0


def test_validate_source_schema_does_not_create_missing_source(tmp_path: Path) -> None:
    source = tmp_path / "missing.db"

    with pytest.raises(FileNotFoundError):
        validate_source_schema(source)

    assert not source.exists()


def test_merge_grind_cache_refuses_row_schema_mismatch_before_target_create(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    _put_cache_row(source, tag="old-row-schema")
    with sqlite3.connect(source) as con:
        con.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} SET store_schema_version=?",
            ("pt1-old-row-schema",),
        )

    rc = merge_grind_cache.main([str(target), str(source)])
    captured = capsys.readouterr()

    assert rc == 2
    assert "pt1-old-row-schema" in captured.err
    assert PT1_STORE_SCHEMA_VERSION in captured.err
    assert not target.exists()


def test_merge_grind_cache_missing_source_does_not_create_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "missing.db"
    target = tmp_path / "target.db"

    rc = merge_grind_cache.main([str(target), str(missing)])
    captured = capsys.readouterr()

    assert rc == 2
    assert "cache source does not exist" in captured.err
    assert not missing.exists()
    assert not target.exists()


def test_merge_grind_cache_resumes_existing_partial_target(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "target.db"
    source_existing = tmp_path / "source-existing.db"
    source_new = tmp_path / "source-new.db"
    _put_cache_row(target, tag="already-merged")
    shutil.copyfile(target, source_existing)
    _put_cache_row(source_new, tag="remaining")

    rc = merge_grind_cache.main(
        [str(target), str(source_existing), str(source_new)]
    )
    captured = capsys.readouterr()

    assert rc == 0
    assert "inserted=0" in captured.out
    assert "inserted=1" in captured.out
    assert payload_count(target) == 2


def test_merge_grind_cache_refuses_invalid_later_source_before_target_create(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    valid = tmp_path / "valid.db"
    invalid = tmp_path / "invalid.db"
    target = tmp_path / "target.db"
    _put_cache_row(valid, tag="valid")
    _put_internal_analytical_cache_row(invalid)

    rc = merge_grind_cache.main([str(target), str(valid), str(invalid)])
    captured = capsys.readouterr()

    assert rc == 2
    assert "internal-analytical" in captured.err
    assert not target.exists()


def test_seed_cache_refuses_invalid_later_source_without_mutating_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.db"
    valid = tmp_path / "valid.db"
    invalid = tmp_path / "invalid.db"
    _put_cache_row(target, tag="existing")
    _put_cache_row(valid, tag="valid")
    _put_internal_analytical_cache_row(invalid)
    before = target.read_bytes()

    with pytest.raises(RuntimeError, match="internal-analytical"):
        seed_cache(target, [valid, invalid])

    assert target.read_bytes() == before
    assert payload_count(target) == 1


def test_seed_cache_refuses_mixed_epoch_collision_without_mutating_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.db"
    conflicting = tmp_path / "conflicting.db"
    _put_cache_row(target, tag="same-key")
    shutil.copyfile(target, conflicting)
    with sqlite3.connect(conflicting) as con:
        con.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} SET created_at=?",
            ("2026-07-12T01:02:03Z",),
        )
    before = target.read_bytes()

    with pytest.raises(CacheMergeCollision):
        seed_cache(target, [conflicting])

    assert target.read_bytes() == before


def test_seed_cache_refuses_late_source_hash_drift_without_mutating_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.db"
    valid = tmp_path / "valid.db"
    invalid = tmp_path / "invalid.db"
    _put_cache_row(target, tag="existing")
    _put_cache_row(valid, tag="valid")
    _put_cache_row(invalid, tag="hash-drift")
    with sqlite3.connect(invalid) as con:
        con.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} SET payload_sha256=?",
            ("0" * 64,),
        )
    before = target.read_bytes()

    with pytest.raises(CacheSourceRowInvalid, match="payload_sha256"):
        seed_cache(target, [valid, invalid])

    assert target.read_bytes() == before


def test_seed_cache_refuses_malformed_late_source_metadata_without_mutating_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.db"
    valid = tmp_path / "valid.db"
    invalid = tmp_path / "invalid.db"
    _put_cache_row(target, tag="existing")
    _put_cache_row(valid, tag="valid")
    _put_cache_row(invalid, tag="bad-metadata")
    with sqlite3.connect(invalid) as con:
        con.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} SET created_at=?, git_dirty=?",
            ("not-a-timestamp", "not-a-flag"),
        )
    before = target.read_bytes()

    with pytest.raises(CacheSourceRowInvalid, match="git_dirty"):
        seed_cache(target, [valid, invalid])

    assert target.read_bytes() == before


def test_seed_cache_refuses_late_source_artifact_key_drift_without_mutating_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.db"
    valid = tmp_path / "valid.db"
    invalid = tmp_path / "invalid.db"
    _put_cache_row(target, tag="existing")
    _put_cache_row(valid, tag="valid")
    _put_cache_row(invalid, tag="artifact-drift")
    with sqlite3.connect(invalid) as con:
        con.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} SET artifact=?",
            ("wrong-artifact",),
        )
    before = target.read_bytes()

    with pytest.raises(CacheSourceRowInvalid, match="artifact"):
        seed_cache(target, [valid, invalid])

    assert target.read_bytes() == before


def test_analyze_dose_cache_study_extracts_dose_and_hour_profile(tmp_path: Path) -> None:
    study = tmp_path / "study"
    study.mkdir()
    records = [
        {
            "candidate_id": "a",
            "cache_hit": False,
            "cache_key": "shared",
            "patch": {
                "campaigns": {
                    "C3": {"alkali_dosing": {"Na_kg": 10.0, "K_kg": 2.0}}
                }
            },
            "trace_summary": {
                "per_hour": [
                    {"hour": 1, "reduced_real_cache_state": "cached_exact"},
                    {"hour": 2, "reduced_real_cache_state": "live_fill"},
                ],
                "reduced_real_cache": {
                    "cache_state_counts": {"cached_exact": 1, "live_fill": 1},
                    "misses": 0,
                },
            },
        },
        {
            "candidate_id": "b",
            "cache_hit": True,
            "cache_key": "shared",
            "patch": {
                "campaigns": {
                    "C3": {"alkali_dosing": {"Na_kg": 20.0, "K_kg": 2.0}}
                }
            },
            "trace_summary": {"per_hour": [{"hour": 1, "cache_state": "cached_exact"}]},
        },
    ]
    (study / "provenance.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )

    summary = summarize(load_provenance(study))

    assert summary["dose_records"] == 2
    assert summary["dose_ranges"]["Na_kg"] == {
        "min": 10.0,
        "max": 20.0,
        "unique_count": 2,
    }
    assert summary["candidate_result_cache"]["hits"] == 1
    assert summary["candidate_result_cache"]["misses"] == 1
    assert summary["per_hour_profile_available"] is True
    assert summary["per_hour_cache_state_counts"]["1"]["cached_exact"] == 2
    assert summary["per_hour_cache_state_counts"]["2"]["live_fill"] == 1
    assert len(summary["dose_divergent_shared_evalspec_cache_keys"]) == 1


def test_cache_trace_payload_strips_full_trace_when_compact_profile_exists() -> None:
    run_execution = SimpleNamespace(
        per_hour=(
            {
                "hour": 1,
                "campaign": "C3",
                "T_C": 1320.0,
                "reduced_real_cache_state": "cached_exact",
                "full_run_payload": {"must": "not leak"},
            },
        ),
        reduced_real_cache={"cache_state_counts": {"cached_exact": 1}, "misses": 0},
        trace={
            "raw": "fallback",
            "per_hour": [{"full_run_payload": {"must": "not leak"}}],
        },
    )

    payload = _cache_trace_payload(run_execution, None)

    assert payload == {
        "reduced_real_cache": {"cache_state_counts": {"cached_exact": 1}, "misses": 0},
        "per_hour": [
            {
                "hour": 1,
                "campaign": "C3",
                "T_C": 1320.0,
                "reduced_real_cache_state": "cached_exact",
            }
        ],
    }
    assert "raw" not in payload


def test_cache_trace_payload_preserves_compact_reduced_real_profile() -> None:
    run_execution = SimpleNamespace(
        per_hour=(
            {
                "hour": 1,
                "campaign": "C3",
                "T_C": 1320.0,
                "reduced_real_cache_state": "cached_exact",
            },
            {
                "hour": 2,
                "campaign": "C3",
                "T_C": 1380.0,
                "reduced_real_cache_state": "live_fill",
            },
        ),
        reduced_real_cache={
            "cache_state_counts": {"cached_exact": 1, "live_fill": 1},
            "misses": 0,
        },
        trace={"raw": "fallback"},
    )

    payload = _cache_trace_payload(run_execution, {"composition_target": {"metric": "x"}})

    assert payload["composition_target"] == {"metric": "x"}
    assert payload["reduced_real_cache"]["cache_state_counts"]["live_fill"] == 1
    assert payload["per_hour"] == [
        {
            "hour": 1,
            "campaign": "C3",
            "T_C": 1320.0,
            "reduced_real_cache_state": "cached_exact",
        },
        {
            "hour": 2,
            "campaign": "C3",
            "T_C": 1380.0,
            "reduced_real_cache_state": "live_fill",
        },
    ]
