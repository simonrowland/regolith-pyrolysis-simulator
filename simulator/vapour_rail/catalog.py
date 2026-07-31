"""Typed compiler for the schema-v2 hot-vapour catalog.

The checked-in YAML is owner-facing and grouped into four readable strata.
This module is the sole place that validates those strata, compiles pressure
evaluators, and projects the temporary schema-v1 view used by legacy consumers
during the U1--U5 shadow period.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
import math
import re
from types import MappingProxyType
from typing import Any


SCHEMA_VERSION = 2
FOUR_STRATA = (
    "physical_properties",
    "fiat_routing",
    "vaporisation_coefficients",
    "code_metadata",
)
DEFAULT_EXTRAPOLATION_POLICY = "conservative_slope_continuation"
OUT_OF_RANGE_STATUS = "out_of_range_conservative_continuation"

# Identity-keyed memo for schema-v2 compile + legacy projection. Payloads are
# treated as immutable after first compile (production loaders deepcopy once and
# never mutate). Keyed by id(payload) with a strong identity check to survive
# allocator reuse. Bounded LRU so long-lived processes cannot grow without limit.
# Restores the pre-VR-7 hot-path cost profile when vapor_pressure_legacy_view is
# called per species on the condensation deposition-flux path (P1-1).
_CATALOG_COMPILE_CACHE_MAX = 8
_catalog_compile_cache: OrderedDict[int, tuple[Any, "VapourRailCatalog"]] = OrderedDict()
_legacy_view_cache: OrderedDict[int, tuple[Any, dict[str, Any]]] = OrderedDict()

# Charge / state aliases that must fold before ledger or cache payloads (VR-7 / REV5).
# Keys are accepted input spellings; values are canonical species catalog IDs.
# Only attested spellings with existing catalog targets are mapped — unmapped
# inputs pass through via canonicalize_charge_alias (P2-1).
CHARGE_ALIAS_CANONICAL: Mapping[str, str] = MappingProxyType(
    {
        "ClO4": "ClO4",
        "ClO4-": "ClO4",
        "ClO4_minus": "ClO4",
        "ClO4_anion": "ClO4",
        "perchlorate": "ClO4",
        # Attested trace-table / feedstock spellings for ClO4 only (NO3/SO4/CO3
        # catalog rows do not exist yet; do not invent canonical keys).
    }
)


def _cache_put(
    cache: OrderedDict[int, tuple[Any, Any]],
    payload: Any,
    value: Any,
    *,
    maxsize: int = _CATALOG_COMPILE_CACHE_MAX,
) -> None:
    key = id(payload)
    cache[key] = (payload, value)
    cache.move_to_end(key)
    while len(cache) > maxsize:
        cache.popitem(last=False)


def _cache_get(
    cache: OrderedDict[int, tuple[Any, Any]], payload: Any
) -> Any | None:
    key = id(payload)
    hit = cache.get(key)
    if hit is None:
        return None
    cached_payload, value = hit
    if cached_payload is not payload:
        del cache[key]
        return None
    cache.move_to_end(key)
    return value


def clear_vapor_pressure_view_caches() -> None:
    """Drop compile/legacy-view memos (tests that mutate payloads in place)."""

    _catalog_compile_cache.clear()
    _legacy_view_cache.clear()


def canonicalize_charge_alias(species_id: str) -> str:
    """Fold charge/state spellings to the canonical catalog species id.

    Bare chemical IDs pass through unchanged. Unknown aliases also pass through
    so callers can layer collision-gas canonicalization separately.
    """
    raw = str(species_id).strip()
    if not raw:
        raise ValueError("species_id is required")
    return CHARGE_ALIAS_CANONICAL.get(raw, raw)
_LEGACY_CONDENSATION_REFERENCE_ORDER = (
    "Fe", "SiO", "Mg", "Na", "K", "Ca", "Mn", "Cr", "CrO2", "NaCl", "KCl"
)
_LEGACY_FIELD_ORDER: Mapping[str, tuple[str, ...]] = {
    "Na": (
        "formula", "molar_mass_g_mol", "electrons_per_atom", "parent_oxide",
        "fit_target", "authority_class", "declared_compensation",
        "declared_compensation_note", "pressure_bracket", "residual_dex",
        "confidence_tier", "pseudo_antoine_status", "backsolve",
        "pure_component_antoine", "evaporation_alpha", "antoine",
        "valid_range_K", "boiling_point_C", "condensation_T_C_at_1mbar", "notes",
    ),
    "K": (
        "formula", "molar_mass_g_mol", "electrons_per_atom", "parent_oxide",
        "fit_target", "residual_dex", "confidence_tier", "gamma_domain_K",
        "gamma_authority", "reaction", "pure_component_antoine",
        "evaporation_alpha", "oxide_activity_exponent", "pO2_exponent",
        "pO2_reference_bar", "antoine", "valid_range_K", "boiling_point_C",
        "condensation_T_C_at_1mbar", "notes",
    ),
    "Mg": (
        "formula", "molar_mass_g_mol", "electrons_per_atom", "parent_oxide",
        "fit_target", "authority_class", "extrapolation_policy",
        "total_source_certified_range_K", "source", "pure_component_antoine",
        "reconstructed_vapor_pressure_segment", "evaporation_alpha",
        "boiling_point_C", "condensation_T_C_at_1mbar", "notes",
        "gas_rail_standard_reaction",
    ),
    "Fe": (
        "formula", "molar_mass_g_mol", "electrons_per_atom", "parent_oxide",
        "fit_target", "authority_class", "pseudo_antoine_status", "residual_dex",
        "confidence_tier", "backsolve", "pure_component_antoine",
        "evaporation_alpha", "antoine", "valid_range_K", "boiling_point_C",
        "condensation_T_C_at_1mbar", "notes",
    ),
    "Ca": (
        "formula", "molar_mass_g_mol", "electrons_per_atom", "parent_oxide",
        "fit_target", "authority_class", "source", "pure_component_antoine",
        "evaporation_alpha", "antoine", "valid_range_K", "boiling_point_C",
        "condensation_T_C_at_1mbar", "notes", "gas_rail_standard_reaction",
    ),
    "Al": (
        "formula", "molar_mass_g_mol", "electrons_per_atom", "parent_oxide",
        "fit_target", "authority_class", "source", "pure_component_antoine",
        "evaporation_alpha", "antoine", "valid_range_K", "boiling_point_C",
        "condensation_T_C_at_1mbar", "notes", "liquid_oxide_standard_reaction",
    ),
    "Si": (
        "formula", "molar_mass_g_mol", "electrons_per_atom", "parent_oxide",
        "fit_target", "consumer_status", "source", "pure_component_antoine",
        "evaporation_alpha", "antoine", "valid_range_K", "boiling_point_C",
        "condensation_T_C_at_1mbar", "notes",
    ),
    "Ti": (
        "formula", "molar_mass_g_mol", "electrons_per_atom", "parent_oxide",
        "fit_target", "authority_class", "source", "pure_component_antoine",
        "evaporation_alpha", "antoine", "valid_range_K", "boiling_point_C",
        "condensation_T_C_at_1mbar", "notes", "liquid_oxide_standard_reaction",
    ),
    "Cr": (
        "formula", "molar_mass_g_mol", "electrons_per_atom", "parent_oxide",
        "fit_target", "authority_class", "source", "pure_component_antoine",
        "evaporation_alpha", "antoine", "valid_range_K", "boiling_point_C",
        "condensation_T_C_at_1mbar", "notes", "liquid_oxide_standard_reaction",
    ),
    "Mn": (
        "formula", "molar_mass_g_mol", "electrons_per_atom", "parent_oxide",
        "fit_target", "authority_class", "source", "pure_component_antoine",
        "evaporation_alpha", "antoine", "valid_range_K", "boiling_point_C",
        "condensation_T_C_at_1mbar", "notes", "liquid_oxide_standard_reaction",
    ),
    "SiO": (
        "formula", "molar_mass_g_mol", "parent_oxide", "fit_target",
        "authority_class", "confidence_tier", "reaction", "stoich_oxide_per_vapor",
        "stoich_O2_per_vapor", "evaporation_alpha", "antoine", "valid_range_K",
        "condensation_T_C", "condensation_product",
        "condensation_products_mol_per_mol_vapor", "suppression_equation",
        "pO2_reference_bar", "suppression_factor_at_1mbar_O2", "notes",
    ),
    "CrO2": (
        "formula", "molar_mass_g_mol", "parent_oxide", "fit_target",
        "authority_class", "reaction", "stoich_oxide_per_vapor",
        "stoich_O2_per_vapor", "evaporation_alpha_policy", "antoine",
        "oxide_activity_exponent", "pO2_exponent", "pO2_reference_bar",
        "residual_dex", "valid_range_K", "vaporock_janaf0_shomate", "source",
        "condensation_T_C", "condensation_setpoint_C", "condensation_product",
        "condensation_products_mol_per_mol_vapor", "condensation_product_accounts",
        "notes",
    ),
    "NaCl": (
        "formula", "molar_mass_g_mol", "fit_target", "carrier_is_own_vapor",
        "pure_component_antoine", "antoine", "valid_range_K", "boiling_point_C",
        "condensation_T_C_at_1mbar", "evaporation_alpha", "notes",
    ),
    "KCl": (
        "formula", "molar_mass_g_mol", "fit_target", "carrier_is_own_vapor",
        "source_drift_dex", "source_drift_note", "pure_component_antoine",
        "antoine", "valid_range_K", "boiling_point_C",
        "condensation_T_C_at_1mbar", "evaporation_alpha", "notes",
    ),
    "NaF": (
        "formula", "molar_mass_g_mol", "fit_target", "carrier_is_own_vapor",
        "confidence", "interval_required", "certified_point", "valid_range_K",
        "boiling_point_C", "condensation_T_C_at_1mbar", "notes",
    ),
}


class CatalogCompileError(ValueError):
    """Raised when schema-v2 data cannot be compiled without inference."""


class PressureObservable(str, Enum):
    EQUILIBRIUM_PARTIAL_PRESSURE = "equilibrium_partial_pressure"
    PURE_COMPONENT_SATURATION_PRESSURE = "pure_component_saturation_pressure"
    TOTAL_MIXTURE_PRESSURE = "total_mixture_pressure"
    ASSOCIATION_PARTIAL_PRESSURE = "association_partial_pressure"


class ValidationStatus(str, Enum):
    PENDING = "pending_validation"
    VALIDATED = "validated"


@dataclass(frozen=True)
class PressureEvaluation:
    pressure_pa: float
    pressure_observable: PressureObservable
    validation_status: ValidationStatus
    out_of_range: bool = False
    status: str | None = None
    acquisition_flag: str | None = None


@dataclass(frozen=True)
class _ReferencePressureModel:
    evaluator_family: str
    coefficients: Mapping[str, Any]
    points: tuple[tuple[float, float], ...]

    def log10_pressure(self, temperature_K: float) -> float:
        if self.evaluator_family == "antoine":
            try:
                A = float(self.coefficients["A"])
                B = float(self.coefficients["B"])
                C = float(self.coefficients["C"])
            except (KeyError, TypeError, ValueError) as exc:
                raise CatalogCompileError(
                    "Antoine reference model requires numeric A, B, and C"
                ) from exc
            if temperature_K + C <= 0.0:
                raise CatalogCompileError(
                    "Antoine reference model has non-positive denominator"
                )
            return A - B / (temperature_K + C)

        if self.evaluator_family == "tabulated_equilibrium":
            return _interpolate_log_pressure(self.points, temperature_K)

        raise CatalogCompileError(
            f"unsupported reference pressure evaluator {self.evaluator_family!r}"
        )


@dataclass(frozen=True)
class CompiledPressureEvaluator:
    """One immutable pressure path shared by evaporation and condensation."""

    species_id: str
    evaluator_family: str
    pressure_observable: PressureObservable
    species_basis: str
    valid_temperature_K: tuple[float, float]
    validation_status: ValidationStatus
    reference_model: _ReferencePressureModel
    extrapolation_policy: str
    out_of_range_status: str
    acquisition_flag: str
    activity_exponent: float = 0.0
    pO2_exponent: float = 0.0
    pO2_reference_bar: float = 1.0

    def evaluate(
        self,
        temperature_K: float,
        *,
        source_activity: float = 1.0,
        pO2_bar: float | None = None,
    ) -> PressureEvaluation:
        temperature_K = _finite_positive(temperature_K, "temperature_K")
        low, high = self.valid_temperature_K
        out_of_range = temperature_K < low or temperature_K > high

        if out_of_range:
            if self.extrapolation_policy != DEFAULT_EXTRAPOLATION_POLICY:
                raise CatalogCompileError(
                    f"{self.species_id}: unsupported extrapolation policy "
                    f"{self.extrapolation_policy!r}"
                )
            log10_reference = self._conservative_log10_continuation(temperature_K)
        else:
            log10_reference = self.reference_model.log10_pressure(temperature_K)

        log10_pressure = log10_reference
        if self.activity_exponent:
            activity = _finite_positive(source_activity, "source_activity")
            log10_pressure += self.activity_exponent * math.log10(activity)
        if self.pO2_exponent:
            oxygen = _finite_positive(
                self.pO2_reference_bar if pO2_bar is None else pO2_bar,
                "pO2_bar",
            )
            log10_pressure += self.pO2_exponent * math.log10(
                oxygen / self.pO2_reference_bar
            )

        pressure_pa = 10.0**log10_pressure
        if not math.isfinite(pressure_pa) or pressure_pa <= 0.0:
            raise CatalogCompileError(
                f"{self.species_id}: evaluator produced invalid pressure"
            )
        return PressureEvaluation(
            pressure_pa=pressure_pa,
            pressure_observable=self.pressure_observable,
            validation_status=self.validation_status,
            out_of_range=out_of_range,
            status=self.out_of_range_status if out_of_range else None,
            acquisition_flag=self.acquisition_flag if out_of_range else None,
        )

    def _conservative_log10_continuation(self, temperature_K: float) -> float:
        low, high = self.valid_temperature_K
        boundary = low if temperature_K < low else high
        span = max(high - low, 1.0)
        step = min(max(span * 1.0e-4, 1.0e-3), 1.0)
        inside = boundary + step if boundary == low else boundary - step
        boundary_log = self.reference_model.log10_pressure(boundary)
        inside_log = self.reference_model.log10_pressure(inside)
        slope = (boundary_log - inside_log) / (boundary - inside)
        straight_delta = slope * (temperature_K - boundary)
        # Conservative means never more volatile than straight continuation.
        # Retain a non-zero slope: attenuate outward increases and strengthen
        # outward decreases instead of introducing a flat/zero cliff.
        factor = 0.5 if straight_delta >= 0.0 else 1.5
        return boundary_log + factor * straight_delta


@dataclass(frozen=True)
class CompiledFiatRouting:
    process_or_terminal_destination: str
    engineering_capture_policy: str
    plant_bin: str | None
    products_and_coproducts: tuple[Any, ...]
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class CompiledVaporisationCoefficients:
    extrapolation_policy: str
    out_of_range_status: str
    acquisition_flag: str
    evaporation_alpha: Mapping[str, Any]
    alpha_domain_and_uncertainty: Mapping[str, Any]


@dataclass(frozen=True)
class CompiledCodeMetadata:
    formula_id: str
    source_account: str
    request_rule: str
    solve_group_id: str
    canonical_aliases: tuple[str, ...]
    compatibility_projection: str
    hot_train_applicability: str
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class CompiledSpecies:
    species_id: str
    family_id: str
    formula: str
    validation_status: ValidationStatus
    evaluator: CompiledPressureEvaluator | None
    pressure_observable: PressureObservable
    valid_temperature_K: tuple[float, float]
    fiat_routing: CompiledFiatRouting
    vaporisation_coefficients: CompiledVaporisationCoefficients
    code_metadata: CompiledCodeMetadata


class VapourRailCatalog:
    """Compiled schema-v2 catalog plus its temporary legacy projection."""

    def __init__(
        self,
        *,
        species: Mapping[str, CompiledSpecies],
        legacy_projection: Mapping[str, Any],
    ) -> None:
        self._species = MappingProxyType(dict(species))
        self._legacy_projection = deepcopy(dict(legacy_projection))

    @property
    def species(self) -> Mapping[str, CompiledSpecies]:
        return self._species

    def evaluator_for(self, species_id: str) -> CompiledPressureEvaluator:
        try:
            evaluator = self._species[species_id].evaluator
        except KeyError as exc:
            raise CatalogCompileError(f"unknown vapour species {species_id!r}") from exc
        if evaluator is None:
            raise CatalogCompileError(
                f"{species_id}: pressure evaluator unavailable pending acquisition"
            )
        return evaluator

    def evaluator_for_evaporation(self, species_id: str) -> CompiledPressureEvaluator:
        return self.evaluator_for(species_id)

    def evaluator_for_condensation(self, species_id: str) -> CompiledPressureEvaluator:
        return self.evaluator_for(species_id)

    def legacy_view(self) -> dict[str, Any]:
        return deepcopy(self._legacy_projection)


class VaporPressureCompatibilityView(dict[str, Any]):
    """Legacy mapping facade retaining its authoritative schema-v2 payload."""

    def __init__(
        self,
        legacy_projection: Mapping[str, Any],
        catalog_payload: Mapping[str, Any],
    ) -> None:
        super().__init__(deepcopy(dict(legacy_projection)))
        self.catalog_payload = deepcopy(dict(catalog_payload))


def compile_vapour_rail_catalog(payload: Mapping[str, Any]) -> VapourRailCatalog:
    """Validate and compile one schema-v2 YAML payload.

    Schema-v2 payloads are memoized by object identity (see module cache). Treat
    payloads as immutable after the first compile; call
    ``clear_vapor_pressure_view_caches`` if a test mutates a cached payload.
    """

    if not isinstance(payload, Mapping):
        raise CatalogCompileError("vapour catalog root must be a mapping")
    cached = _cache_get(_catalog_compile_cache, payload)
    if cached is not None:
        return cached
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise CatalogCompileError("vapour catalog schema_version must be 2")
    families = _mapping(payload.get("families"), "families")
    if not families:
        raise CatalogCompileError("vapour catalog must declare at least one family")

    compiled: dict[str, CompiledSpecies] = {}
    legacy: dict[str, Any] = {}
    condensation_reference: dict[str, Any] = {}
    for family_id, family_value in families.items():
        family = _mapping(family_value, f"families.{family_id}")
        if set(family) != set(FOUR_STRATA):
            missing = sorted(set(FOUR_STRATA) - set(family))
            extra = sorted(set(family) - set(FOUR_STRATA))
            raise CatalogCompileError(
                f"{family_id}: family must contain exactly the four strata; "
                f"missing={missing}, extra={extra}"
            )
        physical = _mapping(
            family["physical_properties"],
            f"{family_id}.physical_properties",
        )
        species_rows = _mapping(
            physical.get("species"),
            f"{family_id}.physical_properties.species",
        )
        routing = _mapping(family["fiat_routing"], f"{family_id}.fiat_routing")
        kinetics = _mapping(
            family["vaporisation_coefficients"],
            f"{family_id}.vaporisation_coefficients",
        )
        code = _mapping(family["code_metadata"], f"{family_id}.code_metadata")
        compiled_routing = _compile_fiat_routing(family_id, routing)
        compiled_kinetics = _compile_vaporisation_coefficients(family_id, kinetics)
        compiled_code = _compile_code_metadata(family_id, code)
        compatibility_group = compiled_code.compatibility_projection
        legacy_group = legacy.setdefault(compatibility_group, {})
        if not isinstance(legacy_group, dict):
            raise CatalogCompileError(
                f"{family_id}: compatibility projection collides with non-family data"
            )

        for species_id, row_value in species_rows.items():
            row = _mapping(row_value, f"{family_id}.{species_id}")
            if species_id in compiled:
                raise CatalogCompileError(f"duplicate vapour species {species_id!r}")
            formula = _required_string(row.get("formula"), f"{species_id}.formula")
            if compiled_code.formula_id != species_id:
                raise CatalogCompileError(
                    f"{family_id}.code_metadata.formula_id must match {species_id!r}"
                )
            validation = _mapping(row.get("validation"), f"{species_id}.validation")
            status = _validation_status(validation, species_id)
            pressure_models = row.get("pressure_models")
            if not isinstance(pressure_models, Sequence) or isinstance(
                pressure_models, (str, bytes)
            ):
                raise CatalogCompileError(
                    f"{species_id}.pressure_models must be a non-empty list"
                )
            if len(pressure_models) != 1:
                raise CatalogCompileError(
                    f"{species_id}: VR-3 compiler requires one selected pressure model"
                )
            model = _mapping(pressure_models[0], f"{species_id}.pressure_models[0]")
            observable, valid_temperature_K = _model_surface(species_id, model)
            evaluator = None
            if model.get("availability") != "unavailable_pending_acquisition":
                evaluator = _compile_evaluator(
                    family_id=family_id,
                    species_id=str(species_id),
                    row=row,
                    model=model,
                    validation_status=status,
                    kinetics=kinetics,
                )
            compiled[str(species_id)] = CompiledSpecies(
                species_id=str(species_id),
                family_id=str(family_id),
                formula=formula,
                validation_status=status,
                evaluator=evaluator,
                pressure_observable=observable,
                valid_temperature_K=valid_temperature_K,
                fiat_routing=compiled_routing,
                vaporisation_coefficients=compiled_kinetics,
                code_metadata=compiled_code,
            )
            legacy_group[str(species_id)] = _legacy_species_row(
                species_id=str(species_id),
                row=row,
                model=model,
                routing=routing,
                kinetics=kinetics,
                code=code,
            )
            reference = routing.get("condensation_reference_at_1mbar_C")
            if reference is not None and code.get(
                "compatibility_condensation_reference_table"
            ):
                condensation_reference[str(species_id)] = deepcopy(reference)

    if condensation_reference:
        legacy["condensation_reference_at_1mbar"] = {
            species_id: condensation_reference[species_id]
            for species_id in _LEGACY_CONDENSATION_REFERENCE_ORDER
            if species_id in condensation_reference
        }
    catalog = VapourRailCatalog(species=compiled, legacy_projection=legacy)
    _cache_put(_catalog_compile_cache, payload, catalog)
    return catalog


def vapor_pressure_legacy_view(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the schema-v1 compatibility view without duplicating YAML authority.

    For schema-v2 payloads the compiled legacy projection is memoized by payload
    identity so hot-path callers (condensation deposition flux) never recompile
    or re-deepcopy the full catalog per species lookup. The cached dict is
    shared across callers for a given payload object — treat it as read-only.
    """

    if not isinstance(payload, Mapping):
        return {}
    if payload.get("schema_version") == SCHEMA_VERSION and "families" in payload:
        cached = _cache_get(_legacy_view_cache, payload)
        if cached is not None:
            return cached
        view = compile_vapour_rail_catalog(payload).legacy_view()
        _cache_put(_legacy_view_cache, payload, view)
        return view
    return deepcopy(dict(payload))


