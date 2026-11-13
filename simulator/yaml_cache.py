"""Process-shared cache for safely parsed YAML data files."""

from __future__ import annotations

from collections.abc import Mapping
import math
import os
from hashlib import sha256
from pathlib import Path
import pickle
import re
import stat
import tempfile
from typing import Any

import yaml


_CACHE_ENV = "REGOLITH_PARSED_YAML_CACHE_DIR"
_CACHE_ENABLED_ENV = "REGOLITH_PARSED_YAML_CACHE"
_CACHE_MAX_BYTES_ENV = "REGOLITH_PARSED_YAML_CACHE_MAX_BYTES"
_DEFAULT_CACHE_MAX_BYTES = 1536 * 1024 * 1024
# The first write triggers a sweep for short-lived processes; later full-root
# sweeps every 32 writes keep hits scan-free and bound growth between scans.
_CACHE_EVICTION_WRITE_INTERVAL = 32
_CACHE_WRITES_SINCE_EVICTION = _CACHE_EVICTION_WRITE_INTERVAL
_CACHE_FORMAT = 3
_YAML12_SCHEMA_ID = "yaml12-core-boolean-resolver-v1"
_CACHE_MISS = object()

# Use libyaml's C-backed reader/parser when it is installed. SafeLoader is the
# pure-Python fallback for environments without libyaml.
_YAML_BASE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def _yaml12_safe_loader(base_loader: type) -> type:
    """Build the same safe YAML 1.2 loader over either PyYAML implementation."""

    class YAML12SafeLoader(base_loader):
        # YAML 1.1 resolves chemistry tokens such as ``yes`` and ``NO`` as
        # booleans. YAML 1.2 core only resolves true/false. Copy the table so
        # changing this resolver never mutates PyYAML's shared loader class.
        yaml_implicit_resolvers = {
            key: [
                (tag, regexp)
                for tag, regexp in resolvers
                if tag != "tag:yaml.org,2002:bool"
            ]
            for key, resolvers in base_loader.yaml_implicit_resolvers.items()
        }

    YAML12SafeLoader.__name__ = "YAML12SafeLoader"
    YAML12SafeLoader.__qualname__ = "YAML12SafeLoader"
    YAML12SafeLoader.add_implicit_resolver(
        "tag:yaml.org,2002:bool",
        re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
        list("tTfF"),
    )
    return YAML12SafeLoader


YAML12SafeLoader = _yaml12_safe_loader(_YAML_BASE_LOADER)


def _assert_type_exact_equal(left: object, right: object, seen=None) -> None:
    """Assert that two parsed YAML values have identical types and contents."""

    assert type(left) is type(right)
    visited = seen if seen is not None else set()
    pair = (id(left), id(right))
    if pair in visited:
        return
    visited.add(pair)

    if isinstance(left, Mapping):
        assert isinstance(right, Mapping)
        assert len(left) == len(right)
        unmatched = list(right.items())
        for left_key, left_value in left.items():
            match = next(
                (
                    index
                    for index, (right_key, _right_value) in enumerate(unmatched)
                    if type(left_key) is type(right_key) and left_key == right_key
                ),
                None,
            )
            assert match is not None, f"mapping key differs: {left_key!r}"
            right_key, right_value = unmatched.pop(match)
            _assert_type_exact_equal(left_key, right_key, visited)
            _assert_type_exact_equal(left_value, right_value, visited)
        return

    if isinstance(left, (list, tuple)):
        assert isinstance(right, type(left))
        assert len(left) == len(right)
        for left_item, right_item in zip(left, right, strict=True):
            _assert_type_exact_equal(left_item, right_item, visited)
        return

    if isinstance(left, (set, frozenset)):
        assert isinstance(right, type(left))
        assert len(left) == len(right)
        unmatched = list(right)
        for left_item in left:
            match = next(
                (
                    index
                    for index, right_item in enumerate(unmatched)
                    if type(left_item) is type(right_item) and left_item == right_item
                ),
                None,
            )
            assert match is not None, f"set value differs: {left_item!r}"
            _assert_type_exact_equal(left_item, unmatched.pop(match), visited)
        return

    if isinstance(left, float) and math.isnan(left):
        assert math.isnan(right)
        return
    assert left == right


def _format_yaml_source_path(path: tuple[object, ...]) -> str:
    result = ""
    for part in path:
        if isinstance(part, int):
            result += f"[{part}]"
        else:
            result += f".{part}" if result else str(part)
    return result


