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


def _put_cache_row(
    db_path: Path,
    *,
    tag: str,
    with_vapor_authority: bool = False,
) -> None:
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
    if with_vapor_authority:
        key["vapor_pressure_provider_selection"] = "builtin-vapor-pressure"
        payload["authority"] = {
            "vapor_pressure": {
                "resolved_provider_id": "builtin-vapor-pressure",
                "resolved_role": "authoritative",
                "authoritative_provider_id": "builtin-vapor-pressure",
                "fallback_provider_id": None,
                "fallback_allowed": False,
            },
        }
    key_bytes = canonical_json_bytes(key)
    payload_bytes = canonical_json_bytes(payload)
    PT1PersistentEquilibriumStore(db_path).put(
        artifact="freeze_gate_curve",
        key=key,
        key_bytes=key_bytes,
        key_hash=hashlib.sha256(key_bytes).hexdigest(),
        payload=payload,
        payload_bytes=payload_bytes,
        payload_hash=hashlib.sha256(payload_bytes).hexdigest(),
    )


def _write_invalid_current_payload_bytes(db_path: Path) -> None:
    payload_bytes = b"{invalid json"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
            "SET payload_bytes = ?, payload_sha256 = ?, payload_codec_version = ?",
            (
                sqlite3.Binary(payload_bytes),
                hashlib.sha256(payload_bytes).hexdigest(),
                rrd._payload_codec_version("freeze_gate_curve"),
            ),
        )


