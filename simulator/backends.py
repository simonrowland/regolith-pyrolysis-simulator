"""Shared melt-backend selection and simulator construction helpers."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, TypeVar

from simulator.corpus_version import (
    current_corpus_version,
    interoperable_corpus_versions,
)
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.alphamelts import AlphaMELTSBackend
from simulator.melt_backend.base import DEFAULT_BACKEND_CAPABILITIES, StubBackend


INELIGIBLE_ACTIVE_BACKENDS = ("vaporock", "magemin")
CACHED_REAL_BACKEND_NAME = "cached-real"
CACHED_REAL_MISS_POLICIES = ("fail-loud", "live-fill")
CACHE_TIER_CEILINGS = (
    "cached_interpolated",
    "cached_physics_bucket",
    "cached_exact",
)
DEFAULT_CACHE_TIER_CEILING = "cached_interpolated"
BACKEND_STATUS_OK = "ok"
BACKEND_STATUS_UNAVAILABLE = "unavailable"
REAL_DATA_REQUIRED_INTENTS = frozenset(
    {
        "silicate_equilibrium",
        "silicate_liquidus",
        "gate_liquid_fraction",
        "equilibrium_crystallization",
        "fractional_crystallization",
        "decompression_path",
    }
)
_REPO_ROOT = Path(__file__).resolve().parents[1]

_E = TypeVar("_E", bound=Exception)


class BackendUnavailableError(RuntimeError):
    """Requested backend is required for this run but is unavailable."""


class BackendSelectionPolicy(Enum):
    """Explicit backend-selection semantics for each caller surface."""

    WEB_AUTODETECT = "web-autodetect"
    RUNNER_STRICT = "runner-strict"


@dataclass(frozen=True)
class BackendResolutionStatus:
    """Machine-readable result of backend selection."""

    requested_backend: str
    active_backend: str
    backend_status: str
    authoritative: bool
    selection_policy: str
    message: str = ""

    def as_payload(self) -> dict[str, Any]:
        return {
            "backend_requested": self.requested_backend,
            "backend_active": self.active_backend,
            "backend_status": self.backend_status,
            "backend_authoritative": self.authoritative,
            "backend_selection_policy": self.selection_policy,
            "backend_status_message": self.message,
        }


@dataclass(frozen=True)
class CachedRealConfig:
    """Runtime PT-1 cache selection for the cached-real tier."""

    db_path: Path
    authorized_backend_name: str
    corpus_version: str
    interoperable_corpus_versions: tuple[str, ...]
    authorized_backend_version: str = ""
    miss_policy: str = "fail-loud"
    cache_tier_ceiling: str = DEFAULT_CACHE_TIER_CEILING
    read_only_base_db_path: Path | None = None
    strict_vapor_gate: bool = False


@dataclass(frozen=True)
class SimulatorBuildConfig:
    """Inputs needed to construct a PyrolysisSimulator."""

    backend: Any
    setpoints: Mapping[str, Any]
    feedstocks: Mapping[str, Any]
    vapor_pressures: Mapping[str, Any]
    materials: Mapping[str, Any] | None = None
    allow_lab_geometry_temperature_profiles: bool = False


def build_simulator(config: SimulatorBuildConfig) -> PyrolysisSimulator:
    """Build a simulator from pre-loaded data and an initialized backend."""

    sim = PyrolysisSimulator(
        config.backend,
        copy.deepcopy(config.setpoints),
        copy.deepcopy(config.feedstocks),
        copy.deepcopy(config.vapor_pressures),
        materials=(
            copy.deepcopy(config.materials)
            if config.materials is not None
            else None
        ),
        allow_lab_geometry_temperature_profiles=(
            config.allow_lab_geometry_temperature_profiles
        ),
    )
    resolution = backend_resolution_status(config.backend)
    sim._backend_resolution_status = resolution
    sim._backend_selection_status = resolution.backend_status
    sim._backend_authoritative = resolution.authoritative
    return sim


class CachedRealBackend:
    """MeltBackend-shaped cached-real facade.

    Cache lookup is owned by ``PT0DeterminismStore`` in ``core.py``. This
    facade gives the public resolver a distinct, non-stub backend identity and
    delegates only explicit ``live-fill`` misses to an active-safe real backend.
    """

    name = CACHED_REAL_BACKEND_NAME

    def __init__(
        self,
        *,
        config: CachedRealConfig,
        live_backend: Any | None = None,
    ) -> None:
        self.config = config
        self._live_backend = live_backend

    def initialize(self, _config: Mapping[str, Any] | None = None) -> bool:
        return True

    def is_available(self) -> bool:
        if self.config.miss_policy == "fail-loud":
            return True
        return (
            self._live_backend is not None
            and bool(self._live_backend.is_available())
        )

    def capabilities(self) -> Mapping[str, bool]:
        if self._live_backend is not None:
            capabilities = getattr(self._live_backend, "capabilities", None)
            if callable(capabilities):
                return capabilities()
        return dict(DEFAULT_BACKEND_CAPABILITIES)

    def equilibrate(
        self,
        *args: Any,
        composition_mol_by_account: Mapping[str, Mapping[str, float]] | None = None,
        **kwargs: Any,
    ):
        if self._live_backend is None:
            raise RuntimeError(
                "cached-real out-of-coverage: no live-fill backend configured"
            )
        if composition_mol_by_account is not None:
            kwargs["composition_mol_by_account"] = composition_mol_by_account
        return self._live_backend.equilibrate(*args, **kwargs)

    def find_liquidus_solidus(
        self,
        *args: Any,
        composition_mol_by_account: Mapping[str, Mapping[str, float]] | None = None,
        **kwargs: Any,
    ):
        if self._live_backend is None:
            raise RuntimeError(
                "cached-real out-of-coverage: no live-fill backend configured"
            )
        finder = getattr(self._live_backend, "find_liquidus_solidus", None)
        if not callable(finder):
            raise RuntimeError(
                "cached-real live-fill backend has no liquidus/solidus solver"
            )
        if composition_mol_by_account is not None:
            kwargs["composition_mol_by_account"] = composition_mol_by_account
        return finder(*args, **kwargs)


def normalize_cached_real_config(
    value: CachedRealConfig | Mapping[str, Any] | None,
    *,
    unavailable_error_cls: type[_E] = BackendUnavailableError,
) -> CachedRealConfig:
    """Validate and normalize the cached-real cache config."""

    if value is None:
        raise unavailable_error_cls(
            "cached-real requires reduced_real_cache.db_path and "
            "reduced_real_cache.miss_policy"
        )
    if isinstance(value, CachedRealConfig):
        return value
    if not isinstance(value, Mapping):
        raise unavailable_error_cls("cached-real cache config must be a mapping")
    raw_db_path = value.get("db_path")
    if raw_db_path in (None, ""):
        raise unavailable_error_cls("cached-real requires reduced_real_cache.db_path")
    db_path = Path(str(raw_db_path)).expanduser()
    if not db_path.is_absolute():
        db_path = (_REPO_ROOT / db_path).resolve()
    authorized_backend_name = str(
        value.get("authorized_backend_name", "")
    ).strip()
    if not authorized_backend_name:
        raise unavailable_error_cls(
            "cached-real requires reduced_real_cache.authorized_backend_name"
        )
    corpus_version = current_corpus_version()
    interoperable_versions = interoperable_corpus_versions()
    authorized_backend_version = str(
        value.get("authorized_backend_version", "")
    ).strip()
    miss_policy = str(value.get("miss_policy", "fail-loud")).strip().lower()
    miss_policy = miss_policy.replace("_", "-")
    if miss_policy not in CACHED_REAL_MISS_POLICIES:
        raise unavailable_error_cls(
            "cached-real reduced_real_cache.miss_policy must be one of "
            f"{', '.join(CACHED_REAL_MISS_POLICIES)}"
        )
    cache_tier_ceiling = str(
        value.get("cache_tier_ceiling", DEFAULT_CACHE_TIER_CEILING)
    ).strip()
    if cache_tier_ceiling not in CACHE_TIER_CEILINGS:
        raise unavailable_error_cls(
            "cached-real reduced_real_cache.cache_tier_ceiling must be one of "
            f"{', '.join(CACHE_TIER_CEILINGS)}"
        )
    read_only_base_db_path = None
    raw_read_only_base = value.get("read_only_base_db_path")
    if raw_read_only_base not in (None, ""):
        read_only_base_db_path = Path(str(raw_read_only_base)).expanduser()
        if not read_only_base_db_path.is_absolute():
            read_only_base_db_path = (_REPO_ROOT / read_only_base_db_path).resolve()
    strict_vapor_gate = value.get("strict_vapor_gate", False)
    if not isinstance(strict_vapor_gate, bool):
        raise unavailable_error_cls(
            "cached-real reduced_real_cache.strict_vapor_gate must be a bool"
        )
    return CachedRealConfig(
        db_path=db_path,
        authorized_backend_name=authorized_backend_name,
        corpus_version=corpus_version,
        interoperable_corpus_versions=interoperable_versions,
        authorized_backend_version=authorized_backend_version,
        miss_policy=miss_policy,
        cache_tier_ceiling=cache_tier_ceiling,
        read_only_base_db_path=read_only_base_db_path,
        strict_vapor_gate=strict_vapor_gate,
    )


def build_cached_real_store(config: CachedRealConfig):
    """Build the PT-0/PT-1 runtime store for a cached-real run."""

    from simulator.reduced_real_determinism import PT0DeterminismStore

    mode = "replay" if config.miss_policy == "fail-loud" else "capture"
    store = PT0DeterminismStore(
        mode,
        db_path=config.db_path,
        read_only_base_db_path=config.read_only_base_db_path,
        strict_vapor_gate=config.strict_vapor_gate,
    )
    store.cached_real_miss_policy = config.miss_policy
    store.cache_tier_ceiling = config.cache_tier_ceiling
    return store


def resolve_backend(
    backend_name: str,
    policy: BackendSelectionPolicy,
    *,
    unavailable_error_cls: type[_E] = BackendUnavailableError,
    log_selection: Callable[[object], None] | None = None,
    log_message: Callable[[str], None] | None = None,
    alphamelts_backend_cls: type = AlphaMELTSBackend,
    stub_backend_cls: type = StubBackend,
    cached_real_config: CachedRealConfig | Mapping[str, Any] | None = None,
    cached_real_live_backend_cls: type | None = None,
    required_intents: Iterable[Any] | None = None,
    backend_config: Mapping[str, Any] | None = None,
):
    """Resolve and initialize the active melt backend under an explicit policy."""

    if policy is BackendSelectionPolicy.WEB_AUTODETECT:
        name = (backend_name or "").strip().lower()
        backend = _resolve_web_autodetect(
            name,
            unavailable_error_cls=unavailable_error_cls,
            log_selection=log_selection,
            log_message=log_message,
            alphamelts_backend_cls=alphamelts_backend_cls,
            stub_backend_cls=stub_backend_cls,
            cached_real_config=cached_real_config,
            cached_real_live_backend_cls=cached_real_live_backend_cls,
            backend_config=backend_config,
        )
    elif policy is BackendSelectionPolicy.RUNNER_STRICT:
        backend = _resolve_runner_strict(
            backend_name,
            unavailable_error_cls=unavailable_error_cls,
            alphamelts_backend_cls=alphamelts_backend_cls,
            stub_backend_cls=stub_backend_cls,
            cached_real_config=cached_real_config,
            cached_real_live_backend_cls=cached_real_live_backend_cls,
            backend_config=backend_config,
        )
    else:
        raise ValueError(f"unknown backend selection policy {policy!r}")

    return _finalize_backend_resolution(
        backend,
        requested_backend=str(backend_name or ""),
        policy=policy,
        required_intents=required_intents,
        unavailable_error_cls=unavailable_error_cls,
    )


def emit_web_engine_selection_log(
    backend,
    log_message: Callable[[str], None] | None = None,
) -> None:
    """Emit the web's one-line engine-selection log."""

    name = type(backend).__name__
    caps = backend.capabilities()
    resolution = backend_resolution_status(backend)
    cap_str = ", ".join(
        f'{key}={"true" if caps.get(key) else "false"}'
        for key in ("silicate_melt", "gas_volatiles")
    )
    _log(
        log_message,
        f"engine selection: {name} "
        f"(backend_status={resolution.backend_status}, "
        f"authoritative={str(resolution.authoritative).lower()}, "
        f"capabilities: {cap_str}) -- "
        "VapoRock/MAGEMin not eligible until kernel",
    )