def vapor_pressure_compatibility_view(
    payload: Mapping[str, Any],
) -> VaporPressureCompatibilityView:
    catalog = compile_vapour_rail_catalog(payload)
    return VaporPressureCompatibilityView(catalog.legacy_view(), payload)


def validate_species_catalog(payload: Mapping[str, Any]) -> None:
    """Enforce collision-gas closure and carrier-only non-flux semantics."""

    rows = payload.get("species") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        raise CatalogCompileError("species catalog must contain a species list")
    for index, value in enumerate(rows):
        row = _mapping(value, f"species[{index}]")
        species_id = _required_string(row.get("id"), f"species[{index}].id")
        if row.get("catalog_role") == "carrier_only":
            if row.get("formula") is not None:
                raise CatalogCompileError(
                    f"{species_id}: carrier_only rows must retain null formula"
                )
            if row.get("pressure_models"):
                raise CatalogCompileError(
                    f"{species_id}: carrier_only rows cannot declare pressure models"
                )
            if row.get("direct_vapour_flux") is not False:
                raise CatalogCompileError(
                    f"{species_id}: carrier_only rows must disable direct vapour flux"
                )
        if species_id.endswith("_gas"):
            _required_string(row.get("formula"), f"{species_id}.formula")
            _mapping(row.get("atoms"), f"{species_id}.atoms")
            if row.get("phase") != "gas" or row.get("catalog_role") != "gas":
                raise CatalogCompileError(
                    f"{species_id}: collision rows must be catalog gas rows"
                )
            _validation_status(
                _mapping(row.get("validation"), f"{species_id}.validation"),
                species_id,
            )
            PressureObservable(
                _required_string(
                    row.get("pressure_observable"),
                    f"{species_id}.pressure_observable",
                )
            )
            domain = _mapping(
                row.get("valid_domain"), f"{species_id}.valid_domain"
            )
            if "temperature_K" not in domain:
                raise CatalogCompileError(
                    f"{species_id}: valid_domain requires temperature_K"
                )
            if row.get("extrapolation_policy") != DEFAULT_EXTRAPOLATION_POLICY:
                raise CatalogCompileError(
                    f"{species_id}: collision gas requires conservative continuation"
                )
            if row.get("out_of_range_status") != OUT_OF_RANGE_STATUS:
                raise CatalogCompileError(
                    f"{species_id}: collision gas requires typed out-of-range status"
                )
            _required_string(
                row.get("acquisition_flag"), f"{species_id}.acquisition_flag"
            )
            code = _mapping(
                row.get("code_metadata"), f"{species_id}.code_metadata"
            )
            _required_string(
                code.get("formula_id"), f"{species_id}.code_metadata.formula_id"
            )
            aliases = code.get("canonical_aliases")
            if not isinstance(aliases, list):
                raise CatalogCompileError(
                    f"{species_id}.code_metadata.canonical_aliases must be a list"
                )


