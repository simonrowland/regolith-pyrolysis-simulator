"""Focused tests for corpus-version pruning in the optimizer result store."""

from __future__ import annotations

import sqlite3
import json

import pytest

from simulator.optimize import cli
from simulator.optimize.discovery import optimizer_run_dirs
from simulator.optimize.results_store import (
    ResultStore,
    ResultStoreWriteLock,
    ResultStoreWriteLockTimeout,
    study_results_write_lock_path,
)
from simulator.optimize import results_store as results_store_module


def _store_with_versions(tmp_path, *, timeout_ms=30_000):
    path = tmp_path / "cache.sqlite"
    store = ResultStore(path, write_lock_timeout_ms=timeout_ms)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        for key, version in (
            ("stale-a", "old"),
            ("current", "current"),
            ("older-compatible", "older"),
            ("unversioned", None),
        ):
            conn.execute(
                """
                INSERT INTO results (
                    cache_key, feedstock_id, recipe_id, profile_id, fidelity,
                    code_version, corpus_version, data_digests, result_scope,
                    feasible, objectives, feasibility_margins, failing_gates,
                    result_blob, run_reference, eval_spec, notes, created_at
                ) VALUES (?, 'feed', 'recipe', 'profile', 'fast', 'code', ?, '{}',
                    '{}', 1, '[]', '{}', '[]', '{}', '{}', '{}', '[]', 'now')
                """,
                (key, version),
            )
            conn.execute(
                "INSERT INTO objective_values "
                "(cache_key, metric, sense, value, units, ordinal) "
                "VALUES (?, 'yield', 'maximize', 1.0, 'kg', 0)",
                (key,),
            )
    return store


def _keys(store):
    with sqlite3.connect(store.path) as conn:
        return {row[0] for row in conn.execute("SELECT cache_key FROM results")}


def test_dry_run_reports_counts_without_deleting(tmp_path):
    store = _store_with_versions(tmp_path)

    counts = store.prune_stale_corpus_version(("current", "older"))

    assert counts == {"stale": 1, "kept": 2, "unversioned": 1}
    assert _keys(store) == {"stale-a", "current", "older-compatible", "unversioned"}


def test_apply_deletes_only_stale_and_keeps_current_and_unversioned(tmp_path):
    store = _store_with_versions(tmp_path)

    counts = store.prune_stale_corpus_version(("current", "older"), apply=True)

    assert counts == {"stale": 1, "kept": 2, "unversioned": 1}
    assert _keys(store) == {"current", "older-compatible", "unversioned"}
    with sqlite3.connect(store.path) as conn:
        assert {row[0] for row in conn.execute("SELECT cache_key FROM objective_values")} == {
            "current", "older-compatible", "unversioned"
        }


def test_second_apply_is_noop(tmp_path):
    store = _store_with_versions(tmp_path)

    store.prune_stale_corpus_version(("current", "older"), apply=True)
    counts = store.prune_stale_corpus_version(("current", "older"), apply=True)

    assert counts == {"stale": 0, "kept": 2, "unversioned": 1}


def test_prune_honours_result_store_write_lock(tmp_path):
    store = _store_with_versions(tmp_path, timeout_ms=1)

    store.write_lock_path = study_results_write_lock_path(tmp_path)
    with store.write_lock():
        with pytest.raises(ResultStoreWriteLockTimeout):
            store.prune_stale_corpus_version(("current", "older"), apply=True)
    assert _keys(store) == {"stale-a", "current", "older-compatible", "unversioned"}


def test_cli_apply_honours_study_results_lock(tmp_path, monkeypatch):
    store = _store_with_versions(tmp_path, timeout_ms=1)
    monkeypatch.setattr(cli, "interoperable_corpus_versions", lambda: ("current", "older"))
    monkeypatch.setattr(
        cli,
        "ResultStore",
        lambda path, **kwargs: ResultStore(path, write_lock_timeout_ms=1, **kwargs),
    )
    lock_path = study_results_write_lock_path(tmp_path)

    with ResultStoreWriteLock(lock_path, timeout_ms=1):
        with pytest.raises(ResultStoreWriteLockTimeout):
            cli.main(["prune-stale-corpus", "--store", str(store.path), "--apply"])

    assert _keys(store) == {"stale-a", "current", "older-compatible", "unversioned"}