def backend_resolution_status(backend: Any) -> BackendResolutionStatus:
    """Return resolver metadata, deriving a conservative fallback if absent."""

    status = getattr(backend, "backend_resolution_status", None)
    if isinstance(status, BackendResolutionStatus):
        return status

    active_backend = type(backend).__name__
    is_stub = isinstance(backend, StubBackend) or active_backend == "StubBackend"
    backend_status = (
        BACKEND_STATUS_UNAVAILABLE if is_stub else BACKEND_STATUS_OK
    )
    authoritative = backend_status == BACKEND_STATUS_OK and not is_stub
    return BackendResolutionStatus(
        requested_backend=str(getattr(backend, "name", active_backend) or active_backend),
        active_backend=active_backend,
        backend_status=backend_status,
        authoritative=authoritative,
        selection_policy="unknown",
        message=(
            "stub backend selected; no authoritative melt result available"
            if is_stub
            else "backend selected"
        ),
    )


def _finalize_backend_resolution(
    backend: Any,
    *,
    requested_backend: str,
    policy: BackendSelectionPolicy,
    required_intents: Iterable[Any] | None,
    unavailable_error_cls: type[_E],
):
    resolution = _make_backend_resolution_status(
        backend,
        requested_backend=requested_backend,
        policy=policy,
    )
    _attach_backend_resolution_status(backend, resolution)
    _raise_if_required_intents_need_real_backend(
        resolution,
        required_intents,
        unavailable_error_cls=unavailable_error_cls,
    )
    return backend


