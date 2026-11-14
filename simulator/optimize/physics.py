"""Hard physics-feasibility gates over a completed PhysicsTrace."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping as MappingABC, Set as AbstractSet
from dataclasses import dataclass, field, replace
import hashlib
import math
from types import MappingProxyType
from typing import Any, Literal, Mapping, Sequence

from simulator.accounting import AccountingError
from simulator.accounting.completeness import (
    DEFAULT_RESIDUAL_SPECIES_BY_TARGET,
    TargetExtractionCompleteness,
    TargetSpeciesYield,
    TARGET_YIELD_DENOMINATOR_SOURCE,
    TARGET_YIELD_NUMERATOR_SOURCE,
    TARGET_YIELD_PROVENANCE_RULE,
    evaporation_quantity_unavailability,
    extraction_completeness_by_target,
    target_species_yield_by_initial_cleaned_melt,
)
from simulator.accounting.queries import AccountingQueries
from simulator.accounting.yield_disposition import (
    YIELD_DISPOSITION_MELT_BOUNDARY_PARTITION,
)
from simulator.condensation_routing import accepted_species_for_stage_number
from simulator.diagnostics import (
    _carrier_is_inapplicable,
    wall_deposit_sticking_authority_status,
)
from simulator.wall_deposit_availability import wall_deposit_figure_availability
from simulator.optimize.canonical import canonical_json_dumps, normalize_canonical_value
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
from simulator.evaporation import (
    EVALUATED_TRANSPORT_PO2_ATTR,
    EVALUATED_TRANSPORT_PO2_EVIDENCE,
    PRE_EVAPORATION_MELT_OXIDE_KG_ATTR,
    WALL_SUPPLY_INTERVAL_PHYSICAL_ZERO_ATTR,
    WALL_SUPPLY_INTERVAL_ZERO_EVIDENCE,
)
from simulator.silent_zero import CATEGORY_PROVEN_ZERO, ZeroBecause
from simulator.vapour_rail.instrumentation import (
    VAPOUR_CARRIER_AUTHORITY_PROVEN_ZERO,
    vapour_carrier_authority_status,
)
from engines.builtin.vapor_pressure import (
    VaporPressureNumericalOverflowError,
    VaporPressureRangeError,
)
from simulator.trace import WALL_DEPOSIT_ZONE_NAMES

SourceKind = Literal[
    "literature",
    "materials.yaml",
    "profile",
    "engineering_envelope",
    "code_default",
]

# D1: coating gate armed -- fail-closed on grounded
# campaigns-to-resinter<N when authoritative; bump invalidates pre-D1 cached
# feasibility verdicts.
# v3 (2026-07-03, milestone-3 L2-P2): the extraction_completeness gate now
# routes through the S2c provenance-aware trace surface (credit-line /
# additive exclusion, honest denominator) when present -- a semantic flip,
# so pre-S2c cached feasibility verdicts must not be served under the same
# physics_constraints_digest.
# v4 (2026-07-12, t-005): optimizer results now include the body-aware
# sub-ambient pumping hard gate, so pre-wiring feasibility/cache identities
# cannot be reused.
# v5: coating and Knudsen transport retain signed continuous margins without
# Boolean exclusion, so cached v4 feasibility verdicts cannot be reused.
# v6: predicted upstream coating, including flagged quantities, is a hard
# no-coating violation; refused wall quantities are unavailable.
# v7: d-045 bounds upstream wall deposition by feedstock charge mass and
# bounds refused trace species by their vapour flux instead of pricing them as
# zero.
# v8: purity is diagnostic-only by default; extraction completeness uses the
# left-the-melt basis and retains the captured product-ledger ratio as a
# provisional diagnostic.
# v9 (b-906): refused wall supply is classified on its run-total basis before
# the gate publishes the corresponding per-campaign quantity.
PHYSICS_GATE_VERSION = "physics-feasibility-v9-run-total-wall-supply"
DEFAULT_ACTIVE_GATES: tuple[str, ...] = (
    "delivered_stream_purity",
    "coating",
    "extraction_completeness",
    "knudsen_viscous",
    "furnace_temperature",
)
GATE_ORDER: tuple[str, ...] = (*DEFAULT_ACTIVE_GATES, "cycle_time")
E1B_TARGET_SPECIES: tuple[str, ...] = ("Na", "K", "Fe", "Mg", "SiO")
E1B_TARGET_YIELD_GATE_FRACTION = 0.95
TARGET_SPECIES_YIELD_CONSUMERS: tuple[str, ...] = (
    "tests/test_north_star_baseline.py::test_e1b_future_target_species_yield_threshold",
    "product_summary.target_species_yield_report",
)
_EPS = 1.0e-12
# Round-off guard only. The d-045 physical fraction below determines whether a
# non-zero upstream deposit violates the no-coating gate.
COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN = 1.0e-12
# d-045 (2026-09-26): one campaign violates no-coating when its predicted
# upstream wall deposit exceeds 0.05 % of that campaign's feedstock charge.
# deposit_kg_per_campaign <= 5e-4 * m_charge_kg.
# The fraction is dimensionless (kg deposit / kg charge). Sanity: a 1 kg
# charge allows 5e-4 kg = 0.5 g per campaign; a 1000 kg charge allows 0.5 kg
# per campaign. Designated-condenser capture is not part of this deposit.
MAX_UPSTREAM_WALL_DEPOSIT_FRACTION_PER_CAMPAIGN = 5.0e-4


@dataclass(frozen=True)
class ThresholdSpec:
    """Non-null threshold plus declared provenance."""

    id: str
    value: float
    units: str
    source: SourceKind
    source_ref: str
    tolerance: float = 0.0

    def __post_init__(self) -> None:
        _finite_number(self.value, f"{self.id}.value")
        if self.tolerance < 0.0 or not math.isfinite(self.tolerance):
            raise ValueError(f"{self.id}.tolerance must be finite and non-negative")
        if not self.source_ref:
            raise ValueError(f"{self.id}.source_ref must be declared")


def _d045_upstream_wall_deposit_threshold() -> ThresholdSpec:
    return ThresholdSpec(
        id="coating_max_upstream_wall_deposit_fraction_per_campaign",
        value=MAX_UPSTREAM_WALL_DEPOSIT_FRACTION_PER_CAMPAIGN,
        units="fraction",
        source="engineering_envelope",
        source_ref=(
            "d-045 (2026-09-26): upstream wall deposit / feedstock charge "
            "mass per campaign"
        ),
    )


@dataclass(frozen=True)
class GateMargin:
    gate: str
    feasible: bool
    margin: float
    threshold: ThresholdSpec
    observed: float | None
    detail: str
    status: str = "available"
    authoritative: bool = True
    output_status: str = "authoritative"
    status_reason: str = ""
    status_payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        status_payload = _plain_payload(self.status_payload)
        object.__setattr__(
            self,
            "status_payload",
            status_payload,
        )
        object.__setattr__(
            self,
            "feasible",
            _normalized_gate_margin_feasibility(
                self.gate,
                self.feasible,
                self.margin,
                self.threshold,
                status_payload,
            ),
        )


def _normalized_gate_margin_feasibility(
    gate: str,
    feasible: bool,
    margin: float,
    threshold: ThresholdSpec,
    status_payload: Mapping[str, Any],
) -> bool:
    if gate != "coating":
        return bool(feasible)
    verdict = status_payload.get("coating_verdict")
    if verdict in {"violated", "unavailable"}:
        return False
    if (
        margin == -math.inf
        or status_payload.get("coating_constraint_mode")
        == "no_unqualified_deposition"
        or threshold.id == "coating_max_unqualified_deposit_kg_per_campaign"
    ):
        return bool(feasible)
    return True


@dataclass(frozen=True)
class FeasibilityResult:
    feasible: bool
    margins: Mapping[str, GateMargin]
    version: str = PHYSICS_GATE_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "margins", MappingProxyType(dict(self.margins)))

    @property
    def failing_gates(self) -> tuple[str, ...]:
        return tuple(
            gate
            for gate in GATE_ORDER
            if gate in self.margins and not self.margins[gate].feasible
        )


class CoatingFeasibilityReportError(ValueError):
    """Runner wall-fouling report cannot support a coating verdict."""


_WALL_SUPPLY_WINDOW_REPORT_KEY = "_wall_supply_window"


@dataclass(frozen=True)
class _WallSupplyWindow:
    """Run-total supply and elapsed campaigns for one integration window."""

    run_total_kg: Mapping[str, float]
    campaigns_elapsed: float
    supply_interval_upper_bound_used: Mapping[str, int] = field(default_factory=dict)
    inventory_capped: Mapping[str, bool] = field(default_factory=dict)
    supply_unavailable: Mapping[str, Sequence[str]] = field(default_factory=dict)
    computed_exceeds_inventory: Mapping[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "run_total_kg",
            MappingProxyType(dict(self.run_total_kg)),
        )
        object.__setattr__(
            self,
            "supply_interval_upper_bound_used",
            MappingProxyType(dict(self.supply_interval_upper_bound_used)),
        )
        object.__setattr__(
            self,
            "inventory_capped",
            MappingProxyType(dict(self.inventory_capped)),
        )
        object.__setattr__(
            self,
            "supply_unavailable",
            MappingProxyType(
                {
                    key: tuple(value)
                    for key, value in self.supply_unavailable.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "computed_exceeds_inventory",
            MappingProxyType(dict(self.computed_exceeds_inventory)),
        )


@dataclass(frozen=True)
class _ClassifiedWallSupplyWindow:
    """Upper bound publication and authorized-zero booking for one window."""

    upper_bound_kg_per_campaign: Mapping[str, float]
    booked_run_total_kg: Mapping[str, float]
    booked_kg_per_campaign: Mapping[str, float]


def _classify_refused_wall_supply_window(
    window: _WallSupplyWindow,
    ledger_resolution_kg: float,
) -> _ClassifiedWallSupplyWindow:
    """Normalize one window and apply the ledger cutoff to its run totals.

    Integration produces kg/run. Each published kg/campaign value divides
    by campaigns_elapsed from that same window; the authorized-zero cutoff
    compares the undivided run total against the run-total ledger resolution.
    """

    try:
        elapsed = float(window.campaigns_elapsed)
        resolution = float(ledger_resolution_kg)
        totals = {
            str(species): float(value)
            for species, value in window.run_total_kg.items()
        }
    except (AttributeError, TypeError, ValueError) as exc:
        raise CoatingFeasibilityReportError(
            "wall supply window and ledger resolution must be numeric"
        ) from exc
    if not math.isfinite(elapsed) or elapsed <= 0.0:
        raise CoatingFeasibilityReportError(
            "campaigns_elapsed must be finite and positive for wall supply "
            "publication"
        )
    if not math.isfinite(resolution) or resolution < 0.0:
        raise CoatingFeasibilityReportError(
            "wall supply ledger resolution must be finite and non-negative"
        )
    if any(not math.isfinite(value) or value < 0.0 for value in totals.values()):
        raise CoatingFeasibilityReportError(
            "wall supply run totals must be finite and non-negative"
        )
    booked_run_total = {
        species: 0.0 if value <= resolution else value
        for species, value in totals.items()
    }
    upper_bound = {
        species: value / elapsed for species, value in totals.items()
    }
    booked_per_campaign = {
        species: value / elapsed
        for species, value in booked_run_total.items()
    }
    return _ClassifiedWallSupplyWindow(
        upper_bound_kg_per_campaign=MappingProxyType(upper_bound),
        booked_run_total_kg=MappingProxyType(booked_run_total),
        booked_kg_per_campaign=MappingProxyType(booked_per_campaign),
    )


def _snapshot_refused_flux_rates(snapshot: Any) -> Any:
    """Rate map for one snapshot, or None when evaporation flux is unavailable.

    The ledger-committed map omits a carrier that produced no evaporation
    transition. The wall supply is the published vapour rate condensation
    was shown (published_species_kg_hr). Fall back to species_kg_hr when
    that map was not retained. Absence from a present map is not a number.
    ``_classify_wall_supply_interval`` classifies it. This function does
    not turn it into zero. A missing map is a different gap: the interval
    has no flux evidence at all.
    """

    flux = getattr(snapshot, "evap_flux", None)
    published = getattr(flux, "published_species_kg_hr", None)
    if isinstance(published, MappingABC):
        return published
    return getattr(flux, "species_kg_hr", None)


def _carrier_mapping_kind(record: Mapping[str, Any], key: str) -> str:
    payload = record.get(key)
    if isinstance(payload, MappingABC):
        return str(payload.get("kind") or "")
    return ""


def _carrier_records_transport_refusal(record: Mapping[str, Any]) -> bool:
    """True for the local missing-transport channel ``_calculate_evaporation`` keeps.

    That path leaves the rest of the run published. The refused species
    has no rate. The refusal may sit on the channel or only on its extra.
    """

    extra = record.get("extra")
    if not isinstance(extra, MappingABC):
        extra = {}
    policy = record.get("policy") or extra.get("policy")
    status = record.get("status") or extra.get("status")
    refusal_type = (
        record.get("refusal_type")
        or extra.get("refusal_type")
        or record.get("refusal_code")
        or extra.get("refusal_code")
    )
    return (
        policy == "fail_loud_missing_transport_parameters"
        and status == "unavailable"
        and bool(refusal_type)
    )


# One interval of one refused-wall species. ``outside_window`` is a
# non-positive duration: it is not part of the integral.
_WALL_SUPPLY_COMPUTED = "computed"
_WALL_SUPPLY_PHYSICAL_ZERO = "physical_zero"
_WALL_SUPPLY_UNAVAILABLE = "unavailable"
_WALL_SUPPLY_OUTSIDE = "outside_window"
# One refused interval bounded by the activity-1 Hertz-Knudsen flux.
# The evidence string is the flag the booking copies, with the count.
_WALL_SUPPLY_UPPER_BOUND = "supply_interval_upper_bound"
SUPPLY_INTERVAL_UPPER_BOUND_USED = "supply_interval_upper_bound_used"
# The parent-oxide inventory, not the kinetic ceiling, limited this species.
INVENTORY_CAPPED = "inventory_capped"
# Computed vapour alone exceeded the window-start inventory. The
# published supply is that inventory. Subthreshold rates stay on the
# wall-supply map and never enter the melt debit
# (EvaporationMixin._overlay_depleted_wall_supply); this flag names
# that leak and does not repair the debit.
COMPUTED_EXCEEDS_INVENTORY = "computed_exceeds_inventory"
# A refused bound whose parent oxide was never recorded. Not a kinetic mass
# and not a zero. The species stays out of the supply map.
MISSING_PARENT_OXIDE_INVENTORY = "missing_parent_oxide_inventory"
# A conservation operand is missing: the parent, the kilogram ratio, or
# both, after the evaporation-stoich owner has been asked. The kinetic
# ceiling is not the supply. The species stays out of the supply map.
MISSING_PARENT_OXIDE_STOICHIOMETRY = "missing_parent_oxide_stoichiometry"
# Species omitted from the supply map, each with the typed reason.
SUPPLY_UNAVAILABLE = "supply_unavailable"


@dataclass(frozen=True)
class _WallSupplyInterval:
    """The contribution of one (species, interval) to the wall-supply integral."""

    kind: str
    rate_kg_hr: float | None = None
    evidence: str = ""


class _WallSupplyIntegral(dict):
    """Per-campaign view of an integral, retaining its typed window basis."""

    def __init__(self, window: _WallSupplyWindow) -> None:
        normalized = _classify_refused_wall_supply_window(
            window,
            ledger_resolution_kg=0.0,
        )
        super().__init__(normalized.upper_bound_kg_per_campaign)
        self.window = window
        self.supply_interval_upper_bound_used = {
            str(species): int(count)
            for species, count in window.supply_interval_upper_bound_used.items()
            if int(count) > 0 and str(species) in self
        }
        self.inventory_capped = {
            str(species): True
            for species, flagged in window.inventory_capped.items()
            if flagged and str(species) in self
        }
        self.computed_exceeds_inventory = {
            str(species): True
            for species, flagged in window.computed_exceeds_inventory.items()
            if flagged and str(species) in self
        }
        self.supply_unavailable = {
            str(species): list(reasons)
            for species, reasons in window.supply_unavailable.items()
            if reasons and str(species) not in self
        }


def _snapshot_duration_h(snapshot: Any) -> float:
    raw_duration = getattr(snapshot, "duration_h", 1.0)
    if isinstance(raw_duration, bool) or not isinstance(raw_duration, int | float):
        raise CoatingFeasibilityReportError(
            "snapshot duration_h must be numeric for refused flux bounds"
        )
    duration_h = float(raw_duration)
    if not math.isfinite(duration_h) or duration_h < 0.0:
        raise CoatingFeasibilityReportError(
            "snapshot duration_h must be finite and non-negative for "
            "refused flux bounds"
        )
    return duration_h


def _require_vapour_supply_rate(raw_rate: Any, species: str) -> float:
    if isinstance(raw_rate, bool) or not isinstance(raw_rate, int | float):
        raise CoatingFeasibilityReportError(
            f"vapour flux for refused species {species!r} must be numeric"
        )
    rate = float(raw_rate)
    if not math.isfinite(rate) or rate < 0.0:
        raise CoatingFeasibilityReportError(
            f"vapour flux for refused species {species!r} must be finite "
            "and non-negative"
        )
    return rate


def _carrier_record_blocks_physical_zero(record: Mapping[str, Any]) -> bool:
    """True when this record is a refusal or an upper bound, not a zero.

    A diagnostic upper bound is not a kg/h. A pressure upper bound is
    the same class. A refused runtime pressure, a transport-unavailable
    extra, or a pressure/flux refusal is missing input. None of those
    is a zero hour, and a whole-interval certificate does not make
    them one.
    """

    if _carrier_mapping_kind(record, "flux") in {
        "diagnostic_upper_bound",
        "refusal",
    }:
        return True
    if _carrier_mapping_kind(record, "pressure") in {"refusal", "upper_bound"}:
        return True
    if _carrier_mapping_kind(record, "selected_runtime_pressure") == "refusal":
        return True
    if bool(record.get("is_refused", False)):
        return True
    return _carrier_records_transport_refusal(record)


def _carrier_record_is_authorized_zero(record: Any, species: str) -> bool:
    """Typed authorized zero: ZeroByPhysics for this species, and not a refusal."""

    if not isinstance(record, MappingABC):
        return False
    if _carrier_record_blocks_physical_zero(record):
        return False
    return (
        vapour_carrier_authority_status(record, expected_species_id=species)
        == VAPOUR_CARRIER_AUTHORITY_PROVEN_ZERO
    )


def _carrier_record_is_below_bound_zero(record: Any, species: str) -> bool:
    """Typed below-bound zero for this species.

    ``zero_because=proven_below_threshold`` with doctrine category 3
    (proven zero). A refusal on the same record is not this evidence.
    Ledger resolution is not this record: that comparison happens at
    booking, after the integral.
    """

    if not isinstance(record, MappingABC):
        return False
    named = str(record.get("species_id") or record.get("species") or "").strip()
    if named != str(species):
        return False
    reason = str(record.get("zero_because") or "")
    if reason != ZeroBecause.PROVEN_BELOW_THRESHOLD.value:
        return False
    category = record.get("doctrine_category")
    if isinstance(category, bool) or not isinstance(category, int | float):
        return False
    if int(category) != CATEGORY_PROVEN_ZERO:
        return False
    return not _carrier_record_blocks_physical_zero(record)


def _interval_physical_zero_reason(flux: Any) -> str:
    """Whole-interval physical zero, or ``""``.

    Producers set this only when the hour evaporates no species: no
    liquid, a non-evaporation campaign, a frozen melt, the sub-400 K
    floor, or empty vapour inventory. The pre-RG empty map below
    1050 C is not this record. A zero-net carrier is a computed 0 on
    the rate map, not this record.
    """

    record = getattr(flux, WALL_SUPPLY_INTERVAL_PHYSICAL_ZERO_ATTR, None)
    if not isinstance(record, MappingABC):
        return ""
    if str(record.get("evidence") or "") != WALL_SUPPLY_INTERVAL_ZERO_EVIDENCE:
        return ""
    return str(record.get("reason") or "").strip()


def _physical_zero_evidence(flux: Any, species: str) -> str:
    """``authorized_zero`` or ``below_bound_zero``, or ``""`` when neither is on record.

    Called only when ``species`` is absent from a present rate map.
    An eligible or authoritative channel, and a missing carrier record,
    are not this evidence.
    """

    channels = getattr(flux, "carrier_authority_by_species", None)
    if not isinstance(channels, MappingABC) or species not in channels:
        return ""
    record = channels[species]
    if _carrier_record_is_authorized_zero(record, species):
        return "authorized_zero"
    if _carrier_record_is_below_bound_zero(record, species):
        return "below_bound_zero"
    return ""


def _interval_certificate_leaves_species_uncovered(
    flux: Any,
    species: str,
) -> bool:
    """True when a refusal or upper bound on ``species`` blocks the certificate.

    No channel, or an eligible channel whose rate the freeze or campaign
    gate discarded, returns False: the certificate covers that species.
    A refusal or an upper bound returns True. That interval is not a
    physical zero. The supply integral bounds it by the activity-1
    Hertz-Knudsen flux when saturation pressure exists, and withholds
    the species only when the interval has no flux law and no saturation
    pressure.
    """

    channels = getattr(flux, "carrier_authority_by_species", None)
    if not isinstance(channels, MappingABC) or species not in channels:
        return False
    record = channels[species]
    if not isinstance(record, MappingABC):
        return False
    return _carrier_record_blocks_physical_zero(record)


def _snapshot_temperature_K(snapshot: Any) -> float | None:
    raw = getattr(snapshot, "temperature_C", None)
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    kelvin = float(raw) + CELSIUS_TO_KELVIN_OFFSET
    if not math.isfinite(kelvin) or kelvin <= 0.0:
        return None
    return kelvin


def _snapshot_melt_area_m2(snapshot: Any) -> float | None:
    """Melt area this interval's flux was integrated over, in m^2.

    ``HourSnapshot`` does not store it. The hour writer copies
    ``melt.melt_surface_area_m2`` onto the flux. A positive finite
    number on the snapshot itself is the same input for a fixture.
    """

    flux = getattr(snapshot, "evap_flux", None)
    for raw in (
        getattr(flux, "melt_surface_area_m2", None),
        getattr(snapshot, "melt_surface_area_m2", None),
    ):
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            continue
        area = float(raw)
        if math.isfinite(area) and area > 0.0:
            return area
    return None


def _carrier_channel(snapshot: Any, species: str) -> Mapping[str, Any] | None:
    flux = getattr(snapshot, "evap_flux", None)
    channels = getattr(flux, "carrier_authority_by_species", None)
    if not isinstance(channels, MappingABC) or species not in channels:
        return None
    record = channels[species]
    if not isinstance(record, MappingABC):
        return None
    return record


def _cited_evaporation_alpha(record: Mapping[str, Any] | None) -> float:
    """``1`` unless this channel carries a cited evaporation coefficient.

    A cited alpha is a numeric ``cited_alpha`` on the carrier record in
    ``(0, 1]``. ``flux.alpha_ref`` is a label, not a coefficient. A
    catalog proxy or the analytical alpha=1 ceiling is not a citation,
    so the bound stays the kinetic ceiling.
    """

    if record is None or "cited_alpha" not in record:
        return 1.0
    raw = record.get("cited_alpha")
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise CoatingFeasibilityReportError(
            "cited evaporation alpha must be numeric"
        )
    alpha = float(raw)
    if not math.isfinite(alpha) or alpha <= 0.0 or alpha > 1.0:
        raise CoatingFeasibilityReportError(
            "cited evaporation alpha must be finite and in (0, 1]"
        )
    return alpha


def _explicit_activity_one_psat_pa(
    record: Mapping[str, Any] | None,
) -> float | None:
    """Carrier-stated activity-1 pressure, or None when the key is absent.

    ``None`` stored under the key is an explicit absence: do not replace
    it with a catalog lookup. ``pressure.pa`` is equilibrium pressure,
    not this value.
    """

    if record is None or "activity_one_psat_pa" not in record:
        return None
    raw = record.get("activity_one_psat_pa")
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise CoatingFeasibilityReportError(
            "activity-one saturation pressure must be numeric"
        )
    pressure_pa = float(raw)
    if not math.isfinite(pressure_pa) or pressure_pa < 0.0:
        raise CoatingFeasibilityReportError(
            "activity-one saturation pressure must be finite and non-negative"
        )
    return pressure_pa


def _execution_vapour_catalog(run_execution: Any) -> Any | None:
    """The run's compiled vapour catalog, or None when this run has none.

    The supply ceiling reads this object. It does not open
    ``DEFAULT_DATA_DIR / vapor_pressures.yaml``. A study that supplied
    a different saturation curve would otherwise be bounded by the
    shipped file, and a species that exists only on the run catalog
    would be reported as having no law.
    """

    simulator = getattr(run_execution, "simulator", None)
    catalog = getattr(simulator, "vapour_rail_catalog", None)
    if catalog is None or not hasattr(catalog, "evaluator_for_hot_train"):
        return None
    return catalog


def _bound_vapour_catalog(snapshot: Any, catalog: Any | None) -> Any | None:
    """Run catalog, else the catalog preserved on the snapshot."""

    if catalog is not None and hasattr(catalog, "evaluator_for_hot_train"):
        return catalog
    stamped = getattr(snapshot, "vapour_catalog", None)
    if stamped is not None and hasattr(stamped, "evaluator_for_hot_train"):
        return stamped
    return None


def _positive_pressure_bar(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    pressure = float(raw)
    if not math.isfinite(pressure) or pressure <= 0.0:
        return None
    return pressure


def _snapshot_transport_pO2_bar(snapshot: Any) -> float | None:
    """Transport pO2 this hour's vapour evaluation consumed, in bar.

    The interval carries that pressure on
    ``evap_flux.evaluated_transport_pO2`` when vapour evaluation ran.
    The snapshot oxygen reservoir is copied after the headspace bleed
    and is a later state (d-401). A recorded absence does not fall
    through to that reservoir, to melt fO2, or to ``p_ref``.

    A fixture interval with no evaluation record may still name the
    pressure on the reservoir or on ``transport_pO2_bar``,
    ``overhead_pO2_bar``, or ``interval_pO2_bar``. Those are the
    author's recorded pressure, not a post-bleed substitute.
    """

    flux = getattr(snapshot, "evap_flux", None)
    record = getattr(flux, EVALUATED_TRANSPORT_PO2_ATTR, None)
    if (
        isinstance(record, MappingABC)
        and str(record.get("evidence") or "")
        == EVALUATED_TRANSPORT_PO2_EVIDENCE
    ):
        return _positive_pressure_bar(record.get("pressure_bar"))
    reservoir = getattr(snapshot, "oxygen_reservoir", None)
    if isinstance(reservoir, MappingABC) and (
        "headspace_transport_pO2_bar" in reservoir
    ):
        recorded = _positive_pressure_bar(
            reservoir.get("headspace_transport_pO2_bar")
        )
        if recorded is not None:
            return recorded
    for name in ("transport_pO2_bar", "overhead_pO2_bar", "interval_pO2_bar"):
        recorded = _positive_pressure_bar(getattr(snapshot, name, None))
        if recorded is not None:
            return recorded
    return None


def _recorded_transport_pO2_bar(snapshots: Any) -> tuple[float, ...]:
    """Every positive transport pO2 the window actually stored."""

    recorded: list[float] = []
    for snapshot in tuple(snapshots or ()):
        pressure = _snapshot_transport_pO2_bar(snapshot)
        if pressure is not None:
            recorded.append(pressure)
    return tuple(recorded)


def _oxygen_bar_for_activity_one_ceiling(
    *,
    oxygen_exponent: float,
    reference_pO2_bar: float,
    hour_pO2_bar: float | None,
    recorded_pO2_bar: Sequence[float],
) -> tuple[float | None, str]:
    """pO2 at which ``a = 1`` is a ceiling, or ``(None, evidence)``.

    The catalog law is ``P = P_unit(T) * a^n * (pO2 / p_ref)^e``.
    Activity is at most 1 (d-369). This function chooses only pO2.
    d-401:

    (a) The hour's own transport/overhead pO2 is the oxygen potential
    its flux used. ``P(a=1, p_ref)`` is not a ceiling when ``e < 0``:
    a lower pO2 raises P, and ``p_ref`` of 1 bar understates it.

    (b) A hour with no recorded pO2 uses the run's recorded pO2 that
    maximises P. ``e < 0`` takes the minimum. ``e > 0`` (CrO2) takes
    the maximum. No 1e-30 clamp and no 1e-9 guard is substituted.

    (c) A run that recorded no pO2 has no ceiling for ``e != 0``.
    ``e == 0`` is unchanged: the oxygen factor is identically 1, so
    evaluating at ``p_ref`` is not a second oxygen state.
    """

    if oxygen_exponent == 0.0:
        if not math.isfinite(reference_pO2_bar) or reference_pO2_bar <= 0.0:
            return None, "missing_recorded_pO2"
        return reference_pO2_bar, ""
    if hour_pO2_bar is not None:
        return float(hour_pO2_bar), ""
    recorded = tuple(
        pressure
        for pressure in recorded_pO2_bar
        if math.isfinite(pressure) and pressure > 0.0
    )
    if not recorded:
        return None, "missing_recorded_pO2"
    if oxygen_exponent < 0.0:
        return min(recorded), ""
    return max(recorded), ""


def _catalog_activity_one_ceiling_pa(
    catalog: Any | None,
    species: str,
    temperature_K: float,
    hour_pO2_bar: float | None,
    recorded_pO2_bar: Sequence[float] = (),
    source_activity: float = 1.0,
) -> tuple[float | None, str]:
    """Ceiling pressure at ``source_activity``, or ``(None, evidence)``.

    ``P = P_unit(T) * a^n * (pO2 / p_ref)^e``. ``source_activity`` is 1
    unless the interval still has a computed activity (d-401 amendment
    2). For ``n >= 0`` that activity is at most 1, so it cannot raise
    the activity-1 ceiling. For ``n < 0`` the pressure grows as activity
    falls, so there is no finite activity ceiling. The oxygen state is
    ``_oxygen_bar_for_activity_one_ceiling`` (d-401). A missing row, an
    inapplicable hot-train row, or a refused evaluation is no law.
    """

    from simulator.vapour_rail.catalog import CatalogCompileError

    if catalog is None:
        return None, "no_flux_law_and_no_psat"
    try:
        evaluator = catalog.evaluator_for_hot_train(species)
    except (
        CatalogCompileError,
        VaporPressureNumericalOverflowError,
        VaporPressureRangeError,
        OverflowError,
        ValueError,
        TypeError,
        AttributeError,
    ):
        return None, "no_flux_law_and_no_psat"
    try:
        activity_exponent = float(getattr(evaluator, "activity_exponent", 0.0))
        oxygen_exponent = float(getattr(evaluator, "pO2_exponent", 0.0))
        reference_pO2_bar = float(getattr(evaluator, "pO2_reference_bar", 1.0))
    except (TypeError, ValueError):
        return None, "no_flux_law_and_no_psat"
    if not math.isfinite(activity_exponent) or not math.isfinite(oxygen_exponent):
        return None, "no_flux_law_and_no_psat"
    if activity_exponent < 0.0:
        return None, "no_finite_activity_ceiling"
    if (
        isinstance(source_activity, bool)
        or not isinstance(source_activity, int | float)
        or not math.isfinite(source_activity)
        or source_activity < 0.0
        or source_activity > 1.0
    ):
        raise CoatingFeasibilityReportError(
            "computed activity must be finite and in [0, 1]"
        )
    # n > 0 and a computed activity of 0 is a zero pressure. The
    # evaluator rejects a non-positive activity, so this does not call it.
    if activity_exponent > 0.0 and float(source_activity) == 0.0:
        return 0.0, ""
    if activity_exponent == 0.0:
        source_activity = 1.0
    oxygen_bar, oxygen_evidence = _oxygen_bar_for_activity_one_ceiling(
        oxygen_exponent=oxygen_exponent,
        reference_pO2_bar=reference_pO2_bar,
        hour_pO2_bar=hour_pO2_bar,
        recorded_pO2_bar=recorded_pO2_bar,
    )
    if oxygen_bar is None:
        return None, oxygen_evidence or "missing_recorded_pO2"
    try:
        evaluation = evaluator.evaluate(
            temperature_K,
            source_activity=float(source_activity),
            pO2_bar=oxygen_bar,
        )
    except (
        CatalogCompileError,
        VaporPressureNumericalOverflowError,
        VaporPressureRangeError,
        OverflowError,
        ValueError,
        TypeError,
    ):
        return None, "no_flux_law_and_no_psat"
    raw = getattr(evaluation, "pressure_pa", None)
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None, "no_flux_law_and_no_psat"
    pressure_pa = float(raw)
    if not math.isfinite(pressure_pa) or pressure_pa < 0.0:
        return None, "activity_one_ceiling_not_finite"
    return pressure_pa, ""


def _explicit_molar_mass_kg_per_mol(
    record: Mapping[str, Any] | None,
) -> float | None:
    if record is None or "molar_mass_kg_per_mol" not in record:
        return None
    raw = record.get("molar_mass_kg_per_mol")
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise CoatingFeasibilityReportError(
            "vapour molar mass must be numeric"
        )
    mass = float(raw)
    if not math.isfinite(mass) or mass <= 0.0:
        raise CoatingFeasibilityReportError(
            "vapour molar mass must be finite and positive"
        )
    return mass


def _catalog_molar_mass_kg_per_mol(
    catalog: Any | None,
    species: str,
) -> float | None:
    """Publisher molar mass on this run's catalog.

    A missing operand is not a guessed formula mass. The legacy view is
    the publisher's lookup, including its ``MOLAR_MASS`` fallback. There
    is no second catalog.
    """

    from simulator.evaporation import production_vapor_molar_mass_g_per_mol
    from simulator.vapour_rail.catalog import CatalogCompileError

    if catalog is None or not hasattr(catalog, "legacy_view"):
        return None
    try:
        legacy = catalog.legacy_view()
    except CatalogCompileError:
        return None
    grams = production_vapor_molar_mass_g_per_mol(legacy, species)
    if isinstance(grams, bool) or not isinstance(grams, int | float):
        return None
    mass_g = float(grams)
    if not math.isfinite(mass_g) or mass_g <= 0.0:
        return None
    return mass_g / 1000.0


def _activity_one_bound_rate_kg_hr(
    *,
    temperature_K: float,
    pressure_pa: float,
    molar_mass_kg_per_mol: float,
    area_m2: float,
    alpha: float,
) -> float:
    """Activity-1 Hertz-Knudsen mass rate for one refused interval.

    Premise. ``pressure_pa`` is the activity-1 ceiling from
    ``_catalog_activity_one_ceiling_pa`` or an explicit
    ``activity_one_psat_pa``. It is not the equilibrium pressure at the
    catalog reference pO2. ``alpha_ub`` is 1 unless a cited alpha is on
    the channel. Deposit still cannot exceed this supply; this function
    does not price the wall.

    Algebra. The kinetic coefficient is the publisher's
    ``hertz_knudsen_k_kg_s_m2_pa``,

        k_HK = sqrt(M / (2 * pi * R * T))

    and the interval rate matches the evaporation kernel's kg/h conversion,

        J_max_kg_hr = alpha_ub * P_sat * k_HK * area_m2 * 3600.

    Unit check. ``k_HK`` is kg/(m^2 Pa s) because Pa = kg/(m s^2).
    Times Pa * m^2 * s/h gives kg/h.

    Sanity value, not a lunar fit. At 1500 K, M = 0.068 kg/mol,
    P_sat = 1e-3 Pa, alpha = 1, area = 0.2 m^2, and R = 8.314462618
    J/(mol K): k_HK = 9.315e-4 kg/(m^2 Pa s) and J_max = 6.707e-4 kg/h.
    """

    from simulator.chemistry.langmuir_knudsen import hertz_knudsen_k_kg_s_m2_pa

    coefficient = hertz_knudsen_k_kg_s_m2_pa(
        temperature_K,
        molar_mass_kg_per_mol,
    )
    rate_kg_hr = (
        alpha * pressure_pa * coefficient * area_m2 * 3600.0
    )
    if not math.isfinite(rate_kg_hr) or rate_kg_hr < 0.0:
        raise CoatingFeasibilityReportError(
            "activity-one supply bound must be finite and non-negative"
        )
    return rate_kg_hr


def _activity_one_refused_supply_interval(
    snapshot: Any,
    species: str,
    catalog: Any | None = None,
    recorded_pO2_bar: Sequence[float] = (),
) -> _WallSupplyInterval:
    """Bound one interval, or type the missing input.

    Unavailable only when this interval has no flux law and no
    activity-1 ceiling, or when a required operand of that bound is
    absent. The reason is the evidence string. An absent rate map and
    an absent carrier record are not, by themselves, this outcome:
    both still reach the ceiling. An oxygen-dependent law with no
    recorded transport pO2 on this hour uses ``recorded_pO2_bar``
    (d-401). An empty record there is a typed gap, not ``p_ref``.
    """

    record = _carrier_channel(snapshot, species)
    temperature_K = _snapshot_temperature_K(snapshot)
    if temperature_K is None:
        return _WallSupplyInterval(
            _WALL_SUPPLY_UNAVAILABLE,
            evidence="missing_temperature",
        )
    area_m2 = _snapshot_melt_area_m2(snapshot)
    if area_m2 is None:
        return _WallSupplyInterval(
            _WALL_SUPPLY_UNAVAILABLE,
            evidence="missing_melt_surface_area",
        )
    # Activity 1 is the ceiling only when this interval has no computed
    # activity. A computed activity replaces it (d-401 amendment 2).
    activity = _computed_source_activity(record)
    if activity is None:
        activity = 1.0
    bound_catalog = _bound_vapour_catalog(snapshot, catalog)
    if record is not None and "activity_one_psat_pa" in record:
        pressure_pa = _explicit_activity_one_psat_pa(record)
        pressure_evidence = (
            "" if pressure_pa is not None else "no_flux_law_and_no_psat"
        )
        if pressure_pa is not None and activity < 1.0 and pressure_pa > 0.0:
            exponent = _ceiling_activity_exponent(
                record,
                bound_catalog,
                species,
            )
            if exponent is not None and exponent < 0.0:
                return _WallSupplyInterval(
                    _WALL_SUPPLY_UNAVAILABLE,
                    evidence="no_finite_activity_ceiling",
                )
            if exponent is not None and exponent > 0.0:
                pressure_pa = pressure_pa * (activity ** exponent)
    else:
        pressure_pa, pressure_evidence = _catalog_activity_one_ceiling_pa(
            bound_catalog,
            species,
            temperature_K,
            _snapshot_transport_pO2_bar(snapshot),
            recorded_pO2_bar,
            source_activity=activity,
        )
    if pressure_pa is None:
        return _WallSupplyInterval(
            _WALL_SUPPLY_UNAVAILABLE,
            evidence=pressure_evidence or "no_flux_law_and_no_psat",
        )
    if record is not None and "molar_mass_kg_per_mol" in record:
        molar_mass = _explicit_molar_mass_kg_per_mol(record)
    else:
        molar_mass = _catalog_molar_mass_kg_per_mol(
            bound_catalog,
            species,
        )
    if molar_mass is None:
        return _WallSupplyInterval(
            _WALL_SUPPLY_UNAVAILABLE,
            evidence="missing_molar_mass",
        )
    rate_kg_hr = _activity_one_bound_rate_kg_hr(
        temperature_K=temperature_K,
        pressure_pa=pressure_pa,
        molar_mass_kg_per_mol=molar_mass,
        area_m2=area_m2,
        alpha=_cited_evaporation_alpha(record),
    )
    return _WallSupplyInterval(
        _WALL_SUPPLY_UPPER_BOUND,
        rate_kg_hr=rate_kg_hr,
        evidence=SUPPLY_INTERVAL_UPPER_BOUND_USED,
    )


def _classify_wall_supply_interval(
    snapshot: Any,
    species: str,
    catalog: Any | None = None,
    recorded_pO2_bar: Sequence[float] = (),
) -> _WallSupplyInterval:
    """Classify one refused-wall species on one interval.

    This is the only classifier the supply integral uses. A
    positive-duration interval contributes exactly one of:

    - ``computed``: the rate map names this species. Any finite
      non-negative magnitude counts, including 0 and a rate at or below
      the 1e-12 kg/h melt-debit floor. Ledger resolution is not applied
      here. A numeric rate wins over a refusal on the same interval.
      A zero-net carrier (``P_eq <= P_bulk``) is this class at rate 0.
    - ``physical_zero``: the species is absent from that map and typed
      evidence covers this species. A whole-interval certificate
      (``wall_supply_interval_physical_zero``, evidence
      ``authorized_zero``) covers an absent species and a stale eligible
      channel whose rate the freeze or campaign gate discarded. It does
      not cover a refusal or an upper bound on that species. A
      species-specific authorized zero (``pressure.kind``
      ``zero_by_physics`` with an eligible flux) or below-bound zero
      (``proven_below_threshold``, doctrine category 3) covers that
      species when the certificate does not.
    - ``supply_interval_upper_bound``: there is no computed rate and no
      covering zero, and an activity-1 ceiling can be formed. The rate
      is the Hertz-Knudsen flux at that ceiling. A computed activity on
      the interval replaces activity 1. The ceiling is an explicit
      ``activity_one_psat_pa`` or the run catalog. An oxygen-dependent
      law uses the transport pO2 this hour's
      vapour evaluation consumed, preserved on the interval. The
      snapshot reservoir is a later state and is not that pressure.
      When this hour recorded none, the run pO2 that maximises P is
      used (d-401). A missing rate map and an absent carrier record
      still reach this class. One such interval does not drop the
      species.
    - ``unavailable``: no flux law and no ceiling, or an operand of
      that ceiling is missing (temperature, area, molar mass, or any
      recorded transport pO2 for an oxygen-dependent law). The evidence
      names which. One unavailable interval drops the species. The
      partial sum of the other intervals is not a supply.

    A non-positive duration is ``outside_window``. It adds no mass and
    is not a gap.
    """

    duration_h = _snapshot_duration_h(snapshot)
    rates = _snapshot_refused_flux_rates(snapshot)
    if duration_h == 0.0:
        if isinstance(rates, MappingABC) and species in rates:
            _require_vapour_supply_rate(rates[species], species)
        return _WallSupplyInterval(_WALL_SUPPLY_OUTSIDE)
    if isinstance(rates, MappingABC) and species in rates:
        return _WallSupplyInterval(
            _WALL_SUPPLY_COMPUTED,
            rate_kg_hr=_require_vapour_supply_rate(rates[species], species),
            evidence="computed_rate",
        )
    flux = getattr(snapshot, "evap_flux", None)
    # A refusal or upper bound outranks the certificate. An eligible
    # channel copied and then discarded by the freeze or campaign gate
    # does not: that species is absent from the rate map and the
    # certificate covers it. A missing rate map is not a withhold: the
    # hour may still carry a ceiling operand.
    if not _interval_certificate_leaves_species_uncovered(flux, species):
        interval_reason = _interval_physical_zero_reason(flux)
        if interval_reason:
            return _WallSupplyInterval(
                _WALL_SUPPLY_PHYSICAL_ZERO,
                evidence=f"interval_physical_zero:{interval_reason}",
            )
        evidence = _physical_zero_evidence(flux, species)
        if evidence:
            return _WallSupplyInterval(
                _WALL_SUPPLY_PHYSICAL_ZERO,
                evidence=evidence,
            )
    return _activity_one_refused_supply_interval(
        snapshot,
        species,
        catalog,
        recorded_pO2_bar,
    )


_ACTIVITY_VERDICTS_THAT_BOUND_PRESSURE = frozenset({
    "Point",
    "StatusBearingValue",
    "UpperBound",
})


def _computed_source_activity(
    record: Mapping[str, Any] | None,
) -> float | None:
    """Computed activity on this interval, or None to keep activity 1.

    A point, a status-bearing value, or an upper bound on the activity
    replaces activity 1. A lower bound is not an upper bound on
    pressure. A refusal is not an activity. A value above 1 does not
    raise the activity-1 ceiling (d-369).
    """

    if record is None:
        return None
    raw: Any = None
    present = False
    if "computed_activity" in record:
        present = True
        raw = record.get("computed_activity")
    else:
        payload = record.get("source_reaction_activity")
        if isinstance(payload, MappingABC):
            verdict = str(payload.get("verdict") or "")
            if (
                verdict in _ACTIVITY_VERDICTS_THAT_BOUND_PRESSURE
                and "value" in payload
            ):
                present = True
                raw = payload.get("value")
        if not present:
            extra = record.get("extra")
            if isinstance(extra, MappingABC) and "source_activity" in extra:
                present = True
                raw = extra.get("source_activity")
    if not present or raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise CoatingFeasibilityReportError(
            "computed activity must be numeric"
        )
    activity = float(raw)
    if not math.isfinite(activity) or activity < 0.0:
        raise CoatingFeasibilityReportError(
            "computed activity must be finite and non-negative"
        )
    if activity > 1.0:
        return 1.0
    return activity


def _ceiling_activity_exponent(
    record: Mapping[str, Any] | None,
    catalog: Any | None,
    species: str,
) -> float | None:
    """Activity exponent for an explicit activity-1 pressure."""

    if record is not None and "activity_exponent" in record:
        raw = record.get("activity_exponent")
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            raise CoatingFeasibilityReportError(
                "activity exponent must be numeric"
            )
        exponent = float(raw)
        if not math.isfinite(exponent):
            raise CoatingFeasibilityReportError(
                "activity exponent must be finite"
            )
        return exponent
    if catalog is None or not hasattr(catalog, "evaluator_for_hot_train"):
        return None
    from simulator.vapour_rail.catalog import CatalogCompileError

    try:
        evaluator = catalog.evaluator_for_hot_train(species)
        exponent = float(getattr(evaluator, "activity_exponent", 0.0))
    except (
        CatalogCompileError,
        VaporPressureNumericalOverflowError,
        VaporPressureRangeError,
        OverflowError,
        ValueError,
        TypeError,
        AttributeError,
    ):
        return None
    if not math.isfinite(exponent):
        return None
    return exponent


def _legacy_vapour_row(
    catalog: Any | None,
    species: str,
) -> Mapping[str, Any] | None:
    """Publisher species row, metals first, as the molar-mass lookup does."""

    if catalog is None or not hasattr(catalog, "legacy_view"):
        return None
    from simulator.vapour_rail.catalog import CatalogCompileError

    try:
        legacy = catalog.legacy_view()
    except CatalogCompileError:
        return None
    if not isinstance(legacy, MappingABC):
        return None
    for group in ("metals", "oxide_vapors"):
        rows = legacy.get(group) or {}
        if not isinstance(rows, MappingABC):
            continue
        row = rows.get(species)
        if isinstance(row, MappingABC) and row:
            return row
    return None


def _positive_kg_per_kg(raw: Any, label: str) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise CoatingFeasibilityReportError(f"{label} must be numeric")
    value = float(raw)
    if not math.isfinite(value) or value <= 0.0:
        raise CoatingFeasibilityReportError(
            f"{label} must be finite and positive"
        )
    return value


@dataclass(frozen=True)
class _ParentOxideDebit:
    """One carrier's parent-oxide conservation operands.

    ``resolved`` carries the parent and the kilograms of oxide per
    kilogram of vapour. ``unparented`` is a carrier with neither parent
    nor ratio after the record, the catalog, and the evaporation-stoich
    owner have been asked. That absence is not an exemption: a refused
    bound stays typed unavailable, and a computed rate is a gap whose
    oxide cannot be named. ``missing_stoichiometry`` is a parent without
    a ratio, or a ratio without a parent. That is not a number.
    """

    status: str
    parent: str | None = None
    oxide_per_vapour_kg: float | None = None


def _parent_oxide_debit_per_vapour_kg(
    snapshot: Any,
    species: str,
    catalog: Any | None,
) -> _ParentOxideDebit:
    """Kilograms of parent oxide consumed per kilogram of this vapour.

    Premise. One parent-oxide inventory is the element budget. K2O_gas,
    K2, and K all debit K2O, so the potassium in the three of them cannot
    exceed the potassium in that oxide. Na2O_gas, Na2, and Na debit Na2O
    the same way. ``stoich_oxide_per_vapor`` is the publisher kilogram
    ratio. An elemental carrier with no stored ratio uses
    ``EvaporationMixin._evaporation_stoich`` (``STOICH_RATIOS``). The
    catalog resolved here is the same one the activity-1 ceiling uses,
    including a catalog that exists only on the snapshot.

    Algebra. ``vapour_kg = oxide_kg / stoich``. A stored
    ``stoich_oxide_per_vapor`` is that ratio. The elemental fallback is
    the owner's ``oxide_per_product_kg``, not a second formula built
    from a catalog molar mass. Carriers of one oxide split that oxide
    in proportion to their kinetic oxide demand. They do not each
    receive the whole inventory.

    Unit check. ``stoich`` is kg oxide / kg vapour. Dividing a kilogram
    inventory by it returns kilograms of vapour.

    Sanity value. The owner returns ``M(K2O) / (2 * M(K))`` from
    ``STOICH_RATIOS``. M(K) = 39.0983 g/mol and M(K2O) = 94.1956 g/mol,
    so the ratio is 1.204599688477504. 1 kg of K2O becomes
    0.8301513021839662 kg of K. K2 and K2O_gas keep their stored ratios.
    Spending that kilogram on one carrier leaves none for the others.
    A catalog mass for K is not an input to this ratio.

    A parent without a ratio the owner can supply, or a ratio without
    a parent, is ``missing_stoichiometry``. The kinetic ceiling is not
    a stand-in for that operand. A carrier with neither parent nor
    ratio is ``unparented``. Missing metadata does not prove the
    carrier is outside the oxide budget, so a refused bound of that
    carrier is typed unavailable rather than the kinetic ceiling. A
    computed rate with neither operand is the same gap, not a skipped
    debit.
    """

    catalog = _bound_vapour_catalog(snapshot, catalog)
    record = _carrier_channel(snapshot, species)
    parent: str | None = None
    ratio: float | None = None
    if record is not None:
        raw_parent = record.get("parent_oxide")
        if isinstance(raw_parent, str) and raw_parent.strip():
            parent = raw_parent.strip()
        if "stoich_oxide_per_vapor" in record:
            ratio = _positive_kg_per_kg(
                record.get("stoich_oxide_per_vapor"),
                "stoich_oxide_per_vapor",
            )
    row = _legacy_vapour_row(catalog, species)
    if row is not None:
        if parent is None:
            raw_parent = row.get("parent_oxide")
            if isinstance(raw_parent, str) and raw_parent.strip():
                parent = raw_parent.strip()
        if ratio is None and row.get("stoich_oxide_per_vapor") is not None:
            ratio = _positive_kg_per_kg(
                row.get("stoich_oxide_per_vapor"),
                "stoich_oxide_per_vapor",
            )
    if parent is not None and ratio is None:
        ratio = _elemental_oxide_per_vapour_kg(parent, species)
    if parent is not None and ratio is not None:
        return _ParentOxideDebit("resolved", parent, ratio)
    if parent is None and ratio is None:
        return _ParentOxideDebit("unparented")
    return _ParentOxideDebit("missing_stoichiometry", parent, ratio)


def _elemental_oxide_per_vapour_kg(parent: str, species: str) -> float | None:
    """Owner ratio for the elemental metal of ``parent``, or None.

    ``EvaporationMixin._evaporation_stoich`` is the production debit.
    Its elemental fallback is ``STOICH_RATIOS``. A catalog molar mass
    is a different operand and must not allocate the same atoms twice
    under another number. A carrier that is not that metal has no
    elemental fallback; the stored ``stoich_oxide_per_vapor`` is its
    ratio, and a missing one is not invented here.
    """

    from simulator.accounting import AccountingError
    from simulator.evaporation import EvaporationMixin

    host = EvaporationMixin.__new__(EvaporationMixin)
    host.species_formula_registry = {}
    try:
        stoich = host._evaporation_stoich(species, {"parent_oxide": parent})
    except AccountingError:
        return None
    if not isinstance(stoich, MappingABC):
        return None
    return _positive_kg_per_kg(
        stoich.get("oxide_per_product_kg"),
        "oxide_per_product_kg",
    )


def _oxide_inventory_kg_map(oxides: Mapping[str, Any]) -> dict[str, float]:
    parsed: dict[str, float] = {}
    for oxide, raw in oxides.items():
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            raise CoatingFeasibilityReportError(
                f"parent-oxide inventory {oxide!r} must be numeric"
            )
        kg = float(raw)
        if not math.isfinite(kg) or kg < 0.0:
            raise CoatingFeasibilityReportError(
                f"parent-oxide inventory {oxide!r} must be finite and "
                "non-negative"
            )
        parsed[str(oxide)] = kg
    return parsed


def _snapshot_oxide_inventory_kg(snapshot: Any) -> dict[str, float] | None:
    """Parent-oxide kilograms still in the melt, or None if unrecorded.

    ``inventory.melt_oxide_kg`` is that inventory. A recorded mapping,
    including an empty one, is authoritative: empty means zero, and a
    composition must not replace it. Composition and melt mass are the
    inventory only when the kilogram map is absent (no inventory, a
    missing key, or None). No inventory and no composition is an
    unknown melt. A refused bound that debits a parent oxide cannot
    replace that operand with the kinetic ceiling.
    """

    inventory = getattr(snapshot, "inventory", None)
    oxides: Any = None
    if isinstance(inventory, MappingABC):
        oxides = inventory.get("melt_oxide_kg")
    elif inventory is not None:
        oxides = getattr(inventory, "melt_oxide_kg", None)
    # Recorded map, including {}. Composition is not consulted.
    if isinstance(oxides, MappingABC):
        return _oxide_inventory_kg_map(oxides)
    composition = getattr(snapshot, "composition_wt_pct", None)
    melt_mass = getattr(snapshot, "melt_mass_kg", None)
    if (
        isinstance(composition, MappingABC)
        and composition
        and isinstance(melt_mass, int | float)
        and not isinstance(melt_mass, bool)
    ):
        mass = float(melt_mass)
        if math.isfinite(mass) and mass > 0.0:
            converted: dict[str, float] = {}
            for oxide, raw_pct in composition.items():
                if isinstance(raw_pct, bool) or not isinstance(
                    raw_pct, int | float
                ):
                    raise CoatingFeasibilityReportError(
                        "composition_wt_pct must be numeric for the "
                        "inventory cap"
                    )
                pct = float(raw_pct)
                if not math.isfinite(pct) or pct < 0.0:
                    raise CoatingFeasibilityReportError(
                        "composition_wt_pct must be finite and non-negative "
                        "for the inventory cap"
                    )
                converted[str(oxide)] = mass * pct / 100.0
            if converted:
                return converted
    return None


@dataclass(frozen=True)
class _ComputedOxideDraws:
    """Computed oxide debits on one interval, plus the gaps.

    ``missing_stoich_species`` had a computed rate and a missing
    conservation operand. ``unreservable_parents`` are the oxides those
    rates name. ``unattributed_oxide_debit`` is a computed rate whose
    oxide cannot be named: a ratio with no parent, or neither operand.
    The atoms were spent. A missing operand is not a zero debit.
    """

    oxide_kg: Mapping[str, float]
    missing_stoich_species: frozenset[str]
    unreservable_parents: frozenset[str]
    unattributed_oxide_debit: bool = False


def _empty_oxide_draws() -> _ComputedOxideDraws:
    return _ComputedOxideDraws({}, frozenset(), frozenset())


def _computed_parent_oxide_draws(
    snapshot: Any,
    catalog: Any | None,
) -> _ComputedOxideDraws:
    """Oxide kilograms this interval's computed vapour rates consume.

    The rate map is the computed draw. A refused carrier is absent from
    it, so this does not reserve the bound the cap still has to
    allocate. Every computed rate with a resolved parent-oxide ratio is
    a debit, including a rate of a species whose supply integral is
    withheld. Withholding drops the unpublished partial sum. It does
    not return those atoms to the melt, and a sibling must not spend
    them again. A computed carrier missing its parent, its ratio, or
    both is a typed gap. A parented carrier whose ratio the owner
    cannot supply makes that parent unreservable. A ratio with no
    parent, or neither operand, leaves the oxide unnamed, so every
    refused sibling is unreservable. Missing metadata is not proof
    the carrier sits outside the budget, and it is not a skipped debit.
    """

    rates = _snapshot_refused_flux_rates(snapshot)
    if not isinstance(rates, MappingABC):
        return _empty_oxide_draws()
    duration_h = _snapshot_duration_h(snapshot)
    if duration_h == 0.0:
        return _empty_oxide_draws()
    draws: dict[str, float] = {}
    missing: set[str] = set()
    unreservable: set[str] = set()
    unattributed = False
    for name, raw in rates.items():
        species = str(name)
        rate_kg_hr = _require_vapour_supply_rate(raw, species)
        debit = _parent_oxide_debit_per_vapour_kg(snapshot, species, catalog)
        if (
            debit.status != "resolved"
            or debit.parent is None
            or debit.oxide_per_vapour_kg is None
        ):
            # One gap for every missing operand, including both.
            # A named parent with no ratio blocks that oxide. No named
            # parent — a bare ratio, or neither operand — cannot be
            # assigned, so every refused sibling is unreservable.
            # Skipping the unparented rate left its atoms unreserved
            # and let a sibling spend the whole charge.
            missing.add(species)
            if debit.parent:
                unreservable.add(debit.parent)
            elif (
                debit.oxide_per_vapour_kg is not None
                or debit.status == "unparented"
            ):
                unattributed = True
            continue
        draws[debit.parent] = (
            draws.get(debit.parent, 0.0)
            + rate_kg_hr * duration_h * debit.oxide_per_vapour_kg
        )
    return _ComputedOxideDraws(
        draws,
        frozenset(missing),
        frozenset(unreservable),
        unattributed,
    )


def _pre_evaporation_oxide_kg(snapshot: Any) -> dict[str, float] | None:
    """Parent-oxide kilograms at the start of this hour, before its debit.

    Production snapshots are taken after evaporation. The remainder on
    ``inventory.melt_oxide_kg`` is not this map. An absent attribute
    means the snapshot recorded its inventory as the charge directly.
    An empty map is a known empty melt, not a missing operand.
    """

    flux = getattr(snapshot, "evap_flux", None)
    raw = getattr(flux, PRE_EVAPORATION_MELT_OXIDE_KG_ATTR, None)
    if raw is None:
        raw = getattr(snapshot, PRE_EVAPORATION_MELT_OXIDE_KG_ATTR, None)
    if raw is None:
        return None
    if not isinstance(raw, MappingABC):
        raise CoatingFeasibilityReportError(
            "pre-evaporation melt oxide inventory must be a mapping"
        )
    return _oxide_inventory_kg_map(raw)


@dataclass(frozen=True)
class _OxideWindowBudget:
    """Charge at the window start, computed debits, and stoich gaps."""

    charge_kg: Mapping[str, float]
    computed_kg: Mapping[str, float]
    missing_stoich_species: frozenset[str]
    unreservable_parents: frozenset[str]
    unattributed_oxide_debit: bool = False


def _window_parent_oxide_budget(
    snapshots: Sequence[Any],
    catalog: Any | None,
) -> _OxideWindowBudget:
    """Pre-evaporation charge and every computed oxide debit.

    The charge is the inventory at the start of the window, before any
    evaporation in the window. Production copies the snapshot after
    that debit, so the first snapshot's ``pre_evaporation_melt_oxide_kg``
    is the charge and its ``melt_oxide_kg`` is the remainder. Using the
    remainder and then subtracting the same debit counts the first hour
    twice. The first recorded map is the whole charge. An oxide absent
    from that map, including every oxide when the map is empty, is a
    charge of zero. A later snapshot does not add an oxide and does not
    replace one. Computed draws anywhere in the window are one debit
    against it, including a draw by a species the integral will not
    publish. A computed ratio with no parent, or a computed rate with
    neither operand, marks the debit unattributed.
    """

    charge: dict[str, float] | None = None
    computed: dict[str, float] = {}
    missing: set[str] = set()
    unreservable: set[str] = set()
    unattributed = False
    for snapshot in snapshots:
        if charge is None:
            pre = _pre_evaporation_oxide_kg(snapshot)
            if pre is not None:
                # Empty included. Absent oxides stay absent, which the
                # cap reads as zero once any map has been recorded.
                charge = dict(pre)
            else:
                post = _snapshot_oxide_inventory_kg(snapshot)
                if post is not None:
                    charge = dict(post)
        draw = _computed_parent_oxide_draws(snapshot, catalog)
        for parent, kg in draw.oxide_kg.items():
            computed[parent] = computed.get(parent, 0.0) + kg
        missing.update(draw.missing_stoich_species)
        unreservable.update(draw.unreservable_parents)
        unattributed = unattributed or draw.unattributed_oxide_debit
    return _OxideWindowBudget(
        {} if charge is None else charge,
        computed,
        frozenset(missing),
        frozenset(unreservable),
        unattributed,
    )


def _window_inventory_recorded(snapshots: Sequence[Any]) -> bool:
    """True when the window has a charge operand, including an empty map.

    ``_snapshot_oxide_inventory_kg`` is that operand: a recorded
    kilogram map, or composition when the map is absent. A
    pre-evaporation map counts. The allocator and the publication cap
    both read this bit. An empty recorded map is a charge of zero for
    every oxide it omits.
    """

    return any(
        _snapshot_oxide_inventory_kg(snapshot) is not None
        or _pre_evaporation_oxide_kg(snapshot) is not None
        for snapshot in snapshots
    )


def _inventory_cap_upper_bounds(
    snapshot: Any,
    classified: Sequence[tuple[str, _WallSupplyInterval]],
    catalog: Any | None,
    booked_oxide_kg: dict[str, float],
    charge_oxide_kg: Mapping[str, float],
    computed_oxide_kg: Mapping[str, float],
    *,
    inventory_map_recorded: bool,
) -> tuple[dict[str, float], set[str]]:
    """Assign every refused upper bound on this snapshot a vapour mass.

    The returned map is kilograms of vapour, already multiplied by the
    interval duration. It contains every upper bound in ``classified``.
    Computed rates are not rewritten. They are reserved first, across
    the whole window, including a computed hour that comes later, a
    computed carrier of the same element, and a computed carrier whose
    own supply is withheld.

    Invariant. For one parent oxide, the oxide-equivalent of the
    published supply — computed vapour plus refused upper bounds — does
    not exceed the inventory at the start of the window. That start is
    the pre-evaporation charge, not the first snapshot's post-debit
    remainder. This function allocates the refused bounds only. It
    leaves a computed rate unchanged. When the computed draw already
    exceeds the charge, the refused remainder here is zero.
    ``_enforce_published_element_inventory`` then scales the published
    computed vapour down to the charge. No upper bound is left out of
    the returned map. A caller that added the kinetic mass for a
    missing key would spend the charge twice.

    Derivation. A refused hour can still show the undepleted melt. The
    cap used to allocate against that snapshot and then add the computed
    mass, so 1 kg of K2O became 1 kg of refused K2O_gas plus another
    kilogram of computed vapour (d-401 amendment 2). The same hole
    exists when the computed hour is earlier and the inventory map was
    not debited, and when the computed carrier is withheld: its measured
    debit was skipped, so the sibling received the full charge. Let C
    be the pre-evaporation inventory at the window start, D the
    oxide-equivalent of every computed rate in the window, S the
    inventory on this snapshot, and B the refused oxide already
    allocated. Production snapshots are taken after evaporation, so the
    first snapshot's melt map is already C minus that hour's debit.
    Using that remainder as C and subtracting D again leaves the later
    refused bound short by that debit. When this snapshot records an
    inventory:

        available = min(S, max(0, C - D)) - B, floored at 0.

    When it records none, S is not infinite. The known charge remains
    the ceiling:

        available = max(0, C - D) - B, floored at 0.

    A window that recorded no inventory map at all does not reach this
    function for a parented carrier. ``_allocate_parent_oxide_supply``
    withholds that species. A recorded map that omits the oxide is a
    charge of zero, including an empty map; a later snapshot that omits
    the map does not reopen the kinetic ceiling. C - D is the charge
    after the computed reserve. S is a tighter cap when this snapshot
    already shows a smaller melt. Subtracting D from S as well would
    count a post-debit drop twice when S is already the remainder and
    C is the pre-evaporation charge. When the map is not debited, S = C
    and available = C - D - B. Two carriers split that remainder in
    proportion to oxide demand. Species order does not change D. A
    carrier with neither parent nor ratio is not given that ceiling:
    missing metadata is not an exemption, and the allocator withholds
    it before this function. A parented carrier with no ratio is not
    given the ceiling either.

    Sanity value. 1 kg of K2O, stoich 1. Hour 1 computes 0.25 kg of
    K2O_gas and refuses K2. Hour 2 has no flux law for K2O_gas, so
    K2O_gas is not published. D still holds the 0.25 kg. K2 receives
    0.75 kg, not 1 kg. A second hour that refuses K2O_gas after the
    1 kg charge is exhausted, and that records no inventory, adds 0
    rather than the kinetic ceiling.
    """

    inventory = _snapshot_oxide_inventory_kg(snapshot)
    duration_h = _snapshot_duration_h(snapshot)
    grouped: dict[str, list[tuple[str, float, float]]] = {}
    replacements: dict[str, float] = {}
    for species, contribution in classified:
        if contribution.kind != _WALL_SUPPLY_UPPER_BOUND:
            continue
        if contribution.rate_kg_hr is None:
            raise CoatingFeasibilityReportError(
                "activity-one supply bound for "
                f"{species!r} is missing its rate"
            )
        kinetic_kg = float(contribution.rate_kg_hr) * duration_h
        debit = _parent_oxide_debit_per_vapour_kg(snapshot, species, catalog)
        # Neither operand, or one operand, is not a kinetic mass. The
        # allocator withholds those carriers. Publishing ``kinetic_kg``
        # here would spend an empty melt.
        if (
            debit.status != "resolved"
            or debit.parent is None
            or debit.oxide_per_vapour_kg is None
        ):
            raise CoatingFeasibilityReportError(
                "refused supply of "
                f"{species!r} is missing parent-oxide stoichiometry"
            )
        grouped.setdefault(debit.parent, []).append(
            (species, kinetic_kg, debit.oxide_per_vapour_kg)
        )
    capped: set[str] = set()
    for parent, rows in grouped.items():
        # A key in ``charge_oxide_kg`` is the first record of that oxide.
        # A recorded map that never names it is zero, not an unknown
        # melt. No recorded map at all is not this branch.
        charge_known = parent in charge_oxide_kg or inventory_map_recorded
        if inventory is None and not charge_known:
            raise CoatingFeasibilityReportError(
                f"parent oxide {parent!r} has a refused supply and no "
                "recorded inventory"
            )
        if parent in charge_oxide_kg:
            charge = charge_oxide_kg[parent]
        else:
            charge = 0.0
        if inventory is None:
            snapshot_kg: float | None = None
        else:
            snapshot_kg = inventory.get(parent, 0.0)
        computed = computed_oxide_kg.get(parent, 0.0)
        ceiling = charge - computed
        if ceiling < 0.0:
            ceiling = 0.0
        booked = booked_oxide_kg.get(parent, 0.0)
        if snapshot_kg is None:
            available = ceiling - booked
        else:
            available = min(snapshot_kg, ceiling) - booked
        if available < 0.0:
            available = 0.0
        demands = {
            species: vapour_kg * oxide_per_vapour
            for species, vapour_kg, oxide_per_vapour in rows
        }
        total_demand = sum(demands.values())
        if total_demand <= 0.0:
            for species, _vapour_kg, _oxide_per_vapour in rows:
                replacements[species] = 0.0
            continue
        if total_demand <= available or math.isclose(
            total_demand,
            available,
            rel_tol=1e-12,
            abs_tol=1e-15,
        ):
            scale = 1.0
        else:
            scale = available / total_demand
        used = 0.0
        for species, _vapour_kg, oxide_per_vapour in rows:
            demand = demands[species]
            allocated = demand * scale
            used += allocated
            replacements[species] = allocated / oxide_per_vapour
            if allocated < demand and not math.isclose(
                allocated,
                demand,
                rel_tol=1e-12,
                abs_tol=1e-15,
            ):
                capped.add(species)
        booked_oxide_kg[parent] = booked + used
    return replacements, capped


@dataclass(frozen=True)
class _ParentOxideAllocation:
    """Refused-bound kilograms from the one parent-oxide allocator.

    ``upper_bound_kg`` is parallel to the classified window. Each dict
    is vapour kilograms already multiplied by that interval's duration.
    ``withheld`` species are absent from those dicts. ``unavailable``
    names why. Computed rates are not in this object; the integral adds
    them, and their oxide debit is already inside the budget this
    allocation spent.
    """

    upper_bound_kg: tuple[Mapping[str, float], ...]
    inventory_capped: frozenset[str]
    withheld: frozenset[str]
    unavailable: Mapping[str, list[str]]


def _allocate_parent_oxide_supply(
    classified_window: Sequence[
        tuple[Any, Sequence[tuple[str, _WallSupplyInterval]]]
    ],
    catalog: Any | None,
) -> _ParentOxideAllocation:
    """Allocate every refused bound against the shared parent-oxide charge.

    This is the only function that turns a refused interval into
    kilograms. The integral adds a computed rate or one value from
    ``upper_bound_kg``. It does not read the kinetic rate again.

    Invariant, for each element, at this function only. The sum over
    all of that element's carriers of booked element mass — computed
    debits plus bounded refused supplies — is at most the element's
    inventory at the start of the integration window. That start is
    the pre-evaporation charge. The first snapshot's post-debit
    remainder is not the charge: snapshots are taken after evaporation.
    A recorded map that never names the oxide, including an empty melt,
    is a charge of zero. That map is the first one recorded. A later
    snapshot cannot add an oxide the first map omitted. The charge is
    unknown only when the window recorded no inventory map and no
    pre-evaporation map at all. Let D be the oxide-equivalent of every
    computed vapour rate, including a rate of a species that is not
    published. A refused bound then receives only what remains after D
    and after refused oxide already allocated. A snapshot that omits
    its inventory does not make the remainder infinite.

    A missing conservation operand is not a number. No parent-oxide
    inventory is ``missing_parent_oxide_inventory``. A parent without a
    ratio, or a ratio without a parent, after the evaporation-stoich
    owner has been asked, is ``missing_parent_oxide_stoichiometry``.
    Neither operand is the same typed gap: missing metadata is not an
    exemption, and the kinetic ceiling is not the supply. A computed
    carrier missing its parent, its ratio, or both is that gap for the
    element budget. A named parent with no ratio makes that parent
    unreservable, so no refused sibling of it may draw. A computed
    ratio with no parent, or a computed rate with neither operand,
    cannot name the oxide, so every refused bound in the window is
    withheld: a sibling must not spend atoms the debit may already
    have taken. A recorded inventory map, including an empty one, is
    the charge; composition fills in only when that map is absent.
    Species order does not change the kilograms.
    """

    unavailable: dict[str, list[str]] = {}

    def record_unavailable(species: str, reason: str) -> None:
        normalized = str(reason).strip()
        if normalized:
            reasons = unavailable.setdefault(species, [])
            if normalized not in reasons:
                reasons.append(normalized)

    classifier_withheld: set[str] = set()
    for _snapshot, classified in classified_window:
        for species, contribution in classified:
            if contribution.kind != _WALL_SUPPLY_UNAVAILABLE:
                continue
            classifier_withheld.add(species)
            # Preserve distinct classifier gaps in interval order. A
            # conservation-only withhold adds its own token below.
            reason = str(contribution.evidence or "").strip()
            if reason:
                record_unavailable(species, reason)
    snapshots = tuple(snapshot for snapshot, _classified in classified_window)
    budget = _window_parent_oxide_budget(snapshots, catalog)
    charge_oxide_kg = budget.charge_kg
    computed_oxide_kg = budget.computed_kg
    # One recorded map anywhere, including an empty melt or a
    # pre-evaporation map, makes every unnamed parent a charge of zero.
    # Withholding for a missing inventory is only a window that recorded
    # neither.
    inventory_map_recorded = _window_inventory_recorded(snapshots)
    withheld = set(classifier_withheld)
    for species in budget.missing_stoich_species:
        withheld.add(species)
        record_unavailable(species, MISSING_PARENT_OXIDE_STOICHIOMETRY)
    for snapshot, classified in classified_window:
        for species, contribution in classified:
            if species in withheld:
                continue
            if contribution.kind != _WALL_SUPPLY_UPPER_BOUND:
                continue
            debit = _parent_oxide_debit_per_vapour_kg(
                snapshot,
                species,
                catalog,
            )
            if budget.unattributed_oxide_debit:
                # The computed debit cannot name its oxide: a ratio
                # with no parent, or neither operand. It may belong to
                # any oxide, so no refused bound is provable.
                withheld.add(species)
                record_unavailable(species, MISSING_PARENT_OXIDE_STOICHIOMETRY)
                continue
            if debit.status in {"missing_stoichiometry", "unparented"}:
                withheld.add(species)
                record_unavailable(species, MISSING_PARENT_OXIDE_STOICHIOMETRY)
            elif (
                debit.status == "resolved"
                and debit.parent in budget.unreservable_parents
            ):
                withheld.add(species)
                record_unavailable(species, MISSING_PARENT_OXIDE_STOICHIOMETRY)
    if not inventory_map_recorded:
        for snapshot, classified in classified_window:
            for species, contribution in classified:
                if species in withheld:
                    continue
                if contribution.kind != _WALL_SUPPLY_UPPER_BOUND:
                    continue
                debit = _parent_oxide_debit_per_vapour_kg(
                    snapshot,
                    species,
                    catalog,
                )
                if debit.status != "resolved" or debit.parent is None:
                    continue
                if debit.parent in charge_oxide_kg:
                    continue
                withheld.add(species)
                record_unavailable(species, MISSING_PARENT_OXIDE_INVENTORY)
    booked_oxide_kg: dict[str, float] = {}
    inventory_capped: set[str] = set()
    upper_rows: list[dict[str, float]] = []
    for snapshot, classified in classified_window:
        eligible = tuple(
            (species, contribution)
            for species, contribution in classified
            if species not in withheld
        )
        capped_kg, capped_species = _inventory_cap_upper_bounds(
            snapshot,
            eligible,
            catalog,
            booked_oxide_kg,
            charge_oxide_kg,
            computed_oxide_kg,
            inventory_map_recorded=inventory_map_recorded,
        )
        inventory_capped.update(capped_species)
        row: dict[str, float] = {}
        for species, contribution in classified:
            if species in withheld:
                continue
            if contribution.kind != _WALL_SUPPLY_UPPER_BOUND:
                continue
            if species not in capped_kg:
                raise CoatingFeasibilityReportError(
                    "refused supply of "
                    f"{species!r} was not allocated against its parent oxide"
                )
            row[species] = capped_kg[species]
        upper_rows.append(row)
    return _ParentOxideAllocation(
        tuple(upper_rows),
        frozenset(inventory_capped),
        frozenset(withheld),
        unavailable,
    )


def _enforce_published_element_inventory(
    totals: dict[str, float],
    candidates: AbstractSet[str],
    computed_vapour_kg: Mapping[str, float],
    snapshots: Sequence[Any],
    catalog: Any | None,
) -> tuple[set[str], set[str]]:
    """Cap candidate supplies using every computed carrier's debit.

    This is the only publication of a wall-supply kilogram. Computed
    rates and refused bounds are already summed into ``totals``. Both
    go through this comparison. Nothing downstream integrates a flux.

    Invariant. For every element whose window-start inventory is known,
    the oxide-equivalent of all computed carriers plus the bounded
    refused supplies in this candidate set is at most that inventory.
    Computed debits come from the full window budget, including carriers
    outside the returned map and carriers withheld from it. The
    inventory is the pre-evaporation charge. A recorded map that omits
    the oxide, including an empty map, is a charge of zero. No recorded
    map is an unknown charge: this function does not invent one and does
    not scale that element.

    When the all-carrier oxide exceeds the charge, each candidate total
    is scaled by ``charge / all_carrier_oxide`` before the caller selects
    the returned subset. The scale does not depend on species order or
    on which interval held the kilogram. Splitting a carrier across
    intervals does not change it.

    The first returned set is ``computed_exceeds_inventory``: carriers
    whose computed vapour was reduced because that computed oxide
    itself exceeded the charge. The second set is extra
    ``inventory_capped`` carriers reduced for any other excess. The
    computed flag is a diagnostic, not a debit repair. Rates at or
    below 1e-12 kg/h stay on the wall-supply map and never enter the
    melt debit (``EvaporationMixin._overlay_depleted_wall_supply``).
    This function does not change that debit.

    A published kilogram whose parent and ratio cannot be resolved is
    not an element supply. The allocator withholds that carrier before
    publication. This function leaves a mass it cannot attribute.
    """

    budget = _window_parent_oxide_budget(snapshots, catalog)
    recorded = _window_inventory_recorded(snapshots)
    grouped: dict[str, list[tuple[str, float]]] = {}
    for species in candidates:
        if species not in totals:
            continue
        resolved: tuple[str, float] | None = None
        for snapshot in snapshots:
            debit = _parent_oxide_debit_per_vapour_kg(snapshot, species, catalog)
            if (
                debit.status == "resolved"
                and debit.parent is not None
                and debit.oxide_per_vapour_kg is not None
            ):
                resolved = (debit.parent, float(debit.oxide_per_vapour_kg))
                break
        if resolved is None:
            continue
        parent, stoich = resolved
        grouped.setdefault(parent, []).append((species, stoich))
    computed_flags: set[str] = set()
    capped_flags: set[str] = set()
    for parent, rows in grouped.items():
        if parent in budget.charge_kg:
            charge = float(budget.charge_kg[parent])
        elif recorded:
            charge = 0.0
        else:
            continue
        bounded_oxide = 0.0
        for species, stoich in rows:
            computed_kg = float(computed_vapour_kg.get(species, 0.0))
            bounded_kg = max(float(totals[species]) - computed_kg, 0.0)
            bounded_oxide += bounded_kg * stoich
        computed_oxide = float(budget.computed_kg.get(parent, 0.0))
        published_oxide = computed_oxide + bounded_oxide
        if published_oxide <= charge or math.isclose(
            published_oxide,
            charge,
            rel_tol=1e-12,
            abs_tol=1e-15,
        ):
            continue
        scale = 0.0 if published_oxide == 0.0 else charge / published_oxide
        cause_is_computed = computed_oxide > charge and not math.isclose(
            computed_oxide,
            charge,
            rel_tol=1e-12,
            abs_tol=1e-15,
        )
        for species, _stoich in rows:
            computed_kg = float(computed_vapour_kg.get(species, 0.0))
            bounded_kg = max(float(totals[species]) - computed_kg, 0.0)
            old = computed_kg + bounded_kg
            new = old * scale
            if new < 0.0:
                new = 0.0
            totals[species] = new
            if math.isclose(old, new, rel_tol=1e-12, abs_tol=1e-15):
                continue
            if cause_is_computed and computed_kg > 0.0:
                computed_flags.add(species)
            else:
                capped_flags.add(species)
    return computed_flags, capped_flags


def _optimizer_refused_flux_bounds(
    snapshots: Any,
    refused_species: tuple[str, ...],
    *,
    campaigns_elapsed: float = 1.0,
    catalog: Any | None = None,
) -> _WallSupplyIntegral:
    """Integral of each species' vapour supply over the window.

    The mapping view is kg/campaign, while its typed ``window`` retains the
    run-total supply and campaign count. Product summary, furnace lifespan,
    the tap projection, and the production gate consume that same window.
    No other path integrates a wall-supply flux.
    Each positive-duration interval is classified by
    ``_classify_wall_supply_interval`` and is not re-read here. Refused
    kilograms come only from ``_allocate_parent_oxide_supply``.

    The window's run total sums computed rates and the allocator's refused
    masses, then ``_enforce_published_element_inventory`` caps that sum
    at the window-start charge. A computed total above the charge is
    not published: the published oxide equals the charge, and
    ``computed_exceeds_inventory`` names the carriers that were scaled.
    That overdraw is the subthreshold debit leak. Rates at or below
    1e-12 kg/h stay on the wall-supply map and never enter the melt
    debit (``EvaporationMixin._overlay_depleted_wall_supply``). This
    integral does not repair the debit. Computed parent-oxide draws in
    the window are reserved before any refused bound is allocated,
    including a draw on a later hour, a draw by another carrier of that
    element, and a draw by a species this map does not publish. The
    refused bound then receives only what remains of the pre-evaporation
    inventory at the window start (d-401 amendment 2). The first
    snapshot's post-debit remainder is not that charge. The first
    recorded oxide map is the whole charge: an oxide it omits stays
    zero, and a later map cannot add one. A snapshot with no inventory
    does not bypass it. A refused bound with no parent, or with a ratio
    and no parent, is typed unavailable. A computed rate with a ratio
    and no parent, or with neither operand, withholds every refused
    bound in the window. ``inventory_capped`` names the species for
    which a refused limit bound. ``supply_unavailable`` names a species
    omitted from the map and its ordered, deduplicated typed reasons.
    Every classifier-unavailable interval keeps its evidence
    (``missing_temperature``, ``missing_molar_mass``,
    ``missing_recorded_pO2``, and the other ceiling gaps).
    ``supply_interval_upper_bound_used``
    counts the bounded refused intervals. That count is not divided by
    ``campaigns_elapsed``. An evidenced physical zero adds nothing and
    keeps the species observed. One unavailable interval drops that
    species: the partial sum of the other intervals is not a supply,
    and the wall refusal stays. Its computed debit still reserves the
    parent oxide. A sibling with a complete integral is still returned.
    Species order does not change the values.

    A species absent from every contributing interval is not given a
    fabricated zero. Ledger resolution is not applied here. Booking
    applies it. The typed window is the only basis source for both the
    classifier's run-total cutoff and its per-campaign normalization.
    """

    if not refused_species:
        return {}
    if not math.isfinite(campaigns_elapsed) or campaigns_elapsed <= 0.0:
        raise CoatingFeasibilityReportError(
            "campaigns_elapsed must be finite and positive for refused flux "
            "bounds"
        )
    totals = {species: 0.0 for species in refused_species}
    computed_vapour_kg: dict[str, float] = {}
    observed: set[str] = set()
    bound_counts = {species: 0 for species in refused_species}
    # One recorded-pO2 set for the window. The classifier chooses the
    # hour's own value, or the extremum that maximises P. This scan
    # does not read a flux map.
    window = tuple(snapshots or ())
    recorded_pO2_bar = _recorded_transport_pO2_bar(window)
    classified_window: list[
        tuple[Any, tuple[tuple[str, _WallSupplyInterval], ...]]
    ] = []
    for snapshot in window:
        classified_window.append(
            (
                snapshot,
                tuple(
                    (
                        species,
                        _classify_wall_supply_interval(
                            snapshot,
                            species,
                            catalog,
                            recorded_pO2_bar,
                        ),
                    )
                    for species in refused_species
                ),
            )
        )
    allocation = _allocate_parent_oxide_supply(classified_window, catalog)
    withheld = allocation.withheld
    for index, (_snapshot, classified) in enumerate(classified_window):
        for species, contribution in classified:
            if contribution.kind == _WALL_SUPPLY_UNAVAILABLE and (
                species not in withheld
            ):
                raise CoatingFeasibilityReportError(
                    "unavailable wall supply of "
                    f"{species!r} was not withheld"
                )
            if contribution.kind == _WALL_SUPPLY_OUTSIDE:
                continue
            if (
                contribution.kind == _WALL_SUPPLY_UNAVAILABLE
                or species in withheld
            ):
                continue
            if contribution.kind == _WALL_SUPPLY_PHYSICAL_ZERO:
                observed.add(species)
                continue
            if contribution.kind == _WALL_SUPPLY_COMPUTED:
                if contribution.rate_kg_hr is None:
                    raise CoatingFeasibilityReportError(
                        "computed wall supply of "
                        f"{species!r} is missing its rate"
                    )
                duration_h = _snapshot_duration_h(_snapshot)
                added = float(contribution.rate_kg_hr) * duration_h
                totals[species] += added
                computed_vapour_kg[species] = (
                    computed_vapour_kg.get(species, 0.0) + added
                )
                observed.add(species)
                continue
            if contribution.kind != _WALL_SUPPLY_UPPER_BOUND:
                raise CoatingFeasibilityReportError(
                    "wall-supply interval classifier returned "
                    f"{contribution.kind!r} for {species!r}"
                )
            try:
                totals[species] += allocation.upper_bound_kg[index][species]
            except KeyError as exc:
                raise CoatingFeasibilityReportError(
                    "refused supply of "
                    f"{species!r} was not allocated against its parent oxide"
                ) from exc
            observed.add(species)
            bound_counts[species] += 1
    candidates = frozenset(
        species for species in refused_species if species not in withheld
    )
    computed_flags, extra_capped = _enforce_published_element_inventory(
        totals,
        candidates,
        computed_vapour_kg,
        window,
        catalog,
    )
    published = frozenset(species for species in candidates if species in observed)
    capped = {species: True for species in allocation.inventory_capped}
    for species in extra_capped:
        capped[species] = True
    basis = _WallSupplyWindow(
        run_total_kg={species: float(totals[species]) for species in published},
        campaigns_elapsed=float(campaigns_elapsed),
        supply_interval_upper_bound_used={
            species: count for species, count in bound_counts.items() if count > 0
        },
        inventory_capped=capped,
        supply_unavailable=allocation.unavailable,
        computed_exceeds_inventory={species: True for species in computed_flags},
    )
    return _WallSupplyIntegral(basis)


@dataclass(frozen=True)
class PhysicsConstraintSet:
    """Stage-1 hard feasibility constraints for optimizer traces."""

    stream_purity_min: ThresholdSpec = field(default_factory=lambda: ThresholdSpec(
        id="delivered_stream_purity_min",
        value=0.95,
        units="fraction",
        source="engineering_envelope",
        source_ref="stage_purity_report PURE cutoff / optimizer profile default",
    ))
    enforce_delivered_stream_purity: bool = False
    coating_min_campaigns_to_resinter: ThresholdSpec = field(
        default_factory=lambda: ThresholdSpec(
            id="coating_min_campaigns_to_resinter",
            value=10.0,
            units="campaigns",
            source="materials.yaml",
            source_ref=(
                "data/materials.yaml:"
                "liner_materials.hot_wall_refractory_liner."
                "fast_fouling_campaign_threshold"
            ),
        )
    )
    extraction_min_fraction: ThresholdSpec = field(default_factory=lambda: ThresholdSpec(
        id="extraction_completeness_min",
        value=0.95,
        units="fraction",
        source="code_default",
        source_ref="simulator.optimize.physics.PhysicsConstraintSet.extraction_min_fraction",
    ))
    extraction_min_fraction_by_species: Mapping[str, ThresholdSpec] = field(
        default_factory=dict
    )
    knudsen_max: ThresholdSpec = field(default_factory=lambda: ThresholdSpec(
        id="knudsen_viscous_max",
        value=0.01,
        units="Kn",
        source="code_default",
        source_ref="simulator.optimize.physics.PhysicsConstraintSet.knudsen_max",
    ))
    furnace_T_max_C: ThresholdSpec = field(default_factory=lambda: ThresholdSpec(
        id="furnace_T_max_C",
        value=1800.0,
        units="degC",
        source="code_default",
        source_ref="simulator.optimize.physics.PhysicsConstraintSet.furnace_T_max_C",
    ))
    cycle_time_max_h: ThresholdSpec = field(default_factory=lambda: ThresholdSpec(
        id="cycle_time_max_h",
        value=1.0e12,
        units="h",
        source="code_default",
        source_ref="simulator.optimize.physics.PhysicsConstraintSet.cycle_time_max_h",
    ))
    target_species: tuple[str, ...] = ("SiO",)
    active_gates: tuple[str, ...] = DEFAULT_ACTIVE_GATES
    residual_species_by_target: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: DEFAULT_RESIDUAL_SPECIES_BY_TARGET
    )
    allowable_wall_deposit_kg: Mapping[tuple[str, str], ThresholdSpec] = field(
        default_factory=dict
    )

    def __getstate__(self) -> dict[str, Any]:
        return {
            "stream_purity_min": self.stream_purity_min,
            "enforce_delivered_stream_purity": (
                self.enforce_delivered_stream_purity
            ),
            "coating_min_campaigns_to_resinter": self.coating_min_campaigns_to_resinter,
            "extraction_min_fraction": self.extraction_min_fraction,
            "extraction_min_fraction_by_species": dict(
                self.extraction_min_fraction_by_species
            ),
            "knudsen_max": self.knudsen_max,
            "furnace_T_max_C": self.furnace_T_max_C,
            "cycle_time_max_h": self.cycle_time_max_h,
            "target_species": self.target_species,
            "active_gates": self.active_gates,
            "residual_species_by_target": dict(self.residual_species_by_target),
            "allowable_wall_deposit_kg": dict(self.allowable_wall_deposit_kg),
        }

    def __setstate__(self, state: Mapping[str, Any]) -> None:
        for key, value in state.items():
            object.__setattr__(self, key, value)
        if "enforce_delivered_stream_purity" not in state:
            object.__setattr__(self, "enforce_delivered_stream_purity", False)
        self.__post_init__()

    def __post_init__(self) -> None:
        if not isinstance(self.enforce_delivered_stream_purity, bool):
            raise ValueError("enforce_delivered_stream_purity must be a bool")
        object.__setattr__(
            self,
            "residual_species_by_target",
            MappingProxyType({
                str(target): tuple(str(item) for item in species)
                for target, species in self.residual_species_by_target.items()
            }),
        )
        object.__setattr__(
            self,
            "extraction_min_fraction_by_species",
            MappingProxyType({
                str(species): threshold
                for species, threshold in self.extraction_min_fraction_by_species.items()
            }),
        )
        if self.extraction_min_fraction_by_species:
            missing = sorted(
                str(species)
                for species in self.target_species
                if str(species) not in self.extraction_min_fraction_by_species
            )
            if missing:
                raise ValueError(
                    "extraction_min_fraction_by_species missing thresholds for "
                    f"target_species: {missing}"
                )
        object.__setattr__(
            self,
            "allowable_wall_deposit_kg",
            MappingProxyType(dict(self.allowable_wall_deposit_kg)),
        )
        if not self.target_species:
            raise ValueError("target_species must be non-empty")
        active = tuple(str(gate) for gate in self.active_gates)
        if not active:
            raise ValueError("active_gates must be non-empty")
        unknown = set(active) - set(GATE_ORDER)
        if unknown:
            raise ValueError(f"active_gates contains unknown gates: {sorted(unknown)}")
        object.__setattr__(self, "active_gates", active)
        for threshold in self.thresholds:
            if threshold.value is None:
                raise ValueError(f"{threshold.id} threshold must not be null")
        for key, threshold in self.allowable_wall_deposit_kg.items():
            if len(key) != 2:
                raise ValueError("allowable_wall_deposit_kg keys are (segment, species)")
            if threshold.source not in {
                "literature",
                "materials.yaml",
                "profile",
                "engineering_envelope",
                "code_default",
            }:
                raise ValueError(f"{threshold.id} source is not declared")

    @property
    def thresholds(self) -> tuple[ThresholdSpec, ...]:
        thresholds = [
            self.stream_purity_min,
            self.coating_min_campaigns_to_resinter,
            _d045_upstream_wall_deposit_threshold(),
            self.extraction_min_fraction,
            *tuple(self.extraction_min_fraction_by_species.values()),
            self.knudsen_max,
            self.furnace_T_max_C,
            *tuple(self.allowable_wall_deposit_kg.values()),
        ]
        if "cycle_time" in self.active_gates:
            thresholds.append(self.cycle_time_max_h)
        return tuple(thresholds)

    def threshold_provenance_table(self) -> tuple[tuple[str, str, str], ...]:
        rows = [
            (
                "delivered_stream_purity",
                f"{self.stream_purity_min.id}={self.stream_purity_min.value:g}",
                self.stream_purity_min.source,
            ),
            (
                "coating",
                (
                    f"{self.coating_min_campaigns_to_resinter.id}="
                    f"{self.coating_min_campaigns_to_resinter.value:g}"
                ),
                self.coating_min_campaigns_to_resinter.source,
            ),
            (
                "coating",
                (
                    f"{_d045_upstream_wall_deposit_threshold().id}="
                    f"{MAX_UPSTREAM_WALL_DEPOSIT_FRACTION_PER_CAMPAIGN:g}"
                ),
                "engineering_envelope",
            ),
            (
                "extraction_completeness",
                f"{self.extraction_min_fraction.id}={self.extraction_min_fraction.value:g}",
                self.extraction_min_fraction.source,
            ),
            (
                "knudsen_viscous",
                f"{self.knudsen_max.id}={self.knudsen_max.value:g}",
                self.knudsen_max.source,
            ),
            (
                "furnace_temperature",
                f"{self.furnace_T_max_C.id}={self.furnace_T_max_C.value:g}",
                self.furnace_T_max_C.source,
            ),
        ]
        if "cycle_time" in self.active_gates:
            rows.append((
                "cycle_time",
                f"{self.cycle_time_max_h.id}={self.cycle_time_max_h.value:g}",
                self.cycle_time_max_h.source,
            ))
        for species, threshold in sorted(self.extraction_min_fraction_by_species.items()):
            rows.append((
                "extraction_completeness",
                f"extraction_min_fraction_by_species[{species}]={threshold.value:g}",
                threshold.source,
            ))
        for (segment, species), threshold in sorted(self.allowable_wall_deposit_kg.items()):
            rows.append((
                "coating",
                f"allowable_wall_deposit_kg[{segment}][{species}]={threshold.value:g}",
                threshold.source,
            ))
        return tuple(rows)

    def digest(self) -> str:
        return physics_constraints_digest(self)

    def evaluate(self, trace: Any) -> FeasibilityResult:
        evaluators = {
            "delivered_stream_purity": self.delivered_stream_purity,
            "coating": self.coating,
            "extraction_completeness": self.extraction_completeness,
            "knudsen_viscous": self.knudsen_viscous,
            "furnace_temperature": self.furnace_temperature,
            "cycle_time": self.cycle_time,
        }
        margins = {
            gate: evaluators[gate](trace)
            for gate in GATE_ORDER
            if gate in self.active_gates
        }
        if "delivered_stream_purity" in margins:
            purity_margin = margins["delivered_stream_purity"]
            purity_detail = (
                f"{purity_margin.detail}; reads provisional condenser capture"
            )
            if self.enforce_delivered_stream_purity:
                margins["delivered_stream_purity"] = replace(
                    purity_margin,
                    detail=purity_detail,
                    status_payload={
                        **purity_margin.status_payload,
                        "diagnostic_only": False,
                        "basis": "provisional condenser capture",
                    },
                )
            else:
                margins["delivered_stream_purity"] = replace(
                    purity_margin,
                    feasible=True,
                    detail=purity_detail,
                    status="warning",
                    authoritative=False,
                    output_status="provisional",
                    status_reason=(
                        "provisional condenser capture; diagnostic-only"
                    ),
                    status_payload={
                        **purity_margin.status_payload,
                        "diagnostic_only": True,
                        "basis": "provisional condenser capture",
                    },
                )
        return FeasibilityResult(
            feasible=all(margin.feasible for margin in margins.values()),
            margins=margins,
        )

    def delivered_stream_purity(self, trace: Any) -> GateMargin:
        try:
            snapshots = _required_sequence(trace, "snapshots")
            deltas = _required_sequence(trace, "condensed_by_stage_species_delta")
            if len(deltas) != len(snapshots):
                return _fail_closed(
                    "delivered_stream_purity",
                    self.stream_purity_min,
                    "condensed delta count does not match snapshots",
                )
            totals: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
            for tick in deltas:
                if not isinstance(tick, Mapping):
                    return _fail_closed(
                        "delivered_stream_purity",
                        self.stream_purity_min,
                        "condensed_by_stage_species_delta tick is not a mapping",
                    )
                for key, kg in tick.items():
                    stage, species = _stage_species_key(key)
                    amount = _non_negative_number(kg, "condensed kg")
                    totals[stage][species] += amount
            worst_margin = math.inf
            worst_observed = 1.0
            worst_detail = "no delivered stream"
            for stage, species_kg in sorted(totals.items()):
                total_kg = sum(species_kg.values())
                if total_kg <= _EPS:
                    continue
                accepted = accepted_species_for_stage_number(stage)
                designated_kg = sum(
                    kg for species, kg in species_kg.items() if species in accepted
                )
                purity = designated_kg / total_kg
                margin = purity - self.stream_purity_min.value
                if margin < worst_margin:
                    contaminants = {
                        species: kg
                        for species, kg in species_kg.items()
                        if species not in accepted and kg > _EPS
                    }
                    worst_margin = margin
                    worst_observed = purity
                    worst_detail = (
                        f"stage {stage} purity {purity:.6g}; "
                        f"contaminants={contaminants}"
                    )
            if math.isinf(worst_margin):
                return _margin(
                    "delivered_stream_purity",
                    -self.stream_purity_min.value,
                    self.stream_purity_min,
                    0.0,
                    "zero delivered stream",
                )
            return _margin(
                "delivered_stream_purity",
                worst_margin,
                self.stream_purity_min,
                worst_observed,
                worst_detail,
            )
        except (KeyError, TypeError, ValueError) as exc:
            return _fail_closed("delivered_stream_purity", self.stream_purity_min, str(exc))

    def coating(self, trace: Any) -> GateMargin:
        base_trace = getattr(trace, "trace", None) or trace
        missing_report = object()
        report = getattr(trace, "wall_fouling_report", missing_report)
        if report is missing_report:
            report = getattr(base_trace, "wall_fouling_report", missing_report)
        if report is not missing_report:
            if isinstance(report, Mapping):
                runtime_diagnostics = _coating_runtime_diagnostics(trace)
                report_diagnostics = report.get("coating_diagnostics")
                merged_diagnostics = _merge_coating_diagnostic_lists(
                    report_diagnostics,
                    runtime_diagnostics,
                )
                report = {
                    **report,
                    "coating_diagnostics": merged_diagnostics,
                }
            return self.coating_from_fouling_report(report)
        try:
            snapshots = _required_sequence(base_trace, "snapshots")
            deltas = _required_sequence(
                base_trace,
                "wall_deposit_by_segment_species_delta",
            )
            if len(deltas) != len(snapshots):
                return _fail_closed(
                    "coating",
                    self.coating_min_campaigns_to_resinter,
                    "wall-deposit delta count does not match snapshots",
                )
            by_campaign: dict[tuple[str, str, str], float] = defaultdict(float)
            for snapshot, tick in zip(snapshots, deltas, strict=True):
                if not isinstance(tick, Mapping):
                    return _fail_closed(
                        "coating",
                        self.coating_min_campaigns_to_resinter,
                        "wall_deposit_by_segment_species_delta tick is not a mapping",
                    )
                campaign = _campaign_name(snapshot)
                for key, kg in tick.items():
                    segment, species = _segment_species_key(key)
                    amount = _non_negative_number(kg, "wall deposit kg")
                    by_campaign[(campaign, segment, species)] += amount
            has_wall_deposit = any(
                kg > COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN
                for kg in by_campaign.values()
            )
            authority = _coating_authority_status(base_trace, by_campaign)
            authoritative = _authority_is_authoritative(authority)
            diagnostics = _coating_runtime_diagnostics(trace)
            deposit_records = [
                {
                    "campaign": campaign,
                    "segment": segment,
                    "species": species,
                    "deposit_kg_per_campaign": float(kg),
                }
                for (campaign, segment, species), kg in sorted(by_campaign.items())
                if kg > COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN
            ]
            reasons = _coating_violation_reasons(
                authority=authority,
                deposit_records=deposit_records,
                diagnostics=diagnostics,
            )
            warning_flags = _coating_dew_point_warnings(diagnostics, authority)
            unavailable_reason = _coating_wall_quantity_unavailable(authority)
            common_payload = {
                **authority,
                "constraint_mode": "continuous",
                "coating_positive_deposit_tolerance_kg_per_campaign": (
                    COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN
                ),
                "coating_violation_reasons": reasons,
                "coating_warning_flags": warning_flags,
            }
            if unavailable_reason:
                return GateMargin(
                    gate="coating",
                    feasible=False,
                    margin=-math.inf,
                    threshold=self.coating_min_campaigns_to_resinter,
                    observed=None,
                    detail=f"coating unavailable: {unavailable_reason}",
                    status="unavailable",
                    authoritative=False,
                    output_status=str(
                        authority.get("output_status", "unavailable")
                    ),
                    status_reason=unavailable_reason,
                    status_payload={
                        **common_payload,
                        "coating_verdict": "unavailable",
                        "coating_unavailable_reason": unavailable_reason,
                    },
                )
            zone_by_segment: Mapping[Any, Any] | None = None
            if has_wall_deposit:
                zone_by_segment = getattr(base_trace, "wall_zone_by_segment", None)
                if zone_by_segment is None:
                    return _fail_closed(
                        "coating",
                        self.coating_min_campaigns_to_resinter,
                        "wall_zone_by_segment trace is missing for wall deposit",
                    )
                if not isinstance(zone_by_segment, Mapping):
                    return _fail_closed(
                        "coating",
                        self.coating_min_campaigns_to_resinter,
                        "wall_zone_by_segment trace is not a mapping",
                    )
            worst_margin = math.inf
            worst_observed = math.inf
            worst_detail = "no wall deposit"
            for (campaign, segment, species), kg in sorted(by_campaign.items()):
                if kg <= COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN:
                    continue
                if zone_by_segment is None:
                    return _fail_closed(
                        "coating",
                        self.coating_min_campaigns_to_resinter,
                        "wall_zone_by_segment trace is missing for wall deposit",
                    )
                if segment not in zone_by_segment:
                    return _fail_closed(
                        "coating",
                        self.coating_min_campaigns_to_resinter,
                        f"missing wall zone for segment {segment}",
                    )
                zone = str(zone_by_segment[segment])
                if zone not in WALL_DEPOSIT_ZONE_NAMES:
                    return _fail_closed(
                        "coating",
                        self.coating_min_campaigns_to_resinter,
                        f"unknown wall zone {zone!r} for segment {segment}",
                    )
                limit = self.allowable_wall_deposit_kg.get((segment, species))
                if limit is None:
                    if math.isinf(worst_margin):
                        worst_margin = 0.0
                        worst_observed = math.inf
                        worst_detail = (
                            f"{campaign}/{zone}/{segment}/{species}: "
                            f"deposit={kg:.6g} kg, allowable=unconfigured; "
                            "absolute kg limit unconfigured; "
                            "campaigns_to_resinter=unreported"
                        )
                    continue
                campaigns_to_resinter = limit.value / kg
                campaign_margin = (
                    campaigns_to_resinter
                    - self.coating_min_campaigns_to_resinter.value
                )
                absolute_margin = limit.value - kg
                margin = min(campaign_margin, absolute_margin)
                if margin < worst_margin:
                    worst_margin = margin
                    worst_detail = (
                        f"{campaign}/{zone}/{segment}/{species}: "
                        f"deposit={kg:.6g} kg, "
                        f"allowable={limit.value:.6g} kg, "
                        f"campaigns_to_resinter={campaigns_to_resinter:.6g}"
                    )
                if campaigns_to_resinter < worst_observed:
                    worst_observed = campaigns_to_resinter
            if math.isinf(worst_margin):
                worst_margin = math.inf
            detail = (
                worst_detail
                if worst_detail == "no wall deposit"
                else f"reported-only: {worst_detail}"
            )
            if reasons:
                detail = (
                    "coating constraint violated: "
                    f"{_coating_reason_summary(reasons)}; {detail}"
                )
                if not authoritative:
                    detail = f"non-authoritative prediction: {detail}"
            elif not authoritative:
                detail = (
                    "non-authoritative: grounded coating criterion not enforced; "
                    f"{detail}"
                )
            common_payload["coating_verdict"] = "violated" if reasons else "clear"
            return GateMargin(
                gate="coating",
                feasible=not reasons,
                margin=float(worst_margin),
                threshold=self.coating_min_campaigns_to_resinter,
                observed=float(worst_observed),
                detail=detail,
                status=(
                    "warning"
                    if (warning_flags and not reasons) or not authoritative
                    else "available"
                ),
                authoritative=authoritative,
                output_status=str(authority.get("output_status", "authoritative")),
                status_reason=(
                    ""
                    if authoritative
                    else str(authority.get("message", "non-authoritative coating"))
                ),
                status_payload=common_payload,
            )
        except (KeyError, TypeError, ValueError) as exc:
            return _fail_closed("coating", self.coating_min_campaigns_to_resinter, str(exc))

    def coating_from_fouling_report(
        self,
        report: Any,
    ) -> GateMargin:
        """Classify the runner's worst-segment lifespan verdict.

        Authority controls the label only. Predicted upstream deposition and
        silica exposure remain hard no-coating violations. A dew-point
        finding is a warning on this path as well as the fraction path. A
        refused wall quantity that cannot be bounded is unavailable rather
        than a zero.
        """
        if not isinstance(report, Mapping):
            raise CoatingFeasibilityReportError(
                "wall-fouling report must be a mapping"
            )
        required = (
            "campaigns_to_resinter_total",
            "authoritative_for_resinter",
            "output_status",
            "status_reason",
        )
        missing = tuple(key for key in required if key not in report)
        if missing:
            raise CoatingFeasibilityReportError(
                f"wall-fouling report missing required fields: {missing}"
            )
        authoritative_raw = report["authoritative_for_resinter"]
        if not isinstance(authoritative_raw, bool):
            raise CoatingFeasibilityReportError(
                "wall-fouling authoritative_for_resinter must be bool"
            )
        authoritative = authoritative_raw
        output_status = report["output_status"]
        status_reason = report["status_reason"]
        if not isinstance(output_status, str):
            raise CoatingFeasibilityReportError(
                "wall-fouling output_status must be str"
            )
        if not isinstance(status_reason, str):
            raise CoatingFeasibilityReportError(
                "wall-fouling status_reason must be str"
            )
        constraint_mode = report.get("coating_constraint_mode")
        threshold_is_unqualified = False
        if "resinter_threshold_kg" in report:
            raw_threshold = report["resinter_threshold_kg"]
            if raw_threshold is None:
                threshold_is_unqualified = True
            elif isinstance(raw_threshold, bool) or not isinstance(
                raw_threshold, int | float
            ):
                raise CoatingFeasibilityReportError(
                    "wall-fouling resinter_threshold_kg must be numeric or null"
                )
            else:
                threshold = float(raw_threshold)
                threshold_is_unqualified = (
                    not math.isfinite(threshold) or threshold <= 0.0
                )
        authority_payload = _coating_report_authority(report)
        diagnostics = _coating_runtime_diagnostics(report)
        report_diagnostics = report.get("coating_diagnostics")
        if isinstance(report_diagnostics, Mapping):
            for key in _COATING_DIAGNOSTIC_LIST_KEYS:
                diagnostics[key].extend(
                    _plain_value(report_diagnostics.get(key, ()))
                    if isinstance(report_diagnostics.get(key, ()), (tuple, list))
                    else []
                )
        deposit_records = _coating_report_deposit_records(report)
        physical_fraction_mode = (
            report.get("coating_constraint_mode")
            == "upstream_deposit_fraction"
            or "feedstock_charge_mass_kg" in report
        )
        if physical_fraction_mode:
            return _coating_from_upstream_deposit_fraction_report(
                report=report,
                authority=authority_payload,
                diagnostics=diagnostics,
                deposit_records=deposit_records,
                output_status=output_status,
                status_reason=status_reason,
            )
        raw_wall_rate = report.get("wall_deposit_kg_per_campaign")
        if (
            "wall_deposit_kg_per_campaign" in report
            and raw_wall_rate is None
            and report.get("unqualified_deposition_rate_kg_per_campaign") is None
        ):
            availability = wall_deposit_figure_availability(
                has_positive_deposit=False,
                authority=authority_payload,
                refusal=(
                    report.get("refusal")
                    if isinstance(report.get("refusal"), Mapping)
                    else None
                ),
                measured=False,
            )
            if availability.status == "refused":
                unavailable_reason = (
                    status_reason
                    or str(
                        (availability.refusal or {}).get("reason")
                        or "wall-deposit quantity unavailable"
                    )
                )
                status_payload = {
                    **report,
                    "constraint_mode": "continuous",
                    "coating_verdict": "unavailable",
                    "coating_unavailable_reason": unavailable_reason,
                }
                if threshold_is_unqualified:
                    status_payload.update(
                        coating_constraint_mode="no_unqualified_deposition",
                        coating_constraint_authoritative=False,
                    )
                if availability.refusal is not None:
                    status_payload["wall_deposit_refusal"] = availability.refusal
                return GateMargin(
                    gate="coating",
                    feasible=False,
                    margin=-math.inf,
                    threshold=self.coating_min_campaigns_to_resinter,
                    observed=None,
                    detail=f"coating unavailable: {unavailable_reason}",
                    status="unavailable",
                    authoritative=False,
                    output_status=output_status,
                    status_reason=unavailable_reason,
                    status_payload=status_payload,
                )
        warning_flags = _coating_dew_point_warnings(
            diagnostics,
            authority_payload,
        )
        unavailable_reason = _coating_wall_quantity_unavailable(
            report,
            authority_payload,
            include_coverage_unknown=True,
        )
        if constraint_mode == "no_unqualified_deposition" or threshold_is_unqualified:
            if (
                constraint_mode == "no_unqualified_deposition"
                and report.get("coating_constraint_authoritative") is not True
            ):
                raise CoatingFeasibilityReportError(
                    "no-unqualified-deposition constraint must be authoritative"
                )
            raw_rate = report.get(
                "unqualified_deposition_rate_kg_per_campaign",
                report.get("wall_deposit_kg_per_campaign"),
            )
            if isinstance(raw_rate, bool) or not isinstance(raw_rate, int | float):
                raise CoatingFeasibilityReportError(
                    "unqualified deposition rate must be numeric"
                )
            rate = float(raw_rate)
            if not math.isfinite(rate) or rate < 0.0:
                raise CoatingFeasibilityReportError(
                    "unqualified deposition rate must be finite and non-negative"
                )
            reasons = _coating_violation_reasons(
                authority=authority_payload,
                deposit_records=deposit_records,
                aggregate_deposit_kg=rate,
                diagnostics=diagnostics,
                explicit_reasons=report.get("coating_violation_reasons", ()),
            )
            threshold = ThresholdSpec(
                id="coating_max_unqualified_deposit_kg_per_campaign",
                value=0.0,
                units="kg/campaign",
                source="engineering_envelope",
                source_ref=(
                    "require_coating_gate with no sourced resinter capacity"
                ),
            )
            coating_authoritative = (
                _authority_is_authoritative(authority_payload)
                if "sticking_alpha_authority" in report
                else bool(report.get("authoritative_for_resinter", True))
            )
            constraint_authoritative = (
                report.get("coating_constraint_authoritative") is True
            )
            if unavailable_reason:
                return GateMargin(
                    gate="coating",
                    feasible=False,
                    margin=-math.inf,
                    threshold=threshold,
                    observed=None,
                    detail=f"coating unavailable: {unavailable_reason}",
                    status="unavailable",
                    authoritative=False,
                    output_status=output_status,
                    status_reason=unavailable_reason,
                    status_payload={
                        **report,
                        "coating_constraint_mode": "no_unqualified_deposition",
                        "coating_constraint_authoritative": False,
                        "constraint_mode": "continuous",
                        "coating_verdict": "unavailable",
                        "coating_unavailable_reason": unavailable_reason,
                        "coating_violation_reasons": reasons,
                        "coating_warning_flags": warning_flags,
                        "coating_positive_deposit_tolerance_kg_per_campaign": (
                            COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN
                        ),
                    },
                )
            violated = bool(reasons)
            detail = (
                "coating constraint violated: "
                f"{_coating_reason_summary(reasons)}; "
                f"deposit_rate={rate:.6g} kg/campaign"
                if violated
                else (
                    "no positive upstream wall deposition or coating finding; "
                    f"deposit_rate={rate:.6g} kg/campaign"
                )
            )
            if violated and not coating_authoritative:
                detail = f"non-authoritative prediction: {detail}"
            return GateMargin(
                gate="coating",
                feasible=not violated,
                margin=-rate if math.isfinite(rate) else -math.inf,
                threshold=threshold,
                observed=rate,
                detail=detail,
                status=(
                    "warning"
                    if (warning_flags and not violated) or not coating_authoritative
                    else "available"
                ),
                authoritative=coating_authoritative,
                output_status=output_status,
                status_reason=(
                    ""
                    if coating_authoritative
                    else status_reason
                ),
                status_payload={
                    **report,
                    "coating_constraint_mode": "no_unqualified_deposition",
                    "coating_constraint_authoritative": constraint_authoritative,
                    "constraint_mode": "continuous",
                    "coating_verdict": "violated" if violated else "clear",
                    "coating_violation_reasons": reasons,
                    "coating_warning_flags": warning_flags,
                    "coating_positive_deposit_tolerance_kg_per_campaign": (
                        COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN
                    ),
                },
            )
        observed_field = (
            "campaigns_to_resinter_worst_segment"
            if "campaigns_to_resinter_worst_segment" in report
            else "campaigns_to_resinter_total"
        )
        raw_observed = report[observed_field]
        if isinstance(raw_observed, bool) or not isinstance(raw_observed, int | float):
            raise CoatingFeasibilityReportError(
                f"wall-fouling {observed_field} must be numeric"
            )
        observed = float(raw_observed)
        if math.isnan(observed) or observed < 0.0:
            raise CoatingFeasibilityReportError(
                f"wall-fouling {observed_field} must be non-negative"
            )
        margin = observed - self.coating_min_campaigns_to_resinter.value
        raw_rate = report.get(
            "unqualified_deposition_rate_kg_per_campaign",
            report.get("wall_deposit_kg_per_campaign"),
        )
        aggregate_deposit = None
        if raw_rate is not None:
            if isinstance(raw_rate, bool) or not isinstance(raw_rate, int | float):
                raise CoatingFeasibilityReportError(
                    "wall deposit rate must be numeric"
                )
            aggregate_deposit = float(raw_rate)
            if not math.isfinite(aggregate_deposit) or aggregate_deposit < 0.0:
                raise CoatingFeasibilityReportError(
                    "wall deposit rate must be finite and non-negative"
                )
        reasons = _coating_violation_reasons(
            authority=authority_payload,
            deposit_records=deposit_records,
            aggregate_deposit_kg=aggregate_deposit,
            diagnostics=diagnostics,
            explicit_reasons=report.get("coating_violation_reasons", ()),
        )
        coating_authoritative = (
            _authority_is_authoritative(authority_payload)
            if "sticking_alpha_authority" in report
            else authoritative
        )
        if unavailable_reason:
            return GateMargin(
                gate="coating",
                feasible=False,
                margin=-math.inf,
                threshold=self.coating_min_campaigns_to_resinter,
                observed=None,
                detail=f"coating unavailable: {unavailable_reason}",
                status="unavailable",
                authoritative=False,
                output_status=output_status,
                status_reason=unavailable_reason,
                status_payload={
                    **report,
                    "constraint_mode": "continuous",
                    "coating_verdict": "unavailable",
                    "coating_unavailable_reason": unavailable_reason,
                    "coating_violation_reasons": reasons,
                    "coating_warning_flags": warning_flags,
                    "coating_positive_deposit_tolerance_kg_per_campaign": (
                        COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN
                    ),
                },
            )
        violated = bool(reasons)
        if authoritative:
            detail = (
                f"continuous runner wall-fouling {observed_field}={observed:.6g}; "
                f"minimum={self.coating_min_campaigns_to_resinter.value:.6g}"
            )
        else:
            detail = (
                "non-authoritative: coating feasibility unconstrained; "
                f"output_status={output_status}; "
                f"status_reason={status_reason}"
            )
        if violated:
            detail = (
                "coating constraint violated: "
                f"{_coating_reason_summary(reasons)}; {detail}"
            )
            if not coating_authoritative:
                detail = f"non-authoritative prediction: {detail}"
        return GateMargin(
            gate="coating",
            feasible=not violated,
            margin=margin,
            threshold=self.coating_min_campaigns_to_resinter,
            observed=observed,
            detail=detail,
            status=(
                "warning"
                if (warning_flags and not violated) or not authoritative
                else "available"
            ),
            authoritative=authoritative,
            output_status=output_status,
            status_reason="" if authoritative else status_reason,
            status_payload={
                **report,
                "constraint_mode": "continuous",
                "coating_verdict": "violated" if violated else "clear",
                "coating_violation_reasons": reasons,
                "coating_warning_flags": warning_flags,
                "coating_positive_deposit_tolerance_kg_per_campaign": (
                    COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN
                ),
            },
        )

    def extraction_completeness(self, trace: Any) -> GateMargin:
        try:
            by_target = _extraction_completeness_by_target_for_trace(
                trace,
                self,
            )
            worst_margin = math.inf
            worst_fraction = 1.0
            worst_detail = ""
            worst_threshold = self.extraction_min_fraction
            for target in self.target_species:
                threshold = self._extraction_threshold_for_target(str(target))
                result = by_target[str(target)]
                if result.completeness_fraction is None:
                    detail = _extraction_completeness_fail_closed_detail(result)
                    return _unavailable_gate(
                        "extraction_completeness",
                        threshold,
                        detail,
                    )
                fraction = result.completeness_fraction
                margin = fraction - threshold.value
                if margin < worst_margin:
                    worst_margin = margin
                    worst_fraction = fraction
                    worst_detail = result.detail
                    worst_threshold = threshold
            return _margin(
                "extraction_completeness",
                worst_margin,
                worst_threshold,
                worst_fraction,
                worst_detail,
            )
        except (
            AccountingError,
            KeyError,
            OverflowError,
            TypeError,
            ValueError,
        ) as exc:
            return _unavailable_gate(
                "extraction_completeness",
                self.extraction_min_fraction,
                str(exc),
            )

    def _extraction_threshold_for_target(self, target: str) -> ThresholdSpec:
        return self.extraction_min_fraction_by_species.get(
            str(target),
            self.extraction_min_fraction,
        )

    def knudsen_viscous(self, trace: Any) -> GateMargin:
        try:
            snapshots = _required_sequence(trace, "snapshots")
            if not snapshots:
                return _fail_closed(
                    "knudsen_viscous",
                    self.knudsen_max,
                    "trace has no snapshots",
                )
            worst_margin = math.inf
            worst_kn = 0.0
            worst_detail = ""
            for index, snapshot in enumerate(snapshots):
                summary = getattr(snapshot, "knudsen_regime_summary", None)
                if not isinstance(summary, Mapping) or not summary:
                    return _fail_closed(
                        "knudsen_viscous",
                        self.knudsen_max,
                        f"snapshot {index} missing knudsen_regime_summary",
                    )
                values = _knudsen_segment_values(summary)
                if not values:
                    return _fail_closed(
                        "knudsen_viscous",
                        self.knudsen_max,
                        f"snapshot {index} missing per-segment knudsen diagnostics",
                    )
                for label, kn, regime in values:
                    margin = self.knudsen_max.value - kn
                    if margin < worst_margin:
                        worst_margin = margin
                        worst_kn = kn
                        worst_detail = (
                            f"snapshot {index} {label} Kn={kn:.6g} regime={regime}"
                        )
            result = _margin(
                "knudsen_viscous",
                worst_margin,
                self.knudsen_max,
                worst_kn,
                worst_detail,
            )
            return replace(
                result,
                feasible=True,
                detail=f"continuous transport constraint: {result.detail}",
                status_payload={
                    **dict(result.status_payload),
                    "constraint_mode": "continuous",
                },
            )
        except (KeyError, TypeError, ValueError) as exc:
            return _fail_closed("knudsen_viscous", self.knudsen_max, str(exc))

    def furnace_temperature(self, trace: Any) -> GateMargin:
        try:
            snapshots = _required_sequence(trace, "snapshots")
            if not snapshots:
                return _fail_closed(
                    "furnace_temperature",
                    self.furnace_T_max_C,
                    "trace has no snapshots",
                )
            max_temperature = -math.inf
            max_index = -1
            for index, snapshot in enumerate(snapshots):
                temperature = _finite_number(
                    getattr(snapshot, "temperature_C", None),
                    "temperature_C",
                )
                if temperature > max_temperature:
                    max_temperature = temperature
                    max_index = index
            return _margin(
                "furnace_temperature",
                self.furnace_T_max_C.value - max_temperature,
                self.furnace_T_max_C,
                max_temperature,
                f"snapshot {max_index} temperature_C={max_temperature:.6g}",
            )
        except (KeyError, TypeError, ValueError) as exc:
            return _fail_closed("furnace_temperature", self.furnace_T_max_C, str(exc))

    def cycle_time(self, trace: Any) -> GateMargin:
        try:
            snapshots = _required_sequence(trace, "snapshots")
            if not snapshots:
                return _fail_closed(
                    "cycle_time",
                    self.cycle_time_max_h,
                    "trace has no snapshots",
                )
            max_hour = -math.inf
            max_index = -1
            for index, snapshot in enumerate(snapshots):
                hour = _finite_number(getattr(snapshot, "hour", None), "hour")
                if hour > max_hour:
                    max_hour = hour
                    max_index = index
            return _margin(
                "cycle_time",
                self.cycle_time_max_h.value - max_hour,
                self.cycle_time_max_h,
                max_hour,
                f"snapshot {max_index} hour={max_hour:.6g}",
            )
        except (KeyError, TypeError, ValueError) as exc:
            return _fail_closed("cycle_time", self.cycle_time_max_h, str(exc))


def _extraction_completeness_fail_closed_detail(
    result: TargetExtractionCompleteness,
) -> str:
    if result.reason.startswith("unavailable: "):
        return (
            f"{result.target_species}: "
            f"{result.reason.removeprefix('unavailable: ')}"
        )
    if result.reason.startswith("not-applicable:"):
        return result.reason
    if result.reason == "no target-equivalent mol evidence":
        return result.detail
    if result.reason.startswith("unknown: "):
        return result.reason.removeprefix("unknown: ")
    return result.detail


def extraction_completeness_report(
    trace: Any,
    constraints: PhysicsConstraintSet | None = None,
) -> Mapping[str, Any]:
    active_constraints = constraints or PhysicsConstraintSet()
    targets = tuple(str(target) for target in active_constraints.target_species)
    try:
        by_target = _extraction_completeness_by_target_for_trace(
            trace,
            active_constraints,
            require_residual_species=True,
        )
    except (
        AccountingError,
        AttributeError,
        KeyError,
        OverflowError,
        TypeError,
        ValueError,
    ) as exc:
        target_payloads = {
            target: _extraction_completeness_insufficient_report(
                target,
                active_constraints,
                str(exc),
            )
            for target in targets
        }
        return _extraction_completeness_report_payload(
            "insufficient-evidence",
            "inconclusive",
            target_payloads,
            str(exc),
        )

    target_payloads = {
        target: _extraction_completeness_target_report(
            by_target[target],
            active_constraints,
        )
        for target in targets
    }
    insufficient = tuple(
        payload
        for payload in target_payloads.values()
        if payload["status"] != "reported"
    )
    if insufficient:
        return _extraction_completeness_report_payload(
            "insufficient-evidence",
            "inconclusive",
            target_payloads,
            str(insufficient[0]["reason"]),
        )
    worst_target = min(
        targets,
        key=lambda target: target_payloads[target]["completeness_fraction"],
    )
    return _extraction_completeness_report_payload(
        "reported",
        "reported",
        target_payloads,
        "",
        worst_target=worst_target,
        completeness_fraction=target_payloads[worst_target]["completeness_fraction"],
    )


def _optional_mapping(trace: Any, field_name: str) -> Mapping[Any, Any]:
    value = getattr(trace, field_name, {})
    return value if isinstance(value, Mapping) else {}


def _extraction_completeness_by_target_for_trace(
    trace: Any,
    constraints: PhysicsConstraintSet,
    *,
    require_residual_species: bool = False,
) -> Mapping[str, TargetExtractionCompleteness]:
    unavailable_results = {}
    for target in constraints.target_species:
        target = str(target)
        unavailable = evaporation_quantity_unavailability(trace, target)
        if unavailable:
            unavailable_results[target] = TargetExtractionCompleteness(
                target,
                None,
                None,
                None,
                None,
                f"unavailable: {unavailable['reason']}",
            )
    fraction_rows = getattr(trace, "yield_disposition_fraction_table_rows", None)
    trace_error = str(
        getattr(trace, "yield_disposition_fraction_table_error", "") or ""
    )
    rows = None
    if fraction_rows is not None and not trace_error:
        if isinstance(fraction_rows, (tuple, list)) and all(
            isinstance(row, Mapping) for row in fraction_rows
        ):
            rows = tuple(fraction_rows)
        else:
            trace_error = "yield-disposition fraction-table rows are malformed"
    results = extraction_completeness_by_target(
        tuple(str(target) for target in constraints.target_species),
        constraints.residual_species_by_target,
        _optional_mapping(trace, "product_ledger_kg"),
        _optional_mapping(trace, "terminal_rump_by_species_kg"),
        process_inventory_residual_kg=_optional_mapping(
            trace,
            "process_inventory_residual_kg",
        ),
        yield_disposition_fraction_table_rows=rows,
        require_residual_species=require_residual_species,
    )
    if rows is None:
        table_reason = (
            trace_error or "yield-disposition fraction table is unavailable"
        )
        results = {
            key: replace(
                result,
                completeness_fraction=None,
                reason=(
                    f"unavailable: yield-disposition fraction table unavailable for "
                    f"{key}: {table_reason}"
                    + (
                        f"; captured basis: {result.reason}"
                        if result.completeness_fraction is None and result.reason
                        else ""
                    )
                ),
                captured_basis_completeness_fraction=(
                    result.completeness_fraction
                    if result.completeness_fraction is not None
                    else result.captured_basis_completeness_fraction
                ),
            )
            for key, result in results.items()
        }
    return MappingProxyType({**results, **unavailable_results})


def _extraction_completeness_target_report(
    result: TargetExtractionCompleteness,
    constraints: PhysicsConstraintSet,
) -> Mapping[str, Any]:
    target = str(result.target_species)
    threshold = constraints._extraction_threshold_for_target(target)
    residual_species = _residual_species_for_target(target, constraints)
    has_denominator = result.completeness_fraction is not None
    uses_melt_boundary_basis = result.denominator_basis_source.startswith(
        "yield_disposition_"
    )
    has_melt_boundary_basis = result.left_melt_target_equiv_mol is not None
    allowed_residual = (
        {
            "account": "yield_disposition.fraction_table.destination_mol_atoms",
            "bins": YIELD_DISPOSITION_MELT_BOUNDARY_PARTITION.not_left_melt,
            "fraction": 1.0 - threshold.value,
            "target_equiv_mol": (
                (1.0 - threshold.value) * result.denominator_target_equiv_mol
                if has_denominator
                else None
            ),
        }
        if uses_melt_boundary_basis
        else _allowed_residual_payload(
            residual_species,
            threshold,
            result.denominator_target_equiv_mol if has_denominator else None,
            has_denominator,
        )
    )
    payload: dict[str, Any] = {
        "status": "reported",
        "conclusion": "reported",
        "target_species": target,
        "denominator_account": (
            "yield_disposition.fraction_table.destination_mol_atoms"
            if uses_melt_boundary_basis
            else _extraction_denominator_account()
        ),
        "denominator_basis": (
            "target-equivalent amount in the melt at the start of the evaluated "
            "span, partitioned into left and not-left yield-disposition bins; "
            "cleanup-sequestered amount is reported separately"
            if uses_melt_boundary_basis
            else "target_equivalent_mol"
        ),
        "completeness_basis": (
            "material_that_left_the_melt"
            if uses_melt_boundary_basis
            else None
        ),
        "allowed_residual": allowed_residual,
        "product_bin": target,
        "product_account": "product_ledger_kg",
        "product_target_equiv_mol": (
            result.product_target_equiv_mol if has_denominator else None
        ),
        "terminal_offgas_target_equiv_mol": (
            result.terminal_offgas_target_equiv_mol
            if has_melt_boundary_basis
            else None
        ),
        "wall_deposit_target_equiv_mol": (
            result.wall_deposit_target_equiv_mol
            if has_melt_boundary_basis
            else None
        ),
        "not_left_melt_target_equiv_mol": (
            result.not_left_melt_target_equiv_mol
            if has_melt_boundary_basis
            else None
        ),
        "cleanup_sequestered_target_equiv_mol": (
            result.cleanup_sequestered_target_equiv_mol
            if has_melt_boundary_basis
            else None
        ),
        "left_melt_target_equiv_mol": (
            result.left_melt_target_equiv_mol
            if has_melt_boundary_basis
            else None
        ),
        "captured_basis_completeness_fraction": (
            result.captured_basis_completeness_fraction
        ),
        "captured_basis_label": (
            "provisional product-ledger recovery; excludes terminal offgas and wall deposits"
            if result.captured_basis_completeness_fraction is not None
            else None
        ),
        "gross_product_target_equiv_mol": (
            result.gross_product_target_equiv_mol
            if result.gross_product_target_equiv_mol
            else None
        ),
        "residual_target_equiv_mol": (
            result.residual_target_equiv_mol if has_denominator else None
        ),
        "denominator_target_equiv_mol": (
            result.denominator_target_equiv_mol if has_denominator else None
        ),
        "feedstock_recovered_reagent_target_equiv_mol": (
            result.feedstock_recovered_reagent_target_equiv_mol
            if has_denominator
            else None
        ),
        "credit_line_reagent_target_equiv_mol": (
            result.credit_line_reagent_target_equiv_mol
            if has_denominator
            else None
        ),
        "external_additive_reagent_target_equiv_mol": (
            result.external_additive_reagent_target_equiv_mol
            if has_denominator
            else None
        ),
        "denominator_basis_source": result.denominator_basis_source,
        "completeness_fraction": result.completeness_fraction,
        "reason": "",
        "detail": result.detail,
    }
    if not uses_melt_boundary_basis:
        for key in (
            "terminal_offgas_target_equiv_mol",
            "wall_deposit_target_equiv_mol",
            "not_left_melt_target_equiv_mol",
            "cleanup_sequestered_target_equiv_mol",
            "left_melt_target_equiv_mol",
            "completeness_basis",
        ):
            payload.pop(key, None)
    if result.completeness_fraction is None:
        payload.update({
            "status": "insufficient-evidence",
            "conclusion": "inconclusive",
            "reason": _extraction_completeness_fail_closed_detail(result),
        })
    return MappingProxyType(payload)


def _extraction_completeness_insufficient_report(
    target: str,
    constraints: PhysicsConstraintSet,
    reason: str,
) -> Mapping[str, Any]:
    threshold = constraints._extraction_threshold_for_target(target)
    residual_species = _residual_species_for_target(target, constraints)
    return MappingProxyType({
        "status": "insufficient-evidence",
        "conclusion": "inconclusive",
        "target_species": target,
        "denominator_account": _extraction_denominator_account(),
        "denominator_basis": (
            "target-equivalent amount in the melt at the start of the evaluated "
            "span, partitioned into left and not-left yield-disposition bins; "
            "cleanup-sequestered amount is reported separately"
        ),
        "completeness_basis": "material_that_left_the_melt",
        "allowed_residual": _allowed_residual_payload(
            residual_species,
            threshold,
            None,
            False,
        ),
        "product_bin": target,
        "product_account": "product_ledger_kg",
        "product_target_equiv_mol": None,
        "terminal_offgas_target_equiv_mol": None,
        "wall_deposit_target_equiv_mol": None,
        "not_left_melt_target_equiv_mol": None,
        "cleanup_sequestered_target_equiv_mol": None,
        "left_melt_target_equiv_mol": None,
        "captured_basis_completeness_fraction": None,
        "captured_basis_label": (
            "provisional product-ledger recovery; excludes terminal offgas and wall deposits"
        ),
        "gross_product_target_equiv_mol": None,
        "residual_target_equiv_mol": None,
        "denominator_target_equiv_mol": None,
        "feedstock_recovered_reagent_target_equiv_mol": None,
        "credit_line_reagent_target_equiv_mol": None,
        "external_additive_reagent_target_equiv_mol": None,
        "denominator_basis_source": "product_plus_residual",
        "completeness_fraction": None,
        "reason": reason,
        "detail": f"{target}: {reason}",
    })


def _extraction_completeness_report_payload(
    status: str,
    conclusion: str,
    target_payloads: Mapping[str, Mapping[str, Any]],
    reason: str,
    *,
    worst_target: str | None = None,
    completeness_fraction: float | None = None,
) -> Mapping[str, Any]:
    return MappingProxyType({
        "status": status,
        "conclusion": conclusion,
        "aggregation": "min_all_targets",
        "worst_target_species": worst_target,
        "completeness_fraction": completeness_fraction,
        "reason": reason,
        "targets": MappingProxyType(dict(target_payloads)),
    })


def target_species_yield_report(
    sim: Any,
    *,
    target_species: tuple[str, ...] = E1B_TARGET_SPECIES,
    gate_fraction: float = E1B_TARGET_YIELD_GATE_FRACTION,
) -> Mapping[str, Any]:
    targets = tuple(str(target) for target in target_species)
    try:
        threshold = _finite_number(gate_fraction, "target_species_yield gate_fraction")
        queries = AccountingQueries(sim)
        initial_cleaned_melt = queries.initial_cleaned_melt_kg()
        by_target = target_species_yield_by_initial_cleaned_melt(
            targets,
            initial_cleaned_melt,
            queries,
        )
    except (AccountingError, AttributeError, KeyError, TypeError, ValueError) as exc:
        payloads = {
            target: _target_species_yield_insufficient_payload(target, str(exc))
            for target in targets
        }
        return _target_species_yield_report_payload(
            "insufficient-evidence",
            "inconclusive",
            payloads,
            str(exc),
            threshold=gate_fraction,
        )

    payloads = {
        target: _target_species_yield_payload(by_target[target], threshold)
        for target in targets
    }
    blocked = tuple(
        payload for payload in payloads.values()
        if payload["status"] == "insufficient-evidence"
    )
    if blocked:
        return _target_species_yield_report_payload(
            "insufficient-evidence",
            "inconclusive",
            payloads,
            str(blocked[0]["reason"]),
            threshold=threshold,
        )

    applicable = tuple(
        target for target, payload in payloads.items()
        if payload["applicable"]
    )
    not_applicable = tuple(
        target for target, payload in payloads.items()
        if not payload["applicable"]
    )
    if not applicable:
        return _target_species_yield_report_payload(
            "not-applicable",
            "not-applicable",
            payloads,
            "no applicable target species",
            threshold=threshold,
            applicable=applicable,
            not_applicable=not_applicable,
        )
    worst_target = min(
        applicable,
        key=lambda target: payloads[target]["yield_fraction"],
    )
    return _target_species_yield_report_payload(
        "reported",
        "reported",
        payloads,
        "",
        threshold=threshold,
        applicable=applicable,
        not_applicable=not_applicable,
        worst_target=worst_target,
        worst_yield_fraction=payloads[worst_target]["yield_fraction"],
    )


def _target_species_yield_payload(
    result: TargetSpeciesYield,
    gate_fraction: float,
) -> Mapping[str, Any]:
    has_denominator = result.yield_fraction is not None
    status = "reported"
    if result.reason.startswith("not-applicable:"):
        status = "not-applicable"
    elif result.yield_fraction is None:
        status = "insufficient-evidence"
    return MappingProxyType({
        "status": status,
        "conclusion": "reported" if has_denominator else status,
        "applicable": has_denominator,
        "target_species": result.target_species,
        "denominator_source": result.denominator_source,
        "denominator_basis": "target_equivalent_mol",
        "initial_cleaned_target_equiv_mol": (
            result.initial_cleaned_target_equiv_mol if has_denominator else None
        ),
        "numerator_source": result.numerator_source,
        "provenance_rule": result.provenance_rule,
        "product_account": TARGET_YIELD_NUMERATOR_SOURCE,
        "product_species_kg": MappingProxyType(dict(result.product_species_kg)),
        "exact_product_kg": result.exact_product_kg if has_denominator else None,
        "product_target_equiv_mol": (
            result.product_target_equiv_mol if has_denominator else None
        ),
        "gross_product_target_equiv_mol": (
            result.gross_product_target_equiv_mol if has_denominator else None
        ),
        "excluded_non_feedstock_reagent_target_equiv_mol": (
            result.excluded_non_feedstock_reagent_target_equiv_mol
            if has_denominator
            else None
        ),
        "yield_fraction": result.yield_fraction,
        "yield_pct": (
            result.yield_fraction * 100.0
            if result.yield_fraction is not None
            else None
        ),
        "gate_fraction": gate_fraction,
        "gap_to_gate_fraction": (
            gate_fraction - result.yield_fraction
            if result.yield_fraction is not None
            else None
        ),
        "reason": result.reason,
    })


def _target_species_yield_insufficient_payload(
    target: str,
    reason: str,
) -> Mapping[str, Any]:
    return MappingProxyType({
        "status": "insufficient-evidence",
        "conclusion": "inconclusive",
        "applicable": False,
        "target_species": target,
        "denominator_source": TARGET_YIELD_DENOMINATOR_SOURCE,
        "denominator_basis": "target_equivalent_mol",
        "initial_cleaned_target_equiv_mol": None,
        "numerator_source": TARGET_YIELD_NUMERATOR_SOURCE,
        "provenance_rule": TARGET_YIELD_PROVENANCE_RULE,
        "product_account": TARGET_YIELD_NUMERATOR_SOURCE,
        "product_species_kg": MappingProxyType({}),
        "exact_product_kg": None,
        "product_target_equiv_mol": None,
        "gross_product_target_equiv_mol": None,
        "excluded_non_feedstock_reagent_target_equiv_mol": None,
        "yield_fraction": None,
        "yield_pct": None,
        "gate_fraction": E1B_TARGET_YIELD_GATE_FRACTION,
        "gap_to_gate_fraction": None,
        "reason": reason,
    })


def _target_species_yield_report_payload(
    status: str,
    conclusion: str,
    target_payloads: Mapping[str, Mapping[str, Any]],
    reason: str,
    *,
    threshold: float,
    applicable: tuple[str, ...] = (),
    not_applicable: tuple[str, ...] = (),
    worst_target: str | None = None,
    worst_yield_fraction: float | None = None,
) -> Mapping[str, Any]:
    return MappingProxyType({
        "status": status,
        "conclusion": conclusion,
        "consumer": TARGET_SPECIES_YIELD_CONSUMERS,
        "gate_status": "skipped_pending_physics",
        "gate_fraction": threshold,
        "denominator_source": TARGET_YIELD_DENOMINATOR_SOURCE,
        "numerator_source": TARGET_YIELD_NUMERATOR_SOURCE,
        "provenance_rule": TARGET_YIELD_PROVENANCE_RULE,
        "aggregation": "min_applicable_targets",
        "applicable_target_species": applicable,
        "not_applicable_target_species": not_applicable,
        "worst_target_species": worst_target,
        "worst_yield_fraction": worst_yield_fraction,
        "reason": reason,
        "targets": MappingProxyType(dict(target_payloads)),
    })


def _extraction_denominator_account() -> Mapping[str, str]:
    return MappingProxyType({
        "product": "product_ledger_kg",
        "terminal_offgas": "terminal_offgas_kg",
        "wall_deposit": "wall_deposit_by_segment_species_kg",
        "residual": "terminal_rump_by_species_kg + process_inventory_residual_kg",
    })


def _allowed_residual_payload(
    residual_species: tuple[str, ...],
    threshold: ThresholdSpec,
    denominator_target_equiv_mol: float | None,
    has_denominator: bool,
) -> Mapping[str, Any]:
    allowed_fraction = max(0.0, 1.0 - threshold.value)
    return MappingProxyType({
        "account": "melt_retained_and_redox_buffer_retained",
        "species": residual_species,
        "fraction": allowed_fraction,
        "target_equiv_mol": (
            denominator_target_equiv_mol * allowed_fraction
            if has_denominator
            else None
        ),
    })


def _residual_species_for_target(
    target: str,
    constraints: PhysicsConstraintSet,
) -> tuple[str, ...]:
    return tuple(constraints.residual_species_by_target.get(target, ()))


def _coating_authority_status(
    trace: Any,
    by_campaign: Mapping[tuple[str, str, str], float],
) -> dict[str, Any]:
    by_segment_species: dict[tuple[str, str], float] = defaultdict(float)
    for (_campaign, segment, species), kg in by_campaign.items():
        amount = _non_negative_number(kg, "wall deposit kg")
        by_segment_species[(segment, species)] += amount
    trace_status = getattr(trace, "wall_deposit_sticking_authority", {}) or {}
    return wall_deposit_sticking_authority_status(
        by_segment_species,
        trace_status if isinstance(trace_status, Mapping) else {},
    )


# One list. Every coating path merges and reads these keys. Adding a
# hot-wall qualification to only one call site is how refusals and
# extrapolation records were dropped (b-282).
_HOT_WALL_DIAGNOSTIC_KEYS = (
    "upstream_hot_wall_judgements",
    "upstream_hot_wall_findings",
    "upstream_hot_wall_refusals",
    "upstream_hot_wall_extrapolations",
)
_COATING_DIAGNOSTIC_LIST_KEYS = (
    *_HOT_WALL_DIAGNOSTIC_KEYS,
    "silica_exposed_to_alkali_findings",
)


def _hot_wall_identity(record: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        record.get("segment"),
        record.get("species"),
        record.get("wall_temperature_C"),
    )


def _merge_coating_diagnostic_lists(
    report_diagnostics: Any,
    runtime_diagnostics: Mapping[str, Any],
) -> dict[str, Any]:
    """Overlay every runtime diagnostic list onto a fouling report.

    The report copy keeps keys the runtime collector does not own.
    Hot-wall judgements, refusals, and extrapolations travel with the
    findings; a handwritten subset of keys is what dropped them.
    """

    merged = dict(
        report_diagnostics
        if isinstance(report_diagnostics, Mapping)
        else {}
    )
    for key, runtime_values in runtime_diagnostics.items():
        if not isinstance(runtime_values, (tuple, list)):
            continue
        existing = merged.get(key, ())
        if not isinstance(existing, (tuple, list)):
            existing = ()
        merged[key] = [*existing, *runtime_values]
    return merged


def _coating_runtime_diagnostics(value: Any) -> dict[str, Any]:
    """Collect upstream coating findings without changing PhysicsTrace shape."""

    hot_wall: dict[str, list[Mapping[str, Any]]] = {
        key: [] for key in _HOT_WALL_DIAGNOSTIC_KEYS
    }
    silica_exposed_to_alkali_findings: list[Mapping[str, Any]] = []
    sources: list[Any] = []
    seen: set[int] = set()

    def add_source(source: Any) -> None:
        if source is None or id(source) in seen:
            return
        seen.add(id(source))
        sources.append(source)

    add_source(value)
    for owner in (
        value,
        getattr(value, "trace", None),
        getattr(value, "original_trace", None),
        getattr(value, "simulator", None),
    ):
        if isinstance(owner, Mapping):
            add_source(owner.get("coating_diagnostics"))
            add_source(owner.get("stage3_route_diagnostic"))
        add_source(getattr(owner, "coating_diagnostics", None))
        for attr in ("condensation_model", "_condensation_model"):
            model = getattr(owner, attr, None)
            add_source(getattr(model, "last_cold_spot_diagnostic", None))
            add_source(getattr(model, "cold_spot_history", None))
            add_source(getattr(model, "last_stage3_route_diagnostic", None))
            add_source(getattr(model, "operating_history", None))
        add_source(getattr(owner, "run_metadata", None))

    def append_findings(target: list[Mapping[str, Any]], raw: Any) -> None:
        if isinstance(raw, Mapping):
            target.append(dict(raw))
        elif isinstance(raw, (tuple, list)):
            target.extend(
                dict(item)
                for item in raw
                if isinstance(item, Mapping)
            )

    def take_hot_wall(source: Any) -> None:
        if isinstance(source, Mapping):
            for key in _HOT_WALL_DIAGNOSTIC_KEYS:
                append_findings(hot_wall[key], source.get(key))
        elif isinstance(source, (tuple, list)):
            for item in source:
                take_hot_wall(item)

    for source in sources:
        take_hot_wall(source)
        if isinstance(source, Mapping):
            append_findings(
                silica_exposed_to_alkali_findings,
                source.get("silica_exposed_to_alkali_findings"),
            )
            route = source.get("stage3_route_diagnostic")
            if isinstance(route, Mapping):
                append_findings(
                    silica_exposed_to_alkali_findings,
                    [
                        finding
                        for finding in route.get("findings", ())
                        if isinstance(finding, Mapping)
                        and finding.get("key") == "silica_exposed_to_alkali"
                    ],
                )
                if "silica_exposed_to_alkali" in (route.get("finding_keys") or ()):
                    silica_exposed_to_alkali_findings.append({
                        "key": "silica_exposed_to_alkali",
                    })
            if source.get("key") == "silica_exposed_to_alkali":
                silica_exposed_to_alkali_findings.append(dict(source))
            if source.get("finding_keys") and "silica_exposed_to_alkali" in source.get(
                "finding_keys", ()
            ):
                silica_exposed_to_alkali_findings.append({
                    "key": "silica_exposed_to_alkali",
                })
            if source.get("stage3_route") and source.get("findings"):
                append_findings(
                    silica_exposed_to_alkali_findings,
                    [
                        finding
                        for finding in source.get("findings", ())
                        if isinstance(finding, Mapping)
                        and finding.get("key") == "silica_exposed_to_alkali"
                    ],
                )
        elif isinstance(source, (tuple, list)):
            for item in source:
                if isinstance(item, Mapping):
                    if item.get("key") == "silica_exposed_to_alkali":
                        silica_exposed_to_alkali_findings.append(dict(item))
                    if item.get("stage3_route_diagnostic"):
                        add_source(item["stage3_route_diagnostic"])

    def unique(findings: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        result: list[Mapping[str, Any]] = []
        keys: set[str] = set()
        for finding in findings:
            plain = _plain_value(finding)
            key = repr(plain)
            if key in keys:
                continue
            keys.add(key)
            result.append(plain)
        return result

    collected = {
        key: unique(hot_wall[key]) for key in _HOT_WALL_DIAGNOSTIC_KEYS
    }
    collected["silica_exposed_to_alkali_findings"] = unique(
        silica_exposed_to_alkali_findings
    )
    return collected


def _coating_authority_flags(authority: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "code",
        "output_status",
        "authoritative_for_deposit_mass",
        "authoritative_for_coating",
        "wall_saturation_pressure_extrapolations_by_species",
        "wall_saturation_pressure_refusals_by_species",
        "wall_saturation_pressure_refused_species",
        "out_of_domain_alpha_species",
        "uncertified_alpha_species",
        "codes",
        "not_applicable_carrier_species",
        "not_applicable_by_species",
    )
    return {
        key: _plain_value(authority[key])
        for key in keys
        if key in authority
    }


def _coating_reason_record(
    reason: str,
    *,
    authority: Mapping[str, Any],
    **payload: Any,
) -> dict[str, Any]:
    return {
        "reason": reason,
        **payload,
        "authority": _plain_value(authority),
        "flags": _coating_authority_flags(authority),
    }


def _priced_upstream_deposit_kg(
    certain_deposit_kg: float,
    certain_records: list[Mapping[str, Any]],
    refused_species: tuple[str, ...],
    refused_bounds: Mapping[str, float],
    classified_supply: _ClassifiedWallSupplyWindow,
) -> tuple[float, float]:
    """Measured mass that stays, and the booked mass of refused species.

    Invariant. A refused species is classified once by
    ``_classify_refused_wall_supply_window`` on its integration window.
    The booked run total is normalized by that same window before it is
    added to the per-campaign deposit. Measured kilograms of this
    species are omitted. Product costing removes them with
    ``_replace_species_deposit_with_supply``; the physics classifier is
    shared with that product-booking path.

    An unlabeled record is not attributed to a refused species, so it
    stays. A refused species with no bound is not replaced: its measured
    mass stays, and the caller leaves the upper bound unavailable.
    """

    bounded = {
        species for species in refused_species if species in refused_bounds
    }
    replaced = 0.0
    for record in certain_records:
        species = record.get("species")
        if species is None or str(species) not in bounded:
            continue
        replaced += float(record["deposit_kg_per_campaign"])
    booked_supply = 0.0
    for species in refused_species:
        if species not in refused_bounds:
            continue
        booked_campaigns = classified_supply.booked_kg_per_campaign.get(species)
        if booked_campaigns is None:
            raise CoatingFeasibilityReportError(
                f"classified refused wall supply is missing for {species!r}"
            )
        booked_supply += booked_campaigns
    return certain_deposit_kg - replaced, booked_supply


def _coating_from_upstream_deposit_fraction_report(
    *,
    report: Mapping[str, Any],
    authority: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
    deposit_records: list[Mapping[str, Any]],
    output_status: str,
    status_reason: str,
) -> GateMargin:
    """Apply d-045 to a report with an explicit feedstock charge basis."""

    threshold = _d045_upstream_wall_deposit_threshold()
    charge_mass_kg = _coating_report_feedstock_charge_mass(report)
    limit_kg = threshold.value * charge_mass_kg
    refused_species = _coating_wall_refused_species(report, authority)
    not_applicable_species, not_applicable_by_species = (
        _coating_not_applicable_carriers(authority)
    )
    certain_deposit_kg, certain_records = _coating_certain_upstream_deposit(
        report,
        deposit_records,
    )
    raw_window = report.get(_WALL_SUPPLY_WINDOW_REPORT_KEY)
    if isinstance(raw_window, _WallSupplyWindow):
        supply_window = raw_window
    else:
        # Older runner reports describe one campaign unless they carry an
        # explicit window record. Reconstitute that record before classifying.
        legacy_bounds = _coating_refused_flux_bounds(report, refused_species)
        supply_window = _WallSupplyWindow(
            run_total_kg=legacy_bounds,
            campaigns_elapsed=1.0,
        )
    # The optimizer overlay keeps measured rows (evaluate.py's upstream
    # records). This is the only sum of those rows with the supply.
    # Product costing books the same species through
    # the same run-total cutoff classifier: measured mass of a bounded
    # species is replaced, not added.
    from simulator.optimize.objective import _mass_balance_ledger_resolution_kg

    resolution_kg = _mass_balance_ledger_resolution_kg(charge_mass_kg)
    classified_supply = _classify_refused_wall_supply_window(
        supply_window,
        resolution_kg,
    )
    refused_bounds = classified_supply.upper_bound_kg_per_campaign
    campaigns_elapsed = supply_window.campaigns_elapsed
    priced_certain_kg, booked_supply_kg = _priced_upstream_deposit_kg(
        certain_deposit_kg,
        certain_records,
        refused_species,
        refused_bounds,
        classified_supply,
    )
    warning_flags: list[dict[str, Any]] = []
    violation_reasons: list[dict[str, Any]] = []

    for record in certain_records:
        amount = float(record["deposit_kg_per_campaign"])
        if (
            amount <= limit_kg
            and _coating_quantity_is_flagged(record, authority)
        ):
            warning_flags.append(
                _coating_reason_record(
                    "flagged_upstream_wall_deposit_below_threshold",
                    authority=authority,
                    **{
                        key: _plain_value(record[key])
                        for key in (
                            "campaign",
                            "segment",
                            "species",
                            "zone",
                        )
                        if key in record
                    },
                    deposit_kg_per_campaign=amount,
                    threshold_kg_per_campaign=limit_kg,
                )
            )

    warning_flags.extend(_coating_dew_point_warnings(diagnostics, authority))

    for species in refused_species:
        if species in refused_bounds:
            warning_flags.append(
                _coating_reason_record(
                    "wall_saturation_pressure_refused_bounded",
                    authority=authority,
                    species=species,
                    flux_upper_bound_kg_per_campaign=refused_bounds[species],
                    derivation=(
                        "a refused species' wall deposit cannot exceed its "
                        "total vapour flux reaching the segment in the "
                        "campaign"
                    ),
                )
            )

    for finding in diagnostics.get("silica_exposed_to_alkali_findings", ()):
        violation_reasons.append(
            _coating_reason_record(
                "silica_exposed_to_alkali",
                authority=authority,
                finding=_plain_value(finding),
            )
        )

    for explicit in report.get("coating_violation_reasons", ()):
        if isinstance(explicit, Mapping):
            reason = dict(_plain_value(explicit))
            reason.setdefault("authority", _plain_value(authority))
            reason.setdefault("flags", _coating_authority_flags(authority))
            violation_reasons.append(reason)

    if priced_certain_kg > limit_kg:
        reason_payload = {
            key: _plain_value(certain_records[0][key])
            for key in ("campaign", "segment", "species", "zone")
            if len(certain_records) == 1 and key in certain_records[0]
        }
        violation_reasons.append(
            _coating_reason_record(
                "upstream_wall_deposit_fraction_exceeded",
                authority=authority,
                **reason_payload,
                deposit_kg_per_campaign=priced_certain_kg,
                threshold_kg_per_campaign=limit_kg,
                feedstock_charge_mass_kg=charge_mass_kg,
                deposit_fraction=priced_certain_kg / charge_mass_kg,
            )
        )

    unavailable_reason = _coating_wall_quantity_unavailable(
        report,
        authority,
        include_coverage_unknown=True,
    )
    if unavailable_reason and (
        priced_certain_kg <= limit_kg
        or report.get("wall_quantity_unavailable") is True
        or authority.get("wall_quantity_unavailable") is True
    ):
        return _coating_fraction_unavailable_margin(
            report=report,
            authority=authority,
            threshold=threshold,
            output_status=output_status,
            reason=unavailable_reason,
            violation_reasons=violation_reasons,
            warning_flags=warning_flags,
            refused_species=refused_species,
            not_applicable_species=not_applicable_species,
            not_applicable_by_species=not_applicable_by_species,
            refused_bounds=refused_bounds,
            charge_mass_kg=charge_mass_kg,
            limit_kg=limit_kg,
            certain_deposit_kg=priced_certain_kg,
        )

    missing_refused_bounds = _coating_missing_refused_flux_bounds(
        refused_species,
        refused_bounds,
    )
    # Measured rows of a bounded species were removed above. Adding the
    # raw supply on top of those rows counts the vapour twice: 0.2 kg
    # measured SiO plus a 0.4 kg supply was evaluated as 0.6 kg.
    upper_bound_kg = (
        None
        if missing_refused_bounds
        else priced_certain_kg + booked_supply_kg
    )
    if (
        refused_species
        and priced_certain_kg <= limit_kg
        and upper_bound_kg is not None
        and upper_bound_kg > limit_kg
    ):
        return _coating_fraction_unavailable_margin(
            report=report,
            authority=authority,
            threshold=threshold,
            output_status=output_status,
            reason=(
                "booked upstream deposit "
                f"{upper_bound_kg:.6g} kg/campaign exceeds "
                f"d-045 limit {limit_kg:.6g} kg/campaign; a refused "
                "species' measured deposit is replaced by its supply bound"
            ),
            violation_reasons=violation_reasons,
            warning_flags=warning_flags,
            refused_species=refused_species,
            not_applicable_species=not_applicable_species,
            not_applicable_by_species=not_applicable_by_species,
            refused_bounds=refused_bounds,
            charge_mass_kg=charge_mass_kg,
            limit_kg=limit_kg,
            certain_deposit_kg=priced_certain_kg,
            upper_bound_kg=upper_bound_kg,
        )

    if not violation_reasons and diagnostics.get(
        "silica_exposed_to_alkali_findings"
    ):
        # Keep the hard silica rule explicit if a future reason filter changes
        # the list above; zero deposited mass does not waive this finding.
        violation_reasons.extend(
            _coating_reason_record(
                "silica_exposed_to_alkali",
                authority=authority,
                finding=_plain_value(finding),
            )
            for finding in diagnostics["silica_exposed_to_alkali_findings"]
        )

    unique_warnings = _unique_coating_records(warning_flags)
    unique_reasons = _unique_coating_records(violation_reasons)
    feasible = not unique_reasons
    observed_kg = (
        priced_certain_kg if upper_bound_kg is None else upper_bound_kg
    )
    observed_fraction = observed_kg / charge_mass_kg
    coating_authoritative = _authority_is_authoritative(authority)
    status = "warning" if unique_warnings or not coating_authoritative else "available"
    if unique_reasons:
        detail = (
            "coating constraint violated: "
            f"{_coating_reason_summary(unique_reasons)}; "
        )
        if upper_bound_kg is None:
            detail += (
                f"certain upstream deposit={priced_certain_kg:.6g} "
                "kg/campaign; refused-species flux upper bound unavailable; "
            )
        else:
            detail += f"upstream deposit upper bound={upper_bound_kg:.6g} kg/campaign, "
        detail += f"d-045 limit={limit_kg:.6g} kg/campaign"
    else:
        detail = (
            "d-045 upstream wall-deposit upper bound="
            f"{observed_kg:.6g} kg/campaign <= {limit_kg:.6g} kg/campaign"
        )
    payload = _coating_fraction_payload(
        report=report,
        authority=authority,
        threshold=threshold,
        violation_reasons=unique_reasons,
        warning_flags=unique_warnings,
        refused_species=refused_species,
        not_applicable_species=not_applicable_species,
        not_applicable_by_species=not_applicable_by_species,
        refused_bounds=refused_bounds,
        charge_mass_kg=charge_mass_kg,
        limit_kg=limit_kg,
        certain_deposit_kg=priced_certain_kg,
        upper_bound_kg=upper_bound_kg,
        verdict="violated" if unique_reasons else "clear",
        constraint_authoritative=True,
    )
    return GateMargin(
        gate="coating",
        feasible=feasible,
        margin=threshold.value - observed_fraction,
        threshold=threshold,
        observed=observed_fraction,
        detail=detail,
        status=status,
        authoritative=coating_authoritative,
        output_status=output_status,
        status_reason=(
            ""
            if coating_authoritative
            else status_reason
            if "resinter threshold" not in status_reason.lower()
            else ""
        ),
        status_payload=payload,
    )


def _coating_fraction_unavailable_margin(
    *,
    report: Mapping[str, Any],
    authority: Mapping[str, Any],
    threshold: ThresholdSpec,
    output_status: str,
    reason: str,
    violation_reasons: list[dict[str, Any]],
    warning_flags: list[dict[str, Any]],
    refused_species: tuple[str, ...],
    not_applicable_species: tuple[str, ...],
    not_applicable_by_species: Mapping[str, Any],
    refused_bounds: Mapping[str, float],
    charge_mass_kg: float,
    limit_kg: float,
    certain_deposit_kg: float,
    upper_bound_kg: float | None = None,
) -> GateMargin:
    payload = _coating_fraction_payload(
        report=report,
        authority=authority,
        threshold=threshold,
        violation_reasons=_unique_coating_records(violation_reasons),
        warning_flags=_unique_coating_records(warning_flags),
        refused_species=refused_species,
        not_applicable_species=not_applicable_species,
        not_applicable_by_species=not_applicable_by_species,
        refused_bounds=refused_bounds,
        charge_mass_kg=charge_mass_kg,
        limit_kg=limit_kg,
        certain_deposit_kg=certain_deposit_kg,
        upper_bound_kg=upper_bound_kg,
        verdict="unavailable",
        constraint_authoritative=False,
    )
    payload["coating_unavailable_reason"] = reason
    payload["coating_excluded_species"] = [
        {
            "species": species,
            "status": "unavailable",
            "reason": "wall_saturation_pressure_refused",
        }
        for species in refused_species
    ]
    # A missing flux bound is not an infinite coating cost. d-045 keeps
    # the gate unavailable (feasible stays false, observed stays null)
    # and the refusal stays named. The margin is the certain-deposit
    # slack against the fraction limit, (limit - certain) / charge,
    # which is finite while the certain deposit is inside the limit.
    # -inf is the 1e30 constraint penalty (_constraint_violation_from_margin)
    # and is reserved for a known upper bound that already exceeds the
    # limit: that case has no slack. Booking a partial sum of the other
    # hours is not this margin (d-367).
    if (
        upper_bound_kg is None
        and certain_deposit_kg <= limit_kg
        and charge_mass_kg > 0.0
    ):
        margin = threshold.value - (certain_deposit_kg / charge_mass_kg)
    else:
        margin = -math.inf
    return GateMargin(
        gate="coating",
        feasible=False,
        margin=margin,
        threshold=threshold,
        observed=None,
        detail=f"coating unavailable: {reason}",
        status="unavailable",
        authoritative=False,
        output_status=output_status,
        status_reason=reason,
        status_payload=payload,
    )


def _coating_fraction_payload(
    *,
    report: Mapping[str, Any],
    authority: Mapping[str, Any],
    threshold: ThresholdSpec,
    violation_reasons: list[dict[str, Any]],
    warning_flags: list[dict[str, Any]],
    refused_species: tuple[str, ...],
    not_applicable_species: tuple[str, ...],
    not_applicable_by_species: Mapping[str, Any],
    refused_bounds: Mapping[str, float],
    charge_mass_kg: float,
    limit_kg: float,
    certain_deposit_kg: float,
    upper_bound_kg: float | None,
    verdict: str,
    constraint_authoritative: bool,
) -> dict[str, Any]:
    public_report = {
        key: value
        for key, value in report.items()
        if key != _WALL_SUPPLY_WINDOW_REPORT_KEY
    }
    payload = {
        **_plain_value(public_report),
        "coating_constraint_mode": "upstream_deposit_fraction",
        "coating_constraint_authoritative": constraint_authoritative,
        "constraint_mode": "fraction",
        "coating_threshold_fraction": threshold.value,
        "feedstock_charge_mass_kg": charge_mass_kg,
        "upstream_wall_deposit_limit_kg_per_campaign": limit_kg,
        "certain_upstream_deposit_kg_per_campaign": certain_deposit_kg,
        "upstream_wall_deposit_upper_bound_kg_per_campaign": upper_bound_kg,
        "wall_saturation_pressure_refused_species": list(refused_species),
        "wall_saturation_pressure_refused_flux_upper_bounds_kg_per_campaign": {
            str(species): float(bound)
            for species, bound in refused_bounds.items()
        },
        "not_applicable_carrier_species": list(not_applicable_species),
        "not_applicable_by_species": _plain_value(not_applicable_by_species),
        "coating_violation_reasons": violation_reasons,
        "coating_warning_flags": warning_flags,
        "coating_verdict": verdict,
        "coating_positive_deposit_tolerance_kg_per_campaign": (
            COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN
        ),
    }
    return payload


def _coating_report_feedstock_charge_mass(report: Mapping[str, Any]) -> float:
    raw = report.get("feedstock_charge_mass_kg")
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise CoatingFeasibilityReportError(
            "d-045 coating report requires numeric feedstock_charge_mass_kg"
        )
    mass = float(raw)
    if not math.isfinite(mass) or mass <= 0.0:
        raise CoatingFeasibilityReportError(
            "d-045 feedstock_charge_mass_kg must be finite and positive"
        )
    return mass


def _coating_certain_upstream_deposit(
    report: Mapping[str, Any],
    records: list[Mapping[str, Any]],
) -> tuple[float, list[dict[str, Any]]]:
    total = 0.0
    certain_records: list[dict[str, Any]] = []
    upstream_record_seen = False
    scoped_record_seen = False
    for record in records:
        scope = str(record.get("scope", "upstream"))
        if scope in {"designated_condenser", "condenser"}:
            scoped_record_seen = True
            continue
        upstream_record_seen = True
        raw_amount = record.get(
            "deposit_kg_per_campaign",
            record.get("wall_deposit_kg_per_campaign", record.get("kg", 0.0)),
        )
        try:
            amount = float(raw_amount)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(amount) or amount < 0.0:
            raise CoatingFeasibilityReportError(
                "upstream wall deposit must be finite and non-negative"
            )
        if amount <= COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN:
            continue
        item = dict(_plain_value(record))
        item["deposit_kg_per_campaign"] = amount
        certain_records.append(item)
        total += amount
    if not upstream_record_seen and not scoped_record_seen:
        raw_aggregate = report.get(
            "unqualified_deposition_rate_kg_per_campaign",
            report.get("wall_deposit_kg_per_campaign"),
        )
        if raw_aggregate is not None:
            try:
                aggregate = float(raw_aggregate)
            except (TypeError, ValueError) as exc:
                raise CoatingFeasibilityReportError(
                    "wall deposit rate must be numeric"
                ) from exc
            if not math.isfinite(aggregate) or aggregate < 0.0:
                raise CoatingFeasibilityReportError(
                    "wall deposit rate must be finite and non-negative"
                )
            if aggregate > COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN:
                certain_records.append({
                    "scope": "upstream",
                    "deposit_kg_per_campaign": aggregate,
                })
                total = aggregate
    return total, certain_records


def _coating_quantity_is_flagged(
    record: Mapping[str, Any],
    authority: Mapping[str, Any],
) -> bool:
    if any(
        bool(record.get(key))
        for key in ("flagged", "extrapolated", "is_extrapolated", "status_bearing")
    ):
        return True
    code = " ".join(
        str(authority.get(key, ""))
        for key in ("code", "output_status", "status", "message")
    ).lower()
    return "extrapolat" in code or "status_bearing" in code


def _coating_wall_refused_species(
    *sources: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    species: set[str] = set()
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        raw_species = source.get("wall_saturation_pressure_refused_species", ())
        if isinstance(raw_species, str):
            species.add(raw_species)
        elif isinstance(raw_species, (tuple, list, set, frozenset)):
            species.update(str(item) for item in raw_species if str(item))
        raw_map = source.get("wall_saturation_pressure_refusals_by_species")
        if isinstance(raw_map, Mapping):
            species.update(str(item) for item in raw_map if str(item))
    return tuple(sorted(species))


def _coating_not_applicable_carriers(
    authority: Mapping[str, Any],
) -> tuple[tuple[str, ...], dict[str, Any]]:
    species: set[str] = set()
    records: dict[str, Any] = {}
    raw_species = authority.get("not_applicable_carrier_species", ())
    if isinstance(raw_species, str):
        species.add(raw_species)
    elif isinstance(raw_species, (tuple, list, set, frozenset)):
        species.update(str(item) for item in raw_species if str(item))
    raw_records = authority.get("not_applicable_by_species")
    if isinstance(raw_records, Mapping):
        for item, record in raw_records.items():
            key = str(item)
            species.add(key)
            records[key] = _plain_value(record)
    carriers = authority.get("vapour_carrier_authority_by_species", {})
    if isinstance(carriers, Mapping):
        for item, record in carriers.items():
            if not isinstance(record, Mapping) or not _carrier_is_inapplicable(record):
                continue
            key = str(item)
            species.add(key)
            records[key] = _plain_value(record)
    return tuple(sorted(species)), records


def _coating_refused_flux_bounds(
    report: Mapping[str, Any],
    refused_species: tuple[str, ...],
) -> dict[str, float]:
    raw = report.get(
        "wall_saturation_pressure_refused_flux_upper_bounds_kg_per_campaign",
        report.get("refused_species_flux_upper_bounds_kg_per_campaign", {}),
    )
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        entries = raw.items()
    elif isinstance(raw, (tuple, list)):
        entries = (
            (
                item.get("species"),
                item.get(
                    "flux_upper_bound_kg_per_campaign",
                    item.get("bound_kg_per_campaign"),
                ),
            )
            for item in raw
            if isinstance(item, Mapping)
        )
    else:
        raise CoatingFeasibilityReportError(
            "refused species flux upper bounds must be a mapping or records"
        )
    bounds: dict[str, float] = {}
    for raw_species, raw_bound in entries:
        if raw_species is None:
            continue
        if isinstance(raw_bound, bool) or not isinstance(raw_bound, int | float):
            raise CoatingFeasibilityReportError(
                f"refused species flux bound for {raw_species!r} must be numeric"
            )
        bound = float(raw_bound)
        if not math.isfinite(bound) or bound < 0.0:
            raise CoatingFeasibilityReportError(
                f"refused species flux bound for {raw_species!r} must be finite "
                "and non-negative"
            )
        bounds[str(raw_species)] = bound
    # Mutation guard: refused flux bounds must not default to zero.
    return bounds


def _coating_missing_refused_flux_bounds(
    refused_species: tuple[str, ...],
    refused_bounds: Mapping[str, float],
) -> tuple[str, ...]:
    """Return refused species without a finite flux upper bound."""
    return tuple(species for species in refused_species if species not in refused_bounds)


def _unique_coating_records(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        key = repr(_plain_value(record))
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique


def _coating_extrapolated_wall_flag(
    source: Mapping[str, Any],
    extrapolations: Any,
    authority: Mapping[str, Any],
) -> dict[str, Any]:
    return _coating_reason_record(
        "upstream_hot_wall_extrapolated",
        authority=authority,
        species=source.get("species"),
        segment=source.get("segment"),
        wall_temperature_C=source.get("wall_temperature_C"),
        partial_pressure_pa=source.get("partial_pressure_pa"),
        saturation_pressure_pa=source.get("saturation_pressure_pa"),
        extrapolations=_plain_value(extrapolations),
    )


def _coating_dew_point_warnings(
    diagnostics: Mapping[str, Any] | None,
    authority: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Publish every hot-wall judgement on every coating path.

    A supersaturation finding stays a warning, not a veto. A typed
    refusal and an out-of-domain best estimate are the same kind of
    visible qualification: they reach this gate and they do not reprice
    deposit mass. Judgements are the chokepoint. Findings, refusals,
    and extrapolation lists are the projections of those judgements,
    kept so a payload that only has one of them is not dropped.
    """

    diagnostics = diagnostics or {}
    records: list[dict[str, Any]] = []
    covered: set[tuple[Any, ...]] = set()

    def _records(key: str) -> tuple[Any, ...] | list[Any]:
        raw = diagnostics.get(key, ())
        if isinstance(raw, (tuple, list)):
            return raw
        return ()

    def _extrapolations_of(source: Mapping[str, Any]) -> Any:
        raw = source.get("extrapolations")
        if raw is None:
            raw = source.get("records")
        return raw if isinstance(raw, (tuple, list)) else ()

    for judgement in _records("upstream_hot_wall_judgements"):
        if not isinstance(judgement, Mapping):
            continue
        covered.add(_hot_wall_identity(judgement))
        status = judgement.get("status")
        plain = _plain_value(judgement)
        if status == "violation":
            records.append(
                _coating_reason_record(
                    "upstream_hot_wall_supersaturation",
                    authority=authority,
                    finding=plain,
                )
            )
        elif status == "refused":
            records.append(
                _coating_reason_record(
                    "upstream_hot_wall_refused",
                    authority=authority,
                    finding=plain,
                )
            )
        extrapolations = _extrapolations_of(judgement)
        if extrapolations:
            records.append(
                _coating_extrapolated_wall_flag(
                    judgement,
                    extrapolations,
                    authority,
                )
            )

    for finding in _records("upstream_hot_wall_findings"):
        if not isinstance(finding, Mapping):
            continue
        if _hot_wall_identity(finding) in covered:
            continue
        covered.add(_hot_wall_identity(finding))
        records.append(
            _coating_reason_record(
                "upstream_hot_wall_supersaturation",
                authority=authority,
                finding=_plain_value(finding),
            )
        )
        extrapolations = _extrapolations_of(finding)
        if extrapolations:
            records.append(
                _coating_extrapolated_wall_flag(
                    finding,
                    extrapolations,
                    authority,
                )
            )

    for refusal in _records("upstream_hot_wall_refusals"):
        if not isinstance(refusal, Mapping):
            continue
        if _hot_wall_identity(refusal) in covered:
            continue
        covered.add(_hot_wall_identity(refusal))
        records.append(
            _coating_reason_record(
                "upstream_hot_wall_refused",
                authority=authority,
                finding=_plain_value(refusal),
            )
        )

    for publication in _records("upstream_hot_wall_extrapolations"):
        if not isinstance(publication, Mapping):
            continue
        if _hot_wall_identity(publication) in covered:
            continue
        extrapolations = _extrapolations_of(publication)
        if not extrapolations:
            continue
        records.append(
            _coating_extrapolated_wall_flag(
                publication,
                extrapolations,
                authority,
            )
        )
    return _unique_coating_records(records)


