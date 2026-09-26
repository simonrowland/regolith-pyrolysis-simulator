"""Optional bridge from simulator melt compositions to the openimcc package.

This module is diagnostic-only.  It does not register a backend or alter the
vendored IMCC-SF04 path.  Callers must make ``openimcc`` importable explicitly;
the bridge never falls back to the vendored kernel when it is absent.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from types import MappingProxyType
from typing import Any

try:  # Optional dependency: C1 must remain importable without openimcc.
    import openimcc as _openimcc
except ImportError as exc:  # pragma: no cover - exercised without PYTHONPATH
    _openimcc = None
    _OPENIMCC_IMPORT_ERROR: BaseException | None = exc
else:
    _OPENIMCC_IMPORT_ERROR = None


OPENIMCC_INSTALL_HINT = (
    "python -m pip install --no-deps "
    "'openimcc @ git+https://github.com/simonrowland/openimcc'"
)

_PACK_RESOURCE_NAMES = {
    "v1.0.2": None,
    "ext-v4": "imcc-sf04-ext-v4.json",
}


class OpenImccUnavailableError(RuntimeError):
    """Raised when the optional openimcc package cannot be imported."""

    reason_code = "openimcc_unavailable"
    code = "openimcc_not_importable"

    def __init__(self, import_error: BaseException | None = None) -> None:
        self.reason = "openimcc_not_importable"
        self.remedy = (
            "remedy: make openimcc importable, e.g. "
            f"{OPENIMCC_INSTALL_HINT}; tests may instead put an openimcc "
            "checkout's src/ directory on PYTHONPATH"
        )
        detail = ""
        if import_error is not None:
            detail = f" ({type(import_error).__name__}: {import_error})"
        super().__init__(
            f"reason={self.reason}: optional dependency unavailable{detail}; "
            f"{self.remedy}"
        )


@dataclass(frozen=True)
class OpenImccBridgeResult:
    """Parent activities and provenance returned by one openimcc evaluation."""

    parent_oxide_activities: Mapping[str, float]
    parent_oxides: tuple[str, ...]
    flags: tuple[str, ...]
    notices: tuple[str, ...]
    acid_sink_ratio: float | None
    pack_model_id: str
    pack_version: str
    pack_digest: str
    openimcc_version: str
    envelope_status: str
    extrapolated: bool
    labels: Any
    coverage: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "parent_oxide_activities",
            MappingProxyType(dict(self.parent_oxide_activities)),
        )
        object.__setattr__(self, "coverage", MappingProxyType(dict(self.coverage)))

def _require_openimcc() -> Any:
    if _openimcc is None:
        raise OpenImccUnavailableError(_OPENIMCC_IMPORT_ERROR)
    return _openimcc


def _canonical_composition(composition: Mapping[str, float]) -> dict[str, float]:
    """Preserve simulator oxide values, including its FeO-equivalent convention."""

    normalized = {str(name): value for name, value in composition.items()}
    if "FeO_total" in normalized:
        if "FeO" in normalized:
            raise ValueError("composition cannot contain both FeO and FeO_total")
        # The simulator's cleaned-melt FeO/FeO_total convention is an FeO
        # equivalent.  This is a rename, not an Fe3+/Fe2+ split.
        normalized["FeO"] = normalized.pop("FeO_total")
    return normalized


@lru_cache(maxsize=2)
def _load_pack(pack_name: str) -> Any:
    package = _require_openimcc()
    if pack_name not in _PACK_RESOURCE_NAMES:
        allowed = ", ".join(sorted(_PACK_RESOURCE_NAMES))
        raise ValueError(f"unknown openimcc pack {pack_name!r}; expected one of {allowed}")

    resource_name = _PACK_RESOURCE_NAMES[pack_name]
    if resource_name is None:
        return package.load_datapack()

    resource = resources.files("openimcc").joinpath("data", "packs", resource_name)
    with resources.as_file(resource) as path:
        return package.load_datapack(path)


def _pack_digest(pack: Any) -> str:
    for owner in (pack, getattr(pack, "kernel_datapack", None)):
        for name in ("published_manifest_sha256", "digest"):
            value = getattr(owner, name, None)
            if value:
                return str(value)

    package = _require_openimcc()
    try:
        from openimcc.kernel import _PUBLISHED_DATAPACK_SHA256
    except (ImportError, AttributeError):  # pragma: no cover - future package API
        return ""
    _ = package
    return str(_PUBLISHED_DATAPACK_SHA256)


def evaluate(
    composition_mol: Mapping[str, float] | None = None,
    temperature_K: float | None = None,
    *,
    composition_kg: Mapping[str, float] | None = None,
    pack: str = "v1.0.2",
    allow_extrapolation: bool = False,
    allow_out_of_envelope: bool = False,
) -> OpenImccBridgeResult:
    """Evaluate openimcc using simulator composition and provenance conventions.

    ``composition_mol`` is the canonical simulator input.  ``composition_kg``
    is accepted as the external mass projection and is passed to openimcc as a
    ``wt`` composition; absolute mass units cancel in the normalization.  If
    both are supplied, the molar composition takes precedence, matching the
    vendored MeltBackend adapter.

    ``pack`` accepts ``"v1.0.2"`` or ``"ext-v4"``.  The two extrapolation
    switches are explicit and default to refusal.  Positive ``Fe2O3`` remains
    an openimcc typed refusal because the IMCC adapter requires the caller's
    redox model to convert it to the FeO-equivalent convention first.
    """

    package = _require_openimcc()
    if temperature_K is None:
        raise TypeError("temperature_K is required")

    if composition_mol is not None:
        composition = _canonical_composition(composition_mol)
        basis_type = "mol"
    elif composition_kg is not None:
        composition = _canonical_composition(composition_kg)
        basis_type = "wt"
    else:
        raise TypeError("composition_mol or composition_kg is required")

    loaded_pack = _load_pack(pack)
    result = package.evaluate(
        composition,
        float(temperature_K),
        loaded_pack,
        basis_type=basis_type,
        enable_sp_extension=pack == "ext-v4",
        allow_extrapolation=allow_extrapolation,
        allow_out_of_envelope=allow_out_of_envelope,
    )
    parent_oxides = tuple(str(name) for name in result.parent_oxides)
    activities = {
        name: float(value)
        for name, value in zip(parent_oxides, result.parent_activity, strict=True)
    }
    labels = result.labels
    identity = labels.identity
    return OpenImccBridgeResult(
        parent_oxide_activities=activities,
        parent_oxides=parent_oxides,
        flags=tuple(labels.flags),
        notices=tuple(labels.notices),
        acid_sink_ratio=(
            None if labels.acid_sink_ratio is None else float(labels.acid_sink_ratio)
        ),
        pack_model_id=str(identity["model_id"]),
        pack_version=str(identity["datapack_version"]),
        pack_digest=_pack_digest(loaded_pack),
        openimcc_version=str(getattr(package, "__version__", "0+unknown")),
        envelope_status=str(labels.envelope_status),
        extrapolated=bool(result.extrapolated),
        labels=labels,
        coverage={str(name): str(value) for name, value in labels.coverage.items()},
    )


evaluate_openimcc = evaluate


__all__ = [
    "OPENIMCC_INSTALL_HINT",
    "OpenImccBridgeResult",
    "OpenImccUnavailableError",
    "evaluate",
    "evaluate_openimcc",
]
