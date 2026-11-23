"""One pickle of the score prologue, reused while its content key matches.

The prologue is the unfiltered migrated store, its ``validate_corpus``
hard-issue map, the source-band catalog, and the compilation JSONL.
Those products are deterministic in the inputs named below. A warm score
serves the pickle. Any other key rebuilds all of them.

The store component is ``store_validation._migrated_store_content_sha256``.
``scripts/check_store_freshness.py`` is a commit-order tripwire, and the
observation-store summary is shard size plus observation-id counts.
Neither is a content digest, so neither is this key.

A git tree hash of HEAD is not the code component. It misses an
uncommitted edit of a module the prologue runs, and that edit would
serve a stale residual. The key hashes the worktree bytes.

``extracts/*.yaml`` review status is not an input. The scorer reads it
on every load and does not store it here.
"""

from __future__ import annotations

import copy
import hashlib
import os
import pickle
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

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
PROLOGUE_SCHEMA = 1
# Two full prologues. A third run drops the oldest. The failure this
# prevents is an unbounded set of multi-GB pickles filling the disk.
_KEEP = 2

# Checkout-relative inputs the prologue opens, besides the migrated store.
# The store digest already covers works, extracts-v2, and observations-v2.
# Each entry is the path the named reader opens; a directory means every
# file under it. Hashing the declared path is what stops a warm score from
# serving rows built from a previous copy of that file.
PROLOGUE_DATA_INPUTS: tuple[str, ...] = (
    # generators/janaf.py reads tables/{element}-*.yaml;
    # reference_data/janaf.py reads source/sidecar.yaml and manifest.yaml.
    "data/literature/compilations/janaf",
    # reference_data/nasa_glenn.py load_all_record_documents reads
    # records/NG-*.json and manifest.yaml.
    "data/literature/compilations/nasa-glenn",
    # diagnostic_helpers/gibbs_battery.py cea_polynomial reads this extract
    # for score_cea_point.
    "data/literature/extracts/nasa-cea-thermo.yaml",
    # generators/compilation_thermo_fits.py load_sidecar reads this file
    # for the NASA-9 fit rows.
    "data/literature/compilation-thermo-fits.generated.json",
    # score._read_decision_band_ratchet reads this file for the catalog.
    # It is not part of the migrated-store digest.
    "data/battery/decision-band-ratchet.yaml",
)

_ACTIVE: dict[int, "ParsedScoreInputs"] = {}
_SESSION: "_Session | None" = None


@dataclass(frozen=True)
class ParsedScoreInputs:
    """Unfiltered records and the hard-issue map stored in the prologue."""

    works: dict[str, Any]
    experiments: dict[str, Any]
    observations: dict[str, Any]
    benches: dict[str, Any]
    origins: dict[str, str]
    hard_issues_by_observation: dict[str, tuple[Any, ...]]


@dataclass
class _Session:
    root: Path
    engines: tuple[str, ...]
    invoke_pure_phase: bool
    work_id: str | None
    rail: str | None
    species_arg: str | None
    entries_material: str | None
    key: str | None = None
    hit: "_Prologue | None" = None
    parsed: ParsedScoreInputs | None = None
    source_band: tuple[Any, ...] | None = None
    compilation: tuple[bytes, int] | None = None


@dataclass(frozen=True)
class _Prologue:
    parsed: ParsedScoreInputs
    source_band: tuple[Any, ...]
    compilation_body: bytes
    compilation_count: int


def cache_enabled() -> bool:
    value = os.environ.get(_CACHE_SWITCH, "").strip().casefold()
    return value not in {"0", "false", "no", "off"}


def canonical_material(payload: object) -> str | None:
    """Stable JSON for a key component. None when ``payload`` is not canonical.

    ``Decimal`` is rendered with ``format(value, "f")`` so a band line does
    not change key because of ``str(Decimal)`` exponent form. Any other
    non-JSON value refuses the key instead of being guessed.
    """

    try:
        import json

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


def runtime_token() -> str:
    """Interpreter and YAML loader. A pickle is not portable across either.

    ``yaml_cache._loader_identity`` is the owner of the PyYAML version and
    of CSafeLoader versus SafeLoader. The parsed records change when that
    identity changes and no source file does.
    """

    from simulator.yaml_cache import _loader_identity

    return f"python={sys.version};{_loader_identity()}"