def test_cli_prints_human_summary_and_json_line(tmp_path, monkeypatch, capsys):
    store = _store_with_versions(tmp_path)
    monkeypatch.setattr(cli, "interoperable_corpus_versions", lambda: ("current", "older"))

    assert cli.main(["prune-stale-corpus", "--store", str(store.path)]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert "would delete 1 stale" in lines[0]
    report = json.loads(lines[-1])
    assert report["stores"] == [{
        "store": str(store.path),
        "artifact_kind": "result_rows",
        "stale": 1,
        "kept": 2,
        "unversioned": 1,
        "deleted": 0,
    }]
    assert _keys(store) == {"stale-a", "current", "older-compatible", "unversioned"}


def test_version_filter_mutation_probe_detects_unfiltered_delete(tmp_path, monkeypatch):
    store = _store_with_versions(tmp_path)

    monkeypatch.setattr(
        results_store_module,
        "_stale_corpus_predicate",
        lambda versions: ("1=1", ()),
    )
    with pytest.raises(AssertionError):
        store.prune_stale_corpus_version(("current", "older"), apply=True)
        assert _keys(store) == {"current", "older-compatible", "unversioned"}
        with sqlite3.connect(store.path) as conn:
            assert {row[0] for row in conn.execute("SELECT cache_key FROM objective_values")} == {
                "current", "older-compatible", "unversioned"
            }


def test_cli_dry_run_leaves_database_bytes_and_mtime_unchanged(tmp_path, monkeypatch):
    store = _store_with_versions(tmp_path)
    monkeypatch.setattr(cli, "interoperable_corpus_versions", lambda: ("current", "older"))
    before_bytes = store.path.read_bytes()
    before_mtime = store.path.stat().st_mtime_ns

    assert cli.main(["prune-stale-corpus", "--store", str(store.path)]) == 0

    assert store.path.read_bytes() == before_bytes
    assert store.path.stat().st_mtime_ns == before_mtime


def test_dry_run_counts_use_one_read_transaction(tmp_path, monkeypatch):
    store = _store_with_versions(tmp_path)
    connect = results_store_module.connect_readonly
    statements = []

    class RecordingConnection:
        def __init__(self, path):
            self.connection = connect(path)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            self.connection.close()

        def execute(self, sql, parameters=()):
            statements.append(sql.strip().upper())
            return self.connection.execute(sql, parameters)

    monkeypatch.setattr(
        results_store_module,
        "connect_readonly",
        lambda path: RecordingConnection(path),
    )

    counts = ResultStore.count_stale_corpus_versions(
        store.path, ("current", "older")
    )

    assert counts == {"stale": 1, "kept": 2, "unversioned": 1}
    assert statements[0] == "BEGIN"
    assert statements.count("BEGIN") == 1


def test_apply_keeps_older_interoperable_corpus_version(tmp_path, monkeypatch):
    store = _store_with_versions(tmp_path)
    monkeypatch.setattr(cli, "interoperable_corpus_versions", lambda: ("current", "older"))

    assert cli.main(["prune-stale-corpus", "--store", str(store.path), "--apply"]) == 0

    assert _keys(store) == {"current", "older-compatible", "unversioned"}


def test_apply_uses_actual_shared_corpus_version_config(tmp_path, monkeypatch):
    config = tmp_path / "corpus-version.yaml"
    config.write_text(
        "corpus_version: current\ninteroperable_versions:\n  - current\n  - older\n"
    )
    monkeypatch.setenv("REGOLITH_CORPUS_VERSION_FILE", str(config))
    store = _store_with_versions(tmp_path)

    assert cli.main(["prune-stale-corpus", "--store", str(store.path), "--apply"]) == 0

    assert _keys(store) == {"current", "older-compatible", "unversioned"}


def test_cli_discovery_matches_web_run_locations_and_runs_override(tmp_path, monkeypatch, capsys):
    root = tmp_path / "configured-runs"
    paths = [
        root / "cache.sqlite",
        root / "run-a" / "cache.sqlite",
        root / "jobs" / "job-a" / "cache.sqlite",
    ]
    for path in paths:
        ResultStore(path)
    imported = root / "imported" / "study-a" / "cache.sqlite"
    imported.parent.mkdir(parents=True)
    imported.write_bytes(b"hash-indexed archive member")
    (imported.parent / "import.overlay.json").write_text("{}")
    monkeypatch.setenv("OPTIMIZER_RUNS_DIR", str(root))
    monkeypatch.setattr(cli, "interoperable_corpus_versions", lambda: ("current",))

    assert {p / "cache.sqlite" for p in optimizer_run_dirs(root)} == set(paths)
    assert cli.main(["prune-stale-corpus"]) == 0
    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert {item["store"] for item in report["stores"]} == {
        *(str(path) for path in paths), str(imported.resolve())
    }


def test_explicit_import_archive_is_skipped_without_modification(tmp_path, capsys):
    path = tmp_path / "runs" / "imported" / "study-a" / "cache.sqlite"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"hash-indexed archive member")
    before_mtime = path.stat().st_mtime_ns

    assert cli.main(["prune-stale-corpus", "--store", str(path), "--apply"]) == 0

    assert path.read_bytes() == b"hash-indexed archive member"
    assert path.stat().st_mtime_ns == before_mtime
    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert report["stores"] == [{
        "store": str(path), "artifact_kind": "archive", "status": "skipped"
    }]