def _make_backend_resolution_status(
    backend: Any,
    *,
    requested_backend: str,
    policy: BackendSelectionPolicy,
) -> BackendResolutionStatus:
    active_backend = type(backend).__name__
    is_stub = isinstance(backend, StubBackend) or active_backend == "StubBackend"
    backend_status = (
        BACKEND_STATUS_UNAVAILABLE if is_stub else BACKEND_STATUS_OK
    )
    return BackendResolutionStatus(
        requested_backend=requested_backend,
        active_backend=active_backend,
        backend_status=backend_status,
        authoritative=backend_status == BACKEND_STATUS_OK and not is_stub,
        selection_policy=policy.value,
        message=(
            "stub backend selected; no authoritative melt result available"
            if is_stub
            else "backend selected"
        ),
    )


def _attach_backend_resolution_status(
    backend: Any,
    resolution: BackendResolutionStatus,
) -> None:
    try:
        backend.backend_resolution_status = resolution
        backend.backend_status = resolution.backend_status
        backend.backend_authoritative = resolution.authoritative
    except Exception:  # noqa: BLE001 - status helper still derives fallback
        return


def _normalize_intent_names(required_intents: Iterable[Any] | None) -> set[str]:
    if required_intents is None:
        return set()
    names: set[str] = set()
    for intent in required_intents:
        raw = getattr(intent, "value", intent)
        name = str(raw).strip().lower()
        if name:
            names.add(name)
    return names