def _compile_evaluator(
    *,
    family_id: str,
    species_id: str,
    row: Mapping[str, Any],
    model: Mapping[str, Any],
    validation_status: ValidationStatus,
    kinetics: Mapping[str, Any],
) -> CompiledPressureEvaluator:
    evaluator_family = _required_string(
        model.get("evaluator_family"), f"{species_id}.evaluator_family"
    )
    observable, (low, high) = _model_surface(species_id, model)
    species_basis = _required_string(
        model.get("species_basis"), f"{species_id}.species_basis"
    )
    activity_exponent = float(model.get("activity_exponent", 0.0) or 0.0)
    pO2_exponent = float(model.get("pO2_exponent", 0.0) or 0.0)
    pO2_reference = _finite_positive(
        model.get("pO2_reference_bar", 1.0),
        f"{species_id}.pO2_reference_bar",
    )
    if evaluator_family == "standard_reaction_term":
        reaction_id = _required_string(
            model.get("source_reaction_id"), f"{species_id}.source_reaction_id"
        )
        reactions = row.get("source_reactions")
        if not isinstance(reactions, list):
            raise CatalogCompileError(
                f"{species_id}: standard_reaction_term requires source_reactions"
            )
        matched = [
            _mapping(item, f"{species_id}.source_reactions")
            for item in reactions
            if isinstance(item, Mapping) and item.get("id") == reaction_id
        ]
        if len(matched) != 1:
            raise CatalogCompileError(
                f"{species_id}: source reaction {reaction_id!r} must resolve once"
            )
        _validate_balanced_reaction(species_id, matched[0])
        reference = _mapping(
            model.get("reference_pressure_model"),
            f"{species_id}.reference_pressure_model",
        )
    elif evaluator_family in {"antoine", "tabulated_equilibrium"}:
        reference = model
    else:
        raise CatalogCompileError(
            f"{species_id}: evaluator family {evaluator_family!r} belongs to a later chunk"
        )

    reference_model = _compile_reference_model(species_id, reference)
    return CompiledPressureEvaluator(
        species_id=species_id,
        evaluator_family=evaluator_family,
        pressure_observable=observable,
        species_basis=species_basis,
        valid_temperature_K=(low, high),
        validation_status=validation_status,
        reference_model=reference_model,
        extrapolation_policy=str(kinetics["extrapolation_policy"]),
        out_of_range_status=str(kinetics["out_of_range_status"]),
        acquisition_flag=_required_string(
            kinetics.get("acquisition_flag"), f"{family_id}.acquisition_flag"
        ),
        activity_exponent=activity_exponent,
        pO2_exponent=pO2_exponent,
        pO2_reference_bar=pO2_reference,
    )


