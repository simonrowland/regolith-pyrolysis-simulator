"""Chemistry-provider kernel public surface.

Stand-alone scaffold: importing this package does NOT register any
provider or modify the legacy simulator code path.  Engine wire-up is
deferred to ``\\goal BUILTIN-ENGINE-EXTRACTION``.
"""

from __future__ import annotations

from simulator.chemistry.kernel.capabilities import (
    CapabilityProfile,
    ChemistryIntent,
)
from simulator.chemistry.kernel.dto import (
    ControlAudit,
    IntentRequest,
    IntentResult,
    LedgerTransitionProposal,
    ProviderAccountView,
)
from simulator.chemistry.kernel.errors import (
    AccountFilterViolation,
    AtomBalanceError,
    ControlAuditMismatch,
    KernelError,
    ProposalRejected,
    ProviderUnavailableError,
    UnauthorizedIntentError,
)
from simulator.chemistry.kernel.planner import ChemistryKernel
from simulator.chemistry.kernel.provider import ChemistryProvider
from simulator.chemistry.kernel.registry import ProviderRegistry

# Planner is an internal seam between ChemistryKernel and the registry;
# downstream callers use ChemistryKernel directly. Tests that need to
# poke the seam import it explicitly via
# ``simulator.chemistry.kernel.planner.Planner`` -- keeping it off the
# package surface stops accidental dependence in non-kernel code.

__all__ = (
    # DTOs
    "IntentRequest",
    "IntentResult",
    "LedgerTransitionProposal",
    "ControlAudit",
    "ProviderAccountView",
    # Capabilities + provider contract
    "ChemistryIntent",
    "CapabilityProfile",
    "ChemistryProvider",
    # Kernel runtime
    "ChemistryKernel",
    "ProviderRegistry",
    # Errors
    "KernelError",
    "UnauthorizedIntentError",
    "AccountFilterViolation",
    "AtomBalanceError",
    "ControlAuditMismatch",
    "ProviderUnavailableError",
    "ProposalRejected",
)
