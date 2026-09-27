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
import math
import numbers
from types import MappingProxyType
from typing import Any

from simulator.accounting.formulas import resolve_species_formula
from simulator.chemistry.melt_activity import (
    MELT_OXIDE_CATIONS_PER_FORMULA,
)
from simulator.composition_projection import (
    PROJECTED_BULK_CLASSIFICATION_KEY,
    classify_projected_bulk,
    projected_component_moles_per_kg,
)

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

OPENIMCC_PARENT_OXIDES = (
    "Na2O",
    "K2O",
    "SiO2",
    "FeO",
    "MgO",
    "CaO",
    "Al2O3",
    "TiO2",
)

# DERIVATION: FeO_total is the FeO-equivalent mass handed to the FeO-only
# SF04 convention.  wt%(FeO_total) = wt%(FeO) + wt%(Fe2O3) *
# 2*M(FeO)/M(Fe2O3), and 2 * 71.844 / 159.688 = 0.89982.  Unit check:
# (mass FeO / mass Fe2O3) is kg/kg, so the factor is dimensionless.  Sanity:
# 1 wt% Fe2O3 contributes 0.89982 wt% FeO at equal Fe atoms.
FE2O3_TO_FEO_TOTAL_WT_FACTOR = 2.0 * 71.844 / 159.688


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


class OpenImccCompositionPolicyRefusal(RuntimeError):
    """Typed C3 refusal for a cleaned-melt composition policy failure."""

    reason_code = "openimcc_composition_policy_refused"

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = str(code)
        self.reason = self.code
        self.diagnostics = dict(diagnostics or {})
        super().__init__(f"reason={self.code}: {detail}")


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


@dataclass(frozen=True)
class OpenImccCleanedMeltResult:
    """Bridge result plus the visible C3 composition-policy record."""

    bridge: OpenImccBridgeResult
    composition_wt_pct: Mapping[str, float]
    policy: Mapping[str, Any]
    single_cation_activities: Mapping[str, float]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "composition_wt_pct",
            MappingProxyType(dict(self.composition_wt_pct)),
        )
        object.__setattr__(self, "policy", MappingProxyType(dict(self.policy)))
        object.__setattr__(
            self,
            "single_cation_activities",
            MappingProxyType(dict(self.single_cation_activities)),
        )


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


def _cleaned_melt_wt_pct(
    composition_mol: Mapping[str, float],
) -> tuple[dict[str, float], dict[str, float], float]:
    """Return source wt%, FeO-folded IMCC wt%, and source mass in kg."""

    try:
        canonical = _canonical_composition(composition_mol)
    except Exception as exc:  # noqa: BLE001 - policy turns this into a refusal
        raise OpenImccCompositionPolicyRefusal(
            "openimcc_composition_canonicalization",
            f"cannot canonicalize cleaned-melt composition: {exc}",
        ) from exc

    source_mass_kg: dict[str, float] = {}
    for raw_name, raw_mol in canonical.items():
        name = str(raw_name)
        if not isinstance(raw_mol, numbers.Real) or isinstance(raw_mol, bool):
            raise OpenImccCompositionPolicyRefusal(
                "openimcc_composition_invalid_input",
                (
                    f"invalid mole inventory for {name!r}: "
                    f"value={raw_mol!r}; expected a real numeric value"
                ),
            )
        if raw_mol < 0.0:
            raise OpenImccCompositionPolicyRefusal(
                "openimcc_composition_invalid_input",
                (
                    f"invalid mole inventory for {name!r}: "
                    f"value={raw_mol!r}; inventory must be non-negative"
                ),
            )
        try:
            mol = float(raw_mol)
        except (OverflowError, TypeError, ValueError) as exc:
            raise OpenImccCompositionPolicyRefusal(
                "openimcc_composition_invalid_input",
                (
                    f"invalid mole inventory for {name!r}: "
                    f"value={raw_mol!r}; cannot convert to float"
                ),
            ) from exc
        if not math.isfinite(mol):
            raise OpenImccCompositionPolicyRefusal(
                "openimcc_composition_invalid_input",
                (
                    f"invalid mole inventory for {name!r}: "
                    f"value={raw_mol!r}; inventory must be finite"
                ),
            )
        if mol == 0.0:
            continue
        try:
            mass_kg = mol * resolve_species_formula(name).molar_mass_kg_per_mol()
        except Exception as exc:  # noqa: BLE001 - policy turns this into a typed refusal
            raise OpenImccCompositionPolicyRefusal(
                "openimcc_composition_formula_unavailable",
                f"cannot resolve cleaned-melt formula for {name!r}: {exc}",
            ) from exc
        source_mass_kg[name] = source_mass_kg.get(name, 0.0) + mass_kg

    total_mass_kg = sum(source_mass_kg.values())
    if total_mass_kg <= 0.0:
        raise OpenImccCompositionPolicyRefusal(
            "openimcc_composition_empty",
            "cleaned-melt composition has no positive mass",
        )
    source_wt_pct = {
        name: mass_kg / total_mass_kg * 100.0
        for name, mass_kg in source_mass_kg.items()
    }
    folded = {
        name: wt_pct
        for name, wt_pct in source_wt_pct.items()
        if name in OPENIMCC_PARENT_OXIDES and name != "FeO"
    }
    feo_total_wt_pct = source_wt_pct.get("FeO", 0.0) + (
        source_wt_pct.get("Fe2O3", 0.0) * FE2O3_TO_FEO_TOTAL_WT_FACTOR
    )
    if feo_total_wt_pct > 0.0:
        folded["FeO"] = feo_total_wt_pct
    return source_wt_pct, folded, total_mass_kg