def _raise_if_required_intents_need_real_backend(
    resolution: BackendResolutionStatus,
    required_intents: Iterable[Any] | None,
    *,
    unavailable_error_cls: type[_E],
) -> None:
    real_required = sorted(
        _normalize_intent_names(required_intents) & REAL_DATA_REQUIRED_INTENTS
    )
    if not real_required:
        return
    if (
        resolution.backend_status == BACKEND_STATUS_OK
        and resolution.authoritative
    ):
        return
    intents = ", ".join(real_required)
    raise unavailable_error_cls(
        "backend_status="
        f"{resolution.backend_status!r} from {resolution.active_backend} "
        f"cannot satisfy real-data intents: {intents}"
    )


def _resolve_web_autodetect(
    name: str,
    *,
    unavailable_error_cls: type[_E],
    log_selection: Callable[[object], None] | None,
    log_message: Callable[[str], None] | None,
    alphamelts_backend_cls: type,
    stub_backend_cls: type,
    cached_real_config: CachedRealConfig | Mapping[str, Any] | None,
    cached_real_live_backend_cls: type | None,
    backend_config: Mapping[str, Any] | None,
):
    if name in INELIGIBLE_ACTIVE_BACKENDS:
        backend_label = "VapoRock" if name == "vaporock" else "MAGEMin"
        raise unavailable_error_cls(
            f"{backend_label} is not eligible as the active melt backend "
            "until \\goal CHEMISTRY-KERNEL-CARVE-OUT wires a multi-intent "
            "dispatcher; select alphamelts or auto."
        )

    if name == CACHED_REAL_BACKEND_NAME:
        backend = _cached_real_backend(
            cached_real_config,
            unavailable_error_cls=unavailable_error_cls,
            alphamelts_backend_cls=alphamelts_backend_cls,
            cached_real_live_backend_cls=cached_real_live_backend_cls,
            backend_config=backend_config,
        )
        _log_selection(backend, log_selection, log_message)
        return backend

    if name not in ("", "auto", "stub", "alphamelts"):
        raise unavailable_error_cls(
            f"unknown backend {name!r}; select auto, stub, alphamelts, "
            "or cached-real"
        )

    # D1 fix: an explicit 'stub' request pins StubBackend deterministically;
    # only 'auto'/'' fall through to the AlphaMELTS->Stub autodetect chain.
    # (Previously 'stub' silently autodetected, so a caller asking for the
    # deterministic stub got AlphaMELTS when it was available.)
    if name == "stub":
        backend = _stub_backend(stub_backend_cls)
        _log_selection(backend, log_selection, log_message)
        return backend

    if name == "alphamelts":
        backend = _try_alphamelts(alphamelts_backend_cls, backend_config)
        if backend is not None:
            _log_selection(backend, log_selection, log_message)
            return backend
        raise unavailable_error_cls(
            "AlphaMELTS unavailable; run install-dependencies.py"
        )

    backend = _try_alphamelts(alphamelts_backend_cls, backend_config)
    if backend is not None:
        _log_selection(backend, log_selection, log_message)
        return backend
    backend = _stub_backend(stub_backend_cls)
    _log_selection(backend, log_selection, log_message)
    return backend


