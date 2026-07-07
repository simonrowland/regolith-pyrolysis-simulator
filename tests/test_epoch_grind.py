from __future__ import annotations

import hashlib
import json
import logging
import signal
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

from scripts import epoch_grind
from scripts.seed_reduced_real_cache import payload_count
from simulator.corpus_version import current_corpus_version
from simulator.reduced_real_determinism import (
    PT1PersistentEquilibriumStore,
    canonical_json_bytes,
)


NO_FEASIBLE_MESSAGE = (
    "no feasible candidates; winner.recipe.yaml not written; "
    "failure_counts={'infeasible_recipe': 3}"
)
NO_FEASIBLE_NON_FINITE_MESSAGE = (
    "all candidates failed with non_finite_payload; "
    "failure_counts={'non_finite_payload': 3}"
)
NO_FEASIBLE_STDERR_LINE = f"error: {NO_FEASIBLE_MESSAGE}"
STALE_PROFILE_MESSAGE = (
    "stale-profile.yaml: constraints.gates contains out-of-policy gate "
    "'delivered_stream_purity' for melt target pool 'residual_rump_at_stop'; "
    "regenerate with FORCE_PROFILES=1"
)


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
    PT1PersistentEquilibriumStore(tmp_path / "base.sqlite")
    return manifest


def _ranked_drain(
    selected_ids: list[str],
    *,
    table_ids: list[str] | None = None,
) -> dict[str, object]:
    ids = table_ids or selected_ids
    return {
        "schema_version": "interpolation_uncertainty_ranked_tables.v1",
        "limit": len(selected_ids),
        "tables": [
            {
                "name": "threshold_straddle",
                "count": len(ids),
                "point_ids": ids,
            },
            {
                "name": "nonlinearity_general",
                "count": 0,
                "point_ids": [],
            },
        ],
        "selected": [
            {
                "point_id": point_id,
                "sequence_index": index,
                "ranked_table": "threshold_straddle",
                "rank_value": 1.0,
            }
            for index, point_id in enumerate(selected_ids)
        ],
    }


def _pt1_cache_key(
    tag: str,
    *,
    allowlist_version: str | None = None,
    recipe_schema_version: str | None = None,
    recipe_id: str | None = None,
) -> dict[str, object]:
    schema = epoch_grind.RecipeSchema()
    return {
        "allowlist_version": allowlist_version or schema.allowlist_version,
        "artifact": "freeze_gate_curve",
        "corpus_version": current_corpus_version(),
        "data_digests": {"fixture": "v1"},
        "recipe_id": recipe_id or hashlib.sha256(f"recipe:{tag}".encode()).hexdigest(),
        "recipe_schema_version": recipe_schema_version or schema.recipe_schema_version,
        "schema_version": "test",
        "tag": tag,
    }


def _pt1_cache_point_id(tag: str, **kwargs: object) -> str:
    return hashlib.sha256(canonical_json_bytes(_pt1_cache_key(tag, **kwargs))).hexdigest()


def _put_pt1_cache_row(db_path: Path, tag: str, **kwargs: object) -> str:
    key = _pt1_cache_key(tag, **kwargs)
    payload = {"curve": {"status": "in_range", "tag": tag}}
    key_bytes = canonical_json_bytes(key)
    payload_bytes = canonical_json_bytes(payload)
    key_hash = hashlib.sha256(key_bytes).hexdigest()
    PT1PersistentEquilibriumStore(db_path).put(
        artifact=str(key["artifact"]),
        key=key,
        key_bytes=key_bytes,
        key_hash=key_hash,
        payload=payload,
        payload_bytes=payload_bytes,
        payload_hash=hashlib.sha256(payload_bytes).hexdigest(),
    )
    return key_hash


def test_control_quantization_cli_parse_and_epoch_profile_overlay(
    tmp_path: Path,
) -> None:
    manifest_path = _manifest_file(tmp_path)
    manifest = epoch_grind.load_manifest(manifest_path)
    args = epoch_grind._parser().parse_args(
        [
            "--manifest",
            str(manifest_path),
            "--control-quantization",
            "XX-COARSE",
        ]
    )

    profile_arg, profile = epoch_grind.plan_epoch_profile(
        manifest.jobs[0],
        manifest.path.parent,
        tmp_path / "epoch" / "job-a.sqlite",
        tmp_path / "epoch",
        base_cache=manifest.base_cache,
        control_quantization=args.control_quantization,
    )

    assert profile_arg == str(tmp_path / "epoch" / "profiles" / "job-a.profile.json")
    assert profile is not None
    assert profile["run"]["reduced_real_cache"]["control_quantization"] == (
        "xx_coarse"
    )

    json_value = {
        "t_k_quantum": 2.0,
        "pressure_bar_quantum": 0.002,
        "log_fo2_quantum": 0.02,
        "composition_sig_figs": 3,
    }
    json_args = epoch_grind._parser().parse_args(
        [
            "--manifest",
            str(manifest_path),
            "--control-quantization",
            json.dumps(json_value),
        ]
    )
    assert json_args.control_quantization == json_value

    with pytest.raises(SystemExit):
        epoch_grind._parser().parse_args(
            ["--manifest", str(manifest_path), "--control-quantization", "bad-tier"]
        )


