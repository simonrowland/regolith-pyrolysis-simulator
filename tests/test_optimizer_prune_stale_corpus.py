"""Focused tests for corpus-version pruning in the optimizer result store."""

from __future__ import annotations

import sqlite3
import json

import pytest

from simulator.optimize import cli
from simulator.optimize.results_store import ResultStore, ResultStoreWriteLockTimeout


def _store_with_versions(tmp_path, *, timeout_ms=30_000):
    path = tmp_path / "cache.sqlite"
    store = ResultStore(path, write_lock_timeout_ms=timeout_ms)
    with sqlite3.connect(path) as conn:
        for key, version in (("stale-a", "old"), ("current", "current"), ("unversioned", None)):
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
    return store


def _keys(store):
    with sqlite3.connect(store.path) as conn:
        return {row[0] for row in conn.execute("SELECT cache_key FROM results")}


def test_dry_run_reports_counts_without_deleting(tmp_path):
    store = _store_with_versions(tmp_path)

    counts = store.prune_stale_corpus_version("current")

    assert counts == {"stale": 1, "kept": 1, "unversioned": 1}
    assert _keys(store) == {"stale-a", "current", "unversioned"}


def test_apply_deletes_only_stale_and_keeps_current_and_unversioned(tmp_path):
    store = _store_with_versions(tmp_path)

    counts = store.prune_stale_corpus_version("current", apply=True)

    assert counts == {"stale": 1, "kept": 1, "unversioned": 1}
    assert _keys(store) == {"current", "unversioned"}


def test_second_apply_is_noop(tmp_path):
    store = _store_with_versions(tmp_path)

    store.prune_stale_corpus_version("current", apply=True)
    counts = store.prune_stale_corpus_version("current", apply=True)

    assert counts == {"stale": 0, "kept": 1, "unversioned": 1}


def test_prune_honours_result_store_write_lock(tmp_path):
    store = _store_with_versions(tmp_path, timeout_ms=1)

    with store.write_lock():
        with pytest.raises(ResultStoreWriteLockTimeout):
            store.prune_stale_corpus_version("current", apply=True)
    assert _keys(store) == {"stale-a", "current", "unversioned"}


def test_cli_prints_human_summary_and_json_line(tmp_path, monkeypatch, capsys):
    store = _store_with_versions(tmp_path)
    monkeypatch.setattr(cli, "current_corpus_version", lambda: "current")

    assert cli.main(["prune-stale-corpus", "--store", str(store.path)]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert "would delete 1 stale" in lines[0]
    report = json.loads(lines[-1])
    assert report["stores"] == [{
        "store": str(store.path),
        "artifact_kind": "result_rows",
        "stale": 1,
        "kept": 1,
        "unversioned": 1,
        "deleted": 0,
    }]
    assert _keys(store) == {"stale-a", "current", "unversioned"}


def test_version_filter_mutation_probe_detects_unfiltered_delete(tmp_path, monkeypatch):
    store = _store_with_versions(tmp_path)

    def mutation(self, keep_corpus_version, *, apply=False):
        if apply:
            with self._connect() as conn:
                conn.execute("DELETE FROM results")
        return {"stale": 3, "kept": 0, "unversioned": 0}

    monkeypatch.setattr(ResultStore, "prune_stale_corpus_version", mutation)
    with pytest.raises(AssertionError):
        counts = store.prune_stale_corpus_version("current", apply=True)
        assert counts["stale"] == 1
        assert _keys(store) == {"current", "unversioned"}
