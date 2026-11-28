"""Evaporation and condensation-routing helpers for PyrolysisSimulator."""

from __future__ import annotations

import copy
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from numbers import Real
from typing import TYPE_CHECKING, Any, NamedTuple

from simulator.alpha_kinetics import (
    ALPHA_AUTHORITY_STATUS_FIELD,
    PROXY_BRIDGE_ALPHA_STATUS,
    parse_alpha_contract,
)
from simulator.accounting import AccountingError, resolve_species_formula
from simulator.account_ids import (
    CHROMIUM_CONDENSED_OXIDE_ACCOUNT,
    SPENT_REDUCTANT_RESIDUE_ACCOUNT,
)

if TYPE_CHECKING:
    from simulator.melt_backend.magemin import MAGEMinDomainIdentity

# Baffle solids the stage UI may show. Wall, overhead, retained holdup
# and O2 stay off this projection: the swing withdraws whatever this
# map names, and only these accounts received the winning solids.
_STAGE_BAFFLE_PRODUCT_ACCOUNTS = frozenset({
    "process.condensation_train",
    CHROMIUM_CONDENSED_OXIDE_ACCOUNT,
})
from simulator.chemistry.melt_activity import single_cation_mole_fractions
from simulator.chemistry.kernel import (
    ChemistryIntent,
    ProviderUnavailableError,
)
from simulator.fe_redox import (
    KRESS91_LIQUID_CALIBRATION_MAX_T_C,
    KRESS91_LIQUID_CALIBRATION_MIN_T_C,
    RedoxState,
    _non_absolute_redox_state_kwargs,
    redox_state_cache_identity_matches,
    _kress91_gate_key_referenced_log_fO2,
    kress91_temperature_band_case,
    normalize_redox_state,
)
from simulator.melt_backend.liquidus import (
    LIQUIDUS_LIQUID_EPSILON,
    LiquidusSampleError,
)
from simulator.engine_pool import EngineWorkerTimeout
from simulator.melt_regime import (
    MeltRegime,
    legacy_raw_liquid_fraction_is_zero,
    liquid_fraction_invalidity,
    melt_regime,
)
from simulator.physical_constants import PA_PER_BAR, PA_PER_MBAR
from simulator.state import (
    GAS_CONSTANT,
    MOLAR_MASS,
    OXIDE_TO_METAL,
    PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNT_PREFIX,
    STOICH_RATIOS,
    EvaporationFlux,
    clamp_stir_factor,
)
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
from simulator.vapour_rail.batch import (
    FLUX_ACTIVATION_EPOCH_PRE_RG,
    FluxActivationContext,
)
from simulator.vapour_rail.activity import StandardStateIdentity
from simulator.vapour_rail.instrumentation import (
    CONTROL_BATCH_REPORT_KEY,
    CONTROL_FLUX_PRESSURES_KEY,
    CONTROL_SHADOW_EQUAL_KEY,
    EFFECTIVE_PRESSURE_SOURCE_CONTEXT,
    EffectivePressureSource,
    TYPED_FAILURE_EFFECTIVE_PRESSURE_SPECIES_SET_MISMATCH,
    _channel_is_typed_non_debit,
    compatibility_pressure_read_context,
    compare_live_shadow_to_batch_flux,
    _finite_live_map,
    finite_live_pressure_map,
    flux_pressures_from_batch,
    provider_flux_overlay_controls,
    serialize_vapour_batch,
)


PRE_RG_EFFECTIVE_PRESSURE_SOURCE_ID = (
    'equilibrium_backend_effective_pressure_pre_rg'
)
_VAPOUR_BATCH_NO_DEBITING_PRESSURE_OUTCOME = (
    'vapour_batch_no_debiting_pressure_outcome'
)


@dataclass(frozen=True)
class _EvaporationAuxMaps:
    """Publisher operands plus species-local failures from their owner."""

    molar_masses_kg_mol: dict[str, float]
    stoich_by_species: dict[str, dict]
    available_oxide_kg: dict[str, float]
    unavailable_by_species: dict[str, str]

    def __iter__(self):
        # Keep the historical three-map unpacking used by read-only callers.
        yield self.molar_masses_kg_mol
        yield self.stoich_by_species
        yield self.available_oxide_kg


@dataclass(frozen=True)
class _EvaporationFluxControlResult:
    """Provider controls with the aux-map owner's local failure result."""

    controls: dict[str, Any]
    molar_masses_kg_mol: dict[str, float]
    unavailable_by_species: dict[str, str]
    transport_sidecar: dict[str, Any]

    def __iter__(self):
        # Keep the historical two-value unpacking at existing consumers.
        yield self.controls
        yield self.molar_masses_kg_mol


class _EvaporationProviderControls(dict[str, Any]):
    """Keep transport metadata beside the base-shaped provider mapping."""

    def __init__(
        self,
        values: Mapping[str, Any],
        *,
        transport_sidecar: Mapping[str, Any],
    ) -> None:
        super().__init__(values)
        self.transport_sidecar = copy.deepcopy(dict(transport_sidecar))
def production_vapor_molar_mass_g_per_mol(
    catalog: Mapping[str, Any] | None,
    species: str,
) -> Any:
    """Molar mass in g/mol the flux publisher emits for ``species``.

    ``_build_evaporation_aux_maps`` is this lookup: the legacy-view
    ``molar_mass_g_mol``, else ``MOLAR_MASS``. The surface closure's
    conductance uses the same operand. Catalog Ca is 40.08 g/mol; the
    formula-derived CIAAW mass is 40.078 g/mol. A root weighted by the
    formula mass reports a residual the emitted flux does not have.
    ``None`` means the publisher raises and emits no flux. The closure
    must not substitute a formula mass in that case.
    """

    row: Mapping[str, Any] | None = None
    if isinstance(catalog, Mapping):
        # Same group order as ``_build_evaporation_aux_maps``: a metals
        # row wins even when its molar mass is absent. The oxide row is
        # the fallback only when the metals entry is missing.
        metals = catalog.get("metals") or {}
        oxides = catalog.get("oxide_vapors") or {}
        if isinstance(metals, Mapping):
            candidate = metals.get(str(species))
            if isinstance(candidate, Mapping) and candidate:
                row = candidate
        if row is None and isinstance(oxides, Mapping):
            candidate = oxides.get(str(species))
            if isinstance(candidate, Mapping) and candidate:
                row = candidate
    if row is not None and row.get("molar_mass_g_mol") is not None:
        return row.get("molar_mass_g_mol")
    return MOLAR_MASS.get(str(species))


PRE_RG_EFFECTIVE_PRESSURE_CHEAP_ZERO_BELOW_C = 1050.0
# Stage-0 phosphorus vapour carriers. C0 must not claim them (they stay
# condensed). C0B claims only this set. Activity-evidence annotations use
# the same membership. Not STRUCTURALLY_ZERO_P_CARRIERS: that name is the
# EC2 "no VP channel" classification, a different rule.
_STAGE0_PHOSPHORUS_VAPOUR_CARRIERS = frozenset({
    'PO', 'PO2', 'P2', 'P4', 'P4O6', 'P4O10',
})


def _has_evaporation_parent_oxide(sp_data: Mapping[str, Any] | None) -> bool:
    return isinstance(sp_data, Mapping) and bool(sp_data.get('parent_oxide'))


def _evaporation_stoich(species: str, sp_data: dict, registry):
    if not _has_evaporation_parent_oxide(sp_data):
        raise AccountingError(
            f"vapor species {species!r} requires parent_oxide "
            "metadata before ledger routing"
        )
    parent_oxide = str(sp_data['parent_oxide'])

    has_oxide = sp_data.get('stoich_oxide_per_vapor') is not None
    has_o2 = sp_data.get('stoich_O2_per_vapor') is not None
    if has_oxide or has_o2:
        missing = []
        if not has_oxide:
            missing.append('stoich_oxide_per_vapor')
        if not has_o2:
            missing.append('stoich_O2_per_vapor')
        if missing:
            raise AccountingError(
                f"vapor species {species!r} from {parent_oxide!r} "
                f"missing explicit stoich metadata: {', '.join(missing)}"
            )
        oxide_per_product = float(sp_data['stoich_oxide_per_vapor'])
        O2_per_product = float(sp_data['stoich_O2_per_vapor'])
        if oxide_per_product <= 0.0:
            raise AccountingError(
                f"vapor species {species!r} from {parent_oxide!r} "
                "requires positive stoich_oxide_per_vapor"
            )
        if not math.isclose(
            oxide_per_product,
            1.0 + O2_per_product,
            rel_tol=1e-6,
            abs_tol=1e-9,
        ):
            raise AccountingError(
                f"vapor species {species!r} from {parent_oxide!r} "
                "stoich metadata must conserve mass: "
                "stoich_oxide_per_vapor must equal "
                "1 + stoich_O2_per_vapor"
            )
        _validate_evaporation_stoich_atoms(
            parent_oxide,
            species,
            oxide_per_product,
            O2_per_product, registry,
        )
        return {
            'parent_oxide': parent_oxide,
            'oxide_per_product_kg': oxide_per_product,
            'O2_per_product_kg': O2_per_product,
            'oxygen_destination': sp_data.get('oxygen_destination'),
        }

    implied = OXIDE_TO_METAL.get(parent_oxide, ('', 0, 0))[0]
    if species != implied:
        raise AccountingError(
            f"vapor species {species!r} from {parent_oxide!r} requires "
            "explicit stoich_oxide_per_vapor and stoich_O2_per_vapor; "
            f"STOICH_RATIOS fallback only applies to elemental "
            f"{implied!r}"
        )
    fallback = STOICH_RATIOS.get(parent_oxide)
    if not fallback or fallback[0] <= 0:
        raise AccountingError(
            f"vapor species {species!r} from {parent_oxide!r} has no "
            "valid elemental stoich fallback"
        )
    kg_product_per_kg_oxide, kg_O2_per_kg_oxide = fallback
    oxide_per_product = 1.0 / kg_product_per_kg_oxide
    O2_per_product = kg_O2_per_kg_oxide / kg_product_per_kg_oxide
    if not math.isclose(
        oxide_per_product,
        1.0 + O2_per_product,
        rel_tol=1e-6,
        abs_tol=1e-9,
    ):
        raise AccountingError(
            f"STOICH_RATIOS[{parent_oxide!r}] does not conserve mass"
        )
    _validate_evaporation_stoich_atoms(
        parent_oxide,
        species,
        oxide_per_product,
        O2_per_product, registry,
    )
    return {
        'parent_oxide': parent_oxide,
        'oxide_per_product_kg': oxide_per_product,
        'O2_per_product_kg': O2_per_product,
        'oxygen_destination': sp_data.get('oxygen_destination'),
    }


def _validate_evaporation_stoich_atoms(
    parent_oxide: str,
    product_species: str,
    oxide_per_product_kg: float,
    O2_per_product_kg: float,
    registry,
) -> None:
    debit_atoms = _atom_moles_for_kg(
        parent_oxide, oxide_per_product_kg, registry)
    credit_atoms = defaultdict(float)
    product_atoms = _atom_moles_for_kg(product_species, 1.0, registry)
    for element, moles in product_atoms.items():
        credit_atoms[element] += moles
    if O2_per_product_kg >= 0.0:
        oxygen_atoms = _atom_moles_for_kg('O2', O2_per_product_kg, registry)
        for element, moles in oxygen_atoms.items():
            credit_atoms[element] += moles
    else:
        oxygen_atoms = _atom_moles_for_kg('O2', abs(O2_per_product_kg), registry)
        for element, moles in oxygen_atoms.items():
            debit_atoms[element] = debit_atoms.get(element, 0.0) + moles

    for element in set(debit_atoms) | set(credit_atoms):
        debit = debit_atoms.get(element, 0.0)
        credit = credit_atoms.get(element, 0.0)
        if not math.isclose(debit, credit, rel_tol=1e-6, abs_tol=1e-9):
            raise AccountingError(
                f"vapor species {product_species!r} from "
                f"{parent_oxide!r} stoich metadata does not conserve "
                f"{element} atoms"
            )


def _atom_moles_for_kg(species: str, kg: float, registry) -> dict:
    if kg <= 0.0:
        return {}
    formula = resolve_species_formula(
        species, registry)
    species_moles = float(kg) / formula.molar_mass_kg_per_mol()
    return formula.atom_moles(species_moles)


def _project_evaporation_overhead_source_mol_hr(
    rates_kg_hr: Mapping[str, float],
    stoich_by_species: Mapping[str, Mapping[str, Any]],
    registry: Mapping[str, Any],
) -> dict[str, float]:
    source_mol_hr: dict[str, float] = {}
    o2_molar_mass = resolve_species_formula(
        'O2', registry
    ).molar_mass_kg_per_mol()
    for species, raw_rate in rates_kg_hr.items():
        rate_kg_hr = max(0.0, float(raw_rate))
        if rate_kg_hr <= 0.0:
            continue
        formula = resolve_species_formula(
            species, registry
        )
        source_mol_hr[species] = (
            source_mol_hr.get(species, 0.0)
            + rate_kg_hr / formula.molar_mass_kg_per_mol()
        )
        stoich = dict(stoich_by_species.get(species) or {})
        o2_kg_hr = rate_kg_hr * float(
            stoich.get('O2_per_product_kg') or 0.0
        )
        oxygen_destination = str(
            stoich.get('oxygen_destination') or ''
        )
        vapor_oxygen_atoms = float(
            formula.elements.get('O', 0.0) or 0.0
        )
        if (
            o2_kg_hr < 0.0
            or (oxygen_destination != 'reservoir.fo2_buffer'
                and (vapor_oxygen_atoms > 0.0
                     or oxygen_destination == 'process.overhead_gas'))
        ):
            source_mol_hr['O2'] = (
                source_mol_hr.get('O2', 0.0)
                + o2_kg_hr / o2_molar_mass
            )
    return source_mol_hr


class EvaporationFluxRefusal(ProviderUnavailableError):
    """Typed terminal refusal for unavailable authoritative flux inputs."""

    terminal_refusal = True

    def __init__(self, reason: str, diagnostic: Mapping[str, Any]):
        self.reason = str(reason)
        self.diagnostic = copy.deepcopy(dict(diagnostic))
        super().__init__(self.reason)


class _VapourBatchResolutionOutcome(NamedTuple):
    resolved_species: frozenset[str]
    reason: str | None
    evidence: Mapping[str, Any]
    refusal: EvaporationFluxRefusal | None


def evaporation_flux_refusal_from_configuration_error(
    exc: BaseException,
) -> EvaporationFluxRefusal:
    """Map helper configuration errors onto the runner's typed refusal set.

    Direct helper consumers (native-Fe vapor, C7 Ca) previously raised
    ``EvaporationFluxConfigurationError``, which is absent from
    ``run_executor._ALL_TYPED_PHYSICS_REFUSALS`` and classified as an ordinary
    failure. Normalize to ``EvaporationFluxRefusal`` so hostile melt-resistance
    configs refuse on every path (projection, core, extraction).
    """
    detail = str(exc)
    lowered = detail.lower()
    if (
        "melt resistance" in lowered
        or "universal pressure conductance" in lowered
    ):
        reason = "uncertified_melt_resistance_model"
    else:
        reason = "evaporation_flux_configuration_error"
    return EvaporationFluxRefusal(
        reason,
        {
            "reason": reason,
            "detail": detail,
            "evaporation_flux_kg_hr": {},
        },
    )


def _vapour_debit_floor_kg_hr() -> float:
    """Publisher nontrivial-flux threshold, the melt-debit floor.

    Melt debit, ledger totals, and ``species_kg_hr`` omit a vapour rate
    at or below this value. Wall-supply accounting does not: the integral
    keeps the computed rate, and booking applies ledger resolution.

    Imported inside the call. ``evaporation`` is imported while
    ``engines.builtin`` is still initialising, and a module-level import
    of ``evaporation_flux`` re-enters ``_common`` before that package
    finishes. The returned object is ``_NONTRIVIAL_FLUX_KG_HR``.
    """

    from engines.builtin.evaporation_flux import _NONTRIVIAL_FLUX_KG_HR

    return _NONTRIVIAL_FLUX_KG_HR


class ProjectedVapourRates(NamedTuple):
    """Debit map, supply map, and the Fe residual-cap record.

    ``debit_kg_hr`` is the publication-gated melt debit. ``supply_kg_hr``
    is every finite non-negative gated rate, including rates the debit
    floor dropped and rates the publisher stored only under
    ``below_threshold_evaporation_flux``.
    """

    debit_kg_hr: dict[str, float]
    supply_kg_hr: dict[str, float]
    fe_allocation: dict[str, Any] | None


def project_publisher_vapour_rates(
    diagnostic: Mapping[str, Any] | None,
    *,
    liquid_fraction_factor: float = 1.0,
    fe_residual_kg_hr: float | None = None,
) -> ProjectedVapourRates:
    """Split one publisher diagnostic into the melt debit and the supply.

    Invariant: a finite non-negative rate the publisher computed is in
    ``supply_kg_hr`` after the liquid-fraction factor and, for Fe, the
    residual cap. The debit floor does not remove it. A species present
    in ``evaporation_flux_kg_hr`` is not also taken from
    ``below_threshold_evaporation_flux``, so the two buckets cannot
    double-count. Only the published bucket can enter the debit, and
    only above the floor, which is the pre-existing melt-debit rule.

    ``zero_net_evaporation_flux`` is a computed drive of zero
    (``P_eq <= P_bulk``, or a series flux of zero). Those species enter
    the supply at 0 and stay out of the debit. A positive number in
    that bucket is not a rate. A species already stored from a
    published or below-threshold bucket is left as that rate.
    """

    payload = diagnostic if isinstance(diagnostic, Mapping) else {}
    published = payload.get("evaporation_flux_kg_hr") or {}
    if not isinstance(published, Mapping):
        published = {}
    below_payload = payload.get("below_threshold_evaporation_flux")
    below: Mapping[str, Any] = {}
    if isinstance(below_payload, Mapping):
        raw_below = below_payload.get("fluxes_kg_hr")
        if isinstance(raw_below, Mapping):
            below = raw_below
    factor = float(liquid_fraction_factor)
    published_names = {str(species) for species in published}
    debit: dict[str, float] = {}
    supply: dict[str, float] = {}
    fe_allocation: dict[str, Any] | None = None

    def _store(species: str, gated: float, *, debit_eligible: bool) -> None:
        if math.isfinite(gated) and gated >= 0.0:
            supply[species] = gated
        if debit_eligible and gated > _vapour_debit_floor_kg_hr():
            debit[species] = gated

    def _cap_fe(species: str, gated: float, *, record: bool) -> float:
        nonlocal fe_allocation
        if species != "Fe" or fe_residual_kg_hr is None:
            return gated
        original = gated
        capped = min(gated, float(fe_residual_kg_hr))
        if record:
            # Same record the debit loop wrote before this split. It is
            # the capped rate, including a rate the debit floor then drops.
            fe_allocation = {
                "rule": "pool_first_residual",
                "native_pool_activity_argument": (
                    "not modeled; allocation is an explicit pool-first "
                    "scheduling policy"
                ),
                "melt_fe_raw_kg_hr": float(original),
                "melt_fe_residual_capacity_kg_hr": float(fe_residual_kg_hr),
                "melt_fe_applied_kg_hr": float(capped),
            }
        return capped

    for species, raw in published.items():
        name = str(species)
        gated = _cap_fe(name, float(raw) * factor, record=True)
        _store(name, gated, debit_eligible=True)
    for species, raw in below.items():
        name = str(species)
        if name in published_names:
            continue
        gated = _cap_fe(name, float(raw) * factor, record=False)
        _store(name, gated, debit_eligible=False)
    zero_net = payload.get("zero_net_evaporation_flux")
    if isinstance(zero_net, Mapping):
        for species, raw in zero_net.items():
            name = str(species)
            if name in supply:
                continue
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                continue
            rate = float(raw)
            # Exact zero only. A positive value here would be a second
            # publisher, and a negative value is not a computed supply.
            if math.isfinite(rate) and rate == 0.0:
                supply[name] = 0.0
    return ProjectedVapourRates(debit, supply, fe_allocation)


def wall_supply_rates_kg_hr(
    *layers: Mapping[str, Any] | None,
) -> dict[str, float]:
    """Union of computed vapour rates. A later layer overwrites a species.

    This is the wall-supply integrand. Every finite rate >= 0 that a
    layer computed is retained. The melt-debit floor is not applied.
    A missing layer adds nothing and is not a zero. Non-finite and
    negative rates are not a computed supply. Ledger resolution is
    applied at booking, not here.
    """

    merged: dict[str, float] = {}
    for layer in layers:
        if not isinstance(layer, Mapping):
            continue
        for species, raw in layer.items():
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                continue
            rate = float(raw)
            if not math.isfinite(rate) or rate < 0.0:
                continue
            merged[str(species)] = rate
    return merged


def publish_wall_supply_on_flux(
    flux: Any,
    computed: Mapping[str, Any] | None,
    *,
    debit_kg_hr: Mapping[str, Any] | None = None,
    phase_scale: float | None = 1.0,
) -> dict[str, float]:
    """Write the wall-supply integrand onto ``flux.published_species_kg_hr``.

    This is the only writer of that map. ``species_kg_hr`` stays the
    melt debit: a carrier at or below the debit floor, or one whose
    evaporation transition was not committed, may be absent there. The
    supply map still carries every computed rate.

    An existing published map is already the phase-scaled debit plus any
    computed rates merged earlier, so a second call does not scale it
    again. Otherwise the debit layer is ``debit_kg_hr``, or
    ``flux.species_kg_hr`` when that argument is omitted, multiplied by
    ``phase_scale`` (None means 1). Computed rates the debit layer
    omitted are added from ``computed``. Where both name a species, the
    debit layer wins, so a post-depletion rate is not replaced by the
    pre-depletion publisher rate.
    """

    existing = getattr(flux, "published_species_kg_hr", None)
    if isinstance(existing, Mapping):
        debit_layer: Mapping[str, Any] = existing
    else:
        source = (
            debit_kg_hr
            if debit_kg_hr is not None
            else getattr(flux, "species_kg_hr", None)
        )
        scale = 1.0 if phase_scale is None else float(phase_scale)
        scaled: dict[str, float] = {}
        if isinstance(source, Mapping):
            for species, raw in source.items():
                if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                    continue
                rate = float(raw) * scale
                if math.isfinite(rate) and rate >= 0.0:
                    scaled[str(species)] = rate
        debit_layer = scaled
    published = wall_supply_rates_kg_hr(computed, debit_layer)
    flux.published_species_kg_hr = published
    return published


# Dynamic attribute, not an EvaporationFlux field. Condensation keys off
# species_kg_hr and carrier_authority_by_species; this evidence is read
# only by the wall-supply interval classifier.
WALL_SUPPLY_INTERVAL_PHYSICAL_ZERO_ATTR = "wall_supply_interval_physical_zero"
WALL_SUPPLY_INTERVAL_ZERO_EVIDENCE = "authorized_zero"
# Whole-interval physical zeros. A zero-net carrier is not in this set:
# that species' computed rate is 0 on the supply map, and a sibling the
# publisher did not compute stays an omission. The pre-RG empty
# effective-pressure map below 1050 C is not in this set either: a
# PressureValue can sit on a FluxRefusal or an upper bound, and that
# channel is not a physical zero (d-367).
WALL_SUPPLY_INTERVAL_ZERO_REASONS = frozenset({
    "zero_liquid",
    "non_evaporation_campaign",
    "frozen_before_evaporation",
    "below_400K",
    "proven_empty_inventory",
})


def note_wall_supply_interval_physical_zero(
    flux: Any,
    *,
    reason: str,
    host: Any | None = None,
) -> None:
    """Record that this interval evaporates no species.

    The snapshot writer copies the host record onto whatever flux
    object is stored, including a condensation replacement. The
    classifier then treats a species absent from that interval's rate
    map as this physical zero, unless that species' channel is a
    refusal or an upper bound. Those stay unavailable: the certificate
    does not price the other hours. A computed rate on the same
    interval is unchanged. Callers are the known non-evaporating
    returns only. An unexplained omission must not call this.
    """

    token = str(reason).strip()
    if token not in WALL_SUPPLY_INTERVAL_ZERO_REASONS:
        raise ValueError(
            "unknown wall-supply interval physical-zero reason "
            f"{reason!r}"
        )
    record = {
        "reason": token,
        "evidence": WALL_SUPPLY_INTERVAL_ZERO_EVIDENCE,
    }
    setattr(flux, WALL_SUPPLY_INTERVAL_PHYSICAL_ZERO_ATTR, dict(record))
    if host is not None:
        host._wall_supply_interval_physical_zero = dict(record)


def evaporation_flux_for_campaign_hour(
    *,
    evaporation_campaign: bool,
    host: Any | None = None,
) -> EvaporationFlux:
    """Flux object for one hour.

    C5, MRE, C6, and C7 do not evaporate. The empty rate map is a
    physical zero for every species, not a missing flux.
    """

    flux = EvaporationFlux()
    if not evaporation_campaign:
        note_wall_supply_interval_physical_zero(
            flux,
            reason="non_evaporation_campaign",
            host=host,
        )
    return flux


# Dynamic attribute, not an EvaporationFlux field. The snapshot inventory
# is copied after evaporation has already debited the melt. This map is
# the parent-oxide inventory at the start of the hour, before that debit.
PRE_EVAPORATION_MELT_OXIDE_KG_ATTR = "pre_evaporation_melt_oxide_kg"


def note_pre_evaporation_melt_oxide_kg(host: Any) -> None:
    """Remember the melt oxides before this hour's evaporation debit.

    ``_update_melt_composition`` projects the ledger after the debit.
    The snapshot then stores that remainder. The wall-supply charge is
    this earlier map, not the remainder. A missing inventory is left
    unset so the allocator does not invent a zero charge.
    """

    inventory = getattr(host, "inventory", None)
    raw = getattr(inventory, "melt_oxide_kg", None)
    if not isinstance(raw, Mapping):
        host._pre_evaporation_melt_oxide_kg = None
        return
    host._pre_evaporation_melt_oxide_kg = {
        str(oxide): kg for oxide, kg in raw.items()
    }


def attach_pre_evaporation_melt_oxide_kg(flux: Any, host: Any) -> None:
    """Copy the pre-evaporation oxide map onto the flux the snapshot keeps.

    Condensation may replace the flux object after the note. The host
    holds the map; this writes it onto whatever flux is stored.
    """

    raw = getattr(host, "_pre_evaporation_melt_oxide_kg", None)
    if not isinstance(raw, Mapping):
        return
    setattr(flux, PRE_EVAPORATION_MELT_OXIDE_KG_ATTR, dict(raw))


def attach_wall_supply_interval_physical_zero(flux: Any, host: Any) -> None:
    """Copy the hour's interval zero onto the flux the snapshot keeps."""

    record = getattr(host, "_wall_supply_interval_physical_zero", None)
    if not isinstance(record, Mapping):
        return
    reason = str(record.get("reason") or "").strip()
    evidence = str(record.get("evidence") or "")
    if (
        reason not in WALL_SUPPLY_INTERVAL_ZERO_REASONS
        or evidence != WALL_SUPPLY_INTERVAL_ZERO_EVIDENCE
    ):
        return
    setattr(
        flux,
        WALL_SUPPLY_INTERVAL_PHYSICAL_ZERO_ATTR,
        {"reason": reason, "evidence": evidence},
    )


# Dynamic attribute, not an EvaporationFlux field. The supply ceiling
# reads it. The snapshot oxygen reservoir is a later state.
EVALUATED_TRANSPORT_PO2_ATTR = "evaluated_transport_pO2"
EVALUATED_TRANSPORT_PO2_EVIDENCE = "vapour_evaluation"


def _positive_transport_pO2_bar(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    pressure = float(raw)
    if not math.isfinite(pressure) or pressure <= 0.0:
        return None
    return pressure


def pressure_vapour_evaluation_consumed(
    diagnostic: Any,
    *,
    dispatch_pO2_bar: Any,
) -> Any:
    """Oxygen potential a vapour evaluation consumed.

    d-401. This is the only choice of that pressure. Live refresh and
    equilibrium-cache consumption both stamp its result.

    The provider's ``pO2_bar`` is the pressure the flux used. A
    diagnostic that omits the key uses ``dispatch_pO2_bar``, which the
    live refresh still holds from the dispatch argument. Cache
    consumption does not hold that argument: the restored diagnostic is
    the evaluation, and a missing key is passed here as a null dispatch
    so the stamp is a recorded absence. The post-bleed reservoir is not
    an argument of this function.
    """

    recorded = None
    if isinstance(diagnostic, Mapping):
        recorded = diagnostic.get("pO2_bar")
    if recorded is None:
        return dispatch_pO2_bar
    return recorded


def remember_evaluated_transport_pO2(host: Any, pressure_bar: Any) -> None:
    """Record the transport pO2 this vapour evaluation consumed.

    d-401. The activity-1 ceiling needs the oxygen potential the hour's
    flux evaluation used. Live refresh and equilibrium-cache consumption
    are the writers; both pass ``pressure_vapour_evaluation_consumed``.
    The live refresh records absence on entry, so a sub-400 K or frozen
    hour that did not dispatch keeps that absence, and overwrites it
    when the dispatch consumed a pressure. Cache consumption writes the
    restored evaluation after the hour reset has cleared the previous
    stamp. A positive finite pressure is kept. Anything else is a
    recorded absence, not a later reservoir value and not ``p_ref``.
    """

    host._evaluated_transport_pO2_bar = _positive_transport_pO2_bar(pressure_bar)
    host._evaluated_transport_pO2_recorded = True


def attach_evaluated_transport_pO2(flux: Any, host: Any) -> None:
    """Copy this hour's evaluated transport pO2 onto the interval.

    Both headspace branches refresh the oxygen reservoir after the
    bleed, and the snapshot copies that later reservoir. The ceiling
    must not read it as the pressure the flux used. When this hour did
    not evaluate, the attribute stays unset so a fixture can still name
    its own recorded pressure. A production hour that evaluated writes
    the record even when the pressure is absent.
    """

    if not getattr(host, "_evaluated_transport_pO2_recorded", False):
        return
    pressure = getattr(host, "_evaluated_transport_pO2_bar", None)
    if pressure is not None and _positive_transport_pO2_bar(pressure) is None:
        pressure = None
    setattr(
        flux,
        EVALUATED_TRANSPORT_PO2_ATTR,
        {
            "pressure_bar": pressure,
            "evidence": EVALUATED_TRANSPORT_PO2_EVIDENCE,
        },
    )


def viscous_p_bulk_out_of_domain_diagnostic(
    *,
    knudsen_number: float,
    overhead_pressure_pa: float,
    pipe_diameter_m: float,
    gas_temperature_K: float,
    carrier_gas: str = "N2",
    commanded_pressure_pa: float | None = None,
    affected_species: tuple[str, ...] = (),
    campaign_name: str | None = None,
    process_regime: str | None = None,
    asking_site: str = "evaporation.viscous_p_bulk",
    stage: str | None = None,
) -> dict[str, Any] | None:
    """Build a continuum-P_bulk validity diagnostic.

    TRANSPORT-MODEL VALIDITY DOMAIN (acceptance-matrix col 7) — NOT a Kn
    safety gate and NOT a coating gate. Returns a typed diagnostic payload
    when Kn is NaN or non-positive (invalid input), or when Kn is transitional
    and the continuum formula is load-bearing for the asking stage. Returns
    None for established in-domain states: viscous continuum, free-molecular
    flow, and true vacuum (Kn = +inf), which is a determined state rather than
    an invalid input.

    Transitional Kn at nonzero overhead is out-of-domain for viscous
    Poiseuille P_bulk. Applicability lives in
    ``simulator.transport_regime.assess_continuum_formula_validity``:
    pyrolysis extraction fail-closes (status=refused); Stage 0 bakeout
    computes and marks (status=out_of_domain). Missing campaign is
    UNKNOWN and still refuses so the pyrolysis guard cannot be dropped
    by omitting the stage. The 0.6.3 optimizer floor (~1 mbar, Kn≈0.004)
    never enters this domain; t-379 (0.7) lifts pyrolysis refusals with
    transitional/molecular conductance.
    """
    from simulator.transport_constants import (
        FREE_MOLECULAR_KNUDSEN_MIN,
        VISCOUS_KNUDSEN_MAX,
    )
    from simulator.transport_regime import (
        ContinuumValidityAction,
        assess_continuum_formula_validity,
    )

    if overhead_pressure_pa <= 0.0:
        return None
    kn = float(knudsen_number)
    # NaN and non-positive Kn are invalid input. POSITIVE INFINITY IS NOT.
    #
    # Kn = lambda / L, so Kn -> +inf is the mean free path dwarfing the pipe:
    # true vacuum, the free-molecular limit, and the operating regime this whole
    # simulator exists to model. HEAD handled it explicitly
    # (`if math.isinf(kn) or ...` under the comment "Free-molecular / true
    # vacuum"). A 2026-08-22 fix for a real fail-open -- NaN silently returning
    # None, i.e. no refusal -- widened the guard to `not math.isfinite(kn)` and
    # swept +inf in with NaN, so a true-vacuum operating point began refusing as
    # `invalid_knudsen_number`. That broke three C7 transport tests and would
    # have refused to compute in the mandate's own baseline regime.
    #
    # NaN is missing information; +inf is a determined physical state. The two
    # are both "non-finite" to float(), and only one of them is invalid.
    # Nothing further is needed for +inf below: `inf < VISCOUS_KNUDSEN_MAX` is
    # False, and `inf >= FREE_MOLECULAR_KNUDSEN_MIN` is True, so it returns None
    # through the free-molecular branch exactly as it did before.
    if math.isnan(kn) or kn <= 0.0:
        commanded_pa = (
            float(overhead_pressure_pa)
            if commanded_pressure_pa is None
            else max(0.0, float(commanded_pressure_pa))
        )
        return {
            "status": "refused",
            "authority_class": "diagnostic-limited",
            "authority_reason": "invalid_knudsen_number",
            "reason": "invalid_knudsen_number",
            "p_bulk_transport_domain": "invalid_input",
            "ledger_yields_authorized": False,
            "detail": (
                "Kn must be finite and greater than zero at nonzero "
                f"overhead pressure; received {kn!r}"
            ),
            "knudsen_number": kn if math.isfinite(kn) else None,
            "knudsen_number_input_repr": repr(kn),
            "model_domain": (
                "finite Kn > 0; true vacuum is represented by "
                "overhead_pressure_pa <= 0"
            ),
            "VISCOUS_KNUDSEN_MAX": VISCOUS_KNUDSEN_MAX,
            "FREE_MOLECULAR_KNUDSEN_MIN": FREE_MOLECULAR_KNUDSEN_MIN,
            "overhead_pressure_pa": float(overhead_pressure_pa),
            "overhead_pressure_mbar": float(overhead_pressure_pa) / 100.0,
            "commanded_pressure_pa": commanded_pa,
            "commanded_pressure_mbar": commanded_pa / 100.0,
            "pipe_diameter_m": float(pipe_diameter_m),
            "gas_temperature_K": float(gas_temperature_K),
            "gas_temperature_C": float(gas_temperature_K) - CELSIUS_TO_KELVIN_OFFSET,
            "carrier_gas": str(carrier_gas),
            "affected_species": tuple(
                sorted(str(species) for species in affected_species)
            ),
            "framing": (
                "transport_model_validity_domain"
                ";invalid_transport_input;not_coating_gate"
            ),
            "evaporation_flux_status": "not_evaluated",
            "evaporation_flux_kg_hr": None,
        }
    # MERGE 2026-08-26, CORRECTED 2026-08-27 (b-275 made the original stale).
    # Validation FIRST, then the engine's stage-scoped classifier.
    #
    # The original text here said assess_continuum_formula_validity() does NOT
    # validate its input, so a NaN would classify OK/in_domain and return None
    # with no refusal. That WAS true and was the fail-open closed on 2026-08-22,
    # but 96646e96 (b-275) then added the same validation inside assess, which
    # made this comment describe code that no longer exists. Measured at HEAD,
    # asking assess directly: NaN -> refuse, negative -> refuse, 0.0 -> ok/
    # in_domain, +inf -> ok/in_domain, 1e12 -> ok/in_domain.
    #
    # So the guard above is now belt-and-braces for NaN and negatives rather
    # than the only line of defence. It is kept because it refuses on a
    # DIFFERENT predicate than assess does -- see the Kn == 0 note below --
    # not because assess is still blind.
    #
    # Kn == 0 IS THE ONE REAL SPLIT, and it is deliberate:
    #   assess       -> in_domain. Kn = lambda/L = 0 is the perfect-continuum
    #                   limit, which is precisely where the continuum formula is
    #                   MOST valid. Refusing it would be an SC-146 refusal of a
    #                   real limit point.
    #   here         -> refused. This branch runs only at NONZERO overhead
    #                   pressure, where a real gas cannot have lambda = 0. A
    #                   zero here is an upstream computation error, not a limit.
    # The two answers differ because the questions differ: "is the formula valid
    # at this Kn" versus "is this Kn physically reachable given our overhead".
    # Written down because a FUTURE caller of assess that lacks this overhead
    # precondition would inherit the permissive answer without inheriting the
    # reason for it (milestone sweep, 2026-08-27).
    #
    # The delegate subsumes the old viscous/free-molecular early returns (both
    # are non-transitional -> OK -> None) and adds the stage scoping from
    # 644fac1a.
    assessment = assess_continuum_formula_validity(
        kn,
        campaign_name=campaign_name,
        process_regime=process_regime,
        stage=stage,
        asking_site=asking_site,
    )
    if assessment.action is ContinuumValidityAction.OK:
        return None
    commanded_pa = (
        float(overhead_pressure_pa)
        if commanded_pressure_pa is None
        else max(0.0, float(commanded_pressure_pa))
    )
    refuses = assessment.action is ContinuumValidityAction.REFUSE
    payload: dict[str, Any] = {
        "status": "refused" if refuses else "out_of_domain",
        "authority_class": (
            "diagnostic-limited" if refuses else "status_bearing"
        ),
        "authority_reason": (
            "viscous_p_bulk_transport_out_of_domain"
            if refuses
            else "continuum_formula_out_of_domain_marked"
        ),
        "reason": (
            "viscous_p_bulk_transport_out_of_domain"
            if refuses
            else "continuum_formula_out_of_domain_marked"
        ),
        "p_bulk_transport_domain": "out_of_domain_transitional",
        "ledger_yields_authorized": not refuses,
        "detail": (
            f"{assessment.detail}; t-379 (0.7) supplies transitional/"
            "molecular conductance"
        ),
        "knudsen_number": kn,
        "model_domain": (
            f"viscous Poiseuille P_bulk: Kn < {VISCOUS_KNUDSEN_MAX:g}; "
            f"free-molecular HKL upper-bound: Kn >= "
            f"{FREE_MOLECULAR_KNUDSEN_MIN:g}"
        ),
        "VISCOUS_KNUDSEN_MAX": VISCOUS_KNUDSEN_MAX,
        "FREE_MOLECULAR_KNUDSEN_MIN": FREE_MOLECULAR_KNUDSEN_MIN,
        "overhead_pressure_pa": float(overhead_pressure_pa),
        "overhead_pressure_mbar": float(overhead_pressure_pa) / 100.0,
        "commanded_pressure_pa": commanded_pa,
        "commanded_pressure_mbar": commanded_pa / 100.0,
        "pipe_diameter_m": float(pipe_diameter_m),
        "gas_temperature_K": float(gas_temperature_K),
        "gas_temperature_C": float(gas_temperature_K) - CELSIUS_TO_KELVIN_OFFSET,
        "carrier_gas": str(carrier_gas),
        "affected_species": tuple(sorted(str(species) for species in affected_species)),
        "framing": (
            "transport_model_validity_domain"
            ";not_kn_safety_gate;not_coating_gate"
        ),
        "process_regime": assessment.process_regime.value,
        "campaign_name": assessment.campaign_name,
        "stage": assessment.stage,
        "asking_site": assessment.asking_site,
        "doctrine_category": 1 if refuses else 2,
        "evaporation_flux_status": "not_evaluated" if refuses else "evaluated",
        "evaporation_flux_kg_hr": None,
    }
    if assessment.note is not None:
        payload["silent_zero_notes"] = [dict(assessment.note)]
    return payload


def refuse_viscous_p_bulk_out_of_domain(
    *,
    knudsen_number: float,
    overhead_pressure_pa: float,
    pipe_diameter_m: float,
    gas_temperature_K: float,
    carrier_gas: str = "N2",
    campaign_name: str | None = None,
    process_regime: str | None = None,
    asking_site: str = "evaporation.viscous_p_bulk",
    stage: str | None = None,
) -> None:
    """Typed refusal when ledger yields leave the viscous P_bulk domain.

    TRANSPORT-MODEL VALIDITY DOMAIN (acceptance-matrix col 7) — NOT a Kn
    safety gate and NOT a coating gate. Hard-raises when the diagnostic helper
    reports a load-bearing transitional refusal or an invalid Kn input.
    Bakeout out-of-domain marks do not raise. The authoritative provider
    returns a refused result from the same payload; bounded C7/native-Fe
    consumers apply their own explicitly tagged diagnostic-limited policy.
    """
    from simulator.transport_regime import continuum_validity_refuses

    diagnostic = viscous_p_bulk_out_of_domain_diagnostic(
        knudsen_number=knudsen_number,
        overhead_pressure_pa=overhead_pressure_pa,
        pipe_diameter_m=pipe_diameter_m,
        gas_temperature_K=gas_temperature_K,
        carrier_gas=carrier_gas,
        campaign_name=campaign_name,
        process_regime=process_regime,
        asking_site=asking_site,
        stage=stage,
    )
    if diagnostic is None or not continuum_validity_refuses(diagnostic):
        return
    reason = str(
        diagnostic.get("reason")
        or "viscous_p_bulk_transport_out_of_domain"
    )
    raise EvaporationFluxRefusal(reason, diagnostic)


# NOTE: the evaporation alpha default lives at engines/builtin/evaporation_flux.py
# (_DEFAULT_EVAPORATION_ALPHA), which is the authoritative flux path; the former
# duplicate here was dead (unused, not imported) and was removed (SC-09 / BUG-051).
_EVAPORATION_ALPHA_GROUPS = ("metals", "oxide_vapors")
def _diagnostic_names_composition_projected(
    diagnostic: Mapping[str, Any] | None,
) -> bool:
    from simulator.melt_backend.magemin import (
        diagnostics_name_composition_projected,
    )

    return diagnostics_name_composition_projected(diagnostic)


def _composition_projected_notice_key(notice: Mapping[str, Any]) -> tuple:
    rows = []
    for row in notice.get('dropped_components') or ():
        if not isinstance(row, Mapping):
            continue
        try:
            fraction = round(float(row.get('mass_fraction')), 8)
        except (TypeError, ValueError):
            fraction = None
        rows.append((str(row.get('component')), fraction))
    return tuple(rows)


_FREEZE_GATE_ACCOUNT = 'process.cleaned_melt'
_FREEZE_GATE_EPSILON = 1.0e-12
# Typed refusal when a supplied fraction at the declared liquidus lies
# below the finder's liquid band. The epsilon itself lives in liquidus.py.
FREEZE_GATE_LIQUIDUS_FRACTION_INCONSISTENT = (
    'freeze_gate_liquidus_fraction_inconsistent'
)


def _liquidus_fraction_inconsistent_since(
    reasons: list[str],
    start: int,
) -> bool:
    token = FREEZE_GATE_LIQUIDUS_FRACTION_INCONSISTENT
    return any(token in reason for reason in reasons[start:])


# The validation map's smallest adjacent Na-dose step changes the FeO mole
# fraction by 0.00105. A 0.0001 quantum leaves more than ten bins across that
# physical delta while remaining well above floating-point composition jitter.
_FREEZE_GATE_FRACTION_QUANTUM = 0.0001
# MAGEMin on the same cleaned lunar-mare melt projected to the ig basis gave
# dry solidus/liquidus 918.75/1370.3125 C. At 0.004 mol H2O per 100 mol oxides
# (water fraction 3.99984e-5), its partial out_of_domain curve changed by up
# to 0.022144 in the shared 400-1050 C samples; at 900 C it was 0.005215 vs
# 0 dry. At 0.1/100 (fraction 9.99001e-4), the valid curve gave
# 770.3125/1370.3125 C and max |delta fraction|=0.096407. Using a 1e-4
# liquid-fraction comparison tolerance, the low-water result implies
# q <= 1e-4 * 3.99984e-5 / 0.022144 = 1.8e-7; q=1e-7 estimates a fraction
# change of 5.6e-5. Scaling the 0.1/100 solidus response to q gives 0.015 C,
# below MAGEMin's 2 C search tolerance.
_FREEZE_GATE_WATER_FRACTION_QUANTUM = 1e-7
# Formula counts are the provider's identity input; ignore count noise smaller
# than 1e-12 atom. The reported 3.5e-17 kg/mol mass jitter is about 2.2e-15
# oxygen atoms, while a real stoichiometric change is many orders larger.
_FREEZE_GATE_FORMULA_ATOM_COUNT_QUANTUM = 1e-12
_FREEZE_GATE_PRESSURE_BAR_QUANTUM = 0.01
_FREEZE_GATE_FO2_LOG_QUANTUM = 1.0
_FREEZE_GATE_FO2_LOG_BOUND = 30.0
_FREEZE_GATE_TRACE_FRACTION_CUTOFF = 0.001
_RESOLVE_EVAPORATION_GATE_AUTHORITY = object()
_PARTIAL_MELT_OFFGASSING_COMPONENTS = {
    'Na': {
        'parent_oxide': 'Na2O',
        'component': 'NaO0.5',
        'partition_coefficient': 0.10,
        'partition_coefficient_range': (0.05, 0.20),
    },
    'K': {
        'parent_oxide': 'K2O',
        'component': 'KO0.5',
        'partition_coefficient': 0.05,
        'partition_coefficient_range': (0.0, 0.10),
    },
}
_FREEZE_GATE_COMPOSITION_SPECIES = frozenset((
    'SiO2',
    'Al2O3',
    'FeO',
    'Fe2O3',
    'MgO',
    'CaO',
    'Na2O',
    'K2O',
    'TiO2',
    'Cr2O3',
    'MnO',
    'P2O5',
    'NiO',
    'CoO',
))


class FreezeGateCurveCacheKey(NamedTuple):
    schema: str
    pressure_bar: float
    fO2_log: float
    composition_mol_fractions: tuple[tuple[str, float], ...]
    water_mol_fraction: float
    formula_registry_digest: str
    inert_species: tuple[str, ...]
    redox_state_identity: tuple[tuple[str, Any], ...] | None


class _FlaggedFreezeGateCurveCacheKey(NamedTuple):
    schema: str
    pressure_bar: float
    fO2_log: float
    composition_mol_fractions: tuple[tuple[str, float], ...]
    water_mol_fraction: float
    formula_registry_digest: str
    inert_species: tuple[str, ...]
    magemin_domain_identity: MAGEMinDomainIdentity
    redox_state_identity: tuple[tuple[str, Any], ...] | None


class _SilicateProviderMeltInputs(NamedTuple):
    composition_mol: dict[str, float]
    inert_species: tuple[str, ...]
    formula_identity: tuple[
        tuple[str, tuple[tuple[str, float], ...]], ...
    ]
    amount_kg: float


def _freeze_gate_notice_value(value: Any) -> float | str | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return number if math.isfinite(number) else str(value)


# Lower authority when a projected bulk has no usable solidus/liquidus.
# The projection is not this source: the calibration floor is.
_KRESS91_LIQUID_CALIBRATION_FLOOR_SOURCE = (
    'kress91_liquid_calibration_floor'
)
# Curve schema requires solidus < liquidus. The floor is a step at the
# calibration minimum (liquid strictly above it). This gap is below any
# recipe temperature quantum, so interpolation matches
# temperature_C > KRESS91_LIQUID_CALIBRATION_MIN_T_C.
_KRESS91_FLOOR_OPEN_SIDE_C = 1.0e-6


def _assert_runtime_alpha_source_not_vaporock(
    species: str, alpha_data: Mapping[str, Any]
) -> None:
    """Production evaporation gate: refuse VapoRock-fit alpha provenance.

    Complements catalog compile denial. Legacy compatibility views still load
    alphas outside a re-compile; this path is the second SC-50 consumer so a
    VapoRock source cannot reach HKL flux silently.
    """

    from simulator.vapour_rail.kinetics_anchors import (
        KineticsAnchorError,
        assert_alpha_source_not_vaporock,
    )

    sources: list[Any] = []
    # source_note is a supported provenance shape (kinetics_anchors loader);
    # omitting it lets VapoRock-fit alphas bypass the gate via
    # {value, source_note} contracts.
    for key in ("source", "provenance", "citation", "source_note"):
        if key in alpha_data and alpha_data.get(key) is not None:
            sources.append(alpha_data.get(key))
    value = alpha_data.get("value")
    if isinstance(value, Mapping):
        for key in ("cite", "source", "provenance", "source_note"):
            if key in value and value.get(key) is not None:
                sources.append(value.get(key))
    for source in sources:
        try:
            assert_alpha_source_not_vaporock(str(source))
        except KineticsAnchorError as exc:
            raise ValueError(
                f"evaporation alpha for {species!r} refused VapoRock provenance: {exc}"
            ) from exc


def _load_evaporation_alpha_by_species(vapor_pressure_data: dict) -> dict[str, Any]:
    """Load alpha specs, retaining tagged proxy provenance and dormant zeros."""

    from simulator.evaporation_classes import (
        BROAD_PROXY_NOT_INTRINSIC_TAG,
        is_broad_proxy_not_intrinsic_row,
    )
    from simulator.vapour_rail.catalog import vapor_pressure_legacy_view

    vapor_pressure_data = vapor_pressure_legacy_view(vapor_pressure_data)
    alpha_by_species: dict[str, Any] = {}
    for group_name in _EVAPORATION_ALPHA_GROUPS:
        group = vapor_pressure_data.get(group_name, {}) or {}
        for species, species_data in group.items():
            if not isinstance(species_data, dict):
                continue
            if "evaporation_alpha" not in species_data:
                continue
            alpha_data = species_data.get("evaporation_alpha") or {}
            if isinstance(alpha_data, Mapping):
                _assert_runtime_alpha_source_not_vaporock(str(species), alpha_data)
            value = parse_alpha_contract(alpha_data)
            if value is None:
                continue
            if species_data.get("flux_dormant") is True:
                alpha_by_species[species] = 0.0
                continue
            if is_broad_proxy_not_intrinsic_row(alpha_data):
                payload = (
                    dict(value)
                    if isinstance(value, Mapping)
                    else {"form": "scalar", "value": value}
                )
                payload["alpha_proxy_tag"] = BROAD_PROXY_NOT_INTRINSIC_TAG
                payload[ALPHA_AUTHORITY_STATUS_FIELD] = PROXY_BRIDGE_ALPHA_STATUS
                payload["authority_reason"] = BROAD_PROXY_NOT_INTRINSIC_TAG
                if alpha_data.get("tier") is not None:
                    payload["alpha_proxy_tier"] = alpha_data.get("tier")
                if alpha_data.get("source"):
                    payload["source"] = str(alpha_data.get("source"))
                alpha_by_species[species] = payload
                continue
            alpha_by_species[species] = value
    return alpha_by_species


def _load_hkl_upper_bound_transport_species(
    vapor_pressure_data: dict,
) -> tuple[str, ...]:
    """Return carriers explicitly modelled by the HKL kinetic upper limit."""

    from simulator.vapour_rail.catalog import vapor_pressure_legacy_view

    legacy = vapor_pressure_legacy_view(vapor_pressure_data)
    species_ids: set[str] = set()
    for group_name in _EVAPORATION_ALPHA_GROUPS:
        group = legacy.get(group_name, {}) or {}
        for species, species_data in group.items():
            if not isinstance(species_data, Mapping):
                continue
            if species_data.get("gas_transport_model") == (
                "hkl_kinetic_upper_bound_no_chapman_enskog"
            ):
                species_ids.add(str(species))
    return tuple(sorted(species_ids))


def _legacy_evaporation_shadow_pressure_map(
    vapor_pressure_data: dict,
    live_pressures_Pa: Mapping[str, float] | None,
    *,
    read_context: str | None = None,
    unresolved_species: set[str] | None = None,
) -> dict[str, float]:
    """Keep finite backend pressures, including carriers with unknown alpha.

    Alpha authority belongs to the flux provider: a missing coefficient must
    reach its explicitly flagged HKL upper-bound path.
    """

    if unresolved_species is None:
        return finite_live_pressure_map(
            live_pressures_Pa,
            read_context=read_context,
        )
    if read_context is None:
        finite, dropped = _finite_live_map(live_pressures_Pa)
    else:
        with compatibility_pressure_read_context(read_context):
            finite, dropped = _finite_live_map(live_pressures_Pa)
    unresolved_species.update(dropped)
    return finite


def _evaporation_legacy_shadow_pressure_map(
    vapor_pressure_data: dict,
    vapor_pressure_diagnostic: Mapping[str, Any] | None,
) -> dict[str, float] | None:
    """Return the independent legacy shadow, or absent comparison evidence.

    The diagnostic retains the pre-refresh backend projection, computed before
    catalog batch selection. Missing evidence stays typed as absent; it is
    never replaced with the runtime operand.
    """

    diagnostic = dict(vapor_pressure_diagnostic or {})
    backend_source = diagnostic.get("backend_vapor_pressures_Pa")
    if not isinstance(backend_source, Mapping):
        return None
    return _legacy_evaporation_shadow_pressure_map(
        vapor_pressure_data,
        backend_source,
        read_context="shadow_comparison",
        unresolved_species=set(),
    )


def _pre_rg_effective_pressure_source(
    vapor_pressure_data: dict,
    equilibrium: Any,
    *,
    unresolved_species: set[str] | None = None,
) -> EffectivePressureSource:
    """Return the sole typed pre-RG handoff for flux-driving pressure values.

    The catalog can now evaluate activity-corrected ``P_eff`` for melt species,
    but VR-13/RG-1 has not authorized a flux-value cutover. The verifier's
    1873.15 K baseline exposed the former pure/reference-vs-effective gap:
    Ca 8.35e5/4.6e-9 Pa, Mg 6.87e5/7.3e-3 Pa, K 4.51e6/0.469 Pa, and
    Al 110.2/1.4e-8 Pa. This factory therefore remains the sole pre-RG handoff
    and continues to return equilibrium-backend effective pressures.

    TODO(VR-13 / RG-1): switch this one factory only after owner authorization
    and the remaining DESIGN-REV5 §7.4 cutover preconditions are green.
    """

    shadow_kwargs = {'read_context': EFFECTIVE_PRESSURE_SOURCE_CONTEXT}
    if unresolved_species is not None:
        shadow_kwargs['unresolved_species'] = unresolved_species
    effective_pressures = _legacy_evaporation_shadow_pressure_map(
        vapor_pressure_data,
        getattr(equilibrium, 'vapor_pressures_Pa', {}),
        **shadow_kwargs,
    )
    temperature_C = float(getattr(equilibrium, 'temperature_C', math.inf))
    physical_zero_reason = None
    if (
        not effective_pressures
        and not unresolved_species
        and temperature_C < PRE_RG_EFFECTIVE_PRESSURE_CHEAP_ZERO_BELOW_C
    ):
        physical_zero_reason = 'pre_rg_backend_below_effective_pressure_threshold'
    return EffectivePressureSource(
        PRE_RG_EFFECTIVE_PRESSURE_SOURCE_ID,
        effective_pressures,
        physical_zero_reason=physical_zero_reason,
    )


def _evaporation_vapor_row(
    vapor_pressure_data: Mapping[str, Any] | None,
    species: str,
) -> Mapping[str, Any] | None:
    """Read the one legacy-projected vapor row used by evaporation owners."""
    if not isinstance(vapor_pressure_data, Mapping):
        return None
    row = (vapor_pressure_data.get('metals', {}) or {}).get(species, {})
    if not row:
        row = (vapor_pressure_data.get('oxide_vapors', {}) or {}).get(
            species, {}
        )
    if not isinstance(row, Mapping):
        return None
    return row


def _transport_parameters_estimated_notice(
    *,
    species: str,
    hour: int | None,
    parameters: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the owner-selected estimate notice with its rung and inputs."""
    return {
        'kind': 'transport_parameters_estimated',
        'species': str(species),
        'hour': hour,
        'method': parameters.get('method'),
        'flag': parameters.get('flag'),
        'inputs': dict(parameters.get('inputs', {}) or {}),
        'sigma_angstrom': parameters.get('sigma_angstrom'),
        'epsilon_over_k_K': parameters.get('epsilon_over_k_K'),
    }


def _publish_transport_parameter_notices(
    overlay: dict[str, Any],
    transport_sidecar: Mapping[str, Any],
) -> None:
    """Publish estimated-carrier notices from the separate transport channel."""
    notices_by_species = transport_sidecar.get(
        'transport_parameter_notices_by_species', {}
    )
    if not isinstance(notices_by_species, Mapping):
        return
    notices = overlay.setdefault('notices', [])
    for notice in notices_by_species.values():
        if isinstance(notice, Mapping) and dict(notice) not in notices:
            notices.append(dict(notice))


def _zero_liquid_precedes_interpolation(equilibrium: Any) -> bool:
    """Share the physical-zero precedence used before cache-flux admission."""
    liquid_fraction = getattr(equilibrium, 'liquid_fraction', None)
    return (
        liquid_fraction is not None
        and legacy_raw_liquid_fraction_is_zero(liquid_fraction)
    )


def _is_interpolated_vapour_equilibrium(
    equilibrium: Any,
    vapor_pressure_diagnostic: Mapping[str, Any] | None,
) -> bool:
    """Check cache provenance before any equilibrium vapour input is read."""
    from simulator.reduced_real_cache_interpolation import (
        is_interpolated_equilibrium,
    )

    return is_interpolated_equilibrium(
        equilibrium,
        vapor_pressure_diagnostic=vapor_pressure_diagnostic,
    )


def _append_vapour_batch_unresolved_notice(
    host: Any,
    overlay: dict[str, Any],
    *,
    species: str | None,
    reason: str,
) -> None:
    """Publish an unresolved species as unavailable on the flux overlay."""
    raw_hour = getattr(getattr(host, "melt", None), "hour", None)
    try:
        hour = int(raw_hour)
    except (TypeError, ValueError, OverflowError):
        hour = None
    notices = overlay.setdefault("notices", [])
    notice = {
        "kind": "vapour_batch_unresolved",
        "species": str(species) if species is not None else None,
        "hour": hour,
        "reason": str(reason),
    }
    if any(
        isinstance(existing, Mapping)
        and existing.get("kind") == "vapour_batch_unresolved"
        and existing.get("species") == notice["species"]
        for existing in notices
    ):
        return
    if notice not in notices:
        notices.append(notice)


def _provider_resolved_flux_species(
    diagnostic: Mapping[str, Any],
    flux_overlay: Mapping[str, Any] | None = None,
    *,
    flux_pressures: Mapping[str, Any] | None = None,
) -> frozenset[str]:
    """Return computed provider channels plus resolved selector outcomes."""
    below = diagnostic.get('below_threshold_evaporation_flux', {})
    below_rates = below.get('fluxes_kg_hr', {}) if isinstance(below, Mapping) else {}
    resolved: set[str] = set()
    for key in (
        'evaporation_flux_kg_hr',
        'zero_net_evaporation_flux',
        'evaporation_series_resistance',
    ):
        channels = diagnostic.get(key, {})
        if isinstance(channels, Mapping):
            resolved.update(str(species) for species in channels)
    if isinstance(below_rates, Mapping):
        resolved.update(str(species) for species in below_rates)
    channel_states = (flux_overlay or {}).get('batch_channel_states', {})
    unavailable_species = {
        str(notice.get('species'))
        for notice in (flux_overlay or {}).get('notices', ()) or ()
        if isinstance(notice, Mapping)
        and notice.get('kind') == 'vapour_batch_unresolved'
        and notice.get('species') is not None
    }
    resolved.difference_update(unavailable_species)
    if isinstance(channel_states, Mapping):
        resolved.update(
            str(species)
            for species, state in channel_states.items()
            if state == 'zero_by_physics'
            and str(species) not in unavailable_species
        )
    if isinstance(flux_pressures, Mapping) and isinstance(channel_states, Mapping):
        resolved.update(
            str(species)
            for species, pressure in flux_pressures.items()
            if str(species) not in unavailable_species
            and channel_states.get(species) == 'eligible'
            and isinstance(pressure, Real)
            and not isinstance(pressure, bool)
            and math.isfinite(float(pressure))
            and float(pressure) > 0.0
        )
    return frozenset(resolved)


def _record_unavailable_provider_flux_channels(
    host: Any,
    diagnostic: Mapping[str, Any],
    overlay: dict[str, Any],
    *,
    expected_species: Iterable[str],
    flux: Any = None,
    provider_status: Any = None,
) -> dict[str, str]:
    """Record explicit species-local provider failures as unavailable."""
    missing_transport = diagnostic.get('missing_transport_parameters', {})
    missing_molar_mass = diagnostic.get('missing_molar_mass', {})
    missing_alpha = diagnostic.get('missing_alpha', {})
    missing_transport = (
        missing_transport if isinstance(missing_transport, Mapping) else {}
    )
    missing_molar_mass = (
        missing_molar_mass if isinstance(missing_molar_mass, Mapping) else {}
    )
    missing_alpha = missing_alpha if isinstance(missing_alpha, Mapping) else {}
    computed_species = _provider_resolved_flux_species(diagnostic, overlay)
    unavailable: dict[str, str] = {}
    candidates = {str(species) for species in expected_species}
    provider_status = str(provider_status or '').strip().lower()
    refusal_species = diagnostic.get('species')
    candidates.update(str(species) for species in missing_transport)
    candidates.update(str(species) for species in missing_molar_mass)
    candidates.update(str(species) for species in missing_alpha)
    if refusal_species is not None:
        candidates.add(str(refusal_species))
    for species_id in sorted(candidates - computed_species):
        refusal = missing_transport.get(species_id)
        mass_failure = missing_molar_mass.get(species_id)
        alpha_failure = missing_alpha.get(species_id)
        if isinstance(refusal, Mapping) and refusal.get('status') == 'unavailable':
            reason = str(refusal.get('reason') or 'transport input unavailable')
            refusal_code = str(
                refusal.get('refusal_type') or 'transport input unavailable'
            )
            extra = dict(refusal)
        elif isinstance(mass_failure, Mapping):
            reason = 'missing_molar_mass'
            refusal_code = reason
            extra = dict(mass_failure)
        elif isinstance(alpha_failure, Mapping):
            reason = str(alpha_failure.get('reason') or 'missing_alpha')
            refusal_code = reason
            extra = dict(alpha_failure)
        elif provider_status in {'refused', 'unavailable'}:
            if refusal_species is None or str(refusal_species) != species_id:
                continue
            reason = str(
                diagnostic.get('reason') or f'provider_{provider_status}'
            )
            refusal_code = str(diagnostic.get('refusal_type') or reason)
            extra = dict(diagnostic)
        else:
            # Green treats a status-ok omission with no typed species-local
            # refusal as no debit. In particular, the selected channel is not
            # made unavailable and cannot trigger a late retry from silence.
            continue
        unavailable[species_id] = reason
        if flux is not None:
            authorities = getattr(flux, 'carrier_authority_by_species', None)
            if isinstance(authorities, dict):
                channel = dict(authorities.get(species_id, {}) or {})
                existing_extra = channel.get('extra')
                channel_extra = (
                    dict(existing_extra)
                    if isinstance(existing_extra, Mapping)
                    else {}
                )
                channel_extra.update(extra)
                channel.update({
                    'species_id': species_id,
                    'flux': {
                        'kind': 'refusal',
                        'code': refusal_code,
                        'detail': reason,
                    },
                    'refusal_code': refusal_code,
                    'is_refused': True,
                    'reason': reason,
                    'authority_level': 'unavailable',
                    'extra': channel_extra,
                })
                authorities[species_id] = channel
        _append_vapour_batch_unresolved_notice(
            host,
            overlay,
            species=species_id,
            reason=reason,
        )
    return unavailable


def _transport_only_unavailable_result(
    status: Any,
    diagnostic: Mapping[str, Any],
) -> bool:
    """Recognize the provider's species-local transport-only outcome."""
    missing = diagnostic.get('missing_transport_parameters', {})
    return (
        str(status) == 'unavailable'
        and isinstance(missing, Mapping)
        and bool(missing)
        and not any(
            key in diagnostic
            for key in ('missing_alpha', 'missing_molar_mass', 'reason')
        )
        and all(
            isinstance(refusal, Mapping)
            and refusal.get('policy') == 'fail_loud_missing_transport_parameters'
            and refusal.get('status') == 'unavailable'
            and bool(refusal.get('refusal_type'))
            for refusal in missing.values()
        )
    )


def _provider_flux_outcome_refusal(
    host: Any,
    status: Any,
    diagnostic: Mapping[str, Any],
    provider_unavailable: Mapping[str, str],
    flux_overlay: dict[str, Any],
    *,
    selector_overlay: Mapping[str, Any] | None = None,
) -> EvaporationFluxRefusal | None:
    """Classify provider status and resolved channels for live and capacity."""
    if str(status) != 'ok' and not _transport_only_unavailable_result(
        status,
        diagnostic,
    ):
        reason = str(diagnostic.get('reason') or '').strip()
        if 'missing_transport_parameters' in diagnostic and not reason:
            missing = ', '.join(
                sorted(diagnostic['missing_transport_parameters'])
            )
            reason = (
                'missing Chapman-Enskog transport parameters for sampled '
                f'species: {missing}'
            )
        if not reason:
            reason = (
                f'evaporation_flux_{status}'
                if status
                else 'evaporation_flux_refused'
            )
        if not provider_unavailable and diagnostic.get('species') is None:
            return EvaporationFluxRefusal(
                reason,
                {
                    **dict(diagnostic),
                    'reason': reason,
                    'evaporation_flux_kg_hr': {},
                },
            )
        outcome = EvaporationMixin._vapour_batch_resolution_outcome(
            host,
            {**diagnostic, 'reason': reason},
            flux_overlay,
            selector_overlay=selector_overlay,
            provider_diagnostic=diagnostic,
            include_unresolved_notices=True,
        )
        refusal = outcome.refusal
        if refusal is not None:
            refusal.diagnostic.update(diagnostic)
            refusal.diagnostic['reason'] = refusal.reason
        return refusal
    unresolved_notice = any(
        isinstance(notice, Mapping)
        and notice.get('kind') == 'vapour_batch_unresolved'
        for notice in flux_overlay.get('notices', ()) or ()
    )
    if provider_unavailable or unresolved_notice:
        outcome = EvaporationMixin._vapour_batch_resolution_outcome(
            host,
            {},
            flux_overlay,
            selector_overlay=selector_overlay,
            provider_diagnostic=diagnostic,
            include_unresolved_notices=True,
        )
        refusal = outcome.refusal
        if refusal is not None:
            refusal.diagnostic.update(diagnostic)
            refusal.diagnostic['reason'] = refusal.reason
        return refusal
    return None


def _take_vapour_batch_selector_overlay(
    host: Any,
    fallback_overlay: Mapping[str, Any],
) -> dict[str, Any]:
    """Return selector evidence without extending the green six-value seam."""
    selector_overlay = getattr(
        host, '_last_vapour_batch_selector_overlay', None
    )
    host._last_vapour_batch_selector_overlay = None
    source = (
        selector_overlay
        if isinstance(selector_overlay, Mapping)
        else fallback_overlay
    )
    return copy.deepcopy(dict(source or {}))


def _take_vapour_batch_terminal_outcome(
    host: Any,
) -> _VapourBatchResolutionOutcome | None:
    """Consume the terminal decision made with the initial closure state."""
    outcome = getattr(host, '_last_vapour_batch_terminal_outcome', None)
    host._last_vapour_batch_terminal_outcome = None
    return (
        outcome
        if isinstance(outcome, _VapourBatchResolutionOutcome)
        else None
    )


def _append_capacity_solve_exclusion_notice(
    host: Any,
    overlay: dict[str, Any],
    *,
    resolved_species: Iterable[str],
) -> dict[str, Any] | None:
    """Disclose excluded species and the finite-capacity bound directions."""
    resolved_species = frozenset(resolved_species)
    notices = overlay.get('notices')
    if isinstance(notices, list):
        notices[:] = [
            notice for notice in notices
            if not (
                isinstance(notice, Mapping)
                and notice.get('kind')
                == 'capacity_solve_excludes_unavailable_species'
            )
        ]
    if not resolved_species:
        return None
    unavailable = sorted(
        {
            (str(notice.get('species')), str(notice.get('reason') or 'unavailable'))
            for notice in overlay.get('notices', ()) or ()
            if isinstance(notice, Mapping)
            and notice.get('kind') == 'vapour_batch_unresolved'
            and notice.get('species') is not None
            and str(notice.get('species')) not in resolved_species
        }
    )
    if not unavailable:
        return None
    raw_hour = getattr(getattr(host, 'melt', None), 'hour', None)
    try:
        hour = int(raw_hour)
    except (TypeError, ValueError, OverflowError):
        hour = None
    flag = {
        'kind': 'capacity_solve_excludes_unavailable_species',
        'hour': hour,
        'excluded_species': [
            {'species': species, 'reason': reason}
            for species, reason in unavailable
        ],
        'bound_direction': {
            'resolved_fluxes': 'upper',
            'headspace_pressure': 'lower',
        },
    }
    notices = overlay.setdefault('notices', [])
    notices.append(flag)
    return flag


@dataclass(frozen=True)
class _CapacityProviderTrialDecision:
    unavailable_by_species: Mapping[str, str]
    resolved_species: frozenset[str]
    bound_notice: Mapping[str, Any] | None
    refusal: EvaporationFluxRefusal | None


def _capacity_zero_load_evidence(
    *,
    zero_liquid: bool = False,
    outcome_reason: Any = None,
    temperature_K: float | None = None,
    candidate: Any = None,
) -> str | None:
    """Own the typed evidence that permits an empty capacity dispatch."""
    allowed_reasons = {
        'zero_liquid',
        'below_400K',
        'internal_analytical_below_400K',
        'proven_empty_inventory',
    }
    if candidate is not None:
        value = str(candidate)
        return value if value in allowed_reasons else None
    if zero_liquid:
        return 'zero_liquid'
    if outcome_reason in allowed_reasons - {'zero_liquid'}:
        return str(outcome_reason)
    if temperature_K is not None and temperature_K < 400.0:
        return 'below_400K'
    return None


def _capacity_provider_trial_decision(
    host: Any,
    status: Any,
    diagnostic: Mapping[str, Any],
    overlay: dict[str, Any],
    *,
    expected_species: Iterable[str],
    selector_overlay: Mapping[str, Any],
    zero_load_evidence: str | None = None,
) -> _CapacityProviderTrialDecision:
    """Own post-dispatch availability, resolved evidence, bound, and refusal."""
    unavailable = _record_unavailable_provider_flux_channels(
        host,
        diagnostic,
        overlay,
        expected_species=expected_species,
        provider_status=status,
    )
    resolved = _provider_resolved_flux_species(diagnostic, overlay)
    bound = _append_capacity_solve_exclusion_notice(
        host,
        overlay,
        resolved_species=resolved,
    )
    refusal = _provider_flux_outcome_refusal(
        host,
        status,
        diagnostic,
        unavailable,
        overlay,
        selector_overlay=selector_overlay,
    )
    excluded_notice = any(
        isinstance(notice, Mapping)
        and notice.get('kind') == 'vapour_batch_unresolved'
        and notice.get('species') is not None
        for notice in overlay.get('notices', ()) or ()
    )
    typed_empty_evidence = _capacity_zero_load_evidence(
        candidate=zero_load_evidence
    )
    if (
        refusal is None
        and not resolved
        and not (
            typed_empty_evidence is not None
            and not unavailable
            and not excluded_notice
        )
    ):
        outcome = EvaporationMixin._vapour_batch_resolution_outcome(
            host,
            {},
            overlay,
            selector_overlay=selector_overlay,
            provider_diagnostic=diagnostic,
            include_unresolved_notices=True,
        )
        refusal = outcome.refusal
    return _CapacityProviderTrialDecision(
        unavailable_by_species=dict(unavailable),
        resolved_species=resolved,
        bound_notice=bound,
        refusal=refusal,
    )


def _load_evaporation_alpha_envelope_by_species(
    vapor_pressure_data: dict,
) -> dict[str, tuple[float, float]]:
    """Load per-species alpha envelopes for flux uncertainty diagnostics."""

    from simulator.vapour_rail.catalog import vapor_pressure_legacy_view

    vapor_pressure_data = vapor_pressure_legacy_view(vapor_pressure_data)
    envelope_by_species: dict[str, tuple[float, float]] = {}
    for group_name in _EVAPORATION_ALPHA_GROUPS:
        group = vapor_pressure_data.get(group_name, {}) or {}
        for species, species_data in group.items():
            if not isinstance(species_data, dict):
                continue
            alpha_data = species_data.get("evaporation_alpha") or {}
            if not isinstance(alpha_data, dict):
                continue
            envelope = alpha_data.get("envelope") or ()
            if not isinstance(envelope, (list, tuple)) or len(envelope) != 2:
                continue
            envelope_by_species[species] = (
                float(envelope[0]),
                float(envelope[1]),
            )
    return envelope_by_species


def gate_dispatch_curve_notices(
    diagnostic: Mapping[str, Any],
) -> dict[str, Any]:
    """Notices a single gate dispatch publishes on its own curve.

    Module-level: the notices depend only on the dispatch diagnostic, not on
    simulator state, so callers (including test stand-ins for the mixin) need
    no bound method.
    """

    notices: dict[str, Any] = {}
    fallback_provider = diagnostic.get('kernel_fallback_used')
    if fallback_provider == 'alphamelts-diagnostic':
        # Step 3 supplies measured-bracket validation. Until then this
        # diagnostic prediction must not earn the primary MAGEMin
        # binding's cache admission through the shared fallback slot.
        notices['calibration_status'] = 'pending_validation'
    step_down = diagnostic.get('gate_provider_step_down')
    if step_down:
        notices['gate_provider_step_down'] = step_down
    backend_diagnostic = diagnostic.get('backend_diagnostics') or {}
    if 'condensed_phase_reference_pressure_bar' not in backend_diagnostic:
        for attempt in (step_down or {}).get('failed_providers', ()):
            details = attempt['diagnostic'].get('backend_diagnostics') or {}
            if 'condensed_phase_reference_pressure_bar' in details:
                backend_diagnostic = details
    if 'condensed_phase_reference_pressure_bar' in backend_diagnostic:
        notices['alphamelts_reference_pressure'] = {
            key: backend_diagnostic[key] for key in (
                'physical_overhead_pressure_bar',
                'condensed_phase_reference_pressure_bar',
            )
        }
        for key in (
            'solidus_bracket_C', 'liquidus_bracket_C', 'numerical_path_warning',
            'commissioning_notice', 'authority', 'certified_band',
        ):
            if key in backend_diagnostic:
                notices['alphamelts_reference_pressure'][key] = (
                    backend_diagnostic[key]
                )
    return notices

class EvaporationMixin:
    def _evaporation_bulk_partial_pressure_pa(self, species: str) -> float:
        """Return upstream melt-headspace backpressure for evaporation."""

        melt_headspace_partials_mbar = getattr(
            self, '_melt_headspace_composition_mbar', {}) or {}
        return float(melt_headspace_partials_mbar.get(species, 0.0)) * 100.0

    def _langmuir_headspace_partials_Pa(
        self,
        species_ids: Mapping[str, Any] | Any,
    ) -> dict[str, float]:
        """Every positive headspace partial the film may sum, in Pa.

        The persisted composition is the vector. ``species_ids`` (the
        vapor map) is not an allowlist: a carrier absent from it, such
        as N2, still sets the realized total. Those keys are extra
        reader queries, so a reader that does not store partials on the
        composition map still answers for them. O2 is queried too, so a
        reader that stores the overhead bound outside that map still
        supplies it. A missing reader is an empty headspace, not a zero
        substituted for a measurement the caller withheld.

        d-376 round 6. Kn and diffusivity use one pressure,
        ``max(sum of this vector, commanded melt pressure)``.
        Intersecting with the vapor map and then adding O2 left a
        commanded total of 0 on vacuum transport under 1000 Pa of
        persisted N2.
        """

        reader = getattr(self, "_evaporation_bulk_partial_pressure_pa", None)
        if not callable(reader):
            return {}
        composition = getattr(self, "_melt_headspace_composition_mbar", None) or {}
        names: list[Any] = []
        if isinstance(composition, Mapping):
            names.extend(composition)
        # Extra queries, not a filter. Dropping any name that is absent
        # from this list was the vacuum-transport hole.
        names.extend(species_ids)
        if not any(str(species) == "O2" for species in names):
            names.append("O2")
        partials: dict[str, float] = {}
        seen: set[str] = set()
        for species in names:
            label = str(species)
            if label in seen:
                continue
            seen.add(label)
            try:
                value = float(reader(species))
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and value > 0.0:
                partials[label] = value
        return partials

    def _langmuir_film(
        self,
        partials_Pa: Mapping[str, float] | None,
    ) -> tuple[dict[str, float], float]:
        """The one partial vector and transport pressure for an evaluation.

        Positive finite partials only. A non-positive entry is not a
        bulk and does not enter the sum. This method does not intersect
        the vector with the vapor map: every positive key, including a
        non-vapor carrier, is in the sum. The transport pressure is the
        flux provider's ``max(partial sum, commanded melt pressure)``
        when that reader exists, and the partial sum when it does not.
        That one pressure is what Kn and diffusivity use. The surface
        root and the flux kernel both consume this pair. Neither sums
        a second vector.
        """

        cleaned: dict[str, float] = {}
        for species, value in dict(partials_Pa or {}).items():
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number) and number > 0.0:
                cleaned[str(species)] = number
        reader = getattr(self, "_evaporation_overhead_total_pressure_Pa", None)
        if callable(reader):
            pressure = float(reader(cleaned))
        else:
            pressure = sum(cleaned.values())
        return cleaned, pressure

    def _evaporation_gas_resistance_enabled(self) -> bool:
        """Resolve the publisher film switch for classification and closure."""
        from engines.builtin.evaporation_flux import (
            resolve_gas_resistance_enabled,
        )

        kernel_config = dict(
            (getattr(self, "setpoints", {}) or {}).get("chemistry_kernel", {})
            or {}
        )
        series_config = dict(
            kernel_config.get("evaporation_series_resistance", {}) or {}
        )
        return resolve_gas_resistance_enabled(series_config, {})

    def _evaporation_carrier_gas(self) -> str:
        """Use the publisher's carrier resolver and its legacy N2 fallback."""
        resolver = getattr(self, '_resolve_condensation_carrier_gas', None)
        if not callable(resolver):
            return 'N2'
        return resolver()

    def _langmuir_transport(
        self,
        partials_Pa: Mapping[str, float],
        temperature_K: float,
        overhead_pressure_Pa: float,
    ) -> Any:
        """Series-resistance knobs for one already-filmed pressure.

        ``overhead_pressure_Pa`` is the value ``_langmuir_film`` returned
        for these partials. This method does not sum them again.
        """

        from simulator.condensation import _knudsen_number
        from simulator.langmuir_surface_oxygen import LangmuirTransport
        from simulator.transport_regime import continuum_validity_refuses

        # Green's publisher film policy, the same parent the flux
        # controls will have. Nested ``evaporation_series_resistance``
        # wins; otherwise the film is on. The top-level kernel flag is
        # not that parent and is not copied onto the controls: a
        # buffered in-band melt keeps green's film. b-906 round 7.
        gas_resistance_enabled = (
            EvaporationMixin._evaporation_gas_resistance_enabled(self)
        )
        overhead_model = getattr(self, "overhead_model", None)
        try:
            pipe_diameter_m = float(
                getattr(overhead_model, "pipe_diameter_m", 0.12) or 0.12
            )
        except (TypeError, ValueError):
            pipe_diameter_m = 0.12
        overhead = getattr(self, "overhead", None)
        try:
            gas_temperature_K = float(
                getattr(overhead, "headspace_temperature_K", 0.0) or 0.0
            )
        except (TypeError, ValueError):
            gas_temperature_K = 0.0
        if not math.isfinite(gas_temperature_K) or gas_temperature_K <= 0.0:
            gas_temperature_K = float(temperature_K)
        stir = getattr(getattr(self, "melt", None), "stir_state", None)
        try:
            axial = clamp_stir_factor(float(getattr(stir, "axial", 0.0) or 0.0))
        except (TypeError, ValueError):
            axial = 0.0
        try:
            radial_source = getattr(stir, "radial", 1.0) if stir is not None else 1.0
            radial = clamp_stir_factor(float(radial_source or 0.0))
        except (TypeError, ValueError):
            radial = 1.0
        try:
            overhead_pressure_Pa = float(overhead_pressure_Pa)
        except (TypeError, ValueError):
            overhead_pressure_Pa = math.nan
        if not math.isfinite(overhead_pressure_Pa) or overhead_pressure_Pa < 0.0:
            overhead_pressure_Pa = 0.0
        carrier_gas = EvaporationMixin._evaporation_carrier_gas(self)
        campaign = getattr(
            getattr(getattr(self, "melt", None), "campaign", None),
            "name",
            None,
        )
        if not isinstance(campaign, str) or not campaign.strip():
            campaign = None
        else:
            campaign = campaign.strip()
        transport_cache = getattr(
            self, '_vapour_batch_hour_availability', None
        )
        cached_property_rows = (
            transport_cache.get('lennard_jones_property_rows_by_species', {})
            if isinstance(transport_cache, Mapping)
            else {}
        )
        transport_property_rows = (
            {
                str(species): dict(row)
                for species, row in cached_property_rows.items()
                if isinstance(row, Mapping)
            }
            if isinstance(cached_property_rows, Mapping)
            else {}
        )
        if not transport_property_rows:
            transport_property_rows = {
                str(species): dict(row)
                for species in partials_Pa
                if (row := _evaporation_vapor_row(
                    getattr(self, 'vapor_pressures', {}), str(species)
                )) is not None
            }
        cached_parameters = (
            transport_cache.get('lennard_jones_parameters_by_species', {})
            if isinstance(transport_cache, Mapping)
            else {}
        )
        transport_parameters = (
            {
                str(species): dict(parameters)
                for species, parameters in cached_parameters.items()
                if isinstance(parameters, Mapping)
            }
            if isinstance(cached_parameters, Mapping)
            else {}
        )
        cached_eligibility = (
            transport_cache.get('transport_eligibility_by_species', {})
            if isinstance(transport_cache, Mapping)
            else {}
        )
        transport_eligibility = (
            {
                str(species): str(decision)
                for species, decision in cached_eligibility.items()
            }
            if isinstance(cached_eligibility, Mapping)
            else {}
        )
        catalog = getattr(self, "vapor_pressures", None)
        cached_hkl = (
            transport_cache.get('hkl_upper_bound_transport_species', ())
            if isinstance(transport_cache, Mapping)
            else None
        )
        hkl = frozenset(
            str(species)
            for species in (
                cached_hkl
                if cached_hkl is not None
                else _load_hkl_upper_bound_transport_species(
                    catalog if isinstance(catalog, dict) else {}
                )
            )
        )
        extrapolate = False
        if gas_resistance_enabled and overhead_pressure_Pa > 0.0 and pipe_diameter_m > 0.0:
            knudsen = _knudsen_number(
                overhead_pressure_Pa,
                max(gas_temperature_K, 1.0),
                pipe_diameter_m,
                carrier_gas=str(carrier_gas),
            )
            commanded_pressure_Pa = max(
                0.0,
                float(
                    getattr(getattr(self, "melt", None), "p_total_mbar", 0.0)
                    or 0.0
                )
                * PA_PER_MBAR,
            )
            diagnostic = viscous_p_bulk_out_of_domain_diagnostic(
                knudsen_number=knudsen,
                overhead_pressure_pa=overhead_pressure_Pa,
                commanded_pressure_pa=commanded_pressure_Pa,
                pipe_diameter_m=pipe_diameter_m,
                gas_temperature_K=gas_temperature_K,
                carrier_gas=str(carrier_gas),
                campaign_name=campaign,
                asking_site="simulator.langmuir_surface_oxygen",
            )
            extrapolate = bool(
                diagnostic
                and continuum_validity_refuses(diagnostic)
                and diagnostic.get("reason") == "viscous_p_bulk_transport_out_of_domain"
            )
        return LangmuirTransport(
            temperature_K=float(temperature_K),
            pipe_diameter_m=pipe_diameter_m,
            gas_temperature_K=gas_temperature_K,
            carrier_gas=str(carrier_gas or "N2"),
            axial_stir_factor=axial,
            radial_stir_factor=radial,
            overhead_pressure_Pa=overhead_pressure_Pa,
            gas_resistance_enabled=gas_resistance_enabled,
            hkl_upper_bound_species=hkl,
            extrapolate_continuum=extrapolate,
            campaign_name=campaign,
            lennard_jones_property_rows_by_species=transport_property_rows,
            lennard_jones_parameters_by_species=transport_parameters,
            transport_eligibility_by_species=transport_eligibility,
        )

    def _closed_langmuir_pressures_for_partials(
        self,
        publication_Pa: Mapping[str, float],
        *,
        temperature_K: float,
        overhead_partials_Pa: Mapping[str, float] | None,
        provenance_by_species: Mapping[str, Mapping[str, Any]] | None,
        source_reaction_fO2_bar: float | None,
        transport_pO2_bar: float | None,
        melt_redox_capacity_mol_per_ln_fO2: float | None,
        overhead_pressure_Pa: float | None = None,
    ) -> Any:
        """Close the flux map on one partial vector. The only writer call."""

        from engines.builtin.evaporation_flux import (
            alpha_policy_controls,
            publisher_alpha_policy,
        )
        from simulator.langmuir_surface_oxygen import apply_langmuir_surface_oxygen

        kernel_config = dict(
            (getattr(self, "setpoints", {}) or {}).get("chemistry_kernel", {})
            or {}
        )
        allow_fallback, fallback_species = publisher_alpha_policy(
            alpha_policy_controls(kernel_config)
        )
        if overhead_pressure_Pa is None:
            partials, overhead_pressure_Pa = EvaporationMixin._langmuir_film(
                self, overhead_partials_Pa
            )
        else:
            partials = dict(overhead_partials_Pa or {})
        return apply_langmuir_surface_oxygen(
            publication_Pa,
            temperature_K=temperature_K,
            provenance_by_species=provenance_by_species,
            source_reaction_fO2_bar=source_reaction_fO2_bar,
            transport_pO2_bar=transport_pO2_bar,
            legacy_catalog=getattr(self, "vapor_pressures", None),
            compiled_catalog=getattr(self, "vapour_rail_catalog", None),
            melt_redox_capacity_mol_per_ln_fO2=(
                melt_redox_capacity_mol_per_ln_fO2
            ),
            overhead_partials_Pa=partials,
            transport=EvaporationMixin._langmuir_transport(
                self,
                partials,
                temperature_K,
                overhead_pressure_Pa,
            ),
            allow_unmeasured_alpha_fallback=allow_fallback,
            unmeasured_alpha_fallback_species_allowlist=fallback_species,
        )

    def _reclose_langmuir_pressures(
        self,
        overhead_partials_Pa: Mapping[str, float],
    ) -> Any | None:
        """Re-enter the closure for a later partial vector on this step.

        Finite-capacity iterations must not keep the map closed on the
        partials from the start of the step. A missing context means this
        step did not publish a flux map.
        """

        context = getattr(self, "_langmuir_flux_closure_context", None)
        if not isinstance(context, Mapping):
            return None
        return EvaporationMixin._closed_langmuir_pressures_for_partials(
            self,
            context["publication_Pa"],
            temperature_K=float(context["temperature_K"]),
            overhead_partials_Pa=overhead_partials_Pa,
            provenance_by_species=context.get("provenance_by_species"),
            source_reaction_fO2_bar=context.get("source_reaction_fO2_bar"),
            transport_pO2_bar=context.get("transport_pO2_bar"),
            melt_redox_capacity_mol_per_ln_fO2=context.get(
                "melt_redox_capacity_mol_per_ln_fO2"
            ),
        )

    def _langmuir_overhead_pO2_bar(self) -> float | None:
        """Upstream headspace O2 partial, in bar.

        The evaporation backpressure reader returns Pa. Dividing by
        ``PA_PER_BAR`` (1e5 Pa/bar) is the same partial the overhead
        lower bound compares with the congruent surface. No O2 in the
        headspace is not a bound.
        """

        try:
            partial_pa = float(self._evaporation_bulk_partial_pressure_pa("O2"))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(partial_pa) or partial_pa <= 0.0:
            return None
        return partial_pa / PA_PER_BAR

    def _langmuir_melt_redox_capacity_mol_per_ln_fO2(
        self,
        *,
        temperature_K: float,
        eval_pO2_bar: float | None,
    ) -> float | None:
        """Reservoir source capacity for the surface-pO2 gate.

        The no-buffer test is ``_melt_redox_source_capacity_mol_per_ln_fO2
        <= OXYGEN_RESERVOIR_NOOP_MOL`` (``no_melt_redox_capacity``). That
        source capacity is ``C_m_full`` times the reservoir liquid-fraction
        factor, not ``equilibrium.liquid_fraction``. A missing method or a
        non-finite result is not a buffer measurement. The liquid-fraction
        factor's ``unavailable`` status returns 0 as a sentinel; that
        sentinel is not ``C_m ~ 0`` unless ``C_m_full`` itself is already
        at the noop floor (no Fe). Pure CaO has ``n_Fe = 0``, so the full
        term is 0 and this returns 0.
        """

        method = getattr(
            self, "_melt_redox_source_capacity_mol_per_ln_fO2", None
        )
        if (
            not callable(method)
            or eval_pO2_bar is None
            or not math.isfinite(eval_pO2_bar)
            or eval_pO2_bar <= 0.0
        ):
            return None
        try:
            capacity = float(method(
                fO2_log=math.log10(float(eval_pO2_bar)),
                T_K=float(temperature_K),
            ))
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(capacity):
            return None
        diagnostic = getattr(
            self, "_last_melt_redox_liquid_fraction_diagnostic", None
        )
        if (
            isinstance(diagnostic, Mapping)
            and diagnostic.get("status") == "unavailable"
        ):
            full_method = getattr(
                self, "_melt_redox_capacity_mol_per_ln_fO2", None
            )
            if not callable(full_method):
                return None
            try:
                full = float(full_method(
                    fO2_log=math.log10(float(eval_pO2_bar)),
                    T_K=float(temperature_K),
                ))
            except (TypeError, ValueError, OverflowError):
                return None
            if not math.isfinite(full) or full > 0.0:
                return None
            return full
        return capacity

    def _vapour_batch_resolution_outcome(
        self,
        resolve_error: Mapping[str, Any] | None,
        flux_overlay: Mapping[str, Any] | None,
        *,
        selector_overlay: Mapping[str, Any] | None = None,
        flux_pressures: Mapping[str, float] | None = None,
        provider_diagnostic: Mapping[str, Any] | None = None,
        include_unresolved_notices: bool = False,
        requested_species_ids: Iterable[str] | None = None,
        liquid_fraction: Any = None,
        temperature_K: float | None = None,
        equilibrium_status: Any = None,
        equilibrium_zero_reason: Any = None,
        effective_pressure_zero_reason: str | None = None,
    ) -> _VapourBatchResolutionOutcome:
        """Own the shared resolved, physical-zero, and refusal decision."""
        error = dict(resolve_error or {})
        overlay = dict(flux_overlay or {})
        selector = dict(
            selector_overlay if selector_overlay is not None else overlay
        )
        pressures = flux_pressures if isinstance(flux_pressures, Mapping) else {}
        resolved_species = _provider_resolved_flux_species(
            provider_diagnostic or {}, overlay, flux_pressures=pressures
        )
        notices = [
            notice
            for notice in overlay.get('notices', ()) or ()
            if isinstance(notice, Mapping)
            and notice.get('kind') == 'vapour_batch_unresolved'
        ]
        channel_states = dict(overlay.get('batch_channel_states', {}) or {})
        refusal_codes = dict(
            overlay.get('batch_channel_refusal_codes', {}) or {}
        )
        provider_zero_reason = (
            effective_pressure_zero_reason
            or overlay.get('effective_pressure_zero_reason')
        )
        evidence = {
            'batch_channel_states': channel_states,
            'net_driving_force_by_species': dict(
                overlay.get('net_driving_force_by_species', {}) or {}
            ),
            'vapour_batch_flux_overlay': overlay,
            'provider_zero_reason': provider_zero_reason,
        }
        if not error:
            requested = (
                frozenset(str(species) for species in requested_species_ids)
                if requested_species_ids is not None
                else None
            )
            liquid_state_admits_inventory_zero = liquid_fraction is None
            if isinstance(liquid_fraction, Real) and not isinstance(
                liquid_fraction, bool
            ):
                try:
                    melt_regime(liquid_fraction=liquid_fraction)
                except (TypeError, ValueError):
                    if requested == frozenset():
                        melt_regime(
                            liquid_fraction=liquid_fraction,
                            epsilon=0.0,
                            invalid_liquid_fraction_regime=MeltRegime.PARTIAL,
                            diagnostic=evidence,
                            diagnostic_site=(
                                'evaporation.empty_vapor_pressure.'
                                'liquid_fraction'
                            ),
                            legacy_predicate='liquid_fraction == 0.0',
                        )
                    pass
                else:
                    liquid_state_admits_inventory_zero = True
            if requested == frozenset() and liquid_state_admits_inventory_zero:
                return _VapourBatchResolutionOutcome(
                    resolved_species,
                    'proven_empty_inventory',
                    evidence,
                    None,
                )
            if (
                temperature_K is not None
                and 0.0 < temperature_K < 400.0
                and equilibrium_status == 'ok'
                and equilibrium_zero_reason
                == 'internal_analytical_below_400K'
                and not notices
            ):
                return _VapourBatchResolutionOutcome(
                    resolved_species,
                    'internal_analytical_below_400K',
                    evidence,
                    None,
                )
            if (
                temperature_K is not None
                and 0.0 < temperature_K < 400.0
                and not notices
            ):
                return _VapourBatchResolutionOutcome(
                    resolved_species, 'below_400K', evidence, None
                )
            if (
                not pressures
                and provider_zero_reason
                and overlay.get('batch_pa_by_species')
            ):
                return _VapourBatchResolutionOutcome(
                    resolved_species,
                    str(provider_zero_reason),
                    evidence,
                    None,
                )
            typed_non_debit_species = {
                str(species)
                for species, state in channel_states.items()
                if _channel_is_typed_non_debit(
                    state, refusal_codes.get(species)
                )
            }
            if (
                channel_states
                and typed_non_debit_species
                and all(
                    str(species) in typed_non_debit_species
                    for species in channel_states
                )
                and not notices
            ):
                return _VapourBatchResolutionOutcome(
                    resolved_species, 'typed_non_debit', evidence, None
                )
            if not include_unresolved_notices and not notices:
                return _VapourBatchResolutionOutcome(
                    resolved_species, None, evidence, None
                )
        if resolved_species:
            return _VapourBatchResolutionOutcome(
                resolved_species, 'resolved_species', evidence, None
            )
        from simulator.langmuir_surface_oxygen import (
            LANGMUIR_SURFACE_OXYGEN_BALANCE_NOT_CLOSED,
        )

        error_reason = error.get('reason')
        if error_reason in {
            TYPED_FAILURE_EFFECTIVE_PRESSURE_SPECIES_SET_MISMATCH,
            LANGMUIR_SURFACE_OXYGEN_BALANCE_NOT_CLOSED,
        }:
            error_reason = None
        if error_reason:
            reason = str(error_reason)
        else:
            # Selector labels identify the value source, not a terminal
            # refusal. Closure notices are downstream detail and likewise
            # never become a terminal code.
            selection_source = (
                selector.get('selection_source')
                if notices
                else overlay.get('selection_source')
            )
            if selection_source in {
                TYPED_FAILURE_EFFECTIVE_PRESSURE_SPECIES_SET_MISMATCH,
                LANGMUIR_SURFACE_OXYGEN_BALANCE_NOT_CLOSED,
                PRE_RG_EFFECTIVE_PRESSURE_SOURCE_ID,
            }:
                selection_source = None
            reason = str(
                selection_source or _VAPOUR_BATCH_NO_DEBITING_PRESSURE_OUTCOME
            )
        refusal = EvaporationFluxRefusal(
            reason,
            {
                **error,
                'reason': reason,
                'evaporation_flux_kg_hr': {},
                **evidence,
            },
        )
        return _VapourBatchResolutionOutcome(
            resolved_species, reason, evidence, refusal
        )

    def _vapour_batch_resolution_refusal(
        self,
        resolve_error: Mapping[str, Any] | None,
        flux_overlay: Mapping[str, Any] | None,
        *,
        selector_overlay: Mapping[str, Any] | None = None,
        flux_pressures: Mapping[str, float] | None = None,
        provider_diagnostic: Mapping[str, Any] | None = None,
        include_unresolved_notices: bool = False,
    ) -> EvaporationFluxRefusal | None:
        """Compatibility projection of the shared terminal outcome owner."""
        return EvaporationMixin._vapour_batch_resolution_outcome(
            self,
            resolve_error,
            flux_overlay,
            selector_overlay=selector_overlay,
            flux_pressures=flux_pressures,
            provider_diagnostic=provider_diagnostic,
            include_unresolved_notices=include_unresolved_notices,
        ).refusal

    def _record_langmuir_refusal_notices(
        self,
        flux_overlay: dict[str, Any],
        surface: Any,
        *,
        final_pressures: Mapping[str, float],
        vapor_pressure_diagnostic: Mapping[str, Any] | None = None,
        overhead_partials_Pa: Mapping[str, float] | None = None,
    ) -> dict[str, str]:
        """Classify closure refusals and disclose those still unavailable."""
        states = flux_overlay.get('batch_channel_states', {})
        refused_species = getattr(surface, 'refused_species', {}) or {}
        refusal_codes = getattr(surface, 'refusal_codes_by_species', {}) or {}
        if not isinstance(refused_species, Mapping):
            refused_species = {}
        if not isinstance(states, Mapping):
            return {}
        if isinstance(vapor_pressure_diagnostic, Mapping):
            previous_cap_evidence = flux_overlay.pop(
                'net_driving_force_by_species', {}
            )
            if isinstance(previous_cap_evidence, Mapping):
                states = dict(states)
                for species in previous_cap_evidence:
                    species_id = str(species)
                    if states.get(species_id) == 'zero_by_physics':
                        states[species_id] = 'eligible'
                flux_overlay['batch_channel_states'] = states
        already_unavailable = {
            str(notice.get('species'))
            for notice in flux_overlay.get('notices', ()) or ()
            if isinstance(notice, Mapping)
            and notice.get('kind') == 'vapour_batch_unresolved'
            and notice.get('species') is not None
        }
        omitted_species = {
            str(species)
            for species, state in states.items()
            if state == 'eligible'
            and str(species) not in final_pressures
            and str(species) not in already_unavailable
        }
        closure_unavailable: dict[str, str] = {}
        refused_species_ids = {
            str(species) for species in refused_species
        } - already_unavailable
        for species in sorted(omitted_species | refused_species_ids):
            species_id = str(species)
            if (
                states.get(species_id) != 'eligible'
                or species_id in final_pressures
            ):
                continue
            detail = refused_species.get(species_id)
            reason = detail or (
                refusal_codes.get(species_id)
                if isinstance(refusal_codes, Mapping)
                else None
            ) or 'langmuir_surface_oxygen_balance_not_closed'
            closure_unavailable[species_id] = str(reason)
            _append_vapour_batch_unresolved_notice(
                self,
                flux_overlay,
                species=species_id,
                reason=str(reason),
            )
        if isinstance(vapor_pressure_diagnostic, Mapping):
            proven_zero = EvaporationMixin._classify_proven_zero_langmuir_channels(
                self,
                flux_overlay,
                vapor_pressure_diagnostic,
                surface=surface,
                overhead_partials_Pa=overhead_partials_Pa,
            )
            closure_unavailable = {
                species: reason
                for species, reason in closure_unavailable.items()
                if species not in proven_zero
            }
        return closure_unavailable

    def _classify_langmuir_trial_channels(
        self,
        flux_overlay: dict[str, Any],
        surface: Any,
        *,
        final_pressures: Mapping[str, float],
        vapor_pressure_diagnostic: Mapping[str, Any],
        overhead_partials_Pa: Mapping[str, float],
        selector_overlay: Mapping[str, Any],
        requested_species_ids: Iterable[str] | None,
        liquid_fraction: Any,
        temperature_K: float,
        resolve_error: Mapping[str, Any] | None = None,
        equilibrium_status: Any = None,
        equilibrium_zero_reason: Any = None,
        effective_pressure_zero_reason: str | None = None,
        include_cap_proven_zero: bool = False,
        admitted_species: Iterable[str] | None = None,
    ) -> tuple[
        dict[str, float],
        dict[str, str],
        _VapourBatchResolutionOutcome,
    ]:
        """Own closure refusal, cap-proof, and terminal trial classification."""
        pressures = {
            str(species): float(pressure)
            for species, pressure in final_pressures.items()
        }
        explicit_refusals = getattr(surface, 'refused_species', {}) or {}
        if not isinstance(explicit_refusals, Mapping):
            explicit_refusals = {}
        closure_failures = (
            EvaporationMixin._record_langmuir_refusal_notices(
                self,
                flux_overlay,
                surface,
                final_pressures=pressures,
                vapor_pressure_diagnostic=vapor_pressure_diagnostic,
                overhead_partials_Pa=overhead_partials_Pa,
            )
            if getattr(surface, 'applied', False) or explicit_refusals
            else {}
        )
        if include_cap_proven_zero:
            EvaporationMixin._include_cap_proven_zero_pressure_values(
                pressures,
                flux_overlay,
                admitted_species=admitted_species,
            )
        outcome = EvaporationMixin._vapour_batch_resolution_outcome(
            self,
            resolve_error,
            flux_overlay,
            selector_overlay=selector_overlay,
            flux_pressures=pressures,
            include_unresolved_notices=True,
            requested_species_ids=requested_species_ids,
            liquid_fraction=liquid_fraction,
            temperature_K=temperature_K,
            equilibrium_status=equilibrium_status,
            equilibrium_zero_reason=equilibrium_zero_reason,
            effective_pressure_zero_reason=effective_pressure_zero_reason,
        )
        return pressures, dict(closure_failures), outcome

    def _carry_classified_vapour_batch_results(
        self,
        batch_report: dict[str, Any] | None,
        flux_overlay: Mapping[str, Any],
        flux: Any,
    ) -> None:
        """Project already-classified typed-zero evidence onto report rows."""
        states = flux_overlay.get('batch_channel_states', {})
        driving_forces = flux_overlay.get('net_driving_force_by_species', {})
        if not isinstance(states, Mapping):
            return
        channels = (
            batch_report.get('channels_by_species')
            if isinstance(batch_report, dict)
            else None
        )
        authorities = getattr(flux, 'carrier_authority_by_species', None)
        for species, state in states.items():
            if state != 'zero_by_physics':
                continue
            evidence = {
                'kind': 'zero_by_physics',
                'evidence_ref': 'non_positive_capped_net_driving_force',
            }
            extra = (
                {'net_driving_force': dict(driving_forces[species])}
                if isinstance(driving_forces, Mapping)
                and isinstance(driving_forces.get(species), Mapping)
                else {}
            )
            for rows in (channels, authorities):
                if not isinstance(rows, dict):
                    continue
                record = rows.get(str(species))
                if not isinstance(record, dict):
                    continue
                current_pressure = record.get('pressure')
                if not (
                    isinstance(current_pressure, Mapping)
                    and current_pressure.get('kind') == 'zero_by_physics'
                ):
                    record['pressure'] = dict(evidence)
                record['extra'] = {**dict(record.get('extra') or {}), **extra}

    def _classify_proven_zero_langmuir_channels(
        self,
        flux_overlay: dict[str, Any],
        vapor_pressure_diagnostic: Mapping[str, Any],
        *,
        flux: Any = None,
        surface: Any = None,
        overhead_partials_Pa: Mapping[str, float] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Mark closure-refused channels as typed zero only with cap proof."""
        from simulator.langmuir_surface_oxygen import (
            LANGMUIR_SURFACE_OXYGEN_BALANCE_NOT_CLOSED,
            _condensed_cap,
        )

        surface = (
            getattr(self, '_last_langmuir_surface_oxygen', {}) or {}
            if surface is None
            else surface
        )
        as_diagnostic = getattr(surface, 'as_diagnostic', None)
        surface_report = (
            dict(as_diagnostic())
            if callable(as_diagnostic)
            else dict(surface)
            if isinstance(surface, Mapping)
            else {}
        )
        if not (
            surface_report.get('applied', getattr(surface, 'applied', False))
            and surface_report.get(
                'surface_pO2_bar', getattr(surface, 'surface_pO2_bar', None)
            ) is None
        ):
            return {}

        provenance = vapor_pressure_diagnostic.get(
            'vapor_pressure_numerator_provenance', {}
        ) or {}
        selected = flux_overlay.get('selected_runtime_pa_by_species', {})
        partials = (
            overhead_partials_Pa
            if overhead_partials_Pa is not None
            else surface_report.get('overhead_partials_Pa', {})
        )
        refused_species = getattr(
            surface, 'refused_species', surface_report.get('refused_species', {})
        ) or {}
        refusal_codes = getattr(
            surface,
            'refusal_codes_by_species',
            surface_report.get('refusal_codes_by_species', {}),
        ) or {}
        channel_states = dict(flux_overlay.get('batch_channel_states', {}) or {})
        driving_forces: dict[str, dict[str, Any]] = {}
        proven_zero: dict[str, dict[str, Any]] = {}
        if not isinstance(refused_species, Mapping):
            return driving_forces
        for species in refused_species:
            if (
                channel_states.get(species) != 'eligible'
                or not isinstance(refusal_codes, Mapping)
                or refusal_codes.get(species)
                != LANGMUIR_SURFACE_OXYGEN_BALANCE_NOT_CLOSED
            ):
                continue
            pressure = selected.get(species) if isinstance(selected, Mapping) else None
            headspace = partials.get(species) if isinstance(partials, Mapping) else None
            if not all(
                isinstance(value, Real) and not isinstance(value, bool)
                and math.isfinite(value) and value > 0.0
                for value in (pressure, headspace)
            ):
                continue
            _capped, _activity, cap = _condensed_cap(
                provenance.get(species, {}) if isinstance(provenance, Mapping) else {},
                pressure,
            )
            if cap is None:
                continue
            drive = cap - headspace
            driving_forces[str(species)] = {
                'capped_equilibrium_pressure_Pa': cap,
                'headspace_partial_pressure_Pa': headspace,
                'driving_force_Pa': drive,
                'driving_force_sign': 'non_positive' if drive <= 0.0 else 'positive',
                'closure_bracket_state': surface_report.get('warning'),
            }
            # With net condensation disabled, J <= 0 at the pure-component
            # cap proves no outward flux at any possible surface pO2.
            if drive > 0.0:
                continue
            proven_zero[str(species)] = driving_forces[str(species)]
            channel_states[str(species)] = 'zero_by_physics'
            authorities = getattr(flux, 'carrier_authority_by_species', None)
            if isinstance(authorities, dict) and species in authorities:
                channel = authorities[species]
                channel['pressure'] = {
                    'kind': 'zero_by_physics',
                    'evidence_ref': 'non_positive_capped_net_driving_force',
                }
                channel['extra'] = {
                    **channel.get('extra', {}),
                    'net_driving_force': driving_forces[str(species)],
                }
            notices = flux_overlay.get('notices')
            if isinstance(notices, list):
                flux_overlay['notices'] = [
                    notice for notice in notices
                    if not (
                        isinstance(notice, Mapping)
                        and notice.get('kind') == 'vapour_batch_unresolved'
                        and notice.get('species') == species
                    )
                ]
        if driving_forces:
            flux_overlay['net_driving_force_by_species'] = driving_forces
            flux_overlay['batch_channel_states'] = channel_states
        return proven_zero

    @staticmethod
    def _include_cap_proven_zero_pressure_values(
        flux_pressures: dict[str, float],
        flux_overlay: Mapping[str, Any],
        *,
        admitted_species: Iterable[str] | None = None,
    ) -> None:
        """Keep cap-proven channels in the pressure map as typed zero values."""
        driving_forces = flux_overlay.get('net_driving_force_by_species', {})
        channel_states = flux_overlay.get('batch_channel_states', {})
        if not (
            isinstance(driving_forces, Mapping)
            and isinstance(channel_states, Mapping)
        ):
            return
        admitted = (
            None
            if admitted_species is None
            else {str(species) for species in admitted_species}
        )
        for species in driving_forces:
            species_id = str(species)
            if (
                channel_states.get(species_id) == 'zero_by_physics'
                and (admitted is None or species_id in admitted)
            ):
                flux_pressures.setdefault(species_id, 0.0)

    def _resolve_evaporation_batch_flux_state(
        self,
        equilibrium: Any,
        *,
        temperature_K: float,
        overhead_partials_Pa: Mapping[str, float] | None = None,
        interpolated_provenance: bool | None = None,
    ) -> tuple[
        dict[str, float],
        dict[str, Any],
        Any,
        dict[str, Any],
        dict[str, Any],
        Any,
    ]:
        """Batch-gated flux pressures shared by live evaporation and capacity.

        ``vapour_batch_flux_pressures_Pa`` is the only flux pressure input.
        An omitted or explicit empty map prices no species; it does not fall
        through to ``equilibrium.vapor_pressures_Pa``. Callers that solve
        flux at another partial-pressure vector (the finite cold-train
        shadow) must pass this map so a flagged transitional species is
        priced with its flag instead of dropped from the solved set.

        The returned map is that shared input after the d-376 amendment 1
        surface closure. The congruent balance runs only when the
        evaluated pO2 is at the physical floor or the reservoir
        no-buffer test is true (source capacity <= the noop floor, not
        ``equilibrium.liquid_fraction`` times ``C_m_full``). Transport-
        headspace pressures, including SiO, stay published. The root is
        the publisher net-flux balance at ``overhead_partials_Pa`` (the
        persisted headspace when the caller passes none). It is not a
        vacuum root clamped to the overhead partial. Everywhere else the
        map stays the reservoir / mass-action publication. Finite
        cold-train capacity re-enters through
        ``_reclose_langmuir_pressures`` on each partial vector, starting
        from the publication stored here, so a later headspace does not
        keep the first vector's pressures. ``equilibrium`` vapor
        pressures stay the mass-action law; the shadow comparison above
        the closure still checks that provider seam.
        """
        vapor_pressure_diagnostic = dict(
            getattr(self, '_last_vapor_pressure_diagnostic', {}) or {}
        )
        self._last_vapour_batch_terminal_outcome = None
        self._last_vapour_batch_selector_overlay = None
        if interpolated_provenance is None:
            interpolated_provenance = _is_interpolated_vapour_equilibrium(
                equilibrium,
                vapor_pressure_diagnostic,
            )
        unresolved_effective_pressure_species: set[str] = set()
        if interpolated_provenance:
            # Treat interpolation as a vapour cache miss before reading any
            # equilibrium vapour input or invoking the batch builder.
            effective_pressure_source = EffectivePressureSource(
                PRE_RG_EFFECTIVE_PRESSURE_SOURCE_ID,
                {},
            )
            vapour_batch = None
            resolve_error = {
                'status': 'unavailable',
                'reason': 'interpolated_equilibrium_not_flux_eligible',
                'detail': 'cached interpolation is served for non-flux consumers only',
            }
            self._last_vapour_batch_resolve_error = dict(resolve_error)
            vapor_pressures, flux_overlay_report = flux_pressures_from_batch(
                None,
                effective_pressure_source=effective_pressure_source,
                resolution_error=resolve_error,
            )
            catalog = getattr(self, 'vapour_rail_catalog', None)
            ledger = getattr(self, 'atom_ledger', None)
            request_builder = getattr(catalog, 'build_request', None)
            inventory_reader = getattr(ledger, 'mol_by_account', None)
            try:
                requested_species_ids = (
                    request_builder(inventory_reader())
                    if callable(request_builder)
                    and callable(inventory_reader)
                    else ()
                )
            except Exception:  # noqa: BLE001 — preserve the provenance refusal
                requested_species_ids = ()
            for species in sorted(requested_species_ids) or [None]:
                _append_vapour_batch_unresolved_notice(
                    self,
                    flux_overlay_report,
                    species=(None if species is None else str(species)),
                    reason=str(resolve_error['reason']),
                )
            flux_overlay_report['n_flux_pressures'] = 0
            flux_overlay_report.update(
                compare_live_shadow_to_batch_flux(
                    batch=None,
                    live_pressures_Pa=None,
                    batch_flux_pressures_Pa={},
                    resolution_error=resolve_error,
                )
            )
            if hasattr(self, '_last_langmuir_surface_oxygen'):
                del self._last_langmuir_surface_oxygen
            self._langmuir_flux_closure_context = None
            self._last_vapour_batch_selector_overlay = copy.deepcopy(
                flux_overlay_report
            )
            self._last_vapour_batch_terminal_outcome = (
                EvaporationMixin._vapour_batch_resolution_outcome(
                    self,
                    resolve_error,
                    flux_overlay_report,
                    selector_overlay=flux_overlay_report,
                    include_unresolved_notices=True,
                    requested_species_ids=requested_species_ids,
                )
            )
            return (
                {},
                flux_overlay_report,
                vapour_batch,
                resolve_error,
                vapor_pressure_diagnostic,
                effective_pressure_source,
            )

        effective_pressure_source = _pre_rg_effective_pressure_source(
            self.vapor_pressures,
            equilibrium,
            unresolved_species=unresolved_effective_pressure_species,
        )
        live_vapor_pressures = _evaporation_legacy_shadow_pressure_map(
            self.vapor_pressures,
            vapor_pressure_diagnostic,
        )
        vapour_batch = self._resolve_evaporation_vapour_batch(
            equilibrium,
            temperature_K=temperature_K,
            effective_pressure_source=effective_pressure_source,
        )
        resolve_error = dict(
            getattr(self, '_last_vapour_batch_resolve_error', {}) or {}
        )
        vapor_pressures, flux_overlay_report = flux_pressures_from_batch(
            vapour_batch,
            effective_pressure_source=effective_pressure_source,
            resolution_error=resolve_error or None,
        )
        requested_species_ids = frozenset(
            getattr(vapour_batch, "requested_species_ids", ()) or ()
        )
        if vapour_batch is None:
            catalog = getattr(self, "vapour_rail_catalog", None)
            ledger = getattr(self, "atom_ledger", None)
            request_builder = getattr(catalog, "build_request", None)
            inventory_reader = getattr(ledger, "mol_by_account", None)
            if callable(request_builder) and callable(inventory_reader):
                try:
                    requested_species_ids = frozenset(
                        request_builder(inventory_reader())
                    )
                except Exception:  # noqa: BLE001 — preserve the resolver refusal
                    requested_species_ids = frozenset()
        unresolved_notice_reasons: dict[str | None, str] = {}
        if resolve_error:
            for species in sorted(requested_species_ids) or [None]:
                unresolved_notice_reasons[species] = str(
                    resolve_error.get("reason")
                    or "vapour_batch_resolution_error"
                )
        else:
            unresolved_missing_species = (
                flux_overlay_report.get(
                    "unresolved_missing_effective_pressure_species", ()
                )
                or ()
            )
            for species in sorted(unresolved_missing_species):
                unresolved_notice_reasons[str(species)] = str(
                    flux_overlay_report.get("selection_source")
                    or _VAPOUR_BATCH_NO_DEBITING_PRESSURE_OUTCOME
                )
            channel_states = flux_overlay_report.get(
                "batch_channel_states", {}
            )
            refusal_codes = flux_overlay_report.get(
                "batch_channel_refusal_codes", {}
            )
            if isinstance(channel_states, Mapping):
                for species, state in sorted(channel_states.items()):
                    state_name = str(state)
                    if (
                        _channel_is_typed_non_debit(
                            state_name,
                            refusal_codes.get(species)
                            if isinstance(refusal_codes, Mapping)
                            else None,
                        )
                        or state_name == "eligible"
                    ):
                        continue
                    answer = (
                        vapour_batch.channels_by_species.get(str(species))
                        if vapour_batch is not None
                        else None
                    )
                    reason = state_name
                    if answer is not None:
                        reason = str(
                            getattr(getattr(answer, "flux", None), "code", None)
                            or getattr(
                                getattr(answer, "pressure", None), "code", None
                            )
                            or reason
                        )
                    unresolved_notice_reasons[str(species)] = reason
        for species in sorted(unresolved_effective_pressure_species):
            unresolved_notice_reasons[species] = (
                'nonfinite_effective_pressure'
            )
        flux_overlay_report['n_flux_pressures'] = len(vapor_pressures)
        flux_overlay_report.update(
            compare_live_shadow_to_batch_flux(
                batch=vapour_batch,
                live_pressures_Pa=live_vapor_pressures,
                batch_flux_pressures_Pa=vapor_pressures,
                resolution_error=resolve_error or None,
            )
        )
        for species, reason in sorted(
            unresolved_notice_reasons.items(),
            key=lambda item: '' if item[0] is None else item[0],
        ):
            if species is not None and species in vapor_pressures:
                continue
            channel_states = flux_overlay_report.get('batch_channel_states')
            refusal_codes = flux_overlay_report.get(
                'batch_channel_refusal_codes', {}
            )
            state = (
                channel_states.get(species)
                if isinstance(channel_states, Mapping)
                else None
            )
            if _channel_is_typed_non_debit(
                state,
                refusal_codes.get(species)
                if isinstance(refusal_codes, Mapping)
                else None,
            ):
                continue
            _append_vapour_batch_unresolved_notice(
                self,
                flux_overlay_report,
                species=species,
                reason=reason,
            )
        solver_species = set(vapor_pressures)
        channel_states = flux_overlay_report.get('batch_channel_states', {})
        if isinstance(channel_states, Mapping):
            solver_species.update(
                str(species)
                for species, state in channel_states.items()
                if state == 'zero_by_physics'
            )
        vapor_pressures = dict(vapor_pressures)
        unavailable_by_species = (
            EvaporationMixin._classify_hour_vapour_availability(
                self,
                equilibrium,
                vapor_pressures,
                flux_overlay_report,
                unresolved_notice_reasons,
            )
        )
        solver_species.difference_update(unavailable_by_species)
        solver_species.intersection_update(vapor_pressures)
        flux_overlay_report['n_flux_pressures'] = len(vapor_pressures)
        # Preserve input-side membership and notices before the first
        # surface closure. Each capacity trial re-evaluates cap proofs
        # against its own pressure vector from this uncapped baseline.
        selector_flux_overlay = copy.deepcopy(flux_overlay_report)
        # Shadow equality is the unadjusted provider seam. The surface
        # balance below is the flux boundary both consumers must share.
        if hasattr(self, "_last_langmuir_surface_oxygen"):
            del self._last_langmuir_surface_oxygen
        self._langmuir_flux_closure_context = None

        source_reaction_fO2_bar = vapor_pressure_diagnostic.get(
            "source_reaction_fO2_bar"
        )
        try:
            eval_pO2_bar = float(source_reaction_fO2_bar)
        except (TypeError, ValueError):
            eval_pO2_bar = None
        else:
            if not math.isfinite(eval_pO2_bar) or eval_pO2_bar <= 0.0:
                eval_pO2_bar = None
        # Namespace fakes bind this resolver without the mixin helpers.
        # A missing reader is not a measurement: capacity stays unknown
        # (not ``C_m ~ 0``) and there is no headspace partial.
        capacity_reader = getattr(
            self, "_langmuir_melt_redox_capacity_mol_per_ln_fO2", None
        )
        capacity = (
            capacity_reader(
                temperature_K=temperature_K,
                eval_pO2_bar=eval_pO2_bar,
            )
            if callable(capacity_reader)
            else None
        )
        if overhead_partials_Pa is None:
            # Call the mixin directly. A namespace host binds the
            # resolver without copying every helper onto itself; the
            # helper then uses whatever bulk reader that host has.
            # ``vapor_pressures`` is not a filter: the helper returns
            # every positive persisted partial, and the film sums it.
            partials = EvaporationMixin._langmuir_headspace_partials_Pa(
                self, vapor_pressures
            )
        else:
            # This evaluation's vector, including explicit zeros. Do not
            # fill gaps from the persisted headspace: a same-step
            # accumulation that omitted a species has no bulk for it.
            partials = {}
            for species, value in overhead_partials_Pa.items():
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(number):
                    partials[str(species)] = number
        provenance = (
            vapor_pressure_diagnostic.get(
                "vapor_pressure_numerator_provenance"
            )
            or {}
        )
        transport_pO2_bar = vapor_pressure_diagnostic.get("pO2_bar")
        # One film. The publisher reads these two fields. It does not
        # rebuild the vector from the vapor-map keys.
        partials, film_pressure = EvaporationMixin._langmuir_film(
            self, partials
        )
        # Pre-close publication. Capacity iterations re-enter from this
        # map; closing the already-adjusted map would apply the power
        # law twice. Only this resolver stores the diagnostic.
        closure_publication = dict(vapor_pressures)
        active_species = set(solver_species) & set(closure_publication)
        for species, state in channel_states.items():
            if state == 'zero_by_physics':
                active_species.discard(str(species))
        closed_pressures: dict[str, float] = {}
        langmuir_surface = None
        failed_root_diagnostic = None
        initial_closure_outcome = None
        for _pass in range(max(1, len(active_species))):
            if not active_species:
                break
            current_publication = {
                species: pressure
                for species, pressure in closure_publication.items()
                if species in active_species
            }
            self._langmuir_flux_closure_context = {
                "publication_Pa": dict(current_publication),
                "temperature_K": float(temperature_K),
                "provenance_by_species": provenance,
                "source_reaction_fO2_bar": source_reaction_fO2_bar,
                "transport_pO2_bar": transport_pO2_bar,
                "melt_redox_capacity_mol_per_ln_fO2": capacity,
                "film_partials_Pa": partials,
                "film_pressure_Pa": film_pressure,
                "root_closed": False,
            }
            langmuir_surface = (
                EvaporationMixin._closed_langmuir_pressures_for_partials(
                    self,
                    current_publication,
                    temperature_K=temperature_K,
                    overhead_partials_Pa=partials,
                    provenance_by_species=provenance,
                    source_reaction_fO2_bar=source_reaction_fO2_bar,
                    transport_pO2_bar=transport_pO2_bar,
                    melt_redox_capacity_mol_per_ln_fO2=capacity,
                    overhead_pressure_Pa=film_pressure,
                )
            )
            self._langmuir_flux_closure_context["root_closed"] = bool(
                langmuir_surface.applied
                and langmuir_surface.surface_pO2_bar is not None
            )
            closed_pressures = {
                str(species): float(pressure)
                for species, pressure in langmuir_surface.pressures_Pa.items()
                if str(species) in active_species
            }
            (
                closed_pressures,
                closure_failures,
                initial_closure_outcome,
            ) = EvaporationMixin._classify_langmuir_trial_channels(
                self,
                flux_overlay_report,
                langmuir_surface,
                final_pressures=closed_pressures,
                vapor_pressure_diagnostic=vapor_pressure_diagnostic,
                overhead_partials_Pa=partials,
                selector_overlay=selector_flux_overlay,
                requested_species_ids=(
                    getattr(vapour_batch, 'requested_species_ids', None)
                    if vapour_batch is not None
                    else None
                ),
                liquid_fraction=getattr(
                    equilibrium, 'liquid_fraction', None
                ),
                temperature_K=temperature_K,
                resolve_error=resolve_error,
                equilibrium_status=getattr(equilibrium, 'status', None),
                equilibrium_zero_reason=(
                    vapor_pressure_diagnostic.get(
                        'vapor_pressure_zero_reason'
                    )
                    or (getattr(equilibrium, 'diagnostics', {}) or {}).get(
                        'vapor_pressure_zero_reason'
                    )
                ),
                effective_pressure_zero_reason=(
                    effective_pressure_source.physical_zero_reason
                ),
            )
            closure_failures = {
                species: reason
                for species, reason in closure_failures.items()
                if species in active_species
            }
            if not closure_failures:
                break
            if failed_root_diagnostic is None:
                failed_root_diagnostic = langmuir_surface.as_diagnostic()
                failed_root_diagnostic['overhead_partials_Pa'] = dict(partials)
                failed_root_diagnostic['overhead_pressure_Pa'] = film_pressure
            EvaporationMixin._classify_hour_vapour_availability(
                self,
                equilibrium,
                vapor_pressures,
                flux_overlay_report,
                unresolved_notice_reasons,
                observed_unavailable_by_species=closure_failures,
            )
            EvaporationMixin._classify_hour_vapour_availability(
                self,
                equilibrium,
                vapor_pressures,
                selector_flux_overlay,
                unresolved_notice_reasons,
                observed_unavailable_by_species=closure_failures,
            )
            active_species.difference_update(closure_failures)
            closure_publication = {
                species: pressure
                for species, pressure in closure_publication.items()
                if species in active_species
            }
        published_pressures = (
            closed_pressures
            if langmuir_surface is not None and langmuir_surface.applied
            else closure_publication
        )
        vapor_pressures = {
            species: pressure
            for species, pressure in published_pressures.items()
            if species in active_species
        }
        if initial_closure_outcome is None:
            initial_closure_outcome = (
                EvaporationMixin._vapour_batch_resolution_outcome(
                    self,
                    resolve_error,
                    flux_overlay_report,
                    selector_overlay=selector_flux_overlay,
                    flux_pressures=vapor_pressures,
                    include_unresolved_notices=True,
                    requested_species_ids=requested_species_ids,
                    liquid_fraction=getattr(
                        equilibrium, 'liquid_fraction', None
                    ),
                    temperature_K=temperature_K,
                    equilibrium_status=getattr(equilibrium, 'status', None),
                    equilibrium_zero_reason=(
                        vapor_pressure_diagnostic.get(
                            'vapor_pressure_zero_reason'
                        )
                        or (getattr(equilibrium, 'diagnostics', {}) or {}).get(
                            'vapor_pressure_zero_reason'
                        )
                    ),
                    effective_pressure_zero_reason=(
                        effective_pressure_source.physical_zero_reason
                    ),
                )
            )
        if langmuir_surface is not None and langmuir_surface.applied:
            diagnostic = langmuir_surface.as_diagnostic()
            diagnostic["overhead_partials_Pa"] = dict(partials)
            diagnostic["overhead_pressure_Pa"] = film_pressure
            self._last_langmuir_surface_oxygen = diagnostic
        elif failed_root_diagnostic is not None:
            self._last_langmuir_surface_oxygen = failed_root_diagnostic
        self._last_vapour_batch_selector_overlay = copy.deepcopy(
            selector_flux_overlay
        )
        self._last_vapour_batch_terminal_outcome = initial_closure_outcome
        return (
            vapor_pressures,
            flux_overlay_report,
            vapour_batch,
            resolve_error,
            vapor_pressure_diagnostic,
            effective_pressure_source,
        )

    def _classify_hour_vapour_availability(
        self,
        equilibrium: Any,
        flux_pressures: dict[str, float],
        flux_overlay: dict[str, Any],
        unresolved_reasons: Mapping[str | None, str],
        *,
        observed_unavailable_by_species: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        """Freeze input-side unavailable species once for this simulation hour.

        Catalog, effective-pressure, stoichiometry, and transport inputs decide
        membership before surface closure. The same frozen exclusions are then
        reused by capacity trials and the final live publication.
        """
        raw_hour = getattr(getattr(self, 'melt', None), 'hour', None)
        try:
            hour = int(raw_hour)
        except (TypeError, ValueError, OverflowError):
            hour = None
        batch_identity = getattr(self, '_vapour_batch_identity', None)
        if batch_identity is None:
            batch_identity = id(getattr(self, 'inventory', self))
        cached = getattr(self, '_vapour_batch_hour_availability', None)
        channel_states = flux_overlay.get('batch_channel_states', {})
        channel_states = (
            channel_states if isinstance(channel_states, Mapping) else {}
        )
        if (
            isinstance(cached, Mapping)
            and cached.get('hour') == hour
            and cached.get('batch_identity') == batch_identity
            and isinstance(cached.get('unavailable_by_species'), Mapping)
        ):
            unavailable = {
                str(species): str(reason)
                for species, reason in cached[
                    'unavailable_by_species'
                ].items()
            }
            estimated_transport = dict(
                cached.get('estimated_lennard_jones_params_by_species', {})
                or {}
            )
            member_species = set(
                str(species)
                for species in cached.get('member_species', flux_pressures)
            )
            transport_property_rows = dict(
                cached.get('lennard_jones_property_rows_by_species', {}) or {}
            )
            transport_parameters = dict(
                cached.get('lennard_jones_parameters_by_species', {}) or {}
            )
            transport_eligibility = dict(
                cached.get('transport_eligibility_by_species', {}) or {}
            )
            hkl_upper_bound_species = set(
                cached.get('hkl_upper_bound_transport_species', ()) or ()
            )
            restarted_unavailable_species = set(
                cached.get('restarted_unavailable_species', ()) or ()
            )
            late_unavailability_restart_used = bool(
                cached.get('late_unavailability_restart_used', False)
            )
        else:
            unavailable = {
                str(species): str(reason)
                for species, reason in unresolved_reasons.items()
                if species is not None
            }
            estimated_transport = {}
            member_species = {
                str(species)
                for species in flux_pressures
                if channel_states.get(str(species)) != 'zero_by_physics'
            }
            transport_property_rows = {}
            transport_parameters = {}
            transport_eligibility = {}
            catalog_hkl_upper_bound_species = set(
                _load_hkl_upper_bound_transport_species(
                    getattr(self, 'vapor_pressures', {}) or {}
                )
            )
            hkl_upper_bound_species = set()
            restarted_unavailable_species = set()
            late_unavailability_restart_used = False
            self._langmuir_flux_closure_context = None
            from simulator.condensation import (
                _resolve_lennard_jones_parameters,
                _knudsen_number,
            )
            from simulator.transport_constants import (
                FREE_MOLECULAR_KNUDSEN_MIN,
            )
            vapor_data = getattr(self, 'vapor_pressures', {}) or {}
            gas_resistance_enabled = (
                EvaporationMixin._evaporation_gas_resistance_enabled(self)
            )
            for species in sorted(member_species):
                if species in unavailable:
                    continue
                if str(species) in catalog_hkl_upper_bound_species:
                    transport_eligibility[str(species)] = 'hkl_upper_bound'
                    hkl_upper_bound_species.add(str(species))
                    continue
                row = _evaporation_vapor_row(vapor_data, species)
                if row is not None:
                    transport_property_rows[str(species)] = dict(row)

                free_molecular_to_rating = False
                try:
                    capacity_policy = getattr(
                        self, '_cold_train_capacity_policy', None
                    )
                    if callable(capacity_policy):
                        _capacity, cold_train = capacity_policy()
                        rating_pa = float(
                            cold_train.relief['vessel_rating_Pa']
                        )
                    else:
                        current_partials = (
                            EvaporationMixin._langmuir_headspace_partials_Pa(
                                self, member_species
                            )
                        )
                        rating_pa = (
                            EvaporationMixin._evaporation_overhead_total_pressure_Pa(
                                self, current_partials
                            )
                        )
                    raw_temperature_k = getattr(
                        getattr(self, 'overhead', None),
                        'headspace_temperature_K',
                        None,
                    )
                    temperature_k = float(
                        raw_temperature_k
                        or (
                            float(self.melt.temperature_C)
                            + CELSIUS_TO_KELVIN_OFFSET
                        )
                    )
                    diameter_m = float(
                        getattr(
                            getattr(self, 'overhead_model', None),
                            'pipe_diameter_m',
                            0.12,
                        )
                        or 0.12
                    )
                    carrier_gas = EvaporationMixin._evaporation_carrier_gas(
                        self
                    )
                    if not isinstance(carrier_gas, str) or not carrier_gas:
                        raise ValueError('carrier gas is unavailable')
                    knudsen = _knudsen_number(
                        rating_pa,
                        temperature_k,
                        diameter_m,
                        carrier_gas=carrier_gas,
                    )
                    free_molecular_to_rating = (
                        math.isfinite(knudsen)
                        and knudsen >= FREE_MOLECULAR_KNUDSEN_MIN
                    ) or math.isinf(knudsen)
                except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
                    free_molecular_to_rating = False
                parameters = _resolve_lennard_jones_parameters(species, row)
                transport_parameters[str(species)] = dict(parameters)
                if parameters.get('status') == 'estimated':
                    estimated_transport[str(species)] = parameters
                if not gas_resistance_enabled:
                    transport_eligibility[str(species)] = (
                        'gas_resistance_disabled'
                    )
                    continue
                if free_molecular_to_rating:
                    # LJ estimates remain disclosed, but the rated regime
                    # contributes no gas resistance and needs no LJ input.
                    transport_eligibility[str(species)] = 'free_molecular'
                    continue
                if parameters.get('status') in {'tabulated', 'estimated'}:
                    transport_eligibility[str(species)] = 'chapman_enskog'
                else:
                    unavailable[str(species)] = (
                        'missing_transport_parameters_possible_continuum'
                    )
                    transport_eligibility[str(species)] = 'unavailable'

            for notice in flux_overlay.get('notices', ()) or ():
                if (
                    isinstance(notice, Mapping)
                    and notice.get('kind') == 'vapour_batch_unresolved'
                    and notice.get('species') is not None
                ):
                    unavailable.setdefault(
                        str(notice['species']),
                        str(notice.get('reason') or 'unavailable'),
                    )

            pressure_diagnostic = getattr(
                self, '_last_vapor_pressure_diagnostic', {}
            )
            pressure_diagnostic = (
                pressure_diagnostic
                if isinstance(pressure_diagnostic, Mapping)
                else {}
            )
        for species, state in channel_states.items():
            if state == 'zero_by_physics':
                unavailable.pop(str(species), None)
        for species in unavailable:
            if species in member_species:
                transport_eligibility[species] = 'unavailable'
        for species, reason in (observed_unavailable_by_species or {}).items():
            unavailable.setdefault(str(species), str(reason))
            transport_eligibility[str(species)] = 'unavailable'
        for species, reason in unavailable.items():
            if species in member_species:
                _append_vapour_batch_unresolved_notice(
                    self,
                    flux_overlay,
                    species=species,
                    reason=reason,
                )
        admitted_member_species = tuple(sorted(
            species
            for species in member_species
            if species not in unavailable
            and channel_states.get(species, 'eligible')
            in {'eligible', 'zero_by_physics'}
        ))
        estimated_notices = {
            str(species): _transport_parameters_estimated_notice(
                species=str(species),
                hour=hour,
                parameters=estimate,
            )
            for species, estimate in estimated_transport.items()
        }
        self._vapour_batch_hour_availability = {
            'batch_identity': batch_identity,
            'hour': hour,
            'member_species': tuple(sorted(member_species)),
            'admitted_member_species': admitted_member_species,
            'unavailable_by_species': dict(unavailable),
            'estimated_lennard_jones_params_by_species': dict(
                estimated_transport
            ),
            'transport_parameter_notices_by_species': estimated_notices,
            'lennard_jones_property_rows_by_species': dict(
                transport_property_rows
            ),
            'lennard_jones_parameters_by_species': dict(
                transport_parameters
            ),
            'transport_eligibility_by_species': dict(
                transport_eligibility
            ),
            'hkl_upper_bound_transport_species': tuple(
                sorted(hkl_upper_bound_species)
            ),
            'restarted_unavailable_species': tuple(
                sorted(restarted_unavailable_species)
            ),
            'late_unavailability_restart_used': (
                late_unavailability_restart_used
            ),
        }

        for species, reason in unavailable.items():
            flux_pressures.pop(species, None)
            _append_vapour_batch_unresolved_notice(
                self,
                flux_overlay,
                species=species,
                reason=reason,
            )
        return unavailable

    def _restart_for_late_unavailability(
        self,
        equilibrium: Any,
        flux_pressures: Mapping[str, Any],
        flux_overlay: dict[str, Any],
        unavailable_by_species: Mapping[str, str],
        *,
        capacity_solve: bool = False,
        capacity_restart_used: bool = False,
    ) -> dict[str, str] | None:
        """Exclude newly refused frozen members before deterministic re-close.

        One late-unavailability restart is shared by every cause in this
        batch hour. Frozen membership, rather than a current pressure map,
        decides which late failures can trigger that restart.
        """
        cache = getattr(self, '_vapour_batch_hour_availability', None)
        restarted = set(
            cache.get('restarted_unavailable_species', ())
            if isinstance(cache, Mapping)
            else ()
        )
        restart_used = bool(
            (isinstance(cache, Mapping) and cache.get(
                'late_unavailability_restart_used'
            ))
            or (capacity_solve and capacity_restart_used)
        )
        members = set(
            str(species)
            for species in (
                cache.get('member_species', flux_pressures)
                if isinstance(cache, Mapping)
                else flux_pressures
            )
        )
        fresh = {
            str(species): str(reason)
            for species, reason in unavailable_by_species.items()
            if str(species) in members
        }
        if not fresh:
            return None
        if restart_used:
            return {}
        EvaporationMixin._classify_hour_vapour_availability(
            self,
            equilibrium,
            dict(flux_pressures),
            flux_overlay,
            {},
            observed_unavailable_by_species=fresh,
        )
        cache = getattr(self, '_vapour_batch_hour_availability', None)
        if isinstance(cache, dict):
            restarted.update(fresh)
            cache['restarted_unavailable_species'] = tuple(sorted(restarted))
            cache['late_unavailability_restart_used'] = True
        return fresh

    def _restart_or_refuse_capacity_members(
        self,
        equilibrium: Any,
        flux_pressures: Mapping[str, Any],
        flux_overlay: dict[str, Any],
        unavailable_by_species: Mapping[str, str],
        *,
        capacity_restart_used: bool,
        detail_key: str,
        resolved_species: Iterable[str],
        capacity_solve: bool = True,
    ) -> dict[str, str]:
        bound_notice = (
            _append_capacity_solve_exclusion_notice(
                self,
                flux_overlay,
                resolved_species=resolved_species,
            )
            if capacity_solve
            else None
        )
        fresh = EvaporationMixin._restart_for_late_unavailability(
            self,
            equilibrium,
            flux_pressures,
            flux_overlay,
            unavailable_by_species,
            capacity_solve=capacity_solve,
            capacity_restart_used=capacity_restart_used,
        )
        if fresh is None:
            return {}
        if fresh:
            return fresh
        diagnostic = {
            'reason': 'capacity_member_closure_failure',
            detail_key: dict(unavailable_by_species),
            'vapour_batch_flux_overlay': flux_overlay,
            'bound_direction': dict(
                (bound_notice or {}).get('bound_direction', {})
            ),
        }
        raise EvaporationFluxRefusal(
            'capacity_member_closure_failure', diagnostic
        )

    def _evaporation_publisher_film(
        self,
        vapor_pressures: Mapping[str, float],
        overhead_partials_override_Pa: Mapping[str, float] | None,
    ) -> tuple[dict[str, float], float]:
        """Partial vector and transport pressure the flux kernel emits.

        Invariant: when this step closed a surface root, the kernel
        receives that root's film (``_langmuir_film`` stored it). The
        publisher does not rebuild the vector from the vapor-map keys.
        Rebuilding drops synthetic O2, so a commanded pressure below the
        overhead partial sum publishes a vacuum film under a root that
        used the O2 gas film.

        An explicit override is that evaluation's vector; the resolver
        already filmed it. A step that did not close a root and has no
        explicit vector keeps the vapor-map backpressure, zeros included.
        d-376 amendment 1 leaves that buffered in-band flux on green:
        the root did not run, and adopting its unused film would change
        the gas resistance of pressures the gate kept. The stored film
        still holds the complete persisted vector, non-vapor carriers
        included; this branch does not rebuild that vector from the
        vapor-map keys.
        """

        context = getattr(self, "_langmuir_flux_closure_context", None)
        root_closed = (
            isinstance(context, Mapping) and bool(context.get("root_closed"))
        )
        if root_closed or overhead_partials_override_Pa is not None:
            if (
                not isinstance(context, Mapping)
                or "film_partials_Pa" not in context
                or "film_pressure_Pa" not in context
            ):
                raise EvaporationFluxRefusal(
                    "langmuir_film_missing",
                    {
                        "reason": "langmuir_film_missing",
                        "detail": (
                            "evaporation flux requires the film stored by "
                            "the surface evaluation"
                        ),
                        "evaporation_flux_kg_hr": {},
                    },
                )
            partials = dict(context["film_partials_Pa"])
            for species in vapor_pressures:
                partials.setdefault(species, 0.0)
            return partials, float(context["film_pressure_Pa"])
        partials = {
            species: self._evaporation_bulk_partial_pressure_pa(species)
            for species in vapor_pressures
        }
        for species in vapor_pressures:
            partials.setdefault(species, 0.0)
        _cleaned, pressure = EvaporationMixin._langmuir_film(self, partials)
        return partials, pressure

    def _calculate_evaporation(
        self,
        equilibrium,
        *,
        gate_authority: Any = _RESOLVE_EVAPORATION_GATE_AUTHORITY,
        overhead_partials_override_Pa: Mapping[str, float] | None = None,
        capacity_bound_notice: Mapping[str, Any] | None = None,
    ) -> EvaporationFlux:
        """
        Calculate evaporation flux using a series-resistance source.

        For each volatile species, the mass flux from the melt surface is:

            J_i = (P_eq_i - P_bulk_i) /
                  (1/(alpha_i*k_HK_i) + R_gas_i(Kn) + R_melt_i(stir))

        where:
            alpha_i     = intrinsic Hertz-Knudsen evaporation coefficient
            k_HK_i      = sqrt(M_i / (2*pi*R*T))
            R_gas_i     = continuum-only gas-side Sherwood resistance
            R_melt_i    = 0 until species/state melt-transfer inputs exist
            A_surface   = melt surface area (m²)
            P_eq_i      = effective equilibrium pressure from VAPOR_PRESSURE (Pa)
            P_bulk_i    = partial pressure above the melt (Pa)
            M_i         = molar mass (kg/mol)
            R           = gas constant (J/mol·K)
            T           = temperature (K)

        The SiO suppression under pO₂ control is handled automatically:
        when pO₂ is elevated, the equilibrium vapor pressure of SiO
        drops by the factor √(pO₂), reducing the driving force.

        EVAPORATION_FLUX intent -- kernel-authoritative.

        \\goal BUILTIN-ENGINE-EXTRACTION (#7), second flip. The
        BuiltinEvaporationFluxProvider is the authoritative source for
        per-species kg/hr fluxes; this method builds the auxiliary maps
        the provider needs (vapor pressures, overhead partials, stoich,
        available oxide masses, molar masses, melt geometry) and routes
        the loop to ``kernel.dispatch(EVAPORATION_FLUX, ...)``. The
        legacy Hertz-Knudsen loop body lives inside the provider now;
        this method owns the precompute + the result projection. Shadow
        parity is the parametrised test in
        ``tests/chemistry/test_builtin_evaporation_flux_provider.py``;
        per the goal spec, the shadow comparator was removed at flip
        time (comparing against the same source is moot).

        Returns:
            EvaporationFlux with species_kg_hr dict
        """
        T_K = self.melt.temperature_C + CELSIUS_TO_KELVIN_OFFSET
        flux = EvaporationFlux()
        # A previous hour's computed rates must not leak into a tick that
        # returns before the publisher projection. The interval-zero
        # record is the same: a later real flux must not inherit it.
        self._wall_supply_computed_kg_hr = {}
        self._wall_supply_interval_physical_zero = None
        self._last_partial_melt_offgassing_diagnostic = {}
        if hasattr(self, "_last_langmuir_surface_oxygen"):
            del self._last_langmuir_surface_oxygen

        # Hostile uncertified melt model must refuse even below the 400 K
        # kinetic floor so cold early hours cannot mask the config as ok.
        kernel_config = dict(
            (getattr(self, 'setpoints', {}) or {}).get('chemistry_kernel', {})
            or {}
        )
        series_config = dict(
            kernel_config.get('evaporation_series_resistance', {}) or {}
        )
        if bool(series_config.get('melt_resistance_enabled', False)):
            diagnostic = {
                'evaporation_flux_kg_hr': {},
                'reason': 'uncertified_melt_resistance_model',
                'detail': (
                    'requires species- and state-specific D_i, k_L,i, '
                    'and dp_eq/dC_i'
                ),
            }
            self._last_evaporation_flux_diagnostic = diagnostic
            raise EvaporationFluxRefusal(
                'uncertified_melt_resistance_model',
                diagnostic,
            )

        # Precedence: no liquid = nothing to evaporate = physical zero,
        # regardless of resolver / vapor-pressure-map state.  Must run
        # BEFORE batch resolution so a bare or incomplete sim with a
        # proven-zero liquid fraction never reaches AttributeError or a
        # typed resolution refusal.  Resolution-error refusal (P1-3)
        # governs only when liquid exists (or liquid_fraction is unset
        # and the path cannot prove physical zero).
        vapor_pressure_diagnostic = getattr(
            self, '_last_vapor_pressure_diagnostic', {}
        )
        interpolated_provenance = _is_interpolated_vapour_equilibrium(
            equilibrium,
            vapor_pressure_diagnostic
            if isinstance(vapor_pressure_diagnostic, Mapping)
            else {},
        )
        liquid_fraction = getattr(equilibrium, 'liquid_fraction', None)
        if _zero_liquid_precedes_interpolation(equilibrium):
            self._last_vapour_batch = None
            self._last_vapour_batch_report = None
            self._last_vapour_batch_flux_overlay = {}
            self._last_vapour_batch_resolve_error = {}
            self._last_imcc_activity_shadow = {}
            note_wall_supply_interval_physical_zero(
                flux,
                reason="zero_liquid",
                host=self,
            )
            return flux

        # VR-11: batch owns channel refusal, eligibility, and active membership.
        # Pre-RG effective values cross only the named typed seam below.
        self._last_vapour_batch_selector_overlay = None
        (
            vapor_pressures,
            flux_overlay_report,
            vapour_batch,
            resolve_error,
            vapor_pressure_diagnostic,
            effective_pressure_source,
        ) = self._resolve_evaporation_batch_flux_state(
            equilibrium,
            temperature_K=T_K,
            overhead_partials_Pa=overhead_partials_override_Pa,
            interpolated_provenance=interpolated_provenance,
        )
        selector_flux_overlay = _take_vapour_batch_selector_overlay(
            self, flux_overlay_report
        )
        outcome = _take_vapour_batch_terminal_outcome(self)
        if outcome is None:
            # Resolver test doubles retain the public six-value seam. The
            # production resolver stores its initial closure decision above.
            outcome = EvaporationMixin._vapour_batch_resolution_outcome(
                self,
                resolve_error,
                flux_overlay_report,
                selector_overlay=selector_flux_overlay,
                flux_pressures=vapor_pressures,
                include_unresolved_notices=True,
                requested_species_ids=(
                    getattr(vapour_batch, 'requested_species_ids', None)
                    if vapour_batch is not None
                    else None
                ),
                liquid_fraction=liquid_fraction,
                temperature_K=T_K,
                equilibrium_status=(
                    None
                    if interpolated_provenance
                    else getattr(equilibrium, 'status', None)
                ),
                equilibrium_zero_reason=(
                    None
                    if interpolated_provenance
                    else vapor_pressure_diagnostic.get(
                        'vapor_pressure_zero_reason'
                    )
                    or (getattr(equilibrium, 'diagnostics', {}) or {}).get(
                        'vapor_pressure_zero_reason'
                    )
                ),
                effective_pressure_zero_reason=(
                    effective_pressure_source.physical_zero_reason
                ),
            )
        resolved_channel_states = flux_overlay_report.get(
            'batch_channel_states', {}
        )
        typed_zero_species = {
            str(species)
            for species, state in (
                resolved_channel_states.items()
                if isinstance(resolved_channel_states, Mapping)
                else ()
            )
            if state == 'zero_by_physics'
        }
        vapor_pressures = {
            str(species): pressure
            for species, pressure in vapor_pressures.items()
            if str(species) not in typed_zero_species
        }
        capacity_bound = None
        if (
            isinstance(capacity_bound_notice, Mapping)
            and capacity_bound_notice.get('kind')
            == 'capacity_solve_excludes_unavailable_species'
        ):
            capacity_bound = dict(capacity_bound_notice)
            notices = flux_overlay_report.setdefault('notices', [])
            if capacity_bound not in notices:
                notices.append(capacity_bound)
        batch_report = serialize_vapour_batch(vapour_batch)
        if isinstance(batch_report, Mapping):
            channels = batch_report.get('channels_by_species')
            if isinstance(channels, Mapping):
                flux.carrier_authority_by_species = {
                    str(species): dict(record)
                    for species, record in channels.items()
                    if isinstance(record, Mapping)
                }
        EvaporationMixin._carry_classified_vapour_batch_results(
            self, batch_report, flux_overlay_report, flux
        )
        if capacity_bound is not None:
            for authority in flux.carrier_authority_by_species.values():
                authority['capacity_solve_excludes_unavailable_species'] = (
                    dict(capacity_bound)
                )
        self._last_vapour_batch = vapour_batch
        self._last_vapour_batch_report = batch_report
        self._last_vapour_batch_flux_overlay = flux_overlay_report
        imcc_shadow = None
        batch_shadow = getattr(vapour_batch, "melt_activity_shadow", None)
        if hasattr(batch_shadow, "as_mapping"):
            batch_shadow = batch_shadow.as_mapping()
        if isinstance(batch_shadow, Mapping):
            imcc_shadow = batch_shadow.get("imcc_activity_shadow")
        self._last_imcc_activity_shadow = (
            dict(imcc_shadow) if isinstance(imcc_shadow, Mapping) else {}
        )
        langmuir_notice = getattr(self, "_last_langmuir_surface_oxygen", None)
        if (
            not interpolated_provenance
            and isinstance(langmuir_notice, dict)
            and langmuir_notice.get("warning")
        ):
            equilibrium_warnings = getattr(equilibrium, "warnings", None)
            if isinstance(equilibrium_warnings, list):
                # One surface-balance notice per equilibrium. The pO2 and
                # residual text change every call; replace the previous
                # notice instead of appending a new string each hour.
                from simulator.langmuir_surface_oxygen import (
                    LANGMUIR_SURFACE_PO2_REASON,
                )

                warning = str(langmuir_notice["warning"])
                equilibrium_warnings[:] = [
                    item
                    for item in equilibrium_warnings
                    if LANGMUIR_SURFACE_PO2_REASON not in str(item)
                ]
                equilibrium_warnings.append(warning)
        extrapolated_flux_species = tuple(
            flux_overlay_report.get('extrapolated_flux_species', ()) or ()
        )
        if extrapolated_flux_species:
            self._record_degraded_path_engagement(
                'vapour_pressure_extrapolation',
                count=len(extrapolated_flux_species),
            )

        # b-149 instance 4: partial channel refusals. When some channels debit
        # and others are typed refusals, refused species drop out of
        # vapor_pressures. Their unresolved overlay notice keeps that absence
        # typed as unavailable while eligible peer species can proceed.
        _channel_states_partial = dict(
            flux_overlay_report.get('batch_channel_states', {}) or {}
        )
        _partial_sz_notes: list = []
        if _channel_states_partial:
            from simulator.silent_zero import (
                CATEGORY_REFUSE,
                ZeroBecause,
                note_dict,
                record_on_host,
            )

            for _sp, _state in sorted(_channel_states_partial.items()):
                if str(_state) != 'refusal':
                    continue
                if _sp in vapor_pressures:
                    continue
                _n = note_dict(
                    ZeroBecause.REFUSED_UPSTREAM,
                    site='evaporation.partial_channel_refusal',
                    species=str(_sp),
                    field='batch_channel_state',
                    detail=(
                        f'channel state=refusal while peer channels still '
                        f'debit; species unavailable in the flux map, not '
                        f'measured as zero. Counted as category-1 missing input; '
                        f'hard raise deferred pending blast-radius rebaseline.'
                    ),
                    doctrine_category=CATEGORY_REFUSE,
                )
                _partial_sz_notes.append(_n)
                record_on_host(
                    self,
                    ZeroBecause.REFUSED_UPSTREAM,
                    site='evaporation.partial_channel_refusal',
                    species=str(_sp),
                    field='batch_channel_state',
                    detail=_n['detail'],
                    doctrine_category=CATEGORY_REFUSE,
                )
        if outcome.refusal is not None:
            self._last_evaporation_flux_diagnostic = outcome.refusal.diagnostic
            raise outcome.refusal
        if outcome.reason == 'proven_empty_inventory':
            # Empty exact-key membership proves no positive eligible parent
            # inventory. Keep the existing typed silent-zero publication.
            from simulator.silent_zero import (
                CATEGORY_PROVEN_ZERO,
                ZeroBecause,
                merge_notes_into_mapping,
                note_dict,
                record_on_host,
            )

            sz_note = note_dict(
                ZeroBecause.PROVEN_EMPTY_INVENTORY,
                site='evaporation.empty_requested_species_ids',
                field='requested_species_ids',
                detail=(
                    'empty vapour request + liquid admits inventory zero → '
                    'empty flux (reason=no_volatile_species_or_positive_parent_'
                    'activity). build_request is the sole request constructor '
                    'and membership is parent-inventory-only, so an empty '
                    'exact-key request proves no positive eligible parent '
                    'inventory (proven-empty, cat-3).'
                ),
                doctrine_category=CATEGORY_PROVEN_ZERO,
            )
            diagnostic = {
                'evaporation_flux_kg_hr': {},
                'reason': 'no_volatile_species_or_positive_parent_activity',
                'vapour_batch_flux_overlay': flux_overlay_report,
            }
            merge_notes_into_mapping(diagnostic, [sz_note])
            record_on_host(
                self,
                ZeroBecause.PROVEN_EMPTY_INVENTORY,
                site='evaporation.empty_requested_species_ids',
                field='requested_species_ids',
                detail=sz_note['detail'],
                doctrine_category=CATEGORY_PROVEN_ZERO,
            )
            self._last_evaporation_flux_diagnostic = diagnostic
            note_wall_supply_interval_physical_zero(
                flux,
                reason='proven_empty_inventory',
                host=self,
            )
            return flux
        if outcome.reason in {'below_400K', 'internal_analytical_below_400K'}:
            self._last_evaporation_flux_diagnostic = {
                **dict(outcome.evidence),
                'reason': outcome.reason,
                'evaporation_flux_kg_hr': {},
                'vapour_batch': batch_report,
            }
            note_wall_supply_interval_physical_zero(
                flux,
                reason='below_400K',
                host=self,
            )
            return flux
        if (
            effective_pressure_source.physical_zero_reason is not None
            and outcome.reason == effective_pressure_source.physical_zero_reason
        ):
            self._last_evaporation_flux_diagnostic = {
                **dict(outcome.evidence),
                'reason': outcome.reason,
                'evaporation_flux_kg_hr': {},
                'vapour_batch': batch_report,
            }
            return flux
        if outcome.reason == 'typed_non_debit':
            self._last_evaporation_flux_diagnostic = {
                **dict(outcome.evidence),
                'reason': 'vapour_batch_all_channels_non_debiting',
                'evaporation_flux_kg_hr': {},
                'vapour_batch': batch_report,
            }
            return flux
        if interpolated_provenance:
            self._last_partial_melt_offgassing_diagnostic = {}
        else:
            self._last_partial_melt_offgassing_diagnostic = (
                self._build_partial_melt_offgassing_diagnostic(
                    equilibrium,
                    vapor_pressures=vapor_pressures,
                    vapor_pressure_diagnostic=vapor_pressure_diagnostic,
                )
            )

        # Overhead backpressure (Pa)                       [LOOP-1]
        # Uses the previous hour's upstream melt-headspace/duct partials as
        # backpressure. ``overhead.composition`` is the physically distinct
        # post-condensation report, so near-total capture must not erase P_bulk
        # above the melt. The flux map was already closed on the film stored
        # by ``_resolve_evaporation_batch_flux_state`` (the override when the
        # capacity shadow supplies one). The kernel receives that film from
        # ``_evaporation_publisher_film``. Do not rebuild it from the vapor
        # map: that drops synthetic O2. Do not solve again here: a second
        # pass would scale the adjusted pressures as if they were still the
        # persisted melt-fO2 publication. A different partial vector
        # re-enters from the stored publication via
        # ``_reclose_langmuir_pressures``. equilibrium vapor pressures
        # stay the melt-supported law.
        if interpolated_provenance:
            overhead_partials_Pa = {}
            overhead_pressure_Pa = 0.0
        else:
            overhead_partials_Pa, overhead_pressure_Pa = (
                EvaporationMixin._evaporation_publisher_film(
                    self,
                    vapor_pressures,
                    overhead_partials_override_Pa,
                )
            )

        # F-B1: EVAPORATION_FLUX is read-only -- no commit_batch follows.
        # The dispatch-only helper centralises melt-derived T/P plumbing
        # so this call site stays in lock-step with the rest of the
        # simulator's kernel callers.
        pre_aux_vapor_pressures = dict(vapor_pressures)
        if interpolated_provenance:
            # Preserve the shared control shape without reopening any
            # equilibrium or catalog vapour inputs after provenance refusal.
            controls = EvaporationMixin._evaporation_provider_controls_for_state(
                self,
                {},
                overhead_partials_Pa={},
                overhead_pressure_pa=0.0,
                flux_pressures={},
                flux_overlay=flux_overlay_report,
                surface_oxygen_bound=False,
            )
            control_result = _EvaporationFluxControlResult(
                controls=controls,
                molar_masses_kg_mol={},
                unavailable_by_species={},
                transport_sidecar={},
            )
        else:
            control_result = EvaporationMixin._evaporation_flux_control_inputs(
                self,
                equilibrium,
                overhead_partials_Pa=overhead_partials_Pa,
                overhead_pressure_pa=overhead_pressure_Pa,
                vapour_batch_flux_pressures_Pa=vapor_pressures,
                vapour_batch_report=batch_report,
                vapour_batch_flux_overlay=flux_overlay_report,
            )
        control_inputs, molar_masses_kg_mol = control_result
        control_vapor_pressures = dict(
            control_inputs[CONTROL_FLUX_PRESSURES_KEY] or {}
            if CONTROL_FLUX_PRESSURES_KEY in control_inputs
            else vapor_pressures
        )
        new_aux_parent_failures = dict(
            getattr(control_result, 'unavailable_by_species', {}) or {}
        )
        if new_aux_parent_failures:
            late_parent_species = EvaporationMixin._restart_or_refuse_capacity_members(
                self,
                equilibrium,
                pre_aux_vapor_pressures,
                flux_overlay_report,
                new_aux_parent_failures,
                capacity_restart_used=False,
                detail_key='auxiliary_map_unavailable',
                resolved_species=vapor_pressures,
                capacity_solve=False,
            )
            if late_parent_species:
                return EvaporationMixin._calculate_evaporation(
                    self,
                    equilibrium,
                    gate_authority=gate_authority,
                    overhead_partials_override_Pa=(
                        overhead_partials_override_Pa
                    ),
                    capacity_bound_notice=capacity_bound_notice,
                )
        _publish_transport_parameter_notices(
            flux_overlay_report,
            getattr(control_result, 'transport_sidecar', {}),
        )
        vapor_pressures = control_vapor_pressures
        kernel_result = self._dispatch_only(
            ChemistryIntent.EVAPORATION_FLUX,
            control_inputs=control_inputs,
        )
        diagnostic = dict(kernel_result.diagnostic or {})
        if batch_report is not None:
            diagnostic[CONTROL_BATCH_REPORT_KEY] = batch_report
        diagnostic['vapour_batch_flux_overlay'] = flux_overlay_report
        # Never default missing proof to True (SC-03 / SC-10).
        if 'shadow_equal' in flux_overlay_report:
            diagnostic[CONTROL_SHADOW_EQUAL_KEY] = bool(
                flux_overlay_report['shadow_equal']
            )
        diagnostic['vapour_batch_flux_shadow_outcome'] = (
            flux_overlay_report.get('shadow_outcome')
        )
        if _partial_sz_notes:
            # b-149 dead-write fix: merge the partial-refusal notes into the
            # final kernel diagnostic. They were previously written to
            # _last_evaporation_flux_diagnostic before the kernel dispatch and
            # then clobbered by this fresh assignment — instrumentation lost.
            from simulator.silent_zero import merge_notes_into_mapping

            merge_notes_into_mapping(diagnostic, _partial_sz_notes)
        langmuir_notice = getattr(self, "_last_langmuir_surface_oxygen", None)
        if isinstance(langmuir_notice, dict):
            diagnostic["langmuir_surface_oxygen"] = langmuir_notice
        self._last_evaporation_flux_diagnostic = diagnostic
        provider_unavailable = _record_unavailable_provider_flux_channels(
            self,
            diagnostic,
            flux_overlay_report,
            expected_species=vapor_pressures,
            flux=flux,
            provider_status=getattr(kernel_result, 'status', None),
        )
        diagnostic['vapour_batch_flux_overlay'] = flux_overlay_report
        if provider_unavailable:
            fresh_provider_unavailable = (
                EvaporationMixin._restart_or_refuse_capacity_members(
                    self,
                    equilibrium,
                    vapor_pressures,
                    flux_overlay_report,
                    provider_unavailable,
                    capacity_restart_used=False,
                    detail_key='provider_unavailable',
                    resolved_species=flux_overlay_report.get(
                        'batch_channel_states', {}
                    ),
                    capacity_solve=False,
                )
            )
            if fresh_provider_unavailable:
                return EvaporationMixin._calculate_evaporation(
                    self,
                    equilibrium,
                    gate_authority=gate_authority,
                    overhead_partials_override_Pa=(
                        overhead_partials_override_Pa
                    ),
                    capacity_bound_notice=capacity_bound_notice,
                )
        continuum_notice = diagnostic.get('continuum_extrapolation_notice')
        condensation_model = getattr(self, 'condensation_model', None)
        if continuum_notice and condensation_model is not None:
            from simulator.wall_deposition import _record_wall_pressure_notice

            for species, series in diagnostic.get('evaporation_series_resistance', {}).items():
                if notice := series.get('continuum_extrapolation_notice'):
                    _record_wall_pressure_notice(
                        condensation_model, 'evaporation_transport_notices_by_species',
                        species, 'evaporation', notice,
                    )
            for species, notice in diagnostic.get('missing_transport_parameters', {}).items():
                _record_wall_pressure_notice(
                    condensation_model, 'evaporation_transport_notices_by_species',
                    species, 'evaporation', {
                        **notice, 'temperature_K': continuum_notice['gas_temperature_K'],
                    },
                )
        alpha_authority_statuses = dict(
            diagnostic.get('alpha_authority_status_by_species', {}) or {}
        )
        unmeasured_alpha_species = tuple(
            diagnostic.get('unmeasured_alpha_fallback_species', ()) or ()
        )
        if unmeasured_alpha_species:
            engaged_species = set(
                getattr(
                    self,
                    '_unmeasured_alpha_fallback_species_engaged',
                    (),
                )
                or ()
            )
            engaged_species.update(str(species) for species in unmeasured_alpha_species)
            self._unmeasured_alpha_fallback_species_engaged = tuple(
                sorted(engaged_species)
            )
            self._record_degraded_path_engagement(
                'unmeasured_alpha_evaporation_fallback',
                count=len(unmeasured_alpha_species),
            )
        provider_refusal = _provider_flux_outcome_refusal(
            self,
            kernel_result.status,
            diagnostic,
            provider_unavailable,
            flux_overlay_report,
            selector_overlay=selector_flux_overlay,
        )
        if provider_refusal is not None:
            self._last_evaporation_flux_diagnostic = provider_refusal.diagnostic
            raise provider_refusal
        flux_kg_hr = diagnostic.get('evaporation_flux_kg_hr') or {}
        liquid_fraction_factor = 1.0
        if flux_kg_hr and self._freeze_gate_enabled():
            if gate_authority is _RESOLVE_EVAPORATION_GATE_AUTHORITY:
                liquid_fraction_factor = (
                    self._freeze_gate_liquid_fraction_factor()
                )
            else:
                liquid_fraction_factor = self._freeze_gate_liquid_fraction_factor(
                    gate_authority=gate_authority,
                )
            if (
                melt_regime(liquid_fraction=liquid_fraction_factor)
                == MeltRegime.FROZEN
            ):
                # Frozen melt: this early return is the pre-existing gate.
                # It runs only when the published bucket is non-empty, so a
                # below-only hour is still projected below. Expanding the
                # gate to that hour would ask for a liquid-fraction curve
                # the published bucket never needed. Default setpoint: off.
                # The published rates are not the supply: the melt is
                # frozen, so this hour evaporates nothing. The empty map
                # needs the interval record or the wall integral withholds
                # every species for the whole run.
                note_wall_supply_interval_physical_zero(
                    flux,
                    reason="frozen_before_evaporation",
                    host=self,
                )
                flux.update_totals()
                return flux
        below_fluxes: Mapping[str, Any] = {}
        below_payload = diagnostic.get('below_threshold_evaporation_flux')
        if isinstance(below_payload, Mapping):
            raw_below = below_payload.get('fluxes_kg_hr')
            if isinstance(raw_below, Mapping):
                below_fluxes = raw_below
        # Fe in either publisher bucket shares the residual cap. The
        # allocation record stays on the published bucket only, which is
        # what project_publisher_vapour_rates records.
        fe_in_publisher = (
            (isinstance(flux_kg_hr, Mapping) and 'Fe' in flux_kg_hr)
            or 'Fe' in below_fluxes
        )
        fe_residual_kg_hr = None
        if fe_in_publisher:
            residual_capacity_mol = getattr(
                self,
                '_native_fe_vapor_residual_capacity_mol_this_hr',
                None,
            )
            if residual_capacity_mol is not None:
                fe_molar_mass = float(molar_masses_kg_mol.get('Fe', 0.0) or 0.0)
                # Explicit scheduling policy, not an activity calculation:
                # native-pool Fe claims the shared transport capacity first,
                # then melt-derived Fe is capped at the residual molar
                # capacity times M_Fe [kg/mol], yielding kg/h. No pool or
                # melt activity is an operand here. Limiting cases: zero
                # residual gives zero melt-derived Fe; residual >= raw
                # rate/M_Fe leaves the raw melt-derived rate unchanged.
                fe_residual_kg_hr = (
                    max(0.0, float(residual_capacity_mol)) * fe_molar_mass
                )
        projected = project_publisher_vapour_rates(
            diagnostic,
            liquid_fraction_factor=liquid_fraction_factor,
            fe_residual_kg_hr=fe_residual_kg_hr,
        )
        if projected.fe_allocation is not None:
            diagnostic['native_fe_capacity_allocation'] = projected.fe_allocation
        for species, gated_rate_kg_hr in projected.debit_kg_hr.items():
            flux.species_kg_hr[species] = gated_rate_kg_hr
            alpha_authority_status = alpha_authority_statuses.get(species)
            if alpha_authority_status is not None:
                flux.alpha_authority_status_by_species[species] = str(
                    alpha_authority_status
                )
        # Supply keeps the sub-floor rates the debit map just dropped.
        # Depletion and the C0 scales update this map in place; the
        # snapshot writer is publish_wall_supply_on_flux.
        self._wall_supply_computed_kg_hr = dict(projected.supply_kg_hr)

        flux.update_totals()
        return flux

    def _build_partial_melt_offgassing_diagnostic(
        self,
        equilibrium: Any,
        *,
        vapor_pressures: Mapping[str, float],
        vapor_pressure_diagnostic: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Estimate partial-melt alkali vapor undercount without changing flux.

        DeMaria et al. 1971 grounds only the existence of Na/K vapor below the
        Apollo 12022 liquidus. It does not establish the fallback partition
        coefficients or their composition, phase, temperature, or pressure
        validity domain. Phase-engine ``liquid_composition_wt_pct`` is preferred;
        when only F(T) exists, the WARN-tier estimate uses
        ``C_liquid/C_bulk = 1 / (D + F * (1 - D))``: bulk balance gives
        ``C_bulk = F*C_liquid + (1-F)*C_solid`` and
        ``D = C_solid/C_liquid``, so division by ``C_bulk`` yields that
        dimensionless ratio. Sanity checks: F=1 gives ratio 1, while D=0 gives
        1/F. The estimate remains explicitly uncertified; the correct numerical
        D values and validity windows are unestablished here.
        """
        fraction = self._partial_melt_fraction_diagnostic(equilibrium)
        liquid_comp, liquid_comp_source = (
            self._phase_engine_liquid_composition_wt_pct(equilibrium)
        )
        bulk_single_cation = self._bulk_single_cation_mole_fractions(
            vapor_pressure_diagnostic
        )
        liquid_single_cation = (
            self._single_cation_mole_fractions_from_wt_pct(liquid_comp)
            if liquid_comp
            else {}
        )

        component_details: dict[str, dict[str, Any]] = {}
        p_bulk_current: dict[str, float] = {}
        p_partial_liquid: dict[str, float] = {}
        ratios: dict[str, float] = {}
        bulk_x: dict[str, float] = {}
        liquid_x: dict[str, float] = {}
        enrichment: dict[str, float] = {}
        sources: set[str] = set()
        warnings: list[str] = []

        for vapor_species, spec in _PARTIAL_MELT_OFFGASSING_COMPONENTS.items():
            current_pressure = self._optional_float(
                vapor_pressures.get(vapor_species)
            )
            if current_pressure is None or current_pressure <= 0.0:
                continue

            parent_oxide = str(spec['parent_oxide'])
            component = str(spec['component'])
            bulk_fraction = self._optional_float(
                bulk_single_cation.get(parent_oxide)
            )
            if bulk_fraction is None or bulk_fraction <= 0.0:
                continue

            source = ''
            liquid_fraction = self._optional_float(
                liquid_single_cation.get(parent_oxide)
            )
            if liquid_fraction is not None:
                source = liquid_comp_source
            else:
                F = self._optional_float(fraction.get('melt_fraction_F'))
                if F is None:
                    continue
                try:
                    regime = melt_regime(liquid_fraction=F)
                except (TypeError, ValueError):
                    continue
                if regime == MeltRegime.FROZEN:
                    continue
                partition_coefficient = float(spec['partition_coefficient'])
                factor = 1.0 / (
                    partition_coefficient + F * (1.0 - partition_coefficient)
                )
                liquid_fraction = min(1.0, bulk_fraction * factor)
                source = 'analytical_batch_partition_fallback'
                warnings.append(
                    'WARN-tier analytical partition fallback used for '
                    f'{component}; phase-engine liquid composition absent'
                )

            ratio = liquid_fraction / bulk_fraction
            enriched_pressure = current_pressure * ratio
            sources.add(source)
            p_bulk_current[vapor_species] = current_pressure
            p_partial_liquid[vapor_species] = enriched_pressure
            ratios[vapor_species] = ratio
            bulk_x[component] = bulk_fraction
            liquid_x[component] = liquid_fraction
            enrichment[component] = ratio
            component_details[component] = {
                'vapor_species': vapor_species,
                'parent_oxide': parent_oxide,
                'bulk_single_cation_mole_fraction': bulk_fraction,
                'estimated_liquid_single_cation_mole_fraction': liquid_fraction,
                'enrichment_factor': ratio,
                'p_bulk_current': current_pressure,
                'p_partial_liquid_diagnostic': enriched_pressure,
                'p_ratio_partial_over_bulk': ratio,
                'liquid_composition_source': source,
            }
            if source == 'analytical_batch_partition_fallback':
                component_details[component].update({
                    'partition_model': 'batch_equilibrium',
                    'partition_coefficient': float(
                        spec['partition_coefficient']
                    ),
                    'partition_coefficient_range': tuple(
                        spec['partition_coefficient_range']
                    ),
                })

        if component_details:
            status = (
                'ENGINE_DERIVED'
                if sources == {liquid_comp_source} and liquid_comp
                else 'UNCERTIFIED_PARAMETERIZED_ESTIMATE'
            )
        else:
            status = 'UNAVAILABLE'

        payload = {
            'status': status,
            'T_K': float(self.melt.temperature_C) + CELSIUS_TO_KELVIN_OFFSET,
            'solidus_K': fraction.get('solidus_K'),
            'liquidus_K': fraction.get('liquidus_K'),
            'melt_fraction_F': fraction.get('melt_fraction_F'),
            'melt_regime': fraction.get('melt_regime'),
            'F_source': fraction.get('F_source'),
            'liquid_composition_source': (
                'mixed' if len(sources) > 1 else next(iter(sources), '')
            ),
            'bulk_oxide_mole_fraction': bulk_x,
            'estimated_liquid_oxide_mole_fraction': liquid_x,
            'enrichment_factor_by_component': enrichment,
            'p_bulk_current': p_bulk_current,
            'p_partial_liquid_diagnostic': p_partial_liquid,
            'p_ratio_partial_over_bulk': ratios,
            'surface_fraction_model': 'pressure_only_no_surface_fraction_applied',
            'golden_authoritative': False,
            'component_details': component_details,
            'diagnostic_basis': (
                'alkali vapor pressure scales linearly with single-cation '
                'alkali oxide activity in the current vapor-pressure provider'
            ),
        }
        reasons = tuple(fraction.get('unavailable_reasons') or ())
        if reasons:
            payload['unavailable_reasons'] = reasons
        payload['warnings'] = tuple(dict.fromkeys(warnings))
        return payload

    def _partial_melt_fraction_diagnostic(
        self,
        equilibrium: Any,
    ) -> dict[str, Any]:
        diagnostics = self._equilibrium_diagnostic_mapping(equilibrium)
        reasons: list[str] = []
        solidus_T_C = self._optional_float(
            getattr(equilibrium, 'solidus_T_C', None)
        )
        if solidus_T_C is None:
            solidus_T_C = self._optional_float(diagnostics.get('solidus_T_C'))
        liquidus_T_C = self._optional_float(
            getattr(equilibrium, 'liquidus_T_C', None)
        )
        if liquidus_T_C is None:
            liquidus_T_C = self._optional_float(diagnostics.get('liquidus_T_C'))
        if liquidus_T_C is None:
            liquidus_T_K = self._optional_float(diagnostics.get('liquidus_T_K'))
            if liquidus_T_K is not None:
                liquidus_T_C = liquidus_T_K - CELSIUS_TO_KELVIN_OFFSET

        F = self._optional_float(getattr(equilibrium, 'liquid_fraction', None))
        source = ''
        if F is not None and 0.0 <= F <= 1.0:
            source = 'phase_engine:equilibrium.liquid_fraction'
        else:
            F = self._optional_float(diagnostics.get('liquid_fraction'))
            if F is not None and 0.0 <= F <= 1.0:
                source = 'phase_engine:diagnostic.liquid_fraction'

        if F is None and solidus_T_C is not None and liquidus_T_C is not None:
            curve = self._freeze_gate_curve_from_bounds(
                solidus_T_C=solidus_T_C,
                liquidus_T_C=liquidus_T_C,
                source='liquidus_solidus:equilibrium_diagnostic_bounds',
                reasons=reasons,
            )
            if curve is not None:
                F = self._interpolate_freeze_gate_curve(
                    curve,
                    float(self.melt.temperature_C),
                )
                source = str(curve['source'])

        regime = None
        if F is not None:
            try:
                regime = melt_regime(liquid_fraction=F).value
            except (TypeError, ValueError) as exc:
                reasons.append(f'melt_regime_invalid_liquid_fraction: {exc}')
                F = None

        if F is None:
            reasons.append('melt_fraction_unavailable')
        return {
            'melt_fraction_F': F,
            'F_source': source,
            'melt_regime': regime,
            'solidus_K': (
                solidus_T_C + CELSIUS_TO_KELVIN_OFFSET if solidus_T_C is not None else None
            ),
            'liquidus_K': (
                liquidus_T_C + CELSIUS_TO_KELVIN_OFFSET if liquidus_T_C is not None else None
            ),
            'unavailable_reasons': tuple(dict.fromkeys(reasons)),
        }

    def _phase_engine_liquid_composition_wt_pct(
        self,
        equilibrium: Any,
    ) -> tuple[dict[str, float], str]:
        direct = self._finite_mapping(
            getattr(equilibrium, 'liquid_composition_wt_pct', {}) or {}
        )
        if direct:
            return direct, 'phase_engine:equilibrium.liquid_composition_wt_pct'

        diagnostics = self._equilibrium_diagnostic_mapping(equilibrium)
        diagnostic_direct = self._finite_mapping(
            diagnostics.get('liquid_composition_wt_pct') or {}
        )
        if diagnostic_direct:
            return diagnostic_direct, 'phase_engine:diagnostic.liquid_composition_wt_pct'

        path_value = diagnostics.get('liquid_fraction_path') or ()
        path = path_value if isinstance(path_value, tuple | list) else ()
        current_T_C = float(self.melt.temperature_C)
        best: tuple[float, dict[str, float]] | None = None
        for point in path:
            if not isinstance(point, Mapping):
                continue
            temperature_C = self._optional_float(
                point.get('temperature_C', point.get('T_C'))
            )
            composition = self._finite_mapping(
                point.get('liquid_composition_wt_pct') or {}
            )
            if temperature_C is None or not composition:
                continue
            distance = abs(temperature_C - current_T_C)
            if best is None or distance < best[0]:
                best = (distance, composition)
        if best is not None:
            return best[1], 'phase_engine:diagnostic.liquid_fraction_path'
        return {}, ''

    @staticmethod
    def _finite_mapping(values: Mapping[str, Any]) -> dict[str, float]:
        if not isinstance(values, Mapping):
            return {}
        out: dict[str, float] = {}
        for key, value in dict(values).items():
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number) and number > 0.0:
                out[str(key)] = number
        return out

    def _single_cation_mole_fractions_from_wt_pct(
        self,
        wt_pct: Mapping[str, float],
    ) -> dict[str, float]:
        account_mol: dict[str, float] = {}
        for species, wt in wt_pct.items():
            molar_mass = MOLAR_MASS.get(str(species))
            if molar_mass is None or molar_mass <= 0.0:
                continue
            value = self._optional_float(wt)
            if value is None or value <= 0.0:
                continue
            account_mol[str(species)] = value / molar_mass
        return single_cation_mole_fractions(account_mol)

    def _bulk_single_cation_mole_fractions(
        self,
        vapor_pressure_diagnostic: Mapping[str, Any],
    ) -> dict[str, float]:
        try:
            projection = getattr(self.atom_ledger, 'project_account_mol', None)
            account_mol = (
                projection(_FREEZE_GATE_ACCOUNT)
                if callable(projection)
                else self.atom_ledger.mol_by_account(_FREEZE_GATE_ACCOUNT)
            )
        except AttributeError:
            account_mol = {}
        fractions = dict(single_cation_mole_fractions(account_mol))
        provenance = dict(
            vapor_pressure_diagnostic.get('vapor_pressure_numerator_provenance')
            or {}
        )
        for vapor_species, spec in _PARTIAL_MELT_OFFGASSING_COMPONENTS.items():
            parent_oxide = str(spec['parent_oxide'])
            species_provenance = provenance.get(vapor_species)
            if not isinstance(species_provenance, Mapping):
                continue
            value = self._optional_float(
                species_provenance.get('melt_oxide_X_single_cation')
            )
            if value is not None and value > 0.0:
                fractions[parent_oxide] = value
        return fractions

    @staticmethod
    def _equilibrium_diagnostic_mapping(equilibrium: Any) -> dict[str, Any]:
        diagnostics: dict[str, Any] = {}
        for attr in (
            'diagnostics',
            'alphamelts_diagnostics',
            'magemin_diagnostics',
        ):
            value = getattr(equilibrium, attr, None)
            if isinstance(value, Mapping):
                diagnostics.update(dict(value))
        backend_diagnostics = diagnostics.get('backend_diagnostics')
        if isinstance(backend_diagnostics, Mapping):
            diagnostics.update(dict(backend_diagnostics))
        return diagnostics

    def _freeze_gate_liquid_fraction_factor(
        self,
        *,
        gate_authority: Any = _RESOLVE_EVAPORATION_GATE_AUTHORITY,
    ) -> float:
        from simulator.reduced_real_determinism import (
            GATE_CURVE_DOMAIN_KEYS,
            GATE_CURVE_NOTICE_KEYS,
        )

        if gate_authority is _RESOLVE_EVAPORATION_GATE_AUTHORITY:
            curve = self._resolved_melt_redox_gate_authority()
        else:
            curve = self._resolved_melt_redox_gate_authority(gate_authority)
        if not isinstance(curve, Mapping):
            reason = str(
                getattr(curve, 'reason', '')
                or 'no liquid-fraction authority is available'
            )
            raise RuntimeError(reason)
        factor = self._interpolate_freeze_gate_curve(
            curve,
            float(self.melt.temperature_C),
        )
        regime_diagnostic: dict[str, Any] = {}
        melt_regime(
            temperature_K=float(self.melt.temperature_C) + CELSIUS_TO_KELVIN_OFFSET,
            solidus_K=float(curve['solidus_T_C']) + CELSIUS_TO_KELVIN_OFFSET,
            epsilon=0.0,
            diagnostic=regime_diagnostic,
            diagnostic_site='evaporation.freeze_gate_curve.solidus_boundary',
            legacy_predicate='temperature_C <= solidus_T_C',
        )
        self._last_freeze_gate_diagnostic = {
            'enabled': True,
            'source': curve['source'],
            'solidus_T_C': curve['solidus_T_C'],
            'liquidus_T_C': curve['liquidus_T_C'],
            'liquid_fraction': factor,
        }
        for notice_key in GATE_CURVE_NOTICE_KEYS:
            notice = curve.get(notice_key)
            if isinstance(notice, Mapping):
                self._last_freeze_gate_diagnostic[notice_key] = dict(notice)
        for domain_key in GATE_CURVE_DOMAIN_KEYS:
            value = curve.get(domain_key)
            if value is not None:
                self._last_freeze_gate_diagnostic[domain_key] = (
                    dict(value) if isinstance(value, Mapping) else value
                )
        self._last_freeze_gate_diagnostic.update(regime_diagnostic)
        return factor

    def _stamp_kress_floor_diagnostic(
        self,
        diagnostic: dict[str, Any],
    ) -> None:
        """Mark a Kress-floor fraction as extrapolated, not a measured liquidus.

        Reads the curve notice. A measured curve, and the generic
        liquidus-unavailable floor, are left unchanged.
        """
        if diagnostic.get('source') != _KRESS91_LIQUID_CALIBRATION_FLOOR_SOURCE:
            return
        notice = diagnostic.get('composition_projected_notice')
        if not isinstance(notice, Mapping):
            return
        status = notice.get('temperature_band_status')
        if status:
            diagnostic['status'] = str(status)
        if 'floor_T_C' in notice:
            diagnostic['floor_T_C'] = float(notice['floor_T_C'])
        case = notice.get('temperature_band_case')
        if case:
            diagnostic['temperature_band_case'] = str(case)

    def _record_composition_projected_liquidus_notice_from_curve(
        self,
        curve: Mapping[str, Any],
    ) -> None:
        notice = curve.get('composition_projected_notice')
        if isinstance(notice, Mapping):
            self._record_composition_projected_liquidus_notice(notice)

    def _record_composition_projected_liquidus_notice(
        self,
        notice: Mapping[str, Any],
    ) -> None:
        stored = getattr(self, '_composition_projected_liquidus_notices', None)
        if not isinstance(stored, list):
            stored = []
        stored_notice = {
            'kind': notice.get('kind'),
            'reason': notice.get('reason'),
            'authority': notice.get('authority'),
            'certified_band': dict(notice.get('certified_band') or {}),
            'dropped_components': [
                {
                    'component': str(row.get('component')),
                    'mass_fraction': float(row.get('mass_fraction')),
                }
                for row in (notice.get('dropped_components') or ())
                if isinstance(row, Mapping)
            ],
        }
        # Present only when the projected bounds were not the curve. A
        # usable projection keeps the historical notice shape.
        if 'bounds_source' in notice:
            stored_notice['bounds_source'] = notice.get('bounds_source')
        if 'projected_bounds' in notice:
            stored_notice['projected_bounds'] = notice.get('projected_bounds')
        projection_band = notice.get('projection_certified_band')
        if isinstance(projection_band, Mapping):
            stored_notice['projection_certified_band'] = dict(projection_band)
            components = projection_band.get('bulk_components')
            if isinstance(components, (list, tuple)):
                stored_notice['projection_certified_band'][
                    'bulk_components'
                ] = [str(item) for item in components]
        if 'floor_T_C' in notice:
            stored_notice['floor_T_C'] = float(notice['floor_T_C'])
        for key in (
            'temperature_band_case',
            'temperature_band_status',
            'temperature_band_source',
        ):
            if key in notice:
                stored_notice[key] = notice.get(key)
        notice_key = _composition_projected_notice_key(stored_notice)
        for index, existing in enumerate(stored):
            if _composition_projected_notice_key(existing) == notice_key:
                # A later curve can resolve the same dropped bulk through a
                # more informative authority, such as the Kress floor.
                stored[index] = stored_notice
                self._composition_projected_liquidus_notices = stored
                return
        stored.append(stored_notice)
        self._composition_projected_liquidus_notices = stored

    def composition_projected_liquidus_run_notice(self) -> dict[str, Any] | None:
        """Run-level flag for liquidus solved on a projected bulk.

        MAGEMin's equilibrate result stays ``out_of_domain``. This notice is
        the category-3 flag: each dropped component and its mass fraction.
        """
        stored = getattr(self, '_composition_projected_liquidus_notices', None)
        if not stored:
            return None
        return {
            'kind': 'composition_projected',
            'reason': 'composition_projected',
            'authority': 'extrapolated',
            'notices': [dict(item) for item in stored],
        }

    def _composition_projected_freeze_gate_curve(
        self,
        diagnostic: Mapping[str, Any],
        *,
        source: str,
        reasons: list[str],
        path: tuple = (),
        repair_notices: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """Use the projected bulk's liquidus only when its bounds are usable.

        Invalid or missing projected bounds are not a curve and do not arm
        the Kress floor by themselves. The caller keeps the drop notice and
        retains the declared provider. A missing per-component fraction stays
        a typed refusal from ``composition_projected_liquidus_notice``.
        """
        from simulator.melt_backend.magemin import (
            composition_projected_liquidus_notice,
        )

        if not _diagnostic_names_composition_projected(diagnostic):
            return None
        notice = composition_projected_liquidus_notice(diagnostic)
        if notice is None:
            reasons.append(
                f'{source} composition_projected refusal has no '
                'per-component mass fraction'
            )
            return None
        from simulator.composition_projection import (
            _projected_liquidus_refusal_reason,
        )

        refusal = _projected_liquidus_refusal_reason(notice)
        if refusal is not None:
            reasons.append(f'{source} {refusal}')
            self._stash_invalid_projected_liquidus_notice(
                {**notice, 'reason': refusal},
            )
            return None
        projected_source = f'{source}:composition_projected'
        curve = None
        inconsistent = False
        if path:
            reason_mark = len(reasons)
            curve = self._freeze_gate_curve_from_path(
                path,
                solidus_T_C=self._optional_float(diagnostic.get('solidus_T_C')),
                liquidus_T_C=self._optional_float(
                    diagnostic.get('liquidus_T_C')
                ),
                source=projected_source,
                repair_notices=repair_notices,
                reasons=reasons,
            )
            inconsistent = _liquidus_fraction_inconsistent_since(
                reasons,
                reason_mark,
            )
        if curve is None and not inconsistent:
            curve = self._freeze_gate_curve_from_bounds(
                solidus_T_C=self._optional_float(diagnostic.get('solidus_T_C')),
                liquidus_T_C=self._optional_float(
                    diagnostic.get('liquidus_T_C')
                ),
                source=projected_source,
                reasons=reasons,
            )
        if curve is None:
            self._stash_invalid_projected_liquidus_notice(notice)
            if not any('composition_projected' in reason for reason in reasons):
                reasons.append(
                    f'{source} composition_projected refusal has no '
                    'projected liquidus'
                )
            return None
        if curve.get('source') != source:
            self._record_freeze_gate_curve_source_repair(
                repair_notices,
                failed_source=source,
                replacement_source=str(curve.get('source') or ''),
            )
        curve = dict(curve)
        curve['composition_projected_notice'] = notice
        return curve

    def _record_freeze_gate_curve_source_repair(
        self,
        repair_notices: list[dict[str, Any]] | None,
        *,
        failed_source: str,
        replacement_source: str,
    ) -> None:
        from simulator.reduced_real_determinism import record_repair_notice

        notice = {
            'type': 'typed_notice',
            'kind': 'freeze_gate_curve_source_replaced',
            'site': 'SR19',
            'failed_source': str(failed_source),
            'replacement_source': str(replacement_source),
        }
        record_repair_notice(self, notice)
        if repair_notices is not None and notice not in repair_notices:
            repair_notices.append(notice)

    def _record_freeze_gate_magemin_timeout_fallback_notice(
        self,
        curve: dict[str, Any],
        *,
        timeout_reason: str | None,
    ) -> dict[str, Any]:
        if (
            self._liquid_fraction_provider != 'magemin'
            or timeout_reason != 'engine_worker_timeout'
        ):
            return curve

        notice = {
            'type': 'typed_notice',
            'kind': 'freeze_gate_magemin_source_fallback',
            'reason': timeout_reason,
            'attempted_source': 'magemin',
            'source_used': str(curve.get('source') or ''),
        }
        refusal = dict(curve.get('gate_provider_refusal') or {})
        refusal['availability_notice'] = notice
        curve['gate_provider_refusal'] = refusal
        return curve

    def _stash_invalid_projected_liquidus_notice(
        self,
        notice: Mapping[str, Any],
    ) -> None:
        if getattr(self, '_invalid_projected_liquidus_notice', None) is not None:
            return
        self._invalid_projected_liquidus_notice = {
            'kind': notice.get('kind'),
            'reason': notice.get('reason'),
            'authority': notice.get('authority'),
            'certified_band': dict(notice.get('certified_band') or {}),
            'dropped_components': [
                {
                    'component': str(row.get('component')),
                    'mass_fraction': float(row.get('mass_fraction')),
                }
                for row in (notice.get('dropped_components') or ())
                if isinstance(row, Mapping)
            ],
        }

    def _invalid_projection_bounds_notice(
        self,
        notice: Mapping[str, Any],
        *,
        bounds_source: str,
    ) -> dict[str, Any]:
        annotated = {
            'kind': notice.get('kind'),
            'reason': notice.get('reason'),
            'authority': notice.get('authority'),
            'certified_band': dict(notice.get('certified_band') or {}),
            'dropped_components': [
                {
                    'component': str(row.get('component')),
                    'mass_fraction': float(row.get('mass_fraction')),
                }
                for row in (notice.get('dropped_components') or ())
                if isinstance(row, Mapping)
            ],
            'bounds_source': bounds_source,
            'projected_bounds': 'invalid',
        }
        if bounds_source != _KRESS91_LIQUID_CALIBRATION_FLOOR_SOURCE:
            return annotated
        # Frozen at and below 1200 C. That limb is the below-1200 C
        # extrapolation: Kress91's liquid relation is certified on
        # 1200–1630 C. The MAGEMin oxide list stays the projection's band.
        below = kress91_temperature_band_case(
            float(KRESS91_LIQUID_CALIBRATION_MIN_T_C) - 1.0
        )
        certified = kress91_temperature_band_case(
            float(KRESS91_LIQUID_CALIBRATION_MIN_T_C)
        )
        projection_band = dict(annotated['certified_band'])
        components = projection_band.get('bulk_components')
        if isinstance(components, (list, tuple)):
            projection_band['bulk_components'] = [
                str(item) for item in components
            ]
        annotated['projection_certified_band'] = projection_band
        annotated['certified_band'] = {
            'min_T_C': float(KRESS91_LIQUID_CALIBRATION_MIN_T_C),
            'max_T_C': float(KRESS91_LIQUID_CALIBRATION_MAX_T_C),
            'source': certified['source'],
        }
        annotated['floor_T_C'] = float(KRESS91_LIQUID_CALIBRATION_MIN_T_C)
        annotated['temperature_band_case'] = below['case']
        annotated['temperature_band_status'] = below['status']
        annotated['temperature_band_source'] = below['source']
        return annotated

    def _freeze_gate_kress_floor_curve(
        self,
        notice: Mapping[str, Any] | None,
        reasons: list[str],
    ) -> dict[str, Any] | None:
        """Kress91 calibration step, named as its own authority.

        Applies only when the cleaned melt still has a silicate species the
        floor was calibrated on. The invalid projection is not the source.
        """
        cleaned_mol = self.atom_ledger.mol_by_account(_FREEZE_GATE_ACCOUNT)
        if not any(
            species in _FREEZE_GATE_COMPOSITION_SPECIES and float(mol) > 0.0
            for species, mol in cleaned_mol.items()
        ):
            reasons.append(
                'kress91 liquid calibration floor unavailable: no cleaned melt'
            )
            return None
        solidus_T_C = float(KRESS91_LIQUID_CALIBRATION_MIN_T_C)
        liquidus_T_C = solidus_T_C + _KRESS91_FLOOR_OPEN_SIDE_C
        source = _KRESS91_LIQUID_CALIBRATION_FLOOR_SOURCE
        curve = {
            'source': source,
            'solidus_T_C': solidus_T_C,
            'liquidus_T_C': liquidus_T_C,
            'is_liquidus_floor': True,
            'path': (
                (solidus_T_C, 0.0),
                (liquidus_T_C, 1.0),
            ),
        }
        if notice is not None:
            curve['composition_projected_notice'] = (
                self._invalid_projection_bounds_notice(
                    notice, bounds_source=source,
                )
            )
        return curve

    def _refuse_freeze_gate_without_liquidus_authority(
        self,
        notice: Mapping[str, Any],
        reasons: list[str],
    ) -> None:
        """No projected or Kress bound. Typed run refusal, not abort."""
        annotated = self._invalid_projection_bounds_notice(
            notice,
            bounds_source='refused',
        )
        self._record_composition_projected_liquidus_notice(annotated)
        detail = '; '.join(reasons[-6:]) or 'no liquidus bound of any authority'
        raise EvaporationFluxRefusal(
            'composition_projected; freeze gate has no liquidus bound of '
            f'any authority. {detail}',
            {
                'reason_refused': 'freeze_gate_no_liquidus_authority',
                'kind': 'composition_projected',
                'reason': 'composition_projected',
                'bounds_source': 'refused',
                'projected_bounds': 'invalid',
                'composition_projected_notice': annotated,
                'detail': detail,
            },
        )

    def _freeze_gate_curve(
        self,
        *,
        redox_state: RedoxState | None = None,
    ) -> dict[str, Any]:
        if redox_state is None:
            return self._freeze_gate_curve_record()['curve']
        return self._freeze_gate_curve_record(redox_state=redox_state)['curve']

    def _remember_freeze_gate_provider_curve(
        self,
        curve: dict[str, Any],
        *,
        redox_state: RedoxState | None = None,
    ) -> dict[str, Any]:
        from simulator.reduced_real_determinism import _identity_gate_curve

        realized_provider = curve.get('gate_provider_step_down', {}).get(
            'realized_provider'
        )
        magemin_domain_identity = (
            getattr(self, '_freeze_gate_current_magemin_domain_identity', None)
            if self._freeze_gate_curve_uses_magemin_domain_identity(curve)
            else None
        )
        self._freeze_gate_last_good_curve = {
            'provider': self._liquid_fraction_provider,
            'realized_provider': realized_provider,
            'curve': copy.deepcopy(_identity_gate_curve(curve)),

            'computed_hour': int(self.melt.hour),
            'composition_wt_pct': self.melt.composition_wt_pct(),
            'redox_state_identity': redox_state,
            'magemin_domain_identity': magemin_domain_identity,
            'domain_identity': (
                curve.get('authority'),
                copy.deepcopy(curve.get('certified_band')),
            ),
        }
        self._freeze_gate_held_curve_record = None
        return curve

    def _freeze_gate_curve_uses_magemin_domain_identity(
        self,
        curve: Mapping[str, Any],
    ) -> bool:
        if self._liquid_fraction_provider != 'magemin':
            return False
        step_down = curve.get('gate_provider_step_down')
        if not isinstance(step_down, Mapping):
            return True
        return (
            step_down.get('magemin_sourced') is True
            or curve.get('is_liquidus_floor') is True
        )

    def _hold_freeze_gate_provider_curve(
        self,
        failure: Mapping[str, Any],
        *,
        timeout_reason: str | None = None,
        redox_state: RedoxState | None = None,
    ) -> dict[str, Any] | None:
        from simulator.reduced_real_determinism import _identity_gate_curve

        previous = self._freeze_gate_last_good_curve
        if previous is None or previous['provider'] != self._liquid_fraction_provider:
            return None
        if not redox_state_cache_identity_matches(
            redox_state,
            previous.get('redox_state_identity'),
        ):
            return None
        diagnostic = failure['provider_diagnostic']
        realized_provider = previous.get('realized_provider')
        magemin_sourced = self._freeze_gate_curve_uses_magemin_domain_identity(
            previous.get('curve') or {}
        )
        if magemin_sourced:
            # A failed attempt has no domain verdict. Match the last-good
            # curve against the current request's pre-dispatch identity,
            # which is computed from the evaluated bulk without the engine.
            magemin_domain_identity = getattr(
                self, '_freeze_gate_current_magemin_domain_identity', None,
            )
            if (
                magemin_domain_identity is None
                or not magemin_domain_identity.matches(
                    previous.get('magemin_domain_identity')
                )
            ):
                return None
        else:
            backend_diagnostic = diagnostic.get('backend_diagnostics') or {}
            authority = diagnostic.get('authority')
            certified_band = diagnostic.get('certified_band')
            if authority is None:
                authority = backend_diagnostic.get('authority')
            if certified_band is None:
                certified_band = backend_diagnostic.get('certified_band')
            current_domain_identity = (
                authority,
                copy.deepcopy(certified_band),
            )
            if previous.get('domain_identity') != current_domain_identity:
                return None
        if realized_provider and realized_provider not in {
            attempt['provider'] for attempt in failure['provider_diagnostic'].get(
                'gate_provider_step_down', {},
            ).get('failed_providers', ())
        }:
            return None
        composition = self.melt.composition_wt_pct()
        previous_composition = previous['composition_wt_pct']
        # d-298 applies d-019's best available extension: retain the SAME
        # provider's F(T; w_solved). Composition drift is the approximation.
        # D_1 = sum_i |w_i(now) - w_i(solved)| over oxides, in wt% points;
        # absent oxides contribute zero, identical compositions give D_1=0.
        # This is a diagnostic distance, never an acceptance threshold.
        distance = sum(
            abs(composition.get(oxide, 0.0) - previous_composition.get(oxide, 0.0))
            for oxide in sorted(composition.keys() | previous_composition.keys())
        )
        # Identity of the solved curve. This hour's retry environment is
        # attached by the caller and is not retained with the held curve.
        curve = copy.deepcopy(_identity_gate_curve(previous['curve']))
        diagnostic = failure['provider_diagnostic']

        curve['gate_provider_refusal'] = {
            **failure,
            'reason': (
                timeout_reason
                or diagnostic.get('backend_status_reason')
                or diagnostic.get('reason')
                or failure['reason']
            ),
            'qualification': 'held_last_good_curve',
            'authority': 'extrapolated (held)',
            'failure_hour': int(self.melt.hour),
            'failure_temperature_C': float(self.melt.temperature_C),
            'computed_hour': previous['computed_hour'],
            'composition_distance_wt_pct': distance,
            **(
                {
                    'certified_band': {
                        'sio2_wt_pct': list(
                            magemin_domain_identity.certified_band
                        ),
                    },
                }
                if (
                    magemin_sourced
                    and magemin_domain_identity is not None
                    and magemin_domain_identity.silica_verdict
                    == 'extrapolated'
                    and magemin_domain_identity.certified_band is not None
                )
                else {}
            ),
        }
        return curve

    def _freeze_gate_curve_record(
        self,
        *,
        redox_state: RedoxState | None = None,
    ) -> dict[str, Any]:
        from simulator.reduced_real_determinism import (
            GateCurveProviderOutcome,
            _gate_curve_live_publication,
            _identity_gate_curve,
            gate_curve_memo_is_cacheable,
            gate_curve_memo_record,
            record_repair_notice,
        )
        from simulator.vapour_rail.run_disclosure import (
            accept_stored_memo,
            bind_simulation_run,
            open_computation,
        )

        if self._liquid_fraction_provider == 'analytical':
            curve = self._freeze_gate_kress_floor_curve(None, [])
            if curve is None:
                raise EvaporationFluxRefusal(
                    'analytical_gate_no_cleaned_melt',
                    {'provider': 'analytical', 'reason': 'no_cleaned_melt'},
                )
            return _gate_curve_live_publication(
                gate_curve_memo_record(curve, None, (), 'live'), curve
            )

        self._reduced_real_uncacheable_reasons = []
        self._last_reduced_real_replay_notices = None
        self._freeze_gate_provider_outcome = None
        pressure_bar = float(self.melt.p_total_mbar) / 1000.0
        fO2_log = self._freeze_gate_liquidus_fO2_log(
            float(self._current_melt_redox_fO2_log())
        )
        fO2_log, redox_state = normalize_redox_state(fO2_log, redox_state)
        redox_state_kwargs = _non_absolute_redox_state_kwargs(redox_state)
        redox_key_fO2_log = self._freeze_gate_redox_key_fO2_log(
            fO2_log=fO2_log,
        )
        key, magemin_domain_reason = self._freeze_gate_cache_key_and_domain(
            pressure_bar=pressure_bar,
            fO2_log=redox_key_fO2_log,
            **redox_state_kwargs,
        )
        magemin_domain_identity = getattr(
            self, '_freeze_gate_current_magemin_domain_identity', None,
        )
        magemin_identity_reusable = (
            self._liquid_fraction_provider != 'magemin'
            or (
                magemin_domain_identity is not None
                and magemin_domain_identity.reusable
            )
        )
        # Classify MAGEMin before reading the held curve. A known held
        # disclosure is replayed into this run; an unknown one is recomputed.
        if magemin_domain_reason is None and magemin_identity_reusable:
            held = self._freeze_gate_held_curve_record
            if (
                isinstance(held, Mapping)
                and held.get('hour') == int(self.melt.hour)
                and held.get('provider') == self._liquid_fraction_provider
                and redox_state_cache_identity_matches(
                    redox_state,
                    held.get('redox_state_identity'),
                )
                and (
                    (
                        magemin_domain_identity is not None
                        and magemin_domain_identity.matches(
                            held.get('domain_identity')
                        )
                    )
                    if self._liquid_fraction_provider == 'magemin'
                    else held.get('domain_identity') == getattr(
                        key, 'magemin_domain_identity', None
                    )
                )
            ):
                held_record = held.get('record')
                published = accept_stored_memo(held_record, owner=self)
                if published is not None:
                    record = copy.deepcopy(held_record)
                    record['law_disclosures'] = published
                    return _gate_curve_live_publication(
                        record, held_record['curve']
                    )
                self._freeze_gate_held_curve_record = None
        store_getter = getattr(self, '_pt0_store', None)
        store = store_getter() if callable(store_getter) else None
        from simulator.engine_binding_admission import (
            gate_curve_memo_reuse_eligible,
        )

        if (
            store is not None
            and getattr(store, 'replay_enabled', False)
            and magemin_domain_reason is None
            and magemin_identity_reusable
        ):
            # Same registry as the live solve. The store hit re-registers
            # into it before this returns.
            with bind_simulation_run(self):
                curve = store.replay_gate_curve(
                    self,
                    fO2_log=fO2_log,
                    **redox_state_kwargs,
                )
                self._record_composition_projected_liquidus_notice_from_curve(curve)
                replay_notices = getattr(
                    self, '_last_reduced_real_replay_notices', None
                )
                # The store already re-registered this hit. Copy that
                # measurement onto the record. A second accept here would
                # count the same read twice. None means the hit was unknown.
                published = getattr(store, '_accepted_hit_disclosures', None)
                replay_record = gate_curve_memo_record(
                    curve,
                    None,
                    replay_notices if isinstance(replay_notices, list) else (),
                    'replay',
                    **(
                        {'law_disclosures': published}
                        if published is not None
                        else {}
                    ),
                )
                return _gate_curve_live_publication(replay_record, curve)
        cache = getattr(self, '_freeze_gate_liquid_fraction_cache', None)
        cached_curve = cache.get('curve') if isinstance(cache, dict) else None
        cached_notices = cache.get('repair_notices') if isinstance(cache, dict) else None
        if (
            magemin_domain_reason is None
            and magemin_identity_reusable
            and cache
            and cache.get('key') == key
            and cache.get('disposition') in {'live', 'memo-hit'}
            and gate_curve_memo_is_cacheable(
                self,
                cached_curve,
                cache.get('provider_outcome'),
                disposition=cache.get('disposition', 'live'),
            )
            and isinstance(cached_curve, Mapping)
            and isinstance(cached_notices, list)
            and gate_curve_memo_reuse_eligible(
                self,
                cached_curve,
                cache.get('origin'),
                fresh_evidence=True,
                redox_state=redox_state,
            )
        ):
            with bind_simulation_run(self):
                published = accept_stored_memo(cache, owner=self)
                if published is not None:
                    curve = dict(cached_curve)
                    for notice in cached_notices:
                        record_repair_notice(self, notice)
                    self._record_composition_projected_liquidus_notice_from_curve(
                        curve
                    )
                    hit = gate_curve_memo_record(
                        curve,
                        cache.get('origin'),
                        cached_notices,
                        'memo-hit',
                        provider_outcome=cache.get('provider_outcome'),
                    )
                    hit["law_disclosures"] = published
                    if store is not None and getattr(store, 'capture_enabled', False):
                        if gate_curve_memo_is_cacheable(
                            self,
                            curve,
                            cache.get('provider_outcome'),
                            disposition=cache.get('disposition', 'live'),
                        ):
                            store.capture_gate_curve(
                                self,
                                fO2_log=fO2_log,
                                curve=curve,
                                repair_notices=cached_notices,
                                law_disclosures=published,
                                **redox_state_kwargs,
                            )
                    return _gate_curve_live_publication(hit, curve)

        # Opt-in caller-owned memo for workflows that build fresh simulator
        # instances for equivalent rows. Reuse is based on the quantized
        # composition/P/fO2 key below, not exact physical-input identity.
        shared_curve_cache = getattr(
            self,
            '_freeze_gate_shared_curve_cache',
            None,
        )
        if (
            magemin_domain_reason is None
            and magemin_identity_reusable
            and isinstance(shared_curve_cache, dict)
        ):
            process_cached = shared_curve_cache.get(key)
            if isinstance(process_cached, Mapping):
                shared_value = process_cached.get('curve')
                shared_origin = process_cached.get('origin')
                shared_notices = process_cached.get('repair_notices')
            else:
                shared_value = {}
                shared_origin = None
                shared_notices = None
            if (
                isinstance(process_cached, Mapping)
                and process_cached.get('disposition') in {'live', 'memo-hit'}
                and gate_curve_memo_is_cacheable(
                    self,
                    shared_value,
                    process_cached.get('provider_outcome'),
                    disposition=process_cached.get('disposition', 'live'),
                )
                and isinstance(shared_value, Mapping)
                and isinstance(shared_notices, list)
                and gate_curve_memo_reuse_eligible(
                    self,
                    shared_value,
                    shared_origin,
                    fresh_evidence=True,
                    redox_state=redox_state,
                )
            ):
                with bind_simulation_run(self):
                    published = accept_stored_memo(process_cached, owner=self)
                    if published is not None:
                        curve = shared_value
                        for notice in shared_notices:
                            record_repair_notice(self, notice)
                        self._record_composition_projected_liquidus_notice_from_curve(
                            curve
                        )
                        record = gate_curve_memo_record(
                            curve,
                            shared_origin,
                            shared_notices,
                            'memo-hit',
                            provider_outcome=process_cached.get('provider_outcome'),
                        )
                        record["law_disclosures"] = published
                        self._freeze_gate_liquid_fraction_cache = {
                            **record,
                            'key': key,
                        }
                        if (
                            store is not None
                            and getattr(store, 'capture_enabled', False)
                            and gate_curve_memo_is_cacheable(
                                self,
                                curve,
                                record.get('provider_outcome'),
                                disposition=record.get('disposition', 'live'),
                            )
                        ):
                            store.capture_gate_curve(
                                self,
                                fO2_log=fO2_log,
                                curve=curve,
                                repair_notices=shared_notices,
                                law_disclosures=published,
                                **redox_state_kwargs,
                            )
                        return _gate_curve_live_publication(record, curve)

        previous_in_progress = bool(
            getattr(self, '_freeze_gate_curve_in_progress', False)
        )
        previous_cache = cache
        computing_cache = {
            'key': key,
            'curve': None,
            'status': 'computing',
        }
        self._freeze_gate_liquid_fraction_cache = computing_cache
        self._freeze_gate_curve_in_progress = True
        cache_committed = False
        previous_invalid_notice = getattr(
            self,
            '_invalid_projected_liquidus_notice',
            None,
        )
        self._invalid_projected_liquidus_notice = None
        # The live curve is one computation. Its memo and PT-0 row store
        # only the laws this solve executes. A later hit re-registers them.
        # The session registry is the parent when one is bound. Otherwise
        # this simulator keeps the registry. open_computation does not
        # build a detached one.
        with bind_simulation_run(self), open_computation() as gate_computation:
            try:
                reasons: list[str] = []
                repair_notices: list[dict[str, Any]] = []
                curve = self._freeze_gate_curve_from_gate_dispatch(
                    reasons,
                    fO2_log=fO2_log,
                    repair_notices=repair_notices,
                    redox_state=redox_state,
                )
                if (
                    isinstance(curve, Mapping)
                    and curve.get('gate_provider_refusal', {}).get('outcome') in {
                        GateCurveProviderOutcome.NUMERICAL_FAILURE,
                        GateCurveProviderOutcome.FAILED,
                    }
                ):
                    record = gate_curve_memo_record(
                        curve, (self._freeze_gate_last_good_curve or {}).get('origin'),
                        repair_notices, 'live',
                        provider_outcome=self._freeze_gate_provider_outcome,
                    )
                    record["law_disclosures"] = [
                        dict(item) for item in gate_computation.snapshot()
                    ]
                    # Holds and first-call floors are tick-local, never solved memos.
                    self._freeze_gate_held_curve_record = {
                        'hour': int(self.melt.hour),
                        'provider': self._liquid_fraction_provider,
                        'domain_identity': magemin_domain_identity,
                        'redox_state_identity': redox_state,
                        'record': copy.deepcopy(record),
                    }
                    return _gate_curve_live_publication(record, curve)

                # Invalid projected bounds are not a liquidus. The Kress floor
                # remains the qualified out-of-domain prediction and
                # the notice names that source plus the dropped components.
                # The projection itself is never the curve source. No bound at
                # all is a typed refusal, not a run-killing RuntimeError.
                invalid_notice = getattr(
                    self,
                    '_invalid_projected_liquidus_notice',
                    None,
                )
                if isinstance(invalid_notice, Mapping):
                    existing_notice = (
                        curve.get('composition_projected_notice')
                        if isinstance(curve, Mapping)
                        else None
                    )
                    if isinstance(curve, Mapping) and isinstance(
                        existing_notice,
                        Mapping,
                    ):
                        pass
                    elif isinstance(curve, Mapping):
                        curve = dict(curve)
                        curve['composition_projected_notice'] = (
                            self._invalid_projection_bounds_notice(
                                invalid_notice,
                                bounds_source=str(curve.get('source') or ''),
                            )
                        )
                    else:
                        curve = self._freeze_gate_kress_floor_curve(
                            invalid_notice,
                            reasons,
                        )
                        if curve is None:
                            self._refuse_freeze_gate_without_liquidus_authority(
                                invalid_notice,
                                reasons,
                            )
                if curve is None:
                    detail = '; '.join(reasons[-6:]) or 'no liquidus engine available'
                    # Keep the token even when the joined tail is long. The redox
                    # gate must not read this refusal as a missing liquidus.
                    if any('composition_projected' in reason for reason in reasons):
                        # Named projection, no parseable drop notice, and no
                        # usable declared-provider bound. Typed refusal; a
                        # RuntimeError would be re-raised as status=failed.
                        detail = f'composition_projected; {detail}'
                        raise EvaporationFluxRefusal(
                            detail,
                            {
                                'reason_refused': (
                                    'freeze_gate_no_liquidus_authority'
                                ),
                                'kind': 'composition_projected',
                                'reason': 'composition_projected',
                                'bounds_source': 'refused',
                                'projected_bounds': 'invalid',
                                'detail': detail,
                            },
                        )
                    raise EvaporationFluxRefusal(
                        'liquid_fraction_provider_failed',
                        {
                            'provider': self._liquid_fraction_provider,
                            'reason': 'liquid_fraction_provider_failed',
                            'detail': detail,
                            'outcome': self._freeze_gate_provider_outcome,
                        },
                    )

                self._record_composition_projected_liquidus_notice_from_curve(curve)
                cache_eligible = gate_curve_memo_is_cacheable(
                    self,
                    curve,
                    self._freeze_gate_provider_outcome,
                ) and magemin_identity_reusable
                from simulator.engine_binding_admission import (
                    gate_curve_memo_evidence,
                    gate_curve_memo_reuse_eligible,
                    write_gate_curve_memo_record,
                )

                evidence = gate_curve_memo_evidence(
                    self,
                    curve,
                    include_selected_bindings=cache_eligible,
                )
                record = write_gate_curve_memo_record(
                    self,
                    curve,
                    repair_notices,
                    evidence=evidence,
                    provider_outcome=self._freeze_gate_provider_outcome,
                    **redox_state_kwargs,
                )
                origin = record['origin']
                cache_eligible = cache_eligible and gate_curve_memo_reuse_eligible(
                    self,
                    curve,
                    origin,
                    fresh_evidence=True,
                    evidence=evidence,
                    redox_state=redox_state,
                )
                last_good = self._freeze_gate_last_good_curve
                if (
                    last_good is not None
                    and _identity_gate_curve(last_good['curve'])
                    == _identity_gate_curve(curve)
                ):
                    last_good['origin'] = origin
                if cache_eligible:
                    self._freeze_gate_liquid_fraction_cache = {
                        **record,
                        'key': key,
                    }
                    if isinstance(shared_curve_cache, dict):
                        # Store a copy so later sim mutations of their local cache
                        # cannot corrupt the caller-owned memo used by sibling rows.
                        shared_curve_cache[key] = record
                    cache_committed = True
                self._freeze_gate_cache_rebuild_count = (
                    int(getattr(self, '_freeze_gate_cache_rebuild_count', 0)) + 1
                )
            finally:
                self._invalid_projected_liquidus_notice = previous_invalid_notice
                if (
                    not cache_committed
                    and getattr(self, '_freeze_gate_liquid_fraction_cache', None)
                    is computing_cache
                ):
                    self._freeze_gate_liquid_fraction_cache = previous_cache
                self._freeze_gate_curve_in_progress = previous_in_progress
            disclosures = [dict(item) for item in gate_computation.snapshot()]
            record["law_disclosures"] = disclosures
            if cache_committed:
                cached_record = self._freeze_gate_liquid_fraction_cache
                if isinstance(cached_record, dict):
                    cached_record["law_disclosures"] = disclosures
                if isinstance(shared_curve_cache, dict):
                    shared_value = shared_curve_cache.get(key)
                    if isinstance(shared_value, dict):
                        shared_value["law_disclosures"] = disclosures
            if (
                store is not None
                and getattr(store, 'capture_enabled', False)
                and gate_curve_memo_is_cacheable(
                    self,
                    curve,
                    record.get('provider_outcome'),
                    disposition=record.get('disposition', 'live'),
                )
            ):
                store.capture_gate_curve(
                    self,
                    fO2_log=fO2_log,
                    curve=curve,
                    repair_notices=repair_notices,
                    law_disclosures=disclosures,
                    **redox_state_kwargs,
                )
            return _gate_curve_live_publication(record, curve)

    @staticmethod
    def _freeze_gate_liquidus_fO2_log(fO2_log: float) -> float:
        try:
            value = float(fO2_log)
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(value):
            return 0.0
        return max(
            -_FREEZE_GATE_FO2_LOG_BOUND,
            min(_FREEZE_GATE_FO2_LOG_BOUND, value),
        )

    def _freeze_gate_redox_key_fO2_log(
        self,
        *,
        fO2_log: float | None = None,
        reference_T_K: float | None = None,
    ) -> float:
        redox_fO2_log = self._freeze_gate_liquidus_fO2_log(
            float(fO2_log)
            if fO2_log is not None
            else float(self._current_melt_redox_fO2_log()),
        )
        redox_reference_T_K = (
            float(reference_T_K)
            if reference_T_K is not None
            else self._current_melt_redox_reference_T_K()
        )
        return self._freeze_gate_liquidus_fO2_log(
            _kress91_gate_key_referenced_log_fO2(
                redox_fO2_log,
                reference_T_K=redox_reference_T_K,
            )
        )

    def _freeze_gate_cache_key(
        self,
        *,
        pressure_bar: float,
        fO2_log: float,
        redox_state: RedoxState | None = None,
        provider_melt_inputs: _SilicateProviderMeltInputs | None = None,
    ) -> FreezeGateCurveCacheKey | _FlaggedFreezeGateCurveCacheKey:
        key, _magemin_domain_reason = self._freeze_gate_cache_key_and_domain(
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            redox_state=redox_state,
            provider_melt_inputs=provider_melt_inputs,
        )
        return key

    def _freeze_gate_magemin_domain_identity_and_reason(
        self,
        provider_inputs: _SilicateProviderMeltInputs,
    ) -> tuple[MAGEMinDomainIdentity, str | None]:
        from simulator.melt_backend.base import project_melt_to_oxide_projection
        from simulator.melt_backend import magemin as magemin_module
        from simulator.melt_backend.magemin import (
            MAGEMinBackend,
            MAGEMinDomainIdentity,
            magemin_domain_identity_from_evaluated_bulk,
            _liquidus_inputs_on_feo_total_iron_basis,
        )

        formula_registry = getattr(self, 'species_formula_registry', {}) or {}
        provider_mol = provider_inputs.composition_mol
        _folded_kg, _folded_mol, folded_accounts = (
            _liquidus_inputs_on_feo_total_iron_basis(
                composition_kg=None,
                composition_mol=None,
                composition_mol_by_account={
                    'process.cleaned_melt': provider_mol,
                },
            )
        )
        magemin_provider_mol = (
            folded_accounts.get('process.cleaned_melt')
            if isinstance(folded_accounts, Mapping)
            else provider_mol
        )
        if not isinstance(magemin_provider_mol, Mapping):
            magemin_provider_mol = provider_mol
        # Domain and projection-threshold identity follow the same FeO_T
        # composition the liquidus finder submits. The solver-input key keeps
        # the original provider account.
        magemin_projection = project_melt_to_oxide_projection(
            composition_kg=None,
            composition_mol=magemin_provider_mol,
            oxide_basis=MAGEMinBackend._MAGEMIN_INPUT_BASIS,
            species_formula_registry=formula_registry,
        )
        backend = MAGEMinBackend()
        # The exact source check retains non-DB oxides. Normalize the FeOt/O
        # convention the same way as the live gate before validating it.
        exact_iron_input = {
            species: amount
            for species, amount in magemin_projection.oxide_wt_pct.items()
            if species in {'FeO', 'Fe2O3', 'FeOt', 'O'}
        }
        if any(
            float(exact_iron_input.get(species, 0.0)) > 0.0
            for species in ('FeO', 'Fe2O3', 'FeOt')
        ):
            exact_engine_bulk = backend._build_db_bulk_projection(
                exact_iron_input
            )
            exact_gate_bulk = backend._domain_gate_oxide_composition(
                exact_engine_bulk.composition_wt_pct,
                submitted_oxide_wt_pct=magemin_projection.oxide_wt_pct,
            )
        else:
            exact_gate_bulk = dict(magemin_projection.oxide_wt_pct)
        exact_valid, _exact_warnings, exact_reason = (
            magemin_module._validate_magemin_projection_with_reason(
                exact_gate_bulk,
                dropped_mass_kg_by_species=(
                    magemin_projection.dropped_mass_kg_by_species
                ),
            )
        )
        (
            _submitted_bulk,
            projected_verdict,
            evaluated_domain_projection,
            projection_reason,
        ) = backend._submitted_bulk_and_domain_verdict(
            magemin_projection,
            species_formula_registry=formula_registry,
            projected_retry=True,
        )
        projected_valid, _projected_warnings, projected_reason = (
            projected_verdict
        )
        domain_reason = exact_reason or projected_reason
        domain_valid = exact_valid and projected_valid
        projection_threshold_verdict = (
            'refused' if projection_reason is not None else 'clear'
        )
        if (
            domain_valid
            and domain_reason is None
            and evaluated_domain_projection is not None
        ):
            identity, _evaluated_silica = (
                magemin_domain_identity_from_evaluated_bulk(
                    evaluated_domain_projection.oxide_wt_pct,
                    projection_threshold_verdict=projection_threshold_verdict,
                    refusal_reason=projection_reason,
                    domain_verdict=(projected_valid, projected_reason),
                )
            )
        else:
            identity, _evaluated_silica = (
                magemin_domain_identity_from_evaluated_bulk(
                    None,
                    projection_threshold_verdict=projection_threshold_verdict,
                    refusal_reason=projection_reason or domain_reason,
                )
            )
        if domain_valid:
            domain_reason = None
        if not isinstance(identity, MAGEMinDomainIdentity):
            identity, _evaluated_silica = (
                magemin_domain_identity_from_evaluated_bulk(None)
            )
        return identity, domain_reason

    def _freeze_gate_cache_key_and_domain(
        self,
        *,
        pressure_bar: float,
        fO2_log: float,
        redox_state: RedoxState | None = None,
        provider_melt_inputs: _SilicateProviderMeltInputs | None = None,
    ) -> tuple[
        FreezeGateCurveCacheKey | _FlaggedFreezeGateCurveCacheKey,
        str | None,
    ]:
        from simulator.reduced_real_determinism import _digest
        from simulator.chemistry.kernel import ChemistryIntent

        if provider_melt_inputs is None:
            provider_inputs = self._silicate_provider_melt_inputs(
                ChemistryIntent.GATE_LIQUID_FRACTION
            )
        else:
            provider_inputs = provider_melt_inputs
        assert provider_inputs is not None
        provider_mol = provider_inputs.composition_mol
        inert_species = provider_inputs.inert_species
        relevant_mol: dict[str, float] = {}
        for species, mol in provider_mol.items():
            species_key = str(species)
            if species_key not in _FREEZE_GATE_COMPOSITION_SPECIES:
                continue
            mol_value = float(mol)
            if mol_value > _FREEZE_GATE_EPSILON:
                relevant_mol[species_key] = mol_value

        # Liquidus is stable to floating-point composition jitter, but dose
        # states below liquidus are not interchangeable: their distinct curves
        # feed F_liquid, melt capacity, and native-Fe behavior.
        composition_key = []
        oxide_total_mol = sum(relevant_mol.values())
        if oxide_total_mol > _FREEZE_GATE_EPSILON:
            for species, mol in relevant_mol.items():
                fraction = mol / oxide_total_mol
                if fraction < _FREEZE_GATE_TRACE_FRACTION_CUTOFF:
                    continue
                quantized_fraction = (
                    round(fraction / _FREEZE_GATE_FRACTION_QUANTUM)
                    * _FREEZE_GATE_FRACTION_QUANTUM
                )
                if quantized_fraction <= 0.0:
                    continue
                composition_key.append((species, round(quantized_fraction, 6)))
        water_mol = max(float(provider_mol.get('H2O', 0.0)), 0.0)
        tracked_total_mol = oxide_total_mol + water_mol
        water_fraction = (
            water_mol / tracked_total_mol
            if tracked_total_mol > _FREEZE_GATE_EPSILON
            else 0.0
        )
        water_bucket = (
            round(water_fraction / _FREEZE_GATE_WATER_FRACTION_QUANTUM)
            * _FREEZE_GATE_WATER_FRACTION_QUANTUM
        )
        pressure_bucket = (
            round(float(pressure_bar) / _FREEZE_GATE_PRESSURE_BAR_QUANTUM)
            * _FREEZE_GATE_PRESSURE_BAR_QUANTUM
        )
        fO2_bucket = (
            round(float(fO2_log) / _FREEZE_GATE_FO2_LOG_QUANTUM)
            * _FREEZE_GATE_FO2_LOG_QUANTUM
        )
        # Quantized equivalence key, not exact identity. For a quantity y and
        # quantum q, y_bucket = q * round(y/q): composition q=1e-4 is
        # dimensionless, pressure q=0.01 bar retains bar, and fO2 q=1 retains
        # log10 units. Sanity checks: 0.010 and 0.014 bar both map to 0.01 bar;
        # fO2 -9.1 and -8.6 both map to -9.0 under Python rounding. Only a
        # midpoint crossing splits a control state; sub-cutoff composition
        # components are omitted before this arithmetic. No physical-
        # equivalence error bound for these pressure/fO2 quanta is established
        # here.
        redox_identity = (
            None if redox_state is None else redox_state.cache_identity()
        )
        redox_identity_key = (
            None if redox_identity is None
            else tuple(sorted(redox_identity.items()))
        )
        formula_registry = getattr(self, 'species_formula_registry', {}) or {}
        provider_formula_species = set(_FREEZE_GATE_COMPOSITION_SPECIES)
        provider_formula_species.add('H2O')
        provider_formula_species.update(
            str(species)
            for species, mol in provider_mol.items()
            if float(mol) > _FREEZE_GATE_EPSILON
        )
        provider_formula_identity = []
        for species_key in sorted(provider_formula_species):
            formula = resolve_species_formula(species_key, formula_registry)
            normalized_elements = tuple(
                (
                    element,
                    round(
                        float(count)
                        / _FREEZE_GATE_FORMULA_ATOM_COUNT_QUANTUM
                    )
                    * _FREEZE_GATE_FORMULA_ATOM_COUNT_QUANTUM,
                )
                for element, count in sorted(formula.elements.items())
            )
            provider_formula_identity.append(
                (species_key, normalized_elements)
            )
        # The ordinary key must retain its v7 tuple shape and byte identity.
        # Non-default MAGEMin identities use the extended key.
        magemin_domain_reason = None
        magemin_domain_identity = None
        if self._liquid_fraction_provider == 'magemin':
            from simulator.melt_backend.magemin import MAGEMinDomainIdentity

            magemin_domain_identity, magemin_domain_reason = (
                self._freeze_gate_magemin_domain_identity_and_reason(
                    provider_inputs,
                )
            )
        key_values = {
            'pressure_bar': round(pressure_bucket, 6),
            'fO2_log': round(fO2_bucket, 6),
            'composition_mol_fractions': tuple(sorted(composition_key)),
            'water_mol_fraction': round(water_bucket, 7),
            'formula_registry_digest': _digest(
                tuple(sorted(provider_formula_identity))
            ),
            'inert_species': inert_species,
            'redox_state_identity': redox_identity_key,
        }
        self._freeze_gate_current_magemin_domain_identity = (
            magemin_domain_identity
        )
        default_in_band_identity = (
            MAGEMinDomainIdentity('in_band', 'clear', None, None)
            if self._liquid_fraction_provider == 'magemin'
            else None
        )
        if (
            self._liquid_fraction_provider == 'magemin'
            and magemin_domain_identity != default_in_band_identity
        ):
            key = _FlaggedFreezeGateCurveCacheKey(
                schema='oxide_mol_fraction_p_fO2_v8',
                **key_values,
                magemin_domain_identity=magemin_domain_identity,
            )
        else:
            key = FreezeGateCurveCacheKey(
                # v7 moves explicitly inert species out of the submitted
                # curve composition while keeping membership as a separate
                # input. Preserve this exact tuple for ordinary compositions.
                schema='oxide_mol_fraction_p_fO2_v7',
                **key_values,
            )
        return key, magemin_domain_reason

    def _silicate_provider_melt_inputs(
        self,
        intent: Any,
        composition_mol: Mapping[str, float] | None = None,
        *,
        composition_sig_figs: int | None = None,
    ) -> _SilicateProviderMeltInputs | None:
        """Build the filtered silicate payload shared by dispatch and replay."""
        from simulator.chemistry.kernel import ChemistryIntent

        if intent not in {
            ChemistryIntent.SILICATE_LIQUIDUS,
            ChemistryIntent.SILICATE_EQUILIBRIUM,
            ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION,
            ChemistryIntent.GATE_LIQUID_FRACTION,
        }:
            return None
        if composition_mol is None:
            composition_mol = self.atom_ledger.project_account_mol(
                _FREEZE_GATE_ACCOUNT
            )
        store = getattr(self, '_pt0_determinism_store', None)
        canonicalizer = getattr(
            store,
            'canonical_composition_mol_by_account',
            None,
        )
        quantize_composition = (
            store is not None
            and getattr(store, 'quantize_live_controls', False)
        )
        if quantize_composition and not callable(canonicalizer):
            raise RuntimeError(
                'PT-0 live-control quantization requires composition '
                'canonicalization support'
            )
        if quantize_composition:
            # PT-0 dispatch canonicalizes the full ledger before removing
            # explicit inerts. Keep replay identity on those exact submitted
            # values, including any rounding interaction with inert species.
            if composition_sig_figs is None:
                composition_mol = canonicalizer(
                    self,
                    {_FREEZE_GATE_ACCOUNT: composition_mol},
                ).get(_FREEZE_GATE_ACCOUNT, {})
            else:
                # Ladder lookups reproduce the source rung's canonicalization
                # using the same owner resolver as the PT-0 store.
                from simulator.reduced_real_determinism import (
                    canonicalized_composition_mol_by_account,
                )

                composition_mol = canonicalized_composition_mol_by_account(
                    {_FREEZE_GATE_ACCOUNT: composition_mol},
                    sig_figs=composition_sig_figs,
                ).get(_FREEZE_GATE_ACCOUNT, {})
        inert_species = set(
            (getattr(self.inventory, 'inert_melt_components_kg', {}) or {})
        )
        active_inert_species = tuple(sorted(
            str(species)
            for species in inert_species
            if species in composition_mol
            and float(composition_mol[species]) > 0.0
        ))
        submitted_mol = {
            species: float(mol)
            for species, mol in composition_mol.items()
            if species not in inert_species and float(mol) > 0.0
        }
        formula_registry = getattr(self, 'species_formula_registry', {}) or {}
        formula_identity = tuple(
            (
                str(species),
                tuple(
                    sorted(
                        (str(element), float(count))
                        for element, count in resolve_species_formula(
                            str(species), formula_registry
                        ).elements.items()
                    )
                ),
            )
            for species, mol in sorted(submitted_mol.items())
            if float(mol) > 0.0
        )
        from simulator.melt_backend.base import positive_melt_mass_by_species

        mass_by_species = positive_melt_mass_by_species(
            composition_kg=None,
            composition_mol=submitted_mol,
            species_formula_registry=formula_registry,
        )
        return _SilicateProviderMeltInputs(
            composition_mol=submitted_mol,
            inert_species=active_inert_species,
            formula_identity=formula_identity,
            amount_kg=sum(mass_by_species.values()),
        )

    def _abandoned_gate_environment_record(
        self,
        diagnostic: Mapping[str, Any],
        status: str,
        timeout_reason: str,
    ) -> dict[str, Any]:
        """One visible record for the dispatch the retry abandoned.

        Includes every row and notice from that dispatch, physics rows
        included. A timeout that leaves ``_dispatch_only`` has no planner
        step-down; the row is synthesized here so the retry does not drop it.
        """

        from simulator.reduced_real_determinism import (
            failed_provider_attempt_is_environment,
            gate_curve_provider_diagnostic_reasons,
        )

        owned = dict(diagnostic)
        step_down = owned.get('gate_provider_step_down')
        failed = list(step_down.get('failed_providers', ())) if isinstance(
            step_down, Mapping,
        ) else []
        if not failed:
            reasons = gate_curve_provider_diagnostic_reasons(diagnostic)
            attempt = {
                'provider': (
                    diagnostic.get('kernel_fallback_used')
                    or self._liquid_fraction_provider
                ),
                'status': status,
                'reason': '; '.join(reasons) or str(
                    diagnostic.get('reason') or status
                ),
                'diagnostic': dict(diagnostic),
            }
            if failed_provider_attempt_is_environment(attempt):
                attempt['disposition'] = 'environment'
            step_down = {
                'failed_providers': [attempt],
                'realized_provider': None,
            }
            owned['gate_provider_step_down'] = step_down
        record = {
            'disposition': 'environment',
            'reason': timeout_reason,
            'status': status,
            'hour': int(self.melt.hour),
        }
        record.update(gate_dispatch_curve_notices(owned))
        return record

    def _freeze_gate_curve_from_gate_dispatch(
        self,
        reasons: list[str],
        *,
        fO2_log: float,
        repair_notices: list[dict[str, Any]] | None = None,
        redox_state: RedoxState | None = None,
    ) -> dict[str, Any] | None:
        from simulator.reduced_real_determinism import (
            ENVIRONMENT_RETRIES_KEY,
            GateCurveProviderOutcome,
            gate_curve_provider_outcome_for_status,
            gate_curve_provider_outcome_token,
            gate_curve_provider_timeout_reason,
            mark_uncacheable_replay_disposition,
            record_gate_curve_provider_outcome,
        )

        fO2_log, redox_state = normalize_redox_state(
            fO2_log,
            redox_state,
        )
        register_gate_providers = getattr(
            self,
            '_register_freeze_gate_liquid_fraction_providers',
            None,
        )
        if callable(register_gate_providers):
            try:
                register_gate_providers()
            except Exception as exc:  # noqa: BLE001 - optional provider boundary
                record_gate_curve_provider_outcome(
                    self,
                    GateCurveProviderOutcome.FAILED,
                )
                mark_uncacheable_replay_disposition(
                    self,
                    f'gate provider registration failed: {type(exc).__name__}',
                )
                reasons.append(f'gate provider registration failed: {exc}')
        magemin_domain_identity = getattr(
            self, '_freeze_gate_current_magemin_domain_identity', None,
        )
        timeout_reason = None
        # The final dispatch is published as that dispatch produced it.
        # The abandoned dispatch, including its non-timeout rows and
        # notices, is one environment record. A second timeout still
        # publishes the final dispatch, so hold-last-good still sees
        # alphamelts. d-298/d-299's hold decision is unchanged.
        abandoned_environment = None
        for attempt_index in range(2):
            try:
                result = self._dispatch_only(
                    ChemistryIntent.GATE_LIQUID_FRACTION,
                    control_inputs={},
                    fO2_log=fO2_log,
                    redox_state=redox_state,
                    fe_redox_policy='intrinsic',
                )
            except (
                ProviderUnavailableError, LiquidusSampleError, EngineWorkerTimeout,
            ) as exc:
                diagnostic = dict(getattr(exc, 'diagnostics', {}) or {})
                if isinstance(exc, EngineWorkerTimeout):
                    diagnostic['reason'] = 'engine_worker_timeout'
                diagnostic['exception_class'] = type(exc).__name__
                diagnostic['exception_message'] = str(exc)
                status = str(getattr(exc, 'status', 'unavailable'))
                warnings = list(getattr(exc, 'warnings', ()) or ())
            else:
                diagnostic = dict(getattr(result, 'diagnostic', None) or {})
                status = str(
                    getattr(result, 'status', None)
                    or diagnostic.get('backend_status')
                    or 'unavailable'
                )
                warnings = list(getattr(result, 'warnings', ()) or ())
            timeout_reason = gate_curve_provider_timeout_reason(diagnostic)
            if timeout_reason is not None and attempt_index == 0:
                abandoned_environment = self._abandoned_gate_environment_record(
                    diagnostic,
                    status,
                    timeout_reason,
                )
                continue
            break

        path = tuple(diagnostic.get('liquid_fraction_path') or ())
        source = 'gate_liquid_fraction'
        fallback_provider = diagnostic.get('kernel_fallback_used')
        if fallback_provider:
            source = f'gate_liquid_fraction:fallback:{fallback_provider}'
        notices = gate_dispatch_curve_notices(diagnostic)
        if abandoned_environment is not None:
            notices[ENVIRONMENT_RETRIES_KEY] = abandoned_environment
        step_down = diagnostic.get('gate_provider_step_down')
        if isinstance(step_down, Mapping):
            # Preserve attempted-provider details while recording the provider
            # selected by the final dispatch.
            diagnostic['gate_provider_step_down'] = {
                **step_down,
                'realized_provider': (
                    fallback_provider if status == 'ok' else None
                ),
                'magemin_sourced': (
                    diagnostic.get('magemin_sourced') is True
                ),
            }
            notices['gate_provider_step_down'] = diagnostic[
                'gate_provider_step_down'
            ]
        backend_diagnostic = diagnostic.get('backend_diagnostics') or {}
        if 'condensed_phase_reference_pressure_bar' not in backend_diagnostic:
            for attempt in (step_down or {}).get('failed_providers', ()):
                details = attempt['diagnostic'].get('backend_diagnostics') or {}
                if 'condensed_phase_reference_pressure_bar' in details:
                    backend_diagnostic = details
        if self._liquid_fraction_provider == 'magemin':
            authority = (
                diagnostic.get('authority')
                or backend_diagnostic.get('authority')
            )
            extrapolation_flag = authority == 'extrapolated' or any(
                'authority=extrapolated' in str(warning)
                for warning in warnings
            )
            if extrapolation_flag:
                if warnings:
                    notices['gate_provider_warnings'] = list(warnings)
                for field in ('authority', 'certified_band'):
                    value = diagnostic.get(field) or backend_diagnostic.get(field)
                    if value is not None:
                        notices[field] = copy.deepcopy(value)

        status_outcome = gate_curve_provider_outcome_for_status(
            status,
            diagnostic,
        )
        control_failure_reason = (
            diagnostic.get('backend_status_reason')
            or backend_diagnostic.get('backend_status_reason')
        )
        if (
            self._liquid_fraction_provider == 'magemin'
            and (
                diagnostic.get('magemin_mode_vector_mass_deficit') is True
                or backend_diagnostic.get(
                    'magemin_mode_vector_mass_deficit'
                ) is True
            )
            and control_failure_reason in {
                'clamped_operating_point',
                'engine_not_converged',
            }
        ):
            # Backend classification keeps its more specific first failure.
            # At the evaporation boundary, the combined control failure can
            # still hold the last good gate curve or use the reviewed floor.
            status_outcome = GateCurveProviderOutcome.NUMERICAL_FAILURE
        record_gate_curve_provider_outcome(self, status_outcome)
        if (
            status_outcome is GateCurveProviderOutcome.NUMERICAL_FAILURE
            or status_outcome is GateCurveProviderOutcome.FAILED
        ):
            failure = {
                'provider': self._liquid_fraction_provider,
                'reason': timeout_reason or 'liquid_fraction_provider_failed',
                'outcome': gate_curve_provider_outcome_token(status_outcome),
                'provider_diagnostic': diagnostic,
                'status': status,
                'warnings': warnings,
                **({
                    'backend_failure_disposition': 'environment',
                    'disposition': 'environment',
                } if timeout_reason is not None else {}),
                **({'detail': diagnostic['exception_message']}
                   if diagnostic.get('exception_message') else {}),
            }
            if (
                status_outcome is GateCurveProviderOutcome.NUMERICAL_FAILURE
                or timeout_reason is not None
            ):
                held = self._hold_freeze_gate_provider_curve(
                    failure,
                    timeout_reason=timeout_reason,
                    redox_state=redox_state,
                )
                if held is not None:
                    if 'gate_provider_step_down' in notices:
                        held['gate_provider_step_down'] = notices['gate_provider_step_down']
                    if ENVIRONMENT_RETRIES_KEY in notices:
                        held[ENVIRONMENT_RETRIES_KEY] = notices[ENVIRONMENT_RETRIES_KEY]
                    current_step_down = diagnostic.get(
                        'gate_provider_step_down'
                    )
                    held_step_down = (
                        current_step_down
                        if isinstance(current_step_down, Mapping)
                        else held.get('gate_provider_step_down')
                    )
                    if isinstance(held_step_down, Mapping):
                        held_step_down = {
                            **held_step_down,
                            'prediction': 'held_last_good_curve',
                            'realized_provider': (self._freeze_gate_last_good_curve or {}).get('realized_provider')
                            or self._liquid_fraction_provider,
                        }
                        if isinstance(current_step_down, Mapping):
                            last_good_curve = (
                                self._freeze_gate_last_good_curve or {}
                            ).get('curve') or {}
                            held_step_down['magemin_sourced'] = (
                                self._freeze_gate_curve_uses_magemin_domain_identity(
                                    last_good_curve
                                )
                            )
                        held['gate_provider_step_down'] = held_step_down

                    return self._record_freeze_gate_magemin_timeout_fallback_notice(
                        held,
                        timeout_reason=timeout_reason,
                    )
                curve = self._freeze_gate_kress_floor_curve(None, reasons)
                if curve is not None:
                    curve['gate_provider_refusal'] = {
                        **failure,
                        'reason': (
                            timeout_reason
                            or diagnostic.get('backend_status_reason')
                            or diagnostic.get('reason')
                            or failure['reason']
                        ),
                        'qualification': 'first_call_kress91_floor_prediction',
                        'authority': 'extrapolated (lower-rung)',
                        **(
                            {
                                'certified_band': {
                                    'sio2_wt_pct': list(
                                        magemin_domain_identity.certified_band
                                    ),
                                },
                            }
                            if (
                                magemin_domain_identity is not None
                                and magemin_domain_identity.silica_verdict
                                == 'extrapolated'
                                and magemin_domain_identity.certified_band
                                is not None
                            )
                            else {}
                        ),
                        'failure_hour': int(self.melt.hour),
                        'failure_temperature_C': float(self.melt.temperature_C),
                        'detail': (
                            'Kress91 calibration-floor step; '
                            'not a fresh solve or a melting measurement'
                        ),
                    }
                    curve.update(notices)
                    if 'gate_provider_step_down' in curve:
                        curve['gate_provider_step_down'] = {
                            **curve['gate_provider_step_down'], 'prediction': 'kress91_floor',
                        }
                    return self._record_freeze_gate_magemin_timeout_fallback_notice(
                        curve,
                        timeout_reason=timeout_reason,
                    )
            if diagnostic.get('exception_class'):
                mark_uncacheable_replay_disposition(
                    self,
                    'gate liquid fraction provider unavailable: '
                    f"{diagnostic['exception_class']}",
                )
            raise EvaporationFluxRefusal('liquid_fraction_provider_failed', failure)
        if status_outcome is GateCurveProviderOutcome.STABLE_REFUSAL:
            self._freeze_gate_held_curve_record = None
            projected = self._composition_projected_freeze_gate_curve(
                diagnostic,
                source=source,
                reasons=reasons,
                repair_notices=repair_notices,
            )
            if projected is not None:
                projected.update(notices)
                return self._remember_freeze_gate_provider_curve(
                    projected,
                    redox_state=redox_state,
                )
            invalid_notice = getattr(
                self, '_invalid_projected_liquidus_notice', None,
            )
            if isinstance(invalid_notice, Mapping) and str(
                invalid_notice.get('reason')
            ).startswith('composition_projected_over_threshold:'):
                held = self._hold_freeze_gate_provider_curve(
                    {
                        'provider': self._liquid_fraction_provider,
                        'status': status,
                        'outcome': gate_curve_provider_outcome_token(
                            status_outcome
                        ),
                        'provider_diagnostic': {
                            **diagnostic,
                            'backend_status_reason': invalid_notice['reason'],
                        },
                    },
                    redox_state=redox_state,
                )
                if held is not None:
                    held['composition_projected_notice'] = (
                        self._invalid_projection_bounds_notice(
                            invalid_notice, bounds_source=str(held['source']),
                        )
                    )
                    if ENVIRONMENT_RETRIES_KEY in notices:
                        held[ENVIRONMENT_RETRIES_KEY] = notices[ENVIRONMENT_RETRIES_KEY]
                    return held
            if _diagnostic_names_composition_projected(diagnostic):
                if not any(
                    'composition_projected' in reason for reason in reasons
                ):
                    reasons.append(
                        f'{source} composition_projected status={status}'
                    )
                return None
            curve = self._freeze_gate_kress_floor_curve(None, reasons)
            if curve is not None:
                from engines.magemin.domain import (
                    _SIO2_MIN_WT_PCT, _SIO2_MAX_WT_PCT,
                )

                curve['gate_provider_refusal'] = {
                    'provider': self._liquid_fraction_provider,
                    'status': status,
                    'outcome': status_outcome.value,
                    'reason': diagnostic.get('backend_status_reason'),
                    'qualification': 'out_of_domain_kress91_floor_prediction',
                    'authority': 'extrapolated',
                    'certified_band': {
                        'sio2_wt_pct': [_SIO2_MIN_WT_PCT, _SIO2_MAX_WT_PCT],
                        'min_T_C': float(KRESS91_LIQUID_CALIBRATION_MIN_T_C),
                        'max_T_C': float(KRESS91_LIQUID_CALIBRATION_MAX_T_C),
                        'source': kress91_temperature_band_case(
                            KRESS91_LIQUID_CALIBRATION_MIN_T_C,
                        )['source'],
                    },
                    'warnings': warnings,
                }
                curve.update(notices)
                return curve
            reasons.append(
                'gate liquid fraction unavailable: '
                f'status={status}'
            )
            return None
        if path:
            reason_mark = len(reasons)
            curve = self._freeze_gate_curve_from_path(
                path,
                solidus_T_C=self._optional_float(diagnostic.get('solidus_T_C')),
                liquidus_T_C=self._optional_float(diagnostic.get('liquidus_T_C')),
                source=source,
                repair_notices=repair_notices,
                reasons=reasons,
            )
            if curve is not None:
                curve.update(notices)
                record_gate_curve_provider_outcome(
                    self,
                    GateCurveProviderOutcome.PRODUCED,
                )
                return self._remember_freeze_gate_provider_curve(
                    curve,
                    redox_state=redox_state,
                )
            if _liquidus_fraction_inconsistent_since(reasons, reason_mark):
                return None
            reasons.append('gate liquid fraction table invalid')
        curve = self._freeze_gate_curve_from_bounds(
            solidus_T_C=self._optional_float(diagnostic.get('solidus_T_C')),
            liquidus_T_C=self._optional_float(diagnostic.get('liquidus_T_C')),
            source=source,
            reasons=reasons,
        )
        if curve is not None:
            curve.update(notices)
            record_gate_curve_provider_outcome(
                self,
                GateCurveProviderOutcome.PRODUCED,
            )
            return self._remember_freeze_gate_provider_curve(
                curve,
                redox_state=redox_state,
            )
        record_gate_curve_provider_outcome(
            self,
            GateCurveProviderOutcome.FAILED,
        )
        return None

    def _freeze_gate_curve_from_kernel_liquidus(
        self,
        reasons: list[str],
        *,
        fO2_log: float,
        redox_state: RedoxState | None = None,
        temperature_C: float | None = None,
        pressure_bar: float | None = None,
        composition_mol_by_account: Mapping[str, Mapping[str, float]] | None = None,
        repair_notices: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        from simulator.reduced_real_determinism import (
            GateCurveProviderOutcome,
            gate_curve_provider_outcome_for_status,
            mark_uncacheable_replay_disposition,
            record_gate_curve_provider_outcome,
        )

        fO2_log, redox_state = normalize_redox_state(
            fO2_log,
            redox_state,
        )
        # Missing authority is known from registry configuration; an
        # attempted dispatch would turn that stable absence into an error.
        if self._chem_registry.authoritative_for(
            ChemistryIntent.SILICATE_LIQUIDUS
        ) is None:
            record_gate_curve_provider_outcome(
                self,
                GateCurveProviderOutcome.NO_PROVIDER,
            )
            reasons.append('kernel liquidus provider is not configured')
            return None
        composition_derived = bool(composition_mol_by_account)
        try:
            if composition_derived:
                result = self._require_chem_kernel().dispatch(
                    ChemistryIntent.SILICATE_LIQUIDUS,
                    temperature_C=(
                        float(self.melt.temperature_C)
                        if temperature_C is None
                        else float(temperature_C)
                    ),
                    pressure_bar=(
                        float(self.melt.p_total_mbar) / 1000.0
                        if pressure_bar is None
                        else float(pressure_bar)
                    ),
                    fO2_log=fO2_log,
                    redox_state=redox_state,
                    fe_redox_policy='intrinsic',
                    control_inputs={
                        'composition_source': 'out_of_domain_crash_point',
                    },
                    account_mol_overrides=composition_mol_by_account,
                )
            else:
                result = self._dispatch_only(
                    ChemistryIntent.SILICATE_LIQUIDUS,
                    control_inputs={},
                    fO2_log=fO2_log,
                    redox_state=redox_state,
                    fe_redox_policy='intrinsic',
                )
        except ProviderUnavailableError as exc:
            record_gate_curve_provider_outcome(
                self,
                GateCurveProviderOutcome.FAILED,
            )
            mark_uncacheable_replay_disposition(
                self,
                f'kernel liquidus provider unavailable: {type(exc).__name__}',
            )
            reasons.append(f'kernel liquidus unavailable: {exc}')
            return None

        diagnostic = dict(getattr(result, 'diagnostic', None) or {})
        status = str(
            getattr(result, 'status', None)
            or diagnostic.get('backend_status')
            or 'unavailable'
        )
        status_outcome = gate_curve_provider_outcome_for_status(
            status,
            diagnostic,
        )
        record_gate_curve_provider_outcome(self, status_outcome)
        if status != 'ok':
            reasons.append(f'kernel liquidus unavailable: status={status}')
            return None
        curve = self._freeze_gate_curve_from_bounds(
            solidus_T_C=self._optional_float(diagnostic.get('solidus_T_C')),
            liquidus_T_C=self._optional_float(diagnostic.get('liquidus_T_C')),
            source=(
                'liquidus_solidus:kernel:composition_derived'
                if composition_derived
                else 'liquidus_solidus:kernel'
            ),
            reasons=reasons,
        )
        if curve is not None and composition_derived:
            curve = dict(curve)
            curve['composition_derived'] = True
        if curve is None:
            record_gate_curve_provider_outcome(
                self,
                GateCurveProviderOutcome.FAILED,
            )
        else:
            record_gate_curve_provider_outcome(
                self,
                GateCurveProviderOutcome.PRODUCED,
            )
        return curve

    def _freeze_gate_curve_from_bounds(
        self,
        *,
        solidus_T_C: float | None,
        liquidus_T_C: float | None,
        source: str,
        reasons: list[str],
    ) -> dict[str, Any] | None:
        if (
            solidus_T_C is None
            or liquidus_T_C is None
            or not solidus_T_C < liquidus_T_C
        ):
            reasons.append(f'{source} invalid bounds')
            return None
        return {
            'source': source,
            'solidus_T_C': solidus_T_C,
            'liquidus_T_C': liquidus_T_C,
            'path': (
                (solidus_T_C, 0.0),
                (liquidus_T_C, 1.0),
            ),
        }

    def _freeze_gate_curve_from_path(
        self,
        path: tuple,
        *,
        solidus_T_C: float | None,
        liquidus_T_C: float | None,
        source: str,
        repair_notices: list[dict[str, Any]] | None = None,
        reasons: list[str] | None = None,
    ) -> dict[str, Any] | None:
        from simulator.reduced_real_determinism import record_repair_notice

        points: list[tuple[float, float]] = []
        for point in path:
            if isinstance(point, Mapping):
                raw_temperature_C = point.get('temperature_C', point.get('T_C'))
                raw_liquid_fraction = point.get('liquid_fraction')
            else:
                raw_temperature_C = getattr(point, 'temperature_C', None)
                raw_liquid_fraction = getattr(point, 'liquid_fraction', None)
            temperature_C = self._optional_float(raw_temperature_C)
            liquid_fraction = self._optional_float(raw_liquid_fraction)
            if temperature_C is None or liquid_fraction is None:
                notice = {
                    'type': 'typed_notice',
                    'kind': 'freeze_gate_curve_point_repaired',
                    'site': 'SR18',
                    'temperature_C': _freeze_gate_notice_value(raw_temperature_C),
                    'raw_fraction': _freeze_gate_notice_value(raw_liquid_fraction),
                    'action': 'drop_point',
                }
                record_repair_notice(self, notice)
                if repair_notices is not None and notice not in repair_notices:
                    repair_notices.append(notice)
                continue
            if liquid_fraction_invalidity(liquid_fraction) == 'out_of_range':
                notice = {
                    'type': 'typed_notice',
                    'kind': 'freeze_gate_curve_point_repaired',
                    'site': 'SR18',
                    'temperature_C': temperature_C,
                    'raw_fraction': liquid_fraction,
                    'action': 'clamp_fraction',
                }
                record_repair_notice(self, notice)
                if repair_notices is not None and notice not in repair_notices:
                    repair_notices.append(notice)
            points.append(
                (temperature_C, max(0.0, min(1.0, liquid_fraction)))
            )
        if solidus_T_C is None and points:
            solidus_T_C = min(T for T, _ in points)
        if liquidus_T_C is None and points:
            liquidus_T_C = max(T for T, _ in points)
        if (
            solidus_T_C is None
            or liquidus_T_C is None
            or not solidus_T_C < liquidus_T_C
        ):
            return None
        dedup: dict[float, float] = {}
        for temperature_C, liquid_fraction in sorted(points):
            dedup[temperature_C] = liquid_fraction
        dedup[solidus_T_C] = 0.0
        # The finder declares liquidus only for frac_M within
        # LIQUIDUS_LIQUID_EPSILON of 1 (liquidus.py liquid_threshold).
        # That residual is the declaration tolerance, so snapping it to 1
        # does not invent melt. A supplied fraction below the band is a
        # measured deficit; writing 1 would replace it. No fraction at
        # this temperature is the endpoint convention: nothing was measured
        # there to overwrite.
        raw_at_liquidus = dedup.get(liquidus_T_C)
        if (
            raw_at_liquidus is None
            or raw_at_liquidus >= 1.0 - LIQUIDUS_LIQUID_EPSILON
        ):
            dedup[liquidus_T_C] = 1.0
        else:
            notice = {
                'type': 'typed_notice',
                'kind': FREEZE_GATE_LIQUIDUS_FRACTION_INCONSISTENT,
                'temperature_C': liquidus_T_C,
                'raw_fraction': raw_at_liquidus,
                'action': 'refuse_curve',
            }
            record_repair_notice(self, notice)
            if repair_notices is not None and notice not in repair_notices:
                repair_notices.append(notice)
            if reasons is not None:
                reasons.append(
                    f'{source} {FREEZE_GATE_LIQUIDUS_FRACTION_INCONSISTENT}'
                )
            return None
        ordered = tuple(sorted(dedup.items()))
        if len(ordered) < 2:
            return None
        previous = ordered[0][1]
        for _, liquid_fraction in ordered[1:]:
            if liquid_fraction + 1.0e-9 < previous:
                return None
            previous = liquid_fraction
        return {
            'source': source,
            'solidus_T_C': solidus_T_C,
            'liquidus_T_C': liquidus_T_C,
            'path': ordered,
        }

    @staticmethod
    def _interpolate_freeze_gate_curve(
        curve: Mapping[str, Any],
        temperature_C: float,
    ) -> float:
        solidus_T_C = float(curve['solidus_T_C'])
        liquidus_T_C = float(curve['liquidus_T_C'])
        if (
            melt_regime(
                temperature_K=float(temperature_C) + CELSIUS_TO_KELVIN_OFFSET,
                solidus_K=solidus_T_C + CELSIUS_TO_KELVIN_OFFSET,
                epsilon=0.0,
            )
            == MeltRegime.FROZEN
        ):
            return 0.0
        if temperature_C >= liquidus_T_C:
            return 1.0
        path = tuple(curve.get('path') or ())
        previous_T, previous_fraction = path[0]
        for next_T, next_fraction in path[1:]:
            previous_T = float(previous_T)
            next_T = float(next_T)
            if previous_T <= temperature_C <= next_T:
                span = max(next_T - previous_T, _FREEZE_GATE_EPSILON)
                weight = (temperature_C - previous_T) / span
                return max(
                    0.0,
                    min(
                        1.0,
                        float(previous_fraction)
                        + (float(next_fraction) - float(previous_fraction))
                        * weight,
                    ),
                )
            previous_T, previous_fraction = next_T, next_fraction
        span = max(liquidus_T_C - solidus_T_C, _FREEZE_GATE_EPSILON)
        return max(0.0, min(1.0, (temperature_C - solidus_T_C) / span))

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(parsed):
            return None
        return parsed

    def _build_evaporation_aux_maps(
        self,
        vapor_pressures: dict,
    ) -> _EvaporationAuxMaps:
        """Precompute per-species auxiliary inputs for EVAPORATION_FLUX.

        Returns ``(molar_masses_kg_mol, stoich_by_species,
        available_oxide_kg)``.

        These three maps are everything the kernel provider needs that
        cannot be derived from the request DTOs alone: the
        ``vapor_pressures.yaml`` payload plus the simulator's stoichiometric
        validation.

        Missing parent metadata makes only that species unavailable. Missing
        molar mass or inconsistent stoichiometry still raises.
        """

        molar_masses_kg_mol: dict[str, float] = {}
        stoich_by_species: dict[str, dict] = {}
        available_oxide_kg: dict[str, float] = {}
        unavailable_by_species: dict[str, str] = {}

        cleaned_melt_kg = self.atom_ledger.kg_by_account(
            'process.cleaned_melt')
        spent_reductant_residue_kg = self.atom_ledger.kg_by_account(
            SPENT_REDUCTANT_RESIDUE_ACCOUNT)
        projection_parity_tolerance_pct = 5.0e-12

        for species in tuple(vapor_pressures):
            sp_data = dict(
                _evaporation_vapor_row(self.vapor_pressures, species) or {}
            )
            if not _has_evaporation_parent_oxide(sp_data):
                unavailable_by_species[str(species)] = (
                    'missing_evaporation_stoichiometry'
                )
                continue

            M_g_mol = production_vapor_molar_mass_g_per_mol(
                self.vapor_pressures, species
            )
            if M_g_mol is None:
                raise AccountingError(
                    f"vapor species {species!r} requires "
                    "molar_mass_g_mol metadata before evaporation flux "
                    "can be emitted"
                )
            molar_masses_kg_mol[species] = M_g_mol / 1000.0

            parent_oxide = str(sp_data['parent_oxide'])
            stoich = self._evaporation_stoich(species, sp_data)
            stoich_by_species[species] = dict(stoich)
            # Mirrors core.py::_project_cleaned_melt_from_atom_ledger:
            # cleaned_melt + spent_reductant_residue define the projection,
            # while evaporation debits cleaned_melt only.
            cleaned_oxide_kg = float(cleaned_melt_kg.get(parent_oxide, 0.0))
            projected_ledger_oxide_kg = (
                cleaned_oxide_kg
                + float(spent_reductant_residue_kg.get(parent_oxide, 0.0))
            )
            projection_oxide_kg = float(self.melt.composition_kg.get(
                parent_oxide, 0.0))
            scale_kg = max(
                abs(projected_ledger_oxide_kg), abs(projection_oxide_kg), 1.0
            )
            divergence_pct = (
                abs(projected_ledger_oxide_kg - projection_oxide_kg)
                / scale_kg
                * 100.0
            )
            if divergence_pct > projection_parity_tolerance_pct:
                raise AccountingError(
                    "cleaned_melt projection stale for parent oxide "
                    f"{parent_oxide!r}: atom_ledger={projected_ledger_oxide_kg:.17g} kg "
                    f"melt_projection={projection_oxide_kg:.17g} kg "
                    f"divergence_pct={divergence_pct:.17g}"
                )
            available_oxide_kg[species] = cleaned_oxide_kg

        return _EvaporationAuxMaps(
            molar_masses_kg_mol=molar_masses_kg_mol,
            stoich_by_species=stoich_by_species,
            available_oxide_kg=available_oxide_kg,
            unavailable_by_species=unavailable_by_species,
        )

    def _evaporation_overhead_total_pressure_Pa(
        self,
        overhead_partials_Pa: Mapping[str, float],
    ) -> float:
        partial_sum_Pa = sum(
            max(0.0, float(value))
            for value in overhead_partials_Pa.values()
        )
        control_floor_Pa = max(
            0.0,
            float(getattr(self.melt, 'p_total_mbar', 0.0) or 0.0) * 100.0,
        )
        return max(partial_sum_Pa, control_floor_Pa)

    @staticmethod
    def _vapor_pressure_activity_operands(
        equilibrium: Any, diagnostic: Mapping[str, Any],
    ) -> dict[str, float]:
        operands = diagnostic.get('activities')
        if not isinstance(operands, Mapping):
            operands = getattr(equilibrium, 'activity_coefficients', {}) or {}
        return dict(operands)

    def _resolve_evaporation_vapour_batch(
        self,
        equilibrium: Any,
        *,
        temperature_K: float,
        effective_pressure_source: EffectivePressureSource,
    ) -> Any:
        """Exact-key VapourBatch for VR-11 flux consumer + instrumentation.

        A complete batch owns channel refusal, eligibility, and the active set.
        Only the typed source's species IDs enter activation; its values remain
        outside catalog/request state. Resolve failures fail closed.
        """

        self._last_vapour_batch_resolve_error = {}
        builder = getattr(self, 'build_vapour_batch', None)
        if not callable(builder):
            self._last_vapour_batch_resolve_error = {
                'status': 'unavailable',
                'reason': 'vapour_batch_builder_missing',
                'detail': 'build_vapour_batch is not available on simulator',
            }
            return None
        vapor_pressure_diagnostic = dict(
            getattr(self, '_last_vapor_pressure_diagnostic', {}) or {}
        )
        transport_pO2_bar = vapor_pressure_diagnostic.get('pO2_bar')
        if transport_pO2_bar is None:
            transport_pO2_bar = getattr(equilibrium, 'pO2_bar', None)
        source_reaction_fO2_bar = vapor_pressure_diagnostic.get(
            'source_reaction_fO2_bar'
        )
        if source_reaction_fO2_bar is not None:
            try:
                source_reaction_fO2_bar = float(source_reaction_fO2_bar)
            except (TypeError, ValueError):
                source_reaction_fO2_bar = None
            if source_reaction_fO2_bar is not None and (
                not math.isfinite(source_reaction_fO2_bar)
                or source_reaction_fO2_bar <= 0.0
            ):
                source_reaction_fO2_bar = None
        total_pressure_Pa = None
        try:
            total_pressure_Pa = float(
                getattr(self.melt, 'p_total_mbar', 0.0) or 0.0
            ) * 100.0
        except (TypeError, ValueError):
            total_pressure_Pa = None
        try:
            reported_activities = EvaporationMixin._vapor_pressure_activity_operands(
                equilibrium, vapor_pressure_diagnostic,
            )
            kernel_activities = vapor_pressure_diagnostic.get('activities')
            kernel_activity_source = (
                isinstance(kernel_activities, Mapping)
                and bool(reported_activities)
            )
            if kernel_activity_source:
                activity_metadata = vapor_pressure_diagnostic
                activity_evidence_owner = (
                    '_last_vapor_pressure_diagnostic.activities'
                )
            else:
                activity_metadata = dict(
                    getattr(equilibrium, 'diagnostics', {}) or {}
                )
                activity_evidence_owner = (
                    'EquilibriumResult.activity_coefficients'
                )
            activity_provider_id = activity_metadata.get('activities_provider')
            activity_provenance_raw = (
                activity_metadata.get('vapor_pressure_numerator_provenance')
                or activity_metadata.get('activity_provenance')
                or {}
            )
            if not isinstance(activity_provenance_raw, Mapping):
                activity_provenance_raw = {}
            activity_provenance = {
                str(species_id): dict(provenance)
                for species_id, provenance in dict(activity_provenance_raw).items()
                if isinstance(provenance, Mapping)
                and species_id in reported_activities
            }
            feo_activity = activity_metadata.get('a_FeO_calphad')
            if 'Fe' in reported_activities and isinstance(feo_activity, Mapping):
                feo_sources = feo_activity.get('sources')
                if isinstance(feo_sources, Mapping) and feo_sources:
                    activity_provenance.setdefault('Fe', {})[
                        'activity_evidence_ref'
                    ] = '; '.join(str(value) for value in feo_sources.values())
            standard_state_raw = activity_metadata.get(
                'activities_standard_state'
            )
            activity_standard_state = None
            if isinstance(standard_state_raw, Mapping):
                activity_standard_state = StandardStateIdentity(
                    convention=str(standard_state_raw.get('convention') or ''),
                    phase=str(standard_state_raw.get('phase') or ''),
                    reference_pressure_bar=float(
                        standard_state_raw.get('reference_pressure_bar')
                    ),
                    reference_temperature_K=(
                        float(standard_state_raw['reference_temperature_K'])
                        if standard_state_raw.get('reference_temperature_K')
                        is not None
                        else None
                    ),
                    component_basis=str(
                        standard_state_raw.get('component_basis') or ''
                    ),
                )
            # Compose: INCOMING structured citation/evidence_ref first
            # (external DOI provenance for activity-corrected channels); HEAD
            # provider-token fallback for channels without melt-oxide citation;
            # HEAD b-133 P-carrier authority annotations when present.
            activity_evidence_refs: dict[str, str] = {}
            for species_id in reported_activities:
                if not activity_provider_id or activity_standard_state is None:
                    continue
                provenance = dict(activity_provenance.get(str(species_id)) or {})
                citation = (
                    provenance.get('melt_oxide_gamma_citation')
                    or provenance.get('activity_evidence_ref')
                )
                if citation:
                    activity_evidence_refs[str(species_id)] = str(citation)
                    continue
                evidence = (
                    f'{activity_provider_id}:'
                    f'{activity_evidence_owner}[{species_id}]'
                )
                if species_id in _STAGE0_PHOSPHORUS_VAPOUR_CARRIERS:
                    authority_status = provenance.get(
                        'melt_oxide_activity_authority_status'
                    )
                    gamma_range = provenance.get(
                        'melt_oxide_gamma_valid_range_K'
                    )
                    activity_basis = (
                        'parent_oxide'
                        if provenance.get('equivalent_parent_oxide_activity')
                        == reported_activities.get(species_id)
                        else 'reported_component'
                    )
                    if authority_status:
                        evidence += f';authority_status={authority_status}'
                    if gamma_range:
                        evidence += (
                            f';gamma_valid_range_K={tuple(gamma_range)}'
                        )
                    evidence += f';activity_basis={activity_basis}'
                activity_evidence_refs[str(species_id)] = evidence
            activity_standard_states = {
                str(species_id): activity_standard_state
                for species_id in reported_activities
                if activity_provider_id and activity_standard_state is not None
            }
            campaign = getattr(self.melt, 'campaign', None)
            campaign_name = str(getattr(campaign, 'name', '') or '')
            # Keep the global request phase on the legacy hot-train contract.
            # C0 does not select stage0_p_carriers: phosphorus stays condensed
            # through C0. C0B still selects c0b_p_cleanup. Flipping the whole
            # phase to stage0 would change unrelated Na/Fe eligibility.
            request_stage = (
                'c0b_p_cleanup'
                if campaign_name == 'C0B'
                else 'evaporation'
            )
            effective_pressure_species_ids = effective_pressure_source.species_ids
            if campaign_name == 'C0B':
                effective_pressure_species_ids = frozenset(
                    species_id
                    for species_id in effective_pressure_species_ids
                    if species_id in _STAGE0_PHOSPHORUS_VAPOUR_CARRIERS
                )
            elif campaign_name == 'C0':
                # Drop the carriers from the claim. Leaving them claimed on
                # stage evaporation fail-closes the batch: their stage0_only
                # predicate does not wake, and the pre-RG guard refuses any
                # claimed channel that is not flux-eligible.
                effective_pressure_species_ids = frozenset(
                    species_id
                    for species_id in effective_pressure_species_ids
                    if species_id not in _STAGE0_PHOSPHORUS_VAPOUR_CARRIERS
                )
            domain_at = getattr(self, "liquid_feo_domain_at", None)
            liquid_feo_domain = (
                domain_at(float(temperature_K)) if callable(domain_at) else None
            )
            batch = builder(
                temperature_K=float(temperature_K),
                process_phase='hot_train',
                stage=request_stage,
                total_pressure_Pa=total_pressure_Pa,
                fO2_bar=(
                    float(transport_pO2_bar)
                    if transport_pO2_bar is not None
                    else None
                ),
                source_reaction_fO2_bar=source_reaction_fO2_bar,
                source_reaction_fO2_log10=(
                    float(vapor_pressure_diagnostic["source_reaction_fO2_log10"])
                    if vapor_pressure_diagnostic.get("source_reaction_fO2_log10")
                    is not None
                    else None
                ),
                source_reaction_activity_pressure_bar=(
                    float(
                        vapor_pressure_diagnostic[
                            "source_reaction_activity_pressure_bar"
                        ]
                    )
                    if vapor_pressure_diagnostic.get(
                        "source_reaction_activity_pressure_bar"
                    )
                    is not None
                    else None
                ),
                source_reaction_redox_model_id=(
                    str(vapor_pressure_diagnostic["source_reaction_redox_model_id"])
                    if vapor_pressure_diagnostic.get(
                        "source_reaction_redox_model_id"
                    )
                    else None
                ),
                source_reaction_composition_wt_pct=(
                    dict(
                        vapor_pressure_diagnostic.get(
                            "source_reaction_composition_wt_pct"
                        )
                        or {}
                    )
                ),
                source_reaction_activities=reported_activities,
                source_reaction_activity_provider=(
                    str(activity_provider_id) if activity_provider_id else None
                ),
                source_reaction_activity_evidence_refs=activity_evidence_refs,
                source_reaction_activity_standard_states=(
                    activity_standard_states
                ),
                source_reaction_activity_provenance=activity_provenance,
                melt_activity_shadow_enabled=getattr(
                    self, "_melt_activity_shadow_enabled", False
                ),
                melt_activity_engine_inputs=getattr(
                    self, "_melt_activity_engine_inputs", {}
                ),
                liquid_feo_domain=liquid_feo_domain,
                flux_activation_context=FluxActivationContext(
                    epoch=FLUX_ACTIVATION_EPOCH_PRE_RG,
                    effective_pressure_species_ids=effective_pressure_species_ids,
                ),
            )
        except Exception as exc:  # noqa: BLE001 — typed failure, not live fallback
            self._last_vapour_batch_resolve_error = {
                'status': 'unavailable',
                'reason': 'vapour_batch_resolve_failed',
                'detail': str(exc),
            }
            return None
        if batch is None and not getattr(self, '_last_vapour_batch_resolve_error', None):
            self._last_vapour_batch_resolve_error = {
                'status': 'unavailable',
                'reason': 'vapour_batch_unavailable',
                'detail': 'build_vapour_batch returned None without a typed error',
            }
        return batch

    def _evaporation_flux_control_inputs(
        self,
        equilibrium: Any,
        *,
        overhead_partials_Pa: Mapping[str, float],
        overhead_pressure_pa: float | None = None,
        vapour_batch_flux_pressures_Pa: Mapping[str, float] | None = None,
        vapour_batch_report: Mapping[str, Any] | None = None,
        vapour_batch_flux_overlay: Mapping[str, Any] | None = None,
        vapor_pressure_diagnostic_override: Mapping[str, Any] | None = None,
    ) -> _EvaporationFluxControlResult:
        # VR-11: this already-gated map comes from the typed effective-pressure
        # seam. The kernel never reads equilibrium.vapor_pressures_Pa for flux.
        if vapour_batch_flux_pressures_Pa is not None:
            vapor_pressures = dict(vapour_batch_flux_pressures_Pa)
        else:
            vapor_pressures = {}
        transport_cache = getattr(
            self, '_vapour_batch_hour_availability', None
        )
        cached_members = (
            transport_cache.get('admitted_member_species')
            if isinstance(transport_cache, Mapping)
            else None
        )
        auxiliary_flux_members = (
            dict.fromkeys((str(species) for species in cached_members), 0.0)
            if isinstance(cached_members, (tuple, list, set, frozenset))
            else vapor_pressures
        )
        # Reporting projection of the post-decision flux map (not a flux input).
        with compatibility_pressure_read_context('reporting_projection'):
            live_reporting = dict(
                getattr(equilibrium, 'vapor_pressures_Pa', {}) or {}
            )
        aux_maps = self._build_evaporation_aux_maps(
            dict(auxiliary_flux_members)
        )
        auxiliary_unavailable = dict(
            getattr(aux_maps, 'unavailable_by_species', {}) or {}
        )
        (
            molar_masses_kg_mol,
            stoich_by_species,
            available_oxide_kg,
        ) = aux_maps
        vapor_pressures = {
            species: pressure
            for species, pressure in vapor_pressures.items()
            if str(species) not in auxiliary_unavailable
        }
        vapor_pressure_diagnostic = dict(
            vapor_pressure_diagnostic_override
            if isinstance(vapor_pressure_diagnostic_override, Mapping)
            else getattr(self, '_last_vapor_pressure_diagnostic', {}) or {}
        )
        kernel_config = dict(
            getattr(self, 'setpoints', {}).get('chemistry_kernel', {}) or {}
        )
        carrier_gas = EvaporationMixin._evaporation_carrier_gas(self)
        # d-376 amendment 1 O2 lower bound. The publisher subtracts
        # overhead O2 on a disabled film or an HKL upper bound only
        # when this step closed a surface root. ``root_closed`` is that
        # predicate (applied and a solved surface). A missing context,
        # and a closed gate, leave the key false: every species,
        # including O2, keeps green's film rule. Round 7.
        closure_context = getattr(self, "_langmuir_flux_closure_context", None)
        surface_oxygen_bound = bool(
            isinstance(closure_context, Mapping)
            and closure_context.get("root_closed")
        )
        controls: dict[str, Any] = {
            # Reporting / shadow projection only — kernel must NOT read this
            # for flux (source guard). Distinct from the batch flux map so a
            # missing batch key cannot silently fall through to live values.
            'vapor_pressures_Pa': live_reporting,
            'vapor_pressures_source': dict(
                getattr(equilibrium, 'vapor_pressures_source', {}) or {}
            ),
            'vapor_pressure_numerator_provenance': dict(
                vapor_pressure_diagnostic.get(
                    'vapor_pressure_numerator_provenance'
                ) or {}
            ),
            'vapor_pressure_activities': EvaporationMixin._vapor_pressure_activity_operands(
                equilibrium, vapor_pressure_diagnostic,
            ),
            'pO2_bar': vapor_pressure_diagnostic.get('pO2_bar'),
            'molar_mass_kg_mol': molar_masses_kg_mol,
            'stoich_by_species': stoich_by_species,
            'available_oxide_kg': available_oxide_kg,
            'melt_surface_area_m2': float(self.melt.melt_surface_area_m2),
            'stir_factor': {
                'axial': clamp_stir_factor(self.melt.stir_state.axial),
                'radial': clamp_stir_factor(self.melt.stir_state.radial),
            },
            'pipe_diameter_m': float(
                getattr(self.overhead_model, 'pipe_diameter_m', 0.12)
            ),
            'commanded_pressure_pa': max(
                0.0,
                float(getattr(self.melt, 'p_total_mbar', 0.0) or 0.0) * 100.0,
            ),
            'gas_temperature_K': float(
                getattr(self.overhead, 'headspace_temperature_K', 0.0)
                or self.melt.temperature_C + CELSIUS_TO_KELVIN_OFFSET
            ),
            'carrier_gas': carrier_gas,
            'evaporation_series_resistance': dict(
                kernel_config.get('evaporation_series_resistance', {}) or {}
            ),
            # Do not copy chemistry_kernel['gas_resistance_enabled'].
            # Green's publisher never read that top-level key: the film
            # stays on unless the nested series override says otherwise.
            # Copying it changed buffered in-band rates. b-906 round 7.
            'alpha': _load_evaporation_alpha_by_species(self.vapor_pressures),
            'alpha_envelope': _load_evaporation_alpha_envelope_by_species(
                self.vapor_pressures
            ),
            'campaign_name': str(
                getattr(
                    getattr(getattr(self, 'melt', None), 'campaign', None),
                    'name',
                    '',
                )
                or ''
            ),
            'hkl_upper_bound_transport_species': tuple(sorted(
                str(species) for species in free_molecular_upper_bound
            )),
        }
        property_rows = (
            transport_cache.get('lennard_jones_property_rows_by_species', {})
            if isinstance(transport_cache, Mapping)
            else {}
        )
        parameters_by_species = (
            transport_cache.get('lennard_jones_parameters_by_species', {})
            if isinstance(transport_cache, Mapping)
            else {}
        )
        eligibility_by_species = (
            transport_cache.get('transport_eligibility_by_species', {})
            if isinstance(transport_cache, Mapping)
            else {}
        )
        estimated_notices = (
            transport_cache.get('transport_parameter_notices_by_species', {})
            if isinstance(transport_cache, Mapping)
            else {}
        )
        free_molecular_upper_bound = (
            transport_cache.get('hkl_upper_bound_transport_species', ())
            if isinstance(transport_cache, Mapping)
            else _load_hkl_upper_bound_transport_species(
                self.vapor_pressures
            )
        )
        transport_sidecar = {
            'lennard_jones_property_rows_by_species': {
                str(species): dict(row)
                for species, row in property_rows.items()
                if isinstance(property_rows, Mapping)
                and isinstance(row, Mapping)
            } if isinstance(property_rows, Mapping) else {},
            'lennard_jones_parameters_by_species': {
                str(species): dict(parameters)
                for species, parameters in parameters_by_species.items()
                if isinstance(parameters_by_species, Mapping)
                and isinstance(parameters, Mapping)
            } if isinstance(parameters_by_species, Mapping) else {},
            'transport_eligibility_by_species': {
                str(species): str(decision)
                for species, decision in eligibility_by_species.items()
            } if isinstance(eligibility_by_species, Mapping) else {},
            'transport_parameter_notices_by_species': {
                str(species): dict(notice)
                for species, notice in estimated_notices.items()
                if isinstance(estimated_notices, Mapping)
                and isinstance(notice, Mapping)
            } if isinstance(estimated_notices, Mapping) else {},
        }
        if vapour_batch_report is not None:
            controls[CONTROL_BATCH_REPORT_KEY] = dict(vapour_batch_report)
        from engines.builtin.evaporation_flux import alpha_policy_controls

        controls.update(alpha_policy_controls(kernel_config))
        controls = _EvaporationProviderControls(
            controls,
            transport_sidecar=transport_sidecar,
        )
        controls = EvaporationMixin._evaporation_provider_controls_for_state(
            self,
            controls,
            overhead_partials_Pa=overhead_partials_Pa,
            overhead_pressure_pa=overhead_pressure_pa,
            flux_pressures=vapor_pressures,
            flux_overlay=vapour_batch_flux_overlay,
            surface_oxygen_bound=surface_oxygen_bound,
        )
        return _EvaporationFluxControlResult(
            controls=controls,
            molar_masses_kg_mol=molar_masses_kg_mol,
            unavailable_by_species=auxiliary_unavailable,
            transport_sidecar=transport_sidecar,
        )

    def _evaporation_provider_controls_for_state(
        self,
        base_controls: Mapping[str, Any],
        *,
        overhead_partials_Pa: Mapping[str, float],
        overhead_pressure_pa: float | None,
        flux_pressures: Mapping[str, float] | None,
        flux_overlay: Mapping[str, Any] | None,
        surface_oxygen_bound: bool,
    ) -> dict[str, Any]:
        """Build the common provider controls for live and Picard flux states."""
        filmed_partials, filmed_pressure = EvaporationMixin._langmuir_film(
            self, overhead_partials_Pa
        )
        transport_sidecar = getattr(base_controls, 'transport_sidecar', {})
        if not isinstance(transport_sidecar, Mapping):
            transport_sidecar = {}
        controls = _EvaporationProviderControls(
            base_controls,
            transport_sidecar=transport_sidecar,
        )
        controls.update({
            CONTROL_FLUX_PRESSURES_KEY: dict(flux_pressures or {}),
            'overhead_partials_Pa': filmed_partials,
            'overhead_pressure_pa': (
                filmed_pressure
                if overhead_pressure_pa is None
                else float(overhead_pressure_pa)
            ),
            'surface_oxygen_bound': bool(surface_oxygen_bound),
        })
        if flux_pressures is not None and flux_overlay is not None:
            overlay_controls = provider_flux_overlay_controls(
                copy.deepcopy(dict(flux_overlay))
            )
            overlay = overlay_controls.get('vapour_batch_flux_overlay')
            if isinstance(overlay, Mapping):
                # IntentRequest freezes a shallow copy of the outer map;
                # metadata on this existing value survives without a key.
                overlay_controls['vapour_batch_flux_overlay'] = (
                    _EvaporationProviderControls(
                        overlay,
                        transport_sidecar=transport_sidecar,
                    )
                )
            controls.update(overlay_controls)
        return controls

    def _project_evaporation_overhead_source_mol_hr(
        self,
        rates_kg_hr: Mapping[str, float],
        stoich_by_species: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, float]:
        resolved_stoich = dict(stoich_by_species)
        for species, raw_rate in rates_kg_hr.items():
            if float(raw_rate) <= 0.0 or resolved_stoich.get(species):
                continue
            sp_data = dict(
                (self.vapor_pressures.get('metals', {}) or {}).get(species, {})
                or (self.vapor_pressures.get('oxide_vapors', {}) or {}).get(species, {})
            )
            if sp_data:
                # The auxiliary-map owner omits missing-parent species and
                # the provider marks them unavailable; a positive rate for
                # one would violate that contract before ledger projection.
                assert _has_evaporation_parent_oxide(sp_data)
                resolved_stoich[species] = self._evaporation_stoich(species, sp_data)
        return _project_evaporation_overhead_source_mol_hr(
            rates_kg_hr, resolved_stoich, self.species_formula_registry)

    def _scale_wall_supply_computed(self, scale: float) -> None:
        """Apply one physical scale to every computed supply rate.

        The melt debit may drop a product at or below its reporting
        floor. This scale does not. A non-positive result is kept when
        it is exactly zero so the hour stays an evidenced zero.
        """

        computed = getattr(self, "_wall_supply_computed_kg_hr", None)
        if not isinstance(computed, Mapping):
            return
        factor = float(scale)
        scaled: dict[str, float] = {}
        for species, raw in computed.items():
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                continue
            rate = float(raw) * factor
            if math.isfinite(rate) and rate >= 0.0:
                scaled[str(species)] = rate
        self._wall_supply_computed_kg_hr = scaled

    def _overlay_depleted_wall_supply(
        self,
        supply_out: Mapping[str, float],
        *,
        phase_scale: float | None,
    ) -> None:
        """Fold post-depletion rates into the computed supply map.

        Subthreshold rates never enter the debit, so depletion does not
        see them. They take the same phase scale the debit rates took.
        ``supply_out`` is already phase-scaled and overwrites those
        species, including a positive rate the debit floor then dropped
        and an explicit zero where the parent or oxygen inventory is gone.
        """

        scale = 1.0 if phase_scale is None else float(phase_scale)
        if scale != 1.0:
            self._scale_wall_supply_computed(scale)
        computed = dict(getattr(self, "_wall_supply_computed_kg_hr", {}) or {})
        for species, raw in supply_out.items():
            rate = float(raw)
            if math.isfinite(rate) and rate >= 0.0:
                computed[str(species)] = rate
        self._wall_supply_computed_kg_hr = computed

    def _apply_analytic_evaporation_depletion(
        self, evap_flux: EvaporationFlux, dt_hr: float = 1.0,
    ) -> EvaporationFlux:
        """Apply sub-tick first-order depletion to raw HKL evaporation rates."""
        if dt_hr <= 0.0 or not evap_flux.species_kg_hr:
            return evap_flux

        phase_scalar = self._record_phase_context_diagnostic(
            'evaporation_depletion', scalar_liquid_fraction=1.0)
        supply_out: dict[str, float] = {}
        effective_rates = self._analytic_evaporation_depletion_rates(
            evap_flux.species_kg_hr,
            dt_hr=dt_hr,
            phase_scalar=phase_scalar,
            cleaned_melt_kg=self.atom_ledger.kg_by_account(
                'process.cleaned_melt'
            ),
            available_o2_kg=float(
                self.atom_ledger.kg_by_account('process.overhead_gas').get(
                    'O2', 0.0
                )
            ),
            supply_out=supply_out,
        )
        self._overlay_depleted_wall_supply(
            supply_out,
            phase_scale=phase_scalar,
        )
        smoothed = EvaporationFlux(
            species_kg_hr=effective_rates,
            alpha_authority_status_by_species={
                species: status
                for species, status in (
                    evap_flux.alpha_authority_status_by_species.items()
                )
                if float(effective_rates.get(species, 0.0)) > _vapour_debit_floor_kg_hr()
            },
            # Canonical serialized answers are treated as immutable evidence;
            # depletion changes rates only and can safely share the records.
            carrier_authority_by_species=(
                evap_flux.carrier_authority_by_species
            ),
        )
        smoothed.update_totals()
        return smoothed

    def _analytic_evaporation_depletion_rates(
        self,
        raw_rates_kg_hr: Mapping[str, float],
        *,
        dt_hr: float,
        phase_scalar: float,
        cleaned_melt_kg: Mapping[str, float],
        available_o2_kg: float,
        supply_out: dict[str, float] | None = None,
    ) -> dict[str, float]:
        """Pure frozen-inventory transform from raw HK rates to actual rates.

        ``supply_out``, when passed, receives every input species' computed
        post-depletion rate, including a positive rate at or below the
        debit floor and a zero where parent or oxygen inventory is gone.
        It does not change the debit map this function returns.
        """

        def _note(species: object, rate: float) -> None:
            if supply_out is None:
                return
            value = float(rate)
            if math.isfinite(value) and value >= 0.0:
                supply_out[str(species)] = value

        if dt_hr <= 0.0 or not raw_rates_kg_hr:
            kept = {
                str(species): max(0.0, float(rate))
                for species, rate in raw_rates_kg_hr.items()
            }
            for species, rate in kept.items():
                _note(species, rate)
            return kept

        parent_groups: dict[str, list[dict]] = defaultdict(list)

        for species in sorted(raw_rates_kg_hr):
            raw_rate_kg_hr = (
                float(raw_rates_kg_hr.get(species, 0.0))
                * phase_scalar
            )
            if raw_rate_kg_hr <= 1e-12:
                # Debit floor. The computed rate is still the supply.
                _note(species, raw_rate_kg_hr if raw_rate_kg_hr > 0.0 else 0.0)
                continue
            sp_data = dict(
                _evaporation_vapor_row(self.vapor_pressures, species) or {}
            )
            stoich = self._evaporation_stoich(species, sp_data)
            parent_oxide = stoich['parent_oxide']
            oxide_per_product_kg = float(stoich['oxide_per_product_kg'])
            parent_draw_kg_hr = raw_rate_kg_hr * oxide_per_product_kg
            if parent_draw_kg_hr <= 1e-12:
                _note(species, raw_rate_kg_hr)
                continue
            parent_groups[parent_oxide].append({
                'species': species,
                'sp_data': sp_data,
                'stoich': stoich,
                'raw_rate_kg_hr': raw_rate_kg_hr,
                'parent_draw_kg_hr': parent_draw_kg_hr,
            })

        effective_rates: dict[str, float] = {}
        max_fraction = math.nextafter(1.0, 0.0)
        for parent_oxide in sorted(parent_groups):
            entries = parent_groups[parent_oxide]
            available_parent_kg = float(cleaned_melt_kg.get(parent_oxide, 0.0))
            total_parent_draw_kg_hr = sum(
                entry['parent_draw_kg_hr'] for entry in entries)
            # Empty inventory has no draw. A positive mass uses one
            # first-order draw. The debit still requires the group and
            # the product to clear 1e-12 kg; a smaller positive product
            # is the supply and is not a debit.
            if available_parent_kg <= 0.0 or total_parent_draw_kg_hr <= 0.0:
                for entry in entries:
                    _note(entry['species'], 0.0)
                continue
            group_clears_debit_floor = (
                available_parent_kg > 1e-12
                and total_parent_draw_kg_hr > 1e-12
            )
            k_hr = total_parent_draw_kg_hr / available_parent_kg
            depletion_fraction = -math.expm1(-k_hr * dt_hr)
            depletion_fraction = max(
                0.0, min(max_fraction, depletion_fraction))
            parent_draw_kg = available_parent_kg * depletion_fraction
            for entry in entries:
                share = entry['parent_draw_kg_hr'] / total_parent_draw_kg_hr
                product_kg = (
                    parent_draw_kg
                    * share
                    / float(entry['stoich']['oxide_per_product_kg'])
                )
                product_rate = product_kg / dt_hr
                if group_clears_debit_floor and product_kg > 1e-12:
                    effective_rates[entry['species']] = product_rate
                _note(
                    entry['species'],
                    product_rate if product_kg > 0.0 else 0.0,
                )

        o2_draws: list[tuple[str, float]] = []
        for parent_oxide in sorted(parent_groups):
            for entry in parent_groups[parent_oxide]:
                species = entry['species']
                rate_kg_hr = float(effective_rates.get(species, 0.0))
                O2_per_product_kg = float(
                    entry['stoich'].get('O2_per_product_kg', 0.0))
                if rate_kg_hr > 1e-12 and O2_per_product_kg < -1e-12:
                    o2_draws.append(
                        (species, rate_kg_hr * abs(O2_per_product_kg)))
        total_o2_draw_kg_hr = sum(draw for _species, draw in o2_draws)
        if total_o2_draw_kg_hr <= 1e-12:
            return effective_rates

        if available_o2_kg <= 1e-12:
            for species, _draw in o2_draws:
                effective_rates.pop(species, None)
                _note(species, 0.0)
            return effective_rates

        max_fraction = math.nextafter(1.0, 0.0)
        k_hr = total_o2_draw_kg_hr / float(available_o2_kg)
        depletion_fraction = -math.expm1(-k_hr * dt_hr)
        depletion_fraction = max(0.0, min(max_fraction, depletion_fraction))
        allowed_o2_draw_kg = float(available_o2_kg) * depletion_fraction
        by_species_draw = dict(o2_draws)
        for species, required_o2_kg_hr in o2_draws:
            if required_o2_kg_hr <= 1e-12:
                continue
            allowed_draw_kg = (
                allowed_o2_draw_kg
                * required_o2_kg_hr
                / total_o2_draw_kg_hr
            )
            current_rate = float(effective_rates.get(species, 0.0))
            allowed_rate = current_rate * (
                allowed_draw_kg / by_species_draw[species])
            if allowed_rate <= 1e-12:
                effective_rates.pop(species, None)
                _note(species, allowed_rate if allowed_rate > 0.0 else 0.0)
            else:
                applied = min(current_rate, allowed_rate)
                effective_rates[species] = applied
                _note(species, applied)
        return effective_rates

    def _route_to_condensation(
        self, evap_flux: EvaporationFlux
    ) -> EvaporationFlux:
        """
        Route evaporated species through the condensation train.

        Each species flows from Stage 0 (hot duct) downward through
        successive stages.  At each stage, a fraction condenses based
        on the condensation efficiency model:

            η = 1 - exp(-residence_time / τ_condensation)

        Species condense preferentially in stages where the stage
        temperature is well below the species' condensation temperature.

        For elemental-metal evaporation from a parent oxide, the oxygen
        coproduct is credited to ``reservoir.fo2_buffer``. That redox buffer is
        not a terminal stored-oxygen product account.

        CONDENSATION_ROUTE intent -- kernel-authoritative.

        \\goal BUILTIN-ENGINE-EXTRACTION (#7), fourth flip and the
        SECOND authoritative intent in the migration. The legacy
        ``CondensationModel.route()`` still computes the per-stage
        deposition projection (η model, residence times), but the
        ledger transition that moves vapor from
        ``process.overhead_gas`` to ``process.condensation_train`` is
        now owned by the :class:`BuiltinCondensationRouteProvider` and
        committed through the kernel. The flow per species is:

        1. EVAPORATION_TRANSITION dispatched with ``remaining=rate`` --
           ALL vapor routed to ``process.overhead_gas``; elemental-metal
           parent-oxide oxygen stays in ``reservoir.fo2_buffer``. No
           condensation_train credit from that intent.
        2. CONDENSATION_ROUTE dispatched with the analytically smoothed
           per-species condensed_kg derived from
           ``route_result.remaining_by_species`` -- debits
           ``process.overhead_gas[species]`` and credits
           ``process.condensation_train[products]`` (with SiO
           disproportionation when sp_data declares the product map).
        3. ``_project_condensed_stage_collection`` projects the actual
           credited_condensed_kg onto stage UI bookkeeping.

        Returns an :class:`EvaporationFlux` containing only this tick's vapor
        that remains free in ``process.overhead_gas`` after all committed
        baffle, wall, and retained-holdup routing. The separately recorded
        ``_ledger_committed_evap_flux_this_tick`` excludes any carrier whose
        coupled transition was rejected at the numerical floor; the caller
        uses that committed flux for reporting, transport, and energy.

        Between the two kernel commits the vapor passes through overhead_gas.
        ``tests/chemistry/test_builtin_condensation_route_provider.py`` checks
        current split-path mass closure, nonzero deposition, and wall-deposit
        goldens, but contains no pre-flip operand and therefore does not prove
        exact legacy single-step end-state parity. The shadow comparator was
        removed at flip time, so that exact parity is currently unestablished.
        """
        phase_scalar = self._record_phase_context_diagnostic(
            'condensation_feed', scalar_liquid_fraction=1.0)
        if not getattr(self.condensation_model, '_knudsen_policy_configured', False):
            transport = self.overhead_model.estimate_transport_state(
                evap_flux,
                self.melt,
            )
            self.condensation_model.configure_operating_conditions(
                overhead_pressure_mbar=transport['pressure_mbar'],
                species_partial_pressures_mbar=(
                    self.overhead_model.species_partial_pressures(
                        evap_flux,
                        transport['vapor_pressure_mbar'],
                        self.species_formula_registry,
                    )
                ),
                pipe_diameter_m=self.overhead_model.pipe_diameter_m,
                # Chapman-Enskog diffusion uses bulk-gas temperature; the
                # separate pipe temperature is a wall/liner condition.
                gas_temperature_C=transport['conductance_temperature_C'],
                stage_area_m2_by_stage=transport['stage_area_m2_by_stage'],
                stage_hydraulic_diameter_m_by_stage=transport[
                    'stage_hydraulic_diameter_m_by_stage'
                ],
                stage_area_geometry_provenance_notice=transport.get(
                    'stage_area_geometry_provenance_notice', {}),
                carrier_gas=self._resolve_condensation_carrier_gas(),
                campaign_name=str(getattr(self.melt.campaign, 'name', '')),
                campaign_hour=float(getattr(self.melt, 'campaign_hour', 0.0) or 0.0),
            )
        route_result = self.condensation_model.route(
            evap_flux, self.melt)

        antoine_extrapolations = dict(
            getattr(route_result, 'antoine_extrapolations', {}) or {}
        )
        antoine_extrapolation_warnings = tuple(
            getattr(route_result, 'antoine_extrapolation_warnings', ()) or ()
        )
        warnings_represented_by_records: set[str] = set()
        for record_key, record in antoine_extrapolations.items():
            if not isinstance(record, Mapping):
                continue
            status = record.get('status')
            if status is not None:
                warnings_represented_by_records.add(str(status))
            valid_range = record.get('valid_range_K')
            temperature_K = record.get('temperature_K')
            if not (
                isinstance(valid_range, (list, tuple))
                and len(valid_range) == 2
            ):
                continue
            try:
                valid_low = float(valid_range[0])
                valid_high = float(valid_range[1])
                temperature = float(temperature_K)
            except (TypeError, ValueError):
                continue
            species = str(record_key).split('#', 1)[0].split(':', 1)[0]
            temperature_text = f"{temperature:.3f}".rstrip('0').rstrip('.')
            warnings_represented_by_records.add(
                f"{species} metal Antoine fit extrapolated beyond "
                f"valid_range_K [{valid_low:g}, {valid_high:g}] at "
                f"{temperature_text} K"
            )
        distinct_warnings = set(antoine_extrapolation_warnings)
        antoine_degraded_path_count = len(antoine_extrapolations) + len(
            distinct_warnings - warnings_represented_by_records
        )
        if antoine_degraded_path_count:
            self._record_degraded_path_engagement(
                'condensation_antoine_extrapolation',
                count=antoine_degraded_path_count,
            )
        capture_budget_notice = dict(
            getattr(route_result, 'capture_budget_regularizer_notice', {}) or {}
        )
        if capture_budget_notice:
            self._record_degraded_path_engagement(
                'capture_budget_regularizer',
                count=1,
            )
        transport_notice = dict(
            getattr(route_result, 'transport_parameter_notice', {}) or {}
        )
        if transport_notice:
            transport_species = tuple(transport_notice.get('species', ()) or ())
            self._record_degraded_path_engagement(
                'transport_d_ab_proxy',
                count=len(transport_species) or 1,
            )

        species_order = tuple(
            getattr(route_result, 'wall_route_species_order', ()) or (
                evap_flux.species_kg_hr.keys()
            )
        )
        canonical_species_order = species_order
        if any(reaction.get('uptake_mol', 0.0) > 0.0 for reaction in
               route_result.stage3_reactive_alkali_by_species.values()):
            # Commit actual gas-side oxygen sources before reactions debit
            # them. Allocation was computed from the complete parcel above;
            # this only satisfies the ledger's per-transition no-overdraft
            # invariant, including sources outside the default SiO-first order.
            stoich_by_species = {
                sp: self._evaporation_stoich(sp, (
                    self.vapor_pressures.get('metals', {}).get(sp)
                    or self.vapor_pressures.get('oxide_vapors', {}).get(sp) or {}))
                for sp in species_order
            }
            source_species = {
                sp for sp in species_order
                if _project_evaporation_overhead_source_mol_hr(
                    {sp: evap_flux.species_kg_hr[sp]}, stoich_by_species,
                    self.species_formula_registry).get('O2', 0.0) > 0.0
            }
            species_order = tuple(sorted(species_order, key=lambda sp: sp not in source_species))
        residual_species_kg_hr = {
            species: max(0.0, float(rate_kg_hr) * phase_scalar)
            for species, rate_kg_hr in evap_flux.species_kg_hr.items()
        }
        committed_species_kg_hr: dict[str, float] = {}
        evaporation_transitions = {}
        for species in species_order:
            if species not in evap_flux.species_kg_hr:
                continue
            rate_kg_hr = evap_flux.species_kg_hr[species] * phase_scalar
            overhead_before_kg = float(
                self.atom_ledger.kg_by_account('process.overhead_gas').get(
                    species, 0.0,
                )
                or 0.0
            )
            route_outcome = self._route_evaporated_species_to_condensation(
                route_result,
                species,
                rate_kg_hr,
                apply_evaporative_redox_source_terms=False,
            )
            if route_outcome.get('evaporation_transition') is not None:
                committed_species_kg_hr[species] = rate_kg_hr
                evaporation_transitions[species] = route_outcome['evaporation_transition']
            overhead_after_kg = float(
                self.atom_ledger.kg_by_account('process.overhead_gas').get(
                    species, 0.0,
                )
                or 0.0
            )

            # Premise: the ledger delta spans this tick's full vapor credit and
            # every committed removal (baffle, wall, or retained holdup).
            # Algebra: free-vapor delta = (before + evolved - removals) - before
            # = evolved - removals. Unit check: kg - kg = kg per one-hour tick,
            # numerically kg/hr. Sanity: zero removal leaves the evolved rate;
            # complete committed removal leaves zero, independent of diagnostics.
            residual_species_kg_hr[species] = max(
                0.0, overhead_after_kg - overhead_before_kg,
            )

        # Premise: the finite-capacity redox clamp is sequential and nonlinear;
        # ledger order can change when its bound is reached. Algebra: in general
        # clamp(clamp(B + s1) + s2) != clamp(clamp(B + s2) + s1) when opposing
        # carrier sources straddle a bound. Unit check: B and each s are mol O2
        # equivalent. Limiting case: with one committed carrier this is the old
        # single-transition result; with none, there is no redox update.
        # Ledger commits may be reordered to prevent Stage-3 overdrafts, so
        # apply each transition once after the loop in canonical carrier order.
        for species in canonical_species_order:
            transition = evaporation_transitions.get(species)
            if transition is not None:
                self._apply_evaporative_redox_source_terms(
                    transition,
                    exchange_direction='redox_source:evaporative_loss',
                )

        self._apply_organic_tar_wall_deposit()
        self._sync_oxygen_kg_counters()

        committed_flux = EvaporationFlux(
            species_kg_hr=committed_species_kg_hr,
            alpha_authority_status_by_species={
                species: status
                for species, status in (
                    evap_flux.alpha_authority_status_by_species.items()
                )
                if species in committed_species_kg_hr
            },
            # Preserve the exact-key audit union even when a refused/proven-zero
            # carrier has no committed mass. Hour snapshots must retain why it
            # did not debit, not only the subset that produced kg/hr.
            carrier_authority_by_species=(
                evap_flux.carrier_authority_by_species
            ),
        )
        committed_flux.update_totals()
        # d-376 amendment 1 wall-deposit ruling: the supply bound is the
        # vapour the publisher computed, not only the ledger debit.
        # species_kg_hr stays the committed subset, so a carrier with no
        # evaporation transition does not change the melt debit. The supply
        # map keeps that shown debit, phase-scaled, and every computed rate
        # the debit floor dropped (including below_threshold_evaporation_flux).
        # publish_wall_supply_on_flux is the only writer. Not a dataclass
        # field: melt, ledger, and snapshot totals keep reading species_kg_hr.
        phase_scale = 1.0 if phase_scalar is None else float(phase_scalar)
        publish_wall_supply_on_flux(
            committed_flux,
            getattr(self, "_wall_supply_computed_kg_hr", None),
            debit_kg_hr=evap_flux.species_kg_hr,
            phase_scale=phase_scale,
        )
        self._ledger_committed_evap_flux_this_tick = committed_flux

        residual_flux = EvaporationFlux(
            species_kg_hr=residual_species_kg_hr,
            alpha_authority_status_by_species={
                species: status
                for species, status in (
                    evap_flux.alpha_authority_status_by_species.items()
                )
                if float(residual_species_kg_hr.get(species, 0.0)) > (
                    _vapour_debit_floor_kg_hr()
                )
            },
            carrier_authority_by_species=(
                evap_flux.carrier_authority_by_species
            ),
        )
        residual_flux.update_totals()
        return residual_flux

    def _route_evaporated_species_to_condensation(
        self,
        route_result,
        species: str,
        rate_kg_hr: float,
        *,
        apply_evaporative_redox_source_terms: bool = True,
    ) -> dict[str, Any]:
        authority = dict(
            getattr(route_result, 'condensation_authority_by_species', {}).get(
                species,
                {},
            )
            or {}
        )
        retained_source_kg = float(
            getattr(route_result, 'retained_in_source_by_species', {}).get(
                species,
                0.0,
            )
            or 0.0
        )
        if (
            str(authority.get('status', '')) in {'refused', 'proven_zero'}
            or retained_source_kg > 1e-15
        ):
            # The rail did not authorize an evaporation debit. Keep the
            # candidate mass in its source account; returning no transition
            # also keeps it out of terminal offgas after the end-of-hour bleed.
            return {
                'credited_condensed_kg': 0.0,
                'remaining_kg': 0.0,
                'retained_in_source_kg': retained_source_kg,
                'evaporation_transition': None,
                'authority_status': authority.get('status'),
            }

        metals_data = self.vapor_pressures.get('metals', {})
        oxide_vapors_data = self.vapor_pressures.get('oxide_vapors', {})
        sp_data = metals_data.get(species, {})
        if not sp_data:
            sp_data = oxide_vapors_data.get(species, {})

        stoich = self._evaporation_stoich(species, sp_data)
        if stoich is None:
            return {}
        available_kg = self.atom_ledger.kg_by_account(
            'process.cleaned_melt').get(stoich['parent_oxide'], 0.0)
        if available_kg <= 1e-12:
            return {}
        remaining_kg_hr = route_result.remaining_by_species.get(
            species, 0.0)
        if (
            remaining_kg_hr < -1e-12
            or remaining_kg_hr > rate_kg_hr + 1e-12
        ):
            raise AccountingError(
                f"condensation route for {species!r} returned "
                "unphysical remaining vapor mass"
            )

        _credited_from_evaporation, evaporation_transition = (
            self._credit_evaporation_transition(
                species,
                rate_kg_hr,
                rate_kg_hr,
                sp_data,
                apply_evaporative_redox_source_terms=(
                    apply_evaporative_redox_source_terms
                ),
                return_transition=True,
            )
        )
        if evaporation_transition is None:
            return {}

        condensed_kg = max(
            0.0, rate_kg_hr - remaining_kg_hr,
        )
        credited_condensed_kg = self._dispatch_condensation_route(
            species, condensed_kg, sp_data, route_result,
        )
        self._project_routed_condensate(
            route_result, species, credited_condensed_kg, sp_data,
        )

        return {
            'credited_condensed_kg': float(credited_condensed_kg),
            'remaining_kg': float(max(0.0, remaining_kg_hr)),
            'evaporation_transition': evaporation_transition,
        }

    def _route_native_fe_vapor_to_condensation(
        self,
        native_fe_vapor_mol: float,
        *,
        sample_time_h: float | None = None,
        source_account: str = 'process.cleaned_melt',
    ) -> dict[str, float]:
        native_fe_vapor_mol = max(0.0, float(native_fe_vapor_mol or 0.0))
        if native_fe_vapor_mol <= 1.0e-12:
            return {}
        fe_formula = resolve_species_formula('Fe', self.species_formula_registry)
        fe_molar_mass = fe_formula.molar_mass_kg_per_mol()
        rate_kg_hr = native_fe_vapor_mol * fe_molar_mass
        evap_flux = EvaporationFlux(species_kg_hr={'Fe': rate_kg_hr})
        evap_flux.update_totals()
        self._configure_condensation_operating_conditions(evap_flux)
        if sample_time_h is not None:
            self._apply_lab_surface_temperatures(sample_time_h=sample_time_h)
        route_result = self.condensation_model.route(evap_flux, self.melt)
        if source_account == 'process.metal_phase':
            remaining_kg = float(
                route_result.remaining_by_species.get('Fe', 0.0) or 0.0
            )
            if remaining_kg < -1e-12 or remaining_kg > rate_kg_hr + 1e-12:
                raise AccountingError(
                    "native Fe metal partition returned unphysical remaining vapor mass"
                )
            condensed_kg = max(0.0, rate_kg_hr - remaining_kg)
            fe_row = dict(
                (self.vapor_pressures.get('metals', {}) or {}).get('Fe') or {}
            )
            overhead_fe_before_kg = float(
                self.atom_ledger.kg_by_account('process.overhead_gas').get(
                    'Fe', 0.0,
                )
                or 0.0
            )
            credited_condensed_kg = self._dispatch_condensation_route(
                'Fe', condensed_kg, fe_row, route_result,
            )
            overhead_fe_after_kg = float(
                self.atom_ledger.kg_by_account('process.overhead_gas').get(
                    'Fe', 0.0,
                )
                or 0.0
            )
            committed_condensed_kg = max(
                0.0, overhead_fe_before_kg - overhead_fe_after_kg,
            )
            uncredited_condensed_kg = max(
                0.0, condensed_kg - committed_condensed_kg,
            )
            ledger_remaining_kg = remaining_kg + uncredited_condensed_kg
            if condensed_kg <= 1e-12:
                route_status = 'committed_uncondensed'
            elif committed_condensed_kg <= 1e-12:
                route_status = 'condensation_no_commit'
            elif committed_condensed_kg + 1e-12 < condensed_kg:
                route_status = 'partial_condensation'
            else:
                route_status = 'committed'
            self._project_routed_condensate(
                route_result, 'Fe', credited_condensed_kg, fe_row,
            )
            self._sync_oxygen_kg_counters()
            return {
                'native_fe_vapor_route_status': route_status,
                'native_fe_vapor_mol': native_fe_vapor_mol,
                'native_fe_vapor_kg': rate_kg_hr,
                'native_fe_overhead_o2_mol': 0.0,
                'native_fe_vapor_feo_debit_mol': 0.0,
                'native_fe_overhead_fe_mol_before_condensation': native_fe_vapor_mol,
                'native_fe_condensed_kg': float(credited_condensed_kg),
                'native_fe_uncondensed_kg': ledger_remaining_kg,
                'native_fe_uncondensed_mol': float(
                    ledger_remaining_kg / fe_molar_mass
                ),
            }
        if source_account != 'process.cleaned_melt':
            raise ValueError(f'unsupported native Fe vapor source account {source_account!r}')
        route_diag = self._route_evaporated_species_to_condensation(
            route_result,
            'Fe',
            rate_kg_hr,
            apply_evaporative_redox_source_terms=False,
        )
        self._sync_oxygen_kg_counters()
        transition = route_diag.get('evaporation_transition')
        routed_fe_mol = 0.0
        overhead_o2_mol = 0.0
        overhead_fe_mol = 0.0
        feo_debit_mol = 0.0
        route_status = 'no_transition'
        if transition is not None:
            route_status = 'committed'
            feo_debit_mol = self._transition_species_mol(
                transition,
                side='debits',
                account='process.cleaned_melt',
                species='FeO',
            )
            overhead_o2_mol = self._transition_species_mol(
                transition,
                side='credits',
                account='process.overhead_gas',
                species='O2',
            )
            overhead_fe_mol = self._transition_species_mol(
                transition,
                side='credits',
                account='process.overhead_gas',
                species='Fe',
            )
            routed_fe_mol = overhead_fe_mol
        remaining_kg = float(route_diag.get('remaining_kg', 0.0) or 0.0)
        return {
            'native_fe_vapor_route_status': route_status,
            'native_fe_vapor_mol': float(routed_fe_mol),
            'native_fe_vapor_kg': float(routed_fe_mol * fe_molar_mass),
            'native_fe_overhead_o2_mol': float(overhead_o2_mol),
            'native_fe_vapor_feo_debit_mol': float(feo_debit_mol),
            'native_fe_overhead_fe_mol_before_condensation': float(overhead_fe_mol),
            'native_fe_condensed_kg': float(
                route_diag.get('credited_condensed_kg', 0.0) or 0.0
            ),
            'native_fe_uncondensed_kg': remaining_kg,
            'native_fe_uncondensed_mol': float(remaining_kg / fe_molar_mass),
        }

    def _dispatch_condensation_route(
        self,
        species: str,
        condensed_kg: float,
        sp_data: dict,
        route_result,
    ) -> float:
        """Dispatch CONDENSATION_ROUTE through the kernel + commit.

        F-B1 (Cluster B): the dispatch + commit interleave collapsed
        into :meth:`_dispatch_and_commit`.  The overhead ->
        condensation_train ledger write still happens ONLY through
        ``commit_batch`` (the helper's only writable path); a no-op
        dispatch (kernel returned ``transition is None``) increments
        the F-A4 ``_chem_no_op_dispatch_count`` counter so a replay
        tool can distinguish "kernel skipped" from "called and no-op".

        Returns ``credited_condensed_kg`` -- the amount of vapor
        actually deposited onto ``process.condensation_train``, used by
        the caller to drive ``_project_condensed_stage_collection``.
        """
        self._last_condensation_baffle_product_kg_by_account = {}
        wall_deposit_fraction = float(
            route_result.wall_deposit_fraction_by_species.get(species, 0.0)
        )
        wall_deposit_account_fractions = dict(
            route_result
            .wall_deposit_account_fractions_by_species
            .get(species, {})
        )
        if wall_deposit_fraction <= 1.0e-12 or not wall_deposit_account_fractions:
            wall_deposit_fraction = 0.0
            wall_deposit_account_fractions = {}

        kernel_result = self._dispatch_and_commit(
            ChemistryIntent.CONDENSATION_ROUTE,
            transition_meta={
                'condensation_authority': dict(
                    getattr(route_result, 'condensation_authority_by_species', {})
                    .get(species, {})
                ),
                **({'wall_reaction_booking': copy.deepcopy(
                    route_result.wall_reaction_booking_by_species[species])}
                   if species in route_result.wall_reaction_booking_by_species else {}),
                **({'stage3_reactive_alkali': dict(
                    route_result.stage3_reactive_alkali_by_species[species])}
                   if species in route_result.stage3_reactive_alkali_by_species else {}),
            },
            control_inputs={
                'species': species,
                'condensed_kg': float(condensed_kg),
                'sp_data': dict(sp_data or {}),
                **({'stage3_reactive_alkali': dict(
                    route_result.stage3_reactive_alkali_by_species[species])}
                   if species in route_result.stage3_reactive_alkali_by_species else {}),
                **({'wall_reaction_booking': copy.deepcopy(
                    route_result.wall_reaction_booking_by_species[species])}
                   if species in route_result.wall_reaction_booking_by_species else {}),
                'wall_deposit_fraction': wall_deposit_fraction,
                'wall_deposit_account_fractions': wall_deposit_account_fractions,
                'wall_temperature_K': (
                    float(self.condensation_model.wall_temperature_C) + CELSIUS_TO_KELVIN_OFFSET
                    if self.condensation_model.wall_temperature_C is not None else None
                ),
                'wall_deposit_account_temperatures_K': {
                    segment.wall_deposit_account: (
                        float(segment.wall_temperature_C) + CELSIUS_TO_KELVIN_OFFSET
                    )
                    for segment in self.condensation_model.active_pipe_segments
                    if segment.name not in self.condensation_model.wall_temperature_input_refusals
                },
                'wall_alkali_binding_diagnostic_state_by_account': dict(
                    getattr(
                        self.condensation_model,
                        'wall_alkali_binding_diagnostic_state_by_account',
                        {},
                    )
                ),
                'dt_hr': 1.0,
            },
        )
        if kernel_result.transition is None:
            return 0.0

        diagnostic = dict(kernel_result.diagnostic or {})
        reaction = dict(diagnostic.get('stage3_reactive_alkali') or {})
        if reaction:
            route_result.stage3_reactive_alkali_by_species[species] = reaction
        self._record_wall_deposit_delta(species, diagnostic)
        self._record_wall_alkali_binding_diagnostic_state(diagnostic)
        self._last_condensation_baffle_product_kg_by_account = (
            self._stage_baffle_product_kg_from_credits(kernel_result.transition)
        )
        return float(diagnostic.get('credited_condensed_kg', 0.0))

    def _apply_organic_tar_wall_deposit(self) -> None:
        """Move cold-wall tar from overhead_gas onto process.wall_deposit.

        Uses the existing CONDENSATION_ROUTE wall split. Hot walls
        (default 1500 C) leave tar in overhead_gas.
        """
        from simulator.chemistry.organics_pyrolysis import tar_fate

        tar_kg = float(
            self.atom_ledger.kg_by_account('process.overhead_gas').get(
                'organic_tar', 0.0,
            )
            or 0.0
        )
        if tar_kg <= 1e-12:
            return
        if self.condensation_model.wall_temperature_C is None:
            from simulator.condensation import DepositionInputRefusal

            raise DepositionInputRefusal(
                "T_wall_K", None,
                str(self.condensation_model.wall_temperature_input_refusals),
            )
        wall_T = float(self.condensation_model.wall_temperature_C)
        melt_T = float(getattr(self.melt, 'temperature_C', wall_T) or wall_T)
        formula = resolve_species_formula(
            'organic_tar', self.species_formula_registry
        )
        tar_mol = tar_kg / formula.molar_mass_kg_per_mol()
        atoms = formula.atom_moles(tar_mol)
        fate = tar_fate(atoms, melt_T, wall_T)
        if fate.coat_frac <= 1e-12:
            return

        class _TarRoute:
            wall_deposit_fraction_by_species = {'organic_tar': 1.0}
            wall_deposit_account_fractions_by_species = {
                'organic_tar': {'process.wall_deposit': 1.0}
            }

        coat_kg = tar_kg * fate.coat_frac
        self._dispatch_condensation_route(
            'organic_tar',
            coat_kg,
            {},
            _TarRoute(),
        )

    def _record_wall_deposit_delta(
        self,
        species: str,
        diagnostic: Mapping[str, Any],
    ) -> None:
        # Delta provenance is the committed, post-validation kernel credit.
        # CondensationRouteResult is only the pre-commit projection; Phase-O
        # coating gates need the deposition that actually landed in the ledger.
        from simulator.vapour_rail.instrumentation import (
            merge_vapour_carrier_lineage,
        )

        accounts_by_species = (
            diagnostic.get('wall_deposit_accounts_kg_delta_by_species') or {}
        )
        if isinstance(accounts_by_species, Mapping):
            for account, species_kg in accounts_by_species.items():
                if not isinstance(species_kg, Mapping):
                    continue
                account_name = str(account)
                if account_name.startswith(PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNT_PREFIX):
                    segment = account_name[len(PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNT_PREFIX):]
                else:
                    segment = account_name
                for product_species, kg in species_kg.items():
                    amount = float(kg)
                    if abs(amount) <= 1e-12:
                        continue
                    notice = (
                        self.condensation_model
                        .last_sticking_alpha_provenance_notice
                    )
                    lineage = notice.setdefault(
                        'vapour_carrier_lineage_by_deposited_species',
                        {},
                    )
                    if isinstance(lineage, dict):
                        product_key = str(product_species)
                        lineage[product_key] = merge_vapour_carrier_lineage(
                            lineage.get(product_key),
                            species,
                        )
                    key = (segment, str(product_species))
                    deltas = self._last_wall_deposit_by_segment_species_delta
                    deltas[key] = deltas.get(key, 0.0) + amount
            return

        accounts_kg = diagnostic.get('credited_wall_deposit_accounts_kg') or {}
        if not isinstance(accounts_kg, Mapping):
            return
        for account, kg in accounts_kg.items():
            amount = float(kg)
            account_name = str(account)
            if account_name.startswith(PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNT_PREFIX):
                segment = account_name[len(PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNT_PREFIX):]
            else:
                segment = account_name
            key = (segment, species)
            deltas = self._last_wall_deposit_by_segment_species_delta
            if abs(amount) <= 1e-12:
                deltas.setdefault(key, 0.0)
            else:
                # The legacy payload has the same signed net-wall-inventory
                # contract as the per-species kernel diagnostic above.
                deltas[key] = deltas.get(key, 0.0) + amount

    def _record_wall_alkali_binding_diagnostic_state(
        self,
        diagnostic: Mapping[str, Any],
    ) -> None:
        updated = diagnostic.get(
            'wall_alkali_binding_diagnostic_state_by_account'
        )
        if not isinstance(updated, Mapping):
            return
        self.condensation_model.wall_alkali_binding_diagnostic_state_by_account = {
            str(account): dict(state)
            for account, state in updated.items()
            if isinstance(state, Mapping)
        }

    def _mark_overhead_na_origin_pool(self) -> None:
        """Declare headspace sodium origins well-mixed for partial withdrawals."""
        self.atom_ledger.mark_amalgamated_pool('process.overhead_gas', ('Na',))

    def _credit_evaporation_transition(
        self,
        species: str,
        rate_kg_hr: float,
        remaining_kg_hr: float,
        sp_data: dict,
        *,
        apply_evaporative_redox_source_terms: bool = True,
        return_transition: bool = False,
    ):
        """Apply the per-species melt -> vapor transition via the kernel.

        EVAPORATION_TRANSITION intent -- kernel-authoritative.

        \\goal BUILTIN-ENGINE-EXTRACTION (#7), third flip and the FIRST
        authoritative intent in the migration. The
        BuiltinEvaporationTransitionProvider builds a
        :class:`LedgerTransitionProposal` (debit cleaned_melt, credit
        overhead_gas + condensation_train); the kernel's commit_batch
        applies it to the AtomLedger after re-validating atom balance
        and the account-filter. This method:

        1. Validates the input stoich and condensation-route output the
           same way the legacy code did (the AccountingError surface
           lives in the caller, not inside the stateless provider --
           matching the EVAPORATION_FLUX pattern).
        2. Receives an analytically smoothed rate whose parent-oxide and
           shared-O2 availability caps have already been applied.
        3. Dispatches EVAPORATION_TRANSITION through the kernel and
           commits the resulting proposal. After this flip,
           ``self.atom_ledger.apply(...)`` no longer fires from inside
           this method -- ``self._chem_kernel.commit_batch(...)`` is the
           only writable path.

        Returns:
            ``credited_condensed_kg`` -- the amount of vapor that
            condensed onto ``process.condensation_train``, used by the
            caller to drive stage-collection bookkeeping.
        """

        stoich = self._evaporation_stoich(species, sp_data)
        if stoich is None:
            return (0.0, None) if return_transition else 0.0

        parent_oxide = stoich['parent_oxide']
        rate_kg_per_tick = rate_kg_hr * 1.0  # 1-hour tick (see core.py)
        oxide_removed = rate_kg_per_tick * stoich['oxide_per_product_kg']
        product_kg = rate_kg_per_tick

        if oxide_removed <= 1e-12:
            return (0.0, None) if return_transition else 0.0

        available_kg = self.atom_ledger.kg_by_account(
            'process.cleaned_melt').get(parent_oxide, 0.0)
        if available_kg <= 1e-12:
            return (0.0, None) if return_transition else 0.0

        # Mirror the legacy validation: the AccountingError surface is
        # owned by the caller, not the provider. The provider receives
        # already-validated inputs (this matches the EVAPORATION_FLUX
        # flip pattern in _calculate_evaporation).
        if remaining_kg_hr < -1e-12 or remaining_kg_hr > rate_kg_hr + 1e-12:
            raise AccountingError(
                f"condensation route for {species!r} returned "
                "unphysical remaining vapor mass"
            )
        remaining_kg = max(0.0, remaining_kg_hr) * 1.0
        if remaining_kg > product_kg + 1e-12:
            raise AccountingError(
                f"condensation route for {species!r} exceeds credited vapor"
            )

        # F-B1: split dispatch / commit so post-commit redox scalar
        # bookkeeping can derive strictly from the applied LedgerTransition.
        kernel_result = self._dispatch_only(
            ChemistryIntent.EVAPORATION_TRANSITION,
            control_inputs={
                'species': species,
                'stoich': dict(stoich),
                'sp_data': dict(sp_data or {}),
                'rate_kg_hr': float(rate_kg_hr),
                'remaining_kg_hr': float(remaining_kg_hr),
                'dt_hr': 1.0,
                'available_kg': float(available_kg),
            },
        )
        proposal = kernel_result.transition
        if proposal is None:
            self._chem_no_op_dispatch_count += 1
            return (0.0, None) if return_transition else 0.0

        diagnostic = dict(kernel_result.diagnostic or {})
        control_inputs = {
            'species': species,
            'stoich': dict(stoich),
            'sp_data': dict(sp_data or {}),
            'rate_kg_hr': float(rate_kg_hr),
            'remaining_kg_hr': float(remaining_kg_hr),
            'dt_hr': 1.0,
            'available_kg': float(available_kg),
        }
        transition = self._commit_proposal(
            ChemistryIntent.EVAPORATION_TRANSITION,
            proposal,
            diagnostic=diagnostic,
            control_inputs=control_inputs,
        )
        # Branch: every evaporation credit declares the Na headspace
        # well-mixed; partial gas withdrawals attribute pro rata.
        self._mark_overhead_na_origin_pool()
        # Green: K and Mg are the same source-mixed overhead pool,
        # declared when that cation is the credited species.
        if species in ('K', 'Mg'):
            self.atom_ledger.mark_amalgamated_pool(
                'process.overhead_gas', (species,)
            )
        if apply_evaporative_redox_source_terms:
            self._apply_evaporative_redox_source_terms(
                transition,
                exchange_direction='redox_source:evaporative_loss',
            )
        credited_condensed_kg = float(diagnostic.get('credited_condensed_kg', 0.0))
        if return_transition:
            return credited_condensed_kg, transition
        return credited_condensed_kg

    def _condensed_products_for_vapor(
        self, species: str, condensed_kg: float, sp_data: dict
    ):
        products_mol_per_mol = self._condensation_product_mol_ratios(
            species, sp_data)
        if products_mol_per_mol is None:
            return None, {species: condensed_kg} if condensed_kg > 0.0 else {}

        vapor_formula = resolve_species_formula(
            species, self.species_formula_registry)
        vapor_mol = condensed_kg / vapor_formula.molar_mass_kg_per_mol()
        product_mol = {
            product: ratio * vapor_mol
            for product, ratio in products_mol_per_mol.items()
            if ratio * vapor_mol > 0.0
        }
        return product_mol, self._species_mol_to_kg(product_mol)

    def _condensed_products_kg(
        self, species: str, condensed_kg: float, sp_data: dict
    ) -> dict:
        _product_mol, product_kg = self._condensed_products_for_vapor(
            species, condensed_kg, sp_data)
        product_accounts = dict(
            (sp_data or {}).get('condensation_product_accounts') or {}
        )
        if product_accounts:
            product_kg = {
                product: kg
                for product, kg in product_kg.items()
                if product_accounts.get(product) != 'process.overhead_gas'
            }
        return product_kg

    def _condensation_product_mol_ratios(
        self, species: str, sp_data: dict
    ):
        ratios = sp_data.get('condensation_products_mol_per_mol_vapor')
        if ratios is None:
            declared = str(sp_data.get('condensation_product', '')).lower()
            if 'disproportion' in declared:
                raise AccountingError(
                    f"vapor species {species!r} declares condensation "
                    "disproportionation but lacks "
                    "condensation_products_mol_per_mol_vapor metadata"
                )
            return None
        if not isinstance(ratios, dict) or not ratios:
            raise AccountingError(
                f"vapor species {species!r} condensation products must be "
                "a non-empty mapping"
            )

        clean = {}
        for product, raw_ratio in ratios.items():
            ratio = float(raw_ratio)
            if ratio <= 0.0 or not math.isfinite(ratio):
                raise AccountingError(
                    f"vapor species {species!r} condensation product "
                    f"{product!r} requires a positive mol ratio"
                )
            clean[str(product)] = ratio
        self._validate_condensation_products_atoms(species, clean)
        return clean

    def _validate_condensation_products_atoms(
        self, vapor_species: str, products_mol_per_mol: dict
    ) -> None:
        debit_atoms = resolve_species_formula(
            vapor_species, self.species_formula_registry).atom_moles(1.0)
        credit_atoms = defaultdict(float)
        for product, mol in products_mol_per_mol.items():
            formula = resolve_species_formula(
                product, self.species_formula_registry)
            for element, moles in formula.atom_moles(mol).items():
                credit_atoms[element] += moles

        for element in set(debit_atoms) | set(credit_atoms):
            debit = debit_atoms.get(element, 0.0)
            credit = credit_atoms.get(element, 0.0)
            if not math.isclose(debit, credit, rel_tol=1e-9, abs_tol=1e-12):
                raise AccountingError(
                    f"vapor species {vapor_species!r} condensation products "
                    f"do not conserve {element} atoms"
                )

    def _species_mol_to_kg(self, species_mol: dict) -> dict:
        converted = {}
        for species, mol in species_mol.items():
            formula = resolve_species_formula(
                species, self.species_formula_registry)
            kg = float(mol) * formula.molar_mass_kg_per_mol()
            if kg > 0.0:
                converted[species] = kg
        return converted

    def _evaporation_stoich(self, species: str, sp_data: dict):
        return _evaporation_stoich(species, sp_data, self.species_formula_registry)

    def _atom_moles_for_kg(self, species: str, kg: float) -> dict:
        return _atom_moles_for_kg(species, kg, self.species_formula_registry)

    def _stage_baffle_product_kg_from_credits(self, proposal) -> dict:
        """Kg of baffle solids on the train and the chromia terminal.

        The proposal stores mol. Wall accounts, overhead O2 and retained
        holdup are not stage condensate, so they are omitted.
        """
        credits = getattr(proposal, "credits", {}) or {}
        out: dict[str, dict[str, float]] = {}
        for account, species_mol in credits.items():
            if account not in _STAGE_BAFFLE_PRODUCT_ACCOUNTS:
                continue
            product_kg: dict[str, float] = {}
            for product, mol in species_mol.items():
                if product == "O2":
                    continue
                mol_f = float(mol)
                if not math.isfinite(mol_f) or mol_f <= 0.0:
                    continue
                formula = resolve_species_formula(
                    product, self.species_formula_registry,
                )
                kg = mol_f * formula.molar_mass_kg_per_mol()
                if kg <= 1e-12:
                    continue
                product_kg[str(product)] = kg
            if product_kg:
                out[str(account)] = product_kg
        return out

    def _project_routed_condensate(
        self,
        route_result,
        species: str,
        credited_condensed_kg: float,
        sp_data: dict,
    ) -> dict:
        """Project this parcel onto the stage UI from the committed solids.

        A wall-reaction booking has already credited its products. The
        K-trap swing withdraws those solids from the train. The static
        map stays the no-booking path: applying it after an oxidative
        booking asks the train for elemental Si it does not hold.
        """
        bookings = getattr(
            route_result, "wall_reaction_booking_by_species", {},
        ) or {}
        if species in bookings:
            by_account = dict(
                getattr(
                    self, "_last_condensation_baffle_product_kg_by_account", {},
                ) or {}
            )
            if not by_account:
                return {}
            train = "process.condensation_train"
            ordered = []
            if train in by_account:
                ordered.append((train, True))
            ordered.extend(
                (account, False)
                for account in by_account
                if account != train
            )
            merged: dict[str, float] = {}
            for account, swing in ordered:
                product_kg = by_account[account]
                for product, kg in product_kg.items():
                    merged[product] = merged.get(product, 0.0) + kg
                self._project_condensed_stage_collection(
                    route_result,
                    species,
                    credited_condensed_kg,
                    product_kg,
                    source_account=account,
                    swing_alkali_cartridge=swing,
                )
            return merged
        product_projection = self._condensed_products_kg(
            species, credited_condensed_kg, sp_data,
        )
        self._project_condensed_stage_collection(
            route_result, species, credited_condensed_kg, product_projection,
        )
        return product_projection

    def _project_condensed_stage_collection(
        self, route_result, species: str, credited_condensed_kg: float,
        product_kg_by_species: dict | None = None,
        *,
        source_account: str = "process.condensation_train",
        swing_alkali_cartridge: bool = True,
    ) -> None:
        if credited_condensed_kg <= 1e-12:
            return
        product_kg_by_species = product_kg_by_species or {
            species: credited_condensed_kg}
        intended_condensed_kg = route_result.condensed_for_species(species)
        if intended_condensed_kg <= 1e-12:
            return
        scale = credited_condensed_kg / intended_condensed_kg
        reaction = route_result.stage3_reactive_alkali_by_species.get(species, {})
        reactive_vapor_kg = float(reaction.get('committed_vapor_kg', 0.0))
        from simulator.condensation_routing import (
            K_TRAP_STAGE_NUMBER,
            K_TRAP_SUB_ORDER,
            STAGE_KEY_BY_NUMBER,
            species_accepted_on_stage,
            stage_index_for_visit_identity,
            stage_list_index_for_destination,
        )
        stage3_key = STAGE_KEY_BY_NUMBER[3]
        if reaction.get('uptake_mol', 0.0) > 0.0:
            ordinary_intended_kg = (
                intended_condensed_kg
                - route_result.condensed_by_stage_species.get(
                    stage3_key, {},
                ).get(species, 0.0)
            )
            scale = ((credited_condensed_kg - reactive_vapor_kg) / ordinary_intended_kg
                     if ordinary_intended_kg > 1e-12 else 0.0)
        product_scale = {
            product: kg / credited_condensed_kg
            for product, kg in product_kg_by_species.items()
            if kg > 1e-12
        }
        def book(stage_idx, stage_products, is_impurity):
            if stage_idx is None:
                return
            stage = self.train.stages[stage_idx]
            for product, stage_product_kg in stage_products.items():
                if stage_product_kg <= 1e-12:
                    continue
                delta_key = (int(stage.stage_number), product)
                deltas = self._last_condensed_by_stage_species_delta
                deltas[delta_key] = deltas.get(delta_key, 0.0) + stage_product_kg
                if is_impurity:
                    impurity = self._last_impurity_delta
                    impurity[delta_key] = (
                        impurity.get(delta_key, 0.0) + stage_product_kg)
                stage.collected_kg.update({
                    product: (
                        stage.collected_kg.get(product, 0.0)
                        + stage_product_kg)
                })
                self._record_stage_collection_source(
                    source_account,
                    stage_idx,
                    product,
                    stage_product_kg,
                )

        def scaled_products(projected_kg):
            return {
                product: projected_kg * fraction
                for product, fraction in product_scale.items()
            }

        identity_map = getattr(
            route_result, 'capture_kg_by_stage_identity', None,
        ) or {}
        cartridge_booked: dict[str, float] = {}
        cartridge_stage_idx: int | None = None
        if identity_map:
            # Visit identity splits two surfaces that share a stage number.
            # Stage 4 and the K trap both are number 4. Off-design mass on
            # one surface is that surface's impurity; the other surface's
            # designated mass stays product. A stage-number boolean cannot
            # say which.
            reactive_booked = False
            for identity, species_kg in identity_map.items():
                stage_number = int(identity[0])
                sub_order = int(identity[1])
                vapor_kg = float(species_kg.get(species, 0.0))
                reactive_here = (
                    stage_number == 3
                    and reaction.get('uptake_mol', 0.0) > 0.0
                    and vapor_kg > 1e-15
                    and not reactive_booked
                )
                if reactive_here:
                    reactive_booked = True
                    stage_products = dict(
                        reaction.get('committed_products_kg', {}),
                    )
                else:
                    stage_products = scaled_products(vapor_kg * scale)
                if not any(kg > 1e-12 for kg in stage_products.values()):
                    continue
                stage_idx = stage_index_for_visit_identity(
                    self.train, stage_number, sub_order,
                )
                if stage_idx is None:
                    continue
                stage = self.train.stages[stage_idx]
                book(
                    stage_idx,
                    stage_products,
                    not species_accepted_on_stage(stage, species),
                )
                if (
                    stage_number == K_TRAP_STAGE_NUMBER
                    and sub_order == K_TRAP_SUB_ORDER
                ):
                    cartridge_stage_idx = stage_idx
                    for product, product_kg in stage_products.items():
                        if product_kg > 1e-12:
                            cartridge_booked[product] = (
                                cartridge_booked.get(product, 0.0)
                                + product_kg
                            )
            if (
                swing_alkali_cartridge
                and cartridge_booked
                and cartridge_stage_idx is not None
            ):
                self._swing_alkali_freeze_out_cartridge(
                    cartridge_stage_idx,
                    cartridge_booked,
                )
            if (
                not reactive_booked
                and reaction.get('uptake_mol', 0.0) > 0.0
                and stage3_key in route_result.condensed_by_stage_species
            ):
                # A stage-3 bucket opened by another species still carries
                # this species' oxide sink when its own vapor kg was not
                # stored. Same condition as the stage-key path.
                stage_idx = stage_list_index_for_destination(
                    self.train, stage3_key,
                )
                is_impurity = (
                    route_result.impurity_by_stage_species
                    .get(stage3_key, {})
                    .get(species, 0.0)
                    > 0.0
                )
                book(
                    stage_idx,
                    dict(reaction.get('committed_products_kg', {})),
                    is_impurity,
                )
            return

        for stage_key, stage_species in (
            route_result.condensed_by_stage_species.items()
        ):
            projected_kg = stage_species.get(species, 0.0) * scale
            stage_products = (
                dict(reaction.get('committed_products_kg', {}))
                if (
                    stage_key == stage3_key
                    and reaction.get('uptake_mol', 0.0) > 0.0
                )
                else scaled_products(projected_kg)
            )
            if not any(kg > 1e-12 for kg in stage_products.values()):
                continue
            stage_idx = stage_list_index_for_destination(
                self.train, str(stage_key),
            )
            if stage_idx is None:
                continue
            is_impurity = (
                route_result.impurity_by_stage_species
                .get(stage_key, {})
                .get(species, 0.0)
                > 0.0
            )
            book(stage_idx, stage_products, is_impurity)

    def _swing_alkali_freeze_out_cartridge(
        self,
        stage_idx: int,
        product_kg: Mapping[str, float],
    ) -> None:
        """One cartridge in service this hour, regenerated off-line.

        The active cartridge accumulates this hour's condensate. Melt-out
        returns liquid Na and K to the train the C3 transfer reads. Mg,
        MgO, Ca, and every other product stay solid and leave the alkali
        product. No swing-timing optimisation: the cartridge account is
        empty again at the end of the hour.
        """

        from simulator.condensation import (
            alkali_cartridge_swing_moves,
            sodium_melting_temperature_K_from_row,
        )

        vapor = getattr(self, 'vapor_pressures', None)
        metals = vapor.get('metals', {}) if isinstance(vapor, Mapping) else {}
        sodium_row = metals.get('Na') if isinstance(metals, Mapping) else None
        stage = self.train.stages[int(stage_idx)]
        moves, notices = alkali_cartridge_swing_moves(
            product_kg,
            melt_out_temperature_C=getattr(
                stage, 'melt_out_temperature_C', None,
            ),
            sodium_melting_temperature_K=(
                sodium_melting_temperature_K_from_row(
                    sodium_row if isinstance(sodium_row, Mapping) else None,
                )
            ),
        )
        self.last_alkali_cartridge_swing_notices = notices
        for move in moves:
            # The train holds this stage's condensate together with every
            # other stage's deposit of the same element. The swing takes
            # only this hour's cartridge mass, a partial withdrawal.
            # ledger.move marks the credit after apply; the debit has to
            # be a declared pool first or mixed origins refuse.
            self.atom_ledger.mark_amalgamated_pool(
                move.debit_account,
                resolve_species_formula(
                    move.species,
                    self.atom_ledger.registry,
                ).elements,
            )
            self._move_ledger_species(
                move.name,
                move.debit_account,
                move.credit_account,
                move.species,
                move.kg,
                reason=move.reason,
                amalgamated_pool=move.species in {'Na', 'K', 'Mg'},
            )
            if move.retarget_source:
                self._retarget_stage_collection_source(
                    'process.condensation_train',
                    move.credit_account,
                    stage_idx,
                    move.species,
                    move.kg,
                )

    def _update_melt_composition(self, evap_flux: EvaporationFlux):
        """Project the cleaned-melt account onto MeltState kg fields."""
        self._project_cleaned_melt_from_atom_ledger()
