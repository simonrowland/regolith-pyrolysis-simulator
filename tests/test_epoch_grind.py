from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scripts import epoch_grind


def _manifest_file(tmp_path: Path, jobs: list[dict[str, object]] | None = None) -> Path:
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
    payload = {
        "base_cache": "base.sqlite",
        "work_dir": "epochs",
        "fidelity": "fast",
        "parallel": 2,
        "jobs": jobs
        or [
            {
                "id": "job-a",
                "feedstock": "lunar_mare_low_ti",
                "profile": str(profile),
                "budget": 8,
                "strategy": "random",
                "seed": 3,
                "out": "runs/job-a",
            }
        ],
    }
    manifest = tmp_path / "jobs.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    return manifest


def test_duplication_rate_math() -> None:
    summary = {
        "inserted_rows": 12,
        "sources": [
            {"source_rows": 10, "inserted_rows": 8},
            {"source_rows": 5, "inserted_rows": 4},
            {"source_rows": 99, "inserted_rows": 0, "skipped": "target"},
        ],
    }

    assert epoch_grind.duplication_rate_from_merge(summary) == pytest.approx(0.2)
    assert epoch_grind.duplication_rate_from_merge(
        {
            "inserted_rows": 20,
            "sources": [{"source_rows": 1020, "seed_rows": 1000, "inserted_rows": 20}],
        }
    ) == pytest.approx(0.0)
    assert epoch_grind.duplication_rate_from_merge(
        {
            "inserted_rows": 0,
            "sources": [{"source_rows": 1020, "seed_rows": 1000, "inserted_rows": 0}],
        }
    ) == pytest.approx(1.0)
    assert epoch_grind.duplication_rate(0, 0) == 0.0
    assert epoch_grind.duplication_rate(10, 15) == 0.0


def test_duplication_rate_rejects_source_rows_below_seed_rows() -> None:
    with pytest.raises(ValueError, match=r"shard-a.sqlite: source_rows=999 .* seed_rows=1000"):
        epoch_grind.duplication_rate_from_merge(
            {
                "inserted_rows": 0,
                "sources": [
                    {
                        "source": "shard-a.sqlite",
                        "source_rows": 999,
                        "seed_rows": 1000,
                        "inserted_rows": 0,
                    }
                ],
            }
        )


def test_adaptive_termination_state_machine() -> None:
    assert (
        epoch_grind.adaptive_decision([0.5], remaining_jobs=0)
        == epoch_grind.DECISION_BATCH_COMPLETE
    )
    assert (
        epoch_grind.adaptive_decision([0.01], remaining_jobs=1, threshold=0.02, consecutive=2)
        == epoch_grind.DECISION_CONTINUE
    )
    assert (
        epoch_grind.adaptive_decision(
            [0.04, 0.01, 0.015],
            remaining_jobs=1,
            threshold=0.02,
            consecutive=2,
        )
        == epoch_grind.DECISION_FINAL_LONG
    )
    assert (
        epoch_grind.adaptive_decision(
            [0.01, 0.015],
            remaining_jobs=1,
            threshold=0.02,
            consecutive=2,
            duplication_expected=False,
        )
        == epoch_grind.DECISION_CONTINUE
    )


def test_manifest_parsing_resolves_paths_and_defaults(tmp_path: Path) -> None:
    manifest = epoch_grind.load_manifest(_manifest_file(tmp_path))

    assert manifest.base_cache == tmp_path / "base.sqlite"
    assert manifest.work_dir == tmp_path / "epochs"
    assert len(manifest.jobs) == 1
    job = manifest.jobs[0]
    assert job.id == "job-a"
    assert job.fidelity == "fast"
    assert job.parallel == 2
    assert job.out == tmp_path / "runs/job-a"


def test_resume_from_journal_keeps_done_jobs_done(tmp_path: Path) -> None:
    manifest_path = _manifest_file(
        tmp_path,
        jobs=[
            {
                "id": "done",
                "feedstock": "lunar_mare_low_ti",
                "profile": "profile.json",
                "budget": 1,
                "strategy": "random",
                "seed": 1,
                "out": "runs/done",
            },
            {
                "id": "pending",
                "feedstock": "lunar_mare_low_ti",
                "profile": "profile.json",
                "budget": 1,
                "strategy": "random",
                "seed": 2,
                "out": "runs/pending",
            },
        ],
    )
    manifest = epoch_grind.load_manifest(manifest_path)
    journal = epoch_grind.initialize_journal(manifest)
    journal["epoch"] = 3
    journal["jobs"][0]["status"] = "done"
    journal_path = tmp_path / "journal.json"
    epoch_grind.save_journal(journal_path, journal)

    loaded = epoch_grind.load_or_initialize_journal(journal_path, manifest)
    pending = epoch_grind.pending_jobs(manifest, loaded)

    assert loaded["epoch"] == 3
    assert loaded["jobs_done"] == ["done"]
    assert loaded["jobs_remaining"] == ["pending"]
    assert [job.id for job in pending] == ["pending"]


