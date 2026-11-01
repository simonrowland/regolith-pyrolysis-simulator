"""Shared extraction-completeness math."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from simulator.accounting.exceptions import AccountingError
from simulator.accounting.formulas import (
    ATOMIC_WEIGHTS_G_PER_MOL,
    parse_formula,
    resolve_species_formula,
)
from simulator.account_ids import (
    METAL_BOTTOM_POOL_ACCOUNT,
    METAL_FLOAT_LAYER_ACCOUNT,
    SPENT_REDUCTANT_RESIDUE_ACCOUNT,
)
from simulator.accounting.queries import (
    TERMINAL_RUMP_REFRACTORY_OXIDES,
    TERMINAL_RUMP_SILICATE_RESIDUAL,
    TERMINAL_RUMP_UNEXTRACTED_METALS,
)
from simulator.state import MOLAR_MASS
from simulator.vapour_rail.instrumentation import (
    VAPOUR_CARRIER_AUTHORITY_MISSING,
    VAPOUR_CARRIER_AUTHORITY_PROVEN_ZERO,
    VAPOUR_CARRIER_AUTHORITY_REFUSED,
    VAPOUR_CARRIER_AUTHORITY_STATUS_BEARING,
    vapour_carrier_authority_status,
)

_EPS = 1.0e-12
_WALL_EPS = 1.0e-12
_REQUIRED_CONTRACT_FIELDS = (
    "contract_id",
    "mechanism",
    "denominator_source",
    "provenance_rule",
    "mid_run_vs_terminal",
    "aggregation",
)
_VAPOR_TERMINAL_OFFGAS_ACCOUNT = "terminal.offgas"
_VAPOR_PRODUCT_ACCOUNTS = (
    "process.condensation_train",
    "process.overhead_gas",
    _VAPOR_TERMINAL_OFFGAS_ACCOUNT,
    "terminal.chromium_condensed_oxide_stored",
)
_VAPOR_RESIDUAL_ACCOUNTS = (
    "process.cleaned_melt",
    "terminal.slag",
)
_VAPOR_WALL_ACCOUNT = "process.wall_deposit_segment_*"
_VAPOR_PROVENANCE_RULE = "narrow_account_feedstock_clean"
_EVAPORATION_SOURCE_ACCOUNTS = (
    "process.cleaned_melt",
    SPENT_REDUCTANT_RESIDUE_ACCOUNT,
)
# The bound sums cumulative withdrawals and current balances over the source
# accounts. Transfers between them may be counted twice, so it is a
# conservative upper bound, not an estimate.
_CONSERVATIVE_LEDGER_UPPER_BOUND_BASIS = "conservative_ledger_bound"
TARGET_YIELD_PRODUCT_ACCOUNTS = (
    _VAPOR_TERMINAL_OFFGAS_ACCOUNT,
    "terminal.stage0_salt_phase",
    "terminal.stage0_chloride_salt_phase",
    "terminal.stage0_sulfide_matte",
    "terminal.drain_tap_material",
    "terminal.chromium_condensed_oxide_stored",
    "process.metal_phase",
    METAL_BOTTOM_POOL_ACCOUNT,
    METAL_FLOAT_LAYER_ACCOUNT,
    "process.condensation_train",
    "process.overhead_gas",
)
TARGET_YIELD_DENOMINATOR_SOURCE = "initial_process.cleaned_melt"
TARGET_YIELD_NUMERATOR_SOURCE = "narrow_product_accounts"
TARGET_YIELD_PROVENANCE_RULE = (
    "feedstock_clean_excluding_unspent_reagents_credit_lines_and_external_additives"
)
_TARGET_YIELD_DENOMINATOR_ACCOUNT = "process.cleaned_melt"

METAL_PRODUCT_SPECIES: tuple[str, ...] = (
    "Na", "K", "Fe", "Mg", "Si", "Ti", "Al", "Ca",
    "Cr", "Mn", "Ni", "Co",
)
O2_PRODUCT_SPECIES: tuple[str, ...] = ("O2",)
PURE_SILICA_GLASS_SPECIES: tuple[str, ...] = ("SiO", "SiO2")

_PRODUCT_CLASS_TARGETS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "metals_plus_O2": (*METAL_PRODUCT_SPECIES, *O2_PRODUCT_SPECIES),
    "ingots_metals": METAL_PRODUCT_SPECIES,
    "oxygen": O2_PRODUCT_SPECIES,
    "pure_silica_glass": PURE_SILICA_GLASS_SPECIES,
    "glass": PURE_SILICA_GLASS_SPECIES,
    "industrial_mixed_glass": METAL_PRODUCT_SPECIES,
    "refractory_ceramic_rump": tuple(dict.fromkeys((
        *METAL_PRODUCT_SPECIES,
        *TERMINAL_RUMP_REFRACTORY_OXIDES,
        *TERMINAL_RUMP_SILICATE_RESIDUAL,
        *TERMINAL_RUMP_UNEXTRACTED_METALS,
    ))),
    "captured_volatiles": METAL_PRODUCT_SPECIES,
})


def product_class_target_species(
    class_name: str | None = None,
    *,
    early_tap_mode: bool = False,
) -> tuple[str, ...]:
    """Return declared product targets for one class or all classes."""
    if class_name is None:
        targets = _PRODUCT_CLASS_TARGETS.values()
    elif class_name == "glass" and early_tap_mode:
        return tuple(dict.fromkeys((
            *PURE_SILICA_GLASS_SPECIES,
            *METAL_PRODUCT_SPECIES,
        )))
    else:
        try:
            return _PRODUCT_CLASS_TARGETS[str(class_name)]
        except KeyError as exc:
            raise ValueError(f"unknown product class {class_name!r}") from exc
    return tuple(dict.fromkeys(
        species for class_targets in targets for species in class_targets
    ))

DEFAULT_RESIDUAL_SPECIES_BY_TARGET: Mapping[str, tuple[str, ...]] = MappingProxyType({
    # VR-11: residual maps stay product-executable only. Refusal-only vapour
    # rows keep source/rump coverage via the channel contract, not this table.

    "SiO": ("SiO2", "SiO"),
    "Fe": ("FeO", "Fe"),
    "CrO2": ("Cr2O3", "CrO2", "Cr"),
    "Mg": ("MgO", "Mg"),
    "Na": ("Na2O", "Na"),
    "K": ("K2O", "K"),
})


@dataclass(frozen=True)
class TargetExtractionCompleteness:
    target_species: str
    completeness_fraction: float | None
    # Core mol fields are Optional: unknown/missing is None, never a silent 0.0
    # "zero product" claim. Explicit 0.0 remains a proven-zero product.
    product_target_equiv_mol: float | None
    residual_target_equiv_mol: float | None
    denominator_target_equiv_mol: float | None
    reason: str = ""
    wall_deposit_target_equiv_mol: float = 0.0
    reagent_target_equiv_mol: float = 0.0
    gross_product_target_equiv_mol: float = 0.0
    contract_id: str = ""
    feedstock_recovered_reagent_target_equiv_mol: float = 0.0
    credit_line_reagent_target_equiv_mol: float = 0.0
    external_additive_reagent_target_equiv_mol: float = 0.0
    denominator_basis_source: str = "product_plus_residual"

    @property
    def detail(self) -> str:
        if self.completeness_fraction is None:
            return f"{self.target_species}: {self.reason}"
        return (
            f"{self.target_species}: "
            f"product_target_equiv_mol={self.product_target_equiv_mol:.6g}, "
            f"residual_target_equiv_mol={self.residual_target_equiv_mol:.6g}, "
            f"denominator_target_equiv_mol={self.denominator_target_equiv_mol:.6g}"
        )


@dataclass(frozen=True)
class AggregateExtractionCompleteness:
    completeness_fraction: float | None
    worst_target_species: str | None
    reason: str
    aggregation: str = "min_all_targets"


class CompletionContractBlocked(AccountingError):
    """Raised when a completion contract cannot compute clean provenance."""


@dataclass(frozen=True)
class CompletionContract:
    contract_id: str
    campaign: str
    mechanism: str
    target_species: str | None
    target_element: str | None
    semantic_target_kind: str | None
    product_accounts: tuple[str, ...]
    residual_accounts: tuple[str, ...]
    residual_species: tuple[str, ...]
    wall_account: str | None
    element_map: Mapping[str, tuple[str, ...]]
    denominator_source: str
    provenance_rule: str
    mid_run_vs_terminal: str
    aggregation: str
    deferred: bool = False
    deferred_reason: str = ""
    stage: str | None = None

    @property
    def target_key(self) -> str:
        if self.target_species:
            return self.target_species
        if self.semantic_target_kind:
            return self.semantic_target_kind
        return self.contract_id

    @property
    def element(self) -> str:
        if self.target_element:
            return self.target_element
        if self.target_species:
            return _target_element(self.target_species)
        raise CompletionContractBlocked(
            f"{self.contract_id}: no target_element for semantic target"
        )

    @property
    def allowed_species(self) -> tuple[str, ...]:
        values = self.element_map.get(self.element, ())
        return tuple(dict.fromkeys((
            *(str(species) for species in values),
            self.element,
        )))


@dataclass(frozen=True)
class ReagentProvenanceTargetEquivalent:
    feedstock_recovered_mol: float = 0.0
    credit_line_mol: float = 0.0
    external_additive_mol: float = 0.0


@dataclass(frozen=True)
class TargetSpeciesYield:
    target_species: str
    yield_fraction: float | None
    product_target_equiv_mol: float
    initial_cleaned_target_equiv_mol: float
    reason: str = ""
    product_species_kg: Mapping[str, float] = field(default_factory=dict)
    exact_product_kg: float = 0.0
    gross_product_target_equiv_mol: float = 0.0
    excluded_non_feedstock_reagent_target_equiv_mol: float = 0.0
    denominator_source: str = TARGET_YIELD_DENOMINATOR_SOURCE
    numerator_source: str = TARGET_YIELD_NUMERATOR_SOURCE
    provenance_rule: str = TARGET_YIELD_PROVENANCE_RULE


def evaporation_quantity_unavailability(
    sim: Any,
    target_species: str | tuple[str, ...] | None,
    *,
    snapshots: Any = None,
) -> dict[str, Any] | None:
    """Return a typed refusal when missing transport overlaps a quantity."""
    matches = evaporation_quantity_unavailabilities(
        sim, target_species, snapshots=snapshots
    )
    return matches[0] if matches else None


def evaporation_quantity_unavailabilities(
    sim: Any,
    target_species: str | tuple[str, ...] | None,
    *,
    snapshots: Any = None,
) -> list[dict[str, Any]]:
    """Return unavailable carrier contributions that overlap a quantity."""

    unavailable, _estimated = _evaporation_quantity_authority_contributions(
        sim, target_species, snapshots=snapshots
    )
    return unavailable


def _evaporation_quantity_authority_contributions(
    sim: Any,
    target_species: str | tuple[str, ...] | None,
    *,
    snapshots: Any = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate withheld carrier mass from positive status-bearing flux."""

    unavailable_by_species: dict[str, dict[str, Any]] = {}
    estimated_by_species_account: dict[tuple[str, str], dict[str, Any]] = {}

    def record_unavailable(
        species: Any,
        record: Any,
        *,
        refusal_type: Any,
        reason: Any,
        parent_oxide: Any = None,
    ) -> None:
        species_id = str(species)
        extra = record.get("extra") if isinstance(record, Mapping) else None
        extra = extra if isinstance(extra, Mapping) else {}
        parent = (
            parent_oxide
            or record.get("parent_oxide")
            or extra.get("parent_oxide")
        )
        unavailable_by_species.setdefault(species_id, {
            "refusal_type": str(refusal_type or "VapourCarrierRefusal"),
            "reason": str(reason or "carrier input is unavailable"),
            "parent_oxide": str(parent) if parent else "",
            "account": str(
                record.get("account") or record.get("source_account")
                or extra.get("account") or "process.cleaned_melt"
            ),
        })

    def record_missing_transport(species: Any, record: Any) -> None:
        if not isinstance(record, Mapping):
            return
        extra = record.get("extra")
        extra = extra if isinstance(extra, Mapping) else {}
        if (record.get("policy") or extra.get("policy")) != (
            "fail_loud_missing_transport_parameters"
        ):
            return
        if (record.get("status") or extra.get("status")) != "unavailable":
            return
        refusal_type = (
            record.get("refusal_type")
            or record.get("refusal_code")
            or extra.get("refusal_type")
        )
        if not refusal_type:
            return
        reason = (
            record.get("reason")
            or record.get("detail")
            or extra.get("reason")
            or "transport parameters are unavailable"
        )
        parent_oxide = record.get("parent_oxide") or extra.get("parent_oxide")
        record_unavailable(
            species,
            record,
            refusal_type=refusal_type,
            reason=reason,
            parent_oxide=parent_oxide,
        )

    def has_positive_flux(flux: Any, species: Any) -> bool:
        rates = getattr(flux, "species_kg_hr", {}) or {}
        if not isinstance(rates, Mapping):
            return False
        try:
            rate = float(rates.get(str(species), 0.0))
        except (TypeError, ValueError):
            return False
        return math.isfinite(rate) and rate > 0.0

    def has_positive_capture(snapshot: Any, species: Any) -> bool:
        # Persisted condensate is positive output even if its snapshot omits
        # the hourly species-flux map.
        captures = getattr(snapshot, "condensed_by_stage_species_delta", {}) or {}
        if not isinstance(captures, Mapping):
            return False
        for key, amount in captures.items():
            if not (
                isinstance(key, tuple)
                and len(key) == 2
                and str(key[1]) == str(species)
            ):
                continue
            try:
                kg = float(amount)
            except (TypeError, ValueError):
                continue
            if math.isfinite(kg) and kg > 0.0:
                return True
        return False

    def record_estimate(species: Any, record: Mapping[str, Any]) -> None:
        species_id = str(species)
        extra = record.get("extra")
        extra = extra if isinstance(extra, Mapping) else {}
        account = str(
            record.get("source_account") or record.get("account")
            or "process.cleaned_melt"
        )
        key = (species_id, account)
        verdict = str(record.get("verdict_status") or "").lower()
        authority = str(
            record.get("authority_level")
            or record.get("authority")
            or (
                "extrapolated"
                if "extrapolat" in verdict
                else VAPOUR_CARRIER_AUTHORITY_STATUS_BEARING
            )
        )
        if key in estimated_by_species_account:
            existing_authority = estimated_by_species_account[key]["authority"]
            if existing_authority == "extrapolated" or authority != "extrapolated":
                return
        estimated_by_species_account[key] = {
            "species": species_id,
            "account": account,
            "authority": authority,
            "reason": str(
                record.get("reason")
                or record.get("detail")
                or extra.get("reason")
                or extra.get("activity_reason")
                or record.get("verdict_status")
                or "carrier evidence is status-bearing"
            ),
        }

    diagnostic = getattr(sim, "_last_evaporation_flux_diagnostic", {}) or {}
    missing = diagnostic.get("missing_transport_parameters", {})
    if isinstance(missing, Mapping):
        for species, record in missing.items():
            record_missing_transport(species, record)
    if snapshots is None:
        snapshots = getattr(getattr(sim, "record", None), "snapshots", None)
    if snapshots is None:
        snapshots = getattr(sim, "snapshots", ())
    for snapshot in snapshots or ():
        flux = getattr(snapshot, "evap_flux", None)
        channels = getattr(flux, "carrier_authority_by_species", {}) or {}
        if isinstance(channels, Mapping):
            for species, record in channels.items():
                if not isinstance(record, Mapping):
                    continue
                extra = record.get("extra")
                extra = extra if isinstance(extra, Mapping) else {}
                policy = record.get("policy") or extra.get("policy")
                if policy == "fail_loud_missing_transport_parameters":
                    record_missing_transport(species, record)
                    continue
                status = vapour_carrier_authority_status(
                    record, expected_species_id=str(species)
                )
                if status == VAPOUR_CARRIER_AUTHORITY_STATUS_BEARING:
                    if has_positive_flux(flux, species):
                        record_estimate(species, record)
                    continue
                if status == VAPOUR_CARRIER_AUTHORITY_MISSING:
                    record_unavailable(
                        species,
                        record,
                        refusal_type="MissingVapourCarrierInput",
                        reason=(
                            record.get("reason") or record.get("detail")
                            or extra.get("reason") or "carrier input is missing"
                        ),
                    )
                    continue
                if (
                    status in {
                        VAPOUR_CARRIER_AUTHORITY_REFUSED,
                        VAPOUR_CARRIER_AUTHORITY_PROVEN_ZERO,
                    }
                    and (
                        has_positive_flux(flux, species)
                        or has_positive_capture(snapshot, species)
                    )
                ):
                    pressure = record.get("pressure")
                    pressure = pressure if isinstance(pressure, Mapping) else {}
                    carrier_flux = record.get("flux")
                    carrier_flux = (
                        carrier_flux if isinstance(carrier_flux, Mapping) else {}
                    )
                    refusal_type = (
                        record.get("refusal_type")
                        or record.get("refusal_code")
                        or pressure.get("code")
                        or carrier_flux.get("code")
                        or "VapourCarrierRefusal"
                    )
                    record_unavailable(
                        species,
                        record,
                        refusal_type=refusal_type,
                        reason=(
                            record.get("reason") or record.get("detail")
                            or pressure.get("detail") or carrier_flux.get("detail")
                            or extra.get("reason")
                            or "carrier authority refused positive species output"
                        ),
                    )

    target_elements: set[str] | None = None
    registry = getattr(sim, "species_formula_registry", {})
    if target_species is not None:
        targets = (
            (target_species,)
            if isinstance(target_species, str)
            else tuple(target_species)
        )
        target_elements = set()
        for target in targets:
            try:
                atoms = set(
                    resolve_species_formula(str(target), registry).elements
                )
            except (AccountingError, ValueError):
                continue
            target_elements.update(atoms if len(atoms) == 1 else atoms - {"O"})

    def matches_target(species: str, parent_oxide: str) -> bool:
        if target_elements is None:
            return True
        try:
            affected = set(resolve_species_formula(species, registry).elements)
        except (AccountingError, ValueError):
            return False
        if parent_oxide:
            try:
                affected.update(
                    resolve_species_formula(parent_oxide, registry).elements
                )
            except (AccountingError, ValueError):
                pass
        return bool(target_elements & affected)

    unavailable = [
        {
            "status": "unavailable",
            "authority": "unavailable",
            "band": None,
            "reason": (
                f"{species}: {record['refusal_type']}: {record['reason']}"
            ),
            "species": species,
            "parent_oxide": record["parent_oxide"],
            "account": record["account"],
        }
        for species, record in sorted(unavailable_by_species.items())
        if matches_target(species, record["parent_oxide"])
    ]
    estimates = [
        record
        for (species, _account), record in sorted(
            estimated_by_species_account.items()
        )
        if matches_target(species, "")
    ]
    return unavailable, estimates


