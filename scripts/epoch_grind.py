"""Epoch-merge-redispatch driver for reduced-real cache grinding.

This is a local orchestrator. It launches one `python -m simulator.optimize`
study per manifest job, using a fresh per-epoch cache shard seeded from the
shared base cache. After each time-boxed epoch it merges shard DBs back into the
base through `scripts.seed_reduced_real_cache.seed_cache`, records the
duplication rate, then either redispatches another epoch or switches to one
final unboxed run when duplication has stayed low.

Minimal JSON manifest:

{
  "base_cache": "cache/base.sqlite",
  "work_dir": "runs/epoch-grind",
  "fidelity": "fast",
  "parallel": 1,
  "jobs": [
    {
      "id": "mare-random",
      "feedstock": "lunar_mare_low_ti",
      "profile": "data/optimize_profiles/lunar_mare_low_ti.yaml",
      "budget": 256,
      "strategy": "random",
      "seed": 11,
      "out": "runs/mare-random"
    }
  ]
}
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    import yaml
except ImportError:  # pragma: no cover - project runtime normally has PyYAML.
    yaml = None  # type: ignore[assignment]

from scripts.seed_reduced_real_cache import seed_cache
from simulator.reduced_real_determinism import PT1PersistentEquilibriumStore


DEFAULT_TIME_BOX_SECONDS = 2 * 60 * 60
DEFAULT_DUP_THRESHOLD = 0.02
DEFAULT_LOW_DUP_EPOCHS = 2
JOURNAL_SCHEMA_VERSION = 1
DECISION_CONTINUE = "continue"
DECISION_FINAL_LONG = "final_long"
DECISION_BATCH_COMPLETE = "batch_complete"
DECISION_FAILED = "failed"


@dataclass(frozen=True)
class JobSpec:
    id: str
    feedstock: str
    profile: str
    budget: int
    strategy: str
    seed: int
    out: Path
    fidelity: str
    parallel: int
    reduced_real_cache: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class Manifest:
    path: Path
    base_cache: Path
    work_dir: Path
    jobs: tuple[JobSpec, ...]


@dataclass(frozen=True)
class DriverConfig:
    python: str
    time_box_seconds: int | None
    dup_threshold: float
    low_dup_epochs: int
    duplication_expected: bool
    nice: int


def load_manifest(path: Path, *, base_cache: Path | None = None, work_dir: Path | None = None) -> Manifest:
    path = path.expanduser().resolve()
    raw = _load_mapping(path)
    default_fidelity = str(raw.get("fidelity", "fast"))
    default_parallel = _positive_int(raw.get("parallel", 1), "parallel")
    resolved_base = _resolve_path(
        base_cache if base_cache is not None else _required(raw, "base_cache"),
        path.parent,
    )
    resolved_work = _resolve_path(
        work_dir if work_dir is not None else raw.get("work_dir", path.with_suffix("").name + "-epochs"),
        path.parent,
    )

    raw_jobs = raw.get("jobs")
    if not isinstance(raw_jobs, list) or not raw_jobs:
        raise ValueError(f"{path}: jobs must be a non-empty list")

    jobs = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw_jobs, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"{path}: jobs[{index}] must be a mapping")
        job_id = str(item.get("id") or _default_job_id(item, index))
        if job_id in seen_ids:
            raise ValueError(f"{path}: duplicate job id {job_id!r}")
        seen_ids.add(job_id)
        jobs.append(
            JobSpec(
                id=job_id,
                feedstock=str(_required(item, "feedstock")),
                profile=str(_required(item, "profile")),
                budget=_positive_int(_required(item, "budget"), f"jobs[{index}].budget"),
                strategy=str(_required(item, "strategy")),
                seed=_non_negative_int(_required(item, "seed"), f"jobs[{index}].seed"),
                out=_resolve_path(_required(item, "out"), path.parent),
                fidelity=str(item.get("fidelity", default_fidelity)),
                parallel=_positive_int(item.get("parallel", default_parallel), f"jobs[{index}].parallel"),
                reduced_real_cache=(
                    dict(item["reduced_real_cache"])
                    if isinstance(item.get("reduced_real_cache"), Mapping)
                    else None
                ),
            )
        )
    return Manifest(path=path, base_cache=resolved_base, work_dir=resolved_work, jobs=tuple(jobs))


def duplication_rate(source_rows: int, inserted_rows: int) -> float:
    if source_rows <= 0:
        return 0.0
    rate = 1.0 - (inserted_rows / source_rows)
    return max(0.0, min(1.0, rate))


def duplication_rate_from_merge(summary: Mapping[str, Any]) -> float:
    source_rows = 0
    for source in summary.get("sources", []):
        if isinstance(source, Mapping) and source.get("skipped") != "target":
            source_rows += int(source.get("source_rows", 0))
    return duplication_rate(source_rows, int(summary.get("inserted_rows", 0)))


def adaptive_decision(
    dup_rates: Sequence[float],
    *,
    remaining_jobs: int,
    threshold: float = DEFAULT_DUP_THRESHOLD,
    consecutive: int = DEFAULT_LOW_DUP_EPOCHS,
    duplication_expected: bool = True,
) -> str:
    if remaining_jobs <= 0:
        return DECISION_BATCH_COMPLETE
    if not duplication_expected:
        return DECISION_CONTINUE
    if consecutive <= 0:
        raise ValueError("consecutive must be positive")
    if len(dup_rates) < consecutive:
        return DECISION_CONTINUE
    if all(rate < threshold for rate in dup_rates[-consecutive:]):
        return DECISION_FINAL_LONG
    return DECISION_CONTINUE


def initialize_journal(manifest: Manifest) -> dict[str, Any]:
    return {
        "schema_version": JOURNAL_SCHEMA_VERSION,
        "manifest": str(manifest.path),
        "base_cache": str(manifest.base_cache),
        "work_dir": str(manifest.work_dir),
        "epoch": 0,
        "decision": DECISION_CONTINUE,
        "dup_rates": [],
        "jobs_done": [],
        "jobs_remaining": [job.id for job in manifest.jobs],
        "jobs": [
            {
                "id": job.id,
                "status": "pending",
                "feedstock": job.feedstock,
                "profile": job.profile,
                "budget": job.budget,
                "strategy": job.strategy,
                "seed": job.seed,
                "out": str(job.out),
                "fidelity": job.fidelity,
                "parallel": job.parallel,
            }
            for job in manifest.jobs
        ],
        "epochs": [],
    }


def _journal_identity_mismatches(journal: Mapping[str, Any], manifest: Manifest) -> list[str]:
    mismatches: list[str] = []
    expected = {
        "manifest": str(manifest.path),
        "base_cache": str(manifest.base_cache),
        "work_dir": str(manifest.work_dir),
    }
    for field, expected_value in expected.items():
        recorded = journal.get(field)
        if recorded != expected_value:
            mismatches.append(f"{field}: journal={recorded!r} manifest={expected_value!r}")
    journal_ids = {
        str(item.get("id"))
        for item in journal.get("jobs", [])
        if isinstance(item, Mapping)
    }
    manifest_ids = {job.id for job in manifest.jobs}
    if journal_ids != manifest_ids:
        missing = sorted(manifest_ids - journal_ids)
        extra = sorted(journal_ids - manifest_ids)
        mismatches.append(
            f"job ids: missing_from_journal={missing} not_in_manifest={extra}"
        )
    return mismatches


def load_or_initialize_journal(path: Path, manifest: Manifest) -> dict[str, Any]:
    if path.exists():
        journal = json.loads(path.read_text(encoding="utf-8"))
        if journal.get("schema_version") != JOURNAL_SCHEMA_VERSION:
            raise ValueError(f"{path}: unsupported journal schema {journal.get('schema_version')!r}")
        mismatches = _journal_identity_mismatches(journal, manifest)
        if mismatches:
            raise ValueError(
                f"{path}: journal identity does not match the loaded manifest; "
                f"refusing to resume (a stale journal would silently skip jobs): "
                + "; ".join(mismatches)
            )
        return journal
    return initialize_journal(manifest)


def save_journal(path: Path, journal: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _journal_with_job_summary(journal)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def pending_jobs(manifest: Manifest, journal: Mapping[str, Any]) -> list[JobSpec]:
    done = {
        str(item.get("id"))
        for item in journal.get("jobs", [])
        if isinstance(item, Mapping) and item.get("status") == "done"
    }
    failed = {
        str(item.get("id"))
        for item in journal.get("jobs", [])
        if isinstance(item, Mapping) and item.get("status") == "failed"
    }
    if failed:
        raise RuntimeError(f"journal has failed jobs: {', '.join(sorted(failed))}")
    return [job for job in manifest.jobs if job.id not in done]


def _journal_with_job_summary(journal: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(journal)
    done: list[str] = []
    remaining: list[str] = []
    for item in payload.get("jobs", []):
        if not isinstance(item, Mapping):
            continue
        job_id = str(item.get("id"))
        if item.get("status") == "done":
            done.append(job_id)
        elif item.get("status") != "failed":
            remaining.append(job_id)
    payload["jobs_done"] = done
    payload["jobs_remaining"] = remaining
    return payload


def build_optimizer_command(
    job: JobSpec,
    *,
    profile: str,
    out_dir: Path,
    python: str,
    nice: int,
) -> list[str]:
    return [
        "nice",
        "-n",
        str(nice),
        python,
        "-m",
        "simulator.optimize",
        "--feedstock",
        job.feedstock,
        "--profile",
        profile,
        "--strategy",
        job.strategy,
        "--fidelity",
        job.fidelity,
        "--parallel",
        str(job.parallel),
        "--budget",
        str(job.budget),
        "--seed",
        str(job.seed),
        "--out",
        str(out_dir),
    ]


def dry_run_plan(manifest: Manifest, config: DriverConfig, journal: Mapping[str, Any]) -> dict[str, Any]:
    next_epoch = int(journal.get("epoch", 0)) + 1
    epoch_dir = manifest.work_dir / f"epoch-{next_epoch:04d}"
    jobs = []
    for job in pending_jobs(manifest, journal):
        shard_db = epoch_dir / "shards" / f"{job.id}.sqlite"
        out_dir = job.out / f"epoch-{next_epoch:04d}"
        profile = _planned_profile_arg(job, manifest.path.parent, shard_db)
        jobs.append(
            {
                "id": job.id,
                "shard_db": str(shard_db),
                "out": str(out_dir),
                "command": build_optimizer_command(
                    job,
                    profile=profile,
                    out_dir=out_dir,
                    python=config.python,
                    nice=config.nice,
                ),
            }
        )
    return {
        "manifest": str(manifest.path),
        "base_cache": str(manifest.base_cache),
        "journal_epoch": int(journal.get("epoch", 0)),
        "next_epoch": next_epoch,
        "time_box_seconds": config.time_box_seconds,
        "dup_threshold": config.dup_threshold,
        "low_dup_epochs": config.low_dup_epochs,
        "duplication_expected": config.duplication_expected,
        "jobs": jobs,
    }


def merge_epoch_shards(
    base_cache: Path,
    shard_paths: Iterable[Path],
    *,
    seed_fn: Callable[[Path, Iterable[Path]], Mapping[str, Any]] = seed_cache,
) -> dict[str, Any]:
    sources = [path for path in shard_paths if path.exists()]
    if not sources:
        return {
            "target": str(base_cache),
            "rows_before": 0,
            "rows_after": 0,
            "inserted_rows": 0,
            "sources": [],
        }
    return dict(seed_fn(base_cache, sources))


def seed_job_cache(
    shard_db: Path,
    base_cache: Path,
    *,
    seed_fn: Callable[[Path, Iterable[Path]], Mapping[str, Any]] = seed_cache,
) -> Mapping[str, Any]:
    if shard_db.exists():
        shard_db.unlink()
    shard_db.parent.mkdir(parents=True, exist_ok=True)
    if base_cache.exists():
        return seed_fn(shard_db, [base_cache])
    PT1PersistentEquilibriumStore(shard_db)
    return {
        "target": str(shard_db),
        "rows_before": 0,
        "rows_after": 0,
        "inserted_rows": 0,
        "sources": [],
    }


def run_driver(manifest: Manifest, config: DriverConfig, *, journal_path: Path, dry_run: bool = False) -> int:
    journal = load_or_initialize_journal(journal_path, manifest)
    if dry_run:
        print(json.dumps(dry_run_plan(manifest, config, journal), indent=2, sort_keys=True))
        return 0

    while True:
        remaining = pending_jobs(manifest, journal)
        if not remaining:
            journal["decision"] = DECISION_BATCH_COMPLETE
            save_journal(journal_path, journal)
            print("decision=batch_complete")
            return 0

        final_long = journal.get("decision") == DECISION_FINAL_LONG
        epoch_result = run_epoch(
            manifest,
            remaining,
            config,
            epoch_index=int(journal.get("epoch", 0)) + 1,
            final_long=final_long,
        )
        _apply_epoch_result(journal, epoch_result)

        merge_summary = merge_epoch_shards(manifest.base_cache, [Path(p) for p in epoch_result["shard_dbs"]])
        rate = duplication_rate_from_merge(merge_summary)
        epoch_result["merge"] = merge_summary
        epoch_result["dup_rate"] = rate
        journal["dup_rates"] = [*journal.get("dup_rates", []), rate]
        journal.setdefault("epochs", []).append(epoch_result)
        if epoch_result.get("failed_jobs"):
            journal["decision"] = DECISION_FAILED
            save_journal(journal_path, journal)
            print(
                "epoch={epoch} failed_jobs={failed} dup_rate={dup_rate:.6f} decision=failed".format(
                    epoch=epoch_result["epoch"],
                    failed=len(epoch_result["failed_jobs"]),
                    dup_rate=rate,
                )
            )
            return 2
        remaining_count = len(pending_jobs(manifest, journal))
        journal["decision"] = adaptive_decision(
            [float(value) for value in journal.get("dup_rates", [])],
            remaining_jobs=remaining_count,
            threshold=config.dup_threshold,
            consecutive=config.low_dup_epochs,
            duplication_expected=config.duplication_expected,
        )
        save_journal(journal_path, journal)
        print(
            "epoch={epoch} completed={completed} remaining={remaining} "
            "dup_rate={dup_rate:.6f} decision={decision}".format(
                epoch=epoch_result["epoch"],
                completed=len(epoch_result["completed_jobs"]),
                remaining=remaining_count,
                dup_rate=rate,
                decision=journal["decision"],
            )
        )

        if journal["decision"] == DECISION_BATCH_COMPLETE:
            return 0
        if final_long:
            return 0 if remaining_count == 0 else 2


def run_epoch(
    manifest: Manifest,
    jobs: Sequence[JobSpec],
    config: DriverConfig,
    *,
    epoch_index: int,
    final_long: bool = False,
) -> dict[str, Any]:
    epoch_dir = manifest.work_dir / f"epoch-{epoch_index:04d}"
    log_dir = epoch_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    deadline = None if final_long or config.time_box_seconds is None else time.monotonic() + config.time_box_seconds
    result: dict[str, Any] = {
        "epoch": epoch_index,
        "mode": "final_long" if final_long else "time_boxed",
        "time_box_seconds": None if final_long else config.time_box_seconds,
        "completed_jobs": [],
        "failed_jobs": [],
        "timed_out_jobs": [],
        "attempted_jobs": [],
        "shard_dbs": [],
    }

    for job in jobs:
        if deadline is not None and time.monotonic() >= deadline:
            break
        shard_db = epoch_dir / "shards" / f"{job.id}.sqlite"
        seed_job_cache(shard_db, manifest.base_cache)
        profile_arg = write_epoch_profile(job, manifest.path.parent, shard_db, epoch_dir)
        out_dir = job.out / f"epoch-{epoch_index:04d}"
        command = build_optimizer_command(
            job,
            profile=profile_arg,
            out_dir=out_dir,
            python=config.python,
            nice=config.nice,
        )
        stdout_path = log_dir / f"{job.id}.stdout.log"
        stderr_path = log_dir / f"{job.id}.stderr.log"
        job_record = {
            "id": job.id,
            "command": command,
            "shard_db": str(shard_db),
            "out": str(out_dir),
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
        }
        result["attempted_jobs"].append(job_record)
        result["shard_dbs"].append(str(shard_db))
        timeout = None if deadline is None else max(0.0, deadline - time.monotonic())
        code = _run_child(command, stdout_path=stdout_path, stderr_path=stderr_path, timeout=timeout)
        if code == 0:
            result["completed_jobs"].append(job.id)
        elif code == 124:
            result["timed_out_jobs"].append(job.id)
            break
        else:
            job_record["returncode"] = code
            result["failed_jobs"].append(job_record)
            break
    return result


def write_epoch_profile(job: JobSpec, manifest_dir: Path, shard_db: Path, epoch_dir: Path) -> str:
    profile_path = _resolve_path(job.profile, manifest_dir)
    if not profile_path.exists():
        return job.profile

    profile = _load_mapping(profile_path)
    changed = _apply_cache_db(profile, shard_db, job.reduced_real_cache)
    if not changed:
        return str(profile_path)

    profile_dir = epoch_dir / "profiles"
    profile_dir.mkdir(parents=True, exist_ok=True)
    out = profile_dir / f"{job.id}.profile.json"
    out.write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(out)


def _apply_epoch_result(journal: dict[str, Any], epoch_result: Mapping[str, Any]) -> None:
    completed = {str(job_id) for job_id in epoch_result.get("completed_jobs", [])}
    failed = {str(job.get("id")) for job in epoch_result.get("failed_jobs", []) if isinstance(job, Mapping)}
    for job in journal.get("jobs", []):
        if not isinstance(job, dict):
            continue
        if job.get("id") in completed:
            job["status"] = "done"
        elif job.get("id") in failed:
            job["status"] = "failed"
    journal["epoch"] = int(epoch_result.get("epoch", journal.get("epoch", 0)))


def _run_child(command: Sequence[str], *, stdout_path: Path, stderr_path: Path, timeout: float | None) -> int:
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(
            list(command),
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            return int(process.wait(timeout=timeout))
        except subprocess.TimeoutExpired:
            _terminate_process_group(process)
            return 124


def _terminate_process_group(process: subprocess.Popen[Any]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def _apply_cache_db(
    profile: dict[str, Any],
    shard_db: Path,
    job_cache_config: Mapping[str, Any] | None,
) -> bool:
    changed = False
    run = profile.get("run")
    if isinstance(run, dict):
        changed = _apply_cache_to_run(run, shard_db, job_cache_config) or changed
    fidelities = profile.get("fidelities")
    if isinstance(fidelities, Mapping):
        for options in fidelities.values():
            if isinstance(options, dict) and (
                options.get("backend_name") == "cached-real" or "reduced_real_cache" in options
            ):
                changed = _apply_cache_to_run(options, shard_db, job_cache_config) or changed
    return changed


def _apply_cache_to_run(
    run_options: dict[str, Any],
    shard_db: Path,
    job_cache_config: Mapping[str, Any] | None,
) -> bool:
    cache_config = run_options.get("reduced_real_cache")
    if not isinstance(cache_config, Mapping):
        cache_config = job_cache_config
    if not isinstance(cache_config, Mapping):
        return False
    updated = dict(cache_config)
    updated["db_path"] = str(shard_db)
    run_options["reduced_real_cache"] = updated
    return True


def _planned_profile_arg(job: JobSpec, manifest_dir: Path, shard_db: Path) -> str:
    profile_path = _resolve_path(job.profile, manifest_dir)
    if profile_path.exists():
        return str(profile_path)
    return job.profile


def _load_mapping(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        if yaml is None:
            data = json.loads(text)
        else:
            data = yaml.safe_load(text)
    if not isinstance(data, Mapping):
        raise ValueError(f"{path}: expected mapping")
    return dict(data)


def _resolve_path(value: str | os.PathLike[str], base: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path


def _required(raw: Mapping[str, Any], key: str) -> Any:
    if key not in raw:
        raise ValueError(f"missing required key {key!r}")
    return raw[key]


def _positive_int(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return parsed


def _non_negative_int(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a non-negative integer") from exc
    if parsed < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return parsed


def _default_job_id(item: Mapping[str, Any], index: int) -> str:
    feedstock = str(item.get("feedstock", "job")).replace("/", "-")
    strategy = str(item.get("strategy", "strategy")).replace("/", "-")
    seed = str(item.get("seed", index))
    return f"{index:03d}-{feedstock}-{strategy}-{seed}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run epoch-merge reduced-real cache grinding.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--base-cache", type=Path, default=None)
    parser.add_argument("--work-dir", type=Path, default=None)
    parser.add_argument("--journal", type=Path, default=None)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--time-box-seconds", type=int, default=DEFAULT_TIME_BOX_SECONDS)
    parser.add_argument("--dup-threshold", type=float, default=DEFAULT_DUP_THRESHOLD)
    parser.add_argument("--low-dup-epochs", type=int, default=DEFAULT_LOW_DUP_EPOCHS)
    parser.add_argument("--nice", type=int, default=15)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--no-duplication-expected",
        action="store_true",
        help="disable adaptive final-long switch based on low duplication rates",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        manifest = load_manifest(args.manifest, base_cache=args.base_cache, work_dir=args.work_dir)
        journal_path = args.journal or (manifest.work_dir / "epoch_grind_journal.json")
        config = DriverConfig(
            python=args.python,
            time_box_seconds=None if args.time_box_seconds <= 0 else args.time_box_seconds,
            dup_threshold=args.dup_threshold,
            low_dup_epochs=args.low_dup_epochs,
            duplication_expected=not args.no_duplication_expected,
            nice=args.nice,
        )
        if not shutil.which("nice"):
            raise RuntimeError("nice command not found")
        return run_driver(manifest, config, journal_path=journal_path, dry_run=args.dry_run)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
