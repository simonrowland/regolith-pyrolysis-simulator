"""Own BLAS thread defaults, loaded-pool limits, and startup provenance."""

from dataclasses import dataclass
import os
import sys
from typing import Literal

THREAD_ENV_VARS: tuple[str, ...] = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)

# OpenBLAS precedence is measured on this build in thread-check/poolprobe.py,
# check.py and solve_bits.py (including the SciPy comparison). MKL follows its
# documented MKL_NUM_THREADS-over-OMP precedence but is untested here; numexpr
# is not installed in this venv. Keep each row most-specific first.
_RUNTIME_CONTROLS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("openblas", ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS")),
    ("mkl", ("MKL_NUM_THREADS", "OMP_NUM_THREADS")),
    ("numexpr", ("NUMEXPR_NUM_THREADS",)),
    ("accelerate", ("VECLIB_MAXIMUM_THREADS",)),
    ("*", ("OMP_NUM_THREADS",)),
)

HowObtained = Literal[
    "variables-before-load",
    "threadpoolctl-after-load",
    "explicit-launcher-value",
    "not-in-effect",
]


@dataclass(frozen=True)
class RuntimeState:
    name: str
    api: str
    build_records: tuple[tuple[str, str], ...]
    library_filename: str | None
    version: str | None
    observable: bool
    num_threads: int | None
    how_obtained: HowObtained
    # True means the observed size equals the governing setting: one under
    # policy or the launcher's value when honored; false if unobserved/conflicting.
    limit_known_to_bind: bool
    reason: str | None = None


@dataclass(frozen=True)
class _RuntimeObservation:
    api: str
    library_filename: str | None
    version: str | None
    build_records: tuple[tuple[str, str], ...]
    build_name: str | None
    num_threads: int | None
    loaded_before: bool


@dataclass(frozen=True)
class _RuntimeClassification:
    how_obtained: HowObtained
    limit_known_to_bind: bool
    reason: str | None


@dataclass(frozen=True)
class Notice:
    kind: str
    values: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class PolicyState:
    launcher_values: tuple[tuple[str, str | None], ...]
    runtimes: tuple[RuntimeState, ...]
    notices: tuple[Notice, ...]


_launcher_values: tuple[tuple[str, str | None], ...] | None = None
_prepared = False
_finished_state: PolicyState | None = None
_limiter: object | None = None
_limiter_installed_at_startup = False
_loaded_at_prepare: tuple[tuple[str, str | None, str | None], ...] | None = None
_builds_at_prepare: tuple[tuple[str, str, bool], ...] = ()


def _snapshot() -> tuple[tuple[str, str | None], ...]:
    global _launcher_values
    if _launcher_values is None:
        _launcher_values = tuple((name, os.environ.get(name)) for name in THREAD_ENV_VARS)
    return _launcher_values


def _threadpoolctl():
    try:
        from threadpoolctl import threadpool_info, threadpool_limits
    except Exception:
        return None
    return threadpool_info, threadpool_limits


def _pool_identity(pool: dict) -> tuple[str, str | None, str | None]:
    return (
        str(pool.get("internal_api", "unknown")).lower(),
        str(pool["filepath"]) if pool.get("filepath") else None,
        str(pool["version"]) if pool.get("version") is not None else None,
    )


def _build_records() -> tuple[tuple[str, str, bool], ...]:
    records = []
    for module_name in ("numpy", "scipy"):
        module = sys.modules.get(module_name)
        if module is None:
            continue
        try:
            config = module.show_config(mode="dicts")
            name = config["Build Dependencies"]["blas"]["name"]
            if not isinstance(name, str) or not name.strip():
                raise ValueError("missing BLAS build name")
        except Exception:
            records.append((module_name, "unknown", False))
        else:
            records.append((module_name, name, True))
    return tuple(records)


def _runtime_row_for_name(
    name: str,
) -> tuple[str, tuple[str, ...]] | None:
    lowered = name.lower()
    if lowered == "unknown":
        return None
    for runtime, controls in _RUNTIME_CONTROLS:
        if lowered == runtime or lowered.startswith(runtime):
            return runtime, controls
    return _RUNTIME_CONTROLS[-1]


def _controls_for_runtime(name: str) -> tuple[str, ...]:
    row = _runtime_row_for_name(name)
    return row[1] if row is not None else ()


def _api_for_build_name(name: str) -> str:
    lowered = name.lower()
    row = _runtime_row_for_name(name)
    return lowered if row is None or row == _RUNTIME_CONTROLS[-1] else row[0]


def _explicit_controls(
    controls: tuple[str, ...], original: dict[str, str | None]
) -> tuple[tuple[tuple[str, str], ...], bool]:
    explicit = tuple(
        (name, original[name]) for name in controls if original.get(name) is not None
    )
    return explicit, len({value for _name, value in explicit}) > 1