def product_class_availability(
    sim: Any,
    target_species: str | tuple[str, ...],
    available_parts_kg: Mapping[str, Any],
    *,
    has_computed_part: bool,
    snapshots: Any = None,
) -> dict[str, Any]:
    """Return computed mass with unavailable and estimated carrier evidence."""
    refusals, estimated_contributions = (
        _evaporation_quantity_authority_contributions(
            sim, target_species, snapshots=snapshots
        )
    )
    parts = (
        {
            str(name): float(value)
            for name, value in available_parts_kg.items()
            if value is not None
        }
        if has_computed_part
        else {}
    )
    total_kg = sum(parts.values())
    excluded: list[dict[str, Any]] = []
    if refusals:
        for refusal in refusals:
            upper_bound_kg = _refused_parent_element_upper_bound_kg(
                sim, refusal
            )
            excluded_item = {
                "species": str(refusal.get("species") or "unknown"),
                "account": str(refusal.get("account") or "unknown"),
                "reason": str(refusal.get("reason") or "transport refusal"),
                "upper_bound_kg": (
                    upper_bound_kg
                    if upper_bound_kg is not None
                    else "unbounded"
                ),
            }
            if upper_bound_kg is not None:
                excluded_item["upper_bound_basis"] = (
                    _CONSERVATIVE_LEDGER_UPPER_BOUND_BASIS
                )
            excluded.append(excluded_item)
    status = (
        "available" if not refusals
        else "partial" if has_computed_part
        else "unavailable"
    )
    return {
        "class_total_kg": (
            total_kg if has_computed_part or not refusals else None
        ),
        "status": status,
        "excluded": excluded,
        "estimated_contributions": estimated_contributions,
        "reason": "; ".join(item["reason"] for item in excluded),
        "flag": (
            None if status == "available" else {
                **{
                    key: value
                    for key, value in (refusals[0] if refusals else {}).items()
                    if not key.startswith("_")
                },
                "status": status,
                "authority": "unavailable",
                "reason": "; ".join(item["reason"] for item in excluded),
                "excluded": excluded,
                **({"total_basis": "lower_bound"} if status == "partial" else {}),
            }
        ),
    }


