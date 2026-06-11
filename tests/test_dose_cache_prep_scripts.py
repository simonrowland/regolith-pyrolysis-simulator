from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.analyze_dose_cache_study import load_provenance, summarize
from scripts.seed_reduced_real_cache import (
    CacheSourceSchemaMismatch,
    payload_count,
    seed_cache,
)
from simulator.optimize.evaluate import _cache_trace_payload
from simulator.reduced_real_determinism import (
    PT1_METADATA_TABLE,
    PT1_STORE_SCHEMA_VERSION,
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