def _compile_reference_model(
    species_id: str, model: Mapping[str, Any]
) -> _ReferencePressureModel:
    family = _required_string(
        model.get("evaluator_family"), f"{species_id}.reference.evaluator_family"
    )
    coefficients: Mapping[str, Any] = {}
    points: tuple[tuple[float, float], ...] = ()
    if family == "antoine":
        coefficients = _mapping(
            model.get("coefficients"), f"{species_id}.reference.coefficients"
        )
        for key in ("A", "B", "C"):
            try:
                float(coefficients[key])
            except (KeyError, TypeError, ValueError) as exc:
                raise CatalogCompileError(
                    f"{species_id}: Antoine reference requires numeric {key}"
                ) from exc
    elif family == "tabulated_equilibrium":
        raw_points = model.get("points")
        if not isinstance(raw_points, list) or len(raw_points) < 2:
            raise CatalogCompileError(
                f"{species_id}: tabulated reference requires at least two points"
            )
        parsed: list[tuple[float, float]] = []
        for index, value in enumerate(raw_points):
            point = _mapping(value, f"{species_id}.points[{index}]")
            parsed.append(
                (
                    _finite_positive(point.get("temperature_K"), "temperature_K"),
                    _finite_positive(point.get("pressure_Pa"), "pressure_Pa"),
                )
            )
        points = tuple(sorted(parsed))
        if len({item[0] for item in points}) != len(points):
            raise CatalogCompileError(f"{species_id}: duplicate table temperature")
    else:
        raise CatalogCompileError(
            f"{species_id}: unsupported reference evaluator {family!r}"
        )
    return _ReferencePressureModel(
        evaluator_family=family,
        coefficients=MappingProxyType(deepcopy(dict(coefficients))),
        points=points,
    )


