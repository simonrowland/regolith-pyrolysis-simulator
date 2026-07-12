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
from simulator.corpus_version import current_corpus_version
from simulator.optimize.evaluate import _cache_trace_payload
from simulator.reduced_real_determinism import (
    PT1_EQUILIBRIUM_TABLE,
    PT1_METADATA_TABLE,
    PT1_STORE_SCHEMA_VERSION,
    SCHEMA_VERSION,
    PT1PersistentEquilibriumStore,
    canonical_json_bytes,
)


def _put_cache_row(db_path: Path, *, tag: str) -> None:
    key = {
        "artifact": "freeze_gate_curve",
        "code_version": "test",
        "data_digests": {"fixture": "v1"},
        "schema_version": "test",
        "tag": tag,
    }
    payload = {"curve": {"status": "in_range", "tag": tag}}
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


def _put_internal_analytical_cache_row(db_path: Path) -> None:
    corpus_version = current_corpus_version()
    key = {
        "schema_version": SCHEMA_VERSION,
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
    payload = {"equilibrium_result": {"status": "ok"}}
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
    assert "builtin-backend-equilibrium" in captured.err
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

    with pytest.raises(RuntimeError, match="builtin-backend-equilibrium"):
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
