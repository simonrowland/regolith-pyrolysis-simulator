"""Registration table mapping intents to providers.

Each intent has at most ONE authoritative provider (whose result
becomes a :class:`LedgerTransitionProposal`), at most ONE fallback
provider (used only when the authoritative provider is absent/unavailable
AND the caller opted into fallback for that intent), and any number of
shadow providers (whose results are recorded for trace and parity testing
but never committed).  Conflicting
authoritative or fallback registrations raise :class:`KernelError`.

The fallback slot was added under \\goal VAPOROCK-AUTHORITY-PROMOTION
(#10) so VapoRock can take VAPOR_PRESSURE authority with the existing
builtin Antoine/Ellingham provider held in reserve. The fallback only
runs when ``ChemistryKernel.dispatch`` receives the matching
``allow_fallback_<intent>`` opt-in -- silent fallback is forbidden by
the goal spec.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Optional

from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.chemistry.kernel.errors import KernelError
from simulator.chemistry.kernel.provider import ChemistryProvider


class ProviderRegistry:
    """Mapping of :class:`ChemistryIntent` to provider entries.

    Use :meth:`register` to attach a provider to an intent.  The
    ``shadow`` / ``fallback`` flags decide whether the provider's result
    is treated as authoritative (default), fallback (consulted only on
    authoritative ``ProviderUnavailableError`` and only when the caller
    opted in), or shadow (recorded for trace, never committed).  Lookup
    via :meth:`authoritative_for`, :meth:`fallback_for`, and
    :meth:`shadows_for`.
    """

    def __init__(self) -> None:
        self._authoritative: dict[ChemistryIntent, ChemistryProvider] = {}
        self._fallback: dict[ChemistryIntent, ChemistryProvider] = {}
        self._shadows: dict[ChemistryIntent, list[ChemistryProvider]] = {}

    def register(
        self,
        provider: ChemistryProvider,
        intents: Iterable[ChemistryIntent],
        *,
        shadow: bool = False,
        fallback: bool = False,
    ) -> None:
        """Attach ``provider`` to each intent in ``intents``.

        Args:
            provider: A :class:`ChemistryProvider` instance.  Its
                :class:`CapabilityProfile` must declare every intent
                being registered against it.  For authoritative and
                fallback registrations the intent must also appear in
                ``is_authoritative_for`` -- a fallback must itself be
                capable of authority, otherwise nothing can take over
                when the authoritative provider is unavailable.
            intents: Iterable of :class:`ChemistryIntent` values.
            shadow: If True, register as a shadow provider; mutually
                exclusive with ``fallback``.
            fallback: If True, register as the fallback provider for
                this intent.  At most one fallback per intent; the same
                provider cannot be both authoritative and fallback for
                the same intent.  ``ChemistryKernel.dispatch`` consults
                the fallback only when the authoritative provider is
                absent/unavailable and the caller opted into fallback
                for that intent.

        Raises:
            KernelError: An authoritative / fallback provider is
                already registered for one of the requested intents
                (when the matching flag is set), ``shadow`` and
                ``fallback`` are both True, the provider's
                :class:`CapabilityProfile` does not cover the intent,
                or a non-shadow registration targets an intent the
                provider has not declared authority for.
        """

        if shadow and fallback:
            raise KernelError(
                "registry.register: shadow=True and fallback=True are "
                "mutually exclusive; a single registration cannot be "
                "both"
            )
        if not isinstance(provider, ChemistryProvider):
            raise KernelError(
                f"registry.register expected ChemistryProvider, got {type(provider).__name__}"
            )
        profile = provider.capability_profile()
        intent_list = list(intents)
        for intent in intent_list:
            if not isinstance(intent, ChemistryIntent):
                raise KernelError(
                    f"intent {intent!r} is not a ChemistryIntent enum value"
                )
            if not profile.can_dispatch(intent):
                raise KernelError(
                    f"provider {profile.provider_id!r} does not declare intent "
                    f"{intent.value!r}; CapabilityProfile.intents = "
                    f"{sorted(i.value for i in profile.intents)}"
                )
            if fallback:
                # Fallback must itself be authority-capable: when the
                # authoritative provider is missing, the fallback
                # produces the LedgerTransitionProposal in its place.
                # An is_authoritative_for=frozenset() provider promoted
                # to fallback would still produce diagnostic-only
                # results -- legal but contractually equivalent to
                # "no fallback wired", so we reject it loudly.
                if not profile.is_authoritative(intent):
                    raise KernelError(
                        f"provider {profile.provider_id!r} cannot be fallback for "
                        f"{intent.value!r}: missing from CapabilityProfile."
                        f"is_authoritative_for (fallback must be "
                        f"authority-capable)"
                    )
                if self._authoritative.get(intent) is provider:
                    raise KernelError(
                        f"provider {profile.provider_id!r} is already the "
                        f"authoritative holder for {intent.value!r}; "
                        f"cannot also register it as fallback for the "
                        f"same intent"
                    )
                existing = self._fallback.get(intent)
                if existing is not None and existing is not provider:
                    raise KernelError(
                        f"conflicting fallback registration for {intent.value!r}: "
                        f"{existing.capability_profile().provider_id!r} already holds it, "
                        f"refusing to register {profile.provider_id!r}"
                    )
                self._fallback[intent] = provider
            elif not shadow:
                if not profile.is_authoritative(intent):
                    raise KernelError(
                        f"provider {profile.provider_id!r} cannot be authoritative for "
                        f"{intent.value!r}: missing from CapabilityProfile."
                        f"is_authoritative_for"
                    )
                if self._fallback.get(intent) is provider:
                    raise KernelError(
                        f"provider {profile.provider_id!r} is already the "
                        f"fallback for {intent.value!r}; cannot also "
                        f"register it as authoritative for the same intent"
                    )
                existing = self._authoritative.get(intent)
                if existing is not None and existing is not provider:
                    raise KernelError(
                        f"conflicting authoritative registration for {intent.value!r}: "
                        f"{existing.capability_profile().provider_id!r} already holds it, "
                        f"refusing to register {profile.provider_id!r}"
                    )
                self._authoritative[intent] = provider
            else:
                shadows = self._shadows.setdefault(intent, [])
                if provider not in shadows:
                    shadows.append(provider)

    def register_idempotent(
        self,
        provider: ChemistryProvider,
        intents: Iterable[ChemistryIntent],
        *,
        shadow: bool = False,
        fallback: bool = False,
    ) -> None:
        """Register ``provider`` only if it is not already attached.

        Sister of :meth:`register` for call sites that may run multiple
        times per simulator lifetime (e.g. ``_build_chemistry_kernel``
        rebuilds the kernel facade on every batch but keeps the
        registry).  Semantics:

        * If no provider is registered against an intent yet, register
          this one (authoritative / fallback / shadow per the flags).
        * If THIS provider is already registered in the requested slot
          for the intent, no-op.
        * If a DIFFERENT provider holds the same slot (authoritative or
          fallback) for the intent, raise :class:`KernelError`.
          Idempotent re-registration is for "same provider, same slot",
          not "swap providers silently".
        * Shadow registrations no-op if ``provider`` already appears in
          the shadow list for the intent; otherwise append.

        The non-idempotent :meth:`register` still raises on any repeat
        attempt, even with the same provider.  Callers wanting
        idempotence must opt in explicitly.
        """

        if shadow and fallback:
            raise KernelError(
                "register_idempotent: shadow=True and fallback=True are "
                "mutually exclusive"
            )
        # Compare by ``provider_id`` rather than object identity: call
        # sites that rebuild kernels per batch (e.g.
        # ``_build_chemistry_kernel``) construct a fresh provider
        # instance every time but the registry's authority is keyed on
        # provider identity (the ``CapabilityProfile.provider_id``).  An
        # idempotent call with the same provider_id is the "already
        # registered" path; a different provider_id is a swap attempt
        # and must raise.
        new_id = provider.capability_profile().provider_id
        intent_list = list(intents)
        to_register: list[ChemistryIntent] = []
        for intent in intent_list:
            if not isinstance(intent, ChemistryIntent):
                raise KernelError(
                    f"intent {intent!r} is not a ChemistryIntent enum value"
                )
            if fallback:
                existing = self._fallback.get(intent)
                if existing is not None:
                    existing_id = existing.capability_profile().provider_id
                    if existing_id == new_id:
                        continue
                    raise KernelError(
                        f"register_idempotent: conflicting fallback "
                        f"registration for {intent.value!r}: "
                        f"{existing_id!r} already holds it, refusing to swap "
                        f"in {new_id!r}"
                    )
                to_register.append(intent)
            elif not shadow:
                existing = self._authoritative.get(intent)
                if existing is not None:
                    existing_id = existing.capability_profile().provider_id
                    if existing_id == new_id:
                        continue
                    raise KernelError(
                        f"register_idempotent: conflicting authoritative "
                        f"registration for {intent.value!r}: "
                        f"{existing_id!r} already holds it, refusing to swap "
                        f"in {new_id!r}"
                    )
                to_register.append(intent)
            else:
                shadow_ids = {
                    p.capability_profile().provider_id
                    for p in self._shadows.get(intent, ())
                }
                if new_id in shadow_ids:
                    continue
                to_register.append(intent)
        if to_register:
            self.register(
                provider, to_register, shadow=shadow, fallback=fallback
            )

    def authoritative_for(
        self, intent: ChemistryIntent
    ) -> Optional[ChemistryProvider]:
        """Return the authoritative provider for ``intent``, or None."""

        return self._authoritative.get(intent)

    def replace_for_test(
        self,
        intent: ChemistryIntent,
        provider: Optional[ChemistryProvider],
    ) -> Optional[ChemistryProvider]:
        """Swap the authoritative provider for ``intent``; return prior.

        0.5.4.1 B3 / M1 historical-audit closure (2026-05-28). Test
        seam ONLY -- production code paths must not use this. Tests
        previously reached into ``sim._chem_registry._authoritative``
        and mutated the private dict directly (see e.g.
        ``tests/test_extraction_ledger.py``); that pattern silently
        breaks if the registry internals are renamed or moved to a
        tuple-keyed scheme. This public seam encapsulates the swap
        without changing any production semantics:

        - Pass a ``ChemistryProvider`` to install it as the new
          authoritative provider for ``intent``. The caller is
          responsible for ensuring the provider's
          ``CapabilityProfile.is_authoritative_for`` includes the
          intent (or the caller is intentionally bypassing that
          check for an isolated test scenario; this method does NOT
          re-validate, matching the prior direct-dict-mutation
          contract).
        - Pass ``None`` to clear the authoritative slot for the
          intent. The caller may then re-register via the canonical
          ``register(...)`` path. Returns whatever was previously in
          the slot (or ``None`` if it was empty), so the test can
          restore the prior state in a ``try`` / ``finally`` block.

        Method name explicitly carries ``_for_test`` so a future
        code-review can flag any production caller. Method docstring
        is the canonical reference for this contract.
        """

        previous = self._authoritative.get(intent)
        if provider is None:
            self._authoritative.pop(intent, None)
        else:
            self._authoritative[intent] = provider
        return previous

    def fallback_for(
        self, intent: ChemistryIntent
    ) -> Optional[ChemistryProvider]:
        """Return the fallback provider for ``intent``, or None.

        Only consulted when the authoritative provider is absent/unavailable
        AND the caller opted into fallback for the intent (see
        :meth:`ChemistryKernel.dispatch`).
        """

        return self._fallback.get(intent)

    def shadows_for(
        self, intent: ChemistryIntent
    ) -> tuple[ChemistryProvider, ...]:
        """Return the tuple of shadow providers for ``intent``."""

        return tuple(self._shadows.get(intent, ()))

    def registered_intents(self) -> frozenset[ChemistryIntent]:
        """Every intent with at least one registered provider."""

        return (
            frozenset(self._authoritative)
            | frozenset(self._fallback)
            | frozenset(self._shadows)
        )

    def capability_summary(self) -> dict[str, dict[str, object]]:
        """Snapshot of registered providers, keyed by intent.value.

        Returns a mapping ``intent_value -> {authoritative, fallback,
        shadows}`` where each entry holds either the
        ``CapabilityProfile.provider_id`` (a string) for the
        authoritative / fallback slot or ``None`` when no provider is
        registered there, and the shadow slot holds a tuple of
        provider_ids.  Goal #10 ``VAPOROCK-AUTHORITY-PROMOTION`` binds
        this surface: the authority swap (builtin -> VapoRock for
        VAPOR_PRESSURE) is auditable from a single read of this dict.
        """

        intents = sorted(self.registered_intents(), key=lambda i: i.value)
        summary: dict[str, dict[str, object]] = {}
        for intent in intents:
            auth = self._authoritative.get(intent)
            fall = self._fallback.get(intent)
            shadows = tuple(
                p.capability_profile().provider_id
                for p in self._shadows.get(intent, ())
            )
            summary[intent.value] = {
                "authoritative": (
                    auth.capability_profile().provider_id
                    if auth is not None
                    else None
                ),
                "fallback": (
                    fall.capability_profile().provider_id
                    if fall is not None
                    else None
                ),
                "shadows": shadows,
            }
        return summary
