"""Registration table mapping intents to providers.

Each intent has at most ONE authoritative provider (whose result
becomes a :class:`LedgerTransitionProposal`) and any number of shadow
providers (whose results are recorded for trace and parity testing but
never committed).  Conflicting authoritative registrations raise
:class:`KernelError`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Optional

from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.chemistry.kernel.errors import KernelError
from simulator.chemistry.kernel.provider import ChemistryProvider


class ProviderRegistry:
    """Mapping of :class:`ChemistryIntent` to provider entries.

    Use :meth:`register` to attach a provider to an intent; the
    ``shadow`` flag decides whether the provider's result is treated
    as authoritative (default) or merely shadow.  Lookup via
    :meth:`authoritative_for` and :meth:`shadows_for`.
    """

    def __init__(self) -> None:
        self._authoritative: dict[ChemistryIntent, ChemistryProvider] = {}
        self._shadows: dict[ChemistryIntent, list[ChemistryProvider]] = {}

    def register(
        self,
        provider: ChemistryProvider,
        intents: Iterable[ChemistryIntent],
        *,
        shadow: bool = False,
    ) -> None:
        """Attach ``provider`` to each intent in ``intents``.

        Args:
            provider: A :class:`ChemistryProvider` instance.  Its
                :class:`CapabilityProfile` must declare every intent
                being registered against it, and for non-shadow
                registrations the intent must appear in
                ``is_authoritative_for``.
            intents: Iterable of :class:`ChemistryIntent` values.
            shadow: If True, register as a shadow provider; otherwise
                register as authoritative.  At most one authoritative
                provider per intent.

        Raises:
            KernelError: An authoritative provider is already
                registered for one of the requested intents (when
                ``shadow=False``), or the provider's
                :class:`CapabilityProfile` does not cover the intent.
        """

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
            if not shadow:
                if not profile.is_authoritative(intent):
                    raise KernelError(
                        f"provider {profile.provider_id!r} cannot be authoritative for "
                        f"{intent.value!r}: missing from CapabilityProfile."
                        f"is_authoritative_for"
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
    ) -> None:
        """Register ``provider`` only if it is not already attached.

        Sister of :meth:`register` for call sites that may run multiple
        times per simulator lifetime (e.g. ``_build_chemistry_kernel``
        rebuilds the kernel facade on every batch but keeps the
        registry).  Semantics:

        * If no provider is registered against an intent yet, register
          this one (authoritative or shadow per ``shadow``).
        * If THIS provider is already registered authoritatively for
          the intent, no-op.
        * If a DIFFERENT provider holds authority for the intent and
          ``shadow=False``, raise :class:`KernelError`.  Idempotent
          re-registration is for "same provider, same authority", not
          "swap providers silently".
        * Shadow registrations no-op if ``provider`` already appears
          in the shadow list for the intent; otherwise append.

        The non-idempotent :meth:`register` still raises on any
        repeat authoritative attempt, even with the same provider.
        Callers wanting idempotence must opt in explicitly.
        """

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
            if not shadow:
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
            self.register(provider, to_register, shadow=shadow)

    def authoritative_for(
        self, intent: ChemistryIntent
    ) -> Optional[ChemistryProvider]:
        """Return the authoritative provider for ``intent``, or None."""

        return self._authoritative.get(intent)

    def shadows_for(
        self, intent: ChemistryIntent
    ) -> tuple[ChemistryProvider, ...]:
        """Return the tuple of shadow providers for ``intent``."""

        return tuple(self._shadows.get(intent, ()))

    def registered_intents(self) -> frozenset[ChemistryIntent]:
        """Every intent with at least one registered provider."""

        return frozenset(self._authoritative) | frozenset(self._shadows)