def _walk_yaml_mapping_source_issues(
    node: object,
    loader: Any,
    source: str,
    path: tuple[object, ...] = (),
) -> list[tuple[str, int, str]]:
    """Find source-level flow-key loss and duplicate constructed mapping keys."""
    issues: list[tuple[str, int, str]] = []
    if isinstance(node, yaml.MappingNode):
        constructed_keys: dict[object, yaml.Mark] = {}
        merge_key_seen = False
        for key_node, value_node in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                if merge_key_seen:
                    issues.append(
                        (
                            "duplicate explicit merge key",
                            key_node.start_mark.line + 1,
                            f"{_format_yaml_source_path(path)}: <<",
                        )
                    )
                merge_key_seen = True
            else:
                constructed_key = loader.construct_object(key_node, deep=True)
                try:
                    first_mark = constructed_keys.get(constructed_key)
                except TypeError:
                    first_mark = None
                if first_mark is not None:
                    issues.append(
                        (
                            "duplicate constructed mapping key",
                            key_node.start_mark.line + 1,
                            f"{_format_yaml_source_path(path)}: {key_node.value!r} "
                            f"constructs as {constructed_key!r}; first at line "
                            f"{first_mark.line + 1}",
                        )
                    )
                else:
                    try:
                        constructed_keys[constructed_key] = key_node.start_mark
                    except TypeError:
                        pass

            if (
                node.flow_style
                and isinstance(value_node, yaml.ScalarNode)
                and value_node.tag == "tag:yaml.org,2002:null"
                and value_node.value == ""
                and ":"
                not in source[
                    key_node.end_mark.index : value_node.start_mark.index
                ]
            ):
                issues.append(
                    (
                        "flow mapping key has no value indicator",
                        key_node.start_mark.line + 1,
                        f"{_format_yaml_source_path(path)}: {key_node.value!r}",
                    )
                )

            issues.extend(
                _walk_yaml_mapping_source_issues(
                    value_node,
                    loader,
                    source,
                    path + (key_node.value,),
                )
            )
    elif isinstance(node, yaml.SequenceNode):
        for index, value_node in enumerate(node.value):
            issues.extend(
                _walk_yaml_mapping_source_issues(
                    value_node,
                    loader,
                    source,
                    path + (index,),
                )
            )
    return issues


def yaml_mapping_source_issues(source: str) -> list[tuple[str, int, str]]:
    """Return the shared source guard findings for one YAML document."""
    loader = YAML12SafeLoader(source)
    try:
        source_tree = loader.get_single_node()
        if source_tree is None:
            return []
        issues = _walk_yaml_mapping_source_issues(source_tree, loader, source)
        loader.construct_document(source_tree)
        return issues
    finally:
        loader.dispose()


def load_cached_safe_yaml(
    payload: str | bytes,
    *,
    content_sha256: str | None = None,
) -> Any:
    """Load YAML, reusing an exact-content typed cache across processes.

    The pickle value preserves the safe loader's Python types and YAML aliases.
    Set ``REGOLITH_PARSED_YAML_CACHE=off`` to bypass the process-shared cache;
    ``REGOLITH_PARSED_YAML_CACHE_DIR`` selects its private root, and
    ``REGOLITH_PARSED_YAML_CACHE_MAX_BYTES`` sets its byte cap (``off`` removes
    the cap without changing the cache enable switch).
    """

    raw = payload.encode("utf-8") if isinstance(payload, str) else payload
    digest = sha256(raw).hexdigest()
    if content_sha256 is not None and content_sha256 != digest:
        raise ValueError("content_sha256 does not match YAML payload")
    if not _cache_enabled():
        return yaml.load(payload, Loader=YAML12SafeLoader)

    max_bytes = _cache_max_bytes()
    loader_identity = _loader_identity()
    cache_path = _cache_path(digest, loader_identity)
    cached = _read_cache(cache_path, digest, loader_identity)
    if cached is not _CACHE_MISS:
        return cached

    parsed = yaml.load(payload, Loader=YAML12SafeLoader)
    if _write_cache(cache_path, digest, loader_identity, parsed):
        _evict_after_write(cache_path, max_bytes)
    return parsed


def _cache_enabled() -> bool:
    value = os.environ.get(_CACHE_ENABLED_ENV, "").strip().casefold()
    return value not in {"0", "false", "no", "off"}


