"""Accounting exception types."""


class AccountingError(ValueError):
    """Base class for accounting failures."""


class UnknownSpeciesError(AccountingError):
    """Raised when a species has no usable formula."""


class UnbalancedTransitionError(AccountingError):
    """Raised when a transition does not conserve atoms or mass."""


class OverdraftError(AccountingError):
    """Raised when an account is debited past its policy limit."""


class PoolWithdrawalError(AccountingError):
    """Raised when a well-mixed pool withdrawal is invalid."""


class MaterialOriginError(AccountingError):
    """Raised when an external material producer omits or corrupts typed origin."""


class OriginUnresolvedError(AccountingError):
    """Raised when feedstock and reagent atoms cannot be separated without guessing."""
