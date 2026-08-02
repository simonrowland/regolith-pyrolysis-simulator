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

from simulator.vapour_rail.activity import (
    ActivityInputDeclaration,
    ActivityVerdictKind,
    BoundDirection,
    SourceReactionActivity,
)
from simulator.vapour_rail.batch import FluxActivationContext
from simulator.vapour_rail.nasa_cea import (
    Nasa7Segment,
    Nasa9Segment,
    NasaCeaPolynomial,
    reaction_equilibrium_constant,
)
from simulator.vapour_rail.shomate import (
    ShomatePolynomial,
    ShomateSegment,
    coefficients_from_mapping,
)


SCHEMA_VERSION = 2
FOUR_STRATA = (
    "physical_properties",
    "fiat_routing",
    "vaporisation_coefficients",
    "code_metadata",
)
DEFAULT_EXTRAPOLATION_POLICY = "conservative_slope_continuation"
OUT_OF_RANGE_STATUS = "out_of_range_conservative_continuation"
# Runtime thermo evaluator families (VR-4b). Short aliases accepted in YAML.
_THERMO_FAMILY_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "nasa7": "nasa_cea_7",
        "nasa9": "nasa_cea_9",
        "nasa_cea_7": "nasa_cea_7",
        "nasa_cea_9": "nasa_cea_9",
        "shomate": "shomate",
    }
)
RUNTIME_THERMO_EVALUATOR_FAMILIES = frozenset(
    {"nasa_cea_7", "nasa_cea_9", "shomate"}
)
# CEA / JANAF standard-state pressure P° (Pa).
_THERMO_REFERENCE_PRESSURE_PA = 100_000.0


def _strip_phase_suffix(formula: str) -> str:
    """``M(g)`` / ``O2(cr)`` → ``M`` / ``O2``; bare formulas unchanged."""
    return re.sub(r"\([^)]*\)$", "", str(formula).strip())


def _is_dioxygen_formula(formula: str) -> bool:
    """True for O2 with optional phase suffix (``O2``, ``O2(g)``, …)."""
    return _strip_phase_suffix(formula) == "O2"


def _domain_temperature_bounds(
    domain: Mapping[str, Any], *, field: str
) -> tuple[float, float]:
    """Read ``temperature_K: [lo, hi]`` or CEA-ingest ``T_min_K``/``T_max_K``."""
    bounds = domain.get("temperature_K")
    if (
        isinstance(bounds, Sequence)
        and not isinstance(bounds, (str, bytes))
        and len(bounds) == 2
    ):
        low = _finite_positive(bounds[0], f"{field}.temperature_K[0]")
        high = _finite_positive(bounds[1], f"{field}.temperature_K[1]")
    elif "T_min_K" in domain and "T_max_K" in domain:
        low = _finite_positive(domain.get("T_min_K"), f"{field}.T_min_K")
        high = _finite_positive(domain.get("T_max_K"), f"{field}.T_max_K")
    else:
        raise CatalogCompileError(
            f"{field} must provide temperature_K [low, high] or T_min_K/T_max_K"
        )
    if low >= high:
        raise CatalogCompileError(f"{field}: invalid temperature domain")
    return low, high


def _require_poly_covers_domain(
    *,
    species_id: str,
    label: str,
    poly: Any,
    domain_low: float,
    domain_high: float,
) -> None:
    """Compile-time: model domain must sit inside every polynomial segment cover."""
    t_min = float(getattr(poly, "T_min_K"))
    t_max = float(getattr(poly, "T_max_K"))
    if t_min > domain_low + 1.0e-12 or t_max < domain_high - 1.0e-12:
        raise CatalogCompileError(
            f"{species_id}: thermo polynomial {label!r} covers "
            f"[{t_min}, {t_max}] K but model valid_domain is "
            f"[{domain_low}, {domain_high}] K — polynomial must cover the "
            "full declared model domain"
        )
# Two distinct memo layers — do not collapse into one (different consumers):
# 1. _COMPILE_CACHE (VR-6 / NV-1): multi-key strong-ref compile memo keyed by
#    (id(payload), emit_u0_request_rules, id(u0_manifest)|0) → (payload pin,
#    catalog). Capability probes and full request-rule compiles share this.
#    Strong-ref pin + ``cached_payload is payload`` prevents stale-catalog on
#    id recycle (NV-1 regression must stay red under reversion).
# 2. _legacy_view_cache (VR-7 / P1-1): payload-identity memo of the schema-v1
#    projection dict. ``legacy_view()`` deepcopies every call; condensation
#    hits vapor_pressure_legacy_view per species, so the projection must be
#    cached separately. Compile memo alone does not serve that consumer
#    (deepcopy cost + dict-identity stability for the warm A/B budget).
_COMPILE_CACHE_MAX = 8
# value: (payload strong-ref pin, compiled catalog)
_COMPILE_CACHE: dict[tuple[int, bool, int], tuple[Mapping[str, Any], "VapourRailCatalog"]] = {}
_COMPILE_CACHE_ORDER: list[tuple[int, bool, int]] = []
_legacy_view_cache: OrderedDict[int, tuple[Any, dict[str, Any]]] = OrderedDict()
_CATALOG_COMPILE_CACHE_MAX = _COMPILE_CACHE_MAX  # shared LRU bound for legacy helpers


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
    """Drop compile + legacy-view memos (tests that mutate payloads in place)."""

    clear_vapour_rail_compile_cache()
    _legacy_view_cache.clear()


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
    # Thermo-backed pressure (VR-4b nasa7/nasa9/shomate). Optional callables
    # avoid freezing large polynomial trees into every Antoine row.
    thermo_log10_pressure: Any | None = None

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

        if self.evaluator_family in RUNTIME_THERMO_EVALUATOR_FAMILIES:
            if self.thermo_log10_pressure is None:
                raise CatalogCompileError(
                    f"thermo family {self.evaluator_family!r} missing compiled "
                    "pressure dispatch"
                )
            return float(self.thermo_log10_pressure(temperature_K))

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
    validation_anchor_refs: tuple[str, ...] = ()


