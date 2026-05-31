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
from simulator.optimize.evalspec import (
    EvalSpec,
    cache_key,
    canonical_evalspec_json,
    canonical_feedstock_recipe_json,
    current_code_version,
    feedstock_recipe_digest,
)
from simulator.optimize.doe import (
    DEPENDENCY_FREE_LHC_SAMPLER,
    FIDELITY_CORRELATION_METRICS,
    SCIPY_SOBOL_SAMPLER,
    DoeSpec,
    FidelityCorrelationProtocol,
    FidelityCorrelationResult,
    active_sampler_name,
    sample_recipe_patches,
)
from simulator.optimize.physics import (
    GATE_ORDER,
    PHYSICS_GATE_VERSION,
    FeasibilityResult,
    GateMargin,
    PhysicsConstraintSet,
    ThresholdSpec,
)

__all__ = [
    "DEPENDENCY_FREE_LHC_SAMPLER",
    "EvalSpec",
    "FIDELITY_CORRELATION_METRICS",
    "KeyPath",
    "KnobSpec",
    "RecipePatch",
    "RecipeSchema",
    "RecipeValidationError",
    "SCIPY_SOBOL_SAMPLER",
    "DoeSpec",
    "FidelityCorrelationProtocol",
    "FidelityCorrelationResult",
    "GATE_ORDER",
    "PHYSICS_GATE_VERSION",
    "FeasibilityResult",
    "GateMargin",
    "active_sampler_name",
    "allowlist_version",
    "cache_key",
    "canonical_evalspec_json",
    "canonical_feedstock_recipe_json",
    "current_code_version",
    "feedstock_recipe_digest",
    "PhysicsConstraintSet",
    "recipe_schema_version",
    "sample_recipe_patches",
    "ThresholdSpec",
]