def _refused_parent_element_upper_bound_kg(
    sim: Any,
    refusal: Mapping[str, Any],
) -> float | None:
    """Bound the refused element using complete cumulative ledger flows."""
    ledger = getattr(sim, "atom_ledger", None)
    atom_moles_by_account = getattr(ledger, "atom_moles_by_account", None)
    gross_flows = getattr(ledger, "gross_account_flows", None)
    if not callable(atom_moles_by_account) or not callable(gross_flows):
        return None

    parent_species = refusal.get("parent_oxide") or refusal.get("species")
    try:
        formula = resolve_species_formula(
            str(parent_species), getattr(sim, "species_formula_registry", {})
        )
        parent_elements = tuple(
            element for element in formula.elements if element != "O"
        )
        if len(parent_elements) != 1:
            return None
        element = parent_elements[0]
        atomic_weight = ATOMIC_WEIGHTS_G_PER_MOL.get(element)
        if atomic_weight is None:
            return None

        flows = gross_flows()
        if not isinstance(flows, Mapping):
            return None
        withdrawals = flows.get("withdrawals")
        if not isinstance(withdrawals, Mapping):
            return None
        attributed_by_account = withdrawals.get(
            "material_origin_atom_moles_by_account"
        )
        unattributed_by_account = withdrawals.get(
            "origin_unattributed_atom_moles_by_account"
        )
        if not isinstance(attributed_by_account, Mapping) or not isinstance(
            unattributed_by_account, Mapping
        ):
            return None

        total_atom_mol = 0.0
        for account in _EVAPORATION_SOURCE_ACCOUNTS:
            current = atom_moles_by_account(account)
            attributed_account = attributed_by_account.get(account, {})
            unattributed_account = unattributed_by_account.get(account, {})
            if not all(isinstance(value, Mapping) for value in (
                current,
                attributed_account,
                unattributed_account,
            )):
                return None
            attributed_element = attributed_account.get(element, {})
            if not isinstance(attributed_element, Mapping):
                return None
            values = [
                *attributed_element.values(),
                unattributed_account.get(element, 0.0),
                current.get(element, 0.0),
            ]
            numeric_values = [float(value) for value in values]
            if any(
                not math.isfinite(value) or value < 0.0
                for value in numeric_values
            ):
                return None
            total_atom_mol += math.fsum(numeric_values)
    except (AccountingError, KeyError, TypeError, ValueError):
        return None

    # For each source account, cumulative gross withdrawals plus its current
    # parent-element balance equals its initial balance plus every credited
    # atom. That covers every possible inventory during the run, including
    # later cleaned-melt additions and spent reductant; transfers between the
    # two accounts can be counted twice, so this is an upper bound, not an
    # estimate.
    upper_bound_kg = total_atom_mol * float(atomic_weight) / 1000.0
    if not math.isfinite(upper_bound_kg) or upper_bound_kg <= 0.0:
        return None
    return upper_bound_kg