class VapourRailCatalog:
    """Compiled schema-v2 catalog plus its temporary legacy projection."""

    def __init__(
        self,
        *,
        species: Mapping[str, CompiledSpecies],
        legacy_projection: Mapping[str, Any],
        request_rules: tuple[Any, ...] = (),
        catalog_payload: Mapping[str, Any] | None = None,
    ) -> None:
        self._species = MappingProxyType(dict(species))
        self._legacy_projection = deepcopy(dict(legacy_projection))
        self._request_rules = tuple(request_rules)
        self._catalog_payload = (
            deepcopy(dict(catalog_payload)) if catalog_payload is not None else None
        )

    @property
    def species(self) -> Mapping[str, CompiledSpecies]:
        return self._species

    @property
    def request_rules(self) -> tuple[Any, ...]:
        """Compiler-emitted request rules (U0 V + eligible C edges + catalog)."""

        return self._request_rules

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

    def resolve_source_reaction_activity(self, *args: Any, **kwargs: Any):
        """Delegate to :class:`CondensedPhaseActivityProvider` (DESIGN-REV5 §9.1).

        Kept on the catalog so runtime callers have one named seam. Diagnostic
        only: does not write the catalog, ledger, or promote authority.
        """

        from simulator.vapour_rail.activity import CondensedPhaseActivityProvider

        provider = kwargs.pop("provider", None) or CondensedPhaseActivityProvider(
            kwargs.pop("phase_endmember_map", None)
        )
        return provider.resolve_source_reaction_activity(*args, **kwargs)

    def validation_may_certify(self, species_id: str) -> bool:
        """Pending-validation rows never certify (progressive ladder ceiling)."""

        from simulator.vapour_rail.activity import validation_row_may_certify

        try:
            status = self._species[species_id].validation_status.value
        except KeyError as exc:
            raise CatalogCompileError(f"unknown vapour species {species_id!r}") from exc
        return validation_row_may_certify(validation_status=status)

    def build_request(
        self,
        ledger_snapshot: Mapping[str, Any] | Any,
        state: Any | None = None,
        *,
        caller_species_filter: Sequence[str] | None = None,
    ) -> frozenset[str]:
        """Manifest + inventory request set only (DESIGN-REV5 §1.2).

        ``state`` is accepted for API symmetry with resolve_batch but is not
        an input to the request projection.
        """

        del state  # request keys derive only from manifest + ledger inventory
        from simulator.vapour_rail.request import build_request

        return build_request(
            self._request_rules,
            ledger_snapshot,
            caller_species_filter=caller_species_filter,
        )

    def resolve_batch(
        self,
        ledger_snapshot: Mapping[str, Any] | Any,
        state: Any | None = None,
        *,
        provider_candidates_by_species: Mapping[str, Sequence[Any]] | None = None,
        caller_species_filter: Sequence[str] | None = None,
        flux_activation_context: FluxActivationContext,
    ) -> Any:
        """Request → refusal closure → solve bundles → exact-key VapourBatch."""

        from simulator.vapour_rail.request import (
            VapourResolveState,
            resolve_vapour_batch,
        )

        resolve_state: VapourResolveState | None
        if state is None:
            resolve_state = None
        elif isinstance(state, VapourResolveState):
            resolve_state = state
        elif isinstance(state, Mapping):
            resolve_state = VapourResolveState(
                temperature_K=state.get("temperature_K"),
                process_phase=state.get("process_phase"),
                stage=state.get("stage"),
                total_pressure_Pa=state.get("total_pressure_Pa"),
                fO2_bar=state.get("fO2_bar"),
                selected_runtime_pressures_Pa=state.get(
                    "selected_runtime_pressures_Pa"
                ),
                extras={
                    key: value
                    for key, value in state.items()
                    if key
                    not in {
                        "temperature_K",
                        "process_phase",
                        "stage",
                        "total_pressure_Pa",
                        "fO2_bar",
                        "selected_runtime_pressures_Pa",
                    }
                },
            )
        else:
            resolve_state = VapourResolveState(
                temperature_K=getattr(state, "temperature_K", None),
                process_phase=getattr(state, "process_phase", None),
                stage=getattr(state, "stage", None),
                selected_runtime_pressures_Pa=getattr(
                    state, "selected_runtime_pressures_Pa", None
                ),
            )

        return resolve_vapour_batch(
            rules=self._request_rules,
            ledger_snapshot=ledger_snapshot,
            state=resolve_state,
            provider_candidates_by_species=provider_candidates_by_species,
            catalog_species=self._species,
            caller_species_filter=caller_species_filter,
            flux_activation_context=flux_activation_context,
        )


