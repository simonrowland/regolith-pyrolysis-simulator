"""Study save-format ZIP exporter."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import unquote
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
# Drive-letter root (`C:\` or `C:/`) at the START of a value (whole-value detection; see
# _is_absolute_host_path for why embedded scanning was removed).
_DRIVE_ROOT_AT_START_RE = re.compile(r"[A-Za-z]:[\\/]")
# Home-directory shorthand at the START: `~/…`, `~\…`, another user's `~name/…` / `~name\…`
# (expanduser resolves these and `~name` LEAKS the username literally), or a bare `~name`.
# Requires a separator OR a whole-value `~name`, so a stray `~5 minutes` free-text is NOT matched.
_HOME_SHORTHAND_RE = re.compile(r"~[^\s/\\]*[\\/]|~[^\s/\\]+\Z")
# Unicode slash homoglyphs normalized to ASCII so a fullwidth/fraction/division solidus (or
# fullwidth backslash) cannot smuggle a path separator past the prefix checks.
_SLASH_HOMOGLYPHS = str.maketrans(
    {"／": "/", "⁄": "/", "∕": "/", "⧸": "/", "＼": "\\"}
)
_HOST_PATH_REDACTION = "<redacted-host-path>"
# Manifest keys whose ENTIRE entry is dropped on export. Deliberately EMPTY:
# host-path leakage is handled by redacting VALUES (see _redact_host_path_string),
# never by removing keys. Dropping a key can fail a presence-guard open downstream —
# e.g. study.py journal replay demands bundled warm-start seed state iff the imported
# manifest still carries a non-None warm_start_from; dropping the key would skip that
# guard and replay a warm-started study without its seed. warm_start_from's host path
# is redacted by value instead, so the key stays present and the guard still fires.
_MANIFEST_HOST_PATH_KEYS: frozenset[str] = frozenset()


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
        # Spec §Integrity: the index carries member_schema_version (+ per-member sha,
        # size, producer code version). save_schema_version is a manifest/summary field
        # (Spec §Versioning), NOT an index field — kept out to stay spec-conformant so a
        # spec-valid third-party bundle is not wrongly rejected by a stricter importer.
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
    if not isinstance(payload, dict):
        return data
    changed = False
    if name == "study.manifest.json":
        sanitized = _sanitize_manifest_for_export(payload)
        if sanitized != payload:
            payload = sanitized
            changed = True
    if payload.get("member_schema_version") is None:
        payload = dict(payload)
        payload["member_schema_version"] = MEMBER_SCHEMA_VERSION
        changed = True
    if not changed:
        return data
    return (
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")


def _sanitize_manifest_for_export(value: Any) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[Any, Any] = {}
        for key, nested in value.items():
            if isinstance(key, str) and key in _MANIFEST_HOST_PATH_KEYS:
                continue
            sanitized_key = _redact_host_path_string(key) if isinstance(key, str) else key
            sanitized[sanitized_key] = _sanitize_manifest_for_export(nested)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_manifest_for_export(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_manifest_for_export(item) for item in value]
    if isinstance(value, str):
        return _redact_host_path_string(value)
    return value


def _redact_host_path_string(value: str) -> str:
    return _HOST_PATH_REDACTION if _is_absolute_host_path(value) else value


def _is_absolute_host_path(value: str) -> bool:
    # WHOLE-VALUE detection: an export-manifest host-path field (warm_start_from,
    # diagnostic_path, …) holds a path AS the entire value, so we redact values that ARE an
    # absolute host path — not values that merely EMBED a path-like substring. Embedded
    # scanning was removed after it over-redacted legitimate repo-relative identity values
    # (e.g. a data_digests key `pkg/data/feedstocks.yaml` contains `/data/`). Embedded/nested
    # host paths inside a larger free-text value are not produced by the exporter and are
    # out of the export threat model (see redaction-threat-model.md).
    text = value.strip()
    if not text:
        return False
    # Percent-decode to a FIXED POINT (bounded) so a multiply-encoded path
    # (`%252F` -> `%2F` -> `/`) cannot bypass the prefix checks.
    forms = [text]
    decoded = text
    for _ in range(6):
        nxt = unquote(decoded)
        if nxt == decoded:
            break
        decoded = nxt
        forms.append(decoded)
    for raw in forms:
        # Normalize unicode slash homoglyphs to ASCII so they cannot smuggle a separator.
        candidate = raw.translate(_SLASH_HOMOGLYPHS)
        if (
            candidate.startswith("/")                        # POSIX absolute
            or candidate.startswith("\\")                    # Windows UNC (\\) or drive-relative root (\)
            or _HOME_SHORTHAND_RE.match(candidate) is not None  # ~/… ~name/… bare ~name
            or candidate.lower().startswith("file:")         # file: URI
            or _DRIVE_ROOT_AT_START_RE.match(candidate) is not None  # C:\ or C:/
        ):
            return True
    return False


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
