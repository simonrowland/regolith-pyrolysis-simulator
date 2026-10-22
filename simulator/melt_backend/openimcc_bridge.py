"""Optional bridge from simulator melt compositions to the openimcc package.

This module is diagnostic-only. Callers must make ``openimcc`` importable
explicitly; the bridge never falls back to an in-repository kernel when it is
absent.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import metadata
from importlib import resources
import json
import math
import numbers
from pathlib import Path
import subprocess
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

_IMCC_REFUSAL_ERROR_TYPE: type[BaseException] | None = (
    getattr(_openimcc, "ImccRefusal", None)
    if _openimcc is not None
    else None
)


OPENIMCC_INSTALL_HINT = (
    "python -m pip install --no-deps "
    "'openimcc @ git+https://github.com/simonrowland/openimcc'"
)
OPENIMCC_RECORDED_PIN = (
    "openimcc @ git+https://github.com/simonrowland/openimcc"
    "@66ec4a3d8fd4c0741ca2fdf9cf0569d4daec7d84"
)


def imported_openimcc_commit() -> str | None:
    """Return the commit for the imported package, if it can be verified."""

    module = _openimcc
    module_file = getattr(module, "__file__", None) if module is not None else None
    if not isinstance(module_file, str):
        return None
    try:
        imported_path = Path(module_file).resolve()
    except (OSError, RuntimeError):
        return None

    try:
        distribution = metadata.distribution("openimcc")
    except metadata.PackageNotFoundError:
        distribution = None

    if distribution is not None:
        distribution_files = distribution.files
        belongs_to_distribution = bool(
            distribution_files
            and any(
                Path(distribution.locate_file(entry)).resolve() == imported_path
                for entry in distribution_files
            )
        )
        if not belongs_to_distribution:
            # Some editable installs omit RECORD entries. Compare the imported
            # package file at the distribution's location rather than trusting
            # an unrelated openimcc dist-info elsewhere on sys.path.
            belongs_to_distribution = (
                Path(distribution.locate_file("openimcc/__init__.py")).resolve()
                == imported_path
            )
        if belongs_to_distribution:
            direct_url = distribution.read_text("direct_url.json")
            if direct_url:
                try:
                    direct_url_data = json.loads(direct_url)
                except (TypeError, json.JSONDecodeError):
                    direct_url_data = None
                vcs_info = (
                    direct_url_data.get("vcs_info")
                    if isinstance(direct_url_data, dict)
                    else None
                )
                commit_id = (
                    vcs_info.get("commit_id") if isinstance(vcs_info, dict) else None
                )
                if isinstance(commit_id, str) and commit_id:
                    return commit_id

    # A PYTHONPATH source checkout may shadow a stale distribution. In that
    # case report the checkout's HEAD, never the unrelated dist-info commit.
    if "site-packages" in imported_path.parts or any(
        part in {".venv", "venv", "env"} for part in imported_path.parts
    ):
        return None
    for checkout in imported_path.parents:
        if not (checkout / ".git").exists():
            continue
        try:
            relative_path = imported_path.relative_to(checkout)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(checkout),
                    "ls-files",
                    "--error-unmatch",
                    str(relative_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            result = subprocess.run(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        commit_id = result.stdout.strip()
        return commit_id or None
    return None
DEFAULT_BACKEND_PACK_NAME = "default"

_PACK_RESOURCE_NAMES = {
    "default": None,
    "published-core": "imcc-sf04-v1.0.2.json",
    "published-ext": "imcc-sf04-ext-v4.json",
    "d066-ext": "imcc-sf04-d066-ext-v1.json",
    "d066-kca-sensitivity": "imcc-sf04-d066-v1-kcaalsi2o7.json",
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
# Oxides openimcc.p2o5.evaluate_p2o5_activity accepts. Anything else is
# omitted from that call and named on a notice; it is not folded in.
_P2O5_ACTIVITY_COMPONENTS = frozenset({
    "SiO2", "CaO", "MgO", "FeO", "FeOeq", "TiO2",
    "Al2O3", "Na2O", "K2O", "P2O5", "MnO", "Fe2O3",
})
# Existing vapour-rail channels. Elemental P stays diagnostic-only.
P2O5_FLUX_SPECIES = ("P2", "P4", "PO", "PO2", "P4O6", "P4O10")
P2O5_DIAGNOSTIC_GAS_SPECIES = ("P", "P2", "P4", "PO", "PO2", "P4O6", "P4O10")
_PA_PER_BAR = 100_000.0

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

    def to_transport(self) -> dict[str, Any]:
        """Return every result field in a plain, multiprocessing-safe shape."""
        return {
            "parent_oxide_activities": dict(self.parent_oxide_activities),
            "parent_oxides": self.parent_oxides,
            "flags": self.flags,
            "notices": self.notices,
            "acid_sink_ratio": self.acid_sink_ratio,
            "pack_model_id": self.pack_model_id,
            "pack_version": self.pack_version,
            "pack_digest": self.pack_digest,
            "openimcc_version": self.openimcc_version,
            "envelope_status": self.envelope_status,
            "extrapolated": self.extrapolated,
            "labels": {
                "identity": dict(self.labels.identity),
                "coverage": dict(self.labels.coverage),
                "trust": self.labels.trust,
                "envelope_status": self.labels.envelope_status,
                "flags": self.labels.flags,
                "notices": self.labels.notices,
                "acid_sink_ratio": self.labels.acid_sink_ratio,
            },
            "coverage": dict(self.coverage),
            "activity_coefficients": {
                key: dict(value)
                for key, value in (self.activity_coefficients or {}).items()
            },
            "parent_oxide_x_star_ratios": dict(self.parent_oxide_x_star_ratios),
        }

    @classmethod
    def from_transport(cls, payload: Mapping[str, Any]) -> OpenImccBridgeResult:
        """Rebuild the immutable bridge DTO after crossing a process boundary."""
        labels = payload["labels"]
        return cls(
            parent_oxide_activities=payload["parent_oxide_activities"],
            parent_oxides=payload["parent_oxides"],
            flags=payload["flags"],
            notices=payload["notices"],
            acid_sink_ratio=payload["acid_sink_ratio"],
            pack_model_id=payload["pack_model_id"],
            pack_version=payload["pack_version"],
            pack_digest=payload["pack_digest"],
            openimcc_version=payload["openimcc_version"],
            envelope_status=payload["envelope_status"],
            extrapolated=payload["extrapolated"],
            labels=ImccAdapterLabels(**labels),
            coverage=payload["coverage"],
            activity_coefficients=payload["activity_coefficients"],
            parent_oxide_x_star_ratios=payload["parent_oxide_x_star_ratios"],
        )

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


def p2o5_extension_callable() -> Any | None:
    """Return openimcc.evaluate_p2o5_activity, or None when this pin lacks it."""

    package = _openimcc
    if package is None:
        return None
    function = getattr(package, "evaluate_p2o5_activity", None)
    return function if callable(function) else None


def _p2o5_moles(composition: Mapping[str, float]) -> float:
    raw = composition.get("P2O5", 0.0)
    if isinstance(raw, bool) or not isinstance(raw, numbers.Real):
        return 0.0
    try:
        moles = float(raw)
    except (OverflowError, TypeError, ValueError):
        return 0.0
    if not math.isfinite(moles) or moles <= 0.0:
        return 0.0
    return moles


def assess_p2o5_extension(
    composition_mol: Mapping[str, float],
    temperature_K: float | None = None,
    *,
    enabled: bool = True,
) -> dict[str, Any]:
    """Plain record for the opt-in dilute P2O5 activity.

    A missing callable, a missing temperature, or a refused activity call
    leaves P2O5 in the 1 wt% count. Only a successful call sets
    ``excluded_from_projection_count``. Fe2O3 is passed through unfolded;
    openimcc folds it and emits the Fe(III) notice.
    """

    canonical = _canonical_composition(composition_mol)
    p2o5_moles = _p2o5_moles(canonical)
    record: dict[str, Any] = {
        "enabled": bool(enabled),
        "available": p2o5_extension_callable() is not None,
        "evaluated": False,
        "excluded_from_projection_count": False,
        "activity": None,
        "log10_activity": None,
        "gamma": None,
        "x_p2o5": None,
        "band_log10_activity": None,
        "notices": [],
        "omitted_components": {},
        "p2o5_moles": p2o5_moles,
    }
    if not enabled or p2o5_moles <= 0.0:
        return record
    function = p2o5_extension_callable()
    if function is None:
        record["notices"] = [{
            "code": "openimcc_p2o5_extension_unavailable",
            "message": (
                "installed openimcc has no evaluate_p2o5_activity; "
                "P2O5 stays in the 1 wt% unsupported-projection count"
            ),
        }]
        return record
    temperature: float | None
    if isinstance(temperature_K, bool) or not isinstance(temperature_K, numbers.Real):
        temperature = None
    else:
        try:
            temperature = float(temperature_K)
        except (OverflowError, TypeError, ValueError):
            temperature = None
    if (
        temperature is None
        or not math.isfinite(temperature)
        or temperature <= 0.0
    ):
        record["notices"] = [{
            "code": "openimcc_p2o5_extension_temperature_missing",
            "message": (
                "P2O5 activity was not evaluated because temperature_K "
                "is missing or not finite and positive; P2O5 stays in "
                "the 1 wt% unsupported-projection count"
            ),
        }]
        return record
    accepted: dict[str, float] = {}
    omitted: dict[str, float] = {}
    for name, amount in canonical.items():
        if isinstance(amount, bool) or not isinstance(amount, numbers.Real):
            continue
        try:
            moles = float(amount)
        except (OverflowError, TypeError, ValueError):
            continue
        if not math.isfinite(moles) or moles <= 0.0:
            continue
        if name in _P2O5_ACTIVITY_COMPONENTS:
            accepted[name] = moles
        else:
            omitted[name] = moles
    record["omitted_components"] = omitted
    notices: list[dict[str, Any]] = []
    if omitted:
        notices.append({
            "code": "p2o5_activity_basis_omitted_components",
            "components": dict(omitted),
            "message": (
                "P2O5 activity omitted components the dilute regression "
                "does not accept; phosphorus is still modelled on the "
                "accepted oxide basis"
            ),
        })
    try:
        result = function(accepted, temperature)
    except Exception as exc:
        notices.append({
            "code": "openimcc_p2o5_extension_refused",
            "message": (
                "evaluate_p2o5_activity refused the composition "
                f"({type(exc).__name__}: {exc}); P2O5 stays in the "
                "1 wt% unsupported-projection count"
            ),
        })
        record["notices"] = notices
        return record
    record["evaluated"] = True
    record["excluded_from_projection_count"] = True
    record["activity"] = float(result.activity)
    record["log10_activity"] = float(result.log10_activity)
    record["gamma"] = float(result.gamma)
    record["x_p2o5"] = float(result.x_p2o5)
    band = getattr(result, "band_log10_activity", None)
    if band is not None:
        record["band_log10_activity"] = [float(band[0]), float(band[1])]
    notice = getattr(result, "notice", None)
    if notice is not None:
        limitations = list(getattr(notice, "limitations", ()) or ())
        notices.append({
            "code": str(getattr(notice, "code", "imcc_p2o5_activity_domain_notice")),
            "regression": str(getattr(notice, "regression", "")),
            "violated_axes": list(getattr(notice, "violated_axes", ()) or ()),
            "band_width_dex": float(getattr(notice, "band_width_dex", 0.0) or 0.0),
            "limitations": limitations,
            # The existing warning formatter prints code plus message.
            # Limitations are the metallic-Fe and Fe(III) flags.
            "message": " ".join(str(item) for item in limitations),
        })
    for extra in getattr(result, "additional_notices", ()) or ():
        notices.append({
            "code": str(getattr(extra, "code", "")),
            "message": str(getattr(extra, "message", "")),
        })
    record["notices"] = notices
    return record


def _extension_visible(extension: Mapping[str, Any]) -> bool:
    return bool(extension.get("enabled")) and float(extension.get("p2o5_moles") or 0.0) > 0.0


def parent_maps_with_p2o5(
    activities: Mapping[str, float],
    parent_oxides: tuple[str, ...] | list[str],
    extension: Mapping[str, Any] | None,
) -> tuple[dict[str, float], tuple[str, ...], bool]:
    """Attach a(P2O5) when the extension evaluated. Other parents stay put."""

    updated = {str(name): float(value) for name, value in activities.items()}
    oxides = tuple(str(name) for name in parent_oxides)
    if not isinstance(extension, Mapping) or not extension.get("evaluated"):
        return updated, oxides, False
    try:
        activity = float(extension.get("activity"))
    except (TypeError, ValueError):
        return updated, oxides, False
    if not math.isfinite(activity) or activity <= 0.0:
        return updated, oxides, False
    updated["P2O5"] = activity
    if "P2O5" not in oxides:
        oxides = oxides + ("P2O5",)
    return updated, oxides, True


def p_gas_notices(gas_result: Any) -> list[dict[str, Any]]:
    """Copy openimcc P-gas notices, including the >1 bar complete-loss notice."""

    notices: list[dict[str, Any]] = []
    for notice in getattr(gas_result, "notices", ()) or ():
        if isinstance(notice, Mapping):
            notices.append(dict(notice))
            continue
        code = getattr(notice, "code", None)
        if not code:
            continue
        item: dict[str, Any] = {
            "code": str(code),
            "message": str(getattr(notice, "message", "")),
            "species": [str(name) for name in (getattr(notice, "species", ()) or ())],
        }
        total = getattr(notice, "total_pressure_bar", None)
        if isinstance(total, numbers.Real) and not isinstance(total, bool):
            item["total_pressure_bar"] = float(total)
        notices.append(item)
    return notices


def _p2o5_gas_unavailable_notice(exc: BaseException) -> dict[str, str]:
    return {
        "code": "openimcc_p2o5_gas_unavailable",
        "message": (
            "openimcc gas evaluation refused the P2O5 parent "
            f"({type(exc).__name__}: {exc}); phosphorus partial "
            "pressures were not produced"
        ),
    }


def _without_p2o5(
    activities: Mapping[str, float],
    parent_oxides: tuple[str, ...] | list[str],
) -> tuple[dict[str, float], tuple[str, ...]]:
    return (
        {
            name: value
            for name, value in activities.items()
            if name != "P2O5"
        },
        tuple(name for name in parent_oxides if name != "P2O5"),
    )


def call_with_p2o5_parents(
    fn: Any,
    activities: Mapping[str, float],
    parent_oxides: tuple[str, ...] | list[str],
    extension: Mapping[str, Any] | None,
) -> tuple[Any, list[dict[str, Any]], tuple[str, ...]]:
    """Call ``fn(activities, parent_oxides)`` with a(P2O5) when evaluated.

    A refusal retries without that parent and returns
    ``openimcc_p2o5_gas_unavailable`` plus the parent tuple that call
    used. Callers that did not attach P2O5 still raise.
    """

    attached_activities, oxides, attached = parent_maps_with_p2o5(
        activities, parent_oxides, extension
    )
    try:
        return fn(attached_activities, oxides), [], oxides
    except Exception as exc:
        if not attached:
            raise
        bare_activities, bare_oxides = _without_p2o5(
            attached_activities, oxides
        )
        return fn(bare_activities, bare_oxides), [
            _p2o5_gas_unavailable_notice(exc)
        ], bare_oxides


def attached_gas_parents(
    activities: Mapping[str, float],
    parent_oxides: tuple[str, ...] | list[str],
    extension: Mapping[str, Any] | None,
    probe: Any,
) -> tuple[dict[str, float], tuple[str, ...], list[dict[str, Any]]]:
    """Parent set a gas call will actually use.

    ``probe(activities, oxides)`` is attempted with a(P2O5) attached.
    A refusal returns the unattached parents and
    ``openimcc_p2o5_gas_unavailable``. Channel metadata and the pressure
    model must both use the returned maps, including on that refusal.
    """

    attached_activities, oxides, attached = parent_maps_with_p2o5(
        activities, parent_oxides, extension
    )
    if not attached:
        return attached_activities, oxides, []
    try:
        probe(attached_activities, oxides)
    except Exception as exc:
        bare_activities, bare_oxides = _without_p2o5(
            attached_activities, oxides
        )
        return bare_activities, bare_oxides, [
            _p2o5_gas_unavailable_notice(exc)
        ]
    return attached_activities, oxides, []


def evaluate_openimcc_gas_with_p2o5(
    evaluate_gas: Any,
    activities: Mapping[str, float],
    temperature_K: float,
    fo2: float,
    datapack: Any,
    parent_oxides: tuple[str, ...] | list[str],
    extension: Mapping[str, Any] | None,
    **kwargs: Any,
) -> tuple[Any, list[dict[str, Any]]]:
    """Call evaluate_gas with a(P2O5) when the extension evaluated.

    A gas refusal retries without the P2O5 parent so the rest of the melt
    still solves. The 1 wt% exclusion is not changed here. Success also
    returns the typed P-gas notices, including complete volatilisation above 1 bar.
    """

    def _call(attached_activities: Mapping[str, float], oxides: tuple[str, ...]) -> Any:
        return evaluate_gas(
            attached_activities,
            temperature_K,
            fo2,
            datapack,
            parent_oxides=oxides,
            **kwargs,
        )

    result, notices, _oxides = call_with_p2o5_parents(
        _call, activities, parent_oxides, extension
    )
    if notices:
        return result, notices
    return result, p_gas_notices(result)


def composition_moles_for_p2o5(
    composition_mol: Mapping[str, float] | None = None,
    composition_kg: Mapping[str, float] | None = None,
) -> dict[str, float]:
    """Mole inventory for the P2O5 assessment.

    A non-empty ``composition_mol`` wins, matching ``evaluate()``. An empty
    mole map falls through to kilograms. A kilogram inventory is converted
    with ``MOLAR_MASS`` (g/mol) and 1000 g/kg. Names without a molar mass
    are omitted; the activity call names the ones it cannot accept.
    """

    # An empty mole map is the same "not supplied" signal evaluate() uses.
    if composition_mol:
        canonical = _canonical_composition(composition_mol)
        return {
            name: float(amount)
            for name, amount in canonical.items()
            if isinstance(amount, numbers.Real)
            and not isinstance(amount, bool)
        }
    if not composition_kg:
        return {}
    canonical = _canonical_composition(composition_kg)
    moles: dict[str, float] = {}
    for name, mass_kg in canonical.items():
        if isinstance(mass_kg, bool) or not isinstance(mass_kg, numbers.Real):
            continue
        molar_g = MOLAR_MASS.get(str(name))
        if molar_g is None:
            continue
        moles[str(name)] = float(mass_kg) * 1000.0 / float(molar_g)
    return moles


def p2o5_vapour_pressures_pa(
    extension: Mapping[str, Any] | None,
    *,
    temperature_K: float,
    fo2_bar: float,
    process_phase: str | None,
) -> dict[str, Any]:
    """Partial pressures for the existing P-carrier channels.

    Stage 0 is left untouched. Elemental P is returned only on
    ``diagnostic_pressures_Pa``. Flux injection requires every existing
    carrier. 1 bar = 1e5 Pa.
    """

    record: dict[str, Any] = {
        "inject": False,
        "flux_pressures_Pa": {},
        "diagnostic_pressures_Pa": {},
        "notices": [],
        "flux_species": [],
    }
    if process_phase == "stage0":
        return record
    if not isinstance(extension, Mapping) or not extension.get("evaluated"):
        return record
    try:
        activity = float(extension.get("activity"))
        temperature = float(temperature_K)
        fo2 = float(fo2_bar)
    except (TypeError, ValueError):
        record["notices"].append({
            "code": "openimcc_p2o5_gas_unavailable",
            "message": (
                "P2O5 gas pressures were not evaluated because activity, "
                "temperature, or melt fO2 was not numeric"
            ),
        })
        return record
    if (
        not math.isfinite(activity)
        or activity <= 0.0
        or not math.isfinite(temperature)
        or temperature <= 0.0
        or not math.isfinite(fo2)
        or fo2 <= 0.0
    ):
        record["notices"].append({
            "code": "openimcc_p2o5_gas_unavailable",
            "message": (
                "P2O5 gas pressures were not evaluated because melt fO2 "
                "or temperature was not finite and positive"
            ),
        })
        return record
    package = _openimcc
    evaluate_gas = getattr(package, "evaluate_gas", None) if package is not None else None
    load_gas = getattr(package, "load_gas_datapack", None) if package is not None else None
    if not callable(evaluate_gas) or not callable(load_gas):
        record["notices"].append({
            "code": "openimcc_p2o5_gas_unavailable",
            "message": "installed openimcc cannot evaluate P2O5 gas pressures",
        })
        return record
    try:
        gas_result, gas_notices = evaluate_openimcc_gas_with_p2o5(
            evaluate_gas,
            {"P2O5": activity},
            temperature,
            fo2,
            load_gas(),
            parent_oxides=("P2O5",),
            extension=extension,
            allow_extrapolation=True,
        )
    except Exception as exc:
        record["notices"].append(_p2o5_gas_unavailable_notice(exc))
        return record
    record["notices"].extend(gas_notices)
    if any(notice.get("code") == "openimcc_p2o5_gas_unavailable" for notice in gas_notices):
        return record
    diagnostic: dict[str, float] = {}
    for species in P2O5_DIAGNOSTIC_GAS_SPECIES:
        if species not in gas_result:
            continue
        try:
            bar = float(gas_result[species])
        except (TypeError, ValueError):
            continue
        if math.isfinite(bar) and bar >= 0.0:
            diagnostic[species] = bar * _PA_PER_BAR
    record["diagnostic_pressures_Pa"] = diagnostic
    flux: dict[str, float] = {}
    for species in P2O5_FLUX_SPECIES:
        pressure = diagnostic.get(species)
        if pressure is None:
            record["notices"].append({
                "code": "openimcc_p2o5_gas_incomplete",
                "message": (
                    f"openimcc did not return a finite {species} pressure; "
                    "phosphorus carriers were not written onto the vapour rail"
                ),
            })
            record["flux_pressures_Pa"] = {}
            record["flux_species"] = []
            return record
        flux[species] = pressure
    record["flux_pressures_Pa"] = flux
    record["flux_species"] = list(P2O5_FLUX_SPECIES)
    record["inject"] = True
    return record


# The CEA carrier activity_input already declares this identity. openimcc's
# published value is the Turkdogan-Pearson hypothetical liquid at 1 atm
# after the gas-pack shift 3.5 R T ln(1.01325) onto 1 bar. That shifted
# number is what the carrier's Raoultian liquid standard state names.
_P2O5_RAOULTIAN_LIQUID_1BAR = {
    "convention": "raoultian_pure_endmember",
    "phase": "liquid",
    "reference_pressure_bar": 1.0,
    "reference_temperature_K": None,
    "component_basis": "raoultian_pure_endmember",
}
_P2O5_STANDARD_STATE_RECONCILIATION = (
    "openimcc a(P2O5) is the Turkdogan-Pearson hypothetical-liquid "
    "activity at 1 atm after the published shift 3.5*R*T*ln(1.01325) "
    "onto the gas-pack Jung-Hudon 1 bar standard. That shifted value "
    "is the Raoultian pure-liquid P2O5 activity declared by the CEA "
    "carrier activity_input. It is not an unshifted Turkdogan gamma "
    "and not pure-endmember a=1."
)


def _p2o5_domain_notice(extension: Mapping[str, Any]) -> dict[str, Any]:
    for notice in extension.get("notices") or []:
        if (
            isinstance(notice, Mapping)
            and notice.get("code") == "imcc_p2o5_activity_domain_notice"
        ):
            return dict(notice)
    return {}


def p2o5_flux_activity_evidence(
    extension: Mapping[str, Any] | None,
) -> tuple[dict[str, float], dict[str, dict[str, Any]]]:
    """a(P2O5) and provenance for the six existing flux carriers.

    The value is openimcc's shifted hypothetical-liquid activity. The
    catalog already declares that Raoultian liquid 1 bar state, so the
    reported activity shares it. Provenance names the shift. Elemental
    P is not a flux carrier and is omitted. Violated regression axes,
    or components the regression cannot accept, label the authority
    extrapolated; a clean evaluation is flagged. The metallic-iron and
    Fe(III) limitations stay on the warning.
    """

    if not isinstance(extension, Mapping) or not extension.get("evaluated"):
        return {}, {}
    try:
        activity = float(extension.get("activity"))
    except (TypeError, ValueError):
        return {}, {}
    if not math.isfinite(activity) or activity <= 0.0:
        return {}, {}
    domain = _p2o5_domain_notice(extension)
    violated = [str(item) for item in (domain.get("violated_axes") or ())]
    warning_parts = [
        str(item) for item in (domain.get("limitations") or ()) if str(item)
    ]
    for notice in extension.get("notices") or []:
        if not isinstance(notice, Mapping):
            continue
        code = str(notice.get("code") or "")
        if code in {
            "p2o5_feiii_phosphate_chemistry_not_modelled",
            "p2o5_activity_basis_omitted_components",
        }:
            message = str(notice.get("message") or code)
            if message and message not in warning_parts:
                warning_parts.append(message)
    omitted = bool(extension.get("omitted_components"))
    extrapolated = bool(violated) or omitted
    provenance = {
        "parent_oxide": "P2O5",
        "source_label": "openimcc_p2o5_gas",
        "equivalent_parent_oxide_activity": activity,
        "melt_parent_oxide_activity": activity,
        "melt_oxide_activity": activity,
        "melt_oxide_activity_authority_status": (
            "extrapolated" if extrapolated else "flagged"
        ),
        "melt_oxide_activity_warning": " ".join(warning_parts),
        "melt_oxide_activity_reference_state": dict(
            _P2O5_RAOULTIAN_LIQUID_1BAR
        ),
        "activity_standard_state_reconciliation": (
            _P2O5_STANDARD_STATE_RECONCILIATION
        ),
        "regression": str(domain.get("regression") or ""),
        "gamma_domain_authority": {
            "authority_status": (
                "out_of_gamma_domain" if violated else "flagged"
            ),
            "violated_axes": list(violated),
        },
    }
    activities = {species: activity for species in P2O5_FLUX_SPECIES}
    provenances = {
        species: {
            **provenance,
            "melt_oxide_activity_reference_state": dict(
                _P2O5_RAOULTIAN_LIQUID_1BAR
            ),
            "gamma_domain_authority": {
                "authority_status": provenance["gamma_domain_authority"][
                    "authority_status"
                ],
                "violated_axes": list(violated),
            },
        }
        for species in P2O5_FLUX_SPECIES
    }
    return activities, provenances


def p2o5_activity_reference_state(
    extension: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Liquid reference for a(P2O5) on a battery result, or None."""

    if not isinstance(extension, Mapping):
        return None
    _activities, provenance = p2o5_flux_activity_evidence(extension)
    if not provenance:
        return None
    row = next(iter(provenance.values()))
    try:
        activity = float(extension.get("activity"))
    except (TypeError, ValueError):
        return None
    return {
        **row["melt_oxide_activity_reference_state"],
        "component": "P2O5",
        "activity": activity,
        "regression": row["regression"],
        "reconciliation": row["activity_standard_state_reconciliation"],
        "authority_status": row["melt_oxide_activity_authority_status"],
        "limitations_warning": row["melt_oxide_activity_warning"],
        "gamma": None if extension is None else extension.get("gamma"),
    }


