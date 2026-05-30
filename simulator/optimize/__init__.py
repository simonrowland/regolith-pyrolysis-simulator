"""Recipe optimization boundary types.

Data-layer boundary (R-F7): the optimizer's run cache is the (Phase-O)
``simulator.results_store`` (sqlite/WAL keyed by the EvalSpec SHA-256), NOT the
legacy single-user ``simulator.persistence`` UI store. The optimizer's scoring
surface is the R-F3 ``simulator.trace.PhysicsTrace`` + ``simulator.accounting``
queries; optimizer modules MUST NOT import the legacy ``simulator.mass_balance``
(enforced by tests/test_optimizer_boundary.py).
"""

from simulator.optimize.recipe import (
    KeyPath,
    KnobSpec,
    RecipePatch,
    RecipeSchema,
    RecipeValidationError,
    allowlist_version,
    recipe_schema_version,
)

__all__ = [
    "KeyPath",
    "KnobSpec",
    "RecipePatch",
    "RecipeSchema",
    "RecipeValidationError",
    "allowlist_version",
    "recipe_schema_version",
]
