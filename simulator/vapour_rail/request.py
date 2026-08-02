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
from simulator.vapour_rail.batch import (
    CERTIFICATION_CEILING_NEVER,
    FLUX_ACTIVATION_EPOCH_PRE_RG,
    FLUX_ACTIVATION_EPOCH_RG_MANIFEST,
    VERDICT_STATUS_BEARING_NON_AUTHORITATIVE,
    FluxActivationContext,
    FluxEligible,
    FluxRefusal,
    IncompleteVapourBatchError,
    PressureRefusal,
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

    def __post_init__(self) -> None:
        object.__setattr__(self, "extras", MappingProxyType(dict(self.extras)))

    def as_mapping(self) -> dict[str, Any]:
        return {
            "temperature_K": self.temperature_K,
            "process_phase": self.process_phase,
            "stage": self.stage,
            "total_pressure_Pa": self.total_pressure_Pa,
            "fO2_bar": self.fO2_bar,
            **dict(self.extras),
        }


def _account_mols(
    ledger: Mapping[str, Any] | LedgerSnapshot,
    account: str,
) -> dict[str, float]:
    if hasattr(ledger, "mol_by_account") and not isinstance(ledger, Mapping):
        raw = ledger.mol_by_account(account)  # type: ignore[union-attr]
        if not isinstance(raw, Mapping):
            return {}
        # Single-account view: species -> mol
        if raw and all(not isinstance(v, Mapping) for v in raw.values()):
            return {str(k): float(v) for k, v in raw.items()}
        nested = raw.get(account) if account in raw else raw
        if isinstance(nested, Mapping):
            return {str(k): float(v) for k, v in nested.items()}
        return {}

    if not isinstance(ledger, Mapping):
        return {}
    # Full ledger: account -> species -> mol
    if account in ledger and isinstance(ledger[account], Mapping):
        return {str(k): float(v) for k, v in ledger[account].items()}
    # Flat species map (already sliced to one account by caller)
    if ledger and all(not isinstance(v, Mapping) for v in ledger.values()):
        return {str(k): float(v) for k, v in ledger.items()}
    return {}


def _positive_mol(mols: Mapping[str, float], species_id: str) -> bool:
    try:
        return float(mols.get(species_id, 0.0)) > _INVENTORY_EPSILON
    except (TypeError, ValueError):
        return False


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
    if predicate in {"applicable", "always"}:
        return True, ""
    if predicate in {"not_applicable", "inapplicable"}:
        return (
            False,
            f"declared applicability predicate {predicate!r} is inactive",
        )
    if predicate == "stage0_only":
        phase = (state.process_phase if state else None) or ""
        stage = (state.stage if state else None) or ""
        if phase == "stage0" or stage == "stage0":
            return True, ""
        return (
            False,
            "stage0_only predicate inactive outside stage0 "
            f"(process_phase={phase!r}, stage={stage!r})",
        )
    # Unknown predicates fail closed as inactive with evidence.
    return False, f"unrecognized applicability predicate {predicate!r}"


def _channel_contract_refusal(rule: RequestRule) -> str | None:
    """Provider-independent missing-contract check (step 2)."""

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

    # Seed answers as non-refused placeholders; iterate to fixed point.
    answers: dict[str, VapourAnswer] = {}
    refused: set[str] = set()

    def _make_refusal(rule: RequestRule, code: str, detail: str) -> VapourAnswer:
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
        pressure: PressureValue | PressureRefusal
        flux: FluxEligible | FluxRefusal
        source_label = "catalog"
        pressure_pa = None
        if (
            compiled is not None
            and compiled.evaluator is not None
            and state is not None
            and state.temperature_K is not None
        ):
            try:
                # Pass every evaluator domain input present on the resolve
                # state (fO2 → pO2_bar). Omitting fO2 silently returns the
                # pO2_reference_bar answer for every oxygen partial pressure.
                evaluation = compiled.evaluator.evaluate(
                    state.temperature_K,
                    pO2_bar=state.fO2_bar,
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
            except Exception as exc:  # noqa: BLE001 — typed as contract miss
                return _make_refusal(
                    rule,
                    REFUSAL_MISSING_CHANNEL_CONTRACT,
                    f"evaluator failed: {exc}",
                )
        else:
            evaluation_extra = {}
        if pressure_pa is not None:
            pressure = PressureValue(pa=float(pressure_pa))
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
    t = state.temperature_K
    t_part = f"{t:.6g}" if t is not None else "na"
    fo2 = state.fO2_bar
    fo2_part = f"{fo2:.6g}" if fo2 is not None else "na"
    p_tot = state.total_pressure_Pa
    p_part = f"{p_tot:.6g}" if p_tot is not None else "na"
    extras = state.extras or {}
    extra_part = ""
    if extras:
        # Canonical, stable ordering so equal states fingerprint equal.
        items = ",".join(
            f"{key}={extras[key]!r}" for key in sorted(extras)
        )
        extra_part = f"|extras={items}"
    return (
        f"state:T={t_part}|phase={state.process_phase or 'na'}|"
        f"stage={state.stage or 'na'}|fO2={fo2_part}|P={p_part}"
        f"{extra_part}"
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

    closure = refusal_closure(
        requested=requested,
        rules=rules,
        ledger_snapshot=ledger_snapshot,
        state=state,
        provider_candidates_by_species=provider_candidates_by_species,
        catalog_species=catalog_species,
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
    # Answerability is not activation authority. Pre-RG keeps the exact species
    # set supplied by the typed effective-pressure seam, but only after refusal
    # closure proves every member catalog-eligible. RG-1 may activate the full
    # manifest/catalog union after its activity-corrected value path lands.
    if flux_activation_context.epoch == FLUX_ACTIVATION_EPOCH_PRE_RG:
        missing_effective = (
            flux_activation_context.effective_pressure_species_ids
            - union_eligible
        )
        if missing_effective:
            raise VapourRequestConstructionError(
                "pre-RG effective-pressure channels are not flux-eligible: "
                f"{sorted(missing_effective)}"
            )
        flux_active = flux_activation_context.effective_pressure_species_ids
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
    )