def _validate_balanced_reaction(species_id: str, reaction: Mapping[str, Any]) -> None:
    reactants = reaction.get("reactants")
    products = reaction.get("products")
    if not isinstance(reactants, list) or not reactants:
        raise CatalogCompileError(f"{species_id}: reaction requires reactants")
    if not isinstance(products, list) or not products:
        raise CatalogCompileError(f"{species_id}: reaction requires products")
    balance: dict[str, float] = {}
    for sign, participants in ((-1.0, reactants), (1.0, products)):
        for item in participants:
            participant = _mapping(item, f"{species_id}.reaction participant")
            formula = _required_string(
                participant.get("formula"), f"{species_id}.reaction.formula"
            )
            amount = _finite_positive(
                participant.get("stoichiometry"),
                f"{species_id}.reaction.stoichiometry",
            )
            for element, count in _formula_atoms(formula).items():
                balance[element] = balance.get(element, 0.0) + sign * amount * count
    unbalanced = {key: value for key, value in balance.items() if abs(value) > 1.0e-9}
    if unbalanced:
        raise CatalogCompileError(
            f"{species_id}: source reaction is not atom balanced: {unbalanced}"
        )


def _formula_atoms(formula: str) -> dict[str, float]:
    cleaned = re.sub(r"\([^)]*\)$", "", formula.strip())
    matches = list(re.finditer(r"([A-Z][a-z]?)(\d+(?:\.\d+)?|\.\d+)?", cleaned))
    if not matches or "".join(match.group(0) for match in matches) != cleaned:
        raise CatalogCompileError(f"unsupported reaction formula {formula!r}")
    atoms: dict[str, float] = {}
    for match in matches:
        atoms[match.group(1)] = atoms.get(match.group(1), 0.0) + float(
            match.group(2) or 1.0
        )
    return atoms


