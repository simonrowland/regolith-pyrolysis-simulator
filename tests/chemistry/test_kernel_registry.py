"""Kernel invariant: :meth:`ProviderRegistry.register_idempotent` is safe to call repeatedly.

The non-idempotent :meth:`ProviderRegistry.register` raises on any
double authoritative registration -- forcing every call site to guard
with a ``registry.authoritative_for(intent) is None`` check.  As more
intents flip to the kernel, that pattern bloats
``_build_chemistry_kernel`` linearly.

:meth:`register_idempotent` no-ops on "same provider, same authority"
re-registrations but STILL refuses to swap providers silently for an
intent that already has an authoritative holder.
"""

from __future__ import annotations

import pytest

from simulator.chemistry.kernel import (
    CapabilityProfile,
    ChemistryIntent,
    ChemistryProvider,
    IntentRequest,
    IntentResult,
    KernelError,
    ProviderRegistry,
)


class _IdemProviderA(ChemistryProvider):
    name = "idem_a"

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="idem_a",
            intents=frozenset({ChemistryIntent.VAPOR_PRESSURE}),
            is_authoritative_for=frozenset({ChemistryIntent.VAPOR_PRESSURE}),
            declared_accounts=frozenset({"process.cleaned_melt"}),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        return IntentResult(
            intent=request.intent,
            status="ok",
            transition=None,
            control_audit=None,
            diagnostic={"source": "idem_a"},
            warnings=(),
        )


class _IdemProviderB(ChemistryProvider):
    """A DIFFERENT provider also authoritative for VAPOR_PRESSURE."""

    name = "idem_b"

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="idem_b",
            intents=frozenset({ChemistryIntent.VAPOR_PRESSURE}),
            is_authoritative_for=frozenset({ChemistryIntent.VAPOR_PRESSURE}),
            declared_accounts=frozenset({"process.cleaned_melt"}),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        return IntentResult(
            intent=request.intent,
            status="ok",
            transition=None,
            control_audit=None,
            diagnostic={"source": "idem_b"},
            warnings=(),
        )


class _IdemShadow(ChemistryProvider):
    name = "idem_shadow"

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="idem_shadow",
            intents=frozenset({ChemistryIntent.VAPOR_PRESSURE}),
            is_authoritative_for=frozenset(),  # diagnostic only
            declared_accounts=frozenset({"process.cleaned_melt"}),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        return IntentResult(
            intent=request.intent,
            status="ok",
            transition=None,
            control_audit=None,
            diagnostic={"source": "idem_shadow"},
            warnings=(),
        )


def test_register_idempotent_no_ops_when_same_provider_already_authoritative():
    """Calling twice with the SAME provider must not raise and must not
    duplicate the registration.
    """

    registry = ProviderRegistry()
    provider = _IdemProviderA()
    registry.register_idempotent(provider, [ChemistryIntent.VAPOR_PRESSURE])
    # Second call: same provider, same intent -- no-op.
    registry.register_idempotent(provider, [ChemistryIntent.VAPOR_PRESSURE])
    assert registry.authoritative_for(ChemistryIntent.VAPOR_PRESSURE) is provider


def test_register_idempotent_no_ops_when_fresh_instance_has_same_provider_id():
    """Different instance, same ``provider_id`` -- no-op.

    This is the load-bearing path for ``_build_chemistry_kernel``: it
    constructs a fresh provider instance on every batch but the
    ``CapabilityProfile.provider_id`` is stable.  Identity comparison
    would falsely flag the second batch as a swap attempt and raise.
    """

    registry = ProviderRegistry()
    first = _IdemProviderA()
    registry.register_idempotent(first, [ChemistryIntent.VAPOR_PRESSURE])
    # New instance, same provider_id.
    second = _IdemProviderA()
    assert second is not first
    assert (
        second.capability_profile().provider_id
        == first.capability_profile().provider_id
    )
    registry.register_idempotent(second, [ChemistryIntent.VAPOR_PRESSURE])
    # Registry keeps the first registration; the second call no-ops.
    assert registry.authoritative_for(ChemistryIntent.VAPOR_PRESSURE) is first


def test_register_idempotent_raises_on_conflicting_authority():
    """A DIFFERENT provider for the same intent must NOT silently swap in."""

    registry = ProviderRegistry()
    registry.register_idempotent(_IdemProviderA(), [ChemistryIntent.VAPOR_PRESSURE])
    with pytest.raises(KernelError):
        registry.register_idempotent(
            _IdemProviderB(), [ChemistryIntent.VAPOR_PRESSURE]
        )
    # Authority must still belong to A.
    holder = registry.authoritative_for(ChemistryIntent.VAPOR_PRESSURE)
    assert holder is not None
    assert holder.capability_profile().provider_id == "idem_a"


def test_register_idempotent_first_call_actually_registers():
    """Fresh registry -- the first idempotent call must take effect."""

    registry = ProviderRegistry()
    assert registry.authoritative_for(ChemistryIntent.VAPOR_PRESSURE) is None
    provider = _IdemProviderA()
    registry.register_idempotent(provider, [ChemistryIntent.VAPOR_PRESSURE])
    assert registry.authoritative_for(ChemistryIntent.VAPOR_PRESSURE) is provider