def test_resume_rejects_journal_from_different_manifest(tmp_path: Path) -> None:
    job = {
        "id": "same-id",
        "feedstock": "lunar_mare_low_ti",
        "profile": "profile.json",
        "budget": 1,
        "strategy": "random",
        "seed": 1,
        "out": "runs/same-id",
    }
    first_dir = tmp_path / "one"
    second_dir = tmp_path / "two"
    first_dir.mkdir()
    second_dir.mkdir()
    first = epoch_grind.load_manifest(_manifest_file(first_dir, jobs=[dict(job)]))
    second = epoch_grind.load_manifest(_manifest_file(second_dir, jobs=[dict(job)]))

    journal = epoch_grind.initialize_journal(first)
    journal["jobs"][0]["status"] = "done"
    journal_path = tmp_path / "journal.json"
    epoch_grind.save_journal(journal_path, journal)

    with pytest.raises(ValueError, match="journal identity does not match"):
        epoch_grind.load_or_initialize_journal(journal_path, second)


def test_resume_rejects_journal_with_mismatched_job_ids(tmp_path: Path) -> None:
    job = {
        "feedstock": "lunar_mare_low_ti",
        "profile": "profile.json",
        "budget": 1,
        "strategy": "random",
        "seed": 1,
        "out": "runs/x",
    }
    manifest = epoch_grind.load_manifest(
        _manifest_file(tmp_path, jobs=[{**job, "id": "job-a"}])
    )
    journal = epoch_grind.initialize_journal(manifest)
    journal_path = tmp_path / "journal.json"
    epoch_grind.save_journal(journal_path, journal)

    renamed = epoch_grind.load_manifest(
        _manifest_file(tmp_path, jobs=[{**job, "id": "job-b"}])
    )

    with pytest.raises(ValueError, match="job ids"):
        epoch_grind.load_or_initialize_journal(journal_path, renamed)


def test_resume_rejects_journal_with_mismatched_job_parameters(tmp_path: Path) -> None:
    job = {
        "id": "job-a",
        "feedstock": "lunar_mare_low_ti",
        "profile": "profile.json",
        "budget": 1,
        "strategy": "random",
        "seed": 1,
        "out": "runs/job-a",
    }
    manifest = epoch_grind.load_manifest(_manifest_file(tmp_path, jobs=[dict(job)]))
    journal_path = tmp_path / "journal.json"
    epoch_grind.save_journal(journal_path, epoch_grind.initialize_journal(manifest))

    changed = epoch_grind.load_manifest(
        _manifest_file(tmp_path, jobs=[{**job, "budget": 2}])
    )

    with pytest.raises(ValueError, match=r"job 'job-a' parameters: .*budget"):
        epoch_grind.load_or_initialize_journal(journal_path, changed)


def test_old_schema_journal_without_job_identity_is_grandfathered(tmp_path: Path) -> None:
    manifest = epoch_grind.load_manifest(_manifest_file(tmp_path))
    journal = epoch_grind.initialize_journal(manifest)
    journal["schema_version"] = epoch_grind.LEGACY_JOURNAL_SCHEMA_VERSION
    for job in journal["jobs"]:
        for field in epoch_grind.JOB_IDENTITY_FIELDS:
            job.pop(field, None)
    journal_path = tmp_path / "journal.json"
    journal_path.write_text(json.dumps(journal), encoding="utf-8")

    loaded = epoch_grind.load_or_initialize_journal(journal_path, manifest)

    assert loaded["schema_version"] == epoch_grind.JOURNAL_SCHEMA_VERSION
    assert loaded["jobs"][0]["profile"] == str(tmp_path / "profile.json")
    assert loaded["jobs"][0]["out"] == str(tmp_path / "runs/job-a")
    assert loaded["journal_notes"][-1]["type"] == "schema_migration"


