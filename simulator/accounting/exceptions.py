"""Accounting exception types."""


class AccountingError(ValueError):
    """Base class for accounting failures."""


class UnknownSpeciesError(AccountingError):
    """Raised when a species has no usable formula."""


class UnbalancedTransitionError(AccountingError):
    """Raised when a transition does not conserve atoms or mass."""


class OverdraftError(AccountingError):
    """Raised when an account is debited past its policy limit."""


class AccountCreditPolicyError(AccountingError):
    """Raised when a reservoir has no configured credit limit for a species.

    b-284 / SC-146. Deliberately NOT a subclass of OverdraftError, because it is
    not an overdraw: nothing has been drawn past a limit, there IS no limit to
    draw past. It says the account's credit policy is incomplete -- a
    configuration gap, which under the three-category rule is missing input and
    must refuse, not a physical property of the recipe being evaluated.

    The distinction is load-bearing downstream. The optimizer catches
    OverdraftError and classifies it as ``inventory_overdraw``, then prunes the
    candidate as infeasible. Raising this as an OverdraftError therefore made a
    missing config entry look like a recipe that tried to draw more inventory
    than exists -- and the operator never learned the config was incomplete,
    because the candidate was silently scored as infeasible instead.
    """


class PoolWithdrawalError(AccountingError):
    """Raised when a well-mixed pool withdrawal is invalid."""


class MaterialOriginError(AccountingError):
    """Raised when an external material producer omits or corrupts typed origin."""


class OriginUnresolvedError(AccountingError):
    """Raised when feedstock and reagent atoms cannot be separated without guessing."""