def extraction_completeness_by_target(
    target_species: tuple[str, ...],
    residual_species_by_target: Mapping[str, tuple[str, ...]],
    product_ledger_kg: Mapping[str, Any],
    terminal_rump_kg: Mapping[str, Any],
    *,
    process_inventory_residual_kg: Mapping[str, Any] | None = None,
    require_residual_species: bool = False,
) -> dict[str, TargetExtractionCompleteness]:
    residual_map = {
        str(target): tuple(str(species) for species in residuals)
        for target, residuals in residual_species_by_target.items()
    }
    products = {str(species): kg for species, kg in product_ledger_kg.items()}
    rump = {str(species): kg for species, kg in terminal_rump_kg.items()}
    for species, kg in (process_inventory_residual_kg or {}).items():
        key = str(species)
        rump[key] = float(rump.get(key, 0.0) or 0.0) + float(kg)
    results: dict[str, TargetExtractionCompleteness] = {}
    for raw_target in target_species:
        target = str(raw_target)
        if require_residual_species and target not in residual_map:
            results[target] = TargetExtractionCompleteness(
                target,
                None,
                0.0,
                0.0,
                0.0,
                "unknown: no residual species map for target",
            )
            continue
        try:
            product_species = tuple(dict.fromkeys((
                target,
                _target_element(target),
                *residual_map.get(target, ()),
            )))
            product_mol = sum(
                _target_equivalent_mol(
                    target,
                    species,
                    products.get(species, 0.0),
                )
                for species in product_species
            )
            residual_mol = 0.0
            for residual in residual_map.get(target, (target,)):
                residual_mol += _target_equivalent_mol(
                    target,
                    residual,
                    rump.get(residual, 0.0),
                )
            denom = product_mol + residual_mol
            if denom <= _EPS:
                results[target] = TargetExtractionCompleteness(
                    target,
                    None,
                    product_mol,
                    residual_mol,
                    denom,
                    f"not-applicable: zero input basis for {target}",
                )
                continue
            results[target] = TargetExtractionCompleteness(
                target,
                product_mol / denom,
                product_mol,
                residual_mol,
                denom,
            )
        except (AccountingError, KeyError, TypeError, ValueError) as exc:
            results[target] = TargetExtractionCompleteness(
                target,
                None,
                0.0,
                0.0,
                0.0,
                f"unknown: {exc}",
            )
    return results


def target_species_yield_by_initial_cleaned_melt(
    target_species: tuple[str, ...],
    initial_cleaned_melt_kg: Mapping[str, Any],
    queries: Any,
) -> dict[str, TargetSpeciesYield]:
    initial = {
        str(species): kg
        for species, kg in initial_cleaned_melt_kg.items()
    }
    product_kg = _species_kg_by_accounts(queries, TARGET_YIELD_PRODUCT_ACCOUNTS)
    results: dict[str, TargetSpeciesYield] = {}
    for raw_target in target_species:
        target = str(raw_target)
        try:
            element = _target_element(target)
            denominator_mol = _target_yield_denominator_element_mol(
                element,
                initial,
                queries,
            )
            product_species_kg = _target_relevant_species_kg(element, product_kg)
            gross_product_mol = _target_relevant_element_mol(
                element,
                product_species_kg,
            )
            excluded_mol = _strict_non_feedstock_reagent_element_mol(
                element,
                queries,
                accounts=TARGET_YIELD_PRODUCT_ACCOUNTS,
                gross_product_target_equiv_mol=gross_product_mol,
            )
            product_mol = max(0.0, gross_product_mol - min(
                gross_product_mol,
                excluded_mol,
            ))
            if denominator_mol <= _EPS:
                results[target] = TargetSpeciesYield(
                    target,
                    None,
                    product_mol,
                    denominator_mol,
                    f"not-applicable: zero initial process.cleaned_melt basis for {target}",
                    product_species_kg=MappingProxyType(product_species_kg),
                    exact_product_kg=float(product_species_kg.get(target, 0.0)),
                    gross_product_target_equiv_mol=gross_product_mol,
                    excluded_non_feedstock_reagent_target_equiv_mol=excluded_mol,
                )
                continue
            unavailable = evaporation_quantity_unavailability(
                getattr(queries, "sim", None),
                target,
            )
            if unavailable:
                results[target] = TargetSpeciesYield(
                    target,
                    None,
                    0.0,
                    denominator_mol,
                    f"unavailable: {unavailable['reason']}",
                )
                continue
            results[target] = TargetSpeciesYield(
                target,
                product_mol / denominator_mol,
                product_mol,
                denominator_mol,
                product_species_kg=MappingProxyType(product_species_kg),
                exact_product_kg=float(product_species_kg.get(target, 0.0)),
                gross_product_target_equiv_mol=gross_product_mol,
                excluded_non_feedstock_reagent_target_equiv_mol=excluded_mol,
            )
        except (AccountingError, KeyError, TypeError, ValueError) as exc:
            results[target] = TargetSpeciesYield(
                target,
                None,
                0.0,
                0.0,
                f"blocked: {exc}",
            )
    return results


def aggregate_extraction_completeness(
    by_target: Mapping[str, TargetExtractionCompleteness | None],
    target_species: tuple[str, ...],
) -> AggregateExtractionCompleteness:
    """Aggregate explicit gated targets; any n/a target makes the step n/a."""

    if target_species is None:
        raise ValueError(
            "target_species is required; do not infer extraction targets from by_target"
        )
    targets = tuple(str(target) for target in target_species)
    if not targets:
        raise ValueError("target_species must be non-empty")

    worst_target: str | None = None
    worst_fraction = math.inf
    for target in targets:
        result = by_target.get(target)
        if result is None:
            return AggregateExtractionCompleteness(
                None,
                target,
                f"{target}: unknown: no result",
            )
        fraction = result.completeness_fraction
        if fraction is None:
            return AggregateExtractionCompleteness(
                None,
                target,
                f"{target}: {result.reason or 'unknown completeness'}",
            )
        if fraction < worst_fraction:
            worst_fraction = fraction
            worst_target = target

    return AggregateExtractionCompleteness(
        worst_fraction,
        worst_target,
        "min completeness across all targets",
    )


def extraction_completeness_pct(
    target_species: tuple[str, ...],
    residual_species_by_target: Mapping[str, tuple[str, ...]],
    product_ledger_kg: Mapping[str, Any],
    terminal_rump_kg: Mapping[str, Any],
) -> float:
    """Return the worst target completeness fraction across target species."""

    results = extraction_completeness_by_target(
        target_species,
        residual_species_by_target,
        product_ledger_kg,
        terminal_rump_kg,
    )
    aggregate = aggregate_extraction_completeness(results, target_species)
    if aggregate.completeness_fraction is None:
        raise ValueError(aggregate.reason)
    return aggregate.completeness_fraction


def completion_contracts_from_setpoints(
    setpoints: Mapping[str, Any],
) -> tuple[CompletionContract, ...]:
    raw_root = setpoints.get("completion_contracts", {})
    if not isinstance(raw_root, Mapping):
        raise ValueError("completion_contracts must be a mapping")
    raw_steps = raw_root.get("gated_steps", {})
    if not isinstance(raw_steps, Mapping):
        raise ValueError("completion_contracts.gated_steps must be a mapping")

    contracts: list[CompletionContract] = []
    for campaign, raw_step in raw_steps.items():
        if not isinstance(raw_step, Mapping):
            raise ValueError(f"{campaign}: completion contract step must map")
        contracts.extend(_contracts_from_list(
            str(campaign),
            None,
            raw_step.get("contracts", []),
        ))
        stage_contracts = raw_step.get("stage_contracts", {})
        if stage_contracts is None:
            stage_contracts = {}
        if not isinstance(stage_contracts, Mapping):
            raise ValueError(
                f"{campaign}.stage_contracts must be a mapping"
            )
        for stage, raw_stage in stage_contracts.items():
            if not isinstance(raw_stage, Mapping):
                raise ValueError(
                    f"{campaign}.{stage}: stage contract must map"
                )
            contracts.extend(_contracts_from_list(
                str(campaign),
                str(stage),
                raw_stage.get("contracts", []),
            ))
    _validate_unique_contract_ids(contracts)
    _validate_unique_contract_targets(contracts)
    return tuple(contracts)