def _expected_threads(controls: tuple[str, ...], original: dict[str, str | None]) -> int | None:
    explicit, conflict = _explicit_controls(controls, original)
    if conflict:
        return None
    if explicit:
        try:
            return int(explicit[0][1])
        except ValueError:
            return None
    return 1


def _classify_runtime(
    num_threads: int | None,
    loaded_before: bool,
    launcher_values: tuple[tuple[str, str | None], ...],
    controls: tuple[str, ...],
    threadpoolctl_available: bool,
    limiter_installed_at_startup: bool = True,
    startup_pool_snapshot_available: bool = True,
) -> _RuntimeClassification:
    """Classify observed provenance using only this runtime's startup inputs.

    Decision table, first matching row applies (observed threads; loaded before;
    launcher controls; readback available; startup limiter installed -> how, known, reason):
    Without a startup pool snapshot (threadpoolctl unavailable at prepare), every
    runtime is not-in-effect, unknown, and unattributed, regardless of other inputs.
    Otherwise:
    1. absent; any; any; no -> not-in-effect, false, readback unavailable
       (plus startup-limiter and late-load details when applicable).
    2. absent; yes; any; yes -> not-in-effect, false, loaded-before/unobservable
       (plus the startup-limiter reason when it was not installed).
    3. absent; no; any; yes -> variables-before-load, false, none.
    4. observed; any; at least one non-one launcher -> explicit-launcher-value,
       observed matches the unconflicted setting.
    5. observed; yes; no non-one launcher; either readback; no limiter ->
       not-in-effect, false, startup limiter could not be installed.
    6. observed; yes; no non-one launcher; either readback; limiter installed ->
       threadpoolctl-after-load, observed matches expected setting, none.
    7. observed; no; no non-one launcher; either readback -> variables-before-load,
       observed matches expected setting, none.
    Rows 5–7 accept either readback-availability input in the pure classifier;
    the gatherer cannot produce an observed count when finish-time readback is
    unavailable because it obtains counts only from threadpoolctl_info().
    """

    original = dict(launcher_values)
    if not startup_pool_snapshot_available:
        return _RuntimeClassification(
            "not-in-effect",
            False,
            "start-up pool snapshot unavailable (threadpoolctl could not be imported at start-up); load order and binding are not attributed",
        )
    explicit, conflict = _explicit_controls(controls, original)
    explicit_non_one = tuple((key, value) for key, value in explicit if value != "1")
    if num_threads is None and not threadpoolctl_available:
        how = "not-in-effect"
        known = False
        reason = "threadpoolctl readback unavailable"
        if not limiter_installed_at_startup:
            reason += "; limiter could not be installed at start-up"
        if loaded_before:
            reason += "; loaded before start-up; not controllable"
            late_variables = tuple(variable for variable in controls if original.get(variable) is None)
            if late_variables:
                reason += "; " + ", ".join(late_variables) + " set after load"
    elif num_threads is None and loaded_before:
        how = "not-in-effect"
        known = False
        late_variables = tuple(variable for variable in controls if original.get(variable) is None)
        reason = "loaded before start-up; not controllable"
        if not limiter_installed_at_startup:
            reason += "; threadpoolctl limiter could not be installed at start-up"
        if late_variables:
            reason += "; " + ", ".join(late_variables) + " set after load"
    elif num_threads is None:
        how = "variables-before-load"
        known = False
        reason = None
    elif explicit_non_one:
        how = "explicit-launcher-value"
        known = not conflict and num_threads == _expected_threads(controls, original)
        reason = None
    elif loaded_before and not limiter_installed_at_startup:
        how = "not-in-effect"
        known = False
        reason = "threadpoolctl limiter could not be installed at start-up"
    else:
        how = "threadpoolctl-after-load" if loaded_before else "variables-before-load"
        known = num_threads == _expected_threads(controls, original)
        reason = None
    return _RuntimeClassification(how, known, reason)


def _gather_runtime_observations() -> tuple[tuple[_RuntimeObservation, ...], bool]:
    """Collect current pools and imported build records for state assembly."""

    api = _threadpoolctl()
    available = api is not None
    pools = tuple(api[0]()) if api is not None else ()
    builds = _build_records()
    loaded_builds = {module for module, _name, _readable in _builds_at_prepare}
    observations: list[_RuntimeObservation] = []
    for pool in pools:
        identity = _pool_identity(pool)
        build_records = tuple(
            (module, name)
            for module, name, readable in builds
            if readable and _api_for_build_name(name) == identity[0]
        )
        observations.append(
            _RuntimeObservation(
                identity[0],
                os.path.basename(identity[1]) if identity[1] else None,
                identity[2],
                build_records,
                build_records[0][1] if build_records else None,
                int(pool["num_threads"])
                if pool.get("num_threads") is not None
                else None,
                _loaded_at_prepare is not None and identity in _loaded_at_prepare,
            )
        )
    for module, build_name, readable in builds:
        api = _api_for_build_name(build_name) if readable else "unknown"
        if readable and any(item.api == api for item in observations):
            continue
        observations.append(
            _RuntimeObservation(
                api,
                None,
                None,
                ((module, build_name if readable else "unknown"),),
                build_name if readable else "unknown",
                None,
                _loaded_at_prepare is not None and module in loaded_builds,
            )
        )
    return tuple(observations), available


