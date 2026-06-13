from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import queue
import sqlite3
from pathlib import Path

import pytest

from scripts import epoch_grind
from scripts.seed_reduced_real_cache import payload_count, seed_cache
from simulator.backends import build_cached_real_store, normalize_cached_real_config
from simulator.reduced_real_determinism import (
    DEFAULT_SHARD_BUSY_TIMEOUT_MS,
    PT0DeterminismStore,
    PT1PersistentEquilibriumStore,
    PT1_READ_ONLY_BASE_ALIAS,
    canonical_json_bytes,
    canonical_physics_bucket_key_from_replay_key,
)


def _put_cache_row(
    db_path: Path,
    *,
    tag: str,
    artifact: str = "freeze_gate_curve",
) -> dict:
    key = {
        "artifact": artifact,
        "code_version": "test",
        "data_digests": {"fixture": "v1"},
        "schema_version": "test",
        "tag": tag,
    }
    payload = {"curve": {"status": "in_range", "tag": tag}}
    key_bytes = canonical_json_bytes(key)
    payload_bytes = canonical_json_bytes(payload)
    PT1PersistentEquilibriumStore(db_path).put(
        artifact=artifact,
        key=key,
        key_bytes=key_bytes,
        key_hash=hashlib.sha256(key_bytes).hexdigest(),
        payload=payload,
        payload_bytes=payload_bytes,
        payload_hash=hashlib.sha256(payload_bytes).hexdigest(),
    )
    return key