def completion_contracts_for_campaign(
    setpoints: Mapping[str, Any],
    campaign: str,
) -> tuple[CompletionContract, ...]:
    return tuple(
        contract
        for contract in completion_contracts_from_setpoints(setpoints)
        if contract.campaign == campaign
    )


def validate_completion_contract_coverage(
    setpoints: Mapping[str, Any],
) -> None:
    campaigns = setpoints.get("campaigns", {})
    if not isinstance(campaigns, Mapping):
        raise ValueError("campaigns must be a mapping")
    contracts = completion_contracts_from_setpoints(setpoints)
    by_step: dict[tuple[str, str | None], list[CompletionContract]] = {}
    for contract in contracts:
        by_step.setdefault((contract.campaign, contract.stage), []).append(
            contract
        )
        _validate_contract(contract)

    errors: list[str] = []
    for campaign, cfg in campaigns.items():
        if not isinstance(cfg, Mapping):
            continue
        campaign_name = str(campaign)
        stages = cfg.get("stages")
        if isinstance(stages, list):
            for stage in stages:
                if not isinstance(stage, Mapping):
                    continue
                stage_name = str(stage.get("name", ""))
                if not stage_name:
                    errors.append(f"{campaign_name}: staged step has no name")
                    continue
                _check_step_targets(
                    errors,
                    campaign_name,
                    stage_name,
                    _as_targets(stage.get("target_species")),
                    by_step,
                )
        else:
            targets = _as_targets(cfg.get("target_species"))
            if targets:
                _check_step_targets(
                    errors,
                    campaign_name,
                    None,
                    targets,
                    by_step,
                )
            elif _has_endpoint(cfg):
                step_contracts = by_step.get((campaign_name, None), ())
                if not step_contracts:
                    errors.append(
                        f"{campaign_name}: endpoint campaign has no contract"
                    )

    if errors:
        raise ValueError("; ".join(errors))


def vapor_contract_completeness(
    contract: CompletionContract,
    queries: Any,
) -> TargetExtractionCompleteness:
    if contract.deferred:
        return TargetExtractionCompleteness(
            contract.target_key,
            None,
            0.0,
            0.0,
            0.0,
            f"deferred: {contract.deferred_reason or contract.mechanism}",
            contract_id=contract.contract_id,
        )
    if contract.mechanism != "vaporization":
        raise CompletionContractBlocked(
            f"{contract.contract_id}: non-vapor contract is not computable"
        )
    if contract.provenance_rule != _VAPOR_PROVENANCE_RULE:
        raise CompletionContractBlocked(
            f"{contract.contract_id}: unsupported provenance_rule "
            f"{contract.provenance_rule!r}"
        )
    _validate_contract(contract)

    target = contract.target_species or contract.target_key
    unavailable = evaporation_quantity_unavailability(
        getattr(queries, "sim", None),
        target,
    )
    if unavailable:
        raise CompletionContractBlocked(
            f"{contract.contract_id}: {unavailable['reason']}"
        )
    product_accounts = tuple(dict.fromkeys((
        *contract.product_accounts,
        _VAPOR_TERMINAL_OFFGAS_ACCOUNT,
    )))
    product_kg = _species_kg_by_accounts(queries, product_accounts)
    residual_kg = _species_kg_by_accounts(queries, contract.residual_accounts)
    wall_kg: Mapping[str, Any] = {}
    if contract.wall_account:
        wall_kg = _species_kg_by_account_pattern(queries, contract.wall_account)

    gross_product_account_mol = _contract_target_equivalent_mol(
        contract, product_kg, "product"
    )
    product_account_mol = gross_product_account_mol
    reagent_provenance = _contract_reagent_provenance_target_equivalent_mol(
        contract, queries
    )
    product_non_feedstock_mol = _non_feedstock_reagent_element_mol(
        contract.element,
        queries,
        accounts=product_accounts,
        gross_surface_target_equiv_mol=gross_product_account_mol,
    )
    product_account_mol = max(
        0.0,
        product_account_mol - min(product_account_mol, product_non_feedstock_mol),
    )
    product_mol = product_account_mol + reagent_provenance.feedstock_recovered_mol
    residual_mol = _contract_residual_target_equivalent_mol(
        contract, residual_kg
    )
    residual_non_feedstock_mol = _non_feedstock_reagent_element_mol(
        contract.element,
        queries,
        accounts=contract.residual_accounts,
        gross_surface_target_equiv_mol=residual_mol,
    )
    residual_mol = max(
        0.0,
        residual_mol - min(residual_mol, residual_non_feedstock_mol),
    )
    wall_mol = _contract_target_equivalent_mol(contract, wall_kg, "wall")
    if contract.wall_account:
        wall_non_feedstock_mol = _non_feedstock_reagent_element_mol(
            contract.element,
            queries,
            account_pattern=contract.wall_account,
            gross_surface_target_equiv_mol=wall_mol,
        )
        wall_mol = max(0.0, wall_mol - min(wall_mol, wall_non_feedstock_mol))
    if wall_mol > _WALL_EPS and not contract.wall_account:
        raise CompletionContractBlocked(
            f"{contract.contract_id}: wall deposit present without wall term"
        )
    denom = product_mol + residual_mol + wall_mol
    if denom <= _EPS:
        return TargetExtractionCompleteness(
            target,
            None,
            product_mol,
            residual_mol,
            denom,
            "no target-equivalent mol evidence",
            wall_deposit_target_equiv_mol=wall_mol,
            reagent_target_equiv_mol=reagent_provenance.feedstock_recovered_mol,
            gross_product_target_equiv_mol=gross_product_account_mol,
            contract_id=contract.contract_id,
            feedstock_recovered_reagent_target_equiv_mol=(
                reagent_provenance.feedstock_recovered_mol
            ),
            credit_line_reagent_target_equiv_mol=reagent_provenance.credit_line_mol,
            external_additive_reagent_target_equiv_mol=(
                reagent_provenance.external_additive_mol
            ),
            denominator_basis_source=(
                "feedstock_derived_product_residual_wall_excluding_credit_line_"
                "and_external_additives"
            ),
        )
    return TargetExtractionCompleteness(
        target,
        product_mol / denom,
        product_mol,
        residual_mol,
        denom,
        wall_deposit_target_equiv_mol=wall_mol,
        reagent_target_equiv_mol=reagent_provenance.feedstock_recovered_mol,
        gross_product_target_equiv_mol=gross_product_account_mol,
        contract_id=contract.contract_id,
        feedstock_recovered_reagent_target_equiv_mol=(
            reagent_provenance.feedstock_recovered_mol
        ),
        credit_line_reagent_target_equiv_mol=reagent_provenance.credit_line_mol,
        external_additive_reagent_target_equiv_mol=(
            reagent_provenance.external_additive_mol
        ),
        denominator_basis_source=(
            "feedstock_derived_product_residual_wall_excluding_credit_line_"
            "and_external_additives"
        ),
    )


def _target_equivalent_mol(target: str, species: str, kg: Any) -> float:
    species_mol = _species_mol(species, kg)
    if species_mol <= _EPS:
        return 0.0
    target_element = _target_element(target)
    species_formula = parse_formula(species, species=species)
    element_count = species_formula.elements.get(target_element, 0.0)
    if element_count <= 0.0:
        raise ValueError(f"{species} contains no {target_element} for target {target}")
    return species_mol * element_count


def _target_element(target: str) -> str:
    formula = parse_formula(target, species=target)
    if len(formula.elements) == 1:
        return next(iter(formula.elements))
    non_oxygen = [element for element in formula.elements if element != "O"]
    if len(non_oxygen) == 1:
        return non_oxygen[0]
    raise ValueError(f"target {target} does not identify one target element")


