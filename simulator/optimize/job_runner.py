"""Disk-backed optimizer CLI job runner for the web launch plane."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from typing import Any, Callable
from uuid import uuid4

from simulator.optimize.evalspec import current_code_version


STATUS_QUEUED = "QUEUED"
STATUS_RUNNING = "RUNNING"
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_FAILED = "FAILED"
TERMINAL_STATUSES = {STATUS_SUCCEEDED, STATUS_FAILED}
META_NAME = ".job_meta.json"
LOG_NAME = "job.log"
CACHE_NAME = "cache.sqlite"
STATUS_MARKER_NAME = "job_status.json"


PopenFactory = Callable[..., subprocess.Popen]
NowFactory = Callable[[], datetime]


@dataclass(frozen=True)
class OptimizerJobRequest:
    feedstock_id: str
    profile_id: str
    strategy: str
    fidelity: str
    budget: int
    parallel: int
    seed: int
    profile_arg: str | None = None


class OptimizerJobRunner:
    """Serial W=1 launcher that persists job state under ``runs/jobs``."""

    def __init__(
        self,
        runs_root: Path | str,
        *,
        popen_factory: PopenFactory = subprocess.Popen,
        now_factory: NowFactory | None = None,
        python_executable: str | None = None,
    ) -> None:
        self.runs_root = Path(runs_root).expanduser()
        self.jobs_root = self.runs_root / "jobs"
        self._popen_factory = popen_factory
        self._now_factory = now_factory or (lambda: datetime.now(UTC))
        self._python_executable = python_executable or sys.executable
        self._jobs: dict[str, dict[str, Any]] = {}
        self._processes: dict[str, subprocess.Popen] = {}
        self._lock = threading.RLock()
        self.reload()

    def reload(self) -> None:
        with self._lock:
            self.jobs_root.mkdir(parents=True, exist_ok=True)
            self._jobs = {}
            for meta_path in sorted(self.jobs_root.glob(f"*/{META_NAME}")):
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                job_id = str(meta.get("job_id") or meta_path.parent.name)
                meta["job_id"] = job_id
                self._jobs[job_id] = self._normalize_meta(meta)
            self._refresh_locked()
            self._pump_queue_locked()

    def submit(self, request: OptimizerJobRequest) -> dict[str, Any]:
        with self._lock:
            self.jobs_root.mkdir(parents=True, exist_ok=True)
            job_id = self._new_job_id()
            job_dir = self.jobs_root / job_id
            job_dir.mkdir(parents=True, exist_ok=False)
            now = self._now()
            meta = {
                "job_id": job_id,
                "feedstock": request.feedstock_id,
                "profile": request.profile_id,
                "feedstock_id": request.feedstock_id,
                "profile_id": request.profile_id,
                "strategy": request.strategy,
                "fidelity": request.fidelity,
                "budget": request.budget,
                "parallel": request.parallel,
                "seed": request.seed,
                "profile_arg": request.profile_arg or request.profile_id,
                "pid": None,
                "status": STATUS_QUEUED,
                "created_at": now,
                "started_at": None,
                "completed_at": None,
                "eta": self._estimate_eta(request.budget),
                "stderr_tail": "",
                "code_version": current_code_version(),
                "out_dir": str(job_dir),
                "log_path": str(job_dir / LOG_NAME),
            }
            self._jobs[job_id] = meta
            self._write_meta(meta)
            self._pump_queue_locked()
            return self._public_meta(self._jobs[job_id])

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            self._refresh_locked()
            self._pump_queue_locked()
            return [
                self._public_meta(meta)
                for meta in sorted(
                    self._jobs.values(),
                    key=lambda row: (row.get("created_at") or "", row.get("job_id") or ""),
                    reverse=True,
                )
            ]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._refresh_locked()
            self._pump_queue_locked()
            meta = self._jobs.get(job_id)
            return self._public_meta(meta) if meta is not None else None

    def _refresh_locked(self) -> None:
        changed = False
        for job_id, meta in list(self._jobs.items()):
            if meta.get("status") != STATUS_RUNNING:
                continue
            process = self._processes.get(job_id)
            if process is not None:
                return_code = process.poll()
                if return_code is None:
                    continue
                self._complete_job_locked(meta, return_code)
                self._processes.pop(job_id, None)
                changed = True
                continue
            if self._pid_is_alive(meta.get("pid")):
                continue
            meta["completed_at"] = meta.get("completed_at") or self._now()
            tail = self._log_tail(job_id)
            meta["stderr_tail"] = tail
            terminal_status = self._terminal_status_from_marker(job_id)
            if terminal_status is None:
                meta["status"] = STATUS_FAILED
                meta["stderr_tail"] = (
                    tail
                    or "optimizer process exited without a terminal status marker"
                )
            elif terminal_status[0] == STATUS_SUCCEEDED and not (
                self._has_stored_results(job_id)
            ):
                # Symmetric with the live-completion path: a success marker is
                # necessary but not sufficient — refuse to advertise SUCCEEDED
                # when the persisted result store is missing/unreadable.
                meta["status"] = STATUS_FAILED
                meta["stderr_tail"] = (
                    tail
                    or "optimizer success marker present but no stored results"
                )
            else:
                status, marker_detail = terminal_status
                meta["status"] = status
                if status == STATUS_FAILED:
                    meta["stderr_tail"] = (
                        tail
                        or marker_detail
                        or "optimizer terminal status marker recorded failure"
                    )
            self._write_meta(meta)
            changed = True
        if changed:
            self._reestimate_open_jobs_locked()

    def _pump_queue_locked(self) -> None:
        if any(meta.get("status") == STATUS_RUNNING for meta in self._jobs.values()):
            return
        queued = sorted(
            (
                meta for meta in self._jobs.values()
                if meta.get("status") == STATUS_QUEUED
            ),
            key=lambda row: (row.get("created_at") or "", row.get("job_id") or ""),
        )
        if queued:
            self._start_job_locked(queued[0])

    def _start_job_locked(self, meta: dict[str, Any]) -> None:
        job_dir = self.jobs_root / str(meta["job_id"])
        job_dir.mkdir(parents=True, exist_ok=True)
        log_path = job_dir / LOG_NAME
        cmd = [
            self._python_executable,
            "-m",
            "simulator.optimize",
            "--feedstock",
            str(meta["feedstock_id"]),
            "--profile",
            str(meta.get("profile_arg") or meta["profile_id"]),
            "--strategy",
            str(meta["strategy"]),
            "--fidelity",
            str(meta["fidelity"]),
            "--parallel",
            str(meta["parallel"]),
            "--budget",
            str(meta["budget"]),
            "--out",
            str(job_dir),
            "--seed",
            str(meta["seed"]),
        ]
        env = os.environ.copy()
        env["OPTIMIZER_RUNS_DIR"] = str(self.runs_root)
        repo_root = Path(__file__).resolve().parents[2]
        with log_path.open("ab") as log:
            try:
                process = self._popen_factory(
                    cmd,
                    cwd=repo_root,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=env,
                )
            except OSError as exc:
                meta["status"] = STATUS_FAILED
                meta["completed_at"] = self._now()
                meta["stderr_tail"] = str(exc)
                self._write_meta(meta)
                return
        meta["pid"] = process.pid
        meta["status"] = STATUS_RUNNING
        meta["started_at"] = self._now()
        meta["stderr_tail"] = ""
        self._processes[str(meta["job_id"])] = process
        self._write_meta(meta)

    def _complete_job_locked(self, meta: dict[str, Any], return_code: int) -> None:
        meta["completed_at"] = self._now()
        job_id = str(meta["job_id"])
        tail = self._log_tail(job_id)
        meta["stderr_tail"] = tail
        terminal_status = self._terminal_status_from_marker(job_id)
        if terminal_status and terminal_status[0] == STATUS_FAILED:
            _, marker_detail = terminal_status
            meta["status"] = STATUS_FAILED
            meta["stderr_tail"] = (
                tail
                or marker_detail
                or "optimizer terminal status marker recorded failure"
            )
        elif return_code == 0 and self._has_stored_results(job_id):
            meta["status"] = STATUS_SUCCEEDED
        elif return_code == 0:
            meta["status"] = STATUS_FAILED
            meta["stderr_tail"] = (
                (tail + "\n") if tail else ""
            ) + "optimizer exited 0 without stored results"
        else:
            meta["status"] = STATUS_FAILED
        self._write_meta(meta)

    def _normalize_meta(self, meta: dict[str, Any]) -> dict[str, Any]:
        status = str(meta.get("status") or STATUS_FAILED).upper()
        if status not in {
            STATUS_QUEUED,
            STATUS_RUNNING,
            STATUS_SUCCEEDED,
            STATUS_FAILED,
        }:
            status = STATUS_FAILED
        normalized = dict(meta)
        normalized["status"] = status
        normalized.setdefault("feedstock_id", normalized.get("feedstock"))
        normalized.setdefault("profile_id", normalized.get("profile"))
        normalized.setdefault("feedstock", normalized.get("feedstock_id"))
        normalized.setdefault("profile", normalized.get("profile_id"))
        normalized.setdefault("fidelity", "")
        normalized.setdefault("profile_arg", normalized.get("profile_id"))
        normalized.setdefault("pid", None)
        normalized.setdefault("started_at", None)
        normalized.setdefault("completed_at", None)
        normalized.setdefault("eta", None)
        normalized.setdefault("stderr_tail", "")
        normalized.setdefault("code_version", None)
        normalized.setdefault("out_dir", str(self.jobs_root / str(normalized["job_id"])))
        normalized.setdefault(
            "log_path",
            str(self.jobs_root / str(normalized["job_id"]) / LOG_NAME),
        )
        return normalized

    def _public_meta(self, meta: dict[str, Any]) -> dict[str, Any]:
        row = dict(meta)
        row["queue_depth"] = self._jobs_ahead(str(row["job_id"]))
        row["version_badge"] = self._version_badge(row.get("code_version"))
        return row

    def _jobs_ahead(self, job_id: str) -> int:
        meta = self._jobs.get(job_id)
        if meta is None:
            return 0
        status = meta.get("status")
        if status == STATUS_RUNNING:
            return 0
        if status != STATUS_QUEUED:
            return 0
        created = meta.get("created_at") or ""
        ahead = sum(1 for row in self._jobs.values() if row.get("status") == STATUS_RUNNING)
        ahead += sum(
            1
            for row in self._jobs.values()
            if row.get("status") == STATUS_QUEUED
            and (
                (row.get("created_at") or "", row.get("job_id") or "")
                < (created, job_id)
            )
        )
        return ahead

    def _estimate_eta(self, budget: int) -> dict[str, Any] | None:
        samples: list[float] = []
        for meta in self._jobs.values():
            if meta.get("status") not in TERMINAL_STATUSES:
                continue
            try:
                sample_budget = int(meta.get("budget"))
            except (TypeError, ValueError):
                continue
            if sample_budget <= 0:
                continue
            duration = self._duration_seconds(
                meta.get("started_at") or meta.get("created_at"),
                meta.get("completed_at"),
            )
            if duration is not None and duration > 0.0:
                samples.append(duration / sample_budget)
        if not samples:
            return None
        seconds = sum(samples) / len(samples) * budget
        return {
            "seconds": seconds,
            "basis": "historical_avg_seconds_per_eval",
            "sample_count": len(samples),
        }

    def _reestimate_open_jobs_locked(self) -> None:
        for meta in self._jobs.values():
            if meta.get("status") in {STATUS_QUEUED, STATUS_RUNNING}:
                try:
                    budget = int(meta.get("budget"))
                except (TypeError, ValueError):
                    budget = 0
                meta["eta"] = self._estimate_eta(budget) if budget > 0 else None
                self._write_meta(meta)

    def _write_meta(self, meta: dict[str, Any]) -> None:
        job_dir = self.jobs_root / str(meta["job_id"])
        job_dir.mkdir(parents=True, exist_ok=True)
        tmp = job_dir / f"{META_NAME}.tmp"
        tmp.write_text(
            json.dumps(meta, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp.replace(job_dir / META_NAME)

    def _new_job_id(self) -> str:
        while True:
            value = f"{self._now_dt().strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
            if value not in self._jobs and not (self.jobs_root / value).exists():
                return value

    def _now(self) -> str:
        return self._now_dt().isoformat()

    def _now_dt(self) -> datetime:
        return self._now_factory().astimezone(UTC)

    def _has_stored_results(self, job_id: str) -> bool:
        cache_path = self.jobs_root / job_id / CACHE_NAME
        if not cache_path.is_file():
            return False
        try:
            with sqlite3.connect(cache_path) as conn:
                row = conn.execute("SELECT COUNT(*) FROM results").fetchone()
        except sqlite3.Error:
            return False
        return bool(row and row[0] > 0)

    def _terminal_status_from_marker(self, job_id: str) -> tuple[str, str] | None:
        marker_path = self.jobs_root / job_id / STATUS_MARKER_NAME
        if not marker_path.is_file():
            return None
        try:
            payload = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return (
                STATUS_FAILED,
                f"optimizer terminal status marker unreadable: {exc}",
            )
        if not isinstance(payload, dict):
            return (
                STATUS_FAILED,
                "optimizer terminal status marker is not a JSON object",
            )
        marker_status = str(payload.get("status") or "").upper()
        success = payload.get("success")
        detail = self._terminal_marker_detail(payload)
        if marker_status in {STATUS_SUCCEEDED, "SUCCESS"} or success is True:
            return STATUS_SUCCEEDED, detail
        if marker_status in {STATUS_FAILED, "FAILURE", "ERROR"} or success is False:
            return STATUS_FAILED, detail
        return (
            STATUS_FAILED,
            f"optimizer terminal status marker has invalid status: {marker_status or '<missing>'}",
        )

    def _terminal_marker_detail(self, payload: dict[str, Any]) -> str:
        reason = str(payload.get("reason") or "").strip()
        message = str(payload.get("message") or "").strip()
        if reason and message:
            return f"{reason}: {message}"
        return reason or message

    def _log_tail(self, job_id: str, limit: int = 4096) -> str:
        log_path = self.jobs_root / job_id / LOG_NAME
        if not log_path.is_file():
            return ""
        try:
            with log_path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - limit), os.SEEK_SET)
                return handle.read().decode("utf-8", errors="replace").strip()
        except OSError:
            return ""

    def _pid_is_alive(self, pid: Any) -> bool:
        try:
            parsed = int(pid)
        except (TypeError, ValueError):
            return False
        if parsed <= 0:
            return False
        try:
            os.kill(parsed, 0)
        except OSError:
            return False
        return True

    def _duration_seconds(self, start: Any, end: Any) -> float | None:
        if not start or not end:
            return None
        try:
            start_dt = datetime.fromisoformat(str(start))
            end_dt = datetime.fromisoformat(str(end))
        except ValueError:
            return None
        return max(0.0, (end_dt - start_dt).total_seconds())

    def _version_badge(self, stored_version: Any) -> dict[str, Any]:
        current = current_code_version()
        if not stored_version:
            return {
                "status": "unknown",
                "label": "version unknown",
                "stored_version": None,
                "current_version": current,
            }
        stored = str(stored_version)
        if stored == current:
            return {
                "status": "current",
                "label": "current",
                "stored_version": stored,
                "current_version": current,
            }
        return {
            "status": "stale",
            "label": "stale version",
            "stored_version": stored,
            "current_version": current,
        }


_RUNNERS: dict[tuple[Path, int], OptimizerJobRunner] = {}
_RUNNERS_LOCK = threading.Lock()


def get_runner(
    runs_root: Path | str,
    *,
    popen_factory: PopenFactory = subprocess.Popen,
) -> OptimizerJobRunner:
    root = Path(runs_root).expanduser()
    key = (root.resolve() if root.exists() else root.absolute(), id(popen_factory))
    with _RUNNERS_LOCK:
        runner = _RUNNERS.get(key)
        if runner is None:
            runner = OptimizerJobRunner(root, popen_factory=popen_factory)
            _RUNNERS[key] = runner
        else:
            runner.reload()
        return runner


def reset_runner_cache() -> None:
    with _RUNNERS_LOCK:
        _RUNNERS.clear()