def _cache_max_bytes() -> int | None:
    value = os.environ.get(_CACHE_MAX_BYTES_ENV)
    if value is None:
        return _DEFAULT_CACHE_MAX_BYTES
    normalized = value.strip().casefold()
    if normalized == "off":
        return None
    try:
        max_bytes = int(normalized, 10)
    except ValueError as exc:
        raise ValueError(
            f"{_CACHE_MAX_BYTES_ENV} must be a non-negative integer or 'off'"
        ) from exc
    if max_bytes < 0:
        raise ValueError(
            f"{_CACHE_MAX_BYTES_ENV} must be a non-negative integer or 'off'"
        )
    return max_bytes or None


def _loader_identity() -> str:
    base_identity = f"{_YAML_BASE_LOADER.__module__}.{_YAML_BASE_LOADER.__qualname__}"
    return f"pyyaml={yaml.__version__};base={base_identity};schema={_YAML12_SCHEMA_ID}"


def _cache_path(content_sha256: str, loader_identity: str | None = None) -> Path:
    root = _cache_root()
    loader_version = re.sub(r"[^A-Za-z0-9_.-]", "_", str(yaml.__version__))
    identity = loader_identity or _loader_identity()
    loader_token = sha256(identity.encode("utf-8")).hexdigest()
    return (
        root
        / f"pyyaml-{loader_version}"
        / f"yaml12-{loader_token}"
        / f"v{_CACHE_FORMAT}"
        / f"{content_sha256}.pickle"
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


def _read_cache(path: Path, content_sha256: str, loader_identity: str) -> Any:
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_mode & 0o077
            or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
        ):
            return _CACHE_MISS
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            envelope = pickle.load(handle)
    except Exception:
        return _CACHE_MISS
    if not isinstance(envelope, dict):
        return _CACHE_MISS
    if envelope.get("cache_format") != _CACHE_FORMAT:
        return _CACHE_MISS
    if envelope.get("content_sha256") != content_sha256:
        return _CACHE_MISS
    if envelope.get("loader_identity") != loader_identity:
        return _CACHE_MISS
    if "value" not in envelope:
        return _CACHE_MISS
    try:
        os.utime(path, None)
    except OSError:
        # A concurrent evictor may unlink the entry after it was read.
        pass
    return envelope["value"]


def _write_cache(
    path: Path, content_sha256: str, loader_identity: str, value: Any
) -> bool:
    envelope = {
        "cache_format": _CACHE_FORMAT,
        "content_sha256": content_sha256,
        "loader_identity": loader_identity,
        "value": value,
    }
    temp_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.stem}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            pickle.dump(envelope, handle, protocol=pickle.HIGHEST_PROTOCOL)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        return True
    except Exception:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass
        return False


def _evict_after_write(path: Path, max_bytes: int | None) -> None:
    global _CACHE_WRITES_SINCE_EVICTION
    _CACHE_WRITES_SINCE_EVICTION += 1
    if _CACHE_WRITES_SINCE_EVICTION < _CACHE_EVICTION_WRITE_INTERVAL:
        return
    _CACHE_WRITES_SINCE_EVICTION = 0

    # The cache path is root/version/loader-token/format/file.pickle.
    _evict_cache(
        root=path.parents[3],
        just_written=path,
        max_bytes=max_bytes,
    )


def _evict_cache(
    *,
    root: Path,
    just_written: Path,
    max_bytes: int | None,
) -> None:
    if max_bytes is None:
        return

    entries: list[tuple[int, Path, int]] = []
    try:
        for path in root.rglob("*.pickle"):
            try:
                metadata = path.lstat()
            except OSError:
                continue
            if stat.S_ISREG(metadata.st_mode):
                entries.append((metadata.st_mtime_ns, path, metadata.st_size))
    except OSError:
        return

    total_bytes = sum(size for _mtime_ns, _path, size in entries)
    target_bytes = max_bytes * 9 // 10
    if total_bytes <= target_bytes:
        return

    entries.sort(key=lambda entry: (entry[0], str(entry[1])))
    for _mtime_ns, path, size in entries:
        if total_bytes <= target_bytes:
            break
        if path == just_written:
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            # Another process removed the entry after the directory scan.
            total_bytes -= size
        except OSError:
            continue
        else:
            total_bytes -= size
