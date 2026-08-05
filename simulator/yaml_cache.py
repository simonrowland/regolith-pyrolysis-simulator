"""Process-shared cache for safely parsed YAML data files."""

from __future__ import annotations

import json
import math
import os
from hashlib import sha256
from pathlib import Path
import re
import stat
import tempfile
from typing import Any

import yaml


_CACHE_ENV = "REGOLITH_PARSED_YAML_CACHE_DIR"
_CACHE_FORMAT = 1
_CACHE_MISS = object()


def load_cached_safe_yaml(
    payload: str | bytes,
    *,
    content_sha256: str | None = None,
) -> Any:
    """Load YAML, reusing an exact-content JSON cache across processes.

    The cache is only written when JSON can preserve every parsed type and no
    YAML alias shares a mutable container. Unsupported trees keep the existing
    loader behavior and simply skip process-shared reuse.
    """

    raw = payload.encode("utf-8") if isinstance(payload, str) else payload
    digest = sha256(raw).hexdigest()
    if content_sha256 is not None and content_sha256 != digest:
        raise ValueError("content_sha256 does not match YAML payload")
    cache_path = _cache_path(digest)
    cached = _read_cache(cache_path, digest)
    if cached is not _CACHE_MISS:
        return cached

    parsed = yaml.safe_load(payload)
    if _json_roundtrip_safe(parsed):
        _write_cache(cache_path, digest, parsed)
    return parsed


def _cache_path(content_sha256: str) -> Path:
    root = _cache_root()
    loader_version = re.sub(r"[^A-Za-z0-9_.-]", "_", str(yaml.__version__))
    return (
        root
        / f"pyyaml-{loader_version}"
        / f"v{_CACHE_FORMAT}"
        / f"{content_sha256}.json"
    )


def _cache_root() -> Path:
    override = os.environ.get(_CACHE_ENV)
    if override:
        candidate = Path(override)
        if _ensure_private_directory(candidate):
            return candidate
    else:
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        if xdg_cache:
            xdg_root = Path(xdg_cache)
            if _ensure_private_directory(xdg_root):
                candidate = _private_descendant(
                    xdg_root,
                    "regolith-pyrolysis-simulator",
                    "parsed-yaml",
                )
                if candidate is not None:
                    return candidate
        else:
            user_token = str(os.getuid()) if hasattr(os, "getuid") else "user"
            candidate = _private_descendant(
                Path(tempfile.gettempdir()),
                f"regolith-pyrolysis-simulator-{user_token}",
                "parsed-yaml",
            )
            if candidate is not None:
                return candidate

    fallback = Path(tempfile.mkdtemp(prefix="regolith-parsed-yaml-"))
    os.environ[_CACHE_ENV] = str(fallback)
    return fallback


def _private_descendant(base: Path, *parts: str) -> Path | None:
    current = base
    for part in parts:
        current /= part
        if not _ensure_private_directory(current):
            return None
    return current


def _ensure_private_directory(path: Path) -> bool:
    if not _safe_parent_directory(path.parent):
        return False
    try:
        path.mkdir(mode=0o700, exist_ok=True)
        metadata = path.lstat()
    except OSError:
        return False
    return _private_directory_metadata(metadata)


def _safe_parent_directory(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    if _private_directory_metadata(metadata):
        return True
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        return False
    if not metadata.st_mode & stat.S_ISVTX:
        return False
    return not hasattr(os, "getuid") or metadata.st_uid in {0, os.getuid()}


def _private_directory_metadata(metadata: os.stat_result) -> bool:
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        return False
    if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
        return False
    return metadata.st_mode & 0o022 == 0


def _read_cache(path: Path, content_sha256: str) -> Any:
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _CACHE_MISS
    if not isinstance(envelope, dict):
        return _CACHE_MISS
    if envelope.get("cache_format") != _CACHE_FORMAT:
        return _CACHE_MISS
    if envelope.get("content_sha256") != content_sha256:
        return _CACHE_MISS
    if "value" not in envelope:
        return _CACHE_MISS
    value = envelope.get("value")
    return value if _json_roundtrip_safe(value) else _CACHE_MISS


def _write_cache(path: Path, content_sha256: str, value: Any) -> None:
    envelope = {
        "cache_format": _CACHE_FORMAT,
        "content_sha256": content_sha256,
        "value": value,
    }
    try:
        encoded = json.dumps(
            envelope,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        path.parent.mkdir(parents=True, exist_ok=True)
    except (OSError, TypeError, ValueError):
        return

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.stem}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(encoded)
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
    except OSError:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass


def _json_roundtrip_safe(value: Any, seen: set[int] | None = None) -> bool:
    if value is None or type(value) in {str, bool, int}:
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) not in {dict, list}:
        return False

    visited = seen if seen is not None else set()
    identity = id(value)
    if identity in visited:
        return False
    visited.add(identity)
    if type(value) is list:
        return all(_json_roundtrip_safe(item, visited) for item in value)
    return all(
        type(key) is str and _json_roundtrip_safe(item, visited)
        for key, item in value.items()
    )