def _coating_violation_reasons(
    *,
    authority: Mapping[str, Any],
    deposit_records: Any = (),
    aggregate_deposit_kg: float | None = None,
    diagnostics: Mapping[str, Any] | None = None,
    explicit_reasons: Any = (),
) -> list[dict[str, Any]]:
    reasons: list[dict[str, Any]] = []
    positive_record_seen = False
    scoped_record_seen = False
    for record in deposit_records if isinstance(deposit_records, (tuple, list)) else ():
        if not isinstance(record, Mapping):
            continue
        if record.get("scope") in {"designated_condenser", "condenser"}:
            scoped_record_seen = True
            continue
        raw_amount = record.get(
            "deposit_kg_per_campaign",
            record.get("wall_deposit_kg_per_campaign", record.get("kg", 0.0)),
        )
        try:
            amount = float(raw_amount)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(amount) or amount <= COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN:
            continue
        positive_record_seen = True
        payload = {
            key: _plain_value(record[key])
            for key in ("campaign", "segment", "species", "zone")
            if key in record
        }
        payload["deposit_kg_per_campaign"] = amount
        reasons.append(
            _coating_reason_record(
                "positive_upstream_wall_deposit",
                authority=authority,
                **payload,
            )
        )
    if (
        not positive_record_seen
        and not scoped_record_seen
        and aggregate_deposit_kg is not None
        and math.isfinite(aggregate_deposit_kg)
        and aggregate_deposit_kg > COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN
    ):
        reasons.append(
            _coating_reason_record(
                "positive_upstream_wall_deposit",
                authority=authority,
                deposit_kg_per_campaign=float(aggregate_deposit_kg),
            )
        )
    diagnostics = diagnostics or {}
    for finding in diagnostics.get("silica_exposed_to_alkali_findings", ()):
        reasons.append(
            _coating_reason_record(
                "silica_exposed_to_alkali",
                authority=authority,
                finding=_plain_value(finding),
            )
        )
    for explicit in explicit_reasons if isinstance(explicit_reasons, (tuple, list)) else ():
        if isinstance(explicit, Mapping):
            record = dict(_plain_value(explicit))
            record.setdefault("authority", _plain_value(authority))
            record.setdefault("flags", _coating_authority_flags(authority))
            reasons.append(record)
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for reason in reasons:
        key = repr(_plain_value(reason))
        if key in seen:
            continue
        seen.add(key)
        unique.append(reason)
    return unique


