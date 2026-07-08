"""Study save-format ZIP exporter."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any
import zipfile

from simulator.optimize.evalspec import current_code_version

SAVE_SCHEMA_VERSION = 1
MEMBER_SCHEMA_VERSION = 1
ARTIFACT_INDEX_NAME = "artifact.index.json"
TERMINAL_STUDY_STATUSES = frozenset(
    {"completed", "completed-no-feasible-winner", "aborted"}
)
REQUIRED_MEMBERS = (
    "study.manifest.json",
    "study.summary.json",
    "study.profile.yaml",
    ARTIFACT_INDEX_NAME,
    "cache.sqlite",
    "pareto.json",
    "leaderboard.csv",
    "job_status.json",
)
OPTIONAL_MEMBERS = (
    "study.events.jsonl",
    "strategy_state.jsonl",
    "provenance.jsonl",
    "winner.recipe.yaml",
    "winner.tap-truncated.json",
    "two_phase_certification.json",
    "search_provenance.json",
)
ALLOWED_MEMBERS = frozenset((*REQUIRED_MEMBERS, *OPTIONAL_MEMBERS))
_REQUIRED_INPUT_MEMBERS = tuple(
    name for name in REQUIRED_MEMBERS if name != ARTIFACT_INDEX_NAME
)
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def export_study_bundle(
    run_dir: Path | str,
    *,
    output_path: Path | str | None = None,
) -> Path:
    """Write a `.rpstudy.zip` bundle for an optimizer run directory."""

    root = Path(run_dir).expanduser()
    if not root.is_dir():
        raise FileNotFoundError(f"optimizer run directory not found: {root}")

    missing = [name for name in _REQUIRED_INPUT_MEMBERS if not (root / name).is_file()]
    if missing:
        raise FileNotFoundError(
            "optimizer run missing required save member(s): " + ", ".join(missing)
        )
    _require_terminal_study_status(root)

    member_names = [
        *(_REQUIRED_INPUT_MEMBERS),
        *(name for name in OPTIONAL_MEMBERS if (root / name).is_file()),
    ]
    unknown = [name for name in member_names if name not in ALLOWED_MEMBERS]
    if unknown:
        raise ValueError("non-whitelisted save member(s): " + ", ".join(unknown))

    member_bytes = {
        name: _member_bytes_with_schema_version(name, (root / name).read_bytes())
        for name in member_names
    }
    index_payload = {
        "member_schema_version": MEMBER_SCHEMA_VERSION,
        # Spec §1: artifact.index.json lists every OTHER member; it cannot hash itself.
        "members": {
            name: _index_entry(name, data)
            for name, data in member_bytes.items()
        },
    }
    index_bytes = (
        json.dumps(index_payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
    (root / ARTIFACT_INDEX_NAME).write_bytes(index_bytes)

    destination = Path(output_path) if output_path is not None else root / _bundle_name(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(destination.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in member_names:
            archive.writestr(name, member_bytes[name])
        archive.writestr(ARTIFACT_INDEX_NAME, index_bytes)
    tmp.replace(destination)
    return destination


def _require_terminal_study_status(root: Path) -> None:
    manifest = _load_json_object(root / "study.manifest.json")
    summary = _load_json_object(root / "study.summary.json")
    raw_statuses = {
        "study.manifest.json": manifest.get("study_status"),
        "study.summary.json": summary.get("study_status"),
    }
    missing = [
        name
        for name, status in raw_statuses.items()
        if status is None or not str(status).strip()
    ]
    if missing:
        raise ValueError(
            "optimizer run missing study_status in "
            + ", ".join(missing)
            + "; refusing export"
        )
    statuses = {str(status) for status in raw_statuses.values()}
    if len(statuses) > 1:
        raise ValueError(
            "optimizer run has inconsistent study_status values: "
            + ", ".join(sorted(statuses))
        )
    status = next(iter(statuses))
    if status not in TERMINAL_STUDY_STATUSES:
        raise ValueError(
            f"optimizer run study_status={status!r} is not terminal; "
            "wait for completion or aborted finalization before export"
        )


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"optimizer run member is not valid JSON: {path.name}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"optimizer run member must be a JSON object: {path.name}")
    return payload


def _member_bytes_with_schema_version(name: str, data: bytes) -> bytes:
    if not name.endswith(".json"):
        return data
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return data
    if not isinstance(payload, dict) or payload.get("member_schema_version") is not None:
        return data
    payload = dict(payload)
    payload["member_schema_version"] = MEMBER_SCHEMA_VERSION
    return (
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")


def _index_entry(name: str, data: bytes) -> dict[str, Any]:
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "member_schema_version": _member_schema_version(name, data),
        "producer_code_version": current_code_version(),
    }


def _member_schema_version(name: str, data: bytes) -> int:
    if not name.endswith((".json", ".jsonl")):
        return MEMBER_SCHEMA_VERSION
    if name.endswith(".jsonl"):
        return MEMBER_SCHEMA_VERSION
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return MEMBER_SCHEMA_VERSION
    raw = payload.get("member_schema_version") if isinstance(payload, dict) else None
    try:
        version = int(raw)
    except (TypeError, ValueError):
        return MEMBER_SCHEMA_VERSION
    return version if version > 0 else MEMBER_SCHEMA_VERSION


def _bundle_name(run_dir: Path) -> str:
    try:
        summary = json.loads((run_dir / "study.summary.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        summary = {}
    feedstock = _safe_part(summary.get("feedstock_id"), "feedstock")
    profile = _safe_part(summary.get("profile_id"), "profile")
    study_id = _safe_part(summary.get("study_id"), run_dir.name)
    return f"{feedstock}-{profile}-{study_id}.rpstudy.zip"


def _safe_part(value: object, fallback: str) -> str:
    text = str(value or "").strip() or fallback
    text = _SAFE_FILENAME_RE.sub("-", text).strip(".-")
    return text or fallback