def _cleaned_melt_policy(
    composition_mol: Mapping[str, float],
) -> tuple[dict[str, float], dict[str, Any]]:
    source_wt_pct, folded_wt_pct, _ = _cleaned_melt_wt_pct(composition_mol)
    dropped = {
        name: value
        for name, value in source_wt_pct.items()
        if name not in OPENIMCC_PARENT_OXIDES and name != "Fe2O3"
    }
    dropped_mol_per_kg, unavailable_reasons = projected_component_moles_per_kg(
        dropped,
        source_sum_wt_pct=100.0,
    )
    classification = classify_projected_bulk(
        dropped,
        source_sum_wt_pct=100.0,
        dropped_component_mol_per_kg=dropped_mol_per_kg,
        dropped_component_mol_unavailable_reasons=unavailable_reasons,
    )
    classification_record = classification.as_diagnostics()
    if classification.verdict == "invalid_input":
        raise OpenImccCompositionPolicyRefusal(
            "openimcc_projection_invalid_input",
            (
                "cleaned-melt projected composition is invalid: "
                f"{classification.invalid_reason or 'unknown reason'}"
            ),
            diagnostics={
                PROJECTED_BULK_CLASSIFICATION_KEY: classification_record,
            },
        )
    if classification.verdict == "over_threshold":
        raise OpenImccCompositionPolicyRefusal(
            "openimcc_projection_over_threshold",
            (
                "cleaned-melt components outside IMCC parents exceed the "
                f"{classification.threshold_wt_pct:g} wt% projected-bulk limit: "
                f"{classification.dropped_total_wt_pct:.12g} wt%"
            ),
            diagnostics={
                PROJECTED_BULK_CLASSIFICATION_KEY: classification_record,
            },
        )

    policy = dict(classification_record)
    policy[PROJECTED_BULK_CLASSIFICATION_KEY] = classification_record
    policy["status"] = "projected" if classification.components else "direct"
    if classification.components:
        policy["notice"] = {
            "code": "openimcc_projected_bulk",
            "message": (
                "openimcc solved the projected cleaned-melt bulk; dropped "
                "components remain visible in the classifier record"
            ),
        }
    fe2o3_wt_pct = source_wt_pct.get("Fe2O3", 0.0)
    if fe2o3_wt_pct > 0.0:
        policy["fe2o3_fold"] = {
            "code": "openimcc_fe2o3_fold",
            "fe2o3_wt_pct": fe2o3_wt_pct,
            "feo_total_wt_pct": folded_wt_pct.get("FeO", 0.0),
            "factor": FE2O3_TO_FEO_TOTAL_WT_FACTOR,
            "basis": "Fe_atoms",
        }
    return folded_wt_pct, policy


def evaluate_cleaned_melt(
    composition_mol: Mapping[str, float],
    temperature_K: float,
    *,
    allow_extrapolation: bool = False,
) -> OpenImccCleanedMeltResult:
    """Apply C3's cleaned-melt policy, then call the C1 bridge."""

    _require_openimcc()
    composition_wt_pct, policy = _cleaned_melt_policy(composition_mol)
    bridge = evaluate(
        composition_kg=composition_wt_pct,
        temperature_K=temperature_K,
        pack="v1.0.2",
        allow_extrapolation=allow_extrapolation,
        allow_out_of_envelope=False,
    )
    single_cation = {}
    for oxide in OPENIMCC_PARENT_OXIDES:
        if oxide not in composition_wt_pct:
            continue
        if (
            oxide not in bridge.parent_oxides
            or oxide not in bridge.parent_oxide_activities
        ):
            raise OpenImccCompositionPolicyRefusal(
                "openimcc_result_shape",
                (
                    "openimcc result is missing the parent activity for "
                    f"present oxide {oxide!r}"
                ),
            )
        raw_activity = bridge.parent_oxide_activities[oxide]
        try:
            parent_activity = float(raw_activity)
        except (OverflowError, TypeError, ValueError) as exc:
            raise OpenImccCompositionPolicyRefusal(
                "openimcc_result_shape",
                (
                    f"openimcc parent activity for {oxide!r} is invalid: "
                    f"value={raw_activity!r}"
                ),
            ) from exc
        if not math.isfinite(parent_activity) or parent_activity <= 0.0:
            raise OpenImccCompositionPolicyRefusal(
                "openimcc_result_shape",
                (
                    f"openimcc parent activity for {oxide!r} is invalid: "
                    f"value={raw_activity!r}; expected a finite positive value"
                ),
            )
        cations = float(MELT_OXIDE_CATIONS_PER_FORMULA.get(oxide, 1.0))
        single_cation[oxide] = parent_activity ** (1.0 / cations)
    return OpenImccCleanedMeltResult(
        bridge=bridge,
        composition_wt_pct=composition_wt_pct,
        policy=policy,
        single_cation_activities=single_cation,
    )


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
    "OpenImccCleanedMeltResult",
    "OpenImccCompositionPolicyRefusal",
    "OpenImccUnavailableError",
    "OPENIMCC_PARENT_OXIDES",
    "FE2O3_TO_FEO_TOTAL_WT_FACTOR",
    "evaluate",
    "evaluate_cleaned_melt",
    "evaluate_openimcc",
]