def _legacy_species_row(
    *,
    species_id: str,
    row: Mapping[str, Any],
    model: Mapping[str, Any],
    routing: Mapping[str, Any],
    kinetics: Mapping[str, Any],
    code: Mapping[str, Any],
) -> dict[str, Any]:
    result = deepcopy(dict(row))
    result.pop("pressure_models", None)
    result.pop("source_reactions", None)
    result.pop("validation", None)
    result["fit_target"] = model.get("fit_target", model.get("evaluator_family"))
    domain = _mapping(model.get("valid_domain"), "valid_domain")
    if not model.get("compatibility_omit_valid_range_K"):
        result["valid_range_K"] = deepcopy(domain["temperature_K"])
    evaluator_family = model.get("evaluator_family")
    reference = (
        _mapping(model.get("reference_pressure_model"), "reference_pressure_model")
        if evaluator_family == "standard_reaction_term"
        else model
    )
    if (
        reference.get("evaluator_family") == "antoine"
        and not model.get("compatibility_omit_antoine")
    ):
        result["antoine"] = deepcopy(dict(_mapping(reference.get("coefficients"), "coefficients")))
    elif (
        model.get("availability") != "unavailable_pending_acquisition"
        and not model.get("compatibility_omit_antoine")
    ):
        result["reference_pressure_model"] = deepcopy(dict(reference))
    alpha = kinetics.get("evaporation_alpha")
    if isinstance(alpha, Mapping) and alpha.get("status") == "no_data":
        legacy_policy = alpha.get("compatibility_policy_field")
        if legacy_policy is not None:
            result["evaporation_alpha_policy"] = deepcopy(legacy_policy)
    elif alpha is not None:
        result["evaporation_alpha"] = deepcopy(alpha)
    if "condensation_temperature_C" in routing:
        result["condensation_T_C"] = deepcopy(routing["condensation_temperature_C"])
    if (
        "condensation_reference_at_1mbar_C" in routing
        and not code.get("compatibility_condensation_reference_top_level_only")
    ):
        result["condensation_T_C_at_1mbar"] = deepcopy(
            routing["condensation_reference_at_1mbar_C"]
        )
    compatibility_fields = routing.get("compatibility_fields", {})
    if isinstance(compatibility_fields, Mapping):
        result.update(deepcopy(dict(compatibility_fields)))
    legacy_extrapolation = model.get("legacy_extrapolation_policy")
    if legacy_extrapolation is not None:
        result["extrapolation_policy"] = deepcopy(legacy_extrapolation)
    field_order = _LEGACY_FIELD_ORDER.get(species_id, ())
    return {
        key: result[key]
        for key in (*field_order, *result)
        if key in result
    }