def _resolve_runner_strict(
    name: str,
    *,
    unavailable_error_cls: type[_E],
    alphamelts_backend_cls: type,
    stub_backend_cls: type,
    cached_real_config: CachedRealConfig | Mapping[str, Any] | None,
    cached_real_live_backend_cls: type | None,
    backend_config: Mapping[str, Any] | None,
):
    if name in ("", "stub"):
        return _stub_backend(stub_backend_cls)
    if name == "auto":
        raise unavailable_error_cls(
            "auto backend selection is unavailable under runner-strict; "
            "select stub, alphamelts, or cached-real"
        )
    if name == "alphamelts":
        backend = _try_alphamelts(alphamelts_backend_cls, backend_config)
        if backend is not None:
            return backend
        raise unavailable_error_cls(
            "AlphaMELTS unavailable; rerun with --backend=stub or "
            "install via install-dependencies.py"
        )
    if name == CACHED_REAL_BACKEND_NAME:
        return _cached_real_backend(
            cached_real_config,
            unavailable_error_cls=unavailable_error_cls,
            alphamelts_backend_cls=alphamelts_backend_cls,
            cached_real_live_backend_cls=cached_real_live_backend_cls,
            backend_config=backend_config,
        )
    raise unavailable_error_cls(f"unknown backend {name!r}")