def test_old_schema_journal_with_drifted_job_ids_is_refused(tmp_path: Path) -> None:
    manifest = epoch_grind.load_manifest(_manifest_file(tmp_path))
    journal = epoch_grind.initialize_journal(manifest)
    journal["schema_version"] = epoch_grind.LEGACY_JOURNAL_SCHEMA_VERSION
    journal["jobs"][0]["id"] = "other-job"
    for field in epoch_grind.JOB_IDENTITY_FIELDS:
        journal["jobs"][0].pop(field, None)
    journal_path = tmp_path / "journal.json"
    journal_path.write_text(json.dumps(journal), encoding="utf-8")

    with pytest.raises(ValueError, match="job ids"):
        epoch_grind.load_or_initialize_journal(journal_path, manifest)


def test_profile_identity_normalizes_manifest_relative_paths(tmp_path: Path) -> None:
    job = {
        "id": "job-a",
        "feedstock": "lunar_mare_low_ti",
        "profile": "./profile.json",
        "budget": 1,
        "strategy": "random",
        "seed": 1,
        "out": "runs/job-a",
    }
    manifest = epoch_grind.load_manifest(_manifest_file(tmp_path, jobs=[dict(job)]))
    journal_path = tmp_path / "journal.json"
    epoch_grind.save_journal(journal_path, epoch_grind.initialize_journal(manifest))

    equivalent = epoch_grind.load_manifest(
        _manifest_file(tmp_path, jobs=[{**job, "profile": "profile.json"}])
    )
    loaded = epoch_grind.load_or_initialize_journal(journal_path, equivalent)

    assert loaded["jobs"][0]["profile"] == str(tmp_path / "profile.json")