class VaporPressureCompatibilityView(dict[str, Any]):
    """Legacy mapping facade retaining its authoritative schema-v2 payload."""

    def __init__(
        self,
        legacy_projection: Mapping[str, Any],
        catalog_payload: Mapping[str, Any],
    ) -> None:
        super().__init__(deepcopy(dict(legacy_projection)))
        self.catalog_payload = deepcopy(dict(catalog_payload))


def clear_vapour_rail_compile_cache() -> None:
    """Drop the process-wide catalog compile memo (tests / hot-reload)."""

    _COMPILE_CACHE.clear()
    _COMPILE_CACHE_ORDER.clear()


def compiled_catalog_for(
    payload: Mapping[str, Any],
    *,
    emit_u0_request_rules: bool = False,
    u0_manifest: Mapping[str, Any] | None = None,
) -> "VapourRailCatalog":
    """Return a cached compile of ``payload`` (hot capability-probe entrypoint).

    Defaults ``emit_u0_request_rules=False`` because Antoine / Psat probes only
    need evaluators. Full request-rule emission stays default-on at
    :func:`compile_vapour_rail_catalog` for core / VR-6 surfaces; that path is
    also cached and the U0 manifest parse is process-memoized, so default-on
    remains cheap after first warm.
    """

    return compile_vapour_rail_catalog(
        payload,
        u0_manifest=u0_manifest,
        emit_u0_request_rules=emit_u0_request_rules,
    )


def compile_vapour_rail_catalog(
    payload: Mapping[str, Any],
    *,
    u0_manifest: Mapping[str, Any] | None = None,
    emit_u0_request_rules: bool = True,
) -> VapourRailCatalog:
    """Validate and compile one schema-v2 YAML payload.

    When ``emit_u0_request_rules`` is true (default), the compiler also emits
    one request rule per executable U0 ``V`` row and eligible ``C`` edge so a
    physically eligible manifest species cannot be unrequested by construction.

    Results are memoized by ``id(payload)`` + emit flag (and ``id(u0_manifest)``
    when supplied) so repeated hot-path compiles of the same config object
    reuse the catalog. Cache entries pin the payload by strong reference and
    only hit when ``cached_payload is payload``, so a recycled ``id()`` after
    the original payload is freed cannot serve a stale catalog (NV-1). The U0
    manifest YAML load is separately memoized in ``load_u0_manifest``.
    """

    if not isinstance(payload, Mapping):
        raise CatalogCompileError("vapour catalog root must be a mapping")

    cache_key = (
        id(payload),
        bool(emit_u0_request_rules),
        id(u0_manifest) if u0_manifest is not None else 0,
    )
    cached = _COMPILE_CACHE.get(cache_key)
    if cached is not None:
        cached_payload, cached_catalog = cached
        # Strong-ref identity: id reuse of a different object cannot hit.
        if cached_payload is payload:
            return cached_catalog
        # Stale id collision (should not occur while pin lives); recompile.
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
            raw_anchors = validation.get("anchor_refs") or []
            if not isinstance(raw_anchors, list):
                raise CatalogCompileError(
                    f"{species_id}: validation.anchor_refs must be a list"
                )
            anchor_refs = tuple(str(a) for a in raw_anchors)
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
                validation_anchor_refs=anchor_refs,
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

    request_rules: tuple[Any, ...] = ()
    if emit_u0_request_rules:
        from simulator.vapour_rail.request import (
            VapourRequestConstructionError,
            emit_request_rules,
        )

        try:
            request_rules = emit_request_rules(
                catalog_species=compiled,
                u0_manifest=u0_manifest,
                catalog_payload=payload,
            )
        except VapourRequestConstructionError as exc:
            raise CatalogCompileError(str(exc)) from exc

    result = VapourRailCatalog(
        species=compiled,
        legacy_projection=legacy,
        request_rules=request_rules,
        catalog_payload=payload,
    )
    # Insert / refresh LRU entry (payload pin prevents id-recycle while cached).
    if cache_key in _COMPILE_CACHE:
        try:
            _COMPILE_CACHE_ORDER.remove(cache_key)
        except ValueError:
            pass
    _COMPILE_CACHE[cache_key] = (payload, result)
    _COMPILE_CACHE_ORDER.append(cache_key)
    while len(_COMPILE_CACHE_ORDER) > _COMPILE_CACHE_MAX:
        evicted = _COMPILE_CACHE_ORDER.pop(0)
        _COMPILE_CACHE.pop(evicted, None)
    return result