def _species_mol(species: str, kg: Any) -> float:
    amount = _non_negative_number(kg, f"{species} kg")
    if amount <= _EPS:
        return 0.0
    molar_mass = MOLAR_MASS.get(species)
    if molar_mass is None:
        try:
            formula = parse_formula(species, species=species)
        except (AccountingError, ValueError) as exc:
            raise KeyError(f"missing molar mass for {species}") from exc
        molar_mass = formula.molar_mass_g_mol
    if molar_mass is None:
        raise KeyError(f"missing molar mass for {species}")
    return amount * 1000.0 / float(molar_mass)


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


def _contracts_from_list(
    campaign: str,
    stage: str | None,
    raw_contracts: Any,
) -> list[CompletionContract]:
    if raw_contracts is None:
        raw_contracts = []
    if not isinstance(raw_contracts, list):
        raise ValueError(f"{campaign}: contracts must be a list")
    return [
        _contract_from_mapping(campaign, stage, raw)
        for raw in raw_contracts
    ]


def _contract_from_mapping(
    campaign: str,
    stage: str | None,
    raw: Any,
) -> CompletionContract:
    if not isinstance(raw, Mapping):
        raise ValueError(f"{campaign}: contract must be a mapping")
    for field in _REQUIRED_CONTRACT_FIELDS:
        if field not in raw:
            raise ValueError(
                f"{campaign}: completion contract missing {field}"
            )
    element_map = raw.get("element_map", {}) or {}
    if not isinstance(element_map, Mapping):
        raise ValueError(f"{raw.get('contract_id')}: element_map must map")
    normalized_map = {
        str(element): tuple(str(species) for species in _as_targets(species))
        for element, species in element_map.items()
    }
    residual_species = _as_targets(raw.get("residual_species"))
    return CompletionContract(
        contract_id=str(raw["contract_id"]),
        campaign=campaign,
        mechanism=str(raw["mechanism"]),
        target_species=_optional_str(raw.get("target_species")),
        target_element=_optional_str(raw.get("target_element")),
        semantic_target_kind=_optional_str(raw.get("semantic_target_kind")),
        product_accounts=_as_targets(raw.get("product_accounts")),
        residual_accounts=_as_targets(raw.get("residual_accounts")),
        residual_species=residual_species,
        wall_account=_optional_str(raw.get("wall_account")),
        element_map=MappingProxyType(normalized_map),
        denominator_source=str(raw["denominator_source"]),
        provenance_rule=str(raw["provenance_rule"]),
        mid_run_vs_terminal=str(raw["mid_run_vs_terminal"]),
        aggregation=str(raw["aggregation"]),
        deferred=bool(raw.get("deferred", False)),
        deferred_reason=str(raw.get("deferred_reason", "")),
        stage=stage,
    )


def _validate_unique_contract_ids(
    contracts: tuple[CompletionContract, ...] | list[CompletionContract],
) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for contract in contracts:
        if contract.contract_id in seen:
            duplicates.append(contract.contract_id)
        seen.add(contract.contract_id)
    if duplicates:
        raise ValueError(
            "duplicate completion contract ids: " + ", ".join(duplicates)
        )


def _validate_unique_contract_targets(
    contracts: tuple[CompletionContract, ...] | list[CompletionContract],
) -> None:
    seen: dict[tuple[str, str | None, str], str] = {}
    duplicates: list[str] = []
    for contract in contracts:
        key = (contract.campaign, contract.stage, contract.target_key)
        prior_id = seen.get(key)
        if prior_id is not None:
            step = (
                f"{contract.campaign}.{contract.stage}"
                if contract.stage
                else contract.campaign
            )
            duplicates.append(
                f"{step}.{contract.target_key}: {prior_id}, {contract.contract_id}"
            )
        else:
            seen[key] = contract.contract_id
    if duplicates:
        raise ValueError(
            "duplicate completion contract targets: " + "; ".join(duplicates)
        )


def _validate_contract(contract: CompletionContract) -> None:
    if contract.deferred:
        if not contract.deferred_reason:
            raise ValueError(
                f"{contract.contract_id}: deferred_reason is required"
            )
        return
    if contract.mechanism != "vaporization":
        raise ValueError(
            f"{contract.contract_id}: non-vapor contract must be deferred"
        )
    if not contract.target_element:
        raise ValueError(
            f"{contract.contract_id}: target_element is required"
        )
    if not contract.product_accounts:
        raise ValueError(
            f"{contract.contract_id}: product_accounts is required"
        )
    if not contract.residual_accounts:
        raise ValueError(
            f"{contract.contract_id}: residual_accounts is required"
        )
    if not contract.residual_species:
        raise ValueError(
            f"{contract.contract_id}: residual_species is required"
        )
    if not contract.wall_account:
        raise ValueError(
            f"{contract.contract_id}: wall_account is required"
        )
    configured_species = contract.element_map.get(contract.element, ())
    if not configured_species:
        raise ValueError(
            f"{contract.contract_id}: element_map must include target_element"
        )
    if contract.denominator_source != "product_plus_residual_plus_wall_deposit":
        raise ValueError(
            f"{contract.contract_id}: denominator_source must include wall"
        )
    if contract.provenance_rule != _VAPOR_PROVENANCE_RULE:
        raise ValueError(
            f"{contract.contract_id}: vapor provenance must use "
            f"{_VAPOR_PROVENANCE_RULE}"
        )


def _check_step_targets(
    errors: list[str],
    campaign: str,
    stage: str | None,
    targets: tuple[str, ...],
    by_step: Mapping[tuple[str, str | None], list[CompletionContract]],
) -> None:
    step_contracts = by_step.get((campaign, stage), ())
    for target in targets:
        matches = [
            contract for contract in step_contracts
            if contract.target_species == target
            or contract.semantic_target_kind == target
        ]
        step_name = f"{campaign}.{stage}" if stage else campaign
        if not matches:
            errors.append(f"{step_name}: no contract for {target}")
            continue
        for contract in matches:
            if contract.deferred:
                continue
            try:
                _validate_contract(contract)
            except ValueError as exc:
                errors.append(str(exc))


