"""Frozen data-transfer objects exchanged between planner and providers.

These mirror the on-the-wire shape declared in
``docs-private/chemistry-engine-refactor-plan-2026-05-10.md`` §"Chemistry
Kernel API".  All values are immutable -- a provider must not mutate a
:class:`ProviderAccountView` it receives, and the kernel returns frozen
:class:`IntentResult` instances to its caller.

Account / species amounts are MOL.  The simulator is mol-native (see
``AGENTS.md`` invariant #1); kg conversions happen only at the legacy
projection boundary, never inside the kernel.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Optional

from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.scalar_boundary import is_declared_real_scalar


_LEGITIMATE_BOOLEAN_CONTROL_INPUTS = frozenset(
    {
        "accumulator_enabled",
        "active_ca_condensation_route",
        "allow_partial_extent",
        "allow_unmeasured_alpha_fallback",
        "back_reduction",
        "commit_empty_transition",
        "dedicated_ca_condenser",
        "diagnostic_only",
        "force_drain_all",
        "gas_resistance_enabled",
        "kinetic_driven_above_crossover",
        "melt_resistance_enabled",
        "product_routing",
        "route_uncaptured_to_wall",
        "thermo_margin_favorable",
        "vapour_batch_flux_shadow_equal",
    }
)

_DECLARED_REAL_SCALAR_CONTROL_INPUTS = frozenset(
    {
        "T_K",
        "ambient_pressure_bar",
        "available_kg",
        "ca_condenser_temperature_C",
        "ca_shuttle_rate_fraction",
        "ca_shuttle_reserve_ca_product_fraction",
        "bleed_conductance_kg_s",
        "bleed_conductance_kg_s_per_bar",
        "carrier_stoichiometry",
        "cavern_capacity_kg",
        "char_c_mol",
        "condensed_kg",
        "current_A",
        "capture_fraction",
        "capture_mol",
        "captured_ca_mol",
        "dn_to_headspace_mol",
        "dt_hr",
        "escaped_source_kg_override",
        "extent_fraction",
        "external_o2_in_overhead_mol",
        "feed_kg",
        "feo_mol",
        "gas_temperature_K",
        "headspace_volume_m3",
        "headspace_temperature_K",
        "hold_temp_C",
        "internal_o2_capacity_mol",
        "intrinsic_fO2_log",
        "k_mix_per_hr",
        "k_relief_kg_hr_Pa",
        "liquid_fraction",
        "log_fO2",
        "melt_sio2_kg",
        "melt_density_kg_m3",
        "melt_fO2_log",
        "melt_surface_area_m2",
        "melt_surface_renewal_base_kg_s_m2_pa",
        "mol_Al_produced",
        "mol_Al_product",
        "native_fe_mol",
        "native_fe_vapor_mol",
        "o2_mol",
        "o2_per_c_mol",
        "o2_bubbler_eta_absorb_default",
        "o2_bubbler_kg_per_hr",
        "o2_bubbler_target_fO2_log",
        "objective_extent_mol",
        "oxidant_kg",
        "overhead_pressure_pa",
        "p_downstream_bar",
        "p_open_Pa",
        "p_ref_Pa",
        "p_total_bar",
        "p_total_mbar",
        "pO2_bar",
        "pO2_mbar",
        "pipe_diameter_m",
        "pressure_bar",
        "rate_kg_hr",
        "reagent_available_kg",
        "remaining_kg_hr",
        "solid_char_c_kg",
        "source_stoichiometry",
        "T_C",
        "temperature_C",
        "temperature_K",
        "thermo_margin_kj_per_mol_o2",
        "transport_extent_mol",
        "vacuum_floor_bar",
        "vessel_rating_Pa",
        "voltage_V",
        "wall_deposit_fraction",
        "wall_temperature_K",
    }
)


def _validate_control_input_booleans(data: Mapping[str, Any]) -> None:
    """Reject booleans for controls the provider schema declares numeric."""

    for field_name, value in (data or {}).items():
        if field_name not in _DECLARED_REAL_SCALAR_CONTROL_INPUTS:
            continue
        if value is not None and not is_declared_real_scalar(
            value,
            allow_numeric_str=True,
        ):
            raise TypeError(
                f"control_inputs.{field_name} is missing: "
                "expected a declared real scalar, got boolean"
            )


def _freeze_nested_mol(
    data: Mapping[str, Mapping[str, float]],
) -> Mapping[str, Mapping[str, float]]:
    """Return a read-only copy of an account -> species_mol mapping."""

    frozen: dict[str, Mapping[str, float]] = {}
    for account, species_mol in dict(data or {}).items():
        cleaned: dict[str, float] = {}
        for species, value in dict(species_mol or {}).items():
            if not is_declared_real_scalar(value, allow_numeric_str=True):
                raise TypeError(
                    f"amount for account {str(account)!r} species "
                    f"{str(species)!r} must be numeric"
                )
            cleaned[str(species)] = float(value)
        frozen[str(account)] = MappingProxyType(cleaned)
    return MappingProxyType(frozen)


def _validate_finite_nested_mol(
    field_name: str,
    data: Mapping[str, Mapping[str, float]],
) -> None:
    for account, species_mol in dict(data or {}).items():
        for species, value in dict(species_mol or {}).items():
            if not is_declared_real_scalar(value, allow_numeric_str=True):
                raise TypeError(
                    f"{field_name} amount for account {str(account)!r} "
                    f"species {str(species)!r} must be numeric"
                )
            amount = float(value)
            if not math.isfinite(amount):
                raise ValueError(
                    f"{field_name} amount for account {str(account)!r} "
                    f"species {str(species)!r} must be finite"
                )


def _freeze_atom_balance(data: Mapping[str, float]) -> Mapping[str, float]:
    cleaned: dict[str, float] = {}
    for element, value in dict(data or {}).items():
        if not is_declared_real_scalar(value, allow_numeric_str=True):
            raise TypeError(
                f"atom_balance_proof value for element {str(element)!r} "
                "must be numeric"
            )
        cleaned[str(element)] = float(value)
    return MappingProxyType(cleaned)


def _validate_finite_atom_balance(data: Mapping[str, float]) -> None:
    for element, value in dict(data or {}).items():
        if not is_declared_real_scalar(value, allow_numeric_str=True):
            raise TypeError(
                f"atom_balance_proof value for element {str(element)!r} "
                "must be numeric"
            )
        amount = float(value)
        if not math.isfinite(amount):
            raise ValueError(
                f"atom_balance_proof value for element {str(element)!r} must be finite"
            )


def _freeze_str_any(data: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(data or {}))


@dataclass(frozen=True)
class ProviderAccountView:
    """Filtered view of the ledger a provider is allowed to see.

    Constructed by :func:`simulator.chemistry.kernel.account_filters.
    build_provider_account_view` from an :class:`AtomLedger` snapshot and
    the provider's :class:`CapabilityProfile`.  Accounts outside the
    provider's ``declared_accounts`` set NEVER appear here -- that is the
    hard invariant the account-filter test enforces.
    """

    accounts: Mapping[str, Mapping[str, float]]
    species_formula_registry: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "accounts", _freeze_nested_mol(self.accounts))
        object.__setattr__(
            self,
            "species_formula_registry",
            _freeze_str_any(self.species_formula_registry),
        )


@dataclass(frozen=True)
class LedgerTransitionProposal:
    """A balanced debit / credit pair proposed -- not yet committed.

    Mirrors the on-wire shape of
    :class:`simulator.accounting.ledger.LedgerTransition` but is
    explicitly *proposed*: only :class:`ChemistryKernel.commit_batch`
    may translate it into a real :class:`LedgerTransition` and apply it
    to the ledger.  ``debits`` and ``credits`` are
    ``account -> species_mol -> amount`` dicts.  ``atom_balance_proof``
    records the net element-by-element atom count the provider asserts
    is zero; the kernel re-checks this on commit.

    ``producer_provider_id`` and ``producer_intent`` are stamped by the
    kernel after a provider returns through the dispatch path.  They are
    provenance, not credentials: :meth:`ChemistryKernel.commit_batch`
    accepts only the live proposal object the kernel stamped and bound
    to a dispatch origin.
    """

    debits: Mapping[str, Mapping[str, float]]
    credits: Mapping[str, Mapping[str, float]]
    reason: str = ""
    producer_provider_id: str = field(default="", init=False)
    producer_intent: str = field(default="", init=False)
    # Optional provider self-check: element -> claimed net credit-debit
    # mol.  :func:`validate_atom_balance` no-ops if empty; populated
    # entries are cross-checked against the kernel's computed atom
    # totals within :data:`PROOF_CROSSCHECK_TOLERANCE_MOL`.
    atom_balance_proof: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_finite_nested_mol("debits", self.debits)
        _validate_finite_nested_mol("credits", self.credits)
        _validate_finite_atom_balance(self.atom_balance_proof)
        object.__setattr__(self, "debits", _freeze_nested_mol(self.debits))
        object.__setattr__(self, "credits", _freeze_nested_mol(self.credits))
        object.__setattr__(self, "reason", str(self.reason or ""))
        object.__setattr__(
            self,
            "producer_provider_id",
            str(self.producer_provider_id or ""),
        )
        object.__setattr__(
            self,
            "producer_intent",
            str(self.producer_intent or ""),
        )
        object.__setattr__(
            self,
            "atom_balance_proof",
            _freeze_atom_balance(self.atom_balance_proof),
        )

    def accounts_touched(self) -> frozenset[str]:
        """All accounts referenced on either side of the proposal."""

        return frozenset(self.debits) | frozenset(self.credits)


@dataclass(frozen=True)
class ControlAudit:
    """Requested vs applied T / P / fO2 (and any other controls).

    ``requested`` is what the request asked for; ``applied`` is what the
    engine actually used.  ``notes`` carries free-form explanations for
    intentional deviations (e.g. "P clamped to engine minimum 1e-6 bar").
    The kernel validator demands ``requested == applied`` within
    tolerance OR a non-empty ``notes`` entry.
    """

    requested: Mapping[str, Any]
    applied: Mapping[str, Any]
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "requested", _freeze_str_any(self.requested))
        object.__setattr__(self, "applied", _freeze_str_any(self.applied))
        object.__setattr__(self, "notes", tuple(str(n) for n in self.notes))


@dataclass(frozen=True)
class IntentRequest:
    """Frozen request a provider receives via :meth:`ChemistryProvider.dispatch`.

    Built by :class:`ChemistryKernel` from a ledger snapshot plus the
    caller's T / P / fO2 / control inputs.  ``account_view`` has already
    been filtered against the provider's
    :class:`CapabilityProfile.declared_accounts`.
    """

    intent: ChemistryIntent
    account_view: ProviderAccountView
    temperature_C: float
    pressure_bar: float
    fO2_log: Optional[float] = None
    fe_redox_policy: str = "intrinsic"
    control_inputs: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.intent, ChemistryIntent):
            raise TypeError("IntentRequest.intent must be a ChemistryIntent")
        if not isinstance(self.account_view, ProviderAccountView):
            raise TypeError("IntentRequest.account_view must be a ProviderAccountView")
        for field_name in ("temperature_C", "pressure_bar"):
            value = getattr(self, field_name)
            if not is_declared_real_scalar(value, allow_numeric_str=True):
                raise TypeError(f"{field_name} must be numeric")
            object.__setattr__(self, field_name, float(value))
        if self.fO2_log is not None:
            if not is_declared_real_scalar(
                self.fO2_log,
                allow_numeric_str=True,
            ):
                raise TypeError("fO2_log must be numeric")
            object.__setattr__(self, "fO2_log", float(self.fO2_log))
        object.__setattr__(self, "fe_redox_policy", str(self.fe_redox_policy))
        _validate_control_input_booleans(self.control_inputs)
        object.__setattr__(self, "control_inputs", _freeze_str_any(self.control_inputs))


INTENT_RESULT_STATUSES = frozenset(
    {
        "ok",
        "refused",
        "not_converged",
        "out_of_domain",
        "unavailable",
        "unsupported",
        "not_attempted",
        "non_authoritative",
    }
)


class IntentResultStatusError(ValueError):
    """An :class:`IntentResult` carried an unrecognised status token."""

    def __init__(self, status: str) -> None:
        self.status = status
        self.allowed_statuses = INTENT_RESULT_STATUSES
        super().__init__(
            f"unrecognised IntentResult.status {status!r}; expected one of "
            f"{tuple(sorted(self.allowed_statuses))}"
        )


# ---------------------------------------------------------------------------
# Whole-run status precedence — ONE owner, and the order is not arbitrary.
# ---------------------------------------------------------------------------

#: Most-severe-first precedence for reducing several per-probe statuses to one
#: whole-run status.
#:
#: DERIVATION — the order follows from WHAT WAS LEARNED, not from a severity
#: ranking picked by feel, and the tie between the first two is decided by the
#: asymmetry of being wrong:
#:
#:   unavailable    the engine was not there. We learned NOTHING about the
#:                  physics of this run.
#:   out_of_domain  the engine WAS there and said this composition/temperature
#:                  lies outside its calibration. That is a physics claim.
#:   not_converged  the engine was there, attempted the solve, and failed to
#:                  converge. Also a claim, but a weaker one.
#:
#: `unavailable` must outrank `out_of_domain` because of what each causes
#: downstream. Reporting `out_of_domain` makes the optimizer prune the candidate
#: as PHYSICALLY INFEASIBLE — a permanent verdict about the recipe. Reporting
#: `unavailable` routes to retry — a verdict about the tooling. Being wrong the
#: first way permanently discards a possibly-good recipe on evidence that was
#: never gathered; being wrong the second way costs a retry. So a run that lost
#: its engine partway MUST NOT be allowed to make a physics claim about itself.
#:
#: ★ DO NOT REORDER THIS TUPLE without re-deriving the above. It previously
#: existed as three separate implementations — two in the optimizer sharing the
#: transposed order `(out_of_domain, unavailable, not_converged)`, and one in the
#: runner with the correct order. Two agreed with each other and disagreed with
#: the third, which is what happens when a rule is COPIED rather than IMPORTED.
BACKEND_STATUS_PRECEDENCE: tuple[str, ...] = (
    "unavailable",
    "out_of_domain",
    "not_converged",
)


def select_backend_status(statuses) -> str | None:
    """Reduce many per-probe statuses to one whole-run status.

    Returns the most severe member of :data:`BACKEND_STATUS_PRECEDENCE` present,
    else the last status seen, else ``None`` for an empty input. Callers that
    need "the latest" rather than "the most severe" want a different function —
    this one deliberately does not preserve ordering information beyond the
    fallback.
    """
    values = tuple(str(status) for status in statuses if status is not None)
    for status in BACKEND_STATUS_PRECEDENCE:
        if status in values:
            return status
    return values[-1] if values else None


@dataclass(frozen=True)
class IntentResult:
    """Provider response to an :class:`IntentRequest`.

    ``transition`` is ``None`` for diagnostic / shadow results; only
    authoritative providers populate it.  ``diagnostic`` is free-form
    metadata for trace and UI (phases present, liquidus margin, parity
    deltas, ...).  ``status`` follows the planner-level vocabulary:
    ``ok`` / ``refused`` / ``not_converged`` / ``out_of_domain`` /
    ``unavailable`` / ``unsupported`` / ``not_attempted`` /
    ``non_authoritative``.  ``refused`` is a policy refusal: dispatch met the
    provider, but the request violates a physics/regime gate (for example,
    reductant margin <= 0).
    """

    intent: ChemistryIntent
    status: str
    transition: Optional[LedgerTransitionProposal] = None
    control_audit: Optional[ControlAudit] = None
    diagnostic: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.intent, ChemistryIntent):
            raise TypeError("IntentResult.intent must be a ChemistryIntent")
        status = str(self.status)
        if status not in INTENT_RESULT_STATUSES:
            raise IntentResultStatusError(status)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "diagnostic", _freeze_str_any(self.diagnostic))
        object.__setattr__(self, "warnings", tuple(str(w) for w in self.warnings))
