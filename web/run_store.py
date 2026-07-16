"""Durable JSON store for backend-free run artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
import fcntl
import json
import logging
import math
import os
import re
import secrets
import stat
import tempfile
import threading
from pathlib import Path
from typing import Any

from flask import current_app

from simulator.accounting.run_artifact import build_run_artifact


DEFAULT_RETENTION = 100
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_SAVE_LOCK = threading.Lock()
_LOG = logging.getLogger(__name__)
# SYMLINK-CONFINEMENT SCOPE (honest boundary): artifact/meta READS and meta
# writes refuse symlinks (O_NOFOLLOW / dir-fd below). The store ROOT, the
# .write-lock files, and tempfile creation still follow pathnames — an actor
# who can replace runs_dir itself with a symlink can redirect them. That actor
# already has filesystem write access to the store's parent (outside the
# local-workbench threat model); full root confinement is queued backlog.
_DIRECTORY_OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_FILE_OPEN_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW


@contextmanager
def _open_directory(path: Path):
    descriptor = os.open(path, _DIRECTORY_OPEN_FLAGS)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def _load_json_regular(path: Path, *, directory_fd: int | None = None) -> Any:
    if directory_fd is None:
        with _open_directory(path.parent) as parent_fd:
            return _load_json_regular(path, directory_fd=parent_fd)
    descriptor = os.open(path.name, _FILE_OPEN_FLAGS, dir_fd=directory_fd)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError(f"refusing non-regular file: {path}")
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            descriptor = -1
            return json.load(handle)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


class RunStoreCorruptionError(RuntimeError):
    """Raised when a stored run exists but is not a decodable JSON object."""

    def __init__(self, run_id: str, path: Path, detail: str) -> None:
        self.run_id = run_id
        self.path = path
        super().__init__(f"corrupt run artifact {run_id!r}: {detail}")


class RunMetaCorruptionError(RunStoreCorruptionError):
    """Raised when a run metadata sidecar is not a decodable JSON object."""

    def __init__(self, run_id: str, path: Path, detail: str) -> None:
        self.run_id = run_id
        self.path = path
        RuntimeError.__init__(self, f"corrupt run metadata {run_id!r}: {detail}")


class DuplicateRunArtifactError(RuntimeError):
    """Raised when persistence cannot prove this payload won the first write."""


class InvalidRunIdError(ValueError):
    """Raised before filesystem access when a run ID is not store-safe."""


class RunArtifactStore:
    def __init__(self, runs_dir: str | Path, *, keep: int = DEFAULT_RETENTION) -> None:
        self.runs_dir = Path(runs_dir)
        self.keep = max(0, int(keep))

    def save(
        self,
        run_id: str,
        artifact: dict[str, Any],
        *,
        parent_run_id: str | None = None,
    ) -> bool:
        """Persist one artifact, with lineage reserved for the W-B command plane.

        ``parent_run_id`` is the landing surface for the HTTP rerun/draft flow;
        the W-B run-library lineage display is its intended consumer.
        """
        destination = self._path(run_id)
        if parent_run_id is not None:
            self._path(parent_run_id)
        claim_path = destination.with_suffix(".write-lock")
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        with self._store_lock():
            with claim_path.open("a", encoding="utf-8") as claim_handle:
                fcntl.flock(claim_handle.fileno(), fcntl.LOCK_EX)
                if destination.exists():
                    _LOG.warning(
                        "run artifact %s already exists; duplicate save ignored",
                        run_id,
                    )
                    return False
                temp_path: Path | None = None
                try:
                    fd, raw_temp_path = tempfile.mkstemp(
                        prefix=f".{run_id}.", suffix=".tmp", dir=self.runs_dir
                    )
                    temp_path = Path(raw_temp_path)
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        json.dump(
                            artifact,
                            handle,
                            indent=2,
                            sort_keys=True,
                            allow_nan=False,
                        )
                        handle.write("\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    if parent_run_id is not None:
                        # The artifact replace is the commit point. Lineage must
                        # be durable first so a committed artifact can never be
                        # left permanently unlineaged after a failed save.
                        self._save_parent_run_id(run_id, parent_run_id)
                    os.replace(temp_path, destination)
                finally:
                    if temp_path is not None:
                        temp_path.unlink(missing_ok=True)
            self._apply_retention_locked()
        return True

    def load(self, run_id: str) -> dict[str, Any] | None:
        path = self._path(run_id)
        try:
            payload = _load_json_regular(path)
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            raise RunStoreCorruptionError(run_id, path, str(exc)) from exc
        if not isinstance(payload, dict):
            raise RunStoreCorruptionError(
                run_id,
                path,
                f"expected a JSON object, got {type(payload).__name__}",
            )
        self._validate_nested_shape(run_id, path, payload)
        return payload

    @staticmethod
    def _validate_nested_shape(
        run_id: str, path: Path, artifact: dict[str, Any]
    ) -> None:
        # Structural keys are REQUIRED, not merely well-typed-when-present:
        # a stored artifact missing header/terminal/timesteps would pass a
        # presence-optional check and crash readers downstream instead of
        # being quarantined here.
        for key in ("header", "terminal"):
            if not isinstance(artifact.get(key), dict):
                raise RunStoreCorruptionError(
                    run_id,
                    path,
                    f"expected {key} to be an object, got "
                    f"{type(artifact.get(key)).__name__}",
                )
        if "timesteps" not in artifact:
            raise RunStoreCorruptionError(run_id, path, "missing timesteps array")
        timesteps = artifact["timesteps"]
        if not isinstance(timesteps, list):
            raise RunStoreCorruptionError(
                run_id,
                path,
                f"expected timesteps to be an array, got {type(timesteps).__name__}",
            )
        for index, timestep in enumerate(timesteps):
            if not isinstance(timestep, dict):
                raise RunStoreCorruptionError(
                    run_id,
                    path,
                    f"expected timesteps[{index}] to be an object, got {type(timestep).__name__}",
                )
            summary = timestep.get("summary")
            if not isinstance(summary, dict):
                raise RunStoreCorruptionError(
                    run_id,
                    path,
                    f"expected timesteps[{index}].summary to be an object, got {type(summary).__name__}",
                )
            metal_yields = summary.get("metal_yields_kg", {})
            if not isinstance(metal_yields, dict):
                raise RunStoreCorruptionError(
                    run_id,
                    path,
                    "expected "
                    f"timesteps[{index}].summary.metal_yields_kg to be an object, "
                    f"got {type(metal_yields).__name__}",
                )

    def list_runs(self) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        if not self.runs_dir.exists():
            return summaries
        with self._store_lock():
            for path in self._artifact_paths():
                try:
                    artifact = self.load(path.stem)
                except RunStoreCorruptionError as exc:
                    self._quarantine_or_log(path, exc)
                    continue
                if artifact is None:
                    continue
                try:
                    metadata = self._load_meta(path.stem)
                except RunMetaCorruptionError as exc:
                    if self._quarantine_or_log(exc.path, exc) is None:
                        continue
                    metadata = {}
                summaries.append(self._summary(artifact, path.stem, metadata))
        return sorted(
            summaries,
            key=lambda row: str(row.get("created_at") or ""),
            reverse=True,
        )

    def _path(self, run_id: str) -> Path:
        value = str(run_id)
        if not _RUN_ID_RE.fullmatch(value):
            raise InvalidRunIdError(
                "run_id must use only letters, digits, underscore, or hyphen"
            )
        return self.runs_dir / f"{value}.json"

    def _meta_path(self, run_id: str) -> Path:
        value = self._path(run_id).stem
        return self.runs_dir / "meta" / f"{value}.json"

    def _artifact_paths(self):
        # Only stems this store could have written (see _RUN_ID_RE) are
        # artifacts. Anything else — legacy `<id>.meta.json` sidecars from the
        # pre-`meta/` layout, stray dotted files — is skipped with a warning
        # instead of raising InvalidRunIdError out of list_runs(), where one
        # alien file would take down the whole run index. Pre-release store:
        # no deployed legacy data exists, so skip-and-warn, not a migration.
        for path in sorted(self.runs_dir.glob("*.json")):
            if _RUN_ID_RE.fullmatch(path.stem):
                yield path
            else:
                _LOG.warning("ignoring non-store file in runs dir: %s", path.name)

    @contextmanager
    def _store_lock(self):
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.runs_dir / ".store.write-lock"
        with _SAVE_LOCK:
            with lock_path.open("a", encoding="utf-8") as lock_handle:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                yield

    def _load_meta(self, run_id: str) -> dict[str, Any]:
        path = self._meta_path(run_id)
        try:
            payload = _load_json_regular(path)
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            raise RunMetaCorruptionError(run_id, path, str(exc)) from exc
        if not isinstance(payload, dict):
            raise RunMetaCorruptionError(
                run_id,
                path,
                f"expected metadata to be an object, got {type(payload).__name__}",
            )
        return payload

    def _has_quarantined_meta(self, run_id: str) -> bool:
        path = self._meta_path(run_id)
        return not path.exists() and any(
            path.parent.glob(f"{path.name}.corrupt*")
        )

    def update_meta(
        self, run_id: str, updates: Mapping[str, Any]
    ) -> dict[str, Any]:
        artifact_path = self._path(run_id)
        unknown = set(updates) - {"starred", "folder"}
        if unknown:
            raise ValueError(f"unknown run metadata keys: {', '.join(sorted(unknown))}")
        if "starred" in updates and not isinstance(updates["starred"], bool):
            raise ValueError("starred must be a boolean")
        if "folder" in updates and updates["folder"] is not None and not isinstance(
            updates["folder"], str
        ):
            raise ValueError("folder must be a string or null")
        with self._store_lock():
            try:
                _load_json_regular(artifact_path)
            except FileNotFoundError:
                raise FileNotFoundError(run_id)
            except (OSError, json.JSONDecodeError) as exc:
                raise RunStoreCorruptionError(run_id, artifact_path, str(exc)) from exc
            return self._write_meta_updates(run_id, updates)

    def _save_parent_run_id(self, run_id: str, parent_run_id: str) -> None:
        self._path(parent_run_id)
        self._write_meta_updates(run_id, {"parent_run_id": parent_run_id})

    def _write_meta_updates(
        self, run_id: str, updates: Mapping[str, Any]
    ) -> dict[str, Any]:
        destination = self._meta_path(run_id)
        with _open_directory(self.runs_dir) as runs_fd:
            try:
                os.mkdir("meta", dir_fd=runs_fd)
            except FileExistsError:
                pass
            meta_fd = os.open("meta", _DIRECTORY_OPEN_FLAGS, dir_fd=runs_fd)
        try:
            claim_name = f"{run_id}.write-lock"
            claim_fd = os.open(
                claim_name,
                os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o600,
                dir_fd=meta_fd,
            )
            with os.fdopen(claim_fd, "a", encoding="utf-8") as claim_handle:
                if not stat.S_ISREG(os.fstat(claim_handle.fileno()).st_mode):
                    raise OSError(f"refusing non-regular file: {destination.parent / claim_name}")
                fcntl.flock(claim_handle.fileno(), fcntl.LOCK_EX)
                try:
                    metadata = _load_json_regular(destination, directory_fd=meta_fd)
                except FileNotFoundError:
                    metadata = {}
                if not isinstance(metadata, dict):
                    raise RunMetaCorruptionError(
                        run_id,
                        destination,
                        f"expected metadata to be an object, got {type(metadata).__name__}",
                    )
                for key, value in updates.items():
                    if key == "folder" and value is None:
                        metadata.pop(key, None)
                    else:
                        metadata[key] = value
                temp_name = f".{run_id}.{secrets.token_hex(8)}.tmp"
                try:
                    temp_fd = os.open(
                        temp_name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                        0o600,
                        dir_fd=meta_fd,
                    )
                    with os.fdopen(temp_fd, "w", encoding="utf-8") as handle:
                        json.dump(
                            metadata,
                            handle,
                            indent=2,
                            sort_keys=True,
                            allow_nan=False,
                        )
                        handle.write("\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    # Keep the destination anchored to the verified meta directory
                    # even if its pathname is swapped while this write is active.
                    os.replace(
                        temp_name,
                        destination.name,
                        src_dir_fd=meta_fd,
                        dst_dir_fd=meta_fd,
                    )
                finally:
                    try:
                        os.unlink(temp_name, dir_fd=meta_fd)
                    except FileNotFoundError:
                        pass
        finally:
            os.close(meta_fd)
        return metadata

    @staticmethod
    def _quarantine(path: Path) -> Path:
        with _open_directory(path.parent) as parent_fd:
            candidate_name = f"{path.name}.corrupt"
            index = 1
            while True:
                try:
                    os.stat(candidate_name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    break
                candidate_name = f"{path.name}.corrupt.{index}"
                index += 1
            os.replace(
                path.name,
                candidate_name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
        return path.parent / candidate_name

    def _quarantine_or_log(
        self, path: Path, corruption_error: RunStoreCorruptionError
    ) -> Path | None:
        try:
            quarantine_path = self._quarantine(path)
        except OSError as quarantine_error:
            _LOG.error(
                "%s; quarantine failed for %s: %s",
                corruption_error,
                path,
                quarantine_error,
            )
            return None
        _LOG.error("%s; quarantined at %s", corruption_error, quarantine_path)
        return quarantine_path

    @staticmethod
    def _summary(
        artifact: dict[str, Any],
        fallback_run_id: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = metadata or {}
        header = artifact.get("header", {}) or {}
        timesteps = artifact.get("timesteps", []) or []
        summaries = [
            row.get("summary", {}) or {}
            for row in timesteps
            if isinstance(row, dict)
        ]
        temperatures = [
            row.get("T_C")
            for row in summaries
            if isinstance(row.get("T_C"), (int, float))
            and not isinstance(row.get("T_C"), bool)
        ]
        final_summary = summaries[-1] if summaries else {}
        metal_yields = final_summary.get("metal_yields_kg", {}) or {}
        headline_yields: dict[str, int | float] = {}
        headline_yield_semantics: dict[str, str] = {}
        summary_parts: list[str] = []
        fe_kg = metal_yields.get("Fe")
        if RunArtifactStore._is_finite_number(fe_kg):
            headline_yields["Fe"] = fe_kg
            headline_yield_semantics["Fe"] = "evolved_product"
            summary_parts.append(f"Fe {fe_kg:g} kg")
        o2_kg = final_summary.get("O2_source_side_potential_kg_cumulative")
        if not RunArtifactStore._is_finite_number(o2_kg):
            o2_kg = final_summary.get("O2_yield_kg_cumulative")
        if RunArtifactStore._is_finite_number(o2_kg):
            headline_yields["O2"] = o2_kg
            headline_yield_semantics["O2"] = "source_side_potential"
            summary_parts.append(f"O₂ (source-side) {o2_kg:g} kg")
        execution_status = artifact.get("execution_status")
        result = {
            "run_id": header.get("run_id", fallback_run_id),
            "name": header.get("name"),
            "feedstock_id": header.get("feedstock_id"),
            "campaign_chain": header.get("campaign_chain", []),
            "peak_T_C": max(temperatures) if temperatures else None,
            "headline_yields_kg": headline_yields,
            "execution_status": execution_status,
            "status": execution_status,
            "created_at": header.get("created_at"),
            "starred": bool(metadata.get("starred", False)),
            "summary": " · ".join(summary_parts),
        }
        if headline_yield_semantics:
            result["headline_yield_semantics"] = headline_yield_semantics
        if "lifecycle" in artifact:
            result["lifecycle"] = artifact["lifecycle"]
        final_hour = final_summary.get("hour")
        if RunArtifactStore._is_finite_number(final_hour):
            result["hours"] = final_hour
        if metadata.get("folder") is not None:
            result["folder"] = metadata["folder"]
        parent_run_id = metadata.get("parent_run_id", header.get("parent_run_id"))
        if parent_run_id is not None:
            result["parent_run_id"] = parent_run_id
        return result

    @staticmethod
    def _is_finite_number(value: Any) -> bool:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )

    def _apply_retention_locked(self) -> None:
        """Evict old unstarred runs; stars intentionally make growth unbounded."""
        unstarred: list[tuple[str, Path]] = []
        starred_count = 0
        for path in self._artifact_paths():
            try:
                artifact = self.load(path.stem)
                if artifact is None:
                    continue
                header = artifact.get("header", {}) or {}
                if self._has_quarantined_meta(path.stem):
                    continue
                metadata = self._load_meta(path.stem)
                if bool(metadata.get("starred", False)):
                    starred_count += 1
                else:
                    created_at = str(header.get("created_at") or "")
                    unstarred.append((created_at, path))
            except RunMetaCorruptionError as exc:
                self._quarantine_or_log(exc.path, exc)
                # A corrupt sidecar may be hiding a star. Skip eviction for
                # this run until metadata is repaired rather than deleting a
                # possibly protected artifact.
                continue
            except (OSError, json.JSONDecodeError, AttributeError, RunStoreCorruptionError):
                continue
        if starred_count > self.keep:
            _LOG.warning(
                "run store has %d starred artifacts, exceeding retention keep=%d; "
                "starred artifacts are intentionally never evicted",
                starred_count,
                self.keep,
            )
        unstarred.sort(key=lambda item: item[0], reverse=True)
        for _created_at, path in unstarred[self.keep:]:
            path.unlink(missing_ok=True)
            self._meta_path(path.stem).unlink(missing_ok=True)


def get_run_store() -> RunArtifactStore:
    runs_dir = current_app.config.get("RUN_ARTIFACT_DIR")
    if runs_dir is None:
        runs_dir = Path(current_app.instance_path) / "runs"
    keep = current_app.config.get("RUN_ARTIFACT_RETENTION", DEFAULT_RETENTION)
    return RunArtifactStore(runs_dir, keep=keep)


def save(
    run_id: str,
    artifact: dict[str, Any],
    *,
    parent_run_id: str | None = None,
) -> bool:
    return get_run_store().save(run_id, artifact, parent_run_id=parent_run_id)


def load(run_id: str) -> dict[str, Any] | None:
    return get_run_store().load(run_id)


def list_runs() -> list[dict[str, Any]]:
    return get_run_store().list_runs()


def persist_run_artifact(
    runner_payload: dict[str, Any],
    run_id: str,
    name: str | None = None,
    *,
    store: RunArtifactStore | None = None,
    parent_run_id: str | None = None,
) -> dict[str, Any]:
    artifact = build_run_artifact(runner_payload, run_id=run_id, name=name)
    stored = (store.save if store is not None else save)(
        run_id,
        artifact,
        parent_run_id=parent_run_id,
    )
    if not stored:
        raise DuplicateRunArtifactError(
            f"run artifact {run_id!r} was not written because the id already exists"
        )
    return artifact