def vapor_pressure_legacy_view(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the schema-v1 compatibility view without duplicating YAML authority.

    For schema-v2 payloads the compiled legacy projection is memoized by payload
    identity so hot-path callers (condensation deposition flux) never recompile
    or re-deepcopy the full catalog per species lookup. The cached dict is
    shared across callers for a given payload object — treat it as read-only.

    Compile uses the VR-6 hot entrypoint (emit_u0_request_rules=False) so this
    path reuses the strong-ref compile memo without paying for U0 rule emission;
    the separate _legacy_view_cache then pins the projection dict so warm hits
    are pure dict returns (VR-7 P1-1 budget).
    """

    if not isinstance(payload, Mapping):
        return {}
    if payload.get("schema_version") == SCHEMA_VERSION and "families" in payload:
        cached = _cache_get(_legacy_view_cache, payload)
        if cached is not None:
            return cached
        # Evaluator/legacy projection only — skip U0 rule emission on this
        # hot path; request rules are built by core's dedicated compile.
        view = compiled_catalog_for(
            payload, emit_u0_request_rules=False
        ).legacy_view()
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


def _normalize_thermo_family(raw: str, *, field: str) -> str:
    key = raw.strip()
    # Accept ``evaluator: nasa9`` spelling as an alias of evaluator_family.
    if key not in _THERMO_FAMILY_ALIASES and key.lower() in _THERMO_FAMILY_ALIASES:
        key = key.lower()
    if key not in _THERMO_FAMILY_ALIASES:
        raise CatalogCompileError(
            f"{field}: unknown thermo evaluator family {raw!r}; "
            f"expected one of {sorted(set(_THERMO_FAMILY_ALIASES))}"
        )
    return _THERMO_FAMILY_ALIASES[key]


def _resolve_model_evaluator_family(
    model: Mapping[str, Any], species_id: str
) -> str:
    """Read ``evaluator_family`` or short ``evaluator`` key from a model row."""
    raw = model.get("evaluator_family", model.get("evaluator"))
    if raw is None:
        raise CatalogCompileError(
            f"{species_id}: pressure model requires evaluator_family (or evaluator)"
        )
    if not isinstance(raw, str) or not raw.strip():
        raise CatalogCompileError(
            f"{species_id}.evaluator_family must be a non-empty string"
        )
    return raw.strip()


def _compile_evaluator(
    *,
    family_id: str,
    species_id: str,
    row: Mapping[str, Any],
    model: Mapping[str, Any],
    validation_status: ValidationStatus,
    kinetics: Mapping[str, Any],
) -> CompiledPressureEvaluator:
    raw_family = _resolve_model_evaluator_family(model, species_id)
    # Thermo families normalize to canonical names; others pass through.
    if raw_family in _THERMO_FAMILY_ALIASES or raw_family.lower() in _THERMO_FAMILY_ALIASES:
        evaluator_family = _normalize_thermo_family(
            raw_family, field=f"{species_id}.evaluator_family"
        )
    else:
        evaluator_family = raw_family

    observable, (low, high) = _model_surface(species_id, model)
    species_basis = _required_string(
        model.get("species_basis"), f"{species_id}.species_basis"
    )
    activity_exponent = float(model.get("activity_exponent", 0.0) or 0.0)
    # Track whether pO2_exponent was explicitly declared (vs omitted default 0).
    _pO2_raw = model.get("pO2_exponent", None)
    pO2_exponent_declared = _pO2_raw is not None and _pO2_raw != ""
    pO2_exponent = float(_pO2_raw if pO2_exponent_declared else 0.0)
    pO2_reference = _finite_positive(
        model.get("pO2_reference_bar", 1.0),
        f"{species_id}.pO2_reference_bar",
    )
    if evaluator_family in RUNTIME_THERMO_EVALUATOR_FAMILIES:
        reference_model, derived_pO2_exponent = _compile_thermo_reference_model(
            species_id=species_id,
            row=row,
            model=model,
            evaluator_family=evaluator_family,
            pressure_kind=observable.value,
            domain_low=low,
            domain_high=high,
            pO2_exponent=pO2_exponent,
            pO2_exponent_declared=pO2_exponent_declared,
            pO2_reference_bar=pO2_reference,
        )
        # Outer activity correction uses −ν_O2/ν_v. Prefer stoichiometry when
        # the declaration was omitted; when declared, require agreement.
        if abs(derived_pO2_exponent) > 0.0:
            if not pO2_exponent_declared:
                pO2_exponent = derived_pO2_exponent
            elif not math.isclose(
                pO2_exponent, derived_pO2_exponent, rel_tol=0.0, abs_tol=1.0e-9
            ):
                raise CatalogCompileError(
                    f"{species_id}: pO2_exponent {pO2_exponent} disagrees with "
                    f"stoichiometry-derived {derived_pO2_exponent} "
                    f"(−ν_O2/ν_vapor)"
                )
    elif evaluator_family == "standard_reaction_term":
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
        reference_model = _compile_reference_model(species_id, reference)
    elif evaluator_family in {"antoine", "tabulated_equilibrium"}:
        reference_model = _compile_reference_model(species_id, model)
    else:
        raise CatalogCompileError(
            f"{species_id}: evaluator family {evaluator_family!r} belongs to a later chunk"
        )

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


def _compile_thermo_reference_model(
    *,
    species_id: str,
    row: Mapping[str, Any],
    model: Mapping[str, Any],
    evaluator_family: str,
    pressure_kind: str,
    domain_low: float,
    domain_high: float,
    pO2_exponent: float,
    pO2_exponent_declared: bool,
    pO2_reference_bar: float,
) -> tuple[_ReferencePressureModel, float]:
    """Compile nasa7/nasa9/shomate pressure dispatch onto landed thermo modules.

    Modes
    -----
    1. **Pure-component Psat** — gas + condensed polynomials →
       ``P_sat / P° = exp(−(G_gas − G_cond)/(R T))``.
    2. **Source-reaction partial** — balanced ``source_reactions`` entry +
       per-formula thermo → ``K = exp(−ΔG_rxn/(R T))``, then the unit-activity
       partial pressure of the vapor product at ``pO2_reference_bar``.

    Returns ``(reference_model, derived_pO2_exponent)`` where the derived
    exponent is ``−ν_O2/ν_vapor`` for the reaction path (else ``0.0``). The
    outer :class:`CompiledPressureEvaluator` uses that exponent for activity
    corrections away from ``pO2_reference_bar``.

    Phase-transition **locations** are never read from these polynomials;
    Ellingham remains the single home for physical breakpoints.
    """
    phase_props = row.get("phase_properties")
    thermo_by_key = _collect_phase_thermo_records(
        species_id, model, phase_props if isinstance(phase_props, list) else []
    )
    raw_source_reaction_id = model.get("source_reaction_id")
    has_source_reaction = (
        raw_source_reaction_id is not None
        and str(raw_source_reaction_id).strip() != ""
    )
    # Mode selection is driven by the declared pressure observable, not by
    # whether a source_reaction_id happens to be present (fail-closed).
    if pressure_kind == PressureObservable.EQUILIBRIUM_PARTIAL_PRESSURE.value:
        if not has_source_reaction:
            raise CatalogCompileError(
                f"{species_id}: equilibrium_partial_pressure requires a "
                "non-empty source_reaction_id"
            )
    elif pressure_kind == PressureObservable.PURE_COMPONENT_SATURATION_PRESSURE.value:
        if has_source_reaction:
            raise CatalogCompileError(
                f"{species_id}: pure_component_saturation_pressure must not "
                "declare source_reaction_id (use equilibrium_partial_pressure "
                "for source-reaction partials)"
            )
    elif has_source_reaction:
        # Other observables: reaction path only when explicitly requested.
        pass
    else:
        # No reaction id and not a pure-psat observable → cannot decide.
        if pressure_kind not in {
            PressureObservable.PURE_COMPONENT_SATURATION_PRESSURE.value,
            PressureObservable.EQUILIBRIUM_PARTIAL_PRESSURE.value,
        }:
            raise CatalogCompileError(
                f"{species_id}: thermo family does not support pressure_kind "
                f"{pressure_kind!r} without source_reaction_id"
            )

    Pstd = float(
        model.get("reference_pressure_Pa", _THERMO_REFERENCE_PRESSURE_PA)
    )
    if not math.isfinite(Pstd) or Pstd <= 0.0:
        raise CatalogCompileError(
            f"{species_id}: reference_pressure_Pa must be finite and positive"
        )

    if has_source_reaction:
        reaction_id = _required_string(
            raw_source_reaction_id, f"{species_id}.source_reaction_id"
        )
        reactions = row.get("source_reactions")
        if not isinstance(reactions, list):
            raise CatalogCompileError(
                f"{species_id}: thermo source-reaction path requires source_reactions"
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
        reaction = matched[0]
        _validate_balanced_reaction(species_id, reaction)
        species_thermo_raw = model.get("species_thermo") or thermo_by_key
        if not isinstance(species_thermo_raw, Mapping):
            raise CatalogCompileError(
                f"{species_id}: species_thermo must be a mapping of formula → record"
            )
        polys: dict[str, Any] = {}
        for formula, rec in species_thermo_raw.items():
            rec_map = _mapping(rec, f"{species_id}.species_thermo[{formula}]")
            fam = evaluator_family
            if rec_map.get("evaluator_family") or rec_map.get("evaluator"):
                fam = _normalize_thermo_family(
                    str(rec_map.get("evaluator_family") or rec_map.get("evaluator")),
                    field=f"{species_id}.species_thermo[{formula}].evaluator",
                )
            polys[str(formula)] = _polynomial_from_thermo_record(
                name=f"{species_id}:{formula}",
                family=fam,
                record=rec_map,
            )
        # Target vapor identity: species_id and optional row formula, bare +
        # phase-suffixed spellings.
        target_keys = {str(species_id), _strip_phase_suffix(str(species_id))}
        row_formula = row.get("formula")
        if row_formula is not None and str(row_formula).strip():
            target_keys.add(str(row_formula).strip())
            target_keys.add(_strip_phase_suffix(str(row_formula)))

        # Stoichiometric (ν, poly, formula) terms: products +, reactants −.
        terms_builders: list[tuple[float, Any, str]] = []
        vapor_candidates: list[tuple[float, str]] = []
        for sign, key in ((-1.0, "reactants"), (1.0, "products")):
            participants = reaction.get(key)
            if not isinstance(participants, list):
                raise CatalogCompileError(
                    f"{species_id}: reaction requires {key}"
                )
            for item in participants:
                part = _mapping(item, f"{species_id}.reaction.{key}")
                formula = _required_string(
                    part.get("formula"), f"{species_id}.reaction.formula"
                )
                amount = _finite_positive(
                    part.get("stoichiometry"),
                    f"{species_id}.reaction.stoichiometry",
                )
                if formula not in polys:
                    raise CatalogCompileError(
                        f"{species_id}: missing thermo for reaction species {formula!r}"
                    )
                poly = polys[formula]
                nu = sign * amount
                terms_builders.append((nu, poly, formula))
                if sign <= 0.0:
                    continue
                # Product matching the target species — must be gas-standard-state.
                formula_keys = {formula, _strip_phase_suffix(formula)}
                if formula_keys.isdisjoint(target_keys):
                    continue
                std = getattr(poly, "standard_state", None)
                if std != "gas":
                    raise CatalogCompileError(
                        f"{species_id}: target vapor product {formula!r} must "
                        f"have gas standard_state; got {std!r}"
                    )
                vapor_candidates.append((amount, formula))
        if len(vapor_candidates) == 0:
            raise CatalogCompileError(
                f"{species_id}: source reaction has no gas product matching "
                f"target species {species_id!r} "
                f"(normalized targets {sorted(target_keys)})"
            )
        if len(vapor_candidates) > 1:
            raise CatalogCompileError(
                f"{species_id}: source reaction has multiple gas products "
                f"matching target species: "
                f"{[f for _, f in vapor_candidates]}"
            )
        vapor_nu = float(vapor_candidates[0][0])
        if vapor_nu <= 0.0:
            raise CatalogCompileError(
                f"{species_id}: vapor stoichiometry must be positive; got {vapor_nu}"
            )

        # ν_O2 from reaction stoich (product positive, reactant negative).
        # Recognize O2 / O2(g) / … — phase suffix must not silently zero this.
        nu_o2 = 0.0
        for nu, _poly, formula in terms_builders:
            if _is_dioxygen_formula(formula):
                nu_o2 += nu

        # Outer activity exponent: −ν_O2 / ν_v (0 when no O2 in the reaction).
        derived_pO2_exponent = (
            -nu_o2 / vapor_nu if abs(nu_o2) > 0.0 else 0.0
        )
        # Validate an explicit declaration against stoichiometry (caller also
        # re-checks and fills omitted exponents).
        if pO2_exponent_declared and abs(nu_o2) > 0.0:
            if not math.isclose(
                pO2_exponent, derived_pO2_exponent, rel_tol=0.0, abs_tol=1.0e-9
            ):
                raise CatalogCompileError(
                    f"{species_id}: pO2_exponent {pO2_exponent} disagrees with "
                    f"stoichiometry-derived {derived_pO2_exponent} "
                    f"(−ν_O2/ν_vapor with ν_O2={nu_o2}, ν_vapor={vapor_nu})"
                )
        elif pO2_exponent_declared and abs(nu_o2) == 0.0 and abs(pO2_exponent) > 0.0:
            raise CatalogCompileError(
                f"{species_id}: pO2_exponent {pO2_exponent} declared but "
                "reaction has no O2 participant"
            )

        for formula, poly in polys.items():
            _require_poly_covers_domain(
                species_id=species_id,
                label=formula,
                poly=poly,
                domain_low=domain_low,
                domain_high=domain_high,
            )

        def _log10_from_reaction(temperature_K: float) -> float:
            # K(T) = exp(−ΔG_rxn / RT) from per-species G°/(RT).
            # Derivation (premise → algebra → units → sanity): see
            # reaction_equilibrium_constant in nasa_cea.py.
            states = [
                (nu, poly.evaluate(temperature_K))
                for nu, poly, _formula in terms_builders
            ]
            K = reaction_equilibrium_constant(states, T_K=temperature_K)
            # Unit-activity partial of the vapor product at pO2_reference.
            #
            # For ν_v vapor + ν_O2 O2 (ν_O2 may be 0; sign follows reaction):
            #   K = (p_v/P°)^{ν_v} · (p_O2/P°)^{ν_O2} / a_cond...
            # At a=1, p_O2 = pO2_ref (bar) → p_O2/P° = pO2_ref_bar * 1e5 / P°.
            #   p_v/P° = [ K / (p_O2/P°)^{ν_O2} ]^{1/ν_v}
            #
            # Use stoichiometric ν_O2 in the pre-root division — NOT
            # −pO2_exponent. The declared outer exponent is already
            # −ν_O2/ν_v; using its negation here double-applies 1/ν_v.
            pO2_over_Pstd = (pO2_reference_bar * 1.0e5) / Pstd
            if abs(nu_o2) > 0.0:
                K_eff = K / (pO2_over_Pstd**nu_o2)
            else:
                K_eff = K
            ratio = K_eff ** (1.0 / vapor_nu)
            pressure_pa = ratio * Pstd
            if not math.isfinite(pressure_pa) or pressure_pa <= 0.0:
                raise CatalogCompileError(
                    f"{species_id}: thermo reaction evaluator produced invalid "
                    f"pressure at T={temperature_K}"
                )
            return math.log10(pressure_pa)

        return (
            _ReferencePressureModel(
                evaluator_family=evaluator_family,
                coefficients=MappingProxyType({}),
                points=(),
                thermo_log10_pressure=_log10_from_reaction,
            ),
            derived_pO2_exponent,
        )

    # Pure-component saturation from gas + condensed pair.
    gas_rec = (
        model.get("gas_thermo_record")
        or model.get("thermo_record")
        or thermo_by_key.get("gas")
    )
    condensed_rec = (
        model.get("condensed_thermo_record")
        or thermo_by_key.get("condensed")
        or thermo_by_key.get("condensed_solid")
        or thermo_by_key.get("condensed_liquid")
    )
    if gas_rec is None or condensed_rec is None:
        raise CatalogCompileError(
            f"{species_id}: thermo pure-psat path requires gas + condensed "
            "thermo records (thermo_record/gas_thermo_record + "
            "condensed_thermo_record, or phase_properties entries)"
        )
    gas_map = _mapping(gas_rec, f"{species_id}.gas_thermo")
    cond_map = _mapping(condensed_rec, f"{species_id}.condensed_thermo")
    gas_poly = _polynomial_from_thermo_record(
        name=f"{species_id}:gas",
        family=evaluator_family,
        record=gas_map,
        default_standard_state="gas",
    )
    cond_poly = _polynomial_from_thermo_record(
        name=f"{species_id}:condensed",
        family=evaluator_family,
        record=cond_map,
        default_standard_state="condensed",
    )
    # Standard-state guard (matches NasaCeaPolynomial.pure_psat_over_Pstd).
    if gas_poly.standard_state != "gas":
        raise CatalogCompileError(
            f"{species_id}: pure-psat gas record requires standard_state 'gas'; "
            f"got {gas_poly.standard_state!r}"
        )
    if cond_poly.standard_state == "gas":
        raise CatalogCompileError(
            f"{species_id}: pure-psat condensed record requires condensed "
            f"standard_state; got {cond_poly.standard_state!r}"
        )
    _require_poly_covers_domain(
        species_id=species_id,
        label="gas",
        poly=gas_poly,
        domain_low=domain_low,
        domain_high=domain_high,
    )
    _require_poly_covers_domain(
        species_id=species_id,
        label="condensed",
        poly=cond_poly,
        domain_low=domain_low,
        domain_high=domain_high,
    )

    def _log10_from_psat(temperature_K: float) -> float:
        g_gas = gas_poly.evaluate(temperature_K).g_over_RT
        g_cond = cond_poly.evaluate(temperature_K).g_over_RT
        # P_sat / P° = exp(−(G_gas − G_cond)/(R T))
        ratio = math.exp(-(g_gas - g_cond))
        pressure_pa = ratio * Pstd
        if not math.isfinite(pressure_pa) or pressure_pa <= 0.0:
            raise CatalogCompileError(
                f"{species_id}: thermo pure-psat evaluator produced invalid "
                f"pressure at T={temperature_K}"
            )
        return math.log10(pressure_pa)

    return (
        _ReferencePressureModel(
            evaluator_family=evaluator_family,
            coefficients=MappingProxyType({}),
            points=(),
            thermo_log10_pressure=_log10_from_psat,
        ),
        0.0,
    )


def _collect_phase_thermo_records(
    species_id: str,
    model: Mapping[str, Any],
    phase_properties: Sequence[Any],
) -> dict[str, Mapping[str, Any]]:
    """Gather thermo records from pressure model + phase_properties.

    ``phase_properties`` entries may declare ``evaluator: nasa9`` (or
    ``evaluator_family``) alongside ``thermo_record`` / inline coefficients.
    """
    out: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(phase_properties):
        if not isinstance(raw, Mapping):
            raise CatalogCompileError(
                f"{species_id}.phase_properties[{index}] must be a mapping"
            )
        phase = raw.get("phase") or raw.get("standard_state")
        record = raw.get("thermo_record")
        if record is None and (
            raw.get("segments") is not None or raw.get("coefficients") is not None
        ):
            record = raw
        if record is None:
            continue
        if not isinstance(record, Mapping):
            raise CatalogCompileError(
                f"{species_id}.phase_properties[{index}].thermo_record must be a mapping"
            )
        # Propagate evaluator declaration from the phase row when missing.
        merged = dict(record)
        if "evaluator_family" not in merged and "evaluator" not in merged:
            if raw.get("evaluator_family") is not None:
                merged["evaluator_family"] = raw["evaluator_family"]
            elif raw.get("evaluator") is not None:
                merged["evaluator"] = raw["evaluator"]
        if "standard_state" not in merged and phase is not None:
            merged["standard_state"] = phase
        key = str(phase) if phase is not None else f"phase_{index}"
        out[key] = merged
    # Inline model thermo_record defaults to gas when standard_state says so.
    inline = model.get("thermo_record")
    if isinstance(inline, Mapping):
        std = inline.get("standard_state") or model.get("standard_state") or "gas"
        out.setdefault(str(std), inline)
        if std == "gas":
            out.setdefault("gas", inline)
    return out


def _polynomial_from_thermo_record(
    *,
    name: str,
    family: str,
    record: Mapping[str, Any],
    default_standard_state: str | None = None,
) -> NasaCeaPolynomial | ShomatePolynomial:
    """Build a landed NASA or Shomate polynomial from a catalog thermo record."""
    fam = family
    if record.get("evaluator_family") or record.get("evaluator"):
        fam = _normalize_thermo_family(
            str(record.get("evaluator_family") or record.get("evaluator")),
            field=f"{name}.evaluator",
        )
    standard_state = (
        record.get("standard_state")
        or default_standard_state
    )
    if not isinstance(standard_state, str) or not standard_state.strip():
        raise CatalogCompileError(
            f"{name}: thermo record requires standard_state convention"
        )
    standard_state = standard_state.strip()
    delta_f = record.get("delta_f_H_298_15_J_per_mol")
    delta_f_f = float(delta_f) if delta_f is not None else None
    formula = record.get("formula")
    formula_s = str(formula) if formula is not None else None
    citation = record.get("citation")
    citation_s = str(citation) if citation is not None else None
    Pstd = float(record.get("reference_pressure_Pa", _THERMO_REFERENCE_PRESSURE_PA))

    if fam in ("nasa_cea_7", "nasa_cea_9"):
        segments_raw = record.get("segments")
        if not isinstance(segments_raw, list) or not segments_raw:
            raise CatalogCompileError(
                f"{name}: NASA thermo record requires a non-empty segments list"
            )
        segs: list[Any] = []
        for index, seg in enumerate(segments_raw):
            sm = _mapping(seg, f"{name}.segments[{index}]")
            t_min = float(sm.get("T_min_K", sm.get("t_min_K")))
            t_max = float(sm.get("T_max_K", sm.get("t_max_K")))
            if fam == "nasa_cea_7":
                coeffs = sm.get("coefficients") or sm.get("a_coefficients")
                if not isinstance(coeffs, (list, tuple)) or len(coeffs) != 7:
                    raise CatalogCompileError(
                        f"{name}: NASA-7 segment needs 7 coefficients"
                    )
                segs.append(
                    Nasa7Segment(
                        t_min,
                        t_max,
                        tuple(float(c) for c in coeffs),  # type: ignore[arg-type]
                    )
                )
            else:
                coeffs = sm.get("a_coefficients") or sm.get("coefficients")
                if not isinstance(coeffs, (list, tuple)) or len(coeffs) != 7:
                    raise CatalogCompileError(
                        f"{name}: NASA-9 segment needs 7 a-coefficients"
                    )
                try:
                    b1 = float(sm["b1"])
                    b2 = float(sm["b2"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise CatalogCompileError(
                        f"{name}: NASA-9 segment requires b1 and b2"
                    ) from exc
                segs.append(
                    Nasa9Segment(
                        t_min,
                        t_max,
                        tuple(float(c) for c in coeffs),  # type: ignore[arg-type]
                        b1,
                        b2,
                    )
                )
        return NasaCeaPolynomial(
            name=name,
            family=fam,  # type: ignore[arg-type]
            standard_state=standard_state,  # type: ignore[arg-type]
            segments=tuple(segs),
            formula=formula_s,
            delta_f_H_298_15_J_per_mol=delta_f_f,
            citation=citation_s,
            reference_pressure_Pa=Pstd,
        )

    if fam == "shomate":
        segments_raw = record.get("segments")
        segs_s: list[ShomateSegment] = []
        if isinstance(segments_raw, list) and segments_raw:
            for index, seg in enumerate(segments_raw):
                sm = _mapping(seg, f"{name}.segments[{index}]")
                t_min = float(sm.get("T_min_K", sm.get("range_K", [None, None])[0]))
                t_max = float(sm.get("T_max_K", sm.get("range_K", [None, None])[1]))
                coeffs_raw = sm.get("coefficients") or sm.get("coeffs")
                coeffs = coefficients_from_mapping(coeffs_raw)
                segs_s.append(
                    ShomateSegment(t_min, t_max, coeffs)  # type: ignore[arg-type]
                )
        else:
            # Single-segment form: coefficients + valid domain on the record.
            domain = record.get("valid_domain") or record.get("range_K")
            if isinstance(domain, Mapping) and "temperature_K" in domain:
                bounds = domain["temperature_K"]
                t_min, t_max = float(bounds[0]), float(bounds[1])
            elif isinstance(domain, (list, tuple)) and len(domain) == 2:
                t_min, t_max = float(domain[0]), float(domain[1])
            else:
                raise CatalogCompileError(
                    f"{name}: Shomate record needs segments or valid temperature bounds"
                )
            coeffs_raw = record.get("coefficients") or record.get("coeffs")
            coeffs = coefficients_from_mapping(coeffs_raw)
            segs_s.append(
                ShomateSegment(t_min, t_max, coeffs)  # type: ignore[arg-type]
            )
        return ShomatePolynomial(
            name=name,
            standard_state=standard_state,  # type: ignore[arg-type]
            segments=tuple(segs_s),
            formula=formula_s,
            delta_f_H_298_15_J_per_mol=delta_f_f,
            citation=citation_s,
            reference_pressure_Pa=Pstd,
        )

    raise CatalogCompileError(f"{name}: unsupported thermo family {fam!r}")


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
        # Accept both temperature_K:[lo,hi] and CEA-ingest T_min_K/T_max_K.
        low, high = _domain_temperature_bounds(
            domain, field=f"{species_id}.valid_domain"
        )
        result["valid_range_K"] = [low, high]
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
    low, high = _domain_temperature_bounds(
        domain, field=f"{species_id}.valid_domain"
    )
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
