"""Chemistry-kernel exception hierarchy.

Every failure mode the kernel enforces raises a subclass of
:class:`KernelError`.  Callers may catch the base class to treat any
kernel failure uniformly, or a specific subclass to react to a single
invariant.
"""

from __future__ import annotations


class KernelError(Exception):
    """Base class for all chemistry-kernel failures."""


class UnauthorizedIntentError(KernelError):
    """Provider emitted a ledger transition for an intent it does not own."""


class AccountFilterViolation(KernelError):
    """Provider received -- or wrote to -- an account it did not declare."""


class AtomBalanceError(KernelError):
    """A proposed ledger transition does not conserve atoms or mass."""


class ControlAuditMismatch(KernelError):
    """Applied T / P / fO2 disagree with the requested values without an audit note."""


class ProviderUnavailableError(KernelError):
    """No authoritative provider is registered for the requested intent."""


class ProposalRejected(KernelError):
    """A ledger transition proposal failed pre-commit validation."""
