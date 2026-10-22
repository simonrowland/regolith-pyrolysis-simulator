"""Per-machine engine paths, version identity, and provenance."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import socket
import subprocess
import tomllib
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Mapping

logger = logging.getLogger(__name__)

_CONFIG_FILENAME = "engines.local.toml"
_LOCK_FILENAME = "engines.lock.toml"
_LEGACY_WARNED: set[str] = set()
_REQUIRED_ENGINE_KEYS = frozenset(
    {
        "alphamelts",
        "magemin",
        "petthermotools",
        "pysulfsat",
        "thermobar",
        "thermoengine",
        "vaporock",
    }
)

THERMOENGINE_DYLIBS = (
    "libphaseobjc.dylib",
    "libswimdew.dylib",
    "libspeciation.dylib",
)

ALPHAMELTS_BINARY_NAMES = (
    "alphamelts2",
    "alphamelts_macos",
    "alphamelts_linux",
    "alphamelts_win64.exe",
)


@dataclass(frozen=True)
class EngineIdentity:
    name: str
    version: str
    digest: str
    extra: Mapping[str, str] = field(default_factory=dict)

    def cache_version(self) -> str:
        digest = normalize_digest(self.digest)
        if not digest:
            return str(self.version).strip()
        return f"{self.version} (digest={digest})"


@dataclass(frozen=True)
class EnginePaths:
    thermoengine_dylib_dir: Path | None = None
    alphamelts_binary_path: Path | None = None
    magemin_binary_path: Path | None = None


@dataclass(frozen=True)
class EngineLocalConfig:
    paths: EnginePaths
    identities: Mapping[str, EngineIdentity]


@dataclass(frozen=True)
class EnginePlatformAsset:
    platform: str
    url: str
    pin: str
    archive_filename: str
    archive_format: str
    binary_name: str
    artifacts: tuple[str, ...]
    runtime_artifact: str
    reference_sha256: str | None = None
    reference_sha256_source: str | None = None


@dataclass(frozen=True)
class EnginePin:
    key: str
    name: str
    source_kind: str
    url: str
    pin: str
    directory: str
    version: str
    build_commands: tuple[tuple[str, ...], ...] = ()
    build_env: Mapping[str, str] = field(default_factory=dict)
    identity_extra: Mapping[str, str] = field(default_factory=dict)
    editable_subdir: str | None = None
    patches: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    runtime_artifact: str | None = None
    runtime_directory: str | None = None
    archive_filename: str | None = None
    archive_format: str | None = None
    binary_name: str | None = None
    reference_sha256: str | None = None
    reference_sha256_source: str | None = None
    platform_assets: Mapping[str, EnginePlatformAsset] = field(default_factory=dict)

    @property
    def install_dir(self) -> Path:
        return Path(self.directory) / self.pin


@dataclass(frozen=True)
class EngineLock:
    engines: Mapping[str, EnginePin]


@dataclass(frozen=True)
class EngineDrift:
    code: str
    engine: str
    path: Path
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class EngineCheckReport:
    host: str
    drifts: tuple[EngineDrift, ...]
    artifact_shas: tuple[tuple[str, str, str], ...]

    @property
    def has_errors(self) -> bool:
        return any(drift.severity == "error" for drift in self.drifts)


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def config_path() -> Path:
    return repo_root() / "engines" / _CONFIG_FILENAME


def engine_lock_path() -> Path:
    return repo_root() / "engines" / _LOCK_FILENAME


def engine_install_root() -> Path:
    return Path.home() / "repos" / "engine-bin"


def engine_install_path(
    lock: EngineLock,
    root: Path,
    engine: str,
    *,
    platform_key: str | None = None,
) -> Path:
    pin = lock.engines[str(engine).strip().lower()]
    if pin.source_kind == "artifact":
        asset = artifact_for_platform(pin, platform_key)
        return Path(root).expanduser() / pin.directory / asset.pin
    return Path(root).expanduser() / pin.install_dir


def artifact_for_platform(
    pin: EnginePin,
    platform_key: str | None = None,
) -> EnginePin | EnginePlatformAsset:
    if pin.source_kind != "artifact":
        raise ValueError(f"{pin.name} is not a release artifact")
    key = platform_key or f"{platform.system()}-{platform.machine()}"
    if key == "Darwin-arm64":
        return pin
    try:
        return pin.platform_assets[key]
    except KeyError as exc:
        raise ValueError(f"{pin.name} has no locked asset for {key}") from exc


def checkout_matches_pin(pin: EnginePin, path: Path) -> bool:
    return path.is_dir() and git_rev(path) == pin.pin


def _relative_lock_path(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    candidate = PurePosixPath(text)
    if not text or candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"engine lock {field_name} must be a safe relative path")
    return text


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"engine lock {field_name} must be an array of strings")
    return tuple(item.strip() for item in value)


def _parse_platform_asset(
    engine_key: str,
    platform_key: str,
    raw: Any,
) -> EnginePlatformAsset:
    field_prefix = f"engines.{engine_key}.platform_assets.{platform_key}"
    if not isinstance(raw, Mapping):
        raise ValueError(f"engine lock {field_prefix} must be a table")

    def required(name: str) -> str:
        value = str(raw.get(name) or "").strip()
        if not value:
            raise ValueError(f"engine lock {field_prefix}.{name} is required")
        return value

    url = required("url")
    pin = required("sha256").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", pin):
        raise ValueError(f"engine lock {field_prefix}.sha256 must be a full SHA-256")
    archive_filename = _relative_lock_path(
        raw.get("archive_filename"), f"{field_prefix}.archive_filename"
    )
    archive_format = required("archive_format")
    binary_name = _relative_lock_path(raw.get("binary_name"), f"{field_prefix}.binary_name")
    artifacts = tuple(
        _relative_lock_path(path, f"{field_prefix}.artifacts")
        for path in _string_tuple(raw.get("artifacts", []), f"{field_prefix}.artifacts")
    )
    runtime_artifact = _relative_lock_path(
        raw.get("runtime_artifact"), f"{field_prefix}.runtime_artifact"
    )
    if (
        archive_format != "zip"
        or archive_filename not in artifacts
        or runtime_artifact not in artifacts
    ):
        raise ValueError(
            f"engine lock {field_prefix} requires a ZIP, archive, and runtime artifact"
        )

    reference_sha256 = str(raw.get("reference_sha256") or "").strip().lower() or None
    if reference_sha256 is not None and not re.fullmatch(
        r"[0-9a-f]{64}", reference_sha256
    ):
        raise ValueError(
            f"engine lock {field_prefix}.reference_sha256 must be a 64-character SHA-256"
        )
    reference_source_raw = raw.get("reference_sha256_source")
    reference_source = (
        str(reference_source_raw).strip()
        if reference_source_raw not in (None, "")
        else None
    )
    if reference_sha256 is not None and reference_source != runtime_artifact:
        raise ValueError(
            f"engine lock {field_prefix}.reference_sha256_source must name "
            "the runtime artifact"
        )

    return EnginePlatformAsset(
        platform=platform_key,
        url=url,
        pin=pin,
        archive_filename=archive_filename,
        archive_format=archive_format,
        binary_name=binary_name,
        artifacts=artifacts,
        runtime_artifact=runtime_artifact,
        reference_sha256=reference_sha256,
        reference_sha256_source=reference_source,
    )


def _parse_engine_pin(key: str, raw: Any) -> EnginePin:
    if not isinstance(raw, Mapping):
        raise ValueError(f"engine lock engines.{key} must be a table")

    def required(name: str) -> str:
        value = str(raw.get(name) or "").strip()
        if not value:
            raise ValueError(f"engine lock engines.{key}.{name} is required")
        return value

    source_kind = required("source_kind")
    if source_kind not in {"git", "artifact"}:
        raise ValueError(
            f"engine lock engines.{key}.source_kind must be 'git' or 'artifact'"
        )
    url = str(raw.get("url") or "").strip()
    if source_kind == "git" and not url:
        raise ValueError(f"engine lock engines.{key}.url is required for git sources")
    pin = required("commit").lower() if source_kind == "git" else ""
    if source_kind == "git" and not re.fullmatch(r"[0-9a-f]{40}", pin):
        raise ValueError(
            f"engine lock engines.{key}.commit must be a full "
            "40-character hexadecimal pin"
        )

    build_commands_raw = raw.get("build_commands", [])
    if not isinstance(build_commands_raw, list):
        raise ValueError(f"engine lock engines.{key}.build_commands must be an array")
    build_commands: list[tuple[str, ...]] = []
    for index, command in enumerate(build_commands_raw):
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(part, str) or not part for part in command)
        ):
            raise ValueError(
                f"engine lock engines.{key}.build_commands[{index}] "
                "must be a non-empty array of strings"
            )
        build_commands.append(tuple(command))

    build_env_raw = raw.get("build_env", {})
    if not isinstance(build_env_raw, Mapping) or any(
        not isinstance(env_key, str) or not isinstance(env_value, str)
        for env_key, env_value in build_env_raw.items()
    ):
        raise ValueError(f"engine lock engines.{key}.build_env must be a string table")
    identity_extra_raw = raw.get("identity_extra", {})
    if not isinstance(identity_extra_raw, Mapping) or any(
        not isinstance(extra_key, str) or not isinstance(value, str)
        for extra_key, value in identity_extra_raw.items()
    ):
        raise ValueError(f"engine lock engines.{key}.identity_extra must be a string table")

    platform_assets: dict[str, EnginePlatformAsset] = {}
    if source_kind == "artifact":
        default_asset = _parse_platform_asset(key, "Darwin-arm64", raw)
        platform_assets_raw = raw.get("platform_assets")
        if not isinstance(platform_assets_raw, Mapping):
            raise ValueError(f"engine lock engines.{key}.platform_assets table is required")
        required_platforms = {"Darwin-x86_64", "Linux-x86_64", "Windows-AMD64"}
        missing_platforms = required_platforms - set(platform_assets_raw)
        if missing_platforms:
            raise ValueError(
                f"engine lock engines.{key} is missing platform assets: "
                + ", ".join(sorted(missing_platforms))
            )
        platform_assets = {
            str(platform_key): _parse_platform_asset(key, str(platform_key), asset_raw)
            for platform_key, asset_raw in platform_assets_raw.items()
        }
        url = default_asset.url
        pin = default_asset.pin
        artifacts = default_asset.artifacts
        runtime_artifact = default_asset.runtime_artifact
        runtime_directory = None
        archive_filename = default_asset.archive_filename
        archive_format = default_asset.archive_format
        binary_name = default_asset.binary_name
        reference_sha256 = default_asset.reference_sha256
        reference_sha256_source = default_asset.reference_sha256_source
        editable_subdir = None
    else:
        artifacts = tuple(
            _relative_lock_path(path, f"engines.{key}.artifacts")
            for path in _string_tuple(raw.get("artifacts", []), f"engines.{key}.artifacts")
        )
        editable_subdir_raw = raw.get("editable_subdir")
        editable_subdir = (
            _relative_lock_path(editable_subdir_raw, f"engines.{key}.editable_subdir")
            if editable_subdir_raw not in (None, "")
            else None
        )
        runtime_artifact_raw = raw.get("runtime_artifact")
        runtime_artifact = (
            _relative_lock_path(runtime_artifact_raw, f"engines.{key}.runtime_artifact")
            if runtime_artifact_raw not in (None, "")
            else None
        )
        runtime_directory_raw = raw.get("runtime_directory")
        runtime_directory = (
            _relative_lock_path(runtime_directory_raw, f"engines.{key}.runtime_directory")
            if runtime_directory_raw not in (None, "")
            else None
        )
        reference_sha256 = str(raw.get("reference_sha256") or "").strip().lower() or None
        if reference_sha256 is not None and not re.fullmatch(
            r"[0-9a-f]{64}", reference_sha256
        ):
            raise ValueError(
                f"engine lock engines.{key}.reference_sha256 must be a 64-character sha256"
            )
        reference_sha256_source_raw = raw.get("reference_sha256_source")
        reference_sha256_source = (
            str(reference_sha256_source_raw).strip()
            if reference_sha256_source_raw not in (None, "")
            else None
        )
        if reference_sha256 is not None and reference_sha256_source is None:
            raise ValueError(
                f"engine lock engines.{key}.reference_sha256_source is required "
                "with reference_sha256"
            )
        if reference_sha256_source not in (None, "combined"):
            reference_sha256_source = _relative_lock_path(
                reference_sha256_source, f"engines.{key}.reference_sha256_source"
            )
        archive_filename = None
        archive_format = None
        binary_name = None

    return EnginePin(
        key=key,
        name=required("name"),
        source_kind=source_kind,
        url=url,
        pin=pin,
        directory=_relative_lock_path(raw.get("directory"), f"engines.{key}.directory"),
        version=required("version"),
        build_commands=tuple(build_commands),
        build_env=dict(build_env_raw),
        identity_extra=dict(identity_extra_raw),
        editable_subdir=editable_subdir,
        patches=_string_tuple(raw.get("patches", []), f"engines.{key}.patches"),
        artifacts=artifacts,
        runtime_artifact=runtime_artifact,
        runtime_directory=runtime_directory,
        archive_filename=archive_filename,
        archive_format=archive_format,
        binary_name=binary_name,
        reference_sha256=reference_sha256,
        reference_sha256_source=reference_sha256_source,
        platform_assets=platform_assets,
    )


def load_engine_lock(path: Path | None = None) -> EngineLock:
    lock_file = path or engine_lock_path()
    if not lock_file.is_file():
        raise FileNotFoundError(f"engine lock missing: {lock_file}")
    raw = tomllib.loads(lock_file.read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1:
        raise ValueError("engine lock schema_version must be 1")
    engines_raw = raw.get("engines")
    if not isinstance(engines_raw, Mapping):
        raise ValueError("engine lock [engines] table is required")
    missing = _REQUIRED_ENGINE_KEYS - set(engines_raw)
    if missing:
        raise ValueError(
            "engine lock is missing required engines: " + ", ".join(sorted(missing))
        )
    engines = {
        str(key).strip().lower(): _parse_engine_pin(str(key).strip().lower(), entry)
        for key, entry in engines_raw.items()
    }
    return EngineLock(engines=engines)


def _identity_digest(pin: EnginePin) -> str:
    if pin.source_kind == "artifact":
        return f"sha256:{artifact_for_platform(pin).pin}"
    source = {
        "engine": pin.key,
        "url": pin.url,
        "commit": pin.pin,
        "build_commands": pin.build_commands,
        "build_env": dict(sorted(pin.build_env.items())),
        "identity_extra": dict(sorted(pin.identity_extra.items())),
        "editable_subdir": pin.editable_subdir,
        "patches": pin.patches,
    }
    encoded = json.dumps(source, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_combined(encoded)


def render_locked_config(lock: EngineLock, root: Path) -> EngineLocalConfig:
    install_root = Path(root).expanduser()
    identities = {
        key: EngineIdentity(
            name=key,
            version=pin.version,
            digest=_identity_digest(pin),
            extra=pin.identity_extra,
        )
        for key, pin in lock.engines.items()
    }
    magemin = lock.engines["magemin"]
    thermoengine = lock.engines["thermoengine"]
    alphamelts = lock.engines["alphamelts"]
    alphamelts_asset = artifact_for_platform(alphamelts)

    def installed_path(
        pin: EnginePin,
        relative: str | None,
        *,
        source_pin: str | None = None,
    ) -> Path | None:
        if relative is None:
            return None
        install_dir = (
            pin.install_dir
            if source_pin is None
            else Path(pin.directory) / source_pin
        )
        return install_root / install_dir / PurePosixPath(relative)

    paths = EnginePaths(
        thermoengine_dylib_dir=installed_path(
            thermoengine, thermoengine.runtime_directory
        ),
        alphamelts_binary_path=installed_path(
            alphamelts,
            alphamelts_asset.runtime_artifact,
            source_pin=alphamelts_asset.pin,
        ),
        magemin_binary_path=installed_path(magemin, magemin.runtime_artifact),
    )
    return EngineLocalConfig(paths=paths, identities=identities)


def write_locked_config(lock: EngineLock, root: Path) -> Path:
    return write_config(render_locked_config(lock, root))


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _is_engine_checkout(candidate: Path, pin: EnginePin) -> bool:
    if (candidate / ".git").exists():
        return True
    artifact_paths = list(pin.artifacts)
    artifact_paths.extend(
        relative
        for asset in pin.platform_assets.values()
        for relative in asset.artifacts
    )
    for relative in artifact_paths:
        if (candidate / PurePosixPath(relative)).is_file():
            return True
        artifact_name = PurePosixPath(relative).name
        try:
            if any(
                child.is_dir() and (child / artifact_name).is_file()
                for child in candidate.iterdir()
            ):
                return True
        except OSError:
            continue
    return False


def _find_extra_builds(
    lock: EngineLock,
    install_root: Path,
    repos_root: Path,
) -> dict[str, tuple[Path, ...]]:
    if not repos_root.is_dir():
        return {}
    aliases = {
        key: {key.casefold(), pin.name.casefold(), pin.directory.casefold()}
        for key, pin in lock.engines.items()
    }
    found: dict[str, list[Path]] = {key: [] for key in lock.engines}
    excluded = {".git", ".venv", "__pycache__", "node_modules"}
    for current, directories, _files in os.walk(repos_root, followlinks=False):
        current_path = Path(current)
        directories[:] = [
            name
            for name in directories
            if name not in excluded
            and not _is_within(current_path / name, install_root)
        ]
        for name in tuple(directories):
            candidate = current_path / name
            for key, pin in lock.engines.items():
                if name.casefold() not in aliases[key]:
                    continue
                if _is_within(candidate, install_root) or not _is_engine_checkout(
                    candidate, pin
                ):
                    continue
                found[key].append(candidate)
    return {key: tuple(sorted(set(paths))) for key, paths in found.items() if paths}


def _config_drifts(
    expected: EngineLocalConfig,
    actual_path: Path,
) -> list[EngineDrift]:
    if not actual_path.is_file():
        return [
            EngineDrift(
                "missing_config", "", actual_path, "engines.local.toml is missing"
            )
        ]
    try:
        actual = tomllib.loads(actual_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return [EngineDrift("invalid_config", "", actual_path, str(exc))]
    expected_raw = tomllib.loads(render_toml(expected))
    drifts: list[EngineDrift] = []
    expected_paths = expected_raw.get("paths", {})
    actual_paths = actual.get("paths", {})
    if actual_paths != expected_paths:
        drifts.append(
            EngineDrift(
                "config_paths_drift",
                "",
                actual_path,
                "[paths] differs from the lock-generated config",
            )
        )

    expected_identities = expected_raw.get("identity", {})
    actual_identities = actual.get("identity", {})
    if not isinstance(actual_identities, Mapping):
        actual_identities = {}
    for engine in sorted(set(expected_identities) | set(actual_identities)):
        expected_identity = expected_identities.get(engine, {})
        actual_identity = actual_identities.get(engine, {})
        if not isinstance(actual_identity, Mapping):
            actual_identity = {}
        if normalize_digest(str(actual_identity.get("digest") or "")) != normalize_digest(
            str(expected_identity.get("digest") or "")
        ):
            drifts.append(
                EngineDrift(
                    "config_digest_drift",
                    engine,
                    actual_path,
                    "identity digest differs from the lock-generated config",
                )
            )
        if any(
            actual_identity.get(field) != expected_identity.get(field)
            for field in ("name", "version")
        ) or {
            key: value
            for key, value in actual_identity.items()
            if key not in {"name", "version", "digest"}
        } != {
            key: value
            for key, value in expected_identity.items()
            if key not in {"name", "version", "digest"}
        }:
            drifts.append(
                EngineDrift(
                    "config_identity_drift",
                    engine,
                    actual_path,
                    "identity name or version differs from the lock-generated config",
                )
            )
    return drifts


def check_engine_install(
    lock: EngineLock,
    root: Path,
    *,
    repos_root: Path | None = None,
    local_config: Path | None = None,
    host: str | None = None,
) -> EngineCheckReport:
    install_root = Path(root).expanduser()
    repo_scan_root = repos_root or (Path.home() / "repos")
    drifts: list[EngineDrift] = []
    artifact_shas: list[tuple[str, str, str]] = []

    for key, pin in sorted(lock.engines.items()):
        engine_dir = engine_install_path(lock, install_root, key)
        artifact_pin = artifact_for_platform(pin) if pin.source_kind == "artifact" else None
        expected_artifacts = artifact_pin.artifacts if artifact_pin else pin.artifacts
        if pin.source_kind == "git":
            if not engine_dir.is_dir():
                drifts.append(
                    EngineDrift("missing_clone", key, engine_dir, "locked clone is missing")
                )
            else:
                actual_commit = git_rev(engine_dir)
                if actual_commit != pin.pin:
                    drifts.append(
                        EngineDrift(
                            "wrong_commit",
                            key,
                            engine_dir,
                            f"expected {pin.pin}, found {actual_commit}",
                        )
                    )
        for relative in expected_artifacts:
            artifact_path = engine_dir / PurePosixPath(relative)
            if not artifact_path.is_file():
                drifts.append(
                    EngineDrift(
                        "missing_artifact",
                        key,
                        artifact_path,
                        "locked artifact is missing",
                    )
                )
                continue
            digest = sha256_file(artifact_path).removeprefix("sha256:")
            artifact_shas.append((key, relative, digest))
            if (
                artifact_pin is not None
                and relative == artifact_pin.archive_filename
                and digest != artifact_pin.pin
            ):
                drifts.append(
                    EngineDrift(
                        "archive_sha256_mismatch",
                        key,
                        artifact_path,
                        f"locked {artifact_pin.pin}, found {digest}",
                    )
                )

        reference_sha256 = (
            artifact_pin.reference_sha256
            if artifact_pin is not None
            else pin.reference_sha256
        )
        if reference_sha256 is not None:
            source = (
                artifact_pin.reference_sha256_source
                if artifact_pin is not None
                else pin.reference_sha256_source
            )
            if source == "combined":
                source_paths = [engine_dir / PurePosixPath(path) for path in expected_artifacts]
                if all(path.is_file() for path in source_paths):
                    parts = [path.read_bytes() for path in source_paths]
                    parts.append((pin.pin or "").encode("utf-8"))
                    actual_reference = sha256_combined(*parts).removeprefix("sha256:")
                else:
                    actual_reference = ""
            elif source is not None:
                source_path = engine_dir / PurePosixPath(source)
                actual_reference = (
                    sha256_file(source_path).removeprefix("sha256:")
                    if source_path.is_file()
                    else ""
                )
            else:
                actual_reference = ""
            if actual_reference and actual_reference != reference_sha256:
                drifts.append(
                    EngineDrift(
                        "artifact_sha256_drift",
                        key,
                        engine_dir,
                        f"reference {reference_sha256}, found {actual_reference}",
                        severity="error" if artifact_pin is not None else "warning",
                    )
                )

    expected_config = render_locked_config(lock, install_root)
    drifts.extend(_config_drifts(expected_config, local_config or config_path()))
    extras = _find_extra_builds(lock, install_root, repo_scan_root)
    for key, paths in extras.items():
        for extra_path in paths:
            drifts.append(
                EngineDrift(
                    "extra_build",
                    key,
                    extra_path,
                    "engine checkout or build exists outside the canonical engine root",
                    severity="warning",
                )
            )

    return EngineCheckReport(
        host=host or socket.gethostname(),
        drifts=tuple(drifts),
        artifact_shas=tuple(artifact_shas),
    )


def sha256_file(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def sha256_combined(*parts: bytes) -> str:
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(part)
    return f"sha256:{hasher.hexdigest()}"


def git_rev(clone_root: Path) -> str:
    try:
        rev = subprocess.check_output(
            ["git", "-C", str(clone_root), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return rev or "unknown"
    except Exception:
        return "unknown"


def normalize_digest(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("sha256:"):
        return text
    return f"sha256:{text}"


def _parse_identity_block(
    engine_key: str,
    block: Mapping[str, Any],
) -> EngineIdentity | None:
    if not isinstance(block, Mapping):
        return None
    name = str(block.get("name") or engine_key).strip()
    version = str(block.get("version") or "").strip()
    digest = str(block.get("digest") or "").strip()
    if not version or not digest:
        return None
    extra = {
        str(key): str(value).strip()
        for key, value in block.items()
        if str(key) not in {"name", "version", "digest"} and value not in (None, "")
    }
    return EngineIdentity(name=name, version=version, digest=digest, extra=extra)


def load_config(*, required: bool = False) -> EngineLocalConfig | None:
    path = config_path()
    if not path.is_file():
        if required:
            raise FileNotFoundError(f"engine local config missing: {path}")
        return None
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    paths_block = raw.get("paths", {})
    if not isinstance(paths_block, Mapping):
        paths_block = {}

    def _optional_path(key: str) -> Path | None:
        value = paths_block.get(key)
        if value in (None, ""):
            return None
        candidate = Path(str(value)).expanduser()
        return candidate if candidate.exists() else Path(str(value)).expanduser()

    paths = EnginePaths(
        thermoengine_dylib_dir=_optional_path("thermoengine_dylib_dir"),
        alphamelts_binary_path=_optional_path("alphamelts_binary_path"),
        magemin_binary_path=_optional_path("magemin_binary_path"),
    )

    identities: dict[str, EngineIdentity] = {}
    identity_root = raw.get("identity", {})
    if isinstance(identity_root, Mapping):
        for engine_key, block in identity_root.items():
            parsed = _parse_identity_block(str(engine_key), block)
            if parsed is not None:
                identities[str(engine_key)] = parsed
    return EngineLocalConfig(paths=paths, identities=identities)


def warn_legacy_once(engine: str, message: str) -> None:
    key = str(engine).strip().lower()
    if key in _LEGACY_WARNED:
        return
    _LEGACY_WARNED.add(key)
    warnings.warn(message, stacklevel=3)
    logger.warning(message)


def identity_for(engine: str) -> EngineIdentity | None:
    config = load_config()
    if config is None:
        return None
    return config.identities.get(str(engine).strip().lower())


def cache_version_for(engine: str) -> str | None:
    identity = identity_for(engine)
    if identity is None:
        return None
    return identity.cache_version()


def is_legacy_cache_version(version: str) -> bool:
    text = str(version or "").strip()
    if not text:
        return False
    if "digest=sha256:" in text:
        return False
    if "alphaMELTS subprocess (" in text and "/" in text:
        return True
    if "; /" in text:
        return True
    return False


def find_alphamelts_binary(engine_root: Path | None = None) -> Path | None:
    config = load_config()
    if config is not None and config.paths.alphamelts_binary_path is not None:
        path = config.paths.alphamelts_binary_path.expanduser()
        if path.exists():
            return path

    root = engine_root or (repo_root() / "engines" / "alphamelts")
    if not root.exists():
        return None
    for name in ALPHAMELTS_BINARY_NAMES:
        direct = root / name
        if direct.exists():
            return direct
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        for name in ALPHAMELTS_BINARY_NAMES:
            candidate = child / name
            if candidate.exists():
                return candidate
    return None


def configured_magemin_binary_path() -> Path | None:
    config = load_config()
    if config is None or config.paths.magemin_binary_path is None:
        return None
    path = config.paths.magemin_binary_path.expanduser()
    return path if path.exists() else None


def resolve_magemin_binary_path(explicit: Any = None) -> Path | None:
    if explicit:
        path = Path(str(explicit)).expanduser()
        return path if path.exists() else None
    return configured_magemin_binary_path()


def setup_thermoengine_dylib_path() -> Path:
    config = load_config()
    dylib_dir: Path | None = None
    if config is not None and config.paths.thermoengine_dylib_dir is not None:
        dylib_dir = config.paths.thermoengine_dylib_dir.expanduser()
    if dylib_dir is None:
        home_lib = Path.home() / "lib"
        if home_lib.is_dir():
            dylib_dir = home_lib

    if dylib_dir is None or not dylib_dir.is_dir():
        raise ImportError(
            "ThermoEngine dylibs not found: configure "
            f"{config_path()} [paths].thermoengine_dylib_dir "
            "or run install-engines.py"
        )

    missing = [
        name
        for name in THERMOENGINE_DYLIBS
        if not (dylib_dir / name).is_file()
    ]
    if missing:
        raise ImportError(
            "ThermoEngine dylibs missing in "
            f"{dylib_dir}: {', '.join(missing)}. "
            "Run install-engines.py to build and stage them."
        )

    existing = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    parts = [str(dylib_dir)]
    if existing:
        parts.append(existing)
    os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(parts)
    return dylib_dir


def render_toml(config: EngineLocalConfig) -> str:
    lines = ["[paths]"]
    paths = config.paths
    if paths.thermoengine_dylib_dir is not None:
        lines.append(f'thermoengine_dylib_dir = "{paths.thermoengine_dylib_dir}"')
    if paths.alphamelts_binary_path is not None:
        lines.append(f'alphamelts_binary_path = "{paths.alphamelts_binary_path}"')
    if paths.magemin_binary_path is not None:
        lines.append(f'magemin_binary_path = "{paths.magemin_binary_path}"')

    for engine_key in sorted(config.identities):
        identity = config.identities[engine_key]
        lines.append("")
        lines.append(f"[identity.{engine_key}]")
        lines.append(f'name = "{identity.name}"')
        lines.append(f'version = "{identity.version}"')
        digest = normalize_digest(identity.digest)
        lines.append(f'digest = "{digest}"')
        for extra_key in sorted(identity.extra):
            value = identity.extra[extra_key]
            if extra_key == "db":
                lines.append(f'db = "{value}"')
            else:
                lines.append(f'{extra_key} = "{value}"')
    lines.append("")
    return "\n".join(lines)


def write_config(config: EngineLocalConfig) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_toml(config), encoding="utf-8")
    return path


__all__ = (
    "ALPHAMELTS_BINARY_NAMES",
    "EngineCheckReport",
    "EngineDrift",
    "EngineIdentity",
    "EngineLock",
    "EngineLocalConfig",
    "EnginePlatformAsset",
    "EnginePin",
    "EnginePaths",
    "THERMOENGINE_DYLIBS",
    "cache_version_for",
    "artifact_for_platform",
    "check_engine_install",
    "checkout_matches_pin",
    "config_path",
    "engine_install_path",
    "engine_install_root",
    "engine_lock_path",
    "find_alphamelts_binary",
    "git_rev",
    "identity_for",
    "is_legacy_cache_version",
    "load_engine_lock",
    "load_config",
    "render_locked_config",
    "render_toml",
    "repo_root",
    "configured_magemin_binary_path",
    "resolve_magemin_binary_path",
    "setup_thermoengine_dylib_path",
    "sha256_combined",
    "sha256_file",
    "warn_legacy_once",
    "write_config",
    "write_locked_config",
)