def prologue_key(
    root: Path | None = None,
    *,
    engines: Sequence[str],
    invoke_pure_phase: bool,
    work_id: str | None,
    rail: str | None,
    species_arg: str | None,
    entries_material: str,
    code_root: Path | None = None,
) -> str:
    """blake2b over every input the prologue products are built from.

    ``code_root`` defaults to this checkout. A fixture store still misses
    when loader code changes. Tests pass the tmp copy they mutate.
    """

    root = root or REPO_ROOT
    code_root = code_root or REPO_ROOT
    hasher = hashlib.blake2b()

    def add(label: str, text: str) -> None:
        encoded = text.encode("utf-8")
        hasher.update(label.encode("utf-8"))
        hasher.update(len(encoded).to_bytes(8, "big"))
        hasher.update(encoded)

    add("store", _migrated_store_content_sha256(root))
    add("code", _python_tree_token(code_root))
    for relative in PROLOGUE_DATA_INPUTS:
        add(relative, _path_token(root, relative))
    # VapoRock's package and JANAF-vapor-data-full.csv live outside this
    # repo. compilation_tier._vaporock_gas_attempt loads both.
    add("vaporock", _vaporock_token())
    # ``vaporock.equil`` constructs ThermoEngine, and pure-phase NASA-9
    # rows load the same dylibs. Both go through
    # ``setup_thermoengine_dylib_path``. Engines here are ``.value`` strings.
    if invoke_pure_phase or "vaporock" in engines:
        add("thermoengine-dylibs", _thermoengine_dylib_token())
    if invoke_pure_phase:
        # Live pure-phase calls also read the ThermoEngine Python tree
        # (every file, not only .py) and the MAGEMin binary. The env
        # string is part of the key even when the path is missing, so two
        # different missing binaries do not share a row. Concurrency knobs
        # (REGOLITH_MAGEMIN_MAX_CONCURRENCY, REGOLITH_MAGEMIN_SLOTS) are not
        # hashed: they do not change the bytes the body writes.
        add("thermoengine", _thermoengine_token())
        add("magemin", _magemin_binary_token())
        add("magemin-env", os.environ.get("REGOLITH_MAGEMIN_BINARY") or "")
    else:
        # False never calls MAGEMin or the ThermoEngine Python tree.
        add("pure", "off")
    add("runtime", runtime_token())
    args = canonical_material(
        {
            "engines": list(engines),
            "invoke_pure_phase": bool(invoke_pure_phase),
            "work_id": work_id,
            "rail": rail,
            "species_arg": species_arg,
            "entries": entries_material,
        }
    )
    if args is None:
        raise ValueError("prologue args are not canonical")
    add("args", args)
    return hasher.hexdigest()


def open_prologue(
    root: Path,
    *,
    engines: Sequence[str],
    invoke_pure_phase: bool,
    work_id: str | None,
    rail: str | None,
    species_arg: str | None,
    entries: object,
) -> None:
    """Start one prologue. The score loads, then bands, then writes rows.

    A hit is served only when this process still holds that pickle's
    mapping objects (``active_parsed_store``). A synthetic context does
    not, so its rows are computed and not stored. A fixture root does not
    use the persistent cache unless ``REGOLITH_PARSED_STORE_CACHE_DIR`` is
    set: a fixture pickle would evict the production prologue, and the
    next fixture load would skip validation.
    """

    global _SESSION
    if _SESSION is not None:
        raise RuntimeError("a prologue session is already open")
    session = _Session(
        root=root,
        engines=tuple(engines),
        invoke_pure_phase=bool(invoke_pure_phase),
        work_id=work_id,
        rail=rail,
        species_arg=species_arg,
        entries_material=canonical_material(entries),
    )
    _SESSION = session
    if session.entries_material is None or not _io_enabled(root):
        return
    try:
        session.key = prologue_key(
            root,
            engines=session.engines,
            invoke_pure_phase=session.invoke_pure_phase,
            work_id=work_id,
            rail=rail,
            species_arg=species_arg,
            entries_material=session.entries_material,
        )
        session.hit = _read_prologue(session.key)
    except Exception:
        # A key or pickle failure rebuilds. It must not fail the score.
        session.key = None
        session.hit = None