def _db_bytes(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def test_read_only_base_attach_exact_hit(tmp_path: Path) -> None:
    base = tmp_path / "base.sqlite"
    shard = tmp_path / "shard.sqlite"
    key = _put_cache_row(base, tag="from-base")
    PT1PersistentEquilibriumStore(shard)

    store = PT0DeterminismStore(
        "capture",
        db_path=shard,
        read_only_base_db_path=base,
    )
    store.cache_tier_ceiling = "cached_exact"
    payload = store._lookup_optional(
        str(key["artifact"]),
        key,
        physics_bucket_key=canonical_physics_bucket_key_from_replay_key(key),
    )

    assert payload is not None
    assert payload["curve"]["tag"] == "from-base"
    assert store.last_cache_state == "cached_exact"
    assert payload_count(shard) == 0


def test_shard_holds_only_new_rows_after_write(tmp_path: Path) -> None:
    base = tmp_path / "base.sqlite"
    shard = tmp_path / "shard.sqlite"
    for index in range(5):
        _put_cache_row(base, tag=f"base-{index}")
    base_rows = payload_count(base)
    base_bytes = _db_bytes(base)

    shard_store = PT1PersistentEquilibriumStore(shard, read_only_base_db_path=base)
    new_key = _put_cache_row(shard, tag="new-only")

    assert payload_count(shard) == 1
    assert payload_count(base) == base_rows
    assert _db_bytes(base) == base_bytes
    assert _db_bytes(shard) < base_bytes

    hit = shard_store.get(
        artifact=str(new_key["artifact"]),
        key=new_key,
        key_bytes=canonical_json_bytes(new_key),
        key_hash=hashlib.sha256(canonical_json_bytes(new_key)).hexdigest(),
    )
    assert hit is not None
    assert hit["payload"]["curve"]["tag"] == "new-only"


def test_base_not_written_by_job_store(tmp_path: Path) -> None:
    base = tmp_path / "base.sqlite"
    shard = tmp_path / "shard.sqlite"
    _put_cache_row(base, tag="seed")
    base_mtime = os.path.getmtime(base)
    base_rows = payload_count(base)

    PT1PersistentEquilibriumStore(shard)
    build_cached_real_store(
        normalize_cached_real_config(
            {
                "db_path": str(shard),
                "read_only_base_db_path": str(base),
                "authorized_backend_name": "magemin",
                "authorized_backend_version": "test",
                "miss_policy": "live-fill",
                "cache_tier_ceiling": "cached_exact",
            }
        )
    )
    _put_cache_row(shard, tag="job-write")

    assert payload_count(base) == base_rows
    assert os.path.getmtime(base) == pytest.approx(base_mtime, abs=1.0)
    assert payload_count(shard) == 1


def test_seed_job_cache_creates_empty_shard_without_base_copy(tmp_path: Path) -> None:
    base = tmp_path / "base.sqlite"
    shard = tmp_path / "epoch" / "shards" / "job-a.sqlite"
    for index in range(3):
        _put_cache_row(base, tag=f"base-{index}")

    summary = epoch_grind.seed_job_cache(shard, base)

    assert summary["seed_rows"] == 0
    assert summary["rows_after"] == 0
    assert payload_count(shard) == 0
    assert payload_count(base) == 3


def test_duplication_rate_with_zero_seed_rows() -> None:
    summary = {
        "inserted_rows": 2,
        "sources": [
            {"source_rows": 5, "seed_rows": 0, "inserted_rows": 2},
            {"source_rows": 3, "seed_rows": 0, "inserted_rows": 0},
        ],
    }
    assert epoch_grind.duplication_rate_from_merge(summary) == pytest.approx(0.75)


def test_seed_rows_zero_merge_summary_accounting() -> None:
    assert epoch_grind.duplication_rate_from_merge(
        {
            "inserted_rows": 2,
            "sources": [
                {
                    "source": "shard-a.sqlite",
                    "source_rows": 5,
                    "seed_rows": 0,
                    "inserted_rows": 2,
                }
            ],
        }
    ) == pytest.approx(0.6)
    assert epoch_grind.duplication_rate_from_merge(
        {
            "inserted_rows": 0,
            "sources": [{"source_rows": 0, "seed_rows": 0, "inserted_rows": 0}],
        }
    ) == pytest.approx(0.0)
    with pytest.raises(ValueError, match=r"merge accounting is corrupt"):
        epoch_grind.duplication_rate_from_merge(
            {
                "inserted_rows": 0,
                "sources": [
                    {
                        "source": "shard-a.sqlite",
                        "source_rows": -1,
                        "seed_rows": 0,
                        "inserted_rows": 0,
                    }
                ],
            }
        )


def _filesystem_is_case_insensitive() -> bool:
    probe = Path(os.environ.get("TMPDIR", "/tmp")) / "case_probe_guard"
    try:
        probe.write_text("x", encoding="utf-8")
        alt = probe.parent / probe.name.swapcase()
        if not alt.exists():
            return False
        return os.stat(probe).st_ino == os.stat(alt).st_ino
    finally:
        probe.unlink(missing_ok=True)


def test_same_path_base_attach_raises(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite"
    with pytest.raises(ValueError, match=r"must not equal db_path"):
        PT1PersistentEquilibriumStore(db_path, read_only_base_db_path=db_path)


@pytest.mark.skipif(
    not _filesystem_is_case_insensitive(),
    reason="case-sensitive filesystem",
)
def test_case_variant_same_path_base_attach_raises(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite"
    alt_base = tmp_path / "Cache.sqlite"
    db_path.touch()
    with pytest.raises(ValueError, match=r"must not equal db_path"):
        PT1PersistentEquilibriumStore(db_path, read_only_base_db_path=alt_base)


def test_missing_base_path_logs_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    shard = tmp_path / "shard.sqlite"
    missing_base = tmp_path / "missing-base.sqlite"

    with caplog.at_level("WARNING"):
        PT1PersistentEquilibriumStore(shard, read_only_base_db_path=missing_base)

    assert any(
        "read_only_base_db_path does not exist" in record.message
        and str(missing_base) in record.message
        for record in caplog.records
    )


def test_prune_merged_shards_after_integrity_check(tmp_path: Path) -> None:
    base = tmp_path / "base.sqlite"
    shard_a = tmp_path / "shard-a.sqlite"
    shard_b = tmp_path / "shard-b.sqlite"
    PT1PersistentEquilibriumStore(base)
    _put_cache_row(shard_a, tag="a")
    _put_cache_row(shard_b, tag="b")

    merge_summary = epoch_grind.merge_epoch_shards(
        base,
        [shard_a, shard_b],
        seed_rows_by_source={str(shard_a): 0, str(shard_b): 0},
    )
    pruned = epoch_grind.prune_merged_shards(
        [shard_a, shard_b],
        base,
        merge_summary=merge_summary,
    )

    assert pruned == [str(shard_a), str(shard_b)]
    assert not shard_a.exists()
    assert not shard_b.exists()
    row = sqlite3.connect(base).execute("PRAGMA integrity_check").fetchone()
    assert row is not None and row[0] == "ok"


def test_prune_merged_shards_unlinks_wal_sidecars(tmp_path: Path) -> None:
    base = tmp_path / "base.sqlite"
    shard = tmp_path / "shard.sqlite"
    PT1PersistentEquilibriumStore(base)
    _put_cache_row(shard, tag="with-sidecars")

    merge_summary = epoch_grind.merge_epoch_shards(
        base,
        [shard],
        seed_rows_by_source={str(shard): 0},
    )
    for suffix in ("-wal", "-shm"):
        shard.with_name(shard.name + suffix).touch()

    pruned = epoch_grind.prune_merged_shards(
        [shard],
        base,
        merge_summary=merge_summary,
    )

    assert pruned == [str(shard)]
    assert not shard.exists()
    assert not shard.with_name(shard.name + "-wal").exists()
    assert not shard.with_name(shard.name + "-shm").exists()


def test_prune_merged_shards_succeeds_without_sidecars(tmp_path: Path) -> None:
    base = tmp_path / "base.sqlite"
    shard = tmp_path / "shard.sqlite"
    PT1PersistentEquilibriumStore(base)
    _put_cache_row(shard, tag="no-sidecars")

    merge_summary = epoch_grind.merge_epoch_shards(
        base,
        [shard],
        seed_rows_by_source={str(shard): 0},
    )
    assert not shard.with_name(shard.name + "-wal").exists()
    assert not shard.with_name(shard.name + "-shm").exists()

    pruned = epoch_grind.prune_merged_shards(
        [shard],
        base,
        merge_summary=merge_summary,
    )

    assert pruned == [str(shard)]
    assert not shard.exists()


def test_prune_skips_shard_on_partial_merge(tmp_path: Path) -> None:
    base = tmp_path / "base.sqlite"
    shard = tmp_path / "shard.sqlite"
    PT1PersistentEquilibriumStore(base)
    _put_cache_row(shard, tag="unmerged")

    partial_summary = {
        "inserted_rows": 0,
        "sources": [
            {
                "source": str(shard),
                "source_rows": 1,
                "seed_rows": 0,
                "inserted_rows": 0,
            }
        ],
    }
    pruned = epoch_grind.prune_merged_shards(
        [shard],
        base,
        merge_summary=partial_summary,
    )

    assert pruned == []
    assert shard.exists()
    assert payload_count(base) == 0


def test_merge_epoch_shards_and_prune_integration(tmp_path: Path) -> None:
    base = tmp_path / "base.sqlite"
    shard = tmp_path / "shard.sqlite"
    PT1PersistentEquilibriumStore(base)
    _put_cache_row(shard, tag="merge-me")

    summary = epoch_grind.merge_epoch_shards(
        base,
        [shard],
        seed_rows_by_source={str(shard): 0},
    )
    pruned = epoch_grind.prune_merged_shards(
        [shard],
        base,
        merge_summary=summary,
    )

    assert summary["inserted_rows"] == 1
    assert payload_count(base) == 1
    assert pruned == [str(shard)]
    assert epoch_grind.duplication_rate_from_merge(summary) == pytest.approx(0.0)


def test_multi_job_shard_scratch_bounded_not_n_times_base(tmp_path: Path) -> None:
    base = tmp_path / "base.sqlite"
    for index in range(10):
        _put_cache_row(base, tag=f"base-{index}")
    base_bytes = _db_bytes(base)

    shard_paths: list[Path] = []
    for job_id in ("job-a", "job-b", "job-c"):
        shard = tmp_path / "epoch-0001" / "shards" / f"{job_id}.sqlite"
        epoch_grind.seed_job_cache(shard, base)
        _put_cache_row(shard, tag=f"new-{job_id}")
        shard_paths.append(shard)

    total_shard_bytes = sum(_db_bytes(path) for path in shard_paths)
    assert total_shard_bytes < 3 * base_bytes
    assert all(payload_count(path) == 1 for path in shard_paths)
    assert payload_count(base) == 10


def test_epoch_profile_wires_read_only_base_path(tmp_path: Path) -> None:
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps(
            {
                "profile_id": "test",
                "profile_schema_version": "optimizer-profile-v1",
                "feedstock": "lunar_mare_low_ti",
                "objectives": {},
                "constraints": {},
                "run": {
                    "backend_name": "cached-real",
                    "reduced_real_cache": {
                        "db_path": "old.sqlite",
                        "authorized_backend_name": "magemin",
                        "authorized_backend_version": "test",
                    },
                },
                "fidelities": {"fast": {}},
                "seed_recipes": [],
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "jobs.json"
    manifest.write_text(
        json.dumps(
            {
                "base_cache": "base.sqlite",
                "work_dir": "epochs",
                "fidelity": "fast",
                "parallel": 1,
                "jobs": [
                    {
                        "id": "job-a",
                        "feedstock": "lunar_mare_low_ti",
                        "profile": str(profile),
                        "budget": 4,
                        "strategy": "random",
                        "seed": 1,
                        "out": "runs/job-a",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    loaded = epoch_grind.load_manifest(manifest)
    PT1PersistentEquilibriumStore(loaded.base_cache)
    shard = tmp_path / "epoch-0001" / "shards" / "job-a.sqlite"
    _, overlay = epoch_grind.plan_epoch_profile(
        loaded.jobs[0],
        tmp_path,
        shard,
        tmp_path / "epoch-0001",
        base_cache=loaded.base_cache,
    )

    assert overlay is not None
    cache = overlay["run"]["reduced_real_cache"]
    assert cache["db_path"] == str(shard)
    assert cache["read_only_base_db_path"] == str(loaded.base_cache)


def _process_shard_put_worker(
    shard_path: str,
    base_path: str,
    worker_idx: int,
    count: int,
    start: multiprocessing.synchronize.Event,
    errors: multiprocessing.queues.Queue,
    *,
    shard_busy_timeout_ms: float,
) -> None:
    try:
        store = PT1PersistentEquilibriumStore(
            Path(shard_path),
            read_only_base_db_path=Path(base_path),
            shard_busy_timeout_ms=shard_busy_timeout_ms,
        )
        start.wait(30)
        for index in range(count):
            tag = f"w{worker_idx}-{index}"
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
            store.put(
                artifact="freeze_gate_curve",
                key=key,
                key_bytes=key_bytes,
                key_hash=hashlib.sha256(key_bytes).hexdigest(),
                payload=payload,
                payload_bytes=payload_bytes,
                payload_hash=hashlib.sha256(payload_bytes).hexdigest(),
            )
    except BaseException as exc:  # pragma: no cover - asserted in parent process
        errors.put(repr(exc))


def _collect_process_errors(
    processes: list[multiprocessing.Process],
    errors: multiprocessing.queues.Queue,
) -> list[str]:
    failures: list[str] = []
    for process in processes:
        process.join(timeout=120)
        if process.exitcode != 0:
            failures.append(f"{process.name} exit={process.exitcode}")
    while True:
        try:
            failures.append(errors.get_nowait())
        except queue.Empty:
            break
    return failures


def test_shard_connect_sets_busy_timeout_and_wal_on_main_only(tmp_path: Path) -> None:
    base = tmp_path / "base.sqlite"
    shard = tmp_path / "shard.sqlite"
    _put_cache_row(base, tag="seed")
    base_bytes = _db_bytes(base)

    store = PT1PersistentEquilibriumStore(shard, read_only_base_db_path=base)
    with store._connect() as conn:
        busy_timeout = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
        main_journal = str(conn.execute("PRAGMA main.journal_mode").fetchone()[0])
        base_journal = str(
            conn.execute(
                f"PRAGMA {PT1_READ_ONLY_BASE_ALIAS}.journal_mode"
            ).fetchone()[0]
        )

    assert busy_timeout == int(DEFAULT_SHARD_BUSY_TIMEOUT_MS)
    assert main_journal.lower() == "wal"
    assert base_journal.lower() != "wal"

    _put_cache_row(shard, tag="shard-only")
    assert payload_count(base) == 1
    assert _db_bytes(base) == base_bytes


def test_concurrent_process_shard_puts_without_database_locked(tmp_path: Path) -> None:
    base = tmp_path / "base.sqlite"
    shard = tmp_path / "shard.sqlite"
    for index in range(5):
        _put_cache_row(base, tag=f"base-{index}")
    base_rows = payload_count(base)
    base_bytes = _db_bytes(base)

    epoch_grind.seed_job_cache(shard, base)

    worker_count = 8
    puts_per_worker = 20
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    errors = ctx.Queue()
    processes = [
        ctx.Process(
            target=_process_shard_put_worker,
            args=(
                str(shard),
                str(base),
                worker_idx,
                puts_per_worker,
                start,
                errors,
            ),
            kwargs={"shard_busy_timeout_ms": DEFAULT_SHARD_BUSY_TIMEOUT_MS},
            name=f"shard-put-{worker_idx}",
        )
        for worker_idx in range(worker_count)
    ]

    for process in processes:
        process.start()
    start.set()
    failures = _collect_process_errors(processes, errors)

    lock_failures = [item for item in failures if "database is locked" in item.lower()]
    assert lock_failures == [], failures
    assert failures == []
    assert payload_count(shard) == worker_count * puts_per_worker
    assert payload_count(base) == base_rows
    assert _db_bytes(base) == base_bytes


def test_concurrent_shard_puts_fail_without_busy_timeout_pragma(tmp_path: Path) -> None:
    base = tmp_path / "base.sqlite"
    shard = tmp_path / "shard.sqlite"
    _put_cache_row(base, tag="seed")
    epoch_grind.seed_job_cache(shard, base)

    worker_count = 8
    puts_per_worker = 80
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    errors = ctx.Queue()
    processes = [
        ctx.Process(
            target=_process_shard_put_worker,
            args=(
                str(shard),
                str(base),
                worker_idx,
                puts_per_worker,
                start,
                errors,
            ),
            kwargs={"shard_busy_timeout_ms": 0.0},
            name=f"shard-put-zero-timeout-{worker_idx}",
        )
        for worker_idx in range(worker_count)
    ]

    for process in processes:
        process.start()
    start.set()
    failures = _collect_process_errors(processes, errors)

    lock_failures = [item for item in failures if "database is locked" in item.lower()]
    assert lock_failures, (
        "expected database is locked under busy_timeout=0 concurrent shard writes; "
        f"got failures={failures!r}"
    )