"""Manifest request rules, inventory-driven request builder, refusal closure.

DESIGN-REV5 §1.2 / §4.2 ordering for one flux step:

1. Construct the request set from compiler-emitted rules + ledger inventory only.
2. Monotone refusal closure to a fixed point (pending_validation is NOT refusal).
3. Build connected solve bundles from survivors.
4. Rank complete candidate sources per bundle (later VR chunks drive selection).

Request keys derive ONLY from the frozen U0 manifest + current eligible source
inventory. Answerability, preferred source, provider capability, and prior
success are forbidden inputs to the request projection.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

from simulator.alpha_kinetics import AlphaSpecError, parse_alpha_contract
from simulator.chemistry.melt_activity import (
    melt_oxide_activity_coefficient,
    single_cation_mole_fractions,
)
from simulator.vapour_rail.activity import (
    ActivityInputDeclaration,
    ActivityVerdictKind,
    CondensedPhaseActivityProvider,
    SourceReactionActivity,
    StandardStateIdentity,
)
from simulator.vapour_rail.batch import (
    CERTIFICATION_CEILING_NEVER,
    FLUX_ACTIVATION_EPOCH_PRE_RG,
    FLUX_ACTIVATION_EPOCH_RG_MANIFEST,
    VERDICT_STATUS_BEARING_NON_AUTHORITATIVE,
    FluxActivationContext,
    FluxDiagnosticUpperBound,
    FluxEligible,
    FluxRefusal,
    IncompleteVapourBatchError,
    PressureRefusal,
    PressureUpperBound,
    PressureValue,
    VapourAnswer,
    VapourBatch,
    VapourRequestConstructionError,
)
from simulator.vapour_rail.u0_manifest import (
    canonicalize_gas_id,
    load_u0_manifest,
)

if TYPE_CHECKING:
    from simulator.vapour_rail.catalog import CompiledSpecies

# Keep validation status tokens local so this module does not import catalog
# at runtime (catalog imports request for rule emission).
_VALIDATION_PENDING = "pending_validation"
_VALIDATION_VALIDATED = "validated"


# Inventory threshold: design uses ``> 0 mol`` (exact).
_INVENTORY_EPSILON = 0.0

# Refusal codes (stable strings for tests / instrumentation)
REFUSAL_INAPPLICABLE_PREDICATE = "inapplicable_by_declared_predicate"
REFUSAL_ABSENT_SOURCE_ATOM = "absent_source_atom"
REFUSAL_MISSING_CHANNEL_CONTRACT = "missing_channel_contract"
REFUSAL_NO_ADMITTED_SOURCE = "no_admitted_source_in_domain"
REFUSAL_PROVIDER_INDEPENDENT_INAPPLICABLE = "provider_independent_inapplicable"
REFUSAL_OMITTED_RULE = "omitted_request_rule"
# Outcome-determining process state missing (HI-8 / DESIGN-REV5 §1.2):
# never fabricate PressureValue(0.0) + FluxEligible as a stand-in.
REFUSAL_MISSING_OUTCOME_STATE = "missing_outcome_determining_state"
# t-571 chemical-potential channel refusals (design §9).  Distinct from the
# legacy formula/evaluator/alpha/route "channel contract" check below.
REFUSAL_CHEMICAL_POTENTIAL_CHANNEL = "refused_missing_channel_input"
REFUSAL_HALIDE_RESERVOIR_OWNER_MISSING = "refused_halide_reservoir_owner_missing"
REFUSAL_SULFUR_RESERVOIR_OWNER_MISSING = "refused_sulfur_reservoir_owner_missing"
REFUSAL_CHANNEL_RUNTIME_OWNER_MISSING = "refused_channel_runtime_owner_missing"

DEFAULT_SOURCE_ACCOUNT = "process.cleaned_melt"
DEFAULT_SOLVE_GROUP_PREFIX = "u0_v:"


class LedgerSnapshot(Protocol):
    """Structural view of source inventory for request activation."""

    def mol_by_account(
        self, account: str | None = None
    ) -> Mapping[str, Mapping[str, float]] | Mapping[str, float]:
        ...


@dataclass(frozen=True)
class RequestRule:
    """One compiler-emitted request edge for a canonical gas ID.

    Emitted for every executable U0 ``V`` row and every eligible ``C`` edge.
    Activation at runtime uses only source-account inventory presence — never
    answerability or provider preference.
    """

    species_id: str
    source_account: str
    parent_species_ids: frozenset[str]
    required_source_atoms: frozenset[str]
    solve_group_id: str
    applicability_predicate: str
    request_rule_kind: str
    origin: str  # "u0_v" | "u0_c_edge" | "catalog"
    formula_id: str
    source_reaction_id: str | None = None
    parent_oxide: str | None = None
    validation_status: str = _VALIDATION_PENDING
    validation_anchor_refs: tuple[str, ...] = ()
    has_pressure_evaluator: bool = False
    has_alpha: bool = False
    has_route: bool = False
    has_formula: bool = True
    evidence: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    source_reaction_activity: ActivityInputDeclaration | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "parent_species_ids", frozenset(self.parent_species_ids)
        )
        object.__setattr__(
            self, "required_source_atoms", frozenset(self.required_source_atoms)
        )
        object.__setattr__(
            self,
            "validation_anchor_refs",
            tuple(self.validation_anchor_refs),
        )
        object.__setattr__(
            self, "evidence", MappingProxyType(dict(self.evidence))
        )


@dataclass(frozen=True)
class ProviderDomainCandidate:
    """One admitted source candidate for a channel (provider-independent gate).

    A single candidate's domain miss does **not** create a step-2 refusal when
    another admitted candidate covers the requested state.
    """

    provider_id: str
    covers_state: Callable[[Mapping[str, Any]], bool]
    evidence_class: str = "analytical:external_grounded"


@dataclass(frozen=True)
class VapourResolveState:
    """Minimal process state for refusal closure / domain checks."""

    temperature_K: float | None = None
    process_phase: str | None = None  # e.g. "hot_train", "stage0"
    stage: str | None = None
    total_pressure_Pa: float | None = None
    fO2_bar: float | None = None
    extras: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    # ABI-safe tail: older callers may pass ``extras`` positionally.
    source_reaction_activities: Mapping[str, float] = field(
        default_factory=lambda: MappingProxyType({})
    )
    source_reaction_activity_provider: str | None = None
    source_reaction_activity_evidence_refs: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    source_reaction_activity_standard_states: Mapping[
        str, StandardStateIdentity
    ] = field(default_factory=lambda: MappingProxyType({}))
    source_reaction_fO2_bar: float | None = None
    source_reaction_activity_provenance: Mapping[str, Mapping[str, Any]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    # t-568 Phase 1 component-keyed typed shadow. Parallel scalar maps remain
    # legacy behavior authority until the separately gated adoption phase.
    source_reaction_activity_results: Mapping[str, SourceReactionActivity] = field(
        default_factory=lambda: MappingProxyType({})
    )
    source_reaction_fO2_log10: float | None = None
    source_reaction_activity_pressure_bar: float | None = None
    source_reaction_redox_model_id: str | None = None
    source_reaction_composition_wt_pct: Mapping[str, float] = field(
        default_factory=lambda: MappingProxyType({})
    )
    melt_activity_shadow_state_fingerprint: str | None = None
    melt_activity_shadow_inventory_digest: str | None = None
    # Explicit diagnostic opt-in: default request resolution pays no t-568
    # engine/registry/shadow cost. Inputs are consumed only when enabled.
    melt_activity_shadow_enabled: bool = False
    melt_activity_engine_inputs: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if not isinstance(self.melt_activity_shadow_enabled, bool):
            raise TypeError("melt_activity_shadow_enabled must be bool")
        object.__setattr__(
            self,
            "source_reaction_activities",
            MappingProxyType(dict(self.source_reaction_activities)),
        )
        object.__setattr__(
            self,
            "source_reaction_activity_evidence_refs",
            MappingProxyType(dict(self.source_reaction_activity_evidence_refs)),
        )
        object.__setattr__(
            self,
            "source_reaction_activity_standard_states",
            MappingProxyType(dict(self.source_reaction_activity_standard_states)),
        )
        object.__setattr__(
            self,
            "source_reaction_activity_provenance",
            MappingProxyType(
                {
                    str(species_id): MappingProxyType(dict(provenance))
                    for species_id, provenance in dict(
                        self.source_reaction_activity_provenance
                    ).items()
                }
            ),
        )
        object.__setattr__(
            self,
            "source_reaction_activity_results",
            MappingProxyType(dict(self.source_reaction_activity_results)),
        )
        object.__setattr__(
            self,
            "source_reaction_composition_wt_pct",
            MappingProxyType(dict(self.source_reaction_composition_wt_pct)),
        )
        object.__setattr__(
            self,
            "melt_activity_engine_inputs",
            MappingProxyType(dict(self.melt_activity_engine_inputs)),
        )
        object.__setattr__(self, "extras", MappingProxyType(dict(self.extras)))

    def as_mapping(self) -> dict[str, Any]:
        return {
            "temperature_K": self.temperature_K,
            "process_phase": self.process_phase,
            "stage": self.stage,
            "total_pressure_Pa": self.total_pressure_Pa,
            "fO2_bar": self.fO2_bar,
            "source_reaction_activity_provider": (
                self.source_reaction_activity_provider
            ),
            "source_reaction_activities": dict(self.source_reaction_activities),
            "source_reaction_activity_evidence_refs": dict(
                self.source_reaction_activity_evidence_refs
            ),
            "source_reaction_activity_standard_states": dict(
                self.source_reaction_activity_standard_states
            ),
            "source_reaction_activity_provenance": {
                species_id: dict(provenance)
                for species_id, provenance in self.source_reaction_activity_provenance.items()
            },
            "source_reaction_activity_results": dict(
                self.source_reaction_activity_results
            ),
            "source_reaction_fO2_bar": self.source_reaction_fO2_bar,
            "source_reaction_fO2_log10": self.source_reaction_fO2_log10,
            "source_reaction_activity_pressure_bar": (
                self.source_reaction_activity_pressure_bar
            ),
            "source_reaction_redox_model_id": self.source_reaction_redox_model_id,
            "source_reaction_composition_wt_pct": dict(
                self.source_reaction_composition_wt_pct
            ),
            "melt_activity_shadow_state_fingerprint": (
                self.melt_activity_shadow_state_fingerprint
            ),
            "melt_activity_shadow_inventory_digest": (
                self.melt_activity_shadow_inventory_digest
            ),
            "melt_activity_shadow_enabled": self.melt_activity_shadow_enabled,
            "melt_activity_engine_inputs": dict(self.melt_activity_engine_inputs),
            **dict(self.extras),
        }


def _account_mols(
    ledger: Mapping[str, Any] | LedgerSnapshot,
    account: str,
) -> dict[str, float]:
    if hasattr(ledger, "mol_by_account") and not isinstance(ledger, Mapping):
        raw = ledger.mol_by_account(account)  # type: ignore[union-attr]
        if raw is None:
            return {}
        if not isinstance(raw, Mapping):
            # Unreadable inventory is unknown, not empty — reading it as {}
            # would drop every parent and silence evolution for the account.
            raise VapourRequestConstructionError(
                f"unreadable inventory account {account!r}: expected mapping, "
                f"got {type(raw).__name__}"
            )
        # Single-account view: species -> mol
        if raw and all(not isinstance(v, Mapping) for v in raw.values()):
            return {
                str(k): _require_readable_mol(v, species_id=str(k), account=account)
                for k, v in raw.items()
            }
        nested = raw.get(account) if account in raw else raw
        if isinstance(nested, Mapping):
            return {
                str(k): _require_readable_mol(v, species_id=str(k), account=account)
                for k, v in nested.items()
            }
        if nested is None:
            return {}
        raise VapourRequestConstructionError(
            f"unreadable inventory account {account!r}: nested view is "
            f"{type(nested).__name__}, not a species→mol mapping"
        )

    if not isinstance(ledger, Mapping):
        raise VapourRequestConstructionError(
            f"unreadable ledger for account {account!r}: expected mapping or "
            f"LedgerSnapshot, got {type(ledger).__name__}"
        )
    # Full ledger: account -> species -> mol
    if account in ledger and isinstance(ledger[account], Mapping):
        return {
            str(k): _require_readable_mol(v, species_id=str(k), account=account)
            for k, v in ledger[account].items()
        }
    if account in ledger and ledger[account] is not None:
        raise VapourRequestConstructionError(
            f"unreadable inventory account {account!r}: expected mapping, "
            f"got {type(ledger[account]).__name__}"
        )
    # Flat species map (already sliced to one account by caller)
    if ledger and all(not isinstance(v, Mapping) for v in ledger.values()):
        return {
            str(k): _require_readable_mol(v, species_id=str(k), account=account)
            for k, v in ledger.items()
        }
    return {}


def _require_readable_mol(
    value: Any,
    *,
    species_id: str,
    account: str,
) -> float:
    """Parse one inventory amount; refuse unreadable values (not zero)."""
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise VapourRequestConstructionError(
            f"unreadable inventory for {species_id!r} in {account!r}: "
            f"{value!r}; unknown inventory is not zero inventory"
        ) from exc
    if not math.isfinite(amount):
        raise VapourRequestConstructionError(
            f"non-finite inventory for {species_id!r} in {account!r}: "
            f"{value!r}; unknown inventory is not zero inventory"
        )
    return amount


def _positive_mol(mols: Mapping[str, float], species_id: str) -> bool:
    if species_id not in mols:
        return False
    try:
        amount = float(mols[species_id])
    except (TypeError, ValueError) as exc:
        raise VapourRequestConstructionError(
            f"unreadable inventory for {species_id!r}: {mols[species_id]!r}; "
            "unknown inventory is not zero inventory"
        ) from exc
    if not math.isfinite(amount):
        raise VapourRequestConstructionError(
            f"non-finite inventory for {species_id!r}: {mols[species_id]!r}; "
            "unknown inventory is not zero inventory"
        )
    return amount > _INVENTORY_EPSILON


def _formula_elements(formula: str | None) -> frozenset[str]:
    if not formula:
        return frozenset()

    import re

    cleaned = re.sub(r"\([^)]*\)$", "", formula.strip())
    matches = list(re.finditer(r"([A-Z][a-z]?)", cleaned))
    if not matches:
        return frozenset()
    return frozenset(match.group(1) for match in matches)


def _alpha_contract_available(alpha: Any) -> bool:
    """True only when the shared parser proves an executable alpha value."""

    try:
        return parse_alpha_contract(alpha) is not None
    except AlphaSpecError:
        return False


def emit_request_rules(
    *,
    catalog_species: Mapping[str, Any],
    u0_manifest: Mapping[str, Any] | None = None,
    catalog_payload: Mapping[str, Any] | None = None,
) -> tuple[RequestRule, ...]:
    """Emit one request rule per executable U0 V row and eligible C edge.

    Catalog live rows supply channel contracts (evaluator, alpha, route,
    parent oxide / source reactions). U0 V rows without a catalog contract still
    receive a rule so they cannot be unrequested by construction; refusal
    closure then types the missing contract.
    """

    manifest = u0_manifest if u0_manifest is not None else load_u0_manifest()
    species_rows = list(manifest.get("species") or [])
    u0_by_id = {str(row["id"]): row for row in species_rows if "id" in row}

    # Parent / reaction metadata from the raw schema-v2 payload (not only
    # CompiledSpecies), so parent oxides and C-edge reactants stay available.
    raw_parents: dict[str, dict[str, Any]] = {}
    if isinstance(catalog_payload, Mapping):
        families = catalog_payload.get("families") or {}
        if isinstance(families, Mapping):
            for family in families.values():
                if not isinstance(family, Mapping):
                    continue
                physical = family.get("physical_properties") or {}
                code = family.get("code_metadata") or {}
                species_map = (
                    physical.get("species") if isinstance(physical, Mapping) else {}
                )
                if not isinstance(species_map, Mapping):
                    continue
                for sid, row in species_map.items():
                    if not isinstance(row, Mapping):
                        continue
                    raw_parents[str(sid)] = {
                        "row": row,
                        "code": code if isinstance(code, Mapping) else {},
                    }

    rules: dict[str, RequestRule] = {}

    def _upsert(rule: RequestRule) -> None:
        existing = rules.get(rule.species_id)
        if existing is None:
            rules[rule.species_id] = rule
            return
        # Prefer catalog-origin contracts over bare U0 stubs; merge parents.
        prefer_new = (
            rule.origin == "catalog"
            and existing.origin != "catalog"
        ) or (
            rule.has_pressure_evaluator and not existing.has_pressure_evaluator
        )
        base = rule if prefer_new else existing
        other = existing if prefer_new else rule
        rules[rule.species_id] = replace(
            base,
            parent_species_ids=base.parent_species_ids | other.parent_species_ids,
            required_source_atoms=(
                base.required_source_atoms | other.required_source_atoms
            ),
            has_pressure_evaluator=(
                base.has_pressure_evaluator or other.has_pressure_evaluator
            ),
            has_alpha=base.has_alpha or other.has_alpha,
            has_route=base.has_route or other.has_route,
            has_formula=base.has_formula or other.has_formula,
            source_reaction_activity=(
                base.source_reaction_activity or other.source_reaction_activity
            ),
        )

    # --- Catalog species (channel contracts + live request rules) ----------
    for species_id, compiled in catalog_species.items():
        raw = raw_parents.get(species_id, {})
        row = raw.get("row") if isinstance(raw.get("row"), Mapping) else {}
        code = compiled.code_metadata
        parent_oxide = None
        if isinstance(row, Mapping):
            parent_oxide = row.get("parent_oxide")
            if parent_oxide is not None:
                parent_oxide = str(parent_oxide)

        parents: set[str] = set()
        required_atoms: set[str] = set()
        reaction_id: str | None = None
        if isinstance(row, Mapping):
            reactions = row.get("source_reactions") or []
            if isinstance(reactions, list):
                for reaction in reactions:
                    if not isinstance(reaction, Mapping):
                        continue
                    rid = reaction.get("id")
                    if reaction_id is None and isinstance(rid, str):
                        reaction_id = rid
                    for participant in reaction.get("reactants") or []:
                        if not isinstance(participant, Mapping):
                            continue
                        formula = participant.get("formula")
                        if isinstance(formula, str) and formula.strip():
                            parents.add(formula.strip())
                            required_atoms |= set(_formula_elements(formula))
        if parent_oxide:
            parents.add(parent_oxide)
            required_atoms |= set(_formula_elements(parent_oxide))
        if compiled.source_reaction_id is not None:
            reaction_id = compiled.source_reaction_id
        # Carrier-is-own-vapor (halides): parent is the gas formula itself.
        if not parents:
            parents.add(compiled.formula or species_id)
            required_atoms |= set(_formula_elements(compiled.formula or species_id))

        alpha = compiled.vaporisation_coefficients.evaporation_alpha
        has_alpha = _alpha_contract_available(alpha)
        has_route = bool(
            compiled.fiat_routing.process_or_terminal_destination
            or compiled.fiat_routing.engineering_capture_policy
        )
        _upsert(
            RequestRule(
                species_id=species_id,
                source_account=code.source_account,
                parent_species_ids=frozenset(parents),
                required_source_atoms=frozenset(required_atoms),
                solve_group_id=code.solve_group_id,
                applicability_predicate=code.hot_train_applicability,
                request_rule_kind=code.request_rule,
                origin="catalog",
                formula_id=code.formula_id or compiled.formula or species_id,
                source_reaction_id=reaction_id,
                parent_oxide=parent_oxide,
                validation_status=compiled.validation_status.value,
                validation_anchor_refs=tuple(
                    getattr(compiled, "validation_anchor_refs", ()) or ()
                ),
                has_pressure_evaluator=compiled.evaluator is not None,
                has_alpha=has_alpha,
                has_route=has_route,
                has_formula=bool(compiled.formula),
                evidence=MappingProxyType(
                    {
                        "family_id": compiled.family_id,
                        "request_rule": code.request_rule,
                    }
                ),
                source_reaction_activity=compiled.source_reaction_activity,
            )
        )

    # --- U0 V rows: every executable vapour channel gets a rule ------------
    for row in species_rows:
        disposition = str(row.get("disposition") or "")
        species_id = str(row["id"])
        if disposition != "V":
            continue
        # Canonical gas ID (collision suffix already present in U0 ids).
        gas_id = canonicalize_gas_id(species_id, treat_as_gas=True)
        if gas_id in rules and rules[gas_id].origin == "catalog":
            # Still record U0 coverage; parents already set from catalog.
            continue
        formula = row.get("formula")
        formula_s = str(formula) if formula else gas_id
        atoms = row.get("atoms") if isinstance(row.get("atoms"), Mapping) else {}
        required = frozenset(str(a) for a in atoms.keys()) or _formula_elements(
            formula_s
        )
        parents = frozenset({formula_s, gas_id})
        flags = set(row.get("flags") or [])
        applicability = "applicable"
        if "diagnostic_only" in flags or "tranche_2_do_not_promote" in flags:
            applicability = "not_applicable"
        _upsert(
            RequestRule(
                species_id=gas_id,
                source_account=DEFAULT_SOURCE_ACCOUNT,
                parent_species_ids=parents,
                required_source_atoms=required,
                solve_group_id=f"{DEFAULT_SOLVE_GROUP_PREFIX}{gas_id}",
                applicability_predicate=applicability,
                request_rule_kind="source_inventory_present",
                origin="u0_v",
                formula_id=formula_s,
                validation_status=str(
                    row.get("validation_status") or _VALIDATION_PENDING
                ),
                validation_anchor_refs=tuple(
                    str(a) for a in (row.get("validation_anchor_refs") or [])
                ),
                has_pressure_evaluator=False,
                has_alpha=False,
                has_route=False,
                has_formula=bool(formula),
                evidence=MappingProxyType(
                    {
                        "u0_disposition": "V",
                        "feedstock_presence": bool(row.get("feedstock_presence")),
                        "flags": sorted(flags),
                    }
                ),
            )
        )

    # --- Eligible C edges: C rows that feed a catalog source reaction ------
    c_ids = {
        str(row["id"])
        for row in species_rows
        if str(row.get("disposition") or "") == "C"
    }
    # Also treat parent_oxide / reactant formulas that match U0 C as edges.
    for species_id, compiled in catalog_species.items():
        raw = raw_parents.get(species_id, {})
        row = raw.get("row") if isinstance(raw.get("row"), Mapping) else {}
        parent_candidates: set[str] = set()
        if isinstance(row, Mapping):
            po = row.get("parent_oxide")
            if isinstance(po, str) and po.strip():
                parent_candidates.add(po.strip())
            for reaction in row.get("source_reactions") or []:
                if not isinstance(reaction, Mapping):
                    continue
                for participant in reaction.get("reactants") or []:
                    if isinstance(participant, Mapping):
                        formula = participant.get("formula")
                        if isinstance(formula, str) and formula.strip():
                            parent_candidates.add(formula.strip())
        edge_parents = parent_candidates & (c_ids | parent_candidates)
        # Edge is eligible when any parent is a U0 C carrier (or condensed
        # oxide that appears as a C-disposition id).
        eligible = parent_candidates & c_ids
        if not eligible:
            continue
        existing = rules.get(species_id)
        if existing is not None:
            rules[species_id] = replace(
                existing,
                parent_species_ids=existing.parent_species_ids | frozenset(eligible),
                origin=(
                    existing.origin
                    if existing.origin == "catalog"
                    else "u0_c_edge"
                ),
                evidence=MappingProxyType(
                    {
                        **dict(existing.evidence),
                        "c_edge_parents": sorted(eligible),
                    }
                ),
            )
        else:
            code = compiled.code_metadata
            _upsert(
                RequestRule(
                    species_id=species_id,
                    source_account=code.source_account,
                    parent_species_ids=frozenset(eligible),
                    required_source_atoms=frozenset(
                        a
                        for p in eligible
                        for a in _formula_elements(p)
                    ),
                    solve_group_id=code.solve_group_id,
                    applicability_predicate=code.hot_train_applicability,
                    request_rule_kind=code.request_rule,
                    origin="u0_c_edge",
                    formula_id=code.formula_id or compiled.formula or species_id,
                    has_pressure_evaluator=compiled.evaluator is not None,
                    has_alpha=_alpha_contract_available(
                        compiled.vaporisation_coefficients.evaporation_alpha
                    ),
                    has_route=bool(
                        compiled.fiat_routing.process_or_terminal_destination
                    ),
                    has_formula=bool(compiled.formula),
                    evidence=MappingProxyType(
                        {"c_edge_parents": sorted(eligible)}
                    ),
                )
            )

    # Completeness: every U0 V must have an emitted rule.
    missing_v = sorted(
        str(row["id"])
        for row in species_rows
        if str(row.get("disposition") or "") == "V"
        and canonicalize_gas_id(str(row["id"]), treat_as_gas=True) not in rules
    )
    if missing_v:
        raise VapourRequestConstructionError(
            "compiler omitted request rules for executable U0 V rows: "
            f"{missing_v[:20]}{'...' if len(missing_v) > 20 else ''}"
        )

    # Silence unused u0_by_id lint while keeping the map for future edge work.
    _ = u0_by_id

    return tuple(sorted(rules.values(), key=lambda r: r.species_id))


def build_request(
    rules: Sequence[RequestRule],
    ledger_snapshot: Mapping[str, Any] | LedgerSnapshot,
    *,
    caller_species_filter: Sequence[str] | None = None,
) -> frozenset[str]:
    """Project the active request set from rules + ledger inventory only.

    ``caller_species_filter`` is rejected when provided: callers may not
    narrow the request set to answerable-only or live-row-only projections.
    """

    if caller_species_filter is not None:
        raise VapourRequestConstructionError(
            "callers must not construct or narrow requested_species_ids; "
            "VapourRailCatalog.build_request is the sole request constructor "
            f"(rejected filter={list(caller_species_filter)!r})"
        )

    requested: set[str] = set()
    for rule in rules:
        mols = _account_mols(ledger_snapshot, rule.source_account)
        if any(_positive_mol(mols, parent) for parent in rule.parent_species_ids):
            requested.add(rule.species_id)
    return frozenset(requested)


def assert_request_coverage(
    rules: Sequence[RequestRule],
    ledger_snapshot: Mapping[str, Any] | LedgerSnapshot,
    requested: frozenset[str],
) -> None:
    """Hard-fail if a physically eligible rule was omitted from the builder result."""

    rules_by_id = {rule.species_id: rule for rule in rules}
    # Every rule with inventory must appear in requested.
    for rule in rules:
        mols = _account_mols(ledger_snapshot, rule.source_account)
        eligible = any(
            _positive_mol(mols, parent) for parent in rule.parent_species_ids
        )
        if eligible and rule.species_id not in requested:
            raise VapourRequestConstructionError(
                f"eligible request rule {rule.species_id!r} omitted from "
                "builder result (inventory present for "
                f"parents={sorted(rule.parent_species_ids)} in "
                f"{rule.source_account})"
            )
    # Requested IDs must come from rules only.
    unknown = sorted(requested - frozenset(rules_by_id))
    if unknown:
        raise VapourRequestConstructionError(
            f"request set contains IDs without compiler rules: {unknown}"
        )


def _predicate_active(
    rule: RequestRule,
    state: VapourResolveState | None,
) -> tuple[bool, str]:
    """Return (active, detail). Inactive → refusal, never omission."""

    predicate = rule.applicability_predicate
    stage = (state.stage if state else None) or ""
    if stage == "c0b_p_cleanup" and "P2O5" not in rule.parent_species_ids:
        return (
            False,
            "c0b_p_cleanup admits only P2O5-sourced carrier rules",
        )
    if predicate in {"applicable", "always"}:
        return True, ""
    if predicate in {"not_applicable", "inapplicable"}:
        return (
            False,
            f"declared applicability predicate {predicate!r} is inactive",
        )
    if predicate == "stage0_only":
        phase = (state.process_phase if state else None) or ""
        if phase == "stage0" or stage == "stage0":
            return True, ""
        # Stage-0 P carriers run inside the legacy hot-train request surface.
        # Wake only P2O5-sourced rules; treating the entire request as stage0
        # changes unrelated volatile-channel eligibility.
        if stage in {"stage0_p_carriers", "c0b_p_cleanup"} and (
            "P2O5" in rule.parent_species_ids
        ):
            return True, ""
        return (
            False,
            "stage0_only predicate inactive outside stage0 "
            f"(process_phase={phase!r}, stage={stage!r})",
        )
    # Unknown predicates fail closed as inactive with evidence.
    return False, f"unrecognized applicability predicate {predicate!r}"


def _executable_contract_refusal(rule: RequestRule) -> str | None:
    """Provider-independent missing-contract check (step 2).

    Renamed from ``_channel_contract_refusal`` (t-571 design §2.1): this gate
    checks formula / evaluator / alpha / route completeness.  It is **not** a
    chemical-potential channel gate — those refusals live in
    :mod:`simulator.vapour_rail.channels` and
    :func:`chemical_potential_channel_refusal`.
    """

    missing: list[str] = []
    if not rule.has_formula:
        missing.append("formula")
    if not rule.has_pressure_evaluator:
        missing.append("pressure_evaluator")
    if not rule.has_alpha:
        missing.append("alpha")
    if not rule.has_route:
        missing.append("route")
    # Source reaction is required only when the rule kind expects one.
    if rule.source_reaction_id is None and rule.parent_oxide and rule.origin == "catalog":
        # Antoine pure-metal paths may omit reaction; not a hard miss.
        pass
    if missing:
        return (
            "channel contract incomplete: missing "
            + ", ".join(missing)
        )
    return None


# Backward-compatible alias (pre-t-571 name).
_channel_contract_refusal = _executable_contract_refusal


def chemical_potential_channel_refusal(
    *,
    carrier: str,
    element: str | None = None,
    pathway: str | None = None,
    missing_text: str | None = None,
    required_channels: Sequence[str] | None = None,
    temperature_K: float | None = 1800.0,
) -> tuple[str, str] | None:
    """t-571 admission rule: typed refusal for unowned chemical-potential channels.

    Returns ``(refusal_code, detail)`` when composition cannot proceed, else
    None.  Names both the missing channel IDs and the missing melt-side owner
    (BaF → F2 + halide_reservoir_owner_missing).  No Rev-3 bypass.
    """

    from simulator.vapour_rail.channels import (
        ChannelCompositionRefusal,
        attempt_channel_composition,
    )

    result = attempt_channel_composition(
        carrier=carrier,
        element=element,
        pathway=pathway,
        missing_text=missing_text,
        required_channels=required_channels,
        temperature_K=temperature_K,
    )
    if isinstance(result, ChannelCompositionRefusal):
        detail = (
            f"missing_channels={list(result.missing_channels)}; "
            f"missing_melt_owners={list(result.missing_melt_owners)}; "
            f"{result.detail}"
        )
        return result.disposition, detail
    return None


def _chemical_potential_channel_gate(
    rule: RequestRule,
    compiled: Any,
    state: VapourResolveState | None,
) -> tuple[str, str] | None:
    """Step-2 gate: non-O2 exchange-channel terms resolve or typed-refuse.

    t-571 design §9 admission criterion 5: every gas exchange participant
    must resolve through the channel resolver to a usable typed potential.
    Phase 1 owns O2 only, so a compiled evaluator carrying a non-O2
    :class:`CompiledReactionTerm` (an ``exchange_channel_bindings`` row)
    composes through :func:`chemical_potential_channel_refusal` and the
    closure emits the owner-specific refusal code —
    ``refused_halide_reservoir_owner_missing`` etc. — naming the missing
    channel **and** the missing melt-side owner.  Without this gate the same
    row fell through to ``_make_live`` and surfaced as the generic
    ``missing_channel_contract`` evaluator failure (P1 review: refusal
    unwired).  Returns ``(refusal_code, detail)`` or None.
    """

    from simulator.vapour_rail.channels import (
        CHANNEL_O2,
        ReactionTermRole,
    )

    evaluator = getattr(compiled, "evaluator", None) if compiled is not None else None
    terms = tuple(getattr(evaluator, "reaction_terms", ()) or ())
    required_channels: list[str] = []
    for term in terms:
        if getattr(term, "role", None) is not ReactionTermRole.EXCHANGE_CHANNEL:
            continue
        input_id = getattr(term, "input_id", None)
        if input_id is None or input_id == CHANNEL_O2:
            # O2 is Phase-1 owned; its point/resolution path is the live
            # evaluator itself (pO2_bar / reaction_inputs).
            continue
        required_channels.append(str(input_id))
    if not required_channels:
        return None
    return chemical_potential_channel_refusal(
        carrier=rule.species_id,
        element=None,
        pathway="catalog_exchange_channel_binding",
        required_channels=tuple(sorted(dict.fromkeys(required_channels))),
        temperature_K=state.temperature_K if state is not None else None,
    )


def _absent_source_atom_detail(
    rule: RequestRule,
    ledger_snapshot: Mapping[str, Any] | LedgerSnapshot,
) -> str | None:
    """If required reactant inventory is incomplete, refuse (keep in batch)."""

    mols = _account_mols(ledger_snapshot, rule.source_account)
    # Prefer explicit parent-species presence for every parent when the rule
    # lists multiple required parents from a source reaction.
    missing_parents = [
        parent
        for parent in sorted(rule.parent_species_ids)
        if not _positive_mol(mols, parent)
    ]
    # Activation only needs *any* parent; closure requires the full reactant
    # set when a multi-reactant edge is declared via required_source_atoms
    # that cannot be satisfied by present inventory elements.
    if not rule.required_source_atoms:
        return None

    # Credit elements only via real formula parse (or exact species-id token).
    # Never use substring ``atom in species_id`` — that fail-opens F∈Fe2O3,
    # N∈NaCl, C∈CaO, etc. (kimi P1-2).
    present_elements: set[str] = set()
    for species_id, amount in mols.items():
        if amount > _INVENTORY_EPSILON:
            present_elements |= set(_formula_elements(species_id))
            present_elements.add(species_id)

    missing_atoms = sorted(
        atom
        for atom in rule.required_source_atoms
        if atom not in present_elements
    )
    # If at least one parent is present and all required atoms are covered by
    # that parent formula, accept. Only refuse when atoms are truly absent.
    if missing_atoms:
        return (
            f"required source atom(s) absent from {rule.source_account}: "
            f"{missing_atoms} (missing_parents={missing_parents})"
        )
    return None


def _candidates_cover_state(
    candidates: Sequence[ProviderDomainCandidate] | None,
    state: VapourResolveState | None,
) -> bool:
    if not candidates:
        # No provider-domain candidates declared → domain is not a step-2 gate
        # (literature evaluators with conservative continuation cover state).
        return True
    state_map = state.as_mapping() if state is not None else {}
    return any(candidate.covers_state(state_map) for candidate in candidates)


@dataclass(frozen=True)
class RefusalClosureResult:
    """Answers plus whether the closure loop truly reached a fixed point."""

    answers: Mapping[str, VapourAnswer]
    reached_fixed_point: bool
    iterations: int = 0


def refusal_closure(
    *,
    requested: frozenset[str],
    rules: Sequence[RequestRule],
    ledger_snapshot: Mapping[str, Any] | LedgerSnapshot,
    state: VapourResolveState | None = None,
    provider_candidates_by_species: Mapping[
        str, Sequence[ProviderDomainCandidate]
    ]
    | None = None,
    catalog_species: Mapping[str, Any] | None = None,
    activity_provider: CondensedPhaseActivityProvider | None = None,
) -> RefusalClosureResult:
    """Monotone refusal closure to a fixed point (DESIGN-REV5 §4.2 step 2).

    Refused channels stay in ``channels_by_species``. Pending validation is
    never a refusal reason. One provider's domain miss cannot create a refusal
    while another admitted candidate covers the state.

    Inter-channel refusal cascade (association/mass-action dependents) is not
    implemented yet — predicates are channel-local — so the fixed point is
    typically reached in one pass. The ``reached_fixed_point`` flag is computed
    honestly from ``not changed``, not hardcoded.
    """

    rules_by_id = {rule.species_id: rule for rule in rules}
    missing_rules = sorted(requested - frozenset(rules_by_id))
    if missing_rules:
        raise VapourRequestConstructionError(
            f"requested species lack compiler request rules: {missing_rules}"
        )

    candidates_map = dict(provider_candidates_by_species or {})
    catalog_species = catalog_species or {}
    activity_provider = activity_provider or CondensedPhaseActivityProvider()

    # Seed answers as non-refused placeholders; iterate to fixed point.
    answers: dict[str, VapourAnswer] = {}
    refused: set[str] = set()

    def _make_refusal(
        rule: RequestRule,
        code: str,
        detail: str,
        *,
        source_reaction_activity: SourceReactionActivity | None = None,
    ) -> VapourAnswer:
        return VapourAnswer(
            species_id=rule.species_id,
            pressure=PressureRefusal(code=code, detail=detail),
            selected_runtime_pressure=PressureRefusal(code=code, detail=detail),
            flux=FluxRefusal(code=code, detail=detail),
            source_label="refusal_closure",
            formula_id=rule.formula_id,
            source_account=rule.source_account,
            solve_group_id=rule.solve_group_id,
            state_fingerprint=_state_fingerprint(state),
            validation_status=rule.validation_status,
            validation_anchor_refs=rule.validation_anchor_refs,
            verdict_status=VERDICT_STATUS_BEARING_NON_AUTHORITATIVE,
            certification_ceiling=CERTIFICATION_CEILING_NEVER,
            refusal_code=code,
            extra=MappingProxyType({"detail": detail, "origin": rule.origin}),
            source_reaction_activity=source_reaction_activity,
        )

    def _outcome_state_refusal_detail(rule: RequestRule) -> str | None:
        """Missing state that would determine the pressure answer → typed refuse.

        DESIGN-REV5 §1.2 / HI-8: never emit ``PressureValue(0.0)`` +
        ``FluxEligible`` as a stand-in for "could not evaluate". A deferred
        or missing temperature/evaluator is a channel-local refusal that
        remains in the exact-key batch and never joins a solve bundle.
        """

        if state is None or state.temperature_K is None:
            return (
                "outcome-determining temperature_K absent from resolve state "
                f"(species={rule.species_id})"
            )
        compiled = catalog_species.get(rule.species_id)
        if compiled is None or compiled.evaluator is None:
            # Contract flags may claim an evaluator exists while the live
            # catalog_species map was not supplied (or evaluator is pending).
            if rule.has_pressure_evaluator:
                return (
                    "no live channel evaluator available for point evaluation "
                    f"(species={rule.species_id})"
                )
        return None

    def _make_live(rule: RequestRule) -> VapourAnswer:
        compiled = catalog_species.get(rule.species_id)
        pressure: PressureValue | PressureUpperBound | PressureRefusal
        flux: FluxEligible | FluxDiagnosticUpperBound | FluxRefusal
        source_label = "catalog"
        pressure_pa = None
        source_reaction_activity: SourceReactionActivity | None = None
        source_reaction_activity_shadow: SourceReactionActivity | None = None
        typed_activity_evaluation: Mapping[str, Any] | None = None
        declaration: ActivityInputDeclaration | None = None
        # b-149 instance 7: source_activity defaulted to 1.0 before the
        # activity-dependent branch (REAL under b-119). Pure-component
        # (exponent==0) legitimately uses a=1; activity-dependent paths
        # must resolve or refuse. Instrument the pure-component default so
        # the unit activity is typed, not silent.
        source_activity = 1.0
        _unit_activity_note: dict[str, Any] | None = None
        if (
            compiled is not None
            and compiled.evaluator is not None
            and state is not None
            and state.temperature_K is not None
        ):
            evaluator_activity_exponent = float(
                getattr(compiled.evaluator, "activity_exponent", 0.0) or 0.0
            )
            if evaluator_activity_exponent != 0.0:
                declaration = rule.source_reaction_activity
                if declaration is None:
                    return _make_refusal(
                        rule,
                        REFUSAL_MISSING_CHANNEL_CONTRACT,
                        "activity-dependent pressure model lacks an explicit "
                        "source_reactions[].activity_input declaration; silent "
                        "pure-component substitution is forbidden",
                    )
                source_reaction_activity_shadow = (
                    state.source_reaction_activity_results.get(
                        declaration.component_id
                    )
                )
                reported_activity = state.source_reaction_activities.get(
                    rule.species_id
                )
                evidence_ref = state.source_reaction_activity_evidence_refs.get(
                    rule.species_id
                )
                reported_standard_state = (
                    state.source_reaction_activity_standard_states.get(
                        rule.species_id
                    )
                )
                reported_provenance = state.source_reaction_activity_provenance.get(
                    rule.species_id, {}
                )
                reported_mole_fraction = reported_provenance.get(
                    "melt_oxide_X_single_cation"
                )
                if reported_mole_fraction is None:
                    coeff = melt_oxide_activity_coefficient(
                        declaration.component_id
                    )
                    if coeff is not None:
                        source_fractions = single_cation_mole_fractions(
                            _account_mols(ledger_snapshot, rule.source_account)
                        )
                        reported_mole_fraction = source_fractions.get(
                            coeff.parent_oxide
                        )
                source_reaction_activity = (
                    activity_provider.resolve_source_reaction_activity(
                        declaration,
                        magemin=None,
                        thermoengine=None,
                        activity_exponent=evaluator_activity_exponent,
                        solve_group_id=rule.solve_group_id,
                        state_fingerprint=_state_fingerprint(state),
                        mole_fraction=reported_mole_fraction,
                        reported_activity=reported_activity,
                        reported_activity_provider=(
                            state.source_reaction_activity_provider
                        ),
                        reported_activity_evidence_ref=evidence_ref,
                        reported_activity_standard_state=reported_standard_state,
                        reported_activity_provenance=reported_provenance,
                    )
                )
                if source_reaction_activity.verdict is ActivityVerdictKind.REFUSAL:
                    refusal_code = (
                        source_reaction_activity.refusal_code.value
                        if source_reaction_activity.refusal_code is not None
                        else REFUSAL_MISSING_CHANNEL_CONTRACT
                    )
                    return _make_refusal(
                        rule,
                        refusal_code,
                        source_reaction_activity.detail
                        or "source-reaction activity provider refused",
                        source_reaction_activity=source_reaction_activity,
                    )
                numeric_activity = source_reaction_activity.as_pressure_activity()
                if numeric_activity is None:
                    return _make_refusal(
                        rule,
                        REFUSAL_MISSING_CHANNEL_CONTRACT,
                        "source-reaction activity verdict carried no numeric value",
                        source_reaction_activity=source_reaction_activity,
                    )
                source_activity = numeric_activity
            else:
                # Pure-component / activity-independent evaluator: a=1 is
                # the correct dimensionless unit activity, but it was
                # previously indistinguishable from a forgotten default.
                from simulator.silent_zero import (
                    CATEGORY_PROVEN_ZERO,
                    ZeroBecause,
                    note_dict,
                )

                _unit_activity_note = note_dict(
                    ZeroBecause.IMPLICIT_UNIT_ACTIVITY,
                    site='vapour_rail.request._make_live',
                    species=str(rule.species_id),
                    field='source_activity',
                    detail=(
                        'source_activity=1.0 pure-component default '
                        f'(activity_exponent={evaluator_activity_exponent}); '
                        'typed as proven unit activity, not missing input'
                    ),
                    doctrine_category=CATEGORY_PROVEN_ZERO,
                )
                if rule.species_id == "Fe":
                    source_reaction_activity_shadow = (
                        state.source_reaction_activity_results.get("FeO")
                    )
            try:
                # Pass every evaluator domain input present on the resolve
                # state (fO2 → pO2_bar). The evaluator refuses omitted
                # activity/fO2 rather than substituting reference conditions.
                #
                # Derivation (RG-1 gateway):
                # premise: the selected source-reaction model declares
                #   log10(P_eff) = log10(P_ref) + n*log10(a_M)
                #   + m*log10(fO2/fO2_ref).
                # algebra: P_eff = P_ref * a_M**n * (fO2/fO2_ref)**m;
                #   for the Ca/Mg/K/Al probe n=1, so the activity correction is
                #   exactly P_eff = a_M * P_sat/reaction-reference.
                # units: P_ref is Pa; activity and reduced fugacity are
                #   dimensionless; P_eff remains Pa.
                # sanity at 1600 C lunar: the result must be in the backend
                #   effective-pressure class (~4.6e-9, 7.3e-3, 0.469,
                #   1.4e-8 Pa for Ca/Mg/K/Al), never the former pure/reference
                #   class (~8.35e5, 6.87e5, 4.51e6, 110 Pa).
                oxygen_fugacity_channel = getattr(
                    compiled.evaluator, "oxygen_fugacity_channel", None
                )
                evaluator_fO2_bar = state.fO2_bar
                if oxygen_fugacity_channel == "intrinsic_melt":
                    if state.source_reaction_fO2_bar is None:
                        return _make_refusal(
                            rule,
                            REFUSAL_MISSING_OUTCOME_STATE,
                            "intrinsic_melt oxygen fugacity is unavailable; "
                            "transport/headspace fO2 substitution is forbidden",
                            source_reaction_activity=source_reaction_activity,
                        )
                    try:
                        evaluator_fO2_bar = float(state.source_reaction_fO2_bar)
                    except (TypeError, ValueError):
                        return _make_refusal(
                            rule,
                            REFUSAL_MISSING_OUTCOME_STATE,
                            "intrinsic_melt oxygen fugacity is malformed",
                            source_reaction_activity=source_reaction_activity,
                        )
                    if not math.isfinite(evaluator_fO2_bar) or evaluator_fO2_bar <= 0.0:
                        return _make_refusal(
                            rule,
                            REFUSAL_MISSING_OUTCOME_STATE,
                            "intrinsic_melt oxygen fugacity must be finite and positive",
                            source_reaction_activity=source_reaction_activity,
                        )
                evaluation = compiled.evaluator.evaluate(
                    state.temperature_K,
                    source_activity=source_activity,
                    pO2_bar=evaluator_fO2_bar,
                )
                pressure_pa = evaluation.pressure_pa
                # VR-11: thread real evaluator range/acquisition state — never
                # synthesize out_of_range=false when the evaluator fired
                # conservative continuation. getattr: unit mocks may not be
                # full PressureEvaluation instances.
                evaluation_extra = {
                    "out_of_range": bool(
                        getattr(evaluation, "out_of_range", False)
                    ),
                    "acquisition_flag": getattr(
                        evaluation, "acquisition_flag", None
                    ),
                    "status": getattr(evaluation, "status", None),
                }
                if _unit_activity_note is not None:
                    from simulator.silent_zero import merge_notes_into_mapping

                    merge_notes_into_mapping(
                        evaluation_extra, [_unit_activity_note]
                    )
                if source_reaction_activity_shadow is not None:
                    try:
                        expected_shadow_component = (
                            declaration.component_id
                            if declaration is not None
                            else source_reaction_activity_shadow.component_id
                        )
                        expected_shadow_standard_state = (
                            declaration.standard_state
                            if declaration is not None
                            else source_reaction_activity_shadow.standard_state
                        )
                        if expected_shadow_standard_state is None:
                            raise ValueError(
                                "typed shadow has no standard-state identity"
                            )
                        typed_activity_evaluation = (
                            compiled.evaluator.evaluate_typed_shadow(
                                state.temperature_K,
                                activity=source_reaction_activity_shadow,
                                pO2_bar=evaluator_fO2_bar,
                                expected_component_id=expected_shadow_component,
                                expected_standard_state=(
                                    expected_shadow_standard_state
                                ),
                                expected_state_fingerprint=str(
                                    state.melt_activity_shadow_state_fingerprint or ""
                                ),
                            )
                        )
                        typed_payload = dict(typed_activity_evaluation)
                        typed_ln_pressure = typed_payload.get("ln_pressure_Pa")
                        if (
                            typed_ln_pressure is not None
                            and pressure_pa is not None
                            and pressure_pa > 0.0
                        ):
                            typed_payload["legacy_pressure_delta_ln"] = float(
                                typed_ln_pressure
                            ) - math.log(float(pressure_pa))
                        typed_activity_evaluation = MappingProxyType(typed_payload)
                    except Exception as shadow_exc:  # noqa: BLE001 - fail-isolated
                        typed_activity_evaluation = MappingProxyType(
                            {
                                "status": "shadow_unavailable_no_behavior_change",
                                "detail": str(shadow_exc),
                            }
                        )
            except Exception as exc:  # noqa: BLE001 — typed as contract miss
                return _make_refusal(
                    rule,
                    REFUSAL_MISSING_CHANNEL_CONTRACT,
                    f"evaluator failed: {exc}",
                    source_reaction_activity=source_reaction_activity,
                )
        else:
            evaluation_extra = {}
        if pressure_pa is not None:
            alpha = (
                compiled.vaporisation_coefficients.evaporation_alpha
                if compiled is not None
                else {}
            )
            alpha_ref = (
                f"alpha:{rule.species_id}"
                if alpha
                else f"alpha_missing:{rule.species_id}"
            )
            alpha_authority_status = (
                str(alpha.get("status") or "")
                if isinstance(alpha, Mapping)
                else ""
            )
            # HEAD oodfix seam: out-of-domain pressure stays PressureValue +
            # FluxEligible (status/acquisition/certification strip authority).
            # INCOMING activity semantics (b-121/b-122): only a genuine Henrian
            # activity bound (UPPER or LOWER) remains non-debiting; OOD gamma
            # is status-bearing and flux-driving.
            activity_is_bound = (
                source_reaction_activity is not None
                and source_reaction_activity.verdict
                in {
                    ActivityVerdictKind.UPPER_BOUND,
                    ActivityVerdictKind.LOWER_BOUND,
                }
            )
            if activity_is_bound:
                bound_evidence = (
                    source_reaction_activity.evidence_ref
                    or source_reaction_activity.reason
                    or "source_reaction_activity_bound"
                )
                pressure = PressureUpperBound(
                    pa=float(pressure_pa), evidence_ref=bound_evidence
                )
                flux = FluxDiagnosticUpperBound(
                    alpha_ref=alpha_ref,
                    reaction_id=rule.source_reaction_id,
                )
            else:
                pressure = PressureValue(pa=float(pressure_pa))
                flux = FluxEligible(
                    alpha_ref=alpha_ref,
                    reaction_id=rule.source_reaction_id,
                )
        else:
            contract_detail = _channel_contract_refusal(rule)
            if contract_detail is not None:
                return _make_refusal(
                    rule, REFUSAL_MISSING_CHANNEL_CONTRACT, contract_detail
                )
            state_detail = _outcome_state_refusal_detail(rule)
            if state_detail is not None:
                return _make_refusal(
                    rule, REFUSAL_MISSING_OUTCOME_STATE, state_detail
                )
            # Reachable only if evaluator was present but returned no pressure
            # without raising — still refuse rather than fabricate a zero.
            return _make_refusal(
                rule,
                REFUSAL_MISSING_OUTCOME_STATE,
                f"point evaluation produced no pressure for {rule.species_id}",
            )

        # pending_validation is preserved, not refused.
        verdict = VERDICT_STATUS_BEARING_NON_AUTHORITATIVE
        if rule.validation_status == _VALIDATION_VALIDATED:
            # Still non-authoritative until R1 flip; certification never.
            verdict = VERDICT_STATUS_BEARING_NON_AUTHORITATIVE

        extra_payload: dict[str, Any] = {"origin": rule.origin}
        if _unit_activity_note is not None:
            from simulator.silent_zero import merge_notes_into_mapping

            merge_notes_into_mapping(extra_payload, [_unit_activity_note])
            extra_payload["source_activity"] = float(source_activity)
            extra_payload["source_activity_origin"] = "pure_component_unit"
        if alpha_authority_status == "analytical_upper_bound":
            extra_payload["alpha_authority_status"] = alpha_authority_status
            # Inventory evolution is intentionally enabled by the owner's
            # analytical-model directive, but this remains a bound-driven,
            # non-certifying result rather than a measured alpha point.
            extra_payload["alpha_inventory_policy"] = (
                "inventory_eligible_analytical_upper_bound_noncertifying"
            )
        if source_reaction_activity is not None:
            source_label = "catalog_activity_corrected"
            extra_payload.update(
                {
                    "activity_verdict": source_reaction_activity.verdict.value,
                    "activity_provider": source_reaction_activity.provider,
                    "activity_evidence_ref": source_reaction_activity.evidence_ref,
                    "activity_evidence_tier": source_reaction_activity.evidence_tier,
                    "activity_reason": source_reaction_activity.reason,
                    "activity_detail": source_reaction_activity.detail,
                }
            )
            if source_reaction_activity.verdict in {
                ActivityVerdictKind.UPPER_BOUND,
                ActivityVerdictKind.LOWER_BOUND,
            }:
                extra_payload["activity_bound"] = (
                    source_reaction_activity.report_label
                    or "bound-not-point"
                )
                extra_payload["activity_bound_direction"] = (
                    source_reaction_activity.bound_direction.value
                    if source_reaction_activity.bound_direction is not None
                    else None
                )
            elif (
                source_reaction_activity.verdict
                is ActivityVerdictKind.STATUS_BEARING_VALUE
            ):
                extra_payload["activity_status"] = (
                    source_reaction_activity.report_label
                    or "status-bearing-not-point"
                )
        extra_payload.update(evaluation_extra)
        return VapourAnswer(
            species_id=rule.species_id,
            pressure=pressure,
            selected_runtime_pressure=pressure,
            flux=flux,
            source_label=source_label,
            formula_id=rule.formula_id,
            source_account=rule.source_account,
            solve_group_id=rule.solve_group_id,
            state_fingerprint=_state_fingerprint(state),
            validation_status=rule.validation_status,
            validation_anchor_refs=rule.validation_anchor_refs,
            verdict_status=verdict,
            certification_ceiling=CERTIFICATION_CEILING_NEVER,
            refusal_code=None,
            extra=MappingProxyType(extra_payload),
            source_reaction_activity=source_reaction_activity,
            source_reaction_activity_shadow=source_reaction_activity_shadow,
            source_reaction_activity_shadow_evaluation=typed_activity_evaluation,
        )

    # Channel-local refusal predicates are monotone and independent of the
    # growing refused set today (no inter-channel cascade yet). The loop still
    # runs to a true fixed point (``changed is False``); cascade edges for
    # association/mass-action dependents are deferred to later R-chunks.
    # Do not invent a dependents map that is never read.
    changed = True
    iterations = 0
    max_iterations = max(8, len(requested) + 2)
    reached_fixed_point = False
    while changed and iterations < max_iterations:
        changed = False
        iterations += 1
        for species_id in sorted(requested):
            if species_id in refused:
                continue
            rule = rules_by_id[species_id]

            # 1) Declared applicability predicate
            active, detail = _predicate_active(rule, state)
            if not active:
                answers[species_id] = _make_refusal(
                    rule, REFUSAL_INAPPLICABLE_PREDICATE, detail
                )
                refused.add(species_id)
                changed = True
                continue

            # 2) Absent required source atom (inventory present for activation
            #    but reactant/atom set incomplete)
            atom_detail = _absent_source_atom_detail(rule, ledger_snapshot)
            if atom_detail is not None:
                answers[species_id] = _make_refusal(
                    rule, REFUSAL_ABSENT_SOURCE_ATOM, atom_detail
                )
                refused.add(species_id)
                changed = True
                continue

            # 3) Channel contract (formula/evaluator/alpha/route)
            contract_detail = _channel_contract_refusal(rule)
            if contract_detail is not None:
                answers[species_id] = _make_refusal(
                    rule, REFUSAL_MISSING_CHANNEL_CONTRACT, contract_detail
                )
                refused.add(species_id)
                changed = True
                continue

            # 3b) t-571 chemical-potential channel gate (design §9): a
            #     compiled non-O2 exchange-channel term attempts composition
            #     through the channel resolver and typed-refuses with the
            #     owner-specific code — never a generic contract miss.
            channel_gate = _chemical_potential_channel_gate(
                rule, catalog_species.get(species_id), state
            )
            if channel_gate is not None:
                gate_code, gate_detail = channel_gate
                answers[species_id] = _make_refusal(
                    rule, gate_code, gate_detail
                )
                refused.add(species_id)
                changed = True
                continue

            # 4) Provider-independent domain: any admitted candidate covers
            #    the state. A single provider domain miss is NOT a step-2
            #    refusal when another candidate remains.
            cands = candidates_map.get(species_id)
            if cands is not None and not _candidates_cover_state(cands, state):
                answers[species_id] = _make_refusal(
                    rule,
                    REFUSAL_NO_ADMITTED_SOURCE,
                    "no admitted source candidate covers requested state "
                    f"(candidates={[c.provider_id for c in cands]})",
                )
                refused.add(species_id)
                changed = True
                continue

            # 5) Missing outcome-determining state (temperature / live
            #    evaluator). Typed refusal — never a flux-active zero.
            state_detail = _outcome_state_refusal_detail(rule)
            if state_detail is not None:
                answers[species_id] = _make_refusal(
                    rule, REFUSAL_MISSING_OUTCOME_STATE, state_detail
                )
                refused.add(species_id)
                changed = True
                continue

            if species_id not in answers:
                answers[species_id] = _make_live(rule)

        if not changed:
            reached_fixed_point = True

    # Ensure every requested ID has an answer (exact-key invariant).
    for species_id in requested:
        if species_id not in answers:
            rule = rules_by_id[species_id]
            answers[species_id] = _make_live(rule)

    return RefusalClosureResult(
        answers=answers,
        reached_fixed_point=reached_fixed_point,
        iterations=iterations,
    )


def build_solve_bundles(
    *,
    flux_active: frozenset[str],
    rules: Sequence[RequestRule],
) -> dict[str, frozenset[str]]:
    """Connected solve bundles from survivors after refusal closure.

    Vertices are flux-active channels. Edges mean shared parent inventory
    species, the same declared ``solve_group_id``, or the same source
    reaction id — not merely sharing an element symbol (O would otherwise
    glue every oxide-derived metal into one bundle).
    """

    rules_by_id = {rule.species_id: rule for rule in rules}
    active = sorted(species_id for species_id in flux_active if species_id in rules_by_id)
    if not active:
        return {}

    adjacency: dict[str, set[str]] = {species_id: set() for species_id in active}
    for i, a in enumerate(active):
        rule_a = rules_by_id[a]
        for b in active[i + 1 :]:
            rule_b = rules_by_id[b]
            shared = False
            if rule_a.solve_group_id == rule_b.solve_group_id:
                shared = True
            elif rule_a.parent_species_ids & rule_b.parent_species_ids:
                shared = True
            elif (
                rule_a.source_reaction_id
                and rule_a.source_reaction_id == rule_b.source_reaction_id
            ):
                shared = True
            if shared:
                adjacency[a].add(b)
                adjacency[b].add(a)

    bundles: dict[str, frozenset[str]] = {}
    seen: set[str] = set()
    bundle_index = 0
    for start in active:
        if start in seen:
            continue
        component: set[str] = set()
        queue: deque[str] = deque([start])
        while queue:
            node = queue.popleft()
            if node in seen:
                continue
            seen.add(node)
            component.add(node)
            queue.extend(adjacency[node] - seen)
        # Stable bundle id from sorted members / shared solve_group when uniform.
        groups = {rules_by_id[s].solve_group_id for s in component}
        if len(groups) == 1:
            bundle_id = next(iter(groups))
            # Disambiguate if the same solve_group_id was split (should not
            # happen when edges include solve_group_id).
            if bundle_id in bundles:
                bundle_id = f"{bundle_id}#{bundle_index}"
        else:
            bundle_id = f"bundle:{bundle_index}:" + "+".join(sorted(component))
        bundles[bundle_id] = frozenset(component)
        bundle_index += 1
    return bundles


def _state_fingerprint(state: VapourResolveState | None) -> str:
    if state is None:
        return "state:none"

    def _number_part(raw: Any) -> str:
        if raw is None:
            return "na"
        try:
            return f"{float(raw):.6g}"
        except (TypeError, ValueError):
            return repr(raw)

    t = state.temperature_K
    t_part = _number_part(t)
    fo2 = state.fO2_bar
    fo2_part = _number_part(fo2)
    p_tot = state.total_pressure_Pa
    p_part = _number_part(p_tot)
    source_fo2 = state.source_reaction_fO2_bar
    source_fo2_part = _number_part(source_fo2)
    extras = state.extras or {}
    extra_part = ""
    if extras:
        # Canonical, stable ordering so equal states fingerprint equal.
        items = ",".join(
            f"{key}={extras[key]!r}" for key in sorted(extras)
        )
        extra_part = f"|extras={items}"
    activity_part = ""
    if state.source_reaction_activities:
        formatted_activities: list[str] = []
        for key in sorted(state.source_reaction_activities):
            raw_value = state.source_reaction_activities[key]
            try:
                value_text = f"{float(raw_value):.12g}"
            except (TypeError, ValueError):
                value_text = repr(raw_value)
            formatted_activities.append(f"{key}={value_text}")
        activity_items = ",".join(formatted_activities)
        activity_part = (
            f"|activity_provider={state.source_reaction_activity_provider or 'na'}"
            f"|activities={activity_items}"
        )
    return (
        f"state:T={t_part}|phase={state.process_phase or 'na'}|"
        f"stage={state.stage or 'na'}|fO2={fo2_part}|"
        f"source_fO2={source_fo2_part}|P={p_part}"
        f"{activity_part}{extra_part}"
    )


def resolve_vapour_batch(
    *,
    rules: Sequence[RequestRule],
    ledger_snapshot: Mapping[str, Any] | LedgerSnapshot,
    state: VapourResolveState | None = None,
    provider_candidates_by_species: Mapping[
        str, Sequence[ProviderDomainCandidate]
    ]
    | None = None,
    catalog_species: Mapping[str, Any] | None = None,
    activity_provider: CondensedPhaseActivityProvider | None = None,
    caller_species_filter: Sequence[str] | None = None,
    flux_activation_context: FluxActivationContext,
) -> VapourBatch:
    """Full §4.2 channel/refusal/set pipeline (no RG-1 value-source flip)."""

    requested = build_request(
        rules,
        ledger_snapshot,
        caller_species_filter=caller_species_filter,
    )
    assert_request_coverage(rules, ledger_snapshot, requested)

    # t-568 Phase 1 is diagnostic-only and explicitly gated. The ordinary
    # request path stays lazy; opted-in callers compute once per component and
    # carry typed objects beside the still-authoritative scalar maps.
    melt_activity_shadow: Any | None = None
    if state is not None and state.melt_activity_shadow_enabled:
        try:
            from simulator.vapour_rail.melt_activity_resolver import (
                build_shadow_for_vapour_batch,
            )

            melt_activity_shadow = build_shadow_for_vapour_batch(
                rules=rules,
                ledger_snapshot=ledger_snapshot,  # type: ignore[arg-type]
                state=state,
                engine_inputs_by_component=state.melt_activity_engine_inputs,
            )
            state = replace(
                state,
                source_reaction_activity_results=(
                    melt_activity_shadow.results_by_component
                ),
                melt_activity_shadow_state_fingerprint=(
                    melt_activity_shadow.state_fingerprint
                ),
                melt_activity_shadow_inventory_digest=(
                    melt_activity_shadow.inventory_digest
                ),
            )
        except Exception as exc:  # noqa: BLE001 - diagnostic shadow is fail-isolated
            melt_activity_shadow = MappingProxyType(
                {
                    "status": "shadow_unavailable_no_behavior_change",
                    "detail": str(exc),
                }
            )

    closure = refusal_closure(
        requested=requested,
        rules=rules,
        ledger_snapshot=ledger_snapshot,
        state=state,
        provider_candidates_by_species=provider_candidates_by_species,
        catalog_species=catalog_species,
        activity_provider=activity_provider,
    )
    answers = dict(closure.answers)

    # Exact-key check before bundle formation.
    if frozenset(answers) != requested:
        raise IncompleteVapourBatchError(
            "refusal closure must answer every requested species; "
            f"missing={sorted(requested - frozenset(answers))}, "
            f"extra={sorted(frozenset(answers) - requested)}"
        )

    union_eligible = frozenset(
        species_id
        for species_id, answer in answers.items()
        if answer.is_flux_active
    )
    # Answerability is not activation authority. Pre-RG keeps the species set
    # supplied by the typed effective-pressure seam, but only after refusal
    # closure proves every *remaining debit claim* is catalog-eligible.
    # Typed non-debiting answers (genuine PressureUpperBound /
    # FluxDiagnosticUpperBound, ZeroByPhysics) demote the seam claim rather
    # than hard-fail the batch.
    # True refusals among claimed species still hard-fail construction.
    # RG-1 may activate the full manifest/catalog union after its
    # activity-corrected value path lands.
    if flux_activation_context.epoch == FLUX_ACTIVATION_EPOCH_PRE_RG:
        claimed = flux_activation_context.effective_pressure_species_ids
        demoted_non_debiting = frozenset(
            species_id
            for species_id in claimed
            if species_id in answers
            and not answers[species_id].is_refused
            and not answers[species_id].is_flux_active
        )
        required_debit_claims = claimed - demoted_non_debiting
        missing_effective = required_debit_claims - union_eligible
        if missing_effective:
            raise VapourRequestConstructionError(
                "pre-RG effective-pressure channels are not flux-eligible: "
                f"{sorted(missing_effective)}"
            )
        flux_active = claimed & union_eligible
    elif flux_activation_context.epoch == FLUX_ACTIVATION_EPOCH_RG_MANIFEST:
        flux_active = union_eligible
    else:  # FluxActivationContext rejects this; retain a fail-closed guard.
        raise VapourRequestConstructionError(
            f"unsupported flux activation epoch: {flux_activation_context.epoch!r}"
        )
    bundles = build_solve_bundles(flux_active=flux_active, rules=rules)

    return VapourBatch(
        requested_species_ids=requested,
        channels_by_species=answers,
        solve_bundle_ids=bundles,
        flux_active_species_ids=flux_active,
        metadata=MappingProxyType(
            {
                "refusal_closure_fixed_point": bool(closure.reached_fixed_point),
                "refusal_closure_iterations": int(closure.iterations),
                "n_requested": len(requested),
                "n_refused": sum(1 for a in answers.values() if a.is_refused),
                "n_flux_active": len(flux_active),
                "n_flux_dormant_by_epoch": len(union_eligible - flux_active),
                "flux_activation_epoch": flux_activation_context.epoch,
                "n_solve_bundles": len(bundles),
            }
        ),
        melt_activity_shadow=melt_activity_shadow,
    )