def _has_endpoint(cfg: Mapping[str, Any]) -> bool:
    return any(
        key in cfg and cfg.get(key) is not None
        for key in ("endpoint", "soft_endpoint", "composition_endpoint")
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _as_targets(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        targets = []
        for item in value:
            if item is None:
                continue
            target = str(item)
            if target:
                targets.append(target)
        return tuple(targets)
    return ()


def _species_kg_by_accounts(
    queries: Any,
    accounts: tuple[str, ...],
) -> Mapping[str, Any]:
    helper = getattr(queries, "species_kg_by_accounts", None)
    if callable(helper):
        return helper(accounts)
    ledger = getattr(queries, "ledger", None)
    if ledger is None:
        raise CompletionContractBlocked("ledger surface is unavailable")
    values: dict[str, float] = {}
    for account in accounts:
        for species, kg in ledger.project_account_kg(str(account)).items():
            amount = float(kg)
            if amount:
                values[str(species)] = values.get(str(species), 0.0) + amount
    return values


def _species_kg_by_account_pattern(
    queries: Any,
    account_pattern: str,
) -> Mapping[str, Any]:
    helper = getattr(queries, "species_kg_by_account_pattern", None)
    if callable(helper):
        return helper(account_pattern)
    ledger = getattr(queries, "ledger", None)
    if ledger is None:
        raise CompletionContractBlocked("ledger surface is unavailable")
    pattern = str(account_pattern)
    if not pattern.endswith("*"):
        return _species_kg_by_accounts(queries, (pattern,))
    prefix = pattern[:-1]
    values: dict[str, float] = {}
    for account in ledger.kg_by_account():
        if not str(account).startswith(prefix):
            continue
        species_kg = ledger.project_account_kg(str(account))
        for species, kg in species_kg.items():
            amount = float(kg)
            if amount:
                values[str(species)] = values.get(str(species), 0.0) + amount
    return values


def _contract_residual_target_equivalent_mol(
    contract: CompletionContract,
    species_kg: Mapping[str, Any],
) -> float:
    filtered = {
        species: species_kg.get(species, 0.0)
        for species in contract.residual_species
    }
    return _contract_target_equivalent_mol(contract, filtered, "residual")


def _contract_reagent_provenance_target_equivalent_mol(
    contract: CompletionContract,
    queries: Any,
) -> ReagentProvenanceTargetEquivalent:
    element = contract.element
    feedstock_recovered_kg = _feedstock_recovered_reagent_kg(element, queries)
    credit_line_kg = _credit_line_reagent_kg(element, queries)
    external_additive_kg = _process_unspent_additive_reagent_kg(element, queries)
    return ReagentProvenanceTargetEquivalent(
        feedstock_recovered_mol=_species_mol(element, feedstock_recovered_kg),
        credit_line_mol=_species_mol(element, credit_line_kg),
        external_additive_mol=_species_mol(element, external_additive_kg),
    )


def _feedstock_recovered_reagent_kg(element: str, queries: Any) -> float:
    helper = getattr(queries, "feedstock_recovered_reagents_kg", None)
    if not callable(helper):
        return 0.0
    return _reagent_mapping_species_kg(
        helper(),
        element,
        "feedstock_recovered",
    )


def _credit_line_reagent_kg(element: str, queries: Any) -> float:
    helper = getattr(queries, "c3_alkali_credit_outstanding_kg_by_species", None)
    if not callable(helper):
        return 0.0
    credit_kg = _reagent_mapping_species_kg(helper(), element, "credit_line")
    if credit_kg <= _EPS:
        return 0.0
    reagent_kg = _species_kg_by_accounts(queries, ("process.reagent_inventory",))
    process_reagent_kg = _non_negative_number(
        reagent_kg.get(element, 0.0),
        f"process.reagent_inventory.{element}",
    )
    feedstock_kg = _feedstock_recovered_reagent_kg(element, queries)
    return min(credit_kg, max(0.0, process_reagent_kg - feedstock_kg))


def _non_feedstock_reagent_element_mol(
    element: str,
    queries: Any,
    *,
    accounts: tuple[str, ...] = (),
    account_pattern: str | None = None,
    gross_surface_target_equiv_mol: float,
) -> float:
    helper = getattr(queries, "non_feedstock_reagent_element_kg_by_account", None)
    if not callable(helper):
        if (
            gross_surface_target_equiv_mol > _EPS
            and _target_element_has_reagent_signal(element, queries)
        ):
            raise CompletionContractBlocked(
                f"unclean additive/reagent provenance for {element}: "
                "non_feedstock_reagent_element_kg_by_account surface unavailable"
            )
        return 0.0
    values = helper()
    if not isinstance(values, Mapping):
        raise CompletionContractBlocked(
            f"unclean additive/reagent provenance for {element}: "
            "non_feedstock_reagent_element_kg_by_account is not a mapping"
        )
    element_kg = 0.0
    provenance_element_kg = 0.0
    for account, element_values in values.items():
        account_name = str(account)
        if not isinstance(element_values, Mapping):
            raise CompletionContractBlocked(
                f"unclean additive/reagent provenance for {element}: "
                f"{account_name} provenance entry is not a mapping"
            )
        account_element_kg = _non_negative_number(
            element_values.get(element, 0.0),
            f"non_feedstock_reagent.{account_name}.{element}",
        )
        provenance_element_kg += account_element_kg
        if accounts and account_name not in accounts:
            continue
        if account_pattern is not None and not _account_matches_pattern(
            account_name,
            account_pattern,
        ):
            continue
        element_kg += account_element_kg
    required_provenance_mol = _target_element_required_provenance_mol(
        element,
        queries,
    )
    provenance_mol = _species_mol(element, provenance_element_kg)
    if (
        gross_surface_target_equiv_mol > _EPS
        and required_provenance_mol > _EPS
        and provenance_mol + _EPS < required_provenance_mol
    ):
        raise CompletionContractBlocked(
            f"unclean additive/reagent provenance for {element}: "
            "provenance map does not account for declared additive/credit signal"
        )
    return _species_mol(element, element_kg)


def _account_matches_pattern(account: str, pattern: str) -> bool:
    if pattern.endswith("*"):
        return account.startswith(pattern[:-1])
    return account == pattern


def _reagent_mapping_species_kg(
    values: Any,
    element: str,
    source_name: str,
) -> float:
    if not isinstance(values, Mapping):
        return 0.0
    direct = values.get(element, None)
    if direct is None:
        direct = values.get(f"recovered_{element}_reagent", 0.0)
    return _non_negative_number(direct, f"{source_name}.{element}")


def _process_unspent_additive_reagent_kg(element: str, queries: Any) -> float:
    helper = getattr(queries, "unspent_additive_reagents_kg", None)
    if not callable(helper):
        return 0.0
    values = helper()
    total_key = f"unspent_{element}_reagent"
    total_unspent_kg = _non_negative_number(
        values.get(total_key, 0.0),
        total_key,
    )
    ledger = getattr(queries, "ledger", None)
    reservoir_kg = 0.0
    if ledger is not None:
        reservoir_balance_kg = _finite_number(
            ledger.kg_by_account(f"reservoir.reagent.{element}").get(
                element, 0.0
            ),
            f"reservoir.reagent.{element}",
        )
        reservoir_kg = max(0.0, reservoir_balance_kg)
    return max(0.0, total_unspent_kg - reservoir_kg)


def _contract_target_equivalent_mol(
    contract: CompletionContract,
    species_kg: Mapping[str, Any],
    source: str,
) -> float:
    total = 0.0
    allowed = set(contract.allowed_species)
    if not allowed:
        raise CompletionContractBlocked(
            f"{contract.contract_id}: no element_map species for {source}"
        )
    for species, kg in species_kg.items():
        species_name = str(species)
        if source == "reagent":
            reagent_element = _unspent_reagent_element(species_name)
            if reagent_element is not None:
                if reagent_element != contract.element:
                    continue
                total += _species_mol(reagent_element, kg)
                continue
            if species_name != contract.element:
                continue
            total += _species_mol(contract.element, kg)
            continue
        if species_name not in allowed:
            continue
        total += _element_equivalent_mol(
            contract.element,
            species_name,
            kg,
        )
    return total


def _target_relevant_species_kg(
    element: str,
    species_kg: Mapping[str, Any],
) -> dict[str, float]:
    values: dict[str, float] = {}
    for species, kg in species_kg.items():
        species_name = str(species)
        if _unspent_reagent_element(species_name) is not None:
            continue
        amount = _non_negative_number(kg, f"{species_name} kg")
        if amount <= _EPS:
            continue
        formula = parse_formula(species_name, species=species_name)
        if formula.elements.get(element, 0.0) <= 0.0:
            continue
        values[species_name] = values.get(species_name, 0.0) + amount
    return values


def _target_relevant_element_mol(
    element: str,
    species_kg: Mapping[str, Any],
) -> float:
    total = 0.0
    for species, kg in _target_relevant_species_kg(element, species_kg).items():
        total += _element_equivalent_mol(element, species, kg)
    return total


def _target_yield_denominator_element_mol(
    element: str,
    initial_cleaned_melt_kg: Mapping[str, Any],
    queries: Any,
) -> float:
    gross_mol = _target_relevant_element_mol(element, initial_cleaned_melt_kg)
    if gross_mol <= _EPS:
        return 0.0
    excluded_mol = _initial_denominator_non_feedstock_reagent_element_mol(
        element,
        initial_cleaned_melt_kg,
        queries,
        gross_mol,
    )
    if math.isinf(excluded_mol):
        raise CompletionContractBlocked(
            f"unclean additive/reagent provenance for {element}: "
            "denominator reagent surface is not finite"
        )
    return max(0.0, gross_mol - min(gross_mol, excluded_mol))


def _initial_denominator_non_feedstock_reagent_element_mol(
    element: str,
    initial_cleaned_melt_kg: Mapping[str, Any],
    queries: Any,
    gross_denominator_mol: float,
) -> float:
    tagged_mol = _strict_non_feedstock_reagent_element_mol(
        element,
        queries,
        accounts=(_TARGET_YIELD_DENOMINATOR_ACCOUNT,),
        gross_product_target_equiv_mol=gross_denominator_mol,
    )
    if tagged_mol > _EPS:
        return tagged_mol
    direct_initial_mol = _species_mol(
        element,
        initial_cleaned_melt_kg.get(element, 0.0),
    )
    if direct_initial_mol <= _EPS:
        return 0.0
    credit_mol = _species_mol(
        element,
        _credit_line_reagent_kg(element, queries),
    )
    additive_mol = _external_unspent_additive_reagent_element_mol(element, queries)
    if math.isinf(credit_mol) or math.isinf(additive_mol):
        return math.inf
    return min(direct_initial_mol, credit_mol + additive_mol)


def _strict_non_feedstock_reagent_element_mol(
    element: str,
    queries: Any,
    *,
    accounts: tuple[str, ...],
    gross_product_target_equiv_mol: float,
) -> float:
    # Both kernel-proposal and backend-equilibrium commit paths update this
    # provenance map immediately after their validated ledger commit.
    helper = getattr(queries, "non_feedstock_reagent_element_kg_by_account", None)
    if not callable(helper):
        if _target_element_has_reagent_signal(element, queries):
            raise CompletionContractBlocked(
                f"unclean additive/reagent provenance for {element}: "
                "non_feedstock_reagent_element_kg_by_account surface unavailable"
            )
        return 0.0
    values = helper()
    if not isinstance(values, Mapping):
        raise CompletionContractBlocked(
            f"unclean additive/reagent provenance for {element}: "
            "non_feedstock_reagent_element_kg_by_account is not a mapping"
        )
    element_kg = 0.0
    provenance_element_kg = 0.0
    for account, element_values in values.items():
        account_name = str(account)
        if not isinstance(element_values, Mapping):
            raise CompletionContractBlocked(
                f"unclean additive/reagent provenance for {element}: "
                f"{account_name} provenance entry is not a mapping"
            )
        account_element_kg = _non_negative_number(
            element_values.get(element, 0.0),
            f"non_feedstock_reagent.{account_name}.{element}",
        )
        provenance_element_kg += account_element_kg
        if account_name not in accounts:
            continue
        element_kg += account_element_kg
    excluded_mol = _species_mol(element, element_kg)
    required_provenance_mol = _target_element_required_provenance_mol(
        element,
        queries,
    )
    provenance_mol = _species_mol(element, provenance_element_kg)
    if (
        required_provenance_mol > _EPS
        and provenance_mol + _EPS < required_provenance_mol
    ):
        raise CompletionContractBlocked(
            f"unclean additive/reagent provenance for {element}: "
            "provenance map does not account for declared additive/credit signal"
        )
    return min(gross_product_target_equiv_mol, excluded_mol)


def _target_element_has_reagent_signal(element: str, queries: Any) -> bool:
    return _target_element_reagent_signal_mol(element, queries) > _EPS


def _target_element_reagent_signal_mol(element: str, queries: Any) -> float:
    return max(
        _declared_additive_element_mol(element, queries),
        _external_unspent_additive_reagent_element_mol(element, queries),
        _credit_line_reagent_element_mol(element, queries),
        _external_reagent_inventory_element_mol(element, queries),
    )


def _target_element_required_provenance_mol(element: str, queries: Any) -> float:
    declared_mol = _declared_additive_element_mol(element, queries)
    unspent_mol = _external_unspent_additive_reagent_element_mol(element, queries)
    credit_mol = _credit_line_reagent_element_mol(element, queries)
    inventory_mol = _external_reagent_inventory_element_mol(element, queries)
    if any(math.isinf(value) for value in (declared_mol, unspent_mol, credit_mol, inventory_mol)):
        return math.inf
    introduced_mol = declared_mol + credit_mol
    if introduced_mol <= _EPS:
        return inventory_mol
    # Unspent and live-inventory surfaces overlap: subtract their maximum once
    # from total declared-plus-credited introduction to obtain spent provenance.
    live_unspent_mol = max(unspent_mol, inventory_mol)
    return max(0.0, introduced_mol - live_unspent_mol)


def _external_unspent_additive_reagent_element_mol(element: str, queries: Any) -> float:
    unspent_mol = _unspent_additive_reagent_element_mol(element, queries)
    recovered_mol = _feedstock_recovered_reagent_element_mol(element, queries)
    if math.isinf(unspent_mol):
        return math.inf
    return max(0.0, unspent_mol - recovered_mol)


def _unspent_additive_reagent_element_mol(element: str, queries: Any) -> float:
    helper = getattr(queries, "unspent_additive_reagents_kg", None)
    if not callable(helper):
        return 0.0
    values = helper()
    if not isinstance(values, Mapping):
        return math.inf
    unspent_key = f"unspent_{element}_reagent"
    return _species_mol(
        element,
        _non_negative_number(values.get(unspent_key, 0.0), unspent_key),
    ) + _target_relevant_element_mol(element, values)


def _credit_line_reagent_element_mol(element: str, queries: Any) -> float:
    total = 0.0
    credit_helper = getattr(queries, "c3_alkali_credit_outstanding_kg_by_species", None)
    if callable(credit_helper):
        values = credit_helper()
        if not isinstance(values, Mapping):
            return math.inf
        total += _target_relevant_element_mol(element, values)
    c7_helper = getattr(queries, "c7_al_credit_kg_by_species", None)
    if callable(c7_helper):
        values = c7_helper()
        if not isinstance(values, Mapping):
            return math.inf
        total += _target_relevant_element_mol(element, values)
    return total


def _external_reagent_inventory_element_mol(element: str, queries: Any) -> float:
    inventory_mol = _reagent_inventory_element_mol(element, queries)
    recovered_mol = _feedstock_recovered_reagent_element_mol(element, queries)
    return max(0.0, inventory_mol - recovered_mol)


def _reagent_inventory_element_mol(element: str, queries: Any) -> float:
    try:
        reagent_inventory = _species_kg_by_accounts(
            queries,
            ("process.reagent_inventory",),
        )
    except (AccountingError, KeyError, TypeError, ValueError):
        reagent_inventory = {}
    return _target_relevant_element_mol(element, reagent_inventory)


def _feedstock_recovered_reagent_element_mol(element: str, queries: Any) -> float:
    helper = getattr(queries, "feedstock_recovered_reagents_kg", None)
    if not callable(helper):
        return 0.0
    values = helper()
    if not isinstance(values, Mapping):
        return 0.0
    return _target_relevant_element_mol(element, values)


def _declared_additive_has_element(element: str, queries: Any) -> bool:
    return _declared_additive_element_mol(element, queries) > _EPS


def _declared_additive_element_mol(element: str, queries: Any) -> float:
    sim = getattr(queries, "sim", None)
    record = getattr(sim, "record", None)
    additives = getattr(record, "additives_kg", {})
    if not isinstance(additives, Mapping):
        return 0.0
    total = 0.0
    for species, kg in additives.items():
        amount = _non_negative_number(kg, f"additives_kg.{species}")
        if amount <= _EPS:
            continue
        total += _target_relevant_element_mol(element, {str(species): amount})
    return total


def _unspent_reagent_element(species_name: str) -> str | None:
    prefix = "unspent_"
    suffix = "_reagent"
    if not species_name.startswith(prefix) or not species_name.endswith(suffix):
        return None
    element = species_name[len(prefix) : -len(suffix)]
    return element or None


def _element_equivalent_mol(element: str, species: str, kg: Any) -> float:
    species_mol = _species_mol(species, kg)
    if species_mol <= _EPS:
        return 0.0
    species_formula = parse_formula(species, species=species)
    element_count = species_formula.elements.get(element, 0.0)
    if element_count <= 0.0:
        raise ValueError(f"{species} contains no {element}")
    return species_mol * element_count