def _model_surface(
    species_id: str, model: Mapping[str, Any]
) -> tuple[PressureObservable, tuple[float, float]]:
    try:
        observable = PressureObservable(
            _required_string(
                model.get("pressure_kind"), f"{species_id}.pressure_kind"
            )
        )
    except ValueError as exc:
        raise CatalogCompileError(
            f"{species_id}: unknown pressure observable {model.get('pressure_kind')!r}"
        ) from exc
    domain = _mapping(model.get("valid_domain"), f"{species_id}.valid_domain")
    bounds = domain.get("temperature_K")
    if (
        not isinstance(bounds, Sequence)
        or isinstance(bounds, (str, bytes))
        or len(bounds) != 2
    ):
        raise CatalogCompileError(
            f"{species_id}.valid_domain.temperature_K must be [low, high]"
        )
    low = _finite_positive(bounds[0], f"{species_id}.valid_domain.temperature_K[0]")
    high = _finite_positive(bounds[1], f"{species_id}.valid_domain.temperature_K[1]")
    if low >= high:
        raise CatalogCompileError(f"{species_id}: invalid temperature domain")
    return observable, (low, high)


def _compile_fiat_routing(
    family_id: str, routing: Mapping[str, Any]
) -> CompiledFiatRouting:
    destination = _required_string(
        routing.get("process_or_terminal_destination"),
        f"{family_id}.fiat_routing.process_or_terminal_destination",
    )
    capture_policy = _required_string(
        routing.get("engineering_capture_policy"),
        f"{family_id}.fiat_routing.engineering_capture_policy",
    )
    plant_bin_value = routing.get("plant_bin")
    if plant_bin_value is not None and not isinstance(plant_bin_value, str):
        raise CatalogCompileError(
            f"{family_id}.fiat_routing.plant_bin must be a string or null"
        )
    products = routing.get("products_and_coproducts")
    if not isinstance(products, list):
        raise CatalogCompileError(
            f"{family_id}.fiat_routing.products_and_coproducts must be a list"
        )
    return CompiledFiatRouting(
        process_or_terminal_destination=destination,
        engineering_capture_policy=capture_policy,
        plant_bin=plant_bin_value,
        products_and_coproducts=tuple(deepcopy(products)),
        raw=MappingProxyType(deepcopy(dict(routing))),
    )