def test_moved_archive_marker_is_skipped_before_store_open(tmp_path, capsys):
    original = _store_with_versions(tmp_path / "quarantine")
    archive_dir = tmp_path / "moved" / "renamed-study"
    archive_dir.parent.mkdir()
    original.path.parent.rename(archive_dir)
    path = archive_dir / "cache.sqlite"
    (archive_dir / "import.overlay.json").write_text("{}")
    before = path.read_bytes(), path.stat().st_mtime_ns

    assert cli.main(["prune-stale-corpus", "--store", str(path), "--apply"]) == 0

    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["stores"] == [{
        "store": str(path.resolve()), "artifact_kind": "archive", "status": "skipped"
    }]


def test_symlink_store_uses_real_database_study_lock(tmp_path, monkeypatch):
    store = _store_with_versions(tmp_path / "native")
    alias = tmp_path / "alias.sqlite"
    alias.symlink_to(store.path)
    monkeypatch.setattr(cli, "interoperable_corpus_versions", lambda: ("current", "older"))

    with ResultStoreWriteLock(study_results_write_lock_path(store.path.parent), timeout_ms=1):
        with pytest.raises(ResultStoreWriteLockTimeout):
            cli.main(["prune-stale-corpus", "--store", str(alias), "--apply"])


def test_prune_cli_rejects_keep_version_override():
    with pytest.raises(SystemExit):
        cli.main(["prune-stale-corpus", "--keep-corpus-version", "current"])


def test_default_report_keeps_pareto_and_lists_quarantined_store(tmp_path, monkeypatch, capsys):
    root = tmp_path / "runs"
    native = root / "run-a"
    ResultStore(native / "cache.sqlite")
    (native / "pareto.json").write_text("{}")
    quarantined = root / "imported" / "study-a"
    quarantined.mkdir(parents=True)
    (quarantined / "cache.sqlite").write_bytes(b"archive")
    (quarantined / "artifact.index.json").write_text("{}")
    monkeypatch.setenv("OPTIMIZER_RUNS_DIR", str(root))

    assert cli.main(["prune-stale-corpus"]) == 0

    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert {entry["artifact_kind"] for entry in report["stores"]} == {
        "result_rows", "archive"
    }
    assert report["retained_artifacts"] == [{
        "path": str(native / "pareto.json"), "artifact_kind": "warm_start", "status": "kept"
    }]
