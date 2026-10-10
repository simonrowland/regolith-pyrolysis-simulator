"""Optional bridge from simulator melt compositions to the openimcc package.

This module is diagnostic-only. Callers must make ``openimcc`` importable
explicitly; the bridge never falls back to an in-repository kernel when it is
absent.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
import math
import numbers
from types import MappingProxyType
from typing import Any

from simulator.accounting.exceptions import UnknownSpeciesError
from simulator.accounting.formulas import resolve_species_formula
from simulator.feedstock_composition import (
    FEOT_FROM_FE2O3,
    feot_equivalent_moles,
    iron_oxide_values,
)
from simulator.chemistry.melt_activity import (
    single_cation_activity_and_fraction,
    single_cation_component_formula,
)
from simulator.composition_projection import (
    PROJECTED_BULK_CLASSIFICATION_KEY,
    classify_projected_bulk,
    projected_component_moles_per_kg,
)
from simulator.state import MOLAR_MASS
from simulator.melt_backend.imcc_adapter_labels import ImccAdapterLabels
from simulator.melt_backend.base import (
    DEFAULT_BACKEND_CAPABILITIES,
    EquilibriumResult,
    MeltBackend,
    split_cleaned_melt_account,
)

try:  # Optional dependency: C1 must remain importable without openimcc.
    import openimcc as _openimcc
except (ImportError, OSError, RuntimeError) as exc:  # pragma: no cover
    _openimcc = None
    _OPENIMCC_IMPORT_ERROR: BaseException | None = exc
else:
    _OPENIMCC_IMPORT_ERROR = None


OPENIMCC_INSTALL_HINT = (
    "python -m pip install --no-deps "
    "'openimcc @ git+https://github.com/simonrowland/openimcc'"
)
OPENIMCC_RECORDED_PIN = (
    "openimcc @ git+https://github.com/simonrowland/openimcc"
    "@f037d0518f47b136b9e4d33bb1ad60aea3c6cc6a"
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
_OPENIMCC_CRMN_RELAXED_OXIDES = ("Cr2O3", "MnO")
_OPENIMCC_CRMN_RELAXED_RULING = "owner 2026-09-27"

# Fe2O3 contributes two FeO-equivalent moles. Keep this mass ratio for the
# policy diagnostic only; the actual projection folds moles directly.
FE2O3_TO_FEO_TOTAL_WT_FACTOR = FEOT_FROM_FE2O3


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


class OpenImccOxygenBalanceUnavailableError(RuntimeError):
    """Typed refusal when the installed package lacks the balance solver."""

    code = "openimcc_oxygen_balance_unavailable"
    reason_code = code

    def __init__(self) -> None:
        self.backend_status_reason = (
            f"{self.code}: installed openimcc does not expose "
            "evaluate_gas_oxygen_balance; remedy: install the recorded pin "
            f"{OPENIMCC_RECORDED_PIN}"
        )
        super().__init__(self.backend_status_reason)


class OpenImccBindingDigestUnavailableError(RuntimeError):
    """Typed refusal when openimcc cannot identify a datapack binding."""

    code = "openimcc_binding_digest_unavailable"
    reason_code = code

    def __init__(self) -> None:
        self.backend_status_reason = (
            f"{self.code}: installed openimcc cannot provide a usable "
            "datapack binding_digest or engine binding identity; remedy: "
            "install the recorded pin "
            f"{OPENIMCC_RECORDED_PIN}"
        )
        super().__init__(self.backend_status_reason)


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
    activity_coefficients: Mapping[str, Mapping[str, Any]] | None = None
    parent_oxide_x_star_ratios: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "parent_oxide_activities",
            MappingProxyType(dict(self.parent_oxide_activities)),
        )
        object.__setattr__(self, "coverage", MappingProxyType(dict(self.coverage)))
        object.__setattr__(
            self,
            "parent_oxide_x_star_ratios",
            MappingProxyType(dict(self.parent_oxide_x_star_ratios)),
        )
        object.__setattr__(
            self,
            "activity_coefficients",
            MappingProxyType(
                {
                    str(key): MappingProxyType(dict(value))
                    for key, value in (self.activity_coefficients or {}).items()
                }
            ),
        )


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


def imcc_complex_saturation_notices(
    flags: tuple[str, ...] | list[str],
    acid_sink_ratio: float | None,
    parent_oxide_x_star_ratios: Mapping[str, float],
) -> tuple[dict[str, Any], ...]:
    """Map each openimcc acidic-sink exhaustion label to a typed notice."""

    notices: list[dict[str, Any]] = []
    for item in flags:
        flag = str(item)
        if not flag.startswith("species-coverage-edge"):
            continue
        sink_name = flag.partition("x*(")[2].partition(")")[0]
        if sink_name == "SiO2":
            # Keep the established silica notice shape byte-for-byte.
            notice = {
                "kind": "imcc_complex_saturation",
                "flag": flag,
                "reason": flag,
                "acid_sink_ratio": acid_sink_ratio,
            }
        else:
            notice = {
                "kind": "imcc_complex_saturation",
                "flag": flag,
                "reason": flag,
                "sink_name": sink_name,
            }
            if sink_name in parent_oxide_x_star_ratios:
                notice["sink_ratio"] = parent_oxide_x_star_ratios[sink_name]
        notices.append(notice)
    return tuple(notices)


def _require_openimcc() -> Any:
    if _openimcc is None:
        raise OpenImccUnavailableError(_OPENIMCC_IMPORT_ERROR)
    return _openimcc


def evaluate_gas_oxygen_balance(
    parent_activities: Mapping[str, float],
    temperature_K: float,
    datapack: Any,
    *,
    parent_oxides: tuple[str, ...] | None = None,
) -> tuple[float, Any, Mapping[str, Any]]:
    """Solve openimcc gas effusion balance using the supplied package table.

    Scoring must restrict the physical interpretation to inert bench cells,
    using ``cell_material``. This helper evaluates the requested balance and
    never substitutes commanded pO2.
    """

    package = _require_openimcc()
    try:
        solver = getattr(package, "evaluate_gas_oxygen_balance")
    except (AttributeError, ImportError) as exc:
        error = OpenImccOxygenBalanceUnavailableError()
        error.backend_status_reason += f" ({type(exc).__name__}: {exc})"
        error.args = (error.backend_status_reason,)
        raise error from exc
    return solver(
        parent_activities,
        float(temperature_K),
        datapack,
        parent_oxides=parent_oxides,
        allow_extrapolation=True,
    )


def _canonical_composition(composition: Mapping[str, float]) -> dict[str, float]:
    """Preserve simulator oxide values, including its FeO-equivalent convention."""

    normalized = {str(name): value for name, value in composition.items()}
    if "FeO_total" in normalized:
        if "FeO" in normalized:
            raise OpenImccCompositionPolicyRefusal(
                "openimcc_composition_canonicalization",
                "cannot canonicalize cleaned-melt composition: "
                "composition cannot contain both FeO and FeO_total",
            )
        # The simulator's cleaned-melt FeO/FeO_total convention is an FeO
        # equivalent.  This is a rename, not an Fe3+/Fe2+ split.
        normalized["FeO"] = normalized.pop("FeO_total")
    return normalized


def _cleaned_melt_projection(
    composition_mol: Mapping[str, float],
) -> tuple[dict[str, float], dict[str, float]]:
    """Return source wt% and the cleaned IMCC mole projection."""

    canonical = _canonical_composition(composition_mol)

    source_mass_kg: dict[str, float] = {}
    cleaned_composition_mol: dict[str, float] = {}
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
        canonical[name] = mol
        if name in ("FeO", "Fe2O3"):
            continue
        if mol == 0.0:
            continue
        if name in OPENIMCC_PARENT_OXIDES:
            cleaned_composition_mol[name] = mol
        try:
            formula = resolve_species_formula(name)
        except UnknownSpeciesError as exc:
            raise OpenImccCompositionPolicyRefusal(
                "openimcc_composition_formula_unavailable",
                f"cannot resolve cleaned-melt formula for {name!r} "
                f"({type(exc).__name__}: {exc})",
            ) from exc
        mass_kg = mol * formula.molar_mass_kg_per_mol()
        source_mass_kg[name] = source_mass_kg.get(name, 0.0) + mass_kg

    feo_moles, fe2o3_moles = iron_oxide_values(canonical)
    for oxide, mol in (("FeO", feo_moles), ("Fe2O3", fe2o3_moles)):
        if mol <= 0.0:
            continue
        source_mass_kg[oxide] = (
            mol * resolve_species_formula(oxide).molar_mass_kg_per_mol()
        )

    feo_equivalent_moles = feot_equivalent_moles(canonical)
    if feo_equivalent_moles > 0.0:
        cleaned_composition_mol["FeO"] = feo_equivalent_moles

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
    return source_wt_pct, cleaned_composition_mol


def _composition_wt_pct_from_moles(
    composition_mol: Mapping[str, float],
) -> dict[str, float]:
    masses = {
        name: float(amount) * float(MOLAR_MASS[name])
        for name, amount in composition_mol.items()
        if amount > 0.0
    }
    total_mass = sum(masses.values())
    return {
        name: mass / total_mass * 100.0
        for name, mass in masses.items()
    }


def _cleaned_melt_policy(
    composition_mol: Mapping[str, float],
) -> tuple[dict[str, float], dict[str, Any]]:
    source_wt_pct, cleaned_composition_mol = _cleaned_melt_projection(
        composition_mol
    )
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
    relaxed_dropped = {
        name: value
        for name, value in dropped.items()
        if name in _OPENIMCC_CRMN_RELAXED_OXIDES
    }
    relaxed_notice: dict[str, Any] | None = None
    threshold_classification = classification
    if relaxed_dropped:
        threshold_dropped = {
            name: value
            for name, value in dropped.items()
            if name not in _OPENIMCC_CRMN_RELAXED_OXIDES
        }
        threshold_dropped_mol_per_kg = {
            name: value
            for name, value in dropped_mol_per_kg.items()
            if name not in _OPENIMCC_CRMN_RELAXED_OXIDES
        }
        threshold_unavailable_reasons = {
            name: value
            for name, value in unavailable_reasons.items()
            if name not in _OPENIMCC_CRMN_RELAXED_OXIDES
        }
        threshold_classification = classify_projected_bulk(
            threshold_dropped,
            source_sum_wt_pct=100.0,
            dropped_component_mol_per_kg=threshold_dropped_mol_per_kg,
            dropped_component_mol_unavailable_reasons=(
                threshold_unavailable_reasons
            ),
        )
        classification_record.update(
            {
                "projection_verdict": threshold_classification.verdict,
                "thresholded_dropped_total_wt_pct": (
                    threshold_classification.dropped_total_wt_pct
                ),
                "thresholded_dropped_components": list(
                    threshold_classification.dropped_components
                ),
                "relaxed_dropped_components": list(relaxed_dropped),
                "relaxed_dropped_component_wt_pct": dict(relaxed_dropped),
            }
        )
        relaxed_notice = {
            "code": "openimcc_projection_crmn_relaxed",
            "ruling": _OPENIMCC_CRMN_RELAXED_RULING,
            "owner_ruling_date": "2026-09-27",
            "wt_pct": dict(relaxed_dropped),
            "message": (
                "Cr2O3 and MnO remain projected from the openimcc input, "
                "but do not count toward the shared 1.0 wt% gate"
            ),
        }
        classification_record["openimcc_projection_crmn_relaxed"] = dict(
            relaxed_notice
        )
    if threshold_classification.verdict == "over_threshold":
        raise OpenImccCompositionPolicyRefusal(
            "openimcc_projection_over_threshold",
            (
                "cleaned-melt components outside IMCC parents exceed the "
                f"{threshold_classification.threshold_wt_pct:g} wt% "
                "projected-bulk limit: "
                f"{threshold_classification.dropped_total_wt_pct:.12g} wt%"
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
    if relaxed_notice is not None:
        policy["openimcc_projection_crmn_relaxed"] = relaxed_notice
    _feo_wt_pct, fe2o3_wt_pct = iron_oxide_values(source_wt_pct)
    if fe2o3_wt_pct > 0.0:
        policy["fe2o3_fold"] = {
            "code": "openimcc_fe2o3_fold",
            "fe2o3_wt_pct": fe2o3_wt_pct,
            "feo_total_wt_pct": _composition_wt_pct_from_moles(
                cleaned_composition_mol
            ).get("FeO", 0.0),
            "factor": FE2O3_TO_FEO_TOTAL_WT_FACTOR,
            "basis": "Fe_atoms",
        }
        policy["fe_redox_notice"] = {
            "code": "openimcc_ferric_component_collapsed",
            "message": "ferric component collapsed until d-072",
        }
    return cleaned_composition_mol, policy


def evaluate_cleaned_melt(
    composition_mol: Mapping[str, float],
    temperature_K: float,
    *,
    allow_extrapolation: bool = False,
) -> OpenImccCleanedMeltResult:
    """Apply C3's cleaned-melt policy, then call the C1 bridge."""

    _require_openimcc()
    cleaned_composition_mol, policy = _cleaned_melt_policy(composition_mol)
    composition_wt_pct = _composition_wt_pct_from_moles(
        cleaned_composition_mol
    )
    bridge = evaluate(
        composition_mol=cleaned_composition_mol,
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
        single_activity, _ = single_cation_activity_and_fraction(
            oxide, parent_activity, composition_mol
        )
        single_cation[oxide] = single_activity
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
    value = getattr(pack, "binding_digest", None)
    if not isinstance(value, str) or not value:
        raise OpenImccBindingDigestUnavailableError()
    return value


def engine_binding_identity(melt_pack: Any, gas_pack: Any) -> dict[str, str]:
    """Return OpenIMCC's parsed-content identity for the packs used together."""

    package = _require_openimcc()
    owner = getattr(package, "engine_binding_identity", None)
    if not callable(owner):
        raise OpenImccBindingDigestUnavailableError()
    try:
        identity = owner(melt_pack, gas_pack)
    except Exception as exc:
        error = OpenImccBindingDigestUnavailableError()
        error.backend_status_reason += f" ({type(exc).__name__}: {exc})"
        error.args = (error.backend_status_reason,)
        raise error from exc
    result = {
        "engine_binding_digest": identity.digest,
        "melt_binding_digest": identity.melt_binding_digest,
        "condensate_table_digest": identity.condensate_table_digest,
        "gas_table_digest": identity.gas_table_digest,
    }
    if any(not isinstance(value, str) or not value for value in result.values()):
        raise OpenImccBindingDigestUnavailableError()
    return result


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
    simulator MeltBackend adapter.

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
    parent_oxide_x_star_ratios: dict[str, float] = {}
    parent_x = getattr(result, "parent_x", None)
    parent_x_star = getattr(result, "parent_x_star", None)
    if parent_x is not None and parent_x_star is not None:
        for name, nominal, free in zip(
            parent_oxides, parent_x, parent_x_star, strict=True
        ):
            nominal = float(nominal)
            free = float(free)
            if nominal > 0.0 and math.isfinite(nominal) and math.isfinite(free):
                parent_oxide_x_star_ratios[name] = free / nominal
    activities = {
        name: float(value)
        for name, value in zip(parent_oxides, result.parent_activity, strict=True)
    }
    if basis_type == "mol":
        composition_mol_for_fraction = composition
    else:
        composition_mol_for_fraction = {
            str(name): float(value)
            / resolve_species_formula(str(name)).molar_mass_kg_per_mol()
            for name, value in composition.items()
            if float(value) > 0.0
        }
    activity_coefficients: dict[str, dict[str, Any]] = {}
    for oxide, parent_activity in activities.items():
        single_activity, fraction = single_cation_activity_and_fraction(
            oxide, parent_activity, composition_mol_for_fraction
        )
        if fraction <= 0.0:
            continue
        component = single_cation_component_formula(oxide)
        activity_coefficients[component] = {
            "value": single_activity / fraction,
            "coefficient_basis": "single_cation",
            "standard_state": {
                "convention": "raoultian_pure_endmember",
                "phase": "l",
                "component_basis": component,
            },
        }
    package_labels = result.labels
    labels = ImccAdapterLabels(
        identity=package_labels.identity,
        coverage=package_labels.coverage,
        trust="internal-analytical",
        envelope_status=package_labels.envelope_status,
        flags=tuple(package_labels.flags),
        notices=tuple(package_labels.notices),
        acid_sink_ratio=(
            None
            if package_labels.acid_sink_ratio is None
            else float(package_labels.acid_sink_ratio)
        ),
    )
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
        activity_coefficients=activity_coefficients,
        parent_oxide_x_star_ratios=parent_oxide_x_star_ratios,
    )


