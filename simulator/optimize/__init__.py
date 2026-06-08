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
    PrefixEvalSpec,
    cache_key,
    canonical_evalspec_json,
    canonical_feedstock_recipe_json,
    current_code_version,
    feedstock_recipe_digest,
)
from simulator.optimize.doe import (
    DEFAULT_ANCHOR_DELTA_FRACTION,
    DEPENDENCY_FREE_LHC_SAMPLER,
    FIDELITY_CORRELATION_METRICS,
    SCIPY_SOBOL_SAMPLER,
    DoeSpec,
    FidelityCorrelationProtocol,
    FidelityCorrelationResult,
    active_sampler_name,
    sample_recipe_patch_at_index,
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
    ObjectiveImportanceEvidence,
    ObjectiveProfileError,
    ObjectiveValue,
    ObjectiveVector,
    compute_objectives,
    dominates,
    objective_definitions,
    objective_importance_evidence,
    objective_scores,
    pareto_front,
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
    "OptunaNSGA2Strategy": "simulator.optimize.strategy",
    "OptunaTPEStrategy": "simulator.optimize.strategy",
    "StagedBeamStateError": "simulator.optimize.strategy",
    "StagedDuplicateCacheKey": "simulator.optimize.strategy",
    "StagedReplayViolation": "simulator.optimize.strategy",
    "StagedStrategy": "simulator.optimize.strategy",
    "StagedStrategyError": "simulator.optimize.strategy",
    "assert_prefix_replay_equal": "simulator.optimize.strategy",
    "make_prefix_eval_spec": "simulator.optimize.strategy",
    "pin_seeds": "simulator.optimize.determinism",
    "pin_worker_env": "simulator.optimize.determinism",
    "study": "simulator.optimize.study",
    "StudyConfig": "simulator.optimize.study",
    "StudyError": "simulator.optimize.study",
    "StudyNoFeasibleError": "simulator.optimize.study",
    "StudyRecord": "simulator.optimize.study",
    "StudyResult": "simulator.optimize.study",
    "run": "simulator.optimize.study",
}


def __getattr__(name: str) -> object:
    try:
        module_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    from importlib import import_module

    module = import_module(module_name)
    if name == "study":
        value = module
    elif name == "RESULT_STORE_SCHEMA_VERSION":
        value = getattr(module, "SCHEMA_VERSION")
    else:
        value = getattr(module, name)
    globals()[name] = value
    return value

__all__ = [
    "DEFAULT_ANCHOR_DELTA_FRACTION",
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
    "PrefixEvalSpec",
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
    "ObjectiveImportanceEvidence",
    "ObjectiveProfileError",
    "ObjectiveValue",
    "ObjectiveVector",
    "OptunaNSGA2Strategy",
    "OptunaTPEStrategy",
    "StagedBeamStateError",
    "StagedDuplicateCacheKey",
    "StagedReplayViolation",
    "StagedStrategy",
    "StagedStrategyError",
    "StudyConfig",
    "StudyError",
    "StudyNoFeasibleError",
    "StudyRecord",
    "StudyResult",
    "RunReference",
    "ScoredResult",
    "ResultStore",
    "ResultStoreSchemaError",
    "ResultsStore",
    "RESULT_STORE_SCHEMA_VERSION",
    "RandomStrategy",
    "active_sampler_name",
    "assert_prefix_replay_equal",
    "assert_deterministic",
    "allowlist_version",
    "cache_key",
    "canonical_evalspec_json",
    "canonical_feedstock_recipe_json",
    "compute_objectives",
    "current_code_version",
    "deterministic_result_view",
    "dominates",
    "evaluate",
    "feedstock_recipe_digest",
    "make_prefix_eval_spec",
    "objective_definitions",
    "objective_importance_evidence",
    "objective_scores",
    "pareto_front",
    "PhysicsConstraintSet",
    "PoolEvaluationRequest",
    "Strategy",
    "pin_seeds",
    "pin_worker_env",
    "recipe_schema_version",
    "sample_recipe_patch_at_index",
    "sample_recipe_patches",
    "ThresholdSpec",
    "evaluate_batch",
    "evaluate_in_process_pool",
    "run_fidelity_correlation",
    "run",
    "study",
]
