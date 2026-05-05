"""Accounting exception types."""


class AccountingError(ValueError):
    """Base class for accounting failures."""


class UnknownSpeciesError(AccountingError):
    """Raised when a species has no usable formula."""


class UnbalancedTransitionError(AccountingError):
    """Raised when a transition does not conserve atoms or mass."""


class OverdraftError(AccountingError):
    """Raised when an account is debited past its policy limit."""