def _write_no_feasible_artifacts(
    out_dir: Path,
    *,
    budget: int,
    provenance_rows: int | None = None,
    write_status: bool = True,
    message: str = NO_FEASIBLE_MESSAGE,
    failure_counts: dict[str, int] | None = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = failure_counts or {"infeasible_recipe": budget}
    row_count = budget if provenance_rows is None else provenance_rows
    (out_dir / "provenance.jsonl").write_text(
        "".join(json.dumps({"candidate_id": f"candidate-{index}"}) + "\n" for index in range(row_count)),
        encoding="utf-8",
    )
    (out_dir / "pareto.json").write_text(
        json.dumps({"failure_counts": counts, "pareto": [], "winner_candidate_id": None}) + "\n",
        encoding="utf-8",
    )
    (out_dir / "leaderboard.csv").write_text("candidate_id,feasible\n", encoding="utf-8")
    if write_status:
        (out_dir / "job_status.json").write_text(
            json.dumps(
                {
                    "message": message,
                    "reason": "StudyNoFeasibleError",
                    "status": "FAILED",
                    "success": False,
                }
            )
            + "\n",
            encoding="utf-8",
        )


def _write_stale_profile_status(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "job_status.json").write_text(
        json.dumps(
            {
                "message": STALE_PROFILE_MESSAGE,
                "reason": "ProfileValidationError",
                "status": "FAILED",
                "success": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_launch_preflight_rejects_fallback_enabled_profile(tmp_path: Path) -> None:
    manifest_path = _manifest_file(tmp_path)
    raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    profile_path = Path(raw_manifest["jobs"][0]["profile"])
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    profile["run"]["allow_fallback_vapor"] = True
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    manifest = epoch_grind.load_manifest(manifest_path)
    config = epoch_grind.DriverConfig(
        python="/venv/bin/python",
        time_box_seconds=7200,
        dup_threshold=0.02,
        low_dup_epochs=2,
        duplication_expected=True,
        nice=15,
    )

    with pytest.raises(epoch_grind.GrindSourceGateError, match="allow_fallback_vapor"):
        epoch_grind.run_driver(
            manifest,
            config,
            journal_path=tmp_path / "journal.json",
            dry_run=True,
        )


def test_launch_preflight_rejects_fallback_enabled_global_setpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = _manifest_file(tmp_path)
    manifest = epoch_grind.load_manifest(manifest_path)
    config = epoch_grind.DriverConfig(
        python="/venv/bin/python",
        time_box_seconds=7200,
        dup_threshold=0.02,
        low_dup_epochs=2,
        duplication_expected=True,
        nice=15,
    )
    bundle = type(
        "Bundle",
        (),
        {"setpoints": {"chemistry_kernel": {"allow_fallback_vapor": True}}},
    )()
    monkeypatch.setattr(epoch_grind, "load_config_bundle", lambda: bundle)

    with pytest.raises(epoch_grind.GrindSourceGateError, match="allow_fallback_vapor"):
        epoch_grind.run_driver(
            manifest,
            config,
            journal_path=tmp_path / "journal.json",
            dry_run=True,
        )


def test_launch_preflight_rejects_uncovered_feedstock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = tmp_path / "profile.json"
    job = {
        "id": "gap",
        "feedstock": "interwindow",
        "profile": str(profile),
        "budget": 8,
        "strategy": "random",
        "seed": 3,
        "out": "runs/gap",
    }
    manifest = epoch_grind.load_manifest(_manifest_file(tmp_path, jobs=[job]))
    config = epoch_grind.DriverConfig(
        python="/venv/bin/python",
        time_box_seconds=7200,
        dup_threshold=0.02,
        low_dup_epochs=2,
        duplication_expected=True,
        nice=15,
    )
    bundle = type(
        "Bundle",
        (),
        {
            "setpoints": {"chemistry_kernel": {}},
            "feedstocks": {
                "interwindow": {
                    "composition_wt_pct": {
                        "SiO2": 42.0,
                        "Al2O3": 12.0,
                        "FeO": 12.0,
                        "MgO": 18.0,
                        "TiO2": 0.3,
                        "CaO": 10.0,
                    }
                }
            },
        },
    )()
    monkeypatch.setattr(epoch_grind, "load_config_bundle", lambda *args, **kwargs: bundle)

    with pytest.raises(epoch_grind.GrindSourceGateError, match="interwindow"):
        epoch_grind.run_driver(
            manifest,
            config,
            journal_path=tmp_path / "journal.json",
            dry_run=True,
        )


def test_launch_preflight_accepts_covered_or_out_of_domain_feedstocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile = tmp_path / "profile.json"
    jobs = [
        {
            "id": "covered",
            "feedstock": "covered",
            "profile": str(profile),
            "budget": 8,
            "strategy": "random",
            "seed": 3,
            "out": "runs/covered",
        },
        {
            "id": "metallic",
            "feedstock": "metallic_ood",
            "profile": str(profile),
            "budget": 8,
            "strategy": "random",
            "seed": 4,
            "out": "runs/metallic",
        },
    ]
    manifest = epoch_grind.load_manifest(_manifest_file(tmp_path, jobs=jobs))
    config = epoch_grind.DriverConfig(
        python="/venv/bin/python",
        time_box_seconds=7200,
        dup_threshold=0.02,
        low_dup_epochs=2,
        duplication_expected=True,
        nice=15,
    )
    bundle = type(
        "Bundle",
        (),
        {
            "setpoints": {"chemistry_kernel": {}},
            "feedstocks": {
                "covered": {"stage0_verdict_b_subprocess_required": True},
                "metallic_ood": {"composition_wt_pct": {"Fe": 100.0}},
            },
            "digests": {},
        },
    )()
    monkeypatch.setattr(epoch_grind, "load_config_bundle", lambda *args, **kwargs: bundle)

    rc = epoch_grind.run_driver(
        manifest,
        config,
        journal_path=tmp_path / "journal.json",
        dry_run=True,
    )

    assert rc == 0
    assert '"id": "covered"' in capsys.readouterr().out


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


def test_seed_source_summary_adds_ladder_polish_without_reducing_mutation_budget(
    tmp_path: Path,
) -> None:
    drain = _ranked_drain(
        [f"ranked-{index}" for index in range(6)],
        table_ids=[f"ranked-{index}" for index in range(10)],
    )
    manifest = epoch_grind.load_manifest(
        _manifest_file(
            tmp_path,
            jobs=[
                {
                    "id": "job-a",
                    "feedstock": "lunar_mare_low_ti",
                    "profile": "profile.json",
                    "budget": 8,
                    "strategy": "random",
                    "seed": 3,
                    "out": "runs/job-a",
                    "acquisition": {
                        "ranked_table_drain": drain,
                        "recompute_limit": 5,
                        "calibration_fraction": 0.2,
                    },
                }
            ],
        )
    )

    summary = epoch_grind.build_seed_source_summary(manifest.jobs[0], manifest.path.parent)

    assert summary["source_counts"] == {
        "mutation": 8,
        "ladder_polish": 4,
        "calibration_uniform": 1,
    }
    assert summary["calibration_quota"] == 1
    point_sources = summary["point_sources"]
    assert [point["source"] for point in point_sources[:4]] == ["ladder_polish"] * 4
    assert point_sources[-1]["source"] == "calibration_uniform"
    assert point_sources[-1]["point_id"] not in {f"ranked-{index}" for index in range(6)}
    assert summary["sources"][0]["source"] == "mutation"
    assert summary["sources"][0]["candidate_id_start"] == "random-3-000000"
    assert summary["sources"][0]["candidate_id_stop"] == "random-3-000007"


def test_seed_source_summary_reserves_uniform_quota_with_rounding_and_exhaustion(
    tmp_path: Path,
) -> None:
    manifest = epoch_grind.load_manifest(
        _manifest_file(
            tmp_path,
            jobs=[
                {
                    "id": "job-a",
                    "feedstock": "lunar_mare_low_ti",
                    "profile": "profile.json",
                    "budget": 8,
                    "strategy": "random",
                    "seed": 3,
                    "out": "runs/job-a",
                    "acquisition": {
                        "ranked_table_drain": _ranked_drain(["a", "b"]),
                        "recompute_limit": 3,
                        "calibration_fraction": 0.34,
                    },
                }
            ],
        )
    )

    summary = epoch_grind.build_seed_source_summary(manifest.jobs[0], manifest.path.parent)

    assert summary["calibration_quota"] == 2
    assert summary["calibration_shortfall"] == 0
    assert summary["source_counts"]["ladder_polish"] == 0
    assert summary["source_counts"]["calibration_uniform"] == 2
    assert {point["source"] for point in summary["point_sources"]} == {"calibration_uniform"}


def test_seed_source_summary_calibration_replay_is_job_seed_pinned(
    tmp_path: Path,
) -> None:
    manifest = epoch_grind.load_manifest(
        _manifest_file(
            tmp_path,
            jobs=[
                {
                    "id": "job-a",
                    "feedstock": "lunar_mare_low_ti",
                    "profile": "profile.json",
                    "budget": 8,
                    "strategy": "random",
                    "seed": 3,
                    "out": "runs/job-a",
                    "acquisition": {
                        "ranked_table_drain": _ranked_drain(
                            ["calib-0", "calib-1"],
                            table_ids=[f"calib-{index}" for index in range(8)],
                        ),
                        "recompute_limit": 4,
                        "calibration_fraction": 0.5,
                    },
                }
            ],
        )
    )

    first = epoch_grind.build_seed_source_summary(manifest.jobs[0], manifest.path.parent)
    second = epoch_grind.build_seed_source_summary(manifest.jobs[0], manifest.path.parent)

    calibration_ids = [
        point["point_id"]
        for point in first["point_sources"]
        if point["source"] == "calibration_uniform"
    ]
    assert calibration_ids == ["calib-3", "calib-4"]
    assert second["point_sources"] == first["point_sources"]


def test_seed_source_summary_dedups_cross_drain_duplicate_ids(
    tmp_path: Path,
) -> None:
    manifest = epoch_grind.load_manifest(
        _manifest_file(
            tmp_path,
            jobs=[
                {
                    "id": "job-a",
                    "feedstock": "lunar_mare_low_ti",
                    "profile": "profile.json",
                    "budget": 8,
                    "strategy": "random",
                    "seed": 3,
                    "out": "runs/job-a",
                    "acquisition": {
                        "ranked_table_drains": [
                            _ranked_drain(["dup", "a"]),
                            _ranked_drain(["dup", "b"]),
                        ],
                        "recompute_limit": 4,
                        "calibration_fraction": 0,
                    },
                }
            ],
        )
    )

    summary = epoch_grind.build_seed_source_summary(manifest.jobs[0], manifest.path.parent)

    assert summary["source_counts"]["ladder_polish"] == 3
    assert [point["point_id"] for point in summary["point_sources"]] == ["dup", "a", "b"]


def test_seed_source_summary_rejects_empty_point_ids(tmp_path: Path) -> None:
    manifest = epoch_grind.load_manifest(
        _manifest_file(
            tmp_path,
            jobs=[
                {
                    "id": "job-a",
                    "feedstock": "lunar_mare_low_ti",
                    "profile": "profile.json",
                    "budget": 8,
                    "strategy": "random",
                    "seed": 3,
                    "out": "runs/job-a",
                    "acquisition": {
                        "ranked_table_drain": _ranked_drain([""]),
                        "recompute_limit": 1,
                        "calibration_fraction": 0,
                    },
                }
            ],
        )
    )

    with pytest.raises(ValueError, match="acquisition point id must not be empty"):
        epoch_grind.build_seed_source_summary(manifest.jobs[0], manifest.path.parent)


def test_seed_source_summary_reads_ranked_drain_jsonl_and_dry_run_consumes_it(
    tmp_path: Path,
) -> None:
    drain = _ranked_drain(
        ["a", "b", "c"],
        table_ids=["a", "b", "c", "d", "e", "f"],
    )
    drain_path = tmp_path / "drains.jsonl"
    drain_path.write_text(
        json.dumps(
            {
                "trace_summary": {
                    "reduced_real_cache": {
                        "interpolation_uncertainty_ranked_table_drain": drain
                    }
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = epoch_grind.load_manifest(
        _manifest_file(
            tmp_path,
            jobs=[
                {
                    "id": "job-a",
                    "feedstock": "lunar_mare_low_ti",
                    "profile": "profile.json",
                    "budget": 8,
                    "strategy": "random",
                    "seed": 3,
                    "out": "runs/job-a",
                    "acquisition": {
                        "ranked_table_drain_paths": ["drains.jsonl"],
                        "recompute_limit": 4,
                        "calibration_fraction": 0.25,
                    },
                }
            ],
        )
    )
    config = epoch_grind.DriverConfig(
        python="/venv/bin/python",
        time_box_seconds=7200,
        dup_threshold=0.02,
        low_dup_epochs=2,
        duplication_expected=True,
        nice=15,
    )

    payload = epoch_grind.dry_run_plan(
        manifest,
        config,
        epoch_grind.initialize_journal(manifest),
    )

    summary = payload["jobs"][0]["seed_source_summary"]
    assert summary["drain_count"] == 1
    assert summary["source_counts"] == {
        "mutation": 8,
        "ladder_polish": 3,
        "calibration_uniform": 1,
    }
    assert {point["source"] for point in summary["point_sources"]} == {
        "ladder_polish",
        "calibration_uniform",
    }
    cache_config = payload["jobs"][0]["would_write_profile"]["content"]["run"][
        "reduced_real_cache"
    ]
    assert "acquisition" not in cache_config
    assert "ranked_table_drain" not in json.dumps(cache_config, sort_keys=True)


def test_ranked_table_drain_paths_must_stay_under_manifest_or_artifact_root(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.jsonl"
    outside.write_text(json.dumps(_ranked_drain(["a"])) + "\n", encoding="utf-8")
    manifest = epoch_grind.load_manifest(
        _manifest_file(
            tmp_path,
            jobs=[
                {
                    "id": "job-a",
                    "feedstock": "lunar_mare_low_ti",
                    "profile": "profile.json",
                    "budget": 8,
                    "strategy": "random",
                    "seed": 3,
                    "out": "runs/job-a",
                    "acquisition": {
                        "ranked_table_drain_paths": ["../" + outside.name],
                        "recompute_limit": 1,
                    },
                }
            ],
        )
    )

    with pytest.raises(ValueError, match="ranked_table_drain_paths entry escapes"):
        epoch_grind.build_seed_source_summary(manifest.jobs[0], manifest.path.parent)

    artifact_root = tmp_path / "artifact-root"
    artifact_root.mkdir()
    inside = artifact_root / "drains.jsonl"
    inside.write_text(json.dumps(_ranked_drain(["a"])) + "\n", encoding="utf-8")
    allowed = epoch_grind.load_manifest(
        _manifest_file(
            tmp_path,
            jobs=[
                {
                    "id": "job-a",
                    "feedstock": "lunar_mare_low_ti",
                    "profile": "profile.json",
                    "budget": 8,
                    "strategy": "random",
                    "seed": 3,
                    "out": "runs/job-a",
                    "acquisition": {
                        "artifact_root": str(artifact_root),
                        "ranked_table_drain_paths": [str(inside)],
                        "recompute_limit": 1,
                        "calibration_fraction": 0,
                    },
                }
            ],
        )
    )

    summary = epoch_grind.build_seed_source_summary(allowed.jobs[0], allowed.path.parent)
    assert summary["source_counts"]["ladder_polish"] == 1


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
    assert loaded["code_version"] == epoch_grind.current_code_version()
    assert set(loaded["data_digests"]) >= {
        "feedstocks",
        "setpoints",
        "vapor_pressures",
        "profiles",
    }
    assert set(loaded["data_digests"]["profiles"]) == {"done", "pending"}
    assert loaded["jobs_done"] == ["done"]
    assert loaded["jobs_remaining"] == ["pending"]
    assert [job.id for job in pending] == ["pending"]


def test_run_driver_resume_after_sigterm_skips_done_job_and_retries_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = _manifest_file(
        tmp_path,
        jobs=[
            {
                "id": "job-a",
                "feedstock": "lunar_mare_low_ti",
                "profile": "profile.json",
                "budget": 1,
                "strategy": "random",
                "seed": 1,
                "out": "runs/job-a",
            },
            {
                "id": "job-b",
                "feedstock": "lunar_mare_low_ti",
                "profile": "profile.json",
                "budget": 1,
                "strategy": "random",
                "seed": 2,
                "out": "runs/job-b",
            },
        ],
    )
    manifest = epoch_grind.load_manifest(manifest_path)
    journal_path = tmp_path / "journal.json"
    config = epoch_grind.DriverConfig(
        python="/venv/bin/python",
        time_box_seconds=10,
        dup_threshold=0.02,
        low_dup_epochs=2,
        duplication_expected=True,
        nice=15,
    )
    monotonic_values = iter([0.0, 1.0, 2.0, 11.0, 20.0, 21.0, 22.0, 40.0, 41.0, 42.0])
    last_time = 42.0

    def fake_monotonic() -> float:
        nonlocal last_time
        try:
            last_time = next(monotonic_values)
        except StopIteration:
            last_time += 1.0
        return last_time

    attempts: list[tuple[str, str]] = []
    interrupted = False

    def fake_run_child(
        command: list[str],
        *,
        stdout_path: Path,
        stderr_path: Path,
        timeout: float | None,
        out_dir: Path | None = None,
        budget: int | None = None,
    ) -> epoch_grind.ChildOutcome:
        nonlocal interrupted
        del command, stdout_path, stderr_path, timeout, budget
        assert out_dir is not None
        job_id = out_dir.parent.name
        epoch_name = out_dir.name
        attempts.append((job_id, epoch_name))
        if job_id == "job-b" and epoch_name == "epoch-0002" and not interrupted:
            interrupted = True
            raise SystemExit(128 + signal.SIGTERM)
        return epoch_grind.ChildOutcome(kind="completed", returncode=0)

    def merge_recorder(base: Path, shard_paths: list[Path], **kwargs: object) -> dict[str, object]:
        seed_rows_by_source = kwargs.get("seed_rows_by_source", {})
        return {
            "target": str(base),
            "inserted_rows": len(shard_paths),
            "sources": [
                {
                    "inserted_rows": 1,
                    "seed_rows": int(seed_rows_by_source[str(path)]),
                    "source": str(path),
                    "source_rows": int(seed_rows_by_source[str(path)]) + 1,
                }
                for path in shard_paths
            ],
        }

    monkeypatch.setattr(epoch_grind.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(epoch_grind, "_run_child", fake_run_child)
    monkeypatch.setattr(epoch_grind, "merge_epoch_shards", merge_recorder)

    with pytest.raises(SystemExit) as exc_info:
        epoch_grind.run_driver(manifest, config, journal_path=journal_path)

    assert exc_info.value.code == 128 + signal.SIGTERM
    interrupted_journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert interrupted_journal["jobs_done"] == ["job-a"]
    assert interrupted_journal["jobs_remaining"] == ["job-b"]
    assert [epoch["completed_jobs"] for epoch in interrupted_journal["epochs"]] == [["job-a"]]

    assert epoch_grind.run_driver(manifest, config, journal_path=journal_path) == 0
    final_journal = json.loads(journal_path.read_text(encoding="utf-8"))

    assert attempts == [
        ("job-a", "epoch-0001"),
        ("job-b", "epoch-0002"),
        ("job-b", "epoch-0002"),
    ]
    assert final_journal["decision"] == epoch_grind.DECISION_BATCH_COMPLETE
    assert final_journal["jobs_done"] == ["job-a", "job-b"]
    assert final_journal["jobs_remaining"] == []
    assert [epoch["completed_jobs"] for epoch in final_journal["epochs"]] == [["job-a"], ["job-b"]]
    epoch_grind.verify_base_cache_integrity(manifest.base_cache)
    assert payload_count(manifest.base_cache) == 0


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

    with pytest.raises(ValueError, match="stale_journal_identity"):
        epoch_grind.load_or_initialize_journal(journal_path, second)


def test_resume_rejects_journal_with_mismatched_code_version(tmp_path: Path) -> None:
    manifest = epoch_grind.load_manifest(_manifest_file(tmp_path))
    journal = epoch_grind.initialize_journal(manifest)
    journal["code_version"] = "0.5.5"
    journal_path = tmp_path / "journal.json"
    epoch_grind.save_journal(journal_path, journal)

    with pytest.raises(ValueError, match="stale_journal_identity:.*code_version"):
        epoch_grind.load_or_initialize_journal(journal_path, manifest)


def test_resume_rejects_journal_with_mismatched_data_digest(tmp_path: Path) -> None:
    manifest = epoch_grind.load_manifest(_manifest_file(tmp_path))
    journal = epoch_grind.initialize_journal(manifest)
    journal["data_digests"]["profiles"]["job-a"] = "sha256:stale"
    journal_path = tmp_path / "journal.json"
    epoch_grind.save_journal(journal_path, journal)

    with pytest.raises(ValueError, match="stale_journal_identity:.*data_digests"):
        epoch_grind.load_or_initialize_journal(journal_path, manifest)


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


def test_pre_056_schema_journal_without_identity_is_refused(tmp_path: Path) -> None:
    manifest = epoch_grind.load_manifest(_manifest_file(tmp_path))
    journal = epoch_grind.initialize_journal(manifest)
    journal["schema_version"] = epoch_grind.LEGACY_JOURNAL_SCHEMA_VERSION
    journal.pop("code_version", None)
    journal.pop("data_digests", None)
    for job in journal["jobs"]:
        for field in epoch_grind.JOB_IDENTITY_FIELDS:
            job.pop(field, None)
    journal_path = tmp_path / "journal.json"
    journal_path.write_text(json.dumps(journal), encoding="utf-8")

    with pytest.raises(ValueError, match="stale_journal_identity"):
        epoch_grind.load_or_initialize_journal(journal_path, manifest)


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
    assert command[command.index("--per-eval-timeout-seconds") + 1] == "2700"
    assert not Path(profile_arg).exists()
    assert payload["jobs"][0]["would_write_profile"]["path"] == profile_arg
    assert payload["jobs"][0]["would_write_profile"]["content"]["run"][
        "reduced_real_cache"
    ]["db_path"].endswith("epoch-0001/shards/job-a.sqlite")

    monkeypatch.setattr(
        epoch_grind,
        "seed_job_cache",
        lambda *args, **kwargs: {"seed_rows": 0},
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


def test_optimizer_command_is_identical_with_acquisition_manifest(tmp_path: Path) -> None:
    point_ids = [_pt1_cache_point_id(f"seed-{index}") for index in range(3)]
    baseline = epoch_grind.load_manifest(_manifest_file(tmp_path))
    acquisition = epoch_grind.load_manifest(
        _manifest_file(
            tmp_path,
            jobs=[
                {
                    "id": "job-a",
                    "feedstock": "lunar_mare_low_ti",
                    "profile": "profile.json",
                    "budget": 8,
                    "strategy": "random",
                    "seed": 3,
                    "out": "runs/job-a",
                    "acquisition": {
                        "ranked_table_drain": _ranked_drain(point_ids),
                        "recompute_limit": 2,
                        "calibration_fraction": 0.5,
                    },
                }
            ],
        )
    )
    config = epoch_grind.DriverConfig(
        python="/venv/bin/python",
        time_box_seconds=7200,
        dup_threshold=0.02,
        low_dup_epochs=2,
        duplication_expected=True,
        nice=15,
    )

    baseline_command = epoch_grind.dry_run_plan(
        baseline,
        config,
        epoch_grind.initialize_journal(baseline),
    )["jobs"][0]["command"]
    acquisition_command = epoch_grind.dry_run_plan(
        acquisition,
        config,
        epoch_grind.initialize_journal(acquisition),
    )["jobs"][0]["command"]

    assert acquisition_command == baseline_command
    assert acquisition_command[acquisition_command.index("--budget") + 1] == "8"


def test_no_acquisition_prepare_job_run_keeps_empty_seed_baseline(tmp_path: Path) -> None:
    manifest = epoch_grind.load_manifest(_manifest_file(tmp_path))
    config = epoch_grind.DriverConfig(
        python="/venv/bin/python",
        time_box_seconds=7200,
        dup_threshold=0.02,
        low_dup_epochs=2,
        duplication_expected=True,
        nice=15,
    )

    prepared = epoch_grind._prepare_job_run(
        manifest.jobs[0],
        manifest,
        config,
        epoch_dir=manifest.work_dir / "epoch-0001",
        epoch_index=1,
        log_dir=manifest.work_dir / "epoch-0001" / "logs",
    )

    assert prepared.seed_rows == 0
    assert payload_count(prepared.shard_db) == 0
    assert prepared.job_record["seed_source_summary"]["acquisition_enabled"] is False
    assert prepared.job_record["seed_source_summary"]["source_counts"] == {
        "mutation": 8,
        "ladder_polish": 0,
        "calibration_uniform": 0,
    }


def test_prepare_job_run_writes_seed_summary_and_actual_seed_rows(tmp_path: Path) -> None:
    point_ids = [_pt1_cache_point_id(f"seed-{index}") for index in range(4)]
    manifest = epoch_grind.load_manifest(
        _manifest_file(
            tmp_path,
            jobs=[
                {
                    "id": "job-a",
                    "feedstock": "lunar_mare_low_ti",
                    "profile": "profile.json",
                    "budget": 8,
                    "strategy": "random",
                    "seed": 3,
                    "out": "runs/job-a",
                    "acquisition": {
                        "ranked_table_drain": _ranked_drain(
                            point_ids,
                            table_ids=point_ids,
                        ),
                        "recompute_limit": 3,
                        "calibration_fraction": 1 / 3,
                    },
                }
            ],
        )
    )
    for index in range(4):
        assert _put_pt1_cache_row(manifest.base_cache, f"seed-{index}") == point_ids[index]
    config = epoch_grind.DriverConfig(
        python="/venv/bin/python",
        time_box_seconds=7200,
        dup_threshold=0.02,
        low_dup_epochs=2,
        duplication_expected=True,
        nice=15,
    )

    prepared = epoch_grind._prepare_job_run(
        manifest.jobs[0],
        manifest,
        config,
        epoch_dir=manifest.work_dir / "epoch-0001",
        epoch_index=1,
        log_dir=manifest.work_dir / "epoch-0001" / "logs",
    )

    summary_path = Path(prepared.job_record["seed_source_manifest"])
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert prepared.seed_rows == 3
    assert payload_count(prepared.shard_db) == 3
    assert summary["source_counts"] == {
        "mutation": 8,
        "ladder_polish": 2,
        "calibration_uniform": 1,
    }
    assert summary["seed_rows"] == 3
    assert summary["seed_source_table"] == epoch_grind.SEED_SOURCE_TABLE
    con = sqlite3.connect(prepared.shard_db)
    try:
        rows = con.execute(
            f"""
            SELECT point_id, source
            FROM {epoch_grind.SEED_SOURCE_TABLE}
            ORDER BY source_index
            """
        ).fetchall()
    finally:
        con.close()
    assert rows == [
        (summary["point_sources"][0]["point_id"], summary["point_sources"][0]["source"]),
        (summary["point_sources"][1]["point_id"], summary["point_sources"][1]["source"]),
        (summary["point_sources"][2]["point_id"], summary["point_sources"][2]["source"]),
    ]


def test_merge_epoch_shards_preserves_seed_source_labels_in_base_cache(tmp_path: Path) -> None:
    base = tmp_path / "base.sqlite"
    shard = tmp_path / "epoch" / "shards" / "job-a.sqlite"
    point_id = _put_pt1_cache_row(base, "seeded")
    seed_summary = epoch_grind.seed_job_cache(
        shard,
        base,
        point_sources=[{"point_id": point_id, "source": "ladder_polish"}],
        job_id="job-a",
    )

    merge_summary = epoch_grind.merge_epoch_shards(
        base,
        [shard],
        seed_rows_by_source={str(shard): int(seed_summary["seed_rows"])},
    )

    con = sqlite3.connect(base)
    try:
        rows = con.execute(
            f"""
            SELECT point_id, source, job_id
            FROM {epoch_grind.SEED_SOURCE_TABLE}
            """
        ).fetchall()
    finally:
        con.close()
    assert merge_summary["seed_source_rows"] == 1
    assert rows == [(point_id, "ladder_polish", "job-a")]


def test_seed_job_cache_rejects_stale_epoch_seed_without_counting_coverage(
    tmp_path: Path,
) -> None:
    base = tmp_path / "base.sqlite"
    shard = tmp_path / "epoch" / "shards" / "job-a.sqlite"
    current_id = _put_pt1_cache_row(base, "current")
    stale_id = _put_pt1_cache_row(
        base,
        "stale",
        allowlist_version="allowlist-old",
    )

    summary = epoch_grind.seed_job_cache(
        shard,
        base,
        point_sources=[
            {"point_id": current_id, "source": "ladder_polish"},
            {"point_id": stale_id, "source": "calibration_uniform"},
        ],
        job_id="job-a",
        expected_seed_epoch=epoch_grind._current_seed_epoch_identity(),
    )

    assert summary["seed_rows"] == 1
    assert summary["rejected_seed_rows"] == 1
    assert payload_count(shard) == 1
    rejected = summary["rejected_seed_point_sources"][0]
    assert rejected["point_id"] == stale_id
    assert rejected["reason"] == "allowlist_version_mismatch"

    con = sqlite3.connect(shard)
    try:
        rows = con.execute(
            f"""
            SELECT point_id, source
            FROM {epoch_grind.SEED_SOURCE_TABLE}
            """
        ).fetchall()
    finally:
        con.close()
    assert rows == [(current_id, "ladder_polish")]


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

    profile_arg = epoch_grind.write_epoch_profile(
        job,
        tmp_path,
        shard,
        tmp_path / "epoch",
        base_cache=manifest.base_cache,
    )
    profile = json.loads(Path(profile_arg).read_text(encoding="utf-8"))

    assert profile["run"]["reduced_real_cache"]["db_path"] == str(shard)
    assert profile["run"]["reduced_real_cache"]["read_only_base_db_path"] == str(
        manifest.base_cache
    )


def test_timeboxed_child_stays_pending_without_merging_partial_shard(
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
        lambda *args, **kwargs: {"seed_rows": 0},
    )
    monkeypatch.setattr(
        epoch_grind,
        "_run_child",
        lambda *args, **kwargs: epoch_grind.ChildOutcome(kind="timed_out"),
    )

    result = epoch_grind.run_epoch(manifest, manifest.jobs, config, epoch_index=1)
    journal = epoch_grind.initialize_journal(manifest)
    epoch_grind._apply_epoch_result(journal, result)

    assert result["timed_out_jobs"][0]["id"] == "job-a"
    assert result["timed_out_jobs"][0]["reason"] == "epoch_child_timeout"
    assert result["timed_out_jobs"][0]["failure_counts"] == {"epoch_child_timeout": 1}
    assert result["completed_jobs"] == []
    assert result["failed_jobs"] == []
    assert result["shard_dbs"] == []
    assert result["seed_rows_by_shard"] == {}
    assert [job.id for job in epoch_grind.pending_jobs(manifest, journal)] == ["job-a"]


def test_rerun_epoch_removes_stale_job_output_dir(
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
    stale_out = manifest.jobs[0].out / "epoch-0001"
    stale_out.mkdir(parents=True)
    (stale_out / "cache.sqlite").write_text("stale partial cache", encoding="utf-8")
    monkeypatch.setattr(
        epoch_grind,
        "seed_job_cache",
        lambda *args, **kwargs: {"seed_rows": 0},
    )
    monkeypatch.setattr(
        epoch_grind,
        "_run_child",
        lambda *args, **kwargs: epoch_grind.ChildOutcome(kind="completed", returncode=0),
    )

    epoch_grind.run_epoch(manifest, manifest.jobs, config, epoch_index=1)

    assert not (stale_out / "cache.sqlite").exists()


def test_epoch_child_cache_gate_receives_profile_strict_vapor_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = _manifest_file(tmp_path)
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    profile_path = Path(str(manifest_payload["jobs"][0]["profile"]))
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    profile["run"]["reduced_real_cache"]["strict_vapor_gate"] = True
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    manifest = epoch_grind.load_manifest(manifest_path)
    config = epoch_grind.DriverConfig(
        python="/venv/bin/python",
        time_box_seconds=7200,
        dup_threshold=0.02,
        low_dup_epochs=2,
        duplication_expected=True,
        nice=15,
    )
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        epoch_grind,
        "seed_job_cache",
        lambda *args, **kwargs: {"seed_rows": 0},
    )
    monkeypatch.setattr(
        epoch_grind,
        "_run_child",
        lambda *args, **kwargs: epoch_grind.ChildOutcome(kind="completed", returncode=0),
    )

    def record_cache_gate(db_path: Path, **kwargs: object) -> dict[str, int]:
        calls.append({"db_path": db_path, **kwargs})
        return {"rows": 0, "vapor_active_rows": 0, "source_reports": 0}

    monkeypatch.setattr(
        epoch_grind,
        "assert_strict_vapor_result_store",
        record_cache_gate,
    )

    epoch_grind.run_epoch(manifest, manifest.jobs, config, epoch_index=1)

    assert calls == [
        {
            "db_path": manifest.jobs[0].out / "epoch-0001" / "cache.sqlite",
            "context": "job-a:cache.sqlite",
            "strict_vapor_gate": True,
        }
    ]


def test_seed_job_cache_removes_stale_sqlite_sidecars(tmp_path: Path) -> None:
    base = tmp_path / "base.sqlite"
    shard = tmp_path / "epoch" / "shards" / "job-a.sqlite"
    shard.parent.mkdir(parents=True)
    for path in (shard, shard.with_name(shard.name + "-wal"), shard.with_name(shard.name + "-shm")):
        path.write_text("stale", encoding="utf-8")

    epoch_grind.seed_job_cache(shard, base)

    assert shard.exists()
    assert not shard.with_name(shard.name + "-wal").exists()
    assert not shard.with_name(shard.name + "-shm").exists()


def test_seed_job_cache_retries_transient_oserror_during_sqlite_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    base = tmp_path / "base.sqlite"
    shard = tmp_path / "epoch" / "shards" / "job-a.sqlite"
    shard.parent.mkdir(parents=True)
    shard.write_text("stale", encoding="utf-8")
    original_unlink = Path.unlink
    failures = 0

    def flaky_unlink(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal failures
        if path == shard and failures == 0:
            failures += 1
            raise OSError("transient unlink race")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)

    with caplog.at_level(logging.WARNING, logger=epoch_grind.__name__):
        epoch_grind.seed_job_cache(shard, base)

    assert failures == 1
    assert shard.exists()
    assert "stale sqlite cleanup retrying" in caplog.text


def test_rerun_epoch_retries_transient_oserror_during_stale_output_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
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
    stale_out = manifest.jobs[0].out / "epoch-0001"
    stale_out.mkdir(parents=True)
    stale_marker = stale_out / "cache.sqlite"
    stale_marker.write_text("stale partial cache", encoding="utf-8")
    original_rmtree = epoch_grind.shutil.rmtree
    failures = 0

    def flaky_rmtree(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal failures
        if Path(path) == stale_out and failures == 0:
            failures += 1
            raise OSError("transient rmtree race")
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(epoch_grind.shutil, "rmtree", flaky_rmtree)
    monkeypatch.setattr(
        epoch_grind,
        "seed_job_cache",
        lambda *args, **kwargs: {"seed_rows": 0},
    )
    monkeypatch.setattr(
        epoch_grind,
        "_run_child",
        lambda *args, **kwargs: epoch_grind.ChildOutcome(kind="completed", returncode=0),
    )

    with caplog.at_level(logging.WARNING, logger=epoch_grind.__name__):
        epoch_grind.run_epoch(manifest, manifest.jobs, config, epoch_index=1)

    assert failures == 1
    assert not stale_marker.exists()
    assert "stale output dir cleanup retrying" in caplog.text


def test_terminate_active_children_forwards_to_registered_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProcess:
        def poll(self) -> None:
            return None

    process = FakeProcess()
    terminated: list[object] = []
    with epoch_grind._ACTIVE_CHILDREN_LOCK:
        epoch_grind._ACTIVE_CHILDREN.clear()
    monkeypatch.setattr(
        epoch_grind,
        "_terminate_process_group",
        lambda child: terminated.append(child),
    )

    epoch_grind._register_child(process)  # type: ignore[arg-type]
    epoch_grind._terminate_active_children()
    epoch_grind._unregister_child(process)  # type: ignore[arg-type]

    assert terminated == [process]


def test_ioreg_monitor_records_epoch_boundaries_and_eval_interval(
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
        ioreg_sample_every_evals=4,
    )
    counts = iter([40, 43, 45])

    def fake_sample() -> dict[str, object]:
        return {
            "timestamp_utc": "2026-06-15T00:00:00Z",
            "platform": "darwin",
            "command": epoch_grind.IOREG_IOSURFACE_COMMAND,
            "status": "ok",
            "count": next(counts),
            "returncode": 0,
        }

    monkeypatch.setattr(epoch_grind, "sample_iosurface_client_count", fake_sample)
    monkeypatch.setattr(
        epoch_grind,
        "seed_job_cache",
        lambda *args, **kwargs: {"seed_rows": 0},
    )
    monkeypatch.setattr(
        epoch_grind,
        "_run_child",
        lambda *args, **kwargs: epoch_grind.ChildOutcome(kind="completed", returncode=0),
    )
    monkeypatch.setattr(
        epoch_grind,
        "merge_epoch_shards",
        lambda *args, **kwargs: {"inserted_rows": 0, "sources": []},
    )

    assert epoch_grind.run_driver(manifest, config, journal_path=journal_path) == 0
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    ioreg = journal["epochs"][0]["ioreg"]
    samples = ioreg["samples"]

    assert ioreg["enabled"] is True
    assert ioreg["sample_every_evals"] == 4
    assert [sample["label"] for sample in samples] == [
        "epoch_start",
        "eval_interval",
        "epoch_end",
    ]
    assert [sample["count"] for sample in samples] == [40, 43, 45]
    assert [sample["delta_from_baseline"] for sample in samples] == [0, 3, 5]
    assert samples[1]["evals_attempted"] == 8
    log_path = Path(ioreg["log"])
    log_rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert [row["label"] for row in log_rows] == ["epoch_start", "eval_interval", "epoch_end"]


def test_run_child_classifies_child_owned_rc_124_as_failure(tmp_path: Path) -> None:
    outcome = epoch_grind._run_child(
        [sys.executable, "-c", "raise SystemExit(124)"],
        stdout_path=tmp_path / "child.stdout.log",
        stderr_path=tmp_path / "child.stderr.log",
        timeout=5.0,
    )

    assert outcome == epoch_grind.ChildOutcome(kind="failed", returncode=124)


def test_run_child_classifies_structured_no_feasible_as_terminal(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    _write_no_feasible_artifacts(out_dir, budget=3)

    outcome = epoch_grind._run_child(
        [sys.executable, "-c", "raise SystemExit(2)"],
        stdout_path=tmp_path / "child.stdout.log",
        stderr_path=tmp_path / "child.stderr.log",
        timeout=5.0,
        out_dir=out_dir,
        budget=3,
    )

    assert outcome == epoch_grind.ChildOutcome(
        kind=epoch_grind.NO_FEASIBLE_STATUS,
        returncode=2,
        failure_counts={"infeasible_recipe": 3},
    )


def test_run_child_classifies_structured_non_finite_no_feasible_as_terminal(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    _write_no_feasible_artifacts(
        out_dir,
        budget=3,
        message=NO_FEASIBLE_NON_FINITE_MESSAGE,
        failure_counts={"non_finite_payload": 3},
    )

    outcome = epoch_grind._run_child(
        [sys.executable, "-c", "raise SystemExit(2)"],
        stdout_path=tmp_path / "child.stdout.log",
        stderr_path=tmp_path / "child.stderr.log",
        timeout=5.0,
        out_dir=out_dir,
        budget=3,
    )

    assert outcome == epoch_grind.ChildOutcome(
        kind=epoch_grind.NO_FEASIBLE_STATUS,
        returncode=2,
        failure_counts={"non_finite_payload": 3},
    )


def test_run_child_classifies_stderr_no_feasible_when_status_missing(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    _write_no_feasible_artifacts(out_dir, budget=3, write_status=False)

    outcome = epoch_grind._run_child(
        [
            sys.executable,
            "-c",
            f"import sys; print({NO_FEASIBLE_STDERR_LINE!r}, file=sys.stderr); raise SystemExit(2)",
        ],
        stdout_path=tmp_path / "child.stdout.log",
        stderr_path=tmp_path / "child.stderr.log",
        timeout=5.0,
        out_dir=out_dir,
        budget=3,
    )

    assert outcome == epoch_grind.ChildOutcome(
        kind=epoch_grind.NO_FEASIBLE_STATUS,
        returncode=2,
        failure_counts={"infeasible_recipe": 3},
    )


@pytest.mark.parametrize("status_payload", ["{not-json", "{}"])
def test_run_child_falls_back_to_stderr_when_status_unparseable_or_empty(
    tmp_path: Path,
    status_payload: str,
) -> None:
    out_dir = tmp_path / "run"
    _write_no_feasible_artifacts(out_dir, budget=3)
    (out_dir / "job_status.json").write_text(status_payload, encoding="utf-8")

    outcome = epoch_grind._run_child(
        [
            sys.executable,
            "-c",
            f"import sys; print({NO_FEASIBLE_STDERR_LINE!r}, file=sys.stderr); raise SystemExit(2)",
        ],
        stdout_path=tmp_path / "child.stdout.log",
        stderr_path=tmp_path / "child.stderr.log",
        timeout=5.0,
        out_dir=out_dir,
        budget=3,
    )

    assert outcome == epoch_grind.ChildOutcome(
        kind=epoch_grind.NO_FEASIBLE_STATUS,
        returncode=2,
        failure_counts={"infeasible_recipe": 3},
    )


def test_run_child_keeps_partial_no_feasible_artifacts_failed(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    _write_no_feasible_artifacts(out_dir, budget=3, provenance_rows=2, write_status=False)

    outcome = epoch_grind._run_child(
        [
            sys.executable,
            "-c",
            f"import sys; print({NO_FEASIBLE_STDERR_LINE!r}, file=sys.stderr); raise SystemExit(2)",
        ],
        stdout_path=tmp_path / "child.stdout.log",
        stderr_path=tmp_path / "child.stderr.log",
        timeout=5.0,
        out_dir=out_dir,
        budget=3,
    )

    assert outcome == epoch_grind.ChildOutcome(kind="failed", returncode=2)


def test_run_child_classifies_stale_profile_status_as_terminal(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    _write_stale_profile_status(out_dir)

    outcome = epoch_grind._run_child(
        [sys.executable, "-c", "raise SystemExit(2)"],
        stdout_path=tmp_path / "child.stdout.log",
        stderr_path=tmp_path / "child.stderr.log",
        timeout=5.0,
        out_dir=out_dir,
        budget=3,
    )

    assert outcome == epoch_grind.ChildOutcome(
        kind=epoch_grind.STALE_PROFILE_STATUS,
        returncode=2,
        failure_counts={"stale_profile": 1},
        reason="ProfileValidationError",
        message=STALE_PROFILE_MESSAGE,
    )


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
        lambda *args, **kwargs: {"seed_rows": 0},
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


def test_no_feasible_job_is_terminal_mergeable_and_counted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
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
        lambda *args, **kwargs: {"seed_rows": 0},
    )
    monkeypatch.setattr(
        epoch_grind,
        "_run_child",
        lambda *args, **kwargs: epoch_grind.ChildOutcome(
            kind=epoch_grind.NO_FEASIBLE_STATUS,
            returncode=2,
            failure_counts={"infeasible_recipe": 8},
        ),
    )

    def merge_recorder(base: Path, shard_paths: list[Path], **kwargs: object) -> dict[str, object]:
        merged_shards.extend(shard_paths)
        seed_rows_by_source = kwargs.get("seed_rows_by_source", {})
        return {
            "inserted_rows": len(shard_paths),
            "sources": [
                {
                    "inserted_rows": 1,
                    "seed_rows": int(seed_rows_by_source[str(path)]),
                    "source": str(path),
                    "source_rows": int(seed_rows_by_source[str(path)]) + 1,
                }
                for path in shard_paths
            ],
        }

    monkeypatch.setattr(epoch_grind, "merge_epoch_shards", merge_recorder)

    assert epoch_grind.run_driver(manifest, config, journal_path=journal_path) == 0
    journal = json.loads(journal_path.read_text(encoding="utf-8"))

    assert journal["decision"] == epoch_grind.DECISION_BATCH_COMPLETE
    assert journal["jobs"][0]["status"] == epoch_grind.NO_FEASIBLE_STATUS
    assert journal["jobs_no_feasible"] == ["job-a"]
    assert journal["job_status_counts"] == {epoch_grind.NO_FEASIBLE_STATUS: 1}
    assert journal["jobs"][0]["failure_counts"] == {"infeasible_recipe": 8}
    assert journal["epochs"][0]["no_feasible_jobs"][0]["id"] == "job-a"
    assert journal["epochs"][0]["no_feasible_jobs"][0]["returncode"] == 2
    assert journal["epochs"][0]["no_feasible_jobs"][0]["failure_counts"] == {"infeasible_recipe": 8}
    assert merged_shards == [manifest.work_dir / "epoch-0001" / "shards" / "job-a.sqlite"]
    assert epoch_grind.pending_jobs(manifest, journal) == []
    out = capsys.readouterr().out
    assert "no_feasible=1" in out
    assert 'status_counts={"no_feasible":1}' in out
    assert 'no_feasible_failure_counts=job-a:{"infeasible_recipe":8}' in out


def test_stale_profile_job_is_terminal_mergeable_and_counted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
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
        lambda *args, **kwargs: {"seed_rows": 0},
    )
    monkeypatch.setattr(
        epoch_grind,
        "_run_child",
        lambda *args, **kwargs: epoch_grind.ChildOutcome(
            kind=epoch_grind.STALE_PROFILE_STATUS,
            returncode=2,
            failure_counts={"stale_profile": 1},
            reason="ProfileValidationError",
            message=STALE_PROFILE_MESSAGE,
        ),
    )

    def merge_recorder(base: Path, shard_paths: list[Path], **kwargs: object) -> dict[str, object]:
        merged_shards.extend(shard_paths)
        seed_rows_by_source = kwargs.get("seed_rows_by_source", {})
        return {
            "inserted_rows": len(shard_paths),
            "sources": [
                {
                    "inserted_rows": 1,
                    "seed_rows": int(seed_rows_by_source[str(path)]),
                    "source": str(path),
                    "source_rows": int(seed_rows_by_source[str(path)]) + 1,
                }
                for path in shard_paths
            ],
        }

    monkeypatch.setattr(epoch_grind, "merge_epoch_shards", merge_recorder)

    assert epoch_grind.run_driver(manifest, config, journal_path=journal_path) == 0
    journal = json.loads(journal_path.read_text(encoding="utf-8"))

    assert journal["decision"] == epoch_grind.DECISION_BATCH_COMPLETE
    assert journal["jobs"][0]["status"] == epoch_grind.STALE_PROFILE_STATUS
    assert journal["jobs_stale_profile"] == ["job-a"]
    assert journal["job_status_counts"] == {epoch_grind.STALE_PROFILE_STATUS: 1}
    assert journal["jobs"][0]["failure_counts"] == {"stale_profile": 1}
    assert journal["jobs"][0]["reason"] == "ProfileValidationError"
    assert journal["jobs"][0]["message"] == STALE_PROFILE_MESSAGE
    assert journal["epochs"][0]["stale_profile_jobs"][0]["id"] == "job-a"
    assert journal["epochs"][0]["stale_profile_jobs"][0]["returncode"] == 2
    assert journal["epochs"][0]["stale_profile_jobs"][0]["failure_counts"] == {
        "stale_profile": 1
    }
    assert merged_shards == [manifest.work_dir / "epoch-0001" / "shards" / "job-a.sqlite"]
    assert epoch_grind.pending_jobs(manifest, journal) == []
    out = capsys.readouterr().out
    assert "stale_profile=1" in out
    assert 'status_counts={"stale_profile":1}' in out
    assert 'terminal_failure_counts=job-a:{"stale_profile":1}' in out


def _put_shard_row(shard_db: Path, *, tag: str, base_db: Path | None = None) -> None:
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
    PT1PersistentEquilibriumStore(
        shard_db,
        read_only_base_db_path=base_db,
    ).put(
        artifact="freeze_gate_curve",
        key=key,
        key_bytes=key_bytes,
        key_hash=hashlib.sha256(key_bytes).hexdigest(),
        payload=payload,
        payload_bytes=payload_bytes,
        payload_hash=hashlib.sha256(payload_bytes).hexdigest(),
    )


def test_concurrent_jobs_complete_with_isolated_shards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
                    },
                },
                "fidelities": {"fast": {}},
                "seed_recipes": [],
            }
        ),
        encoding="utf-8",
    )
    job_ids = ["job-a", "job-b", "job-c"]
    jobs = [
        {
            "id": job_id,
            "feedstock": "lunar_mare_low_ti",
            "profile": str(profile),
            "budget": 4,
            "strategy": "random",
            "seed": index,
            "out": f"runs/{job_id}",
        }
        for index, job_id in enumerate(job_ids, start=1)
    ]
    manifest = epoch_grind.load_manifest(_manifest_file(tmp_path, jobs=jobs))
    journal_path = tmp_path / "journal.json"
    config = epoch_grind.DriverConfig(
        python="/venv/bin/python",
        time_box_seconds=7200,
        dup_threshold=0.02,
        low_dup_epochs=2,
        duplication_expected=True,
        nice=15,
        job_concurrency=3,
    )

    overlap_lock = threading.Lock()
    in_flight = 0
    max_in_flight = 0
    lock_errors: list[str] = []
    rows_per_job = 8

    def fake_run_child(
        command: list[str],
        *,
        stdout_path: Path,
        stderr_path: Path,
        timeout: float | None,
        out_dir: Path | None = None,
        budget: int | None = None,
    ) -> epoch_grind.ChildOutcome:
        del stdout_path, stderr_path, timeout, out_dir, budget
        nonlocal in_flight, max_in_flight
        profile_path = Path(command[command.index("--profile") + 1])
        profile_payload = json.loads(profile_path.read_text(encoding="utf-8"))
        cache = profile_payload["run"]["reduced_real_cache"]
        shard_db = Path(cache["db_path"])
        base_db = Path(cache["read_only_base_db_path"])
        with overlap_lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        try:
            for row_index in range(rows_per_job):
                try:
                    _put_shard_row(
                        shard_db,
                        tag=f"{shard_db.stem}-{row_index}",
                        base_db=base_db,
                    )
                except sqlite3.OperationalError as exc:
                    if "database is locked" in str(exc).lower():
                        lock_errors.append(str(exc))
                    raise
            time.sleep(0.02)
        finally:
            with overlap_lock:
                in_flight -= 1
        return epoch_grind.ChildOutcome(kind="completed", returncode=0)

    monkeypatch.setattr(epoch_grind, "_run_child", fake_run_child)

    assert epoch_grind.run_driver(manifest, config, journal_path=journal_path) == 0
    journal = json.loads(journal_path.read_text(encoding="utf-8"))

    assert lock_errors == []
    assert max_in_flight >= 2
    assert journal["decision"] == epoch_grind.DECISION_BATCH_COMPLETE
    assert set(journal["jobs_done"]) == set(job_ids)
    assert journal["job_status_counts"] == {"done": len(job_ids)}
    epoch = journal["epochs"][0]
    assert epoch["job_concurrency"] == 3
    assert set(epoch["completed_jobs"]) == set(job_ids)
    assert len(epoch["shard_dbs"]) == len(job_ids)
    assert int(epoch["merge"]["inserted_rows"]) == len(job_ids) * rows_per_job
    assert payload_count(manifest.base_cache) == len(job_ids) * rows_per_job


@pytest.mark.parametrize("terminal_kind", ["failed", "timed_out"])
def test_concurrent_jobs_failure_drains_in_flight_siblings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal_kind: str,
) -> None:
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
                    },
                },
                "fidelities": {"fast": {}},
                "seed_recipes": [],
            }
        ),
        encoding="utf-8",
    )
    job_ids = ["job-a", "job-b", "job-c"]
    failing_job = "job-b"
    jobs = [
        {
            "id": job_id,
            "feedstock": "lunar_mare_low_ti",
            "profile": str(profile),
            "budget": 4,
            "strategy": "random",
            "seed": index,
            "out": f"runs/{job_id}",
        }
        for index, job_id in enumerate(job_ids, start=1)
    ]
    manifest = epoch_grind.load_manifest(_manifest_file(tmp_path, jobs=jobs))
    journal_path = tmp_path / "journal.json"
    config = epoch_grind.DriverConfig(
        python="/venv/bin/python",
        time_box_seconds=7200,
        dup_threshold=0.02,
        low_dup_epochs=2,
        duplication_expected=True,
        nice=15,
        job_concurrency=3,
    )

    overlap_lock = threading.Lock()
    in_flight = 0
    max_in_flight = 0
    lock_errors: list[str] = []
    rows_per_job = 8
    failing_job_calls = 0

    def fake_run_child(
        command: list[str],
        *,
        stdout_path: Path,
        stderr_path: Path,
        timeout: float | None,
        out_dir: Path | None = None,
        budget: int | None = None,
    ) -> epoch_grind.ChildOutcome:
        del stdout_path, stderr_path, timeout, out_dir, budget
        nonlocal in_flight, max_in_flight, failing_job_calls
        profile_path = Path(command[command.index("--profile") + 1])
        profile_payload = json.loads(profile_path.read_text(encoding="utf-8"))
        cache = profile_payload["run"]["reduced_real_cache"]
        shard_db = Path(cache["db_path"])
        base_db = Path(cache["read_only_base_db_path"])
        job_id = shard_db.stem
        with overlap_lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        try:
            if job_id == failing_job:
                failing_job_calls += 1
                time.sleep(0.05)
                if terminal_kind == "failed":
                    return epoch_grind.ChildOutcome(kind="failed", returncode=2)
                if failing_job_calls == 1:
                    for row_index in range(2):
                        try:
                            _put_shard_row(
                                shard_db,
                                tag=f"{shard_db.stem}-partial-{row_index}",
                                base_db=base_db,
                            )
                        except sqlite3.OperationalError as exc:
                            if "database is locked" in str(exc).lower():
                                lock_errors.append(str(exc))
                            raise
                    return epoch_grind.ChildOutcome(kind="timed_out")
                for row_index in range(rows_per_job):
                    try:
                        _put_shard_row(
                            shard_db,
                            tag=f"{shard_db.stem}-{row_index}",
                            base_db=base_db,
                        )
                    except sqlite3.OperationalError as exc:
                        if "database is locked" in str(exc).lower():
                            lock_errors.append(str(exc))
                        raise
                return epoch_grind.ChildOutcome(kind="completed", returncode=0)

            time.sleep(0.15)
            for row_index in range(rows_per_job):
                try:
                    _put_shard_row(
                        shard_db,
                        tag=f"{shard_db.stem}-{row_index}",
                        base_db=base_db,
                    )
                except sqlite3.OperationalError as exc:
                    if "database is locked" in str(exc).lower():
                        lock_errors.append(str(exc))
                    raise
        finally:
            with overlap_lock:
                in_flight -= 1
        return epoch_grind.ChildOutcome(kind="completed", returncode=0)

    monkeypatch.setattr(epoch_grind, "_run_child", fake_run_child)

    expected_exit = 2 if terminal_kind == "failed" else 0
    assert epoch_grind.run_driver(manifest, config, journal_path=journal_path) == expected_exit
    journal = json.loads(journal_path.read_text(encoding="utf-8"))

    assert lock_errors == []
    assert max_in_flight >= 2
    epoch1 = journal["epochs"][0]
    assert epoch1["job_concurrency"] == 3
    sibling_ids = {"job-a", "job-c"}
    assert set(epoch1["completed_jobs"]) == sibling_ids
    assert set(journal["jobs_done"]) == (sibling_ids if terminal_kind == "failed" else set(job_ids))

    if terminal_kind == "failed":
        assert journal["decision"] == epoch_grind.DECISION_FAILED
        assert epoch1["timed_out_jobs"] == []
        assert len(epoch1["failed_jobs"]) == 1
        assert epoch1["failed_jobs"][0]["id"] == failing_job
        assert epoch1["failed_jobs"][0]["returncode"] == 2
        failed_status = next(
            job["status"] for job in journal["jobs"] if job["id"] == failing_job
        )
        assert failed_status == "failed"
        assert int(epoch1["merge"]["inserted_rows"]) == len(sibling_ids) * rows_per_job
        assert payload_count(manifest.base_cache) == len(sibling_ids) * rows_per_job
    else:
        assert epoch1["timed_out_jobs"][0]["id"] == failing_job
        assert epoch1["timed_out_jobs"][0]["reason"] == "epoch_child_timeout"
        assert epoch1["failed_jobs"] == []
        timed_out_status = next(
            job["status"] for job in journal["jobs"] if job["id"] == failing_job
        )
        assert timed_out_status == "done"
        assert int(epoch1["merge"]["inserted_rows"]) == len(sibling_ids) * rows_per_job
        assert payload_count(manifest.base_cache) == len(job_ids) * rows_per_job


def test_final_long_epoch_uses_configured_wall_cap(
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
        final_long_timeout_seconds=123,
    )
    captured: list[float | None] = []

    monkeypatch.setattr(
        epoch_grind,
        "seed_job_cache",
        lambda *args, **kwargs: {"seed_rows": 0},
    )

    def fake_run_child(
        command: list[str],
        *,
        stdout_path: Path,
        stderr_path: Path,
        timeout: float | None,
        out_dir: Path | None = None,
        budget: int | None = None,
    ) -> epoch_grind.ChildOutcome:
        del command, stdout_path, stderr_path, out_dir, budget
        captured.append(timeout)
        return epoch_grind.ChildOutcome(
            kind="timed_out",
            reason="epoch_child_timeout",
            timeout_seconds=timeout,
        )

    monkeypatch.setattr(epoch_grind, "_run_child", fake_run_child)

    result = epoch_grind.run_epoch(
        manifest,
        manifest.jobs,
        config,
        epoch_index=1,
        final_long=True,
    )

    assert result["mode"] == "final_long"
    assert result["wall_timeout_seconds"] == 123
    assert captured and 0 < captured[0] <= 123
    assert result["timed_out_jobs"][0]["reason"] == "epoch_child_timeout"


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
        lambda *args, **kwargs: {"seed_rows": 0},
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