def test_dry_run_plan_prints_optimizer_commands(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = epoch_grind.load_manifest(_manifest_file(tmp_path))
    journal = epoch_grind.initialize_journal(manifest)
    config = epoch_grind.DriverConfig(
        python="/venv/bin/python",
        time_box_seconds=7200,
        dup_threshold=0.02,
        low_dup_epochs=2,
        duplication_expected=True,
        nice=15,
    )

    code = epoch_grind.run_driver(
        manifest,
        config,
        journal_path=tmp_path / "journal.json",
        dry_run=True,
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    command = payload["jobs"][0]["command"]
    profile_arg = command[command.index("--profile") + 1]
    assert profile_arg.endswith("epoch-0001/profiles/job-a.profile.json")
    assert not Path(profile_arg).exists()
    assert payload["jobs"][0]["would_write_profile"]["path"] == profile_arg
    assert payload["jobs"][0]["would_write_profile"]["content"]["run"][
        "reduced_real_cache"
    ]["db_path"].endswith("epoch-0001/shards/job-a.sqlite")

    monkeypatch.setattr(
        epoch_grind,
        "seed_job_cache",
            lambda *args, **kwargs: {"rows_after": 1000},
    )
    monkeypatch.setattr(
        epoch_grind,
        "_run_child",
        lambda *args, **kwargs: epoch_grind.ChildOutcome(kind="completed", returncode=0),
    )
    real_epoch = epoch_grind.run_epoch(
        manifest,
        manifest.jobs,
        config,
        epoch_index=1,
    )
    real_command = real_epoch["attempted_jobs"][0]["command"]

    assert command == real_command
    assert command[:5] == ["nice", "-n", "15", "/venv/bin/python", "-m"]
    assert "simulator.optimize" in command
    assert payload["jobs"][0]["shard_db"].endswith("epoch-0001/shards/job-a.sqlite")
    profile = json.loads(Path(profile_arg).read_text(encoding="utf-8"))
    assert profile["run"]["reduced_real_cache"]["db_path"].endswith(
        "epoch-0001/shards/job-a.sqlite"
    )


def test_schema_gate_passthrough_from_merge(tmp_path: Path) -> None:
    base = tmp_path / "base.sqlite"
    source = tmp_path / "source.sqlite"
    source.write_text("not sqlite", encoding="utf-8")

    class SchemaGateError(RuntimeError):
        pass

    def fail_seed(target: Path, sources: object) -> object:
        raise SchemaGateError(f"schema gate saw {target}")

    with pytest.raises(SchemaGateError):
        epoch_grind.merge_epoch_shards(base, [source], seed_fn=fail_seed)


def test_write_epoch_profile_overlays_cache_db(tmp_path: Path) -> None:
    manifest = epoch_grind.load_manifest(_manifest_file(tmp_path))
    job = manifest.jobs[0]
    shard = tmp_path / "epoch" / "shards" / "job-a.sqlite"

    profile_arg = epoch_grind.write_epoch_profile(job, tmp_path, shard, tmp_path / "epoch")
    profile = json.loads(Path(profile_arg).read_text(encoding="utf-8"))

    assert profile["run"]["reduced_real_cache"]["db_path"] == str(shard)


def test_timeboxed_child_stays_pending_and_mergeable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = epoch_grind.load_manifest(_manifest_file(tmp_path))
    config = epoch_grind.DriverConfig(
        python="/venv/bin/python",
        time_box_seconds=7200,
        dup_threshold=0.02,
        low_dup_epochs=2,
        duplication_expected=True,
        nice=15,
    )

    monkeypatch.setattr(
        epoch_grind,
        "seed_job_cache",
        lambda *args, **kwargs: {"rows_after": 1000},
    )
    monkeypatch.setattr(
        epoch_grind,
        "_run_child",
        lambda *args, **kwargs: epoch_grind.ChildOutcome(kind="timed_out"),
    )

    result = epoch_grind.run_epoch(manifest, manifest.jobs, config, epoch_index=1)
    journal = epoch_grind.initialize_journal(manifest)
    epoch_grind._apply_epoch_result(journal, result)

    assert result["timed_out_jobs"] == ["job-a"]
    assert result["completed_jobs"] == []
    assert result["failed_jobs"] == []
    assert result["shard_dbs"] == [
        str(manifest.work_dir / "epoch-0001" / "shards" / "job-a.sqlite")
    ]
    assert result["seed_rows_by_shard"][result["shard_dbs"][0]] == 1000
    assert [job.id for job in epoch_grind.pending_jobs(manifest, journal)] == ["job-a"]


def test_run_child_classifies_child_owned_rc_124_as_failure(tmp_path: Path) -> None:
    outcome = epoch_grind._run_child(
        [sys.executable, "-c", "raise SystemExit(124)"],
        stdout_path=tmp_path / "child.stdout.log",
        stderr_path=tmp_path / "child.stderr.log",
        timeout=5.0,
    )

    assert outcome == epoch_grind.ChildOutcome(kind="failed", returncode=124)


def test_child_owned_rc_124_is_failed_and_excluded_from_merge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = epoch_grind.load_manifest(_manifest_file(tmp_path))
    config = epoch_grind.DriverConfig(
        python="/venv/bin/python",
        time_box_seconds=7200,
        dup_threshold=0.02,
        low_dup_epochs=2,
        duplication_expected=True,
        nice=15,
    )

    monkeypatch.setattr(
        epoch_grind,
        "seed_job_cache",
        lambda *args, **kwargs: {"rows_after": 1000},
    )
    monkeypatch.setattr(
        epoch_grind,
        "_run_child",
        lambda *args, **kwargs: epoch_grind.ChildOutcome(kind="failed", returncode=124),
    )

    result = epoch_grind.run_epoch(manifest, manifest.jobs, config, epoch_index=1)
    journal = epoch_grind.initialize_journal(manifest)
    epoch_grind._apply_epoch_result(journal, result)

    assert result["timed_out_jobs"] == []
    assert result["completed_jobs"] == []
    assert result["shard_dbs"] == []
    assert result["seed_rows_by_shard"] == {}
    assert result["failed_jobs"][0]["id"] == "job-a"
    assert result["failed_jobs"][0]["returncode"] == 124
    with pytest.raises(RuntimeError, match="journal has failed jobs: job-a"):
        epoch_grind.pending_jobs(manifest, journal)


def test_failed_epoch_is_journaled_before_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = epoch_grind.load_manifest(_manifest_file(tmp_path))
    journal_path = tmp_path / "journal.json"
    config = epoch_grind.DriverConfig(
        python="/venv/bin/python",
        time_box_seconds=7200,
        dup_threshold=0.02,
        low_dup_epochs=2,
        duplication_expected=True,
        nice=15,
    )

    merged_shards: list[Path] = []

    monkeypatch.setattr(
        epoch_grind,
        "seed_job_cache",
        lambda *args, **kwargs: {"rows_after": 1000},
    )
    monkeypatch.setattr(
        epoch_grind,
        "_run_child",
        lambda *args, **kwargs: epoch_grind.ChildOutcome(kind="failed", returncode=2),
    )

    def merge_recorder(base: Path, shard_paths: list[Path], **kwargs: object) -> dict[str, object]:
        merged_shards.extend(shard_paths)
        return {"inserted_rows": 0, "sources": []}

    monkeypatch.setattr(epoch_grind, "merge_epoch_shards", merge_recorder)

    assert epoch_grind.run_driver(manifest, config, journal_path=journal_path) == 2
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["decision"] == epoch_grind.DECISION_FAILED
    assert journal["jobs"][0]["status"] == "failed"
    assert merged_shards == []
