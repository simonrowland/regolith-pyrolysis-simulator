"""Host-local assessment receipts for reduced-real engine bindings."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


RECEIPT_SCHEMA_VERSION = 1
RECEIPT_FILENAME = "engines.local.binding-admission.json"
REAL_BINDING_PIN_DIRECTORY = "reduced_real_binding_pins"


@dataclass(frozen=True)
class BindingIdentity:
    engine_id: str
    model_id: str
    binding_revision: str
    transport: str

    def __post_init__(self) -> None:
        for name in ("engine_id", "model_id", "binding_revision", "transport"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"binding identity {name} must be non-empty")

    def as_dict(self) -> dict[str, str]:
        return {
            "engine_id": self.engine_id,
            "model_id": self.model_id,
            "binding_revision": self.binding_revision,
            "transport": self.transport,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BindingIdentity":
        return cls(
            engine_id=str(value.get("engine_id") or ""),
            model_id=str(value.get("model_id") or ""),
            binding_revision=str(value.get("binding_revision") or ""),
            transport=str(value.get("transport") or ""),
        )

    @property
    def producer_transport(self) -> str:
        return f"{self.engine_id}/{self.transport}"


@dataclass(frozen=True)
class BindingAssessmentCandidate:
    identity: BindingIdentity
    artifact: str
    simulator: Any
    result: Any
    provenance: Mapping[str, Any]
    error: str | None = None


@dataclass(frozen=True)
class BindingAssessmentResult:
    identity: BindingIdentity
    status: str
    reason: str | None


class EngineBindingAdmissionError(RuntimeError):
    """Typed refusal when a host-local assessment receipt cannot authorize use."""

    def __init__(self, identity: BindingIdentity, reason: str) -> None:
        self.identity = identity
        self.reason = reason
        super().__init__(
            f"engine binding {identity.producer_transport} is not admitted: {reason}"
        )


_SELECTED_PRODUCER_IDENTITIES = {
    "builtin-vapor-pressure": BindingIdentity(
        "builtin-vapor-pressure",
        "builtin-antoine-ellingham",
        "builtin-vapor-pressure-r1",
        "native",
    ),
    "openimcc": BindingIdentity(
        "openimcc",
        "SF04",
        "openimcc-sf04-r1",
        "python_api",
    ),
    "sulfsat": BindingIdentity(
        "sulfsat",
        "SulfSatGate",
        "sulfsat-r1",
        "python_api",
    ),
}


def binding_receipt_path(config_path: str | Path | None = None) -> Path:
    if config_path is None:
        from simulator.engine_local_config import config_path as local_config_path

        config_path = local_config_path()
    return Path(config_path).with_name(RECEIPT_FILENAME)


def binding_identity_for_cached_real(config: Any) -> BindingIdentity:
    from simulator.config import (
        DEFAULT_ALPHAMELTS_MODEL,
        resolve_alphamelts_python_api_model,
        resolve_alphamelts_subprocess_model,
    )

    name = str(getattr(config, "authorized_backend_name", "")).strip().lower()
    leaf = name.rsplit(".", 1)[-1]
    family = getattr(getattr(config, "authorized_backend_family", None), "name", "")
    model = str(getattr(config, "authorized_model", "") or "").strip()
    transport = str(getattr(config, "authorized_mode", "") or "").strip()
    if leaf in {"alphamelts", "alphameltsbackend"} or family == "ALPHAMELTS":
        transport = transport or "subprocess"
        model = model or DEFAULT_ALPHAMELTS_MODEL
        if transport == "python_api":
            model, _ = resolve_alphamelts_python_api_model(model)
        elif transport == "subprocess":
            model, _ = resolve_alphamelts_subprocess_model(model)
        return BindingIdentity(
            "alphamelts",
            model,
            "alphamelts-r1",
            "python_api" if transport == "python_api" else "subprocess",
        )
    if leaf in {"thermoengine", "thermoenginebackend"} or family == "THERMOENGINE":
        model = model or DEFAULT_ALPHAMELTS_MODEL
        return BindingIdentity(
            "thermoengine",
            model,
            "thermoengine-r1",
            "native",
        )
    raise ValueError(f"no reduced-real binding identity for backend {name!r}")


def binding_identity_for_gate_fallback() -> BindingIdentity:
    return BindingIdentity(
        "magemin",
        "ig",
        "magemin-ig-r1",
        "subprocess",
    )


def binding_identity_for_backend(backend: Any) -> BindingIdentity | None:
    if backend is None:
        return None
    family = getattr(getattr(backend, "real_backend_family", None), "name", "")
    raw_name = str(getattr(backend, "name", "") or type(backend).__name__).lower()
    if family == "ALPHAMELTS" or "alphamelts" in raw_name:
        model = str(
            getattr(backend, "_model", getattr(backend, "model", "")) or ""
        )
        mode = str(
            getattr(backend, "_mode", getattr(backend, "mode", "subprocess"))
            or "subprocess"
        )
        from simulator.config import (
            DEFAULT_ALPHAMELTS_MODEL,
            resolve_alphamelts_python_api_model,
            resolve_alphamelts_subprocess_model,
        )

        model = model or DEFAULT_ALPHAMELTS_MODEL
        if mode == "python_api":
            model, _ = resolve_alphamelts_python_api_model(model)
        else:
            model, _ = resolve_alphamelts_subprocess_model(model)
            mode = "subprocess"
        return BindingIdentity("alphamelts", model, "alphamelts-r1", mode)
    if family == "THERMOENGINE" or "thermoengine" in raw_name:
        from simulator.config import DEFAULT_ALPHAMELTS_MODEL

        model = str(
            getattr(backend, "_model", getattr(backend, "model", ""))
            or DEFAULT_ALPHAMELTS_MODEL
        )
        return BindingIdentity("thermoengine", model, "thermoengine-r1", "native")
    if family == "MAGEMIN" or "magemin" in raw_name:
        return binding_identity_for_gate_fallback()
    return None


def current_backend_provenance(
    identity: BindingIdentity,
    backend: Any,
) -> dict[str, Any]:
    provenance = install_provenance(identity, runtime_backend=backend)
    getter = getattr(backend, "get_engine_version", None)
    provenance["runtime_version"] = version_getter_provenance(getter)
    return provenance


def publish_live_backend_admission(backend: Any) -> None:
    identity = binding_identity_for_backend(backend)
    if identity is None:
        return
    try:
        provenance = current_backend_provenance(identity, backend)
        authorize_binding(identity, provenance)
        fingerprint = admission_fingerprint(identity, provenance)
    except Exception as exc:  # noqa: BLE001 - an unadmitted backend still computes live
        try:
            backend._engine_binding_admission_error = exc
            backend._engine_binding_admission_fingerprint = None
        except (AttributeError, TypeError):
            pass
        return
    try:
        backend._engine_binding_admission_error = None
        backend._engine_binding_admission_identity = identity.as_dict()
        backend._engine_binding_admission_fingerprint = fingerprint
    except (AttributeError, TypeError):
        pass


def cached_real_provenance(config: Any, live_backend: Any | None) -> dict[str, Any]:
    identity = binding_identity_for_cached_real(config)
    if live_backend is not None:
        getter = getattr(live_backend, "get_engine_version", None)
    else:
        version = str(
            getattr(config, "authorized_backend_version", "") or ""
        ).strip()
        getter = lambda: version
    provenance = install_provenance(identity, runtime_backend=live_backend)
    provenance["runtime_version"] = version_getter_provenance(getter)
    return provenance


def current_cached_real_provenance(
    sim: Any,
    identity: BindingIdentity,
) -> dict[str, Any]:
    backend = getattr(sim, "backend", None)
    config = getattr(backend, "config", None)
    live_backend = getattr(backend, "_live_backend", None)
    if identity.engine_id == "magemin":
        from simulator.chemistry.kernel import ChemistryIntent

        registry = getattr(sim, "_chem_registry", None)
        provider = (
            registry.fallback_for(ChemistryIntent.GATE_LIQUID_FRACTION)
            if registry is not None
            else None
        )
        live_backend = getattr(provider, "_backend", None)
        provenance = install_provenance(identity, runtime_backend=live_backend)
        getter = getattr(live_backend, "get_engine_version", None)
        provenance["runtime_version"] = version_getter_provenance(getter)
        return provenance
    if config is None:
        raise ValueError("cached-real binding configuration is unavailable")
    actual_identity = binding_identity_for_cached_real(config)
    if actual_identity != identity:
        raise ValueError("cached-real binding identity changed")
    return cached_real_provenance(config, live_backend)


def selected_binding_provenance(
    identity: BindingIdentity,
    sim: Any,
    backend: Any | None = None,
) -> dict[str, Any]:
    provenance = install_provenance(
        identity,
        runtime_backend=backend,
        simulator=sim,
    )
    if identity.engine_id in {"alphamelts", "thermoengine", "magemin"}:
        getter = getattr(backend, "get_engine_version", None)
        provenance["runtime_version"] = version_getter_provenance(getter)
    elif identity.engine_id == "openimcc":
        from simulator.melt_backend.openimcc_bridge import _openimcc

        provenance["runtime_version"] = version_getter_provenance(
            lambda: getattr(_openimcc, "__version__")
        )
    elif identity.engine_id == "sulfsat":
        gate = getattr(sim, "_sulfsat_gate", None)
        package_getter = getattr(gate, "package_version", None)
        calibration_getter = getattr(gate, "calibration_version", None)
        provenance["runtime_version"] = version_getter_provenance(
            package_getter
            if callable(package_getter)
            else lambda: (_ for _ in ()).throw(
                AttributeError("SulfSat package version getter missing")
            )
        )
        provenance["calibration_version"] = version_getter_provenance(
            calibration_getter
            if callable(calibration_getter)
            else lambda: (_ for _ in ()).throw(
                AttributeError("SulfSat calibration version getter missing")
            )
        )
    return provenance


def pt1_version_provenance(getter: Any) -> str:
    state = version_getter_provenance(getter)
    if state["tag"] == "unknown":
        return canonical_json_bytes(state).decode("utf-8")
    return str(state["value"])


def admission_fingerprint(
    identity: BindingIdentity,
    provenance: Mapping[str, Any],
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {"identity": identity.as_dict(), "provenance": dict(provenance)}
        )
    ).hexdigest()


def _live_selected_binding_eligibility(
    sim: Any,
    *,
    artifact: str,
    provider_role: str | None,
) -> bool:
    try:
        identities = selected_binding_identities(
            sim,
            artifact=artifact,
            provider_role=provider_role,
        )
    except Exception:  # noqa: BLE001 - unknown contributors cannot be cached
        identity = _SELECTED_PRODUCER_IDENTITIES["builtin-vapor-pressure"]
        return _record_live_cache_refusal(
            sim,
            identity,
            "binding dependency selection unverifiable",
        )
    eligible = True
    for identity in identities:
        try:
            provenance = selected_binding_provenance(identity, sim)
        except Exception:  # noqa: BLE001 - live compute remains available
            _record_live_cache_refusal(
                sim,
                identity,
                "install provenance unverifiable",
            )
            eligible = False
            continue
        if not live_cache_eligibility(sim, identity, provenance):
            eligible = False
    return eligible


def _live_with_selected_bindings(
    sim: Any,
    primary_eligible: bool,
    *,
    artifact: str,
    provider_role: str | None,
) -> bool:
    selected_eligible = _live_selected_binding_eligibility(
        sim,
        artifact=artifact,
        provider_role=provider_role,
    )
    return primary_eligible and selected_eligible


def _authorize_selected_bindings(
    sim: Any,
    *,
    artifact: str,
    provider_role: str | None,
) -> None:
    try:
        identities = selected_binding_identities(
            sim,
            artifact=artifact,
            provider_role=provider_role,
        )
    except Exception as exc:  # noqa: BLE001 - replay fails closed
        identity = _SELECTED_PRODUCER_IDENTITIES["builtin-vapor-pressure"]
        raise EngineBindingAdmissionError(
            identity,
            "binding dependency selection unverifiable",
        ) from exc
    for identity in identities:
        try:
            provenance = selected_binding_provenance(identity, sim)
        except Exception as exc:  # noqa: BLE001 - replay fails closed
            raise EngineBindingAdmissionError(
                identity,
                "install provenance unverifiable",
            ) from exc
        authorize_binding(identity, provenance)


def cached_real_live_cache_eligibility(
    sim: Any,
    *,
    provider_role: str | None = None,
    artifact: str = "equilibrium_post_record",
) -> bool:
    backend = getattr(sim, "backend", None)
    config = getattr(backend, "config", None)
    if config is None:
        return _live_selected_binding_eligibility(
            sim,
            artifact=artifact,
            provider_role=provider_role,
        )
    identity = (
        binding_identity_for_gate_fallback()
        if provider_role == "fallback"
        else binding_identity_for_cached_real(config)
    )
    try:
        provenance = current_cached_real_provenance(sim, identity)
    except Exception:  # noqa: BLE001 - live compute stays fail-open
        _record_live_cache_refusal(
            sim,
            identity,
            "install provenance unverifiable",
        )
        _live_selected_binding_eligibility(
            sim,
            artifact=artifact,
            provider_role=provider_role,
        )
        return False
    published = getattr(backend, "_admitted_bindings", {}).get(
        identity.producer_transport
    )
    if provider_role == "fallback":
        from simulator.chemistry.kernel import ChemistryIntent

        registry = getattr(sim, "_chem_registry", None)
        provider = (
            registry.fallback_for(ChemistryIntent.GATE_LIQUID_FRACTION)
            if registry is not None
            else None
        )
        provider_backend = getattr(provider, "_backend", None)
        initialized = bool(getattr(provider, "_backend_initialised", False))
        checker = getattr(provider_backend, "is_available", None)
        initialized = initialized and callable(checker) and bool(checker())
        if published is None and initialized:
            published = admission_fingerprint(identity, provenance)
            backend._admitted_bindings[identity.producer_transport] = published
        if not initialized:
            try:
                authorize_binding(identity, provenance)
            except EngineBindingAdmissionError:
                eligible = live_cache_eligibility(
                    sim,
                    identity,
                    provenance,
                )
                return _live_with_selected_bindings(
                    sim,
                    eligible,
                    artifact=artifact,
                    provider_role=provider_role,
                )
            return _live_with_selected_bindings(
                sim,
                False,
                artifact=artifact,
                provider_role=provider_role,
            )
    eligible = live_cache_eligibility(
        sim,
        identity,
        provenance,
        published_fingerprint=published,
        require_published_fingerprint=True,
    )
    return _live_with_selected_bindings(
        sim,
        eligible,
        artifact=artifact,
        provider_role=provider_role,
    )


def live_binding_cache_eligibility(
    sim: Any,
    *,
    provider_role: str | None = None,
    artifact: str = "equilibrium_post_record",
) -> bool:
    backend = getattr(sim, "backend", None)
    if getattr(backend, "config", None) is not None and hasattr(
        backend,
        "_admitted_bindings",
    ):
        return cached_real_live_cache_eligibility(
            sim,
            provider_role=provider_role,
            artifact=artifact,
        )
    if provider_role == "fallback":
        from simulator.chemistry.kernel import ChemistryIntent

        registry = getattr(sim, "_chem_registry", None)
        provider = (
            registry.fallback_for(ChemistryIntent.GATE_LIQUID_FRACTION)
            if registry is not None
            else None
        )
        live_backend = getattr(provider, "_backend", None)
        if provider is None or live_backend is None:
            return _live_selected_binding_eligibility(
                sim,
                artifact=artifact,
                provider_role=provider_role,
            )
        try:
            identity = binding_identity_for_backend(live_backend)
        except Exception:  # noqa: BLE001 - an unknown identity cannot be cached
            return _live_selected_binding_eligibility(
                sim,
                artifact=artifact,
                provider_role=provider_role,
            )
        if identity is None:
            return _live_selected_binding_eligibility(
                sim,
                artifact=artifact,
                provider_role=provider_role,
            )
        try:
            provenance = current_backend_provenance(identity, live_backend)
        except Exception:  # noqa: BLE001 - live compute stays fail-open
            _record_live_cache_refusal(
                sim,
                identity,
                "install provenance unverifiable",
            )
            _live_selected_binding_eligibility(
                sim,
                artifact=artifact,
                provider_role=provider_role,
            )
            return False
        initialized = bool(getattr(provider, "_backend_initialised", False))
        checker = getattr(live_backend, "is_available", None)
        initialized = initialized and callable(checker) and bool(checker())
        if not initialized:
            try:
                authorize_binding(identity, provenance)
            except EngineBindingAdmissionError:
                eligible = live_cache_eligibility(sim, identity, provenance)
                return _live_with_selected_bindings(
                    sim,
                    eligible,
                    artifact=artifact,
                    provider_role=provider_role,
                )
            return _live_with_selected_bindings(
                sim,
                False,
                artifact=artifact,
                provider_role=provider_role,
            )
        publish_live_backend_admission(live_backend)
        published = getattr(
            live_backend,
            "_engine_binding_admission_fingerprint",
            None,
        )
        eligible = live_cache_eligibility(
            sim,
            identity,
            provenance,
            published_fingerprint=published,
            require_published_fingerprint=True,
        )
        return _live_with_selected_bindings(
            sim,
            eligible,
            artifact=artifact,
            provider_role=provider_role,
        )
    try:
        identity = binding_identity_for_backend(backend)
    except Exception:  # noqa: BLE001 - an unknown identity cannot be cached
        return _live_selected_binding_eligibility(
            sim,
            artifact=artifact,
            provider_role=provider_role,
        )
    if identity is None:
        return _live_selected_binding_eligibility(
            sim,
            artifact=artifact,
            provider_role=provider_role,
        )
    try:
        provenance = current_backend_provenance(identity, backend)
    except Exception:  # noqa: BLE001 - live compute stays fail-open
        _record_live_cache_refusal(
            sim,
            identity,
            "install provenance unverifiable",
        )
        _live_selected_binding_eligibility(
            sim,
            artifact=artifact,
            provider_role=provider_role,
        )
        return False
    eligible = live_cache_eligibility(
        sim,
        identity,
        provenance,
        published_fingerprint=getattr(
            backend,
            "_engine_binding_admission_fingerprint",
            None,
        ),
        require_published_fingerprint=True,
    )
    return _live_with_selected_bindings(
        sim,
        eligible,
        artifact=artifact,
        provider_role=provider_role,
    )


def gate_curve_memo_eligibility(sim: Any, curve: Mapping[str, Any]) -> bool:
    from simulator.reduced_real_determinism import (
        _gate_curve_provider_role,
        _is_cacheable_gate_curve,
    )

    if not _is_cacheable_gate_curve(curve):
        return False
    provider_role = _gate_curve_provider_role(curve)
    return live_binding_cache_eligibility(
        sim,
        provider_role=provider_role,
        artifact="freeze_gate_curve",
    )


def authorize_cached_real_replay(
    sim: Any,
    *,
    provider_role: str | None = None,
    artifact: str = "equilibrium_post_record",
) -> Mapping[str, Any]:
    backend = getattr(sim, "backend", None)
    config = getattr(backend, "config", None)
    if config is None:
        raise RuntimeError("cached-real replay has no binding configuration")
    identity = (
        binding_identity_for_gate_fallback()
        if provider_role == "fallback"
        else binding_identity_for_cached_real(config)
    )
    try:
        provenance = current_cached_real_provenance(sim, identity)
    except Exception as exc:  # noqa: BLE001 - replay refusal is typed
        raise EngineBindingAdmissionError(
            identity,
            "install provenance unverifiable",
        ) from exc
    entry = authorize_binding(identity, provenance)
    _authorize_selected_bindings(
        sim,
        artifact=artifact,
        provider_role=provider_role,
    )
    return entry


def authorize_sim_binding_replay(
    sim: Any,
    *,
    provider_role: str | None = None,
    artifact: str = "equilibrium_post_record",
) -> None:
    backend = getattr(sim, "backend", None)
    if getattr(backend, "config", None) is not None and hasattr(
        backend,
        "_admitted_bindings",
    ):
        authorize_cached_real_replay(
            sim,
            provider_role=provider_role,
            artifact=artifact,
        )
        return
    if provider_role == "fallback":
        from simulator.chemistry.kernel import ChemistryIntent

        registry = getattr(sim, "_chem_registry", None)
        provider = (
            registry.fallback_for(ChemistryIntent.GATE_LIQUID_FRACTION)
            if registry is not None
            else None
        )
        backend = getattr(provider, "_backend", None)
        identity = binding_identity_for_gate_fallback()
    else:
        identity = binding_identity_for_backend(backend)
    if identity is None:
        _authorize_selected_bindings(
            sim,
            artifact=artifact,
            provider_role=provider_role,
        )
        return
    try:
        provenance = current_backend_provenance(identity, backend)
    except Exception as exc:  # noqa: BLE001 - replay refusal is typed
        raise EngineBindingAdmissionError(
            identity,
            "install provenance unverifiable",
        ) from exc
    authorize_binding(identity, provenance)
    _authorize_selected_bindings(
        sim,
        artifact=artifact,
        provider_role=provider_role,
    )


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _projection(candidate: BindingAssessmentCandidate) -> dict[str, Any]:
    from simulator.reduced_real_determinism import (
        canonical_replay_output_projection,
    )

    return canonical_replay_output_projection(
        candidate.artifact,
        candidate.simulator,
        candidate.result,
    )


def known_binding_assessment_targets() -> tuple[tuple[BindingIdentity, str], ...]:
    from simulator.config import (
        DEFAULT_ALPHAMELTS_MODEL,
        resolve_alphamelts_python_api_model,
        resolve_alphamelts_subprocess_model,
    )

    subprocess_model, _ = resolve_alphamelts_subprocess_model(None)
    python_model, _ = resolve_alphamelts_python_api_model(None)
    return (
        (
            BindingIdentity("alphamelts", subprocess_model, "alphamelts-r1", "subprocess"),
            "equilibrium_post_record",
        ),
        (
            BindingIdentity("alphamelts", subprocess_model, "alphamelts-r1", "subprocess"),
            "freeze_gate_curve",
        ),
        (
            BindingIdentity("alphamelts", python_model, "alphamelts-r1", "python_api"),
            "equilibrium_post_record",
        ),
        (
            BindingIdentity("alphamelts", python_model, "alphamelts-r1", "python_api"),
            "freeze_gate_curve",
        ),
        (
            BindingIdentity("thermoengine", DEFAULT_ALPHAMELTS_MODEL, "thermoengine-r1", "native"),
            "equilibrium_post_record",
        ),
        (binding_identity_for_gate_fallback(), "freeze_gate_curve"),
        (
            _SELECTED_PRODUCER_IDENTITIES["builtin-vapor-pressure"],
            "equilibrium_post_record",
        ),
        (
            _SELECTED_PRODUCER_IDENTITIES["openimcc"],
            "equilibrium_post_record",
        ),
        (
            _SELECTED_PRODUCER_IDENTITIES["sulfsat"],
            "equilibrium_post_record",
        ),
    )


def selected_binding_identities(
    sim: Any,
    *,
    artifact: str,
    provider_role: str | None = None,
) -> tuple[BindingIdentity, ...]:
    from simulator.reduced_real_determinism import record_binding_producer_ids

    producer_ids = record_binding_producer_ids(
        sim,
        artifact=artifact,
        provider_role=provider_role,
    )
    return tuple(
        _SELECTED_PRODUCER_IDENTITIES[producer_id]
        for producer_id in producer_ids
    )


def assessment_candidates(
    pin_directory: str | Path,
) -> list[BindingAssessmentCandidate]:
    candidates: list[BindingAssessmentCandidate] = []
    for identity, artifact in known_binding_assessment_targets():
        pin_path, pin = _pin_for(identity, artifact, Path(pin_directory))
        provenance = install_provenance(identity)
        if pin is None:
            candidates.append(
                BindingAssessmentCandidate(
                    identity,
                    artifact,
                    None,
                    None,
                    provenance,
                )
            )
            continue
        probe = pin.get("probe") if isinstance(pin, Mapping) else None
        if not isinstance(probe, Mapping):
            candidates.append(
                BindingAssessmentCandidate(
                    identity,
                    artifact,
                    None,
                    None,
                    provenance,
                    error="reviewed pin is missing its probe object",
                )
            )
            continue
        try:
            simulator, result, provenance = _run_binding_probe(
                identity,
                artifact,
                probe,
            )
        except Exception as exc:  # noqa: BLE001 - failed probes never admit
            candidates.append(
                BindingAssessmentCandidate(
                    identity,
                    artifact,
                    None,
                    None,
                    provenance,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
        else:
            candidates.append(
                BindingAssessmentCandidate(
                    identity,
                    artifact,
                    simulator,
                    result,
                    provenance,
                )
            )
    return candidates


def _run_binding_probe(
    identity: BindingIdentity,
    artifact: str,
    probe: Mapping[str, Any],
) -> tuple[Any, Any, dict[str, Any]]:
    from simulator.backends import (
        BackendSelectionPolicy,
        SimulatorBuildConfig,
        build_simulator,
        resolve_backend,
    )
    from simulator.chemistry.kernel import ChemistryIntent
    from simulator.chemistry.kernel.account_filters import (
        build_provider_account_view,
    )
    from simulator.chemistry.kernel.dto import IntentRequest
    from simulator.config import load_config_bundle
    from simulator.state import CampaignPhase

    active_backend = str(probe.get("active_backend") or "").strip()
    backend_config: dict[str, Any] = {}
    if identity.engine_id == "alphamelts":
        active_backend = "alphamelts"
        backend_config = {"mode": identity.transport, "model": identity.model_id}
    elif identity.engine_id == "thermoengine":
        active_backend = "thermoengine"
        backend_config = {"model": identity.model_id}
    elif not active_backend:
        active_backend = "internal-analytical"
    backend = resolve_backend(
        active_backend,
        BackendSelectionPolicy.RUNNER_STRICT,
        backend_config=backend_config,
    )
    if identity.engine_id == "alphamelts":
        resolved_identity = binding_identity_for_backend(backend)
        if resolved_identity != identity:
            raise ValueError(
                "resolved backend identity mismatch for binding probe: "
                f"requested {identity.producer_transport}/{identity.model_id}, "
                f"got {resolved_identity!r}"
            )
    bundle = load_config_bundle()
    setpoints = dict(bundle.setpoints)
    if "high_t_melt_activity" in probe:
        setpoints["high_t_melt_activity"] = probe["high_t_melt_activity"]
    simulator = build_simulator(
        SimulatorBuildConfig(
            backend=backend,
            setpoints=setpoints,
            feedstocks=bundle.feedstocks,
            vapor_pressures=bundle.vapor_pressures,
        )
    )
    simulator.load_batch(
        str(probe.get("feedstock_id") or "lunar_mare_low_ti"),
        mass_kg=float(probe.get("mass_kg", 1000.0)),
    )
    campaign_name = str(probe.get("campaign") or "C2A_STAGED")
    simulator.start_campaign(CampaignPhase[campaign_name])
    if probe.get("temperature_C") is not None:
        simulator.melt.temperature_C = float(probe["temperature_C"])

    runtime_backend = backend
    if identity.engine_id == "magemin":
        from engines.magemin.provider import MAGEMinShadowProvider

        provider = MAGEMinShadowProvider()
        original_dispatch = simulator._dispatch_only

        def dispatch_magemin(intent: Any, **kwargs: Any) -> Any:
            if intent != ChemistryIntent.GATE_LIQUID_FRACTION:
                return original_dispatch(intent, **kwargs)
            profile = provider.capability_profile()
            request = IntentRequest(
                intent=intent,
                account_view=build_provider_account_view(
                    simulator.atom_ledger,
                    profile.declared_accounts,
                    simulator.species_formula_registry,
                ),
                temperature_C=float(simulator.melt.temperature_C),
                pressure_bar=float(simulator.melt.p_total_mbar) / 1000.0,
                fO2_log=kwargs.get("fO2_log"),
                fe_redox_policy=str(kwargs.get("fe_redox_policy", "intrinsic")),
                control_inputs=kwargs.get("control_inputs", {}),
            )
            return provider.dispatch(request)

        simulator._dispatch_only = dispatch_magemin
        result = simulator._freeze_gate_curve()
        runtime_backend = getattr(provider, "_backend", None)
    elif artifact == "freeze_gate_curve":
        result = simulator._freeze_gate_curve()
    elif artifact == "equilibrium_post_record":
        result = simulator._get_equilibrium()
    else:
        raise ValueError(f"unsupported binding probe artifact {artifact!r}")

    if identity.engine_id in _SELECTED_PRODUCER_IDENTITIES:
        from simulator.reduced_real_determinism import record_binding_producer_ids

        selected_ids = record_binding_producer_ids(
            simulator,
            artifact=artifact,
        )
        if identity.engine_id not in selected_ids:
            raise ValueError(
                f"probe did not select {identity.producer_transport}"
            )
        if identity.engine_id == "openimcc":
            diagnostic = getattr(
                simulator,
                "_last_vapor_pressure_diagnostic",
                {},
            )
            authority = (
                diagnostic.get("high_t_melt_activity", {})
                if isinstance(diagnostic, Mapping)
                else {}
            )
            if (
                not isinstance(authority, Mapping)
                or authority.get("fallback") is True
                or authority.get("provider") != "openimcc"
            ):
                raise ValueError("OpenIMCC probe produced fallback output")
        elif identity.engine_id == "sulfsat":
            gate = getattr(simulator, "_sulfsat_gate", None)
            sulfur_result = getattr(result, "sulfur_saturation", None)
            if (
                gate is None
                or not gate.is_available()
                or sulfur_result is None
                or sulfur_result.calibration_status == "unavailable"
            ):
                raise ValueError("SulfSat probe did not produce an available result")

    provenance = selected_binding_provenance(identity, simulator, runtime_backend)
    return simulator, result, provenance


def _pin_for(
    identity: BindingIdentity,
    artifact: str,
    pin_directory: Path,
) -> tuple[Path | None, Mapping[str, Any] | None]:
    if not pin_directory.is_dir():
        return None, None
    matches: list[tuple[Path, Mapping[str, Any]]] = []
    for path in sorted(pin_directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, Mapping):
            continue
        try:
            pin_identity = BindingIdentity.from_mapping(raw.get("identity", {}))
        except (TypeError, ValueError):
            continue
        if pin_identity == identity and raw.get("artifact") == artifact:
            matches.append((path, raw))
    if len(matches) > 1:
        raise ValueError(
            f"multiple reviewed pins for {identity.producer_transport}"
        )
    return matches[0] if matches else (None, None)


def assess_bindings(
    candidates: Sequence[BindingAssessmentCandidate],
    *,
    pin_directory: str | Path,
    receipt_path: str | Path,
) -> list[BindingAssessmentResult]:
    """Compare live outputs through the canonical projection and write receipt."""
    pin_root = Path(pin_directory)
    entries: list[dict[str, Any]] = []
    results: list[BindingAssessmentResult] = []
    for candidate in candidates:
        pin_path, pin = _pin_for(
            candidate.identity,
            candidate.artifact,
            pin_root,
        )
        status = "failed"
        reason: str | None = None
        comparison_status = "unmatched"
        projected: dict[str, Any] | None = None
        if pin is None:
            reason = (
                "no reviewed pins for "
                f"{candidate.identity.producer_transport}"
            )
            comparison_status = "no_reviewed_pins"
        elif candidate.error is not None:
            reason = f"assessment probe failed: {candidate.error}"
            comparison_status = "probe_failed"
        else:
            try:
                projected = _projection(candidate)
                expected = pin.get("projection")
                if not isinstance(expected, Mapping):
                    raise ValueError("reviewed pin projection must be a mapping")
                if canonical_json_bytes(projected) == canonical_json_bytes(expected):
                    comparison_status = "matched"
                    if candidate.provenance.get(
                        "binding_provenance_verifiable"
                    ) is True:
                        status = "admitted"
                    else:
                        reason = "install provenance unverifiable"
                else:
                    reason = "canonical output projection differs from reviewed pin"
            except Exception as exc:  # noqa: BLE001 - assessment fails closed
                reason = f"assessment projection failed: {type(exc).__name__}: {exc}"
                comparison_status = "projection_failed"
        entry = {
            "identity": candidate.identity.as_dict(),
            "status": status,
            "reason": reason,
            "artifact": candidate.artifact,
            "comparison": {
                "status": comparison_status,
                "pin": str(pin_path) if pin_path is not None else None,
                "projection_sha256": (
                    hashlib.sha256(canonical_json_bytes(projected)).hexdigest()
                    if projected is not None
                    else None
                ),
            },
            "provenance": dict(candidate.provenance),
        }
        entries.append(entry)
        results.append(
            BindingAssessmentResult(candidate.identity, status, reason)
        )
    grouped_entries: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for entry in entries:
        identity = BindingIdentity.from_mapping(entry["identity"])
        grouped_entries.setdefault(
            (
                identity.engine_id,
                identity.model_id,
                identity.binding_revision,
                identity.transport,
            ),
            [],
        ).append(entry)
    receipt_entries: list[dict[str, Any]] = []
    for grouped in grouped_entries.values():
        if len(grouped) == 1:
            receipt_entries.append(grouped[0])
            continue
        comparisons = [
            {
                "artifact": entry["artifact"],
                **dict(entry["comparison"]),
            }
            for entry in grouped
        ]
        provenances = [canonical_json_bytes(entry["provenance"]) for entry in grouped]
        provenance_matches = len(set(provenances)) == 1
        admitted = provenance_matches and all(
            entry["status"] == "admitted"
            and entry["comparison"]["status"] == "matched"
            for entry in grouped
        )
        reasons = list(
            dict.fromkeys(
                str(entry["reason"])
                for entry in grouped
                if entry.get("reason")
            )
        )
        if not provenance_matches:
            reasons.append("candidate provenance differs within binding")
        receipt_entries.append(
            {
                "identity": grouped[0]["identity"],
                "status": "admitted" if admitted else "failed",
                "reason": "; ".join(reasons) or None,
                "artifacts": [entry["artifact"] for entry in grouped],
                "comparison": {
                    "status": "matched" if admitted else "failed",
                    "artifacts": comparisons,
                },
                "provenance": grouped[0]["provenance"],
            }
        )
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        "entries": receipt_entries,
    }
    _write_receipt(Path(receipt_path), receipt)
    return results


def _write_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(
        receipt,
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    ) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def authorize_binding(
    identity: BindingIdentity,
    current_provenance: Mapping[str, Any],
    *,
    receipt_path: str | Path | None = None,
) -> Mapping[str, Any]:
    path = Path(receipt_path) if receipt_path is not None else binding_receipt_path()
    if not path.is_file():
        raise EngineBindingAdmissionError(identity, "receipt missing")
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EngineBindingAdmissionError(
            identity,
            f"receipt unreadable: {type(exc).__name__}",
        ) from exc
    if (
        not isinstance(receipt, Mapping)
        or receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION
        or not isinstance(receipt.get("entries"), list)
    ):
        raise EngineBindingAdmissionError(identity, "receipt invalid")
    exact: list[Mapping[str, Any]] = []
    same_binding: list[Mapping[str, Any]] = []
    for entry in receipt["entries"]:
        if not isinstance(entry, Mapping):
            continue
        try:
            entry_identity = BindingIdentity.from_mapping(entry.get("identity", {}))
        except (TypeError, ValueError):
            continue
        if entry_identity == identity:
            exact.append(entry)
        elif (
            entry_identity.engine_id == identity.engine_id
            and entry_identity.model_id == identity.model_id
            and entry_identity.binding_revision == identity.binding_revision
        ):
            same_binding.append(entry)
    if not exact:
        reason = "transport mismatch" if same_binding else "binding missing from receipt"
        raise EngineBindingAdmissionError(identity, reason)
    if len(exact) != 1:
        raise EngineBindingAdmissionError(identity, "receipt has duplicate entries")
    entry = exact[0]
    if entry.get("status") != "admitted":
        raise EngineBindingAdmissionError(
            identity,
            str(entry.get("reason") or "comparison failed"),
        )
    comparison = entry.get("comparison")
    if not isinstance(comparison, Mapping) or comparison.get("status") != "matched":
        raise EngineBindingAdmissionError(identity, "comparison failed")
    provenance = entry.get("provenance")
    if not isinstance(provenance, Mapping):
        raise EngineBindingAdmissionError(identity, "receipt provenance missing")
    if provenance.get("binding_provenance_verifiable") is not True:
        raise EngineBindingAdmissionError(
            identity,
            "install provenance unverifiable",
        )
    if canonical_json_bytes(dict(provenance)) != canonical_json_bytes(
        dict(current_provenance)
    ):
        raise EngineBindingAdmissionError(identity, "receipt stale")
    return entry


def live_cache_eligibility(
    sim: Any,
    identity: BindingIdentity,
    current_provenance: Mapping[str, Any],
    *,
    receipt_path: str | Path | None = None,
    published_fingerprint: str | None = None,
    require_published_fingerprint: bool = False,
) -> bool:
    try:
        authorize_binding(
            identity,
            current_provenance,
            receipt_path=receipt_path,
        )
        if published_fingerprint is not None and published_fingerprint != (
            admission_fingerprint(identity, current_provenance)
        ):
            raise EngineBindingAdmissionError(
                identity,
                "live binding changed since initialization",
            )
        if require_published_fingerprint and published_fingerprint is None:
            raise EngineBindingAdmissionError(
                identity,
                "live binding was not admitted after initialization",
            )
    except EngineBindingAdmissionError as exc:
        return _record_live_cache_refusal(sim, identity, exc.reason)
    return True


def _record_live_cache_refusal(
    sim: Any,
    identity: BindingIdentity,
    reason: str,
) -> bool:
    notices = getattr(sim, "_engine_binding_admission_notices", None)
    if not isinstance(notices, list):
        notices = []
        sim._engine_binding_admission_notices = notices
    key = identity.as_dict()
    if not any(
        notice.get("identity") == key
        for notice in notices
        if isinstance(notice, Mapping)
    ):
        notices.append(
            {
                "type": "typed_notice",
                "kind": "engine_binding_not_admitted",
                "identity": key,
                "reason": reason,
                "message": (
                    "binding not admitted on this host "
                    f"({reason}); replay and capture disabled; "
                    "run scripts/assess_engine_bindings.py"
                ),
            }
        )
    return False


def binding_admission_run_notice(sim: Any) -> dict[str, Any] | None:
    notices = getattr(sim, "_engine_binding_admission_notices", None)
    if not isinstance(notices, list) or not notices:
        return None
    return {"notices": [dict(notice) for notice in notices]}


def version_getter_provenance(getter: Any) -> dict[str, Any]:
    try:
        value = getter()
        if (
            value is None
            or not str(value).strip()
            or str(value).strip().lower() == "unavailable"
        ):
            raise ValueError("empty version")
        return {
            "tag": "value",
            "value": str(value).strip(),
            "reason": None,
        }
    except Exception as exc:  # noqa: BLE001 - version is provenance only
        return {
            "tag": "unknown",
            "value": None,
            "reason": f"version getter failed: {type(exc).__name__}",
        }


def install_provenance(
    identity: BindingIdentity,
    *,
    runtime_backend: Any | None = None,
    simulator: Any | None = None,
) -> dict[str, Any]:
    """Return host-local evidence used to detect a changed assessed install."""
    from simulator.engine_local_config import config_path, load_config

    repo_root = Path(__file__).resolve().parent.parent

    def file_record(path: Path | None) -> dict[str, Any]:
        if path is None:
            return {"path": None, "sha256": None}
        resolved = path.expanduser().resolve()
        if not resolved.is_file():
            return {"path": str(resolved), "sha256": None}
        return {
            "path": str(resolved),
            "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
        }

    local_path = config_path()
    local = load_config()
    resolved_paths: dict[str, str | None] = {}
    runtime_artifacts: dict[str, Any] = {}
    if local is not None:
        for name in (
            "thermoengine_dylib_dir",
            "alphamelts_binary_path",
            "magemin_binary_path",
        ):
            configured = getattr(local.paths, name)
            resolved = configured.expanduser().resolve() if configured else None
            resolved_paths[name] = str(resolved) if resolved is not None else None
            if resolved is not None:
                runtime_artifacts[name] = _artifact_digest(resolved)

    def module_record(module_name: str) -> dict[str, Any]:
        try:
            spec = importlib.util.find_spec(module_name)
            origin = Path(spec.origin) if spec and spec.origin else None
        except (ImportError, ModuleNotFoundError, ValueError):
            origin = None
        return file_record(origin)

    def module_object_record(module: Any | None) -> dict[str, Any]:
        origin = getattr(module, "__file__", None)
        return file_record(Path(origin) if origin else None)

    def selected_path(value: Any) -> Path | None:
        if value is None:
            return None
        path = Path(str(value)).expanduser()
        if path.exists():
            return path.resolve()
        if path.name and path.parent == Path("."):
            import shutil

            resolved = shutil.which(str(path))
            return Path(resolved).resolve() if resolved else None
        return path.resolve()

    commissioning = file_record(repo_root / "data" / "engine_commissioning.yaml")
    catalog = file_record(repo_root / "data" / "vapour_rail_u0_manifest.yaml")
    sources: dict[str, Any] = {
        "species_catalog": file_record(repo_root / "data" / "species_catalog.yaml")
    }
    if identity.engine_id == "builtin-vapor-pressure":
        sources["vapor_pressures"] = file_record(
            repo_root / "data" / "vapor_pressures.yaml"
        )
        sources["vapour_rail_u0_manifest"] = catalog
        runtime_artifacts["builtin_vapor_pressure_module"] = module_record(
            "engines.builtin.vapor_pressure"
        )
    elif identity.engine_id == "openimcc":
        runtime_artifacts["openimcc_module"] = module_record("openimcc")
        try:
            from simulator.melt_backend.openimcc_bridge import (
                _load_pack,
                _pack_digest,
            )

            sources["openimcc_pack"] = {
                "model_id": identity.model_id,
                "sha256": _pack_digest(_load_pack("v1.0.2")),
            }
        except Exception as exc:  # noqa: BLE001 - optional producer is fail-closed
            sources["openimcc_pack"] = {
                "model_id": identity.model_id,
                "sha256": None,
                "error": type(exc).__name__,
            }
    elif identity.engine_id == "sulfsat":
        gate = getattr(simulator, "_sulfsat_gate", None)
        runtime_artifacts["pysulfsat_module"] = (
            module_object_record(getattr(gate, "_module", None))
            if gate is not None
            else module_record("PySulfSat")
        )
        sources["sulfsat_calibration"] = file_record(
            repo_root / "simulator" / "melt_backend" / "sulfsat.py"
        )
    elif identity.engine_id == "alphamelts":
        runtime_artifacts["alphamelts_adapter"] = module_record(
            "simulator.melt_backend.alphamelts"
        )
        if identity.transport == "subprocess":
            binary = getattr(runtime_backend, "_binary_path", None)
            if binary is None:
                from simulator.engine_local_config import find_alphamelts_binary

                engine_root = repo_root / "engines" / "alphamelts"
                binary = find_alphamelts_binary(engine_root)
                if binary is None:
                    from simulator.melt_backend.alphamelts import AlphaMELTSBackend

                    binary = AlphaMELTSBackend()._find_project_binary(engine_root)
                if binary is None:
                    import shutil

                    binary = shutil.which("alphamelts")
            binary_path = selected_path(binary)
            resolved_paths["alphamelts_selected_binary"] = (
                str(binary_path) if binary_path is not None else None
            )
            runtime_artifacts["alphamelts_selected_binary"] = file_record(
                binary_path
            )
            launcher = getattr(runtime_backend, "_engine_path", None)
            if launcher is None:
                candidate = repo_root / "engines" / "alphamelts" / "run_alphamelts.command"
                launcher = candidate if candidate.is_file() else None
            launcher_path = selected_path(launcher)
            resolved_paths["alphamelts_selected_launcher"] = (
                str(launcher_path) if launcher_path is not None else None
            )
            runtime_artifacts["alphamelts_selected_launcher"] = file_record(
                launcher_path
            )
        else:
            module = getattr(runtime_backend, "_pet_module", None)
            if module is not None:
                module_artifact = module_object_record(module)
            else:
                module_artifact = module_record("petthermotools")
                if module_artifact.get("sha256") is None:
                    module_artifact = module_record("PetThermoTools")
            runtime_artifacts["alphamelts_python_api_module"] = module_artifact
            runtime_artifacts["meltsdynamic_module"] = module_record("meltsdynamic")
            resolved_paths["alphamelts_python_api_module"] = module_artifact.get(
                "path"
            )
    elif identity.engine_id == "thermoengine":
        runtime_artifacts["thermoengine_adapter"] = module_record(
            "simulator.melt_backend.thermoengine"
        )
        module = getattr(runtime_backend, "_thermoengine", None)
        module_artifact = (
            module_object_record(module)
            if module is not None
            else module_record("thermoengine")
        )
        runtime_artifacts["thermoengine_module"] = module_artifact
        resolved_paths["thermoengine_module"] = module_artifact.get("path")
    elif identity.engine_id == "magemin":
        runtime_artifacts["magemin_adapter"] = module_record(
            "simulator.melt_backend.magemin"
        )
        binary = getattr(runtime_backend, "_binary_path", None)
        if binary is None:
            from simulator.engine_local_config import configured_magemin_binary_path

            binary = configured_magemin_binary_path()
            if binary is None:
                from simulator.melt_backend.magemin import MAGEMinBackend

                binary = MAGEMinBackend()._locate_binary(None)
        binary_path = selected_path(binary)
        resolved_paths["magemin_selected_binary"] = (
            str(binary_path) if binary_path is not None else None
        )
        runtime_artifacts["magemin_selected_binary"] = file_record(binary_path)
        runtime_artifacts["magemin_bridge_module"] = module_object_record(
            getattr(runtime_backend, "_magemin_module", None)
        )
    provenance = {
        "engine_config": file_record(local_path),
        "resolved_paths": resolved_paths,
        "runtime_artifacts": runtime_artifacts,
        "runtime_environment": _runtime_environment_fingerprint(
            identity,
            resolved_paths,
        ),
        "selected_source_digests": sources,
        "commissioning_snapshot": commissioning,
        "catalog_manifest": catalog,
    }
    required_artifacts = {
        "alphamelts": (
            "alphamelts_adapter",
            "alphamelts_selected_binary"
            if identity.transport == "subprocess"
            else "alphamelts_python_api_module",
        ),
        "thermoengine": ("thermoengine_adapter", "thermoengine_module"),
        "magemin": ("magemin_adapter", "magemin_selected_binary"),
        "builtin-vapor-pressure": ("builtin_vapor_pressure_module",),
        "openimcc": ("openimcc_module",),
        "sulfsat": ("pysulfsat_module",),
    }.get(identity.engine_id, ())
    required_sources = {
        "alphamelts": ("species_catalog",),
        "thermoengine": ("species_catalog",),
        "magemin": ("species_catalog",),
        "builtin-vapor-pressure": (
            "species_catalog",
            "vapor_pressures",
            "vapour_rail_u0_manifest",
        ),
        "openimcc": ("species_catalog", "openimcc_pack"),
        "sulfsat": ("species_catalog", "sulfsat_calibration"),
    }.get(identity.engine_id, ())
    artifacts_proven = bool(required_artifacts) and all(
        isinstance(runtime_artifacts.get(name), Mapping)
        and runtime_artifacts[name].get("sha256") is not None
        for name in required_artifacts
    )
    sources_proven = bool(required_sources) and all(
        isinstance(sources.get(name), Mapping)
        and sources[name].get("sha256") is not None
        for name in required_sources
    )
    provenance["binding_provenance_verifiable"] = bool(
        artifacts_proven
        and sources_proven
        and commissioning.get("sha256") is not None
        and catalog.get("sha256") is not None
    )
    return provenance


def _artifact_digest(path: Path) -> str | None:
    resolved = path.expanduser().resolve()
    if resolved.is_file():
        return hashlib.sha256(resolved.read_bytes()).hexdigest()
    if resolved.is_dir():
        parts = [
            (child.name, hashlib.sha256(child.read_bytes()).hexdigest())
            for child in sorted(resolved.iterdir())
            if child.is_file()
        ]
        if parts:
            return hashlib.sha256(canonical_json_bytes(parts)).hexdigest()
    return None


def _runtime_environment_fingerprint(
    identity: BindingIdentity,
    resolved_paths: Mapping[str, str | None],
) -> dict[str, Any]:
    if identity.engine_id == "alphamelts":
        names = (
            "PATH",
            "DYLD_FALLBACK_LIBRARY_PATH",
            "LD_LIBRARY_PATH",
            "ALPHAMELTS_CALC_MODE",
        )
    elif identity.engine_id == "thermoengine":
        names = ("DYLD_FALLBACK_LIBRARY_PATH",)
    elif identity.engine_id == "magemin":
        names = ("PATH",)
    else:
        return {"variables": [], "sha256": None}

    selected = {name: os.environ.get(name) for name in names}
    if identity.engine_id == "alphamelts" and identity.transport == "subprocess":
        from simulator.config import resolve_alphamelts_subprocess_model

        _model, expected_mode = resolve_alphamelts_subprocess_model(
            identity.model_id
        )
        ambient_mode = selected["ALPHAMELTS_CALC_MODE"]
        selected["ALPHAMELTS_CALC_MODE"] = (
            "conflict"
            if ambient_mode and ambient_mode != expected_mode
            else expected_mode
        )
    elif identity.engine_id == "thermoengine":
        configured = resolved_paths.get("thermoengine_dylib_dir")
        search_path = str(selected["DYLD_FALLBACK_LIBRARY_PATH"] or "")
        parts = [part for part in search_path.split(":") if part]
        if configured and parts and parts[0] == configured:
            parts = parts[1:]
        selected["DYLD_FALLBACK_LIBRARY_PATH"] = (
            ":".join([configured, *parts]) if configured else ":".join(parts)
        )
    return {
        "variables": list(names),
        "sha256": hashlib.sha256(canonical_json_bytes(selected)).hexdigest(),
    }
