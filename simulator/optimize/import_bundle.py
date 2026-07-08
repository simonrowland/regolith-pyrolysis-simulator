"""Fail-closed importer for untrusted optimizer study bundles."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
from shutil import rmtree
import sqlite3
import stat
import struct
from tempfile import mkdtemp
from typing import Any, Callable, Mapping
from urllib.parse import quote
from uuid import uuid4
import zipfile

import yaml

from simulator.corpus_version import current_corpus_version
from simulator.optimize.evaluate import ScoredResult, evaluate
from simulator.optimize.profiles import ProfileValidationError
from simulator.optimize.recipe import RecipePatch, RecipeSchema, RecipeValidationError
from simulator.optimize.save_bundle import (
    ALLOWED_MEMBERS,
    ARTIFACT_INDEX_NAME,
    MEMBER_SCHEMA_VERSION,
    REQUIRED_MEMBERS,
)

BUNDLE_CAP_BYTES = 512 * 1024 * 1024
SQLITE_CAP_BYTES = 400 * 1024 * 1024
JSON_CAP_BYTES = 32 * 1024 * 1024
CSV_CAP_BYTES = 16 * 1024 * 1024
YAML_CAP_BYTES = 1 * 1024 * 1024
ZIP_DEPTH_LIMIT = 64
JSON_DEPTH_LIMIT = 64
MAX_IMPORTED_ELEMENT_COUNT = 10_000
SQLITE_RESULTS_MAX_ROWS = MAX_IMPORTED_ELEMENT_COUNT
SQLITE_PROGRESS_STEP = 1_000
SQLITE_PROGRESS_OP_LIMIT = 1_000_000
ZIP_READ_CHUNK_BYTES = 1024 * 1024
ZIP_EOCD_SEARCH_BYTES = (64 * 1024) + 22
ZIP_EOCD_SIGNATURE = 0x06054B50
ZIP64_EOCD_SIGNATURE = 0x06064B50
ZIP64_EOCD_LOCATOR_SIGNATURE = 0x07064B50
ZIP_CENTRAL_DIRECTORY_SIGNATURE = 0x02014B50
ZIP_CENTRAL_DIRECTORY_FIXED_BYTES = 46
IMPORTED_DIR_NAME = "imported"
IMPORT_OVERLAY_NAME = "import.overlay.json"
VERDICT_CONFIRMED = "confirmed"
VERDICT_DISPUTED = "disputed"
VERDICT_DRIFTED = "drifted"
VERDICT_UNCHANGED = "unchanged"
VERDICT_NOT_REEVALUABLE = "not-re-evaluable"
VERIFICATION_VERDICTS = frozenset(
    {
        VERDICT_CONFIRMED,
        VERDICT_DISPUTED,
        VERDICT_DRIFTED,
        VERDICT_UNCHANGED,
        VERDICT_NOT_REEVALUABLE,
    }
)
IMPORTED_BADGE = {
    "origin": "imported",
    "ux_label": "UNVERIFIED",
    "tier": "imported",
    "title": "imported bundle; local verification absent or incomplete",
}
_SAFE_STUDY_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)
_IDENTITY_FIELDS = (
    "corpus_version",
    "code_version",
    "recipe_schema_version",
    "allowlist_version",
    "bounds_digest",
    "data_digests",
    "backend_name",
    "chemistry_kernel",
)
_LEADERBOARD_META_COLUMNS = frozenset(
    {
        "rank",
        "candidate_id",
        "cache_key",
        "is_pareto",
        "is_winner",
        "proposal_source",
        "seed_lineage",
        "patch_json",
        "materialized_patch_json",
        "parent_trajectory_patch_json",
    }
)


class ImportBundleError(ValueError):
    """Raised when an uploaded study bundle violates the import contract."""


class ImportSafetyCapError(ImportBundleError):
    """Raised when an import safety cap is exceeded."""


@dataclass(frozen=True)
class ImportedStudy:
    study_id: str
    path: Path
    deduped: bool
    warning: str | None = None


def import_study_bundle(
    bundle_path: Path | str,
    runs_root: Path | str,
    *,
    uploader_note: str = "",
    origin: Mapping[str, Any] | None = None,
    verification_tier: int = 1,
    verification_top_n: int = 8,
    evaluator: Callable[..., ScoredResult] | None = None,
) -> ImportedStudy:
    """Import an untrusted `.rpstudy.zip` into `runs/imported/<study_id>/`."""

    source = Path(bundle_path)
    root = Path(runs_root)
    if not source.is_file():
        raise ImportBundleError(f"bundle not found: {source}")
    if source.stat().st_size > BUNDLE_CAP_BYTES:
        raise ImportSafetyCapError("bundle exceeds 512MB cap")

    imported_root = root / IMPORTED_DIR_NAME
    imported_root.mkdir(parents=True, exist_ok=True)
    tmp = Path(mkdtemp(prefix=".import.", dir=imported_root))
    try:
        parsed, member_names = _extract_bundle_members(source, tmp)
        _verify_artifact_index_files(
            tmp,
            member_names,
            parsed[ARTIFACT_INDEX_NAME],
        )

        manifest = parsed["study.manifest.json"]
        summary = parsed["study.summary.json"]
        study_id = _safe_study_id(summary.get("study_id") or manifest.get("study_id"))
        if not study_id:
            raise ImportBundleError("bundle missing safe study_id")

        # Safety caps are unconditional on any extracted untrusted bundle — they
        # MUST run before the dedupe early-return, else an over-cap bundle whose
        # study_id already matches a quarantine dir would bypass the row/count
        # caps (a safety limit is not a limit if a dedupe short-circuit skips it).
        _enforce_import_safety_caps(tmp)

        destination, deduped, warning = _destination_for_import(
            imported_root,
            study_id,
            (tmp / ARTIFACT_INDEX_NAME).read_bytes(),
        )
        if deduped:
            rmtree(tmp, ignore_errors=True)
            return ImportedStudy(destination.name, destination, True, warning)

        with open_untrusted_result_db(tmp / "cache.sqlite") as conn:
            conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
        verification = verify_imported_study(
            tmp,
            tier=verification_tier,
            top_n=verification_top_n,
            evaluator=evaluator,
        )
        overlay = _overlay_payload(
            imported_study_id=destination.name,
            source_study_id=study_id,
            source=source,
            origin=origin,
            uploader_note=uploader_note,
            verification=verification,
            warning=warning,
        )
        (tmp / IMPORT_OVERLAY_NAME).write_text(
            _json_dump(overlay),
            encoding="utf-8",
        )
        tmp.replace(destination)
    except Exception:
        rmtree(tmp, ignore_errors=True)
        raise
    return ImportedStudy(destination.name, destination, False, warning)


def open_untrusted_result_db(path: Path | str) -> sqlite3.Connection:
    """Open imported sqlite read-only with query-only/defensive guards."""

    db_path = Path(path)
    if not db_path.is_file():
        raise ImportBundleError(f"imported sqlite not found: {db_path}")
    if db_path.stat().st_size > SQLITE_CAP_BYTES:
        raise ImportSafetyCapError("cache.sqlite exceeds 400MB cap")
    uri = "file:" + quote(str(db_path.resolve()), safe="/") + "?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.row_factory = sqlite3.Row
        _install_progress_limit(conn)
        conn.set_authorizer(_untrusted_sql_authorizer)
        try:
            conn.enable_load_extension(False)
        except (AttributeError, sqlite3.Error):
            pass
        if hasattr(conn, "setconfig") and hasattr(sqlite3, "SQLITE_DBCONFIG_DEFENSIVE"):
            try:
                conn.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)
            except sqlite3.Error:
                pass
        conn.execute("PRAGMA query_only = ON")
        try:
            conn.execute("PRAGMA trusted_schema = OFF")
        except sqlite3.Error:
            pass
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
        if page_size * page_count > SQLITE_CAP_BYTES:
            raise ImportSafetyCapError("cache.sqlite page footprint exceeds 400MB cap")
    except Exception:
        conn.close()
        raise
    return conn


def verify_imported_study(
    import_dir: Path | str,
    *,
    tier: int = 1,
    top_n: int = 8,
    evaluator: Callable[..., ScoredResult] | None = None,
) -> dict[str, Any]:
    """Run tier-0 hash verification and optional tier-1 local re-evaluation."""

    root = Path(import_dir)
    hash_report = verify_import_hashes(root)
    safety = _enforce_import_safety_caps(root)
    report: dict[str, Any] = {
        "tier": int(tier),
        "generated_at": _now(),
        "hash_check": hash_report,
        "coverage": {
            "state": "unverified",
            "selection": [],
            "top_n": int(top_n),
        },
        "candidates": [],
    }
    if tier <= 0:
        return report

    try:
        candidates = _selected_verification_candidates(
            root,
            top_n=top_n,
            leaderboard=safety["leaderboard"],
        )
    except ImportSafetyCapError:
        raise
    except ImportBundleError as exc:
        report["coverage"]["reason"] = str(exc)
        return report
    report["coverage"]["selection"] = [
        {
            "cache_key": candidate.get("cache_key"),
            "candidate_id": candidate.get("candidate_id"),
        }
        for candidate in candidates
    ]
    if not candidates:
        report["coverage"]["reason"] = "no-candidates"
        return report

    profile = _load_profile(root / "study.profile.yaml")
    manifest = _load_json_object(root / "study.manifest.json")
    active_evaluator = evaluator or evaluate
    rows_by_cache_key = safety["rows_by_cache_key"]
    results = []
    for candidate in candidates:
        results.append(
            _verify_candidate(
                candidate,
                profile=profile,
                manifest=manifest,
                imported_row=rows_by_cache_key.get(str(candidate.get("cache_key") or "")),
                evaluator=active_evaluator,
                root=root,
            )
        )
    report["candidates"] = results
    report["coverage"]["state"] = "verified"
    report["coverage"]["verified_count"] = len(results)
    return report


def _enforce_import_safety_caps(root: Path) -> dict[str, Any]:
    return {
        "leaderboard": _read_leaderboard(root / "leaderboard.csv"),
        "rows_by_cache_key": _imported_rows_by_cache_key(root / "cache.sqlite"),
    }


def verify_import_hashes(import_dir: Path | str) -> dict[str, Any]:
    root = Path(import_dir)
    index = _load_json_object(root / ARTIFACT_INDEX_NAME)
    members = index.get("members")
    if not isinstance(members, Mapping):
        raise ImportBundleError("artifact.index.json members must be an object")
    checked = []
    for name, entry in members.items():
        if name not in ALLOWED_MEMBERS or name == ARTIFACT_INDEX_NAME:
            raise ImportBundleError(f"artifact.index.json names non-member {name!r}")
        if not isinstance(entry, Mapping):
            raise ImportBundleError(f"artifact.index.json entry must be object: {name}")
        path = root / str(name)
        _assert_child_path(root, path)
        if not path.is_file():
            raise ImportBundleError(f"indexed member missing after import: {name}")
        expected_size = int(entry.get("size_bytes", -1))
        expected_sha = str(entry.get("sha256") or "")
        if expected_size != path.stat().st_size:
            raise ImportBundleError(f"artifact size mismatch: {name}")
        if expected_sha != _sha256_file(path):
            raise ImportBundleError(f"artifact hash mismatch: {name}")
        checked.append(str(name))
    return {
        "verdict": VERDICT_CONFIRMED,
        "checked_members": sorted(checked),
    }


def imported_studies(runs_root: Path | str) -> list[dict[str, Any]]:
    imported_root = Path(runs_root) / IMPORTED_DIR_NAME
    if not imported_root.is_dir():
        return []
    studies = []
    for child in sorted(imported_root.iterdir()):
        if child.is_dir() and (child / "study.summary.json").is_file():
            try:
                studies.append(imported_study_model(child, runs_root))
            except (ImportBundleError, OSError, ValueError) as exc:
                studies.append(
                    {
                        "study_id": child.name,
                        "path": str(child),
                        "error": str(exc),
                        "badges": dict(IMPORTED_BADGE),
                    }
                )
    return sorted(studies, key=lambda item: item.get("imported_at") or "", reverse=True)


def imported_study_model(
    import_dir: Path | str,
    runs_root: Path | str | None = None,
) -> dict[str, Any]:
    root = Path(import_dir)
    summary = _load_json_object(root / "study.summary.json")
    overlay = _load_overlay(root)
    leaderboard = _read_leaderboard(root / "leaderboard.csv")
    pareto = _load_json_object(root / "pareto.json")
    runs = Path(runs_root) if runs_root is not None else root.parent.parent
    badges = _overlay_badges(overlay)
    verification = _overlay_verification(overlay)
    return {
        "study_id": root.name,
        "source_study_id": summary.get("study_id"),
        "feedstock_id": summary.get("feedstock_id"),
        "profile_id": summary.get("profile_id"),
        "study_status": summary.get("study_status"),
        "created_at": summary.get("created_at"),
        "imported_at": overlay.get("imported_at"),
        "origin": overlay.get("origin", {"kind": "imported"}),
        "badges": badges,
        "verification": verification,
        "summary": summary,
        "leaderboard": leaderboard,
        "pareto": pareto.get("pareto", []),
        "path": str(root),
        "relative_path": _relative_to(root, runs),
        "artifacts": _imported_artifacts(root, runs),
    }


def is_imported_path(path: Path | str, runs_root: Path | str) -> bool:
    candidate = Path(path).resolve()
    imported_root = (Path(runs_root) / IMPORTED_DIR_NAME).resolve()
    try:
        candidate.relative_to(imported_root)
    except ValueError:
        return False
    return True


def _extract_bundle_members(
    source: Path,
    target_root: Path,
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    parsed_json: dict[str, dict[str, Any]] = {}
    _preflight_zip_entry_count(source)
    try:
        archive = zipfile.ZipFile(source)
    except zipfile.BadZipFile as exc:
        raise ImportBundleError("bundle is not a valid zip") from exc
    with archive:
        infos = archive.filelist
        _check_element_cap("bundle", len(infos), "zip entry")
        member_names = _zip_member_names(infos)
        for info in infos:
            _validate_zip_member(info)
        required = set(REQUIRED_MEMBERS)
        missing = sorted(required - member_names)
        if missing:
            raise ImportBundleError("bundle missing required member(s): " + ", ".join(missing))
        unknown = sorted(member_names - set(ALLOWED_MEMBERS))
        if unknown:
            raise ImportBundleError("unknown bundle member(s): " + ", ".join(unknown))
        total_uncompressed = 0
        for info in infos:
            cap = _member_cap(info.filename)
            if info.file_size > cap:
                raise ImportSafetyCapError(
                    f"{info.filename} exceeds {_cap_label(cap)} cap"
                )
            remaining_bundle_cap = BUNDLE_CAP_BYTES - total_uncompressed
            if info.file_size > remaining_bundle_cap:
                raise ImportSafetyCapError(
                    f"bundle uncompressed size exceeds {_cap_label(BUNDLE_CAP_BYTES)} cap"
                )
            target = target_root / info.filename
            _assert_child_path(target_root, target)
            actual_size = _extract_zip_member_bounded(
                archive,
                info,
                target,
                member_cap=cap,
                bundle_cap=remaining_bundle_cap,
            )
            total_uncompressed += actual_size
            if info.filename.endswith(".json"):
                parsed_json[info.filename] = _parse_json_object(
                    info.filename,
                    (target_root / info.filename).read_bytes(),
                )
            elif info.filename.endswith(".jsonl"):
                _validate_jsonl_member(
                    info.filename,
                    (target_root / info.filename).read_bytes(),
                )
    return parsed_json, member_names


def _preflight_zip_entry_count(source: Path) -> None:
    total_entries, directory_offset, directory_size = _zip_central_directory_bounds(source)
    _check_element_cap("bundle", total_entries, "zip entry")
    scanned_entries = _scan_zip_central_directory_entries(
        source,
        directory_offset=directory_offset,
        directory_size=directory_size,
    )
    if scanned_entries != total_entries:
        raise ImportBundleError("bundle zip central directory count mismatch")


def _zip_central_directory_bounds(source: Path) -> tuple[int, int, int]:
    file_size = source.stat().st_size
    read_size = min(file_size, ZIP_EOCD_SEARCH_BYTES)
    with source.open("rb") as handle:
        handle.seek(file_size - read_size)
        tail = handle.read(read_size)
    eocd_offset = _find_eocd_offset(tail)
    if eocd_offset is None:
        raise ImportBundleError("bundle is not a valid zip")

    absolute_eocd_offset = file_size - read_size + eocd_offset
    (
        _signature,
        disk_number,
        central_directory_disk,
        entries_on_disk,
        total_entries,
        directory_size,
        directory_offset,
        _comment_length,
    ) = struct.unpack_from("<IHHHHIIH", tail, eocd_offset)
    if disk_number or central_directory_disk or entries_on_disk != total_entries:
        raise ImportBundleError("multi-disk zip bundle rejected")

    if (
        total_entries == 0xFFFF
        or directory_size == 0xFFFFFFFF
        or directory_offset == 0xFFFFFFFF
    ):
        return _zip64_central_directory_bounds(source, absolute_eocd_offset)

    _validate_zip_central_directory_bounds(file_size, directory_offset, directory_size)
    return total_entries, directory_offset, directory_size


def _find_eocd_offset(tail: bytes) -> int | None:
    for offset in range(len(tail) - 22, -1, -1):
        if struct.unpack_from("<I", tail, offset)[0] != ZIP_EOCD_SIGNATURE:
            continue
        comment_length = struct.unpack_from("<H", tail, offset + 20)[0]
        if offset + 22 + comment_length == len(tail):
            return offset
    return None


def _zip64_central_directory_bounds(
    source: Path,
    eocd_offset: int,
) -> tuple[int, int, int]:
    locator_offset = eocd_offset - 20
    if locator_offset < 0:
        raise ImportBundleError("bundle zip64 locator missing")
    with source.open("rb") as handle:
        handle.seek(locator_offset)
        locator = handle.read(20)
        if len(locator) != 20:
            raise ImportBundleError("bundle zip64 locator truncated")
        (
            signature,
            locator_disk,
            zip64_eocd_offset,
            total_disks,
        ) = struct.unpack("<IIQI", locator)
        if signature != ZIP64_EOCD_LOCATOR_SIGNATURE:
            raise ImportBundleError("bundle zip64 locator missing")
        if locator_disk or total_disks != 1:
            raise ImportBundleError("multi-disk zip bundle rejected")

        handle.seek(zip64_eocd_offset)
        record = handle.read(56)
    if len(record) != 56:
        raise ImportBundleError("bundle zip64 end record truncated")
    (
        signature,
        _record_size,
        _version_made,
        _version_needed,
        disk_number,
        central_directory_disk,
        entries_on_disk,
        total_entries,
        directory_size,
        directory_offset,
    ) = struct.unpack("<IQHHIIQQQQ", record)
    if signature != ZIP64_EOCD_SIGNATURE:
        raise ImportBundleError("bundle zip64 end record missing")
    if disk_number or central_directory_disk or entries_on_disk != total_entries:
        raise ImportBundleError("multi-disk zip bundle rejected")
    _validate_zip_central_directory_bounds(
        source.stat().st_size,
        directory_offset,
        directory_size,
    )
    return int(total_entries), int(directory_offset), int(directory_size)


def _validate_zip_central_directory_bounds(
    file_size: int,
    directory_offset: int,
    directory_size: int,
) -> None:
    if (
        directory_offset < 0
        or directory_size < 0
        or directory_offset > file_size
        or directory_offset + directory_size > file_size
    ):
        raise ImportBundleError("bundle zip central directory is invalid")


def _scan_zip_central_directory_entries(
    source: Path,
    *,
    directory_offset: int,
    directory_size: int,
) -> int:
    directory_end = directory_offset + directory_size
    count = 0
    with source.open("rb") as handle:
        handle.seek(directory_offset)
        while handle.tell() < directory_end:
            fixed = handle.read(ZIP_CENTRAL_DIRECTORY_FIXED_BYTES)
            if len(fixed) != ZIP_CENTRAL_DIRECTORY_FIXED_BYTES:
                raise ImportBundleError("bundle zip central directory is truncated")
            signature = struct.unpack_from("<I", fixed)[0]
            if signature != ZIP_CENTRAL_DIRECTORY_SIGNATURE:
                raise ImportBundleError("bundle zip central directory is invalid")
            name_length, extra_length, comment_length = struct.unpack_from("<HHH", fixed, 28)
            handle.seek(name_length + extra_length + comment_length, 1)
            if handle.tell() > directory_end:
                raise ImportBundleError("bundle zip central directory is invalid")
            count += 1
            _check_element_cap("bundle", count, "zip entry")
    return count


def _zip_member_names(infos: list[zipfile.ZipInfo]) -> set[str]:
    seen_names: set[str] = set()
    duplicates: set[str] = set()
    for info in infos:
        if info.filename in seen_names:
            duplicates.add(info.filename)
            continue
        seen_names.add(info.filename)
    if duplicates:
        raise ImportBundleError(
            "duplicate bundle member(s): " + ", ".join(sorted(duplicates))
        )
    return seen_names


def _extract_zip_member_bounded(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    target: Path,
    *,
    member_cap: int,
    bundle_cap: int,
) -> int:
    total = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(info, "r") as handle, target.open("wb") as output:
        while True:
            remaining = min(member_cap, bundle_cap) - total
            read_size = min(ZIP_READ_CHUNK_BYTES, max(remaining, 0) + 1)
            chunk = handle.read(read_size)
            if not chunk:
                break
            total += len(chunk)
            if total > member_cap:
                raise ImportSafetyCapError(
                    f"{info.filename} exceeds {_cap_label(member_cap)} cap"
                )
            if total > bundle_cap:
                raise ImportSafetyCapError(
                    f"bundle uncompressed size exceeds {_cap_label(BUNDLE_CAP_BYTES)} cap"
                )
            output.write(chunk)
    return total


def _validate_zip_member(info: zipfile.ZipInfo) -> None:
    name = info.filename
    if not name or name.endswith("/"):
        raise ImportBundleError("directory entries are not allowed in bundles")
    if "\\" in name:
        raise ImportBundleError(f"zip-slip member rejected: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ImportBundleError(f"zip-slip member rejected: {name!r}")
    if len(path.parts) > ZIP_DEPTH_LIMIT:
        raise ImportSafetyCapError(f"zip member depth exceeds {ZIP_DEPTH_LIMIT}: {name!r}")
    mode = (info.external_attr >> 16) & 0o170000
    if mode and stat.S_ISLNK(mode):
        raise ImportBundleError(f"symlink member rejected: {name!r}")


def _member_cap(name: str) -> int:
    if name == "cache.sqlite":
        return SQLITE_CAP_BYTES
    if name == "leaderboard.csv":
        return CSV_CAP_BYTES
    if name.endswith((".yaml", ".yml")):
        return YAML_CAP_BYTES
    if name.endswith((".json", ".jsonl")):
        return JSON_CAP_BYTES
    return JSON_CAP_BYTES


def _cap_label(cap: int) -> str:
    if cap % (1024 * 1024) == 0:
        return f"{cap // (1024 * 1024)}MB"
    return f"{cap} byte"


def _check_element_cap(name: str, count: int, element: str) -> None:
    if count > MAX_IMPORTED_ELEMENT_COUNT:
        raise ImportSafetyCapError(
            f"{name} exceeds {MAX_IMPORTED_ELEMENT_COUNT} {element} cap"
        )


def _parse_json_object(name: str, data: bytes) -> dict[str, Any]:
    payload = _parse_json_value(name, data)
    if not isinstance(payload, dict):
        raise ImportBundleError(f"{name} must be a JSON object")
    return payload


def _parse_json_value(name: str, data: bytes | str) -> Any:
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ImportBundleError(f"{name} is not valid JSON") from exc
    else:
        text = data
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ImportBundleError(f"{name} is not valid JSON") from exc
    _validate_untrusted_structure(name, payload, format_name="JSON")
    return payload


def _validate_jsonl_member(name: str, data: bytes) -> None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ImportBundleError(f"{name} is not UTF-8 JSONL") from exc
    for line_number, line in enumerate(io.StringIO(text), start=1):
        _check_element_cap(name, line_number, "row")
        if not line.strip():
            continue
        _parse_json_value(f"{name}:{line_number}", line)


def _validate_untrusted_structure(
    name: str,
    value: Any,
    *,
    format_name: str,
) -> None:
    stack: list[tuple[Any, int]] = [(value, 1)]
    max_depth = 0
    total_elements = 0
    while stack:
        node, depth = stack.pop()
        max_depth = max(max_depth, depth)
        if max_depth > JSON_DEPTH_LIMIT:
            raise ImportSafetyCapError(
                f"{name} {format_name} depth exceeds {JSON_DEPTH_LIMIT}"
            )
        if isinstance(node, Mapping):
            _check_element_cap(f"{name} {format_name} object", len(node), "element")
            total_elements += len(node)
            _check_element_cap(f"{name} {format_name}", total_elements, "total element")
            stack.extend((nested, depth + 1) for nested in node.values())
        elif isinstance(node, list):
            _check_element_cap(f"{name} {format_name} array", len(node), "element")
            total_elements += len(node)
            _check_element_cap(f"{name} {format_name}", total_elements, "total element")
            stack.extend((nested, depth + 1) for nested in node)


def _verify_artifact_index_files(
    root: Path,
    member_names: set[str],
    index_payload: Mapping[str, Any],
) -> None:
    index_members = index_payload.get("members")
    if not isinstance(index_members, Mapping):
        raise ImportBundleError("artifact.index.json members must be an object")
    expected_names = set(member_names) - {ARTIFACT_INDEX_NAME}
    if set(index_members) != expected_names:
        raise ImportBundleError("artifact.index.json member set mismatch")
    for name in sorted(expected_names):
        entry = index_members.get(name)
        if not isinstance(entry, Mapping):
            raise ImportBundleError(f"artifact.index.json entry must be object: {name}")
        path = root / name
        _assert_child_path(root, path)
        if not path.is_file():
            raise ImportBundleError(f"indexed member missing after import: {name}")
        if int(entry.get("size_bytes", -1)) != path.stat().st_size:
            raise ImportBundleError(f"artifact size mismatch: {name}")
        if str(entry.get("sha256") or "") != _sha256_file(path):
            raise ImportBundleError(f"artifact hash mismatch: {name}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(ZIP_READ_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _destination_for_import(
    imported_root: Path,
    study_id: str,
    index_bytes: bytes,
) -> tuple[Path, bool, str | None]:
    first = imported_root / study_id
    if not first.exists():
        return first, False, None
    existing_index = first / ARTIFACT_INDEX_NAME
    if existing_index.is_file() and existing_index.read_bytes() == index_bytes:
        return first, True, "identical import already exists"
    suffix = 2
    while True:
        candidate = imported_root / f"{study_id}-{suffix}"
        if not candidate.exists():
            return (
                candidate,
                False,
                f"study_id collision for {study_id}; imported as {candidate.name}",
            )
        suffix += 1


def _overlay_payload(
    *,
    imported_study_id: str,
    source_study_id: str,
    source: Path,
    origin: Mapping[str, Any] | None,
    uploader_note: str,
    verification: Mapping[str, Any],
    warning: str | None,
) -> dict[str, Any]:
    badges = _badges_for_verification(verification)
    return {
        "member_schema_version": MEMBER_SCHEMA_VERSION,
        "imported_study_id": imported_study_id,
        "source_study_id": source_study_id,
        "imported_at": _now(),
        "origin": {
            "kind": "uploaded",
            "filename": source.name,
            **dict(origin or {}),
        },
        "uploader_note": uploader_note,
        "warning": warning,
        "badges": badges,
        "verification": dict(verification),
    }


def _badges_for_verification(verification: Mapping[str, Any]) -> dict[str, Any]:
    badge = dict(IMPORTED_BADGE)
    candidates = verification.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return badge
    verdicts = {
        str(candidate.get("verdict"))
        for candidate in candidates
        if isinstance(candidate, Mapping)
    }
    if VERDICT_DISPUTED in verdicts:
        label = "DISPUTED"
    elif VERDICT_DRIFTED in verdicts:
        label = "DRIFTED"
    elif VERDICT_NOT_REEVALUABLE in verdicts:
        label = "UNVERIFIED"
    elif verdicts == {VERDICT_CONFIRMED}:
        label = "CONFIRMED"
    elif verdicts <= {VERDICT_CONFIRMED, VERDICT_UNCHANGED}:
        label = "UNCHANGED"
    else:
        label = "UNVERIFIED"
    badge.update(
        {
            "ux_label": label,
            "tier": f"tier-{verification.get('tier', 0)}",
            "title": "imported bundle verification by local recomputation",
        }
    )
    return badge


def _selected_verification_candidates(
    root: Path,
    *,
    top_n: int,
    leaderboard: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    summary = _load_json_object(root / "study.summary.json")
    rows = (
        leaderboard
        if leaderboard is not None
        else _read_leaderboard(root / "leaderboard.csv")
    )
    if not rows:
        return []
    status = str(summary.get("study_status") or "")
    winners = [row for row in rows if _truthy(row.get("is_winner"))]
    if winners and status == "completed":
        selected: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for row in [*winners[:1], *(row for row in rows if _truthy(row.get("is_pareto")))]:
            key = (
                str(row.get("cache_key") or ""),
                str(row.get("candidate_id") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            selected.append(row)
        return selected
    return rows[: max(0, top_n)]


def _verify_candidate(
    candidate: Mapping[str, Any],
    *,
    profile: Mapping[str, Any],
    manifest: Mapping[str, Any],
    imported_row: Mapping[str, Any] | None,
    evaluator: Callable[..., ScoredResult],
    root: Path,
) -> dict[str, Any]:
    cache_key = str(candidate.get("cache_key") or "")
    candidate_id = str(candidate.get("candidate_id") or cache_key[:16] or "candidate")
    base = {
        "cache_key": cache_key,
        "candidate_id": candidate_id,
    }
    try:
        patch = _patch_from_leaderboard(candidate, root=root)
        schema = RecipeSchema()
        patch.validated(schema)
    except RecipeValidationError as exc:
        return {
            **base,
            "verdict": VERDICT_NOT_REEVALUABLE,
            "reason_codes": ["knob-vocabulary-changed"],
            "message": str(exc),
        }
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return {
            **base,
            "verdict": VERDICT_NOT_REEVALUABLE,
            "reason_codes": ["knob-vocabulary-changed"],
            "message": str(exc),
        }

    claim = _imported_claim(candidate, imported_row)
    feedstock = str(
        claim.get("feedstock_id")
        or candidate.get("feedstock_id")
        or profile.get("feedstock")
        or profile.get("feedstock_id")
        or ""
    )
    fidelity = str(
        claim.get("fidelity")
        or candidate.get("fidelity")
        or profile.get("default_fidelity")
        or "fast"
    )
    try:
        fresh = evaluator(
            patch,
            feedstock,
            fidelity,
            profile=profile,
            candidate_id=candidate_id,
            schema=schema,
        )
    except ProfileValidationError as exc:
        return {
            **base,
            "verdict": VERDICT_NOT_REEVALUABLE,
            "reason_codes": ["objective-vocabulary-changed"],
            "message": str(exc),
        }
    if fresh.eval_spec is None:
        return {
            **base,
            "verdict": VERDICT_NOT_REEVALUABLE,
            "reason_codes": ["knob-vocabulary-changed"],
            "message": "; ".join(str(note) for note in fresh.notes),
        }

    local_claim = _local_claim(fresh)
    same, moved = _same_identity_epoch(
        local_claim.get("identity", {}),
        _imported_identity(claim, manifest),
    )
    match, mismatches = _claims_match(local_claim, claim)
    if same and match:
        verdict = VERDICT_CONFIRMED
        reasons: list[str] = []
    elif same:
        verdict = VERDICT_DISPUTED
        reasons = mismatches or ["same-epoch-mismatch"]
    elif match:
        verdict = VERDICT_UNCHANGED
        reasons = moved
    else:
        verdict = VERDICT_DRIFTED
        reasons = moved or mismatches or ["identity-drift"]
    return {
        **base,
        "verdict": verdict,
        "reason_codes": reasons,
        "local": local_claim,
        "imported": claim,
    }


def _patch_from_leaderboard(candidate: Mapping[str, Any], *, root: Path) -> RecipePatch:
    raw = candidate.get("patch_json")
    if isinstance(raw, str) and raw.strip():
        payload = json.loads(raw)
        _validate_untrusted_structure(
            "leaderboard.csv patch_json",
            payload,
            format_name="JSON",
        )
    elif _truthy(candidate.get("is_winner")):
        payload = _load_winner_recipe_patch(root / "winner.recipe.yaml")
    else:
        raise ValueError("leaderboard row missing patch_json")
    if not isinstance(payload, Mapping):
        raise ValueError("leaderboard patch_json must decode to object")
    return RecipePatch.from_nested(payload)


def _load_winner_recipe_patch(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("winner row missing patch_json and winner.recipe.yaml")
    if path.stat().st_size > YAML_CAP_BYTES:
        raise ImportSafetyCapError("winner.recipe.yaml exceeds 1MB cap")
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ValueError("winner.recipe.yaml is not valid YAML") from exc
    _validate_untrusted_structure(
        "winner.recipe.yaml",
        payload,
        format_name="YAML",
    )
    if not isinstance(payload, Mapping):
        raise ValueError("winner.recipe.yaml must be a mapping")
    recipe_payload = {
        str(key): value
        for key, value in payload.items()
        if key != "metadata"
    }
    if not recipe_payload:
        raise ValueError("winner.recipe.yaml produced an empty setpoints_patch")
    return recipe_payload


def _imported_claim(
    candidate: Mapping[str, Any],
    imported_row: Mapping[str, Any] | None,
) -> dict[str, Any]:
    row = dict(imported_row or {})
    eval_spec = _json_value(row.get("eval_spec"), {})
    objectives = _objective_mapping(_json_value(row.get("objectives"), []))
    if not objectives:
        objectives = _leaderboard_objectives(candidate)
    margins = _margin_mapping(_json_value(row.get("feasibility_margins"), {}))
    return {
        "cache_key": str(row.get("cache_key") or candidate.get("cache_key") or ""),
        "candidate_id": str(row.get("candidate_id") or candidate.get("candidate_id") or ""),
        "feedstock_id": str(row.get("feedstock_id") or eval_spec.get("feedstock_id") or ""),
        "profile_id": str(row.get("profile_id") or eval_spec.get("profile_id") or ""),
        "fidelity": str(row.get("fidelity") or eval_spec.get("fidelity") or ""),
        "feasible": _optional_bool(row.get("feasible")),
        "objectives": objectives,
        "margins": margins,
        "identity": {
            "corpus_version": row.get("corpus_version") or eval_spec.get("corpus_version"),
            "code_version": eval_spec.get("code_version"),
            "allowlist_version": eval_spec.get("allowlist_version"),
            "bounds_digest": eval_spec.get("bounds_digest"),
            "data_digests": eval_spec.get("data_digests") or {},
            "backend_name": eval_spec.get("backend_name"),
            "chemistry_kernel": eval_spec.get("chemistry_kernel") or {},
        },
    }


def _local_claim(scored: ScoredResult) -> dict[str, Any]:
    spec = scored.eval_spec
    assert spec is not None
    return {
        "cache_key": scored.cache_key,
        "candidate_id": scored.candidate_id,
        "feedstock_id": spec.feedstock_id,
        "profile_id": spec.profile_id,
        "fidelity": spec.fidelity,
        "feasible": bool(scored.feasible),
        "objectives": _scored_objectives(scored),
        "margins": _scored_margins(scored),
        "identity": {
            "corpus_version": getattr(spec, "corpus_version", None) or current_corpus_version(),
            "code_version": spec.code_version,
            "recipe_schema_version": RecipeSchema().recipe_schema_version,
            "allowlist_version": spec.allowlist_version,
            "bounds_digest": spec.bounds_digest,
            "data_digests": dict(spec.data_digests),
            "backend_name": spec.backend_name,
            "chemistry_kernel": dict(spec.chemistry_kernel),
        },
    }


def _imported_identity(
    claim: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    identity = dict(claim.get("identity") or {})
    search_space = manifest.get("search_space_identity")
    if not isinstance(search_space, Mapping):
        search_space = {}
    for key in (
        "recipe_schema_version",
        "allowlist_version",
        "bounds_digest",
        "data_digests",
        "corpus_version",
    ):
        if identity.get(key) in (None, "", {}):
            identity[key] = manifest.get(key) or search_space.get(key)
    return identity


def _same_identity_epoch(
    local: Mapping[str, Any],
    imported: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    moved = [
        key
        for key in _IDENTITY_FIELDS
        if _normalize_compare(local.get(key)) != _normalize_compare(imported.get(key))
    ]
    return not moved, moved


def _claims_match(
    local: Mapping[str, Any],
    imported: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    mismatches: list[str] = []
    if local.get("feasible") != imported.get("feasible"):
        mismatches.append("feasible-mismatch")
    if not _numeric_mapping_match(
        local.get("objectives", {}),
        imported.get("objectives", {}),
    ):
        mismatches.append("objective-mismatch")
    if not _margin_claims_match(local.get("margins", {}), imported.get("margins", {})):
        mismatches.append("gate-margin-mismatch")
    return not mismatches, mismatches


def _numeric_mapping_match(local: Any, imported: Any, *, tolerance: float = 1e-9) -> bool:
    if not isinstance(local, Mapping) or not isinstance(imported, Mapping):
        return local == imported
    if set(local) != set(imported):
        return False
    for key in local:
        left = local.get(key)
        right = imported.get(key)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            if abs(float(left) - float(right)) > tolerance:
                return False
        elif left != right:
            return False
    return True


def _margin_claims_match(local: Any, imported: Any) -> bool:
    if not isinstance(local, Mapping) or not isinstance(imported, Mapping):
        return local == imported
    if set(local) != set(imported):
        return False
    for gate, left in local.items():
        right = imported.get(gate)
        if not isinstance(left, Mapping) or not isinstance(right, Mapping):
            if left != right:
                return False
            continue
        for key in ("feasible", "margin", "observed"):
            if key == "feasible":
                if left.get(key) != right.get(key):
                    return False
            elif not _numeric_mapping_match({"value": left.get(key)}, {"value": right.get(key)}):
                return False
    return True


def _scored_objectives(scored: ScoredResult) -> dict[str, float | None]:
    if scored.objectives is None:
        return {}
    return {
        value.metric: value.value
        for value in scored.objectives.values
    }


def _scored_margins(scored: ScoredResult) -> dict[str, dict[str, Any]]:
    return {
        gate: {
            "feasible": bool(margin.feasible),
            "margin": margin.margin,
            "observed": margin.observed,
        }
        for gate, margin in scored.feasibility_margins.items()
    }


def _objective_mapping(payload: Any) -> dict[str, float | None]:
    if not isinstance(payload, list):
        return {}
    values: dict[str, float | None] = {}
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        metric = item.get("metric")
        if not metric:
            continue
        values[str(metric)] = _optional_number(item.get("value"))
    return values


def _leaderboard_objectives(candidate: Mapping[str, Any]) -> dict[str, float | None]:
    values = {}
    for key, value in candidate.items():
        if key in _LEADERBOARD_META_COLUMNS or str(key).startswith("margin_"):
            continue
        number = _optional_number(value)
        if number is not None:
            values[str(key)] = number
    return values


def _margin_mapping(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return {}
    margins: dict[str, dict[str, Any]] = {}
    for gate, item in payload.items():
        if not isinstance(item, Mapping):
            continue
        margins[str(gate)] = {
            "feasible": _optional_bool(item.get("feasible")),
            "margin": _optional_number(item.get("margin")),
            "observed": _optional_number(item.get("observed")),
        }
    return margins


def _imported_rows_by_cache_key(cache_path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    row_cap = min(SQLITE_RESULTS_MAX_ROWS, MAX_IMPORTED_ELEMENT_COUNT)
    try:
        with open_untrusted_result_db(cache_path) as conn:
            _install_progress_limit(conn)
            try:
                _require_results_table(conn)
                cursor = conn.execute(
                    "SELECT * FROM results LIMIT ?",
                    (row_cap + 1,),
                )
                for index, row in enumerate(cursor, start=1):
                    if index > row_cap:
                        raise ImportSafetyCapError(
                            f"cache.sqlite results exceeds {row_cap} row cap"
                        )
                    if "cache_key" not in row.keys():
                        raise ImportBundleError("cache.sqlite results missing cache_key")
                    rows[str(row["cache_key"])] = dict(row)
            finally:
                conn.set_progress_handler(None, 0)
    except sqlite3.Error:
        raise ImportSafetyCapError(
            "cache.sqlite results query exceeded resource bounds"
        )
    return rows


def _require_results_table(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT type FROM sqlite_master WHERE name = ?",
        ("results",),
    ).fetchone()
    if row is None:
        raise ImportBundleError("cache.sqlite missing results table")
    if row["type"] != "table":
        raise ImportBundleError("cache.sqlite results must be a table")


def _install_progress_limit(conn: sqlite3.Connection) -> None:
    calls = 0

    def progress() -> int:
        nonlocal calls
        calls += 1
        return 1 if calls * SQLITE_PROGRESS_STEP > SQLITE_PROGRESS_OP_LIMIT else 0

    conn.set_progress_handler(progress, SQLITE_PROGRESS_STEP)


# Importer readers intentionally stay separate from save_bundle's trusted-path
# parsers: uploaded bundles are hostile input, so size/depth/resource guards live here.
def _read_leaderboard(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    if path.stat().st_size > CSV_CAP_BYTES:
        raise ImportSafetyCapError("leaderboard.csv exceeds 16MB cap")
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        _check_element_cap("leaderboard.csv", len(reader.fieldnames or ()), "field")
        for row_number, row in enumerate(reader, start=1):
            _check_element_cap("leaderboard.csv", row_number, "row")
            _check_element_cap("leaderboard.csv row", len(row), "field")
            rows.append(dict(row))
    return rows


def _load_profile(path: Path) -> dict[str, Any]:
    if path.stat().st_size > YAML_CAP_BYTES:
        raise ImportSafetyCapError("study.profile.yaml exceeds 1MB cap")
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    _validate_untrusted_structure(
        "study.profile.yaml",
        payload,
        format_name="YAML",
    )
    if not isinstance(payload, dict):
        raise ImportBundleError("study.profile.yaml must be a mapping")
    return payload


def _load_json_object(path: Path) -> dict[str, Any]:
    if path.stat().st_size > JSON_CAP_BYTES:
        raise ImportSafetyCapError(f"{path.name} exceeds 32MB cap")
    return _parse_json_object(path.name, path.read_bytes())


def _load_overlay(root: Path) -> dict[str, Any]:
    path = root / IMPORT_OVERLAY_NAME
    if not path.is_file():
        return {}
    return _load_json_object(path)


def _overlay_badges(overlay: Mapping[str, Any]) -> dict[str, Any]:
    badges = overlay.get("badges")
    if isinstance(badges, Mapping):
        return {**IMPORTED_BADGE, **dict(badges), "origin": "imported"}
    return dict(IMPORTED_BADGE)


def _overlay_verification(overlay: Mapping[str, Any]) -> dict[str, Any] | None:
    verification = overlay.get("verification")
    return dict(verification) if isinstance(verification, Mapping) else None


def _imported_artifacts(root: Path, runs_root: Path) -> list[dict[str, Any]]:
    artifacts = []
    for path in sorted(root.iterdir()):
        if not path.is_file():
            continue
        artifacts.append(
            {
                "name": path.name,
                "relative_path": _relative_to(path, runs_root),
                "size_bytes": path.stat().st_size,
                "modified_at": datetime.fromtimestamp(
                    path.stat().st_mtime,
                    UTC,
                ).isoformat(),
            }
        )
    return artifacts


def _assert_child_path(root: Path, target: Path) -> None:
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ImportBundleError(f"path escapes import directory: {target}") from exc


def _safe_study_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    cleaned = "".join(char if char in _SAFE_STUDY_ID_CHARS else "-" for char in text)
    return cleaned.strip(".-")[:80]


def _untrusted_sql_authorizer(action: int, *_args: Any) -> int:
    denied = {
        getattr(sqlite3, "SQLITE_ATTACH", -1),
        getattr(sqlite3, "SQLITE_DETACH", -1),
        getattr(sqlite3, "SQLITE_ALTER_TABLE", -1),
        getattr(sqlite3, "SQLITE_CREATE_INDEX", -1),
        getattr(sqlite3, "SQLITE_CREATE_TABLE", -1),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_INDEX", -1),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_TABLE", -1),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_TRIGGER", -1),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_VIEW", -1),
        getattr(sqlite3, "SQLITE_CREATE_TRIGGER", -1),
        getattr(sqlite3, "SQLITE_CREATE_VIEW", -1),
        getattr(sqlite3, "SQLITE_DELETE", -1),
        getattr(sqlite3, "SQLITE_DROP_INDEX", -1),
        getattr(sqlite3, "SQLITE_DROP_TABLE", -1),
        getattr(sqlite3, "SQLITE_DROP_TEMP_INDEX", -1),
        getattr(sqlite3, "SQLITE_DROP_TEMP_TABLE", -1),
        getattr(sqlite3, "SQLITE_DROP_TEMP_TRIGGER", -1),
        getattr(sqlite3, "SQLITE_DROP_TEMP_VIEW", -1),
        getattr(sqlite3, "SQLITE_DROP_TRIGGER", -1),
        getattr(sqlite3, "SQLITE_DROP_VIEW", -1),
        getattr(sqlite3, "SQLITE_INSERT", -1),
        getattr(sqlite3, "SQLITE_TRANSACTION", -1),
        getattr(sqlite3, "SQLITE_UPDATE", -1),
    }
    return sqlite3.SQLITE_DENY if action in denied else sqlite3.SQLITE_OK


def _json_value(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if not isinstance(value, str):
        if isinstance(value, (Mapping, list)):
            _validate_untrusted_structure(
                "embedded JSON",
                value,
                format_name="JSON",
            )
        return value
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return default
    _validate_untrusted_structure(
        "embedded JSON",
        payload,
        format_name="JSON",
    )
    return payload


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes"}:
            return True
        if normalized in {"0", "false", "no"}:
            return False
    return None


def _optional_number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _truthy(value: Any) -> bool:
    return _optional_bool(value) is True


def _normalize_compare(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _normalize_compare(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalize_compare(item) for item in value]
    return value


def _relative_to(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def _json_dump(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"


def _now() -> str:
    return datetime.now(UTC).isoformat()
