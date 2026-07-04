"""Chemistry intents and per-provider capability declarations.

The :class:`ChemistryIntent` enum enumerates every state-machine
operation a provider may own (see ``docs-private/chemistry-engine-
binding-spec-2026-05-14.md`` §2).  :class:`CapabilityProfile` is the
provider's declaration of which intents it can dispatch, which subset it
holds authority for (i.e. may emit a :class:`LedgerTransitionProposal`),
and which AtomLedger accounts it requests to see.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ChemistryIntent(str, Enum):
    """Enumeration of every dispatchable chemistry intent.

    The string values match the binding-spec table verbatim so trace
    output / config files can reference them as plain strings.
    """

    SILICATE_LIQUIDUS = "silicate_liquidus"
    SILICATE_EQUILIBRIUM = "silicate_equilibrium"
    EQUILIBRIUM_CRYSTALLIZATION = "equilibrium_crystallization"
    GATE_LIQUID_FRACTION = "gate_liquid_fraction"
    FRACTIONAL_CRYSTALLIZATION = "fractional_crystallization"
    DECOMPRESSION_PATH = "decompression_path"
    VAPOR_PRESSURE = "vapor_pressure"
    EVAPORATION_FLUX = "evaporation_flux"
    EVAPORATION_TRANSITION = "evaporation_transition"
    CONDENSATION_ROUTE = "condensation_route"
    ELECTROLYSIS_STEP = "electrolysis_step"
    METALLOTHERMIC_STEP = "metallothermic_step"
    CA_ALUMINOTHERMIC_STEP = "ca_aluminothermic_step"
    NATIVE_FE_SATURATION = "native_fe_saturation"
    FE_REDOX_RESPECIATION = "fe_redox_respeciation"
    STAGE0_PRETREATMENT = "stage0_pretreatment"
    OVERHEAD_GAS_EQUILIBRIUM = "overhead_gas_equilibrium"
    OVERHEAD_BLEED = "overhead_bleed"
    OXYGEN_BUBBLER = "oxygen_bubbler"
    OXYGEN_RESERVOIR_EXCHANGE = "oxygen_reservoir_exchange"
    BACKEND_EQUILIBRIUM = "backend_equilibrium"
    SULFUR_SATURATION_GATE = "sulfur_saturation_gate"
    T_P_VALIDATION = "t_p_validation"


@dataclass(frozen=True)
class CapabilityProfile:
    """Provider declaration: intents it can serve and accounts it touches.

    Attributes:
        provider_id: Stable identifier for trace and registry keys.
        intents: Every intent the provider can dispatch (authoritative or
            shadow / diagnostic).  A provider may NEVER receive a request
            for an intent outside this set.
        is_authoritative_for: Subset of ``intents`` for which the
            provider may emit a :class:`LedgerTransitionProposal`.
            Providers acting only as shadow / diagnostic leave this
            empty (or a strict subset of ``intents``).
        declared_accounts: AtomLedger account names the provider expects
            to read via its :class:`ProviderAccountView`.  Any account
            outside this set is filtered out before the provider sees
            the snapshot, and any proposal touching an undeclared
            account is rejected.
    """

    provider_id: str
    intents: frozenset[ChemistryIntent] = field(default_factory=frozenset)
    is_authoritative_for: frozenset[ChemistryIntent] = field(default_factory=frozenset)
    declared_accounts: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        provider_id = str(self.provider_id).strip()
        if not provider_id:
            raise ValueError("CapabilityProfile.provider_id is required")
        intents = frozenset(self.intents)
        authoritative = frozenset(self.is_authoritative_for)
        declared = frozenset(str(a).strip() for a in self.declared_accounts)
        if "" in declared:
            raise ValueError("CapabilityProfile.declared_accounts cannot contain empty names")
        if not authoritative.issubset(intents):
            extra = sorted(i.value for i in (authoritative - intents))
            raise ValueError(
                f"CapabilityProfile.is_authoritative_for must be a subset of intents; "
                f"unauthorised: {extra}"
            )
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "intents", intents)
        object.__setattr__(self, "is_authoritative_for", authoritative)
        object.__setattr__(self, "declared_accounts", declared)

    def can_dispatch(self, intent: ChemistryIntent) -> bool:
        """Whether this provider may receive ``intent`` at all."""

        return intent in self.intents

    def is_authoritative(self, intent: ChemistryIntent) -> bool:
        """Whether this provider may emit a transition proposal for ``intent``."""

        return intent in self.is_authoritative_for