def test_register_idempotent_shadow_branch_no_ops_on_double():
    """Idempotence also covers the shadow branch."""

    registry = ProviderRegistry()
    shadow = _IdemShadow()
    registry.register_idempotent(
        _IdemProviderA(), [ChemistryIntent.VAPOR_PRESSURE]
    )
    registry.register_idempotent(
        shadow, [ChemistryIntent.VAPOR_PRESSURE], shadow=True
    )
    # Second call with same shadow -- no-op (not duplicated).
    registry.register_idempotent(
        shadow, [ChemistryIntent.VAPOR_PRESSURE], shadow=True
    )
    shadows = registry.shadows_for(ChemistryIntent.VAPOR_PRESSURE)
    assert len(shadows) == 1
    assert shadows[0] is shadow


def test_non_idempotent_register_still_raises_on_double_authority():
    """``register`` (without idempotent suffix) keeps its strict
    "no duplicate authority" contract -- this is the safety mechanism
    that callers explicitly opt out of via ``register_idempotent``.
    """

    registry = ProviderRegistry()
    registry.register(_IdemProviderA(), [ChemistryIntent.VAPOR_PRESSURE])
    with pytest.raises(KernelError):
        # Even SAME provider re-registration raises via the strict API.
        registry.register(
            _IdemProviderA(), [ChemistryIntent.VAPOR_PRESSURE]
        )


def test_register_idempotent_rejects_non_intent_value():
    """Type-check on intents is preserved across the idempotent API."""

    registry = ProviderRegistry()
    with pytest.raises(KernelError):
        registry.register_idempotent(_IdemProviderA(), ["not_an_intent"])


# ---------------------------------------------------------------------
# Goal #10 ``VAPOROCK-AUTHORITY-PROMOTION`` is a historical name only:
# fallback slot semantics with builtin pressure provider plus diagnostics.
# ---------------------------------------------------------------------


def test_fallback_slot_accepts_authority_capable_provider():
    """A provider with authority capability may sit in the fallback slot.

    Goal #10 binds this surface: the builtin Antoine provider is
    demoted from authoritative to fallback for VAPOR_PRESSURE.  Its
    :class:`CapabilityProfile` keeps ``is_authoritative_for`` populated
    so the registry's fallback slot accepts it.
    """

    registry = ProviderRegistry()
    registry.register_idempotent(
        _IdemProviderB(), [ChemistryIntent.VAPOR_PRESSURE], fallback=True
    )
    fallback = registry.fallback_for(ChemistryIntent.VAPOR_PRESSURE)
    assert fallback is not None
    assert fallback.capability_profile().provider_id == 'idem_b'
    # The authoritative slot is independent and still empty.
    assert registry.authoritative_for(ChemistryIntent.VAPOR_PRESSURE) is None


def test_fallback_rejects_shadow_only_provider():
    """A shadow-only provider (empty ``is_authoritative_for``) is not a
    legal fallback -- the fallback must be able to step in as the
    authoritative answer when the authoritative provider is missing.
    """

    registry = ProviderRegistry()
    with pytest.raises(KernelError, match='cannot be fallback'):
        registry.register(
            _IdemShadow(),
            [ChemistryIntent.VAPOR_PRESSURE],
            fallback=True,
        )


def test_fallback_and_authoritative_cannot_be_same_provider_object():
    """The same provider object cannot hold both slots for an intent."""

    registry = ProviderRegistry()
    provider = _IdemProviderA()
    registry.register(provider, [ChemistryIntent.VAPOR_PRESSURE])
    with pytest.raises(KernelError, match='already the authoritative'):
        registry.register(
            provider, [ChemistryIntent.VAPOR_PRESSURE], fallback=True
        )


def test_authoritative_and_fallback_cannot_be_same_provider_object():
    """Mirror of the above: registering as fallback first must block
    a subsequent authoritative registration for the same intent.
    """

    registry = ProviderRegistry()
    provider = _IdemProviderA()
    registry.register(
        provider, [ChemistryIntent.VAPOR_PRESSURE], fallback=True
    )
    with pytest.raises(KernelError, match='already the fallback'):
        registry.register(provider, [ChemistryIntent.VAPOR_PRESSURE])


def test_register_rejects_shadow_and_fallback_simultaneously():
    """``shadow=True`` and ``fallback=True`` are mutually exclusive."""

    registry = ProviderRegistry()
    with pytest.raises(KernelError, match='mutually exclusive'):
        registry.register(
            _IdemProviderA(),
            [ChemistryIntent.VAPOR_PRESSURE],
            shadow=True,
            fallback=True,
        )


def test_fallback_idempotent_re_register_is_noop():
    """Calling twice with the same fallback provider must not raise."""

    registry = ProviderRegistry()
    provider = _IdemProviderB()
    registry.register_idempotent(
        provider, [ChemistryIntent.VAPOR_PRESSURE], fallback=True
    )
    registry.register_idempotent(
        provider, [ChemistryIntent.VAPOR_PRESSURE], fallback=True
    )
    fallback = registry.fallback_for(ChemistryIntent.VAPOR_PRESSURE)
    assert fallback is provider


