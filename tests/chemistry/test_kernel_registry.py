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
