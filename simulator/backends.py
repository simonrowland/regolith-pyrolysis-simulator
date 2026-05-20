"""Shared melt-backend selection and simulator construction helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping, TypeVar

from simulator.core import PyrolysisSimulator
from simulator.melt_backend.alphamelts import AlphaMELTSBackend
from simulator.melt_backend.base import StubBackend
from simulator.melt_backend.factsage import FactSAGEBackend
from simulator.melt_backend.factsage_config import (
    FactSAGEConfigError,
    load_factsage_config,
)


INELIGIBLE_ACTIVE_BACKENDS = ("vaporock", "magemin")

_E = TypeVar("_E", bound=Exception)


class BackendUnavailableError(RuntimeError):
    """Requested backend is required for this run but is unavailable."""


class BackendSelectionPolicy(Enum):
    """Explicit backend-selection semantics for each caller surface."""

    WEB_AUTODETECT = "web-autodetect"
    RUNNER_STRICT = "runner-strict"


@dataclass(frozen=True)
class SimulatorBuildConfig:
    """Inputs needed to construct a PyrolysisSimulator."""

    backend: Any
    setpoints: Mapping[str, Any]
    feedstocks: Mapping[str, Any]
    vapor_pressures: Mapping[str, Any]


def build_simulator(config: SimulatorBuildConfig) -> PyrolysisSimulator:
    """Build a simulator from pre-loaded data and an initialized backend."""

    return PyrolysisSimulator(
        config.backend,
        config.setpoints,
        config.feedstocks,
        config.vapor_pressures,
    )


def resolve_backend(
    backend_name: str,
    policy: BackendSelectionPolicy,
    *,
    unavailable_error_cls: type[_E] = BackendUnavailableError,
    log_selection: Callable[[object], None] | None = None,
    log_message: Callable[[str], None] | None = None,
    alphamelts_backend_cls: type = AlphaMELTSBackend,
    factsage_backend_cls: type = FactSAGEBackend,
    stub_backend_cls: type = StubBackend,
    factsage_config_loader: Callable[[], Mapping[str, Any]] = load_factsage_config,
    factsage_config_error_cls: type[Exception] = FactSAGEConfigError,
):
    """Resolve and initialize the active melt backend under an explicit policy."""

    if policy is BackendSelectionPolicy.WEB_AUTODETECT:
        name = (backend_name or "").strip().lower()
        return _resolve_web_autodetect(
            name,
            unavailable_error_cls=unavailable_error_cls,
            log_selection=log_selection,
            log_message=log_message,
            alphamelts_backend_cls=alphamelts_backend_cls,
            factsage_backend_cls=factsage_backend_cls,
            stub_backend_cls=stub_backend_cls,
            factsage_config_loader=factsage_config_loader,
            factsage_config_error_cls=factsage_config_error_cls,
        )
    if policy is BackendSelectionPolicy.RUNNER_STRICT:
        return _resolve_runner_strict(
            backend_name,
            unavailable_error_cls=unavailable_error_cls,
            alphamelts_backend_cls=alphamelts_backend_cls,
            factsage_backend_cls=factsage_backend_cls,
            stub_backend_cls=stub_backend_cls,
            factsage_config_loader=factsage_config_loader,
            factsage_config_error_cls=factsage_config_error_cls,
        )
    raise ValueError(f"unknown backend selection policy {policy!r}")


def emit_web_engine_selection_log(
    backend,
    log_message: Callable[[str], None] | None = None,
) -> None:
    """Emit the web's one-line engine-selection log."""

    name = type(backend).__name__
    caps = backend.capabilities()
    cap_str = ", ".join(
        f'{key}={"true" if caps.get(key) else "false"}'
        for key in ("silicate_melt", "gas_volatiles")
    )
    _log(
        log_message,
        f"engine selection: {name} (capabilities: {cap_str}) -- "
        "VapoRock/MAGEMin not eligible until kernel",
    )


