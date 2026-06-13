"""Per-machine engine paths and portable digest identity (engines.local.toml)."""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import tomllib
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)

_CONFIG_FILENAME = "engines.local.toml"
_LEGACY_WARNED: set[str] = set()

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


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def config_path() -> Path:
    return repo_root() / "engines" / _CONFIG_FILENAME


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
    "EngineIdentity",
    "EngineLocalConfig",
    "EnginePaths",
    "THERMOENGINE_DYLIBS",
    "cache_version_for",
    "config_path",
    "find_alphamelts_binary",
    "git_rev",
    "identity_for",
    "is_legacy_cache_version",
    "load_config",
    "render_toml",
    "repo_root",
    "configured_magemin_binary_path",
    "resolve_magemin_binary_path",
    "setup_thermoengine_dylib_path",
    "sha256_combined",
    "sha256_file",
    "warn_legacy_once",
    "write_config",
)