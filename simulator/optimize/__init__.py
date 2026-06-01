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
from simulator.optimize.objective import (
    ObjectiveComputationError,
    ObjectiveDefinition,
    ObjectiveProfileError,
    ObjectiveValue,
    ObjectiveVector,
    compute_objectives,
    objective_definitions,
)
from simulator.optimize.strategy import Candidate, MorrisScreenStrategy, RandomStrategy, Strategy

_LAZY_EXPORTS = {
    "BackendUnavailableAbort": "simulator.optimize.evaluate",
    "EngineBugAbort": "simulator.optimize.evaluate",
    "EvaluationAbort": "simulator.optimize.evaluate",
    "FailureCategory": "simulator.optimize.evaluate",
    "RunReference": "simulator.optimize.evaluate",
    "ScoredResult": "simulator.optimize.evaluate",
    "evaluate": "simulator.optimize.evaluate",
    "RESULT_STORE_SCHEMA_VERSION": "simulator.optimize.results_store",
    "ResultStore": "simulator.optimize.results_store",
    "ResultStoreSchemaError": "simulator.optimize.results_store",
    "ResultsStore": "simulator.optimize.results_store",
    "PoolEvaluationRequest": "simulator.optimize.pool",
    "evaluate_batch": "simulator.optimize.pool",
    "evaluate_in_process_pool": "simulator.optimize.pool",
    "run_fidelity_correlation": "simulator.optimize.fidelity",
    "THREAD_ENV_VARS": "simulator.optimize.determinism",
    "assert_deterministic": "simulator.optimize.determinism",
    "deterministic_result_view": "simulator.optimize.determinism",
    "pin_seeds": "simulator.optimize.determinism",
    "pin_worker_env": "simulator.optimize.determinism",
}


def __getattr__(name: str) -> object:
    try:
        module_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    from importlib import import_module

    module = import_module(module_name)
    if name == "RESULT_STORE_SCHEMA_VERSION":
        value = getattr(module, "SCHEMA_VERSION")
    else:
        value = getattr(module, name)
    globals()[name] = value
    return value

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
    "Candidate",
    "EngineBugAbort",
    "EvaluationAbort",
    "FailureCategory",
    "MorrisScreenStrategy",
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
    "RandomStrategy",
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
    "PoolEvaluationRequest",
    "Strategy",
    "pin_seeds",
    "pin_worker_env",
    "recipe_schema_version",
    "sample_recipe_patches",
    "ThresholdSpec",
    "evaluate_batch",
    "evaluate_in_process_pool",
    "run_fidelity_correlation",
]
