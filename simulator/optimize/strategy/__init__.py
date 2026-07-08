"""Optimizer strategy interfaces and baselines."""

from simulator.optimize.strategy.protocol import (
    PROPOSAL_SOURCES,
    Candidate,
    Strategy,
    WarmStartSeed,
)
from simulator.optimize.strategy.random_strategy import RandomStrategy
from simulator.optimize.strategy.screen import MorrisScreenStrategy

_LAZY_EXPORTS = {
    "OptunaNSGA2Strategy": "simulator.optimize.strategy.genetic",
    "OptunaTPEStrategy": "simulator.optimize.strategy.bayesian",
    "StagedBeamStateError": "simulator.optimize.strategy.staged",
    "StagedDuplicateCacheKey": "simulator.optimize.strategy.staged",
    "StagedReplayViolation": "simulator.optimize.strategy.staged",
    "StagedStrategy": "simulator.optimize.strategy.staged",
    "StagedStrategyError": "simulator.optimize.strategy.staged",
    "assert_prefix_replay_equal": "simulator.optimize.strategy.staged",
    "make_prefix_eval_spec": "simulator.optimize.strategy.staged",
}


def __getattr__(name: str) -> object:
    try:
        module_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    from importlib import import_module

    module = import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value


__all__ = [
    "Candidate",
    "MorrisScreenStrategy",
    "OptunaNSGA2Strategy",
    "OptunaTPEStrategy",
    "PROPOSAL_SOURCES",
    "RandomStrategy",
    "StagedBeamStateError",
    "StagedDuplicateCacheKey",
    "StagedReplayViolation",
    "StagedStrategy",
    "StagedStrategyError",
    "Strategy",
    "WarmStartSeed",
    "assert_prefix_replay_equal",
    "make_prefix_eval_spec",
]