def _coating_reason_summary(reasons: list[Mapping[str, Any]]) -> str:
    labels: list[str] = []
    for reason in reasons:
        label = str(reason.get("reason", "coating violation"))
        segment = reason.get("segment")
        species = reason.get("species")
        if segment or species:
            label += f" ({segment or '?'}/{species or '?'})"
        labels.append(label)
    return "; ".join(labels)


def _coating_wall_quantity_unavailable(
    *sources: Mapping[str, Any] | None,
    include_coverage_unknown: bool = False,
) -> str:
    """One owner: a wall quantity is unavailable only when it cannot be bounded.

    A refused species that still has a vapour-flux bound is not unavailable
    here. The fraction gate compares that bound with the d-045 limit.
    Extrapolation prose and a carrier '=refused' token are not a refusal.
    """

    mappings = [source for source in sources if isinstance(source, Mapping)]
    refused_species = _coating_wall_refused_species(*mappings)
    bounds: dict[str, float] = {}
    if refused_species:
        for source in mappings:
            bounds.update(_coating_refused_flux_bounds(source, refused_species))

    # A null bucket is unknown mass for its recorded species. Only a finite
    # bound on that refused species replaces the unknown; an unrefused or
    # unbounded null leaves the total upper bound unknown. A complete bound
    # equal to the d-045 limit is clear because the gate violates only above it.
    for source in mappings:
        if source.get("wall_quantity_unavailable") is True:
            reason = str(
                source.get("status_reason")
                or source.get("message")
                or "wall quantity unavailable"
            )
            raw_unavailable_species = source.get(
                "wall_quantity_unavailable_species"
            )
            if isinstance(raw_unavailable_species, str):
                unavailable_species = (raw_unavailable_species,)
            elif isinstance(
                raw_unavailable_species,
                (tuple, list, set, frozenset),
            ):
                unavailable_species = tuple(
                    str(item) for item in raw_unavailable_species if str(item)
                )
            else:
                return reason
            if not unavailable_species:
                return reason
            if any(
                species not in refused_species or species not in bounds
                for species in unavailable_species
            ):
                return reason
        code = str(source.get("code", "")).lower()
        if (
            "saturation_pressure_refused" in code
            or "wall_saturation_pressure_refused" in code
        ) and not refused_species:
            return str(
                source.get("status_reason")
                or source.get("message")
                or "wall saturation pressure was refused"
            )
        if include_coverage_unknown and code == "wall_deposit_coverage_unknown":
            return str(source.get("message") or "wall deposit quantity is unavailable")
        lowered_reason = str(source.get("status_reason", "")).lower()
        if "wall saturation unavailable" in lowered_reason:
            return str(source.get("status_reason"))
        if (
            "wall saturation pressure" in lowered_reason
            and "refus" in lowered_reason
            and not refused_species
        ):
            return str(source.get("status_reason"))
        if (
            str(source.get("status", "")) == "unavailable"
            or source.get("output_status") == "unavailable"
        ):
            return str(
                source.get("status_reason")
                or source.get("message")
                or "wall quantity unavailable"
            )
    if not refused_species:
        return ""
    missing = _coating_missing_refused_flux_bounds(refused_species, bounds)
    if not missing:
        return ""
    for source in mappings:
        reason = str(source.get("status_reason") or "")
        lowered = reason.lower()
        if "wall saturation" in lowered and "refus" in lowered:
            return reason
    return (
        "wall saturation pressure refused for "
        + ", ".join(missing)
        + "; vapour flux upper bound unavailable"
    )


