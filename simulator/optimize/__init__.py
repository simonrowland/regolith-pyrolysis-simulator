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
from simulator.optimize.determinism import (
    THREAD_ENV_VARS,
    assert_deterministic,
    deterministic_result_view,
    pin_seeds,
    pin_worker_env,
)
from simulator.optimize.physics import (
    GATE_ORDER,
    PHYSICS_GATE_VERSION,
    FeasibilityResult,
    GateMargin,
    PhysicsConstraintSet,
    ThresholdSpec,
)
from simulator.optimize.objective import (
    ObjectiveComputationError,
    ObjectiveDefinition,
    ObjectiveProfileError,
    ObjectiveValue,
    ObjectiveVector,
    compute_objectives,
    objective_definitions,
)
from simulator.optimize.evaluate import (
    BackendUnavailableAbort,
    EngineBugAbort,
    EvaluationAbort,
    FailureCategory,
    RunReference,
    ScoredResult,
    evaluate,
)
from simulator.optimize.results_store import (
    SCHEMA_VERSION as RESULT_STORE_SCHEMA_VERSION,
    ResultStore,
    ResultStoreSchemaError,
    ResultsStore,
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
    "THREAD_ENV_VARS",
    "FeasibilityResult",
    "GateMargin",
    "BackendUnavailableAbort",
    "EngineBugAbort",
    "EvaluationAbort",
    "FailureCategory",
    "ObjectiveComputationError",
    "ObjectiveDefinition",
    "ObjectiveProfileError",
    "ObjectiveValue",
    "ObjectiveVector",
    "RunReference",
    "ScoredResult",
    "ResultStore",
    "ResultStoreSchemaError",
    "ResultsStore",
    "RESULT_STORE_SCHEMA_VERSION",
    "active_sampler_name",
    "assert_deterministic",
    "allowlist_version",
    "cache_key",
    "canonical_evalspec_json",
    "canonical_feedstock_recipe_json",
    "compute_objectives",
    "current_code_version",
    "deterministic_result_view",
    "evaluate",
    "feedstock_recipe_digest",
    "objective_definitions",
    "PhysicsConstraintSet",
    "pin_seeds",
    "pin_worker_env",
    "recipe_schema_version",
    "sample_recipe_patches",
    "ThresholdSpec",
]
