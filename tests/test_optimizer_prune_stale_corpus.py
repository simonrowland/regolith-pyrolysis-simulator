"""Focused tests for corpus-version pruning in the optimizer result store."""

from __future__ import annotations

import hashlib
import json
import runpy
import sqlite3
from contextlib import closing
from pathlib import Path
from threading import Thread

import pytest

from simulator.optimize import cli
from simulator.optimize.discovery import optimizer_run_dirs
from simulator.optimize.results_store import (
    ResultStore,
    ResultStoreWriteLock,
    ResultStoreWriteLockTimeout,
    study_results_write_lock_path,
)
from simulator.optimize import import_bundle
from simulator.optimize import results_store as results_store_module
from simulator.optimize.import_bundle import (
    IMPORTED_DIR_NAME,
    IMPORT_OVERLAY_NAME,
    IMPORT_STAGING_PREFIX,
    OptimizerStoreClassification,
    StagedImportStoreError,
)
from simulator.optimize.save_bundle import (
    ARTIFACT_INDEX_NAME,
    OPTIMIZER_CACHE_NAME,
    REQUIRED_MEMBERS,
    export_study_bundle,
)


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


def test_apply_interrupt_reports_previously_committed_store(
    tmp_path, monkeypatch, capsys
):
    first = _store_with_versions(tmp_path / "first")
    second = _store_with_versions(tmp_path / "second", timeout_ms=30_000)
    monkeypatch.setattr(cli, "interoperable_corpus_versions", lambda: ("current", "older"))

    def interrupt_during_lock_wait(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(results_store_module.time, "sleep", interrupt_during_lock_wait)
    second_lock = study_results_write_lock_path(second.path.parent)
    with ResultStoreWriteLock(second_lock, timeout_ms=1):
        with pytest.raises(KeyboardInterrupt):
            cli.main([
                "prune-stale-corpus",
                "--store", str(first.path),
                "--store", str(second.path),
                "--apply",
            ])

    assert _keys(first) == {"current", "older-compatible", "unversioned"}
    assert _keys(second) == {"stale-a", "current", "older-compatible", "unversioned"}
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("Prune aborted: KeyboardInterrupt:")
    report = json.loads(lines[-1])
    assert report["stores"] == [{
        "store": str(first.path.resolve()),
        "artifact_kind": "result_rows",
        "stale": 1,
        "kept": 2,
        "unversioned": 1,
        "deleted": 1,
    }]
    assert report["abort_reason"] == "KeyboardInterrupt: "


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
    (imported.parent / IMPORT_OVERLAY_NAME).write_text("{}")
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
    (path.parent / IMPORT_OVERLAY_NAME).write_text("{}")
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
    (archive_dir / IMPORT_OVERLAY_NAME).write_text("{}")
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
    monkeypatch.setenv("OPTIMIZER_RUNS_DIR", str(root))

    assert cli.main(["prune-stale-corpus"]) == 0

    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert {entry["artifact_kind"] for entry in report["stores"]} == {
        "result_rows", "archive"
    }
    assert report["retained_artifacts"] == [{
        "path": str(native / "pareto.json"), "artifact_kind": "warm_start", "status": "kept"
    }]


def _assert_import_staging_is_not_discovered(tmp_path, monkeypatch, capsys):
    root = tmp_path / "runs"
    staging_store = root / "imported" / ".import.in-progress" / "cache.sqlite"
    staging_store.parent.mkdir(parents=True)
    staging_store.write_bytes(b"partially published import")
    (staging_store.parent / IMPORT_OVERLAY_NAME).write_text("{}")
    monkeypatch.setenv("OPTIMIZER_RUNS_DIR", str(root))

    assert cli.main(["prune-stale-corpus"]) == 0

    output = capsys.readouterr().out
    report = json.loads(output.splitlines()[-1])
    assert report["stores"] == []


def test_import_staging_store_is_not_discovered(tmp_path, monkeypatch, capsys):
    _assert_import_staging_is_not_discovered(tmp_path, monkeypatch, capsys)


def _assert_explicit_staging_store_is_refused(tmp_path, monkeypatch, capsys):
    root = tmp_path / "runs"
    import_fixtures = runpy.run_path("tests/test_optimizer_import_bundle.py")
    source_store = _store_with_versions(tmp_path / "source")
    indexed_db = tmp_path / "indexed-cache.sqlite"
    with closing(sqlite3.connect(source_store.path)) as conn:
        conn.execute("VACUUM INTO ?", (str(indexed_db),))
    members = import_fixtures["_members"](tmp_path)
    members[OPTIMIZER_CACHE_NAME] = indexed_db.read_bytes()
    bundle = import_fixtures["_write_zip"](tmp_path / "live-import.rpstudy.zip", members)
    monkeypatch.setenv("OPTIMIZER_RUNS_DIR", str(root))
    monkeypatch.setattr(cli, "interoperable_corpus_versions", lambda: ("current", "older"))
    replace = Path.replace
    attempted_report = []
    observed_staging = []

    def prune_before_publish(staging, destination):
        if staging.name.startswith(IMPORT_STAGING_PREFIX):
            staging_store = staging / OPTIMIZER_CACHE_NAME
            assert (staging / IMPORT_OVERLAY_NAME).is_file()
            before_bytes = staging_store.read_bytes()
            before_hash = hashlib.sha256(before_bytes).hexdigest()

            def staging_keys():
                with sqlite3.connect(staging_store) as conn:
                    return {row[0] for row in conn.execute("SELECT cache_key FROM results")}

            before_keys = staging_keys()
            observed_staging.append(staging)
            try:
                cli.main([
                    "prune-stale-corpus", "--store", str(staging_store), "--apply"
                ])
            except StagedImportStoreError as exc:
                assert str(staging.resolve()) in str(exc)
            else:
                assert False, "explicit staging store was not refused"
            assert staging_keys() == before_keys
            assert staging_store.read_bytes() == before_bytes
            assert hashlib.sha256(staging_store.read_bytes()).hexdigest() == before_hash
            attempted_report.append(json.loads(capsys.readouterr().out.splitlines()[-1]))
        return replace(staging, destination)

    monkeypatch.setattr(Path, "replace", prune_before_publish)
    published = import_bundle.import_study_bundle(bundle, root, verification_tier=0)

    assert len(observed_staging) == 1
    assert not observed_staging[0].exists()
    assert attempted_report[0]["stores"] == []
    assert attempted_report[0]["abort_reason"].startswith(
        "StagedImportStoreError: refusing STAGED"
    )
    assert import_bundle.verify_import_hashes(published.path)["verdict"] == "confirmed"


def test_explicit_staging_store_is_refused_without_mutation(tmp_path, monkeypatch, capsys):
    _assert_explicit_staging_store_is_refused(tmp_path, monkeypatch, capsys)


def test_explicit_staging_store_mutation_probe_rejects_old_boolean_rule(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        cli,
        "classify_optimizer_store",
        lambda path, runs_root: OptimizerStoreClassification.NATIVE,
    )
    with pytest.raises(AssertionError):
        _assert_explicit_staging_store_is_refused(tmp_path, monkeypatch, capsys)


def test_apply_reports_committed_stores_when_later_store_is_missing(
    tmp_path, monkeypatch, capsys
):
    store = _store_with_versions(tmp_path / "native")
    root = tmp_path / "runs"
    staging = root / IMPORTED_DIR_NAME / f"{IMPORT_STAGING_PREFIX}publication"
    missing = staging / "cache.sqlite"
    published = root / IMPORTED_DIR_NAME / "study123"
    import_fixtures = runpy.run_path("tests/test_optimizer_import_bundle.py")
    bundle = import_fixtures["_write_zip"](
        tmp_path / "concurrent-import.rpstudy.zip",
        import_fixtures["_members"](tmp_path),
    )

    def make_known_staging_dir(prefix, dir):
        assert prefix == IMPORT_STAGING_PREFIX
        staging.mkdir(parents=True)
        return str(staging)

    monkeypatch.setattr(import_bundle, "mkdtemp", make_known_staging_dir)
    imported = []

    def publish_import():
        imported.append(
            import_bundle.import_study_bundle(bundle, root, verification_tier=0)
        )

    prune_store = ResultStore.prune_stale_corpus_version

    def prune_then_publish(self, versions, *, apply=False):
        counts = prune_store(self, versions, apply=apply)
        publisher = Thread(target=publish_import)
        publisher.start()
        publisher.join(timeout=10)
        assert not publisher.is_alive()
        return counts

    monkeypatch.setattr(ResultStore, "prune_stale_corpus_version", prune_then_publish)
    monkeypatch.setattr(cli, "interoperable_corpus_versions", lambda: ("current", "older"))

    with pytest.raises(FileNotFoundError, match="optimizer result store not found"):
        cli.main([
            "prune-stale-corpus",
            "--store",
            str(store.path),
            "--store",
            str(missing),
            "--apply",
        ])

    assert _keys(store) == {"current", "older-compatible", "unversioned"}
    assert len(imported) == 1 and imported[0].path == published
    assert not staging.exists()
    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert report["stores"] == [{
        "store": str(store.path.resolve()),
        "artifact_kind": "result_rows",
        "stale": 1,
        "kept": 2,
        "unversioned": 1,
        "deleted": 1,
    }]
    assert report["abort_reason"].startswith("FileNotFoundError: optimizer result store not found:")


def test_quarantine_path_mutation_probe_rejects_marker_only_classifier(
    tmp_path, monkeypatch, capsys
):
    def marker_only_classifier(path, runs_root):
        directory = Path(path).resolve().parent
        return (directory / IMPORT_OVERLAY_NAME).is_file() or (
            directory / ARTIFACT_INDEX_NAME
        ).is_file()

    monkeypatch.setattr(
        cli,
        "classify_optimizer_store",
        lambda path, runs_root: (
            OptimizerStoreClassification.PUBLISHED_ARCHIVE
            if marker_only_classifier(path, runs_root)
            else OptimizerStoreClassification.NATIVE
        ),
    )
    with pytest.raises(AssertionError):
        test_default_report_keeps_pareto_and_lists_quarantined_store(
            tmp_path, monkeypatch, capsys
        )


def _assert_native_export_is_pruned(tmp_path, monkeypatch, capsys):
    run_dir = tmp_path / "runs" / "native"
    store = _store_with_versions(run_dir)
    for name in REQUIRED_MEMBERS:
        path = run_dir / name
        if name == store.path.name:
            continue
        if name in {"study.manifest.json", "study.summary.json"}:
            path.write_text('{"study_status":"completed"}')
        elif name == "study.profile.yaml":
            path.write_text("{}")
        elif name == "leaderboard.csv":
            path.write_text("cache_key\n")
        else:
            path.write_text("{}")

    export_study_bundle(run_dir)
    assert (run_dir / ARTIFACT_INDEX_NAME).is_file()
    monkeypatch.setenv("OPTIMIZER_RUNS_DIR", str(run_dir.parent))
    monkeypatch.setattr(cli, "interoperable_corpus_versions", lambda: ("current", "older"))

    assert cli.main(["prune-stale-corpus", "--store", str(store.path), "--apply"]) == 0

    assert _keys(store) == {"current", "older-compatible", "unversioned"}
    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert report["stores"][0]["artifact_kind"] == "result_rows"


def test_native_export_remains_prunable(tmp_path, monkeypatch, capsys):
    _assert_native_export_is_pruned(tmp_path, monkeypatch, capsys)


def test_native_export_mutation_probe_rejects_artifact_index_classifier(
    tmp_path, monkeypatch, capsys
):
    def old_archive_classifier(path, runs_root):
        directory = Path(path).resolve().parent
        return (directory / IMPORT_OVERLAY_NAME).is_file() or (
            directory / ARTIFACT_INDEX_NAME
        ).is_file()

    monkeypatch.setattr(
        cli,
        "classify_optimizer_store",
        lambda path, runs_root: (
            OptimizerStoreClassification.PUBLISHED_ARCHIVE
            if old_archive_classifier(path, runs_root)
            else OptimizerStoreClassification.NATIVE
        ),
    )
    with pytest.raises(AssertionError):
        _assert_native_export_is_pruned(tmp_path, monkeypatch, capsys)