def close_prologue() -> None:
    """Publish the prologue once all three products exist. Idempotent."""

    global _SESSION
    session = _SESSION
    _SESSION = None
    if (
        session is None
        or session.hit is not None
        or session.key is None
        or session.parsed is None
        or session.source_band is None
        or session.compilation is None
    ):
        return
    try:
        _write_prologue(session)
    except Exception:
        return


def build_unfiltered(root: Path) -> ParsedScoreInputs:
    """Parse and validate one unfiltered store. No cache read or write.

    ``validate_corpus`` with ``residuals=None`` is a pure function of the
    migrated works, experiments, observations, benches, and context rows.
    Those records are a pure function of the bytes the store digest hashes
    and of the code and YAML loader in the prologue key. The call does not
    read the clock, the hostname, the residual ledger, or review status.
    The stored hard-issue map is that report. A different key rebuilds it.
    """

    works, experiments, observations = load_migrated_store(root)
    benches = load_migrated_benches(root)
    report = validate_mod.validate_corpus(
        works,
        experiments,
        observations,
        benches=benches,
        context_rows=load_migrated_context(root),
    )
    return ParsedScoreInputs(
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


def take_parsed_store() -> ParsedScoreInputs | None:
    """Hit records for the open prologue, or None when the caller must build."""

    session = _SESSION
    if session is None or session.hit is None:
        return None
    _remember(session.hit.parsed)
    return session.hit.parsed


def store_parsed(parsed: ParsedScoreInputs) -> None:
    """Remember a live build so later prologue steps can publish with it."""

    _remember(parsed)
    session = _SESSION
    if session is None or session.hit is not None or session.key is None:
        return
    session.parsed = parsed


def active_parsed_store(context: object) -> ParsedScoreInputs | None:
    """The prologue records this context still holds, if any.

    A synthetic context, a filtered load, or a replaced loader does not
    hold those objects. A later product of the on-disk store must not be
    reused for them, and their own product must not be stored for it.
    """

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


def take_source_band(
    context: object, entries: object
) -> tuple[Any, ...] | None:
    """A fresh catalog for this prologue, or None when it must be computed."""

    session = _SESSION
    if session is None or session.hit is None:
        return None
    if active_parsed_store(context) is not session.hit.parsed:
        return None
    if canonical_material(entries) != session.entries_material:
        return None
    # Callers do not mutate the catalog. A hit is a copy so two calls do
    # not share one object.
    return copy.deepcopy(session.hit.source_band)


def store_source_band(context: object, entries: object, value: tuple[Any, ...]) -> None:
    session = _SESSION
    if (
        session is None
        or session.hit is not None
        or session.key is None
        or session.parsed is None
        or active_parsed_store(context) is not session.parsed
        or canonical_material(entries) != session.entries_material
    ):
        return
    session.source_band = value


def take_compilation(
    context: object,
    path: Path,
    *,
    engines: Sequence[Any],
    invoke_pure_phase: bool,
    work_id: str | None,
    rail: Any,
    observation_filter: object,
) -> int | None:
    """Restore the JSONL for this prologue. None means the body must run.

    The filter arg in the key is the species string the scorer was given.
    The lambda is present exactly when that string is. A different engine
    list, rail, work, or pure-phase flag is a different prologue.
    """

    session = _SESSION
    if session is None or session.hit is None:
        return None
    if active_parsed_store(context) is not session.hit.parsed:
        return None
    if not _same_compilation_args(
        session,
        engines=engines,
        invoke_pure_phase=invoke_pure_phase,
        work_id=work_id,
        rail=rail,
        observation_filter=observation_filter,
    ):
        return None
    try:
        publish_bytes(path, session.hit.compilation_body)
    except Exception:
        return None
    return session.hit.compilation_count


def store_compilation(
    context: object,
    path: Path,
    count: int,
    *,
    engines: Sequence[Any],
    invoke_pure_phase: bool,
    work_id: str | None,
    rail: Any,
    observation_filter: object,
) -> None:
    session = _SESSION
    if (
        session is None
        or session.hit is not None
        or session.key is None
        or session.parsed is None
        or active_parsed_store(context) is not session.parsed
        or not _same_compilation_args(
            session,
            engines=engines,
            invoke_pure_phase=invoke_pure_phase,
            work_id=work_id,
            rail=rail,
            observation_filter=observation_filter,
        )
    ):
        return
    try:
        session.compilation = (path.read_bytes(), int(count))
    except OSError:
        return


def publish_bytes(path: Path, body: bytes) -> None:
    """Write ``body`` via the one prologue publisher."""

    def write(handle: Any) -> None:
        handle.write(body)

    _publish_file(path, write)


def _same_compilation_args(
    session: _Session,
    *,
    engines: Sequence[Any],
    invoke_pure_phase: bool,
    work_id: str | None,
    rail: Any,
    observation_filter: object,
) -> bool:
    values = tuple(getattr(engine, "value", engine) for engine in engines)
    rail_value = None if rail is None else getattr(rail, "value", rail)
    return (
        values == session.engines
        and bool(invoke_pure_phase) == session.invoke_pure_phase
        and work_id == session.work_id
        and rail_value == session.rail
        and (observation_filter is None) == (session.species_arg is None)
    )


def _remember(parsed: ParsedScoreInputs) -> None:
    # One unfiltered snapshot per process. A later load must not keep every
    # previous unpickle alive; the score uses the context it just built.
    _ACTIVE.clear()
    _ACTIVE[id(parsed.observations)] = parsed


def _io_enabled(root: Path) -> bool:
    if not cache_enabled():
        return False
    if os.environ.get(_CACHE_DIR_ENV):
        return True
    try:
        return root.resolve() == REPO_ROOT.resolve()
    except OSError:
        return False


def _python_tree_token(code_root: Path) -> str:
    """Every ``simulator/**/*.py`` and ``engines/**/*.py`` under ``code_root``."""

    files: list[Path] = []
    for name in ("simulator", "engines"):
        directory = code_root / name
        if not directory.is_dir():
            continue
        for path in directory.rglob("*.py"):
            if path.is_file() and "__pycache__" not in path.parts:
                files.append(path)
    if not files:
        return "absent"
    return hash_relative_files(code_root, files)


def _path_token(root: Path, relative: str) -> str:
    path = root / relative
    if path.is_file():
        return hash_relative_files(path.parent, [path])
    if not path.is_dir():
        return "absent"
    files = _files_under(path)
    if not files:
        return "empty"
    return hash_relative_files(path, files)


def _files_under(directory: Path) -> list[Path]:
    return [
        path
        for path in directory.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    ]


def _directory_token(directory: Path | None) -> str:
    if directory is None or not directory.is_dir():
        return "absent"
    files = _files_under(directory)
    if not files:
        return "empty"
    return hash_relative_files(directory, files)


def _vaporock_token() -> str:
    """Package directory that holds ``vaporock.equil`` and the vapor CSV.

    Importing ``vaporock`` constructs ThermoEngine. The CSV finder names
    the package without that import; the CSV is ``<package>/data/<file>``.
    """

    from simulator.battery.compilation_tier import _installed_janaf_vapor_csv

    csv_path = _installed_janaf_vapor_csv()
    if csv_path is None:
        return "absent"
    return _directory_token(csv_path.parent.parent)


def _thermoengine_dylib_token() -> str:
    """Bytes of the artifacts ``setup_thermoengine_dylib_path`` resolves.

    ``engines.lock.toml`` names ``libphaseobjc.dylib`` and the other
    dylibs. ``setup_thermoengine_dylib_path`` is the only search. A
    missing install is ``absent``; the body then refuses the call.
    """

    from simulator.engine_local_config import (
        load_engine_lock,
        setup_thermoengine_dylib_path,
        thermoengine_artifact_names,
    )

    try:
        directory = setup_thermoengine_dylib_path()
        names = thermoengine_artifact_names(load_engine_lock())
    except ImportError:
        return "absent"
    try:
        return hash_relative_files(directory, [directory / name for name in names])
    except OSError:
        return "absent"


def _thermoengine_token() -> str:
    import importlib.util

    try:
        spec = importlib.util.find_spec("thermoengine")
    except Exception:
        return "absent"
    if spec is None:
        return "absent"
    locations = getattr(spec, "submodule_search_locations", None)
    if locations:
        return _directory_token(Path(next(iter(locations))))
    origin = getattr(spec, "origin", None)
    if not origin or origin == "namespace":
        return "absent"
    return _directory_token(Path(origin).parent)


def _magemin_binary_token() -> str:
    path = _resolved_magemin_binary()
    if path is None or not path.is_file():
        return "absent"
    try:
        return hash_relative_files(path.parent, [path])
    except OSError:
        return "absent"


def _resolved_magemin_binary() -> Path | None:
    """The binary ``MAGEMinBackend`` would run. None when it cannot be imported.

    The locate order stays on the backend. This only asks for the path.
    """

    try:
        from simulator.melt_backend.magemin import MAGEMinBackend
    except ImportError:
        return None
    backend = MAGEMinBackend()
    explicit = os.environ.get("REGOLITH_MAGEMIN_BINARY")
    if explicit:
        backend._config = {"binary_path": explicit}
    resolved = backend._resolve_binary_path()
    return resolved if isinstance(resolved, Path) else None


def _read_prologue(key: str) -> _Prologue | None:
    """Load one complete prologue. Any failure discards the file and rebuilds.

    A torn pickle can raise ``TypeError``, ``struct.error``, or
    ``RecursionError`` as well as ``UnpicklingError``. Those used to crash
    the score. Every ``Exception`` is a miss. A partial envelope is not
    served: the three products are one record.
    """

    path = _prologue_path(key)
    if path is None or not path.is_file():
        return None
    try:
        with path.open("rb") as handle:
            envelope = pickle.load(handle)
        prologue = _prologue_from_envelope(envelope, key)
        if prologue is None:
            raise ValueError("prologue envelope is incomplete")
        return prologue
    except Exception:
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass
        return None


def _prologue_from_envelope(envelope: object, key: str) -> _Prologue | None:
    if not isinstance(envelope, Mapping):
        return None
    if envelope.get("schema") != PROLOGUE_SCHEMA or envelope.get("key") != key:
        return None
    parsed = envelope.get("parsed")
    band = envelope.get("source_band")
    body = envelope.get("compilation_body")
    count = envelope.get("compilation_count")
    if not isinstance(parsed, ParsedScoreInputs):
        return None
    record_fields = (
        parsed.works,
        parsed.experiments,
        parsed.observations,
        parsed.benches,
        parsed.origins,
        parsed.hard_issues_by_observation,
    )
    if any(not isinstance(field, dict) for field in record_fields):
        return None
    if (
        not isinstance(band, tuple)
        or len(band) != 3
        or not isinstance(band[0], dict)
        or not isinstance(band[1], list)
        or not isinstance(band[2], dict)
    ):
        return None
    if not isinstance(body, bytes) or not isinstance(count, int):
        return None
    return _Prologue(
        parsed=parsed,
        source_band=band,
        compilation_body=body,
        compilation_count=count,
    )


def _write_prologue(session: _Session) -> None:
    path = _prologue_path(session.key or "")
    if path is None or session.compilation is None or session.source_band is None:
        return
    if session.parsed is None:
        return
    envelope = {
        "schema": PROLOGUE_SCHEMA,
        "key": session.key,
        "parsed": session.parsed,
        "source_band": session.source_band,
        "compilation_body": session.compilation[0],
        "compilation_count": session.compilation[1],
    }
    _publish_file(
        path,
        lambda handle: pickle.dump(envelope, handle, protocol=pickle.HIGHEST_PROTOCOL),
    )
    _evict(path.parent, keep=_KEEP)
    _drop_other_versions(path.parent.parent, path.parent.name)
    _drop_child_directories(path.parent)


def _publish_file(path: Path, write: Callable[[Any], None]) -> None:
    """One publisher: temp file, fsync, then ``os.replace``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            write(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _prologue_path(key: str) -> Path | None:
    root = _cache_directory()
    if root is None or not _token_ok(key):
        return None
    return root / f"v{PROLOGUE_SCHEMA}" / f"{key}.pickle"


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


def _mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return -1


def _evict(directory: Path, *, keep: int) -> None:
    try:
        files = [path for path in directory.glob("*.pickle") if path.is_file()]
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
    """A schema bump must not leave the previous multi-GB pickles behind."""

    try:
        children = list(cache_root.iterdir())
    except OSError:
        return
    for child in children:
        if child.name == current or not child.name.startswith("v"):
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)


def _drop_child_directories(version_dir: Path) -> None:
    """The previous layout kept snapshots and reuse blobs in subdirectories."""

    try:
        children = list(version_dir.iterdir())
    except OSError:
        return
    for child in children:
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