def _coating_report_authority(report: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = report.get("sticking_alpha_authority")
    if isinstance(nested, Mapping):
        return nested
    return report


def _coating_report_deposit_records(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = report.get("upstream_wall_deposit_records", ())
    if not isinstance(raw, (tuple, list)):
        return []
    return [dict(item) for item in raw if isinstance(item, Mapping)]


def _authority_is_authoritative(payload: Mapping[str, Any]) -> bool:
    for key in (
        "authoritative_for_coating",
        "authoritative_for_deposit_mass",
        "authoritative",
    ):
        if key in payload:
            return bool(payload[key])
    return not _payload_has_deposited_species(payload)


def _payload_has_deposited_species(payload: Mapping[str, Any]) -> bool:
    raw = payload.get("deposited_species")
    if isinstance(raw, str):
        return bool(raw)
    if isinstance(raw, (list, tuple)):
        return bool(raw)
    return False


def _plain_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _plain_value(value)
        for key, value in payload.items()
    }


def _plain_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _plain_value(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(_plain_value(item) for item in value)
    if isinstance(value, list):
        return [_plain_value(item) for item in value]
    return value


def _margin(
    gate: str,
    margin: float,
    threshold: ThresholdSpec,
    observed: float,
    detail: str,
) -> GateMargin:
    return GateMargin(
        gate=gate,
        feasible=margin >= -threshold.tolerance,
        margin=float(margin),
        threshold=threshold,
        observed=float(observed),
        detail=detail,
    )


def physics_constraints_digest(constraints: Any | None = None) -> str:
    """Stable digest for feasibility constraints used in eval cache keys."""

    if constraints is None:
        constraints = PhysicsConstraintSet()
    payload: dict[str, Any] = {}
    if isinstance(constraints, PhysicsConstraintSet):
        payload.update({
            "target_species": constraints.target_species,
            "active_gates": constraints.active_gates,
            "enforce_delivered_stream_purity": (
                constraints.enforce_delivered_stream_purity
            ),
            "residual_species_by_target": dict(constraints.residual_species_by_target),
            "thresholds": tuple(
                _threshold_payload(threshold)
                for threshold in constraints.thresholds
            ),
            "allowable_wall_deposit_kg": tuple(
                {
                    "segment": segment,
                    "species": species,
                    "threshold": _threshold_payload(threshold),
                }
                for (segment, species), threshold
                in sorted(constraints.allowable_wall_deposit_kg.items())
            ),
        })
    canonical = canonical_json_dumps(normalize_canonical_value(payload)).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _threshold_payload(threshold: ThresholdSpec) -> Mapping[str, Any]:
    return {
        "id": threshold.id,
        "value": threshold.value,
        "units": threshold.units,
        "source": threshold.source,
        "source_ref": threshold.source_ref,
        "tolerance": threshold.tolerance,
    }


def _fail_closed(gate: str, threshold: ThresholdSpec, detail: str) -> GateMargin:
    return GateMargin(
        gate=gate,
        feasible=False,
        margin=-math.inf,
        threshold=threshold,
        observed=math.nan,
        detail=f"fail-closed: {detail}",
    )


def _unavailable_gate(gate: str, threshold: ThresholdSpec, reason: str) -> GateMargin:
    return replace(
        _fail_closed(gate, threshold, reason),
        observed=None,
        status="unavailable",
        authoritative=False,
        output_status="unavailable",
        status_reason=reason,
        status_payload={"reason": reason},
    )


def _not_applicable(gate: str, threshold: ThresholdSpec, detail: str) -> GateMargin:
    return GateMargin(
        gate=gate,
        feasible=False,
        margin=-math.inf,
        threshold=threshold,
        observed=math.inf,
        detail=detail,
    )


def _required_sequence(trace: Any, field_name: str) -> tuple[Any, ...]:
    value = getattr(trace, field_name, None)
    if value is None:
        raise KeyError(f"trace missing {field_name}")
    if not isinstance(value, (tuple, list)):
        raise TypeError(f"{field_name} must be a sequence")
    return tuple(value)


def _stage_species_key(key: Any) -> tuple[int, str]:
    if not isinstance(key, tuple) or len(key) != 2:
        raise TypeError("stage/species key must be a 2-tuple")
    stage, species = key
    return int(stage), str(species)


def _segment_species_key(key: Any) -> tuple[str, str]:
    if not isinstance(key, tuple) or len(key) != 2:
        raise TypeError("segment/species key must be a 2-tuple")
    segment, species = key
    return str(segment), str(species)


def _campaign_name(snapshot: Any) -> str:
    campaign = getattr(snapshot, "campaign", None)
    if campaign is None:
        raise KeyError("snapshot missing campaign")
    return str(getattr(campaign, "name", campaign))


def _knudsen_segment_values(summary: Mapping[Any, Any]) -> list[tuple[str, float, str]]:
    values: list[tuple[str, float, str]] = []
    segments = summary.get("segments")
    if not segments:
        return values
    if not isinstance(segments, (tuple, list)):
        raise TypeError("knudsen segments must be a sequence")
    for segment in segments:
        if not isinstance(segment, Mapping):
            raise TypeError("knudsen segment must be a mapping")
        name = str(segment.get("name", "segment"))
        if "knudsen_number" not in segment:
            raise KeyError(f"knudsen segment {name} missing knudsen_number")
        if "regime" not in segment:
            raise KeyError(f"knudsen segment {name} missing regime")
        values.append((
            name,
            _finite_number(segment["knudsen_number"], f"{name}.knudsen_number"),
            str(segment["regime"]).strip().lower(),
        ))
    return values


def _non_negative_number(value: Any, name: str) -> float:
    amount = _finite_number(value, name)
    if amount < -_EPS:
        raise ValueError(f"{name} must be non-negative")
    return max(0.0, amount)


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be numeric") from exc
    if not math.isfinite(amount):
        raise ValueError(f"{name} must be finite")
    return amount
