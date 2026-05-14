"""MAGEMin kernel-shadow provider scaffold.

This module declares the intent surface the chemistry kernel will require
when ``\\goal CHEMISTRY-KERNEL-CARVE-OUT`` lands. Until then this is a
scaffold: it forward-declares the provider shape, does not register with
any kernel, and delegates real chemistry calls to the today-hook adapter
in :mod:`simulator.melt_backend.magemin`.

Authority posture
-----------------
MAGEMin is **shadow-only** for ``SILICATE_LIQUIDUS`` and
``SILICATE_EQUILIBRIUM`` (see
``docs-private/chemistry-engine-binding-spec-2026-05-14.md`` §3). It must
never emit a ``LedgerTransitionProposal``. The provider's
``is_authoritative_for(intent)`` always returns ``False``.

Reconciliation
--------------
The today-hook (``simulator.melt_backend.magemin.MAGEMinBackend``) is the
*only* place where MAGEMin chemistry is actually executed today. This
provider stub adds a kernel-shape envelope around that call so that when
the kernel exists, both adapter and provider share a single call site —
not two parallel chemistry paths.

See ``\\goal MAGEMIN-SHADOW-PARITY`` in
``docs-private/codex-goal-queue-2026-05-14.md`` for the promotion plan.
"""

from __future__ import annotations

from typing import Any, FrozenSet, Optional

# TODO(kernel): replace with `from simulator.chemistry.kernel.provider import ChemistryProvider`
# and `from simulator.chemistry.kernel.capabilities import ChemistryIntent, CapabilityProfile`
# once \goal CHEMISTRY-KERNEL-CARVE-OUT lands (see
# docs-private/codex-goal-queue-2026-05-14.md).
# Until then, this stub class declares the intent surface kernel will require:
#   - intents() -> set[ChemistryIntent]
#   - capability_profile() -> CapabilityProfile
#   - account_view_filter(view) -> view
#   - dispatch(request) -> IntentResult
#   - emits LedgerTransitionProposal? -> False (shadow-only)


# String literals stand in for the ChemistryIntent enum until the kernel lands.
# The strings here MUST stay in lock-step with the enum names introduced by
# \goal CHEMISTRY-KERNEL-CARVE-OUT (simulator/chemistry/kernel/capabilities.py).
_SILICATE_LIQUIDUS = 'SILICATE_LIQUIDUS'
_SILICATE_EQUILIBRIUM = 'SILICATE_EQUILIBRIUM'


class MAGEMinShadowProvider:
    """Kernel-shadow provider stub for MAGEMin.

    Today this class is not wired into anything — it documents the intent
    surface and delegates to the today-hook adapter. Post-kernel it will
    subclass ``ChemistryProvider`` and register with the kernel as a
    shadow for ``SILICATE_LIQUIDUS`` and ``SILICATE_EQUILIBRIUM``.
    """

    name = 'magemin-shadow'

    def __init__(self) -> None:
        self._adapter: Optional[Any] = None  # lazy MAGEMinBackend

    def intents(self) -> FrozenSet[str]:
        """Intents this provider claims (shadow authority only)."""
        return frozenset({_SILICATE_LIQUIDUS, _SILICATE_EQUILIBRIUM})

    def is_authoritative_for(self, intent: str) -> bool:  # noqa: ARG002
        """MAGEMin is shadow-only. Always returns False.

        Promotion is gated by ``\\goal MAGEMIN-SHADOW-PARITY``: parity
        tolerance ±50 K liquidus / ±2 wt% modal vs alphaMELTS, validated
        on at least one lunar + one Mars + one asteroid feedstock. Even
        after that gate passes, this provider stays shadow — alphaMELTS
        retains authority per the binding spec §3 authority matrix.
        """
        return False

    def emits_ledger_transition(self) -> bool:
        """Shadow providers never emit ledger transitions."""
        return False

    def account_view_filter(self, view: Any) -> Any:
        """Filter the kernel-supplied account view before dispatch.

        Today this is a passthrough — the kernel hasn't been carved out
        yet, so there is no ``ProviderAccountView`` type to constrain.
        When ``\\goal CHEMISTRY-KERNEL-CARVE-OUT`` lands, this method
        must drop every account except ``process.cleaned_melt`` (same
        constraint as alphaMELTS — MAGEMin operates on silicate-oxide
        bulk only, no gas/metal/salt/sulfide).
        """
        return view

    def capability_profile(self) -> dict:
        """Report the capability surface MAGEMin advertises.

        Returned as a plain dict until ``CapabilityProfile`` lands in
        ``simulator.chemistry.kernel.capabilities``. Field names match
        the planned dataclass so the post-kernel migration is purely
        mechanical.
        """
        return {
            'engine': 'magemin',
            'engine_version': 'unknown',
            'intents': sorted(self.intents()),
            'authoritative_intents': frozenset(),
            'shadow_intents': self.intents(),
            'oxide_basis': (
                'SiO2', 'TiO2', 'Al2O3', 'FeO', 'Fe2O3', 'MgO', 'CaO',
                'Na2O', 'K2O', 'Cr2O3', 'MnO', 'P2O5', 'NiO', 'CoO',
            ),
            'pressure_unit': 'GPa',
            'temperature_unit': 'K',
        }

    def dispatch(self, request: Any) -> Any:
        """Execute a kernel intent request. Not implemented until kernel lands.

        Once ``\\goal CHEMISTRY-KERNEL-CARVE-OUT`` lands this method will:

        1. Validate ``request.intent`` is in :meth:`intents`.
        2. Validate ``request.account_view`` is filtered (silicate-only).
        3. Call :meth:`delegate_to_adapter` to run MAGEMin via the
           today-hook.
        4. Wrap the ``EquilibriumResult`` in an ``IntentResult`` with
           ``ledger_transition=None`` (shadow-only) and parity-comparison
           metadata for the kernel trace.

        See ``\\goal MAGEMIN-SHADOW-PARITY`` for the promotion plan.
        """
        raise NotImplementedError(
            'MAGEMinShadowProvider.dispatch() requires the chemistry kernel '
            '(simulator.chemistry.kernel). The kernel has not been carved out '
            'yet — see \\goal CHEMISTRY-KERNEL-CARVE-OUT and '
            '\\goal MAGEMIN-SHADOW-PARITY in '
            'docs-private/codex-goal-queue-2026-05-14.md. Until then, the '
            'simulator should call simulator.melt_backend.magemin.MAGEMinBackend '
            'directly via the existing MeltBackend interface.'
        )

    def delegate_to_adapter(self, *args: Any, **kwargs: Any) -> Any:
        """Reconciliation point: provider and adapter share one call site.

        The provider does not own a second copy of MAGEMin call logic. It
        forwards to the today-hook adapter (``MAGEMinBackend.equilibrate``)
        and, post-kernel, wraps the result in a kernel-shape envelope.

        The import is deliberately lazy because
        ``simulator.melt_backend.magemin`` may be undergoing concurrent
        edits in another worktree; importing at module load would create
        a hard dependency on the adapter's import-time surface.
        """
        if self._adapter is None:
            # TODO(reconciliation): once \goal MAGEMIN-SHADOW-PARITY lands,
            # this adapter instance is constructed/owned by the kernel
            # registry rather than the provider. For now it's lazy so the
            # provider module is importable even if the adapter is being
            # rewritten.
            from simulator.melt_backend.magemin import MAGEMinBackend
            self._adapter = MAGEMinBackend()
        return self._adapter.equilibrate(*args, **kwargs)