def _resolve_web_autodetect(
    name: str,
    *,
    unavailable_error_cls: type[_E],
    log_selection: Callable[[object], None] | None,
    log_message: Callable[[str], None] | None,
    alphamelts_backend_cls: type,
    factsage_backend_cls: type,
    stub_backend_cls: type,
    factsage_config_loader: Callable[[], Mapping[str, Any]],
    factsage_config_error_cls: type[Exception],
):
    if name in INELIGIBLE_ACTIVE_BACKENDS:
        backend_label = "VapoRock" if name == "vaporock" else "MAGEMin"
        raise unavailable_error_cls(
            f"{backend_label} is not eligible as the active melt backend "
            "until \\goal CHEMISTRY-KERNEL-CARVE-OUT wires a multi-intent "
            "dispatcher; select alphamelts, factsage, or auto."
        )

    if name == "alphamelts":
        backend = _try_alphamelts(alphamelts_backend_cls)
        if backend is not None:
            _log_selection(backend, log_selection, log_message)
            return backend
        raise unavailable_error_cls(
            "AlphaMELTS unavailable; run install-dependencies.py"
        )

    if name == "factsage":
        backend = _try_factsage(
            factsage_backend_cls,
            factsage_config_loader,
            factsage_config_error_cls,
            log_message,
        )
        if backend is not None:
            _log_selection(backend, log_selection, log_message)
            return backend
        backend = _stub_backend(stub_backend_cls)
        _log_selection(backend, log_selection, log_message)
        return backend

    backend = _try_alphamelts(alphamelts_backend_cls)
    if backend is not None:
        _log_selection(backend, log_selection, log_message)
        return backend
    backend = _try_factsage(
        factsage_backend_cls,
        factsage_config_loader,
        factsage_config_error_cls,
        log_message,
    )
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
    factsage_backend_cls: type,
    stub_backend_cls: type,
    factsage_config_loader: Callable[[], Mapping[str, Any]],
    factsage_config_error_cls: type[Exception],
):
    if name in ("", "stub"):
        return _stub_backend(stub_backend_cls)
    if name == "auto":
        raise unavailable_error_cls(
            "auto backend selection is unavailable under runner-strict; "
            "select stub, alphamelts, or factsage"
        )
    if name == "alphamelts":
        backend = _try_alphamelts(alphamelts_backend_cls)
        if backend is not None:
            return backend
        raise unavailable_error_cls(
            "AlphaMELTS unavailable; rerun with --backend=stub or "
            "install via install-dependencies.py"
        )
    if name == "factsage":
        try:
            config = factsage_config_loader()
        except factsage_config_error_cls as exc:
            raise unavailable_error_cls(
                f"FactSAGE config error: {exc}; rerun with --backend=stub"
            ) from exc
        backend = factsage_backend_cls()
        if backend.initialize(config) and backend.is_available():
            return backend
        raise unavailable_error_cls(
            "FactSAGE unavailable; rerun with --backend=stub"
        )
    raise unavailable_error_cls(f"unknown backend {name!r}")


def _try_alphamelts(alphamelts_backend_cls: type):
    backend = alphamelts_backend_cls()
    if backend.initialize({}) and backend.is_available():
        return backend
    return None


def _try_factsage(
    factsage_backend_cls: type,
    factsage_config_loader: Callable[[], Mapping[str, Any]],
    factsage_config_error_cls: type[Exception],
    log_message: Callable[[str], None] | None,
):
    try:
        config = factsage_config_loader()
    except factsage_config_error_cls as exc:
        _log(log_message, f"FactSAGE config error: {exc}")
        config = {}
    backend = factsage_backend_cls()
    if backend.initialize(config) and backend.is_available():
        return backend
    return None


def _stub_backend(stub_backend_cls: type):
    backend = stub_backend_cls()
    backend.initialize({})
    return backend


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