def _cleaned_melt_policy(
    composition_mol: Mapping[str, float],
    *,
    enable_p2o5_extension: bool = True,
    temperature_K: float | None = None,
    p2o5_extension: Mapping[str, Any] | None = None,
) -> tuple[dict[str, float], dict[str, Any]]:
    source_wt_pct, cleaned_composition_mol = _cleaned_melt_projection(
        composition_mol
    )
    if p2o5_extension is None:
        extension = assess_p2o5_extension(
            composition_mol,
            temperature_K,
            enabled=enable_p2o5_extension,
        )
    else:
        extension = dict(p2o5_extension)
        extension["notices"] = [
            dict(notice)
            for notice in extension.get("notices") or []
            if isinstance(notice, Mapping)
        ]
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
    if (
        extension.get("excluded_from_projection_count")
        and "P2O5" in dropped
    ):
        gate_dropped = {
            name: value
            for name, value in dropped.items()
            if name not in _OPENIMCC_CRMN_RELAXED_OXIDES and name != "P2O5"
        }
        gate_mol = {
            name: value
            for name, value in dropped_mol_per_kg.items()
            if name not in _OPENIMCC_CRMN_RELAXED_OXIDES and name != "P2O5"
        }
        gate_unavailable = {
            name: value
            for name, value in unavailable_reasons.items()
            if name not in _OPENIMCC_CRMN_RELAXED_OXIDES and name != "P2O5"
        }
        threshold_classification = classify_projected_bulk(
            gate_dropped,
            source_sum_wt_pct=100.0,
            dropped_component_mol_per_kg=gate_mol,
            dropped_component_mol_unavailable_reasons=gate_unavailable,
        )
        p2o5_gate_notice = {
            "code": "openimcc_projection_p2o5_excluded",
            "wt_pct": {"P2O5": dropped["P2O5"]},
            "message": (
                "P2O5 remains projected from the IMCC parent input, but "
                "does not count toward the shared 1.0 wt% gate after the "
                "opt-in dilute activity extension evaluated"
            ),
        }
        classification_record["openimcc_projection_p2o5_excluded"] = dict(
            p2o5_gate_notice
        )
        classification_record["projection_verdict"] = (
            threshold_classification.verdict
        )
        classification_record["thresholded_dropped_total_wt_pct"] = (
            threshold_classification.dropped_total_wt_pct
        )
        classification_record["thresholded_dropped_components"] = list(
            threshold_classification.dropped_components
        )
    refusal_diagnostics: dict[str, Any] = {
        PROJECTED_BULK_CLASSIFICATION_KEY: classification_record,
    }
    if _extension_visible(extension):
        refusal_diagnostics["p2o5_extension"] = extension
    if threshold_classification.verdict == "over_threshold":
        raise OpenImccCompositionPolicyRefusal(
            "openimcc_projection_over_threshold",
            (
                "cleaned-melt components outside IMCC parents exceed the "
                f"{threshold_classification.threshold_wt_pct:g} wt% "
                "projected-bulk limit: "
                f"{threshold_classification.dropped_total_wt_pct:.12g} wt%"
            ),
            diagnostics=refusal_diagnostics,
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
    p2o5_gate_notice = classification_record.get(
        "openimcc_projection_p2o5_excluded"
    )
    if isinstance(p2o5_gate_notice, Mapping):
        policy["openimcc_projection_p2o5_excluded"] = dict(p2o5_gate_notice)
    if _extension_visible(extension):
        policy["p2o5_extension"] = extension
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
    enable_p2o5_extension: bool = True,
) -> OpenImccCleanedMeltResult:
    """Apply C3's cleaned-melt policy, then call the C1 bridge."""

    _require_openimcc()
    cleaned_composition_mol, policy = _cleaned_melt_policy(
        composition_mol,
        enable_p2o5_extension=enable_p2o5_extension,
        temperature_K=temperature_K,
    )
    composition_wt_pct = _composition_wt_pct_from_moles(
        cleaned_composition_mol
    )
    bridge = evaluate(
        composition_mol=cleaned_composition_mol,
        temperature_K=temperature_K,
        pack=DEFAULT_BACKEND_PACK_NAME,
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
    """Load the packaged default or a named pack variant.

    ``published-core`` and ``published-ext`` select published reproductions;
    ``d066-ext`` and ``d066-kca-sensitivity`` select D066 variants. ``default``
    always delegates to openimcc's packaged default.
    """

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
    pack: str | None = None,
    allow_extrapolation: bool = False,
    allow_out_of_envelope: bool = False,
) -> OpenImccBridgeResult:
    """Evaluate openimcc using simulator composition and provenance conventions.

    ``composition_mol`` is the canonical simulator input.  ``composition_kg``
    is accepted as the external mass projection and is passed to openimcc as a
    ``wt`` composition; absolute mass units cancel in the normalization.  If
    both are supplied, the molar composition takes precedence, matching the
    simulator MeltBackend adapter.

    ``pack`` accepts ``"default"``, ``"published-core"``, ``"published-ext"``,
    ``"d066-ext"`` or ``"d066-kca-sensitivity"``. Omitting it selects the
    simulator backend's current default pack. The two extrapolation switches
    are explicit and default to refusal. Positive ``Fe2O3`` remains an openimcc typed refusal
    because the IMCC adapter requires the caller's
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

    if pack is None:
        pack = DEFAULT_BACKEND_PACK_NAME
    loaded_pack = _load_pack(pack)
    result = package.evaluate(
        composition,
        float(temperature_K),
        loaded_pack,
        basis_type=basis_type,
        enable_sp_extension=pack in {"published-ext", "d066-ext"},
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
            self._pack = _load_pack(DEFAULT_BACKEND_PACK_NAME)
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
                pack=DEFAULT_BACKEND_PACK_NAME,
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
