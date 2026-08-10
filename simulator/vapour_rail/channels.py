"""Chemical-potential channel map (t-571 Phase 1).

A channel is one state-specific value of
``mu_q - mu0_q = R T ln(f_q / f0_q)`` for a named gas species, standard
state, and reaction plane.  Phase 1 admits the full vocabulary and makes
``gas.O2.ideal_1bar.v1`` the first runtime citizen: the existing
``source_activity`` + ``pO2_bar`` evaluator path becomes the O2 channel
under this interface with bit-identical pressure evaluation.

Non-O2 channels (H2, F2, Cl2, Br2, I2, S2, N2) have no runtime owners yet.
Resolving them yields typed refusals that name both the missing channel and
the missing melt-side owner (t-568 Rev 3 halide / sulfur coupling).  No new
carrier admissions are unlocked in this chunk.

Construction owner gate (design §1/§3): ``GasChannelPotential`` cannot be
constructed directly — every instance is minted by a typed runtime owner
factory in this module (the O2 legacy adapter, registered as the Phase-1
owner, or the Phase-1 resolver for refusal-only outcomes).  A bare
``GasChannelPotential(...)`` call raises :class:`TypeError` (the grant is a
required keyword-only field — there is no unbound default), and any grant
that was not minted by this module's factories raises
:class:`ChannelConstructionError`; free-floating fugacity is closed by
construction, not by review.  The grant itself is sealed: it can only be
minted with a module-private token, is bound to one channel, and is
accepted only if it *is* the registered object for its (channel, owner,
source-kind, refusal-only) key — a field-perfect look-alike fails the
identity check.

Ownership invariants (design §3) — hard boundaries:

1. Melt side stays in t-568 (condensed activities).
2. Gas side stays in t-571 (exchange potentials only).
3. Stoichiometry stays in the catalog/compiler; exponents are derived, never
   free-fitted or editable on the row.
4. The composer combines once; no double application of a channel term.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any

import math
import re

from simulator.physical_constants import (
    GAS_CONSTANT,
    MELT_DISSOCIATION_PO2_MAX_BAR,
    MELT_DISSOCIATION_PO2_MIN_BAR,
)

# ---------------------------------------------------------------------------
# Channel vocabulary (design §4)
# ---------------------------------------------------------------------------

CHANNEL_O2 = "gas.O2.ideal_1bar.v1"
CHANNEL_H2 = "gas.H2.ideal_1bar.v1"
CHANNEL_F2 = "gas.F2.ideal_1bar.v1"
CHANNEL_Cl2 = "gas.Cl2.ideal_1bar.v1"
CHANNEL_Br2 = "gas.Br2.ideal_1bar.v1"
CHANNEL_I2 = "gas.I2.ideal_1bar.v1"
CHANNEL_S2 = "gas.S2.ideal_1bar.v1"
CHANNEL_N2 = "gas.N2.ideal_1bar.v1"

REACTION_PLANE_MELT_INTERFACE = "melt_interface"
REACTION_PLANE_TRANSPORT_HEADSPACE = "transport_headspace"
REACTION_PLANES = frozenset(
    {
        REACTION_PLANE_MELT_INTERFACE,
        REACTION_PLANE_TRANSPORT_HEADSPACE,
    }
)

# Legacy oxygen_fugacity_channel labels map onto reaction planes.
LEGACY_FO2_PLANE: Mapping[str, str] = MappingProxyType(
    {
        "intrinsic_melt": REACTION_PLANE_MELT_INTERFACE,
        "transport_headspace": REACTION_PLANE_TRANSPORT_HEADSPACE,
    }
)

GAS_STANDARD_STATE_O2 = "gas.ideal.O2.1bar.v1"
GAS_STANDARD_STATE_H2 = "gas.ideal.H2.1bar.v1"
GAS_STANDARD_STATE_F2 = "gas.ideal.F2.1bar.v1"
GAS_STANDARD_STATE_Cl2 = "gas.ideal.Cl2.1bar.v1"
GAS_STANDARD_STATE_Br2 = "gas.ideal.Br2.1bar.v1"
GAS_STANDARD_STATE_I2 = "gas.ideal.I2.1bar.v1"
GAS_STANDARD_STATE_S2 = "gas.ideal.S2.1bar.v1"
GAS_STANDARD_STATE_N2 = "gas.ideal.N2.1bar.v1"


@dataclass(frozen=True)
class ChannelRegistryEntry:
    """One admitted gas-species exchange potential identity."""

    channel_id: str
    gas_formula: str
    gas_standard_state_id: str
    required_planes: frozenset[str]
    runtime_owner: str | None
    """None when Phase 1 has no runtime potential owner."""

    missing_melt_owner_code: str | None
    """t-568 melt-side owner required before adoption can unlock carriers.

    Halogen and sulfur channels are deliberately coupled to melt-side owners
    (design §4 / §8.1 / Phase 4): implementing the gas potential alone does
    not admit any authority-bearing carrier while the melt reservoir owner is
    missing.  Measured pX2 without an admitted halide owner still refuses.
    """


CHANNEL_REGISTRY: Mapping[str, ChannelRegistryEntry] = MappingProxyType(
    {
        CHANNEL_O2: ChannelRegistryEntry(
            channel_id=CHANNEL_O2,
            gas_formula="O2",
            gas_standard_state_id=GAS_STANDARD_STATE_O2,
            required_planes=frozenset(REACTION_PLANES),
            runtime_owner="legacy_pO2_adapter",
            missing_melt_owner_code=None,
        ),
        CHANNEL_H2: ChannelRegistryEntry(
            channel_id=CHANNEL_H2,
            gas_formula="H2",
            gas_standard_state_id=GAS_STANDARD_STATE_H2,
            required_planes=frozenset({REACTION_PLANE_MELT_INTERFACE}),
            runtime_owner=None,
            missing_melt_owner_code=None,  # equipment/chemistry path, not melt halide
        ),
        CHANNEL_F2: ChannelRegistryEntry(
            channel_id=CHANNEL_F2,
            gas_formula="F2",
            gas_standard_state_id=GAS_STANDARD_STATE_F2,
            required_planes=frozenset({REACTION_PLANE_MELT_INTERFACE}),
            runtime_owner=None,
            missing_melt_owner_code="halide_reservoir_owner_missing",
        ),
        CHANNEL_Cl2: ChannelRegistryEntry(
            channel_id=CHANNEL_Cl2,
            gas_formula="Cl2",
            gas_standard_state_id=GAS_STANDARD_STATE_Cl2,
            required_planes=frozenset({REACTION_PLANE_MELT_INTERFACE}),
            runtime_owner=None,
            missing_melt_owner_code="halide_reservoir_owner_missing",
        ),
        CHANNEL_Br2: ChannelRegistryEntry(
            channel_id=CHANNEL_Br2,
            gas_formula="Br2",
            gas_standard_state_id=GAS_STANDARD_STATE_Br2,
            required_planes=frozenset({REACTION_PLANE_MELT_INTERFACE}),
            runtime_owner=None,
            missing_melt_owner_code="halide_reservoir_owner_missing",
        ),
        CHANNEL_I2: ChannelRegistryEntry(
            channel_id=CHANNEL_I2,
            gas_formula="I2",
            gas_standard_state_id=GAS_STANDARD_STATE_I2,
            required_planes=frozenset({REACTION_PLANE_MELT_INTERFACE}),
            runtime_owner=None,
            missing_melt_owner_code="halide_reservoir_owner_missing",
        ),
        CHANNEL_S2: ChannelRegistryEntry(
            channel_id=CHANNEL_S2,
            gas_formula="S2",
            gas_standard_state_id=GAS_STANDARD_STATE_S2,
            required_planes=frozenset({REACTION_PLANE_MELT_INTERFACE}),
            runtime_owner=None,
            missing_melt_owner_code="sulfur_reservoir_owner_missing",
        ),
        CHANNEL_N2: ChannelRegistryEntry(
            channel_id=CHANNEL_N2,
            gas_formula="N2",
            gas_standard_state_id=GAS_STANDARD_STATE_N2,
            required_planes=frozenset({REACTION_PLANE_MELT_INTERFACE}),
            runtime_owner=None,
            missing_melt_owner_code=None,  # plane transform / equipment owner
        ),
    }
)

# Formula → channel_id for non-target gas participants.
FORMULA_TO_CHANNEL_ID: Mapping[str, str] = MappingProxyType(
    {
        entry.gas_formula: entry.channel_id
        for entry in CHANNEL_REGISTRY.values()
    }
)


# ---------------------------------------------------------------------------
# Compiled reaction terms (design §5.2 / §6)
# ---------------------------------------------------------------------------


class ReactionTermRole(str, Enum):
    CONDENSED_ACTIVITY = "condensed_activity"
    EXCHANGE_CHANNEL = "exchange_channel"


@dataclass(frozen=True)
class CompiledReactionTerm:
    """One immutable stoichiometry-derived reaction term.

    Exponents are never persisted as editable authority.  The compiler emits:

        e_i = -nu_i / nu_g

    Derivation (design §6; project rule: algebra + units in comments)
    ----------------------------------------------------------------
    Premise: signed stoichiometric coefficients ``nu_i`` (negative reactants,
    positive products).  Equilibrium at constant T gives

        0 = ΔG°_rxn/(R T) + Σ_i nu_i ln(a_i)

    Solve for the target vapor ``g`` with ``nu_g > 0``:

        ln(a_g) = -ΔG°_rxn/(nu_g R T)
                  + Σ_m (-nu_m/nu_g) ln(a_m)
                  + Σ_q (-nu_q/nu_g) [(μ_q-μ0_q)/(R T)]

    Therefore every compiled exponent is ``e_i = -nu_i / nu_g``.

    Units: ``nu_i`` and ``nu_g`` are mol per reaction extent, so ``e_i`` is
    dimensionless.  For an ideal gas channel,
    ``(μ_q-μ0_q)/(R T) = ln(f_q/f0_q)`` is dimensionless, so every term
    added to ``ln(a_g)`` is dimensionless.  Conversion to the evaluator's
    log10 surface happens once:

        Δ log10(P_g/P0_g) = e_q · (μ_q-μ0_q) / (R T ln 10)

    Sanity: multiplying the whole reaction by any positive common factor
    leaves every ``e_i`` unchanged.  Full reversal is inadmissible (would
    make the catalog target coefficient negative).  BaO + ½ Cl2 → BaCl + ½ O2
    yields ``e_Cl2 = +1/2``, ``e_O2 = -1/2``, ``e_BaO = +1``.
    """

    participant_formula: str
    role: ReactionTermRole
    input_id: str  # component_id | channel_id
    signed_nu: float
    target_nu: float
    derived_exponent: float
    standard_state_id: str | None = None
    required_plane: str | None = None  # channels only

    def __post_init__(self) -> None:
        if self.target_nu <= 0.0:
            raise ValueError(
                f"target_nu must be positive; got {self.target_nu}"
            )
        expected = -self.signed_nu / self.target_nu
        if not math.isclose(
            self.derived_exponent, expected, rel_tol=0.0, abs_tol=1.0e-12
        ):
            raise ValueError(
                f"derived_exponent {self.derived_exponent} disagrees with "
                f"-signed_nu/target_nu = {expected}"
            )
        if self.role is ReactionTermRole.EXCHANGE_CHANNEL:
            if self.input_id not in CHANNEL_REGISTRY:
                raise ValueError(
                    f"unknown channel_id {self.input_id!r} on reaction term"
                )
            if self.required_plane is None:
                raise ValueError(
                    "exchange_channel terms require required_plane"
                )


def derived_exponent(signed_nu: float, target_nu: float) -> float:
    """``e_i = -nu_i / nu_g`` (design §6)."""

    if target_nu <= 0.0:
        raise ValueError(f"target_nu must be positive; got {target_nu}")
    return -signed_nu / target_nu


def compile_o2_channel_term(
    *,
    signed_nu_o2: float,
    target_nu: float,
    reaction_plane: str,
    pO2_reference_bar: float = 1.0,
) -> CompiledReactionTerm:
    """Compile the O2 exchange term that replaces scalar ``pO2_exponent``.

    The legacy scalar was ``pO2_exponent = -nu_O2 / nu_vapor``.  That is
    exactly ``derived_exponent(signed_nu_o2, target_nu)``.
    """

    del pO2_reference_bar  # retained on the evaluator for bit-identity rebase
    if reaction_plane not in REACTION_PLANES:
        raise ValueError(f"unknown reaction plane {reaction_plane!r}")
    return CompiledReactionTerm(
        participant_formula="O2",
        role=ReactionTermRole.EXCHANGE_CHANNEL,
        input_id=CHANNEL_O2,
        signed_nu=float(signed_nu_o2),
        target_nu=float(target_nu),
        derived_exponent=derived_exponent(float(signed_nu_o2), float(target_nu)),
        standard_state_id=GAS_STANDARD_STATE_O2,
        required_plane=reaction_plane,
    )


def compile_channel_term_from_binding(
    *,
    participant_formula: str,
    channel_id: str,
    signed_nu: float,
    target_nu: float,
    required_plane: str,
) -> CompiledReactionTerm:
    """Compile one exchange term from a catalog ``exchange_channel_bindings`` row."""

    entry = CHANNEL_REGISTRY.get(channel_id)
    if entry is None:
        raise ValueError(f"unknown channel_id {channel_id!r}")
    formula = participant_formula.strip()
    if formula not in {entry.gas_formula, f"{entry.gas_formula}(g)"}:
        # Allow bare formula or phase-suffixed form matching the registry gas.
        bare = formula.split("(", 1)[0]
        if bare != entry.gas_formula:
            raise ValueError(
                f"binding formula {participant_formula!r} disagrees with "
                f"channel {channel_id} gas {entry.gas_formula!r}"
            )
    if required_plane not in entry.required_planes:
        raise ValueError(
            f"plane {required_plane!r} not admitted for channel {channel_id}"
        )
    return CompiledReactionTerm(
        participant_formula=entry.gas_formula,
        role=ReactionTermRole.EXCHANGE_CHANNEL,
        input_id=channel_id,
        signed_nu=float(signed_nu),
        target_nu=float(target_nu),
        derived_exponent=derived_exponent(float(signed_nu), float(target_nu)),
        standard_state_id=entry.gas_standard_state_id,
        required_plane=required_plane,
    )


# ---------------------------------------------------------------------------
# Typed runtime result (design §7.1)
# ---------------------------------------------------------------------------


class ChannelVerdictKind(str, Enum):
    POINT = "Point"
    STATUS_BEARING = "StatusBearingValue"
    EXTENDED_BOUND = "ExtendedBound"
    REFUSAL = "Refusal"
    PROVEN_ZERO = "ProvenZero"


# Stable refusal / disposition codes (design §9)
REFUSAL_MISSING_CHANNEL_INPUT = "refused_missing_channel_input"
REFUSAL_MISSING_EQUIPMENT_OWNER = "refused_missing_equipment_owner"
REFUSAL_INVENTORY_CANNOT_SUPPLY = "refused_inventory_cannot_supply_channel"
REFUSAL_STATE_FINGERPRINT_MISMATCH = "refused_state_fingerprint_mismatch"
REFUSAL_HALIDE_RESERVOIR_OWNER_MISSING = "refused_halide_reservoir_owner_missing"
REFUSAL_SULFUR_RESERVOIR_OWNER_MISSING = "refused_sulfur_reservoir_owner_missing"
REFUSAL_UNBOUND_REACTION_PARTICIPANT = "refused_unbound_reaction_participant"
REFUSAL_CARBON_SIDE_OWNER_MISSING = "refused_carbon_side_owner_missing"
REFUSAL_CHANNEL_RUNTIME_OWNER_MISSING = "refused_channel_runtime_owner_missing"
REFUSAL_CHANNEL_PLANE_UNSUPPORTED = "refused_channel_plane_unsupported"

SOURCE_KIND_LEGACY_SCALAR_ADAPTER = "legacy_scalar_adapter"
SOURCE_KIND_RUNTIME_OWNER = "runtime_owner"
SOURCE_KIND_REFUSED = "refused"

# Phase-1 resolver identity: the only owner permitted to mint REFUSAL
# potentials for unowned channels.
RESOLVER_OWNER_PHASE1 = "phase1_resolver"


class ChannelConstructionError(ValueError):
    """``GasChannelPotential`` built without a runtime-owner grant.

    A gas potential is ``mu_q - mu0_q`` for a named species, standard state,
    and plane *backed by a real inventory and equipment/chemistry path*
    (design §1).  Constructing the numeric type directly — with no owner —
    is the free-floating-fugacity failure the design forbids, so the bare
    constructor fails closed.
    """


class ChannelEvaluationError(ValueError):
    """A channel contribution could not be composed into a pressure term."""


# Module-private mint token for :class:`_OwnerGrant`.  Only code physically
# inside this module can reference this object through the mint helpers
# below; the token itself is never exported (absent from ``__all__`` and
# from the package ``__init__`` re-export surface).  The grant constructor
# refuses any call that does not present this exact object.
_GRANT_MINT_TOKEN = object()

# Live-grant registry keyed to real runtime sources:
# ``(channel_id, owner_id, source_kind, refusal_only) -> grant``.
# ``GasChannelPotential.__post_init__`` accepts a grant only when it *is*
# the registered object for its key (identity, not field equality), so a
# look-alike — even one built with a stolen token — is rejected.  The
# registry is bounded: one entry per (registry channel × declared owner ×
# source kind × refusal flag) combination the mint helpers below can
# produce, not per call.
_MINTED_OWNER_GRANTS: dict[tuple[str, str, str, bool], "_OwnerGrant"] = {}


class _OwnerGrant:
    """Module-private construction grant for :class:`GasChannelPotential`.

    Only the owner factories in this module mint grants:

    - :func:`o2_potential_from_pO2_bar` — the registered Phase-1 runtime
      owner for ``gas.O2.ideal_1bar.v1`` (``legacy_pO2_adapter``);
    - :func:`resolve_channel_potential` — the Phase-1 resolver, which may
      mint **refusal** potentials only (no numeric potential without an
      owner).

    Sealed construction: the constructor requires the module-private
    ``_GRANT_MINT_TOKEN`` and every minted grant is registered under its
    ``(channel_id, owner_id, source_kind, refusal_only)`` key by the mint
    helpers.  ``GasChannelPotential.__post_init__`` demands that the
    presented grant *be* the registered object for its key and that the
    grant's ``channel_id`` match the potential's — so a grant cannot be
    forged from outside this module, and a genuine grant cannot be lifted
    onto a different channel.  A potential object is therefore proof that
    construction flowed through a typed owner source for that channel.
    """

    __slots__ = ("channel_id", "owner_id", "source_kind", "refusal_only")

    def __init__(
        self,
        *,
        channel_id: str,
        owner_id: str,
        source_kind: str,
        refusal_only: bool,
        _mint_token: object = None,
    ) -> None:
        if _mint_token is not _GRANT_MINT_TOKEN:
            raise ChannelConstructionError(
                "_OwnerGrant cannot be constructed outside the channels "
                "module; mint one via a registered runtime-owner factory "
                "(free-floating fugacity is forbidden by design §1/§3)"
            )
        self.channel_id = channel_id
        self.owner_id = owner_id
        self.source_kind = source_kind
        self.refusal_only = bool(refusal_only)


def _mint_grant(
    *,
    channel_id: str,
    owner_id: str,
    source_kind: str,
    refusal_only: bool,
) -> _OwnerGrant:
    """Return the registered grant for the key, minting it on first use."""

    key = (channel_id, owner_id, source_kind, bool(refusal_only))
    grant = _MINTED_OWNER_GRANTS.get(key)
    if grant is None:
        grant = _OwnerGrant(
            channel_id=channel_id,
            owner_id=owner_id,
            source_kind=source_kind,
            refusal_only=refusal_only,
            _mint_token=_GRANT_MINT_TOKEN,
        )
        _MINTED_OWNER_GRANTS[key] = grant
    return grant


def _grant_for_registered_owner(
    entry: ChannelRegistryEntry,
    *,
    owner_id: str,
    source_kind: str,
) -> _OwnerGrant:
    """Mint a numeric-capable grant for a registry-declared runtime owner."""

    if entry.runtime_owner is None:
        raise ChannelConstructionError(
            f"channel {entry.channel_id} has no registered runtime owner; "
            "no numeric potential may be constructed"
        )
    if entry.runtime_owner != owner_id:
        raise ChannelConstructionError(
            f"owner {owner_id!r} is not the registered runtime owner "
            f"{entry.runtime_owner!r} for channel {entry.channel_id}"
        )
    return _mint_grant(
        channel_id=entry.channel_id,
        owner_id=owner_id,
        source_kind=source_kind,
        refusal_only=False,
    )


def _grant_for_resolver_refusal(entry: ChannelRegistryEntry) -> _OwnerGrant:
    """Mint a refusal-only grant for the Phase-1 resolver."""

    return _mint_grant(
        channel_id=entry.channel_id,
        owner_id=RESOLVER_OWNER_PHASE1,
        source_kind=SOURCE_KIND_REFUSED,
        refusal_only=True,
    )


@dataclass(frozen=True)
class GasChannelPotential:
    """Canonical gas-side exchange potential (design §7.1).

    The canonical numeric is the natural-log reduced potential
    ``(μ-μ0)/(R T) = ln(f/f0)``.  ``delta_mu_J_per_mol`` is derived at the
    recorded temperature and constructor-checked; neither field may be
    independently tuned on finite-center verdicts.
    """

    channel_id: str
    gas_formula: str
    gas_standard_state_id: str
    reaction_plane: str
    temperature_K: float | None
    verdict: ChannelVerdictKind
    reduced_potential_ln: float | None = None
    delta_mu_J_per_mol: float | None = None
    ln_band: float | None = None
    authority: bool = False
    refusal_code: str | None = None
    detail: str | None = None
    state_fingerprint: str | None = None
    inventory_receipt: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    equipment_receipt: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    chemistry_receipt: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    dependency_receipt: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    observation_or_setpoint_receipt: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    source_kind: str = SOURCE_KIND_REFUSED
    attempts: tuple[str, ...] = ()
    random_variable_key: str | None = None
    independent_sigma: float | None = None
    signed_correlation_loadings: Mapping[str, float] = field(
        default_factory=lambda: MappingProxyType({})
    )
    correlation_basis_digest: str | None = None
    extended_ln_lower: float | None = None
    extended_ln_upper: float | None = None
    # Bit-identity aid for the legacy O2 adapter: the physical pO2 (bar)
    # after the melt-dissociation envelope clamp, when source_kind is
    # legacy_scalar_adapter.  Not part of the public channel contract.
    legacy_pO2_bar: float | None = None
    legacy_pO2_reference_bar: float | None = None
    # Owner-gate (design §1/§3): construction requires a module-minted
    # grant from a typed runtime owner.  The field is keyword-only and has
    # NO default — the unbound path is deleted: omitting the grant raises
    # TypeError at the signature, and any value that is not the registered
    # module-minted grant object for this channel raises
    # ChannelConstructionError in __post_init__.  Excluded from repr/eq so
    # receipts and numerics stay the observable contract; the grant itself
    # is proof of construction provenance, not payload.
    _owner_grant: Any = field(kw_only=True, repr=False, compare=False)

    def __post_init__(self) -> None:
        grant = self._owner_grant
        if not isinstance(grant, _OwnerGrant):
            raise ChannelConstructionError(
                "GasChannelPotential requires a runtime-owner grant; "
                "construct via resolve_channel_potential or "
                "o2_potential_from_pO2_bar (free-floating fugacity is "
                "forbidden by design §1/§3)"
            )
        if grant.channel_id != self.channel_id:
            raise ChannelConstructionError(
                f"owner grant is bound to channel {grant.channel_id!r} by "
                f"owner {grant.owner_id!r}; it cannot mint a potential for "
                f"channel {self.channel_id!r} (grant lifting is forbidden)"
            )
        registry_key = (
            grant.channel_id,
            grant.owner_id,
            grant.source_kind,
            grant.refusal_only,
        )
        if _MINTED_OWNER_GRANTS.get(registry_key) is not grant:
            raise ChannelConstructionError(
                "owner grant is not the module-minted registry object for "
                f"{registry_key}; grants are minted only by the owner "
                "factories in channels.py (forgery fails by identity, not "
                "by field matching)"
            )
        if grant.source_kind != self.source_kind:
            raise ChannelConstructionError(
                f"owner grant source_kind {grant.source_kind!r} "
                f"disagrees with potential source_kind {self.source_kind!r}"
            )
        if (
            grant.refusal_only
            and self.verdict is not ChannelVerdictKind.REFUSAL
        ):
            raise ChannelConstructionError(
                f"owner {grant.owner_id!r} is refusal-only; "
                f"no numeric {self.verdict.value} potential may be minted "
                f"for channel {self.channel_id}"
            )
        object.__setattr__(
            self, "inventory_receipt", MappingProxyType(dict(self.inventory_receipt))
        )
        object.__setattr__(
            self, "equipment_receipt", MappingProxyType(dict(self.equipment_receipt))
        )
        object.__setattr__(
            self, "chemistry_receipt", MappingProxyType(dict(self.chemistry_receipt))
        )
        object.__setattr__(
            self,
            "dependency_receipt",
            MappingProxyType(dict(self.dependency_receipt)),
        )
        object.__setattr__(
            self,
            "observation_or_setpoint_receipt",
            MappingProxyType(dict(self.observation_or_setpoint_receipt)),
        )
        object.__setattr__(
            self,
            "signed_correlation_loadings",
            MappingProxyType(dict(self.signed_correlation_loadings)),
        )
        object.__setattr__(self, "attempts", tuple(self.attempts))

        if self.channel_id not in CHANNEL_REGISTRY:
            raise ValueError(f"unknown channel_id {self.channel_id!r}")
        entry = CHANNEL_REGISTRY[self.channel_id]
        if self.gas_formula != entry.gas_formula:
            raise ValueError(
                f"gas_formula {self.gas_formula!r} disagrees with registry "
                f"{entry.gas_formula!r}"
            )
        if self.gas_standard_state_id != entry.gas_standard_state_id:
            raise ValueError(
                f"standard_state {self.gas_standard_state_id!r} disagrees with "
                f"registry {entry.gas_standard_state_id!r}"
            )

        if self.verdict in {
            ChannelVerdictKind.REFUSAL,
            ChannelVerdictKind.PROVEN_ZERO,
            ChannelVerdictKind.EXTENDED_BOUND,
        }:
            if self.reduced_potential_ln is not None or self.delta_mu_J_per_mol is not None:
                raise ValueError(
                    f"{self.verdict.value} forbids finite center / delta_mu"
                )
            if self.verdict is ChannelVerdictKind.EXTENDED_BOUND:
                if self.extended_ln_lower is None and self.extended_ln_upper is None:
                    raise ValueError(
                        "ExtendedBound requires typed absolute log endpoints"
                    )
            return

        # Finite-center verdicts require both fields and the R T invariant.
        if self.reduced_potential_ln is None or self.delta_mu_J_per_mol is None:
            raise ValueError(
                f"{self.verdict.value} requires reduced_potential_ln and "
                "delta_mu_J_per_mol"
            )
        if self.temperature_K is None or self.temperature_K <= 0.0:
            raise ValueError(
                "finite-center potentials require positive temperature_K"
            )
        expected_delta = (
            GAS_CONSTANT * float(self.temperature_K) * float(self.reduced_potential_ln)
        )
        if not math.isclose(
            float(self.delta_mu_J_per_mol),
            expected_delta,
            rel_tol=0.0,
            abs_tol=1.0e-9 * max(1.0, abs(expected_delta)),
        ):
            raise ValueError(
                f"delta_mu_J_per_mol {self.delta_mu_J_per_mol} disagrees with "
                f"R T reduced_potential_ln = {expected_delta}"
            )

    def may_certify(self) -> bool:
        if self.verdict is not ChannelVerdictKind.POINT:
            return False
        return bool(self.authority)

    def as_log10_fugacity_ratio(self) -> float | None:
        """``log10(f/f0)`` for a finite-center potential, else None."""

        if self.reduced_potential_ln is None:
            return None
        return float(self.reduced_potential_ln) / math.log(10.0)


@dataclass(frozen=True)
class ReactionThermoInputs:
    """Typed handoff into :meth:`CompiledPressureEvaluator.evaluate` (design §7.2)."""

    reaction_id: str | None
    state_fingerprint: str | None
    activities: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    channels: Mapping[str, GasChannelPotential] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "activities", MappingProxyType(dict(self.activities))
        )
        object.__setattr__(
            self, "channels", MappingProxyType(dict(self.channels))
        )


# ---------------------------------------------------------------------------
# O2 legacy adapter (design §7.2) — bit-identity carrier
# ---------------------------------------------------------------------------


def clamp_physical_pO2_bar(pO2_bar: float) -> float:
    """Physical melt/transport pO2 envelope (b-148)."""

    oxygen = float(pO2_bar)
    if not math.isfinite(oxygen) or oxygen <= 0.0:
        raise ValueError(f"pO2_bar must be finite and positive; got {pO2_bar!r}")
    if oxygen < MELT_DISSOCIATION_PO2_MIN_BAR:
        return MELT_DISSOCIATION_PO2_MIN_BAR
    if oxygen > MELT_DISSOCIATION_PO2_MAX_BAR:
        return MELT_DISSOCIATION_PO2_MAX_BAR
    return oxygen


def o2_potential_from_pO2_bar(
    *,
    pO2_bar: float,
    temperature_K: float,
    reaction_plane: str,
    pO2_reference_bar: float = 1.0,
    state_fingerprint: str | None = None,
    authority: bool = False,
) -> GasChannelPotential:
    """Wrap a legacy scalar pO2 into the typed O2 channel (1-bar standard state).

    The channel stores ``reduced_potential_ln = ln(p_clamped / 1 bar)`` against
    the design's 1-bar ideal-gas standard state.  The legacy
    ``pO2_reference_bar`` is retained on the potential so the evaluator can
    apply the bit-identical ratio form

        e · log10(p_clamped / p_ref)

    which is algebraically identical to the rebased composition

        e · log10(p_clamped / 1) − e · log10(p_ref / 1)

    (design §7.2).  Using the ratio form preserves IEEE identity with the
    pre-t-571 evaluator for every catalog row.
    """

    if reaction_plane not in REACTION_PLANES:
        raise ValueError(f"unknown reaction plane {reaction_plane!r}")
    temperature_K = float(temperature_K)
    if not math.isfinite(temperature_K) or temperature_K <= 0.0:
        raise ValueError(
            f"temperature_K must be finite and positive; got {temperature_K!r}"
        )
    p_ref = float(pO2_reference_bar)
    if not math.isfinite(p_ref) or p_ref <= 0.0:
        raise ValueError(
            f"pO2_reference_bar must be finite and positive; got {pO2_reference_bar!r}"
        )
    # Owner gate: this factory is the registered Phase-1 runtime owner for
    # the O2 channel.  The grant is minted against the registry entry, so
    # removing the owner from CHANNEL_REGISTRY closes this adapter too.
    grant = _grant_for_registered_owner(
        CHANNEL_REGISTRY[CHANNEL_O2],
        owner_id="legacy_pO2_adapter",
        source_kind=SOURCE_KIND_LEGACY_SCALAR_ADAPTER,
    )
    oxygen = clamp_physical_pO2_bar(pO2_bar)
    # 1-bar ideal-gas standard state: (μ-μ0)/(R T) = ln(f / 1 bar).
    reduced = math.log(oxygen / 1.0)
    delta_mu = GAS_CONSTANT * temperature_K * reduced
    return GasChannelPotential(
        channel_id=CHANNEL_O2,
        gas_formula="O2",
        gas_standard_state_id=GAS_STANDARD_STATE_O2,
        reaction_plane=reaction_plane,
        temperature_K=temperature_K,
        verdict=ChannelVerdictKind.POINT,
        reduced_potential_ln=reduced,
        delta_mu_J_per_mol=delta_mu,
        authority=authority,
        state_fingerprint=state_fingerprint,
        source_kind=SOURCE_KIND_LEGACY_SCALAR_ADAPTER,
        observation_or_setpoint_receipt=MappingProxyType(
            {
                "kind": "legacy_pO2_bar",
                "pO2_bar_input": float(pO2_bar),
                "pO2_bar_clamped": oxygen,
                "pO2_reference_bar": p_ref,
                "standard_state_bar": 1.0,
            }
        ),
        attempts=("legacy_scalar_adapter",),
        legacy_pO2_bar=oxygen,
        legacy_pO2_reference_bar=p_ref,
        _owner_grant=grant,
    )


def channel_log10_contribution(
    term: CompiledReactionTerm,
    potential: GasChannelPotential,
    *,
    legacy_pO2_reference_bar: float | None = None,
) -> float:
    """Convert one channel potential into a log10 pressure contribution.

    O2 + legacy adapter uses the bit-identical ratio form
    ``e · log10(p / p_ref)``.  All other finite-center potentials use the
    general natural-log form ``e · reduced_potential_ln / ln(10)``.
    """

    if term.role is not ReactionTermRole.EXCHANGE_CHANNEL:
        raise ChannelEvaluationError(
            "channel_log10_contribution requires an exchange term"
        )
    if potential.verdict is ChannelVerdictKind.REFUSAL:
        raise ChannelEvaluationError(
            f"refused channel {potential.channel_id}: {potential.refusal_code}"
        )
    if potential.verdict is ChannelVerdictKind.PROVEN_ZERO:
        raise ChannelEvaluationError(
            "proven-zero channel must be reduced pre-arithmetic by the composer"
        )
    if potential.verdict is ChannelVerdictKind.EXTENDED_BOUND:
        raise ChannelEvaluationError(
            "extended-bound channels require symbolic bound propagation"
        )
    if potential.channel_id != term.input_id:
        raise ChannelEvaluationError(
            f"potential channel {potential.channel_id} does not match term "
            f"{term.input_id}"
        )

    # Bit-identity path for O2 (design §7.2).
    if (
        term.input_id == CHANNEL_O2
        and potential.source_kind == SOURCE_KIND_LEGACY_SCALAR_ADAPTER
        and potential.legacy_pO2_bar is not None
    ):
        p_ref = (
            potential.legacy_pO2_reference_bar
            if potential.legacy_pO2_reference_bar is not None
            else legacy_pO2_reference_bar
        )
        if p_ref is None:
            p_ref = 1.0
        # Exact pre-t-571 expression.
        return float(term.derived_exponent) * math.log10(
            float(potential.legacy_pO2_bar) / float(p_ref)
        )

    if potential.reduced_potential_ln is None:
        raise ChannelEvaluationError(
            f"channel {potential.channel_id} has no reduced_potential_ln"
        )
    return (
        float(term.derived_exponent)
        * float(potential.reduced_potential_ln)
        / math.log(10.0)
    )


def channel_linear_mass_action_factor(
    term: CompiledReactionTerm,
    potential: GasChannelPotential,
) -> float:
    """Linear-space mass-action factor ``(f_clamped / f_ref) ** e``.

    Migration aid for the legacy linear-space composers in
    ``engines/builtin/vapor_pressure.py`` (design §12 Phase 1): those rails
    multiply pressure in linear space instead of summing log10 terms, so the
    log-space :func:`channel_log10_contribution` form cannot preserve IEEE
    identity there.  This helper applies the *same* channel potential through
    the exact pre-t-571 linear expression:

        factor = (legacy_pO2_bar / legacy_pO2_reference_bar) ** e

    Derivation: premise — the legacy rail computes
    ``P_eq = P_ref · a^ea · (p_clamped / p_ref)^e`` with
    ``p_clamped = min(max(pO2, MIN), MAX)``; ``legacy_pO2_bar`` on the
    potential is exactly that clamped value (``clamp_physical_pO2_bar``) and
    ``term.derived_exponent = -(-e)/1 = e`` bit-for-bit, so the factor is
    identical to the pre-migration expression for every input.  Units:
    bar/bar is dimensionless.

    Fail-closed: only the O2 channel carried by the legacy scalar adapter is
    linear-composable; every other channel / source kind must use the
    log-space composer (or refuse).
    """

    if term.role is not ReactionTermRole.EXCHANGE_CHANNEL:
        raise ChannelEvaluationError(
            "channel_linear_mass_action_factor requires an exchange term"
        )
    if potential.channel_id != term.input_id:
        raise ChannelEvaluationError(
            f"potential channel {potential.channel_id} does not match term "
            f"{term.input_id}"
        )
    if potential.verdict is not ChannelVerdictKind.POINT:
        raise ChannelEvaluationError(
            f"linear mass action requires a Point verdict; got "
            f"{potential.verdict.value} for {potential.channel_id} "
            f"({potential.refusal_code or 'no numeric potential'})"
        )
    if (
        term.input_id != CHANNEL_O2
        or potential.source_kind != SOURCE_KIND_LEGACY_SCALAR_ADAPTER
        or potential.legacy_pO2_bar is None
        or potential.legacy_pO2_reference_bar is None
    ):
        raise ChannelEvaluationError(
            "linear mass action is defined only for the O2 legacy scalar "
            f"adapter; got channel={term.input_id} "
            f"source_kind={potential.source_kind}"
        )
    # Exact pre-t-571 linear expression (bit-identity bar).
    return (
        float(potential.legacy_pO2_bar)
        / float(potential.legacy_pO2_reference_bar)
    ) ** float(term.derived_exponent)


# ---------------------------------------------------------------------------
# Channel resolver (Phase 1: O2 only; others typed-refuse)
# ---------------------------------------------------------------------------


def resolve_channel_potential(
    channel_id: str,
    *,
    temperature_K: float | None = None,
    reaction_plane: str = REACTION_PLANE_MELT_INTERFACE,
    pO2_bar: float | None = None,
    pO2_reference_bar: float = 1.0,
    state_fingerprint: str | None = None,
    authority: bool = False,
) -> GasChannelPotential:
    """Resolve one channel potential, or return a typed owner-specific refusal.

    Phase 1 runtime owner: O2 only (via legacy pO2 adapter).  Halogen and
    sulfur channels refuse with the melt-side owner code even if a measured
    pX2 were hypothetically available — adoption is coupled to t-568 melt
    ownership by design (see :class:`ChannelRegistryEntry.missing_melt_owner_code`).
    """

    entry = CHANNEL_REGISTRY.get(channel_id)
    if entry is None:
        raise ValueError(f"unknown channel_id {channel_id!r}")

    if reaction_plane not in entry.required_planes and channel_id != CHANNEL_O2:
        # O2 admits both planes; others require melt_interface in Phase 1.
        return _refusal_potential(
            entry,
            reaction_plane=reaction_plane,
            temperature_K=temperature_K,
            refusal_code=REFUSAL_CHANNEL_PLANE_UNSUPPORTED,
            detail=(
                f"channel {channel_id} does not admit reaction plane "
                f"{reaction_plane!r}; required one of "
                f"{sorted(entry.required_planes)}"
            ),
            state_fingerprint=state_fingerprint,
        )

    if channel_id == CHANNEL_O2:
        if pO2_bar is None:
            return _refusal_potential(
                entry,
                reaction_plane=reaction_plane,
                temperature_K=temperature_K,
                refusal_code=REFUSAL_MISSING_CHANNEL_INPUT,
                detail="O2 channel requires explicit pO2_bar input",
                state_fingerprint=state_fingerprint,
            )
        if temperature_K is None:
            return _refusal_potential(
                entry,
                reaction_plane=reaction_plane,
                temperature_K=None,
                refusal_code=REFUSAL_MISSING_CHANNEL_INPUT,
                detail="O2 channel requires temperature_K",
                state_fingerprint=state_fingerprint,
            )
        plane = reaction_plane
        if plane not in REACTION_PLANES:
            plane = REACTION_PLANE_TRANSPORT_HEADSPACE
        return o2_potential_from_pO2_bar(
            pO2_bar=pO2_bar,
            temperature_K=temperature_K,
            reaction_plane=plane,
            pO2_reference_bar=pO2_reference_bar,
            state_fingerprint=state_fingerprint,
            authority=authority,
        )

    # Non-O2: no runtime owner in Phase 1.
    if entry.missing_melt_owner_code == "halide_reservoir_owner_missing":
        return _refusal_potential(
            entry,
            reaction_plane=reaction_plane,
            temperature_K=temperature_K,
            refusal_code=REFUSAL_HALIDE_RESERVOIR_OWNER_MISSING,
            detail=(
                f"channel {channel_id} has no runtime potential owner; "
                f"melt-side owner missing: halide_reservoir_owner_missing "
                f"(t-568 Rev 3). Gas-side adoption is coupled to the melt "
                f"halide owner — a measured pX2 alone cannot unlock carriers."
            ),
            state_fingerprint=state_fingerprint,
            missing_melt_owner="halide_reservoir_owner_missing",
        )
    if entry.missing_melt_owner_code == "sulfur_reservoir_owner_missing":
        return _refusal_potential(
            entry,
            reaction_plane=reaction_plane,
            temperature_K=temperature_K,
            refusal_code=REFUSAL_SULFUR_RESERVOIR_OWNER_MISSING,
            detail=(
                f"channel {channel_id} has no runtime potential owner; "
                f"melt-side owner missing: sulfur_reservoir_owner_missing "
                f"(t-568). Gas-side adoption is coupled to the melt sulfur "
                f"owner."
            ),
            state_fingerprint=state_fingerprint,
            missing_melt_owner="sulfur_reservoir_owner_missing",
        )
    # H2 / N2: equipment / chemistry path missing (no melt halide code).
    return _refusal_potential(
        entry,
        reaction_plane=reaction_plane,
        temperature_K=temperature_K,
        refusal_code=REFUSAL_CHANNEL_RUNTIME_OWNER_MISSING,
        detail=(
            f"channel {channel_id} has no runtime potential owner in Phase 1 "
            f"(equipment/chemistry path not landed; sources are a later chunk)"
        ),
        state_fingerprint=state_fingerprint,
    )


def _refusal_potential(
    entry: ChannelRegistryEntry,
    *,
    reaction_plane: str,
    temperature_K: float | None,
    refusal_code: str,
    detail: str,
    state_fingerprint: str | None = None,
    missing_melt_owner: str | None = None,
) -> GasChannelPotential:
    chemistry = {}
    if missing_melt_owner is not None:
        chemistry["missing_melt_owner"] = missing_melt_owner
        chemistry["missing_channel"] = entry.channel_id
    return GasChannelPotential(
        channel_id=entry.channel_id,
        gas_formula=entry.gas_formula,
        gas_standard_state_id=entry.gas_standard_state_id,
        reaction_plane=reaction_plane,
        temperature_K=temperature_K,
        verdict=ChannelVerdictKind.REFUSAL,
        refusal_code=refusal_code,
        detail=detail,
        state_fingerprint=state_fingerprint,
        chemistry_receipt=MappingProxyType(chemistry),
        source_kind=SOURCE_KIND_REFUSED,
        attempts=("phase1_resolver",),
        _owner_grant=_grant_for_resolver_refusal(entry),
    )


# ---------------------------------------------------------------------------
# Composer admission (design §9) — typed NEEDS-CHANNEL refusal
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChannelCompositionRefusal:
    """Typed refusal for a NEEDS-CHANNEL carrier composition attempt."""

    carrier: str
    element: str | None
    disposition: str
    missing_channels: tuple[str, ...]
    missing_melt_owners: tuple[str, ...]
    detail: str
    ledger_missing: str | None = None
    pathway: str | None = None

    def as_mapping(self) -> Mapping[str, Any]:
        return MappingProxyType(
            {
                "carrier": self.carrier,
                "element": self.element,
                "disposition": self.disposition,
                "missing_channels": list(self.missing_channels),
                "missing_melt_owners": list(self.missing_melt_owners),
                "detail": self.detail,
                "ledger_missing": self.ledger_missing,
                "pathway": self.pathway,
            }
        )


# Pathway tags used by the coverage gap free-text → typed reason map.
_PATHWAY_CHANNEL_TAGS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "parent_oxide_x_halogen_exchange": frozenset(),  # formula-driven
        "element_condensed_x_halogen_exchange": frozenset(),
        "catalog_base_x_halogen_exchange": frozenset(),
        "base_x_hydrogen_exchange": frozenset({CHANNEL_H2}),
        "halide_condensed_family": frozenset(),
        "related_binary_condensed": frozenset(),
    }
)

_HALOGEN_FORMULA_RE = re.compile(
    r"(?P<el>F|Cl|Br|I)(?P<n>\d*)"
)
_H_FORMULA_RE = re.compile(r"(?<![A-Z])H(?P<n>\d*)(?![a-z])")
_S_FORMULA_RE = re.compile(r"(?<![A-Z])S(?P<n>\d*)(?![a-z])")
_N_FORMULA_RE = re.compile(r"(?<![A-Z])N(?P<n>\d*)(?![a-z])")

_HALOGEN_TO_CHANNEL = {
    "F": CHANNEL_F2,
    "Cl": CHANNEL_Cl2,
    "Br": CHANNEL_Br2,
    "I": CHANNEL_I2,
}


def infer_required_channels_from_carrier(
    carrier: str,
    *,
    pathway: str | None = None,
    missing_text: str | None = None,
) -> tuple[str, ...]:
    """Infer non-O2 channel requirements from a carrier formula / pathway prose.

    Used for typed refusal of the 878 NEEDS-CHANNEL cohort.  Not an admission
    authority — balanced catalog reactions remain the only path to COMPOSABLE.
    """

    text = missing_text or ""
    channels: set[str] = set()

    # Explicit tags in ledger free-text (when present).
    for token, channel_id in (
        ("F2", CHANNEL_F2),
        ("Cl2", CHANNEL_Cl2),
        ("Br2", CHANNEL_Br2),
        ("I2", CHANNEL_I2),
        ("H2", CHANNEL_H2),
        ("S2", CHANNEL_S2),
        ("N2", CHANNEL_N2),
    ):
        if re.search(rf"\b{token}\b", text):
            channels.add(channel_id)

    # Pathway-level defaults.
    if pathway == "base_x_hydrogen_exchange":
        channels.add(CHANNEL_H2)
    if pathway and "halogen" in pathway:
        # Formula-driven halogen detection below fills the specific X2.
        pass
    if pathway == "related_binary_condensed" and "S" in carrier:
        # Sulfur-bearing related binaries.
        if re.search(r"(?<![A-Z])S\d*(?![a-z])", carrier):
            channels.add(CHANNEL_S2)

    # Formula-driven halogen / H / S / N presence.
    for match in _HALOGEN_FORMULA_RE.finditer(carrier):
        el = match.group("el")
        channels.add(_HALOGEN_TO_CHANNEL[el])
    if pathway == "base_x_hydrogen_exchange" or "hydrogen" in (pathway or ""):
        if _H_FORMULA_RE.search(carrier):
            channels.add(CHANNEL_H2)
    if _S_FORMULA_RE.search(carrier) and pathway in {
        "related_binary_condensed",
        "catalog_base_x_halogen_exchange",
        None,
    }:
        # Only tag S2 when the pathway prose or formula implies free S2, not
        # every SO2 carrier.  Prefer explicit text tags above.
        if "S2" in text or pathway == "related_binary_condensed":
            channels.add(CHANNEL_S2)
    if "C?" in text or "carbon" in text.lower():
        pass  # carbon-side is not a gas channel

    # BaF canonical: F in formula → F2.
    if not channels and any(ch in carrier for ch in ("F", "Cl", "Br", "I")):
        for match in _HALOGEN_FORMULA_RE.finditer(carrier):
            channels.add(_HALOGEN_TO_CHANNEL[match.group("el")])

    return tuple(sorted(channels))


def attempt_channel_composition(
    *,
    carrier: str,
    element: str | None = None,
    pathway: str | None = None,
    missing_text: str | None = None,
    required_channels: Sequence[str] | None = None,
    temperature_K: float | None = 1800.0,
    reaction_plane: str = REACTION_PLANE_MELT_INTERFACE,
    state_fingerprint: str | None = None,
    pO2_bar: float | None = None,
) -> ChannelCompositionRefusal | Mapping[str, GasChannelPotential]:
    """Attempt to resolve every required channel for a carrier composition.

    On any missing owner / runtime channel, returns a
    :class:`ChannelCompositionRefusal` naming the missing channel IDs **and**
    the missing melt-side owners.  BaF is the canonical test case: requires
    ``gas.F2.ideal_1bar.v1`` and refuses with
    ``refused_halide_reservoir_owner_missing``.
    """

    channels = tuple(required_channels) if required_channels is not None else (
        infer_required_channels_from_carrier(
            carrier, pathway=pathway, missing_text=missing_text
        )
    )
    if not channels:
        # Carbon-side or unparseable: still a typed refusal.
        if missing_text and (
            "carbon" in missing_text.lower() or "C?" in missing_text
        ):
            return ChannelCompositionRefusal(
                carrier=carrier,
                element=element,
                disposition=REFUSAL_CARBON_SIDE_OWNER_MISSING,
                missing_channels=(),
                missing_melt_owners=(),
                detail=(
                    f"carrier {carrier}: carbon-side owner missing "
                    "(not a gas channel in t-571)"
                ),
                ledger_missing=missing_text,
                pathway=pathway,
            )
        return ChannelCompositionRefusal(
            carrier=carrier,
            element=element,
            disposition=REFUSAL_MISSING_CHANNEL_INPUT,
            missing_channels=(),
            missing_melt_owners=(),
            detail=(
                f"carrier {carrier}: NEEDS-CHANNEL but no channel requirement "
                "could be inferred; balanced reaction still required"
            ),
            ledger_missing=missing_text,
            pathway=pathway,
        )

    resolved: dict[str, GasChannelPotential] = {}
    missing_channel_ids: list[str] = []
    missing_melt_owners: list[str] = []
    details: list[str] = []
    for channel_id in channels:
        pot = resolve_channel_potential(
            channel_id,
            temperature_K=temperature_K,
            reaction_plane=reaction_plane,
            pO2_bar=pO2_bar if channel_id == CHANNEL_O2 else None,
            state_fingerprint=state_fingerprint,
        )
        if pot.verdict is ChannelVerdictKind.REFUSAL:
            missing_channel_ids.append(channel_id)
            melt_owner = pot.chemistry_receipt.get("missing_melt_owner")
            if melt_owner:
                missing_melt_owners.append(str(melt_owner))
            details.append(pot.detail or pot.refusal_code or channel_id)
        else:
            resolved[channel_id] = pot

    if missing_channel_ids:
        # Prefer the most specific melt-owner disposition when present.
        if "halide_reservoir_owner_missing" in missing_melt_owners:
            disposition = REFUSAL_HALIDE_RESERVOIR_OWNER_MISSING
        elif "sulfur_reservoir_owner_missing" in missing_melt_owners:
            disposition = REFUSAL_SULFUR_RESERVOIR_OWNER_MISSING
        else:
            disposition = REFUSAL_CHANNEL_RUNTIME_OWNER_MISSING
        return ChannelCompositionRefusal(
            carrier=carrier,
            element=element,
            disposition=disposition,
            missing_channels=tuple(missing_channel_ids),
            missing_melt_owners=tuple(sorted(set(missing_melt_owners))),
            detail="; ".join(details),
            ledger_missing=missing_text,
            pathway=pathway,
        )
    return MappingProxyType(resolved)


# ---------------------------------------------------------------------------
# Coverage-ledger consumption of typed NEEDS-CHANNEL reasons
# ---------------------------------------------------------------------------

_PATHWAY_RE = re.compile(r"pathway=([a-z0-9_]+)")
_NEEDS_CHANNEL_PREFIX = re.compile(r"^NEEDS-CHANNEL\b")


@dataclass(frozen=True)
class TypedGapReason:
    """Coverage-ledger row projected onto the t-571 channel vocabulary."""

    element: str
    carrier: str
    pathway: str | None
    required_channels: tuple[str, ...]
    missing_melt_owners: tuple[str, ...]
    disposition: str
    raw_missing: str


def parse_needs_channel_entry(entry: Mapping[str, Any]) -> TypedGapReason | None:
    """Project one coverage-gap entry into a typed channel reason, or None."""

    missing = str(entry.get("missing") or "")
    if not _NEEDS_CHANNEL_PREFIX.search(missing):
        return None
    element = str(entry.get("element") or "")
    carrier = str(entry.get("carrier") or "")
    pathway_match = _PATHWAY_RE.search(missing)
    pathway = pathway_match.group(1) if pathway_match else None
    required = infer_required_channels_from_carrier(
        carrier, pathway=pathway, missing_text=missing
    )
    melt_owners: list[str] = []
    for channel_id in required:
        entry_reg = CHANNEL_REGISTRY.get(channel_id)
        if entry_reg and entry_reg.missing_melt_owner_code:
            melt_owners.append(entry_reg.missing_melt_owner_code)
    if any(
        CHANNEL_REGISTRY[c].missing_melt_owner_code == "halide_reservoir_owner_missing"
        for c in required
        if c in CHANNEL_REGISTRY
    ):
        disposition = REFUSAL_HALIDE_RESERVOIR_OWNER_MISSING
    elif any(
        CHANNEL_REGISTRY[c].missing_melt_owner_code == "sulfur_reservoir_owner_missing"
        for c in required
        if c in CHANNEL_REGISTRY
    ):
        disposition = REFUSAL_SULFUR_RESERVOIR_OWNER_MISSING
    elif required:
        disposition = REFUSAL_CHANNEL_RUNTIME_OWNER_MISSING
    else:
        disposition = REFUSAL_MISSING_CHANNEL_INPUT
    return TypedGapReason(
        element=element,
        carrier=carrier,
        pathway=pathway,
        required_channels=required,
        missing_melt_owners=tuple(sorted(set(melt_owners))),
        disposition=disposition,
        raw_missing=missing,
    )


def load_typed_needs_channel_reasons(
    gaps_path: str | Path | None = None,
    *,
    entries: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[TypedGapReason, ...]:
    """Load coverage-gap entries and project NEEDS-CHANNEL rows to typed reasons.

    Callers must pass either ``entries`` or an explicit ``gaps_path``.  The
    coverage-gap ledger is a review/instrumentation artifact, not a simulation
    runtime input — this helper never hard-codes a path under ``data/``.
    """

    if entries is None:
        if gaps_path is None:
            raise ValueError(
                "load_typed_needs_channel_reasons requires entries= or gaps_path="
            )
        import yaml

        path = Path(gaps_path)
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw_entries = payload.get("entries") if isinstance(payload, Mapping) else None
        if not isinstance(raw_entries, list):
            return ()
        entries = [e for e in raw_entries if isinstance(e, Mapping)]

    reasons: list[TypedGapReason] = []
    for entry in entries:
        typed = parse_needs_channel_entry(entry)
        if typed is not None:
            reasons.append(typed)
    return tuple(reasons)


def reconstruct_878_pathway_cohort(
    entries: Sequence[Mapping[str, Any]] | None = None,
    *,
    gaps_path: str | Path | None = None,
) -> Mapping[str, Any]:
    """Reproduce the design §2.2 878-row pathway cohort from the free-text ledger.

    Returns counts by pathway label plus the total.  The 878 figure is the
    sum of these disjoint pathway labels (not a per-channel count).

    Callers must pass either ``entries`` or an explicit ``gaps_path``.  The
    coverage-gap ledger is not a simulation runtime input.
    """

    if entries is None:
        if gaps_path is None:
            raise ValueError(
                "reconstruct_878_pathway_cohort requires entries= or gaps_path="
            )
        import yaml

        path = Path(gaps_path)
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw_entries = payload.get("entries") if isinstance(payload, Mapping) else None
        if not isinstance(raw_entries, list):
            entries = []
        else:
            entries = [e for e in raw_entries if isinstance(e, Mapping)]

    pathway_labels = (
        "parent_oxide_x_halogen_exchange",
        "element_condensed_x_halogen_exchange",
        "catalog_base_x_halogen_exchange",
        "base_x_hydrogen_exchange",
        "halide_condensed_family",
        "related_binary_condensed",
    )
    counts: dict[str, int] = {label: 0 for label in pathway_labels}
    # halide_condensed_family rows in the 878 use the ratio-mismatch prose,
    # not every halide_condensed_family COMPOSABLE-NOW row.
    halide_ratio_mismatch = 0
    for entry in entries:
        missing = str(entry.get("missing") or "")
        if not _NEEDS_CHANNEL_PREFIX.search(missing):
            # Some 878 rows may use pathway labels without the NEEDS-CHANNEL
            # prefix in older ledger snapshots; still count by pathway when
            # the free-text carries the design's pathway token.
            pass
        pathway_match = _PATHWAY_RE.search(missing)
        if pathway_match is None:
            continue
        pathway = pathway_match.group(1)
        if pathway == "halide_condensed_family":
            # Design §2.2: only the "changed total-halogen ratio only" subset
            # (107).  Ledger marks these as NEEDS-CHANNEL with ratio mismatch.
            if "NEEDS-CHANNEL" in missing or "ratio mismatch" in missing:
                counts["halide_condensed_family"] += 1
                halide_ratio_mismatch += 1
            continue
        if pathway in counts:
            if "NEEDS-CHANNEL" in missing or pathway in {
                "parent_oxide_x_halogen_exchange",
                "element_condensed_x_halogen_exchange",
                "catalog_base_x_halogen_exchange",
                "base_x_hydrogen_exchange",
                "related_binary_condensed",
            }:
                # Count NEEDS-CHANNEL pathway rows; related_binary may also
                # carry C? without NEEDS-CHANNEL in some snapshots.
                if "NEEDS-CHANNEL" in missing or pathway == "related_binary_condensed":
                    counts[pathway] += 1

    total = sum(counts.values())
    return MappingProxyType(
        {
            "by_pathway": MappingProxyType(dict(counts)),
            "total": total,
            "design_total": 878,
            "halide_ratio_mismatch_rows": halide_ratio_mismatch,
            "reproducible": total == 878,
        }
    )


def count_o2_dependent_compiled_evaluators(
    catalog: Any,
) -> Mapping[str, Any]:
    """Count compiled evaluators that consume the O2 channel (P3 fold).

    Design review P3: the prose figure "11 models with pO2_reference_bar =
    1e-9" counted YAML declaration sites.  The semantic count is the number
    of **compiled pressure-model evaluators**.  This helper returns both the
    1e-9-ref evaluator count and the total O2-dependent evaluator count so
    the figure is machine-reproducible from the live catalog.
    """

    total_o2 = 0
    ref_1e9 = 0
    ref_1bar = 0
    species_ids_1e9: list[str] = []
    for species_id, species in getattr(catalog, "species", {}).items():
        evaluator = getattr(species, "evaluator", None)
        if evaluator is None:
            continue
        exp = float(getattr(evaluator, "pO2_exponent", 0.0) or 0.0)
        if exp == 0.0:
            # Also accept reaction_terms carrying O2.
            terms = getattr(evaluator, "reaction_terms", ()) or ()
            has_o2 = any(
                getattr(t, "input_id", None) == CHANNEL_O2 for t in terms
            )
            if not has_o2:
                continue
        total_o2 += 1
        pref = float(getattr(evaluator, "pO2_reference_bar", 1.0) or 1.0)
        if abs(pref - 1.0e-9) <= 1.0e-20:
            ref_1e9 += 1
            species_ids_1e9.append(str(species_id))
        elif abs(pref - 1.0) <= 1.0e-15:
            ref_1bar += 1
    return MappingProxyType(
        {
            "o2_dependent_evaluators": total_o2,
            "pO2_reference_1e9_evaluators": ref_1e9,
            "pO2_reference_1bar_evaluators": ref_1bar,
            "species_ids_1e9": tuple(sorted(species_ids_1e9)),
            # Corrected wording (P3): these are compiled evaluators, not YAML
            # declaration sites.  Design §7.2 previously said "11 models".
            "wording": (
                f"{ref_1e9} compiled pressure-model evaluators with "
                "pO2_reference_bar = 1e-9 "
                f"({', '.join(sorted(species_ids_1e9)) or 'none'})"
            ),
        }
    )


def is_dioxygen_formula(formula: str) -> bool:
    """True for O2 / O2(g) / O2(g,ideal) etc."""

    bare = formula.strip().split("(", 1)[0]
    return bare == "O2"


__all__ = [
    "CHANNEL_O2",
    "CHANNEL_H2",
    "CHANNEL_F2",
    "CHANNEL_Cl2",
    "CHANNEL_Br2",
    "CHANNEL_I2",
    "CHANNEL_S2",
    "CHANNEL_N2",
    "CHANNEL_REGISTRY",
    "FORMULA_TO_CHANNEL_ID",
    "REACTION_PLANE_MELT_INTERFACE",
    "REACTION_PLANE_TRANSPORT_HEADSPACE",
    "REACTION_PLANES",
    "LEGACY_FO2_PLANE",
    "ChannelRegistryEntry",
    "ReactionTermRole",
    "CompiledReactionTerm",
    "derived_exponent",
    "compile_o2_channel_term",
    "compile_channel_term_from_binding",
    "ChannelVerdictKind",
    "GasChannelPotential",
    "ReactionThermoInputs",
    "ChannelConstructionError",
    "ChannelEvaluationError",
    "RESOLVER_OWNER_PHASE1",
    "SOURCE_KIND_LEGACY_SCALAR_ADAPTER",
    "REFUSAL_MISSING_CHANNEL_INPUT",
    "REFUSAL_HALIDE_RESERVOIR_OWNER_MISSING",
    "REFUSAL_SULFUR_RESERVOIR_OWNER_MISSING",
    "REFUSAL_CHANNEL_RUNTIME_OWNER_MISSING",
    "REFUSAL_CARBON_SIDE_OWNER_MISSING",
    "clamp_physical_pO2_bar",
    "o2_potential_from_pO2_bar",
    "channel_log10_contribution",
    "channel_linear_mass_action_factor",
    "resolve_channel_potential",
    "ChannelCompositionRefusal",
    "infer_required_channels_from_carrier",
    "attempt_channel_composition",
    "TypedGapReason",
    "parse_needs_channel_entry",
    "load_typed_needs_channel_reasons",
    "reconstruct_878_pathway_cohort",
    "count_o2_dependent_compiled_evaluators",
    "is_dioxygen_formula",
]