evaluate_openimcc = evaluate


class OpenImccMeltBackend(MeltBackend):
    """Simulator MeltBackend wrapper around the packaged openimcc datapack."""

    name = "openimcc"
    backend_name = "openimcc"

    def __init__(self) -> None:
        self._available = False
        self._pack: Any | None = None
        self._last_error: str | None = None

    def initialize(self, config: dict) -> bool:
        del config
        self._available = False
        self._pack = None
        self._last_error = None
        try:
            self._pack = _load_pack("v1.0.2")
        except OpenImccUnavailableError as exc:
            self._last_error = str(exc)
            return False
        self._available = True
        return True

    def is_available(self) -> bool:
        return self._available and self._pack is not None

    def get_vapor_species(self) -> list[str]:
        return []

    def capabilities(self) -> dict[str, bool]:
        return dict(DEFAULT_BACKEND_CAPABILITIES)

    def get_engine_version(self) -> str:
        if self._pack is None:
            return "unavailable"
        return f"{self._pack.model_id} {self._pack.version}"

    def equilibrate(
        self,
        temperature_C: float,
        composition_kg: dict[str, float] | None = None,
        fO2_log: float | None = -9.0,
        pressure_bar: float = 1e-6,
        *,
        composition_mol: dict[str, float] | None = None,
        composition_mol_by_account: Mapping[str, Mapping[str, float]] | None = None,
        species_formula_registry: Mapping[str, Any] | None = None,
    ) -> EquilibriumResult:
        del species_formula_registry
        if composition_mol_by_account is not None:
            composition_mol, _ = split_cleaned_melt_account(composition_mol_by_account)
        if not self.is_available():
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                status="unavailable",
                warnings=[self._last_error or "openimcc backend not initialized"],
                phase_assemblage_available=False,
            )
        if not composition_mol and not composition_kg:
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                status="out_of_domain",
                warnings=["openimcc received empty melt composition"],
                phase_assemblage_available=False,
            )

        _require_openimcc()
        from openimcc.kernel import ImccNonconvergenceError, ImccRefusal

        try:
            result = evaluate(
                composition_mol=composition_mol,
                composition_kg=None if composition_mol else composition_kg,
                temperature_K=float(temperature_C) + 273.15,
                pack="v1.0.2",
            )
        except ImccNonconvergenceError as exc:
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                status="not_converged",
                warnings=[str(exc)],
                phase_assemblage_available=False,
            )
        except (ImccRefusal, OpenImccCompositionPolicyRefusal) as exc:
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                status="out_of_domain",
                warnings=[str(exc)],
                phase_assemblage_available=False,
            )
        return EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            status="ok",
            activity_coefficients=dict(result.parent_oxide_activities),
            phase_assemblage_available=False,
            liquid_fraction=None,
        )


__all__ = [
    "OPENIMCC_INSTALL_HINT",
    "OpenImccBridgeResult",
    "OpenImccMeltBackend",
    "OpenImccCleanedMeltResult",
    "OpenImccCompositionPolicyRefusal",
    "OpenImccUnavailableError",
    "OPENIMCC_PARENT_OXIDES",
    "FE2O3_TO_FEO_TOTAL_WT_FACTOR",
    "evaluate",
    "evaluate_cleaned_melt",
    "evaluate_openimcc",
]