def _try_alphamelts(
    alphamelts_backend_cls: type,
    backend_config: Mapping[str, Any] | None = None,
):
    backend = alphamelts_backend_cls()
    if backend.initialize(dict(backend_config or {})) and backend.is_available():
        return backend
    return None


def _stub_backend(stub_backend_cls: type):
    backend = stub_backend_cls()
    backend.initialize({})
    return backend


def _cached_real_backend(
    cached_real_config: CachedRealConfig | Mapping[str, Any] | None,
    *,
    unavailable_error_cls: type[_E],
    alphamelts_backend_cls: type,
    cached_real_live_backend_cls: type | None,
    backend_config: Mapping[str, Any] | None,
) -> CachedRealBackend:
    config = normalize_cached_real_config(
        cached_real_config,
        unavailable_error_cls=unavailable_error_cls,
    )
    live_backend = None
    if config.miss_policy == "live-fill":
        live_backend_cls = cached_real_live_backend_cls or alphamelts_backend_cls
        live_backend = _try_alphamelts(live_backend_cls, backend_config)
        if live_backend is None:
            raise unavailable_error_cls(
                "cached-real live-fill requires an available live real backend"
            )
        live_identity = _live_backend_identity(live_backend)
        expected_identity = (config.authorized_backend_name, config.corpus_version)
        if not _backend_identity_matches(
            live_identity,
            expected_identity,
            unavailable_error_cls=unavailable_error_cls,
        ):
            raise unavailable_error_cls(
                "cached-real live-fill backend identity mismatch: "
                f"configured {expected_identity[0]} for corpus "
                f"{expected_identity[1]}, got {live_identity[0]}"
            )
    return CachedRealBackend(config=config, live_backend=live_backend)


def _live_backend_identity(backend: Any) -> tuple[str, str]:
    raw_name = getattr(backend, "name", None)
    if raw_name is None and any(
        cls.__name__ == "AlphaMELTSBackend" for cls in type(backend).__mro__
    ):
        raw_name = "alphamelts"
    name = str(raw_name or type(backend).__name__).strip()
    getter = getattr(backend, "get_engine_version", None)
    version = ""
    if callable(getter):
        try:
            version = str(getter()).strip()
        except Exception:  # noqa: BLE001 - fail-loud config validation below
            version = "unavailable"
    if not version:
        version = "unavailable"
    return name, version


def _backend_identity_matches(
    live_identity: tuple[str, str],
    expected_identity: tuple[str, str],
    *,
    unavailable_error_cls: type[_E] = BackendUnavailableError,
) -> bool:
    live_name, _live_version = live_identity
    expected_name, _expected_corpus_version = expected_identity
    return live_name.strip().lower() == expected_name.strip().lower()


def _log_selection(
    backend,
    log_selection: Callable[[object], None] | None,
    log_message: Callable[[str], None] | None,
) -> None:
    if log_selection is not None:
        log_selection(backend)
    else:
        emit_web_engine_selection_log(backend, log_message)


def _log(log_message: Callable[[str], None] | None, message: str) -> None:
    if log_message is not None:
        log_message(message)