def test_fallback_idempotent_raises_on_different_provider_id():
    """A DIFFERENT provider for the same fallback slot must NOT silently
    swap in, mirroring the authoritative-slot contract.
    """

    registry = ProviderRegistry()
    registry.register_idempotent(
        _IdemProviderA(), [ChemistryIntent.VAPOR_PRESSURE], fallback=True
    )
    with pytest.raises(KernelError, match='conflicting fallback'):
        registry.register_idempotent(
            _IdemProviderB(),
            [ChemistryIntent.VAPOR_PRESSURE],
            fallback=True,
        )


def test_capability_summary_keys_match_registered_intents():
    """The summary surfaces every registered intent with its slot state."""

    registry = ProviderRegistry()
    registry.register(_IdemProviderA(), [ChemistryIntent.VAPOR_PRESSURE])
    registry.register(
        _IdemProviderB(), [ChemistryIntent.VAPOR_PRESSURE], fallback=True
    )
    registry.register(
        _IdemShadow(), [ChemistryIntent.VAPOR_PRESSURE], shadow=True
    )
    summary = registry.capability_summary()
    assert set(summary.keys()) == {ChemistryIntent.VAPOR_PRESSURE.value}
    entry = summary[ChemistryIntent.VAPOR_PRESSURE.value]
    assert entry['authoritative'] == 'idem_a'
    assert entry['fallback'] == 'idem_b'
    assert entry['shadows'] == ('idem_shadow',)


# ---------------------------------------------------------------------------
# 0.5.4.1 B3 (M1 historical-audit closure): public replace_for_test seam
# ---------------------------------------------------------------------------

def test_replace_for_test_swaps_authoritative_provider():
    """The public test-seam swaps the authoritative provider for an
    intent and returns the prior provider so the caller can restore
    in a try/finally. Replaces the prior pattern of directly
    mutating ``registry._authoritative[intent]`` which would break
    silently if the registry internals were renamed."""

    registry = ProviderRegistry()
    a = _IdemProviderA()
    b = _IdemProviderB()
    registry.register_idempotent(a, [ChemistryIntent.VAPOR_PRESSURE])
    assert registry.authoritative_for(
        ChemistryIntent.VAPOR_PRESSURE
    ) is a

    prior = registry.replace_for_test(
        ChemistryIntent.VAPOR_PRESSURE, b
    )
    assert prior is a
    assert registry.authoritative_for(
        ChemistryIntent.VAPOR_PRESSURE
    ) is b

    # Restore via the same seam.
    restored_prior = registry.replace_for_test(
        ChemistryIntent.VAPOR_PRESSURE, prior
    )
    assert restored_prior is b
    assert registry.authoritative_for(
        ChemistryIntent.VAPOR_PRESSURE
    ) is a


def test_replace_for_test_with_none_clears_authoritative_slot():
    """Passing ``provider=None`` clears the authoritative slot; the
    caller can then re-register via the canonical
    ``register(...)`` path. Returns the prior provider (or
    ``None`` if the slot was empty)."""

    registry = ProviderRegistry()
    a = _IdemProviderA()
    registry.register_idempotent(a, [ChemistryIntent.VAPOR_PRESSURE])

    prior = registry.replace_for_test(
        ChemistryIntent.VAPOR_PRESSURE, None
    )
    assert prior is a
    assert registry.authoritative_for(
        ChemistryIntent.VAPOR_PRESSURE
    ) is None

    # Idempotent: clearing an already-empty slot returns None and
    # doesn't raise.
    prior2 = registry.replace_for_test(
        ChemistryIntent.VAPOR_PRESSURE, None
    )
    assert prior2 is None


def test_replace_for_test_returns_none_when_slot_was_empty():
    """The first call to the seam (when no authoritative provider was
    registered) returns ``None`` for the prior slot, matching the
    private-dict get-default pattern."""

    registry = ProviderRegistry()
    a = _IdemProviderA()
    prior = registry.replace_for_test(
        ChemistryIntent.VAPOR_PRESSURE, a
    )
    assert prior is None
    assert registry.authoritative_for(
        ChemistryIntent.VAPOR_PRESSURE
    ) is a


def test_replace_for_test_skips_capability_validation_intentionally():
    """The seam DOES NOT re-validate that the provider's
    ``CapabilityProfile.is_authoritative_for`` includes the intent.
    This matches the prior direct-dict-mutation contract and lets
    tests deliberately install a shadow-only provider as
    authoritative for an isolated scenario. Documented behaviour;
    the seam name carries ``_for_test`` so a future review can flag
    any production caller."""

    registry = ProviderRegistry()
    shadow_only = _IdemShadow()
    # Shadow-only provider has ``is_authoritative_for=frozenset()``,
    # so canonical register() rejects it for authoritative. The
    # seam intentionally bypasses the check.
    registry.replace_for_test(
        ChemistryIntent.VAPOR_PRESSURE, shadow_only
    )
    assert registry.authoritative_for(
        ChemistryIntent.VAPOR_PRESSURE
    ) is shadow_only
