"""Parallel cost-of-goods ledger for traceable additive diagnostics."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import wraps
from types import MappingProxyType
from typing import Any

from simulator.accounting.ledger import LedgerTransition
from simulator.accounting.queries import is_reagent_bookkeeping_product
from simulator.cost_energy import (
    furnace_thermal_flux_hours,
    owner_ratify_cost_placeholders,
    project_owner_ratify_money,
)
from simulator.pumping_cost import estimate_subambient_pump_cost, pumping_cost_parameters

VECTOR_TOLERANCE = 1e-12
COST_LEDGER_SCHEMA_VERSION = "cost-ledger-v1"
COST_POLICY_ID = "mass-allocation-default__reagent-full-cost-v1"
COST_BEARING_ACCOUNTS = frozenset({
    "process.reagent_inventory",
    "process.metal_phase",
    "process.condensation_train",
    "process.c7_al_credit",
})
MRE_ELECTRICAL_CAMPAIGNS = frozenset({"C5", "MRE_BASELINE"})
_AUXILIARY_ELECTRICAL_BREAKDOWN_FIELDS = (
    "energy_electrical_breakdown_kWh",
    "electrical_breakdown_kWh",
)
_AUXILIARY_ELECTRICAL_COMPONENT_ALIASES = MappingProxyType({
    "turbine": ("turbine_kWh", "turbine"),
    "condenser": ("condenser_kWh", "condenser"),
    "pumping": ("pumping_electrical_kWh", "pumping_kWh", "pumping"),
})
_RETURN_NONE = object()
_RETURN_EMPTY_TUPLE = object()
_RETURN_SUMMARY = object()


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _best_effort_cost_entrypoint(
    warning_prefix: str,
    *,
    fallback: object = _RETURN_NONE,
):
    def decorate(method):
        @wraps(method)
        def guarded(self, *args, **kwargs):
            snapshot = self._state_snapshot()
            try:
                return method(self, *args, **kwargs)
            except Exception as exc:  # noqa: BLE001 -- cost diagnostics must not abort chemistry
                self._restore_state_snapshot(snapshot)
                self._record_best_effort_error(warning_prefix, method.__name__, exc)
                if fallback is _RETURN_EMPTY_TUPLE:
                    return ()
                if fallback is _RETURN_SUMMARY:
                    return self._summary_fallback()
                return None

        return guarded

    return decorate


@dataclass(frozen=True)
class CostVector:
    electrical_kWh: float = 0.0
    thermal_flux_h: float = 0.0
    furnace_h: float = 0.0
    launch_penalty_kg: float = 0.0
    external_reagent_kg: float = 0.0

    def __post_init__(self) -> None:
        for name in self._fields():
            value = float(getattr(self, name))
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
            object.__setattr__(self, name, 0.0 if abs(value) <= VECTOR_TOLERANCE else value)

    @staticmethod
    def _fields() -> tuple[str, ...]:
        return (
            "electrical_kWh",
            "thermal_flux_h",
            "furnace_h",
            "launch_penalty_kg",
            "external_reagent_kg",
        )

    def __add__(self, other: "CostVector") -> "CostVector":
        return CostVector(
            **{name: getattr(self, name) + getattr(other, name) for name in self._fields()}
        )

    def __sub__(self, other: "CostVector") -> "CostVector":
        return CostVector(
            **{name: getattr(self, name) - getattr(other, name) for name in self._fields()}
        )

    def scale(self, factor: float) -> "CostVector":
        factor = float(factor)
        if not math.isfinite(factor):
            raise ValueError("cost scale factor must be finite")
        return CostVector(**{name: getattr(self, name) * factor for name in self._fields()})

    def is_zero(self, tolerance: float = VECTOR_TOLERANCE) -> bool:
        return all(abs(getattr(self, name)) <= tolerance for name in self._fields())

    def max_abs(self) -> float:
        return max(abs(getattr(self, name)) for name in self._fields())

    def close_to(self, other: "CostVector", tolerance: float = VECTOR_TOLERANCE) -> bool:
        return (self - other).max_abs() <= tolerance

    def to_json(self) -> dict[str, float]:
        return {name: float(getattr(self, name)) for name in self._fields()}


ZERO_COST = CostVector()


@dataclass(frozen=True)
class CostLot:
    lot_id: str
    material_account: str
    species_or_product: str
    quantity_kg: float
    accumulated_cost: CostVector
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        quantity = float(self.quantity_kg)
        if not math.isfinite(quantity) or quantity < -VECTOR_TOLERANCE:
            raise ValueError("CostLot quantity_kg must be finite and non-negative")
        object.__setattr__(self, "quantity_kg", 0.0 if abs(quantity) <= VECTOR_TOLERANCE else quantity)
        object.__setattr__(self, "material_account", str(self.material_account))
        object.__setattr__(self, "species_or_product", str(self.species_or_product))
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance or {})))

    def slice(self, lot_id: str, quantity_kg: float) -> "CostLot":
        if self.quantity_kg <= VECTOR_TOLERANCE:
            fraction = 0.0
        else:
            fraction = max(0.0, min(1.0, float(quantity_kg) / self.quantity_kg))
        return CostLot(
            lot_id=lot_id,
            material_account=self.material_account,
            species_or_product=self.species_or_product,
            quantity_kg=quantity_kg,
            accumulated_cost=self.accumulated_cost.scale(fraction),
            provenance=self.provenance,
        )

    def moved(self, lot_id: str, material_account: str) -> "CostLot":
        return CostLot(
            lot_id=lot_id,
            material_account=material_account,
            species_or_product=self.species_or_product,
            quantity_kg=self.quantity_kg,
            accumulated_cost=self.accumulated_cost,
            provenance=self.provenance,
        )

    def with_cost(self, lot_id: str, cost: CostVector) -> "CostLot":
        return CostLot(
            lot_id=lot_id,
            material_account=self.material_account,
            species_or_product=self.species_or_product,
            quantity_kg=self.quantity_kg,
            accumulated_cost=cost,
            provenance=self.provenance,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "lot_id": self.lot_id,
            "material_account": self.material_account,
            "species_or_product": self.species_or_product,
            "quantity_kg": float(self.quantity_kg),
            "accumulated_cost": self.accumulated_cost.to_json(),
            "provenance": dict(self.provenance),
        }


@dataclass(frozen=True)
class CostTransition:
    transition_id: str
    process_step: str
    mass_transition_refs: tuple[str, ...]
    input_cost_debits: tuple[CostLot, ...]
    processing_cost_added: CostVector
    allocation_policy_id: str
    output_cost_credits: tuple[CostLot, ...]
    audit_notes: tuple[str, ...] = ()

    def input_total(self) -> CostVector:
        return _sum_cost(lot.accumulated_cost for lot in self.input_cost_debits)

    def output_total(self) -> CostVector:
        return _sum_cost(lot.accumulated_cost for lot in self.output_cost_credits)

    def validate_balance(self, tolerance: float = VECTOR_TOLERANCE) -> None:
        expected = self.input_total() + self.processing_cost_added
        actual = self.output_total()
        if not actual.close_to(expected, tolerance):
            raise ValueError(
                f"cost transition {self.transition_id!r} does not balance: "
                f"expected={expected.to_json()} actual={actual.to_json()}"
            )

    def to_json(self) -> dict[str, Any]:
        return {
            "transition_id": self.transition_id,
            "process_step": self.process_step,
            "mass_transition_refs": list(self.mass_transition_refs),
            "input_cost_debits": [lot.to_json() for lot in self.input_cost_debits],
            "processing_cost_added": self.processing_cost_added.to_json(),
            "allocation_policy_id": self.allocation_policy_id,
            "output_cost_credits": [lot.to_json() for lot in self.output_cost_credits],
            "audit_notes": list(self.audit_notes),
        }


@dataclass(frozen=True)
class CostImportContext:
    mode: str = "mature"
    import_flag_enabled: bool = False
    available_supplier_species: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", str(self.mode or "mature"))
        object.__setattr__(
            self,
            "available_supplier_species",
            frozenset(str(s) for s in self.available_supplier_species),
        )

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> "CostImportContext":
        cfg = dict(config or {})
        mode = str(cfg.get("mode") or "mature")
        raw_flag = cfg.get("import_flag_enabled", mode == "bootstrap_narrative")
        if isinstance(raw_flag, str):
            import_flag_enabled = raw_flag.strip().lower() not in {"", "0", "false", "no", "off"}
        else:
            import_flag_enabled = bool(raw_flag)
        suppliers = cfg.get("available_supplier_species") or ()
        return cls(
            mode=mode,
            import_flag_enabled=import_flag_enabled,
            available_supplier_species=frozenset(str(s) for s in suppliers),
        )

    @classmethod
    def mature(cls, available_supplier_species: tuple[str, ...] = ()) -> "CostImportContext":
        return cls(
            mode="mature",
            import_flag_enabled=False,
            available_supplier_species=frozenset(available_supplier_species),
        )

    @classmethod
    def bootstrap_narrative(
        cls,
        available_supplier_species: tuple[str, ...] = (),
    ) -> "CostImportContext":
        return cls(
            mode="bootstrap_narrative",
            import_flag_enabled=True,
            available_supplier_species=frozenset(available_supplier_species),
        )

    def classify(self, species: str) -> str:
        if not self.import_flag_enabled:
            return "isru_local"
        return "isru_local" if str(species) in self.available_supplier_species else "import_penalty"

    def route_option_visible(self, _species: str) -> bool:
        return True

    def to_json(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "import_flag_enabled": bool(self.import_flag_enabled),
            "available_supplier_species": sorted(self.available_supplier_species),
            "classifier_scope": "reporting_only_not_optimizer_gate",
            "all_options_visible": True,
        }


class CostLedger:
    def __init__(
        self,
        *,
        allocation_policy_id: str = COST_POLICY_ID,
        import_context: CostImportContext | None = None,
    ) -> None:
        self.allocation_policy_id = str(allocation_policy_id)
        self.import_context = import_context or CostImportContext.mature()
        self._active_lots: list[CostLot] = []
        self._product_lots: list[CostLot] = []
        self._transitions: list[CostTransition] = []
        self._warnings: list[str] = []
        self._sequence = 0

    @property
    def transitions(self) -> tuple[CostTransition, ...]:
        return tuple(self._transitions)

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(self._warnings)

    @_best_effort_cost_entrypoint("cost_seed_error")
    def seed_external_material(
        self,
        *,
        account: str,
        species: str,
        quantity_kg: float,
        provenance: Mapping[str, Any] | None = None,
        cost: CostVector | None = None,
    ) -> CostLot | None:
        quantity = _finite(quantity_kg)
        if quantity <= VECTOR_TOLERANCE:
            return None
        classification = self.import_context.classify(species)
        seed_cost = cost or CostVector(
            launch_penalty_kg=quantity if classification == "import_penalty" else 0.0,
            external_reagent_kg=quantity,
        )
        lot = CostLot(
            lot_id=self._next_id("costlot"),
            material_account=account,
            species_or_product=species,
            quantity_kg=quantity,
            accumulated_cost=seed_cost,
            provenance={
                "source": "external_seed",
                "import_classification": classification,
                **dict(provenance or {}),
            },
        )
        self._active_lots.append(lot)
        return lot

    @_best_effort_cost_entrypoint("cost_observation_error", fallback=_RETURN_EMPTY_TUPLE)
    def move_inventory_lots(
        self,
        *,
        source_account: str,
        destination_account: str,
        species: str,
        quantity_kg: float,
        reason: str,
    ) -> tuple[CostLot, ...]:
        debits = self._debit_lots(source_account, species, quantity_kg, strict=False)
        moved = tuple(
            lot.moved(self._next_id("costlot"), destination_account)
            for lot in debits
        )
        self._active_lots.extend(moved)
        if quantity_kg > VECTOR_TOLERANCE and not moved:
            self._warnings.append(
                f"zero_by_policy: no cost lot for {source_account}:{species} during {reason}"
            )
        return moved

    @_best_effort_cost_entrypoint("cost_observation_error", fallback=_RETURN_EMPTY_TUPLE)
    def move_product_lots(
        self,
        *,
        source_account: str,
        destination_account: str,
        species: str,
        quantity_kg: float,
        reason: str,
    ) -> tuple[CostLot, ...]:
        requested = max(0.0, _finite(quantity_kg))
        remaining = requested
        kept: list[CostLot] = []
        moved: list[CostLot] = []
        for lot in self._product_lots:
            if (
                remaining > VECTOR_TOLERANCE
                and lot.material_account == source_account
                and lot.species_or_product == species
            ):
                take = min(lot.quantity_kg, remaining)
                if take > VECTOR_TOLERANCE:
                    moved_slice = lot.slice(self._next_id("costlot"), take)
                    moved.append(
                        moved_slice.moved(
                            self._next_id("costlot"), destination_account
                        )
                    )
                    leftover = lot.quantity_kg - take
                    if leftover > VECTOR_TOLERANCE:
                        kept.append(lot.slice(self._next_id("costlot"), leftover))
                    remaining -= take
                    continue
            kept.append(lot)
        self._product_lots = kept + moved
        if _is_cost_bearing_account(destination_account):
            self._active_lots.extend(moved)
        if requested > VECTOR_TOLERANCE and not moved:
            self._warnings.append(
                f"zero_by_policy: no product cost lot for "
                f"{source_account}:{species} during {reason}"
            )
        return tuple(moved)

    @_best_effort_cost_entrypoint("cost_observation_error")
    def apply_mass_allocated_event(
        self,
        *,
        process_step: str,
        outputs_kg: Mapping[Any, float],
        processing_cost: CostVector = ZERO_COST,
        input_lots: tuple[CostLot, ...] = (),
        mass_transition_refs: tuple[str, ...] = (),
        audit_notes: tuple[str, ...] = (),
    ) -> CostTransition:
        outputs = _clean_outputs(outputs_kg)
        input_cost = _sum_cost(lot.accumulated_cost for lot in input_lots)
        allocatable = input_cost + processing_cost
        allocations = _allocate_by_mass(outputs, allocatable)
        credits = tuple(
            self._credit_output(key, outputs[key], allocations[key], process_step)
            for key in sorted(outputs, key=_output_sort_key)
        )
        return self._append_transition(
            process_step=process_step,
            mass_transition_refs=mass_transition_refs,
            input_lots=input_lots,
            processing_cost=processing_cost,
            credits=credits,
            audit_notes=audit_notes,
        )

    @_best_effort_cost_entrypoint("cost_observation_error")
    def apply_reagent_full_cost_event(
        self,
        *,
        process_step: str,
        reagent_account: str,
        reagent_species: str,
        reagent_quantity_kg: float,
        beneficiary_outputs_kg: Mapping[Any, float],
        coproduct_outputs_kg: Mapping[Any, float] | None = None,
        processing_cost: CostVector = ZERO_COST,
        mass_transition_refs: tuple[str, ...] = (),
        strict: bool = True,
    ) -> CostTransition:
        debits = self._debit_lots(
            reagent_account,
            reagent_species,
            reagent_quantity_kg,
            strict=strict,
        )
        return self._apply_reagent_full_cost_from_debits(
            process_step=process_step,
            input_lots=debits,
            beneficiary_outputs_kg=beneficiary_outputs_kg,
            coproduct_outputs_kg=coproduct_outputs_kg or {},
            processing_cost=processing_cost,
            mass_transition_refs=mass_transition_refs,
            audit_notes=("reagent-full-cost exception",),
        )

    @_best_effort_cost_entrypoint("cost_observation_error")
    def observe_transition(
        self,
        *,
        intent: Any,
        transition: LedgerTransition,
        diagnostic: Mapping[str, Any] | None = None,
        control_inputs: Mapping[str, Any] | None = None,
        temperature_C: float | None = None,
        strict: bool = False,
    ) -> CostTransition | None:
        diagnostic = dict(diagnostic or {})
        controls = dict(control_inputs or {})
        process_step = _process_step(intent, controls)
        processing_cost = CostVector(electrical_kWh=max(0.0, _finite(diagnostic.get("energy_kWh"))))
        input_lots: list[CostLot] = []
        saw_cost_bearing_debit = False
        for lot in transition.debits:
            if not _is_cost_bearing_account(lot.account):
                continue
            for species, kg in lot.species_kg.items():
                saw_cost_bearing_debit = True
                input_lots.extend(
                    self._debit_lots(lot.account, species, kg, strict=strict)
                )

        outputs = _outputs_from_lots(transition.credits)
        if not outputs:
            if input_lots or not processing_cost.is_zero():
                if strict:
                    raise ValueError(f"cost-bearing transition {transition.name!r} has no outputs")
                self._warnings.append(
                    f"zero_by_policy: no cost outputs for mass transition {transition.name}"
                )
            return None

        if input_lots:
            beneficiary, coproduct = _split_beneficiary_outputs(transition.credits)
            return self._apply_reagent_full_cost_from_debits(
                process_step=process_step,
                input_lots=tuple(input_lots),
                beneficiary_outputs_kg=beneficiary or outputs,
                coproduct_outputs_kg=coproduct if beneficiary else {},
                processing_cost=processing_cost,
                mass_transition_refs=(transition.name,),
                audit_notes=("observed AtomLedger transition", "reagent-full-cost exception"),
            )

        if saw_cost_bearing_debit and strict:
            raise ValueError(f"missing cost lots for cost-bearing transition {transition.name!r}")
        if saw_cost_bearing_debit:
            self._warnings.append(
                f"zero_by_policy: no seeded cost lot for mass transition {transition.name}"
            )
        if processing_cost.is_zero():
            return None
        return self.apply_mass_allocated_event(
            process_step=process_step,
            outputs_kg=outputs,
            processing_cost=processing_cost,
            mass_transition_refs=(transition.name,),
            audit_notes=("observed AtomLedger transition",),
        )

    @_best_effort_cost_entrypoint("cost_observation_error", fallback=_RETURN_SUMMARY)
    def summary(self) -> dict[str, Any]:
        product_totals: dict[str, CostVector] = defaultdict(lambda: ZERO_COST)
        product_quantities: dict[str, float] = defaultdict(float)
        for lot in self._product_lots:
            key = f"{lot.material_account}:{lot.species_or_product}"
            product_totals[key] = product_totals[key] + lot.accumulated_cost
            product_quantities[key] += lot.quantity_kg

        inventory_totals: dict[str, CostVector] = defaultdict(lambda: ZERO_COST)
        for lot in self._active_lots:
            key = f"{lot.material_account}:{lot.species_or_product}"
            inventory_totals[key] = inventory_totals[key] + lot.accumulated_cost

        transition_balance_max = 0.0
        for transition in self._transitions:
            diff = (
                transition.output_total()
                - (transition.input_total() + transition.processing_cost_added)
            )
            transition_balance_max = max(transition_balance_max, diff.max_abs())

        placeholders = [p.to_json() for p in owner_ratify_cost_placeholders()]
        return {
            "schema_version": COST_LEDGER_SCHEMA_VERSION,
            "policy_id": self.allocation_policy_id,
            "import_context": self.import_context.to_json(),
            "transition_count": len(self._transitions),
            "transition_balance_max_abs": float(transition_balance_max),
            "product_costs": {
                key: {
                    "quantity_kg": float(product_quantities[key]),
                    "accumulated_cost": product_totals[key].to_json(),
                    "owner_ratify_money_projection": project_owner_ratify_money(product_totals[key]),
                }
                for key in sorted(product_totals)
            },
            "active_inventory_costs": {
                key: inventory_totals[key].to_json()
                for key in sorted(inventory_totals)
            },
            "warnings": list(self._warnings),
            "owner_ratify_placeholders": placeholders,
            "owner_ratify_placeholder_count": len(placeholders),
        }

    def _state_snapshot(
        self,
    ) -> tuple[list[CostLot], list[CostLot], list[CostTransition], list[str], int]:
        return (
            list(self._active_lots),
            list(self._product_lots),
            list(self._transitions),
            list(self._warnings),
            int(self._sequence),
        )

    def _restore_state_snapshot(
        self,
        snapshot: tuple[list[CostLot], list[CostLot], list[CostTransition], list[str], int],
    ) -> None:
        (
            self._active_lots,
            self._product_lots,
            self._transitions,
            self._warnings,
            self._sequence,
        ) = snapshot

    def _record_best_effort_error(
        self,
        warning_prefix: str,
        context: str,
        exc: Exception,
    ) -> None:
        try:
            self._warnings.append(
                f"{warning_prefix}: {context}: {type(exc).__name__}: {exc}"
            )
        except Exception:
            pass

    def _summary_fallback(self) -> dict[str, Any]:
        try:
            import_context = self.import_context.to_json()
        except Exception:
            import_context = {
                "mode": "unknown",
                "import_flag_enabled": False,
                "available_supplier_species": [],
                "classifier_scope": "unavailable_after_cost_summary_error",
                "all_options_visible": True,
            }
        try:
            warnings = list(self._warnings)
        except Exception:
            warnings = []
        try:
            placeholders = [p.to_json() for p in owner_ratify_cost_placeholders()]
        except Exception:
            placeholders = []
        return {
            "schema_version": COST_LEDGER_SCHEMA_VERSION,
            "policy_id": str(getattr(self, "allocation_policy_id", COST_POLICY_ID)),
            "import_context": import_context,
            "transition_count": 0,
            "transition_balance_max_abs": 0.0,
            "product_costs": {},
            "active_inventory_costs": {},
            "warnings": warnings,
            "owner_ratify_placeholders": placeholders,
            "owner_ratify_placeholder_count": len(placeholders),
        }

    def _apply_reagent_full_cost_from_debits(
        self,
        *,
        process_step: str,
        input_lots: tuple[CostLot, ...],
        beneficiary_outputs_kg: Mapping[Any, float],
        coproduct_outputs_kg: Mapping[Any, float],
        processing_cost: CostVector,
        mass_transition_refs: tuple[str, ...],
        audit_notes: tuple[str, ...],
    ) -> CostTransition:
        beneficiary_outputs = _clean_outputs(beneficiary_outputs_kg)
        coproduct_outputs = _clean_outputs(coproduct_outputs_kg)
        all_outputs = dict(coproduct_outputs)
        all_outputs.update({
            key: all_outputs.get(key, 0.0) + value
            for key, value in beneficiary_outputs.items()
        })
        inherited_cost = _sum_cost(lot.accumulated_cost for lot in input_lots)
        inherited_alloc = _allocate_by_mass(beneficiary_outputs, inherited_cost)
        direct_alloc = _allocate_by_mass(all_outputs, processing_cost)
        credits = []
        for key in sorted(all_outputs, key=_output_sort_key):
            cost = direct_alloc.get(key, ZERO_COST)
            if key in inherited_alloc:
                cost = cost + inherited_alloc[key]
            credits.append(self._credit_output(key, all_outputs[key], cost, process_step))
        return self._append_transition(
            process_step=process_step,
            mass_transition_refs=mass_transition_refs,
            input_lots=input_lots,
            processing_cost=processing_cost,
            credits=tuple(credits),
            audit_notes=audit_notes,
        )

    def _append_transition(
        self,
        *,
        process_step: str,
        mass_transition_refs: tuple[str, ...],
        input_lots: tuple[CostLot, ...],
        processing_cost: CostVector,
        credits: tuple[CostLot, ...],
        audit_notes: tuple[str, ...],
    ) -> CostTransition:
        transition = CostTransition(
            transition_id=self._next_id("costtx"),
            process_step=process_step,
            mass_transition_refs=tuple(str(ref) for ref in mass_transition_refs),
            input_cost_debits=tuple(input_lots),
            processing_cost_added=processing_cost,
            allocation_policy_id=self.allocation_policy_id,
            output_cost_credits=credits,
            audit_notes=tuple(audit_notes),
        )
        transition.validate_balance()
        self._transitions.append(transition)
        return transition

    def _credit_output(
        self,
        key: Any,
        quantity_kg: float,
        cost: CostVector,
        process_step: str,
    ) -> CostLot:
        account, species = _normalize_output_key(key)
        lot = CostLot(
            lot_id=self._next_id("costlot"),
            material_account=account,
            species_or_product=species,
            quantity_kg=quantity_kg,
            accumulated_cost=cost,
            provenance={"source": process_step, "allocation_policy_id": self.allocation_policy_id},
        )
        self._product_lots.append(lot)
        if _is_cost_bearing_account(account):
            self._active_lots.append(lot)
        return lot

    def _debit_lots(
        self,
        account: str,
        species: str,
        quantity_kg: float | None,
        *,
        strict: bool,
    ) -> tuple[CostLot, ...]:
        requested = None if quantity_kg is None else max(0.0, _finite(quantity_kg))
        remaining = math.inf if requested is None else requested
        kept: list[CostLot] = []
        debits: list[CostLot] = []
        for lot in self._active_lots:
            if (
                remaining > VECTOR_TOLERANCE
                and lot.material_account == account
                and lot.species_or_product == species
            ):
                take = lot.quantity_kg if requested is None else min(lot.quantity_kg, remaining)
                if take > VECTOR_TOLERANCE:
                    debits.append(lot.slice(self._next_id("costlot"), take))
                    leftover = lot.quantity_kg - take
                    remainder = None
                    if leftover > VECTOR_TOLERANCE:
                        remainder = lot.slice(self._next_id("costlot"), leftover)
                        kept.append(remainder)
                    self._replace_product_lot_after_debit(lot.lot_id, remainder)
                    if requested is not None:
                        remaining -= take
                    continue
            kept.append(lot)
        self._active_lots = kept
        if strict and (not debits or (requested is not None and remaining > VECTOR_TOLERANCE)):
            raise ValueError(
                f"missing cost lot for {account}:{species} "
                f"quantity_kg={0.0 if requested is None else requested:.12g}"
            )
        return tuple(debits)

    def _replace_product_lot_after_debit(
        self,
        original_lot_id: str,
        remainder: CostLot | None,
    ) -> None:
        updated: list[CostLot] = []
        for lot in self._product_lots:
            if lot.lot_id == original_lot_id:
                if remainder is not None:
                    updated.append(remainder)
            else:
                updated.append(lot)
        self._product_lots = updated

    def _next_id(self, prefix: str) -> str:
        self._sequence += 1
        return f"{prefix}-{self._sequence:06d}"


def build_cost_rollup_diagnostic(
    *,
    cost_ledger: CostLedger,
    per_hour: tuple[dict[str, Any], ...],
    products_kg: Mapping[str, float],
    pumping_context: Mapping[str, Any] | None = None,
    snapshots: tuple[Any, ...] = (),
) -> dict[str, Any]:
    try:
        return _build_cost_rollup_diagnostic(
            cost_ledger=cost_ledger,
            per_hour=per_hour,
            products_kg=products_kg,
            pumping_context=pumping_context,
            snapshots=snapshots,
        )
    except Exception as exc:  # noqa: BLE001 -- cost diagnostics must not abort runner output
        cost_ledger._record_best_effort_error(
            "cost_observation_error",
            "build_cost_rollup_diagnostic",
            exc,
        )
        summary = cost_ledger._summary_fallback()
        summary["run_input_cost"] = {
            "thermal_proxy": "thermal_flux_h = absolute_temperature_K * duration_h",
            "physical_cost": ZERO_COST.to_json(),
            "allocation_status": "unavailable_after_cost_observation_error",
            "owner_ratify_money_projection": project_owner_ratify_money(ZERO_COST),
        }
        return summary


def _build_cost_rollup_diagnostic(
    *,
    cost_ledger: CostLedger,
    per_hour: tuple[dict[str, Any], ...],
    products_kg: Mapping[str, float],
    pumping_context: Mapping[str, Any] | None = None,
    snapshots: tuple[Any, ...] = (),
) -> dict[str, Any]:
    per_hour = _per_hour_with_snapshot_electrical_breakdown(per_hour, snapshots)
    summary = cost_ledger.summary()
    furnace_input = _run_furnace_input_cost(per_hour)
    pumping_enabled = isinstance(pumping_context, Mapping)
    pumping_input, pumping_diagnostic = (
        _run_pumping_input_cost(pumping_context)
        if pumping_enabled else (ZERO_COST, {})
    )
    auxiliary_electrical_input, auxiliary_electrical_components = (
        _run_auxiliary_electrical_input_cost(per_hour)
    )
    product_allocation_input = (
        furnace_input + pumping_input + auxiliary_electrical_input
    )
    product_inputs = _cost_allocation_product_inputs(products_kg)
    product_alloc = (
        _allocate_by_mass(product_inputs, product_allocation_input)
        if product_inputs else {}
    )
    product_costs = {
        key: dict(value)
        for key, value in summary["product_costs"].items()
    }
    for key in sorted(product_alloc, key=_output_sort_key):
        account, species = _normalize_output_key(key)
        matches = [
            product_key for product_key in product_costs
            if product_key.rsplit(":", 1)[-1] == species
        ]
        if not matches:
            matches = [f"{account}:{species}"]
            quantities = {matches[0]: float(product_inputs[key])}
        else:
            quantities = {
                product_key: max(
                    0.0,
                    float(product_costs[product_key].get("quantity_kg", 0.0)),
                )
                for product_key in matches
            }
            if sum(quantities.values()) <= VECTOR_TOLERANCE:
                quantities = {product_key: 1.0 for product_key in matches}
        split = _allocate_by_mass(quantities, product_alloc[key])
        for product_key, added_cost in split.items():
            existing = CostVector(**product_costs.get(product_key, {}).get("accumulated_cost", {}))
            total = existing + added_cost
            product_costs[product_key] = {
                "quantity_kg": quantities[product_key],
                "accumulated_cost": total.to_json(),
                "owner_ratify_money_projection": project_owner_ratify_money(total),
            }
    run_input_cost = {
        "thermal_proxy": "thermal_flux_h = absolute_temperature_K * duration_h",
        "physical_cost": furnace_input.to_json(),
        "allocation_status": (
            "allocated_by_product_mass"
            if product_inputs else "unallocated_no_product_mass"
        ),
        "owner_ratify_money_projection": project_owner_ratify_money(furnace_input),
    }
    if pumping_enabled:
        summary["pumping_diagnostic"] = {
            **pumping_diagnostic,
            "pumping_electrical_kWh": pumping_input.electrical_kWh,
            "status": pumping_diagnostic.get("status", "unknown"),
        }
    electrical_components = {
        **auxiliary_electrical_components,
        "pumping": auxiliary_electrical_components.get("pumping", 0.0)
        + pumping_input.electrical_kWh,
    }
    summary["auxiliary_electrical_diagnostic"] = {
        "schema_version": "auxiliary-electrical-rollup-v1",
        "components_kWh": {
            component: float(electrical_components.get(component, 0.0))
            for component in sorted(_AUXILIARY_ELECTRICAL_COMPONENT_ALIASES)
        },
        "auxiliary_electrical_kWh": float(
            sum(electrical_components.values())
        ),
    }
    summary["run_input_cost"] = run_input_cost
    if not product_inputs and not product_allocation_input.is_zero():
        summary["warnings"] = [
            *summary.get("warnings", []),
            "run_input_cost_unallocated_no_product_mass",
        ]
    summary["product_costs"] = product_costs
    return summary


def _cost_allocation_product_inputs(products_kg: Mapping[str, float]) -> dict[Any, float]:
    return _clean_outputs({
        ("terminal.product", species): kg
        for species, kg in dict(products_kg or {}).items()
        if not is_reagent_bookkeeping_product(species)
    })


def _run_furnace_input_cost(per_hour: tuple[dict[str, Any], ...]) -> CostVector:
    thermal_flux_h = 0.0
    furnace_h = 0.0
    for row in per_hour:
        if not isinstance(row, Mapping):
            continue
        temperature_C = _finite(row.get("T_C"), default=math.nan)
        if not math.isfinite(temperature_C):
            continue
        thermal_flux_h += furnace_thermal_flux_hours(temperature_C, 1.0)
        furnace_h += 1.0
    return CostVector(thermal_flux_h=thermal_flux_h, furnace_h=furnace_h)


def _per_hour_with_snapshot_electrical_breakdown(
    per_hour: tuple[dict[str, Any], ...],
    snapshots: tuple[Any, ...],
) -> tuple[dict[str, Any], ...]:
    try:
        snapshot_rows = tuple(snapshots or ())
    except TypeError:
        snapshot_rows = ()
    if not snapshot_rows:
        return tuple(per_hour)
    snapshots_by_hour: dict[int, Any] = {}
    for snapshot in snapshot_rows:
        hour = _finite(getattr(snapshot, "hour", math.nan), default=math.nan)
        if math.isfinite(hour):
            snapshots_by_hour[int(hour)] = snapshot

    rows: list[dict[str, Any]] = []
    for index, row in enumerate(tuple(per_hour)):
        if not isinstance(row, Mapping):
            rows.append(row)
            continue
        augmented = dict(row)
        if not any(field in augmented for field in _AUXILIARY_ELECTRICAL_BREAKDOWN_FIELDS):
            snapshot = _snapshot_for_per_hour_row(
                augmented,
                index,
                snapshot_rows,
                snapshots_by_hour,
            )
            breakdown = _electrical_breakdown_from_snapshot(snapshot)
            if breakdown is not None:
                augmented["energy_electrical_breakdown_kWh"] = breakdown
        rows.append(augmented)
    return tuple(rows)


def _snapshot_for_per_hour_row(
    row: Mapping[str, Any],
    index: int,
    snapshots: tuple[Any, ...],
    snapshots_by_hour: Mapping[int, Any],
) -> Any:
    hour = _finite(row.get("hour"), default=math.nan)
    if math.isfinite(hour) and int(hour) in snapshots_by_hour:
        return snapshots_by_hour[int(hour)]
    if index < len(snapshots):
        return snapshots[index]
    return None


def _electrical_breakdown_from_snapshot(snapshot: Any) -> dict[str, float] | None:
    energy = getattr(snapshot, "energy", None)
    if energy is None:
        return None
    return {
        "turbine_kWh": max(0.0, _finite(getattr(energy, "turbine_kWh", 0.0))),
        "condenser_kWh": max(0.0, _finite(getattr(energy, "condenser_kWh", 0.0))),
        "mre_kWh": max(0.0, _finite(getattr(energy, "mre_kWh", 0.0))),
    }


def _run_auxiliary_electrical_input_cost(
    per_hour: tuple[dict[str, Any], ...],
) -> tuple[CostVector, dict[str, float]]:
    components = {
        component: 0.0
        for component in _AUXILIARY_ELECTRICAL_COMPONENT_ALIASES
    }
    for row in per_hour:
        if not isinstance(row, Mapping):
            continue
        row_components = _auxiliary_electrical_from_breakdown(row)
        if row_components is not None:
            for component, value in row_components.items():
                components[component] += value
            continue
        campaign = str(row.get("campaign", "")).split(".")[-1].upper()
        if not campaign or campaign in MRE_ELECTRICAL_CAMPAIGNS:
            continue
        components["turbine"] += max(0.0, _finite(row.get("energy_electrical_kWh")))
    return CostVector(electrical_kWh=sum(components.values())), components


def _auxiliary_electrical_from_breakdown(
    row: Mapping[str, Any],
) -> dict[str, float] | None:
    for field in _AUXILIARY_ELECTRICAL_BREAKDOWN_FIELDS:
        breakdown = row.get(field)
        if isinstance(breakdown, Mapping):
            return _canonical_auxiliary_electrical_components(breakdown)
    if any(
        alias in row
        for aliases in _AUXILIARY_ELECTRICAL_COMPONENT_ALIASES.values()
        for alias in aliases
    ):
        return _canonical_auxiliary_electrical_components(row)
    return None


def _canonical_auxiliary_electrical_components(
    values: Mapping[str, Any],
) -> dict[str, float]:
    components: dict[str, float] = {}
    for component, aliases in _AUXILIARY_ELECTRICAL_COMPONENT_ALIASES.items():
        components[component] = 0.0
        for alias in aliases:
            if alias in values:
                components[component] = max(0.0, _finite(values.get(alias)))
                break
    return components


def _run_pumping_input_cost(
    pumping_context: Mapping[str, Any] | None,
) -> tuple[CostVector, dict[str, Any]]:
    parameter_metadata = [p.to_json() for p in pumping_cost_parameters()]
    if not isinstance(pumping_context, Mapping):
        return ZERO_COST, {
            "schema_version": "pumping-cost-rollup-v1",
            "status": "not_evaluated_no_pumping_context",
            "pumping_electrical_kWh": 0.0,
            "parameter_metadata": parameter_metadata,
            "rows": [],
        }
    ambient_pressure_pa = _finite(
        pumping_context.get("ambient_pressure_pa"),
        default=math.nan,
    )
    rows: list[dict[str, Any]] = []
    total_energy_kWh = 0.0
    all_feasible = True
    for raw_row in pumping_context.get("rows", ()) or ():
        if not isinstance(raw_row, Mapping):
            continue
        result = estimate_subambient_pump_cost(
            target_pressure_pa=_finite(raw_row.get("target_pressure_pa"), math.nan),
            offgas_mol_per_s=_finite(raw_row.get("offgas_mol_per_s"), math.nan),
            duration_s=_finite(raw_row.get("duration_s"), math.nan),
            ambient_pressure_pa=ambient_pressure_pa,
            gas_temperature_K=_finite(raw_row.get("gas_temperature_K"), math.nan),
        )
        total_energy_kWh += max(0.0, _finite(result.energy_kWh))
        all_feasible = all_feasible and bool(result.feasible)
        rows.append({
            "hour": int(_finite(raw_row.get("hour"), len(rows))),
            **result.to_json(),
        })
    cost = CostVector(electrical_kWh=total_energy_kWh)
    status = "ok"
    if not rows:
        status = "no_rows"
    elif not all_feasible:
        status = "infeasible_pumping_point"
    diagnostic = {
        "schema_version": "pumping-cost-rollup-v1",
        "status": status,
        "body": str(pumping_context.get("body", "")),
        "ambient_pressure_pa": ambient_pressure_pa,
        "ambient_pressure_source": str(
            pumping_context.get("ambient_pressure_source", "")
        ),
        "pumping_electrical_kWh": cost.electrical_kWh,
        "feasible": bool(all_feasible),
        "parameter_metadata": parameter_metadata,
        "rows": rows,
    }
    return cost, diagnostic


def _sum_cost(costs: Any) -> CostVector:
    total = ZERO_COST
    for cost in costs:
        total = total + cost
    return total


def _clean_outputs(outputs_kg: Mapping[Any, float]) -> dict[Any, float]:
    result: dict[Any, float] = {}
    for key, value in dict(outputs_kg or {}).items():
        quantity = _finite(value)
        if quantity > VECTOR_TOLERANCE:
            result[key] = result.get(key, 0.0) + quantity
    return result


def _allocate_by_mass(outputs_kg: Mapping[Any, float], cost: CostVector) -> dict[Any, CostVector]:
    outputs = _clean_outputs(outputs_kg)
    if not outputs:
        if cost.is_zero():
            return {}
        raise ValueError("cannot allocate non-zero cost without output masses")
    total = sum(outputs.values())
    return {key: cost.scale(quantity / total) for key, quantity in outputs.items()}


def _normalize_output_key(key: Any) -> tuple[str, str]:
    if isinstance(key, tuple) and len(key) == 2:
        return str(key[0]), str(key[1])
    return "product", str(key)


def _output_sort_key(key: Any) -> tuple[str, str]:
    return _normalize_output_key(key)


def _is_cost_bearing_account(account: str) -> bool:
    account = str(account)
    return account in COST_BEARING_ACCOUNTS or account.startswith("reservoir.reagent.")


def _outputs_from_lots(lots: tuple[Any, ...]) -> dict[tuple[str, str], float]:
    outputs: dict[tuple[str, str], float] = {}
    for lot in lots:
        for species, kg in lot.species_kg.items():
            key = (lot.account, species)
            outputs[key] = outputs.get(key, 0.0) + max(0.0, _finite(kg))
    return _clean_outputs(outputs)


def _split_beneficiary_outputs(lots: tuple[Any, ...]) -> tuple[dict[Any, float], dict[Any, float]]:
    beneficiary: dict[Any, float] = {}
    coproduct: dict[Any, float] = {}
    for lot in lots:
        target = beneficiary if _is_target_product_account(lot.account) else coproduct
        for species, kg in lot.species_kg.items():
            key = (lot.account, species)
            target[key] = target.get(key, 0.0) + max(0.0, _finite(kg))
    return _clean_outputs(beneficiary), _clean_outputs(coproduct)


def _is_target_product_account(account: str) -> bool:
    account = str(account)
    return (
        account == "process.metal_phase"
        or account == "process.condensation_train"
        or account.startswith("terminal.")
        and account not in {"terminal.slag"}
    )


def _process_step(intent: Any, controls: Mapping[str, Any]) -> str:
    intent_name = getattr(intent, "name", None) or getattr(intent, "value", None) or str(intent)
    family = controls.get("reaction_family")
    if family:
        suffix = str(family)
        if controls.get("back_reduction"):
            suffix = f"{suffix}:back_reduction"
        return f"{intent_name}:{suffix}"
    return str(intent_name)