def _mark_authorityless_v3_gate_row(
    db_path: Path,
    *,
    preserve_key: bool = False,
) -> None:
    with sqlite3.connect(db_path) as conn:
        old_key_hash, key_bytes, payload_bytes = conn.execute(
            f"SELECT key_hash, key_bytes, payload_bytes FROM {PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()
        key = json.loads(bytes(key_bytes).decode("utf-8"))
        payload = json.loads(bytes(payload_bytes).decode("utf-8"))
        payload.pop("authority", None)
        if not preserve_key:
            key["vapor_pressure_provider_selection"] = "builtin-vapor-pressure"
        current_key_bytes = canonical_json_bytes(key)
        current_key_hash = hashlib.sha256(current_key_bytes).hexdigest()
        current_payload_bytes = canonical_json_bytes(payload)
        conn.execute(
            f"UPDATE {PT1_EQUILIBRIUM_TABLE} "
            "SET key_hash = ?, key_bytes = ?, key_sha256 = ?, "
            "payload_bytes = ?, payload_sha256 = ?, payload_codec_version = ? "
            "WHERE key_hash = ?",
            (
                current_key_hash,
                sqlite3.Binary(current_key_bytes),
                current_key_hash,
                sqlite3.Binary(current_payload_bytes),
                hashlib.sha256(current_payload_bytes).hexdigest(),
                rrd.PT0_GATE_CURVE_CODEC_VERSION - 1,
                old_key_hash,
            ),
        )


def _put_real_equilibrium_cache_row(db_path: Path) -> None:
    from tests.test_reduced_real_pt0_determinism import (
        _pt0_equilibrium_capture_context,
    )

    capture = rrd.PT0DeterminismStore("capture")
    sim, result = _pt0_equilibrium_capture_context(
        capture,
        strict_vapor_default=True,
    )
    sim._last_vapor_pressures_source = {"Na": "builtin_authoritative"}
    key = capture._equilibrium_key(sim)
    payload = rrd.equilibrium_payload(sim, result)
    vapor_authority = payload["authority"]["vapor_pressure"]
    assert vapor_authority["fallback_allowed"] is False
    assert key["vapor_pressure_provider_selection"] == (
        vapor_authority["resolved_provider_id"]
    )
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
                request_schema_version, payload_codec_version,
                key_sha256, payload_sha256,
                key_bytes, payload_bytes, code_version, corpus_version,
                engine_version, data_digests_json, created_at, git_dirty
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key_hash,
                "equilibrium_post_record",
                PT1_STORE_SCHEMA_VERSION,
                SCHEMA_VERSION,
                rrd._payload_codec_version("equilibrium_post_record"),
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
def test_seed_cache_skips_legacy_and_preserves_current_payload_codec_version(
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

    summary = seed_cache(target, [source])

    if drop_codec_column:
        assert summary["sources"][0]["source_rows"] == 1
        assert summary["sources"][0]["superseded_rows"] == 1
        assert summary["sources"][0]["inserted_rows"] == 0
        assert payload_count(target) == 0
        return

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


@pytest.mark.parametrize("transport", ("seed", "merge"))
def test_cache_merge_skips_and_reports_superseded_gate_source_rows(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    transport: str,
) -> None:
    source = tmp_path / f"{transport}-codec-v3-source.db"
    target = tmp_path / f"{transport}-codec-v3-target.db"
    _put_cache_row(source, tag=f"{transport}-codec-v3")
    _mark_authorityless_v3_gate_row(source)

    if transport == "seed":
        result = seed_cache(target, [source])
        assert result["sources"][0]["source_rows"] == 1
        assert result["sources"][0]["superseded_rows"] == 1
        assert result["sources"][0]["inserted_rows"] == 0
    else:
        assert merge_grind_cache.main([str(target), str(source)]) == 0
        assert "source_rows=1 superseded_rows=1 inserted=0" in capsys.readouterr().out

    assert payload_count(target) == 0


@pytest.mark.parametrize("transport", ("seed", "merge"))
def test_cache_merge_admission_and_counts_are_per_source_for_shared_key(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    transport: str,
) -> None:
    stale_source = tmp_path / "a-stale-source.db"
    current_source = tmp_path / "b-current-source.db"
    target = tmp_path / f"{transport}-target.db"
    _put_cache_row(
        current_source,
        tag="shared-key",
        with_vapor_authority=True,
    )
    shutil.copyfile(current_source, stale_source)
    _mark_authorityless_v3_gate_row(stale_source, preserve_key=True)

    with sqlite3.connect(stale_source) as stale_conn, sqlite3.connect(
        current_source
    ) as current_conn:
        stale_key_hash = stale_conn.execute(
            f"SELECT key_hash FROM {PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()[0]
        current_key_hash = current_conn.execute(
            f"SELECT key_hash FROM {PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()[0]
        current_payload = current_conn.execute(
            f"SELECT payload_bytes FROM {PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()[0]
    assert stale_key_hash == current_key_hash

    if transport == "seed":
        result = seed_cache(target, [stale_source, current_source])
        stale_summary, current_summary = result["sources"]
        assert stale_summary["superseded_rows"] == 1
        assert stale_summary["inserted_rows"] == 0
        assert current_summary["superseded_rows"] == 0
        assert current_summary["inserted_rows"] == 1
    else:
        assert merge_grind_cache.main(
            [str(target), str(stale_source), str(current_source)]
        ) == 0
        output = capsys.readouterr().out
        assert "a-stale-source.db: source_rows=1 superseded_rows=1 inserted=0" in output
        assert "b-current-source.db: source_rows=1 superseded_rows=0 inserted=1" in output

    with sqlite3.connect(target) as conn:
        stored_key_hash, stored_payload, stored_codec_version = conn.execute(
            f"SELECT key_hash, payload_bytes, payload_codec_version "
            f"FROM {PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()
    assert stored_key_hash == current_key_hash
    assert bytes(stored_payload) == bytes(current_payload)
    assert stored_codec_version == rrd.PT0_GATE_CURVE_CODEC_VERSION


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

    with pytest.raises(rrd.PT1PersistentStoreCorrupt, match="payload hash mismatch"):
        seed_cache(target, [valid, invalid])

    assert target.read_bytes() == before


@pytest.mark.parametrize("transport", ("seed", "merge"))
def test_current_invalid_source_payload_uses_shared_pt1_integrity_classifier(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    transport: str,
) -> None:
    target = tmp_path / f"{transport}-target.db"
    source = tmp_path / f"{transport}-invalid-current-source.db"
    _put_cache_row(target, tag=f"{transport}-existing")
    _put_cache_row(source, tag=f"{transport}-invalid")
    _write_invalid_current_payload_bytes(source)
    before = target.read_bytes()

    if transport == "seed":
        with pytest.raises(
            rrd.PT1PersistentStoreCorrupt,
            match="current payload fails codec admission",
        ) as caught:
            seed_cache(target, [source])
        assert isinstance(caught.value.__cause__, json.JSONDecodeError)
    else:
        with pytest.raises(
            rrd.PT1PersistentStoreCorrupt,
            match="current payload fails codec admission",
        ) as caught:
            merge_grind_cache.validate_cache_source_rows(source)
        assert isinstance(caught.value.__cause__, json.JSONDecodeError)

        rc = merge_grind_cache.main([str(target), str(source)])
        captured = capsys.readouterr()
        assert rc == 2
        assert "current payload fails codec admission" in captured.err

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