def apply_environment_defaults() -> None:
    """Apply one-thread defaults without replacing launcher settings."""

    values = dict(_snapshot())
    for name in THREAD_ENV_VARS:
        if os.environ.get(name) is not None:
            continue
        # Within a runtime row, the first set variable wins. A default for V
        # can override a launcher value only when V comes before that control;
        # a later default cannot mask a more-specific launcher setting. Thus
        # OPENBLAS=2 and MKL=4 still get OMP=1, while OMP=2 suppresses earlier
        # OpenBLAS/MKL defaults. These are the three fresh-process checks.
        masks_launcher = any(
            values.get(other) not in (None, "1")
            for _runtime, controls in _RUNTIME_CONTROLS
            if name in controls
            for index, variable in enumerate(controls)
            if variable == name
            for other in controls[index + 1 :]
        )
        if not masks_launcher:
            os.environ[name] = "1"


def prepare() -> None:
    """Snapshot launcher settings, set defaults and limit eligible loaded pools."""

    global _prepared, _limiter, _loaded_at_prepare, _builds_at_prepare
    global _limiter_installed_at_startup
    if _prepared:
        return
    _snapshot()
    apply_environment_defaults()
    _builds_at_prepare = _build_records()
    api = _threadpoolctl()
    if api is not None:
        info, limits = api
        pools = tuple(info())
        _loaded_at_prepare = tuple(_pool_identity(pool) for pool in pools)
        original = dict(_launcher_values or ())
        eligible = {
            str(pool["prefix"]): 1
            for pool in pools
            if pool.get("prefix")
            and not any(
                original.get(variable) not in (None, "1")
                for variable in _controls_for_runtime(str(pool.get("internal_api", "")).lower())
            )
        }
        # threadpoolctl's dict selector is a library prefix; one retained
        # limiter controls every eligible pool while leaving overrides alone.
        if eligible:
            _limiter = limits(limits=eligible)
            _limiter_installed_at_startup = _limiter is not None
    _prepared = True


def finish() -> PolicyState:
    """Read and cache one immutable startup-state snapshot."""

    global _finished_state
    if _finished_state is not None:
        return _finished_state
    prepare()
    launcher_values = _snapshot()
    original = dict(launcher_values)
    notices: list[Notice] = []
    if _loaded_at_prepare is None:
        notices.append(Notice("not-in-effect", (("reason", "threadpoolctl limiter could not be installed at start-up"),)))

    observations, threadpoolctl_available = _gather_runtime_observations()
    runtimes: list[RuntimeState] = []
    for observation in observations:
        api_name = observation.api
        library_filename = observation.library_filename
        version = observation.version
        build_records = observation.build_records
        name = observation.build_name or (build_records[0][1] if build_records else api_name)
        num_threads = observation.num_threads
        controls = _controls_for_runtime(name)
        explicit, conflict = _explicit_controls(controls, original)
        if conflict:
            notices.append(Notice("conflicting-launcher-values", explicit))
        classification = _classify_runtime(
            num_threads,
            observation.loaded_before,
            launcher_values,
            controls,
            threadpoolctl_available,
            _limiter_installed_at_startup,
            startup_pool_snapshot_available=_loaded_at_prepare is not None,
        )
        if classification.how_obtained == "explicit-launcher-value":
            notices.append(
                Notice(
                    "honoured-launcher-value",
                    tuple((key, value) for key, value in explicit if value != "1"),
                )
            )
        runtimes.append(
            RuntimeState(
                name,
                api_name,
                build_records,
                library_filename,
                version,
                bool(num_threads is not None),
                num_threads,
                classification.how_obtained,
                classification.limit_known_to_bind,
                classification.reason,
            )
        )

    _finished_state = PolicyState(
        tuple((name, original[name]) for name in THREAD_ENV_VARS),
        tuple(runtimes),
        tuple(dict.fromkeys(notices)),
    )
    return _finished_state


def state() -> PolicyState | None:
    """Return the cached startup state, or ``None`` before ``finish``."""

    return _finished_state
