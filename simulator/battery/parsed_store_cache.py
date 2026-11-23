"""Reuse a parsed migrated store while its content digest still matches.

The store identity is ``store_validation._migrated_store_content_sha256``.
``scripts/check_store_freshness.py`` is a commit-order tripwire, and
``observation_store_summary.yaml`` is shard size plus observation-id counts.
Neither is a content digest, so neither can key this snapshot.

``extracts/*.yaml`` review status is not part of that digest. The scorer
reads it on every load and does not store it here.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import pickle
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from simulator.battery.migrate import (
    REPO_ROOT,
    load_migrated_benches,
    load_migrated_context,
    load_migrated_store,
    load_observation_origins,
)
from simulator.battery.store_validation import (
    _migrated_store_content_sha256,
    hash_relative_files,
)
from simulator.battery import validate as validate_mod

_CACHE_SWITCH = "REGOLITH_PARSED_STORE_CACHE"
_CACHE_DIR_ENV = "REGOLITH_PARSED_STORE_CACHE_DIR"
SNAPSHOT_SCHEMA = 1
REUSE_SCHEMA = 1
_SNAPSHOT_KEEP = 2
_REUSE_KEEP = 8

# Importing the loader executes these modules and the simulator modules they
# import. The snapshot key hashes that closure so a change to a module the
# loader or validator runs cannot keep a stale pickle. Python's major.minor
# is in the token because a pickle written by one interpreter is not a
# record of another.
PARSED_STORE_ENTRY_FILES = (
    "simulator/battery/migrate.py",
    "simulator/battery/validate.py",
    "simulator/battery/parsed_store_cache.py",
)

# The YAML cache is specified to return the same object a fresh YAML 1.2
# parse would. Its switch and directory cannot change the typed records.
HARMLESS_LOADER_SETTINGS = {
    "REGOLITH_PARSED_YAML_CACHE": "type-exact with a fresh YAML 1.2 parse",
    "REGOLITH_PARSED_YAML_CACHE_DIR": "type-exact with a fresh YAML 1.2 parse",
    "REGOLITH_PARSED_YAML_CACHE_MAX_BYTES": "type-exact with a fresh YAML 1.2 parse",
    _CACHE_SWITCH: "selects whether this snapshot is read; it does not change the records",
    _CACHE_DIR_ENV: "selects where the snapshot is stored; it does not change the records",
    "XDG_CACHE_HOME": "selects the cache directory; it does not change the records",
}

_ACTIVE: dict[int, "ParsedScoreInputs"] = {}


@dataclass(frozen=True)
class ParsedScoreInputs:
    """Unfiltered records ``load_score_context`` builds from the store digest."""

    store_content_sha256: str
    builder_token: str
    works: dict[str, Any]
    experiments: dict[str, Any]
    observations: dict[str, Any]
    benches: dict[str, Any]
    origins: dict[str, str]
    hard_issues_by_observation: dict[str, tuple[Any, ...]]


def cache_enabled() -> bool:
    value = os.environ.get(_CACHE_SWITCH, "").strip().casefold()
    return value not in {"0", "false", "no", "off"}


def load_unfiltered_score_inputs(root: Path | None = None) -> ParsedScoreInputs:
    """Return the full parsed store, reusing one pickle while the digest matches.

    Validation is stored with the pickle. ``validate_corpus`` with
    ``residuals=None`` is a pure function of the migrated works, experiments,
    observations, benches, and context rows. Those records are a pure function
    of the bytes ``_migrated_store_content_sha256`` hashes and of the builder
    modules in the cache key. It does not read the clock, the hostname, the
    residual ledger, or ``extracts/*.yaml`` review status. Serving the stored
    hard-issue map is the same report. A digest or builder mismatch rebuilds
    and validates again. Filtered loads and a test double that replaces
    ``load_migrated_store`` do not call this function.

    A fixture root does not use the persistent cache. Writing it into the
    user cache would evict the production snapshot, and the next fixture load
    would skip ``validate_corpus``. Set ``REGOLITH_PARSED_STORE_CACHE_DIR``
    to opt a fixture in.
    """

    root = root or REPO_ROOT
    if not _snapshot_io_enabled(root):
        return _build_unfiltered(root, "", "")
    try:
        digest = _migrated_store_content_sha256(root)
        token = builder_token()
        cached = _read_snapshot(token, digest)
    except (OSError, ValueError):
        cached = None
        digest = _migrated_store_content_sha256(root)
        token = builder_token()
    if cached is not None:
        _remember(cached)
        return cached
    parsed = _build_unfiltered(root, digest, token)
    _remember(parsed)
    try:
        _write_snapshot(parsed)
    except OSError:
        pass
    return parsed


def _snapshot_io_enabled(root: Path) -> bool:
    """True when this load may read or write the persistent snapshot."""

    if not cache_enabled():
        return False
    if os.environ.get(_CACHE_DIR_ENV):
        return True
    try:
        return root.resolve() == REPO_ROOT.resolve()
    except OSError:
        return False


def active_parsed_store(context: object) -> ParsedScoreInputs | None:
    """The snapshot whose mappings this context still holds, if any.

    A synthetic context, a filtered load, or a replaced loader does not hold
    those objects, so a later pure function of the on-disk store must not
    reuse a result computed for them.
    """

    if not cache_enabled():
        return None
    observations = getattr(context, "observations", None)
    parsed = _ACTIVE.get(id(observations))
    if parsed is None:
        return None
    if (
        getattr(context, "works", None) is not parsed.works
        or getattr(context, "experiments", None) is not parsed.experiments
        or observations is not parsed.observations
        or getattr(context, "origins", None) is not parsed.origins
        or getattr(context, "benches", None) is not parsed.benches
    ):
        return None
    return parsed


def code_token(entries: Sequence[str], *, schema: int) -> str:
    """Hash the simulator source importing ``entries`` would execute.

    The source lives in this checkout (``REPO_ROOT``), not under a fixture
    store root. A tmp store still invalidates when the loader code changes.
    The token is cached for this process: the interpreter has already loaded
    those modules, and a source edit is visible only to a new process.
    """

    return _code_token_cached(tuple(entries), schema)


@lru_cache(maxsize=8)
def _code_token_cached(entries: tuple[str, ...], schema: int) -> str:
    files = reachable_source_files(REPO_ROOT, entries)
    inner = hash_relative_files(
        REPO_ROOT, [REPO_ROOT / relative for relative in files]
    )
    tag = (
        f"schema={schema};python={sys.version_info[0]}.{sys.version_info[1]};"
        f"entries={','.join(entries)};files={inner}"
    )
    return hashlib.sha256(tag.encode("utf-8")).hexdigest()


def builder_token() -> str:
    return code_token(PARSED_STORE_ENTRY_FILES, schema=SNAPSHOT_SCHEMA)


def canonical_material(payload: object) -> str | None:
    """Stable JSON for a reuse key. None when ``payload`` is not canonical.

    ``Decimal`` is rendered with ``format(value, "f")`` so a band line does
    not change key because of ``str(Decimal)`` exponent form. Any other
    non-JSON value refuses the key instead of being guessed.
    """

    try:
        return json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=_material_default,
        )
    except (TypeError, ValueError):
        return None


def _material_default(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(type(value).__name__)


@lru_cache(maxsize=8)
def _reachable_cached(root_text: str, entries: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(_walk_sources(Path(root_text), entries))


def reachable_source_files(root: Path, entries: Sequence[str]) -> tuple[str, ...]:
    return _reachable_cached(str(root), tuple(entries))


def read_reuse_blob(kind: str, key: str) -> tuple[bytes, int] | None:
    if not cache_enabled():
        return None
    path = _reuse_path(kind, key)
    if path is None:
        return None
    try:
        with path.open("rb") as handle:
            envelope = pickle.load(handle)
    except (OSError, pickle.UnpicklingError, EOFError, AttributeError, ImportError, ValueError):
        return None
    if not isinstance(envelope, dict):
        return None
    if envelope.get("schema") != REUSE_SCHEMA or envelope.get("key") != key:
        return None
    body = envelope.get("body")
    count = envelope.get("count")
    if not isinstance(body, bytes) or not isinstance(count, int):
        return None
    return body, count


def write_reuse_blob(kind: str, key: str, body: bytes, count: int) -> None:
    if not cache_enabled():
        return
    path = _reuse_path(kind, key)
    if path is None:
        return
    _write_pickle(
        path,
        {"schema": REUSE_SCHEMA, "key": key, "body": body, "count": count},
    )
    _evict(path.parent, keep=_REUSE_KEEP)


def read_reuse_pickle(kind: str, key: str) -> object | None:
    if not cache_enabled():
        return None
    path = _reuse_path(kind, key)
    if path is None:
        return None
    try:
        with path.open("rb") as handle:
            envelope = pickle.load(handle)
    except (OSError, pickle.UnpicklingError, EOFError, AttributeError, ImportError, ValueError):
        return None
    if not isinstance(envelope, dict):
        return None
    if envelope.get("schema") != REUSE_SCHEMA or envelope.get("key") != key:
        return None
    if "value" not in envelope:
        return None
    return envelope["value"]


def write_reuse_pickle(kind: str, key: str, value: object) -> None:
    if not cache_enabled():
        return
    path = _reuse_path(kind, key)
    if path is None:
        return
    _write_pickle(
        path,
        {"schema": REUSE_SCHEMA, "key": key, "value": value},
    )
    _evict(path.parent, keep=_REUSE_KEEP)


def restore_bytes(path: Path, body: bytes) -> None:
    """Publish ``body`` at ``path`` the same way the comparison writer replaces."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}-",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _build_unfiltered(root: Path, digest: str, token: str) -> ParsedScoreInputs:
    works, experiments, observations = load_migrated_store(root)
    benches = load_migrated_benches(root)
    # Context rows are an input of validate_corpus and are not stored on the
    # score context. They come from the same work YAML the store digest hashes.
    report = validate_mod.validate_corpus(
        works,
        experiments,
        observations,
        benches=benches,
        context_rows=load_migrated_context(root),
    )
    return ParsedScoreInputs(
        store_content_sha256=digest,
        builder_token=token,
        works=works,
        experiments=experiments,
        observations=observations,
        benches=benches,
        origins=load_observation_origins(root),
        hard_issues_by_observation={
            observation_id: tuple(issues)
            for observation_id, issues in report.hard_issues_by_observation.items()
        },
    )


def _remember(parsed: ParsedScoreInputs) -> None:
    # One unfiltered snapshot per process. A later load must not keep every
    # previous unpickle alive; the score uses the context it just built.
    _ACTIVE.clear()
    _ACTIVE[id(parsed.observations)] = parsed


def _read_snapshot(token: str, digest: str) -> ParsedScoreInputs | None:
    path = _snapshot_path(token, digest)
    if path is None or not path.is_file():
        return None
    try:
        with path.open("rb") as handle:
            envelope = pickle.load(handle)
    except (OSError, pickle.UnpicklingError, EOFError, AttributeError, ImportError, ValueError):
        return None
    if not isinstance(envelope, Mapping):
        return None
    if envelope.get("schema") != SNAPSHOT_SCHEMA:
        return None
    if envelope.get("store_content_sha256") != digest:
        return None
    if envelope.get("builder_token") != token:
        return None
    required = (
        "works",
        "experiments",
        "observations",
        "benches",
        "origins",
        "hard_issues_by_observation",
    )
    if any(name not in envelope or not isinstance(envelope[name], dict) for name in required):
        return None
    return ParsedScoreInputs(
        store_content_sha256=digest,
        builder_token=token,
        works=envelope["works"],
        experiments=envelope["experiments"],
        observations=envelope["observations"],
        benches=envelope["benches"],
        origins=envelope["origins"],
        hard_issues_by_observation=envelope["hard_issues_by_observation"],
    )


def _write_snapshot(parsed: ParsedScoreInputs) -> None:
    path = _snapshot_path(parsed.builder_token, parsed.store_content_sha256)
    if path is None:
        return
    _write_pickle(
        path,
        {
            "schema": SNAPSHOT_SCHEMA,
            "store_content_sha256": parsed.store_content_sha256,
            "builder_token": parsed.builder_token,
            "works": parsed.works,
            "experiments": parsed.experiments,
            "observations": parsed.observations,
            "benches": parsed.benches,
            "origins": parsed.origins,
            "hard_issues_by_observation": parsed.hard_issues_by_observation,
        },
    )
    version_dir = path.parents[2]
    _evict(version_dir / "snapshots", keep=_SNAPSHOT_KEEP)
    _drop_other_versions(version_dir.parent, version_dir.name)


def _snapshot_path(token: str, digest: str) -> Path | None:
    root = _cache_directory()
    if root is None:
        return None
    if not _token_ok(token) or not _token_ok(digest):
        return None
    return root / f"v{SNAPSHOT_SCHEMA}" / "snapshots" / token / f"{digest}.pickle"


def _reuse_path(kind: str, key: str) -> Path | None:
    root = _cache_directory()
    if root is None or not _token_ok(key) or not kind.isidentifier():
        return None
    return root / f"v{REUSE_SCHEMA}" / kind / f"{key}.pickle"


def _token_ok(value: str) -> bool:
    return bool(value) and all(character in "0123456789abcdef" for character in value)


def _cache_directory() -> Path | None:
    from simulator.yaml_cache import _ensure_private_directory, _private_descendant

    override = os.environ.get(_CACHE_DIR_ENV)
    if override:
        # An explicit directory that is not private must not fall through to
        # the user cache. Tests ask for a tmp dir and should fail closed.
        try:
            candidate = Path(override).expanduser()
        except (OSError, RuntimeError, ValueError):
            return None
        if _ensure_private_directory(candidate):
            return candidate
        return None
    parts = ("regolith-pyrolysis-simulator", "parsed-store")
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache:
        try:
            xdg_root = Path(xdg_cache).expanduser().resolve()
            if _ensure_private_directory(xdg_root):
                candidate = _private_descendant(xdg_root, *parts)
                if candidate is not None:
                    return candidate
        except (OSError, RuntimeError, ValueError):
            pass
    try:
        home = Path.home().resolve()
        if sys.platform == "darwin":
            return _private_descendant(home, "Library", "Caches", *parts)
        return _private_descendant(home, ".cache", *parts)
    except (OSError, RuntimeError, ValueError):
        return None


def _write_pickle(path: Path, envelope: dict[str, object]) -> None:
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.stem}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            pickle.dump(envelope, handle, protocol=pickle.HIGHEST_PROTOCOL)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    except (OSError, pickle.PicklingError, TypeError, AttributeError):
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return -1


def _evict(directory: Path, *, keep: int) -> None:
    """Drop stale pickles. One full typed store is large enough to fill a disk."""

    try:
        files = [path for path in directory.rglob("*.pickle") if path.is_file()]
    except OSError:
        return
    if len(files) <= keep:
        return
    files.sort(key=lambda path: (_mtime_ns(path), path.as_posix()))
    for path in files[:-keep]:
        try:
            path.unlink()
        except OSError:
            continue


def _drop_other_versions(cache_root: Path, current: str) -> None:
    try:
        children = list(cache_root.iterdir())
    except OSError:
        return
    for child in children:
        if child.name == current or not child.name.startswith("v"):
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)


def _walk_sources(root: Path, entries: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    queue = [entry for entry in entries if entry.endswith(".py")]
    while queue:
        relative = queue.pop()
        if relative in seen:
            continue
        path = root / relative
        if not path.is_file():
            continue
        seen.add(relative)
        module = _module_name(relative)
        try:
            source = path.read_text(encoding="utf-8")
            targets = _import_modules(source, module)
        except (OSError, SyntaxError, UnicodeError):
            continue
        for target in targets:
            for candidate in _module_files(root, target):
                if candidate not in seen:
                    queue.append(candidate)
            for init in _ancestor_inits(root, target):
                if init not in seen:
                    queue.append(init)
    return sorted(seen)


def _module_name(relative: str) -> str:
    path = Path(relative)
    if path.name == "__init__.py":
        path = path.parent
    return ".".join(path.with_suffix("").parts)


def _import_modules(source: str, current: str) -> set[str]:
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "simulator" or alias.name.startswith("simulator."):
                    modules.add(alias.name)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            package = current.split(".")[: -node.level]
            if node.module:
                package = [*package, *node.module.split(".")]
            if package and (package[0] == "simulator"):
                modules.add(".".join(package))
                for alias in node.names:
                    modules.add(".".join((*package, alias.name)))
            continue
        if node.module and (
            node.module == "simulator" or node.module.startswith("simulator.")
        ):
            modules.add(node.module)
            for alias in node.names:
                modules.add(f"{node.module}.{alias.name}")
    return modules


def _module_files(root: Path, module: str) -> list[str]:
    if module == "simulator" or not module.startswith("simulator"):
        return []
    relative = Path(*module.split(".")).with_suffix(".py")
    found: list[str] = []
    if (root / relative).is_file():
        found.append(relative.as_posix())
    package = Path(*module.split(".")) / "__init__.py"
    if (root / package).is_file():
        found.append(package.as_posix())
    return found


def _ancestor_inits(root: Path, module: str) -> list[str]:
    parts = module.split(".")
    found: list[str] = []
    for end in range(1, len(parts)):
        init = Path(*parts[:end]) / "__init__.py"
        if (root / init).is_file():
            found.append(init.as_posix())
    return found