def _compile_vaporisation_coefficients(
    family_id: str, kinetics: Mapping[str, Any]
) -> CompiledVaporisationCoefficients:
    _validate_kinetics(family_id, kinetics)
    evaporation_alpha = _mapping(
        kinetics.get("evaporation_alpha"),
        f"{family_id}.vaporisation_coefficients.evaporation_alpha",
    )
    alpha_domain = _mapping(
        kinetics.get("alpha_domain_and_uncertainty"),
        f"{family_id}.vaporisation_coefficients.alpha_domain_and_uncertainty",
    )
    return CompiledVaporisationCoefficients(
        extrapolation_policy=DEFAULT_EXTRAPOLATION_POLICY,
        out_of_range_status=OUT_OF_RANGE_STATUS,
        acquisition_flag=_required_string(
            kinetics.get("acquisition_flag"),
            f"{family_id}.vaporisation_coefficients.acquisition_flag",
        ),
        evaporation_alpha=MappingProxyType(deepcopy(dict(evaporation_alpha))),
        alpha_domain_and_uncertainty=MappingProxyType(
            deepcopy(dict(alpha_domain))
        ),
    )


def _compile_code_metadata(
    family_id: str, code: Mapping[str, Any]
) -> CompiledCodeMetadata:
    aliases = code.get("canonical_aliases")
    if not isinstance(aliases, list) or not all(
        isinstance(alias, str) and alias.strip() for alias in aliases
    ):
        raise CatalogCompileError(
            f"{family_id}.code_metadata.canonical_aliases must be a string list"
        )
    return CompiledCodeMetadata(
        formula_id=_required_string(
            code.get("formula_id"), f"{family_id}.code_metadata.formula_id"
        ),
        source_account=_required_string(
            code.get("source_account"), f"{family_id}.code_metadata.source_account"
        ),
        request_rule=_required_string(
            code.get("request_rule"), f"{family_id}.code_metadata.request_rule"
        ),
        solve_group_id=_required_string(
            code.get("solve_group_id"), f"{family_id}.code_metadata.solve_group_id"
        ),
        canonical_aliases=tuple(alias.strip() for alias in aliases),
        compatibility_projection=_required_string(
            code.get("compatibility_projection"),
            f"{family_id}.code_metadata.compatibility_projection",
        ),
        hot_train_applicability=_required_string(
            code.get("hot_train_applicability"),
            f"{family_id}.code_metadata.hot_train_applicability",
        ),
        raw=MappingProxyType(deepcopy(dict(code))),
    )


def _validate_kinetics(family_id: str, kinetics: Mapping[str, Any]) -> None:
    if kinetics.get("extrapolation_policy") != DEFAULT_EXTRAPOLATION_POLICY:
        raise CatalogCompileError(
            f"{family_id}: extrapolation_policy must be {DEFAULT_EXTRAPOLATION_POLICY}"
        )
    if kinetics.get("out_of_range_status") != OUT_OF_RANGE_STATUS:
        raise CatalogCompileError(
            f"{family_id}: out_of_range_status must be {OUT_OF_RANGE_STATUS}"
        )
    _required_string(kinetics.get("acquisition_flag"), f"{family_id}.acquisition_flag")


def _validation_status(
    validation: Mapping[str, Any], species_id: str
) -> ValidationStatus:
    try:
        status = ValidationStatus(validation.get("status"))
    except ValueError as exc:
        raise CatalogCompileError(
            f"{species_id}: validation.status must be pending_validation or validated"
        ) from exc
    anchors = validation.get("anchor_refs")
    if not isinstance(anchors, list):
        raise CatalogCompileError(f"{species_id}: validation.anchor_refs must be a list")
    if status is ValidationStatus.VALIDATED and not anchors:
        raise CatalogCompileError(
            f"{species_id}: validated rows require at least one anchor reference"
        )
    return status


def _interpolate_log_pressure(
    points: tuple[tuple[float, float], ...], temperature_K: float
) -> float:
    if temperature_K <= points[0][0]:
        left, right = points[0], points[1]
    elif temperature_K >= points[-1][0]:
        left, right = points[-2], points[-1]
    else:
        for left, right in zip(points, points[1:]):
            if left[0] <= temperature_K <= right[0]:
                break
    fraction = (temperature_K - left[0]) / (right[0] - left[0])
    return math.log10(left[1]) + fraction * (
        math.log10(right[1]) - math.log10(left[1])
    )


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CatalogCompileError(f"{field_name} must be a mapping")
    return value


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CatalogCompileError(f"{field_name} must be a non-empty string")
    return value.strip()


def _finite_positive(value: Any, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise CatalogCompileError(f"{field_name} must be numeric") from exc
    if not math.isfinite(result) or result <= 0.0:
        raise CatalogCompileError(f"{field_name} must be finite and positive")
    return result
